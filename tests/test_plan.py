"""Gelbe Todo-Liste (core/plan.py): Anlegen, Abhaken, Sperre ohne Liste."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / _sub))

import plan  # noqa: E402


class PlanTests(unittest.TestCase):
    def setUp(self):
        plan.verwerfen()
        self.addCleanup(plan.verwerfen)

    def test_ohne_liste_kein_abhaken(self):
        text, ok = plan.ausfuehren({"tool": "plan", "erledigt": 1})
        self.assertFalse(ok)
        self.assertIn("keine Todo-Liste", text)

    def test_anlegen_und_abhaken(self):
        text, ok = plan.ausfuehren({"tool": "plan", "punkte": ["Bild 1", "Bild 2", "Bild 3"]})
        self.assertTrue(ok)
        self.assertIn("0/3 erledigt", text)
        self.assertIn("🟠 1. Bild 1", text)
        self.assertIn("🔴 2. Bild 2", text)
        text, ok = plan.ausfuehren({"tool": "plan", "erledigt": [1, 2]})
        self.assertIn("🟢 1. Bild 1", text)
        self.assertIn("🟠 3. Bild 3", text)
        self.assertEqual((2, 3), plan.zaehlen())

    def test_punkte_als_text_mit_aufzaehlung(self):
        plan.ausfuehren({"tool": "plan", "punkte": "1. Ordner ansehen\n- Datei anlegen\n\n"})
        self.assertEqual(["Ordner ansehen", "Datei anlegen"], [p["text"] for p in plan.daten()["punkte"]])

    def test_streichen_zaehlt_nicht_mit(self):
        plan.ausfuehren({"tool": "plan", "punkte": ["A", "B"]})
        text, _ = plan.ausfuehren({"tool": "plan", "streichen": 2, "grund": "nicht nötig"})
        self.assertIn("⚪ 2. B (gestrichen: nicht nötig)", text)
        self.assertEqual((0, 1), plan.zaehlen())

    def test_neu_und_alle(self):
        plan.ausfuehren({"tool": "plan", "punkte": ["A"]})
        plan.ausfuehren({"tool": "plan", "neu": "B"})
        plan.ausfuehren({"tool": "plan", "erledigt": "alle"})
        self.assertEqual((2, 2), plan.zaehlen())
        self.assertIn("alles abgehakt", plan.kurzstand())

    def test_unbekannte_nummer_wird_genannt(self):
        plan.ausfuehren({"tool": "plan", "punkte": ["A"]})
        text, ok = plan.ausfuehren({"tool": "plan", "erledigt": 7})
        self.assertTrue(ok)
        self.assertIn("Punkt 7 gibt es nicht", text)

    def test_leere_punkte_sind_fehler(self):
        _, ok = plan.ausfuehren({"tool": "plan", "punkte": []})
        self.assertFalse(ok)
        self.assertFalse(plan.aktiv())

    def test_ohne_liste_nur_ein_arbeitsschritt(self):
        acts = [{"tool": "datei_loeschen"}, {"tool": "ordner_erstellen"}, {"tool": "befehl"}]
        laufen, gesperrt = plan.zurueckweisen(acts)
        self.assertEqual(acts[:1], laufen)
        self.assertEqual(acts[1:], gesperrt)
        self.assertIn("ordner_erstellen, befehl", plan.sperr_text(gesperrt))
        self.assertIn("Pro Nachricht des Nutzers ist ein Arbeitsschritt", plan.sperr_text(gesperrt))
        self.assertIn("schon: datei_loeschen", plan.sperr_text(gesperrt))
        self.assertEqual(([], [{"tool": "befehl"}]), plan.zurueckweisen([{"tool": "befehl"}]))  # auch im nächsten Zug

    def test_lesen_und_begleiten_brauchen_keine_liste(self):
        acts = [{"tool": t} for t in ("datei_lesen", "web_suche", "gedaechtnis_suchen", "merken", "kugel",
                                      "vorlesen", "aufgaben")]
        self.assertEqual((acts, []), plan.zurueckweisen(acts))
        self.assertEqual(([{"tool": "bild_malen"}], []), plan.zurueckweisen([{"tool": "bild_malen"}]))

    def test_neue_runde_gibt_wieder_einen_schritt_frei(self):
        plan.zurueckweisen([{"tool": "ordner_erstellen"}])
        self.assertEqual([], plan.zurueckweisen([{"tool": "ordner_erstellen"}])[0])
        plan.runde_neu()
        self.assertEqual([{"tool": "ordner_erstellen"}], plan.zurueckweisen([{"tool": "ordner_erstellen"}])[0])

    def test_bilder_und_videos_ohne_liste(self):
        acts = [{"tool": t} for t in ("bild_malen", "portraet_bewegen", "video_erstellen", "bild_malen")]
        acts.append({"tool": "ordner_erstellen"})
        self.assertEqual((acts, []), plan.zurueckweisen(acts))       # der Ordner ist der eine freie Schritt

    def test_hoechstens_zehn_medien_je_nachricht(self):
        serie = {"tool": "bild_serie", "varianten": ["a"] * 8}
        laufen, zu_viel = plan.medien_begrenzen([serie, {"tool": "bild_malen"}, {"tool": "befehl"}])
        self.assertEqual([serie, {"tool": "bild_malen"}, {"tool": "befehl"}], laufen)
        self.assertEqual([], zu_viel)
        comic = {"tool": "comic_malen", "panels": [{"szene": "x"}] * 2}
        laufen, zu_viel = plan.medien_begrenzen([{"tool": "video_erstellen"}, comic])
        self.assertEqual([{"tool": "video_erstellen"}], laufen)       # 10 erreicht
        self.assertEqual([comic], zu_viel)
        self.assertIn("höchstens 10", plan.medien_text(zu_viel))
        self.assertIn("comic_malen", plan.medien_text(zu_viel))
        plan.runde_neu()
        self.assertEqual(([comic], []), plan.medien_begrenzen([comic]))

    def test_fehlgeschlagenes_bild_zaehlt_nicht(self):
        plan.medien_begrenzen([{"tool": "bild_serie", "varianten": ["a"] * 10}])
        self.assertEqual([], plan.medien_begrenzen([{"tool": "bild_malen"}])[0])
        plan.abhaken_nach({"tool": "bild_serie", "varianten": ["a"] * 10}, False)
        self.assertEqual([{"tool": "bild_malen"}], plan.medien_begrenzen([{"tool": "bild_malen"}])[0])

    def test_einmal_von_sich_aus_neu_malen(self):
        bild = {"tool": "bild_malen"}
        self.assertEqual([bild], plan.medien_begrenzen([bild])[0])        # 1. Zug
        plan.abhaken_nach(bild, True)
        self.assertEqual([bild], plan.medien_begrenzen([bild])[0])        # ein neuer Versuch
        plan.abhaken_nach(bild, True)
        laufen, zurueck = plan.medien_begrenzen([bild, {"tool": "merken"}])
        self.assertEqual(([{"tool": "merken"}], [bild]), (laufen, zurueck))
        self.assertIn("kein Fehler", plan.medien_text(zurueck))
        self.assertIn("neu gemalt", plan.medien_hinweis())
        plan.runde_neu()
        self.assertEqual([bild], plan.medien_begrenzen([bild])[0])

    def test_fehlschlag_und_liste_zaehlen_nicht_als_neu_malen(self):
        bild = {"tool": "bild_malen"}
        for _ in range(4):                                                # Fehlschläge: kein Mal-Zug
            self.assertEqual([bild], plan.medien_begrenzen([bild])[0])
            plan.abhaken_nach(bild, False)
        plan.ausfuehren({"tool": "plan", "punkte": ["Bild 1", "Bild 2", "Bild 3"]})
        for _ in range(3):                                                # mit Liste: Bild für Bild
            self.assertEqual([bild], plan.medien_begrenzen([bild])[0])
            plan.abhaken_nach(bild, True)

    def test_serie_grenze_passt_zur_nachricht(self):
        import actions
        self.assertLessEqual(actions.MAX_SERIE, plan.MAX_MEDIEN)
        self.assertLessEqual(actions.MAX_COMIC, plan.MAX_MEDIEN)

    def test_liste_im_selben_zug_gibt_frei(self):
        acts = [{"tool": "plan", "punkte": ["Lesen"]}, {"tool": "datei_lesen"}]
        laufen, gesperrt = plan.zurueckweisen(acts)
        self.assertEqual(acts, laufen)
        self.assertEqual([], gesperrt)

    def test_aktion_vor_der_liste_zaehlt_als_schritt(self):
        acts = [{"tool": "ordner_erstellen"}, {"tool": "befehl"}, {"tool": "plan", "punkte": ["Lesen"]},
                {"tool": "befehl"}]
        laufen, gesperrt = plan.zurueckweisen(acts)
        self.assertEqual([acts[0], acts[2], acts[3]], laufen)
        self.assertEqual([acts[1]], gesperrt)

    def test_coding_assistent_und_bestehende_liste(self):
        acts = [{"tool": "befehl"}]
        self.assertEqual((acts, []), plan.zurueckweisen(acts, frei=True))
        self.assertEqual(([{"tool": "todo"}], []), plan.zurueckweisen([{"tool": "todo"}]))
        plan.ausfuehren({"tool": "plan", "punkte": ["A"]})
        self.assertEqual((acts, []), plan.zurueckweisen(acts))

    def test_runde_neu_leert_und_marke_folgt_fortschritt(self):
        plan.ausfuehren({"tool": "plan", "punkte": ["A", "B"]})
        vorher = plan.marke()
        plan.ausfuehren({"tool": "plan", "erledigt": 1})
        self.assertNotEqual(vorher, plan.marke())
        self.assertTrue(plan.neu_seit_anzeige())
        plan.angezeigt()
        self.assertFalse(plan.neu_seit_anzeige())
        self.assertTrue(plan.runde_neu())                 # B ist offen: die Liste bleibt
        self.assertTrue(plan.aktiv())
        plan.ausfuehren({"tool": "plan", "erledigt": 2})
        self.assertFalse(plan.runde_neu())                # alles erledigt: neue Runde, neue Liste
        self.assertFalse(plan.aktiv())
        self.assertIsNone(plan.marke())

    def test_verwerfen_beim_neuen_chat(self):
        plan.ausfuehren({"tool": "plan", "punkte": ["A"]})
        plan.verwerfen()
        self.assertFalse(plan.aktiv())

    def test_abhaken_nur_nach_erfolg(self):
        plan.ausfuehren({"tool": "plan", "punkte": ["A", "B", "C"]})
        self.assertFalse(plan.abhaken_nach({"tool": "bild_malen", "abhaken": 1}, False))
        self.assertFalse(plan.abhaken_nach({"tool": "bild_malen"}, True))
        self.assertTrue(plan.abhaken_nach({"tool": "bild_malen", "abhaken": "1, 2"}, True))
        self.assertEqual(["erledigt", "erledigt", "offen"], [p["status"] for p in plan.daten()["punkte"]])
        self.assertFalse(plan.abhaken_nach({"tool": "bild_malen", "abhaken": 1}, True))   # schon grün
        self.assertFalse(plan.abhaken_nach({"tool": "bild_malen", "abhaken": "alle"}, True))

    def test_modus_laesst_plan_immer_zu(self):
        import modes
        alt = modes.current()
        self.addCleanup(modes.set_mode, alt)
        modes.set_mode("lesen")
        self.assertEqual("run", modes.decide({"tool": "plan", "punkte": ["A"]}, False))

    def test_werkzeug_und_anzeige(self):
        import actions
        self.assertIn("plan", actions.ACTIONS)
        self.assertFalse(actions.needs_confirm({"tool": "plan", "punkte": ["A"]}))
        self.assertIn("Todo-Liste anlegen", actions.describe({"tool": "plan", "punkte": ["A"]}))
        import io
        from rich.console import Console
        import ui
        plan.ausfuehren({"tool": "plan", "punkte": ["Fuchs malen", "Katze malen"], "erledigt": 1})
        con = Console(file=io.StringIO(), width=80, color_system=None)
        con.print(ui.plan_panel(plan.daten()))
        aus = con.file.getvalue()
        self.assertIn("Todo · 1/2 erledigt", aus)
        self.assertIn("Katze malen", aus)

    def test_helfer_bekommen_die_regel_nicht(self):
        import persona
        self.assertIn("Todo-Liste bei Aufgaben mit mehreren Schritten", persona._REGELN)
        helfer = persona.helfer_prompt("Leser", "Lies etwas")
        self.assertNotIn("Todo-Liste bei Aufgaben", helfer)
        self.assertNotIn("(plus `plan`)", helfer)


if __name__ == "__main__":
    unittest.main()
