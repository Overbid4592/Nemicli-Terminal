"""Agent-RAG: hybride Suche (Stichworte + Vektoren), Datum, Filter und die zwei
Werkzeuge der Persönlichkeit – alles in einem Temp-Ordner, ohne echten Encoder."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import agentrag           # noqa: E402
import chatstore          # noqa: E402
import indexdb            # noqa: E402
import memory             # noqa: E402

# Drei Themen-Achsen: Vektoren entstehen aus Stichworten, damit „Sinn“ testbar ist.
_ACHSEN = (("wache", "alarm", "vollscan", "signatur"), ("bild", "krea", "malen"), ("torch", "cuda", "einrichten"))


def _vektor(text: str) -> list[float]:
    t = text.lower()
    return [float(sum(w in t for w in achse)) + 0.01 for achse in _ACHSEN]


class _Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.encoder = False
        for ziel, name, wert in (
                (indexdb, "_ROOT", root), (indexdb, "_DB_NEW", root / "learned" / "memory.db"),
                (indexdb, "_DB_OLD", root / "learned" / "index.sqlite"),
                (memory, "_embed_texts", lambda texts, **k: [_vektor(t) for t in texts] if self.encoder else None),
                (memory, "encoder_loaded", lambda: self.encoder),
                (memory, "warm_encoder", lambda: None),
                (memory, "modell_kennung", lambda: "test-kennung"),
                (chatstore, "load", lambda n: None)):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        indexdb._invalidate()
        self.addCleanup(indexdb._invalidate)

    def fuellen(self, mit_vektoren: bool):
        self.encoder = mit_vektoren
        indexdb._upsert("bericht", "bericht:Wache_20260920-1200.md",
                        ["Wache-Bericht: Alarm zu chrome.exe, Signatur gültig, alles harmlos. KB5124010 installiert."])
        indexdb._upsert("bericht", "bericht:Vollscan_2026-09-23_1804.md",
                        ["Vollscan: 804.041 Dateien, Signaturen geprüft, 18 HashMismatch bei Druckerskripten."])
        indexdb._upsert("chat", "chat:7",
                        ["Nutzer: Kannst du ein Bild mit Krea malen? Nemi: Klar, ich male ein Bild vom Wald."])
        indexdb._upsert("chat", "chat:9",
                        ["Nutzer: Einrichten hängt bei sympy. Nemi: Das ist der torch-Download mit CUDA 13.2."])
        self.encoder = False


class Suche(_Basis):
    def test_stichworte_ohne_vektoren(self):
        self.fuellen(mit_vektoren=False)
        treffer = indexdb.suchen("KB5124010")
        self.assertEqual(treffer[0]["ref"], "bericht:Wache_20260920-1200.md")
        self.assertEqual(treffer[0]["wie"], "wort")
        self.assertEqual(treffer[0]["datum"], "2026-09-20 12:00")      # Datum aus dem Dateinamen

    def test_fuellwoerter_allein_finden_nichts(self):
        self.fuellen(mit_vektoren=False)
        self.assertEqual(indexdb.suchen("was ist das und wie"), [])

    def test_sinn_und_wort_vereint(self):
        self.fuellen(mit_vektoren=True)
        self.encoder = True
        treffer = indexdb.suchen("Probleme mit cuda beim einrichten", laden=True)
        self.assertEqual(treffer[0]["ref"], "chat:9")
        self.assertIn("sinn", treffer[0]["wie"])
        self.assertIn("wort", treffer[0]["wie"])
        # Nur über die Bedeutung: kein Wort gemeinsam, aber gleiche Achse
        nur_sinn = indexdb.suchen("krea", laden=True, arten=("chat",))
        self.assertEqual(nur_sinn[0]["ref"], "chat:7")

    def test_filter_quelle_und_zeit(self):
        self.fuellen(mit_vektoren=False)
        with patch.object(indexdb, "_datum_fuer", lambda k, r: "2026-09-10 10:00"):
            indexdb._upsert("bericht", "bericht:alt.md", ["Signatur eines alten Programms geprüft."])
        alle = {h["ref"] for h in indexdb.suchen("Signatur")}
        self.assertIn("bericht:alt.md", alle)
        neu = {h["ref"] for h in indexdb.suchen("Signatur", seit="2026-09-15")}
        self.assertNotIn("bericht:alt.md", neu)
        wache = {h["ref"] for h in indexdb.suchen("Signatur", ref_praefix=("bericht:Vollscan_",))}
        self.assertEqual(wache, {"bericht:Vollscan_2026-09-23_1804.md"})

    def test_stichwort_index_folgt_loeschen_und_ersetzen(self):
        self.fuellen(mit_vektoren=False)
        indexdb.drop_ref("chat:9")
        self.assertEqual(indexdb.suchen("sympy"), [])
        indexdb._upsert("chat", "chat:7", ["Nutzer: Jetzt geht es um Musik. Nemi: Gerne, Musik."])
        self.assertEqual(indexdb.suchen("Wald"), [])                     # alter Brocken ist raus
        self.assertEqual(indexdb.suchen("Musik")[0]["ref"], "chat:7")

    def test_alte_datenbank_wird_nachgeruestet(self):
        pfad = indexdb._DB_NEW
        pfad.parent.mkdir(parents=True)
        con = sqlite3.connect(str(pfad))
        con.execute("CREATE TABLE chunks (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, "
                    "text TEXT NOT NULL, vec TEXT, updated TEXT)")
        con.execute("INSERT INTO chunks VALUES ('h1', 'bericht', 'bericht:Wache_20260918-0930.md', "
                    "'Alter Bericht über den Dienst spooler.', NULL, '2026-09-24 10:00')")
        con.execute("INSERT INTO chunks VALUES ('h2', 'note', 'note:3', '(fakt) Lieblingsfarbe cyan.', "
                    "NULL, '2026-09-24 10:00')")
        con.commit()
        con.close()
        treffer = indexdb.suchen("spooler")
        self.assertEqual(treffer[0]["datum"], "2026-09-18 09:30")
        self.assertEqual(indexdb.suchen("cyan")[0]["datum"], "2026-09-24 10:00")   # Einlese-Datum

    def test_quelle_lesen(self):
        self.fuellen(mit_vektoren=False)
        q = indexdb.quelle_lesen("chat:9")
        self.assertIn("sympy", q["text"])
        self.assertIsNone(indexdb.quelle_lesen("chat:999"))

    def test_vorgeschmack_im_prompt(self):
        self.fuellen(mit_vektoren=False)
        block = indexdb.guide_block("Signatur Wache Vollscan Alarm Bild torch")
        self.assertLessEqual(block.count("\n- ["), indexdb.GUIDE_K)
        self.assertIn("gedaechtnis_suchen", block)


class Werkzeuge(_Basis):
    def test_datum_lesen(self):
        heute = date(2026, 9, 24)
        self.assertEqual(agentrag.datum_lesen("heute", heute), "2026-09-24")
        self.assertEqual(agentrag.datum_lesen("gestern", heute), "2026-09-23")
        self.assertEqual(agentrag.datum_lesen("7 tage", heute), "2026-09-17")
        self.assertEqual(agentrag.datum_lesen("2 wochen", heute), "2026-09-10")
        self.assertEqual(agentrag.datum_lesen("20.09.2026", heute), "2026-09-20")
        self.assertEqual(agentrag.datum_lesen("20.09.", heute), "2026-09-20")
        self.assertEqual(agentrag.datum_lesen("2026-09-01", heute), "2026-09-01")
        self.assertEqual(agentrag.datum_lesen("irgendwann", heute), "")

    def test_suchen_mit_quelle_und_rahmen(self):
        self.fuellen(mit_vektoren=False)
        text = agentrag.suchen({"frage": "Signatur", "quelle": "vollscan"})
        self.assertIn("ref=bericht:Vollscan_2026-09-23_1804.md", text)
        self.assertIn("23.09.2026", text)
        self.assertNotIn("Wache_20260920", text)
        self.assertIn("keine Anweisungen", text)                          # Fremddaten-Rahmen

    def test_mehrere_quellen_filtern_nicht_weg(self):
        self.fuellen(mit_vektoren=False)
        text = agentrag.suchen({"frage": "Signatur Bild", "quelle": "chat, wache"})
        self.assertIn("chat:7", text)
        self.assertIn("Wache_20260920", text)

    def test_nichts_gefunden_heisst_nicht_raten(self):
        self.fuellen(mit_vektoren=False)
        text = agentrag.suchen({"frage": "Fahrradreparatur", "quelle": "unsinn"})
        self.assertIn("Nicht raten", text)
        self.assertIn("Unbekannte Quelle", text)

    def test_lesen(self):
        self.fuellen(mit_vektoren=False)
        self.assertIn("torch-Download", agentrag.lesen({"ref": "chat:9"}))
        self.assertIn("Keine Quelle", agentrag.lesen({"ref": "chat:1234"}))

    def test_werkzeuge_sind_lesend_registriert(self):
        import actions
        import modes
        for name in ("gedaechtnis_suchen", "gedaechtnis_lesen"):
            self.assertIn(name, actions.ACTIONS)
            self.assertFalse(actions.ACTIONS[name]["confirm"])
            self.assertIn(name, modes.READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
