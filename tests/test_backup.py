"""Sicherungen als Zip mit SHA-256 (tools/backup.py, /backup, backup_erstellen).

python -m unittest tests.test_backup
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import backup as B                                     # noqa: E402
import paths                                           # noqa: E402


def _schreibe(p: Path, inhalt: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(inhalt)
    return p


class Basis(unittest.TestCase):
    getrennt = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.daten = self.tmp / "daten"
        self.install = self.tmp / "programm" if self.getrennt else self.daten
        self.daten.mkdir()
        self.install.mkdir(exist_ok=True)
        for name, wert in (("DATEN", self.daten), ("INSTALL", self.install)):
            p = mock.patch.object(paths, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def namen(self, zip_pfad: Path) -> set[str]:
        with zipfile.ZipFile(zip_pfad) as zf:
            return set(zf.namelist())


class Teilbackup(Basis):
    def test_nur_bilder_persoenlichkeiten_profile(self):
        _schreibe(self.daten / "Bilder" / "a.png", b"\x89PNG bild")
        _schreibe(self.daten / "Persoenlichkeiten" / "nemi.md", b"# Nemi")
        _schreibe(self.daten / "Profile" / "Nemi" / "Chats" / "1.json", b"{}")
        _schreibe(self.daten / "chats" / "001.json", b"{}")
        _schreibe(self.daten / "learned" / "memory.json", b"{}")
        erg = B.erstellen("teil")
        self.assertEqual({"Daten/Bilder/a.png", "Daten/Persoenlichkeiten/nemi.md",
                          "Daten/Profile/Nemi/Chats/1.json", "BACKUP.json", "SHA256SUMS.txt"},
                         self.namen(erg.pfad))
        self.assertEqual(3, erg.dateien)
        self.assertTrue(erg.pfad.name.startswith("NemiCLI_teil_"))
        self.assertEqual(self.daten / "Backup", erg.pfad.parent)
        self.assertTrue((self.daten / "Backup" / "LIES-MICH.txt").is_file())

    def test_pruefsummen_stimmen(self):
        _schreibe(self.daten / "Bilder" / "a.png", b"bild-inhalt")
        erg = B.erstellen("teil")
        sha = hashlib.sha256(erg.pfad.read_bytes()).hexdigest()
        self.assertEqual(sha, erg.sha256)
        self.assertEqual(f"{sha}  {erg.pfad.name}\n",
                         (erg.pfad.parent / (erg.pfad.name + ".sha256")).read_text(encoding="utf-8"))
        with zipfile.ZipFile(erg.pfad) as zf:
            summen = zf.read("SHA256SUMS.txt").decode()
            info = json.loads(zf.read("BACKUP.json"))
            self.assertEqual(b"bild-inhalt", zf.read("Daten/Bilder/a.png"))
        self.assertIn(f"{hashlib.sha256(b'bild-inhalt').hexdigest()}  Daten/Bilder/a.png", summen)
        self.assertEqual(("teil", 1), (info["art"], info["dateien"]))

    def test_zwei_in_derselben_sekunde_ueberschreiben_nichts(self):
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        fest = B.datetime(2026, 10, 6, 18, 0, 0)
        with mock.patch.object(B, "datetime") as dt:
            dt.now.return_value = fest
            erst, zweit = B.erstellen("teil"), B.erstellen("teil", ganz=True)
        self.assertNotEqual(erst.pfad, zweit.pfad)
        self.assertTrue(erst.pfad.is_file() and zweit.pfad.is_file())
        self.assertEqual("NemiCLI_teil_2026-10-06_18-00-00_2.zip", zweit.pfad.name)
        self.assertEqual([(erst.pfad.name, True), (zweit.pfad.name, True)],
                         sorted((n, ok) for n, ok, _ in B.pruefen()))

    def test_leer_ist_ein_fehler(self):
        with self.assertRaises(ValueError):
            B.erstellen("teil")
        self.assertEqual([], list((self.daten / "Backup").glob("*.zip*")) if (self.daten / "Backup").exists() else [])

    def test_unbekannte_art(self):
        with self.assertRaises(ValueError):
            B.erstellen("alles")


class VollGetrennt(Basis):
    def setUp(self):
        super().setUp()
        _schreibe(self.daten / "chats" / "001.json", b"{}")
        _schreibe(self.daten / "Cache" / "gguf" / "x.safetensors", b"c")
        _schreibe(self.daten / "Backup" / "NemiCLI_teil_alt.zip", b"alt")
        _schreibe(self.daten / "Models" / "Krea2" / "krea.safetensors", b"m")
        _schreibe(self.install / "main.py", b"print()")
        _schreibe(self.install / ".env", b"OPENAI_API_KEY=dpapi:abc")
        _schreibe(self.install / "core" / "__pycache__" / "a.pyc", b"p")
        _schreibe(self.install / "venv" / "Lib" / "x.py", b"v")
        _schreibe(self.install / ".git" / "HEAD", b"g")
        _schreibe(self.install / "ModelGGUF" / "Gemma" / "g.gguf", b"g")
        _schreibe(self.install / "Vision" / "E2B" / "v.gguf", b"v")

    def test_ohne_neu_erzeugbares_und_ohne_modelle(self):
        erg = B.erstellen("voll")
        n = self.namen(erg.pfad)
        self.assertIn("Daten/chats/001.json", n)
        self.assertIn("Programm/main.py", n)
        self.assertIn("Programm/.env", n)
        for weg in ("Cache", "Backup", "Models", "venv", ".git", "__pycache__", "ModelGGUF", "Vision"):
            self.assertFalse(any(f"/{weg}/" in x for x in n), weg)
        self.assertTrue(erg.pfad.name.startswith("NemiCLI_voll_2"))
        self.assertIn("Programm/venv/", erg.ausgelassen)
        self.assertIn("Daten/Models/", erg.ausgelassen)

    def test_mit_modellen(self):
        erg = B.erstellen("voll", modelle=True)
        n = self.namen(erg.pfad)
        self.assertIn("Daten/Models/Krea2/krea.safetensors", n)
        self.assertIn("Programm/ModelGGUF/Gemma/g.gguf", n)
        self.assertIn("Programm/Vision/E2B/v.gguf", n)
        self.assertTrue(erg.pfad.name.startswith("NemiCLI_voll_modelle_"))
        with zipfile.ZipFile(erg.pfad) as zf:            # schon Gepacktes wird nur gespeichert
            self.assertEqual(zipfile.ZIP_STORED, zf.getinfo("Programm/ModelGGUF/Gemma/g.gguf").compress_type)
            self.assertEqual(zipfile.ZIP_DEFLATED, zf.getinfo("Programm/main.py").compress_type)

    def test_teil_ignoriert_modelle_schalter(self):
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        erg = B.erstellen("teil", modelle=True)
        self.assertTrue(erg.pfad.name.startswith("NemiCLI_teil_"))


class VollGleicherOrdner(Basis):
    getrennt = False

    def test_ein_ordner_ohne_doppelte_und_ohne_backups(self):
        _schreibe(self.daten / "main.py", b"print()")
        _schreibe(self.daten / "chats" / "001.json", b"{}")
        _schreibe(self.daten / "venv" / "x.py", b"v")
        _schreibe(self.daten / "Backup" / "NemiCLI_voll_alt.zip", b"alt")
        erst = B.erstellen("voll")
        n = self.namen(erst.pfad)
        self.assertEqual({"NemiCLI/main.py", "NemiCLI/chats/001.json", "BACKUP.json", "SHA256SUMS.txt"}, n)
        zweit = B.erstellen("voll", ganz=True)           # das erste Backup landet nicht im zweiten
        self.assertFalse(any("Backup" in x for x in self.namen(zweit.pfad)))


class Datenbanken(Basis):
    def test_sqlite_ueber_backup_api_auch_mit_wal(self):
        db = self.daten / "Profile" / "Nemi" / "memory.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (x)")
        con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
        con.commit()                                     # Daten stehen noch im -wal
        try:
            erg = B.erstellen("teil")
        finally:
            con.close()
        n = self.namen(erg.pfad)
        self.assertIn("Daten/Profile/Nemi/memory.db", n)
        self.assertFalse(any(x.endswith(("-wal", "-shm")) for x in n))
        raus = self.tmp / "raus.db"
        with zipfile.ZipFile(erg.pfad) as zf:
            raus.write_bytes(zf.read("Daten/Profile/Nemi/memory.db"))
        k = sqlite3.connect(raus)
        try:
            self.assertEqual(50, k.execute("SELECT COUNT(*) FROM t").fetchone()[0])
        finally:
            k.close()

    def test_unveraenderte_datenbank_nicht_im_nachtrag(self):
        db = self.daten / "Profile" / "test_user" / "memory.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        try:
            con.execute("CREATE TABLE t (x)")
            con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
            con.commit()
        finally:
            con.close()
        B.erstellen("teil")
        plan = B.planen("teil")
        self.assertTrue(plan.nachtrag)
        self.assertEqual(([], 1), (plan.dateien, plan.unveraendert))


class Sicherheit(Basis):
    def test_abbruch_laesst_nichts_zurueck(self):
        for i in range(5):
            _schreibe(self.daten / "Bilder" / f"{i}.png", b"b" * 10)
        with self.assertRaises(B.Abgebrochen):
            B.erstellen("teil", abbruch=lambda: True)
        self.assertEqual([], [p.name for p in (self.daten / "Backup").iterdir() if p.name != "LIES-MICH.txt"])

    def test_zu_wenig_platz(self):
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        voll = type("U", (), {"free": 10})()
        with mock.patch.object(B.shutil, "disk_usage", return_value=voll):
            with self.assertRaisesRegex(RuntimeError, "Zu wenig Platz"):
                B.erstellen("teil")
        self.assertEqual([], list((self.daten / "Backup").glob("*.zip*")))

    def test_nur_eins_gleichzeitig(self):
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        with B._LOCK:
            with self.assertRaisesRegex(RuntimeError, "läuft schon"):
                B.erstellen("teil")

    @unittest.skipUnless(sys.platform == "win32" and hasattr(__import__("_winapi"), "CreateJunction"),
                         "Junctions nur unter Windows")
    def test_verknuepfung_wird_nicht_betreten(self):
        import _winapi
        draussen = self.tmp / "draussen"
        _schreibe(draussen / "geheim.txt", b"nicht sichern")
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        _winapi.CreateJunction(str(draussen), str(self.daten / "Bilder" / "link"))
        erg = B.erstellen("teil")
        self.assertFalse(any("geheim" in x for x in self.namen(erg.pfad)))
        self.assertTrue(any("Verknüpfung" in x for x in erg.ausgelassen))


class Pruefen(Basis):
    def test_stimmt_und_erkennt_veraenderung(self):
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        erg = B.erstellen("teil")
        self.assertEqual([(erg.pfad.name, True, "Prüfsumme stimmt")], B.pruefen())
        with open(erg.pfad, "ab") as f:
            f.write(b"!")
        (name, ok, text), = B.pruefen()
        self.assertFalse(ok)
        self.assertIn("NICHT", text)

    def test_name_bleibt_im_backup_ordner(self):
        _schreibe(self.tmp / "fremd.zip", b"x")
        (_, ok, text), = B.pruefen(r"..\..\fremd.zip")
        self.assertFalse(ok)
        self.assertEqual("nicht gefunden", text)

    def test_liste_und_status(self):
        self.assertIn("Noch keine Backups", B.status_text())
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        erg = B.erstellen("teil")
        self.assertEqual([erg.pfad.name], [e["name"] for e in B.liste()])
        self.assertIn(erg.pfad.name, B.status_text())


class Werkzeug(Basis):
    def test_eingetragen_und_mit_rueckfrage(self):
        import actions
        import modes
        import sicherheit
        self.assertTrue(actions.needs_confirm({"tool": "backup_erstellen", "art": "teil"}))
        self.assertFalse(actions.needs_confirm({"tool": "backup_liste"}))
        self.assertEqual(sicherheit.AENDERT, sicherheit.stufe({"tool": "backup_erstellen", "art": "voll"}))
        self.assertIn("backup_liste", modes.READ_TOOLS)
        self.assertNotIn("backup_erstellen", modes.READ_TOOLS)
        self.assertIn("Bilder, Persönlichkeiten, Profile", actions.describe({"tool": "backup_erstellen", "art": "teil"}))
        self.assertIn("mit Modellen", actions.describe({"tool": "backup_erstellen", "art": "voll", "modelle": True}))

    def test_werkzeug_sichert_und_meldet(self):
        import actions
        _schreibe(self.daten / "Bilder" / "a.png", b"b")
        res = actions._backup_erstellen({"tool": "backup_erstellen", "art": "Teilbackup"})
        self.assertTrue(res.ok, res.text)
        self.assertIn("Backup fertig", res.text)
        self.assertIsNone(actions.bild_status())          # Fortschritt danach wieder frei
        falsch = actions._backup_erstellen({"tool": "backup_erstellen", "art": "alles"})
        self.assertFalse(falsch.ok)
        liste = actions._backup_liste({"tool": "backup_liste", "pruefen": True})
        self.assertTrue(liste.ok)
        self.assertIn("Prüfsumme stimmt", liste.text)

    def test_befehl_im_menue(self):
        import commands
        self.assertIn("/backup", [c.name for c in commands.COMMAND_LIST])
        werte = [w for w, _, _ in commands.UNTERMENUES["/backup"]]
        self.assertEqual(["", "teil", "voll", "voll modelle", "teil ganz", "voll ganz", "auto", "auto jetzt",
                          "pruefen", "auspacken", "ordner"], werte)


class Nachtrag(Basis):
    """Differenziell: Nachtrag enthält, was seit dem letzten vollen Backup neu/geändert ist."""

    def _spaeter(self, p: Path, sekunden: int = 10) -> None:
        st = p.stat()
        os.utime(p, (st.st_atime + sekunden, st.st_mtime + sekunden))

    def _voll(self):
        _schreibe(self.daten / "Bilder" / "bleibt.png", b"gleich")
        _schreibe(self.daten / "Bilder" / "aendert.png", b"alt")
        _schreibe(self.daten / "Bilder" / "angefasst.png", b"inhalt gleich")
        _schreibe(self.daten / "Profile" / "weg.json", b"{}")
        return B.erstellen("teil")

    def test_nur_neues_geaendertes_und_geloeschte_liste(self):
        voll = self._voll()
        self.assertIsNone(voll.basis)
        _schreibe(self.daten / "Bilder" / "aendert.png", b"neu und laenger")
        self._spaeter(self.daten / "Bilder" / "aendert.png")
        self._spaeter(self.daten / "Bilder" / "angefasst.png")          # nur Zeit geändert
        _schreibe(self.daten / "Bilder" / "neu.png", b"frisch")
        (self.daten / "Profile" / "weg.json").unlink()
        erg = B.erstellen("teil")
        self.assertEqual(voll.pfad.name, erg.basis)
        self.assertIn("_nachtrag_", erg.pfad.name)
        self.assertEqual({"Daten/Bilder/aendert.png", "Daten/Bilder/neu.png", "BACKUP.json",
                          "SHA256SUMS.txt"}, self.namen(erg.pfad))
        self.assertEqual((2, 2, 1), (erg.dateien, erg.unveraendert, erg.geloescht))
        info = B.info(erg.pfad)
        self.assertTrue(info["nachtrag"])
        self.assertEqual(["Daten/Profile/weg.json"], info["geloescht"])
        self.assertEqual(voll.sha256, info["basis_sha256"])
        self.assertIn("Nachtrag fertig", erg.text())
        self.assertTrue(all(ok for _, ok, _ in B.pruefen()))

    def test_differenziell_immer_zum_vollen(self):
        voll = self._voll()
        _schreibe(self.daten / "Bilder" / "eins.png", b"1")
        erster = B.erstellen("teil")
        _schreibe(self.daten / "Bilder" / "zwei.png", b"2")
        zweiter = B.erstellen("teil")
        self.assertEqual((voll.pfad.name, voll.pfad.name), (erster.basis, zweiter.basis))
        self.assertEqual({"Daten/Bilder/eins.png", "Daten/Bilder/zwei.png"},
                         self.namen(zweiter.pfad) - {"BACKUP.json", "SHA256SUMS.txt"})

    def test_nichts_geaendert(self):
        self._voll()
        with self.assertRaises(B.NichtsNeu):
            B.erstellen("teil")
        self.assertEqual(1, len(B.liste()))

    def test_ganz_und_zu_alte_basis_sichern_alles(self):
        voll = self._voll()
        self.assertIsNone(B.erstellen("teil", ganz=True).basis)
        with mock.patch.object(B, "BASIS_TAGE", -1):
            plan = B.planen("teil")
        self.assertFalse(plan.nachtrag)
        self.assertEqual(voll.dateien, len(plan.dateien))

    def test_basis_nur_gleicher_art(self):
        self._voll()
        _schreibe(self.install / "main.py", b"print()")
        self.assertFalse(B.planen("voll").nachtrag)

    def test_auspacken_nachtrag_mit_vollem_backup(self):
        self._voll()
        _schreibe(self.daten / "Bilder" / "aendert.png", b"neuer inhalt")
        self._spaeter(self.daten / "Bilder" / "aendert.png")
        _schreibe(self.daten / "Bilder" / "neu.png", b"frisch")
        (self.daten / "Profile" / "weg.json").unlink()
        nachtrag = B.erstellen("teil")
        erg = B.auspacken(nachtrag.pfad.name)
        self.assertEqual(self.daten / "Backup", erg.ordner.parent)
        self.assertEqual([], erg.fehler)
        bilder = erg.ordner / "Daten" / "Bilder"
        self.assertEqual(b"gleich", (bilder / "bleibt.png").read_bytes())
        self.assertEqual(b"neuer inhalt", (bilder / "aendert.png").read_bytes())
        self.assertEqual(b"frisch", (bilder / "neu.png").read_bytes())
        self.assertFalse((erg.ordner / "Daten" / "Profile" / "weg.json").exists())
        self.assertEqual(4, erg.dateien)
        self.assertEqual(2, len(erg.quellen))
        zweiter = B.auspacken(nachtrag.pfad.name)                      # nie überschreiben
        self.assertNotEqual(erg.ordner, zweiter.ordner)

    def test_auspacken_ohne_volles_backup(self):
        voll = self._voll()
        _schreibe(self.daten / "Bilder" / "neu.png", b"frisch")
        nachtrag = B.erstellen("teil")
        voll.pfad.rename(voll.pfad.with_suffix(".weg"))
        with self.assertRaisesRegex(ValueError, "volle Backup"):
            B.auspacken(nachtrag.pfad.name)
        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            B.auspacken(r"..\..\fremd.zip")

    def test_unsichere_pfade(self):
        for name in ("../x", "/abs", "C:/x", "a/../../b", "a\\b", ""):
            self.assertIsNone(B._sicherer_name(name), name)
        self.assertEqual(Path("Daten", "Bilder", "a.png"), B._sicherer_name("Daten/Bilder/a.png"))

    def test_werkzeug_nichts_neu_ist_kein_fehler(self):
        import actions
        self._voll()
        res = actions._backup_erstellen({"tool": "backup_erstellen", "art": "teil"})
        self.assertTrue(res.ok)
        self.assertIn("nichts geändert", res.text)


if __name__ == "__main__":
    unittest.main()
