"""Schutzregeln aus der Code-Durchsicht: fremde Freigaben, Datenströme, Endziele beim Kopieren,
relative Pfade im Arbeitsordner, interne Felder, /compact aus KI-Menüs, späte Bindung, Wache."""
import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions                                         # noqa: E402
import commands                                        # noqa: E402


class PfadTests(unittest.TestCase):
    def test_fremde_freigabe_ohne_namensaufloesung(self):
        with mock.patch("socket.gethostbyname", side_effect=AssertionError("DNS")) as dns:
            self.assertIsNone(actions._kandidaten(r"\\geheim.example\share\a.txt"))
            self.assertIsNone(actions._kandidaten("//geheim.example/share/a.txt"))
        dns.assert_not_called()

    def test_lokale_laufwerksfreigabe_bleibt(self):
        k = actions._kandidaten(r"\\localhost\C$\Temp\x.txt")
        self.assertTrue(k and str(k[0]).lower().startswith("c:\\temp"))

    def test_alternativer_datenstrom_gesperrt(self):
        self.assertIsNone(actions._kandidaten(r"C:\Temp\profil.ps1::$DATA"))
        self.assertIsNone(actions._kandidaten(r"C:\Temp\a.txt:versteckt"))
        self.assertTrue(actions._kandidaten(r"C:\Temp\a.txt"))

    def test_kopieren_prueft_das_endziel(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "quelle").mkdir()
        (tmp / "quelle" / "profile.ps1").write_text("x", encoding="utf-8")
        (tmp / "ziel").mkdir()
        gesperrt = str(tmp / "ziel" / "profile.ps1")

        def guard(*pfade):
            return "gesperrt" if any(str(Path(p)) == gesperrt for p in pfade) else None

        with mock.patch.object(actions, "_guard", side_effect=guard), \
             mock.patch.object(actions, "_read_guard", return_value=None):
            r = actions._datei_kopieren({"von": str(tmp / "quelle" / "profile.ps1"), "nach": str(tmp / "ziel")})
            self.assertFalse(r.ok)
            self.assertFalse(Path(gesperrt).exists())
            r = actions._verschieben({"von": str(tmp / "quelle" / "profile.ps1"), "nach": str(tmp / "ziel")})
            self.assertFalse(r.ok)
            self.assertTrue((tmp / "quelle" / "profile.ps1").exists())

    def test_relative_pfade_gegen_den_arbeitsordner(self):
        gesehen = []

        def guard(p):
            gesehen.append(Path(p).resolve())
            return "gesperrt" if "geheim" in str(p) else None

        with mock.patch("coding.arbeitsordner", return_value=Path(r"C:\Projekte\app")), \
             mock.patch.object(actions, "_guard", side_effect=guard), \
             mock.patch.object(actions, "_read_guard", side_effect=guard):
            self.assertIsNotNone(actions._relativ_sperre(r"Set-Content ..\..\geheim\x.txt 1"))
            self.assertIsNotNone(actions._relativ_sperre("Get-Content '../geheim/.env'", lesend=True))
            self.assertIsNone(actions._relativ_sperre("Get-ChildItem .\\src"))
        self.assertIn(Path(r"C:\geheim\x.txt"), gesehen)


class AktionsTests(unittest.TestCase):
    def test_ki_kann_keine_internen_felder_setzen(self):
        acts, _ = actions.parse_actions('```aktion\n{"tool": "loeschen", "pfad": "x", "_frei": "schon freigegeben"}\n```')
        self.assertEqual([{"tool": "loeschen", "pfad": "x"}], acts)

    def test_compact_nicht_aus_ki_menue(self):
        befehl, grund = commands.menue_pruefen("/compact")
        self.assertIsNone(befehl)
        self.assertNotIn("/compact", commands.menue_befehle())


class MainTests(unittest.TestCase):
    def test_hintergrund_bindet_die_aktion_sofort(self):
        """HG.starten ruft arbeit() erst später auf – in der Schleife muss die Aktion fest gebunden sein."""
        baum = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if (isinstance(knoten, ast.Call) and getattr(knoten.func, "attr", "") == "starten"
                    and len(knoten.args) >= 3 and isinstance(knoten.args[2], ast.Lambda)):
                self.assertTrue(knoten.args[2].args.args, "lambda ohne gebundene Aktion")


class MotorTests(unittest.TestCase):
    def test_lora_huellen_werden_mit_ausgelagert(self):
        try:
            import ggufengine.engine as E
        except Exception as e:                                       # ohne torch
            self.skipTest(str(e))
        self.assertIn("loramotor", E._EIGENE_MODULE)


class WacheTests(unittest.TestCase):
    def test_bekanntes_ziel_adelt_kein_neues_programm(self):
        from wache import speicher as S
        sp = mock.Mock()
        ev = mock.Mock(prozess="neu.exe", exe=r"C:\x\neu.exe", eltern="", ziel="1.2.3.4", datei="", text="")
        bekannt = {"ziel:1.2.3.4": {"x": 1}}
        sp.bekannt = lambda s: bekannt.get(s)
        self.assertFalse(S.Speicher.programm_bekannt(sp, ev))
        self.assertFalse(S.Speicher.ganz_bekannt(sp, ev))
        bekannt[r"prozess:c:\x\neu.exe"] = {"x": 1}
        self.assertTrue(S.Speicher.programm_bekannt(sp, ev))
        self.assertTrue(S.Speicher.ganz_bekannt(sp, ev))


if __name__ == "__main__":
    unittest.main()
