"""
Qwen3-TTS 12 Hz (GGUF arch "qwen3-tts" und "qwen3-tts-tokenizer"): Text -> Sprache, 24 kHz.

Talker: dichtes Qwen3 (GQA, q/k-RMSNorm, SwiGLU).  Er sieht nur Text, deshalb sind alle drei
M-RoPE-Abschnitte gleich – das ist gewöhnliches 1D-RoPE.  Je Schritt (1/12 s) sagt er den ersten
von 16 Codes voraus; der Code-Vorhersager (5 Schichten, eigener KV-Speicher je Schritt) ergänzt
die übrigen 15.  Eingang des nächsten Schritts: Summe der 16 Code-Einbettungen + Text-Füllung.
Klang-Decoder: RVQ (1 + 15 Codebücher) -> kausale Faltung -> Transformer (Fenster 72) ->
2x Hochtakten mit ConvNeXt -> DAC-Decoder mit SnakeBeta -> Wellenform.

Vorbild: qwen_tts (QwenLM/Qwen3-TTS, Apache-2.0) – modeling_qwen3_tts.py,
modeling_qwen3_tts_tokenizer_v2.py; Tensor-Namen wie in den qwentts.cpp-GGUFs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from ..tokenizer import Tokenizer
from .common import GPU_SPERRE, KVCache, RoPE, WeightLoader, attention, aufnahme_strom, rms_norm, swiglu_mlp

ABTASTRATE = 24000


def _zahl(gg: GGUFFile, key: str, default=None):
    v = gg.get(key, default)
    if v is None:
        raise GGUFError(f"missing key {key!r}")
    return v


class _Block:
    """Qwen3-Schicht: Aufmerksamkeit mit q/k-RMSNorm, SwiGLU."""

    def __init__(self, ld: WeightLoader, p: str, hp: dict, rope: RoPE, n_ctx: int):
        self.H, self.Hkv, self.D, self.eps = hp["heads"], hp["kv_heads"], hp["head_dim"], hp["eps"]
        self.rope = rope
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.q, self.k, self.v = (ld.linear(p + f"attn_{n}.weight") for n in "qkv")
        self.o = ld.linear(p + "attn_output.weight")
        self.q_norm, self.k_norm = ld.norm(p + "attn_q_norm.weight"), ld.norm(p + "attn_k_norm.weight")
        if self.q.n_out != self.H * self.D or self.k.n_out != self.Hkv * self.D:
            raise GGUFError(f"{p}: attention shapes do not match the head counts")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")
        self.gate, self.up, self.down = (ld.linear(p + f"ffn_{n}.weight") for n in ("gate", "up", "down"))
        self.cache = KVCache(self.Hkv, self.D, n_ctx, ld.device, ld.dtype)
        self.scale = self.D ** -0.5

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        L = h.shape[0]
        x = rms_norm(h, self.attn_norm, self.eps)
        q = self.rope(rms_norm(self.q(x).float().view(L, self.H, self.D), self.q_norm, self.eps), pos_idx)
        k = self.rope(rms_norm(self.k(x).float().view(L, self.Hkv, self.D), self.k_norm, self.eps), pos_idx)
        self.cache.write(k, self.v(x).view(L, self.Hkv, self.D), pos_idx)
        o = attention(q, *self.cache.kv(kv_len), pos_idx, kv_len, self.scale)
        h = h + self.o(o.reshape(L, -1)).float()
        return h + swiglu_mlp(rms_norm(h, self.ffn_norm, self.eps), self.gate, self.up, self.down).float()


def _hp(gg: GGUFFile, p: str) -> dict:
    return dict(
        layers=int(_zahl(gg, p + "block_count")), embd=int(_zahl(gg, p + "embedding_length")),
        heads=int(_zahl(gg, p + "attention.head_count")), kv_heads=int(_zahl(gg, p + "attention.head_count_kv")),
        head_dim=int(_zahl(gg, p + "attention.key_length")), vocab=int(_zahl(gg, p + "vocab_size")),
        eps=float(gg.get(p + "attention.layer_norm_rms_epsilon", 1e-6)),
        rope_base=float(gg.get(p + "rope.freq_base", 1_000_000.0)),
    )


@dataclass
class Abtastung:
    temperatur: float = 0.9
    top_k: int = 50
    top_p: float = 1.0


def _gumbel(rausch: torch.Tensor) -> torch.Tensor:
    """Gleichverteiltes Rauschen (0, 1) -> Gumbel-Rauschen."""
    return -torch.log(-torch.log(rausch.clamp(1e-10, 1 - 1e-7)))


def _ziehen_t(logits: torch.Tensor, a: Abtastung, rausch: Optional[torch.Tensor]) -> torch.Tensor:
    """Ein Token aus logits (..., V) als Tensor, ohne Rückfrage an die CPU (CUDA-Graph-tauglich).
    Gumbel-Max: argmax(logits/T + Gumbel) zieht genau aus softmax(logits/T); top-k vorher."""
    if a.temperatur <= 0 or rausch is None:
        return logits.argmax(-1)
    x = logits.float() / a.temperatur
    if 0 < a.top_k < x.shape[-1]:
        grenze = torch.topk(x, a.top_k, dim=-1).values[..., -1:]
        x = x.masked_fill(x < grenze, float("-inf"))
    if a.top_p < 1.0:
        werte, idx = torch.sort(x, descending=True, dim=-1)
        p = torch.softmax(werte, -1)
        weg = p.cumsum(-1) - p > a.top_p
        x = x.scatter(-1, idx, werte.masked_fill(weg, float("-inf")))
    return (x + _gumbel(rausch)).argmax(-1)


def _ziehen(logits: torch.Tensor, a: Abtastung, gen: Optional[torch.Generator]) -> int:
    """Ein Token aus logits (V,) – Temperatur, top-k, top-p wie transformers' generate."""
    rausch = None if a.temperatur <= 0 else torch.rand(logits.shape, device=logits.device, generator=gen)
    return int(_ziehen_t(logits, a, rausch))


class Qwen3TTSTalker:
    """Talker + Code-Vorhersager aus der Talker-GGUF (arch "qwen3-tts")."""

    GRAPH_BUCKET = 256

    def __init__(self, gg: GGUFFile, device: torch.device, dtype: torch.dtype, n_ctx: int = 2048,
                 progress=None, quant: bool = True, graphen: bool = True):
        if gg.architecture != "qwen3-tts":
            raise GGUFError(f"{gg.architecture} is not a qwen3-tts talker")
        a = "qwen3-tts."
        self.gruppen = int(_zahl(gg, a + "num_code_groups"))
        self.hp, self.cp = _hp(gg, a + "talker."), _hp(gg, a + "code_pred.")
        if self.gruppen != 16 or self.hp["embd"] != int(_zahl(gg, a + "talker.text_hidden_size")):
            raise GGUFError("unexpected qwen3-tts layout")
        self.ids = {n: int(_zahl(gg, a + f"codec.{n}_id")) for n in
                    ("pad", "bos", "eos", "think", "nothink", "think_bos", "think_eos")}
        self.text_ids = {n: int(_zahl(gg, a + f"text.{n}_id")) for n in
                         ("im_start", "im_end", "tts_pad", "tts_bos", "tts_eos")}
        namen, ids = gg.get(a + "codec.language_names") or [], gg.get(a + "codec.language_ids") or []
        self.sprachen = {str(n).lower(): int(i) for n, i in zip(namen, ids)}
        self.modell_typ = str(gg.get(a + "model_type", ""))
        self.n_ctx, self.device = n_ctx, device
        self.tok = Tokenizer(gg)

        ld = WeightLoader(gg, device, dtype, progress, quant=quant)
        t = "talker."
        self.text_embd, _ = ld.embedding(t + "text_embd.weight", tied_head=False)
        self.fc1 = ld.linear(t + "text_proj.fc1.weight", t + "text_proj.fc1.bias")
        self.fc2 = ld.linear(t + "text_proj.fc2.weight", t + "text_proj.fc2.bias")
        self.codec_embd, _ = ld.embedding(t + "codec_embd.weight", tied_head=False)
        self.codec_head = ld.linear(t + "codec_head.weight")
        self.out_norm = ld.norm(t + "output_norm.weight")
        rope = RoPE(self.hp["head_dim"], self.hp["rope_base"], device, n_ctx)
        self.blocks = [_Block(ld, f"{t}blk.{i}.", self.hp, rope, n_ctx) for i in range(self.hp["layers"])]
        for blk in self.blocks:                     # ganzer Speicher vorab: CUDA-Graphen zeigen auf ihn
            blk.cache.ensure(n_ctx)

        c = "code_pred."
        self.cp_embd = [ld.embedding(f"{c}codec_embd.{i}.weight", tied_head=False)[0] for i in range(self.gruppen - 1)]
        self.cp_head = [ld.linear(f"{c}lm_head.{i}.weight") for i in range(self.gruppen - 1)]
        self.cp_proj = ld.linear(c + "mtp_proj.weight", c + "mtp_proj.bias")
        self.cp_norm = ld.norm(c + "output_norm.weight")
        cp_rope = RoPE(self.cp["head_dim"], self.cp["rope_base"], device, self.gruppen + 1)
        self.cp_blocks = [_Block(ld, f"{c}blk.{i}.", self.cp, cp_rope, self.gruppen + 1)
                          for i in range(self.cp["layers"])]
        self._cp_pos = [torch.arange(0, 2, device=device)] + \
                       [torch.tensor([i + 2], device=device) for i in range(self.gruppen - 2)]

        # feste Puffer für die CUDA-Graphen
        E = self.hp["embd"]
        self._zustand = torch.zeros(1, E, device=device)
        self._code0 = torch.zeros(1, dtype=torch.long, device=device)
        self._rausch = torch.rand(self.gruppen - 1, self.cp["vocab"], device=device)
        self._ein = torch.zeros(1, E, device=device)
        self._pos = torch.zeros(1, dtype=torch.long, device=device)
        self._graphen = graphen and device.type == "cuda"
        self._g_vor = None
        self._g_talker: dict = {}
        self._pool = None
        self._g_abtastung: Optional[Abtastung] = None

    # -- Bausteine -------------------------------------------------------------------
    def _text(self, ids: List[int]) -> torch.Tensor:
        """Text-Token -> Talker-Raum (text_embd + text_projection)."""
        x = self.text_embd(torch.tensor(ids, device=self.device, dtype=torch.long))
        return self.fc2(F.silu(self.fc1(x).float())).float()

    def _codec(self, ids: List[int]) -> torch.Tensor:
        return self.codec_embd(torch.tensor(ids, device=self.device, dtype=torch.long)).float()

    def _talker_t(self, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> tuple[torch.Tensor, torch.Tensor]:
        for blk in self.blocks:
            h = blk(h, pos_idx, kv_len)
        last = rms_norm(h[-1:], self.out_norm, self.hp["eps"])
        return last, self.codec_head(last)[0].float()

    def _talker(self, h: torch.Tensor, pos: int) -> tuple[torch.Tensor, torch.Tensor]:
        """Eingänge (L, E) ab Position pos -> (letzter Zustand nach Norm (1, E), logits (V,))."""
        L = h.shape[0]
        if pos + L > self.n_ctx:
            raise RuntimeError(f"Text zu lang für {self.n_ctx} Schritte")
        if L == 1 and self._graphen:
            return self._talker_graph(h, pos)
        return self._talker_t(h, torch.arange(pos, pos + L, device=self.device, dtype=torch.long), pos + L)

    def _vorhersager_t(self, zustand: torch.Tensor, code0: torch.Tensor, rausch: torch.Tensor,
                       a: Abtastung) -> tuple[torch.Tensor, torch.Tensor]:
        """Codes 1..15 eines Schritts (15,) und die Summe aller 16 Code-Einbettungen (1, E).
        Nur Tensor-Operationen mit festen Positionen – so passt er in einen CUDA-Graphen."""
        e0 = self.codec_embd(code0).float()
        summe = e0
        h = self.cp_proj(torch.cat([zustand, e0], 0)).float()
        codes = []
        for i in range(self.gruppen - 1):
            pos_idx = self._cp_pos[i]
            kv_len = 2 if i == 0 else i + 2
            for blk in self.cp_blocks:
                h = blk(h, pos_idx, kv_len)
            logits = self.cp_head[i](rms_norm(h[-1:], self.cp_norm, self.cp["eps"])).float()   # (1, V)
            code = _ziehen_t(logits, a, rausch[i:i + 1])                                      # (1,)
            codes.append(code)
            e = self.cp_embd[i](code).float()
            summe = summe + e
            if i < self.gruppen - 2:
                h = self.cp_proj(e).float()
        return torch.cat(codes), summe

    # -- CUDA-Graphen ------------------------------------------------------------------
    def _aufnehmen(self, f):
        """f() einmal aufwärmen und als CUDA-Graph aufnehmen; gibt (Graph, Ausgabe) zurück."""
        with GPU_SPERRE:
            s = aufnahme_strom(self.device)          # ein Strom für alle Aufnahmen (cuBLAS-Arbeitsspeicher)
            s.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(s):
                for _ in range(2):
                    f()
            torch.cuda.current_stream().wait_stream(s)
            g = torch.cuda.CUDAGraph()
            with torch.cuda.graph(g, pool=self._pool, stream=s, capture_error_mode="thread_local"):
                out = f()
            if self._pool is None:
                self._pool = g.pool()
        return g, out

    def _vorhersager_graph(self, zustand: torch.Tensor, code0: int, a: Abtastung,
                           gen: Optional[torch.Generator]) -> tuple[torch.Tensor, torch.Tensor]:
        self._zustand.copy_(zustand)                  # Eingaben vor dem Aufnehmen: das Aufwärmen
        self._code0.fill_(code0)                      # rechnet schon diesen Schritt
        torch.rand(self._rausch.shape, device=self.device, generator=gen, out=self._rausch)
        if self._g_vor is None or self._g_abtastung != a:
            self._g_vor = self._aufnehmen(lambda: self._vorhersager_t(self._zustand, self._code0, self._rausch, a))
            self._g_abtastung = a
        g, out = self._g_vor
        g.replay()
        return out

    def _talker_graph(self, h: torch.Tensor, pos: int) -> tuple[torch.Tensor, torch.Tensor]:
        kv_len = min(-(-(pos + 1) // self.GRAPH_BUCKET) * self.GRAPH_BUCKET, self.n_ctx)
        self._ein.copy_(h)                            # vor dem Aufnehmen: das Aufwärmen schreibt den
        self._pos.fill_(pos)                          # KV-Eintrag dieses Schritts, nicht Position 0
        if kv_len not in self._g_talker:
            self._g_talker[kv_len] = self._aufnehmen(lambda: self._talker_t(self._ein, self._pos, kv_len))
        g, out = self._g_talker[kv_len]
        g.replay()
        return out

    def _vorhersager(self, zustand: torch.Tensor, code0: int, a: Abtastung,
                     gen: Optional[torch.Generator]) -> tuple[torch.Tensor, torch.Tensor]:
        if self._graphen:
            return self._vorhersager_graph(zustand, code0, a, gen)
        rausch = torch.rand(self._rausch.shape, device=self.device, generator=gen)
        return self._vorhersager_t(zustand, torch.tensor([code0], device=self.device), rausch, a)

    # -- Prompt ----------------------------------------------------------------------
    def _rolle(self) -> List[int]:
        return [self.text_ids["im_start"]] + self.tok.encode("assistant\n")

    def eingabe(self, text: str, beschreibung: str = "", sprache: str = "german",
                streaming: bool = False) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """(Prefill-Eingänge (L, E), Text-Füllung je Schritt (S, E), tts_pad (1, E)) wie
        Qwen3TTSForConditionalGeneration.generate; nach der Füllung kommt tts_pad."""
        t = self.text_ids
        teile = []
        if beschreibung.strip():                             # <|im_start|>user\n{beschreibung}<|im_end|>\n
            ids = [t["im_start"]] + self.tok.encode(f"user\n{beschreibung.strip()}") + [t["im_end"]] \
                + self.tok.encode("\n")
            teile.append(self._text(ids))
        text_ids = self.tok.encode(text.strip())
        if not text_ids:
            raise ValueError("kein Text zum Sprechen")
        bos, eos, pad = self._text([t["tts_bos"], t["tts_eos"], t["tts_pad"]]).split(1)
        sprache = (sprache or "auto").lower()
        c = self.ids
        if sprache == "auto":
            vorlauf = [c["nothink"], c["think_bos"], c["think_eos"]]
        elif sprache in self.sprachen:
            vorlauf = [c["think"], c["think_bos"], self.sprachen[sprache], c["think_eos"]]
        else:
            raise ValueError(f"Sprache {sprache!r} kennt das Modell nicht")
        codec = self._codec(vorlauf + [c["pad"], c["bos"]])
        teile.append(self._text(self._rolle()))
        teile.append(torch.cat([pad.expand(codec.shape[0] - 2, -1), bos]) + codec[:-1])
        if streaming:
            teile.append(self._text(text_ids[:1]) + codec[-1:])
            fuellung = torch.cat([self._text(text_ids[1:]) if len(text_ids) > 1 else pad[:0], eos])
        else:                                                 # ganzer Text im Prefill (Vorgabe für VoiceDesign)
            teile.append(torch.cat([self._text(text_ids), eos]) + self._codec([c["pad"]] * (len(text_ids) + 1)))
            teile.append(pad + self._codec([c["bos"]]))
            fuellung = pad
        return torch.cat(teile), fuellung, pad

    # -- Erzeugen --------------------------------------------------------------------
    @torch.inference_mode()
    def codes(self, text: str, beschreibung: str = "", sprache: str = "german", *, streaming: bool = False,
              abtastung: Abtastung = Abtastung(), vorhersager: Abtastung = Abtastung(),
              wiederholung: float = 1.05, max_schritte: int = 2048, seed: Optional[int] = None,
              abbruch: Optional[Callable[[], bool]] = None,
              je_schritt: Optional[Callable[[torch.Tensor], None]] = None) -> torch.Tensor:
        """Codes (T, 16) für den Text.  je_schritt bekommt jeden fertigen Schritt (16,) – Streaming."""
        gen = None
        if seed is not None:
            gen = torch.Generator(device=self.device)
            gen.manual_seed(int(seed))
        start, fuellung, pad = self.eingabe(text, beschreibung, sprache, streaming)
        zustand, logits = self._talker(start, 0)
        pos = start.shape[0]
        max_schritte = min(max_schritte, self.n_ctx - pos)
        eos, gesperrt = self.ids["eos"], torch.ones(self.hp["vocab"], dtype=torch.bool, device=self.device)
        gesperrt[:self.hp["vocab"] - 1024] = False            # Steuer-Codes nie ziehen, nur eos
        gesperrt[eos] = False
        gesehen = torch.zeros(self.hp["vocab"], dtype=torch.bool, device=self.device)
        schritte: List[torch.Tensor] = []
        for schritt in range(max_schritte):
            x = logits.masked_fill(gesperrt, float("-inf"))
            if schritt < 2:
                x[eos] = float("-inf")                       # min_new_tokens=2
            if wiederholung != 1.0:                          # repetition_penalty über die bisherigen Code-0
                x = torch.where(gesehen, torch.where(x > 0, x / wiederholung, x * wiederholung), x)
            code0 = _ziehen(x, abtastung, gen)
            if code0 == eos or (abbruch and abbruch()):
                break
            gesehen[code0] = True
            rest, summe = self._vorhersager(zustand, code0, vorhersager, gen)
            zeile = torch.cat([torch.tensor([code0], device=self.device), rest])
            schritte.append(zeile.clone())
            if je_schritt:
                je_schritt(schritte[-1])
            fuell = fuellung[schritt:schritt + 1] if schritt < fuellung.shape[0] else pad
            zustand, logits = self._talker(summe + fuell, pos)
            pos += 1
        if not schritte:
            return torch.zeros(0, self.gruppen, dtype=torch.long)
        return torch.stack(schritte).cpu()
# ---------------------------------------------------------------------------
# Klang-Decoder
# ---------------------------------------------------------------------------

def _kausal(x: torch.Tensor, w: torch.Tensor, b: Optional[torch.Tensor], dilation: int = 1,
            groups: int = 1) -> torch.Tensor:
    """Kausale Faltung (B, C, T), Schrittweite 1: links mit (k-1)*dilation Nullen auffüllen."""
    if w.dim() == 2:                                   # Ausgang mit einem Kanal: GGUF lässt die 1 weg
        w = w.unsqueeze(0)
    k = (w.shape[-1] - 1) * dilation + 1
    return F.conv1d(F.pad(x, (k - 1, 0)), w, b.reshape(-1) if b is not None else None,
                    dilation=dilation, groups=groups)


def _kausal_t(x: torch.Tensor, w: torch.Tensor, b: Optional[torch.Tensor], stride: int) -> torch.Tensor:
    """Transponierte Faltung; rechts k - stride Werte abschneiden (CausalTransConvNet)."""
    y = F.conv_transpose1d(x, w, b, stride=stride)
    rest = w.shape[-1] - stride
    return y[..., :y.shape[-1] - rest] if rest > 0 else y


def _snake(x: torch.Tensor, alpha: torch.Tensor, beta: torch.Tensor) -> torch.Tensor:
    a, b = alpha.exp().view(1, -1, 1), beta.exp().view(1, -1, 1)
    return x + (1.0 / (b + 1e-9)) * torch.sin(x * a).pow(2)


class Qwen3TTSCodec:
    """Klang-Decoder aus der Tokenizer-GGUF (arch "qwen3-tts-tokenizer"); rechnet in float32."""

    def __init__(self, gg: GGUFFile, device: torch.device, progress=None):
        if gg.architecture != "qwen3-tts-tokenizer":
            raise GGUFError(f"{gg.architecture} is not a qwen3-tts tokenizer")
        a = "qwen3-tts-tokenizer.decoder."
        self.n_q = int(_zahl(gg, a + "num_quantizers"))
        self.raten = [int(r) for r in _zahl(gg, a + "upsample_rates")]
        self.hoch = [int(r) for r in _zahl(gg, a + "upsampling_ratios")]
        self.fenster = int(gg.get(a + "sliding_window", 72))
        self.heads, self.head_dim = int(_zahl(gg, a + "num_attention_heads")), int(_zahl(gg, a + "head_dim"))
        self.eps = float(gg.get(a + "rms_norm_eps", 1e-5))
        self.schichten = int(_zahl(gg, a + "num_hidden_layers"))
        self.takt = 1
        for r in self.raten + self.hoch:
            self.takt *= r
        self.device = device
        ld = WeightLoader(gg, device, torch.float32, progress, quant=False)
        g = lambda n: ld.get("tok_dec." + n, torch.float32)      # noqa: E731
        self.w = {n[len("tok_dec."):]: None for n in gg.tensors if n.startswith("tok_dec.")}
        for n in list(self.w):
            self.w[n] = g(n)
        if len([n for n in self.w if n.startswith("vq_rest.") and n.endswith(".codebook")]) != self.n_q - 1:
            raise GGUFError("codebook count does not match num_quantizers")
        self.rope = RoPE(self.head_dim, float(gg.get(a + "rope_theta", 10000.0)), device, 8192)

    def _transformer(self, x: torch.Tensor) -> torch.Tensor:
        """(T, latent) -> (T, latent): input_proj, 8 Schichten mit Layer-Scale, Fenster 72, output_proj."""
        w = self.w
        h = F.linear(x, w["pre_tfm.input_proj.weight"], w["pre_tfm.input_proj.bias"])
        T = h.shape[0]
        pos = torch.arange(T, device=self.device)
        for i in range(self.schichten):
            p = f"pre_tfm.blk.{i}."
            y = rms_norm(h, w[p + "attn_norm.weight"], self.eps)
            q = self.rope(F.linear(y, w[p + "attn_q.weight"]).view(T, self.heads, self.head_dim), pos)
            k = self.rope(F.linear(y, w[p + "attn_k.weight"]).view(T, self.heads, self.head_dim), pos)
            v = F.linear(y, w[p + "attn_v.weight"]).view(T, self.heads, self.head_dim)
            o = attention(q, k.transpose(0, 1), v.transpose(0, 1), pos, T, self.head_dim ** -0.5,
                          window=self.fenster)
            h = h + w[p + "attn_scale"] * F.linear(o.reshape(T, -1), w[p + "attn_output.weight"])
            y = rms_norm(h, w[p + "ffn_norm.weight"], self.eps)
            m = F.linear(F.silu(F.linear(y, w[p + "ffn_gate.weight"])) * F.linear(y, w[p + "ffn_up.weight"]),
                         w[p + "ffn_down.weight"])
            h = h + w[p + "ffn_scale"] * m
        h = rms_norm(h, w["pre_tfm.norm.weight"], self.eps)
        return F.linear(h, w["pre_tfm.output_proj.weight"], w["pre_tfm.output_proj.bias"])

    @torch.inference_mode()
    def _decode(self, codes: torch.Tensor) -> torch.Tensor:
        """Codes (T, 16) -> Wellenform (T * takt,)."""
        w = self.w
        c = codes.to(self.device).long()
        erst = F.embedding(c[:, 0], w["vq_first.0.codebook"])                        # (T, 256)
        rest = sum(F.embedding(c[:, i + 1], w[f"vq_rest.{i}.codebook"]) for i in range(self.n_q - 1))
        h = F.conv1d(erst.T.unsqueeze(0), w["vq_first.output_proj.weight"]) \
            + F.conv1d(rest.T.unsqueeze(0), w["vq_rest.output_proj.weight"])            # (1, 512, T)
        h = _kausal(h, w["pre_conv.weight"], w["pre_conv.bias"])                       # (1, 1024, T)
        h = self._transformer(h[0].T).T.unsqueeze(0)
        for i in range(len(self.hoch)):
            p = f"upsample.{i}."
            h = _kausal_t(h, w[p + "conv.weight"], w[p + "conv.bias"], self.hoch[i])
            y = _kausal(h, w[p + "dwconv.weight"], w[p + "dwconv.bias"], groups=h.shape[1])
            y = F.layer_norm(y.transpose(1, 2), (h.shape[1],), w[p + "norm.weight"], w[p + "norm.bias"], 1e-6)
            y = F.linear(F.gelu(F.linear(y, w[p + "pwconv1.weight"], w[p + "pwconv1.bias"])),
                         w[p + "pwconv2.weight"], w[p + "pwconv2.bias"])
            h = h + (w[p + "gamma"] * y).transpose(1, 2)
        h = _kausal(h, w["dec.0.conv.weight"], w["dec.0.conv.bias"])
        for b, rate in enumerate(self.raten, start=1):
            p = f"dec.{b}."
            h = _snake(h, w[p + "snake.alpha"], w[p + "snake.beta"])
            h = _kausal_t(h, w[p + "conv_t.weight"], w[p + "conv_t.bias"], rate)
            for r, dil in enumerate((1, 3, 9)):
                q = f"{p}res.{r}."
                y = _snake(h, w[q + "act1.alpha"], w[q + "act1.beta"])
                y = _kausal(y, w[q + "conv1.weight"], w[q + "conv1.bias"], dilation=dil)
                y = _snake(y, w[q + "act2.alpha"], w[q + "act2.beta"])
                h = h + _kausal(y, w[q + "conv2.weight"], w[q + "conv2.bias"])
        n = len(self.raten) + 1
        h = _snake(h, w[f"dec.{n}.snake.alpha"], w[f"dec.{n}.snake.beta"])
        h = _kausal(h, w[f"dec.{n + 1}.conv.weight"], w[f"dec.{n + 1}.conv.bias"])
        return h.clamp(-1, 1)[0, 0]

    def decode(self, codes: torch.Tensor, stueck: int = 300, kontext: int = 25) -> torch.Tensor:
        """Wie chunked_decode: lange Folgen in Stücken mit linkem Kontext (gleiches Ergebnis, wenig Speicher)."""
        teile, start = [], 0
        while start < codes.shape[0]:
            ende = min(start + stueck, codes.shape[0])
            k = kontext if start - kontext > 0 else start
            wav = self._decode(codes[start - k:ende])
            teile.append(wav[k * self.takt:])
            start = ende
        return torch.cat(teile) if teile else torch.zeros(0, device=self.device)
