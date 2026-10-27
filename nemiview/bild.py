"""
bild.py - eine lokale HTML-Seite unsichtbar mit NemiView laden: Bildschirmfoto und Konsolenmeldungen
(Werkzeug seite_ansehen, `todo pruefen`). Kein Browser, kein Netz: NemiView liest nur Dateien.

Läuft als eigener Prozess (NemiCLI-Schalter --seitenbild), damit eine hängende Seite die Sitzung nicht
aufhält und Qt nicht im Sitzungsprozess startet. Ergebnis als eine JSON-Zeile auf stdout.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SEITENZEIT_MS = 3000                     # virtuelle Zeit für Zeitgeber/Animationen vor dem Foto
NICHT_DA = {"iframe", "video", "audio", "canvas", "object", "embed", "frame", "frameset", "portal"}


def aufnehmen(datei: Path, breite: int, hoehe: int, png: Path) -> dict:
    """{ok, fehler, konsole, hinweise, titel} – ok False, wenn die Seite nicht lädt oder kein Bild entsteht."""
    if os.name != "nt":                    # unter Windows findet „offscreen“ keine Schriften; ohne Fenster bleibt es ohnehin unsichtbar
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QImage, QPainter

    from .laufzeit import AppLaufzeit
    from .layout import Layout
    from .zeichnen import Maler, QtMesser, bild_laden

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])   # noqa: F841 – Schriften brauchen sie
    datei = Path(datei).resolve()
    try:
        lz = AppLaufzeit(datei.parent, breite, hoehe, dunkel=False, virtuelle_zeit=True, einstieg=datei.name)
        lz.laden()
        lz.js.zeitgeber_laufen(SEITENZEIT_MS)
    except (OSError, RecursionError) as e:
        return {"ok": False, "fehler": [f"Die Seite lässt sich nicht laden: {e}"], "konsole": [], "hinweise": [],
                "titel": ""}
    bilder: dict = {}

    def bild(el):
        src = el.attrs.get("src", "")
        if src not in bilder:
            daten = lz.dok.datei(src)
            bilder[src] = bild_laden(daten, src) if daten else None
        return bilder[src]

    def bild_groesse(el):
        b = bild(el)
        if isinstance(b, QImage):
            return (b.width(), b.height())
        if b is not None and hasattr(b, "defaultSize"):
            s = b.defaultSize()
            return (s.width(), s.height())
        return None

    L = Layout(lz.dok, lz.kaskade, breite, hoehe, QtMesser())
    L.bild_groesse = bild_groesse
    try:
        L.rechnen()
    except RecursionError:
        lz.melden("❌ Layout zu tief verschachtelt")
    img = QImage(breite, hoehe, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    Maler(L, bild).malen(p, 0.0, breite, hoehe)
    p.end()
    ok = img.save(str(png), "PNG")
    fehler = [z for z in dict.fromkeys(lz.fehler + [k for k in lz.js.konsole if k.startswith("❌")])]
    konsole = [k for k in lz.js.konsole if k not in fehler]
    hinweise = sorted({f"<{el.tag}> zeigt NemiView nicht an" for el in lz.dok.alle() if el.tag in NICHT_DA})
    return {"ok": bool(ok), "fehler": fehler[:40], "konsole": konsole[:40], "hinweise": hinweise,
            "titel": lz.dok.titel or ""}


def ergebnis_datei(png: Path) -> Path:
    return Path(png).with_suffix(".json")


def main(argv: list[str]) -> int:
    """--seitenbild <Datei> <Breite> <Höhe> <PNG>; Ergebnis in <PNG>.json (die Fenster-exe hat kein stdout)."""
    if len(argv) < 4:
        return 2
    png = Path(argv[3])
    try:
        erg = aufnehmen(Path(argv[0]), int(argv[1]), int(argv[2]), png)
    except Exception as e:                 # Ergebnis immer als JSON, nie als Absturzmeldung
        erg = {"ok": False, "fehler": [f"NemiView: {type(e).__name__}: {e}"], "konsole": [], "hinweise": [],
               "titel": ""}
    ergebnis_datei(png).write_text(json.dumps(erg, ensure_ascii=False), encoding="utf-8")
    return 0
