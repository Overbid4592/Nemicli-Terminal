"""/restart: nur über die Eingabe, Wache stoppen, Exit-Code fürs eigene Fenster, Nachfolger außerhalb davon."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import commands                                        # noqa: E402
import neustart                                        # noqa: E402


class _Ende(Exception):
    pass


class NeustartTests(unittest.TestCase):
    def setUp(self):
        neustart.ANGEFORDERT = False
        neustart.BEENDEN = None
        neustart._fertig = False

    tearDown = setUp

    def test_befehl_erkannt(self):
        for t in ("/restart", "  /RESTART ", "/neustart"):
            self.assertTrue(neustart.ist_befehl(t), t)
        for t in ("/restart jetzt", "restart", "/reset", ""):
            self.assertFalse(neustart.ist_befehl(t), t)

    def test_ki_darf_ihn_nicht_oeffnen(self):
        self.assertIn("/restart", commands.MENUE_GESPERRT)
        self.assertNotIn("/restart", commands.menue_befehle())
        self.assertIn("/restart", commands.COMMANDS)

    def test_im_eigenen_fenster_nur_exit_code(self):
        with mock.patch.dict(os.environ, {"NEMICLI_FENSTER": "1"}), \
             mock.patch.object(neustart.subprocess, "Popen") as popen, \
             mock.patch.object(neustart.os, "_exit", side_effect=_Ende) as ende:
            with self.assertRaises(_Ende):
                neustart.beenden()
        popen.assert_not_called()
        ende.assert_called_once_with(neustart.CODE)

    def test_ausserhalb_startet_nachfolger_der_wartet(self):
        with mock.patch.dict(os.environ, {"NEMICLI_FENSTER": ""}), \
             mock.patch.object(neustart.subprocess, "Popen") as popen, \
             mock.patch.object(neustart.os, "_exit", side_effect=_Ende):
            with self.assertRaises(_Ende):
                neustart.beenden()
        argv = popen.call_args.args[0]
        self.assertEqual(["--warte-pid", str(os.getpid())], argv[-2:])

    def test_debugbericht_endet_sauber(self):
        import debugbericht
        with mock.patch.dict(os.environ, {"NEMICLI_FENSTER": "1"}), \
             mock.patch.object(debugbericht, "beenden") as zu, mock.patch.object(debugbericht, "ereignis"), \
             mock.patch.object(neustart.os, "_exit"):
            neustart.beenden()
        zu.assert_called_once()                                       # sonst zählt jeder Neustart als Absturz

    def test_nur_einmal_beenden(self):
        with mock.patch.dict(os.environ, {"NEMICLI_FENSTER": "1"}), \
             mock.patch.object(neustart.os, "_exit") as ende:
            neustart.beenden()
            neustart.beenden()                       # Wachhund und normales Ende gleichzeitig
        ende.assert_called_once()

    def test_warten_nimmt_die_schalter_heraus(self):
        argv = ["main.py", "--warte-pid", "999999", "--x"]
        with mock.patch("psutil.Process") as proc:
            neustart.warten(argv)
        self.assertEqual(["main.py", "--x"], argv)
        proc.return_value.wait.assert_called_once()
        argv = ["main.py", "--warte-pid", str(os.getpid())]
        neustart.warten(argv)                        # eigener Prozess: nicht auf sich selbst warten
        self.assertEqual(["main.py"], argv)

    def test_ablauf_stoppt_wache_und_beendet_oberflaeche(self):
        beendet = []
        neustart.BEENDEN = lambda: beendet.append(True)
        with mock.patch.object(neustart, "wache_stoppen", return_value="Wache gestoppt.") as stopp, \
             mock.patch.object(neustart, "_wachhund"), \
             mock.patch("ui.info"), mock.patch("ui.warn"):
            asyncio.run(neustart.ausfuehren())
        stopp.assert_called_once()
        self.assertTrue(neustart.ANGEFORDERT)
        self.assertEqual([True], beendet)

    def test_terminal_fenster_kennt_den_code(self):
        import terminal_fenster
        self.assertEqual(neustart.CODE, terminal_fenster.NEUSTART_CODE)


if __name__ == "__main__":
    unittest.main()
