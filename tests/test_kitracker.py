"""KI-Tracker der Wache: KIs erkennen, Taten aus ihren Protokollen, Regeln R019–R023 (nur synthetische Daten)."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from wache import kitracker as K                         # noqa: E402
from wache.ereignisse import KI, NETZ, PROZESS, Ereignis  # noqa: E402
from wache.justierung import NIE_STUMM_KI                 # noqa: E402
from wache.regeln import KI_LOESCHEN_AB, Regelwerk        # noqa: E402

# Zur Laufzeit zusammengesetzt (wie in kitracker.py)
BROWSER_DATEI = "C:\\Users\\user\\AppData\\Local\\Browser\\User Data\\Default\\Lo" + "gin Data"
SCHLUESSEL = "C:\\Users\\user\\.s" + "sh\\id_test"


def claude_zeile(werkzeug, eingabe, zeit="2026-11-17T18:00:00.000Z", neben=False):
    return json.dumps({"type": "assistant", "timestamp": zeit, "cwd": "C:\\Projekte\\demo", "sessionId": "abc",
                       "isSidechain": neben, "message": {"content": [
                           {"type": "text", "text": "Geheimer Gesprächstext"},
                           {"type": "tool_use", "name": werkzeug, "input": eingabe}]}}) + "\n"


class Erkennen(unittest.TestCase):
    def test_programme_und_dienste(self):
        self.assertEqual(K.programm_ki("claude.exe"), "Claude")
        self.assertEqual(K.programm_ki("", "C:\\x\\Codex.exe"), "Codex")
        self.assertEqual(K.programm_ki("notepad.exe"), "")
        self.assertEqual(K.dienst_ki("api.anthropic.com"), "Anthropic")
        self.assertEqual(K.dienst_ki("eu.api.openai.com"), "OpenAI")
        self.assertEqual(K.dienst_ki("anthropic.com"), "")             # Webseite, keine Schnittstelle
        self.assertEqual(K.dienst_ki("example.com"), "")

    def test_baum(self):
        b = K.KiBaum(eigen_pid=99)
        self.assertTrue(b.wurzel(10, 1.0, "Claude"))
        kind = Ereignis(PROZESS, "process_start", "x", prozess="git.exe", pid=11, ppid=10, start=2.0,
                        eltern_start=1.0)
        self.assertIsNone(b.markieren(kind))
        self.assertEqual(kind.extra["ki"], "Claude")
        enkel = Ereignis(PROZESS, "process_start", "x", prozess="bash.exe", pid=12, ppid=11, start=3.0,
                         eltern_start=2.0)
        b.markieren(enkel)
        self.assertEqual(enkel.extra["ki"], "Claude")
        fremd = Ereignis(PROZESS, "process_start", "x", prozess="calc.exe", pid=13, ppid=5, start=4.0)
        b.markieren(fremd)
        self.assertNotIn("ki", fremd.extra)
        eigen_kind = Ereignis(PROZESS, "process_start", "x", prozess="ipconfig.exe", pid=14, ppid=99, start=5.0)
        b.markieren(eigen_kind)
        self.assertNotIn("ki", eigen_kind.extra)                     # die Wache selbst zählt nie
        b.markieren(Ereignis(PROZESS, "process_stop", "x", pid=11, start=2.0))
        self.assertEqual(b.ki_von(11, 2.0), "")

    def test_ki_am_gespraech_erkannt_browser_nicht(self):
        b = K.KiBaum(eigen_pid=99)
        with mock.patch.object(K.KiBaum, "_startzeit", return_value=7.0):
            netz = Ereignis(NETZ, "conn_open", "x", prozess="agent.exe", pid=20, ziel="203.0.113.5",
                            extra={"name": "api.openai.com"})
            neu = b.markieren(netz)
            self.assertEqual((neu.kategorie, neu.aktion, neu.prozess), (KI, "ki_erkannt", "agent"))
            self.assertEqual(netz.extra["ki"], "agent")
            browser = Ereignis(NETZ, "conn_open", "x", prozess="chrome.exe", pid=21,
                               extra={"name": "api.openai.com"})
            self.assertIsNone(b.markieren(browser))
            self.assertNotIn("ki", browser.extra)


class Protokolle(unittest.TestCase):
    def test_claude_nur_taten(self):
        e = K.claude_taten(claude_zeile("Edit", {"file_path": "C:\\Projekte\\demo\\a.py", "old_string": "alt",
                                                 "new_string": "NEUER INHALT"}))[0]
        self.assertEqual((e.kategorie, e.aktion, e.prozess, e.datei), (KI, "ki_tat", "Claude Code",
                                                                       "C:\\Projekte\\demo\\a.py"))
        self.assertEqual(e.extra["art"], "schreiben")
        self.assertEqual(e.extra["ordner"], "C:\\Projekte\\demo")
        alles = json.dumps(e.zeile(), ensure_ascii=False)
        self.assertNotIn("NEUER INHALT", alles)
        self.assertNotIn("Geheimer Gesprächstext", alles)
        b = K.claude_taten(claude_zeile("Bash", {"command": "git status", "description": "Stand"}, neben=True))[0]
        self.assertEqual((b.cmdline, b.extra["art"], b.extra["nebenagent"]), ("git status", "befehl", True))
        w = K.claude_taten(claude_zeile("WebFetch", {"url": "https://example.com/x", "prompt": "frag"}))[0]
        self.assertEqual((w.ziel, w.extra["art"]), ("https://example.com/x", "web"))
        self.assertAlmostEqual(w.zeit, 1794938400.0, delta=1)
        self.assertEqual(K.claude_taten('{"type": "user"}'), [])
        self.assertEqual(K.claude_taten("kaputt"), [])

    def test_codex(self):
        z = json.dumps({"timestamp": "2026-11-17T18:00:00Z", "type": "response_item", "payload": {
            "type": "function_call", "name": "shell",
            "arguments": json.dumps({"command": ["bash", "-lc", "ls -la"], "workdir": "/w"})}})
        e = K.codex_taten(z)[0]
        self.assertEqual((e.prozess, e.cmdline, e.extra["ordner"], e.extra["art"]),
                         ("Codex", "bash -lc ls -la", "/w", "befehl"))
        p = json.dumps({"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                        "input": "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch"}})
        e = K.codex_taten(p)[0]
        self.assertEqual((e.datei, e.extra["art"]), ("src/a.py", "schreiben"))
        self.assertEqual(K.codex_taten(json.dumps({"type": "message"})), [])

    def test_nemicli(self):
        z = "2026-11-17 18:40:33 | liest  | datei_lesen        | ausgeführt     | Prinz    | Datei lesen: C:\\x\\a.md → Ergebnis: ok\n"
        e = K.nemi_taten(z)[0]
        self.assertEqual((e.prozess, e.datei, e.extra["art"], e.extra["status"]),
                         ("NemiCLI · Prinz", "C:\\x\\a.md", "lesen", "ausgeführt"))
        self.assertEqual(K.nemi_taten("2026-11-17 18:40:33 | liest  | wache | ausgeführt | - | Wache gestartet"), [])


class Mitlesen(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ordner = Path(tmp.name)
        self.wurzel = self.ordner / "projects"
        (self.wurzel / "p").mkdir(parents=True)
        self.datei = self.wurzel / "p" / "s.jsonl"
        self.datei.write_text(claude_zeile("Read", {"file_path": "C:\\a"}), encoding="utf-8")
        self.sensor = K.KiProtokollSensor(self.ordner / "stand.json",
                                          quellen=[("claude_code", self.wurzel, "**/*.jsonl", K.claude_taten)])

    def test_erst_nachgetragen_dann_live(self):
        alt = self.sensor.sammeln()
        self.assertEqual(len(alt), 1)
        self.assertTrue(alt[0].extra["nachgetragen"])
        with open(self.datei, "a", encoding="utf-8") as f:
            f.write(claude_zeile("Bash", {"command": "dir"}))
            f.write('{"type": "assistant", "halb')                       # halbe Zeile: später
        neu = self.sensor.sammeln()
        self.assertEqual([e.cmdline for e in neu], ["dir"])
        self.assertNotIn("nachgetragen", neu[0].extra)
        self.assertEqual(self.sensor.sammeln(), [])
        frisch = K.KiProtokollSensor(self.ordner / "stand.json",
                                     quellen=[("claude_code", self.wurzel, "**/*.jsonl", K.claude_taten)])
        self.assertEqual(frisch.sammeln(), [])                           # Lesestand bleibt über Neustarts

    def test_gekuerzt_und_geloescht(self):
        self.sensor.sammeln()
        self.datei.write_text("", encoding="utf-8")
        weg = self.sensor.sammeln()
        self.assertEqual([(e.aktion, e.extra["wie"]) for e in weg], [("ki_protokoll_weg", "gekürzt")])
        self.datei.write_text(claude_zeile("Read", {"file_path": "C:\\b"}), encoding="utf-8")
        self.sensor.sammeln()
        self.datei.unlink()
        weg = self.sensor.sammeln()
        self.assertEqual([e.extra["wie"] for e in weg], ["gelöscht"])

    def test_altes_protokoll_aufraeumen_ist_kein_alarm(self):
        self.sensor.sammeln()
        self.sensor._stand[str(self.datei)]["mtime"] = time.time() - 10 * 86400
        self.datei.unlink()
        self.assertEqual(self.sensor.sammeln(), [])

    def test_volle_runde_wertet_nichts_als_geloescht(self):
        for i in range(3):
            (self.wurzel / "p" / f"t{i}.jsonl").write_text(claude_zeile("Read", {"file_path": f"C:\\{i}"}) * 3,
                                                           encoding="utf-8")
        with mock.patch.object(K, "JE_DURCHLAUF", 2):
            for _ in range(8):
                self.assertFalse([e for e in self.sensor.sammeln() if e.aktion == "ki_protokoll_weg"])


def tat(cmd="", datei="", ki="Claude Code", art="befehl", zeit_=None, **extra):
    e = Ereignis(KI, "ki_tat", f"{ki} · x: {cmd or datei}", prozess=ki, cmdline=cmd, datei=datei,
                 extra={"ki": ki, "quelle": "claude_code", "art": art, **extra})
    if zeit_ is not None:
        e.zeit = zeit_
    return e


class Regeln(unittest.TestCase):
    def setUp(self):
        self.r = Regelwerk()

    def regeln(self, e):
        return [a.regel for a in self.r.pruefen(e)]

    def test_geheimnisse(self):
        self.assertEqual(self.regeln(tat(datei=BROWSER_DATEI, art="lesen")), ["R019"])
        self.assertEqual(self.regeln(tat(cmd=f"type {SCHLUESSEL}")), ["R019"])
        self.assertEqual(self.regeln(tat(datei="E:\\Daten\\Persoenlichkeiten\\X\\kern.json", art="lesen")), ["R019"])
        self.assertEqual(self.regeln(tat(datei="E:\\Daten\\Persoenlichkeiten\\X\\kern.json", art="lesen",
                                         ki="NemiCLI · X")), [])             # eigener Bereich, prüft NemiCLI selbst
        self.assertEqual(self.regeln(tat(art="suchen", suche=".s" + "sh")), [])   # Suchwort ist kein Zugriff
        self.assertEqual(self.regeln(tat(cmd="git status")), [])
        self.assertEqual(self.regeln(tat(datei=BROWSER_DATEI, art="lesen", nachgetragen=True)), [])

    def test_admin_autostart_protokoll(self):
        self.assertEqual(self.regeln(tat(cmd="Start-Process pwsh -Verb Run" + "As")), ["R020"])
        self.assertEqual(self.regeln(tat(cmd="sch" + "tasks /create /tn x /tr y")), ["R021"])
        self.assertEqual(self.regeln(tat(datei="C:\\Users\\u\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu"
                                              "\\Programs\\Startup\\x.lnk", art="schreiben")), ["R021"])
        weg = Ereignis(KI, "ki_protokoll_weg", "Protokoll gelöscht", prozess="claude_code", datei="C:\\s.jsonl")
        self.assertEqual(self.regeln(weg), ["R023"])

    def test_nachsehen_und_suchen_ist_kein_einrichten(self):
        run = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
        self.assertEqual(self.regeln(tat(cmd=f"Get-ItemProperty '{run}'")), [])
        self.assertEqual(self.regeln(tat(cmd=f"Set-ItemProperty '{run}' -Name x -Value y")), ["R021"])
        self.assertEqual(self.regeln(tat(cmd="grep -rn 'sch" + "tasks /create' tools")), [])
        self.assertEqual(self.regeln(tat(cmd="rg -n 'Run" + "As' src")), [])
        self.assertEqual(self.regeln(tat(cmd="Un" + "register-ScheduledTask -TaskName x")), [])
        self.assertEqual(self.regeln(tat(cmd=f"grep -r key {SCHLUESSEL}")), ["R019"])   # Suchen IN Geheimem zählt

    def test_massenloeschen(self):
        treffer = [self.regeln(tat(cmd=f"Remove-Item C:\\Projekte\\x{i}.txt")) for i in range(KI_LOESCHEN_AB)]
        self.assertEqual(treffer[-1], ["R022"])
        self.assertFalse(any(treffer[:-1]))

    def test_ki_regeln_kann_keine_ki_stumm_schalten(self):
        self.assertTrue({"R019", "R020", "R021", "R022", "R023"} <= NIE_STUMM_KI)

    def test_andere_regeln_bleiben_still(self):
        e = Ereignis(KI, "ki_prozess", "Claude startet git.exe", prozess="git.exe", cmdline="git status",
                     exe="C:\\Program Files\\Git\\cmd\\git.exe", extra={"ki": "Claude", "quelle": "wache"})
        self.assertEqual(self.regeln(e), [])


class Gegencheck(unittest.TestCase):
    T = 1_800_000_000.0

    def setUp(self):
        self.g = K.KiGegencheck()
        p = mock.patch.object(K.time, "time", return_value=self.T)
        p.start()
        self.addCleanup(p.stop)

    def melden(self, befehl, zeit, art="befehl"):
        self.g.gemeldet(tat(cmd=befehl, art=art, zeit_=zeit))

    def test_gemeldeter_befehl_deckt_seine_shell(self):
        self.melden("git status --short", self.T - 5)
        self.g.beobachtet("Claude", "bash.exe", "bash.exe -c source x.sh && eval 'git status --short'", self.T, 1)
        self.assertEqual(self.g.pruefen(self.T + 61), [])

    def test_ungemeldete_shell(self):
        self.melden("git status", self.T - 5)
        self.g.beobachtet("Claude", "powershell.exe", "powershell.exe -NoProfile -Command Invoke-Something geheim", self.T, 2)
        self.assertEqual(self.g.pruefen(self.T + 30), [])                   # Protokoll noch nicht gelesen
        e = self.g.pruefen(self.T + 61)
        self.assertEqual([(x.aktion, x.prozess, x.extra["ki"]) for x in e], [("ki_ungemeldet", "powershell.exe", "Claude")])
        self.assertEqual(self.g.pruefen(self.T + 120), [])                   # nur einmal

    def test_ein_befehl_deckt_einen_start(self):
        self.melden("dir", self.T - 5)
        self.g.beobachtet("Claude", "cmd.exe", "", self.T, 3)                 # Befehlszeile nicht lesbar
        self.g.beobachtet("Claude", "cmd.exe", "", self.T + 1, 4)
        self.assertEqual(len(self.g.pruefen(self.T + 70)), 1)

    def test_alt_intern_und_ohne_protokoll(self):
        self.melden("npm test", self.T - K.GEGEN_FENSTER_S - 10)             # zu lange her
        self.g.beobachtet("Claude", "bash.exe", "bash -c npm test", self.T, 5)
        self.g.beobachtet("Claude", "git.exe", "git rev-parse", self.T, 6)    # Helfer von Claude Code
        self.g.beobachtet("LM Studio", "python.exe", "x", self.T, 7)           # ohne Protokoll: nicht prüfbar
        self.assertEqual([x.pid for x in self.g.pruefen(self.T + 61)], [5])

    def test_starter_und_kodierter_helfer(self):
        import base64
        self.g.gemeldet(tat(cmd="Get-Date", zeit_=self.T - 3, werkzeug="PowerShell"))
        starter = ("cmd.exe /d /s /c chcp 65001 >nul & pwsh.exe -NoProfile -Command $s = "
                   "$env:CLAUDE_CODE_SHELL_LAUNCHER_SCRIPT; Invoke-Expression $s")
        self.g.beobachtet("Claude", "cmd.exe", starter, self.T, 10)
        kodiert = base64.b64encode("$env:PSModulePath=$PSHOME".encode("utf-16-le")).decode()
        schalter = "-" + "Enc" + "odedCommand"
        self.g.beobachtet("Claude", "pwsh.exe", f"pwsh.exe -NoProfile {schalter} {kodiert}", self.T + 1, 11)
        self.assertEqual(self.g.pruefen(self.T + 70), [])
        self.g.beobachtet("Claude", "cmd.exe", starter, self.T + 80, 12)          # zweite Sitzung ohne Aufruf
        heimlich = base64.b64encode("Remove-Thing everything".encode("utf-16-le")).decode()
        self.g.beobachtet("Claude", "pwsh.exe", f"pwsh.exe {schalter} {heimlich}", self.T + 200, 13)
        e = self.g.pruefen(self.T + 300)
        self.assertEqual([x.pid for x in e], [12, 13])
        self.assertIn("[entschlüsselt: Remove-Thing everything]", e[1].cmdline)

    def test_merkmale(self):
        self.assertEqual(K.merkmale("cd /c/x && ./venv/Scripts/python.exe -m unittest tests.test_a"),
                         ["tests.test_a", "python.exe", "unittest"])
        self.assertEqual(K.merkmale("ls"), [])


class ImMotor(unittest.TestCase):
    def setUp(self):
        from wache import justierung as J, motor as MO, vollscan as VS
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ordner = Path(tmp.name)
        e = dict(J.STANDARD)
        e["grenzen"] = dict(J.STANDARD["grenzen"])
        e["ml"] = False
        for ziel, wert in ((MO, ("MODELL_ORDNER", ordner / "modelle")), (VS, ("DB", ordner / "vollscan.db")),
                           (MO, ("ORDNER", ordner))):
            p = mock.patch.object(ziel, *wert)
            p.start()
            self.addCleanup(p.stop)
        self.m = MO.Motor(ordner / "w.db", e)
        self.addCleanup(self.m.speicher.schliessen)
        self.addCleanup(self.m.baseline.schliessen)

    def test_tat_wird_bewertet_nachtrag_nicht(self):
        alarme = []
        self.m._bewerten(tat(datei=BROWSER_DATEI, art="lesen"), alarme)
        self.assertEqual([a.regel for a in alarme], ["R019"])
        alarme = []
        self.m._bewerten(tat(datei=BROWSER_DATEI, art="lesen", nachgetragen=True), alarme)
        self.assertEqual(alarme, [])

    def test_gegencheck_erst_schatten_dann_alarm(self):
        from wache import motor as MO
        ungemeldet = lambda: Ereignis(KI, "ki_ungemeldet", "Claude startete bash.exe", prozess="bash.exe",
                                      cmdline="bash -c x", extra={"ki": "Claude", "quelle": "wache",
                                                                  "protokoll": "Claude Code"})
        alarme = []
        self.m._bewerten(ungemeldet(), alarme)
        self.assertEqual(alarme, [])
        self.assertIn("bash.exe", (MO.MODELL_ORDNER / MO.KI_SCHATTEN).read_text(encoding="utf-8"))
        self.m.e["ki_alarm"] = True
        self.m._bewerten(ungemeldet(), alarme)
        self.assertEqual([a.regel for a in alarme], ["R024"])
        self.assertIn("R024", NIE_STUMM_KI)

    def test_prozess_einer_ki_wird_markiert(self):
        self.m.ki_baum.wurzel(4242, 1.0, "Claude")
        e = Ereignis(PROZESS, "process_start", "x", prozess="git.exe", pid=4243, ppid=4242, start=2.0,
                     eltern_start=1.0, exe="C:\\Git\\git.exe")
        self.m._bewerten(e, [])
        self.assertEqual(e.extra["ki"], "Claude")

    def test_sensoren_mit_und_ohne_tracker(self):
        namen = {s.name for s in (self.m.ki_prozess_sensor, self.m.ki_protokoll_sensor)}
        self.assertEqual(namen, {"ki-prozesse", "ki-protokolle"})
        self.assertEqual(K.protokoll_quellen(), [])                     # Tests lesen keine echten Protokolle

    def test_uebersicht(self):
        self.m.speicher.ereignisse_schreiben([tat(cmd="git status"), tat(datei="C:\\a.py", art="lesen")])
        from wache import werkzeuge, zugang
        with mock.patch.object(zugang, "speicher", return_value=self.m.speicher):
            text = werkzeuge.ki({"stunden": 1})
        self.assertIn("Claude Code: 2", text)
        self.assertIn("git status", text)


if __name__ == "__main__":
    unittest.main()
