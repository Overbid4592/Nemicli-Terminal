"""Funkes Gehirn als torch-Modell – fürs Training und als schnelle Laufzeit, wenn torch da ist.

Kleiner GPT: Pre-LayerNorm, GELU (tanh), gelernte Positionen, Ein- und Ausgabe-Einbettung geteilt, Linear-Schichten
ohne Bias. `Generator` rechnet mit KV-Cache auf der CPU (die Grafikkarte bleibt für Bilder und Videos frei).
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

KENNUNG = b"NEMIH1"


class Block(nn.Module):
    def __init__(self, d: int, koepfe: int):
        super().__init__()
        self.ln1, self.ln2 = nn.LayerNorm(d), nn.LayerNorm(d)
        self.qkv = nn.Linear(d, 3 * d, bias=False)
        self.proj = nn.Linear(d, d, bias=False)
        self.fc = nn.Linear(d, 4 * d, bias=False)
        self.fc_proj = nn.Linear(4 * d, d, bias=False)
        self.koepfe = koepfe

    def forward(self, x: torch.Tensor, cache: list | None = None) -> torch.Tensor:
        b, t, d = x.shape
        q, k, v = self.qkv(self.ln1(x)).split(d, dim=2)
        form = (b, t, self.koepfe, d // self.koepfe)
        q, k, v = (z.view(form).transpose(1, 2) for z in (q, k, v))
        if cache is not None:                          # [k, v] der bisherigen Positionen
            if cache:
                k, v = torch.cat([cache[0], k], dim=2), torch.cat([cache[1], v], dim=2)
            cache[:] = [k, v]
        a = F.scaled_dot_product_attention(q, k, v, is_causal=cache is None or t > 1)
        x = x + self.proj(a.transpose(1, 2).reshape(b, t, d))
        return x + self.fc_proj(F.gelu(self.fc(self.ln2(x)), approximate="tanh"))


class Helfer(nn.Module):
    def __init__(self, vokabular: int, schichten: int, breite: int, koepfe: int, kontext: int):
        super().__init__()
        self.wte = nn.Embedding(vokabular, breite)
        self.wpe = nn.Embedding(kontext, breite)
        self.bloecke = nn.ModuleList(Block(breite, koepfe) for _ in range(schichten))
        self.ln_f = nn.LayerNorm(breite)
        self.apply(self._init)
        for n, p in self.named_parameters():
            if n.endswith("proj.weight"):
                nn.init.normal_(p, std=0.02 / math.sqrt(2 * schichten))

    @staticmethod
    def _init(m: nn.Module) -> None:
        if isinstance(m, (nn.Linear, nn.Embedding)):
            nn.init.normal_(m.weight, std=0.02)

    def forward(self, ids: torch.Tensor, start: int = 0, caches: list | None = None) -> torch.Tensor:
        pos = torch.arange(start, start + ids.shape[1], device=ids.device)
        x = self.wte(ids) + self.wpe(pos)
        for i, blk in enumerate(self.bloecke):
            x = blk(x, None if caches is None else caches[i])
        return self.ln_f(x) @ self.wte.weight.T


def gewichte(modell: Helfer) -> dict[str, torch.Tensor]:
    """Namen wie in laufzeit.Modell (reines Python) erwartet."""
    aus = {"wte": modell.wte.weight, "wpe": modell.wpe.weight,
           "ln_f.w": modell.ln_f.weight, "ln_f.b": modell.ln_f.bias}
    for i, b in enumerate(modell.bloecke):
        aus |= {f"{i}.ln1.w": b.ln1.weight, f"{i}.ln1.b": b.ln1.bias, f"{i}.ln2.w": b.ln2.weight,
                f"{i}.ln2.b": b.ln2.bias, f"{i}.qkv": b.qkv.weight, f"{i}.proj": b.proj.weight,
                f"{i}.fc": b.fc.weight, f"{i}.fc_proj": b.fc_proj.weight}
    return aus


def speichern(modell: Helfer, cfg: dict, pfad: Path, halb: bool = False) -> None:
    """Eigenes Format: Kennung, Kopf-Länge, JSON-Kopf, Daten (little endian; float32 oder mit halb float16)."""
    tensoren, teile, ofs = {}, [], 0
    for name, t in gewichte(modell).items():
        roh = t.detach().float().cpu().contiguous().numpy().astype("<f2" if halb else "<f4").tobytes()
        tensoren[name] = {"form": list(t.shape), "ab": ofs}
        teile.append(roh)
        ofs += len(roh)
    kopf = json.dumps({"cfg": cfg, "tensoren": tensoren} | ({"typ": "f2"} if halb else {})).encode("utf-8")
    with open(pfad, "wb") as f:
        f.write(KENNUNG + struct.pack("<I", len(kopf)) + kopf)
        for roh in teile:
            f.write(roh)


def laden(pfad: Path) -> Helfer:
    daten = Path(pfad).read_bytes()
    if not daten.startswith(KENNUNG):
        raise ValueError("keine Helfer-Datei")
    n = struct.unpack_from("<I", daten, len(KENNUNG))[0]
    start = len(KENNUNG) + 4
    kopf = json.loads(daten[start:start + n].decode("utf-8"))
    roh = memoryview(daten)[start + n:]
    cfg = {k: kopf["cfg"][k] for k in ("vokabular", "schichten", "breite", "koepfe", "kontext")}
    halb = kopf.get("typ") == "f2"
    modell = Helfer(**cfg)
    ziel = gewichte(modell)
    with torch.no_grad():
        for name, info in kopf["tensoren"].items():
            anzahl = math.prod(info["form"])
            stueck = bytearray(roh[info["ab"]:info["ab"] + (2 if halb else 4) * anzahl])
            werte = torch.frombuffer(stueck, dtype=torch.float16 if halb else torch.float32).float()
            ziel[name].copy_(werte.view(*info["form"]))
    modell.cfg = cfg
    return modell.eval()


def halbieren(quelle: Path, ziel: Path) -> None:
    """Gespeichertes Modell in float16 umschreiben (halbe Dateigröße)."""
    modell = laden(quelle)
    speichern(modell, modell.cfg, ziel, halb=True)


class Generator:
    """Schrittweise Vorhersage mit KV-Cache (wie laufzeit.Modell.schritt, nur schnell)."""

    def __init__(self, pfad: Path, threads: int | None = None):
        if threads:                                    # nur auf Wunsch: gilt für den ganzen Prozess
            torch.set_num_threads(threads)
        self.modell = laden(pfad)
        self.cfg = {"kontext": self.modell.wpe.num_embeddings}

    @torch.no_grad()
    def vorne(self, ids: list[int]) -> tuple[list, list[float]]:
        """Vorspann auf einmal rechnen; (Cache, Logits des letzten Tokens)."""
        caches = [[] for _ in self.modell.bloecke]
        logits = self.modell(torch.tensor([ids]), 0, caches)[0, -1]
        return caches, logits.tolist()

    @torch.no_grad()
    def schritt(self, token: int, pos: int, caches: list) -> list[float]:
        return self.modell(torch.tensor([[token]]), pos, caches)[0, -1].tolist()
