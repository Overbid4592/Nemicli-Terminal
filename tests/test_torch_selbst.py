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
        with patch.object(syscheck, "_wheel_for", lambda cap: ("cu132", "Blackwell")),                 patch.object(syscheck, "installierter_bau", lambda: 0.0):
            self.assertEqual(syscheck.torch_plan({"vendor": "nvidia"}, "12.0")["cmd"], NVIDIA_BEFEHL)
        self.assertEqual(syscheck.torch_plan({"vendor": "amd"})["cmd"], "pip3 install torch-directml")


class Einrichten(unittest.TestCase):
    def test_torch_schritt_nur_hinweis(self):
        self.assertNotIn("torch", wizard.ERLEDIGER)
        with patch.object(wizard.S, "gpu_info", lambda: {"vendor": "nvidia"}), \
                patch.object(wizard.syscheck, "compute_cap", lambda: "12.0"), \
                patch.object(syscheck, "_wheel_for", lambda cap: ("cu132", "Blackwell")),                 patch.object(syscheck, "installierter_bau", lambda: 0.0):
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


def _index(kanaele: dict[str, list[str]]):
    """Gefälschter PyTorch-Index: Kanal → torch-Versionen (Windows, dieses Python)."""
    tag = "cp" + uvsetup.PYVER.replace(".", "")

    def holen(url: str) -> str:
        kanal = url.rstrip("/").split("/")[-2]
        return "".join(f'<a href="torch-{v}%2B{kanal}-{tag}-{tag}-win_amd64.whl">x</a>' for v in kanaele.get(kanal, []))
    return holen


class KeinFesterBau(unittest.TestCase):
    """Empfohlen wird nie fest ein CUDA-Bau: der installierte, wenn er passt, sonst der neueste im Index."""

    def setUp(self):
        syscheck._KANAL_MERK.clear()
        p = patch.object(syscheck, "_wheel_for", lambda cap: ("cu128", "Blackwell"))
        p.start()
        self.addCleanup(p.stop)

    def test_installierter_bau_bleibt(self):
        plan = syscheck.torch_plan({"vendor": "nvidia", "cuda_max": 13.5}, "12.0", installiert=13.4)
        self.assertEqual(plan["kanal"], "cu134")
        self.assertIn("passt", plan["grund"])
        self.assertEqual(plan["warnung"], "")

    def test_frisch_der_neueste_im_index(self):
        holen = _index({"cu134": ["2.15.0"], "cu132": ["2.15.0"], "cu128": ["2.14.0"]})
        plan = syscheck.torch_plan({"vendor": "nvidia", "cuda_max": 13.5}, "12.0", installiert=0.0, holen=holen)
        self.assertEqual(plan["kanal"], "cu134")                     # cu135 gibt es (noch) nicht
        self.assertTrue(plan["cmd"].endswith("/whl/cu134"))

    def test_ohne_netz_der_mindest_bau(self):
        def kaputt(url):
            raise OSError("offline")
        plan = syscheck.torch_plan({"vendor": "nvidia", "cuda_max": 13.5}, "12.0", installiert=0.0, holen=kaputt)
        self.assertEqual(plan["kanal"], "cu128")
        self.assertIn("/update torch", plan["grund"])

    def test_treiber_zu_alt_und_bau_ueber_der_grenze(self):
        plan = syscheck.torch_plan({"vendor": "nvidia", "cuda_max": 12.4}, "12.0", installiert=0.0)
        self.assertIn("mindestens 12.8", plan["warnung"])
        plan = syscheck.torch_plan({"vendor": "nvidia", "cuda_max": 13.0}, "12.0", installiert=13.4,
                                   holen=_index({"cu130": ["2.15.0"]}))
        self.assertEqual(plan["kanal"], "cu130")                     # 13.4 kann dieser Treiber nicht

    def test_kein_fester_kanal_in_daten_und_hilfe(self):
        self.assertNotIn("cu132", (ROOT / "ggufengine" / "daten" / "hardware.json").read_text(encoding="utf-8"))
        self.assertNotIn("cu132", commands.hilfe_fuer_ki("/venv"))
        self.assertIn("/update torch", commands.hilfe_fuer_ki("/update"))

    def test_update_torch_ohne_menue(self):
        quelle = main_quelle.text()
        self.assertIn("frage = await update_befehl(ctx, arg)", quelle)
        self.assertIn('if arg.strip().lower() in ("torch", "pytorch"):', quelle)


if __name__ == "__main__":
    unittest.main()
