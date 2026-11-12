"""Updates über winget (tools/aktualisieren.py, Werkzeuge updates_suchen / updates_installieren) – ohne echtes
winget: nachsehen frei, installieren nur nach Freigabe, NemiCLI erhöht nie selbst die Rechte.

python -m unittest tests.test_aktualisieren
"""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions as A                                    # noqa: E402
import aktualisieren as AK                             # noqa: E402
import modes                                           # noqa: E402

TABELLE = """   - \r   \\ \r   | \r
Name                              ID                         Version      Verfügbar    Quelle
-----------------------------------------------------------------------------------------------
Mozilla Firefox (x64 de)          Mozilla.Firefox            131.0        132.0.1      winget
7-Zip 26.05 (x64)                 7zip.7zip                  26.05        26.06        winget
Programm mit sehr langem Namen …  Beispiel.LangesProgramm    1.2.3        1.3.0        winget
2 Aktualisierungen verfügbar.

Die folgenden Pakete haben ein Upgrade, benötigen aber eine explizite Angabe:
Name     ID          Version Verfügbar Quelle
----------------------------------------------
Gepinnt  Pin.Paket   1.0     2.0       winget
"""


class Bausteine(unittest.TestCase):

    def test_tabelle_unabhaengig_von_der_sprache(self):
        e = AK.tabelle(AK.saeubern(TABELLE))
        self.assertEqual(["Mozilla.Firefox", "7zip.7zip", "Beispiel.LangesProgramm"], [x["id"] for x in e])
        self.assertEqual(("Mozilla Firefox (x64 de)", "131.0", "132.0.1", "winget"),
                         (e[0]["name"], e[0]["version"], e[0]["verfuegbar"], e[0]["quelle"]))
        englisch = TABELLE.replace("Verfügbar", "Available").replace("Quelle", "Source")
        self.assertEqual(3, len(AK.tabelle(englisch)))
        self.assertEqual([], AK.tabelle("Es wurde kein installiertes Paket gefunden, das den Eingabekriterien entspricht."))

    def test_saeubern_nimmt_den_kreisel_raus(self):
        self.assertNotIn("\\", AK.saeubern("   - \r   \\ \r   | \rFertig\r\n  ██████▒▒▒▒  \nok"))
        self.assertEqual("Fertig\nok", AK.saeubern("   - \r   \\ \r   | \rFertig\r\n  ██████▒▒▒▒  \nok"))

    def test_ids_pruefen(self):
        self.assertEqual(["Mozilla.Firefox", "7zip.7zip"], AK.gueltige_ids("Mozilla.Firefox, 7zip.7zip Mozilla.Firefox"))
        for kaputt in (["--force"], ["a b"], ["x; del c:"], [], ["Paket" + str(i) for i in range(AK.MAX_PAKETE + 1)]):
            with self.subTest(kaputt=kaputt), self.assertRaises(ValueError):
                AK.gueltige_ids(kaputt)

    def test_installieren_zustaende(self):
        aufrufe = []

        def winget(code, text=""):
            def f(argumente, timeout):
                aufrufe.append(argumente)
                return code, text
            return f
        for code, text, zustand in ((0, "Erfolgreich installiert", "ok"), (-1978335189, "", "kein_update"),
                                    (1223, "", "abgebrochen"), (5, "Installer meldet 1223", "abgebrochen"),
                                    (-1978335226, "Hash passt nicht", "fehler")):
            with self.subTest(code=code), mock.patch.object(AK, "_winget", winget(code, text)):
                self.assertEqual(zustand, AK.installieren("Mozilla.Firefox")[0])
        argumente = aufrufe[0]
        self.assertEqual(["upgrade", "--id", "Mozilla.Firefox", "--exact", "--silent"], argumente[:5])
        self.assertFalse(any("runas" in x.lower() or "admin" in x.lower() for x in argumente))   # nie selbst erhöhen
        with mock.patch.object(AK, "_winget", side_effect=subprocess.TimeoutExpired("winget", 1)):
            self.assertEqual("zeit", AK.installieren("Mozilla.Firefox")[0])

    def test_winget_fehlt(self):
        with mock.patch.object(AK.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(AK.WingetFehlt):
                AK.liste()


class Werkzeuge(unittest.TestCase):

    def test_anmeldung_und_freigabe(self):
        self.assertFalse(A.ACTIONS["updates_suchen"]["confirm"])
        self.assertTrue(A.ACTIONS["updates_installieren"]["confirm"])          # der Nutzer bestätigt vorher
        self.assertIn("updates_suchen", modes.READ_TOOLS)
        self.assertNotIn("updates_installieren", modes.READ_TOOLS)
        self.assertNotIn("updates_installieren", modes.WACHE_URTEIL_TOOLS)      # nie aus dem Hintergrund
        text = A.describe({"tool": "updates_installieren", "ids": ["Mozilla.Firefox"]})
        self.assertIn("Mozilla.Firefox", text)
        self.assertIn("UAC", text)

    def test_suchen(self):
        with mock.patch.object(AK, "_winget", return_value=(0, AK.saeubern(TABELLE))):
            r = A._updates_suchen({})
        self.assertTrue(r.ok)
        self.assertIn("3 Update(s)", r.text)
        self.assertIn("Mozilla.Firefox · 131.0 → 132.0.1", r.text.replace("Id ", ""))
        self.assertIn("UAC", r.text)
        with mock.patch.object(AK, "_winget", return_value=(-1978335212, "Kein installiertes Paket gefunden.")):
            self.assertIn("aktuell", A._updates_suchen({}).text)

    def test_installieren(self):
        with mock.patch.object(AK, "installieren", side_effect=[("ok", ""), ("abgebrochen", "Code 1223")]):
            r = A._updates_installieren({"ids": ["Mozilla.Firefox", "7zip.7zip"]})
        self.assertIsNone(r.ok)
        self.assertIn("✅ Mozilla.Firefox", r.text)
        self.assertIn("⛔ abgebrochen 7zip.7zip", r.text)
        self.assertIn("alten Stand", r.text)
        self.assertFalse(A._updates_installieren({"ids": ["--all"]}).ok)


if __name__ == "__main__":
    unittest.main()
