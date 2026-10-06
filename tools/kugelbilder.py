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
  2. Je Stimmung ein Bild über imagegen.paint (Krea 2, WebUI oder ComfyUI – was gerade
     aktiv ist), 1024×1024, gleicher Seed, ganze Sätze ohne Negativ-Prompt.
  3. rembg stellt frei; Rand knapp beschneiden, auf Quadrat auffüllen, 512 px.
  4. Ablage in Persoenlichkeiten/<Name>/, alte Bilder vorher in den Papierkorb.
Die Kugel merkt es beim nächsten Takt von selbst.

MOTIVE (kugelmotive.py, 20 Bereiche): /kugel malen <bereich> · alle malt mit derselben
Beschreibung und demselben Seed wie die Grundbilder (figur.json) und legt sie unter
Persoenlichkeiten/<Name>/<motiv>.png ab. Gemalt wird nur, was fehlt.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

try:
    from paths import ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent

ORDNER = _ROOT / "Persoenlichkeiten"
GROESSE = 512
SCHRITTE = 10                       # kleine Bilder: 10 Schritte reichen
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
# Die Ausdrücke als Satz – Krea 2 und die WebUI-Modelle wollen Sätze statt Stichworte
SAETZE = {
    "":      "The expression is calm and gentle.",
    "froh":  "The face shows a warm, happy smile with bright eyes.",
    "ernst": "The expression is serious and focused, the brow slightly furrowed.",
    "denkt": "The expression is thoughtful, the gaze turned slightly upward.",
}

# Rahmen um die Beschreibung: Porträt, mittig, einfarbiger Hintergrund –
# der ist fürs Freistellen wichtiger als jedes Detail der Figur.
RAHMEN_SATZ = ("Head-and-shoulders portrait, centered and facing the viewer, "
               "in front of a plain, flat, solid bright green background.")
# Motive brauchen Hände und Dinge – deshalb Oberkörper statt nur Kopf
RAHMEN_MOTIV_SATZ = ("Upper-body view of a single character, centered, "
                     "in front of a plain, flat, solid bright green background.")


def prompt_bauen(beschreibung: str, ausdruck: str, stimmung: str = "") -> str:
    satz = SAETZE.get(stimmung) or (ausdruck.strip().rstrip(".") + ".")
    return f"{beschreibung.strip().rstrip(',.')}. {satz} {RAHMEN_SATZ}"


def motiv_ordner(name: str) -> Path:
    """Persoenlichkeiten/<Name>/ – alle Bilder einer Persönlichkeit, Grundbilder und Motive."""
    sauber = "".join(c for c in name if c.isalnum() or c in "-_").strip("-_")
    return ORDNER / (sauber or "Persoenlichkeit")


def dateiname(name: str, stimmung: str) -> Path:
    return motiv_ordner(name) / f"{stimmung or 'neutral'}.png"


def _grundbild(p: Path) -> bool:
    return p.stem.lower() in {s or "neutral" for s, _ in STIMMUNGEN}


def figur(name: str) -> dict | None:
    """Beschreibung und Seed der Grundbilder – damit alle Motive dieselbe Figur zeigen."""
    try:
        d = json.loads((motiv_ordner(name) / "figur.json").read_text(encoding="utf-8"))
        return d if d.get("beschreibung") else None
    except Exception:
        return None


def _figur_merken(name: str, beschreibung: str, seed: int) -> None:
    ziel = motiv_ordner(name)
    ziel.mkdir(parents=True, exist_ok=True)
    (ziel / "figur.json").write_text(json.dumps({"beschreibung": beschreibung, "seed": seed},
                                                ensure_ascii=False, indent=1), encoding="utf-8")


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
    teil = ziel.with_suffix(".tmp")
    quadrat.save(teil, "PNG")                         # erst ganz schreiben, dann umbenennen:
    teil.replace(ziel)                                # ein Abbruch hinterlässt kein halbes Bild
    return ziel


def _maler():
    """Die Malfunktion – im Test austauschbar."""
    import imagegen
    return imagegen.paint


def malen(name: str, beschreibung: str, *, seed: int | None = None, on_status=None,
          stimmungen=None) -> list[Path]:
    """Alle Stimmungsbilder für `name` malen und ablegen. Gibt die Zieldateien zurück."""
    if not beschreibung.strip():
        raise RuntimeError("Keine Beschreibung – wie soll sie aussehen?")
    stimmungen = list(stimmungen or STIMMUNGEN)
    seed = random.randint(0, 2 ** 31 - 1) if seed is None else int(seed)
    paint = _maler()
    fertig: list[Path] = []
    alte = vorhandene(name)
    for i, (stimmung, ausdruck) in enumerate(stimmungen, 1):
        was = stimmung or "neutral"
        if on_status:
            on_status(f"Bild {i}/{len(stimmungen)} · {was} · male …")
        roh = paint(prompt_bauen(beschreibung, ausdruck, stimmung),
                    size=(1024, 1024), seed=seed, steps=SCHRITTE,
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
    alt = figur(name)
    if alt and (alt.get("beschreibung"), alt.get("seed")) != (beschreibung, seed):
        _motive_weg(name)                             # neue Figur: alte Motive passen nicht mehr
    _figur_merken(name, beschreibung, seed)
    if on_status:
        on_status(f"Bild {len(stimmungen)}/{len(stimmungen)} · fertig (Seed {seed})")
    return fertig


def _motive_weg(name: str) -> int:
    """Motive der alten Figur in den Papierkorb."""
    n = 0
    try:
        import snapshot
    except Exception:
        return 0
    for f in motiv_ordner(name).glob("*.png"):
        if _grundbild(f):
            continue
        try:
            if snapshot.sichern(f, "kugel_motive", verschieben=True):
                n += 1
        except Exception:
            pass
    return n


def motive_fehlend(name: str, schluessel: list[str]) -> list[str]:
    ordner = motiv_ordner(name)
    return [k for k in schluessel if not (ordner / f"{k}.png").exists()]


def motive_malen(name: str, schluessel: list[str], *, on_status=None, abbruch=None) -> list[Path]:
    """Fehlende Motive malen – gleiche Figur (figur.json) wie die Grundbilder.
    `abbruch()` -> True beendet nach dem laufenden Bild."""
    import kugelmotive
    f = figur(name)
    if not f:
        raise RuntimeError("Noch keine Figur – erst /kugel malen (Grundbilder).")
    offen = motive_fehlend(name, schluessel)
    paint = _maler()
    ordner = motiv_ordner(name)
    fertig: list[Path] = []
    for i, k in enumerate(offen, 1):
        if abbruch and abbruch():
            break
        deutsch, englisch = kugelmotive.MOTIVE[k]
        kopf = f"Bild {i}/{len(offen)} · {deutsch}"
        prompt = (f"{f['beschreibung'].strip().rstrip(',.')}. The character is {englisch}. "
                  f"{RAHMEN_MOTIV_SATZ}")
        if on_status:
            on_status(f"{kopf} · male …")
        roh = paint(prompt, size=(1024, 1024), seed=int(f["seed"]), steps=SCHRITTE,
                    on_status=(lambda m, kopf=kopf: on_status(f"{kopf} · {m}")) if on_status else None)
        if on_status:
            on_status(f"{kopf} · stelle frei …")
        fertig.append(freistellen(Path(roh), ordner / f"{k}.png"))
    return fertig


def entfernen(name: str) -> int:
    """Alle Bilder der Persönlichkeit in den Papierkorb – die Kugel ist wieder eine Kugel."""
    n = 0
    try:
        import snapshot
    except Exception:
        snapshot = None
    for p in vorhandene(name).values():
        if p.parent == motiv_ordner(name) and not _grundbild(p):
            continue                                  # Motive bleiben, nur die Grundbilder gehen
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
_FORM_SATZ = "ein bis zwei ganze Sätze in EINER Zeile, 20–60 Wörter (keine Stichwort-Liste)"
SELBSTBESCHREIBUNG_PROMPT = "Wie siehst du aus? Nur die eine Zeile."


def selbstbeschreibung_system(name: str, persoenlichkeit: str = "") -> str:
    """Auftrag für die Selbstbeschreibung – mit dem Text der aktiven Persönlichkeit."""
    text = SELBSTBESCHREIBUNG_SYSTEM.format(name=name, form=_FORM_SATZ)
    if persoenlichkeit.strip():
        text += "\n\n# Deine Persönlichkeit\n" + persoenlichkeit.strip()
    return text
