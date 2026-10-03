"""Offline-UI-Prüfungen mit synthetischen Daten; keine Modelle oder Privatdateien."""

import asyncio
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import patch

from prompt_toolkit.application.current import create_app_session, set_app
from prompt_toolkit.buffer import EditReadOnlyBuffer
from prompt_toolkit.completion import DummyCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.input import DummyInput
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput
from rich.console import Console


ROOT = Path(__file__).resolve().parents[1]


def load_source(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UI = load_source("action_ui_rendering", "ui/ui.py")
with patch.dict(sys.modules, {
    "ui": UI,
    "mascot": NS(rprompt=lambda: [], folder_badge=lambda **kw: []),
    "models": NS(strength_menu=lambda *args: {}, split_ref=lambda ref: ref.partition(":")),
    "chatstore": NS(chat_args=lambda: {}),
    "commands": NS(SlashCompleter=lambda **kw: DummyCompleter()),
}):
    SCREEN = load_source("action_ui_screen", "ui/screen.py")
    CONFIRM = load_source("action_ui_confirm", "core/confirm.py")


def press(app, key):
    with set_app(app):
        bindings = app.key_bindings.get_bindings_for_keys((key,))
        active = [binding for binding in bindings if binding.filter()]
        assert active, key
        active[-1].handler(NS(app=app, current_buffer=app.current_buffer,
                              arg=1, data="", is_repeat=False))


class ActionReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = create_app_session(input=DummyInput(), output=DummyOutput())
        self.session.__enter__()
        self.addCleanup(self.session.__exit__, None, None, None)
        self.screen = SCREEN.Screen()
        self.screen.practice_on = True     # unabhängig von der echten Config (/uebung aus)
        self.screen.app = self.screen._build_app()

    async def test_review_preserves_full_text_and_draft_and_requires_f8(self):
        draft = Document("Synthetischer Entwurf\nzweite Zeile", cursor_position=7)
        self.screen.buffer.set_document(draft)
        preview = "\n".join(f"Vorschauzeile {i}" for i in range(1200))
        task = asyncio.create_task(self.screen.review_action("Testaktion", preview))
        await asyncio.sleep(0)
        self.assertEqual(self.screen._review_buffer.text, "Testaktion\n\n" + preview)
        self.assertEqual(self.screen._review_buffer.cursor_position, 0)
        self.assertEqual(self.screen.transcript, [])
        with self.assertRaises(EditReadOnlyBuffer):
            self.screen._review_buffer.insert_text("verändern")
        press(self.screen.app, Keys.ControlM)
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        press(self.screen.app, Keys.End)
        self.assertEqual(self.screen._review_buffer.cursor_position,
                         len(self.screen._review_buffer.text))
        press(self.screen.app, Keys.Home)
        self.assertEqual(self.screen._review_buffer.cursor_position, 0)
        press(self.screen.app, Keys.F8)
        self.assertTrue(await task)
        self.assertIsNone(self.screen._modal)
        self.assertEqual(self.screen.buffer.text, draft.text)
        self.assertEqual(self.screen.buffer.cursor_position, draft.cursor_position)

    async def test_escape_and_ctrl_c_reject(self):
        for key in (Keys.Escape, Keys.ControlC):
            task = asyncio.create_task(self.screen.review_action("Test", "Volltext"))
            await asyncio.sleep(0)
            press(self.screen.app, key)
            self.assertFalse(await task)
            self.assertIsNone(self.screen._modal)

    async def test_review_cancellation_restores_input(self):
        task = asyncio.create_task(self.screen.review_action("Test", "Volltext"))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.screen._modal)
        self.assertIs(self.screen.app.layout.current_window, self.screen._input_win)

    async def test_classic_review_is_fullscreen_readonly_and_f8_only(self):
        preview = "Letzte Zeile\n" * 1200
        app = CONFIRM._review_app("Synthetische Aktion", preview)
        self.assertTrue(app.full_screen)
        self.assertEqual(app.current_buffer.text, "Synthetische Aktion\n\n" + preview)
        self.assertTrue(app.current_buffer.read_only())
        with patch.object(app, "exit") as finish:
            press(app, Keys.ControlM)
            finish.assert_not_called()
            press(app, Keys.F8)
            finish.assert_called_once_with(result=True)

    async def test_cancel_keeps_busy_until_cleanup_and_is_only_sent_once(self):
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancelled = []

        async def practice(_ctx, _should_stop):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                cleaning.set()
                await release.wait()
                raise

        task = asyncio.create_task(self.screen.run_practice_once(None, practice))
        await started.wait()
        self.screen._stop_practice()
        await cleaning.wait()
        self.screen._stop_practice()
        await asyncio.sleep(0)
        self.assertTrue(self.screen.busy)
        self.assertTrue(self.screen._practicing)
        self.assertFalse(task.done())
        release.set()
        self.assertEqual(await task, "abgebrochen")
        self.assertEqual(cancelled, [True])
        self.assertFalse(self.screen.busy)
        self.assertFalse(self.screen._practicing)

    async def test_parent_cancel_also_waits_for_practice_cleanup(self):
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def practice(_ctx, _should_stop):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        task = asyncio.create_task(self.screen.run_practice_once(None, practice))
        await started.wait()
        task.cancel()
        await cleaning.wait()
        self.assertTrue(self.screen.busy)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.screen.busy)

    async def test_declined_idle_round_does_not_repeat(self):
        calls = []

        async def practice(_ctx, should_stop):
            self.assertFalse(should_stop())
            calls.append(True)
            return "abgelehnt"

        task = self.screen._launch_practice(None, practice, repeat=True)
        self.assertEqual(await task, "abgelehnt")
        self.assertEqual(calls, [True])
        self.assertTrue(self.screen.practice_on)
        self.assertFalse(self.screen.busy)

    async def test_cancel_before_first_task_step_releases_busy(self):
        async def practice(_ctx, _should_stop):
            self.fail("Abgebrochene Runde darf nicht starten")

        task = self.screen._launch_practice(None, practice, repeat=False)
        self.screen._stop_practice()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        self.assertFalse(self.screen.busy)
        self.assertFalse(self.screen._practicing)


class ReceiptTests(unittest.TestCase):
    def render(self, item):
        output = io.StringIO()
        Console(file=output, width=110, theme=UI._build_theme("cyan"),
                color_system=None).print(item)
        return output.getvalue()

    def test_unverified_result_has_no_success_checkmark_and_retains_full_text(self):
        output = self.render(UI.action_result("synthetisch " * 300 + "ENDMARKER", ok=None))
        self.assertIn("Status ungeprüft", output)
        self.assertIn("ENDMARKER", output)
        self.assertNotIn("✓", output)

    def test_receipt_distinguishes_no_action_rejected_and_unverified(self):
        self.assertIn("Keine Werkzeugaktion ausgeführt.", self.render(UI.execution_receipt([])))
        output = self.render(UI.execution_receipt([
            {"tool": "befehl", "status": "success"},
            {"tool": "datei_schreiben", "status": "rejected"},
            {"tool": "subagenten", "status": "success"},
            {"tool": "vorschau", "status": "not_run"},
        ]))
        self.assertIn("Ausführung erfolgreich", output)
        self.assertIn("Abgelehnt", output)
        self.assertIn("Status ungeprüft", output)
        self.assertIn("Nicht ausgeführt", output)
        self.assertNotIn("✓ subagenten", output)


if __name__ == "__main__":
    unittest.main()
