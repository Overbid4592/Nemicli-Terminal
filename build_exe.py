"""
build_exe.py - Macht aus NemiCLI eine NemiCLI.exe.

Aufruf:   python build_exe.py
Ergebnis: dist/NemiCLI/NemiCLI.exe  +  dist/NemiCLI/NemiCLIT2/  (Python, Bibliotheken, Code)
          dist/NemiCLI/SHA256SUMS.txt    (Prüfsummen der exe-Dateien)

Liegt ein Code-Signatur-Zertifikat "CN=NemiCLI" im Zertifikatsspeicher des
Nutzers (anlegen: python zertifikat.py), werden die exe-Dateien signiert.

Die exe startet direkt aus NemiCLIT2 – sie entpackt nichts in den Temp-Ordner.
Das ist Absicht: eine exe, die sich bei jedem Start selbst entpackt und von
dort Code ausführt, hält Bitdefender für Schadsoftware.

Beim Start legt NemiCLI selbst an, was es braucht (paths.ensure_layout), und
fragt beim ersten Mal, wo der Daten-Ordner liegen soll. Auf dem Zielrechner
muss kein Python installiert sein (außer zum Bildermalen, siehe /systemcheck).
Die großen Modelle kommen NICHT mit – jeder sucht sich selbst aus, welche.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
ZIEL = DIST / "NemiCLI"
EXE = ZIEL / "NemiCLI.exe"
ZERTIFIKAT = "CN=NemiCLI"
ZEITSTEMPEL = "http://timestamp.digicert.com"


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


def eigene_daten_im_ziel() -> list[str]:
    """Was in dist/NemiCLI liegt und nicht vom Bau stammt.

    PyInstaller leert dist/NemiCLI vor jedem Bau komplett. Wurde die exe dort
    schon benutzt und ist dort der Daten-Ordner, wären Einstellungen, Schlüssel,
    Chats und Gedächtnis danach weg – deshalb wird dann gar nicht gebaut.
    """
    if not ZIEL.is_dir():
        return []
    gefunden = [n for n in ("nemicli.config.json", ".env") if (ZIEL / n).exists()]
    von_selbst = {"folder_model.json"}           # legt NemiCLI beim Start ohne Zutun an
    for n in ("chats", "Gespraeche", "learned", "Wache", "Persoenlichkeiten", "Bilder",
              "Agenten", "Berichte", "Models"):
        d = ZIEL / n
        if d.is_dir() and any(f.name not in von_selbst and not f.name.startswith("LIES-MICH")
                              for f in d.rglob("*") if f.is_file()):
            gefunden.append(n)
    return gefunden


def build() -> bool:
    _step("PyInstaller läuft (dauert 1-3 Minuten)")
    r = subprocess.run([sys.executable, "-m", "PyInstaller",
                        str(ROOT / "NemiCLI.spec"), "--noconfirm",
                        "--distpath", str(DIST),
                        "--workpath", str(ROOT / "build")],
                       cwd=str(ROOT))
    return r.returncode == 0


def _eigene_exe() -> list[Path]:
    return sorted(ZIEL.glob("*.exe"))


def signieren() -> str:
    """Signiert die exe-Dateien mit dem Zertifikat aus dem Nutzer-Speicher.
    Mit Zeitstempel, damit die Signatur das Ablaufen des Zertifikats überdauert."""
    dateien = ",".join("'" + str(f).replace("'", "''") + "'" for f in _eigene_exe())
    skript = "\n".join([
        "$ErrorActionPreference='Stop'",
        r"$z = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | "
        f"Where-Object {{ $_.Subject -eq '{ZERTIFIKAT}' -and $_.NotAfter -gt (Get-Date) }} | "
        "Sort-Object NotAfter -Descending | Select-Object -First 1",
        "if (-not $z) { 'kein-zertifikat'; exit 0 }",
        f"foreach ($f in @({dateien})) {{",
        "  try { $s = Set-AuthenticodeSignature -FilePath $f -Certificate $z -HashAlgorithm SHA256 "
        f"-TimestampServer '{ZEITSTEMPEL}' }}",
        "  catch { $s = Set-AuthenticodeSignature -FilePath $f -Certificate $z -HashAlgorithm SHA256 }",
        '  "$($s.Status)  $(Split-Path $f -Leaf)"',
        "}",
    ])
    r = subprocess.run(["powershell", "-NoProfile", "-Command", skript],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return "\n".join(x.strip() for x in (r.stdout, r.stderr) if x and x.strip())


def pruefsummen() -> Path:
    """SHA-256 jeder exe in SHA256SUMS.txt (Format wie sha256sum) – nach dem
    Signieren, weil die Signatur die Datei verändert."""
    zeilen = []
    for f in _eigene_exe():
        h = hashlib.sha256()
        with open(f, "rb") as d:
            for block in iter(lambda: d.read(1 << 20), b""):
                h.update(block)
        zeilen.append(f"{h.hexdigest()}  {f.name}")
    ziel = ZIEL / "SHA256SUMS.txt"
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return ziel


def main() -> int:
    t0 = time.time()
    _step("Vorprüfung")
    if not check():
        return 1
    daten = eigene_daten_im_ziel()
    if daten:
        print(f"ABBRUCH: In {ZIEL} liegen eigene Daten ({', '.join(daten)}).")
        print("Der Bau würde diesen Ordner leeren. Erst sichern oder woanders hin verschieben.")
        return 1
    alt = DIST / "NemiCLI.exe"                   # frühere Ein-Datei-Fassung
    if alt.is_file():
        alt.unlink()
        print(f"  alte Ein-Datei-exe entfernt: {alt}")
    print("alles da.")
    if not build():
        print("\nBau fehlgeschlagen – Meldung oben lesen.")
        return 1
    if not EXE.exists():
        print("\nKeine NemiCLI.exe entstanden.")
        return 1
    _step("Signieren")
    ergebnis = signieren()
    if "kein-zertifikat" in ergebnis:
        print(f"  Kein Zertifikat {ZERTIFIKAT} gefunden – unsigniert (anlegen: python zertifikat.py).")
    else:
        print("  " + ergebnis.replace("\n", "\n  "))
    _step("Prüfsummen (SHA-256)")
    summen = pruefsummen()
    print("  " + summen.read_text(encoding="utf-8").strip().replace("\n", "\n  "))
    groesse = sum(f.stat().st_size for f in ZIEL.rglob("*") if f.is_file())
    _step("Fertig")
    print(f"  {EXE}")
    print(f"  Größe: {groesse / (1024**2):.0f} MB   ·   Dauer: {time.time() - t0:.0f} s")
    print("\n  Weitergeben: den ganzen Ordner 'dist/NemiCLI' kopieren oder zippen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
