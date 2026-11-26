"""Auto-Vollscan: Vollscan nach dem Zeitplan des Nutzers (/wache termin).

Der Plan – Wochentage, Uhrzeit, „bei Systemstart“ – entsteht nur im Termin-Fenster (ui/termin_fenster.py)
und liegt im Schlüsselordner von NemiCLI (%LOCALAPPDATA%\\NemiCLI), den die Werkzeuge und Befehle der KI
nicht erreichen. Die laufende Wache prüft ihn alle PRUEFEN_S Sekunden.

- Termin: fällig ist der jüngste geplante Zeitpunkt, der noch nicht lief. War der Rechner zur Uhrzeit aus,
  läuft der Scan beim nächsten Mal nach (nur dieser eine, nicht jeder verpasste). Zeitpunkte vor dem
  Speichern des Plans zählen nicht.
- Systemstart: einmal je Hochfahren, wenn die Wache in den ersten SYSTEMSTART_S Sekunden danach läuft.
- Unabhängig vom Start von Hand (/wache fullscan geht immer); keine Grenze – wie oft, bestimmt der Nutzer.
  Ein angehaltener Scan wird fortgesetzt.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

TAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
PLAN_NAME = "vollscan_plan.json"
STAND_NAME = "vollscan_plan_stand.json"
PRUEFEN_S = 30
ERSTE_PRUEFUNG_S = 60            # nach dem Start der Wache: erst die Sensoren hochkommen lassen
SYSTEMSTART_S = 30 * 60
STANDARD_UHRZEIT = "03:00"


def ordner() -> Path:
    """Schlüsselordner von NemiCLI (in Tests der Ersatzordner der Testdaten)."""
    import eigenbereich
    return eigenbereich.schluessel_datei().parent


def _lesen(name: str) -> dict:
    try:
        d = json.loads((ordner() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _schreiben(name: str, daten: dict) -> None:
    pfad = ordner() / name
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, pfad)


def _uhrzeit(text) -> str:
    try:
        h, m = (int(x) for x in str(text).split(":", 1))
    except (TypeError, ValueError):
        return STANDARD_UHRZEIT
    return f"{h:02d}:{m:02d}" if 0 <= h < 24 and 0 <= m < 60 else STANDARD_UHRZEIT


def plan_normal(roh: dict) -> dict:
    tage = sorted({int(t) for t in (roh.get("tage") or []) if str(t).lstrip("-").isdigit() and 0 <= int(t) < 7})
    return {"tage": tage, "uhrzeit": _uhrzeit(roh.get("uhrzeit")), "systemstart": bool(roh.get("systemstart")),
            "geaendert": float(roh.get("geaendert") or 0)}


def plan_lesen() -> dict:
    return plan_normal(_lesen(PLAN_NAME))


def plan_speichern(tage, uhrzeit: str, systemstart: bool) -> dict:
    plan = plan_normal({"tage": list(tage), "uhrzeit": uhrzeit, "systemstart": systemstart,
                        "geaendert": time.time()})
    _schreiben(PLAN_NAME, plan)
    return plan


def aktiv(plan: dict) -> bool:
    return bool(plan["tage"]) or plan["systemstart"]


def _zeitpunkt(tag: datetime, uhrzeit: str) -> datetime:
    h, m = (int(x) for x in uhrzeit.split(":"))
    return tag.replace(hour=h, minute=m, second=0, microsecond=0)


def letzter_termin(plan: dict, jetzt: float) -> float | None:
    """Jüngster geplanter Zeitpunkt bis `jetzt` (Wochentage + Uhrzeit) – None ohne Wochentage."""
    if not plan["tage"]:
        return None
    heute = datetime.fromtimestamp(jetzt)
    for zurueck in range(8):
        d = _zeitpunkt(heute - timedelta(days=zurueck), plan["uhrzeit"])
        if d.weekday() in plan["tage"] and d.timestamp() <= jetzt:
            return d.timestamp()
    return None


def naechster_termin(plan: dict, jetzt: float) -> float | None:
    if not plan["tage"]:
        return None
    heute = datetime.fromtimestamp(jetzt)
    for vor in range(8):
        d = _zeitpunkt(heute + timedelta(days=vor), plan["uhrzeit"])
        if d.weekday() in plan["tage"] and d.timestamp() > jetzt:
            return d.timestamp()
    return None


def _boot() -> float:
    import psutil
    return float(psutil.boot_time())


def faellig(plan: dict, stand: dict, jetzt: float, boot: float) -> tuple[str, float] | None:
    """("systemstart", Hochfahrzeit) oder ("termin", Zeitpunkt) – was jetzt laufen soll, sonst None."""
    # Systemstart: nur ein Hochfahren nach dem Speichern des Plans (sonst liefe er gleich beim Einstellen)
    if (plan["systemstart"] and 0 <= jetzt - boot <= SYSTEMSTART_S and boot >= plan["geaendert"]
            and round(stand.get("boot") or 0) != round(boot)):
        return "systemstart", boot
    t = letzter_termin(plan, jetzt)
    if t is not None and t > max(float(stand.get("termin") or 0), plan["geaendert"]):
        return "termin", t
    return None


def beschreibung(plan: dict) -> str:
    teile = []
    if plan["tage"]:
        tage = ("jeden Tag" if len(plan["tage"]) == 7 else
                "werktags" if plan["tage"] == [0, 1, 2, 3, 4] else
                "am Wochenende" if plan["tage"] == [5, 6] else ", ".join(TAGE[t] for t in plan["tage"]))
        teile.append(f"{tage} um {plan['uhrzeit']}")
    if plan["systemstart"]:
        teile.append("bei jedem Systemstart")
    return " · ".join(teile) if teile else "kein Zeitplan"


def status_text(jetzt: float | None = None) -> str:
    jetzt = time.time() if jetzt is None else jetzt
    plan = plan_lesen()
    if not aktiv(plan):
        return "Auto-Vollscan: kein Zeitplan – /wache termin stellt einen ein."
    text = f"Auto-Vollscan: {beschreibung(plan)}"
    n = naechster_termin(plan, jetzt)
    if n:
        text += f" · nächster {TAGE[datetime.fromtimestamp(n).weekday()]} " \
                f"{time.strftime('%d.%m. %H:%M', time.localtime(n))}"
    zuletzt = float(_lesen(STAND_NAME).get("gestartet") or 0)
    if zuletzt:
        text += f" · zuletzt {time.strftime('%d.%m. %H:%M', time.localtime(zuletzt))}"
    return text


def _protokoll(text: str, ergebnis: str) -> None:
    try:
        import protokoll
        protokoll.schreibe("wache_vollscan", text, "success", veraendernd=False, wer="Wache", ergebnis=ergebnis)
    except Exception:
        pass


def pruefen(jetzt: float | None = None, *, starten=None, boot: float | None = None) -> str:
    """Einmal prüfen und, wenn fällig, den Vollscan starten. Gibt die Meldung zurück (sonst "")."""
    jetzt = time.time() if jetzt is None else jetzt
    plan = plan_lesen()
    if not aktiv(plan):
        return ""
    stand = _lesen(STAND_NAME)
    grund = faellig(plan, stand, jetzt, _boot() if boot is None else boot)
    if grund is None:
        return ""
    if starten is None:
        import paths
        from . import vollscan

        def starten():
            return vollscan.starten(Path(paths.INSTALL))
    text = starten()
    art, wann = grund
    stand = {**stand, ("boot" if art == "systemstart" else "termin"): wann, "gestartet": jetzt, "meldung": text}
    if art == "systemstart":                     # ein Termin, der zugleich fällig war, ist damit erledigt
        t = letzter_termin(plan, jetzt)
        if t is not None:
            stand["termin"] = max(float(stand.get("termin") or 0), t)
    _schreiben(STAND_NAME, stand)
    _protokoll(f"Vollscan nach Zeitplan ({'Systemstart' if art == 'systemstart' else 'Termin'})", text)
    return text


class Planer:
    """Faden in der Wache: prüft den Plan alle PRUEFEN_S Sekunden."""

    def __init__(self, stopp: threading.Event, bei_fehler=None):
        self.stopp = stopp
        self.bei_fehler = bei_fehler
        self.faden: threading.Thread | None = None

    def start(self) -> None:
        self.faden = threading.Thread(target=self._lauf, daemon=True, name="wache-termin")
        self.faden.start()

    def _lauf(self) -> None:
        if self.stopp.wait(ERSTE_PRUEFUNG_S):
            return
        while True:
            try:
                pruefen()
            except Exception as exc:
                if self.bei_fehler:
                    self.bei_fehler("termin", f"{type(exc).__name__}: {exc}")
            if self.stopp.wait(PRUEFEN_S):
                return
