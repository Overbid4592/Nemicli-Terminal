"""
build_exe.py - Macht aus NemiCLI eine NemiCLI.exe.

Aufruf:   python build_exe.py
Ergebnis: dist/NemiCLI/NemiCLI.exe   (+ alles, was dazugehört)

Der ganze Ordner `dist/NemiCLI` ist NemiCLI. Man kann ihn auf einen
USB-Stick kopieren oder zippen und weitergeben - auf dem Zielrechner muss
kein Python installiert sein (außer man will Bilder malen, siehe LIES-MICH).

Die großen Modelle (GGUF / Stable-Diffusion) kommen NICHT mit - sie sind
Gigabyte-schwer und jeder soll sich selbst aussuchen, welche er nimmt.
Deshalb legt dieses Skript leere Ordner mit Anleitungs-Dateien an.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "NemiCLI"

sys.path.insert(0, str(ROOT / "core"))
import paths as P                     # noqa: E402  (Ordner + LIES-MICH-Texte)


def _step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def check() -> bool:
    """Ist alles da, was zum Bauen gebraucht wird?"""
    ok = True
    try:
        import PyInstaller                                    # noqa: F401
    except ImportError:
        print("FEHLT: PyInstaller.  ->  python -m pip install pyinstaller")
        ok = False
    for mod in ("rich", "textual", "prompt_toolkit", "httpx", "openai", "anthropic", "dotenv"):
        try:
            __import__(mod)
        except ImportError:
            print(f"FEHLT: {mod}  ->  python -m pip install -r requirements.txt")
            ok = False
    if not (ROOT / "NemiCLI.spec").exists():
        print("FEHLT: NemiCLI.spec")
        ok = False
    return ok


def build() -> bool:
    _step("PyInstaller läuft (dauert 1-3 Minuten)")
    r = subprocess.run([sys.executable, "-m", "PyInstaller",
                        str(ROOT / "NemiCLI.spec"), "--noconfirm",
                        "--distpath", str(ROOT / "dist"),
                        "--workpath", str(ROOT / "build")],
                       cwd=str(ROOT))
    return r.returncode == 0


START_TXT = """\
=== NemiCLI ===

Starten:  NemiCLI.exe doppelklicken.   Fertig.

Beim ersten Start legt sie sich selbst die Ordner an, die sie braucht.
Python muss NICHT installiert sein.

------------------------------------------------------------------
Was NemiCLI NICHT mitbringt (und warum)
------------------------------------------------------------------

Die KI-Modelle. Die sind mehrere Gigabyte groß, und welches das richtige
ist, entscheidet jeder selbst. Es gibt drei Wege:

  A) Cloud (am einfachsten)
     NemiCLI starten -> /model -> "Cloud-Anbieter hinzufügen"
     -> API-Schlüssel eintragen. Läuft sofort, kostet je nach Anbieter.
     Der Schlüssel wird verschlüsselt gespeichert (an dein Windows-Konto
     gebunden).

  B) Ollama (einfachster Weg für "läuft bei mir zuhause")
     Ollama von https://ollama.com/download installieren,
     dort ein Modell laden - NemiCLI findet es dann von selbst.

  C) Eigene .gguf-Dateien
     Siehe Models\\LIES-MICH – hier GGUF-Modelle ablegen.txt

Bilder malen (/bild) braucht zusätzlich ein Bild-Modell und den
Rechen-Motor "torch". Was genau auf DEINEM PC gebraucht wird, sagt dir
NemiCLI selbst:

    /systemcheck

Da steht dann auch der exakte Installations-Befehl für deine
Grafikkarte (z.B. cu128 bei den neuen RTX-50er-Karten).
Wichtig: torch braucht ein installiertes Python {pyver} - dieselbe
Version, mit der diese exe gebaut wurde.

------------------------------------------------------------------
Erste Schritte
------------------------------------------------------------------

  /help          alle Befehle
  /systemcheck   was hat mein PC, was fehlt noch
  /model         Modell wählen (Cloud / Ollama / lokal)
  /theme         Farben umstellen
  /webui         Browser-Oberfläche (gut lesbar, mit Vorlesen)

Alles, was NemiCLI speichert (Chats, Notizen, Bilder, Schlüssel),
liegt in diesem Ordner. Ordner kopieren = Umzug fertig.
"""


def arrange() -> None:
    """Ordner + Anleitungen neben die fertige exe legen."""
    _step("Ordner und LIES-MICH-Dateien anlegen")
    P.ROOT = DIST                      # ensure_layout() soll in dist/ arbeiten
    P.ensure_layout()
    (DIST / "START HIER – LIES MICH.txt").write_text(
        START_TXT.format(pyver=f"{sys.version_info.major}.{sys.version_info.minor}"),
        encoding="utf-8")
    # allowlist.json auch offen daneben legen, damit man sie bearbeiten kann.
    src = ROOT / "allowlist.json"
    if src.exists():
        shutil.copy2(src, DIST / "allowlist.json")
    for rel in ("Models", "Models/checkpoints", "Bilder", "chats", "learned"):
        print("  ", (DIST / rel))


def size_of(p: Path) -> str:
    total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return f"{total / (1024**2):.0f} MB"


def main() -> int:
    t0 = time.time()
    _step("Vorprüfung")
    if not check():
        return 1
    print("alles da.")
    if not build():
        print("\nBau fehlgeschlagen – Meldung oben lesen.")
        return 1
    if not (DIST / "NemiCLI.exe").exists():
        print("\nKeine NemiCLI.exe entstanden.")
        return 1
    arrange()
    _step("Fertig")
    print(f"  {DIST / 'NemiCLI.exe'}")
    print(f"  Größe: {size_of(DIST)}   ·   Dauer: {time.time() - t0:.0f} s")
    print("\n  Zum Weitergeben einfach den ganzen Ordner 'dist/NemiCLI' zippen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
