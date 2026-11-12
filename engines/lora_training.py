"""
lora_training.py - Charakter-LoRA aus den Bildern einer Persönlichkeit, für das Video-Modell (Wan 2.2 5B) oder
den Bild-Motor (Krea 2).

Ablauf: Bilder aus Profile/<Name>/Bilder (nachgemalte Gesichter bevorzugt) -> je Bild ein Latent (VAE des
Motors) und eine Beschreibung (Name + Kern der Charakter-Datei) -> Flow-Matching-Training der LoRA auf dem
eingefrorenen Modell -> Profile/<Name>/LoRA/wan22-5b.safetensors bzw. krea2.safetensors.
Zwischenstände liegen in Profile/<Name>/LoRA/Training/; ein abgebrochenes Training läuft dort weiter.
"""
from __future__ import annotations

import math
import random
import threading
import time
from pathlib import Path

ZIEL = "wan22-5b"
GROESSE = (832, 1216)                                  # wie das Video (hochkant)
KREA_ZIEL = "krea2"
KREA_GROESSE = (640, 896)                              # hochkant, kleiner als beim Malen: passt mit Lernen in 13 GB
# Formate statt Beschneiden: jedes Bild lernt in dem Format, das seinem Seitenverhältnis am nächsten kommt (alle
# mit etwa gleich vielen Bildpunkten, damit der Speicher wie bei KREA_GROESSE reicht).
KREA_FORMATE = {"hoch": (640, 896), "quer": (896, 640), "quadrat": (768, 768)}
MOTOREN = {"video": ZIEL, "bild": KREA_ZIEL}
VORGABEN = {"schritte": 1000, "rang": 16, "lr": 1e-4, "bilder": 40, "zwischenstand": 100, "shift": 5.0}
ANHALTEN = threading.Event()                           # gesetzt -> Zwischenstand sichern und aufhören
_LAEUFT = threading.Lock()


def laeuft() -> bool:
    return _LAEUFT.locked()


def bilder_auswaehlen(name: str | None = None, hoechstens: int = VORGABEN["bilder"]) -> list[Path]:
    """Die neuesten Bilder einer Persönlichkeit; gibt es ein nachgemaltes Gesicht, dann das – aus
    Gesichtszuege/<name>.png oder (ältere Bilder) `…_gesicht.png` daneben."""
    import profilordner
    ordner = profilordner.ordner("Bilder", name, anlegen=False)
    if not ordner.is_dir():
        return []
    zuege = ordner / "Gesichtszuege"
    alle = sorted(ordner.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    gesichter = {p.name.replace("_gesicht.png", ".png") for p in alle if p.name.endswith("_gesicht.png")}
    wahl = []
    for p in alle:
        if p.name.endswith("_gesicht.png"):
            wahl.append(p)
        elif (zuege / p.name).is_file():
            wahl.append(zuege / p.name)
        elif p.name not in gesichter:
            wahl.append(p)
    return wahl[:hoechstens]


def beschreibung(name: str | None = None) -> str:
    """Name der Persönlichkeit + Kern ihrer Charakter-Datei – derselbe Satz für alle Bilder, damit die LoRA
    das Aussehen mit dem Namen verbindet."""
    import profilordner
    try:
        import charakter
        kern = (charakter.laden(name) or {}).get("kern", "")
    except Exception:
        kern = ""
    anzeige = name or profilordner.aktiv()
    return f"{anzeige}. {kern}".strip() if kern else f"A photo of {anzeige}."


def krea_format(pfad: Path) -> tuple[int, int]:
    """(W, H) aus KREA_FORMATE, dessen Seitenverhältnis dem Bild am nächsten kommt; unlesbar: hochkant."""
    try:
        from PIL import Image
        with Image.open(pfad) as im:
            w, h = im.size
    except Exception:
        return KREA_FORMATE["hoch"]
    return min(KREA_FORMATE.values(), key=lambda f: abs(math.log((w / max(h, 1)) / (f[0] / f[1]))))


def lernsatz(text: str, bildtext: str) -> str:
    """Auslöser-Satz + Satz zum einzelnen Bild (Kleidung, Pose, Ort, Licht)."""
    return f"{text.strip()} {bildtext.strip()}".strip()


def _texte_kennung(texte: list[str]) -> str:
    import hashlib
    return hashlib.sha256("\n".join(texte).encode("utf-8")).hexdigest()[:16]


def _bild_tensor(pfad: Path, W: int, H: int):
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    with Image.open(pfad) as im:
        im = ImageOps.fit(im.convert("RGB"), (W, H), Image.LANCZOS)
        arr = np.asarray(im).astype("float32") / 127.5 - 1.0
    return torch.from_numpy(arr).permute(2, 0, 1)


def daten(bilder: list[Path], text: str, t: dict, device, dtype, on_status=None) -> tuple[list, object]:
    """Latents (48, 1, h, w) aller Bilder und der Text-Kontext – nacheinander auf der Karte, dann im RAM."""
    import torch
    import video as V
    W, H = GROESSE
    vae = V.WanVAE22({k: v.to(device) for k, v in V._lade_safetensors(t["vae"], dtype).items()})
    latents = []
    with torch.no_grad():
        for i, p in enumerate(bilder):
            if on_status:
                on_status(f"LoRA: Bilder einpacken {i + 1}/{len(bilder)}")
            latents.append(vae.encode(_bild_tensor(p, W, H).to(device)[:, None]).cpu())
    del vae
    V._frei()
    if on_status:
        on_status("LoRA: Beschreibung lesen (umT5) …")
    tok = V._tokenizer()
    enc = V.UmT5(V._kopie(V._lade_gguf(t["text"], dtype), device))
    with torch.no_grad():
        ctx = enc(tok(text), dtype).float().cpu()
    del enc
    V._frei()
    return latents, ctx


def _stand_pfad(name: str | None, ziel: str = ZIEL) -> Path:
    import profilordner
    return profilordner.ordner("LoRA", name) / "Training" / f"{ziel}_stand.safetensors"


def motor_ziel(motor: str | None) -> str:
    """"video" (Vorgabe) -> wan22-5b, "bild" -> krea2; sonst ValueError."""
    m = str(motor or "video").strip().lower()
    m = {"wan": "video", "krea": "bild", "krea2": "bild", "bilder": "bild", "image": "bild"}.get(m, m)
    if m not in MOTOREN:
        raise ValueError(f"Unbekannter Motor '{motor}' – video oder bild")
    return MOTOREN[m]


def stand(name: str | None = None, motor: str = "video") -> dict:
    """Was es gibt: fertige LoRA (mit Metadaten), angefangenes Training, Bild-Vorschlag."""
    import lora as L
    import profilordner
    ziel = motor_ziel(motor)
    aus = {"laeuft": laeuft(), "lora": None, "zwischenstand": None, "ziel": ziel,
           "bilder": [str(p) for p in bilder_auswaehlen(name)], "beschreibung": beschreibung(name)}
    fertig = profilordner.lora_pfad(ziel, name)
    if fertig.is_file():
        aus["lora"] = {"pfad": str(fertig), **L.laden(fertig)[1]}
    zw = _stand_pfad(name, ziel)
    if zw.is_file():
        aus["zwischenstand"] = {"pfad": str(zw), **L.laden(zw)[1]}
    return aus


def _bilder_kennung(bilder: list[Path]) -> str:
    """Kurzer Fingerabdruck der Bildauswahl (Namen, Größe, Änderungszeit) – passt ein Zwischenstand noch?"""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(str(b) for b in bilder):
        try:
            st = Path(p).stat()
            h.update(f"{p}|{st.st_size}|{st.st_mtime_ns}\n".encode())
        except OSError:
            h.update(f"{p}|-\n".encode())
    return h.hexdigest()[:16]


def trainieren(name: str | None = None, *, bilder: list[Path] | None = None, schritte: int | None = None,
               rang: int | None = None, lr: float | None = None, modell: str | None = None, ziel: Path | None = None,
               text: str | None = None, on_status=None, abbruch=None, motor: str = "video",
               fortschritt=None, vorschau: dict | None = None, ausloeser: str = "",
               texte: list[str] | None = None) -> Path:
    """Trainiert die LoRA und gibt den Pfad zurück. Blockiert (Stunden möglich). Ein Zwischenstand gleicher
    Einstellungen wird fortgesetzt; `abbruch()` -> Zwischenstand speichern und RuntimeError.
    `motor`: "video" (Wan) oder "bild" (Krea 2). Nur Krea 2: `fortschritt(dict)` bekommt je Schritt
    {schritt, schritte, prozent, verlust, rest_s}; `vorschau` = {alle, ordner, text, seed} malt alle `alle`
    Schritte ein Testbild mit dem LoRA-Stand; `ausloeser` landet in den Metadaten der LoRA; `texte` (je Bild ein
    Satz, gleiche Reihenfolge wie `bilder`) kommt hinter `text` – dann lernt der Auslöser nur, was alle Bilder
    teilen, statt Kleidung und Hintergrund mit."""
    arbeit = _trainieren_krea if motor_ziel(motor) == KREA_ZIEL else _trainieren
    extra = ({"fortschritt": fortschritt, "vorschau": vorschau, "ausloeser": ausloeser, "texte": texte}
             if arbeit is _trainieren_krea else {})
    if not _LAEUFT.acquire(blocking=False):
        raise RuntimeError("Es läuft schon ein LoRA-Training")
    meldung = None
    try:
        return arbeit(name, bilder, schritte, rang, lr, modell, ziel, text, on_status, abbruch, **extra)
    except Exception as e:
        # Nur den Text behalten: der Traceback hielte DiT, LoRA-Schichten, Optimierer und Latents
        # (mehrere GB VRAM) fest, bis irgendwann die Speicherbereinigung läuft.
        meldung = str(e) or type(e).__name__
    finally:
        _LAEUFT.release()
    gpu_aufraeumen()
    raise RuntimeError(meldung)


def gpu_aufraeumen() -> None:
    """Nach Abbruch oder Fehler: Python-Reste einsammeln und den CUDA-Zwischenspeicher leeren."""
    import gc
    gc.collect()
    try:
        import gpuzustand
        if gpuzustand.kaputt():
            return
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _fortsetzen(stand: Path, schichten: dict, opt, info: dict, on_status) -> int:
    """Passt ein Zwischenstand zu diesen Einstellungen, seine Gewichte und Optimizer-Momente übernehmen.
    Gibt den Schritt zurück, bei dem es weitergeht (0 = neu)."""
    import lora as L
    if not stand.is_file():
        return 0
    alt, meta = L.laden(stand)
    gleich = all(meta.get(k) == str(info[k]) for k in ("modell", "rang", "schritte", "bilder_kennung", "lr",
                                                        "beschreibung") + (("texte_kennung",) if "texte_kennung"
                                                                           in info else ()))
    if gleich and set(alt) == set(schichten):
        L.uebernehmen(schichten, alt)
        L.optimierer_laden(stand, schichten, opt)
        return int(meta.get("schritt", 0))
    if on_status:
        on_status("LoRA: Zwischenstand passt nicht zu diesen Einstellungen (Modell, Bilder, Text, Rang, "
                  "Lernrate oder Schritte) – beginne neu")
    return 0


def _sparsam(bloecke: list, z, vec, rope):
    """Transformer-Blöcke mit Nachrechnen statt Aufheben: gespeichert wird nur der Eingang je Block,
    seine Zwischenwerte entstehen beim Rückweg neu (Speicher eines Blocks statt aller)."""
    from torch.utils.checkpoint import checkpoint
    for blk in bloecke:
        z = checkpoint(blk, z, vec, rope, use_reentrant=False)
    return z


VORSCHAU_SCHRITTE = 8                                  # Testbild während des Trainings: wenige Schritte genügen


def _vorschau_malen(dit, txt, t, device, dtype, mean, std, W: int, H: int, seed: int, ziel: Path) -> Path:
    """Ein Testbild mit dem LoRA-Stand mitten im Training (gleicher Seed jedes Mal: man sieht sie lernen).
    Die VAE kommt nur kurz auf die Karte; passt sie nicht mehr daneben, dekodiert die CPU."""
    import torch
    import krea as K
    from PIL import Image
    with torch.no_grad():
        rauschen = torch.randn((1, 16, H // 8, W // 8), generator=torch.Generator().manual_seed(seed)).to(device)
        sig = K._sigmas(VORSCHAU_SCHRITTE, K.DEFAULTS["shift"]).to(device)
        lat = K._sample(dit, rauschen * sig[0], txt, sig, "euler", seed)
        del rauschen, sig
        z = (lat * std + mean)
        try:
            vae = K._VAEDecoder(t["vae"], device, dtype)
            bild = vae(z)
        except torch.cuda.OutOfMemoryError:
            vae = None
            K._frei()
            vae = K._VAEDecoder(t["vae"], torch.device("cpu"), torch.float32)
            bild = vae(z.float().cpu())
        del vae, lat, z
    arr = ((bild[0].permute(1, 2, 0).float().cpu().numpy() + 1.0) * 127.5).round().clip(0, 255).astype("uint8")
    del bild
    K._frei()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(ziel)
    return ziel


def _trainieren_krea(name, bilder, schritte, rang, lr, modell, ziel, text, on_status, abbruch,
                     fortschritt=None, vorschau: dict | None = None, ausloeser: str = "",
                     texte: list[str] | None = None) -> Path:
    import torch
    import torch.nn.functional as F
    import krea as K
    import lora as L
    import profilordner

    schritte = int(schritte or VORGABEN["schritte"])
    rang = int(rang or VORGABEN["rang"])
    lr = float(lr or VORGABEN["lr"])
    bilder = list(bilder) if bilder is not None else bilder_auswaehlen(name)
    if len(bilder) < 4:
        raise RuntimeError(f"Zu wenige Bilder für eine LoRA ({len(bilder)}, mindestens 4)")
    texte = [str(t or "") for t in texte] if texte else None
    if texte is not None and len(texte) != len(bilder):
        raise RuntimeError(f"{len(texte)} Bildtexte für {len(bilder)} Bilder")
    if (grund := K.missing_reason()):
        raise RuntimeError(grund)
    modelle = K.discover()
    ref = K._modell(modell, modelle)
    pfad, t = modelle[ref], K.teile()
    ziel = Path(ziel) if ziel else profilordner.lora_pfad(KREA_ZIEL, name)
    stand = (_stand_pfad(name, KREA_ZIEL) if ziel == profilordner.lora_pfad(KREA_ZIEL, name)
             else ziel.with_name(ziel.stem + "_stand.safetensors"))
    text = (text or "").strip() or beschreibung(name)
    device, dtype = K._geraet()
    W, H = KREA_GROESSE
    shift = K.DEFAULTS["shift"]

    with K._LOCK:
        K.entladen()
        mean = torch.tensor(K._LAT_MEAN, device=device).view(1, 16, 1, 1)
        std = torch.tensor(K._LAT_STD, device=device).view(1, 16, 1, 1)
        enc = K._VAEEncoder(t["vae"], device, dtype)
        latents = []
        with torch.no_grad():
            for i, p in enumerate(bilder):
                if on_status:
                    on_status(f"LoRA: Bilder einpacken {i + 1}/{len(bilder)}")
                bw, bh = krea_format(p)
                latents.append(((enc(_bild_tensor(p, bw, bh).to(device)[None]) - mean) / std).cpu())
        del enc
        K._frei()
        if on_status:
            on_status("LoRA: Beschreibung lesen (Qwen3-VL) …")
        saetze = [lernsatz(text, b) for b in texte] if texte else [text]
        te = K._Qwen3Text(t["text"], device, dtype)
        with torch.no_grad():
            ctxs = [K._encode(s, te, device) for s in saetze]
            ctx_v = K._encode(str(vorschau.get("text") or text), te, device) if vorschau else None
        del te
        K._frei()
        if on_status:
            on_status("LoRA: Modell laden …")
        dit = K._DiT(pfad, device, dtype, on_status)
        with torch.no_grad():
            txts = [dit.text(c) for c in ctxs]
            txt_v = dit.text(ctx_v) if ctx_v is not None else None
        del ctxs, ctx_v
        schichten = K.lora_anbringen(dit, rang, rang, device, torch.float32)
        dit.bloecke = lambda z, vec, rope: _sparsam(dit.blocks, z, vec, rope)
        opt = torch.optim.AdamW(L.parameter(schichten), lr=lr, weight_decay=0.0)
        info = {"ziel": KREA_ZIEL, "modell": ref, "familie": "krea2", "rang": rang, "schritte": schritte,
                "bilder": len(bilder), "bilder_kennung": _bilder_kennung(bilder), "lr": lr, "beschreibung": text,
                "groesse": " ".join(sorted({"x".join(map(str, krea_format(p))) for p in bilder}))}
        if ausloeser:
            info["ausloeser"] = ausloeser
        if texte:
            info["texte_kennung"] = _texte_kennung(texte)
        start = _fortsetzen(stand, schichten, opt, info, on_status)
        alle_v = int(vorschau.get("alle") or 0) if vorschau else 0
        beginn, verluste = time.time(), []
        for schritt in range(start, schritte):
            if abbruch is not None and abbruch():
                L.speichern(stand, schichten, {**info, "schritt": schritt}, opt)
                raise RuntimeError(f"LoRA-Training angehalten bei Schritt {schritt}/{schritte} – geht beim nächsten "
                                   "Mal dort weiter")
            zufall = random.Random(1234 * 1_000_003 + schritt)
            nr = zufall.randrange(len(latents))
            x0, txt = latents[nr].to(device), txts[nr if texte else 0]
            u = min(max(zufall.random(), 1e-3), 1 - 1e-3)
            sigma = math.exp(shift) / (math.exp(shift) + (1.0 / u - 1.0))     # wie die Rauschstärken beim Malen
            gen = torch.Generator(device=x0.device).manual_seed(1234 + schritt)
            rauschen = torch.randn(x0.shape, generator=gen, device=x0.device, dtype=x0.dtype)
            opt.zero_grad(set_to_none=True)
            x = (1 - sigma) * x0 + sigma * rauschen
            v = dit(x, torch.full((1,), sigma, device=device), txt)
            verlust = F.mse_loss(v.float(), (rauschen - x0).float())
            verlust.backward()
            opt.step()
            verluste.append(verlust.item())
            del x0, x, v, verlust, rauschen
            if (schritt + 1) % VORGABEN["zwischenstand"] == 0:
                L.speichern(stand, schichten, {**info, "schritt": schritt + 1}, opt)
            fertig = schritt + 1 - start
            je = (time.time() - beginn) / fertig
            mittel = sum(verluste[-50:]) / len(verluste[-50:])
            rest = je * (schritte - schritt - 1)
            if on_status:
                on_status(f"LoRA: Schritt {schritt + 1}/{schritte} · Verlust {mittel:.3f} · noch etwa {_uhr(rest)}")
            if fortschritt:
                fortschritt({"schritt": schritt + 1, "schritte": schritte, "prozent": 100.0 * (schritt + 1) / schritte,
                             "verlust": round(mittel, 4), "rest_s": round(rest)})
            if alle_v and txt_v is not None and (schritt + 1) % alle_v == 0 and schritt + 1 < schritte:
                opt.zero_grad(set_to_none=True)
                K._frei()
                try:
                    bild = _vorschau_malen(dit, txt_v, t, device, dtype, mean, std, W, H,
                                           int(vorschau.get("seed") or 7),
                                           Path(vorschau["ordner"]) / f"schritt_{schritt + 1:05d}.png")
                    if fortschritt:
                        fortschritt({"vorschau": str(bild), "schritt": schritt + 1, "schritte": schritte})
                except Exception as e:                   # ein Testbild darf das Training nie aufhalten
                    if on_status:
                        on_status(f"LoRA: Testbild übersprungen ({type(e).__name__})")
                    K._frei()
        L.speichern(ziel, schichten, {**info, "schritt": schritte})
        if stand.is_file():
            stand.unlink()
        del dit, schichten, opt, txts, txt_v
        K._frei()
    return ziel


def _uhr(sekunden: float) -> str:
    m = int(sekunden // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min" if m else f"{int(sekunden)} s"


def _trainieren(name, bilder, schritte, rang, lr, modell, ziel, text, on_status, abbruch) -> Path:
    import torch
    import lora as L
    import profilordner
    import video as V

    schritte = int(schritte or VORGABEN["schritte"])
    rang = int(rang or VORGABEN["rang"])
    lr = float(lr or VORGABEN["lr"])
    bilder = list(bilder) if bilder is not None else bilder_auswaehlen(name)
    if len(bilder) < 4:
        raise RuntimeError(f"Zu wenige Bilder für eine LoRA ({len(bilder)}, mindestens 4)")
    t = V.teile(modell or V.gewaehlt())
    if (grund := V.missing_reason(t["name"])):
        raise RuntimeError(grund)
    ziel = Path(ziel) if ziel else profilordner.lora_pfad(ZIEL, name)
    stand = _stand_pfad(name) if ziel == profilordner.lora_pfad(ZIEL, name) else ziel.with_name(ziel.stem + "_stand.safetensors")
    text = (text or "").strip() or beschreibung(name)
    device, dtype = V._geraet()

    with V._LOCK:
        V.entladen()
        latents, ctx = daten(bilder, text, t, device, dtype, on_status)
        if on_status:
            on_status("LoRA: Modell laden …")
        sd = V._kopie(V._lade_gguf_dit(t["dit"], dtype) if t["gguf"] else V._lade_safetensors(t["dit"], dtype), device)
        if device.type == "cuda" and V._fp8_bereit(device):
            V._als_fp8(sd, dtype)
        schichten = L.anbringen(sd, rang=rang, alpha=rang, device=device, dtype=torch.float32)
        dit = V.WanDiT(sd, V.dit_config(t))
        dit.lernen = True
        opt = torch.optim.AdamW(L.parameter(schichten), lr=lr, weight_decay=0.0)
        try:
            familie = V.familie(V.dit_config(t))
        except Exception:
            familie = ""
        info = {"ziel": ZIEL, "modell": t["name"], "familie": familie, "rang": rang,
                "schritte": schritte, "bilder": len(bilder), "bilder_kennung": _bilder_kennung(bilder),
                "lr": lr, "beschreibung": text}
        start = _fortsetzen(stand, schichten, opt, info, on_status)
        ctx_g = ctx.to(device)
        beginn, verluste = time.time(), []
        for schritt in range(start, schritte):
            if abbruch is not None and abbruch():
                L.speichern(stand, schichten, {**info, "schritt": schritt}, opt)
                raise RuntimeError(f"LoRA-Training angehalten bei Schritt {schritt}/{schritte} – geht beim nächsten "
                                   "Mal dort weiter")
            # Zufall je Schritt aus der Schrittnummer: nach dem Fortsetzen dieselbe Folge wie ohne Pause
            zufall = random.Random(1234 * 1_000_003 + schritt)
            x0 = latents[zufall.randrange(len(latents))].to(device)
            u = zufall.random()
            s = VORGABEN["shift"]
            sigma = s * u / (1 + (s - 1) * u)                     # wie die Rauschstärken beim Erzeugen
            gen = torch.Generator(device=x0.device).manual_seed(1234 + schritt)
            rauschen = torch.randn(x0.shape, generator=gen, device=x0.device, dtype=x0.dtype)
            opt.zero_grad(set_to_none=True)
            verlust = L.flow_verlust(dit, x0, ctx_g, sigma, rauschen)
            verlust.backward()
            opt.step()
            verluste.append(verlust.item())
            if (schritt + 1) % VORGABEN["zwischenstand"] == 0:
                L.speichern(stand, schichten, {**info, "schritt": schritt + 1}, opt)
            if on_status:
                fertig = schritt + 1 - start
                je = (time.time() - beginn) / fertig
                mittel = sum(verluste[-50:]) / len(verluste[-50:])
                on_status(f"LoRA: Schritt {schritt + 1}/{schritte} · Verlust {mittel:.3f} · noch etwa "
                          f"{V._uhr(je * (schritte - schritt - 1))}")
        L.speichern(ziel, schichten, {**info, "schritt": schritte})
        if stand.is_file():
            stand.unlink()
        del sd, dit, schichten, opt
        V._frei()
    return ziel
