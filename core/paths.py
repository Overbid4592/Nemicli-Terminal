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
großen Modelle (GGUF, Bildbeschreiber, Krea 2) liefert NemiCLI nicht mit.
Sprachmodelle (ModelGGUF/), Bildbeschreiber (Vision/) und Embeddings
(Models/embeddings/) liegen im Programm-Ordner, Krea 2 bei den Daten.
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


def _daten_root() -> Path:
    """DATEN bestimmen. Im Zweifel immer INSTALL - NemiCLI startet lieber am
    alten Ort als gar nicht. In der Test-Suite immer TESTDATEN: Tests fassen
    den eingestellten Daten-Ordner nie an, auch nicht zum Prüfen."""
    global GEWAEHLT, PROBLEM
    GEWAEHLT = _lies_gewaehlt()
    if _testlauf():
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
_DIRS = ("Models", "Models/Krea2", "Bilder", "chats",
         "learned", "learned/snippets", "learned/skills", "NemiSandbox", "Befehle",
         "Gespraeche", "Wissen")

# Alles, was beim Umzug in einen neuen Daten-Ordner MITKOMMT - Ordner und
# einzelne Dateien. reich.py arbeitet diese Liste ab; was es nicht gibt, wird
# übersprungen.
#
# Was hier NICHT steht, bleibt bewusst beim Programm, weil es zur Installation
# gehört und nicht zum Nutzer: .env, nemicli.config.json, .git, .runtime,
# requirements.txt, nemicli.cmd, chrome-erweiterung, venv.
DATEN_INHALT = ("chats", "Gespraeche", "learned", "Bilder", "Models",
                "NemiSandbox", "Persoenlichkeiten", "Agenten", "Skills", "Profile", "Befehle",
                "Vorschläge", "Zeitplan", "Berichte", "Wissen", "Papierkorb", ".cli_history")

_BEFEHLE_README = """\
=== Hier landen Hilfs-Skripte (.ps1) ===

Wenn NemiCLI mal ein dauerhaftes PowerShell-Skript für dich schreibt (z.B. zum
Aufräumen von Ordnern), gehört es HIERHER - nicht auf den Desktop.

Für einen einmaligen Befehl braucht NemiCLI KEINE Datei: das Werkzeug führt
mehrzeilige PowerShell direkt aus. Skripte hier darfst du jederzeit löschen.
"""

_MODELS_README = """=== Was in diesen Ordner gehoert ===

Krea2/   Dateien fuer den Bild-Motor Krea 2 (/bild). Erklaerung im Ordner.

Sprachmodelle, Bildbeschreiber und Embeddings liegen NICHT hier, sondern
im Programm-Ordner von NemiCLI:

  ModelGGUF/<Name>/          lokale Sprachmodelle (eigener GGUF-Motor)
  Vision/<Name>/             Bildbeschreiber fuer Modelle ohne Sehen
  Models/embeddings/<Name>/  Embedding-Modell fuers Gedaechtnis

In jedem dieser Ordner steht ein eigener LIES-MICH.
"""

# Erster Satz des frueheren Models-LIES-MICH (Stand llama.cpp/Ollama). Eine solche
# unveraenderte Datei ersetzt ensure_layout() durch _MODELS_README.
_ALTER_MODELS_NAME = "LIES-MICH – hier GGUF-Modelle ablegen.txt"
_ALTER_MODELS_KOPF = "=== Sprach-Modelle laufen ueber Ollama ==="

_MODELGGUF_README = """=== Lokale Sprachmodelle fuer den eigenen GGUF-Motor ===

Ein Unterordner je Modell. Darin:
  - genau eine .gguf mit dem Sprachmodell
  - optional eine mmproj-*.gguf: dann sieht das Modell Bilder

Beispiel:
  ModelGGUF/Gemma4/gemma-4-E4B-it-Q4_K_M.gguf
  ModelGGUF/Gemma4/mmproj-gemma-4-E4B-it-BF16.gguf

Am einfachsten: /model huggingface. NemiCLI zeigt nur Modelle, die der Motor
laden kann (ab 4B Parameter), prueft jede Datei mit SHA-256 und legt sie
hier ab. Danach: /model -> "Lokal (eigener Motor)".

Kein Server, kein llama.cpp, kein Ollama noetig - der Motor rechnet mit torch
im NemiCLI-Prozess.
"""

_VISION_README = """=== Bildbeschreiber: Augen fuer lokale Modelle ohne Sehen ===

Sieht ein Modell im eigenen GGUF-Motor selbst keine Bilder, beschreibt ein
kleines sehendes Modell aus diesem Ordner das Bild als Text. Das Sprachmodell
bekommt die Beschreibung und kann mit dem Werkzeug bild_fragen nachfragen.

Ein Unterordner je Beschreiber. Darin:
  - eine .gguf mit dem Modell (z. B. Gemma-4-E2B)
  - eine mmproj-*.gguf mit dem Bild-Teil

Beispiel:
  Vision/GemmaE2B/gemma-4-E2B-it-Q4_K_M.gguf
  Vision/GemmaE2B/mmproj-gemma-4-E2B-it-BF16.gguf

Genommen wird der erste Ordner mit bekanntem Bild-Encoder.
Gilt nur fuer lokale Modelle im eigenen Motor, nie fuer Cloud-Modelle.
Modelle, die selbst sehen (mmproj im ModelGGUF-Ordner), brauchen ihn nicht.
"""

_KREA_README = """\
=== Hier kommen die Dateien fuer Krea 2 rein (Bilder malen) ===

/bild malt mit Krea 2, NemiCLIs eigener Bild-Pipeline. Die Modelldateien bringt
NemiCLI nicht mit - zu gross.

Hierher gehoeren:
  - das Krea-2-Modell (.safetensors)
  - text_encoders/  (Text-Encoder)
  - vae/            (VAE)
  - der Tokenizer   (vocab.json + merges.txt)

Danach: /bildmodel -> Modell auswaehlen. Fehlt etwas, sagt /bild genau was.
"""

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
    """Legt fehlende Ordner an und erklärt in den Modell-Ordnern, was reingehört.
    Läuft bei jedem Start; vorhandene Dateien bleiben unangetastet."""
    for rel in _DIRS:
        try:
            (DATEN / rel).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    _alten_models_zettel_ersetzen()
    _write_once(DATEN / "Models" / "LIES-MICH.txt", _MODELS_README)
    _write_once(DATEN / "Models" / "Krea2" /
                "LIES-MICH – hier Krea-2-Dateien ablegen.txt", _KREA_README)
    _write_once(DATEN / "Bilder" / "LIES-MICH.txt", _BILDER_README)
    _write_once(DATEN / "Befehle" / "LIES-MICH.txt", _BEFEHLE_README)
    _write_once(DATEN / "Gespraeche" / "LIES-MICH.txt", _GESPRAECHE_README)
    _write_once(DATEN / "Wissen" / "LIES-MICH.txt", _WISSEN_README)
    for rel, text in (("ModelGGUF", _MODELGGUF_README), ("Vision", _VISION_README)):
        try:
            (INSTALL / rel).mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        _write_once(INSTALL / rel / "LIES-MICH.txt", text)


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
