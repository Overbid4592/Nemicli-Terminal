"""
syscheck.py - "Was hat dieser PC, und was fehlt noch?"

Für /systemcheck. Beantwortet drei Fragen:

  1. Welche Grafikkarte steckt drin – und welche Rechen-Stufe (compute
     capability, z.B. sm_120 bei den RTX-50er/Blackwell)? Das entscheidet,
     WELCHES torch man installieren muss: eine zu alte torch-Version kennt
     sm_120 nicht und rechnet dann gar nicht oder nur auf der CPU.
  2. Was ist schon installiert (torch, diffusers, transformers …)?
  3. Was liegt an Modellen da (GGUF / Checkpoints) und was fehlt?

Es wird NICHTS automatisch installiert und kein bestimmtes Modell vorgegeben –
der Bericht nennt nur Fakten plus den passenden pip-Befehl zum Kopieren.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import models as M
import setup as S

try:
    from paths import ROOT
except Exception:
    ROOT = Path(__file__).resolve().parent.parent


# ===========================================================================
#  Rechen-Stufe der NVIDIA-Karte (sm_XX)
# ===========================================================================

def compute_cap() -> str:
    """Compute Capability der ersten NVIDIA-Karte, z.B. '12.0' (= sm_120).
    Leerer String, wenn keine NVIDIA-Karte da ist oder nvidia-smi es nicht sagt."""
    out = S._run(["nvidia-smi", "--query-gpu=compute_cap",
                  "--format=csv,noheader"])
    m = re.search(r"(\d+)\.(\d+)", out)
    return f"{int(m.group(1))}.{int(m.group(2))}" if m else ""


def sm_tag(cap: str) -> str:
    """'12.0' -> 'sm_120'."""
    if not cap:
        return ""
    major, _, minor = cap.partition(".")
    return f"sm_{major}{minor}"


# Was für ein torch braucht diese Rechen-Stufe mindestens?
# Blackwell (sm_120, RTX 50xx) läuft erst ab CUDA 12.8 – ältere torch-Räder
# haben den Code für sm_120 gar nicht drin.
def _wheel_for(cap: str) -> tuple[str, str]:
    """Gibt (kanal, grund) zurück. kanal = pip-Index-Kürzel wie 'cu128'."""
    try:
        major = int(float(cap))
    except Exception:
        return "cu126", "Standard-CUDA-Bau"
    if major >= 12:            # Blackwell und neuer
        return "cu128", "Blackwell (sm_120) braucht CUDA 12.8 oder neuer"
    if major >= 8:             # Ampere / Ada (RTX 30xx, 40xx)
        return "cu126", "passt für Ampere/Ada (RTX 30xx/40xx)"
    if major >= 7:             # Turing / Volta (RTX 20xx, GTX 16xx)
        return "cu126", "passt für Turing (RTX 20xx / GTX 16xx)"
    return "cpu", "diese Karte ist für CUDA zu alt – nur CPU"


_TORCH_URL = "https://download.pytorch.org/whl/"


def torch_plan(gpu: dict | None = None, cap: str = "") -> dict:
    """Der passende Installations-Plan für Bild-Erzeugung auf DIESEM PC.

    Rückgabe: {ok, kanal, grund, cmd, extra, warnung}
      cmd     = pip-Befehl für torch (zum Kopieren)
      extra   = pip-Befehl für den Rest (diffusers …)
      warnung = Hinweis, wenn etwas nicht zusammenpasst
    """
    gpu = gpu or S.gpu_info()
    vendor = gpu.get("vendor", "cpu")
    py = "python -m pip install"

    # transformers MUSS < 5 bleiben: 5.x bricht das Laden von Einzeldatei-
    # Checkpoints in diffusers ('CLIPTextModel' has no attribute 'text_model').
    extra = f'{py} "diffusers>=0.38" "transformers<5" accelerate safetensors'

    if vendor == "nvidia":
        kanal, grund = _wheel_for(cap or compute_cap())
        if kanal == "cpu":
            return {"ok": True, "kanal": "cpu", "grund": grund,
                    "cmd": f"{py} torch --index-url {_TORCH_URL}cpu",
                    "extra": extra,
                    "warnung": "Bilder malen dauert auf der CPU sehr lange (Minuten pro Bild)."}
        warn = ""
        cuda_max = gpu.get("cuda_max") or 0
        need = 12.8 if kanal == "cu128" else 12.6
        if cuda_max and cuda_max < need:
            warn = (f"Dein Grafik-Treiber kann nur CUDA {cuda_max}, gebraucht wird "
                    f"{need}. Erst den NVIDIA-Treiber aktualisieren.")
        return {"ok": True, "kanal": kanal, "grund": grund,
                "cmd": f"{py} torch --index-url {_TORCH_URL}{kanal}",
                "extra": extra, "warnung": warn}

    if vendor == "amd":
        return {"ok": True, "kanal": "directml", "grund":
                "AMD unter Windows: ROCm gibt es nur für Linux",
                "cmd": f"{py} torch-directml",
                "extra": extra,
                "warnung": "AMD auf Windows läuft über DirectML – langsamer und nicht überall stabil."}

    if vendor == "intel":
        return {"ok": True, "kanal": "cpu",
                "grund": "Intel-Grafik: läuft praktisch auf der CPU",
                "cmd": f"{py} torch --index-url {_TORCH_URL}cpu",
                "extra": extra,
                "warnung": "Bilder malen dauert damit sehr lange."}

    return {"ok": True, "kanal": "cpu", "grund": "keine nutzbare Grafikkarte gefunden",
            "cmd": f"{py} torch --index-url {_TORCH_URL}cpu", "extra": extra,
            "warnung": "Ohne Grafikkarte dauert ein Bild mehrere Minuten."}


# ===========================================================================
#  Was ist installiert?
# ===========================================================================

def _version(mod: str) -> str | None:
    """Version eines Pakets, ohne es zu importieren (importlib.metadata)."""
    try:
        from importlib.metadata import version
        return version(mod)
    except Exception:
        return None


def _installed(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def torch_status() -> dict:
    """Ist torch da, sieht es die GPU, kennt es die Rechen-Stufe der Karte?
    Torch wird hier WIRKLICH importiert (dauert ein paar Sekunden) – aber nur,
    wenn es installiert ist."""
    ver = _version("torch")
    if not ver:
        return {"da": False}
    info = {"da": True, "version": ver, "cuda": None, "gpu_nutzbar": False,
            "arch": [], "passt": None}
    try:
        import torch                       # kann ein paar Sekunden dauern
        info["cuda"] = getattr(torch.version, "cuda", None)
        info["gpu_nutzbar"] = bool(torch.cuda.is_available())
        try:
            info["arch"] = list(torch.cuda.get_arch_list())
        except Exception:
            info["arch"] = []
    except Exception as e:
        info["fehler"] = str(e)[:200]
    return info


def packages() -> list[dict]:
    """Liste aller Pakete, die NemiCLI kennt: Name, Version, wofür, Pflicht?"""
    rows = [
        ("rich", "Terminal-Optik", True),
        ("textual", "Vollbild-Oberfläche", True),
        ("prompt_toolkit", "Eingabe (klassisch) & Menüs", True),
        ("httpx", "Internet-Verbindungen", True),
        ("openai", "Cloud-Anbieter (OpenAI-Protokoll)", True),
        ("anthropic", "Cloud-Anbieter Anthropic", True),
        ("dotenv", "Schlüssel aus .env lesen", True),
        ("cv2", "Gesichter finden beim Bild-Nachbessern", False),
        ("torch", "Rechen-Motor fürs Bilder-Malen", False),
        ("diffusers", "Stable-Diffusion-Bausteine", False),
        ("transformers", "Text-Verstehen fürs Bild-Modell (muss < 5 sein!)", False),
        ("safetensors", "Modell-Dateien laden", False),
    ]
    dist = {"cv2": "opencv-python", "dotenv": "python-dotenv"}
    out = []
    for mod, zweck, pflicht in rows:
        v = _version(dist.get(mod, mod))
        out.append({"name": dist.get(mod, mod), "modul": mod, "zweck": zweck,
                    "pflicht": pflicht, "da": bool(v) or _installed(mod),
                    "version": v or ""})
    return out


def transformers_ok() -> bool | None:
    """transformers muss < 5 sein (sonst bricht das Laden der Bild-Modelle).
    None = nicht installiert."""
    v = _version("transformers")
    if not v:
        return None
    try:
        return int(v.split(".")[0]) < 5
    except Exception:
        return True


# ===========================================================================
#  Was liegt an Modellen da?
# ===========================================================================

def model_stock() -> dict:
    """Zählt vorhandene Modelle und sagt, wo sie hingehören."""
    ckpt_dirs = [ROOT / "Models" / "checkpoints", ROOT / "Models"]

    def _count(d: Path, suffix: str, skip_mmproj: bool = False) -> int:
        if not d.exists():
            return 0
        n = 0
        for f in d.glob(f"*{suffix}"):
            if skip_mmproj and f.name.lower().startswith("mmproj"):
                continue
            n += 1
        return n

    ckpts = 0
    seen: set[str] = set()
    for d in ckpt_dirs:
        if d.exists():
            for f in d.glob("*.safetensors"):
                if f.name not in seen:
                    seen.add(f.name)
                    ckpts += 1

    frei = 0
    try:
        frei = shutil.disk_usage(str(ROOT)).free // (1024 ** 3)
    except Exception:
        pass

    return {
        "checkpoints": ckpts,
        "ckpt_ordner": str(ckpt_dirs[0]),
        "ollama": S.ollama_running(),
        "frei_gb": frei,
    }


# ===========================================================================
#  Gesamt-Bericht
# ===========================================================================

def report(deep: bool = True) -> dict:
    """Alles zusammen für ui.system_panel(). deep=False lässt den (langsamen)
    torch-Import weg."""
    try:            # exe-Betrieb: extern installiertes torch erst sichtbar machen,
        import extlibs               # sonst meldet der Bericht es fälschlich als fehlend
        extlibs.enable()
    except Exception:
        pass
    gpu = S.gpu_info()
    cap = compute_cap() if gpu.get("vendor") == "nvidia" else ""
    rep = {
        "gpu": gpu,
        "cap": cap,
        "sm": sm_tag(cap),
        "hint": S.hardware_hint(gpu),
        "plan": torch_plan(gpu, cap),
        "pakete": packages(),
        "transformers_ok": transformers_ok(),
        "stock": model_stock(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "frozen": bool(getattr(sys, "frozen", False)),
        "root": str(ROOT),
    }
    try:
        import extlibs
        rep["extlibs"] = extlibs.status_text()
    except Exception:
        rep["extlibs"] = ""
    rep["torch"] = torch_status() if deep else {"da": bool(_version("torch"))}

    # Passt das installierte torch zur Karte? (kennt es sm_120?)
    t = rep["torch"]
    if t.get("da") and t.get("arch") and rep["sm"]:
        rep["torch_passt"] = any(rep["sm"] in a for a in t["arch"])
    else:
        rep["torch_passt"] = None
    return rep


def todo(rep: dict) -> list[str]:
    """Kurze Liste: was fehlt noch? (Klartext, in der Reihenfolge sinnvoll)"""
    out: list[str] = []
    st = rep["stock"]
    if not st["ollama"]:
        out.append("Kein lokales Modell: Ollama installieren "
                   "(ollama.com/download) und /model → 'Ollama einrichten'.")
    if st["checkpoints"] == 0:
        out.append(f"Zum Bilder-Malen fehlt ein .safetensors-Modell in "
                   f"{st['ckpt_ordner']} (siehe LIES-MICH dort).")
    t = rep["torch"]
    if not t.get("da"):
        out.append("torch ist nicht installiert – ohne das kann NemiCLI keine "
                   "Bilder malen. Befehl steht oben.")
    else:
        if not t.get("gpu_nutzbar") and rep["gpu"].get("vendor") == "nvidia":
            out.append("torch sieht deine Grafikkarte nicht – es rechnet auf der "
                       "CPU. Mit dem Befehl oben neu installieren.")
        if rep.get("torch_passt") is False:
            out.append(f"Dein torch kennt {rep['sm']} nicht (deine Karte ist zu neu "
                       "für diese torch-Version). Befehl oben nutzen.")
    if rep["transformers_ok"] is False:
        out.append('transformers ist Version 5 oder neuer – das bricht die '
                   'Bild-Modelle. Bitte: python -m pip install "transformers<5"')
    if not any(p["da"] for p in rep["pakete"] if p["modul"] == "diffusers"):
        if t.get("da"):
            out.append("diffusers fehlt noch (zweiter Befehl oben).")
    if st["frei_gb"] and st["frei_gb"] < 20:
        out.append(f"Nur noch {st['frei_gb']} GB frei – Modelle brauchen viel Platz.")
    return out


if __name__ == "__main__":            # Selbsttest:  python engines/syscheck.py
    import json
    r = report(deep="--fast" not in sys.argv)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    print("\nTODO:")
    for t in todo(r):
        print(" -", t)
