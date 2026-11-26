"""Wache: Härtung von Regeln, Vorprüfer-Schloss, Sigma-Vergleich, Aufräumen, Lock und kleinen Helfern."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from wache import ereignisse as E, regeln as R, sigma as SG, speicher as S, vorpruefer as V   # noqa: E402

SIGNIERT = "signiert: Beispiel GmbH · kein Systemordner"


def _start(name, cmd="", exe=None, eltern="explorer.exe", **extra):
    return E.Ereignis(E.PROZESS, "process_start", f"Start {name}", prozess=name, eltern=eltern,
                      exe=exe if exe is not None else rf"C:\Programme\{name}", cmdline=cmd, extra=extra)


class R001NurShell(unittest.TestCase):
    def setUp(self):
        self.r = R.Regelwerk()

    def r001(self, e):
        with mock.patch.object(R.herkunft, "originalname", return_value=""):
            return [a for a in self.r.pruefen(e) if a.regel == "R001"]

    def test_andere_programme_mit_schraegstrich_schaltern(self):
        for name, cmd in (("msiexec.exe", "msiexec /i paket.msi /qn"), ("robocopy.exe", "robocopy a b /E"),
                          ("xcopy.exe", "xcopy a b /e"), ("python.exe", "python -E skript.py"),
                          ("iexplore.exe", "iexplore.exe -nop")):
            self.assertEqual([], self.r001(_start(name, cmd)), cmd)

    def test_shell_und_umbenannte_shell(self):
        self.assertTrue(self.r001(_start("powershell.exe", "powershell -nop -c Get-Date")))
        self.assertTrue(self.r001(_start("pwsh.exe", "pwsh -nop -c iex(x)")))
        umbenannt = _start("helfer.exe", "helfer.exe -nop", exe=r"C:\Users\user\helfer.exe")
        with mock.patch.object(R.herkunft, "originalname", return_value="powershell.exe"):
            self.assertTrue([a for a in self.r.pruefen(umbenannt) if a.regel == "R001"])

    def test_iex_nur_als_ganzes_wort(self):
        self.assertIn("iex", R._flags_in("powershell iex(Get-Date)"))
        self.assertIn("iex", R._flags_in("powershell $a|iex"))
        self.assertNotIn("iex", R._flags_in("powershell Start-Process msiexec.exe"))

    def test_muster_zur_laufzeit_wie_vorher(self):
        kurz = "-" + R._KODIER[:3]
        self.assertEqual([kurz, f"-w {R._VERSTECKT}"], R._flags_in(f"powershell {kurz} AAAA -w {R._VERSTECKT}"))
        self.assertIn("-" + R._KODIER, R._VERDAECHTIGE_FLAGS)


class EigenerElternort(unittest.TestCase):
    RAHMEN = "powershell -NoProfile -Command " + R.EIGENER_RAHMEN + "; Get-Date"

    def test_eltern_aus_ablageort_ist_nicht_eigen(self):
        e = _start("powershell.exe", self.RAHMEN, eltern="python.exe",
                   eltern_exe=r"C:\Users\user\AppData\Local\Temp\x\python.exe")
        self.assertFalse(R.eigener_aufruf(e))

    def test_eigener_interpreter_und_ohne_pfad(self):
        e = _start("powershell.exe", self.RAHMEN, eltern="python.exe", eltern_exe=sys.executable)
        self.assertTrue(R.eigener_aufruf(e))
        self.assertTrue(R.eigener_aufruf(_start("powershell.exe", self.RAHMEN, eltern="python.exe")))
        e = _start("powershell.exe", self.RAHMEN, eltern="nemicli.exe", eltern_exe=r"D:\Programme\NemiCLI\NemiCLI.exe")
        self.assertTrue(R.eigener_aufruf(e))                                   # andere Installation


class NachgeholteStarts(unittest.TestCase):
    def test_keine_flut_aus_der_pause(self):
        r = R.Regelwerk()
        alarme = []
        for i in range(40):
            alarme += r.pruefen(_start(f"p{i}.exe", eltern="dienst.exe", nachgeholt=True))
        self.assertNotIn("R004", {a.regel for a in alarme})


class Schloss(unittest.TestCase):
    def text(self, **felder):
        f = {"regel": "R008", "schwere": 2, "score": 0.0, "anzahl": 1, "titel": "Viele Verbindungen", "text": "x",
             "prozess": "beispiel.exe", "exe": r"C:\Programme\beispiel.exe", "herkunft": SIGNIERT}
        f.update(felder)
        return V.eingabe(f)

    def test_skript_hosts_immer_gesperrt(self):
        for name in ("rundll32.exe", "powershell.exe", "regsvr32.exe", "certutil.exe", "mshta.exe"):
            self.assertTrue(V.schloss(self.text(prozess=name, exe=rf"C:\Windows\System32\{name}")), name)

    def test_dll_in_nutzerordner(self):
        cmd = r"rundll32.exe C:\Users\<nutzer>\AppData\Local\x\beacon.dll,Start"
        self.assertEqual("Skript aus Download-, Temp- oder AppData-Ordner", V.schloss(self.text(cmdline=cmd)))

    def test_umbenanntes_werkzeug_ueber_versionsdaten(self):
        with mock.patch("wache.herkunft.originalname", return_value="rundll32.exe"):
            self.assertTrue(V.schloss(self.text(), r"C:\Programme\beispiel.exe"))
        self.assertEqual("", V.schloss(self.text()))

    def test_modell_hash_einmal_je_stand(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.safetensors").write_bytes(b"kein modell")
            V._hash_geprueft.clear()
            self.assertFalse(V.modell_echt(Path(tmp)))
            with mock.patch("hashlib.sha256") as h:
                self.assertFalse(V.modell_echt(Path(tmp)))                     # gemerkt, nicht neu gerechnet
                h.assert_not_called()
            self.assertFalse(V.modell_echt(None))


class SigmaWerte(unittest.TestCase):
    def regel(self, auswahl):
        return SG.regel_aus({"title": "T", "id": "t", "logsource": {"category": "process_creation"},
                             "detection": {"a": auswahl, "condition": "a"}})

    def test_klammern_wortlich_und_escapes(self):
        r = self.regel({"CommandLine|contains": "[System.Text.Encoding]::"})
        self.assertTrue(SG.trifft(r, _start("p.exe", "x [System.Text.Encoding]::UTF8")))
        r = self.regel({"CommandLine|contains": r"a\*b"})
        self.assertTrue(SG.trifft(r, _start("p.exe", "x a*b y")))
        self.assertFalse(SG.trifft(r, _start("p.exe", "x aXXb y")))
        r = self.regel({"CommandLine": "a*b?"})
        self.assertTrue(SG.trifft(r, _start("p.exe", "A123bZ")))

    def test_re_unterscheidet_gross_klein(self):
        r = self.regel({"CommandLine|re": "Invoke-[A-Z]"})
        self.assertTrue(SG.trifft(r, _start("p.exe", "Invoke-X")))
        self.assertFalse(SG.trifft(r, _start("p.exe", "invoke-x")))
        r = self.regel({"CommandLine|re|i": "invoke-x"})
        self.assertTrue(SG.trifft(r, _start("p.exe", "INVOKE-X")))
        with self.assertRaises(SG.Unbekannt):
            self.regel({"CommandLine|contains|i": "x"})


class Aufraeumen(unittest.TestCase):
    def test_unbeurteilte_bleiben_und_wiederholung_zaehlt(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = S.Speicher(Path(tmp) / "w.db")
            try:
                alt = time.time() - 40 * 86400
                evs = {s: E.Ereignis(E.PROZESS, "process_start", s, zeit=alt, prozess=f"{s}.exe")
                       for s in ("offen", "gesehen", "frei")}
                sp.ereignisse_schreiben(list(evs.values()))
                a_offen = E.Alarm("t", "x", E.Schwere.MITTEL, "R003", ereignis_id=evs["offen"].id, zeit=alt)
                a_ges = E.Alarm("t", "x", E.Schwere.MITTEL, "R003", ereignis_id=evs["gesehen"].id, zeit=alt,
                                status="gesehen")
                sp.alarme_schreiben([a_offen, a_ges])
                sp.alarm_zaehlen(a_ges.id, time.time())                          # wiederholt sich noch
                sp.aufraeumen(30, 1_000_000)
                self.assertIsNotNone(sp.ereignis(evs["offen"].id))
                self.assertIsNotNone(sp.ereignis(evs["gesehen"].id))
                self.assertIsNone(sp.ereignis(evs["frei"].id))
                self.assertIsNotNone(sp.alarm(a_ges.id))
            finally:
                sp.schliessen()


class Lock(unittest.TestCase):
    def test_exklusiv_und_leiche(self):
        from wache import zugang as Z
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "wache.lock"
            with mock.patch.object(Z, "LOCK", lock), mock.patch.object(Z, "ordner_anlegen"):
                self.assertTrue(Z.lock_beanspruchen())
                self.assertEqual(os.getpid(), json.loads(lock.read_text(encoding="utf-8"))["pid"])
                fremd = {"pid": os.getpid(), "start": 1.0}       # Prozess jünger als der Eintrag: PID neu vergeben
                lock.write_text(json.dumps(fremd), encoding="utf-8")
                self.assertTrue(Z.lock_beanspruchen())
                with mock.patch.object(Z, "_lock_lesen", return_value={"pid": os.getpid() + 1}):
                    self.assertFalse(Z.lock_beanspruchen())


class Kleinkram(unittest.TestCase):
    def test_taegliche_erinnerung_haelt_die_uhrzeit(self):
        from wache import erinnerungen as ER
        zeit = datetime(2026, 3, 27, 8, 0).timestamp()
        jetzt = datetime(2026, 4, 2, 9, 0).timestamp()
        neu = ER.naechstes_mal(zeit, 86400, jetzt)
        self.assertEqual((8, 0), (datetime.fromtimestamp(neu).hour, datetime.fromtimestamp(neu).minute))
        self.assertEqual(datetime(2026, 4, 3, 8, 0).timestamp(), neu)
        self.assertEqual(zeit + 4 * 3600, ER.naechstes_mal(zeit, 3600, zeit + 3.5 * 3600))

    def test_lnk_auf_shell_zaehlt_das_skript(self):
        from wache import systemstand as SY
        ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        self.assertEqual(r"C:\Users\user\start.ps1",
                         SY.verknuepfung_ziel(ps, r"-File C:\Users\user\start.ps1"))
        self.assertEqual(r"C:\Programme\x.exe", SY.verknuepfung_ziel(r"C:\Programme\x.exe", "-a b"))
        self.assertEqual(r"C:\Users\user\a.ps1", SY.programmpfad(rf"{ps} -File C:\Users\user\a.ps1"))

    def test_netzziele_kuerzen_beim_aeltesten(self):
        from wache.netzwaechter import NetzMerkmale
        m = NetzMerkmale()
        m.anpassen([E.Ereignis(E.NETZ, "conn_open", "x", zeit=100.0 + i, prozess=p, ziel="192.0.2.1")
                    for i, p in enumerate(("zeta.exe", "alpha.exe"))])
        self.assertEqual(["zeta.exe|192.0.2.1", "alpha.exe|192.0.2.1"], m.zustand()["ziele"])

    def test_weiterleitung_nur_https(self):
        import urllib.error
        import urllib.request
        from wache import netzauskunft as NA
        req = urllib.request.Request("https://rdap.arin.net/registry/ip/192.0.2.1")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            NA._NurRegister().redirect_request(req, None, 301, "Moved", {}, "http://rdap.db.ripe.net/ip/192.0.2.1")
        cm.exception.close()

    def test_sofortcheck_wurzel_prozent_und_zeile(self):
        import sofortcheck
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mein bild.png").write_bytes(b"")
            html = '<html><body><img src="mein%20bild.png"><script src="/app.js"></script></body></html>'
            self.assertEqual("", sofortcheck.pruefen(Path(tmp) / "i.html", html))
        funde = sofortcheck.pruefen("x.js", "let a = 'offen\nlet b = (\n")
        self.assertIn("Zeile 2: '(' wird nicht geschlossen", funde)

    def test_schutzsoftware_kanal_gesperrt(self):
        import systemabfrage as SA
        with mock.patch("schutzsoftware.ordner", return_value=[Path(r"C:\Programme\Beispielschutz")]):
            with self.assertRaises(SA.AbfrageFehler):
                SA._kanal_pruefen("Beispielschutz/Operational")
            SA._kanal_pruefen("System")


if __name__ == "__main__":
    unittest.main()
