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

_TORCH_UPDATE = V._torch_update                        # die echte Prüfung; _Basis ersetzt sie (kein Netz)

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
                  mock.patch.object(V, "TORCH_AUSSTEHEND", Path(tmp.name) / "torch_ausstehend.json"),
                  mock.patch.object(V, "_torch_eintrag", lambda inst: None),
                  mock.patch.object(V, "_torch_update", lambda inst: None),            # nie ins Netz
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


def _seite(kanal: str, *versionen: str, paket: str = "torch") -> str:
    """Index-Seite des PyTorch-Kanals mit Rädern für dieses Python, dazu Fremdes, das nicht zählt."""
    tag = "cp" + V.uvsetup.PYVER.replace(".", "")
    links = [f'<a href="/whl/{kanal}/{paket}-{v}%2B{kanal}-{tag}-{tag}-win_amd64.whl">x</a>' for v in versionen]
    links += [f'<a href="torch-9.9.0%2B{kanal}-{tag}-{tag}t-win_amd64.whl">frei-fädig</a>',
              f'<a href="torch-9.9.0%2B{kanal}-{tag}-{tag}-manylinux_2_28_x86_64.whl">linux</a>',
              f'<a href="torch-9.9.0%2B{kanal}-cp39-cp39-win_amd64.whl">altes Python</a>',
              f'<a href="torch-9.9.0rc1%2B{kanal}-{tag}-{tag}-win_amd64.whl">Vorabfassung</a>']
    return "\n".join(links)


class TorchUpdateTests(_Basis):
    """torch über /update: neuere Version oder höherer CUDA-Bau vom PyTorch-Index, mit Rückweg."""

    def setUp(self):
        super().setUp()
        import syscheck
        for p in (mock.patch.object(syscheck, "_wheel_for", lambda cap: ("cu132", "Blackwell")),
                  mock.patch.object(syscheck, "compute_cap", lambda: "12.0")):
            p.start()
            self.addCleanup(p.stop)
        self.nvidia = {"vendor": "nvidia", "cuda_max": 13.5}

    def holen(self, seiten: dict):
        abgerufen = []

        def get(url):
            abgerufen.append(url)
            kanal, paket = url.split("/whl/")[1].split("/")[:2]
            return seiten.get((kanal, paket), seiten.get(kanal, "") if paket == "torch" else "")
        return get, abgerufen

    def test_kanaele_bis_zur_treiber_grenze(self):
        self.assertEqual(V.torch_kanaele("cu132", 13.5), ["cu135", "cu134", "cu133", "cu132"])
        self.assertEqual(V.torch_kanaele("cu126", 13.0), ["cu130", "cu129", "cu128", "cu127", "cu126"])
        self.assertEqual(V.torch_kanaele("cu132", 12.8), ["cu132"])          # Treiber zu alt: nur der Mindest-Bau
        self.assertEqual(V.kanal_cuda("cu134"), 13.4)
        self.assertEqual(V.kanal_cuda("cpu"), 0.0)

    def test_fehlender_kanal_antwortet_403(self):
        """download.pytorch.org meldet einen Kanal, den es nicht gibt, mit 403 – das ist „kein Rad“, kein Fehler."""
        import netzhilfe
        for code in (403, 404):
            antwort = types.SimpleNamespace(status_code=code)
            with mock.patch.object(netzhilfe, "abrufen", lambda *a, **k: antwort):
                self.assertEqual(V._torch_get("https://download.pytorch.org/whl/cu999/torch/"), "")

    def test_nur_raeder_fuer_dieses_python_unter_windows(self):
        get, _ = self.holen({"cu134": _seite("cu134", "2.14.1", "2.15.0")})
        self.assertEqual(V.torch_versionen("cu134", get=get), ["2.14.1", "2.15.0"])
        seite = ('<a href="torchvision-0.29.2%2Bcu134-py3-none-win_amd64.whl">x</a>'      # so heißt torchvision dort
                 '<a href="torchvision-0.29.1%2Bcu134-py3-none-manylinux_2_28_x86_64.whl">x</a>')
        self.assertEqual(V.torch_versionen("cu134", "torchvision", get=lambda url: seite), ["0.29.2"])

    def test_hoeherer_cuda_bau_gleicher_version(self):
        get, abgerufen = self.holen({"cu134": _seite("cu134", "2.15.0"), "cu132": _seite("cu132", "2.14.0", "2.15.0"),
                                     ("cu134", "torchvision"): _seite("cu134", "0.29.1", "0.29.2", paket="torchvision")})
        e = _TORCH_UPDATE({"torch": "2.15.0+cu132", "torchvision": "0.29.2+cu132"}, get=get, gpu=self.nvidia)
        self.assertEqual((e.neu, e.spez, e.grund), ("2.15.0+cu134", "torch==2.15.0+cu134", "torch-update"))
        self.assertEqual(e.index, "https://download.pytorch.org/whl/cu134")
        self.assertEqual(e.dazu, ["torchvision==0.29.2+cu134"])            # gleicher CUDA-Bau, nicht der alte
        self.assertTrue(e.machbar)
        self.assertFalse(e.noetig)                                          # freiwillig, nie „Nötiges“
        self.assertIn("CUDA 13.2 → 13.4", e.hinweis)
        self.assertTrue(all(u.startswith("https://download.pytorch.org/whl/") for u in abgerufen))

    def test_neuere_version_schlaegt_hoeheren_bau(self):
        get, _ = self.holen({"cu134": _seite("cu134", "2.15.0"), "cu132": _seite("cu132", "2.16.0")})
        e = _TORCH_UPDATE({"torch": "2.15.0+cu134"}, get=get, gpu=self.nvidia)
        self.assertEqual(e.neu, "2.16.0+cu132")

    def test_schon_aktuell_cpu_bau_oder_ohne_nvidia(self):
        get, abgerufen = self.holen({"cu134": _seite("cu134", "2.15.0")})
        self.assertIsNone(_TORCH_UPDATE({"torch": "2.15.0+cu134"}, get=get, gpu=self.nvidia))
        self.assertIsNone(_TORCH_UPDATE({"torch": "2.15.0+cu135"}, get=get, gpu=self.nvidia))   # nie zurück
        n = len(abgerufen)
        self.assertIsNone(_TORCH_UPDATE({"torch": "2.15.0"}, get=get, gpu=self.nvidia))         # CPU-Bau
        self.assertIsNone(_TORCH_UPDATE({"torch": "2.15.0+cu132"}, get=get, gpu={"vendor": "amd"}))
        self.assertIsNone(_TORCH_UPDATE({}, get=get, gpu=self.nvidia))
        self.assertEqual(len(abgerufen), n)                                 # dafür kein Netz

    def test_index_nicht_erreichbar_bricht_pruefen_nicht(self):
        def kaputt(inst):
            raise OSError("keine Verbindung")
        liste = V.pruefen(veraltet=lambda: [], torch_neu=kaputt)
        self.assertEqual(V.STAND["torch"], "keine Verbindung")
        self.assertIsInstance(liste, list)

    def test_geladene_dlls_sperren(self):
        lib = self.site / "torch" / "lib"
        lib.mkdir(parents=True)
        (lib / "c10.dll").write_bytes(b"x")
        self.assertTrue(V.torch_frei(self.site))
        with mock.patch.object(V.os, "open", side_effect=PermissionError):
            self.assertFalse(V.torch_frei(self.site))

    def plan(self):
        return {"pakete": ["torch==2.15.0+cu134", "torchvision"], "index": "https://download.pytorch.org/whl/cu134",
                "alt": ["torch==2.15.0+cu132", "torchvision==0.29.2+cu132"],
                "alt_index": "https://download.pytorch.org/whl/cu132", "dazu": ["torchvision"]}

    def test_tausch_gelingt(self):
        aufrufe = []
        with mock.patch.object(V, "_ausfuehren",
                               lambda cmd, t: aufrufe.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")):
            ok, text = V.torch_tauschen(self.plan(), {"laedt": True, "cuda": True, "fp4": True, "rechnet": True},
                                        kann=lambda dazu: {"laedt": True, "cuda": True, "fp4": True, "rechnet": True})
        self.assertTrue(ok)
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(aufrufe[0][-2:], ["--index-url", "https://download.pytorch.org/whl/cu134"])
        self.assertIn("torch==2.15.0+cu134", aufrufe[0])

    def test_tausch_verliert_fp4_alte_fassung_zurueck(self):
        aufrufe = []
        ergebnisse = iter([{"laedt": True, "cuda": True, "rechnet": True},                   # nach dem Tausch
                           {"laedt": True, "cuda": True, "fp4": True, "rechnet": True}])     # nach dem Rückweg
        with mock.patch.object(V, "_ausfuehren",
                               lambda cmd, t: aufrufe.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")):
            ok, text = V.torch_tauschen(self.plan(), {"laedt": True, "cuda": True, "fp4": True, "rechnet": True},
                                        kann=lambda dazu: next(ergebnisse))
        self.assertFalse(ok)
        self.assertIn("fp4", text)
        self.assertIn("alte läuft wieder", text)
        self.assertIn("torch==2.15.0+cu132", aufrufe[1])
        self.assertEqual(aufrufe[1][-1], "https://download.pytorch.org/whl/cu132")

    def eintrag(self):
        return V.Eintrag("torch", "2.15.0+cu132", "2.15.0+cu134", "torch==2.15.0+cu134", "torch-update",
                         index="https://download.pytorch.org/whl/cu134", dazu=["torchvision==0.29.2+cu134"])

    def test_einspielen_sofort_wenn_frei(self):
        (self.site / "torchvision-0.29.2+cu132.dist-info").mkdir()
        gerufen = []
        with mock.patch.object(V, "in_gebrauch", lambda name, site: False), \
                mock.patch.object(V, "torch_frei", lambda site: True), \
                mock.patch.object(V, "torch_tauschen", lambda plan, **kw: gerufen.append(plan) or (True, "läuft")):
            res = V.einspielen([self.eintrag()])
        self.assertTrue(res["torch"]["jetzt"])
        self.assertEqual(gerufen[0]["pakete"], ["torch==2.15.0+cu134", "torchvision==0.29.2+cu134"])
        self.assertEqual(gerufen[0]["alt"], ["torch==2.15.0+cu132", "torchvision==0.29.2+cu132"])
        self.assertEqual(gerufen[0]["dazu"], ["torchvision"])
        self.assertFalse(V.TORCH_AUSSTEHEND.exists())

    def test_einspielen_geladen_wird_vorgemerkt(self):
        with mock.patch.object(V, "in_gebrauch", lambda name, site: True), \
                mock.patch.object(V, "torch_kann", lambda dazu=(): {"laedt": True, "fp4": True}), \
                mock.patch.object(V, "torch_tauschen", side_effect=AssertionError("nicht im laufenden Betrieb")):
            res = V.einspielen([self.eintrag()])
        self.assertTrue(res["torch"]["start"])
        import json
        plan = json.loads(V.TORCH_AUSSTEHEND.read_text(encoding="utf-8"))
        self.assertEqual(plan["pakete"][0], "torch==2.15.0+cu134")
        self.assertEqual(plan["vorher"], {"laedt": True, "fp4": True})

    def test_beim_start_nur_wenn_kein_prozess_torch_hat(self):
        import json
        V.TORCH_AUSSTEHEND.write_text(json.dumps(self.plan()), encoding="utf-8")
        meldungen = []
        with mock.patch.object(V.uvsetup, "venv_python", lambda: self.site / "python.exe"), \
                mock.patch.object(V, "torch_frei", lambda site: False), \
                mock.patch.object(V, "torch_tauschen", side_effect=AssertionError("Wache hat torch")):
            V.ausstehende_einspielen(meldungen.append)
        self.assertTrue(V.TORCH_AUSSTEHEND.exists())                       # nächster Start versucht es wieder
        self.assertIn("Wache", meldungen[0])
        with mock.patch.object(V.uvsetup, "venv_python", lambda: self.site / "python.exe"), \
                mock.patch.object(V, "torch_frei", lambda site: True), \
                mock.patch.object(V, "torch_tauschen", lambda plan, vorher, melden: (True, "läuft")):
            V.ausstehende_einspielen(meldungen.append)
        self.assertFalse(V.TORCH_AUSSTEHEND.exists())
        self.assertIn("läuft", meldungen[-1])

    def test_fremder_index_in_der_vormerkung_wird_verworfen(self):
        import json
        plan = dict(self.plan(), index="https://example.com/simple")
        V.TORCH_AUSSTEHEND.write_text(json.dumps(plan), encoding="utf-8")
        meldungen = []
        with mock.patch.object(V.uvsetup, "venv_python", lambda: self.site / "python.exe"), \
                mock.patch.object(V, "torch_tauschen", side_effect=AssertionError("nie installieren")):
            V.ausstehende_einspielen(meldungen.append)
        self.assertFalse(V.TORCH_AUSSTEHEND.exists())
        self.assertIn("verworfen", meldungen[0])


if __name__ == "__main__":
    unittest.main()
