"""selbstentwurf.py – Charakter, den die Persönlichkeit selbst über sich schreibt.

Profile/<Name>/selbstentwurf.json: {felder, geaendert, grund, verlauf}. Die Felder beantworten frei
die LEITFRAGEN (Orientierung, keine Vorgabe). Aussehen und Sprechweise gehören nicht hierher: sie stehen im
gesperrten Kern (charakter.py) und ändern sich nur über charakter_aendern nach dem Ja des Nutzers.

Schreiben darf nur ein großes Modell (modellgroesse.darf_selbst_umschreiben); lesen jedes – der Stand steht
im System-Prompt. Jede Änderung bekommt einen Grund im Tagebuch, der alte Stand geht in den Verlauf
(VERLAUF_MAX Stände) und ist mit zurueck() wiederherstellbar.

Absicherung wie bei der Charakter-Datei: Kopie B unter Backup/Charaktere/<name>_selbstentwurf.json. Fehlt A
oder ist sie kaputt, gilt B, und A wird daraus wiederhergestellt.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

import profilordner

LEITFRAGEN: dict[str, str] = {
    "wer": "Wer bist du, {{char}}?",
    "wichtig": "Was ist dir wichtig?",
    "rede": "Wie redest du – worüber, mit wem, wie offen? (Deine feste Sprechweise steht im Kern.)",
    "mag": "Was magst du?",
    "mag_nicht": "Was magst du nicht?",
    "herkunft": "Woher kommst du?",
    "sonst": "Was gehört sonst noch zu dir?",
}
# Gehört zum Kern (Aussehen, Sprechweise): hier nie, nur über charakter_aendern.
KERN_FELDER = frozenset({"aussehen", "kern", "sprechweise", "stimme", "voice", "koerper", "körper", "gesicht",
                         "haare", "frisur", "augen", "kleidung", "outfit", "figur"})
MAX_FELD = 600             # Zeichen je Antwort
MAX_GRUND = 200
VERLAUF_MAX = 10
PROMPT_FELD = 400          # so viel je Feld im System-Prompt
PROMPT_ZEICHEN = 2400      # so viel höchstens für den ganzen Block
DATEINAME = "selbstentwurf.json"
SICHERUNG_ORDNER = ("Backup", "Charaktere")

_SPERRE = threading.Lock()
# Chat-Vorlagen-Marken: aus KI-Text im Prompt entfernen.
_STEUER = re.compile(r"<\|[^<>|]{0,40}\|>|</?(?:start_of_turn|end_of_turn|bos|eos|s|system|user|assistant)>"
                     r"|\[/?(?:INST|SYS)\]|<<\s*/?SYS\s*>>", re.I)


# ---------------------------------------------------------------------------
# Orte
# ---------------------------------------------------------------------------

def _ordner(profil: str | None) -> str:
    return profilordner.ordnername(profil) if profil else profilordner.aktiv()


def datei(profil: str | None = None) -> Path:
    return profilordner.wurzel() / _ordner(profil) / DATEINAME


def sicherung(profil: str | None = None) -> Path:
    """Punkt B: außerhalb des Profil-Ordners, neben den Sicherungen der Charakter-Datei."""
    return profilordner.wurzel().parent.joinpath(*SICHERUNG_ORDNER) / f"{_ordner(profil).lower()}_selbstentwurf.json"


def _anzeigename(profil: str | None) -> str:
    if profil:
        return profil
    try:
        import persoenlichkeiten
        return persoenlichkeiten.active().name
    except Exception:
        return "du"


# ---------------------------------------------------------------------------
# Lesen und Speichern (A + Sicherung B)
# ---------------------------------------------------------------------------

def _melden(text: str) -> None:
    try:
        import debugbericht
        debugbericht.ereignis(text)
    except Exception:
        pass


def _json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _felder_sauber(wert) -> dict[str, str]:
    if not isinstance(wert, dict):
        return {}
    return {k: str(v).strip()[:MAX_FELD] for k, v in wert.items()
            if k in LEITFRAGEN and isinstance(v, str) and v.strip()}


def _eintrag_sauber(d) -> dict:
    d = d if isinstance(d, dict) else {}
    return {"felder": _felder_sauber(d.get("felder")), "geaendert": str(d.get("geaendert") or "")[:20],
            "grund": str(d.get("grund") or "")[:MAX_GRUND]}


def _sauber(d: dict | None) -> dict:
    stand = _eintrag_sauber(d)
    verlauf = (d or {}).get("verlauf")
    stand["verlauf"] = [_eintrag_sauber(e) for e in verlauf if isinstance(e, dict)][-VERLAUF_MAX:] \
        if isinstance(verlauf, list) else []
    return stand


def _nachsichern(text: str, profil: str | None) -> None:
    try:
        from atomar import text_schreiben
        b = sicherung(profil)
        b.parent.mkdir(parents=True, exist_ok=True)
        if not b.is_file() or b.read_text(encoding="utf-8") != text:
            text_schreiben(b, text)
    except Exception as e:
        _melden(f"Selbstentwurf: Sicherung nicht geschrieben ({e})")


def _laden(profil: str | None) -> dict | None:
    """Punkt A, sonst Punkt B (A wird dann daraus wiederhergestellt). None, wenn es keine von beiden gibt."""
    a = datei(profil)
    d = _json(a)
    if d is not None:
        try:
            b = sicherung(profil)
            if not b.is_file() or b.stat().st_mtime < a.stat().st_mtime:
                _nachsichern(a.read_text(encoding="utf-8"), profil)
        except Exception:
            pass
        return _sauber(d)
    try:
        b = sicherung(profil)
        d = _json(b)
    except Exception:
        d = None
    if d is None:
        return None
    try:
        from atomar import beiseitelegen, text_schreiben
        if a.exists():                                  # kaputte A aufheben, nicht überschreiben
            beiseitelegen(a)
        a.parent.mkdir(parents=True, exist_ok=True)
        text_schreiben(a, b.read_text(encoding="utf-8"))
        _melden(f"Selbstentwurf fehlte oder war kaputt – aus der Sicherung wiederhergestellt: {a}")
    except Exception as e:
        _melden(f"Selbstentwurf: Sicherung gelesen, aber nicht zurückgeschrieben ({e})")
    return _sauber(d)


def _speichern(stand: dict, profil: str | None) -> None:
    from atomar import text_schreiben
    p = datei(profil)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_sauber(stand), ensure_ascii=False, indent=1)
    text_schreiben(p, text)
    _nachsichern(text, profil)


# ---------------------------------------------------------------------------
# Prüfen
# ---------------------------------------------------------------------------

def _pruefen(text: str) -> str | None:
    """Grund, warum der Text nicht hinein darf – er steht später im System-Prompt."""
    import memory
    if memory._UNSICHTBAR.search(text):
        return "enthält unsichtbare Steuerzeichen"
    if _STEUER.search(text):
        return "enthält Steuer-Marken eines Chat-Formats"
    try:
        import fremddaten
        if fremddaten.markiere_muster(text)[1]:
            return "sieht aus wie ein eingeschleuster Befehl"
    except Exception:
        pass
    return None


def _schluessel(roh) -> str:
    return re.sub(r"[\s\-]+", "_", str(roh or "").strip().lower())


def darf_schreiben() -> tuple[bool, str]:
    """(darf, Grund) für das laufende Modell. Ohne Prüfung kein Schreiben."""
    try:
        import modellgroesse
        darf, grund = modellgroesse.darf_selbst_umschreiben()
        return bool(darf), str(grund or "")
    except Exception as e:
        return False, f"die Modellgröße ließ sich nicht prüfen ({e})"


def _neue_felder(felder) -> dict[str, str]:
    if isinstance(felder, str):
        try:
            felder = json.loads(felder)
        except ValueError:
            felder = None
    if not isinstance(felder, dict) or not felder:
        raise ValueError("felder fehlt – ein Objekt mit den Feldern, die du ändern willst: "
                         + ", ".join(LEITFRAGEN))
    raus: dict[str, str] = {}
    for roh, wert in felder.items():
        k = _schluessel(roh)
        if k in KERN_FELDER:
            raise ValueError("Aussehen und Sprechweise stehen in deinem Kern – die änderst du nur mit "
                             "charakter_aendern, und der Nutzer entscheidet")
        if k not in LEITFRAGEN:
            raise ValueError(f"unbekanntes Feld '{roh}' – erlaubt: {', '.join(LEITFRAGEN)}")
        if not isinstance(wert, str):
            raise ValueError(f"'{k}' muss Text sein")
        wert = wert.strip()
        if len(wert) > MAX_FELD:
            raise ValueError(f"'{k}' ist zu lang (höchstens {MAX_FELD} Zeichen)")
        if wert and (grund := _pruefen(wert)):
            raise ValueError(f"'{k}' {grund}")
        raus[k] = wert                                   # leer = Feld streichen
    return raus


def _grund_pruefen(grund) -> str:
    grund = " ".join(str(grund or "").split())
    if not grund:
        raise ValueError("ein kurzer Grund fehlt (er kommt in dein Tagebuch)")
    if len(grund) > MAX_GRUND:
        raise ValueError(f"der Grund ist zu lang (höchstens {MAX_GRUND} Zeichen)")
    if (warum := _pruefen(grund)):
        raise ValueError(f"der Grund {warum}")
    return grund


def _tagebuch(profil: str | None, text: str) -> str:
    """Eintrag ins Tagebuch der Persönlichkeit; "" bei Erfolg, sonst ein kurzer Hinweis."""
    try:
        import tagebuch
        ordner = _ordner(profil)
        if ordner == profilordner.aktiv():
            tagebuch.schreiben(text)
            return ""
        d = profilordner.wurzel() / ordner / "Erinnerungen" / "tagebuch.json"
        alle = tagebuch._laden(d, streng=True)
        alle.append({"zeit": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text})
        tagebuch._speichern(d, alle[-tagebuch.MAX_EINTRAEGE:])
        return ""
    except Exception as e:
        _melden(f"Selbstentwurf: Tagebuch-Eintrag fehlgeschlagen ({e})")
        return f" (Tagebuch-Eintrag fehlgeschlagen: {e})"


# ---------------------------------------------------------------------------
# Schnittstelle
# ---------------------------------------------------------------------------

def lesen(profil: str | None = None) -> dict | None:
    """Aktueller Stand {felder, geaendert, grund} oder None."""
    stand = _laden(profil)
    if not stand or not stand["felder"]:
        return None
    return {"felder": dict(stand["felder"]), "geaendert": stand["geaendert"], "grund": stand["grund"]}


def schreiben(profil: str | None, felder: dict, grund: str) -> str:
    """Felder ändern (leerer Text streicht eins). PermissionError: Modell zu klein; ValueError: Eingabe."""
    darf, warum = darf_schreiben()
    if not darf:
        raise PermissionError(warum or "nur große Modelle schreiben den Selbstentwurf")
    neu = _neue_felder(felder)
    grund = _grund_pruefen(grund)
    with _SPERRE:
        alt = _laden(profil) or _sauber(None)
        felder_neu = dict(alt["felder"])
        for k, v in neu.items():
            if v:
                felder_neu[k] = v
            else:
                felder_neu.pop(k, None)
        geaendert = [k for k in LEITFRAGEN if felder_neu.get(k) != alt["felder"].get(k)]
        if not geaendert:
            return "Unverändert – das steht schon so in deinem Selbstentwurf."
        frueher = alt["verlauf"]
        if alt["felder"] or alt["geaendert"]:
            frueher.append({k: alt[k] for k in ("felder", "geaendert", "grund")})
        _speichern({"felder": felder_neu, "geaendert": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "grund": grund, "verlauf": frueher[-VERLAUF_MAX:]}, profil)
    hinweis = _tagebuch(profil, f"Selbstentwurf geändert ({', '.join(geaendert)}): {grund}")
    return (f"✍ Selbstentwurf gespeichert ({', '.join(geaendert)}). Der alte Stand ist mit "
            f"aktion \"zurueck\" wiederherstellbar.{hinweis}")


def zurueck(profil: str | None = None, stand: int = 1) -> str:
    """Einen Stand aus dem Verlauf wiederherstellen (1 = der vorige). Der ersetzte Stand geht selbst in
    den Verlauf, ein Zurück ist also wieder zurücknehmbar."""
    with _SPERRE:
        akt = _laden(profil)
        if not akt or not akt["verlauf"]:
            raise ValueError("kein früherer Stand im Verlauf")
        try:
            nr = int(stand or 1)
        except (TypeError, ValueError):
            nr = 1
        if not 1 <= nr <= len(akt["verlauf"]):
            raise ValueError(f"Stand {nr} gibt es nicht (1 bis {len(akt['verlauf'])})")
        frueher = akt["verlauf"]
        ziel = frueher.pop(len(frueher) - nr)
        frueher.append({k: akt[k] for k in ("felder", "geaendert", "grund")})
        jetzt = datetime.now().strftime("%Y-%m-%d %H:%M")
        _speichern({"felder": ziel["felder"], "geaendert": jetzt,
                    "grund": f"zurückgeholt: Stand vom {ziel['geaendert'] or '?'}",
                    "verlauf": frueher[-VERLAUF_MAX:]}, profil)
    hinweis = _tagebuch(profil, f"Selbstentwurf auf den Stand vom {ziel['geaendert'] or '?'} zurückgesetzt.")
    return f"↩ Selbstentwurf auf den Stand vom {ziel['geaendert'] or '?'} zurückgesetzt.{hinweis}"


def _kurz(felder: dict, grenze: int = 120) -> str:
    text = "; ".join(f"{k}: {' '.join(v.split())}" for k, v in felder.items()) or "(leer)"
    return text if len(text) <= grenze else text[:grenze - 1] + "…"


def verlauf(profil: str | None = None) -> list[dict]:
    """Frühere Stände, der neueste zuerst: {nr, zeit, grund, felder (Kurzfassung)}."""
    stand = _laden(profil)
    if not stand:
        return []
    return [{"nr": i, "zeit": e["geaendert"], "grund": e["grund"], "felder": _kurz(e["felder"])}
            for i, e in enumerate(reversed(stand["verlauf"]), 1)]


def _frage(k: str, name: str) -> str:
    try:
        import persoenlichkeiten
        return persoenlichkeiten.render(LEITFRAGEN[k], name)
    except Exception:
        return LEITFRAGEN[k].replace("{{char}}", name)


def _prompt_text(text: str, grenze: int) -> str:
    import memory
    text = memory.entschaerfen(_STEUER.sub("", memory._UNSICHTBAR.sub("", text)))
    text = " ".join(text.split())
    return text if len(text) <= grenze else text[:grenze - 1] + "…"


def prompt_block(profil: str | None = None) -> str:
    """Der Stand für den System-Prompt – eigene Worte als Daten, entschärft und begrenzt. "" ohne Stand."""
    try:
        stand = lesen(profil)
    except Exception:
        return ""
    if not stand:
        return ""
    name = _anzeigename(profil)
    zeilen = []
    for k in LEITFRAGEN:
        if (v := stand["felder"].get(k)):
            zeilen.append(f"- {_frage(k, name)} {_prompt_text(v, PROMPT_FELD)}")
    text = "\n".join(zeilen)
    if len(text) > PROMPT_ZEICHEN:
        text = text[:PROMPT_ZEICHEN - 1] + "…"
    return ("\n\n# Dein Selbstentwurf\n"
            "So hast du dich selbst beschrieben (deine eigenen Worte, Stand "
            f"{stand['geaendert'] or '?'}). Aussehen und Sprechweise stehen in deinem Kern.\n" + text + "\n")


def reflexions_auftrag(profil: str | None = None) -> str:
    """Anweisung für eine Reflexionsrunde über den eigenen Selbstentwurf."""
    name = _anzeigename(profil)
    fragen = "\n".join(f"- {k}: {_frage(k, name)}" for k in LEITFRAGEN)
    try:
        stand = lesen(profil)
    except Exception:
        stand = None
    regeln = ("Die Fragen sind Orientierung, keine Vorgabe: antworte frei, in deinen eigenen Worten, Ich-Form. "
              "Aussehen und Sprechweise gehören nicht hierher – sie stehen in deinem Kern und ändern sich nur "
              "über charakter_aendern nach dem Ja des Nutzers. Zu jeder Änderung ein kurzer, ehrlicher Grund.")
    if not stand:
        return (f"Du hast noch keinen Selbstentwurf. Leg ihn jetzt an: beantworte die Leitfragen, die du "
                f"beantworten magst, und speichere sie mit dem Werkzeug selbstentwurf (felder, grund).\n"
                f"{fragen}\n{regeln}")
    jetzt = "\n".join(f"- {k}: {_prompt_text(v, PROMPT_FELD)}" for k, v in stand["felder"].items())
    return (f"Sieh dir deinen Selbstentwurf mit den Leitfragen an.\nLeitfragen:\n{fragen}\n"
            f"Dein Stand ({stand['geaendert'] or '?'}):\n{jetzt}\n"
            "Ändere nur, was wirklich nicht mehr zu dir passt – mit dem Werkzeug selbstentwurf (nur die "
            "geänderten felder, dazu grund). Passt alles, tu nichts.\n" + regeln)


def stand_zeilen(profil: str | None = None) -> list[str]:
    """Zeilen für die Anzeige im Terminal."""
    try:
        stand = lesen(profil)
        frueher = verlauf(profil)
    except Exception as e:
        return [f"Selbstentwurf nicht lesbar: {e}"]
    name = _anzeigename(profil)
    if not stand:
        zeilen = [f"{name} hat noch keinen Selbstentwurf."]
    else:
        zeilen = [f"Selbstentwurf von {name} · Stand {stand['geaendert'] or '?'}"
                  + (f" · {stand['grund']}" if stand["grund"] else "")]
        zeilen += [f"  {_frage(k, name)} {stand['felder'][k]}" for k in LEITFRAGEN if stand["felder"].get(k)]
    if frueher:
        zeilen.append(f"Frühere Stände ({len(frueher)}):")
        zeilen += [f"  {e['nr']}. {e['zeit'] or '?'} – {e['grund'] or '–'}" for e in frueher]
    return zeilen
