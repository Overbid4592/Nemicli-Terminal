"""Token sampling: temperature, top-k, top-p, min-p, repetition / presence penalty,
DRY ("don't repeat yourself") against phrases the model copies from its own earlier answers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import torch


@dataclass
class SamplerConfig:
    temperature: float = 0.7
    top_k: int = 20
    top_p: float = 0.8
    min_p: float = 0.0
    repeat_penalty: float = 1.0
    presence_penalty: float = 0.0
    repeat_last_n: int = 64
    # DRY: if the tokens just generated repeat an earlier stretch of the model's own
    # output, the token that followed there is penalised by
    # multiplier * base ** (match_length - allowed_length); 0 = off
    dry_multiplier: float = 0.0
    dry_base: float = 1.75
    dry_allowed_length: int = 2
    dry_last_n: int = 8192
    seed: Optional[int] = None


def dry_penalties(history, own, breaker, cfg: SamplerConfig, max_match: int = 64, word_start=None) -> dict:
    """{token: penalty} for the next token.

    history: token ids (context so far); own: per position, True if the model generated
    it; breaker: bool array over the vocabulary – such tokens end a match (newlines,
    ':', '"', '*', special tokens).  Only stretches of the model's own output count:
    copying from the prompt, the user or a tool result is never penalised.
    word_start: bool array over the vocabulary; if given, only tokens that begin a word
    are penalised – a penalty inside a word only makes the model misspell it."""
    import numpy as np
    n = len(history)
    if n < 2 or cfg.dry_multiplier <= 0:
        return {}
    lo = max(0, n - cfg.dry_last_n)
    h = np.asarray(history[lo:], dtype=np.int64)
    o = np.asarray(own[lo:], dtype=bool)
    m = len(h)
    brk = breaker[h]
    if not o[-1] or brk[-1]:
        return {}
    # earlier positions j whose predecessor equals the last token; h[j] followed there
    j = np.nonzero(h[:-1] == h[-1])[0] + 1
    j = j[o[j - 1] & o[j] & ~brk[j - 1]]
    if j.size == 0:
        return {}
    laenge = np.ones(j.size, dtype=np.int64)
    aktiv = np.arange(j.size)
    for k in range(1, max_match):
        b = m - 1 - k                                 # position in the current tail
        if b < 0 or not o[b] or brk[b] or aktiv.size == 0:
            break
        a = j[aktiv] - 1 - k                          # same offset in the earlier stretch
        ok = (a >= 0) & (a < b)
        ok[ok] &= (h[a[ok]] == h[b]) & o[a[ok]] & ~brk[a[ok]]
        aktiv = aktiv[ok]
        laenge[aktiv] += 1
    strafe: dict = {}
    for pos, L in zip(j, laenge):
        if L >= cfg.dry_allowed_length:
            t = int(h[pos])
            if word_start is not None and not word_start[t]:
                continue
            p = cfg.dry_multiplier * cfg.dry_base ** (int(L) - cfg.dry_allowed_length)
            if p > strafe.get(t, 0.0):
                strafe[t] = p
    return strafe


class Sampler:
    def __init__(self, cfg: SamplerConfig, device):
        self.cfg = cfg
        self.gen = torch.Generator(device=device)
        if cfg.seed is not None:
            self.gen.manual_seed(int(cfg.seed))
        else:
            self.gen.seed()

    @torch.inference_mode()
    def sample(self, logits: torch.Tensor, history: List[int], own=None, breaker=None, word_start=None) -> int:
        cfg = self.cfg
        logits = logits.float()
        if cfg.dry_multiplier > 0 and own is not None and breaker is not None:
            strafe = dry_penalties(history, own, breaker, cfg, word_start=word_start)
            if strafe:
                logits = logits.clone()
                idx = torch.tensor(list(strafe), device=logits.device)
                logits[idx] -= torch.tensor(list(strafe.values()), device=logits.device, dtype=logits.dtype)
        if history and (cfg.repeat_penalty != 1.0 or cfg.presence_penalty != 0.0):
            logits = logits.clone()
            recent = torch.unique(torch.tensor(history[-cfg.repeat_last_n:], device=logits.device))
            vals = logits[recent]
            if cfg.repeat_penalty != 1.0:
                vals = torch.where(vals > 0, vals / cfg.repeat_penalty, vals * cfg.repeat_penalty)
            logits[recent] = vals - cfg.presence_penalty
        if cfg.temperature <= 0:
            return int(torch.argmax(logits).item())

        # work on the top-k candidates only (k = vocab size if top-k is disabled)
        k = cfg.top_k if 0 < cfg.top_k < logits.numel() else logits.numel()
        vals, idx = torch.topk(logits, k)                 # sorted descending
        probs = torch.softmax(vals / cfg.temperature, dim=-1)
        if cfg.min_p > 0:
            probs = torch.where(probs < cfg.min_p * probs[0], torch.zeros_like(probs), probs)
        if 0 < cfg.top_p < 1:
            cum = torch.cumsum(probs, dim=-1)
            probs = torch.where(cum - probs > cfg.top_p, torch.zeros_like(probs), probs)
        choice = torch.multinomial(probs, 1, generator=self.gen)
        return int(idx[choice].item())
