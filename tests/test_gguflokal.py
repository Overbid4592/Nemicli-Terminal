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
            m = Motor()
            with patch.object(G, "denken", return_value=False), patch.object(G, "_motor", ("x", 1, m)), \
                    patch.object(G, "_lora_abgleich"):
                return [s async for s in c._erzeugen(m, [], 100, G.threading.Event())]
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

    def test_familie_eine_ebene_tiefer(self):
        """Ordner ohne eigene .gguf: die Unterordner sind die Mitglieder, die Familie selbst ist das Team
        (gerechnet mit dem Mitglied ohne Bild-Teil); noch tiefer wird nicht gesucht."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "Familie"
            for rel in ("Omni/omni.gguf", "Omni/mmproj-omni.gguf", "Text/text.gguf", "VL/vl.gguf",
                        "VL/mmproj-vl.gguf", "Entscheider/d1/d1.gguf"):
                (f / rel).parent.mkdir(parents=True, exist_ok=True)
                (f / rel).write_bytes(b"x")
            hoert = {"mmproj-omni.gguf": True, "mmproj-vl.gguf": False}
            with patch.object(G, "ORDNER", Path(tmp)), \
                    patch.object(G, "_projektor_info", lambda mm: ("lfm3vl", hoert[mm.name])), \
                    patch.object(G, "projektor_bekannt", return_value=True):
                m = G.modelle()
                self.assertEqual(list(m), ["Familie", "Familie-Omni", "Familie-Text", "Familie-VL"])
                self.assertEqual(m["Familie"].name, "text.gguf")
                self.assertEqual(list(G.familien()["Familie"]), ["Omni", "Text", "VL"])
                self.assertEqual(G._mmproj("Familie-VL").name, "mmproj-vl.gguf")
                self.assertIsNone(G._mmproj("Familie"))
                self.assertEqual(G.familien_augen("Familie")[1].name, "mmproj-vl.gguf")    # Seher ohne Audio
                self.assertIn("Team: Omni · Text · VL", G.beschreibung("Familie"))
                with patch.object(G, "_config", return_value={"model": "gguf:Familie"}):
                    self.assertEqual(G.aktive_familie(), "Familie")
                with patch.object(G, "_config", return_value={"model": "gguf:Familie-Text"}):
                    self.assertIsNone(G.aktive_familie())

    def test_mmproj_hinten_im_namen(self):
        """Mistral: …-BF16-mmproj.gguf sortiert vor …-Q4_K_M.gguf und ist trotzdem der Bildteil."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Ministral"
            d.mkdir()
            for n in ("Ministral-3-14B-Instruct-2512-BF16-mmproj.gguf", "Ministral-3-14B-Instruct-2512-Q4_K_M.gguf"):
                (d / n).write_bytes(b"x")
            with patch.object(G, "ORDNER", Path(tmp)):
                self.assertEqual(G.modelle()["Ministral"].name, "Ministral-3-14B-Instruct-2512-Q4_K_M.gguf")
                self.assertEqual(G._mmproj("Ministral").name, "Ministral-3-14B-Instruct-2512-BF16-mmproj.gguf")

    def test_einstellungen(self):
        with patch.object(G, "_config", return_value={}):
            self.assertEqual(G.kontext(), G.KONTEXT_UNBEKANNT)           # Vorgabe max; ohne Modell 128k
            self.assertEqual(G.kontext_einstellung(), 0)
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

    def test_hintergrund_als_eigener_zug(self):
        """LFM3: Gedächtnis-Treffer und Stand stehen als System-Zug vor der Nachricht, die Nachricht bleibt rein."""
        c = G.GgufChat("LFM3")
        c.stand = "Wir bauen ein Spiel."
        verlauf = [{"role": "user", "content": "Hi du da"}, {"role": "assistant", "content": "Hallo!"},
                   {"role": "user", "content": "Weiter?"}]
        c._zusatz.von(verlauf[0])["gesendet"] = "# Gedächtnis\n- Treffer\n\n" + G.NACHRICHT_MARKE + "Hi du da"
        n = c._nachrichten("SYS", verlauf, stand=True, getrennt=True)
        self.assertEqual([m.role for m in n], ["system", "system", "user", "assistant", "user"])
        self.assertIn("Wir bauen ein Spiel.", n[1].content)
        self.assertIn("- Treffer", n[1].content)
        self.assertEqual(n[2].content, "Hi du da")
        self.assertEqual(n[4].content, "Weiter?")
        alt = c._nachrichten("SYS", verlauf, stand=True)                   # andere Formate: wie bisher
        self.assertEqual([m.role for m in alt], ["system", "user", "assistant", "user"])
        self.assertTrue(alt[1].content.endswith(G.NACHRICHT_MARKE + "Hi du da"))


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
            suche.reset_mock()
            self.assertEqual(G._gedaechtnis("Ergebnis von 'datei_lesen' (ok):\nWas steht da drin?"), "")
            suche.assert_not_called()                     # Werkzeug-Ergebnisse: die Treffer hat schon die Nachricht

    def test_anleitung_ohne_treffer(self):
        # die Treffer stehen an der Nachricht – im Systemprompt änderten sie ihn jede Runde (Präfix-Cache verloren)
        import persona
        with patch.object(persona, "_gedaechtnis_block", return_value="# Gedächtnis: Treffer") as block:
            self.assertNotIn("Treffer", persona.build_system_prompt("", kompakt_fuer_lokal=True,
                                                                     mit_gedaechtnis=False))
            block.assert_not_called()
            self.assertIn("Treffer", persona.build_system_prompt("", kompakt_fuer_lokal=True))

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


class EntladenTests(unittest.TestCase):
    """Entladen nach Ollama-Art: Leihe, Stopp-Signal, höchstens LEIHE_WARTEN_S warten, dann die Engine leeren –
    ihr Speicher ist frei, auch wenn noch jemand sie hält."""

    class Motor:
        class _Datei:
            def close(self):
                pass

        def __init__(self):
            self.gg = self._Datei()
            self.geleert = False

        def leeren(self):
            self.geleert = True

    def setUp(self):
        self.log = []
        for ziel, name, wert in ((G._DBG, "ereignis", lambda text, *a, **k: self.log.append(text)),
                                 (G._DBG, "fehler", lambda *a, **k: None)):
            p = patch.object(ziel, name, side_effect=wert)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, G, "_motor", G._motor)
        self.m = self.Motor()
        G._motor = ("x/Test.gguf", 1, self.m)

    def test_ohne_arbeit_sofort_geleert(self):
        G.entladen()
        self.assertTrue(self.m.geleert)
        self.assertIsNone(G._motor)
        self.assertTrue(any(e.startswith("Modell entladen:") for e in self.log), self.log)
        self.assertFalse(G._stopp.is_set())
        with self.assertRaises(G.MotorWeg):                # danach keine Leihe mehr
            with G.geliehen(self.m):
                pass

    def test_wartet_auf_laufende_arbeit(self):
        import threading
        import time
        losgelegt, ende = threading.Event(), []

        def arbeit():                                      # wie die Zusammenfassung: hört auf das Stopp-Signal
            with G.geliehen(self.m):
                losgelegt.set()
                G._stopp.wait(5)
                time.sleep(0.05)
                ende.append(self.m.geleert)               # beim Aufhören noch nicht geleert
        faden = threading.Thread(target=arbeit)
        faden.start()
        losgelegt.wait(2)
        G.entladen()
        faden.join(2)
        self.assertEqual(ende, [False])
        self.assertTrue(self.m.geleert)
        self.assertFalse(any("hörten" in e for e in self.log), self.log)

    def test_haengende_arbeit_wird_nach_der_frist_trotzdem_geleert(self):
        import threading
        losgelegt, los = threading.Event(), threading.Event()

        def stur():                                        # hört nicht auf das Stopp-Signal
            with G.geliehen(self.m):
                losgelegt.set()
                los.wait(5)
        faden = threading.Thread(target=stur)
        faden.start()
        losgelegt.wait(2)
        with patch.object(G, "LEIHE_WARTEN_S", 0.3):
            G.entladen()
        los.set()
        faden.join(2)
        self.assertTrue(self.m.geleert)
        self.assertTrue(any("1 Arbeit(en) hörten nach" in e for e in self.log), self.log)

    def test_neue_leihe_waehrend_des_entladens_abgelehnt(self):
        with patch.object(G, "_entladen_laeuft", True):
            with self.assertRaises(G.MotorWeg):
                with G.geliehen(self.m):
                    pass
            with G.versucht_geliehen(self.m) as da:
                self.assertFalse(da)

    def test_leerlauf(self):
        import time
        jetzt = time.monotonic()
        with patch.object(G, "leerlauf_minuten", return_value=10):
            with patch.object(G, "_zuletzt", jetzt - 5 * 60):
                self.assertFalse(G.leerlauf_pruefen())     # erst 5 von 10 Minuten
            with patch.object(G, "_zuletzt", jetzt - 11 * 60):
                with patch.object(G, "_runden", 1):
                    self.assertFalse(G.leerlauf_pruefen())   # eine Runde läuft (Werkzeug zwischen zwei Zügen)
                with patch.object(G, "_bild_tiefe", 1):
                    self.assertFalse(G.leerlauf_pruefen())   # ein Bild entsteht
                self.assertTrue(G.leerlauf_pruefen())
        self.assertTrue(self.m.geleert)
        self.assertTrue(any("10 Min. ohne Arbeit" in e for e in self.log), self.log)

    def _laden(self, waehrenddessen=None):
        """_motor_laden mit einer Attrappe der Engine; `waehrenddessen` läuft mitten im Laden."""
        import types
        geladen = []
        test = self

        class Engine(test.Motor):
            def __init__(self, *a, **k):
                super().__init__()
                self.vision = None
                geladen.append(self)
                if waehrenddessen:
                    waehrenddessen()
        G._motor = None
        with patch.object(G, "modelle", return_value={"Neu": Path("x/Neu.gguf")}), \
                patch.object(G, "kontext", return_value=8192), patch.object(G, "_torch_bereit"), \
                patch.object(G, "_frei_vram", return_value=None), patch.object(G, "sieht", return_value=False), \
                patch.object(G, "verteilen", return_value=G.Verteilung(16, None, None, 8192, None)), \
                patch.object(G, "experten_laden", return_value=False), patch.object(G, "experten_speichern"), \
                patch.object(G, "_trt_anbinden"), patch.object(G, "_leerlauf_starten"), \
                patch.object(G, "_lora_abgleich"), \
                patch.dict(sys.modules, {"ggufengine": types.SimpleNamespace(Engine=Engine)}):
            try:
                return G._motor_laden("Neu"), geladen
            except G.MotorWeg:
                return None, geladen

    def test_wechsel_mitten_im_laden_behaelt_das_modell_nicht(self):
        # Modellwechsel zu Ollama, während das lokale Modell noch lädt: entladen() fand nichts – danach blieb es
        # im Grafikspeicher, obwohl es keiner mehr wollte
        engine, geladen = self._laden(waehrenddessen=G.entladen)
        self.assertIsNone(engine)
        self.assertIsNone(G._motor)
        self.assertTrue(geladen[0].geleert)
        self.assertTrue(any("während des Ladens abgewählt" in e for e in self.log), self.log)

    def test_laden_ohne_wechsel_bleibt(self):
        engine, geladen = self._laden()
        self.assertIs(engine, geladen[0])
        self.assertIs(G._motor[2], engine)
        self.assertFalse(engine.geleert)

    def test_leerlauf_aus(self):
        import time
        with patch.object(G, "leerlauf_minuten", return_value=0), \
                patch.object(G, "_zuletzt", time.monotonic() - 10 * 3600):
            self.assertFalse(G.leerlauf_pruefen())
        self.assertFalse(self.m.geleert)

    def test_werkzeug_modell_schalter(self):
        import actions
        gesetzt = {}
        with patch("config.update", side_effect=lambda **kw: gesetzt.update(kw)), \
                patch.object(G, "leerlauf_minuten", side_effect=lambda: gesetzt.get("gguf_leerlauf_min", 0)):
            self.assertIn("bleibt geladen", actions._modell_schalter({}))
            self.assertIn("nach 10 Min.", actions._modell_schalter({"leerlauf_min": "10"}))
            self.assertFalse(actions._modell_schalter({"leerlauf_min": -1}).ok)
            self.assertFalse(actions._modell_schalter({"leerlauf_min": "bald"}).ok)
        self.assertEqual(gesetzt, {"gguf_leerlauf_min": 10})

    def test_wechsel_bricht_den_doppelpuffer_ab(self):
        import gc
        import weakref

        async def ablauf():
            chat = G.GgufChat("Test")
            m = self.Motor()
            weg = weakref.ref(m)

            async def doppelpuffer(engine):
                await asyncio.sleep(60)                    # wartet z. B. auf das Vorlesen im Faden
                return engine
            chat._puffer = asyncio.get_running_loop().create_task(doppelpuffer(m))
            await asyncio.sleep(0)
            del m
            await chat.schliessen()
            gc.collect()
            return weg, chat
        weg, chat = asyncio.run(ablauf())
        self.assertIsNone(weg())                           # nichts hält das Modell mehr
        self.assertIsNone(chat._puffer)


class LeerenTests(unittest.TestCase):
    """Engine.leeren(): jeder Tensor wird ein leerer Platzhalter – auch wenn noch jemand die Engine hält."""

    def test_tensoren_weg_hinweise_bleiben(self):
        import types
        import torch
        from ggufengine.engine import Engine
        from ggufengine.models.common import Linear
        e = Engine.__new__(Engine)
        e.device = torch.device("cpu")
        lin = Linear(torch.ones(4, 4))
        e.model = types.SimpleNamespace(graph=None, schicht=lin, caches=[torch.zeros(3)], schnell=object())
        e.vision = None
        e._checkpoint = (2, [1, 2], [torch.zeros(2)])
        e.n_ctx = 4096
        e.leeren()
        self.assertTrue(e.geleert)
        self.assertEqual(lin.w.device.type, "meta")       # auch über ein Objekt des Motors erreichbar
        self.assertEqual(e.model.caches[0].device.type, "meta")
        self.assertIsNone(e.model.schnell)                 # TensorRT-Leser abgehängt
        self.assertIsNone(e._checkpoint)
        self.assertEqual(e.n_ctx, 4096)                    # Angaben ohne Tensor bleiben
        e.leeren()                                         # zweimal schadet nicht


class WechselWaehrendZusammenfassungTests(unittest.TestCase):
    """Der Doppelpuffer fasst gerade zusammen: sein Faden hört nur auf das Stopp-Signal, und beim Abbrechen
    wartet die Aufgabe in ihrem finally auf ihn. Der Wechsel darf daran nicht hängen bleiben."""

    def _ablauf(self, faden_hoert_auf_stopp: bool, warten_s: float):
        import time

        async def zusammenfassen():
            try:
                await asyncio.sleep(60)
            finally:                                       # wie _erzeugen: wartet auf den eigenen Faden
                if faden_hoert_auf_stopp:
                    await asyncio.to_thread(G._stopp.wait, 20)
                else:
                    await asyncio.sleep(20)

        async def los():
            chat = G.GgufChat("Test")
            chat._puffer = asyncio.get_running_loop().create_task(zusammenfassen())
            await asyncio.sleep(0)
            t = time.perf_counter()
            await chat.schliessen(warten_s=warten_s)
            dauer = time.perf_counter() - t
            for aufgabe in asyncio.all_tasks() - {asyncio.current_task()}:
                aufgabe.cancel()
            return dauer
        return asyncio.run(los())

    def test_stopp_signal_beendet_die_zusammenfassung_sofort(self):
        dauer = self._ablauf(faden_hoert_auf_stopp=True, warten_s=5.0)
        self.assertLess(dauer, 3.0)                        # ohne Signal: die vollen 5 s
        self.assertFalse(G._stopp.is_set())                # gilt nur fürs Warten

    def test_haengende_arbeit_haelt_den_wechsel_nicht_auf(self):
        dauer = self._ablauf(faden_hoert_auf_stopp=False, warten_s=0.3)
        self.assertLess(dauer, 3.0)                        # ohne Frist: 20 s
        self.assertFalse(G._stopp.is_set())


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
        # vision vor dem Austausch laden: sonst bindet es beim ersten Import die Attrappe als GGUFFile
        # für immer, und spätere Tests (test_pixtral) lesen ihre Dateien mit ihr.
        import ggufengine.vision  # noqa: F401
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
        from unittest.mock import patch
        from ggufengine import schichten
        with patch.object(schichten, "int8_weg_da", return_value=False):      # int4-Weg
            self.assertEqual(G._gewichte_bytes(gg), 589824 + M * 10 // 8 + M * 5 // 8 + 1024 * 4)
        gg.tensors["output.weight"].n_bytes = M * 210 // 256
        gg.tensors["blk.0.ffn_up.weight"].n_bytes = M * 144 // 256
        with patch.object(schichten, "int8_weg_da", return_value=True):       # Q4_K/Q6_K im Blockformat
            self.assertEqual(G._gewichte_bytes(gg), 589824 + M * 210 // 256 + M * 144 // 256 + 1024 * 4)
        del gg.tensors["output.weight"]                           # gebundener Kopf aus der Einbettung
        with patch.object(schichten, "int8_weg_da", return_value=False):
            self.assertEqual(G._gewichte_bytes(gg), 589824 + M * 5 // 8 + M * 5 // 8 + 1024 * 4)
        with patch.object(schichten, "int8_weg_da", return_value=True):       # Kopf teilt die Bytes
            self.assertEqual(G._gewichte_bytes(gg), 589824 + M * 144 // 256 + 1024 * 4)

    def test_vram_automatik_rechnet_mit_engine_gewichten(self):
        from types import SimpleNamespace as NS
        datei = NS(stat=lambda: NS(st_size=5 * 2**30))
        with patch.object(G, "modelle", return_value={"K2": datei}), \
                patch.object(G, "kv_bytes_pro_token", return_value=163840), \
                patch.object(G, "gewichte_vram", return_value=6 * 2**30):
            frei = int(14.5 * 2**30)                              # 14,5 - 6 - 1,5 = 7 GB: 32k (5 GB) passt
            self.assertEqual(G.passender_kontext("K2", 65536, frei), 32768)
            self.assertEqual(G.passender_kontext("K2", 65536, 12 * 2**30), 16384)   # 4,5 GB: 16k (2,5 GB)

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

    def test_schichten_budget(self):
        from types import SimpleNamespace as NS
        GB = 2**30
        datei = NS(stat=lambda: NS(st_size=12 * GB))
        with patch.object(G, "modelle", return_value={"D": datei}), \
                patch.object(G, "gewichte_vram", return_value=12 * GB), \
                patch.object(G, "experten_vram", return_value=None), \
                patch.object(G, "_sehen_bytes", return_value=GB), \
                patch.object(G, "kv_bytes_pro_token", return_value=65536):
            with patch.object(G, "_config", return_value={}):
                self.assertEqual(G.schichten_modus(), "auto")
                self.assertIsNone(G.schichten_budget("D", 8192, 20 * GB))          # passt samt Kontext
                # erst der Kontext (2 GB), dann Reserve und Bild-Teil: der Rest bleibt den Schichten
                self.assertEqual(G.schichten_budget("D", 32768, 16 * GB), 16 * GB - 2 * GB - G.RESERVE - GB)
                self.assertEqual(G.schichten_budget("D", 32768, 3 * GB), 0)
                with patch.object(G, "schichten_plan",
                                  side_effect=lambda n, b: None if b is None else NS(vram_bytes=8 * GB)):
                    self.assertGreater(G.passender_kontext("D", 65536, 14 * GB, schichten=8 * GB),
                                       G.passender_kontext("D", 65536, 14 * GB))
            with patch.object(G, "_config", return_value={"gguf_kv_ram": True}):   # auch dann: erst der Kontext
                self.assertEqual(G.schichten_budget("D", 32768, 16 * GB), 16 * GB - 2 * GB - G.RESERVE - GB)
            # der Bild-Teil zählt beim Kontext mit: ohne ihn wäre mehr Platz
            with patch.object(G, "_config", return_value={}):
                mit = G.passender_kontext("D", 65536, 16 * GB)
                with patch.object(G, "_sehen_bytes", return_value=0):
                    self.assertGreater(G.passender_kontext("D", 65536, 16 * GB), mit)
            with patch.object(G, "_config", return_value={"gguf_schichten": "vram"}):
                self.assertIsNone(G.schichten_budget("D", 32768, 3 * GB))
            with patch.object(G, "_config", return_value={"gguf_schichten": "ram"}):
                self.assertEqual(G.schichten_budget("D", 8192, 20 * GB), 0)
            with patch.object(G, "_config", return_value={"gguf_schichten": "quatsch"}):
                self.assertEqual(G.schichten_modus(), "auto")
            with patch.object(G, "_config", return_value={}), patch.object(G, "experten_vram", return_value=GB):
                self.assertIsNone(G.schichten_budget("D", 32768, 3 * GB))         # MoE: Experten-Budget

    def test_modell_ort_in_worten(self):
        from types import SimpleNamespace as NS
        datei = object()
        with patch.object(G, "modelle", return_value={"D": datei}):
            with patch.object(G, "_motor", None):
                self.assertIn("automatisch", G.modell_ort("D"))
            ganz = NS(schichten_plan=None)
            with patch.object(G, "_motor", (datei, (), ganz)):
                self.assertIn("ganz in der Grafikkarte", G.modell_ort("D"))
            hybrid = NS(schichten_plan=NS(ram=frozenset({"blk.0.ffn_up.weight"}), ram_bytes=3 * 2**30))
            with patch.object(G, "_motor", (datei, (), hybrid)):
                self.assertIn("Hybrid – 3.0 GB im RAM", G.modell_ort("D"))
            moe = NS(schichten_plan=None, _lager=lambda: [NS(plaetze=40, n_exp=128)])
            with patch.object(G, "_motor", (datei, (), moe)):
                self.assertIn("je Schicht 40 von 128 Experten", G.modell_ort("D"))

    def test_experten_gedaechtnis_nur_fuer_dieselbe_datei(self):
        from types import SimpleNamespace as NS
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            datei = d / "M" / "m.gguf"
            datei.parent.mkdir()
            datei.write_bytes(b"x" * 10)
            gesetzt = []
            engine = NS(_lager=lambda: [object()], experten_zaehlung=lambda: [[1.0, 2.0]],
                        experten_zaehlung_setzen=lambda z: gesetzt.append(z) or True)
            with patch.object(G, "modelle", return_value={"M": datei}), \
                    patch.object(G, "_prefix_datei", return_value=d / "Cache" / "M.safetensors"):
                self.assertTrue(G.experten_speichern("M", engine))
                self.assertTrue(G.experten_laden("M", engine))
                self.assertEqual(gesetzt, [[[1.0, 2.0]]])
                datei.write_bytes(b"y" * 20)                      # neue Modelldatei: alte Zählung passt nicht
                self.assertFalse(G.experten_laden("M", engine))
                self.assertFalse(G.experten_speichern("M", NS()))  # ohne Lager nichts zu sichern

    def test_beiwerk_ist_keine_modelldatei(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for n in ("imatrix_unsloth.gguf", "mmproj-BF16.gguf", "mtp-qwen3.8-27b-Q4_0.gguf", "qwen3.8-27b-IQ3_S.gguf"):
                (d / n).write_bytes(b"")
            self.assertEqual(G._gguf_in(d).name, "qwen3.8-27b-IQ3_S.gguf")

    def test_gespraechsspeicher_8_bit(self):
        with patch.object(G, "_kopf_werte", return_value=(131072, 163840, None)):
            self.assertEqual(G.kv_bytes_pro_token("K2"), 163840)
            self.assertEqual(G.kv_bytes_pro_token("K2", 8), 87040)

    def test_erkenner_waehlt_die_schnellste_verteilung(self):
        from types import SimpleNamespace as NS
        GB = 2**30
        datei = NS(stat=lambda: NS(st_size=10 * GB))
        with patch.object(G, "modelle", return_value={"D": datei}),                 patch.object(G, "_kopf_werte", return_value=(262144, 65536, 10 * GB, None)),                 patch.object(G, "_sehen_bytes", return_value=0),                 patch.object(G, "schichten_plan", side_effect=lambda n, b: None if b is None
                             else NS(vram_bytes=b)),                 patch.object(G, "_config", return_value={"gguf_kv_ram": True, "gguf_kv_4bit": False}):
            ctx = 32768                                    # 16 Bit: 2 GB, 8 Bit: ~1,06 GB
            self.assertEqual(G.verteilen("D", ctx, 14 * GB), G.Verteilung(16, None, None, ctx, None))
            self.assertEqual(G.verteilen("D", ctx, 13 * GB), G.Verteilung(8, None, None, ctx, None))
            v = G.verteilen("D", ctx, 12 * GB)             # Hybrid mit 8 Bit, der Kontext bleibt ganz
            self.assertEqual((v.bits, v.n_ctx, v.kv_vram, v.experten), (8, ctx, None, None))
            self.assertIsNotNone(v.schichten)
            v = G.verteilen("D", 262144, 8 * GB)           # zu groß selbst ohne Schichten: KV teils im RAM
            self.assertEqual((v.bits, v.n_ctx, v.schichten), (8, 262144, 0))
            self.assertIsNotNone(v.kv_vram)
            self.assertLess(v.kv_vram, 262144)
            with patch.object(G, "_frei_vram", return_value=14 * GB):      # ohne Angabe: gemessen (hier fest)
                self.assertEqual(G.verteilen("D", ctx, None), G.Verteilung(16, None, None, ctx, None))

    def test_erkenner_mit_4bit_stufen(self):
        """Vorgabe: nach 8 Bit erst 6 Bit (Schlüssel int8, Werte 4 Bit), dann 4 Bit – alles in der Grafikkarte,
        bevor Schichten in den RAM wandern."""
        from types import SimpleNamespace as NS
        GB = 2**30
        datei = NS(stat=lambda: NS(st_size=10 * GB))
        with patch.object(G, "modelle", return_value={"D": datei}), \
                patch.object(G, "_kopf_werte", return_value=(262144, 65536, 10 * GB, None)), \
                patch.object(G, "_sehen_bytes", return_value=0), \
                patch.object(G, "schichten_plan", side_effect=lambda n, b: None if b is None else NS(vram_bytes=b)), \
                patch.object(G, "_config", return_value={}):
            ctx = 32768                                    # 8 Bit: 1,06 GB, 6 Bit: 0,81 GB, 4 Bit: 0,56 GB
            self.assertEqual(G.kv_bytes_pro_token("D", 6), 65536 * 26 // 64)
            self.assertEqual(G.kv_bytes_pro_token("D", 4), 65536 * 18 // 64)
            self.assertEqual(G.verteilen("D", ctx, 13 * GB).bits, 8)
            weg = []                                       # immer weniger Platz: 8 -> 6 -> 4, dann erst Hybrid
            for frei in range(13 * GB, 10 * GB, -GB // 32):
                v = G.verteilen("D", ctx, frei)
                schritt = (v.bits, v.schichten is not None)
                if not weg or weg[-1] != schritt:
                    weg.append(schritt)
            self.assertEqual(weg, [(8, False), (6, False), (4, False), (4, True)])
            v = G.verteilen("D", 262144, 8 * GB)           # danach wie bisher, mit der kleinsten Stufe:
            self.assertEqual((v.bits, v.n_ctx), (4, 262144))   # mit 8 Bit blieb hier nichts in der Grafikkarte
            self.assertGreater(v.schichten, 0)

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


class LadezeitenTests(unittest.TestCase):
    def test_zeile_mit_allen_schritten(self):
        class E:
            load_phases = {"file": 0.1, "tokenizer": 0.4, "weights": 6.0, "warmup": 1.2, "vision": 0.3}
        zeiten = {"laden": 9.0, "vorbereitung": 0.5, "zwischenspeicher": 0.2, "erstes_token": 4.25,
                  "gelesen": 3412}
        text = G.ladezeiten_text(E(), zeiten)
        self.assertIn("Laden 9,0 s", text)
        for teil in ("Datei 0,1", "Tokenizer 0,4", "Gewichte 6,0", "Aufwärmen 1,2", "Bildteil 0,3",
                     "Sonstiges 1,0", "Zwischenspeicher 0,2 s", "3.412 Token"):
            self.assertIn(teil, text)

    def test_ohne_phasen_und_ohne_antwort(self):
        text = G.ladezeiten_text(object(), {"laden": 2.0})
        self.assertIn("Sonstiges 2,0", text)
        self.assertNotIn("Token", text)


if __name__ == "__main__":
    unittest.main()
