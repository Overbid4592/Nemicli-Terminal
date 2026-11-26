"""Gedächtnis in zwei Stufen: kurze Notiz im Prompt, Details in einer eigenen Markdown-Datei mit stabilem
Kurznamen; gedaechtnis_lesen holt sie bei Bedarf, die Suche findet sie, Vergessen räumt sie in den Papierkorb."""
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
import agentrag     # noqa: E402
import indexdb      # noqa: E402
import memory       # noqa: E402

DETAILS = "# Lieblingsbücher\n\n- Reihe: Beispiel-Saga\n- Link: https://example.com/buch\n- Quelle: Aussage im Chat"


class _Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name)
        self.dropped, self.gesichert = [], []
        for p in (mock.patch.object(memory, "_FILE", self.ordner / "memory.json"),
                  mock.patch.object(indexdb, "drop_ref", side_effect=self.dropped.append),
                  mock.patch("snapshot.sichern", side_effect=lambda pfad, w, verschieben=False: (
                      self.gesichert.append((Path(pfad).name, w, verschieben)),
                      Path(pfad).unlink() if verschieben else None))):
            p.start()
            self.addCleanup(p.stop)

    def merken(self, **a):
        return actions._merken(a)


class Bausteine(_Basis):
    def test_kurzname_stabil(self):
        self.assertEqual("lieblings-buecher-2", memory.detail_name(" Lieblings Bücher #2! "))
        self.assertEqual("", memory.detail_name("!!!"))
        self.assertLessEqual(len(memory.detail_name("x" * 99)), 40)

    def test_schreiben_lesen_ersetzen(self):
        pfad, neu = memory.detail_schreiben("Lieblingsbuch", DETAILS)
        self.assertTrue(neu)
        self.assertEqual(self.ordner / "Details" / "lieblingsbuch.md", pfad)
        self.assertIn("Beispiel-Saga", memory.detail_lesen("lieblingsbuch"))
        _, neu = memory.detail_schreiben("lieblingsbuch", DETAILS + "\n- neu: Band 2")
        self.assertFalse(neu)
        self.assertEqual([("lieblingsbuch.md", "merken", False)], self.gesichert)   # alter Stand gesichert
        self.assertIn("Band 2", memory.detail_lesen("lieblingsbuch"))
        self.assertEqual([("lieblingsbuch", pfad)], memory.details())

    def test_abgelehnt(self):
        for name, inhalt in (("x", ""), ("!!!", "Text"), ("x", "a" * (memory.DETAIL_MAX + 1)),
                             ("x", "Text\u200bmit unsichtbarem Zeichen")):
            with self.subTest(name=name, inhalt=inhalt[:20]), self.assertRaises(ValueError):
                memory.detail_schreiben(name, inhalt)


class Werkzeug(_Basis):
    def test_merken_mit_details(self):
        erg = self.merken(text="Der Nutzer liest gern Science-Fiction.", art="vorliebe", name="lieblingsbuch",
                          details=DETAILS)
        self.assertIn("Gemerkt ✅", erg)
        self.assertIn("📎 detail:lieblingsbuch neu angelegt", erg)
        e = memory.all_entries()[0]
        self.assertEqual("lieblingsbuch", e["detail"])
        kern = memory.kern_block()
        self.assertIn("Science-Fiction. (📎 detail:lieblingsbuch)", kern)
        self.assertNotIn("Beispiel-Saga", kern)                                   # Details nicht im Prompt
        self.assertIn("gedaechtnis_lesen", kern)

    def test_ohne_name_aus_dem_text(self):
        self.merken(text="Projekt Sternwarte läuft auf dem Laptop", details="Details zum Projekt")
        self.assertEqual("projekt-sternwarte-laeuft-auf", memory.all_entries()[0]["detail"])

    def test_nur_verknuepfen_und_fehlende_datei(self):
        erg = self.merken(text="Mag Hörbücher.", name="hoerbuch")
        self.assertIn("keine Details-Datei „hoerbuch“", str(getattr(erg, "text", erg)))
        self.assertEqual([], memory.all_entries())
        memory.detail_schreiben("hoerbuch", "Lieblingssprecher: Beispiel")
        self.assertIn("verknüpft mit detail:hoerbuch", self.merken(text="Mag Hörbücher.", name="hoerbuch"))
        # schon gemerkt: der zweite Aufruf verknüpft nur die Datei, keine zweite Notiz
        memory.detail_schreiben("hoerbuch2", "anderes")
        self.merken(text="Mag Hörbücher.", name="hoerbuch2")
        self.assertEqual([("Mag Hörbücher.", "hoerbuch2")], [(e["text"], e["detail"]) for e in memory.all_entries()])

    def test_ersetzen_behaelt_details(self):
        self.merken(text="Liest gern Krimis.", art="vorliebe", name="lesen", details="Krimis")
        nr = memory.all_entries()[0]["id"]
        self.merken(text="Liest gern Science-Fiction.", art="vorliebe", ersetzt=str(nr))
        e = memory.all_entries()[0]
        self.assertEqual(("Liest gern Science-Fiction.", "lesen"), (e["text"], e["detail"]))

    def test_verdaechtige_details(self):
        erg = self.merken(text="Notiz", name="x", details="a" * (memory.DETAIL_MAX + 1))
        self.assertFalse(erg.ok)
        self.assertEqual([], memory.all_entries())

    def test_vorschau_zeigt_details(self):
        vorschau = actions.describe({"tool": "merken", "text": "x", "name": "Lieblingsbuch",
                                     "details": "Reihe: Beispiel-Saga\n" + "z" * 300})
        self.assertIn("📎 Details: lieblingsbuch", vorschau)
        self.assertIn("Reihe: Beispiel-Saga", vorschau)                       # was in die Datei kommt
        self.assertIn("…", vorschau)


class Lesen(_Basis):
    def test_gedaechtnis_lesen_detail(self):
        memory.detail_schreiben("lieblingsbuch", DETAILS)
        text = agentrag.lesen({"ref": "detail:lieblingsbuch"})
        self.assertIn("📎 Details lieblingsbuch", text)
        self.assertIn("Beispiel-Saga", text)
        self.assertIn("detail:lieblingsbuch", agentrag.lesen({"ref": "detail:"}))  # Liste
        self.assertIn("# Lieblingsbücher", agentrag.lesen({"ref": "detail:"}))
        self.assertIn("Keine Details „fehlt“", agentrag.lesen({"ref": "detail:fehlt"}))


class Aufraeumen(_Basis):
    def test_vergessen_raeumt_nur_unbenutzte_weg(self):
        self.merken(text="A mag Tee.", name="tee", details="Grüner Tee")
        self.merken(text="A trinkt morgens Tee.", name="tee")
        a, b = memory.all_entries()
        memory.forget(a["id"])
        self.assertIsNotNone(memory.detail_pfad("tee"))                         # b zeigt noch darauf
        memory.vergessen(b["id"])
        self.assertIsNone(memory.detail_pfad("tee"))
        self.assertIn(("tee.md", "vergessen", True), self.gesichert)            # Papierkorb, nicht endgültig
        self.assertIn("detail:tee", self.dropped)

    def test_neu_verknuepft_raeumt_alte_datei_weg(self):
        self.merken(text="Liest gern Krimis.", name="krimis", details="Lieblingsreihe")
        nr = memory.all_entries()[0]["id"]
        self.merken(text="Liest gern Science-Fiction.", ersetzt=str(nr), name="scifi", details="Lieblingsreihe")
        self.assertEqual("scifi", memory.all_entries()[0]["detail"])
        self.assertIsNone(memory.detail_pfad("krimis"))
        self.assertIn(("krimis.md", "vergessen", True), self.gesichert)
        self.assertIsNotNone(memory.detail_pfad("scifi"))

    def test_nicht_gemerkt_laesst_keine_datei_liegen(self):
        erg = self.merken(text="Notiz", ersetzt="999", name="waise", details="ohne Notiz")
        self.assertFalse(erg.ok)
        self.assertIsNone(memory.detail_pfad("waise"))
        self.merken(text="Notiz fürs Projekt", art="projektnotiz", name="projekt", details="egal")
        self.assertIsNone(memory.detail_pfad("projekt"))

    def test_leeren(self):
        self.merken(text="A mag Tee.", name="tee", details="Grüner Tee")
        memory.clear()
        self.assertIsNone(memory.detail_pfad("tee"))

    def test_verdichten_behaelt_details(self):
        self.merken(text="Der Nutzer mag Tee", art="vorliebe")
        self.merken(text="Der Nutzer mag Tee sehr", art="vorliebe", name="tee", details="Grüner Tee")
        memory._verdichten(memory._FILE, 0.9)                                    # ohne Vektoren: nach Text
        self.assertEqual(["tee"], [e.get("detail") for e in memory.all_entries()])

    def test_verdichten_nach_den_vektoren_aus_der_datenbank(self):
        self.merken(text="Der Nutzer trinkt gern Tee", art="vorliebe")
        self.merken(text="Am liebsten grünen Tee am Morgen", art="vorliebe")
        self.merken(text="Das Projekt heißt Beispiel", art="projekt")
        a, b, c = (e["id"] for e in memory.all_entries())
        gleich, anders = [0.6, 0.8, 0.0], [0.0, 0.0, 1.0]
        with mock.patch.object(indexdb, "notiz_vektoren", return_value={a: gleich, b: gleich, c: anders}):
            erg = memory.consolidate()
        self.assertEqual({"before": 3, "after": 2, "merged": 1}, erg)
        self.assertEqual(["Am liebsten grünen Tee am Morgen", "Das Projekt heißt Beispiel"],
                         sorted(e["text"] for e in memory.all_entries()))


class Suche(_Basis):
    def test_details_kommen_in_den_index(self):
        memory.detail_schreiben("tee", "Grüner Tee aus Beispielland")
        self.merken(text="A mag Tee.", name="tee")
        notiz = memory.all_entries()[0]
        gespeichert = []
        with mock.patch.object(indexdb, "_upsert_geordnet", side_effect=lambda k, r, p, d=None: gespeichert.append(
                (k, r, p)) or 1), \
                mock.patch.object(indexdb, "_connect") as con:
            con.return_value.execute.return_value = [("note:999", "a", indexdb.SCHNITT),
                                                     ("detail:alt", "b", indexdb.SCHNITT)]
            indexdb.ingest_all_notes()
        self.assertEqual([("note", f"note:{notiz['id']}", [f"({notiz['kind']}) A mag Tee. (📎 detail:tee)"]),
                          ("note", "detail:tee", ["tee.md\nGrüner Tee aus Beispielland"])], gespeichert)
        self.assertEqual(["note:999", "detail:alt"], self.dropped)               # Verwaiste fliegen raus

    def test_unveraenderte_notizen_werden_nicht_neu_geschrieben(self):
        memory.detail_schreiben("tee", "Grüner Tee aus Beispielland")
        self.merken(text="A mag Tee.", name="tee")
        echt, geschrieben = indexdb._upsert_geordnet, []
        with mock.patch.object(indexdb, "_DB_NEW", self.ordner / "memory.db"), \
                mock.patch.object(indexdb, "_DB_OLD", self.ordner / "index.sqlite"), \
                mock.patch.object(indexdb, "_einbetten", return_value=None), \
                mock.patch.object(indexdb, "_upsert_geordnet", side_effect=lambda k, r, p, d=None: (
                    geschrieben.append(r), echt(k, r, p, d))[1]):
            indexdb.ingest_all_notes()
            erster = list(geschrieben)
            indexdb.ingest_all_notes()                                           # nichts geändert
        self.assertEqual(2, len(erster))
        self.assertEqual(erster, geschrieben)

    def _db(self):
        return (mock.patch.object(indexdb, "_DB_NEW", self.ordner / "memory.db"),
                mock.patch.object(indexdb, "_DB_OLD", self.ordner / "index.sqlite"))

    def test_notiz_vektoren_aus_der_datenbank(self):
        neu, alt = self._db()
        with neu, alt, mock.patch.object(indexdb, "_kennung", return_value="test-3"), \
                mock.patch.object(indexdb, "_einbetten", side_effect=lambda t: [[3.0, 4.0, 0.0] for _ in t]):
            indexdb._upsert_geordnet("note", "note:7", ["(fakt) Eine Notiz mit genug Text."])
            indexdb._upsert_geordnet("note", "detail:tee", ["tee.md\nDetails mit genug Text."])
            vektoren = indexdb.notiz_vektoren()
        self.assertEqual([7], list(vektoren))                                    # Details zählen nicht
        self.assertAlmostEqual(0.6, float(vektoren[7][0]), places=5)             # normiert

    def test_skills_nur_bei_aenderung(self):
        skill = self.ordner / "skill.md"
        skill.write_text("# Ein Skill\nMit genug Text für einen Brocken.", encoding="utf-8")
        echt, geschrieben = indexdb._upsert_geordnet, []
        neu, alt = self._db()
        with neu, alt, mock.patch.object(indexdb, "_einbetten", return_value=None), \
                mock.patch("learn.list_skills", return_value=[(skill, "Ein Skill")]), \
                mock.patch("learn.list_snippets", return_value=[]), \
                mock.patch.object(indexdb, "_upsert_geordnet", side_effect=lambda k, r, p, d=None: (
                    geschrieben.append(r), echt(k, r, p, d))[1]):
            indexdb.ingest_learned()
            indexdb.ingest_learned()                                             # unverändert
            skill.write_text("# Ein Skill\nJetzt mit anderem Text im Brocken.", encoding="utf-8")
            indexdb.ingest_learned()
        self.assertEqual(["skill:skill.md", "skill:skill.md"], geschrieben)


class PromptBlock(unittest.TestCase):
    def test_folge_brocken_im_kontext_zaehlt_als_im_kontext(self):
        treffer = [{"text": "[Nutzer] zweiter Teil der langen Nachricht über den Garten", "ref": "chat:1"},
                   {"text": "Nemi: eine alte Antwort, die nicht mehr im Kontext steht", "ref": "chat:1"}]
        kontext = ["Erster Teil. zweiter Teil der langen Nachricht über den Garten"]
        with mock.patch.object(indexdb, "suchen", return_value=treffer):
            self.assertEqual([treffer[1]], indexdb._vorhin_im_chat("Garten", "chat:1", kontext))

    def test_kern_und_berichte_fallen_vor_dem_kuerzen_heraus(self):
        hits = ([{"kind": "bericht", "ref": f"bericht:{i}.md", "text": "Bericht", "datum": ""} for i in range(3)]
                + [{"kind": "note", "ref": "note:1", "text": "Kern", "datum": ""}]
                + [{"kind": "chat", "ref": f"chat:{i}", "text": f"Treffer {i}", "datum": ""} for i in range(4)])
        with mock.patch.object(indexdb, "search", return_value=hits):
            block = indexdb._treffer_block("frage", "", [], 0, ausser={"note:1"})
        self.assertEqual(["chat:0", "chat:1", "chat:2"],
                         [z.split("/", 1)[1].split("]")[0] for z in block.splitlines() if z.startswith("- [")])

    def test_kern_refs_aus_dem_kern_gedaechtnis(self):
        import persona
        kern = "\n\n# Dein Kern-Gedächtnis\nVorlieben:\n- [#12] Kurze Antworten (📎 detail:kurz)\n- [#3] Tee"
        with mock.patch.object(persona, "kern_gedaechtnis", return_value=kern):
            self.assertEqual({"note:12", "note:3"}, persona.kern_refs())
        self.assertIsNone(persona._thema_von("# Dein Kern-Gedächtnis (gelernt – gilt in jedem Gespräch)"))


if __name__ == "__main__":
    unittest.main()
