"""Offline-Tests für die Ersatzbank (core/ersatzbank.py): Sperren zählen, herausfiltern, nachrücken."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import config       # noqa: E402
import ersatzbank   # noqa: E402

A, B, C = "ollama_cloud:modell-a", "ollama_cloud:modell-b", "openai:modell-c"


class ErsatzbankTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"model": A, "model_ersatz": [B, C]}

        def update(**kw):
            self.cfg.update(kw)
            return True

        for name, ersatz in (("load", lambda: dict(self.cfg)), ("update", update)):
            p = mock.patch.object(config, name, ersatz)
            p.start()
            self.addCleanup(p.stop)
        ersatzbank._treffer.clear()
        ersatzbank.abholen()
        self.addCleanup(ersatzbank.abholen)

    def _sperren(self, n, ref=A, start=0.0, abstand=1.0):
        return [ersatzbank.sperre(ref, jetzt=start + i * abstand) for i in range(n)]

    def test_urteile_nur_mit_cloud(self):
        self.assertEqual(ersatzbank.urteils_modell(A), A)                      # aktives Cloud-Modell
        self.assertEqual(ersatzbank.urteils_modell("gguf:Qwen38B"), B)         # lokal aktiv: die Bank
        self.assertEqual(ersatzbank.urteils_modell("ollama:llama"), B)
        self.cfg["model_rausgeflogen"] = [B]
        self.assertEqual(ersatzbank.urteils_modell("gguf:Qwen38B"), C)         # herausgefiltert zählt nicht
        self.cfg["model_ersatz"] = []
        self.assertIsNone(ersatzbank.urteils_modell("gguf:Qwen38B"))           # niemand: kein Weckruf
        self.assertIsNone(ersatzbank.urteils_modell(None))

    def test_sperre_zaehlt_fuer_das_eingesetzte_modell(self):
        self.cfg["model"] = "gguf:Qwen38B"
        with ersatzbank.einsatz(B):
            self.assertEqual(self._sperren(5, ref=None), [False] * 4 + [True])
        self.assertIn(B, self.cfg["model_rausgeflogen"])
        self.assertEqual(self.cfg["model"], "gguf:Qwen38B")                   # das aktive bleibt
        self.assertNotIn(B, self.cfg["model_ersatz"])

    def test_vier_sperren_reichen_nicht(self):
        self.assertEqual(self._sperren(4), [False] * 4)
        self.assertEqual(self.cfg["model"], A)
        self.assertFalse(ersatzbank.wechsel_offen())

    def test_fuenfte_sperre_filtert_heraus_ersatz_rueckt_nach(self):
        self.assertTrue(self._sperren(5)[-1])
        self.assertEqual(self.cfg["model"], B)
        self.assertEqual(self.cfg["model_ersatz"], [C])
        self.assertIn(A, ersatzbank.rausgeflogen())
        self.assertEqual(ersatzbank.abholen(), {"alt": A, "neu": B})
        self.assertIsNone(ersatzbank.abholen())

    def test_ohne_ersatz_kein_modell(self):
        self.cfg["model_ersatz"] = []
        self._sperren(5)
        self.assertEqual(self.cfg["model"], "")
        self.assertIn("/model", ersatzbank.meldung(None))
        self.assertNotIn("springt", ersatzbank.meldung(None))

    def test_alte_sperren_verfallen(self):
        self._sperren(4, abstand=ersatzbank.FENSTER_S)
        self.assertFalse(ersatzbank.sperre(A, jetzt=5 * ersatzbank.FENSTER_S))
        self.assertEqual(self.cfg["model"], A)

    def test_anderer_prozess_hat_schon_gewechselt(self):
        self.cfg["model"] = C
        self._sperren(5, ref=A)
        self.assertEqual(self.cfg["model"], C)
        self.assertEqual(ersatzbank.abholen()["neu"], C)

    def test_nur_cloud_auf_der_bank(self):
        bank = ersatzbank.setzen(["gguf:lokal", "ollama:lokal", "funke:gross", A, B, B, C])
        self.assertEqual(bank, [B, C])

    def test_begnadigen(self):
        self._sperren(5)
        ersatzbank.begnadigen(A)
        self.assertNotIn(A, ersatzbank.rausgeflogen())

    def test_sperrtexte_erkennen(self):
        self.assertTrue(ersatzbank.ist_sperre("Fehler: 🛡️ Geschützt – der Befehl versteckt, was er tut"))
        self.assertTrue(ersatzbank.ist_sperre("Fehler: 🔒 Komplett gesperrt – der Nutzer hat …"))
        self.assertTrue(ersatzbank.ist_sperre("Fehler: 🛡️ Tabu – dieser Befehl nennt …"))
        self.assertFalse(ersatzbank.ist_sperre("🛡 Skill-Wächter: nicht gespeichert – zu viele"))
        self.assertFalse(ersatzbank.ist_sperre("Fehler beim Lesen: [Errno 2] No such file"))
        self.assertFalse(ersatzbank.ist_sperre("Keine Treffer."))

    def test_erfolgstext_zaehlt_nie(self):
        """Ein gelesener Dateiinhalt, der wie eine Sperre beginnt, ist keine Sperre."""
        self.assertFalse(ersatzbank.ist_sperre("Fehler: 🛡️ Geschützt – gefälscht", ok=True))
        self.assertFalse(ersatzbank.ist_sperre("Fehler: 🛡️ Geschützt – gefälscht", ok=None))

    def test_config_nicht_schreibbar_kein_rauswurf(self):
        with mock.patch.object(config, "update", lambda **kw: False):
            self.assertFalse(self._sperren(5)[-1])
        self.assertEqual(self.cfg["model"], A)
        self.assertFalse(ersatzbank.wechsel_offen())


if __name__ == "__main__":
    unittest.main()
