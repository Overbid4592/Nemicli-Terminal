"""
paths.py - Wo liegt was?  (Ein Ort, alle Module fragen hier.)

ZWEI WURZELN, und das ist der ganze Punkt dieser Datei:

  INSTALL - der Programm-Ordner. Hier liegt NemiCLI selbst: Quelltext,
            nemicli.config.json, .env, .runtime, requirements.txt.
            Hier wird so wenig geschrieben wie möglich.

  DATEN   - der NemiCLI-Ordner des Nutzers. Chats, Bilder, Modelle, das
            Gelernte, die Persönlichkeiten. Alles, was wächst.

Bis zum 15.09.2026 war das derselbe Ordner. Das ging so lange gut, bis der
Programm-Ordner umzog (Desktop -> AppData) - dabei blieb das venv auf der
Strecke, und genauso hätte es das ganze Gedächtnis erwischen können. Deshalb
jetzt getrennt: das Programm darf umziehen, neu gebaut oder als exe gepackt
werden, ohne dass die Daten das mitmachen müssen.

Wo DATEN liegt, entscheidet der NUTZER - über das Fenster beim ersten Start
oder jederzeit mit /start. Der Pfad steht in nemicli.config.json unter
"daten_ordner". Steht dort nichts, bleibt alles beim Alten (DATEN = INSTALL);
es geht also nichts kaputt, wenn die Einstellung fehlt.

  ROOT ist nur ein anderer Name für DATEN. Alle Module, die
  `from paths import ROOT` machen, meinen ihre Daten - die mussten deshalb
  nicht angefasst werden. Nur die Handvoll Stellen, die wirklich das PROGRAMM
  meinen, holen sich INSTALL.

Sonderfall exe (PyInstaller): der Code steckt dann in `_internal/` bzw. in
einem Temp-Ordner (sys._MEIPASS) - da darf NICHTS gespeichert werden, das ist
beim nächsten Start weg. Deshalb zeigt INSTALL dann auf den Ordner, in dem
NemiCLI.exe liegt.

Zusätzlich legt `ensure_layout()` beim Start die Ordner an, die NemiCLI
braucht, und schreibt in die Modell-Ordner eine "LIES-MICH"-Datei: die
großen Modelle (GGUF / Stable-Diffusion-Checkpoints) darf NemiCLI nicht
mitliefern - die holt sich jeder selbst.
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


def _daten_root() -> Path:
    """DATEN bestimmen. Im Zweifel immer INSTALL - NemiCLI startet lieber am
    alten Ort als gar nicht."""
    global GEWAEHLT, PROBLEM
    GEWAEHLT = _lies_gewaehlt()
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
_DIRS = ("Models", "Models/checkpoints", "Bilder", "chats",
         "learned", "learned/snippets", "learned/skills", "NemiSandbox", "Befehle",
         "Gespraeche")

# Alles, was beim Umzug in einen neuen Daten-Ordner MITKOMMT - Ordner und
# einzelne Dateien. reich.py arbeitet diese Liste ab; was es nicht gibt, wird
# übersprungen.
#
# Was hier NICHT steht, bleibt bewusst beim Programm, weil es zur Installation
# gehört und nicht zum Nutzer: .env, nemicli.config.json, .git, .runtime,
# requirements.txt, nemicli.cmd, chrome-erweiterung, venv.
DATEN_INHALT = ("chats", "Gespraeche", "learned", "Bilder", "Models",
                "NemiSandbox", "Persoenlichkeiten", "Agenten", "Befehle",
                "Vorschläge", "Zeitplan", "Berichte", ".cli_history")

_BEFEHLE_README = """\
=== Hier landen Hilfs-Skripte (.ps1) ===

Wenn NemiCLI mal ein dauerhaftes PowerShell-Skript für dich schreibt (z.B. zum
Aufräumen von Ordnern), gehört es HIERHER - nicht auf den Desktop.

Für einen einmaligen Befehl braucht NemiCLI KEINE Datei: das Werkzeug führt
mehrzeilige PowerShell direkt aus. Skripte hier darfst du jederzeit löschen.
"""

_GGUF_README = """=== Sprach-Modelle laufen ueber Ollama ===

NemiCLI bringt KEINE Modelle mit - die sind mehrere Gigabyte gross und jeder
soll sich selbst aussuchen, welches er mag.

Bis zum 15.09.2026 konnte NemiCLI eigene .gguf-Dateien aus diesem Ordner
selbst starten (ueber llama.cpp). Das ist entfallen. Eine .gguf hier abzulegen
bringt jetzt nichts mehr - sie wird nicht mehr gefunden.

So geht es heute:

  1. Ollama installieren:  https://ollama.com/download
  2. NemiCLI starten -> /model -> "Ollama-Modell herunterladen"
     (oder im Terminal:  ollama pull gemma3)
  3. /model -> "Ollama" -> Modell auswaehlen.

Ollama bringt seine eigene Rechen-Maschine mit und laeuft auf der GPU.
Welche Groesse auf deinen PC passt, sagt dir:  /systemcheck

Alternative ohne lokale Modelle: einen Cloud-Schluessel in die .env legen
(z.B. OPENAI_API_KEY) - dann laeuft alles ueber die Cloud.

In diesem Ordner liegen weiterhin die BILD-Modelle (Models/checkpoints) und
die Embeddings fuers Gedaechtnis.
"""

_SD_README = """\
=== Hier kommen deine Bild-Modelle rein (.safetensors) ===

Für /bild (Bilder malen) braucht NemiCLI ein Stable-Diffusion-Modell.
Auch das bringt sie NICHT mit - zu groß, und der Geschmack ist verschieden.

So geht's:

  1. Einen Checkpoint herunterladen (Format .safetensors), z.B. von
     https://civitai.com  oder  https://huggingface.co
     Unterstützt: SD 1.5 und SDXL. (SDXL braucht ca. 6-7 GB Platz.)

  2. Die .safetensors-Datei IN DIESEN ORDNER LEGEN.

  3. NemiCLI starten -> /bildmodel -> Modell auswählen.
     Danach: /bild ein fuchs im wald   (oder einfach "mal mir einen Fuchs")

Dafür wird zusätzlich torch + diffusers gebraucht (einmalige Installation).
Welcher Befehl bei DEINER Grafikkarte der richtige ist, sagt dir:
  /systemcheck

Sicherheits-Hinweis: Nimm .safetensors, keine .ckpt-Dateien - .ckpt kann
beim Laden Code ausführen.
"""

_BILDER_README = """\
Hier landen die Bilder, die NemiCLI malt (/bild).
Der Prompt steht als Notiz in der PNG-Datei drin.
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
    """Legt fehlende Ordner an und erklärt in den Modell-Ordnern, was reingehört.
    Läuft bei jedem Start; vorhandene Dateien bleiben unangetastet."""
    for rel in _DIRS:
        try:
            (DATEN / rel).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    _write_once(DATEN / "Models" / "LIES-MICH – hier GGUF-Modelle ablegen.txt",
                _GGUF_README)
    _write_once(DATEN / "Models" / "checkpoints" /
                "LIES-MICH – hier Bild-Modelle ablegen.txt", _SD_README)
    _write_once(DATEN / "Bilder" / "LIES-MICH.txt", _BILDER_README)
    _write_once(DATEN / "Befehle" / "LIES-MICH.txt", _BEFEHLE_README)
    _write_once(DATEN / "Gespraeche" / "LIES-MICH.txt", _GESPRAECHE_README)


if __name__ == "__main__":       # Selbsttest:  python core/paths.py
    print("frozen  :", FROZEN)
    print("INSTALL :", INSTALL)
    print("DATEN   :", DATEN)
    print("ASSETS  :", ASSETS)
    print("gewaehlt:", repr(GEWAEHLT))
    print("Problem :", PROBLEM)
    ensure_layout()
    print("Ordner angelegt/geprüft.")
