"""torch installiert der Nutzer selbst im venv-Fenster (/venv): /einrichten und /update zeigen nur den Befehl."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui", "tests"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import commands     # noqa: E402
import main_quelle  # noqa: E402
import syscheck     # noqa: E402
import uvsetup      # noqa: E402
import venvpflege   # noqa: E402
import wizard       # noqa: E402

NVIDIA_BEFEHL = "pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cu132"


class Befehl(unittest.TestCase):
    def test_nvidia_blackwell_und_amd(self):
        with patch.object(syscheck, "_wheel_for", lambda cap: ("cu132", "Blackwell")):
            self.assertEqual(syscheck.torch_plan({"vendor": "nvidia"}, "12.0")["cmd"], NVIDIA_BEFEHL)
        self.assertEqual(syscheck.torch_plan({"vendor": "amd"})["cmd"], "pip3 install torch-directml")


class Einrichten(unittest.TestCase):
    def test_torch_schritt_nur_hinweis(self):
        self.assertNotIn("torch", wizard.ERLEDIGER)
        with patch.object(wizard.S, "gpu_info", lambda: {"vendor": "nvidia"}), \
                patch.object(wizard.syscheck, "compute_cap", lambda: "12.0"), \
                patch.object(syscheck, "_wheel_for", lambda cap: ("cu132", "Blackwell")):
            schritte = wizard.check_all()
        torch = next(s for s in schritte if s["id"] == "torch")
        self.assertFalse(torch["machbar"])
        self.assertEqual(torch["befehl"], NVIDIA_BEFEHL)
        self.assertIn("/venv", torch["info"])

    def test_venv_fenster(self):
        aufrufe = []
        py = Path(r"C:\x\venv\Scripts\python.exe")
        with patch.object(wizard, "_venv_python", lambda: py), \
                patch.object(wizard, "torch_befehl", lambda: NVIDIA_BEFEHL), \
                patch("subprocess.Popen", lambda *a, **k: aufrufe.append((a, k))):
            self.assertEqual(wizard.venv_fenster(), NVIDIA_BEFEHL)
        (befehl,), k = aufrufe[0]
        self.assertEqual(befehl[:2], ["cmd.exe", "/k"])
        self.assertIn(str(py.parent / "activate.bat"), befehl[2])
        self.assertIn("ensurepip", befehl[2])                          # pip3 landet sicher im venv
        self.assertIn(NVIDIA_BEFEHL, befehl[2])
        self.assertTrue(k.get("creationflags"))                        # eigenes Fenster

    def test_ohne_venv_klare_meldung(self):
        with patch.object(wizard, "_venv_python", lambda: None):
            with self.assertRaises(RuntimeError) as e:
                wizard.venv_fenster()
        self.assertIn("/einrichten", str(e.exception))

    def test_neues_venv_mit_pip(self):
        befehle = []
        with patch.object(uvsetup, "venv_python", lambda: None), \
                patch.object(uvsetup, "_altes_venv_beiseite", lambda: ""), \
                patch.object(uvsetup, "ensure_python", lambda on_status=None: ""), \
                patch.object(uvsetup, "_run", lambda cmd, *a, **k: (befehle.append(cmd), (1, "x"))[1]):
            with self.assertRaises(RuntimeError):
                uvsetup.ensure_venv()
        self.assertIn("--seed", befehle[0])


class Update(unittest.TestCase):
    def test_torch_nur_melden(self):
        self.assertFalse(venvpflege.Eintrag("torch", "2.14.0", "", "torch", "torch-bau").machbar)
        self.assertFalse(venvpflege.Eintrag("torchvision", "0.1", "0.2", "x", "update").machbar)
        self.assertTrue(venvpflege.Eintrag("rich", "15", "16", "x", "update").machbar)
        with patch.object(wizard, "_torch_bau", lambda: "cpu"), \
                patch.object(wizard, "torch_befehl", lambda: NVIDIA_BEFEHL), \
                patch("setup.gpu_info", lambda: {"vendor": "nvidia"}):
            e = venvpflege._torch_eintrag({"torch": "2.14.0"})
        self.assertIn("/venv", e.hinweis)
        self.assertIn(NVIDIA_BEFEHL, e.hinweis)


class Befehle(unittest.TestCase):
    def test_venv_befehl(self):
        self.assertIn("/venv", commands.COMMANDS)
        self.assertIn('cmd == "/venv"', main_quelle.text())


if __name__ == "__main__":
    unittest.main()
