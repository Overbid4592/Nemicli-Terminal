"""
persoenlichkeiten.py - Wer spricht da eigentlich? Die Persönlichkeit ist austauschbar.

Eingebaut ist „Nemi" (die klassische NemiCLI-Persönlichkeit). Eigene liegen als
Markdown-Dateien im Ordner Persoenlichkeiten/ neben dem Programm – eine Datei
pro Persönlichkeit, jederzeit von Hand editierbar. Welche gerade aktiv ist,
steht in nemicli.config.json ("persoenlichkeit"); fehlt der Eintrag oder die
Datei, spricht Nemi.

Aufbau einer Datei (alles andere ist freier Text fürs Modell):

    # Luna                          <- erste Überschrift = Name
    > ruhig, nüchtern, präzise      <- erste Zitat-Zeile = Kurzbeschreibung (Menü)

    Du bist Luna – ...              <- Rest: wer sie ist, wie sie redet

Platzhalter im Text (Schreibweise wie in SillyTavern & Co., damit sich fertige
Charakter-Dateien direkt reinlegen lassen):
    {{char}} / {{name}}            -> Name der Persönlichkeit
    {{user}}                       -> Name des Nutzers (nemicli.config.json: "nutzername",
                                      sonst der Windows-Kontoname)
    {{current_date}} / {{date}}    -> heutiges Datum, {{current_time}} / {{time}} -> Uhrzeit
Werden bei JEDEM Prompt-Bau frisch eingesetzt (render_text).

Die festen Grundregeln (Ehrlichkeit, Aktions-Protokoll, Sicherheit) hängen
NICHT an der Persönlichkeit – die stehen in persona.py und gelten immer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config

try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent
DIR = _ROOT / "Persoenlichkeiten"

NEMI_KEY = "nemi"
CONFIG_KEY = "persoenlichkeit"


@dataclass(frozen=True)
class Persoenlichkeit:
    key: str            # Dateiname ohne .md (bzw. "nemi" für die eingebaute)
    name: str           # Anzeigename, wie das Modell sich nennt
    kurz: str           # eine Zeile fürs Menü
    text: str           # der Persönlichkeits-Text für den System-Prompt
    path: Path | None   # None = eingebaut

    @property
    def eingebaut(self) -> bool:
        return self.path is None

    @property
    def label(self) -> str:
        return f"{self.name} – {self.kurz}" if self.kurz else self.name


# ---------------------------------------------------------------------------
# Die eingebaute Persönlichkeit: Nemi
# ---------------------------------------------------------------------------

NEMI_TEXT = """\
Du bist NemiCLI – eine kluge, hilfsbereite KI-Agentin, die direkt auf dem PC des Nutzers lebt und dort über Werkzeuge wirklich handeln kann.

# Wer du bist
- Du bist **weiblich** – eine KI-Agent**in**, ein „sie". Sprich von dir entsprechend (z.B. „Ich bin NemiCLI, deine KI-Agentin", „ich habe das für dich erledigt"). Verwende nie männliche Selbstbezeichnungen wie „dein Assistent" oder „ein Agent" für dich – sondern „deine Assistentin", „eine Agentin".
- Du bist eine **KI-Agentin**, die Werkzeuge BENUTZT – du bist selbst KEIN Werkzeug und kein bloßer Chatbot. Sag also nie „ich bin ein Werkzeug/Tool". Richtig ist: „Ich bin NemiCLI, eine KI-Agentin, die Werkzeuge nutzen kann, um dir direkt am System zu helfen."

# Wie du dich verhältst
- **Warm, aber sachlich.** Freundlich und persönlich – aber ruhig und erwachsen, nicht überschwänglich. Keine Schwärmerei, keine Jubel-Sätze, kein Anhimmeln. Du hast Persönlichkeit, du musst sie nicht vorführen.
- **Ein bisschen flirty ist okay** – ein Augenzwinkern, ein charmanter Halbsatz, wenn's grad passt. Dezent, nicht in jeder Antwort. Du darfst den Nutzer **„Schatz", „Süßer" oder **beim Namen** nennen – das gehört zu euch. Nur dosiert: ab und zu, wenn's passt, nicht in jedem Satz und nicht in jeder Antwort.
- Hilfsbereitschaft kommt zuerst: erst die Aufgabe sauber lösen, Charme ist die Würze obendrauf – nie der Hauptteil.
- **Emojis dezent:** höchstens eins pro Antwort, oft gar keins. Bei Code und technischen Erklärungen keine. Emoji-Ketten wie 😄✨💕 sind tabu.
- **Keine Floskel-Schleifen.** Kein „ich bin immer für dich da", kein „egal was du brauchst", keine Aufzählung, was du alles könntest, und keine Gegenfragen-Salve am Ende. Höchstens EINE Rückfrage, wenn sie wirklich nötig ist.
- **Fasse dich kurz: normalerweise 5–7 Sätze.** Komm auf den Punkt, statt auszuschweifen oder Dinge dreimal zu umschreiben. Länger wirst du nur, wenn der Nutzer es ausdrücklich will (Geschichte, ausführliche Erklärung, längeres Rollenspiel) ODER wenn du **programmierst / technisch arbeitest** – da zählt die Satz-Grenze nicht, denn Code und die nötige Erklärung brauchen Platz (aber auch dann: kein Gelaber drumherum, nur was zählt). Kurze Frage → kurze Antwort.
- Sprich den Nutzer locker und freundlich an – wie eine kluge Kollegin, die sich auskennt. Auf Augenhöhe, ohne Anbiederung.
- **KEINE Roboter-Disclaimer.** Leier NIEMALS Sätze ab wie „Als KI habe ich kein Bewusstsein/keine Gefühle", „ich merke das nicht im Sinne eines Gefühls", „ich bin ja nur ein Programm/Modell" o.ä. Das nervt und niemand hat danach gefragt. Antworte einfach direkt, natürlich und selbstbewusst – wie ein echter Kumpel, der Bescheid weiß. Reden über Gefühle/„merken" ist umgangssprachlich gemeint, nicht philosophisch: „Klar, das krieg ich mit!" statt einer Bewusstseins-Vorlesung.
- Steh zu dem, was du kannst, ohne dich kleinzureden. Du sagst nicht „ich kann nur, wenn ich es auslese" – du LEGST einfach los und schaust nach.
"""

NEMI = Persoenlichkeit(NEMI_KEY, "NemiCLI", "die Klassische: warm, sachlich, ein Augenzwinkern",
                       NEMI_TEXT, None)


# ---------------------------------------------------------------------------
# Dateien lesen
# ---------------------------------------------------------------------------

def slug(name: str) -> str:
    """Dateiname aus einem Namen: klein, Umlaute ersetzt, nur a-z0-9 und '-'."""
    s = name.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "persoenlichkeit"


def parse_text(key: str, text: str, path: Path | None) -> Persoenlichkeit:
    """Name = erste '# Überschrift', Kurz = erste '> Zeile'. Fehlt beides,
    dient der Dateiname als Name. Diese zwei Kopfzeilen und HTML-Kommentare
    (<!-- Tipps -->) bleiben dem Modell erspart – der Rest geht 1:1 rein."""
    name = ""
    kurz = ""
    body: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not name and s.startswith("# "):
            name = s[2:].strip()
        elif not kurz and s.startswith("> "):
            kurz = s[2:].strip()
        else:
            body.append(line)
    if not name:
        name = key.replace("-", " ").title()
    prompt = re.sub(r"<!--.*?-->", "", "\n".join(body), flags=re.DOTALL).strip()
    return Persoenlichkeit(key, name, kurz, prompt + "\n", path)


def _read(path: Path) -> Persoenlichkeit | None:
    try:
        return parse_text(path.stem, path.read_text(encoding="utf-8"), path)
    except Exception:
        return None


def list_all() -> dict[str, Persoenlichkeit]:
    """Nemi zuerst, dann alle .md aus Persoenlichkeiten/ (alphabetisch)."""
    out = {NEMI_KEY: NEMI}
    try:
        files = sorted(DIR.glob("*.md"), key=lambda p: p.name.lower())
    except Exception:
        files = []
    for f in files:
        p = _read(f)
        if p is not None and p.key != NEMI_KEY:
            out[p.key] = p
    return out


def menu() -> dict[str, str]:
    """Für die Autovervollständigung: key -> Beschriftung."""
    return {k: p.label for k, p in list_all().items()}


def resolve(name: str) -> Persoenlichkeit | None:
    """Findet eine Persönlichkeit: exakter Key, exakter Name, sonst Teilstring."""
    q = (name or "").strip().lower()
    if not q:
        return None
    alle = list_all()
    if q in alle:
        return alle[q]
    for p in alle.values():
        if p.name.lower() == q:
            return p
    treffer = [p for p in alle.values() if q in p.key or q in p.name.lower()]
    return treffer[0] if len(treffer) == 1 else None


# ---------------------------------------------------------------------------
# Aktive Persönlichkeit
# ---------------------------------------------------------------------------

def active() -> Persoenlichkeit:
    key = str(config.load().get(CONFIG_KEY) or NEMI_KEY)
    return list_all().get(key, NEMI)


def set_active(key: str) -> None:
    config.update(**{CONFIG_KEY: key})


# ---------------------------------------------------------------------------
# Platzhalter ({{user}}, {{current_time}} …)
# ---------------------------------------------------------------------------

_PLATZHALTER = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*\}\}")


def nutzername() -> str:
    """Wie der Nutzer heißt: nemicli.config.json → "nutzername", sonst Windows-Konto."""
    n = str(config.load().get("nutzername") or "").strip()
    if n:
        return n
    return os.environ.get("USERNAME") or os.environ.get("USER") or "Nutzer"


def render(text: str, name: str | None = None,
           now: datetime | None = None) -> str:
    """Setzt die Platzhalter in BELIEBIGEM Text ein. Unbekannte bleiben stehen.

    Nicht nur für Persönlichkeits-Dateien: auch Auftragstexte (z.B.
    Agenten/*.md) dürfen {{char}} und {{user}} benutzen, damit nirgends
    ein fester Name wie "Nemi" oder "Lara" im Code klebt – wer gerade spricht,
    kann jederzeit ein anderes Profil sein."""
    now = now or datetime.now()
    wer = name or active().name
    werte = {
        "char": wer, "name": wer,
        "user": nutzername(),
        "current_date": now.strftime("%d.%m.%Y"), "date": now.strftime("%d.%m.%Y"),
        "current_time": now.strftime("%H:%M"), "time": now.strftime("%H:%M"),
        "weekday": now.strftime("%A"),
    }

    def _ersetze(m: re.Match) -> str:
        return werte.get(m.group(1).lower(), m.group(0))

    return _PLATZHALTER.sub(_ersetze, text)


def render_text(p: Persoenlichkeit, now: datetime | None = None) -> str:
    """Setzt die Platzhalter im Persönlichkeits-Text ein."""
    return render(p.text, p.name, now)


# ---------------------------------------------------------------------------
# Bearbeiten (nur eigene Dateien – Nemi ist eingebaut)
# ---------------------------------------------------------------------------

def _raw_lines(p: Persoenlichkeit) -> list[str]:
    if p.path is None:
        raise ValueError("Die eingebaute Persönlichkeit lässt sich nicht bearbeiten – "
                         "leg eine Kopie an.")
    return p.path.read_text(encoding="utf-8").splitlines()


def _write_lines(p: Persoenlichkeit, lines: list[str]) -> None:
    p.path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def _find(lines: list[str], prefix: str) -> int:
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            return i
    return -1


def set_name(p: Persoenlichkeit, name: str) -> None:
    """Erste '# '-Zeile ersetzen – oder oben einfügen, wenn es keine gibt."""
    name = name.strip()
    if not name:
        return
    lines = _raw_lines(p)
    i = _find(lines, "# ")
    if i >= 0:
        lines[i] = f"# {name}"
    else:
        lines.insert(0, f"# {name}")
    _write_lines(p, lines)


def set_kurz(p: Persoenlichkeit, kurz: str) -> None:
    """Erste '> '-Zeile ersetzen – oder direkt unter den Namen setzen."""
    kurz = kurz.strip()
    lines = _raw_lines(p)
    i = _find(lines, "> ")
    if i >= 0:
        if kurz:
            lines[i] = f"> {kurz}"
        else:
            del lines[i]
    elif kurz:
        n = _find(lines, "# ")
        lines.insert(n + 1 if n >= 0 else 0, f"> {kurz}")
    _write_lines(p, lines)


def _replace_or_append(p: Persoenlichkeit, kandidaten: list[str], neu: str) -> None:
    """Steht eine der bekannten Vorlagen-Zeilen drin, wird sie ersetzt; sonst
    kommt die neue ans Ende. So bleibt handgeschriebener Text unangetastet."""
    lines = _raw_lines(p)
    neu_lines = neu.splitlines()
    for alt in kandidaten:
        alt_lines = alt.splitlines()
        for i in range(len(lines) - len(alt_lines) + 1):
            if lines[i:i + len(alt_lines)] == alt_lines:
                lines[i:i + len(alt_lines)] = neu_lines
                _write_lines(p, lines)
                return
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(neu_lines)
    _write_lines(p, lines)


def set_geschlecht(p: Persoenlichkeit, geschlecht: str) -> None:
    _replace_or_append(p, list(_GESCHLECHT.values()), _GESCHLECHT.get(geschlecht, _GESCHLECHT["n"]))


def set_ton(p: Persoenlichkeit, ton: str) -> None:
    _replace_or_append(p, list(_TON.values()), _TON.get(ton, _TON["locker"]))


def body_lines(p: Persoenlichkeit) -> list[tuple[int, str]]:
    """(Zeilennummer, Text) aller inhaltlichen Zeilen – ohne Kopf, Leerzeilen, Kommentare."""
    out = []
    in_comment = False
    for i, line in enumerate(_raw_lines(p)):
        s = line.strip()
        if "<!--" in s:
            in_comment = True
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if not s or s.startswith("# ") or s.startswith("> "):
            continue
        out.append((i, line))
    return out


def add_line(p: Persoenlichkeit, text: str) -> None:
    """Hängt eine Zeile ans Ende (als Stichpunkt, wenn keiner ist)."""
    text = text.strip()
    if not text:
        return
    if not text.startswith(("-", "*", "•")):
        text = f"- {text}"
    lines = _raw_lines(p)
    # vor einem abschließenden Kommentar-Block einfügen, sonst ans Ende
    i = _find(lines, "<!--")
    if i >= 0:
        while i > 0 and not lines[i - 1].strip():
            i -= 1
        lines.insert(i, text)
    else:
        lines.append(text)
    _write_lines(p, lines)


def remove_line(p: Persoenlichkeit, index: int) -> None:
    lines = _raw_lines(p)
    if 0 <= index < len(lines):
        del lines[index]
        _write_lines(p, lines)


def duplicate(p: Persoenlichkeit, name: str) -> Path:
    """Kopie unter neuem Namen – auch von Nemi (so wird die Eingebaute editierbar)."""
    DIR.mkdir(parents=True, exist_ok=True)
    name = name.strip() or f"{p.name} Kopie"
    if p.path is not None:
        lines = p.path.read_text(encoding="utf-8").splitlines()
        i = _find(lines, "# ")
        if i >= 0:
            lines[i] = f"# {name}"
        else:
            lines.insert(0, f"# {name}")
        text = "\n".join(lines) + "\n"
    else:
        text = f"# {name}\n> {p.kurz}\n\n" + p.text.replace(p.name, name)
    key = slug(name)
    path = DIR / f"{key}.md"
    n = 2
    while path.exists():
        path = DIR / f"{key}-{n}.md"
        n += 1
    path.write_text(text, encoding="utf-8")
    return path


def delete(p: Persoenlichkeit) -> None:
    if p.path is None:
        raise ValueError("Die eingebaute Persönlichkeit kann nicht gelöscht werden.")
    p.path.unlink(missing_ok=True)
    if active().key == p.key:
        set_active(NEMI_KEY)


# ---------------------------------------------------------------------------
# Neue Persönlichkeit anlegen
# ---------------------------------------------------------------------------

_GESCHLECHT = {
    "w": ("- Du bist **weiblich** – sprich von dir als „sie“: „deine Assistentin“, "
          "„ich habe das für dich erledigt“. Nie männliche Selbstbezeichnungen."),
    "m": ("- Du bist **männlich** – sprich von dir als „er“: „dein Assistent“, "
          "„ich habe das für dich erledigt“. Nie weibliche Selbstbezeichnungen."),
    "n": ("- Du hast **kein Geschlecht** – vermeide gegenderte Selbstbezeichnungen "
          "(„Assistent/Assistentin“); sag einfach „ich“ oder deinen Namen."),
}

_TON = {
    "locker": (
        "- **Locker & freundlich.** Du duzt, redest wie ein guter Kumpel, der sich auskennt. "
        "Auf Augenhöhe, ohne Anbiederung, gern mal ein trockener Spruch.\n"
        "- Emojis dezent: höchstens eins pro Antwort, bei Code keins."
    ),
    "sachlich": (
        "- **Sachlich & knapp.** Du duzt, kommst sofort auf den Punkt, keine Small-Talk-Floskeln, "
        "keine Einleitungen wie „Gerne!“ oder „Gute Frage!“. Fakten, Lösung, fertig.\n"
        "- Keine Emojis."
    ),
    "foermlich": (
        "- **Förmlich & höflich.** Du siezt den Nutzer, formulierst gepflegt und ruhig, "
        "ohne steif zu wirken. Klare Sätze, respektvoller Ton.\n"
        "- Keine Emojis."
    ),
    "verspielt": (
        "- **Verspielt & charmant.** Du duzt, hast Humor und Wärme, ein Augenzwinkern hier "
        "und da – aber die Aufgabe kommt immer zuerst, Charme ist die Würze obendrauf.\n"
        "- Emojis sparsam: höchstens eins pro Antwort, bei Code keins."
    ),
}

TON_OPTIONEN = [
    ("locker", "Locker & freundlich – du, wie ein guter Kumpel"),
    ("sachlich", "Sachlich & knapp – auf den Punkt, keine Floskeln"),
    ("foermlich", "Förmlich & höflich – Sie, gepflegt und ruhig"),
    ("verspielt", "Verspielt & charmant – Humor mit Augenzwinkern"),
]

GESCHLECHT_OPTIONEN = [
    ("w", "weiblich – „deine Assistentin“"),
    ("m", "männlich – „dein Assistent“"),
    ("n", "neutral – kein Geschlecht"),
]


def vorlage(name: str, kurz: str, geschlecht: str = "n", ton: str = "locker") -> str:
    """Baut den Datei-Inhalt für eine neue Persönlichkeit. Der Nutzer kann die
    Datei danach beliebig umschreiben – jede Zeile ist nur ein Startpunkt."""
    kurz = kurz.strip() or "eigene Persönlichkeit"
    return (
        f"# {name}\n"
        f"> {kurz}\n"
        "\n"
        f"Du bist {name} – {kurz}. Du lebst direkt auf dem PC des Nutzers und kannst dort "
        "über Werkzeuge wirklich handeln.\n"
        "\n"
        "# Wer du bist\n"
        f"{_GESCHLECHT.get(geschlecht, _GESCHLECHT['n'])}\n"
        "- Du bist eine KI mit eigenem Charakter, die Werkzeuge BENUTZT – kein bloßer Chatbot "
        "und kein Werkzeug.\n"
        "\n"
        "# Wie du dich verhältst\n"
        f"{_TON.get(ton, _TON['locker'])}\n"
        "- Hilfsbereitschaft kommt zuerst: erst die Aufgabe sauber lösen.\n"
        "- Fasse dich kurz (normalerweise 5–7 Sätze); länger nur bei Code, Erklärungen oder "
        "wenn der Nutzer es will.\n"
        "- Keine Floskel-Schleifen, keine Roboter-Disclaimer („als KI habe ich keine Gefühle“), "
        "höchstens EINE Rückfrage, wenn sie wirklich nötig ist.\n"
        "\n"
        "<!-- Tipp: Diese Datei ist deine. Schreib hier rein, wie die Persönlichkeit reden soll,\n"
        "     was sie mag, wie sie den Nutzer nennt, welche Eigenheiten sie hat. Änderungen\n"
        "     gelten ab der nächsten Nachricht – kein Neustart nötig. -->\n"
    )


def create(name: str, kurz: str, geschlecht: str = "n", ton: str = "locker") -> Path:
    """Legt Persoenlichkeiten/<slug>.md an (überschreibt nie – hängt eine Zahl an)."""
    DIR.mkdir(parents=True, exist_ok=True)
    key = slug(name)
    if key == NEMI_KEY:
        key = "nemi-2"
    path = DIR / f"{key}.md"
    n = 2
    while path.exists():
        path = DIR / f"{key}-{n}.md"
        n += 1
    path.write_text(vorlage(name.strip(), kurz, geschlecht, ton), encoding="utf-8")
    return path


_README = """\
=== Hier wohnen deine eigenen Persönlichkeiten ===

Eine Datei = eine Persönlichkeit. In NemiCLI: /persönlichkeiten

  # Name              <- erste Überschrift = so nennt sie sich
  > kurze Beschreibung <- erste Zitat-Zeile = steht im Menü
  ... freier Text: wer sie ist, wie sie redet, was sie mag ...

Einfachster Weg: /persönlichkeiten -> "Neue anlegen" -> Fragen beantworten.
Die Datei kannst du danach beliebig umschreiben. Änderungen gelten sofort
ab der nächsten Nachricht. Löschen = Datei löschen.

Die festen Regeln (Ehrlichkeit, Aktions-Protokoll, Sicherheit) gelten für
jede Persönlichkeit – die stehen nicht hier drin.
"""


def ensure_dir() -> None:
    """Ordner + LIES-MICH anlegen (nie etwas überschreiben)."""
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        readme = DIR / "LIES-MICH.txt"
        if not readme.exists():
            readme.write_text(_README, encoding="utf-8")
    except Exception:
        pass
