"""Appstore (tools/appstore.py): Apps aus HTML/CSS/JS lesen, Funktionen in NemiJS aufrufen, Sicherung für
debugAI (vorher → nachher, Behalten, Zurück), Werkzeuge für die KI. Fenster und Ansicht: test_nemiview."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions                                         # noqa: E402
import appstore as AS                                  # noqa: E402
import modes                                           # noqa: E402


class Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ordner = Path(self._tmp.name) / "Apps"
        p = mock.patch.object(AS, "ORDNER", self.ordner)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        AS.vorlagen_einrichten()


class AppsTests(Basis):
    def test_vorlagen_eingerichtet(self):
        alle = AS.apps()
        self.assertEqual({"wuerfel", "farbmischer"}, set(alle))
        w = alle["wuerfel"]
        self.assertEqual(("Würfel", "icon.svg"), (w.titel, w.icon))
        self.assertEqual(["wuerfeln"], [f.name for f in w.funktionen])
        self.assertEqual(0, AS.vorlagen_einrichten())                    # schon da: nichts doppelt

    def test_ungueltige_apps_fallen_weg(self):
        (self.ordner / "Gross Und Leer").mkdir()                          # Name ungültig
        (self.ordner / "ohneindex").mkdir()                               # keine index.html
        kaputt = self.ordner / "kaputt"
        kaputt.mkdir()
        (kaputt / "index.html").write_text("<p>x</p>", encoding="utf-8")
        (kaputt / "app.json").write_text("{kein json", encoding="utf-8")
        alle = AS.apps()
        self.assertNotIn("ohneindex", alle)
        self.assertEqual(("kaputt", [], None), (alle["kaputt"].titel, alle["kaputt"].funktionen, alle["kaputt"].icon))

    def test_liste_fuer_die_ki(self):
        text = AS.liste_text()
        self.assertIn("wuerfeln(seiten", text)
        self.assertIn("mischen(a", text)

    def test_auftrag_aus_dem_gezeigten_element(self):
        text = AS.auftrag(AS.app("wuerfel"), "das soll gelb werden", {
            "tag": "button", "selektor": "#los", "text": "Würfeln",
            "rect": {"x": 120.4, "y": 300, "breite": 140, "hoehe": 44}, "css": {"background-color": "rgb(139, 124, 255)"}})
        for teil in ("das soll gelb werden", "`#los`", "„Würfeln“", "x 120", "background-color: rgb(139, 124, 255)",
                     "style.css", "datei_bearbeiten"):
            self.assertIn(teil, text)

    def test_auftrag_ohne_stelle(self):
        text = AS.auftrag(AS.app("wuerfel"), "alles etwas größer", {})
        self.assertIn("App-ID wuerfel", text)
        self.assertIn("Keine Stelle gezeigt", text)

    def test_felder_sind_nur_daten(self):
        ok, text = AS.aufrufen("wuerfel", "wuerfeln", {"seiten": "</script><script>alert(1)</script>"})
        self.assertTrue(ok, text)                            # Text als Seitenzahl: ungültig → Vorgabe 6
        self.assertIn('"seiten": 6', text)

    def test_stand_aendert_sich_mit_der_datei(self):
        a = AS.app("wuerfel")
        vorher = AS.stand(a)
        (a.ordner / "style.css").write_text("body{color:yellow}", encoding="utf-8")
        self.assertNotEqual(vorher, AS.stand(a))


class EntfernenTests(Basis):
    def test_app_geht_in_den_papierkorb_samt_daten(self):
        import snapshot
        daten = self.ordner / "_daten"
        daten.mkdir()
        (daten / "wuerfel.json").write_text("{}", encoding="utf-8")
        verschoben = []

        def sichern(pfad, werkzeug, verschieben=False):
            verschoben.append((Path(pfad).name, werkzeug, verschieben))
            import shutil
            shutil.rmtree(pfad) if Path(pfad).is_dir() else Path(pfad).unlink()
            return "kopie"

        with mock.patch.object(snapshot, "sichern", side_effect=sichern):
            text = AS.entfernen("wuerfel")
        self.assertIn("Papierkorb", text)
        self.assertEqual([("wuerfel", "app_entfernen", True), ("wuerfel.json", "app_entfernen", True)], verschoben)
        self.assertNotIn("wuerfel", AS.apps())
        self.assertEqual(0, AS.vorlagen_einrichten())                    # Beispiel-App kommt nicht zurück

    def test_ohne_papierkorb_bleibt_alles(self):
        import snapshot
        with mock.patch.object(snapshot, "sichern", return_value=None), self.assertRaises(OSError):
            AS.entfernen("wuerfel")
        self.assertIn("wuerfel", AS.apps())
        with self.assertRaises(ValueError):
            AS.entfernen("../wuerfel")

    def test_bauauftrag_mit_design(self):
        text = AS.bauauftrag()
        for teil in ("SVG", "Farbwelt", "Game Over", "icon.svg", "app_pruefen", str(self.ordner)):
            self.assertIn(teil, text)


class SicherungTests(Basis):
    def test_zurueck_stellt_alles_her(self):
        a = AS.app("wuerfel")
        css = (a.ordner / "style.css").read_text(encoding="utf-8")
        self.assertTrue(AS.sichern(a))
        self.assertNotIn("_vorher", AS.apps())                                    # Sicherung ist keine App
        (a.ordner / "style.css").write_text("body{color:yellow}", encoding="utf-8")
        (a.ordner / "neu.js").write_text("// neu", encoding="utf-8")
        (a.ordner / "index.html").unlink()                                       # App kaputt: trotzdem zurück
        AS.vermerken("wuerfel", "gelb")
        self.assertEqual(["gelb"], AS.sicherung("wuerfel")["wuensche"])
        self.assertTrue(AS.zurueckholen("wuerfel"))
        self.assertEqual(css, (a.ordner / "style.css").read_text(encoding="utf-8"))
        self.assertFalse((a.ordner / "neu.js").exists())
        self.assertTrue((a.ordner / "index.html").is_file())
        self.assertIsNone(AS.sicherung("wuerfel"))
        self.assertFalse(AS.zurueckholen("wuerfel"))

    def test_behalten_und_erste_sicherung_bleibt(self):
        a = AS.app("wuerfel")
        AS.sichern(a)
        (a.ordner / "style.css").write_text("body{color:yellow}", encoding="utf-8")
        self.assertFalse(AS.sichern(a))                                          # vorher = vor der ersten Änderung
        self.assertNotIn("yellow", (AS._vorher("wuerfel") / "style.css").read_text(encoding="utf-8"))
        self.assertTrue(AS.verwerfen("wuerfel"))
        self.assertIn("yellow", (a.ordner / "style.css").read_text(encoding="utf-8"))
        self.assertIsNone(AS.sicherung("wuerfel"))
        self.assertIsNone(AS.sicherung("../wuerfel"))


class AufrufTests(Basis):
    """Funktionen laufen im eigenen Interpreter NemiJS – kein Browser nötig."""

    def test_endlosschleife_wird_gebremst(self):
        (self.ordner / "wuerfel" / "app.js").write_text(
            'NemiApp.funktion("wuerfeln", () => { while (true) {} }, "hängt");', encoding="utf-8")
        ok, text = AS.aufrufen("wuerfel", "wuerfeln", {})
        self.assertFalse(ok)
        self.assertIn("Rechenbremse", text)

    def test_kein_netz_in_apps(self):
        (self.ordner / "wuerfel" / "app.js").write_text(
            'NemiApp.funktion("wuerfeln", () => fetch("http://example.org"), "x");', encoding="utf-8")
        ok, text = AS.aufrufen("wuerfel", "wuerfeln", {})
        self.assertFalse(ok)
        self.assertIn("fetch", text)

    def test_wuerfeln(self):
        ok, text = AS.aufrufen("wuerfel", "wuerfeln", {"seiten": 20, "anzahl": 3})
        self.assertTrue(ok, text)
        e = json.loads(text.split(": ", 1)[1])
        self.assertEqual((3, 20), (len(e["augen"]), e["seiten"]))
        self.assertTrue(all(1 <= x <= 20 for x in e["augen"]))
        self.assertEqual(sum(e["augen"]), e["summe"])

    def test_mischen_und_fehler_der_app(self):
        ok, text = AS.aufrufen("farbmischer", "mischen", {"a": "#000000", "b": "#ffffff", "anteil": 0.5})
        self.assertTrue(ok, text)
        self.assertIn('"farbe": "#808080"', text)
        ok, text = AS.aufrufen("farbmischer", "mischen", {"a": "rot", "b": "#ffffff"})
        self.assertFalse(ok)
        self.assertIn("Keine Farbe", text)

    def test_unbekannt(self):
        self.assertFalse(AS.aufrufen("gibtsnicht", "x")[0])
        ok, text = AS.aufrufen("wuerfel", "zaubern")
        self.assertFalse(ok)
        self.assertIn("wuerfeln", text)


class WerkzeugTests(Basis):
    def test_registriert(self):
        self.assertIn("app_liste", actions.ACTIONS)
        self.assertIn("app_liste", modes.READ_TOOLS)
        self.assertFalse(actions.ACTIONS["app_aufrufen"]["confirm"])
        self.assertIn("App wuerfel · wuerfeln", actions.describe({"tool": "app_aufrufen", "app": "wuerfel",
                                                                   "funktion": "wuerfeln"}))

    def test_falsche_felder(self):
        self.assertFalse(actions._app_aufrufen({"app": "wuerfel"}).ok)
        self.assertFalse(actions._app_aufrufen({"app": "wuerfel", "funktion": "wuerfeln", "felder": "{kaputt"}).ok)
        with mock.patch.object(AS, "aufrufen", return_value=(True, "ok")) as f:
            self.assertTrue(actions._app_aufrufen({"app": "wuerfel", "funktion": "wuerfeln",
                                                   "felder": '{"seiten": 8}'}).ok)
        self.assertEqual({"seiten": 8}, f.call_args.args[2])

    def test_befehl_bekannt(self):
        from commands import COMMAND_LIST
        self.assertIn("/appstore", [c.name for c in COMMAND_LIST])


if __name__ == "__main__":
    unittest.main()
