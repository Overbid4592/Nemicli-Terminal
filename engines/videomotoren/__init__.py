"""
videomotoren - Video-Modelle neben Wan 2.2 5B, je Bauart ein eigener Motor (zuerst LTX-2.5 mit Ton).

Ordner `Models/Videomodelle/` (Daten- und Programmordner): je Modell ein Unterordner mit dem Transformer
(.safetensors oder .gguf), Zubehör geteilt in `text_encoders/` und `vae/` oder im Modellordner.
Die Bauart wird an den Tensornamen erkannt, Text-Encoder, Video-VAE und Ton-VAE an ihren Namen und Formen.

Einstellungen (Config `video_motor_*`, jede mit AUTO): Auflösung, Format, Bilder/s, Schritte, CFG, Ton,
VAE-Kacheln, Speichermodus. AUTO richtet sich nach dem Grafikspeicher (ab 8 GB).
"""

from __future__ import annotations

import time
from pathlib import Path

from bildmotoren.gewichte import ist_folgeteil, namen, ohne_praefix

from . import ltx2

try:
    from paths import INSTALL as _INSTALL, ROOT as _ROOT
except Exception:
    _ROOT = _INSTALL = Path(__file__).resolve().parents[2]

BAUARTEN = {"ltx2": ltx2}
ORDNER = [_ROOT / "Models" / "Videomodelle", _INSTALL / "Models" / "Videomodelle"]
GETEILT = ("text_encoders", "vae")
BACKEND = "motor"
MAX_SEKUNDEN = 20

AUFLOESUNG = {                           # Stufe -> Format -> (Breite, Höhe), Vielfache von 32
    "480p": {"hochkant": (480, 832), "quer": (832, 480), "quadrat": (640, 640)},
    "720p": {"hochkant": (704, 1280), "quer": (1280, 704), "quadrat": (960, 960)},
    "1080p": {"hochkant": (1088, 1920), "quer": (1920, 1088), "quadrat": (1440, 1440)},
}
KACHELN = {"klein": (8, 8, 8), "mittel": (16, 16, 16), "gross": (24, 24, 24), "aus": (10_000, 10_000, 10_000)}
EINSTELLUNGEN = {                        # Schlüssel -> (Vorgabe, erlaubte Werte oder None)
    "aufloesung": ("auto", ("auto", "480p", "720p", "1080p")),
    "format": ("hochkant", ("hochkant", "quer", "quadrat")),
    "fps": (24, (24, 25, 30, 48, 50)),
    "schritte": (0, None),               # 0: Vorgabe des Modells
    "cfg": (0.0, None),                  # 0: Vorgabe des Modells
    "ton": (True, (True, False)),
    "kacheln": ("auto", ("auto", "klein", "mittel", "gross", "aus")),
    "speicher": ("auto", ("auto", "schnell", "sparsam")),
}


# ===========================================================================
#  Finden
# ===========================================================================

def ordner() -> Path:
    for o in ORDNER:
        if o.is_dir():
            return o
    return ORDNER[0]


def _kandidaten(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return [p for p in sorted(d.iterdir())
            if p.is_file() and p.suffix.lower() in (".gguf", ".safetensors") and not ist_folgeteil(p)]


_PASST: dict = {}


def _merken(schluessel, rechnen):
    if schluessel not in _PASST:
        try:
            _PASST[schluessel] = rechnen()
        except Exception:
            _PASST[schluessel] = None
    return _PASST[schluessel]


def bauart_von(pfad: Path) -> str | None:
    try:
        k = ("bauart", str(pfad), pfad.stat().st_mtime_ns)
    except OSError:
        return None
    return _merken(k, lambda: next((n for n, m in BAUARTEN.items() if m.erkennen(ohne_praefix(namen(pfad))[0])), None))


def modelle() -> dict[str, dict]:
    aus: dict[str, dict] = {}
    for o in ORDNER:
        if not o.is_dir():
            continue
        for p in _kandidaten(o):
            if (b := bauart_von(p)) and p.stem not in aus:
                aus[p.stem] = {"name": p.stem, "bauart": b, "dit": p, "ordner": o}
        for unter in sorted(x for x in o.iterdir() if x.is_dir() and x.name not in GETEILT):
            for p in _kandidaten(unter):
                if (b := bauart_von(p)) and unter.name not in aus:
                    aus[unter.name] = {"name": unter.name, "bauart": b, "dit": p, "ordner": unter}
                    break
    return aus


def _suche(m: dict, arten: tuple, passt) -> Path | None:
    orte = [m["ordner"] / a for a in arten] if m["ordner"] not in ORDNER else []
    orte += [o / a for o in ORDNER for a in arten]
    for o in orte:
        for p in _kandidaten(o):
            k = ("passt", str(p), p.stat().st_mtime_ns, passt, str(m["dit"]))
            if _merken(k, lambda: bool(passt(p, m["dit"]))):
                return p
    return None


def teile(name: str | None = None) -> dict:
    m = modelle().get(name or gewaehlt() or "")
    if m is None:
        return {}
    modul = BAUARTEN[m["bauart"]]
    return {"name": m["name"], "bauart": m["bauart"], "dit": m["dit"],
            "text": _suche(m, ("text_encoders",), modul.text_passt),
            "vae": _suche(m, ("vae",), modul.vae_passt),
            "ton": _suche(m, ("vae", "audio_vae"), modul.ton_passt)}


# ===========================================================================
#  Wahl und Einstellungen
# ===========================================================================

def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def gewaehlt() -> str | None:
    g = _config().get("video_motor_modell") or None
    alle = modelle()
    return g if g in alle else next(iter(alle), None)


def aktiv() -> bool:
    """Ist ein Video-Motor gewählt (statt Wan 2.2)?"""
    return _config().get("video_backend") == BACKEND and bool(modelle())


def waehlen(name: str) -> None:
    import config
    config.update(video_motor_modell=name, video_backend=BACKEND)


def abwaehlen() -> None:
    import config
    config.update(video_backend="")


def einstellung(schluessel: str):
    vorgabe, _ = EINSTELLUNGEN[schluessel]
    wert = _config().get(f"video_motor_{schluessel}", vorgabe)
    if isinstance(vorgabe, bool):
        return wert if isinstance(wert, bool) else str(wert).lower() not in ("false", "aus", "0", "nein", "off")
    try:
        return type(vorgabe)(wert)
    except (TypeError, ValueError):
        return vorgabe


def setzen(schluessel: str, wert) -> object:
    """Eine Einstellung prüfen und speichern; gibt den gespeicherten Wert zurück."""
    import config
    if schluessel not in EINSTELLUNGEN:
        raise ValueError(f"Unbekannte Einstellung: {schluessel}")
    vorgabe, erlaubt = EINSTELLUNGEN[schluessel]
    if isinstance(vorgabe, bool):
        wert = wert if isinstance(wert, bool) else str(wert).lower() in ("true", "an", "ja", "1", "on")
    else:
        wert = type(vorgabe)(wert)
    if erlaubt is not None and wert not in erlaubt:
        raise ValueError(f"{schluessel}: erlaubt sind {', '.join(map(str, erlaubt))}")
    config.update(**{f"video_motor_{schluessel}": wert})
    return wert


def vram_gb() -> float:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / (1 << 30)
    except Exception:
        pass
    return 0.0


def auto_aufloesung(vram: float | None = None) -> str:
    vram = vram_gb() if vram is None else vram
    return "480p" if vram < 11 else "720p"


def auto_kacheln(vram: float | None = None) -> str:
    vram = vram_gb() if vram is None else vram
    return "klein" if vram < 11 else ("mittel" if vram < 20 else "gross")


def _gilt(schluessel: str, extra: dict | None):
    """Wert für dieses Video: Überstimmung (geprüft) vor der gespeicherten Einstellung."""
    if extra and extra.get(schluessel) not in (None, ""):
        vorgabe, erlaubt = EINSTELLUNGEN[schluessel]
        w = extra[schluessel]
        if isinstance(vorgabe, bool):
            w = w if isinstance(w, bool) else str(w).lower() in ("true", "an", "ja", "1", "on")
        else:
            try:
                w = type(vorgabe)(w)
            except (TypeError, ValueError):
                w = None                                # ungültig: die gespeicherte Einstellung gilt
        if w is not None and (erlaubt is None or w in erlaubt):
            return w
    return einstellung(schluessel)


def plan(size: tuple[int, int] | None = None, extra: dict | None = None) -> dict:
    """Die wirksamen Einstellungen (AUTO aufgelöst) für das nächste Video; `extra` überstimmt einzelne."""
    stufe = _gilt("aufloesung", extra)
    stufe = auto_aufloesung() if stufe == "auto" else stufe
    B, H = size or AUFLOESUNG[stufe][_gilt("format", extra)]
    k = einstellung("kacheln")
    k = auto_kacheln() if k == "auto" else k
    speicher = einstellung("speicher")
    platz = None
    if speicher == "sparsam":
        try:
            from bildmotoren.gemeinsam import frei_bytes
            import torch
            platz = int(frei_bytes(torch.device("cuda")) * 0.5) if torch.cuda.is_available() else None
        except Exception:
            platz = None
    return {"aufloesung": stufe, "breite": (int(B) // 32) * 32, "hoehe": (int(H) // 32) * 32,
            "fps": _gilt("fps", extra), "schritte": einstellung("schritte") or None, "cfg": einstellung("cfg") or None,
            "ton": _gilt("ton", extra), "kacheln": KACHELN[k], "kacheln_name": k, "speicher": speicher, "platz": platz}


# ===========================================================================
#  Prüfen und Erzeugen
# ===========================================================================

def missing_reason(name: str | None = None) -> str | None:
    try:
        import torch  # noqa: F401
    except Exception:
        return "Es fehlt: PyTorch."
    if not modelle():
        return f"Kein Video-Modell in {ordner()}."
    t = teile(name)
    if not t:
        return f"Video-Modell '{name}' nicht gefunden."
    z = BAUARTEN[t["bauart"]].ZUBEHOER
    for art, wo in (("text", "text_encoders"), ("vae", "vae"), ("ton", "vae")):
        if t[art] is None:
            return f"Es fehlt: {z[art]} – nach {ordner() / wo}"
    return None


def entladen() -> None:
    from bildmotoren import gemeinsam
    gemeinsam.entladen()


def generate(prompt: str, *, sekunden: float = 5.0, size: tuple[int, int] | None = None, seed: int | None = None,
             steps: int | None = None, modell: str | None = None, on_status=None, abbruch=None,
             extra: dict | None = None) -> str:
    """Erzeugt ein Video (mit Ton, wenn eingeschaltet) und gibt den MP4-Pfad zurück. Blockiert.
    `extra`: Einstellungen nur für dieses Video (aufloesung, format, fps, ton)."""
    from bildmotoren import gemeinsam
    name = modell if modell in modelle() else gewaehlt()
    grund = missing_reason(name)
    if grund:
        raise RuntimeError(grund)
    t = teile(name)
    modul = BAUARTEN[t["bauart"]]
    p = plan(size, extra)
    if steps:
        p["schritte"] = int(steps)
    sekunden = min(max(float(sekunden), 1.0), MAX_SEKUNDEN)
    seed = int(time.time() * 1000) & 0x7FFFFFFF if seed is None else int(seed)
    beginn = time.time()
    fehler = None
    with gemeinsam.SPERRE:
        gemeinsam.uhr_halt()
        try:
            device, dtype = gemeinsam.geraet()
            bilder, ton, rate = modul.malen(prompt, t, sekunden=sekunden, breite=p["breite"], hoehe=p["hoehe"],
                                            fps=p["fps"], seed=seed, device=device, dtype=dtype, einstellungen=p,
                                            on_status=on_status, abbruch=abbruch)
        except Exception as e:
            fehler = f"{type(e).__name__}: {e}"
        try:
            gemeinsam.aufraeumen()
        except Exception:
            pass
        gemeinsam.uhr_neu(float(_config().get("video_motor_entladen", 300)))
        if fehler:
            raise RuntimeError(f"{modul.TITEL}: {fehler}")
    import profilordner as _profil
    import videodatei
    out = _profil.video_pfad(".mp4")
    if on_status:
        on_status(f"{modul.TITEL}: MP4 schreiben …")
    videodatei.schreiben(bilder, out, p["fps"], ton=(ton, rate) if ton is not None else None)
    try:
        import debugbericht
        debugbericht.ereignis(f"Video fertig ({t['name']}): {len(bilder)} Bilder {p['breite']}x{p['hoehe']}, "
                              f"Ton {'an' if ton is not None else 'aus'}, {time.time() - beginn:.0f} s -> {out.name}")
    except Exception:
        pass
    return str(out)


LIES_MICH = """\
=== Video-Modelle neben Wan 2.2 ===

Ein Unterordner je Modell, darin der Transformer (.safetensors oder .gguf). Text-Encoder und VAE
einmal geteilt in text_encoders/ und vae/ – oder im Modellordner.

LTX-2.5 (Lightricks, Video mit Ton):
  LTX25/ltx-2.5-22b-distilled-transformer-…safetensors   (oder eine GGUF, z. B. Q4_K_M)
  text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors
  vae/ltx-2.5-video-vae-conv-bf16.safetensors            (die Faltungs-Fassung)
  vae/ltx-2.5-audio-vae-bf16.safetensors                 (Ton)

Quellen: huggingface.co/Lightricks/LTX-2.5 (Anmeldung und Lizenz nötig), huggingface.co/unsloth/LTX-2.5-GGUF.
Die Lizenz „LTX-2.x Community License“ gilt für die Modelldateien.

Wenig Grafikspeicher? Eine GGUF nehmen; AUTO wählt Auflösung und VAE-Kacheln passend zur Karte.
Danach: /video -> Modell wählen.
"""


def ordner_anlegen() -> Path:
    o = ordner()
    for unter in GETEILT:
        (o / unter).mkdir(parents=True, exist_ok=True)
    zettel = o / "LIES-MICH.txt"
    if not zettel.exists():
        try:
            zettel.write_text(LIES_MICH, encoding="utf-8")
        except OSError:
            pass
    return o
