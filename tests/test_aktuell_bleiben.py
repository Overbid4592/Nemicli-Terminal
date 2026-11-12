"""Tests: Coding-Assistent bleibt aktuell – Versions-Blick, Nachschlagen in der Installation, ruff,
Veraltet-Meldung, Sofort-Check, Paket-Info, Seite ansehen, Offline-Doku, Werkzeug-Suche.

python -m unittest tests.test_aktuell_bleiben
"""

import io
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions  # noqa: E402
import coding  # noqa: E402
import doku  # noqa: E402
import modes  # noqa: E402
import paketinfo  # noqa: E402
import persona  # noqa: E402
import pyumgebung  # noqa: E402
import seite  # noqa: E402
import sofortcheck  # noqa: E402

RUFF = pyumgebung.ruff_bin() is not None


class Projekt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        pyumgebung.vergessen()

    def tearDown(self):
        self.tmp.cleanup()
        pyumgebung.vergessen()


class Umgebung(Projekt):
    def test_verlangte_pakete_aus_requirements_und_pyproject(self):
        (self.p / "requirements.txt").write_text("httpx>=0.27  # Netz\n-e .\nnumpy\n", encoding="utf-8")
        (self.p / "pyproject.toml").write_text('[project]\ndependencies = ["rich>=13", "httpx"]\n', encoding="utf-8")
        self.assertEqual(["httpx", "numpy", "rich"], pyumgebung.verlangte_pakete(self.p))

    def test_importe_ohne_eigene_module_und_venv(self):
        (self.p / "main.py").write_text("import numpy as np\nfrom hilfe import x\nimport os.path\n", encoding="utf-8")
        (self.p / "hilfe.py").write_text("x = 1\n", encoding="utf-8")
        (self.p / "venv" / "Lib").mkdir(parents=True)
        (self.p / "venv" / "Lib" / "fremd.py").write_text("import gibtsnicht\n", encoding="utf-8")
        self.assertEqual(["numpy", "os"], pyumgebung.importe(self.p))

    def test_umgebung_aus_dem_echten_python(self):
        (self.p / "requirements.txt").write_text("numpy\ngibts-nicht-xyz\n", encoding="utf-8")
        (self.p / "main.py").write_text("import json\nimport numpy\nimport fehltmodul_xyz\n", encoding="utf-8")
        text = pyumgebung.umgebung_text(self.p)
        self.assertRegex(text, r"Python 3\.\d+")
        self.assertRegex(text, r"numpy \d")
        self.assertIn("NICHT installiert: fehltmodul_xyz, gibts-nicht-xyz", text)
        self.assertRegex(pyumgebung.ziel_version(self.p), r"^py3\d+$")

    def test_projekt_venv_ueber_sein_basis_python(self):
        venv = self.p / "venv"
        (venv / "Lib" / "site-packages").mkdir(parents=True)
        (venv / "Scripts").mkdir()
        (venv / "Scripts" / "python.exe").write_bytes(b"")              # wird nie gestartet
        basis = Path(sys.base_prefix) / "python.exe"
        (venv / "pyvenv.cfg").write_text(f"home = {basis.parent}\n", encoding="utf-8")
        self.assertEqual(basis, pyumgebung.python_fuer(self.p)[0])
        self.assertEqual(venv / "Lib" / "site-packages", pyumgebung._SITE[str(basis)])

    def test_basis_python_im_projekt_zaehlt_nicht(self):
        venv = self.p / "venv"
        (venv / "Lib" / "site-packages").mkdir(parents=True)
        (self.p / "boese").mkdir()
        (self.p / "boese" / "python.exe").write_bytes(b"")
        (venv / "pyvenv.cfg").write_text(f"home = {self.p / 'boese'}\n", encoding="utf-8")
        self.assertNotEqual(self.p / "boese" / "python.exe", pyumgebung.python_fuer(self.p)[0])


class Nachschlagen(Projekt):
    def test_signatur_doku_version_aus_der_installation(self):
        text, ok = pyumgebung.nachschlagen("pathlib.Path.glob", self.p)
        self.assertTrue(ok, text)
        self.assertIn("Signatur: glob(self, pattern", text)
        self.assertIn("Standardbibliothek von Python 3.", text)

    def test_tippfehler_mit_vorschlag(self):
        text, ok = pyumgebung.nachschlagen("os.getcwdu", self.p)
        self.assertFalse(ok)
        self.assertIn("getcwd", text)

    def test_nur_gueltige_namen(self):
        for name in ("os; import shutil", "1abc", "", "a" * 300):
            self.assertFalse(pyumgebung.nachschlagen(name, self.p)[1], name)

    def test_projektdateien_ueberdecken_keine_bibliothek(self):
        for name in ("json.py", "pathlib.py", "importlib.py"):
            (self.p / name).write_text('raise SystemExit("PROJEKTDATEI AUSGEFUEHRT")\n', encoding="utf-8")
        text, ok = pyumgebung.nachschlagen("pathlib.Path", self.p)
        self.assertTrue(ok, text)
        self.assertIn("Standardbibliothek", text)
        self.assertNotIn("fehler", pyumgebung.umgebung(self.p, frisch=True))

    def test_werkzeug_rahmt_die_doku_ein(self):
        res = actions._api_nachschlagen({"name": "json.dumps", "projekt": str(self.p)})
        self.assertTrue(res.ok)
        self.assertIn("AUSGABE von", res.text)


class Veraltet(unittest.TestCase):
    def test_warnungen_aus_der_ausgabe(self):
        aus = ("a.py:3: DeprecationWarning: datetime.datetime.utcnow() is deprecated\n"
               "a.py:3: DeprecationWarning: datetime.datetime.utcnow() is deprecated\n"
               "b.py:9: FutureWarning: The default of observed=False is deprecated\nfertig\n")
        self.assertEqual(2, len(pyumgebung.veraltet_aus(aus)))
        self.assertIn("Modernisiere", pyumgebung.veraltet_hinweis(aus))
        self.assertEqual("", pyumgebung.veraltet_hinweis("alles gut"))

    def test_befehl_haengt_den_hinweis_im_coding_an(self):
        roh = actions.ActionResult("x.py:1: DeprecationWarning: alt\n", ok=True, returncode=0)
        with mock.patch.object(actions, "_befehl_roh", return_value=roh), \
                mock.patch.object(coding, "aktiv", return_value=True), \
                mock.patch.object(pyumgebung, "vergessen") as vergessen:
            res = actions._befehl({"befehl": "venv\\Scripts\\python.exe -m pip install rich"})
        self.assertIn("⚠️ Veraltet gemeldet", res.text)
        vergessen.assert_called_once()
        with mock.patch.object(actions, "_befehl_roh", return_value=roh), \
                mock.patch.object(coding, "aktiv", return_value=False):
            self.assertNotIn("Veraltet", actions._befehl({"befehl": "python x.py"}).text)


@unittest.skipUnless(RUFF, "ruff fehlt")
class Ruff(Projekt):
    def test_findet_namen_und_alten_stil(self):
        f = self.p / "alt.py"
        f.write_text("from typing import List\ndef f(x: List[int]):\n    return y\n", encoding="utf-8")
        text, n = pyumgebung.ruff_pruefen([f], self.p, zielversion="py312")
        self.assertGreaterEqual(n, 2)
        self.assertIn("F821", text)
        self.assertIn("UP", text)

    def test_werkzeug_code_pruefen(self):
        (self.p / "ok.py").write_text("print('hallo')\n", encoding="utf-8")
        res = actions._code_pruefen({"pfad": str(self.p)})
        self.assertTrue(res.ok)
        self.assertIn("keine Funde", res.text)


class Sofortcheck(Projekt):
    def test_html_tags_verweise_und_ids(self):
        (self.p / "app.js").write_text('document.getElementById("knopf");\n', encoding="utf-8")
        html = ('<html><head><script src="app.js"></script><link href="fehlt.css" rel="stylesheet"></head>\n'
                '<body><div id="box"><span>offen\n</div>\n<ul><li>a<li>b</ul><a href="#oben">x</a>'
                '<a href="https://example.com">y</a></body></html>\n')
        funde = sofortcheck.pruefen(self.p / "index.html", html)
        self.assertIn("<span> wird nicht geschlossen", funde)
        self.assertIn("'fehlt.css'", funde)
        self.assertIn("ID 'knopf'", funde)
        self.assertNotIn("<li>", funde)
        self.assertNotIn("example.com", funde)

    def test_klammern_css_js_mit_kommentaren_und_strings(self):
        self.assertEqual("", sofortcheck.pruefen(self.p / "a.js", 'const s = "}"; // {\nf(`${x}`);\n/* ( */\n'))
        self.assertIn("Zeile 2", sofortcheck.pruefen(self.p / "b.js", "function f() {\n  return [1, 2;\n}\n"))
        self.assertIn("'{' wird nicht geschlossen", sofortcheck.pruefen(self.p / "c.css", "a { color: red;\n"))

    def test_regulaere_ausdruecke_sind_keine_klammern(self):
        js = 's = s.replace(/\\(/g, "");\nconst r = /[)]/;\nif (a / b > 2) { return /\\{/.test(x); }\n'
        self.assertEqual("", sofortcheck.pruefen(self.p / "r.js", js))
        self.assertIn("Zeile 2", sofortcheck.pruefen(self.p / "d.js", "let q = (a + b) / 2;\nf(q;\n"))

    def test_python_und_json(self):
        self.assertIn("Syntaxfehler", sofortcheck.pruefen(self.p / "a.py", "def f(:\n  pass\n"))
        self.assertIn("Zeile 1", sofortcheck.pruefen(self.p / "a.json", '{"a": 1,}'))
        self.assertEqual("", sofortcheck.pruefen(self.p / "a.txt", "def f(:"))

    @unittest.skipUnless(RUFF, "ruff fehlt")
    def test_python_undefinierter_name(self):
        self.assertIn("Zeile 2: Undefined name `z` (F821)", sofortcheck.pruefen(self.p / "a.py", "def g():\n    return z\n"))

    def test_schreiben_meldet_sofort(self):
        ziel = self.p / "kaputt.py"
        with mock.patch.object(actions, "_guard", return_value=None):
            text = actions._datei_schreiben({"pfad": str(ziel), "inhalt": "if True\n    pass\n"})
        self.assertIn("Sofort-Check kaputt.py", text)
        with mock.patch.object(actions, "_guard", return_value=None):
            text = actions._datei_bearbeiten({"pfad": str(ziel), "suchen": "if True", "ersetzen": "if True:"})
        self.assertNotIn("Sofort-Check", text)


class PaketInfo(Projekt):
    PYPI = {"info": {"name": "demo", "version": "2.0.0", "requires_python": ">=3.10",
                     "project_urls": {"Changelog": "https://example.com/changes"}},
            "releases": {"2.0.0": [{"upload_time": "2026-09-01T10:00:00"}]}}

    def info(self, installiert, funde=None):
        with mock.patch.object(paketinfo, "installiert", return_value=installiert):
            return paketinfo.info("demo", self.p, pypi=lambda n: self.PYPI,
                                  pruefen=lambda d: {"demo": funde} if funde else {})

    def test_aktuell_veraltet_und_luecken(self):
        text, ok = self.info("2.0.0")
        self.assertTrue(ok)
        self.assertIn("neueste Version 2.0.0 (2026-09-01)", text)
        self.assertIn("aktuell", text)
        self.assertIn("Changelog: https://example.com/changes", text)
        import luecken
        luecke = luecken.Luecke(id="GHSA-1", cve="CVE-2026-1", schwere="hoch", behoben="1.9.1")
        text, _ = self.info("1.8.0", [luecke])
        self.assertIn("es gibt 2.0.0", text)
        self.assertIn("CVE-2026-1 (hoch) – behoben in 1.9.1", text)
        self.assertIn("nicht installiert", self.info("")[0])

    def test_unbekannt_und_ungueltig(self):
        def fehlt(name):
            raise LookupError("'x' gibt es auf PyPI nicht.")
        self.assertFalse(paketinfo.info("x", None, pypi=fehlt)[1])
        self.assertFalse(paketinfo.info("../etc", None)[1])


class SeiteAnsehen(Projekt):
    def test_nur_lokale_dateien(self):
        self.assertIsNone(seite.adresse("https://example.com")[0])
        self.assertIn("HTML-Datei", seite.adresse("http://localhost:8000/")[1])
        (self.p / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
        self.assertTrue(seite.adresse(str(self.p))[0].startswith("file:///"))
        self.assertIsNone(seite.adresse(str(self.p / "fehlt.html"))[0])

    def test_bild_und_konsolenfehler_mit_nemiview(self):
        (self.p / "t.html").write_text('<h1>Hallo</h1><script>console.log("ok"); fehltFunktion();</script>',
                                       encoding="utf-8")
        res = actions._seite_ansehen({"ziel": str(self.p / "t.html"), "breite": 400, "hoehe": 300})
        self.assertTrue(res.ok, res.text)
        self.assertTrue(Path(res.bilder[0]).is_file())
        self.assertIn("fehltFunktion", res.text)
        self.assertIn("· ok", res.text)


class OfflineDoku(Projekt):
    def setUp(self):
        super().setUp()
        self.flick = mock.patch.object(doku, "ordner", return_value=self.p / "Doku")
        self.flick.start()

    def tearDown(self):
        self.flick.stop()
        super().tearDown()

    def test_rst_und_md_zerlegen(self):
        rst = ("pathlib --- Pfade\n*****************\n\nEinleitung " + "x " * 30 + "\n\nPath.glob\n=========\n\n"
               "Path.glob(pattern) yields files " + "y " * 30 + "\n")
        teile = doku.abschnitte_rst(rst, "library/pathlib.txt")
        self.assertEqual(["pathlib --- Pfade", "Path.glob"], [t for t, _ in teile])
        md = ("---\ntitle: Array.prototype.flatMap()\nslug: x\n---\n\n{{JSRef}}\nThe {{jsxref(\"Array\")}} method "
              + "z " * 30 + "\n\n## Syntax\n\nflatMap(callbackFn) " + "w " * 30 + "\n\n## See also\n\n- links " + "v " * 30)
        teile = doku.abschnitte_md(md, "javascript/array/flatmap")
        self.assertEqual(["Array.prototype.flatMap()", "Array.prototype.flatMap() – Syntax"], [t for t, _ in teile])
        self.assertIn("`Array` method", teile[0][1])
        self.assertNotIn("JSRef", teile[0][1])

    def test_laden_suchen_lesen_loeschen(self):
        self.assertIn("Noch keine Offline-Doku", doku.suchen("glob"))
        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w") as z:
            z.writestr("python-3.12-docs-text/library/pathlib.txt",
                       "pathlib\n*******\n\nPath.glob(pattern) liefert passende Dateien " + "a " * 40)
            z.writestr("python-3.12-docs-text/library/json.txt", "json\n****\n\njson.dumps(obj) " + "b " * 40)
        n = doku._speichern("python", "3.12", doku.python_eintraege(puffer.getvalue()))
        self.assertEqual(2, n)
        treffer = doku.suchen("Path glob")
        self.assertIn("python 3.12 · pathlib (library/pathlib.txt)", treffer)
        self.assertIn("«glob»", treffer)
        import re
        rid = re.search(r"\[(\d+)\]", treffer).group(1)
        self.assertIn("Path.glob(pattern)", doku.lesen(rid))
        self.assertIn("nicht geladen", doku.suchen("glob", "mdn"))
        self.assertIn("python 3.12 (2 Abschnitte)", doku.status_text())
        self.assertIn("entfernt", doku.loeschen("python"))
        self.assertEqual({}, doku.stand())

    def test_mdn_nur_die_web_bereiche(self):
        tar = self.p / "mdn.tar.gz"
        with tarfile.open(tar, "w:gz") as t:
            for name, inhalt in (("content-main/files/en-us/web/css/reference/gap/index.md",
                                  "---\ntitle: gap\n---\nThe gap CSS property " + "g " * 40),
                                 ("content-main/files/en-us/learn/intro/index.md", "---\ntitle: lernen\n---\n" + "l " * 40)):
                daten = inhalt.encode()
                info = tarfile.TarInfo(name)
                info.size = len(daten)
                t.addfile(info, io.BytesIO(daten))
        eintraege = list(doku.mdn_eintraege(tar))
        self.assertEqual([("gap", "css/reference/gap")], [(t, p) for t, p, _ in eintraege])

    def test_werkzeug(self):
        doku._speichern("python", "3.12", [("json", "library/json.txt", "json.dumps(obj) " + "b " * 30)])
        res = actions._doku_suchen({"frage": "dumps"})
        self.assertTrue(res.ok)
        self.assertIn("DATEN aus Suche", res.text)


class CodingPrompt(Projekt):
    def test_umgebung_lehren_und_ende(self):
        lehre = self.p / "lehre.md"
        lehre.write_text("# numpy 2.0: np.float_ entfernt\n\n_gelernt am x_\n\nnp.float64 statt np.float_.\n",
                         encoding="utf-8")
        with mock.patch("learn.list_skills", return_value=[(lehre, "# numpy 2.0: np.float_ entfernt")]):
            self.assertEqual(["- numpy 2.0: np.float_ entfernt: np.float64 statt np.float_."],
                             coding.lehren_zu(["numpy"]))
            self.assertEqual([], coding.lehren_zu(["requests"]))
        with mock.patch.object(pyumgebung, "umgebung_text", return_value="Python 3.12 · venv"), \
                mock.patch.object(pyumgebung, "umgebung", return_value={"relevant": {}}):
            block = coding._umgebung_block(self.p)
        self.assertIn("## Umgebung (aus der Installation gelesen", block)
        self.assertIn("api_nachschlagen", coding._WEG)
        self.assertIn("/codeend", (ROOT / "core" / "coding.py").read_text(encoding="utf-8"))


class Werkzeuge(unittest.TestCase):
    NEU = ("api_nachschlagen", "code_pruefen", "paket_info", "seite_ansehen", "doku_suchen")

    def test_angemeldet_lesend_und_beschrieben(self):
        prompt = persona.base_prompt()
        for name in self.NEU:
            self.assertIn(name, actions.ACTIONS)
            self.assertIn(name, modes.READ_TOOLS)
            self.assertIn(f"- {name}", prompt)
        self.assertIn("paket_info", actions._WEB_TOOLS)

    def test_einzelnes_werkzeug_nachschlagen(self):
        text = persona.anleitung("seite_ansehen")
        self.assertTrue(text.startswith("- seite_ansehen"))
        self.assertIn("JavaScript-Fehler", text)
        self.assertNotIn("- doku_suchen", text)
        self.assertEqual(text, persona.anleitung("werkzeug:seite_ansehen"))
        self.assertEqual("", persona.anleitung("gibtsnicht"))
        self.assertIn("oder nur EIN Werkzeug", persona.kompakt(persona.base_prompt()))


if __name__ == "__main__":
    unittest.main()
