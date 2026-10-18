"""Die Modell-Datenbank (ggufengine/daten/*.json): Bauarten, Chat-Formate, Werkzeug-Formate, Hardware.

Die Dateien sind reine Daten – nie ausgeführt, nie Pickle. Sie beschreiben, WAS ein Modell braucht
(welche Steine, welche Marken, welche Reihenfolge); WIE gerechnet wird, steht als Code in den
Steinen (models/, chat.py, werkzeugsprache.py). Ein neues Modell aus bekannten Steinen braucht
nur einen Eintrag hier. Jede Datei wird beim ersten Lesen auf ihre Form geprüft.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .gguf import GGUFError

ORDNER = Path(__file__).resolve().parent / "daten"


class DbFehler(GGUFError):
    """Eine Datei der Modell-Datenbank fehlt oder hat eine falsche Form."""


@lru_cache(maxsize=None)
def lesen(name: str) -> dict:
    """Eine Datei der Datenbank (`name` ohne .json), geprüft und zwischengespeichert."""
    pfad = ORDNER / f"{name}.json"
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DbFehler(f"Modell-Datenbank {pfad.name}: {exc}") from exc
    if not isinstance(daten, dict) or not isinstance(daten.get("version"), int):
        raise DbFehler(f"Modell-Datenbank {pfad.name}: kein Objekt mit 'version'")
    return daten


def _passt(regel: dict, text: str, arch: str = "") -> bool:
    """Eine Erkennungs-Regel: `bauart` (eine davon) ODER alle `alle` im Text, mindestens eines aus
    `eins` (falls angegeben) und keines aus `keins`."""
    if arch and arch in regel.get("bauart", ()):
        return True
    if not regel.get("alle") and not regel.get("eins"):
        return False
    return (all(s in text for s in regel.get("alle", ()))
            and (not regel.get("eins") or any(s in text for s in regel["eins"]))
            and not any(s in text for s in regel.get("keins", ())))


def erkennen(liste: list, text: str, arch: str = "") -> Optional[dict]:
    """Der erste Eintrag der Liste, dessen Regeln (`wenn`: eine davon genügt) passen."""
    for eintrag in liste:
        if any(_passt(r, text, arch) for r in eintrag.get("wenn", ())):
            return eintrag
    return None


# --- die einzelnen Tabellen ------------------------------------------------------------------------

def werkzeugformate() -> list:
    return lesen("werkzeugformate")["formate"]


def werkzeugformat(name: str) -> dict:
    for f in werkzeugformate():
        if f["name"] == name:
            return f
    raise DbFehler(f"kein Werkzeug-Format {name!r} in der Modell-Datenbank")


def chatformat(vorlage: str, arch: str) -> str:
    """Chat-Format aus Vorlage und Bauart (chatformate.json: Erkennung, dann Bauart, dann Vorgabe)."""
    d = lesen("chatformate")
    eintrag = erkennen(d["erkennung"], vorlage, arch)
    if eintrag is not None:
        return eintrag["format"]
    if arch in d["nach_bauart"]:
        return d["nach_bauart"][arch]
    for praefix, fmt in d["bauart_praefix"].items():
        if arch.startswith(praefix):
            return fmt
    return d["standard"]


def rollen_formate() -> dict:
    return lesen("chatformate")["rollen_formate"]


# Merkmale einer Bauart und ihre erlaubten Typen (bauarten.json); fehlt eins, gilt die Grundform
_BAU_MERKMALE = {
    "moe": bool, "rope_paare": bool, "aktivierung": str, "einbettung_wurzel": bool, "fenster_muster": int,
    "global_ohne_rope": bool, "ohne_rope_jede": int, "fenster_rope_schlicht": bool, "parallel": bool,
    "logits_mal_skala": bool, "fenster_basis": float, "topk_normieren": bool, "softmax_nach_auswahl": bool,
    "ffn_norm_heisst_post_attn": bool, "yarn_ungerundet": bool, "yarn_log_mul_all_dim": bool, "glu_oai": bool,
    "temperatur_skala": bool, "gross_27b_bei_schichten": int,
}
_AKTIVIERUNGEN = ("silu", "gelu")


@lru_cache(maxsize=None)
def bauarten() -> dict:
    """Alle Bauarten, geprüft: jede hat eine `klasse`, nur bekannte Merkmale mit passendem Typ."""
    tabelle = lesen("bauarten").get("bauarten")
    if not isinstance(tabelle, dict) or not tabelle:
        raise DbFehler("Modell-Datenbank bauarten.json: 'bauarten' fehlt")
    for arch, e in tabelle.items():
        if not isinstance(e, dict) or not isinstance(e.get("klasse"), str):
            raise DbFehler(f"Bauart {arch!r}: 'klasse' fehlt")
        for k, v in e.items():
            if k == "klasse":
                continue
            typ = _BAU_MERKMALE.get(k)
            if typ is None:
                raise DbFehler(f"Bauart {arch!r}: unbekanntes Merkmal {k!r}")
            ok = (isinstance(v, (int, float)) and not isinstance(v, bool)) if typ is float else \
                (isinstance(v, typ) and (typ is bool or not isinstance(v, bool)))
            if not ok or (typ is int and v < 1) or (k == "aktivierung" and v not in _AKTIVIERUNGEN):
                raise DbFehler(f"Bauart {arch!r}: {k}={v!r} ungültig")
    return tabelle


def bauart(arch: str) -> Optional[dict]:
    """Eintrag einer Bauart oder None, wenn sie nicht in der Datenbank steht."""
    return bauarten().get(arch)


def bauarten_der_klasse(klasse: str) -> tuple:
    return tuple(a for a, e in bauarten().items() if e["klasse"] == klasse)
