"""NemiJS: Sprache (Ausdrücke, Zerlegung, Klassen, async, reguläre Ausdrücke, JSON …) und Grenzen
(Rechenbremse, Tiefe, Textlänge, kein Netz/eval/Prototyp-Verschmutzung)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nemiview.nemijs import Grenze, JSFehler, NemiJS, zu_text   # noqa: E402
from nemiview.nemijs_parser import SyntaxFehler, parsen          # noqa: E402


def rechnen(code: str, js: NemiJS | None = None) -> str:
    """Wert des letzten Ausdrucks als JS-Text."""
    js = js or NemiJS()
    js.ausfuehren("var __e = (" + code + ");")
    return zu_text(js.global_scope.vars["__e"])


class SpracheTests(unittest.TestCase):
    def pruefe(self, faelle):
        for code, soll in faelle:
            with self.subTest(code=code):
                self.assertEqual(soll, rechnen(code))

    def test_ausdruecke(self):
        self.pruefe([("1 + 2 * 3", "7"), ("(1 + 2) * 3", "9"), ("2 ** 3 ** 2", "512"), ("'a' + 1 + 2", "a12"),
                     ("7 % 3 + -1", "0"), ("10 / 4", "2.5"), ("1 / 0", "Infinity"), ("0.1 + 0.2 === 0.3", "false"),
                     ("(5 >>> 1) + (1 << 4) + (~0)", "17"), ("0 == '' && null == undefined && NaN !== NaN", "true"),
                     ("typeof null + typeof undefined + typeof (() => 1)", "objectundefinedfunction"),
                     ("null ?? 'x'", "x"), ("0 || 'y'", "y"), ("1 && 2", "2"), ("[1,2] + ''", "1,2"),
                     ("`a${1 + 1}b${'c'.toUpperCase()}`", "a2bC")])

    def test_zerlegen_und_spread(self):
        self.pruefe([("(() => { const {a, b: [c = 5]} = {a: 1, b: []}; return a + c })()", "6"),
                     ("(() => { let [p, ...q] = [1, 2, 3]; return q.length })()", "2"),
                     ("(({seiten = 6, anzahl = 1} = {}) => seiten * anzahl)({anzahl: 2})", "12"),
                     ("Math.max(...[1, 9, 3])", "9"), ("({...{a: 1}, b: 2}).a + ({...{a: 1}, b: 2}).b", "3"),
                     ("(() => { let a = 1, b = 2; [a, b] = [b, a]; return a * 10 + b })()", "21")])

    def test_schleifen_und_sprung(self):
        self.pruefe([("(() => { let s = 0; for (let i = 0; i < 10; i++) { if (i % 2) continue; s += i } return s })()", "20"),
                     ("(() => { let s = ''; for (const k in {x: 1, y: 2}) s += k; return s })()", "xy"),
                     ("(() => { let n = 0; while (true) { if (++n > 4) break } return n })()", "5"),
                     ("(() => { switch (3) { case 1: return 'a'; case 3: return 'drei'; default: return 'x' } })()", "drei"),
                     ("(() => { const f = []; for (let i = 0; i < 3; i++) f.push(() => i); return f.map(g => g()).join('') })()",
                      "012")])

    def test_klassen(self):
        self.pruefe([("new (class { constructor(x) { this.x = x } doppelt() { return this.x * 2 } })(4).doppelt()", "8"),
                     ("(() => { class T { n = 3; static k = 2; m() { return this.n + T.k } } return new T().m() })()", "5"),
                     ("(() => { class P { constructor(a) { this.a = a } hi() { return 'P' + this.a } } "
                      "class C extends P { constructor() { super(7) } hi() { return 'C' + super.hi() } } return new C().hi() })()",
                      "CP7"),
                     ("(() => { class E extends Error { } try { throw new E('kaputt') } catch (e) { return e.message } })()",
                      "kaputt")])

    def test_fehler(self):
        self.pruefe([("(() => { try { null.x } catch (e) { return e instanceof TypeError } })()", "true"),
                     ("(() => { try { throw new RangeError('r') } catch ({name, message}) { return name + message } })()",
                      "RangeErrorr"),
                     ("(() => { let s = ''; try { s += 'a' } finally { s += 'b' } return s })()", "ab")])

    def test_eingebaut(self):
        self.pruefe([("[3, 1, 2].sort((a, b) => a - b).join('')", "123"), ("[1, [2, [3]]].flat(2).length", "3"),
                     ("'#ff8800'.match(/\\w\\w/g).map(h => parseInt(h, 16)).join(',')", "255,136,0"),
                     ("'a-b-c'.replace(/-/g, '+')", "a+b+c"), ("'2026-10-25'.replace(/(\\d+)-(\\d+)-(\\d+)/, '$3.$2.$1')",
                                                               "25.10.2026"),
                     ("JSON.stringify({a: [1, 'x', true, null], b: {c: 2.5}})", '{"a":[1,"x",true,null],"b":{"c":2.5}}'),
                     ("JSON.parse('{\"x\": [1, 2]}').x[1]", "2"), ("(0.125).toFixed(2)", "0.13"), ("(255).toString(16)", "ff"),
                     ("'7'.padStart(3, '0')", "007"), ("Object.entries({a: 1, b: 2}).map(([k, v]) => k + v).join()", "a1,b2"),
                     ("[...new Set([1, 1, 2])].length", "2"), ("new Map([[1, 'a']]).get(1)", "a"),
                     ("Array.from({length: 3}, (_, i) => i * i).join()", "0,1,4"), ("new Date(2026, 9, 25).getMonth()", "9"),
                     ("'Würfel'.length", "6"), ("Number('12px')", "NaN"), ("parseInt('12px')", "12")])

    def test_async_und_zeitgeber(self):
        js = NemiJS()
        js.ausfuehren("var v; (async () => { v = await new Promise(r => setTimeout(() => r(42), 100)) })();")
        self.assertEqual("42", zu_text(js.global_scope.vars["v"]))
        js.ausfuehren("var log = []; Promise.resolve(1).then(x => log.push('p' + x)); log.push('s');")
        self.assertEqual("s,p1", zu_text(js.global_scope.vars["log"]))       # Mikroaufgabe nach dem Skript
        js.ausfuehren("var n = 0; var id = setInterval(() => { if (++n >= 3) clearInterval(id) }, 10);")
        js.zeitgeber_laufen(1000)
        self.assertEqual("3", zu_text(js.global_scope.vars["n"]))

    def test_syntax_ohne_semikolon_und_fehlerzeile(self):
        parsen("let a = 1\nlet b = a\n  + 2\nconst f = x => x\nf(b)")
        with self.assertRaises(SyntaxFehler) as kontext:
            parsen("let x = 1\nlet = = 2")
        self.assertEqual(2, kontext.exception.zeile)


class GrenzenTests(unittest.TestCase):
    def test_rechenbremse_tiefe_text(self):
        for code in ("while (true) {}", "function r() { return r() } r()", "let s = 'x'; while (true) s += s",
                     "let a = []; while (true) a.push(1)", "for (;;) { let x = [1, 2, 3].map(y => y) }"):
            with self.subTest(code=code), self.assertRaises(Grenze):
                NemiJS(max_schritte=300_000).ausfuehren(code)

    def test_nichts_von_draussen(self):
        for code in ("fetch('http://example.org')", "new XMLHttpRequest()", "new WebSocket('ws://x')", "eval('1')",
                     "Function('return 1')()", "require('fs')", "import('x')"):
            with self.subTest(code=code), self.assertRaises(JSFehler):
                NemiJS().ausfuehren(code)

    def test_keine_prototyp_verschmutzung_und_kein_python(self):
        js = NemiJS()
        with self.assertRaises(JSFehler):                     # __proto__ gibt es nicht: nichts zum Verbiegen
            js.ausfuehren("var a = {}; a.__proto__.boese = 1;")
        js.ausfuehren("var b = {}; b.__proto__ = {boese: 1}; var r = b.boese;"
                      "var c = JSON.parse('{\"__proto__\": {\"x\": 1}}'); var d = ({}).x;")
        self.assertEqual("undefined", zu_text(js.global_scope.vars["r"]))
        self.assertEqual("undefined", zu_text(js.global_scope.vars["d"]))
        for code in ("[].__class__", "''.__class__", "(() => 1).__globals__", "({}).__dict__", "Math.__module__"):
            with self.subTest(code=code):
                js.ausfuehren(f"var p = {code};")
                self.assertEqual("undefined", zu_text(js.global_scope.vars["p"]))

    def test_settimeout_mit_text_wird_nicht_ausgefuehrt(self):
        with self.assertRaises(JSFehler):
            NemiJS().ausfuehren("setTimeout('alert(1)', 0)")


class ZahlenUndDatumTests(unittest.TestCase):
    def pruefe(self, faelle):
        for code, soll in faelle:
            with self.subTest(code=code):
                self.assertEqual(soll, rechnen(code))

    def test_fehlende_oder_unendliche_zahlen(self):
        self.pruefe([("(1.6).toFixed()", "2"), ("(1.5).toPrecision()", "1.5"), ("'x'.padStart()", "x"),
                     ("'abc'.charAt()", "a"), ("[1, 2, 3].at()", "1"), ("'abc'.repeat()", ""),
                     ("[1, [2, [3, [4]]]].flat(Infinity).length", "4"), ("[1, 2, 3].splice(1, NaN).length", "0"),
                     ("[1, 2][NaN]", "undefined"), ("[1, 2][Infinity]", "undefined"), ("'abc'.substr(NaN, 2)", "ab"),
                     ("(Infinity).toString(2)", "Infinity"),
                     ("Math.exp(1000)", "Infinity"), ("Math.sinh(-1000)", "-Infinity")])

    def test_map_set_size_und_suche_ab_index(self):
        self.pruefe([("new Map([[1, 'a'], [2, 'b']]).size", "2"),
                     ("(() => { const s = new Set([1, 1, 2]); s.add(3); return s.size })()", "3"),
                     ("[1, 2, 1, 2].indexOf(1, 1)", "2"), ("[1, 2, 1, 2].lastIndexOf(2, 2)", "1"),
                     ("[1, 2, 1].indexOf(1, -1)", "2"), ("[1, 2, 3].includes(1, 1)", "false"),
                     ("[...'a-b-c'.matchAll('-')].length", "2")])

    def test_datum(self):
        self.pruefe([("new Date(2020, 0, 10) > new Date(2020, 0, 2)", "true"),
                     ("new Date(2020, 0, 2) > new Date(2020, 0, 10)", "false"),
                     ("new Date(1960, 5, 15).getFullYear()", "1960"),
                     ("new Date(-86400000).toISOString()", "1969-12-31T00:00:00.000Z"),
                     ("Date.parse('1900-01-01')", "-2208988800000"), ("new Date('1950-03-04').getUTCDate()", "4"),
                     ("String(new Date(NaN))", "Invalid Date"), ("new Date(2020, NaN).getTime()", "NaN"),
                     ("new Date('2020-01-01T05:06:07Z').getUTCHours()", "5"),
                     ("(() => { const d = new Date(2020, 0, 31); d.setMonth(1); return d.getMonth() + '/' + d.getDate() })()",
                      "2/2"),
                     ("(() => { const d = new Date(2020, 0, 1); d.setDate(d.getDate() + 40); return d.getMonth() })()", "1"),
                     ("(() => { const d = new Date(2020, 5, 1, 10); d.setHours(25); return d.getDate() + ':' + d.getHours() })()",
                      "2:1"),
                     ("(() => { const d = new Date(0); d.setTime(86400000); return d.getUTCDate() })()", "2"),
                     ("(() => { const d = new Date(2020, 0, 1); d.setFullYear(1955); return d.getFullYear() })()", "1955")])


class VarUndAusdrueckeTests(unittest.TestCase):
    def pruefe(self, faelle):
        for code, soll in faelle:
            with self.subTest(code=code):
                self.assertEqual(soll, rechnen(code))

    def test_var(self):
        self.pruefe([("(() => { var x = 1; return (function () { var x; return x })() })()", "undefined"),
                     ("(function () { return v; var v = 1 })()", "undefined"),
                     ("(() => { for (var k in {a: 1, b: 2}) {} return k })()", "b"),
                     ("(() => { for (var w of [1, 2, 3]) {} return w })()", "3")])

    def test_aendern_wertet_ziel_einmal_aus(self):
        self.pruefe([("(() => { const z = [0, 0, 0], a = [1, 2]; let i = 0; z[a[i++]]++; return z.join('') + i })()",
                      "0101")])

    def test_surrogate_und_escapes(self):
        bs = chr(92)
        self.pruefe([(f"'{bs}uD83D{bs}uDE00' === '{bs}u{{1F600}}'", "true"),
                     (f"`{bs}uD83D{bs}uDE00`.length", "1"),
                     ("String.fromCharCode(0xD83D, 0xDE00) === String.fromCodePoint(0x1F600)", "true")])
        for code in (f"'{bs}xZZ'", f"'{bs}u12'", f"'{bs}u{{110000}}'", f"'{bs}u{{12'"):
            with self.subTest(code=code), self.assertRaises(SyntaxFehler):
                parsen(code)

    def test_lange_unaere_kette(self):
        with self.assertRaises(SyntaxFehler):
            parsen("!" * 5000 + "x")

    def test_session_storage_ohne_platte(self):
        js = NemiJS()
        gespeichert = []
        js.storage_geaendert = lambda d: gespeichert.append(dict(d))
        js.ausfuehren("sessionStorage.setItem('a', '1'); var r = sessionStorage.getItem('a') + localStorage.length;"
                      "var k = localStorage.key();")
        self.assertEqual("10", zu_text(js.global_scope.vars["r"]))
        self.assertEqual("null", zu_text(js.global_scope.vars["k"]))
        self.assertEqual([], gespeichert)


if __name__ == "__main__":
    unittest.main()
