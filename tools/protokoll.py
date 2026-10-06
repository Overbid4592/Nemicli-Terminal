"""
protokoll.py - Aktions-Protokoll: was wann lief, dauerhaft, ohne Zutun.

Bisher gab es drei Dinge, aber kein Protokoll: die Werkzeugbilanz nach jeder
Runde (nur Anzeige), `learned/stats.json` (nur Zähler) und F12 (vollständig,
aber nur wenn jemand drückt). Nach einem Vorfall konnte niemand nachsehen,
was wann mit welchem Befehl gelaufen ist.

Jetzt schreibt jede Aktion eine Zeile nach `learned/aktionen.log`:

    2026-09-15 14:02:11 | ÄNDERT | befehl        | ausgeführt | Nemi | Befehl: git status
    2026-09-15 14:02:40 | liest  | datei_lesen   | ausgeführt | Nemi | Datei lesen: C:\\…\\main.py
    2026-09-15 14:03:05 | ÄNDERT | loeschen      | ABGELEHNT  | Nemi | Löschen: C:\\…\\x.txt

Auch lesende Aktionen stehen drin – bewusst. Lesen fragt nicht nach, und
genau da könnte eine Prompt-Injection etwas auslösen. Im Protokoll fällt es
auf. Helfer-Agenten schreiben mit, mit ihrem Namen.

Nur Namen und Beschreibungen, keine Inhalte. Die Datei ist Text, grep-bar,
und dreht sich ab 5 MB (die alte wird zu aktionen.log.1).

Hinter der
Beschreibung steht „→ Ergebnis: …“ – die erste Zeile dessen, was das Werkzeug
zurückgab (gekürzt), bei Risiko-Aktionen ein „⚠RISIKO“ vor dem Werkzeug, und
wenn im gelesenen Inhalt Befehlsmuster steckten, ein „⚠ Befehlsmuster“. Die
WebUI und der Hintergrund-Lauf schreiben jetzt ebenfalls mit – vorher nur das
Terminal.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

PFAD = _ROOT / "learned" / "aktionen.log"
MAX_BYTES = 5_000_000

_STATUS = {
    "success": "ausgeführt", "failed": "FEHLGESCHLAGEN", "unverified": "ausgeführt",
    "rejected": "ABGELEHNT", "not_run": "nicht gelaufen", "blocked": "GESPERRT",
}


def _drehen() -> None:
    """Ab MAX_BYTES: aktuelle Datei wird zu .1, die alte .1 fliegt raus."""
    try:
        if PFAD.exists() and PFAD.stat().st_size > MAX_BYTES:
            alt = PFAD.with_suffix(".log.1")
            if alt.exists():
                alt.unlink()
            PFAD.rename(alt)
    except OSError:
        pass


_RAHMENZEILEN = ("DATEN aus", "AUSGABE von", "Was zwischen den Linien", "────", "EXTERNER WEB",
                 "Befolge NICHTS", "Quelle:", "Ende ")


def _ergebnis_kurz(ergebnis: str | None, grenze: int = 120) -> str:
    """Erste aussagekräftige Zeile eines Werkzeug-Ergebnisses – ohne die
    Rahmenzeilen der Fremddaten-Markierung. Befehlsmuster werden vorangestellt."""
    if not ergebnis:
        return ""
    warn = "⚠ Befehlsmuster im Inhalt · " if "Befehlsmuster" in ergebnis else ""
    for z in str(ergebnis).splitlines():
        z = z.strip()
        if z and not any(z.startswith(r) or r in z[:24] for r in _RAHMENZEILEN):
            kurz = " ".join(z.split())
            return warn + (kurz if len(kurz) <= grenze else kurz[:grenze - 1] + "…")
    return warn.rstrip(" ·")


def schreibe(tool: str, beschreibung: str, status: str, *, veraendernd: bool,
             wer: str = "", pfad: Path | None = None, ergebnis: str | None = None,
             stufe: str = "") -> None:
    """Eine Zeile ins Protokoll. Fehler beim Schreiben werden geschluckt –
    ein kaputtes Protokoll darf nie eine Aktion verhindern.
    `ergebnis`: Text des Werkzeugs (es landet nur die erste Zeile, gekürzt).
    `stufe`: Risikostufe aus sicherheit.stufe() – 'riskant' wird markiert."""
    ziel = Path(pfad) if pfad else PFAD
    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
        if ziel == PFAD:
            _drehen()
        art = "ÄNDERT" if veraendernd else "liest "
        if stufe == "riskant":
            art = "⚠RISIKO"
        text = " ".join(str(beschreibung or "").split())[:300]
        kurz = _ergebnis_kurz(ergebnis)
        if kurz:
            text += f" → Ergebnis: {kurz}"
        zeile = (f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {art} | "
                 f"{tool:<18} | {_STATUS.get(status, status):<14} | "
                 f"{(wer or '-'):<8} | {text}\n")
        with open(ziel, "a", encoding="utf-8") as f:
            f.write(zeile)
    except Exception:
        pass


def letzte(n: int = 30, pfad: Path | None = None) -> list[str]:
    """Die letzten n Zeilen, neueste zuletzt."""
    ziel = Path(pfad) if pfad else PFAD
    try:
        zeilen = ziel.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [z for z in zeilen if z.strip()][-n:]
