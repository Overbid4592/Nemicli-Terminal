"""Gedächtnis-Pflege: Kandidaten, Selbsteinschätzung der Cloud-KI mit Ersatzbank, Prüfung der Antwort,
Archiv (zusammenlegen, zurückholen) – nichts geht endgültig verloren."""
import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions               # noqa: E402
import gedaechtnispflege as GP  # noqa: E402
import indexdb               # noqa: E402
import memory                # noqa: E402

JETZT = datetime(2026, 11, 4, 12, 0)
ALT = (JETZT - timedelta(days=90)).strftime("%Y-%m-%d %H:%M")
NEU = (JETZT - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")


class _Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name)
        self.dropped = []
        for p in (mock.patch.object(memory, "_FILE", self.ordner / "memory.json"),
                  mock.patch.object(memory, "_profil_datei", return_value=None),
                  mock.patch.object(indexdb, "drop_ref", side_effect=self.dropped.append),
                  mock.patch("snapshot.sichern", side_effect=lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        GP._wunsch["grund"] = None

    def notiz(self, text, kind="fakt", ts=NEU, **mehr):
        e = memory.remember(text, kind)
        data = memory._load(memory._FILE)
        for x in data["entries"]:
            if x["id"] == e["id"]:
                x.update(ts=ts, **mehr)
        memory._save(data, memory._FILE)
        return e["id"]

    def texte(self):
        return sorted(e["text"] for e in memory.all_entries())


class Archiv(_Basis):
    def test_zusammenlegen_archiviert_beide_und_behaelt_details(self):
        a = self.notiz("Kaffee nach 15 Uhr meiden")
        b = self.notiz("Ab 15 Uhr keinen Kaffee", detail="kaffee")
        e = memory.zusammenlegen(a, [b], "Nach 15 Uhr keinen Kaffee mehr.", "Pflege: zusammengelegt")
        self.assertEqual("kaffee", e["detail"])
        self.assertEqual(["Nach 15 Uhr keinen Kaffee mehr."], self.texte())
        archiv = memory.archiv_liste()
        self.assertEqual({a, b}, {x["id"] for x in archiv})
        self.assertTrue(all("Pflege" in x["grund"] for x in archiv))
        self.assertEqual({f"note:{a}", f"note:{b}"}, set(self.dropped))

    def test_zusammenlegen_verdaechtig_aendert_nichts(self):
        a, b = self.notiz("eins eins"), self.notiz("zwei zwei")
        self.assertIsNone(memory.zusammenlegen(a, [b], "Text​mit unsichtbarem Zeichen"))
        self.assertEqual(["eins eins", "zwei zwei"], self.texte())
        self.assertEqual([], memory.archiv_liste())

    def test_zurueckholen(self):
        a = self.notiz("Kaffee nach 15 Uhr meiden")
        b = self.notiz("Ab 15 Uhr keinen Kaffee")
        memory.zusammenlegen(a, [b], "Nach 15 Uhr keinen Kaffee mehr.")
        self.assertEqual("Ab 15 Uhr keinen Kaffee", memory.zurueckholen(b)["text"])
        self.assertEqual("Kaffee nach 15 Uhr meiden", memory.zurueckholen(a)["text"])     # alte Fassung gilt
        self.assertEqual(["Ab 15 Uhr keinen Kaffee", "Kaffee nach 15 Uhr meiden"], self.texte())
        self.assertIn("Nach 15 Uhr keinen Kaffee mehr.", [x["text"] for x in memory.archiv_liste()])
        self.assertIsNone(memory.zurueckholen(999))

    def test_vergessen_und_zurueck(self):
        a = self.notiz("Umzug am Monatsende", "projekt")
        self.assertTrue(memory.vergessen(a, "Pflege: veraltet"))
        self.assertEqual([], self.texte())
        memory.zurueckholen(a)
        self.assertEqual(["Umzug am Monatsende"], self.texte())

    def test_verdichten_ohne_ki_legt_ins_archiv(self):
        self.notiz("Der Hund heißt Bruno")
        self.notiz("Der Hund heißt Bruno und ist sieben")
        erg = memory._verdichten(memory._FILE, 0.9)
        self.assertEqual(1, erg["merged"])
        self.assertEqual(["Der Hund heißt Bruno"], [x["text"] for x in memory.archiv_liste()
                                                   if x["grund"].startswith("verschmolzen in")])


class Kandidaten(_Basis):
    def test_paare_und_alte(self):
        a = self.notiz("Nach 15 Uhr kein Kaffee")
        b = self.notiz("Ab 15 Uhr keinen Kaffee")
        c = self.notiz("Das Fahrrad hat 21 Gänge")
        d = self.notiz("Umzug am Monatsende", "projekt", ts=ALT)
        self.notiz("Antworten kurz halten", "vorliebe", ts=ALT)                 # Vorlieben veralten nicht
        self.notiz("Alter Fakt, frisch geprüft", ts=ALT, geprueft=NEU)
        vek = {a: [1.0, 0.0], b: [0.95, 0.31], c: [0.0, 1.0]}
        k = GP.kandidaten(vektoren=vek, jetzt=JETZT, schwelle=0.85)
        self.assertEqual([(a, b)], [(x["id"], y["id"]) for _s, x, y in k["paare"]])
        self.assertEqual([d], [e["id"] for e in k["alte"]])

    def test_beide_frisch_geprueft_kein_paar(self):
        a = self.notiz("eins", geprueft=NEU)
        b = self.notiz("eins auch", geprueft=NEU)
        k = GP.kandidaten(vektoren={a: [1.0, 0.0], b: [1.0, 0.0]}, jetzt=JETZT, schwelle=0.85)
        self.assertTrue(GP.leer(k))

    def test_faellig(self):
        k = {"paare": [(0.9, {}, {})] * 3, "alte": []}
        self.assertTrue(GP.faellig(k, JETZT, zuletzt=""))
        self.assertFalse(GP.faellig(k, JETZT, zuletzt=JETZT.strftime("%Y-%m-%d 10:00")))   # Takt noch nicht um
        self.assertFalse(GP.faellig({"paare": [(0.9, {}, {})], "alte": []}, JETZT, zuletzt=""))
        GP.wuenschen("doppelt")
        self.assertTrue(GP.faellig({"paare": [(0.9, {}, {})], "alte": []}, JETZT, zuletzt=JETZT.isoformat()))
        self.assertFalse(GP.faellig({"paare": [], "alte": []}, JETZT, zuletzt=""))

    def test_nachsehen_mit_pause(self):
        with mock.patch.object(GP, "zuletzt", return_value=""), mock.patch.dict(GP._blick, {"bis": 0.0}):
            self.assertTrue(GP.nachsehen())
            GP.spaeter()
            self.assertFalse(GP.nachsehen())
            GP.wuenschen()
            self.assertTrue(GP.nachsehen())                 # ein Wunsch der KI wartet nicht


def _k(*paare, alte=()):
    return {"paare": [(0.9, {"id": a, "kind": "fakt"}, {"id": b, "kind": "fakt"}) for a, b in paare],
            "alte": [{"id": n, "kind": kind} for n, kind in alte]}


class Auswerten(unittest.TestCase):
    def test_zutrauen_nein(self):
        plan = GP.auswerten('{"zutrauen": "nein", "grund": "zu unsicher"}', _k((1, 2)))
        self.assertFalse(plan["zutrauen"])
        self.assertEqual("zu unsicher", plan["grund"])

    def test_nur_kandidaten_und_jede_nummer_einmal(self):
        k = _k((1, 2), (2, 3), alte=[(7, "projekt"), (8, "vorliebe")])
        antwort = ('Hier mein Ergebnis:\n{"zutrauen": "ja", "zusammen": ['
                   '{"behalten": 1, "weg": [2], "text": "Eins und zwei."},'
                   '{"behalten": 3, "weg": [2], "text": "Doppelt benutzt."},'
                   '{"behalten": 1, "weg": [99], "text": "Fremde Nummer."}],'
                   '"veraltet": [{"nr": 7, "grund": "erledigt"}, {"nr": 8}, {"nr": 1}, {"nr": 42}]}')
        plan = GP.auswerten(antwort, k)
        self.assertEqual([{"behalten": 1, "weg": [2], "text": "Eins und zwei."}], plan["zusammen"])
        self.assertEqual([{"nr": 7, "grund": "erledigt"}], plan["veraltet"])

    def test_unbrauchbar(self):
        self.assertIsNone(GP.auswerten("Keine Ahnung.", _k((1, 2))))
        self.assertIsNone(GP.auswerten("{kaputt", _k((1, 2))))


class _KI:
    def __init__(self, antwort):
        self.antwort, self.gefragt = antwort, 0

    async def ask_once(self, prompt, system):
        self.gefragt += 1
        self.prompt = prompt
        return self.antwort


class Pflegen(_Basis):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(indexdb, "_stand", return_value="")
        self.stand = p.start()
        self.addCleanup(p.stop)

    def _lauf(self, aktiv, ref, ersatz, gebaut=None):
        a = self.notiz("Nach 15 Uhr kein Kaffee")
        b = self.notiz("Ab 15 Uhr keinen Kaffee")
        d = self.notiz("Umzug am Monatsende", "projekt", ts=ALT)
        k = GP.kandidaten(vektoren={a: [1.0, 0.0], b: [0.99, 0.1]}, jetzt=datetime.now(), schwelle=0.85)
        gebaute = []

        async def bauen(r):
            gebaute.append(r)
            return gebaut

        erg = asyncio.run(GP.pflegen(aktiv, ref, bauen=bauen, k=k, ersatz=ersatz))
        return erg, (a, b, d), gebaute

    def test_ki_traut_sich_nicht_ersatz_uebernimmt(self):
        zaghaft = _KI('{"zutrauen": "nein", "grund": "Bedeutungen zu fein"}')
        ersatz = _KI('{"zutrauen": "ja", "zusammen": [{"behalten": 1, "weg": [2], '
                     '"text": "Nach 15 Uhr keinen Kaffee mehr."}], "veraltet": [{"nr": 3, "grund": "vorbei"}]}')
        erg, (a, b, d), gebaute = self._lauf(zaghaft, "ollama_cloud:klein", ["openai:gross"], ersatz)
        self.assertEqual("openai:gross", erg["modell"])
        self.assertEqual([("ollama_cloud:klein", "Bedeutungen zu fein")], erg["abgegeben"])
        self.assertEqual(["openai:gross"], gebaute)
        self.assertEqual(["Nach 15 Uhr keinen Kaffee mehr."], self.texte())
        self.assertEqual({a, b, d}, {x["id"] for x in memory.archiv_liste()})
        self.assertIn("Umzug am Monatsende", ersatz.prompt)
        self.stand.assert_called()                                  # Takt gemerkt
        self.assertIn("openai:gross", GP.bericht_text(erg))

    def test_alle_trauen_sich_nicht_nichts_geaendert(self):
        nein = _KI('{"zutrauen": "nein", "grund": "lieber nicht"}')
        erg, _ids, _g = self._lauf(nein, "anthropic:a", ["openai:b"], _KI('{"zutrauen": "nein"}'))
        self.assertIsNone(erg["modell"])
        self.assertEqual(2, len(erg["abgegeben"]))
        self.assertEqual(3, len(self.texte()))
        self.assertIn("kein Cloud-Modell", GP.bericht_text(erg))

    def test_lokales_modell_wird_nie_gefragt(self):
        lokal = _KI('{"zutrauen": "ja"}')
        erg, _ids, gebaute = self._lauf(lokal, "gguf:klein", ["gguf:anderes"], None)
        self.assertEqual(0, lokal.gefragt)
        self.assertEqual([], gebaute)
        self.assertIsNone(erg["modell"])

    def test_falsches_format_bekommt_einen_zweiten_versuch(self):
        class Erst(_KI):
            async def ask_once(self, prompt, system):
                self.gefragt += 1
                self.prompt = prompt
                return "Hier ist mein Plan: zusammenlegen 1 und 2." if self.gefragt == 1 else self.antwort
        ki = Erst('{"zutrauen": "ja", "zusammen": [{"behalten": 1, "weg": [2], "text": "Nach 15 Uhr kein Kaffee."}],'
                  ' "veraltet": []}')
        erg, _ids, gebaute = self._lauf(ki, "anthropic:a", [], None)
        self.assertEqual(2, ki.gefragt)
        self.assertIn("kein gültiges JSON", ki.prompt)
        self.assertEqual("anthropic:a", erg["modell"])
        self.assertEqual([], erg["abgegeben"])
        self.assertIn("Nach 15 Uhr kein Kaffee.", self.texte())

    def test_zweimal_falsch_dann_das_naechste_modell(self):
        kaputt = _KI("leider kein JSON")
        ersatz = _KI('{"zutrauen": "ja", "zusammen": [], "veraltet": []}')
        erg, _ids, _g = self._lauf(kaputt, "anthropic:a", ["openai:b"], ersatz)
        self.assertEqual(2, kaputt.gefragt)
        self.assertEqual([("anthropic:a", "keine gültige Antwort")], erg["abgegeben"])
        self.assertEqual("openai:b", erg["modell"])

    def test_geprueft_markiert(self):
        ki = _KI('{"zutrauen": "ja", "zusammen": [], "veraltet": []}')
        self._lauf(ki, "anthropic:a", [], None)
        self.assertTrue(all(e.get("geprueft") for e in memory.all_entries()))


class Werkzeug(_Basis):
    def test_nur_mit_cloud(self):
        with mock.patch("config.load", return_value={"model": "gguf:klein"}):
            self.assertFalse(actions._gedaechtnis_aufraeumen({}).ok)
        self.assertFalse(GP.gewuenscht())
        with mock.patch("config.load", return_value={"model": "anthropic:a"}):
            self.assertTrue(actions._gedaechtnis_aufraeumen({"grund": "Notizen doppeln sich"}).ok)
        self.assertTrue(GP.gewuenscht())

    def test_registriert(self):
        self.assertIn("gedaechtnis_aufraeumen", actions.ACTIONS)
        self.assertFalse(actions.ACTIONS["gedaechtnis_aufraeumen"]["confirm"])


if __name__ == "__main__":
    unittest.main()
