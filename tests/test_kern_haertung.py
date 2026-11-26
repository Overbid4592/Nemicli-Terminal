"""Härtung von main/core: zwei Fenster am selben Chat, Profil-Ordner beim Anlegen und Löschen, neutraler
Nutzername, cachebarer Hintergrund-Hinweis, Befehle mit Zeilenumbruch, Test-Isolation der Config."""
import ast
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "engines", "tools", "ui", "tests"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import chatstore            # noqa: E402
import commands             # noqa: E402
import config               # noqa: E402
import hintergrund          # noqa: E402
import main_quelle          # noqa: E402
import paths                # noqa: E402
import persoenlichkeiten as PS  # noqa: E402
import profilordner         # noqa: E402


def _verlauf(n, wer="x"):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{wer} {i}"} for i in range(n)]


class ZweiFenster(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.ort = self.tmp / "chats"
        for p in (mock.patch.object(chatstore, "CHATS_DIR", self.ort), mock.patch.dict(chatstore._EIGEN, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def _fremd_schreiben(self, nr, messages):
        """Ein anderes Fenster schreibt – an diesem Prozess vorbei."""
        c = chatstore._verbinden(self.ort)
        try:
            chatstore._schreiben(c, nr, messages, ["p"], "m", "2026-01-01 00:00")
            c.commit()
        finally:
            c.close()

    def test_fremde_runde_bleibt_eigene_wird_neuer_chat(self):
        basis = _verlauf(4)
        self.assertEqual(chatstore.save(1, basis, ["a"], "m"), 1)
        fremd = basis + _verlauf(2, "fremd")
        self._fremd_schreiben(1, fremd)
        eigen = basis + _verlauf(2, "eigen")
        nr = chatstore.save(1, eigen, ["a", "b"], "m")
        self.assertNotEqual(nr, 1)
        self.assertEqual(chatstore.load(1)["messages"], fremd)          # nichts gelöscht
        self.assertEqual(chatstore.load(nr)["messages"], eigen)
        self.assertEqual(chatstore.save(nr, eigen + _verlauf(2, "weiter"), ["a", "b", "c"], "m"), nr)

    def test_eigener_ausgelagerter_anfang_kommt_mit(self):
        alles = _verlauf(10)
        chatstore.save(1, alles, ["a"], "m")
        self._fremd_schreiben(1, alles + _verlauf(2, "fremd"))
        nr = chatstore.save(1, alles[6:] + _verlauf(2, "eigen"), ["a", "b"], "m")   # Kontext aufgeräumt
        self.assertEqual(chatstore.load(nr)["messages"], alles + _verlauf(2, "eigen"))

    def test_gleiche_neue_nummer_in_zwei_fenstern(self):
        self._fremd_schreiben(5, _verlauf(2, "fremd"))
        nr = chatstore.save(5, _verlauf(2, "eigen"), ["a"], "m")
        self.assertNotEqual(nr, 5)
        self.assertEqual(chatstore.load(5)["messages"], _verlauf(2, "fremd"))

    def test_fortgesetzt_und_ohne_fremde_aenderung_bleibt_es_ein_chat(self):
        self._fremd_schreiben(3, _verlauf(4))
        chatstore.load(3, merken=True)                                  # Start: geht hier weiter
        self.assertEqual(chatstore.save(3, _verlauf(6), ["a"], "m"), 3)
        self.assertEqual(chatstore.save(3, _verlauf(4), ["a"], "m"), 3)    # Runde zurückgenommen: eigene Sache
        self.assertEqual(len(chatstore.load(3)["messages"]), 4)

    def test_main_uebernimmt_die_neue_nummer(self):
        quelle = main_quelle.text()
        knoten = next(k for k in ast.walk(ast.parse(quelle))
                      if isinstance(k, ast.AsyncFunctionDef) and k.name == "_chat_speichern")
        meldungen = []
        ns = {"asyncio": asyncio, "chatstore": NS(save=lambda *a, **k: 9), "ui": NS(info=meldungen.append)}
        exec(compile(ast.Module([knoten], []), "_chat_speichern", "exec"), ns)
        ctx = NS(current_chat=4, current_prompts=["a"], model="m", backend=NS(messages=[]),
                 sync_session=lambda: meldungen.append("sync"))
        asyncio.run(ns["_chat_speichern"](ctx))
        self.assertEqual(ctx.current_chat, 9)
        self.assertIn("sync", meldungen)
        self.assertEqual(quelle.count("asyncio.to_thread(chatstore.save"), 1)      # alle Wege über _chat_speichern


class Profilordner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.wurzel = tmp / "Profile"
        self.wurzel.mkdir()
        for p in (mock.patch.object(PS, "DIR", tmp / "Persoenlichkeiten"),
                  mock.patch.object(config, "_FILE", tmp / "cfg.json"),
                  mock.patch.object(profilordner, "wurzel", lambda: self.wurzel)):
            p.start()
            self.addCleanup(p.stop)
        PS.ensure_dir()

    def test_geraetenamen(self):
        self.assertEqual(profilordner.ordnername("Con"), "Con_")
        self.assertEqual(profilordner.ordnername("nul"), "nul_")
        self.assertEqual(profilordner.ordnername("Conny"), "Conny")
        self.assertEqual(PS.slug("Con"), "con-1")

    def test_neuer_name_erbt_keinen_liegengebliebenen_ordner(self):
        (self.wurzel / "Ada" / "Chats").mkdir(parents=True)
        pfad = PS.create("Ada", "x")
        self.assertNotEqual(profilordner.ordnername(PS.list_all()[pfad.stem].name), "Ada")

    def test_loeschen_legt_den_ordner_beiseite(self):
        pfad = PS.create("Bea", "x")
        p = PS.list_all()[pfad.stem]
        (self.wurzel / "Bea" / "Chats").mkdir(parents=True)
        (self.wurzel / "Bea" / "Chats" / "chat_bea.db").write_bytes(b"x")
        beiseite = PS.delete(p)
        self.assertFalse((self.wurzel / "Bea").exists())
        self.assertTrue(beiseite.name.startswith("_geloescht_Bea_"))
        self.assertTrue((beiseite / "Chats" / "chat_bea.db").exists())      # nichts gelöscht
        neu = PS.create("Bea", "y")
        self.assertEqual(PS.list_all()[neu.stem].name, "Bea")              # Name ist wieder frei

    def test_geteilter_ordner_bleibt(self):
        (PS.DIR / "ole.md").write_text("# Ole\n", encoding="utf-8")
        (PS.DIR / "ole-2.md").write_text("# Ole!\n", encoding="utf-8")    # ältere Kollision: derselbe Ordner
        (self.wurzel / "Ole").mkdir()
        self.assertIsNone(PS.delete(PS.list_all()["ole"]))
        self.assertTrue((self.wurzel / "Ole").exists())


class Neutral(unittest.TestCase):
    def test_nutzername_ohne_eintrag(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(config, "_FILE", Path(t) / "c.json"), \
                mock.patch.dict(os.environ, {"USERNAME": "konto_x", "USER": "konto_x"}):
            self.assertEqual(PS.nutzername(), "Nutzer")
            config.update(nutzername="test_user")
            self.assertEqual(PS.nutzername(), "test_user")

    def test_kein_festes_startmodell(self):
        self.assertNotIn("claude-sonnet-4-6", main_quelle.text())

    def test_kugel_schalter_ueber_update(self):
        self.assertNotIn("config.save(_c)", main_quelle.text())

    def test_befehle_ordner_in_den_daten(self):
        import persona
        self.assertEqual(persona._DATEN, str(paths.DATEN))
        self.assertIn(f"{paths.DATEN}\\Befehle\\aufraeumen.ps1", persona._REGELN)
        self.assertNotIn("NemiCLI-Projekt:", persona._REGELN)


class Kleinigkeiten(unittest.TestCase):
    def test_hintergrund_hinweis_bleibt_gleich(self):
        hintergrund._test_reset()
        self.addCleanup(hintergrund._test_reset)
        hintergrund._AUFGABEN[1] = {"nr": 1, "tool": "video_erstellen", "titel": "🎬 Video", "start": time.time() - 5,
                                    "ende": None}
        vorher = hintergrund.prompt_hinweis()
        with mock.patch.object(hintergrund.time, "time", return_value=time.time() + 125):
            self.assertEqual(vorher, hintergrund.prompt_hinweis())
        self.assertIn("Uhr)", vorher)

    def test_parse_am_zeilenumbruch(self):
        self.assertEqual(commands.parse("/bild\neine Katze\nim Gras"), ("/bild", "eine Katze\nim Gras"))
        self.assertEqual(commands.parse("/bild  eine Katze"), ("/bild", "eine Katze"))
        self.assertEqual(commands.parse("/help"), ("/help", ""))
        self.assertEqual(commands.parse("hallo"), (None, "hallo"))

    def test_daten_inhalt_vollstaendig(self):
        for name in ("Charaktergalerie", "Doku", "Export"):
            self.assertIn(name, paths.DATEN_INHALT)

    def test_config_im_testlauf_umgelenkt(self):
        self.assertTrue(paths._testlauf())
        self.assertEqual(config._ROOT, paths.TESTDATEN)
        self.assertEqual(config._ENV.parent, paths.TESTDATEN)

    def test_lora_feste_liste(self):
        spec = (ROOT / "NemiCLI.spec").read_text(encoding="utf-8")
        self.assertNotIn('"lora").glob', spec)


if __name__ == "__main__":
    unittest.main()
