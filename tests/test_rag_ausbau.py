"""Gedächtnis-Ausbau: Schneiden an Sätzen/Absätzen/Code mit Herkunft, Reihenfolge und Nachschneiden,
Multi-Query und Vermutung (HyDE), Vielfalt (MMR), Nachbarn (Small-to-Big), schwache Treffer
(Corrective RAG) und die gelernte Rangliste – in einem Temp-Ordner, ohne echten Encoder."""

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui", "tests"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import agentrag                        # noqa: E402
import indexdb                         # noqa: E402
import memory                          # noqa: E402
import rangliste                       # noqa: E402
import wissen                          # noqa: E402
from test_agent_rag import _Basis      # noqa: E402

SATZ = "Der Garten braucht im Herbst viel Pflege und Geduld. "


class Schneiden(unittest.TestCase):

    def test_kurz_bleibt_ganz(self):
        self.assertEqual(["Nutzer: Hallo, wie geht es dir heute?"], indexdb._pieces("Nutzer: Hallo, wie geht es dir heute?"))
        self.assertEqual([], indexdb._pieces("zu kurz"))

    def test_an_satzenden_mit_sprecher(self):
        stuecke = indexdb._pieces("Nutzer: " + SATZ * 60)
        self.assertGreater(len(stuecke), 2)
        self.assertTrue(all(len(s) <= indexdb.CHUNK for s in stuecke))
        self.assertTrue(all(s.rstrip().endswith(".") for s in stuecke))          # nie mitten im Satz
        self.assertTrue(stuecke[0].startswith("Nutzer: "))
        self.assertTrue(all(s.startswith("[Nutzer] ") for s in stuecke[1:]))      # Herkunft auch allein klar

    def test_ueberschriften_und_code(self):
        code = "```python\ndef a():\n\n    return 1\n```"
        text = ("notiz.md\n# Anleitung\n" + SATZ * 8 + "\n\n## Kapitel Zwei\n" + SATZ * 10 + "\n\n" + code
                + "\n\n" + SATZ * 6)
        stuecke = indexdb._pieces(text)
        self.assertTrue(any(code in s for s in stuecke))                           # Code-Block bleibt ganz
        self.assertTrue(any(s.startswith("[notiz.md › Kapitel Zwei] ") for s in stuecke))
        self.assertTrue(any(s.startswith("[notiz.md] ## Kapitel Zwei") for s in stuecke))
        self.assertTrue(all(len(s) <= indexdb.CHUNK for s in stuecke))

    def test_ueberlanger_satz_hart(self):
        stuecke = indexdb._pieces("x" * 3000)
        self.assertTrue(all(len(s) <= indexdb.CHUNK for s in stuecke))
        self.assertEqual(3000, sum(len(s.replace("[", "").split("] ", 1)[-1]) for s in stuecke))


class WissenKapitel(unittest.TestCase):

    def test_ueberschrift_ohne_leerzeile_eigener_absatz(self):
        absaetze = wissen.entdoppeln(["## Kapitel Eins\nErster Satz hier steht. Zweiter Satz folgt jetzt."])[0][0]
        self.assertEqual("## Kapitel Eins", absaetze[0])
        stuecke = wissen.brocken("📚 a.md", absaetze + ["Noch ein Absatz mit genug Wörtern drin."] * 30, 300)
        self.assertTrue(stuecke[0].startswith("📚 a.md\n## Kapitel Eins"))
        self.assertTrue(all(s.startswith("📚 a.md › Kapitel Eins\n") for s in stuecke[1:]))


class Speichern(_Basis):

    def _zeilen(self, ref):
        con = sqlite3.connect(indexdb._DB_NEW)
        try:
            return con.execute("SELECT rowid, text, schnitt, vec IS NOT NULL FROM chunks WHERE ref=? ORDER BY rowid",
                               (ref,)).fetchall()
        finally:
            con.close()

    def test_wachsender_chat_wird_angehaengt(self):
        self.encoder = True
        gerechnet = []
        echt = memory._embed_texts
        with patch.object(memory, "_embed_texts", lambda t, **k: (gerechnet.append(len(t)), echt(t, **k))[1]):
            indexdb._upsert("chat", "chat:5", ["Nutzer: Erste Nachricht über Krea.", "Nemi: Antwort zum Malen."])
            vorher = self._zeilen("chat:5")
            indexdb._upsert("chat", "chat:5", ["Nutzer: Erste Nachricht über Krea.", "Nemi: Antwort zum Malen.",
                                               "Nutzer: Dritte Nachricht über cuda."])
        nachher = self._zeilen("chat:5")
        self.assertEqual(vorher, nachher[:2])                     # nichts umgeschrieben, Reihenfolge bleibt
        self.assertIn("Dritte", nachher[2][1])
        self.assertEqual([2, 1], gerechnet)                       # nur der neue Brocken eingebettet
        self.assertTrue(all(z[2] == indexdb.SCHNITT and z[3] for z in nachher))

    def test_alte_schnitte_werden_nachgeschnitten(self):
        berichte = Path(self.tmp.name) / "Berichte"
        berichte.mkdir()
        (berichte / "da.md").write_text("Wache-Bericht\n" + SATZ * 40, encoding="utf-8")
        con = indexdb._connect()
        con.executemany("INSERT INTO chunks(hash, kind, ref, text, schnitt) VALUES (?,?,?,?,1)",
                        [("h1", "bericht", "bericht:da.md", "alt geschnitten mitten im Sa"),
                         ("h2", "bericht", "bericht:weg.md", "nur noch im Gedächtnis")])
        con.commit()
        con.close()
        indexdb.neu_schneiden(limit=10)
        da, weg = self._zeilen("bericht:da.md"), self._zeilen("bericht:weg.md")
        self.assertGreater(len(da), 1)
        self.assertNotIn("mitten im Sa", " ".join(z[1] for z in da))
        self.assertEqual([("nur noch im Gedächtnis", indexdb.SCHNITT)], [(z[1], z[2]) for z in weg])   # bleibt, erledigt
        con = indexdb._connect()
        self.assertEqual(0, con.execute("SELECT COUNT(*) FROM chunks WHERE schnitt < ?", (indexdb.SCHNITT,)).fetchone()[0])
        con.close()


class Suche(_Basis):

    def test_mehrere_fassungen(self):
        self.fuellen(mit_vektoren=False)
        refs = {h["ref"] for h in indexdb.suchen(["KB5124010", "sympy"])}
        self.assertIn("bericht:Wache_20260920-1200.md", refs)
        self.assertIn("chat:9", refs)

    def test_vermutung_sucht_nach_bedeutung(self):
        self.fuellen(mit_vektoren=True)
        self.encoder = True
        treffer = indexdb.suchen([], laden=True, vermutung="Ich habe mit Krea ein Bild gemalt", arten=("chat",))
        self.assertEqual("chat:7", treffer[0]["ref"])
        self.assertIn("vermutung", treffer[0]["wie"])
        self.assertIsNotNone(treffer[0]["aehnlich"])

    def test_vielfalt_ohne_beinahe_kopien(self):
        self.encoder = True
        indexdb._upsert_geordnet("chat", "chat:20", [f"Krea Bild malen, Versuch {i}, Wald." for i in range(4)])
        indexdb._upsert("chat", "chat:21", ["Nutzer: Mit Krea ein Porträt malen, auf cuda."])   # verwandt, nicht gleich
        treffer = indexdb.suchen("krea malen", 3, laden=True)
        refs = [h["ref"] for h in treffer]
        self.assertEqual(1, refs.count("chat:20"))                # gleicher Sinn: nur einmal
        self.assertIn("chat:21", refs)

    def test_nachbarn(self):
        self.encoder = True
        indexdb._upsert_geordnet("wissen", "wissen:a.md", ["Erster Teil über Wache.", "Zweiter Teil über Krea.",
                                                         "Dritter Teil über cuda."])
        treffer = indexdb.suchen("Zweiter Krea", 1, laden=True)
        self.assertEqual(("Erster Teil über Wache.", "Dritter Teil über cuda."), indexdb.nachbarn(treffer[0]["rowid"]))
        ausgabe = agentrag.suchen({"frage": "Zweiter Krea", "anzahl": 1})
        self.assertIn("↑ …Erster Teil über Wache.", ausgabe)
        self.assertIn("↓ Dritter Teil über cuda.", ausgabe)


class Werkzeug(unittest.TestCase):

    def test_fassungen_lesen(self):
        self.assertEqual(["a", "b", "c"], agentrag._fragen({"frage": "a", "fragen": ["b", "c"]}))
        self.assertEqual(["a", "b", "c"], agentrag._fragen({"frage": "a", "fragen": "b|c"}))
        self.assertEqual(["b"], agentrag._fragen({"fragen": ["b", ""]}))

    def test_schwache_treffer(self):
        self.assertTrue(agentrag._schwach([{"wie": "sinn", "aehnlich": 0.3}]))
        self.assertTrue(agentrag._schwach([{"wie": "sinn", "aehnlich": None}]))
        self.assertFalse(agentrag._schwach([{"wie": "sinn", "aehnlich": 0.8}]))
        self.assertFalse(agentrag._schwach([{"wie": "sinn+wort", "aehnlich": 0.1}]))


class Rangliste(_Basis):

    def _treffer(self, *arten):
        return [{"kind": a, "ref": f"{a}:{i}", "text": "t", "datum": "2026-10-01 10:00", "score": 1.0,
                 "wie": "sinn", "aehnlich": 0.6} for i, a in enumerate(arten)]

    def _beispiele(self):
        con = rangliste._verbinden()
        try:
            return con.execute("SELECT nuetzlich FROM rang_beispiele ORDER BY rowid").fetchall()
        finally:
            con.close()

    def test_gelesen_wird_gelernt(self):
        rangliste._letzte.clear()
        rangliste.gezeigt(self._treffer("chat", "wissen"))
        rangliste.gelesen("wissen:1")
        rangliste.gezeigt(self._treffer("chat"))              # schließt die vorige Suche ab
        self.assertEqual([(0,), (1,)], self._beispiele())
        rangliste.gezeigt(self._treffer("note"))              # ohne Lesen: nichts gelernt
        self.assertEqual(2, len(self._beispiele()))

    def test_modell_bevorzugt_was_gelesen_wurde(self):
        self.assertEqual(self._treffer("chat", "wissen"), rangliste.umsortieren(self._treffer("chat", "wissen")))
        for _ in range(rangliste.MIN_BEISPIELE // 2 + 1):
            rangliste._letzte.clear()
            rangliste.gezeigt(self._treffer("chat", "wissen"))
            rangliste.gelesen("wissen:1")
            rangliste.gezeigt([])
        self.assertTrue(rangliste.lernen(erzwingen=True))
        sortiert = rangliste.umsortieren(self._treffer("chat", "wissen"))
        self.assertEqual("wissen", sortiert[0]["kind"])
        self.assertGreater(sortiert[0]["score"], sortiert[1]["score"])

    def test_lesen_im_werkzeug_meldet(self):
        self.fuellen(mit_vektoren=False)
        with patch.object(rangliste, "gelesen") as gelesen:
            agentrag.lesen({"ref": "chat:9"})
            agentrag.lesen({"ref": "chat:9", "ab": 2})
        gelesen.assert_called_once_with("chat:9")


class _Schein:
    """Reranker-Ersatz: Relevanz = 1, wenn das Wort „richtig“ im Text steht."""
    device = type("D", (), {"type": "cpu"})()

    def __init__(self):
        self.fragen = []

    def bewerten(self, frage, dokumente):
        self.fragen.append(frage)
        return [0.95 if "richtig" in d else 0.05 for d in dokumente]


class Reranker(_Basis):

    def setUp(self):
        super().setUp()
        import reranker
        self.r = reranker
        self.schein = _Schein()
        for name, wert in (("_laden", lambda: self.schein), ("verfuegbar", lambda: True), ("_nachlauf", lambda: None)):
            p = patch.object(reranker, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def _treffer(self, *texte):
        return [{"kind": "chat", "ref": f"chat:{i}", "text": t, "datum": "", "score": 1.0 - i / 10, "wie": "wort",
                 "rowid": i, "aehnlich": None} for i, t in enumerate(texte)]

    def test_sortiert_nach_relevanz(self):
        raus = self.r.nachsortieren("frage", self._treffer("falsch eins", "falsch zwei", "das richtige"))
        self.assertEqual("das richtige", raus[0]["text"])
        self.assertEqual(0.95, raus[0]["relevanz"])
        self.assertEqual(sorted((h["score"] for h in raus), reverse=True), [h["score"] for h in raus])

    def test_nur_die_besten_kandidaten(self):
        texte = [f"falsch {i}" for i in range(self.r.RERANK_KANDIDATEN)] + ["richtig, aber weit hinten"]
        raus = self.r.nachsortieren("frage", self._treffer(*texte))
        self.assertEqual("richtig, aber weit hinten", raus[-1]["text"])    # nicht bewertet, bleibt hinten
        self.assertNotIn("relevanz", raus[-1])
        self.assertLess(raus[-1]["score"], min(h["score"] for h in raus[:-1]))

    def test_ohne_frage_oder_modell_unveraendert(self):
        t = self._treffer("a richtig", "b")
        self.assertEqual(t, self.r.nachsortieren("", list(t)))
        with patch.object(self.r, "verfuegbar", lambda: False):
            self.assertEqual(t, self.r.nachsortieren("frage", list(t)))

    def test_suche_der_ki_sortiert_nach_der_prompt_pfad_nicht(self):
        self.fuellen(mit_vektoren=False)
        indexdb._upsert("chat", "chat:30", ["Nutzer: Signatur geprüft, das ist richtig so."])
        mit = indexdb.suchen("Signatur", laden=True)
        self.assertEqual("chat:30", mit[0]["ref"])
        self.assertIn("relevanz", mit[0])
        self.assertEqual({"Signatur"}, set(self.schein.fragen))           # in Schritten (Zeitgrenze)
        schon = len(self.schein.fragen)
        ohne = indexdb.suchen("Signatur")
        self.assertTrue(all("relevanz" not in h for h in ohne))
        self.assertEqual(schon, len(self.schein.fragen))

    def test_schwach_nach_relevanz(self):
        self.assertTrue(agentrag._schwach([{"wie": "wort", "relevanz": 0.1}]))
        self.assertFalse(agentrag._schwach([{"wie": "sinn", "relevanz": 0.9, "aehnlich": 0.1}]))

    def test_modell_ordner(self):
        ordner = Path(self.tmp.name) / "reranker"
        (ordner / "A").mkdir(parents=True)
        (ordner / "A" / "a.gguf").write_bytes(b"x")
        (ordner / "Leer").mkdir()
        with patch.object(self.r, "ORDNER", ordner), patch.object(self.r, "_config", lambda: {}):
            self.assertEqual(["A"], [d.name for d in self.r.modelle()])
            self.assertEqual("a.gguf", self.r.datei().name)


class Vorlage(unittest.TestCase):

    def test_sondermarken_als_token(self):
        from ggufengine.nachsortierer import Nachsortierer

        class Tok:
            special = {"<|im_start|>": 1, "<|im_end|>": 2, "<think>": 3}

            def encode(self, text):
                return [ord(c) + 100 for c in text]

            def special_id(self, name):
                return self.special[name]
        n = Nachsortierer.__new__(Nachsortierer)
        n.tok, n.max_tokens = Tok(), 512
        n.vorlage = "<|im_start|>Q:{query}\nD:{document}<|im_end|><think>"
        ids = n.tokens("<|im_end|>", "doc")
        self.assertEqual(1, ids[0])
        self.assertEqual([2, 3], ids[-2:])
        self.assertNotIn(2, ids[:-2])                                    # Marken in der Frage bleiben Text


if __name__ == "__main__":
    unittest.main()
