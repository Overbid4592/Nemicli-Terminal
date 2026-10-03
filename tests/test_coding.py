"""Offline-Tests für den Coding-Assistenten (core/coding.py + Anbindung).

python -m unittest discover -s tests -p test_coding.py
"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import config        # noqa: E402
import coding        # noqa: E402
import modes         # noqa: E402

LISTE = {
    "aktion": "anlegen",
    "ziel": "Kleines Notiz-Tool",
    "muss": ["startet mit start.bat"],
    "nicht": ["keine Cloud"],
    "recherche": "nicht nötig",
    "punkte": [
        {"text": "venv anlegen", "pruefung": "Python im venv läuft",
         "befehl": "venv\\Scripts\\python.exe --version", "erwartet": "Python 3"},
        {"text": "README schreiben", "pruefung": "README beschreibt den Start"},
    ],
}


def befehl_ok(ausgabe="Python 3.12.1"):
    return lambda cmd: (True, ausgabe)


def befehl_fehler(cmd):
    return False, "Befehl fehlgeschlagen (Exitcode 1)."


class CodingBasis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.projekt = Path(self._tmp.name) / "notiztool"
        self._daten = {}
        self._p = [mock.patch.object(config, "load", lambda: dict(self._daten)),
                   mock.patch.object(config, "update", self._update)]
        for p in self._p:
            p.start()
        modes.set_mode("normal")

    def _update(self, **kw):
        self._daten.update(kw)

    def tearDown(self):
        for p in self._p:
            p.stop()
        modes.set_mode("normal")
        self._tmp.cleanup()

    def starten_und_anlegen(self):
        coding.starten(str(self.projekt), "Notiz-Tool bauen")
        coding.ausfuehren(dict(LISTE), befehl_ok())
        return coding.laden()


class StartenUndBeenden(CodingBasis):
    def test_am_anfang_aus(self):
        self.assertFalse(coding.aktiv())
        self.assertEqual(coding.prompt_hinweis(), "")
        self.assertEqual(coding.status_text(), "")

    def test_starten_auch_wenn_ordner_noch_fehlt(self):
        erg = coding.starten(str(self.projekt), "x")
        self.assertTrue(coding.aktiv())
        self.assertEqual(coding.projekt(), self.projekt.resolve())
        self.assertFalse(self.projekt.exists())          # angelegt wird erst mit der Liste
        self.assertIsNone(erg["liste"])

    def test_sammelordner_und_laufwerk_abgelehnt(self):
        with self.assertRaises(ValueError):
            coding.starten(str(Path.home() / "Desktop"))
        with self.assertRaises(ValueError):
            coding.starten(Path(self._tmp.name).anchor)
        with self.assertRaises(ValueError):
            coding.starten("")

    def test_beenden_laesst_ablage_liegen(self):
        self.starten_und_anlegen()
        alt = coding.beenden()
        self.assertFalse(coding.aktiv())
        self.assertEqual(alt, self.projekt.resolve())
        self.assertTrue((self.projekt / ".nemicli" / "todo.json").exists())

    def test_neustart_findet_bestehende_liste(self):
        self.starten_und_anlegen()
        coding.beenden()
        erg = coding.starten(str(self.projekt))
        self.assertIsNotNone(erg["liste"])

    def test_ende_ansage(self):
        for text in ("Coding aus", "mach bitte coding aus", "Coding-Assistent beenden",
                     "stopp den coding modus", "beende coding"):
            self.assertTrue(coding.ist_ende_ansage(text), text)
        for text in ("wir coden heute aus Spaß", "Coding ausprobieren", "leg ein venv an"):
            self.assertFalse(coding.ist_ende_ansage(text), text)


class Liste(CodingBasis):
    def test_anlegen_schreibt_ablage(self):
        daten = self.starten_und_anlegen()
        ablage = self.projekt / ".nemicli"
        for name in ("todo.json", "Todo.md", "Auftrag.md"):
            self.assertTrue((ablage / name).exists(), name)
        self.assertEqual([p["status"] for p in daten["punkte"]], ["offen", "offen"])
        self.assertIn("keine Cloud", (ablage / "Auftrag.md").read_text(encoding="utf-8"))

    def test_anlegen_braucht_pruefkriterium(self):
        coding.starten(str(self.projekt))
        kaputt = dict(LISTE, punkte=[{"text": "irgendwas"}])
        with self.assertRaisesRegex(ValueError, "pruefung"):
            coding.ausfuehren(kaputt, befehl_ok())
        with self.assertRaisesRegex(ValueError, "ziel"):
            coding.ausfuehren(dict(LISTE, ziel=""), befehl_ok())

    def test_ohne_start_keine_liste(self):
        with self.assertRaises(ValueError):
            coding.ausfuehren(dict(LISTE), befehl_ok())

    def test_nur_ein_punkt_in_arbeit(self):
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "arbeiten", "nr": 1}, befehl_ok())
        coding.ausfuehren({"aktion": "arbeiten", "nr": 2}, befehl_ok())
        self.assertEqual(coding.laden()["aktuell"], 2)

    def test_frage_macht_orange_und_blockiert(self):
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "arbeiten", "nr": 1}, befehl_ok())
        coding.ausfuehren({"aktion": "frage", "nr": 1, "frage": "Welche Python-Version?"}, befehl_ok())
        daten = coding.laden()
        self.assertEqual(daten["punkte"][0]["status"], "frage")
        self.assertIsNone(daten["aktuell"])
        with self.assertRaises(ValueError):
            coding.ausfuehren({"aktion": "arbeiten", "nr": 1}, befehl_ok())
        coding.ausfuehren({"aktion": "geklaert", "nr": 1, "antwort": "3.12"}, befehl_ok())
        self.assertEqual(coding.laden()["punkte"][0]["status"], "offen")

    def test_neu_haengt_an(self):
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "neu", "text": "Aufräumen", "pruefung": "keine alten Dateien"},
                          befehl_ok())
        self.assertEqual(coding.laden()["punkte"][-1]["nr"], 3)

    def test_streichen_braucht_grund_und_zaehlt_nicht(self):
        self.starten_und_anlegen()
        with self.assertRaises(ValueError):
            coding.ausfuehren({"aktion": "streichen", "nr": 2}, befehl_ok())
        coding.ausfuehren({"aktion": "streichen", "nr": 2, "grund": "nicht gebraucht"}, befehl_ok())
        self.assertEqual(coding.zaehlen(coding.laden()), (0, 1))


class Gruen(CodingBasis):
    """Grün setzt nur der Prüfbefehl oder der Nutzer – nie die KI."""

    def test_pruefbefehl_erfolgreich(self):
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "arbeiten", "nr": 1}, befehl_ok())
        text = coding.ausfuehren({"aktion": "pruefen", "nr": 1}, befehl_ok())
        self.assertIn("🟢", text)
        daten = coding.laden()
        self.assertEqual(daten["punkte"][0]["status"], "fertig")
        self.assertIsNone(daten["aktuell"])
        self.assertIn("Prüfbefehl", daten["punkte"][0]["geprueft"])

    def test_pruefbefehl_fehlgeschlagen_bleibt_rot(self):
        self.starten_und_anlegen()
        text = coding.ausfuehren({"aktion": "pruefen", "nr": 1}, befehl_fehler)
        self.assertIn("NICHT bestanden", text)
        self.assertEqual(coding.laden()["punkte"][0]["status"], "offen")

    def test_erwarteter_text_fehlt_bleibt_rot(self):
        self.starten_und_anlegen()
        text = coding.ausfuehren({"aktion": "pruefen", "nr": 1}, befehl_ok("kein Python hier"))
        self.assertIn("erwarteter Text", text)
        self.assertEqual(coding.laden()["punkte"][0]["status"], "offen")

    def test_aendern_macht_wieder_rot(self):
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "pruefen", "nr": 1}, befehl_ok())
        coding.ausfuehren({"aktion": "aendern", "nr": 1, "befehl": "echo ok"}, befehl_ok())
        self.assertEqual(coding.laden()["punkte"][0]["status"], "offen")

    def test_kein_feld_setzt_status_direkt(self):
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "aendern", "nr": 2, "status": "fertig", "text": "x"}, befehl_ok())
        self.assertEqual(coding.laden()["punkte"][1]["status"], "offen")
        with self.assertRaises(ValueError):
            coding.ausfuehren({"aktion": "fertig", "nr": 2}, befehl_ok())

    def test_fortschritt_aendert_marke(self):
        self.starten_und_anlegen()
        vorher = coding.marke()
        coding.ausfuehren({"aktion": "arbeiten", "nr": 1}, befehl_ok())
        mitte = coding.marke()
        coding.ausfuehren({"aktion": "pruefen", "nr": 1}, befehl_ok())
        self.assertNotEqual(vorher, mitte)
        self.assertNotEqual(mitte, coding.marke())


class Freigabe(CodingBasis):
    """Liste abnicken und Prüfen ohne Befehl fragt in JEDEM Modus."""

    def setUp(self):
        super().setUp()
        import actions
        import sicherheit
        self.actions, self.sicherheit = actions, sicherheit
        self.starten_und_anlegen()

    def urteil(self, act):
        return modes.decide(act, self.actions.needs_confirm(act))

    def test_werkzeuge_registriert(self):
        self.assertIn("coding_start", self.actions.ACTIONS)
        self.assertIn("todo", self.actions.ACTIONS)

    def test_auto_fragt_trotzdem_bei_liste_und_nutzerpruefung(self):
        for modus in ("normal", "auto", "autoan"):
            modes.set_mode(modus)
            self.assertEqual(self.urteil(dict(LISTE, tool="todo")), "ask", modus)
            self.assertEqual(self.urteil({"tool": "todo", "aktion": "pruefen", "nr": 2}), "ask", modus)
            self.assertEqual(self.urteil({"tool": "todo", "aktion": "streichen", "nr": 2,
                                          "grund": "x"}), "ask", modus)

    def test_arbeiten_laeuft_ohne_frage(self):
        modes.set_mode("normal")
        self.assertEqual(self.urteil({"tool": "todo", "aktion": "arbeiten", "nr": 1}), "run")
        self.assertEqual(self.urteil({"tool": "coding_start", "projekt": str(self.projekt)}), "run")

    def test_pruefbefehl_folgt_den_regeln_von_befehl(self):
        act = {"tool": "todo", "aktion": "pruefen", "nr": 1}
        befehl = {"tool": "befehl", "befehl": LISTE["punkte"][0]["befehl"]}
        for modus in ("normal", "auto"):
            modes.set_mode(modus)
            self.assertEqual(self.urteil(act), self.urteil(befehl), modus)
        self.assertEqual(self.sicherheit.stufe(act), self.sicherheit.stufe(befehl))

    def test_nie_immer_erlauben(self):
        for act in (dict(LISTE, tool="todo"), {"tool": "todo", "aktion": "pruefen", "nr": 2},
                    {"tool": "todo", "aktion": "pruefen", "nr": 1}):
            keys = [k for k, _ in self.sicherheit.optionen(act)]
            self.assertNotIn("always", keys)

    def test_lesemodus_sperrt(self):
        modes.set_mode("lesen")
        self.assertEqual(self.urteil({"tool": "todo", "aktion": "arbeiten", "nr": 1}), "block")

    def test_beschreibung_zeigt_ganze_liste(self):
        text = self.actions.describe(dict(LISTE, tool="todo"))
        self.assertIn("venv anlegen", text)
        self.assertIn("README schreiben", text)

    def test_helfer_duerfen_die_liste_nicht_fuehren(self):
        import subagents
        self.assertIn("todo", subagents._VERBOTEN)
        self.assertIn("coding_start", subagents._VERBOTEN)


class PromptUndAnzeige(CodingBasis):
    def test_prompt_zeigt_weg_und_stand(self):
        self.starten_und_anlegen()
        block = coding.prompt_hinweis()
        self.assertIn("Coding-Assistent ist AN", block)
        self.assertIn("venv anlegen", block)
        self.assertIn("Abweichung vom Auftrag", block)

    def test_prompt_ohne_liste_nennt_aufgabe(self):
        coding.starten(str(self.projekt), "Notiz-Tool bauen")
        self.assertIn("Notiz-Tool bauen", coding.prompt_hinweis())

    def test_alles_gruen_verlangt_abschluss(self):
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "pruefen", "nr": 1}, befehl_ok())
        coding.ausfuehren({"aktion": "pruefen", "nr": 2}, befehl_ok())   # Nutzer hat bestätigt
        self.assertTrue(coding.fertig(coding.laden()))
        self.assertIn("Abschluss-Bericht", coding.prompt_hinweis())

    def test_systemprompt_kennt_werkzeuge_und_block(self):
        import persona
        self.starten_und_anlegen()
        text = persona.build_system_prompt()
        self.assertIn("coding_start       Felder", text)
        self.assertTrue(text.rstrip().endswith(coding.prompt_hinweis().rstrip()))

    def test_todo_panel_rendert(self):
        import ui
        from rich.console import Console
        self.starten_und_anlegen()
        coding.ausfuehren({"aktion": "frage", "nr": 2, "frage": "Englisch oder Deutsch?"}, befehl_ok())
        con = Console(width=100, record=True, color_system=None, file=io.StringIO())
        con.print(ui.todo_panel(coding.laden()))
        out = con.export_text()
        self.assertIn("Todo · 0/2 geprüft", out)
        self.assertIn("Englisch oder Deutsch?", out)

    def test_status_text(self):
        self.starten_und_anlegen()
        self.assertEqual(coding.status_text(), "Coding · notiztool · 0/2")

    def test_todo_json_ist_lesbar(self):
        self.starten_und_anlegen()
        roh = json.loads((self.projekt / ".nemicli" / "todo.json").read_text(encoding="utf-8"))
        self.assertEqual(roh["ziel"], "Kleines Notiz-Tool")


class Befehle(unittest.TestCase):
    def test_code_befehle_registriert(self):
        from commands import COMMAND_LIST
        namen = [c.name for c in COMMAND_LIST]
        self.assertIn("/code", namen)
        self.assertIn("/codeend", namen)


if __name__ == "__main__":
    unittest.main()
