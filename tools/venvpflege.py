"""
venvpflege.py - Das venv neben NemiCLI aktuell halten (/update).

Geprüft wird gegen requirements.txt (ohne torch): fehlt ein Paket, passt eine Version nicht zur Angabe, gibt es eine neuere
erlaubte Version? Ein falscher torch-Bau für die Grafikkarte wird nur gemeldet, samt Befehl.

torch (NVIDIA): Neuere Version oder ein höherer CUDA-Bau, den der Treiber kann, vom PyTorch-Index
(download.pytorch.org) – als freiwilliges Update „torch-update“. Eingespielt wird nur, wenn kein Prozess
torch geladen hat (Probe: DLLs zum Schreiben öffnen); sonst beim nächsten Start (TORCH_AUSSTEHEND).
Danach prüft ein eigener Prozess, ob torch lädt, die Karte sieht, rechnet und FP4 noch kann; wenn nicht,
kommt die alte Fassung zurück.

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
TORCH_AUSSTEHEND = uvsetup.RUNTIME / "torch_ausstehend.json"
TORCH_INDEX = "https://download.pytorch.org/whl/"
TORCH_FRIST_S = 30.0
TORCH_MAX_BYTES = 4 << 20
# Pakete, die NemiCLI nicht mehr braucht (PySide6-Essentials genügt); bleiben, solange ein anderes
# Paket sie verlangt. transformers steht wieder in requirements.txt (Wache-Vorprüfer).
ALTLASTEN = ("diffusers", "accelerate", "opencv-python", "pyside6-webengine", "pyside6-addons")
_TORCH_FAMILIE = {"torch", "torchvision", "torchaudio", "torch-directml"}


@dataclass
class Eintrag:
    name: str
    installiert: str      # leer = fehlt
    neu: str              # Zielversion bei Updates, sonst leer
    spez: str             # Angabe aus requirements.txt bzw. den Bild-Bausteinen
    grund: str            # "fehlt" · "passt nicht" · "update" · "torch-bau" · "torch-update" · "luecke" ·
    #                       "luecke-offen" · "schadcode" · "ueberfluessig"
    luecken: list = field(default_factory=list)      # luecken.Luecke
    hinweis: str = ""
    index: str = ""                                  # torch-update: PyTorch-Index des neuen Baus
    dazu: list = field(default_factory=list)         # torch-update: torchvision/torchaudio, passend mitgeholt

    @property
    def noetig(self) -> bool:
        if self.grund == "ueberfluessig":
            return bool(self.luecken)              # mit Lücken: entfernen ist nötig
        return self.grund not in ("update", "torch-update")

    @property
    def entfernen(self) -> bool:
        return self.grund in ("schadcode", "ueberfluessig")

    @property
    def machbar(self) -> bool:
        """luecke-offen: keine Behebung, die NemiCLI einspielen darf – nur Meldung. torch & Co. nur als
        torch-update vom PyTorch-Index; alles andere daran im venv-Fenster (/venv)."""
        if self.grund == "torch-update":
            return True
        return self.grund != "luecke-offen" and self.name not in _TORCH_FAMILIE

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
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          env=uvsetup._env(),
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
        return Eintrag("torch", inst["torch"], "", "torch", "torch-bau",
                       hinweis=f"CPU-Bau statt CUDA – im venv-Fenster (/venv): {wizard.torch_befehl()}")
    return None


# --- torch vom PyTorch-Index ---------------------------------------------------------------------

def kanal_cuda(kanal: str) -> float:
    """'cu134' -> 13.4; kein CUDA-Kanal -> 0."""
    m = re.fullmatch(r"cu(\d+)(\d)", kanal or "")
    return float(f"{m.group(1)}.{m.group(2)}") if m else 0.0


def torch_kanaele(minimum: str, treiber_cuda: float, hoechstens: int = 12) -> list[str]:
    """CUDA-Kanäle vom Mindest-Bau der Karte bis zur Grenze des Treibers, höchster zuerst."""
    unten = kanal_cuda(minimum)
    if not unten or treiber_cuda < unten:
        return [minimum] if unten else []
    raus, c = [], round(treiber_cuda, 1)
    while c >= unten - 1e-9 and len(raus) < hoechstens:
        raus.append(f"cu{int(round(c * 10))}")
        c = round(c - 0.1, 1)
    return raus


def _torch_get(url: str) -> str:
    """Seite des PyTorch-Index als Text; "" wenn es den Kanal nicht gibt (der Index antwortet dann 403 oder 404)."""
    import netzhilfe
    global _TORCH_CLIENT
    if _TORCH_CLIENT is None:
        _TORCH_CLIENT = netzhilfe.GeteilterClient(headers={"User-Agent": "NemiCLI-Paketpruefung"}, timeout=TORCH_FRIST_S)
    r = netzhilfe.abrufen(_TORCH_CLIENT(), "GET", url, frist_s=TORCH_FRIST_S, max_bytes=TORCH_MAX_BYTES)
    if r.status_code in (403, 404):
        return ""
    return r.raise_for_status().text


_TORCH_CLIENT = None


def torch_versionen(kanal: str, paket: str = "torch", get: Optional[Callable[[str], str]] = None) -> list[str]:
    """Versionen (ohne +cuXYZ) von `paket` im Kanal für dieses Python unter Windows 64 Bit, älteste zuerst."""
    from packaging.version import InvalidVersion, Version
    seite = (get or _torch_get)(f"{TORCH_INDEX}{kanal}/{paket}/")
    tag = "cp" + uvsetup.PYVER.replace(".", "")
    passt = rf"(?:{tag}-{tag}|py3-none|cp3\d+-abi3)"       # torch: cp314-cp314 · torchvision: py3-none
    muster = rf"{re.escape(paket)}-(\d[\w.]*?)(?:%2B|\+){re.escape(kanal)}-{passt}-win_amd64\.whl"
    gueltig = []
    for v in set(re.findall(muster, seite)):
        try:
            if not Version(v).is_prerelease:
                gueltig.append(v)
        except InvalidVersion:
            pass
    return sorted(gueltig, key=Version)


def _torch_update(inst: dict[str, str], get: Optional[Callable[[str], str]] = None,
                  gpu: Optional[dict] = None) -> Optional[Eintrag]:
    """Neuere torch-Version oder höherer CUDA-Bau, den Treiber und Karte können (nur NVIDIA, nur CUDA-Bau)."""
    from packaging.version import Version
    jetzt = inst.get("torch", "")
    basis, _, lokal = jetzt.partition("+")
    if not jetzt or not kanal_cuda(lokal):
        return None                                   # fehlt oder CPU-Bau: meldet _torch_eintrag
    import setup as S
    import syscheck
    gpu = gpu if gpu is not None else S.gpu_info()
    if gpu.get("vendor") != "nvidia":
        return None
    minimum, _ = syscheck._wheel_for(syscheck.compute_cap())
    bestes = None
    for kanal in torch_kanaele(minimum, float(gpu.get("cuda_max") or 0)):
        for v in torch_versionen(kanal, get=get):
            schluessel = (Version(v), kanal_cuda(kanal))
            if bestes is None or schluessel > bestes[0]:
                bestes = (schluessel, kanal, v)
    if bestes is None or bestes[0] <= (Version(basis), kanal_cuda(lokal)):
        return None
    (_, cuda), kanal, v = bestes
    was = (f"CUDA {kanal_cuda(lokal):g} → {cuda:g}" if v == basis else f"{basis} → {v}, CUDA {cuda:g}")
    dazu = []
    for n in ("torchvision", "torchaudio"):
        if n in inst:                                 # gleicher CUDA-Bau wie torch, sonst bliebe der alte stehen
            passend = torch_versionen(kanal, n, get=get)
            dazu.append(f"{n}=={passend[-1]}+{kanal}" if passend else n)
    namen = [_paketname(s) for s in dazu]
    return Eintrag("torch", jetzt, f"{v}+{kanal}", f"torch=={v}+{kanal}", "torch-update",
                   hinweis=f"{was} · rund 3 GB vom PyTorch-Index" + (f" · mit {', '.join(namen)}" if namen else ""),
                   index=f"{TORCH_INDEX}{kanal}", dazu=dazu)


def torch_frei(site: Optional[Path]) -> bool:
    """Hat kein Prozess torch geladen? Geladene DLLs lassen sich unter Windows nicht zum Schreiben öffnen."""
    if site is None:
        return False
    for dll in ("c10.dll", "torch_cpu.dll", "torch_cuda.dll", "torch_python.dll"):
        p = site / "torch" / "lib" / dll
        if not p.is_file():
            continue
        try:
            os.close(os.open(str(p), os.O_RDWR))
        except PermissionError:
            return False
        except OSError:
            continue
    return True


_TORCH_PROBE = (
    "import json, sys\n"
    "import torch, torch.nn.functional as F\n"
    "k = {'laedt': True, 'cuda': torch.cuda.is_available(),\n"
    "     'fp4': hasattr(torch, 'float4_e2m1fn_x2') and hasattr(F, 'scaled_mm')}\n"
    "if k['cuda']:\n"
    "    x = torch.randn(256, 256, device='cuda', dtype=torch.bfloat16)\n"
    "    k['rechnet'] = bool(torch.isfinite((x @ x).float()).all())\n"
    "for m in sys.argv[1:]:\n"
    "    __import__(m)\n"
    "print('TORCHPROBE' + json.dumps(k))\n")


def torch_kann(dazu: list[str] = ()) -> dict:
    """Was torch im venv kann (eigener Prozess): {laedt, cuda, fp4, rechnet}; {} wenn es nicht lädt."""
    try:
        r = _ausfuehren([str(uvsetup.venv_python()), "-c", _TORCH_PROBE, *dazu], 600)
    except Exception:
        return {}
    for z in (r.stdout or "").splitlines():
        if z.startswith("TORCHPROBE"):
            try:
                return json.loads(z[len("TORCHPROBE"):])
            except ValueError:
                return {}
    return {}


def torch_verloren(vorher: dict, nachher: dict) -> list[str]:
    """Fähigkeiten, die vorher da waren und jetzt fehlen."""
    return [k for k in ("laedt", "cuda", "rechnet", "fp4") if vorher.get(k) and not nachher.get(k)]


def _torch_plan(e: Eintrag) -> dict:
    """Was beim Tausch zu tun ist: neue Pakete samt Index, alte Fassung samt Index für den Rückweg."""
    alt_kanal = e.installiert.partition("+")[2]
    site = site_packages()
    inst = installiert(site) if site else {}
    namen = [_paketname(s) for s in e.dazu]
    return {"pakete": [e.spez] + list(e.dazu), "index": e.index,
            "alt": [f"torch=={e.installiert}"] + [f"{n}=={inst[n]}" for n in namen if n in inst],
            "alt_index": f"{TORCH_INDEX}{alt_kanal}", "dazu": [n for n in namen if n != "torchaudio"]}


def torch_tauschen(plan: dict, vorher: Optional[dict] = None, melden: Callable[[str], None] = lambda s: None,
                   kann: Optional[Callable[[list], dict]] = None) -> tuple[bool, str]:
    """Neue torch-Fassung einspielen und prüfen; fehlt danach etwas, die alte zurückholen.
    Rückgabe: (neu ist drin, Meldung)."""
    kann = kann or torch_kann
    vorher = vorher if vorher is not None else kann(plan.get("dazu", []))
    melden("🔥 lade und installiere PyTorch (rund 3 GB) …")
    r = _ausfuehren(_pip("install") + list(plan["pakete"]) + ["--index-url", plan["index"]], 3600)
    if r.returncode != 0:
        nachher = kann(plan.get("dazu", []))
        if not torch_verloren(vorher, nachher):
            return False, f"PyTorch nicht installiert, die alte Fassung läuft weiter: {_kurz((r.stderr or '') + (r.stdout or ''))}"
        fehler = _kurz((r.stderr or "") + (r.stdout or ""))
    else:
        melden("🔥 prüfe: lädt PyTorch, sieht es die Grafikkarte, rechnet es? …")
        nachher = kann(plan.get("dazu", []))
        verloren = torch_verloren(vorher, nachher)
        if not verloren:
            return True, f"PyTorch {plan['pakete'][0].split('==', 1)[1]} läuft (Karte: {'ja' if nachher.get('cuda') else 'nein'})."
        fehler = "fehlt danach: " + ", ".join(verloren)
    melden("↩ hole die alte PyTorch-Fassung zurück …")
    z = _ausfuehren(_pip("install") + list(plan["alt"]) + ["--index-url", plan["alt_index"]], 3600)
    if z.returncode == 0 and not torch_verloren(vorher, kann(plan.get("dazu", []))):
        return False, f"Neue PyTorch-Fassung zurückgenommen ({fehler}) – die alte läuft wieder."
    return False, (f"Neue PyTorch-Fassung defekt ({fehler}), und die alte kam nicht sauber zurück. Im venv-Fenster "
                   f"(/venv): pip install {' '.join(plan['alt'])} --index-url {plan['alt_index']}")


def torch_vormerken(e: Eintrag) -> None:
    """Für den nächsten Start vormerken, samt dem jetzigen Können als Maßstab für die Prüfung danach."""
    plan = _torch_plan(e)
    plan["vorher"] = torch_kann(plan["dazu"])
    TORCH_AUSSTEHEND.parent.mkdir(parents=True, exist_ok=True)
    TORCH_AUSSTEHEND.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")


def _torch_ausstehend_einspielen(melden: Callable[[str], None]) -> None:
    """Beim Start: vorgemerktes torch-Update, sobald kein anderer Prozess (z. B. die Wache) torch geladen hat."""
    try:
        plan = json.loads(TORCH_AUSSTEHEND.read_text(encoding="utf-8"))
        if not (isinstance(plan, dict) and plan.get("pakete") and plan.get("index", "").startswith(TORCH_INDEX)
                and plan.get("alt_index", "").startswith(TORCH_INDEX)):
            raise ValueError("Vormerkung unvollständig")
    except (OSError, ValueError) as e:
        TORCH_AUSSTEHEND.unlink(missing_ok=True)
        melden(f"🔥 PyTorch-Vormerkung verworfen: {e}")
        return
    if not torch_frei(site_packages()):
        melden("🔥 PyTorch-Update wartet: ein anderer Prozess hat PyTorch geladen (läuft die Wache?). "
               "Nächster Start versucht es wieder.")
        return
    try:
        TORCH_AUSSTEHEND.unlink()                       # einmal versuchen, nicht bei jedem Start
    except FileNotFoundError:
        return                                          # ein zweiter Prozess war schneller
    melden("🔥 PyTorch-Update aus /update wird eingespielt …")
    ok, text = torch_tauschen(plan, plan.get("vorher") or None, melden)
    melden(("🔥 " if ok else "⚠ ") + text)


def _pip_eintrag(inst: dict[str, str]) -> Optional[Eintrag]:
    """uv legt venvs ohne pip an; ein `pip` in der Konsole griffe dann zum System-Python."""
    if "pip" in inst:
        return None
    return Eintrag("pip", "", "", "pip", "fehlt", hinweis="damit pip im venv-Fenster (/venv) das venv trifft")


STAND = {"luecken": "", "torch": ""}    # nach pruefen(): "" geprüft, sonst der Grund, warum nicht


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
        if alt is not None and alt.grund == "torch-update" and not any(l.schadcode for l in liste):
            alt.luecken = liste                        # das Update bleibt anwählbar, die Lücke steht dabei
            continue
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
            luecken_holen: Optional[Callable[[dict], dict]] = None,
            torch_neu: Optional[Callable[[dict], Optional[Eintrag]]] = None) -> list[Eintrag]:
    """Was im venv zu tun ist: Nötiges (fehlt, passt nicht, torch, Lücken, Schadpakete) und Updates."""
    veraltet = veraltet or _veraltet
    luecken_holen = luecken_holen or _luecken_holen
    torch_neu = torch_neu or _torch_update
    site = site_packages()
    if site is None or not site.is_dir():
        return []
    inst = installiert(site)
    wuensche = _wuensche(inst)
    raus: list[Eintrag] = []
    STAND["torch"] = ""
    if (t := _torch_eintrag(inst)) is not None:
        raus.append(t)
    else:
        try:
            if (t := torch_neu(inst)) is not None:
                raus.append(t)
        except Exception as e:
            STAND["torch"] = str(e) or "PyTorch-Index nicht erreichbar"
    if (p := _pip_eintrag(inst)) is not None:
        raus.append(p)
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
            "ueberfluessig": 6, "update": 7, "torch-update": 8}
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
    site = site_packages()
    jetzt, spaeter, neustart = [], [], []
    torch_erg = {"jetzt": False, "start": False, "text": ""}
    for e in [e for e in auswahl if e.grund == "torch-update"]:
        if site is not None and not in_gebrauch("torch", site) and torch_frei(site):
            ok, text = torch_tauschen(_torch_plan(e), melden=on_status or (lambda s: None))
            torch_erg.update(jetzt=ok, text=text)
        else:
            if on_status:
                on_status("PyTorch ist geladen – merke das Update für den nächsten Start vor …")
            torch_vormerken(e)
            torch_erg.update(start=True, text=f"PyTorch {e.neu} wird beim nächsten Start eingespielt, "
                                              "bevor etwas es lädt.")
    auswahl = [e for e in auswahl if e.grund != "torch-update"]
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
            "abgelehnt": [(e.name, g) for e, g in abgelehnt], "zurueck": [(e.name, f) for e, f in zurueck],
            "torch": torch_erg}


def ausstehende_einspielen(melden: Callable[[str], None] = print) -> None:
    """Beim Start, bevor etwas aus dem venv geladen ist: Zurückgestelltes installieren."""
    if not uvsetup.venv_python():
        return
    if TORCH_AUSSTEHEND.is_file():
        try:
            _torch_ausstehend_einspielen(melden)
        except Exception as e:
            melden(f"⚠ PyTorch-Update beim Start nicht möglich: {e}")
    if not AUSSTEHEND.is_file():
        return
    melden("📦 Zurückgestellte Updates aus /update werden eingespielt …")
    try:
        # Stehen Hashes in der Liste, muss jedes Paket einen haben
        mit_hashes = "--hash=" in AUSSTEHEND.read_text(encoding="utf-8", errors="replace")
        ok = _ausfuehren(_pip("install") + ["-r", str(AUSSTEHEND)] + (["--require-hashes"] if mit_hashes else []),
                         3600).returncode == 0
    except Exception:
        ok = False
    if ok:
        AUSSTEHEND.unlink(missing_ok=True)
        melden("📦 Fertig.")
    else:
        melden("📦 Hat nicht geklappt – läuft die Wache? /update versucht es erneut.")
