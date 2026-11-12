"""
sdvideo.py - Video aus Stable Diffusion: ein Bild-Checkpoint (SD 1.5 oder SDXL) plus Zeit-Schichten
(Bewegungs-Modul: AnimateDiff, AnimateDiff-Lightning oder Hotshot-XL).

    Prompt -> CLIP (aus dem Checkpoint) -> Bildnetz mit Zeit-Schichten, fensterweise über alle Bilder
           -> VAE (aus dem Checkpoint) -> MP4 mit 8 Bildern je Sekunde, ohne Ton

Die Zeit-Schichten kennen nur wenige Bilder am Stück (8 bis 32). Längere Videos entstehen in überlappenden
Fenstern, deren Vorhersagen je Schritt gemittelt werden. Ein Startbild (z. B. von Krea 2) hält die Figur:
alle Bilder beginnen als verrauschtes Startbild, das erste bleibt es bis zum Schluss.

Ordner je Modell unter Models/Videomodelle/: der Checkpoint (.safetensors) und das Bewegungs-Modul – dort,
in `bewegung/` im Modellordner oder geteilt in Models/Videomodelle/bewegung/. Der CLIP-Tokenizer
(vocab.json + merges.txt) liegt in `tokenizer_clip/` (auch der von Models/Bildmodelle gilt).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from bildmotoren.gemeinsam import aufraeumen, aus_ram
from bildmotoren.gewichte import Quelle, namen, ohne_praefix

from . import sd_netz, sd_text, sd_vae

TITEL = "SD-Video"
SYMBOL = "🎬"
MERKMAL = "Stable Diffusion, 8 Bilder/s, ohne Ton"
TON = False
STARTBILD = True                          # ein Startbild hält die Figur
FPS = 8                                   # darauf sind die Zeit-Schichten trainiert
GROESSE = {"hochkant": (512, 768), "quer": (768, 512), "quadrat": (512, 512)}      # (Breite, Höhe)
UEBERLAPPUNG = 4                          # Bilder, die sich zwei Fenster teilen
START_STAERKE = 0.8                       # Anteil der Schritte, die mit Startbild noch gerechnet werden
ZEITSCHRITTE = 1000
BETA = (0.00085, 0.012)
GEPRUEFT = "geprueft.json"
ZUBEHOER = {"bewegung": "ein Bewegungs-Modul (AnimateDiff-Lightning, AnimateDiff v3 oder Hotshot-XL)",
            "tokenizer": "der CLIP-Tokenizer (vocab.json + merges.txt)"}

# Offizielle Bewegungs-Module: Name -> (Bytes, SHA-256, Bauart). Quellen: huggingface.co/ByteDance/
# AnimateDiff-Lightning, huggingface.co/guoyww/animatediff, huggingface.co/hotshotco/Hotshot-XL.
BEKANNT = {
    "animatediff_lightning_1step_comfyui.safetensors": (908929664, "5f45da3a6ffa5edbc868f577a875a0e0d6cdc6f8048dbbd17a13bc93ccf34393", "sd15"),
    "animatediff_lightning_1step_diffusers.safetensors": (907702248, "ca4ea6e68f4634d72c222de2b0187c3a31f00278a55868420b4e66b987bb7ccb", "sd15"),
    "animatediff_lightning_2step_comfyui.safetensors": (908929664, "29b4879520056657b897f48821760285b3eef371576e80c86c2ef1fdf95f6c1d", "sd15"),
    "animatediff_lightning_2step_diffusers.safetensors": (907702248, "fbd62904484d3e0842aa6c2f12d919ef91bacac45d36b1a13ae1ba10b5da4fdd", "sd15"),
    "animatediff_lightning_4step_comfyui.safetensors": (908929664, "aeb66ae8ff4a868d31379c3bde3e5e7e510a4d4b565a06ee4f1297e93a561dc5", "sd15"),
    "animatediff_lightning_4step_diffusers.safetensors": (907702248, "8f3330914edd8fc2ee5659e6944323858feb0ec21cc913789202d043f69b5e17", "sd15"),
    "animatediff_lightning_8step_comfyui.safetensors": (908929664, "5173ea8209053dd5de9b973baefac1801fbe7a433d07fa0122664121751885cf", "sd15"),
    "animatediff_lightning_8step_diffusers.safetensors": (907702248, "849e93641e2ae7dde37fb312747a5e8671920bddf5a8d27757ac7b1699571a96", "sd15"),
    "v3_sd15_mm.ckpt": (1673262583, "2412711886f61091846f53204aabc38aa6e09356d62a9808abe4daa802168343", "sd15"),
    "mm_sd_v15_v2.ckpt": (1817888431, "69ed0f5fef82b110aca51bcab73b21104242bc65d6ab4b8b2a2a94d31cad1bf0", "sd15"),
    "mm_sdxl_v10_beta.ckpt": (950143538, "fa4950a062e892fca50d4c441fcd6130d1ad68a621a0404d155be17580072978", "sdxl"),
    "hsxl_temporal_layers.f16.safetensors": (474694696, "b46ce6b4ec297b21e508b8888135f65b5a07aa76d755cc086dc5ee804064bb1c", "sdxl"),
    "hsxl_temporal_layers.safetensors": (949328384, "3a3a8013f99ba2991da633a9b82c833c7e7e6b62020b6cab41756601ad06c484", "sdxl"),
}

# Vorgaben je Bewegungs-Modul (Namensanfang): Schritte, CFG, Fenster in Bildern, Beta-Verlauf
VORGABEN = (
    ("animatediff_lightning_", {"schritte": 4, "cfg": 1.0, "fenster": 16, "beta": "linear"}),
    ("hsxl_", {"schritte": 30, "cfg": 7.0, "fenster": 8, "beta": "skaliert"}),
    ("mm_sdxl_", {"schritte": 40, "cfg": 8.5, "fenster": 16, "beta": "skaliert"}),
    ("", {"schritte": 25, "cfg": 7.5, "fenster": 16, "beta": "linear"}),
)


# ===========================================================================
#  Erkennen und Finden
# ===========================================================================

def erkennen(alle: dict) -> bool:
    return sd_netz.erkennen(alle)


def bauart(pfad: Path) -> str | None:
    return sd_netz.bauart(ohne_praefix(namen(pfad))[0])


def vorgaben(bewegung: Path | None) -> dict:
    name = bewegung.name.lower() if bewegung is not None else ""
    v = dict(next(w for anfang, w in VORGABEN if name.startswith(anfang)))
    m = re.match(r"animatediff_lightning_(\d)step", name)
    if m:
        v["schritte"] = int(m.group(1))
    return v


def _bewegung_passt(pfad: Path, art: str, netz_stufen: int) -> bool:
    if pfad.suffix.lower() == ".ckpt":                 # Pickle: nur die bekannten offiziellen Dateien
        soll = BEKANNT.get(pfad.name)
        return soll is not None and soll[2] == art
    try:
        return sd_netz.bewegung_passt(namen(pfad), netz_stufen)
    except Exception:
        return False


def _dateien(d: Path, endungen: tuple) -> list[Path]:
    return [p for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() in endungen] if d.is_dir() else []


def teile(m: dict, wurzeln: list[Path]) -> dict:
    """Zubehör zum Checkpoint: Bewegungs-Modul und Tokenizer (Text-Encoder und VAE stecken im Checkpoint)."""
    netz_namen = ohne_praefix(namen(m["dit"]))[0]
    art, netz_stufen = sd_netz.bauart(netz_namen), sd_netz.stufen(netz_namen)
    orte = [m["ordner"], m["ordner"] / "bewegung"] if m["ordner"] not in wurzeln else []
    orte += [w / "bewegung" for w in wurzeln]
    bewegung = None
    for o in orte:
        fertig = {p.stem for p in _dateien(o, (".safetensors",))}
        for p in _dateien(o, (".safetensors", ".ckpt")):
            if p == m["dit"] or (p.suffix.lower() == ".ckpt" and p.stem in fertig):
                continue                               # die umgeschriebene Fassung liegt schon daneben
            if _bewegung_passt(p, art, netz_stufen):
                bewegung = p
                break
        if bewegung is not None:
            break
    tokenizer = None
    for o in [m["ordner"] / "tokenizer_clip", m["ordner"]] + [w / "tokenizer_clip" for w in wurzeln] \
            + [w.parent / "Bildmodelle" / "tokenizer_clip" for w in wurzeln]:
        if (o / "tokenizer.json").is_file() or ((o / "vocab.json").is_file() and (o / "merges.txt").is_file()):
            tokenizer = o
            break
    return {"art": art, "bewegung": bewegung, "tokenizer": tokenizer}


def fehlt(t: dict, ordner: Path) -> str | None:
    if t.get("bewegung") is None:
        return f"Es fehlt: {ZUBEHOER['bewegung']} für {'SD 1.5' if t.get('art') == 'sd15' else 'SDXL'} – nach " \
               f"{ordner / 'bewegung'} oder in den Modellordner"
    if t.get("tokenizer") is None:
        return f"Es fehlt: {ZUBEHOER['tokenizer']} – nach {ordner / 'tokenizer_clip'}"
    return None


def groesse(t: dict, format_: str) -> tuple[int, int]:
    return GROESSE.get(format_, GROESSE["hochkant"])


# ===========================================================================
#  Prüfen und Laden
# ===========================================================================

def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(1 << 22), b""):
            h.update(stueck)
    return h.hexdigest()


def pruefen(pfad: Path, on_status=None) -> None:
    """Bekanntes Bewegungs-Modul: Größe und SHA-256 einmal je Dateistand prüfen; wirft bei Abweichung."""
    soll = BEKANNT.get(pfad.name)
    if soll is None:
        return
    st = pfad.stat()
    merker = pfad.parent / GEPRUEFT
    try:
        stand = json.loads(merker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stand = {}
    if stand.get(pfad.name) == [st.st_size, st.st_mtime_ns, soll[1]]:
        return
    if st.st_size != soll[0]:
        raise RuntimeError(f"{pfad.name}: Größe stimmt nicht ({st.st_size} statt {soll[0]} Bytes) – "
                           "unvollständig oder eine andere Datei")
    if on_status:
        on_status(f"Prüfe {pfad.name} (SHA-256, einmalig) …")
    if _sha256(pfad) != soll[1]:
        raise RuntimeError(f"{pfad.name}: SHA-256 stimmt nicht – beschädigt oder verändert. Bitte neu laden.")
    stand[pfad.name] = [st.st_size, st.st_mtime_ns, soll[1]]
    try:
        merker.write_text(json.dumps(stand, indent=1), encoding="utf-8")
    except OSError:
        pass


def _umschreiben(ckpt: Path, on_status=None) -> Path:
    """Offizielle Pickle-Datei einmal nach .safetensors umschreiben – nur mit passender SHA-256 und nur
    als reine Gewichte gelesen (weights_only)."""
    import torch
    from safetensors.torch import save_file
    ziel = ckpt.with_suffix(".safetensors")
    if ziel.is_file():
        return ziel
    if ckpt.name not in BEKANNT:
        raise RuntimeError(f"{ckpt.name}: unbekannte Pickle-Datei – nur die offiziellen Bewegungs-Module werden gelesen")
    pruefen(ckpt, on_status)
    if on_status:
        on_status(f"{ckpt.name} einmalig nach safetensors umschreiben …")
    roh = torch.load(ckpt, map_location="cpu", weights_only=True)
    roh = roh.get("state_dict", roh) if isinstance(roh, dict) else {}
    sd = {k: v.half().contiguous() for k, v in roh.items()
          if isinstance(v, torch.Tensor) and (sd_netz.bewegung_name(k) or k.endswith((".pe", ".positional_encoding")))}
    if not sd:
        raise RuntimeError(f"{ckpt.name}: keine Zeit-Schichten gefunden")
    save_file(sd, str(ziel), metadata={"quelle": ckpt.name, "quelle_sha256": BEKANNT[ckpt.name][1]})
    return ziel


def netz_laden(pfad: Path) -> dict:
    """Gewichte des Bildnetzes (halbe Genauigkeit) im Arbeitsspeicher."""
    import torch
    with Quelle(pfad) as q:
        vorsatz = ohne_praefix({k: () for k in q.alle()})[1]
        q.vorsatz = vorsatz
        return {k: q.tensor(k, torch.float16) for k in q.alle()}


def bewegung_laden(pfad: Path, on_status=None) -> dict:
    """Zeit-Schichten (halbe Genauigkeit, Namen wie bei AnimateDiff) und die Fensterlänge der Datei."""
    import torch
    if pfad.suffix.lower() == ".ckpt":
        pfad = _umschreiben(pfad, on_status)
    else:
        pruefen(pfad, on_status)
    with Quelle(pfad) as q:
        sd = {n: q.tensor(k, torch.float16) for k in q.alle() if (n := sd_netz.bewegung_name(k))}
    return {"sd": sd, "fenster": sd_netz.fenster_laenge(namen(pfad))}


def text_laden(pfad: Path, art: str) -> dict:
    return {"l": sd_text.laden_l(pfad), "g": sd_text.laden_g(pfad) if art == "sdxl" else None}


def _tokenizer(ordner: Path):
    from bildmotoren.textenc import clip_tokenizer
    return clip_tokenizer(ordner)


# ===========================================================================
#  Rauschplan und Fenster
# ===========================================================================

def rauschplan(schritte: int, beta: str = "linear"):
    """(Zeitschritte, Rauschstärken + 0): gleichmäßig von hinten gezählt (das letzte Rauschen zuerst)."""
    import torch
    if beta == "skaliert":
        betas = torch.linspace(BETA[0] ** 0.5, BETA[1] ** 0.5, ZEITSCHRITTE, dtype=torch.float64) ** 2
    else:
        betas = torch.linspace(BETA[0], BETA[1], ZEITSCHRITTE, dtype=torch.float64)
    anteil = torch.cumprod(1.0 - betas, dim=0)
    alle = ((1.0 - anteil) / anteil) ** 0.5
    zeit = (torch.arange(ZEITSCHRITTE, 0, -ZEITSCHRITTE / schritte, dtype=torch.float64).round() - 1).long()
    return zeit, torch.cat([alle[zeit], torch.zeros(1, dtype=torch.float64)]).float()


def fenster_plan(bilder: int, laenge: int, ueberlappung: int = UEBERLAPPUNG) -> list[list[int]]:
    """Überlappende Fenster über alle Bilder; das letzte schließt bündig ab."""
    if bilder <= laenge:
        return [list(range(bilder))]
    schritt = max(1, laenge - min(ueberlappung, laenge - 1))
    starts = list(range(0, bilder - laenge, schritt)) + [bilder - laenge]
    return [list(range(s, s + laenge)) for s in starts]


def fenster_gewicht(laenge: int):
    """Gewicht je Bild im Fenster: zur Mitte hin steigend – an den Rändern zählt das Nachbarfenster mehr."""
    import torch
    return torch.tensor([min(i + 1, laenge - i) for i in range(laenge)], dtype=torch.float32)


def bilder_zahl(sekunden: float) -> int:
    return max(2, round(sekunden * FPS))


def _startbild(pfad, breite: int, hoehe: int):
    """Startbild auf die Videogröße bringen: füllend verkleinert, mittig beschnitten -> uint8 (H, W, 3)."""
    import numpy as np
    from PIL import Image
    with Image.open(pfad) as b:
        b = b.convert("RGB")
        f = max(breite / b.width, hoehe / b.height)
        b = b.resize((max(breite, round(b.width * f)), max(hoehe, round(b.height * f))), Image.LANCZOS)
        x, y = (b.width - breite) // 2, (b.height - hoehe) // 2
        return np.asarray(b.crop((x, y, x + breite, y + hoehe)), dtype=np.uint8)


# ===========================================================================
#  Rechnen
# ===========================================================================

def malen(prompt: str, teile: dict, *, sekunden: float, breite: int, hoehe: int, fps: int, seed: int,
          device, dtype, einstellungen: dict, on_status=None, abbruch=None):
    """Rechnet das Video. Gibt (Bilder (T, H, W, 3) uint8, None, 0) zurück – ohne Ton."""
    import torch
    art = teile["art"]
    v = vorgaben(teile["bewegung"])
    schritte = int(einstellungen.get("schritte") or v["schritte"])
    cfg = float(einstellungen.get("cfg") or v["cfg"])
    breite, hoehe = (breite // 64) * 64, (hoehe // 64) * 64

    # --- Text ---
    if on_status:
        on_status(f"{TITEL}: Text lesen …")
    kodieren = _tokenizer(teile["tokenizer"])
    clip = aus_ram("text", (teile["dit"], "sd"), lambda: text_laden(teile["dit"], art))
    texte = [prompt] + ([str(einstellungen.get("negativ") or "")] if cfg > 1.0 else [])
    kontexte, zusaetze = [], []
    for text in texte:
        ids = kodieren(text)
        if art == "sdxl":
            k, gepoolt = sd_text.text_sdxl(clip["l"], clip["g"], ids, device, dtype)
            zusaetze.append(sd_netz.zusatz_sdxl(gepoolt, hoehe, breite, device, dtype))
        else:
            k = sd_text.text_sd15(clip["l"], ids, device, dtype)
            zusaetze.append(None)
        kontexte.append(k)
    clip = None
    aufraeumen()

    # --- Netz ---
    if on_status:
        on_status(f"{TITEL}: Modell laden …")
    ram = aus_ram("dit", teile["dit"], lambda: netz_laden(teile["dit"]))
    zeit_ram = aus_ram("zeit", teile["bewegung"], lambda: bewegung_laden(teile["bewegung"], on_status))
    netz = sd_netz.Netz({k: t.to(device, dtype) for k, t in ram.items()},
                        {k: t.to(device, dtype) for k, t in zeit_ram["sd"].items()}, zeit_ram["fenster"])
    n = bilder_zahl(sekunden)
    laenge = min(int(v["fenster"]), netz.fenster_max)
    fenster = fenster_plan(n, laenge)
    gewicht = fenster_gewicht(laenge).to(device)
    zeit, sig = rauschplan(schritte, v["beta"])
    h, w = hoehe // 8, breite // 8
    rauschen = torch.randn((n, 4, h, w), generator=torch.Generator().manual_seed(seed)).to(device)

    # --- Startbild: alle Bilder beginnen als verrauschtes Startbild, das erste bleibt es ---
    fest, erster = None, 0
    if einstellungen.get("start"):
        if on_status:
            on_status(f"{TITEL}: Startbild einpacken …")
        vae = aus_ram("vae", teile["dit"], lambda: sd_vae.laden(teile["dit"]))
        fest = sd_vae.einpacken(vae, _startbild(einstellungen["start"], breite, hoehe), art, device)
        staerke = min(1.0, max(0.1, float(einstellungen.get("start_staerke") or START_STAERKE)))
        erster = min(schritte - 1, schritte - max(1, round(schritte * staerke)))
        x = fest + rauschen * float(sig[erster])
    else:
        x = rauschen * float(sig[0])

    def vorhersage(x_ein, t, kontext, zusatz):
        """Rauschvorhersage über alle Bilder: Fenster einzeln rechnen, gewichtet mitteln."""
        summe = torch.zeros_like(x_ein, dtype=torch.float32)
        anteil = torch.zeros(x_ein.shape[0], device=x_ein.device)
        for f in fenster:
            m = len(f)
            e = netz(x_ein[f].to(dtype), t.expand(m), kontext.expand(m, -1, -1), bilder=m,
                     zusatz=None if zusatz is None else zusatz.expand(m, -1))
            g = gewicht[:m] if m == laenge else torch.ones(m, device=x_ein.device)
            summe[f] += e.float() * g[:, None, None, None]
            anteil[f] += g
        return summe / anteil[:, None, None, None]

    for i in range(erster, schritte):
        if abbruch is not None and abbruch():
            raise RuntimeError("abgebrochen")
        s, s2 = float(sig[i]), float(sig[i + 1])
        if fest is not None:
            x[0] = fest[0] + rauschen[0] * s
        ein = x / (s * s + 1.0) ** 0.5
        t = zeit[i].to(device).reshape(1)
        with torch.no_grad():
            e = vorhersage(ein, t, kontexte[0], zusaetze[0])
            if cfg > 1.0:
                leer = vorhersage(ein, t, kontexte[1], zusaetze[1])
                e = leer + cfg * (e - leer)
        x = x + (s2 - s) * e
        if on_status:
            on_status(f"{TITEL}: Schritt {i + 1 - erster}/{schritte - erster}")
    if fest is not None:
        x[0] = fest[0]
    netz = kontexte = zusaetze = None
    aufraeumen()

    if on_status:
        on_status(f"{TITEL}: Bilder auspacken …")
    vae = aus_ram("vae", teile["dit"], lambda: sd_vae.laden(teile["dit"]))
    bilder = sd_vae.auspacken(vae, x.float(), art, device,
                              on_status=(lambda m: on_status(f"{TITEL}: {m}")) if on_status else None)
    x = None
    aufraeumen()
    return bilder, None, 0
