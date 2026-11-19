"""Wache-Regel CVE-001 (tools/wache/schwachstellen.py): installierte Software mit bekannter Schwachstelle.
Vorsichtig (kein Alarm bei unklarer Version, niedriger CVSS, fremdem Hersteller), ein Alarm je Programm, nie
gedämpft, von der KI nicht stumm schaltbar, vom Vorprüfer nicht schließbar, belastet das Lernen, Hinweis im
Weckruf. Nur synthetische Testdaten.

python -m unittest tests.test_wache_cve
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("tests", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import gefahren as G                                                        # noqa: E402
from test_gefahren import ablegen, cve_json                                 # noqa: E402
from wache import ereignisse as E, inventar as I, justierung as J           # noqa: E402
from wache import motor as M, regeln as R, schwachstellen as SW             # noqa: E402
from wache import speicher as S, vorpruefer as V, wecker as W               # noqa: E402

ORT = r"C:\Program Files\Example Viewer"
EXE = ORT + r"\viewer.exe"
SIGNIERT = "signiert: ExampleVendor Inc. · Programme-Ordner"


def _antwort(urteil: str) -> str:
    return f"Fakten: ok\nPrüfung: ok\nUrteil: {urteil}\n</think>\nURTEIL: {urteil}\nBRIEF: Belegt."


class _Rechner:
    def urteilen(self, texte):
        self.texte = list(texte)
        return [_antwort("harmlos") for _ in texte]

    def freigeben(self):
        pass


class _Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.liste = tmp / "cvelistV5"
        (self.liste / "cves").mkdir(parents=True)
        self.db = tmp / "gefahren.db"
        p = mock.patch.object(G, "DB", self.db)
        p.start()
        self.addCleanup(p.stop)
        st = mock.patch.object(SW, "_stumm", return_value=(False, set()))
        self.stumm = st.start()
        self.addCleanup(st.stop)
        self.sp = S.Speicher(tmp / "w.db")
        ablegen(self.liste, cve_json("CVE-2026-0001"))                                       # 9.8, < 2.5.0
        ablegen(self.liste, cve_json("CVE-2026-0002", versionen=[{"version": "2.0", "lessThan": "3.0",
                                                                  "status": "affected"}],
                                     metriken=[{"cvssV3_1": {"baseScore": 7.5, "baseSeverity": "HIGH"}}]))
        ablegen(self.liste, cve_json("CVE-2026-0003", metriken=[{"cvssV3_1": {"baseScore": 5.3}}]))   # zu niedrig
        ablegen(self.liste, cve_json("CVE-2026-0004", produkt="Other Tool"))
        G.importieren(str(self.liste))
        self.sp.inventar_abgleichen([
            I.Eintrag(I.SOFTWARE, "Example Viewer 2.1 (x64)", ORT, version="2.1.0", hersteller="ExampleVendor Inc."),
            I.Eintrag(I.SOFTWARE, "Example Viewer Portable", r"C:\Tools\Portable", version="Build A",
                      hersteller="ExampleVendor"),
            I.Eintrag(I.SOFTWARE, "Example Viewer", r"D:\Apps\Viewer", version="2.1.0", hersteller="Someone Else"),
            I.Eintrag(I.PROZESS, "viewer.exe", EXE)])

    def tearDown(self):
        self.sp.schliessen()
        self._tmp.cleanup()

    def pruefen(self):
        return SW.pruefen(self.sp, db=self.db)

    def cve_alarme(self):
        return [a for a in self.sp.alarme(100) if a.regel == SW.REGEL]


class Abgleich(_Basis):
    def test_ein_alarm_je_programm_vorsichtig(self):
        erg = self.pruefen()
        self.assertEqual((1, 1), (erg["treffer"], erg["alarme"]))
        (a,) = self.cve_alarme()
        self.assertEqual(E.Schwere.KRITISCH, a.schwere)
        self.assertIn("CVE-2026-0001", a.text)
        self.assertIn("CVE-2026-0002", a.text)
        self.assertNotIn("CVE-2026-0003", a.text)           # CVSS unter 7.0
        self.assertNotIn("CVE-2026-0004", a.text)           # anderes Produkt
        self.assertIn("Example Viewer 2.1 (x64)", a.subjekt)
        (g,) = self.sp.gefahr_liste()
        self.assertEqual((2, 9.8, ORT.lower() + "\\"), (g["anzahl"], g["max_cvss"], g["ort"]))
        self.assertEqual(a.id, g["alarm_id"])

    def test_gleicher_stand_meldet_nicht_noch_einmal(self):
        self.pruefen()
        self.assertEqual(0, self.pruefen()["alarme"])
        self.sp.alarm_urteil(self.cve_alarme()[0].id, "erledigt", "Nutzer", "aktualisiert")   # Nutzer urteilt normal
        self.assertEqual("erledigt", self.cve_alarme()[0].status)
        ablegen(self.liste, cve_json("CVE-2026-0005", metriken=[{"cvssV3_1": {"baseScore": 8.1}}]))
        G.importieren(str(self.liste))
        self.assertEqual(1, self.pruefen()["alarme"])        # neue Schwachstelle: neuer Alarm
        self.assertEqual(2, len(self.cve_alarme()))

    def test_nach_update_keine_gefahr_mehr(self):
        self.pruefen()
        self.sp.inventar_abgleichen([I.Eintrag(I.SOFTWARE, "Example Viewer 2.1 (x64)", ORT, version="3.0.0",
                                               hersteller="ExampleVendor Inc.")])
        self.assertEqual(0, self.pruefen()["treffer"])
        self.assertEqual([], self.sp.gefahr_liste())

    def test_ohne_liste_nichts(self):
        with mock.patch.object(G, "DB", self.db.with_name("fehlt.db")):
            self.assertTrue(SW.pruefen(self.sp)["fehlt"])
        self.assertEqual([], self.cve_alarme())

    def test_nutzer_darf_stumm_schalten(self):
        self.stumm.return_value = (True, set())
        self.assertEqual(0, self.pruefen()["alarme"])
        self.assertEqual(1, len(self.sp.gefahr_liste()))    # der Hinweis bleibt

    def test_ort_zu_allgemein(self):
        self.assertEqual("", SW.ort_norm(r"C:\Program Files"))
        self.assertEqual("", SW.ort_norm(r"C:\Windows\System32\Example"))
        self.assertEqual("", SW.ort_norm("relativ\\ordner"))
        self.assertTrue(SW.ort_norm(ORT).endswith("example viewer\\"))


class Regeln(unittest.TestCase):
    def test_nie_gedaempft_nie_stumm_ki_nie_vorpruefer(self):
        self.assertIn(SW.REGEL, R.BESCHREIBUNG)
        self.assertIn(SW.REGEL, R.ATTACK)
        self.assertIn(SW.REGEL, R.NIE_GEDAEMPFT)
        self.assertNotIn(SW.REGEL, R.MENGENREGELN)
        self.assertIn(SW.REGEL, J.NIE_STUMM_KI)
        self.assertNotIn(SW.REGEL, V.ERLAUBT)
        self.assertNotIn(SW.REGEL, R.NIE_STUMM)              # der Nutzer darf

    def test_justierung(self):
        e = dict(J.STANDARD)
        e["grenzen"] = dict(J.STANDARD["grenzen"])
        e["stumme_regeln"] = []
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("regel_stumm", "CVE-001=aus", e)
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("regel_stumm", "CVE-001@Software · Example Viewer · 2.1.0=aus", e)
        self.assertEqual(["CVE-001"], J.pruefen("regel_stumm", "CVE-001=aus", e, nutzer=True)[2])


class Daempfen(_Basis):
    def test_harmlos_urteile_daempfen_nicht(self):
        for _ in range(5):
            a = E.Alarm("t", "x", E.Schwere.HOCH, SW.REGEL, subjekt="Software · X · 1")
            self.sp.alarme_schreiben([a])
            self.sp.alarm_urteil(a.id, "harmlos", "KI", "Test")
        self.assertNotIn((SW.REGEL, "software · x · 1"), self.sp.gedaempft(3))


class Vorpruefer(_Basis):
    def alarm(self):
        ev = E.Ereignis("prozess", "process_start", "Prozess gestartet", prozess="viewer.exe", exe=EXE)
        self.sp.ereignisse_schreiben([ev])
        a = E.Alarm("Anomalie: viewer.exe", "Prozess gestartet", E.Schwere.MITTEL, "ML-001", ereignis_id=ev.id,
                    subjekt="viewer.exe")
        self.sp.alarme_schreiben([a])
        self.sp.messwerte_setzen(a.id, {"herkunft": SIGNIERT})
        return a

    def test_schloss_mit_gefahrenhinweis(self):
        self.pruefen()
        a = self.alarm()
        with mock.patch.object(V, "_fernwartung", return_value=False):
            rest = V.durchgang([a], self.sp, lambda: _Rechner())
        self.assertEqual([a.id], [x.id for x in rest])
        self.assertEqual("offen", self.sp.alarm(a.id).status)
        self.assertIn("bekannter Schwachstelle", V.brief(a.id).sperre)
        self.assertIn("gefahr: ⚠ bekannt verwundbar: CVE-2026-0001", V.eingabe(V.messwerte(a, self.sp)))

    def test_ohne_hinweis_wie_bisher(self):
        a = self.alarm()
        self.assertEqual([], V.messwerte(a, self.sp)["gefahr"])
        self.assertNotIn("bekannter Schwachstelle", V.schloss(V.eingabe(V.messwerte(a, self.sp)), EXE))


class Lernen(_Basis):
    def test_belastet_solange_offen(self):
        self.pruefen()
        t = time.time() - 100
        im_ort = E.Ereignis("prozess", "process_start", "x", prozess="viewer.exe", exe=EXE, pid=10, zeit=t,
                            start=t - 5)
        kind = E.Ereignis("prozess", "process_start", "y", prozess="helper.exe", exe=r"C:\Other\helper.exe",
                          pid=11, ppid=10, zeit=t + 1, start=t + 1, eltern_start=t - 5)
        fremd = E.Ereignis("prozess", "process_start", "z", prozess="other.exe", exe=r"C:\Other\other.exe",
                           pid=12, zeit=t + 2, start=t + 2)
        self.sp.ereignisse_schreiben([im_ort, kind, fremd])
        frei = {e.id for e in self.sp.lern_ereignisse(t - 10, time.time() + 10)}
        self.assertNotIn(im_ort.id, frei)
        self.assertNotIn(kind.id, frei)                      # Kette des Programms
        self.assertIn(fremd.id, frei)
        self.assertIn(EXE.lower(), self.sp.offene_pfade())   # Inventar-Prozess im Ort
        self.assertEqual((ORT.lower() + "\\",), self.sp.gefahr_orte())
        self.sp.alarm_urteil(self.cve_alarme()[0].id, "erledigt", "Nutzer", "aktualisiert")
        self.assertEqual((), self.sp.gefahr_orte())
        self.assertIn(im_ort.id, {e.id for e in self.sp.lern_ereignisse(t - 10, time.time() + 10)})

    def test_motor_kette(self):
        stub = SimpleNamespace(_belastet={}, _offene_pfade=set(), _gefahr_orte=(ORT.lower() + "\\",))
        e = E.Ereignis("prozess", "process_start", "x", prozess="viewer.exe", exe=EXE)
        self.assertTrue(M.Motor._kette_belastet(stub, e))
        e2 = E.Ereignis("prozess", "process_start", "x", prozess="other.exe", exe=r"C:\Other\other.exe")
        self.assertFalse(M.Motor._kette_belastet(stub, e2))


class Weckruf(_Basis):
    def test_hinweis_im_rahmen(self):
        self.pruefen()
        ev = E.Ereignis("prozess", "process_start", "Prozess gestartet", prozess="viewer.exe", exe=EXE)
        self.sp.ereignisse_schreiben([ev])
        a = E.Alarm("Neuer Prozess", "x", E.Schwere.MITTEL, "R003", ereignis_id=ev.id, subjekt="viewer.exe")
        self.sp.alarme_schreiben([a])
        text = W.auftrag_text([a], self.sp, "Test")
        block = text.split(f"### Alarm {a.id}", 1)[1]
        anfang, ende = block.index("Messwerte ["), block.index("Ende Messwerte")
        zeile = next(z for z in block.splitlines() if "bekannt verwundbar" in z)
        self.assertTrue(zeile.startswith("⚠ bekannt verwundbar: CVE-2026-0001"))
        self.assertTrue(anfang < block.index(zeile) < ende)
        self.assertLessEqual(len(zeile), 300)
        self.assertNotIn("wurden schon als harmlos beurteilt", block)


if __name__ == "__main__":
    unittest.main()
