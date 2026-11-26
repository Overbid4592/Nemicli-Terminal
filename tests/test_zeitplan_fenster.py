"""Zeitplan-Fenster: Vorschläge der KI, Kalender-Helfer und das Fenster selbst (offscreen)."""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import actions  # noqa: E402
import qt_testhilfe  # noqa: E402
import sicherheit  # noqa: E402
import zeitplan as Z  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import zeitplan_fenster
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # PySide6 fehlt
    zeitplan_fenster = None


class _Ordner(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ordner = Path(tmp.name) / "Zeitplan"
        for p in (mock.patch.object(Z, "ORDNER", ordner), mock.patch.object(Z, "VORSCHLAEGE", ordner / "_vorschlaege"),
                  mock.patch.object(Z, "BERICHTE", Path(tmp.name) / "Berichte")):
            p.start()
            self.addCleanup(p.stop)
        self.ordner = ordner


class Helfer(_Ordner):
    def test_eingabe_hin_und_zurueck(self):
        morgen = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        for text in ("täglich 09:00", "wöchentlich mo,mi,fr 18:30", "alle 30 minuten", "alle 2 stunden",
                     "anmeldung", f"einmal {morgen} 14:00"):
            w = Z.parse_wann(text)
            self.assertEqual(w, Z.parse_wann(Z.wann_eingabe(w)), text)

    def test_laeuft_am(self):
        montag = date(2026, 11, 16)
        self.assertEqual(["09:00"], Z.laeuft_am(Z.parse_wann("täglich 09:00"), montag))
        woche = Z.parse_wann("wöchentlich mo,fr 18:00")
        self.assertEqual(["18:00"], Z.laeuft_am(woche, montag))
        self.assertEqual([], Z.laeuft_am(woche, montag + timedelta(days=1)))
        self.assertEqual(["14:00"], Z.laeuft_am({"art": "einmal", "datum": "2026-11-16", "zeit": "14:00"}, montag))
        self.assertEqual(["laufend"], Z.laeuft_am({"art": "intervall", "minuten": 30}, montag))
        self.assertEqual([], Z.laeuft_am({"art": "anmeldung"}, montag))

    def test_vorschlag_ablegen_und_lesen(self):
        p = Z.vorschlag_ablegen("anlegen", "Morgen Bericht", "täglich 08:00", "Prüf das System.", wer="Alpha")
        d = Z.vorschlag_lesen(p)
        self.assertEqual(("anlegen", "Morgen Bericht", "taeglich", "Alpha"),
                         (d["aktion"], d["name"], d["wann"]["art"], d["wer"]))
        self.assertFalse(p.exists())                                     # einmal gelesen, weg
        self.assertIsNone(Z.vorschlag_lesen(p))
        with self.assertRaises(ValueError):
            Z.vorschlag_ablegen("anlegen", "x/../y", "täglich 08:00", "a")
        with self.assertRaises(ValueError):
            Z.vorschlag_ablegen("anlegen", "x", "irgendwann", "a")
        with self.assertRaises(ValueError):
            Z.vorschlag_ablegen("loeschen", "Gibt es nicht")

    def test_vorschlag_nur_aus_eigenem_ordner(self):
        fremd = self.ordner.parent / "fremd.json"
        fremd.write_text('{"aktion": "anlegen", "name": "x", "wann": {"art": "anmeldung"}, "auftrag": "a"}',
                         encoding="utf-8")
        self.assertIsNone(Z.vorschlag_lesen(fremd))
        self.assertTrue(fremd.exists())

    def test_fenster_befehl(self):
        p = self.ordner / "_vorschlaege" / "a.json"
        befehl = Z.fenster_befehl(p)
        self.assertIn("--zeitplan", befehl)
        self.assertEqual(["--vorschlag", str(p)], befehl[-2:])


class Werkzeug(_Ordner):
    def test_ki_schlaegt_nur_vor(self):
        with mock.patch.object(Z, "fenster_oeffnen") as auf, mock.patch.object(Z, "anlegen") as eintragen:
            r = actions._zeitplan({"aktion": "anlegen", "name": "Morgen", "wann": "täglich 08:00",
                                   "auftrag": "Prüf das System."})
        self.assertTrue(r.ok, r.text)
        self.assertIn("erst, wenn der Nutzer", r.text)
        auf.assert_called_once()
        eintragen.assert_not_called()
        self.assertEqual(1, len(list((self.ordner / "_vorschlaege").glob("*.json"))))

    def test_fehler_gehen_an_die_ki(self):
        with mock.patch.object(Z, "fenster_oeffnen") as auf:
            r = actions._zeitplan({"aktion": "anlegen", "name": "Morgen", "wann": "bald", "auftrag": "x"})
        self.assertFalse(r.ok)
        auf.assert_not_called()

    def test_nicht_im_hintergrund(self):
        with mock.patch.object(sys, "argv", ["main.py", "--auftrag", "x"]), \
                mock.patch.object(Z, "fenster_oeffnen") as auf:
            r = actions._zeitplan({"aktion": "anlegen", "name": "M", "wann": "täglich 08:00", "auftrag": "x"})
        self.assertFalse(r.ok)
        auf.assert_not_called()

    def test_harmlos_ohne_rueckfrage(self):
        act = {"tool": "zeitplan", "aktion": "anlegen", "name": "M"}
        self.assertEqual("harmlos", sicherheit.stufe(act))
        self.assertFalse(actions.needs_confirm(act))


@unittest.skipIf(zeitplan_fenster is None, "PySide6 fehlt")
class Fenster(_Ordner):
    def setUp(self):
        super().setUp()
        self.app = QApplication.instance() or QApplication([])
        self.windows = mock.patch.object(Z, "_windows_liste", return_value={})
        self.windows.start()
        self.addCleanup(self.windows.stop)

    def _fenster(self, vorschlag=None):
        f = zeitplan_fenster.ZeitplanFenster(vorschlag)
        self.addCleanup(f.deleteLater)
        return f

    def test_vorschlag_fuellt_und_speichert_erst_mit_klick(self):
        d = {"aktion": "anlegen", "name": "Abend", "wer": "Alpha", "auftrag": "Fass den Tag zusammen.",
             "wann": Z.parse_wann("wöchentlich mo,mi 20:15")}
        with mock.patch.object(Z, "anlegen", return_value="Aufgabe 'Abend' angelegt") as eintragen:
            f = self._fenster(d)
            self.assertTrue(f.banner.isVisibleTo(f))
            self.assertEqual("Abend", f.name.text())
            self.assertEqual("wöchentlich mo,mi 20:15", f.eingabe())
            eintragen.assert_not_called()
            f.speichern()
            eintragen.assert_called_once_with("Abend", "wöchentlich mo,mi 20:15", "Fass den Tag zusammen.")

    def test_arten_ergeben_gueltige_eingaben(self):
        f = self._fenster()
        f.datum.setDate(QDate.currentDate().addDays(3))
        for art in ("taeglich", "woechentlich", "intervall", "einmal", "anmeldung"):
            f._art_setzen(art)
            if art == "woechentlich":
                f.tage[2].setzen(True)
            Z.parse_wann(f.eingabe())                                   # wirft nicht

    def test_kalender_zeigt_laeufe(self):
        Z.ORDNER.mkdir(parents=True)
        (Z.ORDNER / "Tag.json").write_text(
            '{"name": "Tag", "auftrag": "a", "wann": {"art": "taeglich", "zeit": "07:30"}, "wann_text": "täglich"}',
            encoding="utf-8")
        f = self._fenster()
        f._tag_gewaehlt(QDate.currentDate())
        self.assertIn("07:30 Uhr  Tag", f.tag_text.text())
        self.assertEqual(1, f.liste.count())

    def test_loeschen_fragt_nach(self):
        Z.ORDNER.mkdir(parents=True)
        (Z.ORDNER / "Weg.json").write_text(
            '{"name": "Weg", "auftrag": "a", "wann": {"art": "anmeldung"}, "wann_text": "bei Anmeldung"}',
            encoding="utf-8")
        f = self._fenster({"aktion": "loeschen", "name": "Weg", "wer": "Alpha"})
        self.assertEqual("Weg", f.geladen)
        with mock.patch.object(zeitplan_fenster.QMessageBox, "question",
                               return_value=zeitplan_fenster.QMessageBox.StandardButton.No), \
                mock.patch.object(Z, "loeschen") as weg:
            f.loeschen()
            weg.assert_not_called()
        with mock.patch.object(zeitplan_fenster.QMessageBox, "question",
                               return_value=zeitplan_fenster.QMessageBox.StandardButton.Yes), \
                mock.patch.object(Z, "loeschen", return_value="Aufgabe 'Weg' gelöscht.") as weg:
            f.loeschen()
            weg.assert_called_once_with("Weg")


if __name__ == "__main__":
    unittest.main()
