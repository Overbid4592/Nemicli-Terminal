"""Schnell-Lesen: lange Prompt-Stücke rechnet ein TensorRT-Netz, die Antwort schreibt der GGUF-Motor.

Beim Lesen vieler Token rechnen die FP4-/FP8-Kerne von TensorRT (rechengebunden) schneller als
die Quant-Kerne des GGUF-Motors, beim Schreiben (ein Token je Schritt, speichergebunden) ist der
GGUF-Motor im Vorteil. Beide teilen sich deshalb die Arbeit – und den KV-Cache:
das Netz schreibt K und V direkt in die Caches des Baukasten-Modells (gleiche Form, gleicher Typ).

Ablauf (`anbinden`, nach dem Laden einer Engine):
  1. Einmessen: ein fester Text läuft durch das Modell, gemessen wird der Höchstwert jedes
     Matrix-Eingangs (für die Skalen der Aktivierungen) – nur, wenn kein Netz gespeichert ist.
  2. Bauen im Hintergrund: Gewichte aus der GGUF-Datei (CPU), als NVFP4/FP8/bf16 ins Netz.
  3. Anbinden: `modell.schnell = Leser` – der Baukasten fragt ihn bei langen Stücken.
Nicht abgedeckte Bauarten, LoRA, 8-Bit- oder geteilte KV-Caches: es rechnet der GGUF-Motor.
"""

from __future__ import annotations

import json
import threading
import time
import weakref
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

VERSION = 1
AB = 64                     # ab so vielen Token je Stück liest TensorRT
T_MAX = 8192                # längstes Stück: seltener Gewichte nachschieben (der Baukasten teilt sonst in 2048er)
FORMATE = ("fp4", "fp8", "bf16")
RESERVE = int(1.5 * 2**30)  # bleibt beim Lesen frei; TensorRT holt beim Lauf noch ~0,5 GB extra
MESS_RAND = 2.0             # Spielraum über dem gemessenen Höchstwert

# Fester Einmess-Text: Alltagssprache, Technik, Code, Zahlen – damit jede Matrix typische Werte sieht.
KALIBRIER_TEXT = (
    "Guten Morgen! Heute ist ein ruhiger Tag. Die Sonne scheint, und auf dem Tisch steht eine Tasse Tee. "
    "Kannst du mir kurz erklären, wie ein Elektromotor funktioniert? Ein Elektromotor wandelt elektrische "
    "Energie in Bewegung um: Strom fließt durch Spulen, das Magnetfeld dreht den Läufer.\n\n"
    "Hello! Could you summarise the main points of the meeting? 1) Budget approved, 2) release on 14 March, "
    "3) testing needs two more weeks. Please reply in a friendly tone and keep it short.\n\n"
    "```python\ndef fibonacci(n: int) -> list[int]:\n    folge = [0, 1]\n    while len(folge) < n:\n"
    "        folge.append(folge[-1] + folge[-2])\n    return folge[:n]\n\nprint(fibonacci(10))\n```\n"
    "Der Code oben berechnet die ersten zehn Fibonacci-Zahlen: 0, 1, 1, 2, 3, 5, 8, 13, 21, 34.\n\n"
    "Tabelle: | Monat | Umsatz | Kosten |\n| Januar | 12.400 € | 9.850 € |\n| Februar | 13.120 € | 10.010 € |\n"
    "Frage: Wie hoch ist der Gewinn im Februar? Antwort: 13.120 € − 10.010 € = 3.110 €.\n\n"
    "Wir planen einen Ausflug in die Berge. Was sollten wir einpacken? Wasser, Regenjacke, feste Schuhe, "
    "eine Karte, Pflaster und etwas zu essen. Bei Gewitter sofort absteigen und Grate meiden.\n"
    "SELECT name, COUNT(*) FROM bestellungen WHERE datum >= '2024-01-01' GROUP BY name ORDER BY 2 DESC;\n"
    "Die Antwort sollte klar, freundlich und vollständig sein. Danke!"
)


class NichtMoeglich(RuntimeError):
    """Dieses Modell (oder dieser Zustand) geht nicht über TensorRT."""


# ----------------------------------------------------------------------------- Bauplan

def bauplan(modell) -> dict:
    """Aufbau des Baukasten-Modells als reine Daten – oder NichtMoeglich mit Grund."""
    if type(modell).__name__ != "BaukastenModel":
        raise NichtMoeglich(f"Bauart {type(modell).__name__} wird nicht über TensorRT gerechnet")
    hp = modell.hp
    for schluessel, grund in (("layernorm", "LayerNorm"), ("attn_softcap", "Attention-Softcap"),
                              ("parallel", "parallele Blöcke"), ("glu_oai", "gpt-oss-SwiGLU")):
        if hp.get(schluessel):
            raise NichtMoeglich(f"{grund} wird nicht über TensorRT gerechnet")
    if hp["norm_groups"] != 1:
        raise NichtMoeglich("gruppierte Normen werden nicht über TensorRT gerechnet")
    if getattr(modell, "moe", False):
        raise NichtMoeglich("Mixture-of-Experts wird nicht über TensorRT gerechnet")
    ropes: list = []
    schichten = []
    for i, l in enumerate(modell.layers):
        if l.sinks is not None:
            raise NichtMoeglich("Attention-Sinks werden nicht über TensorRT gerechnet")
        if l.fenster and l.fenster < modell.n_ctx:
            raise NichtMoeglich("Schiebefenster kleiner als der Kontext")
        r = None
        if l.rope is not None:
            r = next((j for j, x in enumerate(ropes) if x is l.rope), None)
            if r is None:
                ropes.append(l.rope)
                r = len(ropes) - 1
        q_norm = None if l.q_norm is None else ("kopf" if l.q_norm.numel() == l.D else "alle")
        k_norm = None if l.k_norm is None else ("kopf" if l.k_norm.numel() == l.D else "alle")
        if l.gate is not None:
            ffn = "gated"
        elif l.gate_up:
            ffn = "gate_up"
        else:
            ffn = "einfach"
        schichten.append({"qkv": l.qkv is not None, "q_norm": q_norm, "k_norm": k_norm, "rope": r,
                          "ffn": ffn, "attn_norm": l.attn_norm is not None, "attn_post": l.attn_post is not None,
                          "ffn_norm": l.ffn_norm is not None, "ffn_post": l.ffn_post is not None})
    plan = {"hp": {k: hp[k] for k in ("arch", "n_layer", "n_embd", "n_heads", "n_kv_heads", "head_dim", "eps",
                                      "gelu", "attn_scale", "residual_scale", "ffn_norm_post_attn")},
            "q_temp": list(hp["q_temp"]) if hp["q_temp"] else None, "n_ctx": int(modell.n_ctx),
            "ropes": [{"dim": x.dim, "neox": bool(x.neox)} for x in ropes], "schichten": schichten}
    return plan


def _gguf_kennung(datei: Path) -> dict:
    st = Path(datei).stat()
    return {"datei": Path(datei).name, "groesse": st.st_size, "zeit": st.st_mtime_ns}


# ----------------------------------------------------------------------------- Einmessen

class _Messer:
    """Hülle um eine Matrix des Modells: merkt sich den Höchstwert ihres Eingangs."""

    def __init__(self, ziel, name: str, werte: dict):
        self.ziel, self.name, self.werte = ziel, name, werte

    def __call__(self, x, *a, **k):
        m = float(x.detach().abs().amax())
        self.werte[self.name] = max(self.werte.get(self.name, 0.0), m)
        return self.ziel(x, *a, **k)

    def __getattr__(self, attr):
        return getattr(self.ziel, attr)


def einmessen(engine) -> dict:
    """Höchstwerte aller Matrix-Eingänge für den Einmess-Text (Name -> float). Das Modell rechnet
    dabei normal; die Plätze im KV-Cache werden beim nächsten Gespräch überschrieben."""
    modell = engine.model
    werte: dict = {}
    huellen = []
    for i, l in enumerate(modell.layers):
        for attr, name in (("qkv", "attn"), ("q", "attn"), ("o", "o"), ("up", "ffn"), ("down", "down")):
            ziel = getattr(l, attr, None)
            if ziel is not None:
                setattr(l, attr, _Messer(ziel, f"{i}.{name}", werte))
                huellen.append((l, attr, ziel))
    try:
        ids = engine.tokenizer.encode(KALIBRIER_TEXT)[:T_MAX]
        engine._kv_room(len(ids))
        with torch.inference_mode():
            modell.forward(ids, 0)
        if modell.device.type == "cuda":
            torch.cuda.synchronize()
    finally:
        for l, attr, ziel in huellen:
            setattr(l, attr, ziel)
    return werte


# ----------------------------------------------------------------------------- Gewichte

class _Gewichte:
    """Liest Tensoren aus der GGUF-Datei auf der CPU (für den Bau im Hintergrund)."""

    def __init__(self, datei: Path):
        from ggufengine.gguf import GGUFFile
        self.gg = GGUFFile(str(datei))

    def has(self, name: str) -> bool:
        return name in self.gg.tensors

    def get(self, name: str) -> torch.Tensor:
        from ggufengine.quant import dequantize, raw_to_tensor
        ti = self.gg.tensors[name]
        roh = raw_to_tensor(self.gg.tensor_bytes(ti), "cpu")
        return dequantize(roh, ti.ggml_type, ti.n_elements, torch.float32).view(ti.shape)

    def close(self):
        self.gg.close()


# ----------------------------------------------------------------------------- Netz

class _Bauer(Netzbauer):
    """Baut das TensorRT-Netz Schicht für Schicht wie baukasten._Schicht.__call__."""

    def __init__(self, trt, net, plan: dict, gew: _Gewichte, mess: dict, fmt: str):
        super().__init__(trt, net)
        self.plan, self.gew, self.mess, self.fmt = plan, gew, mess, fmt

    def rms(self, x, w: torch.Tensor):
        return super().rms(x, w, self.plan["hp"]["eps"])

    def rope(self, x, kopfe: int, cos, sin, r: dict, pos):
        """RoPE auf x [1, Köpfe, T, D] wie ggufengine.RoPE: NeoX (Hälften) oder Paare, nur die
        ersten r["dim"] Merkmale. Die Rotary-Ebene von TensorRT liefert mit Positions-Eingabe
        wechselnde Formen an die folgenden Matrizen – deshalb aus Grundbausteinen."""
        trt = self.trt
        D, rd = self.plan["hp"]["head_dim"], r["dim"]
        h = rd // 2
        c = self.cast(self.umform(self.net.add_gather(cos, pos, 0).get_output(0), (1, 1, "T", h)), trt.float32)
        s = self.cast(self.umform(self.net.add_gather(sin, pos, 0).get_output(0), (1, 1, "T", h)), trt.float32)
        xf = self.cast(x, trt.float32)
        minus = trt.ElementWiseOperation.SUB
        if r["neox"]:
            x1 = self.scheibe(xf, (0, 0, 0, 0), (1, kopfe, "T", h))
            x2 = self.scheibe(xf, (0, 0, 0, h), (1, kopfe, "T", h))
            teile = [self.ew(self.mal(x1, c), self.mal(x2, s), minus), self.plus(self.mal(x2, c), self.mal(x1, s))]
        else:
            xr = self.umform(self.scheibe(xf, (0, 0, 0, 0), (1, kopfe, "T", rd)), (1, kopfe, "T", h, 2))
            xe = self.umform(self.scheibe(xr, (0, 0, 0, 0, 0), (1, kopfe, "T", h, 1)), (1, kopfe, "T", h))
            xo = self.umform(self.scheibe(xr, (0, 0, 0, 0, 1), (1, kopfe, "T", h, 1)), (1, kopfe, "T", h))
            ge = self.umform(self.ew(self.mal(xe, c), self.mal(xo, s), minus), (1, kopfe, "T", h, 1))
            go = self.umform(self.plus(self.mal(xo, c), self.mal(xe, s)), (1, kopfe, "T", h, 1))
            paare = self.net.add_concatenation([ge, go])
            paare.axis = 4
            teile = [self.umform(paare.get_output(0), (1, kopfe, "T", rd))]
        if rd < D:
            teile.append(self.scheibe(xf, (0, 0, 0, rd), (1, kopfe, "T", D - rd)))
        if len(teile) > 1:
            cat = self.net.add_concatenation(teile)
            cat.axis = 3
            out = cat.get_output(0)
        else:
            out = teile[0]
        return self.cast(out, self.A)

    def akt(self, x):
        return self.gelu_tanh(x) if self.plan["hp"]["gelu"] else self.silu(x)

    def linear(self, x, name: str, mess: str, teil=None):
        """x (A, [T, K]) @ W^T (+ Bias) -> A. teil=(von, bis): nur diese Ausgabe-Zeilen von W."""
        trt = self.trt
        w = self.gew.get(name + ".weight")
        if teil is not None:
            w = w[teil[0]:teil[1]]
        n, k = w.shape
        amax = max(self.mess.get(mess, 0.0), 1e-3) * MESS_RAND
        if self.fmt == "fp4" and k % 16 == 0:
            wdq = self.fp4_gewicht(*fp4_packen(w), n, k)
            a = self.fp4_eingang(x, amax)
        elif self.fmt in ("fp4", "fp8"):
            sw = max(float(w.abs().max()) / 448, 1e-12)
            q = (w / sw).to(torch.float8_e4m3fn).view(torch.uint8).contiguous().numpy()
            wdq = self.net.add_dequantize(self.konst(q, trt.fp8, (n, k), n * k), self.f32(sw, form=()),
                                          self.A).get_output(0)
            sa = self.f32(amax / 448, form=())
            a = self.net.add_dequantize(self.net.add_quantize(x, sa, trt.fp8).get_output(0), sa, self.A).get_output(0)
        else:
            wdq, a = self.bf16(w), x
        y = self.net.add_matrix_multiply(a, trt.MatrixOperation.NONE, wdq, trt.MatrixOperation.TRANSPOSE).get_output(0)
        if self.gew.has(name + ".bias"):
            b = self.gew.get(name + ".bias").reshape(-1)
            if teil is not None:
                b = b[teil[0]:teil[1]]
            y = self.plus(y, self.bf16(b.reshape(1, -1)))
        return y

    def norm_name(self, i: int, s: dict, art: str):
        hp = self.plan["hp"]
        namen = {"attn_norm": "attn_norm", "attn_post": "post_attention_norm", "ffn_norm": "ffn_norm",
                 "ffn_post": "post_ffw_norm"}
        if hp["ffn_norm_post_attn"]:
            namen["ffn_norm"], namen["attn_post"] = "post_attention_norm", None
        n = namen[art]
        return f"blk.{i}.{n}.weight" if n and s[art] else None

    # -- das ganze Netz
    def bauen(self):
        trt, net, plan = self.trt, self.net, self.plan
        hp = plan["hp"]
        E, H, Hkv, D = hp["n_embd"], hp["n_heads"], hp["n_kv_heads"], hp["head_dim"]
        h = net.add_input("h", trt.float32, (-1, E))
        self.T = self.laenge_von(h, 0)
        pos = net.add_input("pos", trt.int32, (1, -1))
        wi = net.add_input("wi", trt.int32, (1,))
        kvlen = net.add_input("kvlen", trt.int32, (1,))
        qfak = net.add_input("qfak", trt.float32, (1, 1, -1, 1)) if plan["q_temp"] else None
        n_ctx = plan["n_ctx"]
        tabellen = [(net.add_input(f"cos{j}", self.A, (n_ctx, r["dim"] // 2)),
                     net.add_input(f"sin{j}", self.A, (n_ctx, r["dim"] // 2))) for j, r in enumerate(plan["ropes"])]
        skala = self.f32(hp["attn_scale"], form=(1, 1, 1, 1))
        rest = hp["residual_scale"]
        rest_k = self.f32(rest, form=(1, 1)) if rest != 1.0 else None
        for i, s in enumerate(plan["schichten"]):
            p = f"blk.{i}."
            n = self.norm_name(i, s, "attn_norm")
            x = self.cast(self.rms(h, self.gew.get(n)) if n else h, self.A)
            if s["qkv"]:
                q = self.linear(x, p + "attn_qkv", f"{i}.attn", (0, H * D))
                k = self.linear(x, p + "attn_qkv", f"{i}.attn", (H * D, (H + Hkv) * D))
                v = self.linear(x, p + "attn_qkv", f"{i}.attn", ((H + Hkv) * D, (H + 2 * Hkv) * D))
            else:
                q = self.linear(x, p + "attn_q", f"{i}.attn")
                k = self.linear(x, p + "attn_k", f"{i}.attn")
                v = self.linear(x, p + "attn_v", f"{i}.attn")
            if s["q_norm"] == "alle":
                q = self.cast(self.rms(self.cast(q, trt.float32), self.gew.get(p + "attn_q_norm.weight")), self.A)
            if s["k_norm"] == "alle":
                k = self.cast(self.rms(self.cast(k, trt.float32), self.gew.get(p + "attn_k_norm.weight")), self.A)
            q = self.umform(q, (1, "T", H, D), (0, 2, 1, 3))          # [1, H, T, D]
            k = self.umform(k, (1, "T", Hkv, D), (0, 2, 1, 3))
            v = self.umform(v, (1, "T", Hkv, D), (0, 2, 1, 3))
            if s["q_norm"] == "kopf":
                q = self.cast(self.rms(self.cast(q, trt.float32), self.gew.get(p + "attn_q_norm.weight")), self.A)
            if s["k_norm"] == "kopf":
                k = self.cast(self.rms(self.cast(k, trt.float32), self.gew.get(p + "attn_k_norm.weight")), self.A)
            if s["rope"] is not None:
                r = plan["ropes"][s["rope"]]
                cos, sin = tabellen[s["rope"]]
                q, k = self.rope(q, H, cos, sin, r, pos), self.rope(k, Hkv, cos, sin, r, pos)
            qf = self.cast(q, trt.float32)
            if qfak is not None:
                qf = self.mal(qf, qfak)
            q = self.cast(self.mal(qf, skala), self.A)
            kc = net.add_input(f"k{i}", self.A, (1, Hkv, plan["n_ctx"], D))   # feste Länge: der ganze Kontext
            vc = net.add_input(f"v{i}", self.A, (1, Hkv, plan["n_ctx"], D))
            ku = net.add_kv_cache_update(kc, k, wi, trt.KVCacheMode.LINEAR)
            vu = net.add_kv_cache_update(vc, v, wi, trt.KVCacheMode.LINEAR)
            ku.get_output(0).name, vu.get_output(0).name = f"k{i}_neu", f"v{i}_neu"
            net.mark_output(ku.get_output(0))
            net.mark_output(vu.get_output(0))
            att = net.add_attention_v2(q, ku.get_output(0), vu.get_output(0), trt.AttentionNormalizationOp.SOFTMAX,
                                       trt.CausalMaskKind.LOWER_RIGHT)
            att.key_value_lengths = kvlen
            att.decomposable = True                 # kleine Köpfe ohne fertigen Kern: zerlegt rechnen
            o = self.umform(att.get_output(0), ("T", H * D), (0, 2, 1, 3), erst_perm=True)
            o = self.cast(self.linear(o, p + "attn_output", f"{i}.o"), trt.float32)
            if (n := self.norm_name(i, s, "attn_post")):
                o = self.rms(o, self.gew.get(n))
            h = self.plus(h, self.mal(o, rest_k) if rest_k is not None else o)
            n = self.norm_name(i, s, "ffn_norm")
            x = self.cast(self.rms(h, self.gew.get(n)) if n else h, self.A)
            if s["ffn"] == "gated":
                g = self.linear(x, p + "ffn_gate", f"{i}.ffn")
                u = self.linear(x, p + "ffn_up", f"{i}.ffn")
                y = self.linear(self.mal(self.akt(g), u), p + "ffn_down", f"{i}.down")
            elif s["ffn"] == "gate_up":
                ff = self.gew.gg.tensors[p + "ffn_up.weight"].shape[0] // 2
                g = self.linear(x, p + "ffn_up", f"{i}.ffn", (0, ff))
                u = self.linear(x, p + "ffn_up", f"{i}.ffn", (ff, 2 * ff))
                y = self.linear(self.mal(self.akt(g), u), p + "ffn_down", f"{i}.down")
            else:
                y = self.linear(self.akt(self.linear(x, p + "ffn_up", f"{i}.ffn")), p + "ffn_down", f"{i}.down")
            y = self.cast(y, trt.float32)
            if (n := self.norm_name(i, s, "ffn_post")):
                y = self.rms(y, self.gew.get(n))
            h = self.plus(h, self.mal(y, rest_k) if rest_k is not None else y)
        h.name = "aus"
        net.mark_output(h)


def _bauen_fn(plan: dict, datei: Path, mess: dict, fmt: str):
    def bauen(trt, builder):
        net = builder.create_network(0)
        gew = _Gewichte(datei)
        bauer = _Bauer(trt, net, plan, gew, mess, fmt)
        try:
            bauer.bauen()
        finally:
            gew.close()
        hp = plan["hp"]
        cfg = builder.create_builder_config()
        cfg.set_flag(trt.BuilderFlag.WEIGHT_STREAMING)   # was nicht ins VRAM passt, kommt je Lauf über PCIe
        prof = builder.create_optimization_profile()
        E = hp["n_embd"]
        t_max = min(T_MAX, plan["n_ctx"])
        t_opt = min(512, t_max)
        prof.set_shape("h", (1, E), (t_opt, E), (t_max, E))
        prof.set_shape("pos", (1, 1), (1, t_opt), (1, t_max))
        if plan["q_temp"]:
            prof.set_shape("qfak", (1, 1, 1, 1), (1, 1, t_opt, 1), (1, 1, t_max, 1))
        cfg.add_optimization_profile(prof)
        bauen.halten = bauer.halten                      # Puffer leben, bis der Bau fertig ist
        return net, cfg
    return bauen


# ----------------------------------------------------------------------------- Leser

class Leser:
    """Hängt am Baukasten-Modell (`modell.schnell`) und rechnet lange Stücke über TensorRT."""

    ab = AB

    def __init__(self, netz: laufzeit.Netz, plan: dict, modell, raum=None):
        self.netz, self.plan = netz, plan
        self.raum = raum                             # engine._kv_room: Caches wachsen lassen
        netz.reserve = RESERVE                       # Budget bemisst das Netz selbst, Arbeitsspeicher zuerst
        self.laeufe = 0
        ropes = []
        for l in modell.layers:
            if l.rope is not None and all(l.rope is not r for r in ropes):
                ropes.append(l.rope)
        self.tabellen = [(r.cos.to(modell.device, torch.bfloat16).contiguous(),
                          r.sin.to(modell.device, torch.bfloat16).contiguous()) for r in ropes]

    def parken(self):
        """Gewichte aus dem VRAM (Budget 0); beim nächsten Lauf wird neu bemessen."""
        self.netz.gewichte_weg()

    def aufwecken(self):
        self.netz.neu_bemessen = True

    def _caches(self, modell):
        """Die vollen KV-Caches in bf16 auf ganzer Kontext-Länge (das Netz braucht eine feste Länge)."""
        n = self.plan["n_ctx"]
        if modell.n_ctx != n:
            return None
        caches = [l.cache for l in modell.layers]
        if any(getattr(c, "int8", True) or c.geteilt or c.k.dtype != torch.bfloat16 for c in caches):
            return None
        if caches[0].k.shape[1] < n and self.raum is not None:
            self.raum(n)
        if any(c.k.shape[1] != n for c in caches):
            return None
        return caches

    def __call__(self, modell, tokens: list, pos: int, inject=None):
        if getattr(modell, "_nemicli_coding_lora", ""):
            return None
        T = len(tokens)
        if T > min(T_MAX, self.plan["n_ctx"]):
            return None
        caches = self._caches(modell)
        if caches is None:
            return None
        dev = modell.device
        tok = torch.tensor(tokens, device=dev, dtype=torch.long)
        h = modell.einbettung(tok, inject).float().contiguous()
        p = torch.arange(pos, pos + T, device=dev, dtype=torch.int32).view(1, T)
        wi = torch.tensor([pos], device=dev, dtype=torch.int32)
        kvlen = torch.tensor([pos + T], device=dev, dtype=torch.int32)
        aus = torch.empty_like(h)
        adr = {"h": h, "pos": p, "wi": wi, "kvlen": kvlen, "aus": aus}
        formen = {"h": tuple(h.shape), "pos": (1, T)}
        if self.plan["q_temp"]:
            beta, n = self.plan["q_temp"]
            qf = (1 + beta * torch.log1p(torch.div(p.view(-1).long(), int(n), rounding_mode="floor").float()))
            adr["qfak"] = qf.view(1, 1, T, 1).contiguous()
            formen["qfak"] = (1, 1, T, 1)
        for j, (c, s) in enumerate(self.tabellen):
            adr[f"cos{j}"], adr[f"sin{j}"] = c, s
        for i, c in enumerate(caches):
            k, v = c.k.unsqueeze(0), c.v.unsqueeze(0)
            adr[f"k{i}"] = adr[f"k{i}_neu"] = k
            adr[f"v{i}"] = adr[f"v{i}_neu"] = v
            formen[f"k{i}"] = formen[f"v{i}"] = tuple(k.shape)
        self.netz(adr, formen)
        self.netz.puffer_frei()                      # VRAM fürs Schreiben und andere Programme zurück
        self.laeufe += 1
        if self.laeufe in (1, 3):                    # übersetzte Kerne sichern: der nächste Start spart das
            self.netz.cache_sichern()
        if not bool(torch.isfinite(aus[-1]).all()):         # nie ungültige Werte weitergeben
            _DBG.ereignis("TensorRT-Leser lieferte ungültige Werte – abgeschaltet, der GGUF-Motor liest")
            try:
                import fehlerwaechter
                fehlerwaechter.befund("rechenfehler", "TensorRT-Leser lieferte ungültige Werte (NaN/Inf) – "
                                      "abgeschaltet, der GGUF-Motor rechnete das Stück neu")
            except Exception:
                pass
            modell.schnell = None
            return None                                     # der Baukasten rechnet das Stück neu
        return modell.kopf(modell.schluss_norm(aus[-1:]))


# ----------------------------------------------------------------------------- Anbinden

def format_waehlen(plan: dict, wunsch: str = "auto") -> str:
    if wunsch in FORMATE:
        return wunsch
    return "fp4"


def _schwach(obj):
    """Schwacher Verweis – Objekte, die keinen erlauben (einfache Platzhalter), werden fest gehalten."""
    try:
        return weakref.ref(obj)
    except TypeError:
        return lambda: obj


def anbinden(engine, datei, fmt: str = "auto", warten: bool = False, fertig=None) -> str:
    """Schnell-Leser für `engine` vorbereiten. Gibt sofort zurück (Bau im Hintergrund), mit
    warten=True erst nach dem Anbinden. Rückgabe: "" oder warum es nicht geht."""
    try:
        import modes
        if modes._wache_dienst:
            return "nicht im Wache-Prozess (zwei Leser bräuchten doppelt VRAM)"
    except ImportError:
        pass
    if not laufzeit.bereit():
        return laufzeit.grund()
    modell = engine.model
    if getattr(modell, "device", torch.device("cpu")).type != "cuda":
        return "nur auf der Grafikkarte"
    try:
        plan = bauplan(modell)
    except NichtMoeglich as exc:
        return str(exc)
    c = modell.layers[0].cache
    if getattr(c, "int8", False) or getattr(c, "geteilt", False):
        return "8-Bit- oder geteilter Gesprächsspeicher (VRAM knapp) – es liest der GGUF-Motor"
    fmt = format_waehlen(plan, fmt)
    teile = {"v": VERSION, "fmt": fmt, "t_max": T_MAX, "gguf": _gguf_kennung(Path(datei)),
             "plan": json.dumps(plan, sort_keys=True)}
    name = laufzeit.schluessel("chat", teile)
    gespeichert = (laufzeit.engine_ordner() / f"{name}.engine").is_file()
    mess = {} if gespeichert or fmt == "bf16" else einmessen(engine)

    # Der Bau-Faden hält Engine und Modell nur schwach: wird während des Baus (Sekunden, beim ersten Mal
    # Minuten) entladen, ist der Grafikspeicher gleich frei, und an ein entladenes Modell bindet nichts mehr an.
    engine_ref, modell_ref = _schwach(engine), _schwach(modell)

    def los():
        t0 = time.time()
        try:
            netz = laufzeit.holen("chat", teile, _bauen_fn(plan, Path(datei), mess, fmt), budget=0)
            gross = netz.engine.streamable_weights_size
            e, m = engine_ref(), modell_ref()
            if e is None or m is None or e.model is not m or getattr(e, "geleert", False):
                return
            leser = Leser(netz, plan, m, getattr(e, "_kv_room", None))
            del e
            m.prefill_chunk = min(T_MAX, plan["n_ctx"])         # die Engine liest in längeren Stücken
            m.schnell = leser
            _DBG.ereignis(f"TensorRT-Leser angebunden ({fmt}, Gewichte {gross / 2**30:.2f} GiB, "
                          f"{time.time() - t0:.0f} s)")
            if fertig:
                fertig("")
        except Exception as exc:
            _DBG.ereignis(f"TensorRT-Leser nicht gebaut: {exc}")
            if fertig:
                fertig(str(exc))
            if warten:
                raise

    if warten:
        los()
    else:
        threading.Thread(target=los, daemon=True, name="trt-chat-bau").start()
    return ""


def abbinden(engine) -> None:
    modell = getattr(engine, "model", None)
    if modell is not None and getattr(modell, "schnell", None) is not None:
        modell.schnell = None
        modell.__dict__.pop("prefill_chunk", None)
