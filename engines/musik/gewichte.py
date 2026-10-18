"""gewichte.py – die vier ACE-Step-GGUFs finden, gegen ihre SHA-256 prüfen und Tensoren laden.

Erwartet unter Models/Musik/ oder Models/Music/ (Daten- oder Programmordner, Unterordner egal):
  acestep-v15-turbo-*.gguf (Musik-Erzeuger) · Qwen3-Embedding-0.6B-*.gguf (Textleser) ·
  vae-*.gguf (Ton) · optional acestep-5Hz-lm-*.gguf (Song-Planer)
Nur Dateien aus der Liste werden gelesen. Das Prüfergebnis merkt sich der Motor je Dateistand
(Größe, Änderungszeit) in .geprueft.json im Modellordner – sonst kostete jeder Start Minuten.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import torch

try:
    from paths import INSTALL as _INSTALL, ROOT as _ROOT
except Exception:
    _ROOT = _INSTALL = Path(__file__).resolve().parents[2]

ORDNER = [b / "Models" / n for b in (_ROOT, _INSTALL) for n in ("Musik", "Music")]
TEILE = {"dit": "acestep-v15-", "text": "Qwen3-Embedding-", "vae": "vae-", "lm": "acestep-5Hz-lm-"}
PFLICHT = ("dit", "text", "vae")
GEPRUEFT = ".geprueft.json"
# SHA-256 laut Hugging Face (Serveurperso/ACE-Step-1.5-GGUF, Umwandlung der Originalgewichte von ACE-Step).
SHA256: dict[str, str] = {
    "acestep-v15-turbo-Q8_0.gguf": "288f708a61cfc241013a98a62f98ba331f83fe34d0d3559acdd9b0f6a2f7cd6b",
    "acestep-5Hz-lm-1.7B-Q8_0.gguf": "726f99a82f050b32ebc5c5e36acaa9d7acd06bfe5e579a62b00e0d0d6d0b7ec6",
    "Qwen3-Embedding-0.6B-Q8_0.gguf": "972f23255e46adfe744a0eb9a0039f3c63988f65753b0968d776e8b27168c321",
    "vae-BF16.gguf": "0599862ac5d15cd308e1d2e368373aea6c02e25ebd1737ad4a4562a0901b0ef8",
}


def _alle_gguf() -> list[Path]:
    gefunden = []
    for o in ORDNER:
        if o.is_dir():
            gefunden += sorted(o.rglob("*.gguf"))
    return gefunden


def dateien() -> dict[str, Path]:
    """Teil -> Pfad (nur Dateien aus der SHA-Liste; erste Fundstelle gewinnt)."""
    aus: dict[str, Path] = {}
    for p in _alle_gguf():
        if p.name not in SHA256:
            continue
        for teil, anfang in TEILE.items():
            if p.name.startswith(anfang) and teil not in aus:
                aus[teil] = p
    return aus


def fehlt() -> str | None:
    """Grund, warum der Motor nicht starten kann – oder None."""
    d = dateien()
    weg = [t for t in PFLICHT if t not in d]
    if weg:
        namen = {"dit": "acestep-v15-turbo-Q8_0.gguf", "text": "Qwen3-Embedding-0.6B-Q8_0.gguf",
                 "vae": "vae-BF16.gguf"}
        return "Es fehlen unter Models/Musik: " + ", ".join(namen[t] for t in weg)
    return None


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for stueck in iter(lambda: f.read(1 << 22), b""):
            h.update(stueck)
    return h.hexdigest()


def pruefen(pfad: Path, melden=None) -> None:
    """SHA-256 gegen die Liste – einmal je Dateistand. Wirft bei Abweichung."""
    soll = SHA256.get(pfad.name)
    if soll is None:
        raise RuntimeError(f"{pfad.name} steht nicht auf der Liste geprüfter Musik-Modelle.")
    merker = pfad.parent / GEPRUEFT
    try:
        stand = json.loads(merker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stand = {}
    st = pfad.stat()
    kennung = [st.st_size, st.st_mtime_ns, soll]
    if stand.get(pfad.name) == kennung:
        return
    if melden:
        melden(f"Musik: prüfe {pfad.name} …")
    ist = sha256(pfad)
    if ist != soll:
        raise RuntimeError(f"{pfad.name}: SHA-256 stimmt nicht (beschädigt oder eine andere Datei).")
    stand[pfad.name] = kennung
    try:
        merker.write_text(json.dumps(stand, indent=1), encoding="utf-8")
    except OSError:
        pass


class Datei:
    """Eine geöffnete GGUF: Tensoren als torch-Tensoren im gewünschten Datentyp."""

    def __init__(self, pfad: Path):
        from ggufengine.gguf import GGUFFile
        self.pfad = Path(pfad)
        self.gg = GGUFFile(self.pfad)

    def meta(self, schluessel: str, vorgabe=None):
        return self.gg.get(schluessel, vorgabe)

    def namen(self) -> list[str]:
        return [ti.name for ti in self.gg.tensor_list]

    def tensor(self, name: str, geraet="cpu", dtype=torch.bfloat16) -> torch.Tensor:
        from ggufengine.quant import dequantize, raw_to_tensor
        ti = self.gg.tensors[name]
        roh = raw_to_tensor(self.gg.tensor_bytes(ti), geraet)
        return dequantize(roh, ti.ggml_type, ti.n_elements, out_dtype=dtype).view(ti.shape)

    def schliessen(self) -> None:
        self.gg.close()
