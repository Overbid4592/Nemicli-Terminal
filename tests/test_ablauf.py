"""Abläufe: Befehlsfolgen aufnehmen, wiedererkennen, abspielen und dabei dazulernen (tools/ablauf.py)."""
import ast
import asyncio
import contextvars
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for d in ("tests", "core", "tools", "engines", "ui"):
    if str(ROOT / d) not in sys.path:
        sys.path.insert(0, str(ROOT / d))

import ablauf as AB      # noqa: E402
import commands as C     # noqa: E402
import main_quelle       # noqa: E402
import notstopp          # noqa: E402

STUNDE = 3600


class _Basis(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(AB, "DATEI", Path(tmp.name) / "ablauf.json"),
                  mock.patch.object(AB, "vorschlaege_an", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        AB._zuletzt.clear()
        AB._vorschlag.clear()
        self.zeit = 1_000_000.0

    def folge(self, *zeilen, pause: float = STUNDE) -> list[str]:
        """Eine Sitzung: Zeilen im Minutenabstand, davor eine lange Pause. Gibt alle Hinweise zurück."""
        self.zeit += pause
        hinweise = []
        for z in zeilen:
            self.zeit += 60
            hinweise += AB.aufzeichnen(z, jetzt=self.zeit)
        return hinweise


class Kennzeichnung(unittest.TestCase):
    def test_nur_die_markierte_zeile_zaehlt(self):
        def lauf():
            AB.markieren()
            erste = AB.nimm()
            return erste, AB.nimm()          # verschachtelter Aufruf: zählt nicht mehr
        self.assertEqual(contextvars.copy_context().run(lauf), (True, False))
        self.assertFalse(contextvars.copy_context().run(AB.nimm))


class Erkennen(_Basis):
    def test_dritte_gleiche_folge_wird_vorgeschlagen(self):
        self.assertEqual(self.folge("/theme dunkel", "/kugel", "/office"), [])
        self.assertEqual(self.folge("/theme dunkel", "/kugel", "/office"), [])
        self.assertEqual(self.folge("/help", "/theme dunkel", "/kugel", "/office"), [])
        hinweise = self.folge("/bild")       # die vorige Folge ist zu Ende: jetzt geprüft, die ganze Reihe
        self.assertEqual(len(hinweise), 1)
        self.assertIn("/theme dunkel → /kugel → /office", hinweise[0])
        self.assertEqual(AB.speichern("morgen"), ["/theme dunkel", "/kugel", "/office"])
        self.folge("/theme dunkel", "/kugel", "/office")
        self.assertEqual(self.folge("/bild"), [])                 # gespeichert: kein Vorschlag mehr

    def test_einmal_vorgeschlagen_bleibt_still(self):
        for _ in range(3):
            self.folge("/a", "/b")
        self.assertTrue(self.folge("/c"))
        self.folge("/a", "/b")
        self.assertEqual(self.folge("/c"), [])

    def test_kurze_pause_ist_dieselbe_folge(self):
        self.folge("/a", "/b")
        self.folge("/a", "/b", pause=60)      # gehört zur vorigen Folge
        self.folge("/a", "/b")
        self.assertEqual(self.folge("/c"), [])
        self.folge("/a", "/b")
        self.assertTrue(self.folge("/c"))

    def test_steuerbefehle_und_wiederholungen_nicht(self):
        for _ in range(4):
            self.assertEqual(self.folge("/help", "/help", "/exit", "/ablauf liste", "/restart"), [])
        self.assertEqual([z for _t, z in AB._laden()["verlauf"]], ["/help", "/help"] * 4)

    def test_schluessel_nie_gespeichert(self):
        AB.aufnehmen("geheim")
        for z in ("/web key sk-abcdefghijklmnop1234", "mein token ist hf_AbCdEfGhIjKlMnOp",
                  "/model x " + "Q" * 40, "/schluessel 30"):
            AB.aufzeichnen(z, slash=z.startswith("/"), jetzt=1)
        AB.aufzeichnen("/theme dunkel", jetzt=2)
        self.assertEqual(AB.stopp(), ("geheim", ["/theme dunkel"]))
        self.assertEqual([z for _t, z in AB._laden()["verlauf"]], ["/theme dunkel"])
        self.assertFalse(AB.abspielbar("/schluessel 30"))
        self.assertTrue(AB.aufzeichnen("/start C:/Users/example/Desktop/Projekte/ein_ordner", jetzt=3) == [])
        self.assertIn("/start C:/Users/example/Desktop/Projekte/ein_ordner", [z for _t, z in AB._laden()["verlauf"]])

    def test_aus_heisst_keine_vorschlaege(self):
        with mock.patch.object(AB, "vorschlaege_an", return_value=False):
            for _ in range(5):
                self.assertEqual(self.folge("/a", "/b"), [])

    def test_vergessen_leert_nur_den_verlauf(self):
        AB.speichern("x", ["/a", "/b"])
        self.folge("/c")
        self.assertEqual(AB.vergessen(), 1)
        self.assertEqual(AB.namen(), ["x"])


class Aufnehmen(_Basis):
    def test_aufnahme_mit_chat_bis_stopp(self):
        AB.aufnehmen("abend")
        self.assertEqual(AB.aufnahme_laeuft(), "abend")
        AB.aufzeichnen("/model gguf:test", jetzt=1)
        AB.aufzeichnen("Was steht heute an?", slash=False, jetzt=2)
        AB.aufzeichnen("/ablauf stopp", jetzt=3)          # Steuerbefehl: nicht aufgenommen
        self.assertEqual(AB.stopp(), ("abend", ["/model gguf:test", "Was steht heute an?"]))
        self.assertEqual(AB.schritte("abend"), ["/model gguf:test", "Was steht heute an?"])
        self.assertEqual(AB.aufnahme_laeuft(), "")

    def test_leere_aufnahme_speichert_nichts(self):
        AB.aufnehmen("leer")
        self.assertEqual(AB.stopp(), ("leer", []))
        self.assertEqual(AB.namen(), [])

    def test_namen(self):
        for schlecht in ("", "liste", "stopp", "a b", "x" * 40, "_x"):
            with self.assertRaises(ValueError):
                AB.name_pruefen(schlecht)
        self.assertEqual(AB.name_pruefen("Morgen-Ü"), "morgen-ü")
        AB.speichern("doppelt", ["/a"])
        with self.assertRaises(ValueError):
            AB.speichern("doppelt", ["/b"])
        with self.assertRaises(ValueError):
            AB.speichern("nichts")                          # kein Vorschlag da


class Dazulernen(_Basis):
    def test_fehler_je_schritt_und_danach_anhaengen(self):
        AB.speichern("morgen", ["/theme dunkel", "/kugel"])
        hinweise = []
        for _ in range(AB.DANACH_AB):
            AB.lauf_ende("morgen", [1], jetzt=self.zeit)
            self.zeit += 30
            hinweise += AB.aufzeichnen("/office", jetzt=self.zeit)
            self.zeit += STUNDE
        self.assertEqual(len(hinweise), 1)
        self.assertIn("/ablauf anhaengen morgen", hinweise[0])
        zeilen = "\n".join(AB.liste_zeilen())
        self.assertIn(f"{AB.DANACH_AB}× abgespielt", zeilen)
        self.assertIn(f"2. /kugel   ⚠ {AB.DANACH_AB}× mit Fehler", zeilen)
        self.assertEqual(AB.anhaengen("morgen"), "/office")
        self.assertEqual(AB.schritte("morgen"), ["/theme dunkel", "/kugel", "/office"])

    def test_nur_der_erste_befehl_kurz_danach(self):
        AB.speichern("m", ["/a"])
        AB.lauf_ende("m", [], jetzt=self.zeit)
        AB.aufzeichnen("/b", jetzt=self.zeit + AB.PAUSE_S + 5)  # zu spät
        AB.aufzeichnen("/c", jetzt=self.zeit + AB.PAUSE_S + 10)
        self.assertEqual(AB._laden()["ablaeufe"]["m"]["danach"], {})


class Abspielen(_Basis):
    def setUp(self):
        super().setUp()
        notstopp._test_reset()
        self.addCleanup(notstopp._test_reset)

    def _ui(self):
        return NS(FEHLER_ZAHL=0, info=lambda *a: None, success=lambda *a: None, warn=lambda *a: None,
                  error=lambda *a: None, console=NS(print=lambda *a, **k: None))

    def _funktion(self, ns):
        ns.setdefault("notstopp", notstopp)
        baum = ast.parse(main_quelle.text())
        knoten = [n for n in baum.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_ablauf_abspielen"]
        self.assertEqual(len(knoten), 1)
        exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), ns)
        return ns["_ablauf_abspielen"]

    def test_schritte_nacheinander_fehler_gezaehlt(self):
        AB.speichern("m", ["/theme dunkel", "/kaputt", "/kugel"])
        ui = self._ui()
        gelaufen = []

        async def handle_line(ctx, zeile):
            gelaufen.append(zeile)
            if zeile == "/kaputt":
                ui.FEHLER_ZAHL += 1
        abspielen = self._funktion({"ablauf": AB, "ui": ui, "handle_line": handle_line})
        asyncio.run(abspielen(None, "m"))
        self.assertEqual(gelaufen, ["/theme dunkel", "/kaputt", "/kugel"])
        self.assertEqual(AB._laden()["ablaeufe"]["m"]["fehler"], {"1": 1})

    def test_esc_haelt_vor_dem_naechsten_schritt(self):
        AB.speichern("m", ["/a", "/b", "/c"])
        gelaufen = []

        async def handle_line(ctx, zeile):
            gelaufen.append(zeile)
            self.assertTrue(notstopp.runde_laeuft())         # ESC greift während des Ablaufs
            notstopp.ausloesen()
        abspielen = self._funktion({"ablauf": AB, "ui": self._ui(), "handle_line": handle_line})
        asyncio.run(abspielen(None, "m"))
        self.assertEqual(gelaufen, ["/a"])
        self.assertFalse(notstopp.runde_laeuft())
        self.assertEqual(AB._laden()["ablaeufe"]["m"]["laeufe"], 0)   # angehalten zählt nicht als Lauf

    def test_abbruch_der_aufgabe_geht_durch(self):
        AB.speichern("m", ["/a", "/b"])
        gelaufen = []

        async def handle_line(ctx, zeile):
            gelaufen.append(zeile)
            raise asyncio.CancelledError
        abspielen = self._funktion({"ablauf": AB, "ui": self._ui(), "handle_line": handle_line})
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(abspielen(None, "m"))
        self.assertEqual(gelaufen, ["/a"])
        self.assertFalse(notstopp.runde_laeuft())


class Einbindung(unittest.TestCase):
    def test_handle_line_nimmt_die_marke_zuerst(self):
        baum = ast.parse(main_quelle.text())
        f = next(n for n in baum.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_line")
        erste = f.body[1] if isinstance(f.body[0], ast.Expr) and isinstance(f.body[0].value, ast.Constant) \
            else f.body[0]
        self.assertIn("ablauf.nimm()", ast.unparse(erste))

    def test_getippte_zeilen_markiert(self):
        for datei in ("ui/screen.py", "ui/screen_tx.py"):
            self.assertIn("_nutzer_markieren()", (ROOT / datei).read_text(encoding="utf-8"), datei)
        self.assertIn("await nutzer_zeile(ctx, text)", (ROOT / "main_start.py").read_text(encoding="utf-8"))

    def test_befehl_bekannt_und_fuer_die_ki_gesperrt(self):
        self.assertIn("/ablauf", {c.name for c in C.COMMAND_LIST})
        self.assertIn("/ablauf", C.MENUE_GESPERRT)
        self.assertTrue(any("/ablauf" in t[2] for t in C.THEMEN))
        self.assertIn("aufnehmen", C.hilfe_fuer_ki("/ablauf"))


if __name__ == "__main__":
    unittest.main()
