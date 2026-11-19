"""KI-Tracker: erkennt die KIs auf dem Rechner und schreibt mit, was sie tun – alles bleibt lokal.

Drei Quellen, alle als Ereignisse der Kategorie `ki` (Regeln R019–R024 in regeln.py):

1. Erkennen (`KiBaum`): bekannte KI-Programme (KI_PROGRAMME) und Programme, die mit einem KI-Dienst
   sprechen (KI_DIENSTE, am Namen des Verbindungsziels; Browser ausgenommen). Was eine KI startet, gehört
   zu ihrem Prozessbaum; Prozess- und Netz-Ereignisse daraus tragen `extra["ki"]`. Die Wache selbst und
   ihre Kindprozesse zählen nie dazu.
2. Von außen (`KiProzessSensor`): die Prozessbäume der KIs im Takt von 0,5 s – kurze Befehle, die der
   allgemeine Prozess-Sensor (3 s) verpasst – als `ki/ki_prozess`; neu erkannte KIs als `ki/ki_erkannt`.
3. Selbstauskunft (`KiProtokollSensor`): die eigenen Protokolle der KIs – Claude Code (~/.claude/projects),
   Codex (~/.codex/sessions), NemiCLI (learned/aktionen.log) – als `ki/ki_tat`. Übernommen wird nur, was
   getan wurde: Werkzeug, Befehl, Dateipfad, Adresse, Suchbegriff, Zeit, Arbeitsordner. Nie Gesagtes, nie
   Dateiinhalte. Wird ein frisch benutztes Protokoll kürzer oder verschwindet es: `ki/ki_protokoll_weg`.

Gegencheck (`KiGegencheck`): Startet Claude Code oder Codex eine Shell, muss der Befehl in ihrem Protokoll
stehen – sonst `ki/ki_ungemeldet` (R024; Schattenmodus, bis der Nutzer /wache ki warnen sagt).

Der Lesestand je Datei liegt in Wache/ki_protokolle.json. Beim ersten Lauf kommen die letzten NACHTRAG_TAGE
als „nachgetragen“ – sie stehen in der Zeitleiste, lösen aber keine Regeln aus.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

from .ereignisse import KI, NETZ, PROZESS, Ereignis, Schwere

KI_PROGRAMME = {
    "claude.exe": "Claude", "codex.exe": "Codex", "ollama.exe": "Ollama", "ollama app.exe": "Ollama",
    "lm studio.exe": "LM Studio", "lms.exe": "LM Studio", "cursor.exe": "Cursor", "windsurf.exe": "Windsurf",
    "jan.exe": "Jan", "nemicli.exe": "NemiCLI", "nemiclic.exe": "NemiCLI", "llama-server.exe": "llama.cpp",
    "gemini.exe": "Gemini CLI", "aider.exe": "Aider", "goose.exe": "Goose",
}
# Schnittstellen der KI-Dienste (nicht ihre Webseiten – die besucht auch ein Mensch im Browser).
KI_DIENSTE = {
    "api.anthropic.com": "Anthropic", "api.openai.com": "OpenAI", "generativelanguage.googleapis.com": "Gemini",
    "aiplatform.googleapis.com": "Gemini", "api.x.ai": "xAI", "api.mistral.ai": "Mistral",
    "api.deepseek.com": "DeepSeek", "openrouter.ai": "OpenRouter", "api.groq.com": "Groq",
    "api.together.xyz": "Together", "api.perplexity.ai": "Perplexity", "api.cohere.com": "Cohere",
    "api.fireworks.ai": "Fireworks", "api.moonshot.ai": "Moonshot", "ollama.com": "Ollama Cloud",
}
BROWSER = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "iexplore.exe"}

INTERVALL_S = 0.5
NEU_SCANNEN_S = 30.0
NACHTRAG_TAGE = 30
JE_DURCHLAUF = 2000                 # Taten je Durchlauf (die Ereignis-Schlange fasst 20 000)
LESEN_ALLE_S = 5.0
AKTIV_S = 2 * 86400                 # ein so lange unberührtes Protokoll darf die KI selbst aufräumen
MAX_TEXT = 2000

_CLAUDE_ART = {"Bash": "befehl", "PowerShell": "befehl", "Read": "lesen", "NotebookRead": "lesen",
               "Edit": "schreiben", "MultiEdit": "schreiben", "Write": "schreiben", "NotebookEdit": "schreiben",
               "Grep": "suchen", "Glob": "suchen", "LS": "suchen", "WebFetch": "web", "WebSearch": "websuche",
               "Agent": "agent", "Task": "agent"}
_NEMI_ART = {"befehl": "befehl", "abfragen": "befehl", "code_ausfuehren": "befehl", "datei_lesen": "lesen",
             "ordner_auflisten": "suchen", "dateien_suchen": "suchen", "inhalt_suchen": "suchen",
             "datei_schreiben": "schreiben", "datei_bearbeiten": "schreiben", "datei_kopieren": "schreiben",
             "verschieben": "schreiben", "ordner_erstellen": "schreiben", "loeschen": "loeschen",
             "web_lesen": "web", "web_suche": "websuche", "web_wiki": "websuche", "recherche": "websuche"}


def _klein(name: str) -> str:
    return os.path.basename(str(name or "")).lower()


def programm_ki(prozess: str = "", exe: str = "") -> str:
    """Name der KI, wenn das Programm selbst eine ist – sonst ""."""
    return KI_PROGRAMME.get(_klein(exe) or _klein(prozess)) or KI_PROGRAMME.get(_klein(prozess), "")


def dienst_ki(name: str) -> str:
    """Name des KI-Dienstes hinter einem Verbindungsziel – sonst ""."""
    n = str(name or "").lower().rstrip(".")
    for host, dienst in KI_DIENSTE.items():
        if n == host or n.endswith("." + host):
            return dienst
    return ""


# --------------------------------------------------------------------------- 1. Erkennen

class KiBaum:
    """Welche Prozesse gehören zu welcher KI? Schlüssel: (PID, Startzeit) – Windows vergibt PIDs neu."""

    def __init__(self, eigen_pid: int | None = None):
        self._lock = threading.Lock()
        self.wurzeln: dict[tuple[int, float], str] = {}
        self.mitglieder: dict[tuple[int, float], str] = {}
        self._pid: dict[int, tuple[int, float]] = {}
        self.eigen_pid = os.getpid() if eigen_pid is None else eigen_pid
        self._eigen: set[int] = {self.eigen_pid}

    @staticmethod
    def schluessel(pid: int, start: float) -> tuple[int, float]:
        return int(pid or 0), round(float(start or 0.0), 3)

    def ki_von(self, pid: int, start: float = 0.0) -> str:
        with self._lock:
            k = self.schluessel(pid, start) if start else self._pid.get(int(pid or 0))
            return self.mitglieder.get(k, "") if k else ""

    def _aufnehmen(self, k: tuple[int, float], name: str, wurzel: bool) -> bool:
        if k[0] in self._eigen or k in self.mitglieder:
            return False
        self.mitglieder[k] = name
        self._pid[k[0]] = k
        if wurzel:
            self.wurzeln[k] = name
        return True

    def eigenen_ausschliessen(self, pid: int) -> None:
        with self._lock:
            self._eigen.add(int(pid))

    def wurzel(self, pid: int, start: float, name: str) -> bool:
        with self._lock:
            return self._aufnehmen(self.schluessel(pid, start), name, True)

    def kind(self, pid: int, start: float, ppid: int, eltern_start: float = 0.0) -> str:
        """Gehört der Elternprozess zu einer KI, gehört das Kind dazu. Gibt den KI-Namen zurück."""
        with self._lock:
            if int(ppid or 0) in self._eigen:
                self._eigen.add(int(pid or 0))
                return ""
            ek = self.schluessel(ppid, eltern_start) if eltern_start else self._pid.get(int(ppid or 0))
            name = self.mitglieder.get(ek, "") if ek else ""
            if name:
                self._aufnehmen(self.schluessel(pid, start), name, False)
            return name

    def entfernen(self, pid: int, start: float = 0.0) -> None:
        with self._lock:
            k = self.schluessel(pid, start) if start else self._pid.get(int(pid or 0))
            if k:
                self.mitglieder.pop(k, None)
                self.wurzeln.pop(k, None)
                if self._pid.get(k[0]) == k:
                    del self._pid[k[0]]

    def aufraeumen(self, infos: dict[int, dict]) -> int:
        """Nicht mehr laufende Prozesse vergessen (PID + Startzeit; ohne Startzeit nach PID) – auch die
        ausgeschlossenen eigenen Kinder. `infos`: pid → {create_time, …} der laufenden Prozesse."""
        lebend = {self.schluessel(pid, i.get("create_time") or 0.0) for pid, i in infos.items()}
        with self._lock:
            weg = [k for k in self.mitglieder if (k not in lebend if k[1] else k[0] not in infos)]
            for k in weg:
                self.mitglieder.pop(k, None)
                self.wurzeln.pop(k, None)
                if self._pid.get(k[0]) == k:
                    del self._pid[k[0]]
            self._eigen = {p for p in self._eigen if p in infos} | {self.eigen_pid}
        return len(weg)

    def scannen(self) -> list[tuple[int, float, str]]:
        """Laufende Prozesse durchsehen: neue KI-Wurzeln und ihre Nachkommen. Gibt neue Wurzeln zurück."""
        neu = []
        eltern: dict[int, list] = {}
        infos = {}
        for p in psutil.process_iter(["pid", "ppid", "name", "exe", "create_time"], ad_value=None):
            i = p.info
            infos[i["pid"]] = i
            eltern.setdefault(i.get("ppid") or 0, []).append(i)
        self.aufraeumen(infos)
        for i in infos.values():
            name = programm_ki(i.get("name") or "", i.get("exe") or "")
            if name and self.wurzel(i["pid"], i.get("create_time") or 0.0, name):
                neu.append((i["pid"], i.get("create_time") or 0.0, name))
        offen = [k for k in list(self.mitglieder)]
        while offen:                                   # Nachkommen bekannter Mitglieder nachtragen
            pid, start = offen.pop()
            for c in eltern.get(pid, []):
                if self.kind(c["pid"], c.get("create_time") or 0.0, pid, start):
                    offen.append(self.schluessel(c["pid"], c.get("create_time") or 0.0))
        return neu

    def markieren(self, e: Ereignis) -> Ereignis | None:
        """Prozess- und Netz-Ereignis der Wache: KI-Name in extra["ki"] schreiben. Ein Programm, das mit einem
        KI-Dienst spricht und noch keiner KI gehört, wird selbst zur KI – dann kommt ein ki_erkannt zurück."""
        name = ""
        if e.kategorie == PROZESS and e.aktion == "process_start":
            name = self.kind(e.pid, e.start, e.ppid, e.eltern_start)
            if not name and (prog := programm_ki(e.prozess, e.exe)) and self.wurzel(e.pid, e.start, prog):
                name = prog
        elif e.kategorie == PROZESS and e.aktion == "process_stop":
            name = self.ki_von(e.pid, e.start)
            self.entfernen(e.pid, e.start)
        elif e.kategorie == NETZ:
            name = self.ki_von(e.pid)
            dienst = dienst_ki((e.extra or {}).get("name", ""))
            if dienst:
                e.extra = {**(e.extra or {}), "ki_dienst": dienst}
            if not name and dienst and _klein(e.prozess) not in BROWSER and e.pid not in self._eigen:
                name = programm_ki(e.prozess, e.exe) or os.path.splitext(_klein(e.prozess))[0] or f"pid-{e.pid}"
                if self.wurzel(e.pid, e.start or self._startzeit(e.pid), name):
                    e.extra = {**(e.extra or {}), "ki": name}
                    return Ereignis(KI, "ki_erkannt", f"{name} spricht mit {dienst} – als KI erkannt",
                                    prozess=name, pid=e.pid, exe=e.exe, ziel=e.ziel, zielport=e.zielport,
                                    schwere=Schwere.NIEDRIG,
                                    extra={"ki": name, "quelle": "wache", "art": "erkannt", "dienst": dienst})
        if name:
            e.extra = {**(e.extra or {}), "ki": name}
        return None

    @staticmethod
    def _startzeit(pid: int) -> float:
        try:
            return psutil.Process(pid).create_time()
        except (psutil.Error, ValueError):
            return 0.0


# --------------------------------------------------------------------------- Gegencheck

# Welche KI im Prozessbaum zu welchem Protokoll gehört. Nur KIs mit eigenem Protokoll lassen sich gegenprüfen.
GEPRUEFT = {"Claude": "Claude Code", "Codex": "Codex"}
SHELLS = {"bash.exe", "sh.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "zsh.exe"}
# Helfer, die die KI-Programme selbst starten (Konsole, Git-Stand, Suche, Aktualisierung) – ohne Werkzeugaufruf.
INTERN = {"conhost.exe", "git.exe", "claude.exe", "codex.exe", "rg.exe", "node.exe", "cygpath.exe", "where.exe",
          "openconsole.exe"}
GEGEN_FENSTER_S = 1800              # so lange vor dem Start darf der Befehl im Protokoll stehen (Rückfragen dauern)
GEGEN_WARTEN_S = 60                 # so lange auf das Protokoll warten, bevor ein Start als ungemeldet gilt
_MERKMAL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{3,}")
_STARTER = re.compile(r"(?i)\$env:claude_code_shell_launcher_script")
# Zusammengesetzt wie in regeln.py: der Schalter steht nicht ausgeschrieben im Quelltext.
_KODIERT = re.compile(r"(?i)\s-(?:e|ec|enc|" + "enc" + "oded" + r"command)\s+([A-Za-z0-9+/=]{8,})")


def entschluesselt(cmdline: str) -> str:
    """Base64-Befehl einer PowerShell-Befehlszeile im Klartext (gekürzt) – sonst ""."""
    import base64
    import binascii
    m = _KODIERT.search(cmdline or "")
    if not m:
        return ""
    roh = m.group(1)
    try:
        text = base64.b64decode(roh + "=" * (-len(roh) % 4)).decode("utf-16-le", errors="replace")
    except (binascii.Error, ValueError):
        return ""
    return _kurz(text, 600)


def merkmale(befehl: str) -> list[str]:
    """Die drei längsten Wörter eines Befehls – überstehen Anführungszeichen und Maskierung der Shell."""
    return sorted({t.lower() for t in _MERKMAL.findall(befehl or "")}, key=lambda t: (-len(t), t))[:3]


class KiGegencheck:
    """Startet eine KI eine Shell, muss der Befehl dazu in ihrem eigenen Protokoll stehen. Erste Kinder der
    KI-Wurzel werden mit den gemeldeten Befehlen verglichen (die längsten Wörter des Befehls in der Befehlszeile);
    alles darunter gehört zum Befehl seiner Shell. Ein gemeldeter Befehl deckt genau einen Start."""

    def __init__(self):
        self._lock = threading.Lock()
        self._offen: list[dict] = []
        self._taten: dict[str, list[dict]] = {}

    def beobachtet(self, ki: str, name: str, cmdline: str, zeit: float, pid: int) -> None:
        name = _klein(name)
        if ki not in GEPRUEFT or name in INTERN:
            return
        with self._lock:
            self._offen.append({"ki": ki, "name": name, "cmd": cmdline or "", "zeit": zeit, "pid": pid})
            del self._offen[:-500]

    def gemeldet(self, e: Ereignis) -> None:
        x = e.extra or {}
        if x.get("art") not in ("befehl", "suchen") or time.time() - e.zeit > GEGEN_FENSTER_S:
            return
        with self._lock:
            liste = self._taten.setdefault(e.prozess, [])
            liste.append({"zeit": e.zeit, "befehl": e.cmdline or "", "art": x.get("art"),
                          "werkzeug": x.get("werkzeug", ""), "frei": True})
            del liste[:-2000]

    def pruefen(self, jetzt: float | None = None) -> list[Ereignis]:
        jetzt = time.time() if jetzt is None else jetzt
        raus = []
        with self._lock:
            bleiben = []
            for o in self._offen:
                if jetzt - o["zeit"] < GEGEN_WARTEN_S:
                    bleiben.append(o)
                    continue
                if not self._decken(o):
                    klar = entschluesselt(o["cmd"])
                    raus.append(Ereignis(
                        KI, "ki_ungemeldet", f"{o['ki']} startete {o['name']} – im eigenen Protokoll steht kein "
                        "passender Befehl", zeit=o["zeit"], prozess=o["name"], pid=o["pid"],
                        cmdline=_kurz(o["cmd"] + (f"  [entschlüsselt: {klar}]" if klar else "")),
                        schwere=Schwere.MITTEL,
                        extra={"ki": o["ki"], "quelle": "wache", "art": "gegencheck", "protokoll": GEPRUEFT[o["ki"]]}))
            self._offen = bleiben
            for liste in self._taten.values():
                liste[:] = [t for t in liste if jetzt - t["zeit"] < GEGEN_FENSTER_S + GEGEN_WARTEN_S]
        return raus

    def _decken(self, o: dict) -> bool:
        taten = [t for t in self._taten.get(GEPRUEFT[o["ki"]], [])
                 if o["zeit"] - GEGEN_FENSTER_S <= t["zeit"] <= o["zeit"] + 10]
        klar = entschluesselt(o["cmd"])
        cmd = (o["cmd"] + " " + klar).lower()
        # Startprogramm, das den Befehl über eine Umgebungsvariable übergibt: passt zu genau einem Aufruf
        # desselben Werkzeugs (Claude Code: PowerShell-Werkzeug).
        if _STARTER.search(cmd):
            for t in taten:
                if t["frei"] and t["werkzeug"] == "PowerShell":
                    t["frei"] = False
                    return True
            return False
        for t in taten:
            if not t["frei"] or (o["name"] in SHELLS and t["art"] != "befehl"):
                continue
            if o["cmd"] and not all(m in cmd for m in merkmale(t["befehl"])):
                continue                                   # Befehlszeile lesbar: muss zum Befehl passen
            t["frei"] = False
            return True
        # Entschlüsselter Hilfsprozess (z. B. Umgebungsabfrage) kurz nach einem gemeldeten Shell-Aufruf
        return bool(klar) and any(t["werkzeug"] in ("PowerShell", "Bash") and
                                  o["zeit"] - 60 <= t["zeit"] <= o["zeit"] + 10 for t in taten)


# --------------------------------------------------------------------------- 2. Von außen

class KiProzessSensor:
    """Prozessbäume der KIs im schnellen Takt."""
    name = "ki-prozesse"

    def __init__(self, baum: KiBaum, intervall: float = INTERVALL_S, gegencheck: "KiGegencheck | None" = None):
        self.baum = baum
        self.intervall = intervall
        self.gegencheck = gegencheck
        self._gescannt = 0.0
        self._erster = True

    def sammeln(self) -> list[Ereignis]:
        out: list[Ereignis] = []
        jetzt = time.time()
        if jetzt - self._gescannt > NEU_SCANNEN_S:
            self._gescannt = jetzt
            for pid, start, name in self.baum.scannen():
                out.append(Ereignis(KI, "ki_erkannt", f"{name} läuft (PID {pid})" + (
                    " – beim Start der Wache gefunden" if self._erster else ""), prozess=name, pid=pid,
                    start=start, extra={"ki": name, "quelle": "wache", "art": "erkannt"}))
            if self._erster:                            # vorhandene Nachkommen sind Ausgangslage, kein Start
                self._erster = False
                return out
        for (pid, start), name in list(self.baum.wurzeln.items()):
            try:
                kinder = psutil.Process(pid).children(recursive=True)
            except (psutil.Error, ValueError):
                self.baum.entfernen(pid, start)
                continue
            for c in kinder:
                try:
                    with c.oneshot():
                        cstart, cppid = c.create_time(), c.ppid()
                        if self.baum.ki_von(c.pid, cstart):
                            continue
                        ki = self.baum.kind(c.pid, cstart, cppid)
                        if not ki:
                            continue
                        try:
                            exe, cmd = c.exe(), " ".join(c.cmdline())
                        except psutil.Error:
                            exe, cmd = "", ""
                        cname = c.name()
                except psutil.Error:
                    continue
                if self.gegencheck is not None and cppid == pid:          # erstes Kind: ein Werkzeugaufruf der KI
                    self.gegencheck.beobachtet(name, cname, cmd, cstart, c.pid)
                out.append(Ereignis(KI, "ki_prozess", f"{ki} startet {cname}", prozess=cname, pid=c.pid,
                                    ppid=cppid, exe=exe, cmdline=_kurz(cmd), start=cstart,
                                    extra={"ki": ki, "quelle": "wache", "art": "prozess"}))
        return out


# --------------------------------------------------------------------------- 3. Selbstauskunft

def _kurz(text, n: int = MAX_TEXT) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _zeit(iso: str, ersatz: float) -> float:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return ersatz


def _tat(ki: str, quelle: str, werkzeug: str, art: str, zeit: float, *, befehl: str = "", datei: str = "",
         ziel: str = "", such: str = "", ordner: str = "", sitzung: str = "", neben: bool = False,
         nachgetragen: bool = False) -> Ereignis:
    was = befehl or datei or ziel or such or werkzeug
    extra = {"ki": ki, "quelle": quelle, "werkzeug": werkzeug, "art": art}
    for k, v in (("ordner", ordner), ("sitzung", sitzung[:36]), ("suche", _kurz(such, 300))):
        if v:
            extra[k] = v
    if neben:
        extra["nebenagent"] = True
    if nachgetragen:
        extra["nachgetragen"] = True
    return Ereignis(KI, "ki_tat", _kurz(f"{ki} · {werkzeug}: {was}", 300), zeit=zeit, prozess=ki,
                    cmdline=_kurz(befehl), datei=_kurz(datei, 500), ziel=_kurz(ziel, 500), extra=extra)


def claude_taten(zeile: str, nachgetragen: bool = False) -> list[Ereignis]:
    """Eine Zeile eines Claude-Code-Protokolls → Taten (nur Werkzeugaufrufe, ohne Inhalte)."""
    try:
        o = json.loads(zeile)
    except ValueError:
        return []
    if not isinstance(o, dict) or o.get("type") != "assistant":
        return []
    inhalt = (o.get("message") or {}).get("content")
    if not isinstance(inhalt, list):
        return []
    zeit = _zeit(o.get("timestamp"), time.time())
    raus = []
    for b in inhalt:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        w, i = str(b.get("name") or "?"), b.get("input") or {}
        if not isinstance(i, dict):
            i = {}
        raus.append(_tat("Claude Code", "claude_code", w, _CLAUDE_ART.get(w, "werkzeug"), zeit,
                         befehl=str(i.get("command") or ""),
                         datei=str(i.get("file_path") or i.get("notebook_path") or i.get("path") or ""),
                         ziel=str(i.get("url") or ""),
                         such=str(i.get("pattern") or i.get("query") or i.get("description") or ""),
                         ordner=str(o.get("cwd") or ""), sitzung=str(o.get("sessionId") or ""),
                         neben=bool(o.get("isSidechain")), nachgetragen=nachgetragen))
    return raus


def codex_taten(zeile: str, nachgetragen: bool = False) -> list[Ereignis]:
    """Eine Zeile einer Codex-Sitzung (rollout-*.jsonl) → Taten. Codex schreibt Werkzeugaufrufe als
    function_call / custom_tool_call / local_shell_call; die Argumente stehen als JSON-Text darin."""
    try:
        o = json.loads(zeile)
    except ValueError:
        return []
    if not isinstance(o, dict):
        return []
    p = o.get("payload") if isinstance(o.get("payload"), dict) else o
    typ = str(p.get("type") or "")
    if typ not in ("function_call", "custom_tool_call", "local_shell_call"):
        return []
    zeit = _zeit(o.get("timestamp"), time.time())
    name = str(p.get("name") or ("shell" if typ == "local_shell_call" else "?"))
    args = p.get("arguments") if "arguments" in p else p.get("input")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"text": args}
    if typ == "local_shell_call":
        args = p.get("action") or {}
    if not isinstance(args, dict):
        args = {}
    cmd = args.get("command") or args.get("cmd") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    datei = ""
    if name == "apply_patch":
        text = str(args.get("text") or args.get("input") or "")
        datei = ", ".join(re.findall(r"(?m)^\*\*\* (?:Add|Update|Delete) File: (.+)$", text))[:500]
    art = "befehl" if cmd else ("schreiben" if datei else "werkzeug")
    return [_tat("Codex", "codex", name, art, zeit, befehl=str(cmd), datei=datei,
                 ordner=str(args.get("workdir") or args.get("cwd") or ""), nachgetragen=nachgetragen)]


_NEMI_ZEILE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ([^|]+)\| ([^|]+)\| ([^|]+)\| ([^|]+)\| (.*)$")
_NEMI_FREMD = {"wache", "recherche_intern"}


def nemi_taten(zeile: str, nachgetragen: bool = False) -> list[Ereignis]:
    """Eine Zeile aus NemiCLIs Aktions-Protokoll → Tat (die Wache selbst zählt nicht)."""
    m = _NEMI_ZEILE.match(zeile.rstrip("\r\n"))
    if not m:
        return []
    zeit_t, _art, werkzeug, status, wer, text = (t.strip() for t in m.groups())
    if werkzeug in _NEMI_FREMD:
        return []
    try:
        zeit = datetime.strptime(zeit_t, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        zeit = time.time()
    ki = f"NemiCLI · {wer}" if wer and wer != "-" else "NemiCLI"
    beschreibung = text.split(" → Ergebnis:", 1)[0]
    _, _, rest = beschreibung.partition(": ")
    rest = rest or beschreibung
    art = _NEMI_ART.get(werkzeug, "werkzeug")
    e = _tat(ki, "nemicli", werkzeug, art, zeit, befehl=rest if art == "befehl" else "",
             datei=rest if art in ("lesen", "schreiben", "loeschen", "suchen") else "",
             ziel=rest if art == "web" else "", such=rest if art == "websuche" else "", nachgetragen=nachgetragen)
    e.extra["status"] = status
    return [e]


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def protokoll_quellen() -> list[tuple[str, Path, str, object]]:
    """(Name, Wurzelordner, Muster, Leser) aller bekannten KI-Protokolle – in der Test-Suite keine
    (Tests lesen nie die echten Protokolle des Rechners)."""
    try:
        import paths
        if paths._testlauf():
            return []
        nemi = Path(paths.ROOT) / "learned"
    except Exception:
        nemi = None
    q = [("claude_code", _home() / ".claude" / "projects", "**/*.jsonl", claude_taten),
         ("codex", _home() / ".codex" / "sessions", "**/*.jsonl", codex_taten)]
    if nemi is not None:
        q.append(("nemicli", nemi, "aktionen.log", nemi_taten))
    return q


class KiProtokollSensor:
    """Liest die Protokolle der KIs fortlaufend mit (nur neue Zeilen)."""
    name = "ki-protokolle"

    def __init__(self, stand_datei: Path, quellen: list | None = None, intervall: float = LESEN_ALLE_S,
                 gegencheck: "KiGegencheck | None" = None):
        self.stand_datei = Path(stand_datei)
        self.quellen = quellen
        self.gegencheck = gegencheck
        self.intervall = intervall
        self._stand: dict[str, dict] = {}
        self._geladen = False
        self.erster_lauf = False

    def _laden(self) -> None:
        self._geladen = True
        try:
            self._stand = json.loads(self.stand_datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._stand, self.erster_lauf = {}, True

    def _speichern(self) -> None:
        try:
            self.stand_datei.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.stand_datei.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._stand), encoding="utf-8")
            os.replace(tmp, self.stand_datei)
        except OSError:
            pass

    def sammeln(self) -> list[Ereignis]:
        if not self._geladen:
            self._laden()
        out: list[Ereignis] = []
        jetzt = time.time()
        gesehen: set[str] = set()
        voll = False
        for quelle, wurzel, muster, leser in (self.quellen if self.quellen is not None else protokoll_quellen()):
            if not wurzel.is_dir():
                continue
            for pfad in wurzel.glob(muster):
                if len(out) >= JE_DURCHLAUF:
                    voll = True                         # Rest beim nächsten Durchlauf – nicht als gelöscht werten
                    break
                schluessel = str(pfad)
                gesehen.add(schluessel)
                try:
                    st = pfad.stat()
                except OSError:
                    continue
                alt = self._stand.get(schluessel)
                if alt is None:
                    alt_genug = jetzt - st.st_mtime > NACHTRAG_TAGE * 86400
                    alt = {"pos": st.st_size if alt_genug else 0, "neu": self.erster_lauf}
                    self._stand[schluessel] = alt
                if st.st_size < alt["pos"]:                # kürzer geworden: Rotation erlaubt, sonst melden
                    if not (quelle == "nemicli" and pfad.with_suffix(".log.1").exists()):
                        out.append(self._weg(quelle, pfad, "gekürzt", alt))
                    alt["pos"] = 0
                if st.st_size == alt["pos"]:
                    alt["mtime"] = st.st_mtime
                    continue
                out += self._lesen(pfad, alt, leser, JE_DURCHLAUF - len(out))
                alt["mtime"] = st.st_mtime
        for schluessel in ([] if voll else [k for k in self._stand if k not in gesehen]):
            alt = self._stand.pop(schluessel)
            if not Path(schluessel).exists() and jetzt - float(alt.get("mtime") or 0) < AKTIV_S:
                out.append(self._weg(self._quelle_von(schluessel), Path(schluessel), "gelöscht", alt))
        self.erster_lauf = False
        self._speichern()
        if self.gegencheck is not None:
            for e in out:
                if e.aktion == "ki_tat":
                    self.gegencheck.gemeldet(e)
            out += self.gegencheck.pruefen()
        return out

    def _quelle_von(self, schluessel: str) -> str:
        for quelle, wurzel, _m, _l in (self.quellen if self.quellen is not None else protokoll_quellen()):
            if schluessel.startswith(str(wurzel)):
                return quelle
        return "?"

    @staticmethod
    def _weg(quelle: str, pfad: Path, wie: str, alt: dict) -> Ereignis:
        return Ereignis(KI, "ki_protokoll_weg", f"Protokoll von {quelle} {wie}: {pfad.name}", prozess=quelle,
                        datei=str(pfad), schwere=Schwere.HOCH,
                        extra={"ki": quelle, "quelle": "wache", "art": "protokoll", "wie": wie,
                               "zuletzt": alt.get("mtime")})

    @staticmethod
    def _lesen(pfad: Path, alt: dict, leser, hoechstens: int) -> list[Ereignis]:
        raus: list[Ereignis] = []
        try:
            with open(pfad, "rb") as f:
                f.seek(alt["pos"])
                while len(raus) < hoechstens:
                    zeile = f.readline()
                    if not zeile or not zeile.endswith(b"\n"):     # halbe Zeile: beim nächsten Mal
                        break
                    alt["pos"] = f.tell()
                    raus += leser(zeile.decode("utf-8", errors="replace"), nachgetragen=bool(alt.get("neu")))
                else:
                    return raus
            alt.pop("neu", None)                        # Datei bis zum Ende gelesen: ab jetzt live
        except OSError:
            pass
        return raus


# --------------------------------------------------------------------------- Muster für die Regeln

def _muster(*teile: str) -> re.Pattern:
    # Zur Laufzeit zusammengesetzt: Pfade, die auch Datendiebe suchen, stehen so nicht ausgeschrieben im Quelltext.
    return re.compile("(?i)(?:" + "|".join(teile) + ")")


_B = "\\\\"
GEHEIM = _muster(
    _B + "user data" + _B + r"[^\\]+" + _B + "(?:lo" + "gin data|coo" + "kies|web data)\\b",
    _B + "user data" + _B + "lo" + "cal state\\b",
    _B + r"firefox\\profiles\\[^\\]+" + _B + "(?:lo" + "gins\\.json|ke" + "y4\\.db|coo" + "kies\\.sqlite)",
    _B + r"\.ssh(?:\\|$|\s)", _B + r"\.gnupg(?:\\|$|\s)", r"\.kd" + r"bx\b",
    _B + r"microsoft\\(?:cre" + "dentials|pro" + "tect|va" + r"ult)\\",
    _B + r"\.aws\\cre" + "dentials", _B + r"\.git-cre" + "dentials", _B + r"\.docker\\config\.json",
    _B + r"\.claude\\\.cre" + r"dentials\.json", _B + r"\.codex\\au" + r"th\.json",
)
PERSOENLICHKEITEN = _muster(_B + r"persoenlichkeiten(?:\\|$|\s)")
ADMIN = _muster(r"-verb\s+run" + "as\\b", r"\brun" + r"as(?:\.exe)?\s+/", r"\bg?su" + r"do\b",
                r"net\s+localgroup\s+admin")
AUTOSTART = _muster(r"\bsch" + r"tasks(?:\.exe)?\s+/create\b", r"(?<![\w-])register-scheduled" + "task\\b",
                    r"\bnew-ser" + "vice\\b", r"\bsc(?:\.exe)?\s+create\b",
                    r"__event" + "filter", r"commandline" + "eventconsumer")
# Autostart-Orte zählen nur mit schreibendem Befehl – nachsehen richtet nichts ein.
AUTOSTART_ORT = _muster(r"\\currentversion\\run(?:once)?\b", r"\\start menu\\programs\\startup\\")
SCHREIBEND = _muster(r"\b(?:set|new)-itemproperty\b", r"\breg(?:\.exe)?\s+add\b", r"\bnew-item\b", r"\bcopy-item\b",
                     r"\bmove-item\b", r"\bout-file\b", r"\b(?:set|add)-content\b", r"\bcopy\s", r"\bmove\s",
                     r"\bxcopy\b", r"\brobocopy\b", r"(?<![<>=-])>{1,2}\s*['\"]?[a-z]:\\")
# Befehle, die nur nach Text suchen: das Suchwort ist keine Absicht (für R020/R021, nicht für R019 –
# wer in ~/.ssh sucht, greift darauf zu).
# Ordnername beginnt mit einem Nicht-Leerzeichen: so gibt es nur eine Zerlegung (kein Rückverfolgen).
NUR_SUCHE = re.compile(r"(?i)^\s*(?:cd\s+[^;&|\s][^;&|]*(?:;|&&)\s*)*(?:grep|rg|egrep|findstr|select-string|sls|git\s+grep)\b")
LOESCHEN = _muster(r"\bremove-item\b", r"\brm\s+-[a-z]*[rf]", r"\bdel\s", r"\berase\b", r"\brmdir\b",
                   r"\brd\s+/s\b", r"shutil\.rmtree", r"\bos\.(?:remove|unlink)\b")


def autostart(e: Ereignis) -> str:
    """Was an der Tat einen Autostart, Dienst oder eine Aufgabe einrichtet – sonst ""."""
    cmd = e.cmdline or ""
    if cmd and NUR_SUCHE.match(cmd):
        return ""
    if (m := AUTOSTART.search(cmd)):
        return m.group(0)
    if (m := AUTOSTART_ORT.search(e.datei or "")) and (e.extra or {}).get("art") == "schreiben":
        return m.group(0)
    if (m := AUTOSTART_ORT.search(cmd)) and SCHREIBEND.search(cmd):
        return m.group(0)
    return ""


def admin(e: Ereignis) -> str:
    cmd = e.cmdline or ""
    if not cmd or NUR_SUCHE.match(cmd):
        return ""
    m = ADMIN.search(cmd)
    return m.group(0) if m else ""


def tat_text(e: Ereignis) -> str:
    """Wohin eine Tat greift (Befehl, Pfad, Adresse) – für die Muster der Regeln. Suchbegriffe zählen nicht:
    wer nach „.ssh“ sucht, greift noch nicht darauf zu."""
    return " ".join(t for t in (e.cmdline, e.datei, e.ziel) if t)
