"""ablage.py – welche Datei zu welcher App gehört, Laden/Speichern je Format und die Liste „zuletzt benutzt“.
Bilder (.png .jpg .webp .bmp) öffnet 🎨 Malen; geladen und gespeichert werden sie im Fenster (Qt).

Textdokumente: .md .txt .html werden direkt angezeigt; .docx .odt .rtf gehen über das Textmodell;
.pdf wird nur als Text übernommen. Tabellen: .ods .xlsx .csv .tsv und Markdown-Tabellen.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from buero import docx, odt, rtf, tabformate
from buero.malen import BILD_OEFFNEN, BILD_SPEICHERN  # noqa: F401 – auch für die Oberfläche
from buero.sicher import MAX_DATEI, FormatFehler, datei_lesen
from buero.tabelle import Mappe

TEXT_OEFFNEN = {".md": "Markdown", ".markdown": "Markdown", ".txt": "Text", ".log": "Text", ".html": "HTML",
                ".htm": "HTML", ".docx": "Word", ".odt": "OpenDocument-Text", ".rtf": "RTF", ".pdf": "PDF (nur Text)"}
TEXT_SPEICHERN = {".md": "Markdown", ".odt": "OpenDocument-Text", ".docx": "Word", ".txt": "Text", ".html": "HTML",
                  ".rtf": "RTF", ".pdf": "PDF"}
TABELLE_OEFFNEN = {".ods": "OpenDocument-Tabelle", ".xlsx": "Excel", ".xlsm": "Excel (Makros werden nicht ausgeführt)",
                   ".csv": "CSV", ".tsv": "TSV", ".md": "Markdown-Tabelle"}
TABELLE_SPEICHERN = {".ods": "OpenDocument-Tabelle", ".xlsx": "Excel", ".csv": "CSV (;)", ".tsv": "TSV",
                     ".md": "Markdown-Tabelle", ".html": "HTML", ".pdf": "PDF"}
MAX_ZULETZT = 30
ZULETZT_DATEI = "zuletzt.json"


def app_fuer(pfad: str | Path) -> str | None:
    """"schreiben", "tabelle" oder "malen" nach der Endung (Markdown öffnet im Schreiben)."""
    end = Path(pfad).suffix.lower()
    if end in TEXT_OEFFNEN:
        return "schreiben"
    if end in TABELLE_OEFFNEN:
        return "tabelle"
    if end in BILD_OEFFNEN:
        return "malen"
    return None


def filter_text(formate: dict[str, str], alle: bool = True) -> str:
    """Filterzeile für den Datei-Dialog."""
    gruppen: dict[str, list[str]] = {}
    for end, name in formate.items():
        gruppen.setdefault(name, []).append(f"*{end}")
    zeilen = [f"{name} ({' '.join(e)})" for name, e in gruppen.items()]
    if alle:
        zeilen.insert(0, "Alle unterstützten (" + " ".join(f"*{e}" for e in formate) + ")")
    return ";;".join(zeilen)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _text_aus_bytes(roh: bytes) -> str:
    return tabformate._decodieren(roh)


def text_laden(pfad: str | Path) -> tuple[str, object]:
    """("markdown" | "text" | "html" | "modell", Inhalt). Inhalt ist Text oder ein textmodell.Dokument."""
    p = Path(pfad)
    end = p.suffix.lower()
    if end in (".md", ".markdown"):
        return "markdown", _text_aus_bytes(datei_lesen(p))
    if end in (".txt", ".log"):
        return "text", _text_aus_bytes(datei_lesen(p))
    if end in (".html", ".htm"):
        return "html", _text_aus_bytes(datei_lesen(p))
    if end == ".docx":
        return "modell", docx.lesen(p)
    if end == ".odt":
        return "modell", odt.lesen(p)
    if end == ".rtf":
        return "modell", rtf.lesen(p)
    if end == ".pdf":
        import anhang
        if p.stat().st_size > MAX_DATEI:
            raise FormatFehler(f"{p.name} ist zu groß")
        text, _, _ = anhang.lese_pdf(p, grenze=2_000_000)
        return "text", text
    raise FormatFehler(f"Dieses Format kann Schreiben nicht öffnen: {end or '(ohne Endung)'}")


def text_speichern(pfad: str | Path, art: str, inhalt) -> None:
    """art "markdown" | "text" | "html": inhalt ist Text; art "docx" | "odt" | "rtf": inhalt ist ein Dokument;
    art "bytes": fertige Bytes (z. B. PDF aus der Oberfläche)."""
    import atomar
    p = Path(pfad)
    if art in ("markdown", "text", "html"):
        atomar.text_schreiben(p, inhalt)
    elif art == "docx":
        atomar.bytes_schreiben(p, docx.schreiben_bytes(inhalt))
    elif art == "odt":
        atomar.bytes_schreiben(p, odt.schreiben_bytes(inhalt))
    elif art == "rtf":
        atomar.bytes_schreiben(p, rtf.nach_bytes(inhalt))
    elif art == "bytes":
        atomar.bytes_schreiben(p, inhalt)
    else:
        raise FormatFehler(f"Unbekannte Speicherart: {art}")


# ---------------------------------------------------------------------------
# Tabellen
# ---------------------------------------------------------------------------

def tabelle_laden(pfad: str | Path) -> Mappe:
    p = Path(pfad)
    end = p.suffix.lower()
    if end == ".ods":
        return tabformate.ods_lesen(p)
    if end in (".xlsx", ".xlsm"):
        return tabformate.xlsx_lesen(p)
    if end == ".csv":
        m = tabformate.csv_lesen(p)
    elif end == ".tsv":
        m = tabformate.csv_lesen(p, "\t")
    elif end in (".md", ".markdown"):
        m = tabformate.markdown_lesen(p)
    else:
        raise FormatFehler(f"Dieses Format kann Tabelle nicht öffnen: {end or '(ohne Endung)'}")
    m.berechnen()
    return m


def tabelle_speichern(pfad: str | Path, m: Mappe, blatt_nr: int = 0) -> None:
    """Speichern nach Endung; CSV/TSV/Markdown nehmen das gewählte Blatt (eine Datei = ein Blatt)."""
    import atomar
    p = Path(pfad)
    end = p.suffix.lower()
    blatt = m.blaetter[max(0, min(blatt_nr, len(m.blaetter) - 1))]
    m.berechnen()
    if end == ".ods":
        atomar.bytes_schreiben(p, tabformate.ods_bytes(m))
    elif end == ".xlsx":
        atomar.bytes_schreiben(p, tabformate.xlsx_bytes(m))
    elif end == ".csv":
        atomar.bytes_schreiben(p, tabformate.csv_bytes(m, blatt, ";"))
    elif end == ".tsv":
        atomar.bytes_schreiben(p, tabformate.csv_bytes(m, blatt, "\t"))
    elif end in (".md", ".markdown"):
        atomar.text_schreiben(p, tabformate.markdown_text(m, blatt))
    elif end in (".html", ".htm"):
        atomar.text_schreiben(p, tabformate.html_text(m))
    else:
        raise FormatFehler(f"Als {end or '(ohne Endung)'} kann Tabelle nicht speichern")


# ---------------------------------------------------------------------------
# Zuletzt benutzt
# ---------------------------------------------------------------------------

MAX_VORSCHAU = 600


def _vorschau_sauber(v):
    """Vorschau: Text (gekürzt) oder Tabelle als Liste von Zeilen (höchstens 8 × 5 kurze Zellen)."""
    if isinstance(v, str):
        return v[:MAX_VORSCHAU]
    if isinstance(v, list):
        return [[str(c)[:24] for c in z[:5]] for z in v[:8] if isinstance(z, list)]
    return ""


def zuletzt(ordner: str | Path) -> list[dict]:
    """Einträge {pfad, app, zeit, vorschau} – nur noch vorhandene Dateien, neueste zuerst."""
    import atomar
    daten = atomar.json_lesen(Path(ordner) / ZULETZT_DATEI, [], list)
    raus = []
    for e in daten:
        if not isinstance(e, dict) or not isinstance(e.get("pfad"), str):
            continue
        p = Path(e["pfad"])
        try:
            da = p.is_file()
        except OSError:
            da = False
        if da and app_fuer(p):
            try:
                zeit = float(e.get("zeit") or 0)
            except (TypeError, ValueError):
                zeit = 0.0
            raus.append({"pfad": str(p), "app": _app_eintrag(p, e.get("app")), "zeit": zeit,
                         "vorschau": _vorschau_sauber(e.get("vorschau"))})
    return raus[:MAX_ZULETZT]


def _app_eintrag(p: Path, gemerkt) -> str | None:
    """Gemerkte App, wenn sie das Format öffnen kann (eine als .md gespeicherte Tabelle bleibt Tabelle)."""
    end = p.suffix.lower()
    if gemerkt == "tabelle" and end in TABELLE_OEFFNEN:
        return "tabelle"
    if gemerkt == "schreiben" and end in TEXT_OEFFNEN:
        return "schreiben"
    return app_fuer(p)


def merken(ordner: str | Path, pfad: str | Path, vorschau=None, app: str | None = None) -> None:
    import atomar
    o = Path(ordner)
    o.mkdir(parents=True, exist_ok=True)
    p = str(Path(pfad).resolve())
    alt = [e for e in atomar.json_lesen(o / ZULETZT_DATEI, [], list)
           if isinstance(e, dict) and isinstance(e.get("pfad"), str) and e["pfad"].lower() != p.lower()]
    eintrag = {"pfad": p, "zeit": time.time(), "vorschau": _vorschau_sauber(vorschau)}
    if app in ("schreiben", "tabelle", "malen"):
        eintrag["app"] = app
    neu = [eintrag] + alt[: MAX_ZULETZT - 1]
    atomar.text_schreiben(o / ZULETZT_DATEI, json.dumps(neu, ensure_ascii=False, indent=1))


def vergessen(ordner: str | Path, pfad: str | Path) -> None:
    import atomar
    o = Path(ordner)
    p = str(Path(pfad).resolve()).lower()
    alt = [e for e in atomar.json_lesen(o / ZULETZT_DATEI, [], list)
           if isinstance(e, dict) and isinstance(e.get("pfad"), str) and e["pfad"].lower() != p]
    atomar.text_schreiben(o / ZULETZT_DATEI, json.dumps(alt, ensure_ascii=False, indent=1))


def neuer_pfad(ordner: str | Path, basis: str, endung: str) -> Path:
    o = Path(ordner)
    p, n = o / f"{basis}{endung}", 2
    while p.exists():
        p = o / f"{basis} {n}{endung}"
        n += 1
    return p
