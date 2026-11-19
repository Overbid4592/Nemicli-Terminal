"""Entpackte Archive im Gedächtnis (indexdb): Medien aus Entpackt/*/Bilder|Videos|Audio, Export-Chats (kind
„export“), sichtbares Einlesen (entpackt_einlesen), Profil-Grenze und Einbett-Sperre. Ohne Modell, nur
synthetische Daten in einem Temp-Ordner.

python -m unittest tests.test_indexdb_entpackt
"""
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import bibkind      # noqa: E402
import indexdb      # noqa: E402
import memory       # noqa: E402
import profilordner  # noqa: E402
from test_chatexport import chatgpt_daten, claude_daten  # noqa: E402


def _bild(pfad: Path) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.new("RGB", (8, 8), "red").save(pfad)


def _wav(pfad: Path) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.profil = "test_user"
        self.p = self.tmp / "Profile" / "test_user"
        self.archiv = self.p / "Entpackt" / "export_1"
        _bild(self.p / "Bilder" / "eigen.png")
        _bild(self.archiv / "Bilder" / "urlaub.png")
        (self.archiv / "Videos").mkdir(parents=True)
        (self.archiv / "Videos" / "clip.mp4").write_bytes(b"\0" * 64)
        _wav(self.archiv / "Audio" / "sprachnachricht.wav")
        (self.archiv / "Programme").mkdir()
        (self.archiv / "Programme" / "setup.exe").write_bytes(b"MZ")
        (self.archiv / "Programme" / "bild.png").write_bytes(b"x")         # falscher Ordner: nie lesen
        for name in ("Übersicht.md", "Zuordnung.csv", "SHA256.txt", "Protokoll.txt"):
            (self.archiv / name).write_text("Bilder/urlaub.png", encoding="utf-8")
        (self.archiv / "JSON").mkdir()
        (self.archiv / "JSON" / "conversations.json").write_text(json.dumps(chatgpt_daten()), encoding="utf-8")
        db = self.tmp / "memory.db"
        self.kennung = ""
        self.patches = [mock.patch.object(indexdb, "_db_path", lambda: db),
                        mock.patch.object(profilordner, "aktiv", lambda: self.profil),
                        mock.patch.object(profilordner, "wurzel", lambda: self.tmp / "Profile"),
                        mock.patch.object(indexdb, "_fremde_notizen", lambda *a, **k: frozenset()),
                        mock.patch.object(indexdb, "_kennung", lambda: self.kennung),
                        mock.patch.object(bibkind, "ordner", lambda pid=None: self.tmp / "bib" / "bibliothekar_1"),
                        mock.patch.object(memory, "kann_bilder", lambda: False),
                        mock.patch.object(memory, "_embed_texts", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        indexdb._invalidate()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        indexdb._invalidate()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refs(self, kind: str | None = None) -> list[str]:
        con = sqlite3.connect(str(indexdb._db_path()))
        try:
            sql, werte = ("SELECT DISTINCT ref FROM chunks", ()) if kind is None else \
                ("SELECT DISTINCT ref FROM chunks WHERE kind=?", (kind,))
            return sorted(r for (r,) in con.execute(sql, werte))
        finally:
            con.close()

    def ohne_vektor(self) -> int:
        con = sqlite3.connect(str(indexdb._db_path()))
        try:
            return con.execute("SELECT COUNT(*) FROM chunks WHERE vec IS NULL").fetchone()[0]
        finally:
            con.close()

    def modell(self):
        """Ein Schein-Modell: jeder Text bekommt denselben kleinen Vektor."""
        self.kennung = "test-modell"
        for p in (mock.patch.object(memory, "_embed_texts", lambda texte, **k: [[1.0, 0.0, 0.0] for _t in texte]),
                  mock.patch.object(memory, "encoder_loaded", lambda: True),
                  mock.patch.object(memory, "auftrag", lambda n: contextlib.nullcontext(False))):
            p.start()
            self.addCleanup(p.stop)


class MedienAusEntpackt(Basis):
    def test_refs_und_unveraendert(self):
        self.assertEqual(4, indexdb.ingest_medien(None))
        self.assertEqual(["bild:test_user:Entpackt/export_1/Bilder/urlaub.png", "bild:test_user:eigen.png",
                          "ton:test_user:Entpackt/export_1/Audio/sprachnachricht.wav",
                          "video:test_user:Entpackt/export_1/Videos/clip.mp4"], self.refs())
        self.assertEqual(0, indexdb.ingest_medien(None))
        self.assertEqual("Entpackt/export_1/Bilder", indexdb._medien_ordner(
            "bild", "bild:test_user:Entpackt/export_1/Bilder/urlaub.png"))
        self.assertEqual("Bilder", indexdb._medien_ordner("bild", "bild:test_user:Gesichtszuege/b.png"))
        self.assertEqual("Musik", indexdb._medien_ordner("ton", "ton:test_user:Musik/lied.wav"))

    def test_geloeschtes_archiv_verschwindet(self):
        indexdb.ingest_medien(None)
        shutil.rmtree(self.archiv)
        indexdb.ingest_medien(None)
        self.assertEqual(["bild:test_user:eigen.png"], self.refs())
        shutil.rmtree(self.p / "Entpackt")
        _bild(self.p / "Entpackt" / "b" / "Bilder" / "x.png")
        indexdb.ingest_medien(None)
        shutil.rmtree(self.p / "Entpackt")                                  # ganz weg: auch ausgetragen
        indexdb.ingest_medien(None)
        self.assertEqual(["bild:test_user:eigen.png"], self.refs())

    def test_profil_fehlt_nichts_austragen(self):
        indexdb.ingest_medien(None)
        vorher = self.refs()
        shutil.move(str(self.p), str(self.tmp / "weg"))                     # Laufwerk kurz weg
        indexdb.ingest_medien(None)
        self.assertEqual(vorher, self.refs())

    def test_bilder_ordner_der_wie_ein_archiv_aussieht(self):
        _bild(self.p / "Bilder" / "Entpackt" / "x" / "Bilder" / "y.png")
        indexdb.ingest_medien(None)
        self.assertNotIn("bild:test_user:Entpackt/x/Bilder/y.png", self.refs())

    @unittest.skipUnless(sys.platform == "win32", "Junction gibt es nur unter Windows")
    def test_verknuepfung_hinaus_bleibt_draussen(self):
        import _winapi
        fremd = self.tmp / "Profile" / "example" / "Entpackt" / "geheim"
        _bild(fremd / "Bilder" / "fremd.png")
        _winapi.CreateJunction(str(fremd), str(self.p / "Entpackt" / "link"))
        indexdb.ingest_medien(None)
        self.assertFalse([r for r in self.refs() if "fremd" in r])

    def test_fremdes_profil_sieht_nichts(self):
        indexdb.ingest_medien(None)
        self.assertEqual(1, len(indexdb.medien_suchen("urlaub", 5, ("bild",))))
        self.profil = "example"
        self.assertEqual([], indexdb.medien_suchen("urlaub", 5, ("bild",)))


class ExportChats(Basis):
    def test_einlesen_suchen_profilgrenze(self):
        self.assertGreater(indexdb.ingest_exporte(None), 0)
        refs = self.refs("export")
        self.assertEqual(1, len(refs))
        self.assertTrue(refs[0].startswith("export:test_user:export_1/chatgpt-"))
        hits = indexdb.suchen("Tomatensorten Balkon", 5, nur_worte=True, lernen=False)
        self.assertEqual([refs[0]], [h["ref"] for h in hits])
        self.assertEqual("export", hits[0]["kind"])
        self.assertTrue(hits[0]["datum"].startswith("2024-03-"))
        text = indexdb.quelle_lesen(refs[0])["text"]
        self.assertIn("ChatGPT-Chat „Gartenplanung Balkon“", text)
        self.assertIn("## Nutzer", text)
        self.assertIn("## ChatGPT", text)                                   # nicht „KI“: es war ein anderer Dienst
        self.profil = "example"
        self.assertEqual([], indexdb.suchen("Tomatensorten Balkon", 5, nur_worte=True, lernen=False))
        self.assertIsNone(indexdb.quelle_lesen(refs[0]))
        self.assertTrue(indexdb._fremdes_profil(refs[0], "example"))

    def test_unveraendert_nicht_neu_lesen(self):
        import chatexport
        indexdb.ingest_exporte(None)
        with mock.patch.object(chatexport, "lesen", wraps=chatexport.lesen) as lesen:
            self.assertEqual(0, indexdb.ingest_exporte(None))
            lesen.assert_not_called()
            datei = self.archiv / "JSON" / "conversations.json"
            datei.write_text(json.dumps(claude_daten()), encoding="utf-8")
            os.utime(datei, ns=(1, 2_000_000_000_000_000_000))
            indexdb.ingest_exporte(None)
            lesen.assert_called_once()
        refs = self.refs("export")
        self.assertEqual(["export:test_user:export_1/claude-c-1"], refs)   # alter Chat ausgetragen

    def test_archiv_weg_chats_weg(self):
        indexdb.ingest_exporte(None)
        shutil.rmtree(self.archiv)
        indexdb.ingest_exporte(None)
        self.assertEqual([], self.refs("export"))

    def test_bibliothekar_nimmt_sie_mit(self):
        nichts = lambda *a, **k: 0                                         # noqa: E731
        with mock.patch.multiple(indexdb, ingest_all_notes=nichts, ingest_learned=nichts, nachzuegler_chats=nichts,
                                 backfill=nichts, ingest_wissen=nichts, neu_schneiden=nichts,
                                 vektoren_umwandeln=nichts, ingest_berichte=lambda *a, **k: (0, 0)):
            indexdb.after_turn(None)
        self.assertEqual(1, len(self.refs("export")))
        self.assertEqual(4, len([r for r in self.refs() if not r.startswith("export:")]))
        self.assertGreater(self.ohne_vektor(), 0)                           # gesammelt, Vektoren folgen


class SichtbaresEinlesen(Basis):
    def test_alles_mit_vektoren_und_fortschritt(self):
        self.modell()
        gesehen = []
        erg = indexdb.entpackt_einlesen(self.archiv, "test_user", fortschritt=lambda *a: gesehen.append(a))
        self.assertEqual((4, 4, False, ""), (erg["eingelesen"], erg["gesamt"], erg["abgebrochen"], erg["fehler"]))
        self.assertEqual(0, erg["ohne_vektor"])
        self.assertEqual(("fertig", 4, 4), gesehen[-1])
        self.assertIn("urlaub.png", [a[0] for a in gesehen])
        self.assertIn("Gartenplanung Balkon", [a[0] for a in gesehen])
        self.assertNotIn("bild:test_user:eigen.png", self.refs())          # nur dieses Archiv
        self.assertFalse([r for r in self.refs() if "Programme" in r or ".md" in r or ".csv" in r])
        self.assertEqual(0, self.ohne_vektor())
        erg = indexdb.entpackt_einlesen(self.archiv, "test_user")         # zweiter Lauf: nichts zu tun
        self.assertEqual((4, 4, 0), (erg["eingelesen"], erg["gesamt"], erg["neu"]))

    def test_anderes_profil_aktiv(self):
        self.profil = "example"
        erg = indexdb.entpackt_einlesen(self.archiv, "test_user")
        self.assertEqual(4, erg["eingelesen"])
        self.assertTrue(all(":test_user:" in r for r in self.refs()))

    def test_abbruch_und_weiter(self):
        self.modell()
        stopp = threading.Event()

        def fortschritt(name, erledigt, gesamt):
            if erledigt >= 1:
                stopp.set()
        erg = indexdb.entpackt_einlesen(self.archiv, "test_user", fortschritt, stopp)
        self.assertTrue(erg["abgebrochen"])
        self.assertLess(erg["eingelesen"], erg["gesamt"])
        self.assertIsNone(getattr(indexdb._lauf, "stopp", None))            # nichts bleibt hängen
        erg = indexdb.entpackt_einlesen(self.archiv, "test_user")
        self.assertEqual((4, False, 0), (erg["eingelesen"], erg["abgebrochen"], erg["ohne_vektor"]))

    def test_falscher_ordner(self):
        for ordner in (self.p / "Bilder", self.tmp, self.p / "Entpackt" / "fehlt"):
            erg = indexdb.entpackt_einlesen(ordner, "test_user")
            self.assertTrue(erg["fehler"])
            self.assertEqual(0, erg["eingelesen"])
        self.assertFalse(indexdb._db_path().exists() and self.refs())


class Sperre(Basis):
    def fremd_halten(self):
        """Simuliert einen anderen Prozess, der gerade einbettet."""
        pfad = indexdb._sperr_datei()
        pfad.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(pfad), os.O_RDWR | os.O_CREAT)
        self.assertTrue(indexdb._sperre_nehmen(fd))

        def lassen():
            indexdb._sperre_lassen(fd)
            os.close(fd)
            pfad.unlink(missing_ok=True)
        self.addCleanup(lassen)

    def test_im_selben_prozess_verschachtelt(self):
        with indexdb.einbett_sperre() as a:
            self.assertTrue(a)
            with indexdb.einbett_sperre() as b:
                self.assertTrue(b)
            self.assertFalse(indexdb._fremd_gesperrt())
        self.assertEqual(0, indexdb._sperre_tiefe)

    def test_fenster_wartet_dann_nur_text(self):
        self.fremd_halten()
        (self.tmp / "bib" / "bibliothekar_1").mkdir(parents=True)
        self.modell()
        with mock.patch.object(indexdb, "SPERRE_WARTEN_S", 0.2):
            erg = indexdb.entpackt_einlesen(self.archiv, "test_user")
        self.assertTrue((self.tmp / "bib" / "bibliothekar_1" / "unterbrechen").exists())   # Kind hört auf
        self.assertTrue(erg["fehler"])
        self.assertEqual(4, erg["eingelesen"])
        self.assertGreater(erg["ohne_vektor"], 0)
        self.assertFalse(getattr(indexdb._lauf, "nur_text", False))

    def test_bibliothekar_mit_modell_weicht_aus(self):
        self.fremd_halten()
        with mock.patch.object(indexdb, "after_turn", side_effect=AssertionError("darf nicht laufen")):
            out = bibkind.lauf_hier(1, True, None)
        self.assertIn("anderer Prozess", out["err"])
        self.assertEqual(0, indexdb.ingest_exporte(None))                  # Sammeln schreibt nicht dazwischen
        indexdb.ingest_medien(None)
        self.assertEqual(["bild:test_user:eigen.png"], self.refs())

    def test_sammeln_ohne_modell_braucht_keine_sperre(self):
        self.fremd_halten()
        with mock.patch.object(indexdb, "after_turn", return_value=0), \
                mock.patch.object(indexdb, "offen", return_value={"brocken": 0, "ohne_vektor": 0}):
            self.assertIsNone(bibkind.lauf_hier(1, False, None)["err"])


if __name__ == "__main__":
    unittest.main()
