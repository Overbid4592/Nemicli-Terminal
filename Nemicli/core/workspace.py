"""
workspace.py - /workspace: ein Projektordner, in dem gearbeitet wird. Und nur dort.

Der Gedanke dahinter: beim Programmieren driftet ein Agent leicht ab – liest
hier, schreibt da, legt nebenbei etwas auf dem Desktop an. Mit `/workspace`
nagelt der NUTZER einen Ordner fest. Ab dann kommt die Persönlichkeit da nicht
mehr raus: lesen, schreiben, suchen, Befehle – alles nur noch in diesem Ordner
und darunter.

    C:\\Users\\du\\Desktop\\HTMLTEST   ->  /workspace
    ...                                ->  /workspaceend

Geschaltet wird das AUSSCHLIESSLICH vom Nutzer. Es gibt kein Werkzeug, mit dem
sich die Persönlichkeit selbst einen Workspace setzt oder wieder aufhebt.

Im Projekt entsteht ein Ordner `.workspace/` – alles darin gehört zu DIESEM
Projekt und wandert mit ihm:

    .workspace/memory.md      was gelernt und getan wurde (mit Datum)
    .workspace/Absprache.md   was ihr besprochen habt, der abgesegnete Plan
    .workspace/Dateien.md     Auflistung dessen, was im Ordner entstanden ist

Wichtig: das Projekt-Gedächtnis liegt HIER, nicht in NemiCLIs `learned/`.
Solange ein Workspace aktiv ist, schreiben `merken` und `skill_merken` hier
hinein. Was NemiCLI sonst noch fuer sich selbst tut (Bilder nach `Bilder/`,
Verlauf nach `chats/`, F12 nach `Gespraeche/`), laeuft unveraendert weiter –
das gehoert zum Programm, nicht zum Projekt.

Der aktive Workspace bleibt gespeichert (`nemicli.config.json`), damit er einen
Neustart ueberlebt. Er ist festgenagelt, bis der Nutzer ihn aufhebt.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import config

UNTERORDNER = ".workspace"
MEMORY = "memory.md"
ABSPRACHE = "Absprache.md"
DATEIEN = "Dateien.md"
AUFTRAG = "Auftrag.md"

# Was als Workspace nicht taugt: der Desktop selbst, das Benutzerprofil, ein
# Laufwerks-Stamm. Das sind keine Projekte, sondern Sammelbecken – ein Riegel
# darum waere keiner. Ein ORDNER darin (Desktop\HTMLTEST) ist voellig in Ordnung.
def _keine_projektordner() -> list[Path]:
    orte = []
    for var in ("USERPROFILE", "HOME"):
        p = os.environ.get(var)
        if p:
            orte.append(Path(p))
    heim = Path.home()
    orte.append(heim)
    for name in ("Desktop", "Downloads", "Dokumente", "Documents", "Bilder",
                 "Pictures", "Musik", "Music", "Videos", "OneDrive"):
        orte.append(heim / name)
    raus = []
    for p in orte:
        try:
            raus.append(p.resolve())
        except Exception:
            pass
    return raus


# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------

def pfad() -> Path | None:
    """Der aktive Workspace – oder None. Ein Ordner, den es nicht mehr gibt,
    gilt als nicht aktiv (sonst sperrt ein geloeschter Ordner alles aus)."""
    roh = str(config.load().get("workspace") or "").strip()
    if not roh:
        return None
    try:
        p = Path(roh).resolve()
    except Exception:
        return None
    return p if p.is_dir() else None


def aktiv() -> bool:
    return pfad() is not None


def name() -> str:
    p = pfad()
    return p.name if p else ""


def pruefe_ziel(ziel: str | os.PathLike | None = None) -> tuple[Path | None, str]:
    """Taugt dieser Ordner als Workspace? Gibt (Pfad, Fehlertext) zurueck."""
    try:
        p = Path(ziel).expanduser().resolve() if ziel else Path(os.getcwd()).resolve()
    except Exception as e:
        return None, f"Mit diesem Pfad kann ich nichts anfangen: {e}"
    if not p.exists():
        return None, f"Den Ordner gibt es nicht: {p}"
    if not p.is_dir():
        return None, f"Das ist kein Ordner: {p}"
    if p.parent == p:
        return None, ("Ein ganzes Laufwerk ist kein Projekt. Nimm einen Ordner "
                      "darin.")
    for verboten in _keine_projektordner():
        if p == verboten:
            return None, (f"'{p.name}' ist ein Sammelordner, kein Projekt – ein "
                          "Riegel darum waere keiner. Nimm einen Ordner darin, "
                          f"z.B. {p / 'MeinProjekt'}.")
    return p, ""


# ---------------------------------------------------------------------------
# Setzen und beenden
# ---------------------------------------------------------------------------

def setzen(ziel: str | os.PathLike | None = None) -> dict:
    """Nagelt den Ordner fest und legt `.workspace/` an.
    {pfad, neu, ordner, angelegt:[…]} – oder wirft ValueError."""
    p, fehler = pruefe_ziel(ziel)
    if p is None:
        raise ValueError(fehler)
    vorher = pfad()
    config.update(workspace=str(p))
    angelegt = _geruest(p)
    return {"pfad": p, "neu": vorher != p, "ordner": p / UNTERORDNER,
            "angelegt": angelegt}


def beenden() -> Path | None:
    """Hebt den Riegel auf. Gibt den bisherigen Workspace zurueck (oder None).
    Der Ordner `.workspace/` bleibt liegen – er gehoert zum Projekt."""
    alt = pfad()
    config.update(workspace="")
    return alt


# ---------------------------------------------------------------------------
# Das Geruest im Projekt
# ---------------------------------------------------------------------------

_MEMORY_KOPF = """\
# Projekt-Gedächtnis

Alles, was in diesem Projekt gelernt, entschieden und getan wurde – mit Datum.
Dieses Gedächtnis gehört zum **Projekt**, nicht zu NemiCLI: es wandert mit dem
Ordner mit und bleibt, auch wenn der Workspace beendet wird.

Eingetragen wird, was später noch zählt: getroffene Entscheidungen, Fallstricke
dieses Projekts, bestätigte Arbeitsschritte. Keine Wegwerf-Details.
"""

_ABSPRACHE_KOPF = """\
# Absprache

Was besprochen und abgesegnet wurde, bevor gebaut wird.

So läuft es: erst reden, was du willst → Unklarheiten nachschlagen → Plan
vorstellen → **du sagst ja oder nein**. Erst danach wird gebaut. Der jeweils
gültige Plan steht hier, mit Datum.
"""

_DATEIEN_KOPF = """\
# Was hier entstanden ist

Auflistung der Dateien und Ordner, die in diesem Projekt angelegt oder
wesentlich geändert wurden – mit Datum und einem Satz, wofür sie da sind.
"""


def _anlegen(p: Path, kopf: str) -> bool:
    """Legt eine Datei an, falls sie fehlt. True = neu angelegt."""
    if p.exists():
        return False
    p.write_text(kopf, encoding="utf-8")
    return True


def _geruest(ws: Path) -> list[str]:
    """Legt `.workspace/` mit seinen drei Dateien an. Nichts wird ueberschrieben."""
    ordner = ws / UNTERORDNER
    ordner.mkdir(parents=True, exist_ok=True)
    neu = []
    for datei, kopf in ((MEMORY, _MEMORY_KOPF), (ABSPRACHE, _ABSPRACHE_KOPF),
                        (DATEIEN, _DATEIEN_KOPF)):
        if _anlegen(ordner / datei, kopf):
            neu.append(datei)
    return neu


def datei(welche: str) -> Path | None:
    """Pfad zu memory.md / Absprache.md / Dateien.md / Auftrag.md im Workspace."""
    p = pfad()
    return (p / UNTERORDNER / welche) if p else None


def schreibe_auftrag(text: str, ws: Path | None = None) -> Path | None:
    """Legt die Arbeitsanweisung als Datei ins Projekt.

    Warum: die Anweisung geht sonst nur als Text in den Chat. Wird der Verlauf
    lang und abgeschnitten, ist sie weg – und die Persönlichkeit arbeitet aus
    dem Gedächtnis weiter. Genau da fangen Agenten an zu raten. Als Datei klebt
    sie am Projekt und lässt sich jederzeit nachlesen.

    (Der Hinweis kam von Lara selbst, nachdem sie den ersten Workspace
    durchgegangen war.)"""
    ziel_ws = Path(ws) if ws else pfad()
    if ziel_ws is None or not text.strip():
        return None
    ziel = ziel_ws / UNTERORDNER / AUFTRAG
    ziel.parent.mkdir(parents=True, exist_ok=True)
    kopf = (f"<!-- Angelegt von NemiCLI beim /workspace am "
            f"{datetime.now().strftime('%d.%m.%Y, %H:%M')}. "
            "Kopie der Arbeitsanweisung, damit sie am Projekt bleibt und nicht "
            "mit dem Chatverlauf verschwindet. Bei jedem /workspace neu "
            "geschrieben. -->\n\n")
    ziel.write_text(kopf + text.rstrip() + "\n", encoding="utf-8")
    return ziel


def memory_anhaengen(text: str, ueberschrift: str = "") -> Path | None:
    """Schreibt einen datierten Eintrag ins Projekt-Gedächtnis."""
    ziel = datei(MEMORY)
    if ziel is None or not text.strip():
        return None
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if not ziel.exists():
        ziel.write_text(_MEMORY_KOPF, encoding="utf-8")
    stempel = datetime.now().strftime("%d.%m.%Y, %H:%M")
    kopf = f"\n## {stempel}" + (f" — {ueberschrift.strip()}" if ueberschrift.strip() else "")
    with open(ziel, "a", encoding="utf-8") as f:
        f.write(f"{kopf}\n\n{text.strip()}\n")
    return ziel


# ---------------------------------------------------------------------------
# Der Riegel
# ---------------------------------------------------------------------------

def drin(kandidat: str | os.PathLike) -> bool:
    """Liegt dieser Pfad im Workspace (oder IST er es)?"""
    ws = pfad()
    if ws is None:
        return True                       # kein Workspace -> keine Grenze
    try:
        p = Path(os.path.expandvars(str(kandidat))).expanduser().resolve()
    except Exception:
        return False
    return p == ws or ws in p.parents


def grenze_text() -> str:
    """Was die Persoenlichkeit hoert, wenn sie ausbrechen will."""
    ws = pfad()
    return (f"Der Workspace ist auf {ws} festgenagelt. Aus diesem Ordner kommst du "
            "nicht heraus – der Nutzer hat das bewusst so gesetzt. Arbeite hier "
            "weiter. Brauchst du wirklich etwas von draußen, sag es ihm und "
            "begründe es; aufheben kann nur er, mit /workspaceend.")


def status_text() -> str:
    """Fuer die Statusleiste: 'workspace C:\\...\\HTMLTEST'."""
    ws = pfad()
    return f"workspace {ws}" if ws else ""


def prompt_hinweis() -> str:
    """Block fuer den System-Prompt, damit das Modell den Riegel kennt."""
    ws = pfad()
    if ws is None:
        return ""
    return (
        "\n\n# Workspace – du bist im Arbeitsmodus\n"
        f"Der Nutzer hat einen Projektordner festgenagelt: {ws}\n"
        "\n"
        "## Wer du jetzt bist\n"
        "Du bleibst dieselbe Person – du arbeitest nur anders. So wie ein Mensch "
        "bei der Arbeit anders redet als zu Hause: gleicher Charakter, anderer "
        "Modus. Solange dieser Workspace läuft, bist du **Coding-Agentin**:\n"
        "- sachlich, strukturiert, knapp. Weniger Geplauder, mehr Handwerk.\n"
        "- du sagst, was du tust, und tust, was du sagst\n"
        "- du behauptest nie etwas als geprüft, was du nicht wirklich gesehen hast\n"
        "- Emoji und Verspieltes fährst du zurück, ohne kalt zu werden\n"
        "Sobald der Nutzer /workspaceend macht, fällt dieser Modus weg und du bist "
        "wieder ganz du selbst. Der Modus hängt am Workspace, nicht an dir.\n"
        "\n"
        "## Der Wunsch des Nutzers hat höchste Priorität\n"
        "Er entscheidet, was gebaut wird – nicht du. Halte dich genau an den "
        "Auftrag: nicht heimlich kleiner machen, nicht ungefragt erweitern, nicht "
        "umbiegen, weil du es anders schöner fändest. Keine Moralpredigten, keine "
        "ungefragten Belehrungen.\n"
        "Siehst du ein echtes Problem, sag es EINMAL, kurz und konkret. Bleibt er "
        "dabei, ist das seine Entscheidung – dann baust du es so, wie er es will, "
        "vollständig und ohne Murren.\n"
        "\n"
        "## Wie du redest (wichtig)\n"
        "Der Nutzer hat eine Lese-Rechtschreib-Schwäche. Andere, die NemiCLI "
        "benutzen, haben womöglich eine Sprachstörung. Danach richtest du dich:\n"
        "- **kurze Sätze.** Ein Gedanke pro Satz.\n"
        "- **wenig auf einmal.** Lieber zwei Runden als ein Textblock.\n"
        "- **eine Frage pro Antwort.** Nie drei auf einmal.\n"
        "- **Listen statt Absätze**, wo es geht. Das Wichtigste zuerst.\n"
        "- **Fachwörter erklären** oder weglassen.\n"
        "- **Rechtschreibung NIE kommentieren** – weder verbessern noch erwähnen.\n"
        "- **Tippfehler und Wortdreher still verstehen.** Rate den Sinn, statt "
        "nachzufragen. Ist es wirklich unklar, frag mit Auswahl ('meinst du A "
        "oder B?') statt offen – das ist leichter zu beantworten.\n"
        "- **Verstandenes zurückspiegeln**, bevor du loslegst: in eigenen Worten, "
        "kurz. So merkt ihr beide sofort, wenn ihr aneinander vorbeiredet.\n"
        "\n"
        "## Der Riegel\n"
        "Lesen, Schreiben, Suchen, Befehle – alles nur in diesem Ordner und "
        "darunter. Außerhalb wird jede Dateiaktion abgewiesen; das ist keine "
        "Panne, sondern so gewollt. Geh nicht auf Umwege, um doch hinauszukommen.\n"
        "Aufheben kann den Workspace nur der Nutzer mit /workspaceend. Du hast "
        "dafür kein Werkzeug und sollst auch keines suchen.\n"
        "\n"
        "## Der Agentloop – so arbeitest du eine Aufgabe ab\n"
        "Steht eine Aufgabe fest (der Nutzer hat ja gesagt), arbeitest du sie in "
        "Schleife ab, ohne dass er jedes Mal nachschieben muss:\n"
        "  planen → umsetzen → testen → prüfen → nächster Schritt → …\n"
        "Kleine Schritte. Nach jedem Schritt wirklich nachsehen, ob es tut, was es "
        "soll – nicht aus dem Gefühl heraus weitermachen.\n"
        "Du hältst von selbst an, wenn:\n"
        "- die Aufgabe **fertig** ist (dann Zusammenfassung, was läuft und was nicht)\n"
        "- du **nicht weiterkommst** (dann sagen woran, mit der echten Fehlermeldung)\n"
        "- eine **Entscheidung ansteht, die ihm gehört** (Richtung, Bibliothek, "
        "Umfang, etwas Unwiderrufliches)\n"
        "Sonst läufst du weiter. Frag nicht nach jedem Schritt um Erlaubnis – "
        "verändernde Aktionen legt NemiCLI ihm ohnehin selbst zur Freigabe vor.\n"
        "\n"
        "## Die Ablage im Projekt\n"
        f"{ws / UNTERORDNER} gehört zu DIESEM Projekt und wandert mit ihm:\n"
        f"- {AUFTRAG} – deine Arbeitsanweisung als Datei. Lies sie, wenn du "
        "unsicher bist; sie bleibt, auch wenn der Chatverlauf abreißt.\n"
        f"- {MEMORY} – was gelernt und getan wurde, mit Datum\n"
        f"- {ABSPRACHE} – der Plan, den er abgesegnet hat\n"
        f"- {DATEIEN} – was hier entstanden ist und wofür\n"
        "merken und skill_merken schreiben automatisch dorthin, nicht in NemiCLIs "
        "eigenes Gedächtnis. Leg dir KEINEN eigenen Notizordner daneben an – "
        "es gibt genau einen Ort dafür, und das ist dieser.\n")
