"""Embedding ohne Bremse: Vektoren als float32 in WAL-Datenbank, Umwandeln alter JSON-Vektoren,
Zählen ohne Vektoren, kein Nachladen des Encoders während Bilderserien, Gedächtnissuche einmal
je Runde und Bibliothekar nur in ruhigen Phasen."""
import ast
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import indexdb                          # noqa: E402
import memory                           # noqa: E402
import persona                          # noqa: E402
from test_agent_rag import _Basis       # noqa: E402


class Speicher(_Basis):
    def _roh(self):
        con = indexdb._connect()
        try:
            return con.execute("SELECT typeof(vec), vec FROM chunks WHERE vec IS NOT NULL").fetchall()
        finally:
            con.close()

    def test_float32_und_wal(self):
        self.fuellen(mit_vektoren=True)
        self.assertTrue(all(t == "blob" for t, _v in self._roh()))
        con = indexdb._connect()
        try:
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            con.close()
        self.assertEqual(indexdb.offen()["ohne_vektor"], 0)

    def test_alte_json_vektoren_werden_umgewandelt(self):
        self.fuellen(mit_vektoren=True)
        neu_gebaut = {m[4]: m for m in indexdb._index()["meta"]}
        vorher = {r: tuple(round(float(x), 5) for x in indexdb._index()["mat"][j])
                  for j, r in enumerate(indexdb._index()["vec_zeilen"])}
        con = indexdb._connect()                           # Stand wie vor dem Umbau: JSON-Text
        for rowid, roh in con.execute("SELECT rowid, vec FROM chunks WHERE vec IS NOT NULL").fetchall():
            con.execute("UPDATE chunks SET vec=? WHERE rowid=?", (json.dumps(indexdb.vec_entpacken(roh)), rowid))
        con.commit()
        con.close()
        self.assertTrue(all(t == "text" for t, _v in self._roh()))
        indexdb._CACHE.update({"meta": None, "stamp": None})
        self.assertEqual(len(indexdb._index()["vec_zeilen"]), len(vorher))   # JSON wird weiter gelesen
        self.assertEqual(indexdb.vektoren_umwandeln(), len(vorher))
        self.assertTrue(all(t == "blob" for t, _v in self._roh()))
        ix = indexdb._index()
        nachher = {r: tuple(round(float(x), 5) for x in ix["mat"][j]) for j, r in enumerate(ix["vec_zeilen"])}
        self.assertEqual(vorher, nachher)
        self.assertEqual(neu_gebaut, {m[4]: m for m in ix["meta"]})

    def test_zaehlen_ohne_vektor(self):
        self.fuellen(mit_vektoren=False)
        o = indexdb.offen()
        self.assertEqual(o["ohne_vektor"], o["brocken"])
        self.assertFalse(indexdb._ref_vollstaendig("chat:7"))


class GpuPause(unittest.TestCase):
    def setUp(self):
        p = patch.object(memory, "_gpu_pause_bis", 0.0)
        p.start()
        self.addCleanup(p.stop)

    def test_bilderserie_laedt_den_encoder_nicht_nach(self):
        with patch.object(memory, "encoder_loaded", return_value=False), \
             patch.object(memory, "_get_encoder", side_effect=AssertionError("geladen")) as laden, \
             patch.object(indexdb, "unterbrechen") as unterbrechen:
            memory.gpu_freigeben()
            unterbrechen.assert_called_once()
            self.assertTrue(memory.gpu_pause())
            self.assertIsNone(memory._embed_texts(["Suche"]))
            memory.warm_encoder()                         # startet keinen Faden
            laden.assert_not_called()


class SucheJeRunde(unittest.TestCase):
    def test_einmal_mit_der_nachricht(self):
        with patch.object(indexdb, "guide_block", return_value="\n# Treffer") as block:
            persona.runde_beginnt("Wie war das mit dem Krea-Bild?")
            persona._gedaechtnis_block("langes Werkzeug-Ergebnis " * 200)
            persona._gedaechtnis_block("noch ein Ergebnis")
            block.assert_called_once()
            self.assertEqual(block.call_args.args[0], "Wie war das mit dem Krea-Bild?")
            persona.runde_beginnt("Neue Frage")
            persona._gedaechtnis_block("x")
            self.assertEqual(block.call_count, 2)
        persona.runde_beginnt("")


def _ruhe_funktionen(tui, pause=False):
    """_beschaeftigt und _ruhe_abwarten aus main_dienste.py, mit eigener Uhr statt echter Wartezeit."""
    baum = ast.parse((ROOT / "main_dienste.py").read_text(encoding="utf-8-sig"))
    knoten = [n for n in baum.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name in ("_beschaeftigt", "_ruhe_abwarten")]
    uhr = {"t": 1000.0, "schritte": 0}

    async def schlafen(s):
        uhr["t"] += s
        uhr["schritte"] += 1
        if uhr["schritte"] > 500:
            raise AssertionError("wartet endlos")
        await asyncio.sleep(0)                            # der Schleife wirklich abgeben

    idx = NS(_unterbrechung=0)
    ns = {"asyncio": NS(sleep=schlafen), "time": NS(monotonic=lambda: uhr["t"]), "indexdb": idx,
          "memory": NS(gpu_pause=lambda: pause), "BIB_RUHE_S": 20.0}
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main_dienste.py", "exec"), ns)
    ns["_uhr"], ns["_idx"] = uhr, idx
    return ns


class RuhigePhase(unittest.TestCase):
    def test_wartet_auf_ruhe_uebungsmodus_zaehlt_nicht(self):
        tui = NS(busy=True, _practicing=True, _last_input=1000.0)
        ns = _ruhe_funktionen(tui)
        with patch.dict(sys.modules, {"hintergrund": NS(gpu_belegt=lambda: None)}):
            self.assertFalse(ns["_beschaeftigt"](tui))
            asyncio.run(ns["_ruhe_abwarten"](tui, False))
        self.assertGreaterEqual(ns["_uhr"]["t"] - 1000.0, 20.0)

    def test_bild_oder_runde_haelt_den_bibliothekar_an(self):
        tui = NS(busy=True, _practicing=False, _last_input=0.0)
        ns = _ruhe_funktionen(tui)
        with patch.dict(sys.modules, {"hintergrund": NS(gpu_belegt=lambda: {"tool": "bild_serie"})}):
            self.assertTrue(ns["_beschaeftigt"](NS(busy=False)))
        self.assertTrue(ns["_beschaeftigt"](tui))

        async def ablauf():
            warten = asyncio.ensure_future(ns["_ruhe_abwarten"](tui, True))
            for _ in range(30):
                await asyncio.sleep(0)
            self.assertFalse(warten.done())               # Runde läuft noch
            tui.busy = False
            await warten
        with patch.dict(sys.modules, {"hintergrund": NS(gpu_belegt=lambda: None)}):
            asyncio.run(ablauf())


if __name__ == "__main__":
    unittest.main()


class SuchTeilzeiten(unittest.TestCase):
    def test_langsame_suche_meldet_teile(self):
        import debugbericht
        gemeldet = []
        uhr = iter([0.0, 2.5, 2.5, 2.6] + [2.6] * 20)
        with patch.object(indexdb.time, "perf_counter", lambda: next(uhr)), \
                patch.object(indexdb, "_laufender_chat", lambda: ("", [], 0)), \
                patch.object(indexdb, "search", lambda q, k: []), \
                patch.object(debugbericht, "ereignis", lambda text, **k: gemeldet.append(text)):
            self.assertEqual(indexdb.guide_block("hallo"), "")
        self.assertEqual(len(gemeldet), 1)
        self.assertIn("Chat 2.5 s", gemeldet[0])
        self.assertFalse(hasattr(indexdb._ZEITEN, "messen"))

    def test_schnelle_suche_still(self):
        import debugbericht
        with patch.object(debugbericht, "ereignis") as e:
            indexdb._zeiten_melden({"Index": 0.2, "Einbetten": 0.3})
        e.assert_not_called()


class VergleichSchnell(unittest.TestCase):
    def test_profil_einmal_je_suche(self):
        import numpy as np
        import profilordner
        n = 3000
        rng = np.random.default_rng(0)
        mat = rng.normal(size=(n, 8)).astype("float32") + 3.0          # alle ähnlich: viele über der Schwelle
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        meta = [("chat", f"chat:{'Ada' if i % 2 else 'Bo'}:{i}", "t", "", i) for i in range(n)]
        ix = {"meta": meta, "mat": mat, "vecs": [], "idx": list(range(n))}
        zaehler = []

        def aktiv():
            zaehler.append(1)
            return "Ada"
        with patch.object(indexdb, "_index", lambda warten=False: ix), \
                patch.object(indexdb, "_fts_anfrage", lambda q: ""), \
                patch.object(memory, "encoder_loaded", lambda: True), \
                patch.object(memory, "frage_einbetten", lambda q, **_k: [list(mat[1])]), \
                patch.object(profilordner, "aktiv", aktiv):
            treffer = indexdb.suchen("frage", 5)
        self.assertEqual(len(zaehler), 1)
        self.assertEqual(len(treffer), 5)
        self.assertTrue(all(":Ada:" in h["ref"] for h in treffer))
        self.assertEqual(treffer[0]["ref"], "chat:Ada:1")


class WissenNamen(unittest.TestCase):
    def test_gemerkt_und_neu_nach_aenderung(self):
        import tempfile
        db = Path(tempfile.mkdtemp()) / "memory.db"
        with patch.object(indexdb, "_db_path", lambda: db), patch.dict(indexdb._WISSEN_NAMEN, clear=True):
            con = indexdb._connect()
            con.execute("INSERT INTO chunks(hash, kind, ref, text) VALUES ('a', 'wissen', 'wissen:B.pdf', 't')")
            con.commit()
            plan = con.execute("EXPLAIN QUERY PLAN SELECT DISTINCT ref FROM chunks INDEXED BY idx_chunks_kind_ref "
                               "WHERE kind='wissen'").fetchall()
            self.assertIn("COVERING INDEX", str(plan))
            self.assertEqual(indexdb.wissen_namen(), ["B.pdf"])
            with patch.object(indexdb, "_connect", side_effect=AssertionError("nicht neu gelesen")):
                self.assertEqual(indexdb.wissen_namen(), ["B.pdf"])
            stempel = indexdb._db_stamp()
            con.execute("INSERT INTO chunks(hash, kind, ref, text) VALUES ('b', 'wissen', 'wissen:A.md', 't')")
            con.commit()                                   # landet in der WAL-Datei
            self.assertNotEqual(indexdb._db_stamp(), stempel)
            self.assertEqual(indexdb.wissen_namen(), ["A.md", "B.pdf"])
            con.close()


class WacheHintergrundRunden(unittest.TestCase):
    """Alarm, Blick aufs Spiel und freier Moment laden das Gedächtnis-Modell nicht und halten es nicht wach."""

    def _lage(self, kugel: bool, von_selbst: bool):
        import modes
        import vonsichaus
        vonsichaus.runde(von_selbst)
        self.addCleanup(vonsichaus.runde, False)
        return patch.multiple(modes, _wache_dienst=True, _kugel_runde=kugel)

    def test_wer_zaehlt_als_hintergrund(self):
        import modes
        with self._lage(kugel=True, von_selbst=False):
            self.assertFalse(memory._hintergrund_runde())       # jemand schreibt der Kugel
        with self._lage(kugel=True, von_selbst=True):
            self.assertTrue(memory._hintergrund_runde())        # Blick aufs Spiel, freier Moment
        with self._lage(kugel=False, von_selbst=False):
            self.assertTrue(memory._hintergrund_runde())        # Alarm der Wache
        with patch.object(modes, "_wache_dienst", False):
            self.assertFalse(memory._hintergrund_runde())       # Hauptprogramm

    def test_laedt_nicht_und_haelt_nicht_wach(self):
        with self._lage(kugel=True, von_selbst=True), patch.object(memory, "_get_encoder") as laden, \
                patch.object(memory, "encoder_loaded", return_value=False), patch.object(memory, "_last_use", 123.0):
            memory.warm_encoder()
            memory.release_encoder()
            self.assertEqual(memory._last_use, 123.0)
            laden.assert_not_called()
            self.assertTrue(memory._warm_thread is None or not memory._warm_thread.is_alive())

    def test_chat_mit_der_kugel_haelt_wach(self):
        with self._lage(kugel=True, von_selbst=False), patch.object(memory, "encoder_loaded", return_value=False), \
                patch.object(memory, "_last_use", 123.0):
            memory.release_encoder()
            self.assertGreater(memory._last_use, 123.0)
