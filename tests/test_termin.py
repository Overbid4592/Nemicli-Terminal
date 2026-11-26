"""Auto-Vollscan nach Zeitplan: Plan, Fälligkeit, Systemstart, Grenze, Schutz vor der KI, Fenster."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qt_testhilfe  # noqa: E402
from wache import termin as T  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

MO_10 = datetime(2026, 11, 16, 10, 0).timestamp()     # Montag 10:00


def ts(tag, h, m=0):
    return datetime(2026, 11, tag, h, m).timestamp()


class _Ordner(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        p = mock.patch.object(T, "ordner", lambda: self.ordner)
        p.start()
        self.addCleanup(p.stop)

    def plan(self, tage, uhrzeit="03:00", systemstart=False, geaendert=0.0):
        (self.ordner / T.PLAN_NAME).write_text(json.dumps(
            {"tage": tage, "uhrzeit": uhrzeit, "systemstart": systemstart, "geaendert": geaendert}), encoding="utf-8")
        return T.plan_lesen()


class Termine(_Ordner):
    def test_letzter_und_naechster(self):
        plan = T.plan_normal({"tage": [0, 2], "uhrzeit": "03:00"})          # Mo, Mi
        self.assertEqual(T.letzter_termin(plan, MO_10), ts(16, 3))
        self.assertEqual(T.naechster_termin(plan, MO_10), ts(18, 3))
        self.assertEqual(T.letzter_termin(plan, ts(16, 2, 59)), ts(11, 3))   # vorige Woche Mittwoch
        self.assertIsNone(T.naechster_termin(T.plan_normal({}), MO_10))

    def test_kaputter_plan_wird_sauber(self):
        plan = T.plan_normal({"tage": [9, -1, "3", 3, "x"], "uhrzeit": "25:99"})
        self.assertEqual(plan["tage"], [3])
        self.assertEqual(plan["uhrzeit"], T.STANDARD_UHRZEIT)

    def test_faellig_und_nachholen(self):
        plan = self.plan([0], "03:00", geaendert=ts(10, 12))
        self.assertEqual(T.faellig(plan, {}, MO_10, boot=0.0), ("termin", ts(16, 3)))       # PC war um 3 aus
        self.assertIsNone(T.faellig(plan, {"termin": ts(16, 3)}, MO_10, boot=0.0))          # lief schon

    def test_termin_vor_dem_speichern_zaehlt_nicht(self):
        plan = self.plan([0], "03:00", geaendert=ts(16, 9))
        self.assertIsNone(T.faellig(plan, {}, MO_10, boot=0.0))

    def test_systemstart_einmal_je_hochfahren(self):
        plan = self.plan([], systemstart=True, geaendert=ts(15, 12))
        boot = MO_10 - 120
        self.assertEqual(T.faellig(plan, {}, MO_10, boot), ("systemstart", boot))
        self.assertIsNone(T.faellig(plan, {"boot": boot}, MO_10, boot))
        self.assertIsNone(T.faellig(plan, {}, MO_10 + T.SYSTEMSTART_S + 1, boot))           # Wache kam zu spät
        spaet = self.plan([], systemstart=True, geaendert=MO_10 - 60)                       # nach dem Hochfahren gestellt
        self.assertIsNone(T.faellig(spaet, {}, MO_10, boot))

    def test_pruefen_startet_und_merkt(self):
        self.plan([0], "03:00", geaendert=ts(10, 12))
        aufrufe = []
        with mock.patch.object(T, "_protokoll"):
            text = T.pruefen(MO_10, starten=lambda: aufrufe.append(1) or "Vollscan gestartet", boot=0.0)
            self.assertEqual(text, "Vollscan gestartet")
            self.assertEqual(T.pruefen(MO_10 + 30, starten=lambda: aufrufe.append(1) or "x", boot=0.0), "")
        self.assertEqual(len(aufrufe), 1)
        self.assertIn("zuletzt", T.status_text(MO_10 + 60))
        self.assertIn("nächster Mo 23.11. 03:00", T.status_text(MO_10 + 60))

    def test_ohne_plan_nichts(self):
        self.assertEqual(T.pruefen(MO_10, starten=lambda: self.fail("darf nicht starten"), boot=0.0), "")
        self.assertIn("kein Zeitplan", T.status_text(MO_10))

    def test_beschreibung(self):
        self.assertEqual(T.beschreibung(T.plan_normal({"tage": range(7), "uhrzeit": "4:5"})), "jeden Tag um 04:05")
        self.assertEqual(T.beschreibung(T.plan_normal({"tage": [0, 1, 2, 3, 4]})), "werktags um 03:00")
        self.assertEqual(T.beschreibung(T.plan_normal({"tage": [1, 3], "systemstart": True})),
                         "Di, Do um 03:00 · bei jedem Systemstart")


class Befehle(unittest.TestCase):
    def test_auch_ueber_wache_fullscan(self):
        """/wache termin und zusätzlich /wache fullscan termin öffnen das Fenster; fullscan status zeigt den Plan.
        main_start wird in main.py eingelesen, nicht importiert – geprüft wird der Quelltext."""
        import main_quelle
        quelle = main_quelle.text()
        wache = quelle[quelle.index('elif sub in ("termin", "autoscan", "zeitplan")'):]
        self.assertIn("_termin_fenster()", wache[:600])
        voll = quelle[quelle.index("async def _vollscan_befehl"):quelle.index("_VOLLSCAN: dict")]
        termin = voll[voll.index('if rest.split()[:1] == ["termin"]'):]
        self.assertIn("_termin_fenster()", termin[:400])
        self.assertIn("TE.status_text()", termin[:400])
        self.assertIn('"📅 " + TE.status_text()', voll)                # fullscan status zeigt den Plan
        self.assertLess(voll.index('["termin"]'), voll.index("ask_confirm("))   # öffnet nie die Start-Frage


class VonHand(unittest.TestCase):
    def test_nutzer_startet_immer(self):
        """/wache fullscan des Nutzers startet immer, unabhängig vom Zeitplan; es gibt keine Grenze."""
        import main_quelle
        quelle = main_quelle.text()
        teil = quelle[quelle.index("async def _vollscan_befehl"):quelle.index("_VOLLSCAN: dict")]
        self.assertNotIn("woche_frei", teil)
        self.assertNotIn("sperre_text", teil)
        self.assertIn("VS.starten, paths.INSTALL)", teil)


class SchutzVorDerKi(unittest.TestCase):
    def test_ganzer_schluesselordner_gesperrt(self):
        import eigenbereich as E
        E._SCHLUESSEL_STR.clear()
        self.addCleanup(E._SCHLUESSEL_STR.clear)
        ordner = E.schluessel_datei().parent.resolve()
        self.assertTrue(E.ist_schluessel_datei(ordner / T.PLAN_NAME))
        self.assertTrue(E.ist_schluessel_datei(ordner / "bereiche.json"))
        self.assertTrue(E.ist_schluessel_datei(ordner))
        self.assertFalse(E.ist_schluessel_datei(ordner.parent / (ordner.name + "-anders") / "x.json"))

    def test_lesewerkzeug_der_ki_abgewiesen(self):
        import actions
        import eigenbereich as E
        E._SCHLUESSEL_STR.clear()
        self.addCleanup(E._SCHLUESSEL_STR.clear)
        ziel = E.schluessel_datei().parent / T.PLAN_NAME
        grund = actions._sperrgrund(str(ziel))
        self.assertIsNotNone(grund)
        self.assertEqual(grund[1], "schluessel")


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import termin_fenster
except ImportError:                                      # PySide6 fehlt
    termin_fenster = None


@unittest.skipIf(termin_fenster is None, "PySide6 fehlt")
class Fenster(_Ordner):
    def setUp(self):
        super().setUp()
        termin_fenster.app_vorbereiten("NemiCLI.Test")

    def test_waehlen_und_speichern(self):
        f = termin_fenster.TerminFenster()
        self.assertIn("Kein Zeitplan", f.vorschau.text())
        f.tage[0].click()                                   # Mo
        f.tage[2].click()                                   # Mi
        f.uhr.setTime(termin_fenster.QTime(4, 30))
        f.systemstart.click()
        self.assertIn("Mo, Mi um 04:30 · bei jedem Systemstart", f.vorschau.text())
        with mock.patch.object(termin_fenster.QTimer, "singleShot"):
            f.speichern()
        plan = T.plan_lesen()
        self.assertEqual((plan["tage"], plan["uhrzeit"], plan["systemstart"]), ([0, 2], "04:30", True))
        self.assertIn("Gespeichert", f.status.text())
        f.tage[0].click()                                   # wieder ab
        self.assertFalse(f.tage[0].akzent)

    def test_laden_und_schnellwahl(self):
        self.plan([5, 6], "22:15")
        f = termin_fenster.TerminFenster()
        self.assertEqual([k.akzent for k in f.tage], [False] * 5 + [True] * 2)
        self.assertEqual(f.uhr.time().toString("HH:mm"), "22:15")
        f._tage_setzen(range(5))
        self.assertIn("werktags", f.vorschau.text())
        f._tage_setzen(())
        self.assertIn("Kein Zeitplan", f.vorschau.text())


if __name__ == "__main__":
    unittest.main()
