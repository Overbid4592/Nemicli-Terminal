"""Herkunft: Wo kommt eine ausführbare Datei her – Ort, Signatur, Tarnung?

Ein Prozessname ist das Einzige an einem Prozess, das frei wählbar ist. Ein
Urteil, das sich auf den Namen stützt („heißt wie ein Windows-Programm, also
ist es eins“), ist deshalb wertlos: dieselbe Datei unter %TEMP% erzeugt ein
identisches Ereignis. Belegbar sind nur Pfad und Signatur, und beide müssen im
Alarm stehen, bevor jemand danach fragt – eine Prüfung, die erst auf Zuruf
passiert, unterbleibt irgendwann.

Zwei Kosten-Klassen, deshalb zwei Wege:

* `maskerade()` und `ist_systemort()` kosten nichts – reine Pfadprüfung. Die
  laufen bei jedem Prozessstart über die Regel R014.
* `signatur()` prüft Authenticode und läuft nur, wenn ein Alarm tatsächlich
  vorgelegt wird. Das Ergebnis wird über (Pfad, Änderungszeit, Größe) gecacht –
  ändert sich die Datei, wird neu geprüft.

Signatur und Ereignisprotokolle werden im eigenen Prozess über die Windows-
Bibliotheken gelesen (wintrust.dll, wevtapi.dll) – ohne Hilfsprozesse.
"""

from __future__ import annotations

import os
import threading
import time

_WINDOWS = os.environ.get("SystemRoot") or r"C:\Windows"
_PROGRAMME = os.environ.get("ProgramFiles") or r"C:\Program Files"
_PROGRAMME_X86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"

# Orte, an denen nur mit Administrator- oder Systemrechten geschrieben werden kann.
# Ein Systemprogramm, das hier liegt, ist an der richtigen Stelle.
SYSTEM_ORTE = tuple(os.path.normcase(p) for p in (
    os.path.join(_WINDOWS, "System32"),
    os.path.join(_WINDOWS, "SysWOW64"),
    os.path.join(_WINDOWS, "SystemApps"),
    os.path.join(_WINDOWS, "WinSxS"),
    os.path.join(_WINDOWS, "servicing"),
    os.path.join(_WINDOWS, "UUS"),
    os.path.join(_WINDOWS, "ImmersiveControlPanel"),
    os.path.join(_PROGRAMME, "WindowsApps"),
))

# … mit Ausnahme der Ablageorte INNERHALB des Windows-Ordners: dort packen auch
# Installer aus (z. B. `C:\Windows\SystemTemp\…`). Solche Ordner sind kein
# Beleg für Herkunft und zählen hier bewusst nicht als geschützt.
KEINE_SYSTEM_ORTE = tuple(os.path.normcase(p) for p in (
    os.path.join(_WINDOWS, "Temp"),
    os.path.join(_WINDOWS, "SystemTemp"),
    os.path.join(_WINDOWS, "Tasks"),
    os.path.join(_WINDOWS, "Debug"),
))

# Namen, die nur aus einem Systemordner kommen dürfen. `updater.exe`,
# `python.exe` & Co. gehören NICHT hierher – die liegen legitim überall.
SYSTEM_BINAERE = frozenset((
    "svchost.exe", "lsass.exe", "services.exe", "csrss.exe", "smss.exe", "wininit.exe",
    "winlogon.exe", "spoolsv.exe", "explorer.exe", "taskhostw.exe", "dllhost.exe",
    "rundll32.exe", "regsvr32.exe", "conhost.exe", "sihost.exe", "ctfmon.exe",
    "runtimebroker.exe", "searchindexer.exe", "searchprotocolhost.exe", "wuauclt.exe",
    "msiexec.exe", "wmiprvse.exe", "audiodg.exe", "fontdrvhost.exe", "dwm.exe",
    "textinputhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "backgroundtaskhost.exe", "smartscreen.exe", "sppsvc.exe", "wermgr.exe",
    "consent.exe", "dashost.exe", "lsm.exe", "userinit.exe",
))


def ist_systemort(exe: str) -> bool:
    """Liegt die Datei an einem Ort, an den nur System/Administrator schreiben darf?"""
    if not exe:
        return False
    p = os.path.normcase(os.path.abspath(exe))
    if any(p.startswith(x + os.sep) for x in KEINE_SYSTEM_ORTE):
        return False
    return any(p.startswith(x + os.sep) for x in SYSTEM_ORTE)


def maskerade(prozess: str, exe: str) -> str:
    """Trägt ein Programm den Namen eines Systemprogramms, liegt aber woanders?

    Gibt den Grund als Satz zurück, sonst "". Kostet nichts – nur Pfadvergleich.
    Leerer Pfad heißt „nicht lesbar“ (ohne Adminrechte häufig) und nie „verdächtig“:
    lieber nichts sagen als falsch anschlagen.
    """
    name = (prozess or "").strip().lower()
    if not name or not exe or name not in SYSTEM_BINAERE:
        return ""
    if ist_systemort(exe):
        return ""
    return (f"{prozess} ist ein Windows-Systemprogramm, läuft hier aber aus {exe}. "
            "Systemprogramme kommen ausschließlich aus System32, SysWOW64, SystemApps "
            "oder WinSxS – derselbe Name an einem anderen Ort ist ein Tarnmuster.")


# ---------------------------------------------------------------------------
# Signatur (wintrust.dll)
# ---------------------------------------------------------------------------

_cache: dict[tuple, dict] = {}
_cache_lock = threading.Lock()
_CACHE_GRENZE = 400

# WinVerifyTrust-Ergebnis -> die bisherigen Statusnamen (Valid, NotSigned …)
_STATUS = {
    0x00000000: "Valid",
    0x800B0100: "NotSigned",                 # TRUST_E_NOSIGNATURE
    0x80096010: "HashMismatch",              # TRUST_E_BAD_DIGEST
    0x800B0003: "NotSupportedFileFormat",    # TRUST_E_SUBJECT_FORM_UNKNOWN
    0x800B0004: "NotTrusted",                # TRUST_E_SUBJECT_NOT_TRUSTED
    0x800B0109: "NotTrusted",                # CERT_E_UNTRUSTEDROOT
    0x800B010A: "NotTrusted",                # CERT_E_CHAINING
    0x800B0111: "NotTrusted",                # TRUST_E_EXPLICIT_DISTRUST
    0x800B0101: "NotTrusted",                # CERT_E_EXPIRED
    0x800B010C: "NotTrusted",                # CERT_E_REVOKED
}
_KEINE_SIGNATUR = (0x800B0100, 0x800B0003)   # dann im Windows-Katalog nachsehen


def _kennung(pfad: str) -> tuple | None:
    """(Pfad, Änderungszeit, Größe) – tauscht jemand die Datei aus, prüfen wir neu."""
    try:
        s = os.stat(pfad)
    except OSError:
        return None
    return (os.path.normcase(os.path.abspath(pfad)), int(s.st_mtime), s.st_size)


def signatur(exe: str, timeout: float = 12.0) -> dict:
    """Authenticode-Signatur einer Datei: {status, signierer, gueltig, gecacht}.

    status: Valid, NotSigned, HashMismatch, NotTrusted, NotSupportedFileFormat,
    UnknownError. `fehlt` heißt: Datei nicht (mehr) da, `unbekannt`: nicht
    prüfbar (kein Windows, Bibliothek nicht ladbar).
    """
    if not exe:
        return {"status": "unbekannt", "signierer": "", "gueltig": False, "gecacht": False}
    kennung = _kennung(exe)
    if kennung is None:
        return {"status": "fehlt", "signierer": "", "gueltig": False, "gecacht": False}
    with _cache_lock:
        treffer = _cache.get(kennung)
    if treffer is not None:
        return dict(treffer, gecacht=True)

    ergebnis = _pruefen(exe, timeout)
    with _cache_lock:
        if len(_cache) > _CACHE_GRENZE:
            _cache.clear()
        _cache[kennung] = ergebnis
    return dict(ergebnis, gecacht=False)


_WT: dict = {}


def _wintrust():
    """ctypes-Anbindung an wintrust.dll/crypt32.dll – einmal aufgebaut, dann gemerkt."""
    if _WT:
        return _WT
    import ctypes
    from ctypes import wintypes as w

    class GUID(ctypes.Structure):
        _fields_ = [("d1", w.DWORD), ("d2", w.WORD), ("d3", w.WORD), ("d4", ctypes.c_ubyte * 8)]

    class DateiInfo(ctypes.Structure):          # WINTRUST_FILE_INFO
        _fields_ = [("cbStruct", w.DWORD), ("pcwszFilePath", w.LPCWSTR),
                    ("hFile", w.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

    class KatalogInfo(ctypes.Structure):        # WINTRUST_CATALOG_INFO
        _fields_ = [("cbStruct", w.DWORD), ("dwCatalogVersion", w.DWORD),
                    ("pcwszCatalogFilePath", w.LPCWSTR), ("pcwszMemberTag", w.LPCWSTR),
                    ("pcwszMemberFilePath", w.LPCWSTR), ("hMemberFile", w.HANDLE),
                    ("pbCalculatedFileHash", ctypes.c_void_p), ("cbCalculatedFileHash", w.DWORD),
                    ("pcCatalogContext", ctypes.c_void_p), ("hCatAdmin", w.HANDLE)]

    class TrustDaten(ctypes.Structure):         # WINTRUST_DATA
        _fields_ = [("cbStruct", w.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", w.DWORD),
                    ("fdwRevocationChecks", w.DWORD), ("dwUnionChoice", w.DWORD),
                    ("pInfo", ctypes.c_void_p), ("dwStateAction", w.DWORD),
                    ("hWVTStateData", w.HANDLE), ("pwszURLReference", w.LPWSTR),
                    ("dwProvFlags", w.DWORD), ("dwUIContext", w.DWORD),
                    ("pSignatureSettings", ctypes.c_void_p)]

    class KatalogDatei(ctypes.Structure):       # CATALOG_INFO
        _fields_ = [("cbStruct", w.DWORD), ("wszCatalogFile", w.WCHAR * 260)]

    wt = ctypes.WinDLL("wintrust")
    c32 = ctypes.WinDLL("crypt32")
    wt.WinVerifyTrust.restype = w.LONG
    wt.WinVerifyTrust.argtypes = [w.HWND, ctypes.POINTER(GUID), ctypes.c_void_p]
    wt.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wt.WTHelperProvDataFromStateData.argtypes = [w.HANDLE]
    wt.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
    wt.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, w.DWORD, w.BOOL, w.DWORD]
    wt.CryptCATAdminAcquireContext2.restype = w.BOOL
    wt.CryptCATAdminAcquireContext2.argtypes = [ctypes.POINTER(w.HANDLE), ctypes.c_void_p,
                                                w.LPCWSTR, ctypes.c_void_p, w.DWORD]
    wt.CryptCATAdminCalcHashFromFileHandle2.restype = w.BOOL
    wt.CryptCATAdminCalcHashFromFileHandle2.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.DWORD),
                                                        ctypes.c_void_p, w.DWORD]
    wt.CryptCATAdminEnumCatalogFromHash.restype = w.HANDLE
    wt.CryptCATAdminEnumCatalogFromHash.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, w.DWORD,
                                                    ctypes.c_void_p]
    wt.CryptCATCatalogInfoFromContext.restype = w.BOOL
    wt.CryptCATCatalogInfoFromContext.argtypes = [w.HANDLE, ctypes.POINTER(KatalogDatei), w.DWORD]
    wt.CryptCATAdminReleaseCatalogContext.argtypes = [w.HANDLE, w.HANDLE, w.DWORD]
    wt.CryptCATAdminReleaseContext.argtypes = [w.HANDLE, w.DWORD]
    c32.CertGetNameStringW.restype = w.DWORD
    c32.CertGetNameStringW.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p,
                                       w.LPWSTR, w.DWORD]
    aktion = GUID(0x00AAC56B, 0xCD44, 0x11D0,
                  (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    _WT.update(ct=ctypes, w=w, wt=wt, c32=c32, aktion=aktion, DateiInfo=DateiInfo,
               KatalogInfo=KatalogInfo, TrustDaten=TrustDaten, KatalogDatei=KatalogDatei)
    return _WT


def _signierer(t: dict, zustand) -> str:
    """Anzeigename des Signierers aus dem Prüfzustand (CN des ersten Zertifikats)."""
    ct = t["ct"]
    daten = t["wt"].WTHelperProvDataFromStateData(zustand)
    if not daten:
        return ""
    sgnr = t["wt"].WTHelperGetProvSignerFromChain(daten, 0, False, 0)
    if not sgnr:
        return ""
    # CRYPT_PROVIDER_SGNR: DWORD cbStruct, FILETIME, DWORD csCertChain (@12), Zeiger pasCertChain (@16)
    anzahl = ct.c_uint32.from_address(sgnr + 12).value
    kette = ct.c_void_p.from_address(sgnr + 16).value
    if not anzahl or not kette:
        return ""
    zert = ct.c_void_p.from_address(kette + 8).value      # CRYPT_PROVIDER_CERT.pCert
    if not zert:
        return ""
    puffer = ct.create_unicode_buffer(256)
    t["c32"].CertGetNameStringW(zert, 4, 0, None, puffer, 256)   # CERT_NAME_SIMPLE_DISPLAY_TYPE
    return puffer.value.strip()


def _vertrauen(t: dict, info, art: int) -> tuple[int, str]:
    """WinVerifyTrust ohne Oberfläche und ohne Netzabruf -> (Ergebnis, Signierer)."""
    ct = t["ct"]
    d = t["TrustDaten"]()
    d.cbStruct = ct.sizeof(d)
    d.dwUIChoice = 2                       # WTD_UI_NONE
    d.fdwRevocationChecks = 0              # WTD_REVOKE_NONE
    d.dwUnionChoice = art                  # 1 = Datei, 2 = Katalog
    d.pInfo = ct.cast(ct.pointer(info), ct.c_void_p)
    d.dwStateAction = 1                    # WTD_STATEACTION_VERIFY
    d.dwProvFlags = 0x10 | 0x1000          # keine Sperrlisten, nur Cache – nichts aus dem Netz
    ergebnis = t["wt"].WinVerifyTrust(None, ct.byref(t["aktion"]), ct.byref(d)) & 0xFFFFFFFF
    name = ""
    try:
        if d.hWVTStateData:
            name = _signierer(t, d.hWVTStateData)
    finally:
        d.dwStateAction = 2                # WTD_STATEACTION_CLOSE
        t["wt"].WinVerifyTrust(None, ct.byref(t["aktion"]), ct.byref(d))
    return ergebnis, name


def _katalog(t: dict, pfad: str) -> tuple[int, str] | None:
    """Windows-Dateien sind oft nicht selbst signiert, sondern über einen Katalog."""
    import msvcrt
    ct, w, wt = t["ct"], t["w"], t["wt"]
    for algo in ("SHA256", None):          # None = SHA1-Kataloge älterer Pakete
        admin = w.HANDLE()
        if not wt.CryptCATAdminAcquireContext2(ct.byref(admin), None, algo, None, 0):
            continue
        try:
            with open(pfad, "rb") as f:
                h = w.HANDLE(msvcrt.get_osfhandle(f.fileno()))
                n = w.DWORD(0)
                wt.CryptCATAdminCalcHashFromFileHandle2(admin, h, ct.byref(n), None, 0)
                if not n.value:
                    continue
                hash_ = (ct.c_ubyte * n.value)()
                if not wt.CryptCATAdminCalcHashFromFileHandle2(admin, h, ct.byref(n), hash_, 0):
                    continue
            kat = wt.CryptCATAdminEnumCatalogFromHash(admin, hash_, n.value, 0, None)
            if not kat:
                continue
            try:
                ki = t["KatalogDatei"]()
                ki.cbStruct = ct.sizeof(ki)
                if not wt.CryptCATCatalogInfoFromContext(kat, ct.byref(ki), 0):
                    continue
                info = t["KatalogInfo"]()
                info.cbStruct = ct.sizeof(info)
                info.pcwszCatalogFilePath = ki.wszCatalogFile
                info.pcwszMemberTag = bytes(hash_).hex().upper()
                info.pcwszMemberFilePath = pfad
                info.pbCalculatedFileHash = ct.cast(hash_, ct.c_void_p)
                info.cbCalculatedFileHash = n.value
                info.hCatAdmin = admin
                return _vertrauen(t, info, 2)
            finally:
                wt.CryptCATAdminReleaseCatalogContext(admin, kat, 0)
        except OSError:
            return None
        finally:
            wt.CryptCATAdminReleaseContext(admin, 0)
    return None


def _pruefen(exe: str, timeout: float) -> dict:
    """Eingebettete Signatur prüfen, sonst den Windows-Katalog. `timeout` bleibt
    für die Aufrufform erhalten – geprüft wird lokal und ohne Netz."""
    if os.name != "nt":
        return {"status": "unbekannt", "signierer": "", "gueltig": False}
    try:
        t = _wintrust()
        info = t["DateiInfo"]()
        info.cbStruct = t["ct"].sizeof(info)
        info.pcwszFilePath = exe
        ergebnis, name = _vertrauen(t, info, 1)
        if ergebnis in _KEINE_SIGNATUR:
            aus_katalog = _katalog(t, exe)
            if aus_katalog is not None:
                ergebnis, name = aus_katalog
    except Exception:
        return {"status": "unbekannt", "signierer": "", "gueltig": False}
    status = _STATUS.get(ergebnis, "UnknownError")
    return {"status": status, "signierer": name, "gueltig": status == "Valid"}


def signatur_zeile(exe: str) -> str:
    """Eine Zeile für den Alarm – das, was die Persönlichkeit lesen soll."""
    if not exe:
        return ""
    s = signatur(exe)
    ort = "geschützter Systemordner" if ist_systemort(exe) else "kein Systemordner"
    if s["gueltig"]:
        return f"signiert: {s['signierer'] or 'gültig, Aussteller unbekannt'} · {ort}"
    if s["status"] == "NotSigned":
        return f"NICHT signiert · {ort}"
    if s["status"] == "fehlt":
        return "Datei nicht mehr vorhanden (Prozess bereits beendet?)"
    if s["status"] == "unbekannt":
        return f"Signatur nicht prüfbar · {ort}"
    return f"Signatur {s['status']} · {ort}"


# ---------------------------------------------------------------------------
# Ereignisprotokolle (wevtapi.dll)
# ---------------------------------------------------------------------------

def _evt_xml(kanal: str, xpath: str, grenze: int = 60) -> list[str]:
    """Ereignisse als XML, neueste zuerst – über wevtapi.dll im eigenen Prozess."""
    import ctypes
    from ctypes import wintypes as w
    try:
        api = ctypes.WinDLL("wevtapi", use_last_error=True)
    except (OSError, AttributeError):
        return []
    api.EvtQuery.restype = ctypes.c_void_p
    api.EvtQuery.argtypes = [ctypes.c_void_p, w.LPCWSTR, w.LPCWSTR, w.DWORD]
    api.EvtNext.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p),
                            w.DWORD, w.DWORD, ctypes.POINTER(w.DWORD)]
    api.EvtRender.argtypes = [ctypes.c_void_p, ctypes.c_void_p, w.DWORD, w.DWORD,
                              ctypes.c_void_p, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD)]
    api.EvtClose.argtypes = [ctypes.c_void_p]
    kanal_pfad, rueckwaerts, als_xml = 0x1, 0x200, 1
    h = api.EvtQuery(None, kanal, xpath, kanal_pfad | rueckwaerts)
    if not h:
        return []
    raus: list[str] = []
    try:
        feld = (ctypes.c_void_p * 16)()
        n = w.DWORD()
        while len(raus) < grenze and api.EvtNext(h, 16, feld, 1000, 0, ctypes.byref(n)):
            for i in range(n.value):
                try:
                    noetig, anzahl = w.DWORD(), w.DWORD()
                    api.EvtRender(None, feld[i], als_xml, 0, None,
                                  ctypes.byref(noetig), ctypes.byref(anzahl))
                    puffer = ctypes.create_unicode_buffer(noetig.value // 2 + 1)
                    if api.EvtRender(None, feld[i], als_xml, noetig.value, puffer,
                                     ctypes.byref(noetig), ctypes.byref(anzahl)):
                        raus.append(puffer.value)
                finally:
                    api.EvtClose(feld[i])
    finally:
        api.EvtClose(h)
    return raus[:grenze]


def _zeit_lokal(system_time: str) -> tuple[float, str]:
    """'2026-09-23T09:17:32.18Z' (UTC) -> (Unix-Zeit, 'dd.mm. HH:MM:SS' Ortszeit)."""
    import calendar
    try:
        t = calendar.timegm(time.strptime(system_time[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0, "?"
    return float(t), time.strftime("%d.%m. %H:%M:%S", time.localtime(t))


def _zeitraum(von: float, bis: float) -> str:
    a = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(von))
    b = time.strftime("%Y-%m-%dT%H:%M:%S.999Z", time.gmtime(bis))
    return f"TimeCreated[@SystemTime>='{a}' and @SystemTime<='{b}']"


def _zusammenfassen(zeilen: list[str]) -> list[str]:
    """Gleiche Zeilen hintereinander (Windows protokolliert Teilpakete einzeln) → „(7×)“."""
    raus: list[list] = []
    for z in zeilen:
        if raus and raus[-1][0] == z:
            raus[-1][1] += 1
        else:
            raus.append([z, 1])
    return [z if n == 1 else f"{z} ({n}×)" for z, n in raus]


def _daten(wurzel) -> dict[str, str]:
    """<EventData><Data Name='…'>…</Data> als Wörterbuch."""
    raus = {}
    for el in wurzel.iter():
        if el.tag.rsplit("}", 1)[-1] == "Data" and el.get("Name"):
            raus[el.get("Name")] = (el.text or "").strip()
    return raus


# --- Was tat die Paketverwaltung gerade? (AppX-Bereitstellungsprotokoll) ---

_APPX_KANAL = "Microsoft-Windows-AppXDeploymentServer/Operational"
_APPX_IDS = {400: "Bereitstellung gestartet", 404: "Bereitstellung FEHLGESCHLAGEN",
             603: "Vorgang gestartet", 613: "Vorgang gestartet"}


def appx_zeilen(xml_liste: list[str]) -> list[tuple[float, str]]:
    """Protokoll-XML -> [(Zeit, 'HH:MM:SS | Aktion: Paket (von Aufrufer)')], älteste zuerst."""
    import xml.etree.ElementTree as ET
    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
    raus: list[tuple[float, str]] = []
    for x in xml_liste:
        try:
            wurzel = ET.fromstring(x)
            eid = int(wurzel.findtext("e:System/e:EventID", "0", ns))
        except (ET.ParseError, ValueError):
            continue
        if eid not in _APPX_IDS:
            continue
        zk = wurzel.find("e:System/e:TimeCreated", ns)
        t, wann = _zeit_lokal(zk.get("SystemTime", "") if zk is not None else "")
        d = _daten(wurzel)
        paket = d.get("PackageFullName") or d.get("Path") or ""
        if not paket or paket.upper() == "NULL":
            continue
        aufrufer = d.get("CallingProcess", "")
        zeile = f"{wann[-8:]} | {_APPX_IDS[eid]}: {paket[:160]}"
        if aufrufer:
            zeile += f" (von {aufrufer[:60]})"
        raus.append((t, zeile))
    raus.sort()
    return raus


def appx_kontext(zeit: float, fenster: float = 60.0, timeout: float = 20.0) -> list[str]:
    """Store-/AppX-Pakete, die um `zeit` herum registriert oder entfernt wurden.

    Eine Store-Installation zieht Folgeschritte nach sich – `rundll32 … ShellRefresh`,
    ein Neustart von StartMenuExperienceHost –, die als hohe ML-Anomalien auffallen,
    weil die Eltern-Kind-Beziehungen neu sind. Ohne diesen Kontext bleibt nur Raten;
    mit ihm steht das auslösende Paket namentlich am Alarm.
    """
    if not zeit or os.name != "nt":
        return []
    ids = " or ".join(f"EventID={i}" for i in _APPX_IDS)
    try:
        xml_liste = _evt_xml(_APPX_KANAL,
                             f"*[System[({ids}) and {_zeitraum(zeit - fenster, zeit + fenster)}]]")
        zeilen = _zusammenfassen([z for _, z in appx_zeilen(xml_liste)])
    except Exception:
        return []
    return [z[:300] for z in zeilen][:8]


# --- Was hat Windows Update installiert? ---
#
# Ein kumulatives Update tauscht hunderte Systemdateien, startet TiWorker,
# TrustedInstaller und danach ngen/mscorsvw in neuen Eltern-Kind-Kombinationen.
# „Nach einem Update" ist als Erklärung nur etwas wert, wenn das Update belegt
# ist. Gelesen werden nur zwei Protokolle mit festen Ereignis-IDs; das Ergebnis
# wird 10 Minuten zwischengespeichert.

_WU_ANBIETER = "Microsoft-Windows-WindowsUpdateClient"
_WU_IDS = {19: "installiert", 20: "FEHLGESCHLAGEN", 43: "Installation gestartet"}
_SETUP_IDS = {2: "installiert", 3: "FEHLGESCHLAGEN", 4: "installiert, Neustart nötig"}
_UPDATE_CACHE: dict[tuple, tuple[float, list[str]]] = {}
_UPDATE_CACHE_S = 600


def update_zeilen(xml_liste: list[str]) -> list[tuple[float, str]]:
    """Protokoll-XML -> [(Zeit, Zeile)]. Übernommen werden nur Zeit, Ereignis und
    Update-Name; Rechnername, Benutzer-SIDs und Prozess-IDs bleiben draußen."""
    import xml.etree.ElementTree as ET
    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
    raus: list[tuple[float, str]] = []
    for x in xml_liste:
        try:
            wurzel = ET.fromstring(x)
        except ET.ParseError:
            continue
        sysk = wurzel.find("e:System", ns)
        if sysk is None:
            continue
        anbieter = (sysk.find("e:Provider", ns).get("Name", "")
                    if sysk.find("e:Provider", ns) is not None else "")
        try:
            eid = int(sysk.findtext("e:EventID", "0", ns))
        except ValueError:
            continue
        zk = sysk.find("e:TimeCreated", ns)
        t, wann = _zeit_lokal(zk.get("SystemTime", "") if zk is not None else "")
        if anbieter == _WU_ANBIETER and eid in _WU_IDS:
            titel = _daten(wurzel).get("updateTitle", "")
            if titel:
                raus.append((t, f"{wann} | Windows Update {_WU_IDS[eid]}: {titel[:160]}"))
        elif anbieter == "Microsoft-Windows-Servicing" and eid in _SETUP_IDS:
            paket = fehler = ""
            for el in wurzel.iter():
                tag = el.tag.rsplit("}", 1)[-1]
                if tag == "PackageIdentifier":
                    paket = (el.text or "").strip()
                elif tag == "ErrorCode":
                    fehler = (el.text or "").strip()
            if not paket.upper().startswith("KB"):
                continue                 # Metadaten-Pakete (FodMetadata …) sagen nichts
            art = _SETUP_IDS[eid]
            if fehler and fehler.lower() not in ("0x0", "0"):
                art = f"FEHLGESCHLAGEN ({fehler})"
            raus.append((t, f"{wann} | Windows-Paket {paket}: {art}"))
    raus.sort()
    return raus


def update_kontext(zeit: float, vorher: float = 24 * 3600, nachher: float = 600) -> list[str]:
    """Windows-Updates im Fenster [zeit − vorher, zeit + nachher], älteste zuerst.

    Leere Liste heißt: im Fenster wurde laut Protokoll nichts installiert – dann
    ist „nach einem Update" als Erklärung nicht belegt.
    """
    if not zeit or os.name != "nt":
        return []
    schluessel = (int(zeit // _UPDATE_CACHE_S), int(vorher), int(nachher))
    jetzt = time.time()
    with _cache_lock:
        treffer = _UPDATE_CACHE.get(schluessel)
    if treffer is not None and jetzt - treffer[0] < _UPDATE_CACHE_S:
        return list(treffer[1])
    zeitraum = _zeitraum(zeit - vorher, zeit + nachher)
    wu = " or ".join(f"EventID={i}" for i in _WU_IDS)
    setup = " or ".join(f"EventID={i}" for i in _SETUP_IDS)
    try:
        xml_liste = (_evt_xml("System", f"*[System[Provider[@Name='{_WU_ANBIETER}'] "
                                        f"and ({wu}) and {zeitraum}]]")
                     + _evt_xml("Setup", f"*[System[({setup}) and {zeitraum}]]"))
        zeilen = _zusammenfassen([z for _, z in update_zeilen(xml_liste)])[-10:]
    except Exception:
        zeilen = []
    with _cache_lock:
        if len(_UPDATE_CACHE) > 50:
            _UPDATE_CACHE.clear()
        _UPDATE_CACHE[schluessel] = (jetzt, zeilen)
    return list(zeilen)
