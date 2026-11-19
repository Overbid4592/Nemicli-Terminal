"""Schutz rund um Medien und Modelle: präparierte Dateien, Signier-Befehle, Modellordner, fremde Signatur.

python -m unittest tests.test_schutz_medien_modelle
"""
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions      # noqa: E402
import paths        # noqa: E402
import videodatei as VD  # noqa: E402
from musik import tondatei  # noqa: E402


def _wav(pfad: Path, rate: int, daten: bytes = b"\0\0" * 50) -> None:
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    pfad.write_bytes(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(daten)) + b"WAVE" + b"fmt "
                     + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(daten)) + daten)


class PraeparierteDateien(unittest.TestCase):
    def test_unplausible_abtastrate_wird_abgelehnt(self):
        with tempfile.TemporaryDirectory() as d:
            _wav(Path(d) / "x.wav", 49_999_991)
            t = time.time()
            with self.assertRaises(ValueError):
                tondatei._wav(Path(d) / "x.wav")
            self.assertLess(time.time() - t, 1.0)
            _wav(Path(d) / "gut.wav", 16_000)
            self.assertEqual(50, tondatei.mono(Path(d) / "gut.wav").shape[0])     # 100 Bytes = 50 Proben zu 16 Bit

    def test_info_liest_nicht_die_ganze_datei(self):
        with tempfile.TemporaryDirectory() as d:
            _wav(Path(d) / "x.wav", 16_000, b"\0" * 4_000_000)
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("ganze Datei gelesen")):
                self.assertEqual({}, tondatei.info(Path(d) / "x.wav"))

    def test_auswahl_bei_riesiger_dauer(self):
        t = time.time()
        nummern = VD.auswahl(1.8e12, 30, int(5e13))
        self.assertEqual(32, len(nummern))
        self.assertLess(time.time() - t, 0.5)
        self.assertEqual(32, len(VD._ziel_zeiten(1.8e12, 1.0, 32)))


class SignierBefehle(unittest.TestCase):
    def test_signieren_und_zertifikate_immer_gestoppt(self):
        for befehl in ("Set-AuthenticodeSignature -FilePath a.exe -Certificate $z", "signtool sign /a a.exe",
                       "New-SelfSignedCertificate -Type CodeSigningCert", "Export-PfxCertificate -Cert $z"):
            with self.subTest(befehl=befehl):
                self.assertIsNotNone(actions._IMMER_STOPP.search(befehl))


class Modellordner(unittest.TestCase):
    def test_alte_modellorte_sind_kein_ki_bereich(self):
        with tempfile.TemporaryDirectory() as d:
            daten = Path(d)
            with mock.patch.object(paths, "DATEN", daten), mock.patch.object(paths, "INSTALL", daten / "p"), \
                    mock.patch.object(actions, "_schluessel_steckt", lambda: False):
                frei = actions._daten_bereiche()
                self.assertNotIn((daten / "Models").resolve(), frei)
                self.assertIn((daten / "Bilder").resolve(), frei)
                self.assertIn(daten / "Models", actions._modell_bases())
                self.assertIsNotNone(actions._gesperrt_zum_aendern(str(daten / "Models" / "Krea2" / "x.safetensors")))
                self.assertIsNone(actions._gesperrt_zum_aendern(str(daten / "Bilder" / "a.png")))

    def test_ki_modelle_ist_tabu(self):
        import modellorte
        self.assertIn(Path(modellorte.WURZEL), actions._modelle_wurzel())


class FremdeSignatur(unittest.TestCase):
    def test_nemicli_zertifikat_auf_fremder_datei_warnt(self):
        from wache import herkunft
        gueltig = {"status": "Valid", "signierer": "NemiCLI", "gueltig": True}
        with mock.patch.object(herkunft, "signatur", return_value=gueltig), \
                mock.patch.object(herkunft, "ist_systemort", return_value=False):
            self.assertIn("VORSICHT", herkunft.signatur_zeile(r"C:\Temp\werkzeug.exe"))
            self.assertNotIn("VORSICHT", herkunft.signatur_zeile(r"C:\Programme\NemiCLI\NemiCLI.exe"))


if __name__ == "__main__":
    unittest.main()
