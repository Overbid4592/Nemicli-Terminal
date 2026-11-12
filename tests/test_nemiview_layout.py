"""NemiView-Layout: Text direkt in Flex/Grid, Flex-Spalten (grow, align-items), Inhaltsbreite, Zeilen."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from nemiview.laufzeit import AppLaufzeit                      # noqa: E402
from nemiview.layout import Layout                             # noqa: E402


class LayoutNachbesserungTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def legen(self, body: str):
        ordner = Path(self._tmp.name) / "Apps" / "probe"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "index.html").write_text(f'<!doctype html><html><body style="margin:0">{body}</body></html>',
                                           encoding="utf-8")
        lz = AppLaufzeit(ordner, 800, 600, speicher_ordner=Path(self._tmp.name) / "_daten", virtuelle_zeit=True)
        lz.laden()
        L = Layout(lz.dok, lz.kaskade, 800, 600)
        L.rechnen()
        return lz, L

    @staticmethod
    def kasten(L, el):
        return next(k for k in L.alle if k.el is el)

    def test_text_direkt_in_flex_und_grid_wird_gezeichnet(self):
        lz, L = self.legen('<div id="f" style="display:flex">Hallo <b>Welt</b></div>'
                           '<div id="g" style="display:grid">Gitter</div>')
        self.assertIn("Hallo", [t[2] for t in self.kasten(L, lz.dok.nach_id("f")).texte])
        self.assertIn("Gitter", [t[2] for t in self.kasten(L, lz.dok.nach_id("g")).texte])

    def test_flex_spalte_mit_grow(self):
        lz, _L = self.legen('<div style="display:flex;flex-direction:column;height:300px">'
                            '<div style="height:50px"></div><div id="haupt" style="flex:1"></div></div>')
        _x, y, _w, h = lz.dok.nach_id("haupt").box
        self.assertAlmostEqual(50, y, delta=1)
        self.assertAlmostEqual(250, h, delta=1)

    def test_flex_spalte_align_items_center(self):
        lz, _L = self.legen('<div style="display:flex;flex-direction:column;align-items:center;width:400px">'
                            '<div id="c">abc</div></div>')
        x, _y, w, _h = lz.dok.nach_id("c").box
        self.assertLess(w, 100)
        self.assertAlmostEqual(200, x + w / 2, delta=1)

    def test_inhaltsbreite_nimmt_breitestes_block_kind(self):
        lz, _L = self.legen('<div id="ib" style="display:inline-block"><div>aaaa</div><div>bbbb</div></div>'
                            '<div id="eins" style="display:inline-block"><div>aaaa</div></div>')
        self.assertAlmostEqual(lz.dok.nach_id("eins").box[2], lz.dok.nach_id("ib").box[2], delta=1)

    def test_br_am_ende_und_nowrap(self):
        lz, _L = self.legen('<p id="p" style="margin:0">a<br><br></p><p id="q" style="margin:0">a<br>b</p>'
                            '<div id="n" style="width:50px;white-space:nowrap">aaa bbb ccc</div>'
                            '<div id="e" style="width:50px">a</div>')
        self.assertAlmostEqual(lz.dok.nach_id("q").box[3], lz.dok.nach_id("p").box[3], delta=1)
        self.assertAlmostEqual(lz.dok.nach_id("e").box[3], lz.dok.nach_id("n").box[3], delta=1)

    def test_relativ_mit_bottom_verschiebt_den_fluss_nicht(self):
        lz, _L = self.legen('<div style="position:relative;bottom:10px;height:20px"></div>'
                            '<div id="d" style="height:5px"></div><textarea rows="abc"></textarea>')
        self.assertAlmostEqual(20, lz.dok.nach_id("d").box[1], delta=1)


if __name__ == "__main__":
    unittest.main()
