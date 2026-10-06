"""
venvpflege.py - Das venv neben NemiCLI aktuell halten (/update).

Geprüft wird gegen requirements.txt (ohne torch): fehlt ein Paket, passt eine Version nicht zur Angabe, gibt es eine neuere
erlaubte Version? torch wird nur repariert (falscher Bau für die Grafikkarte), nie einfach
hochgezogen – der CUDA-Bau muss zur Karte passen; ein fehlendes torch holt /einrichten.

Dazu bekannte Sicherheitslücken und Schadpakete aller installierten Pakete (luecken.py, OSV.dev):
behoben wird mit der ersten Version, die die Lücke schließt – bei Paketen von NemiCLI nur
innerhalb der erlaubten Versionen, sonst wird es gemeldet. Schadpakete werden entfernt.
Nach dem Installieren prüft ein Import-Test, ob die Pakete noch laden; wenn nicht, kommt die
alte Version zurück.

Dateien, die dieser Prozess gerade geladen hat, kann Windows nicht ersetzen. Solche Pakete
kommen in AUSSTEHEND und werden beim nächsten Start eingespielt, bevor etwas aus dem venv
geladen ist (main.py ganz oben).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import uvsetup

AUSSTEHEND = uvsetup.RUNTIME / "update_ausstehend.txt"
# Früher von NemiCLI installiert, heute nicht mehr gebraucht (SD-Pipeline, transformers).
ALTLASTEN = ("transformers", "diffusers", "accelerate")
_TORCH_FAMILIE = {"torch", "torchvision", "torchaudio", "torch-directml"}


@dataclass
class Eintrag:
    name: str
    installiert: str      # leer = fehlt
    neu: str              # Zielversion bei Updates, sonst leer
    spez: str             # Angabe aus requirements.txt bzw. den Bild-Bausteinen
    grund: str            # "fehlt" · "passt nicht" · "update" · "torch-bau" · "luecke" · "luecke-offen" ·
    #                       "schadcode" · "ueberfluessig"
    luecken: list = field(default_factory=list)      # luecken.Luecke
    hinweis: str = ""

    @property
    def noetig(self) -> bool:
        if self.grund == "ueberfluessig":
            return bool(self.luecken)              # mit Lücken: entfernen ist nötig
        return self.grund != "update"

    @property
    def entfernen(self) -> bool:
        return self.grund in ("schadcode", "ueberfluessig")

    @property
    def machbar(self) -> bool:
        """luecke-offen: keine Behebung, die NemiCLI einspielen darf – nur Meldung."""
        return self.grund != "luecke-offen"

    @property
    def installieren_als(self) -> str:
        return f"{self.name}=={self.neu}" if self.grund == "update" else self.spez


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def site_packages() -> Optional[Path]:
    py = uvsetup.venv_python()
    return Path(py).parent.parent / "Lib" / "site-packages" if py else None


def installiert(site: Path) -> dict[str, str]:
    """{Paketname: Version} aus den dist-info-Ordnern."""
    raus = {}
    for d in site.glob("*.dist-info"):
        name, _, version = d.name[:-len(".dist-info")].partition("-")
        if version:
            raus[norm(name)] = version
    return raus


def _wuensche(inst: dict[str, str]) -> dict[str, object]:
    """{Paketname: Requirement} aus requirements.txt (ohne torch); im Quelltext-Modus
    zusätzlich requirements-dev.txt (Pakete für die Tests)."""
    from packaging.requirements import Requirement
    import wizard
    specs = list(wizard.requirements_ohne_torch())
    specs += _dev_wuensche()
    raus: dict[str, object] = {}
    for spec in specs:
        try:
            r = Requirement(spec)
        except Exception:
            continue
        if r.marker is not None and not r.marker.evaluate():
            continue
        n = norm(r.name)
        if n in _TORCH_FAMILIE:
            continue
        if n in raus:
            raus[n].specifier &= r.specifier          # zwei Angaben zum selben Paket: beide gelten
        else:
            raus[n] = r
    return raus


def _dev_wuensche() -> list[str]:
    if getattr(sys, "frozen", False):
        return []
    try:
        from paths import INSTALL
        zeilen = (Path(INSTALL) / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    return [z.split("#", 1)[0].strip() for z in zeilen if z.split("#", 1)[0].strip() and not z.lstrip().startswith("-")]


def _gebraucht_von(site: Path) -> set[str]:
    """Pakete, die ein installiertes Paket verlangt (Requires-Dist ohne Extras)."""
    from packaging.requirements import Requirement
    raus: set[str] = set()
    for d in site.glob("*.dist-info"):
        eigener = norm(d.name.partition("-")[0])
        if eigener in ALTLASTEN:
            continue                                 # Altlasten halten sich nicht gegenseitig
        try:
            zeilen = (d / "METADATA").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for z in zeilen:
            if not z.startswith("Requires-Dist:"):
                continue
            try:
                r = Requirement(z.split(":", 1)[1].strip())
            except Exception:
                continue
            if r.marker is not None and "extra" in str(r.marker):
                continue
            if r.marker is None or r.marker.evaluate():
                raus.add(norm(r.name))
    return raus


def _ueberfluessig(site: Path, inst: dict[str, str], wuensche: dict) -> list[Eintrag]:
    gebraucht = _gebraucht_von(site)
    return [Eintrag(n, inst[n], "", n, "ueberfluessig", hinweis="wird nicht mehr gebraucht")
            for n in ALTLASTEN if n in inst and n not in wuensche and n not in gebraucht]


def _pip(befehl: str) -> list[str]:
    """Aufruf für `pip <befehl>` im venv: uv, wenn da (venvs von /einrichten haben kein pip),
    sonst pip des venv selbst."""
    py = str(uvsetup.venv_python())
    if uvsetup.UV_EXE.is_file():
        return [str(uvsetup.UV_EXE), "pip", befehl, "--python", py]
    return [py, "-m", "pip", befehl, "--disable-pip-version-check"]


def _ausfuehren(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=uvsetup._env(),
                          creationflags=uvsetup._NOWIN)


def _veraltet() -> list[dict]:
    """[{name, version, latest_version}] – leer, wenn PyPI nicht erreichbar ist."""
    try:
        r = _ausfuehren(_pip("list") + ["--outdated", "--format", "json"], 300)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
    except Exception:
        return []


def _torch_eintrag(inst: dict[str, str]) -> Optional[Eintrag]:
    """Nur reparieren: ein fehlendes torch (~2,5 GB) installiert /einrichten."""
    import wizard
    import setup as S
    if "torch" not in inst:
        return None
    if S.gpu_info().get("vendor") == "nvidia" and wizard._torch_bau() in ("", "cpu"):
        return Eintrag("torch", inst["torch"], "", "torch", "torch-bau")
    return None


STAND = {"luecken": ""}          # nach pruefen(): "" geprüft, sonst der Grund, warum nicht


def _luecken_holen(inst: dict[str, str]) -> dict:
    import luecken
    return luecken.pruefen(inst)


def _luecken_einarbeiten(raus: list[Eintrag], inst: dict[str, str], wuensche: dict, funde: dict) -> None:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    nach_name = {e.name: e for e in raus}
    for n, liste in sorted(funde.items()):
        n = norm(n)
        alt = nach_name.get(n)
        if alt is not None and alt.grund == "ueberfluessig" and not any(l.schadcode for l in liste):
            alt.luecken = liste
            alt.hinweis = "wird nicht mehr gebraucht und hat bekannte Lücken – entfernen schließt sie"
            continue
        if any(l.schadcode for l in liste):
            e = Eintrag(n, inst.get(n, ""), "", n, "schadcode", liste, "als schädlich gemeldet – wird entfernt")
            if alt is not None:
                raus.remove(alt)
            raus.append(e)
            nach_name[n] = e
            continue
        fixe = [l.behoben for l in liste if l.behoben]
        ziel = str(max(fixe, key=Version)) if fixe else ""
        erlaubt = wuensche[n].specifier if n in wuensche else SpecifierSet()
        name = wuensche[n].name if n in wuensche else n
        if ziel and (alt is not None and alt.neu and Version(alt.neu) >= Version(ziel)):
            alt.grund, alt.spez, alt.luecken = "luecke", f"{name}=={alt.neu}", liste
            continue
        offen = [l for l in liste if not l.behoben]
        if ziel and _erreichbar(erlaubt, ziel):
            e = Eintrag(n, inst.get(n, ""), "", f"{name}{erlaubt & SpecifierSet('>=' + ziel)}", "luecke", liste,
                        f"{len(offen)} davon noch ohne Behebung" if offen else "")
        else:
            grund = (f"Behebung erst ab {ziel}, NemiCLI erlaubt {erlaubt}" if ziel
                     else "noch keine Version, die das behebt")
            e = Eintrag(n, inst.get(n, ""), "", "", "luecke-offen", liste, grund)
        if alt is not None:
            if alt.machbar and e.grund == "luecke-offen":      # Reparatur bleibt, Lücke als Hinweis dazu
                alt.luecken, alt.hinweis = liste, e.hinweis
                continue
            raus.remove(alt)
        raus.append(e)
        nach_name[n] = e


def _erreichbar(erlaubt, ziel: str) -> bool:
    """Lässt die Angabe eine Version ab `ziel` zu? Nein, wenn eine Grenze darunter liegt (<5, ==4.1, ~=4.2)."""
    from packaging.version import InvalidVersion, Version
    z = Version(ziel)
    for s in erlaubt:
        if s.operator in ("!=", "==="):
            continue
        try:
            basis = Version(s.version.replace(".*", ""))
        except InvalidVersion:
            continue
        if z >= basis and not s.contains(ziel, prereleases=True):
            return False
    return True


def pruefen(veraltet: Optional[Callable[[], list[dict]]] = None,
            luecken_holen: Optional[Callable[[dict], dict]] = None) -> list[Eintrag]:
    """Was im venv zu tun ist: Nötiges (fehlt, passt nicht, torch, Lücken, Schadpakete) und Updates."""
    veraltet = veraltet or _veraltet
    luecken_holen = luecken_holen or _luecken_holen
    site = site_packages()
    if site is None or not site.is_dir():
        return []
    inst = installiert(site)
    wuensche = _wuensche(inst)
    raus: list[Eintrag] = []
    if (t := _torch_eintrag(inst)) is not None:
        raus.append(t)
    for n, r in wuensche.items():
        spez = f"{r.name}{r.specifier}"
        if n not in inst:
            raus.append(Eintrag(n, "", "", spez, "fehlt"))
        elif r.specifier and not r.specifier.contains(inst[n], prereleases=True):
            raus.append(Eintrag(n, inst[n], "", spez, "passt nicht"))
    erledigt = {e.name for e in raus}
    for v in veraltet():
        n = norm(str(v.get("name", "")))
        neu = str(v.get("latest_version", ""))
        if n in erledigt or n not in wuensche or not neu:
            continue
        if wuensche[n].specifier.contains(neu, prereleases=True):
            raus.append(Eintrag(n, inst.get(n, str(v.get("version", ""))), neu, f"{wuensche[n].name}{wuensche[n].specifier}",
                                "update"))
    raus += _ueberfluessig(site, inst, wuensche)
    STAND["luecken"] = ""
    try:
        _luecken_einarbeiten(raus, inst, wuensche, luecken_holen(inst))
    except Exception as e:
        STAND["luecken"] = str(e) or "Lücken-Prüfung nicht möglich"
    rang = {"schadcode": 0, "luecke": 1, "torch-bau": 2, "fehlt": 3, "passt nicht": 4, "luecke-offen": 5,
            "ueberfluessig": 6, "update": 7}
    raus.sort(key=lambda e: rang.get(e.grund, 9))
    return raus


def in_gebrauch(name: str, site: Path) -> bool:
    """Hat dieser Prozess eine native Datei (.pyd/.dll) des Pakets geladen?"""
    geladen = set()
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if isinstance(f, str) and f.lower().endswith((".pyd", ".dll")):
            geladen.add(os.path.normcase(os.path.abspath(f)))
    if not geladen:
        return False
    for d in site.glob("*.dist-info"):
        if norm(d.name.partition("-")[0]) != name:
            continue
        try:
            zeilen = (d / "RECORD").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        for z in zeilen:
            pfad = z.split(",", 1)[0]
            if pfad.lower().endswith((".pyd", ".dll")) and \
                    os.path.normcase(os.path.abspath(site / pfad)) in geladen:
                return True
    return False


def _paketname(spez: str) -> str:
    return norm(re.split(r"[\[<>=!~;\s]", spez.strip(), maxsplit=1)[0])


def _mit_basis(ziele: list[str]) -> list[str]:
    """`ziele` plus alle übrigen Pakete von NemiCLI mit ihren Angaben: so prüft der Installer,
    ob alles zusammenpasst, statt ein Update gegen die anderen durchzudrücken."""
    site = site_packages()
    drin = {_paketname(z) for z in ziele}
    basis = _wuensche(installiert(site)) if site else {}
    return ziele + [f"{r.name}{r.specifier}" for n, r in basis.items() if n not in drin]


def _kurz(text: str) -> str:
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    for z in zeilen:
        if "conflict" in z.lower() or "requires" in z.lower() or "unsatisfiable" in z.lower():
            return z[:200]
    return zeilen[-1][:200] if zeilen else "unbekannter Fehler"


def _installieren(eintraege: list[Eintrag], probe: bool = False) -> tuple[list[Eintrag], list[tuple]]:
    """Alle zusammen; klappt das nicht, einzeln – so bleibt nur das stehen, was nicht passt.
    probe=True: nur durchrechnen (--dry-run), nichts ändern."""
    def versuch(teil):
        cmd = _pip("install") + (["--dry-run"] if probe else []) + _mit_basis([e.installieren_als for e in teil])
        r = _ausfuehren(cmd, 3600)
        return r.returncode == 0, (r.stderr or "") + (r.stdout or "")
    ok, text = versuch(eintraege)
    if ok:
        return list(eintraege), []
    if len(eintraege) == 1:
        return [], [(eintraege[0], _kurz(text))]
    gut, schlecht = [], []
    for e in eintraege:
        ok, text = versuch([e])
        if ok:
            gut.append(e)
        else:
            schlecht.append((e, _kurz(text)))
    return gut, schlecht


def _module(name: str, site: Path) -> list[str]:
    """Import-Namen eines Pakets (top_level.txt, sonst aus RECORD)."""
    for d in site.glob("*.dist-info"):
        if norm(d.name.partition("-")[0]) != name:
            continue
        tl = d / "top_level.txt"
        if tl.is_file():
            mods = tl.read_text(encoding="utf-8", errors="replace").split()
        else:
            mods = []
            try:
                zeilen = (d / "RECORD").read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                zeilen = []
            for z in zeilen:
                teile = z.split(",", 1)[0].split("/")
                if len(teile) == 2 and teile[1] == "__init__.py":
                    mods.append(teile[0])
                elif len(teile) == 1 and teile[0].endswith((".py", ".pyd")):
                    mods.append(teile[0].split(".")[0])
        return sorted({m for m in mods if m.isidentifier() and not m.startswith("_")})[:5]
    return []


def _laedt(name: str, site: Path) -> str:
    """Import-Test im venv (eigener Prozess): "" wenn alles lädt, sonst die Fehlerzeile."""
    mods = _module(name, site)
    if not mods:
        return ""
    code = "import importlib, sys\nfor m in sys.argv[1:]:\n    importlib.import_module(m)"
    try:
        r = _ausfuehren([str(uvsetup.venv_python()), "-c", code, *mods], 600)
    except Exception as e:
        return str(e)
    return "" if r.returncode == 0 else _kurz((r.stderr or "") + (r.stdout or ""))


def einspielen(auswahl: list[Eintrag], on_status=None) -> dict:
    """Installiert, was frei ist, entfernt Schadpakete, prüft danach mit einem Import-Test und
    holt bei Bruch die alte Version zurück; Gesperrtes wandert (geprüft) nach AUSSTEHEND.
    Rückgabe: {"jetzt", "spaeter", "neustart", "entfernt": [Namen],
               "abgelehnt", "zurueck": [(Name, Grund)]}."""
    import wizard
    site = site_packages()
    jetzt, spaeter, neustart = [], [], []
    for e in auswahl:
        if not e.machbar:
            continue
        if site is not None and e.installiert and in_gebrauch(e.name, site):
            (neustart if e.name in _TORCH_FAMILIE or e.entfernen else spaeter).append(e)
        else:
            jetzt.append(e)
    weg = [e for e in jetzt if e.entfernen]
    torch = [e for e in jetzt if e.name in _TORCH_FAMILIE]
    rest = [e for e in jetzt if e.name not in _TORCH_FAMILIE and not e.entfernen]
    abgelehnt: list[tuple] = []
    entfernt = []
    for e in weg:
        if on_status:
            on_status(f"entferne {e.name} …")
        r = _ausfuehren(_pip("uninstall") + ([] if uvsetup.UV_EXE.is_file() else ["-y"]) + [e.name], 600)
        (entfernt.append(e) if r.returncode == 0 else abgelehnt.append((e, _kurz(r.stderr or r.stdout))))
    vorher = {e.name: _laedt(e.name, site) for e in torch + rest if e.installiert} if site else {}
    if torch:
        wizard.install_torch(on_status)
    if rest:
        if on_status:
            on_status(f"installiere {len(rest)} Paket(e) – prüfe, ob alles zusammenpasst …")
        rest, nein = _installieren(rest)
        abgelehnt += nein
    zurueck = []
    if site is not None:
        if on_status and (torch or rest):
            on_status("Funktionsprüfung: lädt alles noch? …")
        for e in torch + rest:
            fehler = _laedt(e.name, site)
            if fehler and e.installiert and not vorher.get(e.name):
                _ausfuehren(_pip("install") + _mit_basis([f"{e.name}=={e.installiert}"]), 3600)
                zurueck.append((e, fehler))
    if spaeter:
        if on_status:
            on_status("prüfe die Pakete für den nächsten Start …")
        spaeter, nein = _installieren(spaeter, probe=True)
        abgelehnt += nein
    if spaeter:
        alt = AUSSTEHEND.read_text(encoding="utf-8").splitlines() if AUSSTEHEND.is_file() else []
        neu = _mit_basis([e.installieren_als for e in spaeter])
        namen = {_paketname(s) for s in neu}
        alt = [s for s in alt if s.strip() and _paketname(s) not in namen]
        AUSSTEHEND.parent.mkdir(parents=True, exist_ok=True)
        AUSSTEHEND.write_text("\n".join(alt + neu) + "\n", encoding="utf-8")
    zurueck_namen = {e.name for e, _ in zurueck}
    return {"jetzt": [e.name for e in torch + rest if e.name not in zurueck_namen],
            "spaeter": [e.name for e in spaeter], "neustart": [e.name for e in neustart],
            "entfernt": [e.name for e in entfernt if e.grund == "schadcode"],
            "aufgeraeumt": [e.name for e in entfernt if e.grund == "ueberfluessig"],
            "abgelehnt": [(e.name, g) for e, g in abgelehnt], "zurueck": [(e.name, f) for e, f in zurueck]}


def ausstehende_einspielen(melden: Callable[[str], None] = print) -> None:
    """Beim Start, bevor etwas aus dem venv geladen ist: Zurückgestelltes installieren."""
    if not AUSSTEHEND.is_file():
        return
    if not uvsetup.venv_python():
        return
    melden("📦 Zurückgestellte Updates aus /update werden eingespielt …")
    try:
        ok = _ausfuehren(_pip("install") + ["-r", str(AUSSTEHEND)], 3600).returncode == 0
    except Exception:
        ok = False
    if ok:
        AUSSTEHEND.unlink(missing_ok=True)
        melden("📦 Fertig.")
    else:
        melden("📦 Hat nicht geklappt – läuft die Wache? /update versucht es erneut.")
