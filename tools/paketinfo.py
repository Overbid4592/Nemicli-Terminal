"""
paketinfo.py - Werkzeug paket_info: neueste Version eines Python-Pakets, die installierte im Projekt
und bekannte Lücken der installierten (OSV.dev, siehe luecken.py).

Quelle der Neuigkeiten: die PyPI-JSON-Schnittstelle (pypi.org/pypi/<name>/json).
"""
from __future__ import annotations

import re
from pathlib import Path

PYPI = "https://pypi.org/pypi/{}/json"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_VERSION = r"""
import sys, importlib.metadata as m
try:
    print(m.version(sys.stdin.read().strip()))
except Exception:
    print("")
"""


def _pypi(name: str) -> dict:
    import httpx
    with httpx.Client(headers={"User-Agent": "NemiCLI-Paketinfo"}, timeout=20.0, follow_redirects=True) as c:
        r = c.get(PYPI.format(name))
        if r.status_code == 404:
            raise LookupError(f"'{name}' gibt es auf PyPI nicht.")
        r.raise_for_status()
        return r.json()


def installiert(name: str, ordner: Path | None) -> str:
    """Version im Projekt-Python (leer: nicht installiert oder unbekannt)."""
    import pyumgebung
    python, _ = pyumgebung.python_fuer(ordner)
    if python is None:
        return ""
    ok, aus = pyumgebung._lauf(python, _VERSION, name, ordner)
    return aus.strip() if ok else ""


def info(name: str, ordner: Path | None, *, pypi=None, pruefen=None) -> tuple[str, bool]:
    name = str(name or "").strip()
    if not _NAME.match(name):
        return "Paketname wie 'requests' oder 'python-dotenv' angeben.", False
    try:
        daten = (pypi or _pypi)(name)
    except LookupError as e:
        return str(e), False
    except Exception as e:
        return f"PyPI nicht erreichbar: {e}", False
    i = daten.get("info") or {}
    neu = str(i.get("version") or "?")
    datum = ""
    for datei in (daten.get("releases") or {}).get(neu) or []:
        datum = str(datei.get("upload_time") or "")[:10]
        break
    hier = installiert(name, ordner)
    zeilen = [f"{i.get('name') or name}: neueste Version {neu}" + (f" ({datum})" if datum else "")
              + (f" · braucht Python {i['requires_python']}" if i.get("requires_python") else "")]
    if not hier:
        zeilen.append("Im Projekt nicht installiert.")
    elif hier == neu:
        zeilen.append(f"Installiert: {hier} – aktuell.")
    else:
        zeilen.append(f"Installiert: {hier} – es gibt {neu}. Vor einem Wechsel den Changelog lesen: "
                      "Funktionen können sich geändert haben.")
    if hier:
        try:
            import luecken
            funde = (pruefen or luecken.pruefen)({name: hier}).get(name, [])
        except OSError as e:
            funde, zeilen = [], zeilen + [f"Lücken-Prüfung nicht möglich: {e}"]
        if funde:
            zeilen.append(f"⚠️ Bekannte Lücken in {hier}:")
            zeilen += [f"  • {l.kennung} ({l.schwere})" + (f" – behoben in {l.behoben}" if l.behoben else " – noch ohne Behebung")
                       for l in funde[:8]]
        elif not any(z.startswith("Lücken-Prüfung") for z in zeilen):
            zeilen.append(f"Keine bekannten Lücken in {hier} (OSV.dev).")
    links = i.get("project_urls") or {}
    for schluessel in ("Changelog", "Changes", "Release notes", "Release Notes", "History", "Documentation"):
        if links.get(schluessel):
            zeilen.append(f"{schluessel}: {links[schluessel]}")
            break
    return "\n".join(zeilen), True
