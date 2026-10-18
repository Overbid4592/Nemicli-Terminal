"""Bilder (Krea 2): die Transformer-Blöcke des DiT als TensorRT-Netz.

TensorRT verschmilzt Norm, Modulation, Quantisierung und Matrizen eines Blocks zu wenigen Kernen;
torch rechnet dieselben Schritte einzeln (scaled_mm, cuDNN-Attention und viele kleine Kerne).
Text-Teil, Einbettung und letzte Schicht bleiben in torch (wenig Rechenzeit).

Ablauf: Das erste Bild rechnet torch und misst dabei die Höchstwerte der Matrix-Eingänge
(globale Skalen der FP4-/FP8-Aktivierungen). Danach baut ein Hintergrund-Faden das Netz aus der
RAM-Kopie des Modells (NVFP4-Gewichte werden unverändert übernommen) und speichert es.
Ab dem nächsten Bild rechnet TensorRT die Blöcke; die Gewichte liegen im VRAM, soweit es frei
ist (Weight-Streaming), nach dem Bild wieder nur im RAM.

Die Blöcke sind auf mehrere Netze von je etwa GRUPPE_BYTES Gewichten verteilt (laufzeit.NetzKette):
Das Budget-Setzen hält Python fest, bei einem einzigen 6-GB-Netz stand die Oberfläche bis zu 2 s.
Zwischen den Netzen bleibt der Rest-Strom in float32 wie innerhalb eines Netzes.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import torch

from . import laufzeit
from .bausteine import Netzbauer, fp4_packen

try:
    import debugbericht as _DBG
except Exception:
    class _DBG:                                      # type: ignore[no-redef]
        @staticmethod
        def ereignis(*a, **k): pass

VERSION = 2                                    # 2: Block-Gruppen mit float32-Ein- und -Ausgang
GRUPPE_BYTES = 512 * 2**20                     # Gewichte je Netz (Budget-Wechsel ≈ 0,24 s je GB)
S_MIN, S_OPT, S_MAX = 16, 4352, 17408          # Token (Text + Bild) – bis 2048×2048
RESERVE = int(1.5 * 2**30)                     # bleibt frei (VAE-Puffer, Windows); Arbeitsspeicher zählt extra
MESS_RAND = 2.0
EPS = 1e-5

_lock = threading.Lock()
_stand: dict = {}                              # pfad -> {"netz", "baut", "mess", "fehler"}


class NichtMoeglich(RuntimeError):
    pass


# ----------------------------------------------------------------------------- Bauplan

def _lin_art(lin) -> str:
    if lin.art not in ("nvfp4_mm", "nvfp4", "fp8", "plain"):
        raise NichtMoeglich(f"Matrix-Art {lin.art}")
    return lin.art


def plan(dit) -> dict:
    """Aufbau der Blöcke als Daten (prüft, ob alle Blöcke gleich gebaut sind)."""
    if not dit.blocks:
        raise NichtMoeglich("keine Blöcke")
    b0 = dit.blocks[0]
    a = b0.attn
    D = a.wo.out_f if hasattr(a.wo, "out_f") else a.wo.weight().shape[0]
    hd = a.qn.numel()
    gate = b0.mlp.gate
    F = gate.out_f if hasattr(gate, "out_f") else gate.weight().shape[0]
    arten = sorted({_lin_art(l) for blk in dit.blocks for l in (blk.attn.wq, blk.attn.wk, blk.attn.wv,
                                                                blk.attn.gate, blk.attn.wo, blk.mlp.gate,
                                                                blk.mlp.up, blk.mlp.down)})
    return {"bloecke": len(dit.blocks), "D": int(D), "hq": a.heads, "hkv": a.kvheads, "hd": int(hd), "F": int(F),
            "arten": arten}


def _kennung(pfad: Path) -> dict:
    st = Path(pfad).stat()
    return {"datei": Path(pfad).name, "groesse": st.st_size, "zeit": st.st_mtime_ns}


def _teile(pfad: Path, p: dict, von: int, bis: int) -> dict:
    return {"v": VERSION, "datei": _kennung(pfad), "plan": json.dumps(p, sort_keys=True),
            "s": [S_MIN, S_OPT, S_MAX], "bloecke": [von, bis]}


def _alte_netze_weg(pfad: Path, p: dict) -> None:
    """Das Netz dieses Modells im Format von Version 1 (ein Netz für alle Blöcke) ist nach dem Neubau
    überflüssig: genau dieses löschen (Name aus denselben Angaben nachgerechnet)."""
    alt = {"v": 1, "datei": _kennung(pfad), "plan": json.dumps(p, sort_keys=True), "s": [S_MIN, S_OPT, S_MAX]}
    name = laufzeit.schluessel("krea", alt)
    for endung in (".engine", ".cache"):
        datei = laufzeit.engine_ordner() / f"{name}{endung}"
        try:
            if datei.is_file():
                groesse = datei.stat().st_size
                datei.unlink()
                _DBG.ereignis(f"TensorRT-Krea: altes Netz {datei.name} entfernt ({groesse / 2**30:.1f} GiB)")
        except OSError:
            pass


def _block_bytes(blk) -> int:
    """Gewichte eines Blocks im Netz: NVFP4 ein halbes Byte je Wert, sonst FP8 ein Byte."""
    n = 0
    for lin in (blk.attn.wq, blk.attn.wk, blk.attn.wv, blk.attn.gate, blk.attn.wo, blk.mlp.gate, blk.mlp.up,
                blk.mlp.down):
        werte = lin.out_f * lin.in_f if hasattr(lin, "out_f") else lin.weight().numel()
        n += werte // 2 if lin.art in ("nvfp4_mm", "nvfp4") else werte
    return n


def gruppen(dit) -> list[tuple[int, int]]:
    """Block-Bereiche [von, bis) mit je höchstens etwa GRUPPE_BYTES Gewichten."""
    je = max(1, int(GRUPPE_BYTES // max(1, _block_bytes(dit.blocks[0]))))
    n = len(dit.blocks)
    return [(a, min(n, a + je)) for a in range(0, n, je)]


# ----------------------------------------------------------------------------- Einmessen

def _namen(dit) -> dict:
    """id(Matrix) -> Name des Eingangs ("i.attn" teilen sich q, k, v, gate; "i.mlp" gate und up)."""
    out = {}
    for i, blk in enumerate(dit.blocks):
        for l in (blk.attn.wq, blk.attn.wk, blk.attn.wv, blk.attn.gate):
            out[id(l)] = f"{i}.attn"
        out[id(blk.attn.wo)] = f"{i}.wo"
        out[id(blk.mlp.gate)] = out[id(blk.mlp.up)] = f"{i}.mlp"
        out[id(blk.mlp.down)] = f"{i}.down"
    return out


class _Messer:
    """Hülle um eine Krea-Matrix: merkt sich beim ersten Aufruf den Höchstwert ihres Eingangs."""

    def __init__(self, ziel, name: str, werte: dict):
        self.ziel, self.name, self.werte = ziel, name, werte

    def __call__(self, x):
        if self.name not in self.werte:
            e = getattr(x, "x", x)
            self.werte[self.name] = float(e.detach().abs().amax())
        return self.ziel(x)

    def __getattr__(self, attr):
        return getattr(self.ziel, attr)


def einmessen(pfad, dit) -> None:
    """Hüllen um die Block-Matrizen der Arbeitskopie `dit` (die Kopie wird nach dem Bild verworfen)."""
    with _lock:
        st = _stand.setdefault(str(pfad), {})
        if st.get("netz") is not None or st.get("baut") or st.get("fehler"):
            return
        st["mess"] = werte = {}
    namen = _namen(dit)
    for blk in dit.blocks:
        for besitzer, attr in ((blk.attn, "wq"), (blk.attn, "wk"), (blk.attn, "wv"), (blk.attn, "gate"),
                               (blk.attn, "wo"), (blk.mlp, "gate"), (blk.mlp, "up"), (blk.mlp, "down")):
            ziel = getattr(besitzer, attr)
            setattr(besitzer, attr, _Messer(ziel, namen[id(ziel)], werte))


# ----------------------------------------------------------------------------- Netz

def _gewicht(nb: Netzbauer, lin, eingang, amax: float):
    """Krea-Matrix -> (dequantisiertes Gewicht, passend quantisierter Eingang)."""
    trt = nb.trt
    if lin.art == "nvfp4_mm":
        import krea as K
        n, k = lin.out_f, lin.in_f
        q = lin.q.detach().cpu()
        codes = (((q & 0xF) << 4) | (q >> 4)).contiguous().numpy()        # Krea: erstes Element oben
        skalen = K._from_blocked(lin.bs.detach().cpu(), n, k // 16).contiguous().view(torch.uint8).numpy()
        return nb.fp4_gewicht(codes, skalen, float(lin.ts), n, k), nb.fp4_eingang(eingang, amax)
    w = lin.weight().detach().float().cpu()
    n, k = w.shape
    if lin.art == "nvfp4":
        return nb.fp4_gewicht(*fp4_packen(w), n, k), nb.fp4_eingang(eingang, amax)
    sw = max(float(w.abs().max()) / 448, 1e-12)
    q = (w / sw).to(torch.float8_e4m3fn).view(torch.uint8).contiguous().numpy()
    wdq = nb.net.add_dequantize(nb.konst(q, trt.fp8, (n, k), n * k), nb.f32(sw, form=()), nb.A).get_output(0)
    sa = nb.f32(max(amax, 1e-6) / 448, form=())
    a = nb.net.add_dequantize(nb.net.add_quantize(eingang, sa, trt.fp8).get_output(0), sa, nb.A).get_output(0)
    return wdq, a


class _KreaBauer(Netzbauer):

    def __init__(self, trt, net, p: dict, dit, mess: dict, von: int = 0, bis: int | None = None):
        super().__init__(trt, net)
        self.p, self.dit, self.mess = p, dit, mess
        self.von, self.bis = von, len(dit.blocks) if bis is None else bis
        self.eingaenge: dict = {}

    def lin(self, x, lin, name: str):
        amax = max(self.mess.get(name, 0.0), 1e-3) * MESS_RAND
        wdq, a = _gewicht(self, lin, x, amax)
        y = self.matmul_t(a, wdq)
        if getattr(lin, "bias", None) is not None:
            y = self.plus(y, self.bf16(lin.bias.reshape(1, 1, -1)))
        return y

    def rope_paare(self, x, kopfe: int, cos, sin):
        trt, hd = self.trt, self.p["hd"]
        h = hd // 2
        xf = self.cast(x, trt.float32)
        xr = self.umform(xf, (1, kopfe, "T", h, 2))
        xe = self.umform(self.scheibe(xr, (0, 0, 0, 0, 0), (1, kopfe, "T", h, 1)), (1, kopfe, "T", h))
        xo = self.umform(self.scheibe(xr, (0, 0, 0, 0, 1), (1, kopfe, "T", h, 1)), (1, kopfe, "T", h))
        ge = self.umform(self.minus(self.mal(xe, cos), self.mal(xo, sin)), (1, kopfe, "T", h, 1))
        go = self.umform(self.plus(self.mal(xo, cos), self.mal(xe, sin)), (1, kopfe, "T", h, 1))
        cat = self.net.add_concatenation([ge, go])
        cat.axis = 4
        return self.cast(self.umform(cat.get_output(0), (1, kopfe, "T", hd)), self.A)

    def block(self, i: int, blk, zf, vec, cos, sin):
        trt, p = self.trt, self.p
        D, hq, hkv, hd = p["D"], p["hq"], p["hkv"], p["hd"]
        m = self.plus(self.cast(vec, trt.float32), self.f32(blk.mod.float().reshape(1, 1, -1)))
        ps, psh, pg, qs, qsh, qg = (self.stueck(m, 2, j * D, D) for j in range(6))
        eins = self.f32(1.0, form=(1, 1, 1))
        x1 = self.cast(self.plus(self.mal(self.plus(eins, ps), self.rms(zf, blk.pre, EPS)), psh), self.A)
        a = blk.attn
        q = self.umform(self.lin(x1, a.wq, f"{i}.attn"), (1, "T", hq, hd), (0, 2, 1, 3))
        k = self.umform(self.lin(x1, a.wk, f"{i}.attn"), (1, "T", hkv, hd), (0, 2, 1, 3))
        v = self.umform(self.lin(x1, a.wv, f"{i}.attn"), (1, "T", hkv, hd), (0, 2, 1, 3))
        q = self.cast(self.rms(self.cast(q, trt.float32), a.qn, EPS), self.A)
        k = self.cast(self.rms(self.cast(k, trt.float32), a.kn, EPS), self.A)
        q, k = self.rope_paare(q, hq, cos, sin), self.rope_paare(k, hkv, cos, sin)
        q = self.cast(self.mal(self.cast(q, trt.float32), self.f32(hd ** -0.5, form=(1, 1, 1, 1))), self.A)
        att = self.net.add_attention_v2(q, k, v, trt.AttentionNormalizationOp.SOFTMAX, trt.CausalMaskKind.NONE)
        att.decomposable = True
        o = self.umform(att.get_output(0), (1, "T", hq * hd), (0, 2, 1, 3), erst_perm=True)
        o = self.mal(o, self.sigmoid(self.lin(x1, a.gate, f"{i}.attn")))
        zf = self.plus(zf, self.mal(pg, self.cast(self.lin(o, a.wo, f"{i}.wo"), trt.float32)))
        x2 = self.cast(self.plus(self.mal(self.plus(eins, qs), self.rms(zf, blk.post, EPS)), qsh), self.A)
        mlp = blk.mlp
        y = self.lin(self.mal(self.silu(self.lin(x2, mlp.gate, f"{i}.mlp")), self.lin(x2, mlp.up, f"{i}.mlp")),
                     mlp.down, f"{i}.down")
        return self.plus(zf, self.mal(qg, self.cast(y, trt.float32)))

    def bauen(self):
        trt, net, p = self.trt, self.net, self.p
        z = net.add_input("z", trt.float32, (1, -1, p["D"]))      # Rest-Strom in float32 (wie im Block)
        vec = net.add_input("vec", self.A, (1, 1, 6 * p["D"]))
        cos = net.add_input("cos", trt.float32, (1, 1, -1, p["hd"] // 2))
        sin = net.add_input("sin", trt.float32, (1, 1, -1, p["hd"] // 2))
        self.T = self.laenge_von(z, 1)
        zf = z
        for i in range(self.von, self.bis):
            zf = self.block(i, self.dit.blocks[i], zf, vec, cos, sin)
        aus = self.plus(zf, self.f32(0.0, form=(1, 1, 1)))               # Ausgang darf nicht der Eingang sein
        aus.name = "aus"
        net.mark_output(aus)


def _bauen_fn(p: dict, dit, mess: dict, von: int, bis: int):
    def bauen(trt, builder):
        net = builder.create_network(0)
        bauer = _KreaBauer(trt, net, p, dit, mess, von, bis)
        bauer.bauen()
        cfg = builder.create_builder_config()
        cfg.set_flag(trt.BuilderFlag.WEIGHT_STREAMING)
        prof = builder.create_optimization_profile()
        D, h = p["D"], p["hd"] // 2
        prof.set_shape("z", (1, S_MIN, D), (1, S_OPT, D), (1, S_MAX, D))
        for n in ("cos", "sin"):
            prof.set_shape(n, (1, 1, S_MIN, h), (1, 1, S_OPT, h), (1, 1, S_MAX, h))
        cfg.add_optimization_profile(prof)
        bauen.halten = bauer.halten
        return net, cfg
    return bauen


# ----------------------------------------------------------------------------- Ablauf

def _netz_laden_oder_bauen(pfad: str, dit_ram, mess: dict | None) -> None:
    t0 = time.time()
    try:
        p = plan(dit_ram)
        netze = [laufzeit.holen("krea", _teile(Path(pfad), p, a, b), _bauen_fn(p, dit_ram, mess or {}, a, b),
                                budget=0) for a, b in gruppen(dit_ram)]
        netz = laufzeit.NetzKette(netze)
        with _lock:
            _stand[pfad].update(netz=netz, baut=False)
        _alte_netze_weg(Path(pfad), p)
        _DBG.ereignis(f"TensorRT-Krea bereit ({netz.streambar / 2**30:.2f} GiB Gewichte in {len(netze)} Netzen, "
                      f"{time.time() - t0:.0f} s)")
    except Exception as exc:
        voruebergehend = "synchronizing CUDA operation" in str(exc)    # Prüfmodus eines anderen Fadens
        with _lock:
            _stand[pfad].update(baut=False, fehler=None if voruebergehend else str(exc))
        _DBG.ereignis(f"TensorRT-Krea nicht gebaut: {exc}" + (" – nächstes Bild versucht es neu" if voruebergehend
                                                                  else ""))


def gespeichert(pfad, dit_ram) -> bool:
    try:
        p = plan(dit_ram)
        namen = [laufzeit.schluessel("krea", _teile(Path(pfad), p, a, b)) for a, b in gruppen(dit_ram)]
    except Exception:
        return False
    return all((laufzeit.engine_ordner() / f"{name}.engine").is_file() for name in namen)


def vorbereiten(pfad, dit_ram, warten: bool = False) -> None:
    """Netz laden (gespeichert) oder – mit fertiger Einmessung – bauen, im Hintergrund."""
    if not laufzeit.bereit():
        return
    pfad = str(pfad)
    with _lock:
        st = _stand.setdefault(pfad, {})
        if st.get("netz") is not None or st.get("baut") or st.get("fehler"):
            return
        mess = st.get("mess")
        if not gespeichert(pfad, dit_ram):
            if not mess or len(mess) < 4 * len(dit_ram.blocks):
                return                                 # erst einmessen (nächstes Bild über torch)
        st["baut"] = True
    if warten:
        _netz_laden_oder_bauen(pfad, dit_ram, mess)
    else:
        threading.Thread(target=_netz_laden_oder_bauen, args=(pfad, dit_ram, mess), daemon=True,
                         name="trt-krea-bau").start()


def bloecke(pfad):
    """Ersatz für _DiT.bloecke über TensorRT – oder None, solange kein Netz bereit ist."""
    with _lock:
        netz = _stand.get(str(pfad), {}).get("netz")
    if netz is None:
        return None

    def rechnen(z, vec, rope):
        cos, sin = rope
        cos, sin = cos.float().contiguous(), sin.float().contiguous()
        vec = vec.to(z.dtype).contiguous()
        a, b = z.float().contiguous(), torch.empty(z.shape, dtype=torch.float32, device=z.device)
        adressen = []
        for _ in netz.netze:                       # zwei Puffer im Wechsel: Ausgang wird Eingang des nächsten
            adressen.append({"z": a, "vec": vec, "cos": cos, "sin": sin, "aus": b})
            a, b = b, a
        netz(adressen, {"z": tuple(z.shape), "cos": tuple(cos.shape), "sin": tuple(sin.shape)})
        return a.to(z.dtype)

    netz.reserve = RESERVE                         # Budget bemisst das Netz selbst, Arbeitsspeicher zuerst
    return rechnen


def nach_bild(pfad) -> None:
    """Bild fertig: Gewichte und Arbeitsspeicher aus dem VRAM (wie Krea selbst)."""
    with _lock:
        netz = _stand.get(str(pfad), {}).get("netz")
    if netz is not None:
        netz.cache_sichern()
        netz.gewichte_weg()


def entladen() -> None:
    """Alle Netze freigeben – Stück für Stück: Beim Abbau hält TensorRT Python fest (alle Netze auf einmal
    ließen die Oberfläche 4–5 s stehen). Je Netz erst der Kontext, dann Puffer, Caches und Gewichte."""
    with _lock:
        alt = list(_stand.values())
        _stand.clear()
    laufzeit.stand_abbauen(alt)
