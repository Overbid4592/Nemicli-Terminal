"""
lora.py - LoRA für die eigenen Motoren: an ausgewählten Linear-Schichten sitzen zwei kleine Matrizen
(A: Rang × Eingang, B: Ausgang × Rang); gelernt wird nur deren Produkt, die Grundgewichte bleiben eingefroren.

Die Grundgewichte dürfen in den schnellen Formaten der Motoren vorliegen (FP8, GGUF-Blöcke): vorwärts rechnen die
gewohnten Kerne, rückwärts braucht es nur den Gradienten zur Eingabe – dafür wird das Gewicht kurz entpackt.
Gespeichert wird je Persönlichkeit unter Profile/<Name>/LoRA/<ziel>.safetensors (profilordner.lora_pfad).
"""
from __future__ import annotations

import math
import re
from pathlib import Path

# Wan-DiT: Attention (Selbst- und Kreuz-) und Feed-Forward jedes Blocks
WAN_ZIELE = re.compile(r"^blocks\.\d+\.(self_attn\.[qkvo]|cross_attn\.[qkvo]|ffn\.[02])\.weight$")


def _form(basis) -> tuple[int, int]:
    """(Ausgang, Eingang) eines Grundgewichts – Tensor, _F8, gepackte GGUF-Matrix oder Krea-Linear."""
    if hasattr(basis, "n_out"):
        return int(basis.n_out), int(basis.n_in)
    if hasattr(basis, "out_f"):
        return int(basis.out_f), int(basis.in_f)
    return int(basis.shape[0]), int(basis.shape[1])


def _entpackt(basis, dtype, device):
    """Grundgewicht als dichte Matrix (Ausgang, Eingang) – nur für den Rückweg und zum Einmischen."""
    import torch
    if isinstance(basis, torch.Tensor):
        return basis.to(device, dtype)
    if hasattr(basis, "w8"):                                   # _F8: e4m3 mit einer Skala
        return basis.w8.to(device, dtype) * basis.ws.to(device, dtype)
    if callable(getattr(basis, "weight", None)):               # Krea-Linear: bf16, FP8 oder NVFP4
        return basis.weight().to(device, dtype)
    from ggufengine.quant import dequantize                   # _Q: GGUF-Blöcke
    return dequantize(basis.raw.reshape(-1).to(device), basis.ggml_type, basis.n_out * basis.n_in,
                      dtype).view(basis.n_out, basis.n_in)


def _basis_rechnen(basis, x):
    import torch.nn.functional as F
    if callable(basis):
        return basis(x)
    return F.linear(x, basis.to(x.dtype))


def _basis_mit_gradient(basis, x):
    """basis(x) – vorwärts mit dem schnellen Kern, rückwärts Gradient zur Eingabe über das entpackte Gewicht.
    Das Gewicht selbst bekommt keinen Gradienten (eingefroren). `x` darf eine geteilte Eingabe sein
    (krea.Eingabe: Tensor in `.x`)."""
    import torch
    roh = getattr(x, "x", x)

    class _Fn(torch.autograd.Function):
        @staticmethod
        def forward(ctx, eingabe):
            with torch.no_grad():
                return _basis_rechnen(basis, eingabe)

        @staticmethod
        def backward(ctx, grad):
            w = _entpackt(basis, grad.dtype, grad.device)
            return grad @ w

    if not (torch.is_grad_enabled() and roh.requires_grad):
        return _basis_rechnen(basis, x)
    return _Fn.apply(roh)


class LoraLinear:
    """Grundgewicht + skala · B·A. B beginnt bei 0: ohne Training rechnet die Schicht genau wie vorher."""

    def __init__(self, basis, rang: int, alpha: float, device, dtype):
        import torch
        self.basis = basis
        n_out, n_in = _form(basis)
        self.rang, self.alpha = int(rang), float(alpha)
        self.skala = self.alpha / self.rang
        self.A = torch.nn.Parameter(torch.randn(self.rang, n_in, device=device, dtype=dtype) / math.sqrt(n_in))
        self.B = torch.nn.Parameter(torch.zeros(n_out, self.rang, device=device, dtype=dtype))

    def parameter(self):
        return [self.A, self.B]

    def __call__(self, x):
        y = _basis_mit_gradient(self.basis, x)
        roh = getattr(x, "x", x)
        zusatz = (roh.to(self.A.dtype) @ self.A.t()) @ self.B.t()
        return y + (zusatz * self.skala).to(y.dtype)


def anbringen(sd: dict, ziele=WAN_ZIELE, rang: int = 16, alpha: float | None = None, device="cpu",
              dtype=None) -> dict[str, LoraLinear]:
    """Ersetzt die passenden Gewichte in `sd` durch LoraLinear. Gibt {Name: Schicht} zurück."""
    import torch
    dtype = dtype or torch.float32
    alpha = float(rang if alpha is None else alpha)
    schichten = {}
    for k in list(sd):
        if ziele.search(k) and not isinstance(sd[k], LoraLinear):
            sd[k] = schichten[k] = LoraLinear(sd[k], rang, alpha, device, dtype)
    return schichten


def abnehmen(sd: dict) -> None:
    """Setzt die Grundgewichte wieder ein (LoRA entfernen, ohne einzumischen)."""
    for k, v in list(sd.items()):
        if isinstance(v, LoraLinear):
            sd[k] = v.basis


def parameter(schichten: dict) -> list:
    return [p for s in schichten.values() for p in s.parameter()]


def _param_namen(schichten: dict) -> dict:
    """{Parameter-Name: Parameter} – „<Schicht>.A“ / „<Schicht>.B“, für den Optimizer-Zustand."""
    return {f"{k}.{teil}": p for k, s in schichten.items() for teil, p in zip(("A", "B"), s.parameter())}


def speichern(pfad: Path, schichten: dict, info: dict | None = None, opt=None) -> Path:
    """LoRA als safetensors: <Name>.lora_A / .lora_B, dazu Rang, Alpha und `info` als Metadaten.
    Mit `opt` (AdamW) auch dessen Momente je Parameter (adam.<Name>.exp_avg / .exp_avg_sq) und den
    Schrittzähler – ein Zwischenstand setzt dann genau so fort, als wäre nie angehalten worden."""
    from safetensors.torch import save_file
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tensoren = {}
    for k, s in schichten.items():
        tensoren[f"{k}.lora_A"] = s.A.detach().float().cpu().contiguous()
        tensoren[f"{k}.lora_B"] = s.B.detach().float().cpu().contiguous()
    eins = next(iter(schichten.values()))
    meta = {"rang": str(eins.rang), "alpha": str(eins.alpha), **{k: str(v) for k, v in (info or {}).items()}}
    if opt is not None:
        for name, p in _param_namen(schichten).items():
            st = opt.state.get(p) or {}
            if "exp_avg" in st and "exp_avg_sq" in st:
                tensoren[f"adam.{name}.exp_avg"] = st["exp_avg"].detach().float().cpu().contiguous()
                tensoren[f"adam.{name}.exp_avg_sq"] = st["exp_avg_sq"].detach().float().cpu().contiguous()
                meta["adam_schritt"] = str(int(float(st["step"])))
    teil = pfad.with_suffix(".part")
    save_file(tensoren, str(teil), metadata=meta)
    teil.replace(pfad)
    return pfad


def laden(pfad: Path) -> tuple[dict[str, tuple], dict]:
    """{Name: (A, B, skala)} und Metadaten aus einer gespeicherten LoRA."""
    from safetensors import safe_open
    with safe_open(str(pfad), framework="pt", device="cpu") as f:
        meta = dict(f.metadata() or {})
        rang, alpha = int(meta.get("rang", 0)), float(meta.get("alpha", 0))
        namen = {k[:-len(".lora_A")] for k in f.keys() if k.endswith(".lora_A")}
        teile = {n: (f.get_tensor(n + ".lora_A"), f.get_tensor(n + ".lora_B")) for n in sorted(namen)}
    return {n: (a, b, alpha / (rang or a.shape[0])) for n, (a, b) in teile.items()}, meta


def optimierer_laden(pfad: Path, schichten: dict, opt) -> bool:
    """AdamW-Momente eines Zwischenstands übernehmen (nur Tensoren aus safetensors, kein Pickle).
    False, wenn der Zwischenstand keine enthält (ältere Fassung) – dann beginnt der Optimizer neu."""
    import torch
    from safetensors import safe_open
    with safe_open(str(pfad), framework="pt", device="cpu") as f:
        meta = dict(f.metadata() or {})
        if "adam_schritt" not in meta:
            return False
        schritt = torch.tensor(float(meta["adam_schritt"]))
        keys = set(f.keys())
        for name, p in _param_namen(schichten).items():
            m, v = f"adam.{name}.exp_avg", f"adam.{name}.exp_avg_sq"
            if m in keys and v in keys:
                opt.state[p] = {"step": schritt.clone(), "exp_avg": f.get_tensor(m).to(p.device, p.dtype),
                                "exp_avg_sq": f.get_tensor(v).to(p.device, p.dtype)}
    return True


def passt(sd: dict, lora: dict) -> str:
    """Leer, wenn jede Schicht der LoRA im Modell vorkommt und die Form stimmt – sonst der Grund."""
    fehlend, falsch = [], []
    for name, (a, b, _) in lora.items():
        if name not in sd:
            fehlend.append(name)
            continue
        basis = sd[name].basis if isinstance(sd[name], LoraLinear) else sd[name]
        try:
            form = _form(basis)
        except Exception:
            form = None
        if form is None or (b.shape[0], a.shape[1]) != tuple(form) or b.shape[1] != a.shape[0]:
            falsch.append(name)
    if not lora:
        return "die LoRA ist leer"
    if falsch:
        return f"{len(falsch)} Matrizen haben eine andere Form (z. B. {falsch[0]})"
    if len(fehlend) == len(lora):
        return "keine ihrer Schichten kommt in diesem Modell vor"
    return ""


def uebernehmen(schichten: dict, lora: dict) -> int:
    """Gespeicherte A/B (z. B. ein Zwischenstand) in vorhandene Schichten laden."""
    import torch
    n = 0
    with torch.no_grad():
        for name, (a, b, _) in lora.items():
            if name in schichten:
                schichten[name].A.copy_(a.to(schichten[name].A.device, schichten[name].A.dtype))
                schichten[name].B.copy_(b.to(schichten[name].B.device, schichten[name].B.dtype))
                n += 1
    return n


def einmischen(sd: dict, lora: dict, staerke: float = 1.0, dtype=None) -> int:
    """W ← W + stärke · skala · B·A für jede Schicht der LoRA; danach ist sd ein gewöhnlicher Gewichtssatz
    (dichte Matrizen in `dtype`), den der Motor wie immer (z. B. nach FP8) weiterverarbeitet."""
    import torch
    n = 0
    for name, (a, b, skala) in lora.items():
        if name not in sd:
            continue
        basis = sd[name]
        if isinstance(basis, LoraLinear):
            basis = basis.basis
        geraet = basis.device if isinstance(basis, torch.Tensor) else getattr(basis, "raw", a).device
        w = _entpackt(basis, torch.float32, geraet)
        w = w + (staerke * skala) * (b.to(geraet, torch.float32) @ a.to(geraet, torch.float32))
        sd[name] = w.to(dtype or (basis.dtype if isinstance(basis, torch.Tensor) else torch.bfloat16))
        n += 1
    return n


def flow_verlust(dit, x0, ctx, sigma: float, rauschen, **kw):
    """Flow-Matching-Verlust eines Schritts: x_σ = (1 − σ)·x0 + σ·ε, Ziel v = ε − x0."""
    import torch.nn.functional as F
    x = (1 - sigma) * x0 + sigma * rauschen
    v = dit(x, sigma * 1000.0, ctx, **kw)
    return F.mse_loss(v.float(), (rauschen - x0).float())
