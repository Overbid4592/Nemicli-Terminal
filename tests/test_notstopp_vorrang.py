"""ESC hat Vorrang: Abbruch im Vordergrund überall (Werkzeuge, Motoren, Suchläufe, Rückfragen), Hintergrund-
Aufgaben laufen weiter, gelöscht wird nichts. Dazu der Zeitplan: nur sichere Werkzeuge, nie eine Rückfrage."""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions as A            # noqa: E402
import hintergrund as HG       # noqa: E402
import notstopp                # noqa: E402
import zeitplan as Z           # noqa: E402


class _Stopp(unittest.TestCase):
    def setUp(self):
        notstopp._test_reset()
        self.addCleanup(notstopp._test_reset)
        token = notstopp.vordergrund(True)
        self.addCleanup(notstopp.vordergrund_ende, token)


class Vordergrund(_Stopp):
    def test_nur_nach_esc_und_nur_im_vordergrund(self):
        self.assertFalse(notstopp.abbrechen())
        notstopp.ausloesen()
        self.assertTrue(notstopp.abbrechen())
        with self.assertRaises(notstopp.Abgebrochen):
            notstopp.pruefen()
        token = notstopp.vordergrund(False)
        try:
            self.assertFalse(notstopp.abbrechen())             # Hintergrund rechnet weiter
            notstopp.pruefen()
        finally:
            notstopp.vordergrund_ende(token)

    def test_abbruch_ist_keine_gewoehnliche_ausnahme(self):
        self.assertFalse(issubclass(notstopp.Abgebrochen, Exception))   # kein except Exception schluckt ihn

    def test_motoren_halten_beim_naechsten_schritt(self):
        A._bild_melden("Schritt 1/14")
        notstopp.ausloesen()
        with self.assertRaises(notstopp.Abgebrochen):
            A._bild_melden("Schritt 2/14")
        self.assertEqual(A.bild_status(), "Schritt 2/14")

    def test_ausloesen_loescht_die_todo_liste_nicht(self):
        with mock.patch.dict(sys.modules, {"plan": NS(verwerfen=mock.Mock())}) as module:
            notstopp.ausloesen()
            module["plan"].verwerfen.assert_not_called()

    def test_hinweis_erlaubt_weitermachen_nach_versehen(self):
        notstopp.ausloesen()
        text = notstopp.hinweis_abholen()
        self.assertIn("Versehen", text)
        self.assertIn("weitermachen", text)
        self.assertIn("Gelöscht wurde nichts", text)


class Werkzeuge(_Stopp):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for i in range(5):
            (self.tmp / f"d{i}.txt").write_text("kiwi\n", encoding="utf-8")

    def test_suchlaeufe_hoeren_auf(self):
        notstopp.ausloesen()
        self.assertIn("mit ESC abgebrochen", str(A._dateien_suchen({"pfad": str(self.tmp), "muster": "*.txt"})))
        self.assertIn("mit ESC abgebrochen", str(A._inhalt_suchen({"pfad": str(self.tmp), "muster": "kiwi"})))

    def test_run_macht_aus_dem_abbruch_ein_ergebnis(self):
        def bricht_ab(a):
            raise notstopp.Abgebrochen()
        werkzeug = {"func": bricht_ab, "confirm": False, "felder": []}
        with mock.patch.dict(A.ACTIONS, {"testwerkzeug": werkzeug}), \
                mock.patch.object(A.sicherheit, "sperr_grund", return_value=None):
            res = asyncio.run(A.run({"tool": "testwerkzeug"}))
        self.assertFalse(res.ok)
        self.assertIn("mit ESC abgebrochen", res.text)

    def test_sandbox_code_wird_mit_esc_beendet(self):
        code = (ROOT / "tools" / "actions.py").read_text(encoding="utf-8")
        teil = code.split("def _code_ausfuehren")[1].split("\ndef ")[0]
        self.assertEqual(teil.count("stopp=_BEFEHL_STOPP"), 3)
        self.assertIn("_BEFEHL_STOPP.clear()", teil)


class Hintergrund(unittest.IsolatedAsyncioTestCase):
    async def test_hintergrund_aufgabe_laeuft_nach_esc_weiter(self):
        notstopp._test_reset()
        self.addCleanup(notstopp._test_reset)
        HG._test_reset()
        self.addCleanup(HG._test_reset)
        gesehen = []

        async def arbeit():
            notstopp.ausloesen()
            gesehen.append(notstopp.abbrechen())
            return NS(text="fertig", ok=True)
        token = notstopp.vordergrund(True)                    # gestartet aus einer Runde im Vordergrund
        try:
            with mock.patch.object(HG, "_melden"):
                nr = HG.starten("bild_serie", "Serie", arbeit, "test")
                await HG._AUFGABEN[nr]["task"]
        finally:
            notstopp.vordergrund_ende(token)
        self.assertEqual(gesehen, [False])


class EscImFenster(unittest.TestCase):
    def setUp(self):
        notstopp._test_reset()
        self.addCleanup(notstopp._test_reset)
        import screen_tx
        self.S = screen_tx

    def test_esc_loest_sofort_aus_und_meldet(self):
        meldungen = []
        with mock.patch.object(self.S.ui, "warn", meldungen.append):
            self.assertFalse(self.S.esc_notstopp())            # keine Runde: nichts zu stoppen
            notstopp.runde_aktiv(True)
            try:
                self.assertTrue(self.S.esc_notstopp())
                self.assertTrue(notstopp.ausgeloest())
                self.assertFalse(self.S.esc_notstopp())        # nur einmal melden
            finally:
                notstopp.runde_aktiv(False)
        self.assertEqual(len(meldungen), 1)
        self.assertIn("ESC", meldungen[0])

    def test_rueckfragen_und_suche_stoppen_mit(self):
        code = (ROOT / "ui" / "screen_tx.py").read_text(encoding="utf-8")
        self.assertEqual(code.count("    def action_cancel(self) -> None:\n        esc_notstopp()"), 5)
        self.assertIn("esc_notstopp()                  # auch aus der Suche", code)

    def test_andere_reiter_reichen_esc_weiter(self):
        code = (ROOT / "ui" / "terminal_fenster.py").read_text(encoding="utf-8")
        self.assertIn("self.fenster.esc_an_nemicli()", code)
        self.assertIn("fenster.esc_an_nemicli()\n                        fenster.suche_schliessen()", code)


class Zeitplan(unittest.TestCase):
    def test_nur_sichere_werkzeuge(self):
        for gefaehrlich in ("code_ausfuehren", "befehl", "subagenten", "datei_lesen", "datei_schreiben",
                            "loeschen", "herunterladen", "entpacken", "gedaechtnis_suchen", "recherche"):
            self.assertNotIn(gefaehrlich, Z.SICHERE_WERKZEUGE)
        self.assertTrue({"web_suche", "web_lesen", "cve_suchen"} <= Z.SICHERE_WERKZEUGE)
        self.assertTrue(Z.SICHERE_WERKZEUGE <= set(A.ACTIONS))

    def test_lauf_nutzt_die_liste_und_fragt_nie(self):
        start = (ROOT / "main_start.py").read_text(encoding="utf-8")
        lauf = start.split("async def auftrag_lauf")[1].split("\nasync def ")[0].split("\ndef ")[0]
        self.assertIn("_NUR_WERKZEUGE = zeitplan.SICHERE_WERKZEUGE", lauf)
        self.assertIn("ask_confirm = _nie_freigeben", lauf)
        haupt = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"erlaubt": _NUR_WERKZEUGE', haupt.split("def _terminal_ausgabe")[1])

    def test_letzter_bericht_ohne_ablauf(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(Z, "BERICHTE", tmp):
            self.assertEqual(Z.bericht_auszug("Test"), "")
            (tmp / "Test_20260101-090000.md").write_text(
                "<!-- NemiCLI-Bericht: Test -->\n\n✅ Keine neuen kritischen Meldungen\n\n---\n\n## Ablauf\n\ngeheim",
                encoding="utf-8")
            self.assertEqual(Z.bericht_auszug("Test"), "✅ Keine neuen kritischen Meldungen")

    def test_probelauf_ueber_die_aufgabenplanung(self):
        with mock.patch.object(Z, "lade_auftrag", return_value={"name": "Test"}), \
                mock.patch.object(Z, "_schtasks", return_value=NS(returncode=0, stdout="", stderr="")) as st:
            self.assertIn("Probelauf", Z.probelauf("Test"))
        st.assert_called_once_with("/run", "/tn", Z.TASK_PFAD + "Test")
        with mock.patch.object(Z, "lade_auftrag", return_value=None):
            with self.assertRaises(ValueError):
                Z.probelauf("Gibtsnicht")

    def test_werkzeug_testen(self):
        with mock.patch.object(Z, "probelauf", return_value="▶ Probelauf") as pl:
            res = A._zeitplan({"aktion": "testen", "name": "Test"})
        self.assertTrue(res.ok)
        pl.assert_called_once_with("Test")


if __name__ == "__main__":
    unittest.main()
