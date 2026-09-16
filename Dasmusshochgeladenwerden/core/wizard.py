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
  4. diffusers & Co.             (Bausteine fürs Bilder-Malen)
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
        "ok": _hat_paket("torch"), "machbar": True, "pflicht": False,
        "info": plan["grund"],
    })
    schritte.append({
        "id": "diffusers", "titel": "Bild-Bausteine (diffusers, transformers …)",
        "ok": _hat_paket("diffusers"), "machbar": True, "pflicht": False,
        "info": "nötig für /bild",
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
    schritte.append({
        "id": "bildmodell", "titel": "Bild-Modell (zum Malen)",
        "ok": stock["checkpoints"] > 0, "machbar": False, "pflicht": False,
        "info": (f"{stock['checkpoints']} Checkpoint(s)" if stock["checkpoints"]
                 else f"fehlt – .safetensors nach {stock['ckpt_ordner']}"),
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
        uvsetup.pip_install(["torch"], on_status,
                            extra=["--index-url", f"https://download.pytorch.org/whl/{kanal}"])
    return f"torch ({kanal}) installiert."


def install_diffusers(on_status=None) -> str:
    """Die restlichen Bausteine fürs Bilder-Malen."""
    if not _venv_python():
        install_venv(on_status)
    if on_status:
        on_status("installiere diffusers, transformers, safetensors, pillow …")
    # transformers MODERN pinnen: uv (strenger als pip) fiel bei losem
    # `transformers<5` sonst auf uralt-4.12.2 zurück, dessen `tokenizers 0.10.3`
    # kein 3.12-Wheel hat und aus Rust-Quellcode gebaut werden müsste → Fehler.
    # >=4.44 erzwingt eine Fassung mit fertigen cp312-Wheels (kein Rust nötig).
    uvsetup.pip_install(["diffusers>=0.38", "transformers>=4.44,<5", "tokenizers>=0.20",
                         "accelerate", "safetensors", "pillow", "numpy",
                         "opencv-python<5"], on_status)
    return "Bild-Bausteine installiert."


ERLEDIGER = {
    "python": install_python,
    "venv": install_venv,
    "torch": install_torch,
    "diffusers": install_diffusers,
}


def erledige(schritt_id: str, on_status=None) -> str:
    fn = ERLEDIGER.get(schritt_id)
    if not fn:
        raise RuntimeError(f"Für '{schritt_id}' gibt es nichts zu installieren.")
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
    ("Bild-Modelle (.safetensors)", "https://civitai.com"),
]


def modell_ordner() -> dict:
    """Wohin die selbst geladenen Dateien gehören."""
    return {"checkpoints": str(ROOT / "Models" / "checkpoints")}


def erster_start() -> bool:
    """War NemiCLI hier noch nie eingerichtet? (dann Assistent anbieten)"""
    return not (INSTALL / "nemicli.config.json").exists()


if __name__ == "__main__":         # Selbsttest: python core/wizard.py
    for s in check_all():
        print(f"  [{'x' if s['ok'] else ' '}] {s['titel']:<45} {s['info']}")
    print("\noffen & machbar:", [s["id"] for s in offene(check_all())])
