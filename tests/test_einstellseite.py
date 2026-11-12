"""Einstellungsseite im Desktop-Fenster: Einträge mit aktivem Wert, nur angebotene Befehle, Seite im Fenster."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen         # Qt-Fenster nach den Tests dieses Moduls löschen
for d in ("", "core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / d))

import commands  # noqa: E402
import einstellseite  # noqa: E402


def _menue(cmd):
    return {"/theme": [("cyan", "✓ cyan  · Cyan", None), ("mint", "   mint  · Mint", None)],
            "/kontext": [("anzeigen", "   📊 Anzeigen", None), ("16384", "✓ 16k", None)],
            "/debug": [("", "   🐞 Stand", None), ("an", "   an", None), ("aus", "✓ aus", None),
                       ("motor an", "   Motor an", None), ("motor aus", "✓ Motor aus", None)],
            "/modus": [], "/staerke": [], "/embeddings": [("{}", "eingeben", "Welches?")]}.get(cmd, [])


class Seite(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(commands, "AUSWAHL_JETZT", _menue)
        p.start()
        self.addCleanup(p.stop)

    def test_eintraege_und_aktiv(self):
        with mock.patch("stimme.modus", return_value="knopf"):
            s = {(e["befehl"], e["titel"]): e for e in einstellseite.seite()}
        stimme = s[("/voice", "🔊 Stimme")]
        self.assertEqual([o["wert"] for o in stimme["optionen"] if o["aktiv"]], ["knopf"])
        farben = next(e for (b, _), e in s.items() if b == "/theme")
        self.assertEqual(farben["optionen"][0], {"wert": "cyan", "text": "cyan  · Cyan", "aktiv": True})
        kontext = next(e for (b, _), e in s.items() if b == "/kontext")
        self.assertEqual([o["wert"] for o in kontext["optionen"]], ["16384"])          # „anzeigen“ fällt weg
        debug = [e for (b, _), e in s.items() if b == "/debug"]
        self.assertEqual([[o["wert"] for o in e["optionen"]] for e in debug], [["an", "aus"], ["motor an", "motor aus"]])
        befehle = {b for b, _ in s}
        self.assertNotIn("/modus", befehle)                                               # ohne Optionen keine Karte
        self.assertNotIn("/embeddings", befehle)                                          # nur Eingabe-Einträge
        self.assertEqual(stimme["bereich"], "einfach")

    def test_nur_angebotene_befehle(self):
        self.assertEqual(einstellseite.befehl_fuer("/theme", "mint"), "/theme mint")
        self.assertEqual(einstellseite.befehl_fuer("/voice", "aus"), "/voice aus")
        self.assertIsNone(einstellseite.befehl_fuer("/theme", "mint; /schluessel 60"))
        self.assertIsNone(einstellseite.befehl_fuer("/schluessel", "60"))
        self.assertIsNone(einstellseite.befehl_fuer("/kontext", "anzeigen"))

    def test_fehler_eines_eintrags_kostet_nicht_die_seite(self):
        with mock.patch("kontextmenue.status", side_effect=OSError("kein Zugriff")):
            befehle = [e["befehl"] for e in einstellseite.seite()]
        self.assertNotIn("/kontextmenue", befehle)
        self.assertIn("/voice", befehle)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    import gui_fenster
except ImportError:                                   # PySide6 fehlt
    gui_fenster = None


@unittest.skipIf(gui_fenster is None, "PySide6 fehlt")
class Fenster(unittest.TestCase):
    def test_seite_fuellen_profis_und_klick(self):
        QApplication.instance() or QApplication(["test"])

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, auftrag):
                self.gesendet.append(auftrag)
        lt = Leitung()
        f = gui_fenster.Fenster(lt)
        f.seite("einstellungen")
        self.assertEqual(lt.gesendet[-1], {"op": "einstellungen"})
        eintraege = [{"bereich": "einfach", "befehl": "/voice", "titel": "Stimme", "erklaerung": "",
                      "optionen": [{"wert": "an", "text": "An", "aktiv": False},
                                   {"wert": "aus", "text": "Aus", "aktiv": True}]},
                     {"bereich": "profi", "befehl": "/uebung", "titel": "Übung", "erklaerung": "",
                      "optionen": [{"wert": "an", "text": "An", "aktiv": True}]}]
        f.ereignis({"t": "einstellungen", "eintraege": eintraege, "gemeldet": "✓ /voice aus"})
        seite = f.einstellungen
        karten = seite.findChildren(gui_fenster.EinstellKarte)
        self.assertEqual(len([k for k in karten if not k.isHidden()]), 1)               # Profis zugeklappt
        self.assertEqual(seite.status.text(), "✓ /voice aus")
        karte = karten[0]
        self.assertTrue(karte.knoepfe[1].akzent and karte.knoepfe[1].text().endswith("✔️"))
        karte.knoepfe[0].click()
        self.assertEqual(lt.gesendet[-1], {"op": "einstellung", "befehl": "/voice", "wert": "an"})
        seite._profi_umschalten()
        self.app_verarbeiten()
        sichtbar = [k for k in seite.findChildren(gui_fenster.EinstellKarte) if not k.isHidden()]
        self.assertIn("/uebung", {k.knoepfe[0].toolTip().split()[0] for k in sichtbar})
        f.deleteLater()

    @staticmethod
    def app_verarbeiten():
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
