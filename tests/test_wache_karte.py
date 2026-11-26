"""Wache-Karte der Startseite: zählt wie der Tray (offen, unklar, echt) und zählt laufend neu,
damit geschlossene Alarme nicht als offen stehen bleiben."""
import ast
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("tests", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import main_quelle                                  # noqa: E402
import startseite                                   # noqa: E402
from wache import ereignisse as E, speicher as S    # noqa: E402
from wache import zugang as Z                       # noqa: E402

NAMEN = {"wache_karte_text", "wache_karte_setzen", "wache_karte_auffrischen", "_wache_karte_takt"}


def _karte(takt: float = 60.0) -> dict:
    """Die Karten-Funktionen aus main_start, ohne das ganze Programm zu laden."""
    knoten = [n for n in ast.parse(main_quelle.text()).body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in NAMEN]
    ns = {"asyncio": asyncio, "WACHE_KARTE_TAKT_S": takt}
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main_start.py", "exec"), ns)
    return ns


class Karte(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sp = S.Speicher(Path(self.tmp.name) / "w.db")
        self.alt = startseite.STAND.get("wache")
        for p in (mock.patch.object(Z, "laeuft", return_value={"pid": 1}),
                  mock.patch.object(Z, "speicher", return_value=self.sp),
                  mock.patch.object(startseite, "auffrischen", lambda: None)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        startseite.STAND["wache"] = self.alt
        self.sp.schliessen()
        self.tmp.cleanup()

    def alarme(self, n: int) -> list:
        a = [E.Alarm(f"t{i}", "x", E.Schwere.HOCH, "R001") for i in range(n)]
        self.sp.alarme_schreiben(a)
        return a

    def test_text_wie_tray(self):
        self.assertEqual("🟢 alles ruhig", startseite.wache_text(True, {"offen": 0, "unklar": 0}))
        self.assertIn("2 unklar", startseite.wache_text(True, {"unklar": 2}))
        self.assertTrue(startseite.wache_text(True, {"unklar": 2}).startswith("🟡"))
        self.assertIn("3 Alarme offen · 1 unklar", startseite.wache_text(True, {"offen": 3, "unklar": 1}))
        self.assertTrue(startseite.wache_text(True, {"echt": 1, "unklar": 4}).startswith("🔴"))

    def test_lage_zaehlt_unklar(self):
        a = self.alarme(3)
        self.sp.alarm_urteil(a[0].id, "gesehen", "Cloud-Modell", "unklar")
        self.sp.alarm_urteil(a[1].id, "gesehen", "Wache", "übergeben")        # nur weitergereicht: nicht unklar
        lage = self.sp.lage()
        self.assertEqual((1, 2, 1), (lage["offen"], lage["gesehen"], lage["unklar"]))
        with mock.patch.object(S.time, "time", return_value=time.time() + (S.UNKLAR_TAGE + 1) * 86400):
            self.assertEqual(0, self.sp.lage()["unklar"])                     # altes „unklar“ zählt nicht mehr

    def test_nach_urteilen_gruen(self):
        a = self.alarme(2)
        k = _karte()
        k["wache_karte_auffrischen"]()
        self.assertTrue(startseite.STAND["wache"].startswith("🟡 2 Alarme offen"))
        for x in a:
            self.sp.alarm_urteil(x.id, "harmlos", "Cloud-Modell", "bekanntes Programm")
        k["wache_karte_auffrischen"]()
        self.assertEqual("🟢 alles ruhig", startseite.STAND["wache"])

    def test_wache_aus(self):
        with mock.patch.object(Z, "laeuft", return_value=None):
            _karte()["wache_karte_auffrischen"]()
        self.assertTrue(startseite.STAND["wache"].startswith("⚪"))

    def test_gleicher_text_zeichnet_nicht_neu(self):
        self.alarme(1)
        k = _karte()
        k["wache_karte_auffrischen"]()
        with mock.patch.object(startseite, "setzen") as setzen:
            k["wache_karte_auffrischen"]()
        setzen.assert_not_called()

    def test_takt_zaehlt_neu(self):
        a = self.alarme(1)
        k = _karte(takt=0.01)
        k["wache_karte_auffrischen"]()
        self.sp.alarm_urteil(a[0].id, "harmlos", "Cloud-Modell", "ok")

        async def lauf():
            t = asyncio.create_task(k["_wache_karte_takt"]())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if startseite.STAND["wache"] == "🟢 alles ruhig":
                    break
            t.cancel()
        asyncio.run(lauf())
        self.assertEqual("🟢 alles ruhig", startseite.STAND["wache"])

    def test_wache_befehl_frischt_auf(self):
        src = main_quelle.text()
        self.assertIn("await _wache_befehl(ctx, arg.strip())\n        wache_karte_auffrischen()", src)
        self.assertIn("wache_karte_beobachten()", src)


if __name__ == "__main__":
    unittest.main()
