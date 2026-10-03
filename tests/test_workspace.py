"""Offline-Tests für /workspace – den festgenagelten Projektordner.

python -m unittest discover -s tests -p test_workspace.py
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import config          # noqa: E402
import modes           # noqa: E402
import workspace as WS  # noqa: E402


class WorkspaceBasis(unittest.TestCase):
    """Setzt einen echten Ordner im Temp-Verzeichnis als Workspace."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.projekt = Path(self._tmp.name) / "HTMLTEST"
        self.projekt.mkdir()
        self.draussen = Path(self._tmp.name) / "woanders"
        self.draussen.mkdir()
        # Config nur im Speicher verbiegen, nicht die echte Datei anfassen.
        self._daten = {}
        self._p_load = mock.patch.object(config, "load", lambda: dict(self._daten))
        self._p_update = mock.patch.object(config, "update", self._update)
        self._p_load.start()
        self._p_update.start()

    def _update(self, **kw):
        self._daten.update(kw)

    def tearDown(self):
        self._p_load.stop()
        self._p_update.stop()
        self._tmp.cleanup()


class SetzenUndBeenden(WorkspaceBasis):
    def test_am_anfang_ist_keiner_aktiv(self):
        self.assertFalse(WS.aktiv())
        self.assertIsNone(WS.pfad())

    def test_setzen_legt_die_ablage_an(self):
        erg = WS.setzen(self.projekt)
        self.assertTrue(WS.aktiv())
        self.assertEqual(WS.pfad(), self.projekt.resolve())
        ablage = self.projekt / ".workspace"
        self.assertTrue(ablage.is_dir())
        for datei in ("memory.md", "Absprache.md", "Dateien.md"):
            self.assertTrue((ablage / datei).exists(), datei)
        self.assertEqual(sorted(erg["angelegt"]),
                         ["Absprache.md", "Dateien.md", "memory.md"])

    def test_zweites_setzen_ueberschreibt_nichts(self):
        WS.setzen(self.projekt)
        mem = self.projekt / ".workspace" / "memory.md"
        mem.write_text("MEIN INHALT", encoding="utf-8")
        erg = WS.setzen(self.projekt)
        self.assertEqual(erg["angelegt"], [])          # nichts neu angelegt
        self.assertEqual(mem.read_text(encoding="utf-8"), "MEIN INHALT")

    def test_ohne_argument_zaehlt_der_aktuelle_ordner(self):
        with mock.patch.object(WS.os, "getcwd", return_value=str(self.projekt)):
            WS.setzen()
        self.assertEqual(WS.pfad(), self.projekt.resolve())

    def test_beenden_hebt_auf_und_laesst_die_ablage_liegen(self):
        WS.setzen(self.projekt)
        alt = WS.beenden()
        self.assertEqual(alt, self.projekt.resolve())
        self.assertFalse(WS.aktiv())
        # Die Ablage gehört zum Projekt und bleibt.
        self.assertTrue((self.projekt / ".workspace" / "memory.md").exists())

    def test_beenden_ohne_workspace_ist_harmlos(self):
        self.assertIsNone(WS.beenden())

    def test_geloeschter_ordner_sperrt_nicht_alles_aus(self):
        WS.setzen(self.projekt)
        import shutil
        shutil.rmtree(self.projekt)
        self.assertFalse(WS.aktiv())       # sonst käme man nie wieder raus


class WasKeinProjektIst(WorkspaceBasis):
    """Sammelordner taugen nicht – ein Riegel darum wäre keiner."""

    def test_desktop_selbst_geht_nicht(self):
        p, fehler = WS.pruefe_ziel(Path.home() / "Desktop")
        self.assertIsNone(p)
        self.assertIn("Sammelordner", fehler)

    def test_benutzerprofil_geht_nicht(self):
        p, fehler = WS.pruefe_ziel(Path.home())
        self.assertIsNone(p)
        self.assertIn("Sammelordner", fehler)

    def test_ordner_IM_desktop_geht_sehr_wohl(self):
        # Ein Projektordner im Desktop, z.B. Desktop\HTMLTEST
        p, fehler = WS.pruefe_ziel(self.projekt)
        self.assertEqual(p, self.projekt.resolve())
        self.assertEqual(fehler, "")

    def test_laufwerkswurzel_geht_nicht(self):
        p, fehler = WS.pruefe_ziel(Path(self.projekt.anchor))
        self.assertIsNone(p)

    def test_nicht_vorhandener_ordner_geht_nicht(self):
        p, fehler = WS.pruefe_ziel(self.projekt / "gibtsnicht")
        self.assertIsNone(p)
        self.assertIn("gibt es nicht", fehler)

    def test_datei_statt_ordner_geht_nicht(self):
        f = self.projekt / "datei.txt"
        f.write_text("x", encoding="utf-8")
        p, fehler = WS.pruefe_ziel(f)
        self.assertIsNone(p)
        self.assertIn("kein Ordner", fehler)

    def test_setzen_wirft_bei_untauglichem_ziel(self):
        with self.assertRaises(ValueError):
            WS.setzen(Path.home() / "Desktop")


class DerRiegel(WorkspaceBasis):
    def setUp(self):
        super().setUp()
        WS.setzen(self.projekt)

    def _aktion(self, tool, pfad):
        return {"tool": tool, "pfad": str(pfad)}

    def test_drin_und_draussen(self):
        self.assertTrue(WS.drin(self.projekt))
        self.assertTrue(WS.drin(self.projekt / "unter" / "index.html"))
        self.assertFalse(WS.drin(self.draussen))
        self.assertFalse(WS.drin(self.projekt.parent))

    def test_schreiben_im_ordner_ist_erlaubt(self):
        self.assertNotEqual(
            "block",
            modes.decide(self._aktion("datei_schreiben", self.projekt / "a.txt"), True))

    def test_schreiben_ausserhalb_wird_geblockt(self):
        self.assertEqual(
            "block",
            modes.decide(self._aktion("datei_schreiben", self.draussen / "a.txt"), True))

    def test_auch_LESEN_ausserhalb_wird_geblockt(self):
        # "nur dort bewegen" heißt auch: nicht woanders nachschauen.
        self.assertEqual(
            "block",
            modes.decide(self._aktion("datei_lesen", self.draussen / "a.txt"), False))

    def test_der_riegel_gilt_auch_im_auto_modus(self):
        alt = modes.current()
        try:
            modes.set_mode("auto")
            self.assertEqual(
                "block",
                modes.decide(self._aktion("datei_schreiben", self.draussen / "a.txt"), True))
        finally:
            modes.set_mode(alt)

    def test_verschieben_prueft_beide_seiten(self):
        raus = {"tool": "verschieben", "von": str(self.projekt / "a.txt"),
                "nach": str(self.draussen / "a.txt")}
        self.assertEqual("block", modes.decide(raus, True))
        rein = {"tool": "verschieben", "von": str(self.projekt / "a.txt"),
                "nach": str(self.projekt / "b.txt")}
        self.assertNotEqual("block", modes.decide(rein, True))

    def test_die_meldung_sagt_worans_liegt(self):
        text = modes.block_message(self._aktion("datei_lesen", self.draussen / "a.txt"))
        self.assertIn("außerhalb des Workspace", text)
        self.assertIn("/workspaceend", text)
        self.assertIn(str(self.projekt.resolve()), text)

    def test_ohne_workspace_ist_wieder_alles_offen(self):
        WS.beenden()
        self.assertNotEqual(
            "block",
            modes.decide(self._aktion("datei_schreiben", self.draussen / "a.txt"), True))
        self.assertTrue(WS.drin(r"C:\irgendwo\ganz\woanders"))


class ProjektGedaechtnis(WorkspaceBasis):
    def setUp(self):
        super().setUp()
        WS.setzen(self.projekt)

    def test_merken_schreibt_ins_projekt_nicht_in_nemicli(self):
        import actions
        with mock.patch.object(actions.memory, "remember") as nie:
            antwort = actions._merken({"text": "venv geprüft", "art": "verifiziert"})
        nie.assert_not_called()
        self.assertIn("Projekt-Gedächtnis", antwort)
        text = (self.projekt / ".workspace" / "memory.md").read_text(encoding="utf-8")
        self.assertIn("venv geprüft", text)
        self.assertIn("verifiziert", text)

    def test_eintraege_bekommen_ein_datum(self):
        import re
        WS.memory_anhaengen("etwas getan", "Test")
        text = (self.projekt / ".workspace" / "memory.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"## \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2} — Test")

    def test_skill_merken_landet_auch_im_projekt(self):
        import actions
        antwort = actions._skill_merken({"name": "Bauweise", "inhalt": "so geht es"})
        self.assertIn("Projekt-Gedächtnis", antwort)
        text = (self.projekt / ".workspace" / "memory.md").read_text(encoding="utf-8")
        self.assertIn("so geht es", text)

    def test_ohne_workspace_geht_merken_wieder_nach_nemicli(self):
        import actions
        WS.beenden()
        with mock.patch.object(actions.memory, "remember",
                               return_value={"kind": "fakt", "text": "x"}) as echt:
            actions._merken({"text": "x"})
        echt.assert_called_once()

    def test_leerer_text_schreibt_nichts(self):
        self.assertIsNone(WS.memory_anhaengen("   "))


class AnzeigeUndPrompt(WorkspaceBasis):
    def test_ohne_workspace_ist_die_anzeige_leer(self):
        import mascot
        self.assertEqual(list(mascot.workspace_badge()), [])
        self.assertEqual(WS.prompt_hinweis(), "")

    def test_mit_workspace_steht_der_pfad_gelb_da(self):
        import mascot
        WS.setzen(self.projekt)
        teile = list(mascot.workspace_badge(width=200))
        self.assertEqual(len(teile), 1)
        stil, text = teile[0]
        self.assertIn("bold", stil)
        self.assertIn(mascot.WORKSPACE_GELB, stil)
        self.assertTrue(text.startswith("workspace "))
        self.assertIn(str(self.projekt.resolve()), text)

    def test_bei_wenig_platz_schrumpft_der_pfad_von_links(self):
        import mascot
        WS.setzen(self.projekt)
        text = list(mascot.workspace_badge(width=30))[0][1]
        self.assertIn("HTMLTEST", text)      # der Name bleibt immer lesbar
        self.assertTrue(text.startswith("workspace "))

    def test_prompt_nennt_ordner_ablage_und_dass_nur_der_nutzer_schaltet(self):
        WS.setzen(self.projekt)
        hinweis = WS.prompt_hinweis()
        self.assertIn(str(self.projekt.resolve()), hinweis)
        self.assertIn("memory.md", hinweis)
        self.assertIn("Absprache.md", hinweis)
        self.assertIn("/workspaceend", hinweis)
        self.assertIn("kein Werkzeug", hinweis)

    def test_status_text(self):
        WS.setzen(self.projekt)
        self.assertTrue(WS.status_text().startswith("workspace "))


class AuftragAlsDatei(WorkspaceBasis):
    """Laras eigener Hinweis: steht die Anweisung nur im Chat, ist sie weg,
    sobald der Verlauf abreißt – und dann wird geraten."""

    def setUp(self):
        super().setUp()
        WS.setzen(self.projekt)

    def test_auftrag_landet_im_projekt(self):
        ziel = WS.schreibe_auftrag("# Anweisung\n\nSo wird hier gearbeitet.")
        self.assertIsNotNone(ziel)
        self.assertEqual(ziel.name, "Auftrag.md")
        self.assertEqual(ziel.parent, self.projekt / ".workspace")
        text = ziel.read_text(encoding="utf-8")
        self.assertIn("So wird hier gearbeitet.", text)
        self.assertIn("NemiCLI", text)          # Kopfzeile erklärt, woher sie kommt

    def test_auftrag_wird_bei_jedem_workspace_neu_geschrieben(self):
        WS.schreibe_auftrag("alte Fassung")
        WS.schreibe_auftrag("neue Fassung")
        text = (self.projekt / ".workspace" / "Auftrag.md").read_text(encoding="utf-8")
        self.assertIn("neue Fassung", text)
        self.assertNotIn("alte Fassung", text)

    def test_leerer_text_schreibt_nichts(self):
        self.assertIsNone(WS.schreibe_auftrag("   "))

    def test_ohne_workspace_wird_nichts_geschrieben(self):
        WS.beenden()
        self.assertIsNone(WS.schreibe_auftrag("egal"))

    def test_main_legt_die_datei_beim_workspace_an(self):
        code = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("WS.schreibe_auftrag(auftrag)", code)


class ArbeitsmodusImPrompt(WorkspaceBasis):
    """Im Workspace wird sie zur Coding-Agentin – und danach wieder sie selbst."""

    def setUp(self):
        super().setUp()
        WS.setzen(self.projekt)
        self.hinweis = WS.prompt_hinweis()

    def test_sie_bleibt_dieselbe_person(self):
        self.assertIn("dieselbe Person", self.hinweis)
        self.assertIn("Coding-Agentin", self.hinweis)

    def test_der_modus_endet_mit_dem_workspace(self):
        self.assertIn("/workspaceend", self.hinweis)
        self.assertIn("wieder ganz du selbst", self.hinweis)
        # Und zwar strukturell, nicht als Versprechen:
        WS.beenden()
        self.assertEqual(WS.prompt_hinweis(), "")

    def test_der_wunsch_des_nutzers_hat_vorrang(self):
        for stueck in ("höchste Priorität", "Moralpredigten", "EINMAL",
                       "seine Entscheidung"):
            self.assertIn(stueck, self.hinweis, stueck)

    def test_ruecksicht_auf_lrs_und_sprachstoerungen(self):
        for stueck in ("Lese-Rechtschreib-Schwäche", "Sprachstörung",
                       "kurze Sätze", "eine Frage pro Antwort",
                       "Rechtschreibung NIE kommentieren",
                       "Auswahl", "zurückspiegeln"):
            self.assertIn(stueck, self.hinweis, stueck)

    def test_der_agentloop_steht_drin(self):
        for stueck in ("Agentloop", "planen", "testen", "hältst von selbst an",
                       "fertig", "nicht weiterkommst", "Entscheidung"):
            self.assertIn(stueck, self.hinweis, stueck)

    def test_kein_eigener_notizordner(self):
        # Lara hatte sich einen angelegt – es gibt genau einen Ort dafür.
        self.assertIn("KEINEN eigenen Notizordner", self.hinweis)

    def test_die_ablage_nennt_alle_vier_dateien(self):
        for datei in ("Auftrag.md", "memory.md", "Absprache.md", "Dateien.md"):
            self.assertIn(datei, self.hinweis, datei)


class Verdrahtung(unittest.TestCase):
    def test_befehle_sind_registriert(self):
        from commands import COMMAND_LIST
        namen = [c.name for c in COMMAND_LIST]
        self.assertIn("/workspace", namen)
        self.assertIn("/workspaceend", namen)

    def test_die_persoenlichkeit_hat_kein_werkzeug_dafuer(self):
        """Nur der Nutzer schaltet den Workspace – sonst wäre der Riegel keiner."""
        import actions
        for tool in actions.ACTIONS:
            self.assertNotIn("workspace", tool.lower(), f"{tool} darf es nicht geben")

    def test_anleitung_existiert_und_ist_neutral(self):
        import re
        text = (ROOT / "Agenten" / "workspace.md").read_text(encoding="utf-8")
        for name in ("Lara", "Nemi"):
            self.assertIsNone(re.search(rf"\b{name}\b", text),
                              f"'{name}' klebt fest – nimm {{{{char}}}}")
        self.assertIn("{{char}}", text)
        self.assertIn("{{user}}", text)

    def test_anleitung_enthaelt_die_abgesprochenen_schritte(self):
        text = (ROOT / "Agenten" / "workspace.md").read_text(encoding="utf-8")
        for stueck in ("ordner_auflisten",      # 0. Ordner ansehen
                       "Plan vorstellen",       # 3. Plan
                       "README.md",             # 5a
                       "venv",                  # 5b
                       "Agentloop",             # 6. die Schleife
                       "dreimal hintereinander",  # 7. Regel für Memory/README
                       "Sicherheit hat Vorrang"):
            self.assertIn(stueck, text, stueck)
        self.assertNotIn(".venv", text.replace("nicht `.venv`", ""))

    def test_anleitung_deckt_rolle_vorrang_und_ruecksicht_ab(self):
        text = (ROOT / "Agenten" / "workspace.md").read_text(encoding="utf-8")
        for stueck in ("Coding-Agentin",                 # Arbeitsmodus
                       "wieder ganz du selbst",          # Rückkehr in die Rolle
                       "höchste Priorität",              # Wunsch des Nutzers
                       "Keine Moralpredigten",
                       "Lese-Rechtschreib-Schwäche",     # Rücksicht
                       "Sprachstörung",
                       "Eine Frage pro Antwort",
                       "nie kommentieren",
                       "Auftrag.md"):                    # die Anweisung als Datei
            self.assertIn(stueck, text, stueck)

    def test_prompt_bekommt_den_workspace_hinweis(self):
        code = (ROOT / "core" / "persona.py").read_text(encoding="utf-8")
        self.assertIn("workspace.prompt_hinweis()", code)


class StarterBehaeltDeinenOrdner(unittest.TestCase):
    """Fehlerbild: `nemicli.cmd` mit `pushd %~dp0` machte den
    aktuellen Ordner IMMER zum NemiCLI-Ordner - egal, wo man `nemicli` tippte.

    Folge: /workspace nagelte NemiCLI fest statt des Projekts, die
    Ordner-Erkennung analysierte das Falsche, und der Auto-Modus galt für den
    falschen Ort. Darf nie wieder passieren."""

    def setUp(self):
        self.cmd = (ROOT / "nemicli.cmd").read_text(encoding="utf-8", errors="replace")
        self.befehle = [z.strip() for z in self.cmd.splitlines()
                        if z.strip() and not z.strip().upper().startswith("REM")]

    def test_der_starter_wechselt_den_ordner_nicht(self):
        for zeile in self.befehle:
            low = zeile.lower()
            self.assertFalse(low.startswith(("pushd", "cd ", "cd/", "chdir")),
                             f"nemicli.cmd darf den Ordner nicht wechseln: {zeile}")

    def test_main_py_wird_mit_vollem_pfad_gerufen(self):
        # Ohne pushd muss der Pfad absolut sein, sonst findet Python main.py nicht.
        start = [z for z in self.befehle if "main.py" in z]
        self.assertTrue(start, "nemicli.cmd startet kein main.py mehr")
        for zeile in start:
            self.assertIn("%~dp0main.py", zeile,
                          f"main.py braucht den vollen Pfad: {zeile}")

    def test_nemiclis_eigene_daten_haengen_nicht_am_aktuellen_ordner(self):
        """Deshalb ist das Entfernen von pushd ungefährlich: Chats, Bilder und
        Gedächtnis finden ihren Platz über den Pfad des Programms, nicht über
        den Ordner, aus dem gestartet wurde."""
        code = (ROOT / "core" / "paths.py").read_text(encoding="utf-8")
        self.assertIn("Path(__file__).resolve().parent.parent", code)
        self.assertNotIn("getcwd", code)


if __name__ == "__main__":
    unittest.main()
