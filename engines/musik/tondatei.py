"""tondatei.py – Audiodateien einlesen: WAV selbst (PCM 8/16/24/32 Bit, Float 32/64), alles andere
(MP3, M4A/AAC, FLAC, WMA …) über die Decoder von Windows (Media Foundation, Source Reader).
Ergebnis: `lesen` Stereo float32 [2, N] bei 48 kHz, `mono` eine Spur in wählbarer Rate (16 kHz fürs Gedächtnis).
"""
from __future__ import annotations

import ctypes
import struct
from ctypes import POINTER, byref, c_int64, c_uint32, c_void_p
from math import gcd
from pathlib import Path

import numpy as np

RATE = 48_000
ENDUNGEN = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".wma", ".ogg", ".opus", ".mp4")

_ERSTER_TON = 0xFFFFFFFD                 # MF_SOURCE_READER_FIRST_AUDIO_STREAM
_ALLE = 0xFFFFFFFE                       # MF_SOURCE_READER_ALL_STREAMS
_ENDE = 0x2                              # MF_SOURCE_READERF_ENDOFSTREAM
_FEHLER = 0x1                            # MF_SOURCE_READERF_ERROR
# Plätze in den COM-Tabellen
_GET_UINT32 = 7
_SR_SELECT, _SR_GET_CURRENT, _SR_SET_CURRENT, _SR_READ = 4, 6, 7, 9
_SMP_ZUSAMMEN = 41
_BUF_LOCK, _BUF_UNLOCK = 3, 4


RATE_MIN, RATE_MAX = 4_000, 384_000      # andere Abtastraten sind kaputt oder präpariert (Umrechnen frisse GB)
INFO_MAX = 1 << 20                       # so viel liest `info` höchstens aus einem LIST/INFO-Block
MAX_SEKUNDEN = 3 * 3600                  # Obergrenze für `mono`, auch ohne Angabe


def _bloecke(f, groesse: int):
    """(Kennung, Inhalt-Anfang, Länge) der RIFF-Blöcke; liest nur die Köpfe (seek)."""
    pos = 12
    while pos + 8 <= groesse:
        f.seek(pos)
        kopf = f.read(8)
        if len(kopf) < 8:
            return
        kennung, laenge = kopf[:4], struct.unpack("<I", kopf[4:])[0]
        yield kennung, pos + 8, min(laenge, groesse - pos - 8)
        pos += 8 + laenge + (laenge & 1)


def info(pfad: Path) -> dict:
    """LIST/INFO-Felder einer WAV-Datei (INAM, ICMT …), sonst leer. Liest nur Blockköpfe und den INFO-Block."""
    aus = {}
    try:
        groesse = Path(pfad).stat().st_size
        with open(pfad, "rb") as f:
            if f.read(12)[8:12] != b"WAVE":
                return {}
            for kennung, anfang, laenge in _bloecke(f, groesse):
                if kennung != b"LIST":
                    continue
                f.seek(anfang)
                roh = f.read(min(laenge, INFO_MAX))
                if roh[:4] != b"INFO":
                    continue
                q = 4
                while q + 8 <= len(roh):
                    k, n = roh[q:q + 4].decode("ascii", "replace"), struct.unpack("<I", roh[q + 4:q + 8])[0]
                    wert = roh[q + 8:q + 8 + n].rstrip(b"\0")
                    try:
                        aus[k] = wert.decode("utf-8")
                    except UnicodeDecodeError:
                        aus[k] = wert.decode("cp1252", "replace")
                    q += 8 + n + (n & 1)
    except (OSError, struct.error):
        return aus
    return aus


def _rate_pruefen(rate: int) -> int:
    if not RATE_MIN <= int(rate) <= RATE_MAX:
        raise ValueError(f"unplausible Abtastrate {rate} Hz")
    return int(rate)


def _wav(pfad: Path, max_sekunden: float | None = None) -> tuple[np.ndarray, int]:
    """WAV lesen; mit `max_sekunden` nur so viele Proben (die Datei wird nicht ganz geladen)."""
    groesse = Path(pfad).stat().st_size
    with open(pfad, "rb") as f:
        kopf = f.read(12)
        if kopf[:4] != b"RIFF" or kopf[8:12] != b"WAVE":
            raise ValueError("keine WAV-Datei")
        fmt = daten_ort = None
        for kennung, anfang, laenge in _bloecke(f, groesse):
            if kennung == b"fmt ":
                f.seek(anfang)
                fmt = f.read(min(laenge, 64))
            elif kennung == b"data":
                daten_ort = (anfang, laenge)
        if fmt is None or daten_ort is None or len(fmt) < 16:
            raise ValueError("WAV ohne fmt- oder data-Teil")
        art, kanaele, rate = struct.unpack("<HHI", fmt[:8])
        bits = struct.unpack("<H", fmt[14:16])[0]
        if art == 0xFFFE and len(fmt) >= 26:                     # WAVE_FORMAT_EXTENSIBLE: Art steht in der GUID
            art = struct.unpack("<H", fmt[24:26])[0]
        if not 1 <= kanaele <= 32 or bits not in (8, 16, 24, 32, 64):
            raise ValueError(f"WAV mit {kanaele} Kanälen und {bits} Bit wird nicht gelesen")
        rate = _rate_pruefen(rate)
        breite = bits // 8
        anfang, laenge = daten_ort
        if max_sekunden:
            laenge = min(laenge, int(max_sekunden * rate + rate) * breite * kanaele)
        f.seek(anfang)
        daten = f.read(laenge)
    daten = daten[:len(daten) - len(daten) % (breite * kanaele)]
    if art == 3 and bits in (32, 64):
        x = np.frombuffer(daten, "<f4" if bits == 32 else "<f8").astype(np.float32)
    elif art == 1 and bits == 8:
        x = (np.frombuffer(daten, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif art == 1 and bits == 16:
        x = np.frombuffer(daten, "<i2").astype(np.float32) / 32768.0
    elif art == 1 and bits == 24:
        b = np.frombuffer(daten, np.uint8).reshape(-1, 3).astype(np.int32)
        x = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8 >> 8).astype(np.float32) / 8388608.0
    elif art == 1 and bits == 32:
        x = np.frombuffer(daten, "<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"WAV-Format {art} mit {bits} Bit wird nicht gelesen")
    return x.reshape(-1, kanaele).T, rate


def _attr_u32(obj, schluessel) -> int:
    import videodatei as VD
    wert = c_uint32()
    VD._ruf(obj, _GET_UINT32, (POINTER(VD._GUID), POINTER(c_uint32)), byref(schluessel), byref(wert),
            was="GetUINT32")
    return wert.value


def _windows(pfad: Path, max_sekunden: float | None = None) -> tuple[np.ndarray, int]:
    """Decoder von Windows: Source Reader liefert Float-PCM in der Original-Rate; mit `max_sekunden` hört er
    danach auf (lange Dateien werden nicht ganz dekodiert)."""
    import videodatei as VD
    plat, rw = VD._dlls()
    rw.MFCreateSourceReaderFromURL.restype = ctypes.c_long
    ole = ctypes.WinDLL("ole32")
    com = ole.CoInitializeEx(None, 0)
    try:
        VD._pruefen(plat.MFStartup(VD.MF_VERSION, 0), "MFStartup")
    except Exception:
        if com in (0, 1):
            ole.CoUninitialize()
        raise
    leser, typ, aktuell = c_void_p(), c_void_p(), c_void_p()
    teile: list[np.ndarray] = []
    try:
        VD._pruefen(rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(str(Path(pfad).resolve())), None,
                                                   byref(leser)), "Datei öffnen")
        VD._ruf(leser, _SR_SELECT, (c_uint32, ctypes.c_int), _ALLE, 0, was="SetStreamSelection")
        VD._ruf(leser, _SR_SELECT, (c_uint32, ctypes.c_int), _ERSTER_TON, 1, was="SetStreamSelection (Ton)")
        VD._pruefen(plat.MFCreateMediaType(byref(typ)), "MFCreateMediaType")
        VD._setzen(typ, VD.MF_MT_MAJOR_TYPE, VD.MFMediaType_Audio, "guid")
        VD._setzen(typ, VD.MF_MT_SUBTYPE, VD._guid("00000003-0000-0010-8000-00aa00389b71"), "guid")   # Float
        VD._ruf(leser, _SR_SET_CURRENT, (c_uint32, c_void_p, c_void_p), _ERSTER_TON, None, typ,
                was="SetCurrentMediaType")
        VD._ruf(leser, _SR_GET_CURRENT, (c_uint32, POINTER(c_void_p)), _ERSTER_TON, byref(aktuell),
                was="GetCurrentMediaType")
        kanaele = _attr_u32(aktuell, VD.MF_MT_AUDIO_NUM_CHANNELS)
        rate = _rate_pruefen(_attr_u32(aktuell, VD.MF_MT_AUDIO_SAMPLES_PER_SECOND))
        if not 1 <= kanaele <= 32:
            raise ValueError(f"Ton mit {kanaele} Kanälen wird nicht gelesen")
        grenze = int(max_sekunden * rate * kanaele) if max_sekunden else None
        gelesen = 0
        while grenze is None or gelesen < grenze:
            probe, flaggen, strom, zeit = c_void_p(), c_uint32(), c_uint32(), c_int64()
            VD._ruf(leser, _SR_READ, (c_uint32, c_uint32, POINTER(c_uint32), POINTER(c_uint32),
                                      POINTER(c_int64), POINTER(c_void_p)),
                    _ERSTER_TON, 0, byref(strom), byref(flaggen), byref(zeit), byref(probe), was="ReadSample")
            if probe:
                puffer = c_void_p()
                try:
                    VD._ruf(probe, _SMP_ZUSAMMEN, (POINTER(c_void_p),), byref(puffer),
                            was="ConvertToContiguousBuffer")
                    zeiger, maximal, laenge = ctypes.POINTER(ctypes.c_ubyte)(), c_uint32(), c_uint32()
                    VD._ruf(puffer, _BUF_LOCK, (POINTER(POINTER(ctypes.c_ubyte)), POINTER(c_uint32),
                                                POINTER(c_uint32)), byref(zeiger), byref(maximal), byref(laenge),
                            was="Lock")
                    try:
                        teile.append(np.frombuffer(ctypes.string_at(zeiger, laenge.value & ~3), "<f4").copy())
                        gelesen += len(teile[-1])
                    finally:
                        VD._ruf(puffer, _BUF_UNLOCK, (), was="Unlock")
                finally:
                    VD._frei(puffer)
                    VD._frei(probe)
            if flaggen.value & (_ENDE | _FEHLER):
                break
    finally:
        for o in (aktuell, typ, leser):
            VD._frei(o)
        plat.MFShutdown()
        if com in (0, 1):
            ole.CoUninitialize()
    x = np.concatenate(teile) if teile else np.zeros(0, np.float32)
    x = x[:len(x) - len(x) % max(1, kanaele)]
    return x.reshape(-1, max(1, kanaele)).T, rate


def stereo_48k(x: np.ndarray, rate: int) -> np.ndarray:
    """[K, N] bei `rate` -> [2, N'] float32 bei 48 kHz (Mono doppelt, mehr als zwei Kanäle: die ersten zwei)."""
    x = np.asarray(x, np.float32)
    rate = _rate_pruefen(rate)
    if x.shape[0] == 1:
        x = np.repeat(x, 2, 0)
    x = x[:2]
    if rate != RATE and x.shape[-1]:
        from scipy.signal import resample_poly
        g = gcd(RATE, int(rate))
        x = resample_poly(x, RATE // g, int(rate) // g, axis=-1).astype(np.float32)
    return np.ascontiguousarray(x)


def _roh(pfad: Path, max_sekunden: float | None = None) -> tuple[np.ndarray, int]:
    pfad = Path(pfad)
    if not pfad.is_file():
        raise FileNotFoundError(f"Audiodatei nicht gefunden: {pfad}")
    if pfad.suffix.lower() == ".wav":
        try:
            return _wav(pfad, max_sekunden)
        except (ValueError, struct.error):
            pass
    return _windows(pfad, max_sekunden)


def mono(pfad: Path, rate: int = 16_000, max_sekunden: float | None = None) -> np.ndarray:
    """Audiodatei -> Mono float32 [N] bei `rate` (Mittel der Kanäle), auf `max_sekunden` gekürzt (höchstens
    MAX_SEKUNDEN; gelesen wird nur so viel)."""
    max_sekunden = min(max_sekunden or MAX_SEKUNDEN, MAX_SEKUNDEN)
    x, quelle = _roh(pfad, max_sekunden)
    if max_sekunden:
        x = x[:, :int(max_sekunden * quelle) + quelle]
    x = np.asarray(x, np.float32).mean(axis=0)
    if quelle != rate and x.size:
        from scipy.signal import resample_poly
        g = gcd(rate, int(quelle))
        x = resample_poly(x, rate // g, int(quelle) // g).astype(np.float32)
    if max_sekunden:
        x = x[:int(max_sekunden * rate)]
    return np.ascontiguousarray(x, np.float32)


def lesen(pfad: Path, max_sekunden: float | None = None) -> np.ndarray:
    """Audiodatei -> Stereo float32 [2, N] bei 48 kHz, wahlweise auf `max_sekunden` gekürzt."""
    x, rate = _roh(pfad, max_sekunden)
    if max_sekunden:
        x = x[:, :int(max_sekunden * rate) + rate]                  # vor dem Umrechnen kürzen (schneller)
    ton = stereo_48k(x, rate)
    if max_sekunden:
        ton = ton[:, :int(max_sekunden * RATE)]
    return ton
