"""Wächter (tools/kiwertung.py): ➕/➖ des Nutzers, Werte, Temperatur-Lerner – und dass keine KI den Befehl sieht.

python -m unittest tests.test_kiwertung
"""
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "tools", "engines", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import commands as C  # noqa: E402
import kiwertung as KW  # noqa: E402
import kontrollzentrum as KZ  # noqa: E402
import main_quelle  # noqa: E402
import persona  # noqa: E402


class _Db(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "ki_wertung.db"
        for p in (mock.patch.object(KW, "DB", self.db), mock.patch.object(KW, "steuern_an", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        KW._offen.clear()
        KW._letzte_temperatur.clear()
        self.addCleanup(KW._offen.clear)


class Wertung(_Db):
    def test_wert_lesen(self):
        for t in ("+", "➕", "👍", "gut"):
            self.assertEqual(1, KW.wert_lesen(t))
        for t in ("-", "–", "➖", "👎", "schlecht"):
            self.assertEqual(-1, KW.wert_lesen(t))
        self.assertIsNone(KW.wert_lesen("vielleicht"))

    def test_ki_erfaehrt_nur_das_zeichen(self):
        KW.werten(-1, "cloud:a", "Alpha", "hat den Kern ungefragt geändert", ["bilder"], 300)
        KW.werten(1, "cloud:a", "Alpha", "", [], 100)
        notiz = KW.notiz_abholen()
        self.assertIn("[Wächter]", notiz)
        self.assertIn("➖ ➕", notiz)
        self.assertNotIn("Kern", notiz)                     # der Grund bleibt beim Nutzer
        self.assertNotIn("wächter", notiz.lower().replace("[wächter]", ""))
        self.assertEqual("", KW.notiz_abholen())            # nur einmal

    def test_rangliste_vorsichtig_sortiert(self):
        for _ in range(3):
            KW.werten(1, "cloud:wenig", "Alpha")
        for i in range(30):
            KW.werten(1 if i % 10 else -1, "cloud:viel", "Beta", themen=["coding"] if i % 2 else ["bilder"])
        KW.werten(-1, "cloud:schlecht", "Alpha")
        liste = KW.werte(self.db)
        self.assertEqual(["cloud:viel", "cloud:wenig", "cloud:schlecht"], [m["modell"] for m in liste])
        viel = liste[0]
        self.assertEqual((27, 3, 24), (viel["plus"], viel["minus"], viel["punkte"]))
        self.assertEqual(24, viel["verlauf"][-1])
        self.assertEqual({"bilder", "coding"}, set(viel["themen"]))
        self.assertEqual({"Beta": [27, 3]}, viel["persoenlichkeiten"])
        self.assertLess(KW.wilson(3, 3), KW.wilson(27, 30))

    def test_werten_braucht_modell(self):
        with self.assertRaises(ValueError):
            KW.werten(1, "")
        with self.assertRaises(ValueError):
            KW.werten(0, "cloud:a")


class Temperatur(_Db):
    def test_aus_heisst_vorgabe_des_anbieters(self):
        self.assertIsNone(KW.temperatur_fuer("cloud:a", self.db))
        KW.werten(1, "cloud:a")
        self.assertIsNone(KW.werte(self.db)[0]["beste_temperatur"])

    def test_lerner_bevorzugt_die_beste_stufe(self):
        with mock.patch.object(KW, "steuern_an", return_value=True):
            for t, wert in [(0.7, 1)] * 8 + [(0.3, -1)] * 6 + [(0.9, -1)] * 4:
                KW._letzte_temperatur["cloud:a"] = t
                KW.werten(wert, "cloud:a")
            zufall = random.Random(1)
            wahl = [KW.temperatur_fuer("cloud:a", self.db, zufall) for _ in range(200)]
        self.assertGreater(wahl.count(0.7), 120)
        self.assertEqual(0.7, KW.werte(self.db)[0]["beste_temperatur"])
        self.assertEqual(wahl[-1], KW._letzte_temperatur["cloud:a"])           # gilt für die nächste Wertung

    def test_cloud_setzt_temperatur_nur_ohne_denkstufe(self):
        import cloud
        with mock.patch.object(KW, "temperatur_fuer", return_value=0.5):
            self.assertEqual(0.5, cloud._waechter_temperatur("ollama_cloud", "m", {"max_tokens": 9}))
            self.assertIsNone(cloud._waechter_temperatur("moonshot", "k", {"reasoning_effort": "low"}))
            self.assertIsNone(cloud._waechter_temperatur("x", "m", {"temperature": 0}))
        src = (ROOT / "engines" / "cloud.py").read_text(encoding="utf-8")
        self.assertIn('kwargs.pop("temperature")', src)                         # abgelehnt: ohne sie erneut


class Unsichtbar(unittest.TestCase):
    """Den Befehl sieht keine KI – weder in der Hilfe noch in den Listen und der Suche des Controllcenters."""

    def test_fuer_den_nutzer_da(self):
        self.assertIn("/wächter", C.COMMANDS)
        self.assertIn("/wächter", C.NUR_NUTZER)
        namen = [c.name for _z, _t, b in C.nach_themen() for c in b]
        self.assertIn("/wächter", namen)

    def test_nicht_in_ki_hilfe(self):
        self.assertNotIn("/wächter", C.hilfe_fuer_ki())
        self.assertNotIn("/wächter", C.hilfe_fuer_ki("/wächter"))
        self.assertNotIn("/wächter", C.hilfe_fuer_ki("wächter"))
        self.assertNotIn("/wächter", persona.anleitung("nemicli"))

    def test_nicht_bei_der_controllcenter_ki(self):
        self.assertNotIn("/wächter", KZ.befehle_uebersicht())
        self.assertNotIn("/wächter", KZ.system_prompt("Test"))
        self.assertNotIn("/wächter", KZ.suchen("wächter werten daumen"))
        self.assertEqual(("", ""), KZ.befehl_aufloesen("wächter"))
        self.assertNotEqual("/wächter", KZ.befehl_aufloesen("/waechter")[0])
        for tool in ("zeigen", "hilfe", "einstellen"):
            self.assertIsNotNone(KZ.aktion_pruefen({"tool": tool, "befehl": "/wächter", "zusatz": "+"}))
        self.assertNotIn("wächter", KZ.prompt({"text": "x", "gewaehlt": {"name": "/wächter", "lang": "geheim"}}))
        self.assertNotIn("rangliste", KZ._SEITEN)                          # die KI kann die Seite nicht zeigen
        quelle = main_quelle.text()
        self.assertIn('gewaehlt_id != "rangliste"', quelle)

    def test_vor_statistik_und_ablauf_abgefangen(self):
        quelle = main_quelle.text()
        abfang = quelle.index('if cmd in ("/wächter", "/waechter"):')
        self.assertLess(abfang, quelle.index("stats.record_command(cmd)"))
        self.assertIn("user_text += kiwertung.notiz_abholen()", quelle)

    def test_anleitung_erklaert_nur_das_zeichen(self):
        basis = persona.base_prompt()
        self.assertIn("[Wächter] 🛡", basis)
        self.assertNotIn("/wächter", basis)


class Seite(unittest.TestCase):
    def test_toplist_seite(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import control_fenster as CF
        gesendet = []
        leitung = mock.MagicMock()
        leitung.senden.side_effect = gesendet.append
        f = CF.ControlFenster(leitung)
        self.addCleanup(f.deleteLater)
        self.assertIn("rangliste", [a["id"] for a in KZ.baum()])
        f.waehlen_id("rangliste")
        self.assertEqual({"op": "rangliste"}, gesendet[-1])
        m = {"modell": "cloud:a", "plus": 5, "minus": 2, "punkte": 3, "quote": 5 / 7, "sicher": 0.4,
             "verlauf": [1, 2, 1, 2, 3, 4, 3], "themen": {"bilder": [3, 1]}, "beste_temperatur": 0.7}
        f._ereignis({"t": "rangliste", "liste": [m, {**m, "modell": "cloud:b", "punkte": -1}], "temperatur_an": True})
        self.assertTrue(f.rangliste.schalter.isChecked())
        grafik = f.rangliste.findChildren(CF._WertGrafik)
        self.assertEqual(2, len(grafik))
        f.rangliste.resize(900, 700)
        grafik[0].resize(700, 70)
        grafik[0].grab()                                                    # malt ohne Fehler
        f.rangliste.schalter.setChecked(False)
        self.assertEqual({"op": "rangliste_temperatur", "an": False}, gesendet[-1])


if __name__ == "__main__":
    unittest.main()
