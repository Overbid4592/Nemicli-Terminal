"""
shellfrei.py - Die offene Liste: reine Abfragen, die `befehl` in jedem Modus ohne Rückfrage ausführt.

Gästeliste statt Sperrliste: Frei ist nur, was hier ausdrücklich steht. Der echte PowerShell-Parser zerlegt den Befehl,
ohne ihn auszuführen; geprüft wird jeder Knoten des Baums. Was nicht sicher einzuordnen ist, fragt wie bisher.
Nicht auf der Liste: Datei-Inhalte (dafür gibt es datei_lesen mit seinen Sperren), Umgebungsvariablen, Zwischenablage,
fremde Rechner. Nach Inhalt aus dem Netz gilt die Liste nicht (modes.decide).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from functools import lru_cache

# Abfragen. Get-CimInstance nur mit den Klassen unten.
ABFRAGEN = frozenset("""
get-ciminstance get-process get-counter get-service get-volume get-command get-module get-netadapter get-psdrive
get-disk get-partition get-netipconfiguration get-netipaddress get-netroute get-dnsclientserveraddress
get-nettcpconnection get-netudpendpoint get-netconnectionprofile get-netadapterstatistics get-netfirewallprofile
get-netfirewallrule get-mpcomputerstatus get-mpthreatdetection get-alias get-psprovider get-executionpolicy get-help
get-date get-timezone get-culture get-uiculture get-hotfix
get-childitem get-location get-computerinfo get-host get-physicaldisk get-netipinterface
""".split())
ORDNER_ALIASE = frozenset({"dir", "ls"})         # gelten wie Get-ChildItem
FILTER = frozenset("""
select-object sort-object where-object group-object measure-object format-table format-list out-string
""".split())
CIM_KLASSEN = frozenset(k.lower() for k in """
Win32_BIOS Win32_Battery Win32_ComputerSystem Win32_ComputerSystemProduct Win32_DesktopMonitor Win32_DiskDrive
Win32_LogicalDisk Win32_OperatingSystem Win32_PhysicalMemory Win32_Processor Win32_Service Win32_SoundDevice
Win32_StartupCommand Win32_SystemDriver Win32_VideoController
""".split())
GIT_LESEN = ("status", "log", "diff", "show", "branch")
VERSION_PROGRAMME = ("python", "py", "pip", "git", "uv", "ruff", "ollama")

# Programme: jedes Argument muss einem Muster entsprechen; `-f` bei nvidia-smi oder `/s` bei tasklist bleiben draußen.
_NATIV: dict[str, re.Pattern] = {
    "nvidia-smi": re.compile(r"(?i)--query-[a-z-]+=[\w.,-]+|--format=[\w,-]+|-L|--list-gpus|-q|--help|-h|--version"),
    "whoami": re.compile(r"^$"),
    "hostname": re.compile(r"^$"),
    "systeminfo": re.compile(r"^$"),
    "getmac": re.compile(r"^$"),
    "driverquery": re.compile(r"^$"),
    "ipconfig": re.compile(r"(?i)/all"),
    "tasklist": re.compile(r"(?i)/nh|/v"),
}
_NV_FELDER = re.compile(r"(?i)--query-[a-z-]+=[\w.-]+(,[\w.-]+)+")
_GIT_NIE = re.compile(r"(?i)^(--output|--ext-diff|--exec|--open-files-in-pager|--no-index|-O)")
_GIT_BRANCH = re.compile(r"(?i)-a|-r|-v|-vv|--all|--list|--show-current")
_GIT_ARG = re.compile(r"[\w@^~.:/\\=%,*-]+")

# Abgekürzte Schalter zählen wie die vollen: PowerShell nimmt jeden eindeutigen Anfang.
_SCHALTER_NIE = ("computername", "cn", "cimsession", "pssession", "session", "credential", "online", "showwindow",
                 "continuous", "asjob", "servername", "__server", "ipaddress")
_CIM_SCHALTER = ("classname", "property", "filter")

_KNOTEN = frozenset("""
ScriptBlockAst NamedBlockAst PipelineAst CommandAst CommandExpressionAst CommandParameterAst StringConstantExpressionAst
ConstantExpressionAst VariableExpressionAst MemberExpressionAst InvokeMemberExpressionAst BinaryExpressionAst
UnaryExpressionAst ParenExpressionAst ArrayLiteralAst HashtableAst StatementBlockAst ScriptBlockExpressionAst
TypeExpressionAst ConvertExpressionAst TypeConstraintAst IndexExpressionAst
""".split())
_VARIABLEN = frozenset({"_", "psitem", "true", "false", "null", "psversiontable"})
_UNAER = frozenset({"Not", "Exclaim", "Minus", "Plus"})
_TYPEN_WERT = frozenset({"[int]", "[int32]", "[int64]", "[long]", "[double]", "[decimal]", "[string]"})
_MATH_TYP = frozenset({"[math]", "[system.math]"})
_MATH = frozenset({"round", "floor", "ceiling", "abs", "min", "max", "sqrt", "pow"})
# Env:, HKLM:, Cert: …, Anbieter mit Modulnamen (…\Registry::…) und Netzwerk-Freigaben (\\, //, \/ …)
_ANBIETER = re.compile(r"^[A-Za-z]{2,}:|^[\\/]{2}|::")

MAX_LAENGE = 2000
ZEITLIMIT_S = 10.0

# Gibt den Syntaxbaum als JSON aus; führt den Befehl nicht aus. Der Anfang ist NemiCLIs eigener Rahmen
# (wache/regeln.EIGENER_RAHMEN), damit die Wache den Parser-Start nicht als verdächtige PowerShell meldet.
_PARSER = (
    "$ProgressPreference='SilentlyContinue'; $OutputEncoding = [Console]::OutputEncoding;"
    "$r=New-Object IO.StreamReader([Console]::OpenStandardInput(),[Text.Encoding]::UTF8);$t=$r.ReadToEnd();"
    "$k=$null;$e=$null;"
    "$a=[System.Management.Automation.Language.Parser]::ParseInput($t,[ref]$k,[ref]$e);"
    "$n=foreach($x in $a.FindAll({$true},$true)){"
    "$o=@{t=$x.GetType().Name;x=$x.Extent.Text};"
    "switch($o.t){"
    "'CommandAst'{$o.op=[string]$x.InvocationOperator;$o.r=$x.Redirections.Count;"
    "$o.e=@(foreach($c in $x.CommandElements){$w=@{t=$c.GetType().Name;x=$c.Extent.Text};"
    "if($c.PSObject.Properties['ParameterName']){$w.p=$c.ParameterName;$w.a=[bool]$c.Argument};"
    "if($c.PSObject.Properties['StringConstantType']){$w.v=$c.Value};$w})}"
    "'VariableExpressionAst'{$o.v=$x.VariablePath.UserPath}"
    "'InvokeMemberExpressionAst'{$o.m=$x.Member.Extent.Text;$o.s=[bool]$x.Static;$o.z=$x.Expression.Extent.Text}"
    "'UnaryExpressionAst'{$o.k=[string]$x.TokenKind}"
    "};$o};"
    "$j=@{f=$e.Count;s=$a.EndBlock.Statements.Count;n=@($n)}|ConvertTo-Json -Depth 6 -Compress;"
    "$b=[Text.Encoding]::UTF8.GetBytes($j);$s=[Console]::OpenStandardOutput();$s.Write($b,0,$b.Length)"
)


def _bekannter_anfang(cmd: str) -> bool:
    """Spart den Parser-Start, wenn das erste Wort gar nicht auf der Liste steht."""
    m = re.match(r"\s*\(*\s*(\$?[\w.-]+)", cmd)
    if m is None:
        return False
    w = m.group(1).lower()
    return (w in ABFRAGEN or w in ORDNER_ALIASE or w in _NATIV or w in VERSION_PROGRAMME
            or w.split(".")[0] == "$psversiontable")


def _baum(cmd: str) -> dict | None:
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PARSER], input=cmd.encode("utf-8"),
                           capture_output=True, timeout=ZEITLIMIT_S,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return json.loads(r.stdout.decode("utf-8-sig")) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _schalter_ok(name: str) -> bool:
    n = name.lower()
    return bool(n) and not any(s.startswith(n) for s in _SCHALTER_NIE)


def _cim_grund(elemente: list[dict]) -> str | None:
    klasse, i = None, 0
    while i < len(elemente):
        e = elemente[i]
        i += 1
        if e["t"] == "CommandParameterAst":
            n = str(e.get("p") or "").lower()
            if not n or not any(s.startswith(n) for s in _CIM_SCHALTER) or len(n) < 2:
                return f"Get-CimInstance -{e.get('p')} steht nicht auf der Liste"
            if "classname".startswith(n):
                if i >= len(elemente):
                    return "Get-CimInstance ohne Klasse"
                klasse = str(elemente[i].get("v") or "").lower()
                i += 1
            elif not e.get("a"):
                i += 1                                  # Wert von -Property oder -Filter
        elif klasse is None:
            klasse = str(e.get("v") or "").lower()
        else:
            return "Get-CimInstance mit weiterem Argument"
    return None if klasse in CIM_KLASSEN else f"Klasse {klasse or '?'} steht nicht auf der Liste"


def _nativ_grund(name: str, args: list[str]) -> str | None:
    if name in VERSION_PROGRAMME and [a.lower() for a in args] == ["--version"]:
        return None
    if name == "git":
        if not args or args[0].lower() not in GIT_LESEN:
            return f"git {args[0] if args else ''} steht nicht auf der Liste".strip()
        for a in args[1:]:
            if _GIT_NIE.match(a) or not _GIT_ARG.fullmatch(a):
                return f"git-Argument {a} ist nicht frei"
            if args[0].lower() == "branch" and not _GIT_BRANCH.fullmatch(a):
                return "git branch legt mit einem Namen einen Zweig an"
        return None
    muster = _NATIV.get(name)
    if muster is None:
        return f"{name} steht nur mit --version auf der Liste"
    if not args:
        return None
    for a in args:
        if not muster.fullmatch(a):
            return f"{name} {a} ist nicht frei"
    return None


def _befehl_grund(k: dict) -> str | None:
    if k.get("op") not in (None, "", "Unknown"):
        return "startet über & oder ."
    if k.get("r"):
        return "leitet um"
    elemente = k.get("e") or []
    if not elemente or elemente[0].get("t") != "StringConstantExpressionAst":
        return "der Befehlsname steht nicht im Klartext da"
    name = str(elemente[0].get("v") or "").lower()
    rest = elemente[1:]
    if name in ABFRAGEN or name in ORDNER_ALIASE or name in FILTER:
        for e in rest:
            if e["t"] == "CommandParameterAst" and not _schalter_ok(str(e.get("p") or "")):
                return f"-{e.get('p')} ist nicht frei"
        if name == "get-ciminstance":
            return _cim_grund(rest)
        return None
    if name in _NATIV or name in VERSION_PROGRAMME:
        # Argumente aus dem Baum, nicht aus dem Rohtext: eine Komma-Liste gibt PowerShell als mehrere Argumente
        # weiter. Frei ist sie nur als Feldliste von nvidia-smi --query-…, deren Teile reine Feldnamen sind.
        for e in rest:
            if e["t"] == "ArrayLiteralAst" and name == "nvidia-smi" and _NV_FELDER.fullmatch(str(e.get("x") or "")):
                continue
            if e["t"] not in ("StringConstantExpressionAst", "CommandParameterAst"):
                return f"{name}: ein Argument ist kein einfacher Text"
        return _nativ_grund(name, [str(e.get("x") or "") for e in rest])
    return f"{name} steht nicht auf der Liste"


def _ordner_grund(knoten: list[dict]) -> str | None:
    """Bei Ordner-Listen jeden Text im ganzen Baum prüfen – auch Werte hinter `-Path:`, in Klammern
    oder per Pipeline übergeben – damit nur Dateisystem-Pfade auf diesem Rechner gelesen werden."""
    def name(k: dict) -> str:
        e = k.get("e") or []
        return str(e[0].get("v") or "").lower() if e else ""
    if not any(k.get("t") == "CommandAst" and (name(k) in ORDNER_ALIASE or name(k) == "get-childitem")
               for k in knoten):
        return None
    for k in knoten:
        if k.get("t") == "StringConstantExpressionAst":
            text = str(k.get("x") or "").strip().strip("'\"").strip()
            if _ANBIETER.search(text):
                return f"{k.get('x')} ist kein normaler Ordner"
    return None


def _knoten_grund(k: dict) -> str | None:
    t = k.get("t")
    if t not in _KNOTEN:
        return f"{t} ist in einer Abfrage nicht vorgesehen"
    if t == "CommandAst":
        return _befehl_grund(k)
    if t == "VariableExpressionAst" and str(k.get("v") or "").lower() not in _VARIABLEN:
        return f"Variable ${k.get('v')} ist nicht frei"
    if t == "InvokeMemberExpressionAst":
        m = str(k.get("m") or "").lower()
        if not (m == "tostring" or (k.get("s") and m in _MATH and str(k.get("z") or "").lower() in _MATH_TYP)):
            return f"die Methode {k.get('m')} ist nicht frei"
    if t == "UnaryExpressionAst" and k.get("k") not in _UNAER:
        return "ändert einen Wert"
    if t == "TypeExpressionAst" and str(k.get("x") or "").lower() not in _MATH_TYP:
        return f"der Typ {k.get('x')} ist nicht frei"
    if t == "TypeConstraintAst" and str(k.get("x") or "").lower() not in _TYPEN_WERT:
        return f"der Typ {k.get('x')} ist nicht frei"
    return None


@lru_cache(maxsize=256)
def grund(cmd: str) -> str | None:
    """None, wenn der Befehl frei läuft; sonst warum nicht."""
    cmd = (cmd or "").strip()
    if not cmd or len(cmd) > MAX_LAENGE:
        return "leer oder zu lang"
    if not _bekannter_anfang(cmd):
        return "der Befehl steht nicht auf der Liste"
    baum = _baum(cmd)
    if baum is None:
        return "der Parser hat nicht geantwortet"
    if baum.get("f") or baum.get("s") != 1:
        return "kein einzelner, sauberer Befehl"
    knoten = baum.get("n") or []
    for k in knoten:
        if (g := _knoten_grund(k)):
            return g
    return _ordner_grund(knoten)


def frei(cmd: str) -> bool:
    return grund(cmd) is None


def liste_text() -> str:
    """Für /whitelist."""
    return ("Abfragen: " + ", ".join(sorted(ABFRAGEN)) + "\n\nFiltern: " + ", ".join(sorted(FILTER))
            + "\n\nProgramme: " + ", ".join(sorted(_NATIV)) + "\n\nGit: "
            + ", ".join(f"git {g}" for g in GIT_LESEN) + "\n\nVersion: "
            + ", ".join(f"{p} --version" for p in VERSION_PROGRAMME)
            + "\n\nGet-CimInstance-Klassen: " + ", ".join(sorted(CIM_KLASSEN)))
