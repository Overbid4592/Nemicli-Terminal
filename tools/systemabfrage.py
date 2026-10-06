"""
systemabfrage.py - feste, nur lesende Systemabfragen für das Werkzeug `abfragen`.

Alles läuft im eigenen Prozess (psutil, winreg, wintrust, wevtapi) – es wird
kein Kindprozess gestartet. Welche Abfrage, bestimmt das Feld `was`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import platform
import re
import time
from pathlib import Path

import psutil

try:
    import winreg
except ImportError:                       # pragma: no cover
    winreg = None

MAX_ZEILEN = 300

ARTEN = {
    "prozesse":     "laufende Prozesse (filter: Text im Namen/Pfad)",
    "prozess":      "ein Prozess im Detail (pid) – Pfad, Kommandozeile, Eltern, Signatur, Verbindungen",
    "verbindungen": "Netzverbindungen mit Prozess (filter: Text, Port oder pid)",
    "dienste":      "Windows-Dienste (filter: Text; mit filter auch Pfad und Starttyp)",
    "autostart":    "Run/RunOnce-Einträge in HKCU/HKLM",
    "aufgaben":     "geplante Aufgaben (filter: Text)",
    "software":     "installierte Programme (filter: Text)",
    "signatur":     "Authenticode-Signatur einer Datei (pfad)",
    "hash":         "SHA-256 einer Datei (pfad)",
    "datei":        "Größe und Zeitstempel einer Datei (pfad)",
    "registry":     "Werte und Unterschlüssel eines Registry-Schlüssels (schluessel)",
    "laufwerke":    "Laufwerke mit Belegung",
    "system":       "Betriebssystem, Laufzeit, CPU, Arbeitsspeicher, Grafikkarten, Rechenweg lokaler Modelle",
    "netz":         "Netzwerkadapter mit Adressen und Zählern",
    "ereignisse":   "Ereignisprotokoll (kanal, optional id, anzahl)",
    "nutzer":       "angemeldete Benutzer",
}

_ALIASE = {
    "prozessliste": "prozesse", "processes": "prozesse", "process": "prozess",
    "netzwerk": "netz", "verbindung": "verbindungen", "connections": "verbindungen",
    "ports": "verbindungen", "dienst": "dienste", "services": "dienste",
    "aufgabe": "aufgaben", "tasks": "aufgaben", "programme": "software",
    "sha256": "hash", "registrierung": "registry", "reg": "registry",
    "laufwerk": "laufwerke", "datentraeger": "laufwerke", "ereignis": "ereignisse",
    "events": "ereignisse", "benutzer": "nutzer",
}

_HIVES = {
    "HKLM": "HKEY_LOCAL_MACHINE", "HKCU": "HKEY_CURRENT_USER", "HKCR": "HKEY_CLASSES_ROOT",
    "HKU": "HKEY_USERS", "HKCC": "HKEY_CURRENT_CONFIG",
}


class AbfrageFehler(ValueError):
    pass


def hilfe() -> str:
    return "Mögliche Werte für 'was':\n" + "\n".join(f"  {k:<13} {v}" for k, v in ARTEN.items())


def art(roh: str) -> str | None:
    w = str(roh or "").strip().lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    w = _ALIASE.get(w, w)
    return w if w in ARTEN else None


def _passt(filt: str, *teile) -> bool:
    if not filt:
        return True
    f = filt.lower()
    return any(f in str(t or "").lower() for t in teile)


def _zeit(t: float | None) -> str:
    if not t:
        return "–"
    return _dt.datetime.fromtimestamp(t).strftime("%d.%m.%Y %H:%M:%S")


def _mb(n: float | int | None) -> str:
    return f"{(n or 0) / 1048576:.0f} MB"


def _gb(n: float | int | None) -> str:
    return f"{(n or 0) / 1073741824:.1f} GB"


def _kuerzen(zeilen: list[str], was: str) -> str:
    if not zeilen:
        return "(keine Treffer)"
    if len(zeilen) > MAX_ZEILEN:
        rest = len(zeilen) - MAX_ZEILEN
        zeilen = zeilen[:MAX_ZEILEN] + [f"… {rest} weitere – mit 'filter' eingrenzen."]
    return "\n".join(zeilen)


def _int(v, vorgabe: int | None = None) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return vorgabe


# ---------------------------------------------------------------------------
# Prozesse
# ---------------------------------------------------------------------------

def prozesse(filt: str = "") -> str:
    zeilen = []
    for p in psutil.process_iter(["pid", "ppid", "name", "exe", "memory_info"], ad_value=None):
        i = p.info
        if not _passt(filt, i.get("name"), i.get("exe"), i.get("pid")):
            continue
        mem = i.get("memory_info")
        zeilen.append(f"{i['pid']:>6}  {i.get('name') or '?':<32} eltern {i.get('ppid') or 0:>6}  "
                      f"{_mb(mem.rss if mem else 0):>8}  {i.get('exe') or ''}")
    zeilen.sort(key=lambda z: z[8:40].lower())
    kopf = f"{'PID':>6}  {'Name':<32} {'Eltern':>13}  {'RAM':>8}  Pfad"
    return kopf + "\n" + _kuerzen(zeilen, "prozesse") if zeilen else "(keine Treffer)"


def prozess(pid: int | None, filt: str = "") -> str:
    if pid is None:
        if not filt:
            raise AbfrageFehler("Für 'prozess' fehlt 'pid' (oder ein Name in 'filter').")
        treffer = [p for p in psutil.process_iter(["name"]) if _passt(filt, p.info.get("name"))]
        if not treffer:
            return f"Kein Prozess mit '{filt}' im Namen."
        if len(treffer) > 1:
            return (f"{len(treffer)} Prozesse passen – nimm 'pid':\n"
                    + "\n".join(f"  {p.pid}  {p.info.get('name')}" for p in treffer[:40]))
        pid = treffer[0].pid
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return f"Kein Prozess mit PID {pid}."

    def feld(fn, vorgabe="(kein Zugriff)"):
        try:
            return fn()
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            return vorgabe
        except psutil.NoSuchProcess:
            return "(beendet)"

    exe = feld(p.exe, "")
    if exe:
        _schutz_pruefen(exe)
    eltern = feld(p.ppid, 0)
    eltern_name = ""
    if eltern:
        try:
            eltern_name = psutil.Process(eltern).name()
        except (psutil.Error, OSError):
            eltern_name = "(nicht mehr da)"
    cmd = feld(p.cmdline, [])
    zeilen = [
        f"Name:        {feld(p.name)}",
        f"PID:         {pid}",
        f"Pfad:        {exe or '(kein Zugriff)'}",
        f"Kommando:    {' '.join(cmd) if isinstance(cmd, list) else cmd}",
        f"Eltern:      {eltern} {eltern_name}",
        f"Gestartet:   {_zeit(feld(p.create_time, 0))}",
        f"Benutzer:    {feld(p.username)}",
        f"RAM:         {_mb(getattr(feld(p.memory_info, None), 'rss', 0))}",
    ]
    if exe:
        zeilen.append(f"Signatur:    {_signatur_zeile(exe)}")
    try:
        verb = p.net_connections(kind="inet")
    except (psutil.Error, OSError):
        verb = []
    if verb:
        zeilen.append("Verbindungen:")
        for c in verb[:40]:
            zeilen.append("  " + _verbindung_text(c))
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Netz
# ---------------------------------------------------------------------------

def _adresse(a) -> str:
    if not a:
        return "-"
    return f"[{a.ip}]:{a.port}" if ":" in a.ip else f"{a.ip}:{a.port}"


def _verbindung_text(c, namen: dict | None = None) -> str:
    art_ = "TCP" if c.type == 1 else "UDP"
    name = (namen or {}).get(c.pid, "")
    wer = f"{c.pid or 0} {name}".strip()
    return f"{art_}  {_adresse(c.laddr):<28} → {_adresse(c.raddr):<28} {c.status or '':<12} {wer}"


def verbindungen(filt: str = "", pid: int | None = None) -> str:
    try:
        alle = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError) as exc:
        return f"Verbindungen nicht lesbar: {exc}"
    namen: dict[int, str] = {}
    for p in psutil.process_iter(["name"]):
        namen[p.pid] = p.info.get("name") or ""
    zeilen = []
    for c in alle:
        if pid is not None and c.pid != pid:
            continue
        text = _verbindung_text(c, namen)
        if not _passt(filt, text):
            continue
        zeilen.append(text)
    zeilen.sort()
    return _kuerzen(zeilen, "verbindungen")


def netz() -> str:
    adressen = psutil.net_if_addrs()
    status = psutil.net_if_stats()
    zaehler = psutil.net_io_counters(pernic=True)
    zeilen = []
    for name in sorted(adressen):
        st = status.get(name)
        an = "an" if st and st.isup else "aus"
        tempo = f"{st.speed} Mbit/s" if st and st.speed else ""
        zeilen.append(f"{name}  ({an}{', ' + tempo if tempo else ''})")
        for a in adressen[name]:
            if a.family in (2, 23):             # AF_INET, AF_INET6
                zeilen.append(f"    {a.address}")
        z = zaehler.get(name)
        if z:
            zeilen.append(f"    empfangen {_mb(z.bytes_recv)} · gesendet {_mb(z.bytes_sent)}")
    return _kuerzen(zeilen, "netz")


# ---------------------------------------------------------------------------
# Dienste, Autostart, Aufgaben, Software, Nutzer
# ---------------------------------------------------------------------------

def dienste(filt: str = "") -> str:
    if not hasattr(psutil, "win_service_iter"):
        return "Dienste gibt es nur unter Windows."
    zeilen = []
    for s in psutil.win_service_iter():
        try:
            name, anzeige = s.name(), s.display_name()
            if not _passt(filt, name, anzeige):
                continue
            if filt:
                d = s.as_dict()
                zeilen.append(f"{name:<32} {d.get('status', ''):<9} {d.get('start_type', ''):<9} "
                              f"{anzeige}\n    {d.get('binpath', '')}")
            else:
                zeilen.append(f"{name:<32} {s.status():<9} {anzeige}")
        except (psutil.Error, OSError):
            continue
    return _kuerzen(zeilen, "dienste")


def _inventar():
    from wache.inventar import Inventar
    return Inventar(mit_aufgaben=True)


def autostart() -> str:
    inv = _inventar()
    zeilen = [f"{e.name}  →  {e.detail}" for e in inv.autostart()]
    zeilen += [f"(Fehler) {f}" for f in inv.fehler]
    return _kuerzen(zeilen, "autostart")


def aufgaben(filt: str = "") -> str:
    inv = _inventar()
    zeilen = []
    for e in inv.aufgaben():
        if not _passt(filt, e.name):
            continue
        zeilen.append(f"{e.name}  ·  {e.extra.get('status', '')}  ·  nächster: {e.extra.get('naechster', '')}")
    zeilen += [f"(Fehler) {f}" for f in inv.fehler]
    return _kuerzen(zeilen, "aufgaben")


def software(filt: str = "") -> str:
    inv = _inventar()
    zeilen = []
    for e in inv.software():
        if not _passt(filt, e.name, e.detail):
            continue
        zeilen.append(f"{e.name}  {e.detail}".rstrip())
    zeilen.sort(key=str.lower)
    return _kuerzen(zeilen, "software")


def nutzer() -> str:
    zeilen = [f"{u.name}  seit {_zeit(u.started)}  {u.host or ''}".rstrip() for u in psutil.users()]
    return _kuerzen(zeilen, "nutzer")


# ---------------------------------------------------------------------------
# Dateien
# ---------------------------------------------------------------------------

def _signatur_zeile(pfad: str) -> str:
    try:
        from wache.herkunft import signatur_zeile
        return signatur_zeile(pfad) or "nicht prüfbar"
    except Exception as exc:
        return f"nicht prüfbar ({exc})"


def _schutz_pruefen(*texte) -> None:
    """Schutzsoftware wird nicht untersucht (schutzsoftware.py)."""
    try:
        import schutzsoftware as S
    except ImportError:
        return
    for t in texte:
        if not t:
            continue
        t = str(t)
        if S.nennt(t) or (os.path.isabs(os.path.expandvars(t)) and S.betroffen(t)):
            raise AbfrageFehler(S.HINWEIS)


def _datei_pfad(pfad: str) -> Path:
    if not str(pfad or "").strip():
        raise AbfrageFehler("Es fehlt 'pfad'.")
    p = Path(os.path.expandvars(str(pfad).strip().strip('"')))
    _schutz_pruefen(str(p))
    if not p.is_file():
        raise AbfrageFehler(f"Keine Datei: {p}")
    return p


def signatur(pfad: str) -> str:
    p = _datei_pfad(pfad)
    return f"{p}\n{_signatur_zeile(str(p))}"


def sha256(pfad: str) -> str:
    p = _datei_pfad(pfad)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return f"SHA-256  {h.hexdigest().upper()}\n{p}"


def datei(pfad: str) -> str:
    p = _datei_pfad(pfad)
    st = p.stat()
    return "\n".join([
        f"Datei:     {p}",
        f"Größe:     {st.st_size:,} Bytes".replace(",", "."),
        f"Geändert:  {_zeit(st.st_mtime)}",
        f"Erstellt:  {_zeit(st.st_ctime)}",
    ])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _schluessel_teilen(roh: str):
    s = str(roh or "").strip().strip('"').replace("/", "\\")
    s = re.sub(r"^(Registry::|Microsoft\.PowerShell\.Core\\Registry::)", "", s, flags=re.I)
    s = re.sub(r"^(HK[A-Z]{1,3}):", r"\1", s, flags=re.I)
    kopf, _, rest = s.partition("\\")
    hive = _HIVES.get(kopf.upper(), kopf.upper())
    if not hive.startswith("HKEY_") or not hasattr(winreg, hive):
        raise AbfrageFehler(f"Unbekannter Registry-Zweig '{kopf}'. Beispiel: HKLM\\SOFTWARE\\Microsoft")
    return getattr(winreg, hive), rest.strip("\\"), hive


def registry(schluessel: str) -> str:
    if winreg is None:
        return "Registry gibt es nur unter Windows."
    if not str(schluessel or "").strip():
        raise AbfrageFehler("Es fehlt 'schluessel', z.B. HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run")
    wurzel, pfad, hive = _schluessel_teilen(schluessel)
    try:
        k = winreg.OpenKey(wurzel, pfad, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return f"Schlüssel nicht vorhanden: {hive}\\{pfad}"
    except PermissionError:
        return f"Kein Lesezugriff: {hive}\\{pfad}"
    zeilen = [f"{hive}\\{pfad}"]
    with k:
        i = 0
        while True:
            try:
                name, wert, _typ = winreg.EnumValue(k, i)
            except OSError:
                break
            text = wert.hex() if isinstance(wert, bytes) else str(wert)
            zeilen.append(f"  {name or '(Standard)'} = {text[:400]}")
            i += 1
        i, unter = 0, []
        while True:
            try:
                unter.append(winreg.EnumKey(k, i))
            except OSError:
                break
            i += 1
    if unter:
        zeilen.append(f"Unterschlüssel ({len(unter)}):")
        zeilen += [f"  {u}" for u in unter]
    return _kuerzen(zeilen, "registry")


# ---------------------------------------------------------------------------
# System, Laufwerke
# ---------------------------------------------------------------------------

def system() -> str:
    ram = psutil.virtual_memory()
    boot = psutil.boot_time()
    stunden = (time.time() - boot) / 3600
    cpu, extra = platform.processor(), []
    try:
        import systemprofil                      # echter CPU-Name, Grafikkarten, Rechenweg
        p = systemprofil.erkennen(prozesse=False)
        cpu = p.cpu if p.cpu != "?" else cpu
        extra = [z.replace(":", ":   ", 1) for z in p.zeilen() if z.startswith(("GPU", "Motor"))]
    except Exception:
        pass
    return "\n".join([
        f"System:     {platform.platform()}",
        f"Rechner:    {platform.node()}",
        f"Gestartet:  {_zeit(boot)} (seit {stunden:.1f} h)",
        f"CPU:        {cpu} · {psutil.cpu_count(logical=False)} Kerne / "
        f"{psutil.cpu_count()} Threads · Last {psutil.cpu_percent(interval=0.3):.0f} %",
        f"RAM:        {_gb(ram.used)} von {_gb(ram.total)} belegt ({ram.percent:.0f} %)",
    ] + extra)


def laufwerke() -> str:
    zeilen = []
    for t in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(t.mountpoint)
            belegt = f"{_gb(u.used)} von {_gb(u.total)} ({u.percent:.0f} %) · frei {_gb(u.free)}"
        except (PermissionError, OSError):
            belegt = "nicht lesbar"
        zeilen.append(f"{t.mountpoint:<6} {t.fstype:<6} {belegt}")
    return _kuerzen(zeilen, "laufwerke")


# ---------------------------------------------------------------------------
# Ereignisprotokoll
# ---------------------------------------------------------------------------

_KANAL_KURZ = {"system": "System", "anwendung": "Application", "application": "Application",
               "sicherheit": "Security", "security": "Security", "setup": "Setup"}
_EBENE = {"1": "Kritisch", "2": "Fehler", "3": "Warnung", "4": "Info", "0": "Info", "5": "Ausführlich"}


def ereignisse(kanal: str, ereignis_id: int | None = None, anzahl: int = 20) -> str:
    import xml.etree.ElementTree as ET
    from wache.herkunft import _evt_xml, _zeit_lokal, _daten
    k = str(kanal or "").strip() or "System"
    k = _KANAL_KURZ.get(k.lower(), k)
    anzahl = max(1, min(int(anzahl or 20), 100))
    xpath = f"*[System[(EventID={int(ereignis_id)})]]" if ereignis_id is not None else "*"
    roh = _evt_xml(k, xpath, grenze=anzahl)
    if not roh:
        return f"Keine Ereignisse in '{k}' (oder Kanal nicht lesbar – Sicherheit braucht Adminrechte)."
    zeilen = []
    for x in roh:
        try:
            w = ET.fromstring(x)
        except ET.ParseError:
            continue
        sys_ = {el.tag.rsplit("}", 1)[-1]: el for el in w.iter()}
        zeit = sys_.get("TimeCreated")
        _, wann = _zeit_lokal(zeit.get("SystemTime", "") if zeit is not None else "")
        quelle = sys_.get("Provider").get("Name", "") if sys_.get("Provider") is not None else ""
        eid = (sys_.get("EventID").text or "").strip() if sys_.get("EventID") is not None else ""
        ebene = _EBENE.get((sys_.get("Level").text or "").strip() if sys_.get("Level") is not None else "", "")
        daten = "; ".join(f"{a}={b}" for a, b in list(_daten(w).items())[:6] if b)
        zeilen.append(f"{wann}  {ebene:<8} {eid:>5}  {quelle}" + (f"\n    {daten[:300]}" if daten else ""))
    return _kuerzen(zeilen, "ereignisse")


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------

def ausfuehren(a: dict) -> str:
    """Führt die Abfrage aus `a` aus. AbfrageFehler bei fehlenden/falschen Feldern."""
    w = art(a.get("was") or a.get("art") or "")
    if not w:
        raise AbfrageFehler(f"Unbekannte Abfrage '{a.get('was', '')}'.\n" + hilfe())
    filt = str(a.get("filter") or a.get("name") or "").strip()
    pid = _int(a.get("pid"))
    pfad = a.get("pfad") or ""
    _schutz_pruefen(filt, a.get("schluessel"))
    if w == "prozesse":
        return prozesse(filt)
    if w == "prozess":
        return prozess(pid, filt)
    if w == "verbindungen":
        return verbindungen(filt, pid)
    if w == "dienste":
        return dienste(filt)
    if w == "autostart":
        return autostart()
    if w == "aufgaben":
        return aufgaben(filt)
    if w == "software":
        return software(filt)
    if w == "signatur":
        return signatur(pfad)
    if w == "hash":
        return sha256(pfad)
    if w == "datei":
        return datei(pfad)
    if w == "registry":
        return registry(a.get("schluessel") or a.get("pfad") or "")
    if w == "laufwerke":
        return laufwerke()
    if w == "system":
        return system()
    if w == "netz":
        return netz()
    if w == "ereignisse":
        return ereignisse(a.get("kanal") or "", _int(a.get("id")), _int(a.get("anzahl"), 20) or 20)
    if w == "nutzer":
        return nutzer()
    raise AbfrageFehler(hilfe())            # pragma: no cover
