"""GUI-Fenster: Modell-Text öffnet nur http(s)-Links und lädt nichts nach."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    import gui_fenster
except ImportError:                                   # PySide6 fehlt
    gui_fenster = None


@unittest.skipIf(gui_fenster is None, "PySide6 fehlt")
class ModellText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test"])

    def test_nur_http_links_oeffnen(self):
        with mock.patch.object(gui_fenster.QDesktopServices, "openUrl") as oeffnen:
            for url in ("file:///C:/Windows/System32/calc.exe", "ms-msdt:/id", "search-ms:query=x",
                        "file://example/share/x.png", "javascript:alert(1)"):
                gui_fenster.Markdown._link(QUrl(url))
            oeffnen.assert_not_called()
            gui_fenster.Markdown._link(QUrl("https://example.org/seite"))
            oeffnen.assert_called_once()

    def test_nachfrage_zeigt_ganze_vorschau(self):
        from PySide6.QtWidgets import QPlainTextEdit
        inhalt = "\n".join(f"Zeile {i}" for i in range(2000))
        karte = gui_fenster.Karte("Datei schreiben?")
        karte.frage(1, [("yes", "Erlauben"), ("no", "Ablehnen")], inhalt)
        felder = karte.findChildren(QPlainTextEdit)
        self.assertEqual(len(felder), 1)
        self.assertEqual(felder[0].toPlainText(), inhalt)

    def test_langsames_zeichnen_zeichnet_seltener(self):
        block = gui_fenster.KiBlock()
        block.schreiben("Hallo")
        with mock.patch.object(block.text, "setzen", side_effect=lambda _t: __import__("time").sleep(0.05)):
            block._zeichnen()
        self.assertGreaterEqual(block._takt, 150)                        # 50 ms Zeichnen → ≥ 200 ms Takt
        with mock.patch.object(block.text, "setzen"):
            block._zeichnen()
        self.assertEqual(block._takt, 80)

    def test_sanftes_scrollen_bleibt_im_bereich(self):
        from PySide6.QtWidgets import QWidget
        inhalt = QWidget()
        inhalt.setMinimumHeight(3000)
        bereich = gui_fenster.rollbereich(inhalt)
        bereich.resize(400, 300)
        bereich.show()
        self.app.processEvents()
        leiste = bereich.verticalScrollBar()
        bereich.sanft_zu(10**6)
        self.assertEqual(bereich._anim.endValue(), leiste.maximum())
        bereich._anim.stop()
        leiste.setValue(leiste.maximum() // 2)
        bereich.sanft_zu(-50)
        self.assertEqual(bereich._anim.endValue(), leiste.minimum())

    def test_keine_ressourcen_kein_html(self):
        md = gui_fenster.Markdown()
        md.setzen('![x](file://example/share/x.png) <img src="file://example/y.png"> **fett**')
        self.assertIsNone(md.loadResource(2, QUrl("file://example/share/x.png")))
        self.assertIn("<img", md.toPlainText())             # HTML bleibt Text
        self.assertFalse(md.openLinks())


if __name__ == "__main__":
    unittest.main()
