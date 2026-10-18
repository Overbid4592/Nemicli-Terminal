"""Bausteine für TensorRT-Netze (gemeinsam für Chat, Bild und Video).

Zwei Eigenheiten von TensorRT-RTX 1.7 sind hier berücksichtigt:
* Umformen mit -1 nach einer Attention liefert an folgende Matrizen zum Teil falsche Längen –
  Formen stehen deshalb als Shape-Tensor ausdrücklich da (`form`, "T" = variable Länge).
* NVFP4 (Blackwell): Gewichte E2M1 mit Blockskala E4M3 je 16 Werte und globaler Skala,
  Eingänge dynamisch je Block mit fester globaler Skala aus der Einmessung.
"""

from __future__ import annotations

import numpy as np
import torch

FP4_GRENZEN = torch.tensor([0.25, 0.75, 1.25, 1.75, 2.5, 3.5, 5.0])


def fp4_packen(w: torch.Tensor):
    """NVFP4-Gewicht: (Codes zwei je Byte, erstes im niedrigen Halbbyte; Blockskalen E4M3; global)."""
    n, k = w.shape
    blk = w.float().reshape(n, k // 16, 16)
    amax = blk.abs().amax(-1)
    glob = max(float(amax.max()) / (6 * 448), 1e-12)
    s = (amax / 6 / glob).clamp(2.0 ** -9, 448).to(torch.float8_e4m3fn)
    werte = blk / (s.float() * glob).unsqueeze(-1)
    code = torch.bucketize(werte.abs(), FP4_GRENZEN).to(torch.uint8) | ((werte < 0).to(torch.uint8) << 3)
    code = code.reshape(n, k // 2, 2)
    return (code[..., 0] | (code[..., 1] << 4)).contiguous().numpy(), s.view(torch.uint8).numpy(), glob


class Netzbauer:
    """Hilfen rund um ein INetworkDefinition; `A` ist der Rechentyp der Aktivierungen (bf16)."""

    def __init__(self, trt, net):
        self.trt, self.net = trt, net
        self.halten: list = []                       # numpy-Puffer leben bis nach dem Bau
        self.A = trt.bfloat16
        self.T = None                                # Shape-Tensor der variablen Länge ("T")

    # -- Konstanten
    def konst(self, a: np.ndarray, typ=None, form=None, anzahl=None):
        self.halten.append(a)
        if typ is None:
            return self.net.add_constant(form if form is not None else a.shape, self.trt.Weights(a)).get_output(0)
        return self.net.add_constant(form, self.trt.Weights(typ, a.ctypes.data, anzahl)).get_output(0)

    def bf16(self, t: torch.Tensor, form=None):
        a = t.detach().to("cpu", torch.bfloat16).contiguous().view(torch.int16).numpy()
        return self.konst(a, self.trt.bfloat16, tuple(form or t.shape), t.numel())

    def f32(self, t, form=None):
        a = np.array(t.detach().cpu().numpy() if isinstance(t, torch.Tensor) else t, dtype=np.float32, copy=True)
        return self.konst(a, form=form if form is not None else a.shape)

    # -- Rechnen
    def cast(self, x, typ):
        return self.net.add_cast(x, typ).get_output(0)

    def ew(self, a, b, op):
        return self.net.add_elementwise(a, b, op).get_output(0)

    def mal(self, a, b):
        return self.ew(a, b, self.trt.ElementWiseOperation.PROD)

    def plus(self, a, b):
        return self.ew(a, b, self.trt.ElementWiseOperation.SUM)

    def minus(self, a, b):
        return self.ew(a, b, self.trt.ElementWiseOperation.SUB)

    def sigmoid(self, x):
        return self.net.add_activation(x, self.trt.ActivationType.SIGMOID).get_output(0)

    def silu(self, x):
        return self.mal(x, self.sigmoid(x))

    def gelu_tanh(self, x):
        return self.net.add_activation(x, self.trt.ActivationType.GELU_TANH).get_output(0)

    # -- Formen
    def form(self, *teile):
        """Form als Shape-Tensor; "T" ist die variable Länge."""
        stuecke = [self.T if x == "T" else self.konst(np.array([x], dtype=np.int64)) for x in teile]
        return self.net.add_concatenation(stuecke).get_output(0)

    def laenge_von(self, x, achse: int):
        """Shape-Tensor [n] der Achse `achse` von x (für "T")."""
        return self.net.add_slice(self.net.add_shape(x).get_output(0), (achse,), (1,), (1,)).get_output(0)

    def umform(self, x, form, perm=None, erst_perm=False):
        s = self.net.add_shuffle(x)
        if erst_perm:
            s.first_transpose = perm
        s.set_input(1, self.form(*form))
        if not erst_perm and perm is not None:
            s.second_transpose = perm
        return s.get_output(0)

    def scheibe(self, x, start: tuple, form: tuple):
        """Ausschnitt ab `start` mit Form `form` ("T" = variable Länge), Schritt 1."""
        s = self.net.add_slice(x, start, (1,) * len(start), (1,) * len(start))
        s.set_input(2, self.form(*form))
        return s.get_output(0)

    def stueck(self, x, achse: int, von: int, n: int):
        """Feste Scheibe [von, von+n) entlang `achse` (alle anderen Achsen ganz, keine variable Länge)."""
        start = [0] * len(x.shape)
        start[achse] = von
        groesse = list(x.shape)
        groesse[achse] = n
        return self.net.add_slice(x, tuple(start), tuple(groesse), (1,) * len(start)).get_output(0)

    # -- Normen
    def rms(self, x, w: torch.Tensor | None, eps: float):
        """RMSNorm über die letzte Achse in fp32 (x fp32, Ergebnis fp32); w=None: ohne Gewicht."""
        trt = self.trt
        rang = len(x.shape)
        m = self.net.add_reduce(self.mal(x, x), trt.ReduceOperation.AVG, 1 << (rang - 1), True).get_output(0)
        r = self.net.add_unary(self.net.add_unary(self.plus(m, self.f32(eps, form=(1,) * rang)),
                                                  trt.UnaryOperation.SQRT).get_output(0),
                               trt.UnaryOperation.RECIP).get_output(0)
        y = self.mal(x, r)
        if w is None:
            return y
        form = (1,) * (rang - 1) + (w.numel(),)
        return self.mal(y, self.f32(w.float().reshape(form), form=form))

    # -- Matrizen
    def fp4_gewicht(self, codes: np.ndarray, skalen: np.ndarray, glob: float, n: int, k: int):
        """NVFP4-Gewicht [n, k] als dequantisierter Tensor (TensorRT verschmilzt das zum FP4-Kern)."""
        trt = self.trt
        sdq = self.net.add_dequantize(self.konst(skalen, trt.fp8, (n, k // 16), n * k // 16),
                                      self.f32(glob, form=()), self.A).get_output(0)
        return self.net.add_dequantize(self.konst(codes, trt.fp4, (n, k), n * k), sdq, self.A).get_output(0)

    def fp4_eingang(self, x, amax: float):
        """Eingang dynamisch NVFP4 (Block 16 entlang der letzten Achse), globale Skala aus amax."""
        trt = self.trt
        ga = self.f32(max(amax, 1e-6) / (6 * 448), form=())
        dz = self.net.add_dynamic_quantize(x, len(x.shape) - 1, 16, trt.fp4, trt.fp8)
        dz.set_input(1, ga)
        xs = self.net.add_dequantize(dz.get_output(1), ga, self.A).get_output(0)
        return self.net.add_dequantize(dz.get_output(0), xs, self.A).get_output(0)

    def matmul_t(self, a, w):
        """a [..., k] @ w^T mit w [n, k] (2D, wird für 3D-Eingänge gesendet)."""
        if len(a.shape) == 3 and len(w.shape) == 2:
            w = self.net.add_shuffle(w)
            w.reshape_dims = (1,) + tuple(w.get_input(0).shape)
            w = w.get_output(0)
        trt = self.trt
        return self.net.add_matrix_multiply(a, trt.MatrixOperation.NONE, w, trt.MatrixOperation.TRANSPOSE).get_output(0)
