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

import actions            # noqa: E402
import agentrag           # noqa: E402
import chatstore          # noqa: E402
import indexdb            # noqa: E402
import memory             # noqa: E402

# Drei Themen-Achsen: Vektoren entstehen aus Stichworten, damit „Sinn“ testbar ist.
_ACHSEN = (("wache", "alarm", "vollscan", "signatur"), ("bild", "krea", "malen"), ("torch", "cuda", "einrichten"))


def _vektor(text: str) -> list[float]:
    t = text.lower()
    return [float(sum(w in t for w in achse)) + 0.01 for achse in _ACHSEN]


def _index_faden_abwarten() -> None:
    """Ein Index-Aufbau im Hintergrund (Prompt-Pfad wartet nie) darf nicht in den nächsten Test laufen:
    er hielte die Sperre und die Datenbank im gelöschten Temp-Ordner offen."""
    faden = getattr(indexdb, "_index_bau", None)
    if faden is not None and faden.is_alive():
        faden.join(30)


class _Basis(unittest.TestCase):
    def setUp(self):
        _index_faden_abwarten()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(_index_faden_abwarten)        # läuft vor dem Löschen des Temp-Ordners
        root = Path(self.tmp.name)
        self.encoder = False
        for ziel, name, wert in (
                (indexdb, "_ROOT", root), (indexdb, "_DB_NEW", root / "learned" / "memory.db"),
                (indexdb, "_DB_OLD", root / "learned" / "index.sqlite"),
                (memory, "_embed_texts", lambda texts, **k: [_vektor(t) for t in texts] if self.encoder else None),
                (memory, "encoder_loaded", lambda: self.encoder),
                (memory, "warm_encoder", lambda: None),
                (memory, "modell_kennung", lambda: "test-kennung"),
                (chatstore, "load", lambda n: None),
                # Netz-Marken der Werkzeuge: Treffer aus Webquellen setzen sie, der nächste Test erbte sie sonst
                (actions, "_web_taint", False), (actions, "_privat_gelesen", False)):
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
        treffer = indexdb.suchen("spooler", laden=True)      # auf den Umbau warten (Prompt-Pfad wartet nie)
        self.assertEqual(treffer[0]["datum"], "2026-09-18 09:30")
        self.assertEqual(indexdb.suchen("cyan", laden=True)[0]["datum"], "2026-09-24 10:00")   # Einlese-Datum

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


class IndexNachladen(_Basis):
    """Nach Änderungen an der DB lädt der Index nur geänderte Zeilen – das Ergebnis muss einem
    vollen Neuaufbau gleichen."""

    def _stand(self):
        ix = indexdb._index()
        vek = {}
        if ix["mat"] is not None:
            for j, r in enumerate(ix["vec_zeilen"]):
                vek[r] = tuple(round(float(x), 5) for x in ix["mat"][j])
        return {m[4]: m for m in ix["meta"]}, vek

    def _wie_neu_gebaut(self):
        nachgeladen = self._stand()
        indexdb._CACHE.update({"meta": None, "stamp": None})
        self.assertEqual(nachgeladen, self._stand())
        return nachgeladen

    def test_neu_geloescht_vektor_nachgetragen_datum(self):
        self.fuellen(mit_vektoren=True)
        self._stand()
        with patch.object(indexdb, "_voll_aufbauen", side_effect=AssertionError("voller Aufbau")):
            self.encoder = True
            indexdb._upsert("chat", "chat:11", ["Nutzer: Der Alarm beim Vollscan war harmlos."])
            self.encoder = False
            indexdb._upsert("chat", "chat:12", ["Nutzer: Noch ein Bild malen, bitte."])   # erst ohne Vektor
            indexdb.drop_ref("chat:7")
            con = indexdb._connect()
            con.execute("UPDATE chunks SET vec=?, vec_modell=? WHERE ref=?",
                        (str(_vektor("bild malen")), "test-kennung", "chat:12"))
            con.execute("UPDATE chunks SET datum=? WHERE ref=?", ("2026-10-01", "chat:9"))
            con.commit()
            con.close()
            meta, vek = self._stand()
        refs = {m[1] for m in meta.values()}
        self.assertIn("chat:11", refs)
        self.assertNotIn("chat:7", refs)
        self.assertEqual(len(vek), 5)                       # 4 alte − 1 gelöscht + 1 neu + 1 nachgetragen
        self.assertTrue(any(m[1] == "chat:9" and m[3] == "2026-10-01" for m in meta.values()))
        self._wie_neu_gebaut()

    def test_ersetzte_datenbank_wird_neu_gelesen(self):
        self.fuellen(mit_vektoren=True)
        self._stand()
        con = indexdb._connect()                            # gleiche Zeilennummern, anderer Inhalt
        con.execute("DELETE FROM chunks")
        con.commit()
        con.close()
        self.encoder = True
        indexdb._upsert("bericht", "bericht:Neu.md", ["Ganz anderer Inhalt über cuda."])
        self.encoder = False
        meta, _vek = self._wie_neu_gebaut()
        self.assertEqual({m[1] for m in meta.values()}, {"bericht:Neu.md"})

    def test_prompt_pfad_wartet_nicht_auf_den_aufbau(self):
        self.fuellen(mit_vektoren=True)
        indexdb._CACHE.update({"meta": None, "stamp": None})
        treffer = indexdb.search("KB5124010")               # kalt: nur Stichworte, Aufbau im Hintergrund
        self.assertEqual(treffer[0]["ref"], "bericht:Wache_20260920-1200.md")
        self.assertEqual(treffer[0]["wie"], "wort")
        indexdb._index_bau.join(10)
        self.assertIsNotNone(indexdb._CACHE["meta"])
        with indexdb._INDEX_SPERRE:                          # ein anderer Faden baut gerade
            indexdb._invalidate()
            self.assertIsNone(indexdb._index(warten=False))


if __name__ == "__main__":
    unittest.main()
