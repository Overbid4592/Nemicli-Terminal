"""Offline-Tests: Rechtsklick-Menü in der exe, echte Wache-Alarme bleiben (erledigt),
Config sicher speichern, keine Code-Schnipsel aus Fremd-Inhalt.

python -m unittest discover -s tests -p test_luecken_fixes2.py
"""

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

import config        # noqa: E402
import kontextmenue  # noqa: E402
import learn         # noqa: E402
from wache import ereignisse as E, speicher as S, werkzeuge as W, zugang  # noqa: E402


class KontextmenueTests(unittest.TestCase):
    def test_exe_ruft_sich_selbst_mit_kontext_auf(self):
        exe = Path(r"C:\Users\user\.ncli2\NemiCLI.exe")
        with patch.object(sys, "frozen", True, create=True), patch("paths.exe_ohne_fenster", lambda: exe):
            b = kontextmenue.befehle()
        self.assertEqual(f'"{exe}" --kontext screenshot', b["screenshot"])
        self.assertEqual(f'"{exe}" --kontext bild "%1"', b["bild"])
        self.assertEqual(f'"{exe}" --kontext antwort', b["antwort"])
        self.assertTrue(kontextmenue.befehle()["screenshot"].endswith('kontextmenue.py" screenshot'))

    def test_fenster_starten_in_der_exe_ohne_nemicli_cmd(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        exe = Path(tmp.name) / "NemiCLI.exe"
        gestartet = []
        with patch.object(sys, "frozen", True, create=True), patch("paths.exe_ohne_fenster", lambda: exe), \
                patch.object(kontextmenue.subprocess, "Popen", lambda args, **k: gestartet.append(args)):
            self.assertTrue(kontextmenue.fenster_starten())
        self.assertEqual([[str(exe)]], gestartet)

    def test_ohne_argument_nur_hilfe(self):
        with patch("builtins.print"):
            self.assertEqual(1, kontextmenue.main([]))


class WacheErledigtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.sp = S.Speicher(Path(self.tmp.name) / "w.db"); self.addCleanup(self.sp.schliessen)
        alt = (zugang.motor, zugang._speicher)
        zugang.motor, zugang._speicher = None, self.sp
        self.addCleanup(lambda: (setattr(zugang, "motor", alt[0]), setattr(zugang, "_speicher", alt[1])))
        p = patch("protokoll.schreibe", lambda *a, **k: None); p.start(); self.addCleanup(p.stop)

    def alarm(self, aid, status, tage):
        zeit = time.time() - tage * 86400
        e = E.Ereignis(E.PROZESS, "process_start", aid, zeit=zeit, prozess=f"{aid}.exe")
        self.sp.ereignisse_schreiben([e])
        a = E.Alarm(aid, "x", E.Schwere.HOCH, "R008", ereignis_id=e.id, zeit=zeit, status=status, subjekt=aid)
        a.id = aid
        self.sp.alarme_schreiben([a])
        return e

    def test_aufraeumen_behaelt_echte_und_ihr_ereignis(self):
        e_echt = self.alarm("echt1", "echt", 30)
        self.alarm("erledigt1", "erledigt", 30)
        self.alarm("unklar1", "gesehen", 30)
        self.alarm("harmlos30", "harmlos", 30)
        self.alarm("harmlos100", "harmlos", 100)
        self.alarm("offen1", "offen", 30)
        self.sp.aufraeumen(14, 200_000)
        self.assertEqual({"echt1", "erledigt1", "harmlos30", "offen1"}, {a.id for a in self.sp.alarme(limit=50)})
        self.assertIsNotNone(self.sp.ereignis(e_echt.id))

    def test_erledigt_nur_fuer_echte_und_daempft_nicht(self):
        self.alarm("a1", "harmlos", 1)
        self.assertIn("nur Alarme, die als echt", W.bewerten({"id": "a1", "urteil": "erledigt",
                                                                "begruendung": "Ist behoben worden."}))
        self.alarm("b1", "echt", 1)
        text = W.bewerten({"id": "b1", "urteil": "behoben", "begruendung": "Datei gelöscht, Rechner sauber."},
                          wer="Nutzer")
        self.assertIn("erledigt", text)
        self.assertEqual("erledigt", self.sp.alarm("b1").status)
        st = self.sp.alarm_statistik()
        self.assertEqual(1, st.get("erledigt")); self.assertEqual(0, st.get("echt", 0))
        for i in range(3):
            self.alarm(f"h{i}", "harmlos", 1)
            self.sp._c.execute("UPDATE alarme SET subjekt='b1' WHERE id=?", (f"h{i}",))
        self.sp._c.commit()
        self.assertEqual({}, self.sp.gedaempft(3))                   # ein erledigter Fund hebt Dämpfung auf


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.datei = Path(self.tmp.name) / "nemicli.config.json"
        p = patch.object(config, "_FILE", self.datei); p.start(); self.addCleanup(p.stop)
        self.gewarnt = []
        p = patch("ui.warn", lambda t: self.gewarnt.append(t)); p.start(); self.addCleanup(p.stop)
        config._gemeldet.clear()

    def test_kaputte_config_verliert_keine_einstellungen(self):
        self.assertTrue(config.save({"model": "a", "theme": "dunkel"}))
        self.assertTrue(config.update(model="b"))
        self.datei.write_text('{"model": "b", "the', encoding="utf-8")       # Absturz beim Schreiben
        self.assertEqual({"model": "a", "theme": "dunkel"}, config.load())
        config.update(kugel_leben=False)
        self.assertEqual({"model": "a", "theme": "dunkel", "kugel_leben": False}, config.load())
        self.assertFalse(self.datei.with_name("nemicli.config.json.tmp").exists())

    def test_geloeschte_config_faengt_frisch_an(self):
        config.save({"model": "a"}); config.save({"model": "b"})
        self.datei.unlink()
        self.assertEqual({}, config.load())

    def test_fehler_wird_gemeldet(self):
        with patch.object(config, "_FILE", Path(self.tmp.name) / "fehlt" / "nemicli.config.json"):
            self.assertFalse(config.update(model="x"))
            self.assertFalse(config.update(model="y"))
        self.assertTrue(config.letzter_fehler)
        self.assertEqual(1, len(self.gewarnt))                         # einmal, nicht bei jedem Versuch


class LernTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        for name, wert in (("SNIPPETS_DIR", Path(self.tmp.name) / "snippets"),
                           ("SKILLS_DIR", Path(self.tmp.name) / "skills")):
            p = patch.object(learn, name, wert); p.start(); self.addCleanup(p.stop)

    def verlauf(self, mit_fremd: bool):
        import fremddaten
        code = "```python\nimport os\nprint(os.listdir('.'))\n```"
        m = [{"role": "user", "content": "Wie liste ich Dateien?"}]
        if mit_fremd:
            m.append({"role": "user", "content": fremddaten.rahmen("import os # aus dem Netz", "web", "x.de")})
        m.append({"role": "assistant", "content": "So geht es:\n" + code})
        return m

    def test_nur_ohne_fremdinhalt(self):
        self.assertEqual(0, learn.capture_from_answer("Wie liste ich Dateien?", self.verlauf(True)))
        self.assertEqual(1, learn.capture_from_answer("Wie liste ich Dateien?", self.verlauf(False)))
        with patch.object(learn, "MAX_SNIPPETS", 1):
            m = self.verlauf(False)
            m[-1]["content"] = m[-1]["content"].replace("'.'", "'..'")
            self.assertEqual(0, learn.capture_from_answer("Wie liste ich Dateien?", m))


if __name__ == "__main__":
    unittest.main()
