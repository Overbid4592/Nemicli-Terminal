"""Video (Wan 2.2 5B): die DiT-Blöcke als TensorRT-Netz.

Ersetzt WanDiT._bloecke: Selbst-Attention über alle Bild-Token, Cross-Attention auf den Text, FFN –
der Residual-Strom bleibt in float32 wie im Original, Modulationen gelten je Latent-Bild (G Gruppen).
Text-Einbettung, Zeit-Einbettung und Kopf bleiben in torch (wenig Rechenzeit).

Matrizen: NVFP4 (Blackwell, Vorgabe) – oder FP8 (W8A8) wie der Videomotor selbst (`video_trt_format`).
Die globalen Skalen der Aktivierungen misst das erste Video (über torch); danach baut ein
Hintergrund-Faden das Netz aus der RAM-Kopie des Modells. Videos mit Charakter-LoRA rechnet torch.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import torch

from . import laufzeit
from .bausteine import Netzbauer, fp4_packen

try:
    import debugbericht as _DBG
except Exception:
    class _DBG:                                      # type: ignore[no-redef]
        @staticmethod
        def ereignis(*a, **k): pass

VERSION = 1
FORMATE = ("fp4", "fp8")
G_MIN, G_OPT, G_MAX = 1, 31, 128               # Latent-Bilder (121 Bilder = 31, 20 s bis 128)
N_MIN, N_OPT, N_MAX = 16, 704, 4096             # Token je Latent-Bild (704×1024 = 704)
S_MAX = G_MAX * N_MAX                          # Bild-Token insgesamt
TEXT = 512
RESERVE = int(1.5 * 2**30)                     # bleibt frei; Arbeitsspeicher zählt extra
MESS_RAND = 2.0
MATRIZEN = ("self_attn.q", "self_attn.k", "self_attn.v", "self_attn.o", "cross_attn.q", "cross_attn.k",
            "cross_attn.v", "cross_attn.o", "ffn.0", "ffn.2")
# welcher Eingang (für die Skala): q, k, v teilen sich h; cross k, v den Text
EINGANG = {"self_attn.q": "attn", "self_attn.k": "attn", "self_attn.v": "attn", "self_attn.o": "o",
           "cross_attn.q": "cq", "cross_attn.k": "ctx", "cross_attn.v": "ctx", "cross_attn.o": "co",
           "ffn.0": "ffn", "ffn.2": "ffn2"}

_lock = threading.Lock()
_stand: dict = {}


def _dicht(w) -> torch.Tensor:
    """Matrix aus dem Videomotor (gepackt _Q, FP8 _F8 oder dicht) als float32 auf der CPU."""
    if hasattr(w, "raw") and hasattr(w, "ggml_type"):
        from ggufengine.quant import dequantize
        return dequantize(w.raw.reshape(-1).cpu(), w.ggml_type, w.n_out * w.n_in, torch.float32).view(w.n_out, w.n_in)
    if hasattr(w, "w8"):
        return w.w8.float().cpu() * float(w.ws)
    return w.detach().float().cpu()


def _teile(pfad: Path, cfg: dict, fmt: str) -> dict:
    st = Path(pfad).stat()
    return {"v": VERSION, "fmt": fmt, "datei": {"name": Path(pfad).name, "groesse": st.st_size, "zeit": st.st_mtime_ns},
            "cfg": json.dumps({k: cfg[k] for k in sorted(cfg) if isinstance(cfg[k], (int, float, str))},
                              sort_keys=True)}


# ----------------------------------------------------------------------------- Einmessen

class _Messer:
    def __init__(self, ziel, name: str, werte: dict):
        self.ziel, self.name, self.werte = ziel, name, werte

    def __call__(self, x, *a, **k):
        if self.name not in self.werte:
            self.werte[self.name] = float(x.detach().abs().amax())
        if callable(self.ziel):
            return self.ziel(x, *a, **k)
        import torch.nn.functional as F
        return F.linear(x, self.ziel.to(x.dtype))


def einmessen(pfad, sd: dict, n_layer: int) -> None:
    """Hüllen um die Block-Matrizen der Arbeitskopie `sd` (die Kopie wird nach dem Video verworfen)."""
    with _lock:
        st = _stand.setdefault(str(pfad), {})
        if st.get("netz") is not None or st.get("baut") or st.get("fehler"):
            return
        st["mess"] = werte = {}
    for i in range(n_layer):
        for m in MATRIZEN:
            k = f"blocks.{i}.{m}.weight"
            if k in sd:
                sd[k] = _Messer(sd[k], f"{i}.{EINGANG[m]}", werte)


# ----------------------------------------------------------------------------- Netz

class _WanBauer(Netzbauer):

    def __init__(self, trt, net, cfg: dict, sd: dict, mess: dict, fmt: str):
        super().__init__(trt, net)
        self.cfg, self.sd, self.mess, self.fmt = cfg, sd, mess, fmt
        self.D, self.n = int(cfg["dim"]), int(cfg["num_heads"])
        self.hd = self.D // self.n
        self.eps = float(cfg.get("eps", 1e-6))
        self.G = self.N = None

    def lin(self, x, name: str, mess: str):
        trt = self.trt
        w = _dicht(self.sd[name + ".weight"])
        n, k = w.shape
        amax = max(self.mess.get(mess, 0.0), 1e-3) * MESS_RAND
        if self.fmt == "fp4" and k % 16 == 0:
            wdq, a = self.fp4_gewicht(*fp4_packen(w), n, k), self.fp4_eingang(x, amax)
        else:
            sw = max(float(w.abs().max()) / 448, 1e-12)
            q = (w / sw).to(torch.float8_e4m3fn).view(torch.uint8).contiguous().numpy()
            wdq = self.net.add_dequantize(self.konst(q, trt.fp8, (n, k), n * k), self.f32(sw, form=()),
                                          self.A).get_output(0)
            sa = self.f32(amax / 448, form=())
            a = self.net.add_dequantize(self.net.add_quantize(x, sa, trt.fp8).get_output(0), sa, self.A).get_output(0)
        y = self.matmul_t(a, wdq)
        b = self.sd.get(name + ".bias")
        if b is not None:
            y = self.plus(y, self.bf16(b.reshape(1, 1, -1) if len(x.shape) == 3 else b.reshape(1, -1)))
        return y

    def norm(self, x, w=None, b=None):
        """LayerNorm über D in fp32 (ohne oder mit Gewicht/Bias)."""
        trt = self.trt
        achse = 1 << (len(x.shape) - 1)
        m = self.net.add_reduce(x, trt.ReduceOperation.AVG, achse, True).get_output(0)
        xc = self.minus(x, m)
        v = self.net.add_reduce(self.mal(xc, xc), trt.ReduceOperation.AVG, achse, True).get_output(0)
        r = self.net.add_unary(self.net.add_unary(self.plus(v, self.f32(self.eps, form=(1, 1, 1))),
                                                  trt.UnaryOperation.SQRT).get_output(0),
                               trt.UnaryOperation.RECIP).get_output(0)
        y = self.mal(xc, r)
        if w is not None:
            y = self.mal(y, self.f32(w.float().reshape(1, 1, -1)))
        if b is not None:
            y = self.plus(y, self.f32(b.float().reshape(1, 1, -1)))
        return y

    def rope(self, x, cos, sin):
        """x [S, n, hd] (fp32), Paare (komplex) mit cos/sin [S, 1, hd/2]."""
        h = self.hd // 2
        xr = self.umform(x, ("T", self.n, h, 2))
        xe = self.umform(self.scheibe(xr, (0, 0, 0, 0), ("T", self.n, h, 1)), ("T", self.n, h))
        xo = self.umform(self.scheibe(xr, (0, 0, 0, 1), ("T", self.n, h, 1)), ("T", self.n, h))
        ge = self.umform(self.minus(self.mal(xe, cos), self.mal(xo, sin)), ("T", self.n, h, 1))
        go = self.umform(self.plus(self.mal(xo, cos), self.mal(xe, sin)), ("T", self.n, h, 1))
        cat = self.net.add_concatenation([ge, go])
        cat.axis = 3
        return self.umform(cat.get_output(0), ("T", self.n, self.hd))

    def attention(self, q, k, v, lk: str):
        """q [S, n, hd], k/v [L, n, hd] (A) -> [S, n*hd]; L = "T" (Selbst) oder Textlänge."""
        trt = self.trt
        qq = self.umform(q, (1, "T", self.n, self.hd), (0, 2, 1, 3))
        kk = self.umform(k, (1, lk, self.n, self.hd), (0, 2, 1, 3))
        vv = self.umform(v, (1, lk, self.n, self.hd), (0, 2, 1, 3))
        qq = self.cast(self.mal(self.cast(qq, trt.float32), self.f32(self.hd ** -0.5, form=(1, 1, 1, 1))), self.A)
        att = self.net.add_attention_v2(qq, kk, vv, trt.AttentionNormalizationOp.SOFTMAX, trt.CausalMaskKind.NONE)
        att.decomposable = True
        return self.umform(att.get_output(0), ("T", self.D), (0, 2, 1, 3), erst_perm=True)

    def block(self, i: int, x, e0, c, cos, sin):
        """x [G, N, D] fp32, e0 [G, 6, D] fp32, c [L, D] (A) -> x."""
        trt, sd, D = self.trt, self.sd, self.D
        p = f"blocks.{i}."
        m = self.plus(self.f32(sd[p + "modulation"].float().reshape(1, 6, D)), e0)        # [G, 6, D]
        teil = [self.scheibe(m, (0, j, 0), ("G", 1, D)) for j in range(6)]
        eins = self.f32(1.0, form=(1, 1, 1))
        flach = lambda t: self.umform(t, ("T", D))                                       # [G, N, D] -> [S, D]
        gruppen = lambda t: self.umform(t, ("G", "N", D))
        h = self.cast(flach(self.plus(teil[0], self.mal(self.norm(x), self.plus(eins, teil[1])))), self.A)
        q = self.cast(self.rms(self.cast(self.lin(h, p + "self_attn.q", f"{i}.attn"), trt.float32),
                               sd[p + "self_attn.norm_q.weight"], self.eps), trt.float32)
        k = self.cast(self.rms(self.cast(self.lin(h, p + "self_attn.k", f"{i}.attn"), trt.float32),
                               sd[p + "self_attn.norm_k.weight"], self.eps), trt.float32)
        v = self.umform(self.lin(h, p + "self_attn.v", f"{i}.attn"), ("T", self.n, self.hd))
        q = self.cast(self.rope(self.umform(q, ("T", self.n, self.hd)), cos, sin), self.A)
        k = self.cast(self.rope(self.umform(k, ("T", self.n, self.hd)), cos, sin), self.A)
        a = self.attention(q, k, v, "T")
        y = self.cast(self.lin(a, p + "self_attn.o", f"{i}.o"), trt.float32)
        x = self.plus(x, self.mal(gruppen(y), teil[2]))
        h = self.cast(flach(self.norm(x, sd[p + "norm3.weight"], sd[p + "norm3.bias"])), self.A)
        q = self.rms(self.cast(self.lin(h, p + "cross_attn.q", f"{i}.cq"), trt.float32),
                     sd[p + "cross_attn.norm_q.weight"], self.eps)
        k = self.rms(self.cast(self.lin(c, p + "cross_attn.k", f"{i}.ctx"), trt.float32),
                     sd[p + "cross_attn.norm_k.weight"], self.eps)
        v = self.lin(c, p + "cross_attn.v", f"{i}.ctx")
        a = self.attention(self.cast(self.umform(q, ("T", self.n, self.hd)), self.A),
                           self.cast(self.umform(k, (TEXT, self.n, self.hd)), self.A),
                           self.umform(v, (TEXT, self.n, self.hd)), TEXT)
        x = self.plus(x, gruppen(self.cast(self.lin(a, p + "cross_attn.o", f"{i}.co"), trt.float32)))
        h = self.cast(flach(self.plus(teil[3], self.mal(self.norm(x), self.plus(eins, teil[4])))), self.A)
        h = self.lin(self.gelu_tanh(self.lin(h, p + "ffn.0", f"{i}.ffn")), p + "ffn.2", f"{i}.ffn2")
        return self.plus(x, self.mal(gruppen(self.cast(h, trt.float32)), teil[5]))

    def bauen(self):
        trt, net, D = self.trt, self.net, self.D
        x = net.add_input("x", trt.float32, (-1, -1, D))
        e0 = net.add_input("e0", trt.float32, (-1, 6, D))
        c = net.add_input("c", self.A, (TEXT, D))
        cos = net.add_input("cos", trt.float32, (-1, 1, self.hd // 2))
        sin = net.add_input("sin", trt.float32, (-1, 1, self.hd // 2))
        self.T = self.laenge_von(cos, 0)                                  # alle Bild-Token
        self._G, self._N = self.laenge_von(x, 0), self.laenge_von(x, 1)
        for i in range(int(self.cfg["num_layers"])):
            x = self.block(i, x, e0, c, cos, sin)
        x.name = "aus"
        net.mark_output(x)

    def form(self, *teile):
        """Wie Netzbauer.form, dazu "G" (Latent-Bilder) und "N" (Token je Bild)."""
        ersatz = {"T": self.T, "G": self._G, "N": self._N}
        stuecke = [ersatz[x] if isinstance(x, str) else self.konst(np.array([x], dtype=np.int64)) for x in teile]
        return self.net.add_concatenation(stuecke).get_output(0)


def _bauen_fn(cfg: dict, sd: dict, mess: dict, fmt: str):
    def bauen(trt, builder):
        net = builder.create_network(0)
        bauer = _WanBauer(trt, net, cfg, sd, mess, fmt)
        bauer.bauen()
        conf = builder.create_builder_config()
        conf.set_flag(trt.BuilderFlag.WEIGHT_STREAMING)
        prof = builder.create_optimization_profile()
        D, h = int(cfg["dim"]), int(cfg["dim"]) // int(cfg["num_heads"]) // 2
        prof.set_shape("x", (G_MIN, N_MIN, D), (G_OPT, N_OPT, D), (G_MAX, N_MAX, D))
        prof.set_shape("e0", (G_MIN, 6, D), (G_OPT, 6, D), (G_MAX, 6, D))
        for n in ("cos", "sin"):
            prof.set_shape(n, (G_MIN * N_MIN, 1, h), (G_OPT * N_OPT, 1, h), (S_MAX, 1, h))
        conf.add_optimization_profile(prof)
        bauen.halten = bauer.halten
        return net, conf
    return bauen


# ----------------------------------------------------------------------------- Ablauf

def format_wahl() -> str:
    try:
        import config
        f = str(config.load().get("video_trt_format", "fp4")).lower()
        return f if f in FORMATE else "fp4"
    except Exception:
        return "fp4"


def _laden_oder_bauen(pfad: str, cfg: dict, sd: dict, mess: dict | None, fmt: str) -> None:
    t0 = time.time()
    try:
        netz = laufzeit.holen("wan", _teile(Path(pfad), cfg, fmt), _bauen_fn(cfg, sd, mess or {}, fmt), budget=0)
        with _lock:
            _stand[pfad].update(netz=netz, baut=False)
        _DBG.ereignis(f"TensorRT-Wan bereit ({fmt}, {netz.engine.streamable_weights_size / 2**30:.2f} GiB, "
                      f"{time.time() - t0:.0f} s)")
    except Exception as exc:
        voruebergehend = "synchronizing CUDA operation" in str(exc)    # Prüfmodus eines anderen Fadens
        with _lock:
            _stand[pfad].update(baut=False, fehler=None if voruebergehend else str(exc))
        _DBG.ereignis(f"TensorRT-Wan nicht gebaut: {exc}" + (" – nächstes Video versucht es neu" if voruebergehend
                                                                 else ""))


def gespeichert(pfad, cfg: dict) -> bool:
    try:
        name = laufzeit.schluessel("wan", _teile(Path(pfad), cfg, format_wahl()))
    except Exception:
        return False
    return (laufzeit.engine_ordner() / f"{name}.engine").is_file()


def vorbereiten(pfad, cfg: dict, sd_ram: dict, warten: bool = False) -> None:
    if not laufzeit.bereit():
        return
    pfad, fmt = str(pfad), format_wahl()
    with _lock:
        st = _stand.setdefault(pfad, {})
        if st.get("netz") is not None or st.get("baut") or st.get("fehler"):
            return
        mess = st.get("mess")
        if not gespeichert(pfad, cfg) and (not mess or len(mess) < 6 * int(cfg["num_layers"])):
            return
        st["baut"] = True
    if warten:
        _laden_oder_bauen(pfad, cfg, sd_ram, mess, fmt)
    else:
        threading.Thread(target=_laden_oder_bauen, args=(pfad, cfg, sd_ram, mess, fmt), daemon=True,
                         name="trt-wan-bau").start()


def bloecke(pfad):
    """Ersatz für den Block-Durchlauf (WanDiT.block_lauf) – oder None, solange kein Netz bereit ist."""
    with _lock:
        netz = _stand.get(str(pfad), {}).get("netz")
    if netz is None:
        return None

    def rechnen(x, e0, c, freqs, bilder: int):
        """x [G, S/G, D] wie im Videomotor (G = 1 oder Latent-Bilder); gerechnet wird je Latent-Bild."""
        G, S, D = x.shape[0], x.shape[0] * x.shape[1], x.shape[2]
        xb = x.float().reshape(bilder, S // bilder, D).contiguous()
        eb = e0.float().expand(bilder, 6, D).contiguous() if e0.shape[0] == 1 else e0.float().contiguous()
        cos, sin = freqs.real.float().contiguous(), freqs.imag.float().contiguous()
        c = c.to(torch.bfloat16).contiguous()
        aus = torch.empty_like(xb)
        netz({"x": xb, "e0": eb, "c": c, "cos": cos, "sin": sin, "aus": aus},
             {"x": tuple(xb.shape), "e0": tuple(eb.shape), "cos": tuple(cos.shape), "sin": tuple(sin.shape)})
        return aus.view(G, S // G, D)

    netz.reserve = RESERVE                         # Budget bemisst das Netz selbst, Arbeitsspeicher zuerst
    return rechnen


def nach_video(pfad) -> None:
    with _lock:
        netz = _stand.get(str(pfad), {}).get("netz")
    if netz is not None:
        netz.cache_sichern()
        netz.gewichte_weg()


def entladen() -> None:
    """Alle Netze freigeben, Stück für Stück wie bei Krea (bild.entladen) – sonst steht die Oberfläche."""
    with _lock:
        alt = list(_stand.values())
        _stand.clear()
    laufzeit.stand_abbauen(alt)
