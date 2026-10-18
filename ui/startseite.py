"""
startseite.py - Startseite im Vollbild: links die aktive Persönlichkeit (Bild aus farbigen
Halbblöcken, Name, Status, ihr eigener Gruß), rechts Karten – letzter Chat, Offenes, Wache,
ein Lieblingsbild. Leere Karten fallen weg; im schmalen Fenster steht alles untereinander.

Die Seite ist ein Reflow-Block (ui.banner_renderable): sie wird bei jeder Breite neu gebaut,
und `auffrischen()` zeichnet sie neu, sobald etwas nachkommt (Gruß, Wache).
"""

from __future__ import annotations

import base64
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.align import Align
from rich.box import ROUNDED
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

LINKS_BREITE = 24            # Kasten der Persönlichkeit (mit Rahmen)
BILD_SPALTEN = 18            # Bild darin: Spalten × Zeilen (eine Zeile = zwei Bildpunkte)
BILD_ZEILEN = 9
LIEBLING_SPALTEN, LIEBLING_ZEILEN = 12, 5
LEISTE_SPALTEN, LEISTE_ZEILEN = 6, 3   # Bild in der festen Leiste über dem Chat
NEBENEINANDER_AB = 72        # darunter: alles untereinander
KARTEN_PAAR_AB = 46          # rechte Spalte so breit: Offen und Wache nebeneinander
GRUSS_MAX = 160

STATUS = {"bereit": ("●", "#4ade80", "bereit"), "laedt": ("◌", "#fbbf24", "lädt …"),
          "ohne": ("○", "#7d8590", "kein Modell")}

# Im eigenen Terminal-Fenster (terminal_fenster.py) malt das Fenster echte Bilder über Platzhalter-Zeichen
# U+E000 + n·64 + Zeile; die Sitzung meldet Pfad und Zeilen per Steuerbefehl (main: _fenster_steuern).
# Sonst Halbblöcke.
STEUERN = None
PLATZ = 0xE000
_GEMELDET: dict[int, tuple[str, int]] = {}

STAND: dict = {"name": "", "bild": None, "status": "laedt", "gruss": "", "letzter": None, "offen": [],
               "wache": None, "liebling": None}
_BILDER: dict = {}


# ---------------------------------------------------------------------------
# Bild aus Halbblöcken
# ---------------------------------------------------------------------------

def bild_bloecke(pfad: Path, spalten: int, zeilen: int) -> Text | None:
    """Bild als Text: je Zeichen „▀“ mit oberem Bildpunkt als Schrift- und unterem als Hintergrundfarbe.
    Durchsichtige Stellen bleiben leer (Terminal-Hintergrund). None, wenn das Bild nicht lesbar ist."""
    try:
        schluessel = (str(pfad), Path(pfad).stat().st_mtime, spalten, zeilen)
    except OSError:
        return None
    if schluessel in _BILDER:
        return _BILDER[schluessel]
    try:
        from PIL import Image
        with Image.open(pfad) as roh:
            bild = roh.convert("RGBA")
        bild.thumbnail((spalten, zeilen * 2), Image.LANCZOS)
        zeilen = (bild.height + 1) // 2                    # keine leeren Zeilen über und unter dem Bild
        flaeche = Image.new("RGBA", (spalten, zeilen * 2), (0, 0, 0, 0))
        flaeche.paste(bild, ((spalten - bild.width) // 2, 0))
    except Exception:
        return None
    px = flaeche.load()
    out = Text(no_wrap=True, overflow="crop")
    for z in range(zeilen):
        for s in range(spalten):
            oben, unten = px[s, 2 * z], px[s, 2 * z + 1]
            o_da, u_da = oben[3] >= 128, unten[3] >= 128
            if o_da and u_da:
                out.append("▀", style=f"{_hex(oben)} on {_hex(unten)}")
            elif o_da:
                out.append("▀", style=_hex(oben))
            elif u_da:
                out.append("▄", style=_hex(unten))
            else:
                out.append(" ")
        if z < zeilen - 1:
            out.append("\n")
    if len(_BILDER) > 16:
        _BILDER.clear()
    _BILDER[schluessel] = out
    return out


def echtes_fenster() -> bool:
    return os.environ.get("NEMICLI_FENSTER") == "1" and STEUERN is not None


def bild_platz(pfad: Path, spalten: int, zeilen: int, nr: int) -> Text | None:
    """Platzhalter in Bildgröße für das eigene Fenster, das dort das echte Bild malt; meldet den Pfad
    einmal je Bild-Nummer. None, wenn das Bild nicht lesbar ist."""
    try:
        from PIL import Image
        with Image.open(pfad) as b:
            w, h = b.size
    except Exception:
        return None
    f = min(spalten / w, zeilen * 2 / h)
    breite, hoehe = max(1, round(w * f)), min(64, max(1, round(h * f / 2)))
    if _GEMELDET.get(nr) != (str(pfad), hoehe):
        try:
            STEUERN(f"bild{nr}-{hoehe}=" + base64.urlsafe_b64encode(str(pfad).encode("utf-8")).decode("ascii"))
            _GEMELDET[nr] = (str(pfad), hoehe)
        except Exception:
            return None
    return Text("\n".join(chr(PLATZ + nr * 64 + z) * breite for z in range(hoehe)), no_wrap=True,
                overflow="crop")


def _bild(pfad, spalten: int, zeilen: int, nr: int) -> Text | None:
    if echtes_fenster():
        platz = bild_platz(Path(pfad), spalten, zeilen, nr)
        if platz is not None:
            return platz
    return bild_bloecke(Path(pfad), spalten, zeilen)


def _hex(p) -> str:
    return f"#{p[0]:02x}{p[1]:02x}{p[2]:02x}"


# ---------------------------------------------------------------------------
# Daten sammeln
# ---------------------------------------------------------------------------

def wann(zeit: str, jetzt: datetime | None = None) -> str:
    """'2026-10-11 14:32' -> 'heute 14:32' / 'gestern 18:30' / '09.10. 18:30'."""
    try:
        t = datetime.strptime(zeit, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return zeit or ""
    heute = (jetzt or datetime.now()).date()
    if t.date() == heute:
        return f"heute {t:%H:%M}"
    if t.date() == heute - timedelta(days=1):
        return f"gestern {t:%H:%M}"
    return f"{t:%d.%m.} {t:%H:%M}"


def _start_marke() -> Path:
    import paths
    return Path(paths.DATEN) / "learned" / "start_zuletzt.txt"


def letzter_start() -> float | None:
    """Zeit des vorigen Starts; merkt sich dabei den jetzigen."""
    datei = _start_marke()
    try:
        vorher = float(datei.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        vorher = None
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(f"{time.time():.0f}", encoding="utf-8")
    except OSError:
        pass
    return vorher


def _neue_dateien(ordner: Path, endungen: tuple[str, ...], seit: float) -> int:
    try:
        return sum(1 for p in ordner.iterdir()
                   if p.is_file() and p.suffix.lower() in endungen and p.stat().st_mtime > seit)
    except OSError:
        return 0


def sammeln(seit: float | None = None) -> None:
    """Persönlichkeit, Bild, letzter Chat, Offenes, Lieblingsbild – jedes Teil für sich, Fehler lassen
    nur die Karte weg."""
    try:
        import persoenlichkeiten as PS
        STAND["name"] = PS.active().name
    except Exception:
        STAND["name"] = "Nemi"
    STAND["bild"] = None
    try:
        import profilbild                  # selbst gewählt, sonst das Standard-Kugel-Bild
        STAND["bild"] = profilbild.pfad()
    except Exception:
        pass
    STAND["letzter"] = None
    try:
        import chatstore
        if (c := chatstore.letzter()):
            STAND["letzter"] = {"titel": c["title"] or "ohne Titel", "wann": wann(c["updated"])}
    except Exception:
        pass
    offen: list[str] = []
    if seit:
        try:
            import profilordner
            n = _neue_dateien(profilordner.ordner("Bilder", anlegen=False), (".png", ".jpg", ".webp"), seit)
            v = _neue_dateien(profilordner.ordner("Videos", anlegen=False), (".mp4",), seit)
            if n:
                offen.append(f"🎨 {n} neue{'s' if n == 1 else ''} Bild{'er' if n > 1 else ''}")
            if v:
                offen.append(f"🎬 {v} neue{'s' if v == 1 else ''} Video{'s' if v > 1 else ''}")
        except Exception:
            pass
    try:
        import chatstore
        if chatstore.faellig():
            offen.append("🛟 Chat-Sicherung fällig")
    except Exception:
        pass
    STAND["offen"] = offen
    STAND["liebling"] = None
    try:
        import tagebuch
        da = [e for e in tagebuch.lieblinge() if Path(e["pfad"]).is_file()]
        if da:
            STAND["liebling"] = random.choice(da)
    except Exception:
        pass


def wache_text(laeuft: bool, statistik: dict) -> str:
    """Ampel für die Wache-Karte aus alarm_statistik()."""
    if not laeuft:
        return "⚪ aus  ·  /wache start"
    echt, offen = int(statistik.get("echt") or 0), int(statistik.get("offen") or 0)
    if echt:
        return f"🔴 {echt} echte{'r' if echt == 1 else ''} Alarm{'e' if echt > 1 else ''}  ·  /wache"
    if offen:
        return f"🟡 {offen} Alarm{'e' if offen > 1 else ''} offen  ·  /wache"
    return "🟢 alles ruhig"


def setzen(**werte) -> None:
    STAND.update(werte)
    auffrischen()


def offen_dazu(zeile: str) -> None:
    if zeile not in STAND["offen"]:
        STAND["offen"].append(zeile)
    auffrischen()


def auffrischen() -> None:
    try:
        import ui
        if ui.TUI is not None and hasattr(ui.TUI, "reflow_auffrischen"):
            ui.TUI.reflow_auffrischen()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Gruß
# ---------------------------------------------------------------------------

GRUSS_PROMPT = (
    "[NemiCLI wurde gerade geöffnet · {zeit} · {tag}] Du stehst auf der Startseite neben deinem Bild. "
    "Begrüße den Nutzer mit EINEM kurzen Satz (höchstens 14 Wörter), so wie du gerade bist – in deinem Ton, "
    "keine Floskel.{letzter} Antworte nur mit dem Satz, ohne Anführungszeichen.")
_TAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def gruss_prompt() -> str:
    jetzt = datetime.now()
    lz = STAND.get("letzter")
    zuletzt = f" Euer letzter Chat: „{lz['titel']}“ ({lz['wann']})." if lz else ""
    return GRUSS_PROMPT.format(zeit=jetzt.strftime("%H:%M"), tag=_TAGE[jetzt.weekday()], letzter=zuletzt)


def gruss_bereinigen(text: str) -> str:
    import re
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = " ".join(text.split()).strip().strip('"„“”»«').strip()
    if len(text) > GRUSS_MAX:
        text = text[:GRUSS_MAX].rsplit(" ", 1)[0] + " …"
    return text


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------

def _farbe() -> str:
    try:
        import ui
        return ui.persona_farbe(STAND["name"])
    except Exception:
        return "#8b7cff"


def _person() -> Group:
    farbe = _farbe()
    teile = []
    bild = _bild(STAND["bild"], BILD_SPALTEN, BILD_ZEILEN, 0) if STAND.get("bild") else None
    if bild is not None:
        teile += [Align.center(bild), Text("")]
    name = Text(STAND["name"] or "Nemi", style=f"bold {farbe}", justify="center")
    zeichen, f, wort = STATUS.get(STAND.get("status"), STATUS["laedt"])
    status = Text(justify="center")
    status.append(f"{zeichen} ", style=f)
    status.append(wort, style="muted")
    teile += [name, status]
    kasten = Panel(Group(*teile), box=ROUNDED, border_style=farbe, padding=(0, 1), width=LINKS_BREITE)
    gruss = []
    if STAND.get("gruss"):
        gruss = [Padding(Text(f"„{STAND['gruss']}“", style=f"italic {farbe}"), (0, 0, 0, 1))]
    return Group(kasten, *gruss)


def _karte(titel: str, inhalt) -> Panel:
    return Panel(inhalt, title=f"[brand]{titel}[/brand]", title_align="left", box=ROUNDED,
                 border_style="brand.dim", padding=(0, 1))


def _karten(breite: int) -> list:
    karten = []
    lz = STAND.get("letzter")
    if lz:
        t = Text()
        t.append(f"„{lz['titel']}“", style="assistant")
        t.append(f"  ·  {lz['wann']}", style="muted")
        t.append("\ngeht hier weiter – „weißt du noch …?“ findet Älteres", style="muted")
        karten.append(_karte("💬 Weiter, wo wir waren", t))
    paar = []
    if STAND.get("offen"):
        paar.append(_karte("📋 Offen", Text("\n".join(STAND["offen"]))))
    w = STAND.get("wache")
    if w:
        paar.append(_karte("🛡 Wache", Text(w)))
    if len(paar) == 2 and breite >= KARTEN_PAAR_AB:
        reihe = Table.grid(expand=True, padding=(0, 1))
        reihe.add_column(ratio=1)
        reihe.add_column(ratio=1)
        reihe.add_row(*paar)
        karten.append(reihe)
    else:
        karten += paar
    lieb = STAND.get("liebling")
    if lieb:
        bild = _bild(lieb["pfad"], LIEBLING_SPALTEN, LIEBLING_ZEILEN, 1)
        name = Text(f"💖 {lieb['name']}", style="assistant")
        if bild is not None:
            reihe = Table.grid(padding=(0, 2))
            reihe.add_row(bild, name)
            karten.append(_karte("🖼 Lieblingsbild", reihe))
        else:
            karten.append(_karte("🖼 Lieblingsbild", name))
    return karten


def leiste(breite: int):
    """Feste Leiste über dem Chat: kleines Bild, Name und Status, Gruß, Lage (Wache, Offenes), Version."""
    farbe = _farbe()
    bild = _bild(STAND["bild"], LEISTE_SPALTEN, LEISTE_ZEILEN, 2) if STAND.get("bild") else None
    zeichen, f, wort = STATUS.get(STAND.get("status"), STATUS["laedt"])
    oben = Text(no_wrap=True, overflow="ellipsis")
    oben.append(STAND["name"] or "Nemi", style=f"bold {farbe}")
    oben.append(f"  {zeichen} ", style=f)
    oben.append(wort, style="muted")
    gruss = Text(f"„{STAND['gruss']}“" if STAND.get("gruss") else "", style=f"italic {farbe}",
                 no_wrap=True, overflow="ellipsis")
    lage = []
    if STAND.get("wache"):
        lage.append(f"🛡 {STAND['wache'].split('  ·  ')[0]}")
    if STAND.get("offen"):
        lage.append(f"📋 {len(STAND['offen'])} offen")
    unten = Text("  ·  ".join(lage), style="muted", no_wrap=True, overflow="ellipsis")
    try:
        from version import VERSION
        version = Text(f"NemiCLI {VERSION.split()[0]}", style="brand.dim", no_wrap=True)
    except Exception:
        version = Text("")
    raster = Table.grid(expand=True, padding=(0, 2))
    if bild is not None:
        raster.add_column(width=LEISTE_SPALTEN)
    raster.add_column(ratio=1)
    raster.add_column(justify="right")
    zelle = Group(oben, gruss, unten)
    raster.add_row(*([bild] if bild is not None else []), zelle, version)
    return Panel(raster, box=ROUNDED, border_style=farbe, padding=(0, 1))


def renderable(kopf, breite: int):
    """Kopfzeile + Persönlichkeit links + Karten rechts (schmal: untereinander)."""
    person = _person()
    if breite >= NEBENEINANDER_AB:
        rechts = max(20, breite - LINKS_BREITE - 4)
        karten = _karten(rechts)
        raster = Table.grid(padding=(0, 2), expand=True)
        raster.add_column(width=LINKS_BREITE)
        raster.add_column(ratio=1)
        raster.add_row(person, Group(*karten) if karten else Text(""))
        koerper = raster
    else:
        koerper = Group(person, *_karten(breite))
    return Group(kopf, Text(""), koerper)
