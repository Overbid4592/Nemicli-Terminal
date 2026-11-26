"""
llamareserve.py - llama.cpp als Reserve neben dem eigenen GGUF-Motor.

Genau ein Build ist aktiv (`aktiv()`, ohne Eintrag der Grund-Build BUILD mit Hashes im Code). Die
offiziellen Windows-Pakete kommen in die Ablage: von Hand oder über `holen()` – nur von GitHub (HOSTS),
Größe und SHA-256 aus dem Katalog des Builds, fortsetzbar; welches Paket, entscheidet die Grafikkarte
(`passende_pakete`). `einrichten()` prüft jedes Paket erneut, entpackt nur die gewählte Art samt ihrer
CUDA-Laufzeit (kein Pfad aus dem Archiv heraus) und hält die Hashes aller entpackten Dateien in der Prüfliste
fest. Vor jedem Start werden die Dateien gegen diese Liste geprüft.

`aktualisieren()` fragt GitHub nach dem neuesten Build (Katalog mit den SHA-256 aus den Release-Daten),
lädt und richtet ihn neben dem aktiven ein, prüft per `--help`, dass er jeden festen Schalter kennt, und
schaltet ihn erst dann ein. Höchstens BEHALTEN Builds bleiben, ältere wandern in den Papierkorb.

Der Server startet nur mit festen Schaltern (SCHALTER): 127.0.0.1, freier Port, zufälliger
Schlüssel, ohne Web-Oberfläche, ohne Jinja-Vorlage, ohne Werkzeuge, ohne Slot-Endpunkt, ohne
mmproj, offline. RPC, Slot-Speicher und Schlafmodus bleiben aus (Standard; mehrere CVEs dort).
Kontext „max“: -c 0 mit --fit-ctx/--fit-target (llama.cpp kürzt den Kontext, statt Schichten auszulagern);
Gesprächsspeicher f16, q8_0 oder q4_0 (`kv_stufe`), quantisiert mit Flash Attention. Schalter, die ein
Build nicht kennt (ZUSATZ, laut --help), fallen weg – dann gilt das frühere Verhalten (Kontext höchstens
OHNE_FIT_CTX).
Die Prompts kommen als fertige Token-Folgen aus ggufengine.chat (llamamotor.py). Hauptprogramm und Wache
teilen sich einen Server je Modell (Zettel im Cache, Schlüssel mit DPAPI verschlüsselt).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

BUILD = "b12199"                       # Grund-Build: Pakete und Hashes stehen fest im Code
RELEASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/tag/{BUILD}"
# Offizielle Windows-x64-Pakete des Grund-Builds mit der SHA-256 aus dem Release.
PAKETE = {
    f"llama-{BUILD}-bin-win-cpu-x64.zip": "1cde1df073c2526ef3ad34a5b2884998802b837ed4b965a36d0af2920f10a1ae",
    f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip": "3c80120dc7763d67d0e882dda1545fbf626b2db471270030731cca4913cfc228",
    f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip": "1cb9b561b0d62b3ecc48ecbff2003fa656bdbbf71bead919ae92110df7f961a5",
    f"llama-{BUILD}-bin-win-vulkan-x64.zip": "7bb426133c32a424b2b74a14d6dae6aaf7ff56a742f1a93af0bc34dd6f224ce9",
    f"llama-{BUILD}-bin-win-rocm-10.0-x64.zip": "89a1498d553981c9075d8668086cf22ae748c980e2391d2181bca5a1788b2b77",
    f"llama-{BUILD}-bin-win-sycl-x64.zip": "c3fac08ec4cb7fda1a9b6e4a60791df3cede47feb64de26db11165f89f9976a4",
    "cudart-llama-bin-win-cuda-12.4-x64.zip": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
    "cudart-llama-bin-win-cuda-13.5-x64.zip": "c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba",
}
GROESSE = {
    f"llama-{BUILD}-bin-win-cpu-x64.zip": 24758741,
    f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip": 288734574,
    f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip": 170781058,
    f"llama-{BUILD}-bin-win-vulkan-x64.zip": 37715448,
    f"llama-{BUILD}-bin-win-rocm-10.0-x64.zip": 267336186,
    f"llama-{BUILD}-bin-win-sycl-x64.zip": 156077103,
    "cudart-llama-bin-win-cuda-12.4-x64.zip": 391443627,
    "cudart-llama-bin-win-cuda-13.5-x64.zip": 426314199,
}
DOWNLOAD_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{build}/{name}"
# GitHub leitet Release-Dateien auf seinen Speicher um; andere Ziele werden nicht angefragt.
HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
MAX_UMLEITUNGEN = 5
# Release-Liste ohne Anmeldung (60 Abfragen je Stunde); keine Umleitung, nur dieser Host.
API_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
API_MAX = 16 << 20
MAX_PAKET = 2 << 30
BEHALTEN = 3                           # eingerichtete Builds, der aktive zählt mit
_TAG = re.compile(r"^b(\d{1,7})$")
# Windows-x64-Pakete, die ein Katalog enthalten darf: (Build, Art) bzw. (CUDA-Laufzeit)
_PAKET = re.compile(r"^(?:llama-(b\d{1,7})-bin-win-(cpu|vulkan|cuda-\d{1,2}\.\d{1,2})"
                    r"|cudart-llama-bin-win-(cuda-\d{1,2}\.\d{1,2}))-x64\.zip$")
# Paket-Arten des Grund-Builds; "auto" wählt nach der Grafikkarte
ARTEN = {"cuda-13.5": f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip",
         "cuda-12.4": f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip",
         "vulkan": f"llama-{BUILD}-bin-win-vulkan-x64.zip",
         "cpu": f"llama-{BUILD}-bin-win-cpu-x64.zip"}
# CUDA-Paket -> passende Laufzeit (ohne sie startet der CUDA-Teil nicht)
LAUFZEIT = {f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip": "cudart-llama-bin-win-cuda-12.4-x64.zip",
            f"llama-{BUILD}-bin-win-cuda-13.5-x64.zip": "cudart-llama-bin-win-cuda-13.5-x64.zip"}
SERVER = "llama-server.exe"
MAX_ENTPACKT = 8 << 30                 # alle Dateien zusammen
MAX_DATEIEN = 2000
START_WARTEN_S = 600                   # Laden großer Modelle von der Platte
STOPP_WARTEN_S = 10
# Kontext „max“ (nicht eingestellt): -c 0, --fit kürzt den Kontext bis FIT_CTX, bevor Schichten auf die CPU gehen.
# Ein eingestellter Kontext bleibt -c N (--fit verschiebt dann nur Schichten).
FIT_CTX = 4096
FIT_ZIEL_MIB = 1536                    # frei bleibender Grafikspeicher je Gerät (wie RESERVE im eigenen Motor)
OHNE_FIT_CTX = 32768                   # Kontext „max“ bei Builds ohne --fit-ctx (kein Kürzen durch llama.cpp)
# Gesprächsspeicher (KV) des Servers: nur gleiche Paare für K und V (die CUDA-Kerne der offiziellen Pakete
# haben Flash Attention nur dafür). Anteil am f16-Platz: q8_0 34 Byte, q4_0 18 Byte je 32 Werte.
KV_ARTEN = ("f16", "q8_0", "q4_0")
KV_WAHL = ("auto", *KV_ARTEN)
KV_ANTEIL = {"f16": (1, 1), "q8_0": (17, 32), "q4_0": (9, 32)}
KV_BITS = {"f16": 16, "q8_0": 8, "q4_0": 4}
# Startfehler, nach denen der KV einmal auf f16 zurückgeht (quantisierter V-Cache braucht Flash Attention)
_FA_FEHLER = re.compile(r"(?i)quantized v cache|requires flash attention|flash attention not supported"
                        r"|flash[ _-]?attn[^\n]*(?:not supported|unsupported|failed)")


def _daten() -> Path:
    try:
        from paths import DATEN
        return Path(DATEN)
    except Exception:
        return Path(os.environ.get("TEMP") or ".") / "nemicli_daten"


def ablage() -> Path:
    """Hier legt man die heruntergeladenen Pakete ab."""
    return _daten() / "Motoren" / "llama.cpp"


def _nummer(build: str) -> int:
    m = _TAG.match(build or "")
    return int(m.group(1)) if m else -1


def _aktiv_datei() -> Path:
    return ablage() / "aktiv.txt"


def aktiv() -> str:
    """Der eingeschaltete Build; ohne gültigen Eintrag der Grund-Build."""
    try:
        build = _aktiv_datei().read_text(encoding="utf-8").strip()
    except OSError:
        return BUILD
    return build if _TAG.match(build) else BUILD


def ziel(build: str | None = None) -> Path:
    return ablage() / (build or aktiv())


def _pruefliste(build: str | None = None) -> Path:
    return ziel(build) / "pruefliste.json"


def _katalog_datei(build: str) -> Path:
    return ablage() / f"{build}.pakete.json"


def _katalog_pruefen(build: str, roh) -> dict[str, tuple[str, int]]:
    """Nur Windows-x64-Pakete dieses Builds (und CUDA-Laufzeiten) mit gültiger SHA-256 und Größe."""
    out: dict[str, tuple[str, int]] = {}
    if not isinstance(roh, dict):
        return out
    for name, wert in roh.items():
        m = _PAKET.match(str(name))
        if not m or (m.group(1) and m.group(1) != build):
            continue
        try:
            sha, groesse = str(wert[0]).lower(), int(wert[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if re.fullmatch(r"[0-9a-f]{64}", sha) and 0 < groesse <= MAX_PAKET:
            out[str(name)] = (sha, groesse)
    return out


def katalog(build: str | None = None) -> dict[str, tuple[str, int]]:
    """Paketname -> (SHA-256, Größe): Grund-Build aus dem Code, neuere aus der GitHub-Abfrage beim Aktualisieren."""
    build = build or aktiv()
    if build == BUILD:
        return {n: (PAKETE[n], GROESSE[n]) for n in PAKETE if n in GROESSE}
    try:
        roh = json.loads(_katalog_datei(build).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return _katalog_pruefen(build, roh)


def arten(build: str | None = None) -> dict[str, str]:
    """Paket-Art (cpu, vulkan, cuda-X.Y) -> Paketname des Builds."""
    build = build or aktiv()
    out = {}
    for name in katalog(build):
        m = _PAKET.match(name)
        if m and m.group(1) == build:
            out[m.group(2)] = name
    return out


def _laufzeit(haupt: str, kat: dict) -> str | None:
    """Die CUDA-Laufzeit zu einem CUDA-Paket, wenn der Katalog sie hat (ohne sie startet der CUDA-Teil nicht)."""
    m = _PAKET.match(haupt)
    if m and m.group(2) and m.group(2).startswith("cuda-"):
        rt = f"cudart-llama-bin-win-{m.group(2)}-x64.zip"
        return rt if rt in kat else None
    return None


def _version(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(t) for t in str(text).split(".")[:2])
    except ValueError:
        return (0,)


class ReserveFrage(RuntimeError):
    """Der eigene Motor kann das Modell nicht laden, llama.cpp ist eingerichtet: erst fragen."""

    def __init__(self, name: str, grund: str):
        self.name, self.grund = name, grund
        super().__init__(f"Der eigene Motor kann {name} nicht laden: {grund}")

    @property
    def frage(self) -> str:
        return (f"Der eigene Motor kann {self.name} nicht laden ({self.grund}).\n"
                f"Soll die Reserve llama.cpp {aktiv()} das Modell übernehmen?")


# ---------------------------------------------------------------------------
# Prüfen und Einrichten
# ---------------------------------------------------------------------------
def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(4 << 20), b""):
            h.update(stueck)
    return h.hexdigest()


def pakete_in_ablage() -> list[Path]:
    ordner = ablage()
    if not ordner.is_dir():
        return []
    return sorted(p for p in ordner.iterdir() if p.is_file() and p.suffix.lower() == ".zip")


def passende_pakete(profil=None, art: str = "auto", build: str | None = None) -> tuple[list[str], str]:
    """Pakete für diesen Rechner und warum. NVIDIA: das CUDA-Paket mit der höchsten Version, die der
    Treiber kann – Blackwell (Rechen-Stufe 12) erst ab CUDA 12.8, ältere Builds kennen sie nicht; sonst
    Vulkan. Andere Grafikkarten: Vulkan; ohne Grafikkarte: CPU."""
    build = build or aktiv()
    kat, wahl = katalog(build), arten(build)
    if art != "auto":
        if art not in wahl:
            raise ValueError(f"Unbekannte Paket-Art {art!r} (möglich: auto, {', '.join(sorted(wahl))})")
        haupt = wahl[art]
        return [n for n in (_laufzeit(haupt, kat), haupt) if n], f"ausdrücklich gewählt: {art}"
    if profil is None:
        try:
            import systemprofil
            profil = systemprofil.erkennen()
        except Exception:
            profil = None
    nv = getattr(profil, "nvidia", None) if profil is not None else None
    if nv is not None:
        cuda = float(getattr(profil, "cuda_max", 0.0) or 0.0)
        try:
            stufe = float(nv.cuda_cap or 0)
        except ValueError:
            stufe = 0.0
        treiber = _version(f"{cuda:g}")
        cudas = sorted((a for a in wahl if a.startswith("cuda-")), key=lambda a: _version(a[5:]), reverse=True)
        passend = [a for a in cudas if _version(a[5:]) <= treiber and (stufe < 12 or _version(a[5:]) >= (12, 8))]
        if passend:
            art, grund = passend[0], f"{nv.name}, Treiber kann CUDA {cuda:g}"
        else:
            art, grund = "vulkan", f"{nv.name}, Treiber kann nur CUDA {cuda:g} – Vulkan läuft trotzdem auf der Karte"
    elif profil is not None and getattr(profil, "haupt_gpu", None) is not None:
        art, grund = "vulkan", f"{profil.haupt_gpu.name} (keine NVIDIA)"
    else:
        art, grund = "cpu", "keine Grafikkarte erkannt"
    haupt = wahl.get(art) or wahl.get("cpu")
    if haupt is None:
        raise ValueError(f"Kein passendes Paket für llama.cpp {build}")
    return [n for n in (_laufzeit(haupt, kat), haupt) if n], grund


class Abbruch(Exception):
    pass


def _host_erlaubt(url) -> bool:
    return url.scheme == "https" and url.host in HOSTS


def _laden(name: str, build: str, kat: dict, fortschritt=None, stopp: threading.Event | None = None) -> str:
    """Ein Paket in die Ablage laden (.part wird fortgesetzt), Größe und SHA-256 prüfen, erst dann ablegen."""
    import httpx
    soll, groesse = kat[name]
    ziel_datei = ablage() / name
    if ziel_datei.is_file() and ziel_datei.stat().st_size == groesse and sha256(ziel_datei) == soll:
        return f"✅ {name}: schon da, SHA-256 stimmt"
    teil = ablage() / (name + ".part")
    start = teil.stat().st_size if teil.is_file() else 0
    if start >= groesse:
        teil.unlink()
        start = 0
    url = httpx.URL(DOWNLOAD_URL.format(build=build, name=name))
    kopf = {"User-Agent": "NemiCLI-llamacpp-Reserve"}
    with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False) as c:
        for _ in range(MAX_UMLEITUNGEN + 1):
            if not _host_erlaubt(url):
                raise OSError(f"{name}: Umleitung zu {url.host} – nur GitHub ist erlaubt, abgebrochen")
            bereich = {"Range": f"bytes={start}-"} if start else {}
            with c.stream("GET", url, headers=kopf | bereich) as r:
                if r.is_redirect:
                    url = url.join(r.headers.get("location", ""))
                    continue
                if r.status_code == 200:
                    start = 0                        # setzt nicht fort: von vorn
                elif r.status_code != 206:
                    raise OSError(f"{name}: GitHub antwortet mit {r.status_code}")
                with open(teil, "ab" if start else "wb") as f:
                    erledigt = start
                    for stueck in r.iter_bytes(1 << 20):
                        if stopp is not None and stopp.is_set():
                            raise Abbruch()
                        erledigt += len(stueck)
                        if erledigt > groesse:
                            raise OSError(f"{name}: größer als angegeben – verworfen")
                        f.write(stueck)
                        if fortschritt is not None:
                            fortschritt(name, erledigt, groesse)
            break
        else:
            raise OSError(f"{name}: zu viele Umleitungen")
    if teil.stat().st_size != groesse or sha256(teil) != soll:
        teil.unlink(missing_ok=True)
        raise OSError(f"{name}: Größe oder SHA-256 stimmen nicht – die Datei wurde verworfen")
    teil.replace(ziel_datei)
    return f"✅ {name}: geladen, SHA-256 stimmt"


def holen(art: str = "auto", fortschritt=None, stopp: threading.Event | None = None,
          profil=None, build: str | None = None) -> tuple[bool, list[str]]:
    """Passende Pakete von GitHub laden und einrichten. (eingerichtet, Zeilen für die Anzeige)."""
    build = build or aktiv()
    kat = katalog(build)
    try:
        namen, grund = passende_pakete(profil, art, build)
    except ValueError as exc:
        return False, [f"❌ {exc}"]
    zeilen = [f"🦙 llama.cpp {build}: {', '.join(namen)} ({grund})"]
    ablage().mkdir(parents=True, exist_ok=True)
    noetig = sum(kat[n][1] for n in namen) * 3       # Paket, Prüfkopie beim Entpacken, Entpacktes
    frei = shutil.disk_usage(ablage()).free
    if frei < noetig:
        zeilen.append(f"❌ Zu wenig Platz: frei {frei / 2**30:.1f} GB, nötig etwa {noetig / 2**30:.1f} GB")
        return False, zeilen
    try:
        for n in namen:
            zeilen.append(_laden(n, build, kat, fortschritt, stopp))
    except Abbruch:
        zeilen.append("⏹ Abgebrochen – der angefangene Teil bleibt liegen und wird beim nächsten Mal fortgesetzt.")
        return False, zeilen
    except Exception as exc:
        zeilen.append(f"❌ {exc}")
        return False, zeilen
    ok, mehr = einrichten(build, art, profil)
    return ok, zeilen + mehr


def _sicherer_name(name: str) -> Path | None:
    """Relativer Pfad eines Archiv-Eintrags, oder None, wenn er aus dem Ziel herausführt."""
    teile = name.replace("\\", "/").split("/")
    if not name or name.startswith("/") or ":" in name or any(t in ("..",) for t in teile):
        return None
    teile = [t for t in teile if t not in ("", ".")]
    return Path(*teile) if teile else None


def _entpacken(paket: Path, nach: Path, bisher: int) -> int:
    """Entpackt ein geprüftes Paket nach `nach`; gibt die Bytes zurück. Wirft bei unsicheren Einträgen."""
    summe = bisher
    with zipfile.ZipFile(paket) as z:
        eintraege = [e for e in z.infolist() if not e.is_dir()]
        if len(eintraege) > MAX_DATEIEN:
            raise ValueError(f"{paket.name}: zu viele Dateien ({len(eintraege)})")
        for e in eintraege:
            rel = _sicherer_name(e.filename)
            if rel is None:
                raise ValueError(f"{paket.name}: unsicherer Pfad im Archiv: {e.filename!r}")
            summe += e.file_size
            if summe > MAX_ENTPACKT:
                raise ValueError(f"{paket.name}: entpackt größer als {MAX_ENTPACKT >> 30} GB")
            aus = nach / rel
            aus.parent.mkdir(parents=True, exist_ok=True)
            with z.open(e) as quelle, open(aus, "wb") as senke:
                kopiert = 0
                for stueck in iter(lambda: quelle.read(1 << 20), b""):
                    kopiert += len(stueck)
                    if kopiert > e.file_size:
                        raise ValueError(f"{paket.name}: {e.filename} größer als angegeben")
                    senke.write(stueck)
    return summe


def _server_in(ordner: Path) -> Path | None:
    treffer = sorted(ordner.rglob(SERVER), key=lambda p: len(p.parts))
    return treffer[0] if treffer else None


def _haupt_waehlen(haupt: list[Path], art: str, profil, build: str) -> Path:
    """Das eine llama-Paket, das entpackt wird: die Art aus passende_pakete (`art`, „auto“ nach der
    Grafikkarte); liegt sie nicht in der Ablage, die beste vorhandene (CUDA neueste, dann Vulkan, dann CPU)."""
    if len(haupt) == 1 and art == "auto":
        return haupt[0]
    try:
        gewaehlt = passende_pakete(profil, art, build)[0]
    except ValueError:
        gewaehlt = []
    for p in haupt:
        if p.name in gewaehlt:
            return p

    def rang(p: Path):
        m = _PAKET.match(p.name)
        a = (m.group(2) if m else "") or ""
        if a.startswith("cuda-"):
            return (0, tuple(-x for x in _version(a[5:])))
        return ({"vulkan": 1, "cpu": 2}.get(a, 3), ())
    return min(haupt, key=rang)


def einrichten(build: str | None = None, art: str = "auto", profil=None) -> tuple[bool, list[str]]:
    """Prüft die Pakete in der Ablage und entpackt die gewählte Art samt ihrer CUDA-Laufzeit; andere geprüfte
    Pakete werden übersprungen (ein Ordner, eine Art). (eingerichtet, Zeilen für die Anzeige)."""
    build = build or aktiv()
    kat = katalog(build)
    zeilen: list[str] = []
    gut: list[Path] = []
    for p in pakete_in_ablage():
        eintrag = kat.get(p.name)
        if eintrag is None:
            m = _PAKET.match(p.name)
            anderer = m is not None and (m.group(3) or (m.group(1) and ziel(m.group(1)).is_dir()))
            if not anderer:                          # Pakete anderer eingerichteter Builds: still übergehen
                zeilen.append(f"⏭ {p.name}: gehört nicht zu {build} – übersprungen")
            continue
        soll = eintrag[0]
        ist = sha256(p)
        if ist != soll:
            zeilen.append(f"❌ {p.name}: SHA-256 stimmt nicht ({ist[:12]}… statt {soll[:12]}…) – nicht entpackt")
            continue
        zeilen.append(f"✅ {p.name}: SHA-256 stimmt")
        gut.append(p)
    haupt = [p for p in gut if p.name.startswith("llama-")]
    if not haupt:
        zeilen.append(f"Kein passendes llama.cpp-Paket in {ablage()}.")
        return False, zeilen
    wahl = _haupt_waehlen(haupt, art, profil, build)
    m = _PAKET.match(wahl.name)
    wahl_art = (m.group(2) if m else "") or ""
    rt = f"cudart-llama-bin-win-{wahl_art}-x64.zip" if wahl_art.startswith("cuda-") else None
    if rt is not None and rt not in {p.name for p in gut}:
        zeilen.append(f"⚠ Zu {wahl.name} fehlt {rt} – ohne sie läuft nur die CPU.")
    nehmen = [p for p in gut if p == wahl or p.name == rt]
    zeilen += [f"⏭ {p.name}: andere Art als {wahl_art} – übersprungen" for p in gut if p not in nehmen]
    namen = {p.name for p in nehmen}
    neu = ablage() / f"{build}.neu"
    shutil.rmtree(neu, ignore_errors=True)
    try:
        neu.mkdir(parents=True)
        summe = 0
        for p in sorted(nehmen, key=lambda x: x.name.startswith("llama-")):   # Laufzeit zuerst, Paket überschreibt
            summe = _entpacken(p, neu, summe)
        server = _server_in(neu)
        if server is None:
            raise ValueError(f"{SERVER} fehlt in den Paketen")
        dateien = {str(f.relative_to(neu)).replace("\\", "/"): sha256(f) for f in sorted(neu.rglob("*"))
                   if f.is_file()}
        liste = {"build": build, "pakete": sorted(namen), "server": str(server.relative_to(neu)).replace("\\", "/"),
                 "dateien": dateien}
        (neu / "pruefliste.json").write_text(json.dumps(liste, indent=1), encoding="utf-8")
        if build == aktiv():
            _beenden_alle()
        alt = ziel(build)
        weg = ablage() / f"{build}.alt"
        shutil.rmtree(weg, ignore_errors=True)
        if alt.exists():
            alt.replace(weg)                         # gesperrte Datei: scheitert ganz, nichts halb gelöscht
        try:
            neu.replace(alt)
        except OSError:
            if weg.exists() and not alt.exists():
                weg.replace(alt)                     # alten Stand zurück
            raise
        shutil.rmtree(weg, ignore_errors=True)
    except Exception as exc:
        shutil.rmtree(neu, ignore_errors=True)
        zeilen.append(f"❌ Einrichten abgebrochen: {exc}")
        return False, zeilen
    _GEPRUEFT.clear()
    zeilen.append(f"✅ llama.cpp {build} eingerichtet ({len(dateien)} Dateien, {summe / 2**20:.0f} MB).")
    return True, zeilen


_GEPRUEFT: dict[tuple[str, str], tuple[int, int]] = {}   # (Build, Datei) -> (Größe, Zeit) beim letzten Hash
_PRUEF_LOCK = threading.Lock()


def _liste(build: str | None = None) -> dict | None:
    build = build or aktiv()
    if not _TAG.match(build):
        return None
    try:
        daten = json.loads(_pruefliste(build).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(daten, dict) or daten.get("build") != build or not isinstance(daten.get("dateien"), dict):
        return None
    return daten


def pruefen(build: str | None = None) -> str | None:
    """None, wenn llama.cpp startbereit ist und jede Datei zur Prüfliste passt; sonst der Grund.
    Unveränderte Dateien (gleiche Größe und Zeit) werden nicht erneut gehasht."""
    build = build or aktiv()
    daten = _liste(build)
    if daten is None:
        return f"llama.cpp {build} ist nicht eingerichtet"
    basis = ziel(build)
    server = daten.get("server")
    if server not in daten["dateien"]:
        return f"{SERVER} fehlt in der Prüfliste"
    with _PRUEF_LOCK:
        vorhanden = {str(f.relative_to(basis)).replace("\\", "/") for f in basis.rglob("*")
                     if f.is_file() and f.name != "pruefliste.json"}
        fremd = vorhanden - set(daten["dateien"])
        if fremd:
            return f"fremde Datei im llama.cpp-Ordner: {sorted(fremd)[0]}"
        for rel, soll in daten["dateien"].items():
            pfad = basis / rel
            try:
                st = pfad.stat()
            except OSError:
                return f"Datei fehlt: {rel}"
            stempel = (st.st_size, st.st_mtime_ns)
            if _GEPRUEFT.get((build, rel)) == stempel:
                continue
            if sha256(pfad) != soll:
                _GEPRUEFT.pop((build, rel), None)
                return f"Datei verändert: {rel}"
            _GEPRUEFT[(build, rel)] = stempel
    return None


def eingerichtet() -> bool:
    return _liste() is not None


def bereit() -> bool:
    return pruefen() is None


def server_pfad(build: str | None = None) -> Path:
    daten = _liste(build) or {}
    return ziel(build) / str(daten.get("server") or SERVER)


def installiert() -> list[str]:
    """Eingerichtete Builds (mit Prüfliste), neuester zuerst."""
    try:
        namen = [p.name for p in ablage().iterdir() if p.is_dir() and _TAG.match(p.name)]
    except OSError:
        return []
    return sorted((b for b in namen if _liste(b) is not None), key=_nummer, reverse=True)


def ist_geprueft(exe) -> bool:
    """Ist `exe` der llama-server eines eingerichteten Builds, jede Datei laut Prüfliste unverändert?"""
    try:
        pfad = os.path.normcase(str(Path(exe).resolve()))
        for build in installiert():
            if os.path.normcase(str(server_pfad(build).resolve())) == pfad:
                return pruefen(build) is None
    except (OSError, ValueError):
        pass
    return False


def status_zeilen() -> list[str]:
    build = aktiv()
    grund = pruefen()
    zeilen = [f"🦙 llama.cpp-Reserve · aktiv: {build}"]
    if grund is None:
        daten = _liste() or {}
        zeilen.append(f"✅ bereit · Pakete: {', '.join(daten.get('pakete', []))}")
        andere = [b for b in installiert() if b != build]
        if andere:
            zeilen.append(f"Weitere eingerichtet: {', '.join(andere)} – /llamacpp wechseln <build>")
        zeilen.append("/llamacpp aktualisieren holt den neuesten Build von GitHub (geprüft, mit Probelauf).")
    else:
        zeilen.append(f"⏸ {grund}")
        url = f"https://github.com/ggml-org/llama.cpp/releases/tag/{build}"
        zeilen += ["So richtest du es ein:",
                   "  /llamacpp holen – NemiCLI wählt das Paket für deine Grafikkarte, lädt es von GitHub, "
                   "prüft jede SHA-256 und richtet es ein",
                   f"  oder von Hand: Pakete von {url} nach {ablage()} legen, dann /llamacpp einrichten"]
    zeilen.append("Schalter: nur 127.0.0.1, zufälliger Schlüssel, ohne Web-Oberfläche, Jinja, Werkzeuge, RPC, "
                  "Slot-Speicher und Schlafmodus.")
    return zeilen


# ---------------------------------------------------------------------------
# Aktualisieren
# ---------------------------------------------------------------------------
def neuester() -> tuple[str, dict[str, tuple[str, int]]]:
    """Neuester Build mit Windows-Paketen laut GitHub: (Build, Katalog). Die SHA-256 stammt aus den
    Release-Daten (digest), die Größe ebenso."""
    import httpx
    kopf = {"User-Agent": "NemiCLI-llamacpp-Reserve", "Accept": "application/vnd.github+json"}
    roh = bytearray()
    with httpx.Client(timeout=httpx.Timeout(30.0), follow_redirects=False) as c:
        with c.stream("GET", API_URL, headers=kopf) as r:
            if r.status_code != 200:
                raise OSError(f"GitHub antwortet mit {r.status_code}")
            for stueck in r.iter_bytes(1 << 16):
                roh += stueck
                if len(roh) > API_MAX:
                    raise OSError("Antwort von GitHub zu groß")
    daten = json.loads(bytes(roh))
    for rel in daten if isinstance(daten, list) else []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = str(rel.get("tag_name") or "")
        if not _TAG.match(tag):
            continue
        roh_kat = {}
        for a in rel.get("assets") or []:
            digest = str(a.get("digest") or "") if isinstance(a, dict) else ""
            if digest.startswith("sha256:"):
                roh_kat[str(a.get("name"))] = (digest[7:], a.get("size"))
        kat = _katalog_pruefen(tag, roh_kat)
        if any(_PAKET.match(n).group(1) == tag for n in kat):
            return tag, kat
    raise OSError("GitHub nennt keinen Build mit Windows-Paketen")


# Schalter, die nur neuere Builds kennen: fehlen sie dem aktiven Build (--help), gilt das alte Verhalten
# (-c mit der Modelllänge, höchstens OHNE_FIT_CTX; KV f16) statt eines Startfehlers.
ZUSATZ = ("--fit-ctx", "--fit-target", "--cache-type-k", "--cache-type-v", "--flash-attn")
# Was der Server laut --help kennen muss: die festen Schalter, die Startwerte und der Schlüssel aus der Umgebung
PFLICHT = ("-m", "--port", "-c", "--mmproj", "LLAMA_API_KEY", *ZUSATZ)


def _hilfe(server: Path) -> str:
    """Ausgabe von `llama-server --help` (wirft OSError/SubprocessError)."""
    r = subprocess.run([str(server), "--help"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, cwd=str(server.parent),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (r.stdout or "") + (r.stderr or "")


def _kennt(text: str, schalter: str) -> bool:
    if not re.search(rf"(?<![\w-]){re.escape(schalter)}(?![\w-])", text):
        return False
    if schalter == "--flash-attn":           # ältere Builds: Schalter ohne Wert (on|off fehlt)
        return bool(re.search(r"--flash-attn\b[^\n]*on\|off", text))
    return True


def probelauf(build: str) -> list[str]:
    """Kennt der Server dieses Builds jeden festen Schalter? Leere Liste = ja. Ruft nur --help auf."""
    if (grund := pruefen(build)) is not None:
        return [grund]
    try:
        text = _hilfe(server_pfad(build))
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"Start fehlgeschlagen ({exc})"]
    noetig = dict.fromkeys(s for s in (*SCHALTER, *OHNE_BILDTEIL, *PFLICHT) if s.startswith("-") or s.isupper())
    return [s for s in noetig if not _kennt(text, s)]


_KANN: dict[tuple, frozenset] = {}


def kann(build: str | None = None) -> frozenset:
    """Die ZUSATZ-Schalter, die der Server dieses Builds laut --help kennt (je Server-Datei gemerkt, auch ein
    Fehlschlag – sonst liefe vor jedem Start erneut --help bis zur Zeitgrenze). Ohne Antwort: keiner."""
    server = server_pfad(build)
    try:
        st = server.stat()
    except OSError:
        return frozenset()
    schluessel = (os.path.normcase(str(server)), st.st_size, st.st_mtime_ns)
    if schluessel not in _KANN:
        try:
            text = _hilfe(server)
        except (OSError, subprocess.SubprocessError):
            text = ""
        _KANN[schluessel] = frozenset(s for s in ZUSATZ if _kennt(text, s))
    return _KANN[schluessel]


def kv_stufe(wahl: str, pro_token_f16: int | None, ctx: int, platz: int | None, mindest: int = FIT_CTX) -> str:
    """KV-Typ für llama-server. Ausdrücklich gewählt (f16, q8_0, q4_0): der. „auto“ – feste Regel aus
    KV-Bytes je Token (f16), gewünschtem Kontext und freiem Grafikspeicher neben Gewichten und Reserve
    (`platz`): passt f16 für `ctx` → f16, sonst q8_0; q4_0 erst, wenn auch q8_0 `mindest` nicht schafft.
    Ohne Angaben: f16."""
    wahl = str(wahl or "auto").lower()
    if wahl in KV_ARTEN:
        return wahl
    if not pro_token_f16 or platz is None or ctx <= 0:
        return "f16"

    def braucht(art: str, n: int) -> int:
        z, nenner = KV_ANTEIL[art]
        return int(pro_token_f16) * z // nenner * int(n)
    if braucht("f16", ctx) <= platz:
        return "f16"
    if braucht("q8_0", min(mindest, ctx)) <= platz:
        return "q8_0"
    return "q4_0"


def aktivieren(build: str) -> str:
    """Einen eingerichteten Build einschalten; laufende Server dieses Prozesses enden (der nächste startet neu).
    Geteilte Server anderer NemiCLI-Prozesse bekommen im Zettel „wechseln“: sie beenden ihn vor der nächsten
    Anfrage und starten mit dem neuen Build."""
    if (grund := pruefen(build)) is not None:
        raise ValueError(grund)
    _beenden_alle()
    neu = _aktiv_datei().with_suffix(".tmp")
    neu.write_text(build, encoding="utf-8")
    neu.replace(_aktiv_datei())
    _wechsel_melden()
    return f"✅ llama.cpp {build} ist jetzt aktiv."


def _wechsel_melden() -> None:
    try:
        zettel = list(_ordner().glob("geteilt_*.json"))
    except OSError:
        return
    for pfad in zettel:
        try:
            with _Sperre(pfad.with_suffix(".zettel.lock"), 10.0):
                if pfad.stat().st_size > ZETTEL_MAX:
                    continue
                z = json.loads(pfad.read_text(encoding="utf-8"))
                if isinstance(z, dict) and z.get("server_pid") and not z.get("wechseln"):
                    z["wechseln"] = True
                    _datei_schreiben(pfad, z)
        except (OSError, ValueError, RuntimeError):
            continue


def aufraeumen() -> list[str]:
    """Höchstens BEHALTEN Builds bleiben, der aktive immer; ältere samt Paketen in den Papierkorb."""
    import snapshot
    jetzt = aktiv()
    try:
        builds = sorted({p.name for p in ablage().iterdir() if p.is_dir() and _TAG.match(p.name)},
                        key=_nummer, reverse=True)
    except OSError:
        return []
    behalten = [b for b in builds if b == jetzt]
    for b in builds:
        if len(behalten) >= BEHALTEN:
            break
        if b not in behalten:
            behalten.append(b)
    zeilen = []
    for b in builds:
        if b in behalten:
            continue
        for stueck in (ziel(b), _katalog_datei(b), *ablage().glob(f"llama-{b}-bin-*.zip")):
            if stueck.exists():
                snapshot.sichern(stueck, "llamacpp_aufraeumen", verschieben=True)
        zeilen.append(f"⚠ {b}: konnte nicht in den Papierkorb" if ziel(b).exists()
                      else f"🗑 llama.cpp {b} liegt im Papierkorb (/undo holt ihn zurück).")
    return zeilen


def aktualisieren(fortschritt=None, stopp: threading.Event | None = None,
                  profil=None) -> tuple[bool, list[str]]:
    """Neuesten Build holen und einrichten, Probelauf, einschalten, aufräumen. (aktiv, Zeilen)."""
    alt = aktiv()
    try:
        neu, kat = neuester()
    except Exception as exc:
        return False, [f"❌ GitHub-Abfrage: {exc}"]
    if _nummer(neu) <= _nummer(alt) and bereit():
        return True, [f"✅ llama.cpp {alt} ist schon der neueste Build."]
    zeilen = [f"🦙 Neuer Build: {neu} (aktiv: {alt})"]
    if neu not in installiert():
        try:
            ablage().mkdir(parents=True, exist_ok=True)
            _katalog_datei(neu).write_text(json.dumps({n: list(w) for n, w in kat.items()}, indent=1),
                                           encoding="utf-8")
        except OSError as exc:
            return False, zeilen + [f"❌ {exc}"]
        ok, mehr = holen("auto", fortschritt, stopp, profil, build=neu)
        zeilen += mehr
        if not ok:
            return False, zeilen + [f"Es bleibt bei {alt}."]
    fehlt = probelauf(neu)
    if fehlt:
        return False, zeilen + [f"❌ Probelauf: {neu} kennt {', '.join(fehlt)} nicht – es bleibt bei {alt}."]
    zeilen.append(f"✅ Probelauf: {neu} kennt alle festen Schalter.")
    zeilen.append(aktivieren(neu))
    zeilen += aufraeumen()
    return True, zeilen


# ---------------------------------------------------------------------------
# Server-Prozess
# ---------------------------------------------------------------------------
# Feste Schalter; nur Modell, Kontext, Port, KV-Typ und --fit-Grenzen kommen dazu. Der Schlüssel geht über
# die Umgebung, nicht über die Kommandozeile.
# Zwei Plätze, ein gemeinsamer KV-Puffer (--kv-unified) der Größe n_ctx: Hauptprogramm und Wache haben je
# einen eigenen Präfix-Cache, jeder Platz darf bis n_ctx lang werden – zusammen belegen beide aber höchstens
# n_ctx Positionen (kein zusätzlicher Grafikspeicher, dafür teilen sie sich den Platz).
SCHALTER = ("--host", "127.0.0.1", "--no-ui", "--no-jinja", "--no-slots", "--offline",
            "--parallel", "2", "--kv-unified", "--fit", "on")
OHNE_BILDTEIL = ("--no-mmproj",)


def _freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Job:
    """Windows-Job: endet dieser NemiCLI-Prozess, endet der von ihm gestartete Server mit (auch nach einem
    Absturz) – auch wenn ein anderer Prozess ihn gerade als Gast nutzt."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            from ctypes import wintypes as w
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
            k32.CreateJobObjectW.restype = w.HANDLE
            k32.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
            k32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
            k32.CloseHandle.argtypes = [w.HANDLE]

            class _Basis(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", w.DWORD),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", w.DWORD),
                            ("f", ctypes.c_size_t), ("g", w.DWORD), ("h", w.DWORD)]

            class _Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in "abcdef"]

            class _Erweitert(ctypes.Structure):
                _fields_ = [("Basis", _Basis), ("Io", _Io), ("p", ctypes.c_size_t), ("j", ctypes.c_size_t),
                            ("pp", ctypes.c_size_t), ("jp", ctypes.c_size_t)]

            self._k32 = k32
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = _Erweitert()
            info.Basis.LimitFlags = 0x2000                  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(h)
                return
            self.handle = h
        except Exception:
            self.handle = None

    def aufnehmen(self, proc: subprocess.Popen) -> None:
        if self.handle is not None:
            try:
                self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))
            except Exception:
                pass


_JOB: _Job | None = None
_LAUFEND: set = set()                  # gestartete Server (Einrichten beendet sie vorher)


def _job() -> _Job:
    global _JOB
    if _JOB is None:
        _JOB = _Job()
    return _JOB


def _beenden_alle() -> None:
    for s in list(_LAUFEND):
        s.stoppen()


LOG_BEHALTEN = 6                       # so viele Server-Protokolle bleiben liegen
GAST_FRIST_S = 900                     # wer den Server so lange nicht benutzt hat, hält ihn nicht mehr
PAUSE_MAX_S = 1800                     # länger hält eine Mal-Pause andere Prozesse nicht vom Neustart ab
PAUSE_HINWEIS = ("🦙 Ein anderer NemiCLI-Prozess malt gerade – llama.cpp startet, sobald sein Bild fertig ist "
                 "(ESC bricht ab).")
MELDEN_ALLE_S = 60
ZETTEL_MAX = 64 << 10


def _ordner() -> Path:
    return _daten() / "Cache" / "llamacpp"


def log_datei(pid: int | None = None) -> Path:
    """Ein Protokoll je NemiCLI-Prozess (Hauptprogramm und Wache schreiben sonst durcheinander)."""
    return _ordner() / f"server_{pid or os.getpid()}.log"


def _alte_logs_weg() -> None:
    try:
        logs = sorted(_ordner().glob("server_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in logs[LOG_BEHALTEN:]:
            p.unlink(missing_ok=True)
        (_ordner() / "server.log").unlink(missing_ok=True)       # gemeinsames Protokoll früherer Fassungen
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Ein Server je Modell für alle NemiCLI-Prozesse (Hauptprogramm, Wache)
# ---------------------------------------------------------------------------
# Wer zuerst startet, legt einen Zettel ab: Server-PID samt Startzeit, Port, Schlüssel (mit DPAPI für diesen
# Windows-Nutzer verschlüsselt), Build, Server-Datei, Kontext und KV-Typ. Andere Prozesse hängen sich an, statt
# ein zweites Mal Grafikspeicher zu belegen – nur an einen Server des aktiven Builds. Jeder trägt sich bei
# Benutzung als Gast ein (PID und Startzeit: eine neu vergebene PID zählt nicht); wer aufhört (stoppen()), beendet
# den Server nur, wenn sonst niemand ihn in den letzten GAST_FRIST_S genutzt hat. Endet dagegen der Prozess, der ihn
# gestartet hat, endet der Server mit ihm (Windows-Job, KILL_ON_JOB_CLOSE): eine gerade laufende Anfrage eines
# Gastes bricht ab, seine nächste startet einen eigenen Server. Fürs Bildermalen wird er immer beendet; der Zettel
# sagt dann „Pause“, und die anderen warten, statt neu zu starten. Nach einem Build-Wechsel sagt er „wechseln“:
# wer den alten Server nutzt, beendet ihn vor der nächsten Anfrage.
def _lebt(pid) -> bool:
    try:
        import psutil
        return bool(pid) and psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def _startzeit(pid) -> float | None:
    """Startzeit des Prozesses (unterscheidet ihn von einem späteren mit derselben PID)."""
    try:
        import psutil
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def _lebt_als(pid, start) -> bool:
    """Lebt `pid` noch als derselbe Prozess? Ohne gemerkte Startzeit (ältere Zettel): nur die PID."""
    if not _lebt(pid):
        return False
    try:
        start = float(start) if start is not None else None
    except (TypeError, ValueError):
        start = None
    jetzt = _startzeit(pid) if start is not None else None
    return start is None or jetzt is None or abs(jetzt - start) < 1.0


def _gleicher_pfad(a, b) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except (OSError, ValueError, TypeError):
        return False


def _bekannter_server(pfad) -> bool:
    """Ist `pfad` die llama-server.exe des aktiven oder eines anderen eingerichteten Builds?"""
    return _gleicher_pfad(pfad, server_pfad()) or any(_gleicher_pfad(pfad, server_pfad(b)) for b in installiert())


def _ist_unser_server(pid, pfad=None) -> bool:
    """Läuft unter dieser PID genau die llama-server.exe `pfad` (Vorgabe: die des aktiven Builds)?
    Ein fremder Pfad (Zettel) zählt nur, wenn er zu einem eingerichteten Build gehört."""
    try:
        import psutil
        if pfad is None:
            pfad = server_pfad()
        elif not _bekannter_server(pfad):
            return False
        return _gleicher_pfad(psutil.Process(int(pid)).exe(), pfad)
    except Exception:
        return False


def _horcht(port: int, pid: int) -> bool | None:
    """Lauscht an 127.0.0.1:`port` nur der Prozess `pid` (oder einer seiner Kindprozesse)? So geht der Schlüssel
    nie an einen Prozess, der sich den Port zwischen Wahl und Start genommen hat. None: ohne psutil nicht
    feststellbar."""
    try:
        import psutil
        lauscher = {c.pid for c in psutil.net_connections("tcp4")
                    if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == int(port)}
    except Exception:
        return None
    if not lauscher or not pid:
        return False
    erlaubt = {int(pid)}
    try:
        import psutil
        erlaubt |= {k.pid for k in psutil.Process(int(pid)).children(recursive=True)}
    except Exception:
        pass
    return lauscher <= erlaubt


def _prozess_beenden(pid, pfad=None, start=None) -> None:
    if not _ist_unser_server(pid, pfad) or not _lebt_als(pid, start):
        return
    try:
        import psutil
        p = psutil.Process(int(pid))
        p.terminate()
        try:
            p.wait(timeout=STOPP_WARTEN_S)
        except psutil.TimeoutExpired:
            p.kill()
            p.wait(timeout=STOPP_WARTEN_S)
    except Exception:
        pass


def _verschluesseln(text: str) -> str | None:
    try:
        import keyvault
        return keyvault.encrypt(text) if keyvault.available() else None
    except Exception:
        return None


def _entschluesseln(text) -> str | None:
    try:
        import keyvault
        if not isinstance(text, str) or not keyvault.is_encrypted(text):
            return None
        return keyvault.decrypt(text)
    except Exception:
        return None


def _kennung(modell: Path) -> list:
    try:
        st = modell.stat()
        return [os.path.normcase(str(modell.resolve())), st.st_size, st.st_mtime_ns]
    except OSError:
        return []


def _zettel_pfad(modell: Path) -> Path:
    h = hashlib.sha256(os.path.normcase(str(Path(modell).resolve())).encode("utf-8")).hexdigest()[:16]
    return _ordner() / f"geteilt_{h}.json"


class _Sperre:
    """Sperre über Prozesse hinweg (msvcrt.locking): endet ein Prozess, ist sie von selbst frei.
    `abbruch()` True beendet das Warten (RuntimeError)."""

    def __init__(self, pfad: Path, warten_s: float, abbruch=None):
        self.pfad, self.warten_s, self.abbruch, self.f = pfad, warten_s, abbruch, None

    def __enter__(self):
        try:
            import msvcrt
        except ImportError:
            return self
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.pfad, "a+b")
        ende = time.monotonic() + self.warten_s
        while True:
            try:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                abgebrochen = self.abbruch is not None and self.abbruch()
                if abgebrochen or time.monotonic() > ende:
                    self.f.close()
                    self.f = None
                    raise RuntimeError("Start abgebrochen" if abgebrochen else
                                       "llama.cpp: ein anderer NemiCLI-Prozess startet den Server gerade zu lange")
                time.sleep(0.2)

    def __exit__(self, *_):
        if self.f is not None:
            try:
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.f.close()


def _zettel_lesen(modell: Path) -> dict | None:
    pfad = _zettel_pfad(modell)
    try:
        if pfad.stat().st_size > ZETTEL_MAX:
            return None
        z = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(z, dict) or not _TAG.match(str(z.get("build") or "")) or z.get("kennung") != _kennung(modell):
        return None
    if not isinstance(z.get("gaeste"), dict):
        z["gaeste"] = {}
    if not isinstance(z.get("start"), dict):
        z["start"] = {}
    return z


def _datei_schreiben(pfad: Path, z: dict) -> None:
    neu = pfad.with_suffix(f".{os.getpid()}.tmp")
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        neu.write_text(json.dumps(z), encoding="utf-8")
        neu.replace(pfad)
    except OSError:
        neu.unlink(missing_ok=True)


def _zettel_schreiben(modell: Path, z: dict) -> None:
    _datei_schreiben(_zettel_pfad(modell), z)


class Server:
    """llama-server für genau eine Modelldatei – eigener Prozess oder der eines anderen NemiCLI-Prozesses.
    n_ctx 0: Kontext „max“ (llama.cpp kürzt ihn per --fit bis `mindest`, statt Schichten auszulagern);
    `n_ctx_max`: die Modelllänge für Builds ohne --fit-ctx. `kv`: f16, q8_0 oder q4_0."""

    def __init__(self, modell: Path, n_ctx: int = 0, bildteil: Path | None = None, kv: str = "f16",
                 mindest: int = FIT_CTX, n_ctx_max: int = 0):
        self.modell = Path(modell)
        self.bildteil = Path(bildteil) if bildteil else None
        self.n_ctx_wunsch = max(0, int(n_ctx or 0))
        self.n_ctx_max = max(0, int(n_ctx_max or 0))
        self.mindest = max(256, int(mindest or FIT_CTX))
        self.kv = kv if kv in KV_ARTEN else "f16"
        self.kv_wirksam = "f16"                # was der laufende Server wirklich nutzt
        self.kv_rueckfall = False              # Startfehler mit quantisiertem KV: auf f16 zurück
        self._kann: frozenset | None = None    # ZUSATZ-Schalter des Builds (kann()); None: keiner
        self.proc: subprocess.Popen | None = None
        self.server_pid = 0
        self.server_start: float | None = None
        self.server_exe = ""
        self.build = ""
        self.ich = os.getpid()
        self.gast = False
        self.port = 0
        self.schluessel = ""
        self.n_ctx = 0
        self.c_arg = ""
        self.ladezeit = 0.0
        self._gemeldet = 0.0
        self._log_anfang = 0

    @property
    def basis(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def kopf(self) -> dict:
        return {"Authorization": f"Bearer {self.schluessel}"}

    def laeuft(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return (bool(self.server_pid) and _lebt_als(self.server_pid, self.server_start)
                and _ist_unser_server(self.server_pid, self.server_exe or None))

    def _kv_argumente(self, kann: frozenset) -> tuple[list[str], str]:
        """Schalter für den KV-Typ und der Typ, der damit gilt (ohne die Schalter: f16)."""
        if self.kv == "f16" or not {"--cache-type-k", "--cache-type-v", "--flash-attn"} <= kann:
            return [], "f16"
        return ["--flash-attn", "on", "--cache-type-k", self.kv, "--cache-type-v", self.kv], self.kv

    def _kontext_argumente(self, kann: frozenset) -> list[str]:
        if self.n_ctx_wunsch:
            return ["-c", str(self.n_ctx_wunsch)]
        if {"--fit-ctx", "--fit-target"} <= kann:
            mindest = min(self.mindest, self.n_ctx_max) if self.n_ctx_max else self.mindest
            return ["-c", "0", "--fit-ctx", str(mindest), "--fit-target", str(FIT_ZIEL_MIB)]
        # Build ohne --fit-ctx: die Modelllänge, höchstens OHNE_FIT_CTX (-c 0 wäre die volle Modelllänge)
        return ["-c", str(min(self.n_ctx_max or OHNE_FIT_CTX, OHNE_FIT_CTX))]

    def argumente(self) -> list[str]:
        kann = self._kann or frozenset()
        bild = ["--mmproj", str(self.bildteil)] if self.bildteil else list(OHNE_BILDTEIL)
        kontext = self._kontext_argumente(kann)
        kv, self.kv_wirksam = self._kv_argumente(kann)
        self.c_arg = kontext[1]
        return [str(server_pfad()), "-m", str(self.modell), "--port", str(self.port),
                *kontext, *SCHALTER, *kv, *bild]

    def _sperre(self, art: str, warten_s: float, abbruch=None) -> _Sperre:
        return _Sperre(_zettel_pfad(self.modell).with_suffix(f".{art}.lock"), warten_s, abbruch)

    def _props(self) -> int | None:
        """n_ctx des Servers, wenn er mit diesem Schlüssel antwortet – gefragt wird nur, wenn am Port der
        erwartete Prozess lauscht."""
        import httpx
        if self.server_pid and _horcht(self.port, self.server_pid) is False:
            return None
        try:
            with httpx.Client(timeout=5.0, trust_env=False) as c:
                r = c.get(self.basis + "/props", headers=self.kopf)
            if r.status_code != 200:
                return None
            return int(((r.json() or {}).get("default_generation_settings") or {}).get("n_ctx") or 0)
        except Exception:
            return None

    def _anhaengen(self) -> bool:
        """Den Server eines anderen NemiCLI-Prozesses für dieselbe Modelldatei mitbenutzen – nur, wenn er vom
        aktiven Build stammt (Build und Server-Datei laut Zettel) und keinen Wechsel angekündigt hat."""
        z = _zettel_lesen(self.modell)
        if not z or z.get("pause") or z.get("wechseln") or \
                z.get("bildteil") != (str(self.bildteil) if self.bildteil else None):
            return False
        if z.get("build") != aktiv():
            return False
        exe = z.get("server_pfad")
        if exe is not None and (not isinstance(exe, str) or not _gleicher_pfad(exe, server_pfad())):
            return False
        pid, start = z.get("server_pid"), z.get("server_start")
        if not isinstance(pid, int) or not (_lebt_als(pid, start) and _ist_unser_server(pid, exe)):
            return False
        schluessel = _entschluesseln(z.get("schluessel"))
        port = z.get("port")
        if not schluessel or not isinstance(port, int):
            return False
        alt = (self.port, self.schluessel, self.server_pid)
        self.port, self.schluessel, self.server_pid = port, schluessel, pid
        n_ctx = self._props()
        if n_ctx is None:
            self.port, self.schluessel, self.server_pid = alt
            return False
        self.proc, self.gast, self.n_ctx = None, True, n_ctx
        self.server_start = start if isinstance(start, (int, float)) else None
        self.server_exe = exe or str(server_pfad())
        self.build = str(z.get("build"))
        self.kv_wirksam = z.get("kv") if z.get("kv") in KV_ARTEN else "f16"
        self.c_arg = str(z.get("c") or "")
        self.benutzt(sofort=True)
        return True

    def benutzt(self, sofort: bool = False) -> None:
        """Im Zettel festhalten, dass dieser Prozess den geteilten Server gerade nutzt."""
        if not self.server_pid or (not sofort and time.monotonic() - self._gemeldet < MELDEN_ALLE_S):
            return
        self._gemeldet = time.monotonic()
        with self._sperre("zettel", 10.0):
            z = _zettel_lesen(self.modell)
            if z and z.get("server_pid") == self.server_pid:
                z["gaeste"][str(self.ich)] = time.time()
                z["start"][str(self.ich)] = _startzeit(self.ich)
                _zettel_schreiben(self.modell, z)

    def wechsel_faellig(self) -> bool:
        """Stammt der laufende Server von einem anderen als dem aktiven Build (oder kündigt der Zettel
        einen Wechsel an)? Dann soll er vor der nächsten Anfrage enden."""
        if not self.server_pid:
            return False
        if self.build and self.build != aktiv():
            return True
        z = _zettel_lesen(self.modell)
        return bool(z and z.get("server_pid") == self.server_pid and z.get("wechseln"))

    def _pause_abwarten(self, abbruch=None, melden=None) -> None:
        """Malt ein anderer NemiCLI-Prozess gerade (Server dafür beendet), wird nicht neu gestartet.
        `melden(text)` erfährt einmal, dass gewartet wird."""
        t0 = time.monotonic()
        gemeldet = False
        while True:
            z = _zettel_lesen(self.modell)
            if not z or not z.get("pause"):
                return
            von = z.get("pause_von")
            if von == self.ich or not _lebt_als(von, z.get("pause_von_start")) or \
                    time.time() - float(z.get("pause_seit") or 0) > PAUSE_MAX_S:
                return
            if abbruch is not None and abbruch():
                raise RuntimeError("Start abgebrochen")
            if time.monotonic() - t0 > PAUSE_MAX_S:
                return
            if melden is not None and not gemeldet:
                gemeldet = True
                try:
                    melden(PAUSE_HINWEIS)
                except Exception:
                    pass
            time.sleep(1.0)

    def starten(self, abbruch=None, melden=None) -> float:
        """Startet den Server (oder hängt sich an einen laufenden an) und wartet, bis das Modell geladen ist.
        Gibt die Ladezeit zurück (0 beim Anhängen). Scheitert der Start mit quantisiertem KV an Flash Attention,
        startet er einmal mit f16. `abbruch()` True beendet jedes Warten (RuntimeError); `melden(text)` erfährt,
        wenn auf das Bild eines anderen Prozesses gewartet wird."""
        if self.laeuft():
            return 0.0
        if (grund := pruefen()) is not None:
            raise RuntimeError(f"llama.cpp-Reserve nicht startbereit: {grund}")
        self._pause_abwarten(abbruch, melden)
        with self._sperre("start", START_WARTEN_S, abbruch):
            if self._anhaengen():
                self.ladezeit = 0.0
                return 0.0
            self._kann = kann()
            try:
                return self._selbst_starten(abbruch)
            except RuntimeError:
                if self.kv_wirksam == "f16" or (abbruch is not None and abbruch()) \
                        or not _FA_FEHLER.search(self._log_seit_start()):
                    raise
            self.kv, self.kv_rueckfall = "f16", True
            return self._selbst_starten(abbruch)

    def _selbst_starten(self, abbruch=None) -> float:
        import httpx
        self.port = _freier_port()
        self.schluessel = secrets.token_urlsafe(32)
        self.gast = False
        umgebung = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_")}
        umgebung["LLAMA_API_KEY"] = self.schluessel
        log = log_datei(self.ich)
        log.parent.mkdir(parents=True, exist_ok=True)
        _alte_logs_weg()
        try:
            self._log_anfang = log.stat().st_size
        except OSError:
            self._log_anfang = 0
        t0 = time.monotonic()
        argumente = self.argumente()
        with open(log, "ab") as ausgabe:
            self.proc = subprocess.Popen(
                argumente, cwd=str(server_pfad().parent), env=umgebung, stdin=subprocess.DEVNULL,
                stdout=ausgabe, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.server_pid = self.proc.pid
        self.server_start = _startzeit(self.server_pid)
        self.server_exe = str(server_pfad())
        self.build = aktiv()
        _job().aufnehmen(self.proc)
        _LAUFEND.add(self)
        try:
            with httpx.Client(timeout=5.0, trust_env=False) as c:
                while True:
                    if not self.laeuft():
                        raise RuntimeError(f"llama-server hat sich beendet: {self.log_ende()}")
                    if abbruch is not None and abbruch():
                        raise RuntimeError("Start abgebrochen")
                    if time.monotonic() - t0 > START_WARTEN_S:
                        raise RuntimeError(f"llama-server antwortet nach {START_WARTEN_S} s nicht")
                    try:
                        if c.get(self.basis + "/health").status_code == 200 and \
                                _horcht(self.port, self.server_pid) is not False:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
            self.n_ctx = self._props() or 0
        except BaseException:
            self.stoppen()
            raise
        if (verschluesselt := _verschluesseln(self.schluessel)) is not None:
            with self._sperre("zettel", 10.0):
                _zettel_schreiben(self.modell, {
                    "build": self.build, "kennung": _kennung(self.modell), "server_pid": self.server_pid,
                    "server_start": self.server_start, "server_pfad": self.server_exe, "port": self.port,
                    "schluessel": verschluesselt, "n_ctx": self.n_ctx, "c": self.c_arg, "kv": self.kv_wirksam,
                    "bildteil": str(self.bildteil) if self.bildteil else None,
                    "gaeste": {str(self.ich): time.time()}, "start": {str(self.ich): _startzeit(self.ich)},
                    "pause": False, "wechseln": False})
        self._gemeldet = time.monotonic()
        self.ladezeit = time.monotonic() - t0
        return self.ladezeit

    def _log_seit_start(self, hoechstens: int = 256 << 10) -> str:
        """Was der zuletzt gestartete Server ins Protokoll schrieb (für die Fehlerart)."""
        try:
            with open(log_datei(self.ich), "rb") as f:
                f.seek(0, 2)
                ende = f.tell()
                f.seek(max(self._log_anfang, ende - hoechstens))
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def log_ende(self, zeichen: int = 600) -> str:
        try:
            with open(log_datei(self.ich), "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - zeichen))
                return f.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return "(kein Protokoll)"

    def stoppen(self, fuer_bild: bool = False) -> None:
        """Nicht mehr gebraucht: Nutzt ein anderer NemiCLI-Prozess den Server noch, läuft er für ihn weiter (bis
        dieser Prozess endet, siehe _Job).
        fuer_bild: immer beenden (das Bildmodell braucht die Grafikkarte); die anderen warten auf das Ende."""
        proc, self.proc = self.proc, None
        pid, self.server_pid = self.server_pid, 0
        start, exe = self.server_start, self.server_exe or None
        _LAUFEND.discard(self)
        try:
            self._stoppen_mit_zettel(proc, pid, fuer_bild, start, exe)
        except BaseException:
            if proc is not None:                             # ohne Zettel: der eigene Server belegte sonst VRAM
                _proc_beenden(proc)
            raise

    def _stoppen_mit_zettel(self, proc: subprocess.Popen | None, pid: int, fuer_bild: bool,
                            start: float | None = None, exe: str | None = None) -> None:
        with self._sperre("zettel", 10.0):
            z = _zettel_lesen(self.modell)
            geteilt = bool(pid) and z is not None and z.get("server_pid") == pid
            if geteilt and not fuer_bild:
                z["gaeste"].pop(str(self.ich), None)
                z["start"].pop(str(self.ich), None)
                jetzt = time.time()
                andere = [int(p) for p, ts in z["gaeste"].items()
                          if str(p).isdigit() and jetzt - float(ts or 0) < GAST_FRIST_S
                          and _lebt_als(int(p), z["start"].get(str(p)))]
                if andere and (proc is None or proc.poll() is None):
                    _zettel_schreiben(self.modell, z)
                    return                                   # läuft für die anderen weiter
            if proc is not None:
                _proc_beenden(proc)
            elif pid:
                _prozess_beenden(pid, exe, start)
            if fuer_bild:                                    # auch ohne laufenden Server: niemand startet ihn jetzt
                z = z if geteilt else {"build": aktiv(), "kennung": _kennung(self.modell), "n_ctx": 0}
                z.update(server_pid=0, server_start=None, port=0, schluessel="", gaeste={}, start={}, pause=True,
                         pause_von=self.ich, pause_von_start=_startzeit(self.ich), pause_seit=time.time())
                _zettel_schreiben(self.modell, z)
            elif geteilt:
                _zettel_pfad(self.modell).unlink(missing_ok=True)

    def pause_ende(self) -> None:
        """Das Bild ist fertig: die eigene Pause aufheben (der nächste Start legt einen neuen Zettel an)."""
        with self._sperre("zettel", 10.0):
            z = _zettel_lesen(self.modell)
            if z and z.get("pause") and z.get("pause_von") == self.ich:
                _zettel_pfad(self.modell).unlink(missing_ok=True)


def _proc_beenden(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=STOPP_WARTEN_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=STOPP_WARTEN_S)
        except subprocess.TimeoutExpired:
            pass
