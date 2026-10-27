"""
modellkatalog.py - Modelle für die Motoren von NemiCLI, freiwillig nachzuladen (/einrichten modelle).

Jeder Eintrag nennt die offizielle Quelle auf Hugging Face mit fester Revision, Größe und SHA-256 jeder
Datei und den Ordner, in dem der Motor sie sucht. Geladen wird über hfladen.laden: fortsetzbar, erst
nach bestandener Prüfung abgelegt. Ein geändertes Repo ändert nichts – es gilt die angeheftete Revision.

Krea 2 steht nur als Hinweis drin (eigene Lizenz, offizielles Repo mit Anmeldung): Ordner und Link.
Nichts davon ist Pflicht; ohne Modell meldet der jeweilige Motor nur, dass es fehlt.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from paths import INSTALL as _INSTALL, ROOT as _ROOT
except Exception:
    _ROOT = _INSTALL = Path(__file__).resolve().parent.parent

HF = "https://huggingface.co"


@dataclass(frozen=True)
class Datei:
    repo: str
    revision: str
    pfad: str                # im Repo
    groesse: int
    sha256: str
    unter: str = ""          # Unterordner im Zielordner (vae, text_encoders …)

    @property
    def name(self) -> str:
        return self.pfad.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Teil:
    id: str
    titel: str
    wozu: str
    lizenz: str
    lizenz_link: str
    ziel: Callable[[], Path]          # Ordner, in den geladen wird
    da: Callable[[], bool]            # schon eingerichtet (auch mit eigenen Dateien)?
    dateien: tuple[Datei, ...] = ()
    link: str = ""                    # nur Hinweis: hier selbst laden

    @property
    def groesse(self) -> int:
        return sum(d.groesse for d in self.dateien)

    @property
    def ladbar(self) -> bool:
        return bool(self.dateien)


# ---------------------------------------------------------------------------
# Wo die Motoren suchen (ohne sie zu importieren – manche laden torch schon beim Import)
# ---------------------------------------------------------------------------

def _erster(kandidaten: list[Path], sonst: Path) -> Path:
    return next((k for k in kandidaten if k.is_dir()), sonst)


def _unterordner(basis: Path) -> list[Path]:
    try:
        return sorted(p for p in basis.iterdir() if p.is_dir())
    except OSError:
        return []


def _ggufs(d: Path) -> list[str]:
    try:
        return [p.name.lower() for p in d.glob("*.gguf")]
    except OSError:
        return []


def _gedaechtnis_da() -> bool:
    return any(_ggufs(d) or (d / "config.json").is_file() for d in _unterordner(_INSTALL / "Models" / "embeddings"))


def _reranker_da() -> bool:
    return any(len(_ggufs(d)) == 1 for d in _unterordner(_INSTALL / "Models" / "reranker"))


def _sehen_da() -> bool:
    return any(any("mmproj" in n for n in g) and any("mmproj" not in n for n in g)
               for g in map(_ggufs, _unterordner(_INSTALL / "Vision")))


def _stimme_da() -> bool:
    ordner = [d for o in ("Vision", "Sinne") for d in _unterordner(_INSTALL / o)]
    return any(any("talker" in n for n in g) and any("tokenizer" in n for n in g) for g in map(_ggufs, ordner))


_MUSIK = [b / "Models" / n for b in (_ROOT, _INSTALL) for n in ("Musik", "Music")]


def _musik_da() -> bool:
    namen = set()
    for o in _MUSIK:
        if o.is_dir():
            namen |= {p.name for p in o.rglob("*.gguf")}
    return all(any(n.startswith(a) for n in namen) for a in ("acestep-v15-", "Qwen3-Embedding-", "vae-"))


_VIDEO = [_INSTALL / "VideoGen", _ROOT / "VideoGen", _ROOT / "Models" / "Video", _INSTALL / "Models" / "Video"]


def _video_da() -> bool:
    for o in _VIDEO:
        for d in _unterordner(o):
            if d.name not in ("text_encoders", "tokenizer", "tae") and (_ggufs(d) or any(d.glob("*.safetensors"))):
                return True
    return False


_PORTRAET = [_ROOT / "Models" / "LivePortrait", _INSTALL / "Models" / "LivePortrait"]
_PORTRAET_TEILE = ("appearance_feature_extractor", "motion_extractor", "spade_generator", "warping_module",
                   "stitching_retargeting_module")


def _portraet_da() -> bool:
    for o in _PORTRAET:
        if o.is_dir() and all(any(o.rglob(s + ".pth")) or any(o.rglob(s + ".safetensors")) for s in _PORTRAET_TEILE) \
                and any(o.rglob("landmark.onnx")):
            return True
    return False


def _krea_ordner() -> Path:
    try:
        import krea
        return krea.krea_dir()
    except Exception:
        return _ROOT / "Models" / "Krea2"


def _krea_da() -> bool:
    try:
        import syscheck
        return syscheck.model_stock()["bildmodelle"] > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Der Katalog (Revisionen, Größen und SHA-256 laut Hugging Face-API)
# ---------------------------------------------------------------------------

_APACHE = ("Apache 2.0", "https://www.apache.org/licenses/LICENSE-2.0")
_MIT = ("MIT", "https://opensource.org/license/mit")

_EMB = ("Qwen/Qwen3-Embedding-0.6B-GGUF", "370f27d7550e0def9b39c1f16d3fbaa13aa67728")
_RR = ("ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF", "a02f48bb4f057028298c21fa033da2b30d7742d5")
_VL = ("Qwen/Qwen3-VL-4B-Instruct-GGUF", "1cd86afb9a95c410a6038ab3b40d8b578c892266")
_TTS = ("Serveurperso/Qwen3-TTS-GGUF", "b7ee2e8c7459c3bea99da23e3d178125a7d1713c")
_ACE = ("Serveurperso/ACE-Step-1.5-GGUF", "666ac70204440867d8c01ba4b119cc79c95b370a")
_WAN = ("unsloth/Wan2.2-TI2V-5B-GGUF", "b5c2a3816e7056e57e200f6be726fb36ce523b49")
_UMT5 = ("city96/umt5-xxl-encoder-gguf", "b535255bee98c2b0a59ea7c0ae2dcd0c6657b3b7")
_UMT5_TOK = ("alibaba-pai/Wan2.1-Fun-1.3B-InP", "9bcbbcf673c0e6a996df6f3f25bee596ffa36d47")
_LP = ("KlingTeam/LivePortrait", "82a4fa6735ca58432b6ce39301b4b9ee066dea47")

KATALOG: tuple[Teil, ...] = (
    Teil("gedaechtnis", "💾 Gedächtnis", "Suche nach Bedeutung in Chats, Notizen und Wissen", *_APACHE,
         ziel=lambda: _INSTALL / "Models" / "embeddings" / "Qwen3GGUFQ8_0", da=_gedaechtnis_da,
         dateien=(Datei(*_EMB, "Qwen3-Embedding-0.6B-Q8_0.gguf", 639150592,
                        "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"),)),
    Teil("reranker", "🎯 Reranker", "sortiert Gedächtnis-Treffer nach Relevanz", *_APACHE,
         ziel=lambda: _INSTALL / "Models" / "reranker" / "Qwen3-Reranker-0.6B", da=_reranker_da,
         dateien=(Datei(*_RR, "qwen3-reranker-0.6b-q8_0.gguf", 639153184,
                        "22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48"),)),
    Teil("sehen", "👁 Bildbeschreiber", "beschreibt Bilder für Modelle, die selbst nicht sehen", *_APACHE,
         ziel=lambda: _INSTALL / "Vision" / "Qwen3-VL-4B", da=_sehen_da,
         dateien=(Datei(*_VL, "Qwen3VL-4B-Instruct-Q4_K_M.gguf", 2497281664,
                        "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a"),
                  Datei(*_VL, "mmproj-Qwen3VL-4B-Instruct-F16.gguf", 836180256,
                        "256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331"))),
    Teil("stimme", "🔊 Stimme", "liest vor, eigene Stimme je Persönlichkeit", *_APACHE,
         ziel=lambda: _INSTALL / "Vision" / "Qwen3-TTS", da=_stimme_da,
         dateien=(Datei(*_TTS, "qwen-talker-1.7b-voicedesign-Q8_0.gguf", 2042833824,
                        "575610ab1ddcca4dca6bd9a64bcd859d93bbad8764f9cab24e1dbc0c51f62276"),
                  Datei(*_TTS, "qwen-tokenizer-12hz-Q8_0.gguf", 291150624,
                        "1883beeed99348fc35e23dd225e9082f93f6f8c109330a33d935baa8acdbfd94"))),
    Teil("musik", "🎵 Musik", "komponiert Lieder mit Gesang (ACE-Step 1.5 Turbo)", *_MIT,
         ziel=lambda: _erster(_MUSIK, _INSTALL / "Models" / "Music") / "ACE-Step-1.5", da=_musik_da,
         dateien=(Datei(*_ACE, "acestep-v15-turbo-Q8_0.gguf", 2549528000,
                        "288f708a61cfc241013a98a62f98ba331f83fe34d0d3559acdd9b0f6a2f7cd6b"),
                  Datei(*_ACE, "Qwen3-Embedding-0.6B-Q8_0.gguf", 784144960,
                        "972f23255e46adfe744a0eb9a0039f3c63988f65753b0968d776e8b27168c321"),
                  Datei(*_ACE, "vae-BF16.gguf", 337420928,
                        "0599862ac5d15cd308e1d2e368373aea6c02e25ebd1737ad4a4562a0901b0ef8"),
                  Datei(*_ACE, "acestep-5Hz-lm-1.7B-Q8_0.gguf", 1975837568,
                        "726f99a82f050b32ebc5c5e36acaa9d7acd06bfe5e579a62b00e0d0d6d0b7ec6"))),
    Teil("video", "🎬 Video", "Videos bis 20 s aus Text oder Startbild (Wan 2.2 TI2V 5B)", *_APACHE,
         ziel=lambda: _erster(_VIDEO, _INSTALL / "VideoGen") / "WAN-GGUF", da=_video_da,
         dateien=(Datei(*_WAN, "Wan2.2-TI2V-5B-Q4_K_M.gguf", 3433116000,
                        "95b19697b7f98e65b0a543640e9ca7b4dfec32e2a6e3731e8e10708be52655e2"),
                  Datei(*_WAN, "VAE/Wan2.2_VAE.safetensors", 1409400960,
                        "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156", "vae"),
                  Datei(*_UMT5, "umt5-xxl-encoder-Q8_0.gguf", 6043068256,
                        "2521d4de0bf9e1cc6549866463ceae85e4ec3239bc6063f7488810be39033bbc", "text_encoders"),
                  Datei(*_UMT5_TOK, "google/umt5-xxl/tokenizer.json", 16837417,
                        "6e197b4d3dbd71da14b4eb255f4fa91c9c1f2068b20a2de2472967ca3d22602b", "tokenizer"))),
    Teil("portraet", "🎭 Porträt", "bewegte, sprechende Porträts (LivePortrait)", *_MIT,
         ziel=lambda: _erster(_PORTRAET, _INSTALL / "Models" / "LivePortrait"), da=_portraet_da,
         dateien=tuple(Datei(*_LP, f"liveportrait/{p}", g, s) for p, g, s in (
             ("base_models/appearance_feature_extractor.pth", 3387959,
              "5279bb8654293dbdf327030b397f107237dd9212fb11dd75b83dfb635211ceb5"),
             ("base_models/motion_extractor.pth", 112545506,
              "251e6a94ad667a1d0c69526d292677165110ef7f0cf0f6d199f0e414e8aa0ca5"),
             ("base_models/spade_generator.pth", 221813590,
              "4780afc7909a9f84e24c01d73b31a555ef651521a1fe3b2429bd04534d992aee"),
             ("base_models/warping_module.pth", 182180086,
              "2f61a6f265fe344f14132364859a78bdbbc2068577170693da57fb96d636e282"),
             ("retargeting_models/stitching_retargeting_module.pth", 2393098,
              "3652d5a3f95099141a56986aaddec92fadf0a73c87a20fac9a2c07c32b28b611"),
             ("landmark.onnx", 114666491, "31d22a5041326c31f19b78886939a634a5aedcaa5ab8b9b951a1167595d147db")))),
    Teil("krea", "🎨 Krea 2 (Bilder malen)", "Diffusionsmodell im ComfyUI-Format, NVFP4 oder bf16, dazu "
         "text_encoders/ (Qwen3-VL-4B), vae/ und tokenizer/", "eigene Lizenz von Krea – vor dem Laden lesen",
         f"{HF}/krea/Krea-2-Turbo", ziel=_krea_ordner, da=_krea_da, link=f"{HF}/krea/Krea-2-Turbo"),
)


# Grundmodelle des Einrichtungs-Helfers (einrichtungshelfer/gemmahelfer.py): eigener Ordner, damit sie nicht
# als Chat-Modell erscheinen. Immer Q4_K_M von unsloth.
_E2B = ("unsloth/gemma-4-E2B-it-GGUF", "0314792d7f1f7e229411f620751375812bb9faf2")
_E4B = ("unsloth/gemma-4-E4B-it-GGUF", "bfc15c382204943c3a8fff0c750b94ae2364d7a3")
_GEMMA_LIZENZ = ("Apache 2.0", "https://ai.google.dev/gemma/docs/gemma_4_license")


def _helfer_da(name: str) -> Callable[[], bool]:
    return lambda: (_INSTALL / "Models" / "helfer" / name).is_dir() and bool(_ggufs(_INSTALL / "Models" / "helfer" / name))


HELFER: dict[str, Teil] = {
    "gemma4-e2b": Teil("gemma4-e2b", "✨ Gemma 4 E2B", "Einrichtungs-Helfer für kleinere Grafikkarten", *_GEMMA_LIZENZ,
                       ziel=lambda: _INSTALL / "Models" / "helfer" / "gemma4-e2b", da=_helfer_da("gemma4-e2b"),
                       dateien=(Datei(*_E2B, "gemma-4-E2B-it-Q4_K_M.gguf", 3106738272,
                                      "740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8"),)),
    "gemma4-e4b": Teil("gemma4-e4b", "✨ Gemma 4 E4B", "Einrichtungs-Helfer ab etwa 8 GB Grafikspeicher", *_GEMMA_LIZENZ,
                       ziel=lambda: _INSTALL / "Models" / "helfer" / "gemma4-e4b", da=_helfer_da("gemma4-e4b"),
                       dateien=(Datei(*_E4B, "gemma-4-E4B-it-Q4_K_M.gguf", 4977171584,
                                      "85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87"),)),
}


def teil(id_: str) -> Teil | None:
    return next((t for t in KATALOG if t.id == id_), None) or HELFER.get(id_)


def stand() -> list[tuple[Teil, bool]]:
    """(Teil, eingerichtet?) für alle Einträge."""
    aus = []
    for t in KATALOG:
        try:
            da = bool(t.da())
        except Exception:
            da = False
        aus.append((t, da))
    return aus


def quelle(d: Datei) -> str:
    return f"{HF}/{d.repo}/blob/{d.revision}/{d.pfad}"


def ziel_ordner(t: Teil, d: Datei) -> Path:
    o = t.ziel()
    return o / d.unter if d.unter else o


def als_hf_datei(d: Datei):
    """Für hfladen.laden: Pfad, Größe und SHA-256 aus dem Katalog – nicht aus dem aktuellen Repo."""
    import hfladen
    return hfladen.Datei(pfad=d.pfad, groesse=d.groesse, sha256=d.sha256)
