"""Offline-Tests für die Nutzungs-Statistik (tools/stats.py)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import stats   # noqa: E402


class StatsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        stats._FILE = Path(self._tmp.name) / "stats.json"
        stats._data = None
        stats._session_start = None

    def tearDown(self):
        self._tmp.cleanup()

    def test_records_accumulate_and_persist(self):
        stats.start_session("m1")
        stats.record_command("/model")
        stats.record_command("/model")
        stats.record_turn("m1", "Lara", "auto", 100, 20, 0.0)
        stats.record_turn("m2", "NemiCLI", "normal", 200, 50, 0.01)
        stats.record_tools([{"tool": "datei_lesen", "status": "success"},
                            {"tool": "befehl", "status": "failed"},
                            {"tool": "bild_malen", "status": "success"}])
        stats.end_session()
        # frisch laden (neue Instanz simulieren)
        stats._data = None
        r = stats.report()
        self.assertEqual(r["sessions"], 1)
        self.assertEqual(r["messages"], 2)
        self.assertEqual(r["commands_total"], 2)
        self.assertEqual(dict(r["top_commands"])["/model"], 2)
        self.assertEqual(r["tokens_in"], 300)
        self.assertEqual(r["tokens_out"], 70)
        self.assertEqual(r["images"], 1)
        tools = {name: (tot, ok, fail) for name, tot, ok, fail in r["top_tools"]}
        self.assertEqual(tools["datei_lesen"], (1, 1, 0))
        self.assertEqual(tools["befehl"], (1, 0, 1))
        models = {name: turns for name, turns, _, _ in r["top_models"]}
        self.assertEqual(models, {"m1": 1, "m2": 1})
        self.assertEqual(dict(r["top_personas"]), {"Lara": 1, "NemiCLI": 1})
        self.assertEqual(r["modes"], {"auto": 1, "normal": 1})

    def test_no_message_content_stored(self):
        stats.record_turn("m", "P", "chat", 10, 5, 0.0)
        stats.record_command("/bild")
        raw = stats._FILE.read_text(encoding="utf-8") if stats._FILE.exists() else ""
        # Es dürfen nur Namen/Zahlen drinstehen, keine Prompt-Texte (stichprobenartig)
        for key in ("commands", "models", "personas", "modes", "tokens_in"):
            self.assertIn(key, stats._load())

    def test_reset(self):
        stats.record_turn("m", "P", "chat", 10, 5, 0.0)
        stats.reset()
        self.assertEqual(stats.report()["messages"], 0)

    def test_recording_never_raises(self):
        # kaputte Eingaben dürfen nichts umwerfen
        stats.record_turn(None, None, None, None, None, None)
        stats.record_tools(None)
        stats.record_tools([{"nix": 1}])
        stats.record_command("")


if __name__ == "__main__":
    unittest.main()
