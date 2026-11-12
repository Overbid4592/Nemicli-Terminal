"""Modell-Datenbank (ggufengine/daten/*.json): Form, Prüfung, Erkennung und Abgleich mit dem Code."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "engines", ROOT / "core", ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    import torch  # noqa: F401
    from ggufengine import ausfuellen as A
except ImportError:                                   # ggufengine braucht torch
    A = None


@unittest.skipIf(A is None, "torch fehlt")
class Datenbank(unittest.TestCase):
    def tearDown(self):
        A.lesen.cache_clear()
        A.bauarten.cache_clear()

    def test_alle_dateien_lesbar(self):
        dateien = sorted(A.ORDNER.glob("*.json"))
        self.assertEqual({d.stem for d in dateien},
                         {"bauarten", "chatformate", "hardware", "sampling", "werkzeugformate"})
        for d in dateien:
            self.assertIsInstance(A.lesen(d.stem)["version"], int, d.name)
        self.assertTrue(A.bauarten())

    def test_falsche_form_wird_abgelehnt(self):
        with mock.patch.object(Path, "read_text", return_value="[1, 2]"):
            with self.assertRaises(A.DbFehler):
                A.lesen("bauarten")
        A.lesen.cache_clear()
        with mock.patch.object(Path, "read_text", return_value="{kaputt"):
            with self.assertRaises(A.DbFehler):
                A.lesen("bauarten")
        A.lesen.cache_clear()
        with self.assertRaises(A.DbFehler):
            A.lesen("gibt_es_nicht")

    def _bauarten_mit(self, eintrag):
        A.bauarten.cache_clear()
        daten = {"version": 1, "bauarten": {"test": eintrag}}
        with mock.patch.object(A, "lesen", lambda name: daten):
            return A.bauarten()

    def test_bauart_merkmale_werden_geprueft(self):
        self.assertEqual(self._bauarten_mit({"klasse": "baukasten", "fenster_basis": 10000})["test"]["klasse"],
                         "baukasten")
        for falsch in ({}, {"klasse": 1}, {"klasse": "baukasten", "unbekannt": True},
                       {"klasse": "baukasten", "parallel": 1}, {"klasse": "baukasten", "fenster_muster": True},
                       {"klasse": "baukasten", "fenster_muster": 0}, {"klasse": "baukasten", "aktivierung": "relu"},
                       {"klasse": "baukasten", "fenster_basis": "10000"}):
            with self.subTest(falsch), self.assertRaises(A.DbFehler):
                self._bauarten_mit(falsch)

    def test_klassen_und_bauarten_stimmen_ueberein(self):
        from ggufengine import models as M
        self.assertEqual(set(M.KLASSEN.values()), set(M._MODELS))
        for arch, e in A.bauarten().items():
            with self.subTest(arch):
                self.assertIn(arch, M.KLASSEN[e["klasse"]].arch_names)
        for cls in M._MODELS:
            for arch in cls.arch_names:
                self.assertIs(M.KLASSEN[A.bauart(arch)["klasse"]], cls, arch)
        self.assertIsNone(A.bauart("gibt_es_nicht"))

    def test_build_model_lehnt_unbekannte_bauart_ab(self):
        from ggufengine import models as M
        gg = mock.Mock(architecture="gibt_es_nicht")
        with self.assertRaises(A.GGUFError):
            M.build_model(gg, None, None, 16)

    def test_erkennung_reihenfolge_und_regeln(self):
        liste = [{"name": "a", "wenn": [{"alle": ["x", "y"], "keins": ["z"]}]},
                 {"name": "b", "wenn": [{"eins": ["x", "q"]}, {"bauart": ["foo"]}]}]
        self.assertEqual(A.erkennen(liste, "x y")["name"], "a")
        self.assertEqual(A.erkennen(liste, "x y z")["name"], "b")
        self.assertEqual(A.erkennen(liste, "q")["name"], "b")
        self.assertEqual(A.erkennen(liste, "", "foo")["name"], "b")
        self.assertIsNone(A.erkennen(liste, "nichts"))
        self.assertIsNone(A.erkennen([{"name": "leer", "wenn": [{}]}], "egal"))

    def test_chatformat_reihenfolge(self):
        self.assertEqual(A.chatformat("", "phi3"), "phi3")          # nach Bauart
        self.assertEqual(A.chatformat("", "qwen2"), "chatml")       # Präfix
        self.assertEqual(A.chatformat("", "unbekannt"), A.lesen("chatformate")["standard"])
        self.assertEqual(A.chatformat("<|start_header_id|>", "unbekannt"), "llama3")

    def test_werkzeugformate_eindeutig(self):
        namen = [f["name"] for f in A.werkzeugformate()]
        self.assertEqual(len(namen), len(set(namen)))
        with self.assertRaises(A.DbFehler):
            A.werkzeugformat("gibt_es_nicht")


@unittest.skipIf(A is None, "torch fehlt")
class Sampling(unittest.TestCase):
    """Sampling-Werte: Datenbank, Modelldatei, eigene Einstellung."""

    def tearDown(self):
        A.lesen.cache_clear()
        A.sampling_tabelle.cache_clear()

    def _empf(self, meta=None, fmt="chatml", arch="", denken=False, eigene=None):
        from ggufengine.sampling import empfohlen
        return empfohlen(meta or {}, fmt, arch, denken, eigene, dry_multiplier=2.5)

    def test_tabelle_gueltig_und_familien_bauarten_bekannt(self):
        d = A.sampling_tabelle()
        namen = [f["name"] for f in d["familien"]]
        self.assertEqual(len(namen), len(set(namen)))
        for f in d["familien"]:
            for regel in f["wenn"]:
                for arch in regel.get("bauart", ()):
                    self.assertIsNotNone(A.bauart(arch), f"{f['name']}: {arch}")

    def test_falsche_werte_werden_abgelehnt(self):
        for falsch in ({"temperature": "1"}, {"top_p": 1.5}, {"top_k": 2.5}, {"unbekannt": 1},
                       {"min_p": True}):
            daten = {"version": 1, "standard": falsch, "familien": []}
            A.sampling_tabelle.cache_clear()
            with self.subTest(falsch), mock.patch.object(A, "lesen", lambda name: daten), \
                    self.assertRaises(A.DbFehler):
                A.sampling_tabelle()

    def test_familien_und_denken(self):
        c = self._empf(arch="gemma4", fmt="gemma4")
        self.assertEqual((c.temperature, c.top_p, c.top_k), (1.0, 0.95, 64))
        self.assertEqual(c.min_p, 0.05)                          # aus 'standard'
        self.assertEqual(c.dry_multiplier, 2.5)
        c = self._empf(arch="qwen35", denken=True)
        self.assertEqual((c.temperature, c.top_p, c.top_k), (1.0, 0.95, 20))
        c = self._empf(arch="qwen35", denken=False)
        self.assertEqual((c.temperature, c.top_p, c.top_k), (0.7, 0.8, 20))
        self.assertEqual(self._empf(arch="mistral3", fmt="mistral-v7-tekken").temperature, 0.15)
        self.assertEqual(self._empf(arch="llama", fmt="llama3").temperature, 0.6)
        self.assertEqual(self._empf(fmt="deepseek", arch="qwen2").temperature, 0.6)
        c = self._empf(arch="unbekannt")
        self.assertEqual((c.temperature, c.top_k, c.quelle), (0.8, 40, "standard"))

    def test_datei_geht_vor_und_wird_gerundet(self):
        meta = {"general.sampling.temp": 0.30000001192092896, "general.sampling.top_k": 50,
                "general.sampling.top_p": 2.0, "general.sampling.sequence": "kpt"}
        c = self._empf(meta, arch="unbekannt")
        self.assertEqual((c.temperature, c.top_k, c.top_p), (0.3, 50, 0.95))   # top_p ungültig: Rückfall
        self.assertIn("Datei", c.quelle)

    def test_ohne_denken_geht_familie_der_datei_vor(self):
        meta = {"general.sampling.temp": 0.6, "general.sampling.top_p": 0.95, "general.sampling.min_p": 0.0}
        c = self._empf(meta, arch="qwen3", denken=False)
        self.assertEqual((c.temperature, c.top_p, c.min_p), (0.7, 0.8, 0.0))   # min_p kennt die Familie nicht
        c = self._empf(meta, arch="qwen3", denken=True)
        self.assertEqual((c.temperature, c.top_p), (0.6, 0.95))

    def test_eigene_einstellung_geht_allem_vor(self):
        meta = {"general.sampling.temp": 0.3}
        c = self._empf(meta, arch="gemma4", eigene={"temperature": 0.5, "top_k": "viel", "seed": 3})
        self.assertEqual((c.temperature, c.top_k, c.seed), (0.5, 64, None))
        self.assertTrue(c.quelle.endswith("Einstellung"))
        self.assertEqual(self._empf(arch="gemma4", eigene="kaputt").temperature, 1.0)


class OhneTorch(unittest.TestCase):
    """hfladen und systemprofil lesen die Datenbank direkt (ohne torch)."""

    def test_hfladen_bauarten_und_moe(self):
        import hfladen as H
        self.assertIn("llama", H.unterstuetzt())
        self.assertTrue(H.ist_moe("deepseek2", "a/Modell-GGUF")[0])
        self.assertFalse(H.ist_moe("llama", "a/Modell-GGUF")[0])

    def test_hardware_generation_und_torch_bau(self):
        import systemprofil as P
        import syscheck as C
        daten = json.loads((ROOT / "ggufengine" / "daten" / "hardware.json").read_text(encoding="utf-8"))
        self.assertEqual(P.nvidia(), daten["nvidia"])
        self.assertEqual(P.Grafikkarte("k", "nvidia", cuda_cap="8.9").generation, "Ada")
        self.assertEqual(P.Grafikkarte("k", "nvidia", cuda_cap="8.6").generation, "Ampere")
        self.assertEqual(C._wheel_for("12.0")[0], "cu132")
        self.assertEqual(C._wheel_for("6.1")[0], "cpu")
        self.assertEqual(C._cuda_fuer("cu132"), 13.2)

    def test_hardware_fehlt_sicherer_rueckfall(self):
        import systemprofil as P
        import syscheck as C
        with mock.patch.object(P, "hardware", lambda: {}):
            self.assertEqual(P.Grafikkarte("k", "nvidia", cuda_cap="8.9").generation, "")
            self.assertEqual(C._wheel_for("12.0")[0], "cu126")
            self.assertEqual(C._cuda_fuer("cu132"), 12.6)
            self.assertIn("hardware.json", C.torch_plan({"vendor": "nvidia"}, "12.0")["warnung"])
        self.assertEqual(C.torch_plan({"vendor": "nvidia"}, "12.0")["warnung"], "")

    def test_bauarten_fehlen_klare_meldung(self):
        import hfladen as H
        with mock.patch.object(H, "_bauarten", dict), self.assertRaisesRegex(OSError, "Modell-Datenbank"):
            H.katalog(lambda *a, **k: [], neu=True)


if __name__ == "__main__":
    unittest.main()
