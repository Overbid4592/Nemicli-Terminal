"""Datenträger-Übersicht für den Vollscan: Festplatte → Partition → Volume.

Nur lesende Abfragen über kernel32 (DeviceIoControl mit Zugriff 0 – geht
ohne Adminrechte): Modell, Anschluss, SSD/HDD, Größe und Partitionstabelle
jeder Festplatte, dazu alle Volumes mit Bezeichnung, Dateisystem und
Belegung. Volumes werden über ihre Lage auf der Platte den Partitionen
zugeordnet.
"""

from __future__ import annotations

import os
import struct
import uuid

_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0
_IOCTL_DISK_GET_DRIVE_LAYOUT_EX = 0x00070050
_IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000

# Laufwerksarten aus GetDriveTypeW
WECHSEL, FEST, NETZ, OPTISCH, RAMDISK = 2, 3, 4, 5, 6
ART_TEXT = {WECHSEL: "Wechseldatenträger", FEST: "Festplatte", NETZ: "Netzlaufwerk",
            OPTISCH: "optisches Laufwerk / eingebundenes Abbild", RAMDISK: "RAM-Disk"}
LOKAL = (WECHSEL, FEST, OPTISCH, RAMDISK)

_BUS = {1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "IEEE 1394", 5: "SSA", 6: "Fibre Channel", 7: "USB",
        8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD-Karte", 13: "MMC", 14: "virtuell",
        15: "virtuelle Datei (VHD/ISO)", 16: "Speicherplätze", 17: "NVMe", 18: "SCM", 19: "UFS"}

_GPT_TYPEN = {
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "EFI-Systempartition",
    "e3c9e316-0b5c-4db8-817d-f92df00215ae": "Microsoft reserviert (MSR)",
    "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7": "Basisdaten",
    "de94bba4-06d1-4d40-a16a-bfd50179d6ac": "Windows-Wiederherstellung",
    "5808c8aa-7e8f-42e0-85d2-e1e90434cfb3": "LDM-Metadaten (dynamischer Datenträger)",
    "af9b60a0-1431-4f62-bc68-3311714a69ad": "LDM-Daten (dynamischer Datenträger)",
    "e75caf8f-f680-4cee-afa3-b001e56efc2d": "Speicherplätze",
    "0fc63daf-8483-4772-8e79-3d69d8477de4": "Linux-Daten",
    "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f": "Linux-Swap",
    "e6d6d379-f507-44c2-a23c-238f2a3df928": "Linux LVM",
    "21686148-6449-6e6f-744e-656564454649": "BIOS-Boot",
}
_MBR_TYPEN = {0x01: "FAT12", 0x04: "FAT16", 0x05: "erweitert", 0x06: "FAT16", 0x07: "NTFS/exFAT",
              0x0B: "FAT32", 0x0C: "FAT32", 0x0E: "FAT16", 0x0F: "erweitert", 0x11: "versteckt FAT12",
              0x17: "versteckt NTFS", 0x1B: "versteckt FAT32", 0x1C: "versteckt FAT32",
              0x27: "Windows-Wiederherstellung", 0x42: "dynamischer Datenträger", 0x82: "Linux-Swap",
              0x83: "Linux-Daten", 0x8E: "Linux LVM", 0xEE: "GPT-Schutz", 0xEF: "EFI-Systempartition"}


def _k32():
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = w.HANDLE
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    k.DeviceIoControl.restype = w.BOOL
    k.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD,
                                  ctypes.POINTER(w.DWORD), w.LPVOID]
    k.CloseHandle.argtypes = [w.HANDLE]
    k.FindFirstVolumeW.restype = ctypes.c_void_p
    k.FindFirstVolumeW.argtypes = [w.LPWSTR, w.DWORD]
    k.FindNextVolumeW.argtypes = [ctypes.c_void_p, w.LPWSTR, w.DWORD]
    k.FindVolumeClose.argtypes = [ctypes.c_void_p]
    k.GetVolumePathNamesForVolumeNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD)]
    k.GetDriveTypeW.argtypes = [w.LPCWSTR]
    k.GetVolumeInformationW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD),
                                        ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
    k.GetDiskFreeSpaceExW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_ulonglong),
                                      ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)]
    return k


def _oeffnen(k, geraet: str):
    """Gerät nur zum Abfragen öffnen (Zugriff 0) – None, wenn es das nicht gibt."""
    import ctypes
    h = k.CreateFileW(geraet, 0, 0x1 | 0x2, None, 3, 0, None)       # FILE_SHARE_READ|WRITE, OPEN_EXISTING
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    return h


def _ioctl(k, h, code: int, eingabe: bytes = b"", groesse: int = 4096) -> bytes | None:
    import ctypes
    from ctypes import wintypes as w
    for _ in range(6):
        aus = ctypes.create_string_buffer(groesse)
        ein = ctypes.create_string_buffer(eingabe, len(eingabe)) if eingabe else None
        n = w.DWORD()
        if k.DeviceIoControl(h, code, ein, len(eingabe), aus, groesse, ctypes.byref(n), None):
            return aus.raw[:n.value]
        if ctypes.get_last_error() not in (122, 234):            # zu kleiner Puffer → größer
            return None
        groesse *= 4
    return None


def _text(roh: bytes, versatz: int) -> str:
    if not versatz or versatz >= len(roh):
        return ""
    ende = roh.find(b"\0", versatz)
    return roh[versatz:ende if ende >= 0 else None].decode("ascii", "replace").strip()


def _platte(k, nummer: int) -> dict | None:
    h = _oeffnen(k, rf"\\.\PhysicalDrive{nummer}")
    if h is None:
        return None
    try:
        p = {"nummer": nummer, "modell": "", "seriennummer": "", "anschluss": "", "ssd": None,
             "wechselbar": False, "groesse": 0, "stil": "", "partitionen": []}
        roh = _ioctl(k, h, _IOCTL_STORAGE_QUERY_PROPERTY, struct.pack("<II4x", 0, 0), 1024)
        if roh and len(roh) >= 36:
            (_, _, _, _, wechsel, _, hersteller, produkt, _, serie, bus, _) = struct.unpack_from(
                "<IIBBBBIIIIII", roh)
            p["modell"] = " ".join(x for x in (_text(roh, hersteller), _text(roh, produkt)) if x)
            p["seriennummer"] = _text(roh, serie)
            p["anschluss"] = _BUS.get(bus, f"Bus {bus}")
            p["wechselbar"] = bool(wechsel)
        roh = _ioctl(k, h, _IOCTL_STORAGE_QUERY_PROPERTY, struct.pack("<II4x", 7, 0), 64)
        if roh and len(roh) >= 9:
            p["ssd"] = not bool(roh[8])                              # IncursSeekPenalty
        roh = _ioctl(k, h, _IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, b"", 256)
        if roh and len(roh) >= 32:
            p["groesse"] = struct.unpack_from("<q", roh, 24)[0]
        roh = _ioctl(k, h, _IOCTL_DISK_GET_DRIVE_LAYOUT_EX, b"", 48 + 144 * 32)
        if roh and len(roh) >= 48:
            stil, anzahl = struct.unpack_from("<II", roh)
            p["stil"] = {0: "MBR", 1: "GPT", 2: "ohne Partitionstabelle"}.get(stil, "?")
            for i in range(anzahl):
                o = 48 + 144 * i
                if o + 144 > len(roh):
                    break
                start, laenge, nr = struct.unpack_from("<qqI", roh, o + 8)
                if stil == 1:
                    typ = str(uuid.UUID(bytes_le=roh[o + 32:o + 48]))
                    name = roh[o + 72:o + 144].decode("utf-16-le", "replace").split("\0")[0]
                    art = _GPT_TYPEN.get(typ, f"Typ {typ}")
                elif stil == 0:
                    typ_nr = roh[o + 32]
                    if typ_nr == 0 or laenge == 0:
                        continue
                    name, art = "", _MBR_TYPEN.get(typ_nr, f"Typ 0x{typ_nr:02X}")
                    if roh[o + 33]:
                        art += ", aktiv"
                else:
                    continue
                p["partitionen"].append({"nummer": nr, "start": start, "groesse": laenge,
                                         "art": art, "name": name})
        return p
    finally:
        k.CloseHandle(h)


def _platten(k) -> list[dict]:
    raus, fehlend = [], 0
    for n in range(64):
        p = _platte(k, n)
        if p is None:
            fehlend += 1
            if fehlend >= 8:
                break
            continue
        fehlend = 0
        raus.append(p)
    return raus


def volume_liste() -> list[dict]:
    """Alle Volumes: GUID-Pfad, Einhängepunkte, Wurzel für den Scan, Art."""
    if os.name != "nt":
        return [{"guid": "/", "pfade": ["/"], "wurzel": "/", "art": FEST}]
    import ctypes
    from ctypes import wintypes as w
    k = _k32()
    raus = []
    puffer = ctypes.create_unicode_buffer(260)
    h = k.FindFirstVolumeW(puffer, 260)
    if not h or h == ctypes.c_void_p(-1).value:
        return raus
    try:
        while True:
            guid = puffer.value
            namen = ctypes.create_unicode_buffer(2048)
            laenge = w.DWORD()
            pfade = []
            if k.GetVolumePathNamesForVolumeNameW(guid, namen, 2048, ctypes.byref(laenge)):
                pfade = [x for x in namen[:laenge.value].split("\0") if x]
            raus.append({"guid": guid, "pfade": pfade,
                         "wurzel": sorted(pfade, key=len)[0] if pfade else guid,
                         "art": k.GetDriveTypeW(guid)})
            if not k.FindNextVolumeW(h, puffer, 260):
                break
    finally:
        k.FindVolumeClose(h)
    return raus


def _volume_details(k, v: dict) -> dict:
    import ctypes
    from ctypes import wintypes as w
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serie = w.DWORD()
    v = dict(v, name="", dateisystem="", seriennummer="", groesse=0, frei=0, lesbar=False, grund="",
             extents=[])
    if k.GetVolumeInformationW(v["guid"], name, 261, ctypes.byref(serie), None, None, fs, 261):
        v.update(name=name.value, dateisystem=fs.value, seriennummer=f"{serie.value:08X}")
    else:
        fehler = ctypes.get_last_error()
        v["grund"] = {21: "kein Medium", 5: "von Windows gesperrt (Systembereich)",
                      1005: "kein lesbares Dateisystem (leer, fremdes Format oder verschlüsselt)"
                      }.get(fehler, f"nicht lesbar (Fehler {fehler})")
    gesamt, frei = ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if k.GetDiskFreeSpaceExW(v["guid"], None, ctypes.byref(gesamt), ctypes.byref(frei)):
        v.update(groesse=gesamt.value, frei=frei.value)
    if v["dateisystem"]:
        try:
            os.listdir(v["wurzel"])
            v["lesbar"] = True
        except OSError:
            v["grund"] = "von Windows gesperrt (Systembereich)"
    h = _oeffnen(k, v["guid"].rstrip("\\"))
    if h is not None:
        try:
            roh = _ioctl(k, h, _IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, b"", 8 + 24 * 8)
            if roh and len(roh) >= 8:
                anzahl = struct.unpack_from("<I", roh)[0]
                for i in range(anzahl):
                    o = 8 + 24 * i
                    if o + 24 <= len(roh):
                        platte, start, laenge = struct.unpack_from("<I4xqq", roh, o)
                        v["extents"].append({"platte": platte, "start": start, "groesse": laenge})
        finally:
            k.CloseHandle(h)
    return v


def uebersicht() -> dict:
    """{"platten": [...], "volumes": [...]} – jede Partition mit `volume` (Wurzel) oder leer."""
    if os.name != "nt":
        return {"platten": [], "volumes": []}
    k = _k32()
    volumes = [_volume_details(k, v) for v in volume_liste()]
    platten = _platten(k)
    for p in platten:
        for t in p["partitionen"]:
            t["volume"] = ""
            for v in volumes:
                if any(e["platte"] == p["nummer"] and e["start"] == t["start"] for e in v["extents"]):
                    t["volume"] = v["wurzel"]
    return {"platten": platten, "volumes": volumes}


def groesse_text(n: float) -> str:
    for einheit, teiler in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= teiler:
            wert = n / teiler
            return f"{wert:.1f} {einheit}".replace(".", ",") if wert < 10 else f"{wert:.0f} {einheit}"
    return f"{int(n)} B"
