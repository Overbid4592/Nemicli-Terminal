"""Die offene Liste: Prüfung mit dem echten PowerShell-Parser. Kein Befehl wird ausgeführt."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import actions     # noqa: E402
import modes       # noqa: E402
import sicherheit  # noqa: E402
import shellfrei   # noqa: E402

BEISPIELE = r"""
Get-CimInstance Win32_Processor
Get-CimInstance Win32_Processor | Select-Object Name
Get-CimInstance Win32_Processor | Select-Object NumberOfCores
Get-CimInstance Win32_Processor | Select-Object NumberOfLogicalProcessors
Get-CimInstance Win32_Processor | Select-Object MaxClockSpeed
Get-CimInstance Win32_Processor | Select-Object CurrentClockSpeed
Get-CimInstance Win32_Processor | Select-Object LoadPercentage
Get-Counter '\Processor(_Total)\% Processor Time'
Get-Counter '\System\Processor Queue Length'
(Get-CimInstance Win32_Processor).Name
Get-CimInstance Win32_PhysicalMemory
Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer
Get-CimInstance Win32_PhysicalMemory | Select-Object Speed
Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity
Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel
Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator
Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory
Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize
Get-Counter '\Memory\Available MBytes'
Get-Counter '\Memory\Pages/sec'
Get-Process
(Get-Process).Count
Get-Process | Select-Object -First 10
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10
Get-Process | Sort-Object Handles -Descending | Select-Object -First 10
Get-Process | Select-Object Name, Id
Get-Process chrome -ErrorAction SilentlyContinue
Get-Process -Name "NemiCLI*" -ErrorAction SilentlyContinue
Get-Process | Group-Object ProcessName | Sort-Object Count -Descending | Select-Object -First 10
Get-CimInstance Win32_OperatingSystem
Get-CimInstance Win32_OperatingSystem | Select-Object Caption
Get-CimInstance Win32_OperatingSystem | Select-Object Version
Get-CimInstance Win32_OperatingSystem | Select-Object BuildNumber
Get-CimInstance Win32_OperatingSystem | Select-Object OSArchitecture
Get-CimInstance Win32_OperatingSystem | Select-Object LastBootUpTime
Get-CimInstance Win32_ComputerSystem
Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model
Get-CimInstance Win32_BIOS | Select-Object Manufacturer, Version
Get-CimInstance Win32_BIOS | Select-Object ReleaseDate
Get-PSDrive -PSProvider FileSystem
Get-Volume
Get-Disk
Get-Partition
Get-CimInstance Win32_LogicalDisk
Get-CimInstance Win32_DiskDrive
Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID, FreeSpace
Get-Volume | Select-Object DriveLetter, FileSystem
Get-Volume | Select-Object DriveLetter, HealthStatus
Get-Counter '\PhysicalDisk(_Total)\% Disk Time'
Get-NetAdapter
Get-NetAdapter | Select-Object Name, Status
Get-NetIPConfiguration
Get-NetIPAddress
Get-NetRoute
Get-DnsClientServerAddress
Get-NetTCPConnection -State Established
Get-NetUDPEndpoint
Get-NetConnectionProfile
Get-NetAdapterStatistics
Get-CimInstance Win32_VideoController
Get-CimInstance Win32_VideoController | Select-Object Name
Get-CimInstance Win32_VideoController | Select-Object DriverVersion
Get-CimInstance Win32_VideoController | Select-Object VideoProcessor
Get-CimInstance Win32_VideoController | Select-Object CurrentHorizontalResolution
Get-CimInstance Win32_VideoController | Select-Object CurrentVerticalResolution
Get-CimInstance Win32_DesktopMonitor
nvidia-smi
nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu --format=csv
nvidia-smi --query-gpu=memory.used,memory.free --format=csv
Get-Service
Get-Service | Where-Object Status -eq Running
Get-Service | Where-Object Status -eq Stopped
Get-Service | Group-Object Status
Get-Service | Sort-Object DisplayName | Select-Object -First 20
Get-CimInstance Win32_Service | Select-Object -First 20 Name, State
Get-NetFirewallProfile | Select-Object Name, Enabled
Get-NetFirewallRule -Enabled True | Select-Object -First 20 DisplayName
Get-MpComputerStatus | Select-Object AntivirusEnabled, RealTimeProtectionEnabled
Get-MpThreatDetection | Select-Object -First 10
$PSVersionTable
$PSVersionTable.PSVersion
Get-Command
(Get-Command).Count
Get-Module -ListAvailable
Get-Module
Get-Alias
Get-PSProvider
Get-ExecutionPolicy -List
Get-Help Get-Process
Get-Date
Get-TimeZone
Get-Culture
Get-UICulture
Get-CimInstance Win32_Battery
Get-CimInstance Win32_SoundDevice
Get-CimInstance Win32_ComputerSystemProduct
Get-CimInstance Win32_StartupCommand | Select-Object Name, Location
Get-HotFix | Select-Object -First 10
Get-CimInstance Win32_SystemDriver | Select-Object -First 20 Name, State
""".strip().splitlines()

WEITERE = [
    "dir", "ls", "Get-ChildItem", "Get-ChildItem .\\core -Recurse -Filter *.py", "Get-Location", "whoami", "hostname",
    "ipconfig", "ipconfig /all", "tasklist", "systeminfo", "getmac", "driverquery", "nvidia-smi -L", "nvidia-smi -q",
    "git status", "git status --short", "git log --oneline -5", "git diff HEAD~1 --stat", "git show HEAD",
    "git branch", "git branch -a", "git --version", "python --version", "pip --version", "ollama --version",
    "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 Name, @{N='MB';E={[math]::Round($_.WS/1MB)}}",
    "Get-Process | Where-Object { $_.CPU -gt 10 } | Format-Table Name, CPU",
    "GET-PROCESS", "(Get-Process | Measure-Object WS -Sum).Sum",
]

NIE_FREI = [
    "(Get-WmiObject Win32_OperatingSystem).Reboot()", "(Get-CimInstance Win32_OperatingSystem).Win32Shutdown(1)",
    "Get-Process notepad | ForEach-Object { $_.CloseMainWindow() }", "(Get-Process notepad).Kill()",
    "Get-Process | Where-Object { $_.Kill() }", "Get-Process | Select-Object @{N='x';E={$_.Kill()}}",
    "Get-Process | Stop-Process", "Get-Process | Where-Object { Stop-Process $_ }",
    "Get-Process; Remove-Item x", "Get-Process\nRemove-Item x", "Get-Process > x.txt", "Get-Process | Out-File x.txt",
    "Get-Process 2>&1", "Get-Process $(Remove-Item x)", 'Get-Process "$(Remove-Item x)"', "Get-Process @(Remove-Item x)",
    "& Get-Process", ". Get-Process", "& { Remove-Item x }", "$x = Get-Process", "Get-Process } ; Remove-Item x ; & {",
    "$env:OPENAI_API_KEY", "Get-ChildItem Env:", "dir Env:", "Get-ChildItem -Path Env:", "Get-ChildItem HKLM:\\Software",
    "Get-ChildItem \\\\server\\share", 'Get-Process "$env:USERNAME"',
    "Get-Content .env", "Get-Content C:\\Users\\user\\.ssh\\id_rsa", "gc .env", "type .env", "Get-Clipboard",
    "Get-Process -ComputerName server01", "Get-Process -Comp server01", "Get-Service -cn server01",
    "Get-CimInstance Win32_Process", "Get-CimInstance Win32_Processor -ComputerName server01",
    "Get-CimInstance -Query 'SELECT * FROM Win32_Process'", "Get-CimInstance Win32_BIOS -Namespace root\\x",
    "Get-CimInstance Win32_BIOS | Invoke-CimMethod -MethodName Reboot", "Get-Help Get-Process -Online",
    "Get-Help Get-Process -ShowWindow", "Get-Counter -Continuous", "Get-NetAdapter -AsJob",
    "nvidia-smi -pl 100", "nvidia-smi -r", "nvidia-smi --query-gpu=name -f x.txt", "nvidia-smi -pm 1",
    "ipconfig /release", "ipconfig /flushdns", "tasklist /s server01", "systeminfo /s server01",
    "git push", "git commit -m x", "git checkout main", "git branch neu", "git branch -D main",
    "git log --output=x.txt", "git diff --ext-diff", "git -c core.pager=x log", "git status > x",
    "python x.py", "python -c 1", "pip install x", "ollama run x", "ruff check --fix",
    "[System.IO.File]::ReadAllText('x')", "Get-Process | ForEach-Object { $_.Name }", "Get-Date | Set-Date",
    "Invoke-Expression 'Get-Process'", "iex 'x'", "Start-Process notepad", "Start-Sleep 99999",
    "Get-Process | Select-Object @{N='x';E={[IO.File]::Delete('x')}}", "Get-Process | Sort-Object { $x = 1 }",
    "Get-Process | Sort-Object { $i++ }", "function Get-Process { Remove-Item x }; Get-Process", "",
    # Anbieter- und Netzpfade auch hinter -Param:, in Klammern, per Pipeline oder mit Modulnamen
    "Get-ChildItem -Path:Env:", "Get-ChildItem ('HKLM:\\Software')", "'HKLM:\\Software' | Get-ChildItem",
    "Get-ChildItem Microsoft.PowerShell.Core\\Registry::HKEY_LOCAL_MACHINE", "Get-ChildItem \\/server\\share",
    "dir -LiteralPath:\\\\server\\share",
    # Komma-Listen werden zu mehreren Argumenten
    "git log --oneline,--output=x.txt", "nvidia-smi -L,-r",
    # weitere Namen für den fremden Rechner
    "Get-HotFix -IPAddress server01", "Get-HotFix -__Server server01",
]


@unittest.skipUnless(os.name == "nt", "braucht den PowerShell-Parser")
class Liste(unittest.TestCase):
    def test_die_100_beispiele_sind_frei(self):
        self.assertEqual(100, len(BEISPIELE))
        offen = {c: shellfrei.grund(c) for c in BEISPIELE if shellfrei.grund(c)}
        self.assertEqual({}, offen)

    def test_weitere_sind_frei(self):
        offen = {c: shellfrei.grund(c) for c in WEITERE if shellfrei.grund(c)}
        self.assertEqual({}, offen)

    def test_die_sperren_lassen_alle_frei_laufen(self):
        offen = {c: actions._guard_command(c) for c in BEISPIELE + WEITERE if actions._guard_command(c)}
        self.assertEqual({}, offen)

    def test_was_nie_frei_sein_darf(self):
        durch = [c for c in NIE_FREI if shellfrei.frei(c)]
        self.assertEqual([], durch)

    def test_parser_start_ist_eigener_aufruf(self):
        import subprocess
        from wache import ereignisse as E, regeln as R
        zeile = subprocess.list2cmdline(["powershell", "-NoProfile", "-NonInteractive", "-Command", shellfrei._PARSER])
        e = E.Ereignis(E.PROZESS, "process_start", "powershell.exe", prozess="powershell.exe", eltern="python.exe",
                       cmdline=zeile)
        self.assertTrue(R.eigener_aufruf(e))

    def test_66_befehle(self):
        anzahl = (len(shellfrei.ABFRAGEN) + len(shellfrei.FILTER) + len(shellfrei._NATIV) + len(shellfrei.GIT_LESEN)
                  + len(shellfrei.VERSION_PROGRAMME))
        self.assertEqual(66, anzahl)
        self.assertEqual(15, len(shellfrei.CIM_KLASSEN))


@unittest.skipUnless(os.name == "nt", "braucht den PowerShell-Parser")
class Modi(unittest.TestCase):
    FREI = {"tool": "befehl", "befehl": "Get-Process | Select-Object -First 3"}
    NICHT = {"tool": "befehl", "befehl": "Remove-Item x"}

    def _modus(self, name):
        return mock.patch.object(modes, "current", return_value=name)

    def test_frei_in_jedem_modus(self):
        for m in ("chat", "lesen", "normal", "auto", "autoan"):
            with self._modus(m), mock.patch.object(actions, "_web_taint", False):
                self.assertEqual("run", modes.decide(self.FREI, True), m)

    def test_anderes_bleibt_wie_es_war(self):
        with mock.patch.object(actions, "_web_taint", False):
            with self._modus("normal"):
                self.assertEqual("ask", modes.decide(self.NICHT, True))
            with self._modus("lesen"):
                self.assertEqual("block", modes.decide(self.NICHT, True))

    def test_nach_netz_inhalt_gilt_die_liste_nicht(self):
        with self._modus("normal"), mock.patch.object(actions, "_web_taint", True):
            self.assertEqual("ask", modes.decide(self.FREI, True))


class LueckeNurLesend(unittest.TestCase):
    """Auch die Stufe „nur lesend“ (Auto läuft ohne Frage, sonst eine statt zwei Fragen) lässt das nicht mehr durch."""

    def test_nicht_mehr_lesend(self):
        for c in ("(Get-WmiObject Win32_OperatingSystem).Reboot()",
                  "(Get-CimInstance Win32_OperatingSystem).Win32Shutdown(1)",
                  "Get-Process notepad | ForEach-Object { $_.CloseMainWindow() }",
                  "(Get-WmiObject Win32_Product -Filter \"Name='X'\").Uninstall()",
                  "(Get-WmiObject Win32_Volume -Filter \"DriveLetter='D:'\").Format('NTFS')",
                  "Get-Content .env", "Get-Content C:\\Users\\user\\.ssh\\id_rsa", "$env:OPENAI_API_KEY",
                  "Get-ChildItem Env:", "nvidia-smi -pl 100", "nvidia-smi -r", "powercfg /energy",
                  "Get-Help Get-Process -Online", "Get-CimInstance Win32_Process -ComputerName server01"):
            self.assertIsNotNone(actions._nur_lesend(c), c)
            with mock.patch.object(sicherheit, "whitelist", return_value=[]), \
                    mock.patch.object(sicherheit, "auto_liste", return_value=[]):
                self.assertFalse(sicherheit.auto_befehl_frei(c, ROOT), c)

    def test_lesen_bleibt_moeglich(self):
        for c in ("Get-ChildItem $env:USERPROFILE", "Get-Content README.md",
                  "Get-Process | Select-Object @{N='MB';E={[math]::Round($_.WS/1MB)}}", "(Get-Date).ToString('yyyy')",
                  "'a b'.Split(' ')", "nvidia-smi", "nvidia-smi -L", "Get-Help Get-Process", "powercfg /l"):
            self.assertIsNone(actions._nur_lesend(c), c)


if __name__ == "__main__":
    unittest.main()
