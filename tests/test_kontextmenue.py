"""Offline-Test: Rechtsklick-Menü (tools/kontextmenue.py) – Befehle, Registry (HKCU), Screenshot.

python -m unittest discover -s tests -p test_kontextmenue.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import kontextmenue as K                          # noqa: E402

ECHT_STARTEN = K.nemicli_starten                  # andere Tests ersetzen sie


class BefehlTests(unittest.TestCase):
    def test_befehle_zeigen_auf_skript(self):
        b = K.befehle()
        self.assertIn("kontextmenue.py", b["screenshot"])
        self.assertTrue(b["screenshot"].endswith(" screenshot"))
        self.assertIn('bild "%1"', b["bild"])
        self.assertTrue(b["screenshot"].startswith('"'))          # Pfade mit Leerzeichen sicher

    def test_screenshot_in_ordner_ohne_wartezeit(self):
        K._hinweis = lambda *a, **k: None                         # kein Fenster im Test
        K.in_zwischenablage = lambda t: True
        with tempfile.TemporaryDirectory() as d:
            p = K.screenshot(warte_s=0, ordner=Path(d), starten=False)
            self.assertIsNotNone(p)
            self.assertTrue(p.exists() and p.suffix == ".png")

    def test_bild_fehlend(self):
        K._hinweis = lambda *a, **k: None
        self.assertFalse(K.bild("C:/gibt/es/nicht.png"))

    def test_offenes_fenster_bekommt_die_nachricht(self):
        import time
        gestartet = []
        K.nemicli_starten = lambda n: gestartet.append(n) or True
        K._hinweis = lambda *a, **k: None
        with tempfile.TemporaryDirectory() as d:
            K.ALIVE_DATEI, K.INBOX_DATEI = Path(d) / "alive", Path(d) / "inbox"
            self.assertFalse(K.nemicli_laeuft())
            K.uebergeben("hallo")                       # kein Fenster -> neues starten
            self.assertEqual(gestartet, ["hallo"])
            K.ALIVE_DATEI.write_text(str(time.time()))  # Fenster lebt
            self.assertTrue(K.nemicli_laeuft())
            K.uebergeben("bild")
            self.assertEqual(gestartet, ["hallo"])      # NICHT neu gestartet
            self.assertEqual(K.INBOX_DATEI.read_text(encoding="utf-8"), "bild")

    def test_antwort_tag_und_text(self):
        self.assertEqual(K.antwort_tag("[[einfuegen:4711]] Schreib die Antwort"),
                         (4711, "Schreib die Antwort"))
        self.assertEqual(K.antwort_tag("normal"), (None, "normal"))
        self.assertEqual(K.antwort_tag("[[einfuegen:x]] y"), (None, "[[einfuegen:x]] y"))
        roh = ("Hallo Herr Müller,\n\nvielen Dank für Ihre Nachricht.\n\nViele Grüße\ntest_user\n\n"
               "```aktion\n{\"tool\": \"merken\", \"text\": \"x\"}\n```")
        self.assertEqual(K.antwort_text(roh),
                         "Hallo Herr Müller,\n\nvielen Dank für Ihre Nachricht.\n\nViele Grüße\ntest_user")
        self.assertEqual(K.antwort_text('"Danke!"'), "Danke!")
        self.assertEqual(K.antwort_text("```\nText\n```"), "Text")

    def test_antwort_auftrag_traegt_fenster(self):
        gesendet = []
        orig = (K.uebergeben, K.zielfenster)
        K.uebergeben = lambda n: gesendet.append(n) or True
        K.zielfenster = lambda: 12345
        K._hinweis = lambda *a, **k: None
        K.in_zwischenablage = lambda t: True
        try:
            with tempfile.TemporaryDirectory() as d:
                p = K.antwort(warte_s=0, ordner=Path(d))
                self.assertIsNotNone(p)
        finally:
            K.uebergeben, K.zielfenster = orig
        self.assertEqual(len(gesendet), 1)
        hwnd, auftrag = K.antwort_tag(gesendet[0])
        self.assertEqual(hwnd, 12345)
        self.assertIn("NUR den fertigen Antworttext", auftrag)
        self.assertIn(str(p), auftrag)

    def test_start_uebergibt_sag(self):
        gestartet = []
        K.nemicli_starten = lambda n: gestartet.append(n) or True
        K.in_zwischenablage = lambda t: True
        K._hinweis = lambda *a, **k: None
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False); f.write(b"x"); f.close()
        self.assertTrue(K.bild(f.name))
        self.assertEqual(len(gestartet), 1)
        self.assertIn(f.name, gestartet[0])
        self.assertTrue(gestartet[0].startswith("Schau dir bitte dieses Bild an"))


@unittest.skipUnless(sys.platform.startswith("win"), "Registry gibt es nur unter Windows")
class StartOhneBefehlszeileTests(unittest.TestCase):
    def test_text_nur_ueber_die_inbox(self):
        """Dateinamen mit %VAR% oder & dürfen nie durch cmd laufen: der Text geht in die Inbox."""
        aufrufe = []
        alt = (K.INBOX_DATEI, K.NEMICLI_CMD, K.subprocess.Popen)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                K.INBOX_DATEI = Path(tmp) / ".nemicli.inbox"
                K.NEMICLI_CMD = Path(tmp) / "nemicli.cmd"
                K.NEMICLI_CMD.write_text("@echo off", encoding="utf-8")
                K.subprocess.Popen = lambda args, **kw: aufrufe.append(args)
                text = "Schau dir bitte dieses Bild an: C:\Bilder\%USERNAME% & Co.png"
                self.assertTrue(ECHT_STARTEN(text))
                self.assertEqual(K.INBOX_DATEI.read_text(encoding="utf-8"), text)
                self.assertEqual(aufrufe, [["cmd", "/c", "start", "", str(K.NEMICLI_CMD)]])
            finally:
                K.INBOX_DATEI, K.NEMICLI_CMD, K.subprocess.Popen = alt


class RegistryTests(unittest.TestCase):
    """Schreibt in einen Test-Schlüssel unter HKCU und räumt wieder auf."""

    def setUp(self):
        self._orig = (K._KEY_SCREEN, K._KEY_BILD, K._KEY_ANTWORT)
        K._KEY_SCREEN = [r"Software\Classes\NemiCLI-Test\Background\shell\NemiCLI.Screenshot"]
        K._KEY_BILD = [r"Software\Classes\NemiCLI-Test\image\shell\NemiCLI.Ansehen"]
        K._KEY_ANTWORT = [r"Software\Classes\NemiCLI-Test\Background\shell\NemiCLI.Antwort"]

    def tearDown(self):
        K.entfernen()
        K._KEY_SCREEN, K._KEY_BILD, K._KEY_ANTWORT = self._orig

    def test_eintragen_status_entfernen(self):
        self.assertEqual(K.status(), {"bildschirm": False, "bild": False, "antwort": False})
        labels = K.installieren()
        self.assertEqual(len(labels), 3)
        self.assertEqual(K.status(), {"bildschirm": True, "bild": True, "antwort": True})
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, K._KEY_SCREEN[0] + r"\command") as k:
            cmd, _ = winreg.QueryValueEx(k, "")
        self.assertEqual(cmd, K.befehle()["screenshot"])
        self.assertEqual(K.entfernen(), 3)
        self.assertEqual(K.status(), {"bildschirm": False, "bild": False, "antwort": False})
        self.assertEqual(K.entfernen(), 0)


if __name__ == "__main__":
    unittest.main()
