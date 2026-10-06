"""
pyumgebung.py - Was im Projekt wirklich installiert ist (Coding-Assistent).

Modelle lernen mit einem Wissensstand; Bibliotheken ändern sich danach weiter. Dieses Modul
holt die Wahrheit aus der Installation selbst:

  umgebung()        Python-Version und Paket-Versionen des Projekt-Pythons (venv) – für den Prompt
  nachschlagen()    Signatur, Doku und Version eines Namens aus der INSTALLIERTEN Bibliothek
  ruff_pruefen()    ruff (E9, F, UP, B): Syntax, undefinierte Namen, veralteter Stil, typische Fehler
  veraltet_aus()    DeprecationWarning/FutureWarning aus einer Programmausgabe

Die Abfragen laufen als fester Python-Code im Projekt-Python (kein PowerShell); der Name kommt
über stdin, nie in den Code. Zeitgrenze je Aufruf.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
import warnings
from pathlib import Path

ZEIT = 30
CACHE_S = 90
MAX_PAKETE_PROMPT = 30
MAX_PY_DATEIEN = 300
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_FREMD = {"venv", ".venv", "env", ".nemicli", "__pycache__", "site-packages", "node_modules", ".git", "build", "dist"}
_cache: dict[str, tuple[float, dict]] = {}


# --- Welches Python ----------------------------------------------------------

def python_fuer(ordner: Path | None) -> tuple[Path | None, str]:
    """(Python, Herkunft): das venv des Projekts, sonst das Python von NemiCLI."""
    if ordner is not None:
        for name in ("venv", ".venv", "env"):
            p = Path(ordner) / name / "Scripts" / "python.exe"
            if p.is_file():
                return p, f"{name}\\Scripts\\python.exe im Projekt"
    try:
        import uvsetup
        p = Path(uvsetup.venv_python())
        if p.is_file():
            return p, "Python von NemiCLI (kein venv im Projekt)"
    except Exception:
        pass
    if not getattr(sys, "frozen", False):
        return Path(sys.executable), "Python von NemiCLI (kein venv im Projekt)"
    return None, ""


def _lauf(python: Path, skript: str, eingabe: str, cwd: Path | None) -> tuple[bool, str]:
    """Fester Code im Projekt-Python. -I (isoliert): weder der Projektordner noch PYTHON*-Variablen
    kommen in den Suchpfad – eine Projektdatei wie json.py wird nie statt der Bibliothek geladen."""
    try:
        r = subprocess.run([str(python), "-I", "-X", "utf8", "-c", skript], input=eingabe, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=ZEIT,
                           cwd=str(cwd) if cwd else None,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, f"Zeitgrenze ({ZEIT} s) überschritten."
    except OSError as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "").strip()[-1500:]
    return True, r.stdout


# --- Versions-Blick ----------------------------------------------------------

_UMGEBUNG = r"""
import sys, json, importlib.metadata as m
namen = json.loads(sys.stdin.read() or "[]")
pak = {}
for d in m.distributions():
    n = d.metadata.get("Name")
    if n:
        pak[n] = d.version
try:
    zu = m.packages_distributions()
except Exception:
    zu = {}
print(json.dumps({"python": sys.version.split()[0], "pakete": pak,
                  "importe": {i: zu.get(i, []) for i in namen},
                  "stdlib": sorted(set(namen) & set(getattr(sys, "stdlib_module_names", ())))}))
"""


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def verlangte_pakete(ordner: Path) -> list[str]:
    """Paketnamen aus requirements*.txt und pyproject.toml ([project] dependencies)."""
    namen: list[str] = []
    for datei in sorted(Path(ordner).glob("requirements*.txt")):
        try:
            zeilen = datei.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for z in zeilen:
            z = z.split("#", 1)[0].strip()
            if z and not z.startswith("-") and (m := re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", z)):
                namen.append(m.group(0))
    pyproject = Path(ordner) / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
            daten = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            for z in daten.get("project", {}).get("dependencies", []) or []:
                if m := re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(z)):
                    namen.append(m.group(0))
        except Exception:
            pass
    return list(dict.fromkeys(namen))


def importe(ordner: Path) -> list[str]:
    """Oberste Modulnamen aus den .py-Dateien des Projekts (ohne venv & Co., ohne eigene Module)."""
    import os
    gefunden: set[str] = set()
    eigene: set[str] = set()
    dateien: list[Path] = []
    for wurzel, unter, namen in os.walk(ordner):
        unter[:] = [u for u in unter if u not in _FREMD]          # venv & Co. gar nicht erst betreten
        dateien += [Path(wurzel) / n for n in namen if n.endswith(".py")]
        if len(dateien) >= MAX_PY_DATEIEN:
            break
    for p in dateien[:MAX_PY_DATEIEN]:
        eigene.add(p.stem)
        try:
            with warnings.catch_warnings():          # fremder Code: SyntaxWarnings sind nicht unsere
                warnings.simplefilter("ignore")
                baum = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError, ValueError):
            continue
        for k in ast.walk(baum):
            if isinstance(k, ast.Import):
                gefunden.update(a.name.split(".")[0] for a in k.names)
            elif isinstance(k, ast.ImportFrom) and k.module and not k.level:
                gefunden.add(k.module.split(".")[0])
    eigene |= {p.name for p in Path(ordner).iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    return sorted(gefunden - eigene)


def umgebung(ordner: Path, *, frisch: bool = False) -> dict:
    """{python, herkunft, relevant: {Paket: Version}, fehlt: [Paket], fehler}. 90 s zwischengespeichert."""
    schluessel = str(Path(ordner).resolve())
    if not frisch and (alt := _cache.get(schluessel)) and time.time() - alt[0] < CACHE_S:
        return alt[1]
    python, herkunft = python_fuer(ordner)
    if python is None:
        return {"fehler": "Kein Python gefunden."}
    verlangt, imp = verlangte_pakete(ordner), importe(ordner)
    ok, aus = _lauf(python, _UMGEBUNG, json.dumps(imp), ordner)
    if not ok:
        ergebnis = {"fehler": f"Abfrage im Projekt-Python fehlgeschlagen: {aus}", "herkunft": herkunft}
    else:
        d = json.loads(aus.strip().splitlines()[-1])
        installiert = {_norm(n): (n, v) for n, v in d["pakete"].items()}
        relevant, fehlt = {}, []
        for n in verlangt:
            treffer = installiert.get(_norm(n))
            if treffer:
                relevant[treffer[0]] = treffer[1]
            else:
                fehlt.append(n)
        for modul, dists in d["importe"].items():
            for dist in dists:
                if (t := installiert.get(_norm(dist))):
                    relevant[t[0]] = t[1]
            if not dists and modul not in d["stdlib"] and _norm(modul) not in installiert:
                fehlt.append(modul)
        ergebnis = {"python": d["python"], "herkunft": herkunft, "relevant": relevant,
                    "fehlt": sorted(set(fehlt), key=str.lower)}
    _cache[schluessel] = (time.time(), ergebnis)
    return ergebnis


def vergessen() -> None:
    """Nach pip install & Co.: beim nächsten Mal neu abfragen."""
    _cache.clear()


def umgebung_text(ordner: Path) -> str:
    u = umgebung(ordner)
    if u.get("fehler"):
        return f"Umgebung unbekannt: {u['fehler']}"
    pakete = sorted(u["relevant"].items(), key=lambda kv: kv[0].lower())
    zeile = ", ".join(f"{n} {v}" for n, v in pakete[:MAX_PAKETE_PROMPT])
    if len(pakete) > MAX_PAKETE_PROMPT:
        zeile += f" … (+{len(pakete) - MAX_PAKETE_PROMPT})"
    text = f"Python {u['python']} · {u['herkunft']}\nInstalliert und benutzt: {zeile or '–'}"
    if u["fehlt"]:
        text += f"\nVerlangt/importiert, aber NICHT installiert: {', '.join(u['fehlt'][:20])}"
    return text


def ziel_version(ordner: Path | None) -> str:
    """'py312' für ruff aus der Python-Version des Projekts; leer, wenn unbekannt."""
    if ordner is None:
        return ""
    v = umgebung(ordner).get("python") or ""
    m = re.match(r"3\.(\d+)", v)
    return f"py3{m.group(1)}" if m else ""


# --- Nachschlagen in der Installation ----------------------------------------

_NACHSCHLAGEN = r"""
import sys, json, importlib, inspect, difflib, re, warnings
name = sys.stdin.read().strip()
teile = name.split(".")
obj, fehler, i, gemeldet = None, "", 0, []
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    for i in range(len(teile), 0, -1):
        try:
            obj = importlib.import_module(".".join(teile[:i]))
            break
        except Exception as e:
            fehler = fehler or f"{type(e).__name__}: {e}"
    if obj is None:
        print(json.dumps({"fehler": fehler or "nicht importierbar"}))
        sys.exit()
    weg = teile[:i]
    for t in teile[i:]:
        if not hasattr(obj, t):
            namen = [n for n in dir(obj) if not n.startswith("_")]
            print(json.dumps({"fehler": f"'{'.'.join(weg)}' hat kein '{t}'",
                              "vorschlaege": difflib.get_close_matches(t, namen, n=6)}))
            sys.exit()
        obj = getattr(obj, t)
        weg.append(t)
    gemeldet = [str(x.message) for x in w if issubclass(x.category, (DeprecationWarning, FutureWarning))]
out = {"name": ".".join(weg), "art": type(obj).__name__, "warnungen": gemeldet[:5]}
try:
    out["signatur"] = str(inspect.signature(obj))
except Exception:
    pass
doku = inspect.getdoc(obj) or ""
out["doku"] = doku[:3000] + (" …" if len(doku) > 3000 else "")
out["veraltet"] = bool(re.search(r"deprecated|will be removed|no longer supported", doku, re.I))
if inspect.ismodule(obj) or inspect.isclass(obj):
    out["mitglieder"] = [n for n in dir(obj) if not n.startswith("_")][:80]
try:
    out["datei"] = inspect.getsourcefile(obj) or ""
except Exception:
    out["datei"] = ""
top = teile[0]
if top in getattr(sys, "stdlib_module_names", ()):
    out["version"] = f"Standardbibliothek von Python {sys.version.split()[0]}"
else:
    try:
        import importlib.metadata as m
        dist = (m.packages_distributions().get(top) or [top])[0]
        out["version"] = f"{dist} {m.version(dist)}"
    except Exception:
        out["version"] = getattr(sys.modules.get(top), "__version__", "") or "unbekannt"
print(json.dumps(out))
"""


def nachschlagen(name: str, ordner: Path | None) -> tuple[str, bool]:
    """Signatur, Doku, Version eines Namens aus dem Projekt-Python. (Text, ok)."""
    name = str(name or "").strip()
    if not _NAME.match(name) or len(name) > 200:
        return "Name wie 'requests.get', 'pathlib.Path.glob' oder 'numpy' angeben (nur Buchstaben, Ziffern, _ und .).", False
    python, herkunft = python_fuer(ordner)
    if python is None:
        return "Kein Python gefunden.", False
    ok, aus = _lauf(python, _NACHSCHLAGEN, name, ordner)
    if not ok:
        return f"Nachschlagen fehlgeschlagen: {aus}", False
    try:
        d = json.loads(aus.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return f"Unerwartete Antwort: {aus[-500:]}", False
    if d.get("fehler"):
        text = f"{name}: {d['fehler']} ({herkunft})."
        if d.get("vorschlaege"):
            text += " Meintest du: " + ", ".join(d["vorschlaege"]) + "?"
        return text, False
    zeilen = [f"{d['name']}  ·  {d['art']}  ·  {d['version']}  ·  {herkunft}"]
    if d.get("signatur"):
        zeilen.append(f"Signatur: {d['name'].split('.')[-1]}{d['signatur']}")
    if d.get("veraltet") or d.get("warnungen"):
        zeilen.append("⚠️ Veraltet: " + ("; ".join(d.get("warnungen") or []) or "die Doku nennt es veraltet – Ersatz in der Doku unten."))
    if d.get("mitglieder"):
        zeilen.append("Mitglieder: " + ", ".join(d["mitglieder"]))
    if d.get("doku"):
        zeilen.append("Doku:\n" + d["doku"])
    if d.get("datei"):
        zeilen.append(f"Quelle: {d['datei']}")
    return "\n".join(zeilen), True


# --- ruff ---------------------------------------------------------------------

REGELN = "E9,F,UP,B"


def ruff_bin() -> str | None:
    try:
        import extlibs
        extlibs.enable()
    except Exception:
        pass
    try:
        from ruff.__main__ import find_ruff_bin
        return str(find_ruff_bin())
    except Exception:
        import shutil
        return shutil.which("ruff")


def ruff_pruefen(ziele: list[Path], cwd: Path | None = None, regeln: str = REGELN,
                 zielversion: str = "", inhalt: str | None = None) -> tuple[str, int] | None:
    """(Text, Anzahl Funde) – None, wenn ruff fehlt. Mit `inhalt`: genau dieser Text als ziele[0]."""
    ruff = ruff_bin()
    if not ruff:
        return None
    befehl = [ruff, "check", "--isolated", "--no-cache", "--output-format", "concise", "--select", regeln]
    if zielversion:
        befehl += ["--target-version", zielversion]
    befehl += ["--stdin-filename", str(ziele[0]), "-"] if inhalt is not None else [str(z) for z in ziele]
    try:
        r = subprocess.run(befehl, input=inhalt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=ZEIT, cwd=str(cwd) if cwd else None,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"ruff lief nicht: {e}", 0
    funde = [z for z in r.stdout.splitlines() if re.search(r":\d+:\d+: [A-Z]+\d+", z)]
    if cwd:
        funde = [z.replace(str(cwd) + "\\", "") for z in funde]
    if not funde:
        return "ruff: keine Funde ✅", 0
    text = "\n".join(funde[:40]) + (f"\n… (+{len(funde) - 40} weitere)" if len(funde) > 40 else "")
    return text, len(funde)


# --- Veraltetes in Programmausgaben ------------------------------------------

_WARNUNG = re.compile(r"(PendingDeprecationWarning|DeprecationWarning|FutureWarning)\b[: ].*")


def veraltet_aus(text: str) -> list[str]:
    """Eindeutige Warnzeilen über veraltete Aufrufe (höchstens 8)."""
    gesehen = []
    for m in _WARNUNG.finditer(text or ""):
        z = " ".join(m.group(0).split())[:300]
        if z not in gesehen:
            gesehen.append(z)
    return gesehen[:8]


def veraltet_hinweis(text: str) -> str:
    funde = veraltet_aus(text)
    if not funde:
        return ""
    return ("\n\n⚠️ Veraltet gemeldet (läuft noch, bricht aber in einer späteren Version):\n"
            + "\n".join(f"  • {f}" for f in funde)
            + "\nModernisiere diese Stellen – den heutigen Weg zeigt api_nachschlagen bzw. die Meldung selbst. "
              "Merk dir die Änderung (skill_merken), damit du sie nächstes Mal gleich richtig machst.")
