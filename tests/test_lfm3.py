"""LFM3 im GGUF-Motor: Rechnung gegen eine Nachbildung des Referenzmodells (Mini-Zufallsmodell),
Fenster-Ring im Merkpunkt, Augen (lfm3vl), Chat-Format, Werkzeug-Dialekt, Denken und die Familie."""
import gc
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "tests", "engines"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import torch.nn.functional as F
except Exception:                                   # ohne torch: überspringen
    torch = None

# 8 Schichten: conv, voll, conv, Fenster, conv, voll, conv, Fenster (wie das Original im Kleinen)
TYPEN = ["conv", "full", "conv", "swa", "conv", "full", "conv", "swa"]
E, H, HKV, D, FF, V, K, FENSTER, EPS = 64, 4, 2, 16, 96, 96, 7, 8, 1e-5
TEIL = 0.5                                          # Partial RoPE der vollen Schichten


def _gewichte():
    g = torch.Generator().manual_seed(0)
    r = lambda *s, f=0.2: torch.randn(*s, generator=g) * f
    eins = lambda n: 1 + r(n, f=0.1)
    w = {"token_embd.weight": r(V, E), "output_norm.weight": eins(E)}
    winkel = int(TEIL * D) // 2
    w["rope_freqs.weight"] = torch.tensor([1.0] * winkel + [1e30] * (D // 2 - winkel))
    for i, typ in enumerate(TYPEN):
        b = f"blk.{i}."
        for n in ("attn_norm", "ffn_norm", "post_attention_norm", "post_ffw_norm"):
            w[b + n + ".weight"] = eins(E)
        w[b + "ffn_gate.weight"], w[b + "ffn_up.weight"], w[b + "ffn_down.weight"] = r(FF, E), r(FF, E), r(E, FF)
        if typ == "conv":
            w[b + "shortconv.in_proj.weight"] = r(3 * E, E)
            w[b + "shortconv.conv.weight"] = r(E, K)
            w[b + "shortconv.out_proj.weight"] = r(E, E)
        else:
            w[b + "attn_q.weight"], w[b + "attn_k.weight"] = r(H * D, E), r(HKV * D, E)
            w[b + "attn_v.weight"], w[b + "attn_output.weight"] = r(HKV * D, E), r(E, H * D)
            w[b + "attn_q_norm.weight"], w[b + "attn_k_norm.weight"] = eins(D), eins(D)
            w[b + "attn_gate.weight"] = r(H, E)
    return w


def _meta():
    a = lambda s: f"lfm3.{s}"
    return {"general.architecture": "lfm3", a("block_count"): len(TYPEN), a("context_length"): 128,
            a("embedding_length"): E, a("feed_forward_length"): FF, a("attention.head_count"): H,
            a("attention.head_count_kv"): [0 if t == "conv" else HKV for t in TYPEN],
            a("attention.key_length"): D, a("attention.value_length"): D,
            a("attention.layer_norm_rms_epsilon"): EPS, a("rope.freq_base"): 1e6, a("rope.freq_base_swa"): 1e4,
            a("rope.dimension_count"): D, a("attention.sliding_window"): FENSTER,
            a("attention.sliding_window_pattern"): [t == "swa" for t in TYPEN], a("shortconv.l_cache"): K}


def _referenz(w, ids):
    """Vorwärtsrechnung wie modeling_lfm3 (Lfm3ForCausalLM), unabhängig vom Motor geschrieben."""
    T = len(ids)
    pos = torch.arange(T)
    rms = lambda x, g: x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + EPS) * g

    def rope(x, basis, teil):
        winkel = int(teil * D) // 2
        inv = 1.0 / (basis ** (torch.arange(0, 2 * winkel, 2).float() / D))
        inv = torch.cat([inv, torch.zeros(D // 2 - winkel)])
        f = pos[:, None].float() * inv
        emb = torch.cat([f, f], -1)
        cos, sin = emb.cos()[:, None], emb.sin()[:, None]
        x1, x2 = x[..., :D // 2], x[..., D // 2:]
        return x * cos + torch.cat([-x2, x1], -1) * sin

    h = w["token_embd.weight"][ids]
    for i, typ in enumerate(TYPEN):
        b = f"blk.{i}."
        x = rms(h, w[b + "attn_norm.weight"])
        if typ == "conv":
            bb, c, xx = (x @ w[b + "shortconv.in_proj.weight"].T).chunk(3, -1)
            bx = (bb * xx).T[None]
            conv = F.conv1d(bx, w[b + "shortconv.conv.weight"][:, None, :], padding=K - 1, groups=E)[0, :, :T]
            out = (c * conv.T) @ w[b + "shortconv.out_proj.weight"].T
        else:
            q = rms((x @ w[b + "attn_q.weight"].T).view(T, H, D), w[b + "attn_q_norm.weight"])
            k = rms((x @ w[b + "attn_k.weight"].T).view(T, HKV, D), w[b + "attn_k_norm.weight"])
            v = (x @ w[b + "attn_v.weight"].T).view(T, HKV, D)
            basis, teil = (1e6, TEIL) if typ == "full" else (1e4, 1.0)
            q, k = rope(q, basis, teil), rope(k, basis, teil)
            k, v = k.repeat_interleave(H // HKV, 1), v.repeat_interleave(H // HKV, 1)
            s = torch.einsum("qhd,khd->hqk", q, k) * D ** -0.5
            dist = pos[:, None] - pos[None]
            maske = dist >= 0
            if typ == "swa":
                maske &= dist < FENSTER
            o = torch.einsum("hqk,khd->qhd", torch.softmax(s.masked_fill(~maske, float("-inf")), -1), v)
            o = o * torch.sigmoid(x @ w[b + "attn_gate.weight"].T)[..., None]
            out = o.reshape(T, -1) @ w[b + "attn_output.weight"].T
        h = h + rms(out, w[b + "post_attention_norm.weight"])
        y = rms(h, w[b + "ffn_norm.weight"])
        f = (F.silu(y @ w[b + "ffn_gate.weight"].T) * (y @ w[b + "ffn_up.weight"].T)) @ w[b + "ffn_down.weight"].T
        h = h + rms(f, w[b + "post_ffw_norm.weight"])
    return rms(h, w["output_norm.weight"]) @ w["token_embd.weight"].T


@unittest.skipIf(torch is None, "torch fehlt")
class Lfm3Motor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gguf_testbau as B
        from ggufengine.gguf import GGUFFile
        from ggufengine.models import build_model
        cls.w = _gewichte()
        fd, cls.pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        B.schreibe_gguf(cls.pfad, _meta(), cls.w)
        cls.gg = GGUFFile(cls.pfad)
        cls.modell = build_model(cls.gg, torch.device("cpu"), torch.float32, 64, quant=False)
        cls.ids = torch.randint(0, V, (40,), generator=torch.Generator().manual_seed(1))
        with torch.no_grad():
            cls.ref = _referenz(cls.w, cls.ids)

    @classmethod
    def tearDownClass(cls):
        del cls.modell
        gc.collect()
        cls.gg.close()
        os.unlink(cls.pfad)

    def _gleich(self, logits, j):
        diff = float((logits - self.ref[j]).abs().max())
        self.assertLess(diff, 2e-3 * max(1.0, float(self.ref[j].abs().max())), f"Position {j}: {diff:.2e}")

    def test_bauart_und_ringe(self):
        m = self.modell
        self.assertEqual(type(m).__name__, "HybridModel")
        self.assertTrue(m.ringe)
        fenster = [b.attn.window for b in m.blocks if b.attn is not None]
        self.assertEqual(fenster, [0, FENSTER, 0, FENSTER])

    def test_einlesen_und_einzelschritte(self):
        m = self.modell
        m.reset()
        self._gleich(m.forward(self.ids[:20].tolist(), 0), 19)
        for j in range(20, 40):                     # weit über das Fenster hinaus
            self._gleich(m.forward([int(self.ids[j])], j), j)

    def test_merkpunkt_mit_ring(self):
        """Ein Merkpunkt bringt den Ring zurück, auch wenn danach viele Token ihn überschrieben haben."""
        m = self.modell
        m.reset()
        m.forward(self.ids[:16].tolist(), 0)
        punkt = m.checkpoint_state()
        for j in range(16, 40):
            m.forward([int(self.ids[(j * 7) % V % 40])], j)    # fremde Token überschreiben den Ring
        m.restore_checkpoint(punkt)
        self._gleich(m.forward(self.ids[16:30].tolist(), 16), 29)

    def test_zu_viele_token_je_aufruf(self):
        from ggufengine.models.common import PREFILL_CHUNK
        m = self.modell
        self.assertEqual(m.prefill_chunk, PREFILL_CHUNK)
        vorher, m.n_ctx = m.n_ctx, 4 * PREFILL_CHUNK               # nur die Grenze je Aufruf prüfen
        try:
            with self.assertRaises(ValueError):
                m._check([1] * (PREFILL_CHUNK + 1), 0)
        finally:
            m.n_ctx = vorher


# Augen im Kleinen: 4 Schichten (conv, Attention, conv, Attention), Breite 32, 4 Köpfe, 2 kv-Köpfe
V_TYPEN = ["conv", "attn", "conv", "attn"]
VE, VH, VKV, VFF, VP, VAUS, VMM = 32, 4, 2, 48, 4, 24, 40


def _v_gewichte():
    g = torch.Generator().manual_seed(2)
    r = lambda *s, f=0.3: torch.randn(*s, generator=g) * f
    eins = lambda n: 1 + r(n, f=0.1)
    D = VE // VH
    w = {"v.patch_embd.weight": r(VE, 3, VP, VP), "v.patch_embd.weight.1": r(VE, 3, VP, VP),
         "v.embedding_norm.weight": eins(VE), "mm.input_norm.weight": eins(VE), "mm.input_norm.bias": r(VE, f=0.1),
         "mm.0.weight": r(VMM, 4 * VE), "mm.0.bias": r(VMM, f=0.1), "mm.2.weight": r(VAUS, VMM),
         "mm.2.bias": r(VAUS, f=0.1)}
    for i, typ in enumerate(V_TYPEN):
        p = f"v.blk.{i}."
        w[p + "ln1.weight"], w[p + "ln2.weight"] = eins(VE), eins(VE)
        w[p + "ffn_gate.weight"], w[p + "ffn_up.weight"], w[p + "ffn_down.weight"] = r(VFF, VE), r(VFF, VE), r(VE, VFF)
        if typ == "conv":
            w[p + "conv2d_in_proj.weight"], w[p + "conv2d_out_proj.weight"] = r(3 * VE, VE), r(VE, VE)
            w[p + "conv2d_spatial.weight"] = r(VE, 1, 3, 3)
        else:
            w[p + "attn_q.weight"], w[p + "attn_k.weight"] = r(VE, VE), r(VKV * D, VE)
            w[p + "attn_v.weight"], w[p + "attn_out.weight"] = r(VKV * D, VE), r(VE, VE)
            w[p + "attn_q_norm.weight"], w[p + "attn_k_norm.weight"] = eins(D), eins(D)
    return w


def _v_meta():
    c = lambda s: f"clip.vision.{s}"
    return {"general.architecture": "clip", "clip.has_vision_encoder": True, c("projector_type"): "lfm3vl",
            c("embedding_length"): VE, c("attention.head_count"): VH, c("attention.head_count_kv"): VKV,
            c("block_count"): len(V_TYPEN), c("patch_size"): VP, c("projection_dim"): VAUS,
            c("attention.layer_norm_epsilon"): EPS, c("spatial_merge_size"): 2}


def _v_referenz(w, pix):
    """Wie modeling_lfm3_vl (Lfm3VlVisionModel + Projektor): Patchifier mit zwei Zeit-Abgriffen, Reihenfolge
    in 2x2-Blöcken, axiale RoPE, Faltung im Raster – unabhängig vom Motor geschrieben."""
    D, m = VE // VH, 2
    rms = lambda x, g: x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + EPS) * g
    _, H, W = pix.shape
    gh, gw = H // VP, W // VP
    # wie der Bildprozessor: (C, T, ph, pw) je Patch, beide Zeit-Bilder gleich
    p = pix.reshape(3, gh // m, m, VP, gw // m, m, VP).permute(1, 4, 2, 5, 0, 3, 6)
    p = p.unsqueeze(5).expand(-1, -1, -1, -1, -1, 2, -1, -1).reshape(gh * gw, 3 * 2 * VP * VP)
    gewicht = torch.stack([w["v.patch_embd.weight"], w["v.patch_embd.weight.1"]], 2).reshape(VE, -1)
    h = rms(p @ gewicht.T, w["v.embedding_norm.weight"])
    hp, wp = torch.meshgrid(torch.arange(gh), torch.arange(gw), indexing="ij")
    block = lambda t: t.reshape(gh // m, m, gw // m, m).transpose(1, 2).flatten()
    pos = torch.stack([block(hp), block(wp)], 1).float()
    inv = 1.0 / (1e6 ** (torch.arange(0, D // 2, 2).float() / (D // 2)))
    f = pos[..., None] * inv
    f = torch.cat([f[:, 0], f[:, 1]], -1)
    cos, sin = torch.cat([f, f], -1).cos()[:, None], torch.cat([f, f], -1).sin()[:, None]
    dreh = lambda x: x * cos + torch.cat([-x[..., D // 2:], x[..., :D // 2]], -1) * sin
    N = h.shape[0]
    for i, typ in enumerate(V_TYPEN):
        b = f"v.blk.{i}."
        x = rms(h, w[b + "ln1.weight"])
        if typ == "conv":
            bb, c, xx = (x @ w[b + "conv2d_in_proj.weight"].T).chunk(3, -1)
            fr = (bb * xx).view(gh // m, gw // m, m, m, -1).permute(4, 0, 2, 1, 3).reshape(1, -1, gh, gw)
            fr = F.conv2d(fr, w[b + "conv2d_spatial.weight"], padding=1, groups=VE)
            fr = fr.view(-1, gh // m, m, gw // m, m).permute(1, 3, 2, 4, 0).reshape(N, -1)
            out = (c * fr) @ w[b + "conv2d_out_proj.weight"].T
        else:
            q = dreh(rms((x @ w[b + "attn_q.weight"].T).view(N, VH, D), w[b + "attn_q_norm.weight"]))
            k = dreh(rms((x @ w[b + "attn_k.weight"].T).view(N, VKV, D), w[b + "attn_k_norm.weight"]))
            v = (x @ w[b + "attn_v.weight"].T).view(N, VKV, D)
            k, v = k.repeat_interleave(VH // VKV, 1), v.repeat_interleave(VH // VKV, 1)
            a = torch.softmax(torch.einsum("qhd,khd->hqk", q, k) * D ** -0.5, -1)
            out = torch.einsum("hqk,khd->qhd", a, v).reshape(N, -1) @ w[b + "attn_out.weight"].T
        h = h + out
        y = rms(h, w[b + "ln2.weight"])
        h = h + (F.silu(y @ w[b + "ffn_gate.weight"].T) * (y @ w[b + "ffn_up.weight"].T)) @ w[b + "ffn_down.weight"].T
    y = F.layer_norm(h, (VE,), w["mm.input_norm.weight"], w["mm.input_norm.bias"], 1e-6).reshape(-1, 4 * VE)
    y = F.gelu(y @ w["mm.0.weight"].T + w["mm.0.bias"])
    return y @ w["mm.2.weight"].T + w["mm.2.bias"]


@unittest.skipIf(torch is None, "torch fehlt")
class Lfm3Augen(unittest.TestCase):
    def test_gleich_der_referenz(self):
        import gguf_testbau as B
        from ggufengine.vision import Lfm3Vision, load_projector
        w = _v_gewichte()
        fd, pfad = tempfile.mkstemp(suffix=".gguf")
        os.close(fd)
        try:
            B.schreibe_gguf(pfad, _v_meta(), w)
            auge = load_projector(pfad, "cpu", torch.float32)
            self.assertIsInstance(auge, Lfm3Vision)
            auge.load()
            pix = torch.randn(3, 6 * VP, 4 * VP, generator=torch.Generator().manual_seed(3))   # nicht quadratisch
            with torch.no_grad():
                aus = auge.encode_pixels(pix.unsqueeze(0))
                ref = _v_referenz(w, pix)
            auge.unload()
            auge.gg.close()
        finally:
            os.unlink(pfad)
        self.assertEqual(tuple(aus.shape), (6 * 4 // 4, VAUS))
        self.assertLess(float((aus - ref).abs().max()), 1e-3 * max(1.0, float(ref.abs().max())))

    def test_bildgroesse_wie_referenz(self):
        from ggufengine.vision import Lfm3Vision
        auge = Lfm3Vision.__new__(Lfm3Vision)
        auge.patch, auge.merge, auge.min_pixels, auge.max_pixels = 16, 2, 65536, 16777216
        self.assertEqual(auge.groesse(1920, 1080, 1024), (1344, 768))       # 42 x 24 = 1008 Token, Seiten /32
        w, h = auge.groesse(100, 50, 1024)                                  # zu klein: auf min_pixels hoch
        self.assertTrue(w * h >= 65536 and w % 32 == 0 and h % 32 == 0)


class Lfm3Format(unittest.TestCase):
    SPEZIAL = {"<|startoftext|>": 1, "<|im_start|>": 2, "<|im_end|>": 3, "<think>": 4, "</think>": 5}

    def _tok(self):
        s = self.SPEZIAL
        return NS(special=s, special_id=s.__getitem__, bos_id=1, encode=lambda t: [9] * len(t))

    def test_erkennung(self):
        from ggufengine.chat import detect_format
        self.assertEqual(detect_format("<|im_start|>{{ '<think></think>' }}", "lfm3"), "lfm3")

    def test_prompt(self):
        from ggufengine.chat import ChatFormatter, Message
        f = ChatFormatter(self._tok(), "lfm3", thinking=False)
        ids = f.encode([Message("user", "ab"), Message("assistant", "<think>x</think>c")])
        # BOS [S] "user\n" a b [E] "\n" [S] "assistant\n" <think></think> c [E] "\n" [S] "assistant\n" <think></think>
        erwartet = [1, 2] + [9] * 5 + [9, 9, 3, 9] + [2] + [9] * 10 + [4, 5, 9, 3, 9] + [2] + [9] * 10 + [4, 5]
        self.assertEqual(ids, erwartet)
        denkt = ChatFormatter(self._tok(), "lfm3", thinking=True)
        self.assertEqual(denkt.encode([Message("user", "a")])[-1], 4)          # endet in "<think>"
        self.assertTrue(denkt.opens_thinking and denkt.denken_schaltbar and denkt.drops_old_thinking)

    def test_werkzeug_ergebnis_in_der_rolle_tool(self):
        """Wie die Vorlage: <|im_start|>tool, nicht user – sonst hielte das Modell das Ergebnis für den Menschen."""
        from ggufengine.chat import ChatFormatter, Message
        gesehen = []
        tok = self._tok()
        tok.encode = lambda t: gesehen.append(t) or [9] * len(t)
        f = ChatFormatter(tok, "lfm3", thinking=False)
        f.encode([Message("user", "a"), Message("user", "<tool_response>\nx\n</tool_response>", werkzeug_antwort=True)])
        self.assertIn("user\n", gesehen)
        self.assertIn("tool\n", gesehen)
        self.assertTrue(f.hintergrund_getrennt)
        self.assertFalse(ChatFormatter(self._tok(), "chatml").hintergrund_getrennt)

    def test_denkt_unter_auto_immer(self):
        """Ohne Nachdenken erfindet LFM3 Werkzeug-Ergebnisse: unter auto denkt es jede Runde, „aus“ gilt weiter."""
        sys.path.insert(0, str(ROOT / "core"))
        import denkweise
        from ggufengine.chat import ChatFormatter
        f = ChatFormatter(self._tok(), "lfm3")
        runde = NS(stufe="einfach", handlung=False)
        self.assertTrue(f.denkt_immer)
        self.assertTrue(denkweise.nativ(f, runde, "auto") and denkweise.vorgabe(f, "auto"))
        self.assertFalse(denkweise.nativ(f, runde, "aus"))
        self.assertFalse(ChatFormatter(self._tok(), "chatml").denkt_immer)

    def test_werkzeug_dialekt(self):
        import werkzeugsprache as W
        # Merkmale der LFM3-Vorlage (sie enthält auch die von Granite 4: der eigene Eintrag steht davor)
        d = W.erkennen("<tool_call>\n<function=x>\n<parameter=a>\n</parameter>\n</function>\n</tool_call>"
                       "<additional_tools> tool_to_json")
        self.assertEqual(d.name, "lfm3_xml")
        kopf = d.kopf([{"type": "function", "function": {"name": "lesen", "parameters": {}}}])
        self.assertTrue(kopf.startswith("<tools>\n{\"name\": \"lesen\""), kopf)
        self.assertEqual(d.aufrufe_finden("<tool_call>\n<function=lesen>\n<parameter=pfad>\na.txt\n</parameter>\n"
                                          "</function>\n</tool_call>")[0][2:], ("lesen", {"pfad": "a.txt"}))

    def test_kv_nur_volle_schichten(self):
        import gguflokal as G
        gg = NS(architecture="lfm3", get=lambda k, d=None: _meta().get(k, d), require=lambda k: _meta()[k])
        self.assertEqual(G._kv_bytes(gg), 2 * HKV * D * 2 * 2)               # 2 volle Schichten


class Lfm3Ausnahme(unittest.TestCase):
    """Chat-Modelle unter 4B sind gesperrt – LFM3 ist die Ausnahme (Merkmal `unter_4b` der Bauart)."""

    def _eintrag(self, arch, total, name):
        return {"id": f"LiquidAI/{name}-GGUF", "gguf": {"architecture": arch, "total": total, "context_length": 8},
                "downloads": 5, "pipeline_tag": "text-generation"}

    def test_downloader_zeigt_lfm3_unter_4b(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import hfladen as H
        bauarten = H.unterstuetzt()
        self.assertTrue(H.ausnahme_unter_4b("lfm3"))
        self.assertFalse(H.ausnahme_unter_4b("lfm2"))
        self.assertIsNotNone(H._modell(self._eintrag("lfm3", 1_700_000_000, "LFM3-1.7B"), bauarten))
        self.assertIsNone(H._modell(self._eintrag("lfm2", 1_200_000_000, "LFM2-1.2B"), bauarten))
        self.assertEqual(H.GROESSEN["klein"][:2], (0.0, 4.0))

    def test_katalog_legt_die_familie_richtig_ab(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import modellkatalog as K
        t = K.teil("lfm3")
        self.assertEqual(t.ziel().parts[-2:], ("ModelGGUF", "LFM3Familie"))
        ordner = {}
        for d in t.dateien:
            ordner.setdefault(d.unter, []).append(d.name)
        self.assertEqual(ordner["Text"], ["LFM3-1.7B-Q8_0.gguf"])                 # der Kopf: ohne Bild-Teil
        for mitglied in ("VL", "Audio", "Omni", "Entscheider/d1", "Entscheider/d1-Omni"):
            self.assertEqual(sum(n.startswith("mmproj") for n in ordner[mitglied]), 1, mitglied)
        self.assertTrue(all(d.repo.startswith("LiquidAI/") for d in t.dateien))


class Lfm3Familie(unittest.TestCase):
    def test_bildbeschreiber_nimmt_die_augen_der_familie(self):
        import bildbeschreiber as BB
        import gguflokal as G
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "Fam"
            for rel in ("Text/t.gguf", "VL/v.gguf", "VL/mmproj-v.gguf"):
                (f / rel).parent.mkdir(parents=True, exist_ok=True)
                (f / rel).write_bytes(b"x")
            with mock.patch.object(G, "ORDNER", Path(tmp)), \
                    mock.patch.object(G, "_projektor_info", lambda mm: ("lfm3vl", False)), \
                    mock.patch.object(G, "projektor_bekannt", return_value=True), \
                    mock.patch.object(G, "_config", return_value={"model": "gguf:Fam"}):
                self.assertEqual([p.name for p in BB.finden()], ["v.gguf", "mmproj-v.gguf"])
                self.assertEqual(BB.aktiv(), "Fam-VL")
                self.assertTrue(BB.zustaendig("gguf:Fam"))          # der Kopf sieht selbst nicht


if __name__ == "__main__":
    unittest.main()
