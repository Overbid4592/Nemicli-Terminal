"""
zeitplan.py - NemiCLI trägt sich selbst in die Windows-Aufgabenplanung ein.

Die Persönlichkeit legt über die Aktion `zeitplan` einen Auftrag an ("prüf
jeden Morgen um 9 das System und schreib einen Bericht"). Windows startet
NemiCLI dann zur gewünschten Zeit OHNE Oberfläche (`main.py --auftrag <name>`),
die Persönlichkeit arbeitet den Auftrag mit ihren Lese-Werkzeugen ab und die
Antwort landet als Bericht in `Berichte/` im Daten-Ordner.

Was hier NICHT fest steht: was geprüft wird. Der Auftrag ist freier Text – die
Persönlichkeit entscheidet mit dem Nutzer, was sie sich ansieht.

Aufbau:
  Zeitplan/<name>.json   der Auftrag (Text, Zeitangabe, wann angelegt)
  Berichte/<name>_<datum>.md   was beim Lauf herauskam
  Aufgabenplanung: Ordner \\NemiCLI\\  – nur dort legen wir an, nur dort
  löschen wir. Fremde Windows-Aufgaben fasst dieses Modul nie an.

Die Aufgabe läuft als der angemeldete Nutzer (interaktiv), damit die
verschlüsselten API-Keys (DPAPI) lesbar bleiben. Damit dabei kein Fenster
aufgeht, startet sie über `pythonw.exe`; die Ausgabe geht in eine Log-Datei.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT, INSTALL as _INSTALL, FROZEN as _FROZEN
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = _INSTALL = Path(__file__).resolve().parent.parent
    _FROZEN = False

ORDNER = _ROOT / "Zeitplan"          # die Aufträge
BERICHTE = _ROOT / "Berichte"        # was dabei herauskam
TASK_PFAD = "\\NemiCLI\\"            # Ordner in der Aufgabenplanung
MAX_AUFTRAG = 4000                   # Zeichen – ein Auftrag ist eine Anweisung, kein Roman

_WOCHENTAGE = {
    "montag": "Monday", "mo": "Monday",
    "dienstag": "Tuesday", "di": "Tuesday",
    "mittwoch": "Wednesday", "mi": "Wednesday",
    "donnerstag": "Thursday", "do": "Thursday",
    "freitag": "Friday", "fr": "Friday",
    "samstag": "Saturday", "sa": "Saturday",
    "sonntag": "Sunday", "so": "Sunday",
}

WANN_HILFE = (
    "So kannst du 'wann' schreiben:\n"
    "  täglich 09:00            jeden Tag um 9 Uhr\n"
    "  alle 2 stunden           ab jetzt, alle 2 Stunden (auch: alle 30 minuten)\n"
    "  wöchentlich montag 08:30 jeden Montag (auch: mo,mi,fr 18:00)\n"
    "  anmeldung                bei jeder Anmeldung des Nutzers\n"
    "  einmal 2026-09-20 14:00  ein einziges Mal (auch: 20.09.2026 14:00)"
)


# ---------------------------------------------------------------------------
# Name und Auftrag prüfen
# ---------------------------------------------------------------------------

def sauberer_name(name: str) -> str | None:
    """Nur Buchstaben, Ziffern, Leerzeichen, - und _. Sonst None.

    Der Name wird Dateiname UND Aufgaben-Name – deshalb streng."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n or len(n) > 60:
        return None
    if not re.fullmatch(r"[A-Za-z0-9ÄÖÜäöüß _-]+", n):
        return None
    return n


def _uhrzeit(text: str) -> str | None:
    """'9', '9:00', '09.30', '9 uhr' -> 'HH:MM'."""
    m = re.search(r"(?<!\d)(\d{1,2})(?:[:.](\d{2}))?\s*(?:uhr)?(?!\d)", text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def parse_wann(text: str) -> dict:
    """Freie deutsche Zeitangabe -> {"art": …, …}. ValueError mit Hilfe bei Unsinn."""
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    t = t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    if not t:
        raise ValueError("Es fehlt die Angabe 'wann'.\n" + WANN_HILFE)

    # alle N minuten/stunden
    m = re.fullmatch(r"alle (\d{1,3}) ?(min(ute)?n?|std|stunden?|h)", t)
    if m:
        n = int(m.group(1))
        einheit = m.group(2)
        minuten = n * 60 if einheit.startswith(("std", "stunde", "h")) else n
        if minuten < 5:
            raise ValueError("Kürzer als alle 5 Minuten geht nicht – das wäre Dauerlast.")
        if minuten > 31 * 24 * 60:
            raise ValueError("Länger als 31 Tage Abstand geht nicht. Nimm 'wöchentlich' oder 'einmal'.")
        return {"art": "intervall", "minuten": minuten}

    # anmeldung
    if re.fullmatch(r"(bei(m)? )?(der )?anmeld(ung|en)|login|logon|(beim )?start", t):
        return {"art": "anmeldung"}

    # täglich HH:MM
    m = re.fullmatch(r"(taeglich|jeden tag|jeden morgen|jeden abend)( um)? (.+)", t)
    if m:
        zeit = _uhrzeit(m.group(3))
        if not zeit:
            raise ValueError(f"Uhrzeit nicht verstanden: '{m.group(3)}'.\n" + WANN_HILFE)
        return {"art": "taeglich", "zeit": zeit}

    # wöchentlich TAGE HH:MM
    m = re.fullmatch(r"(woechentlich|jeden|jede woche)( am)? ([a-z, ]+?)( um)? (\d.*)", t)
    if m:
        tage = []
        for w in re.split(r"[ ,]+", m.group(3).strip()):
            if w in ("und", "am"):
                continue
            eng = _WOCHENTAGE.get(w)
            if not eng:
                raise ValueError(f"Wochentag nicht verstanden: '{w}'.\n" + WANN_HILFE)
            if eng not in tage:
                tage.append(eng)
        zeit = _uhrzeit(m.group(5))
        if not tage or not zeit:
            raise ValueError("Wöchentlich braucht Wochentag UND Uhrzeit.\n" + WANN_HILFE)
        return {"art": "woechentlich", "tage": tage, "zeit": zeit}

    # einmal DATUM HH:MM
    m = re.fullmatch(r"(einmal|einmalig|am)? ?(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})( um)? (.+)", t)
    if m:
        datum = m.group(2)
        try:
            d = (datetime.strptime(datum, "%Y-%m-%d") if "-" in datum
                 else datetime.strptime(datum, "%d.%m.%Y"))
        except ValueError:
            raise ValueError(f"Datum nicht verstanden: '{datum}'.\n" + WANN_HILFE)
        zeit = _uhrzeit(m.group(4))
        if not zeit:
            raise ValueError(f"Uhrzeit nicht verstanden: '{m.group(4)}'.\n" + WANN_HILFE)
        wann = d.replace(hour=int(zeit[:2]), minute=int(zeit[3:]))
        if wann < datetime.now():
            raise ValueError(f"{wann:%d.%m.%Y %H:%M} liegt in der Vergangenheit.")
        return {"art": "einmal", "datum": wann.strftime("%Y-%m-%d"), "zeit": zeit}

    raise ValueError(f"Zeitangabe nicht verstanden: '{text}'.\n" + WANN_HILFE)


def wann_text(w: dict) -> str:
    """Die geparste Zeitangabe wieder als Satz – für Bestätigung und Liste."""
    art = w.get("art")
    if art == "intervall":
        m = int(w["minuten"])
        return f"alle {m // 60} Stunden" if m % 60 == 0 else f"alle {m} Minuten"
    if art == "anmeldung":
        return "bei jeder Anmeldung"
    if art == "taeglich":
        return f"täglich um {w['zeit']}"
    if art == "woechentlich":
        rueck = {v: k.capitalize() for k, v in _WOCHENTAGE.items() if len(k) > 2}
        return "jeden " + ", ".join(rueck.get(t, t) for t in w["tage"]) + f" um {w['zeit']}"
    if art == "einmal":
        return f"einmal am {w['datum']} um {w['zeit']}"
    return str(w)


# ---------------------------------------------------------------------------
# Aufgaben-XML bauen (reine Text-Funktionen, testbar ohne Windows)
# ---------------------------------------------------------------------------
_XMLNS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _x(s: str) -> str:
    return xml_escape(str(s), {'"': "&quot;"})


def _trigger_xml(w: dict, jetzt: datetime | None = None) -> str:
    jetzt = jetzt or datetime.now()
    art = w.get("art")
    heute = jetzt.strftime("%Y-%m-%d")
    if art == "intervall":
        start = (jetzt + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:00")
        return (f"<TimeTrigger><Repetition><Interval>PT{int(w['minuten'])}M</Interval>"
                f"<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
                f"<StartBoundary>{start}</StartBoundary><Enabled>true</Enabled></TimeTrigger>")
    if art == "anmeldung":
        konto = "\\".join(x for x in (os.environ.get("USERDOMAIN"), os.environ.get("USERNAME")) if x)
        nutzer = f"<UserId>{_x(konto)}</UserId>" if konto else ""
        return f"<LogonTrigger><Enabled>true</Enabled>{nutzer}</LogonTrigger>"
    if art == "taeglich":
        return (f"<CalendarTrigger><StartBoundary>{heute}T{w['zeit']}:00</StartBoundary>"
                "<Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval>"
                "</ScheduleByDay></CalendarTrigger>")
    if art == "woechentlich":
        tage = "".join(f"<{t}/>" for t in w["tage"])
        return (f"<CalendarTrigger><StartBoundary>{heute}T{w['zeit']}:00</StartBoundary>"
                f"<Enabled>true</Enabled><ScheduleByWeek><DaysOfWeek>{tage}</DaysOfWeek>"
                "<WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger>")
    if art == "einmal":
        return (f"<TimeTrigger><StartBoundary>{w['datum']}T{w['zeit']}:00</StartBoundary>"
                "<Enabled>true</Enabled></TimeTrigger>")
    raise ValueError(f"Unbekannte Zeitart: {art}")


def programm() -> tuple[str, str]:
    """(Programm, Argumente) für die Aufgabe: NemiCLI ohne Fenster.

    pythonw.exe öffnet keine Konsole. Fehlt es (fremde Installation), nimmt
    Windows python.exe – dann blitzt ein Fenster auf, läuft aber."""
    if _FROZEN:
        from paths import exe_ohne_fenster
        return str(exe_ohne_fenster()), "--auftrag"
    for kandidat in (_INSTALL / "venv" / "Scripts" / "pythonw.exe",
                     _INSTALL / "venv" / "Scripts" / "python.exe"):
        if kandidat.exists():
            return str(kandidat), f'"{_INSTALL / "main.py"}" --auftrag'
    return sys.executable, f'"{_INSTALL / "main.py"}" --auftrag'


def aufgabe_xml(name: str, w: dict, beschreibung: str) -> str:
    """Die Aufgabe als Task-Scheduler-XML: läuft als angemeldeter Nutzer, ohne
    erhöhte Rechte, höchstens 30 Minuten, nie doppelt."""
    exe, args = programm()
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{_XMLNS}">'
        f"<RegistrationInfo><Description>{_x(beschreibung)}</Description></RegistrationInfo>"
        f"<Triggers>{_trigger_xml(w)}</Triggers>"
        '<Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<StartWhenAvailable>true</StartWhenAvailable>"
        "<ExecutionTimeLimit>PT30M</ExecutionTimeLimit><Enabled>true</Enabled></Settings>"
        '<Actions Context="Author"><Exec>'
        f"<Command>{_x(exe)}</Command>"
        f"<Arguments>{_x(args + ' ' + _win_arg(name))}</Arguments>"
        f"<WorkingDirectory>{_x(str(_ROOT))}</WorkingDirectory>"
        "</Exec></Actions></Task>"
    )


def _win_arg(name: str) -> str:
    """Der Name als Argument – in Anführungszeichen, weil Leerzeichen drin sein dürfen."""
    return f'"{name}"'


def _schtasks(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          encoding="oem", errors="replace", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _eintragen(name: str, xml: str) -> subprocess.CompletedProcess:
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        return _schtasks("/create", "/tn", TASK_PFAD + name, "/xml", tmp, "/f")
    finally:
        Path(tmp).unlink(missing_ok=True)


def _windows_liste() -> dict[str, dict]:
    """Aufgaben im Ordner \\NemiCLI\\. Spalten nach Position (Überschriften sind
    übersetzt): 1 Name, 2 nächster Lauf, 3 Status, 5 letzter Lauf, 6 Ergebnis."""
    r = _schtasks("/query", "/tn", TASK_PFAD, "/fo", "CSV", "/v", "/nh")
    win: dict[str, dict] = {}
    if r.returncode != 0:
        return win
    for z in csv.reader(io.StringIO(r.stdout or "")):
        if len(z) < 7 or not z[1].startswith(TASK_PFAD):
            continue
        try:
            erg = int(z[6])
        except ValueError:
            erg = None
        win[z[1][len(TASK_PFAD):]] = {"status": z[3], "naechster": z[2], "letzter": z[5],
                                      "ergebnis": erg}
    return win


# ---------------------------------------------------------------------------
# Die Aufträge (Dateien)
# ---------------------------------------------------------------------------

def _datei(name: str) -> Path:
    return ORDNER / f"{name}.json"


def lade_auftrag(name: str) -> dict | None:
    n = sauberer_name(name)
    if not n:
        return None
    try:
        return json.loads(_datei(n).read_text(encoding="utf-8"))
    except Exception:
        return None


def alle_auftraege() -> list[dict]:
    out = []
    if not ORDNER.exists():
        return out
    for p in sorted(ORDNER.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("name"):
                out.append(d)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Die drei Handgriffe
# ---------------------------------------------------------------------------

def anlegen(name: str, wann: str, auftrag: str) -> str:
    """Aufgabe eintragen. Gibt einen lesbaren Satz zurück, wirft ValueError bei Unsinn."""
    n = sauberer_name(name)
    if not n:
        raise ValueError("Der Name darf nur Buchstaben, Ziffern, Leerzeichen, - und _ "
                         "enthalten (höchstens 60 Zeichen).")
    text = str(auftrag or "").strip()
    if not text:
        raise ValueError("Es fehlt der Auftrag – was soll NemiCLI zu der Zeit tun?")
    if len(text) > MAX_AUFTRAG:
        raise ValueError(f"Der Auftrag ist zu lang ({len(text)} Zeichen, höchstens {MAX_AUFTRAG}).")
    w = parse_wann(wann)

    ORDNER.mkdir(parents=True, exist_ok=True)
    BERICHTE.mkdir(parents=True, exist_ok=True)
    _datei(n).write_text(json.dumps({
        "name": n, "auftrag": text, "wann": w, "wann_text": wann_text(w),
        "angelegt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    kurz = text if len(text) <= 120 else text[:119] + "…"
    r = _eintragen(n, aufgabe_xml(n, w, f"NemiCLI-Auftrag: {kurz}"))
    if r.returncode != 0:
        _datei(n).unlink(missing_ok=True)
        raise ValueError("Windows hat die Aufgabe nicht angenommen: "
                         + (r.stderr or r.stdout or "keine Meldung").strip())
    return (f"Aufgabe '{n}' angelegt – {wann_text(w)}. Der Bericht landet danach in "
            f"{BERICHTE}. Auftrag: {kurz}")


def loeschen(name: str) -> str:
    n = sauberer_name(name)
    if not n:
        raise ValueError("Ungültiger Name.")
    bekannt = _datei(n).exists()
    r = _schtasks("/delete", "/tn", TASK_PFAD + n, "/f")
    _datei(n).unlink(missing_ok=True)
    if r.returncode != 0 and not bekannt:
        raise ValueError(f"Keine NemiCLI-Aufgabe namens '{n}' gefunden.")
    return f"Aufgabe '{n}' gelöscht." + ("" if r.returncode == 0 else
                                        " (In Windows war sie schon weg – nur der Auftrag lag noch hier.)")


def liste() -> str:
    """Alle NemiCLI-Aufgaben mit Zeit, letztem Lauf und Auftrag – lesbar."""
    auftraege = {a["name"]: a for a in alle_auftraege()}
    try:
        win = _windows_liste()
    except (OSError, subprocess.TimeoutExpired):
        win = {}
    namen = sorted(set(auftraege) | set(win))
    if not namen:
        return "Keine Aufgaben eingetragen."
    zeilen = []
    for n in namen:
        a = auftraege.get(n, {})
        w = win.get(n)
        status = f"{w.get('status', '?')}" if w else "nur Auftrag, in Windows nicht (mehr) eingetragen"
        zeilen.append(f"• {n} – {a.get('wann_text', '?')} – Status: {status}")
        if w:
            erg = w.get("ergebnis")
            zeilen.append(f"    letzter Lauf: {w.get('letzter') or '–'}"
                          + (f" (Ergebnis {erg})" if erg not in (None, 0, 267011) else "")
                          + f"  ·  nächster: {w.get('naechster') or '–'}")
        if a.get("auftrag"):
            t = a["auftrag"]
            zeilen.append("    Auftrag: " + (t if len(t) <= 160 else t[:159] + "…"))
        letzter = letzter_bericht(n)
        if letzter:
            zeilen.append(f"    letzter Bericht: {letzter.name}")
    zeilen.append(f"\nAufträge: {ORDNER}\nBerichte: {BERICHTE}")
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Berichte
# ---------------------------------------------------------------------------

def bericht_pfad(name: str) -> Path:
    BERICHTE.mkdir(parents=True, exist_ok=True)
    return BERICHTE / f"{name}_{datetime.now():%Y%m%d-%H%M%S}.md"


def letzter_bericht(name: str) -> Path | None:
    if not BERICHTE.exists():
        return None
    treffer = sorted(BERICHTE.glob(f"{name}_*.md"))
    return treffer[-1] if treffer else None


_MARKER = "learned/.berichte.gesehen"


def neue_berichte() -> list[Path]:
    """Berichte, die seit dem letzten Aufruf dazugekommen sind (für den Start)."""
    marker = _ROOT / _MARKER
    try:
        seit = float(marker.read_text(encoding="utf-8").strip())
    except Exception:
        seit = 0.0
    neu = []
    if BERICHTE.exists():
        neu = sorted((p for p in BERICHTE.glob("*.md") if p.stat().st_mtime > seit),
                     key=lambda p: p.stat().st_mtime)
    # Marker = neuester Bericht (nicht "jetzt": Datei-Zeiten runden anders als
    # die Uhr, und ein Bericht darf nicht zweimal als neu gelten).
    stand = max([seit, datetime.now().timestamp()] + [p.stat().st_mtime for p in neu])
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(repr(stand), encoding="utf-8")
    except Exception:
        pass
    return neu


def bericht_kopfzeile(p: Path) -> str:
    """Die erste inhaltliche Zeile des Berichts – im Idealfall '✅ Alles OK'."""
    try:
        for zeile in p.read_text(encoding="utf-8").splitlines():
            z = zeile.strip().lstrip("#").strip()
            if z and not z.startswith(("<!--", "|", "---")):
                return z if len(z) <= 120 else z[:119] + "…"
    except Exception:
        pass
    return ""
