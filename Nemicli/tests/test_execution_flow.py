"""Gesprächsabläufe mit künstlichen Antworten und Werkzeugen, ohne Modellaufruf."""

import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace as NS
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
for _sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / _sub))

import mitschrift      # noqa: E402  (F12-Mitschnitt laeuft im Ablauf mit)
import protokoll       # noqa: E402  (Aktions-Protokoll laeuft im Ablauf mit)


def extract_functions(relative, names, namespace):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name in names]
    if len(nodes) != len(names):
        raise AssertionError("Test muss die aktuellen Originalfunktionen verwenden")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), relative, "exec"), namespace)


class Backend:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.inputs = []
        self.messages = []

    async def stream(self, text, images=None):
        self.inputs.append(text)
        self.messages.append({"role": "user", "content": text})
        answer = next(self.answers)
        yield {"type": "text", "text": answer}
        self.messages.append({"role": "assistant", "content": answer})


def action(tool="befehl"):
    return '```aktion\n' + json.dumps({"tool": tool, "pfad": "synthetisch.txt"}) + '\n```'


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, answers, result=None, approve=True, preview=None):
        self.output, self.events, self.calls, self.reviews = [], [], [], []
        self.mode_decide = lambda act, need: "ask" if need else "run"   # Modus „normal“
        self.backend = Backend(answers)
        self.preview_error = None

        @contextmanager
        def live(*args):
            yield NS(update=lambda *args: None)

        async def run(act):
            self.calls.append(deepcopy(act))
            if isinstance(result, BaseException):
                raise result
            return result or NS(text="synthetische Ausgabe", ok=True, returncode=0)

        async def review(title, content):
            self.assertEqual([], self.calls, "Freigabe muss vor der Ausführung liegen")
            self.reviews.append(content)
            return approve

        async def confirm(question, options, **kwargs):
            if "preview" in kwargs:
                await review(question, kwargs["preview"])
            return "yes" if approve else "no"

        def preview_for(act):
            if self.preview_error:
                raise self.preview_error
            return preview

        parser = {"json": json,
                  "_BLOCK": re.compile(r'```(?:aktion|json)?\s*\n(\{.*?\})\s*(?:```|\Z)', re.S),
                  "_FENCE_OPEN": re.compile(r'```(?:aktion|json)\s*\n')}
        extract_functions("tools/actions.py", {"parse_actions", "unparsed_action_note"}, parser)
        ui = NS(TUI=None, console=NS(print=self.output.append),
                execution_receipt=lambda rows: ("receipt", deepcopy(rows)),
                action_request=lambda desc, confirm: ("request", desc, confirm),
                action_result=lambda text, ok: ("result", text, ok),
                new_verb_seed=lambda: 0, verb_at=lambda *args: "", live_view=live, thinking=live,
                working_meter=lambda *args: None, stream_meter=lambda *args: None,
                done_meter=lambda *args: None, warn=self.output.append, info=self.output.append)
        async def keine_bilder(res, model):          # Bild-Uebergabe (bild_ansehen) hier nicht Thema
            return None, res

        mitschrift.leeren()          # F12-Mitschnitt: jede Runde faengt frisch an
        # Aktions-Protokoll NICHT in die echte Datei schreiben.
        import tempfile
        self._log_tmp = tempfile.TemporaryDirectory()
        self._log_alt = protokoll.PFAD
        protokoll.PFAD = Path(self._log_tmp.name) / "aktionen.log"
        self.addCleanup(lambda: setattr(protokoll, "PFAD", self._log_alt))
        self.addCleanup(self._log_tmp.cleanup)
        self.ns = dict(asyncio=asyncio, time=time, ui=ui, MAX_STEPS=4, MAX_LESE_STEPS=7,
                       _schritt_grenze=lambda nur_gelesen: 7 if nur_gelesen else 4,
                       AUTO_ALLOW=set(),
                       mitschrift=mitschrift, protokoll=protokoll,
                       PS=NS(active=lambda: NS(name="Test")),
                       _bilder_aus_ergebnis=keine_bilder, _bild_vorschau=lambda *a, **k: None,
                       _DEDUPE_TOOLS={"datei_lesen"}, _STEP_LIMIT_NOTE=" [LIMIT]",
                       _STEP_LIMIT_WARN="limit",
                       _DUPLICATE_NOTE="dup", _action_key=repr,
                       modes=NS(decide=lambda act, need: self.mode_decide(act, need),
                                block_message=lambda act: "blockiert",
                                READ_TOOLS={"datei_lesen"}),
                       Group=lambda *args: args, SESSION={"tokens": 0},
                       _esc_pressed=lambda: False, _update_session_meters=lambda *args: None,
                       _render=lambda thinking, answer, *args, **kwargs: answer,
                       review_action_confirm=review, ask_confirm=confirm,
                       actions=NS(parse_actions=parser["parse_actions"],
                                  unparsed_action_note=parser["unparsed_action_note"],
                                  resolve_tool=lambda name: name,
                                  describe=lambda act: act["tool"],
                                  needs_confirm=lambda act: act["tool"] != "datei_lesen",
                                  confirmation_preview=preview_for, run=run,
                                  reset_taint=lambda: None,
                                  ActionResult=lambda text, ok: NS(text=text, ok=ok, returncode=None)))
        extract_functions("main.py", {"_result_feedback", "_finish_record", "_close_records",
                                       "converse", "_converse", "web_converse", "_web_converse"}, self.ns)
        self.confirm = confirm

    async def run_flow(self, web):
        if web:
            await self.ns["web_converse"](self.backend, "Offline-Test", None,
                                          lambda ev: self.events.append(deepcopy(ev)), self.confirm)
            return next(ev["records"] for ev in reversed(self.events) if ev["t"] == "receipt")
        await self.ns["converse"](self.backend, "Offline-Test")
        return next(item[1] for item in reversed(self.output)
                    if isinstance(item, tuple) and item[0] == "receipt")

    async def test_f12_mitschnitt_faengt_frage_antwort_und_aktion(self):
        """F12 soll das GANZE Gespraech sichern koennen - also muss der
        Mitschnitt schon waehrend des Ablaufs gefuellt werden, nicht erst
        hinterher (der Denktext existiert danach nicht mehr)."""
        self.setup_flow([action(), "Fertig."], NS(text="ausgefuehrt", ok=True, returncode=0))
        await self.run_flow(False)
        arten = [e["art"] for e in mitschrift._eintraege]
        self.assertEqual(arten[0], "nutzer")
        self.assertIn("aktion", arten)
        self.assertIn("assistent", arten)
        frage = mitschrift._eintraege[0]["text"]
        self.assertEqual(frage, "Offline-Test")
        getan = next(e for e in mitschrift._eintraege if e["art"] == "aktion")
        self.assertEqual(getan["werkzeug"], "befehl")
        self.assertTrue(getan["ok"])
        self.assertIn("ausgefuehrt", getan["ergebnis"])
        # ... und im Markdown steht es dann auch wirklich drin.
        md = mitschrift.als_markdown({"chat": 1})
        self.assertIn("Offline-Test", md)
        self.assertIn("`befehl`", md)

    async def test_abgelehnte_aktion_steht_als_abgelehnt_im_mitschnitt(self):
        self.setup_flow([action(), "Na gut."], NS(text="egal", ok=True, returncode=0),
                        approve=False)
        await self.run_flow(False)
        getan = [e for e in mitschrift._eintraege if e["art"] == "aktion"]
        self.assertTrue(getan)
        self.assertFalse(getan[0]["ok"])
        self.assertIn("abgelehnt", getan[0]["ergebnis"].lower())

    async def test_text_claim_has_no_execution_receipt(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow(["Ich habe die Datei geschrieben."])
                self.assertEqual([], await self.run_flow(web))
                self.assertEqual([], self.calls)

    async def test_result_status_and_feedback_do_not_depend_on_output_words(self):
        for web in (False, True):
            for ok, status, text in ((False, "failed", "Access is denied."),
                                     (True, "success", "Fehlerstatistik: 0 Fehler"),
                                     (None, "unverified", "Eine externe Textantwort")):
                with self.subTest(web=web, ok=ok):
                    self.setup_flow([action(), "Fertig."], NS(text=text, ok=ok, returncode=7))
                    rows = await self.run_flow(web)
                    self.assertEqual([{"tool": "befehl", "status": status}], rows)
                    self.assertIn("Exitcode 7", self.backend.inputs[1])
                    self.assertIn(text, self.backend.inputs[1])
                    if web:
                        shown = next(ev for ev in self.events if ev["t"] == "action_result")
                        self.assertIs(ok, shown["ok"])
                    else:
                        shown = next(item for item in self.output if isinstance(item, tuple)
                                     and item[0] == "result")
                        self.assertIs(ok, shown[2])

    async def test_full_content_approved_before_execution(self):
        content = "Ziel: synthetisch.txt\n" + "Zeile\n" * 800 + "LETZTE ZEILE"
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Fertig."], preview=content)
                rows = await self.run_flow(web)
                self.assertEqual([content], self.reviews)
                self.assertEqual(1, len(self.calls))
                self.assertEqual("success", rows[0]["status"])

    async def test_rejected_preview_does_not_execute(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Abgelehnt."], approve=False,
                                preview="Vollständiger Testinhalt")
                rows = await self.run_flow(web)
                self.assertEqual([], self.calls)
                self.assertEqual("rejected", rows[0]["status"])

    async def test_preview_failure_does_not_offer_blind_approval(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_bearbeiten"), "Quelle nicht lesbar."])
                self.preview_error = OSError("Quelle nicht lesbar")
                rows = await self.run_flow(web)
                self.assertEqual([], self.calls)
                self.assertEqual([], self.reviews)
                self.assertEqual("not_run", rows[0]["status"])

    async def test_cancelled_action_never_gets_success_receipt(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action()], result=asyncio.CancelledError())
                with self.assertRaises(asyncio.CancelledError):
                    await self.run_flow(web)
                rows = (self.events[-1]["records"] if web else self.output[-1][1])
                self.assertEqual("unverified", rows[0]["status"])

    async def test_duplicate_read_action_is_skipped(self):
        """Zweites datei_lesen mit identischen Feldern läuft nicht nochmal."""
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_lesen"), action("datei_lesen"), "Fertig."])
                rows = await self.run_flow(web)
                self.assertEqual(1, len(self.calls))                       # nur einmal ausgeführt
                self.assertEqual([{"tool": "datei_lesen", "status": "success"},
                                  {"tool": "datei_lesen", "status": "not_run"}], rows)
                self.assertIn("dup", self.backend.inputs[2])               # Hinweis geht ans Modell

    async def test_step_limit_gets_summary_round_without_execution(self):
        """Nach MAX_STEPS Aktionen: eine Extra-Runde nur zum Zusammenfassen; eine dort
        geplante Aktion wird NICHT ausgeführt, die Bremse wird gemeldet."""
        for web in (False, True):
            with self.subTest(web=web):
                answers = [action("befehl")] * 4 + [action("befehl")]      # 4 = MAX_STEPS im Test
                self.setup_flow(answers)
                rows = await self.run_flow(web)
                self.assertEqual(4, len(self.calls))
                self.assertIn("[LIMIT]", self.backend.inputs[4])           # Hinweis in der Extra-Runde
                self.assertEqual("not_run", rows[-1]["status"])
                if web:
                    self.assertTrue(any(ev.get("t") == "warn" and ev["text"] == "limit" for ev in self.events))
                else:
                    self.assertIn("limit", self.output)

    async def test_read_only_chain_gets_longer_leash(self):
        """Nur Lese-Aktionen: die längere Grenze gilt (7 im Test statt 4). Sobald eine
        ändernde Aktion dabei war, zählt wieder die kurze Bremse (Chat 137: die
        Systemwache war nach dem Lesen der Anleitung schon am Limit)."""
        for web in (False, True):
            with self.subTest(web=web):
                lese = [action("datei_lesen").replace("synthetisch.txt", f"d{i}.txt") for i in range(7)]
                self.setup_flow(lese + [action("datei_lesen").replace("synthetisch.txt", "d9.txt")])
                rows = await self.run_flow(web)
                self.assertEqual(7, len(self.calls))                       # 7 gelesen, die 8. nicht mehr
                self.assertEqual("not_run", rows[-1]["status"])
            with self.subTest(web=web, gemischt=True):
                lese = [action("datei_lesen").replace("synthetisch.txt", f"e{i}.txt") for i in range(3)]
                self.setup_flow([action("befehl")] + lese + [action("befehl")])
                rows = await self.run_flow(web)
                self.assertEqual(4, len(self.calls))                       # kurze Bremse: 1 + 3, die 5. nicht
                self.assertEqual("not_run", rows[-1]["status"])

    async def test_step_limit_summary_answer_ends_cleanly(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("befehl")] * 4 + ["Zwischenstand: …"])
                rows = await self.run_flow(web)
                self.assertEqual(4, len(self.calls))
                self.assertEqual(4, len(rows))
                self.assertTrue(all(r["status"] == "success" for r in rows))

    async def test_read_only_mode_blocks_without_asking(self):
        """Modus 👁 Nur Lesen: verändernde Aktion wird weder gefragt noch ausgeführt."""
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Verstanden, nur Vorschlag."])
                self.mode_decide = lambda act, need: "block" if need else "run"
                rows = await self.run_flow(web)
                self.assertEqual([], self.calls)
                self.assertEqual([], self.reviews)                       # keine Rückfrage
                self.assertEqual([{"tool": "datei_schreiben", "status": "not_run"}], rows)
                self.assertIn("blockiert", self.backend.inputs[1])       # Modell erfährt es

    async def test_auto_mode_runs_without_asking(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Fertig."], preview="Inhalt")
                self.mode_decide = lambda act, need: "run"
                rows = await self.run_flow(web)
                self.assertEqual(1, len(self.calls))
                self.assertEqual([], self.reviews)
                self.assertEqual("success", rows[0]["status"])


if __name__ == "__main__":
    unittest.main()
