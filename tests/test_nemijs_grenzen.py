"""NemiJS/NemiView-Grenzen: Regex-Zeitgrenze, Textlänge beim Ersetzen, repeat() im Grid, Zeitbudget der
Zeitgeber, Python-Fehler an den Einstiegen, interne Felder, localStorage-Schreiben, Surrogate im Ergebnis."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from nemiview import appjs, laufzeit, nemijs                  # noqa: E402
from nemiview.laufzeit import AppLaufzeit                     # noqa: E402
from nemiview.layout import MAX_SPUREN, Layout                # noqa: E402
from nemiview.nemijs import Grenze, JSFehler, NemiJS, zu_text  # noqa: E402


def rechnen(code: str, js: NemiJS | None = None) -> str:
    js = js or NemiJS()
    js.ausfuehren("var __e = (" + code + ");")
    return zu_text(js.global_scope.vars["__e"])


class RegexTests(unittest.TestCase):
    @unittest.skipIf(nemijs._rx is None, "Paket regex fehlt")
    def test_katastrophales_backtracking_endet_mit_grenze(self):
        t = time.monotonic()
        with self.assertRaises(Grenze):
            NemiJS().ausfuehren("/^(a|aa)+$/.test('a'.repeat(60) + 'b')")
        self.assertLess(time.monotonic() - t, nemijs.REGEX_ZEIT_S + 5)

    def test_laengengrenze_in_allen_methoden(self):
        for aufruf in ("split(/a/)", "match(/a/)", "match(/a/g)", "matchAll(/a/g)", "search(/a/)",
                       "replace(/a/g, 'b')"):
            with self.subTest(aufruf=aufruf), self.assertRaises(JSFehler):
                NemiJS().ausfuehren(f"'a'.repeat({nemijs.REGEX_MAX_EINGABE + 1}).{aufruf}")

    def test_normales_verhalten_bleibt(self):
        for code, soll in (("'baaac'.replace(/a*/g, '-')", "-b--c-"), ("'a-b-c'.replace(/-/, '+')", "a+b-c"),
                           ("'a1b22'.replace(/\\d+/g, m => '<' + m + '>')", "a<1>b<22>"),
                           ("'x-y'.replace(/(\\w)-(\\w)/, '$2-$1')", "y-x"), ("'abc'.search('c')", "2"),
                           ("'abc'.search({})", "-1"), ("'a,b'.split(/,/).length", "2"),
                           ("[...'a1a2'.matchAll(/a(\\d)/g)].map(m => m[1]).join()", "1,2"),
                           ("/\\u{5b}/u.test('[')", "true"), ("'Ab'.match(/b/i).index", "1")):
            with self.subTest(code=code):
                self.assertEqual(soll, rechnen(code))


class ErsetzenTests(unittest.TestCase):
    def test_ersatz_mit_funktion_beachtet_textgrenze(self):
        for code in ("'a'.repeat(10000).replaceAll('a', () => 'b'.repeat(1000))",
                     "'a'.repeat(10000).replace(/a/g, () => 'b'.repeat(1000))",
                     "'a'.repeat(10000).replaceAll('a', 'b'.repeat(1000))"):
            with self.subTest(code=code), self.assertRaises(Grenze):
                NemiJS().ausfuehren(code)

    def test_leeres_suchmuster_wie_js(self):
        self.assertEqual("-a-b-", rechnen("'ab'.replaceAll('', () => '-')"))
        self.assertEqual("-ab", rechnen("'ab'.replace('', () => '-')"))
        self.assertEqual("-a-b-", rechnen("'ab'.replaceAll('', '-')"))
        self.assertEqual("x1y1", rechnen("'xaya'.replaceAll('a', (m, i) => '1')"))


class InterneFelderTests(unittest.TestCase):
    def test_interna_unsichtbar_und_fest(self):
        self.assertEqual("undefined", rechnen("/a/.__re"))
        self.assertEqual("b", rechnen("(() => { const r = /a/; r.__re = 5; delete r.__re; "
                                      "return 'a'.replace(r, 'b') })()"))
        self.assertEqual("undefined", rechnen("({__daten: 1}).__daten"))
        self.assertEqual("undefined", rechnen("JSON.parse('{\"__zeit\": 1}').__zeit"))
        self.assertEqual("2", rechnen("(() => { const m = new Map([[1, 2], [3, 4]]); m.__daten = 0; "
                                      "return m.size })()"))
        self.assertEqual("true", rechnen("(() => { const d = new Date(0); d.__zeit = 'x'; "
                                         "return d.getTime() === 0 })()"))


class ZeitgeberTests(unittest.TestCase):
    SCHWER = "for (let i = 0; i < 20000; i++) {}"

    def test_headless_lauf_hat_gesamtbudget(self):
        js = NemiJS(virtuelle_zeit=True)
        js.ausfuehren(f"for (let n = 0; n < 2000; n++) setTimeout(() => {{ {self.SCHWER} }}, n);")
        t = time.monotonic()
        with self.assertRaises(Grenze):
            js.zeitgeber_laufen(10_000, frist=time.monotonic() + 0.2)
        self.assertLess(time.monotonic() - t, 3)

    def test_viewer_takt_hoert_nach_budget_auf(self):
        js = NemiJS(virtuelle_zeit=False)
        js.ausfuehren(f"var n = 0; for (let k = 0; k < 500; k++) setTimeout(() => {{ n++; {self.SCHWER} }}, 0);")
        with mock.patch.object(nemijs, "TAKT_ZEIT_S", 0.02):
            js.faellige_ausfuehren()
        erledigt = js.global_scope.vars["n"]
        self.assertGreaterEqual(erledigt, 1)
        self.assertLess(erledigt, 500)


class AppTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.basis = Path(self._tmp.name)

    def app(self, body: str) -> AppLaufzeit:
        ordner = self.basis / "Apps" / "probe"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "index.html").write_text(f"<!doctype html><html><body>{body}</body></html>", encoding="utf-8")
        return AppLaufzeit(ordner, 800, 600, speicher_ordner=self.basis / "_daten", virtuelle_zeit=True)

    def test_grid_repeat_wird_geklemmt(self):
        lz = self.app("")
        lz.laden()
        L = Layout(lz.dok, lz.kaskade, 800, 600)
        for vorlage in ("repeat(99999999999, 1fr)", "repeat(1e999, 10px)", "repeat(nan, 1fr)",
                        "repeat(auto-fill, minmax(-50px, 1fr))", "repeat(auto-fill, 0px)",
                        "repeat(500, 1fr) repeat(800, 1fr)"):
            with self.subTest(vorlage=vorlage):
                spalten = L._spalten(vorlage, 800, -10.0, 16)
                self.assertGreaterEqual(len(spalten), 1)
                self.assertLessEqual(len(spalten), MAX_SPUREN)
        self.assertEqual(3, len(L._spalten("repeat(3, 1fr)", 800, 0, 16)))

    def test_python_fehler_landet_in_der_konsole(self):
        lz = self.app("")
        lz.laden()
        self.assertIsNone(lz.sicher(lambda: int("x")))
        self.assertTrue(any("interner Fehler (ValueError" in f for f in lz.fehler))

    def test_zeitgeber_headless_mit_budget_ueber_alle_versuche(self):
        lz = self.app("<script>for (let n = 0; n < 3000; n++) setTimeout(() => { for (;;) {} }, n);</script>")
        lz.laden()
        lz.js.max_schritte = 20_000
        t = time.monotonic()
        with mock.patch.object(laufzeit, "ZEITGEBER_ZEIT_S", 0.3):
            lz.zeitgeber_laufen(10_000)
        self.assertLess(time.monotonic() - t, 5)

    def test_localstorage_schreibt_einmal_je_einstieg(self):
        lz = self.app("<script>for (let i = 0; i < 300; i++) localStorage.setItem('k' + i, 'x'.repeat(100));</script>")
        with mock.patch.object(AppLaufzeit, "_speicher_schreiben", autospec=True) as schreiben:
            lz.laden()
            self.assertEqual(1, schreiben.call_count)
            self.assertEqual(300, len(schreiben.call_args[0][1]))
            lz.sicher(lambda: lz.js.ausfuehren("localStorage.setItem('neu', '1')"))
            self.assertEqual(1, schreiben.call_count)          # gedrosselt: wartet auf den nächsten Einstieg
            lz.speicher_sichern()
            self.assertEqual(2, schreiben.call_count)

    def test_appjs_python_fehler_und_halbe_surrogate(self):
        app_js = self.basis / "app.js"
        app_js.write_text("NemiApp.funktion('f', () => ({a: '\\uD800x', b: '\\uD83D\\uDE00'}), '');",
                          encoding="utf-8")
        ok, erg = appjs.aufrufen(app_js, "f", {})
        self.assertTrue(ok)
        self.assertEqual({"a": "\ufffdx", "b": "\U0001F600"}, erg)
        with mock.patch.object(appjs, "laden", side_effect=ValueError("kaputt")):
            ok, text = appjs.aufrufen(app_js, "f", {})
        self.assertFalse(ok)
        self.assertIn("interner Fehler", text)


if __name__ == "__main__":
    unittest.main()
