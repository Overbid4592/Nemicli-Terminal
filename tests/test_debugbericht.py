"""Debugbericht: aus ohne Schalter, Datei je Prozess, Ereignisse mit Speicherstand, Fehler aus
Threads und asyncio, Prozessliste über den Leistungszähler."""
import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import debugbericht as D          # noqa: E402


class Bericht(unittest.TestCase):
    def setUp(self):
        self.ordner = Path(tempfile.mkdtemp()) / "Debugbericht"
        D.beenden()
        self.addCleanup(D.beenden)

    def _an(self):
        p = mock.patch.object(D, "an", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_aus_ohne_schalter(self):
        with mock.patch.object(D, "an", return_value=False):
            self.assertIsNone(D.starten("test", self.ordner, takt=False))
            D.ereignis("nichts")
            D.fehler("nichts")
        self.assertFalse(self.ordner.exists())
        self.assertFalse(D.laeuft())

    def test_datei_je_prozess_mit_kopf_und_ereignissen(self):
        self._an()
        pfad = D.starten("haupt", self.ordner, takt=False)
        self.assertTrue(D.laeuft())
        self.assertIn(f"_haupt_{os.getpid()}.log", pfad.name)
        self.assertIs(D.starten("haupt", self.ordner, takt=False), pfad)     # zweiter Start: dieselbe Datei
        D.ereignis("Modell geladen: Test")
        with D.messen("Werkzeug test"):
            pass
        try:
            raise ValueError("kaputt")
        except ValueError:
            D.fehler("Probe")
        D.beenden()
        text = pfad.read_text(encoding="utf-8")
        self.assertIn(f"Rolle haupt, PID {os.getpid()}", text)
        self.assertIn("Prozesse mit Grafikspeicher", text)
        self.assertIn("Modell geladen: Test   [VRAM", text)
        self.assertIn("Werkzeug test fertig (", text)
        self.assertIn("FEHLER Probe", text)
        self.assertIn("ValueError: kaputt", text)
        self.assertIn("  Ende", text)
        self.assertFalse(D.laeuft())

    def test_fehler_aus_thread_und_asyncio(self):
        self._an()
        pfad = D.starten("wache", self.ordner, takt=False)

        def kaputt():
            raise RuntimeError("im Thread")
        alt = threading.excepthook
        try:
            threading.excepthook = lambda args: None       # Testlauf nicht mit dem Fehler beschmutzen
            D._haken_gesetzt = False                        # Haken über dem stummen Original setzen
            D._fehlerhaken()
            t = threading.Thread(target=kaputt)
            t.start()
            t.join()
        finally:
            threading.excepthook = alt

        async def lauf():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda lp, k: None)
            D.schleife(loop)
            D.schleife(loop)                                # kein doppelter Haken
            loop.call_exception_handler({"message": "Aufgabe", "exception": KeyError("asyncio")})
        asyncio.run(lauf())
        text = pfad.read_text(encoding="utf-8")
        self.assertIn("FEHLER Thread", text)
        self.assertIn("RuntimeError: im Thread", text)
        self.assertEqual(text.count("FEHLER asyncio: Aufgabe"), 1)
        self.assertIn("KeyError: 'asyncio'", text)

    def test_zaehler_nach_beenden_neu(self):
        # Ein geschlossener Zähler darf nicht bleiben, sonst ist die Prozessliste nach aus → an leer.
        self._an()
        D.starten("haupt", self.ordner, takt=False)
        D.prozesse()
        D.beenden()
        self.assertIsNone(D._zaehler)

    def test_prozessliste(self):
        liste = D.prozesse()
        self.assertIsInstance(liste, list)
        for name, pid, mb in liste:
            self.assertIsInstance(name, str)
            self.assertIsInstance(pid, int)
            self.assertGreater(mb, 0)
        self.assertEqual(liste, sorted(liste, key=lambda x: -x[2]))

    def test_arbeitsspeicher_in_zeile_und_tabelle(self):
        self._an()
        eigen = os.getpid()
        with mock.patch.object(D, "ram_eigen", return_value={"ram": 3277, "privat": 4198, "gemeinsam": 1126}):
            self.assertIn("RAM 3,2 GB · privat 4,1 GB · festgenagelt 1,1 GB", D._speicher_kurz())
        with mock.patch.object(D, "prozesse", return_value=[("NemiCLIc.exe", eigen, 900)]), \
                mock.patch.object(D, "gemeinsam", return_value={eigen: 27000, 4242: 5000}), \
                mock.patch.object(D, "_prozessname", return_value="chrome.exe"), \
                mock.patch.object(D, "_nutzer_stand", ([("chrome.exe", 17, 21000), ("NemiCLIc.exe", 1, 3000)], 0.0)), \
                mock.patch.object(D, "ram_system", return_value=(51000, 65400)), \
                mock.patch.object(D, "ram_nutzer", side_effect=AssertionError("nicht im Haupt-Faden")):
            pfad = D.starten("haupt", self.ordner, takt=False)
            D.tabelle("Probe")
        text = pfad.read_text(encoding="utf-8")
        self.assertIn("900 MB  NemiCLIc.exe", text)
        self.assertIn("+ 27.000 MB festgenagelt  <- dieser Prozess", text)
        self.assertIn("0 MB  chrome.exe (4242)  + 5.000 MB festgenagelt", text)    # nur festgenagelt, kein VRAM
        self.assertIn("Größte Arbeitsspeicher-Nutzer (Rechner: 49,8 GB von 63,9 GB belegt)", text)
        self.assertIn("21.000 MB  chrome.exe (17 Prozesse)", text)
        kurz = D.verdichten(text, tabelle_zeilen=1)
        self.assertIn("21.000 MB  chrome.exe", kurz)                              # eigene Tabelle, nicht abgeschnitten
        self.assertNotIn("3.000 MB  NemiCLIc.exe", kurz)

    def test_ram_nutzer_nur_im_hintergrund_neu(self):
        with mock.patch.object(D, "_nutzer_stand", ([("alt.exe", 1, 1)], 0.0)), \
                mock.patch.object(D, "ram_nutzer", return_value=[("neu.exe", 1, 2)]) as messen:
            self.assertEqual(D._nutzer_jetzt(), [("alt.exe", 1, 1)])               # Haupt-Faden: letzte Messung
            ergebnis = []
            f = threading.Thread(target=lambda: ergebnis.append(D._nutzer_jetzt()))
            f.start()
            f.join()
            self.assertEqual(ergebnis, [[("neu.exe", 1, 2)]])
            self.assertEqual(messen.call_count, 1)

    def test_echte_messung(self):
        r = D.ram_eigen()
        if r is None:
            self.skipTest("psutil fehlt")
        self.assertGreater(r["ram"], 0)
        belegt, gesamt = D.ram_system()
        self.assertLess(0, belegt)
        self.assertLessEqual(belegt, gesamt)
        self.assertIsInstance(D.gemeinsam(), dict)

    def test_haken_in_den_modulen(self):
        import gguflokal
        import stimme
        self.assertIs(gguflokal._DBG, D)
        self.assertIs(stimme._DBG, D)
        self._an()
        pfad = D.starten("haupt", self.ordner, takt=False)
        with mock.patch.object(gguflokal, "_motor", ("datei", "k", object())):
            with mock.patch("ggufengine.models.common.release_pinned_memory", create=True):
                gguflokal.entladen()
        self.assertIn("Modell entladen", pfad.read_text(encoding="utf-8"))


BERICHT = """NemiCLI Debugbericht – Rolle haupt, PID 1, Version 6.8 Alpha (2026.10.09)
Start 09.10.2026 09:54:29   Python 3.12.10   exe True
09:54:29.267 +     0.2s  Prozesse mit Grafikspeicher – Start:
                           4.287 MB  NemiCLIc.exe (1)  <- dieser Prozess
                           2.258 MB  dwm.exe (2)
                             195 MB  chrome.exe (3)
                             195 MB  explorer.exe (4)
                              76 MB  WindowsTerminal.exe (5)
                              75 MB  csrss.exe (6)
09:54:39.270 +    10.2s  Takt   [VRAM torch belegt 3.979 MB. reserviert 5.522 MB | frei 9.506 / 16.283 MB]
09:54:49.273 +    20.3s  Takt   [VRAM torch belegt 3.979 MB. reserviert 5.522 MB | frei 9.506 / 16.283 MB]
09:54:59.275 +    30.3s  Takt   [VRAM torch belegt 3.979 MB. reserviert 5.522 MB | frei 9.506 / 16.283 MB]
09:55:43.976 +    75.0s  Runde: 93 Zeichen, 0 Bild(er)   [VRAM torch belegt 3.979 MB. reserviert 5.522 MB | frei 9.506 / 16.283 MB]
09:56:10.902 +   101.9s  Stimme freigegeben   [VRAM torch belegt 982 MB. reserviert 1.078 MB | frei 13.950 / 16.283 MB]
09:56:19.321 +   110.3s  Takt   [VRAM torch belegt 3.130 MB. reserviert 3.164 MB | frei 11.864 / 16.283 MB]
09:58:03.135 +   214.1s  Stimme geladen   [VRAM torch belegt 3.912 MB. reserviert 3.976 MB | frei 10.980 / 16.283 MB]
"""


class Werkzeug(unittest.TestCase):
    """`debugbericht` für die KI: nur Cloud + /debug an, verdichtet, als Fremd-Inhalt."""

    def test_verdichten(self):
        kurz = D.verdichten(BERICHT, tabelle_zeilen=3)
        self.assertIn("09:54:39.270 – 09:54:59.275  Takt ×3 unverändert", kurz)       # gleiche Takte = eine Spanne
        self.assertIn("09:56:19.321 +   110.3s  Takt   [VRAM torch belegt 3.130", kurz)  # einzelner Takt bleibt
        self.assertIn("Stimme geladen", kurz)
        self.assertIn("Stimme freigegeben", kurz)
        self.assertIn("chrome.exe", kurz)
        self.assertNotIn("csrss.exe", kurz)                                            # Tabelle gekappt
        self.assertIn("kleinere Prozesse weggelassen", kurz)
        lang = D.verdichten("Anfang\n" + "\n".join(f"Zeile {i}" for i in range(3000)) + "\nEnde", grenze=2000)
        self.assertLess(len(lang), 2300)
        self.assertTrue(lang.startswith("Anfang"))
        self.assertTrue(lang.endswith("Ende"))
        self.assertIn("Mitte gekürzt", lang)

    def test_cloud_modell(self):
        for ref, erwartet in (("gguf:Gemma", False), ("ollama:x", False), ("", False),
                              ("anthropic:claude-sonnet", True), ("openai:gpt", True)):
            with mock.patch("config.load", return_value={"model": ref}):
                self.assertEqual(D.cloud_modell(), erwartet, ref)

    def test_werkzeug(self):
        import actions
        import modes
        import persona
        self.assertIn("debugbericht", actions.ACTIONS)
        self.assertTrue(actions.ACTIONS["debugbericht"].get("entwickler"))   # nicht in der Standard-Anleitung
        self.assertIn("debugbericht", modes.READ_TOOLS)
        ordner = Path(tempfile.mkdtemp())
        (ordner / "2026-10-09_09-54-29_haupt_1.log").write_text(BERICHT, encoding="utf-8")
        (ordner / "2026-10-09_09-00-00_wache_2.log").write_text("Wache alt\n", encoding="utf-8")
        with mock.patch.object(D, "an", return_value=False), mock.patch.object(D, "cloud_modell", return_value=True), \
                mock.patch.object(D, "ordner", return_value=ordner):
            r = actions._debugbericht({})
            self.assertFalse(r.ok)
            self.assertIn("abgeschaltet", r.text)                         # Bericht liegt da, wird aber nicht gelesen
            self.assertNotIn("Stimme geladen", r.text)
            self.assertEqual(persona._debug_hinweis(), "")
        with mock.patch.object(D, "an", return_value=True), mock.patch.object(D, "cloud_modell", return_value=False), \
                mock.patch.object(D, "ordner", return_value=ordner):
            r = actions._debugbericht({})
            self.assertFalse(r.ok)
            self.assertIn("Cloud-Modell", r.text)
            self.assertNotIn("Stimme geladen", r.text)
            self.assertEqual(persona._debug_hinweis(), "")
        with mock.patch.object(D, "an", return_value=True), mock.patch.object(D, "cloud_modell", return_value=True), \
                mock.patch.object(D, "ordner", return_value=ordner):
            text = actions._debugbericht({})
            self.assertIn("debugbericht", persona._debug_hinweis())
        self.assertIsInstance(text, str)
        self.assertIn("_haupt_1.log", text)
        self.assertIn("_wache_2.log", text)
        self.assertIn("Stimme geladen", text)
        self.assertIn("Takt ×3", text)
        with mock.patch.object(D, "an", return_value=True), mock.patch.object(D, "cloud_modell", return_value=True), \
                mock.patch.object(D, "ordner", return_value=ordner / "leer"):
            r = actions._debugbericht({})
        self.assertFalse(r.ok)
        self.assertIn("Noch kein Debugbericht", r.text)

    def test_sichten_und_tiefer_lesen(self):
        import actions
        ordner = Path(tempfile.mkdtemp())
        fehler = ("09:59:00.000 +   300.0s  FEHLER Erzeugen   [VRAM torch belegt 1 MB. reserviert 1 MB | frei 1 / 2 MB]\n"
                  "    Traceback (most recent call last):\n    RuntimeError: kaputt\n"
                  "09:59:01.000 +   301.0s  Werkzeug plan …   [VRAM x]\n")
        spaeter = "".join(f"10:0{i // 6}:{i % 6}0.000 +   4{i:02d}.0s  Takt   [VRAM torch belegt {i} MB]\n"
                          for i in range(40))
        (ordner / "2026-10-09_09-54-29_haupt_1.log").write_text(BERICHT + fehler + spaeter, encoding="utf-8")
        (ordner / "2026-10-09_09-00-00_wache_2.log").write_text("Wache\n", encoding="utf-8")
        f = D.fuer_ki(ordner_pfad=ordner, was="fehler", rolle="haupt")
        self.assertIn("FEHLER Erzeugen", f)
        self.assertIn("RuntimeError: kaputt", f)
        self.assertNotIn("Werkzeug plan", f)
        self.assertNotIn("_wache_2.log", f)                               # nur die gewünschte Rolle
        s = D.fuer_ki(ordner_pfad=ordner, was="speicher", rolle="haupt")
        self.assertIn("Stimme geladen", s)
        self.assertIn("Takt ×3", s)
        self.assertIn("csrss.exe", s)                                     # Speicher-Sicht: Tabelle breiter
        self.assertNotIn("Runde: 93 Zeichen", s)
        a1 = D.fuer_ki(ordner_pfad=ordner, was="alles", ab=0, rolle="haupt", grenze=400)
        self.assertIn("NemiCLI Debugbericht", a1)
        self.assertIn("weiter ab Zeile", a1)
        naechste = int(a1.rsplit("ab=", 1)[1].rstrip(")\n"))
        a2 = D.fuer_ki(ordner_pfad=ordner, was="alles", ab=naechste, rolle="haupt", grenze=100_000)
        self.assertNotIn("NemiCLI Debugbericht", a2)
        self.assertIn("(Ende der Datei)", a2)
        self.assertIn("+   439.0s  Takt", a2)                              # bis zur letzten Zeile
        # Stück für Stück gelesen ergibt genau die Datei – nichts doppelt, nichts verloren
        gelesen, ab, runden = [], 0, 0
        while runden < 50:
            runden += 1
            seite = D.fuer_ki(ordner_pfad=ordner, was="alles", ab=ab, rolle="haupt", grenze=300)
            zeilen = seite.split("\n")[1:]                                # ohne Kopfzeile
            if zeilen and zeilen[-1] == "(Ende der Datei)":
                gelesen += zeilen[:-1]
                break
            gelesen += zeilen[:-1]
            ab = int(zeilen[-1].rsplit("ab=", 1)[1].rstrip(")"))
        self.assertEqual(gelesen, (BERICHT + fehler + spaeter).splitlines())
        with mock.patch.object(D, "an", return_value=True), mock.patch.object(D, "cloud_modell", return_value=True), \
                mock.patch.object(D, "ordner", return_value=ordner):
            r = actions._debugbericht({"was": "unsinn"})
            self.assertFalse(r.ok)
            self.assertIn("muss eines sein", r.text)
            r = actions._debugbericht({"was": "alles", "ab": "x"})
            self.assertFalse(r.ok)
            self.assertIsInstance(actions._debugbericht({"was": "alles", "ab": 3, "rolle": "haupt"}), str)

    def test_laeuft_immer_und_raeumt_auf(self):
        with mock.patch("config.load", return_value={}):
            self.assertTrue(D.an())                                       # Vorgabe: an
            self.assertFalse(D.motor_log_an())                            # Motor-Log nur auf Wunsch
        with mock.patch("config.load", return_value={"debug": False}):
            self.assertFalse(D.an())
        ordner = Path(tempfile.mkdtemp())
        for i in range(35):
            p = ordner / f"2026-10-0{i % 9 + 1}_00-00-{i:02d}_haupt_{i}.log"
            p.write_text("x", encoding="utf-8")
            os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
        self.assertEqual(D.aufraeumen(ordner, behalten=30), 5)
        rest = sorted(ordner.glob("*.log"), key=lambda p: p.stat().st_mtime)
        self.assertEqual(len(rest), 30)
        self.assertTrue(rest[0].name.endswith("_haupt_5.log"))            # die ältesten fünf sind weg
        with mock.patch.object(D, "an", return_value=True):               # jeder Start räumt auf
            D.starten("haupt", ordner, takt=False)
        self.addCleanup(D.beenden)
        self.assertEqual(len(list(ordner.glob("*.log"))), 30)
        with mock.patch("ggufengine.debuglog.enable") as enable, mock.patch("ggufengine.debuglog.path", return_value=None):
            with mock.patch.object(D, "motor_log_an", return_value=False):
                D.motor_log()
            enable.assert_not_called()                                    # Motor-Log nur auf Wunsch
            with mock.patch.object(D, "motor_log_an", return_value=True):
                D.motor_log()
            enable.assert_called_once()


if __name__ == "__main__":
    unittest.main()
