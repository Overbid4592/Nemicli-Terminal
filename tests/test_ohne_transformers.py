"""NemiCLI ohne transformers: Gedächtnis nimmt GGUF zuerst, /update räumt Altlasten weg.

python -m unittest tests.test_ohne_transformers
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import luecken as L                                    # noqa: E402
import memory                                          # noqa: E402
import venvpflege as V                                 # noqa: E402
import wizard                                          # noqa: E402


class GedaechtnisTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        st = self.ordner / "AQwenSafetensors"                  # alphabetisch zuerst
        st.mkdir()
        for f in ("config.json", "model.safetensors", "tokenizer.json"):
            (st / f).write_text("{}", encoding="utf-8")
        gg = self.ordner / "Qwen3GGUF"
        gg.mkdir()
        (gg / "Qwen3-Embedding-0.6B-Q8_0.gguf").write_bytes(b"GGUF")
        for p in (mock.patch.object(memory, "EMBED_ORDNER", self.ordner),
                  mock.patch.object(memory, "_gewuenscht", lambda: "")):
            p.start()
            self.addCleanup(p.stop)

    def test_gguf_zuerst(self):
        self.assertEqual("Qwen3GGUF", memory.modell_ordner().name)

    def test_safetensors_ohne_transformers_sagt_was_zu_tun_ist(self):
        with mock.patch.object(memory, "_gewuenscht", lambda: "AQwenSafetensors"), \
                mock.patch.object(memory, "_transformers_da", lambda: False), \
                mock.patch.object(memory, "_encoder", None):
            text = memory.encoder_status()
        self.assertIn("braucht transformers", text)
        self.assertIn("GGUF", text)


class AufraeumenTests(unittest.TestCase):
    PAKETE = {"rich": "14.0.0", "transformers": "4.57.6", "diffusers": "0.40.0", "accelerate": "1.0.0",
              "opencv_python": "4.10.0.84", "rembg": "2.0.85", "pip": "26.0"}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.site = Path(tmp.name) / "Lib" / "site-packages"
        for name, version in self.PAKETE.items():
            (self.site / f"{name}-{version}.dist-info").mkdir(parents=True)
        # rembg braucht opencv-python – das darf nicht weg; diffusers' transformers-Bedarf ist nur ein Extra
        (self.site / "rembg-2.0.85.dist-info" / "METADATA").write_text(
            "Name: rembg\nRequires-Dist: opencv-python-headless\nRequires-Dist: opencv-python\n", encoding="utf-8")
        (self.site / "diffusers-0.40.0.dist-info" / "METADATA").write_text(
            'Name: diffusers\nRequires-Dist: transformers>=4.41; extra == "test"\n', encoding="utf-8")
        for p in (mock.patch.object(V, "site_packages", lambda: self.site),
                  mock.patch.object(V, "_torch_eintrag", lambda inst: None),
                  mock.patch.object(V, "_veraltet", lambda: []),
                  mock.patch.object(V, "_luecken_holen", lambda inst: {}),
                  mock.patch.object(V, "_dev_wuensche", lambda: []),
                  mock.patch.object(wizard, "requirements_ohne_torch", lambda: ["rich>=13"])):
            p.start()
            self.addCleanup(p.stop)

    def test_altlasten_ohne_abnehmer(self):
        liste = {e.name: e for e in V.pruefen()}
        self.assertEqual(sorted(liste), ["accelerate", "diffusers", "transformers"])   # opencv braucht rembg
        self.assertTrue(all(e.grund == "ueberfluessig" and e.entfernen for e in liste.values()))
        self.assertFalse(liste["diffusers"].noetig)

    def test_opencv_ohne_abnehmer_kommt_weg(self):
        (self.site / "rembg-2.0.85.dist-info" / "METADATA").write_text(
            "Name: rembg\nRequires-Dist: numpy\nRequires-Dist: pillow\n", encoding="utf-8")
        liste = {e.name: e for e in V.pruefen()}
        self.assertIn("opencv-python", liste)
        self.assertEqual("ueberfluessig", liste["opencv-python"].grund)

    def test_browser_reste_kommen_weg(self):
        for name in ("pyside6_essentials-6.12.0", "pyside6_addons-6.12.0", "pyside6_webengine-6.12.0.140"):
            (self.site / f"{name}.dist-info").mkdir()
        (self.site / "pyside6_webengine-6.12.0.140.dist-info" / "METADATA").write_text(
            "Name: PySide6_WebEngine\nRequires-Dist: PySide6_Essentials==6.12.0\nRequires-Dist: PySide6_Addons==6.12.0\n",
            encoding="utf-8")
        liste = {e.name: e for e in V.pruefen()}
        self.assertEqual("ueberfluessig", liste["pyside6-webengine"].grund)
        self.assertEqual("ueberfluessig", liste["pyside6-addons"].grund)      # nur die Altlast verlangte es
        self.assertNotIn("pyside6-essentials", liste)

    def test_addons_bleiben_wenn_volles_pyside6_sie_braucht(self):
        (self.site / "pyside6_addons-6.12.0.dist-info").mkdir()
        (self.site / "pyside6-6.12.0.dist-info").mkdir()
        (self.site / "pyside6-6.12.0.dist-info" / "METADATA").write_text(
            "Name: PySide6\nRequires-Dist: PySide6_Addons==6.12.0\n", encoding="utf-8")
        self.assertNotIn("pyside6-addons", {e.name for e in V.pruefen()})

    def test_mit_luecke_ist_entfernen_noetig(self):
        funde = {"transformers": [L.Luecke(id="G", cve="CVE-2026-4372", schwere="hoch", behoben="5.3.0")]}
        e = {e.name: e for e in V.pruefen(luecken_holen=lambda inst: funde)}["transformers"]
        self.assertEqual(e.grund, "ueberfluessig")
        self.assertTrue(e.noetig)
        self.assertIn("Lücken", e.hinweis)

    def test_entwickler_behalten_transformers(self):
        with mock.patch.object(V, "_dev_wuensche", lambda: ["transformers>=5.10"]):
            namen = {e.name: e.grund for e in V.pruefen()}
        self.assertEqual(namen.get("transformers"), "passt nicht")       # 4.57 → wird aktualisiert, nicht entfernt

    def test_einspielen_entfernt(self):
        aufrufe = []

        def ausfuehren(cmd, timeout):
            aufrufe.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(V, "_ausfuehren", ausfuehren), \
                mock.patch.object(V, "in_gebrauch", lambda name, site: False):
            res = V.einspielen([V.Eintrag("diffusers", "0.40.0", "", "diffusers", "ueberfluessig")])
        self.assertEqual((res["aufgeraeumt"], res["entfernt"]), (["diffusers"], []))
        self.assertTrue(any("uninstall" in c and "diffusers" in c for c in aufrufe))


if __name__ == "__main__":
    unittest.main()
