"""Laufzeit des Einrichtungs-Helfers in reinem Python (ohne numpy/torch).

Gewichte liegen als array('f')-Zeilen vor (eine Zeile je Ausgabe), gerechnet wird mit math.sumprod und einem
KV-Cache. Bei ~3 Mio. Parametern dauert eine Antwort wenige Sekunden.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import struct
from array import array
from pathlib import Path

from einrichtungshelfer.schema import ATOMAR_GROSS, eingabe
from einrichtungshelfer.wortschatz import Wortschatz

KENNUNG = b"NEMIH1"
_sumprod = math.sumprod


def stuecke_zusammensetzen(datei: Path) -> bool:
    """Fehlt `datei`, liegen aber Stücke daneben (helfer.bin.001, .002 …), werden sie einmal zur Datei
    zusammengesetzt – große Modelldateien stehen im Repo in Stücken, weil der Web-Upload keine großen
    Einzeldateien annimmt. Mit `<datei>.sha256` daneben wird das Ergebnis geprüft. True = Datei da."""
    datei = Path(datei)
    if datei.is_file():
        return True
    stuecke = sorted(datei.parent.glob(datei.name + ".[0-9][0-9][0-9]"))
    if not stuecke or [int(s.suffix[1:]) for s in stuecke] != list(range(1, len(stuecke) + 1)):
        return False                                        # keine Stücke oder eins fehlt
    tmp = datei.with_name(datei.name + ".tmp")
    pruef = hashlib.sha256()
    try:
        with open(tmp, "wb") as aus:
            for s in stuecke:
                teil = s.read_bytes()
                pruef.update(teil)
                aus.write(teil)
        soll = datei.with_name(datei.name + ".sha256")
        if soll.is_file() and soll.read_text(encoding="utf-8").split()[0].lower() != pruef.hexdigest():
            tmp.unlink()
            return False
        os.replace(tmp, datei)
        return True
    except (OSError, IndexError):
        tmp.unlink(missing_ok=True)
        return False


def _flach(roh: memoryview, typ: str) -> array:
    """float32 direkt, float16 (typ "f2") über memoryview.cast("e")."""
    if typ == "f2":
        return array("f", roh.cast("B").cast("e"))
    flach = array("f")
    flach.frombytes(roh)
    return flach


def _zeilen(roh: memoryview, form: list[int], typ: str = "f4") -> list[array]:
    spalten = form[-1]
    flach = _flach(roh, typ)
    return [flach[i:i + spalten] for i in range(0, len(flach), spalten)]


def _vektor(roh: memoryview, typ: str = "f4") -> list[float]:
    return _flach(roh, typ).tolist()


def _matvec(zeilen: list[array], x: list[float]) -> list[float]:
    return [_sumprod(z, x) for z in zeilen]


def _layernorm(x: list[float], w: list[float], b: list[float]) -> list[float]:
    n = len(x)
    mittel = math.fsum(x) / n
    d = [v - mittel for v in x]
    s = 1.0 / math.sqrt(_sumprod(d, d) / n + 1e-5)
    return [v * s * wi + bi for v, wi, bi in zip(d, w, b)]


_K = math.sqrt(2.0 / math.pi)


def _gelu(x: list[float]) -> list[float]:
    t = math.tanh
    return [0.5 * v * (1.0 + t(_K * (v + 0.044715 * v * v * v))) for v in x]


class Modell:
    def __init__(self, pfad: Path):
        daten = Path(pfad).read_bytes()
        if not daten.startswith(KENNUNG):
            raise ValueError("keine Helfer-Datei")
        n = struct.unpack_from("<I", daten, len(KENNUNG))[0]
        start = len(KENNUNG) + 4
        kopf = json.loads(daten[start:start + n].decode("utf-8"))
        roh = memoryview(daten)[start + n:]
        self.cfg = kopf["cfg"]
        t = {}
        typ = kopf.get("typ", "f4")
        breite = 2 if typ == "f2" else 4
        for name, info in kopf["tensoren"].items():
            stueck = roh[info["ab"]:info["ab"] + breite * math.prod(info["form"])]
            t[name] = _zeilen(stueck, info["form"], typ) if len(info["form"]) == 2 else _vektor(stueck, typ)
        self.t = t
        self.schichten = self.cfg["schichten"]
        self.d = self.cfg["breite"]
        self.koepfe = self.cfg["koepfe"]
        self.kd = self.d // self.koepfe
        self.wpe = [z.tolist() for z in t["wpe"]]

    def neu(self) -> list[tuple[list, list]]:
        """Leerer KV-Cache: je Schicht (Schlüssel, Werte), je Position eine Liste pro Kopf."""
        return [([], []) for _ in range(self.schichten)]

    def schritt(self, token: int, pos: int, cache: list, mit_logits: bool = True) -> list[float] | None:
        t, d, kd = self.t, self.d, self.kd
        x = [a + b for a, b in zip(t["wte"][token], self.wpe[pos])]
        skala = 1.0 / math.sqrt(kd)
        for i in range(self.schichten):
            h = _layernorm(x, t[f"{i}.ln1.w"], t[f"{i}.ln1.b"])
            qkv = _matvec(t[f"{i}.qkv"], h)
            q, k, v = qkv[:d], qkv[d:2 * d], qkv[2 * d:]
            schluessel, werte = cache[i]
            schluessel.append([k[j:j + kd] for j in range(0, d, kd)])
            werte.append([v[j:j + kd] for j in range(0, d, kd)])
            aus: list[float] = []
            for kopf in range(self.koepfe):
                qk = q[kopf * kd:(kopf + 1) * kd]
                punkte = [_sumprod(qk, s[kopf]) * skala for s in schluessel]
                m = max(punkte)
                e = [math.exp(p - m) for p in punkte]
                summe = math.fsum(e)
                gew = [g / summe for g in e]
                spalte = [0.0] * kd
                for g, w in zip(gew, werte):
                    wk = w[kopf]
                    spalte = [a + g * b for a, b in zip(spalte, wk)]
                aus.extend(spalte)
            x = [a + b for a, b in zip(x, _matvec(t[f"{i}.proj"], aus))]
            h = _layernorm(x, t[f"{i}.ln2.w"], t[f"{i}.ln2.b"])
            x = [a + b for a, b in zip(x, _matvec(t[f"{i}.fc_proj"], _gelu(_matvec(t[f"{i}.fc"], h))))]
        if not mit_logits:
            return None
        return _matvec(t["wte"], _layernorm(x, t["ln_f.w"], t["ln_f.b"]))


def _waehlen(logits: list[float], temperatur: float, top_k: int, rng: random.Random) -> int:
    if temperatur <= 0:
        return max(range(len(logits)), key=logits.__getitem__)
    beste = sorted(range(len(logits)), key=logits.__getitem__, reverse=True)[:top_k]
    m = logits[beste[0]]
    gew = [math.exp((logits[i] - m) / temperatur) for i in beste]
    return rng.choices(beste, weights=gew)[0]


def bereinigen(text: str) -> str:
    """Nutzertext darf keine Marken oder Platzhalter einschleusen."""
    text = " ".join(text.split())[:300]
    for a in ATOMAR_GROSS:
        text = text.replace(a, a.replace("<", "‹").replace(">", "›").replace("{", "(").replace("}", ")"))
    return text


class Helfer:
    def __init__(self, ordner: Path, seed: int | None = None):
        ordner = Path(ordner)
        self.ws = Wortschatz.laden(ordner / "wortschatz.json")
        stuecke_zusammensetzen(ordner / "helfer.bin")
        self.modell = Modell(ordner / "helfer.bin")
        self.rng = random.Random(seed)
        self.ende = self.ws.id["</h>"]

    def antworten(self, zust: str, nutzer: str = "", temperatur: float = 0.6, top_k: int = 8,
                  max_neu: int = 140, beim_token=None) -> str:
        """Rohe Antwort ohne </h>, z. B. '[anrede=tom] Hallo {name}!'. In der Aktions-Klammer wird nicht gewürfelt.
        beim_token(bisher) bekommt nach jedem Token den Text bis dahin (Wort-für-Wort-Anzeige)."""
        ids = self.ws.kodieren(eingabe(zust, bereinigen(nutzer)))
        grenze = self.modell.cfg["kontext"]
        ids = ids[-(grenze - max_neu):] if len(ids) > grenze - max_neu else ids
        cache = self.modell.neu()
        for pos, tok in enumerate(ids[:-1]):
            self.modell.schritt(tok, pos, cache, mit_logits=False)
        logits = self.modell.schritt(ids[-1], len(ids) - 1, cache)
        aus: list[int] = []
        in_klammer = True
        for _ in range(max_neu):
            tok = _waehlen(logits, 0.0 if in_klammer else temperatur, top_k, self.rng)
            if tok == self.ende:
                break
            aus.append(tok)
            if in_klammer and "]" in self.ws.tokens[tok]:
                in_klammer = False
            if beim_token is not None:
                beim_token(self.ws.dekodieren(aus))
            pos = len(ids) + len(aus) - 1
            if pos >= grenze:
                break
            logits = self.modell.schritt(tok, pos, cache)
        return self.ws.dekodieren(aus).strip()
