"""gewichte.py – Gewichte finden, prüfen, einmal in safetensors umwandeln und laden.

Erwartet unter Models/LivePortrait/ (Daten- oder Programmordner, Unterordner egal):
  appearance_feature_extractor.pth · motion_extractor.pth · warping_module.pth ·
  spade_generator.pth · stitching_retargeting_module.pth · landmark.onnx
Die .pth-Dateien sind Pickle. Sie werden nur gelesen, wenn ihre SHA-256 zur Liste passt, und
dann mit weights_only=True (torch ≥ 2.10, CVE-2026-24747) einmal nach .safetensors umgeschrieben.
Danach lädt der Motor nur noch safetensors.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import torch

try:
    from paths import ROOT as _ROOT, INSTALL as _INSTALL
except Exception:
    _ROOT = _INSTALL = Path(__file__).resolve().parents[2]

ORDNER = [_ROOT / "Models" / "LivePortrait", _INSTALL / "Models" / "LivePortrait"]
DATEIEN = {"F": "appearance_feature_extractor", "M": "motion_extractor", "W": "warping_module",
           "G": "spade_generator", "S": "stitching_retargeting_module"}
LANDMARK = "landmark.onnx"
# Teile in stitching_retargeting_module.pth -> Netzname in netze.NAEHTE
S_TEILE = {"retarget_shoulder": "stitching", "retarget_mouth": "lip", "retarget_eye": "eye"}
# SHA-256 der Originaldateien (Hugging Face KlingTeam/LivePortrait, Ordner liveportrait/). Nur diese
# Dateien werden gelesen.
SHA256: dict[str, str] = {
    "appearance_feature_extractor.pth": "5279bb8654293dbdf327030b397f107237dd9212fb11dd75b83dfb635211ceb5",
    "motion_extractor.pth": "251e6a94ad667a1d0c69526d292677165110ef7f0cf0f6d199f0e414e8aa0ca5",
    "spade_generator.pth": "4780afc7909a9f84e24c01d73b31a555ef651521a1fe3b2429bd04534d992aee",
    "warping_module.pth": "2f61a6f265fe344f14132364859a78bdbbc2068577170693da57fb96d636e282",
    "stitching_retargeting_module.pth": "3652d5a3f95099141a56986aaddec92fadf0a73c87a20fac9a2c07c32b28b611",
    "landmark.onnx": "31d22a5041326c31f19b78886939a634a5aedcaa5ab8b9b951a1167595d147db",
}


def _hat_gewichte(o: Path) -> bool:
    stamm = DATEIEN["F"]
    return o.is_dir() and any(o.rglob(stamm + ".pth")) or o.is_dir() and any(o.rglob(stamm + ".safetensors"))


def ordner() -> Path | None:
    """Models/LivePortrait – sonst der Ordner unter Models/, in dem die Dateien liegen (Name egal)."""
    for o in ORDNER:
        if _hat_gewichte(o):
            return o
    for models in dict.fromkeys(o.parent for o in ORDNER):
        if models.is_dir():
            for o in sorted(p for p in models.iterdir() if p.is_dir()):
                if _hat_gewichte(o):
                    return o
    return None


def _finden(basis: Path, name: str) -> Path | None:
    treffer = sorted(basis.rglob(name))
    return treffer[0] if treffer else None


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(1 << 20), b""):
            h.update(stueck)
    return h.hexdigest()


def spektral_einrechnen(sd: dict) -> dict:
    """weight_orig/weight_u/weight_v (torch spectral_norm) -> weight = weight_orig / sigma, wie im Eval-Modus."""
    aus = {}
    for k, v in sd.items():
        if k.endswith(".weight_orig"):
            stamm = k[:-len("_orig")]
            u, vv = sd[stamm + "_u"], sd[stamm + "_v"]
            w = v.float()
            sigma = torch.dot(u.float(), torch.mv(w.reshape(w.shape[0], -1), vv.float()))
            aus[stamm] = (w / sigma).to(v.dtype)
        elif k.endswith(".weight_u") or k.endswith(".weight_v"):
            continue
        else:
            aus[k] = v
    return aus


def _ohne_modul(sd: dict) -> dict:
    return {k[len("module."):] if k.startswith("module.") else k: v for k, v in sd.items()}


def _pth_lesen(pfad: Path, teil: str) -> dict[str, torch.Tensor]:
    """Eine geprüfte .pth als flaches Wörterbuch {name: tensor} (S: 'stitching.mlp.0.weight' …)."""
    roh = torch.load(pfad, map_location="cpu", weights_only=True)
    if teil == "S":
        return {f"{S_TEILE[t]}.{k}": v for t in S_TEILE for k, v in _ohne_modul(roh[t]).items()}
    if isinstance(roh, dict) and "model" in roh and isinstance(roh["model"], dict):
        roh = roh["model"]
    return spektral_einrechnen(_ohne_modul(roh))


def umwandeln(basis: Path, *, pruefen: bool = True) -> list[Path]:
    """Alle .pth einmal nach .safetensors (neben die Originaldatei). Ohne passende Prüfsumme: Fehler."""
    from safetensors.torch import save_file
    neu = []
    for teil, stamm in DATEIEN.items():
        pth = _finden(basis, stamm + ".pth")
        if pth is None or pth.with_suffix(".safetensors").exists():
            continue
        summe = sha256(pth)
        if pruefen and SHA256.get(pth.name) != summe:
            raise RuntimeError(f"{pth.name}: Prüfsumme {summe[:16]}… ist nicht die der Originaldatei – "
                               "nicht geladen.")
        sd = {k: v.contiguous() for k, v in _pth_lesen(pth, teil).items()}
        ziel = pth.with_suffix(".safetensors")
        save_file(sd, str(ziel), metadata={"quelle": pth.name, "quelle_sha256": summe})
        neu.append(ziel)
    return neu


def fehlt(basis: Path | None = None) -> list[str]:
    basis = basis or ordner()
    if basis is None:
        return [s + ".pth" for s in DATEIEN.values()] + [LANDMARK]
    weg = [s + ".pth" for s in DATEIEN.values()
           if _finden(basis, s + ".safetensors") is None and _finden(basis, s + ".pth") is None]
    return weg + ([] if _finden(basis, LANDMARK) else [LANDMARK])


def laden(basis: Path | None = None) -> dict[str, dict[str, torch.Tensor]]:
    """{'F': sd, 'M': sd, 'W': sd, 'G': sd, 'S': sd} – nur aus safetensors."""
    from safetensors.torch import load_file
    basis = basis or ordner()
    if basis is None:
        raise RuntimeError("Kein Ordner Models/LivePortrait.")
    umwandeln(basis)
    sds = {}
    for teil, stamm in DATEIEN.items():
        p = _finden(basis, stamm + ".safetensors")
        if p is None:
            raise RuntimeError(f"{stamm} fehlt.")
        sds[teil] = load_file(str(p))
    return sds


def landmark_pfad(basis: Path | None = None) -> Path | None:
    basis = basis or ordner()
    if basis is None:
        return None
    p = _finden(basis, LANDMARK)
    if p is not None and SHA256.get(LANDMARK) != sha256(p):
        raise RuntimeError(f"{LANDMARK}: Prüfsumme passt nicht zur Originaldatei – nicht geladen.")
    return p
