"""🎨 Malen in Nemi Office: Formen prüfen, Leinwand (offscreen), Farbeimer, Rückgängig, Laden und Speichern, die
OfficeAI zeichnet Form für Form, internes Einsetzen und Herausgeben – und die Sitzung (Bild-Motor, Umzeichnen,
Seh-Modell) mit Schein-KI und Schein-Fenster."""
import ast
import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import actions as A  # noqa: E402
import paths  # noqa: E402
import profilordner  # noqa: E402
import qt_testhilfe  # noqa: E402
from buero import ablage  # noqa: E402
from buero import frage as BF  # noqa: E402
from buero import malen as M  # noqa: E402
from buero import werkzeuge as WZ  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    import office_agent
    import office_fenster
    import office_malen
except ImportError:                                   # PySide6 fehlt
    office_fenster = None


class Formen(unittest.TestCase):
    def test_alle_arten_und_farben(self):
        formen = [
            {"art": "linie", "von": [0, 0], "nach": [10, 10], "farbe": "rot", "dicke": 4},
            {"art": "pfeil", "von": [5, 5], "nach": [50, 5]},
            {"art": "rechteck", "x": 1, "y": 2, "b": 30, "h": 20, "fuellung": "himmelblau", "rund": 5},
            {"art": "kreis", "x": 50, "y": 50, "r": 10, "farbe": "", "fuellung": "#FFCC00"},
            {"art": "ellipse", "x": 0, "y": 0, "b": 40, "h": 20},
            {"art": "polygon", "punkte": [[0, 0], [10, 0], [5, 8]], "fuellung": "grün"},
            {"art": "strich", "punkte": [[0, 0], [5, 9], [10, 0]], "dicke": 2},
            {"art": "text", "x": 3, "y": 4, "text": "  Hallo   Welt ", "groesse": 20, "fett": "true"},
            {"art": "circle", "x": 1, "y": 1, "r": 2},                                  # englischer Name
        ]
        aus, fehler = M.formen_pruefen(formen, 100, 100)
        self.assertIsNone(fehler)
        self.assertEqual([f["art"] for f in aus], ["linie", "pfeil", "rechteck", "kreis", "ellipse", "polygon",
                                                   "strich", "text", "kreis"])
        self.assertEqual(aus[0]["farbe"], M.MAL_FARBEN["rot"])
        self.assertEqual(aus[3]["fuellung"], "#ffcc00")
        self.assertEqual(aus[3]["farbe"], "")                     # Fläche ohne Rand
        self.assertEqual(aus[7]["text"], "Hallo Welt")
        self.assertTrue(aus[7]["fett"])
        self.assertIn("Kreis", M.beschreiben(aus[3]))

    def test_fehler_werden_benannt(self):
        for form, teil in (({"art": "wolke"}, "gibt es nicht"), ({"art": "kreis", "x": 1, "y": 1}, "r angeben"),
                           ({"art": "linie", "von": [1]}, "von [x, y]"), ({"art": "kreis", "x": 1, "y": 1, "r": 3,
                                                                          "farbe": "glitzer"}, "kenne ich nicht"),
                           ({"art": "kreis", "x": 1, "y": 1, "r": 3, "farbe": ""}, "unsichtbar"),
                           ({"art": "polygon", "punkte": [[0, 0], [1, 1]]}, "mindestens 3"),
                           ({"art": "text", "x": 1, "y": 1, "text": "  "}, "text angeben")):
            with self.subTest(form=form):
                aus, fehler = M.formen_pruefen([form], 100, 100)
                self.assertEqual(aus, [])
                self.assertIn(teil, fehler)
        self.assertIn("Höchstens", M.formen_pruefen([{"art": "kreis", "x": 1, "y": 1, "r": 1}] * 81, 9, 9)[1])
        weit = M.form_pruefen({"art": "kreis", "x": 1e9, "y": -1e9, "r": 1}, 100, 100)
        self.assertEqual((weit["x"], weit["y"]), (200, -100))                     # bleibt nahe der Leinwand

    def test_werkzeuge_und_ablage(self):
        self.assertIn("zeichnen", WZ.erlaubt("malen"))
        self.assertIsNone(WZ.pruefen("malen", {"tool": "fuellen", "x": 3, "y": 4, "farbe": "gelb"}))
        self.assertIn("x und y", WZ.pruefen("malen", {"tool": "fuellen", "farbe": "gelb"}))
        self.assertIn("zwischen", WZ.pruefen("malen", {"tool": "groesse", "b": 5, "h": 5}))
        self.assertIn("prompt", WZ.pruefen("malen", {"tool": "umzeichnen"}))
        self.assertIn("gibt es hier nicht", WZ.pruefen("malen", {"tool": "bild_setzen", "pfad": "x"}))   # nur intern
        self.assertEqual(ablage.app_fuer("Bild.WEBP"), "malen")
        self.assertEqual(M.bildgroesse_fuer(1280, 800), (1280, 768))
        self.assertEqual(M.bildgroesse_fuer(100, 400)[1] > M.bildgroesse_fuer(100, 400)[0], True)

    def test_regeln_fuer_die_ki(self):
        s = BF.agent_system("malen", "Test")
        for teil in ("auf der geöffneten Leinwand", "(0,0) ist oben links", "zeichnen", "anschauen", "umzeichnen",
                     "Bildsperre"):
            self.assertIn(teil, s)


@unittest.skipIf(office_fenster is None, "PySide6 fehlt")
class _Basis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        for p in (mock.patch.object(paths, "DATEN", self.ordner), mock.patch.object(profilordner, "aktiv", lambda: "Test"),
                  mock.patch.object(office_agent, "FLUG_MS", 0), mock.patch.object(office_agent, "PAUSE_MS", 0),
                  mock.patch.object(office_malen, "FORM_MS", 0), mock.patch.object(office_malen, "TEIL_MS", 0)):
            p.start()
            self.addCleanup(p.stop)
        self.fenster = office_fenster.OfficeFenster(None)
        self.addCleanup(self._zu)

    def _zu(self):
        for s in self.fenster._seiten():
            if hasattr(s, "_veraendert"):
                s._veraendert = False
            if hasattr(s, "feld"):
                s.feld.document().setModified(False)
                s.quelle.document().setModified(False)
        self.fenster.close()
        self.fenster.deleteLater()

    def _malseite(self):
        self.fenster.neu("malen")
        return self.fenster.aktuelle_seite()

    @staticmethod
    def _farbe(seite, x, y) -> str:
        return seite.leinwand.bild.pixelColor(x, y).name()


class Leinwand(_Basis):
    def test_start_kachel_reiter_und_icon(self):
        self.assertEqual(self.fenster.start.findChildren(office_fenster.AppKachel)[2].titel, "Malen")
        s = self._malseite()
        self.assertEqual(s.app, "malen")
        i = self.fenster.stapel.indexOf(s)
        self.assertFalse(self.fenster.reiter.tabIcon(i).isNull())                 # eigenes Symbol im Reiter
        self.assertEqual(self.fenster.reiter.tabText(i), "Neues Bild")
        icon = office_malen.mal_icon(64).toImage()
        self.assertEqual(icon.pixelColor(0, 0).alpha(), 0)                         # durchsichtiger Rand
        self.assertGreater(icon.pixelColor(19, 18).red(), 200)                     # roter Farbklecks

    def test_formen_fuellen_rueckgaengig(self):
        s = self._malseite()
        lw = s.leinwand
        lw.merken()
        lw.form(M.form_pruefen({"art": "rechteck", "x": 10, "y": 10, "b": 100, "h": 60, "farbe": "schwarz",
                                "fuellung": "", "dicke": 2}, 1280, 800))
        self.assertEqual(self._farbe(s, 60, 40), "#ffffff")                       # innen noch weiß
        lw.merken()
        self.assertTrue(lw.fuellen(60, 40, QColor("#ff0000")))
        self.assertEqual(self._farbe(s, 60, 40), "#ff0000")
        self.assertEqual(self._farbe(s, 200, 200), "#ffffff")                     # außen bleibt
        self.assertFalse(lw.fuellen(60, 40, QColor("#ff0000")))                   # schon diese Farbe
        self.assertFalse(lw.fuellen(5000, 5, QColor("#00ff00")))                  # außerhalb
        self.assertTrue(lw.rueckgaengig())
        self.assertEqual(self._farbe(s, 60, 40), "#ffffff")
        self.assertTrue(lw.wiederholen())
        self.assertEqual(self._farbe(s, 60, 40), "#ff0000")

    def test_farbeimer_ganze_leinwand_schnell_genug(self):
        s = self._malseite()
        t0 = time.monotonic()
        self.assertTrue(s.leinwand.fuellen(5, 5, QColor("#123456")))
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertEqual(self._farbe(s, 1279, 799), "#123456")

    def test_auswahl_zuschneiden_einfuegen(self):
        from PySide6.QtCore import QRect
        s = self._malseite()
        lw = s.leinwand
        lw.form(M.form_pruefen({"art": "kreis", "x": 100, "y": 100, "r": 40, "farbe": "", "fuellung": "blau"},
                               1280, 800))
        lw.auswahl = QRect(50, 50, 100, 100)
        self.assertTrue(lw.kopieren())
        self.assertTrue(lw.zuschneiden())
        self.assertEqual((lw.bild.width(), lw.bild.height()), (100, 100))
        self.assertEqual(self._farbe(s, 50, 50), M.MAL_FARBEN["blau"])
        self.assertTrue(lw.einfuegen())                                           # Kopie schwebt oben links
        lw.schwebend_ablegen()
        self.assertTrue(lw.rueckgaengig())
        self.assertTrue(lw.rueckgaengig())
        self.assertEqual(lw.bild.width(), 1280)

    def test_speichern_und_laden_alle_formate(self):
        s = self._malseite()
        s.leinwand.form(M.form_pruefen({"art": "rechteck", "x": 0, "y": 0, "b": 400, "h": 400, "farbe": "",
                                        "fuellung": "#00aa00"}, 1280, 800))
        for end in M.BILD_SPEICHERN:
            with self.subTest(end=end):
                pfad = str(self.ordner / f"probe{end}")
                s.bild_speichern(pfad)
                n = office_malen.bild_lesen(pfad)
                self.assertEqual((n.width(), n.height()), (1280, 800))
                c = n.pixelColor(100, 100)
                self.assertLess(abs(c.green() - 0xaa) + c.red() + c.blue(), 30)
        with self.assertRaises(ValueError):
            office_malen.bild_lesen(str(self.ordner / "fehlt.png"))
        (self.ordner / "kaputt.png").write_bytes(b"kein bild")
        with self.assertRaises(ValueError):
            office_malen.bild_lesen(str(self.ordner / "kaputt.png"))

    def test_bild_oeffnen_und_zuletzt_mit_vorschau(self):
        bild = QImage(300, 200, QImage.Format.Format_RGB32)
        bild.fill(QColor("#ff8800"))
        pfad = self.ordner / "Office" / "Sonne.png"
        pfad.parent.mkdir(parents=True, exist_ok=True)
        bild.save(str(pfad))
        self.fenster.oeffnen(str(pfad))
        s = self.fenster.aktuelle_seite()
        self.assertEqual((s.app, s.pfad, s.leinwand.bild.width()), ("malen", str(pfad), 300))
        eintraege = ablage.zuletzt(self.fenster.ordner)
        self.assertEqual([(e["app"], Path(e["pfad"]).name) for e in eintraege], [("malen", "Sonne.png")])
        from PySide6.QtCore import QSize
        mini = office_fenster.miniatur(str(pfad), QSize(120, 150))
        self.assertLessEqual(mini.width(), 120)
        self.fenster.start.liste.viewport().repaint()                             # Karte mit Bild zeichnet
        k = s.kontext()
        self.assertEqual(k["groesse"], [300, 200])
        self.assertIn("Sonne.png geöffnet", k["kontext"])


class _KI(_Basis):
    """Helfer: Aktionen wie aus der Sitzung schicken und auf das Ergebnis des Fensters warten."""

    def setUp(self):
        super().setUp()
        self.gesendet = []
        self.fenster.leitung = mock.Mock(senden=self.gesendet.append)
        self.schritt = 0

    def _auftrag(self):
        s = self._malseite()
        self.fenster.nemi.fragen("Zeichne etwas")
        return s, self.gesendet[-1]["id"]

    def _tun(self, nr: int, **aktion) -> dict:
        self.schritt += 1
        vorher = len(self.gesendet)
        self.fenster._ereignis({"t": "aktion", "id": nr, "schritt": self.schritt, "aktion": aktion})
        for _ in range(2000):
            neu = [g for g in self.gesendet[vorher:] if g.get("op") == "ergebnis"]
            if neu:
                return neu[-1]
            QApplication.processEvents()
        self.fail("kein Ergebnis")


class OfficeAI(_KI):
    """Die OfficeAI zeichnet selbst: Aktionen wie aus der Sitzung, Ergebnisse gehen zurück."""

    def test_frage_traegt_die_leinwand(self):
        s, _nr = self._auftrag()
        frage = self.gesendet[-1]
        self.assertEqual((frage["app"], frage["groesse"]), ("malen", [1280, 800]))
        self.assertIn("Die Leinwand ist leer", frage["kontext"])

    def test_zeichnen_form_fuer_form(self):
        s, nr = self._auftrag()
        e = self._tun(nr, tool="zeichnen", formen=[
            {"art": "rechteck", "x": 0, "y": 600, "b": 1280, "h": 200, "farbe": "", "fuellung": "grün"},
            {"art": "kreis", "x": 1100, "y": 120, "r": 60, "farbe": "", "fuellung": "gelb"},
            {"art": "strich", "punkte": [[100, 500], [200, 450], [300, 500]], "farbe": "braun", "dicke": 8},
            {"art": "text", "x": 40, "y": 40, "text": "Sommer", "groesse": 48, "fett": True, "farbe": "blau"}])
        self.assertTrue(e["ok"], e["text"])
        self.assertIn("4 Form(en)", e["text"])
        self.assertEqual(self._farbe(s, 600, 700), M.MAL_FARBEN["grün"])
        self.assertEqual(self._farbe(s, 1100, 120), M.MAL_FARBEN["gelb"])
        self.assertIn("Kreis gefüllt gelb Mitte (1100,120)", e["kontext"]["kontext"])
        self.assertTrue(s.ungespeichert())
        s.leinwand.rueckgaengig()                                                 # ein Aufruf = ein Schritt zurück
        self.assertEqual(self._farbe(s, 600, 700), "#ffffff")

    def test_falsche_form_und_unbekanntes_werkzeug(self):
        s, nr = self._auftrag()
        e = self._tun(nr, tool="zeichnen", formen=[{"art": "kreis", "x": 1, "y": 1}])
        self.assertFalse(e["ok"])
        self.assertIn("Form 1", e["text"])
        self.assertFalse(self._tun(nr, tool="ersetzen", suchen="x", durch="y")["ok"])

    def test_fuellen_hintergrund_groesse_ansehen(self):
        s, nr = self._auftrag()
        self.assertTrue(self._tun(nr, tool="hintergrund", farbe="nachtblau")["ok"])
        self.assertEqual(self._farbe(s, 5, 5), M.MAL_FARBEN["nachtblau"])
        self.assertTrue(self._tun(nr, tool="fuellen", x=10, y=10, farbe="#ffffff")["ok"])
        self.assertFalse(self._tun(nr, tool="fuellen", x=10, y=10, farbe="weiß")["ok"])         # schon weiß
        e = self._tun(nr, tool="groesse", b=640, h=480)
        self.assertTrue(e["ok"])
        self.assertEqual(e["kontext"]["groesse"], [640, 480])
        blick = self._tun(nr, tool="ansehen")
        self.assertIn("Leinwand 640×480", blick["text"])
        self.assertIn("Fläche bei (10,10)", blick["text"])

    def test_esc_haelt_mitten_im_zeichnen_an(self):
        s, nr = self._auftrag()
        formen = [{"art": "kreis", "x": 20 * i, "y": 50, "r": 5, "farbe": "rot"} for i in range(1, 30)]
        with mock.patch.object(office_malen, "FORM_MS", 5):
            self.schritt += 1
            vorher = len(self.gesendet)
            self.fenster._ereignis({"t": "aktion", "id": nr, "schritt": self.schritt,
                                    "aktion": {"tool": "zeichnen", "formen": formen}})
            for _ in range(3):
                QApplication.processEvents()
            self.fenster.ausfuehrer.angehalten = True
            ende = time.monotonic() + 5
            erg = []
            while not erg and time.monotonic() < ende:
                QApplication.processEvents()
                erg = [g for g in self.gesendet[vorher:] if g.get("op") == "ergebnis"]
        self.assertTrue(erg)
        self.assertFalse(erg[-1]["ok"])
        self.assertIn("angehalten", erg[-1]["text"])

    def test_intern_herausgeben_und_einsetzen(self):
        s, nr = self._auftrag()
        e = self._tun(nr, tool="leinwand_export")
        self.assertTrue(e["ok"])
        self.assertTrue(Path(e["text"]).is_file())
        bild = QImage(200, 100, QImage.Format.Format_RGB32)
        bild.fill(QColor("#ff00ff"))
        pfad = self.ordner / "gemalt.png"
        bild.save(str(pfad))
        e = self._tun(nr, tool="bild_setzen", pfad=str(pfad), x=100, y=100, b=400, h=400, beschreibung="a fox")
        self.assertTrue(e["ok"], e["text"])
        self.assertEqual(self._farbe(s, 300, 300), "#ff00ff")                     # mittig, Seitenverhältnis bleibt
        self.assertEqual(self._farbe(s, 300, 120), "#ffffff")
        self.assertIn("a fox", e["kontext"]["kontext"])
        e = self._tun(nr, tool="bild_setzen", pfad=str(pfad), ersetzen=True)
        self.assertEqual(self._farbe(s, 5, 5), "#ff00ff")                         # ganze Leinwand
        self.assertFalse(self._tun(nr, tool="bild_setzen", pfad=str(self.ordner / "x.txt"))["ok"])


class Sitzung(unittest.TestCase):
    """mal_werkzeug in main_dienste.start_office: Seh-Modell, Umzeichnen und Bild mit Schein-KI und Schein-Fenster."""

    def _start_office(self):
        quelle = (ROOT / "main_dienste.py").read_text(encoding="utf-8-sig")
        baum = ast.parse(quelle)
        fn = next(n for n in baum.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_office")
        modul = ast.Module(body=[fn], type_ignores=[])
        self.gemalt: list[dict] = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        ergebnis_png = self.tmp / "ergebnis.png"
        ergebnis_png.write_bytes(b"\x89PNG")

        def paint(prompt, **kw):
            self.gemalt.append({"prompt": prompt, **kw})
            return str(ergebnis_png)
        stubs = {
            "gui_bruecke": types.SimpleNamespace(GuiBruecke=self._bruecke),
            "debugbericht": types.SimpleNamespace(ereignis=lambda *a, **k: None),
            "profilordner": types.SimpleNamespace(ordner=lambda *_: self.tmp),
            "profilbild": types.SimpleNamespace(pfad=lambda: None),
            "imagegen": types.SimpleNamespace(missing_reason_aktiv=lambda: None, backend=lambda: "motor", KREA="krea",
                                              paint=paint),
            "hintergrund": types.SimpleNamespace(sperre=lambda _n: None),
            "krea": types.SimpleNamespace(missing_reason=lambda: None, chosen_model=lambda: "x"),
            "bildbeschreiber": types.SimpleNamespace(finden=lambda: ("m", "p"),
                                                     beschreiben=lambda p, f=None: f"ein rotes Haus ({Path(p).name})"),
        }
        self.patch = mock.patch.dict(sys.modules, stubs)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        ns = {"asyncio": asyncio, "time": time, "Path": Path, "TURN_LOCK": asyncio.Lock(),
              "fenster_gesichter": lambda: {},
              "PS": types.SimpleNamespace(active=lambda: types.SimpleNamespace(name="Test", kurz=""))}
        exec(compile(modul, "main_dienste.py", "exec"), ns)
        return ns["start_office"]

    def _bruecke(self, loop, handlers):
        self.handlers = handlers
        test = self

        class Schein:
            def senden(self, ev):
                test.events.append(ev)
                if ev.get("t") == "aktion":
                    a = ev["aktion"]
                    text = str(test.tmp / "leinwand.png") if a["tool"] == "leinwand_export" else f"{a['tool']} ok"
                    if a["tool"] == "leinwand_export":
                        (test.tmp / "leinwand.png").write_bytes(b"\x89PNG")
                    loop.call_soon(handlers["ergebnis"], {"id": ev["id"], "schritt": ev["schritt"], "ok": True,
                                                         "text": text, "kontext": None})
        return Schein()

    async def _warten(self) -> None:
        ende = time.monotonic() + 10
        while not any(e.get("t") in ("antwort", "fehler") for e in self.events):
            self.assertLess(time.monotonic(), ende, "keine Antwort")
            await asyncio.sleep(0.01)
        self.assertEqual([e for e in self.events if e.get("t") == "fehler"], [])

    def _lauf(self, aktionen: list[dict]) -> list[str]:
        start_office = self._start_office()
        self.events: list[dict] = []
        antworten = ["```aktion\n" + json.dumps(a) + "\n```" for a in aktionen] + ["Fertig gezeichnet."]
        prompts: list[str] = []

        class Backend:
            async def ask_once(self, prompt, system):
                prompts.append(prompt)
                return antworten.pop(0)

        async def main():
            ctx = types.SimpleNamespace(backend=Backend(), model="schein")
            await start_office(ctx)
            self.handlers["frage"]({"id": 1, "app": "malen", "text": "x", "kontext": "", "name": "Bild",
                                    "auswahl": "", "groesse": [1280, 800]})
            await self._warten()
        asyncio.run(main())
        return prompts

    def test_anschauen_mit_seh_modell(self):
        prompts = self._lauf([{"tool": "anschauen", "frage": "Was ist das?"}])
        self.assertIn("Das Seh-Modell sieht auf der Leinwand: ein rotes Haus (leinwand.png)", prompts[-1])
        self.assertEqual([e["aktion"]["tool"] for e in self.events if e.get("t") == "aktion"], ["leinwand_export"])
        self.assertEqual(self.gemalt, [])

    def test_umzeichnen_mit_vorlage(self):
        self._lauf([{"tool": "umzeichnen", "prompt": "a watercolor painting of a house", "staerke": 2}])
        self.assertEqual(len(self.gemalt), 1)
        g = self.gemalt[0]
        self.assertEqual((g["vorlage"], g["staerke"], g["size"]), (str(self.tmp / "leinwand.png"), 0.9, (1280, 768)))
        gesetzt = [e["aktion"] for e in self.events if e.get("t") == "aktion"][-1]
        self.assertEqual((gesetzt["tool"], gesetzt["ersetzen"]), ("bild_setzen", True))

    def test_bild_in_einen_bereich(self):
        self._lauf([{"tool": "bild", "prompt": "a red fox", "x": 10, "y": 20, "b": 300, "h": 600}])
        self.assertEqual(self.gemalt[0]["size"], M.bildgroesse_fuer(300, 600))
        gesetzt = [e["aktion"] for e in self.events if e.get("t") == "aktion"][-1]
        self.assertEqual({k: gesetzt[k] for k in ("tool", "x", "y", "b", "h")},
                         {"tool": "bild_setzen", "x": 10, "y": 20, "b": 300, "h": 600})

    def test_ohne_seh_modell_ein_klarer_hinweis(self):
        start_office = self._start_office()
        sys.modules["bildbeschreiber"].finden = lambda: None
        self.events = []
        antworten = ['```aktion\n{"tool": "anschauen"}\n```', "ok"]
        prompts = []

        class Backend:
            async def ask_once(self, prompt, system):
                prompts.append(prompt)
                return antworten.pop(0)

        async def main():
            await start_office(types.SimpleNamespace(backend=Backend(), model="schein"))
            self.handlers["frage"]({"id": 2, "app": "malen", "text": "x", "kontext": "", "name": "", "auswahl": ""})
            await self._warten()
        asyncio.run(main())
        self.assertIn("Kein Seh-Modell eingerichtet", prompts[-1])

    def _lauf_roh(self, antworten: list[str]) -> tuple[list[str], str]:
        """Die Schein-KI antwortet wörtlich (ohne ```aktion-Rahmen) – Ergebnis: Prompts und Text der Antwort."""
        start_office = self._start_office()
        self.events = []
        prompts: list[str] = []

        class Backend:
            async def ask_once(self, prompt, system):
                prompts.append(prompt)
                return antworten.pop(0)

        async def main():
            await start_office(types.SimpleNamespace(backend=Backend(), model="schein"))
            self.handlers["frage"]({"id": 3, "app": "malen", "text": "Mal ein Haus", "kontext": "", "name": "Bild",
                                    "auswahl": "", "groesse": [1280, 800]})
            await self._warten()
        asyncio.run(main())
        return prompts, next(e for e in self.events if e.get("t") == "antwort")["text"]

    def _werkzeuge(self) -> list[str]:
        return [e["aktion"]["tool"] for e in self.events if e.get("t") == "aktion"]

    def test_werkzeugname_als_zaun_und_json_ohne_tool(self):
        formen = [{"art": "rechteck", "x": 0, "y": 500, "b": 1280, "h": 300, "farbe": "", "fuellung": "grün"}]
        _p, text = self._lauf_roh([
            'Ich male.\n```hintergrund\n{"farbe": "#a8cfe8"}\n```\n' + json.dumps({"formen": formen}),
            "Fertig – das Haus steht."])
        self.assertEqual(self._werkzeuge(), ["hintergrund", "zeichnen"])
        self.assertTrue(text.startswith("Fertig – das Haus steht."))
        self.assertIn("✅ 🎨 Hintergrund · ✏️ gezeichnet (1 Formen)", text)
        self.assertNotIn("{", text)

    def test_unklares_json_bekommt_einmal_den_hinweis(self):
        prompts, text = self._lauf_roh(['Erledigt!\n{"wert": 1, "irgendwas": 2}',
                                        'Ich kann das so nicht malen.\n{"wert": 1}'])
        self.assertEqual(self._werkzeuge(), [])
        self.assertIn("NICHTS ausgeführt", prompts[-1])                        # die KI erfährt es
        self.assertEqual(len(prompts), 2)                                       # nur einmal nachgefragt
        self.assertEqual(text, "Ich kann das so nicht malen.")                  # ohne rohes JSON


class MalenErweitert(unittest.TestCase):
    """Neue Formen, Stil-Felder und Bereichs-Werkzeuge – Prüfung ohne Oberfläche."""

    def test_neue_formen(self):
        for f, teil in (({"art": "kurve", "punkte": [[0, 0], [50, 80], [100, 0]], "geschlossen": True,
                          "fuellung": "rot", "farbe": ""}, "Kurve geschlossen"),
                        ({"art": "stern", "x": 50, "y": 50, "r": 30, "zacken": 7}, "Stern (7 Zacken)"),
                        ({"art": "sechseck", "x": 50, "y": 50, "r": 30}, "6-Eck"),
                        ({"art": "herz", "x": 50, "y": 50, "b": 40, "h": 36, "farbe": "",
                          "verlauf": {"von": "rosa", "nach": "rot", "art": "radial"}}, "Verlauf rosa→rot"),
                        ({"art": "bogen", "x": 50, "y": 50, "b": 40, "h": 20, "start": 200, "winkel": 140}, "Bogen"),
                        ({"art": "spray", "x": 50, "y": 50, "r": 20, "dichte": 100}, "100 Tupfer")):
            with self.subTest(art=f["art"]):
                g = M.form_pruefen(f, 400, 300)
                self.assertIsInstance(g, dict, g)
                self.assertIn(teil, M.beschreiben(g))
        g = M.form_pruefen({"art": "strich", "punkte": [[0, 0], [9, 9]], "pinsel": "kalligrafie",
                            "linienstil": "gestrichelt"}, 400, 300)
        self.assertEqual((g["pinsel"], g["linienstil"]), ("kalligrafie", "gestrichelt"))
        self.assertEqual(M.form_pruefen({"art": "rechteck", "x": 1, "y": 1, "b": 9, "h": 9, "farbe": "rot",
                                         "drehung": 30}, 400, 300)["drehung"], 30)

    def test_fehler_neuer_formen(self):
        for f in ({"art": "kurve", "punkte": [[0, 0], [1, 1]], "geschlossen": True, "fuellung": "rot"},
                  {"art": "stern", "x": 1, "y": 1}, {"art": "bogen", "x": 1, "y": 1, "b": 9, "h": 9, "start": 0},
                  {"art": "strich", "punkte": [[0, 0], [1, 1]], "pinsel": "wachsmalstift"},
                  {"art": "kreis", "x": 1, "y": 1, "r": 5, "linienstil": "wellig"},
                  {"art": "kreis", "x": 1, "y": 1, "r": 5, "farbe": "", "verlauf": {"von": "rot"}}):
            with self.subTest(f=f):
                self.assertIsInstance(M.form_pruefen(f, 400, 300), str)

    def test_bereichs_werkzeuge_pruefen(self):
        self.assertEqual(M.werkzeug_pruefen("verschieben", {"x": -10, "y": 0, "b": 50, "h": 50, "nach": [5, 5]},
                                            100, 100)["bereich"], (0, 0, 40, 50))       # auf die Leinwand beschnitten
        for name, a in (("verschieben", {"x": 0, "y": 0, "b": 5, "h": 5}), ("spiegeln", {"richtung": "schräg"}),
                        ("drehen", {"winkel": 45}), ("skalieren", {"x": 0, "y": 0, "b": 5, "h": 5}),
                        ("umfaerben", {"von": "rot"}), ("zuschneiden", {}), ("farbe_lesen", {"x": 500, "y": 1}),
                        ("radieren", {"x": 200, "y": 200, "b": 5, "h": 5})):
            with self.subTest(name=name):
                self.assertIsInstance(M.werkzeug_pruefen(name, a, 100, 100), str)
        self.assertEqual(M.werkzeug_pruefen("drehen", {"winkel": 45, "x": 0, "y": 0, "b": 9, "h": 9}, 100, 100)["winkel"],
                         45)

    def test_zuordnung_und_anzeige(self):
        self.assertEqual(WZ.werkzeug_aus_feldern("malen", {"formen": []}), "zeichnen")
        self.assertEqual(WZ.werkzeug_aus_feldern("malen", {"frage": "?"}), "anschauen")
        self.assertIsNone(WZ.werkzeug_aus_feldern("malen", {"x": 1, "y": 2, "b": 3, "h": 4}))   # mehrdeutig
        self.assertIsNone(WZ.werkzeug_aus_feldern("schreiben", {"text": "a"}))                  # mehrdeutig
        self.assertEqual(WZ.werkzeug_aus_feldern("schreiben", {"text": "a", "wo": "ende"}), "einfuegen")
        text = WZ.namen_ergaenzen("malen", 'A\n{"farbe": "rot"}\n```zeichnen\n{"formen": []}\n```\n```\n{"frage": "?"}\n'
                                           '```\n{"egal": 1}\nB')
        aktionen, rest = A.parse_actions(text, WZ.erlaubt("malen"))
        self.assertEqual([a["tool"] for a in aktionen], ["hintergrund", "zeichnen", "anschauen"])   # Reihenfolge
        self.assertIn('{"egal": 1}', rest)                                                     # Unklares bleibt
        self.assertEqual(WZ.anzeige_sauber('Fertig!\n```json\n{"a": 1}\n```\n{"b": 2}'), "Fertig!")
        self.assertEqual(WZ.zusammenfassung([({"tool": "zeichnen", "formen": [1, 2]}, True, ""),
                                             ({"tool": "zeichnen", "formen": [3]}, True, ""),
                                             ({"tool": "drehen"}, False, "")]),
                         "✏️ gezeichnet ×2 (3 Formen) · ⚠️ 1 ging nicht")

    def test_beschreibung_fuer_die_ki(self):
        text = WZ.beschreibung("malen")
        for wort in ("kurve", "verlauf", "pinsel", "spray", "radieren:", "verschieben:", "umfaerben:", "farbe_lesen:"):
            self.assertIn(wort, text)
        self.assertIn("wie eine Künstlerin", BF.agent_system("malen", "PersonA"))


class OfficeAIErweitert(_KI):
    """Neue Formen und Bereichs-Werkzeuge im Fenster."""

    def test_neue_formen_auf_der_leinwand(self):
        s, nr = self._auftrag()
        e = self._tun(nr, tool="zeichnen", formen=[
            {"art": "stern", "x": 200, "y": 200, "r": 80, "farbe": "", "fuellung": "gold"},
            {"art": "herz", "x": 500, "y": 200, "b": 160, "h": 150, "farbe": "", "fuellung": "rot"},
            {"art": "kurve", "punkte": [[700, 100], [800, 300], [900, 100]], "geschlossen": True, "farbe": "",
             "fuellung": "blau"},
            {"art": "rechteck", "x": 0, "y": 600, "b": 1280, "h": 200, "farbe": "",
             "verlauf": {"von": "weiß", "nach": "schwarz", "art": "linear", "winkel": 0}},
            {"art": "spray", "x": 1100, "y": 300, "r": 40, "dichte": 600, "dicke": 4, "farbe": "grün"},
            {"art": "strich", "punkte": [[50, 450], [300, 480]], "farbe": "lila", "dicke": 10, "pinsel": "weich"}])
        self.assertTrue(e["ok"], e["text"])
        self.assertEqual(self._farbe(s, 200, 200), M.MAL_FARBEN["gold"])
        self.assertEqual(self._farbe(s, 500, 220), M.MAL_FARBEN["rot"])
        self.assertEqual(self._farbe(s, 800, 180), M.MAL_FARBEN["blau"])
        links, rechts = s.leinwand.bild.pixelColor(5, 700), s.leinwand.bild.pixelColor(1275, 700)
        self.assertGreater(links.lightness(), rechts.lightness() + 150)               # Verlauf hell → dunkel
        gruen = sum(s.leinwand.bild.pixelColor(x, y).name() != "#ffffff"
                    for x in range(1060, 1140, 4) for y in range(260, 340, 4))
        self.assertGreater(gruen, 40)                                                  # Sprühtupfer
        self.assertIn("Stern (5 Zacken)", e["kontext"]["kontext"])

    def test_bereichs_werkzeuge(self):
        s, nr = self._auftrag()
        self._tun(nr, tool="zeichnen", formen=[{"art": "rechteck", "x": 0, "y": 0, "b": 100, "h": 100, "farbe": "",
                                                "fuellung": "rot"}])
        self.assertIn("#e53935", self._tun(nr, tool="farbe_lesen", x=50, y=50)["text"])
        e = self._tun(nr, tool="verschieben", x=0, y=0, b=100, h=100, nach=[300, 300])
        self.assertTrue(e["ok"], e["text"])
        self.assertEqual((self._farbe(s, 50, 50), self._farbe(s, 350, 350)), ("#ffffff", M.MAL_FARBEN["rot"]))
        self.assertTrue(self._tun(nr, tool="umfaerben", von="rot", nach="blau")["ok"])
        self.assertEqual(self._farbe(s, 350, 350), M.MAL_FARBEN["blau"])
        self.assertFalse(self._tun(nr, tool="umfaerben", von="gelb", nach="blau")["ok"])     # nichts zu tun
        self.assertTrue(self._tun(nr, tool="skalieren", x=300, y=300, b=100, h=100, neu_b=200, neu_h=200)["ok"])
        self.assertEqual(self._farbe(s, 480, 480), M.MAL_FARBEN["blau"])
        self.assertTrue(self._tun(nr, tool="radieren", x=300, y=300, b=200, h=200)["ok"])
        self.assertEqual(self._farbe(s, 400, 400), "#ffffff")
        s.leinwand.rueckgaengig()                                                         # radieren zurück
        self.assertEqual(self._farbe(s, 400, 400), M.MAL_FARBEN["blau"])
        self.assertTrue(self._tun(nr, tool="spiegeln", richtung="waagerecht")["ok"])
        self.assertEqual(self._farbe(s, 1280 - 400, 400), M.MAL_FARBEN["blau"])
        e = self._tun(nr, tool="drehen", winkel=90)
        self.assertTrue(e["ok"], e["text"])
        self.assertEqual(e["kontext"]["groesse"], [800, 1280])
        e = self._tun(nr, tool="zuschneiden", x=0, y=0, b=400, h=300)
        self.assertEqual(e["kontext"]["groesse"], [400, 300])
        self.assertFalse(self._tun(nr, tool="drehen", winkel=45)["ok"])                   # ganze Leinwand nur 90er

    def test_nutzer_werkzeuge(self):
        s = self._malseite()
        for name in ("spruehen", "stern", "herz", "n_eck"):
            self.assertIn(name, s.werkzeug_knoepfe)
        lw = s.leinwand
        lw.form({"art": "rechteck", "x": 0, "y": 0, "b": 10, "h": 10, "farbe": "", "fuellung": "#ff0000",
                 "dicke": 1, "deckkraft": 1})
        s._bereich_knopf("spiegeln", richtung="waagerecht")
        self.assertEqual(lw.bild.pixelColor(1275, 5).name(), "#ff0000")
        s._bereich_knopf("drehen", winkel=90)
        self.assertEqual((lw.bild.width(), lw.bild.height()), (800, 1280))


if __name__ == "__main__":
    unittest.main()
