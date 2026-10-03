"""
Expert FFN of a mixture-of-experts layer, shared by all architectures.

The model computes its own routing (softmax, sigmoid, groups, extra scales ...) and passes
the chosen experts `idx` (L, k) with their weights `w` (L, k).  `Experten` then runs

    out[t] = sum_j w[t, j] * down_e( act(gate_e(x[t])) * up_e(x[t]) ),   e = idx[t, j]

for every storage form of the experts, each wrapped as a `_Teil`:

* PackedExperts (IQ block formats): one token through the own CUDA kernels / table
  lookups, longer inputs unpack the used experts in groups
* QuantLinear (int4 group format): stacked per part, the chosen expert is taken with
  index_select
* Linear (compute dtype): stacked into one tensor, likewise
* anything else (bf16 Linear ...): per expert, needs the choice on the host

One token runs without reading the choice on the host (CUDA-graph safe) when every part
supports it (`graphfaehig`).  Longer inputs sort the tokens by expert: one host read per
layer instead of one per expert.
"""
from __future__ import annotations

from typing import Callable, List, Optional

import torch

from ..qlinear import G, QuantLinear
from .common import Linear, PackedExperts, WeightLoader

AUSPACKEN_JE_SCHRITT = 32      # packed experts unpacked at once when reading longer inputs


class _Teil:
    """One expert tensor (gate, up, gate+up or down) of a layer."""
    graphfaehig = False

    def __init__(self, experten):
        self.experten = experten
        self.n_out, self.n_in = experten[0].n_out, experten[0].n_in

    def einzel(self, ids: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        """Experts ids (k,) on one shared input x (n_in,) -> (k, n_out) float32."""
        return self.je(ids, x.unsqueeze(0).expand(ids.numel(), -1))

    def je(self, ids: torch.Tensor, a: torch.Tensor) -> torch.Tensor:
        """Expert ids[j] on its own input a[j]: (k, n_in) -> (k, n_out) float32."""
        return torch.stack([self.experten[e](a[j:j + 1])[0].float() for j, e in enumerate(ids.tolist())])

    def gruppe(self, ids: List[int]) -> Callable:
        """For longer inputs: fn(j, rows) applies expert ids[j] to rows (n, n_in)."""
        return lambda j, xs: self.experten[ids[j]](xs).float()


class _StapelTeil(_Teil):
    """int4 QuantLinear experts, stacked so that the chosen one is picked on the GPU."""
    graphfaehig = True

    def __init__(self, experten: List[QuantLinear]):
        super().__init__(experten)
        self.stapel = []
        for m in range(len(experten[0].mats)):
            self.stapel.append((torch.stack([e.mats[m][0] for e in experten]),
                                torch.stack([e.mats[m][1] for e in experten])))
        for i, e in enumerate(experten):              # experts keep views: nothing is held twice
            e.mats = [(p[i], z[i]) for p, z in self.stapel]

    @staticmethod
    def passt(experten) -> bool:
        if not experten or not all(isinstance(e, QuantLinear) and e.b is None for e in experten):
            return False
        return len({len(e.mats) for e in experten}) == 1

    def _mm(self, e: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        y = None
        for p, z in self.stapel:
            part = torch._weight_int4pack_mm(x, p.index_select(0, e)[0], G, z.index_select(0, e)[0])
            y = part if y is None else y + part
        return y

    def je(self, ids, a):
        a = a.to(torch.bfloat16)
        return torch.cat([self._mm(ids[j:j + 1], a[j:j + 1]) for j in range(ids.numel())]).float()

    def gruppe(self, ids):
        # small matrices: one kernel call over all rows beats QuantLinear's slicing / unpacking
        return lambda j, xs: self.experten[ids[j]]._mm(xs.to(torch.bfloat16)).float()


class _DichtTeil(_Teil):
    """Experts as plain matrices (Linear, compute dtype), stacked into one (n, out, in) tensor."""
    graphfaehig = True

    def __init__(self, experten: List[Linear]):
        super().__init__(experten)
        self.w = torch.stack([e.w for e in experten])
        for i, e in enumerate(experten):
            e.w = self.w[i]

    @staticmethod
    def passt(experten) -> bool:
        return bool(experten) and all(isinstance(e, Linear) and e.b is None for e in experten) \
            and len({(e.w.shape, e.w.dtype, e.w.device) for e in experten}) == 1

    def je(self, ids, a):
        w = self.w.index_select(0, ids)
        return torch.bmm(a.to(w.dtype).unsqueeze(1), w.transpose(1, 2))[:, 0].float()

    def gruppe(self, ids):
        return lambda j, xs: (xs.to(self.w.dtype) @ self.w[ids[j]].t()).float()


class _GepacktTeil(_Teil):
    """Experts in an IQ block format (PackedExperts)."""
    graphfaehig = True

    def __init__(self, experten: PackedExperts):
        self.experten = experten
        self.n_out, self.n_in = experten.n_out, experten.n_in

    def einzel(self, ids, x):
        return self.experten.matvec(ids, x)

    def je(self, ids, a):
        return self.experten.matvec_je(ids, a)

    def gruppe(self, ids):
        w = self.experten.weights(torch.tensor(ids, device=self.experten.raw.device))
        return lambda j, xs: (xs.to(w.dtype) @ w[j].t()).float()


def _teil(experten) -> _Teil:
    if isinstance(experten, PackedExperts):
        return _GepacktTeil(experten)
    if _StapelTeil.passt(experten):
        return _StapelTeil(experten)
    if _DichtTeil.passt(experten):
        return _DichtTeil(experten)
    return _Teil(experten)


class Experten:
    """gate/up (separate or fused as [gate | up]) and down of all experts of one layer, each
    with an optional bias per expert (`ffn_*_exps.bias`).  `glu(gate, up)` combines both
    projections (default act(gate) * up)."""

    def __init__(self, ld: WeightLoader, prefix: str, akt: Callable[[torch.Tensor], torch.Tensor],
                 glu: Optional[Callable[[torch.Tensor, torch.Tensor], torch.Tensor]] = None):
        self.akt = akt
        self.glu = glu
        if ld.has(prefix + "ffn_gate_up_exps.weight"):
            self.gate_up = _teil(ld.experts(prefix + "ffn_gate_up_exps.weight"))
            self.gate = self.up = None
        else:
            self.gate_up = None
            self.gate = _teil(ld.experts(prefix + "ffn_gate_exps.weight"))
            self.up = _teil(ld.experts(prefix + "ffn_up_exps.weight"))
        self.down = _teil(ld.experts(prefix + "ffn_down_exps.weight"))
        teile = [t for t in (self.gate_up, self.gate, self.up, self.down) if t is not None]
        self.n_exp = len(self.down.experten)
        self.bias = {x: ld.norm(prefix + f"ffn_{x}_exps.bias") for x in ("gate_up", "gate", "up", "down")
                     if ld.has(prefix + f"ffn_{x}_exps.bias")}
        self.graphfaehig = all(t.graphfaehig for t in teile)

    def __len__(self) -> int:
        return self.n_exp

    def pruefen(self) -> bool:
        """Same expert count in every part; gate/up widths match the input of down."""
        teile = [t for t in (self.gate_up, self.gate, self.up) if t is not None]
        if any(len(t.experten) != self.n_exp for t in teile):
            return False
        n_ff = self.down.n_in
        breite = {"gate_up": 2 * n_ff, "gate": n_ff, "up": n_ff, "down": self.down.n_out}
        if any(tuple(b.shape) != (self.n_exp, breite[x]) for x, b in self.bias.items()):
            return False
        if self.gate_up is not None:
            return self.gate_up.n_out == 2 * n_ff and self.gate_up.n_in == self.down.n_out
        return self.gate.n_out == self.up.n_out == n_ff and self.gate.n_in == self.up.n_in == self.down.n_out

    def _gu(self, h_gate: torch.Tensor, h_up: torch.Tensor) -> torch.Tensor:
        return self.glu(h_gate, h_up) if self.glu is not None else self.akt(h_gate) * h_up

    def _b(self, teil: str, y: torch.Tensor, e) -> torch.Tensor:
        """Bias of part `teil` for expert(s) e (int or (k,) tensor) added to y."""
        b = self.bias.get(teil)
        if b is None:
            return y
        return y + (b[e] if isinstance(e, int) else b.index_select(0, e))

    def __call__(self, x: torch.Tensor, idx: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
        """x (L, E) -> (L, E) float32; idx (L, k) expert ids, w (L, k) their weights."""
        L, E = x.shape
        if L == 1 and self.graphfaehig:
            ids = idx[0]
            if self.gate_up is not None:
                g, u = self._b("gate_up", self.gate_up.einzel(ids, x[0]), ids).chunk(2, dim=-1)
            else:
                g = self._b("gate", self.gate.einzel(ids, x[0]), ids)
                u = self._b("up", self.up.einzel(ids, x[0]), ids)
            y = self._b("down", self.down.je(ids, self._gu(g, u)), ids)          # (k, E)
            return (y * w[0].float().unsqueeze(-1)).sum(0, keepdim=True)
        # tokens sorted by expert: one host read (the counts) per layer
        flach = idx.reshape(-1)
        folge = flach.sort(stable=True)[1]
        zeile = folge // idx.shape[1]
        xs = x[zeile]
        ys = torch.empty(xs.shape[0], E, device=x.device, dtype=torch.float32)
        anzahl = torch.bincount(flach, minlength=self.n_exp).tolist()
        benutzt = [e for e, n in enumerate(anzahl) if n]
        anfang = [0] * self.n_exp
        pos = 0
        for e in range(self.n_exp):
            anfang[e], pos = pos, pos + anzahl[e]
        for a in range(0, len(benutzt), AUSPACKEN_JE_SCHRITT):
            ids = benutzt[a:a + AUSPACKEN_JE_SCHRITT]
            down = self.down.gruppe(ids)
            if self.gate_up is not None:
                gu = self.gate_up.gruppe(ids)
            else:
                gate, up = self.gate.gruppe(ids), self.up.gruppe(ids)
            for j, e in enumerate(ids):
                sl = slice(anfang[e], anfang[e] + anzahl[e])
                if self.gate_up is not None:
                    g, u = self._b("gate_up", gu(j, xs[sl]), e).chunk(2, dim=-1)
                else:
                    g, u = self._b("gate", gate(j, xs[sl]), e), self._b("up", up(j, xs[sl]), e)
                ys[sl] = self._b("down", down(j, self._gu(g, u)), e)
            del down
        # back to (token, slot) order and summed there: a fixed order, unlike atomic index_add_
        rueck = torch.empty_like(ys)
        rueck[folge] = ys
        return (rueck.view(L, -1, E) * w.float().unsqueeze(-1)).sum(1)
