"""Regex aus fremden Daten: Suchmuster der KI begrenzt, Sigma-Platzhalter linear, Werkzeugformate mit
Zeitgrenze und ohne exponentielles Muster, Feldnamen maskiert."""
import json
import random
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui", ""):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions as A                                                # noqa: E402
import werkzeugsprache as W                                        # noqa: E402
from ggufengine import ausfuellen                                 # noqa: E402
from wache import sigma as S, vorpruefer as V                      # noqa: E402


class SuchmusterDerKI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "a.txt").write_text("abc\na.c\n", encoding="utf-8")

    def test_zu_langes_muster(self):
        r = A._inhalt_suchen({"pfad": str(self.tmp), "muster": "a" * (A.SUCHE_MUSTER_MAX + 1)})
        self.assertFalse(r.ok)
        self.assertIn("zu lang", r.text)

    def test_ungueltiges_muster(self):
        r = A._inhalt_suchen({"pfad": str(self.tmp), "muster": "(offen"})
        self.assertFalse(r.ok)
        self.assertIn("ungültiges Suchmuster", r.text)

    def test_ohne_regex_woertlich(self):
        with mock.patch.dict(sys.modules, {"regex": None}):           # import regex -> ImportError
            text = str(A._inhalt_suchen({"pfad": str(self.tmp), "muster": "a.c"}))
        self.assertIn(":2: a.c", text)
        self.assertNotIn(":1: abc", text)
        self.assertIn(":1: abc", str(A._inhalt_suchen({"pfad": str(self.tmp), "muster": "a.c"})))


def _alt(soll: str, art: str, flags: int):
    """Bisheriger Vergleich (.* um das Muster, fullmatch) als Maßstab."""
    teile, i = [], 0
    while i < len(soll):
        c = soll[i]
        if c == "\\" and i + 1 < len(soll) and soll[i + 1] in "*?\\":
            teile.append(re.escape(soll[i + 1]))
            i += 2
            continue
        teile.append(".*" if c == "*" else "." if c == "?" else re.escape(c))
        i += 1
    m = "".join(teile)
    m = {"in": f".*{m}.*", "anfang": f"{m}.*", "ende": f".*{m}"}.get(art, m)
    rx = re.compile(m, flags)
    return lambda ist: rx.fullmatch(ist) is not None


class SigmaPlatzhalter(unittest.TestCase):
    def test_gleich_wie_bisher(self):
        zufall = random.Random(7)
        zeichen = "ab*?\\"
        for _ in range(3000):
            soll = "".join(zufall.choice(zeichen) for _ in range(zufall.randint(1, 6)))
            if S._woertlich(soll) is not None:
                continue
            art = zufall.choice(("in", "anfang", "ende", "gleich"))
            for _ in range(5):
                ist = "".join(zufall.choice("abAB*?\\\n") for _ in range(zufall.randint(0, 8)))
                for flags in (re.DOTALL, re.DOTALL | re.IGNORECASE):
                    self.assertEqual(S._platzhalter(soll, art, flags)(ist), _alt(soll, art, flags)(ist),
                                     (soll, art, ist, flags))

    def test_linear_bei_vielen_treffern(self):
        pruefen = S._vergleicher("*a*a*a*b*", ["contains"])
        start = time.monotonic()
        self.assertFalse(pruefen("a" * 30000))
        self.assertTrue(pruefen("x" + "a" * 30000 + "b"))
        self.assertLess(time.monotonic() - start, 1.0)


class Werkzeugformate(unittest.TestCase):
    BOESE = "<tool_call>f " + "<arg_key>a</arg_key><arg_value>b</arg_value>" * 40 + " x"

    def test_glm_muster_ohne_exponentielles_backtracking(self):
        d = W.dialekt("glm_xml")
        start = time.monotonic()
        self.assertEqual(d.aufrufe_finden(self.BOESE), [])
        self.assertLess(time.monotonic() - start, 1.0)
        gut = "<tool_call>web_suche\n<arg_key>suche</arg_key>\n<arg_value>Wetter</arg_value>\n</tool_call>"
        self.assertEqual([(n, f) for _a, _e, n, f in d.aufrufe_finden(gut)], [("web_suche", {"suche": "Wetter"})])

    def test_teures_muster_haelt_nicht_fest(self):
        eintrag = json.loads(json.dumps(next(e for e in ausfuellen.werkzeugformate() if e.get("name") == "glm_xml")))
        eintrag["lesen"]["aufruf"] = r"<tool_call>\s*([^\n<]+?)\s*((?:<arg_key>.*?</arg_value>\s*)*)</tool_call>"
        d = W.Dialekt(eintrag)
        with mock.patch.object(W, "LESE_ZEIT_S", 0.2):
            start = time.monotonic()
            self.assertEqual(d.aufrufe_finden(self.BOESE), [])
            self.assertLess(time.monotonic() - start, 5.0)

    def test_kaputtes_muster_wird_abgelehnt(self):
        eintrag = json.loads(json.dumps(next(e for e in ausfuellen.werkzeugformate() if e.get("name") == "glm_xml")))
        eintrag["lesen"]["aufruf"] = "(offen"
        with self.assertRaises(ausfuellen.DbFehler):
            W.Dialekt(eintrag)


class VorprueferFeld(unittest.TestCase):
    def test_feldname_maskiert(self):
        self.assertEqual(V._feld("a.b: falsch\na+b: richtig", "a+b"), "richtig")
        self.assertEqual(V._feld("Regel: R007 x", "Regel"), "R007 x")


if __name__ == "__main__":
    unittest.main()
