"""Fehler-Wächter: Bremsen und Hänger der Hauptschleife mit Stelle, Halter festgehaltener Objekte,
wiederholte Fehler und NaN, Abstürze und unsauberes Ende früherer Starts, Befunde in Bericht und Datei."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "core", "engines", "tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import debugbericht as D          # noqa: E402
import fehlerwaechter as FW       # noqa: E402


class Grundlage(unittest.TestCase):
    def setUp(self):
        D.beenden()
        self.ordner = Path(tempfile.mkdtemp()) / "Debugbericht"
        p = mock.patch.object(D, "an", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._aufraeumen)
        FW._puls = FW._haupt_ident = None
        FW._fehler_zeiten.clear()
        FW._bremsen_zeiten.clear()

    def _aufraeumen(self):
        D.beenden()
        if FW._faden is not None:
            FW._faden.join(2)

    def starten(self):
        return D.starten("haupt", self.ordner, takt=False)

    def text(self, pfad):
        return pfad.read_text(encoding="utf-8")


class Befunde(Grundlage):
    def test_befund_im_bericht_und_in_der_datei(self):
        pfad = self.starten()
        FW.befund("probe", "etwas fiel auf", "Zeile eins\nZeile zwei")
        self.assertIn("⚠ BEFUND probe: etwas fiel auf", self.text(pfad))
        self.assertIn("    Zeile zwei", self.text(pfad))
        b = FW.befunde()[-1]
        self.assertEqual((b["art"], b["text"], b["pid"]), ("probe", "etwas fiel auf", os.getpid()))
        self.assertIn("(dieser Start)", FW.kurz_text(b))
        sicht = D._sicht(self.text(pfad), "fehler", 0, 10_000)        # die Fehler-Sicht zeigt den Befund ganz
        self.assertIn("BEFUND probe", sicht)
        self.assertIn("Zeile zwei", sicht)

    def test_ohne_bericht_nichts(self):
        FW.befund("probe", "still")
        self.assertFalse(self.ordner.exists())

    def test_wiederholte_fehler_und_nan(self):
        self.starten()
        for _ in range(FW.WIEDERHOLT):
            try:
                raise KeyError("x")
            except KeyError:
                D.fehler("Werkzeug datei_lesen")
        arten = [b["art"] for b in FW.befunde()]
        self.assertEqual(arten.count("wiederholt"), 1)
        FW.fehler_gesehen("Sampler", "RuntimeError", "model produced invalid values (NaN/Inf)")
        FW.fehler_gesehen("Konto", "ValueError", "Finanzen nicht gefunden")      # „nan“ mitten im Wort
        arten = [b["art"] for b in FW.befunde()]
        self.assertEqual(arten.count("rechenfehler"), 1)


class Hauptschleife(Grundlage):
    def _blockieren(self, sekunden):
        async def ablauf():
            D.schleife(asyncio.get_running_loop())
            await asyncio.sleep(0.6)                       # Herzschlag läuft an
            time.sleep(sekunden)                           # blockiert die Schleife (synchron)
            await asyncio.sleep(0.8)                       # Prüf-Faden sieht das Ende
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(ablauf())
        finally:
            loop.close()

    def test_bremse_mit_stelle(self):
        pfad = self.starten()
        with mock.patch.object(FW, "BREMSE_S", 0.6):      # Abstand zur Blockade: kein Wackeln auf vollem PC
            self._blockieren(2.0)
        text = self.text(pfad)
        self.assertIn("⚠ Bremse: Hauptschleife", text)
        self.assertIn("in ablauf", text)                   # die Stelle im Code, die blockiert hat
        self.assertNotIn("BEFUND haenger", text)

    def test_haenger_mit_allen_faeden(self):
        pfad = self.starten()
        with mock.patch.object(FW, "HAENGER_S", 1.5):
            self._blockieren(3.0)
        text = self.text(pfad)
        self.assertIn("BEFUND haenger: Hauptschleife reagiert seit", text)
        self.assertIn("[Hauptschleife]", text)
        self.assertIn("Hänger vorbei nach", text)
        self.assertEqual([b["art"] for b in FW.befunde()], ["haenger"])

    def test_aus_und_wieder_an(self):
        # Nach beenden → starten laufen Herzschlag und Prüf-Faden wieder, je einmal.
        self.starten()

        async def ablauf():
            loop = asyncio.get_running_loop()
            D.schleife(loop)
            await asyncio.sleep(0.4)
            erster = FW._faden
            D.beenden()
            erster.join(2)
            await asyncio.sleep(0.4)                       # Herzschlag endet mit dem Bericht
            self.assertIsNone(FW._schlag_schleife)
            self.starten()
            D.schleife(loop)
            await asyncio.sleep(0.4)
            self.assertIsNotNone(FW._puls)
            return erster
        loop = asyncio.new_event_loop()
        try:
            erster = loop.run_until_complete(ablauf())
        finally:
            loop.close()
        self.assertIsNot(FW._faden, erster)
        self.assertTrue(FW._faden.is_alive())


    def test_prozess_stillstand_mit_allen_faeden(self):
        pfad = self.starten()
        FW._bremse_melden(2.0, "a.py:1 in x", True, "Faden-1: b.py:2 in y")
        text = self.text(pfad)
        self.assertIn("der ganze Prozess stand", text)
        self.assertIn("    Faden-1: b.py:2 in y", text)


class Stellen(unittest.TestCase):
    def test_gebautes_programm_relative_pfade(self):
        # Im gebauten Programm fehlen site-packages-Pfade; der Modulname entscheidet.
        self.assertFalse(FW._eigen("httpcore\\_sync\\connection.py", "httpcore._sync.connection"))
        self.assertFalse(FW._eigen("asyncio\\base_events.py", "asyncio.base_events"))
        self.assertTrue(FW._eigen("engines\\cloud.py", "cloud"))
        self.assertTrue(FW._eigen("main.py", "__main__"))


class Halter(unittest.TestCase):
    def test_attribut_und_wartende_aufgabe(self):
        class Modell:
            pass

        class Chat:
            pass
        chat = Chat()
        chat.engine = Modell()

        async def doppelpuffer(engine):
            await asyncio.sleep(10)

        async def ablauf(chat):                            # hält nur `chat`, nicht das Modell selbst
            aufgabe = asyncio.get_running_loop().create_task(doppelpuffer(chat.engine), name="puffer")
            await asyncio.sleep(0)
            halter = FW.wer_haelt(chat.engine)
            aufgabe.cancel()
            return halter
        halter = asyncio.run(ablauf(chat))
        self.assertIn("Attribut Chat.engine", halter)
        self.assertTrue(any("doppelpuffer" in h for h in halter), halter)
        self.assertFalse(any("wer_haelt" in h or "ablauf" in h for h in halter), halter)   # Suche zählt nicht

    def test_befund_festgehalten(self):
        D.beenden()
        ordner = Path(tempfile.mkdtemp()) / "Debugbericht"
        with mock.patch.object(D, "an", return_value=True):
            pfad = D.starten("haupt", ordner, takt=False)
            try:
                class Engine:
                    pass
                e = Engine()
                halter = {"motor": e}                       # noqa: F841 – hält e fest
                FW.festgehalten(e, "Testmodell", 10)
            finally:
                D.beenden()
        self.assertIn("BEFUND leck: Testmodell nach dem Entladen 10 s später noch im Speicher", pfad.read_text("utf-8"))


class Absturz(Grundlage):
    def _marke(self, pid, spur=""):
        self.ordner.mkdir(parents=True, exist_ok=True)
        log = self.ordner / f"alt_haupt_{pid}.log"
        log.write_text("Start\nletzte Zeile vor dem Ende\n", encoding="utf-8")
        absturz = ""
        if spur:
            absturz = str(self.ordner / f"alt_haupt_{pid}.absturz")
            Path(absturz).write_text(spur, encoding="utf-8")
        (self.ordner / f"laeuft_haupt_{pid}.json").write_text(json.dumps(
            {"pid": pid, "start": "16.10.2026 18:22:54", "log": str(log), "absturz": absturz}), encoding="utf-8")

    def test_absturz_des_letzten_starts(self):
        self._marke(999_991, spur="Windows fatal exception: access violation\n\nThread 0x1 (most recent call first):")
        with mock.patch.object(FW, "_pid_lebt", return_value=False):
            pfad = self.starten()
        text = self.text(pfad)
        self.assertIn("BEFUND absturz: Start 16.10.2026 18:22:54 (PID 999991) ist abgestürzt", text)
        self.assertIn("access violation", text)
        self.assertFalse((self.ordner / "laeuft_haupt_999991.json").exists())
        self.assertIn("abgestürzt", FW.hinweis_beim_start())

    def test_unsauberes_ende_ohne_spur(self):
        self._marke(999_992)
        with mock.patch.object(FW, "_pid_lebt", return_value=False), \
                mock.patch.object(FW, "windows_beendet", return_value=False):
            pfad = self.starten()
        text = self.text(pfad)
        self.assertIn("BEFUND unsauber: Start 16.10.2026 18:22:54 (PID 999992) endete ohne normales Beenden", text)
        self.assertIn("letzte Zeile vor dem Ende", text)
        self.assertEqual(FW.hinweis_beim_start(), "")      # nur nach echtem Absturz ein Hinweis

    def test_herunterfahren_ist_kein_befund(self):
        # Wache am 22.10. 02:05: PC ausgeschaltet, Qt bekam kein Abmelden mit – Windows' Protokoll zeigt es
        self._marke(999_994)
        zeiten = []
        with mock.patch.object(FW, "_pid_lebt", return_value=False), \
                mock.patch.object(FW, "windows_beendet", side_effect=lambda z: zeiten.append(z) or True):
            pfad = self.starten()
        text = self.text(pfad)
        self.assertNotIn("BEFUND", text)
        self.assertIn("endete mit dem Herunterfahren oder Abmelden von Windows", text)
        self.assertAlmostEqual(time.time(), zeiten[0], delta=60)        # Zeit = letzte Zeile des alten Berichts
        self.assertEqual(FW.befunde(), [])

    def test_windows_beendet_ohne_zeit(self):
        self.assertFalse(FW.windows_beendet(0))

    def test_laufender_anderer_prozess_zaehlt_nicht(self):
        self._marke(999_993)
        with mock.patch.object(FW, "_pid_lebt", return_value=True):
            self.starten()
        self.assertEqual(FW.befunde(), [])
        self.assertTrue((self.ordner / "laeuft_haupt_999993.json").exists())

    def test_sauberes_ende_raeumt_marke_und_leere_spur(self):
        pfad = self.starten()
        marke = self.ordner / f"laeuft_haupt_{os.getpid()}.json"
        spur = pfad.with_suffix(".absturz")
        self.assertTrue(marke.exists())
        self.assertTrue(spur.exists())
        D.beenden()
        self.assertFalse(marke.exists())
        self.assertFalse(spur.exists())


class Selbstdiagnose(Grundlage):
    def test_auffaelligkeiten_abschnitt(self):
        import selbstdiagnose as S
        self.starten()
        self.assertIn("keine", "\n".join(S._auffaelligkeiten()))
        FW.befund("bremse", "Oberfläche 3.2 s blockiert", "gguflokal.py:705 in entladen")
        text = "\n".join(S._auffaelligkeiten())
        self.assertIn("bremse: Oberfläche 3.2 s blockiert", text)
        self.assertIn("(dieser Start)", text)


if __name__ == "__main__":
    unittest.main()
