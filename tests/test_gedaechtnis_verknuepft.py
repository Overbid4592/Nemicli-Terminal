"""Gedächtnis: Verknüpfungen über seltene Begriffe, Neuheit je Brocken und die neuen Merkmale der Rangliste –
im Temp-Ordner, ohne echten Encoder."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui", "tests"):
    if str(ROOT / d) not in sys.path:
        sys.path.insert(0, str(ROOT / d))

import indexdb                         # noqa: E402
import rangliste                       # noqa: E402
from test_agent_rag import _Basis      # noqa: E402


def _sql(befehl: str, werte=()):
    con = sqlite3.connect(indexdb._DB_NEW)
    try:
        r = con.execute(befehl, werte).fetchall()
        con.commit()
        return r
    finally:
        con.close()


class Begriffe(unittest.TestCase):
    def test_namen_kennungen_dateien(self):
        b = indexdb.begriffe("Nutzer: Der Server Zephyr7 braucht KB5034441, siehe start.py und die Datei. "
                             "Version 10.07 und ein Ei.")
        for soll in ("server", "zephyr7", "kb5034441", "start.py", "datei"):
            self.assertIn(soll, b)
        for nicht in ("nutzer", "der", "und", "10.07", "ei"):
            self.assertNotIn(nicht, b)        # Sprecher, Füllwort, reine Zahl, zu kurz


class Verknuepft(_Basis):
    def fuellen(self):
        indexdb._upsert("chat", "chat:1", ["Nutzer: Wir starten das Projekt Orion nächste Woche gemeinsam."])
        indexdb._upsert("chat", "chat:2", ["Nutzer: Für Orion nehmen wir den Server Zephyr7 im Keller."])
        indexdb._upsert("chat", "chat:3", ["Nutzer: Zephyr7 braucht dringend ein neues Netzteil."])
        indexdb._upsert("chat", "chat:4", ["Nutzer: Heute gab es Nudeln mit Tomatensoße zum Abendessen."])

    def test_zweiter_schritt_ueber_seltenen_begriff(self):
        self.fuellen()
        frage = "Welcher Server gehört zum Projekt?"
        ohne = indexdb.suchen(frage, laden=True, verknuepfen=False)
        self.assertNotIn("chat:3", [h["ref"] for h in ohne])
        mit = indexdb.suchen(frage, laden=True)
        refs = [h["ref"] for h in mit]
        self.assertIn("chat:3", refs)                      # nur über „Zephyr7“ aus chat:2 erreichbar
        self.assertEqual(next(h["wie"] for h in mit if h["ref"] == "chat:3"), "verknuepft")
        self.assertNotIn("chat:4", refs)

    def test_nur_in_der_suche_der_persoenlichkeit(self):
        self.fuellen()
        self.assertNotIn("chat:3", [h["ref"] for h in indexdb.suchen("Welcher Server gehört zum Projekt?")])

    def test_filter_gelten_auch_fuer_verknuepfte(self):
        self.fuellen()
        indexdb._upsert("note", "note:9", ["Zephyr7 steht seit Montag im Keller."])
        mit = indexdb.suchen("Welcher Server gehört zum Projekt?", laden=True, arten=("chat",))
        self.assertTrue(all(h["kind"] == "chat" for h in mit))

    def test_zu_haeufiger_begriff_verbindet_nicht(self):
        self.fuellen()
        for i in range(indexdb.VERKNUEPFT_DOC_MIN + 2):
            indexdb._upsert("chat", f"chat:{100 + i}", [f"Nutzer: Zephyr7 Eintrag Nummer {i} ohne Inhalt hier."])
        mit = indexdb.suchen("Welcher Server gehört zum Projekt?", laden=True)
        self.assertNotIn("verknuepft", {h["wie"] for h in mit})


class Neuheit(_Basis):
    def test_wert_aus_den_schwellen(self):
        self.assertEqual(indexdb._neuheit_wert(-1.0, "test-kennung"), 1.0)
        self.assertEqual(indexdb._neuheit_wert(0.99, "test-kennung"), 0.0)
        self.assertTrue(0.0 < indexdb._neuheit_wert(0.6, "test-kennung") < 1.0)

    def test_neu_gegen_aeltere_anderer_quellen(self):
        self.encoder = True
        indexdb._upsert("chat", "chat:1", ["Nutzer: Wache meldet Alarm, Signatur geprüft, Vollscan danach."])
        indexdb._upsert("chat", "chat:2", ["Nutzer: Wieder Alarm der Wache, Signatur ok, Vollscan läuft."])
        indexdb._upsert("chat", "chat:3", ["Nutzer: Mit Krea ein Bild malen, bitte im Wald."])
        self.encoder = False
        indexdb._index(warten=True)
        self.assertEqual(indexdb.neuheit_nachtragen(), 3)
        werte = dict(_sql("SELECT ref, neuheit FROM chunks"))
        self.assertEqual(werte["chat:1"], 1.0)              # das erste Mal
        self.assertLess(werte["chat:2"], 0.2)               # fast dasselbe wie vorher
        self.assertGreater(werte["chat:3"], 0.8)            # anderes Thema
        self.assertEqual(indexdb.neuheit_nachtragen(), 0)   # schon gerechnet

    def test_neues_zaehlt_in_der_suche_mehr(self):
        indexdb._upsert("chat", "chat:1", ["Nutzer: Orion Alpha eins steht hier."])
        indexdb._upsert("chat", "chat:2", ["Nutzer: Orion Alpha zwei steht hier."])
        for neu in ("chat:1", "chat:2"):
            _sql("UPDATE chunks SET neuheit = CASE WHEN ref = ? THEN 1.0 ELSE 0.0 END", (neu,))
            treffer = indexdb.suchen("Orion Alpha")
            self.assertEqual(treffer[0]["ref"], neu)
            self.assertEqual(treffer[0]["neuheit"], 1.0)


class Rangliste(_Basis):
    def test_neue_merkmale_hinten(self):
        alt = rangliste.merkmale({"wie": "wort", "kind": "chat"}, 0, 0)
        neu = rangliste.merkmale({"wie": "verknuepft", "kind": "chat", "neuheit": 0.8}, 0, 0)
        self.assertEqual(len(alt), len(neu))
        self.assertEqual(neu[-2:], [1.0, 0.8])
        self.assertEqual(alt[-2:], [0.0, 0.5])              # ohne Neuheit: neutral

    def test_alte_beispiele_werden_ergaenzt(self):
        breite = len(rangliste.merkmale({}, 0, 0))
        con = rangliste._verbinden()
        try:
            con.execute("INSERT INTO rang_beispiele VALUES ('2026-01-01 00:00', ?, 1)",
                        (json.dumps([1.0] * (breite - 2)),))
            con.execute("INSERT INTO rang_beispiele VALUES ('2026-01-01 00:00', ?, 0)", (json.dumps([1.0] * breite),))
            con.commit()
        finally:
            con.close()
        x, y = rangliste._beispiele()
        self.assertEqual([len(v) for v in x], [breite, breite])
        self.assertEqual(x[0][-2:], [0.0, 0.0])
        self.assertEqual(y, [1, 0])


if __name__ == "__main__":
    unittest.main()
