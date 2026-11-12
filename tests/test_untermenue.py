"""Tests: Auswahl, wenn ein Slash-Befehl ohne Zusatz getippt wird.

python -m unittest tests.test_untermenue
"""

import ast
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402
for _d in ("core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import commands as C                                   # noqa: E402


def _funktionen(namespace):
    tree = ast.parse(main_quelle.text())
    namen = {"_untermenue", "_untermenue_eintraege", "_menue_aufbereiten", "_schalter_an", "_motive_stand", "_wahl_stand"}
    knoten = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name in namen]
    assert len(knoten) == len(namen)
    knoten += [n for n in tree.body if isinstance(n, ast.Assign)
               and any(getattr(z, "id", "") in ("_SCHALTER_NAME", "_SCHALTER_ZEICHEN") for z in n.targets)]
    namespace.setdefault("re", __import__("re"))
    namespace.setdefault("config", NS(load=lambda: {}))
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), namespace)
    return namespace


class UntermenueTests(unittest.TestCase):

    def lauf(self, cmd, wahl, antwort="", ctx=None):
        gefragt = {}

        async def ask_confirm(frage, optionen):
            gefragt["optionen"] = optionen
            return wahl(optionen) if callable(wahl) else wahl

        async def ask_text(frage):
            gefragt["frage"] = frage
            return antwort

        ns = _funktionen({"Ctx": object, "ask_confirm": ask_confirm, "ask_text": ask_text,
                          "modes": NS(current=lambda: "normal", MODES=[("chat", "💬", "Chatten"),
                                                                         ("normal", "⚙", "Normal")]),
                          "ui": NS(current_theme=lambda: "cyan", THEMES={"cyan": {"label": "Cyan"}, "amber": {}, "wald": {"label": "Grün"}},
                                   EINGEBAUT=frozenset({"cyan", "amber"})),
                          "M": NS(strength_menu=lambda ref: {"schnell": "flott", "stark": "gründlich"})})
        ctx = ctx or NS(strength_active=lambda: True, strength="stark", model="x")
        return asyncio.run(ns["_untermenue"](ctx, cmd)), gefragt

    def test_feste_auswahl_mit_zurueck(self):
        zusatz, g = self.lauf("/uebung", "1")
        self.assertEqual(zusatz, "jetzt")
        self.assertEqual(g["optionen"][-1][0], C.ZURUECK)
        self.assertEqual(self.lauf("/uebung", C.ZURUECK)[0], None)

    def test_status_eintrag_fuehrt_ohne_zusatz_aus(self):
        self.assertEqual(self.lauf("/sandbox", "0")[0], "")

    def test_nachfrage_setzt_angabe_ein(self):
        zusatz, g = self.lauf("/sandbox", "2", r"F:\Projekt X")
        self.assertEqual(zusatz, r"freigeben F:\Projekt X schreiben")
        self.assertEqual(g["frage"], "Welcher Ordner?")
        self.assertIsNone(self.lauf("/sandbox", "1", "")[0])          # leere Angabe = abbrechen
        self.assertEqual(self.lauf("/kugel", "1", "")[0], "malen")     # malen geht auch ohne

    def test_bild_mit_schritten(self):
        self.assertEqual(self.lauf("/bild", "2")[0], "schritte")
        self.assertEqual(self.lauf("/bild", "1", "ein Fuchs im Schnee")[0], "ein Fuchs im Schnee")
        self.assertIsNone(self.lauf("/bild", "1", "")[0])

    def test_auswahl_nach_aktuellem_stand(self):
        zusatz, g = self.lauf("/modus", "0")
        self.assertEqual(zusatz, "chat")
        self.assertTrue(g["optionen"][1][1].endswith("✔️"))
        self.assertEqual(self.lauf("/theme", "1")[0], "amber")
        _, g = self.lauf("/theme", "0")
        self.assertEqual(g["optionen"][0][1], "cyan  · Cyan  ✔️")
        self.assertEqual(g["optionen"][2][1], "wald  · Grün  ✨ eigenes")
        zusatz, g = self.lauf("/staerke", "1")
        self.assertEqual(zusatz, "stark")
        self.assertTrue(g["optionen"][1][1].endswith("✔️"))

    def test_video_menue_mit_modellen_und_ordner(self):
        from unittest import mock
        video = NS(gewaehlt=lambda: "WAN-GGUF", modelle=lambda: {"WAN-GGUF": {}, "WAN-Turbo": {}})
        motoren = NS(gewaehlt=lambda: None, aktiv=lambda: False, modelle=lambda: {}, merkmal=lambda n=None: "")
        # ohne die Video-Motoren dieses Rechners: ein Modell in Models/Videomodelle verschöbe die Einträge
        with mock.patch.dict(sys.modules, {"video": video, "videomotoren": motoren}):
            self.assertEqual(self.lauf("/video", "0")[0], "")                     # Stand
            zusatz, g = self.lauf("/video", "1", "a fox running in snow")
            self.assertEqual(zusatz, "a fox running in snow")
            self.assertIn("Video", g["frage"])
            self.assertEqual(self.lauf("/video", "2", "a fox")[0], "a fox --sekunden 10")
            self.assertIsNone(self.lauf("/video", "3", "")[0])                    # leer = abbrechen
            zusatz, g = self.lauf("/video", "5")
            self.assertEqual(zusatz, "modell WAN-Turbo")
            self.assertTrue(g["optionen"][4][1].endswith("✔️"))                  # gewähltes Modell
            werte = [o[1] for o in self.lauf("/video", "0")[1]["optionen"]]
            stelle = lambda text: str(next(i for i, t in enumerate(werte) if text in t))   # noqa: E731
            self.assertEqual(self.lauf("/video", stelle("Video-Ordner"))[0], "ordner")
            self.assertEqual(self.lauf("/video", stelle("Cloud-Anbieter hinzufügen"))[0], "cloud neu")

    def test_debug_menue_zeigt_stand(self):
        from unittest import mock
        with mock.patch.dict(sys.modules, {"debugbericht": NS(an=lambda: True, motor_log_an=lambda: False)}):
            zusatz, g = self.lauf("/debug", "2")
            self.assertEqual(zusatz, "motor an")                                  # ein Schalter: schaltet um
            self.assertTrue(g["optionen"][1][1].endswith("✔️"))                   # Bericht an
            self.assertTrue(g["optionen"][2][1].endswith("🛑"))                   # Motor-Log aus
            self.assertEqual(self.lauf("/debug", "1")[0], "aus")
        self.assertIn("/video", C.menue_befehle())
        self.assertIn("/debug", C.menue_befehle())

    def test_ki_oeffnet_den_store_aber_keine_app_direkt(self):
        self.assertEqual(("/appstore", ""), C.menue_pruefen("/appstore"))
        befehl, grund = C.menue_pruefen("/appstore debug wuerfel")
        self.assertIsNone(befehl)
        self.assertIn("würde direkt etwas ändern", grund)

    def test_befehl_ohne_menue_bleibt_unveraendert(self):
        self.assertEqual(self.lauf("/help", "0")[0], "")

    def test_alle_menues_gueltig(self):
        for cmd, eintraege in C.UNTERMENUES.items():
            self.assertTrue(eintraege, cmd)
            for zusatz, anzeige, frage in eintraege:
                self.assertTrue(anzeige, cmd)
                self.assertEqual("{}" in zusatz, frage is not None, (cmd, zusatz))
            self.assertIn(cmd, C.COMMANDS if cmd != "/schlüssel" else {"/schlüssel": 1}, cmd)



class HilfeFuerKI(unittest.TestCase):
    """Die KI bekommt dieselben Werte wie das Auswahlmenü – nichts zum Raten."""

    def setUp(self):
        stand = {"/theme": [("cyan", "✓ cyan", None), ("matrix", "   matrix", None)],
                 "/wache": C.UNTERMENUES["/wache"]}
        self._alt = C.AUSWAHL_JETZT
        C.AUSWAHL_JETZT = lambda cmd: stand.get(cmd, [])

    def tearDown(self):
        C.AUSWAHL_JETZT = self._alt

    def test_uebersicht_nennt_kurze_wertelisten_mit_aktivem_wert(self):
        text = C.hilfe_fuer_ki()
        zeile = next(z for z in text.splitlines() if z.startswith("- /theme"))
        self.assertIn("[Werte: cyan (aktiv) · matrix]", zeile)
        self.assertIn("keine anderen", text)
        wache = next(z for z in text.splitlines() if z.startswith("- /wache"))
        self.assertNotIn("[Werte", wache)                    # lange Menüs nur im Detail

    def test_detail_zeigt_laufenden_stand(self):
        text = C.hilfe_fuer_ki("/theme")
        self.assertIn("/theme cyan (✓ cyan)", text)
        self.assertIn("/theme matrix (matrix)", text)

    def test_fehler_im_stand_faellt_auf_feste_menues_zurueck(self):
        C.AUSWAHL_JETZT = lambda cmd: 1 / 0
        self.assertEqual(C.auswahl("/bild"), C.UNTERMENUES["/bild"])
        self.assertEqual(C.auswahl("/theme"), [])


if __name__ == "__main__":
    unittest.main()
