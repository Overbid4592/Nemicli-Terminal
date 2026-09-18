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

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

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
# PowerShell-Skripte bauen (reine Text-Funktionen, testbar ohne Windows)
# ---------------------------------------------------------------------------

def _ps_str(s: str) -> str:
    """Einfach gequoteter PowerShell-String – ' wird verdoppelt."""
    return "'" + str(s).replace("'", "''") + "'"


def _trigger_ps(w: dict) -> str:
    art = w.get("art")
    if art == "intervall":
        return (f"New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
                f"-RepetitionInterval (New-TimeSpan -Minutes {int(w['minuten'])})")
    if art == "anmeldung":
        return "New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME"
    if art == "taeglich":
        return f"New-ScheduledTaskTrigger -Daily -At {_ps_str(w['zeit'])}"
    if art == "woechentlich":
        tage = ",".join(w["tage"])
        return f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek {tage} -At {_ps_str(w['zeit'])}"
    if art == "einmal":
        return f"New-ScheduledTaskTrigger -Once -At {_ps_str(w['datum'] + ' ' + w['zeit'])}"
    raise ValueError(f"Unbekannte Zeitart: {art}")


def programm() -> tuple[str, str]:
    """(Programm, Argumente) für die Aufgabe: NemiCLI ohne Fenster.

    pythonw.exe öffnet keine Konsole. Fehlt es (fremde Installation), nimmt
    Windows python.exe – dann blitzt ein Fenster auf, läuft aber."""
    if _FROZEN:
        return str(Path(sys.executable)), "--auftrag"
    for kandidat in (_INSTALL / "venv" / "Scripts" / "pythonw.exe",
                     _INSTALL / "venv" / "Scripts" / "python.exe"):
        if kandidat.exists():
            return str(kandidat), f'"{_INSTALL / "main.py"}" --auftrag'
    return sys.executable, f'"{_INSTALL / "main.py"}" --auftrag'


def anlegen_ps(name: str, w: dict, beschreibung: str) -> str:
    exe, args = programm()
    return (
        f"$a = New-ScheduledTaskAction -Execute {_ps_str(exe)} "
        f"-Argument {_ps_str(args + ' ' + _win_arg(name))} "
        f"-WorkingDirectory {_ps_str(str(_ROOT))}\n"
        f"$t = {_trigger_ps(w)}\n"
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew\n"
        f"Register-ScheduledTask -TaskName {_ps_str(name)} -TaskPath {_ps_str(TASK_PFAD)} "
        f"-Action $a -Trigger $t -Settings $s -Description {_ps_str(beschreibung)} "
        "-Force | Out-Null\n"
        "'ok'"
    )


def loeschen_ps(name: str) -> str:
    return (f"Unregister-ScheduledTask -TaskName {_ps_str(name)} -TaskPath {_ps_str(TASK_PFAD)} "
            "-Confirm:$false\n'ok'")


def liste_ps() -> str:
    return (
        f"Get-ScheduledTask -TaskPath {_ps_str(TASK_PFAD)} -ErrorAction SilentlyContinue | "
        "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{ name=$_.TaskName; status=[string]$_.State; "
        "letzter=[string]$i.LastRunTime; ergebnis=$i.LastTaskResult; "
        "naechster=[string]$i.NextRunTime } } | ConvertTo-Json -Compress"
    )


def _win_arg(name: str) -> str:
    """Der Name als Argument – in Anführungszeichen, weil Leerzeichen drin sein dürfen."""
    return f'"{name}"'


def _powershell(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    wrapped = ("$ProgressPreference='SilentlyContinue'; "
               "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8;\n"
               "$ErrorActionPreference='Stop';\ntry {\n" + script +
               "\n} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }")
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


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
    r = _powershell(anlegen_ps(n, w, f"NemiCLI-Auftrag: {kurz}"))
    if r.returncode != 0 or "ok" not in (r.stdout or ""):
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
    r = _powershell(loeschen_ps(n))
    _datei(n).unlink(missing_ok=True)
    if r.returncode != 0 and not bekannt:
        raise ValueError(f"Keine NemiCLI-Aufgabe namens '{n}' gefunden.")
    return f"Aufgabe '{n}' gelöscht." + ("" if r.returncode == 0 else
                                        " (In Windows war sie schon weg – nur der Auftrag lag noch hier.)")


def liste() -> str:
    """Alle NemiCLI-Aufgaben mit Zeit, letztem Lauf und Auftrag – lesbar."""
    auftraege = {a["name"]: a for a in alle_auftraege()}
    r = _powershell(liste_ps())
    win: dict[str, dict] = {}
    try:
        roh = json.loads(r.stdout.strip()) if r.stdout.strip() else []
        for d in (roh if isinstance(roh, list) else [roh]):
            win[d.get("name", "")] = d
    except Exception:
        pass
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
