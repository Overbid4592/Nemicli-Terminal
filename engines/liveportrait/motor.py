"""motor.py – ein Porträt bewegen: Kopf drehen, nicken, blinzeln, lächeln, zwinkern, sprechen.

Ohne Referenzvideo: Die Bewegung kommt aus Ausdruckswerten über die Zeit (Schlüsselbilder) –
dieselbe Steuerung wie die Regler der Original-Oberfläche (gradio_pipeline.py: Ausdruck über
feste Schlüsselpunkt-Verschiebungen, Augen über das Augen-Netz, Nähte über das Naht-Netz).
Das Originalbild wird nur gelesen; das Video landet im Profil der Persönlichkeit.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import gesicht, gewichte, netze, sprechen

# Werte und Grenzen wie die Regler des Originals (app.py)
NEUTRAL = {"nicken": 0.0, "drehen": 0.0, "neigen": 0.0, "laecheln": 0.0, "zwinkern": 0.0, "augenbrauen": 0.0,
           "blick_x": 0.0, "blick_y": 0.0, "augen": None, "mund": 0.0, "grinsen": 0.0, "spitzen": 0.0,
           "schmollen": 0.0, "x": 0.0, "y": 0.0, "zoom": 1.0}
GRENZEN = {"nicken": (-15, 15), "drehen": (-25, 25), "neigen": (-15, 15), "laecheln": (-0.3, 1.3),
           "zwinkern": (0, 39), "augenbrauen": (-30, 30), "blick_x": (-30, 30), "blick_y": (-63, 63),
           "augen": (0.0, 0.8), "mund": (-90, 120), "grinsen": (0, 15), "spitzen": (-20, 15),
           "schmollen": (-0.09, 0.09), "x": (-0.19, 0.19), "y": (-0.19, 0.19), "zoom": (0.9, 1.2)}
VRAM_MIN_MB = 2000             # weniger frei: auf der CPU rechnen
STAPEL = ((5000, 8), (3500, 4), (2500, 2), (0, 1))   # frei (MB) -> Bilder je Durchgang auf der Grafikkarte
FPS = 25
MAX_SEKUNDEN = 60
VORSCHAU_BILDER = 300          # längere Clips: in der Vorschau nur jedes n-te Bild (Größe und Speicher)


def _schritt(teil: torch.Tensor, kp: int, achse: int, faktor) -> None:
    teil[:, kp, achse] += faktor


def ausdruck_verschieben(delta: torch.Tensor, w: dict[str, torch.Tensor]) -> torch.Tensor:
    """Ausdrucks-Verschiebung je Bild (B, 21, 3) – Faktoren aus gradio_pipeline.update_delta_new_*."""
    d = delta.clone()
    bx, by = w["blick_x"], w["blick_y"]
    _schritt(d, 11, 0, torch.where(bx > 0, bx * 0.0007, bx * 0.001))
    _schritt(d, 15, 0, torch.where(bx > 0, bx * 0.001, bx * 0.0007))
    _schritt(d, 11, 1, by * -0.001)
    _schritt(d, 15, 1, by * -0.001)
    blinz = -by / 2
    _schritt(d, 11, 1, blinz * -0.001)
    _schritt(d, 13, 1, blinz * 0.0003)
    _schritt(d, 15, 1, blinz * -0.001)
    _schritt(d, 16, 1, blinz * 0.0003)
    s = w["laecheln"]
    for kp, achse, f in ((20, 1, -0.01), (14, 1, -0.02), (17, 1, 0.0065), (17, 2, 0.003), (13, 1, -0.00275),
                         (16, 1, -0.00275), (3, 1, -0.0035), (7, 1, -0.0035)):
        _schritt(d, kp, achse, s * f)
    z = w["zwinkern"]
    for kp, achse, f in ((11, 1, 0.001), (13, 1, -0.0003), (17, 0, 0.0003), (17, 1, 0.0003), (3, 1, -0.0003)):
        _schritt(d, kp, achse, z * f)
    e = w["augenbrauen"]
    hoch = e > 0
    _schritt(d, 1, 1, torch.where(hoch, e * 0.001, e * 0.0003))
    _schritt(d, 2, 1, torch.where(hoch, e * -0.001, e * -0.0003))
    _schritt(d, 1, 0, torch.where(hoch, torch.zeros_like(e), e * -0.001))
    _schritt(d, 2, 0, torch.where(hoch, torch.zeros_like(e), e * 0.001))
    _schritt(d, 19, 0, w["schmollen"])
    p = w["spitzen"]
    for kp, achse, f in ((14, 1, 0.001), (3, 1, -0.0005), (7, 1, -0.0005), (17, 2, -0.0005)):
        _schritt(d, kp, achse, p * f)
    g = w["grinsen"]
    for kp, achse, f in ((20, 2, -0.001), (20, 1, -0.001), (14, 1, -0.001)):
        _schritt(d, kp, achse, g * f)
    m = w["mund"]
    for kp, achse, f in ((19, 1, 0.001), (19, 2, 0.0001), (17, 1, -0.0001)):
        _schritt(d, kp, achse, m * f)
    _schritt(d, 5, 0, -w["x"])
    _schritt(d, 5, 1, w["y"])
    return d


def grad_aus_klassen(pred: torch.Tensor) -> torch.Tensor:
    """66 Klassen -> Grad (camera.headpose_pred_to_degree)."""
    idx = torch.arange(66, dtype=pred.dtype, device=pred.device)
    return (F.softmax(pred, dim=1) * idx).sum(1) * 3 - 97.5


def drehmatrix(pitch, yaw, roll) -> torch.Tensor:
    """(B,) Grad -> (B, 3, 3), wie camera.get_rotation_matrix (transponiert)."""
    x, y, z = (v / 180 * math.pi for v in (pitch, yaw, roll))
    eins, null = torch.ones_like(x), torch.zeros_like(x)
    rx = torch.stack([eins, null, null, null, x.cos(), -x.sin(), null, x.sin(), x.cos()], 1).view(-1, 3, 3)
    ry = torch.stack([y.cos(), null, y.sin(), null, eins, null, -y.sin(), null, y.cos()], 1).view(-1, 3, 3)
    rz = torch.stack([z.cos(), -z.sin(), null, z.sin(), z.cos(), null, null, null, eins], 1).view(-1, 3, 3)
    return (rz @ ry @ rx).transpose(1, 2)


# ---------------------------------------------------------------------------
# Schlüsselbilder und fertige Abläufe
# ---------------------------------------------------------------------------

def _weich(a: float) -> float:
    return a * a * (3 - 2 * a)


def verlauf(schluessel: list[dict], sekunden: float, fps: int, augen_quelle: float) -> list[dict]:
    """Schlüsselbilder [{"t": s, wert: …}] -> Werte je Bild. Nicht genannte Werte bleiben, wie sie
    zuletzt waren; dazwischen weich übergeblendet. augen None = wie im Bild."""
    zustand = dict(NEUTRAL, augen=augen_quelle)
    stuetzen = []
    for k in sorted(schluessel, key=lambda k: float(k.get("t", 0))):
        for name, wert in k.items():
            if name in NEUTRAL:
                lo, hi = GRENZEN[name]
                zustand[name] = augen_quelle if wert is None else min(hi, max(lo, float(wert)))
        stuetzen.append((float(k.get("t", 0)), dict(zustand)))
    if not stuetzen or stuetzen[0][0] > 0:
        stuetzen.insert(0, (0.0, dict(NEUTRAL, augen=augen_quelle)))
    n = max(1, round(sekunden * fps))
    aus = []
    for i in range(n):
        t = i / fps
        j = max(k for k in range(len(stuetzen)) if stuetzen[k][0] <= t)
        if j + 1 >= len(stuetzen):
            aus.append(dict(stuetzen[j][1]))
            continue
        (t0, a), (t1, b) = stuetzen[j], stuetzen[j + 1]
        f = _weich((t - t0) / max(t1 - t0, 1e-6))
        aus.append({k: a[k] + (b[k] - a[k]) * f for k in NEUTRAL})
    return aus


def _blinzeln(t: float) -> list[dict]:
    return [{"t": t, "augen": None}, {"t": t + 0.08, "augen": 0.0}, {"t": t + 0.2, "augen": None}]


def ablauf(name: str, sekunden: float, seed: int = 0) -> list[dict]:
    """Fertige Abläufe: lebendig · blinzeln · laecheln · zwinkern · nicken · kopfschuetteln · hallo."""
    r = random.Random(seed)
    d = max(1.0, float(sekunden))
    if name == "blinzeln":
        return _blinzeln(d * 0.4)
    if name == "laecheln":
        return [{"t": d * 0.15, "laecheln": 0.0}, {"t": d * 0.45, "laecheln": 0.9, "augen": None}]
    if name == "zwinkern":
        return [{"t": d * 0.3, "zwinkern": 0.0}, {"t": d * 0.42, "zwinkern": 25, "laecheln": 0.4},
                {"t": d * 0.62, "zwinkern": 0.0}]
    if name == "nicken":
        return [{"t": d * 0.1, "nicken": 0}, {"t": d * 0.3, "nicken": 8}, {"t": d * 0.5, "nicken": -3},
                {"t": d * 0.7, "nicken": 7}, {"t": d * 0.9, "nicken": 0}]
    if name == "kopfschuetteln":
        return [{"t": d * 0.1, "drehen": 0}, {"t": d * 0.3, "drehen": 12}, {"t": d * 0.5, "drehen": -12},
                {"t": d * 0.7, "drehen": 10}, {"t": d * 0.9, "drehen": 0}]
    if name == "hallo":
        return ([{"t": 0.2, "laecheln": 0.0}, {"t": d * 0.35, "laecheln": 0.8, "nicken": 6, "augenbrauen": 8},
                 {"t": d * 0.6, "nicken": -1, "augenbrauen": 0}] + _blinzeln(d * 0.75))
    # lebendig: leichtes Wiegen, ab und zu blinzeln, ein Hauch Lächeln
    k = [{"t": 0.0, "laecheln": 0.15}]
    t = 0.0
    while t < d:
        t += r.uniform(1.2, 2.2)
        k.append({"t": t, "drehen": r.uniform(-5, 5), "neigen": r.uniform(-2.5, 2.5), "nicken": r.uniform(-2.5, 2.5),
                  "blick_x": r.uniform(-6, 6)})
    t = r.uniform(0.6, 1.4)
    while t < d - 0.3:
        k += _blinzeln(t)
        t += r.uniform(2.2, 4.2)
    return k


ABLAEUFE = ("lebendig", "blinzeln", "laecheln", "zwinkern", "nicken", "kopfschuetteln", "hallo")

MUND_MAX = 55.0                # so weit öffnet der lauteste Laut den Mund (Regler „mund“, Grenze 120)
MUND_SCHWELLE = 0.08           # leiser als das (Anteil am Pegel): Mund zu
MUND_AUF, MUND_ZU = 0.7, 0.35  # wie schnell der Mund dem Pegel folgt: öffnen schneller als schließen


def mundwerte(wellen, abtastrate: int, fps: int = FPS) -> list[float]:
    """Mundöffnung je Bild aus der Lautstärke der Stimme (ohne eigenes Lippen-Modell): Pegel je Bild,
    auf die lauten Stellen bezogen, mit Ansprechen und Abklingen geglättet."""
    w = np.asarray(wellen, dtype=np.float32).ravel()
    n = max(1, math.ceil(len(w) * fps / abtastrate))
    schritt = abtastrate / fps
    teile = [w[int(i * schritt):int((i + 1) * schritt)] for i in range(n)]
    pegel = np.array([float(np.sqrt(np.mean(np.square(t)))) if t.size else 0.0 for t in teile], dtype=np.float32)
    laut = pegel[pegel > 0]
    bezug = float(np.percentile(laut, 90)) if len(laut) else 1.0
    x = np.clip(pegel / max(bezug, 1e-6), 0, 1)
    x[x < MUND_SCHWELLE] = 0.0
    aus, y = [], 0.0
    for v in x:
        y += (v - y) * (MUND_AUF if v > y else MUND_ZU)
        aus.append(round(y * MUND_MAX, 2))
    return aus


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

@dataclass
class Quelle:
    bild: torch.Tensor           # (1, 3, H, W) 0–1, ganzes Bild
    merkmal: torch.Tensor        # f_s (1, 32, 16, 64, 64)
    kp: torch.Tensor             # kanonische Punkte (1, 21, 3)
    ausdruck: torch.Tensor       # (1, 21, 3)
    skala: torch.Tensor          # (1, 1)
    t: torch.Tensor              # (1, 3)
    winkel: torch.Tensor         # (3,) pitch, yaw, roll in Grad
    x_s: torch.Tensor            # Punkte der Quelle (1, 21, 3)
    augen: tuple[float, float]   # offen links, rechts
    M_c2o: np.ndarray            # Ausschnitt -> Original (3×3)
    maske: torch.Tensor          # (1, 1, H, W) im Original
    alpha: torch.Tensor | None = None        # Deckkraft (1, 1, H, W) – None: Bild ohne Freistellung
    alpha_ausschnitt: torch.Tensor | None = None   # dieselbe im Gesichtsausschnitt (1, 1, 512, 512)
    aus_datei: bool = False      # Deckkraft stammt aus dem Bild selbst (PNG mit durchsichtigen Stellen)


class Motor:
    def __init__(self, geraet: str | None = None, basis: Path | None = None):
        self.geraet = geraet or self.geraet_waehlen()
        sds = gewichte.laden(basis)
        self.netze = netze.bauen()
        for teil in "FMWG":
            self.netze[teil].load_state_dict(sds[teil], strict=True)
        for n in netze.NAEHTE:
            self.netze[n].load_state_dict({k[len(n) + 1:]: v for k, v in sds["S"].items()
                                           if k.startswith(n + ".")}, strict=True)
        for m in self.netze.values():
            m.eval().to(self.geraet)
        lm = gewichte.landmark_pfad(basis)
        if lm is None:
            raise RuntimeError("landmark.onnx fehlt.")
        self.punkte = gesicht.Gesichtspunkte(lm)

    @staticmethod
    def geraet_waehlen() -> str:
        if not torch.cuda.is_available():
            return "cpu"
        try:
            frei, _ = torch.cuda.mem_get_info()
        except Exception:
            return "cpu"
        return "cuda" if frei >= VRAM_MIN_MB * 2 ** 20 else "cpu"

    def _halb(self):
        return torch.autocast("cuda", dtype=torch.float16) if self.geraet == "cuda" else torch.autocast(
            "cpu", enabled=False)

    @torch.no_grad()
    def vorbereiten(self, pfad, kasten=None, frei: bool = False) -> Quelle:
        """frei: Hintergrund durchsichtig – die Deckkraft kommt aus dem Bild (PNG mit Alpha) oder wird
        einmal freigestellt; beim Bewegen wandert sie mit dem Bewegungsfeld mit."""
        arr, alpha = gesicht.bild_und_alpha(pfad)
        aus_datei = alpha is not None
        if alpha is None and frei:
            alpha = freistellen(arr)
        pts = gesicht.finden(arr, self.punkte, kasten)
        mitte, groesse, winkel = gesicht.rechteck(pts, gesicht.QUELLE_SKALA, vy=gesicht.QUELLE_VY)
        M = gesicht.matrix(mitte, groesse, winkel, gesicht.QUELLE_GROESSE)
        bild = gesicht.als_tensor(arr, self.geraet)
        aus = gesicht.verziehen(bild, M, gesicht.QUELLE_GROESSE, gesicht.QUELLE_GROESSE)
        aus = F.interpolate(aus, size=(256, 256), mode="area").clamp(0, 1)
        with self._halb():
            merkmal = self.netze["F"](aus).float()
            info = {k: v.float() for k, v in self.netze["M"](aus).items()}
        winkel3 = torch.stack([grad_aus_klassen(info[k])[0] for k in ("pitch", "yaw", "roll")])
        kp, ausdruck = info["kp"].view(1, -1, 3), info["exp"].view(1, -1, 3)
        R = drehmatrix(*(winkel3[i:i + 1] for i in range(3)))
        x_s = (kp @ R + ausdruck) * info["scale"][..., None]
        x_s[:, :, :2] += info["t"][:, None, :2]
        M_c2o = np.linalg.inv(M)
        h, w = arr.shape[:2]
        maske = gesicht.verziehen(gesicht.maske().to(self.geraet), M_c2o, w, h)
        a = a_aus = None
        if alpha is not None:
            a = torch.from_numpy(alpha)[None, None].float().to(self.geraet)
            a_aus = gesicht.verziehen(a, M, gesicht.QUELLE_GROESSE, gesicht.QUELLE_GROESSE).clamp(0, 1)
        return Quelle(bild, merkmal, kp, ausdruck, info["scale"], info["t"], winkel3, x_s,
                      gesicht.augen_offen(pts), M_c2o, maske, a, a_aus, aus_datei)

    @torch.no_grad()
    def bilder(self, q: Quelle, werte: list[dict], mit_gesicht: bool = False, mit_alpha: bool = False):
        """Werte je Bild -> ganze Bilder (B, 3, H, W) 0–1; mit_gesicht: dazu der Gesichtsausschnitt (B, 3, 512, 512);
        mit_alpha (nur mit q.alpha): zuletzt die mitbewegte Deckkraft (B, 1, H, W)."""
        mit_alpha = mit_alpha and q.alpha is not None
        B = len(werte)
        w = {k: torch.tensor([float(v[k]) for v in werte], device=self.geraet) for k in NEUTRAL}
        R = drehmatrix(q.winkel[0] + w["nicken"], q.winkel[1] + w["drehen"], q.winkel[2] + w["neigen"])
        delta = ausdruck_verschieben(q.ausdruck.expand(B, -1, -1), w)
        t = q.t.expand(B, -1).clone()
        t[:, 2] = 0                                              # wie die Pipeline: keine Tiefenverschiebung
        x_d = w["zoom"][:, None, None] * q.skala[..., None] * (q.kp.expand(B, -1, -1) @ R + delta) + t[:, None, :]
        x_s = q.x_s.expand(B, -1, -1)
        offen = sum(q.augen) / 2
        if any(abs(float(v["augen"]) - offen) > 1e-4 for v in werte):
            # Augen-Netz relativ zum Bild selbst: bei „wie im Bild“ genau null, dazwischen ohne Sprung
            # (das Original wendet das Netz nur an, wenn der Wert abweicht – bei Übergängen springt das).
            quelle = torch.tensor(q.augen, device=self.geraet).expand(B, 2)
            ziel = torch.cat([w["augen"], torch.full_like(w["augen"], offen)])[:, None]      # erst Ziel, dann Bild
            ein = torch.cat([x_s.reshape(B, -1).repeat(2, 1), quelle.repeat(2, 1), ziel], dim=1)
            beide = self.netze["eye"](ein).view(2, B, -1, 3)
            x_d = x_d + beide[0] - beide[1]
        naht = self.netze["stitching"](torch.cat([x_s.reshape(B, -1), x_d.reshape(B, -1)], dim=1))
        x_d = x_d + naht[:, :63].view(B, -1, 3)
        x_d[..., :2] += naht[:, 63:65].view(B, 1, 2)
        with self._halb():
            aus = self.netze["W"](q.merkmal.expand(B, -1, -1, -1, -1), x_d, x_s, mit_fluss=mit_alpha)
            merkmal, fluss = aus if mit_alpha else (aus, None)
            gesichter = self.netze["G"](merkmal).float()
        H, W = q.bild.shape[-2:]
        zurueck = gesicht.verziehen(gesichter, q.M_c2o, W, H)
        ganz = (q.maske * zurueck + (1 - q.maske) * q.bild).clamp(0, 1)
        raus = [ganz, gesichter.clamp(0, 1)] if mit_gesicht else [ganz]
        if mit_alpha:
            raus.append(alpha_mitbewegen(q, fluss))
        return tuple(raus) if len(raus) > 1 else ganz


def alpha_mitbewegen(q: Quelle, fluss: torch.Tensor) -> torch.Tensor:
    """Deckkraft je Bild (B, 1, H, W): im Ausschnitt mit dem Bewegungsfeld des Warping-Netzes verschoben
    (dieselbe Bewegung, die das Gesicht macht), außerhalb wie im Bild – ohne zweites Modell, ohne Flackern."""
    B, h, w, _ = fluss.shape
    g = gesicht.QUELLE_GROESSE
    # Nur die Verschiebung übernehmen: Das Netz rechnet sein Gitter mit (n−1)-Teilung, sampelt aber mit
    # align_corners=False – für die 512er-Maske wäre das eine Verzerrung von 1/63 (~4 Pixel am Rand).
    eigen = netze.koordinatengitter(1, h, w, ref=fluss)[0, ..., :2]                  # (h, w, 2)
    schub = F.interpolate((fluss - eigen).permute(0, 3, 1, 2), size=(g, g), mode="bilinear", align_corners=False)
    achse = (torch.arange(g, dtype=fluss.dtype, device=fluss.device) * 2 + 1) / g - 1
    yy, xx = torch.meshgrid(achse, achse, indexing="ij")
    gitter = torch.stack([xx, yy], dim=-1)[None] + schub.permute(0, 2, 3, 1)
    a = F.grid_sample(q.alpha_ausschnitt.expand(B, -1, -1, -1), gitter,
                      mode="bilinear", padding_mode="border", align_corners=False)
    H, W = q.bild.shape[-2:]
    zurueck = gesicht.verziehen(a, q.M_c2o, W, H)
    return (q.maske * zurueck + (1 - q.maske) * q.alpha).clamp(0, 1)


FREISTELL_MODELL = "isnet-general-use"     # dasselbe Modell wie bei den Kugel-Bildern
_freistell_sitzung = None


def freistellen(arr: np.ndarray) -> np.ndarray:
    """Deckkraft (H, W) 0–1 eines RGB-Bildes über rembg – einmal je Porträt, nicht je Bild."""
    global _freistell_sitzung
    from PIL import Image
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise RuntimeError("rembg fehlt – damit wird der Hintergrund freigestellt.") from exc
    if _freistell_sitzung is None:
        _freistell_sitzung = new_session(FREISTELL_MODELL)
    maske = remove(Image.fromarray(arr), session=_freistell_sitzung, only_mask=True)
    return np.asarray(maske, dtype=np.float32) / 255.0


_MOTOR: Motor | None = None


def motor(geraet: str | None = None) -> Motor:
    global _MOTOR
    if _MOTOR is None or (geraet and _MOTOR.geraet != geraet):
        _MOTOR = Motor(geraet)
    return _MOTOR


def entladen() -> None:
    global _MOTOR
    _MOTOR = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def fehlt() -> str | None:
    """Grund, warum es nicht geht – oder None."""
    weg = gewichte.fehlt()
    if weg:
        return "Es fehlen in Models/LivePortrait: " + ", ".join(weg)
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return "onnxruntime fehlt."
    return None


VORSCHAU_KANTE = 480          # animiertes WebP fürs kleine Fenster
KUGEL_KANTE = 256             # Gesichtsausschnitt für die Kugel
FREI_KANTE = 384              # freigestellte Figur für die Kugel (durchsichtig, nicht rund)
FREI_RAND = 0.06              # Rand um die Figur (Anteil), damit Bewegung nicht abgeschnitten wird
GRUND = (0.08, 0.08, 0.1)     # MP4/Vorschau eines durchsichtigen Bildes: ruhiger dunkler Grund statt Randfarben
WEBP_METHODE = 0              # schnellste Kompression – method 4 kostete bei 7 s Sprache 13 s, kaum kleiner


def _uint8(b: torch.Tensor) -> np.ndarray:
    return (b.permute(0, 2, 3, 1) * 255).round().byte().cpu().numpy()


def _klein(bild: np.ndarray, kante: int):
    from PIL import Image
    h, w = bild.shape[:2]
    f = min(1.0, kante / max(h, w))
    return Image.fromarray(bild).resize((max(2, round(w * f)), max(2, round(h * f))), Image.LANCZOS)


def _figur_kasten(alpha: torch.Tensor) -> tuple[int, int, int, int]:
    """Quadrat um alles Sichtbare (l, o, r, u) mit etwas Rand – passt für alle Bilder des Clips."""
    a = alpha[0, 0] > 0.05
    H, W = a.shape
    if not bool(a.any()):
        return 0, 0, W, H
    zeilen, spalten = torch.nonzero(a.any(1)).flatten(), torch.nonzero(a.any(0)).flatten()
    o, u, l, r = int(zeilen[0]), int(zeilen[-1]) + 1, int(spalten[0]), int(spalten[-1]) + 1
    seite = int(max(r - l, u - o) * (1 + 2 * FREI_RAND))
    cx, cy = (l + r) // 2, (o + u) // 2
    return cx - seite // 2, cy - seite // 2, cx - seite // 2 + seite, cy - seite // 2 + seite


def _frei_bilder(ganz: torch.Tensor, alpha: torch.Tensor, kasten, kante: int) -> np.ndarray:
    """RGBA der Figur im Kasten (B, kante, kante, 4) uint8 – auf der Grafikkarte zugeschnitten und verkleinert;
    was über den Bildrand ragt, bleibt durchsichtig."""
    l, o, r, u = kasten
    H, W = ganz.shape[-2:]
    rgba = torch.cat([ganz * alpha, alpha], dim=1)                       # vormultipliziert: saubere Kanten
    rgba = F.pad(rgba, (max(0, -l), max(0, r - W), max(0, -o), max(0, u - H)))
    l2, o2 = max(0, l), max(0, o)
    rgba = rgba[..., o2:o2 + (u - o), l2:l2 + (r - l)]
    rgba = F.interpolate(rgba, size=(kante, kante), mode="area")
    a = rgba[:, 3:4]
    rgb = torch.where(a > 1e-4, rgba[:, :3] / a.clamp_min(1e-4), torch.zeros_like(rgba[:, :3]))
    return _uint8(torch.cat([rgb.clamp(0, 1), a], dim=1))


def webp_schreiben(bilder, pfad: Path, fps: float, kante: int) -> Path:
    """Animiertes WebP (Endlosschleife), längste Kante höchstens `kante` – Qt spielt es mit QMovie ab.
    bilder: Array (T, H, W, 3) oder schon verkleinerte PIL-Bilder."""
    folge = [b if not isinstance(b, np.ndarray) else _klein(b, kante) for b in bilder]
    folge[0].save(pfad, save_all=True, append_images=folge[1:], duration=round(1000 / fps), loop=0,
                  quality=82, method=WEBP_METHODE)
    return pfad


def animieren(bild, ablauf_oder_schluessel="lebendig", sekunden: float = 4.0, fps: int = FPS, ziel=None,
              geraet: str | None = None, kasten=None, on_status=None, mund: list[float] | None = None,
              ton=None, text: str = "", frei: bool = False, seed: int = 0) -> dict:
    """Porträt bewegen und als MP4 speichern, dazu zwei animierte WebP: <name>.webp (ganzes Bild, fürs
    kleine Fenster) und <name>_kugel.webp (Gesicht, für die Kugel). Rückgabe: pfad, vorschau, kugel,
    bilder, sekunden_rechnen, vram_mb, geraet.
    Sprechen: ton = (Wellenform, Abtastrate) – die Lippen formen die Laute (sprechen.lippen), der Kopf
    spricht mit (sprechen.koerpersprache, statt „lebendig“; `text` verrät Fragen), der Ton landet im MP4.
    mund = nur Mundöffnung je Bild (ältere Steuerung, ohne Formen).
    frei: Hintergrund durchsichtig – <name>_frei_kugel.webp zeigt die ganze Figur ohne Kreis; die Maske
    wandert mit der Bewegung mit."""
    from PIL import Image
    lippen = None
    if ton is not None and not mund:
        klang = sprechen.analyse(ton[0], ton[1], fps)
        lippen = sprechen.lippen(klang, fps)
        sekunden = len(lippen) / fps
        if ablauf_oder_schluessel == "lebendig":
            ablauf_oder_schluessel = sprechen.koerpersprache(klang, fps, text, seed)
    if mund:
        sekunden = len(mund) / fps
    if (grund := fehlt()):
        raise RuntimeError(grund)
    try:
        import hintergrund
        belegt = hintergrund.gpu_belegt()
    except Exception:
        belegt = None
    if belegt and geraet != "cpu":
        raise RuntimeError(f"Die Grafikkarte ist belegt (#{belegt['nr']} {belegt['titel']}).")
    start = time.perf_counter()
    m = motor(geraet)
    if m.geraet == "cuda":
        torch.cuda.reset_peak_memory_stats()
    q = m.vorbereiten(bild, kasten, frei=frei)
    frei = frei and q.alpha is not None
    mit_alpha = frei or q.aus_datei
    schluessel = (ablauf(ablauf_oder_schluessel, sekunden, seed) if isinstance(ablauf_oder_schluessel, str)
                  else list(ablauf_oder_schluessel))
    werte = verlauf(schluessel, min(float(sekunden), MAX_SEKUNDEN), fps, sum(q.augen) / 2)
    if mund:
        lo, hi = GRENZEN["mund"]
        for i, v in enumerate(werte):
            v["mund"] = min(hi, max(lo, v["mund"] + (mund[i] if i < len(mund) else 0.0)))
    if lippen:
        for v, lp in zip(werte, lippen):
            for name, wert in lp.items():
                lo, hi = GRENZEN[name]
                v[name] = min(hi, max(lo, v[name] + wert))
    kasten_frei = _figur_kasten(q.alpha) if frei else None
    stapel = 2
    if m.geraet == "cuda":
        frei_mb = torch.cuda.mem_get_info()[0] / 2 ** 20
        stapel = next(n for grenze, n in STAPEL if frei_mb >= grenze)
    jedes = max(1, math.ceil(len(werte) / VORSCHAU_BILDER))      # Vorschau: höchstens so viele Bilder
    vorschau_bilder, kugel_bilder, frei_bilder = [], [], []

    def folge():
        """Bilder entstehen erst, wenn der MP4-Schreiber sie holt – im Speicher bleibt nur die Vorschau."""
        nonlocal stapel
        i = 0
        while i < len(werte):
            try:
                b, g, *a = m.bilder(q, werte[i:i + stapel], mit_gesicht=True, mit_alpha=mit_alpha)
            except torch.OutOfMemoryError:
                if stapel == 1:
                    raise
                torch.cuda.empty_cache()
                stapel = max(1, stapel // 2)
                continue
            figur = _frei_bilder(b, a[0], kasten_frei, FREI_KANTE) if frei else None
            if q.aus_datei:                         # durchsichtiges Bild: fürs MP4 auf einen festen Grund
                grund = torch.tensor(GRUND, device=b.device).view(1, 3, 1, 1)
                b = b * a[0] + grund * (1 - a[0])
                g_alpha = gesicht.verziehen(a[0], np.linalg.inv(q.M_c2o), g.shape[-1], g.shape[-2])
                g = g * g_alpha + grund * (1 - g_alpha)
            ganz = _uint8(b)
            klein = _uint8(F.interpolate(g, size=(KUGEL_KANTE, KUGEL_KANTE), mode="area"))
            for k in range(len(ganz)):
                if (i + k) % jedes == 0:
                    vorschau_bilder.append(_klein(ganz[k], VORSCHAU_KANTE))
                    kugel_bilder.append(_klein(klein[k], KUGEL_KANTE))
                    if frei:
                        frei_bilder.append(Image.fromarray(figur[k], "RGBA"))
                yield ganz[k]
            i += len(ganz)
            if on_status:
                on_status(f"Porträt: Bild {min(i, len(werte))}/{len(werte)}")

    if ziel is None:
        import profilordner
        ziel = profilordner.video_pfad()
    import videodatei
    ziel = Path(ziel)
    videodatei.schreiben(folge(), ziel, fps, ton=ton)
    rechnen = time.perf_counter() - start
    vram = torch.cuda.max_memory_allocated() / 2 ** 20 if m.geraet == "cuda" else 0.0
    vorschau = webp_schreiben(vorschau_bilder, ziel.with_suffix(".webp"), fps / jedes, VORSCHAU_KANTE)
    kugel = webp_schreiben(kugel_bilder, ziel.with_name(ziel.stem + "_kugel.webp"), fps / jedes, KUGEL_KANTE)
    erg = {"pfad": str(ziel), "vorschau": str(vorschau), "kugel": str(kugel), "bilder": len(werte),
           "sekunden_rechnen": round(rechnen, 2),
           "vram_mb": round(vram), "geraet": m.geraet}
    if frei_bilder:                     # durchsichtige Figur; die Kugel zeigt sie statt des runden Gesichts
        erg["kugel"] = str(webp_schreiben(frei_bilder, ziel.with_name(ziel.stem + "_frei_kugel.webp"),
                                          fps / jedes, FREI_KANTE))
    if ton is not None:                 # Tonspur einzeln – die Kugel spielt sie zum WebP ab
        erg["ton"] = str(ton_schreiben(ton[0], ton[1], ziel.with_suffix(".wav")))
    return erg


def ton_schreiben(wellen, abtastrate: int, pfad: Path) -> Path:
    """Mono-WAV (16 Bit) neben dem Video."""
    import wave
    w = np.clip(np.asarray(wellen, dtype=np.float32).ravel(), -1, 1)
    with wave.open(str(pfad), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(int(abtastrate))
        f.writeframes((w * 32767).astype("<i2").tobytes())
    return pfad
