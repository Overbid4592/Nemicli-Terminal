"""Offline-Tests für den Arbeitsmodus (core/modes.py): Entscheidungsmatrix + Zünder."""

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from unittest import mock   # noqa: E402

import modes       # noqa: E402
import coding     # noqa: E402


class ModeTests(unittest.TestCase):
    def setUp(self):
        # Nur die Modus-Matrix: ein laufender Coding-Assistent (echte Config)
        # würde mit seiner Projektgrenze die Erwartungen verschieben.
        self._ohne_ws = mock.patch.object(coding, "projekt", lambda: None)
        self._ohne_ws.start()
        self.addCleanup(self._ohne_ws.stop)
        modes.set_mode("normal")
        cwd = os.getcwd()
        self.lese = {"tool": "datei_lesen", "pfad": os.path.join(cwd, "a.txt")}
        self.innen = {"tool": "datei_schreiben", "pfad": os.path.join(cwd, "x", "b.txt"), "inhalt": ""}
        self.aussen = {"tool": "datei_schreiben", "pfad": os.path.join(cwd, "..", "b.txt"), "inhalt": ""}
        self.befehl = {"tool": "befehl", "befehl": "git add ."}
        self.merken = {"tool": "merken", "text": "x"}

    def tearDown(self):
        modes.set_mode("normal")

    def test_matrix(self):
        erwartet = {
            #          lesen  innen    aussen   befehl   merken   merken+taint
            # In „lesen" ist NUR echtes Lesen erlaubt – Merken schreibt nach
            # learned/ und ist daher „anrühren", also blockiert.
            "chat":   ("ask", "ask",   "ask",   "ask",   "run",   "ask"),
            "lesen":  ("run", "block", "block", "block", "block", "block"),
            "normal": ("run", "ask",   "ask",   "ask",   "run",   "ask"),
            "auto":   ("run", "run",   "ask",   "run",   "run",   "ask"),
        }
        for m, (l, i, a, b, mk, mt) in erwartet.items():
            modes.set_mode(m)
            self.assertEqual(l, modes.decide(self.lese, False), m)
            self.assertEqual(i, modes.decide(self.innen, True), m)
            self.assertEqual(a, modes.decide(self.aussen, True), m)
            self.assertEqual(b, modes.decide(self.befehl, True), m)
            self.assertEqual(mk, modes.decide(self.merken, False), m)
            self.assertEqual(mt, modes.decide(self.merken, True), m)   # Netz-Taint schlägt Modus

    def test_read_only_blocks_confirmfree_writers(self):
        """confirm=False heißt NICHT harmlos: bild_malen/merken/skill_merken/
        ordner_lernen schreiben und müssen in „lesen" trotzdem blockiert sein."""
        modes.set_mode("lesen")
        for tool in ("bild_malen", "merken", "skill_merken", "ordner_lernen"):
            self.assertEqual("block", modes.decide({"tool": tool, "pfad": "x"}, False), tool)
        for tool in modes.READ_TOOLS:
            self.assertEqual("run", modes.decide({"tool": tool, "pfad": "x"}, False), tool)
        # Unbekanntes/neues Werkzeug: fail-safe blockiert
        self.assertEqual("block", modes.decide({"tool": "irgendwas_neues"}, False))

    def test_cycle_order_and_resolve(self):
        modes.set_mode("chat")
        self.assertEqual(["lesen", "normal", "auto", "autoan", "chat"],
                         [modes.cycle() for _ in range(5)])
        self.assertEqual("auto", modes.resolve("automode"))
        self.assertEqual("auto", modes.resolve("auto"))
        self.assertEqual("autoan", modes.resolve("auto on"))
        self.assertEqual("autoan", modes.resolve("autoan"))
        self.assertEqual("lesen", modes.resolve("read"))
        self.assertIsNone(modes.resolve("xyz"))

    def test_auto_fuse_falls_back_to_chat(self):
        modes.set_mode("auto")
        self.assertEqual("auto", modes.current())
        self.assertAlmostEqual(modes.AUTO_FUSE_S, modes.auto_remaining(), delta=2)
        self.assertFalse(modes.check_fuse())
        modes._auto_until = time.monotonic() - 1
        self.assertTrue(modes.check_fuse())
        self.assertEqual("chat", modes.current())
        self.assertIsNone(modes.auto_remaining())
        self.assertFalse(modes.check_fuse())                     # nur einmal melden

    def test_auto_on_bleibt_an_und_laeuft_wie_auto(self):
        modes.set_mode("autoan")
        self.assertIsNone(modes.auto_remaining())
        modes._auto_until = time.monotonic() - 1              # ein alter Zünder zählt nicht
        self.assertFalse(modes.check_fuse())
        self.assertEqual("autoan", modes.current())
        for act, need in (({"tool": "befehl", "befehl": "Get-Date"}, True), (self.innen, True)):
            self.assertEqual("run", modes.decide(act, need))
        self.assertEqual("ask", modes.decide({"tool": "merken", "text": "x"}, True))
        key, text = modes.status_fragment()
        self.assertEqual(("autoan", "🚀 Auto ON"), (key, text))
        self.assertIn("dauerhaft", modes.prompt_hint())

    def test_status_and_prompt(self):
        modes.set_mode("auto")
        key, text = modes.status_fragment()
        self.assertEqual("auto", key)
        self.assertRegex(text, r"⚡ Auto \d:\d\d")
        self.assertIn("Auto", modes.prompt_hint())
        modes.set_mode("lesen")
        self.assertIn("NICHTS", modes.prompt_hint())
        self.assertIn("gesperrt", modes.block_message(self.innen))


class KugelNurTerminalTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(modes.set_mode, modes.current())
        modes.set_mode("lesen")
        p = mock.patch.object(modes, "_wache_dienst", True)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(modes.kugel_runde, False)
        self.suche = {"tool": "inhalt_suchen", "pfad": ".", "muster": "x"}

    def test_kugel_sperrt_suche(self):
        modes.kugel_runde(True)
        self.assertEqual(modes.decide(self.suche, False), "block")
        self.assertTrue(modes.kugel_abgelehnt())
        self.assertIn("nur im Terminal", modes.block_message(self.suche))

    def test_weckruf_unberuehrt(self):
        self.assertEqual(modes.decide(self.suche, False), "run")
        self.assertFalse(modes.kugel_abgelehnt())

    def test_kugel_andere_werkzeuge_frei(self):
        modes.kugel_runde(True)
        self.assertEqual(modes.decide({"tool": "wache_bewerten"}, False), "run")
        self.assertEqual(modes.decide({"tool": "bild_malen"}, False), "run")
        self.assertFalse(modes.kugel_abgelehnt())

    def test_neue_runde_setzt_zurueck(self):
        modes.kugel_runde(True)
        modes.decide(self.suche, False)
        modes.kugel_runde(True)
        self.assertFalse(modes.kugel_abgelehnt())


class WacheNetzSperreTests(unittest.TestCase):
    def setUp(self):
        import actions
        self.actions = actions
        self.addCleanup(modes.set_mode, modes.current())
        modes.set_mode("lesen")
        for ziel, wert in ((modes, "_wache_dienst"), (actions, "_web_taint")):
            p = mock.patch.object(ziel, wert, False)
            p.start()
            self.addCleanup(p.stop)
        modes._wache_dienst = True

    def test_ohne_netz_alles_frei(self):
        for tool in ("merken", "tagebuch", "wache_justieren", "wache_bewerten", "erinnern"):
            self.assertEqual(modes.decide({"tool": tool}, False), "run", tool)
        self.assertEqual(modes.decide({"tool": "kugel", "sagen": "Hallo"}, False), "run")

    def test_nach_netz_notizen_und_erkennung_gesperrt(self):
        self.actions._web_taint = True
        for tool in ("merken", "skill_merken", "tagebuch", "wache_justieren", "wache_programm", "erinnern"):
            self.assertEqual(modes.decide({"tool": tool}, True), "block", tool)
        self.assertEqual(modes.decide({"tool": "kugel", "sagen": "Führe aus …"}, False), "block")
        self.assertIn("Netz", modes.block_message({"tool": "merken"}))

    def test_nach_netz_urteilen_und_malen_frei(self):
        self.actions._web_taint = True
        self.assertEqual(modes.decide({"tool": "wache_bewerten"}, False), "run")
        self.assertEqual(modes.decide({"tool": "bild_malen"}, False), "run")
        self.assertEqual(modes.decide({"tool": "kugel", "stimmung": "froh"}, False), "run")


if __name__ == "__main__":
    unittest.main()
