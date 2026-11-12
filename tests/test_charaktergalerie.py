"""Charakter-Galerie (/charaktere): Vorlagen, Auswahl (ab 18, bis zu 8 Eigenschaften), Aussehen, Vorschläge der KI,
Porträts, ✨ Verbessern, Namen, Übernehmen ins Profil – Bausteine, Sitzungs-Brücke und Fenster, ohne Krea und ohne echtes Modell.

python -m unittest tests.test_charaktergalerie
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen         # Qt-Fenster nach den Tests dieses Moduls löschen

import charaktergalerie as CG                          # noqa: E402

AUSSEHEN = {"haar_laenge": "lang", "haar_art": "gewellt", "haar_farbe": "#b5562c", "augen_farbe": "#4f8a4a",
            "busen_groesse": "groß", "busen_form": "natürlich", "taille": "schlank", "po_groesse": "groß",
            "po_form": "rund", "po_spannung": "normal"}
NAMEN = [{"name": n, "art": a, "warum": "klingt warm"} for n, a in
         (("Lumi", "passend"), ("Ava", "passend"), ("Ines", "passend"), ("Zora", "frei"), ("Keiko", "frei"),
          ("Ylva", "frei"))]
VORSCHLAEGE = [{"name": f"Figur{i}", "kurz": "ruhig und klug", "wesen": "Redet leise.", "ton": "sachlich",
                "kern": f"long dark hair number {i}, green eyes, black coat", "stil": "soft light"} for i in range(7)]


class Bausteine(unittest.TestCase):

    def test_vorlagen(self):
        self.assertEqual(9, len(CG.VORLAGEN))
        self.assertEqual(9, len({v["key"] for v in CG.VORLAGEN}))
        for v in CG.VORLAGEN:
            with self.subTest(v=v["key"]):
                self.assertGreaterEqual(v["alter"], CG.ALTER_MIN)
                self.assertIn(v["geschlecht"], CG.GESCHLECHTER)
                CG.auswahl(v["geschlecht"], v["alter"], v["eigenschaften"])          # nur echte Eigenschaften

    def test_auswahl(self):
        self.assertEqual(("m", 30, ["ruhig", "Gaming"]), CG.auswahl("männlich", "30", ["ruhig", "Gaming", "quatsch"]))
        for falsch in (("x", 30, ["ruhig"]), ("w", 17, ["ruhig"]), ("w", "alt", ["ruhig"]), ("w", 30, []),
                       ("w", 30, [e for liste in CG.KATEGORIEN.values() for e in liste][:9])):
            with self.subTest(falsch=falsch[:2]), self.assertRaises(ValueError):
                CG.auswahl(*falsch)
        self.assertEqual(99, CG.auswahl("n", 150, ["ruhig"])[1])

    def test_portraet_immer_erwachsen(self):
        p = CG.portraet_prompt("w", 18, "red hair, freckles", "")
        self.assertIn("single adult woman, around 18 years old", p)
        self.assertIn("Red hair, freckles", p)
        self.assertIn(CG.STIL_STANDARD, p)
        self.assertIn("adult man, around 40", CG.portraet_prompt("m", 40, "beard", "x"))
        self.assertIn("androgynous person", CG.portraet_prompt("n", 30, "x", "y"))

    def test_vorschlaege_lesen(self):
        text = "<think>hm</think>Hier:\n```json\n" + json.dumps(VORSCHLAEGE + [{"name": "", "kern": "x"}, "Unsinn"]) + "\n```"
        liste = CG.vorschlaege_lesen(text, "w", 25, ["fröhlich", "verspielt"])
        self.assertEqual(CG.ANZAHL_VORSCHLAEGE, len(liste))
        self.assertEqual("verspielt", liste[0]["ton"])                        # Umgang gewinnt
        self.assertTrue(all(v["seed"] and v["geschlecht"] == "w" and v["alter"] == 25 for v in liste))
        with self.assertRaises(ValueError):
            CG.vorschlaege_lesen("keine Liste", "w", 25, ["ruhig"])
        with self.assertRaises(ValueError):
            CG.vorschlaege_lesen("[{\"name\": \"\"}]", "w", 25, ["ruhig"])

    def test_verbessern(self):
        auftrag = CG.verbessern_auftrag("kern", "red hair", {"name": "Lina", "geschlecht": "w", "alter": 26,
                                                             "eigenschaften": ["frech"], "kurz": "frech"}, "mehr Locken")
        self.assertIn("Aussehen", auftrag)
        self.assertIn("mehr Locken", auftrag)
        self.assertIn("red hair", auftrag)
        self.assertNotIn('"kern"', auftrag)                                      # das Feld selbst nicht doppelt
        self.assertEqual("Curly red hair", CG.verbessert_lesen('<think>x</think> "Curly red hair" ', "kern"))
        with self.assertRaises(ValueError):
            CG.verbessern_auftrag("passwort", "", {})

    def test_charakter_daten(self):
        d = CG.charakter_daten({"name": "Kai", "geschlecht": "m", "alter": 19, "kern": "short hair.", "seed": 7})
        self.assertEqual("an adult man around 19 years old, short hair", d["kern"])
        self.assertEqual(7, d["seed_referenz"])
        self.assertIn("never younger than 18", d["regeln"][0])


class Aussehen(unittest.TestCase):

    def test_koerperform(self):
        for erwartet, a in (("", {}), ("Kurvig", AUSSEHEN), ("Voll", {"taille": "kräftig", "po_groesse": "groß"}),
                            ("Trainiert", {"po_spannung": "durchtrainiert"}), ("Trainiert", {"brust": "muskulös"}),
                            ("Trainiert", {"taille": "sportlich schlank"}),
                            ("Kurvig", {"taille": "sportlich schlank", "busen_groesse": "sehr groß"}),
                            ("Zierlich", {"taille": "schlank", "busen_groesse": "klein"}),
                            ("Zierlich", {"busen_groesse": "klein", "po_groesse": "klein"}),
                            ("Normal", {"taille": "normal", "po_form": "rund"})):
            with self.subTest(a=a):
                self.assertEqual(erwartet, CG.koerperform(a))

    def test_pruefen(self):
        roh = {**AUSSEHEN, "brust": "breit", "haar_farbe": "#B5562C", "augen_farbe": "grün", "taille": "riesig",
               "unbekannt": "x", "koerperform": "Voll"}
        a = CG.aussehen_pruefen(roh, "w")
        self.assertEqual("#b5562c", a["haar_farbe"])
        self.assertNotIn("augen_farbe", a)                                       # nur Hex
        self.assertNotIn("brust", a)                                             # Frau: Busen statt Brust
        self.assertNotIn("taille", a)
        self.assertNotIn("unbekannt", a)
        self.assertEqual("Kurvig", a["koerperform"])                             # ohne „fest“ errechnet
        m = CG.aussehen_pruefen(roh, "m")
        self.assertEqual(("breit", False), (m["brust"], "busen_groesse" in m))
        n = CG.aussehen_pruefen(roh, "n")
        self.assertTrue("brust" in n and "busen_groesse" in n)
        fest = CG.aussehen_pruefen({**AUSSEHEN, "koerperform": "Trainiert", "koerperform_fest": True}, "w")
        self.assertEqual(("Trainiert", True), (fest["koerperform"], fest["koerperform_fest"]))
        self.assertEqual({}, CG.aussehen_pruefen("quatsch", "w"))

    def test_farben(self):
        self.assertEqual("Kupfer", CG.farbe_anzeige("#b5562c", CG.HAAR_FARBEN))
        self.assertEqual("≈ Kupfer", CG.farbe_anzeige("#b85a30", CG.HAAR_FARBEN))
        self.assertEqual("egal", CG.farbe_anzeige("", CG.AUGEN_FARBEN))
        self.assertEqual("blue", CG.farbe_naechste("#3070d0", CG.AUGEN_FARBEN)[2])

    def test_kern_aus_und_tauschen(self):
        import charakter
        a = CG.aussehen_pruefen(AUSSEHEN, "w")
        kern = CG.kern_aus(a)
        self.assertEqual("long wavy copper hair, green eyes, a large natural bust, a slim waist, a large round butt, "
                         "curvy build", kern)
        self.assertEqual([], charakter.kleidung_im_kern(kern))
        self.assertEqual("short black hair in a ponytail",
                         CG.kern_aus({"haar_laenge": "kurz", "haar_art": "Pferdeschwanz", "haar_farbe": "#151515"}))
        self.assertEqual("", CG.kern_aus({}))
        voll = f"an adult woman around 25 years old, {kern}, freckles, warm smile"
        neu = CG.aussehen_pruefen({**AUSSEHEN, "haar_farbe": "#151515"}, "w")
        self.assertEqual(f"an adult woman around 25 years old, {CG.kern_aus(neu)}, freckles, warm smile",
                         CG.kern_neu(voll, a, neu))
        self.assertEqual("an adult woman around 25 years old, freckles, warm smile", CG.kern_neu(voll, a, {}))
        self.assertEqual("an adult man around 30 years old, green eyes, beard",          # alter Kern ohne Angaben
                         CG.kern_neu("an adult man around 30 years old, beard", {}, {"augen_farbe": "#4f8a4a"}))

    def test_vorschlaege_mit_aussehen(self):
        a = CG.aussehen_pruefen(AUSSEHEN, "w")
        auftrag = CG.vorschlag_auftrag("w", 25, ["ruhig"], aussehen=a)
        self.assertIn("steht fest", auftrag)
        self.assertIn("Haare lang gewellt Kupfer", auftrag)
        self.assertNotIn("steht fest", CG.vorschlag_auftrag("w", 25, ["ruhig"]))
        liste = CG.vorschlaege_lesen(json.dumps([{**v, "outfit": "a black coat"} for v in VORSCHLAEGE]), "w", 25,
                                     ["ruhig"], aussehen=a)
        self.assertTrue(all(v["kern"].startswith(CG.kern_aus(a) + ", long dark hair") for v in liste))
        self.assertEqual(("a black coat", a), (liste[0]["outfit"], liste[0]["aussehen"]))
        self.assertIn("Wearing a black coat.", CG.portraet_prompt("w", 25, liste[0]["kern"], "", "a black coat"))

    def test_namen(self):
        text = "<think>x</think>" + json.dumps(NAMEN + [{"name": "Lumi"}, {"name": "R2D2"}, "Mara", {"name": ""}])
        liste = CG.namen_lesen(text)
        self.assertEqual(CG.ANZAHL_NAMEN, len(liste))
        self.assertEqual(["passend"] * 3 + ["frei"] * 3, [n["art"] for n in liste])
        self.assertEqual(["Mara"], [n["name"] for n in CG.namen_lesen(text, [n["name"] for n in NAMEN])])
        with self.assertRaises(ValueError):
            CG.namen_lesen(json.dumps(NAMEN), [n["name"].upper() for n in NAMEN])
        auftrag = CG.namen_auftrag({"geschlecht": "w", "alter": 25, "eigenschaften": ["ruhig"], "kern": "red hair"},
                                   ["Ava"])
        self.assertIn("red hair", auftrag)
        self.assertIn("Nicht noch einmal: Ava", auftrag)

    def test_charakter_daten_mit_aussehen(self):
        import charakter
        d = CG.charakter_daten({"name": "Ava", "geschlecht": "w", "alter": 25, "kern": "red hair", "seed": 3,
                                "outfit": "a green dress.", "aussehen": {**AUSSEHEN, "brust": "breit"}})
        self.assertEqual({"standard": "a green dress"}, d["outfits"])
        self.assertEqual(("w", "Kurvig", False), (d["aussehen"]["geschlecht"], d["aussehen"]["koerperform"],
                                                  "brust" in d["aussehen"]))
        self.assertEqual(d["aussehen"], charakter._sauber(d)["aussehen"])
        self.assertNotIn("aussehen", CG.charakter_daten({"name": "X", "kern": "x"}))


class Gespraech(unittest.TestCase):

    def test_aenderungen_pruefen(self):
        aend = CG.editor_aenderungen({"geschlecht": "weiblich", "alter": "24", "haar_farbe": "#A0F",
                                      "aussehen": {"haar_laenge": "lang", "taille": "riesig", "augen_farbe": "",
                                                   "busen_form": ""},
                                      "eigenschaften": ["fröhlich", "unbekannt"], "idee": " liebt  Regen ",
                                      "passwort": "x"})
        self.assertEqual({"geschlecht": "w", "alter": 24, "eigenschaften": ["fröhlich"], "idee": "liebt Regen",
                          "aussehen": {"haar_laenge": "lang", "augen_farbe": "", "busen_form": ""}}, aend)
        self.assertNotIn("alter", CG.editor_aenderungen({"alter": 16}))               # nie unter 18
        self.assertEqual({}, CG.editor_aenderungen("quatsch"))
        self.assertEqual("m", CG.editor_aenderungen({"geschlecht": "Mann"})["geschlecht"])

    def test_lesen(self):
        text = '<think>x</think>```json\n{"antwort": "Lila passt! Lang oder kurz?", ' \
               '"felder": {"haar_farbe": "#7d4fc0"}}\n```'
        self.assertEqual(("Lila passt! Lang oder kurz?", {"aussehen": {"haar_farbe": "#7d4fc0"}}),
                         CG.editor_lesen(text))
        self.assertEqual(("Gern, erzähl mehr.", {}), CG.editor_lesen("Gern, erzähl mehr."))   # ohne JSON

    def test_stand_anwenden(self):
        stand = {"geschlecht": "w", "alter": 25, "aussehen": CG.aussehen_pruefen(AUSSEHEN, "w"),
                 "eigenschaften": ["ruhig"], "idee": ""}
        neu = CG.stand_anwenden(stand, {"aussehen": {"haar_farbe": "#151515", "augen_farbe": "",
                                                     "koerperform": "Zierlich"}})
        self.assertEqual("#151515", neu["aussehen"]["haar_farbe"])
        self.assertNotIn("augen_farbe", neu["aussehen"])                         # leer = egal
        self.assertEqual(("Zierlich", True), (neu["aussehen"]["koerperform"], neu["aussehen"]["koerperform_fest"]))
        self.assertEqual("#b5562c", stand["aussehen"]["haar_farbe"])            # alter Stand unberührt
        mann = CG.stand_anwenden(neu, {"geschlecht": "m"})
        self.assertNotIn("busen_groesse", mann["aussehen"])
        self.assertEqual(["ruhig"], mann["eigenschaften"])                       # nicht genannt: bleibt

    def test_auftrag(self):
        frage = CG.editor_auftrag([{"wer": "du", "text": "pinke Haare"}, {"wer": "ki", "text": "Lang?"}], "ja",
                                  {"geschlecht": "w"})
        self.assertIn("Person: pinke Haare\nKI: Lang?\nPerson: ja", frage)
        self.assertIn('"geschlecht": "w"', frage)
        self.assertIn("haar_laenge: sehr kurz | kurz", CG.EDITOR_SYSTEM)
        self.assertIn("Eigene Wünsche, bei allen berücksichtigen: liebt Regen",
                      CG.vorschlag_auftrag("w", 25, ["ruhig"], idee="liebt Regen"))


FREI = {"aussehen": "Pinke Locken mit einer silbernen Strähne, ein Auge grün, eins blau, Sommersprossen.",
        "wesen": "Verträumt, summt beim Nachdenken.", "eigenheiten": "Trägt gern weite Pullis, liebt Regenwetter."}


class FreierWeg(unittest.TestCase):

    def test_ton_locker(self):
        for system in (CG.EDITOR_SYSTEM, CG.FREI_SYSTEM):
            self.assertIn("locker", system)
            self.assertIn("nie steif", system)
            self.assertIn("duzt", system)

    def test_lesen(self):
        text = json.dumps({"antwort": "Oh, wie schön! 🌧", "felder": {
            "aussehen": FREI["aussehen"] + "  ", "geschlecht": "weiblich", "alter": 15, "taille": "schlank",
            "wesen": "x" * 2000}})
        antwort, aend = CG.frei_lesen(text)
        self.assertEqual("Oh, wie schön! 🌧", antwort)
        self.assertEqual(FREI["aussehen"], aend["aussehen"])
        self.assertEqual("w", aend["geschlecht"])
        self.assertNotIn("alter", aend)                                         # nie unter 18
        self.assertNotIn("taille", aend)                                        # keine Auswahllisten hier
        self.assertEqual(CG.MAX_ABSCHNITT, len(aend["wesen"]))
        self.assertEqual(("Na klar!", {}), CG.frei_lesen("Na klar!"))

    def test_varianten_auftrag_und_lesen(self):
        auftrag = CG.frei_varianten_auftrag("w", 24, FREI)
        self.assertIn("verbindlich", auftrag)
        self.assertIn("ein Auge grün, eins blau", auftrag)
        self.assertIn("Eigenheiten & Stil: Trägt gern weite Pullis", auftrag)
        liste = CG.vorschlaege_lesen(json.dumps([{**v, "kurz": ""} for v in VORSCHLAEGE]), "w", 24, [], frei=FREI)
        self.assertEqual(FREI, liste[0]["frei"])
        self.assertEqual("Redet leise.", liste[0]["kurz"])                     # ohne Eigenschaften: aus dem Wesen
        self.assertEqual({"aussehen": FREI["aussehen"]}, CG.charakter_daten(
            {**liste[0], "frei": {"aussehen": FREI["aussehen"]}})["steckbrief"])


class Uebernehmen(unittest.TestCase):
    """Legt in den Testdaten an (paths.TESTDATEN) und räumt danach wieder ab."""

    def test_persoenlichkeit_charakterdatei_und_portraet(self):
        import charakter
        import persoenlichkeiten as PS
        import profilordner
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        bild = tmp / "p.png"
        bild.write_bytes(b"\x89PNG\r\n\x1a\nTEST")
        name = f"Galerietest{os.getpid()}"
        erg = CG.uebernehmen({"name": name + "!?", "kurz": "frech und klug", "wesen": "Redet gern in Rätseln.",
                              "geschlecht": "n", "alter": 21, "eigenschaften": ["frech", "Kunst", "verspielt"],
                              "kern": "pink undercut, piercings", "stil": "neon light", "seed": 4242,
                              "aussehen": {"haar_farbe": "#e0388f", "brust": "schmal"}}, str(bild))
        self.addCleanup(lambda: Path(erg["pfad"]).unlink(missing_ok=True))
        self.addCleanup(shutil.rmtree, profilordner.ordner(name=erg["name"], anlegen=False), True)
        self.addCleanup(lambda: charakter.sicherung(erg["name"]).unlink(missing_ok=True))
        self.assertTrue(charakter.sicherung(erg["name"]).is_file())               # Punkt B gleich mit angelegt
        self.assertEqual(name, erg["name"])                                      # Sonderzeichen raus
        text = Path(erg["pfad"]).read_text(encoding="utf-8")
        self.assertIn("Du bist 21 Jahre alt (erwachsen)", text)
        self.assertIn("frech, Kunst, verspielt", text)
        self.assertIn("Redet gern in Rätseln.", text)
        self.assertIn("kein Geschlecht", text)
        self.assertIn("Verspielt & charmant", text)
        d = charakter.laden(erg["name"])
        self.assertEqual(4242, d["seed_referenz"])
        self.assertTrue(d["kern"].startswith("an adult androgynous person around 21 years old, pink undercut"))
        self.assertEqual("neon light", d["stil"])
        self.assertTrue(Path(erg["bild"]).is_file())
        self.assertIn(str(profilordner.ordner("Bilder", erg["name"], anlegen=False)), erg["bild"])
        self.assertIn(erg["key"], PS.list_all())
        # später verbessern: Aussehen und Stil neu, Stimme bleibt
        charakter.stimme_setzen({"beschreibung": "warme Stimme", "seed": 3}, erg["name"])
        CG.bearbeitung_speichern(erg["name"], "blue undercut", "soft light")
        d = charakter.roh(erg["name"])
        self.assertEqual(("blue undercut", "soft light", 4242), (d["kern"], d["stil"], d["seed_referenz"]))
        self.assertEqual("warme Stimme", d["voice"]["beschreibung"])
        geladen = CG.bearbeiten_laden(erg["name"])
        self.assertEqual(("blue undercut", "n", "#e0388f"), (geladen["kern"], geladen["geschlecht"],
                                                              geladen["aussehen"]["haar_farbe"]))
        CG.bearbeitung_speichern(erg["name"], "blue undercut", "soft light", aussehen={"haar_farbe": "#2f5fd0"})
        d = charakter.roh(erg["name"])
        self.assertEqual({"haar_farbe": "#2f5fd0", "geschlecht": "n"}, d["aussehen"])
        self.assertEqual("warme Stimme", d["voice"]["beschreibung"])
        with self.assertRaises(ValueError):
            CG.uebernehmen({"name": "X", "kern": "x", "geschlecht": "w", "alter": 16, "eigenschaften": ["ruhig"]})

    def test_freier_weg(self):
        import charakter
        import profilordner
        name = f"Freitest{os.getpid()}"
        erg = CG.uebernehmen({"name": name, "kurz": "verträumt", "wesen": "Summt beim Nachdenken.",
                              "geschlecht": "w", "alter": 24, "eigenschaften": [], "kern": "pink curls",
                              "outfit": "a wide sweater", "frei": FREI, "seed": 5})
        self.addCleanup(lambda: Path(erg["pfad"]).unlink(missing_ok=True))
        self.addCleanup(shutil.rmtree, profilordner.ordner(name=erg["name"], anlegen=False), True)
        self.addCleanup(lambda: charakter.sicherung(erg["name"]).unlink(missing_ok=True))
        text = Path(erg["pfad"]).read_text(encoding="utf-8")
        self.assertIn("Du bist 24 Jahre alt", text)
        self.assertNotIn("Deine Eigenschaften", text)                           # keine Liste im freien Weg
        self.assertIn("Eigenheiten & Stil: Trägt gern weite Pullis", text)
        d = charakter.roh(erg["name"])
        self.assertEqual(FREI, d["steckbrief"])
        self.assertEqual({"standard": "a wide sweater"}, d["outfits"])
        with self.assertRaises(ValueError):
            CG.uebernehmen({"name": "Y", "kern": "x", "geschlecht": "w", "alter": 17, "frei": FREI})


class _Bruecke:
    def __init__(self, loop, handlers, **_):
        self.handlers, self.ereignisse = handlers, []

    def senden(self, ev):
        self.ereignisse.append(ev)

    def verbunden(self):
        return True


class Sitzung(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        import main
        self.main = main
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prompts = []

        def malen(prompt, seed=None):
            self.prompts.append((prompt, seed))
            p = self.tmp / f"roh_{len(self.prompts)}.png"
            p.write_bytes(b"PNG")
            return str(p)
        import imagegen
        import gui_bruecke
        for p in (mock.patch.object(gui_bruecke, "GuiBruecke", _Bruecke),
                  mock.patch.object(imagegen, "missing_reason_aktiv", return_value=""),
                  mock.patch.object(imagegen, "paint", side_effect=malen),
                  mock.patch.object(CG, "ordner", return_value=self.tmp / "Galerie")):
            p.start()
            self.addCleanup(p.stop)

        async def ask_once(prompt, system):
            self.frage = (prompt, system)
            return json.dumps(VORSCHLAEGE)
        self.ctx = NS(backend=NS(ask_once=ask_once))
        self.bruecke = await main.start_charaktere(self.ctx)

    async def warten(self, art, anzahl=1):
        for _ in range(300):
            if sum(1 for e in self.bruecke.ereignisse if e["t"] == art) >= anzahl:
                return
            await asyncio.sleep(0.01)
        self.fail(f"kein {art}: {self.bruecke.ereignisse}")

    async def test_start_malt_fehlende_vorlagen(self):
        self.bruecke.handlers["start"]({})
        await self.warten("vorlage_bild", 9)
        start = next(e for e in self.bruecke.ereignisse if e["t"] == "start")
        self.assertEqual(9, len(start["vorlagen"]))
        self.assertEqual(CG.MAX_EIGENSCHAFTEN, start["max_eigenschaften"])
        self.assertTrue(all(CG.vorlagen_bild(v["key"]).is_file() for v in CG.VORLAGEN))
        self.assertTrue(all("adult" in p for p, _ in self.prompts))
        self.assertEqual(CG.VORLAGEN[0]["seed"], self.prompts[0][1])           # fester Seed = gleiches Gesicht

    async def test_vorschlaege_mit_portraets(self):
        self.bruecke.handlers["vorschlaege"]({"geschlecht": "m", "alter": 33, "eigenschaften": ["ruhig", "Bücher"]})
        await self.warten("vorschlag_bild", CG.ANZAHL_VORSCHLAEGE)
        self.assertIn("männlich", self.frage[0])
        liste = next(e for e in self.bruecke.ereignisse if e["t"] == "vorschlaege")["liste"]
        self.assertEqual(CG.ANZAHL_VORSCHLAEGE, len(liste))
        bilder = [e["pfad"] for e in self.bruecke.ereignisse if e["t"] == "vorschlag_bild"]
        self.assertTrue(all(Path(b).is_file() and "Entwuerfe" in b for b in bilder))
        self.assertIn("single adult man, around 33 years old", self.prompts[0][0])

    async def test_auswahl_unter_18_und_ohne_modell(self):
        self.bruecke.handlers["vorschlaege"]({"geschlecht": "w", "alter": 15, "eigenschaften": ["ruhig"]})
        await self.warten("fehler")
        self.assertIn("mindestens 18", self.bruecke.ereignisse[-1]["text"])
        self.ctx.backend = None
        self.bruecke.handlers["verbessern"]({"feld": "kern", "entwurf": {}})
        await self.warten("fehler", 2)
        self.assertIn("Kein Modell", self.bruecke.ereignisse[-1]["text"])
        self.assertEqual([], self.prompts)

    async def test_vorschlaege_mit_aussehen(self):
        self.bruecke.handlers["vorschlaege"]({"geschlecht": "w", "alter": 25, "eigenschaften": ["ruhig"],
                                              "aussehen": AUSSEHEN})
        await self.warten("vorschlag_bild", CG.ANZAHL_VORSCHLAEGE)
        self.assertIn("steht fest", self.frage[0])
        self.assertIn("Long wavy copper hair, green eyes", self.prompts[0][0])

    async def test_namen(self):
        async def ask_once(prompt, system):
            self.frage = (prompt, system)
            return json.dumps(NAMEN)
        self.ctx.backend = NS(ask_once=ask_once)
        self.bruecke.handlers["namen"]({"entwurf": {"geschlecht": "w", "alter": 25, "kern": "red hair"},
                                        "bisher": ["Mara"], "nr": 4})
        await self.warten("namen")
        ev = next(e for e in self.bruecke.ereignisse if e["t"] == "namen")
        self.assertEqual((6, 4), (len(ev["liste"]), ev["nr"]))
        self.assertEqual(CG.NAMEN_SYSTEM, self.frage[1])
        self.assertIn("Nicht noch einmal: Mara", self.frage[0])

    async def test_gespraech(self):
        async def ask_once(prompt, system):
            self.frage = (prompt, system)
            return json.dumps({"antwort": "Lila, gern!", "felder": {"haar_farbe": "#7d4fc0", "alter": 12}})
        self.ctx.backend = NS(ask_once=ask_once)
        self.bruecke.handlers["editor"]({"text": "lila Haare", "verlauf": [], "stand": {"geschlecht": "w"},
                                         "nr": 2})
        await self.warten("editor")
        ev = next(e for e in self.bruecke.ereignisse if e["t"] == "editor")
        self.assertEqual(("Lila, gern!", {"aussehen": {"haar_farbe": "#7d4fc0"}}, 2),
                         (ev["antwort"], ev["aenderungen"], ev["nr"]))
        self.assertEqual(CG.EDITOR_SYSTEM, self.frage[1])

    async def test_freier_weg(self):
        async def ask_once(prompt, system):
            self.frage = (prompt, system)
            if system == CG.FREI_SYSTEM:
                return json.dumps({"antwort": "Wie toll!", "felder": {"aussehen": FREI["aussehen"]}})
            return json.dumps(VORSCHLAEGE)
        self.ctx.backend = NS(ask_once=ask_once)
        self.bruecke.handlers["frei"]({"text": "pinke Locken", "verlauf": [], "stand": {}, "nr": 3})
        await self.warten("frei")
        ev = next(e for e in self.bruecke.ereignisse if e["t"] == "frei")
        self.assertEqual(("Wie toll!", {"aussehen": FREI["aussehen"]}, 3),
                         (ev["antwort"], ev["aenderungen"], ev["nr"]))
        self.bruecke.handlers["vorschlaege"]({"geschlecht": "w", "alter": 24, "frei": {}})
        await self.warten("fehler")
        self.assertIn("Steckbrief ist noch leer", self.bruecke.ereignisse[-1]["text"])
        self.bruecke.handlers["vorschlaege"]({"geschlecht": "w", "alter": 24, "frei": FREI})
        await self.warten("vorschlag_bild", CG.ANZAHL_VORSCHLAEGE)
        self.assertIn("verbindlich", self.frage[0])
        liste = next(e for e in self.bruecke.ereignisse if e["t"] == "vorschlaege")["liste"]
        self.assertEqual(FREI, liste[0]["frei"])

    async def test_debugbericht_zeile(self):
        import debugbericht
        with mock.patch.object(debugbericht, "ereignis") as ereignis:
            self.ctx.model = "test:modell"
            self.bruecke.handlers["namen"]({"entwurf": {"geschlecht": "w", "alter": 25}, "bisher": [], "nr": 1})
            await self.warten("fehler")                                         # VORSCHLAEGE sind keine Namen
        zeilen = [c.args[0] for c in ereignis.call_args_list if c.args and "Galerie-KI" in c.args[0]]
        self.assertTrue(zeilen and zeilen[0].startswith("Galerie-KI Namen: test:modell ·"), zeilen)

    async def test_verbessern_und_neu_malen(self):
        async def ask_once(prompt, system):
            return "Long silver hair, violet eyes"
        self.ctx.backend = NS(ask_once=ask_once)
        entwurf = {"name": "Mira", "geschlecht": "w", "alter": 24, "eigenschaften": ["verträumt"], "kern": "x",
                   "seed": 11}
        self.bruecke.handlers["verbessern"]({"feld": "kern", "entwurf": entwurf})
        await self.warten("verbessert")
        self.assertEqual("Long silver hair, violet eyes", self.bruecke.ereignisse[-1]["text"])
        self.bruecke.handlers["neu_malen"]({"entwurf": entwurf})
        await self.warten("bild")
        self.assertEqual(11, self.prompts[-1][1])                               # gleiches Gesicht
        self.bruecke.handlers["neu_malen"]({"entwurf": entwurf, "neues_gesicht": True})
        await self.warten("bild", 2)
        self.assertNotEqual(11, self.prompts[-1][1])


class Fenster(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(["test"])

    def setUp(self):
        from PySide6.QtCore import QObject, Signal
        import charakter_fenster as CF
        self.CF = CF

        class Leitung(QObject):
            ereignis = Signal(object)
            getrennt = Signal()

            def __init__(self):
                super().__init__()
                self.gesendet = []

            def senden(self, auftrag):
                self.gesendet.append(auftrag)
        self.lt = Leitung()
        self.f = CF.CharakterFenster(self.lt)
        self.addCleanup(self.f.deleteLater)
        self.f._ereignis({"t": "start", "vorlagen": [{**v, "bild": ""} for v in CG.VORLAGEN],
                          "kategorien": CG.KATEGORIEN, "max_eigenschaften": CG.MAX_EIGENSCHAFTEN, "alter_min": 18,
                          "alter_max": 99, "aktiv": "Nemi"})

    def test_start_und_auswahl(self):
        self.assertEqual({"op": "start"}, self.lt.gesendet[0])
        self.assertEqual(9, len(self.f.vorlagen))
        self.f.stapel.setCurrentWidget(self.f.seite_person)
        self.f.alter.setText("30")
        self.f._person_weiter()
        self.assertIs(self.f.seite_person, self.f.stapel.currentWidget())       # erst das Geschlecht
        self.f._geschlecht("w")
        self.assertIn("sie", self.f.alter_titel.text())
        self.f.alter.setText("17")
        self.f._person_weiter()
        self.assertIs(self.f.seite_person, self.f.stapel.currentWidget())       # unter 18 geht nicht
        self.assertIn("mindestens 18", self.f.status.text())
        self.f.alter.setText("18")
        self.f._person_weiter()
        self.assertIs(self.f.seite_aussehen, self.f.stapel.currentWidget())
        self.assertTrue(self.f.box_brust.isHidden())
        self.f._aussehen_weiter()                                               # alles „egal“ geht
        self.assertEqual({}, self.f.auswahl["aussehen"])
        self.assertFalse(self.f.eig_weiter.isEnabled())                         # noch keine Eigenschaft
        alle = [e for liste in CG.KATEGORIEN.values() for e in liste]
        for e in alle[:10]:
            self.f.chips[e].setChecked(True)
        self.assertEqual(CG.MAX_EIGENSCHAFTEN, len(self.f.auswahl["eigenschaften"]))
        self.assertFalse(self.f.chips[alle[9]].isChecked())
        self.f.eig_weiter.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("vorschlaege", "w", 18, 8), (auftrag["op"], auftrag["geschlecht"], auftrag["alter"],
                                                      len(auftrag["eigenschaften"])))

    def test_schritt_leiste_und_steckbrief(self):
        self.assertTrue(self.f.leiste.isHidden())                               # Galerie: ohne Leiste
        self.f._erschaffen()
        self.assertFalse(self.f.leiste.isHidden())
        self.assertFalse(self.f.rechts.isHidden())
        self.assertFalse(self.f.schritt_knoepfe[1].isEnabled())                 # erst Person
        self.f._geschlecht("w")
        self.f.alter.setText("26")
        self.assertTrue(self.f.schritt_knoepfe[2].isEnabled())
        self.f.schritt_knoepfe[2].click()
        self.assertIs(self.f.seite_eig, self.f.stapel.currentWidget())
        self.assertEqual("✓", self.f.schritt_knoepfe[0].symbol)
        self.f.chips["Musik"].setChecked(True)
        self.f.idee_feld.setText("liebt Regenwetter")
        brief = self.f.steckbrief.text()
        for teil in ("Weiblich", "26 Jahre", "Musik", "liebt Regenwetter"):
            self.assertIn(teil, brief)
        self.f.eig_weiter.click()
        self.assertEqual("liebt Regenwetter", self.lt.gesendet[-1]["idee"])
        self.f._ansehen(dict(CG.VORLAGEN[0]), "", self.f.seite_start)           # Vorlage: ohne Leiste
        self.assertTrue(self.f.leiste.isHidden())

    def test_freier_weg(self):
        self.f._frei_starten()
        self.assertIs(self.f.seite_frei, self.f.stapel.currentWidget())
        self.assertTrue(self.f.leiste.isHidden())
        self.assertTrue(self.f.rechts.isHidden())                               # eigener Steckbrief auf der Seite
        self.f.frei_feld.setText("Sie hat pinke Locken und ist 24")
        self.f.frei_senden_k.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("frei", 1, "Sie hat pinke Locken und ist 24"), (auftrag["op"], auftrag["nr"], auftrag["text"]))
        self.f._ereignis({"t": "frei", "nr": 1, "antwort": "Wie süß! 💖",
                          "aenderungen": {"geschlecht": "w", "alter": 24, "aussehen": FREI["aussehen"]}})
        self.assertEqual(("w", "24", FREI["aussehen"]), (self.f.frei_geschlecht.currentData(), self.f.frei_alter.text(),
                                                       self.f.frei_felder["aussehen"].toPlainText()))
        # frei änderbar: selbst ergänzen, die KI sieht es beim nächsten Mal
        self.f.frei_felder["wesen"].setPlainText(FREI["wesen"])
        self.f.frei_feld.setText("und sie mag Regen")
        self.f._frei_senden()
        self.assertEqual(FREI["wesen"], self.lt.gesendet[-1]["stand"]["wesen"])
        self.assertEqual(2, len(self.lt.gesendet[-1]["verlauf"]))
        # ↺ nimmt die letzte Änderung der KI zurück
        self.f._ereignis({"t": "frei", "nr": 2, "antwort": "Regen!", "aenderungen": {"eigenheiten": "liebt Regen"}})
        self.f.frei_rueck_k.click()
        self.assertEqual("", self.f.frei_felder["eigenheiten"].toPlainText())
        self.assertEqual(FREI["wesen"], self.f.frei_felder["wesen"].toPlainText())   # eigene Eingabe bleibt
        self.f.frei_varianten_k.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("vorschlaege", "w", 24, FREI["aussehen"]),
                         (auftrag["op"], auftrag["geschlecht"], auftrag["alter"], auftrag["frei"]["aussehen"]))
        liste = [{**v, "geschlecht": "w", "alter": 24, "eigenschaften": [], "seed": 5, "frei": FREI}
                 for v in VORSCHLAEGE[:6]]
        self.f._ereignis({"t": "vorschlaege", "liste": liste, "nr": 1})
        self.f._ansehen(self.f.vorschlag_karten[0].entwurf, "", self.f.seite_vorschlaege)
        self.assertTrue(self.f.aussehen_k.isHidden())                           # frei: im Steckbrief ändern
        self.f.uebernehmen_k.click()
        self.assertIs(self.f.seite_namen, self.f.stapel.currentWidget())
        self.f.namen_ok.click()
        self.assertEqual(FREI, self.lt.gesendet[-1]["entwurf"]["frei"])

    def test_gespraech(self):
        self.f._erschaffen()
        self.f.chat_k.click()
        self.assertFalse(self.f.chat_box.isHidden())
        self.f.chat_feld.setText("Eine Frau, 24, lange lila Haare, verträumt")
        self.f.senden_k.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("editor", 1, []), (auftrag["op"], auftrag["nr"], auftrag["verlauf"]))
        self.f._ereignis({"t": "editor", "nr": 1, "antwort": "Schön! Welche Augenfarbe?",
                          "aenderungen": {"geschlecht": "w", "alter": 24, "eigenschaften": ["verträumt"],
                                          "aussehen": {"haar_laenge": "lang", "haar_farbe": "#7d4fc0"}}})
        self.assertTrue(self.f.g_knoepfe["w"].akzent)
        self.assertEqual("24", self.f.alter.text())
        self.assertEqual(("lang", "#7d4fc0"), (self.f.wahlen["haar_laenge"].currentData(), self.f.haar_farbe.hex))
        self.assertTrue(self.f.chips["verträumt"].isChecked())
        self.assertIn("Lila", self.f.steckbrief.text())
        self.assertEqual(2, len(self.f.verlauf))
        # freie Wahl: selbst umstellen geht sofort
        self.f.haar_farbe.tupfer["#e0388f"].click()
        self.assertEqual("#e0388f", self.f.auswahl["aussehen"]["haar_farbe"])
        # ↺ nimmt die letzte Änderung der KI zurück
        self.assertTrue(self.f.rueck_k.isEnabled())
        self.f.rueck_k.click()
        self.assertEqual(("", "", []), (self.f.auswahl["geschlecht"], self.f.alter.text(),
                                        self.f.auswahl["eigenschaften"]))
        self.assertFalse(self.f.chips["verträumt"].isChecked())
        self.assertFalse(self.f.rueck_k.isEnabled())
        self.f._ereignis({"t": "editor", "nr": 0, "antwort": "alt", "aenderungen": {"geschlecht": "m"}})
        self.assertEqual("", self.f.auswahl["geschlecht"])                      # alte Antwort zählt nicht

    def test_aussehen_seite(self):
        self.f._geschlecht("m")
        self.f.alter.setText("40")
        self.f._person_weiter()
        self.assertTrue(self.f.box_busen.isHidden())
        self.assertFalse(self.f.box_brust.isHidden())
        w = self.f.wahlen
        w["haar_laenge"].setCurrentIndex(w["haar_laenge"].findData("kurz"))
        w["brust"].setCurrentIndex(w["brust"].findData("muskulös"))
        self.assertIn("Trainiert", self.f.form_info.text())                     # live errechnet
        self.assertTrue(self.f.form_chips["Trainiert"].akzent)
        self.f.haar_farbe.tupfer["#151515"].click()
        self.assertEqual("Schwarz", self.f.haar_farbe.anzeige.text())
        self.f.haar_farbe.setzen("#123456")
        self.assertIn("#123456", self.f.haar_farbe.anzeige.text())             # eigene Farbe
        self.f.form_chips["Normal"].click()
        self.assertIn("Von dir gewählt: Normal", self.f.form_info.text())
        self.f._aussehen_weiter()
        a = self.f.auswahl["aussehen"]
        self.assertEqual(("kurz", "muskulös", "#123456", "Normal", True),
                         (a["haar_laenge"], a["brust"], a["haar_farbe"], a["koerperform"], a["koerperform_fest"]))
        self.f.chips["ruhig"].setChecked(True)
        self.f.eig_weiter.click()
        self.assertEqual(a, self.lt.gesendet[-1]["aussehen"])
        self.f._person_weiter()                                                 # zurück: Angaben bleiben
        self.assertEqual("muskulös", w["brust"].currentData())
        self.f.form_chips["Normal"].click()                                     # noch einmal = automatisch
        self.assertIn("Automatisch", self.f.form_info.text())

    def test_name_waehlen(self):
        a = CG.aussehen_pruefen(AUSSEHEN, "w")
        entwurf = {**VORSCHLAEGE[0], "kern": CG.kern_aus(a) + ", freckles", "geschlecht": "w", "alter": 25,
                   "eigenschaften": ["ruhig"], "seed": 5, "aussehen": a, "outfit": "a black coat"}
        self.f._ansehen(entwurf, "", self.f.seite_vorschlaege)
        self.assertTrue(self.f.zeilen["name"].isHidden())                       # Name kommt zum Schluss
        self.assertFalse(self.f.zeilen["outfit"].isHidden())
        self.f.uebernehmen_k.click()
        self.assertIs(self.f.seite_namen, self.f.stapel.currentWidget())
        self.assertEqual(("namen", 1, []), (self.lt.gesendet[-1]["op"], self.lt.gesendet[-1]["nr"],
                                            self.lt.gesendet[-1]["bisher"]))
        self.assertEqual("Figur0", self.f.name_feld.text())
        self.f._ereignis({"t": "namen", "liste": NAMEN, "nr": 0})               # alter Auftrag
        self.assertFalse(self.f.namen_knoepfe[0].isEnabled())
        self.f._ereignis({"t": "namen", "liste": NAMEN, "nr": 1})
        self.assertEqual(("Lumi", "✨", "🌈"), (self.f.namen_knoepfe[0].text(), self.f.namen_knoepfe[0].symbol,
                                                self.f.namen_knoepfe[5].symbol))
        self.f.namen_knoepfe[3].click()
        self.assertEqual("Zora", self.f.name_feld.text())
        self.f.wuerfel_k.click()
        self.assertEqual(6, len(self.lt.gesendet[-1]["bisher"]))               # 🎲: die gezeigten nicht wieder
        self.f._ereignis({"t": "namen", "liste": [{"name": "Mara", "art": "frei"}], "nr": 2})
        self.f.name_feld.setText("  Nina!  ")
        self.f.namen_ok.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("uebernehmen", "Nina", a, "a black coat"),
                         (auftrag["op"], auftrag["entwurf"]["name"], auftrag["entwurf"]["aussehen"],
                          auftrag["entwurf"]["outfit"]))

    def test_aussehen_spaeter_aendern(self):
        a = CG.aussehen_pruefen(AUSSEHEN, "w")
        kern = f"an adult woman around 25 years old, {CG.kern_aus(a)}, freckles"
        self.f._ereignis({"t": "bearbeiten", "name": "Nina", "bild": "",
                          "daten": {"name": "Nina", "kern": kern, "stil": "dark", "seed": 9, "geschlecht": "w",
                                    "aussehen": a}})
        self.assertFalse(self.f.aussehen_k.isHidden())
        self.f.aussehen_k.click()
        self.assertIs(self.f.seite_aussehen, self.f.stapel.currentWidget())
        self.assertEqual("gewellt", self.f.wahlen["haar_art"].currentData())     # genau wieder geöffnet
        self.assertEqual("#b5562c", self.f.haar_farbe.hex)
        self.f.haar_farbe.tupfer["#151515"].click()
        self.f._aussehen_weiter()
        self.assertIs(self.f.seite_ansehen, self.f.stapel.currentWidget())
        self.assertIn("long wavy black hair", self.f.felder["kern"].toPlainText())
        self.assertTrue(self.f.felder["kern"].toPlainText().endswith(", freckles"))
        self.f.uebernehmen_k.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("speichern", "#151515", "w"), (auftrag["op"], auftrag["aussehen"]["haar_farbe"],
                                                         auftrag["aussehen"]["geschlecht"]))

    def test_vorschlag_ansehen_verbessern_uebernehmen(self):
        liste = [{**v, "geschlecht": "w", "alter": 25, "eigenschaften": ["ruhig"], "seed": 5} for v in VORSCHLAEGE[:4]]
        self.f._ereignis({"t": "vorschlaege", "liste": liste, "nr": 3})
        self.assertEqual(4, len(self.f.vorschlag_karten))
        self.f._ereignis({"t": "vorschlag_bild", "index": 1, "pfad": "gibts-nicht.png", "nr": 2})   # alter Auftrag
        self.assertEqual("", self.f.vorschlag_karten[1].bild)
        karte = self.f.vorschlag_karten[0]
        self.f._ansehen(karte.entwurf, "", self.f.seite_vorschlaege)
        self.assertEqual("Figur0", self.f.felder["name"].text())
        self.assertTrue(self.f.zeilen["name"].isHidden())
        self.f.wunsch.setText("mehr Locken")
        self.f.funken["kern"].click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("verbessern", "kern", "mehr Locken"), (auftrag["op"], auftrag["feld"], auftrag["wunsch"]))
        self.f._ereignis({"t": "verbessert", "feld": "kern", "text": "curly dark hair"})
        self.assertEqual("curly dark hair", self.f.felder["kern"].toPlainText())
        self.f.uebernehmen_k.click()                                            # → Name wählen
        self.f.namen_ok.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("uebernehmen", "Figur0", "curly dark hair"),
                         (auftrag["op"], auftrag["entwurf"]["name"], auftrag["entwurf"]["kern"]))
        self.f._ereignis({"t": "uebernommen", "name": "Figur0"})
        self.assertIs(self.f.seite_start, self.f.stapel.currentWidget())

    def test_eigenen_charakter_bearbeiten(self):
        self.f._ereignis({"t": "bearbeiten", "name": "Juna", "daten": {"name": "Juna", "kern": "an adult woman",
                                                                        "stil": "dark", "seed": 9}, "bild": ""})
        self.assertIs(self.f.seite_ansehen, self.f.stapel.currentWidget())
        self.assertTrue(self.f.zeilen["name"].isHidden())                       # nur Aussehen und Stil
        self.f.uebernehmen_k.click()
        auftrag = self.lt.gesendet[-1]
        self.assertEqual(("speichern", "an adult woman", "dark", 9),
                         (auftrag["op"], auftrag["kern"], auftrag["stil"], auftrag["seed"]))
        self.f._ereignis({"t": "bearbeiten", "name": "Nemi", "daten": None, "bild": ""})
        self.assertIn("noch keine Charakter-Datei", self.f.status.text())


if __name__ == "__main__":
    unittest.main()
