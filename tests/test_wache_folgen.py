"""Offline-Tests: Folgen-Netz der Wache (GRU auf Prozess-Ketten), sauberes Lernen,
Fehlerschutz des Motors, explorer.exe-Herkunft und PID-Wiederverwendung.

python -m unittest discover -s tests -p test_wache_folgen.py
"""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from wache import ereignisse as E, folgen as F, gru as G, herkunft as H, justierung as J     # noqa: E402
from wache import selbsttest as ST, sensoren as SE, speicher as S                            # noqa: E402

T0 = ST.ANKER


def start(pid, ts, name, eltern=None, eltern_name="explorer.exe"):
    return E.Ereignis(E.PROZESS, "process_start", f"Start {name}", zeit=ts, prozess=name, pid=pid, start=ts,
                      ppid=eltern[0] if eltern else 0, eltern_start=eltern[1] if eltern else 0.0,
                      eltern=eltern_name)


def netz(pid, ts_start, name, port, ts, extern=True):
    return E.Ereignis(E.NETZ, "conn_open", f"{name} → :{port}", zeit=ts, prozess=name, pid=pid,
                      start=ts_start, zielport=port, extra={"extern": extern})


class GruTests(unittest.TestCase):
    def test_gradient_stimmt_numerisch(self):
        g = G.GRU(6, 3, 4, zufall=3)
        rng = np.random.default_rng(1)
        X, Y = rng.integers(0, 6, (2, 4)), rng.integers(0, 6, (2, 4))
        M = np.ones((2, 4)); M[1, 2:] = 0
        _, grad = g.verlust_und_gradient(X, Y, M)
        for k in ("emb", "Uzr", "Un", "Wo"):
            i = tuple(rng.integers(0, n) for n in g.p[k].shape)
            alt = g.p[k][i]
            g.p[k][i] = alt + 1e-6; a, _ = g.verlust_und_gradient(X, Y, M)
            g.p[k][i] = alt - 1e-6; b, _ = g.verlust_und_gradient(X, Y, M)
            g.p[k][i] = alt
            self.assertAlmostEqual((a - b) / 2e-6, grad[k][i], places=5, msg=k)

    def test_lernt_verteilung_und_speichert_identisch(self):
        g = G.GRU(5, 6, 12)
        verlauf = g.trainieren([[1, 2, 3]] * 120 + [[1, 4, 3]] * 40, epochen=30)
        self.assertLess(verlauf[-1], verlauf[0])
        p = g.verteilung(g.schritt(g.anfang(), 1))[0]
        self.assertGreater(p[2], p[4])
        self.assertGreater(p[4], p[0])
        g2 = G.GRU.aus_zustand(g.zustand())
        np.testing.assert_allclose(p, g2.verteilung(g2.schritt(g2.anfang(), 1))[0])


class KettenbuchTests(unittest.TestCase):
    def test_gleiche_pid_andere_startzeit_sind_zwei_ketten(self):
        kb = F.Kettenbuch()
        kb.schritte(start(100, T0, "a.exe"))
        s = kb.schritte(start(100, T0 + 50, "b.exe"))
        self.assertTrue(s[-1].neu)
        self.assertEqual(2, len(kb))
        self.assertEqual("a.exe", kb.name((100, round(T0, 3))))

    def test_kind_haengt_nur_mit_passender_elternstartzeit_an(self):
        kb = F.Kettenbuch()
        kb.schritte(start(10, T0, "winword.exe"))
        s = kb.schritte(start(20, T0 + 5, "powershell.exe", eltern=(10, T0), eltern_name="winword.exe"))
        self.assertEqual(["K:powershell.exe"], s[0].zeichen)
        self.assertEqual(["^", "E:winword.exe", "S:powershell.exe"], s[1].zeichen)
        # gleiche PID des Elternteils, andere Startzeit: kein Anhängen an fremde Kette
        s = kb.schritte(start(21, T0 + 6, "cmd.exe", eltern=(10, T0 + 3)))
        self.assertEqual(1, len(s))

    def test_wiederholung_zaehlt_einmal_fenster_und_ende(self):
        kb = F.Kettenbuch(fenster=5)
        kb.schritte(start(1, T0, "chrome.exe"))
        self.assertEqual(1, len(kb.schritte(netz(1, T0, "chrome.exe", 443, T0 + 1))))
        self.assertEqual([], kb.schritte(netz(1, T0, "chrome.exe", 443, T0 + 2)))
        kb.schritte(netz(1, T0, "chrome.exe", 80, T0 + 3))
        s = kb.schritte(netz(1, T0, "chrome.exe", 443, T0 + 4))
        self.assertEqual([F.KOPF_WEITER, "S:chrome.exe", "N:443:e"], s[0].zeichen)
        stopp = E.Ereignis(E.PROZESS, "process_stop", "x", pid=1, start=T0, prozess="chrome.exe")
        self.assertTrue(kb.schritte(stopp)[0].ende)
        self.assertEqual(0, len(kb))

    def test_ohne_startzeit_keine_kette(self):
        self.assertEqual([], F.Kettenbuch().schritte(netz(5, 0.0, "x.exe", 443, T0)))

    def test_laufender_prozess_bekommt_eigenen_kopf_und_verdraengen(self):
        kb = F.Kettenbuch(max_ketten=2)
        s = kb.schritte(netz(7, T0, "svchost.exe", 443, T0))
        self.assertEqual([F.KOPF_LIEF, "S:svchost.exe"], s[0].zeichen)
        kb.schritte(netz(8, T0, "a.exe", 443, T0))
        s = kb.schritte(netz(9, T0, "b.exe", 443, T0))
        self.assertTrue(any(x.ende and x.kette == (7, round(T0, 3)) for x in s))

    def test_training_sieht_dieselben_folgen_wie_die_bewertung(self):
        ev = ST.ketten_alltag(1)
        folgen = F.folgen_bauen(ev)
        self.assertTrue(folgen)
        for zeichen, bewerten in folgen:
            self.assertIn(zeichen[0], (F.KOPF, F.KOPF_LIEF, F.KOPF_WEITER))
            self.assertEqual(len(zeichen), len(bewerten))

    def test_vokabular_seltene_namen_teilen_sammelzeichen(self):
        v = F.Vokabular.bauen([(["^", "E:explorer.exe", "S:chrome.exe"], [False, False, True])] * 3
                              + [(["^", "E:explorer.exe", "S:einmal.exe"], [False, False, True])])
        self.assertEqual(v.id("S:~"), v.id("S:einmal.exe"))
        self.assertNotEqual(v.id("S:~"), v.id("S:chrome.exe"))
        self.assertEqual(v.id("?"), v.id("N:c2:e"))


class FolgenDetektorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ordner = Path(self.tmp.name) / "modelle" / "folgen"

    def test_zu_wenige_ketten(self):
        r = F.FolgenDetektor(self.ordner).trainieren([start(1, T0, "a.exe")])
        self.assertFalse(r["ok"])

    def test_alltag_ruhig_angriffe_laut_und_versionen(self):
        d = F.FolgenDetektor(self.ordner, schwelle=0.995)
        self.assertFalse(d.trainiert)
        self.assertEqual((0.0, ""), d.bewerten(start(1, T0, "a.exe")))
        r = d.trainieren(ST.ketten_alltag(10))
        self.assertTrue(r["ok"]); self.assertEqual("v0001", r["version"])
        werte = [w for w in (d.bewerten(e)[0] for e in ST.ketten_alltag(3, zufall=5, anfang=T0 + 86400)) if w]
        self.assertLessEqual(sum(1 for w in werte if d.ist_auffaellig(w)) / len(werte), 0.03)
        for _, ereignisse in ST.ketten_angriffe(T0 + 9 * 86400):
            d.kettenbuch = F.Kettenbuch()
            self.assertTrue(d.ist_auffaellig(max(d.bewerten(e)[0] for e in ereignisse)))
        # gleiche Gewichte nach dem Laden
        probe = ST.ketten_angriffe(T0 + 20 * 86400)[0][1]
        d2 = F.FolgenDetektor(self.ordner, schwelle=0.995)
        d.kettenbuch = F.Kettenbuch()
        self.assertEqual([d.bewerten(e)[0] for e in probe], [d2.bewerten(e)[0] for e in probe])
        # Versionen: höchstens drei, zurückdrehen und Zeiger übernehmen
        for _ in range(3):
            d.trainieren(ST.ketten_alltag(10))
        self.assertEqual(["v0002", "v0003", "v0004"], [p.stem for p in F.versionen(self.ordner)])
        self.assertIn("v0003", F.zurueckdrehen(self.ordner))
        self.assertTrue(d.zeiger_pruefen()); self.assertEqual("v0003", d.version)
        self.assertEqual("v0003", F.info(self.ordner)["version"])
        d.zuruecksetzen()
        self.assertFalse(d.trainiert); self.assertEqual([], F.versionen(self.ordner))

    def test_schatten_protokoll_einmal_pro_stunde(self):
        d = F.FolgenDetektor(self.ordner)
        e = start(3, T0, "x.exe")
        d.schatten_melden(e, 0.999, "x.exe: N:c2:e (p=0.0001)")
        d.schatten_melden(e, 0.999, "x.exe: N:c2:e (p=0.0002)")
        self.assertEqual(1, len(F.schatten_lesen(self.ordner)))
        self.assertEqual(1, F.schatten_zaehlen(self.ordner, T0 - 1))


class LernEreignisseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.sp = S.Speicher(Path(self.tmp.name) / "w.db"); self.addCleanup(self.sp.schliessen)

    def _alarm(self, e, status, aid=""):
        a = E.Alarm("x", "x", E.Schwere.HOCH, "R001", ereignis_id=e.id, zeit=e.zeit, status=status)
        if aid:
            a.id = aid
        self.sp.alarme_schreiben([a])

    def test_kette_mit_offenem_alarm_lernt_nicht_harmlos_schon(self):
        boese = start(10, T0, "powershell.exe")
        boese_netz = netz(10, T0, "powershell.exe", 4444, T0 + 5)
        gut = start(11, T0, "tool.exe")
        gut_netz = netz(11, T0, "tool.exe", 443, T0 + 5)
        gleiche_pid_spaeter = start(10, T0 + 600, "notepad.exe")
        self.sp.ereignisse_schreiben([boese, boese_netz, gut, gut_netz, gleiche_pid_spaeter])
        self._alarm(boese, "offen")
        self._alarm(gut, "harmlos", aid="gut")
        ids = {e.id for e in self.sp.lern_ereignisse(T0 - 10, T0 + 1000)}
        self.assertEqual({gut.id, gut_netz.id, gleiche_pid_spaeter.id}, ids)
        # echt und unklar zählen genauso wie offen – samt Kette
        for status in ("echt", "gesehen"):
            self.sp.alarm_urteil("gut", status, "Nutzer")
            ids = {e.id for e in self.sp.lern_ereignisse(T0 - 10, T0 + 1000)}
            self.assertEqual({gleiche_pid_spaeter.id}, ids, status)

    def test_alte_ereignisse_ohne_startzeit_ueber_pid_und_name(self):
        alt = E.Ereignis(E.PROZESS, "process_start", "x", zeit=T0, prozess="x.exe", pid=50)
        nach = E.Ereignis(E.NETZ, "conn_open", "x", zeit=T0 + 60, prozess="x.exe", pid=50)
        viel_spaeter = E.Ereignis(E.NETZ, "conn_open", "x", zeit=T0 + 7200, prozess="x.exe", pid=50)
        self.sp.ereignisse_schreiben([alt, nach, viel_spaeter])
        self._alarm(alt, "offen")
        self.assertEqual([viel_spaeter.id], [e.id for e in self.sp.lern_ereignisse(T0 - 10, T0 + 9000)])

    def test_gleichmaessig_ueber_das_ganze_fenster(self):
        evs = [start(1000 + i, T0 + i * 360, f"p{i}.exe") for i in range(1000)]   # 100 Stunden
        self.sp.ereignisse_schreiben(evs)
        gelernt = self.sp.lern_ereignisse(T0 - 1, T0 + 1000 * 360, limit=100)
        self.assertEqual(100, len(gelernt))
        zeiten = [e.zeit for e in gelernt]
        self.assertEqual(sorted(zeiten), zeiten)
        self.assertLess(zeiten[0] - T0, 360 * 20)                  # nicht nur die neuesten Stunden …
        self.assertGreater(zeiten[-1] - T0, 360 * 980)            # … sondern bis ans Ende

    def test_ausduennen_laesst_belastete_kette_draussen(self):
        boese = start(10, T0, "p.exe")
        kette = [netz(10, T0, "p.exe", 443, T0 + 10 + i) for i in range(300)]
        gut = [start(2000 + i, T0 + 5 + i, f"g{i}.exe") for i in range(300)]
        self.sp.ereignisse_schreiben([boese] + kette + gut)
        self._alarm(boese, "unpruefbar")
        gelernt = self.sp.lern_ereignisse(T0 - 10, T0 + 1000, limit=50)
        self.assertEqual(50, len(gelernt))
        self.assertFalse([e for e in gelernt if e.pid == 10])

    def test_karenz_grenze_bis(self):
        self.sp.ereignisse_schreiben([start(1, T0, "a.exe"), start(2, T0 + 100, "b.exe")])
        self.assertEqual(["a.exe"], [e.prozess for e in self.sp.lern_ereignisse(T0 - 1, T0 + 50)])

    def test_alte_datenbank_bekommt_startspalten(self):
        pfad = Path(self.tmp.name) / "alt.db"
        c = sqlite3.connect(str(pfad))
        c.execute("CREATE TABLE ereignisse (id TEXT PRIMARY KEY, zeit REAL NOT NULL, kategorie TEXT NOT NULL, "
                  "aktion TEXT NOT NULL, schwere INTEGER DEFAULT 0, text TEXT DEFAULT '', prozess TEXT DEFAULT '', "
                  "pid INTEGER DEFAULT 0, ppid INTEGER DEFAULT 0, eltern TEXT DEFAULT '', nutzer TEXT DEFAULT '', "
                  "exe TEXT DEFAULT '', cmdline TEXT DEFAULT '', lokal TEXT DEFAULT '', ziel TEXT DEFAULT '', "
                  "zielport INTEGER DEFAULT 0, protokoll TEXT DEFAULT '', datei TEXT DEFAULT '', "
                  "score REAL DEFAULT 0.0, anomalie INTEGER DEFAULT 0, extra TEXT DEFAULT '')")
        c.execute("INSERT INTO ereignisse (id, zeit, kategorie, aktion) VALUES ('alt', ?, 'prozess', 'x')", (T0,))
        c.commit(); c.close()
        sp = S.Speicher(pfad); self.addCleanup(sp.schliessen)
        self.assertEqual(0.0, sp.ereignis("alt").start)
        sp.ereignisse_schreiben([start(9, T0 + 1, "neu.exe", eltern=(8, T0))])
        neu = sp.ereignisse(limit=1)[0]
        self.assertEqual((round(T0 + 1, 3), T0), (round(neu.start, 3), neu.eltern_start))


class MotorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        import protokoll
        from wache import motor as MO, vollscan as VS
        self.MO = MO
        for ziel, name, wert in ((MO, "MODELL_ORDNER", tmp / "modelle"), (MO, "TRAINING_LOG", tmp / "t.log"),
                                 (VS, "DB", tmp / "vollscan.db"),
                                 (protokoll, "schreibe", lambda *a, **k: None)):
            p = patch.object(ziel, name, wert); p.start(); self.addCleanup(p.stop)
        self.e = dict(J.STANDARD); self.e["grenzen"] = dict(J.STANDARD["grenzen"]); self.e["ml"] = False
        self.m = MO.Motor(tmp / "w.db", self.e)
        self.addCleanup(self.m.speicher.schliessen)
        self.addCleanup(self.m.baseline.schliessen)

    def test_fehler_beendet_die_schleife_nicht_und_meldet_stoerung(self):
        m = self.m
        m._bewerten_in = lambda e, a: (_ for _ in ()).throw(RuntimeError("kaputt"))
        stapel, alarme = [], []
        for i in range(self.MO.FEHLER_GRENZE):
            m.queue.put(start(100 + i, T0 + i, "x.exe"))
            m._durchlauf(stapel, alarme, 0.0)
        self.assertEqual(self.MO.FEHLER_GRENZE, m.speicher.anzahl_ereignisse())   # trotzdem gespeichert
        self.assertIn("kaputt", m.stand()["gestoert"])
        self.assertTrue(any("kaputt" in f for f in m.stand()["fehler"]))
        m._letzter_fehler = time.time() - self.MO.RUHIG_NACH - 1
        m._ruhig_pruefen()
        self.assertEqual("", m.gestoert)

    def test_schreibfehler_haelt_ereignisse_fuer_den_naechsten_takt(self):
        m = self.m
        m.queue.put(start(1, T0, "a.exe"))
        echt = m.speicher.ereignisse_schreiben
        m.speicher.ereignisse_schreiben = lambda liste: (_ for _ in ()).throw(sqlite3.OperationalError("locked"))
        stapel, alarme = [], []
        with self.assertRaises(sqlite3.OperationalError):
            m._durchlauf(stapel, alarme, 0.0)
        self.assertEqual(1, len(stapel))
        m.speicher.ereignisse_schreiben = echt
        m._durchlauf(stapel, alarme, 0.0)
        self.assertEqual(([], 1), (stapel, m.speicher.anzahl_ereignisse()))

    def test_schatten_meldet_keinen_alarm_warnen_schon(self):
        m = self.m
        self.assertTrue(m.folgen.trainieren(ST.ketten_alltag(10))["ok"])
        _, ereignisse = ST.ketten_angriffe(T0 + 9 * 86400)[2]
        alarme = []
        for e in ereignisse:
            m._bewerten(e, alarme)
        self.assertEqual([], [a for a in alarme if a.regel == "DL-001"])
        self.assertGreater(m.dl_auffaellig, 0)
        self.assertTrue(F.schatten_lesen(m.folgen.ordner))
        self.assertTrue(any("dl" in (e.extra or {}) for e in ereignisse))
        m.e["dl_alarm"] = True
        m.folgen.kettenbuch = F.Kettenbuch()
        _, ereignisse = ST.ketten_angriffe(T0 + 10 * 86400)[2]
        alarme = []
        for e in ereignisse:
            m._bewerten(e, alarme)
        dl = [a for a in alarme if a.regel == "DL-001"]
        self.assertEqual(1, len(dl))
        self.assertEqual("dl", dl[0].quelle)
        self.assertIn("a8f3k2p9.exe", dl[0].subjekt)

    def test_folgen_training_nur_aus_alten_sauberen_ereignissen(self):
        m = self.m
        jetzt = time.time()
        alt = ST.ketten_alltag(10, anfang=jetzt - 12 * 86400 + 3 * 86400)
        frisch = [start(9000, jetzt - 60, "frisch.exe")]
        m.speicher.ereignisse_schreiben(alt + frisch)
        with patch.object(m.folgen, "trainieren", wraps=m.folgen.trainieren) as t:
            self.assertIn("gestartet", m.folgen_trainieren("Test", erzwingen=True, warten=True))
        gesehen = {e.prozess for e in t.call_args.args[0]}
        self.assertNotIn("frisch.exe", gesehen)
        self.assertTrue(m.folgen.trainiert)
        self.assertIn("später", m.folgen_trainieren("Test"))
        m.e["dl"] = False
        self.assertIn("aus", m.folgen_trainieren("Test", erzwingen=True))

    def test_stand_und_statuszeile(self):
        from wache import werkzeuge as W
        st = self.m.stand()
        self.assertFalse(st["dl"]["trainiert"]); self.assertFalse(st["dl"]["alarm"])
        self.assertIn("Schattenmodus", W.folgen_zeile(st["dl"]))
        self.assertIn("aus", W.folgen_zeile({"an": False}))
        self.assertIn("DL-001", W.regeln_text())


class HerkunftTests(unittest.TestCase):
    def test_explorer_nur_am_genauen_ort(self):
        echt = H.os.path.join(H._WINDOWS, "explorer.exe")
        self.assertEqual("", H.maskerade("explorer.exe", echt))
        self.assertEqual("", H.maskerade("Explorer.EXE", echt.upper()))
        self.assertEqual("", H.maskerade("explorer.exe", H.os.path.join(H._WINDOWS, "SysWOW64", "explorer.exe")))
        self.assertTrue(H.maskerade("explorer.exe", H.os.path.join(H._WINDOWS, "Temp", "explorer.exe")))
        self.assertTrue(H.maskerade("explorer.exe", r"C:\Users\x\AppData\Local\Temp\explorer.exe"))
        # der Windows-Ordner selbst bleibt für andere Systemnamen falsch
        self.assertTrue(H.maskerade("svchost.exe", H.os.path.join(H._WINDOWS, "svchost.exe")))


class SensorTests(unittest.TestCase):
    def test_elternstart_nur_wenn_eltern_aelter(self):
        tabelle = {5: {"start": 100.0}}
        self.assertEqual(100.0, SE.ProzessSensor._elternstart({"ppid": 5, "start": 200.0}, tabelle))
        self.assertEqual(0.0, SE.ProzessSensor._elternstart({"ppid": 5, "start": 50.0}, tabelle))
        self.assertEqual(0.0, SE.ProzessSensor._elternstart({"ppid": 6, "start": 200.0}, tabelle))

    def test_netzsensor_erkennt_wiederverwendete_pid(self):
        class P:
            def __init__(self, name, start):
                self._n, self._s = name, start
            def name(self): return self._n
            def exe(self): return rf"C:\P\{self._n}"
            def create_time(self): return self._s
        s = SE.NetzSensor()
        with patch.object(SE.psutil, "Process", lambda pid: P("alt.exe", 100.0)):
            self.assertEqual(("alt.exe", r"C:\P\alt.exe", 100.0), s._prozessinfo(42))
        with patch.object(SE.psutil, "Process", lambda pid: P("neu.exe", 200.0)):
            self.assertEqual("neu.exe", s._prozessinfo(42)[0])


if __name__ == "__main__":
    unittest.main()
