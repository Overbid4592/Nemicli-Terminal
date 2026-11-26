"""Ordnerkennung (core/ordnerkennung.py) und ihre Nutzung im Setup (installer/aufspielen.py):
Kennung anlegen, Umbenennen erkennen, verschlüsselter Merkzettel, Prüfung vor dem Aktualisieren."""
import base64
import ctypes
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _d in ("core", "installer"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import aufspielen as A     # noqa: E402
import ordnerkennung as K  # noqa: E402


def _programm(ordner: Path, build: str = "9.75.1") -> Path:
    (ordner / "NemiCLIT2").mkdir(parents=True)
    (ordner / "NemiCLIT2" / "build.txt").write_text(build + "\n", encoding="utf-8")
    (ordner / "NemiCLI.exe").write_bytes(b"MZ")
    return ordner


@unittest.skipUnless(sys.platform == "win32", "nur Windows (DPAPI, Dateiattribute)")
class Kennung(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.heim = self.tmp / "Benutzer"
        self.heim.mkdir()
        for p in (mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp / "AppData")}),
                  mock.patch.object(Path, "home", return_value=self.heim)):
            p.start()
            self.addCleanup(p.stop)

    def test_anlegen_versteckt_und_verschluesselt(self):
        ordner = _programm(self.heim / ".nemicli")
        d = K.pflegen(ordner, "9.75.1")
        self.assertRegex(d["kennung"], r"^[0-9a-f]{32}$")
        datei = ordner / K.KENNUNG_DATEI
        attr = ctypes.windll.kernel32.GetFileAttributesW(str(datei))
        self.assertTrue(attr & 0x2 and attr & 0x4, hex(attr))          # versteckt + System
        self.assertEqual(K.lesen(ordner)["kennung"], d["kennung"])
        self.assertEqual(K.gemerkter_ordner(), (ordner, d["kennung"]))
        roh = K.register_datei().read_text(encoding="utf-8")
        self.assertNotIn(".nemicli", roh)                                # kein Klartext-Pfad
        self.assertNotIn(d["kennung"], roh)

    def test_umbenennen_wird_erkannt(self):
        alt = _programm(self.heim / ".nemicli")
        kennung = K.pflegen(alt)["kennung"]
        neu = self.heim / "MeinNemi"
        alt.rename(neu)
        d = K.pflegen(neu)
        self.assertEqual((d["kennung"], d["name"], d["vorher"]), (kennung, "MeinNemi", [".nemicli"]))
        self.assertTrue(d["umbenannt"])
        self.assertEqual(K.gemerkter_ordner(), (neu, kennung))
        noch_mal = self.heim / "Nemi3"                                    # versteckte Datei erneut überschreiben
        neu.rename(noch_mal)
        self.assertEqual(K.pflegen(noch_mal)["vorher"], [".nemicli", "MeinNemi"])

    def test_ohne_aenderung_wird_nichts_geschrieben(self):
        ordner = _programm(self.heim / ".nemicli")
        K.pflegen(ordner)
        vorher = (ordner / K.KENNUNG_DATEI).stat().st_mtime_ns, K.register_datei().stat().st_mtime_ns
        K.pflegen(ordner)
        self.assertEqual(vorher, ((ordner / K.KENNUNG_DATEI).stat().st_mtime_ns,
                                  K.register_datei().stat().st_mtime_ns))

    def test_veraenderter_merkzettel_gilt_nicht(self):
        K.pflegen(_programm(self.heim / ".nemicli"))
        roh = json.loads(K.register_datei().read_text(encoding="utf-8"))
        b = bytearray(base64.b64decode(roh["daten"]))
        b[-5] ^= 0xFF
        roh["daten"] = base64.b64encode(bytes(b)).decode()
        K.register_datei().write_text(json.dumps(roh), encoding="utf-8")
        self.assertIsNone(K.gemerkter_ordner())

    def test_kaputte_kennung_wird_neu_angelegt(self):
        ordner = _programm(self.heim / ".nemicli")
        (ordner / K.KENNUNG_DATEI).write_text("{kaputt", encoding="utf-8")
        self.assertIsNone(K.lesen(ordner))
        self.assertRegex(K.pflegen(ordner)["kennung"], r"^[0-9a-f]{32}$")

    # --- Setup ------------------------------------------------------------------------------------------

    def test_setup_findet_umbenannten_ordner(self):
        alt = _programm(self.heim / ".nemicli", "9.75.7")
        K.pflegen(alt)
        neu = self.heim / "MeinNemi"
        alt.rename(neu)
        K.pflegen(neu)
        with mock.patch.object(A, "signatur_passt", return_value=True) as sig:
            self.assertEqual(A.installation(), A.Installation(neu, "9.75.7"))
        sig.assert_called_once_with(neu / "NemiCLI.exe")

    def test_setup_ohne_passende_signatur_nimmt_den_festen_ordner(self):
        alt = _programm(self.heim / ".nemicli")
        K.pflegen(alt)
        neu = self.heim / "MeinNemi"
        alt.rename(neu)
        K.pflegen(neu)
        with mock.patch.object(A, "signatur_passt", return_value=False):
            self.assertEqual(A.installation(), A.Installation(self.heim / ".nemicli", ""))

    def test_setup_mit_fremder_kennung_im_ordner(self):
        fremd = _programm(self.heim / "Fremd")
        K.pflegen(fremd)
        (fremd / K.KENNUNG_DATEI).unlink()                               # Kennung passt nicht mehr
        with mock.patch.object(A, "signatur_passt", return_value=True):
            self.assertEqual(A.installation().ordner, self.heim / ".nemicli")

    def test_fester_ordner_geht_vor_kopie(self):
        echt = _programm(self.heim / ".nemicli", "9.75.9")
        K.pflegen(echt)
        kopie = _programm(self.tmp / "Testkopie", "9.75.9")
        K.pflegen(kopie)                                                 # zuletzt gestartet: die Kopie
        self.assertEqual(K.gemerkter_ordner()[0], kopie)
        with mock.patch.object(A, "signatur_passt", return_value=True) as sig:
            self.assertEqual(A.installation(), A.Installation(echt, "9.75.9"))
        sig.assert_not_called()

    def test_setup_fester_ort_ohne_signaturpruefung(self):
        ordner = _programm(self.heim / ".nemicli", "9.75.8")
        K.pflegen(ordner)
        with mock.patch.object(A, "signatur_passt") as sig:
            self.assertEqual(A.installation(), A.Installation(ordner, "9.75.8"))
        sig.assert_not_called()

    def test_entwicklung_vergleicht_keine_signatur(self):
        self.assertFalse(A.signatur_passt(Path(sys.executable)))       # Setup nicht gepackt: nein


if __name__ == "__main__":
    unittest.main()
