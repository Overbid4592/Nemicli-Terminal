"""Coding-LoRA an ein geladenes GGUF-Modell anklinken – von außen, ohne den GGUF-Motor zu ändern.

Ein LoRA ergänzt ausgewählte lineare Schichten:  y = W x + staerke * (alpha / rang) * B (A x).
Die Schichten werden über ihren GGUF-Tensornamen gefunden (Tabelle je Bauart), beim Anklinken
umhüllt und beim Abklinken wieder freigelegt. Danach werden die CUDA-Graphen des Modells geleert;
sie nehmen beim nächsten Schritt den neuen Rechenweg auf.

Ein LoRA gehört zu genau einem Grundmodell: Name und Größe der GGUF-Datei stehen in den
Metadaten und müssen passen.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import torch
import torch.nn.functional as F

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

ORDNER = _ROOT / "learned" / "coding_lora"
ART = "nemicli-coding-lora"

# Bauarten mit Lernweg; je Klasse (Modul.Klasse oder nur Klasse): Attribut -> GGUF-Name der Schicht
BAUARTEN = ("qwen35", "gemma4")
SCHICHTEN = {
    "_AttentionLayer": {"q": "attn_q", "k": "attn_k", "v": "attn_v", "o": "attn_output"},
    "_GatedDeltaNetLayer": {"qkv": "attn_qkv", "z": "attn_gate", "out": "ssm_out"},
    "_Block": {"gate": "ffn_gate", "up": "ffn_up", "down": "ffn_down"},
    "gemma4._Layer": {"q": "attn_q", "k": "attn_k", "v": "attn_v", "qkv": "attn_qkv", "o": "attn_output",
                      "gate": "ffn_gate", "up": "ffn_up", "down": "ffn_down"},
}
_MERKER = "_nemicli_coding_lora"         # am Modell: Kennung des angeklinkten LoRA


def bloecke(model) -> list:
    """Die Schichten-Blöcke eines Modells (qwen35: blocks, gemma4: layers)."""
    return list(getattr(model, "blocks", None) or getattr(model, "layers", None) or [])


def _tabelle(obj) -> dict:
    art = type(obj)
    return SCHICHTEN.get(f"{art.__module__.rsplit('.', 1)[-1]}.{art.__name__}") or SCHICHTEN.get(art.__name__, {})


def schichten(model) -> dict[str, tuple[object, str]]:
    """GGUF-Name -> (Besitzer, Attribut) der Schichten, die ein LoRA bekommen können."""
    out = {}
    for i, blk in enumerate(bloecke(model)):
        for obj in (getattr(blk, "mixer", None), blk):
            if obj is None:
                continue
            for attr, name in _tabelle(obj).items():
                if getattr(obj, attr, None) is not None:
                    out[f"blk.{i}.{name}.weight"] = (obj, attr)
    return out


def basis_kennung(gguf: str | os.PathLike) -> str:
    p = Path(gguf)
    return f"{p.name}|{p.stat().st_size}"


def datei_fuer(gguf: str | os.PathLike) -> Path:
    return ORDNER / f"{Path(gguf).stem}.safetensors"


class LoraSchicht:
    """Umhüllt eine lineare Schicht des GGUF-Motors und addiert den LoRA-Anteil."""

    def __init__(self, basis, a: torch.Tensor, b: torch.Tensor, faktor: float):
        self.basis = basis
        self.a, self.b, self.faktor = a, b, faktor
        self.n_out, self.n_in = basis.n_out, basis.n_in

    def __getattr__(self, name):         # w, dtype, … wie die umhüllte Schicht
        return getattr(self.__dict__["basis"], name)

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        y = self.basis(x)
        d = F.linear(F.linear(x.to(self.a.dtype), self.a), self.b)
        return y + (d * self.faktor).to(y.dtype)


def speichern(pfad: str | os.PathLike, gewichte: dict[str, tuple[torch.Tensor, torch.Tensor]],
              meta: dict, opt=None, halb: bool = False) -> Path:
    """gewichte: GGUF-Name -> (A (rang, n_in), B (n_out, rang)). Mit `opt` (AdamW, für Zwischenstände) auch
    dessen Momente je Matrix und der Schrittzähler – Fortsetzen rechnet dann wie ohne Pause. `halb`: als
    bfloat16 (fertige LoRA – angeklinkt rechnet die Grafikkarte ohnehin in bfloat16)."""
    from safetensors.torch import save_file
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tensoren = {}
    meta = dict(meta)
    art = torch.bfloat16 if halb else torch.float32
    for name, (a, b) in gewichte.items():
        tensoren[f"{name}.lora_a"] = a.detach().to("cpu", art).contiguous()
        tensoren[f"{name}.lora_b"] = b.detach().to("cpu", art).contiguous()
        for teil, p in (("lora_a", a), ("lora_b", b)):
            st = opt.state.get(p) if opt is not None else None
            if st and "exp_avg" in st:
                tensoren[f"adam.{name}.{teil}.exp_avg"] = st["exp_avg"].detach().float().cpu().contiguous()
                tensoren[f"adam.{name}.{teil}.exp_avg_sq"] = st["exp_avg_sq"].detach().float().cpu().contiguous()
                meta["adam_schritt"] = int(float(st["step"]))
    tmp = pfad.with_suffix(".tmp")
    save_file(tensoren, str(tmp), metadata={"art": ART, **{k: str(v) for k, v in meta.items()}})
    os.replace(tmp, pfad)
    return pfad


def laden(pfad: str | os.PathLike) -> tuple[dict[str, tuple[torch.Tensor, torch.Tensor]], dict]:
    from safetensors import safe_open
    gewichte: dict = {}
    with safe_open(str(pfad), framework="pt") as f:
        meta = dict(f.metadata() or {})
        if meta.get("art") != ART:
            raise ValueError(f"{Path(pfad).name} ist kein Coding-LoRA.")
        for key in f.keys():
            name, teil = key.rsplit(".", 1)
            if key.startswith("adam.") or teil not in ("lora_a", "lora_b"):
                continue                                   # Optimizer-Zustand eines Zwischenstands
            gewichte.setdefault(name, [None, None])[0 if teil == "lora_a" else 1] = f.get_tensor(key)
    return {n: (a, b) for n, (a, b) in gewichte.items() if a is not None and b is not None}, meta


def optimierer_laden(pfad: str | os.PathLike, gewichte: dict, opt) -> bool:
    """AdamW-Momente eines Zwischenstands übernehmen; False, wenn er keine enthält."""
    from safetensors import safe_open
    with safe_open(str(pfad), framework="pt") as f:
        meta = dict(f.metadata() or {})
        if "adam_schritt" not in meta:
            return False
        schritt = torch.tensor(float(meta["adam_schritt"]))
        keys = set(f.keys())
        for name, (a, b) in gewichte.items():
            for teil, p in (("lora_a", a), ("lora_b", b)):
                m, v = f"adam.{name}.{teil}.exp_avg", f"adam.{name}.{teil}.exp_avg_sq"
                if m in keys and v in keys:
                    opt.state[p] = {"step": schritt.clone(), "exp_avg": f.get_tensor(m).to(p.device, p.dtype),
                                    "exp_avg_sq": f.get_tensor(v).to(p.device, p.dtype)}
    return True


def info(pfad: str | os.PathLike) -> dict:
    """Metadaten eines LoRA (ohne die Gewichte)."""
    from safetensors import safe_open
    with safe_open(str(pfad), framework="pt") as f:
        return dict(f.metadata() or {})


def _graphen_leeren(model) -> None:
    g = getattr(model, "graph", None)
    if g is not None:
        g.leeren()


def angeklinkt(model) -> str:
    return getattr(model, _MERKER, "") or ""


def anklinken(model, pfad: str | os.PathLike, gguf: str | os.PathLike, staerke: float = 1.0) -> int:
    """LoRA an `model` anklinken. Gibt die Zahl der umhüllten Schichten zurück."""
    gewichte, meta = laden(pfad)
    if meta.get("basis") != basis_kennung(gguf):
        raise ValueError(f"Das LoRA gehört zu {meta.get('basis', '?')}, geladen ist {basis_kennung(gguf)}.")
    abklinken(model)
    ziele = schichten(model)
    rang = int(meta.get("rang") or next(iter(gewichte.values()))[0].shape[0])
    faktor = float(meta.get("alpha") or rang) / rang * float(staerke)
    n = 0
    for name, (a, b) in gewichte.items():
        if name not in ziele:
            continue
        besitzer, attr = ziele[name]
        basis = getattr(besitzer, attr)
        if a.shape != (a.shape[0], basis.n_in) or b.shape != (basis.n_out, a.shape[0]):
            raise ValueError(f"{name}: Form passt nicht zum Modell.")
        geraet = getattr(model, "device", torch.device("cpu"))
        dtype = torch.bfloat16 if torch.device(geraet).type == "cuda" else torch.float32
        setattr(besitzer, attr, LoraSchicht(basis, a.to(geraet, dtype), b.to(geraet, dtype), faktor))
        n += 1
    setattr(model, _MERKER, f"{Path(pfad).name}|{staerke}")
    _graphen_leeren(model)
    return n


def abklinken(model) -> int:
    """Alle LoRA-Hüllen entfernen; das Modell rechnet danach wie vorher."""
    n = 0
    for besitzer, attr in schichten(model).values():
        schicht = getattr(besitzer, attr)
        if isinstance(schicht, LoraSchicht):
            setattr(besitzer, attr, schicht.basis)
            n += 1
    if n or angeklinkt(model):
        setattr(model, _MERKER, "")
        _graphen_leeren(model)
    return n


# ---------------------------------------------------------------------------
# Abgleich mit dem Coding-Assistenten (vor jeder Antwort des lokalen Modells)
# ---------------------------------------------------------------------------

def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def gewuenscht(gguf: str | os.PathLike) -> tuple[Path, float] | None:
    """(LoRA-Datei, Stärke), wenn der Coding-Assistent an ist, das LoRA eingeschaltet ist und
    es eines für dieses Modell gibt – sonst None."""
    try:
        import coding
        if not coding.aktiv():
            return None
    except Exception:
        return None
    cfg = _config()
    if not cfg.get("coding_lora", True):
        return None
    pfad = datei_fuer(gguf)
    if not pfad.is_file():
        return None
    try:
        staerke = min(2.0, max(0.0, float(cfg.get("coding_lora_staerke", 1.0))))
    except (TypeError, ValueError):
        staerke = 1.0
    return (pfad, staerke) if staerke > 0 else None


def abgleich(engine, gguf: str | os.PathLike) -> bool:
    """Coding-Assistent an → LoRA angeklinkt, aus → abgeklinkt. True, wenn sich etwas geändert hat
    (dann ist der Gesprächsstand der Engine verworfen: er wurde mit dem anderen Rechenweg gelesen)."""
    model = engine.model
    soll = gewuenscht(gguf)
    kennung = ""
    if soll is not None:
        kennung = f"{soll[0].name}|{soll[1]}|{soll[0].stat().st_mtime_ns}"
    if kennung == getattr(engine, "_nemicli_lora_soll", ""):
        return False
    try:
        if soll is None:
            abklinken(model)
        else:
            anklinken(model, soll[0], gguf, soll[1])
    except Exception as exc:
        abklinken(model)
        kennung = f"fehler|{exc}"
        _melden(f"Coding-LoRA nicht angeklinkt: {exc}")
    engine._nemicli_lora_soll = kennung
    engine.reset()
    engine._checkpoint = None
    return True


def _melden(text: str) -> None:
    try:
        import debugbericht
        debugbericht.ereignis(text)
    except Exception:
        pass


def stand_text(gguf: str | os.PathLike | None) -> str:
    """Kurzer Stand für das Werkzeug coding_lora."""
    if gguf is None:
        return "Kein lokales GGUF-Modell eingestellt."
    pfad = datei_fuer(gguf)
    if not pfad.is_file():
        return f"Noch kein Coding-LoRA für {Path(gguf).name}."
    m = info(pfad)
    wann = time.strftime("%d.%m.%Y %H:%M", time.localtime(pfad.stat().st_mtime))
    return (f"Coding-LoRA für {Path(gguf).name}: Rang {m.get('rang')}, {m.get('schritte')} Schritte, "
            f"{m.get('beispiele')} Beispiele, Verlust {m.get('verlust', '?')} – trainiert {wann}.")
