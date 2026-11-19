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
TON_BYTES_JE_S = 16_000                  # AAC 128 kbit/s (mono); Stereo: 192 kbit/s
TON_BYTES_JE_S_STEREO = 24_000

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


def _ton_typ(plat, aac: bool, kanaele: int = 1):
    t = c_void_p()
    _pruefen(plat.MFCreateMediaType(byref(t)), "MFCreateMediaType")
    _setzen(t, MF_MT_MAJOR_TYPE, MFMediaType_Audio, "guid")
    _setzen(t, MF_MT_SUBTYPE, MFAudioFormat_AAC if aac else MFAudioFormat_PCM, "guid")
    _setzen(t, MF_MT_AUDIO_NUM_CHANNELS, kanaele, "u32")
    _setzen(t, MF_MT_AUDIO_SAMPLES_PER_SECOND, TON_RATE, "u32")
    _setzen(t, MF_MT_AUDIO_BITS_PER_SAMPLE, 16, "u32")
    if aac:
        _setzen(t, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, TON_BYTES_JE_S if kanaele == 1 else TON_BYTES_JE_S_STEREO, "u32")
    else:
        _setzen(t, MF_MT_AUDIO_BLOCK_ALIGNMENT, 2 * kanaele, "u32")
        _setzen(t, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, TON_RATE * 2 * kanaele, "u32")
    return t


def kanaele(wellen) -> int:
    """2 für Stereo (2, Proben), sonst 1."""
    import numpy as np
    w = np.asarray(wellen)
    return 2 if w.ndim == 2 and w.shape[0] == 2 else 1


def ton_vorbereiten(wellen, rate: int) -> bytes:
    """Wellenform (float −1…1, mono oder Stereo (2, Proben), beliebige Abtastrate) -> 16-Bit-PCM mit 48 kHz,
    Stereo verschränkt (L, R, L, R …)."""
    import numpy as np
    w = np.asarray(wellen, dtype=np.float32)
    spuren = list(w) if kanaele(w) == 2 else [w.ravel()]
    aus = []
    for s in spuren:
        if len(s) and rate != TON_RATE:
            n = max(1, round(len(s) * TON_RATE / rate))
            s = np.interp(np.linspace(0, len(s) - 1, n), np.arange(len(s)), s).astype(np.float32)
        aus.append(s)
    w = np.stack(aus, 1).ravel() if len(aus) == 2 else aus[0]
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


# ---------------------------------------------------------------------------
# Lesen: Einzelbilder (Source Reader mit Videoverarbeitung -> RGB32)
# ---------------------------------------------------------------------------

MFVideoFormat_RGB32 = _guid("00000016-0000-0010-8000-00aa00389b71")
MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING = _guid("fb394f3d-ccf1-42ee-bbb3-f9b845d5681d")
MF_PD_DURATION = _guid("6c990d33-bb8e-477a-8598-0d5d96fcd88a")
_ERSTES_BILD = 0xFFFFFFFC                 # MF_SOURCE_READER_FIRST_VIDEO_STREAM
_MEDIENQUELLE = 0xFFFFFFFF                # MF_SOURCE_READER_MEDIASOURCE
_ALLE_STROEME = 0xFFFFFFFE
_ENDE = 0x2                               # MF_SOURCE_READERF_ENDOFSTREAM
_GET_UINT32, _GET_UINT64 = 7, 8
_SR_SELECT, _SR_GET_CURRENT, _SR_SET_CURRENT, _SR_READ, _SR_ATTRIBUT = 4, 6, 7, 9, 12
_SMP_ZUSAMMEN = 41


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("r1", ctypes.c_uint16), ("r2", ctypes.c_uint16), ("r3", ctypes.c_uint16),
                ("wert", c_uint64), ("rest", c_uint64)]


def _get_u64(attr, schluessel: _GUID) -> int:
    wert = c_uint64()
    _ruf(attr, _GET_UINT64, (POINTER(_GUID), POINTER(c_uint64)), byref(schluessel), byref(wert), was="GetUINT64")
    return wert.value


def _get_u32(attr, schluessel: _GUID) -> int | None:
    wert = c_uint32()
    try:
        _ruf(attr, _GET_UINT32, (POINTER(_GUID), POINTER(c_uint32)), byref(schluessel), byref(wert), was="GetUINT32")
    except OSError:
        return None
    return wert.value


MAX_DAUER_S = 24 * 3600                   # längere Angaben gelten als unbekannt (präparierte Datei)
MAX_PUNKTE = 40_000_000                    # Bildpunkte je Einzelbild (8K = 33 Mio.)
MAX_KANTE = 1536                           # längere Kante danach (der Bild-Encoder verkleinert ohnehin)
MAX_LESEN = 600                            # Proben je Sprung, bis die Zielzeit erreicht ist
MAX_FOLGE = 200_000                        # Proben ohne Dauer (der Reihe nach), dann Schluss
_FEHLER, _TYP_NEU = 0x1, 0x20              # MF_SOURCE_READERF_ERROR, _CURRENTMEDIATYPECHANGED
_SR_POSITION = 8
_VT_I8 = 20


def auswahl(dauer_s: float, fps: float, n_bilder: int, ziel_fps: float = 1.0, max_bilder: int = 32) -> list[int]:
    """Nummern der Bilder, die gelesen werden: `ziel_fps` je Sekunde (Bild int(i · fps / ziel_fps)), bei mehr als
    `max_bilder` gleichmäßig verteilt – wie transformers' EmbeddingGemma2VideoProcessor.sample_frames. Rechnet nur
    die höchstens `max_bilder` Stellen aus (keine Liste über die ganze Dauer)."""
    import numpy as np
    if n_bilder <= 0:
        return []
    if fps > 0 and dauer_s > 0:
        schritt = fps / ziel_fps
        n = max(1, int(dauer_s * ziel_fps))
        stellen = range(n) if n <= max_bilder else np.linspace(0, n - 1, max_bilder, dtype=int)
        return [min(n_bilder - 1, int(int(i) * schritt)) for i in stellen]
    if n_bilder <= max_bilder:
        return list(range(n_bilder))
    return [int(i) for i in np.linspace(0, n_bilder - 1, max_bilder, dtype=int)]


def _ziel_zeiten(dauer_s: float, ziel_fps: float, max_bilder: int) -> list[float]:
    """Sekunden, an denen ein Bild genommen wird (i / ziel_fps, bei Überlänge gleichmäßig verteilt)."""
    import numpy as np
    n = max(1, int(dauer_s * ziel_fps))
    stellen = range(n) if n <= max_bilder else np.linspace(0, n - 1, max_bilder, dtype=int)
    return [int(i) / ziel_fps for i in stellen]


def bilder_lesen(pfad: Path, ziel_fps: float = 1.0, max_bilder: int = 32) -> list:
    """Video -> ausgewählte Einzelbilder als (H, W, 3) uint8 RGB (längere Kante höchstens MAX_KANTE).

    Mit bekannter Dauer springt der Leser zu jeder Zielzeit (höchstens `max_bilder` Sprünge) statt das ganze Video
    zu dekodieren. Ohne Dauer liest er der Reihe nach, behält 1 Bild je Zielabstand und dünnt aus, sobald es mehr
    als `max_bilder` werden (höchstens MAX_FOLGE Proben). Unplausible Angaben (Dauer, Bildgröße) gelten als Fehler
    bzw. als unbekannt."""
    plat, rw = _dlls()
    rw.MFCreateSourceReaderFromURL.restype = c_long
    plat.MFCreateAttributes.restype = c_long
    pfad = Path(pfad).resolve()
    if not pfad.is_file():
        raise FileNotFoundError(f"Video nicht gefunden: {pfad}")
    ole = ctypes.WinDLL("ole32")
    com = ole.CoInitializeEx(None, 0)
    try:
        _pruefen(plat.MFStartup(MF_VERSION, 0), "MFStartup")
    except Exception:
        if com in (0, 1):
            ole.CoUninitialize()
        raise
    attr, leser, typ = c_void_p(), c_void_p(), c_void_p()
    try:
        _pruefen(plat.MFCreateAttributes(byref(attr), 1), "MFCreateAttributes")
        _setzen(attr, MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, 1, "u32")
        _pruefen(rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(str(pfad)), attr, byref(leser)), "Video öffnen")
        _ruf(leser, _SR_SELECT, (c_uint32, ctypes.c_int), _ALLE_STROEME, 0, was="SetStreamSelection")
        _ruf(leser, _SR_SELECT, (c_uint32, ctypes.c_int), _ERSTES_BILD, 1, was="SetStreamSelection (Bild)")
        _pruefen(plat.MFCreateMediaType(byref(typ)), "MFCreateMediaType")
        _setzen(typ, MF_MT_MAJOR_TYPE, MFMediaType_Video, "guid")
        _setzen(typ, MF_MT_SUBTYPE, MFVideoFormat_RGB32, "guid")
        _ruf(leser, _SR_SET_CURRENT, (c_uint32, c_void_p, c_void_p), _ERSTES_BILD, None, typ,
             was="SetCurrentMediaType")
        form = _form(leser)
        pv = _PROPVARIANT()
        try:
            _ruf(leser, _SR_ATTRIBUT, (c_uint32, POINTER(_GUID), POINTER(_PROPVARIANT)), _MEDIENQUELLE,
                 byref(MF_PD_DURATION), byref(pv), was="GetPresentationAttribute")
            dauer_s = pv.wert / 10_000_000
        except OSError:
            dauer_s = 0.0
        if not 0 < dauer_s <= MAX_DAUER_S:
            dauer_s = 0.0

        def lesen_eins():
            """(Zeit in s, Bild oder None, Ende?) der nächsten Probe."""
            nonlocal form
            probe, flaggen, strom, zeit = c_void_p(), c_uint32(), c_uint32(), c_int64()
            _ruf(leser, _SR_READ, (c_uint32, c_uint32, POINTER(c_uint32), POINTER(c_uint32), POINTER(c_int64),
                                   POINTER(c_void_p)),
                 _ERSTES_BILD, 0, byref(strom), byref(flaggen), byref(zeit), byref(probe), was="ReadSample")
            if flaggen.value & _TYP_NEU:
                form = _form(leser)
            bild = None
            if probe:
                try:
                    bild = _rgb_aus_probe(probe, *form)
                finally:
                    _frei(probe)
            return zeit.value / 10_000_000, bild, bool(flaggen.value & (_ENDE | _FEHLER))

        raus = []
        if dauer_s:
            # Zeiten zählen ab dem ersten Bild (sein Zeitstempel ist nicht immer 0)
            start, erstes = None, None
            for _ in range(MAX_LESEN):
                zeit, erstes, ende = lesen_eins()
                if erstes is not None or ende:
                    start = zeit
                    break
            if erstes is None:
                return raus
            for t in _ziel_zeiten(dauer_s, ziel_fps, max_bilder):
                if t == 0:
                    raus.append(erstes)
                    continue
                pos = _PROPVARIANT(vt=_VT_I8, wert=int((start + t) * 10_000_000))
                _ruf(leser, _SR_POSITION, (POINTER(_GUID), POINTER(_PROPVARIANT)), byref(_GUID()), byref(pos),
                     was="SetCurrentPosition")
                for _ in range(MAX_LESEN):
                    zeit, bild, ende = lesen_eins()
                    if bild is not None and zeit - start >= t - 1e-3:      # erstes Bild ab der Zielzeit
                        raus.append(bild)
                        break
                    if ende:
                        break
            return raus
        abstand, naechste = 1.0 / max(ziel_fps, 1e-6), 0.0
        for _ in range(MAX_FOLGE):
            zeit, bild, ende = lesen_eins()
            if bild is not None and zeit >= naechste:
                raus.append(bild)
                naechste = zeit + abstand
                if len(raus) > max_bilder:                # ausdünnen: jedes zweite behalten, Abstand verdoppeln
                    raus, abstand = raus[::2], abstand * 2
            if ende:
                break
        return raus
    finally:
        for o in (typ, leser, attr):
            _frei(o)
        plat.MFShutdown()
        if com in (0, 1):
            ole.CoUninitialize()


def _form(leser) -> tuple[int, int, int]:
    """(Breite, Höhe, Zeilenschritt) des aktuellen Bildformats; zu große Bilder sind ein Fehler."""
    aktuell = c_void_p()
    _ruf(leser, _SR_GET_CURRENT, (c_uint32, POINTER(c_void_p)), _ERSTES_BILD, byref(aktuell),
         was="GetCurrentMediaType")
    try:
        groesse = _get_u64(aktuell, MF_MT_FRAME_SIZE)
        W, H = groesse >> 32, groesse & 0xFFFFFFFF
        if not (0 < W and 0 < H and W * H <= MAX_PUNKTE):
            raise ValueError(f"unplausible Bildgröße {W}×{H}")
        schritt = _get_u32(aktuell, MF_MT_DEFAULT_STRIDE)
        schritt = W * 4 if schritt is None else ctypes.c_int32(schritt).value
        if abs(schritt) < W * 4:
            raise ValueError("Zeilenschritt kleiner als die Bildbreite")
        return W, H, schritt
    finally:
        _frei(aktuell)


def _rgb_aus_probe(probe, W: int, H: int, schritt: int):
    import numpy as np
    puffer = c_void_p()
    _ruf(probe, _SMP_ZUSAMMEN, (POINTER(c_void_p),), byref(puffer), was="ConvertToContiguousBuffer")
    try:
        zeiger, maximal, laenge = ctypes.POINTER(ctypes.c_ubyte)(), c_uint32(), c_uint32()
        _ruf(puffer, _BUF_LOCK, (POINTER(POINTER(ctypes.c_ubyte)), POINTER(c_uint32), POINTER(c_uint32)),
             byref(zeiger), byref(maximal), byref(laenge), was="Lock")
        try:
            zeile = abs(schritt)
            if laenge.value < zeile * H:
                raise ValueError("Bildpuffer kürzer als das Bildformat")
            roh = np.frombuffer(ctypes.string_at(zeiger, zeile * H), np.uint8)
        finally:
            _ruf(puffer, _BUF_UNLOCK, (), was="Unlock")
    finally:
        _frei(puffer)
    bild = roh.reshape(H, zeile)[:, :W * 4].reshape(H, W, 4)
    if schritt < 0:                                       # von unten nach oben gespeichert
        bild = bild[::-1]
    bild = bild[..., 2::-1]                               # BGRA -> RGB
    if max(W, H) > MAX_KANTE:
        from PIL import Image
        img = Image.fromarray(np.ascontiguousarray(bild))
        img.thumbnail((MAX_KANTE, MAX_KANTE), Image.BICUBIC)
        return np.asarray(img)
    return np.ascontiguousarray(bild)


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
    Datenrate an. ton: (Wellenform float mono oder Stereo (2, Proben), Abtastrate) -> AAC-Tonspur, im Takt
    der Bilder geschrieben."""
    import itertools
    import numpy as np
    plat, rw = _dlls()
    folge = iter(bilder)
    erstes = next(folge)
    H, W = erstes.shape[0] & ~1, erstes.shape[1] & ~1
    dauer = round(10_000_000 / fps)                       # Media Foundation zählt in 100 ns
    pcm = ton_vorbereiten(*ton) if ton is not None else b""
    k = kanaele(ton[0]) if ton is not None else 1
    fb = 2 * k                                            # Bytes je Abtastzeitpunkt (16 Bit je Kanal)
    ton_stueck = TON_RATE // 10 * fb                     # 100 ms je Ton-Probe (Bytes)
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
            typen += [_ton_typ(plat, aac=True, kanaele=k), _ton_typ(plat, aac=False, kanaele=k)]
            _ruf(schreiber, _SW_ADD_STREAM, (c_void_p, POINTER(c_uint32)), typen[2], byref(ton_strom),
                 was="AddStream (Ton)")
            _ruf(schreiber, _SW_SET_INPUT, (c_uint32, c_void_p, c_void_p), ton_strom.value, typen[3], None,
                 was="SetInputMediaType (Ton)")
        _ruf(schreiber, _SW_BEGIN, (), was="BeginWriting")
        ton_pos = 0

        def ton_bis(zeit: int) -> None:                  # Ton bis zur Zeit `zeit` (100 ns) nachschreiben
            nonlocal ton_pos
            while ton_pos < len(pcm) and ton_pos // fb * 10_000_000 // TON_RATE < zeit:
                stueck = pcm[ton_pos:ton_pos + ton_stueck]
                _probe(plat, schreiber, ton_strom.value, stueck, ton_pos // fb * 10_000_000 // TON_RATE,
                       len(stueck) // fb * 10_000_000 // TON_RATE)
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
            luecke(strom.value, ton_pos // fb * 10_000_000 // TON_RATE)
            ton_bis(ton_pos // fb * 10_000_000 // TON_RATE + 1)
        _ruf(schreiber, _SW_FINALIZE, (), was="Finalize")
    finally:
        for t in typen:
            _frei(t)
        _frei(schreiber)
        plat.MFShutdown()
        if com in (0, 1):                                 # S_OK / S_FALSE: diesem Aufruf gehört die Anmeldung
            ole.CoUninitialize()
