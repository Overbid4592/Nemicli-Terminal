"""Training von Funke (braucht torch; nur in der Werkstatt).

Zwei Arten: „klein“ (Funke für /einrichten, Notfall und Plaudern – läuft in reinem Python) und „gross“
(Chat-Modell in /model mit Denken, Vorgaben und Gesprächs-Gedächtnis). Gelernt werden nur die Antworten
(nach </n> bis einschließlich </h>, beim großen samt Denken).

Aufruf: python -m einrichtungshelfer.training <werkstatt> [--art klein|gross] [--epochen 6]
Ergebnis: <werkstatt>/Modell/helfer.bin + wortschatz.json, Lernkurve in <werkstatt>/Training/.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
import torch.nn.functional as F

from einrichtungshelfer import daten, daten_gross
from einrichtungshelfer.schema import ATOMAR, ATOMAR_GROSS, aktion_lesen
from einrichtungshelfer.torchmodell import Helfer, gewichte, speichern  # noqa: F401  (gewichte: für Tests)
from einrichtungshelfer.wortschatz import Wortschatz, trainieren

PROBE_GROSS = "<v>name=Funke;anrede=du;laenge=normal;emoji=ja</v><n>wie kann ich ein bild malen</n>"

ARTEN = {
    "klein": {"groesse": {"schichten": 6, "breite": 256, "koepfe": 8, "kontext": 256}, "wortschatz": 3072,
              "atomar": ATOMAR, "daten": daten.erzeugen, "stapel": 128, "lr": 3e-3, "takt": 100},
    "gross": {"groesse": {"schichten": 8, "breite": 512, "koepfe": 8, "kontext": 512}, "wortschatz": 4096,
              "atomar": ATOMAR_GROSS, "daten": daten_gross.erzeugen, "stapel": 48, "lr": 1.5e-3, "takt": 250},
}


def maske(ids: list[int], n_ende: int, h_ende: int) -> list[int]:
    """1 für Ziel-Tokens innerhalb der Antworten (nach </n> bis einschließlich </h>), sonst 0."""
    aus, innen = [0] * (len(ids) - 1), False
    for t in range(len(ids) - 1):
        if ids[t] == n_ende:
            innen = True
        if innen:
            aus[t] = 1
            if ids[t + 1] == h_ende:
                innen = False
    return aus


def _stapel(paare: list[tuple[list[int], list[int]]], geraet: str) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    laenge = max(len(ids) for ids, _ in paare) - 1
    x = torch.zeros(len(paare), laenge, dtype=torch.long)
    y = torch.zeros_like(x)
    m = torch.zeros(len(paare), laenge)
    for i, (ids, mk) in enumerate(paare):
        n = len(ids) - 1
        x[i, :n] = torch.tensor(ids[:-1])
        y[i, :n] = torch.tensor(ids[1:])
        m[i, :n] = torch.tensor(mk, dtype=torch.float)
    return x.to(geraet), y.to(geraet), m.to(geraet)


@torch.no_grad()
def _verlust(modell: Helfer, paare: list, geraet: str) -> float:
    summe, anzahl = 0.0, 0.0
    for i in range(0, len(paare), 64):                 # in Häppchen – sonst belegt die Prüfung viel Grafikspeicher
        x, y, m = _stapel(paare[i:i + 64], geraet)
        summe += float((F.cross_entropy(modell(x).transpose(1, 2), y, reduction="none") * m).sum())
        anzahl += float(m.sum())
    return summe / anzahl


@torch.no_grad()
def _weiter(modell: Helfer, ws: Wortschatz, vorne: str, geraet: str, max_neu: int, halt) -> str:
    seq = ws.kodieren(vorne)
    start, grenze = len(seq), modell.wpe.num_embeddings
    for _ in range(max_neu):
        nxt = int(modell(torch.tensor([seq[-grenze:]], device=geraet))[0, -1].argmax())
        seq.append(nxt)
        if halt(nxt):
            break
    return ws.dekodieren(seq[start:])


@torch.no_grad()
def _aktionen(modell: Helfer, ws: Wortschatz, test: list[str], geraet: str, n: int = 150) -> float:
    """Anteil richtiger Aktionen (Funke klein)."""
    richtig, ende = 0, ws.id["</h>"]
    for text in test[:n]:
        vorne, _, soll = text.partition("<h>")
        ist = _weiter(modell, ws, vorne + "<h>", geraet, 24, lambda t: t == ende or "]" in ws.tokens[t])
        richtig += aktion_lesen(ist + " x")[:2] == aktion_lesen(soll)[:2]
    return richtig / min(n, len(test))


def main() -> None:
    ap = argparse.ArgumentParser(description="Funke trainieren")
    ap.add_argument("werkstatt", type=Path)
    ap.add_argument("--art", choices=list(ARTEN), default="klein")
    ap.add_argument("--anzahl", type=int, default=60_000)
    ap.add_argument("--epochen", type=int, default=6)
    ap.add_argument("--stapel", type=int)
    ap.add_argument("--lr", type=float)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    art = ARTEN[a.art]
    stapel, lr, klein = a.stapel or art["stapel"], a.lr or art["lr"], a.art == "klein"
    torch.manual_seed(a.seed)
    geraet = "cuda" if torch.cuda.is_available() else "cpu"
    ordner_daten, ordner_train, ordner_modell = (a.werkstatt / n for n in ("Daten", "Training", "Modell"))
    for o in (ordner_daten, ordner_train, ordner_modell):
        o.mkdir(parents=True, exist_ok=True)

    train, test = art["daten"](a.anzahl, a.seed)
    for name, liste in (("train.jsonl", train), ("pruefung.jsonl", test)):
        (ordner_daten / name).write_text("".join(json.dumps({"text": z}, ensure_ascii=False) + "\n" for z in liste),
                                         encoding="utf-8")
    ws = trainieren(train, art["wortschatz"], art["atomar"])
    ws.speichern(ordner_modell / "wortschatz.json")
    cfg = dict(art["groesse"], vokabular=len(ws))
    n_ende, h_ende = ws.id["</n>"], ws.id["</h>"]

    def paar(text: str) -> tuple[list[int], list[int]]:
        ids = ws.kodieren(text)[:cfg["kontext"] + 1]
        return ids, maske(ids, n_ende, h_ende)

    kodiert = [paar(t) for t in train]
    test_paare = [paar(t) for t in test]
    modell = Helfer(**cfg).to(geraet)
    zahl = sum(p.numel() for p in modell.parameters())
    print(f"🐣 Gehirn: {zahl / 1e6:.2f} Mio. Parameter · Wortschatz {len(ws)} · {len(train)} Gespräche · {geraet}",
          flush=True)

    schritte = a.epochen * math.ceil(len(kodiert) / stapel)
    opt = torch.optim.AdamW(modell.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.1)
    warm = 200
    plan = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warm) * (
        0.03 + 0.97 * 0.5 * (1 + math.cos(math.pi * min(1.0, s / schritte)))))
    kurve = open(ordner_train / "lernkurve.csv", "w", encoding="utf-8")
    kurve.write("schritt;verlust;pruef_verlust;aktionen_richtig;sekunden\n")
    rng, schritt, t0 = random.Random(a.seed), 0, time.time()
    for epoche in range(a.epochen):
        rng.shuffle(kodiert)
        for i in range(0, len(kodiert), stapel):
            x, y, m = _stapel(kodiert[i:i + stapel], geraet)
            with torch.autocast(geraet, dtype=torch.bfloat16, enabled=geraet == "cuda"):
                logits = modell(x)
            verlust = (F.cross_entropy(logits.float().transpose(1, 2), y, reduction="none") * m).sum() / m.sum()
            opt.zero_grad(set_to_none=True)
            verlust.backward()
            torch.nn.utils.clip_grad_norm_(modell.parameters(), 1.0)
            opt.step()
            plan.step()
            schritt += 1
            if schritt % art["takt"] == 0 or schritt == schritte:
                modell.eval()
                pv = _verlust(modell, test_paare, geraet)
                ak = _aktionen(modell, ws, test, geraet) if klein else 0.0
                probe = "" if klein else _weiter(modell, ws, PROBE_GROSS, geraet, 90, lambda t: t == h_ende)
                modell.train()
                sek, v = time.time() - t0, verlust.item()
                kurve.write(f"{schritt};{v:.4f};{pv:.4f};{ak:.3f};{sek:.0f}\n")
                kurve.flush()
                zusatz = f"Aktionen richtig {ak:.0%}" if klein else f"Probe: {probe[:180]}"
                print(f"Epoche {epoche + 1} · Schritt {schritt}/{schritte} · Verlust {v:.3f} · Prüfung {pv:.3f} · "
                      f"{sek:.0f} s · {zusatz}", flush=True)
    kurve.close()
    speichern(modell, cfg, ordner_modell / "helfer.bin")
    print(f"✅ Gespeichert: {ordner_modell / 'helfer.bin'}")


if __name__ == "__main__":
    main()
