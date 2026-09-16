"""
mitschrift.py - F12: das ganze laufende Gespräch als Markdown sichern.

Warum es das gibt: im Terminal lässt sich ein langes Gespräch nur mühsam
markieren – und der **Denktext** steht dort ohnehin nur zusammengeklappt
(F2 zeigt immer nur die letzte Runde). F12 schreibt alles in eine Datei:
jede Frage, jeden Denktext, jede Antwort, jede ausgeführte Aktion.

Was mitläuft, wird WÄHREND des Gesprächs gesammelt – nicht hinterher aus
`backend.messages` rekonstruiert. Dort steckt der Denktext nämlich gar nicht
drin; er entsteht beim Streamen und ist danach weg. Deshalb meldet `main.py`
jede Runde hier an.

Gespeichert wird nach `Gespraeche/` neben NemiCLI:

    Gespraeche/Chat-093_20260914-1204.md

Nichts davon geht ins Netz. Die Datei liegt neben dem Programm wie Chats,
Bilder und Gedächtnis auch.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

ORDNER = _ROOT / "Gespraeche"

# Der Mitschnitt dieser Sitzung: eine Liste von Einträgen.
#   {"art": "nutzer",    "text": …, "zeit": …}
#   {"art": "assistent", "text": …, "denken": …, "sekunden": …, "zeit": …}
#   {"art": "aktion",    "werkzeug": …, "text": …, "ok": …, "zeit": …}
#   {"art": "notiz",     "text": …, "zeit": …}
_eintraege: list[dict] = []

# Woher Kopfdaten kommen (Chat-Nummer, Modell, Persönlichkeit …). main.py
# hängt das ein, damit dieses Modul nichts über main wissen muss.
_quelle = None


def setze_quelle(fn) -> None:
    """main.py meldet hier eine Funktion an, die ein dict mit Kopfdaten liefert."""
    global _quelle
    _quelle = fn


def _kopfdaten() -> dict:
    if _quelle is None:
        return {}
    try:
        return _quelle() or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Mitschreiben
# ---------------------------------------------------------------------------

def _jetzt() -> str:
    return datetime.now().strftime("%H:%M:%S")


def leeren() -> None:
    """Neuer Chat (/reset, /resume): Mitschnitt von vorn."""
    _eintraege.clear()


def nutzer(text: str) -> None:
    if text and text.strip():
        _eintraege.append({"art": "nutzer", "text": text, "zeit": _jetzt()})


def assistent(text: str, denken: str = "", sekunden: float | None = None) -> None:
    """Eine Antwort-Ausgabe – mit dem Denktext, der zu ihr gehört."""
    if not (text and text.strip()) and not (denken and denken.strip()):
        return
    _eintraege.append({"art": "assistent", "text": text or "",
                       "denken": denken or "", "sekunden": sekunden,
                       "zeit": _jetzt()})


def aktion(werkzeug: str, beschreibung: str, ergebnis: str = "",
           ok: bool | None = None) -> None:
    _eintraege.append({"art": "aktion", "werkzeug": werkzeug,
                       "text": beschreibung, "ergebnis": ergebnis,
                       "ok": ok, "zeit": _jetzt()})


def notiz(text: str) -> None:
    """Etwas, das keine Antwort ist (Abbruch, Hinweis, Moduswechsel)."""
    if text and text.strip():
        _eintraege.append({"art": "notiz", "text": text, "zeit": _jetzt()})


def anzahl() -> int:
    return len(_eintraege)


def leer() -> bool:
    return not _eintraege


# ---------------------------------------------------------------------------
# Markdown bauen
# ---------------------------------------------------------------------------

def _zaun(text: str) -> str:
    """Text so einbetten, dass eigene ```-Zäune die Datei nicht zerreißen."""
    t = str(text).rstrip()
    zaun = "```"
    while zaun in t:
        zaun += "`"
    return f"{zaun}\n{t}\n{zaun}"


def als_markdown(kopf: dict | None = None) -> str:
    """Der ganze Mitschnitt als Markdown."""
    k = dict(_kopfdaten())
    k.update(kopf or {})
    now = datetime.now()

    L: list[str] = []
    a = L.append

    nummer = k.get("chat")
    a(f"# Gespräch {('Chat ' + str(nummer)) if nummer else ''}".rstrip())
    a("")
    a(f"- **Gesichert:** {now.strftime('%d.%m.%Y, %H:%M:%S Uhr')}")
    if k.get("persona"):
        a(f"- **Persönlichkeit:** {k['persona']}")
    if k.get("model"):
        a(f"- **Modell:** {k['model']}")
    if k.get("modus"):
        a(f"- **Arbeitsmodus:** {k['modus']}")
    if k.get("ordner"):
        a(f"- **Arbeitsordner:** `{k['ordner']}`")
    if nummer:
        a(f"- **Fortsetzen mit:** `/resume {nummer}`")
    a(f"- **Einträge:** {len(_eintraege)}")
    a("")
    a("> Vollständiger Mitschnitt dieser Sitzung: jede Frage, jeder Denktext,")
    a("> jede Antwort, jede ausgeführte Aktion. Denktexte stehen sonst nirgends –")
    a("> im Terminal zeigt F2 immer nur die letzte Runde.")
    a("")
    a("---")
    a("")

    runde = 0
    for e in _eintraege:
        art = e["art"]
        if art == "nutzer":
            runde += 1
            a(f"## {runde}. Du · {e['zeit']}")
            a("")
            a(e["text"].rstrip())
            a("")
        elif art == "assistent":
            name = k.get("persona") or "Antwort"
            dauer = (f" · {e['sekunden']:.1f} s gedacht"
                     if e.get("sekunden") else "")
            a(f"### {name} · {e['zeit']}{dauer}")
            a("")
            if e.get("denken", "").strip():
                a("<details><summary>💭 Denktext</summary>")
                a("")
                a(_zaun(e["denken"]))
                a("")
                a("</details>")
                a("")
            if e.get("text", "").strip():
                a(e["text"].rstrip())
                a("")
        elif art == "aktion":
            zeichen = "✓" if e.get("ok") else ("✗" if e.get("ok") is False else "·")
            a(f"> {zeichen} **Aktion** `{e['werkzeug']}` · {e['zeit']}  ")
            if e.get("text"):
                a(f"> {e['text']}")
            if e.get("ergebnis", "").strip():
                a("")
                a(_zaun(e["ergebnis"]))
            a("")
        elif art == "notiz":
            a(f"_{e['text'].strip()}_ · {e['zeit']}")
            a("")

    a("---")
    a("")
    a("_Gesichert mit F12 aus NemiCLI._")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Speichern
# ---------------------------------------------------------------------------

def _sicherer_name(text: str) -> str:
    """Aus einer Überschrift einen Dateinamen machen, der unter Windows geht."""
    t = re.sub(r"[^\w\- ]+", "", str(text), flags=re.UNICODE).strip()
    t = re.sub(r"\s+", "-", t)
    return t[:40].strip("-")


def dateiname(kopf: dict | None = None) -> str:
    k = dict(_kopfdaten())
    k.update(kopf or {})
    stempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    nummer = k.get("chat")
    teil = f"Chat-{int(nummer):03d}_" if nummer else ""
    return f"{teil}{stempel}.md"


def speichern(kopf: dict | None = None, ordner: Path | None = None) -> Path:
    """Schreibt den Mitschnitt. Gibt den Pfad zurück.

    Wirft ValueError, wenn es nichts zu sichern gibt – dann soll die
    Oberfläche das sagen und keine leere Datei anlegen."""
    if not _eintraege:
        raise ValueError("Noch nichts zu sichern – das Gespräch ist leer.")
    ziel_ordner = Path(ordner) if ordner else ORDNER
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    ziel = ziel_ordner / dateiname(kopf)
    ziel.write_text(als_markdown(kopf), encoding="utf-8")
    return ziel
