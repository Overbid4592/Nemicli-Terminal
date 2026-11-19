"""KI-Modell der Wache (ML-003): Merkmale je KI, Lernen, Bewerten, Schattenmodus (nur synthetische Daten)."""
import random
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from wache import kimodell as KM                          # noqa: E402
from wache.detektor import Detektor                       # noqa: E402
from wache.ereignisse import KI, Ereignis                 # noqa: E402
from wache.regeln import MENGENREGELN, subjekt_fuer       # noqa: E402

PROJEKT = "C:\\Projekte\\demo"
TAG = datetime(2026, 11, 10, 14, 0).timestamp()


def tat(werkzeug="Read", art="lesen", datei="", cmd="", ziel="", ki="Claude Code", zeit=TAG, **extra):
    e = Ereignis(KI, "ki_tat", f"{ki} · {werkzeug}", prozess=ki, cmdline=cmd, datei=datei, ziel=ziel,
                 extra={"ki": ki, "quelle": "claude_code", "werkzeug": werkzeug, "art": art, "ordner": PROJEKT,
                        **extra})
    e.zeit = zeit
    return e


def normal(n=2000, seed=1):
    """Ein gewohnter Arbeitstag: lesen, ändern, git und python im Projekt, tagsüber."""
    r = random.Random(seed)
    out = []
    for i in range(n):
        zeit = TAG + r.uniform(-4, 4) * 3600 + (i // 60) * 86400
        wahl = r.random()
        if wahl < 0.4:
            out.append(tat("Read", "lesen", datei=f"{PROJEKT}\\src\\modul{r.randint(1, 5)}.py", zeit=zeit))
        elif wahl < 0.7:
            out.append(tat("Edit", "schreiben", datei=f"{PROJEKT}\\src\\modul{r.randint(1, 5)}.py", zeit=zeit))
        elif wahl < 0.9:
            out.append(tat("Bash", "befehl", cmd=r.choice(["git status", "git diff", "git log -5"]), zeit=zeit))
        else:
            out.append(tat("Bash", "befehl", cmd="python -m unittest tests.test_demo", zeit=zeit))
    return out


class Merkmale(unittest.TestCase):
    def setUp(self):
        self.m = KM.KiMerkmale()
        self.i = {n: k for k, n in enumerate(KM.KI_MERKMALE)}

    def test_neu_beim_lernen_der_reihe_nach(self):
        erste, zweite = tat("Grep", "suchen", zeit=TAG), tat("Grep", "suchen", zeit=TAG + 5)
        self.m.anpassen([zweite, erste])                          # unsortiert übergeben
        v1, v2 = self.m.vektor(erste), self.m.vektor(zweite)
        self.assertEqual(v1[self.i["werkzeug_selten"]], 0.0)       # erste Tat einer KI: die KI selbst ist neu
        self.assertEqual(v1[self.i["ki_neu"]], 1.0)
        self.assertEqual(v2[self.i["ki_neu"]], 0.0)
        self.assertEqual(v2[self.i["werkzeug_selten"]], 0.5)       # einmal gesehen
        dritte = tat("WebFetch", "web", ziel="https://www.neu.example/x", zeit=TAG + 9)
        v3 = self.m.vektor(dritte)                                 # nach dem Lernen: gegen das ganze Normalbild
        self.assertEqual(v3[self.i["werkzeug_selten"]], 1.0)
        self.assertEqual(v3[self.i["domain_selten"]], 1.0)
        self.assertEqual(v3[self.i["kombi_selten"]], 1.0)
        self.assertEqual(v3[self.i["art_web"]], 1.0)

    def test_lernvektor_wird_nur_einmal_genutzt(self):
        e = tat()
        self.m.anpassen([e])
        self.m.vektor(e)
        self.assertEqual(self.m._lern, {})

    def test_andere_ki_ist_neu(self):
        self.m.anpassen(normal(50))
        v = self.m.vektor(tat(ki="Codex"))
        self.assertEqual(v[self.i["ki_neu"]], 1.0)
        self.assertEqual(v[self.i["werkzeug_selten"]], 0.0)       # ohne Bild der KI ist nichts „selten“

    def test_befehlswort(self):
        self.assertEqual(KM.befehl(tat(cmd="git status")), "git")
        self.assertEqual(KM.befehl(tat(cmd='"C:\\Tools\\Werkzeug.EXE" -v')), "werkzeug")
        self.assertEqual(KM.befehl(tat(cmd="$x = 1")), "")
        self.assertEqual(KM.befehl(Ereignis(KI, "ki_prozess", "x", prozess="git.exe", cmdline="git status")), "")
        self.assertEqual(KM.befehl(tat(cmd="function f { 1 }; f")), "")
        self.assertEqual(KM.befehl(tat(cmd="foreach($d in 1..3) { $d }")), "")
        self.assertEqual(KM.befehl(tat(cmd='"C:\\Temp\\ausgabe.txt"; Get-Content x')), "")
        self.assertEqual(KM.befehl(tat(cmd='& "C:\\Program Files\\Werkzeug\\werkzeug.exe" --hilfe')), "werkzeug")

    def test_einmal_gesehen_ist_nicht_mehr_nie(self):
        self.m.anpassen(normal(50))
        e = tat("Read", "lesen", datei="D:\\Neu\\Ordner\\a.txt")
        self.assertEqual(self.m.vektor(e)[self.i["bereich_selten"]], 1.0)
        self.m.gesehen(e)
        self.assertEqual(self.m.vektor(e)[self.i["bereich_selten"]], 0.5)
        self.assertNotIn("live", self.m.zustand())              # gesehen bleibt bis zum nächsten Training

    def test_bereich_und_ort(self):
        e = tat(datei=f"{PROJEKT}/src/a.py")
        self.assertEqual(KM.bereich(e), "projekt:src")
        v = self.m.vektor(e)
        self.assertEqual(v[self.i["ort_projekt"]], 1.0)
        sysdatei = tat(datei=(KM._SYSTEMORDNER[0] + "system32\\treiber.sys"))
        v = self.m.vektor(sysdatei)
        self.assertEqual((v[self.i["ort_system"]], v[self.i["ort_projekt"]]), (1.0, 0.0))
        self.assertEqual(KM.domain(tat(ziel="https://www.beispiel.example/pfad")), "beispiel.example")

    def test_programmstart_zaehlt_als_werkzeug(self):
        e = Ereignis(KI, "ki_prozess", "x", prozess="Git.exe", exe="C:\\Git\\git.exe", extra={"ki": "Claude Code"})
        self.assertEqual(KM.werkzeug(e), "programm:git.exe")
        self.assertEqual(KM.art(e), "prozess")

    def test_zustand_hin_und_zurueck(self):
        self.m.anpassen(normal(80))
        z = self.m.zustand()
        neu = KM.KiMerkmale()
        neu.zustand_laden(z)
        e = tat("Write", "schreiben", datei="D:\\Fremd\\x.txt")
        self.assertEqual(self.m.vektor(e), neu.vektor(e))

    def test_subjekt_und_mengenregel(self):
        e = tat(datei=f"{PROJEKT}\\src\\a.py")
        self.assertEqual(KM.subjekt(e), "Claude Code · Read · projekt:src")
        self.assertEqual(subjekt_fuer("ML-003", e), KM.subjekt(e))
        self.assertIn("ML-003", MENGENREGELN)


class Modell(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Detektor(Path(tmp.name) / "ki.json", KM.KiMerkmale(), schwelle=0.995, label="KI")
        self.assertTrue(self.d.trainieren(normal())["ok"])

    def test_gewohntes_unauffaellig_fremdes_auffaellig(self):
        gewohnt, _ = self.d.bewerten(tat("Bash", "befehl", cmd="git status", zeit=TAG + 600))
        self.assertLess(gewohnt, 0.995)
        nacht = datetime(2026, 11, 12, 3, 30).timestamp()
        fremd = Ereignis(KI, "ki_prozess", "x", prozess="fremd.exe", zeit=nacht,
                         exe="D:\\Unbekannt\\x7\\fremd.exe",
                         cmdline="fremd.exe " + "q8Zr2vX0pL7mK3nB9wT5yH1jD6fG4sA" * 4,
                         extra={"ki": "Claude Code", "quelle": "wache", "art": "prozess"})
        score, gruende = self.d.bewerten(fremd)
        self.assertGreaterEqual(score, 0.995)
        self.assertTrue(any("bisher nie" in g or "nie gesehen" in g for g in gruende), gruende)

    def test_laden_nach_speichern(self):
        e = tat("Edit", "schreiben", datei=f"{PROJEKT}\\src\\modul2.py", zeit=TAG + 60)
        vorher = self.d.bewerten(e)[0]
        neu = Detektor(self.d.pfad, KM.KiMerkmale(), schwelle=0.995, label="KI")
        self.assertTrue(neu.trainiert)
        self.assertAlmostEqual(neu.bewerten(e)[0], vorher)


class ImMotor(unittest.TestCase):
    def setUp(self):
        from wache import justierung as J, motor as MO, vollscan as VS
        self.MO = MO
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ordner = Path(tmp.name)
        e = dict(J.STANDARD)
        e["grenzen"] = dict(J.STANDARD["grenzen"])
        e["ml"] = False
        for ziel, wert in ((MO, ("MODELL_ORDNER", ordner / "modelle")), (VS, ("DB", ordner / "vollscan.db")),
                           (MO, ("ORDNER", ordner))):
            p = mock.patch.object(ziel, *wert)
            p.start()
            self.addCleanup(p.stop)
        self.m = MO.Motor(ordner / "w.db", e)
        self.addCleanup(self.m.speicher.schliessen)
        self.addCleanup(self.m.baseline.schliessen)

    def _fremd(self):
        return Ereignis(KI, "ki_prozess", "x", prozess="fremd.exe", zeit=datetime(2026, 11, 12, 3, 30).timestamp(),
                        exe="D:\\Unbekannt\\x7\\fremd.exe",
                        cmdline="fremd.exe " + "q8Zr2vX0pL7mK3nB9wT5yH1jD6fG4sA" * 4,
                        extra={"ki": "Claude Code", "quelle": "wache", "art": "prozess"})

    def test_ohne_training_kein_urteil(self):
        e = self._fremd()
        alarme = []
        self.m._bewerten(e, alarme)
        self.assertEqual(alarme, [])
        self.assertNotIn("ki_ml", e.extra)
        self.assertIn("burst", e.extra)

    def test_erst_schatten_dann_alarm(self):
        self.m.ki_detektor.trainieren(normal())
        e = self._fremd()
        alarme = []
        self.m._bewerten(e, alarme)
        self.assertEqual(alarme, [])
        self.assertTrue(e.extra.get("ki_fund"))
        log = (self.MO.MODELL_ORDNER / self.MO.KI_SCHATTEN).read_text(encoding="utf-8")
        self.assertIn("ML-003", log)
        self.assertIn("fremd.exe", log)
        self.m.e["ki_ml_alarm"] = True
        self.m._bewerten(self._fremd(), alarme)
        self.assertEqual([a.regel for a in alarme], ["ML-003"])
        self.assertTrue(alarme[0].subjekt.startswith("Claude Code · programm:fremd.exe"))

    def test_nachgetragen_wird_nicht_bewertet(self):
        self.m.ki_detektor.trainieren(normal())
        e = self._fremd()
        e.extra["nachgetragen"] = True
        alarme = []
        self.m._bewerten(e, alarme)
        self.assertEqual(alarme, [])
        self.assertNotIn("ki_ml", e.extra)
        self.assertIn("burst", e.extra)

    def test_training_aus_dem_speicher_ohne_schattenfunde(self):
        alt = time.time() - 3 * 86400
        taten = normal(250)
        for i, e in enumerate(taten):
            e.zeit = alt + i
        fund = tat("Bash", "befehl", cmd="git push", zeit=alt + 1000)
        fund.extra["ki_fund"] = True
        self.m.speicher.ereignisse_schreiben(taten + [fund])
        lern = self.m.speicher.lern_ereignisse(alt - 10, time.time(), kategorie=KI)
        self.assertEqual(len(lern), 250)
        r = self.m._ki_trainieren("Nutzer", time.time(), 24 * 3600)
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.m.ki_detektor.trainiert)
        self.m.e["min_training_ki"] = 1000
        self.assertFalse(self.m._ki_trainieren("Nutzer", time.time(), 24 * 3600)["ok"])
        self.m.e["ki_ml"] = False
        self.assertIn("aus", self.m._ki_trainieren("Nutzer", time.time(), 0)["grund"])

    def test_stand_und_anzeige(self):
        self.assertIn("ki", self.m.stand())
        from wache import werkzeuge
        with mock.patch("wache.MODELL_ORDNER", self.MO.MODELL_ORDNER):
            self.assertIn("noch nicht trainiert", werkzeuge.ki_modell_zeile())
            self.m.ki_detektor.trainieren(normal())
            self.assertIn("gelernt aus 2000 Taten von 1 KIs", werkzeuge.ki_modell_zeile())


if __name__ == "__main__":
    unittest.main()
