"""
ltx2_netz.py - Der Video-und-Ton-Transformer von LTX-2.x (Lightricks) samt Text-Verbindern.

Je Block: Video-Selbst-Attention, Ton-Selbst-Attention, Text-Cross-Attention (beide Ströme, mit
Modulation von Text und Anfrage), Ton→Video und Video→Ton, dann die Feed-Forwards. Alle Normen ohne
Gewicht; Q/K-Norm über alle Köpfe; RoPE „split“ (LTX-2.3/2.5) oder „interleaved“; Gate je Kopf.

Liest diffusers-Namen und die Original-Namen von Lightricks (auch GGUF); die Text-Verbinder stecken im DiT.
"""

from __future__ import annotations

import math
from pathlib import Path

from bildmotoren.gemeinsam import Bloecke
from bildmotoren.gewichte import Quelle, namen, ohne_praefix, zu

EPS = 1e-6

# Original-Namen (Lightricks, ComfyUI, GGUF) -> diffusers-Namen
_UMBENENNEN = [
    ("audio_patchify_proj", "audio_proj_in"), ("patchify_proj", "proj_in"),
    ("av_ca_video_scale_shift_adaln_single", "av_cross_attn_video_scale_shift"),
    ("av_ca_a2v_gate_adaln_single", "av_cross_attn_video_a2v_gate"),
    ("av_ca_audio_scale_shift_adaln_single", "av_cross_attn_audio_scale_shift"),
    ("av_ca_v2a_gate_adaln_single", "av_cross_attn_audio_v2a_gate"),
    ("scale_shift_table_a2v_ca_video", "video_a2v_cross_attn_scale_shift_table"),
    ("scale_shift_table_a2v_ca_audio", "audio_a2v_cross_attn_scale_shift_table"),
    ("audio_prompt_adaln_single.", "audio_prompt_adaln."), ("prompt_adaln_single.", "prompt_adaln."),
    ("video_embeddings_connector.", "video_connector."), ("audio_embeddings_connector.", "audio_connector."),
    ("transformer_1d_blocks.", "transformer_blocks."),
    (".q_norm.", ".norm_q."), (".k_norm.", ".norm_k."),
]


def diffusers_name(k: str) -> str:
    for alt, neu in _UMBENENNEN:
        k = k.replace(alt, neu)
    if k.startswith("adaln_single."):
        k = "time_embed." + k[len("adaln_single."):]
    elif k.startswith("audio_adaln_single."):
        k = "audio_time_embed." + k[len("audio_adaln_single."):]
    return k


class Namen:
    """Eine Quelle, die unter diffusers-Namen gelesen wird – egal wie die Datei sie schreibt."""

    def __init__(self, q: Quelle):
        self.q = q
        self.karte = {diffusers_name(k): k for k in q.alle()}

    def hat(self, n): return n in self.karte
    def lin(self, n, dtype): return self.q.lin(self.karte[n + ".weight"][:-len(".weight")], dtype)
    def tensor(self, n, dtype=None): return self.q.tensor(self.karte[n], dtype)
    def alle(self): return list(self.karte)


def erkennen(alle: dict) -> bool:
    alle = {diffusers_name(k) for k in alle}
    return "audio_proj_in.weight" in alle and "av_cross_attn_video_a2v_gate.linear.weight" in alle


# ===========================================================================
#  Gewichte
# ===========================================================================

def _attn(q: Namen, p: str, dtype) -> dict:
    A = {"q": q.lin(p + "to_q", dtype), "k": q.lin(p + "to_k", dtype), "v": q.lin(p + "to_v", dtype),
         "out": q.lin(p + "to_out.0", dtype)}
    for n, x in (("qn", "norm_q"), ("kn", "norm_k")):
        A[n] = q.tensor(p + x + ".weight").float() if q.hat(p + x + ".weight") else None
    if q.hat(p + "to_gate_logits.weight"):
        A["gate"] = q.lin(p + "to_gate_logits", dtype)
    return A


def _ff(q: Namen, p: str, dtype) -> dict:
    return {"in": q.lin(p + "net.0.proj", dtype), "out": q.lin(p + "net.2", dtype)}


def _ada(q: Namen, p: str, dtype) -> dict:
    return {"l1": q.lin(p + "emb.timestep_embedder.linear_1", dtype),
            "l2": q.lin(p + "emb.timestep_embedder.linear_2", dtype), "lin": q.lin(p + "linear", dtype)}


def _block(q: Namen, i: int, dtype) -> dict:
    p = f"transformer_blocks.{i}."
    B = {n: _attn(q, p + n + ".", dtype) for n in ("attn1", "audio_attn1", "attn2", "audio_attn2",
                                                     "audio_to_video_attn", "video_to_audio_attn")}
    B["ff"], B["audio_ff"] = _ff(q, p + "ff.", dtype), _ff(q, p + "audio_ff.", dtype)
    for t in ("scale_shift_table", "audio_scale_shift_table", "prompt_scale_shift_table",
              "audio_prompt_scale_shift_table", "video_a2v_cross_attn_scale_shift_table",
              "audio_a2v_cross_attn_scale_shift_table"):
        if q.hat(p + t):
            B[t] = q.tensor(p + t).float()
    return B


def _verbinder(q: Namen, p: str, dtype) -> dict | None:
    if not q.hat(p + "learnable_registers"):
        return None
    n = 1 + max(int(k.split(".")[2]) for k in q.alle() if k.startswith(p + "transformer_blocks."))
    return {"register": q.tensor(p + "learnable_registers").float(),
            "bloecke": [{"attn": _attn(q, f"{p}transformer_blocks.{i}.attn1.", dtype),
                         "ff": _ff(q, f"{p}transformer_blocks.{i}.ff.", dtype)} for i in range(n)]}


def laden(pfad: Path, dtype) -> dict:
    """DiT im Arbeitsspeicher: kleine Teile, Blöcke und (falls in der Datei) die Text-Verbinder."""
    _, vors = ohne_praefix(namen(pfad))
    with Quelle(pfad, vors) as roh:
        q = Namen(roh)
        n = 1 + max(int(k.split(".")[1]) for k in q.alle() if k.startswith("transformer_blocks."))
        klein = {"proj_in": q.lin("proj_in", dtype), "audio_proj_in": q.lin("audio_proj_in", dtype),
                 "proj_out": q.lin("proj_out", dtype), "audio_proj_out": q.lin("audio_proj_out", dtype),
                 "sst": q.tensor("scale_shift_table").float(), "audio_sst": q.tensor("audio_scale_shift_table").float()}
        for n_ada in ("time_embed", "audio_time_embed", "av_cross_attn_video_scale_shift",
                      "av_cross_attn_audio_scale_shift", "av_cross_attn_video_a2v_gate",
                      "av_cross_attn_audio_v2a_gate", "prompt_adaln", "audio_prompt_adaln"):
            if q.hat(n_ada + ".linear.weight"):
                klein[n_ada] = _ada(q, n_ada + ".", dtype)
        if q.hat("keyframes_abs_pos_embedding"):
            klein["keyframes"] = q.tensor("keyframes_abs_pos_embedding").float()
        verbinder = {s: _verbinder(q, f"{s}_connector.", dtype) for s in ("video", "audio")}
        bloecke = [_block(q, i, dtype) for i in range(n)]
    return {"klein": klein, "bloecke": bloecke, "verbinder": verbinder}


# ===========================================================================
#  Bausteine
# ===========================================================================

def _rms(x, w=None, eps=EPS):
    import torch.nn.functional as F
    return F.rms_norm(x.float(), (x.shape[-1],), weight=None if w is None else w.to(x.device), eps=eps)


def _sinus(t, dim=256):
    import torch
    half = dim // 2
    f = torch.exp(-math.log(10000.0) * torch.arange(half, dtype=torch.float32, device=t.device) / half)
    a = t[:, None].float() * f[None]
    return torch.cat([torch.cos(a), torch.sin(a)], -1)


def ada(A: dict, t, dtype):
    """AdaLayerNormSingle: (Modulation (1, n·dim), eingebettete Zeit (1, dim))."""
    import torch.nn.functional as F
    emb = A["l2"](F.silu(A["l1"](_sinus(t).to(dtype))))
    return A["lin"](F.silu(emb)).float(), emb.float()


def rope_tabelle(gitter, dim: int, koepfe: int, theta: float = 10000.0, art: str = "split"):
    """gitter (T, Achsen) schon auf [0, 1] normiert -> cos, sin für „split“ (H, T, D/2) oder „interleaved“ (T, dim)."""
    import torch
    T, achsen = gitter.shape
    n = dim // (2 * achsen)
    hoch = torch.pow(theta, torch.linspace(0.0, 1.0, n, dtype=torch.float64, device=gitter.device))
    f = (hoch * math.pi / 2.0).float()
    w = ((gitter.float()[:, :, None] * 2 - 1) * f).transpose(-1, -2).flatten(1)       # (T, achsen·n)
    if art == "interleaved":
        cos, sin = w.cos().repeat_interleave(2, -1), w.sin().repeat_interleave(2, -1)
        rest = dim - cos.shape[-1]
        if rest:
            cos = torch.cat([torch.ones(T, rest, device=w.device), cos], -1)
            sin = torch.cat([torch.zeros(T, rest, device=w.device), sin], -1)
        return cos, sin
    rest = dim // 2 - w.shape[-1]
    cos, sin = w.cos(), w.sin()
    if rest:
        cos = torch.cat([torch.ones(T, rest, device=w.device), cos], -1)
        sin = torch.cat([torch.zeros(T, rest, device=w.device), sin], -1)
    return cos.view(T, koepfe, -1).transpose(0, 1), sin.view(T, koepfe, -1).transpose(0, 1)


def _rope_an(x, cs, art: str):
    """x (T, H·D) float -> gedreht, gleiche Form. Bei „split“ bestimmt die Tabelle die Kopfteilung."""
    import torch
    cos, sin = cs
    T = x.shape[0]
    if art == "interleaved":
        a, b = x.unflatten(-1, (-1, 2)).unbind(-1)
        return x * cos + torch.stack([-b, a], -1).flatten(-2) * sin
    h = x.view(T, cos.shape[0], -1).transpose(0, 1)                  # (H, T, D)
    r = h.shape[-1] // 2
    e, z = h[..., :r], h[..., r:]
    h = torch.cat([e * cos - z * sin, z * cos + e * sin], -1)
    return h.transpose(0, 1).reshape(T, -1)


def attention(A, x, kv, koepfe, dtype, cs_q=None, cs_k=None, art="split", maske=None):
    """x (T, dim) float, kv (S, dim_kv) float oder None -> (T, dim) float."""
    import torch.nn.functional as F
    kv = x if kv is None else kv
    xd, kvd = x.to(dtype), kv.to(dtype)
    q = _rms(A["q"](xd), A["qn"])
    k = _rms(A["k"](kvd), A["kn"])
    v = A["v"](kvd)
    if cs_q is not None:
        q = _rope_an(q, cs_q, art)
        k = _rope_an(k, cs_q if cs_k is None else cs_k, art)
    T, S = q.shape[0], k.shape[0]
    q, k, v = (t.to(dtype).view(n, koepfe, -1).transpose(0, 1) for t, n in ((q, T), (k, S), (v, S)))
    o = F.scaled_dot_product_attention(q, k, v, attn_mask=maske).transpose(0, 1)        # (T, H, D)
    if "gate" in A:
        o = o * (2.0 * A["gate"](xd).float().sigmoid()).to(o.dtype)[..., None]
    return A["out"](o.reshape(T, -1)).float()


def ff(Fw, x, dtype):
    import torch.nn.functional as F
    return Fw["out"](F.gelu(Fw["in"](x.to(dtype)), approximate="tanh")).float()


def _mod(tabelle, temb):
    """Tabelle (n, dim) + Modulation (1, n·dim) -> n Vektoren (dim,)."""
    return (tabelle.to(temb.device) + temb.view(tabelle.shape[0], -1)).unbind(0)


# ===========================================================================
#  Text-Verbinder
# ===========================================================================

def text_projektion(zustaende, proj, breite: int, dtype):
    """LTX-2.3/2.5: alle Gemma-Zustände (n, C, Schichten) je Token und Schicht RMS-normiert, nebeneinander,
    mit sqrt(breite / C) skaliert und projiziert -> (n, breite)."""
    import math as _m
    import torch
    z = zustaende.float()
    z = z * torch.rsqrt(z.pow(2).mean(1, keepdim=True) + 1e-6)
    return proj(((z.flatten(1)) * _m.sqrt(breite / z.shape[1])).to(dtype)).float()


def verbinder(V: dict, x, n_echt: int, laenge: int, koepfe: int, dtype, theta: float = 10000.0,
              basis: int = 4096, art: str = "split"):
    """Die echten Text-Token (n_echt, dim) vorn, dahinter die gelernten Register bis `laenge`, dann die Blöcke.
    Gibt (laenge, dim) float zurück."""
    import torch
    reg = V["register"].to(x.device)
    rest = torch.arange(n_echt, laenge, device=x.device) % reg.shape[0]
    h = torch.cat([x.float(), reg[rest]], 0)
    gitter = (torch.arange(laenge, device=x.device, dtype=torch.float32) / basis)[:, None]
    cs = rope_tabelle(gitter, h.shape[-1], koepfe, theta, art)
    for B in V["bloecke"]:
        h = h + attention(B["attn"], _rms(h), None, koepfe, dtype, cs, None, art)
        h = h + ff(B["ff"], _rms(h), dtype)
    return _rms(h)


# ===========================================================================
#  Positionen
# ===========================================================================

def video_gitter(f: int, h: int, w: int, fps: float, faktoren=(8, 32, 32), versatz: int = 1,
                 max_pos=(20, 2048, 2048), device=None):
    """Mittelpunkte der Latent-Zellen in Sekunden/Pixeln, normiert auf die Maximal-Positionen -> (f·h·w, 3)."""
    import torch
    gf, gh, gw = (torch.arange(n, dtype=torch.float32, device=device) for n in (f, h, w))
    g = torch.stack(torch.meshgrid(gf, gh, gw, indexing="ij"), 0).flatten(1)          # (3, N)
    start, ende = g * torch.tensor(faktoren, device=device).view(3, 1), (g + 1) * torch.tensor(faktoren, device=device).view(3, 1)
    for p in (start, ende):
        p[0] = (p[0] + versatz - faktoren[0]).clamp(min=0) / fps
    mitte = (start + ende) / 2
    return (mitte / torch.tensor(max_pos, device=device, dtype=torch.float32).view(3, 1)).T


def audio_gitter(n: int, rate: int = 16000, hop: int = 160, faktor: int = 4, versatz: int = 1, max_pos: int = 20,
                 device=None):
    import torch
    g = torch.arange(n, dtype=torch.float32, device=device)
    start = (g * faktor + versatz - faktor).clamp(min=0) * hop / rate
    ende = ((g + 1) * faktor + versatz - faktor).clamp(min=0) * hop / rate
    return (((start + ende) / 2) / max_pos)[:, None]


# ===========================================================================
#  Netz
# ===========================================================================

class Netz:
    """Ein Durchlauf: Video-Token (N, 128) und Ton-Token (M, 128) -> Geschwindigkeiten."""

    def __init__(self, ram: dict, device, dtype, cfg: dict, platz: int | None = None):
        self.device, self.dtype, self.cfg = device, dtype, cfg
        self.k = zu(ram["klein"], device)
        self.bloecke = Bloecke(ram["bloecke"], device, platz)
        B0 = ram["bloecke"][0]
        self.dim = B0["attn1"]["q"].n_out
        self.adim = B0["audio_attn1"]["q"].n_out
        self.ca_dim = B0["audio_to_video_attn"]["q"].n_out

    def __call__(self, x, a, ctx, actx, sigma: float, form: tuple, fps: float, maske=None, amaske=None):
        """x (N, 128), a (M, 128) float32; ctx/actx Text nach den Verbindern; form = (f, h, w) der Latents."""
        import torch
        c, dt, k, dev = self.cfg, self.dtype, self.k, self.device
        H, AH = c["koepfe"], c["audio_koepfe"]
        art = c.get("rope_type", "split")
        t = torch.full((1,), sigma * c.get("zeit_faktor", 1000.0), device=dev)
        temb, emb = ada(k["time_embed"], t, dt)
        atemb, aemb = ada(k["audio_time_embed"], t, dt)
        ca_v, _ = ada(k["av_cross_attn_video_scale_shift"], t, dt)
        ca_a, _ = ada(k["av_cross_attn_audio_scale_shift"], t, dt)
        g_v, _ = ada(k["av_cross_attn_video_a2v_gate"], t, dt)
        g_a, _ = ada(k["av_cross_attn_audio_v2a_gate"], t, dt)
        p_v = ada(k["prompt_adaln"], t, dt)[0] if "prompt_adaln" in k else None
        p_a = ada(k["audio_prompt_adaln"], t, dt)[0] if "audio_prompt_adaln" in k else None

        vg = video_gitter(*form, fps, device=dev)
        ag = audio_gitter(a.shape[0], device=dev)
        cs_v = rope_tabelle(vg, self.dim, H, art=art)
        cs_a = rope_tabelle(ag, self.adim, AH, art=art)
        cs_cv = rope_tabelle(vg[:, :1], self.ca_dim, H, art=art)          # nur die Zeit-Achse
        cs_ca = rope_tabelle(ag, self.ca_dim, AH, art=art)

        x = k["proj_in"](x.to(dt)).float()
        a = k["audio_proj_in"](a.to(dt)).float()
        for B in self.bloecke:
            x, a = block(B, x, a, ctx, actx, temb, atemb, ca_v, ca_a, g_v, g_a, p_v, p_a,
                         cs_v, cs_a, cs_cv, cs_ca, H, AH, dt, art, maske, amaske)
        sh, sc = (k["sst"].to(dev) + emb.view(1, -1)).unbind(0)
        x = k["proj_out"](((_ln(x) * (1 + sc) + sh)).to(dt)).float()
        ash, asc = (k["audio_sst"].to(dev) + aemb.view(1, -1)).unbind(0)
        a = k["audio_proj_out"](((_ln(a) * (1 + asc) + ash)).to(dt)).float()
        return x, a


def _ln(x):
    import torch.nn.functional as F
    return F.layer_norm(x.float(), (x.shape[-1],), eps=1e-6)


def block(B, x, a, ctx, actx, temb, atemb, ca_v, ca_a, g_v, g_a, p_v, p_a, cs_v, cs_a, cs_cv, cs_ca,
          H, AH, dt, art, maske=None, amaske=None):
    m = _mod(B["scale_shift_table"], temb)
    am = _mod(B["audio_scale_shift_table"], atemb)
    x = x + attention(B["attn1"], _rms(x) * (1 + m[1]) + m[0], None, H, dt, cs_v, None, art) * m[2]
    a = a + attention(B["audio_attn1"], _rms(a) * (1 + am[1]) + am[0], None, AH, dt, cs_a, None, art) * am[2]

    kv_v, kv_a = ctx, actx
    if "prompt_scale_shift_table" in B:
        if p_v is not None:
            sh, sc = _mod(B["prompt_scale_shift_table"], p_v)
        else:
            sh, sc = B["prompt_scale_shift_table"].to(x.device).unbind(0)
        kv_v = ctx * (1 + sc) + sh
        if p_a is not None:
            ash, asc = _mod(B["audio_prompt_scale_shift_table"], p_a)
        else:
            ash, asc = B["audio_prompt_scale_shift_table"].to(x.device).unbind(0)
        kv_a = actx * (1 + asc) + ash
    q = _rms(x)
    if len(m) == 9:
        q = q * (1 + m[7]) + m[6]
    o = attention(B["attn2"], q, kv_v, H, dt, maske=maske)
    x = x + (o * m[8] if len(m) == 9 else o)
    q = _rms(a)
    if len(am) == 9:
        q = q * (1 + am[7]) + am[6]
    o = attention(B["audio_attn2"], q, kv_a, AH, dt, maske=amaske)
    a = a + (o * am[8] if len(am) == 9 else o)

    nx, na = _rms(x), _rms(a)
    tv, ta = B["video_a2v_cross_attn_scale_shift_table"], B["audio_a2v_cross_attn_scale_shift_table"]
    v_sc_a2v, v_sh_a2v, v_sc_v2a, v_sh_v2a = _mod(tv[:4], ca_v)
    a_sc_a2v, a_sh_a2v, a_sc_v2a, a_sh_v2a = _mod(ta[:4], ca_a)
    gate_a2v = _mod(tv[4:], g_v)[0]
    gate_v2a = _mod(ta[4:], g_a)[0]
    x = x + gate_a2v * attention(B["audio_to_video_attn"], nx * (1 + v_sc_a2v) + v_sh_a2v,
                                 na * (1 + a_sc_a2v) + a_sh_a2v, AH, dt, cs_cv, cs_ca, art)
    a = a + gate_v2a * attention(B["video_to_audio_attn"], na * (1 + a_sc_v2a) + a_sh_v2a,
                                 nx * (1 + v_sc_v2a) + v_sh_v2a, AH, dt, cs_ca, cs_cv, art)
    x = x + ff(B["ff"], _rms(x) * (1 + m[4]) + m[3], dt) * m[5]
    a = a + ff(B["audio_ff"], _rms(a) * (1 + am[4]) + am[3], dt) * am[5]
    return x, a
