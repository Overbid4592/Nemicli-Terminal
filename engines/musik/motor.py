"""motor.py – Text zu Musik mit ACE-Step 1.5 Turbo.

Ablauf: (Song-Planer -> Audio-Codes) -> Stil und Liedtext -> Textleser (Qwen3-Embedding) ->
Bedingungs-Encoder -> 8 Euler-Schritte im DiT ab Rauschen (Kontext = Gerüst aus den Codes oder Stille) ->
VAE-Decoder -> WAV 48 kHz Stereo.
Cover: VAE-Encoder und FSQ-Tokenizer machen aus einem vorhandenen Song die Codes; der Rest läuft gleich.
Die Teile liegen nacheinander auf der Karte, nie alle zugleich.
"""
from __future__ import annotations

import gc
import random
import re
import struct
import time
import wave
from pathlib import Path

import numpy as np
import torch

from . import gewichte, netze, planer, tondatei
from .wunsch import INSTRUMENTAL, MAX_SEKUNDEN, MIN_SEKUNDEN, titel_vorschlag

ABTASTRATE = 48000
LATENT_HZ = 25
SCHRITTE, SHIFT = 8, 3.0
ENDE = "<|endoftext|>"
ENDE_ID = 151643
ANWEISUNG = "Fill the audio semantic mask based on the given conditions:"
ANWEISUNG_CODES = "Generate audio semantic tokens based on the given conditions:"   # Kontext aus Planer-Codes


class Abbruch(Exception):
    pass


def prompts(stil: str, text: str, sekunden: int, bpm: int | None = None, takt: str | None = None,
            tonart: str | None = None, sprache: str | None = None, anweisung: str = ANWEISUNG) -> tuple[str, str]:
    """Text- und Liedtext-Eingabe genau im Trainingsformat."""
    metas = (f"- bpm: {bpm if bpm else 'N/A'}\n- timesignature: {takt or 'N/A'}\n"
             f"- keyscale: {tonart or 'N/A'}\n- duration: {int(sekunden)} seconds\n")
    t = f"# Instruction\n{anweisung}\n\n# Caption\n{stil}\n\n# Metas\n{metas}{ENDE}\n"
    l = f"# Languages\n{sprache or 'unknown'}\n\n# Lyric\n{text}{ENDE}"
    return t, l


def kodieren(tok, s: str) -> list[int]:
    """BPE mit <|endoftext|> als Sondertoken, am Ende ein weiteres EOS."""
    ids: list[int] = []
    teile = s.split(ENDE)
    for i, teil in enumerate(teile):
        if teil:
            ids += tok.encode(teil)
        if i < len(teile) - 1:
            ids.append(ENDE_ID)
    return ids + [ENDE_ID]


def zeitplan(schritte: int = SCHRITTE, shift: float = SHIFT) -> list[float]:
    plan = []
    for i in range(schritte):
        t = 1.0 - i / schritte
        plan.append(shift * t / (1.0 + (shift - 1.0) * t))
    return plan


def latente(sekunden: float) -> int:
    t = int(round(sekunden * LATENT_HZ))
    return max(2, t - t % 2)


STILLE_PEGEL = 0.004             # RMS darunter gilt als still (etwa -48 dBFS)
STILLE_AB_S = 2.0                # erst so viel Stille am Ende wird abgeschnitten
AUSKLANG_S, AUSBLENDEN_S = 1.0, 0.5


def stille_kuerzen(ton: np.ndarray) -> np.ndarray:
    """Stille am Songende abschneiden (der Planer beendet Songs, wenn der Liedtext aus ist), mit Ausklang
    und Ausblenden. ton: [2, N]."""
    fenster = ABTASTRATE // 20
    n = ton.shape[-1] // fenster
    if n == 0:
        return ton
    pegel = np.sqrt((ton[:, :n * fenster].reshape(2, n, fenster) ** 2).mean((0, 2)))
    laut = np.flatnonzero(pegel > STILLE_PEGEL)
    if not len(laut):
        return ton
    ende = (laut[-1] + 1) * fenster
    if ton.shape[-1] - ende < STILLE_AB_S * ABTASTRATE:
        return ton
    ton = ton[:, :ende + int(AUSKLANG_S * ABTASTRATE)].copy()
    blende = min(ton.shape[-1], int(AUSBLENDEN_S * ABTASTRATE))
    ton[:, -blende:] *= np.linspace(1.0, 0.0, blende, dtype=ton.dtype)
    return ton


def _info_block(info: dict) -> bytes:
    """RIFF-LIST/INFO-Block (INAM Titel, IART Künstler, IGNR Stil, ICMT Liedtext …). Windows liest INFO in
    der ANSI-Codepage: cp1252, nur wenn ein Zeichen dort fehlt UTF-8."""
    teile = b""
    for kennung, wert in info.items():
        if not wert:
            continue
        try:
            roh = str(wert).encode("cp1252") + b"\0"
        except UnicodeEncodeError:
            roh = str(wert).encode("utf-8") + b"\0"
        teile += kennung.encode("ascii") + struct.pack("<I", len(roh)) + roh + (b"\0" if len(roh) % 2 else b"")
    return b"LIST" + struct.pack("<I", 4 + len(teile)) + b"INFO" + teile if teile else b""


def wav_schreiben(ton: np.ndarray, ziel: Path, info: dict | None = None) -> Path:
    """ton: [2, N] float -> 16-Bit-WAV Stereo, mit `info` als LIST/INFO-Block (Player zeigen den Titel)."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    spitze = float(np.abs(ton).max()) if ton.size else 0.0
    if spitze > 0.99:
        ton = ton * (0.99 / spitze)
    with wave.open(str(ziel), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(ABTASTRATE)
        w.writeframes((np.clip(ton.T, -1, 1) * 32767).astype("<i2").tobytes())
    if (block := _info_block(info or {})):
        with open(ziel, "r+b") as f:
            f.seek(0, 2)
            f.write(block)
            groesse = f.tell() - 8
            f.seek(4)
            f.write(struct.pack("<I", groesse))
    return ziel


def fehlt() -> str | None:
    return gewichte.fehlt()


def _leeren():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _tokenizer(datei):
    from ggufengine.tokenizer import Tokenizer
    tok = Tokenizer(datei.gg)
    tok.pre = "qwen2"
    return tok


def komponieren(stil: str, text: str = INSTRUMENTAL, sekunden: float = 30, bpm: int | None = None,
                tonart: str | None = None, takt: str | None = None, sprache: str | None = None,
                seed: int | None = None, ziel: Path | None = None, on_status=None, abbruch=None,
                geraet: str | None = None, titel: str = "", kuenstler: str = "", planen: bool = True,
                codes: list[int] | None = None, vorlage: torch.Tensor | None = None, naehe: float = 0.0,
                genre: str | None = None) -> dict:
    """Einen Song erzeugen. Rückgabe: pfad, titel, sekunden, seed, sekunden_rechnen, vram_mb, geraet.
    Ohne `titel` gilt die erste Zeile des Refrains; er steht im Dateinamen und in der WAV-Datei.
    `planen`: liegt der Song-Planer (acestep-5Hz-lm) bereit, schreibt er zuerst die Audio-Codes – der
    Komponist arbeitet dann mit diesem Gerüst statt aus Stille. `codes` gibt das Gerüst fertig vor (Cover);
    mit `vorlage` (Latents [T, 64]) und `naehe` > 0 beginnt die Rechnung nicht bei reinem Rauschen, sondern
    bei einer Mischung mit der Vorlage – das Ergebnis bleibt näher am Original.
    Die GPU-Sperre prüfen die Aufrufer (hintergrund.sperre) – als Hintergrund-Aufgabe sähe der Motor
    sonst sich selbst als belegende Aufgabe."""
    if (grund := fehlt()):
        raise RuntimeError(grund)
    melden = on_status or (lambda _m: None)

    def pruefen_abbruch():
        if abbruch and abbruch():
            raise Abbruch("Musik abgebrochen.")

    sekunden = float(min(MAX_SEKUNDEN, max(MIN_SEKUNDEN, sekunden)))
    text = (text or "").strip() or INSTRUMENTAL
    titel = (titel or "").strip() or titel_vorschlag("" if text == INSTRUMENTAL else text, stil)
    seed = int(seed) if seed is not None else random.randrange(2 ** 31)
    geraet = geraet or ("cuda" if torch.cuda.is_available() else "cpu")
    start = time.perf_counter()
    if geraet == "cuda":
        torch.cuda.reset_peak_memory_stats()
    pfade = gewichte.dateien()
    planen = planen and codes is None and "lm" in pfade
    for teil in gewichte.PFLICHT + (("lm",) if planen else ()):
        gewichte.pruefen(pfade[teil], melden)

    td = gewichte.Datei(pfade["text"])
    try:
        tok = _tokenizer(td)
    except Exception:
        td.schliessen()
        raise
    codes = list(codes or [])
    if planen:
        melden("Musik: Planer schreibt das Gerüst …")
        ld = gewichte.Datei(pfade["lm"])
        try:
            pl = planer.Planer(ld, geraet)
        finally:
            ld.schliessen()
        bed_ids, unbed_ids = planer.prompt_ids(
            tok.encode, stil, text, planer.cot(stil, int(sekunden), bpm, tonart, sprache, takt))
        codes = pl.codes(bed_ids, unbed_ids, int(sekunden * planer.CODES_JE_SEKUNDE), seed,
                         melden=melden, abbruch=abbruch)
        del pl
        _leeren()
        pruefen_abbruch()

    melden("Musik: lese Stil und Text …")
    try:
        t_str, l_str = prompts(stil, text, int(sekunden), bpm, takt, tonart, sprache,
                               ANWEISUNG_CODES if codes else ANWEISUNG)
        leser = netze.Textleser(td, geraet)
        text_hidden = leser(kodieren(tok, t_str))
        liedtext = leser.einbetten(kodieren(tok, l_str))
        del leser
    finally:
        td.schliessen()
    _leeren()
    pruefen_abbruch()

    melden("Musik: lade den Komponisten …")
    dd = gewichte.Datei(pfade["dit"])
    try:
        bed = netze.Bedingung(dd, geraet)
        dit = netze.DiT(dd, geraet)
        detok = planer.Detokenizer(dd, geraet) if codes else None
    finally:
        dd.schliessen()
    T = latente(sekunden)
    stille = dit.stille[:T]
    enc = bed(text_hidden, liedtext, stille[:1][None].to(bed.dtype))
    quelle = stille
    if detok is not None:
        gerust = detok(codes)[:T]                    # 5 Bilder je Code; endet der Planer früher: Stille dahinter
        quelle = torch.cat([gerust, dit.stille[:T - len(gerust)]]) if len(gerust) < T else gerust
    del bed, text_hidden, liedtext, detok
    _leeren()

    g = torch.Generator(device="cpu").manual_seed(seed)
    xt = torch.randn(1, T, 64, generator=g).to(torch.bfloat16).float().to(geraet)
    kontext = torch.cat([quelle, torch.ones_like(quelle)], -1)[None]
    plan = zeitplan()
    if vorlage is not None and naehe > 0:
        start = min(range(len(plan)), key=lambda i: abs(plan[i] - (1.0 - naehe)))
        rein = vorlage[:T].to(geraet, torch.float32)
        if len(rein) < T:
            rein = torch.cat([rein, rein[-1:].expand(T - len(rein), -1)])
        xt = plan[start] * xt + (1.0 - plan[start]) * rein[None]
        plan = plan[start:]
    for i, t in enumerate(plan):
        pruefen_abbruch()
        melden(f"Musik: Schritt {i + 1}/{len(plan)}")
        v = dit(torch.cat([kontext, xt], -1), t, enc)
        if i == len(plan) - 1:
            xt = xt - v * t
        else:
            xt = xt - v * (t - plan[i + 1])
    z = xt[0].T.contiguous()
    del dit, enc, kontext, stille, quelle, v
    _leeren()
    pruefen_abbruch()

    melden("Musik: mache daraus Klang …")
    vd = gewichte.Datei(pfade["vae"])
    try:
        vae = netze.VAEDecoder(vd, geraet)
    finally:
        vd.schliessen()
    ton = stille_kuerzen(vae.dekodieren(z, melden=melden).numpy())
    del vae, z
    vram = torch.cuda.max_memory_allocated() / 2 ** 20 if geraet == "cuda" else 0.0
    _leeren()

    if ziel is None:
        import profilordner
        ziel = profilordner.musik_pfad(titel=titel)
    info = {"INAM": titel, "IART": kuenstler, "IGNR": (genre or stil)[:200],
            "ICMT": "" if text == INSTRUMENTAL else text, "ICRD": time.strftime("%Y-%m-%d"),
            "ISFT": "NemiCLI (ACE-Step 1.5)"}
    pfad = wav_schreiben(ton, Path(ziel), info)
    return {"pfad": pfad, "titel": titel, "codes": len(codes), "gewuenscht": sekunden, "sekunden": ton.shape[-1] / ABTASTRATE, "seed": seed,
            "sekunden_rechnen": time.perf_counter() - start, "vram_mb": round(vram), "geraet": geraet}


def _titel_aus(pfad: Path, info: dict) -> str:
    """Titel einer Vorlage: INFO-Titel, sonst Dateiname ohne angehängtes Datum."""
    if info.get("INAM"):
        return info["INAM"].strip()
    return re.sub(r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(-\d{2})?(_\d+)?$", "", Path(pfad).stem).strip(" _")


def covern(vorlage: Path, stil: str, text: str | None = None, titel: str = "", naehe: float = 0.0,
           seed: int | None = None, ziel: Path | None = None, on_status=None, abbruch=None,
           geraet: str | None = None, kuenstler: str = "") -> dict:
    """Einen vorhandenen Song in einem anderen Stil neu einspielen (Melodie und Aufbau bleiben).
    `text`: Liedtext; ohne Angabe der aus der WAV-Datei (eigene Songs tragen ihn mit), sonst instrumental.
    `naehe` 0–0.9: wie dicht am Original (0 = nur Melodie/Aufbau, höher = auch Klang)."""
    if (grund := fehlt()):
        raise RuntimeError(grund)
    melden = on_status or (lambda _m: None)
    vorlage = Path(vorlage)
    melden("Musik: lese die Vorlage …")
    ton = tondatei.lesen(vorlage, MAX_SEKUNDEN)
    sekunden = ton.shape[-1] / ABTASTRATE
    if sekunden < MIN_SEKUNDEN:
        raise ValueError(f"Die Vorlage ist zu kurz ({sekunden:.1f} s, mindestens {MIN_SEKUNDEN} s).")
    info = tondatei.info(vorlage) if vorlage.suffix.lower() == ".wav" else {}
    if text is None or not str(text).strip():
        text = info.get("ICMT") or INSTRUMENTAL
    alt = _titel_aus(vorlage, info)
    titel = (titel or "").strip() or (f"{alt} (Cover)" if alt else "")
    geraet = geraet or ("cuda" if torch.cuda.is_available() else "cpu")
    naehe = float(min(0.9, max(0.0, naehe or 0.0)))
    pfade = gewichte.dateien()
    for teil in ("vae", "dit"):
        gewichte.pruefen(pfade[teil], melden)

    vd = gewichte.Datei(pfade["vae"])
    try:
        enc = netze.VAEEncoder(vd, geraet)
    finally:
        vd.schliessen()
    z = enc.kodieren(torch.from_numpy(ton), melden=melden)
    del enc, ton
    _leeren()
    if abbruch and abbruch():
        raise Abbruch("Musik abgebrochen.")

    melden("Musik: schreibe Melodie und Aufbau mit …")
    dd = gewichte.Datei(pfade["dit"])
    try:
        tok = planer.Tokenizer(dd, geraet)
        stille = dd.tensor("silence_latent", geraet, torch.float32)
    finally:
        dd.schliessen()
    codes = tok(z, stille)
    del tok, stille
    _leeren()
    erg = komponieren(stil, text, len(z) / LATENT_HZ, seed=seed, ziel=ziel, on_status=on_status,
                      abbruch=abbruch, geraet=geraet, titel=titel, kuenstler=kuenstler, planen=False,
                      codes=codes, vorlage=z if naehe > 0 else None, naehe=naehe)
    erg.update(vorlage=str(vorlage), naehe=naehe)
    return erg
