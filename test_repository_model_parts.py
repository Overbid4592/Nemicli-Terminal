"""Verifiziert die tatsaechlichen im Repository gelieferten Funke-Stuecke.

Nur fuer CI oder eine isolierte Arbeitskopie; erstellt die Modelldateien
aus den Paketstuecken im temporaeren Checkout, nicht auf dem Nutzer-PC.
"""
import unittest
from pathlib import Path
from einrichtungshelfer.laufzeit import stuecke_zusammensetzen

ROOT = Path(__file__).resolve().parent

class RepositoryModelle(unittest.TestCase):
    def test_beide_modelle_und_sha256(self):
        for ordner in ("modell", "modell_gross"):
            with self.subTest(modell=ordner):
                ziel = ROOT / "einrichtungshelfer" / ordner / "helfer.bin"
                self.assertTrue(
                    stuecke_zusammensetzen(ziel),
                    f"Modell {ordner}: Teile fehlen oder SHA-256 ungueltig")
                self.assertGreater(ziel.stat().st_size, 100_000)

if __name__ == "__main__":
    unittest.main()
