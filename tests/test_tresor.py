"""Tresor: AES-256-GCM (Windows CNG) und Windows-Schloss, Siegel und Wächter (verändert, ersetzt, alter Stand →
alles vernichtet), Umzug aus .env und chatgpt_konto.json, Eintragen nur im Controllcenter, Sperre für die KI."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
for d in ("tests", "core", "tools", "engines", "ui"):
    if str(ROOT / d) not in sys.path:
        sys.path.insert(0, str(ROOT / d))

import config                 # noqa: E402
import keyvault               # noqa: E402
import kontrollzentrum as KZ  # noqa: E402
import main_quelle            # noqa: E402
import qt_testhilfe           # noqa: E402
import tresor                 # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen
WINDOWS = tresor.verfuegbar()


class _Basis(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for name, wert in (("ORDNER", self.tmp / "versteck" / ".tresor"), ("SIEGEL_DATEI", self.tmp / "siegel")):
            p = mock.patch.object(tresor, name, wert)
            p.start()
            self.addCleanup(p.stop)
        tresor._merk.update(sha=None, daten=None)
        tresor.ZUSTAND.update(vernichtet="", zeit=0.0)
        self.addCleanup(tresor._merk.update, sha=None, daten=None)
        self.addCleanup(tresor.ZUSTAND.update, vernichtet="", zeit=0.0)

    def inhalt(self) -> Path:
        return tresor.ordner() / "inhalt"


@unittest.skipUnless(WINDOWS, "braucht die Windows-Kryptografie")
class Schloesser(_Basis):
    def test_aes_gcm_offizieller_pruefwert(self):
        chiffre, tag = tresor._aes_gcm(b"\0" * 32, b"\0" * 12, b"\0" * 16)        # GCM-Spezifikation, Fall 14
        self.assertEqual(chiffre.hex(), "cea7403d4d606b6e074ec5d3baf39d18")
        self.assertEqual(tag.hex(), "d0d1c8a799996bf0265b98b5d48ab919")
        with self.assertRaises(ValueError):
            tresor._aes_gcm(b"\0" * 32, b"\0" * 12, chiffre, b"\1" * 16)

    def test_hin_und_zurueck_ohne_klartext_auf_der_platte(self):
        tresor.setzen("schluessel", "TEST_API_KEY", "geheim-123")
        tresor.bereich_setzen("chatgpt", {"email": "test_user@example.com", "refresh_token": "rt-xyz"})
        roh = self.inhalt().read_bytes()
        for offen in (b"geheim-123", b"TEST_API_KEY", b"test_user@example.com", b"rt-xyz", b"chatgpt",
                      b"schluessel"):
            self.assertNotIn(offen, roh)
        tresor._merk.update(sha=None, daten=None)                 # wirklich neu von der Platte
        self.assertEqual(tresor.bereich("schluessel"), {"TEST_API_KEY": "geheim-123"})
        self.assertEqual(tresor.bereich("chatgpt")["refresh_token"], "rt-xyz")
        self.assertEqual(self.inhalt().parent.name, ".tresor")

    def test_entfernen_und_leer(self):
        tresor.setzen("schluessel", "A_API_KEY", "1")
        tresor.setzen("schluessel", "A_API_KEY", None)
        self.assertFalse(self.inhalt().exists())                    # nichts mehr drin: Datei weg
        self.assertEqual(tresor.pruefen(), "leer")


@unittest.skipUnless(WINDOWS, "braucht die Windows-Kryptografie")
class Waechter(_Basis):
    def setUp(self):
        super().setUp()
        self.gemeldet = []
        tresor.BEI_VERNICHTUNG.append(self.gemeldet.append)
        self.addCleanup(tresor.BEI_VERNICHTUNG.remove, self.gemeldet.append)
        tresor.setzen("schluessel", "TEST_API_KEY", "geheim")

    def test_unveraendert_ok(self):
        self.assertEqual(tresor.pruefen(), "ok")
        self.assertEqual(self.gemeldet, [])

    def test_veraendert_wird_vernichtet(self):
        roh = bytearray(self.inhalt().read_bytes())
        roh[-1] ^= 1
        self.inhalt().write_bytes(bytes(roh))
        self.assertEqual(tresor.pruefen(), "vernichtet")
        self.assertFalse(self.inhalt().exists())
        self.assertEqual(tresor.lesen(), {})
        self.assertEqual(len(self.gemeldet), 1)
        self.assertIn("Siegel", tresor.ZUSTAND["vernichtet"])

    def test_alter_stand_zurueckgelegt_wird_vernichtet(self):
        alt = self.inhalt().read_bytes()
        tresor.setzen("schluessel", "TEST_API_KEY", "neu")           # neues Siegel
        self.inhalt().write_bytes(alt)
        self.assertEqual(tresor.pruefen(), "vernichtet")

    def test_ohne_siegel_wird_vernichtet(self):
        (self.tmp / "siegel").unlink()
        self.assertEqual(tresor.pruefen(), "vernichtet")
        self.assertFalse(self.inhalt().exists())

    def test_beim_lesen_geprueft(self):
        self.inhalt().write_bytes(b"NTR1" + os.urandom(80))
        self.assertEqual(tresor.bereich("schluessel"), {})
        self.assertTrue(tresor.ZUSTAND["vernichtet"])

    def test_schreddern(self):
        datei = self.tmp / "x.txt"
        datei.write_text("geheim", encoding="utf-8")
        self.assertTrue(tresor.schreddern(datei))
        self.assertFalse(datei.exists())


@unittest.skipUnless(WINDOWS, "braucht die Windows-Kryptografie")
class Konfiguration(_Basis):
    def setUp(self):
        super().setUp()
        self.env = self.tmp / ".env"
        for ziel, name, wert in ((config, "_ENV", self.env),):
            p = mock.patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {}, clear=False)
        p.start()
        self.addCleanup(p.stop)

    def test_schluessel_nur_im_tresor(self):
        self.env.write_text("FOO=bar\nTEST_API_KEY=alt\n", encoding="utf-8")
        self.assertTrue(config.set_env("TEST_API_KEY", " neu-123 "))
        self.assertEqual(os.environ["TEST_API_KEY"], "neu-123")
        self.assertEqual(self.env.read_text(encoding="utf-8").splitlines(), ["FOO=bar"])
        self.assertEqual(tresor.bereich("schluessel"), {"TEST_API_KEY": "neu-123"})
        self.assertEqual(config.tresor_namen(), ["TEST_API_KEY"])
        config.geheimnis_setzen("TEST_API_KEY", "")
        self.assertNotIn("TEST_API_KEY", os.environ)

    def test_umzug_aus_der_env(self):
        self.env.write_text(f"FOO=bar\nA_API_KEY={keyvault.encrypt('dpapi-wert')}\nB_API_KEY=klar\n", encoding="utf-8")
        self.assertEqual(config.geheimnisse_laden(), 2)
        self.assertEqual(self.env.read_text(encoding="utf-8").splitlines(), ["FOO=bar"])
        self.assertEqual(tresor.bereich("schluessel"), {"A_API_KEY": "dpapi-wert", "B_API_KEY": "klar"})
        self.assertEqual((os.environ["A_API_KEY"], os.environ["B_API_KEY"]), ("dpapi-wert", "klar"))

    def test_vernichtet_nimmt_schluessel_aus_dem_prozess(self):
        config.geheimnis_setzen("TEST_API_KEY", "geheim")
        tresor.vernichten("Test")
        self.assertNotIn("TEST_API_KEY", os.environ)


class Controllcenter(_Basis):
    def test_liste_ohne_werte(self):
        with mock.patch.object(config, "tresor_namen", return_value=["OPENAI_API_KEY", "EIGEN_API_KEY"]), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-geheim-wert"}):
            daten = KZ.tresor_eintraege()
        self.assertNotIn("sk-geheim-wert", repr(daten))
        namen = {e["name"]: e for g in daten["gruppen"] for e in g["eintraege"]}
        self.assertTrue(namen["OPENAI_API_KEY"]["gesetzt"])
        self.assertIn("EIGEN_API_KEY", namen)                                   # „Eigene“
        self.assertIn("OLLAMA_API_KEY", namen)
        self.assertGreater(len(daten["gruppen"]), 2)                            # ganze Auswahl

    def test_namen_und_werte_pruefen(self):
        self.assertIsNone(KZ.tresor_name_pruefen("MEIN_DIENST_API_KEY", set()))
        self.assertIsNotNone(KZ.tresor_name_pruefen("PATH", set()))
        self.assertIsNotNone(KZ.tresor_name_pruefen("../x_API_KEY", set()))
        self.assertIsNotNone(KZ.tresor_wert_pruefen("a\nb"))
        self.assertEqual(KZ._email_kurz("test_user@example.com"), "t…@example.com")

    def test_ki_sieht_den_tresor_nicht(self):
        system = KZ.system_prompt("Test")
        self.assertIn("Tresor", system)
        self.assertIsNone(KZ.aktion_pruefen({"tool": "zeigen", "seite": "tresor"}))

    def test_fenster_tresor_seite(self):
        from PySide6.QtWidgets import QApplication, QInputDialog
        QApplication.instance() or QApplication([])
        import control_fenster as CF
        gesendet: list[dict] = []
        leitung = mock.MagicMock()
        leitung.senden.side_effect = gesendet.append
        f = CF.ControlFenster(leitung)
        self.addCleanup(f.deleteLater)
        f._ereignis({"t": "tresor", "gruppen": [{"titel": "☁ Chat-Anbieter", "eintraege": [
            {"name": "OPENAI_API_KEY", "titel": "OpenAI", "url": "https://example.com", "gesetzt": False}]}],
            "chatgpt": {"angemeldet": False}, "stand": {"verfuegbar": True, "siegel": "abc", "schluessel": 0}})
        knopf = next(w for w in f.tresor.findChildren(CF.Knopf) if w.text() == "Eintragen")
        with mock.patch.object(QInputDialog, "getText", return_value=(" sk-neu ", True)):
            knopf.click()
        self.assertIn({"op": "tresor_setzen", "name": "OPENAI_API_KEY", "wert": "sk-neu"}, gesendet)
        f._ereignis({"t": "tresor_zeigen", "name": "OPENAI_API_KEY"})
        self.assertEqual(f.stapel.currentIndex(), CF.SEITEN["tresor"])
        self.assertIn({"op": "tresor"}, gesendet)


class Einbindung(unittest.TestCase):
    def test_kein_schluessel_mehr_im_terminal(self):
        quelle = main_quelle.text()
        self.assertNotIn("API-Key einfügen", quelle)
        self.assertGreaterEqual(quelle.count("await tresor_zeigen("), 3)
        self.assertIn("config.geheimnisse_laden()", quelle)
        self.assertIn("create_task(tresor_waechter(ctx))", quelle)

    def test_ki_kommt_nicht_an_den_tresor(self):
        import actions
        self.assertIn(tresor.ordner().parent, [Path(p) for p in actions._tresor_bases()])
        self.assertTrue(actions._GEHEIM.search(r"Get-Content C:\x\.tresor\inhalt"))

    def test_test_lauf_nie_der_echte_tresor(self):
        self.assertIn("nemicli_testdaten", str(tresor.ordner()))


if __name__ == "__main__":
    unittest.main()
