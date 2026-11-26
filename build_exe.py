"""
build_exe.py - Macht aus NemiCLI eine NemiCLI.exe.

Aufruf:   python build_exe.py              exe bauen + Setup-Datei
          python build_exe.py --nur-setup  nur die Setup-Datei aus dem vorhandenen dist/NemiCLI
          python build_exe.py --msi        exe bauen + dist/NemiCLI-<Build>.msi (Windows Installer)
          python build_exe.py --nur-msi    nur die MSI aus dem vorhandenen dist/NemiCLI
Ergebnis: dist/NemiCLI/NemiCLI.exe  +  dist/NemiCLI/NemiCLIT2/  (Python, Bibliotheken, Code)
          dist/NemiCLI/SHA256SUMS.txt    (Prüfsummen der exe-Dateien)
          dist/NemiCLI-Setup-<Build>.exe (eine Datei: installiert oder aktualisiert .nemicli im Benutzerordner)

Liegt ein Code-Signatur-Zertifikat "CN=NemiCLI" im Zertifikatsspeicher des
Nutzers (anlegen: python zertifikat.py), werden die exe-Dateien signiert.

Die exe startet direkt aus NemiCLIT2 – sie entpackt nichts in den Temp-Ordner.
Das ist Absicht: eine exe, die sich bei jedem Start selbst entpackt und von
dort Code ausführt, hält der Virenschutz für Schadsoftware.

Beim Start legt NemiCLI selbst an, was es braucht (paths.ensure_layout), und
fragt beim ersten Mal, wo der Daten-Ordner liegen soll. Auf dem Zielrechner
muss kein Python installiert sein (außer zum Bildermalen, siehe /systemcheck).
Die großen Modelle kommen NICHT mit – jeder sucht sich selbst aus, welche.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
ZIEL = DIST / "NemiCLI"
EXE = ZIEL / "NemiCLI.exe"
ZERTIFIKAT = "CN=NemiCLI"
# Set-AuthenticodeSignature akzeptiert nur http. Die Antwort ist von der Zeitstempelstelle signiert.
ZEITSTEMPEL = "http://timestamp.digicert.com"


def _step(msg: str) -> None:
    print(f"\n=== {msg} ===")


# Build-Nummern mit diesem Ende werden nie vergeben (Notrufnummern): vorher einen weiteren Commit anlegen.
GESPERRTE_BUILDS = ("110", "112")


def build_gesperrt(nummer: str | None = None) -> str | None:
    """Das gesperrte Ende, auf das die Build-Nummer (Vorgabe: dieser Stand) endet – sonst None."""
    if nummer is None:
        if str(ROOT / "core") not in sys.path:
            sys.path.insert(0, str(ROOT / "core"))
        import version
        nummer = version.build_nummer()
    n = str(nummer).rsplit(".", 1)[-1]
    return next((g for g in GESPERRTE_BUILDS if n.endswith(g)), None)


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
    for fehler in motion_fehlt():
        print(f"FEHLT: {fehler}")
        ok = False
    return ok


def motion_fehlt() -> list[str]:
    """Jede exe bringt die Bewegung der Figur mit (NemiCLI.spec): Modell und Laufzeit im Bewegungs-Modellordner,
    mit der festen SHA-256 aus engines/bewegung.py. Gibt zurück, was fehlt oder nicht stimmt."""
    sys.path.insert(0, str(ROOT / "engines"))
    import bewegung
    ordner = ROOT / "KI-Modelle" / "KI-Bewegung" / "Bewegungsmodell" / "SprongMotion-14M"   # wie NemiCLI.spec
    if not (ordner / bewegung.MODELL).is_file() and (ROOT / "Models" / "Motion" / bewegung.MODELL).is_file():
        ordner = ROOT / "Models" / "Motion"                                            # früherer Ort
    noetig = [(ordner / bewegung.MODELL, bewegung.MODELL_SHA),
              (ordner / f"sprongmotion-{bewegung.VERSION}" / bewegung.DLL, bewegung.DLL_SHA)]
    fehlt = []
    for pfad, soll in noetig:
        if not pfad.is_file():
            fehlt.append(f"{pfad.relative_to(ROOT)}  ->  /bewegung holen bzw. einrichten (Entwicklungsordner)")
        elif bewegung.sha256(pfad) != soll:
            fehlt.append(f"{pfad.relative_to(ROOT)}: SHA-256 stimmt nicht")
    for name in ("LICENSE", "NOTICE"):
        if not (ordner / f"sprongmotion-{bewegung.VERSION}" / name).is_file():
            fehlt.append(f"{(ordner / f'sprongmotion-{bewegung.VERSION}' / name).relative_to(ROOT)} (gehört zur DLL)")
    return fehlt


def eigene_daten_im_ziel() -> list[str]:
    """Was in dist/NemiCLI liegt und nicht vom Bau stammt.

    PyInstaller leert dist/NemiCLI vor jedem Bau komplett. Wurde die exe dort
    schon benutzt und ist dort der Daten-Ordner, wären Einstellungen, Schlüssel,
    Chats und Gedächtnis danach weg – deshalb wird dann gar nicht gebaut.
    """
    if not ZIEL.is_dir():
        return []
    gefunden = [n for n in ("nemicli.config.json", ".env") if (ZIEL / n).exists()]
    von_selbst = {"folder_model.json", "Anleitung.txt"}     # legt NemiCLI beim Start ohne Zutun an
    sys.path.insert(0, str(ROOT / "core"))
    import paths                                            # alles, was zum Daten-Ordner gehört
    for n in dict.fromkeys(list(paths.DATEN_INHALT) + ["KI-Modelle", "Backup", "Cache", "ModelGGUF", "Vision"]):
        d = ZIEL / n
        if d.is_file():
            gefunden.append(n)
        elif d.is_dir() and any(f.name not in von_selbst and not f.name.startswith("LIES-MICH")
                                for f in d.rglob("*") if f.is_file()):
            gefunden.append(n)
    return gefunden


MOMENT_ORDNER = ROOT / "build"          # NemiCLI.spec packt die Momentaufnahme der Schutzliste von hier ein


def schutzliste_moment() -> str:
    """Schutzliste laden und als Momentaufnahme ins Bündel legen: jede exe ist ab dem ersten Start geschützt.
    Ohne Netz bleibt der bisherige Stand (einmal gesperrt, immer gesperrt)."""
    for sub in ("core", "tools"):
        if str(ROOT / sub) not in sys.path:
            sys.path.insert(0, str(ROOT / sub))
    import schutzliste
    MOMENT_ORDNER.mkdir(parents=True, exist_ok=True)
    stand = schutzliste.momentaufnahme(MOMENT_ORDNER)
    fehler = [f"{q.get('anzeige')}: {q['fehler']}" for q in stand.get("quellen", {}).values() if q.get("fehler")]
    return (f"{stand.get('eintraege', 0):,} Adressen".replace(",", ".")
            + (f" (nicht aktualisiert: {', '.join(fehler)})" if fehler else ""))


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


def signieren(liste: list[Path] | None = None) -> str:
    """Signiert die exe-Dateien mit dem Zertifikat aus dem Nutzer-Speicher.
    Mit Zeitstempel, damit die Signatur das Ablaufen des Zertifikats überdauert."""
    dateien = ",".join("'" + str(f).replace("'", "''") + "'" for f in (liste or _eigene_exe()))
    skript = "\n".join([
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false",   # sonst OEM-Codepage
        "$ErrorActionPreference='Stop'",
        r"$z = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | "
        f"Where-Object {{ $_.Subject -eq '{ZERTIFIKAT}' -and $_.NotAfter -gt (Get-Date) }} | "
        "Sort-Object NotAfter -Descending | Select-Object -First 1",
        "if (-not $z) { 'kein-zertifikat'; exit 0 }",
        f"foreach ($f in @({dateien})) {{",
        "  try { $s = Set-AuthenticodeSignature -FilePath $f -Certificate $z -HashAlgorithm SHA256 "
        f"-TimestampServer '{ZEITSTEMPEL}' }}",
        "  catch { $s = Set-AuthenticodeSignature -FilePath $f -Certificate $z -HashAlgorithm SHA256 }",
        "  $ts = if ((Get-AuthenticodeSignature $f).TimeStamperCertificate) { '' } else { '  (OHNE Zeitstempel)' }",
        '  "$($s.Status)  $(Split-Path $f -Leaf)$ts"',
        "}",
    ])
    # PSModulePath aus PowerShell 7 lässt Windows PowerShell fremde Module laden (-CodeSigningCert fehlt dann).
    umgebung = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    r = subprocess.run(["powershell", "-NoProfile", "-Command", skript], env=umgebung,
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


def msi_bauen() -> Path | None:
    """dist/NemiCLI-<Build>.msi aus dist/NemiCLI, signiert, mit .sha256 daneben.
    Die Build-Nummer kommt aus build.txt im Bau, damit MSI und exe gleich heißen."""
    _step("Windows Installer (MSI)")
    if not EXE.exists():
        print(f"  Kein Bau in {ZIEL} – erst ohne --nur-msi bauen.")
        return None
    daten = eigene_daten_im_ziel()
    if daten:
        print(f"  ABBRUCH: In {ZIEL} liegen eigene Daten ({', '.join(daten)}) – die kämen in die MSI.")
        return None
    try:
        build = (ZIEL / "NemiCLIT2" / "build.txt").read_text(encoding="utf-8").strip()
    except OSError:
        build = "0.0.0"
    import importlib.util
    if importlib.util.find_spec("msilib") is None:          # aus der Standardbibliothek entfernt ab Python 3.13
        print(f"  Übersprungen: msilib gibt es nur bis Python 3.12, gebaut wird mit Python "
              f"{sys.version_info.major}.{sys.version_info.minor}. Die exe ist fertig, "
              "die MSI braucht eine neue Bauart.")
        return None
    sys.path.insert(0, str(ROOT / "installer"))
    import msi
    paket = msi.bauen(ZIEL, DIST, build, ROOT / "nemicli.ico")
    ergebnis = signieren([paket])
    print("  " + ("unsigniert (kein Zertifikat)" if "kein-zertifikat" in ergebnis
                  else ergebnis.replace("\n", "\n  ")))
    h = hashlib.sha256(paket.read_bytes()).hexdigest()
    (DIST / f"{paket.name}.sha256").write_text(f"{h}  {paket.name}\n", encoding="utf-8")
    print(f"  {paket}  ({paket.stat().st_size / (1024**2):.0f} MB)")
    print(f"  SHA-256: {h}")
    return paket


def setup_bauen() -> Path | None:
    """dist/NemiCLI-Setup-<Build>.exe: eine Datei, die dist/NemiCLI als ZIP trägt und es in eine
    vorhandene Installation (.nemicli im Benutzerordner) aufspielt oder neu installiert (installer/nemicli_setup.py).
    Sie packt sich nur zum Installieren aus, NemiCLI selbst startet weiter aus seinem Ordner."""
    import zipfile
    _step("Setup-Datei")
    if (daten := eigene_daten_im_ziel()):
        print(f"  ABBRUCH: In {ZIEL} liegen eigene Daten ({', '.join(daten)}) – die kämen sonst ins Setup.")
        return None
    try:
        build = (ZIEL / "NemiCLIT2" / "build.txt").read_text(encoding="utf-8").strip()
    except OSError:
        print("  Kein Bau in dist/NemiCLI.")
        return None
    arbeit = ROOT / "build" / "setup"
    arbeit.mkdir(parents=True, exist_ok=True)
    paket = arbeit / "nutzlast.zip"
    sys.path.insert(0, str(ROOT / "installer"))
    from aufspielen import PROGRAMMTEILE           # das Setup nimmt nur diese an (z. B. kein KI-Modelle/)
    with zipfile.ZipFile(paket, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(ZIEL.rglob("*")):
            if f.is_file() and f.relative_to(ZIEL).parts[0] in PROGRAMMTEILE:
                z.write(f, f.relative_to(ZIEL).as_posix())
    name = f"NemiCLI-Setup-{build}"
    icon = ROOT / "nemicli.ico"
    befehl = [sys.executable, "-m", "PyInstaller", str(ROOT / "installer" / "nemicli_setup.py"),
              "--onefile", "--windowed", "--noconfirm", "--name", name,
              "--paths", str(ROOT / "installer"), "--paths", str(ROOT / "core"),
              "--add-data", f"{paket};.", "--add-data", f"{icon};.", "--icon", str(icon),
              "--distpath", str(DIST), "--workpath", str(arbeit / "werk"), "--specpath", str(arbeit)]
    for weg in ("torch", "numpy", "PIL", "tkinter", "matplotlib"):
        befehl += ["--exclude-module", weg]
    if subprocess.run(befehl, cwd=str(ROOT)).returncode != 0:
        print("  Bau der Setup-Datei fehlgeschlagen – Meldung oben lesen.")
        return None
    datei = DIST / f"{name}.exe"
    ergebnis = signieren([datei])
    print("  " + ("unsigniert (kein Zertifikat)" if "kein-zertifikat" in ergebnis
                  else ergebnis.replace("\n", "\n  ")))
    h = hashlib.sha256(datei.read_bytes()).hexdigest()
    (DIST / f"{datei.name}.sha256").write_text(f"{h}  {datei.name}\n", encoding="utf-8")
    print(f"  {datei}")
    print(f"  SHA-256: {h}")
    for weg in alte_setups_weg(DIST):
        print(f"  alte Setup-Datei entfernt: {weg}")
    zwischenstand_weg(arbeit)
    return datei


def zwischenstand_weg(arbeit: Path) -> None:
    """Arbeitsordner und Rezepte der Setup-Bauten (je Bau einige 100 MB) – entstehen bei jedem Bau neu."""
    shutil.rmtree(arbeit / "werk", ignore_errors=True)
    for spec in arbeit.glob("NemiCLI-Setup-*.spec"):
        spec.unlink(missing_ok=True)


SETUPS_BEHALTEN = 3
SETUPS_STABIL = {"NemiCLI-Setup-9.80.1048.exe"}   # sichere Stände: bleiben immer und zählen nicht mit


def _setup_version(p: Path) -> tuple[int, ...]:
    m = re.fullmatch(r"NemiCLI-Setup-([\d.]+)\.exe", p.name)
    return tuple(int(x) for x in m.group(1).split(".") if x) if m else ()


def alte_setups_weg(ordner: Path, behalten: int = SETUPS_BEHALTEN) -> list[str]:
    """Nur die neuesten Setup-Dateien (nach Versionsnummer) samt .sha256 behalten, dazu immer die aus SETUPS_STABIL."""
    setups = sorted((p for p in ordner.glob("NemiCLI-Setup-*.exe") if _setup_version(p) and p.name not in SETUPS_STABIL),
                    key=_setup_version)
    weg = []
    for p in setups[:-behalten] if behalten > 0 else []:
        for f in (p, p.with_name(p.name + ".sha256")):
            if f.is_file():
                f.unlink()
                weg.append(f.name)
    return weg


def main() -> int:
    if "--nur-setup" in sys.argv:
        return 0 if setup_bauen() else 1
    if "--nur-msi" in sys.argv:
        return 0 if msi_bauen() else 1
    t0 = time.time()
    _step("Vorprüfung")
    if not check():
        return 1
    if (gesperrt := build_gesperrt()):
        print(f"ABBRUCH: Die Build-Nummer endet auf {gesperrt} – diese Nummer wird nicht vergeben. "
              "Erst einen weiteren Commit anlegen.")
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
    _step("Schutzliste (Momentaufnahme)")
    try:
        print("  " + schutzliste_moment())
    except Exception as e:                     # kein Abbruch: die exe lädt die Listen dann beim ersten Start
        print(f"  nicht geladen ({type(e).__name__}: {e}) – die exe lädt die Listen beim ersten Start")
    if not build():
        print("\nBau fehlgeschlagen – Meldung oben lesen.")
        return 1
    if not EXE.exists():
        print("\nKeine NemiCLI.exe entstanden.")
        return 1
    _step("Chrome-Erweiterung")
    erweiterung = ZIEL / "chrome-erweiterung"          # dorthin zeigt /chrome (paths.INSTALL)
    shutil.copytree(ROOT / "chrome-erweiterung", erweiterung, dirs_exist_ok=True)
    print(f"  {erweiterung}")
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
    if "--msi" in sys.argv:
        return 0 if msi_bauen() else 1
    setup = setup_bauen()
    if setup is None:
        return 1
    print(f"\n  Weitergeben: die eine Datei {setup.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
