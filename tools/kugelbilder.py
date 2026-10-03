"""
kugelbilder.py - /kugel malen: die aktive Persönlichkeit malt sich ihr Gesicht für die Kugel.

Die Schwebekugel kann ein Bild sein (Persoenlichkeiten/<Name>.png), die Persönlichkeit
steuert per Aktion `kugel`, welche Stimmung sie zeigt (<Name>_froh.png, _ernst.png,
_denkt.png …). Das aktive Bildmodell malt vier Porträts mit FESTEM Seed und gleichem
Prompt, nur der Gesichtsausdruck wechselt – so bleibt es dieselbe Figur. Dann wird
freigestellt (rembg) und quadratisch abgelegt. Keine LoRA, kein Edit-Modell nötig.

ABLAUF
  1. Beschreibung: vom Nutzer (/kugel malen <text>) oder die Persönlichkeit
     beschreibt sich selbst (main.py fragt sie per ask_once, mit ihrem Persönlichkeits-
     Text – steht dort ihr Aussehen, gilt das).
  2. Je Stimmung ein Bild über imagegen.paint (ComfyUI/WebUI/eigene Pipeline –
     was gerade aktiv ist), 1024×1024, gleicher Seed. Die Prompt-Art folgt dem Motor:
     SD 1.5/SDXL Stichworte + Negativ-Prompt, Krea 2 und externe Motoren ganze Sätze
     ohne Negativ (dort setzt der Motor seine eigenen Werte).
  3. rembg stellt frei; Rand knapp beschneiden, auf Quadrat auffüllen, 512 px.
  4. Ablage in Persoenlichkeiten/, alte Bilder vorher in den Papierkorb.
Die Kugel merkt es beim nächsten Takt von selbst.
"""

from __future__ import annotations

import random
from pathlib import Path

try:
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

ORDNER = _ROOT / "Persoenlichkeiten"
GROESSE = 512
# rembg-Modell: der Standard (bria-rmbg 2.0) ist 1 GB – isnet-general-use kann Figuren
# und Zeichenstile genauso sauber und ist 176 MB. Wird beim ersten Mal nach ~/.rembg geladen.
REMBG_MODELL = "isnet-general-use"
_rembg_session = None

# (Dateizusatz, was das Gesicht zeigt) – Reihenfolge = Reihenfolge beim Malen
STIMMUNGEN = [
    ("",      "neutral calm expression, soft gentle look"),
    ("froh",  "smiling warmly, happy, bright eyes"),
    ("ernst", "serious concerned expression, focused, slightly furrowed brow"),
    ("denkt", "thoughtful expression, looking slightly upward, thinking"),
]
# Dieselben Ausdrücke als Satz – für Motoren, die Sätze statt Stichworte wollen (Krea 2)
SAETZE = {
    "":      "The expression is calm and gentle.",
    "froh":  "The face shows a warm, happy smile with bright eyes.",
    "ernst": "The expression is serious and focused, the brow slightly furrowed.",
    "denkt": "The expression is thoughtful, the gaze turned slightly upward.",
}

# Rahmen um die Beschreibung: Porträt, mittig, einfarbiger Hintergrund –
# der ist fürs Freistellen wichtiger als jedes Detail der Figur.
RAHMEN = ("portrait, head and shoulders, centered, facing the viewer, "
          "plain flat solid bright green background, clean sharp edges, no text, no watermark")
RAHMEN_SATZ = ("Head-and-shoulders portrait, centered and facing the viewer, "
               "in front of a plain, flat, solid bright green background.")
NEGATIV = "blurry, low quality, text, watermark, multiple people, cropped head, busy background, gradient background"


def sd_pipeline() -> bool:
    """True: eigene SD-1.5/SDXL-Pipeline (Stichworte + Negativ). Sonst Sätze, kein Negativ."""
    try:
        import imagegen
        return imagegen.backend() == "builtin"
    except Exception:
        return True


def prompt_bauen(beschreibung: str, ausdruck: str, stimmung: str = "", *, sd: bool = True) -> str:
    if sd:
        return f"{beschreibung.strip().rstrip(',.')}, {ausdruck}, {RAHMEN}"
    satz = SAETZE.get(stimmung) or (ausdruck.strip().rstrip(".") + ".")
    return f"{beschreibung.strip().rstrip(',.')}. {satz} {RAHMEN_SATZ}"


def dateiname(name: str, stimmung: str) -> Path:
    return ORDNER / (f"{name}_{stimmung}.png" if stimmung else f"{name}.png")


def vorhandene(name: str) -> dict[str, Path]:
    try:
        from wache import steuerung
        alt = steuerung.BILDER_ORDNER
        steuerung.BILDER_ORDNER = ORDNER
        try:
            return steuerung.bilder(name)
        finally:
            steuerung.BILDER_ORDNER = alt
    except Exception:
        return {}


def freistellen(quelle: Path, ziel: Path, groesse: int = GROESSE) -> Path:
    """Hintergrund weg (rembg), auf den Inhalt beschneiden, quadratisch auffüllen, skalieren."""
    from PIL import Image
    bild = Image.open(quelle).convert("RGBA")
    global _rembg_session
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise RuntimeError("rembg fehlt – pip install rembg[cpu]  (stellt den Hintergrund frei)") from exc
    if _rembg_session is None:
        _rembg_session = new_session(REMBG_MODELL)
    frei = remove(bild, session=_rembg_session)
    box = frei.getchannel("A").getbbox()
    if box:
        l, t, r, b = box
        rand = max(4, int(min(r - l, b - t) * 0.04))
        frei = frei.crop((max(0, l - rand), max(0, t - rand), min(frei.width, r + rand), min(frei.height, b + rand)))
    seite = max(frei.width, frei.height)
    quadrat = Image.new("RGBA", (seite, seite), (0, 0, 0, 0))
    quadrat.paste(frei, ((seite - frei.width) // 2, (seite - frei.height) // 2))
    quadrat = quadrat.resize((groesse, groesse), Image.LANCZOS)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    quadrat.save(ziel, "PNG")
    return ziel


def _maler():
    """Die Malfunktion – im Test austauschbar."""
    import imagegen
    return imagegen.paint


def malen(name: str, beschreibung: str, *, seed: int | None = None, on_status=None,
          stimmungen=None, sd: bool | None = None) -> list[Path]:
    """Alle Stimmungsbilder für `name` malen und ablegen. Gibt die Zieldateien zurück."""
    if not beschreibung.strip():
        raise RuntimeError("Keine Beschreibung – wie soll sie aussehen?")
    stimmungen = list(stimmungen or STIMMUNGEN)
    sd = sd_pipeline() if sd is None else sd
    seed = random.randint(0, 2 ** 31 - 1) if seed is None else int(seed)
    paint = _maler()
    fertig: list[Path] = []
    alte = vorhandene(name)
    for i, (stimmung, ausdruck) in enumerate(stimmungen, 1):
        was = stimmung or "neutral"
        if on_status:
            on_status(f"Bild {i}/{len(stimmungen)} · {was} · male …")
        roh, _nachbessern = paint(prompt_bauen(beschreibung, ausdruck, stimmung, sd=sd),
                                  neg=NEGATIV if sd else None,
                                  size=(1024, 1024), seed=seed,
                                  on_status=(lambda m, i=i, was=was: on_status(f"Bild {i}/{len(stimmungen)} · {was} · {m}"))
                                  if on_status else None)
        if on_status:
            on_status(f"Bild {i}/{len(stimmungen)} · {was} · stelle frei …")
        ziel = dateiname(name, stimmung)
        if ziel.exists():
            try:
                import snapshot
                snapshot.sichern(ziel, "kugel_malen", verschieben=True)
            except Exception:
                pass
        freistellen(Path(roh), ziel)
        fertig.append(ziel)
    if on_status:
        on_status(f"Bild {len(stimmungen)}/{len(stimmungen)} · fertig (Seed {seed})")
    return fertig


def entfernen(name: str) -> int:
    """Alle Bilder der Persönlichkeit in den Papierkorb – die Kugel ist wieder eine Kugel."""
    n = 0
    try:
        import snapshot
    except Exception:
        snapshot = None
    for p in vorhandene(name).values():
        try:
            if snapshot is not None and snapshot.sichern(p, "kugel_entfernen", verschieben=True):
                n += 1
            elif p.exists():
                p.unlink(); n += 1
        except Exception:
            pass
    return n


# Bewusst OHNE Beispielbild: ein Beispiel wie „anime girl, cyan hair“ wird vom Modell
# nachgeplappert. Wie sie aussieht, kommt aus ihrer Persönlichkeit, nicht aus dem Prompt.
SELBSTBESCHREIBUNG_SYSTEM = (
    "Du bist {name}. Ein Bildmodell soll ein Porträt von dir malen. Beschreibe dein Aussehen. "
    "Steht unten in deiner Persönlichkeit, wie du aussiehst, übernimm das genau – nichts "
    "dazuerfinden, nichts weglassen, woran man dich erkennt. Nur wenn dort nichts steht, "
    "entscheidest du frei, was du bist (Mensch, Tier, Roboter, Fabelwesen, abstrakt …) und in "
    "welchem Stil (Foto, Gemälde, Zeichnung, 3D-Render …). "
    "Englisch, {form}, nur Erscheinung: Art/Stil, Kopf/Gesicht, Haare oder Fell oder Oberfläche, "
    "Augen, Kleidung/Details, Ausstrahlung. "
    "Kein Hintergrund, kein Gesichtsausdruck, keine Erklärung, keine Anführungszeichen."
)
_FORM_SD = "EINE Zeile mit kommagetrennten Stichworten, 15–35 Wörter"
_FORM_SATZ = "ein bis zwei ganze Sätze in EINER Zeile, 20–60 Wörter (keine Stichwort-Liste)"
SELBSTBESCHREIBUNG_PROMPT = "Wie siehst du aus? Nur die eine Zeile."


def selbstbeschreibung_system(name: str, persoenlichkeit: str = "", *, sd: bool = True) -> str:
    """Auftrag für die Selbstbeschreibung – mit dem Text der aktiven Persönlichkeit,
    in der Prompt-Art des aktiven Motors."""
    text = SELBSTBESCHREIBUNG_SYSTEM.format(name=name, form=_FORM_SD if sd else _FORM_SATZ)
    if persoenlichkeit.strip():
        text += "\n\n# Deine Persönlichkeit\n" + persoenlichkeit.strip()
    return text
