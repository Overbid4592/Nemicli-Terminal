"""Tempo des eigenen Terminal-Fensters und der Oberfläche: nur geänderte Zeilen neu zeichnen, pyte in Stücken,
Maus nur bei Zellwechsel, Bildrate, Entprellen, Lade-Faden, Schreib-Faden, Sende-Schlange, Beobachter, Config."""

import ast
import asyncio
import json
import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

R = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(R / d)
    if p not in sys.path:
        sys.path.insert(0, p)
if str(R / "tests") not in sys.path:
    sys.path.insert(0, str(R / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyte                          # noqa: E402
import terminal_fenster as TF        # noqa: E402
import qt_testhilfe                  # noqa: E402

tearDownModule = qt_testhilfe.aufraeumen

NAMEN = ["pelvis", "l_hip", "r_hip", "spine1", "l_knee", "r_knee", "spine2", "l_ankle", "r_ankle", "spine3", "l_foot",
         "r_foot", "neck", "l_collar", "r_collar", "head", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist",
         "r_wrist"]
ELTERN = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19]


def _bewegung(ordner: Path, bilder: int = 12) -> Path:
    import math
    pfad = ordner / "b.json"
    frames = []
    for i in range(bilder):
        roh = {n: [0.0, 1.0, 0.0] for n in NAMEN}
        roh.update(head=[0.0, 1.65, 0.0], neck=[0.0, 1.5, 0.0], l_shoulder=[0.18, 1.45, 0.0],
                   r_shoulder=[-0.18, 1.45, 0.0], l_foot=[0.1, 0.0, 0.05], r_foot=[-0.1, 0.0, 0.05],
                   l_wrist=[0.3, 1.2 + 0.2 * math.sin(i), 0.0])
        frames.append([roh[n] for n in NAMEN])
    pfad.write_text(json.dumps({"fps": 20, "namen": NAMEN, "eltern": ELTERN, "schleife": True, "bilder": frames}),
                    encoding="utf-8")
    return pfad


def _text(schirm) -> str:
    return "\n".join("".join(schirm.buffer[y][x].data for x in range(schirm.columns)) for y in range(schirm.lines))


class Stuecke(unittest.TestCase):
    def test_feed_in_stuecken_wie_am_stueck(self):
        steuer_a, steuer_b = [], []
        titel = f"\x1b]0;{TF.STEUER_TITEL}{TF.STEUER_KEY}:puls=1:7\x07"
        text = ("\x1b[1;31mrot\x1b[0m und \x1b[38;2;10;20;30mfarbig\x1b[0m " * 40 + titel
                + "Herz ❤️ Familie 👩‍💻 é́ Ende\r\n" * 30 + "\x1b[5;3Hhier\x1b[2K\x1b[10;1Hweiter")
        a, b = TF.Schirm(60, 20, steuer=steuer_a.append), TF.Schirm(60, 20, steuer=steuer_b.append)
        pyte.Stream(a).feed(text)
        strom = pyte.Stream(b)
        teile = TF.feed_stuecke(text, 7)
        self.assertGreater(len(teile), 50)
        self.assertEqual("".join(teile), text)
        for t in teile:
            strom.feed(t)
        self.assertEqual(_text(a), _text(b))
        self.assertEqual((a.cursor.x, a.cursor.y), (b.cursor.x, b.cursor.y))
        self.assertEqual(steuer_a, ["puls=1"])
        self.assertEqual(steuer_a, steuer_b)

    def test_teilt_nur_zwischen_ascii(self):
        text = "ab" + "👩‍💻" * 30 + "cd" * 20
        for t in TF.feed_stuecke(text, 10)[:-1]:
            self.assertLess(t[-1], "\x80")
        self.assertEqual(TF.feed_stuecke("kurz"), ["kurz"])


class Zeichnen(unittest.TestCase):
    def test_streifen_aus_zeilen(self):
        self.assertEqual(TF.zeilen_streifen({3, 4, 10}, 20.0), [(67, 42), (207, 22)])
        self.assertEqual(TF.zeilen_streifen(set(), 20.0), [])

    def test_puls_in_stufen(self):
        self.assertEqual(TF.puls_stufe(0.0), 0)
        self.assertEqual(TF.puls_stufe(TF.PULS_S / 2), TF.PULS_STUFEN)
        self.assertEqual(TF.puls_stufe(None, 0.5), TF.PULS_STUFEN // 2)
        self.assertEqual(TF.puls_stufe(None, 0.0), 0)
        self.assertAlmostEqual(TF.leuchten_staerke(0), 0.55)
        self.assertAlmostEqual(TF.leuchten_staerke(TF.PULS_STUFEN), 1.0)
        stufen = {TF.puls_stufe(t / 100) for t in range(0, 240)}
        self.assertLessEqual(len(stufen), TF.PULS_STUFEN + 1)

    def test_platzhalter_zeile(self):
        s = TF.Schirm(20, 3)
        pyte.Stream(s).feed("text " + TF.platzhalter(1, 0) * 3)
        self.assertTrue(TF._hat_platzhalter([s.buffer[0][x] for x in range(20)]))
        self.assertFalse(TF._hat_platzhalter([s.buffer[1][x] for x in range(20)]))


class Einstellungen(unittest.TestCase):
    def test_bildrate(self):
        self.assertEqual(TF.textual_fps({}), 30)
        self.assertEqual(TF.textual_fps({"terminal_fps": 60}), 60)
        self.assertEqual(TF.textual_fps({"terminal_fps": "60"}), 60)
        self.assertEqual(TF.textual_fps({"terminal_fps": 45}), 30)
        self.assertEqual(TF.textual_fps({"terminal_fps": "viel"}), 30)

    def test_haupt_umgebung(self):
        env = TF.haupt_umgebung({"PATH": "x"}, 60)
        self.assertEqual(env["TEXTUAL_FPS"], "60")
        self.assertEqual(env["NEMICLI_FENSTER"], "1")
        self.assertEqual(env["NEMICLI_STEUER_KEY"], TF.STEUER_KEY)
        self.assertEqual(TF.haupt_umgebung({"TEXTUAL_FPS": "24"}, 60)["TEXTUAL_FPS"], "24")   # von außen: bleibt

    def test_menue_schalter(self):
        import config
        import einstellseite
        with mock.patch.object(config, "load", lambda: {"terminal_fps": 60}):
            seite = [e for e in einstellseite.seite() if e["befehl"] == "/terminal" and "Bildrate" in e["titel"]]
        self.assertEqual([o["wert"] for o in seite[0]["optionen"] if o["aktiv"]], ["tempo 60"])

    def test_terminal_befehl_tempo(self):
        import main_quelle
        import ui
        quelle = main_quelle.text()
        gespeichert, gesendet = {}, []
        raum = {"os": os, "ui": ui, "_fenster_steuern": gesendet.append,
                "config": mock.Mock(update=lambda **kw: gespeichert.update(kw), load=lambda: dict(gespeichert))}
        exec(quelle[quelle.index("# /terminal: Schalter -> "):quelle.index("def _schalter_an(")], raum)
        with mock.patch.object(ui, "success"), mock.patch.object(ui, "info") as info:
            raum["terminal_befehl"]("tempo 60")
            raum["terminal_befehl"]("tempo 45")                     # unbekannt: nur der Stand
        self.assertEqual(gespeichert, {"terminal_fps": 60})
        self.assertIn("🎞 60", info.call_args_list[0][0][0])


class Steuerbefehle(unittest.TestCase):
    def test_fehler_verwirft_die_anderen_nicht(self):
        getan = []

        def tun(b):
            if b == "kaputt":
                raise ValueError("x")
            getan.append(b)
        with mock.patch.object(TF, "_fehler_melden") as melden:
            self.assertEqual(TF.befehle_einzeln(["a", "kaputt", "b"], tun), ["kaputt"])
        self.assertEqual(getan, ["a", "b"])
        melden.assert_called_once()

    def test_laden_im_faden(self):
        self.assertTrue(TF.braucht_laden("bild0-3=abc"))
        self.assertTrue(TF.braucht_laden("bewegung=abc"))
        self.assertTrue(TF.braucht_laden("posen="))
        self.assertTrue(TF.braucht_laden("rig="))
        for b in ("pose=winken", "sitz=4", "puls=1", "justierung=x", "tray"):
            self.assertFalse(TF.braucht_laden(b))

    def test_maus_nur_bei_zellwechsel(self):
        zuletzt = ((3, 4), 3, (False, False, False))
        self.assertFalse(TF.maus_gewechselt(zuletzt, (3, 4), 3, (False, False, False)))
        self.assertTrue(TF.maus_gewechselt(zuletzt, (4, 4), 3, (False, False, False)))
        self.assertTrue(TF.maus_gewechselt(zuletzt, (3, 4), 0, (False, False, False)))
        self.assertTrue(TF.maus_gewechselt(zuletzt, (3, 4), 3, (True, False, False)))
        self.assertTrue(TF.maus_gewechselt(None, (3, 4), 3, (False, False, False)))

    def test_bewegung_einmal_gelesen(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        pfad = _bewegung(tmp)
        a = TF.strich_lesen(pfad, "froh", True)
        with mock.patch.object(TF, "_strich_pruefen", side_effect=AssertionError("neu gelesen")):
            b = TF.strich_lesen(pfad, "neutral", False)
        self.assertIs(a["bilder"], b["bilder"])
        self.assertEqual((a["gesicht"], a["vorrang"], b["gesicht"], b["vorrang"]), ("froh", True, "neutral", False))
        self.assertIsNot(a, b)
        alt = pfad.stat().st_mtime_ns
        _bewegung(tmp, 13)
        os.utime(pfad, ns=(alt + 10_000_000, alt + 10_000_000))
        self.assertEqual(len(TF.strich_lesen(pfad)["bilder"]), 13)        # geändert: neu gelesen

    def test_protokoll_ende(self):
        stuecke = ["\x1b[2mhinweis\x1b[0m\r\n"] + [f"zeile {i}\r\n" for i in range(50)] \
            + ["\x1b[31m" + "".join(f"fehler {i}\r\n" for i in range(30)) + "\x1b[0m"]
        ende = TF.protokoll_ende(stuecke, 10)
        self.assertLessEqual("".join(ende).count("\n"), 10)
        self.assertTrue(ende[0].startswith("\x1b[31m"))                    # angeschnitten: Farbe bleibt
        self.assertTrue(ende[-1].endswith("\x1b[0m"))
        self.assertEqual(TF.protokoll_ende(stuecke, 10_000), stuecke)


@unittest.skipUnless(os.name == "nt", "Pseudokonsole nur unter Windows")
class Schreiben(unittest.TestCase):
    def test_tastendruecke_in_reihenfolge(self):
        import terminal_pty
        aus, fertig = [], threading.Event()
        p = terminal_pty.Pty([sys.executable, "-c", "s = input(); print('ERHALTEN:' + s)"], 80, 10,
                             bei_ausgabe=aus.append, bei_ende=lambda _c: fertig.set())
        time.sleep(0.5)
        t0 = time.perf_counter()
        for c in "abcdefghijklmnopqrstuvwxyz0123456789":
            p.schreiben(c)
        p.schreiben("\r")
        self.assertLess(time.perf_counter() - t0, 0.5)                     # schreiben kehrt sofort zurück
        self.assertTrue(fertig.wait(15))
        self.assertIn("ERHALTEN:abcdefghijklmnopqrstuvwxyz0123456789", "".join(aus))
        p.schreiben("nach dem Ende")                                        # geschlossen: still verworfen


def _standard() -> dict:
    return {"thema": TF.THEMEN["breeze"], "schrift": "", "groesse": 11, "deckkraft": 1.0, "hintergrund": 1.0,
            "unschaerfe": False, "ki_reiter": True, "leuchten": True, "weich": False, "geteilt": False, "figur": True,
            "fps": 30}


@unittest.skipUnless(os.name == "nt", "Pseudokonsole nur unter Windows")
class FensterTempo(unittest.TestCase):
    def test_teilbild_gleich_ganzem_bild_entprellen_figur_laden(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["test"])
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        programm = textwrap.dedent("""
            import sys, time
            for i in range(12):
                sys.stdout.write(f"zeile {i} \\x1b[1;3{i % 7}mfarbig\\x1b[0m\\r\\n")
            sys.stdout.flush()
            time.sleep(8)
        """)
        bewegung = _bewegung(tmp)
        import figurrig
        import test_figurrig
        test_figurrig.t_haltung().save(tmp / "k.png")
        rig = figurrig.bauen(tmp / "k.png", tmp / "rig_vorne", "vorne")
        b = {}

        def teil1():
            fenster = next(w for w in app.topLevelWidgets() if hasattr(w, "reiter"))
            haupt = fenster.haupt
            b["fps"] = haupt.kind_env.get("TEXTUAL_FPS")
            app.processEvents()
            # Teilbild: nur die geänderten Zeilen neu zeichnen ergibt dasselbe wie ein ganzes Bild
            vorher = haupt.grab().toImage()
            with haupt.lock:
                haupt.strom.feed("\x1b[4;1H\x1b[2Kneu in Zeile vier\x1b[7;5Hx")
            gefangen = []
            haupt.update = lambda *a: gefangen.append(a)
            haupt.neu = True
            haupt._neu_zeichnen()
            del haupt.update
            b["teil"] = bool(gefangen and gefangen[0])
            if b["teil"]:
                bereich = gefangen[0][0]
                b["kleiner"] = bereich.boundingRect().height() < haupt.height()
                haupt.render(vorher, bereich.boundingRect().topLeft(), bereich)   # an derselben Stelle
            nachher = haupt.grab().toImage()
            b["gleich"] = vorher == nachher
            # ein Steuerbefehl, der scheitert, verwirft die anderen nicht
            with haupt.lock:
                haupt.steuerbefehle += ["puls=1", "sitz=4"]
            with mock.patch.object(haupt, "_steuer_wert", side_effect=RuntimeError("x")), \
                    mock.patch.object(TF, "_fehler_melden") as melden:
                haupt._takt()
            b["gemeldet"] = melden.call_count
            b["sitz"] = haupt.figur_sitz
            # Strichmännchen: Bewegung im Lade-Faden, ein Bild der Figur wird je Zeitpunkt einmal gerechnet
            haupt._figur_steuern("posen", ("strich", {}))
            haupt._laden("rig=" + __import__("base64").urlsafe_b64encode(
                json.dumps({"vorne": str(rig)}).encode()).decode())
            haupt._laden("bewegung=" + __import__("base64").urlsafe_b64encode(
                json.dumps({"datei": str(bewegung)}).encode()).decode())
            QTimer.singleShot(600, lambda: teil2(fenster, haupt))

        def teil2(fenster, haupt):
            b["bewegung"] = haupt.strich_basis is not None
            rechnen = []
            haupt.figur_zeit = time.monotonic()                 # ein neues Figur-Bild
            echt = haupt._strich_rechnen
            haupt._strich_rechnen = lambda *a: (rechnen.append(1), echt(*a))[1]
            haupt.repaint()
            haupt.repaint()
            b["einmal"] = len(rechnen)
            b["figur"] = haupt.figur_rect is not None
            erstes = haupt.grab().toImage()
            b["gleiches_figurbild"] = erstes == haupt.grab().toImage()
            del haupt._strich_rechnen
            b["huelle"] = bool(haupt.strich_rig) and haupt.figur_merk[1]["rig"] is not None
            # Teilbild unter der Figur: sie bleibt dieselbe, keine Reste
            with haupt.lock:
                zeile = haupt.schirm.lines - haupt.figur_sitz - 1
                haupt.strom.feed(f"\x1b[{zeile};1H\x1b[2K" + "unter der Figur " * 8)
            gefangen = []
            haupt.update = lambda *a: gefangen.append(a)
            haupt.neu = True
            haupt._neu_zeichnen()
            del haupt.update
            bereich = gefangen[0][0]
            b["unter_figur"] = bereich.intersects(haupt.figur_rect.toAlignedRect())
            haupt.render(erstes, bereich.boundingRect().topLeft(), bereich)
            b["figur_teilbild_gleich"] = erstes == haupt.grab().toImage()
            # Reiter „KI“: neue Größe erst nach dem Entprellen, der alte Bildschirm bleibt so lange
            ki = fenster._ki_ansicht(zeigen=True)
            app.processEvents()
            ki.einspeisen("".join(f"eintrag {i:03d}\r\n" for i in range(300)))
            ki.raster_gesetzt = True
            alt = ki.schirm
            fenster.resize(fenster.width() - 160, fenster.height() - 60)
            app.processEvents()
            b["alt_bleibt"] = ki.schirm is alt and ki.raster_warten.isActive()
            QTimer.singleShot(1200, lambda: teil3(fenster, ki, alt))

        def teil3(fenster, ki, alt):
            b["neu_gebaut"] = ki.schirm is not alt and ki.ki_bau is None
            b["ki_text"] = _text(ki.schirm)
            b["takt_ruht"] = not fenster.haupt.takt.isActive() or fenster.haupt.takt.interval() > 16
            fenster.close()

        QTimer.singleShot(1500, teil1)
        with mock.patch.object(TF, "_einstellungen", _standard), mock.patch.object(TF, "_konfiguration", dict), \
                mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "TEXTUAL_FPS"}, clear=True):
            TF.starten([sys.executable, "-c", programm], titel="NemiCLI")
        self.assertEqual(b["fps"], "30")
        self.assertTrue(b["teil"])
        self.assertTrue(b["kleiner"])
        self.assertTrue(b["gleich"])
        self.assertEqual(b["sitz"], 4)
        self.assertEqual(b["gemeldet"], 1)
        self.assertTrue(b["bewegung"])
        self.assertTrue(b["figur"])
        self.assertEqual(b["einmal"], 1)
        self.assertTrue(b["gleiches_figurbild"])
        self.assertTrue(b["huelle"])
        self.assertTrue(b["unter_figur"])
        self.assertTrue(b["figur_teilbild_gleich"])
        self.assertTrue(b["alt_bleibt"])
        self.assertTrue(b["neu_gebaut"])
        self.assertIn("eintrag 299", b["ki_text"])
        self.assertTrue(b["takt_ruht"])


class Beobachter(unittest.TestCase):
    def setUp(self):
        import beobachter
        self.B = beobachter
        beobachter.zuruecksetzen()
        self.addCleanup(beobachter.zuruecksetzen)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_nach_sechs_nur_gezaehlt(self):
        dateien = []
        for i in range(10):
            f = self.tmp / f"d{i}.txt"
            f.write_text("a\nb\n", encoding="utf-8")
            self.B.gelesen(f)
            dateien.append(f)
        for f in dateien:
            f.write_text("a\nc\nd\n", encoding="utf-8")
        with mock.patch.object(self.B, "unterschied", wraps=self.B.unterschied) as diff:
            text = self.B.runde_hinweis({})
        self.assertEqual(diff.call_count, self.B.MAX_MELDUNGEN)
        self.assertIn("… und 4 weitere", text)
        self.assertEqual(self.B.runde_hinweis({}), "")                    # Stand übernommen, auch für die gezählten

    def test_zeilen_deckel(self):
        alt = "\n".join(f"z{i}" for i in range(self.B.MAX_DIFF_EINGABE + 10))
        self.assertEqual(self.B.unterschied(alt, alt + "\nneu"), "")
        text = self.B._aenderung({"text": alt, "groesse": 1}, {"text": alt + "\nneu", "groesse": 2}, "x")
        self.assertIn("Zeilen", text)


class Malen(unittest.TestCase):
    def test_takt_waechst_mit_der_dauer(self):
        quelle = (R / "main.py").read_text(encoding="utf-8")
        anfang = quelle.index("async def _terminal_antworten(")
        rumpf = quelle[anfang:quelle.index("\ndef ", anfang)]
        self.assertIn("if now - last_paint >= paint_takt:", rumpf)
        self.assertIn("paint_takt = min(0.4, max(0.08, (time.perf_counter() - t_mal) * 4))", rumpf)


class Statusleiste(unittest.TestCase):
    def _schluessel(self):
        baum = ast.parse((R / "main_befehle.py").read_text(encoding="utf-8"))
        knoten = [n for n in baum.body if isinstance(n, ast.FunctionDef) and n.name == "_ctx_schluessel"]
        import models
        import pricing
        raum = {"M": models, "pricing": pricing, "config": mock.Mock(load=lambda: dict(self.cfg))}
        exec(compile(ast.Module(body=knoten, type_ignores=[]), "main_befehle.py", "exec"), raum)
        return raum["_ctx_schluessel"]

    def test_schluessel_folgt_modell_und_einstellung(self):
        import gguflokal
        self.cfg = {"gguf_kontext": 8192}
        schluessel = self._schluessel()
        a = schluessel("gguf:modell-a")
        self.assertEqual(a, schluessel("gguf:modell-a"))
        self.assertNotEqual(a, schluessel("gguf:modell-b"))
        self.cfg = {"gguf_kontext": 16384}
        self.assertNotEqual(a, schluessel("gguf:modell-a"))
        self.cfg = {"gguf_kontext": 8192}
        with mock.patch.object(gguflokal, "_motor", ("datei", "x", object())):       # Motor geladen
            self.assertNotEqual(a, schluessel("gguf:modell-a"))
        self.assertEqual(schluessel("ollama:x")[0], "ollama")


@unittest.skipUnless(os.name == "nt", "Named Pipes nur unter Windows")
class Bruecke(unittest.TestCase):
    def test_haengendes_fenster_blockiert_senden_nicht(self):
        import gui_bruecke
        from multiprocessing.connection import Client

        async def ablauf():
            br = gui_bruecke.GuiBruecke(asyncio.get_running_loop(), {})
            try:
                conn = await asyncio.to_thread(Client, br.adresse, "AF_PIPE", br.schluessel)
                for _ in range(100):
                    if br.verbunden():
                        break
                    await asyncio.sleep(0.01)
                t0 = time.perf_counter()
                with mock.patch.object(gui_bruecke, "STAU_NACHRICHTEN", 50):
                    for i in range(400):                       # niemand liest: die Pipe läuft voll
                        br.senden({"t": "answer", "delta": "x" * 4000, "i": i})
                self.assertLess(time.perf_counter() - t0, 2.0)
                for _ in range(200):
                    if not br.verbunden():
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(br.verbunden())                # Rückstau: getrennt
                conn.close()
            finally:
                br.stop()

        asyncio.run(ablauf())

    def test_reihenfolge_bleibt(self):
        import gui_bruecke
        from multiprocessing.connection import Client

        async def ablauf():
            br = gui_bruecke.GuiBruecke(asyncio.get_running_loop(), {})
            try:
                conn = await asyncio.to_thread(Client, br.adresse, "AF_PIPE", br.schluessel)
                for _ in range(100):
                    if br.verbunden():
                        break
                    await asyncio.sleep(0.01)
                for i in range(200):
                    br.senden({"i": i})
                erhalten = [json.loads(await asyncio.to_thread(conn.recv_bytes))["i"] for _ in range(200)]
                self.assertEqual(erhalten, list(range(200)))
                conn.close()
            finally:
                br.stop()

        asyncio.run(ablauf())


class Config(unittest.TestCase):
    def setUp(self):
        import config
        self.config = config
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.datei = Path(self.tmp.name) / "nemicli.config.json"
        p = mock.patch.object(config, "_FILE", self.datei)
        p.start()
        self.addCleanup(p.stop)
        self.gewarnt = []
        p = mock.patch("ui.warn", lambda t: self.gewarnt.append(t))
        p.start()
        self.addCleanup(p.stop)
        config._gemeldet.clear()

    def test_speichern_atomar_mit_sicherung(self):
        import atomar
        with mock.patch.object(atomar, "text_schreiben", wraps=atomar.text_schreiben) as schreiben:
            self.assertTrue(self.config.save({"a": 1}))
            self.assertTrue(self.config.save({"a": 2}))
        self.assertEqual(json.loads(self.datei.read_text(encoding="utf-8")), {"a": 2})
        self.assertEqual(json.loads(self.datei.with_name(self.datei.name + ".bak").read_text(encoding="utf-8")),
                         {"a": 1})
        self.assertGreaterEqual(schreiben.call_count, 3)

    def test_rueckfall_direkt_wenn_kein_tausch(self):
        import atomar
        with mock.patch.object(atomar, "text_schreiben", side_effect=PermissionError("gesperrt")), \
                mock.patch.object(self.config.time, "sleep"):
            self.assertTrue(self.config.save({"b": 1}))
        self.assertEqual(json.loads(self.datei.read_text(encoding="utf-8")), {"b": 1})

    def test_belegte_sperre_wird_gemeldet(self):
        sperre = self.datei.with_name(self.datei.name + ".lock")
        sperre.write_text("", encoding="utf-8")
        with mock.patch.object(self.config._Dateisperre, "WARTEN_S", (0.05, 0.05)):
            self.assertTrue(self.config.update(c=3))
        self.assertEqual(len(self.gewarnt), 2)                               # erster Versuch, dann ohne Sperre
        self.assertEqual(self.config.load(), {"c": 3})
        self.assertTrue(sperre.exists())                                     # fremde Sperre bleibt liegen


class Fensterstart(unittest.TestCase):
    def test_ziel_wie_main_start(self):
        import fensterstart as FS
        self.assertEqual(FS.ziel(["main.py", "--fenster"], False, False), "fenster")
        self.assertEqual(FS.ziel(["main.py", "--gui"], False, False), "gui")
        self.assertEqual(FS.ziel(["main.py", "--charaktere"], False, False), "charaktere")
        self.assertEqual(FS.ziel(["NemiCLI.exe"], True, True), "fenster")      # Fenster-App ohne Auftrag
        self.assertIsNone(FS.ziel(["NemiCLI.exe", "--wache"], True, True))
        self.assertIsNone(FS.ziel(["NemiCLI.exe", "--musik"], True, True))
        self.assertIsNone(FS.ziel(["main.py", "--version", "--fenster"], False, False))
        self.assertIsNone(FS.ziel(["main.py"], False, False))
        self.assertIsNone(FS.ziel(["main.py"], True, False))                    # exe mit Konsole: normal

    def test_kind_befehl(self):
        import fensterstart as FS
        befehl = FS.kind_befehl(["main.py", "--fenster", "--debug"], False, R / "main.py")
        self.assertEqual(befehl, [sys.executable, str(R / "main.py"), "--debug"])

    def test_main_verzweigt_vor_den_schweren_imports(self):
        quelle = (R / "main.py").read_text(encoding="utf-8")
        self.assertLess(quelle.index("fensterstart.frueh("), quelle.index("from prompt_toolkit import"))
        self.assertLess(quelle.index("venvpflege.ausstehende_einspielen()"), quelle.index("fensterstart.frueh("))
        self.assertIn("netzzwang.installieren()", (R / "ui" / "fensterstart.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
