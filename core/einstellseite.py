"""einstellseite.py – Einstellungsseite des Desktop-Fensters: wenige Schalter vorne, der Rest unter
„Für Profis“. Jede Wahl ist ein vorhandener Befehl (/voice knopf, /theme mint …); die Sitzung führt ihn
aus wie im Terminal. Ausgeführt wird nur, was die Seite gerade selbst anbietet.
"""
from __future__ import annotations

import config

EINFACH, PROFI = "einfach", "profi"


def _schalter(schluessel: str, vorgabe: bool) -> str:
    return "an" if bool(config.load().get(schluessel, vorgabe)) else "aus"


def _stimme() -> str:
    import stimme
    return stimme.modus()


def _reflektieren() -> str:
    import lernschleife
    return "auto an" if lernschleife.an() else "auto aus"


def _skills() -> str:
    import skills
    return "selbst an" if skills.selbst() else "selbst aus"


def _helfer() -> str:
    import subagents
    return subagents.stufe()


def _fremdseiten() -> str:
    import webfetch
    return f"fremdseiten {webfetch.fremdseiten_modus()}"


def _rechtsklick() -> str:
    import kontextmenue
    st = kontextmenue.status()
    return "an" if st.get("bildschirm") and st.get("bild") else "aus"


AN_AUS = (("an", "An"), ("aus", "Aus"))

# (Bereich, Befehl, Titel, Erklärung, feste Optionen oder None = aus dem Befehls-Menü, aktiver Wert, nur diese Werte)
EINTRAEGE: list[tuple] = [
    (EINFACH, "/voice", "🔊 Stimme", "Wann die Persönlichkeit vorliest.",
     (("an", "Jede Antwort"), ("knopf", "Nur auf Anfrage"), ("aus", "Aus")), _stimme, None),
    (EINFACH, "/modus", "🧭 Arbeitsweise", "Was die KI ohne Rückfrage tun darf.", None, None, None),
    (EINFACH, "/staerke", "🧠 Denkstufe", "Wie gründlich das Modell nachdenkt.", None, None, None),
    (EINFACH, "/theme", "🎨 Farben im Terminal", "Farbschema des Terminals.", None, None, None),
    (PROFI, "/kontext", "📏 Kontext (lokales Modell)", "Wie viel vom Gespräch das Modell im Blick hat.",
     None, None, None),
    (PROFI, "/embeddings", "🧩 Gedächtnis-Modell", "Modell für die Suche im Gedächtnis.", None, None, None),
    (PROFI, "/reflektieren", "🪞 Selbst reflektieren", "Nach Korrekturen, Fehlern und größeren Aufgaben lernen.",
     (("auto an", "An"), ("auto aus", "Aus")), _reflektieren, None),
    (PROFI, "/skills", "🧩 Skills selbst anlegen", "Neue Skills ohne Rückfrage anlegen und ändern.",
     (("selbst an", "An"), ("selbst aus", "Aus")), _skills, None),
    (PROFI, "/uebung", "🎓 Übungsmodus", "Nach längerer Ruhe eine Übung vorbereiten.",
     AN_AUS, lambda: _schalter("uebung", True), None),
    (PROFI, "/subagenten", "🤝 Helfer", "Ob die KI Helfer losschicken darf.",
     (("an", "Mit Rückfrage"), ("auto", "Nach Bedarf"), ("off", "Aus")), _helfer, None),
    (PROFI, "/web", "🌐 Seiten außerhalb der Allowlist", "Fremde Seiten über Ollama lesen.",
     (("fremdseiten frei", "Frei"), ("fremdseiten fragen", "Nachfragen"), ("fremdseiten aus", "Aus")),
     _fremdseiten, None),
    (PROFI, "/kugel", "🌈 Kugel: freier Moment", "Ab und zu selbst etwas sagen oder malen.",
     (("impuls an", "An"), ("impuls aus", "Aus")), lambda: "impuls " + _schalter("kugel_impuls", False), None),
    (PROFI, "/kugel", "👋 Kugel: Begrüßen", "Nach längerer Pause begrüßen.",
     (("ankommen an", "An"), ("ankommen aus", "Aus")), lambda: "ankommen " + _schalter("kugel_ankommen", True),
     None),
    (PROFI, "/kugel", "🐾 Kugel: Desktop-Leben", "Schlafen, gähnen, herumgehen.",
     (("leben an", "An"), ("leben aus", "Aus")), lambda: "leben " + _schalter("kugel_leben", True), None),
    (PROFI, "/kugel", "🎵 Kugel: Musik spüren", "Auf Musik reagieren.",
     (("musik an", "An"), ("musik aus", "Aus")), lambda: "musik " + _schalter("kugel_musik", True), None),
    (PROFI, "/kontextmenue", "🖱 Rechtsklick-Menü", "NemiCLI im Rechtsklick-Menü von Windows.",
     AN_AUS, _rechtsklick, None),
    (PROFI, "/debug", "🐞 Debugbericht", "Bericht, in den die Cloud-KI hineinsehen kann.", None, None,
     ("an", "aus")),
    (PROFI, "/debug", "⚙ Motor-Log", "Jeder Prompt des lokalen Motors (groß, privat).", None, None,
     ("motor an", "motor aus")),
]

_UEBERSPRINGEN = {"", "anzeigen", "huggingface", "jetzt", "text"}


def _aus_menue(befehl: str, nur) -> list[dict]:
    """Optionen aus dem Auswahlmenü des Befehls (Haken ✓ = aktiv); Einträge mit Eingabe fallen weg."""
    import commands
    optionen = []
    for wert, text, frage in commands.auswahl(befehl):
        if frage or "{}" in wert or wert in _UEBERSPRINGEN or (nur and wert not in nur):
            continue
        roh = text.strip()
        aktiv = roh.startswith("✓")
        optionen.append({"wert": wert, "text": roh.removeprefix("✓").strip(), "aktiv": aktiv})
    return optionen


def seite() -> list[dict]:
    """Einträge der Seite: bereich, befehl, titel, erklaerung, optionen [{wert, text, aktiv}].
    Befehle ohne Optionen (z. B. Denkstufe bei Modellen ohne Stufen) fehlen."""
    aus = []
    for bereich, befehl, titel, erklaerung, feste, aktiv_fn, nur in EINTRAEGE:
        try:
            if feste is None:
                optionen = _aus_menue(befehl, nur)
            else:
                aktiv = aktiv_fn() if aktiv_fn else None
                optionen = [{"wert": w, "text": t, "aktiv": w == aktiv} for w, t in feste]
        except Exception:
            continue
        if optionen:
            aus.append({"bereich": bereich, "befehl": befehl, "titel": titel, "erklaerung": erklaerung,
                        "optionen": optionen})
    return aus


def befehl_fuer(befehl: str, wert: str) -> str | None:
    """Der auszuführende Befehl – nur, wenn die Seite genau diese Wahl gerade anbietet."""
    for e in seite():
        if e["befehl"] == befehl and any(o["wert"] == wert for o in e["optionen"]):
            return f"{befehl} {wert}"
    return None
