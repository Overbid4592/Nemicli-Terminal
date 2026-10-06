"""
wache – NemiCLIs Systemwache: Sensoren, Regeln, lernende Anomalie-Erkennung.

Abgespeckte Fassung eines eigenständigen SIEM-Projekts. Der Unterschied zum SIEM: hier gibt es keine Oberfläche mit Tabs, hier
gibt es eine Persönlichkeit. Die Wache sammelt und bewertet; wenn etwas
aus dem Rahmen fällt, wird die aktive Persönlichkeit geweckt, schaut nach und entscheidet – und darf
in Grenzen selbst nachjustieren. Alles, was sie tut, steht im Protokoll.

Bausteine (alles ohne Administratorrechte, alles lokal auf F:):

  ereignisse.py   Ereignis / Alarm / Schwere – das gemeinsame Schema
  speicher.py     SQLite: Ereignisse, Alarme, Prozess-Profile, Inventar, Meta
  sensoren.py     Prozesse (psutil-Diff), Netz (Socket-Diff), Dateien (watchdog)
  inventar.py     Tiefenscan: Prozesse, Dienste, Autostart, Aufgaben, Software, Ports
  regeln.py       R001–R014 – bekannte Muster, zuverlässig
  merkmale.py     Ereignis → Zahlenvektor (zwei Merkmalssätze: Prozesse, Dateien)
  wald.py         eigener Isolation Forest auf numpy – kein scikit-learn
  detektor.py     Modell + Kaltstart + Perzentil-Score + Feedback + Persistenz
  motor.py        die Pipeline: sammeln → anreichern → bewerten → Regeln → speichern
                  → alle 200 Ereignisse nachtrainieren → bei Alarm die Persönlichkeit wecken
  justierung.py   was die Persönlichkeit selbst ändern darf (Schwelle, stumme Regeln) – mit Grenzen
  tray.py         Symbol in der Taskleiste (pystray) – Grün ruhig, Rot: die Persönlichkeit hat was gefunden
  selbsttest.py   synthetische Ereignisse + Angriffsmuster, ohne Wartezeit

Die Daten liegen in DATEN/Wache/ (wache.db, modelle/, alarme/); Berichte der
Persönlichkeit in DATEN/Berichte/. Was die Persönlichkeit bei einem Alarm tun soll, steht nicht im
Code, sondern in DATEN/Agenten/wache_alarm.md – der Nutzer kann es ändern.
"""

from __future__ import annotations

from pathlib import Path

try:
    from paths import ROOT as _ROOT
except Exception:                          # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent.parent

ORDNER = _ROOT / "Wache"
DB_PFAD = ORDNER / "wache.db"
MODELL_ORDNER = ORDNER / "modelle"
BERICHTE = _ROOT / "Berichte"
ANLEITUNG = _ROOT / "Agenten" / "wache_alarm.md"
LOCK = ORDNER / "laeuft.json"              # PID + Start des Hintergrund-Prozesses
STOPP = ORDNER / "stopp"                   # Datei da = Hintergrund-Prozess beendet sich
TRAINING_LOG = ORDNER / "training.log"


def ordner_anlegen() -> None:
    ORDNER.mkdir(parents=True, exist_ok=True)
    MODELL_ORDNER.mkdir(parents=True, exist_ok=True)
    BERICHTE.mkdir(parents=True, exist_ok=True)
