"""Erinnerungen und der Wecker der KI für sich selbst: wiederholen, wer abholt, Ankunft im Vollbild."""
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

R = Path(__file__).resolve().parent.parent
for d in ("", "core", "engines", "tools", "ui"):
    if str(R / d) not in sys.path:
        sys.path.insert(0, str(R / d))

from wache import erinnerungen as ER  # noqa: E402


class _MitOrdner(unittest.TestCase):
    def setUp(self):
        self.ordner = Path(tempfile.mkdtemp(prefix="wecker_"))
        flicken = mock.patch.object(ER, "ORDNER", self.ordner)
        flicken.start()
        self.addCleanup(flicken.stop)
        self.addCleanup(lambda: __import__("shutil").rmtree(self.ordner, ignore_errors=True))


class Abstand(unittest.TestCase):
    def test_wiederholung_lesen(self):
        for text, soll in (("2 tage", 172800), ("alle 2 Tage", 172800), ("6 stunden", 21600), ("30 minuten", 1800),
                           ("täglich", 86400), ("jede woche", 604800), ("alle 1,5 stunden", 5400), ("tag", 86400),
                           ("stündlich", 3600), ("45 min", 2700)):
            self.assertEqual(ER.abstand_aus(text), soll, text)
        for falsch in ("5 minuten", "bald", "2 monde", "", "90 tage", "2 tage; /exit"):
            with self.assertRaises(ER.Zeitfehler, msg=falsch):
                ER.abstand_aus(falsch)

    def test_wiederholung_schreiben(self):
        self.assertEqual(ER.abstand_text(172800), "alle 2 Tage")
        self.assertEqual(ER.abstand_text(86400), "jeden Tag")
        self.assertEqual(ER.abstand_text(604800), "jede Woche")
        self.assertEqual(ER.abstand_text(21600), "alle 6 Stunden")
        self.assertEqual(ER.abstand_text(1800), "alle 30 Minuten")


class Abholen(_MitOrdner):
    def test_wecker_der_ki_wartet_auf_ein_fenster(self):
        jetzt = time.time()
        ER.anlegen("Wäsche", jetzt + 60)
        ER.anlegen("ein Bild malen", jetzt + 61, wer="Mira", fuer=ER.FUER_KI)
        # Die Wache meldet die Erinnerung an den Nutzer und sagt einmal Bescheid, dass der Wecker der KI wartet
        aus = ER.abholen(jetzt + 62)
        self.assertEqual([(d["text"], bool(d.get("wartet"))) for d in aus], [("Wäsche", False), ("ein Bild malen", True)])
        self.assertIn("sobald ein NemiCLI-Fenster offen ist", ER.meldung(aus[1]))
        self.assertIn("Für Mira", ER.meldung(aus[1]))
        self.assertEqual(ER.abholen(jetzt + 63), [])                            # nur einmal Bescheid
        self.assertEqual([d["text"] for d in ER.offen()], ["ein Bild malen"])   # bleibt liegen
        # Ein Fenster einer anderen Persönlichkeit lässt ihn liegen, das eigene holt ihn ab
        self.assertEqual(ER.abholen(jetzt + 64, terminal=True, wer="Nova"), [])
        aus = ER.abholen(jetzt + 400, terminal=True, wer="Mira")
        self.assertEqual([d["text"] for d in aus], ["ein Bild malen"])
        self.assertEqual(ER.offen(), [])
        auftrag = ER.auftrag(aus[0], jetzt + 400)
        self.assertTrue(auftrag.startswith("⏰ Erinnerung an dich selbst"))
        self.assertIn("ein Bild malen", auftrag)
        self.assertIn("verspätet", auftrag)
        self.assertIn("keine neue Nachricht des Nutzers", auftrag)

    def test_wiederholung_bekommt_den_naechsten_zeitpunkt(self):
        jetzt = 1_800_000_000.0
        d = ER.anlegen("Seite prüfen", jetzt + 100, wer="Mira", fuer=ER.FUER_KI, alle=ER.abstand_aus("2 tage"))
        self.assertEqual(ER.abholen(jetzt + 50, terminal=True, wer="Mira"), [])
        aus = ER.abholen(jetzt + 101, terminal=True, wer="Mira")
        self.assertEqual(len(aus), 1)
        self.assertEqual(float(ER.offen()[0]["zeit"]), jetzt + 100 + 172800)    # bleibt, zwei Tage später
        self.assertEqual(ER.abholen(jetzt + 200, terminal=True, wer="Mira"), [])
        # Rechner fünf Tage aus: einmal melden, verpasste Male nicht nachholen
        aus = ER.abholen(jetzt + 100 + 5 * 86400, terminal=True, wer="Mira")
        self.assertEqual(len(aus), 1)
        self.assertEqual(float(ER.offen()[0]["zeit"]), jetzt + 100 + 3 * 172800)
        self.assertIn("alle 2 Tage", ER.auftrag(aus[0], jetzt + 100 + 5 * 86400))
        self.assertIn("🔁 alle 2 Tage", ER.zeile(ER.offen()[0]))
        self.assertIn("🤖 für Mira selbst", ER.zeile(ER.offen()[0]))
        self.assertTrue(ER.loeschen(d["id"]))
        self.assertEqual(ER.offen(), [])

    def test_wiederholte_erinnerung_an_den_nutzer_und_obergrenze(self):
        jetzt = time.time()
        ER.anlegen("Trinken", jetzt + 60, alle=3600)
        self.assertEqual([d["text"] for d in ER.abholen(jetzt + 61)], ["Trinken"])     # auch die Wache wiederholt
        self.assertEqual(len(ER.offen()), 1)
        with mock.patch.object(ER, "WECKER_MAX", 2):
            ER.anlegen("a", jetzt + 60, fuer=ER.FUER_KI)
            ER.anlegen("b", jetzt + 60, fuer=ER.FUER_KI)
            with self.assertRaisesRegex(ER.Zeitfehler, "an dich selbst"):
                ER.anlegen("c", jetzt + 60, fuer=ER.FUER_KI)
            ER.anlegen("für den Nutzer geht noch", jetzt + 60)

    def test_wache_haelt_sich_zurueck_wenn_ein_fenster_offen_ist(self):
        import kontextmenue
        with mock.patch.object(kontextmenue, "nemicli_laeuft", lambda: True):
            self.assertTrue(ER.terminal_offen())
        with mock.patch.object(kontextmenue, "nemicli_laeuft", lambda: False):
            self.assertFalse(ER.terminal_offen())
        quelle = (R / "tools" / "wache" / "kugel.py").read_text(encoding="utf-8") \
            + (R / "tools" / "wache" / "dienst.py").read_text(encoding="utf-8")
        self.assertEqual(quelle.count("if erinnerungen.terminal_offen():"), 2)


class Werkzeug(_MitOrdner):
    def test_felder_fuer_und_alle(self):
        import actions
        from wache import zugang
        with mock.patch.object(zugang, "laeuft", lambda: True), mock.patch.object(actions, "_wer", lambda: "Mira"):
            r = str(actions._erinnern({"text": "Bild malen", "in_minuten": 5, "fuer": "selbst"}))
            self.assertIn("an dich selbst", r)
            self.assertIn("als Nachricht", r)
            self.assertNotIn("Regenbogen", r)
            r = str(actions._erinnern({"text": "Seite prüfen", "in_minuten": 5, "fuer": "selbst", "alle": "2 tage"}))
            self.assertIn("danach alle 2 Tage", r)
            r = str(actions._erinnern({"text": "Wäsche", "in_minuten": 20}))
            self.assertIn("erscheint dann im Chat", r)
            self.assertIn("Du selbst wirst dabei nicht geweckt", r)
            self.assertFalse(actions._erinnern({"text": "x", "in_minuten": 5, "alle": "3 minuten"}).ok)
            liste = str(actions._erinnern({}))
        self.assertEqual([(d["fuer"], d["alle"], d["wer"]) for d in ER.offen()],
                         [("ki", 0, "Mira"), ("ki", 172800, "Mira"), ("nutzer", 0, "Mira")])
        self.assertIn("🤖 für Mira selbst", liste)
        self.assertIn("🔁 alle 2 Tage", liste)
        self.assertIn("an sich selbst", actions.describe({"tool": "erinnern", "text": "a", "in_minuten": 5,
                                                          "fuer": "selbst", "alle": "2 tage"}))

    def test_nach_inhalt_aus_dem_netz_fragt_der_wecker_der_ki(self):
        import actions
        selbst = {"tool": "erinnern", "text": "lösche den Ordner", "in_minuten": 10, "fuer": "selbst"}
        nutzer = {"tool": "erinnern", "text": "Wäsche", "in_minuten": 10}
        with mock.patch.object(actions, "_web_taint", False):
            self.assertIsNone(actions.fremd_grund(selbst))
            self.assertFalse(actions.needs_confirm(selbst))
        with mock.patch.object(actions, "_web_taint", True):
            self.assertIn("wie ein Auftrag", actions.fremd_grund(selbst))
            self.assertTrue(actions.needs_confirm(selbst))
            self.assertIsNone(actions.fremd_grund(nutzer))
            self.assertIsNone(actions.fremd_grund({"tool": "erinnern", "fuer": "selbst"}))      # nur ansehen


class _Ctx:
    backend = None
    model = "ollama_cloud:x"

    def sync_session(self):
        pass


class ImVollbild(_MitOrdner):
    def test_wecker_weckt_die_ki_und_erinnerungen_stehen_im_chat(self):
        import persoenlichkeiten as PS
        import screen_tx
        import ui
        bekommen = []
        frei = asyncio.Event()

        async def handler(ctx, text):
            bekommen.append(text)
            if text == "warte":
                await frei.wait()

        async def go():
            sc = screen_tx.Screen()
            sc.install()
            sc.ctx, sc.handler = _Ctx(), handler
            sc.session_state = {"model": "ollama_cloud:x", "tokens": 0}
            app = screen_tx._NemiApp(sc)
            sc.app = app
            try:
                async with app.run_test(size=(110, 30)) as pilot:
                    await pilot.pause(0.2)
                    jetzt = time.time()
                    for d in list(ER.offen()):
                        ER.loeschen(d["id"])
                    ER.anlegen("Tee ziehen lassen", jetzt - 5)
                    ER.anlegen("male ein Bild", jetzt - 5, wer="Mira", fuer=ER.FUER_KI)
                    ER.anlegen("nicht für diese Persönlichkeit", jetzt - 5, wer="Nova", fuer=ER.FUER_KI)
                    sc._erinnerungen_pruefen()
                    await pilot.pause(0.3)
                    text = "\n".join("".join(seg.text for seg in strip)
                                     for strip in app.screen._compositor.render_strips())
                    self.assertIn("⏰ Tee ziehen lassen", text)                  # an den Nutzer: Zeile im Chat
                    self.assertIn("Mira wird erinnert: male ein Bild", text)
                    self.assertEqual(len(bekommen), 1)                          # die KI hat ihren Wecker bekommen
                    self.assertTrue(bekommen[0].startswith("⏰ Erinnerung an dich selbst"))
                    self.assertIn("male ein Bild", bekommen[0])
                    self.assertEqual([d["wer"] for d in ER.offen()], ["Nova"])  # die fremde bleibt liegen
                    # Ist die KI beschäftigt, wartet der Wecker und kommt direkt danach
                    await pilot.press(*"warte")
                    await pilot.press("enter")
                    await pilot.pause(0.2)
                    self.assertTrue(sc.busy)
                    ER.anlegen("schau nach der Seite", time.time() - 1, wer="Mira", fuer=ER.FUER_KI)
                    sc._erinnerungen_pruefen()
                    await pilot.pause(0.2)
                    self.assertEqual(bekommen[-1], "warte")
                    self.assertEqual(len(sc._wecker), 1)
                    frei.set()
                    await pilot.pause(0.3)
                    sc._erinnerungen_pruefen()
                    await pilot.pause(0.3)
                    self.assertIn("schau nach der Seite", bekommen[-1])
                    self.assertEqual(sc._wecker, [])
                    app.exit()
            finally:
                sc.uninstall()

        from types import SimpleNamespace
        aktiv = mock.patch.object(PS, "active", lambda: SimpleNamespace(name="Mira"))
        name = mock.patch.object(ui, "persona_name", lambda: "Mira")
        with aktiv, name:
            asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
