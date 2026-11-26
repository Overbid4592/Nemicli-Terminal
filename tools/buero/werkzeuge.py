"""werkzeuge.py – was die KI in Nemi Office selbst tun darf: nur im geöffneten Dokument.

Keine Datei öffnen, speichern oder löschen, kein Netz, kein Befehl – nur Text und Zellen des Dokuments ändern
und sich orientieren („ansehen“: was gerade sichtbar ist). Ausgeführt wird im Office-Fenster (ui/office_agent.py),
hier stehen die Beschreibung für die KI und die Prüfung der Felder.
"""
from __future__ import annotations

import re

from buero import formel as F
from buero.textmodell import farbe_sauber

MAX_SCHRITTE = 12            # Runden KI -> Werkzeuge -> KI je Auftrag
MAX_AKTIONEN = 6             # Werkzeuge je Runde
MAX_TEXT = 20_000            # Zeichen, die ein Werkzeug einsetzen darf
MAX_ZELLEN = 20_000          # Zellen, die ein Werkzeug auf einmal ändern darf
MAX_PROMPT = 2_000           # Zeichen eines Bild-Prompts
MAX_BILDER = 4               # Bilder je Auftrag

FARB_WOERTER = {
    "weiß": "#ffffff", "weiss": "#ffffff", "hellgrau": "#f2f2f2", "grau": "#d9d9d9", "dunkelgrau": "#808080",
    "schwarz": "#000000", "gelb": "#fff2cc", "orange": "#fce4d6", "lachs": "#f8cbad", "rosa": "#ffd6e0",
    "flieder": "#e4dfec", "lila": "#e4dfec", "hellblau": "#ddebf7", "blau": "#ddebf7", "türkis": "#d9f2f7",
    "grün": "#e2efda", "gruen": "#e2efda", "rot": "#f8cbad", "gold": "#ffc000",
}
# Schriftfarben sollen kräftig sein, Flächen hell
SCHRIFT_WOERTER = {"rot": "#e74c3c", "blau": "#2f75b5", "grün": "#70ad47", "gruen": "#70ad47", "orange": "#ed7d31",
                   "lila": "#8e44ad", "pink": "#e91e63", "türkis": "#00a3b4", "grau": "#808080", "schwarz": "#000000",
                   "braun": "#7f6000", "gold": "#ffc000", "weiß": "#ffffff", "weiss": "#ffffff"}


def farbe(wert, schrift: bool = False) -> str | None:
    """"#rrggbb", Farbwort (grau, gelb, …) oder ""/"keine" -> "" (entfernen); None = ungültig."""
    t = str(wert if wert is not None else "").strip().lower()
    if t in ("", "keine", "kein", "none", "aus", "standard", "transparent"):
        return ""
    if (c := farbe_sauber(t)):
        return c
    return (SCHRIFT_WOERTER if schrift else FARB_WOERTER).get(t) or FARB_WOERTER.get(t) or None


def bereich(text) -> tuple[int, int, int, int] | None:
    """"B2" oder "A1:C3" -> (zeile1, spalte1, zeile2, spalte2), sonst None."""
    teile = str(text or "").replace(" ", "").upper().split(":")
    if not 1 <= len(teile) <= 2:
        return None
    a = F.zelle_aus(teile[0])
    b = F.zelle_aus(teile[-1])
    if a is None or b is None:
        return None
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def spalte(text) -> int | None:
    t = str(text or "").strip().upper()
    if re.fullmatch(r"[A-Z]{1,3}", t):
        return F.spalte_nr(t)
    if t.isdigit() and int(t) >= 1:
        return int(t) - 1
    return None


def zeile(text) -> int | None:
    t = str(text if text is not None else "").strip()
    return int(t) - 1 if t.isdigit() and 1 <= int(t) <= F.MAX_ZEILE else None


WERKZEUGE = {
    "schreiben": {
        "ansehen": "Selbstblick zur Orientierung: was gerade sichtbar ist, wo die Schreibmarke steht, was markiert ist.",
        "ersetzen": "Felder: suchen (genauer Text im Dokument), durch (neuer Text), alle (true = jede Stelle). "
                    "Format der Stelle bleibt.",
        "einfuegen": "Felder: text (Markdown: # Überschrift, - Liste, 1. Liste, > Zitat, - [ ] Checkbox, --- Linie, "
                     "**fett**, *kursiv*, `code`, Tabellen mit | ), wo (\"ende\" | \"anfang\" | \"cursor\" | \"nach\" | "
                     "\"vor\"), bezug (Text, nach/vor dem eingefügt wird – nur bei wo nach/vor).",
        "neu_schreiben": "Felder: text (Markdown) – ersetzt den ganzen Inhalt (Papierfarbe bleibt). Nur, wenn der Nutzer "
                         "eine ganz neue Fassung will.",
        "formatieren": "Felder: suchen (Text), alle (true/false) und eins oder mehr von fett, kursiv, unter (true/false), "
                       "farbe (Textfarbe), marker (Hintergrund), stufe (Überschrift 0–3).",
        "papier": "Felder: farbe – Papierfarbe der ganzen Seite (\"\" = weiß).",
        "bild": "Felder: prompt (ENGLISCH, genau: Motiv, Umgebung, Licht, Kamera/Fokus – keine Verneinungen, keine "
                "Qualitätswörter), wo (\"cursor\" | \"ende\"). Malt mit dem Bild-Motor (dauert etwas) und setzt das "
                "Bild ein. Nur, wenn der Nutzer ein Bild möchte.",
    },
    "tabelle": {
        "ansehen": "Selbstblick zur Orientierung: sichtbare Zeilen und Spalten mit Werten und Farben, aktuelle Zelle, "
                   "Markierung, Blätter.",
        "eintragen": "Felder: zelle (z. B. B2, Startzelle), text (ein Wert, eine Formel wie =SUMME(B2:B9) oder eine "
                     "Markdown-Tabelle – die füllt ab der Startzelle nach rechts und unten).",
        "faerben": "Felder: bereich (z. B. A1:C1), hg (Füllfarbe), fg (Schriftfarbe), fett (true/false). "
                   "\"\" entfernt eine Farbe.",
        "leeren": "Felder: bereich – Inhalte löschen (Farben bleiben).",
        "zeilen": "Felder: aktion (\"einfuegen\" | \"loeschen\"), ab (Zeilennummer, z. B. 5), anzahl (Standard 1). "
                  "Formeln wandern mit.",
        "spalten": "Felder: aktion (\"einfuegen\" | \"loeschen\"), ab (Spaltenbuchstabe, z. B. C), anzahl.",
        "sortieren": "Felder: bereich (Zeilen ohne Kopfzeile, z. B. A2:C9), nach (Spalte, z. B. B), absteigend.",
        "blatt": "Felder: name, neu (true = neues Blatt anlegen) – wechselt das Blatt.",
    },
    "malen": {
        "ansehen": "Selbstblick: Leinwandgröße, Hintergrund und die Liste der Formen darauf (was du und der Nutzer "
                   "gezeichnet habt), Markierung.",
        "zeichnen": "Felder: formen – Liste (höchstens 80), je Form: art und ihre Lage in Bildpunkten, (0, 0) = oben "
                    "links. Arten: linie/pfeil: von [x, y], nach [x, y] · rechteck/ellipse: x, y (oben links), b, h "
                    "(rechteck auch rund = Ecken-Radius) · kreis: x, y (Mitte), r · polygon (geschlossen, Ecken): "
                    "punkte [[x, y], …] · strich (freier Pinselstrich): punkte · kurve (weich GENAU durch alle Punkte – "
                    "für Organisches wie Flügel, Blätter, Wolken, Haare, Körper): punkte, geschlossen (true = Fläche) · "
                    "bogen: x, y (Mitte), b, h, start und winkel in Grad (0 = rechts, positiv = gegen den Uhrzeigersinn; "
                    "z. B. Lächeln start 200, winkel 140) · stern: x, y (Mitte), r, zacken (3–24), innen (0,15–0,95) · "
                    "herz: x, y (Mitte), b, h · n_eck (regelmäßig): x, y (Mitte), r, ecken (3–24) · spray (Sprühfarbe, "
                    "weiche Tupfer für Laub, Fell, Nebel, Sterne, Textur): x, y (Mitte), r, dichte (10–3000), dicke "
                    "(Tupfergröße) · text: x, y, text, groesse (px), fett. Für alle: farbe (Linie bzw. Schrift), "
                    "fuellung (Fläche, \"\" = keine), verlauf {von, nach, art: linear | radial, winkel} statt einer "
                    "Füllfarbe, linienstil (durchgezogen | gestrichelt | gepunktet), dicke (1–200), deckkraft (0,05–1; "
                    "Lasuren und Schatten), drehung (Grad, bei rechteck, ellipse, stern, herz, n_eck, text); strich und "
                    "offene kurve: pinsel (normal | bleistift | marker | kalligrafie | weich). Wird Form für Form "
                    "sichtbar gezeichnet.",
        "fuellen": "Felder: x, y, farbe – füllt die zusammenhängende Fläche an diesem Punkt (Farbeimer).",
        "hintergrund": "Felder: farbe – die ganze Leinwand in dieser Farbe neu (löscht alles Gezeichnete).",
        "groesse": "Felder: b, h – neue Leinwandgröße in Bildpunkten (der Inhalt bleibt oben links).",
        "radieren": "Felder: x, y, b, h (Bereich) ODER punkte [[x, y], …] und dicke (Radierstrich) – macht die Stelle "
                    "wieder zur Hintergrundfarbe.",
        "verschieben": "Felder: x, y, b, h (Bereich), nach [x, y] (neue obere linke Ecke), kopieren (true = das "
                       "Original bleibt). Zum Umsetzen gezeichneter Teile – die Formenliste zeigt, wo sie liegen.",
        "spiegeln": "Felder: richtung (waagerecht | senkrecht), optional x, y, b, h (ohne: die ganze Leinwand).",
        "drehen": "Felder: winkel (Grad, im Uhrzeigersinn), optional x, y, b, h (Bereich, jeder Winkel); ohne Bereich "
                  "die ganze Leinwand um 90, 180 oder 270.",
        "skalieren": "Felder: x, y, b, h (Bereich), neu_b, neu_h – den Bereich größer oder kleiner ziehen.",
        "umfaerben": "Felder: von, nach (Farben), toleranz (0–255, Standard 30), optional x, y, b, h – ersetzt eine "
                     "Farbe durch eine andere (ohne Bereich auf der ganzen Leinwand).",
        "zuschneiden": "Felder: x, y, b, h – die Leinwand auf diesen Bereich zuschneiden.",
        "farbe_lesen": "Felder: x, y – welche Farbe an dieser Stelle ist (#rrggbb). Zum Nachprüfen und Abstimmen.",
        "anschauen": "Felder: frage (optional, deutsch) – ein Seh-Modell schaut sich die Leinwand an und beschreibt "
                     "sie oder beantwortet die Frage. Zum Prüfen und Nachbessern, auch für Zeichnungen des Nutzers.",
        "bild": "Felder: prompt (ENGLISCH, genau: Motiv, Umgebung, Licht, Stil – keine Verneinungen), optional x, y, "
                "b, h (Platz auf der Leinwand; ohne: die ganze Leinwand). Malt mit dem Bild-Motor (dauert etwas) "
                "und setzt das Bild ein. Für Fotos und Realistisches, nicht für Zeichnungen.",
        "umzeichnen": "Felder: prompt (ENGLISCH: wie das Ergebnis aussehen soll, z. B. Stil und Motiv), staerke "
                      "(0,3–0,9; Standard 0,6 – höher = mehr Veränderung). Der Bild-Motor (nur Krea 2) malt die "
                      "Leinwand neu, Aufbau und Farben bleiben ungefähr.",
    },
}
# Pflicht- und Zusatzfelder je Werkzeug: Schreibt die KI ein JSON ohne "tool", wird es dem einen Werkzeug
# zugeordnet, dessen Pflichtfelder es hat und dessen Felder alle seine Schlüssel abdecken – sonst keinem.
FELDER: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "schreiben": {"ersetzen": (("suchen", "durch"), ("alle",)), "einfuegen": (("text",), ("wo", "bezug")),
                  "neu_schreiben": (("text",), ()),
                  "formatieren": (("suchen",), ("alle", "fett", "kursiv", "unter", "farbe", "marker", "stufe")),
                  "papier": (("farbe",), ()), "bild": (("prompt",), ("wo",))},
    "tabelle": {"eintragen": (("zelle", "text"), ()), "faerben": (("bereich",), ("hg", "fg", "fett")),
                "leeren": (("bereich",), ()), "zeilen": (("aktion", "ab"), ("anzahl",)),
                "spalten": (("aktion", "ab"), ("anzahl",)), "sortieren": (("bereich", "nach"), ("absteigend",)),
                "blatt": (("name",), ("neu",))},
    "malen": {"zeichnen": (("formen",), ()), "fuellen": (("x", "y", "farbe"), ()), "hintergrund": (("farbe",), ()),
              "groesse": (("b", "h"), ()), "anschauen": (("frage",), ()),
              "bild": (("prompt",), ("x", "y", "b", "h")), "umzeichnen": (("prompt", "staerke"), ()),
              "radieren": (("x", "y", "b", "h"), ()),
              "verschieben": (("x", "y", "b", "h", "nach"), ("kopieren",)),
              "spiegeln": (("richtung",), ("x", "y", "b", "h")), "drehen": (("winkel",), ("x", "y", "b", "h")),
              "skalieren": (("x", "y", "b", "h", "neu_b", "neu_h"), ()),
              "umfaerben": (("von", "nach"), ("toleranz", "x", "y", "b", "h")),
              "zuschneiden": (("x", "y", "b", "h"), ()), "farbe_lesen": (("x", "y"), ())},
}


def werkzeug_aus_feldern(app: str, obj: dict) -> str | None:
    """Das eine Werkzeug der App, zu dem die Felder eines JSON ohne "tool" passen – sonst None."""
    schluessel = {k for k in obj if not str(k).startswith("_")}
    if not schluessel:
        return None
    treffer = [name for name, (pflicht, extra) in FELDER.get(app, {}).items()
               if pflicht and set(pflicht) <= schluessel <= set(pflicht) | set(extra)]
    return treffer[0] if len(treffer) == 1 else None


_ZAUN = re.compile(r"```([\w-]*)[^\n]*\n.*?(?:```|\Z)", re.DOTALL)


def namen_ergaenzen(app: str, text: str) -> str:
    """JSON-Objekte ohne "tool" bekommen das Werkzeug, zu dem ihre Felder eindeutig passen: frei im Text werden sie zu
    einem ```aktion-Block, in einem Zaun ohne Werkzeugnamen bekommen sie "tool" dazu. So liest parse_actions sie in
    der Reihenfolge der Antwort. Ein Zaun mit Werkzeugnamen (```zeichnen) und unklare Objekte bleiben unverändert."""
    import json
    zaeune = [(m.start(), m.end(), m.group(1)) for m in _ZAUN.finditer(text)]
    dekoder, raus, pos = json.JSONDecoder(), [], 0
    while (start := text.find("{", pos)) >= 0:
        try:
            obj, ende = dekoder.raw_decode(text, start)
        except ValueError:
            raus.append(text[pos:start + 1])
            pos = start + 1
            continue
        zaun = next((z for z in zaeune if z[0] <= start < z[1]), None)
        name = (werkzeug_aus_feldern(app, obj) if isinstance(obj, dict) and "tool" not in obj
                and not (zaun and zaun[2] in WERKZEUGE.get(app, {})) else None)
        raus.append(text[pos:start])
        if name:
            neu = json.dumps({"tool": name, **obj}, ensure_ascii=False)
            raus.append(neu if zaun else f"\n```aktion\n{neu}\n```\n")
        else:
            raus.append(text[start:ende])
        pos = ende
    raus.append(text[pos:])
    return "".join(raus)


def sieht_nach_aktion_aus(text: str) -> bool:
    """Stehen JSON-Felder im Text (z. B. ein Aufruf ohne Werkzeugnamen), obwohl nichts erkannt wurde?"""
    return bool(re.search(r'\{\s*"\w+"\s*:', text or ""))


def anzeige_sauber(text: str) -> str:
    """Antwort für das Fenster: ohne Code-Blöcke und ohne Zeilen, die nur JSON sind."""
    t = re.sub(r"```.*?(```|\Z)", "", text or "", flags=re.DOTALL)
    zeilen = [z for z in t.splitlines() if not re.match(r'\s*[\[{\]}]|\s*"\w+"\s*:', z)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(zeilen)).strip()


SYMBOLE = {"zeichnen": "✏️", "hintergrund": "🎨", "fuellen": "🪣", "groesse": "📐", "anschauen": "👀",
           "ansehen": "👀", "bild": "🖼", "umzeichnen": "✨", "radieren": "🧽", "verschieben": "↔️", "spiegeln": "⇋",
           "drehen": "⟳", "skalieren": "⤢", "umfaerben": "🎨", "zuschneiden": "✂️", "farbe_lesen": "💧",
           "ersetzen": "🔁", "einfuegen": "➕", "neu_schreiben": "📝", "formatieren": "🖋", "papier": "📄",
           "eintragen": "✍️", "faerben": "🎨", "leeren": "🧹", "zeilen": "↕️", "spalten": "↔️", "sortieren": "🔃",
           "blatt": "📑"}
NAMEN = {"zeichnen": "gezeichnet", "hintergrund": "Hintergrund", "fuellen": "Farbeimer", "groesse": "Größe",
         "anschauen": "angeschaut", "ansehen": "nachgesehen", "bild": "Bild", "umzeichnen": "umgezeichnet",
         "radieren": "radiert", "verschieben": "verschoben", "spiegeln": "gespiegelt", "drehen": "gedreht",
         "skalieren": "skaliert", "umfaerben": "umgefärbt", "zuschneiden": "zugeschnitten",
         "farbe_lesen": "Farbe gelesen", "ersetzen": "ersetzt", "einfuegen": "eingefügt",
         "neu_schreiben": "neu geschrieben", "formatieren": "formatiert",
         "papier": "Papierfarbe", "eintragen": "eingetragen", "faerben": "gefärbt", "leeren": "geleert",
         "zeilen": "Zeilen", "spalten": "Spalten", "sortieren": "sortiert", "blatt": "Blatt"}


def zusammenfassung(schritte: list[tuple[dict, bool, str]]) -> str:
    """Die Schritte eines Auftrags sortiert und kurz: „✅ ✏️ zeichnen ×3 (41 Formen) · 👀 anschauen“."""
    gezaehlt: dict[str, list[int]] = {}
    fehler = 0
    for aktion, ok, _text in schritte:
        name = str(aktion.get("tool") or "")
        if name not in SYMBOLE:
            continue
        if not ok:
            fehler += 1
            continue
        eintrag = gezaehlt.setdefault(name, [0, 0])
        eintrag[0] += 1
        if name == "zeichnen" and isinstance(aktion.get("formen"), list):
            eintrag[1] += len(aktion["formen"])
    teile = []
    for name, (anzahl, formen) in gezaehlt.items():
        t = f"{SYMBOLE[name]} {NAMEN.get(name, name)}" + (f" ×{anzahl}" if anzahl > 1 else "")
        teile.append(t + (f" ({formen} Formen)" if formen else ""))
    if fehler:
        teile.append(f"⚠️ {fehler} ging{'en' if fehler > 1 else ''} nicht")
    return ("✅ " if teile and not fehler else "") + " · ".join(teile)


FARB_HILFE = ("Farben als #rrggbb oder als Wort: grau, hellgrau, gelb, orange, rosa, lila, hellblau, grün, rot, weiß; "
              "Schriftfarben kräftig (rot, blau, grün …).")


def erlaubt(app: str) -> tuple[str, ...]:
    return tuple(WERKZEUGE.get(app, {}))


def beschreibung(app: str) -> str:
    zeilen = [f"- {name}: {text}" for name, text in WERKZEUGE.get(app, {}).items()]
    return "\n".join(zeilen)


def pruefen(app: str, aktion: dict) -> str | None:
    """Fehlertext, wenn die Aktion nicht ausgeführt werden darf; None = in Ordnung."""
    name = str(aktion.get("tool") or "")
    if name not in WERKZEUGE.get(app, {}):
        return f"'{name}' gibt es hier nicht – erlaubt: {', '.join(erlaubt(app))}."
    for feld in ("text", "durch", "suchen", "bezug"):
        if len(str(aktion.get(feld) or "")) > MAX_TEXT:
            return f"Feld {feld} ist länger als {MAX_TEXT} Zeichen."
    if app == "tabelle":
        if name in ("faerben", "leeren", "sortieren"):
            b = bereich(aktion.get("bereich"))
            if b is None:
                return "bereich fehlt oder ist ungültig (z. B. A1:C3)."
            if (b[2] - b[0] + 1) * (b[3] - b[1] + 1) > MAX_ZELLEN:
                return f"Der Bereich ist größer als {MAX_ZELLEN} Zellen."
        if name == "eintragen" and F.zelle_aus(str(aktion.get("zelle") or "")) is None:
            return "zelle fehlt oder ist ungültig (z. B. B2)."
        if name == "faerben":
            for feld, schrift in (("hg", False), ("fg", True)):
                if feld in aktion and farbe(aktion[feld], schrift) is None:
                    return f"{feld}: unbekannte Farbe '{aktion[feld]}'. {FARB_HILFE}"
        if name == "zeilen" and zeile(aktion.get("ab")) is None:
            return "ab: Zeilennummer ab 1 angeben."
        if name == "spalten" and spalte(aktion.get("ab")) is None:
            return "ab: Spaltenbuchstabe angeben (z. B. C)."
        if name == "sortieren" and spalte(aktion.get("nach")) is None:
            return "nach: Spalte angeben (z. B. B)."
    elif app == "malen":
        return _malen_pruefen(name, aktion)
    else:
        if name in ("ersetzen", "formatieren") and not str(aktion.get("suchen") or ""):
            return "suchen fehlt."
        if name in ("einfuegen", "neu_schreiben") and not str(aktion.get("text") or ""):
            return "text fehlt."
        if name == "einfuegen" and str(aktion.get("wo") or "ende") in ("nach", "vor") and not aktion.get("bezug"):
            return "bezug fehlt (bei wo nach/vor)."
        for feld, schrift in (("farbe", True), ("marker", False)):
            if feld in aktion and farbe(aktion[feld], schrift) is None:
                return f"{feld}: unbekannte Farbe '{aktion[feld]}'. {FARB_HILFE}"
        if name == "papier" and farbe(aktion.get("farbe")) is None:
            return f"farbe: unbekannte Farbe '{aktion.get('farbe')}'. {FARB_HILFE}"
        if name == "bild":
            prompt = str(aktion.get("prompt") or "").strip()
            if not prompt:
                return "prompt fehlt (englische Bildbeschreibung)."
            if len(prompt) > MAX_PROMPT:
                return f"prompt ist länger als {MAX_PROMPT} Zeichen."
            if str(aktion.get("wo") or "cursor") not in ("cursor", "ende"):
                return "wo: \"cursor\" oder \"ende\"."
    return None


def _malen_pruefen(name: str, aktion: dict) -> str | None:
    """Felder der Mal-Werkzeuge (die Formen selbst prüft die Leinwand mit ihrer Größe: buero.malen)."""
    from buero import malen as M
    if name == "zeichnen":
        formen = aktion.get("formen")
        if not isinstance(formen, list) or not formen:
            return "formen: Liste mit mindestens einer Form."
        if len(formen) > M.MAX_FORMEN:
            return f"Höchstens {M.MAX_FORMEN} Formen je Aufruf – teil die Zeichnung auf mehrere Aufrufe auf."
    elif name in ("fuellen", "hintergrund"):
        if M.farbe(aktion.get("farbe")) is None:
            return f"farbe: unbekannt '{aktion.get('farbe')}' – #rrggbb oder ein Farbwort."
        if name == "fuellen" and (not isinstance(aktion.get("x"), (int, float)) or
                                  not isinstance(aktion.get("y"), (int, float))):
            return "fuellen: x und y (Bildpunkte) angeben."
    elif name == "groesse":
        g = M.groesse_pruefen(aktion.get("b"), aktion.get("h"))
        if isinstance(g, str):
            return g
    elif name in ("bild", "umzeichnen"):
        prompt = str(aktion.get("prompt") or "").strip()
        if not prompt:
            return "prompt fehlt (englische Bildbeschreibung)."
        if len(prompt) > MAX_PROMPT:
            return f"prompt ist länger als {MAX_PROMPT} Zeichen."
    elif name == "anschauen" and len(str(aktion.get("frage") or "")) > 1000:
        return "frage ist zu lang (höchstens 1000 Zeichen)."
    return None


def anzahl(wert, standard: int = 1, hoechst: int = 1000) -> int:
    try:
        return max(1, min(hoechst, int(wert)))
    except (TypeError, ValueError):
        return standard


def wahr(wert) -> bool:
    return wert is True or str(wert).strip().lower() in ("true", "ja", "1", "wahr", "yes")
