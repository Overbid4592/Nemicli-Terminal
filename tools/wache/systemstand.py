"""Systemstand für den Vollscan (Stufe 3): Autostart, Dienste, geplante
Aufgaben, geladene Treiber und Netz – je Eintrag Programmpfad, Signatur und
Ampel.

Die Ampel ist eine Vorsortierung nach festen, im Bericht abgedruckten Regeln
(AMPEL_REGELN) – kein Urteil. Sie ordnet nur, was zuerst angesehen werden
sollte; bewertet wird wie bei Alarmen von der Persönlichkeit.

Alles nur lesend: Registry, psutil, Dienststeuerung. Einzige Ausnahme sind die
geplanten Aufgaben – deren Dateien sind ohne Adminrechte nicht lesbar, daher
`schtasks /query` (wie im Inventar).
"""

from __future__ import annotations

import csv
import io
import ipaddress
import os
import re
import subprocess

GRUEN, GELB, ROT = "grün", "gelb", "rot"

AMPEL_REGELN = [
    (ROT, "Programm fehlt: Eintrag zeigt auf eine Datei, die es nicht gibt"),
    (ROT, "Signatur passt nicht zum Inhalt (HashMismatch) oder nicht vertrauenswürdig (NotTrusted)"),
    (ROT, "unsigniert und in einem Nutzer-beschreibbaren Ordner (Benutzer, ProgramData, Temp)"),
    (ROT, "unsigniert und seit dem letzten Lauf neu"),
    (ROT, "hosts-Eintrag leitet auf eine öffentliche Adresse um"),
    (GELB, "unsigniert an anderem Ort"),
    (GELB, "Skript als Ziel (bat, cmd, vbs, js, ps1 …)"),
    (GELB, "seit dem letzten Lauf neu (signiert)"),
    (GELB, "Proxy eingeschaltet, DNS-Server von Hand gesetzt, hosts-Sperreintrag"),
    (GRUEN, "gültig signiert, Datei vorhanden, nicht neu"),
]

_SKRIPT = {".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".ps1", ".hta", ".py", ".pyw"}
# Programme, die eine andere Datei ausführen – maßgeblich ist dann diese.
_WIRTE = {"rundll32.exe", "regsvr32.exe", "wscript.exe", "cscript.exe", "mshta.exe", "cmd.exe"}
_ENDUNG = r"\.(?:exe|dll|sys|com|scr|cpl|ocx|bat|cmd|vbs|vbe|js|jse|wsf|hta|ps1|py|pyw|msi)"
_PFAD_RE = re.compile(r'"([^"]+?' + _ENDUNG + r')"|([A-Za-z]:\\[^"]*?' + _ENDUNG + r')(?=[\s,"]|$)',
                      re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pfade aus Befehlszeilen
# ---------------------------------------------------------------------------

def _normieren(pfad: str) -> str:
    pfad = os.path.expandvars(pfad.strip().strip('"'))
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    low = pfad.lower()
    if low.startswith("\\systemroot\\"):
        pfad = windir + pfad[11:]
    elif low.startswith("\\??\\"):
        pfad = pfad[4:]
    elif low.startswith("system32\\") or low.startswith("syswow64\\"):
        pfad = os.path.join(windir, pfad)
    if pfad and not os.path.isabs(pfad) and os.sep not in pfad:
        kandidat = os.path.join(windir, "System32", pfad)
        if os.path.exists(kandidat):
            pfad = kandidat
    return pfad


def programmpfad(befehl: str) -> str:
    """Die ausgeführte Datei aus einer Befehlszeile. Bei Wirtprogrammen
    (rundll32, wscript, cmd /c …) die Datei, die sie ausführen."""
    if not befehl:
        return ""
    befehl = os.path.expandvars(befehl)
    treffer = [m.group(1) or m.group(2) for m in _PFAD_RE.finditer(befehl)]
    if not treffer:
        erstes = befehl.strip().split()[0] if befehl.strip() else ""
        return _normieren(erstes) if erstes else ""
    erstes = _normieren(treffer[0])
    if os.path.basename(erstes).lower() in _WIRTE and len(treffer) > 1:
        return _normieren(treffer[1])
    return erstes


# ---------------------------------------------------------------------------
# Sammeln
# ---------------------------------------------------------------------------

def _eintrag(art: str, name: str, pfad: str = "", detail: str = "") -> dict:
    return {"art": art, "name": name, "pfad": pfad, "detail": detail}


def autostart() -> list[dict]:
    from .inventar import Inventar
    raus = []
    for e in Inventar(mit_aufgaben=False).autostart():
        pfad = programmpfad(e.detail) if e.extra.get("herkunft") == "registry" else e.detail
        raus.append(_eintrag("autostart", e.name, pfad, f"{e.extra.get('quelle', '')} · {e.detail}"[:300]))
    return raus


def _dienst_dll(name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SYSTEM\CurrentControlSet\Services\{name}\Parameters") as k:
            return _normieren(str(winreg.QueryValueEx(k, "ServiceDll")[0]))
    except OSError:
        return ""


def dienste() -> list[dict]:
    try:
        import psutil
    except ImportError:
        return []
    raus = []
    for s in getattr(psutil, "win_service_iter", lambda: [])():
        try:
            i = s.as_dict()
        except Exception:
            continue
        bin_ = i.get("binpath") or ""
        pfad = programmpfad(bin_)
        if os.path.basename(pfad).lower() == "svchost.exe":
            pfad = _dienst_dll(i.get("name", "")) or pfad
        raus.append(_eintrag("dienst", i.get("name", ""), pfad,
                             f"{i.get('start_type', '')} · {i.get('status', '')} · "
                             f"{i.get('username') or ''} · {bin_}"[:300]))
    return raus


def aufgaben(timeout: int = 60) -> list[dict]:
    """Aktivierte geplante Aufgaben mit ihrem Programm. Spalten nach Position
    (die Überschriften sind übersetzt): 1 Name, 8 Programm, 11 Zustand, 14 Konto."""
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/v", "/nh"], capture_output=True,
                           text=True, timeout=timeout, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return []
    raus, gesehen = [], set()
    for z in csv.reader(io.StringIO(r.stdout)):
        if len(z) < 15 or not z[1].startswith("\\"):
            continue
        name, programm, zustand, konto = z[1], z[8], z[11], z[14]
        if (name, programm) in gesehen:
            continue
        gesehen.add((name, programm))
        pfad = programmpfad(programm) if re.search(_ENDUNG, programm, re.IGNORECASE) else ""
        raus.append(_eintrag("aufgabe", name, pfad, f"{zustand} · {konto} · {programm}"[:300]))
    return raus


def _treiberpfad(name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\{name}") as k:
            return _normieren(str(winreg.QueryValueEx(k, "ImagePath")[0]))
    except OSError:
        return _normieren(rf"System32\drivers\{name}.sys")


def treiber() -> list[dict]:
    """Laufende Kernel- und Dateisystemtreiber laut Dienststeuerung. Die Liste
    der geladenen Module (EnumDeviceDrivers) liefert ohne Adminrechte keine
    Adressen mehr; die Dienststeuerung kennt die laufenden Treiber trotzdem."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes as w

    class Status(ctypes.Structure):
        _fields_ = [(n, w.DWORD) for n in ("typ", "zustand", "annahme", "exitcode", "dienst_exitcode",
                                           "pruefpunkt", "warten", "pid", "flags")]

    class Eintrag(ctypes.Structure):
        _fields_ = [("name", w.LPWSTR), ("anzeige", w.LPWSTR), ("status", Status)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenSCManagerW.restype = ctypes.c_void_p
    adv.OpenSCManagerW.argtypes = [w.LPCWSTR, w.LPCWSTR, w.DWORD]
    adv.EnumServicesStatusExW.argtypes = [ctypes.c_void_p, ctypes.c_int, w.DWORD, w.DWORD, ctypes.c_void_p,
                                          w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD),
                                          ctypes.POINTER(w.DWORD), w.LPCWSTR]
    adv.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    scm = adv.OpenSCManagerW(None, None, 0x0004)          # SC_MANAGER_ENUMERATE_SERVICE
    if not scm:
        return []
    raus = []
    try:
        noetig, anzahl, weiter = w.DWORD(), w.DWORD(), w.DWORD(0)
        # SC_ENUM_PROCESS_INFO, SERVICE_DRIVER (Kernel + Dateisystem), SERVICE_ACTIVE
        adv.EnumServicesStatusExW(scm, 0, 0x0B, 0x01, None, 0, ctypes.byref(noetig),
                                  ctypes.byref(anzahl), ctypes.byref(weiter), None)
        puffer = ctypes.create_string_buffer(noetig.value + 4096)
        weiter = w.DWORD(0)
        if not adv.EnumServicesStatusExW(scm, 0, 0x0B, 0x01, puffer, len(puffer), ctypes.byref(noetig),
                                         ctypes.byref(anzahl), ctypes.byref(weiter), None):
            return []
        liste = ctypes.cast(puffer, ctypes.POINTER(Eintrag))
        for i in range(anzahl.value):
            name = liste[i].name or ""
            pfad = _treiberpfad(name)
            raus.append(_eintrag("treiber", name, pfad, liste[i].anzeige or ""))
    finally:
        adv.CloseServiceHandle(scm)
    return raus


def _privat(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        return a.is_private or a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast
    except ValueError:
        return True


def netz() -> list[dict]:
    raus: list[dict] = []
    try:
        import psutil
        verbindungen = psutil.net_connections(kind="inet")
    except Exception:
        verbindungen = []
    exes: dict[int, tuple[str, str]] = {}

    def prozess(pid: int) -> tuple[str, str]:
        if pid not in exes:
            try:
                p = psutil.Process(pid)
                exes[pid] = (p.name(), p.exe() or "")
            except Exception:
                exes[pid] = ("?", "")
        return exes[pid]

    ziele: dict[str, set[str]] = {}
    for c in verbindungen:
        if not c.pid:
            continue
        name, exe = prozess(c.pid)
        if c.status == "LISTEN" and c.laddr:
            raus.append(_eintrag("port", f"{c.laddr.ip}:{c.laddr.port}", exe, name))
        elif c.status == "ESTABLISHED" and c.raddr and not _privat(c.raddr.ip):
            ziele.setdefault(exe or name, set()).add(f"{c.raddr.ip}:{c.raddr.port}")
    for exe, adressen in ziele.items():
        raus.append(_eintrag("verbindung", os.path.basename(exe), exe,
                             f"{len(adressen)} Ziele: " + ", ".join(sorted(adressen)[:8])))
    raus += _hosts() + _dns() + _proxy()
    return raus


def _hosts() -> list[dict]:
    pfad = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), r"System32\drivers\etc\hosts")
    raus = []
    try:
        with open(pfad, encoding="utf-8", errors="replace") as f:
            for zeile in f:
                zeile = zeile.split("#", 1)[0].strip()
                teile = zeile.split()
                if len(teile) >= 2:
                    for name in teile[1:]:
                        raus.append(_eintrag("hosts", name, "", teile[0]))
    except OSError:
        pass
    return raus


def _dns() -> list[dict]:
    try:
        import winreg
    except ImportError:
        return []
    raus = []
    basis = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, basis) as k:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, guid) as s:
                        wert = str(winreg.QueryValueEx(s, "NameServer")[0] or "")
                except OSError:
                    continue
                if wert.strip():
                    raus.append(_eintrag("dns", guid, "", wert.strip()))
    except OSError:
        pass
    return raus


def _proxy() -> list[dict]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            an = winreg.QueryValueEx(k, "ProxyEnable")[0]
            server = str(winreg.QueryValueEx(k, "ProxyServer")[0]) if an else ""
    except OSError:
        return []
    return [_eintrag("proxy", "Proxy", "", server)] if an else []


SAMMLER = [("Autostart", autostart), ("Dienste", dienste), ("Aufgaben", aufgaben),
           ("Treiber", treiber), ("Netz", netz)]


# ---------------------------------------------------------------------------
# Signatur und Ampel
# ---------------------------------------------------------------------------

def _nutzerort(pfad: str) -> bool:
    low = pfad.lower()
    return any(t in low for t in ("\\users\\", "\\programdata\\", "\\temp\\", "\\tmp\\"))


def ampel(e: dict) -> tuple[str, str]:
    """(Farbe, Regel) für einen Eintrag mit status/neu. Erste passende Regel gilt."""
    art, pfad, status, neu = e["art"], e.get("pfad", ""), e.get("status", ""), e.get("neu", 0)
    if art == "hosts":
        ziel = e.get("detail", "")
        if not _privat(ziel):
            return ROT, AMPEL_REGELN[4][1]
        if ziel in (str(ipaddress.ip_address(0)), "127.0.0.1", "::1") and e["name"].lower() != "localhost":
            return GELB, AMPEL_REGELN[8][1]
        return GRUEN, "lokaler Eintrag"
    if art in ("dns", "proxy"):
        return GELB, AMPEL_REGELN[8][1]
    if not pfad:
        return GRUEN, "kein Programmpfad (z. B. COM-Handler)"
    if status == "fehlt":
        return ROT, AMPEL_REGELN[0][1]
    if status in ("HashMismatch", "NotTrusted"):
        return ROT, AMPEL_REGELN[1][1]
    unsigniert = status != "Valid"
    skript = os.path.splitext(pfad)[1].lower() in _SKRIPT
    if unsigniert and _nutzerort(pfad):
        return ROT, AMPEL_REGELN[2][1]
    if unsigniert and neu:
        return ROT, AMPEL_REGELN[3][1]
    if skript:                                    # Skripte haben meist kein Signaturformat
        return GELB, AMPEL_REGELN[6][1]
    if unsigniert:
        return GELB, AMPEL_REGELN[5][1]
    if neu:
        return GELB, AMPEL_REGELN[7][1]
    return GRUEN, AMPEL_REGELN[9][1]


def sammeln(signatur, melden=None) -> tuple[list[dict], list[str]]:
    """Alle Sammler; `signatur(pfad) -> (status, signierer)`. Rückgabe: (Einträge, Fehler)."""
    raus, fehler = [], []
    for label, fn in SAMMLER:
        if melden:
            melden(label)
        try:
            eintraege = fn()
        except Exception as exc:
            fehler.append(f"{label}: {exc!r}"[:200])
            continue
        for e in eintraege:
            if e["pfad"]:
                if os.path.exists(e["pfad"]):
                    e["status"], e["signierer"] = signatur(e["pfad"])
                else:
                    e["status"], e["signierer"] = "fehlt", ""
            else:
                e["status"], e["signierer"] = "", ""
        raus += eintraege
    return raus, fehler
