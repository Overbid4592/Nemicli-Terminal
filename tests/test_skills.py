"""Skills: Format, Anlegen/Ändern mit Prüffenster, Freigaberegeln, alte Notizen."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions      # noqa: E402
import learn        # noqa: E402
import modes        # noqa: E402
import paths        # noqa: E402
import persona      # noqa: E402
import sicherheit   # noqa: E402
import skills       # noqa: E402

BEISPIEL = {"name": "Krea: Text im Bild", "beschreibung": "Schrift in Krea-Bildern sauber hinbekommen",
            "wann": "wenn ein Bild Text enthalten soll", "anleitung": "1. Text in Anführungszeichen.\n2. Kurz halten."}


class MitDatenordner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.daten = Path(self._tmp.name).resolve()
        self._patches = [mock.patch.object(paths, "DATEN", self.daten),
                         mock.patch.object(skills, "selbst", return_value=False),
                         mock.patch("profilordner.aktiv", return_value="Test")]
        for p in self._patches:
            p.start()
        actions.reset_taint()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        actions.reset_taint()
        self._tmp.cleanup()


class Format(MitDatenordner):
    def test_hin_und_zurueck(self):
        text = skills.text_bauen(**BEISPIEL)
        self.assertEqual(skills.zerlegen(text), BEISPIEL)

    def test_kopf_bleibt_einzeilig(self):
        text = skills.text_bauen("A\n---\nwann: böse", "b", "c", "d")
        f = skills.zerlegen(text)
        self.assertEqual(f["wann"], "c")
        self.assertNotIn("\n", f["name"])

    def test_pruefen_verlangt_beschreibung_und_wann(self):
        self.assertIsNotNone(skills.pruefen("n", "", "w", "a"))
        self.assertIsNotNone(skills.pruefen("n", "b", "", "a"))
        self.assertIsNotNone(skills.pruefen("n", "b", "w", " "))
        self.assertIsNone(skills.pruefen(**BEISPIEL))


class AnlegenUndLaden(MitDatenordner):
    def test_anlegen_finden_laden(self):
        pfad = skills.schreiben(**BEISPIEL)
        self.assertEqual(pfad, self.daten / "Profile" / "Test" / "Skills" / "krea-text-im-bild" / "SKILL.md")
        for name in ("Krea: Text im Bild", "krea-text-im-bild", "krea: text im bild", "Krea: Text im Bld"):
            self.assertIsNotNone(skills.finden(name), name)
        text = skills.laden_text("Krea: Text im Bild")
        self.assertIn("freigegebene Anleitung", text)
        self.assertIn("Kurz halten", text)
        self.assertIn("Krea: Text im Bild", skills.index_fuer_prompt())

    def test_aussehen_der_persoenlichkeit_geht_vor(self):
        skills.schreiben(**BEISPIEL)
        text = skills.laden_text("Krea: Text im Bild")
        self.assertIn(skills.PERSON_VORRANG, text)
        self.assertLess(text.index(skills.PERSON_VORRANG), text.index("Kurz halten"))   # vor der Anleitung

    def test_aendern_sichert_alten_stand_und_zeigt_vergleich(self):
        skills.schreiben(**BEISPIEL)
        neu = dict(BEISPIEL, anleitung="1. Text in Anführungszeichen.\n2. Kurz halten.\n3. Große Schrift.")
        vorschau = skills.vorschau(**neu)
        self.assertIn("GEÄNDERT", vorschau)
        self.assertIn("+", vorschau)
        self.assertIn("Große Schrift", vorschau)
        with mock.patch("snapshot.sichern") as sichern:
            skills.schreiben(**neu)
        sichern.assert_called_once()
        self.assertEqual(len(skills.alle()), 1)

    def test_neuer_skill_zeigt_ganzen_text(self):
        vorschau = skills.vorschau(**BEISPIEL)
        self.assertIn("Neuer Skill", vorschau)
        self.assertIn(BEISPIEL["anleitung"].splitlines()[1], vorschau)

    def test_werkzeuge(self):
        act = dict(BEISPIEL, tool="skill_schreiben")
        self.assertIn("Große", actions.confirmation_preview(dict(act, anleitung="Große Schrift")))
        self.assertTrue(actions._skill_schreiben(act).text.startswith("Skill gespeichert"))
        self.assertIn("Kurz halten", actions._skill_laden({"name": BEISPIEL["name"]}).text)
        self.assertIs(actions._skill_laden({"name": "gibt es nicht"}).ok, False)


class Freigabe(MitDatenordner):
    ACT = dict(BEISPIEL, tool="skill_schreiben")

    def test_fragt_ohne_erlaubnis(self):
        self.assertTrue(actions.needs_confirm(self.ACT))

    def test_selbst_an_fragt_nicht_ausser_nach_web(self):
        with mock.patch.object(skills, "selbst", return_value=True):
            self.assertFalse(actions.needs_confirm(self.ACT))
            actions._web_taint = True
            self.assertTrue(actions.needs_confirm(self.ACT))

    def test_fragt_auch_im_auto_modus_und_sperrt_im_lesemodus(self):
        for modus, erwartet in (("auto", "ask"), ("normal", "ask"), ("chat", "ask"), ("lesen", "block")):
            with self.subTest(modus=modus), mock.patch.object(modes, "current", return_value=modus):
                self.assertEqual(modes.decide(self.ACT, actions.needs_confirm(self.ACT)), erwartet)

    def test_laden_ist_lesen(self):
        self.assertIn("skill_laden", modes.READ_TOOLS)

    def test_nie_gebuendelt(self):
        acts = [dict(self.ACT), dict(self.ACT, name="Zweiter")]
        self.assertEqual(sicherheit.buendel(acts, lambda a: True), [])

    def test_skills_ordner_ist_kein_freier_arbeitsbereich(self):
        (self.daten / "Skills" / "x").mkdir(parents=True)
        (self.daten / "learned").mkdir()
        with mock.patch.object(paths, "INSTALL", self.daten.parent / "programm"):
            frei = sicherheit.arbeitsbereich(
                {"tool": "datei_schreiben", "pfad": str(self.daten / "learned" / "n.md"), "inhalt": "x"})
            skill = sicherheit.arbeitsbereich(
                {"tool": "datei_schreiben", "pfad": str(self.daten / "Skills" / "x" / "SKILL.md"), "inhalt": "x"})
        self.assertTrue(frei)
        self.assertFalse(skill)


class AlteNotizen(MitDatenordner):
    def test_alte_notiz_zerlegen_und_als_gesehen_merken(self):
        alt = self.daten / "learned" / "skills"
        alt.mkdir(parents=True)
        (alt / "bild-oeffnen.md").write_text(
            "# Bild unter Windows öffnen\n\n_gelernt am 2026-01-01 10:00_\n\nMit oeffnen statt befehl. Geht schneller.\n",
            encoding="utf-8")
        with mock.patch.object(learn, "SKILLS_DIR", alt):
            offen = skills.alte()
            self.assertEqual([p.name for p in offen], ["bild-oeffnen.md"])
            f = skills.alt_zerlegen(offen[0])
            self.assertEqual(f["name"], "Bild unter Windows öffnen")
            self.assertEqual(f["beschreibung"], "Mit oeffnen statt befehl.")
            self.assertNotIn("gelernt am", f["anleitung"])
            self.assertIsNone(skills.pruefen(**f))
            skills.alt_gesehen(offen[0])
            self.assertEqual(skills.alte(), [])


class Anleitung(unittest.TestCase):
    def test_anleitung_und_kompakte_fassung(self):
        voll = persona.anleitung("skills")
        self.assertIn("Einen Skill bauen", voll)
        kompakt = persona.kompakt(persona.base_prompt())
        self.assertIn("- skill_schreiben", kompakt)
        self.assertIn('anleitung_lesen mit thema "skills"', kompakt)
        self.assertIn("skills", persona.passende_anleitungen("Lass uns einen Skill machen"))


if __name__ == "__main__":
    unittest.main()
