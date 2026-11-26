"""Offline-Tests: Office-Pakete als Anhang (tools/anhang.py, lese_office) und Strg+V-Helfer im Fenster.

python -m unittest discover -s tests -p test_anhang_office.py
"""

import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import anhang                   # noqa: E402
import terminal_fenster as TF   # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
PR = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
ODF = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"')


class Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def paket(self, name: str, teile: dict[str, str]) -> Path:
        p = self.t / name
        with zipfile.ZipFile(p, "w") as z:
            for k, v in teile.items():
                z.writestr(k, v)
        return p


DOCX = {"word/document.xml": f"""<w:document {W}><w:body>
<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr><w:r><w:t>Titel der Notiz</w:t></w:r></w:p>
<w:p><w:r><w:t>Eins</w:t></w:r><w:r><w:tab/><w:t>Zwei</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B1</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""}

XLSX = {
    "xl/workbook.xml": f'<workbook {S} {R}><sheets><sheet name="Liste" sheetId="1" r:id="rId1"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels": f'<Relationships {PR}><Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                                  '</Relationships>',
    "xl/sharedStrings.xml": f"<sst {S}><si><t>Name</t></si><si><t>Menge</t></si><si><t>Apfel</t></si></sst>",
    "xl/worksheets/sheet1.xml": f"""<worksheet {S}><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2"><v>4711</v></c></row>
<row r="3"><c r="A3" t="inlineStr"><is><t>frei</t></is></c><c r="B3" t="b"><v>1</v></c></row>
</sheetData></worksheet>"""}

PPTX = {
    "ppt/presentation.xml": f'<p:presentation {P} {R}><p:sldIdLst><p:sldId id="256" r:id="rId2"/>'
                            '<p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>',
    "ppt/_rels/presentation.xml.rels": f'<Relationships {PR}><Relationship Id="rId1" Target="slides/slide1.xml"/>'
                                       '<Relationship Id="rId2" Target="slides/slide2.xml"/></Relationships>',
    "ppt/slides/slide1.xml": f"<p:sld {P} {A}><a:p><a:r><a:t>Zweite Folie</a:t></a:r></a:p></p:sld>",
    "ppt/slides/slide2.xml": f"<p:sld {P} {A}><a:p><a:r><a:t>Erste</a:t></a:r><a:br/><a:r><a:t>Zeile</a:t></a:r>"
                             "</a:p></p:sld>"}


class OfficeLesen(Basis):
    def test_office_ist_eigene_art(self):
        for end in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"):
            self.assertEqual(anhang._art(self.t / f"x{end}"), "office", end)

    def test_word_absaetze_tabs_und_tabelle(self):
        text, gesamt, _ = anhang.lese_office(self.paket("a.docx", DOCX))
        self.assertEqual(text, "Titel der Notiz\nEins\tZwei\nA1\tB1")   # Tabstopp-Angabe zählt nicht als Tab
        self.assertEqual(gesamt, len(text))

    def test_excel_mit_geteilten_texten_und_luecken(self):
        text, _, zusatz = anhang.lese_office(self.paket("a.xlsx", XLSX))
        self.assertEqual(text, "--- Blatt Liste ---\nName\tMenge\nApfel\t\t4711\nfrei\twahr")
        self.assertEqual(zusatz, "1 Blatt")

    def test_powerpoint_in_der_reihenfolge_der_praesentation(self):
        text, _, zusatz = anhang.lese_office(self.paket("a.pptx", PPTX))
        self.assertEqual(text, "--- Folie 1 ---\nErste\nZeile\n--- Folie 2 ---\nZweite Folie")
        self.assertEqual(zusatz, "2 Folie(n)")

    def test_writer(self):
        p = self.paket("a.odt", {"content.xml": f"""<office:document-content {ODF}><office:body><office:text>
<text:h>Kopf</text:h><text:p>a<text:s text:c="2"/>b<text:tab/>c<text:line-break/>d</text:p>
</office:text></office:body></office:document-content>"""})
        self.assertEqual(anhang.lese_office(p)[0], "Kopf\na  b\tc\nd")

    def test_calc_mit_wiederholten_spalten(self):
        p = self.paket("a.ods", {"content.xml": f"""<office:document-content {ODF}><office:body><office:spreadsheet>
<table:table table:name="T"><table:table-row><table:table-cell><text:p>x</text:p></table:table-cell>
<table:table-cell table:number-columns-repeated="2"/><table:table-cell><text:p>y</text:p></table:table-cell>
<table:table-cell table:number-columns-repeated="16000"/></table:table-row></table:table>
</office:spreadsheet></office:body></office:document-content>"""})
        self.assertEqual(anhang.lese_office(p)[0], "--- Blatt T ---\nx\t\t\ty")

    def test_impress(self):
        p = self.paket("a.odp", {"content.xml": f"""<office:document-content {ODF}><office:body><office:presentation>
<draw:page><draw:frame><draw:text-box><text:p>Hallo</text:p></draw:text-box></draw:frame></draw:page>
</office:presentation></office:body></office:document-content>"""})
        self.assertEqual(anhang.lese_office(p)[0], "--- Folie 1 ---\nHallo")

    def test_kein_paket_meldet_es(self):
        p = self.t / "alt.docx"
        p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 100)        # verschlüsselt/alt: kein ZIP
        with self.assertRaisesRegex(RuntimeError, "kein Office-Paket"):
            anhang.lese_office(p)

    def test_zu_grosser_teil_wird_nicht_gelesen(self):
        alt = anhang.MAX_XML_BYTES
        try:
            anhang.MAX_XML_BYTES = 50
            with self.assertRaisesRegex(RuntimeError, "größer"):
                anhang.lese_office(self.paket("a.docx", DOCX))
        finally:
            anhang.MAX_XML_BYTES = alt

    def test_anhaengen_haengt_word_als_daten_an(self):
        p = self.paket("bericht.docx", DOCX)
        e = anhang.anhaengen(f"{p} fass zusammen")
        self.assertEqual([(x.name, art) for x, art, _ in e["gelesen"]], [("bericht.docx", "office")])
        self.assertIn("ANGEHÄNGTE DATEI (Word)", e["text"])
        self.assertIn("Titel der Notiz", e["text"])
        self.assertNotIn(str(p), e["text"].split("Pfad:")[0])

    def test_leeres_dokument_wird_uebergangen(self):
        p = self.paket("leer.docx", {"word/document.xml": f"<w:document {W}><w:body/></w:document>"})
        e = anhang.anhaengen(str(p))
        self.assertEqual(e["gelesen"], [])
        self.assertIn("kein Text", e["uebergangen"][0][1])


class Platz(Basis):
    def test_budget_folgt_dem_kontext_mit_untergrenze(self):
        from unittest import mock
        import pricing
        with mock.patch.object(pricing, "context_window", return_value=128_000):
            self.assertEqual(anhang.budget("x"), 192_000)
        with mock.patch.object(pricing, "context_window", return_value=512):
            self.assertEqual(anhang.budget("x"), anhang.MAX_ZEICHEN)

    def test_lange_datei_kommt_ganz_wenn_platz_ist(self):
        p = self.t / "lang.md"
        p.write_text("a" * 44_507, encoding="utf-8")
        e = anhang.anhaengen(str(p), 192_000)
        self.assertNotIn("gekürzt", e["gelesen"][0][2])
        self.assertEqual(e["text"].count("a" * 1000), 44)

    def test_dateien_teilen_sich_den_platz(self):
        a, b, c = self.t / "a.txt", self.t / "b.txt", self.t / "c.txt"
        a.write_text("x" * 3000, encoding="utf-8")
        b.write_text("y" * 3000, encoding="utf-8")
        c.write_text("z" * 3000, encoding="utf-8")
        e = anhang.anhaengen(f"{a} {b} {c}", 4000)
        self.assertEqual([x.name for x, _, _ in e["gelesen"]], ["a.txt", "b.txt"])
        self.assertIn("gekürzt", e["gelesen"][1][2])                  # b bekommt den Rest
        self.assertEqual(e["uebergangen"][0][0].name, "c.txt")
        self.assertIn("datei_lesen", e["uebergangen"][0][1])
        self.assertIn(f"Nicht angehängt (Kontext voll): {c}", e["text"])


class Einfuegen(Basis):
    def test_nur_link(self):
        self.assertTrue(TF.nur_link(" https://example.com/bild.png "))
        self.assertFalse(TF.nur_link("schau https://example.com"))
        self.assertFalse(TF.nur_link(""))

    def test_alte_bilder_werden_aufgeraeumt(self):
        alt, neu, fremd = self.t / "bild_alt.png", self.t / "bild_neu.png", self.t / "andere.png"
        for p in (alt, neu, fremd):
            p.write_bytes(b"x")
        vor = time.time() - 8 * 86400
        os.utime(alt, (vor, vor))
        os.utime(fremd, (vor, vor))
        TF.einfuegungen_aufraeumen(self.t)
        self.assertEqual((alt.exists(), neu.exists(), fremd.exists()), (False, True, True))

    def test_ordner_liegt_im_temp(self):
        self.assertEqual(TF.einfuege_ordner().parent.parent, Path(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
