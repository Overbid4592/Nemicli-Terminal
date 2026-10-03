"""Die drei Sensoren: Prozesse, Netz, Dateien – alle ohne Administratorrechte.

Prozesse und Netz arbeiten als Snapshot-Diff (psutil): was seit dem letzten
Blick dazukam oder verschwand, wird zum Ereignis. Dateien kommen ereignis-
getrieben über watchdog aus den überwachten Ordnern (Autostart, Temp,
Downloads – die klassischen Persistenz- und Staging-Orte).

Der erste Durchlauf jedes Sensors füllt nur die Baseline und meldet EINE
Zusammenfassung – sonst regneten 240 „Prozess gestartet“ herein.

Ohne Adminrechte: Pfad und Kommandozeile fremder Prozesse sind oft nicht
lesbar (Felder bleiben leer), psutil.net_connections zeigt vor allem eigene
Sockets. Das reicht für die Baseline des Benutzerkontexts.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time
import traceback
from typing import Callable

import psutil

from .ereignisse import DATEI, NETZ, PROZESS, Ereignis, Schwere

# Ports, die erklärungsbedürftig sind.
PORTS_AUFFAELLIG = {
    22: "SSH", 23: "Telnet", 445: "SMB", 1433: "MSSQL", 3306: "MySQL", 3389: "RDP",
    4444: "Metasploit-Standard", 5432: "PostgreSQL", 5900: "VNC", 6667: "IRC",
    9001: "Tor-Alt", 9050: "Tor-SOCKS",
}
PORTS_C2 = (4444, 6667, 9001, 9050, 23)

_ATTRS = ["pid", "ppid", "name", "username", "exe", "cmdline", "cpu_percent",
          "memory_info", "create_time", "num_threads"]


def extern(ip: str) -> bool:
    """True, wenn die IP nicht privat/loopback/link-local ist."""
    if not ip:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_reserved or a.is_unspecified)


# ---------------------------------------------------------------------------
# Prozesse
# ---------------------------------------------------------------------------

class ProzessSensor:
    name = "prozesse"

    def __init__(self, intervall: float = 3.0):
        self.intervall = intervall
        self._bekannt: dict[int, dict] = {}
        self._erster = True

    def schnappschuss(self) -> dict[int, dict]:
        procs: dict[int, dict] = {}
        for p in psutil.process_iter(_ATTRS, ad_value=None):
            try:
                i = p.info
                pid = i.get("pid")
                if pid is None:
                    continue
                mem = i.get("memory_info")
                cmd = i.get("cmdline") or []
                procs[pid] = {
                    "pid": pid, "ppid": i.get("ppid") or 0, "name": i.get("name") or f"pid-{pid}",
                    "nutzer": i.get("username") or "", "exe": i.get("exe") or "",
                    "cmdline": " ".join(cmd) if isinstance(cmd, list) else "",
                    "cpu": i.get("cpu_percent") or 0.0,
                    "mem": (mem.rss / 1_048_576) if mem else 0.0,
                    "threads": i.get("num_threads") or 0,
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        return procs

    def sammeln(self) -> list[Ereignis]:
        aktuell = self.schnappschuss()
        out: list[Ereignis] = []
        if self._erster:
            self._bekannt = aktuell
            self._erster = False
            return [Ereignis(PROZESS, "baseline", f"Baseline: {len(aktuell)} laufende Prozesse",
                             extra={"anzahl": len(aktuell)})]
        alt, neu = set(self._bekannt), set(aktuell)
        for pid in neu - alt:
            p = aktuell[pid]
            out.append(Ereignis(
                PROZESS, "process_start", f"Prozess gestartet: {p['name']} (PID {pid})",
                prozess=p["name"], pid=pid, ppid=p["ppid"],
                eltern=self._elternname(p["ppid"], aktuell), nutzer=p["nutzer"],
                exe=p["exe"], cmdline=p["cmdline"],
                extra={"cpu": round(p["cpu"], 2), "mem_mb": round(p["mem"], 2),
                       "threads": p["threads"]}))
        for pid in alt - neu:
            p = self._bekannt[pid]
            out.append(Ereignis(PROZESS, "process_stop", f"Prozess beendet: {p['name']} (PID {pid})",
                                prozess=p["name"], pid=pid, ppid=p["ppid"], nutzer=p["nutzer"],
                                exe=p["exe"]))
        for pid in neu & alt:
            if pid == 0:                          # "System Idle Process" zählt Leerlauf aller Kerne
                continue
            n, a = aktuell[pid], self._bekannt[pid]
            if 70 < n["cpu"] <= 100 * 64 and n["cpu"] - a["cpu"] > 40:
                out.append(Ereignis(PROZESS, "cpu_spike", f"CPU-Sprung: {n['name']} bei {n['cpu']:.0f}%",
                                    schwere=Schwere.NIEDRIG, prozess=n["name"], pid=pid,
                                    nutzer=n["nutzer"], exe=n["exe"],
                                    extra={"cpu": round(n["cpu"], 2)}))
            if n["mem"] - a["mem"] > 500:
                out.append(Ereignis(PROZESS, "memory_spike",
                                    f"Speicher-Sprung: {n['name']} +{n['mem'] - a['mem']:.0f} MB",
                                    schwere=Schwere.NIEDRIG, prozess=n["name"], pid=pid,
                                    nutzer=n["nutzer"], exe=n["exe"],
                                    extra={"mem_mb": round(n["mem"], 2)}))
        self._bekannt = aktuell
        return out

    @staticmethod
    def _elternname(ppid: int, tabelle: dict[int, dict]) -> str:
        e = tabelle.get(ppid)
        if e:
            return e["name"]
        try:
            return psutil.Process(ppid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            return ""


# ---------------------------------------------------------------------------
# Netz
# ---------------------------------------------------------------------------

class NetzSensor:
    name = "netz"

    def __init__(self, intervall: float = 5.0):
        self.intervall = intervall
        self._bekannt: set[tuple] = set()
        self._erster = True
        self._namen: dict[int, tuple[str, str]] = {}

    def _prozessinfo(self, pid: int | None) -> tuple[str, str]:
        """(Name, Pfad) zur PID – gecacht, weil dieselbe PID viele Sockets hat.

        Der Pfad muss mit: Ein Alarm „Prozess X verbindet sich nach draußen“ ist
        ohne ihn nicht prüfbar, denn der Name allein ist frei wählbar. `.exe()`
        hängt am selben psutil-Objekt wie `.name()` und kostet nichts extra.
        """
        if not pid:
            return "", ""
        if pid in self._namen:
            return self._namen[pid]
        n, exe = "", ""
        try:
            p = psutil.Process(pid)
            n = p.name()
            try:
                exe = p.exe() or ""
            except (psutil.AccessDenied, OSError):
                exe = ""              # ohne Adminrechte bei fremden Prozessen normal
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            pass
        self._namen[pid] = (n, exe)
        return n, exe

    def sammeln(self) -> list[Ereignis]:
        out: list[Ereignis] = []
        aktuell: set[tuple] = set()
        details: dict[tuple, dict] = {}
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            return out
        for c in conns:
            if c.status not in ("ESTABLISHED", "LISTEN", "SYN_SENT"):
                continue
            lokal = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
            ziel = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
            key = (c.pid or 0, lokal, ziel, c.status)
            aktuell.add(key)
            details[key] = {"pid": c.pid or 0, "lokal": lokal, "ziel": ziel,
                            "ip": c.raddr.ip if c.raddr else "",
                            "port": c.raddr.port if c.raddr else 0, "status": c.status,
                            "proto": "TCP" if c.type == 1 else "UDP"}
        if self._erster:
            self._bekannt = aktuell
            self._erster = False
            return [Ereignis(NETZ, "baseline", f"Netz-Baseline: {len(aktuell)} aktive Sockets",
                             extra={"anzahl": len(aktuell)})]
        for key in aktuell - self._bekannt:
            d = details[key]
            name, exe = self._prozessinfo(d["pid"])
            if d["status"] == "LISTEN":
                out.append(Ereignis(NETZ, "listen_open",
                                    f"Neuer Listener: {name or 'unbekannt'} auf {d['lokal']}",
                                    schwere=Schwere.NIEDRIG, prozess=name, pid=d["pid"],
                                    exe=exe, lokal=d["lokal"], protokoll=d["proto"]))
                continue
            ist_extern = extern(d["ip"])
            schwere, hinweis = Schwere.INFO, PORTS_AUFFAELLIG.get(d["port"], "")
            if hinweis:
                schwere = Schwere.NIEDRIG
            if ist_extern and d["port"] in PORTS_C2:
                schwere = Schwere.MITTEL
            text = f"Verbindung: {name or 'unbekannt'} → {d['ziel']}" + (f" ({hinweis})" if hinweis else "")
            out.append(Ereignis(NETZ, "conn_open", text, schwere=schwere, prozess=name,
                                pid=d["pid"], exe=exe, lokal=d["lokal"], ziel=d["ip"],
                                zielport=d["port"], protokoll=d["proto"],
                                extra={"extern": ist_extern}))
        self._bekannt = aktuell
        if len(self._namen) > 2000:
            self._namen.clear()
        return out


# ---------------------------------------------------------------------------
# Dateien (watchdog)
# ---------------------------------------------------------------------------

_SENSIBEL = ("startup", "\\temp", "/temp", "appdata\\roaming", "downloads")
_IGNORIERT = ("__psscriptpolicytest_", "~$")     # PowerShell-Testdatei je Start, Office-Sperren
STANDARD_ENDUNGEN = [".exe", ".dll", ".ps1", ".bat", ".cmd", ".vbs", ".js", ".scr", ".lnk"]


def standard_pfade() -> list[str]:
    pfade = []
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    profil = os.environ.get("USERPROFILE")
    if appdata:
        pfade.append(os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup"))
    if local:
        pfade.append(os.path.join(local, "Temp"))
    if profil:
        pfade.append(os.path.join(profil, "Downloads"))
    gesehen, out = set(), []
    for p in pfade:
        k = os.path.normcase(os.path.realpath(p))
        if k not in gesehen and os.path.isdir(p):
            gesehen.add(k)
            out.append(p)
    return out


class DateiSensor:
    name = "dateien"

    def __init__(self, pfade: list[str] | None, endungen: list[str] | None, intervall: float = 2.0):
        self.intervall = intervall
        self.pfade = pfade if pfade is not None else standard_pfade()
        self.endungen = {e.lower() for e in (endungen or STANDARD_ENDUNGEN)}
        self._puffer: list[tuple] = []
        self._lock = threading.Lock()
        self._letzte: dict[tuple, float] = {}
        self._observer = None
        self.ueberwacht: list[str] = []
        self.fehlgeschlagen: list[str] = []

    def _interessant(self, pfad: str) -> bool:
        if not pfad or os.path.splitext(pfad)[1].lower() not in self.endungen:
            return False
        return not os.path.basename(pfad).lower().startswith(_IGNORIERT)

    def _merken(self, aktion: str, pfad: str, ziel: str = "") -> None:
        if not self._interessant(pfad) and not self._interessant(ziel):
            return
        jetzt = time.time()
        key = (aktion, pfad)
        if jetzt - self._letzte.get(key, 0.0) < 1.5:     # Editoren feuern mehrfach
            return
        self._letzte[key] = jetzt
        with self._lock:
            self._puffer.append((aktion, pfad, ziel, jetzt))
            if len(self._letzte) > 500:
                self._letzte.clear()

    def start(self) -> None:
        if self._observer is not None:
            return
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        sensor = self

        class _H(FileSystemEventHandler):
            def on_created(self, e):
                if not e.is_directory: sensor._merken("file_created", e.src_path)
            def on_modified(self, e):
                if not e.is_directory: sensor._merken("file_modified", e.src_path)
            def on_deleted(self, e):
                if not e.is_directory: sensor._merken("file_deleted", e.src_path)
            def on_moved(self, e):
                if not e.is_directory:
                    sensor._merken("file_moved", e.src_path, getattr(e, "dest_path", ""))

        self._observer = Observer()
        for p in self.pfade:
            try:
                self._observer.schedule(_H(), p, recursive=False)
                self.ueberwacht.append(p)
            except (OSError, PermissionError):
                self.fehlgeschlagen.append(p)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None

    def sammeln(self) -> list[Ereignis]:
        if self._observer is None:
            self.start()
        with self._lock:
            stapel, self._puffer = self._puffer, []
        out: list[Ereignis] = []
        for aktion, pfad, ziel, ts in stapel:
            wohin = ziel or pfad
            low = wohin.lower()
            sensibel = any(h in low for h in _SENSIBEL)
            ext = os.path.splitext(wohin)[1].lower()
            schwere = Schwere.INFO
            if aktion in ("file_created", "file_moved"):
                schwere = Schwere.NIEDRIG
                if sensibel and ext in (".exe", ".ps1", ".bat", ".cmd", ".vbs", ".scr"):
                    schwere = Schwere.MITTEL
            label = {"file_created": "Datei angelegt", "file_modified": "Datei geändert",
                     "file_deleted": "Datei gelöscht", "file_moved": "Datei verschoben"}[aktion]
            text = f"{label}: {os.path.basename(wohin)}"
            if ziel:
                text += f" ({os.path.basename(pfad)} → {os.path.basename(ziel)})"
            out.append(Ereignis(DATEI, aktion, text, schwere=schwere, datei=wohin, zeit=ts,
                                extra={"ordner": os.path.dirname(wohin), "endung": ext,
                                       "sensibel": sensibel}))
        return out


# ---------------------------------------------------------------------------
# Takt-Thread
# ---------------------------------------------------------------------------

class SensorThread(threading.Thread):
    """Fragt einen Sensor im Takt ab und reicht Ereignisse weiter. Ein
    Fehler im Durchlauf zählt und wird gemeldet, killt aber den Sensor nicht."""

    def __init__(self, sensor, weiter: Callable[[list[Ereignis]], None],
                 fehler: Callable[[str, str], None] | None = None):
        super().__init__(daemon=True, name=f"wache-{sensor.name}")
        self.sensor = sensor
        self._weiter = weiter
        self._fehler = fehler
        self._stopp = threading.Event()
        self.ereignisse = 0
        self.fehler = 0
        self.letzter_fehler = ""

    def stop(self) -> None:
        self._stopp.set()

    @property
    def laeuft(self) -> bool:
        return self.is_alive() and not self._stopp.is_set()

    def run(self) -> None:
        while not self._stopp.is_set():
            try:
                liste = self.sensor.sammeln()
                if liste:
                    self.ereignisse += len(liste)
                    self._weiter(liste)
            except Exception:
                self.fehler += 1
                self.letzter_fehler = traceback.format_exc(limit=3)
                if self._fehler:
                    self._fehler(self.sensor.name, self.letzter_fehler)
            self._stopp.wait(self.sensor.intervall)
