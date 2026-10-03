"""Tests für engines/gguflokal.py ohne Modell und ohne GPU.

python -m unittest tests.test_gguflokal
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import gguflokal as G                                  # noqa: E402


class DenktrennerTests(unittest.TestCase):

    def _alles(self, stuecke):
        t = G._Denktrenner()
        raus = []
        for s in stuecke:
            raus += t(s)
        raus += t.ende()
        denk = "".join(x for a, x in raus if a == "thinking")
        text = "".join(x for a, x in raus if a == "text")
        return denk, text

    def test_denkkanal_wird_abgetrennt(self):
        d, t = self._alles(["<|channel>thought\nIch überlege.<channel|>Hallo!"])
        self.assertEqual(d, "Ich überlege.")
        self.assertEqual(t, "Hallo!")

    def test_marken_ueber_stuecke_verteilt(self):
        d, t = self._alles(["<|chan", "nel>tho", "ught\nA", "B<chan", "nel|>Antw", "ort"])
        self.assertEqual(d, "AB")
        self.assertEqual(t, "Antwort")

    def test_ohne_denken(self):
        self.assertEqual(self._alles(["Nur ", "Text <b>"]), ("", "Nur Text <b>"))

    def test_k2_denkblock_schon_offen(self):
        t = G._Denktrenner("k2", offen=True)
        raus = t("Erst rechnen.\n</ifm|think>\n16 Beine") + t.ende()
        self.assertEqual("".join(x for a, x in raus if a == "thinking").strip(), "Erst rechnen.")
        self.assertEqual("".join(x for a, x in raus if a == "text").strip(), "16 Beine")

    def test_harmony_analyse_dann_final(self):
        t = G._Denktrenner("harmony", offen=True)
        raus = []
        for s in ["Erst rech", "nen.<|end|><|start|>assis", "tant<|channel|>final<|message|>16 Beine"]:
            raus += t(s)
        raus += t.ende()
        self.assertEqual("".join(x for a, x in raus if a == "thinking"), "Erst rechnen.")
        self.assertEqual("".join(x for a, x in raus if a == "text"), "16 Beine")

    def test_think_marken(self):
        t = G._Denktrenner("deepseek")
        raus = []
        for s in ["<thi", "nk>\nErst rechnen.</th", "ink>\n\nDas Ergebnis ist 4."]:
            raus += t(s)
        raus += t.ende()
        self.assertEqual("".join(x for a, x in raus if a == "thinking"), "Erst rechnen.")
        self.assertEqual("".join(x for a, x in raus if a == "text").strip(), "Das Ergebnis ist 4.")


class BlockStoppTests(unittest.TestCase):

    def test_erkennt_nur_fertige_werkzeug_bloecke(self):
        fertig = 'Ich schau nach.\n```aktion\n{"tool": "web_suche", "suche": "Wetter"}\n```'
        self.assertTrue(G.werkzeug_block_fertig(fertig))
        self.assertFalse(G.werkzeug_block_fertig(fertig[:-3]))                  # Zaun noch offen
        self.assertFalse(G.werkzeug_block_fertig("```python\nprint(1)\n```"))   # Code, kein Werkzeug
        self.assertTrue(G.werkzeug_block_fertig('```json\n{"tool": "x"}\n```'))

    def test_erzeugung_endet_nach_dem_block(self):
        stuecke = ["Ich suche.\n", "```aktion\n", '{"tool": "web_suche", "suche": "Wetter"}', "\n```",
                   "\nMorgen wird es 18 °C!"]

        class Motor:
            chat_format = type("F", (), {"fmt": "chatml", "thinking": False})()
            def chat(self, *a, **k):
                yield from stuecke

        async def sammeln():
            c = G.GgufChat("Test")
            with patch.object(G, "denken", return_value=False):
                return [s async for s in c._erzeugen(Motor(), [], 100, G.threading.Event())]
        raus = "".join(asyncio.run(sammeln()))
        self.assertIn('"tool": "web_suche"', raus)
        self.assertNotIn("18 °C", raus)


class ModelleTests(unittest.TestCase):

    def test_ordner_je_modell_ohne_mmproj(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "Gemma4").mkdir()
            (t / "Gemma4" / "gemma.gguf").write_bytes(b"x")
            (t / "Gemma4" / "mmproj-BF16.gguf").write_bytes(b"x")
            (t / "NurVision").mkdir()
            (t / "NurVision" / "mmproj.gguf").write_bytes(b"x")
            (t / "Leer").mkdir()
            with patch.object(G, "ORDNER", t):
                m = G.modelle()
                self.assertEqual(list(m), ["Gemma4"])
                self.assertEqual(m["Gemma4"].name, "gemma.gguf")
                with patch.object(G, "sieht", return_value=True):
                    self.assertIn("sieht Bilder", G.beschreibung("Gemma4"))
                with patch.object(G, "sieht", return_value=False):
                    self.assertNotIn("sieht Bilder", G.beschreibung("Gemma4"))

    def test_einstellungen(self):
        with patch.object(G, "_config", return_value={}):
            self.assertEqual(G.kontext(), 131072)
            self.assertEqual(G.tok_s(), 30)
            self.assertFalse(G.denken())
        with patch.object(G, "_config", return_value={"gguf_tok_s": 0, "gguf_kontext": 32768}):
            self.assertIsNone(G.tok_s())
            self.assertEqual(G.kontext(), 32768)


class VerlaufTests(unittest.TestCase):

    def test_erzeugte_token_und_gesendeter_text(self):
        c = G.GgufChat("Gemma4")
        verlauf = [{"role": "user", "content": "Frage"}, {"role": "assistant", "content": "Antwort"},
                   {"role": "user", "content": "Neu"}]
        c._zusatz.von(verlauf[1])["ids"] = [5, 6, 7]
        c._zusatz.von(verlauf[0])["gesendet"] = "Treffer\n\nFrage"
        n = c._nachrichten("SYS", verlauf)
        self.assertEqual([m.role for m in n], ["system", "user", "assistant", "user"])
        self.assertEqual(n[1].content, "Treffer\n\nFrage")
        self.assertEqual(n[2].ids, [5, 6, 7])
        self.assertIsNone(n[3].ids)


class DenkenInDerWerkzeugKetteTests(unittest.TestCase):
    def test_denktext_bleibt_nur_in_der_laufenden_kette(self):
        """Vor der letzten echten Nachricht: Antwort als Text (Denktext weg); danach – nur
        Werkzeug-Ergebnisse – mit den erzeugten Token samt Denktext."""
        c = G.GgufChat("Gemma4")
        v = [{"role": "user", "content": "Alte Frage"},
             {"role": "assistant", "content": "Alte Antwort"},
             {"role": "user", "content": "Lies die Datei"},
             {"role": "assistant", "content": "```aktion ...```"},
             {"role": "user", "content": "Ergebnis von 'datei_lesen' (erfolgreich):\nInhalt"},
             {"role": "assistant", "content": "```aktion ...```"},
             {"role": "user", "content": "Ergebnis von 'inhalt_suchen' (erfolgreich):\nTreffer"}]
        for i, ids in ((1, [1, 2]), (3, [3, 4]), (5, [5, 6])):
            c._zusatz.von(v[i]).update(ids=ids, gedacht=True)
        n = c._nachrichten("", v, denk_weg=True)
        self.assertEqual([m.ids for m in n if m.role == "assistant"], [None, [3, 4], [5, 6]])
        n = c._nachrichten("", v + [{"role": "user", "content": "Danke, und jetzt?"}], denk_weg=True)
        self.assertEqual([m.ids for m in n if m.role == "assistant"], [None, None, None])
        n = c._nachrichten("", v, denk_weg=False)                 # Vorlage behält Denktext (Ling)
        self.assertEqual([m.ids for m in n if m.role == "assistant"], [[1, 2], [3, 4], [5, 6]])


class GedaechtnisTests(unittest.TestCase):

    def test_kurzes_ohne_frage_sucht_nicht(self):
        import indexdb
        with patch.object(indexdb, "guide_block", return_value="Treffer") as suche:
            self.assertEqual(G._gedaechtnis("Hi du da"), "")
            self.assertEqual(G._gedaechtnis("ok danke"), "")
            suche.assert_not_called()
            self.assertEqual(G._gedaechtnis("Wie geht's?"), "Treffer")                 # Frage: suchen
            self.assertEqual(G._gedaechtnis("Erkläre mir bitte die Wache"), "Treffer")

    def test_nachricht_ist_markiert(self):
        self.assertTrue(G.NACHRICHT_MARKE.startswith("# "))
        self.assertTrue(G.NACHRICHT_MARKE.endswith("\n"))


class GpuFuerBildTests(unittest.TestCase):

    def test_auslagern_nur_aussen(self):
        class Motor:
            def __init__(self):
                self.log = []
            def offload(self):
                self.log.append("raus")
            def restore(self):
                self.log.append("rein")
        m = Motor()
        with patch.object(G, "_motor", ("x", 1, m)):
            with G.gpu_fuer_bild():
                with G.gpu_fuer_bild():
                    self.assertEqual(m.log, ["raus"])
                self.assertEqual(m.log, ["raus"])
        self.assertEqual(m.log, ["raus", "rein"])

    def test_ohne_modell_wirkungslos(self):
        with patch.object(G, "_motor", None):
            with G.gpu_fuer_bild():
                pass

    def test_imagegen_nutzt_die_regel(self):
        import imagegen
        self.assertTrue(hasattr(imagegen.paint, "__wrapped__"))
        self.assertTrue(hasattr(imagegen.auto_nachbessern, "__wrapped__"))
        self.assertTrue(hasattr(imagegen.repaint_region, "__wrapped__"))


class BilderTests(unittest.TestCase):

    def test_bild_aus_data_uri(self):
        import base64, io
        from PIL import Image
        puffer = io.BytesIO()
        Image.new("RGB", (5, 3), (255, 0, 0)).save(puffer, "PNG")
        uri = "data:image/png;base64," + base64.b64encode(puffer.getvalue()).decode()
        self.assertEqual(G._bild_aus_uri(uri).size, (5, 3))
        for falsch in ("data:text/plain;base64,QUJD", "https://x/y.png", "data:image/png,roh"):
            with self.assertRaises(ValueError):
                G._bild_aus_uri(falsch)

    def test_bilder_haengen_an_der_nutzernachricht(self):
        c = G.GgufChat("Gemma4")
        verlauf = [{"role": "user", "content": "Was ist das?"}, {"role": "user", "content": "Und das?"}]
        c._zusatz.von(verlauf[0])["bilder"] = ["EMB"]
        n = c._nachrichten("", verlauf)
        self.assertEqual(n[0].images, ["EMB"])
        self.assertIsNone(n[1].images)

    def test_gleicher_text_verschiedene_bilder(self):
        """Zweimal nur ein Bild abgelegt: gleicher (leerer) Text, trotzdem je eigenes Bild."""
        c = G.GgufChat("Gemma4")
        verlauf = [{"role": "user", "content": ""}, {"role": "assistant", "content": "Ein Hund."},
                   {"role": "user", "content": ""}]
        c._zusatz.von(verlauf[0])["bilder"] = ["HUND"]
        c._zusatz.von(verlauf[2])["bilder"] = ["KATZE"]
        n = c._nachrichten("", verlauf)
        self.assertEqual((n[0].images, n[2].images), (["HUND"], ["KATZE"]))
        c._zusatz.aufraeumen(verlauf[2:])                  # erste Nachricht aus dem Verlauf gefallen
        self.assertIsNone(c._zusatz.get(verlauf[0], "bilder"))

    def test_sieht_nur_mit_mmproj(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for name, mm in (("MitAuge", True), ("Blind", False)):
                (t / name).mkdir()
                (t / name / "m.gguf").write_bytes(b"x")
                if mm:
                    (t / name / "mmproj-BF16.gguf").write_bytes(b"x")
            class Kopf:
                typ = "gemma4v"
                def __init__(self, pfad): pass
                schluessel = "clip.vision.projector_type"
                def get(self, k, d=None): return Kopf.typ if k == Kopf.schluessel else d
                def close(self): pass
            with patch.object(G, "ORDNER", t), patch("ggufengine.gguf.GGUFFile", Kopf), \
                    patch.object(G, "_mmproj_typ", {}):
                self.assertTrue(G.sieht("MitAuge"))
                self.assertFalse(G.sieht("Blind"))
                self.assertFalse(G.sieht("Fehlt"))
                Kopf.typ = "unbekannt"                        # fremder Bild-Encoder
                G._mmproj_typ.clear()
                self.assertFalse(G.sieht("MitAuge"))
                Kopf.typ, Kopf.schluessel = "qwen3vl_merger", "clip.projector_type"
                G._mmproj_typ.clear()
                self.assertTrue(G.sieht("MitAuge"))


class KontextTests(unittest.TestCase):

    def test_vram_automatik(self):
        from types import SimpleNamespace as NS
        datei = NS(stat=lambda: NS(st_size=6 * 2**30))
        with patch.object(G, "modelle", return_value={"K2": datei}), \
                patch.object(G, "kv_bytes_pro_token", return_value=147456), \
                patch.object(G, "gewichte_vram", return_value=None):
            frei = 14 * 2**30                                   # 14 - 6 - 1,5 = 6,5 GB für KV
            self.assertEqual(G.passender_kontext("K2", 32768, frei), 32768)    # 4,5 GB passt
            self.assertEqual(G.passender_kontext("K2", 65536, frei), 32768)    # 9 GB nicht
            self.assertEqual(G.passender_kontext("K2", 131072, 8 * 2**30), 4096)

    def test_gewichte_wie_die_engine_sie_ablegt(self):
        from types import SimpleNamespace as NS
        def t(typ, shape, n_bytes=0):
            n = 1
            for d in shape:
                n *= d
            return NS(ggml_type=typ, shape=shape, n_elements=n, n_bytes=n_bytes)
        gg = NS(tensors={
            "token_embd.weight": t(12, (1024, 1024), 589824),     # Blockformat bleibt
            "output.weight": t(14, (1024, 1024)),                 # Q6_K: 10 Bit/Wert
            "blk.0.ffn_up.weight": t(12, (1024, 1024)),           # Q4_K: 5 Bit/Wert
            "blk.0.attn_norm.weight": t(0, (1024,)),              # f32
            "per_layer_token_embd.weight": t(12, (1024, 1024)),   # im RAM
        })
        M = 1024 * 1024
        self.assertEqual(G._gewichte_bytes(gg), 589824 + M * 10 // 8 + M * 5 // 8 + 1024 * 4)
        del gg.tensors["output.weight"]                           # gebundener Kopf aus der Einbettung
        self.assertEqual(G._gewichte_bytes(gg), 589824 + M * 5 // 8 + M * 5 // 8 + 1024 * 4)

    def test_vram_automatik_rechnet_mit_engine_gewichten(self):
        from types import SimpleNamespace as NS
        datei = NS(stat=lambda: NS(st_size=5 * 2**30))
        with patch.object(G, "modelle", return_value={"K2": datei}), \
                patch.object(G, "kv_bytes_pro_token", return_value=163840), \
                patch.object(G, "gewichte_vram", return_value=6 * 2**30):
            frei = int(14.5 * 2**30)                              # 14,5 - 6 - 1,5 = 7 GB: 32k (5 GB) passt
            self.assertEqual(G.passender_kontext("K2", 65536, frei), 32768)
            self.assertEqual(G.passender_kontext("K2", 65536, 12 * 2**30), 16384)

    def test_experten_budget(self):
        from types import SimpleNamespace as NS
        GB = 2**30
        datei = NS(stat=lambda: NS(st_size=12 * GB))
        with patch.object(G, "modelle", return_value={"M": datei}),                 patch.object(G, "gewichte_vram", return_value=14 * GB),                 patch.object(G, "experten_vram", return_value=10 * GB),                 patch.object(G, "kv_bytes_pro_token", return_value=1000):
            with patch.object(G, "_config", return_value={}):
                self.assertIsNone(G.experten_budget("M", 8192, 20 * GB))           # passt ganz in den VRAM
                rest = 12 * GB - 4 * GB - 8192 * 1000 - G.RESERVE                 # übrige Gewichte, Kontext, Reserve
                self.assertEqual(G.experten_budget("M", 8192, 12 * GB), rest)
                self.assertEqual(G.experten_budget("M", 8192, 4 * GB), 0)          # nicht einmal der Rest passt
                # mit Budget zählen nur die Experten im VRAM: der Kontext wird wieder größer
                self.assertGreater(G.passender_kontext("M", 65536, 12 * GB, budget=0),
                                   G.passender_kontext("M", 65536, 12 * GB))
            with patch.object(G, "_config", return_value={"gguf_experten": "vram"}):
                self.assertIsNone(G.experten_budget("M", 8192, 4 * GB))
            with patch.object(G, "_config", return_value={"gguf_experten": "ram"}):
                self.assertEqual(G.experten_budget("M", 8192, 20 * GB), 0)

    def test_gespraechsspeicher_8_bit(self):
        with patch.object(G, "_kopf_werte", return_value=(131072, 163840, None)):
            with patch.object(G, "_config", return_value={}):
                self.assertEqual((G.kv_bits(), G.kv_bytes_pro_token("K2")), (16, 163840))
            with patch.object(G, "_config", return_value={"gguf_kv_8bit": True}):
                self.assertEqual((G.kv_bits(), G.kv_bytes_pro_token("K2")), (8, 87040))

    def test_grenze_des_modells_und_max(self):
        with patch.object(G, "kontext_max", return_value=65536):
            with patch.object(G, "_config", return_value={"gguf_kontext": 131072}):
                self.assertEqual(G.kontext("X"), 65536)          # nie mehr, als das Modell kann
            with patch.object(G, "_config", return_value={"gguf_kontext": 0}):
                self.assertEqual(G.kontext("X"), 65536)          # 0 = max
            with patch.object(G, "_config", return_value={"gguf_kontext": 16384}):
                self.assertEqual(G.kontext("X"), 16384)
            with patch.object(G, "_config", return_value={"gguf_kontext": 100}):
                self.assertEqual(G.kontext("X"), 2048)           # Untergrenze

    def test_kv_berechnung(self):
        from types import SimpleNamespace as NS
        def kopf(arch, werte):
            return NS(architecture=arch, get=lambda k, d=None: werte.get(k, d),
                      require=lambda k: werte[k])
        gemma = kopf("gemma4", {"gemma4.block_count": 6, "gemma4.attention.head_count_kv": 2,
                                "gemma4.attention.key_length": 512, "gemma4.attention.shared_kv_layers": 2,
                                "gemma4.attention.sliding_window_pattern": [True, False, True, False, True, False]})
        self.assertEqual(G._kv_bytes(gemma), 2 * 2 * 512 * 2 * 2)     # Schicht 1 und 3 (nicht 5: geteilt)
        gemma = kopf("gemma4", {"gemma4.block_count": 4, "gemma4.attention.head_count_kv": [8, 2, 8, 2],
                                "gemma4.attention.key_length": 512,
                                "gemma4.attention.sliding_window_pattern": [True, False, True, False]})
        self.assertEqual(G._kv_bytes(gemma), 2 * 2 * 512 * 2 * 2)     # KV-Köpfe je Schicht
        qwen = kopf("qwen35", {"qwen35.block_count": 32, "qwen35.attention.head_count_kv": 4,
                               "qwen35.attention.key_length": 256, "qwen35.full_attention_interval": 4})
        self.assertEqual(G._kv_bytes(qwen), 32 * 1024)


if __name__ == "__main__":
    unittest.main()
