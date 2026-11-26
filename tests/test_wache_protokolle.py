"""Sensor für Ereignisprotokolle: XML lesen, beim ersten Lauf nur den Stand merken, R017, PowerShell nur als Beleg, Inventar-Anstoß.
Die Protokolle sind Attrappen – es wird kein echtes Windows-Protokoll gelesen."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

from wache import protokolle as P, regeln as R                                 # noqa: E402

NS = "http://schemas.microsoft.com/win/2004/08/events/event"


def xml(eid: int, rec: int, daten: dict | None = None, userdata: str = "") -> str:
    felder = "".join(f'<Data Name="{k}">{v}</Data>' for k, v in (daten or {}).items())
    return (f'<Event xmlns="{NS}"><System><EventID>{eid}</EventID><EventRecordID>{rec}</EventRecordID>'
            f'<TimeCreated SystemTime="2026-10-27T12:00:00.000Z"/></System>'
            + (f"<EventData>{felder}</EventData>" if daten is not None else "")
            + (f"<UserData>{userdata}</UserData>" if userdata else "") + "</Event>")


class LesenTests(unittest.TestCase):
    def test_eventdata_und_userdata(self):
        d = P.lesen(xml(7045, 12, {"ServiceName": "Neu", "ImagePath": r"C:\Programme\neu.exe -k"}))
        self.assertEqual(12, d["rec"])
        self.assertEqual("Neu", d["felder"]["ServiceName"])
        d = P.lesen(xml(5861, 3, userdata='<Op xmlns="x"><Namespace>root</Namespace><CONSUMER>C1</CONSUMER></Op>'))
        self.assertEqual("C1", d["felder"]["CONSUMER"])
        self.assertIsNone(P.lesen("<kaputt"))


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.kanaele: dict[str, list[str]] = {k: [] for k, *_ in P.QUELLEN}
        self.s = P.ProtokollSensor(abfragen=lambda kanal, _x, _n: list(reversed(self.kanaele[kanal])))

    def test_erster_lauf_meldet_altes_nicht(self):
        self.kanaele["System"] = [xml(7045, 1, {"ServiceName": "Alt", "ImagePath": "a.exe"})]
        self.assertEqual([], self.s.sammeln())
        self.kanaele["System"].append(xml(7045, 2, {"ServiceName": "Neu", "ImagePath": r"C:\x\neu.exe"}))
        ev = self.s.sammeln()
        self.assertEqual(["evtlog_dienst_installiert"], [e.aktion for e in ev])
        self.assertIn("Neu", ev[0].text)
        self.assertEqual([], self.s.sammeln())                       # nichts doppelt

    def test_geleertes_protokoll_meldet_wieder(self):
        self.kanaele["System"] = [xml(7045, 500, {"ServiceName": "Alt", "ImagePath": "a.exe"})]
        self.s.sammeln()
        self.kanaele["System"] = [xml(7045, 3, {"ServiceName": "Neu", "ImagePath": r"C:\x\neu.exe"})]
        ev = self.s.sammeln()
        self.assertEqual(["evtlog_dienst_installiert"], [e.aktion for e in ev])
        self.assertEqual([], self.s.sammeln())

    def test_eigener_powershell_rahmen_bleibt_draussen(self):
        kanal = "Microsoft-Windows-PowerShell/Operational"
        self.s.sammeln()
        self.kanaele[kanal] = [xml(4104, 1, {"ScriptBlockText": R.EIGENER_RAHMEN + "; Get-Date"}),
                               xml(4104, 2, {"ScriptBlockText": "IEX (fremd)", "Path": r"C:\x\a.ps1"})]
        ev = self.s.sammeln()
        self.assertEqual(1, len(ev))
        self.assertEqual(r"C:\x\a.ps1", ev[0].datei)


class RegelTests(unittest.TestCase):
    def test_r017_und_r018(self):
        rw = R.Regelwerk()
        wmi = P.ereignis("wmi_dauerabo", P.lesen(xml(5861, 1, userdata=(
            '<Op xmlns="x"><Namespace>root\\subscription</Namespace><CONSUMER>CommandLineEventConsumer="x"</CONSUMER>'
            "</Op>"))))
        self.assertEqual(["R017"], [a.regel for a in rw.pruefen(wmi)])
        ps = P.ereignis("powershell_verdaechtig", P.lesen(xml(4104, 1, {"ScriptBlockText": "IEX x", "Path": "a.ps1"})))
        self.assertEqual([], rw.pruefen(ps))                         # nur Beleg: auf Alltagsrechnern Hunderte am Tag
        self.assertNotIn("R017", R.MENGENREGELN)
        for r in ("R017", "VS-001"):
            self.assertIn(r, R.BESCHREIBUNG)

    def test_installation_ohne_regel_aber_mit_inventar(self):
        from wache import motor as M
        m = M.Motor.__new__(M.Motor)                                 # nur die Bewertung, kein echter Motor
        m.e = {"ml": True, "dl": True, "netz": True}
        m.regeln = R.Regelwerk()
        m._fehler = lambda *_a: None
        m._anreichern = lambda _e: None
        m.detektor = mock.Mock(side_effect=AssertionError("kein ML"))
        angestossen = []
        m._inventar_anstossen = lambda: angestossen.append(1)
        e = P.ereignis("dienst_installiert", P.lesen(xml(7045, 1, {"ServiceName": "N", "ImagePath": "n.exe"})))
        alarme = []
        m._bewerten_in(e, alarme)
        self.assertEqual([], alarme)
        self.assertEqual([1], angestossen)


if __name__ == "__main__":
    unittest.main()
