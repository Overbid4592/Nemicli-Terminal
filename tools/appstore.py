"""
appstore.py - Apps aus HTML, CSS und JavaScript, deren Funktionen die KI als Werkzeuge aufrufen kann.

Eine App ist ein Ordner unter Apps/ im Datenordner:

    Apps/<name>/
        app.json      Titel, Beschreibung, Icon, Version und die Funktionen für die KI
        index.html    was man sieht
        style.css     wie es aussieht (frei)
        app.js        was es tut; Funktionen für die KI meldet es so an:
                          NemiApp.funktion("wuerfeln", ({seiten = 6}) => …, "Würfelt eine Zahl")
        icon.svg      das Bild im Store (oder icon.png)

app.json:  {"titel": "Würfel", "beschreibung": "…", "icon": "icon.svg", "version": "1.0",
            "funktionen": [{"name": "wuerfeln", "beschreibung": "…", "felder": {"seiten": "Zahl, Vorgabe 6"}}]}

Die KI ruft eine Funktion im eigenen JavaScript-Interpreter NemiJS auf (nemiview, ohne Browser und ohne Internet);
das Ergebnis kommt als JSON zurück. Store und Apps zeigt NemiView mit eigener Engine (python -m nemiview,
NemiCLI-Schalter --nemiview); die Verbindung zur Sitzung ist eine Named Pipe mit Schlüssel, kein Server.

debugAI: Jede App bekommt im Fenster einen 🐞-Knopf. Er zeigt, welche App es ist, fragt, was stört, und lässt
auf die Stelle tippen – NemiCLI gibt der KI das Element (Selektor, Text, Position, CSS) und die Dateien der App.
Vor der Änderung wird die App unter Apps/_vorher/ gesichert; danach vorher → nachher, Behalten oder Zurück.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

try:
    from paths import ASSETS as _ASSETS, DATEN as _DATEN
except Exception:
    _ASSETS = _DATEN = Path(__file__).resolve().parent.parent

ORDNER = Path(_DATEN) / "Apps"
SPEICHER = ORDNER / "_daten"                     # localStorage je App (NemiView)
VORLAGEN = Path(_ASSETS) / "appstore_vorlagen"     # mitgelieferte Beispiel-Apps
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_FUNKTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
ICONS = ("icon.svg", "icon.png")



@dataclass
class Funktion:
    name: str
    beschreibung: str = ""
    felder: dict = field(default_factory=dict)


@dataclass
class App:
    name: str
    ordner: Path
    titel: str
    beschreibung: str
    version: str
    icon: str | None
    funktionen: list[Funktion]

    def funktion(self, name: str) -> Funktion | None:
        return next((f for f in self.funktionen if f.name == name), None)


# ---------------------------------------------------------------------------
# Apps finden
# ---------------------------------------------------------------------------

def _lesen(ordner: Path) -> App | None:
    """Eine App aus ihrem Ordner – None, wenn etwas Wesentliches fehlt."""
    if not _NAME.match(ordner.name) or not (ordner / "index.html").is_file():
        return None
    try:
        roh = json.loads((ordner / "app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        roh = {}
    if not isinstance(roh, dict):
        roh = {}
    funktionen = []
    for f in roh.get("funktionen") or []:
        if isinstance(f, dict) and _FUNKTION.match(str(f.get("name") or "")):
            felder = f.get("felder") if isinstance(f.get("felder"), dict) else {}
            funktionen.append(Funktion(str(f["name"]), str(f.get("beschreibung") or "")[:300],
                                       {str(k)[:40]: str(v)[:200] for k, v in felder.items()}))
    icon = str(roh.get("icon") or "")
    if not (icon in ICONS and (ordner / icon).is_file()):
        icon = next((i for i in ICONS if (ordner / i).is_file()), None)
    return App(ordner.name, ordner, str(roh.get("titel") or ordner.name)[:60],
               str(roh.get("beschreibung") or "")[:400], str(roh.get("version") or "1.0")[:20], icon, funktionen)


def vorlagen_einrichten() -> int:
    """Beim ersten Öffnen die Beispiel-Apps in den Apps-Ordner legen (nur fehlende). Gibt die Anzahl zurück."""
    ORDNER.mkdir(parents=True, exist_ok=True)
    n = 0
    weg = _entfernte()
    if VORLAGEN.is_dir():
        for quelle in sorted(p for p in VORLAGEN.iterdir() if p.is_dir()):
            ziel = ORDNER / quelle.name
            if not ziel.exists() and quelle.name not in weg and _lesen(quelle) is not None:
                shutil.copytree(quelle, ziel)
                n += 1
    return n


def _entfernt_datei() -> Path:
    return ORDNER / "_entfernt.json"                # entfernte Beispiel-Apps kommen nicht von selbst zurück


def _entfernte() -> set[str]:
    try:
        daten = json.loads(_entfernt_datei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(n) for n in daten} if isinstance(daten, list) else set()


def entfernen(name: str) -> str:
    """App samt gespeicherten Daten in den Papierkorb (/undo holt sie zurück). Nie endgültig löschen."""
    import snapshot
    a = app(name)
    if a is None or a.ordner.parent != ORDNER:
        raise ValueError(f"Keine App „{name}“")
    if snapshot.sichern(a.ordner, "app_entfernen", verschieben=True) is None:
        raise OSError("Papierkorb nicht erreichbar – nichts entfernt")
    daten = ORDNER / "_daten" / f"{a.name}.json"           # localStorage der App (wie SPEICHER)
    if daten.is_file():
        snapshot.sichern(daten, "app_entfernen", verschieben=True)
    if (VORLAGEN / a.name).is_dir():
        _entfernt_datei().write_text(json.dumps(sorted(_entfernte() | {a.name})), encoding="utf-8")
    return f"„{a.titel}“ liegt im Papierkorb (/undo holt die App zurück)."


def apps() -> dict[str, App]:
    if not ORDNER.is_dir():
        return {}
    aus = {}
    for d in sorted(p for p in ORDNER.iterdir() if p.is_dir()):
        a = _lesen(d)
        if a is not None:
            aus[a.name] = a
    return aus


def app(name: str) -> App | None:
    return apps().get(str(name or "").strip().lower())


def liste_text() -> str:
    """Übersicht für die KI: Apps und ihre Funktionen."""
    alle = apps()
    if not alle:
        return (f"Noch keine Apps in {ORDNER}. Eine App ist ein Ordner mit app.json, index.html, style.css, app.js "
                "und icon.svg; Funktionen meldet app.js mit NemiApp.funktion(name, fn, beschreibung) an.")
    zeilen = [f"Apps in {ORDNER}:"]
    for a in alle.values():
        zeilen.append(f"- {a.name} „{a.titel}“ {a.version}: {a.beschreibung}")
        for f in a.funktionen:
            felder = ", ".join(f"{k} ({v})" for k, v in f.felder.items())
            zeilen.append(f"    · {f.name}({felder}) – {f.beschreibung}")
        if not a.funktionen:
            zeilen.append("    (keine Funktionen für die KI)")
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Funktion aufrufen (NemiJS, ohne Browser und ohne Internet)
# ---------------------------------------------------------------------------

def aufrufen(name: str, funktion: str, felder: dict | None = None) -> tuple[bool, str]:
    """Funktion einer App ausführen: (ok, Text mit dem Ergebnis als JSON). Läuft im eigenen
    JavaScript-Interpreter NemiJS – ohne Browser, ohne Netz, mit Rechen- und Zeitgrenzen."""
    a = app(name)
    if a is None:
        return False, f"Keine App „{name}“. Vorhanden: {', '.join(apps()) or 'keine'}."
    if a.funktion(funktion) is None:
        return False, (f"Die App „{a.titel}“ hat keine Funktion „{funktion}“. Vorhanden: "
                       + (", ".join(f.name for f in a.funktionen) or "keine"))
    from nemiview import appjs
    ok, erg = appjs.aufrufen(a.ordner / "app.js", funktion, felder or {})
    if not ok:
        return False, f"Fehler in der App „{a.titel}“: {erg}"
    text = json.dumps(erg, ensure_ascii=False)
    return True, f"{a.titel} · {funktion}: {text[:4000]}"


# ---------------------------------------------------------------------------
# Zeige-Editor: aus dem gezeigten Element einen Auftrag für die KI machen
# ---------------------------------------------------------------------------

def auftrag(a: App, wunsch: str, element: dict) -> str:
    """Nachricht an die KI: Wunsch, gezeigtes Element, Dateien der App."""
    e = element if isinstance(element, dict) else {}
    rect = e.get("rect") if isinstance(e.get("rect"), dict) else {}
    css = e.get("css") if isinstance(e.get("css"), dict) else {}
    teile = [f"🐞 debugAI – Änderungswunsch in meiner App „{a.titel}“ (App-ID {a.name}, Version {a.version}, "
             f"Ordner {a.ordner}): {wunsch.strip()[:600]}"]
    if not e:
        teile.append("Keine Stelle gezeigt – der Wunsch betrifft die App allgemein.")
    else:
        teile.append(f"Gezeigtes Element: <{str(e.get('tag') or '?')[:20]}> · Selektor `{str(e.get('selektor') or '')[:200]}`"
                     + (f" · Text „{str(e.get('text') or '')[:120]}“" if e.get("text") else ""))
        if rect:
            teile.append("Position im Fenster: " + ", ".join(f"{k} {int(float(rect.get(k) or 0))}"
                                                            for k in ("x", "y", "breite", "hoehe")))
        if css:
            teile.append("Jetziges CSS: " + "; ".join(f"{str(k)[:30]}: {str(v)[:60]}" for k, v in list(css.items())[:16]))
    teile.append("Dateien: " + ", ".join(p.name for p in sorted(a.ordner.iterdir()) if p.is_file()) + ". "
                 "Lies die passende Datei, ändere genau diese Stelle (datei_bearbeiten) im Stil der App und sag kurz, "
                 "was du geändert hast. "
                 "Der vorige Stand ist gesichert; das Fenster lädt die App danach neu und der Nutzer vergleicht "
                 "vorher → nachher.")
    return "\n".join(teile)


DESIGN = """So soll die App aussehen – wie aus einem guten App-Store, nicht wie ein Entwurf:
- Eigene Stimmung: eine passende, kräftige Farbwelt (3–5 Farben als CSS-Variablen), nicht einfach Grau und Lila.
  Verläufe, Rundungen, Schatten und klare Abstände; eine große, gut lesbare Überschrift.
- Grafik statt Klötze: Figuren, Gegenstände und Hintergründe als eigene SVG-Dateien im App-Ordner (<img> oder
  background-image) oder als Emoji – nie nur ein farbiges Rechteck als Spielfigur. Hintergründe in Ebenen.
- Bedienung: große Knöpfe mit :hover und :active, sichtbare Anzeige (Punkte, Level, Zeit, Leben), kurze Anleitung.
- Spiele: Startbild mit Titel, Spielen, Pause, „Game Over“/„Geschafft“ mit Neustart, Bestwert in localStorage,
  steigende Schwierigkeit; Bewegung über setInterval (ca. 30 Bilder/s) und style-Werte – Rückmeldung bei Treffern
  (kurz Farbe, Größe oder Emoji wechseln).
- Eine eigene Idee dazu, die die App besonders macht (Thema, Belohnung, kleine Überraschung).
- icon.svg: ein klares, farbiges Motiv mit Hintergrund in abgerundetem Quadrat, das die App sofort erkennen lässt."""


def bauauftrag() -> str:
    """Rahmen für eine neue App aus dem Store: wohin, welche Dateien, wie sie aussehen soll, danach prüfen."""
    return (f"Bau die App als neuen Ordner unter {ORDNER} (Name in Kleinbuchstaben, ohne Leerzeichen, noch nicht "
            "vergeben – app_liste zeigt die vorhandenen): app.json (titel, beschreibung, icon, version), index.html, "
            "style.css, app.js, icon.svg und die Grafiken als SVG. Halte dich an das, was NemiView kann.\n\n"
            + DESIGN + "\n\nDanach app_pruefen aufrufen, Fehler beheben und kurz sagen, was die App kann. "
            "Sie erscheint von selbst im Store.")


def stand(a: App) -> str:
    """Kennung des Dateistands – ändert sie sich, lädt der Editor neu."""
    teile = []
    for p in sorted(a.ordner.rglob("*")):
        if p.is_file():
            st = p.stat()
            teile.append(f"{p.name}:{st.st_size}:{st.st_mtime_ns}")
    return str(abs(hash("|".join(teile))))


def dateien(a: App) -> list[str]:
    return [p.relative_to(a.ordner).as_posix() for p in sorted(a.ordner.rglob("*")) if p.is_file()][:60]


# ---------------------------------------------------------------------------
# debugAI: Sicherung vor der Änderung, Behalten oder Zurück
# ---------------------------------------------------------------------------

def _vorher(name: str) -> Path:
    return ORDNER / "_vorher" / name           # „_vorher“ ist kein gültiger App-Name: kein Eintrag im Store


def sicherung(name: str) -> dict | None:
    """Offene (noch nicht entschiedene) Sicherung einer App: {"wuensche": [...]} – oder None."""
    if not _NAME.match(str(name or "")) or not _vorher(name).is_dir():
        return None
    try:
        meta = json.loads(_vorher(name).with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    w = meta.get("wuensche") if isinstance(meta, dict) else None
    return {"wuensche": [str(x) for x in w] if isinstance(w, list) else []}


def sichern(a: App) -> bool:
    """Stand der App vor einer Änderung sichern. Eine offene Sicherung bleibt, damit „vorher“ der Stand vor
    der ersten unentschiedenen Änderung ist. True, wenn jetzt neu gesichert wurde."""
    ziel = _vorher(a.name)
    if ziel.is_dir():
        return False
    ziel.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(a.ordner, ziel)
    ziel.with_suffix(".json").write_text('{"wuensche": []}', encoding="utf-8")
    return True


def vermerken(name: str, wunsch: str) -> None:
    """Umgesetzten Wunsch zur offenen Sicherung schreiben (für die Anzeige beim Entscheiden)."""
    meta = sicherung(name)
    if meta is None or not wunsch.strip():
        return
    meta["wuensche"] = (meta["wuensche"] + [wunsch.strip()[:200]])[-10:]
    _vorher(name).with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def verwerfen(name: str) -> bool:
    """Behalten: die Sicherung wegwerfen."""
    if sicherung(name) is None:
        return False
    shutil.rmtree(_vorher(name), ignore_errors=True)
    _vorher(name).with_suffix(".json").unlink(missing_ok=True)
    return True


def zurueckholen(name: str) -> bool:
    """Zurück: die App auf den gesicherten Stand setzen – auch wenn die Änderung sie unlesbar gemacht hat."""
    if sicherung(name) is None:
        return False
    ordner, weg = ORDNER / name, _vorher(name).with_name(name + ".weg")
    shutil.rmtree(weg, ignore_errors=True)
    if ordner.exists():
        ordner.rename(weg)
    try:
        shutil.copytree(_vorher(name), ordner)
    except OSError:
        if weg.exists() and not ordner.exists():
            weg.rename(ordner)
        raise
    shutil.rmtree(weg, ignore_errors=True)
    return verwerfen(name)
