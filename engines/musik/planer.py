"""planer.py – der Song-Planer von ACE-Step 1.5: ein Qwen3-Sprachmodell (1.7B) schreibt zu Stil, Liedtext
und Metadaten Audio-Codes (5 je Sekunde); der FSQ-Detokenizer aus der DiT-GGUF macht daraus den Kontext
(25 Hz Latents), mit dem der Komponist statt aus Stille arbeitet.

Ablauf wie acestep.cpp (prompt.h, pipeline-lm.cpp Phase 2, fsq-detok.h): Metadaten als CoT vorgegeben,
erlaubt sind nur Audio-Codes und <|im_end|>, CFG gegen einen leeren Prompt, Temperatur → top-p.
"""
from __future__ import annotations

import torch
import torch.nn.functional as F

from .netze import Qwen3Schicht, rms, rope_tabelle

IM_START, IM_END = 151644, 151645
THINK, THINK_END = 151667, 151668
CODE_BASIS, CODE_ZAHL = 151669, 65535
ANWEISUNG = "Generate audio semantic tokens based on the given conditions:"
CODES_JE_SEKUNDE = 5
TEMPERATUR, TOP_P, CFG = 0.85, 0.9, 2.0
FSQ_STUFEN = (8, 8, 8, 5, 5, 5)


def _yaml_umbruch(schluessel: str, wert: str) -> str:
    """Wie yaml.dump: Zeilenumbruch mit zwei Leerzeichen, sobald die Spalte 80 überschreitet."""
    aus, spalte = schluessel + ":", len(schluessel) + 1
    for wort in wert.split(" "):
        if spalte > 80:
            aus += "\n  "
            spalte = 2
        else:
            aus += " "
            spalte += 1
        aus += wort
        spalte += len(wort)
    return aus + "\n"


def cot(stil: str, sekunden: int, bpm=None, tonart=None, sprache=None, takt=None) -> str:
    """CoT-Block (Schlüssel alphabetisch wie yaml.dump(sort_keys=True))."""
    y = f"bpm: {int(bpm)}\n" if bpm else ""
    y += _yaml_umbruch("caption", stil) if stil else ""
    y += f"duration: {int(sekunden)}\n"
    y += f"keyscale: {tonart}\n" if tonart else ""
    y += f"language: {sprache}\n" if sprache else ""
    y += f"timesignature: {takt}\n" if takt else ""
    return y


def prompt_ids(kodieren, stil: str, text: str, cot_yaml: str) -> tuple[list[int], list[int]]:
    """(bedingt, unbedingt). `kodieren(text)` liefert BPE-IDs ohne Sondertoken."""
    def kopf():
        return [IM_START] + kodieren(f"system\n# Instruction\n{ANWEISUNG}\n\n") + [IM_END] + kodieren("\n")
    bed = (kopf() + [IM_START] + kodieren(f"user\n# Caption\n{stil}\n\n# Lyric\n{text}\n") + [IM_END]
           + kodieren("\n") + [IM_START] + kodieren("assistant\n") + [THINK] + kodieren("\n" + cot_yaml)
           + [THINK_END] + kodieren("\n\n"))
    unbed = (kopf() + [IM_START] + kodieren("user\n") + [IM_END] + kodieren("\n") + [IM_START]
             + kodieren("assistant\n") + [THINK] + kodieren("\n\n") + [THINK_END] + kodieren("\n\n"))
    return bed, unbed


def fsq_werte(codes: torch.Tensor) -> torch.Tensor:
    """Code-Index -> 6 Werte in [-1, 1] (Ziffern zu den Stufen 8, 8, 8, 5, 5, 5)."""
    aus, schritt = [], 1
    for stufen in FSQ_STUFEN:
        aus.append(((codes // schritt) % stufen).float() / ((stufen - 1) / 2) - 1.0)
        schritt *= stufen
    return torch.stack(aus, -1)


def fsq_index(roh: torch.Tensor) -> torch.Tensor:
    """6 Rohwerte (vor tanh) -> Code-Index; Umkehrung von fsq_werte."""
    index, schritt = torch.zeros(roh.shape[:-1], dtype=torch.long, device=roh.device), 1
    for d, stufen in enumerate(FSQ_STUFEN):
        ziffer = torch.floor((stufen - 1) * (torch.tanh(roh[..., d].float()) + 1) / 2 + 0.5)
        index += ziffer.clamp(0, stufen - 1).long() * schritt
        schritt *= stufen
    return index


def _rope_je_zeile(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """x: [B, S, H, D]; cos/sin: [B, S, D] (eigene Positionen je Zeile)."""
    h = x.shape[-1] // 2
    gedreht = torch.cat([-x[..., h:], x[..., :h]], -1)
    return x * cos[:, :, None] + gedreht * sin[:, :, None]


def top_p_ziehen(logits: torch.Tensor, temperatur: float, top_p: float, gen: torch.Generator) -> int:
    """Temperatur -> top-p -> ziehen, alles auf dem Gerät der Logits (`gen` auf demselben Gerät)."""
    p = torch.softmax(logits.float() / temperatur, -1)
    werte, idx = torch.sort(p, descending=True)
    summe = torch.cumsum(werte, -1)
    werte = werte.masked_fill((summe - werte) >= top_p, 0.0)          # erstes Token über der Schwelle bleibt
    wahl = torch.multinomial(werte, 1, generator=gen)
    return int(idx[wahl].item())


class Planer:
    """Qwen3-LM (acestep-5Hz-lm) mit KV-Cache; Kopf nur für <|im_end|> und die Audio-Codes."""

    def __init__(self, datei, geraet, dtype=torch.bfloat16):
        laden = lambda n: datei.tensor(n, geraet, dtype)          # noqa: E731
        embed = laden("model.embed_tokens.weight")
        n = int(datei.meta("acestep-lm.block_count", 28))
        self.schichten = [Qwen3Schicht(laden, f"model.layers.{i}") for i in range(n)]
        self.norm = laden("model.norm.weight")
        # Ausgabe = Einbettung (tied); nur die erlaubten Zeilen: Index 0 = <|im_end|>, 1 + c = Code c
        self.kopf = torch.cat([embed[IM_END:IM_END + 1], embed[CODE_BASIS:CODE_BASIS + CODE_ZAHL]]).contiguous()
        self.embed = embed
        self.geraet, self.dtype = geraet, dtype

    def _vorwaerts(self, ids, cache, start, polster, maske):
        """ids [B, S] ab Cache-Stelle `start`; polster [B] = linkes Auffüllen je Zeile."""
        B, S = ids.shape
        x = self.embed[ids]
        pos = (torch.arange(start, start + S, device=self.geraet)[None] - polster[:, None]).clamp_min(0)
        cos_t, sin_t = self._rope
        cos, sin = cos_t[pos], sin_t[pos]
        for s, (k_c, v_c) in zip(self.schichten, cache):
            h = rms(x, s.ln1)
            q = _rope_je_zeile(rms((h @ s.wq.T).view(B, S, s.koepfe, s.dim), s.qn), cos, sin)
            k = _rope_je_zeile(rms((h @ s.wk.T).view(B, S, s.kv, s.dim), s.kn), cos, sin)
            v = (h @ s.wv.T).view(B, S, s.kv, s.dim)
            k_c[:, :, start:start + S] = k.transpose(1, 2)
            v_c[:, :, start:start + S] = v.transpose(1, 2)
            a = F.scaled_dot_product_attention(q.transpose(1, 2), k_c[:, :, :start + S], v_c[:, :, :start + S],
                                               attn_mask=maske, enable_gqa=True)
            x = x + a.transpose(1, 2).reshape(B, S, -1) @ s.wo.T
            h = rms(x, s.ln2)
            x = x + (F.silu(h @ s.gate.T) * (h @ s.up.T)) @ s.down.T
        return rms(x[:, -1], self.norm) @ self.kopf.T          # [B, 1 + CODE_ZAHL]

    def _schritt(self, cache):
        """Ein Token je Zeile mit festen Formen (Graph-tauglich): Eingaben in self._tok/_pos/_stelle/_maske,
        Cache in voller Länge, ungeschriebene Stellen ausmaskiert."""
        B = self._tok.shape[0]
        x = self.embed[self._tok]
        cos_t, sin_t = self._rope
        cos, sin = cos_t[self._pos], sin_t[self._pos]
        for s, (k_c, v_c) in zip(self.schichten, cache):
            h = rms(x, s.ln1)
            q = _rope_je_zeile(rms((h @ s.wq.T).view(B, 1, s.koepfe, s.dim), s.qn), cos, sin)
            k = _rope_je_zeile(rms((h @ s.wk.T).view(B, 1, s.kv, s.dim), s.kn), cos, sin)
            v = (h @ s.wv.T).view(B, 1, s.kv, s.dim)
            k_c.index_copy_(2, self._stelle, k.transpose(1, 2))
            v_c.index_copy_(2, self._stelle, v.transpose(1, 2))
            a = F.scaled_dot_product_attention(q.transpose(1, 2), k_c, v_c, attn_mask=self._maske,
                                               enable_gqa=True)
            x = x + a.transpose(1, 2).reshape(B, 1, -1) @ s.wo.T
            h = rms(x, s.ln2)
            x = x + (F.silu(h @ s.gate.T) * (h @ s.up.T)) @ s.down.T
        self._logits.copy_(rms(x[:, -1], self.norm) @ self.kopf.T)

    def _graph(self, cache):
        """Schritt als CUDA-Graph aufnehmen (ein Start statt tausender) – sonst None."""
        if not str(self.geraet).startswith("cuda"):
            return None
        try:
            strom = torch.cuda.Stream()
            strom.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(strom):
                self._schritt(cache)                       # Aufwärmen (cuBLAS-Arbeitsspeicher)
            torch.cuda.current_stream().wait_stream(strom)
            g = torch.cuda.CUDAGraph()
            with torch.cuda.graph(g, stream=strom, capture_error_mode="thread_local"):
                self._schritt(cache)
            return g
        except Exception:
            return None

    @torch.no_grad()
    def codes(self, bed: list[int], unbed: list[int], max_codes: int, seed: int, cfg: float = CFG,
              temperatur: float = TEMPERATUR, top_p: float = TOP_P, melden=None, abbruch=None) -> list[int]:
        zeilen = [bed, unbed] if cfg > 1.0 else [bed]
        L = max(len(z) for z in zeilen)
        gesamt = L + max_codes + 1
        g_ = self.geraet
        polster = torch.tensor([L - len(z) for z in zeilen], device=g_)
        ids = torch.tensor([[0] * (L - len(z)) + z for z in zeilen], device=g_)
        B = len(zeilen)
        s0 = self.schichten[0]
        # Nullen statt leer: ausmaskierte Stellen gehen mit Gewicht 0 ein, NaN aus altem Speicher nicht
        cache = [(torch.zeros(B, s0.kv, gesamt, s0.dim, device=g_, dtype=self.dtype),
                  torch.zeros(B, s0.kv, gesamt, s0.dim, device=g_, dtype=self.dtype))
                 for _ in self.schichten]
        self._rope = rope_tabelle(gesamt, s0.dim, g_, self.dtype)
        k_idx = torch.arange(gesamt, device=g_)
        gueltig = k_idx[None] >= polster[:, None]                                   # [B, gesamt]
        q_idx = torch.arange(L, device=g_)
        maske = (gueltig[:, None, None, :L] & (k_idx[None, None, :L] <= q_idx[None, :, None])[:, None])
        # Auffüllstellen sehen sich selbst – ganz leere Zeilen gäben NaN, das über den Cache weiterliefe
        maske = maske | torch.eye(L, dtype=torch.bool, device=g_)[None, None]
        logits = self._vorwaerts(ids, cache, 0, polster, maske)

        self._tok = torch.zeros(B, 1, dtype=torch.long, device=g_)
        self._pos = torch.zeros(B, 1, dtype=torch.long, device=g_)
        self._stelle = torch.zeros(1, dtype=torch.long, device=g_)
        self._maske = (gueltig & (k_idx[None] < L))[:, None, None].clone()          # [B, 1, 1, gesamt]
        self._logits = torch.empty_like(logits)
        graph = None
        gen = torch.Generator(device=g_).manual_seed(int(seed))
        aus: list[int] = []
        for schritt in range(max_codes + 1):
            lg = logits[0] if B == 1 else logits[1] + cfg * (logits[0] - logits[1])
            wahl = top_p_ziehen(lg, temperatur, top_p, gen)
            if wahl == 0:                                                           # <|im_end|>
                break
            aus.append(wahl - 1)
            if len(aus) >= max_codes:
                break
            if abbruch and abbruch():
                raise RuntimeError("Musik abgebrochen.")
            if melden and len(aus) % 25 == 0:
                melden(f"Musik: Planer {len(aus)}/{max_codes}")
            stelle = L + schritt
            self._tok.fill_(CODE_BASIS + wahl - 1)
            self._stelle.fill_(stelle)
            self._pos.copy_((stelle - polster)[:, None])
            self._maske[..., stelle] = True
            if graph is None and schritt == 0:
                graph = self._graph(cache)       # nimmt diesen Schritt mit auf; ohne Graph rechnet er direkt
                if graph is None:
                    self._schritt(cache)
            if graph is not None:
                graph.replay()
            elif schritt > 0:
                self._schritt(cache)
            logits = self._logits
        self._tok = self._pos = self._stelle = self._maske = self._logits = None
        return aus


class Detokenizer:
    """Audio-Codes (5 Hz) -> Kontext-Latents (25 Hz, 64 Kanäle). Gewichte aus der DiT-GGUF."""

    def __init__(self, datei, geraet, dtype=torch.bfloat16):
        laden = lambda n: datei.tensor(n, geraet, dtype)          # noqa: E731
        self.fsq_w = laden("tokenizer.quantizer.project_out.weight")
        self.fsq_b = laden("tokenizer.quantizer.project_out.bias")
        self.emb_w, self.emb_b = laden("detokenizer.embed_tokens.weight"), laden("detokenizer.embed_tokens.bias")
        self.spezial = laden("detokenizer.special_tokens")                          # [5, 2048]
        self.schichten = [Qwen3Schicht(laden, f"detokenizer.layers.{i}") for i in range(2)]
        self.norm = laden("detokenizer.norm.weight")
        self.aus_w, self.aus_b = laden("detokenizer.proj_out.weight"), laden("detokenizer.proj_out.bias")
        self.geraet, self.dtype = geraet, dtype

    @torch.no_grad()
    def __call__(self, codes: list[int], stueck: int = 512) -> torch.Tensor:
        """-> [len(codes) * 5, 64] float32."""
        c = torch.tensor(codes, device=self.geraet)
        q = fsq_werte(c).to(self.dtype) @ self.fsq_w.T + self.fsq_b
        e = q @ self.emb_w.T + self.emb_b                                           # [T5, 2048]
        cos, sin = rope_tabelle(5, 128, self.geraet, self.dtype)
        teile = []
        for a in range(0, len(codes), stueck):
            h = e[a:a + stueck, None] + self.spezial[None]                          # [n, 5, 2048]
            for s in self.schichten:
                h = s(h, cos, sin)
            h = rms(h, self.norm) @ self.aus_w.T + self.aus_b                       # [n, 5, 64]
            teile.append(h.reshape(-1, 64).float())
        return torch.cat(teile)


class Tokenizer:
    """Latents (25 Hz) -> Audio-Codes (5 Hz): je 5 Bilder ein CLS-Token durch 2 Qwen3-Schichten, dann FSQ.
    Gewichte aus der DiT-GGUF (acestep.cpp fsq-tok.h)."""

    def __init__(self, datei, geraet, dtype=torch.bfloat16):
        laden = lambda n: datei.tensor(n, geraet, dtype)          # noqa: E731
        self.proj_w, self.proj_b = laden("tokenizer.audio_acoustic_proj.weight"), laden("tokenizer.audio_acoustic_proj.bias")
        p = "tokenizer.attention_pooler"
        self.emb_w, self.emb_b = laden(f"{p}.embed_tokens.weight"), laden(f"{p}.embed_tokens.bias")
        self.cls = laden(f"{p}.special_token")                                      # [2048]
        self.schichten = [Qwen3Schicht(laden, f"{p}.layers.{i}") for i in range(2)]
        self.norm = laden(f"{p}.norm.weight")
        self.fsq_w = laden("tokenizer.quantizer.project_in.weight")
        self.fsq_b = laden("tokenizer.quantizer.project_in.bias")
        self.geraet, self.dtype = geraet, dtype

    @torch.no_grad()
    def __call__(self, latents: torch.Tensor, stille: torch.Tensor, stueck: int = 512) -> list[int]:
        """latents [T, 64]; fehlende Bilder bis zum Vielfachen von 5 kommen aus `stille`."""
        rest = (-latents.shape[0]) % 5
        x = torch.cat([latents, stille[:rest].to(latents)]).to(self.geraet, self.dtype).view(-1, 5, 64)
        cos, sin = rope_tabelle(6, 128, self.geraet, self.dtype)
        codes = []
        for a in range(0, x.shape[0], stueck):
            e = (x[a:a + stueck] @ self.proj_w.T + self.proj_b) @ self.emb_w.T + self.emb_b     # [n, 5, 2048]
            h = torch.cat([self.cls.view(1, 1, -1).expand(e.shape[0], 1, -1), e], 1)          # [n, 6, 2048]
            for s in self.schichten:
                h = s(h, cos, sin)
            roh = rms(h[:, 0], self.norm).float() @ self.fsq_w.float().T + self.fsq_b.float()  # [n, 6]
            codes += fsq_index(roh).tolist()
        return codes
