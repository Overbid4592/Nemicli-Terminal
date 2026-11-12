"""Programm-Absolvierung (tools/wache/absolvierung.py): ein Programm auf Anweisung beobachten, sein Normalbild an
Hintergrund-Prozessen lernen und danach Neues als R015 melden – ohne echte Prozesse.

python -m unittest tests.test_programm_absolvierung
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from wache import absolvierung as AB                   # noqa: E402
from wache import werkzeuge as W                       # noqa: E402
from wache.ereignisse import PROZESS, Ereignis, Schwere   # noqa: E402


def start(name, pid, ppid=0, eltern="", exe="", t=100.0, eltern_t=0.0):
    return Ereignis(PROZESS, "process_start", f"Prozess gestartet: {name}", prozess=name, pid=pid, ppid=ppid,
                    eltern=eltern, exe=exe, start=t, eltern_start=eltern_t)


class Grundlagen(unittest.TestCase):

    def setUp(self):
        self.ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.ordner, True)

    def test_schluessel(self):
        for ein in ("Steam", "steam.exe", r"C:\Program Files (x86)\Steam\steam.exe", '"STEAM.EXE"'):
            self.assertEqual("steam.exe", AB.schluessel(ein))
        for kaputt in ("", "a/b/../", "x" * 200, "steam.dll"):
            with self.subTest(kaputt=kaputt), self.assertRaises(ValueError):
                AB.schluessel(kaputt)

    def test_auftrag_starten_stoppen_erlauben(self):
        a = AB.starten("Steam", 2, von="Mira", ordner=self.ordner)
        self.assertEqual(("steam.exe", "Steam", "Mira", False), (a["programm"], a["anzeige"], a["von"], a["aus"]))
        self.assertTrue(AB.lernt(a))
        self.assertTrue(AB.erlauben("steam", "Foo.exe", ordner=self.ordner)["erlaubt"] == ["foo.exe"])
        self.assertTrue(AB.stoppen("steam", ordner=self.ordner)["aus"])
        neu = AB.starten("steam", 0, ordner=self.ordner)       # neu gestartet: erlaubt bleibt, Lernen vorbei
        self.assertFalse(AB.lernt(neu))
        self.assertEqual(["foo.exe"], neu["erlaubt"])
        self.assertIsNone(AB.stoppen("discord", ordner=self.ordner))
        self.assertEqual(["steam.exe"], [x["programm"] for x in AB.auftraege(self.ordner)])


class Beobachten(unittest.TestCase):

    def setUp(self):
        self.ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.ordner, True)
        p = mock.patch.object(AB.psutil, "process_iter", return_value=[])     # nichts läuft schon
        p.start()
        self.addCleanup(p.stop)
        self.urteile = {}
        self.ab = AB.Absolvierung(self.ordner, urteile=lambda regel, subjekt: self.urteile.get(subjekt, {}))

    def auftrag(self, tage):
        AB.starten("steam", tage, ordner=self.ordner)
        self.ab.auftraege_pruefen(erzwingen=True)

    def nachlernen_beenden(self):
        pfad = self.ordner / "steam.exe.auftrag.json"
        AB.starten("steam", 0, ordner=self.ordner)
        st = pfad.stat().st_mtime + 5
        os.utime(pfad, (st, st))
        self.ab.auftraege_pruefen(erzwingen=True)

    def test_lernen_dann_wachen(self):
        self.auftrag(3)
        self.assertEqual([], self.ab.verarbeiten(start("steam.exe", 10, exe=r"C:\Steam\steam.exe", t=100)))
        self.assertEqual([], self.ab.verarbeiten(start("steamwebhelper.exe", 11, 10, "steam.exe",
                                                       r"C:\Steam\bin\steamwebhelper.exe", 101, 100)))
        self.assertEqual([], self.ab.verarbeiten(start("helper2.exe", 12, 11, "steamwebhelper.exe", "", 102, 101)))
        self.assertEqual(2, self.ab.stand()["steam.exe"]["kinder"])           # Kind und Enkel gelernt
        self.nachlernen_beenden()
        self.assertEqual([], self.ab.verarbeiten(start("steamwebhelper.exe", 13, 10, "steam.exe",
                                                       r"C:\Steam\bin\steamwebhelper.exe", 103, 100)))   # bekannt
        alarme = self.ab.verarbeiten(start("fremd.exe", 14, 10, "steam.exe", r"C:\Temp\fremd.exe", 104, 100))
        self.assertEqual(1, len(alarme))
        a = alarme[0]
        self.assertEqual(("R015", Schwere.NIEDRIG, "steam.exe → fremd.exe"), (a.regel, a.schwere, a.subjekt))
        self.assertIn(r"C:\Temp\fremd.exe", a.text)
        self.assertIn("harmlos", a.text)
        self.ab.speichern()
        bild = AB.bild("steam.exe", self.ordner)
        self.assertEqual(2, bild["kinder"][r"c:\steam\bin\steamwebhelper.exe"]["anzahl"])
        self.assertEqual([r"C:\Steam\steam.exe"], bild["pfade"])
        self.assertNotIn(r"c:\temp\fremd.exe", bild["kinder"])               # gemeldet, nicht still gelernt

    def test_harmlos_und_erlaubt_kommen_ins_normalbild(self):
        self.auftrag(0)
        self.ab.verarbeiten(start("steam.exe", 10, t=100))
        self.urteile["steam.exe → gut.exe"] = {"harmlos": 1}
        self.urteile["steam.exe → boese.exe"] = {"harmlos": 1, "echt": 1}
        self.assertEqual([], self.ab.verarbeiten(start("gut.exe", 11, 10, "steam.exe", "", 101, 100)))
        self.assertEqual(1, len(self.ab.verarbeiten(start("boese.exe", 12, 10, "steam.exe", "", 102, 100))))
        AB.erlauben("steam", "spiel.exe", ordner=self.ordner)
        pfad = self.ordner / "steam.exe.auftrag.json"
        st = pfad.stat().st_mtime + 10
        os.utime(pfad, (st, st))
        self.ab.auftraege_pruefen(erzwingen=True)
        self.assertEqual([], self.ab.verarbeiten(start("Spiel.exe", 13, 10, "steam.exe", "", 103, 100)))
        quellen = {v["name"]: v["quelle"] for v in self.ab._bilder["steam.exe"]["kinder"].values()}
        self.assertEqual({"gut.exe": "harmlos geurteilt", "Spiel.exe": "erlaubt"}, quellen)

    def test_fremde_und_wiederverwendete_pids(self):
        self.auftrag(0)
        self.assertEqual([], self.ab.verarbeiten(start("irgendwas.exe", 20, 99, "explorer.exe")))   # nicht von Steam
        self.ab.verarbeiten(start("steam.exe", 10, t=100))
        self.assertEqual([], self.ab.verarbeiten(start("x.exe", 21, 10, "anderes.exe", "", 300, 250)))  # PID neu vergeben
        self.ab.verarbeiten(Ereignis(PROZESS, "process_stop", "weg", prozess="steam.exe", pid=10))
        self.assertEqual([], self.ab.verarbeiten(start("y.exe", 22, 10, "", "", 301, 0)))

    def test_kind_vor_dem_programm_im_selben_schnappschuss(self):
        self.auftrag(0)
        alarme = self.ab.verarbeiten(start("neu.exe", 31, 30, "steam.exe", "", 101, 100))
        self.assertEqual(["R015"], [a.regel for a in alarme])
        self.assertEqual([], self.ab.verarbeiten(start("steam.exe", 30, t=100)))

    def test_beendeter_auftrag_beobachtet_nicht_mehr(self):
        self.auftrag(0)
        self.ab.verarbeiten(start("steam.exe", 10, t=100))
        AB.stoppen("steam", ordner=self.ordner)
        pfad = self.ordner / "steam.exe.auftrag.json"
        st = pfad.stat().st_mtime + 10
        os.utime(pfad, (st, st))
        self.ab.auftraege_pruefen(erzwingen=True)
        self.assertEqual({}, self.ab.stand())
        self.assertEqual([], self.ab.verarbeiten(start("neu.exe", 11, 10, "steam.exe", "", 101, 100)))


class Werkzeug(unittest.TestCase):

    def setUp(self):
        self.ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.ordner, True)
        for p in (mock.patch.object(AB, "ORDNER", self.ordner), mock.patch.object(W.zugang, "laeuft", return_value=None),
                  mock.patch.object(AB, "laufende", return_value=[{"pid": 5, "name": "neu.exe", "exe": ""}])):
            p.start()
            self.addCleanup(p.stop)

    def test_start_status_liste_stop(self):
        text = W.programm({"aktion": "start", "programm": "Steam", "tage": "2"}, wer="Mira")
        self.assertIn("Ich passe ab jetzt auf Steam (steam.exe) auf", text)
        self.assertIn("Die Wache läuft gerade nicht", text)
        self.assertEqual("Mira", AB.auftrag("steam.exe")["von"])
        status = W.programm({"programm": "steam"})
        self.assertIn("lernt noch bis", status)
        self.assertIn("★ neu neu.exe", status)
        self.assertIn("steam.exe", W.programm({}))                           # ohne Felder: Liste
        self.assertIn("Normalbild bleibt", W.programm({"aktion": "stop", "programm": "steam"}))
        self.assertIn("beendet", W.programm({"aktion": "liste"}))
        self.assertIn("Fehler", W.programm({"aktion": "start", "programm": "../x.dll"}))
        self.assertIn("tage muss eine Zahl", W.programm({"aktion": "start", "programm": "steam", "tage": "viel"}))

    def test_aktion_und_rechte(self):
        import actions
        import modes
        self.assertIn("wache_programm", actions.ACTIONS)
        self.assertFalse(actions.ACTIONS["wache_programm"]["confirm"])
        self.assertIn("wache_programm", modes.WACHE_URTEIL_TOOLS)
        self.assertIn("Programm start steam", actions.describe({"tool": "wache_programm", "aktion": "start",
                                                                "programm": "steam"}))
        from wache.regeln import BESCHREIBUNG
        self.assertIn("R015", BESCHREIBUNG)


if __name__ == "__main__":
    unittest.main()
