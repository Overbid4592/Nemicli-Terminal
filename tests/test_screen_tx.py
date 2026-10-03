"""Offline-Test: Textual-Vollbild (ui/screen_tx.py) – headless über Textuals Pilot.

python -m unittest discover -s tests -p test_screen_tx.py
"""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from rich.panel import Panel                      # noqa: E402
from rich.style import Style                      # noqa: E402
from rich.text import Text                        # noqa: E402
import ui                                         # noqa: E402
import screen_tx                                  # noqa: E402


def screen_text(app) -> str:
    """Sichtbarer Bildschirm als reiner Text (Zeile für Zeile)."""
    strips = app.screen._compositor.render_strips()
    return "\n".join("".join(seg.text for seg in strip) for strip in strips)


class _Ctx:
    backend = None
    model = "ollama_cloud:x"

    def sync_session(self):
        pass


class HelperTests(unittest.TestCase):
    def test_pt_style(self):
        st = screen_tx.pt_style("fg:#41e0d0 bg:#11131d bold")
        self.assertEqual(st.color.name, "#41e0d0")
        self.assertEqual(st.bgcolor.name, "#11131d")
        self.assertTrue(st.bold)
        self.assertEqual(screen_tx.pt_style("class:prompt"), Style())

    def test_fragments_to_text_emoji_sicher(self):
        t = screen_tx.fragments_to_text([("fg:#fff", "a ❤️ "), ("bold", "b")])
        self.assertEqual(t.plain, "a ❤ b")

    def test_sanitize_deckt_die_typen_ab(self):
        self.assertEqual(screen_tx.sanitize("x ❤️"), "x ❤")
        t = screen_tx.sanitize(Text("y ⚠️"))
        self.assertEqual(t.plain, "y ⚠")
        p = screen_tx.sanitize(Panel(Text("z ❤️"), title="t ❤️"))
        self.assertEqual(p.renderable.plain, "z ❤")
        self.assertEqual(p.title, "t ❤")
        from rich.markdown import Markdown
        md = screen_tx.sanitize(Markdown("**hi** ❤️"))
        self.assertEqual(md.markup, "**hi** ❤")


class AppTests(unittest.TestCase):
    """Ein Lauf durch die ganze Oberfläche – Menüs, Eingabe, Resize, Live."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_oberflaeche(self):
        async def go():
            sc = screen_tx.Screen()
            sc.install()
            try:
                self.assertIs(ui.TUI, sc)
                ui.banner()                                   # gepuffert bis zum Start
                ui.console.print(ui.assistant_panel("Hallo **test_user** ❤️"))
                ui.info("Info-Zeile")
                seen = []

                async def handler(ctx, text):
                    seen.append(text)
                    if text == "/frag":
                        seen.append(await sc.select("Wie weiter?", [("a", "A"), ("b", "B")]))
                    elif text == "/txt":
                        seen.append(await sc.text_input("Key?", password=True))
                    elif text == "/rev":
                        seen.append(await sc.review_action("Prüfen", "code\n" * 40))
                    elif text == "/nein":
                        seen.append(await sc.review_action("Prüfen", "code"))
                    elif text == "/denk":
                        sc.set_thinking("ich denke …", 1.0, live=False)
                    return None

                sc.ctx = _Ctx(); sc.handler = handler
                sc.session_state = {"model": "ollama_cloud:x", "tokens": 0}
                app = screen_tx._NemiApp(sc); sc.app = app
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause(0.2)
                    # Blöcke aus der Zeit vor dem Start sind da (Banner, Panel, Info, Leerzeile)
                    self.assertEqual(len(list(app.query(".block"))), 4)
                    txt = screen_text(app)
                    self.assertIn("dein eigener Coding-Agent", txt)
                    self.assertIn("Hallo test_user ❤", txt)          # ❤️ -> ❤
                    # Kein fester Name: die Kopfzeile zeigt, wer gerade eingestellt ist
                    self.assertIn(f"╭─ {ui.persona_name()} · ● bereit", txt)
                    self.assertEqual(ui.console.width, 100)

                    # Eingabe + Enter -> Handler
                    await pilot.press(*"hallo"); await pilot.press("enter"); await pilot.pause(0.2)
                    self.assertEqual(seen, ["hallo"])
                    self.assertIn("❯ hallo", screen_text(app))
                    self.assertFalse(sc.busy)

                    # Auswahl-Menü: ↓ + Enter -> "b"
                    await pilot.press(*"/frag"); await pilot.press("enter"); await pilot.pause(0.2)
                    self.assertEqual(sc._modal, "select")
                    self.assertIn("Wie weiter?", screen_text(app))
                    await pilot.press("down"); await pilot.press("enter"); await pilot.pause(0.2)
                    self.assertEqual(seen[-1], "b")
                    self.assertIsNone(sc._modal)

                    # Auswahl per Maus-Klick auf den Knopf
                    await pilot.press(*"/frag"); await pilot.press("enter"); await pilot.pause(0.2)
                    await pilot.click("#opt0"); await pilot.pause(0.2)
                    self.assertEqual(seen[-1], "a")
                    self.assertIsNone(sc._modal)

                    # Auswahl per Ziffer, Esc = letzte Option
                    await pilot.press(*"/frag"); await pilot.press("enter"); await pilot.pause(0.2)
                    await pilot.press("1"); await pilot.pause(0.2)
                    self.assertEqual(seen[-1], "a")
                    await pilot.press(*"/frag"); await pilot.press("enter"); await pilot.pause(0.2)
                    await pilot.press("escape"); await pilot.pause(0.2)
                    self.assertEqual(seen[-1], "b")

                    # Texteingabe
                    await pilot.press(*"/txt"); await pilot.press("enter"); await pilot.pause(0.2)
                    self.assertEqual(sc._modal, "text")
                    await pilot.press(*"geheim"); await pilot.press("enter"); await pilot.pause(0.2)
                    self.assertEqual(seen[-1], "geheim")

                    # Aktionsprüfung: Enter darf NICHT freigeben, F8 schon, Esc lehnt ab
                    await pilot.press(*"/rev"); await pilot.press("enter"); await pilot.pause(0.2)
                    self.assertEqual(sc._modal, "review")
                    await pilot.press("enter"); await pilot.pause(0.1)
                    self.assertEqual(sc._modal, "review")
                    await pilot.press("f8"); await pilot.pause(0.2)
                    self.assertIs(seen[-1], True)
                    await pilot.press(*"/nein"); await pilot.press("enter"); await pilot.pause(0.2)
                    await pilot.press("escape"); await pilot.pause(0.2)
                    self.assertIs(seen[-1], False)

                    # Denktext per F2 öffnen und schließen
                    await pilot.press(*"/denk"); await pilot.press("enter"); await pilot.pause(0.2)
                    await pilot.press("f2"); await pilot.pause(0.2)
                    self.assertEqual(sc._modal, "thinking")
                    self.assertIn("ich denke", screen_text(app))
                    await pilot.press("f2"); await pilot.pause(0.2)
                    self.assertIsNone(sc._modal)

                    # Vervollständigung: Tab setzt ein und blättert
                    await pilot.press(*"/mo"); await pilot.pause(0.1)
                    self.assertTrue(sc._complete.display)
                    await pilot.press("tab"); await pilot.pause(0.1)
                    self.assertEqual(sc._input.text, "/model")
                    await pilot.press("tab"); await pilot.pause(0.1)
                    self.assertEqual(sc._input.text, "/modus")
                    sc._input.load_text("")

                    # Strg+J = neue Zeile, Enter sendet beides
                    await pilot.press(*"a"); await pilot.press("ctrl+j"); await pilot.press(*"b")
                    await pilot.pause(0.1)
                    self.assertEqual(sc._input.text, "a\nb")
                    await pilot.press("enter"); await pilot.pause(0.2)
                    self.assertEqual(seen[-1], "a\nb")

                    # Live-Anzeige
                    sc.live_update(Panel("streamt …")); await pilot.pause(0.1)
                    self.assertTrue(sc._live.display)
                    self.assertIn("streamt", screen_text(app))
                    sc.live_clear(); await pilot.pause(0.1)
                    self.assertFalse(sc._live.display)

                    # Helfer-Anzeige
                    sc.set_helfer(3); await pilot.pause(0.3)
                    self.assertIn("● Subagenten aktiv: 3", screen_text(app))
                    sc.set_helfer(0); await pilot.pause(0.3)
                    self.assertNotIn("Subagenten aktiv", screen_text(app))

                    # Resize: Breite wird nachgezogen, Banner passt sich an, unten bleibt unten
                    await pilot.resize_terminal(56, 32); await pilot.pause(0.4)
                    self.assertLessEqual(ui.console.width, 56)
                    txt = screen_text(app)
                    self.assertIn(f"╭─ {ui.persona_name()}", txt)
                    self.assertIn("❯ a", txt)                    # letzte Eingabe sichtbar
                    self.assertTrue(all(len(l) <= 56 for l in txt.split("\n")))

                    # Text im Verlauf markieren (Maus ziehen) und mit Strg+C kopieren
                    MARK = "Markier mich bitte.\n\nZweite Zeile."
                    ui.console.print(ui.assistant_panel(MARK))
                    await pilot.pause(0.2)
                    kopiert = []
                    app.copy_to_clipboard = lambda t: kopiert.append(t)
                    screen_tx._windows_clipboard = lambda t: None
                    from textual.geometry import Offset
                    blk = list(app.query(".block"))[-1]
                    await pilot.mouse_down(blk, offset=Offset(3, 2))
                    await pilot.hover(blk, offset=Offset(15, 4))
                    await pilot.mouse_up(blk, offset=Offset(15, 4))
                    await pilot.pause(0.2)
                    self.assertEqual(app.screen.get_selected_text(), MARK)
                    # Loslassen kopiert bereits; Strg+C bleibt der Tastenweg
                    self.assertEqual(kopiert, [MARK])
                    await pilot.press("ctrl+c"); await pilot.pause(0.2)
                    self.assertEqual(kopiert, [MARK, MARK])
                    self.assertTrue(app.is_running)            # Strg+C hat NICHT beendet
                    self.assertIsNone(app.screen.get_selected_text())

                    # Escape bei busy -> Abbruch-Flag
                    sc.busy = True
                    await pilot.press("escape"); await pilot.pause(0.1)
                    self.assertTrue(sc.consume_abort())
                    self.assertFalse(sc.consume_abort())
                    sc.busy = False

                    # /clear
                    sc.clear(); await pilot.pause(0.1)
                    self.assertEqual(len(list(app.query(".block"))), 0)

                    # Theme-Wechsel läuft über die abfangende Console bis in Textual
                    self.assertTrue(ui.set_theme("matrix")); await pilot.pause(0.1)
                    ui.console.print(ui.assistant_panel("nach Theme")); await pilot.pause(0.1)
                    self.assertIn("nach Theme", screen_text(app))
                    ui.set_theme("cyan")
                    app.exit()
            finally:
                sc.uninstall()
                self.assertIsNone(ui.TUI)

        self._run(go())


class InboxTests(unittest.TestCase):
    """Das offene Fenster holt sich Nachrichten aus .nemicli.inbox und lebt sichtbar."""

    def test_inbox_wird_abgeschickt(self):
        import tempfile, time
        import kontextmenue as K

        async def go():
            with tempfile.TemporaryDirectory() as d:
                K.ALIVE_DATEI, K.INBOX_DATEI = Path(d) / "alive", Path(d) / "inbox"
                sc = screen_tx.Screen(); sc.install()
                try:
                    seen = []

                    async def handler(ctx, text):
                        seen.append(text)

                    sc.ctx = _Ctx(); sc.handler = handler
                    sc.session_state = {"model": "x", "tokens": 0}
                    app = screen_tx._NemiApp(sc); sc.app = app
                    async with app.run_test(size=(80, 24)) as pilot:
                        await pilot.pause(0.5)
                        self.assertTrue(K.ALIVE_DATEI.exists())          # Lebenszeichen
                        self.assertTrue(K.nemicli_laeuft())
                        K.INBOX_DATEI.write_text("Schau dir das an: C:/x.png", encoding="utf-8")
                        await pilot.pause(1.5)
                        self.assertEqual(seen, ["Schau dir das an: C:/x.png"])
                        self.assertFalse(K.INBOX_DATEI.exists())
                        app.exit()
                    self.assertFalse(K.ALIVE_DATEI.exists())              # beim Ende weg
                finally:
                    sc.uninstall()
        asyncio.run(go())


class StartTextTests(unittest.TestCase):
    """--sag: die erste Nachricht geht ab, sobald die Oberfläche steht."""

    def test_start_text_wird_abgeschickt(self):
        async def go():
            sc = screen_tx.Screen(); sc.install()
            try:
                seen = []

                async def handler(ctx, text):
                    seen.append(text)

                sc.ctx = _Ctx(); sc.handler = handler
                sc.session_state = {"model": "x", "tokens": 0}
                sc.start_text = "Schau dir bitte dieses Bild an: C:/x.png"
                app = screen_tx._NemiApp(sc); sc.app = app
                async with app.run_test(size=(80, 24)) as pilot:
                    await pilot.pause(0.8)
                    self.assertEqual(seen, ["Schau dir bitte dieses Bild an: C:/x.png"])
                    self.assertIsNone(sc.start_text)
                    self.assertIn("❯ Schau dir bitte", screen_text(app))
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()


class MarkierenTests(unittest.TestCase):
    """Markieren mit der linken Maustaste allein: ziehen, Rand scrollt mit,
    Loslassen kopiert – ohne Umschalt und ohne Strg+C."""

    def _app(self, zeilen=30):
        sc = screen_tx.Screen(); sc.install()
        sc.ctx = _Ctx(); sc.handler = lambda c, t: None
        sc.session_state = {"model": "ollama_cloud:x", "tokens": 0}
        for i in range(zeilen):
            ui.info(f"Zeile {i:02d} mit etwas Text zum Markieren")
        app = screen_tx._NemiApp(sc); sc.app = app
        return sc, app

    def setUp(self):
        self.kopiert = []
        echt = screen_tx._windows_clipboard
        screen_tx._windows_clipboard = self.kopiert.append
        self.addCleanup(lambda: setattr(screen_tx, "_windows_clipboard", echt))

    def test_ziehen_mit_linker_taste_kopiert_beim_loslassen(self):
        async def go():
            sc, app = self._app()
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    block = list(app.query(".block"))[-1]
                    # KEIN shift=True – nur die linke Taste gedrückt halten und ziehen
                    await pilot.mouse_down(block, offset=(2, 0)); await pilot.pause(0.1)
                    await pilot.hover(block, offset=(20, 0)); await pilot.pause(0.1)
                    self.assertTrue(app.screen.selections, "Ziehen markiert nichts")
                    self.assertEqual([], self.kopiert)          # vor dem Loslassen noch nichts
                    await pilot.mouse_up(block, offset=(20, 0)); await pilot.pause(0.2)
                    self.assertEqual(1, len(self.kopiert))
                    self.assertIn("Zeile 29", self.kopiert[0])
                    # Die Markierung bleibt stehen, damit man sieht, was man erwischt hat
                    self.assertTrue(app.screen.selections)
                    self.assertIn("Zeichen kopiert", screen_text(app))
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())

    def test_klick_ohne_ziehen_kopiert_nichts(self):
        async def go():
            sc, app = self._app()
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    block = list(app.query(".block"))[-1]
                    await pilot.click(block, offset=(5, 0)); await pilot.pause(0.2)
                    self.assertEqual([], self.kopiert)      # sonst kippt jeder Klick die Ablage
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())

    def test_strg_c_kopiert_weiterhin_und_raeumt_die_markierung_weg(self):
        async def go():
            sc, app = self._app()
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    block = list(app.query(".block"))[-1]
                    await pilot.mouse_down(block, offset=(2, 0)); await pilot.pause(0.1)
                    await pilot.hover(block, offset=(18, 0)); await pilot.pause(0.1)
                    await pilot.mouse_up(block, offset=(18, 0)); await pilot.pause(0.2)
                    self.kopiert.clear()
                    await pilot.press("ctrl+c"); await pilot.pause(0.2)
                    self.assertEqual(1, len(self.kopiert))
                    self.assertFalse(app.screen.selections)   # Tastenweg räumt auf
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())

    def test_neue_ausgabe_reisst_die_markierung_nicht_weg(self):
        async def go():
            sc, app = self._app()
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    log = app.query_one("#log")
                    block = list(app.query(".block"))[-1]
                    await pilot.mouse_down(block, offset=(2, 0)); await pilot.pause(0.1)
                    await pilot.hover(block, offset=(20, 0)); await pilot.pause(0.1)
                    await pilot.mouse_up(block, offset=(20, 0)); await pilot.pause(0.2)
                    stand = log.scroll_y
                    ui.info("frische Ausgabe, die sonst nach unten reißt")
                    await pilot.pause(0.3)
                    self.assertEqual(stand, log.scroll_y,
                                     "Der Verlauf ist weggescrollt, obwohl etwas markiert war")
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())

    def test_am_oberen_rand_scrollt_der_verlauf_beim_ziehen_mit(self):
        async def go():
            sc, app = self._app(zeilen=60)
            try:
                async with app.run_test(size=(100, 24)) as pilot:
                    await pilot.pause(0.3)
                    log = app.query_one("#log")
                    self.assertGreater(log.scroll_y, 5, "zu wenig Verlauf zum Hochscrollen")
                    start = log.scroll_y
                    block = list(app.query(".block"))[-1]
                    await pilot.mouse_down(block, offset=(2, 0)); await pilot.pause(0.1)
                    # In die Randzone oben ziehen, Taste gedrückt. Pilot.hover schickt
                    # delta_y=0, und ohne echte Bewegung steigt Textual gleich wieder aus –
                    # deshalb hier ein Mausereignis mit richtiger Bewegung nach oben.
                    from textual import events as _ev
                    from textual.pilot import _get_mouse_message_arguments as _args
                    kw = _args(log, offset=(10, 1), button=1)
                    kw["delta_y"] = -3
                    app.screen._forward_event(_ev.MouseMove(**kw))
                    await pilot.pause(0.8)
                    self.assertLess(log.scroll_y, start,
                                    "Am oberen Rand rollt der Verlauf beim Ziehen nicht mit")
                    await pilot.mouse_up(log, offset=(10, 1)); await pilot.pause(0.2)
                    app.exit()
            finally:
                sc.uninstall()
        asyncio.run(go())


class VorschauFarbenTest(unittest.TestCase):
    TEXT = ("Dateiänderung prüfen\n\nZiel: C:/x/a.py\nVorhandene Datei wird ÜBERSCHRIEBEN.\n\n"
            "Änderungen (grün + neu, rot − entfernt): 1 Zeile(n) neu, 1 entfernt – vollständige Datei:\n"
            "-     1 │ alt\n+     1 │ neu\n      2 │ gleich")

    def _stile(self, text, wort):
        start = text.plain.index(wort)
        return " ".join(str(s.style) for s in text.spans if s.start <= start < s.end)

    def test_plus_gruen_minus_rot_und_volle_breite(self):
        t = screen_tx._vorschau_text(self.TEXT, True, breite=40)
        self.assertIn(screen_tx._GRAU["plus_grund"], self._stile(t, "│ neu"))
        self.assertIn(screen_tx._GRAU["minus_grund"], self._stile(t, "│ alt"))
        self.assertIn(screen_tx._GRAU["warnung"], self._stile(t, "ÜBERSCHRIEBEN"))
        zeilen = t.plain.split("\n")
        self.assertEqual(len(next(z for z in zeilen if z.startswith("+ "))), 40)
        self.assertNotIn(screen_tx._GRAU["plus_grund"], self._stile(t, "gleich"))

    def test_denktext_bleibt_schlicht(self):
        self.assertEqual(screen_tx._vorschau_text("+ 1 │ x", False).plain, "+ 1 │ x")
