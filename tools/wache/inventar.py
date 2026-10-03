"""Systeminventar: der Ist-Zustand des Rechners, einmal beim Start und dann alle
paar Stunden.

Ohne diesen Scan lernt die Wache nur, was zufällig während ihrer Laufzeit
passiert – ein Prozess, der seit dem Boot läuft, bliebe für immer „unbekannt“.
Der Scan gibt jedem laufenden Prozess sofort ein Profil (Baseline ab Sekunde
eins) und merkt sich Dienste, Autostart, Aufgaben, Software, Ports. Jeder
spätere Scan vergleicht: was ist neu? Ein neuer Autostart-Eintrag oder Dienst
ist genau das Muster, mit dem sich Schadsoftware einnistet.

Beim ersten Scan ist alles neu – deshalb meldet er nur eine Zusammenfassung,
keine 700 Einzel-Ereignisse.

Dienstnamen sind nicht stabil: Windows hängt an Benutzer-Dienste die LUID der
Sitzung an (CDPUserSvc_f69e08b). Ohne Normalisierung wären das bei JEDER
Anmeldung zwei Dutzend „neue Dienste“.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import subprocess
import time

import psutil

from .ereignisse import SYSTEM, Ereignis, Schwere

try:
    import winreg
except ImportError:                       # pragma: no cover
    winreg = None

_AUTOSTART = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
]
_UNINSTALL = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]

PROZESS, AUTOSTART, DIENST, SOFTWARE, AUFGABE, PORT, SCHNITTSTELLE, NUTZER = (
    "prozess", "autostart", "dienst", "software", "aufgabe", "port", "schnittstelle", "nutzer")

_SITZUNG = re.compile(r"_[0-9a-f]{5,}$", re.IGNORECASE)


def dienstname(name: str) -> str:
    """Sitzungs-Anhang abschneiden – nur den langen Hex-Teil, `_1` bleibt."""
    return _SITZUNG.sub("", name or "")


def eintrag_id(art: str, identitaet: str) -> str:
    return hashlib.sha1(f"{art}::{identitaet.lower()}".encode("utf-8", "replace")).hexdigest()[:16]


class Eintrag:
    __slots__ = ("art", "name", "detail", "extra")

    def __init__(self, art: str, name: str, detail: str = "", **extra):
        self.art, self.name, self.detail, self.extra = art, name, detail, extra

    @property
    def id(self) -> str:
        name = dienstname(self.name) if self.art == DIENST else self.name
        return eintrag_id(self.art, f"{name}|{self.detail}")


def _reg(key, name: str) -> str:
    try:
        wert, _ = winreg.QueryValueEx(key, name)
        return str(wert) if wert is not None else ""
    except (FileNotFoundError, OSError):
        return ""


class Inventar:
    def __init__(self, mit_aufgaben: bool = True):
        self.mit_aufgaben = mit_aufgaben
        self.letzter_scan = 0.0
        self.dauer = 0.0
        self.zaehlung: dict[str, int] = {}
        self.fehler: list[str] = []

    def scan(self) -> list[Eintrag]:
        start = time.perf_counter()
        self.fehler = []
        out: list[Eintrag] = []
        teile = [("Prozesse", self.prozesse), ("Autostart", self.autostart),
                 ("Dienste", self.dienste), ("Software", self.software),
                 ("Ports", self.ports), ("Schnittstellen", self.schnittstellen),
                 ("Nutzer", self.nutzer)]
        if self.mit_aufgaben:
            teile.append(("Aufgaben", self.aufgaben))
        for label, fn in teile:
            try:
                out.extend(fn())
            except Exception as exc:
                self.fehler.append(f"{label}: {exc!r}")
        self.letzter_scan = time.time()
        self.dauer = time.perf_counter() - start
        self.zaehlung = {}
        for e in out:
            self.zaehlung[e.art] = self.zaehlung.get(e.art, 0) + 1
        return out

    def prozesse(self) -> list[Eintrag]:
        out = []
        attrs = ["pid", "ppid", "name", "username", "exe", "cmdline", "memory_info"]
        for p in psutil.process_iter(attrs, ad_value=None):
            try:
                i = p.info
                if not i.get("name"):
                    continue
                eltern = ""
                try:
                    if i.get("ppid"):
                        eltern = psutil.Process(i["ppid"]).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                    pass
                cmd = i.get("cmdline") or []
                mem = i.get("memory_info")
                out.append(Eintrag(PROZESS, i["name"], i.get("exe") or "", pid=i.get("pid"),
                                   eltern=eltern, nutzer=i.get("username") or "",
                                   cmdline=" ".join(cmd) if isinstance(cmd, list) else "",
                                   mem_mb=round(mem.rss / 1_048_576, 1) if mem else 0.0))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return out

    def autostart(self) -> list[Eintrag]:
        out = []
        if winreg is not None:
            wurzeln = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
            for wurzel, sub in _AUTOSTART:
                try:
                    with winreg.OpenKey(wurzeln[wurzel], sub) as k:
                        i = 0
                        while True:
                            try:
                                name, wert, _ = winreg.EnumValue(k, i)
                            except OSError:
                                break
                            i += 1
                            out.append(Eintrag(AUTOSTART, name, str(wert),
                                               quelle=f"{wurzel}\\{sub}", herkunft="registry"))
                except FileNotFoundError:
                    continue
                except PermissionError:
                    self.fehler.append(f"Autostart {wurzel}\\{sub}: kein Zugriff")
        ordner = []
        for var in ("APPDATA", "PROGRAMDATA"):
            basis = os.environ.get(var)
            if basis:
                ordner.append(os.path.join(basis, r"Microsoft\Windows\Start Menu\Programs\Startup"))
        for o in ordner:
            if not os.path.isdir(o):
                continue
            try:
                for e in os.scandir(o):
                    if e.is_file():
                        out.append(Eintrag(AUTOSTART, e.name, e.path, quelle=o, herkunft="ordner"))
            except OSError:
                continue
        return out

    def dienste(self) -> list[Eintrag]:
        out = []
        if not hasattr(psutil, "win_service_iter"):
            return out
        for s in psutil.win_service_iter():
            try:
                i = s.as_dict()
                out.append(Eintrag(DIENST, i.get("name", ""), i.get("binpath", "") or "",
                                   anzeige=i.get("display_name", ""), status=i.get("status", ""),
                                   start=i.get("start_type", ""), nutzer=i.get("username", "")))
            except Exception:
                continue
        return out

    def software(self) -> list[Eintrag]:
        out = []
        if winreg is None:
            return out
        wurzeln = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
        gesehen: set[str] = set()
        for wurzel, sub in _UNINSTALL:
            try:
                with winreg.OpenKey(wurzeln[wurzel], sub) as k:
                    n = winreg.QueryInfoKey(k)[0]
                    for i in range(n):
                        try:
                            kind = winreg.EnumKey(k, i)
                            with winreg.OpenKey(k, kind) as c:
                                name = _reg(c, "DisplayName")
                                if not name or name in gesehen:
                                    continue
                                gesehen.add(name)
                                out.append(Eintrag(SOFTWARE, name, _reg(c, "InstallLocation"),
                                                   version=_reg(c, "DisplayVersion"),
                                                   hersteller=_reg(c, "Publisher")))
                        except OSError:
                            continue
            except FileNotFoundError:
                continue
            except PermissionError:
                self.fehler.append(f"Software {wurzel}: kein Zugriff")
        return out

    def aufgaben(self, timeout: int = 25) -> list[Eintrag]:
        out = []
        try:
            r = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/nh"], capture_output=True,
                               text=True, timeout=timeout, errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            self.fehler.append(f"Aufgaben: {exc!r}")
            return out
        if r.returncode != 0:
            return out
        for zeile in csv.reader(io.StringIO(r.stdout)):
            if len(zeile) < 3:
                continue
            name = zeile[0].strip('"').strip()
            if not name or name.lower() == "taskname":
                continue
            out.append(Eintrag(AUFGABE, name, "", naechster=zeile[1].strip('"'),
                               status=zeile[2].strip('"')))
        return out

    def ports(self) -> list[Eintrag]:
        out = []
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            self.fehler.append("Ports: kein Zugriff")
            return out
        for c in conns:
            if c.status != "LISTEN" or not c.laddr:
                continue
            name, exe = "", ""
            if c.pid:
                try:
                    p = psutil.Process(c.pid)
                    name = p.name()
                    try:                  # Pfad mit, damit ein neuer Listener prüfbar ist
                        exe = p.exe() or ""
                    except (psutil.AccessDenied, OSError):
                        pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                    pass
            out.append(Eintrag(PORT, f"{c.laddr.ip}:{c.laddr.port}", name, port=c.laddr.port,
                               pid=c.pid or 0, exe=exe,
                               protokoll="TCP" if c.type == 1 else "UDP"))
        return out

    def schnittstellen(self) -> list[Eintrag]:
        out = []
        try:
            adressen = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
        except Exception:
            return out
        for name, addrs in adressen.items():
            ips = [a.address for a in addrs if a.address.count(".") == 3]
            st = stats.get(name)
            out.append(Eintrag(SCHNITTSTELLE, name, ", ".join(ips), an=bool(st.isup) if st else False))
        return out

    def nutzer(self) -> list[Eintrag]:
        out = []
        try:
            for u in psutil.users():
                out.append(Eintrag(NUTZER, u.name, u.terminal or "", host=u.host or ""))
        except Exception:
            pass
        return out


_SCHWERE = {AUTOSTART: Schwere.HOCH, DIENST: Schwere.MITTEL, AUFGABE: Schwere.MITTEL,
            SOFTWARE: Schwere.NIEDRIG, PORT: Schwere.NIEDRIG, PROZESS: Schwere.INFO,
            SCHNITTSTELLE: Schwere.NIEDRIG, NUTZER: Schwere.MITTEL}
_LABEL = {AUTOSTART: "Neuer Autostart-Eintrag", DIENST: "Neuer Dienst",
          AUFGABE: "Neue geplante Aufgabe", SOFTWARE: "Neu installierte Software",
          PORT: "Neuer offener Port", PROZESS: "Neuer Prozess im Inventar",
          SCHNITTSTELLE: "Neue Netzwerkschnittstelle", NUTZER: "Neue Benutzersitzung"}


def neue_als_ereignisse(eintraege: list[Eintrag], bekannt: set[str]) -> list[Ereignis]:
    """Nur NEUE Funde werden Ereignisse (der erste Scan meldet nur eine Zusammenfassung)."""
    out = []
    for e in eintraege:
        if e.id in bekannt:
            continue
        out.append(Ereignis(
            SYSTEM, f"inventory_new_{e.art}", f"{_LABEL.get(e.art, 'Neuer Eintrag')}: {e.name}"
            + (f" ({e.detail})" if e.detail else ""),
            schwere=_SCHWERE.get(e.art, Schwere.NIEDRIG),
            prozess=e.name if e.art == PROZESS else (e.detail if e.art == PORT else ""),
            # Ports tragen den Pfad in extra["exe"] – sonst stünde beim Alarm
            # „Neuer offener Port“ nur eine Portnummer ohne prüfbares Programm.
            exe=(e.detail if e.art in (PROZESS, AUTOSTART, DIENST)
                 else (str(e.extra.get("exe") or "") if e.art == PORT else "")),
            extra={"art": e.art, **{k: v for k, v in e.extra.items() if isinstance(v, (str, int, float, bool))}}))
    return out
