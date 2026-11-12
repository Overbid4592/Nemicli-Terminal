"""Netzwächter: Netzspur, Auskunft (DNS-Speicher, Register), Netz-Modell (ML), Netz-Folgen (DL), Motor, Werkzeug."""

import random
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

from wache import detektor as D, ereignisse as E, netzauskunft as NA, netzspur as NS  # noqa: E402
from wache import netzwaechter as NW, speicher as S  # noqa: E402

JETZT = time.time()
ANKER = __import__("datetime").datetime(2026, 6, 15, 14, 30).timestamp()   # fester Werktag-Nachmittag

IPCONFIG = """
Windows-IP-Konfiguration

    ollama.com
    ----------------------------------------
    Eintragsname . . . . . : ollama.com
    Eintragstyp. . . . . . : 1
    Gültigkeitsdauer . . . : 120
    Datenlänge . . . . . . : 4
    Abschnitt. . . . . . . : Antwort
    (Host-)A-Eintrag . . . : 34.36.133.15

    www.beispiel.de
    ----------------------------------------
    Eintragsname . . . . . : www.beispiel.de
    Eintragstyp. . . . . . : 5
    Abschnitt. . . . . . . : Antwort
    CNAME-Eintrag  . . . . : beispiel.cdn.example.net

    Eintragsname . . . . . : beispiel.cdn.example.net
    Eintragstyp. . . . . . : 1
    Abschnitt. . . . . . . : Antwort
    (Host-)A-Eintrag . . . : 11.22.33.44

    v6.example.org
    ----------------------------------------
    Eintragsname . . . . . : v6.example.org
    AAAA-Eintrag . . . . . : 2001:db8::1

    15.133.36.34.in-addr.arpa
    ----------------------------------------
    Eintragsname . . . . . : 15.133.36.34.in-addr.arpa
    PTR-Eintrag  . . . . . : irgendwas.googleusercontent.com
"""

RDAP = {"name": "GOOGL-2", "handle": "NET-34-4-5-0-1", "startAddress": "34.4.5.0", "endAddress": "34.63.255.255",
        "entities": [{"roles": ["abuse"], "vcardArray": ["vcard", [["fn", {}, "text", "GC Abuse"]]]},
                     {"roles": ["registrant"], "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                                                        ["fn", {}, "text", "Google LLC"]]]}]}


def verbindung(prozess, ziel, name="", port=443, ts=JETZT, pid=0, start=0.0, extern=True, **extra):
    x = {"extern": extern, **extra}
    if name:
        x["name"] = name
    return E.Ereignis(E.NETZ, "conn_open", f"Verbindung: {prozess} → {ziel}:{port}", zeit=ts, prozess=prozess,
                      pid=pid, start=start, exe=rf"C:\P\{prozess}", ziel=ziel, zielport=port, extra=x)


def _ordner(test: unittest.TestCase) -> str:
    """Temp-Ordner, der erst NACH dem Schließen der Datenbank gelöscht wird (Cleanups laufen rückwärts)."""
    import shutil
    t = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, t, True)
    return t


class NetzspurTests(unittest.TestCase):
    def test_merken_entdoppeln_lesen(self):
        with tempfile.TemporaryDirectory() as t:
            datei = Path(t) / "eigene.jsonl"
            antwort = [(2, 1, 6, "", ("34.36.133.15", 443))]
            with patch.object(NS, "_zuletzt", {}):
                self.assertTrue(NS.merken("Ollama.com", antwort, datei))
                self.assertFalse(NS.merken("ollama.com", antwort, datei))          # innerhalb von 10 min
                self.assertFalse(NS.merken("127.0.0.1", antwort, datei))           # keine Namen, nur Adressen
                self.assertFalse(NS.merken("localhost", antwort, datei))
                self.assertTrue(NS.merken(b"api.anthropic.com", [(2, 1, 6, "", ("160.79.104.10", 443))], datei))
            eintraege = NS.lesen(datei)
            self.assertEqual(["ollama.com", "api.anthropic.com"], [e["name"] for e in eintraege])
            self.assertEqual(["34.36.133.15"], eintraege[0]["ips"])

    def test_haken_stoert_die_verbindung_nie(self):
        def kaputt(*a, **k):
            raise OSError("Platte voll")
        alt = socket.getaddrinfo
        echt = [(2, 1, 6, "", ("11.22.33.44", 443))]
        with patch.object(NS, "_original", None), patch.object(socket, "getaddrinfo", lambda *a, **k: echt), \
                patch.object(NS, "merken", kaputt):
            self.assertTrue(NS.installieren("Test"))
            self.assertFalse(NS.installieren("Test"))                              # nur einmal
            self.assertEqual(echt, socket.getaddrinfo("beispiel.de", 443))
        self.assertIs(alt, socket.getaddrinfo)


class AuskunftTests(unittest.TestCase):
    def test_dns_speicher_parsen(self):
        karte = NA.dns_speicher_parsen(IPCONFIG)
        self.assertEqual("ollama.com", karte["34.36.133.15"])
        self.assertEqual("www.beispiel.de", karte["11.22.33.44"])                # über die CNAME-Kette hinweg
        self.assertEqual("v6.example.org", karte["2001:db8::1"])
        self.assertNotIn("irgendwas.googleusercontent.com", karte.values())    # PTR-Abschnitte zählen nicht

    def test_domain_zweck_oeffentlich(self):
        self.assertEqual("hf.co", NA.registriert("cdn-lfs.hf.co"))
        self.assertEqual("bbc.co.uk", NA.registriert("www.bbc.co.uk"))
        self.assertEqual("ollama.com", NA.registriert("ollama.com"))
        self.assertIn("Web-Suche", NA.zweck("ollama.com"))
        self.assertIn("Hugging Face", NA.zweck("cdn-lfs.huggingface.co"))
        self.assertEqual("", NA.zweck("boese-ollama.com"))                       # nur ganze Namensteile
        self.assertTrue(NA.oeffentlich("34.36.133.15"))
        self.assertFalse(NA.oeffentlich("192.168.1.5"))

    def test_rdap_lesen_und_nur_register(self):
        d = NA.rdap_lesen(RDAP)
        self.assertEqual(("34.4.5.0", "34.63.255.255", "GOOGL-2", "Google LLC"), (d["von"], d["bis"], d["netz"], d["org"]))
        handler = NA._NurRegister()
        import urllib.error
        import urllib.request
        req = urllib.request.Request("https://rdap.arin.net/registry/ip/1.2.3.4")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(req, None, 301, "Moved", {}, "https://fremd.example/ip/1.2.3.4")
        self.assertIsNotNone(handler.redirect_request(req, None, 301, "Moved", {},
                                                      "https://rdap.db.ripe.net/ip/1.2.3.4"))
        self.assertIsNone(NA.rdap_abfragen("10.0.0.1"))                          # private Adressen nie fragen

    def test_bericht(self):
        t = _ordner(self)
        sp = S.Speicher(Path(t) / "w.db")
        self.addCleanup(sp.schliessen)
        sp.ereignisse_schreiben([verbindung("NemiCLI.exe", "34.36.133.15", "ollama.com"),
                                 verbindung("chrome.exe", "34.36.133.15", "ollama.com", ts=JETZT - 60)])
        sp.besitzer_merken(NA.rdap_lesen(RDAP))
        datei = Path(t) / "eigene.jsonl"
        datei.write_text('{"zeit": 1, "name": "ollama.com", "ips": ["34.36.133.15"], "programm": "NemiCLI.exe"}\n',
                         encoding="utf-8")
        a = NA.Auskunft(sp, register=False)
        a.dns.gelesen = time.time() + 3600                                    # kein ipconfig im Test
        with patch.object(NS, "DATEI", datei):
            b = a.bericht("34.36.133.15")
        self.assertTrue(b["eigen"])
        self.assertEqual("Google LLC", b["besitzer"]["org"])
        text = NA.bericht_text(b)
        for teil in ("ollama.com", "NemiCLI selbst", "Web-Suche", "Google LLC", "chrome.exe", "NemiCLI.exe"):
            self.assertIn(teil, text)
        # ohne eigene Auflösung: Name aus dem Ereignis-Stempel
        with patch.object(NS, "DATEI", Path(t) / "fehlt.jsonl"):
            a2 = NA.Auskunft(sp, register=False)
            a2.dns.gelesen = time.time() + 3600
            b2 = a2.bericht("34.36.133.15")
        self.assertFalse(b2["eigen"])
        self.assertEqual("ollama.com", b2["name"])
        self.assertIn("unbekannt", NA.bericht_text(a2.bericht("55.66.77.88")))


class NetzModellTests(unittest.TestCase):
    def _alltag(self, n=300):
        rnd = random.Random(1)
        progs = {"chrome.exe": ["google.com", "gstatic.com", "youtube.com"], "steam.exe": ["steampowered.com"],
                 "NemiCLI.exe": ["ollama.com", "anthropic.com"]}
        out = []
        for i in range(n):
            p = rnd.choice(list(progs))
            dom = rnd.choice(progs[p])
            out.append(verbindung(p, f"11.22.{rnd.randint(0, 3)}.{rnd.randint(1, 20)}", "www." + dom,
                                  ts=ANKER + i * 20, ziele_min=rnd.randint(1, 4)))
        return out

    def test_neue_domain_faellt_auf(self):
        with tempfile.TemporaryDirectory() as t:
            d = D.Detektor(Path(t) / "netz.json", NW.NetzMerkmale(), label="Netz")
            self.assertTrue(d.trainieren(self._alltag())["ok"])
            alltag = self._alltag()
            normal, _ = d.bewerten(alltag[10])                              # ein gelerntes Alltags-Ereignis
            fremd, gruende = d.bewerten(verbindung("chrome.exe", "55.66.77.88", "", port=4444, ts=ANKER + 600,
                                                   ziele_min=25))
            self.assertGreater(fremd, normal)
            self.assertTrue(any("ohne Namen" in g or "auffälliger Zielport" in g or "nie gesehen" in g for g in gruende))
            v = d.merkmale.vektor(verbindung("steam.exe", "11.22.1.1", "api.unbekannt.xyz"))
            idx = {n: i for i, n in enumerate(d.merkmale.namen)}
            self.assertEqual(1.0, v[idx["domain_neu_fuer_prozess"]])
            self.assertEqual(1.0, v[idx["domain_neu"]])
            d2 = D.Detektor(Path(t) / "netz.json", NW.NetzMerkmale(), label="Netz")   # Zustand kommt mit
            self.assertEqual(d.merkmale.paare, d2.merkmale.paare)

    def test_ziele_pro_minute_und_domain(self):
        z = NW.ZieleProMinute()
        self.assertEqual(1, z.beobachten("a.exe", "1.1.1.1", 0))
        self.assertEqual(1, z.beobachten("a.exe", "1.1.1.1", 10))
        self.assertEqual(2, z.beobachten("a.exe", "2.2.2.2", 20))
        self.assertEqual(1, z.beobachten("a.exe", "3.3.3.3", 200))               # Fenster 60 s
        self.assertEqual("ip", NW.domain_von(verbindung("x", "55.66.77.88")))
        self.assertEqual("intern", NW.domain_von(verbindung("x", "10.0.0.2", extern=False)))
        self.assertEqual("hf.co", NW.domain_von(verbindung("x", "1.1.1.1", "cdn-lfs.hf.co")))


class NetzFolgenTests(unittest.TestCase):
    def test_takt_und_ketten(self):
        self.assertEqual(["T:0", "T:1", "T:2", "T:3", "T:4", "T:5"], [NW.takt(s) for s in (0, 5, 30, 120, 900, 9999)])
        k = NW.NetzKetten()
        s1 = k.schritte(verbindung("a.exe", "1.1.1.1", "x.google.com", pid=5, start=100.0, ts=1000))
        self.assertEqual(["^", "P:a.exe", "D:google.com"], s1[0].zeichen)
        s2 = k.schritte(verbindung("a.exe", "1.1.1.1", "x.google.com", pid=5, start=100.0, ts=1030))
        self.assertEqual(["T:2", "D:google.com"], s2[0].zeichen)
        ende = k.schritte(E.Ereignis(E.PROZESS, "process_stop", "weg", pid=5, start=100.0))
        self.assertTrue(ende[0].ende)

    def test_lernt_ablauf_und_erkennt_fremden_takt(self):
        rnd = random.Random(3)
        ereignisse = []
        for i in range(160):                                                     # 160 Browser-Sitzungen
            pid, start, ts = 1000 + i, 5000.0 + i, JETZT - 3 * 86400 + i * 600
            for j, dom in enumerate(("google.com", "gstatic.com", "youtube.com", "google.com")):
                ereignisse.append(verbindung("chrome.exe", "11.22.33.1", "www." + dom, pid=pid, start=start,
                                             ts=ts + j * rnd.uniform(2, 8)))
        with tempfile.TemporaryDirectory() as t:
            f = NW.NetzFolgen(Path(t) / "netzfolgen")
            r = f.trainieren(ereignisse, epochen=8)
            self.assertTrue(r["ok"], r)
            self.assertTrue((Path(t) / "netzfolgen" / "v0001.json").exists())
            normal = [f.bewerten(verbindung("chrome.exe", "11.22.33.1", "www." + d, pid=9, start=9.0, ts=100 + k * 4))[0]
                      for k, d in enumerate(("google.com", "gstatic.com", "youtube.com"))]
            fremd = [f.bewerten(verbindung("chrome.exe", "55.66.77.88", "", pid=9, start=9.0, ts=200 + k * 60))[0]
                     for k in range(3)]
            self.assertGreater(max(fremd), max(normal))
            self.assertTrue(f.ist_auffaellig(max(fremd)))
            f2 = NW.NetzFolgen(Path(t) / "netzfolgen")                         # Version wird geladen
            self.assertTrue(f2.trainiert and f2.version == "v0001")
            self.assertEqual("netz_schatten.log", f2.SCHATTEN)


class MotorNetzTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from wache import justierung as J
        from wache import motor as MO, vollscan as VS
        ordner = Path(self.tmp.name)
        for ziel, wert in ((MO, ("MODELL_ORDNER", ordner / "modelle")), (VS, ("DB", ordner / "vollscan.db"))):
            p = patch.object(ziel, *wert)
            p.start()
            self.addCleanup(p.stop)
        self.e = dict(J.STANDARD)
        self.e["grenzen"] = dict(J.STANDARD["grenzen"])
        self.e["ml"] = False
        self.m = MO.Motor(ordner / "w.db", self.e)
        self.addCleanup(self.m.speicher.schliessen)
        self.addCleanup(self.m.baseline.schliessen)
        self.m.auskunft.name = lambda ip: "ollama.com" if ip == "34.36.133.15" else ""

    def test_anreichern_stempelt_name_und_ziele(self):
        e = verbindung("NemiCLI.exe", "34.36.133.15")
        e.extra.pop("name", None)
        self.m._anreichern(e)
        self.assertEqual("ollama.com", e.extra["name"])
        self.assertEqual(1, e.extra["ziele_min"])
        self.assertIn("34.36.133.15", self.m._besitzer_offen)                   # Besitzer wird nachgefragt
        privat = verbindung("x.exe", "10.0.0.5", extern=False)
        self.m._anreichern(privat)
        self.assertNotIn("name", privat.extra)
        self.assertNotIn("10.0.0.5", self.m._besitzer_offen)

    def test_schattenmodus_und_scharf(self):
        alltag = NetzModellTests()._alltag()
        self.m.netz_detektor.trainieren(alltag)
        self.m.netz_detektor.schwelle = 0.0                                     # jede Verbindung ist auffällig
        e = verbindung("chrome.exe", "55.66.77.88", port=4444)
        alarme: list = []
        self.m._netz_bewerten(e, alarme)
        self.assertEqual([], alarme)                                            # Schatten: kein Alarm
        self.assertTrue(e.extra["netz_fund"])
        self.assertIn("chrome.exe", "".join(self.m.netz_schatten.lesen()))
        self.m.speicher.ereignisse_schreiben([e])
        self.assertNotIn(e.id, {x.id for x in self.m.speicher.lern_ereignisse(JETZT - 10, JETZT + 10)})
        self.m.e["netz_alarm"] = True
        e2 = verbindung("chrome.exe", "55.66.77.89", port=4444)
        self.m._netz_bewerten(e2, alarme)
        self.assertEqual(["ML-002"], [a.regel for a in alarme])
        self.assertEqual("netz", alarme[0].quelle)
        self.assertEqual("chrome.exe → ip", alarme[0].subjekt)
        self.assertIn("netz", self.m.stand())

    def test_training_mit_netz_modell(self):
        alt = [verbindung(f"p{i % 4}.exe", f"11.22.33.{i % 9}", f"www.d{i % 5}.com", ts=JETZT - 2 * 86400 + i)
               for i in range(250)]
        self.m.speicher.ereignisse_schreiben(alt)
        r = self.m.trainieren()
        self.assertTrue(r["netz"]["ok"], r["netz"])
        self.assertTrue(self.m.netz_detektor.trainiert)


class WerkzeugTests(unittest.TestCase):
    def test_netz_auskunft_und_uebersicht(self):
        from wache import werkzeuge as W, zugang
        t = _ordner(self)
        sp = S.Speicher(Path(t) / "w.db")
        self.addCleanup(sp.schliessen)
        sp.ereignisse_schreiben([verbindung("NemiCLI.exe", "34.36.133.15", "ollama.com", netz_ml=0.2)])
        with patch.object(zugang, "motor", None), patch.object(zugang, "_speicher", sp), \
                patch.object(NS, "DATEI", Path(t) / "fehlt.jsonl"), \
                patch.object(NA.DnsSpeicher, "lesen", lambda self: {}):
            meldung = ("Die Anwendung NemiCLIc.exe hat versucht eine Internetverbindung herzustellen: "
                       "Ziel: 34.36.133.15/0 Protokoll: TCP Port: HTTPS")
            with patch.object(NA, "rdap_abfragen", lambda ip: None):
                text = W.netz({"text": meldung})
                self.assertIn("ollama.com", text)
                self.assertIn("Web-Suche", text)
                self.assertIn("Keine gültige IP", W.netz({"text": "nichts drin"}))
                uebersicht = W.netz({})
            self.assertIn("NemiCLI.exe → ollama.com", uebersicht)
            self.assertIn("🟢", uebersicht)

    def test_verdrahtung(self):
        import actions, modes
        self.assertIn("wache_netz", actions.ACTIONS)
        self.assertIn("wache_netz", modes.READ_TOOLS)
        self.assertIn("Wache", actions.describe({"tool": "wache_netz", "ip": "1.2.3.4"}))
        from wache import regeln, werkzeuge as W
        self.assertTrue({"ML-002", "DL-002"} <= regeln.MENGENREGELN)
        self.assertIn("ML-002", W.regeln_text())
        self.assertEqual(["34.36.133.15"], W._adressen("Ziel: 34.36.133.15/0, um 22:41:00"))


if __name__ == "__main__":
    unittest.main()
