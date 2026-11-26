"""tresor.py – alle Geheimnisse von NemiCLI in einem verschlossenen Ordner `.tresor`.

Ort: %LOCALAPPDATA%\\<Name aus der Rechner-Kennung>\\.tresor\\ (beide Ordner versteckt), darin `inhalt` und `sperre`.
Keine Hinweisdatei verrät den Ort; NemiCLI berechnet ihn jedes Mal neu.

Zwei Schlösser:
  1. AES-256-GCM aus der Windows-Kryptografie (CNG, bcrypt.dll) – verschlüsselt und prüft die Echtheit.
  2. Der AES-Schlüssel (32 Zufalls-Bytes je Tresor) liegt im Windows-Schloss (DPAPI, nur dieser Windows-Benutzer
     auf diesem Rechner) mit einem Zusatz-Geheimnis aus der Rechner-Kennung.
Geöffnet wird nur im Speicher; auf der Platte steht nie Klartext.

Siegel (Wächter): Jedes Schreiben über dieses Modul legt den SHA-256 der Tresor-Datei im Windows-Schloss unter
HKCU ab – an einem anderen Ort als die Datei. `pruefen()` (bei jedem Öffnen und im Takt der Sitzung) vergleicht:
passt die Datei nicht zum Siegel – von außen verändert, ersetzt oder ein alter Stand zurückgelegt –, wird der Tresor
sofort vernichtet. Alle Schlüssel sind dann weg und werden im Controllcenter neu eingetragen.

Inhalt: {"schluessel": {NAME: wert}, "chatgpt": {…Konto…}, …} – Bereiche nach Zweck.
Ohne Windows-Kryptografie (anderes System) bleibt nichts auf der Platte: Werte leben dann nur im Speicher.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

MAGIE = b"NTR1"
NONCE, TAG, SCHLUESSEL = 12, 16, 32
SPERRE_WARTEN_S = 20.0
_ENTROPIE_VORSATZ = b"NemiCLI-Tresor-v1|"

_lock = threading.RLock()
_merk: dict = {"sha": None, "daten": None}
_speicher: dict = {}                     # ohne Windows-Kryptografie: Inhalt nur im Speicher dieses Prozesses
ZUSTAND: dict = {"vernichtet": "", "zeit": 0.0}
BEI_VERNICHTUNG: list = []               # Rückrufe (z. B. Schlüssel aus os.environ nehmen)

# Zum Testen umlenkbar: Ort des Tresors und des Siegels
ORDNER: Path | None = None
SIEGEL_DATEI: Path | None = None         # statt der Registry (nur Tests)


# ---------------------------------------------------------------------------
# Windows: CNG (AES-GCM) und DPAPI
# ---------------------------------------------------------------------------

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


class _GCM_INFO(ctypes.Structure):                       # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO
    _fields_ = [("cbSize", ctypes.c_ulong), ("dwInfoVersion", ctypes.c_ulong),
                ("pbNonce", ctypes.c_void_p), ("cbNonce", ctypes.c_ulong),
                ("pbAuthData", ctypes.c_void_p), ("cbAuthData", ctypes.c_ulong),
                ("pbTag", ctypes.c_void_p), ("cbTag", ctypes.c_ulong),
                ("pbMacContext", ctypes.c_void_p), ("cbMacContext", ctypes.c_ulong),
                ("cbAAD", ctypes.c_ulong), ("cbData", ctypes.c_ulonglong), ("dwFlags", ctypes.c_ulong)]


def _dlls():
    if os.name != "nt":
        return None
    try:
        return ctypes.WinDLL("bcrypt.dll"), ctypes.WinDLL("crypt32.dll"), ctypes.WinDLL("kernel32.dll")
    except OSError:
        return None


def verfuegbar() -> bool:
    """Windows-Kryptografie da? Ohne sie speichert der Tresor nichts auf der Platte."""
    return _dlls() is not None


def _pruef(status: int, was: str) -> None:
    if status != 0:
        raise OSError(f"Windows-Kryptografie ({was}): 0x{status & 0xFFFFFFFF:08X}")


def _aes_gcm(schluessel: bytes, nonce: bytes, daten: bytes, tag: bytes | None = None) -> tuple[bytes, bytes]:
    """AES-256-GCM. Ohne `tag`: verschlüsseln → (Chiffre, Tag); mit `tag`: entschlüsseln → (Klartext, tag).
    Ein falscher Tag (verändert, falscher Schlüssel) wirft ValueError."""
    bcrypt = _dlls()[0]
    alg, key = ctypes.c_void_p(), ctypes.c_void_p()
    _pruef(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ctypes.c_wchar_p("AES"), None, 0), "AES")
    try:
        modus = ctypes.create_unicode_buffer("ChainingModeGCM")
        _pruef(bcrypt.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"), modus, ctypes.sizeof(modus), 0),
               "GCM")
        k = ctypes.create_string_buffer(schluessel, len(schluessel))
        _pruef(bcrypt.BCryptGenerateSymmetricKey(alg, ctypes.byref(key), None, 0, k, len(schluessel), 0), "Schlüssel")
        try:
            n = ctypes.create_string_buffer(nonce, len(nonce))
            t = ctypes.create_string_buffer(tag or b"\0" * TAG, TAG)
            info = _GCM_INFO(cbSize=ctypes.sizeof(_GCM_INFO), dwInfoVersion=1,
                             pbNonce=ctypes.cast(n, ctypes.c_void_p), cbNonce=len(nonce),
                             pbTag=ctypes.cast(t, ctypes.c_void_p), cbTag=TAG)
            ein = ctypes.create_string_buffer(daten, max(1, len(daten)))
            aus = ctypes.create_string_buffer(max(1, len(daten)))
            laenge = ctypes.c_ulong()
            if tag is None:
                _pruef(bcrypt.BCryptEncrypt(key, ein, len(daten), ctypes.byref(info), None, 0, aus, len(daten),
                                            ctypes.byref(laenge), 0), "verschlüsseln")
                return aus.raw[:laenge.value], t.raw
            status = bcrypt.BCryptDecrypt(key, ein, len(daten), ctypes.byref(info), None, 0, aus, len(daten),
                                          ctypes.byref(laenge), 0)
            if status != 0:
                raise ValueError("Tresor: Echtheitsprüfung fehlgeschlagen")
            return aus.raw[:laenge.value], tag
        finally:
            bcrypt.BCryptDestroyKey(key)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def _rechner_kennung() -> bytes:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return str(winreg.QueryValueEx(k, "MachineGuid")[0]).encode()
    except OSError:
        return os.environ.get("COMPUTERNAME", "nemicli").encode()


def _entropie() -> bytes:
    return hashlib.sha256(_ENTROPIE_VORSATZ + _rechner_kennung()).digest()


def _dpapi(daten: bytes, schuetzen: bool) -> bytes:
    _, crypt32, kernel32 = _dlls()
    ein_puffer = ctypes.create_string_buffer(daten, len(daten))
    ein = _BLOB(len(daten), ctypes.cast(ein_puffer, ctypes.POINTER(ctypes.c_char)))
    ent = _entropie()
    ent_puffer = ctypes.create_string_buffer(ent, len(ent))
    zusatz = _BLOB(len(ent), ctypes.cast(ent_puffer, ctypes.POINTER(ctypes.c_char)))
    aus = _BLOB()
    fn = crypt32.CryptProtectData if schuetzen else crypt32.CryptUnprotectData
    args = ((ctypes.byref(ein), ctypes.c_wchar_p("nemicli"), ctypes.byref(zusatz), None, None, 1, ctypes.byref(aus))
            if schuetzen else (ctypes.byref(ein), None, ctypes.byref(zusatz), None, None, 1, ctypes.byref(aus)))
    if not fn(*args):
        raise ValueError("Windows-Schloss: nicht " + ("verschlossen" if schuetzen else "geöffnet"))
    try:
        return ctypes.string_at(aus.pbData, aus.cbData)
    finally:
        kernel32.LocalFree(aus.pbData)


# ---------------------------------------------------------------------------
# Ort, Siegel, Sperre
# ---------------------------------------------------------------------------

def _name() -> str:
    return hashlib.sha256(b"NemiCLI-Ort|" + _rechner_kennung()).hexdigest()[:16]


def _testlauf() -> Path | None:
    """Test-Suite: Tresor und Siegel im Test-Ordner – nie der echte Tresor des Nutzers."""
    try:
        import paths
        return paths.TESTDATEN if paths._testlauf() else None
    except Exception:
        return None


def ordner() -> Path:
    if ORDNER is not None:
        return ORDNER
    if (test := _testlauf()) is not None:
        return test / ".tresor"
    basis = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return basis / _name() / ".tresor"


def _datei() -> Path:
    return ordner() / "inhalt"


def _verstecken(pfad: Path) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(pfad), 0x2)          # FILE_ATTRIBUTE_HIDDEN
        except (AttributeError, OSError):
            pass


def _anlegen() -> None:
    o = ordner()
    if not o.is_dir():
        o.mkdir(parents=True, exist_ok=True)
        _verstecken(o.parent)
        _verstecken(o)


def _reg_pfad() -> str:
    """Neutraler Schlüssel unter HKCU/Software – derselbe berechnete Name wie der Ordner, kein Hinweis auf NemiCLI."""
    return "Software\\" + _name()


def _siegel_datei() -> Path | None:
    if SIEGEL_DATEI is not None:
        return SIEGEL_DATEI
    test = _testlauf()
    return test / "tresor_siegel" if test is not None else None


def _siegel_lesen() -> list[str]:
    """Erlaubte SHA-256 der Tresor-Datei (leer = kein Siegel)."""
    try:
        if (datei := _siegel_datei()) is not None:
            roh = datei.read_bytes()
        else:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _reg_pfad()) as k:
                roh = bytes(winreg.QueryValueEx(k, "s")[0])
        werte = json.loads(_dpapi(roh, False).decode())
        return [str(w) for w in werte] if isinstance(werte, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _siegel_schreiben(werte: list[str]) -> None:
    datei = _siegel_datei()
    if not werte:
        try:
            if datei is not None:
                datei.unlink(missing_ok=True)
            else:
                import winreg
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _reg_pfad())
        except OSError:
            pass
        return
    roh = _dpapi(json.dumps(werte).encode(), True)
    if datei is not None:
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_bytes(roh)
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _reg_pfad()) as k:
        winreg.SetValueEx(k, "s", 0, winreg.REG_BINARY, roh)


@contextlib.contextmanager
def _sperre():
    """Sperre über Prozesse (Lesen–Ändern–Schreiben samt Siegel); der Wächter prüft nie einen halben Stand."""
    with _lock:
        _anlegen()
        f = open(ordner() / "sperre", "a+b")
        ende = time.monotonic() + SPERRE_WARTEN_S
        try:
            while True:
                try:
                    f.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > ende:
                        raise RuntimeError("Tresor: ein anderes NemiCLI-Fenster arbeitet gerade damit.") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                if os.name == "nt":
                    try:
                        import msvcrt
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
        finally:
            f.close()


def _sha(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


# ---------------------------------------------------------------------------
# Vernichten und Schreddern
# ---------------------------------------------------------------------------

def schreddern(pfad: Path) -> bool:
    """Datei mit Zufall überschreiben, dann löschen. True, wenn sie weg ist."""
    pfad = Path(pfad)
    try:
        if pfad.is_symlink() or not pfad.is_file():
            return not pfad.exists()
        groesse = pfad.stat().st_size
        with open(pfad, "r+b", buffering=0) as f:
            f.write(secrets.token_bytes(max(1, groesse)))
            os.fsync(f.fileno())
        pfad.unlink()
        return True
    except OSError:
        return False


def _vernichten_ohne_sperre(grund: str) -> None:
    schreddern(_datei())
    _siegel_schreiben([])
    _merk.update(sha=None, daten=None)
    _speicher.clear()
    ZUSTAND.update(vernichtet=grund, zeit=time.time())
    try:
        import debugbericht
        debugbericht.ereignis(f"🚨 Tresor vernichtet: {grund}")
    except Exception:
        pass
    for fn in list(BEI_VERNICHTUNG):
        try:
            fn(grund)
        except Exception:
            pass


def vernichten(grund: str = "von Hand") -> None:
    """Alle Geheimnisse weg: Datei geschreddert, Siegel gelöscht."""
    if not verfuegbar():
        _speicher.clear()
        ZUSTAND.update(vernichtet=grund, zeit=time.time())
        return
    with _sperre():
        _vernichten_ohne_sperre(grund)


# ---------------------------------------------------------------------------
# Lesen, Prüfen, Schreiben
# ---------------------------------------------------------------------------

def _pruefen_ohne_sperre() -> str:
    """"ok", "leer" oder "vernichtet" (Datei passt nicht zum Siegel)."""
    datei = _datei()
    siegel = _siegel_lesen()
    if not datei.is_file():
        if siegel:
            _siegel_schreiben([])                       # Datei weg (z. B. von Hand gelöscht): nichts zu schützen
        return "leer"
    roh = datei.read_bytes()
    if not siegel:
        _vernichten_ohne_sperre("Tresor ohne Siegel gefunden")
        return "vernichtet"
    if _sha(roh) not in siegel:
        _vernichten_ohne_sperre("Tresor passt nicht zum Siegel (verändert, ersetzt oder alter Stand)")
        return "vernichtet"
    return "ok"


def pruefen() -> str:
    """Der Wächter: passt der Tresor zum Siegel? Sonst sofort vernichten. "ok" · "leer" · "vernichtet"."""
    if not verfuegbar():
        return "ok" if _speicher else "leer"
    with _sperre():
        return _pruefen_ohne_sperre()


def _entschluesseln(roh: bytes) -> dict:
    if roh[:4] != MAGIE:
        raise ValueError("Tresor: unbekanntes Format")
    laenge = int.from_bytes(roh[4:8], "big")
    huelle = roh[8:8 + laenge]
    rest = roh[8 + laenge:]
    nonce, tag, chiffre = rest[:NONCE], rest[NONCE:NONCE + TAG], rest[NONCE + TAG:]
    schluessel = _dpapi(huelle, False)
    klar, _ = _aes_gcm(schluessel, nonce, chiffre, tag)
    daten = json.loads(klar.decode("utf-8"))
    return daten if isinstance(daten, dict) else {}


def _verschluesseln(daten: dict) -> bytes:
    schluessel = secrets.token_bytes(SCHLUESSEL)
    nonce = secrets.token_bytes(NONCE)
    chiffre, tag = _aes_gcm(schluessel, nonce, json.dumps(daten, ensure_ascii=False).encode("utf-8"))
    huelle = _dpapi(schluessel, True)
    return MAGIE + len(huelle).to_bytes(4, "big") + huelle + nonce + tag + chiffre


def _lesen_ohne_sperre() -> dict:
    if _pruefen_ohne_sperre() != "ok":
        return {}
    roh = _datei().read_bytes()
    sha = _sha(roh)
    if _merk["sha"] == sha and _merk["daten"] is not None:
        return json.loads(json.dumps(_merk["daten"]))
    try:
        daten = _entschluesseln(roh)
    except (OSError, ValueError):
        _vernichten_ohne_sperre("Tresor ließ sich nicht öffnen (beschädigt oder fremd)")
        return {}
    _merk.update(sha=sha, daten=daten)
    return json.loads(json.dumps(daten))


def _schreiben_ohne_sperre(daten: dict) -> None:
    roh = _verschluesseln(daten)
    neu = _sha(roh)
    alt = _datei()
    vorher = _sha(alt.read_bytes()) if alt.is_file() else None
    _siegel_schreiben([neu] + ([vorher] if vorher else []))      # Übergang: alter und neuer Stand gelten
    tmp = ordner() / f"inhalt.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        tmp.write_bytes(roh)
        os.replace(tmp, alt)
    finally:
        if tmp.exists():
            schreddern(tmp)
    _siegel_schreiben([neu])
    _merk.update(sha=neu, daten=json.loads(json.dumps(daten)))
    ZUSTAND.update(vernichtet="")


def lesen() -> dict:
    """Ganzer Inhalt (geprüft, nur im Speicher). Passt das Siegel nicht: vernichtet, {}."""
    if not verfuegbar():
        return json.loads(json.dumps(_speicher))
    with _sperre():
        return _lesen_ohne_sperre()


def bereich(name: str) -> dict:
    wert = lesen().get(name)
    return wert if isinstance(wert, dict) else {}


def bereich_setzen(name: str, werte: dict | None) -> None:
    """Einen Bereich ersetzen (None/leer: entfernen). Erneuert das Siegel."""
    if not verfuegbar():
        if werte:
            _speicher[name] = dict(werte)
        else:
            _speicher.pop(name, None)
        return
    with _sperre():
        daten = _lesen_ohne_sperre()
        if werte:
            daten[name] = dict(werte)
        else:
            daten.pop(name, None)
        if daten:
            _schreiben_ohne_sperre(daten)
        elif _datei().is_file():
            schreddern(_datei())
            _siegel_schreiben([])
            _merk.update(sha=None, daten=None)


def setzen(bereich_name: str, schluessel: str, wert: str | None) -> None:
    """Einen Wert in einem Bereich setzen oder (None/"") entfernen."""
    if not verfuegbar():
        b = dict(_speicher.get(bereich_name) or {})
    else:
        b = bereich(bereich_name)
    if wert:
        b[schluessel] = wert
    else:
        b.pop(schluessel, None)
    bereich_setzen(bereich_name, b)


def stand() -> dict:
    """Für die Anzeige: da?, Zahl der Schlüssel, Siegel (kurz), zuletzt geändert, ggf. Vernichtungsgrund."""
    raus = {"verfuegbar": verfuegbar(), "vernichtet": ZUSTAND["vernichtet"], "siegel": "", "geaendert": "",
            "schluessel": 0}
    try:
        daten = lesen()
        raus["schluessel"] = len(daten.get("schluessel") or {})
        if verfuegbar() and _datei().is_file():
            raus["siegel"] = _sha(_datei().read_bytes())[:12]
            raus["geaendert"] = time.strftime("%d.%m.%Y %H:%M", time.localtime(_datei().stat().st_mtime))
    except (OSError, RuntimeError):
        pass
    raus["vernichtet"] = ZUSTAND["vernichtet"]
    return raus
