"""Offline-Tests für tools/zeitplan.py – keine echte Aufgabenplanung, kein Modell.

python -m unittest tests.test_zeitplan
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load():
    name = "isolated_zeitplan_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/zeitplan.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module}):
        spec.loader.exec_module(module)
    return module


Z = load()


class NameTests(unittest.TestCase):
    def test_gute_namen(self):
        self.assertEqual(Z.sauberer_name("Systemwache"), "Systemwache")
        self.assertEqual(Z.sauberer_name("  Wache  morgens "), "Wache morgens")
        self.assertEqual(Z.sauberer_name("Prüfung_1-täglich"), "Prüfung_1-täglich")

    def test_schlechte_namen(self):
        for n in ("", "   ", "a/b", "a\\b", "x;y", "x|y", "..", "a" * 61, "name.json", 'x"y'):
            self.assertIsNone(Z.sauberer_name(n), n)


class WannTests(unittest.TestCase):
    def test_taeglich(self):
        for t in ("täglich 09:00", "taeglich 9", "jeden Tag um 9 Uhr", "jeden morgen 09.00"):
            self.assertEqual(Z.parse_wann(t), {"art": "taeglich", "zeit": "09:00"}, t)

    def test_intervall(self):
        self.assertEqual(Z.parse_wann("alle 2 stunden"), {"art": "intervall", "minuten": 120})
        self.assertEqual(Z.parse_wann("alle 30 minuten"), {"art": "intervall", "minuten": 30})
        self.assertEqual(Z.parse_wann("alle 1 std"), {"art": "intervall", "minuten": 60})
        with self.assertRaises(ValueError):
            Z.parse_wann("alle 2 minuten")            # Dauerlast

    def test_anmeldung(self):
        for t in ("anmeldung", "bei der Anmeldung", "beim Anmelden", "login"):
            self.assertEqual(Z.parse_wann(t), {"art": "anmeldung"}, t)

    def test_woechentlich(self):
        self.assertEqual(Z.parse_wann("wöchentlich montag 08:30"),
                         {"art": "woechentlich", "tage": ["Monday"], "zeit": "08:30"})
        self.assertEqual(Z.parse_wann("jeden mo,mi,fr um 18:00"),
                         {"art": "woechentlich", "tage": ["Monday", "Wednesday", "Friday"],
                          "zeit": "18:00"})
        with self.assertRaises(ValueError):
            Z.parse_wann("wöchentlich blautag 09:00")

    def test_einmal(self):
        morgen = datetime.now() + timedelta(days=1)
        w = Z.parse_wann(f"einmal {morgen:%Y-%m-%d} 14:00")
        self.assertEqual(w, {"art": "einmal", "datum": f"{morgen:%Y-%m-%d}", "zeit": "14:00"})
        w = Z.parse_wann(f"am {morgen:%d.%m.%Y} um 14:00")
        self.assertEqual(w["art"], "einmal")
        with self.assertRaises(ValueError):
            Z.parse_wann("einmal 2001-01-01 10:00")     # Vergangenheit

    def test_unsinn_mit_hilfe(self):
        for t in ("", "irgendwann", "täglich", "alle stunden", "9 uhr"):
            with self.assertRaises(ValueError) as cm:
                Z.parse_wann(t)
            self.assertIn("täglich 09:00", str(cm.exception), t)

    def test_wann_text(self):
        self.assertEqual(Z.wann_text({"art": "intervall", "minuten": 120}), "alle 2 Stunden")
        self.assertEqual(Z.wann_text({"art": "intervall", "minuten": 45}), "alle 45 Minuten")
        self.assertEqual(Z.wann_text({"art": "taeglich", "zeit": "09:00"}), "täglich um 09:00")
        self.assertIn("Montag", Z.wann_text({"art": "woechentlich", "tage": ["Monday"], "zeit": "08:30"}))


class XmlTests(unittest.TestCase):
    """Die Aufgaben-XML ist reiner Text – hier wird geprüft, dass sie gültig ist,
    ohne erhöhte Rechte läuft und nur NemiCLI startet."""

    def _xml(self, w, name="Wache", beschreibung="NemiCLI-Auftrag: x"):
        import xml.etree.ElementTree as ET
        text = Z.aufgabe_xml(name, w, beschreibung)
        return text, ET.fromstring(text.split("\n", 1)[1])

    def test_aufgabe_xml(self):
        text, wurzel = self._xml({"art": "taeglich", "zeit": "09:00"})
        self.assertIn("--auftrag &quot;Wache&quot;", text)
        self.assertIn("<RunLevel>LeastPrivilege</RunLevel>", text)
        self.assertIn("<ExecutionTimeLimit>PT30M</ExecutionTimeLimit>", text)
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", text)
        self.assertTrue(wurzel.tag.endswith("Task"))

    def test_sonderzeichen_werden_maskiert(self):
        text, _ = self._xml({"art": "anmeldung"}, beschreibung="a < b & \"c\"")
        self.assertIn("a &lt; b &amp; &quot;c&quot;", text)

    def test_programm_ist_nemicli(self):
        exe, args = Z.programm()
        self.assertTrue(exe.lower().endswith(("pythonw.exe", "python.exe", "nemicli.exe")), exe)
        self.assertIn("--auftrag", args)

    def test_trigger_arten(self):
        jetzt = datetime(2030, 1, 1, 10, 0)
        self.assertIn("<Interval>PT120M</Interval>",
                      Z._trigger_xml({"art": "intervall", "minuten": 120}, jetzt))
        self.assertIn("<LogonTrigger>", Z._trigger_xml({"art": "anmeldung"}, jetzt))
        wo = Z._trigger_xml({"art": "woechentlich", "tage": ["Monday", "Friday"], "zeit": "08:30"}, jetzt)
        self.assertIn("<Monday/><Friday/>", wo)
        self.assertIn("2030-01-01T08:30:00", wo)
        self.assertIn("<StartBoundary>2030-01-01T14:00:00</StartBoundary>",
                      Z._trigger_xml({"art": "einmal", "datum": "2030-01-01", "zeit": "14:00"}, jetzt))
        for w in ({"art": "intervall", "minuten": 5}, {"art": "anmeldung"},
                  {"art": "taeglich", "zeit": "09:00"}):
            self._xml(w)                    # parsebar


class AbläufeTests(unittest.TestCase):
    """anlegen/loeschen/liste mit vorgetäuschtem schtasks."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._p = [patch.object(Z, "ORDNER", base / "Zeitplan"),
                   patch.object(Z, "BERICHTE", base / "Berichte"),
                   patch.object(Z, "_ROOT", base)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.tmp.cleanup()

    def test_anlegen_schreibt_auftrag_und_ruft_windows(self):
        ok = NS(returncode=0, stdout="ok\n", stderr="")
        with patch.object(Z, "_schtasks", return_value=ok) as st:
            text = Z.anlegen("Wache", "täglich 09:00", "Prüf das System.")
        self.assertIn("täglich um 09:00", text)
        d = json.loads((Z.ORDNER / "Wache.json").read_text(encoding="utf-8"))
        self.assertEqual(d["auftrag"], "Prüf das System.")
        self.assertEqual(d["wann"], {"art": "taeglich", "zeit": "09:00"})
        args = st.call_args[0]
        self.assertEqual(args[:3], ("/create", "/tn", "\\NemiCLI\\Wache"))
        self.assertIn("/xml", args)

    def test_anlegen_raeumt_auf_wenn_windows_nein_sagt(self):
        nein = NS(returncode=1, stdout="", stderr="Zugriff verweigert")
        with patch.object(Z, "_schtasks", return_value=nein):
            with self.assertRaises(ValueError) as cm:
                Z.anlegen("Wache", "täglich 09:00", "x")
        self.assertIn("Zugriff verweigert", str(cm.exception))
        self.assertFalse((Z.ORDNER / "Wache.json").exists())

    def test_anlegen_prueft_eingaben_vor_windows(self):
        with patch.object(Z, "_schtasks") as st:
            for name, wann, auftrag in (("a/b", "täglich 9", "x"), ("W", "nie", "x"),
                                        ("W", "täglich 9", ""), ("W", "täglich 9", "x" * 5000)):
                with self.assertRaises(ValueError):
                    Z.anlegen(name, wann, auftrag)
        st.assert_not_called()

    def test_loeschen(self):
        Z.ORDNER.mkdir(parents=True)
        (Z.ORDNER / "Wache.json").write_text("{}", encoding="utf-8")
        ok = NS(returncode=0, stdout="ok", stderr="")
        with patch.object(Z, "_schtasks", return_value=ok) as st:
            self.assertIn("gelöscht", Z.loeschen("Wache"))
        self.assertEqual(st.call_args[0], ("/delete", "/tn", "\\NemiCLI\\Wache", "/f"))
        self.assertFalse((Z.ORDNER / "Wache.json").exists())

    def test_loeschen_unbekannt(self):
        nein = NS(returncode=1, stdout="", stderr="nicht gefunden")
        with patch.object(Z, "_schtasks", return_value=nein):
            with self.assertRaises(ValueError):
                Z.loeschen("Gibtsnicht")

    def test_liste(self):
        Z.ORDNER.mkdir(parents=True)
        (Z.ORDNER / "Wache.json").write_text(json.dumps(
            {"name": "Wache", "auftrag": "Prüf das System.", "wann_text": "täglich um 09:00"}),
            encoding="utf-8")
        zeile = ('"host","\\NemiCLI\\Wache","19.09.2026 09:00:00","Bereit","Interaktiv",'
                 '"18.09.2026 09:00:00","0","autor"\n')
        with patch.object(Z, "_schtasks", return_value=NS(returncode=0, stderr="", stdout=zeile)):
            text = Z.liste()
        self.assertIn("Wache – täglich um 09:00 – Status: Bereit", text)
        self.assertIn("nächster: 19.09.2026", text)
        self.assertIn("Prüf das System.", text)

    def test_liste_leer(self):
        with patch.object(Z, "_schtasks", return_value=NS(returncode=1, stdout="", stderr="")):
            self.assertEqual(Z.liste(), "Keine Aufgaben eingetragen.")


class BerichtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._p = [patch.object(Z, "BERICHTE", base / "Berichte"), patch.object(Z, "_ROOT", base)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.tmp.cleanup()

    def test_kopfzeile_ueberspringt_kommentar(self):
        p = Z.bericht_pfad("Wache")
        p.write_text("<!-- NemiCLI-Bericht -->\n\n✅ Alles OK – 212 Prozesse, nichts Auffälliges.\n\nDetails …",
                     encoding="utf-8")
        self.assertEqual(Z.bericht_kopfzeile(p), "✅ Alles OK – 212 Prozesse, nichts Auffälliges.")
        self.assertEqual(Z.letzter_bericht("Wache"), p)
        self.assertIsNone(Z.letzter_bericht("Andere"))

    def test_neue_berichte_nur_einmal(self):
        p = Z.bericht_pfad("Wache")
        p.write_text("x", encoding="utf-8")
        self.assertEqual(Z.neue_berichte(), [p])
        self.assertEqual(Z.neue_berichte(), [])        # Marker gesetzt: schon gesehen


if __name__ == "__main__":
    unittest.main()
