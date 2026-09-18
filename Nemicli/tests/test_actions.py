"""Offline-Tests für Status und Freigabevorschau, nur mit synthetischen Daten.

python -I -S -B -m unittest discover -s tests -p test_actions.py
Keine echten App-Abhängigkeiten, Dateien, Programme oder Modelle werden benutzt.
"""

import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_actions():
    name = "isolated_actions_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/actions.py")
    module = importlib.util.module_from_spec(spec)
    dependencies = {key: NS() for key in ("foldersense", "learn", "memory", "pdfgen", "webfetch")}
    with patch.dict(sys.modules, {name: module, **dependencies}):
        spec.loader.exec_module(module)
    return module


A = load_actions()


class ParseTests(unittest.TestCase):
    """Chat 131 (16.09.2026): Modell schloss den Aktions-Zaun nicht, die Datei
    'wurde geschrieben' – nur nicht wirklich. So ein Block darf nicht stumm verloren gehen."""

    def test_block_without_closing_fence_at_end_is_parsed(self):
        text = ('Ich leg den Bericht ab.\n\n```aktion\n'
                '{"tool": "datei_schreiben", "pfad": "x.md", "inhalt": "a {b} c"}')
        acts, cleaned = A.parse_actions(text)
        self.assertEqual([a["tool"] for a in acts], ["datei_schreiben"])
        self.assertEqual(acts[0]["inhalt"], "a {b} c")
        self.assertEqual(cleaned, "Ich leg den Bericht ab.")
        self.assertIsNone(A.unparsed_action_note(text, acts))

    def test_closed_and_unclosed_blocks_together(self):
        text = '```aktion\n{"tool": "a"}\n```\nmehr\n```aktion\n{"tool": "b"}'
        self.assertEqual([a["tool"] for a in A.parse_actions(text)[0]], ["a", "b"])

    def test_truncated_json_gives_visible_note(self):
        text = 'Text\n```aktion\n{"tool": "datei_schreiben", "pfad": "x", "inhalt": "abgeschn'
        acts, _ = A.parse_actions(text)
        self.assertEqual(acts, [])
        self.assertIn("NICHTS ausgeführt", A.unparsed_action_note(text, acts))

    def test_plain_text_and_code_blocks_give_no_note(self):
        for text in ("Hallo", "```python\nprint(1)\n```", '```\n{"x": 1}\n```'):
            with self.subTest(text=text):
                acts, _ = A.parse_actions(text)
                self.assertIsNone(A.unparsed_action_note(text, acts))


class ResultTests(unittest.TestCase):
    def run_action(self, action):
        return asyncio.run(A.run(action))

    def test_command_uses_exitcode_not_output_prefix(self):
        cases = [(7, "", "", False), (1, "", "Access is denied.", False),
                 (0, "Fehlerstatistik: 0 Fehler", "", True)]
        for code, stdout, stderr, ok in cases:
            with self.subTest(code=code, stdout=stdout):
                process = NS(returncode=code, stdout=stdout, stderr=stderr)
                with patch.object(A.subprocess, "run", return_value=process):
                    result = self.run_action({"tool": "befehl", "befehl": "Write-Output 'Test'"})
                self.assertIs(result.ok, ok)
                self.assertEqual(result.returncode, code)
                if code:
                    self.assertIn(f"Exitcode {code}", result.text)
                if stderr:
                    self.assertIn(stderr, result.text)

    def test_command_wrapper_preserves_native_and_powershell_errors(self):
        with patch.object(A.subprocess, "run", return_value=NS(returncode=0, stdout="Test", stderr="")) as run:
            A._befehl({"befehl": "Write-Output 'Test' # abschließender Kommentar"})
        invocation = run.call_args.args[0]
        self.assertEqual(invocation[:3], ["powershell", "-NoProfile", "-Command"])
        wrapped = invocation[3]
        self.assertIn("$ErrorActionPreference='Stop'", wrapped)
        self.assertIn("$nemiNativeExitCode=$LASTEXITCODE", wrapped)
        self.assertIn("$Error.Count -gt 0", wrapped)
        self.assertIn("catch { [Console]::Error.WriteLine", wrapped)
        self.assertIn("# abschließender Kommentar\n}", wrapped)

    def test_timeout_is_explicit_error(self):
        with patch.object(A.subprocess, "run", side_effect=subprocess.TimeoutExpired("simulated", 120)):
            result = self.run_action({"tool": "befehl", "befehl": "Write-Output 'Test'"})
        self.assertIs(result.ok, False)
        self.assertIsNone(result.returncode)

    def test_unknown_tool_and_missing_fields_are_errors(self):
        for action in ({"tool": "unbekannt"}, {"tool": "datei_schreiben", "pfad": "synthetic.txt"}):
            with self.subTest(action=action):
                self.assertIs(self.run_action(action).ok, False)

    def test_ordinary_tool_text_cannot_set_failure_status(self):
        with patch.dict(A.ACTIONS, {"synthetic": {"func": lambda a: "Fehler ist ein Wort im Inhalt",
                                                   "confirm": False, "felder": []}}):
            result = self.run_action({"tool": "synthetic"})
        self.assertIs(result.ok, True)

    def test_protected_mutations_have_explicit_failure(self):
        cases = [
            {"tool": "datei_schreiben", "pfad": "synthetic.txt", "inhalt": "Test"},
            {"tool": "datei_bearbeiten", "pfad": "synthetic.txt", "suchen": "Test"},
            {"tool": "ordner_erstellen", "pfad": "synthetic"},
            {"tool": "verschieben", "von": "synthetic.txt", "nach": "synthetic-other.txt"},
            {"tool": "loeschen", "pfad": "synthetic.txt"},
            {"tool": "pdf_erstellen", "pfad": "synthetic.pdf", "inhalt": "Test"},
        ]
        with patch.object(A, "_guard", return_value="Synthetische Schutzablehnung"):
            for action in cases:
                with self.subTest(tool=action["tool"]):
                    self.assertIs(self.run_action(action).ok, False)

    def test_command_guard_does_not_launch_a_process(self):
        with patch.object(A, "_guard_command", return_value="Synthetische Schutzablehnung"), \
                patch.object(A.subprocess, "run") as run:
            result = self.run_action({"tool": "befehl", "befehl": "Write-Output 'Test'"})
        self.assertIs(result.ok, False)
        run.assert_not_called()

    def test_missing_edit_match_does_not_write(self):
        with patch.object(A, "_guard", return_value=None), \
                patch.object(A.Path, "read_text", return_value="original"), \
                patch.object(A.Path, "write_text") as write:
            result = self.run_action({"tool": "datei_bearbeiten", "pfad": "synthetic.txt", "suchen": "fehlt"})
        self.assertIs(result.ok, False)
        write.assert_not_called()

    def test_web_strings_have_unverified_status(self):
        for tool, function, fields in (("web_lesen", "fetch", {"url": "https://example.invalid"}),
                                       ("web_wiki", "wikipedia", {"suche": "Test"}),
                                       ("web_suche", "search", {"suche": "Test"})):
            for body in ("Fehler ist Bestandteil des Inhalts", "❌ Synthetischer Quellenfehler", "Testinhalt"):
                with self.subTest(tool=tool, body=body), \
                        patch.object(A.webfetch, function, return_value=body, create=True):
                    result = self.run_action({"tool": tool, **fields})
                    self.assertIsNone(result.ok)
                    self.assertEqual(result.status, "unverified")
                    self.assertEqual(result.text, body)


class PreviewTests(unittest.TestCase):
    def preview(self, action, exists=False, original=""):
        with patch.object(A.Path, "exists", return_value=exists), \
                patch.object(A.Path, "is_dir", return_value=False), \
                patch.object(A.Path, "read_text", return_value=original), \
                patch.object(A.Path, "write_text") as write:
            result = A.confirmation_preview(action)
        write.assert_not_called()
        return result

    def test_full_content_is_available_beyond_old_limits(self):
        content = "A" * 6500 + "\nEINDEUTIGES_ENDE"
        result = self.preview({"tool": "datei_schreiben", "pfad": "synthetic.txt", "inhalt": content})
        self.assertTrue(result.endswith(content))
        self.assertIn("Neue Datei wird angelegt", result)
        self.assertIn(str(Path("synthetic.txt").resolve()), result)

    def test_overwrite_and_different_suffixes_are_visible(self):
        action = {"tool": "datei_schreiben", "pfad": "synthetic.txt", "inhalt": "A" * 80 + "VERSION_1"}
        first = self.preview(action, exists=True)
        second = self.preview(dict(action, inhalt="A" * 80 + "VERSION_2"), exists=True)
        self.assertIn("ÜBERSCHRIEBEN", first)
        self.assertNotEqual(first, second)

    def test_edit_preview_matches_every_replacement(self):
        action = {"tool": "datei_bearbeiten", "pfad": "synthetic.txt", "suchen": "alt", "ersetzen": "neu"}
        result = self.preview(action, exists=True, original="alt\nweiter alt\n")
        self.assertIn("2 Fundstelle(n)", result)
        self.assertTrue(result.endswith("neu\nweiter neu\n"))

    def test_pdf_preview_contains_title_and_full_markdown(self):
        content = "# Bericht\n" + "Text\n" * 400
        result = self.preview({"tool": "pdf_erstellen", "pfad": "synthetic.pdf", "inhalt": content,
                               "titel": "Synthetischer Titel"}, exists=True)
        self.assertIn("PDF-Titel: Synthetischer Titel", result)
        self.assertIn("ÜBERSCHRIEBEN", result)
        self.assertTrue(result.endswith(content))

    def test_pdf_preview_names_the_actual_normalized_target(self):
        result = self.preview({"tool": "pdf_erstellen", "pfad": "synthetic.txt", "inhalt": "Test"}, exists=True)
        self.assertIn(str(Path("synthetic.pdf").resolve()), result)
        self.assertNotIn("synthetic.txt", result)
        self.assertIn("ÜBERSCHRIEBEN", result)

    def test_unreadable_source_and_missing_match_prevent_preview(self):
        action = {"tool": "datei_bearbeiten", "pfad": "synthetic.txt", "suchen": "fehlt"}
        with self.assertRaises(ValueError):
            self.preview(action, exists=True, original="original")
        with patch.object(A.Path, "is_dir", return_value=False), \
                patch.object(A.Path, "read_text", side_effect=OSError("synthetic read failure")), \
                self.assertRaises(OSError):
            A.confirmation_preview(action)

    def test_other_actions_do_not_need_file_preview(self):
        self.assertIsNone(A.confirmation_preview({"tool": "befehl", "befehl": "Write-Output 'Test'"}))


class ImageOpeningTests(unittest.TestCase):
    def fake_imagegen(self):
        # paint() gibt (pfad, nachbessern_sinnvoll) zurück – hier eigene Pipeline.
        # missing_reason_aktiv() prueft den AKTIVEN Motor (seit 15.09.2026);
        # hier: eigene Pipeline, alles installiert -> None.
        return NS(backend=lambda: "builtin", missing_reason=lambda: None,
                  missing_reason_aktiv=lambda: None,
                  discover=lambda: ["synthetic"], resolve=lambda name: None,
                  paint=Mock(return_value=("synthetic.png", True)),
                  auto_nachbessern=Mock(return_value=None))

    def test_open_failure_preserves_saved_image_and_reports_failure(self):
        imagegen = self.fake_imagegen()
        with patch.dict(sys.modules, {"imagegen": imagegen}), \
                patch.object(A.os, "startfile", side_effect=OSError("synthetic opening failure"), create=True):
            result = asyncio.run(A.run({"tool": "bild_malen", "prompt": "synthetischer Bildtest"}))
        self.assertIs(result.ok, False)
        self.assertIn("gespeichert: synthetic.png", result.text)
        self.assertIn("Öffnen fehlgeschlagen", result.text)
        self.assertNotIn("und geöffnet", result.text)
        self.assertEqual("synthetischer Bildtest", imagegen.paint.call_args.args[0])

    def test_success_only_claims_opening_was_requested(self):
        with patch.dict(sys.modules, {"imagegen": self.fake_imagegen()}), \
                patch.object(A.os, "startfile", create=True) as start:
            result = asyncio.run(A.run({"tool": "bild_malen", "prompt": "synthetischer Bildtest"}))
        self.assertIs(result.ok, True)
        self.assertIn("Öffnen beim Anzeigeprogramm angefordert", result.text)
        self.assertNotIn("und geöffnet", result.text)
        start.assert_called_once_with("synthetic.png")


if __name__ == "__main__":
    unittest.main()


class TaintTests(unittest.TestCase):
    """merken/skill_merken brauchen Rückfrage, sobald in der Runde Web-Inhalt kam."""

    def setUp(self):
        A.reset_taint()

    def tearDown(self):
        A.reset_taint()

    def test_memory_tools_free_without_web(self):
        self.assertFalse(A.needs_confirm({"tool": "merken", "text": "x"}))
        self.assertFalse(A.needs_confirm({"tool": "skill_merken", "name": "n", "inhalt": "i"}))
        self.assertTrue(A.needs_confirm({"tool": "datei_schreiben", "pfad": "p", "inhalt": ""}))

    def test_web_tool_sets_taint_and_forces_confirm(self):
        A._web_taint = False
        with patch.object(A, "ACTIONS", {**A.ACTIONS,
                                         "web_wiki": {"func": lambda a: "ok", "confirm": False,
                                                      "felder": ["suche"]}}):
            asyncio.run(A.run({"tool": "web_wiki", "suche": "x"}))
        self.assertTrue(A.web_tainted())
        self.assertTrue(A.needs_confirm({"tool": "merken", "text": "x"}))
        self.assertTrue(A.needs_confirm({"tool": "skill_merken", "name": "n", "inhalt": "i"}))
        self.assertIn("aus dem Netz", A.describe({"tool": "merken", "text": "x"}))
        A.reset_taint()
        self.assertFalse(A.needs_confirm({"tool": "merken", "text": "x"}))
        self.assertNotIn("aus dem Netz", A.describe({"tool": "merken", "text": "x"}))

    def test_skill_describe_shows_content(self):
        d = A.describe({"tool": "skill_merken", "name": "Trick", "inhalt": "Zeile 1\nZeile 2"})
        self.assertIn("Trick", d)
        self.assertIn("Zeile 1 Zeile 2", d)


class SystemDirTabuTests(unittest.TestCase):
    """Windows-/Systemordner: seit 18.09.2026 nachschauen ja, ändern nie.
    Lesende Werkzeuge und `abfragen` kommen hin, `befehl` und alles Ändernde nicht."""

    WIN = r"C:\Windows"

    def setUp(self):
        A.foldersense.describe = lambda p: "synthetisch"       # ordner_auflisten hängt den ML-Typ an

    def _blocked(self, r):
        return isinstance(r, A.ActionResult) and r.ok is False and "Tabu" in r.text

    def test_read_tools_may_look_into_system_dirs(self):
        for r in (A._datei_lesen({"pfad": self.WIN + r"\win.ini"}),
                  A._datei_lesen({"pfad": "C:/Windows/win.ini"}),
                  A._datei_lesen({"pfad": r"%windir%\win.ini"}),
                  A._ordner_auflisten({"pfad": r"C:\Program Files"}),
                  A._dateien_suchen({"pfad": self.WIN, "muster": "win.ini"})):
            self.assertFalse(self._blocked(r), r)
        self.assertIn("win.ini", A._dateien_suchen({"pfad": self.WIN, "muster": "win.ini"}))

    def test_root_listing_shows_system_dirs_but_search_skips_them(self):
        r = A._ordner_auflisten({"pfad": "C:\\"})
        self.assertIn("[Ordner] Windows", r)
        self.assertIn("[Ordner] Users", r)
        # Suche ab C:\ wühlt NICHT durch Windows – nur eine Suche, die dort beginnt
        self.assertTrue(A._skip_system(Path(r"C:\Windows\win.ini"), base=Path("C:\\")))
        self.assertFalse(A._skip_system(Path(r"C:\Windows\win.ini"), base=Path(r"C:\Windows")))

    def test_write_tools_still_refuse_system_dirs(self):
        for r in (A._datei_schreiben({"pfad": self.WIN + r"\x.txt", "inhalt": "x"}),
                  A._loeschen({"pfad": self.WIN + r"\win.ini"}),
                  A._ordner_erstellen({"pfad": self.WIN + r"\neu"})):
            self.assertTrue(isinstance(r, A.ActionResult) and r.ok is False, r)

    def test_befehl_naming_system_dirs_is_refused_abfragen_not(self):
        for cmd in (r"Get-ChildItem C:\Windows", "ls C:/Windows/System32",
                    r"Get-Content $env:windir\win.ini", r"reg query HKLM\SOFTWARE",
                    r'& "C:\Program Files\Git\bin\git.exe" status', r"type C:\ProgramData\x.txt"):
            self.assertIsNotNone(A._guard_command(cmd), cmd)                 # befehl: nein
        for cmd in (r"Get-ChildItem C:\Windows", r"Get-Content $env:windir\win.ini",
                    r"reg query HKLM\SOFTWARE", r"Get-ItemProperty HKLM:\SOFTWARE\x"):
            self.assertIsNone(A._guard_command(cmd, lesend=True), cmd)      # abfragen: ja
            self.assertIsNone(A._nur_lesend(cmd), cmd)
        for cmd in (r"Remove-Item C:\Windows\x", r"reg add HKLM\SOFTWARE\x /v y"):
            self.assertIsNotNone(A._guard_command(cmd) or A._nur_lesend(cmd), cmd)
        for cmd in ("git status", "Get-ChildItem .", "python -V"):
            self.assertIsNone(A._guard_command(cmd), cmd)

    def test_user_profile_stays_readable(self):
        r = A._ordner_auflisten({"pfad": str(Path.home())})
        self.assertIsInstance(r, str)


class AbfragenTests(unittest.TestCase):
    """`abfragen` (18.09.2026): PowerShell ohne Rückfrage – aber NUR lesend.
    Fail-safe: alles, was ändert, startet, schreibt oder selbst ins Netz geht,
    muss draußen bleiben, egal wie es verpackt ist."""

    LESEND = [
        "Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Id, ProcessName, Path",
        "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine",
        "Get-NetTCPConnection -State Established | Select-Object OwningProcess, RemoteAddress",
        "Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled",
        "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct",
        "Get-Service | Where-Object Status -eq Running",
        "$a = Get-NetAdapterStatistics; Start-Sleep 2; $b = Get-NetAdapterStatistics; $b",
        "netstat -ano | Select-String ESTABLISHED",
        "ipconfig /all", "tasklist /v", "nslookup example.com", "route print",
        "Test-NetConnection 1.1.1.1 -Port 443",
        "Get-ScheduledTask | Where-Object State -ne Disabled",
        "Get-WinEvent -LogName Security -MaxEvents 20 | Format-List",
        "Get-Process | Where-Object { $_.Path -like '*AppData*' } | ForEach-Object { [pscustomobject]@{ n=$_.Name } }",
        "Get-Process | Where-Object Name -like 'svc-host*'",
        "(Get-Date).ToString('s')",
        r"[math]::Round((Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples[0].CookedValue, 1)",
        "if ((Get-Service WinDefend).Status -eq 'Running') { 'ja' } else { 'nein' }",
        "gps | sort cpu -desc | select -first 5",
        "Get-Process 2>$null | measure",
        "netsh advfirewall show allprofiles",
    ]
    AENDERND = [
        "Stop-Process -Name notepad", "Get-Process notepad | Stop-Process",
        r"Remove-Item C:\Users\x\Desktop\x.txt",
        r"Get-Process > C:\Users\x\p.txt", "Get-Process >> log.txt",
        "Get-Process | Out-File p.txt", "Get-Process | Tee-Object p.txt",
        "Get-Process | Export-Csv p.csv", "Set-Content x.txt 'a'", "New-Item x.txt",
        "Start-Process notepad", "Invoke-Expression 'Get-Process'",
        "Invoke-WebRequest https://example.com", "curl https://example.com",
        "cmd /c dir", "powershell -Command Get-Process", "notepad.exe",
        r"& 'C:\Users\x\x.exe'", r". .\script.ps1",
        "[System.IO.File]::Delete('x.txt')", "(Get-Process notepad).Kill()",
        "Get-Process | % { $_.Kill() }", "Add-Type -TypeDefinition 'class X {}'",
        "New-Object System.Net.WebClient", "Get-Process; Remove-Item x",
        "Get-Process | ForEach-Object { Stop-Process $_ }",
        "ipconfig /flushdns", "route add 0.0.0.0 mask 0.0.0.0 1.2.3.4",
        "netsh advfirewall set allprofiles state off", "arp -d *",
        "Read-Host 'x'", "Set-Service WinDefend -StartupType Disabled",
        "function x { Remove-Item y }; x", r"Import-Module .\evil.psm1",
        "Restart-Computer", "Clear-EventLog Security", "Rename-Item a b", "Copy-Item a b",
    ]

    def test_lesende_abfragen_kommen_durch(self):
        for cmd in self.LESEND:
            self.assertIsNone(A._guard_command(cmd) or A._nur_lesend(cmd), cmd)

    def test_aendernde_befehle_bleiben_draussen(self):
        for cmd in self.AENDERND:
            self.assertIsNotNone(A._guard_command(cmd) or A._nur_lesend(cmd), cmd)

    def test_abfragen_fragt_nicht_und_ist_lesewerkzeug(self):
        self.assertFalse(A.needs_confirm({"tool": "abfragen", "befehl": "Get-Process"}))
        self.assertTrue(A.needs_confirm({"tool": "zeitplan", "aktion": "anlegen", "name": "x"}))
        self.assertFalse(A.needs_confirm({"tool": "zeitplan_anzeigen"}))

    def test_abfragen_weist_aenderndes_ab_ohne_auszufuehren(self):
        with patch.object(A.subprocess, "run") as run:
            r = A._abfragen({"befehl": "Stop-Process -Name notepad"})
        self.assertFalse(r.ok)
        self.assertIn("nur lesen", r.text)
        run.assert_not_called()

    def test_abfragen_fuehrt_lesendes_aus(self):
        fake = NS(stdout="Name  Id\nnotepad 1", stderr="", returncode=0)
        with patch.object(A.subprocess, "run", return_value=fake) as run:
            r = A._abfragen({"befehl": "Get-Process notepad"})
        self.assertTrue(r.ok)
        self.assertIn("notepad", r.text)
        run.assert_called_once()

    def test_describe(self):
        self.assertIn("nur lesen", A.describe({"tool": "abfragen", "befehl": "Get-Process"}))
        d = A.describe({"tool": "zeitplan", "aktion": "anlegen", "name": "Wache",
                        "wann": "täglich 09:00", "auftrag": "Prüf das System."})
        self.assertIn("Wache", d)
        self.assertIn("täglich 09:00", d)
        self.assertIn("Prüf das System", d)
