"""Einlese-Speicher: Balken (Farbe, Füllung, Aufschrift), GPU-Platz, Werkzeug und Befehl."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions      # noqa: E402
import commands     # noqa: E402
import einlesen     # noqa: E402
import indexdb      # noqa: E402
import memory       # noqa: E402
import modes        # noqa: E402
import pricing      # noqa: E402


class Balken(unittest.TestCase):
    def test_farben(self):
        for token, farbe in ((0, einlesen.GRUEN), (49_999, einlesen.GRUEN), (50_000, einlesen.ORANGE),
                             (79_999, einlesen.ORANGE), (80_000, einlesen.DUNKELROT), (250_000, einlesen.DUNKELROT)):
            self.assertEqual(einlesen.farbe(token), farbe, token)

    def test_fuellung_in_halben_zellen(self):
        z = einlesen.ZELLEN
        self.assertEqual(einlesen.halbe(0), 0)
        self.assertEqual(einlesen.halbe(10), 1)                    # schon wenig ist sichtbar
        self.assertEqual(einlesen.halbe(50_000), z)
        self.assertEqual(einlesen.halbe(100_000), 2 * z)
        self.assertEqual(einlesen.halbe(400_000), 2 * z)
        self.assertLess(einlesen.VOLL / (2 * z), 4_000)            # spätestens alle 4k ein sichtbarer Schritt
        self.assertNotEqual(einlesen.halbe(4_000), einlesen.halbe(8_000))

    def test_aufschrift_zeigt_den_stand(self):
        for token, text in ((300, "300 │ 100k"), (4_000, "4k │ 100k"), (74_239, "74k │ 100k"),
                            (121_291, "121k │ 100k")):
            z = einlesen.zeichen(token)
            self.assertEqual(len(z), einlesen.ZELLEN, token)
            self.assertEqual(z.strip(), text)
        self.assertEqual(einlesen.zeichen().strip(), "100k")

    def test_halbe_zelle_und_aufleuchten_sind_eigene_farben(self):
        for t in (20_000, 60_000, 90_000):
            self.assertNotIn(einlesen.halb(t), (einlesen.farbe(t), einlesen.LEER))
            self.assertNotEqual(einlesen.hell(einlesen.farbe(t)), einlesen.farbe(t))

    def test_bild_wie_im_kontext(self):
        self.assertEqual(einlesen.BILD_TOKEN, pricing.IMG_TOKENS)

    def test_kurz(self):
        self.assertEqual(einlesen.kurz(42_300), "42k von 100k")


class Statuszeile(unittest.TestCase):
    def _zeile(self, token, breite=150):
        import screen_tx
        s = mock.MagicMock()
        s._terminal_width.return_value = breite
        s._modal, s.busy, s._practicing, s._practice_stopping = None, False, False, False
        s._kopie_zeichen, s.helfer_aktiv, s.bibliothekar_text, s.vollscan_text = 0, 0, "", ""
        s.speicher_token = token
        return screen_tx.Screen._composer_header(s)

    def _farben(self, zeile, token):
        i = zeile.plain.find(einlesen.zeichen(token))
        return [sp.style.bgcolor.name for sp in zeile.spans
                if i <= sp.start and sp.end <= i + einlesen.ZELLEN and sp.style.bgcolor]

    def test_farbe_und_fuellung(self):
        for token, farbe in ((20_000, einlesen.GRUEN), (60_000, einlesen.ORANGE), (85_000, einlesen.DUNKELROT)):
            zeile = self._zeile(token)
            self.assertIn(f"{round(token / 1000)}k │ 100k", zeile.plain)
            farben = self._farben(zeile, token)
            self.assertEqual(len(farben), einlesen.ZELLEN)
            halbe = einlesen.halbe(token)
            self.assertEqual(farben.count(farbe), halbe // 2, token)
            self.assertEqual(farben.count(einlesen.halb(token)), halbe % 2, token)

    def test_schritt_von_4k_ist_zu_sehen(self):
        self.assertNotEqual(self._farben(self._zeile(4_000), 4_000), self._farben(self._zeile(8_000), 8_000))

    def test_aenderung_leuchtet_kurz_auf(self):
        import screen_tx
        s = mock.MagicMock()
        s.speicher_token = 20_000
        with mock.patch("screen_tx.time.monotonic", return_value=100.0):
            screen_tx.Screen.set_speicher(s, 24_000)
        self.assertEqual(100.0 + screen_tx.SPEICHER_BLITZ_S, s._speicher_blitz_bis)
        s.app.set_timer.assert_called_once()
        s.app.set_timer.reset_mock()
        screen_tx.Screen.set_speicher(s, 24_000)                     # gleich geblieben: kein Aufleuchten
        s.app.set_timer.assert_not_called()

    def test_ohne_modell_und_zu_schmal_kein_balken(self):
        self.assertNotIn("100k", self._zeile(None).plain)
        self.assertNotIn("100k", self._zeile(50_000, breite=50).plain)


class Hinweis(unittest.TestCase):
    def test_nennt_den_grund_und_passt_in_die_notiz(self):
        text = einlesen.hinweis(99_000, "nur 0.8 GB Grafikspeicher frei, nötig sind 1.5 GB")
        self.assertIn("0.8 GB", text)
        self.assertLessEqual(len(text), 300)                       # lernschleife.melden kürzt danach


class GpuPlatz(unittest.TestCase):
    def test_budget_entscheidet(self):
        with mock.patch.object(memory, "gpu_pause", return_value=False):
            with mock.patch.object(memory, "gpu_budget_mb", return_value=4096):
                self.assertEqual(memory.gpu_platz(), (True, ""))
            with mock.patch.object(memory, "gpu_budget_mb", return_value=800):
                kann, grund = memory.gpu_platz()
                self.assertFalse(kann)
                self.assertIn("0.8 GB", grund)
            with mock.patch.object(memory, "gpu_budget_mb", return_value=None):
                self.assertTrue(memory.gpu_platz()[0])             # nicht messbar: versuchen

    def test_beim_malen_nicht(self):
        with mock.patch.object(memory, "gpu_pause", return_value=True):
            self.assertFalse(memory.gpu_platz()[0])


class NurGpu(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(memory, "NUR_GPU", True)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(memory, "_encoder", None)
        q.start()
        self.addCleanup(q.stop)

    def test_waehlt_die_gpu(self):
        self.assertEqual(memory._geraet_waehlen(), "cuda")

    def test_weicht_nie_auf_die_cpu_aus(self):
        versuche = []

        def laden(ordner, device="cpu", **k):
            versuche.append(device)
            raise RuntimeError("CUDA out of memory")
        import embedder
        with mock.patch.object(embedder, "laden", laden), \
                mock.patch.object(memory, "modell_ordner", return_value=Path("x")), \
                mock.patch.object(memory, "_vram_deckeln"):
            self.assertIsNone(memory._get_encoder())
        self.assertEqual(versuche, ["cuda"])


class Werkzeug(unittest.TestCase):
    def setUp(self):
        einlesen.abholen()
        self.addCleanup(einlesen.abholen)

    def test_merkt_den_durchgang_vor(self):
        with mock.patch.object(einlesen, "stand", return_value=99_000):
            self.assertTrue(actions._gedaechtnis_einlesen({}).ok)
        self.assertTrue(einlesen.abholen())
        self.assertFalse(einlesen.abholen())

    def test_ohne_modell_nicht(self):
        with mock.patch.object(einlesen, "stand", return_value=None):
            self.assertFalse(actions._gedaechtnis_einlesen({}).ok)
        self.assertFalse(einlesen.abholen())

    def test_registriert_und_nach_netz_inhalt_mit_rueckfrage(self):
        self.assertIn("gedaechtnis_einlesen", actions.ACTIONS)
        self.assertIn("gedaechtnis_einlesen", actions._MEMORY_TOOLS)
        self.assertIn("gedaechtnis_einlesen", modes.MEMORY_TOOLS)


class Befehl(unittest.TestCase):
    def test_einlesen_ersetzt_den_takt(self):
        namen = set(commands.COMMANDS)
        self.assertIn("/einlesen", namen)
        self.assertNotIn("/embeddingtimer", namen)
        self.assertNotIn("/embeddingtimer", commands.menue_befehle())


if __name__ == "__main__":
    unittest.main()
