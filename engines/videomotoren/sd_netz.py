"""
sd_netz.py - Das Bildnetz von Stable Diffusion (SD 1.5 und SDXL) mit Zeit-Schichten für Video.

Das Bildnetz bleibt, wie es ist; zwischen seine Blöcke kommen Zeit-Schichten (AnimateDiff-Bauart), in denen
jeder Bildpunkt über die Bilder hinweg auf sich selbst schaut. Gerechnet werden alle Bilder eines Fensters
als ein Stapel (Bilder, Kanäle, Höhe, Breite).

Gewichte: das Bildnetz mit den Namen ganzer Checkpoints (`input_blocks.4.1.transformer_blocks.0.attn1.to_q`),
die Zeit-Schichten mit den Namen der Bewegungs-Module (`down_blocks.1.motion_modules.0.temporal_transformer…`).
Der Aufbau (Stufen, Kanäle, Blöcke, Köpfe) kommt aus den Namen und Formen der Datei.
"""

from __future__ import annotations

import math
import re

GN_GRUPPEN = 32
KOPF_BREITE = 64                 # SDXL: Breite je Kopf; SD 1.5 rechnet fest mit 8 Köpfen
ZEIT_KOEPFE = 8                  # Köpfe der Zeit-Schichten
ZEIT_LAENGE = 32                 # so viele Bilder kennen die Zeit-Schichten ohne Angabe in der Datei
ADM_TEIL = 256                   # SDXL: Breite je Zusatzwert (Größe, Ausschnitt, Zielgröße)


def erkennen(alle: dict) -> bool:
    """Bildnetz von SD 1.5 oder SDXL (Namen ohne Vorsatz)."""
    return bauart(alle) is not None


def bauart(alle: dict) -> str | None:
    """„sd15“ oder „sdxl“ nach der Breite des Text-Eingangs – sonst None."""
    form = alle.get("input_blocks.1.1.transformer_blocks.0.attn2.to_k.weight") \
        or alle.get("input_blocks.4.1.transformer_blocks.0.attn2.to_k.weight")
    if form is None or "time_embed.0.weight" not in alle or "out.2.weight" not in alle:
        return None
    if int(form[1]) == 768 and "label_emb.0.0.weight" not in alle:
        return "sd15"
    if int(form[1]) == 2048 and "label_emb.0.0.weight" in alle:
        return "sdxl"
    return None


_DIFFUSERS = ((".attn1.", ".attention_blocks.0."), (".attn2.", ".attention_blocks.1."), (".norm1.", ".norms.0."),
              (".norm2.", ".norms.1."), (".norm3.", ".ff_norm."))


def bewegung_name(k: str) -> str | None:
    """Name einer Zeit-Schicht in der Schreibweise von AnimateDiff – aus AnimateDiff (Original und
    diffusers) und Hotshot-XL. None: gehört nicht zu den Zeit-Schichten (Lagen-Tabellen, anderes)."""
    if k.endswith((".pe", ".positional_encoding")) or "pos_embed" in k:
        return None
    k = re.sub(r"\.temporal_attentions\.(\d+)\.", r".motion_modules.\1.temporal_transformer.", k)   # Hotshot-XL
    if ".motion_modules." not in k:
        return None
    if ".temporal_transformer." not in k:                                                          # diffusers
        k = re.sub(r"(\.motion_modules\.\d+)\.", r"\1.temporal_transformer.", k)
        for alt, neu in _DIFFUSERS:
            k = k.replace(alt, neu)
    return k


def fenster_laenge(alle: dict) -> int:
    """So viele Bilder kennen die Zeit-Schichten (Länge der Lagen-Tabelle in der Datei)."""
    for k, form in alle.items():
        if k.endswith((".pe", ".positional_encoding")) and len(form) == 3:
            return int(form[1])
    return ZEIT_LAENGE


def _zeit_namen(alle: dict) -> set:
    return {n for k in alle if (n := bewegung_name(k))}


def ist_bewegung(alle: dict) -> bool:
    """Datei mit Zeit-Schichten (Bewegungs-Modul)."""
    return any(k.endswith("temporal_transformer.proj_in.weight") for k in _zeit_namen(alle))


def stufen(alle: dict) -> int:
    """Auflösungs-Stufen eines Bildnetzes: SD 1.5 hat vier, SDXL drei."""
    return 1 + sum(1 for k in alle if re.fullmatch(r"input_blocks\.\d+\.0\.op\.weight", k))


def bewegung_passt(alle: dict, netz_stufen: int) -> bool:
    """Passen die Zeit-Schichten zum Bildnetz? Sie müssen genauso viele Stufen haben."""
    gefunden = {int(m.group(1)) for k in _zeit_namen(alle)
                if (m := re.match(r"down_blocks\.(\d+)\.motion_modules\.", k))}
    return bool(gefunden) and max(gefunden) + 1 == netz_stufen


# ===========================================================================
#  Bausteine
# ===========================================================================

def zeit_einbettung(t, breite: int):
    """Sinus-Einbettung (Kosinus zuerst), wie im Original."""
    import torch
    halb = breite // 2
    freq = torch.exp(-math.log(10000.0) * torch.arange(halb, dtype=torch.float32, device=t.device) / halb)
    arg = t.float()[:, None] * freq[None]
    return torch.cat([torch.cos(arg), torch.sin(arg)], dim=-1)


def _gn(x, sd, p, eps=1e-5):
    import torch.nn.functional as F
    return F.group_norm(x, GN_GRUPPEN, sd[p + ".weight"], sd[p + ".bias"], eps)


def _conv(x, sd, p, pad=1, schritt=1):
    import torch.nn.functional as F
    return F.conv2d(x, sd[p + ".weight"], sd[p + ".bias"], stride=schritt, padding=pad)


def _lin(x, sd, p):
    import torch.nn.functional as F
    return F.linear(x, sd[p + ".weight"], sd.get(p + ".bias"))


def _ln(x, sd, p):
    import torch.nn.functional as F
    return F.layer_norm(x, (x.shape[-1],), sd[p + ".weight"], sd[p + ".bias"], 1e-5)


def _aufmerksamkeit(x, kontext, sd, p, koepfe: int):
    import torch.nn.functional as F
    b, n, _ = x.shape
    q = _lin(x, sd, p + ".to_q").view(b, n, koepfe, -1).transpose(1, 2)
    k = _lin(kontext, sd, p + ".to_k").view(b, kontext.shape[1], koepfe, -1).transpose(1, 2)
    v = _lin(kontext, sd, p + ".to_v").view(b, kontext.shape[1], koepfe, -1).transpose(1, 2)
    a = F.scaled_dot_product_attention(q, k, v).transpose(1, 2).reshape(b, n, -1)
    return _lin(a, sd, p + ".to_out.0")


def _vorwaerts(x, sd, p):
    """FeedForward mit GEGLU."""
    import torch.nn.functional as F
    h, tor = _lin(x, sd, p + ".net.0.proj").chunk(2, dim=-1)
    return _lin(h * F.gelu(tor), sd, p + ".net.2")


def _res(x, emb, sd, p):
    import torch.nn.functional as F
    h = _conv(F.silu(_gn(x, sd, p + ".in_layers.0")), sd, p + ".in_layers.2")
    h = h + _lin(F.silu(emb), sd, p + ".emb_layers.1")[:, :, None, None]
    h = _conv(F.silu(_gn(h, sd, p + ".out_layers.0")), sd, p + ".out_layers.3")
    if p + ".skip_connection.weight" in sd:
        x = _conv(x, sd, p + ".skip_connection", pad=0)
    return x + h


def _bild_transformer(x, kontext, sd, p, koepfe: int):
    """Blöcke mit Selbst- und Text-Aufmerksamkeit über die Bildpunkte eines Bildes."""
    b, c, hh, ww = x.shape
    h = _gn(x, sd, p + ".norm", 1e-6)
    linear = sd[p + ".proj_in.weight"].dim() == 2
    if linear:
        h = _lin(h.flatten(2).transpose(1, 2), sd, p + ".proj_in")
    else:
        h = _conv(h, sd, p + ".proj_in", pad=0).flatten(2).transpose(1, 2)
    j = 0
    while f"{p}.transformer_blocks.{j}.norm1.weight" in sd:
        q = f"{p}.transformer_blocks.{j}"
        n = _ln(h, sd, q + ".norm1")
        h = h + _aufmerksamkeit(n, n, sd, q + ".attn1", koepfe)
        h = h + _aufmerksamkeit(_ln(h, sd, q + ".norm2"), kontext, sd, q + ".attn2", koepfe)
        h = h + _vorwaerts(_ln(h, sd, q + ".norm3"), sd, q + ".ff")
        j += 1
    if linear:
        h = _lin(h, sd, p + ".proj_out").transpose(1, 2).reshape(b, c, hh, ww)
    else:
        h = _conv(h.transpose(1, 2).reshape(b, c, hh, ww), sd, p + ".proj_out", pad=0)
    return x + h


_LAGEN: dict = {}


def zeit_lagen(laenge: int, breite: int, device, dtype):
    """Sinus-Lagen der Bilder im Fenster (1, Bilder, Breite), gemerkt."""
    import torch
    k = (laenge, breite, str(device), dtype)
    if k not in _LAGEN:
        if len(_LAGEN) > 24:
            _LAGEN.clear()
        ort = torch.arange(laenge, dtype=torch.float32)[:, None]
        teiler = torch.exp(torch.arange(0, breite, 2, dtype=torch.float32) * (-math.log(10000.0) / breite))
        pe = torch.zeros(1, laenge, breite)
        pe[0, :, 0::2] = torch.sin(ort * teiler)
        pe[0, :, 1::2] = torch.cos(ort * teiler)
        _LAGEN[k] = pe.to(device, dtype)
    return _LAGEN[k]


def _zeit_schicht(x, bilder: int, sd, p, staerke: float = 1.0):
    """Zeit-Schicht: Jeder Bildpunkt schaut über die Bilder des Fensters auf sich selbst."""
    p = p + ".temporal_transformer"
    bf, c, hh, ww = x.shape
    stapel = bf // bilder
    h = _gn(x, sd, p + ".norm", 1e-6)
    h = _lin(h.flatten(2).transpose(1, 2), sd, p + ".proj_in")              # (Stapel·Bilder, Punkte, c)
    punkte = h.shape[1]
    h = h.view(stapel, bilder, punkte, c).transpose(1, 2).reshape(stapel * punkte, bilder, c)
    lagen = zeit_lagen(bilder, c, h.device, h.dtype)
    j = 0
    while f"{p}.transformer_blocks.{j}.ff_norm.weight" in sd:
        q = f"{p}.transformer_blocks.{j}"
        a = 0
        while f"{q}.attention_blocks.{a}.to_q.weight" in sd:
            n = _ln(h, sd, f"{q}.norms.{a}") + lagen
            h = h + _aufmerksamkeit(n, n, sd, f"{q}.attention_blocks.{a}", ZEIT_KOEPFE)
            a += 1
        h = h + _vorwaerts(_ln(h, sd, q + ".ff_norm"), sd, q + ".ff")
        j += 1
    h = h.view(stapel, punkte, bilder, c).transpose(1, 2).reshape(bf, punkte, c)
    h = _lin(h, sd, p + ".proj_out").transpose(1, 2).reshape(bf, c, hh, ww)
    return x + h * staerke if staerke != 1.0 else x + h


# ===========================================================================
#  Das Netz
# ===========================================================================

class Netz:
    """Bildnetz + Zeit-Schichten auf dem Gerät. `sd`: Gewichte des Bildnetzes, `zeit`: der Zeit-Schichten
    (oder None – dann rechnet es einzelne Bilder)."""

    def __init__(self, sd: dict, zeit: dict | None = None, fenster_max: int = ZEIT_LAENGE):
        self.sd, self.zeit, self.fenster_max = sd, zeit or {}, fenster_max
        self.art = bauart({k: tuple(v.shape) for k, v in sd.items()})
        if self.art is None:
            raise RuntimeError("Kein Bildnetz von SD 1.5 oder SDXL.")
        self.breite = int(sd["time_embed.0.weight"].shape[1])
        self.eingaenge = self._zaehlen("input_blocks")
        self.ausgaenge = self._zaehlen("output_blocks")

    def _zaehlen(self, teil: str) -> int:
        return 1 + max(int(k.split(".")[1]) for k in self.sd if k.startswith(teil + "."))

    def _koepfe(self, kanaele: int) -> int:
        return 8 if self.art == "sd15" else kanaele // KOPF_BREITE

    def _zeit(self, x, bilder: int, name: str, staerke: float):
        if bilder > 1 and name + ".temporal_transformer.proj_in.weight" in self.zeit:
            return _zeit_schicht(x, bilder, self.zeit, name, staerke)
        return x

    def __call__(self, x, t, kontext, *, bilder: int = 1, zusatz=None, staerke: float = 1.0):
        """Rauschvorhersage. x: (Stapel·Bilder, 4, h, w), t: (Stapel·Bilder,) Zeitschritte 0…999,
        kontext: (Stapel·Bilder, 77, Breite), zusatz (SDXL): (Stapel·Bilder, 2816) gepoolter Text + Zusatzwerte."""
        sd = self.sd
        emb = zeit_einbettung(t, self.breite).to(x.dtype)
        emb = _lin(_silu(_lin(emb, sd, "time_embed.0")), sd, "time_embed.2")
        if "label_emb.0.0.weight" in sd:
            if zusatz is None:
                raise RuntimeError("SDXL braucht den gepoolten Text und die Zusatzwerte.")
            emb = emb + _lin(_silu(_lin(zusatz.to(x.dtype), sd, "label_emb.0.0")), sd, "label_emb.0.2")
        merker = []
        h = x
        stufe = lage = 0
        for i in range(self.eingaenge):
            p = f"input_blocks.{i}"
            if i == 0:
                h = _conv(h, sd, p + ".0")
            elif p + ".0.op.weight" in sd:
                h = _conv(h, sd, p + ".0.op", schritt=2)
                stufe, lage = stufe + 1, 0
            else:
                h = _res(h, emb, sd, p + ".0")
                if p + ".1.norm.weight" in sd:
                    h = _bild_transformer(h, kontext, sd, p + ".1", self._koepfe(h.shape[1]))
                h = self._zeit(h, bilder, f"down_blocks.{stufe}.motion_modules.{lage}", staerke)
                lage += 1
            merker.append(h)
        h = _res(h, emb, sd, "middle_block.0")
        h = _bild_transformer(h, kontext, sd, "middle_block.1", self._koepfe(h.shape[1]))
        h = self._zeit(h, bilder, "mid_block.motion_modules.0", staerke)
        h = _res(h, emb, sd, "middle_block.2")
        stufe = lage = 0
        for i in range(self.ausgaenge):
            p = f"output_blocks.{i}"
            import torch
            h = _res(torch.cat([h, merker.pop()], dim=1), emb, sd, p + ".0")
            n = 1
            if p + ".1.norm.weight" in sd:
                h = _bild_transformer(h, kontext, sd, p + ".1", self._koepfe(h.shape[1]))
                n = 2
            h = self._zeit(h, bilder, f"up_blocks.{stufe}.motion_modules.{lage}", staerke)
            lage += 1
            if f"{p}.{n}.conv.weight" in sd:
                import torch.nn.functional as F
                h = _conv(F.interpolate(h, scale_factor=2.0, mode="nearest"), sd, f"{p}.{n}.conv")
                stufe, lage = stufe + 1, 0
        return _conv(_silu(_gn(h, sd, "out.0")), sd, "out.2")


def _silu(x):
    import torch.nn.functional as F
    return F.silu(x)


def zusatz_sdxl(gepoolt, hoehe: int, breite: int, device, dtype):
    """SDXL-Zusatz (1, 2816): gepoolter Text (1280) + Größe, Ausschnitt (0, 0) und Zielgröße als Sinus-Werte."""
    import torch
    werte = torch.tensor([hoehe, breite, 0, 0, hoehe, breite], dtype=torch.float32, device=device)
    return torch.cat([gepoolt.reshape(1, -1).float().to(device),
                      zeit_einbettung(werte, ADM_TEIL).reshape(1, -1)], dim=1).to(dtype)
