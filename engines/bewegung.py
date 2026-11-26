"""
bewegung.py - Text zu Bewegung für die Figur im Terminal-Fenster (SprongMotion).

Alles liegt fest im Modellordner `KI-Modelle/KI-Bewegung/Bewegungsmodell/SprongMotion-14M/` (`modell_ordner()`,
früher `Models/Motion/`).
Laufzeit: genau eine Fassung (VERSION), das offizielle Windows-Paket von GitHub – nur von HOSTS, Größe und
SHA-256 von Paket und DLL fest (`holen()`), abgelegt in `<modell_ordner>/sprongmotion-<VERSION>/`.
Modell: SprongMotion-14M (Apache-2.0) legt der Nutzer selbst in den Modellordner; NemiCLI lädt
keine Modelle herunter. Die exe bringt beides mit (im Programmteil, `NemiCLIT2/Motion/`): Fehlt etwas im
Modellordner, legt `einspeisen()` die mitgelieferten Dateien dorthin – nur mit passender SHA-256; was schon
da ist, bleibt. `modell_einrichten()` übernimmt eine schon geladene Datei, wenn Größe und SHA-256 stimmen.
Vor dem ersten Laden je Prozess werden DLL und Modell erneut geprüft.

Ergebnis von `Motor.rechnen()`: 22 Gelenke je Bild (Meter, Y oben, Blick nach +Z, auf der Stelle), 20 Bilder
je Sekunde – das Fenster zeichnet daraus das Strichmännchen (ui/terminal_fenster.py).
"""

from __future__ import annotations

import ctypes as C
import hashlib
import io
import math
import shutil
import sys
import tarfile
import threading
from pathlib import Path

VERSION = "1.1.0"
PAKET = f"sprongmotion-{VERSION}-windows-x86_64.tar.gz"
PAKET_SHA = "87a483751e3b22023e84c9edb280835fed7134a462e8ffdbd0d89ff83b16c999"
PAKET_GROESSE = 173188
URL = f"https://github.com/SegerEnd/sprongmotion-runtime/releases/download/v{VERSION}/{PAKET}"
HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
MAX_UMLEITUNGEN = 5
DLL = "libsprongmotion.dll"
DLL_SHA = "5a7cff6c91276524889eea5c3f14487ed16e8696853be7213a93bd39d8ead7fa"
# Aus dem Paket übernommen werden nur diese Einträge (Lizenz und Hinweise gehören zur DLL)
AUS_PAKET = {f"sprongmotion-{VERSION}-windows-x86_64/lib/{DLL}": DLL,
             f"sprongmotion-{VERSION}-windows-x86_64/LICENSE": "LICENSE",
             f"sprongmotion-{VERSION}-windows-x86_64/NOTICE": "NOTICE"}
MODELL = "SprongMotion-14M-v1.0-Q8_0.gguf"
MODELL_SHA = "68fbd311a0d7d75f60508b35254171583c85880e7e124140457b57ac84c5de9d"
MODELL_GROESSE = 33337824
MODELL_SEITE = "https://huggingface.co/SegerEnd/SprongMotion-14M"
GELENKE = 22
SEKUNDEN_MIN, SEKUNDEN_MAX = 0.5, 10.0


def _install() -> Path:
    try:
        from paths import INSTALL
        return Path(INSTALL)
    except Exception:
        return Path(__file__).resolve().parent.parent


def _neuer_ordner() -> Path:
    try:
        import modellorte
        return modellorte.ordner("bewegung") / modellorte.BEWEGUNG
    except Exception:
        return _install() / "KI-Modelle" / "KI-Bewegung" / "Bewegungsmodell" / "SprongMotion-14M"


def _alter_ordner() -> Path:
    return _install() / "Models" / "Motion"


def modell_ordner() -> Path:
    """Modell und Laufzeit, fest je Installation: KI-Modelle/KI-Bewegung/Bewegungsmodell/SprongMotion-14M.
    Liegt das Modell nur am früheren Ort `Models/Motion`, bleibt es dort."""
    neu, alt = _neuer_ordner(), _alter_ordner()
    if not (neu / MODELL).is_file() and (alt / MODELL).is_file():
        return alt
    return neu


def ablage() -> Path:
    return modell_ordner() / f"sprongmotion-{VERSION}"


def dll_pfad() -> Path:
    return ablage() / DLL


def modell_pfad() -> Path:
    return modell_ordner() / MODELL


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(4 << 20), b""):
            h.update(stueck)
    return h.hexdigest()


def _datei_stimmt(pfad: Path, soll: str, groesse: int | None = None) -> bool:
    try:
        return pfad.is_file() and (groesse is None or pfad.stat().st_size == groesse) and sha256(pfad) == soll
    except OSError:
        return False


MITGELIEFERT = "Motion"                # Ordner im Programmteil der exe (NemiCLIT2/Motion)
_EINGESPEIST = [False]


def _mitgeliefert() -> Path | None:
    basis = getattr(sys, "_MEIPASS", None)
    ordner = Path(basis) / MITGELIEFERT if basis else None
    return ordner if ordner is not None and ordner.is_dir() else None


def einspeisen(quelle: Path | None = None) -> list[str]:
    """Mitgelieferte Laufzeit und Modell in den Modellordner legen, wo sie fehlen – jede Datei vorher gegen die feste
    SHA-256 geprüft. Gibt die übernommenen Dateinamen zurück."""
    quelle = quelle or _mitgeliefert()
    if quelle is None:
        return []
    neu = []
    paare = [(quelle / MODELL, modell_pfad(), MODELL_SHA, MODELL_GROESSE),
             (quelle / f"sprongmotion-{VERSION}" / DLL, dll_pfad(), DLL_SHA, None)]
    for von, nach, soll, groesse in paare:
        if nach.is_file() or not _datei_stimmt(von, soll, groesse):
            continue
        nach.parent.mkdir(parents=True, exist_ok=True)
        teil = nach.with_suffix(nach.suffix + ".part")
        shutil.copyfile(von, teil)
        teil.replace(nach)
        neu.append(nach.name)
    for name in ("LICENSE", "NOTICE"):                   # Lizenz und Hinweise gehören zur DLL
        von, nach = quelle / f"sprongmotion-{VERSION}" / name, ablage() / name
        if von.is_file() and not nach.is_file() and dll_pfad().is_file():
            shutil.copyfile(von, nach)
    return neu


def stand() -> dict:
    """{"laufzeit": bool, "modell": bool, "bereit": bool, "zeilen": [...]} – ohne SHA-Prüfung (schnell).
    Beim ersten Aufruf je Prozess legt es Mitgeliefertes in den Modellordner, falls dort etwas fehlt."""
    if not _EINGESPEIST[0]:
        _EINGESPEIST[0] = True
        try:
            einspeisen()
        except OSError:
            pass
    laufzeit = dll_pfad().is_file()
    modell = modell_pfad().is_file() and modell_pfad().stat().st_size == MODELL_GROESSE
    zeilen = [f"{'✅' if laufzeit else '⬜'} Laufzeit SprongMotion {VERSION}" + ("" if laufzeit else " – /bewegung holen"),
              f"{'✅' if modell else '⬜'} Modell {MODELL}" + ("" if modell else
                                                             f" – nach {modell_ordner()} legen oder /bewegung einrichten")]
    return {"laufzeit": laufzeit, "modell": modell, "bereit": laufzeit and modell, "zeilen": zeilen}


# ---------------------------------------------------------------------------
# Laufzeit holen und Modell übernehmen
# ---------------------------------------------------------------------------

class Abbruch(Exception):
    pass


def _paket_laden(fortschritt=None, stopp: threading.Event | None = None) -> bytes:
    """Das Paket von GitHub in den Speicher (173 KB), Größe und SHA-256 geprüft."""
    import httpx
    url = httpx.URL(URL)
    daten = bytearray()
    with httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=False) as c:
        for _ in range(MAX_UMLEITUNGEN + 1):
            if url.scheme != "https" or url.host not in HOSTS:
                raise OSError(f"Umleitung zu {url.host} – nur GitHub ist erlaubt, abgebrochen")
            with c.stream("GET", url, headers={"User-Agent": "NemiCLI-Bewegung"}) as r:
                if r.is_redirect:
                    url = url.join(r.headers.get("location", ""))
                    continue
                if r.status_code != 200:
                    raise OSError(f"GitHub antwortet mit {r.status_code}")
                for stueck in r.iter_bytes(1 << 16):
                    if stopp is not None and stopp.is_set():
                        raise Abbruch()
                    daten += stueck
                    if len(daten) > PAKET_GROESSE:
                        raise OSError("Paket größer als angegeben – verworfen")
                    if fortschritt is not None:
                        fortschritt(len(daten), PAKET_GROESSE)
            break
        else:
            raise OSError("zu viele Umleitungen")
    if len(daten) != PAKET_GROESSE or hashlib.sha256(daten).hexdigest() != PAKET_SHA:
        raise OSError("Größe oder SHA-256 des Pakets stimmen nicht – verworfen")
    return bytes(daten)


def auspacken(paket: bytes, ziel: Path) -> list[str]:
    """Nur DLL, LICENSE und NOTICE aus dem geprüften Paket, unter festen Namen; die DLL muss DLL_SHA haben."""
    if hashlib.sha256(paket).hexdigest() != PAKET_SHA:
        raise ValueError("Paket: SHA-256 stimmt nicht")
    neu = ziel.with_name(ziel.name + ".neu")
    shutil.rmtree(neu, ignore_errors=True)
    neu.mkdir(parents=True)
    gefunden = []
    with tarfile.open(fileobj=io.BytesIO(paket), mode="r:gz") as tar:
        for eintrag in tar.getmembers():
            name = AUS_PAKET.get(eintrag.name)
            if name is None or not eintrag.isfile() or eintrag.size > 4 << 20:
                continue
            quelle = tar.extractfile(eintrag)
            if quelle is None:
                continue
            (neu / name).write_bytes(quelle.read())
            gefunden.append(name)
    if DLL not in gefunden or sha256(neu / DLL) != DLL_SHA:
        shutil.rmtree(neu, ignore_errors=True)
        raise ValueError(f"{DLL} fehlt im Paket oder hat eine andere SHA-256")
    shutil.rmtree(ziel, ignore_errors=True)
    neu.replace(ziel)
    return sorted(gefunden)


def holen(fortschritt=None, stopp: threading.Event | None = None) -> tuple[bool, list[str]]:
    """Laufzeit laden, prüfen, ablegen. (eingerichtet, Zeilen für die Anzeige)."""
    if _datei_stimmt(dll_pfad(), DLL_SHA):
        return True, [f"✅ SprongMotion {VERSION}: schon da, SHA-256 stimmt"]
    try:
        paket = _paket_laden(fortschritt, stopp)
        dateien = auspacken(paket, ablage())
    except Abbruch:
        return False, ["⏹ Abgebrochen."]
    except Exception as exc:
        return False, [f"❌ {exc}"]
    return True, [f"✅ SprongMotion {VERSION} von GitHub: Paket und {DLL} – SHA-256 stimmt",
                  f"   abgelegt in {ablage()} ({', '.join(dateien)})"]


def modell_einrichten(quelle: Path | None = None) -> tuple[bool, list[str]]:
    """Eine schon geladene Modelldatei übernehmen, nur mit passender Größe und SHA-256. Ohne Angabe kommt sie
    aus dem Download-Ordner und wird dorthin verschoben, wo sie hingehört; ein angegebener Pfad wird kopiert."""
    aus_downloads = quelle is None
    ziel = modell_pfad()
    if _datei_stimmt(ziel, MODELL_SHA, MODELL_GROESSE):
        return True, [f"✅ {MODELL}: schon eingerichtet, SHA-256 stimmt"]
    quelle = Path(quelle) if quelle else Path.home() / "Downloads" / MODELL
    if not quelle.is_file():
        return False, [f"⬜ {MODELL} nicht gefunden in {quelle.parent}.",
                       f"   Offizielle Seite: {MODELL_SEITE} – Datei laden und nach {modell_ordner()} legen "
                       "oder /bewegung einrichten <pfad>."]
    if not _datei_stimmt(quelle, MODELL_SHA, MODELL_GROESSE):
        return False, [f"❌ {quelle.name}: Größe oder SHA-256 passen nicht zu {MODELL} 1.0 – nicht übernommen"]
    modell_ordner().mkdir(parents=True, exist_ok=True)
    teil = ziel.with_suffix(".part")
    shutil.copyfile(quelle, teil)
    teil.replace(ziel)
    if aus_downloads and _datei_stimmt(ziel, MODELL_SHA, MODELL_GROESSE):
        quelle.unlink(missing_ok=True)
        return True, [f"✅ {MODELL}: SHA-256 stimmt, aus dem Download-Ordner verschoben nach {modell_ordner()}"]
    return True, [f"✅ {MODELL}: SHA-256 stimmt, kopiert nach {modell_ordner()}"]


# ---------------------------------------------------------------------------
# Laufzeit ansprechen (ctypes, Strukturen aus sprongmotion.h 1.1.0)
# ---------------------------------------------------------------------------

class _Model(C.Structure):
    _fields_ = [("id", C.c_char_p), ("fps", C.c_int), ("frames_per_token", C.c_int), ("max_seconds", C.c_float),
                ("code_layers", C.c_int), ("feature_size", C.c_int), ("text_vector_size", C.c_int),
                ("joint_count", C.c_int), ("joint_names", C.POINTER(C.c_char_p)), ("joint_parents", C.POINTER(C.c_int)),
                ("rest_offsets", C.POINTER(C.c_float)), ("has_generator", C.c_bool), ("has_encoder", C.c_bool),
                ("follows_path", C.c_bool), ("internal", C.c_void_p)]


class _Options(C.Structure):
    _fields_ = [("seconds", C.c_float), ("seed", C.c_int), ("steps", C.c_int), ("residual_layers", C.c_int),
                ("text_guidance", C.c_float), ("residual_guidance", C.c_float), ("temperature", C.c_float),
                ("top_k", C.c_int), ("foot_lock", C.c_bool), ("in_place", C.c_bool), ("loop", C.c_bool),
                ("path_warp", C.c_bool), ("path", C.c_void_p), ("path_count", C.c_int), ("previous", C.c_void_p),
                ("pinned_codes", C.c_void_p), ("pinned_token_count", C.c_int), ("text", C.c_void_p),
                ("progress", C.c_void_p), ("user", C.c_void_p)]


class _Motion(C.Structure):
    _fields_ = [("frame_count", C.c_int), ("fps", C.c_int), ("seed", C.c_int), ("joints", C.POINTER(C.c_float)),
                ("rotations", C.POINTER(C.c_float)), ("offsets", C.POINTER(C.c_float)), ("root", C.POINTER(C.c_float)),
                ("speed", C.POINTER(C.c_float)), ("contacts", C.POINTER(C.c_uint8)), ("features", C.POINTER(C.c_float)),
                ("codes", C.POINTER(C.c_int16)), ("confidence", C.POINTER(C.c_float)), ("token_count", C.c_int),
                ("path_error", C.c_float), ("block_start_frames", C.POINTER(C.c_int)), ("block_count", C.c_int),
                ("internal", C.c_void_p)]


def prompt_sauber(text: str) -> str:
    """Ein Satz, ohne Steuerzeichen, höchstens 200 Zeichen – das Modell versteht kurze englische Sätze."""
    text = " ".join("".join(z for z in str(text or "") if z.isprintable()).split())
    return text[:200]


class Motor:
    """Lädt DLL und Modell beim ersten Aufruf (beides vorher gegen die SHA-256 geprüft) und rechnet Bewegungen."""

    def __init__(self):
        self._lock = threading.Lock()
        self._lib = None
        self._modell = None
        self.namen: list[str] = []
        self.eltern: list[int] = []

    def _laden(self) -> None:
        if self._modell is not None:
            return
        if not _datei_stimmt(dll_pfad(), DLL_SHA):
            raise RuntimeError("Laufzeit fehlt oder ist verändert – /bewegung holen")
        if not _datei_stimmt(modell_pfad(), MODELL_SHA, MODELL_GROESSE):
            raise RuntimeError(f"Modell fehlt oder ist verändert – {MODELL} nach {modell_ordner()}")
        lib = C.CDLL(str(dll_pfad()))
        lib.sm_get_error.restype = C.c_char_p
        lib.sm_load_model.restype, lib.sm_load_model.argtypes = _Model, [C.c_char_p]
        lib.sm_is_model_valid.restype, lib.sm_is_model_valid.argtypes = C.c_bool, [_Model]
        lib.sm_default_options.restype, lib.sm_default_options.argtypes = _Options, [_Model]
        lib.sm_generate_motion_ex.restype = _Motion
        lib.sm_generate_motion_ex.argtypes = [_Model, C.c_char_p, _Options]
        lib.sm_is_motion_valid.restype, lib.sm_is_motion_valid.argtypes = C.c_bool, [_Motion]
        lib.sm_unload_motion.argtypes = [_Motion]
        entladen = getattr(lib, "sm_unload_model", None)
        if entladen is not None:
            entladen.argtypes = [_Model]
        modell = lib.sm_load_model(str(modell_pfad()).encode("utf-8"))
        try:
            if not lib.sm_is_model_valid(modell):
                raise RuntimeError("Modell: " + (lib.sm_get_error() or b"").decode("utf-8", "replace"))
            if modell.joint_count != GELENKE:
                raise RuntimeError(f"Modell hat {modell.joint_count} Gelenke statt {GELENKE}")
            namen = [modell.joint_names[i].decode("utf-8") for i in range(GELENKE)]
            eltern = [int(modell.joint_parents[i]) for i in range(GELENKE)]
        except BaseException:
            if entladen is not None:                  # unbrauchbares Modell nicht im Speicher lassen
                entladen(modell)
            raise
        self.namen, self.eltern = namen, eltern
        self._lib, self._modell = lib, modell

    def rechnen(self, prompt: str, sekunden: float = 3.0, seed: int = 7, schleife: bool = False) -> dict:
        """{"fps", "namen", "eltern", "bilder": [[[x, y, z] × 22] × n], "schleife"} – auf der Stelle."""
        prompt = prompt_sauber(prompt)
        if not prompt:
            raise ValueError("Welche Bewegung? (ein kurzer englischer Satz)")
        sekunden = min(SEKUNDEN_MAX, max(SEKUNDEN_MIN, float(sekunden)))
        with self._lock:
            self._laden()
            lib = self._lib
            o = lib.sm_default_options(self._modell)
            o.seconds, o.seed, o.in_place, o.loop = sekunden, int(seed) & 0x7FFFFFFF, True, bool(schleife)
            mo = lib.sm_generate_motion_ex(self._modell, prompt.encode("utf-8"), o)
            if not lib.sm_is_motion_valid(mo):
                fehler = (lib.sm_get_error() or b"").decode("utf-8", "replace")
                lib.sm_unload_motion(mo)              # auch eine unvollständige Bewegung freigeben
                raise RuntimeError("Bewegung: " + fehler)
            try:
                n, g = mo.frame_count, mo.joints
                bilder = [[[round(g[(f * GELENKE + j) * 3 + k], 4) for k in range(3)] for j in range(GELENKE)]
                          for f in range(n)]
                fps = int(mo.fps) or 20
            finally:
                lib.sm_unload_motion(mo)
        if not all(math.isfinite(v) for b in bilder for p in b for v in p):
            raise RuntimeError("Bewegung mit ungültigen Werten")
        return {"fps": fps, "namen": self.namen, "eltern": self.eltern, "bilder": bilder,
                "schleife": bool(schleife), "prompt": prompt}


MOTOR = Motor()
