"""Offline-Tests: datei_lesen nach Kontext, Malen der Wache im Kindprozess (engines/malkind.py),
Bibliothekar im eigenen Prozess (tools/bibkind.py).

Das Kind läuft hier als Faden im selben Prozess (gleiches Datei-Protokoll) – kein echtes NemiCLI, keine echten Daten.

python -m unittest discover -s tests -p test_eigene_prozesse.py
"""

import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import actions      # noqa: E402
import bibkind      # noqa: E402
import indexdb      # noqa: E402
import malkind      # noqa: E402
import memory       # noqa: E402


class Fadenkind:
    """Wie subprocess.Popen, aber das Kind läuft als Faden: hauptprogramm(argv) mit dem Datei-Protokoll."""

    def __init__(self, ziel, argv):
        self.returncode = None
        self.pid = 0

        def lauf():
            self.returncode = ziel(argv)
        self._t = threading.Thread(target=lauf, daemon=True)
        self._t.start()

    def poll(self):
        return None if self._t.is_alive() else self.returncode

    def kill(self):
        pass

    def wait(self, timeout=None):
        self._t.join(timeout)
        return self.returncode


class Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        self._env = dict(os.environ)
        self._gpu = memory.NUR_GPU

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        memory.NUR_GPU = self._gpu
        self._tmp.cleanup()


class DateiLesen(Basis):
    def test_grenze_folgt_dem_kontext(self):
        with mock.patch.object(actions, "KONTEXT_TOKEN", 0):
            self.assertEqual(actions.lese_grenze(), actions.MAX_OUT)
        with mock.patch.object(actions, "KONTEXT_TOKEN", 128_000):
            self.assertEqual(actions.lese_grenze(), 96_000)

    def test_lange_datei_kommt_in_einem_stueck(self):
        p = self.t / "lang.md"
        p.write_text("".join(f"Zeile {i} mit etwas Text\n" for i in range(2000)), encoding="utf-8")
        with mock.patch.object(actions, "_read_guard", return_value=None), \
                mock.patch.object(actions, "KONTEXT_TOKEN", 0):
            kurz = actions._datei_lesen({"pfad": str(p)})
        with mock.patch.object(actions, "_read_guard", return_value=None), \
                mock.patch.object(actions, "KONTEXT_TOKEN", 128_000):
            ganz = actions._datei_lesen({"pfad": str(p)})
        self.assertIn("gekürzt", kurz)
        self.assertNotIn("gekürzt", ganz)
        self.assertIn("Zeile 1999", ganz)


class MalKind(Basis):
    def test_nur_in_der_wache_und_nie_im_kind(self):
        modes = types.SimpleNamespace(_wache_dienst=False)
        with mock.patch.dict(sys.modules, {"modes": modes}):
            self.assertFalse(malkind.aktiv())
            modes._wache_dienst = True
            with mock.patch("config.load", return_value={}):
                self.assertTrue(malkind.aktiv())
            with mock.patch("config.load", return_value={"bild_eigener_prozess": False}):
                self.assertFalse(malkind.aktiv())
            os.environ[malkind.ENV] = "1"
            self.assertFalse(malkind.aktiv())

    def test_befehl_startet_main_mit_malkind(self):
        b = malkind.befehl(self.t)
        self.assertEqual(b[-3:], ["--malkind", str(self.t), str(os.getpid())])
        self.assertTrue(b[1].endswith("main.py"))

    def _mit_kind(self, fake_imagegen):
        def starten(o):
            if malkind._kind is None or malkind._kind.poll() is not None:
                o.mkdir(parents=True, exist_ok=True)
                malkind._kind = Fadenkind(malkind.hauptprogramm, [str(o), str(os.getpid())])
        return [mock.patch.object(malkind, "ordner", return_value=self.t / "mk"),
                mock.patch.object(malkind, "_starten", side_effect=starten),
                mock.patch.object(malkind, "_ruhe_s", return_value=0.5),
                mock.patch.dict(sys.modules, {"imagegen": fake_imagegen})]

    def test_auftrag_mit_status_und_ergebnis(self):
        aufrufe = []

        def paint(prompt, on_status=None, **kw):
            aufrufe.append((prompt, kw))
            on_status("Schritt 1/2")
            on_status("Schritt 2/2")
            return "C:/bilder/x.png"
        fake = types.SimpleNamespace(paint=paint, gesicht_nachbessern=lambda path, on_status=None: path + ".besser")
        gesehen = []
        patches = self._mit_kind(fake)
        for p in patches:
            p.start()
        try:
            pfad = malkind.auftrag("paint", {"prompt": "ein Haus", "size": [832, 1216], "seed": 7}, gesehen.append)
            zweit = malkind.auftrag("gesicht_nachbessern", {"path": "C:/bilder/x.png"})
            malkind._kind.wait(5)
        finally:
            for p in reversed(patches):
                p.stop()
            malkind._kind = None
        self.assertEqual(pfad, "C:/bilder/x.png")
        self.assertEqual(zweit, "C:/bilder/x.png.besser")
        self.assertEqual(gesehen, ["Schritt 1/2", "Schritt 2/2"])
        self.assertEqual(aufrufe[0][1]["size"], (832, 1216))          # Liste wird wieder zum Tupel
        self.assertEqual(list((self.t / "mk").glob("*")), [])          # Auftragsdateien aufgeräumt

    def test_fehler_im_kind_kommt_als_runtimeerror(self):
        def paint(prompt, on_status=None, **kw):
            raise MemoryError("CUDA out of memory")
        patches = self._mit_kind(types.SimpleNamespace(paint=paint, gesicht_nachbessern=None))
        for p in patches:
            p.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "MemoryError: CUDA out of memory"):
                malkind.auftrag("paint", {"prompt": "x"})
            malkind._kind.wait(5)
            self.assertEqual(malkind._kind.returncode, 1)             # nach einem Fehler endet das Kind
        finally:
            for p in reversed(patches):
                p.stop()
            malkind._kind = None

    def test_unbekannte_funktion_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            malkind.auftrag("os.system", {})

    def test_imagegen_gibt_in_der_wache_an_das_kind(self):
        import imagegen
        malen = imagegen.paint.__wrapped__      # ohne GPU-Hülle: sie pausiert sonst den Encoder späterer Tests
        with mock.patch.object(imagegen, "backend", return_value=imagegen.KREA), \
                mock.patch.object(malkind, "aktiv", return_value=True), \
                mock.patch.object(malkind, "auftrag", return_value="C:/b.png") as auftrag:
            self.assertEqual(malen("ein Baum", size=(512, 512), figur=True), "C:/b.png")
        fn, kw, _ = auftrag.call_args[0]
        self.assertEqual((fn, kw["prompt"], kw["size"], kw["figur"]), ("paint", "ein Baum", [512, 512], True))


class BibKind(Basis):
    def setUp(self):
        super().setUp()
        self._stand = dict(indexdb._chat_stand), dict(indexdb._nachgeholt)

    def tearDown(self):
        indexdb._chat_stand.clear()
        indexdb._chat_stand.update(self._stand[0])
        indexdb._nachgeholt.clear()
        indexdb._nachgeholt.update(self._stand[1])
        indexdb.SIGNAL_DATEI = None
        super().tearDown()

    def test_unterbrechen_ueber_datei(self):
        indexdb.SIGNAL_DATEI = self.t / "unterbrechen"
        with mock.patch.object(indexdb, "_lauf_marke", indexdb._unterbrechung):
            self.assertFalse(indexdb._unterbrochen())
            (self.t / "unterbrechen").touch()                     # so meldet der Chat-Prozess eine neue Runde
            self.assertTrue(indexdb._unterbrochen())
        with mock.patch.object(indexdb, "_lauf_marke", None):     # /einlesen: nie unterbrechen
            self.assertFalse(indexdb._unterbrochen())

    def test_unterbrechen_legt_die_datei_an(self):
        indexdb.SIGNAL_DATEI = self.t / "unterbrechen"
        indexdb.unterbrechen()
        self.assertTrue((self.t / "unterbrechen").exists())

    def test_lauf_im_kind_mit_merkzettel_hin_und_zurueck(self):
        indexdb._chat_stand["chat:Test:1"] = 100
        gesehen = []

        def after_turn(chat_id, status=None, alles=False, chat_erzwingen=False, nur_kurz=False):
            status("Chat #1 · Brocken 1/2")
            self.assertEqual(indexdb._chat_stand["chat:Test:1"], 100)   # Merkzettel kam im Kind an
            indexdb._chat_stand["chat:Test:1"] = 30_000
            indexdb._nachgeholt["chat:Test:2"] = "neu"
            return 5
        zaehler = iter([{"brocken": 10, "ohne_vektor": 2}, {"brocken": 15, "ohne_vektor": 2}])

        def popen(cmd, **kw):
            return Fadenkind(bibkind.hauptprogramm, cmd[-2:])
        with mock.patch.object(bibkind, "ordner", return_value=self.t / "bib"), \
                mock.patch.object(bibkind, "aktiv", return_value=True), \
                mock.patch.object(bibkind, "_niedrig"), \
                mock.patch.object(bibkind, "_eltern_beobachten"), \
                mock.patch.object(bibkind.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(indexdb, "after_turn", side_effect=after_turn), \
                mock.patch.object(indexdb, "offen", side_effect=lambda: next(zaehler)):
            res = bibkind.laufen(1, False, gesehen.append, True)
        self.assertEqual(res, {"n": 5, "err": None, "mit_vektor": 5, "neu": 5})
        self.assertEqual(gesehen, ["Chat #1 · Brocken 1/2"])
        self.assertEqual(indexdb._chat_stand["chat:Test:1"], 30_000)    # zurück im Chat-Prozess
        self.assertEqual(indexdb._nachgeholt["chat:Test:2"], "neu")
        self.assertIsNone(indexdb.SIGNAL_DATEI)
        self.assertFalse((self.t / "bib").exists())

    def _kind(self, hat_gpu):
        gesehen = {}

        def after_turn(chat_id, status=None, alles=False, chat_erzwingen=False, nur_kurz=False):
            gesehen["gpu"] = memory.NUR_GPU
            return 0

        def popen(cmd, **kw):
            return Fadenkind(bibkind.hauptprogramm, cmd[-2:])
        with mock.patch.object(bibkind, "ordner", return_value=self.t / "bib"), \
                mock.patch.object(bibkind, "aktiv", return_value=True), \
                mock.patch.object(bibkind, "_niedrig"), \
                mock.patch.object(bibkind, "_eltern_beobachten"), \
                mock.patch.object(memory, "hat_gpu", return_value=hat_gpu), \
                mock.patch.object(bibkind.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(indexdb, "after_turn", side_effect=after_turn), \
                mock.patch.object(indexdb, "offen", return_value={"brocken": 0, "ohne_vektor": 0}):
            bibkind.laufen(1, True, None)
        return gesehen["gpu"]

    def test_kind_rechnet_nur_auf_der_gpu(self):
        self.assertTrue(self._kind(True))

    def test_ohne_grafikkarte_die_cpu(self):
        self.assertFalse(self._kind(False))

    def test_kind_stirbt_ohne_ergebnis(self):
        class Tot:
            pid, returncode = 0, 3

            def poll(self):
                return 3
        with mock.patch.object(bibkind, "ordner", return_value=self.t / "bib"), \
                mock.patch.object(bibkind, "aktiv", return_value=True), \
                mock.patch.object(bibkind.subprocess, "Popen", return_value=Tot()):
            res = bibkind.laufen(None, False, None)
        self.assertIn("unerwartet beendet (Code 3)", res["err"])

    def test_ohne_eigenen_prozess_rechnet_er_hier(self):
        with mock.patch.object(bibkind, "aktiv", return_value=False), \
                mock.patch.object(bibkind, "lauf_hier", return_value={"n": 1}) as hier:
            self.assertEqual(bibkind.laufen(2, True, None), {"n": 1})
        hier.assert_called_once_with(2, True, None, False)

    def test_startet_das_kind_nicht_rechnet_er_hier(self):
        with mock.patch.object(bibkind, "ordner", return_value=self.t / "bib"), \
                mock.patch.object(bibkind, "aktiv", return_value=True), \
                mock.patch.object(bibkind.subprocess, "Popen", side_effect=OSError("fehlt")), \
                mock.patch.object(bibkind, "lauf_hier", return_value={"n": 2}) as hier:
            self.assertEqual(bibkind.laufen(3, False, None), {"n": 2})
        hier.assert_called_once()

    def test_main_nutzt_den_eigenen_prozess(self):
        code = main_quelle.text()
        self.assertIn("bibkind.laufen(chat_id, alles, on_status, erzwingen)", code)
        self.assertIn('"--bibliothekar" in sys.argv', code)
        self.assertIn('"--malkind" in sys.argv', code)


if __name__ == "__main__":
    unittest.main()
