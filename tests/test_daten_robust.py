"""Daten-Robustheit: unlesbare Dateien werden beiseitegelegt statt überschrieben, Speicherfehler
werden gemeldet, Kopien und Namen gehen nicht verloren."""
import codecs
import http.client
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import atomar        # noqa: E402
import memory        # noqa: E402


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)


class JsonLesen(_Tmp):
    def test_fehlt_bom_kaputt_falscher_typ(self):
        p = self.d / "a.json"
        self.assertEqual([], atomar.json_lesen(p, [], list))
        p.write_bytes(codecs.BOM_UTF8 + b'[1, 2]')
        self.assertEqual([1, 2], atomar.json_lesen(p, [], list))
        p.write_text("[1, 2", encoding="utf-8")
        self.assertEqual([], atomar.json_lesen(p, [], list))
        self.assertFalse(p.exists())
        self.assertEqual("[1, 2", (self.d / "a.json.kaputt").read_text(encoding="utf-8"))
        p.write_text('{"x": 1}', encoding="utf-8")                       # falscher Typ: zweite Ablage
        self.assertEqual([], atomar.json_lesen(p, [], list))
        self.assertEqual("[1, 2", (self.d / "a.json.kaputt").read_text(encoding="utf-8"))
        self.assertEqual(2, len(list(self.d.glob("a.json.kaputt*"))))

    def test_gesperrt_geht_an_den_aufrufer(self):
        p = self.d / "a.json"
        p.write_text("[]", encoding="utf-8")
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("gesperrt")):
            with self.assertRaises(PermissionError):
                atomar.json_lesen(p, [], list)
        self.assertTrue(p.exists())


class TagebuchKaputt(_Tmp):
    def setUp(self):
        super().setUp()
        import profilordner
        import tagebuch
        self.T = tagebuch
        for name, wert in (("wurzel", lambda: self.d / "Profile"), ("aktiv", lambda: "PersonA")):
            p = mock.patch.object(profilordner, name, wert)
            p.start()
            self.addCleanup(p.stop)
        self.datei = tagebuch._datei()
        self.datei.parent.mkdir(parents=True)

    def test_kaputtes_tagebuch_bleibt_erhalten(self):
        self.datei.write_text('[{"zeit": "x", "text": "alt"', encoding="utf-8")
        self.T.schreiben("Neuer Eintrag.")
        self.assertEqual(1, len(self.T.eintraege()))
        self.assertIn('"alt"', (self.datei.parent / "tagebuch.json.kaputt").read_text(encoding="utf-8"))

    def test_gesperrt_schreibt_nicht(self):
        self.T.schreiben("Erster.")
        with mock.patch("atomar.json_lesen", side_effect=PermissionError("gesperrt")):
            with self.assertRaises(OSError):
                self.T.schreiben("Zweiter.")
            self.assertEqual([], self.T.eintraege())                        # lesen: nur leer angezeigt
        self.assertEqual(["Erster."], [e["text"] for e in self.T.eintraege()])


class MemorySpeichern(_Tmp):
    def setUp(self):
        super().setUp()
        import indexdb
        for p in (mock.patch.object(memory, "_FILE", self.d / "memory.json"),
                  mock.patch.object(memory, "_profil_datei", return_value=None),
                  mock.patch.object(indexdb, "drop_ref"),
                  mock.patch.object(memory.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)

    def test_gesperrt_meldet_nicht_gemerkt(self):
        e = memory.remember("Der Ordner heißt Beispiel", "fakt")
        versuche = []

        def replace(*_a):
            versuche.append(1)
            raise PermissionError("gesperrt")
        with mock.patch.object(memory.os, "replace", replace):
            self.assertIsNone(memory.remember("Noch eine Notiz zum Test", "fakt"))
            self.assertIn("nicht gespeichert", memory.letzter_grund)
            self.assertEqual(5, len(versuche))
            self.assertFalse(memory.vergessen(e["id"]))
            self.assertIsNone(memory.ersetzen(e["id"], "Neuer Text zum Test"))
            self.assertEqual(0, memory.clear())
        self.assertEqual(["Der Ordner heißt Beispiel"], [x["text"] for x in memory.all_entries()])
        self.assertFalse((self.d / "memory.json.tmp").exists())

    def test_kurz_gesperrt_wird_wiederholt(self):
        echt, versuche = memory.os.replace, []

        def replace(a, b):
            versuche.append(1)
            if len(versuche) < 3:
                raise PermissionError("gesperrt")
            echt(a, b)
        with mock.patch.object(memory.os, "replace", replace):
            self.assertIsNotNone(memory.remember("Notiz trotz kurzer Sperre", "fakt"))
        self.assertEqual(1, memory.count())

    def test_datei_mit_bom(self):
        daten = {"next_id": 2, "entries": [{"id": 1, "text": "von Hand", "kind": "fakt"}]}
        (self.d / "memory.json").write_bytes(codecs.BOM_UTF8 + json.dumps(daten).encode("utf-8"))
        self.assertEqual(["von Hand"], [e["text"] for e in memory.all_entries()])


class Verdichten(_Tmp):
    def setUp(self):
        super().setUp()
        import indexdb
        for p in (mock.patch.object(memory, "_FILE", self.d / "memory.json"),
                  mock.patch.object(memory, "_profil_datei", return_value=None),
                  mock.patch.object(indexdb, "drop_ref"),
                  mock.patch("snapshot.sichern", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def test_kurze_vorliebe_bleibt(self):
        memory.remember("Mag Tee", "vorliebe")
        memory.remember("Mag Tee nicht mehr, lieber Kaffee am Morgen", "vorliebe")
        memory.remember("Der Hund heißt Bruno", "lektion")
        memory.remember("Der Hund heißt Bruno und ist sieben", "fakt")         # andere Art
        self.assertEqual(0, memory._verdichten(memory._FILE, 0.9)["merged"])
        self.assertEqual(4, memory.count())

    def test_echte_dublette(self):
        self.assertTrue(memory._text_dublette("Der Nutzer mag Tee", "Der Nutzer mag Tee sehr"))
        self.assertFalse(memory._text_dublette("Tee", "Kein Tee mehr"))
        self.assertFalse(memory._text_dublette("Der Nutzer mag", "Der Nutzermagazin liegt da"))


class WebUiZuGross(unittest.TestCase):
    def test_413_und_verbindung_zu(self):
        import webui
        bridge = webui.WebBridge(None)
        gesendet = []
        url = webui.start(bridge, {"send": lambda t, i: gesendet.append(t), "models": list, "state": dict})
        self.addCleanup(bridge.stop)
        port = int(url.split(":")[2].split("/")[0])
        con = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        con.putrequest("POST", f"/send?token={bridge.token}")
        con.putheader("Content-Type", "application/json")
        con.putheader("Content-Length", str(webui.MAX_BODY + 1))
        con.endheaders()
        r = con.getresponse()
        self.assertEqual(413, r.status)
        self.assertEqual("close", r.getheader("Connection"))
        con.close()
        self.assertEqual([], gesendet)


class AutobackupUri(_Tmp):
    def test_raute_im_pfad(self):
        import autobackup
        import paths
        daten = self.d / "Daten #1 %20"
        (daten / "learned").mkdir(parents=True)
        with sqlite3.connect(daten / "learned" / "memory.db") as con:
            con.execute("CREATE TABLE t (x)")
            con.execute("INSERT INTO t VALUES (42)")
        con.close()
        with mock.patch.object(paths, "DATEN", str(daten)), \
                mock.patch.object(autobackup, "_ordner", return_value=self.d / "Sicherung"):
            kopie = autobackup._gedaechtnis_kopie()
        self.assertIsNotNone(kopie)
        with sqlite3.connect(kopie) as con:
            self.assertEqual([(42,)], con.execute("SELECT x FROM t").fetchall())
        con.close()


class IndexDb(_Tmp):
    def test_sprecher_neutral_und_alte_erkannt(self):
        import indexdb
        gesehen = []
        with mock.patch.object(indexdb, "_upsert", side_effect=lambda k, r, t, d=None: gesehen.extend(t) or 0):
            indexdb.ingest_chat(1, "", [], [{"role": "assistant", "content": "Antwort"}])
        self.assertEqual(["KI: Antwort"], gesehen)
        for text in ("KI: x", "Nemi: x", "[KI] x", "[Nemi › Teil] x", "Nutzer: x"):
            self.assertEqual("x", indexdb._HERKUNFT.sub("", text, count=1), text)
        self.assertTrue(indexdb._SPRECHER.match("Nemi: alt") and indexdb._SPRECHER.match("KI: neu"))

    def test_praefix_ohne_platzhalter(self):
        import indexdb
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE chunks (kind, ref, datum)")
        con.executemany("INSERT INTO chunks VALUES ('chat', ?, '')",
                        [("chat:A_B:1",), ("chat:AxB:2",), ("chat:a_b:3",)])
        with mock.patch.object(indexdb, "_profil_bedingung", return_value=("", [])):
            bedingung, werte = indexdb._filter_sql(("chat",), "", "", ("chat:A_B:",))
        treffer = [r[0] for r in con.execute(f"SELECT c.ref FROM chunks c WHERE 1{bedingung}", werte)]
        con.close()
        self.assertEqual(["chat:A_B:1"], treffer)

    def test_lehren_anderer_persoenlichkeiten(self):
        import indexdb
        profile = self.d / "Profile"
        eigen = profile / "PersonA" / "Erinnerungen" / "memory.json"
        fremd = profile / "PersonB" / "Erinnerungen" / "memory.json"
        for datei, nr in ((eigen, 5), (fremd, 7)):
            datei.parent.mkdir(parents=True)
            datei.write_text(json.dumps({"entries": [{"id": nr, "text": f"Lehre {nr}", "kind": "lektion"}]}),
                             encoding="utf-8")
        weg = []
        with mock.patch.object(memory, "_FILE", self.d / "memory.json"), \
                mock.patch.object(memory, "_profil_datei", return_value=eigen), \
                mock.patch.object(memory, "_detail_ordner", return_value=None):
            self.assertEqual({"note:7"}, set(indexdb._fremde_notizen()))
            con = mock.Mock()
            with mock.patch.object(indexdb, "_connect", return_value=con), \
                    mock.patch.object(indexdb, "_hashes_je_ref",
                                      return_value={"note:5": [], "note:7": [], "note:99": []}), \
                    mock.patch.object(indexdb, "drop_ref", side_effect=weg.append), \
                    mock.patch.object(indexdb, "_wenn_geaendert", return_value=0):
                indexdb.ingest_all_notes()
        self.assertEqual(["note:99"], weg)


class KontextmenueInbox(_Tmp):
    def test_zweite_nachricht_kommt_dazu(self):
        import kontextmenue as K
        with mock.patch.object(K, "INBOX_DATEI", self.d / ".nemicli.inbox"):
            self.assertTrue(K.an_laufendes("erste"))
            self.assertTrue(K.an_laufendes("zweite"))
            self.assertEqual("erste\n\nzweite", K.INBOX_DATEI.read_text(encoding="utf-8"))
            self.assertFalse(K.an_laufendes("x" * K.INBOX_MAX))            # zu lang: die erste bleibt
            self.assertEqual("erste\n\nzweite", K.INBOX_DATEI.read_text(encoding="utf-8"))
            self.assertEqual([".nemicli.inbox"], [p.name for p in self.d.iterdir()])


class ZeitplanAlterAuftrag(_Tmp):
    def test_abgelehnt_alter_auftrag_bleibt(self):
        import zeitplan as Z
        with mock.patch.object(Z, "ORDNER", self.d / "Zeitplan"), mock.patch.object(Z, "BERICHTE", self.d / "B"):
            ok, nein = NS(returncode=0, stdout="", stderr=""), NS(returncode=1, stdout="", stderr="nein")
            with mock.patch.object(Z, "_schtasks", return_value=ok):
                Z.anlegen("Wache", "täglich 09:00", "Alter Auftrag.")
            with mock.patch.object(Z, "_schtasks", return_value=nein), self.assertRaises(ValueError):
                Z.anlegen("Wache", "täglich 10:00", "Neuer Auftrag.")
            self.assertEqual("Alter Auftrag.", Z.lade_auftrag("Wache")["auftrag"])


class Kugelbilder(_Tmp):
    def test_koerper_bleibt_ohne_sicherung(self):
        import kugelbilder as KB
        ziel = self.d / "koerper_vorne.png"
        ziel.write_bytes(b"alt")
        maler = mock.Mock(side_effect=AssertionError("darf nicht malen"))
        with mock.patch.object(KB, "koerper_fehlend", return_value={"koerper_vorne"}), \
                mock.patch.object(KB, "KOERPER", {"vorne": "standing"}), \
                mock.patch("snapshot.sichern", return_value=None), \
                mock.patch.object(KB, "_gesichter_malen"), mock.patch.object(KB, "koerper_bauen"):
            self.assertEqual([], KB._koerper_malen(self.d, "figur", "", 1, maler, {}))
        self.assertEqual(b"alt", ziel.read_bytes())

    def test_kaputte_outfits(self):
        import kugelbilder as KB
        with mock.patch.object(KB, "ORDNER", self.d):
            datei = KB.motiv_ordner("PersonA") / KB.OUTFIT_DATEI
            datei.parent.mkdir(parents=True)
            datei.write_text('{"liste": {"winter"', encoding="utf-8")
            self.assertEqual({"aktiv": "", "liste": {}}, KB.outfits("PersonA"))
            self.assertTrue(datei.with_name(datei.name + ".kaputt").exists())


class StatsKaputt(_Tmp):
    def test_kaputte_statistik_bleibt(self):
        import stats
        datei = self.d / "stats.json"
        datei.write_text('{"turns": 9', encoding="utf-8")
        with mock.patch.object(stats, "_FILE", datei), mock.patch.object(stats, "_data", None):
            stats.record_command("/x")
            stats.flush()
        self.assertTrue((self.d / "stats.json.kaputt").exists())


class Anhang(_Tmp):
    def test_utf16_ist_text(self):
        import anhang
        p = self.d / "ausgabe.log"
        p.write_bytes(codecs.BOM_UTF16_LE + "Zeile äöü\r\nzwei\r\n".encode("utf-16-le"))
        self.assertEqual("text", anhang._art(p))
        self.assertEqual("Zeile äöü\r\nzwei\r\n", anhang.lese_text(p)[0])


class LoraBild(_Tmp):
    def test_inhaltsgleiche_bilder(self):
        import lorabild as LB
        a, b = self.d / "a.png", self.d / "b.png"
        a.write_bytes(b"gleich")
        b.write_bytes(b"gleich")
        with mock.patch.object(LB, "ordner", lambda name, anlegen=False: self.d / "lora" / name):
            kopien = LB.daten_kopieren("figur", [a, b])
        self.assertEqual(1, len(kopien))
        self.assertTrue(all(k.is_file() for k in kopien))


class BackupRoh(_Tmp):
    def test_wal_wird_eingearbeitet(self):
        import backup
        db = self.d / "x.db"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE t (x)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        tmp = self.d / "tmp"
        tmp.mkdir()
        try:
            self.assertGreater(db.with_name("x.db-wal").stat().st_size, 0)
            quelle, hinweis = backup._sqlite_roh(db, tmp)
        finally:
            con.close()
        self.assertIn("-wal eingearbeitet", hinweis)
        con = sqlite3.connect(f"file:{quelle.as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            self.assertEqual([(1,)], con.execute("SELECT x FROM t").fetchall())
        finally:
            con.close()


class RechercheName(_Tmp):
    def test_gleiche_minute_ueberschreibt_nicht(self):
        import recherche as R
        a = R.speichern(R.Stand("Gleiche Frage"), self.d)
        b = R.speichern(R.Stand("Gleiche Frage"), self.d)
        self.assertNotEqual(a, b)
        self.assertTrue(a.is_file() and b.is_file())


if __name__ == "__main__":
    unittest.main()
