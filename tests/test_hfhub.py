"""⬇️ Hugging Face im Controllcenter: Verbindung nur nach dem OK, zu mit dem Fenster; Suche, Dateien, Prüfsummen,
Pickle-Sperre, Laden mit Fortsetzen – ohne Netz (Schein-Client und Schein-Antworten).

python -m unittest tests.test_hfhub
"""

import hashlib
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hfhub  # noqa: E402
import hfladen  # noqa: E402

SHA = "a" * 64
GIT = "b" * 40


class _Client:
    def __init__(self, **einstellungen):
        self.einstellungen, self.zu = einstellungen, False

    def close(self):
        self.zu = True


class Tor(unittest.TestCase):

    def setUp(self):
        hfhub.TOR.schliessen()
        self.addCleanup(hfhub.TOR.schliessen)

    def test_zu_bis_zum_oeffnen_und_zu_beim_schliessen(self):
        self.assertFalse(hfhub.TOR.offen())
        with self.assertRaises(hfhub.Geschlossen):
            hfhub.suchen("qwen")
        hfhub.TOR.oeffnen(False, client_fabrik=_Client)
        c = hfhub.TOR.client()
        self.assertNotIn("Authorization", c.einstellungen["headers"])
        stopp = hfhub.TOR.stopp()
        hfhub.TOR.schliessen()
        self.assertTrue(c.zu and stopp.is_set() and not hfhub.TOR.offen())          # alles getrennt
        self.assertTrue(hfhub.TOR.stopp().is_set())                                 # zu: sofort angehalten

    def test_mit_schluessel(self):
        with mock.patch.dict(os.environ, {"HF_TOKEN": ""}):
            with self.assertRaises(ValueError):
                hfhub.TOR.oeffnen(True, client_fabrik=_Client)
            self.assertFalse(hfhub.hat_schluessel())
        with mock.patch.dict(os.environ, {"HF_TOKEN": "hf_beispiel"}):
            hfhub.TOR.oeffnen(True, client_fabrik=_Client)
            self.assertEqual(hfhub.TOR.client().einstellungen["headers"]["Authorization"], "Bearer hf_beispiel")
            self.assertTrue(hfhub.TOR.mit_schluessel())
        self.assertTrue(hfhub.schluessel_pruefen(lambda url: {"type": "user"}))
        self.assertFalse(hfhub.schluessel_pruefen(mock.Mock(side_effect=PermissionError())))

    def test_schluessel_nie_bei_gestarteten_programmen(self):
        import config
        with mock.patch.dict(os.environ, {"HF_TOKEN": "hf_beispiel"}):
            self.assertNotIn("HF_TOKEN", config.umgebung_ohne_geheimnisse())

    def test_schluessel_im_tresor_bekannt(self):
        import kontrollzentrum as KZ
        with mock.patch("tresor.stand", return_value={}, create=True):
            daten = KZ.tresor_eintraege(set())
        namen = {e["name"] for g in daten["gruppen"] for e in g["eintraege"]}
        self.assertIn("HF_TOKEN", namen)


class Suchen(unittest.TestCase):

    def test_suche_mit_art_und_sauberen_namen(self):
        gesehen = {}

        def holen(url, params=None):
            gesehen.update(url=url, params=params)
            return [{"id": "example/Model-A", "downloads": 5, "likes": 2, "gated": "auto"},
                    {"id": "../boese"}, {"modelId": "example/Model-B"}]
        liste = hfhub.suchen("  qwen   3 " + "x" * 300, "bild", holen)
        self.assertEqual([t["id"] for t in liste], ["example/Model-A", "example/Model-B"])
        self.assertTrue(liste[0]["gated"])
        self.assertEqual(gesehen["params"]["pipeline_tag"], "text-to-image")
        self.assertLessEqual(len(gesehen["params"]["search"]), hfhub.MAX_SUCHE)
        self.assertEqual(hfhub.suchen("", "chat", holen) and gesehen["params"]["filter"], "gguf")


class Dateien(unittest.TestCase):

    def _holen(self, baum, info=None):
        def holen(url, params=None):
            return baum if "/tree/" in url else (info or {"sha": "c" * 40, "cardData": {"license": "apache-2.0"}})
        return holen

    def test_pruefsummen_pickle_und_pfade(self):
        baum = [{"type": "file", "path": "model.safetensors", "size": 10, "lfs": {"oid": SHA, "size": 10_000}},
                {"type": "file", "path": "config.json", "size": 300, "oid": GIT},
                {"type": "file", "path": "old/pytorch_model.bin", "size": 9, "lfs": {"oid": SHA, "size": 9}},
                {"type": "file", "path": "../ausbruch.txt", "size": 1, "oid": GIT},
                {"type": "file", "path": "riesig.txt", "size": hfhub.MAX_KLEIN + 1, "oid": GIT},
                {"type": "file", "path": "assets/bild.png", "size": 5, "oid": GIT},
                {"type": "directory", "path": "vae"}]
        d = hfhub.dateien("example/Model-A", "bild", self._holen(baum))
        nach = {e.pfad: e for e in d["eintraege"]}
        self.assertNotIn("../ausbruch.txt", nach)
        self.assertEqual((nach["model.safetensors"].sha256, nach["model.safetensors"].groesse), (SHA, 10_000))
        self.assertEqual(nach["config.json"].git, GIT)
        self.assertIn("Pickle", nach["old/pytorch_model.bin"].gesperrt)
        self.assertTrue(nach["riesig.txt"].gesperrt)
        self.assertTrue(nach["model.safetensors"].vorgewaehlt and nach["config.json"].vorgewaehlt)
        self.assertFalse(nach["assets/bild.png"].vorgewaehlt or nach["old/pytorch_model.bin"].vorgewaehlt)
        self.assertEqual((d["revision"], d["lizenz"], d["hinweis"]), ("c" * 40, "apache-2.0", ""))
        with self.assertRaises(ValueError):
            hfhub.dateien("kein repo", "", self._holen([]))

    def test_chat_waehlt_eine_groesse_und_das_bildmodul(self):
        baum = [{"type": "file", "path": f"M-{q}.gguf", "size": 1, "lfs": {"oid": SHA, "size": g}}
                for q, g in (("Q8_0", 8), ("Q4_K_M", 4), ("Q2_K", 2))]
        baum += [{"type": "file", "path": "mmproj-F16.gguf", "size": 1, "lfs": {"oid": SHA, "size": 1}},
                 {"type": "file", "path": "README.md", "size": 10, "oid": GIT}]
        d = hfhub.dateien("example/M-GGUF", "chat", self._holen(baum))
        self.assertEqual({e.pfad for e in d["eintraege"] if e.vorgewaehlt}, {"M-Q4_K_M.gguf", "mmproj-F16.gguf",
                                                                             "README.md"})

    def test_lizenz_hinweise(self):
        self.assertIn("nicht-kommerziell", hfhub.lizenz_hinweis("cc-by-nc-4.0"))
        self.assertIn("Modellseite", hfhub.lizenz_hinweis("other"))
        self.assertIn("Keine Lizenz", hfhub.lizenz_hinweis(""))
        self.assertEqual(hfhub.lizenz_hinweis("mit"), "")
        self.assertEqual(hfhub.lizenz({"tags": ["license:mit"]}), "mit")


class Laden(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.basis = Path(tmp.name) / "Modell"
        hfhub.TOR.oeffnen(False, client_fabrik=_Client)
        self.addCleanup(hfhub.TOR.schliessen)

    def test_grosse_und_kleine_dateien_in_unterordnern(self):
        inhalt = b'{"a": 1}'
        git = hashlib.sha1(b"blob %d\0" % len(inhalt) + inhalt).hexdigest()
        eintraege = [hfhub.Eintrag("vae/model.safetensors", 4, sha256=SHA),
                     hfhub.Eintrag("vae/config.json", len(inhalt), git=git),
                     hfhub.Eintrag("weg.bin", 1, sha256=SHA, gesperrt="Pickle")]
        gerufen = []

        def laden(repo, datei, ordner, fortschritt, stopp, session=None, revision="main"):
            gerufen.append((datei.pfad, ordner, revision))
            fortschritt("lade", 4, 4)
            return ordner / datei.name
        antwort = mock.Mock(status_code=200, content=inhalt)
        meldungen = []
        with mock.patch.object(hfladen, "laden", side_effect=laden), \
                mock.patch("netzhilfe.abrufen", return_value=antwort):
            pfade = hfhub.laden("example/M", "c" * 40, eintraege, self.basis,
                                lambda *a: meldungen.append(a), threading.Event())
        self.assertEqual(gerufen, [("vae/model.safetensors", self.basis / "vae", "c" * 40)])
        self.assertEqual((self.basis / "vae" / "config.json").read_bytes(), inhalt)
        self.assertEqual(len(pfade), 2)                                            # die gesperrte nicht
        self.assertEqual(meldungen[0][:3], (1, "vae/model.safetensors", "lade"))

    def test_falsche_pruefsumme_wird_verworfen(self):
        e = hfhub.Eintrag("config.json", 3, git="0" * 40)
        with mock.patch("netzhilfe.abrufen", return_value=mock.Mock(status_code=200, content=b"abc")), \
                self.assertRaises(hfladen.PruefFehler):
            hfhub.laden("example/M", "main", [e], self.basis, lambda *a: None, threading.Event())
        self.assertFalse((self.basis / "config.json").exists())

    def test_zu_viele_anfragen_wartet_und_macht_weiter(self):
        versuche = []

        def laden(*a, **k):
            versuche.append(1)
            if len(versuche) == 1:
                raise OSError("Hugging Face antwortet mit 429")
            return self.basis / "m.safetensors"
        with mock.patch.object(hfladen, "laden", side_effect=laden), mock.patch.object(hfhub, "WARTEN_429_S", 0.01):
            hfhub.laden("example/M", "main", [hfhub.Eintrag("m.safetensors", 1, sha256=SHA)], self.basis,
                        lambda *a: None, threading.Event())
        self.assertEqual(len(versuche), 2)

    def test_angehalten_und_zu(self):
        stopp = threading.Event()
        stopp.set()
        with self.assertRaises(hfladen.Abbruch):
            hfhub.laden("example/M", "main", [hfhub.Eintrag("m.safetensors", 1, sha256=SHA)], self.basis,
                        lambda *a: None, stopp)
        hfhub.TOR.schliessen()
        with mock.patch.object(hfladen, "laden", side_effect=lambda *a, **k: hfhub.TOR.client()), \
                self.assertRaises(hfhub.Geschlossen):
            hfhub.laden("example/M", "main", [hfhub.Eintrag("m.safetensors", 1, sha256=SHA)], self.basis,
                        lambda *a: None, threading.Event())


class Sitzung(unittest.TestCase):

    def test_verbindung_zu_mit_dem_letzten_fenster(self):
        quelle = (ROOT / "main_dienste.py").read_text(encoding="utf-8")
        self.assertIn("bei_wechsel=fenster_gezaehlt", quelle)
        self.assertIn("if anzahl == 0:", quelle)
        self.assertIn('"hub_laden": hub_laden', quelle)
        teil = quelle[quelle.index("def hub_laden"):quelle.index("def hub_stopp")]
        self.assertIn("nur, was wir anboten", teil)                                 # die Auswahl des Fensters wird geprüft


class Fenster(unittest.TestCase):

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        import control_fenster as CF
        self.CF = CF
        self.gesendet: list[dict] = []
        leitung = mock.MagicMock()
        leitung.senden.side_effect = self.gesendet.append
        self.f = CF.ControlFenster(leitung)
        self.addCleanup(self.f.deleteLater)
        self.hub = self.f.hub

    def test_oeffnen_nur_nach_dem_ok(self):
        from PySide6.QtWidgets import QMessageBox
        self.f._ereignis({"t": "hub_stand", "offen": False, "hat_schluessel": False,
                          "arten": [["chat", "💬 Chat"], ["bild", "🎨 Bild"]]})
        self.assertFalse(self.hub.suchbereich.isEnabled())
        self.assertFalse(self.hub.k_schluessel.isEnabled())                          # ohne Schlüssel im Tresor
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.hub.k_frei.click()
        self.assertNotIn("hub_oeffnen", [g.get("op") for g in self.gesendet])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.hub.k_frei.click()
        self.assertEqual(self.gesendet[-1], {"op": "hub_oeffnen", "mit_schluessel": False})
        self.f._ereignis({"t": "hub_stand", "offen": True, "hat_schluessel": False})
        self.assertTrue(self.hub.suchbereich.isEnabled())
        self.assertEqual(self.hub.art_box.count(), 3)

    def test_suchen_dateien_laden_esc_und_schliessen(self):
        self.f._ereignis({"t": "hub_stand", "offen": True, "arten": [["chat", "💬 Chat"]]})
        self.hub.suche.setText("qwen")
        self.hub.art_box.setCurrentIndex(1)
        self.hub._suchen()
        self.assertEqual(self.gesendet[-1], {"op": "hub_suchen", "text": "qwen", "art": "chat"})
        self.f._ereignis({"t": "hub_treffer", "liste": [{"id": "example/M-GGUF", "downloads": 1, "likes": 0}]})
        self.hub._dateien_holen("example/M-GGUF")
        self.assertEqual(self.gesendet[-1], {"op": "hub_dateien", "repo": "example/M-GGUF", "art": "chat"})
        self.f._ereignis({"t": "hub_dateien", "repo": "example/M-GGUF", "art": "chat", "lizenz": "mit", "ziel": "x",
                          "eintraege": [{"pfad": "a.gguf", "groesse": 10, "vorgewaehlt": True},
                                        {"pfad": "b.gguf", "groesse": 20},
                                        {"pfad": "c.bin", "groesse": 5, "gesperrt": "Pickle-Format"}]})
        self.assertEqual(set(self.hub.haken), {"a.gguf", "b.gguf"})                    # die gesperrte nicht wählbar
        self.hub._laden()
        self.assertEqual(self.gesendet[-1], {"op": "hub_laden", "repo": "example/M-GGUF", "art": "chat",
                                             "pfade": ["a.gguf"]})
        self.f._ereignis({"t": "hub_stand", "offen": True, "laeuft": True})
        self.f._ereignis({"t": "hub_fortschritt", "nr": 1, "anzahl": 1, "pfad": "a.gguf", "phase": "lade",
                          "erledigt": 5, "groesse": 10, "davor": 0, "gesamt": 10})
        self.assertEqual(self.hub.balken_datei.value(), 500)
        self.f._esc_gedrueckt()                                                     # ESC hält auch den Download an
        self.assertEqual(self.gesendet[-1], {"op": "hub_stopp"})
        self.f._ereignis({"t": "hub_fertig", "repo": "example/M-GGUF", "art": "chat", "ordner": "x", "anzahl": 1})
        self.assertFalse(self.hub.k_modell.isHidden())
        self.hub.k_modell.click()
        self.assertEqual(self.gesendet[-1], {"op": "ausfuehren", "befehl": "/model", "zusatz": ""})
        self.f.close()                                                              # Fenster zu: Verbindung zu
        self.assertEqual(self.gesendet[-1], {"op": "hub_schliessen"})


if __name__ == "__main__":
    unittest.main()
