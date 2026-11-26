"""Nemi Office mit Oberfläche (offscreen): Schreiben, Tabelle, Startseite, Nemi-Leiste, Brücke zur Sitzung."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402
import profilordner  # noqa: E402
import qt_testhilfe  # noqa: E402
from buero import formel as F  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage, QTextCursor, QTextDocument
    from PySide6.QtWidgets import QApplication
    import office_fenster
    import office_agent
    import office_schreiben
except ImportError:                                   # PySide6 fehlt
    office_fenster = None


@unittest.skipIf(office_fenster is None, "PySide6 fehlt")
class _Basis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        for p in (mock.patch.object(paths, "DATEN", self.ordner), mock.patch.object(profilordner, "aktiv", lambda: "Test")):
            p.start()
            self.addCleanup(p.stop)
        self.fenster = office_fenster.OfficeFenster(None)
        self.addCleanup(self._zu)

    def _zu(self):
        for s in self.fenster._seiten():
            if hasattr(s, "feld"):
                s.feld.document().setModified(False)
                s.quelle.document().setModified(False)
            else:
                s._veraendert = False
        self.fenster.close()
        self.fenster.deleteLater()


class SchreibenTest(_Basis):
    TEXT = ("# Brief\n\nHallo **fett** und *kursiv*.\n\n- eins\n- zwei\n\n1. a\n2. b\n\n| x | y |\n|---|---|\n"
            "| 1 | 2 |\n")

    def _seite(self):
        self.fenster.neu("schreiben")
        s = self.fenster.aktuelle_seite()
        s.feld.document().setMarkdown(self.TEXT)
        return s

    def test_ordner_im_profil(self):
        self.assertEqual(Path(self.fenster.ordner), self.ordner / "Profile" / "Test" / "Office")

    def test_alle_formate_rund(self):
        s = self._seite()
        for end in (".md", ".docx", ".odt", ".rtf", ".html", ".txt"):
            s.pfad = str(self.ordner / f"brief{end}")
            self.assertTrue(s.speichern(), end)
            self.assertFalse(s.ungespeichert())
            neu = office_schreiben.SchreibSeite(str(self.ordner))
            neu.laden(str(self.ordner / f"brief{end}"))
            text = neu.feld.toPlainText()
            self.assertIn("Brief", text, end)
            self.assertIn("zwei", text, end)
            if end in (".docx", ".odt"):
                dok = office_schreiben.qt_zu_modell(neu.feld.document())
                self.assertEqual(dok.bloecke[0].stufe, 1, end)
                self.assertTrue(any(getattr(b, "liste", "") == "zahl" for b in dok.bloecke), end)
                self.assertTrue(any(type(b).__name__ == "Tabelle" for b in dok.bloecke), end)
            neu.deleteLater()
        self.assertTrue(s.als_pdf(str(self.ordner / "brief.pdf")))
        self.assertTrue((self.ordner / "brief.pdf").read_bytes().startswith(b"%PDF"))

    def test_bild_rund(self):
        s = self._seite()
        bild = QImage(40, 20, QImage.Format.Format_RGB32)
        bild.fill(0xFF8800)
        office_schreiben.bild_einfuegen(s.feld, bild)
        s.pfad = str(self.ordner / "bild.docx")
        self.assertTrue(s.speichern())
        neu = office_schreiben.SchreibSeite(str(self.ordner))
        neu.laden(s.pfad)
        dok = office_schreiben.qt_zu_modell(neu.feld.document())
        bilder = [b for b in dok.bloecke if type(b).__name__ == "Bild"]
        self.assertEqual(len(bilder), 1)
        self.assertEqual(round(bilder[0].breite_pt), 30)                  # 40 px bei 96 dpi
        neu.deleteLater()

    def test_keine_netzbilder(self):
        s = self._seite()
        typ = QTextDocument.ResourceType.ImageResource
        self.assertIsNone(s.feld.loadResource(typ, QUrl("https://example.com/x.png")))
        self.assertIsNone(s.feld.loadResource(typ, QUrl("file://server/freigabe/x.png")))
        self.assertIsNone(s.feld.loadResource(typ, QUrl.fromLocalFile("//server/freigabe/x.png")))
        self.assertFalse(office_schreiben.lokal_erlaubt(QUrl("file:////server/x.png")))
        self.assertTrue(office_schreiben.lokal_erlaubt(QUrl.fromLocalFile("C:/bilder/x.png")))

    def test_markdown_quelle(self):
        s = self._seite()
        s.k_quelle.setChecked(True)
        self.assertIn("# Brief", s.quelle.toPlainText())
        s.quelle.setPlainText("# Neu\n\nanders")
        s.k_quelle.setChecked(False)
        self.assertTrue(s.feld.toPlainText().startswith("Neu"))

    def test_kontext_und_uebernehmen(self):
        s = self._seite()
        c = s.feld.textCursor()
        c.movePosition(QTextCursor.MoveOperation.Start)
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        s.feld.setTextCursor(c)
        k = s.kontext()
        self.assertEqual(k["app"], "schreiben")
        self.assertIn("Brief", k["auswahl"])
        self.assertIn("- zwei", k["kontext"])
        probe = QTextDocument()
        probe.setMarkdown("**a**")
        if "**" in probe.toMarkdown():                   # Qt braucht dafür Schriften (offscreen nicht immer)
            self.assertIn("**fett**", k["kontext"])
        self.assertEqual(s.uebernehmen("Neuer **Titel**"), "ersetzt")
        self.assertIn("Neuer Titel", s.feld.toPlainText())
        self.assertNotIn("Brief", s.feld.toPlainText())
        self.assertTrue(s.ungespeichert())


class SchreibenFarbenTest(_Basis):
    def test_farben_und_papier_rund(self):
        self.fenster.neu("schreiben")
        s = self.fenster.aktuelle_seite()
        s.feld.setPlainText("rot markiert normal")
        c = s.feld.textCursor()
        c.setPosition(0)
        c.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        s.feld.setTextCursor(c)
        s.textfarbe("#e74c3c")
        c.setPosition(4)
        c.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
        s.feld.setTextCursor(c)
        s.marker("#fff2cc")
        s.papier("#f2f2f2")
        self.assertEqual(s.feld.papier.name(), "#f2f2f2")
        self.assertTrue(office_schreiben.hat_farben(s.feld.document()))
        for end in (".docx", ".odt", ".html"):
            s.pfad = str(self.ordner / f"bunt{end}")
            self.assertTrue(s.speichern(), end)
            neu = office_schreiben.SchreibSeite(str(self.ordner))
            neu.laden(s.pfad)
            dok = office_schreiben.qt_zu_modell(neu.feld.document())
            farben = {(l.text, l.farbe, l.marker) for l in dok.bloecke[0].laeufe}
            self.assertIn(("rot", "#e74c3c", ""), farben, end)
            self.assertIn(("markiert", "", "#fff2cc"), farben, end)
            self.assertEqual(dok.papier, "#f2f2f2", end)
            self.assertEqual(neu.feld.papier.name(), "#f2f2f2", end)
            neu.deleteLater()
        s.pfad = str(self.ordner / "bunt.md")
        s.speichern()
        self.assertIn("Farben kann .md nicht", s.status.links.text())

    def test_farbe_entfernen(self):
        self.fenster.neu("schreiben")
        s = self.fenster.aktuelle_seite()
        s.feld.setPlainText("Wort")
        s.feld.selectAll()
        s.textfarbe("#e74c3c")
        s.feld.selectAll()
        s.textfarbe("")
        s.papier("#fff2cc")
        s.papier("")
        self.assertFalse(office_schreiben.hat_farben(s.feld.document()))
        self.assertEqual(s.feld.papier.name(), "#fdfdfb")

    def test_papier_bleibt_nach_markdown_quelle(self):
        self.fenster.neu("schreiben")
        s = self.fenster.aktuelle_seite()
        s.feld.setPlainText("Text")
        s.papier("#e2efda")
        s.k_quelle.setChecked(True)
        s.quelle.setPlainText("Anderer Text")
        s.k_quelle.setChecked(False)
        self.assertEqual(office_schreiben.papier_von(s.feld.document()), "#e2efda")


def _pdf_seiten(daten: bytes) -> int:
    import re
    return len(re.findall(rb"/Type\s*/Page[^s]", daten))


class SeitenTest(_Basis):
    """A4-Blätter im Schreiben (wie im PDF), Druckgrenzen in der Tabelle."""

    def setUp(self):
        super().setUp()
        import office_seiten
        self.S = office_seiten
        self.fenster.resize(1200, 900)
        self.fenster.show()

    def _schreiben(self, absaetze: int = 15):
        self.fenster.neu("schreiben")
        s = self.fenster.aktuelle_seite()
        text = "Es war einmal ein kleiner Drache, der gern Geschichten las und dabei Tee trank. " * 6
        s.feld.document().setMarkdown("# Titel\n\n" + "\n\n".join(text for _ in range(absaetze)))
        QApplication.processEvents()
        return s

    def test_a4_blaetter_wie_im_pdf(self):
        from PySide6.QtCore import QSizeF
        s = self._schreiben()
        doc = s.feld.document()
        self.assertEqual(doc.pageSize(), QSizeF(self.S.SEITE_W, self.S.SEITE_H))
        self.assertEqual(doc.documentMargin(), self.S.RAND)
        seiten = self.S.seiten_zahl(doc)
        self.assertGreaterEqual(seiten, 2)
        pfad = self.ordner / "g.pdf"
        office_schreiben.pdf_schreiben(doc, str(pfad))
        self.assertEqual(_pdf_seiten(pfad.read_bytes()), seiten)          # Bildschirm = PDF
        self.assertIn(f"von {seiten}", s.status.rechts_lb.text())
        s.feld.viewport().repaint()                                       # Blätter, Lücken, Eckwinkel, Lineal

    def test_schmales_fenster_bleibt_a4(self):
        s = self._schreiben(2)
        self.fenster.resize(560, 700)
        QApplication.processEvents()
        self.assertEqual(s.feld.lineWrapColumnOrWidth(), self.S.SEITE_W)
        self.assertGreater(s.feld.horizontalScrollBar().maximum(), 0)
        s.feld.horizontalScrollBar().setValue(50)
        self.assertEqual(s.feld.lineal.versatz, 50)

    def test_seiten_aus(self):
        s = self._schreiben(2)
        s.k_seiten.setChecked(False)
        self.assertLessEqual(s.feld.document().pageSize().height(), 0)
        self.assertNotIn("Seite", s.status.rechts_lb.text())
        s.k_seiten.setChecked(True)
        self.assertEqual(s.feld.document().pageSize().height(), self.S.SEITE_H)

    def test_tabelle_grenzen_und_pdf(self):
        self.assertEqual(self.S.umbrueche([110] * 9, self.S.PLATZ_W), [5])
        self.assertEqual(self.S.umbrueche([900, 10], self.S.PLATZ_W), [1])             # zu breit: eigene Seite
        self.assertEqual(self.S.gruppen([110] * 9, self.S.PLATZ_W), [(0, 5), (5, 9)])
        self.fenster.neu("tabelle")
        t = self.fenster.aktuelle_seite()
        m = t.modell
        for z in range(60):
            for sp in range(9):
                m.setData(m.index(z, sp), str(z + sp))
        m.setData(m.index(0, 0), "=1/0")
        t.raster.viewport().repaint()
        zeilen = len(self.S.gruppen([28] * 60, self.S.PLATZ_H))
        pfad = self.ordner / "t.pdf"
        self.assertTrue(t.als_pdf(str(pfad)))
        self.assertEqual(_pdf_seiten(pfad.read_bytes()), 2 * zeilen)
        t.k_seiten.setChecked(False)
        self.assertFalse(t.raster.seiten)


class MarkdownBilderTest(_Basis):
    """Markdown mit Bildern: wählbar mit (PNG-Ordner daneben) oder ohne; HTML bettet Bilder ein."""

    def setUp(self):
        super().setUp()
        self.fenster.neu("schreiben")
        self.s = self.fenster.aktuelle_seite()
        self.s.feld.setPlainText("Eine Geschichte")
        bild = QImage(40, 30, QImage.Format.Format_RGB32)
        bild.fill(0x8B5CF6)
        self.s.feld.moveCursor(QTextCursor.MoveOperation.End)
        office_schreiben.bild_einfuegen(self.s.feld, bild)
        self.s.feld.textCursor().insertText("\nEnde")

    def _speichern(self, wahl: str, name: str = "story.md"):
        self.s.pfad = str(self.ordner / name)
        with mock.patch.object(office_schreiben, "waehlen", return_value=wahl) as frage:
            ok = self.s.speichern()
        return ok, frage

    def test_mit_bildern(self):
        ok, frage = self._speichern("mit")
        self.assertTrue(ok)
        md = (self.ordner / "story.md").read_text(encoding="utf-8")
        self.assertIn("](story_Bilder/bild-01.png)", md)
        self.assertTrue((self.ordner / "story_Bilder" / "bild-01.png").is_file())
        with mock.patch.object(office_schreiben, "waehlen") as nochmal:      # Strg+S fragt nicht erneut
            self.assertTrue(self.s.speichern())
        nochmal.assert_not_called()
        neu = office_schreiben.SchreibSeite(str(self.ordner))
        neu.laden(str(self.ordner / "story.md"))
        import office_markdown as MD
        namen = MD.bilder_namen(neu.feld.document())
        self.assertEqual(len(namen), 1)
        self.assertIsNotNone(MD.bild_von(neu.feld.document(), namen[0]))     # Bild beim Öffnen wieder da
        neu.feld.document().setModified(False)
        neu.deleteLater()

    def test_ohne_bilder(self):
        ok, _ = self._speichern("ohne")
        self.assertTrue(ok)
        md = (self.ordner / "story.md").read_text(encoding="utf-8")
        self.assertNotIn("![", md)
        self.assertIn("Eine Geschichte", md)
        self.assertFalse((self.ordner / "story_Bilder").exists())

    def test_abbrechen_und_ohne_bild_keine_frage(self):
        ok, _ = self._speichern("ab")
        self.assertFalse(ok)
        self.assertFalse((self.ordner / "story.md").exists())
        self.s.feld.setPlainText("nur Text")
        ok, frage = self._speichern("mit", "text.md")
        self.assertTrue(ok)
        frage.assert_not_called()

    def test_html_bettet_ein(self):
        self.s.pfad = str(self.ordner / "story.html")
        self.assertTrue(self.s.speichern())
        html = (self.ordner / "story.html").read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,", html)
        self.assertNotIn("nemi-bild:", html)


class LiveMarkdownTest(_Basis):
    """Markdown beim Tippen sofort als Format – und Strg+Z holt die Zeichen zurück."""

    def setUp(self):
        super().setUp()
        self.fenster.neu("schreiben")
        self.s = self.fenster.aktuelle_seite()
        self.feld = self.s.feld
        self.feld.setFocus()

    def _tippen(self, *teile):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        for t in teile:
            if t == "\n":
                QTest.keyClick(self.feld, Qt.Key.Key_Return)
            else:
                QTest.keyClicks(self.feld, t)

    def _bloecke(self):
        import office_markdown as MD
        from PySide6.QtGui import QTextBlockFormat
        raus, b = [], self.feld.document().begin()
        while b.isValid():
            bf = b.blockFormat()
            raus.append((b.text(), bf.headingLevel(), b.textList() is not None,
                         bf.marker() != QTextBlockFormat.MarkerType.NoMarker and bf.marker().name,
                         MD.ist_zitat(bf), MD.ist_linie(bf)))
            b = b.next()
        return raus

    def test_bloecke(self):
        self._tippen("# Titel", "\n", "- Milch", "\n", "Brot", "\n", "\n", "1. Eins", "\n", "\n", "> Zitat", "\n",
                     "\n", "- [ ] offen", "\n", "[x] fertig", "\n", "\n", "---", "\n", "Ende")
        self.assertEqual(self._bloecke(), [
            ("Titel", 1, False, False, False, False), ("Milch", 0, True, False, False, False),
            ("Brot", 0, True, False, False, False), ("Eins", 0, True, False, False, False),
            ("Zitat", 0, False, False, True, False), ("offen", 0, True, "Unchecked", False, False),
            ("fertig", 0, True, "Checked", False, False), ("", 0, False, False, False, True),
            ("Ende", 0, False, False, False, False)])

    def test_inline_und_rueckgaengig(self):
        import office_markdown as MD
        self._tippen("a **fett** b *kursiv* c ~~weg~~ d `x()` e")
        self.assertEqual(self.feld.toPlainText(), "a fett b kursiv c weg d x() e")
        probe = QTextDocument()
        probe.setMarkdown("**a**")
        if "**" in probe.toMarkdown():                   # Qt braucht für fett/kursiv Schriften (offscreen nicht immer)
            self.assertEqual(MD.markdown(self.feld.document()).strip(), "a **fett** b *kursiv* c ~~weg~~ d `x()` e")
        else:
            self.assertIn("~~weg~~ d `x()` e", MD.markdown(self.feld.document()))
        self.feld.clear()
        self._tippen("d `x()`")
        self.assertEqual(self.feld.toPlainText(), "d x()")
        self.feld.undo()                                            # die Umwandlung zurück: Zeichen wieder da
        self.assertEqual(self.feld.toPlainText(), "d `x()`")

    def test_kein_doppeltes_umwandeln(self):
        self._tippen("Preis 5 * 3 * 2 = 30")                          # kein Kursiv aus Rechenzeichen
        self.assertEqual(self.feld.toPlainText(), "Preis 5 * 3 * 2 = 30")
        self._tippen("\n", "a - b")                                   # Strich mitten in der Zeile bleibt
        self.assertEqual(self.feld.document().lastBlock().text(), "a - b")

    def test_checkbox_anklicken(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QTextCursor
        from PySide6.QtTest import QTest
        self.fenster.resize(1000, 800)
        self.fenster.show()
        self.feld.document().setMarkdown("- [ ] Aufgabe\n")
        QApplication.processEvents()
        b = self.feld.document().begin()
        r = self.feld.cursorRect(QTextCursor(b))
        QTest.mouseClick(self.feld.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(r.left() - 12, r.center().y()))
        self.assertEqual(b.blockFormat().marker().name, "Checked")
        QTest.mouseClick(self.feld.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(r.left() - 12, r.center().y()))
        self.assertEqual(b.blockFormat().marker().name, "Unchecked")

    def test_editor_rund_docx_und_odt(self):
        self.feld.document().setMarkdown("> Zitat\n\n---\n\n- [ ] offen\n- [x] fertig\n\nmit `code` drin\n")
        for end in (".docx", ".odt"):
            self.s.pfad = str(self.ordner / f"md{end}")
            self.assertTrue(self.s.speichern(), end)
            neu = office_schreiben.SchreibSeite(str(self.ordner))
            neu.laden(self.s.pfad)
            md = __import__("office_markdown").markdown(neu.feld.document())
            for teil in ("> Zitat", "- - -", "- [ ] offen", "- [x] fertig", "`code`"):
                self.assertIn(teil, md, end)
            neu.deleteLater()

    def test_code_und_tabelle_huebsch(self):
        import office_markdown as MD
        self.feld.document().setMarkdown("Text `code` hier\n\n| a | b |\n|:--|--:|\n| 1 | 2 |\n")
        MD.aufhuebschen(self.feld.document())
        it = self.feld.document().begin().begin()
        familien = []
        while not it.atEnd():
            familien.append(it.fragment().charFormat().fontFamilies())
            it += 1
        self.assertIn(MD.CODE_SCHRIFTEN, familien)
        tabelle = QTextCursor(self.feld.document().findBlockByNumber(1)).currentTable()
        self.assertGreaterEqual(tabelle.format().cellPadding(), 4)
        self.assertIn("|---|---:|", MD.markdown(self.feld.document()))     # Ausrichtung bleibt beim Export

    def test_knoepfe_und_seiten(self):
        self.feld.setPlainText("Zeile")
        self.s.zitat()
        self.s.aufgabe()
        self.s.linie()
        self.s.k_seiten.setChecked(True)
        self.assertTrue(self.feld.seiten)
        self.fenster.show()
        self.feld.viewport().repaint()                                # Malen mit Balken und Seitenlinien
        md = __import__("office_markdown").markdown(self.feld.document())
        self.assertIn("- [ ] Zeile", md)
        self.assertIn("- - -", md)

    def test_bild_malen_fragt_die_officeai(self):
        gesendet = []
        self.fenster.leitung = mock.Mock(senden=gesendet.append)
        self.s.bild_wunsch.emit("ein Café im Herbst")
        self.assertEqual(gesendet[-1]["op"], "frage")
        self.assertIn("Werkzeug bild", gesendet[-1]["text"])
        self.assertIn("ein Café im Herbst", gesendet[-1]["text"])


class TabelleFarbenTest(_Basis):
    def _seite(self):
        self.fenster.neu("tabelle")
        t = self.fenster.aktuelle_seite()
        t.modell.setzen([(0, 0, "Posten"), (0, 1, "Betrag"), (1, 0, "Strom"), (1, 1, "85"), (2, 0, "Miete"),
                         (2, 1, "650"), (3, 0, "Handy"), (3, 1, "20")])
        return t

    def _markieren(self, t, zellen):
        sm = t.raster.selectionModel()
        sm.clear()
        t.raster.setCurrentIndex(t.modell.index(*zellen[0]))
        for z, s in zellen:
            sm.select(t.modell.index(z, s), sm.SelectionFlag.Select)

    def test_einfaerben_und_rueckgaengig(self):
        t = self._seite()
        self._markieren(t, [(0, 0), (0, 1)])
        t.formatieren({"hg": "#d9d9d9"})
        t.fett_umschalten()
        rolle = office_fenster.Qt.ItemDataRole
        self.assertEqual(t.modell.data(t.modell.index(0, 1), rolle.BackgroundRole).name(), "#d9d9d9")
        self.assertTrue(t.modell.data(t.modell.index(0, 0), rolle.FontRole).bold())
        t.formatieren({"fg": "#2f75b5"})
        self.assertEqual(t.modell.data(t.modell.index(0, 0), rolle.ForegroundRole).name(), "#2f75b5")
        t.rueckgaengig()                                   # Schriftfarbe weg, Fett und Füllung bleiben
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 0, 0), {"hg": "#d9d9d9", "fett": True})
        t.fett_umschalten()                                # alle fett -> aus
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 0, 0), {"hg": "#d9d9d9"})
        t.formatieren({"hg": "", "fg": "", "fett": False})
        self.assertEqual(t.modell.blatt.formate, {})
        self.assertTrue(t.ungespeichert())

    def test_speichern_und_laden(self):
        t = self._seite()
        self._markieren(t, [(0, 0), (0, 1)])
        t.formatieren({"hg": "#d9d9d9", "fett": True})
        for end in (".xlsx", ".ods"):
            t.pfad = str(self.ordner / f"bunt{end}")
            self.assertTrue(t.speichern())
            self.fenster.oeffnen(t.pfad)
            neu = self.fenster.aktuelle_seite()
            self.assertEqual(neu.modell.mappe.format(neu.modell.blatt, 0, 1), {"hg": "#d9d9d9", "fett": True}, end)

    def test_sortieren_nimmt_farben_mit(self):
        t = self._seite()
        self._markieren(t, [(3, 1)])
        t.formatieren({"hg": "#fff2cc"})                   # Handy (20) gelb
        self._markieren(t, [(1, 1), (2, 1), (3, 1)])
        t.sortieren(absteigend=False)                      # 20, 85, 650
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 1, 0), "Handy")
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 1, 1), {"hg": "#fff2cc"})
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 3, 1), {})
        t.rueckgaengig()                                   # ein Schritt für Werte und Farben
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 3, 0), "Handy")
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 3, 1), {"hg": "#fff2cc"})

    def test_farbmenue(self):
        from office_teile import PALETTE, farb_menue
        gewaehlt = []
        menue = farb_menue(self.fenster, gewaehlt.append, "Test")
        felder = [w for w in menue.findChildren(office_fenster.QWidget) if type(w).__name__ == "_Farbfeld"]
        self.assertEqual(len(felder), sum(len(r) for r in PALETTE))
        felder[2].click()
        self.assertEqual(gewaehlt, [PALETTE[0][2]])


class TabelleTest(_Basis):
    def _seite(self):
        self.fenster.neu("tabelle")
        t = self.fenster.aktuelle_seite()
        t.modell.setzen([(0, 0, "Posten"), (0, 1, "Betrag"), (1, 0, "Strom"), (1, 1, "85,40"), (2, 0, "Handy"),
                         (2, 1, "19,99"), (3, 0, "Miete"), (3, 1, "650")])
        return t

    def test_summe_und_rueckgaengig(self):
        t = self._seite()
        t.raster.setCurrentIndex(t.modell.index(4, 1))
        t.summe()
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 4, 1), "=SUMME(B2:B4)")
        self.assertEqual(t.modell.data(t.modell.index(4, 1)), "755,39")
        self.assertEqual(t.formelfeld.text(), "=SUMME(B2:B4)")
        t.rueckgaengig()
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 4, 1), "")
        t.wiederholen()
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 4, 1), "=SUMME(B2:B4)")

    def test_formelzeile_und_springen(self):
        t = self._seite()
        t.namensfeld.setText("D10")
        t._springen()
        self.assertEqual((t.raster.currentIndex().row(), t.raster.currentIndex().column()), (9, 3))
        t.formelfeld.setText("=B2*2")
        t._formel_setzen()
        self.assertEqual(t.modell.mappe.wert(t.modell.blatt, 9, 3), 170.8)

    def test_zwischenablage(self):
        t = self._seite()
        QApplication.clipboard().setText("1\t2\n3\t4")
        t.raster.setCurrentIndex(t.modell.index(6, 0))
        t.einfuegen()
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 7, 1), "4")
        t.raster.selectionModel().clear()
        t.raster.setCurrentIndex(t.modell.index(1, 0))
        t.raster.selectionModel().select(t.modell.index(1, 1), t.raster.selectionModel().SelectionFlag.Select)
        t.raster.selectionModel().select(t.modell.index(1, 0), t.raster.selectionModel().SelectionFlag.Select)
        t.kopieren()
        self.assertEqual(QApplication.clipboard().text(), "Strom\t85,40")

    def test_zeile_einfuegen_mit_formel(self):
        t = self._seite()
        t.modell.setzen([(4, 1, "=SUMME(B2:B4)")])
        t.raster.setCurrentIndex(t.modell.index(2, 0))
        t._achse("zeile", 0, True)
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 5, 1), "=SUMME(B2:B5)")

    def test_sortieren(self):
        t = self._seite()
        sm = t.raster.selectionModel()
        for z in (1, 2, 3):
            sm.select(t.modell.index(z, 1), sm.SelectionFlag.Select)
        t.raster.setCurrentIndex(t.modell.index(1, 1))
        for z in (1, 2, 3):
            sm.select(t.modell.index(z, 1), sm.SelectionFlag.Select)
        t.sortieren(absteigend=True)
        self.assertEqual([t.modell.mappe.roh(t.modell.blatt, z, 0) for z in (1, 2, 3)], ["Miete", "Strom", "Handy"])

    def test_uebernehmen(self):
        t = self._seite()
        t.raster.setCurrentIndex(t.modell.index(5, 0))
        self.assertIn("eingetragen", t.uebernehmen("So:\n| Name | Wert |\n|---|---|\n| Summe | =SUMME(B2:B4) |"))
        self.assertEqual(t.modell.mappe.wert(t.modell.blatt, 6, 1), 755.39)
        t.raster.setCurrentIndex(t.modell.index(8, 1))
        t.uebernehmen("=MITTELWERT(B2:B4)")
        self.assertAlmostEqual(t.modell.mappe.wert(t.modell.blatt, 8, 1), 251.79666666666665)

    def test_speichern_und_laden(self):
        t = self._seite()
        t.modell.setzen([(4, 1, "=SUMME(B2:B4)")])
        for end in (".ods", ".xlsx"):
            t.pfad = str(self.ordner / f"haushalt{end}")
            self.assertTrue(t.speichern(), end)
            self.fenster.oeffnen(t.pfad)
            neu = self.fenster.aktuelle_seite()
            self.assertEqual(neu.modell.mappe.roh(neu.modell.blatt, 4, 1), "=SUMME(B2:B4)", end)
            self.assertIn("755,39", neu.kontext()["kontext"])
        self.assertTrue(t.als_pdf(str(self.ordner / "haushalt.pdf")))

    def test_blaetter(self):
        t = self._seite()
        t._blatt_dazu()
        self.assertEqual(t.reiter.count(), 2)
        self.assertEqual(t.modell.blatt.name, "Tabelle2")
        t.modell.setzen([(0, 0, "=Tabelle1!B2*2")])
        self.assertEqual(t.modell.mappe.wert(t.modell.blatt, 0, 0), 170.8)

    def test_fehler_rot(self):
        t = self._seite()
        t.modell.setzen([(0, 3, "=1/0")])
        self.assertEqual(t.modell.data(t.modell.index(0, 3)), "#DIV/0!")
        self.assertIsNotNone(t.modell.data(t.modell.index(0, 3), role=office_fenster.Qt.ItemDataRole.ForegroundRole))


class FensterTest(_Basis):
    def test_zuletzt_und_wiederfinden(self):
        p = self.ordner / "notiz.md"
        p.write_text("# Notiz\n\nInhalt", encoding="utf-8")
        self.fenster.oeffnen(str(p))
        self.assertEqual(self.fenster.reiter.count(), 2)
        self.fenster.oeffnen(str(p))                             # schon offen: kein zweiter Reiter
        self.assertEqual(self.fenster.reiter.count(), 2)
        eintraege = office_fenster.ablage.zuletzt(self.fenster.ordner)
        self.assertEqual([Path(e["pfad"]).name for e in eintraege], ["notiz.md"])
        self.assertIn("Notiz", eintraege[0]["vorschau"])
        self.fenster.reiter.setCurrentIndex(0)
        self.assertEqual(self.fenster.start.liste.count(), 1)

    def test_netzfreigabe_wird_nicht_geoeffnet(self):
        self.fenster.oeffnen("\\\\server\\freigabe\\x.docx")
        self.assertEqual(self.fenster.reiter.count(), 1)

    def test_unbekanntes_format(self):
        p = self.ordner / "x.exe"
        p.write_bytes(b"MZ")
        self.fenster.oeffnen(str(p))
        self.assertEqual(self.fenster.reiter.count(), 1)

    def test_kugel_frage_und_stopp(self):
        gesendet = []
        self.fenster.leitung = mock.Mock(senden=gesendet.append)
        self.fenster.neu("schreiben")
        s = self.fenster.aktuelle_seite()
        s.feld.setPlainText("Sehr geehrte Damen und Herren")
        self.fenster.nemi.fragen("Verbessere das.", "Verbessern")
        auftrag = gesendet[-1]
        self.assertEqual((auftrag["op"], auftrag["app"]), ("frage", "schreiben"))
        self.assertIn("Sehr geehrte", auftrag["kontext"])
        self.assertTrue(self.fenster._esc.isEnabled())                     # ESC hält an, solange sie arbeitet
        self.assertTrue(self.fenster.nemi.kugel.arbeitet)
        self.fenster._ereignis({"t": "antwort", "id": auftrag["id"], "text": "<think>x</think>Fertig!",
                                "geaendert": True})
        self.assertIsNone(self.fenster.nemi.laufend)
        self.assertFalse(self.fenster._esc.isEnabled())
        self.assertEqual(self.fenster.nemi.blase.text.roh, "Fertig!")
        self.fenster.nemi.fragen("Noch was?")
        self.fenster._esc.activated.emit()                                  # ESC
        self.assertEqual(gesendet[-1], {"op": "stopp", "id": gesendet[-2]["id"]})
        self.assertIsNone(self.fenster.nemi.laufend)
        self.assertTrue(self.fenster.ausfuehrer.angehalten)

    def test_hinweis_beendet_keinen_auftrag(self):
        self.fenster.leitung = mock.Mock(senden=lambda a: None)
        self.fenster.neu("tabelle")
        self.fenster.nemi.fragen("Mach was")
        self.fenster.oeffnen(r"\\server\freigabe\x.docx")
        self.assertIsNotNone(self.fenster.nemi.laufend)

    def test_stand_setzt_ordner_und_name(self):
        neu = self.ordner / "anders"
        self.fenster._ereignis({"t": "stand", "name": "PersonA", "ordner": str(neu), "modell": True})
        self.assertEqual(self.fenster.ordner, str(neu))
        self.assertEqual(self.fenster.nemi.blase.titel.text(), "PersonA · OfficeAI")
        self.fenster._ereignis({"t": "stand", "name": "PersonA", "ordner": "relativ/pfad", "modell": True})
        self.assertEqual(self.fenster.ordner, str(neu))


class AgentTest(_Basis):
    """Die OfficeAI arbeitet selbst: Aktionen kommen wie aus der Sitzung, Ergebnisse gehen zurück."""

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(office_agent, "FLUG_MS", 0), mock.patch.object(office_agent, "PAUSE_MS", 0)):
            p.start()
            self.addCleanup(p.stop)
        self.gesendet = []
        self.fenster.leitung = mock.Mock(senden=self.gesendet.append)
        self.schritt = 0

    def _auftrag(self, app: str):
        self.fenster.neu(app)
        seite = self.fenster.aktuelle_seite()
        self.fenster.nemi.fragen("Auftrag")
        return seite, self.gesendet[-1]["id"]

    def _tun(self, nr: int, **aktion) -> dict:
        self.schritt += 1
        vorher = len(self.gesendet)
        self.fenster._ereignis({"t": "aktion", "id": nr, "schritt": self.schritt, "aktion": aktion})
        for _ in range(200):
            neu = [g for g in self.gesendet[vorher:] if g.get("op") == "ergebnis"]
            if neu:
                return neu[-1]
            QApplication.processEvents()
        self.fail("kein Ergebnis")

    def test_tabelle(self):
        t, nr = self._auftrag("tabelle")
        e = self._tun(nr, tool="eintragen", zelle="A1",
                      text="| Posten | Betrag |\n|---|---|\n| Strom | 85 |\n| Miete | 650 |\n| Summe | =SUMME(B2:B3) |")
        self.assertTrue(e["ok"], e)
        self.assertIn("735", e["kontext"]["kontext"])                   # neuer Stand geht mit zurück
        self.assertTrue(self._tun(nr, tool="faerben", bereich="A1:B1", hg="grau", fett=True)["ok"])
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 0, 1), {"hg": "#d9d9d9", "fett": True})
        self.assertTrue(self._tun(nr, tool="faerben", bereich="B4", fg="rot")["ok"])
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 3, 1), {"fg": "#e74c3c"})
        blick = self._tun(nr, tool="ansehen")
        self.assertIn("Sichtbar:", blick["text"])
        self.assertIn("A1 {'hg': '#d9d9d9', 'fett': True}", blick["text"])
        self.assertTrue(self._tun(nr, tool="sortieren", bereich="A2:B3", nach="B", absteigend=True)["ok"])
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 1, 0), "Miete")
        self.assertTrue(self._tun(nr, tool="zeilen", aktion="einfuegen", ab=2)["ok"])
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 4, 1), "=SUMME(B3:B4)")
        self.assertTrue(self._tun(nr, tool="leeren", bereich="A2")["ok"])
        self.assertTrue(self._tun(nr, tool="blatt", name="Auswertung", neu=True)["ok"])
        self.assertEqual(t.modell.blatt.name, "Auswertung")
        self.assertFalse(self._tun(nr, tool="speichern")["ok"])          # gibt es nicht: nur im Dokument
        falsch = self._tun(nr, tool="faerben", bereich="A1", hg="regenbogen")
        self.assertFalse(falsch["ok"])
        self.assertIn("unbekannte Farbe", falsch["text"])
        self.assertFalse(self._tun(nr, tool="faerben", bereich="A1:XFD1048576", hg="grau")["ok"])

    def test_tabelle_rueckgaengig(self):
        t, nr = self._auftrag("tabelle")
        self._tun(nr, tool="eintragen", zelle="B2", text="42")
        self._tun(nr, tool="faerben", bereich="B2", hg="gelb")
        t.rueckgaengig()
        self.assertEqual(t.modell.mappe.format(t.modell.blatt, 1, 1), {})
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 1, 1), "42")

    def test_schreiben(self):
        s, nr = self._auftrag("schreiben")
        s.feld.document().setMarkdown("# Brief\n\nHallo Welt. Hallo Mond.\n\nGruß")
        self.assertTrue(self._tun(nr, tool="ersetzen", suchen="Hallo", durch="Servus", alle=True)["ok"])
        self.assertIn("Servus Welt. Servus Mond.", s.feld.toPlainText())
        self.assertFalse(self._tun(nr, tool="ersetzen", suchen="gibt es nicht", durch="x")["ok"])
        self.assertTrue(self._tun(nr, tool="formatieren", suchen="Welt", farbe="rot", fett=True)["ok"])
        dok = office_schreiben.qt_zu_modell(s.feld.document())
        welt = [l for b in dok.bloecke if hasattr(b, "laeufe") for l in b.laeufe if l.text == "Welt"]
        self.assertEqual((welt[0].farbe, welt[0].fett), ("#e74c3c", True))
        self.assertTrue(self._tun(nr, tool="einfuegen", text="**Neuer Absatz**", wo="nach", bezug="Mond")["ok"])
        bloecke = [s.feld.document().findBlockByNumber(i).text() for i in range(s.feld.document().blockCount())]
        self.assertEqual(bloecke[bloecke.index("Servus Welt. Servus Mond.") + 1], "Neuer Absatz")
        self.assertTrue(self._tun(nr, tool="papier", farbe="gelb")["ok"])
        self.assertTrue(self._tun(nr, tool="neu_schreiben", text="# Neu\n\nKomplett anders.")["ok"])
        self.assertEqual(office_schreiben.papier_von(s.feld.document()), "#fff2cc")   # Papier bleibt
        self.assertTrue(s.feld.toPlainText().startswith("Neu"))
        s.feld.undo()                                                   # Rückgängig holt die alte Fassung
        self.assertIn("Servus", s.feld.toPlainText())
        blick = self._tun(nr, tool="ansehen")
        self.assertIn("Sichtbar", blick["text"])

    def test_bild_einsetzen(self):
        s, nr = self._auftrag("schreiben")
        s.feld.setPlainText("Vorher")
        bild = QImage(60, 40, QImage.Format.Format_RGB32)
        bild.fill(0x22AA66)
        pfad = str(self.ordner / "gemalt.png")
        bild.save(pfad)
        e = self._tun(nr, tool="bild_einfuegen", pfad=pfad, wo="ende")
        self.assertTrue(e["ok"], e)
        dok = office_schreiben.qt_zu_modell(s.feld.document())
        self.assertTrue(any(type(b).__name__ == "Bild" for b in dok.bloecke))
        self.assertFalse(self._tun(nr, tool="bild_einfuegen", pfad=str(self.ordner / "fehlt.png"))["ok"])
        self.assertFalse(self._tun(nr, tool="bild_einfuegen", pfad=r"\\server\x.png")["ok"])
        t, nr2 = self._auftrag("tabelle")
        self.assertFalse(self._tun(nr2, tool="bild_einfuegen", pfad=pfad)["ok"])      # nur im Schreiben

    def test_arbeitet_im_dokument_des_auftrags(self):
        t, nr = self._auftrag("tabelle")
        self.fenster.neu("schreiben")                                  # Nutzer wechselt den Reiter
        self.assertTrue(self._tun(nr, tool="eintragen", zelle="A1", text="x")["ok"])
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 0, 0), "x")
        self.assertIs(self.fenster.aktuelle_seite(), t)                  # zurück zum Dokument des Auftrags

    def test_nach_stopp_nichts_mehr(self):
        t, nr = self._auftrag("tabelle")
        self.fenster.nemi.anhalten()
        e = self._tun(nr, tool="eintragen", zelle="A1", text="x")
        self.assertFalse(e["ok"])
        self.assertEqual(t.modell.mappe.roh(t.modell.blatt, 0, 0), "")

    def test_geistzeiger_fliegt(self):
        t, nr = self._auftrag("tabelle")
        self._tun(nr, tool="faerben", bereich="C5", hg="grau")
        self.assertTrue(self.fenster.zeiger.isVisibleTo(self.fenster))
        self.assertIn("C5", self.fenster.zeiger.text)
        self.fenster._ereignis({"t": "antwort", "id": nr, "text": "Fertig", "geaendert": True})
        self.assertFalse(self.fenster.zeiger.isVisibleTo(self.fenster))

    def test_gesichter(self):
        bild = QImage(40, 40, QImage.Format.Format_RGB32)
        bild.fill(0x336699)
        pfad = str(self.ordner / "gesicht.png")
        bild.save(pfad)
        self.fenster._ereignis({"t": "stand", "name": "PersonA", "modell": True,
                                "gesichter": {"": pfad, "denkt": pfad, "x": "C:/gibt/es/nicht.png", "y": 5}})
        self.assertEqual(set(self.fenster.nemi.gesichter), {"", "denkt"})
        self.assertIsNotNone(self.fenster.nemi.kugel.bild)

    def test_kugel_klappt_aus(self):
        k = self.fenster.nemi
        self.fenster.resize(1200, 800)
        k.ausklappen(fokus=False)
        k._breite.setCurrentTime(k._breite.duration())
        self.assertTrue(k.offen)
        self.assertTrue(k.feld.isVisibleTo(self.fenster))
        k.einklappen()
        self.assertFalse(k.feld.isVisibleTo(self.fenster))


class BefehlTest(unittest.TestCase):
    def test_befehl_und_hilfe(self):
        import commands
        self.assertIn("/office", commands.COMMANDS)
        self.assertEqual(commands.thema_von("/office")[1], "Erschaffen")
        hilfe = next(c for c in commands.COMMAND_LIST if c.name == "/office").help_text
        for wort in (".docx", ".xlsx", ".ods", "Markdown", "OfficeAI", "Rückgängig", "ESC", "=SUMME"):
            self.assertIn(wort, hilfe)

    def test_fenster_verzweigung(self):
        import fensterstart
        self.assertIsNone(fensterstart.ziel(["x", "--office"], True, True))
        quelle = (ROOT / "main_start.py").read_text(encoding="utf-8")
        self.assertIn('"--office" in sys.argv', quelle)
        self.assertIn('elif cmd == "/office":', (ROOT / "main_befehle.py").read_text(encoding="utf-8"))
        self.assertIn("async def start_office", (ROOT / "main_dienste.py").read_text(encoding="utf-8"))

    def test_zahl_text(self):
        self.assertEqual(F.zahl_text(1234.5), "1234,5")


if __name__ == "__main__":
    unittest.main()
