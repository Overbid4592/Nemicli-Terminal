"""Offline-Test: Helfer-Agenten (Rolle + Auftrag, eigene Aktionsschleife, Bremse).

python -m unittest discover -s tests -p test_subagents.py
"""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import subagents                                  # noqa: E402


def _aktion(tool: str, **felder) -> str:
    import json
    return "kurz.\n\n```aktion\n" + json.dumps({"tool": tool, **felder}) + "\n```"


class _FakeBackend:
    """Spielt ein Cloud-Modell: liefert pro Aufruf die nächste vorbereitete Antwort."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.calls = []                         # (system, messages) je Aufruf

    async def ask_messages(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        await asyncio.sleep(0)                  # wie ein echter Netzaufruf: gibt den Loop frei
        if not self.antworten:
            return "Bericht: fertig."
        return self.antworten.pop(0)


class NormalizeTests(unittest.TestCase):
    def test_neues_format_mit_rolle(self):
        h = subagents.normalize({"tool": "subagenten", "helfer": [
            {"rolle": "Rechercheur", "aufgabe": "Finde X"},
            {"aufgabe": "Ohne Rolle"},
            "nur Text",
            {"rolle": "leer", "aufgabe": "   "},
        ]})
        self.assertEqual(h, [{"rolle": "Rechercheur", "aufgabe": "Finde X"},
                             {"rolle": "Helfer", "aufgabe": "Ohne Rolle"},
                             {"rolle": "Helfer", "aufgabe": "nur Text"}])

    def test_altes_format_bleibt_lesbar(self):
        h = subagents.normalize({"tool": "subagenten", "aufgaben": ["A", "B"]})
        self.assertEqual([x["aufgabe"] for x in h], ["A", "B"])
        self.assertEqual(subagents.normalize({"tool": "subagenten", "aufgabe": "solo"}),
                         [{"rolle": "Helfer", "aufgabe": "solo"}])

    def test_maximal_fuenf(self):
        h = subagents.normalize({"helfer": [{"rolle": "r", "aufgabe": f"t{i}"} for i in range(9)]})
        self.assertEqual(len(h), subagents.MAX)
        self.assertEqual(subagents.MAX, 5)
        self.assertEqual(subagents.normalize({"tool": "subagenten"}), [])


class RunTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_helfer_macht_schritte_und_berichtet(self):
        backend = _FakeBackend([
            _aktion("datei_lesen", pfad="C:/x.txt"),
            _aktion("web_suche", suche="x"),
            "Ich habe alles gelesen.\n\nBERICHT: x ist y.",
        ])
        ausgefuehrt = []

        async def execute(act, lab):
            ausgefuehrt.append((act["tool"], lab))
            return f"Ergebnis von '{act['tool']}': ok"

        h = {"rolle": "Rechercheur", "aufgabe": "Finde x"}
        bericht = self._run(subagents.run_one(h, backend, execute, idx=2))
        self.assertIn("x ist y", bericht)
        self.assertNotIn("```aktion", bericht)
        self.assertEqual(ausgefuehrt, [("datei_lesen", "Helfer 2 · Rechercheur"),
                                       ("web_suche", "Helfer 2 · Rechercheur")])
        # eigener Verlauf: Auftrag, Antwort, Rückmeldung, Antwort, Rückmeldung, Antwort
        system, msgs = backend.calls[-1]
        self.assertIn("Rolle: Rechercheur", system)
        self.assertIn("Auftrag: Finde x", system)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "assistant", "user"])
        self.assertIn("Ergebnis von 'web_suche'", msgs[-1]["content"])

    def test_schrittbremse(self):
        backend = _FakeBackend([_aktion("datei_lesen", pfad="C:/x.txt")] * 20)
        zaehler = []

        async def execute(act, lab):
            zaehler.append(1)
            return "ok"

        bericht = self._run(subagents.run_one({"rolle": "r", "aufgabe": "a"}, backend, execute))
        self.assertEqual(len(zaehler), subagents.MAX_STEPS)
        self.assertIn("Schrittbremse", bericht)
        # die Stop-Notiz kam VOR der letzten Modellantwort im Verlauf an
        _, msgs = backend.calls[-1]
        self.assertIn("Obergrenze", msgs[-1]["content"])

    def test_helfer_ruft_keine_helfer(self):
        backend = _FakeBackend([
            _aktion("subagenten", helfer=[{"rolle": "x", "aufgabe": "y"}]),
            "Bericht: allein gemacht.",
        ])
        ausgefuehrt = []

        async def execute(act, lab):
            ausgefuehrt.append(act["tool"])
            return "ok"

        bericht = self._run(subagents.run_one({"rolle": "r", "aufgabe": "a"}, backend, execute))
        self.assertEqual(ausgefuehrt, [])
        self.assertIn("allein gemacht", bericht)
        _, msgs = backend.calls[-1]
        self.assertIn("keine weiteren Helfer", msgs[-1]["content"])

    def test_fuenf_parallel_mit_eigenen_rollen(self):
        backend = _FakeBackend([])              # jeder antwortet sofort mit Bericht
        helfer = [{"rolle": f"R{i}", "aufgabe": f"A{i}"} for i in range(7)]

        async def execute(act, lab):
            return "ok"

        res = self._run(subagents.run(helfer, backend, execute))
        self.assertEqual(len(res), 5)
        self.assertEqual([h["rolle"] for h, _ in res], ["R0", "R1", "R2", "R3", "R4"])
        self.assertTrue(all(b == "Bericht: fertig." for _, b in res))
        rollen = {s.split("Rolle: ")[1].split("\n")[0] for s, _ in backend.calls}
        self.assertEqual(rollen, {"R0", "R1", "R2", "R3", "R4"})

    def test_fehler_wird_bericht(self):
        class Kaputt:
            async def ask_messages(self, system, messages):
                raise RuntimeError("Netz weg")

        async def execute(act, lab):
            return "ok"

        bericht = self._run(subagents.run_one({"rolle": "r", "aufgabe": "a"}, Kaputt(), execute))
        self.assertIn("Netz weg", bericht)



class ExecutorTests(unittest.TestCase):
    """Der Ausführer aus main: Bestätigung, Modus, Lock – ohne echte Aktionen."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import main, actions, modes, workspace
        from unittest import mock
        self.main, self.actions, self.modes = main, actions, modes
        # Ist beim Nutzer gerade ein Workspace gesetzt, wuerde dessen Riegel die
        # Testpfade blocken - hier geht es aber um Bestaetigung und Lock.
        ohne_ws = mock.patch.object(workspace, "pfad", lambda: None)
        ohne_ws.start()
        self.addCleanup(ohne_ws.stop)
        # Aktions-Protokoll NICHT in die echte Datei schreiben.
        import protokoll, tempfile
        from pathlib import Path as _P
        log_tmp = tempfile.TemporaryDirectory()
        log_alt = protokoll.PFAD
        protokoll.PFAD = _P(log_tmp.name) / "aktionen.log"
        self.addCleanup(lambda: setattr(protokoll, "PFAD", log_alt))
        self.addCleanup(log_tmp.cleanup)
        self._run_orig = actions.run              # wird pro Test durch eine Attrappe ersetzt
        main.AUTO_ALLOW.clear()

    def tearDown(self):
        self.actions.run = self._run_orig
        self.main.AUTO_ALLOW.clear()

    def _executor(self, antwort="yes"):
        log = {"aktion": [], "ergebnis": [], "frage": [], "warn": []}

        async def frage(text, optionen):
            log["frage"].append(text)
            return antwort

        async def pruefe(titel, preview):
            log["frage"].append(titel)
            return antwort

        ex = self.main._helfer_executor(
            [], zeige_aktion=lambda d, c: log["aktion"].append((d, c)),
            zeige_ergebnis=lambda t, ok, rc: log["ergebnis"].append((t, ok)),
            frage=frage, pruefe=pruefe, warne=lambda t: log["warn"].append(t))
        return ex, log

    def test_lesende_aktion_laeuft_ohne_frage(self):
        ex, log = self._executor()
        self.actions.run = _fake_run("INHALT")
        r = asyncio.run(ex({"tool": "ordner_auflisten", "pfad": str(ROOT)}, "Helfer 1 · r"))
        self.assertIn("INHALT", r)
        self.assertEqual(log["frage"], [])
        self.assertTrue(log["aktion"][0][0].startswith("🤝 Helfer 1 · r:"))
        self.assertFalse(log["aktion"][0][1])

    def test_veraendernde_aktion_fragt_und_ablehnung_stoppt(self):
        ex, log = self._executor(antwort="no")
        self.actions.run = _fake_run("DARF NICHT")
        r = asyncio.run(ex({"tool": "loeschen", "pfad": "C:/nix/da.txt"}, "Helfer 3 · Aufräumer"))
        self.assertIn("ABGELEHNT", r)
        self.assertNotIn("DARF NICHT", r)
        self.assertEqual(len(log["frage"]), 1)
        self.assertIn("Helfer 3 · Aufräumer", log["frage"][0])
        self.assertEqual(log["ergebnis"], [])

    def test_always_merkt_sich_werkzeug(self):
        ex, log = self._executor(antwort="always")
        self.actions.run = _fake_run("weg")
        asyncio.run(ex({"tool": "loeschen", "pfad": "C:/nix/da.txt"}, "H"))
        self.assertIn("loeschen", self.main.AUTO_ALLOW)
        asyncio.run(ex({"tool": "loeschen", "pfad": "C:/nix/db.txt"}, "H"))
        self.assertEqual(len(log["frage"]), 1)          # zweites Mal keine Frage mehr

    def test_nur_ein_helfer_fragt_gleichzeitig(self):
        gleichzeitig = {"jetzt": 0, "max": 0}

        async def frage(text, optionen):
            gleichzeitig["jetzt"] += 1
            gleichzeitig["max"] = max(gleichzeitig["max"], gleichzeitig["jetzt"])
            await asyncio.sleep(0.01)
            gleichzeitig["jetzt"] -= 1
            return "yes"

        ex = self.main._helfer_executor(
            [], zeige_aktion=lambda d, c: None, zeige_ergebnis=lambda t, ok, rc: None,
            frage=frage, pruefe=frage, warne=lambda t: None)
        self.actions.run = _fake_run("ok")

        async def alle():
            await asyncio.gather(*(ex({"tool": "loeschen", "pfad": f"C:/nix/{i}.txt"}, f"H{i}")
                                   for i in range(5)))
        asyncio.run(alle())
        self.assertEqual(gleichzeitig["max"], 1)


def _fake_run(text):
    import actions

    async def run(act):
        return actions.ActionResult(text, ok=True)
    return run


class _FakeConfig:
    """config-Attrappe: nichts landet in nemicli.config.json."""

    def __init__(self):
        self.data = {}

    def load(self):
        return dict(self.data)

    def update(self, **kw):
        self.data.update(kw)


class StufeTests(unittest.TestCase):
    def setUp(self):
        import config
        self.cfg = _FakeConfig()
        self._orig = (config.load, config.update)
        config.load, config.update = self.cfg.load, self.cfg.update

    def tearDown(self):
        import config
        config.load, config.update = self._orig

    def test_standard_ist_an(self):
        self.assertEqual(subagents.stufe(), "an")

    def test_setzen_und_aliasse(self):
        self.assertEqual(subagents.set_stufe("auto"), "auto")
        self.assertEqual(subagents.stufe(), "auto")
        self.assertEqual(subagents.set_stufe("aus"), "off")
        self.assertEqual(subagents.set_stufe("on"), "an")
        self.assertIsNone(subagents.set_stufe("quatsch"))
        self.assertEqual(subagents.stufe(), "an")
        self.cfg.data["subagenten"] = "kaputt"
        self.assertEqual(subagents.stufe(), "an")


class AktivTests(unittest.TestCase):
    def test_zaehler_hoch_und_runter_auch_bei_fehler(self):
        gesehen = []
        subagents.on_aktiv(gesehen.append)
        try:
            backend = _FakeBackend([])

            async def execute(act, lab):
                return "ok"

            asyncio.run(subagents.run([{"rolle": "a", "aufgabe": "x"},
                                       {"rolle": "b", "aufgabe": "y"}], backend, execute))
            self.assertEqual(max(gesehen), 2)
            self.assertEqual(gesehen[-1], 0)
            self.assertEqual(subagents.aktiv(), 0)

            class Kaputt:
                async def ask_messages(self, system, messages):
                    raise RuntimeError("weg")
            gesehen.clear()
            asyncio.run(subagents.run_one({"rolle": "a", "aufgabe": "x"}, Kaputt(), execute))
            self.assertEqual(gesehen, [1, 0])
        finally:
            subagents._ON_AKTIV.remove(gesehen.append)


class LaufTests(unittest.TestCase):
    """_helfer_lauf aus main: off / an (fragen) / auto."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import main, config
        self.main = main
        self.cfg = _FakeConfig()
        self._orig = (config.load, config.update)
        config.load, config.update = self.cfg.load, self.cfg.update
        self.act = {"tool": "subagenten", "helfer": [{"rolle": "R", "aufgabe": "A"}]}
        self.backend = _FakeBackend([])

    def tearDown(self):
        import config
        config.load, config.update = self._orig

    def _lauf(self, antwort):
        log = {"start": [], "bericht": [], "frage": []}
        records = []

        async def frage(text, optionen):
            log["frage"].append(text)
            return antwort

        async def execute(act, lab):
            return "ok"

        r = asyncio.run(self.main._helfer_lauf(
            self.act, self.backend, records, execute,
            zeige_start=lambda hs: log["start"].append(len(hs)),
            zeige_bericht=lambda i, h, b: log["bericht"].append((i, h["rolle"], b)),
            frage=frage))
        return r, log, records

    def test_off_sperrt(self):
        self.cfg.data["subagenten"] = "off"
        r, log, records = self._lauf("yes")
        self.assertIn("AUS", r)
        self.assertEqual(log["start"], [])
        self.assertEqual(log["bericht"], [])
        self.assertEqual(records[-1]["status"], "not_run")

    def test_an_fragt_und_nein_stoppt(self):
        r, log, records = self._lauf("no")
        self.assertEqual(log["frage"], ["1 Helfer losschicken?"])
        self.assertIn("ABGELEHNT", r)
        self.assertEqual(log["bericht"], [])

    def test_an_fragt_und_ja_laeuft(self):
        r, log, records = self._lauf("yes")
        self.assertEqual(len(log["frage"]), 1)
        self.assertEqual(log["bericht"], [(1, "R", "Bericht: fertig.")])
        self.assertIn("Bericht: fertig.", r)
        self.assertEqual(subagents.stufe(), "an")

    def test_an_mit_auto_antwort_schaltet_um(self):
        r, log, records = self._lauf("auto")
        self.assertEqual(len(log["bericht"]), 1)
        self.assertEqual(subagents.stufe(), "auto")
        r2, log2, _ = self._lauf("no")           # jetzt keine Frage mehr
        self.assertEqual(log2["frage"], [])
        self.assertEqual(len(log2["bericht"]), 1)

    def test_lokales_modell_ohne_helfer(self):
        class Lokal:
            pass
        self.backend = Lokal()
        r, log, _ = self._lauf("yes")
        self.assertIn("Cloud-Modell", r)
        self.assertEqual(log["start"], [])


class KopfzeileTests(unittest.TestCase):
    def test_kopfzeile_zeigt_pulsierenden_punkt(self):
        import screen
        sc = screen.Screen()
        sc.invalidate = lambda: None
        self.assertEqual(sc._helfer_fragments("m", "a"), [])
        sc.set_helfer(2)                     # ohne laufenden Loop: kein Puls-Task, aber Zähler
        frags = sc._helfer_fragments("m", "a")
        text = "".join(t for _, t in frags)
        self.assertIn("●", text)
        self.assertIn("Subagenten aktiv: 2", text)
        sc._pulse_on = False
        dunkel = [st for st, t in sc._helfer_fragments("m", "a") if t == "●"][0]
        sc._pulse_on = True
        hell = [st for st, t in sc._helfer_fragments("m", "a") if t == "●"][0]
        self.assertNotEqual(hell, dunkel)
        sc.set_helfer(0)
        self.assertEqual(sc._helfer_fragments("m", "a"), [])


class CloudAskMessagesTests(unittest.TestCase):
    """cloud.ask_messages: volles Budget wie der Haupt-Agent, kein harter Abbruch."""

    def _backend(self, finish_reason, content):
        import cloud
        be = cloud.CloudChat.__new__(cloud.CloudChat)
        be.provider_id, be.model_id, be.strength = "ollama_cloud", "deepseek-v4.1-flash", "normal"
        gesehen = {}

        class _Msg:
            pass

        class _Choice:
            pass

        class _Resp:
            pass

        class _Completions:
            async def create(self, **kw):
                gesehen.update(kw)
                m = _Msg(); m.content = content
                c = _Choice(); c.message = m; c.finish_reason = finish_reason
                r = _Resp(); r.choices = [c]
                return r

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        be.client = _Client()
        return be, gesehen

    def test_helfer_budget(self):
        # Denk-Modell bei Ollama Cloud: 4096 reichte nicht (das Nachdenken zählt
        # mit – „Tokenbudget erreicht“ bei jedem Spielzug), deshalb 16k + Stufe low.
        import reasoning
        be, gesehen = self._backend("stop", "Bericht.")
        text = asyncio.run(be.ask_messages("sys", [{"role": "user", "content": "x"}]))
        self.assertEqual(text, "Bericht.")
        self.assertEqual(gesehen["max_tokens"], reasoning.CLOUD_HELPER_BUDGET)
        self.assertEqual(gesehen["reasoning_effort"], "low")
        self.assertEqual(reasoning.HELPER_BUDGET, 4096)          # unbekannte Modelle weiter knapp
        self.assertEqual(gesehen["messages"][0], {"role": "system", "content": "sys"})

    def test_abgeschnitten_liefert_text_statt_fehler(self):
        be, _ = self._backend("length", "Halber Ber")
        text = asyncio.run(be.ask_messages("sys", [{"role": "user", "content": "x"}]))
        self.assertTrue(text.startswith("Halber Ber"))
        self.assertIn("abgeschnitten", text)


if __name__ == "__main__":
    unittest.main()
