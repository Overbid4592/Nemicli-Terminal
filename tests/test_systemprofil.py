"""Steckbrief des Rechners (engines/systemprofil.py) mit gespielten Teilen.

python -m unittest tests.test_systemprofil
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import hfladen as H                                    # noqa: E402
import systemprofil as SP                              # noqa: E402


class KartenTests(unittest.TestCase):
    def test_hersteller_und_integriert(self):
        faelle = {"NVIDIA GeForce RTX 5060 Ti": ("nvidia", False),
                  "AMD Radeon RX 7900 XTX": ("amd", False),
                  "AMD Radeon(TM) Graphics": ("amd", True),
                  "Intel(R) Arc(TM) A770 Graphics": ("intel", False),
                  "Intel(R) Arc(TM) Graphics": ("intel", True),
                  "Intel(R) UHD Graphics 770": ("intel", True)}
        for name, (h, integriert) in faelle.items():
            with self.subTest(name):
                self.assertEqual(SP._hersteller(name), h)
                self.assertEqual(SP._integriert(name, h), integriert)

    def test_generation(self):
        for cap, gen in (("12.0", "Blackwell"), ("8.9", "Ada"), ("8.6", "Ampere"), ("7.5", "Turing"), ("", "")):
            self.assertEqual(SP.Grafikkarte("x", "nvidia", cuda_cap=cap).generation, gen)


class ProfilTests(unittest.TestCase):
    def profil(self, *gpus):
        return SP.Profil(cpu="Test-CPU", kerne=8, threads=16, avx2=True, ram_mb=32768, ram_frei_mb=16000,
                         gpus=list(gpus), cuda_max=13.0, windows="Windows 11", build=26100)

    def test_nvidia_neben_integrierter(self):
        p = self.profil(SP.Grafikkarte("Intel(R) UHD Graphics 770", "intel", 128, True),
                        SP.Grafikkarte("NVIDIA GeForce RTX 4070", "nvidia", 12282, cuda_cap="8.9"))
        self.assertEqual((p.motor, p.haupt_gpu.name), ("cuda", "NVIDIA GeForce RTX 4070"))
        self.assertEqual(p.vram_nutzbar_mb, 12282 - SP.WINDOWS_VRAM_MB)
        self.assertTrue(any("CUDA auf NVIDIA GeForce RTX 4070" in z for z in p.zeilen()))

    def test_amd_rechnet_auf_der_cpu(self):
        p = self.profil(SP.Grafikkarte("AMD Radeon RX 7900 XTX", "amd", 24560))
        self.assertEqual((p.motor, p.vram_nutzbar_mb, p.haupt_gpu.hersteller), ("cpu", 0, "amd"))
        self.assertTrue(any(z.startswith("Motor:") and "CPU" in z for z in p.zeilen()))

    def test_ohne_grafikkarte(self):
        p = self.profil()
        self.assertIsNone(p.haupt_gpu)
        self.assertIn("GPU:     keine erkannt", p.zeilen())

    def test_erkennen_setzt_zusammen(self):
        nv = [SP.Grafikkarte("NVIDIA GeForce RTX 5060 Ti", "nvidia", 16311, cuda_cap="12.0")]
        reg = [SP.Grafikkarte("NVIDIA GeForce RTX 5060 Ti", "nvidia", 0),
               SP.Grafikkarte("Intel(R) UHD Graphics 770", "intel", 128, True)]
        with mock.patch.object(SP, "_cpu", lambda p: None), \
                mock.patch.object(SP, "_nvidia_karten", lambda: (nv, 13.2)), \
                mock.patch.object(SP, "_registry_karten", lambda: reg), \
                mock.patch.object(SP, "_PROFIL", None):
            p = SP.erkennen(neu=True)
        self.assertEqual([g.name for g in p.gpus], ["NVIDIA GeForce RTX 5060 Ti", "Intel(R) UHD Graphics 770"])
        self.assertEqual((p.cuda_max, p.gpus[0].vram_mb), (13.2, 16311))


    def test_ohne_prozesse_nur_registry_und_nicht_gemerkt(self):
        reg = [SP.Grafikkarte("NVIDIA GeForce RTX 5060 Ti", "nvidia", 16311)]

        def verboten():
            raise AssertionError("nvidia-smi gestartet")
        with mock.patch.object(SP, "_cpu", lambda p: None), \
                mock.patch.object(SP, "_nvidia_karten", verboten), \
                mock.patch.object(SP, "_registry_karten", lambda: reg), \
                mock.patch.object(SP, "_PROFIL", None):
            p = SP.erkennen(prozesse=False)
            self.assertEqual((p.motor, p.gpus[0].vram_mb), ("cuda", 16311))
            self.assertIsNone(SP._PROFIL)


class AmpelTests(unittest.TestCase):
    def test_downloader_nutzt_ram_und_rechenweg(self):
        gb = 2**30
        self.assertEqual(H.passt(10 * gb, 0, 64 * 1024)[0], "🐢")        # AMD/keine NVIDIA: CPU mit genug RAM
        self.assertEqual(H.passt(60 * gb, 0, 64 * 1024)[0], "❌")


if __name__ == "__main__":
    unittest.main()
