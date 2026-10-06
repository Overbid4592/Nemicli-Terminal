"""
wizard.py - Der Einrichtungs-Assistent (/einrichten).

Ziel: Jemand startet NemiCLI zum ersten Mal und muss NICHTS über pip,
Python-Versionen oder CUDA wissen. Der Assistent prüft der Reihe nach, was
da ist, und installiert das Fehlende selbst.

Geprüft/erledigt wird:

  1. Python für den Bild-Motor   (nur nötig, wenn NemiCLI als exe läuft)
  2. Eigene Arbeits-Umgebung     (venv neben NemiCLI, damit nichts am
                                  System-Python herumgepfuscht wird)
  3. torch                       (passend zur Grafikkarte, siehe syscheck)
  4. Pakete aus requirements.txt (Krea 2, Gedächtnis-Encoder …)
  5. Ollama                      (Motor für lokale Modelle – nur prüfen)
  6. Modelle                     -> NUR prüfen und erklären. Die lädt der
                                    Nutzer selbst, NemiCLI darf keine
                                    Gigabyte-Modelle für ihn auswählen.

ISOLATION (seit 12.09.2026): Python + venv + Bild-Pakete kommen über `uv`
(core/uvsetup.py). Es wird ein EIGENSTÄNDIGES Python in <NemiCLI>/.runtime/
gelegt - kein Admin, keine PATH-Änderung, nichts am System des Nutzers. Das
löst den alten python.org-Weg (systemweite Installation) ab.

SICHERHEIT: Geladen wird nur von github.com (uv) und den üblichen
Paket-Quellen (PyPI, download.pytorch.org) - HTTPS, feste Host-Prüfung.
Installiert wird immer erst nach Rückfrage.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import models as M
import setup as S
import syscheck
import extlibs
import uvsetup

try:
    from paths import ROOT, INSTALL
except Exception:
    ROOT = INSTALL = Path(__file__).resolve().parent.parent

FROZEN = bool(getattr(sys, "frozen", False))
PYVER = f"{sys.version_info.major}.{sys.version_info.minor}"      # "3.12"

# Isoliertes Python + venv liegen unter <NemiCLI>/.runtime bzw. /venv,
# verwaltet von uvsetup (uv). Die Minor-Version muss zur exe passen (cp312).
VENV_DIR = uvsetup.VENV_DIR


# ===========================================================================
#  Kleine Helfer
# ===========================================================================

def _venv_python() -> Path | None:
    """Das Python der isolierten Umgebung (falls schon angelegt)."""
    return uvsetup.venv_python()


# Paket-Installation läuft jetzt über uvsetup.pip_install (uv) – siehe install_*.


def _hat_paket(name: str) -> bool:
    """Ist ein Paket in der isolierten Umgebung (oder ohnehin) installiert?"""
    if uvsetup.has_package(name):
        return True
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


# ===========================================================================
#  Prüfen: was ist da, was fehlt?
# ===========================================================================

def check_all() -> list[dict]:
    """Der Prüfbericht. Jeder Eintrag:
       {id, titel, ok, info, machbar, pflicht}
       machbar=True -> der Assistent kann es selbst erledigen."""
    schritte: list[dict] = []

    # 1. Isoliertes Python (uv holt es in .runtime/ – kein Admin, kein PATH).
    #    Ist schon ein venv da, brauchen wir das eigene Python nicht mehr → grün.
    py_ok = uvsetup.python_ready() or bool(_venv_python())
    schritte.append({
        "id": "python", "titel": f"Isoliertes Python {PYVER} (fürs Bilder-Malen)",
        "ok": py_ok, "machbar": True, "pflicht": False,
        "info": (str(uvsetup.PY_INSTALL_DIR) if uvsetup.python_ready()
                 else "über die Arbeits-Umgebung vorhanden" if py_ok
                 else "wird von uv geholt (eigenständig, nichts am System)"),
    })

    # 2. Eigene Umgebung (venv neben NemiCLI)
    vp = _venv_python()
    schritte.append({
        "id": "venv", "titel": "Isolierte Arbeits-Umgebung (venv)",
        "ok": bool(vp), "machbar": True, "pflicht": False,
        "info": str(VENV_DIR) if vp else "wird neben NemiCLI angelegt",
    })

    # 3. + 4. Bild-Motor
    gpu = S.gpu_info()
    cap = syscheck.compute_cap() if gpu.get("vendor") == "nvidia" else ""
    plan = syscheck.torch_plan(gpu, cap)
    schritte.append({
        "id": "torch", "titel": f"Rechen-Motor torch ({plan['kanal']})",
        # Ein CPU-torch zählt bei einer NVIDIA-Karte nicht als erledigt.
        "ok": _hat_paket("torch") and (not plan["kanal"].startswith("cu")
                                       or _torch_bau() not in ("", "cpu")),
        "machbar": True, "pflicht": False,
        "info": plan["grund"],
    })
    fehlend = _fehlende_pakete()
    schritte.append({
        "id": "pakete", "titel": "Pakete aus requirements.txt (ohne torch)",
        "ok": not fehlend, "machbar": True, "pflicht": False,
        "info": ("alle da" if not fehlend
                 else "fehlt: " + ", ".join(fehlend[:6]) + (" …" if len(fehlend) > 6 else "")),
    })

    # 5. Ollama (der lokale Motor; llama.cpp ist am 15.09.2026 entfallen)
    ollama = S.ollama_running()
    schritte.append({
        "id": "ollama", "titel": "Ollama (Motor für lokale Modelle)",
        "ok": ollama, "machbar": False, "pflicht": False,
        "info": "läuft" if ollama else "nicht erreichbar – über /model einrichten",
    })

    # 6. Modelle – nur nachsehen, NICHT herunterladen
    stock = syscheck.model_stock()
    schritte.append({
        "id": "sprachmodell", "titel": "Sprach-Modell (zum Reden)",
        "ok": ollama, "machbar": False, "pflicht": True,
        "info": ("Ollama läuft" if ollama
                 else "fehlt – Cloud-Schlüssel oder Ollama"),
    })
    try:
        import memory
        gedaechtnis_ok = memory.modell_ordner() is not None
        gedaechtnis_info = memory.encoder_status()
    except Exception as exc:
        gedaechtnis_ok, gedaechtnis_info = False, f"nicht prüfbar: {exc}"
    schritte.append({
        "id": "gedaechtnismodell", "titel": "Gedächtnis-Modell (Suche nach Bedeutung)",
        "ok": gedaechtnis_ok, "machbar": False, "pflicht": False,
        "info": gedaechtnis_info,
    })
    schritte.append({
        "id": "bildmodell", "titel": "Bild-Modell (zum Malen)",
        "ok": stock["bildmodelle"] > 0, "machbar": False, "pflicht": False,
        "info": (f"{stock['bildmodelle']} Krea-2-Modell(e)" if stock["bildmodelle"]
                 else f"fehlt – Krea-2-Dateien nach {stock['bild_ordner']}"),
    })
    return schritte


def offene(schritte: list[dict]) -> list[dict]:
    """Was fehlt UND kann vom Assistenten erledigt werden?"""
    return [s for s in schritte if not s["ok"] and s["machbar"]]


# ===========================================================================
#  Erledigen
# ===========================================================================

def install_python(on_status=None) -> str:
    """Holt ein EIGENSTÄNDIGES Python via uv (kein Admin, kein PATH)."""
    return uvsetup.ensure_python(on_status)


def install_venv(on_status=None) -> str:
    """Legt die isolierte Arbeits-Umgebung neben NemiCLI an (uv)."""
    return uvsetup.ensure_venv(on_status)


def install_torch(on_status=None) -> str:
    """Installiert torch – in der Fassung, die zu dieser Grafikkarte passt."""
    if not _venv_python():
        install_venv(on_status)
    gpu = S.gpu_info()
    cap = syscheck.compute_cap() if gpu.get("vendor") == "nvidia" else ""
    plan = syscheck.torch_plan(gpu, cap)
    kanal = plan["kanal"]
    if on_status:
        on_status(f"installiere torch ({kanal}) – das sind ~2,5 GB, dauert …")
    if kanal == "directml":
        uvsetup.pip_install(["torch-directml"], on_status)
    else:
        # --reinstall-package: ein schon vorhandenes torch (z. B. der CPU-Bau, den
        # andere Pakete als Abhängigkeit mitziehen) wird ersetzt, statt als
        # „schon erfüllt“ liegen zu bleiben.
        uvsetup.pip_install(["torch"], on_status,
                            extra=["--index-url", f"https://download.pytorch.org/whl/{kanal}",
                                   "--reinstall-package", "torch"],
                            namen={"torch": torch_anzeigename(kanal)})
        bau = _torch_bau()
        if kanal.startswith("cu") and bau in ("cpu", ""):
            raise RuntimeError(f"torch im venv ist der Bau „{bau or '?'}“, erwartet {kanal}.")
    return f"torch ({kanal}) installiert."


def torch_anzeigename(kanal: str) -> str:
    """cu132 → „PyTorch CUDA 13.2“, cpu → „PyTorch (CPU)“."""
    if kanal.startswith("cu") and kanal[2:].isdigit() and len(kanal) > 3:
        z = kanal[2:]
        return f"PyTorch CUDA {z[:-1]}.{z[-1]}"
    return f"PyTorch ({kanal.upper()})"


def _torch_bau() -> str:
    """CUDA-Bau des torch im eigenen venv ('13.2', 'cpu' oder '' wenn keins da ist),
    gelesen aus torch/version.py – ohne torch zu importieren."""
    py = _venv_python()
    if not py:
        return ""
    datei = Path(py).parent.parent / "Lib" / "site-packages" / "torch" / "version.py"
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    import re
    m = re.search(r"^cuda[^=]*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    return m.group(1) if m else "cpu"


# torch kommt ausschließlich über install_torch vom PyTorch-Index (CUDA-Bau).
# Aus requirements.txt heraus würde uv es von PyPI holen – dort gibt es für
# Windows nur den CPU-Bau.
_TORCH_PAKETE = {"torch", "torchvision", "torchaudio", "torch-directml"}


def _requirements_datei() -> Path | None:
    for ort in (INSTALL, Path(getattr(sys, "_MEIPASS", INSTALL))):
        if (ort / "requirements.txt").is_file():
            return ort / "requirements.txt"
    return None


def _paketname(spez: str) -> str:
    import re
    return re.split(r"[\[<>=!~;\s]", spez, maxsplit=1)[0].strip().lower().replace("_", "-")


def requirements_ohne_torch() -> list[str]:
    """Die Einträge aus requirements.txt, ohne Kommentare und ohne torch."""
    datei = _requirements_datei()
    if not datei:
        return []
    pakete = []
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        zeile = zeile.split("#", 1)[0].strip()
        if zeile and not zeile.startswith("-") and _paketname(zeile) not in _TORCH_PAKETE:
            pakete.append(zeile)
    return pakete


def _fehlende_pakete() -> list[str]:
    """Namen aus requirements.txt, für die im venv kein dist-info liegt."""
    py = _venv_python()
    if not py:
        return [_paketname(p) for p in requirements_ohne_torch()]
    site = Path(py).parent.parent / "Lib" / "site-packages"
    try:
        da = {d.name.split("-", 1)[0].lower().replace("_", "-")
              for d in site.glob("*.dist-info")}
    except OSError:
        da = set()
    return [n for n in map(_paketname, requirements_ohne_torch()) if n not in da]


def _torch_zuerst(on_status=None) -> None:
    """Bei NVIDIA den CUDA-torch vor allem anderen: Pakete, die torch als
    Abhängigkeit haben, zögen sonst den CPU-Bau von PyPI."""
    if S.gpu_info().get("vendor") == "nvidia" and _torch_bau() in ("", "cpu"):
        install_torch(on_status)


def install_pakete(on_status=None) -> str:
    """requirements.txt ins venv – getrennt von torch, das vorher schon steht."""
    if not _venv_python():
        install_venv(on_status)
    pakete = requirements_ohne_torch()
    if not pakete:
        raise RuntimeError("requirements.txt nicht gefunden.")
    _torch_zuerst(on_status)
    if on_status:
        on_status(f"installiere {len(pakete)} Pakete aus requirements.txt …")
    uvsetup.pip_install(pakete, on_status)
    return f"{len(pakete)} Pakete aus requirements.txt installiert."


ERLEDIGER = {
    "python": install_python,
    "venv": install_venv,
    "torch": install_torch,
    "pakete": install_pakete,
}


def erledige(schritt_id: str, on_status=None) -> str:
    fn = ERLEDIGER.get(schritt_id)
    if not fn:
        raise RuntimeError(f"Für '{schritt_id}' gibt es nichts zu installieren.")
    with uvsetup.install_sperre():
        ergebnis = fn(on_status)
    extlibs._state.clear()        # Suche nach torch neu starten lassen
    return ergebnis


# ===========================================================================
#  Wo bekomme ich Modelle her?  (nur Hinweise – Download macht der Nutzer)
# ===========================================================================

MODELL_HINWEISE = [
    ("Cloud (am einfachsten, kein Download)",
     "/model → 'Cloud-Anbieter hinzufügen' → Schlüssel eintragen"),
    ("Ollama (lokal, bequem)", S.OLLAMA_INSTALL_URL),
    ("Lokale Sprach-Modelle", "/model → 'Modell von Hugging Face holen'"),
    ("Krea 2 (Bilder)", "/bildmodel → 'Krea-2-Ordner öffnen'"),
]


def modell_ordner() -> dict:
    """Wohin die selbst geladenen Dateien gehören."""
    try:
        import krea
        return {"bilder": str(krea.krea_dir())}
    except Exception:
        return {"bilder": str(ROOT / "Models" / "Krea2")}


def erster_start() -> bool:
    """War NemiCLI hier noch nie eingerichtet? (dann Assistent anbieten)"""
    return not (INSTALL / "nemicli.config.json").exists()


if __name__ == "__main__":         # Selbsttest: python core/wizard.py
    for s in check_all():
        print(f"  [{'x' if s['ok'] else ' '}] {s['titel']:<45} {s['info']}")
    print("\noffen & machbar:", [s["id"] for s in offene(check_all())])
