"""
sd.py - Stable Diffusion als Bild-Motor: SDXL und SD 1.5 aus einem ganzen Checkpoint (Bildnetz, CLIP und VAE
in einer .safetensors-Datei, z. B. CyberRealistic XL).

    Prompt -> CLIP-L (+ CLIP-G bei SDXL), lange Prompts in Stücken zu 75 Token
           -> Bildnetz mit Rauschvorhersage, DPM++ 2M mit Karras-Rauschstärken, CFG
           -> VAE (8× größer, 4 Latent-Kanäle) -> PNG

Bildnetz, Text-Encoder und VAE rechnen die Bausteine des SD-Video-Motors (videomotoren/sd_netz, sd_text,
sd_vae) – hier ohne Zeit-Schichten, ein Bild.
Die Gegen-Seite von CFG rechnet immer mit Text: mindestens dem festen Gegen-Prompt der Bildsperre.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from .gemeinsam import aufraeumen, aus_ram
from .gewichte import namen

TITEL = "Stable Diffusion"
SYMBOL = "🟫"
SCHRITTE = (4, 80)
NEGATIV = True                               # nimmt einen Gegen-Prompt (bildmotoren.generate)
RASTER = 64                                  # Seitenlängen: Vielfache davon (Latent durch 8 und Netz durch 4)
STANDARD = {"sdxl": {"schritte": 28, "groesse": (1024, 1024), "cfg": 5.0},
            "sd15": {"schritte": 25, "groesse": (512, 768), "cfg": 7.0}}
ZEITSCHRITTE = 1000
BETA = (0.00085, 0.012)                      # „scaled linear“ wie im Original
RHO = 7.0                                    # Karras
STUECKE = 6                                  # bis zu 6 × 75 Token Prompt (Charakter-Datei + Szene)

BEKANNT = {
    # civitai.com · CyberRealistic XL v10.0 (Modellversion 2840768)
    "CyberRealisticXLPlay_V10.0_FP16.safetensors":
        (6938041288, "fd5e870b5bbce4bddeb64f4bb8e49c57f84ab793c0262a503f0123be435e667d"),
}

TOKENIZER = {"tokenizer_clip": "vocab.json + merges.txt"}
ZUBEHOER = {
    "text": "CLIP – steckt im ganzen Checkpoint (eine reine Bildnetz-Datei reicht nicht)",
    "vae": "VAE – steckt im ganzen Checkpoint (eine reine Bildnetz-Datei reicht nicht)",
}


# ===========================================================================
#  Erkennen, Zubehör
# ===========================================================================

def art(alle: dict) -> str | None:
    """„sdxl“ oder „sd15“ nach den Namen des Bildnetzes (ohne Vorsatz)."""
    from videomotoren import sd_netz
    return sd_netz.bauart(alle)


def erkennen(alle: dict) -> bool:
    return art(alle) is not None


def art_von(dit: Path) -> str:
    from .gewichte import ohne_praefix
    return art(ohne_praefix(namen(dit))[0]) or "sdxl"


def vorgaben(dit: Path) -> dict:
    return dict(STANDARD[art_von(dit)])


def text_passt(pfad: Path, dit: Path | None = None) -> bool:
    return False                             # CLIP und VAE kommen aus dem Checkpoint (eigene_teile)


vae_passt = text_passt


def eigene_teile(dit: Path) -> dict:
    """Text-Encoder und VAE aus dem Checkpoint selbst – fehlt einer, bleibt er leer (missing_reason nennt ihn)."""
    from videomotoren import sd_text
    alle = namen(dit)
    text = sd_text.vorsatz_l(alle) is not None and (art_von(dit) == "sd15" or sd_text.hat_g(alle))
    vae = "first_stage_model.decoder.conv_in.weight" in alle
    return {"text": dit if text else None, "vae": dit if vae else None}


# ===========================================================================
#  Rauschplan und Sampler
# ===========================================================================

def rauschstaerken_alle():
    """Rauschstärke je Zeitschritt 0…999 (aufsteigend)."""
    import torch
    betas = torch.linspace(BETA[0] ** 0.5, BETA[1] ** 0.5, ZEITSCHRITTE, dtype=torch.float64) ** 2
    anteil = torch.cumprod(1.0 - betas, dim=0)
    return ((1.0 - anteil) / anteil) ** 0.5


def karras(schritte: int, alle) -> list[float]:
    """Karras-Rauschstärken vom größten zum kleinsten Wert des Modells, dazu 0 am Ende."""
    lo, hi = float(alle[0]) ** (1 / RHO), float(alle[-1]) ** (1 / RHO)
    n = max(1, schritte - 1)
    return [(hi + i / n * (lo - hi)) ** RHO for i in range(schritte)] + [0.0]


def zeitschritt(sigma: float, alle) -> float:
    """Rauschstärke -> Zeitschritt (stetig, linear zwischen den Log-Rauschstärken)."""
    import torch
    log = torch.log(alle)
    s = math.log(sigma)
    i = int(torch.searchsorted(log, torch.tensor(s, dtype=log.dtype)).clamp(1, len(log) - 1))
    w = (s - float(log[i - 1])) / (float(log[i]) - float(log[i - 1]))
    return min(max(i - 1 + w, 0.0), float(len(log) - 1))


def dpmpp_2m(entrauschen, x, sigmas: list[float], on_status=None, name: str = TITEL):
    """DPM++ 2M: `entrauschen(x, sigma)` -> geschätztes sauberes Latent."""
    import torch
    alt = None
    n = len(sigmas) - 1
    for i in range(n):
        s, s2 = sigmas[i], sigmas[i + 1]
        t0 = time.time()
        with torch.no_grad():
            d = entrauschen(x, s).float()
        if s2 == 0.0:
            x = d
        else:
            h = math.log(s) - math.log(s2)
            if alt is not None:
                r = (math.log(sigmas[i - 1]) - math.log(s)) / h
                d_mix = (1 + 1 / (2 * r)) * d - (1 / (2 * r)) * alt
            else:
                d_mix = d
            x = (s2 / s) * x - math.expm1(-h) * d_mix
        alt = d
        if on_status:
            on_status(f"{name}: Schritt {i + 1}/{n} ({time.time() - t0:.1f}s)")
    return x


# ===========================================================================
#  Text
# ===========================================================================

def _text_laden(dit: Path, sd_art: str) -> dict:
    from videomotoren import sd_text
    return {"l": sd_text.laden_l(dit), "g": sd_text.laden_g(dit) if sd_art == "sdxl" else None}


def text(clip: dict, stuecke: list[list[int]], sd_art: str, device, dtype):
    """Stücke -> (Kontext (1, 77·Stücke, Breite), gepoolt (1280,) aus dem ersten Stück oder None)."""
    import torch
    from videomotoren import sd_text
    kontexte, gepoolt = [], None
    for ids in stuecke:
        if sd_art == "sdxl":
            k, p = sd_text.text_sdxl(clip["l"], clip["g"], ids, device, dtype)
            gepoolt = p if gepoolt is None else gepoolt
        else:
            k = sd_text.text_sd15(clip["l"], ids, device, dtype)
        kontexte.append(k)
    return torch.cat(kontexte, dim=1), gepoolt


def _einstellung(schluessel: str, standard):
    try:
        import config
        wert = config.load().get(schluessel)
    except Exception:
        wert = None
    return standard if wert in (None, "") else wert


# ===========================================================================
#  Stil-Vorlagen (Format der Forge/A1111-styles.csv: name, prompt, negative_prompt)
# ===========================================================================

VORLAGEN_DATEI = "sd_vorlagen.json"
VORLAGEN_MAX = 2000
VORLAGE_TEXT_MAX = 4000
CSV_MAX_BYTES = 4 << 20
NEGATIV_MAX = 4000


def _vorlagen_pfad() -> Path:
    try:
        from paths import DATEN
    except Exception:                                    # Selbsttest ohne Bootstrap
        DATEN = Path(__file__).resolve().parents[2]
    return Path(DATEN) / VORLAGEN_DATEI


def vorlagen() -> list[dict]:
    """[{name, prompt, negativ}] in der Reihenfolge der Datei."""
    import json
    try:
        roh = json.loads(_vorlagen_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [v for v in roh if isinstance(v, dict) and v.get("name")] if isinstance(roh, list) else []


def aktive() -> list[str]:
    """Namen der eingeschalteten Vorlagen (Config `bild_sd_vorlagen`), nur vorhandene."""
    namen_ = {v["name"] for v in vorlagen()}
    roh = _einstellung("bild_sd_vorlagen", [])
    return [n for n in (roh if isinstance(roh, list) else []) if n in namen_]


def vorlage_schalten(name: str, an: bool) -> list[str]:
    import config
    liste = [n for n in aktive() if n != name] + ([name] if an and any(v["name"] == name for v in vorlagen()) else [])
    config.update(bild_sd_vorlagen=liste)
    return liste


def vorlagen_einlesen(pfad: Path) -> int:
    """Eine styles.csv (Forge/A1111) übernehmen: gleichnamige werden ersetzt, neue angehängt. Gibt die Zahl zurück."""
    import csv
    import io
    import json
    from atomar import text_schreiben
    pfad = Path(pfad)
    if pfad.suffix.lower() != ".csv" or not pfad.is_file():
        raise ValueError("bitte eine .csv-Datei (styles.csv aus Forge oder A1111) wählen")
    if pfad.stat().st_size > CSV_MAX_BYTES:
        raise ValueError("die Datei ist zu groß für eine Vorlagen-Liste")
    text = pfad.read_text(encoding="utf-8-sig", errors="replace")
    zeilen = list(csv.DictReader(io.StringIO(text)))
    if not zeilen or "name" not in zeilen[0]:
        raise ValueError("keine Vorlagen gefunden (erwartet: Spalten name, prompt, negative_prompt)")
    kurz = lambda s: " ".join(str(s or "").split())[:VORLAGE_TEXT_MAX]       # noqa: E731
    neu = {}
    for z in zeilen:
        name = kurz(z.get("name"))[:120]
        if name:
            neu[name] = {"name": name, "prompt": kurz(z.get("prompt")), "negativ": kurz(z.get("negative_prompt"))}
    alt = [v for v in vorlagen() if v["name"] not in neu]
    alle = (alt + list(neu.values()))[:VORLAGEN_MAX]
    text_schreiben(_vorlagen_pfad(), json.dumps(alle, ensure_ascii=False, indent=1))
    return len(neu)


def anwenden(prompt: str, negativ: str, namen_: list[str] | None = None) -> tuple[str, str]:
    """Eingeschaltete Vorlagen auf Prompt und Gegen-Prompt: `{prompt}` in der Vorlage wird ersetzt, sonst angehängt."""
    nach_name = {v["name"]: v for v in vorlagen()}
    for name in (aktive() if namen_ is None else namen_):
        v = nach_name.get(name)
        if v is None:
            continue
        if v.get("prompt"):
            prompt = v["prompt"].replace("{prompt}", prompt) if "{prompt}" in v["prompt"] \
                else ", ".join(t for t in (prompt, v["prompt"]) if t)
        if v.get("negativ"):
            negativ = ", ".join(t for t in (negativ, v["negativ"]) if t)
    return prompt, negativ


# ===========================================================================
#  Ein Bild
# ===========================================================================

def malen(prompt: str, teile: dict, *, schritte: int, breite: int, hoehe: int, seed: int,
          device, dtype, on_status=None, cfg: float | None = None, negativ: str | None = None,
          neg: str | None = None):
    """`negativ`: der festgenagelte Gegen-Prompt (ohne: aus der Config), `neg`: der zum einzelnen Bild
    (von der KI) – beide gelten, dazu die der eingeschalteten Stil-Vorlagen."""
    import torch
    from videomotoren import sd_netz, sd_vae, sdvideo
    from .textenc import clip_stuecke
    dit = teile["dit"]
    sd_art = art_von(dit)
    v = STANDARD[sd_art]
    cfg = float(_einstellung(f"bild_sd_{sd_art}_cfg", v["cfg"]) if cfg is None else cfg)
    negativ = str(_einstellung("bild_sd_negativ", "") if negativ is None else negativ).strip()
    negativ = ", ".join(t for t in (" ".join(str(neg or "").split())[:NEGATIV_MAX], negativ) if t)
    prompt, negativ = anwenden(prompt, negativ)
    import bildsperre
    prompt, negativ = bildsperre.pruefen(prompt, negativ)   # auch Stil-Vorlagen gehen durch die Sperre
    negativ = bildsperre.gegenprompt(negativ)                # fester Teil, nicht abschaltbar
    breite, hoehe = max(RASTER, breite // RASTER * RASTER), max(RASTER, hoehe // RASTER * RASTER)

    if on_status:
        on_status(f"{TITEL}: Text lesen …")
    kodieren = clip_stuecke(teile["tokenizer_clip"], STUECKE)
    clip = aus_ram("text", (dit, "sd"), lambda: _text_laden(dit, sd_art))
    kontext, gepoolt = text(clip, kodieren(prompt), sd_art, device, dtype)
    leer_k = leer_z = None
    if cfg > 1.0:
        leer_k, leer_p = text(clip, kodieren(negativ), sd_art, device, dtype)
        if sd_art == "sdxl":
            leer_z = sd_netz.zusatz_sdxl(leer_p, hoehe, breite, device, dtype)
    zusatz = sd_netz.zusatz_sdxl(gepoolt, hoehe, breite, device, dtype) if sd_art == "sdxl" else None
    clip = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: Modell in den Grafikspeicher …")
    ram = aus_ram("dit", dit, lambda: sdvideo.netz_laden(dit))
    netz = sd_netz.Netz({k: t.to(device, dtype) for k, t in ram.items()})
    alle = rauschstaerken_alle()
    sigmas = karras(schritte, alle)

    def entrauschen(x, sigma):
        ein = (x / (sigma * sigma + 1.0) ** 0.5).to(dtype)
        t = torch.tensor([zeitschritt(sigma, alle)], device=device)
        e = netz(ein, t, kontext, zusatz=zusatz).float()
        if leer_k is not None:
            u = netz(ein, t, leer_k, zusatz=leer_z).float()
            e = u + cfg * (e - u)
        return x - sigma * e

    rauschen = torch.randn((1, 4, hoehe // 8, breite // 8), generator=torch.Generator().manual_seed(seed))
    x = dpmpp_2m(entrauschen, rauschen.to(device) * (1.0 + sigmas[0] ** 2) ** 0.5, sigmas, on_status)
    netz = kontext = leer_k = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: VAE …")
    vae = aus_ram("vae", dit, lambda: sd_vae.laden(dit))
    bild = sd_vae.auspacken(vae, x.float(), sd_art, device)[0]
    aufraeumen()
    return bild
