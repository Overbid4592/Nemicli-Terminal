"""🛡 Wache-Fenster: Alarmliste, Steckbrief, Urteil des Nutzers, Modell der Wache-KI, Prompt und Werkzeuge,
die Sitzungs-Brücke (Vorschlag statt Urteil) und das Fenster (offscreen).

python -m unittest tests.test_wachezentrale
"""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import qt_testhilfe
import wachezentrale as WZ
from wache import herkunft, zugang
from wache import speicher as S
from wache.ereignisse import Alarm, Ereignis, Schwere

tearDownModule = qt_testhilfe.aufraeumen
SIGNATUR = "signiert: Beispiel GmbH · kein Systemordner"


def _datenbank(test: unittest.TestCase) -> S.Speicher:
    tmp = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, tmp, True)
    sp = S.Speicher(Path(tmp) / "w.db")
    test.addCleanup(sp.schliessen)
    ev1 = Ereignis("prozess", "process_start", "Start", prozess="updater_x.exe", eltern="dienst.exe",
                   exe=r"C:\Programme\Beispiel\updater_x.exe", cmdline="updater_x.exe --still")
    ev2 = Ereignis("prozess", "process_start", "Start", prozess="werkzeug.exe", exe=r"C:\Temp\werkzeug.exe")
    sp.ereignisse_schreiben([ev1, ev2])
    sp.alarme_schreiben([
        Alarm("Ausführung aus temporärem Pfad", "Start aus Temp", Schwere.MITTEL, "R003", ereignis_id=ev2.id,
              id="a00000000002", subjekt="werkzeug.exe"),
        Alarm("Anomalie", "Ungewöhnlich", Schwere.NIEDRIG, "ML-001", ereignis_id=ev1.id, id="a00000000001",
              subjekt="updater_x.exe", score=0.97, quelle="ml"),
        Alarm("Alt", "schon geklärt", Schwere.NIEDRIG, "R001", id="a00000000003", subjekt="alt.exe",
              status="harmlos"),
    ])
    for p in (mock.patch.object(zugang, "_speicher", sp), mock.patch.object(zugang, "motor", None),
              mock.patch.object(herkunft, "signatur_zeile", return_value=SIGNATUR)):
        p.start()
        test.addCleanup(p.stop)
    return sp


class Daten(unittest.TestCase):
    def setUp(self):
        self.sp = _datenbank(self)

    def test_liste_filter_und_suche(self):
        ids = [a["id"] for a in WZ.liste()]
        self.assertEqual({"a00000000001", "a00000000002"}, set(ids))            # harmlos nicht unter „aktiv“
        self.assertEqual(["a00000000003"], [a["id"] for a in WZ.liste("harmlos")])
        self.assertEqual(["a00000000002"], [a["id"] for a in WZ.liste("aktiv", "WERKZEUG")])
        self.assertEqual(3, len(WZ.liste("alle")))
        self.assertEqual({"offen": 2, "harmlos": 1}, WZ.zaehlen())
        zeile = next(a for a in WZ.liste() if a["id"] == "a00000000002")
        self.assertEqual(("🟠", "offen", "R003"), (zeile["symbol"], zeile["status_text"], zeile["regel"]))

    def test_steckbrief(self):
        d = WZ.steckbrief("a00000000001")
        felder = dict(d["felder"])
        self.assertEqual(r"C:\Programme\Beispiel\updater_x.exe", felder["Pfad"])
        self.assertEqual("dienst.exe", felder["Elternprozess"])
        self.assertEqual(SIGNATUR, d["signatur"])
        self.assertIn("a00000000001", d["ki_text"])
        self.assertIn("Einordnung", d["einordnung"])
        self.assertIsNone(WZ.steckbrief("gibtsnicht"))

    def test_urteil_des_nutzers(self):
        ok, text = WZ.urteilen("a00000000002", "harmlos", "", "Eigenes Werkzeug")
        self.assertTrue(ok, text)
        al = self.sp.alarm("a00000000002")
        self.assertEqual(("harmlos", "Nutzer", WZ.GRUND_NUTZER), (al.status, al.urteil_von, al.begruendung))
        self.assertFalse(WZ.urteilen("a00000000001", "loeschen")[0])
        ok, _ = WZ.urteilen("a00000000001", "gesehen", "kurz")                 # kurzer Grund wird ergänzt
        self.assertTrue(ok)
        self.assertEqual("gesehen", self.sp.alarm("a00000000001").status)

    def test_loeschen_nur_der_eintrag(self):
        ev_id = self.sp.alarm("a00000000002").ereignis_id
        ok, text = WZ.loeschen("a00000000002")
        self.assertTrue(ok, text)
        self.assertIn("R003", text)
        self.assertIsNone(self.sp.alarm("a00000000002"))
        self.assertIsNotNone(self.sp.ereignis(ev_id))                          # das Ereignis bleibt
        self.assertEqual(["a00000000001"], [a["id"] for a in WZ.liste()])
        self.assertFalse(WZ.loeschen("a00000000002")[0])
        self.assertFalse(WZ.loeschen("")[0])

    def _aufgabe_alarm(self, name=r"\Test\Aufgabe Eins"):
        ev = Ereignis("system", "inventory_new_aufgabe", f"Neue geplante Aufgabe: {name}")
        self.sp.ereignisse_schreiben([ev])
        self.sp.alarme_schreiben([Alarm("Neuer geplante Aufgabe", "neu", Schwere.MITTEL, "R011", ereignis_id=ev.id,
                                        id="a00000000011", subjekt=name, status="unpruefbar")])
        return "a00000000011"

    def test_aufgabe_aus_dem_alarm(self):
        aid = self._aufgabe_alarm()
        self.assertEqual(r"\Test\Aufgabe Eins", WZ.aufgabe_von(aid))
        self.assertEqual("", WZ.aufgabe_von("a00000000002"))                    # kein Aufgaben-Alarm
        self.sp.alarm_loeschen(aid)
        self.assertEqual("", WZ.aufgabe_von(self._aufgabe_alarm('\\Test\\x" & echo')))   # Sonderzeichen: nie

    def test_aufgabe_deaktivieren_und_loeschen(self):
        aid = self._aufgabe_alarm()
        abfrage = NS(returncode=0, stdout='"\\Test\\Aufgabe Eins","N/A","Bereit"\n')
        aufrufe = []

        def schtasks(*args, **_):
            aufrufe.append(args)
            return abfrage if args[0] == "/Query" else NS(returncode=0, stdout="")
        with mock.patch.object(WZ, "_schtasks", side_effect=schtasks), \
                mock.patch.object(WZ, "_schtasks_admin") as admin:
            ok, text = WZ.aufgabe_aendern(aid, "deaktivieren")
        self.assertTrue(ok, text)
        self.assertIn(("/Change", "/TN", r"\Test\Aufgabe Eins", "/Disable"), aufrufe)
        admin.assert_not_called()                                               # ging ohne Adminrechte
        al = self.sp.alarm(aid)
        self.assertEqual(("gesehen", "Nutzer"), (al.status, al.urteil_von))
        self.assertIn("deaktiviert", al.begruendung)

        def verweigert(*args, **_):
            return abfrage if args[0] == "/Query" else NS(returncode=1, stdout="")
        with mock.patch.object(WZ, "_schtasks", side_effect=verweigert), \
                mock.patch.object(WZ, "_schtasks_admin", return_value=0) as admin:
            self.assertTrue(WZ.aufgabe_aendern(aid, "loeschen")[0])
        self.assertEqual(("/Delete", "/TN", r"\Test\Aufgabe Eins", "/F"), admin.call_args[0][0])
        with mock.patch.object(WZ, "_schtasks", side_effect=verweigert), \
                mock.patch.object(WZ, "_schtasks_admin", return_value=1223):        # Rückfrage abgelehnt
            self.assertFalse(WZ.aufgabe_aendern(aid, "loeschen")[0])
        with mock.patch.object(WZ, "_schtasks", return_value=NS(returncode=1, stdout="")):
            self.assertIn("nicht mehr", WZ.aufgabe_aendern(aid, "loeschen")[1])
        self.assertFalse(WZ.aufgabe_aendern(aid, "formatieren")[0])
        self.assertFalse(WZ.aufgabe_aendern("a00000000002", "loeschen")[0])

    def test_admin_aufruf(self):
        with mock.patch.object(WZ.subprocess, "run", return_value=NS(returncode=0)) as run:
            self.assertEqual(0, WZ._schtasks_admin(("/Change", "/TN", r"\Test\Aufgabe Eins", "/Disable")))
        befehl = run.call_args[0][0][-1]
        self.assertIn("-Verb RunAs", befehl)
        self.assertIn('"\\Test\\Aufgabe Eins"', befehl)
        self.assertTrue(run.call_args[0][0][0].lower().endswith("powershell.exe"))

    def test_steckbrief_mit_aufgabe(self):
        aid = self._aufgabe_alarm()
        with mock.patch.object(WZ, "aufgabe_zustand", return_value="Bereit"):
            d = WZ.steckbrief(aid)
        self.assertEqual((r"\Test\Aufgabe Eins", "Bereit"), (d["aufgabe"], d["aufgabe_zustand"]))
        self.assertNotIn("aufgabe", WZ.steckbrief("a00000000002"))

    def test_ki_weiss_nichts_vom_loeschen(self):
        werkzeuge = (" ".join(WZ.WERKZEUGE) + " " + " ".join(WZ.WERKZEUGE.values())).lower()
        self.assertNotIn("lösch", werkzeuge)
        self.assertNotIn("loesch", werkzeuge)
        self.assertNotIn("deaktiv", werkzeuge)
        self.assertNotIn("loeschen", WZ.URTEILE)
        self.assertTrue("nichts löschen" in WZ.system_prompt("Test").lower(), "Verbot fehlt in der Anleitung")

    def test_ordner_nur_fuer_vorhandene_dateien(self):
        with mock.patch.object(WZ.subprocess, "Popen") as popen:
            self.assertFalse(WZ.ordner_zeigen("a00000000002"))                 # Datei gibt es nicht
            self.assertFalse(WZ.ordner_zeigen("a00000000003"))                 # ohne Ereignis
        popen.assert_not_called()


class Modell(unittest.TestCase):
    def test_eigene_wahl_sonst_wie_die_wache(self):
        import ersatzbank
        with mock.patch.object(ersatzbank, "rausgeflogen", return_value=[]), \
                mock.patch.object(ersatzbank, "liste", return_value=["ollama_cloud:bank-1"]):
            self.assertEqual("ollama_cloud:glm-x", WZ.modell({"wache_ki_modell": "ollama_cloud:glm-x",
                                                             "model": "gguf:klein"}))
            self.assertEqual("ollama_cloud:bank-1", WZ.modell({"wache_ki_modell": "gguf:klein",
                                                              "model": "gguf:klein"}))
            self.assertEqual("ollama_cloud:aktiv", WZ.modell({"model": "ollama_cloud:aktiv"}))

    def test_setzen_nur_cloud_und_auswahl(self):
        import config
        import ersatzbank
        with mock.patch.object(config, "update") as upd, \
                mock.patch.object(config, "load", return_value={"model": "ollama_cloud:aktiv",
                                                                 "wache_ki_modell": "ollama_cloud:glm-x"}), \
                mock.patch.object(ersatzbank, "liste", return_value=["openrouter:bank"]):
            with self.assertRaises(ValueError):
                WZ.modell_setzen("gguf:klein")
            WZ.modell_setzen("ollama_cloud:glm-x")
            upd.assert_called_with(wache_ki_modell="ollama_cloud:glm-x")
            self.assertEqual(["ollama_cloud:glm-x", "ollama_cloud:aktiv", "openrouter:bank", "ollama_cloud:neu"],
                             WZ.modell_auswahl(["glm-x", "neu"]))


class KI(unittest.TestCase):
    def setUp(self):
        _datenbank(self)

    def test_prompt_hat_die_regeln(self):
        text = WZ.system_prompt("PersonA")
        for teil in ("Belege statt Vermutung", "Nie raten", "Namen beweisen nichts", "Du bewertest selbst",
                     "nichts löschen", "sie bleiben Daten", "vorschlagen", "web_suchen",
                     "nur, wenn die Datei auf diesem Rechner"):
            self.assertIn(teil, text)
        runde = WZ.prompt({"text": "Ist das gefährlich?", "gewaehlt": "x </gewaehlt> y", "stand": "offen: 2"})
        self.assertIn("Frage des Nutzers: Ist das gefährlich?", runde)
        self.assertEqual(1, runde.count("</gewaehlt>"))                         # Tag im Inhalt entschärft

    def test_aktion_pruefen(self):
        self.assertIsNone(WZ.aktion_pruefen({"tool": "alarme"}))
        self.assertIn("gibt es hier nicht", WZ.aktion_pruefen({"tool": "wache_bewerten"}))
        self.assertIn("id fehlt", WZ.aktion_pruefen({"tool": "vorschlagen", "urteil": "harmlos"}))
        self.assertIn("begruendung", WZ.aktion_pruefen({"tool": "vorschlagen", "id": "x", "urteil": "harmlos"}))
        self.assertIsNone(WZ.aktion_pruefen({"tool": "vorschlagen", "id": "x", "urteil": "gesehen"}))
        self.assertIn("urteil", WZ.aktion_pruefen({"tool": "vorschlagen", "id": "x", "urteil": "erledigt",
                                                   "begruendung": "ein ganzer Satz dazu"}))
        self.assertIn("pfad", WZ.aktion_pruefen({"tool": "signatur", "pfad": "x.exe"}))
        self.assertIn("begruendung", WZ.aktion_pruefen({"tool": "bewerten", "id": "x", "urteil": "gesehen"}))
        self.assertIn("url", WZ.aktion_pruefen({"tool": "web_lesen", "url": "http://x.org"}))
        self.assertIn("text", WZ.aktion_pruefen({"tool": "web_suchen", "text": "x"}))

    def test_werkzeuge_lesen(self):
        ok, text = WZ.werkzeug({"tool": "alarm", "id": "a00000000001"})
        self.assertTrue(ok)
        self.assertIn("updater_x.exe", text)
        ok, text = WZ.werkzeug({"tool": "alarm", "id": "nix"})
        self.assertFalse(ok)
        ok, text = WZ.werkzeug({"tool": "alarme", "status": "unklar"})
        self.assertIn("Keine Alarme", text)
        ok, text = WZ.werkzeug({"tool": "signatur", "pfad": r"C:\Programme\x.exe"})
        self.assertIn(SIGNATUR, text)

    def test_ki_bewertet_selbst(self):
        sp = zugang._speicher
        ok, text = WZ.werkzeug({"tool": "bewerten", "id": "a00000000001", "urteil": "harmlos",
                                "begruendung": "Gültig signiert von Beispiel GmbH im Programmordner.",
                                "bezeichnung": "Updater von Beispiel"}, WZ.neue_runde(), "PersonA · Wache-KI (m)")
        self.assertTrue(ok, text)
        al = sp.alarm("a00000000001")
        self.assertEqual(("harmlos", "PersonA · Wache-KI (m)"), (al.status, al.urteil_von))
        ok, _ = WZ.werkzeug({"tool": "bewerten", "ids": ["a00000000002"], "urteil": "unpruefbar",
                             "begruendung": "Datei nicht mehr da, Herkunft nicht belegbar."}, WZ.neue_runde(), "KI")
        self.assertTrue(ok)
        self.assertEqual("unpruefbar", sp.alarm("a00000000002").status)

    def test_nach_netz_harmlos_nur_mit_signatur(self):
        import webfetch
        runde = WZ.neue_runde()
        treffer = "1. Beispiel – https://beispiel.org/info\n2. Anderes – https://andere.org/x"
        with mock.patch.object(webfetch, "search_available", return_value=True), \
                mock.patch.object(webfetch, "search", return_value=treffer):
            ok, _ = WZ.werkzeug({"tool": "web_suchen", "text": "updater_x.exe Beispiel"}, runde)
        self.assertTrue(ok and runde["netz"])
        with mock.patch.object(webfetch, "fetch", return_value="Seite: das ist ein Updater.") as fetch:
            ok, text = WZ.werkzeug({"tool": "web_lesen", "url": "https://beispiel.org/info"}, runde)
            self.assertTrue(ok)
            self.assertIn("das ist ein Updater", text)
            ok, text = WZ.werkzeug({"tool": "web_lesen", "url": "https://selbst-gebaut.org/?d=geheim"}, runde)
        self.assertFalse(ok)
        self.assertEqual(1, fetch.call_count)                                   # selbst gebaute Adresse: nicht geladen
        harmlos = {"tool": "bewerten", "urteil": "harmlos", "begruendung": "Laut Recherche ein Updater von Beispiel."}
        with mock.patch.object(herkunft, "signatur_zeile", return_value="NICHT signiert · kein Systemordner"):
            ok, text = WZ.werkzeug({**harmlos, "id": "a00000000002"}, runde, "KI")
        self.assertFalse(ok)
        self.assertIn("gültig signiert", text)
        self.assertEqual("offen", zugang._speicher.alarm("a00000000002").status)
        ok, text = WZ.werkzeug({**harmlos, "id": "a00000000001"}, runde, "KI")   # signiert: geht
        self.assertTrue(ok, text)

    def test_suche_ohne_schluessel(self):
        import webfetch
        with mock.patch.object(webfetch, "search_available", return_value=False):
            ok, text = WZ.werkzeug({"tool": "web_suchen", "text": "irgendwas"})
        self.assertFalse(ok)
        self.assertIn("/web key", text)


class _Bruecke:
    def __init__(self, loop, handlers, **_):
        self.handlers, self.ereignisse = handlers, []

    def senden(self, ev):
        self.ereignisse.append(ev)

    def verbunden(self):
        return True


class Sitzung(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import gui_bruecke

        import main
        self.main = main
        self.sp = _datenbank(self)
        self.antworten = ['```aktion\n{"tool": "vorschlagen", "id": "a00000000001", "urteil": "harmlos", '
                          '"begruendung": "Gültig signiert von Beispiel GmbH im Programmordner.", '
                          '"bezeichnung": "Updater von Beispiel"}\n```',
                          "🟢 harmlos – belegt durch die Signatur."]
        self.fragen = []

        async def ask_once(prompt, system):
            self.fragen.append((prompt, system))
            return self.antworten.pop(0)

        async def cloud_backend(ref, strength=None):
            return NS(ask_once=ask_once)
        for p in (mock.patch.object(gui_bruecke, "GuiBruecke", _Bruecke),
                  mock.patch.object(main, "cloud_backend", side_effect=cloud_backend),
                  mock.patch.object(WZ, "modell", return_value="ollama_cloud:glm-x"),
                  mock.patch.object(main, "wache_karte_auffrischen", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.bruecke = await main.start_wachefenster(NS())

    async def warten(self, art, anzahl=1):
        for _ in range(400):
            if sum(1 for e in self.bruecke.ereignisse if e["t"] == art) >= anzahl:
                return next(e for e in reversed(self.bruecke.ereignisse) if e["t"] == art)
            await asyncio.sleep(0.01)
        self.fail(f"kein {art}: {self.bruecke.ereignisse}")

    async def test_start_liste_und_steckbrief(self):
        self.bruecke.handlers["start"]({})
        liste = await self.warten("liste")
        self.assertEqual(2, len(liste["alarme"]))
        await self.warten("modell")
        self.bruecke.handlers["steckbrief"]({"id": "a00000000002"})
        brief = await self.warten("steckbrief")
        self.assertEqual("a00000000002", brief["alarm"]["id"])

    async def test_ki_bewertet_und_schlaegt_vor(self):
        self.antworten.insert(0, '```aktion\n{"tool": "bewerten", "id": "a00000000002", "urteil": "unpruefbar", '
                                 '"begruendung": "Die Datei liegt im Temp-Ordner und ist nicht mehr da."}\n```')
        self.bruecke.handlers["frage"]({"id": 1, "text": "Ist das ok?", "gewaehlt": "a00000000001"})
        geurteilt = await self.warten("geurteilt")
        self.assertTrue(geurteilt["von_ki"])
        al = self.sp.alarm("a00000000002")
        self.assertEqual("unpruefbar", al.status)                                # die KI bewertet selbst
        self.assertIn("Wache-KI (glm-x)", al.urteil_von)
        vorschlag = await self.warten("vorschlag")
        self.assertEqual(("a00000000001", "harmlos"), (vorschlag["id"], vorschlag["urteil"]))
        antwort = await self.warten("antwort")
        self.assertIn("harmlos", antwort["text"])
        self.assertEqual("offen", self.sp.alarm("a00000000001").status)          # Vorschlag ist kein Urteil
        prompt, system = self.fragen[0]
        self.assertIn("<gewaehlt>", prompt)
        self.assertIn("Du bewertest selbst", system)
        self.bruecke.handlers["urteilen"]({"id": "a00000000001", "urteil": "harmlos",
                                           "grund": vorschlag["grund"], "bezeichnung": vorschlag["bezeichnung"]})
        erg = await self.warten("geurteilt", 2)
        self.assertTrue(erg["ok"], erg)
        self.assertEqual(("harmlos", "Nutzer"), (self.sp.alarm("a00000000001").status,
                                                 self.sp.alarm("a00000000001").urteil_von))

    async def test_loeschen_aus_dem_fenster(self):
        self.bruecke.handlers["loeschen"]({"id": "a00000000002"})
        erg = await self.warten("geloescht")
        self.assertTrue(erg["ok"], erg)
        self.assertIsNone(self.sp.alarm("a00000000002"))
        liste = await self.warten("liste")
        self.assertNotIn("a00000000002", [a["id"] for a in liste["alarme"]])

    async def test_ohne_cloud_modell(self):
        with mock.patch.object(WZ, "modell", return_value=None):
            self.bruecke.handlers["frage"]({"id": 2, "text": "Hallo"})
            fehler = await self.warten("fehler")
        self.assertIn("Cloud-Modell", fehler["text"])


class Fenster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(["test"])

    def setUp(self):
        import wache_fenster as WF
        from PySide6.QtCore import QObject, Signal

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, auftrag):
                self.gesendet.append(auftrag)
        self.WF = WF
        self.lt = Leitung()
        self.f = WF.WacheFenster(self.lt)
        self.addCleanup(self.f.deleteLater)

    def test_ablauf(self):
        f, lt = self.f, self.lt
        self.assertEqual({"op": "start"}, lt.gesendet[0])
        f._ereignis({"t": "modell", "gewaehlt": "", "aktiv": "ollama_cloud:glm-x",
                     "auswahl": ["ollama_cloud:glm-x", "ollama_cloud:anderes"]})
        self.assertEqual(3, f.modell.count())
        f.modell.setCurrentIndex(2)
        f._modell_gewaehlt(2)
        self.assertEqual({"op": "modell", "ref": "ollama_cloud:anderes"}, lt.gesendet[-1])
        alarme = [{"id": "a1", "symbol": "🟠", "zeit": "24.11. 16:07", "regel": "R003", "anzahl": 2,
                   "subjekt": "werkzeug.exe", "titel": "Temp", "status_text": "offen"}]
        f._ereignis({"t": "liste", "alarme": alarme, "zahl": {"offen": 1}})
        self.assertEqual(1, f.liste.count())
        f.liste.setCurrentRow(0)
        self.assertEqual({"op": "steckbrief", "id": "a1"}, lt.gesendet[-1])
        f._ereignis({"t": "steckbrief", "alarm": {"id": "a1", "symbol": "🟠", "titel": "Temp", "regel": "R003",
                                                   "status": "offen", "status_text": "offen",
                                                   "felder": [["Pfad", r"C:\Temp\werkzeug.exe"]],
                                                   "signatur": "NICHT signiert · kein Systemordner",
                                                   "exe": r"C:\Temp\werkzeug.exe"}})
        f.grund.setText("Mein eigenes Werkzeug")
        f._urteilen("gesehen")
        self.assertEqual({"op": "urteilen", "id": "a1", "urteil": "gesehen", "grund": "Mein eigenes Werkzeug",
                          "bezeichnung": ""}, lt.gesendet[-1])
        f._ereignis({"t": "vorschlag", "id": "a1", "urteil": "harmlos", "grund": "Belegt durch X.",
                     "bezeichnung": "Werkzeug"})
        self.assertEqual("harmlos", f.vorschlag["urteil"])
        f._urteilen(f.vorschlag["urteil"], f.vorschlag["grund"], f.vorschlag["bezeichnung"], f.vorschlag["id"])
        self.assertEqual({"op": "urteilen", "id": "a1", "urteil": "harmlos", "grund": "Belegt durch X.",
                          "bezeichnung": "Werkzeug"}, lt.gesendet[-1])
        self.assertEqual({}, f.vorschlag)
        f.nemi.fragen("Was ist das?")
        self.assertEqual(("frage", "a1"), (lt.gesendet[-1]["op"], lt.gesendet[-1]["gewaehlt"]))

    def test_loeschen_mit_rueckfrage(self):
        f, lt = self.f, self.lt
        f._ereignis({"t": "liste", "alarme": [{"id": "a1", "symbol": "🟡", "zeit": "18.11. 11:23", "regel": "R011",
                                              "subjekt": "Aufgabe", "titel": "Neue Aufgabe",
                                              "status_text": "unklar"}], "zahl": {"unpruefbar": 1}})
        f.liste.setCurrentRow(0)
        f._ereignis({"t": "steckbrief", "alarm": {"id": "a1", "symbol": "🟡", "titel": "Neue Aufgabe",
                                                   "regel": "R011", "status": "unpruefbar", "status_text": "unklar"}})
        knopf = next(k for k in f.findChildren(self.WF.Knopf) if "Löschen" in k.text())
        nein, ja = self.WF.QMessageBox.StandardButton.No, self.WF.QMessageBox.StandardButton.Yes
        with mock.patch.object(self.WF.QMessageBox, "question", return_value=nein):
            knopf.click()
        self.assertNotIn("loeschen", [g.get("op") for g in lt.gesendet])        # Nein: nichts passiert
        with mock.patch.object(self.WF.QMessageBox, "question", return_value=ja):
            knopf.click()
        self.assertEqual({"op": "loeschen", "id": "a1"}, lt.gesendet[-1])
        f._ereignis({"t": "geloescht", "id": "a1", "ok": True, "text": "Alarm R011 gelöscht: Aufgabe"})
        self.assertEqual(("", {}), (f.gewaehlt, f.alarm))

    def test_aufgabe_knoepfe(self):
        f, lt = self.f, self.lt
        brief = {"id": "a1", "symbol": "🟡", "titel": "Neue Aufgabe", "regel": "R011", "status": "unpruefbar",
                 "status_text": "unklar", "aufgabe": r"\Test\Aufgabe Eins", "aufgabe_zustand": "Bereit"}
        f.gewaehlt = "a1"
        f._ereignis({"t": "steckbrief", "alarm": brief})

        def knoepfe():
            return [k.text() for k in f.findChildren(self.WF.Knopf) if "Aufgabe" in k.text() and k.isVisibleTo(f)]
        self.assertTrue(any("deaktivieren" in t for t in knoepfe()))
        ja = self.WF.QMessageBox.StandardButton.Yes
        k = next(k for k in f.findChildren(self.WF.Knopf) if "deaktivieren" in k.text())
        with mock.patch.object(self.WF.QMessageBox, "question", return_value=ja):
            k.click()
        self.assertEqual({"op": "aufgabe", "id": "a1", "art": "deaktivieren"}, lt.gesendet[-1])
        f._ereignis({"t": "steckbrief", "alarm": {**brief, "aufgabe_zustand": "Deaktiviert"}})
        self.qt_warten()
        self.assertFalse(any("deaktivieren" in t for t in knoepfe()))           # schon aus: nur noch löschen
        self.assertTrue(any("löschen" in t for t in knoepfe()))
        f._ereignis({"t": "steckbrief", "alarm": {**brief, "aufgabe_zustand": None}})
        self.qt_warten()
        self.assertEqual([], knoepfe())                                         # gibt es nicht mehr

    def qt_warten(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class SymbolKlick(unittest.TestCase):
    """Ein Klick aufs Wache-Symbol legt einen festen Wunsch ab; die Sitzung holt ihn ab und öffnet das Fenster."""

    def setUp(self):
        import kontextmenue as K
        self.K = K
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = mock.patch.object(K, "WACHE_FENSTER_DATEI", Path(tmp) / ".nemicli.wachefenster")
        p.start()
        self.addCleanup(p.stop)

    def test_wunsch_und_abholen(self):
        K = self.K
        with mock.patch.object(K, "nemicli_laeuft", return_value=True), \
                mock.patch.object(K, "fenster_starten") as start:
            self.assertTrue(K.wache_fenster_wuenschen())
        start.assert_not_called()                                                # offenes NemiCLI: kein zweites
        self.assertTrue(K.wache_fenster_abholen())
        self.assertFalse(K.wache_fenster_abholen())                              # nur einmal
        with mock.patch.object(K, "nemicli_laeuft", return_value=False), \
                mock.patch.object(K, "fenster_starten", return_value=True) as start:
            self.assertTrue(K.wache_fenster_wuenschen())
        start.assert_called_once()                                               # sonst NemiCLI starten
        alt = K.WACHE_FENSTER_DATEI
        os.utime(alt, (1, 1))
        self.assertFalse(K.wache_fenster_abholen())                              # zu alt: verworfen
        self.assertFalse(alt.exists())

    def test_linksklick_und_menue(self):
        from PySide6.QtWidgets import QSystemTrayIcon
        from wache import tray
        aktionen = []
        t = tray.Tray(stand=lambda: {}, aktion=aktionen.append, install_ordner=Path("."), berichte=Path("."))
        t._klick(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertEqual(["wache_fenster"], aktionen)

    def test_dienst_schickt_den_wunsch(self):
        import threading
        from wache import dienst
        fertig = threading.Event()
        with mock.patch.object(self.K, "wache_fenster_wuenschen", side_effect=lambda: fertig.set()):
            dienst.Dienst.aktion(NS(), "wache_fenster")
            self.assertTrue(fertig.wait(5))

    def test_sitzung_prueft_den_wunsch(self):
        quelle = (ROOT / "ui" / "screen_tx.py").read_text(encoding="utf-8")
        self.assertIn('K.wache_fenster_abholen()', quelle)
        self.assertIn('self.handler(self.ctx, "/wache fenster")', quelle)


class Verdrahtung(unittest.TestCase):
    def test_start_und_menue(self):
        import commands as C
        import fensterstart
        self.assertIn("--wachefenster", fensterstart._VOR_CHARAKTERE)
        self.assertNotIn("--wachefenster", fensterstart.HINTERGRUND)
        self.assertEqual("fenster", C.auswahl("/wache")[0][0])
        quelle = (ROOT / "main_start.py").read_text(encoding="utf-8")
        self.assertIn('"--wachefenster" in sys.argv', quelle)


if __name__ == "__main__":
    unittest.main()
