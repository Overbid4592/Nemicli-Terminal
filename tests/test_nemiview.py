"""NemiView: App-Laufzeit (DOM, Ereignisse, localStorage), CSS-Kaskade, Layout, Zeichnen und das Fenster mit
debugAI (Zeigen → Auftrag über die Leitung → neue Fassung, Rückfragen im eigenen Qt-Bereich)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from nemiview import nemicss                                   # noqa: E402
from nemiview.laufzeit import AppLaufzeit                      # noqa: E402
from nemiview.layout import Layout                             # noqa: E402

VORLAGEN = ROOT / "appstore_vorlagen"


class Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.apps = Path(self._tmp.name) / "Apps"
        shutil.copytree(VORLAGEN, self.apps)
        self.addCleanup(self._tmp.cleanup)

    def app(self, name, html=None, js=None, css=None):
        ordner = self.apps / name
        ordner.mkdir(exist_ok=True)
        if html is not None:
            (ordner / "index.html").write_text(html, encoding="utf-8")
        if js is not None:
            (ordner / "app.js").write_text(js, encoding="utf-8")
        if css is not None:
            (ordner / "style.css").write_text(css, encoding="utf-8")
        lz = AppLaufzeit(ordner, 800, 600, speicher_ordner=self.apps / "_daten", virtuelle_zeit=True)
        lz.laden()
        return lz


class LaufzeitTests(Basis):
    def test_wuerfel_knopf(self):
        lz = self.app("wuerfel")
        lz.dok.nach_id("anzahl").wert = "4"
        lz.sicher(lz.dok.klick, lz.dok.nach_id("los"))
        augen = lz.dok.nach_id("augen").text_inhalt().split()
        self.assertEqual(4, len(augen))
        self.assertTrue(lz.dok.nach_id("summe").text_inhalt().startswith("Summe "))
        self.assertEqual([], lz.fehler)

    def test_dom_ereignisse_und_klassen(self):
        lz = self.app("probe", html="""<!doctype html><html><head><style>.an{color:red}</style></head><body>
            <ul id="liste"></ul><button id="k">+</button><input id="t"><script>
            let n = 0;
            document.getElementById("k").addEventListener("click", (e) => {
              n++; const li = document.createElement("li"); li.textContent = "Eintrag " + n;
              li.classList.add("an"); document.querySelector("#liste").append(li); e.target.dataset.zahl = n;
            });
            document.getElementById("t").addEventListener("input", (e) => { document.title = e.target.value; });
            </script></body></html>""")
        k = lz.dok.nach_id("k")
        for _ in range(3):
            lz.sicher(lz.dok.klick, k)
        liste = lz.dok.nach_id("liste")
        self.assertEqual(["Eintrag 1", "Eintrag 2", "Eintrag 3"], [e.text_inhalt() for e in liste.elemente()])
        self.assertEqual("3", k.attrs["data-zahl"])
        stil = lz.kaskade.stil(liste.elemente()[0], {})
        self.assertEqual("red", stil["color"])
        t = lz.dok.nach_id("t")
        t.wert = "Hallo"
        lz.ausloesen(t, "input")
        self.assertEqual("Hallo", lz.dok.titel)

    def test_innerhtml_ohne_skripte_und_on_attribute(self):
        lz = self.app("probe2", html="""<body><div id="d"></div><script>
            document.getElementById("d").innerHTML = '<b onclick="boese()">x</b><script>boese()<\\/script><img src="http://example.org/a.png">';
            </script></body>""")
        d = lz.dok.nach_id("d")
        b = d.elemente()[0]
        self.assertNotIn("onclick", b.attrs)
        self.assertFalse(any(e.tag == "script" for e in d.alle()))
        self.assertIsNone(lz.dok.datei("http://example.org/a.png"))
        self.assertIsNone(lz.dok.datei("../../geheim.txt"))

    def test_localstorage_als_datei_je_app(self):
        lz = self.app("speicher", html="<body><script>localStorage.setItem('zahl', '42');</script></body>")
        self.assertEqual({"zahl": "42"}, json.loads((self.apps / "_daten" / "speicher.json").read_text("utf-8")))
        lz2 = self.app("speicher", html="<body><script>document.title = localStorage.getItem('zahl');</script></body>")
        self.assertEqual("42", lz2.dok.titel)

    def test_on_eigenschaft_ersetzt_und_body_onload(self):
        lz = self.app("eigen", html="""<body onload="document.body.dataset.geladen = 'ja'"><button id="k"></button>
            <script>
            let n = 0, r = 0;
            const k = document.getElementById("k");
            k.onclick = () => { n += 1; };
            k.onclick = () => { n += 10; document.title = String(n); };
            window.onresize = () => { r += 1; };
            window.onresize = () => { r += 10; k.dataset.r = r; };
            </script></body>""")
        k = lz.dok.nach_id("k")
        lz.sicher(lz.dok.klick, k)
        self.assertEqual("10", lz.dok.titel)                          # nur der letzte on…-Hörer läuft
        lz.groesse(500, 400)
        self.assertEqual("10", k.attrs.get("data-r"))
        self.assertEqual("ja", lz.dok.finden("body").attrs.get("data-geladen"))
        k.js_set("onclick", None, lz.js)
        lz.sicher(lz.dok.klick, k)
        self.assertEqual("10", lz.dok.titel)

    def test_knotengrenze_nach_neuaufbauten(self):
        from unittest import mock

        from nemiview import nemidom
        from nemiview.nemijs import Grenze
        lz = self.app("knoten", html="<body><ul id='l'></ul></body>")
        liste = lz.dok.nach_id("l")
        with mock.patch.object(nemidom, "MAX_KNOTEN", 50):
            for _ in range(100):                                     # entfernte Knoten zählen nicht weiter
                liste.js_set("innerHTML", "<li>a</li><li>b</li>", lz.js)
            self.assertEqual(2, len(liste.elemente()))
            with self.assertRaises(Grenze):                          # die Grenze selbst bleibt
                liste.js_set("innerHTML", "<li>x</li>" * 60, lz.js)

    def test_fehler_im_zeitgeber_haelt_die_uebrigen_nicht_an(self):
        lz = self.app("zeit", html="""<body><script>
            setTimeout(() => { throw new Error("kaputt"); }, 10);
            setTimeout(() => { document.title = "weiter"; }, 20);
            </script></body>""")
        lz.zeitgeber_laufen(100)
        self.assertEqual("weiter", lz.dok.titel)
        self.assertTrue(any("kaputt" in f for f in lz.fehler))

    def test_fehler_in_der_app_bleiben_in_der_app(self):
        lz = self.app("kaputt", html="<body><script>while (true) {}</script><p>noch da</p></body>")
        self.assertTrue(any("Rechenbremse" in f for f in lz.fehler))
        self.assertEqual("noch da", lz.dok.finden("p").text_inhalt())


class CssTests(unittest.TestCase):
    def test_kaskade_spezifitaet_und_variablen(self):
        k = nemicss.Kaskade([":root{--a:#123456} p{color:var(--a)} .x{color:blue} #y{color:green}"
                             "p.x{margin:1px 2px} @media (max-width:500px){p{color:pink}}"], breite=900)
        from nemiview.nemidom import Dokument, Element
        from nemiview.nemijs import NemiJS
        dok = Dokument(NemiJS())
        html_el = Element(dok, "html")
        p = Element(dok, "p", {"class": "x"})
        p.eltern = html_el
        html_el.kinder.append(p)
        s_html = k.stil(html_el, {})
        s = k.stil(p, s_html)
        self.assertEqual("blue", s["color"])
        self.assertEqual("2px", s["margin-right"])
        p.attrs.pop("class")
        self.assertEqual("#123456", k.stil(p, s_html)["color"])
        k.breite = 400
        self.assertEqual("pink", k.stil(p, s_html)["color"])

    def test_farben(self):
        self.assertEqual((255, 0, 0, 255), nemicss.farbe("red"))
        self.assertEqual((17, 34, 51, 128), nemicss.farbe("#11223380"))
        self.assertEqual((0, 255, 0, 255), nemicss.farbe("hsl(120, 100%, 50%)"))
        self.assertIsNone(nemicss.farbe("transparent"))

    def test_calc_ohne_eval(self):
        from nemiview.layout import Ctx, laenge
        ctx = Ctx(1000, 800)
        self.assertAlmostEqual(482.0, laenge("calc(50% - 2em + 2px)", 1000, 10, ctx))
        self.assertAlmostEqual(30.0, laenge("calc((1px + 2px) * 10)", None, 16, ctx))
        self.assertAlmostEqual(-4.0, laenge("calc(-8px / 2)", None, 16, ctx))
        for kaputt in ("calc(1px 2px)", "calc((1px)", "calc(1px / 0)", "calc(" + "(" * 40 + "1px" + ")" * 40 + ")",
                       "calc(__import__)", "calc(2px ** 3px)"):
            self.assertIsNone(laenge(kaputt, None, 16, ctx), kaputt)


class LayoutTests(Basis):
    def test_wuerfel_zentriert(self):
        lz = self.app("wuerfel")
        L = Layout(lz.dok, lz.kaskade, 800, 600)
        L.rechnen()
        x, y, w, h = lz.dok.finden("main").box
        self.assertAlmostEqual(360, w, delta=1)                      # width: min(360px, 92vw)
        self.assertAlmostEqual(400, x + w / 2, delta=2)              # waagerecht mittig (Grid, place-items)
        self.assertAlmostEqual(300, y + h / 2, delta=3)              # senkrecht mittig (min-height: 100vh)
        self.assertIs(lz.dok.nach_id("los"), L.treffer(x + w / 2, lz.dok.nach_id("los").box[1] + 5))

    def test_flex_gap_und_umbruch(self):
        lz = self.app("flex", html="""<body style="margin:0"><div id="r" style="display:flex;gap:10px;width:300px;
            flex-wrap:wrap"><div style="width:100px;height:20px"></div><div style="width:100px;height:20px"></div>
            <div style="width:100px;height:20px"></div></div></body>""", css="")
        L = Layout(lz.dok, lz.kaskade, 800, 600)
        L.rechnen()
        kinder = lz.dok.nach_id("r").elemente()
        self.assertEqual([0, 110], [round(k.box[0]) for k in kinder[:2]])
        self.assertEqual(30, round(kinder[2].box[1]))                 # dritte Box bricht um (100+10+100+10+100 > 300)


class PruefenTests(Basis):
    def test_meldet_fehler_und_internet(self):
        from nemiview.pruefen import pruefen
        ok, text = pruefen(self.apps / "wuerfel")
        self.assertTrue(ok, text)
        ordner = self.apps / "kaputt"
        ordner.mkdir()
        (ordner / "index.html").write_text('<body><script src="app.js"></script>'
                                           '<img src="https://example.org/a.png"><video></video></body>',
                                           encoding="utf-8")
        (ordner / "app.js").write_text("let x = ;", encoding="utf-8")
        ok, text = pruefen(ordner)
        self.assertFalse(ok)
        self.assertIn("app.js", text)
        self.assertIn("Internet", text)
        self.assertIn("<video>", text)

    def test_werkzeug_registriert(self):
        import actions
        import modes
        self.assertIn("app_pruefen", actions.ACTIONS)
        self.assertIn("app_pruefen", modes.READ_TOOLS)


class ZeichnenUndFensterTests(Basis):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("ohne PySide6")
        cls.qt = QApplication.instance() or QApplication([])

    def test_bild_hat_die_farben_der_app(self):
        from PySide6.QtGui import QImage, QPainter
        from nemiview.zeichnen import Maler, QtMesser
        lz = self.app("farbmischer")
        L = Layout(lz.dok, lz.kaskade, 700, 560, QtMesser())
        L.rechnen()
        img = QImage(700, 560, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        Maler(L).malen(p)
        p.end()
        x, y, w, h = lz.dok.nach_id("ergebnis").box
        self.assertEqual("#c5a880", img.pixelColor(int(x + w / 2), int(y + h / 2)).name())
        self.assertEqual("#0f1117", img.pixelColor(5, 5).name())

    def test_debugai_ablauf_ueber_die_leitung(self):
        from PySide6.QtCore import QObject, Signal
        from nemiview.fenster import AppFenster

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, a):
                self.gesendet.append(a)

        leitung = Leitung()
        f = AppFenster(self.apps, "wuerfel", self.apps / "_daten", leitung)
        f.resize(900, 650)
        f.ansicht._rechnen()
        f.debug_auf()
        f.debug.wunsch.setPlainText("Der Knopf soll gelb werden")
        f.zeigen_starten()
        self.assertTrue(f.ansicht.zeige_modus)
        f._gezeigt(f.ansicht.lz.dok.nach_id("los"))
        f.schicken(True)
        auftrag = leitung.gesendet[-1]
        self.assertEqual(("zeigen", "wuerfel", "#los"), (auftrag["op"], auftrag["app"], auftrag["element"]["selektor"]))
        leitung.ereignis.emit({"t": "confirm", "id": 7, "question": "Datei ändern?", "options": [["yes", "Ja"]],
                               "app": "wuerfel"})
        self.assertEqual(1, f.debug.fragen_bereich.count())
        (self.apps / "wuerfel" / "style.css").write_text("button{background:yellow}", encoding="utf-8")
        leitung.ereignis.emit({"t": "debug", "geaendert": True, "app": "wuerfel"})
        self.assertFalse(f.laeuft)
        knopf = f.ansicht.lz.dok.nach_id("los")
        f.ansicht._rechnen()
        self.assertEqual("yellow", knopf.stil.get("background-color"))
        vorher = f.debug.fragen_bereich.count()
        leitung.ereignis.emit({"t": "confirm", "id": 8, "question": "Fremd?", "options": [["yes", "Ja"]]})
        self.assertEqual(vorher, f.debug.fragen_bereich.count())          # kein laufender Auftrag: keine Rückfrage
        f.close()

    def test_store_kacheln_und_neue_app(self):
        from unittest import mock
        from PySide6.QtCore import QObject, Signal
        from nemiview import fenster as F

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, a):
                self.gesendet.append(a)

        leitung = Leitung()
        s = F.StoreFenster(self.apps, self.apps / "_daten", leitung)
        kacheln = [s.raster.itemAt(i).widget() for i in range(s.raster.count())]
        self.assertEqual(["Farbmischer", "Würfel", "Neue App"], [k.titel for k in kacheln])
        self.assertTrue(kacheln[-1].plus)
        with mock.patch.object(F.QInputDialog, "getMultiLineText", return_value=("Ein Notizblock", True)):
            s.neue_app()
        self.assertEqual({"op": "neue_app", "wunsch": "Ein Notizblock"}, leitung.gesendet[-1])
        leitung.ereignis.emit({"t": "confirm", "id": 3, "question": "Datei anlegen?", "options": [["yes", "Ja"]]})
        self.assertEqual(1, s.bau_fragen.count())
        leitung.ereignis.emit({"t": "answer", "delta": "fremd", "app": "wuerfel"})     # anderer Auftrag
        self.assertNotIn("fremd", s.bau_log.toPlainText())
        neu = self.apps / "notiz"
        shutil.copytree(self.apps / "wuerfel", neu)
        leitung.ereignis.emit({"t": "neu_fertig", "neu": ["notiz"], "app": F.NEU_APP})
        self.assertFalse(s.baut)
        self.assertEqual(4, s.raster.count())
        with mock.patch.object(F.QMessageBox, "question", return_value=F.QMessageBox.StandardButton.No):
            s.entfernen("notiz")                                       # Nein: nichts geschickt
        self.assertEqual("neue_app", leitung.gesendet[-1]["op"])
        with mock.patch.object(F.QMessageBox, "question", return_value=F.QMessageBox.StandardButton.Yes):
            s.entfernen("notiz")
        self.assertEqual({"op": "entfernen", "app": "notiz"}, leitung.gesendet[-1])
        shutil.rmtree(neu)
        leitung.ereignis.emit({"t": "entfernt", "app": "notiz", "ok": True, "text": "im Papierkorb"})
        self.assertEqual(3, s.raster.count())
        s.close()

    def test_kachel_name_hoechstens_zwei_zeilen(self):
        from PySide6.QtGui import QFont, QFontMetrics
        from nemiview.fenster import Kachel
        fm = QFontMetrics(QFont("Segoe UI", 10))
        zeilen = Kachel.zeilen(fm, "Ein sehr langer Name für eine kleine App mit vielen Wörtern", 100)
        self.assertLessEqual(len(zeilen), 2)
        self.assertTrue(all(fm.horizontalAdvance(z) <= 100 for z in zeilen))


if __name__ == "__main__":
    unittest.main()
