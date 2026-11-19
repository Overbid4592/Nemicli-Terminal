"""
paths.py - Wo liegt was?  (Ein Ort, alle Module fragen hier.)

  INSTALL - der Programm-Ordner: Quelltext, nemicli.config.json, .env, .runtime,
            requirements.txt. Hier wird so wenig geschrieben wie möglich.
  DATEN   - der Daten-Ordner: Chats, Bilder, Modelle, Gelerntes, Persönlichkeiten.

Getrennt, damit das Programm umziehen, neu gebaut oder als exe gepackt werden kann,
ohne die Daten mitzunehmen. DATEN steht in nemicli.config.json unter "daten_ordner"
(Fenster beim ersten Start oder /start); fehlt der Eintrag, gilt DATEN = INSTALL.
ROOT ist ein anderer Name für DATEN.

Sonderfall exe (PyInstaller): der Code steckt dann in `_internal/` bzw. in
einem Temp-Ordner (sys._MEIPASS) - da darf NICHTS gespeichert werden, das ist
beim nächsten Start weg. Deshalb zeigt INSTALL dann auf den Ordner, in dem
NemiCLI.exe liegt.

Zusätzlich legt `ensure_layout()` beim Start die Ordner an, die NemiCLI
braucht, darunter die Modell-Bereiche aus modellorte.py (KI-Modelle/) mit
je einer Anleitung.txt: die großen Modelle liefert NemiCLI nicht mit.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# --- Läuft NemiCLI als gepackte exe? ---------------------------------------
FROZEN = bool(getattr(sys, "frozen", False))


def _install_root() -> Path:
    """Ordner des Programms. Als exe: der Ordner, in dem NemiCLI.exe liegt."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _asset_root() -> Path:
    """Ordner für mitgelieferte Dateien (z.B. allowlist.json)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", _install_root()))
    return Path(__file__).resolve().parent.parent


INSTALL = _install_root()    # hier liegt das Programm
ASSETS = _asset_root()       # hier liegen mitgelieferte Dateien


def exe_ohne_fenster() -> Path:
    """Als exe: NemiCLI.exe (Fenster-App, öffnet kein Terminal) – für Wache,
    Vollscan, Zeitplan-Aufträge und das eigene Terminal-Fenster."""
    w = INSTALL / "NemiCLI.exe"
    return w if w.exists() else Path(sys.executable)


def exe_mit_fenster() -> Path:
    """Als exe: NemiCLIc.exe (Konsolen-Programm) – läuft im eigenen Terminal-Fenster."""
    k = INSTALL / "NemiCLIc.exe"
    return k if k.exists() else Path(sys.executable)

CONFIG_DATEI = INSTALL / "nemicli.config.json"


# ===========================================================================
#  Der Daten-Ordner
# ===========================================================================
# Die Config wird hier von Hand gelesen statt über config.py - sonst dreht es
# sich im Kreis: config.py fragt paths, wo die Config liegt.

#: None = der Nutzer wurde noch nie gefragt (-> Fenster beim Start zeigen).
#: ""   = er hat bewusst gesagt "bleibt beim Programm" (-> nicht mehr fragen).
GEWAEHLT: str | None = None

#: Gesetzt, wenn ein Ordner eingestellt ist, aber nicht erreichbar (Laufwerk
#: abgezogen, Buchstabe verrutscht). Dann NICHT stillschweigend mit leerem
#: Gedächtnis starten, sondern warnen - main.py zeigt den Text beim Start.
PROBLEM: str | None = None


def _lies_gewaehlt() -> str | None:
    try:
        d = json.loads(CONFIG_DATEI.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict) or "daten_ordner" not in d:
        return None
    return str(d.get("daten_ordner") or "")


def _beschreibbar(p: Path) -> bool:
    """Wirklich anfassbar? Dass der Pfad existiert, reicht nicht - ein
    abgezogenes Laufwerk kann als Pfad noch plausibel aussehen."""
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".nemicli.schreibprobe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _testlauf() -> bool:
    """Läuft die Test-Suite (python -m unittest)?"""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if str(getattr(spec, "name", "") or "").split(".")[0] == "unittest":
        return True
    return "unittest" in str(sys.argv[0] if sys.argv else "")


TESTDATEN = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "nemicli_testdaten"
_TESTDATEN_FRISCH = "NEMICLI_TESTDATEN_FRISCH"      # einmal je Testlauf leeren, auch bei erneutem Import


def _testdaten_leeren() -> None:
    """Reste früherer Testläufe entfernen – einmal je Lauf (Kindprozesse erben die Marke)."""
    if os.environ.get(_TESTDATEN_FRISCH):
        return
    os.environ[_TESTDATEN_FRISCH] = "1"
    if TESTDATEN.name == "nemicli_testdaten" and TESTDATEN.is_dir():
        import shutil
        shutil.rmtree(TESTDATEN, ignore_errors=True)


def _daten_root() -> Path:
    """DATEN bestimmen. Im Zweifel immer INSTALL - NemiCLI startet lieber am
    alten Ort als gar nicht. In der Test-Suite immer TESTDATEN: Tests fassen
    den eingestellten Daten-Ordner nie an, auch nicht zum Prüfen."""
    global GEWAEHLT, PROBLEM
    GEWAEHLT = _lies_gewaehlt()
    if _testlauf():
        _testdaten_leeren()
        TESTDATEN.mkdir(parents=True, exist_ok=True)
        return TESTDATEN
    if not GEWAEHLT:                      # None (nie gefragt) oder "" (bewusst hier)
        return INSTALL
    try:
        ziel = Path(os.path.expandvars(GEWAEHLT)).expanduser().resolve()
    except Exception:
        PROBLEM = (f"Der eingestellte NemiCLI-Ordner ist unbrauchbar: {GEWAEHLT!r}. "
                   "Ich arbeite vorerst beim Programm weiter. Mit /start kannst du "
                   "ihn neu setzen.")
        return INSTALL
    if not _beschreibbar(ziel):
        PROBLEM = (f"Der NemiCLI-Ordner ist nicht erreichbar: {ziel}. "
                   "Liegt er auf einem Laufwerk, das gerade nicht da ist? "
                   "Deine Daten sind NICHT weg - ich komme nur nicht ran. "
                   "Ich arbeite vorerst beim Programm weiter; mit /start kannst du "
                   "einen anderen Ordner wählen.")
        return INSTALL
    return ziel


DATEN = _daten_root()        # hier wird gespeichert
ROOT = DATEN                 # alter Name, damit bestehende Module weiterlaufen


def neu_laden() -> None:
    """Nach einem Wechsel über /start: DATEN/ROOT neu bestimmen.

    ACHTUNG - das hilft nur Modulen, die `paths.ROOT` bei JEDEM Zugriff lesen.
    Die meisten holen sich ROOT beim Import in eine eigene Variable, und die
    bleibt stehen. Deshalb sagt /start dem Nutzer, dass er NemiCLI neu starten
    soll, statt so zu tun, als sei der Wechsel sofort überall angekommen.
    """
    global DATEN, ROOT, PROBLEM
    PROBLEM = None
    DATEN = _daten_root()
    ROOT = DATEN
    ensure_layout()


def asset(name: str) -> Path:
    """Mitgelieferte Datei suchen: erst neben dem Programm (überschreibbar),
    sonst im gepackten Bündel."""
    own = INSTALL / name
    if own.exists():
        return own
    return ASSETS / name


# ===========================================================================
#  Ordner anlegen + LIES-MICH-Dateien
# ===========================================================================

# Ordner, die immer da sein sollen.
_DIRS = ("Bilder", "chats", "learned", "learned/snippets", "learned/skills", "NemiSandbox", "Befehle",
         "Gespraeche", "Wissen")

# Alles, was beim Umzug in einen neuen Daten-Ordner MITKOMMT - Ordner und
# einzelne Dateien. reich.py arbeitet diese Liste ab; was es nicht gibt, wird
# übersprungen.
#
# Was hier NICHT steht, bleibt bewusst beim Programm, weil es zur Installation
# gehört und nicht zum Nutzer: .env, nemicli.config.json, .git, .runtime,
# requirements.txt, nemicli.cmd, chrome-erweiterung, venv.
# Backup/ zieht ebenfalls nicht mit um: Sicherungen können sehr groß sein.
DATEN_INHALT = ("chats", "Gespraeche", "learned", "Bilder", "Models",
                "NemiSandbox", "Persoenlichkeiten", "Agenten", "Skills", "Profile", "Befehle",
                "Vorschläge", "Zeitplan", "Berichte", "Wissen", "Papierkorb", "Wache", "Apps",
                "Charaktergalerie", "Doku", "Export", "LoRA", ".cli_history")

_BEFEHLE_README = """\
=== Hier landen Hilfs-Skripte (.ps1) ===

Wenn NemiCLI mal ein dauerhaftes PowerShell-Skript für dich schreibt (z.B. zum
Aufräumen von Ordnern), gehört es HIERHER - nicht auf den Desktop.

Für einen einmaligen Befehl braucht NemiCLI KEINE Datei: das Werkzeug führt
mehrzeilige PowerShell direkt aus. Skripte hier darfst du jederzeit löschen.
"""

# Erster Satz des frueheren Models-LIES-MICH (Stand llama.cpp/Ollama). Eine solche
# unveraenderte Datei ersetzt ensure_layout() durch _MODELS_README.
_ALTER_MODELS_NAME = "LIES-MICH – hier GGUF-Modelle ablegen.txt"
_ALTER_MODELS_KOPF = "=== Sprach-Modelle laufen ueber Ollama ==="

_BILDER_README = """\
Hier landen die Bilder, die NemiCLI malt (/bild).
Der Prompt steht als Notiz in der PNG-Datei drin.
"""


_WISSEN_README = """\
Wissen für ALLE Persönlichkeiten: PDF, Markdown (.md) und Text (.txt), auch in Unterordnern.

Neue und geänderte Dateien liest NemiCLI nach der nächsten Antwort ins Gedächtnis
ein (sofort: /gedaechtnis indexieren). Gelöschte Dateien verschwinden auch dort.

Wortgleiche Sätze, die schon in einer älteren Datei stehen, kommen nur einmal ins
Gedächtnis. Die Dateien hier bleiben dabei unverändert.
"""


_GESPRAECHE_README = """Hier landen die Gespraeche, die du mit F12 sicherst.

F12 schreibt das KOMPLETTE laufende Gespraech als Markdown-Datei: jede Frage,
jeden Denktext, jede Antwort und jede ausgefuehrte Aktion. Gedacht fuer alle,
die im Terminal schlecht markieren koennen - und weil der Denktext sonst
nirgends steht (F2 zeigt immer nur die letzte Runde).

Eine Datei je Druck auf F12, benannt nach Chat-Nummer und Uhrzeit:
    Chat-093_20260914-1204.md

Die Dateien bleiben hier auf dem PC. Nichts davon geht ins Netz.
"""


def _write_once(path: Path, text: str) -> None:
    """Schreibt die Datei nur, wenn sie fehlt (nie etwas überschreiben)."""
    try:
        if not path.exists():
            path.write_text(text, encoding="utf-8")
    except Exception:
        pass


def ensure_layout() -> None:
    """Legt fehlende Ordner an, dazu die Modell-Bereiche mit ihren Anleitungen.
    Läuft bei jedem Start; vorhandene Dateien bleiben unangetastet."""
    for rel in _DIRS:
        try:
            (DATEN / rel).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    _alten_models_zettel_ersetzen()
    _write_once(DATEN / "Bilder" / "LIES-MICH.txt", _BILDER_README)
    _write_once(DATEN / "Befehle" / "LIES-MICH.txt", _BEFEHLE_README)
    _write_once(DATEN / "Gespraeche" / "LIES-MICH.txt", _GESPRAECHE_README)
    _write_once(DATEN / "Wissen" / "LIES-MICH.txt", _WISSEN_README)
    try:
        import modellorte
        modellorte.anlegen()
    except Exception:
        pass


def _alten_models_zettel_ersetzen() -> None:
    """Den früheren Models-LIES-MICH entfernen, solange er unverändert ist."""
    alt = DATEN / "Models" / _ALTER_MODELS_NAME
    try:
        if alt.is_file() and alt.read_text(encoding="utf-8").startswith(_ALTER_MODELS_KOPF):
            alt.unlink()
    except Exception:
        pass


if __name__ == "__main__":       # Selbsttest:  python core/paths.py
    print("frozen  :", FROZEN)
    print("INSTALL :", INSTALL)
    print("DATEN   :", DATEN)
    print("ASSETS  :", ASSETS)
    print("gewaehlt:", repr(GEWAEHLT))
    print("Problem :", PROBLEM)
    ensure_layout()
    print("Ordner angelegt/geprüft.")
