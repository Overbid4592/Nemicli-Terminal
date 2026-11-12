"""/update – venv prüfen und auffrischen (tools/venvpflege.py) mit einem gespielten venv.

python -m unittest tests.test_venvpflege
"""

import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import venvpflege as V                                 # noqa: E402
import wizard                                          # noqa: E402

PAKETE = {"rich": "13.0.0", "httpx": "0.20.0", "tokenizers": "0.22.2",
          "opencv_python": "4.10.0.84", "torch": "2.14.0", "pip": "26.0"}


class _Basis(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.site = Path(tmp.name) / "Lib" / "site-packages"
        for name, version in PAKETE.items():
            (self.site / f"{name}-{version}.dist-info").mkdir(parents=True)
        self.aussteh = Path(tmp.name) / "update_ausstehend.txt"
        for p in (mock.patch.object(V, "site_packages", lambda: self.site),
                  mock.patch.object(V, "AUSSTEHEND", self.aussteh),
                  mock.patch.object(V, "_torch_eintrag", lambda inst: None),
                  mock.patch.object(V, "_luecken_holen", lambda inst: {}),        # nie ins Netz
                  mock.patch.object(V, "_veraltet", lambda: []),
                  mock.patch.object(V, "_dev_wuensche", lambda: []),
                  mock.patch.object(wizard, "requirements_ohne_torch",
                                    lambda: ["rich>=13", "httpx>=0.27", "anthropic", "tokenizers>=0.20",
                                             "opencv-python<5", "pywin32; sys_platform == 'linux'"])):
            p.start()
            self.addCleanup(p.stop)


class PruefenTests(_Basis):
    def test_noetig_und_updates(self):
        veraltet = [{"name": "rich", "version": "13.0.0", "latest_version": "14.1.0"},
                    {"name": "opencv-python", "version": "4.10.0.84", "latest_version": "5.0.0"},  # nicht erlaubt
                    {"name": "fremd", "version": "1", "latest_version": "2"},                    # nicht von NemiCLI
                    {"name": "torch", "version": "2.14.0", "latest_version": "2.15.0"}]          # nie hochziehen
        liste = V.pruefen(veraltet=lambda: veraltet)
        self.assertEqual([(e.name, e.grund) for e in liste],
                         [("anthropic", "fehlt"), ("httpx", "passt nicht"), ("rich", "update")])
        self.assertEqual(liste[2].installieren_als, "rich==14.1.0")
        self.assertTrue(liste[1].noetig and not liste[2].noetig)

    def test_venv_ohne_pip_bekommt_pip(self):
        (self.site / "pip-26.0.dist-info").rmdir()
        e = {e.name: e for e in V.pruefen(veraltet=lambda: [])}["pip"]
        self.assertEqual(("fehlt", "pip"), (e.grund, e.installieren_als))
        self.assertTrue(e.noetig and e.machbar and not e.entfernen)

    def test_aktuell(self):
        for d in ("httpx-0.20.0", ):
            (self.site / f"{d}.dist-info").rename(self.site / "httpx-0.28.0.dist-info")
        (self.site / "anthropic-1.0.0.dist-info").mkdir()
        self.assertEqual(V.pruefen(veraltet=lambda: []), [])


class LueckenTests(_Basis):
    def luecke(self, kennung, behoben="", schadcode=False):
        import luecken
        return luecken.Luecke(id=kennung, cve=kennung, schwere="hoch", text="t", behoben=behoben, schadcode=schadcode)

    def test_behebbar_offen_schadcode(self):
        funde = {"rich": [self.luecke("CVE-1", "13.5.0")],                 # erlaubt (>=13)
                 "opencv-python": [self.luecke("CVE-2", "5.1.0")],          # NemiCLI erlaubt <5
                 "urllib3": [self.luecke("CVE-3", "2.6.0")],                # nicht von NemiCLI
                 "tokenizers": [self.luecke("CVE-4")],                      # keine Behebung
                 "boese": [self.luecke("MAL-1", schadcode=True)]}
        liste = {e.name: e for e in V.pruefen(veraltet=lambda: [], luecken_holen=lambda inst: funde)}
        self.assertEqual((liste["rich"].grund, liste["rich"].installieren_als), ("luecke", "rich>=13,>=13.5.0"))
        self.assertEqual(liste["urllib3"].installieren_als, "urllib3>=2.6.0")
        self.assertEqual(liste["opencv-python"].grund, "luecke-offen")
        self.assertIn("erst ab 5.1.0", liste["opencv-python"].hinweis)
        self.assertFalse(liste["tokenizers"].machbar)
        self.assertEqual(liste["boese"].grund, "schadcode")
        self.assertEqual(list(liste)[0], "boese")                            # Schadpakete zuerst

    def test_update_schliesst_luecke(self):
        funde = {"rich": [self.luecke("CVE-1", "14.0.0")]}
        veraltet = [{"name": "rich", "version": "13.0.0", "latest_version": "14.1.0"}]
        e = {e.name: e for e in V.pruefen(veraltet=lambda: veraltet, luecken_holen=lambda inst: funde)}["rich"]
        self.assertEqual((e.name, e.grund, e.installieren_als), ("rich", "luecke", "rich==14.1.0"))
        self.assertTrue(e.noetig)

    def test_osv_nicht_erreichbar(self):
        def kaputt(inst):
            raise OSError("OSV nicht erreichbar")
        liste = V.pruefen(veraltet=lambda: [], luecken_holen=kaputt)
        self.assertEqual(V.STAND["luecken"], "OSV nicht erreichbar")
        self.assertEqual([e.grund for e in liste], ["fehlt", "passt nicht"])


class GebrauchTests(_Basis):
    def test_geladene_pyd(self):
        d = self.site / "tokenizers-0.22.2.dist-info"
        (d / "RECORD").write_text("tokenizers/__init__.py,,\ntokenizers/tokenizers.pyd,,\n", encoding="utf-8")
        modul = types.ModuleType("tok_test")
        modul.__file__ = str(self.site / "tokenizers" / "tokenizers.pyd")
        with mock.patch.dict(sys.modules, {"tok_test": modul}):
            self.assertTrue(V.in_gebrauch("tokenizers", self.site))
        self.assertFalse(V.in_gebrauch("tokenizers", self.site))


class EinspielenTests(_Basis):
    def lauf(self, scheitert=()):
        aufrufe = []

        def ausfuehren(cmd, timeout):
            aufrufe.append(cmd)
            ziele = [a for a in cmd if "==" in a]
            code = 1 if any(z.split("==")[0] in scheitert for z in ziele) else 0
            return subprocess.CompletedProcess(cmd, code, "", "ERROR: conflict for " + ",".join(scheitert))
        p = mock.patch.object(V, "_ausfuehren", ausfuehren)
        p.start()
        self.addCleanup(p.stop)
        return aufrufe

    def eintraege(self):
        return [V.Eintrag("rich", "13.0.0", "14.1.0", "rich>=13", "update"),
                V.Eintrag("tokenizers", "0.22.2", "0.23.2", "tokenizers>=0.20", "update")]

    def test_zusammen_mit_basis(self):
        aufrufe = self.lauf()
        res = V.einspielen(self.eintraege())
        self.assertEqual(res["jetzt"], ["rich", "tokenizers"])
        self.assertEqual(len(aufrufe), 1)
        cmd = aufrufe[0]
        self.assertIn("rich==14.1.0", cmd)
        self.assertIn("httpx>=0.27", cmd)                      # die anderen Angaben prüfen mit
        self.assertNotIn("rich>=13", cmd)

    def test_was_nicht_passt_bleibt_stehen(self):
        self.lauf(scheitert=("tokenizers",))
        res = V.einspielen(self.eintraege())
        self.assertEqual(res["jetzt"], ["rich"])
        self.assertEqual([n for n, _ in res["abgelehnt"]], ["tokenizers"])
        self.assertIn("conflict", res["abgelehnt"][0][1])

    def test_in_gebrauch_spaeter_geprueft(self):
        aufrufe = self.lauf()
        with mock.patch.object(V, "in_gebrauch", lambda name, site: name == "tokenizers"):
            res = V.einspielen(self.eintraege())
        self.assertEqual((res["jetzt"], res["spaeter"]), (["rich"], ["tokenizers"]))
        self.assertIn("--dry-run", aufrufe[1])
        zeilen = self.aussteh.read_text(encoding="utf-8").split()
        self.assertIn("tokenizers==0.23.2", zeilen)
        self.assertIn("httpx>=0.27", zeilen)

    def test_funktionspruefung_holt_alte_version(self):
        aufrufe = self.lauf()
        geladen = {"rich": iter(["", "ImportError: kaputt"]), "tokenizers": iter(["", ""])}
        with mock.patch.object(V, "_laedt", lambda name, site: next(geladen[name])):
            res = V.einspielen(self.eintraege())
        self.assertEqual(res["jetzt"], ["tokenizers"])
        self.assertEqual(res["zurueck"], [("rich", "ImportError: kaputt")])
        self.assertIn("rich==13.0.0", aufrufe[-1])

    def test_schon_vorher_kaputt_bleibt(self):
        self.lauf()
        with mock.patch.object(V, "_laedt", lambda name, site: "ImportError: war schon so"):
            res = V.einspielen(self.eintraege())
        self.assertEqual(res["zurueck"], [])

    def test_schadpaket_entfernen_und_offenes_auslassen(self):
        aufrufe = self.lauf()
        weg = V.Eintrag("boese", "1.0", "", "boese", "schadcode")
        offen = V.Eintrag("tokenizers", "0.22.2", "", "", "luecke-offen")
        with mock.patch.object(V, "_laedt", lambda name, site: ""):
            res = V.einspielen([weg, offen])
        self.assertEqual(res["entfernt"], ["boese"])
        self.assertTrue(any("uninstall" in c and "boese" in c for c in aufrufe))
        self.assertFalse(any("tokenizers" in " ".join(c) for c in aufrufe))

    def test_ausstehende_beim_start(self):
        aufrufe = self.lauf()
        self.aussteh.write_text("tokenizers==0.23.2\n", encoding="utf-8")
        meldungen = []
        with mock.patch.object(V.uvsetup, "venv_python", lambda: self.site / "python.exe"):
            V.ausstehende_einspielen(meldungen.append)
        self.assertFalse(self.aussteh.exists())
        self.assertIn(str(self.aussteh), aufrufe[0])
        self.assertEqual(len(meldungen), 2)


if __name__ == "__main__":
    unittest.main()
