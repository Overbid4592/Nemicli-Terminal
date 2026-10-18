"""
videodatei.py - MP4 (H.264) mit dem Encoder, den Windows mitbringt (Media Foundation über ctypes).

Kein OpenCV, kein ffmpeg, kein zusätzliches Paket. Eingabe: Bilder (T, H, W, 3) uint8 RGB.
numpy rechnet RGB -> NV12 (BT.709, 16–235), Windows kodiert nur. Der Encoder schreibt keine Farbangaben in
den Datenstrom; Player nehmen ab 720 Zeilen BT.709 an (OpenCV/ffmpeg rechnen stur mit BT.601 – Farben dort
leicht verschoben). Höhe und Breite müssen gerade sein (sonst wird die letzte Zeile/Spalte abgeschnitten).
"""
from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import POINTER, byref, c_int64, c_long, c_uint32, c_uint64, c_void_p
from pathlib import Path

MF_VERSION = 0x00020070
BITS_JE_PIXEL = 0.25                     # Zielrate: Bits je Bildpunkt und Bild (832×1216 bei 24 fps ≈ 6 Mbit/s)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    u = uuid.UUID(text)
    g = _GUID(u.fields[0], u.fields[1], u.fields[2])
    g.Data4[:] = u.bytes[8:]
    return g


MF_MT_MAJOR_TYPE = _guid("48eba18e-f8c9-4687-bf11-0a74c9f96a8f")
MF_MT_SUBTYPE = _guid("f7e34c9a-42e8-4714-b74b-cb29d72c35e5")
MF_MT_AVG_BITRATE = _guid("20332624-fb0d-4d9e-bd0d-cbf6786c102e")
MF_MT_INTERLACE_MODE = _guid("e2724bb8-e676-4806-b4b2-a8d6efb44ccd")
MF_MT_FRAME_SIZE = _guid("1652c33d-d6b2-4012-b834-72030849a37d")
MF_MT_FRAME_RATE = _guid("c459a2e8-3d2c-4e44-b132-fee5156c7bb0")
MF_MT_PIXEL_ASPECT_RATIO = _guid("c6376a1e-8d0a-4027-be45-6d9a0ad39bb6")
MF_MT_DEFAULT_STRIDE = _guid("644b4e48-1e02-4516-b0eb-c01ca9d49ac6")
MF_MT_YUV_MATRIX = _guid("3e23d450-2c75-4d25-a00e-b91670d12327")
MF_MT_VIDEO_NOMINAL_RANGE = _guid("c21b8ee5-b956-4071-8daf-325edf5cab11")
MF_MT_VIDEO_PRIMARIES = _guid("dbfbe4d7-0740-4ee0-8192-850ab0e21935")
MF_MT_TRANSFER_FUNCTION = _guid("5fb0fce9-be5c-4935-a811-ec838f8eed93")
MF_MT_MPEG2_PROFILE = _guid("ad76a80b-2d5c-4e0b-b375-64e520137036")
MFMediaType_Video = _guid("73646976-0000-0010-8000-00aa00389b71")
MFVideoFormat_H264 = _guid("34363248-0000-0010-8000-00aa00389b71")
MFVideoFormat_NV12 = _guid("3231564e-0000-0010-8000-00aa00389b71")
PROGRESSIV, BT709, BEREICH_16_235, PRIMAER_709, KURVE_709, PROFIL_HIGH = 2, 1, 2, 2, 5, 100
# Ton: AAC (Windows-Encoder kann nur 44,1 und 48 kHz), Eingabe 16-Bit-PCM mono
MFMediaType_Audio = _guid("73647561-0000-0010-8000-00aa00389b71")
MFAudioFormat_PCM = _guid("00000001-0000-0010-8000-00aa00389b71")
MFAudioFormat_AAC = _guid("00001610-0000-0010-8000-00aa00389b71")
MF_MT_AUDIO_NUM_CHANNELS = _guid("37e48bf5-645e-4c5b-89de-ada9e29b696a")
MF_MT_AUDIO_SAMPLES_PER_SECOND = _guid("5faeeae7-0290-4c31-9e8a-c534f68d9dba")
MF_MT_AUDIO_BITS_PER_SAMPLE = _guid("f2deb57f-40fa-4764-aa33-ed4f2d1ff669")
MF_MT_AUDIO_AVG_BYTES_PER_SECOND = _guid("1aab75c8-cfef-451c-ab95-ac034b8e1731")
MF_MT_AUDIO_BLOCK_ALIGNMENT = _guid("322de230-9eeb-43bd-ab7a-ff412251541d")
TON_RATE = 48_000
TON_BYTES_JE_S = 16_000                  # AAC 128 kbit/s

# Plätze in den COM-Tabellen (IUnknown 0–2, IMFAttributes 3–32)
_SET_UINT32, _SET_UINT64, _SET_GUID = 21, 22, 24
_SW_ADD_STREAM, _SW_SET_INPUT, _SW_BEGIN, _SW_WRITE, _SW_TICK, _SW_FINALIZE = 3, 4, 5, 6, 7, 11
_BUF_LOCK, _BUF_UNLOCK, _BUF_SET_LEN = 3, 4, 6
_SMP_SET_TIME, _SMP_SET_DAUER, _SMP_ADD_BUFFER = 36, 38, 42


def _dlls():
    plat, rw = ctypes.WinDLL("mfplat"), ctypes.WinDLL("mfreadwrite")
    for f in (plat.MFStartup, plat.MFShutdown, plat.MFCreateMediaType, plat.MFCreateSample,
              plat.MFCreateMemoryBuffer, rw.MFCreateSinkWriterFromURL):
        f.restype = c_long
    return plat, rw


def bereit() -> bool:
    """Gibt es den Windows-Encoder (Media Foundation)? Fehlt z. B. auf Windows-N-Ausgaben ohne Media Feature Pack."""
    if sys.platform != "win32":
        return False
    try:
        _dlls()
        return True
    except (OSError, AttributeError):
        return False


def _pruefen(hr: int, was: str) -> None:
    if hr < 0:
        raise OSError(f"Media Foundation: {was} fehlgeschlagen (0x{hr & 0xFFFFFFFF:08X})")


def _ruf(obj, platz: int, argtypes: tuple, *args, was: str) -> None:
    tabelle = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    _pruefen(ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)(tabelle[platz])(obj, *args), was)


def _frei(obj) -> None:
    if obj:
        tabelle = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(tabelle[2])(obj)


def _setzen(attr, schluessel: _GUID, wert, art: str) -> None:
    if art == "guid":
        _ruf(attr, _SET_GUID, (POINTER(_GUID), POINTER(_GUID)), byref(schluessel), byref(wert), was="SetGUID")
    elif art == "u32":
        _ruf(attr, _SET_UINT32, (POINTER(_GUID), c_uint32), byref(schluessel), wert, was="SetUINT32")
    else:
        _ruf(attr, _SET_UINT64, (POINTER(_GUID), c_uint64), byref(schluessel), wert, was="SetUINT64")


def _typ(plat, untertyp: _GUID, W: int, H: int, fps: int, bitrate: int | None):
    t = c_void_p()
    _pruefen(plat.MFCreateMediaType(byref(t)), "MFCreateMediaType")
    _setzen(t, MF_MT_MAJOR_TYPE, MFMediaType_Video, "guid")
    _setzen(t, MF_MT_SUBTYPE, untertyp, "guid")
    _setzen(t, MF_MT_INTERLACE_MODE, PROGRESSIV, "u32")
    _setzen(t, MF_MT_FRAME_SIZE, (W << 32) | H, "u64")
    _setzen(t, MF_MT_FRAME_RATE, (fps << 32) | 1, "u64")
    _setzen(t, MF_MT_PIXEL_ASPECT_RATIO, (1 << 32) | 1, "u64")
    _setzen(t, MF_MT_YUV_MATRIX, BT709, "u32")
    _setzen(t, MF_MT_VIDEO_NOMINAL_RANGE, BEREICH_16_235, "u32")
    _setzen(t, MF_MT_VIDEO_PRIMARIES, PRIMAER_709, "u32")
    _setzen(t, MF_MT_TRANSFER_FUNCTION, KURVE_709, "u32")
    if bitrate:
        _setzen(t, MF_MT_AVG_BITRATE, bitrate, "u32")
        _setzen(t, MF_MT_MPEG2_PROFILE, PROFIL_HIGH, "u32")     # Vorgabe wäre Baseline
    else:
        _setzen(t, MF_MT_DEFAULT_STRIDE, W, "u32")
    return t


def _ton_typ(plat, aac: bool):
    t = c_void_p()
    _pruefen(plat.MFCreateMediaType(byref(t)), "MFCreateMediaType")
    _setzen(t, MF_MT_MAJOR_TYPE, MFMediaType_Audio, "guid")
    _setzen(t, MF_MT_SUBTYPE, MFAudioFormat_AAC if aac else MFAudioFormat_PCM, "guid")
    _setzen(t, MF_MT_AUDIO_NUM_CHANNELS, 1, "u32")
    _setzen(t, MF_MT_AUDIO_SAMPLES_PER_SECOND, TON_RATE, "u32")
    _setzen(t, MF_MT_AUDIO_BITS_PER_SAMPLE, 16, "u32")
    if aac:
        _setzen(t, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, TON_BYTES_JE_S, "u32")
    else:
        _setzen(t, MF_MT_AUDIO_BLOCK_ALIGNMENT, 2, "u32")
        _setzen(t, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, TON_RATE * 2, "u32")
    return t


def ton_vorbereiten(wellen, rate: int) -> bytes:
    """Wellenform (float −1…1, mono, beliebige Abtastrate) -> 16-Bit-PCM mit 48 kHz."""
    import numpy as np
    w = np.asarray(wellen, dtype=np.float32).ravel()
    if len(w) and rate != TON_RATE:
        n = max(1, round(len(w) * TON_RATE / rate))
        w = np.interp(np.linspace(0, len(w) - 1, n), np.arange(len(w)), w).astype(np.float32)
    return (np.clip(w, -1, 1) * 32767).astype("<i2").tobytes()


def _probe(plat, schreiber, strom: int, daten: bytes, zeit: int, dauer: int) -> None:
    """Ein Stück Daten als Probe schreiben (Zeiten in 100 ns)."""
    puffer, probe = c_void_p(), c_void_p()
    try:
        _pruefen(plat.MFCreateMemoryBuffer(len(daten), byref(puffer)), "MFCreateMemoryBuffer")
        ziel = ctypes.POINTER(ctypes.c_ubyte)()
        _ruf(puffer, _BUF_LOCK, (POINTER(POINTER(ctypes.c_ubyte)), c_void_p, c_void_p), byref(ziel),
             None, None, was="Lock")
        ctypes.memmove(ziel, daten, len(daten))
        _ruf(puffer, _BUF_UNLOCK, (), was="Unlock")
        _ruf(puffer, _BUF_SET_LEN, (c_uint32,), len(daten), was="SetCurrentLength")
        _pruefen(plat.MFCreateSample(byref(probe)), "MFCreateSample")
        _ruf(probe, _SMP_ADD_BUFFER, (c_void_p,), puffer, was="AddBuffer")
        _ruf(probe, _SMP_SET_TIME, (c_int64,), zeit, was="SetSampleTime")
        _ruf(probe, _SMP_SET_DAUER, (c_int64,), dauer, was="SetSampleDuration")
        _ruf(schreiber, _SW_WRITE, (c_uint32, c_void_p), strom, probe, was="WriteSample")
    finally:
        _frei(probe)
        _frei(puffer)


def nv12(rgb):
    """(H, W, 3) uint8 RGB -> NV12-Bytes (Y-Ebene, dann U/V abwechselnd je 2×2 Bildpunkte), BT.709 16–235."""
    import numpy as np
    H, W = rgb.shape[0] & ~1, rgb.shape[1] & ~1
    f = rgb[:H, :W].astype(np.float32)
    y = 16 + f @ np.array([0.1826, 0.6142, 0.0620], np.float32)
    m = f.reshape(H // 2, 2, W // 2, 2, 3).mean(axis=(1, 3))
    u = 128 + m @ np.array([-0.1006, -0.3386, 0.4392], np.float32)
    v = 128 + m @ np.array([0.4392, -0.3989, -0.0403], np.float32)
    uv = np.stack([u, v], -1)
    return np.concatenate([y.ravel(), uv.ravel()]).round().clip(0, 255).astype(np.uint8)


def schreiben(bilder, pfad: Path, fps: int, rate: float = 1.0, ton=None) -> None:
    """bilder (T, H, W, 3) uint8 RGB – oder eine Folge solcher Bilder, die erst beim Schreiben entsteht
    (dann liegt nie das ganze Video im Speicher) -> MP4 (H.264) über Media Foundation; `rate` hebt die
    Datenrate an. ton: (Wellenform float mono, Abtastrate) -> AAC-Tonspur, im Takt der Bilder geschrieben."""
    import itertools
    import numpy as np
    plat, rw = _dlls()
    folge = iter(bilder)
    erstes = next(folge)
    H, W = erstes.shape[0] & ~1, erstes.shape[1] & ~1
    dauer = round(10_000_000 / fps)                       # Media Foundation zählt in 100 ns
    pcm = ton_vorbereiten(*ton) if ton is not None else b""
    ton_stueck = TON_RATE // 10 * 2                      # 100 ms je Ton-Probe (Bytes)
    pfad = Path(pfad).resolve()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    ole = ctypes.WinDLL("ole32")
    com = ole.CoInitializeEx(None, 0)                     # 0 = COINIT_MULTITHREADED; Fremd-Modus -> nicht beenden
    _pruefen(plat.MFStartup(MF_VERSION, 0), "MFStartup")
    schreiber, typen = c_void_p(), []
    try:
        _pruefen(rw.MFCreateSinkWriterFromURL(ctypes.c_wchar_p(str(pfad)), None, None, byref(schreiber)),
                 "MFCreateSinkWriterFromURL")
        typen += [_typ(plat, MFVideoFormat_H264, W, H, fps, max(1_000_000, int(W * H * fps * BITS_JE_PIXEL * rate))),
                  _typ(plat, MFVideoFormat_NV12, W, H, fps, None)]
        strom = c_uint32()
        _ruf(schreiber, _SW_ADD_STREAM, (c_void_p, POINTER(c_uint32)), typen[0], byref(strom), was="AddStream")
        _ruf(schreiber, _SW_SET_INPUT, (c_uint32, c_void_p, c_void_p), strom.value, typen[1], None,
             was="SetInputMediaType")
        ton_strom = c_uint32()
        if pcm:
            typen += [_ton_typ(plat, aac=True), _ton_typ(plat, aac=False)]
            _ruf(schreiber, _SW_ADD_STREAM, (c_void_p, POINTER(c_uint32)), typen[2], byref(ton_strom),
                 was="AddStream (Ton)")
            _ruf(schreiber, _SW_SET_INPUT, (c_uint32, c_void_p, c_void_p), ton_strom.value, typen[3], None,
                 was="SetInputMediaType (Ton)")
        _ruf(schreiber, _SW_BEGIN, (), was="BeginWriting")
        ton_pos = 0

        def ton_bis(zeit: int) -> None:                  # Ton bis zur Zeit `zeit` (100 ns) nachschreiben
            nonlocal ton_pos
            while ton_pos < len(pcm) and ton_pos // 2 * 10_000_000 // TON_RATE < zeit:
                stueck = pcm[ton_pos:ton_pos + ton_stueck]
                _probe(plat, schreiber, ton_strom.value, stueck, ton_pos // 2 * 10_000_000 // TON_RATE,
                       len(stueck) // 2 * 10_000_000 // TON_RATE)
                ton_pos += len(stueck)

        # Endet eine Spur früher, wartet der Schreiber bei jeder Probe der anderen auf sie (~0,4 s je
        # Probe). Eine Lücke meldet man mit SendStreamTick.
        def luecke(nr: int, zeit: int) -> None:
            _ruf(schreiber, _SW_TICK, (c_uint32, c_int64), nr, zeit, was="SendStreamTick")

        zeit = 0
        for i, bild in enumerate(itertools.chain([erstes], folge)):
            zeit = round(i * 10_000_000 / fps)
            ton_bis((i + 1) * dauer)
            if pcm and ton_pos >= len(pcm):
                luecke(ton_strom.value, zeit)
            _probe(plat, schreiber, strom.value, np.ascontiguousarray(nv12(bild)).tobytes(), zeit, dauer)
        while ton_pos < len(pcm):                         # Ton, der länger ist als das Bild
            luecke(strom.value, ton_pos // 2 * 10_000_000 // TON_RATE)
            ton_bis(ton_pos // 2 * 10_000_000 // TON_RATE + 1)
        _ruf(schreiber, _SW_FINALIZE, (), was="Finalize")
    finally:
        for t in typen:
            _frei(t)
        _frei(schreiber)
        plat.MFShutdown()
        if com in (0, 1):                                 # S_OK / S_FALSE: diesem Aufruf gehört die Anmeldung
            ole.CoUninitialize()
