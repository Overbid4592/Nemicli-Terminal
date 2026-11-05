"""
ltx2.py - LTX-2.x (Lightricks) als Video-Motor: Video mit Ton aus einem Prompt.

    Prompt -> Gemma 4 (alle Zustände) -> Projektion + Verbinder (Video, Ton) -> Video-und-Ton-Transformer
           -> Faltungs-VAE (Kacheln) + Ton-VAE -> Vocoder (48 kHz Stereo) -> MP4

„distilled“: 8 feste Rauschstufen ohne CFG. „dev“: Zeitplan nach Videogröße (letzter Schritt auf 0,1 gestreckt),
CFG für Bild und Ton gegen einen leeren Prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

from bildmotoren.gemeinsam import aufraeumen, aus_ram, freigeben, mu_linear, sigmas_flux
from bildmotoren.gewichte import namen, st_kopf

from . import ltx2_netz

TITEL = "LTX-2"
SYMBOL = "🎞"
DESTILLIERT = [1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875]
STANDARD_DEV = {"schritte": 30, "cfg": 3.0, "ton_cfg": 7.0}
TEXT_LAENGE = 1024
TON_JE_S = 16000 / 160 / 4             # Ton-Latents je Sekunde
ZEIT_DEV = {"basis_n": 1024, "max_n": 4096, "basis": 0.95, "hoch": 2.05, "ende": 0.1}

BEKANNT: dict = {}                      # Lightricks-Dateien sind zugangsbeschränkt: keine öffentliche Prüfsumme


def erkennen(alle: dict) -> bool:
    return ltx2_netz.erkennen(alle)


def text_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .gemma_text import ist_gemma4
    if not ist_gemma4(pfad):
        return False
    alle = namen(pfad)
    return all(f"text_embedding_projection.{s}_aggregate_embed.weight" in alle for s in ("video", "audio"))


def vae_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .vae_ltx2 import ist_ltx2_vae
    return ist_ltx2_vae(pfad)


def ton_passt(pfad: Path, dit: Path | None = None) -> bool:
    from .ton_ltx2 import ist_ltx2_ton
    return ist_ltx2_ton(pfad)


ZUBEHOER = {
    "text": "Gemma 4 mit LTX-Projektion (gemma4-12b-with-proj-ltx-2.5-bf16.safetensors)",
    "vae": "Video-VAE als Faltungs-Fassung (ltx-2.5-video-vae-conv-bf16.safetensors)",
    "ton": "Ton-VAE mit Vocoder (ltx-2.5-audio-vae-bf16.safetensors)",
}


def ist_destilliert(dit: Path) -> bool:
    return "distill" in Path(dit).name.lower()


def konfig(dit: Path) -> dict:
    """Köpfe, RoPE-Art aus dem Dateikopf (GGUF oder safetensors), sonst die Werte von LTX-2.5."""
    c = {}
    if Path(dit).suffix.lower() == ".gguf":
        try:
            from ggufengine.gguf import GGUFFile
            with GGUFFile(str(dit)) as gg:
                c = json.loads(gg.get("config") or "{}").get("transformer", {})
        except Exception:
            c = {}
    else:
        try:
            c = json.loads((st_kopf(dit).get("__metadata__") or {}).get("config") or "{}").get("transformer", {})
        except ValueError:
            c = {}
    return {"koepfe": int(c.get("num_attention_heads", 32)), "audio_koepfe": int(c.get("audio_num_attention_heads", 32)),
            "rope_type": c.get("rope_type", "split"),
            "verbinder_koepfe": int(c.get("connector_num_attention_heads", 32)),
            "audio_verbinder_koepfe": int(c.get("audio_connector_num_attention_heads", 32))}


def vorgaben(dit: Path) -> dict:
    v = {"schritte": len(DESTILLIERT), "groesse": (1280, 704), "fps": 24}
    if not ist_destilliert(dit):
        v.update(STANDARD_DEV)
    return v


# ===========================================================================
#  Ablauf
# ===========================================================================

def text_projiziert(prompts: list[str], teile: dict, device, dtype, on_status=None) -> list:
    """Prompts -> je (Video-Projektion, Ton-Projektion, Token-Zahl). Gemma wird danach ganz freigegeben –
    ein noch gehaltenes Video-Modell vorher auch: beide liegen nie zugleich im Arbeitsspeicher."""
    from . import gemma_text
    if on_status:
        on_status(f"{TITEL}: Text-Encoder …")
    freigeben("dit")
    gew = gemma_text.laden(teile["text"], dtype)
    tok = gemma_text.tokenizer(gew)
    pv, pa = (gew[s + "_proj"].zu(device) for s in ("video", "audio"))
    aus = []
    for p_ in prompts:
        ids = tok(p_.strip())[:TEXT_LAENGE]
        z = gemma_text.zustaende(gew, ids, device, dtype)
        aus.append((ltx2_netz.text_projektion(z, pv, pv.n_out, dtype).cpu(),
                    ltx2_netz.text_projektion(z, pa, pa.n_out, dtype).cpu(), len(ids)))
    gew = pv = pa = z = None
    aufraeumen()
    return aus


def verbinden(proj, dit_ram: dict, k: dict, device, dtype):
    """(Video-Projektion, Ton-Projektion, Token) -> Text für den Transformer (1024 Token je Strom)."""
    from bildmotoren.gewichte import zu
    xv, xa, n = proj
    V, A = zu(dit_ram["verbinder"]["video"], device), zu(dit_ram["verbinder"]["audio"], device)
    ctx = ltx2_netz.verbinder(V, xv.to(device), n, TEXT_LAENGE, k["verbinder_koepfe"], dtype, art=k["rope_type"])
    actx = ltx2_netz.verbinder(A, xa.to(device), n, TEXT_LAENGE, k["audio_verbinder_koepfe"], dtype, art=k["rope_type"])
    return ctx, actx


def bilder_zahl(sekunden: float, fps: int) -> int:
    """8k+1 Bilder, die der VAE passen."""
    return max(1, round(sekunden * fps / 8)) * 8 + 1


def malen(prompt: str, teile: dict, *, sekunden: float, breite: int, hoehe: int, fps: int, seed: int,
          device, dtype, einstellungen: dict, on_status=None, abbruch=None):
    """Rechnet Video und Ton. Gibt (Bilder (T, H, W, 3) uint8, Ton (2, Proben) oder None, Abtastrate) zurück."""
    import torch
    from . import ton_ltx2, vae_ltx2
    k = konfig(teile["dit"])
    v = vorgaben(teile["dit"])
    cfg = float(einstellungen.get("cfg") or v.get("cfg", 1.0))
    proj = text_projiziert([prompt, ""] if cfg > 1.0 else [prompt], teile, device, dtype, on_status)
    if on_status:
        on_status(f"{TITEL}: Modell laden …")
    ram = aus_ram("dit", teile["dit"], lambda: ltx2_netz.laden(teile["dit"], dtype))
    if ram["verbinder"]["video"] is None:
        raise RuntimeError("Die Modelldatei enthält keine Text-Verbinder (LTX-2.0 wird nicht unterstützt).")
    ctx, actx = verbinden(proj[0], ram, k, device, dtype)
    neg = verbinden(proj[1], ram, k, device, dtype) if len(proj) > 1 else None
    proj = None

    n_bilder = bilder_zahl(sekunden, fps)
    F, H, W = (n_bilder - 1) // 8 + 1, hoehe // 32, breite // 32
    M = round(n_bilder / fps * TON_JE_S)
    g = torch.Generator().manual_seed(seed)
    x = torch.randn((1, 128, F, H, W), generator=g).permute(0, 2, 3, 4, 1).reshape(F * H * W, 128).to(device)
    a = torch.randn((1, 8, M, 16), generator=g).transpose(1, 2).flatten(2, 3)[0].to(device)

    platz = einstellungen.get("platz")
    netz = ltx2_netz.Netz(ram, device, dtype, k, platz)
    if ist_destilliert(teile["dit"]):
        sig = torch.tensor(DESTILLIERT + [0.0])
    else:
        z = ZEIT_DEV
        sig = sigmas_flux(int(einstellungen.get("schritte") or v["schritte"]),
                          mu_linear(F * H * W, z["basis_n"], z["max_n"], z["basis"], z["hoch"]), ende=z["ende"])
    ton_cfg = float(einstellungen.get("ton_cfg") or v.get("ton_cfg", cfg))
    zustand = {"a": a}

    def schritt(x, s):
        vx, va = netz(x, zustand["a"], ctx, actx, s, (F, H, W), float(fps))
        if neg is not None:
            nx, na = netz(x, zustand["a"], neg[0], neg[1], s, (F, H, W), float(fps))
            vx, va = nx + cfg * (vx - nx), na + ton_cfg * (va - na)
        zustand["v_a"] = va
        return vx

    n = len(sig) - 1
    for i in range(n):
        if abbruch is not None and abbruch():
            raise RuntimeError("abgebrochen")
        s, s2 = float(sig[i]), float(sig[i + 1])
        with torch.no_grad():
            vx = schritt(x, s)
        x = x + (s2 - s) * vx
        zustand["a"] = zustand["a"] + (s2 - s) * zustand["v_a"]
        if on_status:
            on_status(f"{TITEL}: Schritt {i + 1}/{n}")
    a = zustand["a"]
    netz = ctx = actx = neg = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: Video auspacken …")
    vae = aus_ram("vae", teile["vae"], lambda: vae_ltx2.laden(teile["vae"]))
    lat = vae_ltx2.entnormen(vae, x.reshape(F, H, W, 128).permute(3, 0, 1, 2)[None].float().cpu())
    kachel = einstellungen.get("kacheln") or (16, 16, 16)
    video = vae_ltx2.dekodieren_kacheln(vae, lat, device, dtype, kachel=kachel, ueber=(2, 4, 4), on_status=on_status)
    bilder = ((video[0].permute(1, 2, 3, 0).clamp(-1, 1) + 1) * 127.5).round().to(torch.uint8).numpy()
    video = lat = None
    aufraeumen()

    ton, rate = None, 48000
    if einstellungen.get("ton", True):
        if on_status:
            on_status(f"{TITEL}: Ton …")
        from bildmotoren.gewichte import zu
        tvae = aus_ram("ton", teile["ton"], lambda: ton_ltx2.laden(teile["ton"]))
        karte = {"sd": zu(tvae["sd"], device), "cfg": tvae["cfg"]}
        al = ton_ltx2.entnormen(karte, a.float()).view(M, 8, 16).permute(1, 0, 2)[None]
        ton, rate = ton_ltx2.wellenform(karte, ton_ltx2.mel(karte, al))
        ton = ton[:, : int(rate * n_bilder / fps)].float().cpu().numpy()
        karte = None
        aufraeumen()
    return bilder, ton, rate
