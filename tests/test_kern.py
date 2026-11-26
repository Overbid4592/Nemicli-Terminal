"""Kern-Sperre: eigene Kern-Datei mit Sicherung, Sperre nur durch den Nutzer, Rückfrage in jedem Modus,
Hintertür über Datei-Werkzeuge, Zwang vor jedem Selbstbild (auch Kugel und mit LoRA), Frisur und Standard-Outfit."""
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

import charakter          # noqa: E402
import paths              # noqa: E402
import profilordner       # noqa: E402

KERN = "an adult woman with long red hair and green eyes"
NEU = "an adult woman with short black hair and brown eyes"


class _Daten(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.daten = Path(tmp.name)
        for p in (mock.patch.object(paths, "DATEN", self.daten),
                  mock.patch.object(profilordner, "aktiv", lambda: "PersonA")):
            p.start()
            self.addCleanup(p.stop)
        charakter.schreiben({"name": "PersonA", "kern": KERN, "seed_referenz": 42, "stil": "photo",
                             "outfits": {"standard": "a white shirt and jeans", "abend": "an evening gown"},
                             "posen": {}, "ausdruecke": {}, "frisuren": {"dutt": "a high bun"}, "regeln": []},
                            "PersonA")


class Ablage(_Daten):
    def test_eigene_datei_mit_sicherung(self):
        a, b = charakter.kern_datei("PersonA"), charakter.kern_sicherung("PersonA")
        self.assertTrue(a.is_file() and b.is_file())
        self.assertEqual(KERN, charakter.kern("PersonA"))
        a.unlink()
        self.assertEqual(KERN, charakter.kern("PersonA"))                 # B springt ein, A kommt zurück
        self.assertTrue(a.is_file())

    def test_kern_datei_gilt_vor_dem_spiegel(self):
        roh = charakter.roh("PersonA")
        roh["kern"] = "a stranger"                                        # jemand ändert nur den Spiegel
        charakter.datei("PersonA").write_text(__import__("json").dumps(roh), encoding="utf-8")
        self.assertEqual(KERN, charakter.laden("PersonA")["kern"])

    def test_zurueck_und_verlauf(self):
        charakter.kern_setzen(NEU, "PersonA")
        self.assertEqual(NEU, charakter.kern("PersonA"))
        self.assertEqual(KERN, charakter.kern_zurueck("PersonA"))
        self.assertEqual(KERN, charakter.kern("PersonA"))
        self.assertEqual(NEU, charakter.kern_stand("PersonA")["vorher"][0])

    def test_ohne_charakter_datei_legt_kern_setzen_sie_an(self):
        charakter.kern_setzen(KERN, "PersonB")
        d = charakter.laden("PersonB")
        self.assertEqual(KERN, d["kern"])
        self.assertIsNotNone(d["seed_referenz"])


class Sperre(_Daten):
    def test_ki_aendert_gesperrten_kern_nur_mit_ja(self):
        charakter.kern_sperren(True, "PersonA")
        self.assertIsNotNone(charakter.kern_aenderung({"kern": NEU}))
        self.assertIsNone(charakter.kern_aenderung({"kern": KERN}))          # gleicher Kern: nichts zu fragen
        self.assertIsNone(charakter.kern_aenderung({"stil": "anime"}))
        text, ok = charakter.aendern({"kern": NEU, "stil": "anime"})          # ohne Ja: Kern bleibt
        self.assertTrue(ok)
        self.assertIn("gesperrt", text)
        self.assertEqual(KERN, charakter.kern("PersonA"))
        self.assertEqual("anime", charakter.laden("PersonA")["stil"])
        charakter.aendern({"kern": NEU, "_kern_ja": True})                   # das Ja des Nutzers
        self.assertEqual(NEU, charakter.kern("PersonA"))

    def test_offen_darf_die_ki_wie_bisher(self):
        self.assertIsNone(charakter.kern_aenderung({"kern": NEU}))
        charakter.aendern({"kern": NEU})
        self.assertEqual(NEU, charakter.kern("PersonA"))

    def test_rueckfrage_in_jedem_modus_auch_auto(self):
        import modes
        import sicherheit
        charakter.kern_sperren(True, "PersonA")
        act = {"tool": "charakter_aendern", "kern": NEU}
        self.assertIsNotNone(sicherheit.steuerdatei_grund(act))
        self.assertEqual([("yes", "Ja, ausführen"), ("no", "Nein, abbrechen")], sicherheit.optionen(act))  # kein „Immer“
        for modus in ("normal", "auto", "autoan", "chat"):
            with mock.patch.object(modes, "current", lambda m=modus: m):
                self.assertEqual("ask", modes.decide(act, True), modus)

    def test_hintertuer_ueber_dateien(self):
        import sicherheit
        ziel = str(charakter.kern_datei("PersonA"))
        for act in ({"tool": "datei_schreiben", "pfad": ziel, "inhalt": "{}"},
                    {"tool": "datei_bearbeiten", "pfad": ziel, "alt": "red", "neu": "blue"}):
            self.assertIn("Kern-Datei", sicherheit.steuerdatei_grund(act) or "")

    def test_ki_kann_das_ja_nicht_selbst_setzen(self):
        import actions
        acts, _rest = actions.parse_actions('```aktion\n{"tool": "charakter_aendern", "kern": "x", '
                                            '"_kern_ja": true}\n```')
        self.assertNotIn("_kern_ja", acts[0])

    def test_kern_ist_ein_befehl_nur_fuer_den_nutzer(self):
        import commands
        self.assertIn("/kern", commands.MENUE_GESPERRT)


class Zwang(_Daten):
    def setUp(self):
        super().setUp()
        import actions
        import imagegen
        self.A = actions
        p = mock.patch.object(imagegen, "lora_moeglich", lambda vorlage=None: True)
        p.start()
        self.addCleanup(p.stop)

    def test_kern_vorne_frisur_und_kleidung_frei(self):
        prompt, *_ = self.A._figur({"figur": True, "frisur": "dutt", "outfit": "abend"}, "dancing at a ball")
        self.assertTrue(prompt.startswith("An adult woman with long red hair and green eyes."))
        self.assertIn("Hair: a high bun.", prompt)
        self.assertIn("Wearing an evening gown.", prompt)
        frei, *_ = self.A._figur({"frisur": "two long braids"}, "in a park")    # Frisur allein = Figur
        self.assertIn("Hair: two long braids.", frei)
        self.assertTrue(frei.startswith("An adult woman"))

    def test_standard_outfit_nur_ohne_kleidung(self):
        ohne, *_ = self.A._figur({"figur": True}, "reading in a cafe")
        self.assertIn("Wearing a white shirt and jeans.", ohne)
        mit, *_ = self.A._figur({"figur": True}, "reading in a cafe, wearing a red dress")
        self.assertNotIn("white shirt", mit)

    def test_mit_lora_kern_dann_name(self):
        import lorabild
        f = lorabild.datei("figur_a")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"gewichte")
        lorabild.binden("figur_a", "PersonA")
        prompt, _s, lora, _h = self.A._figur({"figur": True}, "at the beach")
        self.assertTrue(prompt.startswith("An adult woman with long red hair"))
        self.assertIn("A photo of figur_a, an adult.", prompt)
        self.assertEqual(str(f), lora)

    def test_kugel_malt_mit_gesperrtem_kern(self):
        import kugelbilder
        self.assertEqual("eine eigene Beschreibung", kugelbilder._basis("PersonA", "eine eigene Beschreibung"))
        charakter.kern_sperren(True, "PersonA")
        basis = kugelbilder._basis("PersonA", "eine eigene Beschreibung")
        self.assertIn("long red hair", basis)
        gemalt = []
        with mock.patch.object(kugelbilder, "_maler", lambda: lambda prompt, **kw: gemalt.append(prompt) or "x.png"), \
                mock.patch.object(kugelbilder, "freistellen", lambda quelle, ziel: (ziel.write_bytes(b"x"), ziel)[1]), \
                mock.patch.object(kugelbilder, "motiv_ordner", lambda name: self.daten / "kugel"):
            (self.daten / "kugel").mkdir()
            kugelbilder.malen("PersonA", "ganz anders", stimmungen=[("", "smiling")])
        self.assertIn("long red hair", gemalt[0])
        self.assertNotIn("ganz anders", gemalt[0])


class BildBefehl(unittest.TestCase):
    """/bild --figur malt mit Kern, Seed und LoRA; /bild ohne --figur bleibt, wie es dasteht."""

    def test_figur_schalter(self):
        import imagegen
        o = imagegen.parse_opts("liest ein Buch --figur --seed 3")
        self.assertEqual((True, "liest ein Buch", 3), (o.get("figur"), o["prompt"], o["seed"]))
        self.assertNotIn("figur", imagegen.parse_opts("eine Katze"))

    def test_befehl_nimmt_den_figur_weg(self):
        code = (ROOT / "main_start.py").read_text(encoding="utf-8")
        teil = code[code.index('elif cmd == "/bild":'):code.index('elif cmd == "/video":')]
        self.assertIn('if opts.get("figur"):', teil)
        self.assertIn('actions._figur({"figur": True}, opts["prompt"])', teil)
        self.assertIn("figur=bool(opts.get(\"figur\")), lora=lora", teil)


if __name__ == "__main__":
    unittest.main()
