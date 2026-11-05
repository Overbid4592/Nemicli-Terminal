"""
gemeinsam.py - Was alle Bild-Motoren teilen: Gerät und Rechengenauigkeit, Speicherplan,
Blöcke nachschieben, Flow-Sampler, Arbeitsspeicher-Ablage und Entladen nach Ruhe.

Speicherplan: Alle Gewichte liegen fest im Arbeitsspeicher. Passt das Netz ganz in den freien
Grafikspeicher, kommt es für das Bild ganz hinüber. Sonst bleiben so viele Blöcke auf der Karte,
wie Platz ist, und die übrigen werden je Schritt nachgeschoben – der nächste Block wandert schon,
während der aktuelle rechnet. Ohne CUDA rechnet alles auf der CPU in float32.
"""

from __future__ import annotations

import math
import threading
import time

try:
    import debugbericht as _DBG
except Exception:                                    # Tests ohne core/ im Pfad
    class _DBG:                                      # type: ignore[no-redef]
        @staticmethod
        def ereignis(*a, **k): pass

from .gewichte import groesse, zu

# Grafikspeicher, der neben den Gewichten frei bleiben muss (Aktivierungen, Attention, VAE)
RESERVE_GB = 2.5


# ===========================================================================
#  Gerät
# ===========================================================================

def geraet():
    """(device, dtype): CUDA mit bf16 ab Ampere, fp16 auf älteren Karten; sonst CPU mit float32."""
    import torch
    if torch.cuda.is_available():
        d = torch.device("cuda")
        return d, (torch.bfloat16 if torch.cuda.get_device_capability(d) >= (8, 0) else torch.float16)
    return torch.device("cpu"), torch.float32


def frei_bytes(device) -> int:
    """Freier Grafikspeicher in Bytes (CPU: unbegrenzt)."""
    import torch
    if torch.device(device).type != "cuda":
        return 1 << 62
    frei, _ = torch.cuda.mem_get_info(device)
    return int(frei)


def aufraeumen() -> None:
    import gc
    import torch
    gc.collect()
    if torch.cuda.is_available():
        try:
            import krea
            sperre = krea.gpu_sperre()
        except Exception:
            import contextlib
            sperre = contextlib.nullcontext()
        with sperre:
            torch.cuda.synchronize()
            torch.cuda.empty_cache()


# ===========================================================================
#  Blöcke: ganz auf der Karte oder nachgeschoben
# ===========================================================================

def _tensoren(obj):
    import torch
    from .gewichte import Lin
    if isinstance(obj, Lin):
        yield from obj.tensoren()
    elif isinstance(obj, torch.Tensor):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _tensoren(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _tensoren(v)


class Bloecke:
    """Die Blöcke eines Netzes für einen Durchlauf über die Karte.

    `bleiben` Blöcke liegen dauerhaft auf der Karte, die übrigen werden beim Durchlaufen
    auf einem Nebenstrom einen Block voraus kopiert und danach verworfen."""

    def __init__(self, ram: list, device, platz: int | None = None):
        import torch
        self.ram, self.device = ram, torch.device(device)
        n = len(ram)
        if self.device.type != "cuda":
            self.bleiben, self.dauer = n, list(ram)
            return
        je = [groesse(b) for b in ram]
        platz = frei_bytes(self.device) - int(RESERVE_GB * (1 << 30)) if platz is None else platz
        if sum(je) <= platz:
            k = n
        else:                                        # zwei Plätze für den rechnenden und den nächsten Block
            budget, k, summe = platz - 2 * max(je), 0, 0
            while k < n and summe + je[k] <= budget:
                summe += je[k]
                k += 1
        self.bleiben = k
        self.dauer = [zu(b, self.device) for b in ram[:k]]
        self._strom = torch.cuda.Stream(self.device) if k < n else None
        if k < n:
            _DBG.ereignis(f"Bildmotor: {k} von {n} Blöcken auf der Karte, Rest wird nachgeschoben")

    def _holen(self, i):
        import torch
        with torch.cuda.stream(self._strom):
            b = zu(self.ram[i], self.device)
            ev = torch.cuda.Event()
            ev.record(self._strom)
        return b, ev

    def __iter__(self):
        import torch
        yield from self.dauer
        n = len(self.ram)
        if self.bleiben >= n:
            return
        haupt = torch.cuda.current_stream(self.device)
        naechster = self._holen(self.bleiben)
        for i in range(self.bleiben, n):
            b, ev = naechster
            haupt.wait_event(ev)
            for t in _tensoren(b):
                t.record_stream(haupt)
            if i + 1 < n:
                naechster = self._holen(i + 1)
            yield b
            del b

    def __len__(self):
        return len(self.ram)


# ===========================================================================
#  Flow-Matching
# ===========================================================================

def sigmas_linear(schritte: int, shift: float):
    """linspace(1, 1/n, n) mit fester Zeitverschiebung, dazu 0 am Ende (diffusers FlowMatchEuler)."""
    import torch
    s = torch.linspace(1.0, 1.0 / schritte, schritte, dtype=torch.float64)
    s = shift * s / (1.0 + (shift - 1.0) * s)
    return torch.cat([s, torch.zeros(1, dtype=torch.float64)]).float()


def sigmas_flux(schritte: int, mu: float, sigma: float = 1.0, ende: float | None = None):
    """linspace(1, 1/n, n) mit Zeitverschiebung exp(mu) (dynamisch nach Bildgröße), dazu 0.
    `ende`: der letzte Schritt vor 0 wird auf diese Rauschstärke gestreckt (diffusers shift_terminal)."""
    import torch
    s = torch.linspace(1.0, 1.0 / schritte, schritte, dtype=torch.float64)
    s = math.exp(mu) / (math.exp(mu) + (1.0 / s - 1.0) ** sigma)
    if ende is not None:
        rest = 1.0 - s
        s = 1.0 - rest / (rest[-1] / (1.0 - ende))
    return torch.cat([s, torch.zeros(1, dtype=torch.float64)]).float()


def mu_linear(n_bild: int, basis_n: int = 256, max_n: int = 4096, basis: float = 0.5, hoch: float = 1.15) -> float:
    """Zeitverschiebung linear nach Bild-Token (diffusers calculate_shift)."""
    m = (hoch - basis) / (max_n - basis_n)
    return n_bild * m + (basis - m * basis_n)


def euler(geschwindigkeit, x, sigmas, on_status=None, name: str = "Bild"):
    """Flow-Euler: x_{i+1} = x_i + (s_{i+1} - s_i) * v(x_i, s_i). `geschwindigkeit(x, s)` -> v."""
    import torch
    n = len(sigmas) - 1
    for i in range(n):
        s, s_next = float(sigmas[i]), float(sigmas[i + 1])
        t0 = time.time()
        with torch.no_grad():
            v = geschwindigkeit(x, s).float()
        x = x + (s_next - s) * v
        if on_status:
            on_status(f"{name}: Schritt {i + 1}/{n} ({time.time() - t0:.1f}s)")
    return x


# ===========================================================================
#  Arbeitsspeicher-Ablage und Entladen nach Ruhe
# ===========================================================================

SPERRE = threading.RLock()
_RAM: dict = {}                       # art -> (schlüssel, objekt)


def aus_ram(art: str, schluessel, bauen):
    """Objekt aus der Ablage – beim ersten Mal gebaut und im Arbeitsspeicher festgehalten."""
    import torch
    eintrag = _RAM.get(art)
    if eintrag is not None and eintrag[0] == schluessel:
        return eintrag[1]
    _RAM.pop(art, None)
    aufraeumen()
    _DBG.ereignis(f"Bildmotor: {art} von der Platte")
    obj = zu(bauen(), "cpu", pin=torch.cuda.is_available())
    _RAM[art] = (schluessel, obj)
    return obj


def im_ram() -> list[str]:
    return sorted(_RAM)


def _host_leeren() -> None:
    """Gepinnten Arbeitsspeicher aus dem Zwischenlager von torch an Windows zurückgeben."""
    try:
        import torch
        leeren = getattr(torch._C, "_host_emptyCache", None)
        if leeren and torch.cuda.is_available():
            leeren()
    except Exception:
        pass


def freigeben(art: str) -> None:
    """Einen Eintrag der Ablage freigeben (das nächste `aus_ram` lädt ihn von der Platte)."""
    with SPERRE:
        if _RAM.pop(art, None) is None:
            return
        aufraeumen()
        _host_leeren()
    _DBG.ereignis(f"Bildmotor: {art} aus dem Arbeitsspeicher")


def entladen() -> None:
    """Alles aus dem Arbeitsspeicher; das nächste Bild lädt wieder von der Platte."""
    global _UHR
    with SPERRE:
        if _UHR is not None:
            _UHR.set()
            _UHR = None
        if not _RAM:
            return
        while _RAM:
            _RAM.popitem()
            time.sleep(0.02)
        aufraeumen()
        _host_leeren()
    _DBG.ereignis("Bildmotoren entladen")


_UHR: threading.Event | None = None


def uhr_neu(sekunden: float) -> None:
    """Nach `sekunden` ohne Bild (und ohne laufende Runde) alles entladen; <= 0: sofort."""
    global _UHR
    if _UHR is not None:
        _UHR.set()
    if sekunden <= 0:
        _UHR = None
        entladen()
        return
    halt = threading.Event()
    _UHR = halt

    def warten():
        try:
            import krea
            beschaeftigt, fremd = krea._beschaeftigt, krea._fremd_malt
        except Exception:
            beschaeftigt = fremd = lambda: False  # noqa: E731
        ruhig_seit = time.monotonic()
        while not halt.wait(min(5.0, sekunden)):
            if beschaeftigt():
                ruhig_seit = time.monotonic()
            elif fremd() or time.monotonic() - ruhig_seit >= sekunden:   # ein anderer Prozess malt: frei
                with SPERRE:
                    if not halt.is_set():
                        entladen()
                return

    threading.Thread(target=warten, daemon=True).start()


def uhr_halt() -> None:
    global _UHR
    if _UHR is not None:
        _UHR.set()
        _UHR = None
