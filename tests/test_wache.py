"""Offline-Tests: Systemwache (tools/wache) – ohne echte Sensoren, ohne Modellaufruf.

python -m unittest discover -s tests -p test_wache.py
"""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from wache import ereignisse as E, regeln as R, merkmale as M, wald as W, speicher as S      # noqa: E402
from wache import detektor as D, justierung as J, wecker as WK, inventar as I               # noqa: E402

ANKER = datetime(2026, 6, 15, 14, 30).timestamp()


def prozess(name="x.exe", eltern="explorer.exe", exe=r"C:\Programme\x.exe", cmd="", ts=ANKER, **extra):
    return E.Ereignis(E.PROZESS, "process_start", f"Start {name}", zeit=ts, prozess=name, eltern=eltern,
                      exe=exe, cmdline=cmd, extra=extra)


def netz(name, ziel, port, ts=ANKER, extern=True):
    return E.Ereignis(E.NETZ, "conn_open", f"{name} → {ziel}:{port}", zeit=ts, prozess=name, ziel=ziel,
                      zielport=port, extra={"extern": extern})


class Regeln(unittest.TestCase):
    def setUp(self):
        self.r = R.Regelwerk()

    def ids(self, e):
        return sorted({a.regel for a in self.r.pruefen(e)})

    def test_r001_r002_r003(self):
        self.assertEqual(["R001", "R002"], self.ids(prozess("powershell.exe", "winword.exe",
                                                             cmd="powershell -w hidden -enc AAAA")))
        self.assertEqual(["R002"], self.ids(prozess("svchost.exe", "chrome.exe", r"C:\Windows\System32\svchost.exe")))
        self.assertEqual(["R003"], self.ids(prozess("a.exe", exe=r"C:\Users\x\AppData\Local\Temp\a.exe")))
        self.assertEqual([], self.ids(prozess("svchost.exe", "services.exe", r"C:\Windows\System32\svchost.exe")))

    def test_r006_r007_beaconing(self):
        self.assertEqual(["R006"], self.ids(netz("x.exe", "8.8.8.8", 4444)))
        r = R.Regelwerk()
        treffer = set()
        for i in range(8):
            treffer |= {a.regel for a in r.pruefen(netz("impl.exe", "45.1.2.3", 443, ANKER + i * 60))}
        self.assertIn("R007", treffer)
        r2 = R.Regelwerk()
        treffer = set()
        for i, gap in enumerate([5, 90, 20, 200, 3, 150, 40, 300]):
            treffer |= {a.regel for a in r2.pruefen(netz("mensch.exe", "45.1.2.3", 443, ANKER + sum([5, 90, 20, 200, 3, 150, 40, 300][:i + 1])))}
        self.assertNotIn("R007", treffer)                     # unregelmäßig = menschlich

    def test_r005_r013_dateien(self):
        st = r"C:\Users\x\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\u.vbs"
        e = E.Ereignis(E.DATEI, "file_created", "u.vbs", datei=st, extra={"endung": ".vbs"})
        self.assertEqual(["R005"], self.ids(e))
        e = E.Ereignis(E.DATEI, "file_created", "x.scr", datei=r"C:\Users\x\Downloads\x.scr", extra={"endung": ".scr"})
        self.assertEqual(["R013"], self.ids(e))

    def test_r010_r011_inventar(self):
        e = E.Ereignis(E.SYSTEM, "inventory_new_autostart", "Neuer Autostart: x", exe=r"C:\Temp\x.exe")
        a = self.r.pruefen(e)
        self.assertEqual("R010", a[0].regel)
        self.assertEqual(E.Schwere.KRITISCH, a[0].schwere)
        e = E.Ereignis(E.SYSTEM, "inventory_new_dienst", "Neuer Dienst: y")
        self.assertEqual(["R011"], self.ids(e))

    def test_stumm_zaehlt_aber_meldet_nicht(self):
        r = R.Regelwerk(stumm={"R003"})
        self.assertEqual([], [a.regel for a in r.pruefen(prozess("a.exe", exe=r"C:\Users\x\Downloads\a.exe"))])
        self.assertEqual(1, r.treffer["R003"])


class Wald(unittest.TestCase):
    def test_ausreisser_hoch_normal_niedrig(self):
        rng = np.random.default_rng(1)
        X = rng.normal(0, 1, size=(800, 8))
        sk = W.Skalierer().anpassen(X)
        f = W.IsolationForest(60, 128).anpassen(sk.anwenden(X))
        normal = f.anomalie(sk.anwenden(X)).mean()
        weit = f.anomalie(sk.anwenden(np.array([[7.0] * 8])))[0]
        self.assertGreater(weit, normal + 0.15)

    def test_zustand_rund(self):
        rng = np.random.default_rng(2)
        X = rng.normal(0, 1, size=(300, 5))
        f = W.IsolationForest(20, 64).anpassen(X)
        f2 = W.IsolationForest.aus_zustand(json.loads(json.dumps(f.zustand())))
        self.assertTrue(np.allclose(f.anomalie(X[:10]), f2.anomalie(X[:10])))

    def test_konstante_achsen(self):
        X = np.zeros((50, 3)); X[:, 0] = np.arange(50)
        sk = W.Skalierer().anpassen(X)
        self.assertEqual([1, 2], sk.unbekannte_achsen(np.array([3.0, 1.0, 1.0])).tolist())
        self.assertEqual([], sk.unbekannte_achsen(np.array([3.0, 0.0, 0.0])).tolist())


class Merkmale(unittest.TestCase):
    def test_vektor_laenge_und_rein(self):
        m = M.ProzessMerkmale()
        e = prozess("a.exe", cpu=5, mem_mb=100)
        v1, v2 = m.vektor(e), m.vektor(e)
        self.assertEqual(len(M.PROZESS_MERKMALE), len(v1))
        self.assertEqual(v1, v2)                           # reine Funktion
        self.assertEqual(1.0, v1[M.PROZESS_MERKMALE.index("prozess_neu")])

    def test_profil_wirkt(self):
        m = M.ProzessMerkmale()
        m.profile_setzen({"a.exe": {"anzahl": 50, "exes": r"C:\Programme\a.exe", "eltern": "explorer.exe",
                                    "nutzer": "", "stunden": [0] * 24, "kinder": "", "ports": "443"}}, set())
        v = m.vektor(prozess("a.exe", exe=r"C:\Programme\a.exe"))
        idx = M.PROZESS_MERKMALE.index
        self.assertEqual(0.0, v[idx("prozess_neu")])
        self.assertEqual(0.0, v[idx("pfad_neu")])
        v = m.vektor(prozess("a.exe", exe=r"C:\Temp\a.exe", eltern="winword.exe"))
        self.assertEqual(1.0, v[idx("pfad_neu")])
        self.assertEqual(1.0, v[idx("eltern_neu")])
        v = m.vektor(netz("a.exe", "1.2.3.4", 4444))
        self.assertEqual(1.0, v[idx("port_neu_fuer_prozess")])

    def test_stunde_untypisch_braucht_mindestmenge(self):
        hist = [0] * 24; hist[14] = 80
        self.assertEqual(0.0, M.ProzessMerkmale._stunde_untypisch({"stunden": hist}, 3))
        hist[14] = 200
        self.assertGreater(M.ProzessMerkmale._stunde_untypisch({"stunden": hist}, 3), 0.85)
        self.assertLess(M.ProzessMerkmale._stunde_untypisch({"stunden": hist}, 14), 0.05)

    def test_entropie_und_burst(self):
        self.assertGreater(M.entropie("a8f3k2p9"), M.entropie("aaaaaaaa"))
        b = M.Burst(fenster=60, saettigung=10)
        werte = [b.beobachten("x", ANKER + i) for i in range(10)]
        self.assertEqual(1.0, werte[-1])
        self.assertLess(b.beobachten("x", ANKER + 500), 0.2)

    def test_datei_merkmale(self):
        m = M.DateiMerkmale()
        alltag = [E.Ereignis(E.DATEI, "file_created", "x", datei=rf"C:\T\f{i}.dll",
                             extra={"ordner": r"C:\T", "endung": ".dll"}) for i in range(20)]
        m.anpassen(alltag)
        idx = M.DATEI_MERKMALE.index
        v = m.vektor(E.Ereignis(E.DATEI, "file_created", "x", datei=r"C:\T\neu.vbs",
                                extra={"ordner": r"C:\T", "endung": ".vbs"}))
        self.assertEqual(1.0, v[idx("endung_neu_im_ordner")])
        self.assertEqual(0.0, v[idx("ordner_neu")])
        z = m.zustand()
        m2 = M.DateiMerkmale(); m2.zustand_laden(json.loads(json.dumps(z)))
        self.assertEqual(m.vektor(alltag[0]), m2.vektor(alltag[0]))


class Detektor(unittest.TestCase):
    def test_kaltstart_dann_training_dann_laden(self):
        tmp = tempfile.TemporaryDirectory()
        pfad = Path(tmp.name) / "m.json"
        m = M.ProzessMerkmale()
        d = D.Detektor(pfad, m, schwelle=0.97)
        self.assertFalse(d.trainiert)
        s, gruende = d.bewerten(prozess("neu.exe"))
        self.assertGreater(s, 0.3)                          # Kaltstart: neu = auffällig
        import random
        random.seed(1)
        namen = [f"p{i}.exe" for i in range(12)]
        m.profile_setzen({n: {"anzahl": 30, "exes": rf"C:\P\{n}", "eltern": "explorer.exe", "nutzer": "u",
                              "stunden": [0] * 24, "kinder": "", "ports": ""} for n in namen}, set())
        ereignisse = [prozess(random.choice(namen), exe=rf"C:\P\{random.choice(namen)}",
                              ts=ANKER - random.random() * 7 * 86400, cpu=random.random() * 5) for _ in range(300)]
        r = d.trainieren(ereignisse)
        self.assertTrue(r["ok"] and d.trainiert)
        normal = np.mean([d.bewerten(e)[0] for e in ereignisse[:100]])
        auffaellig, gruende = d.bewerten(prozess("dropper.exe", "winword.exe", r"C:\Users\x\AppData\Local\Temp\dropper.exe"))
        self.assertGreater(auffaellig, normal)
        self.assertTrue(any("nie gesehen" in g or "Prozess noch nie" in g for g in gruende))
        d2 = D.Detektor(pfad, M.ProzessMerkmale(), schwelle=0.97)
        d2.merkmale.profile_setzen(m.profile, set())
        self.assertTrue(d2.trainiert)
        self.assertAlmostEqual(d.bewerten(ereignisse[0])[0], d2.bewerten(ereignisse[0])[0])
        d.feedback({"dropper.exe"})
        self.assertLess(d.bewerten(prozess("dropper.exe", "winword.exe", r"C:\Temp\dropper.exe"))[0], auffaellig)
        tmp.cleanup()

    def test_zu_wenig_ereignisse(self):
        tmp = tempfile.TemporaryDirectory()
        d = D.Detektor(Path(tmp.name) / "m.json", M.ProzessMerkmale())
        self.assertFalse(d.trainieren([prozess()] * 5)["ok"])
        tmp.cleanup()


class Speicher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sp = S.Speicher(Path(self.tmp.name) / "w.db")

    def tearDown(self):
        self.sp.schliessen(); self.tmp.cleanup()

    def test_ereignisse_alarme_rund(self):
        e = prozess("a.exe", cmd="x")
        self.sp.ereignisse_schreiben([e])
        self.assertEqual(e.cmdline, self.sp.ereignis(e.id).cmdline)
        a = E.Alarm("t", "x", E.Schwere.HOCH, "R001", ereignis_id=e.id)
        self.sp.alarme_schreiben([a])
        self.assertEqual("offen", self.sp.alarm(a.id[:6]).status)
        self.assertTrue(self.sp.alarm_urteil(a.id, "harmlos", "Test", "weil"))
        self.assertEqual({"a.exe"}, self.sp.fehlalarm_prozesse())
        self.assertEqual(1, self.sp.alarm_statistik()["harmlos"])

    def test_profil_lernt(self):
        self.assertTrue(self.sp.profil_fortschreiben("a.exe", exe="p1", eltern="e1", stunde=3))
        self.assertFalse(self.sp.profil_fortschreiben("a.exe", exe="p2", eltern="e1", stunde=3, kind="k1", port=443))
        p = self.sp.profile()["a.exe"]
        self.assertEqual(2, p["anzahl"])
        self.assertEqual("p1|p2", p["exes"])
        self.assertEqual(2, p["stunden"][3])
        self.assertEqual("k1", p["kinder"]); self.assertEqual("443", p["ports"])

    def test_inventar_abgleich_und_doppelte(self):
        a = I.Eintrag(I.DIENST, "CDPUserSvc_f69e08b", r"C:\svc.exe")
        b = I.Eintrag(I.DIENST, "CDPUserSvc_1457a656", r"C:\svc.exe")
        self.assertEqual(a.id, b.id)                        # Sitzungs-Anhang normalisiert
        c = I.Eintrag(I.AUTOSTART, "x", "y")
        r = self.sp.inventar_abgleichen([a, b, c])
        self.assertEqual({"gesamt": 2, "neu": 2, "verschwunden": 0}, r)
        r = self.sp.inventar_abgleichen([a])
        self.assertEqual(1, r["verschwunden"])
        neu = I.neue_als_ereignisse([c, I.Eintrag(I.AUTOSTART, "z", "w")], self.sp.inventar_ids())
        self.assertEqual(["inventory_new_autostart"], [x.aktion for x in neu])
        self.assertEqual("z", neu[0].text.split(": ")[1].split(" (")[0])

    def test_justierungen_und_aufraeumen(self):
        self.sp.justierung_merken("Lara", "schwelle", 0.97, 0.98, "zu viele Fehlalarme")
        self.assertEqual("schwelle", self.sp.justierungen()[0]["was"])
        alt = prozess("alt.exe", ts=time.time() - 30 * 86400)
        self.sp.ereignisse_schreiben([alt, prozess("neu.exe", ts=time.time())])
        self.assertEqual(1, self.sp.aufraeumen(14, 1000))
        self.assertEqual(1, self.sp.anzahl_ereignisse())


class Justierung(unittest.TestCase):
    def setUp(self):
        self._alt = J.einstellungen
        self.e = dict(J.STANDARD); self.e["grenzen"] = dict(J.STANDARD["grenzen"])
        J.einstellungen = lambda: self.e

    def tearDown(self):
        J.einstellungen = self._alt

    def test_grenzen(self):
        self.assertEqual(("schwelle", 0.97, 0.95), J.pruefen("schwelle", "0,95"))
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("schwelle", 0.5)
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("schwelle", "abc")
        self.assertEqual(("stumme_regeln", [], ["R004"]), J.pruefen("regel_stumm", "R004=aus"))
        self.e["stumme_regeln"] = ["R001", "R002", "R003"]
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("regel_stumm", "R004=aus")
        self.assertEqual(("stumme_regeln", ["R001", "R002", "R003"], ["R002", "R003"]), J.pruefen("stumm", "R001=an"))
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("regel_stumm", "R099")
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("trigger_ab", "nie")                  # nur der Nutzer
        self.assertEqual(("trigger_ab", "hoch", "kritisch"), J.pruefen("trigger_ab", "kritisch"))
        with self.assertRaises(J.Grenzverletzung):
            J.pruefen("aktiv", False)                       # nicht justierbar

    def test_begruendung_pflicht(self):
        J.speichern = lambda neu: None
        # Sonst landet die Test-Justierung als echte Zeile in learned/aktionen.log.
        import protokoll
        self._schreibe = protokoll.schreibe
        protokoll.schreibe = lambda *a, **k: None
        self.addCleanup(lambda: setattr(protokoll, "schreibe", self._schreibe))
        with self.assertRaises(J.Grenzverletzung):
            J.anwenden("schwelle", 0.95, wer="Test", begruendung="ok")
        self.assertIn("0.97 → 0.95", J.anwenden("schwelle", 0.95, wer="Test",
                                                   begruendung="dieselbe harmlose Sache dreimal"))


class WeckerTest(unittest.IsolatedAsyncioTestCase):
    async def test_buendelt_und_weckt_einmal(self):
        tmp = tempfile.TemporaryDirectory()
        sp = S.Speicher(Path(tmp.name) / "w.db")
        geweckt = []

        async def wecken(alarme):
            geweckt.append(list(alarme))
            return "✅ ok"

        alt = WK.einstellungen
        WK.einstellungen = lambda: {**J.STANDARD, "trigger_buendel_s": 0.2, "trigger_pause_s": 0.1,
                                    "trigger_ab": "hoch", "trigger_ml": True}
        try:
            w = WK.Wecker(sp, wecken, asyncio.get_running_loop())
            hoch = E.Alarm("h", "x", E.Schwere.HOCH, "R006")
            mittel = E.Alarm("m", "x", E.Schwere.MITTEL, "R003")
            ml = E.Alarm("ml", "x", E.Schwere.MITTEL, "ML-001", quelle="ml", score=0.99)
            sp.alarme_schreiben([hoch, mittel, ml])
            w.alarm([hoch, mittel])
            w.alarm([ml])
            await asyncio.sleep(0.8)
            self.assertEqual(1, len(geweckt))
            self.assertEqual({"h", "ml"}, {a.titel for a in geweckt[0]})   # mittel passt nicht zur Stufe
            self.assertEqual("gesehen", sp.alarm(hoch.id).status)
            self.assertEqual("✅ ok", w.letzter_bericht)
        finally:
            WK.einstellungen = alt
            sp.schliessen(); tmp.cleanup()

    def test_auftrag_text_enthaelt_anleitung_und_ereignis(self):
        tmp = tempfile.TemporaryDirectory()
        sp = S.Speicher(Path(tmp.name) / "w.db")
        alt = WK.ANLEITUNG
        WK.ANLEITUNG = Path(tmp.name) / "wache_alarm.md"
        try:
            e = prozess("evil.exe", "winword.exe", cmd="-enc AAAA")
            sp.ereignisse_schreiben([e])
            a = E.Alarm("Verdächtig", "x", E.Schwere.HOCH, "R001", ereignis_id=e.id)
            text = WK.auftrag_text([a], sp, "Maia")
            self.assertIn("wache_bewerten", text)
            self.assertIn("evil.exe", text)
            self.assertIn("winword.exe", text)
            self.assertTrue(WK.ANLEITUNG.exists())          # Vorgabe angelegt
            self.assertNotIn("Bitdefender", WK.VORGABE_ANLEITUNG)   # rechnerneutral
        finally:
            WK.ANLEITUNG = alt
            sp.schliessen(); tmp.cleanup()


class KugelTest(unittest.TestCase):
    def test_gruesse_vorgabe_und_platzhalter(self):
        from wache import kugel as K
        tmp = tempfile.TemporaryDirectory()
        alt = K.GRUESSE_DATEI
        K.GRUESSE_DATEI = Path(tmp.name) / "gruesse.md"
        try:
            liste = K.gruesse("Maia", "test_user")
            self.assertTrue(K.GRUESSE_DATEI.exists())
            self.assertTrue(any("test_user" in g for g in liste))
            self.assertTrue(any("Maia" in g for g in liste))
            self.assertFalse(any("{nutzer}" in g or "{name}" in g for g in liste))
            K.GRUESSE_DATEI.write_text("# Kommentar\n- Hey {nutzer}, hier {name}\n\nAlles gut.\n",
                                       encoding="utf-8")
            self.assertEqual(["Hey test_user, hier Maia", "Alles gut."], K.gruesse("Maia", "test_user"))
        finally:
            K.GRUESSE_DATEI = alt
            tmp.cleanup()

    def test_farben_und_hex(self):
        from wache import kugel as K
        self.assertEqual("#3fb950", K._hex((63, 185, 80)))
        for f in ("gruen", "gelb", "rot", "grau"):
            self.assertIn(f, K.FARBEN)

    def test_qt_kugel_blase_panel_und_tray(self):
        """Qt: Kugel, Blase, Chatfenster und Tray-Schild
        entstehen ohne Bildschirm (offscreen), malen sich und reden über die Brücke."""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from wache import kugel as K, tray as T, oberflaeche as O
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        alt = (K.GRUESSE_DATEI, K.LAGE_DATEI)
        K.GRUESSE_DATEI, K.LAGE_DATEI = Path(tmp.name) / "g.md", Path(tmp.name) / "k.json"
        self.addCleanup(lambda: setattr(K, "GRUESSE_DATEI", alt[0]))
        self.addCleanup(lambda: setattr(K, "LAGE_DATEI", alt[1]))
        app = O.anwendung()
        loop = asyncio.new_event_loop()
        import threading
        threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True).start()
        self.addCleanup(lambda: loop.call_soon_threadsafe(loop.stop))
        lage = {"farbe": "gruen", "zahl": 0, "text": "ruhig"}

        async def chat(text, bilder):
            return f"Antwort auf: {text} ({len(bilder)} Bilder)", []

        k = K.Kugel(stand=lambda: lage, chat=chat, schleife=loop, sichtbar=lambda: True, name="Lara",
                    nutzer="test_user", nemicli_oeffnen=lambda: None, screenshot=lambda: None)
        k._takt_tick()
        self.assertTrue(k._gezeigt)
        k.fenster.takt()
        self.assertFalse(k.fenster.grab().isNull())
        k.blase("Hey test_user", 5)
        self.assertIsNotNone(k._blase)
        k.panel_umschalten()                                    # Blase weg, Fenster auf
        self.assertIsNone(k._blase); self.assertIsNotNone(k._panel)
        k.senden("Wie ist die Lage?")
        self.assertTrue(k._beschaeftigt)
        for _ in range(40):                                     # Antwort kommt über im_gui zurück
            app.processEvents(); time.sleep(0.02)
            if not k._beschaeftigt:
                break
        self.assertFalse(k._beschaeftigt)
        texte = [w.text() for w in k._panel.inhalt.findChildren(type(k._panel.status))]
        self.assertTrue(any("Antwort auf: Wie ist die Lage?" in t for t in texte))
        self.assertTrue(any(t == "Du" for t in texte) and any(t == "Lara" for t in texte))
        lage["farbe"] = "rot"; k._takt_tick()
        self.assertEqual("rot", k.fenster.farbe)
        k.verstecken(True); self.assertFalse(k.fenster.isVisible())
        k.verstecken(False); self.assertTrue(k.fenster.isVisible())
        k.panel_umschalten(); self.assertIsNone(k._panel)
        k._lage_speichern(); self.assertTrue(K.LAGE_DATEI.exists())
        # Tray-Schild: Häkchen, Zahl, 99+
        for farbe, zahl in (("gruen", 0), ("gelb", 7), ("rot", 123)):
            self.assertFalse(T.symbol(farbe, zahl).pixmap(64, 64).isNull())
        # Brücke: aus fremdem Thread in den GUI-Thread
        kam = []
        threading.Thread(target=lambda: O.im_gui(lambda: kam.append(threading.current_thread().name))).start()
        for _ in range(40):
            app.processEvents(); time.sleep(0.02)
            if kam:
                break
        self.assertEqual([threading.main_thread().name], kam)

    def test_nemicli_startet_comfyui_nicht(self):
        import comfyui
        self.assertFalse(hasattr(comfyui, "starten"))      # ComfyUI startet mit Windows, nicht durch NemiCLI
        code = (ROOT / "tools" / "wache" / "dienst.py").read_text(encoding="utf-8")
        self.assertNotIn("ComfyUI", code)
        self.assertIn("nemicli_laeuft()", code)             # Kugel weicht dem Terminal


class Verdrahtung(unittest.TestCase):
    def test_werkzeuge_eingehaengt(self):
        import actions, modes, sicherheit
        for t in ("wache_status", "wache_alarme", "wache_ereignisse", "wache_inventar",
                  "wache_bewerten", "wache_justieren"):
            self.assertIn(t, actions.ACTIONS)
            self.assertIn("Wache", actions.describe({"tool": t, "id": "1", "urteil": "harmlos",
                                                     "was": "schwelle", "wert": 0.9}))
        for t in ("wache_status", "wache_alarme", "wache_ereignisse", "wache_inventar"):
            self.assertIn(t, modes.READ_TOOLS)
        self.assertTrue({"wache_bewerten", "wache_justieren", "bild_malen"} <= modes.WACHE_URTEIL_TOOLS)
        self.assertNotIn("befehl", modes.WACHE_URTEIL_TOOLS)
        self.assertNotIn("loeschen", modes.WACHE_URTEIL_TOOLS)
        self.assertEqual("harmlos", sicherheit.stufe({"tool": "wache_bewerten", "id": "x", "urteil": "harmlos"}))

    def test_urteil_im_lesemodus_nur_im_dienst(self):
        import modes
        alt = modes.current()
        modes.set_mode("lesen")
        try:
            act = {"tool": "wache_bewerten", "id": "x", "urteil": "harmlos"}
            self.assertEqual("block", modes.decide(act, False))
            modes.wache_dienst(True)
            self.assertEqual("run", modes.decide(act, False))
            self.assertEqual("block", modes.decide({"tool": "loeschen", "pfad": "x"}, True))
        finally:
            modes.wache_dienst(False)
            modes.set_mode(alt)

    def test_prompt_und_befehl(self):
        text = (ROOT / "core" / "persona.py").read_text(encoding="utf-8")
        for w in ("wache_status", "wache_bewerten", "wache_justieren", "Isolation Forest"):
            self.assertIn(w, text)
        from commands import COMMAND_LIST
        self.assertIn("/wache", [c.name for c in COMMAND_LIST])
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"--wache" in sys.argv', code)
        self.assertIn("def wache_lauf", code)
        self.assertIn("modes.wache_dienst(True)", code)

    def test_kein_sklearn(self):
        for p in (ROOT / "tools" / "wache").glob("*.py"):
            code = p.read_text(encoding="utf-8")
            self.assertNotIn("import sklearn", code, p.name)
            self.assertNotIn("from sklearn", code, p.name)
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotRegex(req, r"(?m)^scikit-learn")

    def test_selbsttest_gruen(self):
        from wache import selbsttest
        text = selbsttest.laufen()
        self.assertIn("alle Prüfungen bestanden", text)


if __name__ == "__main__":
    unittest.main()


class BekanntTests(unittest.TestCase):
    """wache_bewerten harmlos + bezeichnung → Liste bekannter Dinge, beim nächsten Alarm dabei."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.sp = S.Speicher(Path(self.tmp.name) / "w.db"); self.addCleanup(self.sp.schliessen)
        from wache import zugang, werkzeuge
        self.W = werkzeuge
        self._alt = (zugang.motor, zugang._speicher)
        zugang.motor, zugang._speicher = None, self.sp
        import protokoll                       # nicht ins echte aktionen.log schreiben
        self._schreibe = protokoll.schreibe
        protokoll.schreibe = lambda *a, **k: None
        self.addCleanup(lambda: setattr(protokoll, "schreibe", self._schreibe))
        self.addCleanup(lambda: setattr(zugang, "motor", self._alt[0]))
        self.addCleanup(lambda: setattr(zugang, "_speicher", self._alt[1]))

    def _alarm(self, name="WinStore.App.exe", ziel=""):
        e = prozess(name=name, exe=r"C:\Programme\WindowsApps\x.exe")
        if ziel:
            e.ziel = ziel
        self.sp.ereignisse_schreiben([e])
        a = E.Alarm("Seltener Prozess", "x", E.Schwere.HOCH, "R001", ereignis_id=e.id)
        self.sp.alarme_schreiben([a])
        return a

    def test_bezeichnung_wird_gemerkt_und_beim_naechsten_alarm_gezeigt(self):
        a = self._alarm()
        out = self.W.bewerten({"id": a.id, "urteil": "harmlos", "begruendung": "Signiert von Microsoft, Store-App.",
                               "bezeichnung": "Microsoft Store"}, wer="Maia")
        self.assertIn("Als bekannt gemerkt: WinStore.App.exe = Microsoft Store", out)
        b = self.sp.bekannt("prozess:winstore.app.exe")
        self.assertEqual(("prozess", "Microsoft Store", "Maia"), (b["art"], b["bezeichnung"], b["wer"]))
        # zweiter Alarm zum selben Prozess: „bekannt“ steht in Detail, Liste und Weckruf
        a2 = self._alarm()
        detail = self.W.alarme({"id": a2.id})
        self.assertIn("bekannt: WinStore.App.exe = Microsoft Store – harmlos seit", detail)
        liste = self.W.alarme({"anzahl": 5})
        self.assertIn("↳ bekannt: WinStore.App.exe = Microsoft Store", liste)
        weck = WK.auftrag_text([a2], self.sp, "Maia")
        self.assertIn("⭑ bekannt: WinStore.App.exe = Microsoft Store", weck)
        self.assertIn("direkt harmlos bewerten", weck)
        # nochmal harmlos → Zähler steigt
        self.W.bewerten({"id": a2.id, "urteil": "harmlos", "begruendung": "Wie beim letzten Mal, nichts Neues.",
                         "bezeichnung": "Microsoft Store"}, wer="Maia")
        self.assertEqual(2, self.sp.bekannt("prozess:winstore.app.exe")["anzahl"])
        self.assertIn("[prozess] WinStore.App.exe = Microsoft Store", self.W.inventar({"art": "bekannt"}))

    def test_ohne_bezeichnung_tipp_und_nichts_gemerkt(self):
        a = self._alarm()
        out = self.W.bewerten({"id": a.id, "urteil": "harmlos", "begruendung": "Ist der Store, alles gut."})
        self.assertIn("Tipp: mit bezeichnung", out)
        self.assertEqual([], self.sp.bekannt_liste())

    def test_netz_alarm_merkt_prozess_und_ziel(self):
        a = self._alarm(name="WindowsPackageManagerServer.exe", ziel="2.23.246.164")
        self.W.bewerten({"id": a.id, "urteil": "harmlos", "begruendung": "winget lädt von Akamai.",
                         "bezeichnung": "winget-Dienst, Akamai-CDN"})
        arten = sorted(b["art"] for b in self.sp.bekannt_liste())
        self.assertEqual(["prozess", "ziel"], arten)
        self.assertTrue(self.sp.bekannt_vergessen("ziel:2.23.246.164"))
        self.assertEqual(1, len(self.sp.bekannt_liste()))

    def test_echt_merkt_nichts(self):
        a = self._alarm(name="Un.exe")
        self.W.bewerten({"id": a.id, "urteil": "echt", "begruendung": "Unsigniert aus Temp gestartet.",
                         "bezeichnung": "Dropper?"})
        self.assertEqual([], self.sp.bekannt_liste())



class KugelSteuerungTests(unittest.TestCase):
    """Die Persönlichkeit steuert ihre Kugel (Aktion `kugel` → Wache/kugel_zustand.json → Kugel)."""

    def setUp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from wache import steuerung as ST, kugel as K
        self.ST, self.K = ST, K
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._alt = (ST.ZUSTAND, ST.BILDER_ORDNER, K.GRUESSE_DATEI, K.LAGE_DATEI, ST._reden_min)
        ST.ZUSTAND, ST.BILDER_ORDNER = root / "kugel_zustand.json", root / "Persoenlichkeiten"
        K.GRUESSE_DATEI, K.LAGE_DATEI = root / "g.md", root / "k.json"
        ST._reden_min = lambda: 10.0
        ST.BILDER_ORDNER.mkdir()
        import protokoll
        self._schreibe = protokoll.schreibe
        self.protokoll = []
        protokoll.schreibe = lambda tool, text, *a, **k: self.protokoll.append(f"{tool}: {text}")
        self.addCleanup(lambda: setattr(protokoll, "schreibe", self._schreibe))
        self.addCleanup(self._zurueck)

    def _zurueck(self):
        ST, K = self.ST, self.K
        ST.ZUSTAND, ST.BILDER_ORDNER, K.GRUESSE_DATEI, K.LAGE_DATEI, ST._reden_min = self._alt

    def _bild(self, name):
        from PySide6.QtGui import QImage, QColor
        img = QImage(120, 120, QImage.Format.Format_ARGB32); img.fill(QColor(255, 120, 200, 255))
        img.save(str(self.ST.BILDER_ORDNER / name))

    def test_bilder_je_persoenlichkeit(self):
        for n in ("Lara.png", "lara_froh.PNG", "Lara-ernst.png", "Nemi_froh.png", "Lara_notizen.txt"):
            self._bild(n) if n.lower().endswith("png") else (self.ST.BILDER_ORDNER / n).write_text("x")
        self.assertEqual({"", "froh", "ernst"}, set(self.ST.bilder("Lara")))
        self.assertEqual({"froh"}, set(self.ST.bilder("Nemi")))
        self.assertEqual({}, self.ST.bilder("Maia"))

    def test_steuern_mit_grenzen(self):
        ST = self.ST
        # ohne Bild: Stimmung abgelehnt, Hinweis, wie man eins anlegt
        out = ST.steuern({"stimmung": "froh"}, wer="Lara")
        self.assertIn("kein Bild", out); self.assertIn("Persoenlichkeiten/Lara_froh.png", out)
        self._bild("Lara.png")
        out = ST.steuern({"stimmung": "froh", "bewegung": "hüpfen", "ecke": "oben links", "groesse": 120}, wer="Lara")
        self.assertIn("Stimmung froh (kein eigenes Bild dafür – zeige Lara.png)", out)
        self.assertIn("huepfen", out); self.assertIn("Ecke oben_links", out); self.assertIn("Größe 120px", out)
        z = ST.lesen()
        self.assertEqual(("froh", "huepfen", "oben_links", 120, "Lara"),
                         (z["stimmung"], z["bewegung"], z["ecke"], z["groesse"], z["wer"]))
        self.assertTrue(any(p.startswith("kugel: Kugel: Stimmung froh") for p in self.protokoll))
        # reden: einmal ja, gleich nochmal nein (10-min-Pause), mit wichtig ja; nachts nur wichtig
        stunde = datetime.now().hour
        tag = 8 <= stunde < 23
        out = ST.steuern({"sagen": "Hey  test_user,   alles ruhig."}, wer="Lara")
        if tag:
            self.assertIn("Blase „Hey test_user, alles ruhig.“", out)
            self.assertIn("Pause ist 10 min", ST.steuern({"sagen": "nochmal"}, wer="Lara"))
        else:
            self.assertIn("nachts", out)
        self.assertIn("Blase „Dringend", ST.steuern({"sagen": "Dringend!", "wichtig": True, "sekunden": 99}, wer="Lara"))
        self.assertEqual(60.0, ST.lesen()["sagen_sekunden"])
        # Unsinn wird benannt, nicht geschluckt
        out = ST.steuern({"bewegung": "fliegen", "ecke": "dach", "groesse": 999, "position": "abc"}, wer="Lara")
        self.assertIn("fliegen", out); self.assertIn("dach", out); self.assertIn("48–200", out); self.assertIn("[x, y]", out)
        self.assertIn("versteckt", ST.steuern({"versteckt": "ja"}, wer="Lara"))
        self.assertTrue(ST.lesen()["versteckt"])
        self.assertIn("wieder da", ST.steuern({"versteckt": False}, wer="Lara"))
        self.assertIn("Bilder für dich: (Standard)", ST.steuern({}, wer="Lara"))
        self.assertIn("wieder die Kugel", ST.steuern({"stimmung": "kugel"}, wer="Lara"))

    def test_kugel_setzt_zustand_um(self):
        from wache import oberflaeche as O
        ST, K = self.ST, self.K
        app = O.anwendung()
        self._bild("Lara.png"); self._bild("Lara_froh.png")
        loop = asyncio.new_event_loop()
        k = K.Kugel(stand=lambda: {"farbe": "gruen"}, chat=None, schleife=loop, sichtbar=lambda: True,
                    name="Lara", nutzer="test_user", nemicli_oeffnen=lambda: None, screenshot=lambda: None)
        k._takt_tick()
        self.assertEqual(ST.BILDER_ORDNER / "Lara.png", k._gesteuert["bild_pfad"])   # Bild da → Bild statt Kugel
        ST.steuern({"stimmung": "froh", "groesse": 100, "bewegung": "wackeln", "ecke": "oben_links"}, wer="Lara")
        k._takt_tick()
        self.assertIsNotNone(k.fenster.bild)
        self.assertEqual(100, k.fenster.groesse)
        self.assertEqual("wackeln", k.fenster._bewegung)
        self.assertEqual(ST.BILDER_ORDNER / "Lara_froh.png", k._gesteuert["bild_pfad"])
        self.assertLess(k.fenster.x(), 200)                      # oben links
        self.assertFalse(k.fenster.grab().isNull())              # malt sich mit Bild + Geste
        ST.steuern({"sagen": "Hallo test_user", "wichtig": True}, wer="Lara")
        k._takt_tick()
        self.assertIsNotNone(k._blase); self.assertEqual("Hallo test_user", k._blase.label.text())
        k._takt_tick()                                           # dieselbe Blase kommt nicht zweimal
        self.assertEqual("Hallo test_user", k._blase.label.text())
        ST.steuern({"versteckt": True}, wer="Lara"); k._takt_tick()
        self.assertFalse(k._gezeigt)
        ST.steuern({"versteckt": False, "stimmung": "kugel"}, wer="Lara"); k._takt_tick()
        self.assertTrue(k._gezeigt); self.assertIsNone(k.fenster.bild)
        # Aktion ist verdrahtet und im Wache-Dienst erlaubt
        import actions, modes, sicherheit
        self.assertIn("kugel", actions.ACTIONS); self.assertIn("kugel", modes.WACHE_URTEIL_TOOLS)
        self.assertEqual("harmlos", sicherheit.stufe({"tool": "kugel", "sagen": "x"}))
        self.assertIn("nicken", actions.describe({"tool": "kugel", "bewegung": "nicken"}))


class VerdichtenDaempfenTests(unittest.TestCase):
    """Viele offene Alarme aus demselben Vorgang.
    Verdichten (Zähler statt neue Zeile), Dämpfen (3× harmlos → still), leiser
    bei Bekanntem, und wache_bewerten für mehrere Alarme auf einmal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "w.db"
        self.sp = S.Speicher(self.db); self.addCleanup(self.sp.schliessen)
        from wache import zugang, werkzeuge
        self.W = werkzeuge
        self._alt = (zugang.motor, zugang._speicher)
        zugang.motor, zugang._speicher = None, self.sp
        import protokoll
        self._schreibe = protokoll.schreibe
        self.protokoll: list[str] = []
        protokoll.schreibe = lambda tool, text, *a, **k: self.protokoll.append(f"{tool}: {text}")
        self.addCleanup(lambda: setattr(protokoll, "schreibe", self._schreibe))
        self.addCleanup(lambda: setattr(zugang, "motor", self._alt[0]))
        self.addCleanup(lambda: setattr(zugang, "_speicher", self._alt[1]))
        self._e = J.einstellungen
        self.e = dict(J.STANDARD); self.e["grenzen"] = dict(J.STANDARD["grenzen"]); self.e["ml"] = False
        J.einstellungen = lambda: self.e
        self.addCleanup(lambda: setattr(J, "einstellungen", self._e))

    def _motor(self):
        from unittest.mock import patch
        from wache import motor as MO, vollscan as VS
        from wache.motor import Motor
        # Modelle und Vollscan im Temp-Ordner – nie die echten Daten des Rechners.
        tmp = Path(self.db).parent
        for ziel, wert in ((MO, ("MODELL_ORDNER", tmp / "modelle")), (VS, ("DB", tmp / "vollscan.db"))):
            p = patch.object(ziel, *wert)
            p.start()
            self.addCleanup(p.stop)
        m = Motor(self.db, self.e)
        self.addCleanup(m.speicher.schliessen)              # sonst hält Windows die Datei fest
        self.addCleanup(m.baseline.schliessen)
        return m

    def _r008(self, name="chrome.exe", ts=ANKER):
        e = netz(name, "142.250.1.1", 443, ts=ts)
        self.sp.ereignisse_schreiben([e])
        return E.Alarm("Auffällig viele Verbindungen", "x", E.Schwere.MITTEL, "R008",
                       ereignis_id=e.id, zeit=ts, subjekt=name), e

    # ---------------------------------------------------------------- Motor

    def test_gleicher_alarm_innerhalb_cooldown_wird_gezaehlt(self):
        m = self._motor()
        a1, e1 = self._r008()
        out = m._verdichten([a1], e1)
        self.assertEqual([a1], out); self.sp.alarme_schreiben(out)
        for i in range(1, 38):                                   # 37 Wiederholungen, alle 10 s
            a, e = self._r008(ts=ANKER + 10 * i)
            self.assertEqual([], m._verdichten([a], e))
        gespeichert = self.sp.alarme()
        self.assertEqual(1, len(gespeichert))
        self.assertEqual(38, gespeichert[0].anzahl)
        self.assertEqual(ANKER + 370, gespeichert[0].zuletzt)
        self.assertIn("×38", gespeichert[0].kurz())
        self.assertEqual(37, m.verdichtet)
        # gleitendes Fenster: 300 s nach dem LETZTEN Vorkommen ist es ein neuer Alarm
        a, e = self._r008(ts=ANKER + 370 + 301)
        self.assertEqual(1, len(m._verdichten([a], e)))
        # anderes Subjekt = eigener Alarm, sofort
        a, e = self._r008(name="steam.exe", ts=ANKER + 380)
        self.assertEqual(1, len(m._verdichten([a], e)))

    def test_bekanntes_subjekt_kommt_leiser(self):
        m = self._motor()
        self.sp.bekannt_merken("prozess:chrome.exe", "prozess", "chrome.exe", "Google Chrome", "Browser.", "Maia")
        a, e = self._r008()
        out = m._verdichten([a], e)
        self.assertEqual(E.Schwere.NIEDRIG, out[0].schwere)
        a, e = self._r008(name="fremd.exe")
        self.assertEqual(E.Schwere.MITTEL, m._verdichten([a], e)[0].schwere)
        # Muster-Regel: bekannt oder nicht, R001 bleibt laut
        e = prozess(name="chrome.exe", cmd="powershell -enc AAAA -w hidden"); self.sp.ereignisse_schreiben([e])
        a = E.Alarm("Verdächtige Kommandozeile", "x", E.Schwere.HOCH, "R001", ereignis_id=e.id, subjekt="chrome.exe")
        self.assertEqual(E.Schwere.HOCH, m._verdichten([a], e)[0].schwere)

    def test_daempfung_nach_dreimal_harmlos_und_echt_hebt_auf(self):
        alarme = []
        for i in range(3):
            a, _ = self._r008(ts=ANKER + 1000 * i)
            alarme.append(a)
        self.sp.alarme_schreiben(alarme)
        self.W.bewerten({"id": alarme[0].id, "urteil": "harmlos", "begruendung": "Chrome surft, normal."})
        self.W.bewerten({"id": alarme[1].id, "urteil": "harmlos", "begruendung": "Chrome surft, normal."})
        self.assertEqual({}, self.sp.gedaempft(3))
        out = self.W.bewerten({"id": alarme[2].id, "urteil": "harmlos", "begruendung": "Chrome surft, normal."})
        self.assertIn("Ab jetzt still (oft genug harmlos): R008 · chrome.exe", out)
        self.assertEqual({("R008", "chrome.exe"): 3}, self.sp.gedaempft(3))
        self.assertTrue(any(p.startswith("wache_daempfung: R008 · chrome.exe") for p in self.protokoll))
        m = self._motor()
        a, e = self._r008(ts=ANKER + 5000)
        self.assertEqual([], m._verdichten([a], e))
        self.assertEqual({("R008", "chrome.exe"): 1}, m.gedaempft_zaehler)
        from wache import zugang
        zugang.motor = m                                     # Status zeigt es, Urteil aktualisiert live
        self.assertIn("Gedämpft (meldet nicht mehr, nur gezählt): R008·chrome.exe (3× harmlos, seit Start 1× still)",
                      self.W.status({}))
        self.assertIn("Dämpfung ab 3× harmlos", self.W.status({}))
        # ein „echt“ hebt die Dämpfung auf – ohne dass jemand den Motor anstoßen muss
        self.W.bewerten({"id": alarme[1].id, "urteil": "echt", "begruendung": "Doch Datenabfluss gefunden."})
        self.assertEqual({}, self.sp.gedaempft(3))
        self.assertEqual(1, len(m._verdichten([a], e)))
        # 0 = Dämpfung aus
        self.assertEqual({}, self.sp.gedaempft(0))
        # Muster-Regeln werden nie gedämpft – auch nach 23× harmlos (R001·powershell.exe)
        r001 = []
        for i in range(5):
            e = prozess(name="powershell.exe", cmd="-nop -w hidden", ts=ANKER + i); self.sp.ereignisse_schreiben([e])
            r001.append(E.Alarm("Verdächtige Kommandozeile", "x", E.Schwere.MITTEL, "R001",
                                ereignis_id=e.id, subjekt="powershell.exe", status="harmlos"))
        self.sp.alarme_schreiben(r001)
        self.assertEqual({}, self.sp.gedaempft(3))
        a = E.Alarm("Verdächtige Kommandozeile", "x", E.Schwere.MITTEL, "R001", ereignis_id=e.id, subjekt="powershell.exe")
        self.assertEqual(1, len(m._verdichten([a], e)))

    # ------------------------------------------------------------ Werkzeuge

    def test_bewerten_mehrere_ids_und_nach_regel(self):
        chrome = [self._r008(ts=ANKER + 100 * i)[0] for i in range(5)]
        steam = [self._r008(name="steam.exe", ts=ANKER + 7)[0]]
        self.sp.alarme_schreiben(chrome + steam)
        # Eingabe kommagetrennt
        out = self.W.bewerten({"id": f"{chrome[0].id},{chrome[1].id}", "urteil": "harmlos",
                               "begruendung": "Chrome mit vielen Tabs.", "bezeichnung": "Google Chrome"})
        self.assertIn("2 Alarme → harmlos", out)
        self.assertEqual(1, self.sp.bekannt("prozess:chrome.exe")["anzahl"])    # je Aufruf einmal gezählt
        # Liste weist auf die Gruppe hin
        liste = self.W.alarme({"status": "offen"})
        self.assertIn('3× R008 zu "chrome.exe" → wache_bewerten regel="R008" subjekt="chrome.exe"', liste)
        # nach Regel + Subjekt: der Rest von Chrome, Steam bleibt offen
        out = self.W.bewerten({"regel": "r008", "subjekt": "chrome", "urteil": "harmlos",
                               "begruendung": "Chrome mit vielen Tabs.", "bezeichnung": "Google Chrome"})
        self.assertIn('3 Alarme zu R008 · "chrome" (offen/gesehen) → harmlos', out)
        self.assertEqual("offen", self.sp.alarm(steam[0].id).status)
        self.assertTrue(all(self.sp.alarm(a.id).status == "harmlos" for a in chrome))
        self.assertEqual(2, self.sp.bekannt("prozess:chrome.exe")["anzahl"])
        # ids als Liste, unbekannte werden genannt, nichts mehr offen → klare Meldung
        out = self.W.bewerten({"ids": [steam[0].id, "gibtsnicht"], "urteil": "gesehen"})
        self.assertIn("1 Alarm (nicht gefunden: gibtsnicht) → gesehen", out)
        self.assertIn("1 Alarm zu R008 (offen/gesehen) → gesehen", self.W.bewerten({"regel": "R008", "urteil": "gesehen"}))
        self.assertEqual("Keine unbeurteilten Alarme zu R004.", self.W.bewerten({"regel": "R004", "urteil": "gesehen"}))
        self.assertIn("unbekannte Regel R099", self.W.bewerten({"regel": "R099", "urteil": "gesehen"}))
        self.assertIn("id (eine oder mehrere", self.W.bewerten({"urteil": "gesehen"}))
        self.assertEqual("Kein Alarm mit id abc, def.", self.W.bewerten({"id": "abc,def", "urteil": "gesehen"}))

    def test_alte_datenbank_wird_nachgeruestet(self):
        import sqlite3
        pfad = Path(self.tmp.name) / "alt.db"
        c = sqlite3.connect(str(pfad))
        c.executescript("""CREATE TABLE alarme (
            id TEXT PRIMARY KEY, zeit REAL NOT NULL, titel TEXT NOT NULL, text TEXT DEFAULT '',
            schwere INTEGER DEFAULT 0, regel TEXT DEFAULT '', ereignis_id TEXT DEFAULT '',
            quelle TEXT DEFAULT 'regel', score REAL DEFAULT 0.0, status TEXT DEFAULT 'offen',
            urteil_von TEXT DEFAULT '', begruendung TEXT DEFAULT '');""")
        c.execute("INSERT INTO alarme VALUES ('alt1', ?, 'Alt', 'x', 2, 'R008', '', 'regel', 0, 'offen', '', '')",
                  (ANKER,))
        c.commit(); c.close()
        # Ereignis-Tabelle im heutigen Schema + zwei alte Alarme MIT Ereignis: Subjekt wird nachgetragen
        sp0 = S.Speicher(pfad); sp0.schliessen()          # legt ereignisse an, rüstet nach – noch ohne Ereignisse
        c = sqlite3.connect(str(pfad))
        c.execute("UPDATE alarme SET subjekt = ''")
        e1, e2 = netz("chrome.exe", "1.1.1.1", 443), prozess(name="k.exe", eltern="chrome.exe")
        c.executemany(f"INSERT INTO ereignisse VALUES ({','.join('?' * 21)})", [e1.zeile(), e2.zeile()])
        c.executemany("INSERT INTO alarme (id, zeit, titel, regel, ereignis_id) VALUES (?, ?, 'x', ?, ?)",
                      [("alt2", ANKER, "R008", e1.id), ("alt3", ANKER, "R004", e2.id)])
        c.execute("DROP INDEX idx_a_regel_subjekt"); c.execute("ALTER TABLE alarme DROP COLUMN subjekt")
        c.commit(); c.close()
        sp = S.Speicher(pfad); self.addCleanup(sp.schliessen)
        alt = sp.alarm("alt1")
        self.assertEqual(("", 1, 0.0), (alt.subjekt, alt.anzahl, alt.zuletzt))
        self.assertNotIn("×", alt.kurz())
        self.assertEqual("chrome.exe", sp.alarm("alt2").subjekt)     # Netz: Prozess (R008 zählt je Prozess)
        self.assertEqual("chrome.exe", sp.alarm("alt3").subjekt)     # R004: der Elternprozess
        sp.alarme_schreiben([E.Alarm("Neu", "x", E.Schwere.MITTEL, "R008", subjekt="chrome.exe")])
        self.assertEqual("chrome.exe", sp.alarme_unbeurteilt("R008")[0].subjekt)
        self.assertEqual({}, sp.gedaempft(3))            # alte Zeilen ohne Subjekt zählen nicht

    def test_training_nimmt_die_neuesten_und_zaehler_ueberlebt_neustart(self):
        # 30 Ereignisse, Limit 10 → die 10 NEUESTEN, aufsteigend (vorher: die 10 ältesten)
        self.sp.ereignisse_schreiben([prozess(name=f"p{i}.exe", ts=ANKER + i) for i in range(30)])
        liste = self.sp.ereignisse_seit(ANKER - 1, limit=10)
        self.assertEqual([f"p{i}.exe" for i in range(20, 30)], [e.prozess for e in liste])
        # Zähler: letztes Training bei ANKER+19.5 → 10 Ereignisse danach, der Motor weiß das beim Start
        self.sp.meta_setzen("training_zuletzt", str(ANKER + 19.5))
        self.assertEqual(10, self.sp.anzahl_ereignisse_seit(ANKER + 19.5))
        m = self._motor()
        self.assertEqual(10, m._seit_training)
        self.assertEqual(10, m.stand()["seit_training"])

    def test_nemicli_selbst_ist_kein_r001(self):
        """NemiCLIs eigene abfragen-PowerShell (-NoProfile = „-nop“ + „-noprofile“ +
        „-noninteractive“) galt als R001 Hoch → weckte die Persönlichkeit → die machte abfragen → …"""
        r = R.Regelwerk()
        rahmen = ("powershell -NoProfile -NonInteractive -Command $ProgressPreference='SilentlyContinue'; "
                  "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                  "$ErrorActionPreference='Stop'; try { Get-Process } catch { exit 1 }")
        eigen = prozess(name="powershell.exe", eltern="python.exe", cmd=rahmen)
        self.assertEqual([], r.pruefen(eigen))
        self.assertEqual(1, r.treffer["eigen"])
        # derselbe Rahmen unter fremdem Elternprozess: KEINE Ausnahme
        fremd = prozess(name="powershell.exe", eltern="winword.exe", cmd=rahmen)
        self.assertTrue(any(a.regel == "R001" for a in r.pruefen(fremd)))
        # -nop zählt nur als ganzes Wort: -NoProfile allein ist EIN Flag, nicht zwei → Mittel, nicht Hoch
        (a,) = [x for x in r.pruefen(prozess(name="powershell.exe", eltern="explorer.exe",
                                             cmd="powershell -NoProfile -Command Get-Date")) if x.regel == "R001"]
        self.assertEqual(E.Schwere.MITTEL, a.schwere)
        self.assertEqual(["-noprofile"], R._flags_in("powershell -NoProfile -Command Get-Date"))
        self.assertEqual(["-enc", "-w hidden"], R._flags_in("powershell -enc AAAA -w hidden"))
        self.assertEqual([], R._flags_in("Get-Process -Name explorer"))
        # „bekannt“ für Skript-Hosts hängt am Elternprozess
        self.sp.bekannt_merken("prozess:powershell.exe<python.exe", "prozess", "powershell.exe (von python.exe)",
                               "NemiCLI abfragen", "eigener Aufruf", "Test")
        self.assertEqual(1, len(self.sp.bekannt_fuer(eigen)))
        self.assertEqual([], self.sp.bekannt_fuer(fremd))
        self.assertEqual("prozess:powershell.exe<winword.exe", S.bekannt_schluessel(fremd)[0][0])
        self.assertEqual("prozess:chrome.exe", S.bekannt_schluessel(prozess(name="chrome.exe"))[0][0])

    def test_regeln_liefern_subjekt(self):
        r = R.Regelwerk()
        alarme = []
        for i in range(30):
            alarme += r.pruefen(prozess(name=f"k{i}.exe", eltern="chrome.exe", ts=ANKER + i))
        self.assertTrue(alarme and all(a.regel == "R004" and a.subjekt == "chrome.exe" for a in alarme))
        alarme = []
        for i in range(65):
            alarme += r.pruefen(netz("chrome.exe", f"10.0.0.{i}", 443, ts=ANKER + i))
        self.assertTrue(alarme and all(a.regel == "R008" and a.subjekt == "chrome.exe" for a in alarme))
        (a,) = r.pruefen(netz("evil.exe", "1.2.3.4", 4444))
        self.assertEqual(("R006", "evil.exe → 1.2.3.4"), (a.regel, a.subjekt))


class HerkunftTests(unittest.TestCase):
    """Ort, Signatur, Tarnung – die Belege, auf die ein Urteil sich stützen muss."""

    def setUp(self):
        from wache import herkunft as H
        self.H = H
        H._cache.clear()
        echt = H._pruefen                       # nie PowerShell im Test – und sauber zurück
        self.addCleanup(lambda: setattr(H, "_pruefen", echt))
        self.addCleanup(H._cache.clear)

    def test_systemort_erkennt_geschuetzte_und_offene_orte(self):
        self.assertTrue(self.H.ist_systemort(r"C:\Windows\System32\rundll32.exe"))
        self.assertTrue(self.H.ist_systemort(
            r"C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\TextInputHost.exe"))
        self.assertTrue(self.H.ist_systemort(r"C:\Program Files\WindowsApps\Foo_1.0_x64__abc\foo.exe"))
        # Ablageorte IM Windows-Ordner sind keine geschützten Orte: dort packen
        # auch Installer aus, und darauf lässt sich keine Herkunft stützen.
        self.assertFalse(self.H.ist_systemort(r"C:\Windows\SystemTemp\Unpacker\setup.exe"))
        self.assertFalse(self.H.ist_systemort(r"C:\Windows\Temp\x.exe"))
        self.assertFalse(self.H.ist_systemort(r"C:\Users\x\AppData\Local\Temp\TextInputHost.exe"))
        self.assertFalse(self.H.ist_systemort(""))

    def test_maskerade_nur_bei_systemnamen_am_falschen_ort(self):
        # Systemname an einem Ort, an dem er nicht liegen kann
        grund = self.H.maskerade("TextInputHost.exe", r"C:\Users\x\AppData\Local\Temp\TextInputHost.exe")
        self.assertIn("Tarnmuster", grund)
        self.assertIn("Temp", grund)
        # Dasselbe Programm am richtigen Ort: still
        self.assertEqual("", self.H.maskerade(
            "TextInputHost.exe",
            r"C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\TextInputHost.exe"))
        # Kein Systemname – updater.exe, python.exe & Co. liegen legitim überall
        self.assertEqual("", self.H.maskerade("updater.exe", r"C:\Users\x\AppData\Local\Temp\updater.exe"))
        # Pfad nicht lesbar (ohne Adminrechte normal) → lieber schweigen als falsch anschlagen
        self.assertEqual("", self.H.maskerade("svchost.exe", ""))
        self.assertEqual("", self.H.maskerade("", r"C:\Temp\x.exe"))

    def test_signatur_wird_gecacht_und_bei_aenderung_neu_geprueft(self):
        rufe = []

        def fake(exe, timeout):
            rufe.append(exe)
            return {"status": "Valid", "signierer": "Microsoft Windows", "gueltig": True}

        with tempfile.TemporaryDirectory() as d:
            ziel = Path(d) / "probe.exe"
            ziel.write_bytes(b"alt")
            self.H._pruefen = fake
            erst = self.H.signatur(str(ziel))
            zweit = self.H.signatur(str(ziel))
            self.assertTrue(erst["gueltig"])
            self.assertFalse(erst["gecacht"])
            self.assertTrue(zweit["gecacht"])
            self.assertEqual(1, len(rufe))              # zweiter Aufruf ohne PowerShell
            # Datei ausgetauscht → Kennung (Größe) ändert sich → neue Prüfung
            ziel.write_bytes(b"ganz anderer inhalt")
            self.H.signatur(str(ziel))
            self.assertEqual(2, len(rufe))

    def test_signatur_zeile_sagt_ort_und_stand(self):
        self.H._pruefen = lambda exe, timeout: {"status": "NotSigned", "signierer": "", "gueltig": False}
        with tempfile.TemporaryDirectory() as d:
            ziel = Path(d) / "x.exe"
            ziel.write_bytes(b"x")
            zeile = self.H.signatur_zeile(str(ziel))
        self.assertIn("NICHT signiert", zeile)
        self.assertIn("kein Systemordner", zeile)
        self.assertEqual("", self.H.signatur_zeile(""))
        self.assertIn("nicht mehr vorhanden", self.H.signatur_zeile(r"C:\gibt\es\nicht.exe"))


class R014Tests(unittest.TestCase):
    """Systemprogramm am falschen Ort – die Regel, die der TextInputHost-Fall verlangte."""

    def setUp(self):
        self.r = R.Regelwerk()

    def ids(self, e):
        return sorted(a.regel for a in self.r.pruefen(e))

    def test_prozessstart_aus_temp_unter_systemnamen(self):
        e = prozess("rundll32.exe", "explorer.exe", r"C:\Users\x\AppData\Local\Temp\rundll32.exe")
        alarme = self.r.pruefen(e)
        r014 = [a for a in alarme if a.regel == "R014"]
        self.assertEqual(1, len(r014))
        self.assertEqual(E.Schwere.KRITISCH, r014[0].schwere)
        self.assertIn("rundll32.exe", r014[0].text)

    def test_echtes_systemprogramm_schweigt(self):
        self.assertNotIn("R014", self.ids(
            prozess("rundll32.exe", "svchost.exe", r"C:\Windows\System32\rundll32.exe")))
        self.assertNotIn("R014", self.ids(
            prozess("StartMenuExperienceHost.exe", "svchost.exe",
                    r"C:\Windows\SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy"
                    r"\StartMenuExperienceHost.exe")))

    def test_netzverbindung_traegt_den_pfad_und_faellt_auf(self):
        # Netz-Alarm mit Pfad – und der Pfad passt nicht zum Namen.
        e = E.Ereignis(E.NETZ, "conn_open", "TextInputHost.exe → 104.18.20.226:80",
                       prozess="TextInputHost.exe", ziel="104.18.20.226", zielport=80,
                       exe=r"C:\Users\x\AppData\Local\Temp\TextInputHost.exe",
                       extra={"extern": True})
        self.assertIn("R014", self.ids(e))
        # Derselbe Alarm mit dem echten Pfad bleibt still
        e.exe = (r"C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy"
                 r"\TextInputHost.exe")
        self.assertNotIn("R014", sorted(a.regel for a in R.Regelwerk().pruefen(e)))

    def test_ohne_pfad_kein_alarm(self):
        # Ereignis ohne exe-Feld: Kein Pfad heißt „nichts gewusst“,
        # nicht „verdächtig“ – sonst alarmiert jeder unlesbare Prozess.
        e = E.Ereignis(E.NETZ, "conn_open", "TextInputHost.exe → 104.18.20.226:80",
                       prozess="TextInputHost.exe", ziel="104.18.20.226", zielport=80,
                       extra={"extern": True})
        self.assertNotIn("R014", self.ids(e))

    def test_r014_ist_stumm_schaltbar_wie_jede_regel(self):
        r = R.Regelwerk(stumm={"R014"})
        e = prozess("rundll32.exe", "explorer.exe", r"C:\Users\x\AppData\Local\Temp\rundll32.exe")
        self.assertEqual([], [a for a in r.pruefen(e) if a.regel == "R014"])
        self.assertEqual(1, r.treffer["R014"])          # gezählt, aber nicht gemeldet
        self.assertIn("R014", R.BESCHREIBUNG)


class NetzSensorPfadTests(unittest.TestCase):
    """Netz-Ereignisse müssen den Pfad tragen – ein Alarm „X verbindet sich nach
    draußen“ ohne prüfbaren Pfad lässt sich nicht beurteilen."""

    class _Addr:
        def __init__(self, ip, port):
            self.ip, self.port = ip, port

    class _Conn:
        def __init__(self, pid, lport, rip=None, rport=0, status="ESTABLISHED"):
            self.pid, self.status, self.type = pid, status, 1
            self.laddr = NetzSensorPfadTests._Addr("192.168.1.10", lport)
            self.raddr = NetzSensorPfadTests._Addr(rip, rport) if rip else None

    def _sensor(self, verbindungen, prozesse):
        from unittest import mock
        from wache import sensoren as SN

        class _P:
            def __init__(self, pid):
                if pid not in prozesse:
                    raise SN.psutil.NoSuchProcess(pid)
                self._n, self._e = prozesse[pid]

            def name(self):
                return self._n

            def exe(self):
                if self._e is None:
                    raise SN.psutil.AccessDenied(0)
                return self._e

        s = SN.NetzSensor()
        stapel = [[], verbindungen]
        with mock.patch.object(SN.psutil, "net_connections", side_effect=lambda kind: stapel.pop(0)), \
             mock.patch.object(SN.psutil, "Process", _P):
            s.sammeln()                      # erster Durchlauf = Baseline
            return s, s.sammeln()

    def test_conn_open_traegt_exe(self):
        conns = [self._Conn(4711, 50000, "104.18.20.226", 80)]
        _, ereignisse = self._sensor(conns, {4711: ("TextInputHost.exe", r"C:\Windows\SystemApps\X\TextInputHost.exe")})
        (e,) = [x for x in ereignisse if x.aktion == "conn_open"]
        self.assertEqual("TextInputHost.exe", e.prozess)
        self.assertEqual(r"C:\Windows\SystemApps\X\TextInputHost.exe", e.exe)
        self.assertEqual(("104.18.20.226", 80), (e.ziel, e.zielport))

    def test_listener_traegt_exe(self):
        conns = [self._Conn(99, 8188, status="LISTEN")]
        _, ereignisse = self._sensor(conns, {99: ("python.exe", r"C:\venv\python.exe")})
        (e,) = [x for x in ereignisse if x.aktion == "listen_open"]
        self.assertEqual(r"C:\venv\python.exe", e.exe)

    def test_ohne_leserecht_bleibt_das_feld_leer_statt_zu_raten(self):
        conns = [self._Conn(4, 445, "8.8.8.8", 443)]
        _, ereignisse = self._sensor(conns, {4: ("fremd.exe", None)})   # exe() = AccessDenied
        (e,) = [x for x in ereignisse if x.aktion == "conn_open"]
        self.assertEqual("fremd.exe", e.prozess)
        self.assertEqual("", e.exe)

    def test_pfad_wird_je_pid_nur_einmal_geholt(self):
        # Eine PID hat oft Dutzende Sockets – psutil.Process() pro Socket wäre teuer.
        conns = [self._Conn(7, 1000 + i, f"10.0.0.{i}", 443) for i in range(5)]
        sensor, ereignisse = self._sensor(conns, {7: ("chrome.exe", r"C:\Chrome\chrome.exe")})
        self.assertEqual(5, len([x for x in ereignisse if x.aktion == "conn_open"]))
        self.assertEqual({7: ("chrome.exe", r"C:\Chrome\chrome.exe")}, sensor._namen)


class ShellKontextTests(unittest.TestCase):
    """Das AppX-Protokoll am Alarm – damit keine Ursachenkette erfunden wird."""

    def setUp(self):
        from wache import werkzeuge as WZ
        self.WZ = WZ
        from wache import herkunft as H
        echt = H.appx_kontext
        self.addCleanup(lambda: setattr(H, "appx_kontext", echt))
        self.gefragt = []
        H.appx_kontext = lambda zeit, *a, **k: (
            self.gefragt.append(zeit) or ["22:24:55 | Register: OpenAI.Codex_26.917.6896.0"])

    def test_shell_prozess_bekommt_paketprotokoll(self):
        e = E.Ereignis(E.PROZESS, "process_start", "rundll32", prozess="rundll32.exe",
                       exe=r"C:\Windows\System32\rundll32.exe",
                       cmdline=r"rundll32.exe AppXDeploymentExtensions.OneCore.dll,ShellRefresh")
        zeilen = self.WZ._shell_kontext(e)
        self.assertTrue(any("OpenAI.Codex" in z for z in zeilen))
        self.assertEqual(1, len(self.gefragt))

    def test_gewoehnlicher_prozess_fragt_das_protokoll_nicht_ab(self):
        e = E.Ereignis(E.PROZESS, "process_start", "spiel", prozess="Borderlands4.exe",
                       exe=r"C:\Steam\Borderlands4.exe")
        self.assertEqual([], self.WZ._shell_kontext(e))
        self.assertEqual([], self.gefragt)              # kein PowerShell-Start für nichts

    def test_netzereignis_fragt_nicht(self):
        self.assertEqual([], self.WZ._shell_kontext(netz("rundll32.exe", "1.2.3.4", 443)))
        self.assertEqual([], self.gefragt)


class HerkunftOhneHilfsprozessTests(unittest.TestCase):
    """Signatur und Protokolle kommen aus Windows-Bibliotheken – kein Hilfsprozess."""

    def setUp(self):
        from wache import herkunft as H
        self.H = H
        H._cache.clear()
        self.addCleanup(H._cache.clear)

    def test_startet_keinen_prozess(self):
        from unittest import mock
        import subprocess
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("Hilfsprozess")), \
                mock.patch.object(self.H, "_evt_xml", return_value=[]):
            self.H._pruefen(r"C:\Windows\System32\kernel32.dll", 5.0)
            self.H.appx_kontext(time.time())
            self.H.update_kontext(time.time())

    @unittest.skipUnless(sys.platform == "win32", "nur Windows")
    def test_windows_datei_ist_ueber_katalog_von_microsoft(self):
        s = self.H._pruefen(r"C:\Windows\System32\kernel32.dll", 5.0)
        self.assertEqual("Valid", s["status"])
        self.assertTrue(s["gueltig"])
        self.assertIn("Microsoft", s["signierer"])

    @unittest.skipUnless(sys.platform == "win32", "nur Windows")
    def test_textdatei_ist_nicht_gueltig(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"
            p.write_text("hallo", encoding="utf-8")
            s = self.H._pruefen(str(p), 5.0)
        self.assertFalse(s["gueltig"])
        self.assertIn(s["status"], ("NotSigned", "NotSupportedFileFormat"))

    def test_fehler_in_der_bibliothek_heisst_nicht_pruefbar(self):
        from unittest import mock
        with mock.patch.object(self.H, "_wintrust", side_effect=OSError("weg")):
            s = self.H._pruefen(r"C:\x.exe", 5.0)
        self.assertEqual("unbekannt", s["status"])
        self.assertFalse(s["gueltig"])

    def test_appx_protokoll_wird_zu_zeilen(self):
        xml = ("<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
               "<EventID>400</EventID><TimeCreated SystemTime='2026-09-22T20:24:55Z'/>"
               "<Computer>rechnername</Computer></System><EventData>"
               "<Data Name='PackageFullName'>OpenAI.Codex_26.917.6896.0_x64__2p2nqsd0c76g0</Data>"
               "<Data Name='CallingProcess'>svchost.exe,wuauserv</Data></EventData></Event>")
        leer = xml.replace("OpenAI.Codex_26.917.6896.0_x64__2p2nqsd0c76g0", "")
        zeilen = self.H.appx_zeilen([xml, leer, "<kaputt"])
        self.assertEqual(1, len(zeilen))
        text = zeilen[0][1]
        self.assertIn("Bereitstellung gestartet: OpenAI.Codex", text)
        self.assertIn("(von svchost.exe,wuauserv)", text)
        self.assertNotIn("rechnername", text)

WU_XML = ("<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
          "<Provider Name='Microsoft-Windows-WindowsUpdateClient'/><EventID>{eid}</EventID>"
          "<TimeCreated SystemTime='{zeit}'/><Computer>rechnername</Computer>"
          "<Security UserID='S-1-5-18'/></System><EventData>"
          "<Data Name='updateTitle'>{titel}</Data></EventData></Event>")
SETUP_XML = ("<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
             "<Provider Name='Microsoft-Windows-Servicing'/><EventID>{eid}</EventID>"
             "<TimeCreated SystemTime='{zeit}'/><Computer>rechnername</Computer></System>"
             "<UserData><CbsPackageChangeState xmlns='http://manifests.microsoft.com/win/2004/08/"
             "windows/setup_provider'><PackageIdentifier>{paket}</PackageIdentifier>"
             "<ErrorCode>{fehler}</ErrorCode></CbsPackageChangeState></UserData></Event>")


class UpdateKontextTests(unittest.TestCase):
    """Windows-Update-Beleg am Alarm – „nach einem Update“ nur, wenn es belegt ist."""

    def setUp(self):
        from wache import herkunft as H, werkzeuge as WZ
        self.H, self.WZ = H, WZ
        H._UPDATE_CACHE.clear()
        self.addCleanup(H._UPDATE_CACHE.clear)

    def test_protokoll_wird_zu_lesbaren_zeilen(self):
        zeilen = self.H.update_zeilen([
            WU_XML.format(eid=19, zeit="2026-09-23T09:17:32.18Z",
                          titel="2026-09 Vorschauupdate (KB5124010) (26200.9550)"),
            SETUP_XML.format(eid=2, zeit="2026-09-23T09:17:18.86Z", paket="KB5124010", fehler="0x0"),
            SETUP_XML.format(eid=2, zeit="2026-09-23T09:17:18.87Z",
                             paket="Microsoft-Windows-FodMetadataServicing-Desktop-Metadata", fehler="0x0"),
        ])
        text = "\n".join(z for _, z in zeilen)
        self.assertEqual(2, len(zeilen))                     # Metadaten-Paket fällt weg
        self.assertIn("Windows-Paket KB5124010: installiert", text)
        self.assertIn("Windows Update installiert: 2026-09 Vorschauupdate (KB5124010)", text)
        self.assertLess(zeilen[0][0], zeilen[1][0])          # älteste zuerst
        self.assertNotIn("rechnername", text)                # nichts Identifizierendes
        self.assertNotIn("S-1-5-18", text)

    def test_fehlschlag_bleibt_sichtbar(self):
        zeilen = self.H.update_zeilen([
            WU_XML.format(eid=20, zeit="2026-09-23T08:00:00Z", titel="Update (KB1)"),
            SETUP_XML.format(eid=2, zeit="2026-09-23T08:00:01Z", paket="KB1", fehler="0x800f0922"),
        ])
        text = "\n".join(z for _, z in zeilen)
        self.assertIn("Windows Update FEHLGESCHLAGEN", text)
        self.assertIn("FEHLGESCHLAGEN (0x800f0922)", text)

    def test_gleiche_zeilen_werden_gezaehlt(self):
        self.assertEqual(["a (3×)", "b", "a"],
                         self.H._zusammenfassen(["a", "a", "a", "b", "a"]))

    def test_kaputtes_xml_bricht_nichts(self):
        self.assertEqual([], self.H.update_zeilen(["<kaputt", "", "<Event/>"]))

    def test_liest_ohne_fremdprozess_und_merkt_es_sich(self):
        from unittest import mock
        import subprocess
        with mock.patch.object(self.H, "_evt_xml", return_value=[
                WU_XML.format(eid=19, zeit="2026-09-23T09:17:32Z", titel="Update (KB5124010)")]) as evt, \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("Hilfsprozess")):
            a = self.H.update_kontext(time.time())
            b = self.H.update_kontext(time.time())
        self.assertEqual(a, b)
        self.assertEqual(2, evt.call_count)                  # System + Setup, beim zweiten Mal gecacht

    def test_systemprogramm_bekommt_den_beleg(self):
        from unittest import mock
        e = E.Ereignis(E.PROZESS, "process_start", "mscorsvw", prozess="mscorsvw.exe", eltern="ngen.exe",
                       exe=r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\mscorsvw.exe")
        with mock.patch.object(self.H, "update_kontext", return_value=["23.09. 11:17:32 | Windows Update "
                                                                       "installiert: Update (KB5124010)"]):
            zeilen = self.WZ._update_kontext(e)
        self.assertIn("KB5124010", "\n".join(zeilen))

    def test_ohne_update_steht_das_ausdruecklich_da(self):
        from unittest import mock
        e = E.Ereignis(E.PROZESS, "process_start", "tiworker", prozess="TiWorker.exe",
                       exe=r"C:\Windows\WinSxS\x\TiWorker.exe")
        with mock.patch.object(self.H, "update_kontext", return_value=[]):
            self.assertIn("nichts installiert", "\n".join(self.WZ._update_kontext(e)))

    def test_gewoehnliches_programm_fragt_nicht(self):
        from unittest import mock
        with mock.patch.object(self.H, "update_kontext") as uk:
            self.assertEqual([], self.WZ._update_kontext(prozess("spiel.exe", exe=r"D:\Spiele\spiel.exe")))
            self.assertEqual([], self.WZ._update_kontext(netz("svchost.exe", "1.2.3.4", 443)))
        uk.assert_not_called()

    def test_anleitung_verlangt_den_beleg(self):
        text = WK.VORGABE_ANLEITUNG
        self.assertIn("Windows Update in den 24 h davor", text)
        self.assertIn("NUR erlaubt", text)


class WacheBefehlTests(unittest.TestCase):
    """Als exe gibt es kein main.py und kein pythonw - die exe startet sich selbst."""

    def test_exe_startet_sich_selbst(self):
        from unittest import mock
        from wache import zugang as Z
        exe = str(Path("C:/x/NemiCLI.exe"))
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", exe):
            self.assertEqual([exe, "--wache"], Z.wache_befehl(Path("C:/x")))

    def test_skript_startet_main_py(self):
        from unittest import mock
        from wache import zugang as Z
        with mock.patch.object(sys, "frozen", False, create=True):
            befehl = Z.wache_befehl(Path("C:/prog"))
        self.assertEqual("--wache", befehl[-1])
        self.assertTrue(befehl[1].endswith("main.py"))
