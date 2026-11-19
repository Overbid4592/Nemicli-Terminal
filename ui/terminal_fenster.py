"""terminal_fenster.py – NemiCLIs eigenes Terminal-Fenster.

NemiCLI läuft in einer Pseudokonsole (terminal_pty.py); dieses Fenster setzt die
Steuerfolgen mit `pyte` in ein Zeichenraster um, zeichnet es mit PySide6 und
schickt Tasten, Maus, Einfügen, Zeichen aus der Eingabemethode (Emoji-Feld
Win + .) und abgelegte Dateien als Terminal-Folgen zurück. Kein Windows
Terminal, keine fremde Terminal-Software, keine Fremd-DLL.

Einstellungen (nemicli.config.json): `terminal_thema` (auto | breeze | mint; auto = Farben von /theme),
`terminal_schrift`, `terminal_groesse`, `terminal_hintergrund` (0.3–1.0, Durchsicht
nur des Hintergrunds, verschwommen), `terminal_unschaerfe` (true/false), `terminal_deckkraft`
(0.5–1.0, ganzes Fenster), `terminal_ki_reiter` (true/false), `terminal_leuchten` (true/false, Leuchten im
Hintergrund), `terminal_weich` (true/false, weiches Rollen und gleitender Cursor), `terminal_geteilt`
(true/false, Reiter „KI“ rechts neben dem Chat).

Reiter wie bei GNOME-Terminal: die Leiste erscheint ab dem zweiten. Strg+Umschalt+T öffnet eine
Kommandozeile, Strg+Umschalt+W schließt, Strg+Bild↑/↓ wechselt. „🤖 KI“ (Strg+Umschalt+K) zeigt die
Befehle der KI live – NemiCLI meldet sie über eine Named Pipe (core/ki_terminal.py). Strg+Umschalt+G
stellt ihn rechts neben den Chat. Strg+F sucht (Reiter „KI“ und Kommandozeile; im Chat sucht NemiCLI selbst),
Strg+Klick öffnet Web-Adressen und Pfade.

Gezeichnet wird je Zeile in ein Bild, das mit dem Zeileninhalt gemerkt bleibt: Neu gezeichnet werden nur
Zeilen, deren Inhalt es noch nicht gab – auch beim Rollen bleiben die Bilder gültig. Neue Ausgabe zeichnet nur
die geänderten Zeilen neu; der Takt läuft nur, solange etwas ansteht (Ausgabe, Gleiten, Figur, Atmen).
`terminal_fps` (30 | 60) ist die Bildrate der Oberfläche im Haupt-Reiter (TEXTUAL_FPS).
"""

from __future__ import annotations

import base64
import collections
import json
import math
import os
import queue
import re
import secrets
import functools
import sys
import threading
import time
from pathlib import Path

import pyte
import pyte.screens

import figurmass
import terminal_text


@functools.lru_cache(maxsize=4096)
def zellbreite(zeichen: str) -> int:
    """Zellen eines Zeichens, gezählt wie rich/Textual, die in diesem Fenster laufen. pyte zählt mit wcwidth:
    Hautton-Modifier (👍🏻) als 2 statt 0 und Flaggen-Buchstaben als 2 statt 1 – dahinter verrutschte die
    Oberfläche, und ihre Updates trafen die falschen Spalten (alte Zellen blieben stehen)."""
    try:
        from rich.cells import get_character_cell_size
        return get_character_cell_size(zeichen)
    except Exception:
        return max(0, pyte.screens._wcwidth(zeichen))


pyte.screens.wcwidth = zellbreite


class _NullbreiteAnhaengen:
    """Schreibt Text in Graphem-Gruppen – so, wie rich/Textual ihn zählt. pyte schreibt Zeichen für Zeichen: an
    Zeichen ohne eigene Zelle, die kein Akzent sind (Emoji-Variante U+FE0F, Verbinder U+200D, Hautton), bricht es
    ab und verwirft den REST des Textstücks („Herz ❤️ und weiter“ wurde zu „Herz ❤“); Gruppen wie „❤️“ (2 Zellen)
    oder „👩‍💻“ (2 statt 4) zählt es anders als die Oberfläche. Jede Gruppe kommt hier mit genau den Zellen der
    Oberfläche in eine Zelle (breite: mit Stummel dahinter).

    Gruppen, die pyte anders zählen würde, schreibt ein Platzhalter gleicher Breite (Umbruch, Einfügemodus und
    Stummel erledigt pyte), danach ersetzt die Gruppe ihn in seiner Zelle."""

    _PLATZHALTER = {1: "\uf8ff", 2: "\u3000"}

    def draw(self, data: str) -> None:
        try:
            from rich.cells import split_graphemes
            spannen, _ = split_graphemes(data)
        except Exception:
            super().draw(data)
            return
        einfach: list[str] = []
        for a, e, w in spannen:
            g = data[a:e]
            if len(g) == 1 and zellbreite(g) == w:
                einfach.append(g)
                continue
            if einfach:
                super().draw("".join(einfach))
                einfach = []
            if w == 0:
                self._anhaengen(g)
            elif w in self._PLATZHALTER:
                self._gruppe(g, w)
            else:
                super().draw(g)
        if einfach:
            super().draw("".join(einfach))

    def _gruppe(self, g: str, w: int) -> None:
        platz = self._PLATZHALTER[w]
        super().draw(platz)
        zeile = self.buffer[self.cursor.y]
        for x in range(min(self.cursor.x, self.columns) - 1, -1, -1):
            if zeile[x].data == platz:
                zeile[x] = zeile[x]._replace(data=g)
                break

    def _anhaengen(self, z: str) -> None:
        zeile = self.buffer[self.cursor.y]
        x = self.cursor.x - 1
        if x >= 1 and zeile[x].data == "":                 # zweite Zelle eines breiten Zeichens
            x -= 1
        if x >= 0:
            zeile[x] = zeile[x]._replace(data=zeile[x].data + z)
            self.dirty.add(self.cursor.y)


# ---------------------------------------------------------------------------
# Farbschemata
# ---------------------------------------------------------------------------

_NAMEN = ("black", "red", "green", "brown", "blue", "magenta", "cyan", "white")

THEMEN = {
    # KDE Breeze – Standard von Konsole (u. a. CachyOS KDE)
    "breeze": {"hintergrund": "#232627", "vordergrund": "#fcfcfc", "cursor": "#fcfcfc",
               "normal": ["#232627", "#ed1515", "#11d116", "#f67400", "#1d99f3", "#9b59b6", "#1abc9c", "#fcfcfc"],
               "hell": ["#7f8c8d", "#c0392b", "#1cdc9a", "#fdbc4b", "#3daee9", "#8e44ad", "#16a085", "#ffffff"]},
    # Tango – GNOME-Terminal (u. a. Linux Mint)
    "mint": {"hintergrund": "#2b2b2b", "vordergrund": "#e6e6e6", "cursor": "#e6e6e6",
             "normal": ["#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf"],
             "hell": ["#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"]},
}


def thema_aus_palette(palette: dict) -> dict:
    """Farben des Fensters aus dem Farbschema von /theme (design.PALETTEN). Die 16 Terminal-Farben
    bleiben die von Breeze – Programme in der Kommandozeile rechnen mit ihnen."""
    breeze = THEMEN["breeze"]
    return {"hintergrund": palette["grund"], "vordergrund": palette["text"], "cursor": palette["cyan"],
            "normal": [palette["grund"], *breeze["normal"][1:]], "hell": list(breeze["hell"]),
            "akzent": palette["cyan"], "akzent2": palette["lila"]}


def akzente(thema: dict) -> tuple[str, str]:
    """Die beiden Akzentfarben eines Themas (Reiter, Leuchten, Treffer)."""
    return thema.get("akzent") or thema["hell"][4], thema.get("akzent2") or thema["hell"][5]


def steuer_wert(befehl: str) -> tuple[str, str] | None:
    """Steuerbefehle mit Wert: „grund=rrggbb“ (Grundfarbe des Programms), „puls=0|1“ (es arbeitet) und die
    Schalter von /terminal: „leuchten=0|1“, „weich=0|1“, „geteilt=0|1“, „figur=0|1“."""
    m = re.fullmatch(r"grund=([0-9a-fA-F]{6})", befehl or "")
    if m:
        return "grund", "#" + m.group(1).lower()
    m = re.fullmatch(r"(puls|leuchten|weich|geteilt|figur)=([01])", befehl or "")
    return (m.group(1), m.group(2)) if m else None


# ---------------------------------------------------------------------------
# Figur der Persönlichkeit (ui/figur.py): sitzt rechts auf der Kopfzeile der Eingabe. Das Programm meldet
# Bilder („posen=“), Pose („pose=“) und Sitzzeile („sitz=“); für die sitzenden Bilder hält das Fenster rechts
# Spalten frei, die stehende Figur (strich) steht oben auf dem Rahmen über dem Inhalt. Bewegt per Code.
# ---------------------------------------------------------------------------

FIGUR_POSEN = ("sitzen", "winken", "nachdenken", "pruefen", "arbeiten", "freude", "herz", "grinsen",
               "neugierig", "schlafen")
FIGUR_KURZ = {"winken": 3.2, "freude": 2.4, "herz": 2.8, "grinsen": 2.4, "neugierig": 3.4}   # Sekunden
FIGUR_ZEILEN = {"figur": 13, "buste": 8, "strich": 14}   # Höhe der Figur in Zeilen
FIGUR_HOECHSTENS = 0.5                    # … aber höchstens dieser Anteil der Fensterhöhe
FIGUR_STEHT_BREITE = 0.55                 # Platz rechts für sie: Anteil ihrer Höhe
FIGUR_TEXT_MIN = 80                       # so viele Spalten bleiben dem Text mindestens, sonst keine Figur
FIGUR_SITZ_ANTEIL = 0.58                  # Sitzfläche bei 58 % der Bildhöhe (ganze, sitzende Figur)
FIGUR_SITZ_VORGABE = 5                    # Zeilen vom unteren Rand, solange das Programm nichts meldet
FIGUR_SCHLAF_S = 600                      # so lange ohne Taste: sie döst
FIGUR_WECHSEL_S = 0.25                    # weicher Übergang zwischen zwei Posen
FIGUR_ZEICHEN = {"nachdenken": "…", "neugierig": "?", "herz": "♥", "freude": "✦", "schlafen": "z"}


STRICH_GROESSE_M = 1.75                   # so groß ist ein Mensch im Modell: Figurhöhe in Bildpunkten / 1,75 m
STRICH_WINKEL = math.radians(25)          # leicht schräg gesehen: Sitzen und Verbeugen haben Tiefe
STRICH_WECHSEL_S = 0.45                   # weicher Übergang zwischen zwei Bewegungen (sanft an- und auslaufend)
STRICH_MAX_BYTES = 8 << 20
STRICH_MAX_BILDER = 12001
STRICH_GELENKE = 22


STRICH_MERK_MAX = 24                      # so viele geprüfte Bewegungen bleiben gemerkt (nach Pfad und Stand)
_STRICH_MERK: collections.OrderedDict = collections.OrderedDict()
_STRICH_MERK_LOCK = threading.Lock()


def strich_lesen(pfad: Path, gesicht: str = "neutral", vorrang: bool = False) -> dict | None:
    """Eine Bewegung aus dem Cache (engines/bewegung.py) – geprüft: Größe, Form, Zahlen. None, wenn sie nicht
    taugt. Gelesen und geprüft wird je Datei und Stand (Änderungszeit, Größe) nur einmal."""
    try:
        pfad = Path(pfad)
        if pfad.suffix.lower() != ".json" or not pfad.is_file():
            return None
        st = pfad.stat()
        if st.st_size > STRICH_MAX_BYTES:
            return None
        schluessel = (str(pfad), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    with _STRICH_MERK_LOCK:
        roh = _STRICH_MERK.get(schluessel)
        if roh is not None:
            _STRICH_MERK.move_to_end(schluessel)
    if roh is None:
        roh = _strich_pruefen(pfad)
        if roh is None:
            return None
        with _STRICH_MERK_LOCK:
            _STRICH_MERK[schluessel] = roh
            while len(_STRICH_MERK) > STRICH_MERK_MAX:
                _STRICH_MERK.popitem(last=False)
    return {**roh, "gesicht": re.sub(r"[^a-z]", "", str(gesicht or "neutral"))[:30] or "neutral",
            "vorrang": bool(vorrang)}


def _strich_pruefen(pfad: Path) -> dict | None:
    try:
        d = json.loads(pfad.read_text(encoding="utf-8"))
        bilder, eltern, namen = d["bilder"], [int(e) for e in d["eltern"]], [str(n) for n in d["namen"]]
        fps = int(d.get("fps") or 20)
        if not (1 <= len(bilder) <= STRICH_MAX_BILDER and len(eltern) == len(namen) == STRICH_GELENKE
                and 1 <= fps <= 60 and all(-1 <= e < i for i, e in enumerate(eltern))):
            return None
        sauber = []
        for b in bilder:
            if len(b) != STRICH_GELENKE:
                return None
            punkte = [(float(q[0]), float(q[1]), float(q[2])) for q in b]
            if not all(math.isfinite(v) and abs(v) < 50 for q in punkte for v in q):
                return None
            sauber.append(punkte)
    except Exception:
        return None
    return {"bilder": sauber, "eltern": eltern, "namen": namen, "fps": fps, "schleife": bool(d.get("schleife"))}


def strich_bild(bew: dict, sekunden: float) -> tuple[int, bool]:
    """(Bildnummer, fertig) nach `sekunden` in der Bewegung – eine Schleife läuft immer weiter."""
    n = len(bew["bilder"])
    f = max(0, int(sekunden * bew["fps"]))
    if bew["schleife"]:
        return f % n, False
    return min(f, n - 1), f >= n


def strich_mischen(von: list, nach: list, anteil: float) -> list:
    anteil = min(1.0, max(0.0, anteil))
    return [tuple(a + (b - a) * anteil for a, b in zip(p, q)) for p, q in zip(von, nach)]


def strich_sanft(anteil: float) -> float:
    """0 … 1 sanft an- und auslaufend (statt mit einem Ruck zu beginnen und zu enden)."""
    anteil = min(1.0, max(0.0, anteil))
    return anteil * anteil * (3 - 2 * anteil)


def strich_zwischen(bew: dict, sekunden: float) -> tuple[list, bool]:
    """(Gelenke, fertig) nach `sekunden` – zwischen zwei Bildern der Bewegung überblendet, damit sie bei jedem
    Takt flüssig läuft. Eine Schleife geht vom letzten Bild weich ins erste."""
    bilder, fps = bew["bilder"], bew["fps"]
    n = len(bilder)
    f = max(0.0, sekunden * fps)
    if bew["schleife"]:
        f %= n
        i = int(f)
        return strich_mischen(bilder[i], bilder[(i + 1) % n], f - i), False
    if f >= n - 1:
        return bilder[-1], f >= n
    i = int(f)
    return strich_mischen(bilder[i], bilder[i + 1], f - i), False


def strich_projektion(gelenke: list, winkel: float = STRICH_WINKEL) -> list[tuple[float, float, float]]:
    """(waagerecht, Höhe, Tiefe) je Gelenk in Metern: um die Hochachse gedreht, von vorn gesehen; Tiefe > 0 = näher."""
    c, s = math.cos(winkel), math.sin(winkel)
    return [(x * c + z * s, y, -x * s + z * c) for x, y, z in gelenke]


def strich_von_vorn(gelenke: list, namen: list, winkel: float = STRICH_WINKEL) -> bool:
    """Schaut die Figur zum Betrachter? (Blick aus der Schulterlinie: links × oben; zu Beginn nach +Z)."""
    try:
        links, rechts = gelenke[namen.index("l_shoulder")], gelenke[namen.index("r_shoulder")]
    except ValueError:
        return True
    lx, lz = links[0] - rechts[0], links[2] - rechts[2]
    vx, vz = -lz, lx                                    # (links × oben) in der Ebene
    return -vx * math.sin(winkel) + vz * math.cos(winkel) >= 0


RIG_VERSION = 8                           # tools/figurrig.py: Hülle aus Röhren (weiche Gelenke, Hände, Gesichter) und Kappen
RIG_MAX_NETZ = 400                        # Punkte im Netz einer Röhre höchstens
# Hände: die Bewegung kennt sie nicht – sie folgen dem Unterarm, hängen locker etwas zur Schwerkraft hin und werden
# schmal, wenn der Arm hängt (die Handfläche zeigt dann zum Bein, man sieht sie von der Seite)
RIG_HAND = {("l_wrist", "l_hand"): ("l_elbow", "l_wrist"), ("r_wrist", "r_hand"): ("r_elbow", "r_wrist")}
RIG_HAND_LOCKER = 0.35                    # so viel des Winkels zur Senkrechten gibt die Hand nach (höchstens 30°)
RIG_HAND_SCHMAL = 0.45                    # so viel schmaler ist die Hand bei hängendem Arm
RIG_NAHT = 0.6                            # jedes Dreieck wird um so viele Bildpunkte größer gemalt: keine Fugen
RIG_MAX_BYTES = 1 << 20
RIG_MAX_KNOCHEN = 64
RIG_MAX_STUECKE = 96
# Rücken, Hals und Kopf: werden kaum gestreckt, der Kopf nie (fest wie Knorpel); beim Drehen werden sie quer schmal,
# höchstens bis auf ihre Tiefe von der Seite gesehen (Rumpf flach, Kopf fast rund)
RIG_SENKRECHT = {("pelvis", "spine1"): 0.5, ("spine1", "spine2"): 0.5, ("spine2", "spine3"): 0.5,
                 ("spine3", "neck"): 0.8, ("neck", "head"): 0.85}
RIG_FEST = {("neck", "head")}
RIG_RUMPF_TIEFE = 0.5
# Der Fuß zeigt in der Bewegung nach vorn – von vorn gesehen kippt seine Richtung beliebig; er folgt dem Unterschenkel
RIG_WIE = {("l_ankle", "l_foot"): ("l_knee", "l_ankle"), ("r_ankle", "r_foot"): ("r_knee", "r_ankle")}
# Quer liegende Knochen (Hüfte, Schlüsselbein, Schulter): von der Seite gesehen hat ihr Bild keine Richtung mehr –
# sie drehen sich mit dem Rumpf und werden so schmal wie er
RIG_QUER = {("pelvis", "l_hip"): ("pelvis", "spine1"), ("pelvis", "r_hip"): ("pelvis", "spine1"),
            ("spine3", "l_collar"): ("spine3", "neck"), ("spine3", "r_collar"): ("spine3", "neck"),
            ("l_collar", "l_shoulder"): ("spine3", "neck"), ("r_collar", "r_shoulder"): ("spine3", "neck")}


def rig_lesen(pfad: Path) -> dict | None:
    """Eine Ansicht der beweglichen Figur (tools/figurrig.py, Version 4): Gelenke, Knochen und die Stücke der Hülle
    (Röhren und Kappen, je ein Bild stueck_<n>.png daneben, mit Knochen und Lage im Bild) – geprüft auf Form, Zahlen
    und Grenzen. None, wenn etwas nicht stimmt."""
    try:
        pfad = Path(pfad)
        if pfad.name != "rig.json" or not pfad.is_file() or pfad.stat().st_size > RIG_MAX_BYTES:
            return None
        d = json.loads(pfad.read_text(encoding="utf-8"))
        if int(d.get("version") or 0) != RIG_VERSION:
            return None
        hoehe = float(d["hoehe"])
        gelenke = {str(n)[:30]: (float(v[0]), float(v[1])) for n, v in dict(d["gelenke"]).items()}
        knochen = [(str(e), str(k)) for e, k in d["knochen"]]
        stuecke = []
        for s in d["stuecke"]:
            art, datei = str(s["art"]), str(s["bild"])
            bild = pfad.parent / datei
            if (art not in ("roehre", "kappe") or not re.fullmatch(r"stueck_\d{1,3}\.png", datei) or not bild.is_file()
                    or bild.stat().st_size > BILD_MAX):
                return None
            netz = None
            if s.get("netz") is not None:
                n = s["netz"]
                punkte = [(float(x), float(y)) for x, y in n["punkte"]]
                netz = {"punkte": punkte,
                        "dreiecke": [(int(a), int(b), int(c)) for a, b, c in n["dreiecke"]],
                        "gewichte": [[(int(k), float(w)) for k, w in g] for g in n["gewichte"]]}
                if (not 3 <= len(punkte) <= RIG_MAX_NETZ or len(netz["gewichte"]) != len(punkte)
                        or not all(0 <= i < len(punkte) for d3 in netz["dreiecke"] for i in d3)
                        or not all(1 <= len(g) <= 3 for g in netz["gewichte"])
                        or not all(math.isfinite(v) for p in punkte for v in p)
                        or not all(math.isfinite(w) for g in netz["gewichte"] for _k, w in g)):
                    return None
            gesichter = {}
            for name, datei_g in dict(s.get("gesichter") or {}).items():
                name, datei_g = str(name), str(datei_g)
                pfad_g = pfad.parent / datei_g
                if (not re.fullmatch(r"[a-z]{1,30}", name) or not re.fullmatch(r"stueck_\d{1,3}_[a-z]{1,30}\.png", datei_g)
                        or not pfad_g.is_file() or pfad_g.stat().st_size > BILD_MAX):
                    return None
                gesichter[name] = pfad_g
            stuecke.append({"art": art, "knochen": int(s["knochen"]), "bild": bild, "netz": netz,
                            "gesichter": gesichter,
                            "ursprung": (float(s["ursprung"][0]), float(s["ursprung"][1]))})
    except Exception:
        return None
    zahlen = [v for p in list(gelenke.values()) + [s["ursprung"] for s in stuecke] for v in p]
    if (not (10 < hoehe < 20000) or not all(math.isfinite(v) and abs(v) < 1e6 for v in zahlen)
            or not 1 <= len(knochen) <= RIG_MAX_KNOCHEN or not all(e in gelenke and k in gelenke for e, k in knochen)
            or not 1 <= len(stuecke) <= RIG_MAX_STUECKE
            or not all(0 <= s["knochen"] < len(knochen) for s in stuecke)
            or not all(0 <= k < len(knochen) for s in stuecke if s["netz"] for g in s["netz"]["gewichte"]
                       for k, _w in g)):
        return None
    return {"ansicht": str(d.get("ansicht") or ""), "hoehe": hoehe, "gelenke": gelenke, "knochen": knochen,
            "stuecke": stuecke}


def rig_hand(unterarm_z, bild_von, bild_nach, mass: float) -> tuple:
    """Die Abbildung einer Hand: am Handgelenk angesetzt, in Richtung des Unterarms; zeigt der Arm nach unten, gibt sie
    ein Stück zur Senkrechten nach (locker) und wird quer schmaler, je mehr der Arm hängt."""
    (ex, ey), (wx, wy) = unterarm_z
    richtung = math.atan2(wy - ey, wx - ex)
    haengt = max(0.0, math.sin(richtung))                             # 1 = Unterarm zeigt senkrecht nach unten
    # nur ein Arm, der nach unten zeigt, lässt die Hand hängen – eine winkende Hand bleibt oben
    zur_senkrechten = math.atan2(math.sin(math.pi / 2 - richtung), math.cos(math.pi / 2 - richtung))
    nachgeben = max(-math.radians(30), min(math.radians(30), zur_senkrechten * RIG_HAND_LOCKER)) * haengt
    richtung += nachgeben
    nach = (wx + math.cos(richtung), wy + math.sin(richtung))
    return rig_abbildung(bild_von, bild_nach, (wx, wy), nach, mass, mass * (1 - RIG_HAND_SCHMAL * haengt))


def rig_knochen(rig: dict, ziel: dict, mass: float, blick: float, werte: dict | None = None) -> list | None:
    """Je Knochen die Abbildung Bild -> Fenster (Drehung, Streckung längs, Maß quer) auf seinen Knochen der Bewegung.
    `ziel`: Gelenkname -> (x, y, Tiefe) im Fenster. `werte`: Maße aus dem Figur-Justierer (ui/figurmass.py) – die
    Gelenke in `ziel` sind damit schon gestreckt; hier wächst der Kopf mit und der Fuß um seinen Knöchel.
    None, wenn der Bewegung ein Gelenk fehlt."""
    werte = werte or {}
    if not all(e in ziel and (k in ziel or (e, k) in RIG_HAND) for e, k in rig["knochen"]):
        return None

    def winkel(a, b):
        return math.atan2(b[1] - a[1], b[0] - a[0])

    abb = {}
    for e, k in rig["knochen"]:
        if (e, k) in RIG_HAND:
            ue, uk = RIG_HAND[(e, k)]
            abb[(e, k)] = rig_hand((ziel[ue][:2], ziel[uk][:2]), rig["gelenke"][e], rig["gelenke"][k], mass)
            continue
        von_b, nach_b = rig["gelenke"][e], rig["gelenke"][k]
        von_z, nach_z = ziel[e], ziel[k]
        rumpf = RIG_QUER.get((e, k))
        if rumpf is not None and rumpf[0] in rig["gelenke"] and rumpf[1] in rig["gelenke"]:
            drehung = winkel(ziel[rumpf[0]], ziel[rumpf[1]]) - winkel(rig["gelenke"][rumpf[0]], rig["gelenke"][rumpf[1]])
            richtung = winkel(von_b, nach_b) + drehung
            nach = (von_z[0] + math.cos(richtung), von_z[1] + math.sin(richtung))
            abb[(e, k)] = rig_abbildung(von_b, nach_b, von_z[:2], nach, mass * max(RIG_RUMPF_TIEFE, abs(blick)), mass)
            continue
        bild_l = math.hypot(nach_b[0] - von_b[0], nach_b[1] - von_b[1]) or 1.0
        ziel_l = math.hypot(nach_z[0] - von_z[0], nach_z[1] - von_z[1])
        faktor = float(werte.get(figurmass.KETTE.get(k, ""), 1.0))
        if (e, k) in RIG_FEST:
            laengs = mass * faktor
        elif (e, k) in RIG_SENKRECHT:
            laengs = min(1.15 * mass * faktor, max(0.85 * mass * faktor, ziel_l / bild_l))
        else:
            laengs = min(1.3 * mass * max(1.0, faktor), max(0.2 * mass, ziel_l / bild_l))
        quer = mass * max(RIG_SENKRECHT[(e, k)], abs(blick)) if (e, k) in RIG_SENKRECHT else mass
        if (e, k) in RIG_FEST:
            quer *= faktor
        abb[(e, k)] = rig_abbildung(von_b, nach_b, von_z[:2], nach_z[:2], laengs, quer)
    raus = []
    for e, k in rig["knochen"]:
        wie = RIG_WIE.get((e, k))
        if wie not in abb:
            raus.append(abb[(e, k)])
            continue
        a, b, c, d, ee, f = abb[wie]                     # wie der Unterschenkel, um den Knöchel gestreckt
        s = float(werte.get(figurmass.KETTE.get(k, ""), 1.0))
        kx, ky = ziel[e][0], ziel[e][1]
        raus.append((a * s, b * s, c * s, d * s, kx + (ee - kx) * s, ky + (f - ky) * s))
    return raus


def rig_tiefen(rig: dict, ziel: dict) -> list[float]:
    """Tiefe je Knochen (Mitte seiner Gelenke); eine Hand – die Bewegung kennt sie nicht – liegt auf ihrem Handgelenk."""
    return [(ziel[e][2] + ziel.get(k, ziel[e])[2]) / 2 for e, k in rig["knochen"]]


def rig_netz(netz: dict, abbildungen: list) -> list:
    """Die Punkte eines Röhren-Netzes im Fenster: jeder Punkt mit seinen Knochen bewegt (gewichtet)."""
    raus = []
    for (x, y), gewicht in zip(netz["punkte"], netz["gewichte"]):
        sx = sy = summe = 0.0
        for k, w in gewicht:
            a, b, c, d, e, f = abbildungen[k]
            sx += w * (a * x + c * y + e)
            sy += w * (b * x + d * y + f)
            summe += w
        summe = summe or 1.0
        raus.append((sx / summe, sy / summe))
    return raus


def dreieck_abbildung(q, z):
    """Affine Abbildung (a, b, c, d, e, f) – x' = a·x + c·y + e, y' = b·x + d·y + f –, die das Dreieck q genau auf z
    legt. None, wenn q keine Fläche hat."""
    (x0, y0), (x1, y1), (x2, y2) = q
    nenner = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(nenner) < 1e-9:
        return None

    def zeile(u0, u1, u2):
        a = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / nenner
        c = ((u2 - u0) * (x1 - x0) - (u1 - u0) * (x2 - x0)) / nenner
        return a, c, u0 - a * x0 - c * y0

    a, c, e = zeile(z[0][0], z[1][0], z[2][0])
    b, d, f = zeile(z[0][1], z[1][1], z[2][1])
    return a, b, c, d, e, f


def dreieck_weiter(z, um: float = RIG_NAHT):
    """Das Dreieck z ein Stück größer (jede Ecke `um` Bildpunkte weiter weg vom Schwerpunkt)."""
    mx, my = sum(p[0] for p in z) / 3, sum(p[1] for p in z) / 3
    raus = []
    for x, y in z:
        dx, dy = x - mx, y - my
        laenge = math.hypot(dx, dy) or 1.0
        raus.append((x + dx / laenge * um, y + dy / laenge * um))
    return raus


def rig_reihe(rig: dict, tiefen: list) -> list[int]:
    """In welcher Reihenfolge die Stücke gemalt werden: erst alle Kappen (sie füllen nur Lücken und liegen unter
    jeder Röhre), dann die Röhren, hintere zuerst (Tiefe ihres Knochens)."""
    def tiefe(i):
        s = rig["stuecke"][i]
        return (s["art"] != "kappe", tiefen[s["knochen"]])
    return sorted(range(len(rig["stuecke"])), key=tiefe)


def rig_abbildung(von_bild, nach_bild, von_ziel, nach_ziel, laengs: float, quer: float):
    """Affine Abbildung (a, b, c, d, e, f) Bild -> Fenster: x' = a·x + c·y + e, y' = b·x + d·y + f. Der Knochen im
    Bild liegt danach auf dem Knochen der Bewegung (Anfang genau, Richtung genau), längs um `laengs`, quer um `quer`
    gestreckt."""
    wi = math.atan2(nach_bild[1] - von_bild[1], nach_bild[0] - von_bild[0])
    wz = math.atan2(nach_ziel[1] - von_ziel[1], nach_ziel[0] - von_ziel[0])
    ci, si, cz, sz = math.cos(wi), math.sin(wi), math.cos(wz), math.sin(wz)
    # R(wz) · diag(laengs, quer) · R(−wi)
    a = cz * laengs * ci + sz * quer * si
    c = cz * laengs * si - sz * quer * ci
    b = sz * laengs * ci - cz * quer * si
    d = sz * laengs * si + cz * quer * ci
    e = von_ziel[0] - (a * von_bild[0] + c * von_bild[1])
    f = von_ziel[1] - (b * von_bild[0] + d * von_bild[1])
    return a, b, c, d, e, f


def strich_blick(gelenke: list, namen: list, winkel: float = STRICH_WINKEL) -> float:
    """−1 … 1: wie sehr die Figur zum Betrachter schaut (1 = genau her, 0 = seitlich, −1 = weg)."""
    try:
        links, rechts = gelenke[namen.index("l_shoulder")], gelenke[namen.index("r_shoulder")]
    except ValueError:
        return 1.0
    lx, lz = links[0] - rechts[0], links[2] - rechts[2]
    laenge = math.hypot(lx, lz) or 1.0
    return (lz * math.sin(winkel) + lx * math.cos(winkel)) / laenge


def figur_befehl(befehl: str) -> tuple[str, object] | None:
    """„posen=<base64 JSON>“ -> ("posen", (Art, {Pose: Pfad})), „pose=<name>“ -> ("pose", name),
    „sitz=<n>“ -> ("sitz", n), „bewegung=<base64 JSON>“ -> ("bewegung", Bewegung). Unlesbares oder Unbekanntes
    -> None; leeres „posen=“ = keine Figur. Beim Strichmännchen sind die Bilder Gesichter (beliebige Namen)."""
    m = re.fullmatch(r"justierung=([A-Za-z0-9_=-]+)", befehl or "")
    if m:                                        # {"datei": …/justierung.json, "werte": {…}} – Figur-Justierer
        try:
            d = json.loads(base64.urlsafe_b64decode(m.group(1)).decode("utf-8"))
            datei = Path(str(d.get("datei") or "")) if d.get("datei") else None
        except Exception:
            return None
        if datei is not None and datei.name != figurmass.DATEI:
            datei = None
        return ("justierung", {"datei": datei, "werte": figurmass.sauber(d.get("werte"))})
    m = re.fullmatch(r"rig=([A-Za-z0-9_=-]*)", befehl or "")
    if m:
        if not m.group(1):
            return ("rig", {})
        try:
            pfade = json.loads(base64.urlsafe_b64decode(m.group(1)).decode("utf-8"))
            rigs = {a: r for a in ("vorne", "hinten") if a in pfade and (r := rig_lesen(Path(str(pfade[a]))))}
        except Exception:
            return None
        return ("rig", rigs if "vorne" in rigs else {})
    m = re.fullmatch(r"bewegung=([A-Za-z0-9_=-]+)", befehl or "")
    if m:
        try:
            kopf = json.loads(base64.urlsafe_b64decode(m.group(1)).decode("utf-8"))
            bew = strich_lesen(Path(str(kopf["datei"])), kopf.get("gesicht", "neutral"), bool(kopf.get("vorrang")))
        except Exception:
            return None
        return ("bewegung", bew) if bew is not None else None
    m = re.fullmatch(r"pose=([a-z]+)", befehl or "")
    if m:
        return ("pose", m.group(1)) if m.group(1) in FIGUR_POSEN else None
    m = re.fullmatch(r"sitz=(\d{1,2})", befehl or "")
    if m:
        return ("sitz", max(1, int(m.group(1))))
    m = re.fullmatch(r"posen=([A-Za-z0-9_=-]*)", befehl or "")
    if not m:
        return None
    if not m.group(1):
        return ("posen", ("", {}))
    try:
        daten = json.loads(base64.urlsafe_b64decode(m.group(1)).decode("utf-8"))
        art = daten.get("art")
        bilder = {}
        for pose, pfad in dict(daten.get("bilder") or {}).items():
            pfad = Path(str(pfad))
            lesbar = pfad.suffix.lower() in BILD_ENDUNGEN and pfad.is_file() and pfad.stat().st_size <= BILD_MAX
            erlaubt = re.fullmatch(r"[a-z]{1,30}", str(pose)) if art == "strich" else pose in FIGUR_POSEN
            if erlaubt and lesbar:
                bilder[pose] = pfad
    except Exception:
        return None
    if art == "strich":
        return ("posen", ("strich", bilder))
    if art not in FIGUR_ZEILEN or "sitzen" not in bilder:
        return ("posen", ("", {}))
    return ("posen", (art, bilder))


def figur_pose(basis: str, kurz: str | None, kurz_seit: float, jetzt: float, taste_seit: float) -> str:
    """Was die Figur gerade zeigt: eine kurze Pose (winken, freude …), solange sie gilt; sonst die Grundpose,
    und nach langer Ruhe döst sie."""
    if kurz in FIGUR_KURZ and 0 <= jetzt - kurz_seit < FIGUR_KURZ[kurz]:
        return kurz
    if basis == "sitzen" and jetzt - taste_seit > FIGUR_SCHLAF_S:
        return "schlafen"
    return basis


def figur_bewegung(pose: str, t: float) -> tuple[float, float, float, float]:
    """(dy, Winkel, sx, sy) nach t Sekunden in der Pose: dy als Anteil der Figurhöhe (negativ = nach oben),
    Winkel in Grad um die Sitzfläche, Streckung waagerecht/senkrecht. Immer ein leises Atmen."""
    atem = math.sin(2 * math.pi * t / 4.2)
    dy, winkel, sx, sy = 0.0, 0.0, 1 - 0.004 * atem, 1 + 0.012 * atem
    if pose == "winken":
        winkel = 2.5 * math.sin(2 * math.pi * t / 0.9)
    elif pose == "nachdenken":
        winkel = -1.5 + math.sin(2 * math.pi * t / 3.2)
    elif pose == "pruefen":
        winkel, dy = -2.0, 0.004 * math.sin(2 * math.pi * t / 1.6)
    elif pose == "arbeiten":
        dy = 0.006 * abs(math.sin(2 * math.pi * t / 0.5))
    elif pose == "freude":
        hoch = math.sin(math.pi * ((t % 0.8) / 0.8)) if t < 1.6 else 0.0      # zwei Hüpfer
        dy, sx, sy = -0.08 * hoch, sx * (1 - 0.02 * hoch), sy * (1 + 0.03 * hoch)
    elif pose == "herz":
        s = 1 + 0.025 * math.sin(2 * math.pi * t / 0.7)
        sx, sy = sx * s, sy * s
    elif pose == "grinsen":
        winkel = 2.0 * math.sin(2 * math.pi * t / 1.6)
    elif pose == "neugierig":
        winkel = 5.0 * min(1.0, t / 0.4)
    elif pose == "schlafen":
        winkel, sy = 2.0, 1 + 0.02 * math.sin(2 * math.pi * t / 6.0)
    return dy, winkel, sx, sy


def figur_hoehe(art: str, zh: float, fenster_hoehe: float, sitz: int, zeilen: int | None = None) -> float:
    """Höhe der Figur in Bildpunkten: FIGUR_ZEILEN, höchstens FIGUR_HOECHSTENS des Fensters – und eine ganze,
    sitzende Figur nur so groß, dass ihre Füße unter der Sitzzeile noch ins Fenster passen. Die stehende (strich)
    behält ihre Größe bei jeder Fenstergröße und wird nur kleiner, wenn sie über der Eingabe sonst nicht passt."""
    if art == "strich":            # `zeilen`: Größe aus dem Figur-Justierer
        return max(zh * 3, min((zeilen or FIGUR_ZEILEN[art]) * zh, (fenster_hoehe / max(zh, 1) - sitz - 1) * zh * 0.85))
    hoehe = min(FIGUR_ZEILEN[art] * zh, fenster_hoehe * FIGUR_HOECHSTENS)
    if art == "figur":
        unten = (sitz - 0.5) * zh + 8 - 4                 # bis zum unteren Rand, 4 px Luft
        hoehe = min(hoehe, unten / (1 - FIGUR_SITZ_ANTEIL))
    return max(zh * 3, hoehe)


def figur_reserve(spalten: int, zb: float, breite_px: float) -> int:
    """Spalten, die rechts für die Figur frei bleiben – 0, wenn dem Text sonst weniger als FIGUR_TEXT_MIN blieben."""
    frei = math.ceil(breite_px / max(zb, 1)) + 2
    return frei if spalten - frei >= FIGUR_TEXT_MIN else 0


PULS_S = 2.4                          # Dauer eines Atemzugs des Hintergrunds, solange das Programm arbeitet
GLANZ_S = 0.9                         # kurzes Aufleuchten, wenn das Programm fertig ist (mit „Leuchten“ abschaltbar)
ZEILEN_SPEICHER = 48 * 1024 * 1024    # so viele Bytes dürfen gemerkte Zeilenbilder belegen
ROLL_ZEILEN = 3                       # Zeilen je Raste des Mausrads (Reiter „KI“)
TREFFER_MARKEN = 400                  # so viele Marken am Rand höchstens


def glanz_staerke(sekunden: float) -> float:
    """Verlauf des Aufleuchtens: sanft an und wieder aus, außerhalb von 0 … GLANZ_S null."""
    if not 0 <= sekunden < GLANZ_S:
        return 0.0
    return math.sin(math.pi * sekunden / GLANZ_S)


# ---------------------------------------------------------------------------
# Tempo: was neu gezeichnet und eingespeist wird (reine Funktionen, testbar ohne Fenster)
# ---------------------------------------------------------------------------

FEED_STUECK = 8 * 1024                # Zeichen je pyte-Durchgang; dazwischen ist die Sperre für das Zeichnen frei
PULS_STUFEN = 16                      # Stärke des Atmens in Stufen: neu gezeichnet wird nur beim Wechsel
PULS_TAKT_S = 0.1                     # so oft wird die Stufe geprüft (≈ 10 Bilder je Sekunde)
FIGUR_RUHE_BILDER = 10                # sitzende Figur, nur Atmen: Bilder je Sekunde
FIGUR_BILDER = 15                     # sitzende Figur in Bewegung, Strichmännchen in Ruhe
STRICH_BILDER = 30                    # Strichmännchen in Bewegung
KI_EINSPEISEN_S = 0.008               # Reiter „KI“: so lange je Takt Text einspeisen, der Rest folgt im nächsten
KI_RASTER_WARTEN_MS = 150             # Reiter „KI“: erst neu aufbauen, wenn die Größe so lange steht
BILD_KANTE_MAX = 4096                 # größere Bilder werden beim Laden auf diese Kantenlänge verkleinert
TEXTUAL_FPS = (30, 60)                # erlaubte Bildraten der Oberfläche (terminal_fps), die erste ist die Vorgabe
_SGR_ANFANG = re.compile(r"(?:\x1b\[[0-9;]*m)*")


def _grenze(text: str, a: int, groesse: int) -> int:
    """Ende des Stücks ab `a`: nach etwa `groesse` Zeichen, zwischen zwei ASCII-Zeichen."""
    if len(text) - a <= groesse:
        return len(text)
    e = a + groesse
    bis = min(len(text) - 1, e + 256)
    while e < bis and not (text[e - 1] < "\x80" and text[e] < "\x80"):
        e += 1
    return e if e < bis else a + groesse


def feed_stuecke(text: str, groesse: int = FEED_STUECK) -> list[str]:
    """Text in Stücke für pyte. pyte.Stream ist zustandsbehaftet – eine Steuerfolge, die an einer Grenze endet,
    setzt das nächste Stück fort. Geteilt wird zwischen zwei ASCII-Zeichen, damit Graphem-Gruppen ganz bleiben."""
    teile, a = [], 0
    while a < len(text) or not teile:
        e = _grenze(text, a, groesse)
        teile.append(text[a:e])
        a = e
    return teile


def feed_kopf(text: str, groesse: int = FEED_STUECK) -> tuple[str, str]:
    """(erstes Stück, Rest) wie bei feed_stuecke."""
    e = _grenze(text, 0, groesse)
    return text[:e], text[e:]


def zeilen_streifen(zeilen, zh: float, oben: float = 8.0, rand: int = 1) -> list[tuple[int, int]]:
    """Bildschirmzeilen -> senkrechte Streifen (y, Höhe) in ganzen Bildpunkten; benachbarte Zeilen werden ein
    Streifen. `oben`: y der Zeile 0 (Rand und Rollstand)."""
    streifen: list[list[int]] = []
    for y in sorted(set(zeilen)):
        anfang = math.floor(oben + y * zh) - rand
        ende = math.ceil(oben + (y + 1) * zh) + rand
        if streifen and anfang <= streifen[-1][1]:
            streifen[-1][1] = max(streifen[-1][1], ende)
        else:
            streifen.append([anfang, ende])
    return [(a, e - a) for a, e in streifen]


def puls_stufe(puls_s: float | None, glanz: float = 0.0) -> int:
    """Stärke des Leuchtens als Stufe 0 … PULS_STUFEN: atmend, solange das Programm arbeitet (`puls_s` = Sekunden
    seit Beginn), sonst das Aufleuchten danach (`glanz` 0 … 1)."""
    anteil = 0.5 - 0.5 * math.cos(puls_s * 2 * math.pi / PULS_S) if puls_s is not None else glanz
    return round(min(1.0, max(0.0, anteil)) * PULS_STUFEN)


def leuchten_staerke(stufe: int) -> float:
    return 0.55 + 0.45 * stufe / PULS_STUFEN


def protokoll_ende(stuecke: list[str], zeilen: int) -> list[str]:
    """Das Ende des Protokolls, das für `zeilen` Zeilen reicht – gezählt an Zeilenenden (lange Zeilen brechen zu
    mehr Zeilen um, nie zu weniger). Ein angeschnittenes Stück behält die Farbfolgen seines Anfangs."""
    raus, gezaehlt = [], 0
    for text in reversed(stuecke):
        n = text.count("\n")
        if gezaehlt + n <= zeilen:
            raus.append(text)
            gezaehlt += n
            continue
        pos = len(text)
        for _ in range(zeilen - gezaehlt + 1):
            pos = text.rfind("\n", 0, pos)
            if pos < 0:
                break
        raus.append(text if pos < 0 else _SGR_ANFANG.match(text).group(0) + text[pos + 1:])
        break
    return raus[::-1]


def textual_fps(c: dict) -> int:
    """Bildrate der Oberfläche aus `terminal_fps` (30 oder 60, sonst die Vorgabe)."""
    try:
        wert = int(c.get("terminal_fps") or 0)
    except (TypeError, ValueError, AttributeError):
        wert = 0
    return wert if wert in TEXTUAL_FPS else TEXTUAL_FPS[0]


def haupt_umgebung(basis: dict | None, fps: int) -> dict:
    """Umgebung des Programms im Haupt-Reiter. TEXTUAL_FPS nur, wenn es nicht schon gesetzt ist."""
    env = dict(basis if basis is not None else os.environ)
    env["NEMICLI_FENSTER"] = "1"
    env["NEMICLI_STEUER_KEY"] = STEUER_KEY             # nur NemiCLI im Haupt-Reiter kennt ihn
    env.setdefault("TEXTUAL_FPS", str(fps))
    return env


def _fehler_melden(wo: str) -> None:
    """Fehler in den Debugbericht des Fenster-Prozesses (läuft er nicht, passiert nichts)."""
    try:
        import debugbericht
        debugbericht.fehler(wo)
    except Exception:
        pass


def befehle_einzeln(befehle, tun) -> list:
    """Jeder Steuerbefehl für sich: ein Fehler verwirft die übrigen nicht. Gibt die gescheiterten zurück."""
    gescheitert = []
    for befehl in befehle:
        try:
            tun(befehl)
        except Exception:
            gescheitert.append(befehl)
            _fehler_melden(f"Steuerbefehl {str(befehl).split('=', 1)[0][:40]}")
    return gescheitert


def maus_gewechselt(zuletzt, zelle, knopf, tasten) -> bool:
    """Bewegung melden? Nur, wenn sich Zelle, Knopf oder Umschalttasten geändert haben."""
    return zuletzt != (zelle, knopf, tasten)


_LADEN = re.compile(r"bild\d-|posen=|rig=|bewegung=")


def braucht_laden(befehl: str) -> bool:
    """Steuerbefehle, die Dateien lesen – sie laufen im Lade-Faden, in Reihenfolge."""
    return bool(_LADEN.match(befehl or ""))


def bild_laden(pfad, kante: int | None = BILD_KANTE_MAX):
    """QImage aus einer Datei (in jedem Faden erlaubt); sehr große Bilder gleich beim Lesen verkleinert. None,
    wenn es nicht lesbar ist."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImageReader
    leser = QImageReader(str(pfad))
    groesse = leser.size()
    if kante and groesse.isValid() and max(groesse.width(), groesse.height()) > kante:
        leser.setScaledSize(groesse.scaled(kante, kante, Qt.KeepAspectRatio))
    bild = leser.read()
    return None if bild.isNull() else bild


def steuer_laden(befehl: str):
    """Lade-Teil eines Steuerbefehls (Lade-Faden): (Art, Daten) mit QImage statt Pfaden, None bei Unlesbarem."""
    if befehl.startswith("bild"):
        bild = bild_befehl(befehl)
        if bild is None:
            return None
        nr, hoehe, pfad = bild
        return "bild", (nr, hoehe, bild_laden(pfad) if pfad is not None else None)
    fb = figur_befehl(befehl)
    if fb is None:
        return None
    name, wert = fb
    if name == "posen":
        art, pfade = wert
        return name, (art, {pose: b for pose, pfad in pfade.items() if (b := bild_laden(pfad, None)) is not None})
    if name == "rig":
        rigs = {}
        for ansicht, rig in wert.items():
            stuecke = [bild_laden(s["bild"], None) for s in rig["stuecke"]]
            gesichter = [{n: b for n, p in s["gesichter"].items() if (b := bild_laden(p, None)) is not None}
                         for s in rig["stuecke"]]
            if stuecke and all(b is not None for b in stuecke):
                rigs[ansicht] = dict(rig, roh_bilder=stuecke, roh_gesichter=gesichter)
        return name, rigs
    return name, wert


def farbe(wert: str, thema: dict, vordergrund: bool) -> str:
    """pyte-Farbe (Name, „brightred“ oder Hex ohne #) → „#rrggbb“."""
    if not wert or wert == "default":
        return thema["vordergrund"] if vordergrund else thema["hintergrund"]
    if wert.startswith("bright") and wert[6:] in _NAMEN:
        return thema["hell"][_NAMEN.index(wert[6:])]
    if wert in _NAMEN:
        return thema["normal"][_NAMEN.index(wert)]
    if len(wert) == 6:
        return "#" + wert
    return thema["vordergrund"] if vordergrund else thema["hintergrund"]


# ---------------------------------------------------------------------------
# Tasten → Terminal-Folgen (reine Funktion, testbar ohne Fenster)
# ---------------------------------------------------------------------------

_PFEILE = {"up": "A", "down": "B", "right": "C", "left": "D"}
_TILDE = {"insert": 2, "delete": 3, "pageup": 5, "pagedown": 6,
          "f5": 15, "f6": 17, "f7": 18, "f8": 19, "f9": 20, "f10": 21, "f11": 23, "f12": 24}
_SS3 = {"f1": "P", "f2": "Q", "f3": "R", "f4": "S"}


def taste_zu_folge(taste: str, text: str = "", *, shift=False, strg=False, alt=False,
                   anwendungs_cursor=False) -> str:
    """Eine Taste (Name wie „up“, „enter“, „f5“ oder leer) mit Text → was ins Terminal geht."""
    mod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if strg else 0)
    if taste in _PFEILE:
        if mod > 1:
            return f"\x1b[1;{mod}{_PFEILE[taste]}"
        return ("\x1bO" if anwendungs_cursor else "\x1b[") + _PFEILE[taste]
    if taste in ("home", "end"):
        z = "H" if taste == "home" else "F"
        return f"\x1b[1;{mod}{z}" if mod > 1 else f"\x1b[{z}"
    if taste in _TILDE:
        n = _TILDE[taste]
        return f"\x1b[{n};{mod}~" if mod > 1 else f"\x1b[{n}~"
    if taste in _SS3:
        return f"\x1b[1;{mod}{_SS3[taste]}" if mod > 1 else "\x1bO" + _SS3[taste]
    if taste == "enter":
        return "\x1b\r" if alt else "\r"
    if taste == "backspace":
        return "\x08" if strg else "\x7f"
    if taste == "tab":
        return "\x1b[Z" if shift else "\t"
    if taste == "escape":
        return "\x1b"
    if strg and text and len(text) == 1:
        c = text.lower()
        if "a" <= c <= "z":
            return ("\x1b" if alt else "") + chr(ord(c) - 96)
        steuer = {"[": "\x1b", "\\": "\x1c", "]": "\x1d", "^": "\x1e", "_": "\x1f"}
        if c in steuer:
            return steuer[c]
        if c == " ":
            return "\x00"
    if text:
        return ("\x1b" + text) if alt else text
    return ""


def maus_folge(knopf: int, spalte: int, zeile: int, *, losgelassen=False, bewegung=False,
               shift=False, alt=False, strg=False, sgr=True) -> str:
    """Mausereignis (Knopf 0 links, 1 Mitte, 2 rechts, 64/65 Rad) an Zelle (0-basiert) → Folge."""
    b = knopf + (32 if bewegung else 0) + (4 if shift else 0) + (8 if alt else 0) + (16 if strg else 0)
    x, y = spalte + 1, zeile + 1
    if sgr:
        return f"\x1b[<{b};{x};{y}{'m' if losgelassen else 'M'}"
    if losgelassen:
        b = 3 + (b & ~3)
    return "\x1b[M" + chr(32 + b) + chr(32 + min(x, 223)) + chr(32 + min(y, 223))


def abgelegt_zu_text(pfade: list[str]) -> str:
    """Abgelegte Dateien → Text wie in der Windows-Konsole: Pfade mit Leerzeichen in
    Anführungszeichen, durch Leerzeichen getrennt, eins am Ende zum Weitertippen."""
    teile = [f'"{p}"' if " " in p else p for p in pfade if p]
    return " ".join(teile) + " " if teile else ""


EINFUEGEN_TAGE = 7                       # so lange bleiben eingefügte Bilder liegen


def einfuege_ordner() -> Path:
    """Ablage für Bilder aus der Zwischenablage – der Chat bekommt ihren Pfad wie bei einer abgelegten Datei."""
    import tempfile
    return Path(tempfile.gettempdir()) / "NemiCLI" / "eingefuegt"


def einfuegungen_aufraeumen(ordner: Path, tage: float = EINFUEGEN_TAGE) -> None:
    grenze = time.time() - tage * 86400
    for p in ordner.glob("bild_*.png"):
        try:
            if p.stat().st_mtime < grenze:
                p.unlink()
        except OSError:
            pass


def nur_link(text: str) -> bool:
    """Ist der Text nur eine Web-Adresse? (Browser legen beim Bild-Kopieren oft die Adresse mit ab.)"""
    t = (text or "").strip()
    return bool(re.fullmatch(r"https?://\S+", t))


def geordnet(a: tuple, b: tuple) -> tuple:
    """Zwei Zellen (spalte, zeile) in Lesereihenfolge."""
    return (a, b) if (a[1], a[0]) <= (b[1], b[0]) else (b, a)


def auswahl_text(zeilen: list, start: tuple, ende: tuple) -> str:
    """Text zwischen zwei Zellen (einschließlich), Zeile für Zeile wie gelesen.
    `zeilen`: je Zeile die Zellinhalte; leere Zellen breiter Zeichen zählen nicht."""
    (x1, y1), (x2, y2) = geordnet(start, ende)
    raus = []
    for y in range(y1, y2 + 1):
        z = zeilen[y]
        von = x1 if y == y1 else 0
        bis = x2 if y == y2 else len(z) - 1
        raus.append("".join(c for c in z[von:bis + 1] if not ist_platzhalter(c)).rstrip())
    return "\n".join(raus)


# ---------------------------------------------------------------------------
# Echte Bilder: Die Sitzung füllt die Bildstelle mit Platzhaltern – Zeichen U+E000 + n·64 + z
# (n = Bild-Nummer, z = Zeile im Bild) – und schickt den Pfad als Steuerbefehl
# „bild<n>-<Zeilen>=<Pfad, base64>“. Das Fenster malt das Bild über die Platzhalter, scharf statt
# aus Zeichen-Blöcken, ohne Sixel oder Zusatzpaket. Aus der Zeilennummer weiß es, wo das ganze Bild
# säße, wenn ein Teil schon hinausgerollt ist – es wird angeschnitten, nicht gestaucht.
# ---------------------------------------------------------------------------

PLATZ_ERSTES, PLATZ_BILDER, PLATZ_ZEILEN = 0xE000, 8, 64
BILD_ENDUNGEN = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
BILD_MAX = 50 * 1024 * 1024


def ist_platzhalter(zeichen: str) -> bool:
    return len(zeichen) == 1 and PLATZ_ERSTES <= ord(zeichen) < PLATZ_ERSTES + PLATZ_BILDER * PLATZ_ZEILEN


def platzhalter(nr: int, zeile: int) -> str:
    return chr(PLATZ_ERSTES + nr * PLATZ_ZEILEN + zeile)


_PLATZ_SUCHE = re.compile(f"[{chr(PLATZ_ERSTES)}-{chr(PLATZ_ERSTES + PLATZ_BILDER * PLATZ_ZEILEN - 1)}]")


def _hat_platzhalter(zellen) -> bool:
    """Könnte die Zeile (pyte-Zellen) Bild-Platzhalter enthalten? Nur solche Zeilen sucht das Zeichnen ab."""
    return bool(_PLATZ_SUCHE.search("".join(z.data for z in zellen)))


def bild_befehl(befehl: str) -> tuple[int, int, Path | None] | None:
    """„bild<n>-<Zeilen>=<base64>“ -> (n, Zeilen, Pfad); leerer Pfad -> Pfad None = Bild weg.
    None: kein Bild-Befehl oder keine lesbare Bilddatei."""
    m = re.fullmatch(r"bild(\d)-(\d{1,2})=([A-Za-z0-9_=-]*)", befehl or "")
    if not m or int(m.group(1)) >= PLATZ_BILDER or not 1 <= int(m.group(2)) <= PLATZ_ZEILEN:
        return None
    nr, hoehe = int(m.group(1)), int(m.group(2))
    if not m.group(3):
        return nr, hoehe, None
    try:
        pfad = Path(base64.urlsafe_b64decode(m.group(3)).decode("utf-8"))
        if pfad.suffix.lower() in BILD_ENDUNGEN and pfad.is_file() and pfad.stat().st_size <= BILD_MAX:
            return nr, hoehe, pfad
    except (ValueError, OSError):
        pass
    return None


def platzhalter_kaesten(zeilen: list) -> dict[int, tuple[int, int, int, int, int]]:
    """Je Bild-Nummer der sichtbare Kasten (x0, y0, x1, y1) in Zellen und die oberste sichtbare Bildzeile."""
    kaesten: dict[int, list[int]] = {}
    for y, z in enumerate(zeilen):
        for x, c in enumerate(z):
            if ist_platzhalter(c):
                nr, zeile = divmod(ord(c) - PLATZ_ERSTES, PLATZ_ZEILEN)
                k = kaesten.get(nr)
                if k is None:
                    kaesten[nr] = [x, y, x, y, zeile]
                else:
                    k[0], k[1], k[2], k[3] = min(k[0], x), min(k[1], y), max(k[2], x), max(k[3], y)
                    k[4] = min(k[4], zeile)
    return {n: tuple(k) for n, k in kaesten.items()}


# ---------------------------------------------------------------------------
# Bildschirm: pyte + gemerkte Modi + Antworten ans Programm
# ---------------------------------------------------------------------------

STEUER_TITEL = "nemicli-steuer:"   # Titel „nemicli-steuer:<befehl>:<zähler>“ steuert das Fenster
STEUER_KEY = secrets.token_hex(8)    # je Fenster neu
NEUSTART_CODE = 75                   # Exit-Code von NemiCLI für /restart: das Fenster startet es neu


class Schirm(_NullbreiteAnhaengen, pyte.Screen):
    """pyte-Bildschirm, der Antworten (Cursor-Position, Gerätekennung) ans Programm
    zurückgibt und die privaten Modi merkt, die pyte selbst nicht auswertet.

    Steuerbefehle der Sitzung kommen als Konsolentitel (SetConsoleTitleW – die
    Pseudokonsole reicht ihn als OSC 0 weiter); der Zähler macht jeden Titel neu."""

    def __init__(self, spalten: int, zeilen: int, antworten=None, steuer=None):
        super().__init__(spalten, zeilen)
        self.antworten = antworten or (lambda _t: None)
        self.steuer = steuer or (lambda _b: None)
        self.privat: set[int] = set()

    def write_process_input(self, data: str) -> None:       # pyte ruft das für Antworten
        self.antworten(data)

    def set_title(self, param: str) -> None:
        if param.startswith(STEUER_TITEL):
            # „nemicli-steuer:<schlüssel>:<befehl>:<zähler>“ – ohne den Schlüssel dieses Fensters (etwa aus einer
            # ausgegebenen Datei oder einem fremden Programm) passiert nichts.
            teile = param[len(STEUER_TITEL):].split(":")
            if len(teile) >= 2 and secrets.compare_digest(teile[0].encode(), STEUER_KEY.encode()):
                self.steuer(teile[1])
            return
        super().set_title(param)

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            self.privat.update(modes)
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            self.privat.difference_update(modes)
        super().reset_mode(*modes, **kwargs)

    # Modi, die das Fenster braucht
    def maus_an(self) -> bool:
        return bool(self.privat & {1000, 1002, 1003})

    def maus_bewegung(self) -> bool:
        return 1003 in self.privat or 1002 in self.privat

    def maus_sgr(self) -> bool:
        return 1006 in self.privat

    def einfuegen_geklammert(self) -> bool:
        return 2004 in self.privat

    def fokus_melden(self) -> bool:
        return 1004 in self.privat

    def anwendungs_cursor(self) -> bool:
        return 1 in self.privat


# ---------------------------------------------------------------------------
# Das Fenster
# ---------------------------------------------------------------------------

def _terminal_punkt(c: dict) -> int:
    """Schrift des Terminal-Fensters in Punkt aus /schriftgröße terminal (px); ohne Wahl 11 pt."""
    try:
        wert = int((c.get("schriftgroesse") or {}).get("terminal", 0))
    except (AttributeError, TypeError, ValueError):
        wert = 0
    return max(8, min(27, round(wert * 0.75))) if wert else 11


def _konfiguration() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def _einstellungen() -> dict:
    c = _konfiguration()
    name = str(c.get("terminal_thema") or "auto").lower()
    thema = THEMEN.get(name)
    if thema is None:                                  # auto: wie die anderen Fenster nach /theme
        try:
            import design
            thema = thema_aus_palette(design.PALETTEN[design.palette_name()])
        except Exception:
            thema = THEMEN["breeze"]
    def zahl(schluessel, standard, unten, oben):
        try:
            return min(oben, max(unten, float(c.get(schluessel, standard))))
        except (TypeError, ValueError):
            return standard
    return {"thema": thema,
            "leuchten": bool(c.get("terminal_leuchten", True)),
            "weich": bool(c.get("terminal_weich", True)),
            "geteilt": bool(c.get("terminal_geteilt", False)),
            "figur": bool(c.get("terminal_figur", True)),
            "schrift": str(c.get("terminal_schrift") or ""),
            # /schriftgröße terminal (px) → Punkt; terminal_groesse (Punkt) hat Vorrang
            "groesse": int(c.get("terminal_groesse") or _terminal_punkt(c)),
            # ganzes Fenster (auch Schrift) – 1.0 = aus
            "deckkraft": zahl("terminal_deckkraft", 1.0, 0.5, 1.0),
            # nur der Hintergrund, verschwommen (Windows 11 Acrylic) – 1.0 = deckend
            "hintergrund": zahl("terminal_hintergrund", 0.82, 0.3, 1.0),
            "unschaerfe": bool(c.get("terminal_unschaerfe", True)),
            # Reiter „KI“: Befehle der KI live mitlesen
            "ki_reiter": bool(c.get("terminal_ki_reiter", True)),
            # Bildrate der Oberfläche im Haupt-Reiter (TEXTUAL_FPS)
            "fps": textual_fps(c)}


def acrylic(hwnd: int) -> bool:
    """Windows-11-Hintergrund „Acrylic“ (verschwommen durchsichtig) und dunkle Titelleiste.
    False, wenn Windows das nicht kann (z. B. Windows 10) – dann bleibt es deckend."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes as w

        class _MARGINS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("o", ctypes.c_int), ("u", ctypes.c_int)]
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        dwm.DwmExtendFrameIntoClientArea.argtypes = [w.HWND, ctypes.POINTER(_MARGINS)]
        dunkel = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dunkel), 4)       # DWMWA_USE_IMMERSIVE_DARK_MODE
        art = ctypes.c_int(3)                                               # DWMSBT_TRANSIENTWINDOW = Acrylic
        if dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(art), 4) != 0:  # DWMWA_SYSTEMBACKDROP_TYPE
            return False
        rand = _MARGINS(-1, -1, -1, -1)
        return dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(rand)) == 0
    except Exception:
        return False


def _app_kennung() -> None:
    """Eigene Taskleisten-Identität: NemiCLI erscheint als eigene App, nicht als Python."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NemiCLI.Terminal")
        except Exception:
            pass


class KiSchirm(_NullbreiteAnhaengen, pyte.HistoryScreen):
    """Bildschirm des Reiters „KI“: nur zum Lesen, mit Verlauf zum Zurückrollen. Er antwortet nicht,
    kennt keine Maus- und Steuer-Modi – Ausgaben der Befehle können dort nichts auslösen."""

    VERLAUF = 5000

    def __init__(self, spalten: int, zeilen: int):
        super().__init__(spalten, zeilen, history=self.VERLAUF, ratio=0.5)
        self.geschoben = 0             # Zeilen, die je oben hinausgerollt sind (bleibende Zeilennummern)

    def index(self) -> None:
        unten = (self.margins.bottom if self.margins else self.lines - 1)
        if self.cursor.y == unten:
            self.geschoben += 1
        super().index()

    def write_process_input(self, data: str) -> None:
        pass

    def maus_an(self) -> bool:
        return False

    maus_bewegung = maus_sgr = einfuegen_geklammert = fokus_melden = anwendungs_cursor = maus_an


def ki_text(ereignis: dict) -> str:
    """Ereignis von ki_terminal.py -> Terminal-Text (CR LF, Farben). Fremder Text behält nur Farben: Cursor- und
    Löschfolgen könnten sonst die Zeile mit dem Exitcode überschreiben."""
    t = ereignis.get("t")
    if t == "start":
        befehl = terminal_text.nur_farben(str(ereignis.get("befehl") or "").replace("\r\n", "\n"))
        befehl = befehl.replace("\n", "\r\n   ")
        ordner = terminal_text.nur_farben(str(ereignis.get("ordner") or ""))
        kopf = f"\x1b[2m{ordner}\x1b[0m\r\n" if ordner else ""
        return f"\r\n{kopf}\x1b[1;36m❯\x1b[0m \x1b[1m{befehl}\x1b[0m\r\n"
    if t == "aus":
        text = terminal_text.nur_farben(str(ereignis.get("text") or "").replace("\r\n", "\n")).replace("\n", "\r\n")
        return f"\x1b[31m{text}\x1b[0m" if ereignis.get("fehler") else text
    if t == "ende":
        dauer = str(ereignis.get("sekunden", "")).replace(".", ",")
        if ereignis.get("abgebrochen"):
            return f"\x1b[33m⏹ abgebrochen\x1b[0m \x1b[2m({dauer} s)\x1b[0m\r\n"
        code = ereignis.get("code")
        if code in (0, 250):
            return f"\x1b[32m✓ fertig\x1b[0m \x1b[2m({dauer} s)\x1b[0m\r\n"
        return f"\x1b[31m✗ Exitcode {code}\x1b[0m \x1b[2m({dauer} s)\x1b[0m\r\n"
    return ""


class KiLeitung:
    """Named Pipe, über die NemiCLI die Befehle der KI meldet (core/ki_terminal.py). Jede Verbindung muss
    den Schlüssel beweisen (multiprocessing-Challenge); danach kommen nur JSON-Ereignisse."""

    MAX_NACHRICHT = 1 << 20

    def __init__(self):
        import secrets
        from multiprocessing.connection import Listener
        self.adresse = r"\\.\pipe\nemicli-ki-" + secrets.token_hex(8)
        self.schluessel = secrets.token_bytes(32)
        self._listener = Listener(self.adresse, family="AF_PIPE", authkey=self.schluessel)
        self._lock = threading.Lock()
        self._ereignisse: list[dict] = []
        threading.Thread(target=self._annehmen, name="ki-annehmen", daemon=True).start()

    def umgebung(self) -> dict:
        return {"NEMICLI_KI_PIPE": self.adresse, "NEMICLI_KI_KEY": self.schluessel.hex()}

    def _annehmen(self) -> None:
        while True:
            try:
                conn = self._listener.accept()
            except Exception:
                return
            threading.Thread(target=self._lesen, args=(conn,), name="ki-lesen", daemon=True).start()

    def _lesen(self, conn) -> None:
        import json
        try:
            while True:
                ereignis = json.loads(conn.recv_bytes(self.MAX_NACHRICHT).decode("utf-8"))
                if isinstance(ereignis, dict):
                    with self._lock:
                        self._ereignisse.append(ereignis)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def holen(self) -> list[dict]:
        with self._lock:
            ereignisse, self._ereignisse = self._ereignisse, []
        return ereignisse


REITER_ZEICHEN = 28
PROTOKOLL_MAX = 2_000_000            # Zeichen, die der Reiter „KI“ für den Neuaufbau behält


def reiter_name(titel: str) -> str:
    """Fenstertitel eines Programms -> kurzer Reitername (Pfade nur mit dem letzten Teil)."""
    titel = titel.strip()
    if re.match(r"^[A-Za-z]:[\\/]|^[\\/]", titel):
        titel = re.split(r"[\\/]", titel.split(" - ")[0].rstrip("\\/"))[-1] + (
            " - " + titel.split(" - ", 1)[1] if " - " in titel else "")
    return titel if len(titel) <= REITER_ZEICHEN else titel[:REITER_ZEICHEN - 1] + "…"


def _shell() -> list[str]:
    """Befehl für einen neuen Reiter: die Windows-Kommandozeile aus COMSPEC."""
    return [os.environ.get("COMSPEC") or "cmd.exe"]


def schutz_starten() -> None:
    """Fenster-Prozess: Fehler-Haken (sys, threading) und faulthandler über den Debugbericht der Rolle „fenster“,
    in einem eigenen Unterordner (die Berichte der Sitzung bleiben vollzählig), ohne Speicher-Takt. Im Hintergrund:
    das Fenster wartet nicht darauf."""
    def los():
        try:
            import debugbericht
            debugbericht.starten("fenster", debugbericht.ordner() / "Fenster", takt=False)
        except Exception:
            pass
    threading.Thread(target=los, name="fenster-bericht", daemon=True).start()


def starten(befehl: list[str], *, ordner: str | None = None, env: dict | None = None,
            titel: str = "NemiCLI") -> int:
    """Öffnet das Fenster, startet `befehl` darin und kehrt zurück, wenn es zu ist.
    Weitere Reiter: Kommandozeile (Strg+Umschalt+T) und „🤖 KI“ (Befehle der KI live, nur lesen)."""
    _app_kennung()
    from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
    from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QIcon, QImage,
                               QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient,
                               QRegion, QShortcut, QTransform)
    from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QSlider,
                                   QSplitter, QStackedWidget, QSystemTrayIcon, QTabBar, QToolButton, QToolTip,
                                   QVBoxLayout, QWidget)
    import terminal_pty

    e = _einstellungen()
    thema = e["thema"]
    akzent, akzent2 = akzente(thema)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    import taskleiste
    taskleiste.installieren(app, "NemiCLI.Terminal")          # Symbol in der Taskleiste auch ohne Verknüpfung
    try:
        ki_leitung = KiLeitung() if e["ki_reiter"] else None
    except Exception:
        ki_leitung = None

    def _pixmap(quelle):
        """QPixmap aus einem geladenen QImage (Lade-Faden) oder einem Pfad."""
        if isinstance(quelle, QImage):
            return QPixmap.fromImage(quelle)
        return QPixmap(str(quelle))

    class Ansicht(QWidget):
        """Ein Reiter: Programm in einer Pseudokonsole – oder nur zum Lesen (Reiter „KI“)."""

        weckruf = Signal()            # aus Lese- und Warte-Faden: es gibt Neues
        geladen = Signal(object)      # aus dem Lade-Faden: (Art, Daten) eines Steuerbefehls

        def __init__(self, fenster, programm=None, *, kind_env=None, nur_lesen=False, haupt=False):
            super().__init__(fenster)
            self.fenster, self.programm, self.kind_env = fenster, programm, kind_env
            self.nur_lesen, self.haupt = nur_lesen, haupt
            self.setFocusPolicy(Qt.StrongFocus)
            if not fenster.durchsichtig:
                self.setAttribute(Qt.WA_OpaquePaintEvent)
            self.grund = QColor(thema["hintergrund"])
            if fenster.durchsichtig:
                self.grund.setAlphaF(e["hintergrund"])
            self.setMouseTracking(True)
            self.setAttribute(Qt.WA_InputMethodEnabled, True)     # Emoji-Feld (Win + .), IME
            self.setAcceptDrops(not nur_lesen)
            self.schrift, self.fett = fenster.schrift, fenster.fett
            self.zb, self.zh, self.oben = fenster.zb, fenster.zh, fenster.oben
            self.lock = threading.Lock()
            self.schirm = KiSchirm(100, 30) if nur_lesen else Schirm(100, 30, self._senden, self._steuer)
            self.titel_alt = ""
            self.protokoll: list[str] = []            # Reiter „KI“: alles Eingespeiste
            self.protokoll_laenge = 0
            self.steuerbefehle = []       # aus dem Lese-Thread, ausgeführt im Takt
            self.bilder = {}              # Bild-Nummer -> QPixmap (echte Bilder über Platzhaltern)
            self.bilder_skaliert = {}
            self.strom = pyte.Stream(self.schirm)
            self.neu = False
            self.ende = False
            self.pty = None
            self.maus_knopf = None
            # Markieren: Will das Programm die Maus (NemiCLI), bekommt es auch das Ziehen – es markiert
            # selbst, rollt am Rand mit und kopiert. Sonst – oder mit Umschalt – markiert das Fenster
            # (nur Sichtbares); ein Klick ohne Ziehen geht ans Programm.
            self.druck = None             # (zelle, shift, alt, strg) beim Drücken links
            self.zieht = False
            self.auswahl = None           # (start, ende) in Zellen (Spalte, bleibende Zeilennummer)
            self.zeilen_bild = {}         # Zeileninhalt -> QPixmap (None = leere Zeile), älteste zuerst
            self.zeilen_schluessel = {}   # Bildschirmzeile -> Inhalt beim letzten Zeichnen
            # Bildschirmzeile -> (Bild, hat Platzhalter) zum Inhalt in zeilen_schluessel; gilt, solange die Zeile
            # nicht in schirm.dirty steht – unveränderte Zeilen werden so weder neu gelesen noch neu gehasht
            self.zeilen_bild_y = {}
            self.verlauf_bild = {}        # Reiter „KI“: bleibende Nummer einer Verlaufszeile -> (Bild, Platzhalter)
            self.alles_neu = False        # beim nächsten Takt die ganze Fläche neu zeichnen
            self.cursor_spur = set()      # Zeilen, in denen der Cursor seit dem letzten Neuzeichnen gemalt wurde
            self.cursor_gleitet = False
            self.roll_gemalt = 0.0
            self.maus_zuletzt = None      # (Zelle, Knopf, Tasten) der zuletzt gemeldeten Bewegung
            self.ki_rest = collections.deque()    # Reiter „KI“: Text, der noch eingespeist wird
            self.ki_bau = None            # Reiter „KI“: Bildschirm in neuer Größe, der im Takt aufgebaut wird
            self.raster_gesetzt = False
            self.raster_warten = QTimer(self)
            self.raster_warten.setSingleShot(True)
            self.raster_warten.setInterval(KI_RASTER_WARTEN_MS)
            self.raster_warten.timeout.connect(self._ki_neu_aufbauen)
            self.lade_schlange = None     # Steuerbefehle, die Dateien lesen (eigener Faden, Reihenfolge bleibt)
            self._geweckt = False
            self.bild_stand = None        # (Spalten, Bildpunkt-Verhältnis), für die die Bilder gelten
            self.grund_programm = ""      # „#rrggbb“: Grundfarbe des Programms – solche Zellen bleiben frei
            self.puls = False             # das Programm arbeitet: der Hintergrund atmet
            self.puls_seit = 0.0
            self.puls_stufe = 0           # gezeichnete Stärke des Leuchtens; Teilbilder nehmen dieselbe
            self.puls_naechste = 0.0
            self.glanz_seit = 0.0         # Zeitpunkt, an dem das Programm zuletzt fertig wurde
            self.figur_art = ""           # „figur“ (ganz, sitzend) oder „buste“; leer = keine Figur
            self.figur_bilder = {}        # Pose -> QPixmap
            self.figur_skaliert = {}
            self.figur_basis, self.figur_kurz, self.figur_kurz_seit = "sitzen", None, 0.0
            self.figur_zeigt, self.figur_seit, self.figur_alt = "sitzen", time.monotonic(), None
            self.figur_sitz = FIGUR_SITZ_VORGABE
            self.figur_taste = time.monotonic()
            self.figur_rect = None        # wo sie zuletzt gemalt wurde (für das Neuzeichnen im Takt)
            # Zeitpunkt des Figur-Bildes, das gerade gilt: jedes Zeichnen (auch ein Teilbild) zeigt dasselbe Bild
            self.figur_zeit = time.monotonic()
            self.figur_naechste = 0.0
            self.figur_merk = None        # (Schlüssel, Daten): Strichmännchen und Hülle für das aktuelle Bild
            self.strich_basis = None      # Strichmännchen: Ruhe- bzw. Status-Schleife
            self.strich_akt = None        # die Bewegung, die gerade läuft
            self.strich_start = 0.0
            self.strich_von = None        # (Gelenke, Zeit): woher der weiche Übergang kommt
            self.strich_gesichter = {}    # Name -> rund ausgeschnittenes Gesicht (QPixmap), nach Größe
            self.strich_rig = {}          # Ansicht -> bewegliche Figur (Bild als QPixmap, verkleinerte Fassungen)
            self.figur_justierung = dict(figurmass.VORGABE)   # Größe, Längen, Platz (Figur-Justierer)
            self.justier_datei = None     # wohin der Justierer die Werte schreibt (vom Programm gemeldet)
            self.justierer = None         # das Feld mit den Reglern, wenn es offen ist
            self.figur_koerper = None     # wo ihr Körper gerade ist (Rechtsklick darauf öffnet den Justierer)
            self.leuchten_bild = None     # (Größe, Bild) des Leuchtens
            self.roll = self.roll_ziel = 0.0          # nur Lesen: Bildpunkte über dem Ende des Verlaufs
            self.geschoben_alt = 0
            self.cursor_xy = None         # gleitender Cursor (Spalte, Zeile als Kommazahl)
            self.such_begriff = ""
            self.treffer = []             # (bleibende Zeilennummer, erste Zelle, letzte Zelle)
            self.treffer_nr = -1
            self.link = None              # (Zeilennummer, erste Zelle, letzte Zelle, Art, Ziel) unter der Maus
            self.link_merk = (None, [])   # zuletzt untersuchte Zeile und ihre Links
            self.link_klick = False
            self.weckruf.connect(self._wach, Qt.QueuedConnection)
            self.geladen.connect(self._geladen, Qt.QueuedConnection)
            self.takt = QTimer(self)      # läuft nur, solange etwas ansteht; Neues weckt ihn (weckruf)
            self.takt.timeout.connect(self._takt)
            self.takt.start(16)

        # -- Programm ------------------------------------------------------------
        def programm_starten(self):
            sp, ze = self._raster()
            with self.lock:
                self.schirm.resize(ze, sp)
            kind_env = dict(self.kind_env if self.kind_env is not None else os.environ)
            kind_env.update({"COLORTERM": "truecolor", "TERM": "xterm-256color"})
            if self.haupt and fps_frei:                 # auch nach /restart die eingestellte Bildrate
                kind_env["TEXTUAL_FPS"] = str(textual_fps(_konfiguration()))
            self.pty = terminal_pty.Pty(self.programm, sp, ze, ordner=ordner, env=kind_env,
                                        bei_ausgabe=self._ausgabe, bei_ende=self._beendet)

        def _ausgabe(self, text):                       # Lese-Thread
            # In Stücken: zwischen zwei Stücken kann das Zeichnen die Zeilen lesen
            for stueck in feed_stuecke(text):
                with self.lock:
                    self.strom.feed(stueck)
            self.neu = True
            self._wecken()

        def einspeisen(self, text):
            """Reiter „KI“: Text ohne Programm. Er bleibt gemerkt – bei neuer Größe wird der Bildschirm
            daraus neu aufgebaut (pyte verliert beim Verkleinern Zeilen; ein Programm zeichnet selbst neu).
            Eingespeist wird im Takt, in Stücken: lange Ausgaben halten das Fenster nicht an."""
            self.protokoll.append(text)
            self.protokoll_laenge += len(text)
            while self.protokoll_laenge > PROTOKOLL_MAX and len(self.protokoll) > 1:
                self.protokoll_laenge -= len(self.protokoll.pop(0))
            self.ki_rest.append(text)
            if self.ki_bau is not None:
                self.ki_bau["rest"].append(text)
            self._antreiben()

        def _ki_nachspeisen(self):
            """Reiter „KI“: wartenden Text einspeisen, höchstens KI_EINSPEISEN_S je Takt."""
            ende = time.perf_counter() + KI_EINSPEISEN_S
            while self.ki_rest and time.perf_counter() < ende:
                kopf, rest = feed_kopf(self.ki_rest.popleft())
                if rest:
                    self.ki_rest.appendleft(rest)
                with self.lock:
                    self.strom.feed(kopf)
                self.neu = True

        def _ki_neu_aufbauen(self):
            """Reiter „KI“ in neuer Größe: der Bildschirm wird im Takt neben dem alten aufgebaut (nur das Ende des
            Protokolls, das in den Verlauf passt) und ersetzt ihn erst, wenn er fertig ist."""
            sp, ze = self._raster()
            if (sp, ze) == (self.schirm.columns, self.schirm.lines):
                self.ki_bau = None                          # wieder die alte Größe: der Bildschirm passt
                return
            schirm = KiSchirm(sp, ze)
            self.ki_bau = {"schirm": schirm, "strom": pyte.Stream(schirm), "groesse": (sp, ze),
                           "rest": collections.deque(protokoll_ende(self.protokoll, KiSchirm.VERLAUF + ze))}
            self._antreiben()

        def _ki_bau_weiter(self):
            bau = self.ki_bau
            ende = time.perf_counter() + KI_EINSPEISEN_S
            while bau["rest"] and time.perf_counter() < ende:
                kopf, rest = feed_kopf(bau["rest"].popleft())
                if rest:
                    bau["rest"].appendleft(rest)
                bau["strom"].feed(kopf)
            if bau["rest"]:
                return
            self.ki_bau = None
            if bau["groesse"] != self._raster():           # inzwischen wieder anders groß
                self._ki_neu_aufbauen()
                return
            with self.lock:
                self.schirm, self.strom = bau["schirm"], bau["strom"]
                self.ki_rest.clear()                        # alles Wartende steht schon im neuen Bildschirm
                # neu aufgebaut: Zeilennummern, Rollstand und Treffer gelten nicht mehr
                self.geschoben_alt = self.schirm.geschoben
                self.roll = self.roll_ziel = 0.0
                self.auswahl, self.treffer, self.treffer_nr, self.link = None, [], -1, None
                self._zeilen_vergessen()
            self.alles_neu = True

        def _zeilen_vergessen(self):
            """Gemerkte Zeilen gelten nicht mehr (neuer Bildschirm, neue Größe). Mit `lock` aufrufen."""
            self.zeilen_schluessel.clear()
            self.zeilen_bild_y.clear()
            self.verlauf_bild.clear()

        def bilder_verwerfen(self):
            """Zeilenbilder neu malen (andere Grundfarbe, Leuchten an/aus) – die ganze Fläche."""
            self.zeilen_bild.clear()
            with self.lock:
                self.zeilen_bild_y.clear()
                self.verlauf_bild.clear()
            self.alles_neu = True
            self._antreiben()

        def _beendet(self, _code):                      # Warte-Thread
            self.ende = True
            self._wecken()

        # -- Takt: läuft nur, solange etwas ansteht ------------------------------
        def _wecken(self):                              # aus jedem Faden
            if not self._geweckt:
                self._geweckt = True
                try:
                    self.weckruf.emit()
                except RuntimeError:                    # Fenster schon zu
                    pass

        def _wach(self):
            self._geweckt = False
            self._takt()

        def _antreiben(self):
            """Den Takt (wieder) schnell laufen lassen."""
            if not self.takt.isActive() or self.takt.interval() != 16:
                self.takt.start(16)

        def _sichtbar(self) -> bool:
            return self.isVisible() and not self.window().isMinimized()

        def _figur_bilder_je_s(self) -> int:
            if self.figur_art == "strich":
                ruhe = (self.strich_akt is None or self.strich_akt is self.strich_basis) and self.strich_von is None
                return FIGUR_BILDER if ruhe else STRICH_BILDER
            ruhe = self.figur_zeigt == "sitzen" and self.figur_alt is None
            return FIGUR_RUHE_BILDER if ruhe else FIGUR_BILDER

        def _takt_planen(self, sichtbar):
            schnell = (self.neu or self.alles_neu or self.cursor_gleitet or self.ki_rest or self.ki_bau is not None
                       or (self.nur_lesen and self.roll != self.roll_ziel))
            if schnell:
                self._antreiben()
                return
            abstaende = []
            if sichtbar and self.figur_rect is not None:
                abstaende.append(1000 // self._figur_bilder_je_s())
            if sichtbar and self.fenster.leuchten and (self.puls or self._glanz() > 0 or self.puls_stufe != 0):
                abstaende.append(int(PULS_TAKT_S * 1000))
            if not abstaende:
                self.takt.stop()
                return
            abstand = min(abstaende)
            if not self.takt.isActive() or self.takt.interval() != abstand:
                self.takt.start(abstand)

        def _neu_zeichnen(self):
            """Nur die geänderten Zeilen (und wo der Cursor war und ist) neu zeichnen – oder alles."""
            ganz = self.alles_neu or (self.nur_lesen and self.roll != self.roll_gemalt)
            self.alles_neu = False
            if not ganz:
                with self.lock:
                    s = self.schirm
                    zeilen = set(s.dirty)
                    c = s.cursor
                    ganz = len(zeilen) * 2 > s.lines or self.bild_stand is None
                if not ganz:
                    zeilen |= self.cursor_spur
                    y = self.cursor_xy[1] if self.cursor_xy is not None else c.y
                    zeilen |= {math.floor(y), math.ceil(y)}
                    self.cursor_spur = set()
                    flaeche = QRegion()
                    for oben, hoehe in zeilen_streifen(zeilen, self.zh, 8 + self.roll):
                        flaeche += QRect(0, oben, self.width(), hoehe)
                    if not flaeche.isEmpty():
                        self.update(flaeche)
                    return
            self.cursor_spur = set()
            self.update()

        def showEvent(self, ev):
            super().showEvent(ev)
            self._antreiben()                           # wieder da (Reiter, Tray): Figur und Atmen weiter

        def _senden(self, text):
            if self.pty is not None and text and not self.nur_lesen:
                self.pty.schreiben(text)

        def _steuer(self, befehl):                      # Lese-Thread
            if self.haupt:                              # Titel aus Kommandozeilen-Reitern steuern nichts
                self.steuerbefehle.append(befehl)

        def beenden(self):
            if self.pty is not None and self.pty.laeuft():
                self.pty.beenden()

        def _takt(self):
            if self.ende:
                if self.haupt and self.pty is not None and self.pty.exitcode == NEUSTART_CODE:
                    # /restart: NemiCLI hat sich zum Neustart beendet – im selben Fenster neu starten, erst
                    # jetzt, wo der alte Prozess wirklich weg ist (Grafikkarte und Dateien sind frei).
                    self.ende = False
                    self.puls = False
                    with self.lock:
                        self.schirm.reset()
                        self.neu = True
                    self.programm_starten()
                    self._antreiben()
                    return
                self.takt.stop()
                self.fenster.programm_zu(self)
                return
            with self.lock:                             # _steuer hängt unter derselben Sperre an (aus _ausgabe)
                befehle, self.steuerbefehle = self.steuerbefehle, []
            befehle_einzeln(befehle, self._steuerbefehl)
            if not self.haupt and not self.nur_lesen and self.schirm.title != self.titel_alt:
                self.titel_alt = self.schirm.title
                self.fenster.reiter_benennen(self, self.titel_alt)
            if self.ki_bau is not None:
                self._ki_bau_weiter()
            if self.ki_rest:
                self._ki_nachspeisen()
            if self.nur_lesen:
                self._rollen_takt()
            if self._cursor_takt():
                self.neu = True
            sichtbar = self._sichtbar()
            jetzt = time.monotonic()
            if self.figur_rect is not None and sichtbar and jetzt >= self.figur_naechste:
                self.figur_zeit = jetzt                 # nächstes Bild der Figur, nur ihr Bereich
                self.figur_naechste = jetzt + 1 / self._figur_bilder_je_s()
                self.update(self.figur_rect.toAlignedRect())
            if self.fenster.leuchten and sichtbar and jetzt >= self.puls_naechste:
                self.puls_naechste = jetzt + PULS_TAKT_S
                stufe = puls_stufe(jetzt - self.puls_seit if self.puls else None, self._glanz())
                if stufe != self.puls_stufe:            # das Leuchten liegt hinter allem: dann die ganze Fläche
                    self.puls_stufe = stufe
                    self.alles_neu = True
            if self.neu or self.alles_neu:
                self.neu = False
                self._neu_zeichnen()
            self._takt_planen(sichtbar)

        def _steuerbefehl(self, befehl):
            if befehl in ("tray", "zeigen"):
                self.fenster.in_tray(befehl == "tray")
            elif (wert := steuer_wert(befehl)) is not None:
                self._steuer_wert(*wert)
            elif braucht_laden(befehl):
                self._laden(befehl)
            elif (fb := figur_befehl(befehl)) is not None:
                self._figur_steuern(*fb)

        # -- Lade-Faden: Bilder und Bewegungen lesen, ohne das Fenster anzuhalten ------
        def _laden(self, befehl):
            if self.lade_schlange is None:
                self.lade_schlange = queue.SimpleQueue()
                threading.Thread(target=self._lade_lauf, args=(self.lade_schlange,), name="terminal-laden",
                                 daemon=True).start()
            self.lade_schlange.put(befehl)

        def _lade_lauf(self, schlange):
            while True:
                befehl = schlange.get()
                try:
                    ergebnis = steuer_laden(befehl)
                except Exception:
                    _fehler_melden(f"Steuerbefehl {befehl.split('=', 1)[0][:40]} laden")
                    continue
                if ergebnis is None:
                    continue
                try:
                    self.geladen.emit(ergebnis)
                except RuntimeError:                    # Fenster schon zu
                    return

        def _geladen(self, ergebnis):
            art, daten = ergebnis
            try:
                if art == "bild":
                    self._bild_setzen(*daten)
                else:
                    self._figur_steuern(art, daten)
            except Exception:
                _fehler_melden(f"Steuerbefehl {art}")

        def _steuer_wert(self, name, wert):
            if name == "grund" and wert != self.grund_programm:
                # Das Programm füllt jede Zelle mit seiner Grundfarbe. Diese Zellen bleiben frei, sonst
                # deckten sie Durchsicht und Leuchten des Fensters zu.
                self.grund_programm = wert
                self.grund = QColor(wert)
                if self.fenster.durchsichtig:
                    self.grund.setAlphaF(e["hintergrund"])
                self.bilder_verwerfen()
            elif name == "puls":
                an = wert == "1"
                if an and not self.puls:
                    self.puls_seit = time.monotonic()
                elif self.puls and not an:
                    self.glanz_seit = time.monotonic()
                self.puls, self.neu = an, True
                self.puls_naechste = 0.0
            elif name == "leuchten":
                self.fenster.leuchten_setzen(wert == "1")
            elif name == "weich":
                self.fenster.weich = wert == "1"
            elif name == "geteilt":
                self.fenster.teilen(wert == "1")
            elif name == "figur":
                self.fenster.figur_setzen(wert == "1")

        def _figur_steuern(self, name, wert):
            if name == "justierung":
                self.justier_datei = wert["datei"]
                if self.justierer is None:              # offen: die Regler des Nutzers gelten
                    self._justierung_setzen(wert["werte"])
                return
            if name == "pose":
                jetzt = time.monotonic()
                if wert in FIGUR_KURZ:
                    self.figur_kurz, self.figur_kurz_seit = wert, jetzt
                else:
                    self.figur_basis, self.figur_kurz = wert, None
                self._figur_neues_bild(jetzt)
            elif name == "sitz":
                if wert != self.figur_sitz:
                    self.figur_sitz = wert          # die Größe der Figur hängt daran – und damit die freien Spalten
                    self.raster_anwenden()
                    self.update()
            elif name == "posen":
                art, quellen = wert                 # Pose -> QImage (Lade-Faden) oder Pfad
                bilder = {}
                for pose, quelle in quellen.items():
                    bild = _pixmap(quelle)
                    if not bild.isNull():
                        bilder[pose] = bild
                self.figur_art = art if art == "strich" or "sitzen" in bilder else ""
                self.figur_bilder = bilder if self.figur_art else {}
                self.figur_skaliert.clear()
                self.strich_gesichter.clear()
                if art != "strich":
                    self.strich_basis = self.strich_akt = self.strich_von = None
                self._figur_neues_bild()
                self.raster_anwenden()
                self.update()
            elif name == "rig":
                rigs = {}
                for ansicht, rig in wert.items():
                    roh = rig.get("roh_bilder") or [s["bild"] for s in rig["stuecke"]]
                    roh_g = rig.get("roh_gesichter") or [s["gesichter"] for s in rig["stuecke"]]
                    bilder = [_pixmap(b) for b in roh]
                    gesichter = [{n: _pixmap(q) for n, q in g.items()} for g in roh_g]
                    if bilder and not any(b.isNull() for b in bilder):
                        rig = {k: v for k, v in rig.items() if k not in ("roh_bilder", "roh_gesichter")}
                        rigs[ansicht] = dict(rig, pixmaps=bilder, gesicht_pixmaps=gesichter, klein={})
                self.strich_rig = rigs if "vorne" in rigs else {}
                self._figur_neues_bild()
                self.update()
            elif name == "bewegung":
                erste = self.strich_akt is None and self.strich_basis is None
                jetzt = time.monotonic()
                self._strich_neu(wert, jetzt)
                self._figur_neues_bild(jetzt)
                if erste:                           # erst jetzt hat sie eine Größe: rechts Platz machen
                    self.raster_anwenden()
                self.update()

        def _figur_neues_bild(self, jetzt=None):
            """Die Figur hat sich geändert: gleich ein neues Bild (nicht erst zum nächsten Figur-Takt)."""
            self.figur_zeit = time.monotonic() if jetzt is None else jetzt
            self.figur_naechste = 0.0
            self.figur_merk = None
            self._antreiben()

        def _rollen_takt(self):
            """Reiter „KI“: Kommt Neues, während zurückgerollt ist, bleibt die Ansicht stehen; das Rollen
            selbst läuft in kleinen Schritten auf sein Ziel zu."""
            with self.lock:
                neu = self.schirm.geschoben - self.geschoben_alt
                self.geschoben_alt = self.schirm.geschoben
                hoechstens = self._oben() * self.zh
            if neu > 0 and self.roll_ziel > 0:
                self.roll += neu * self.zh
                self.roll_ziel += neu * self.zh
            self.roll_ziel = max(0.0, min(self.roll_ziel, hoechstens))
            self.roll = max(0.0, min(self.roll, hoechstens))
            if self.roll != self.roll_ziel:
                rest = self.roll_ziel - self.roll
                springen = not self.fenster.weich or abs(rest) < 1.0
                self.roll = self.roll_ziel if springen else self.roll + rest * 0.3
                self.neu = True

        def rollen(self, bildpunkte: float):
            """Reiter „KI“ zurück- (+) oder vorrollen (−)."""
            self.roll_ziel += bildpunkte
            self._antreiben()

        def _cursor_takt(self) -> bool:
            """Der Cursor gleitet kurze Wege, weite springt er. True: neu zeichnen."""
            self.cursor_gleitet = False
            c = self.schirm.cursor
            ziel = (float(c.x), float(c.y))
            alt = self.cursor_xy
            if alt == ziel:
                return False
            if alt is None or c.hidden or self.nur_lesen:
                self.cursor_xy = ziel
                return False
            if not self.fenster.weich or abs(alt[1] - ziel[1]) > 1.0 or abs(alt[0] - ziel[0]) > 24.0:
                self.cursor_xy = ziel
                return True
            x, y = alt[0] + (ziel[0] - alt[0]) * 0.45, alt[1] + (ziel[1] - alt[1]) * 0.45
            self.cursor_xy = ziel if abs(x - ziel[0]) < 0.05 and abs(y - ziel[1]) < 0.05 else (x, y)
            self.cursor_gleitet = self.cursor_xy != ziel
            return True

        # -- Zeilen: Verlauf und Bildschirm als eine Folge --------------------------
        def _oben(self) -> int:
            """Zeilen im Verlauf über dem Bildschirm (nur der Reiter „KI“ hat einen)."""
            return len(self.schirm.history.top) if self.nur_lesen else 0

        def _versatz(self) -> int:
            """Bleibende Nummer der ältesten Zeile – sie zählt weiter, wenn der Verlauf vorn kürzt."""
            return self.schirm.geschoben - self._oben() if self.nur_lesen else 0

        def _zeile(self, v: int):
            oben = self._oben()
            return self.schirm.history.top[v] if v < oben else self.schirm.buffer[v - oben]

        def _zellen(self, nummer: int) -> list | None:
            """Zellinhalte der Zeile mit dieser bleibenden Nummer (None: gibt es nicht mehr)."""
            v = nummer - self._versatz()
            if not 0 <= v < self._oben() + self.schirm.lines:
                return None
            zeile = self._zeile(v)
            return [zeile[x].data for x in range(self.schirm.columns)]

        def _zeilen_y(self, nummer: int, oben: int, versatz: int) -> float:
            return 8 + (nummer - versatz - oben) * self.zh + self.roll

        def _bild_setzen(self, nr, hoehe, quelle):
            """Bild Nummer `nr` (QImage aus dem Lade-Faden oder Pfad); None = Bild weg."""
            self.bilder_skaliert = {k: v for k, v in self.bilder_skaliert.items() if k[0] != nr}
            bild = _pixmap(quelle) if quelle is not None else None
            if bild is None or bild.isNull():
                self.bilder.pop(nr, None)
            else:
                self.bilder[nr] = (bild, hoehe)
            self.update()

        def _bilder_malen(self, p, zeilen):
            """Echte Bilder über ihre Platzhalter: eingepasst in den ganzen Bildkasten (auch den
            hinausgerollten Teil), gezeichnet nur im sichtbaren. `zeilen`: je Zeile die Zellinhalte – leer für
            Zeilen ohne Platzhalter."""
            if not self.bilder or not any(zeilen):
                return
            dpr = self.devicePixelRatioF()
            for nr, (x0, y0, x1, y1, erste) in platzhalter_kaesten(zeilen).items():
                if nr not in self.bilder:
                    continue
                bild, hoehe = self.bilder[nr]
                sichtbar = QRectF(8 + x0 * self.zb, 8 + y0 * self.zh,
                                  (x1 - x0 + 1) * self.zb, (y1 - y0 + 1) * self.zh)
                ganz = QRectF(sichtbar.x(), sichtbar.y() - erste * self.zh, sichtbar.width(), hoehe * self.zh)
                f = min(ganz.width() / max(1, bild.width()), ganz.height() / max(1, bild.height()))
                b, h = bild.width() * f, bild.height() * f
                schluessel = (nr, int(b * dpr), int(h * dpr))
                klein = self.bilder_skaliert.get(schluessel)
                if klein is None:
                    klein = bild.scaled(schluessel[1], schluessel[2], Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    klein.setDevicePixelRatio(dpr)
                    self.bilder_skaliert[schluessel] = klein
                p.save()
                p.setClipRect(sichtbar)
                p.drawPixmap(QPointF(ganz.x() + (ganz.width() - b) / 2, ganz.y() + (ganz.height() - h) / 2), klein)
                p.restore()

        # -- Raster --------------------------------------------------------------
        def _raster(self):
            spalten = max(20, int((self.width() - 16) // self.zb))
            return spalten - self._figur_spalten(spalten), max(5, int((self.height() - 16) // self.zh))

        def _figur_groesse(self):
            """(Breite, Höhe) der Figur in Bildpunkten – None ohne Figur."""
            if not (self.haupt and self.fenster.figur and self.figur_art):
                return None
            hoehe = figur_hoehe(self.figur_art, self.zh, self.height(), self.figur_sitz,
                                self.figur_justierung["groesse"])
            if self.figur_art == "strich":
                return (hoehe * FIGUR_STEHT_BREITE, hoehe) if (self.strich_akt or self.strich_basis) else None
            bild = self.figur_bilder.get("sitzen")
            if bild is None:
                return None
            return hoehe * bild.width() / max(1, bild.height()), hoehe

        def _figur_spalten(self, spalten):
            """Rechts frei gehaltene Spalten – die stehende Figur braucht keine: sie steht oben auf dem Rahmen der
            Eingabe, über dem Inhalt, und NemiCLI behält die volle Breite."""
            groesse = self._figur_groesse()
            if groesse is None or self.figur_art == "strich":
                return 0
            return figur_reserve(spalten, self.zb, groesse[0])

        def raster_anwenden(self):
            """Raster an Fenstergröße und Figur anpassen; das Programm erfährt die neue Größe."""
            sp, ze = self._raster()
            if self.nur_lesen and self.raster_gesetzt:
                # Reiter „KI“: erst, wenn die Größe kurz steht – der Neuaufbau liest das Protokoll neu ein
                self.raster_warten.start()
                return
            if (sp, ze) != (self.schirm.columns, self.schirm.lines):
                with self.lock:
                    if self.nur_lesen:
                        self.schirm = KiSchirm(sp, ze)
                        self.strom = pyte.Stream(self.schirm)
                        self.strom.feed("".join(protokoll_ende(self.protokoll, KiSchirm.VERLAUF + ze)))
                        self.ki_rest.clear()
                        self.ki_bau = None
                        # neu aufgebaut: Zeilennummern, Rollstand und Treffer gelten nicht mehr
                        self.geschoben_alt = self.schirm.geschoben
                        self.roll = self.roll_ziel = 0.0
                        self.auswahl, self.treffer, self.treffer_nr, self.link = None, [], -1, None
                    else:
                        self.schirm.resize(ze, sp)
                    self._zeilen_vergessen()
                self.alles_neu = True
                if self.pty is not None:
                    self.pty.groesse(sp, ze)
            self.raster_gesetzt = self.raster_gesetzt or self.isVisible()

        def _zelle(self, pos):
            """Zelle unter einem Punkt: (Spalte, bleibende Zeilennummer). Ohne Verlauf ist die Nummer
            die Bildschirmzeile."""
            sp = int((pos.x() - 8) // self.zb)
            ze = int((pos.y() - 8 - self.roll) // self.zh) + self._oben()
            ze = max(0, min(ze, self._oben() + self.schirm.lines - 1)) + self._versatz()
            return max(0, min(sp, self.schirm.columns - 1)), ze

        def resizeEvent(self, ev):
            self.raster_anwenden()
            self.leuchten_bild = None
            super().resizeEvent(ev)

        # -- Zeichnen ------------------------------------------------------------
        def paintEvent(self, ev):
            # Gezeichnet wird nur, was im Bereich des Ereignisses liegt (geänderte Zeilen, Figur …). Leuchten
            # und Figur zeigen dabei denselben Stand wie im Rest der Fläche (puls_stufe, figur_zeit).
            bereich = ev.region()
            p = QPainter(self)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(ev.rect(), self.grund)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            self._leuchten_malen(p)
            dpr = self.devicePixelRatioF()
            breite, zh_px = self.width(), math.ceil(self.zh) + 2

            def y_von(v):                        # auf ganze Bildpunkte: die Zeilenbilder bleiben scharf
                return round((8 + (v - oben) * self.zh + self.roll) * dpr) / dpr

            with self.lock:
                # Unter der Sperre nur lesen, was sich geändert hat; gezeichnet wird danach – der
                # Lese-Thread des Programms wartet so nicht auf das Zeichnen.
                s = self.schirm
                spalten, zeilen = s.columns, s.lines
                oben, versatz = self._oben(), self._versatz()
                if self.bild_stand != (spalten, dpr):
                    self.bild_stand = (spalten, dpr)
                    self.zeilen_bild.clear()
                    self._zeilen_vergessen()
                    s.dirty.update(range(zeilen))
                    self.alles_neu = True            # Teilbild: der Rest folgt im nächsten Takt
                erste = int(math.floor(oben - self.roll / self.zh))
                anfang = max(0, erste)
                sichtbar = []                    # (v, Inhalt oder None, (Bild, Platzhalter) oder None)
                gemalt = set()
                for v in range(anfang, min(oben + zeilen, erste + zeilen + 1)):
                    if not bereich.intersects(QRect(0, math.floor(y_von(v)) - 1, breite, zh_px)):
                        continue
                    y = v - oben
                    if y >= 0:
                        gemerkt = self.zeilen_bild_y.get(y) if y not in s.dirty else None
                        schluessel = self.zeilen_schluessel.get(y) if gemerkt is not None else None
                        if schluessel is None:
                            zeile = self._zeile(v)
                            schluessel = tuple(zeile[x] for x in range(spalten))
                            self.zeilen_schluessel[y] = schluessel
                            gemerkt = None
                            gemalt.add(y)
                    else:                        # Verlauf (Reiter „KI“): Zeilen ändern sich dort nicht mehr
                        gemerkt = self.verlauf_bild.get(v + versatz)
                        schluessel = None
                        if gemerkt is None:
                            zeile = self._zeile(v)
                            schluessel = tuple(zeile[x] for x in range(spalten))
                    sichtbar.append((v, schluessel, gemerkt))
                s.dirty.difference_update(gemalt)
                c = s.cursor
                cursor = None if (self.nur_lesen or c.hidden or not (0 <= c.y < zeilen and 0 <= c.x < spalten)) \
                    else (self.cursor_xy or (c.x, c.y))

            platz_zeilen = {}                    # v -> Inhalt der Zeilen mit Platzhaltern (echte Bilder)
            for v, schluessel, gemerkt in sichtbar:
                if gemerkt is None:
                    gemerkt = (self._zeilenbild(schluessel, dpr), _hat_platzhalter(schluessel))
                    if v >= oben:
                        self.zeilen_bild_y[v - oben] = gemerkt
                    else:
                        if len(self.verlauf_bild) > 4 * zeilen + 200:
                            self.verlauf_bild.clear()
                        self.verlauf_bild[v + versatz] = gemerkt
                bild, platz = gemerkt
                if bild is not None:
                    p.drawPixmap(QPointF(8.0, y_von(v)), bild)
                if platz and v >= oben:
                    platz_zeilen[v] = self.zeilen_schluessel.get(v - oben) or ()
            if self.bilder and platz_zeilen:     # die Zellmatrix nur für Zeilen mit Platzhaltern
                self._bilder_malen(p, [[z.data for z in platz_zeilen[v]] if v in platz_zeilen else []
                                       for v in range(anfang, min(oben + zeilen, erste + zeilen + 1))])
            if self.figur_rect is None or bereich.intersects(self.figur_rect.toAlignedRect()):
                self._figur_malen(p, spalten, zeilen)
            if self.treffer:
                self._treffer_malen(p, y_von, versatz, oben + zeilen)
            if self.auswahl:
                (x1, n1), (x2, n2) = geordnet(*self.auswahl)
                farbe_a = QColor(akzent)
                farbe_a.setAlpha(110)
                for v, _schluessel, _gemerkt in sichtbar:
                    if n1 <= v + versatz <= n2:
                        von = x1 if v + versatz == n1 else 0
                        bis = x2 if v + versatz == n2 else spalten - 1
                        p.fillRect(QRectF(8 + von * self.zb, y_von(v), (bis - von + 1) * self.zb, self.zh), farbe_a)
            if self.link is not None:
                nummer, x0, x1 = self.link[:3]
                y = y_von(nummer - versatz) + self.zh - 1
                p.setPen(QColor(akzent))
                p.drawLine(QPointF(8 + x0 * self.zb, y), QPointF(8 + (x1 + 1) * self.zb, y))
            if cursor is not None:
                r = QRectF(8 + cursor[0] * self.zb, y_von(oben) + cursor[1] * self.zh, self.zb, self.zh)
                if bereich.intersects(r.toAlignedRect()):
                    self.cursor_spur |= {math.floor(cursor[1]), math.ceil(cursor[1])}   # dort später löschen
                farbe_c = QColor(thema["cursor"])
                if self.hasFocus():
                    farbe_c.setAlpha(170)
                    p.fillRect(r, farbe_c)
                else:
                    p.setPen(farbe_c)
                    p.drawRect(r.adjusted(0, 0, -1, -1))
            p.end()
            self.roll_gemalt = self.roll
            if not self.takt.isActive() and (self.figur_rect is not None or self.alles_neu):
                self._antreiben()

        def _leuchten_malen(self, p):
            """Sanftes Leuchten oben im Fenster in den Akzentfarben. Arbeitet das Programm, atmet es.
            Das Leuchten ist ein gemerktes Bild; beim Atmen ändert sich nur seine Deckkraft."""
            if not self.fenster.leuchten:
                return
            groesse = (self.width(), self.height())
            if self.leuchten_bild is None or self.leuchten_bild[0] != groesse:
                b, h = groesse
                bild = QPixmap(max(1, b), max(1, h))
                bild.fill(Qt.transparent)
                maler = QPainter(bild)
                for mitte, ton, deckung in ((0.2, akzent, 46), (0.85, akzent2, 38)):
                    verlauf = QRadialGradient(QPointF(b * mitte, -h * 0.2), max(b, h) * 0.8)
                    innen, aussen = QColor(ton), QColor(ton)
                    innen.setAlpha(deckung)
                    aussen.setAlpha(0)
                    verlauf.setColorAt(0.0, innen)
                    verlauf.setColorAt(1.0, aussen)
                    maler.fillRect(bild.rect(), verlauf)
                maler.end()
                self.leuchten_bild = (groesse, bild)
            # die Stufe setzt der Takt: auch ein Teilbild hat so dieselbe Stärke wie die Fläche daneben
            p.setOpacity(leuchten_staerke(self.puls_stufe))
            p.drawPixmap(0, 0, self.leuchten_bild[1])
            p.setOpacity(1.0)

        def _figur_bild(self, pose, b, h, dpr):
            name = pose if pose in self.figur_bilder else "sitzen"
            schluessel = (name, int(b * dpr), int(h * dpr))
            klein = self.figur_skaliert.get(schluessel)
            if klein is None:
                klein = self.figur_bilder[name].scaled(schluessel[1], schluessel[2], Qt.IgnoreAspectRatio,
                                                       Qt.SmoothTransformation)
                klein.setDevicePixelRatio(dpr)
                self.figur_skaliert[schluessel] = klein
            return klein

        def _figur_malen(self, p, spalten, zeilen):
            """Die Figur rechts in den freien Spalten: Sitzfläche auf der Kopfzeile der Eingabe (Büste: unten
            bündig darauf). Kurzer Übergang zwischen Posen, kleines Zeichen über dem Kopf."""
            groesse = self._figur_groesse()
            if groesse is None or (self.figur_art != "strich" and spalten >= int((self.width() - 16) // self.zb)):
                self.figur_rect = None
                return
            b, h = groesse
            if self.figur_art == "strich" and not figur_reserve(spalten, self.zb, b):
                self.figur_rect = None                   # zu schmal: sie verdeckte zu viel vom Text
                return
            jetzt = self.figur_zeit                      # das Bild, das der Takt gerade zeigt
            pose = figur_pose(self.figur_basis, self.figur_kurz, self.figur_kurz_seit, jetzt, self.figur_taste)
            if pose != self.figur_zeigt:
                self.figur_alt = (self.figur_zeigt, jetzt)
                self.figur_zeigt, self.figur_seit = pose, jetzt
            links = 8 + spalten * self.zb
            mitte_x = links + (self.width() - 8 - links) / 2
            linie = 8 + (zeilen - self.figur_sitz) * self.zh + self.zh / 2
            if self.figur_art == "strich":                # oben auf dem Rahmen der Eingabe, Platz aus dem Justierer
                j = self.figur_justierung
                rechts, ganz_links = self.width() - 8 - b / 2 - self.zb, 8 + b / 2 + self.zb
                mitte_x = ganz_links + (rechts - ganz_links) * j["x"]
                self._strich_malen(p, mitte_x - b / 2, mitte_x, linie - j["y"] * self.zh, b, h, pose, jetzt)
                return
            anker = h * FIGUR_SITZ_ANTEIL if self.figur_art == "figur" else h
            dpr = self.devicePixelRatioF()

            def eine(pose_n, deckung):
                dy, winkel, sx, sy = figur_bewegung(pose_n, jetzt - self.figur_seit)
                p.save()
                p.setOpacity(deckung)
                p.translate(mitte_x, linie + dy * h)
                p.rotate(winkel)
                p.scale(sx, sy)
                p.drawPixmap(QPointF(-b / 2, -anker), self._figur_bild(pose_n, b, h, dpr))
                p.restore()

            kante = QColor(akzent)                 # die Kopfzeile der Eingabe läuft als Kante bis unter sie
            kante.setAlpha(150)
            p.setPen(QPen(kante, 2))
            p.drawLine(QPointF(links, linie), QPointF(mitte_x + b * 0.45, linie))
            if self.figur_alt is not None and jetzt - self.figur_alt[1] < FIGUR_WECHSEL_S:
                anteil = (jetzt - self.figur_alt[1]) / FIGUR_WECHSEL_S
                eine(self.figur_alt[0], 1 - anteil)
                eine(pose, anteil)
            else:
                self.figur_alt = None
                eine(pose, 1.0)
            if (zeichen := FIGUR_ZEICHEN.get(pose)) is not None:
                schwebt = 4 * math.sin(2 * math.pi * (jetzt - self.figur_seit) / 1.8)
                gross = QFont(self.fett)
                gross.setPointSizeF(self.fett.pointSizeF() * 1.6)
                p.setFont(gross)
                p.setPen(QColor(akzent2 if pose == "herz" else akzent))
                p.drawText(QPointF(mitte_x + b * 0.28, linie - anker - self.zh * 0.2 + schwebt), zeichen)
            self.figur_rect = QRectF(links - 4, linie - anker - h * 0.2 - self.zh, self.width() - links + 4,
                                     h * 1.3 + self.zh * 2)

        # -- Strichmännchen ---------------------------------------------------------------------------------
        def _strich_neu(self, bew, jetzt):
            """Eine neue Bewegung: Schleifen ohne Vorrang werden die Grundbewegung; läuft gerade eine der KI
            (Vorrang), wartet die Grundbewegung, und kurze Status-Bewegungen fallen weg."""
            akt = self.strich_akt
            laeuft_vorrang = akt is not None and akt["vorrang"] and not strich_bild(akt, jetzt - self.strich_start)[1]
            if bew["schleife"] and not bew["vorrang"]:
                self.strich_basis = bew
                if laeuft_vorrang:
                    return
            elif laeuft_vorrang and not bew["vorrang"]:
                return
            self._strich_wechsel(bew, jetzt)

        def _strich_wechsel(self, bew, jetzt):
            gelenke = self._strich_gelenke(jetzt) if self.strich_akt is not None else None
            self.strich_von = (gelenke, jetzt) if gelenke is not None else None
            self.strich_akt, self.strich_start = bew, jetzt

        def _strich_gelenke(self, jetzt):
            """Die Gelenke dieses Augenblicks; eine fertige kurze Bewegung geht in die Grundbewegung über."""
            akt = self.strich_akt
            if akt is None:
                if self.strich_basis is None:
                    return None
                self.strich_akt, self.strich_start, akt = self.strich_basis, jetzt, self.strich_basis
            gelenke, fertig = strich_zwischen(akt, jetzt - self.strich_start)
            if fertig and self.strich_basis is not None and akt is not self.strich_basis:
                self.strich_von = (akt["bilder"][-1], jetzt)
                self.strich_akt, self.strich_start, akt = self.strich_basis, jetzt, self.strich_basis
                gelenke = akt["bilder"][0]
            if self.strich_von is not None:
                anteil = (jetzt - self.strich_von[1]) / STRICH_WECHSEL_S
                if anteil < 1:
                    return strich_mischen(self.strich_von[0], gelenke, strich_sanft(anteil))
                self.strich_von = None
            return gelenke

        def _rig_gesicht(self, rig, i, name, stufe):
            """Der Kopf mit Ausdruck `name`, verkleinert wie die übrigen Stücke (einmal je Größe gemerkt)."""
            klein = rig.setdefault("klein_gesicht", {})
            schluessel = (i, name, stufe)
            if schluessel not in klein:
                voll = rig["gesicht_pixmaps"][i].get(name)
                if voll is None or voll.isNull():
                    return None
                if len(klein) > 64:
                    klein.clear()
                klein[schluessel] = voll.scaled(max(1, round(voll.width() * stufe)), max(1, round(voll.height() * stufe)),
                                                Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            return klein[schluessel]

        def _strich_gesicht(self, name, durchmesser, dpr):
            """Ein Gesicht rund ausgeschnitten: aus dem Kugel-Bild der obere, mittlere Teil (Kopf und Schultern)."""
            bild = self.figur_bilder.get(name) or self.figur_bilder.get("neutral")
            if bild is None:
                return None
            schluessel = (name if name in self.figur_bilder else "neutral", int(durchmesser * dpr))
            fertig = self.strich_gesichter.get(schluessel)
            if fertig is None:
                seite = schluessel[1]
                fertig = QPixmap(max(1, seite), max(1, seite))
                fertig.fill(Qt.transparent)
                q = QPainter(fertig)
                q.setRenderHint(QPainter.Antialiasing)
                q.setRenderHint(QPainter.SmoothPixmapTransform)
                rund = QPainterPath()
                rund.addEllipse(QRectF(0, 0, seite, seite))
                q.setClipPath(rund)
                w, hh = bild.width(), bild.height()
                q.drawPixmap(QRectF(0, 0, seite, seite), bild, QRectF(w * 0.22, hh * 0.06, w * 0.56, w * 0.56))
                q.end()
                fertig.setDevicePixelRatio(dpr)
                self.strich_gesichter[schluessel] = fertig
            return fertig

        def _strich_malen(self, p, links, mitte_x, linie, b, h, pose, jetzt):
            """Körper aus den 22 Gelenken (leicht schräg, hintere Knochen dunkler), Kopf mit dem Gesicht der
            Persönlichkeit – von hinten ohne Gesicht. Steht auf der Kopfzeile der Eingabe. Gelenke, Knochen und
            Hülle werden je Figur-Bild einmal gerechnet; Teilbilder zeichnen sie nur noch."""
            dpr = self.devicePixelRatioF()
            schluessel = (jetzt, b, h, mitte_x, linie, self.width(), dpr, id(self.strich_rig),
                          tuple(sorted(self.figur_justierung.items())))
            if self.figur_merk is None or self.figur_merk[0] != schluessel:
                self.figur_merk = (schluessel, self._strich_rechnen(mitte_x, linie, h, jetzt))
            d = self.figur_merk[1]
            if d is None:
                self.figur_rect = None
                return
            self.figur_rect, self.figur_koerper = d["rect"], d["koerper"]
            punkte, gelenke, namen, eltern = d["punkte"], d["gelenke"], d["namen"], d["eltern"]
            p.save()
            p.setClipRect(self.figur_rect)
            p.setRenderHint(QPainter.Antialiasing)
            if d["rig"] is not None:
                self._rig_zeichnen(p, d["rig"], h, pose, jetzt)
                return
            dicke = max(2.0, h * 0.024)
            knochen = sorted(((j, e) for j, e in enumerate(eltern) if e >= 0),
                             key=lambda je: punkte[je[0]][2] + punkte[je[1]][2])
            for j, e in knochen:
                farbe = QColor(akzent2)
                if punkte[j][2] + punkte[e][2] < 0:          # hinten: etwas dunkler
                    farbe = farbe.darker(150)
                stift = QPen(farbe, dicke)
                stift.setCapStyle(Qt.RoundCap)
                p.setPen(stift)
                p.drawLine(QPointF(punkte[e][0], punkte[e][1]), QPointF(punkte[j][0], punkte[j][1]))
            try:
                kopf, hals = punkte[namen.index("head")], punkte[namen.index("neck")]
            except ValueError:
                kopf, hals = punkte[-1], punkte[-1]
            radius = max(6.0, h * 0.085)
            dx, dy = kopf[0] - hals[0], kopf[1] - hals[1]
            laenge = math.hypot(dx, dy) or 1.0
            mitte = QPointF(kopf[0] + dx / laenge * radius * 0.55, kopf[1] + dy / laenge * radius * 0.55)
            kreis = QRectF(mitte.x() - radius, mitte.y() - radius, 2 * radius, 2 * radius)
            gesicht = self._strich_gesicht(d["gesicht"], 2 * radius, dpr) if strich_von_vorn(gelenke, namen) else None
            p.setPen(QPen(QColor(akzent), max(1.5, dicke * 0.6)))
            if gesicht is not None:
                p.drawPixmap(kreis.topLeft(), gesicht)
                p.setBrush(Qt.NoBrush)
            else:                                            # von hinten (oder ohne Bilder): Hinterkopf
                p.setBrush(QColor(akzent2).darker(170))
            p.drawEllipse(kreis)
            p.setBrush(Qt.NoBrush)
            if (zeichen := FIGUR_ZEICHEN.get(pose)) is not None:
                schwebt = 4 * math.sin(2 * math.pi * (jetzt - self.figur_seit) / 1.8)
                gross = QFont(self.fett)
                gross.setPointSizeF(self.fett.pointSizeF() * 1.6)
                p.setFont(gross)
                p.setPen(QColor(akzent2 if pose == "herz" else akzent))
                p.drawText(QPointF(mitte.x() + radius * 1.1, mitte.y() - radius + schwebt), zeichen)
            p.restore()

        def _strich_rechnen(self, mitte_x, linie, h, jetzt):
            """Gelenke im Fenster, Bereich, Körper und (mit Hülle) der Plan der Stücke für ein Figur-Bild."""
            gelenke = self._strich_gelenke(jetzt)
            akt = self.strich_akt
            if gelenke is None or akt is None:
                return None
            namen, eltern = akt["namen"], akt["eltern"]
            mass = h / STRICH_GROESSE_M
            punkte = [(mitte_x + x * mass, linie - y * mass, z) for x, y, z in strich_projektion(gelenke)]
            punkte = figurmass.strecken(punkte, eltern, namen, self.figur_justierung)
            # fester Bereich für das Neuzeichnen im Takt (Arme, Sprünge): keine Reste der letzten Pose über dem Text
            oben = max(0.0, linie - h * 1.8 - self.zh)
            frei_links = max(0.0, mitte_x - h)
            rect = QRectF(frei_links, oben, min(self.width(), mitte_x + h) - frei_links, linie - oben + self.zh * 2)
            xs, ys = [q[0] for q in punkte], [q[1] for q in punkte]
            koerper = QRectF(min(xs) - h * 0.08, min(ys) - h * 0.2, max(xs) - min(xs) + h * 0.16,
                             max(ys) - min(ys) + h * 0.25)
            plan = self._rig_plan(punkte, gelenke, namen, h) if self.strich_rig else None
            return {"rect": rect, "koerper": plan["koerper"] if plan is not None else koerper, "punkte": punkte,
                    "gelenke": gelenke, "namen": namen, "eltern": eltern, "gesicht": akt["gesicht"], "rig": plan}

        def _rig_plan(self, punkte, gelenke, namen, h):
            """Ihr echtes Bild als Hülle um das Skelett (vorn oder hinten je nach Blick): jedes Stück – Röhre oder
            Kappe – liegt starr auf seinem Knochen, hintere zuerst. Gerechnet wird hier, gezeichnet in _rig_zeichnen.
            None, wenn die Bewegung nicht passt (dann zeichnet der Aufrufer Striche)."""
            blick = strich_blick(gelenke, namen)
            ansicht = "vorne" if blick >= 0 or "hinten" not in self.strich_rig else "hinten"
            rig = self.strich_rig[ansicht]
            mass = h / rig["hoehe"]
            ziel = {n: punkte[i] for i, n in enumerate(namen)}
            abbildungen = rig_knochen(rig, ziel, mass, blick, self.figur_justierung)
            if abbildungen is None:
                return None
            tiefen = rig_tiefen(rig, ziel)
            # jedes Stück nur einmal je Größe verkleinern (scharf statt flimmernd), in Stufen von 5 %
            dpr = self.devicePixelRatioF()
            stufe = min(1.0, max(0.05, round(mass * dpr * 20) / 20))
            bilder = rig["klein"].get(stufe)
            if bilder is None:
                bilder = [voll.scaled(max(1, round(voll.width() * stufe)), max(1, round(voll.height() * stufe)),
                                      Qt.IgnoreAspectRatio, Qt.SmoothTransformation) for voll in rig["pixmaps"]]
                rig["klein"] = {stufe: bilder}
            ecken, stuecke = [], []
            gesicht = (self.strich_akt or {}).get("gesicht", "neutral")
            for i in rig_reihe(rig, tiefen):
                s, bild = rig["stuecke"][i], bilder[i]
                if gesicht in s["gesichter"]:             # der Kopf mit dem gewünschten Ausdruck
                    bild = self._rig_gesicht(rig, i, gesicht, stufe) or bild
                x0, y0 = s["ursprung"]
                if s["netz"] is not None:                 # Röhre: starr in der Mitte, weich an den Gelenken
                    quelle, ziel_n = s["netz"]["punkte"], rig_netz(s["netz"], abbildungen)
                    ecken += [QPointF(x, y) for x, y in ziel_n]
                    textur = QTransform.fromScale(1 / stufe, 1 / stufe) * QTransform.fromTranslate(x0, y0)
                    dreiecke = []
                    for a, b, c in s["netz"]["dreiecke"]:
                        z = (ziel_n[a], ziel_n[b], ziel_n[c])
                        abb_d = dreieck_abbildung((quelle[a], quelle[b], quelle[c]), z)
                        if abb_d is None:
                            continue
                        dreiecke.append((textur * QTransform(*abb_d),
                                         QPolygonF([QPointF(x, y) for x, y in dreieck_weiter(z)])))
                    stuecke.append(("netz", bild, dreiecke))
                    continue
                abb = QTransform(*abbildungen[s["knochen"]])
                w, hh = bild.width() / stufe, bild.height() / stufe
                ecken += [abb.map(QPointF(x0 + dx, y0 + dy)) for dx, dy in ((0, 0), (w, 0), (0, hh), (w, hh))]
                stuecke.append(("starr", bild, abb, x0, y0))
            xs, ys = [q.x() for q in ecken], [q.y() for q in ecken]
            return {"stuecke": stuecke, "stufe": stufe, "kopf": ziel.get("head", punkte[-1]),
                    "koerper": QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))}

        def _rig_zeichnen(self, p, plan, h, pose, jetzt):
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            stufe = plan["stufe"]
            for stueck in plan["stuecke"]:
                if stueck[0] == "netz":
                    _art, bild, dreiecke = stueck
                    pinsel = QBrush(bild)
                    p.setPen(Qt.NoPen)
                    p.setRenderHint(QPainter.Antialiasing, False)
                    for transform, dreieck in dreiecke:
                        pinsel.setTransform(transform)
                        p.setBrush(pinsel)
                        p.drawPolygon(dreieck)
                    p.setBrush(Qt.NoBrush)
                    continue
                _art, bild, abb, x0, y0 = stueck
                p.save()
                p.setTransform(abb, True)
                p.translate(x0, y0)
                p.scale(1 / stufe, 1 / stufe)
                p.drawPixmap(0, 0, bild)
                p.restore()
            p.setRenderHint(QPainter.Antialiasing)
            if (zeichen := FIGUR_ZEICHEN.get(pose)) is not None:
                kopf = plan["kopf"]
                schwebt = 4 * math.sin(2 * math.pi * (jetzt - self.figur_seit) / 1.8)
                gross = QFont(self.fett)
                gross.setPointSizeF(self.fett.pointSizeF() * 1.6)
                p.setFont(gross)
                p.setPen(QColor(akzent2 if pose == "herz" else akzent))
                p.drawText(QPointF(kopf[0] + h * 0.1, kopf[1] - h * 0.12 + schwebt), zeichen)
            p.restore()

        # -- Figur-Justierer -----------------------------------------------------
        def _justierung_setzen(self, werte):
            alt = self.figur_justierung
            self.figur_justierung = figurmass.sauber(werte)
            if alt["groesse"] != self.figur_justierung["groesse"]:
                self.raster_anwenden()
            if self.figur_rect is not None:                 # auch die alte Stelle neu malen
                self.update(self.figur_rect.toAlignedRect())
            self.update()

        def _justierer_zeigen(self):
            """Regler für Größe, Längen und Platz der Figur; sie bewegt sich dabei live mit. Fertig legt die Werte
            in justierung.json ab (das Programm hat den Ort gemeldet)."""
            if self.justierer is not None:
                self.justierer.raise_()
                return
            feld = QFrame(self)
            feld.setObjectName("justierer")
            grund = QColor(thema["hintergrund"]).lighter(130)
            feld.setStyleSheet(
                f"QFrame#justierer {{ background: {grund.name()}; border: 1px solid {akzent}; border-radius: 8px; }}"
                f"QLabel {{ color: {thema['vordergrund']}; background: transparent; }}"
                f"QToolButton {{ color: {thema['vordergrund']}; background: transparent; border: 1px solid {akzent};"
                f" border-radius: 4px; padding: 3px 10px; }}"
                f"QToolButton:hover {{ background: {akzent2}; }}")
            gitter = QGridLayout(feld)
            gitter.setContentsMargins(14, 10, 14, 10)
            kopf = QLabel("🎚️ Figur anpassen")
            kopf.setFont(self.fett)
            gitter.addWidget(kopf, 0, 0, 1, 3)
            zeilen = [("groesse", "Größe", 1, "{} Zeilen"), ("kopf", "Kopf", 100, "{} %"), ("rumpf", "Rumpf", 100, "{} %"),
                      ("arme", "Arme", 100, "{} %"), ("beine", "Beine", 100, "{} %"), ("fuesse", "Füße", 100, "{} %"),
                      ("x", "Links ↔ Rechts", 100, "{} %"), ("y", "Runter ↕ Hoch", 1, "{} Zeilen")]
            regler = {}
            for nr, (k, wort, mal, form) in enumerate(zeilen, 1):
                unten, oben = figurmass.GRENZEN[k]
                r = QSlider(Qt.Horizontal)
                r.setRange(round(unten * mal), round(oben * mal))
                r.setValue(round(self.figur_justierung[k] * mal))
                r.setMinimumWidth(170)
                wert = QLabel(form.format(r.value()))
                wert.setMinimumWidth(80)

                def geaendert(v, k=k, mal=mal, form=form, wert=wert):
                    wert.setText(form.format(v))
                    self._justierung_setzen(dict(self.figur_justierung, **{k: v / mal}))
                r.valueChanged.connect(geaendert)
                gitter.addWidget(QLabel(wort), nr, 0)
                gitter.addWidget(r, nr, 1)
                gitter.addWidget(wert, nr, 2)
                regler[k] = (r, mal)
            knoepfe = QHBoxLayout()

            def standard():
                for k, (r, mal) in regler.items():
                    r.setValue(round(figurmass.VORGABE[k] * mal))

            def fertig():
                if self.justier_datei is not None:
                    try:
                        figurmass.schreiben(self.justier_datei, self.figur_justierung)
                    except Exception:
                        pass
                self.justierer = None
                feld.deleteLater()
                self.setFocus()
            for text, tun in (("Standard", standard), ("Fertig", fertig)):
                knopf = QToolButton()
                knopf.setText(text)
                knopf.clicked.connect(tun)
                knoepfe.addWidget(knopf)
            gitter.addLayout(knoepfe, len(zeilen) + 1, 0, 1, 3)
            feld.adjustSize()
            feld.move(16, max(8, (self.height() - feld.height()) // 3))
            feld.show()
            self.justierer = feld

        def _glanz(self) -> float:
            """0 … 1 … 0 während GLANZ_S nach dem Ende einer Arbeit, sonst 0."""
            return glanz_staerke(time.monotonic() - self.glanz_seit) if self.glanz_seit else 0.0

        def _treffer_malen(self, p, y_von, versatz, gesamt):
            """Suchtreffer hinterlegen (der aktuelle kräftiger) und als Marken am rechten Rand zeigen."""
            blass, kraeftig = QColor(akzent2), QColor(akzent2)
            blass.setAlpha(90)
            kraeftig.setAlpha(190)
            hoehe = self.height()
            for i, (nummer, x0, x1) in enumerate(self.treffer):
                y = y_von(nummer - versatz)
                if -self.zh < y < hoehe:
                    p.fillRect(QRectF(8 + x0 * self.zb, y, (x1 - x0 + 1) * self.zb, self.zh),
                               kraeftig if i == self.treffer_nr else blass)
            schritt = max(1, len(self.treffer) // TREFFER_MARKEN)
            for i in range(0, len(self.treffer), schritt):
                lage = (self.treffer[i][0] - versatz) / max(1, gesamt)
                p.fillRect(QRectF(self.width() - 5, 8 + lage * (hoehe - 16), 4, 2), kraeftig)

        def _zeilenbild(self, schluessel, dpr):
            """Bild einer Zeile, gemerkt unter ihrem Inhalt (None: leere Zeile)."""
            merk = self.zeilen_bild
            if schluessel in merk:
                bild = merk.pop(schluessel)                 # wieder ans Ende: zuletzt gebraucht
                merk[schluessel] = bild
                return bild
            bild = None
            if any(z.data.strip() or z.reverse or z.underscore or not self._grund_frei(z.bg) for z in schluessel):
                breite = max(1, math.ceil(len(schluessel) * self.zb * dpr))
                hoehe = max(1, math.ceil(self.zh * dpr))
                bild = QPixmap(breite, hoehe)
                bild.setDevicePixelRatio(dpr)
                # Durchsichtig nur, wenn dahinter etwas zu sehen ist; deckend bleibt die Schrift
                # mit Farbrand-Glättung (ClearType) so scharf wie beim direkten Zeichnen.
                frei = self.fenster.durchsichtig or self.fenster.leuchten
                bild.fill(Qt.transparent if frei else self.grund)
                maler = QPainter(bild)
                x, spalten = 0, len(schluessel)
                while x < spalten:
                    z = schluessel[x]
                    stil = (z.fg, z.bg, z.bold, z.italics, z.underscore, z.reverse)
                    start, zellen = x, [z.data]
                    x += 1
                    while x < spalten:
                        n = schluessel[x]
                        if (n.fg, n.bg, n.bold, n.italics, n.underscore, n.reverse) != stil:
                            break
                        zellen.append(n.data)
                        x += 1
                    self._lauf(maler, start, x - start, zellen, stil)
                maler.end()
                hoechstens = max(self.schirm.lines * 2, ZEILEN_SPEICHER // max(1, breite * hoehe * 4))
                while len(merk) >= hoechstens:
                    merk.pop(next(iter(merk)))
            merk[schluessel] = bild
            return bild

        def _grund_frei(self, bg: str) -> bool:
            """Hintergrund einer Zelle, der nicht gefüllt wird: die Grundfarbe des Fensters oder des Programms."""
            return bg == "default" or farbe(bg, thema, False) in (thema["hintergrund"], self.grund_programm)

        def _lauf(self, p, x, breite, zellen, stil):
            fg, bg, fett, kursiv, unter, umgekehrt = stil
            vorn, hinten = farbe(fg, thema, True), farbe(bg, thema, False)
            if umgekehrt:
                vorn, hinten = hinten, vorn
            r = QRectF(x * self.zb, 0, breite * self.zb, self.zh)
            if hinten != thema["hintergrund"] and hinten != self.grund_programm:
                p.fillRect(r, QColor(hinten))
            if any(ch.strip() for ch in zellen):
                f = self.fett if fett else self.schrift
                if kursiv:
                    f = QFont(f)
                    f.setItalic(True)
                p.setFont(f)
                p.setPen(QColor(vorn))
                # Jede Zelle an ihre Rasterposition. Breite Zeichen (Emojis) belegen zwei
                # Zellen, die zweite ist leer – sie zählt trotzdem mit, sonst rutscht der Rest.
                for i, ch in enumerate(zellen):
                    if ch and ch != " " and not ist_platzhalter(ch):
                        p.drawText(r.left() + i * self.zb, r.top() + self.oben, ch)
                if unter:
                    p.drawLine(r.left(), r.bottom() - 1, r.right(), r.bottom() - 1)

        # -- Tastatur ------------------------------------------------------------
        _QT_NAMEN = {Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
                     Qt.Key_Home: "home", Qt.Key_End: "end", Qt.Key_PageUp: "pageup",
                     Qt.Key_PageDown: "pagedown", Qt.Key_Insert: "insert", Qt.Key_Delete: "delete",
                     Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Backspace: "backspace",
                     Qt.Key_Tab: "tab", Qt.Key_Backtab: "tab", Qt.Key_Escape: "escape",
                     **{getattr(Qt, f"Key_F{i}"): f"f{i}" for i in range(1, 13)}}

        def event(self, ev):
            # Tab und Shift+Tab gehören dem Programm, nicht dem Fokuswechsel von Qt.
            from PySide6.QtCore import QEvent
            if ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Tab, Qt.Key_Backtab):
                self.keyPressEvent(ev)
                return True
            return super().event(ev)

        def keyPressEvent(self, ev):
            self.figur_taste = time.monotonic()           # die Figur döst nicht, solange getippt wird
            mods = ev.modifiers()
            shift = bool(mods & Qt.ShiftModifier) or ev.key() == Qt.Key_Backtab
            strg = bool(mods & Qt.ControlModifier)
            alt = bool(mods & Qt.AltModifier)
            if strg and not alt and ev.key() == Qt.Key_V or (shift and ev.key() == Qt.Key_Insert):
                self._einfuegen()
                return
            if strg and not alt and ev.key() == Qt.Key_C:
                # Wie unter Windows gewohnt: Strg+C kopiert und beendet nie – als Signal
                # durchgereicht würde es NemiCLI sofort schließen. Abbrechen geht mit Esc.
                self._kopieren()
                self.auswahl = None
                self.update()
                return
            if strg and not alt and not shift and ev.key() == Qt.Key_F and not self.haupt:
                self.fenster.suche_oeffnen(self)         # im Chat sucht NemiCLI selbst (Strg+F geht durch)
                return
            if self.auswahl:
                self.auswahl = None
                self.update()
            if self.nur_lesen:
                seite = (self.schirm.lines - 2) * self.zh
                if ev.key() in (Qt.Key_PageUp, Qt.Key_PageDown):
                    self.rollen(seite if ev.key() == Qt.Key_PageUp else -seite)
                elif ev.key() in (Qt.Key_Up, Qt.Key_Down):
                    self.rollen(self.zh if ev.key() == Qt.Key_Up else -self.zh)
                elif ev.key() in (Qt.Key_Home, Qt.Key_End):
                    self.roll_ziel = self._oben() * self.zh if ev.key() == Qt.Key_Home else 0.0
                    self._antreiben()
                return
            name = self._QT_NAMEN.get(ev.key(), "")
            text = ev.text()
            if strg and not text and Qt.Key_A <= ev.key() <= Qt.Key_Z:
                text = chr(ev.key()).lower()
            folge = taste_zu_folge(name, text, shift=shift, strg=strg, alt=alt,
                                   anwendungs_cursor=self.schirm.anwendungs_cursor())
            self._senden(folge)

        def keyReleaseEvent(self, ev):
            if ev.key() == Qt.Key_Control:
                self._link_setzen(None)
            super().keyReleaseEvent(ev)

        def _kopieren(self):
            if not self.auswahl:
                return
            (x1, n1), (x2, n2) = geordnet(*self.auswahl)
            with self.lock:
                zeilen = [self._zellen(n) or [] for n in range(n1, n2 + 1)]
            text = auswahl_text(zeilen, (x1, 0), (x2, n2 - n1))
            if text:
                QGuiApplication.clipboard().setText(text)

        # -- Suche -----------------------------------------------------------------
        def suchen(self, begriff: str) -> int:
            """Sucht im Verlauf und auf dem Bildschirm, beginnt beim neuesten Treffer. Gibt die Zahl zurück."""
            with self.lock:
                versatz = self._versatz()
                zeilen = [(v + versatz, self._zellen(v + versatz)) for v in range(self._oben() + self.schirm.lines)]
            self.such_begriff = begriff
            self.treffer = terminal_text.suche(zeilen, begriff, ist_platzhalter)
            self.treffer_nr = len(self.treffer) - 1
            self._treffer_zeigen()
            return len(self.treffer)

        def such_sprung(self, schritt: int) -> None:
            self.treffer_nr = terminal_text.naechster(self.treffer, self.treffer_nr, schritt)
            self._treffer_zeigen()

        def suche_leeren(self) -> None:
            self.such_begriff, self.treffer, self.treffer_nr = "", [], -1
            self.update()

        def _treffer_zeigen(self) -> None:
            """Den aktuellen Treffer ins Bild rollen (Reiter „KI“), falls er außerhalb liegt."""
            if self.nur_lesen and 0 <= self.treffer_nr < len(self.treffer):
                with self.lock:
                    lage = (self.treffer[self.treffer_nr][0] - self._versatz() - self._oben()) * self.zh
                y = 8 + lage + self.roll_ziel
                if not self.zh <= y <= self.height() - 2 * self.zh:
                    self.roll_ziel = self.height() / 2 - lage          # der Takt begrenzt auf den Verlauf
                    self._antreiben()
            self.update()

        # -- Links (Strg+Klick) ------------------------------------------------------
        def _link_bei(self, pos):
            """Link unter einem Punkt: (Zeilennummer, erste Zelle, letzte Zelle, Art, Ziel) oder None."""
            spalte, nummer = self._zelle(pos)
            with self.lock:
                zellen = self._zellen(nummer)
            if zellen is None:
                return None
            if self.link_merk[0] != zellen:             # je Zeile nur einmal auf der Platte nachsehen
                self.link_merk = (zellen, terminal_text.finde_links(zellen, platzhalter=ist_platzhalter))
            for x0, x1, art, ziel in self.link_merk[1]:
                if x0 <= spalte <= x1:
                    return nummer, x0, x1, art, ziel
            return None

        def _link_setzen(self, link, ev=None) -> None:
            if link == self.link:
                return
            self.link = link
            if link is None:
                self.unsetCursor()
                QToolTip.hideText()
            else:
                self.setCursor(Qt.PointingHandCursor)
                if ev is not None:                      # das Ziel zeigen, bevor geklickt wird
                    QToolTip.showText(ev.globalPosition().toPoint(), link[4], self)
            self.update()

        def _einfuegen(self):
            """Strg+V: kopierte Dateien als Pfade, sonst Text, sonst ein Bild (als PNG abgelegt, Pfad eingefügt)."""
            mime = QGuiApplication.clipboard().mimeData()
            if mime is None:
                return
            text = self._abgelegt(mime)
            if mime.hasImage() and (not text.strip() or nur_link(text)):
                text = self._bild_ablegen(mime.imageData()) or text
            self._text_einfuegen(text)

        def _bild_ablegen(self, bild) -> str:
            if bild is None or not isinstance(bild, QImage):
                bild = QImage(bild) if isinstance(bild, QPixmap) else None
            if bild is None or bild.isNull():
                return ""
            ordner = einfuege_ordner()
            try:
                ordner.mkdir(parents=True, exist_ok=True)
                einfuegungen_aufraeumen(ordner)
            except OSError:
                return ""
            pfad = ordner / f"bild_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}.png"
            return abgelegt_zu_text([str(pfad)]) if bild.save(str(pfad), "PNG") else ""

        def _text_einfuegen(self, text):
            if not text or self.nur_lesen:
                return
            # Steuerzeichen aus der Zwischenablage weg (Tab und Zeilenende bleiben): „ESC[201~“ beendete sonst
            # den Einfüge-Modus vorzeitig, und der Rest liefe als getippte Eingabe samt Enter los.
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
            text = text.replace("\r\n", "\r").replace("\n", "\r")
            if self.schirm.einfuegen_geklammert():
                text = "\x1b[200~" + text + "\x1b[201~"
            self._senden(text)

        # -- Eingabemethode (Emoji-Feld, IME) ---------------------------------------
        def inputMethodEvent(self, ev):
            if ev.commitString() and not self.nur_lesen:
                if self.auswahl:
                    self.auswahl = None
                    self.update()
                self._senden(ev.commitString())
            ev.accept()

        def inputMethodQuery(self, frage):
            if frage == Qt.ImCursorRectangle:            # Emoji-Feld erscheint am Cursor
                c = self.schirm.cursor
                return QRect(int(8 + c.x * self.zb), int(8 + c.y * self.zh), int(self.zb), int(self.zh))
            if frage == Qt.ImEnabled:
                return not self.nur_lesen
            return super().inputMethodQuery(frage)

        # -- Ablegen (Drag & Drop) ---------------------------------------------------
        def _abgelegt(self, mime):
            dateien = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
            if dateien:
                return abgelegt_zu_text([os.path.normpath(d) for d in dateien])
            return mime.text() if mime.hasText() else ""

        def dragEnterEvent(self, ev):
            if self._abgelegt(ev.mimeData()):
                ev.acceptProposedAction()

        def dragMoveEvent(self, ev):
            ev.acceptProposedAction()

        def dropEvent(self, ev):
            text = self._abgelegt(ev.mimeData())
            if text:
                ev.acceptProposedAction()
                self._text_einfuegen(text)
                self.window().activateWindow()
                self.setFocus()

        def focusInEvent(self, ev):
            if self.schirm.fokus_melden():
                self._senden("\x1b[I")
            self.update()
            super().focusInEvent(ev)

        def focusOutEvent(self, ev):
            if self.schirm.fokus_melden():
                self._senden("\x1b[O")
            self.update()
            super().focusOutEvent(ev)

        # -- Maus ----------------------------------------------------------------
        _KNOPF = {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}

        @staticmethod
        def _tasten(ev):
            m = ev.modifiers()
            return (bool(m & Qt.ShiftModifier), bool(m & Qt.AltModifier), bool(m & Qt.ControlModifier))

        def _maus_an(self, knopf, zelle, tasten, **kw):
            if not self.schirm.maus_an():
                return
            shift, alt, strg = tasten
            self._senden(maus_folge(knopf, zelle[0], zelle[1], shift=shift, alt=alt, strg=strg,
                                    sgr=self.schirm.maus_sgr(), **kw))

        def _maus(self, ev, knopf, **kw):
            self._maus_an(knopf, self._zelle(ev.position()), self._tasten(ev), **kw)

        def _programm_markiert(self, ev) -> bool:
            """Das Programm markiert selbst (Maus mit Bewegung angefordert, ohne Umschalt)."""
            return self.schirm.maus_an() and self.schirm.maus_bewegung() and not self._tasten(ev)[0]

        def mousePressEvent(self, ev):
            k = self._KNOPF.get(ev.button())
            if (k == 2 and self.figur_art == "strich" and self.figur_rect is not None
                    and self.figur_koerper is not None and self.figur_koerper.contains(ev.position())):
                menue = QMenu(self)                        # Rechtsklick auf die Figur: Justierer statt Programm
                menue.addAction("🎚️ Figur anpassen …", self._justierer_zeigen)
                menue.exec(ev.globalPosition().toPoint())
                return
            shift, _alt, strg = self._tasten(ev)
            if k == 0 and strg and not shift:
                link = self._link_bei(ev.position())
                if link is not None:                    # Strg+Klick auf einen Link: öffnen, nichts ans Programm
                    self.link_klick = True
                    terminal_text.oeffnen(link[3], link[4])
                    return
            if k == 0 and self._programm_markiert(ev):
                if self.auswahl:
                    self.auswahl = None
                    self.update()
                self.druck = None
                self.maus_knopf = 0
                self._maus(ev, 0)
                return
            if k == 0:
                # Noch nicht weitergeben: erst beim Loslassen steht fest, ob es ein Klick
                # oder eine Markierung war.
                self.druck = (self._zelle(ev.position()), self._tasten(ev))
                self.zieht = False
                if self.auswahl:
                    self.auswahl = None
                    self.update()
                return
            if k is not None:
                self.maus_knopf = k
                self._maus(ev, k)

        def mouseReleaseEvent(self, ev):
            k = self._KNOPF.get(ev.button())
            if k == 0 and self.link_klick:
                self.link_klick = False
                return
            if k == 0 and self.druck is not None:
                zelle, tasten = self.druck
                self.druck = None
                if self.zieht:
                    self.zieht = False
                    self._kopieren()             # wie unter Linux: markiert = kopiert
                else:
                    self._maus_an(0, zelle, tasten)
                    self._maus_an(0, self._zelle(ev.position()), self._tasten(ev), losgelassen=True)
                return
            if k is not None:
                self._maus(ev, k, losgelassen=True)
                self.maus_knopf = None

        def mouseMoveEvent(self, ev):
            shift, _alt, strg = self._tasten(ev)
            if strg and not shift and not ev.buttons():
                self._link_setzen(self._link_bei(ev.position()), ev)
            elif self.link is not None:
                self._link_setzen(None)
            if self.druck is not None and ev.buttons() & Qt.LeftButton:
                zelle = self._zelle(ev.position())
                if self.zieht or zelle != self.druck[0]:
                    self.zieht = True
                    self.auswahl = (self.druck[0], zelle)
                    self.update()
                return
            if self.schirm.maus_bewegung():
                # wie xterm: eine Bewegung nur beim Wechsel der Zelle (oder von Knopf/Umschalttasten)
                knopf = self.maus_knopf if self.maus_knopf is not None else 3
                zelle, tasten = self._zelle(ev.position()), self._tasten(ev)
                if maus_gewechselt(self.maus_zuletzt, zelle, knopf, tasten):
                    self.maus_zuletzt = (zelle, knopf, tasten)
                    self._maus_an(knopf, zelle, tasten, bewegung=True)

        def wheelEvent(self, ev):
            if self.nur_lesen:
                punkte = ev.pixelDelta().y()            # Tastfeld: Bildpunkte, Mausrad: Rasten zu je 120
                self.rollen(punkte if punkte else ev.angleDelta().y() / 120 * ROLL_ZEILEN * self.zh)
            elif self.schirm.maus_an():
                self._maus(ev, 64 if ev.angleDelta().y() > 0 else 65)
            else:
                self._senden(taste_zu_folge("up" if ev.angleDelta().y() > 0 else "down"))

    class Fenster(QWidget):
        """Das Fenster: Reiterleiste (ab zwei Reitern sichtbar), Durchsicht, Tray, Tastenkürzel."""

        def __init__(self):
            super().__init__()
            self.setWindowTitle(titel)
            self.durchsichtig = False
            if e["unschaerfe"] and e["hintergrund"] < 1.0:
                self.setAttribute(Qt.WA_TranslucentBackground)
                self.durchsichtig = acrylic(int(self.winId()))
                if not self.durchsichtig:
                    self.setAttribute(Qt.WA_TranslucentBackground, False)
            schrift = QFont(e["schrift"]) if e["schrift"] else QFontDatabase.systemFont(QFontDatabase.FixedFont)
            if not e["schrift"]:
                for name in ("Cascadia Mono", "Cascadia Code", "Consolas"):
                    if name in QFontDatabase.families():
                        schrift = QFont(name)
                        break
            schrift.setPointSize(e["groesse"])
            schrift.setStyleHint(QFont.Monospace)
            self.schrift = schrift
            self.fett = QFont(schrift)
            self.fett.setBold(True)
            m = QFontMetricsF(schrift)
            # Zeilenhöhe in ganzen Bildpunkten: die Zeilenbilder schließen so lückenlos aneinander an.
            dpr = self.devicePixelRatioF()
            self.zb, self.zh, self.oben = m.horizontalAdvance("M"), math.ceil(m.height() * dpr) / dpr, m.ascent()
            self.leuchten = bool(e.get("leuchten", True))
            self.figur = bool(e.get("figur", True))
            self.weich = bool(e.get("weich", True))
            self.tray = None
            self.haupt = None
            self.ki = None
            self.ki_zu = False            # Reiter „KI“ geschlossen: erst Strg+Umschalt+K öffnet ihn wieder
            self.geteilt = False          # Reiter „KI“ steht rechts neben dem Chat
            self.such_ziel = None         # Ansicht, in der die Suchleiste gerade sucht
            self.reiter: list = []
            self.resize(int(self.zb * 110 + 16), int(self.zh * 32 + 16))
            self.setWindowOpacity(e["deckkraft"])
            # In der exe liegt das Icon in NemiCLIT2 (sys._MEIPASS), im Quelltext daneben.
            for icon in (Path(getattr(sys, "_MEIPASS", "")) / "nemicli.ico",
                         Path(__file__).resolve().parent.parent / "nemicli.ico"):
                if icon.is_file():
                    self.setWindowIcon(QIcon(str(icon)))
                    app.setWindowIcon(QIcon(str(icon)))
                    break
            spalte = QVBoxLayout(self)
            spalte.setContentsMargins(0, 0, 0, 0)
            spalte.setSpacing(0)
            self.leiste = QWidget(self)
            self.leiste.setAttribute(Qt.WA_StyledBackground, True)
            reihe = QHBoxLayout(self.leiste)
            reihe.setContentsMargins(6, 4, 6, 0)
            reihe.setSpacing(4)
            self.tabs = QTabBar(self.leiste)
            self.tabs.setTabsClosable(True)
            self.tabs.setMovable(True)
            self.tabs.setExpanding(False)
            self.tabs.setDrawBase(False)
            self.tabs.setFocusPolicy(Qt.NoFocus)
            self.tabs.currentChanged.connect(self._gewechselt)
            self.tabs.tabCloseRequested.connect(lambda i: self.reiter_schliessen(self._ansicht(i)))
            self.tabs.tabMoved.connect(self._verschoben)
            plus = QToolButton(self.leiste)
            plus.setText("+")
            plus.setToolTip("Neue Kommandozeile (Strg+Umschalt+T)")
            plus.setFocusPolicy(Qt.NoFocus)
            plus.clicked.connect(self.neue_shell)
            reihe.addWidget(self.tabs, 1)
            reihe.addWidget(plus)
            self.leiste.setStyleSheet(self._leisten_stil())
            self.leiste.hide()
            self.stapel = QStackedWidget(self)
            self.teiler = QSplitter(Qt.Horizontal, self)      # links die Reiter, rechts bei Bedarf „KI“
            self.teiler.setChildrenCollapsible(False)
            self.teiler.setHandleWidth(3)
            self.teiler.setStyleSheet(
                f"QSplitter::handle {{ background: {QColor(thema['hintergrund']).lighter(170).name()}; }}")
            self.teiler.addWidget(self.stapel)
            self.suchleiste = self._suchleiste_bauen()
            spalte.addWidget(self.leiste)
            spalte.addWidget(self.suchleiste)
            spalte.addWidget(self.teiler, 1)
            for tasten, aktion in (("Ctrl+Shift+T", self.neue_shell), ("Ctrl+Shift+W", self._aktuellen_schliessen),
                                   ("Ctrl+PgDown", lambda: self._weiter(1)), ("Ctrl+PgUp", lambda: self._weiter(-1)),
                                   ("Ctrl+Shift+K", self.ki_zeigen), ("Ctrl+Shift+G", self.teilen_umschalten)):
                kurz = QShortcut(QKeySequence(tasten), self)
                kurz.setContext(Qt.WindowShortcut)
                kurz.activated.connect(aktion)
            if ki_leitung is not None:
                self.ki_takt = QTimer(self)
                self.ki_takt.timeout.connect(self._ki_ereignisse)
                self.ki_takt.start(50)

        def _leisten_stil(self) -> str:
            grund = QColor(thema["hintergrund"])
            if self.durchsichtig:
                grund.setAlphaF(e["hintergrund"])
            gewaehlt = QColor(thema["hintergrund"]).lighter(140)
            return (f"QWidget {{ background: rgba({grund.red()},{grund.green()},{grund.blue()},{grund.alpha()}); }}"
                    f"QTabBar::tab {{ background: transparent; color: {thema['normal'][7]}; padding: 4px 12px;"
                    f" border: none; border-bottom: 2px solid transparent; margin-right: 2px; }}"
                    f"QTabBar::tab:selected {{ color: {thema['vordergrund']}; background: {gewaehlt.name()};"
                    f" border-bottom: 2px solid {akzent}; border-radius: 4px; }}"
                    f"QTabBar::tab:hover {{ color: {thema['vordergrund']}; }}"
                    f"QToolButton {{ color: {thema['vordergrund']}; background: transparent; border: none;"
                    f" padding: 2px 8px; font-size: 14px; }}"
                    f"QToolButton:hover {{ background: {gewaehlt.name()}; border-radius: 4px; }}"
                    f"QLineEdit {{ color: {thema['vordergrund']}; background: {gewaehlt.name()};"
                    f" border: 1px solid {akzent}; border-radius: 4px; padding: 2px 6px;"
                    f" selection-background-color: {akzent2}; }}"
                    f"QLabel {{ color: {thema['normal'][7]}; background: transparent; padding: 0 6px; }}")

        # -- Suchleiste (Strg+F in „KI“ und Kommandozeile) ----------------------------
        def _suchleiste_bauen(self):
            fenster = self

            class Suchfeld(QLineEdit):
                def keyPressEvent(self, ev):
                    if ev.key() == Qt.Key_Escape:
                        fenster.suche_schliessen()
                    elif ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Up, Qt.Key_Down):
                        # Enter und ↑: älterer Treffer; Umschalt+Enter und ↓: neuerer
                        zurueck = ev.key() == Qt.Key_Up or (
                            ev.key() != Qt.Key_Down and not ev.modifiers() & Qt.ShiftModifier)
                        fenster.such_sprung(-1 if zurueck else 1)
                    else:
                        super().keyPressEvent(ev)

            leiste = QWidget(self)
            leiste.setAttribute(Qt.WA_StyledBackground, True)
            reihe = QHBoxLayout(leiste)
            reihe.setContentsMargins(8, 4, 6, 4)
            reihe.setSpacing(4)
            self.suchfeld = Suchfeld(leiste)
            self.suchfeld.setPlaceholderText("Suchen …  Enter/↑ älter · ↓ neuer · Esc schließt")
            self.suchzahl = QLabel("", leiste)
            zu = QToolButton(leiste)
            zu.setText("✕")
            zu.setFocusPolicy(Qt.NoFocus)
            zu.clicked.connect(self.suche_schliessen)
            reihe.addWidget(self.suchfeld, 1)
            reihe.addWidget(self.suchzahl)
            reihe.addWidget(zu)
            leiste.setStyleSheet(self._leisten_stil())
            leiste.hide()
            self.such_warten = QTimer(self)            # erst suchen, wenn kurz nicht getippt wurde
            self.such_warten.setSingleShot(True)
            self.such_warten.setInterval(200)
            self.such_warten.timeout.connect(self._suche_starten)
            self.suchfeld.textChanged.connect(lambda _t: self.such_warten.start())
            return leiste

        def suche_oeffnen(self, ansicht):
            self.such_ziel = ansicht
            self.suchleiste.show()
            self.suchfeld.setFocus()
            self.suchfeld.selectAll()
            self._suche_starten()

        def suche_schliessen(self):
            self.such_warten.stop()
            self.suchleiste.hide()
            ziel, self.such_ziel = self.such_ziel, None
            if ziel is not None and (ziel in self.reiter or ziel is self.ki):
                ziel.suche_leeren()
                ziel.setFocus()

        def _suche_starten(self):
            ziel = self.such_ziel
            if ziel is None:
                return
            if not self.suchfeld.text().strip():
                ziel.suche_leeren()
                self.suchzahl.setText("")
                return
            ziel.suchen(self.suchfeld.text())
            self._suchzahl_zeigen()

        def such_sprung(self, schritt: int):
            if self.such_ziel is None:
                return
            if self.such_warten.isActive():           # noch nicht gesucht: erst suchen
                self.such_warten.stop()
                self._suche_starten()
                return
            self.such_ziel.such_sprung(schritt)
            self._suchzahl_zeigen()

        def _suchzahl_zeigen(self):
            ziel = self.such_ziel
            if ziel is None or not ziel.treffer:
                self.suchzahl.setText("kein Treffer")
            else:
                self.suchzahl.setText(f"{ziel.treffer_nr + 1} / {len(ziel.treffer)}")

        def figur_setzen(self, an: bool):
            """Figur der Persönlichkeit ein- oder ausschalten (/terminal figur). Die Spalten rechts werden frei
            oder wieder Text."""
            self.figur = an
            for ansicht in self.reiter:
                if ansicht.haupt:
                    ansicht.raster_anwenden()
                    ansicht.update()

        def leuchten_setzen(self, an: bool):
            """Leuchten ein- oder ausschalten (/terminal). Die Zeilenbilder entstehen neu: mit Leuchten
            sind sie durchsichtig, ohne deckend."""
            self.leuchten = an
            for ansicht in [*self.reiter, *([self.ki] if self.ki is not None and self.ki not in self.reiter else [])]:
                ansicht.bilder_verwerfen()

        # -- Geteilte Ansicht: „KI“ rechts neben den Reitern (Strg+Umschalt+G) -----------
        def teilen_umschalten(self):
            self.teilen(not self.geteilt)
            try:
                import config
                config.update(terminal_geteilt=self.geteilt)
            except Exception:
                pass

        def teilen(self, an: bool):
            if ki_leitung is None or an == self.geteilt:
                return
            self.suche_schliessen()
            self.geteilt = an
            self.ki_zu = False
            ki = self.ki
            if an:
                if ki is not None and ki in self.reiter:      # aus den Reitern nach rechts
                    self._aus_reitern(ki)
                if ki is None:
                    ki = self._ki_ansicht()
                else:
                    self.teiler.addWidget(ki)
                    ki.show()
                breite = max(2, self.teiler.width())
                self.teiler.setSizes([breite * 62 // 100, breite * 38 // 100])
            elif ki is not None:                              # von rechts zurück in die Reiter
                ki.setParent(None)
                self.hinzufuegen(ki, "🤖 KI", zeigen=False)
            if self.haupt is not None:
                self.haupt.setFocus()

        def _aus_reitern(self, ansicht):
            i = self.reiter.index(ansicht)
            self.reiter.pop(i)
            self.tabs.removeTab(i)
            self.stapel.removeWidget(ansicht)
            self.leiste.setVisible(len(self.reiter) > 1)
            aktuell = self._ansicht(self.tabs.currentIndex())
            if aktuell is not None:
                self.stapel.setCurrentWidget(aktuell)

        # -- Reiter --------------------------------------------------------------
        def _ansicht(self, i: int):
            return self.reiter[i] if 0 <= i < len(self.reiter) else None

        def hinzufuegen(self, ansicht, name: str, zeigen: bool = True):
            self.reiter.append(ansicht)
            self.stapel.addWidget(ansicht)
            i = self.tabs.addTab(name)
            if ansicht.haupt:                              # NemiCLI selbst schließt man über das Fenster
                for seite in (QTabBar.RightSide, QTabBar.LeftSide):
                    if self.tabs.tabButton(i, seite) is not None:
                        self.tabs.tabButton(i, seite).hide()
            self.leiste.setVisible(len(self.reiter) > 1)
            if zeigen:
                self.tabs.setCurrentIndex(i)
                self.stapel.setCurrentWidget(ansicht)
                ansicht.setFocus()

        def reiter_benennen(self, ansicht, name: str):
            if ansicht in self.reiter and name:
                self.tabs.setTabText(self.reiter.index(ansicht), reiter_name(name))

        def neue_shell(self):
            kind_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("NEMICLI_")}
            ansicht = Ansicht(self, _shell(), kind_env=kind_env)
            self.hinzufuegen(ansicht, "Kommandozeile")
            ansicht.programm_starten()

        def ki_zeigen(self):
            self.ki_zu = False
            self._ki_ansicht(zeigen=True)

        def _ki_ansicht(self, zeigen: bool = False):
            if self.ki is None:
                self.ki = Ansicht(self, nur_lesen=True)
                if self.geteilt:
                    self.teiler.addWidget(self.ki)
                else:
                    self.hinzufuegen(self.ki, "🤖 KI", zeigen=zeigen)
                self.ki.einspeisen("\x1b[2mHier erscheinen die Befehle der KI live (nur lesen, "
                                   "Mausrad/Bild↑↓ rollt zurück, Strg+F sucht).\x1b[0m\r\n")
            elif zeigen and self.geteilt:
                self.ki.setFocus()
            elif zeigen:
                self.tabs.setCurrentIndex(self.reiter.index(self.ki))
            return self.ki

        def _ki_ereignisse(self):
            for ereignis in ki_leitung.holen():
                if self.ki is None and (self.ki_zu or ereignis.get("t") != "start"):
                    continue
                self._ki_ansicht().einspeisen(ki_text(ereignis))

        def reiter_schliessen(self, ansicht):
            # schon zu: von Hand geschlossen, danach meldet das beendete Programm sich noch einmal
            if ansicht is None or ansicht.haupt or ansicht not in self.reiter:
                return
            if ansicht is self.such_ziel:
                self.suche_schliessen()
            ansicht.takt.stop()
            ansicht.beenden()
            i = self.reiter.index(ansicht)
            self.reiter.pop(i)
            self.tabs.removeTab(i)
            self.stapel.removeWidget(ansicht)
            if ansicht is self.ki:
                self.ki, self.ki_zu = None, True
            ansicht.deleteLater()
            self.leiste.setVisible(len(self.reiter) > 1)
            aktuell = self._ansicht(self.tabs.currentIndex())
            if aktuell is not None:
                self.stapel.setCurrentWidget(aktuell)
                aktuell.setFocus()

        def programm_zu(self, ansicht):
            """Programm eines Reiters beendet: NemiCLI schließt das Fenster, eine Kommandozeile ihren Reiter."""
            if ansicht.haupt:
                if self.tray is not None:
                    self.tray.hide()
                self.close()
            else:
                self.reiter_schliessen(ansicht)

        def _aktuellen_schliessen(self):
            self.reiter_schliessen(self._ansicht(self.tabs.currentIndex()))

        def _weiter(self, schritt: int):
            if len(self.reiter) > 1:
                self.tabs.setCurrentIndex((self.tabs.currentIndex() + schritt) % len(self.reiter))

        def _gewechselt(self, i: int):
            ansicht = self._ansicht(i)
            if self.such_ziel is not None and self.such_ziel in self.reiter and self.such_ziel is not ansicht:
                self.suche_schliessen()
            if ansicht is not None:
                self.stapel.setCurrentWidget(ansicht)
                ansicht.setFocus()

        def _verschoben(self, von: int, nach: int):
            self.reiter.insert(nach, self.reiter.pop(von))

        # -- Tray ------------------------------------------------------------------
        def in_tray(self, an: bool):
            """Solange das Desktop-Fenster (/gui) offen ist: Terminal aus dem Weg, Symbol im Tray."""
            if an:
                if self.tray is None:
                    self.tray = QSystemTrayIcon(self.windowIcon(), self)
                    self.tray.setToolTip("NemiCLI – läuft (Desktop-Fenster offen)")
                    self.tray_menue = QMenu()                 # setContextMenu übernimmt es nicht
                    self.tray_menue.addAction("Terminal zeigen", lambda: self.in_tray(False))
                    self.tray_menue.addSeparator()
                    self.tray_menue.addAction("NemiCLI beenden", self.close)
                    self.tray.setContextMenu(self.tray_menue)
                    self.tray.activated.connect(
                        lambda grund: self.in_tray(False)
                        if grund in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
                self.tray.show()
                self.hide()
            else:
                self.showNormal()
                self.raise_()
                self.activateWindow()
                if self.tray is not None:
                    self.tray.hide()

        def changeEvent(self, ev):
            # Minimiert ruhen Figur und Atmen; wieder groß laufen sie weiter
            if ev.type() == QEvent.WindowStateChange and not self.isMinimized():
                for ansicht in {*self.reiter, *([self.ki] if self.ki is not None else [])}:
                    ansicht._antreiben()
            super().changeEvent(ev)

        def closeEvent(self, ev):
            for ansicht in list(self.reiter):
                ansicht.beenden()
            super().closeEvent(ev)
            # ausdrücklich: hat jemand setQuitOnLastWindowClosed(False) gesetzt (Wache), liefe exec() sonst weiter
            QTimer.singleShot(0, app.quit)

    fenster = Fenster()
    fps_frei = "TEXTUAL_FPS" not in (env if env is not None else os.environ)    # von außen gesetzt: bleibt
    haupt_env = haupt_umgebung(env, e.get("fps", TEXTUAL_FPS[0]))
    if ki_leitung is not None:
        haupt_env.update(ki_leitung.umgebung())
    fenster.haupt = Ansicht(fenster, befehl, kind_env=haupt_env, haupt=True)
    fenster.hinzufuegen(fenster.haupt, titel)
    if e.get("geteilt"):
        fenster.teilen(True)
    fenster.show()
    fenster.haupt.programm_starten()
    fenster.haupt.setFocus()
    app.exec()
    pty = fenster.haupt.pty
    return (pty.exitcode or 0) if pty is not None else 0
