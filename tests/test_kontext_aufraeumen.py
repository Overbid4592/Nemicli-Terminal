"""Kontext aufräumen statt gleitend kürzen, ausgelagerter Anfang bleibt im Chat und in der
Suche („vorhin in diesem Chat“, Quelle „aktuell“)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import agentrag      # noqa: E402
import chatstore     # noqa: E402
import indexdb       # noqa: E402
import pricing       # noqa: E402


def _verlauf(n, laenge=10):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Nachricht {i} " + "x" * laenge}
            for i in range(n)]


class AufraeumenTests(unittest.TestCase):

    def test_unter_dem_budget_bleibt_alles(self):
        v = _verlauf(30)
        with mock.patch.object(pricing, "context_window", return_value=32768):
            self.assertEqual(pricing.trim_history(v, "x"), v)

    def test_voll_bleiben_die_letzten_drei(self):
        v = _verlauf(41, laenge=400)                        # ~41 x 100 Token > 55 % von 4096
        with mock.patch.object(pricing, "context_window", return_value=4096):
            neu = pricing.trim_history(v, "x")
        self.assertEqual(neu, v[-3:])
        self.assertEqual(neu[0]["role"], "user")

    def test_nie_mit_antwort_beginnen(self):
        v = _verlauf(40, laenge=400)                        # endet mit einer Antwort
        with mock.patch.object(pricing, "context_window", return_value=4096):
            neu = pricing.trim_history(v, "x")
        self.assertEqual(neu, v[-2:])

    def test_hinweis_einmal_und_kontext_gemerkt(self):
        b = NS(model="x", messages=_verlauf(41, laenge=400))
        with mock.patch.object(pricing, "context_window", return_value=4096):
            pricing.kuerzen(b)
        self.assertIn("38 ältere Nachrichten", pricing.aufraeum_hinweis(b))
        self.assertIsNone(pricing.aufraeum_hinweis(b))
        self.assertEqual(chatstore.im_kontext(), b.messages)
        self.assertEqual(len(pricing.weggefallen_holen(b)), 38)


class ZusammenfuehrenTests(unittest.TestCase):

    def test_aufgeraeumter_verlauf_setzt_fort(self):
        alt = _verlauf(10)
        neu = alt[-2:] + _verlauf(12)[10:]                  # aufgeräumt, dann weiter
        self.assertEqual(chatstore.zusammenfuehren(alt, neu), _verlauf(12))

    def test_letzte_runde_zurueckgenommen(self):
        alt = _verlauf(10)
        self.assertEqual(chatstore.zusammenfuehren(alt, alt[6:8]), alt[:8])

    def test_geaenderter_verlauf_gilt(self):
        alt = _verlauf(4)
        neu = alt[:3] + [{"role": "assistant", "content": "anders"}]
        self.assertEqual(chatstore.zusammenfuehren(alt, neu), neu)
        self.assertEqual(chatstore.zusammenfuehren([], neu), neu)

    def test_speichern_behaelt_den_ausgelagerten_anfang(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(chatstore, "CHATS_DIR", Path(tmp)):
            chatstore.save(7, _verlauf(10), ["a"], "m")
            chatstore.save(7, _verlauf(12)[8:], ["a", "b"], "m")
            self.assertEqual(chatstore.load(7)["messages"], _verlauf(12))


def _bild(kb: int) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * (kb * 1024)}}


class BilderGrenzeTests(unittest.TestCase):
    """Bilder zählen pauschal als Tokens, sind aber groß – eigene Grenze in Bytes (HTTP 413)."""

    def _verlauf(self):
        return [{"role": "user", "content": [{"type": "text", "text": f"Foto {i}"}, _bild(400)]}
                for i in range(5)]

    def test_unter_der_grenze_bleibt_alles(self):
        v = self._verlauf()
        self.assertEqual(pricing.bilder_begrenzen(v, max_bytes=10 * 1024 * 1024), 0)
        self.assertTrue(all(m["content"][1]["type"] == "image_url" for m in v))

    def test_aelteste_zuerst_neuestes_bleibt(self):
        v = self._verlauf()
        alt_inhalt = v[0]["content"]
        n = pricing.bilder_begrenzen(v, max_bytes=900 * 1024)        # Platz für zwei Bilder
        self.assertEqual(n, 3)
        self.assertEqual([m["content"][1]["type"] for m in v], ["text"] * 3 + ["image_url"] * 2)
        self.assertEqual(v[0]["content"][1]["text"], pricing.BILD_ERSATZ)
        self.assertEqual(v[0]["content"][0]["text"], "Foto 0")         # Text der Nachricht bleibt
        self.assertEqual(alt_inhalt[1]["type"], "image_url")          # Original nicht verändert

    def test_null_laesst_nur_das_neueste(self):
        v = self._verlauf()
        self.assertEqual(pricing.bilder_begrenzen(v, max_bytes=0), 4)
        self.assertEqual(v[-1]["content"][1]["type"], "image_url")

    def test_kuerzen_meldet_entfernte_bilder(self):
        b = NS(messages=[{"role": "user", "content": [_bild(4000)]},
                         {"role": "assistant", "content": "schön"},
                         {"role": "user", "content": [_bild(4000)]}], model="x")
        with mock.patch.object(pricing, "context_window", return_value=1_000_000):
            pricing.kuerzen(b)
        self.assertEqual(b.messages[0]["content"][0]["text"], pricing.BILD_ERSATZ)
        self.assertIn("Bild", pricing.aufraeum_hinweis(b))
        self.assertIsNone(pricing.aufraeum_hinweis(b))

    def test_zu_gross_erkennt_413(self):
        self.assertTrue(pricing.zu_gross(Exception("Error code: 413 - Request Entity Too Large")))
        self.assertTrue(pricing.zu_gross(Exception("Payload Too Large")))
        self.assertFalse(pricing.zu_gross(Exception("Error code: 500 (ref: 2cb4131)")))


class VorhinImChatTests(unittest.TestCase):

    def test_nur_was_nicht_mehr_im_kontext_steht(self):
        treffer = [{"text": "Chat #7: Titel", "ref": "chat:7"},
                   {"text": "Nutzer: Nachricht 1 über Brücken", "ref": "chat:7"},
                   {"text": "Nemi: Nachricht 9 steht noch da", "ref": "chat:7"}]
        with mock.patch.object(indexdb, "suchen", return_value=treffer) as suche:
            raus = indexdb._vorhin_im_chat("Brücken", "chat:7", ["Nachricht 9 steht noch da und mehr"])
        self.assertEqual([h["text"] for h in raus], ["Nutzer: Nachricht 1 über Brücken"])
        self.assertEqual(suche.call_args.kwargs["ref_praefix"], ("chat:7$",))

    def test_genaue_quelle_statt_anfang(self):
        meta = ("chat", "chat:52", "t", "")
        self.assertTrue(indexdb._passt(meta, None, "", "", ("chat:52$",)))
        self.assertFalse(indexdb._passt(("chat", "chat:520", "t", ""), None, "", "", ("chat:52$",)))
        bedingung, werte = indexdb._filter_sql(None, "", "", ("chat:52$", "bericht:"))
        self.assertIn("c.ref = ?", bedingung)
        self.assertIn("chat:52", werte)
        self.assertIn("bericht:%", werte)

    def test_quelle_aktuell(self):
        with mock.patch.object(chatstore, "aktueller_chat", return_value=52), \
                mock.patch.object(chatstore, "index_ref", return_value="chat:Alpha:52"):
            self.assertEqual(agentrag._quelle("aktuell"), (("chat",), ("chat:Alpha:52$",), []))
        with mock.patch.object(chatstore, "aktueller_chat", return_value=None):
            self.assertEqual(agentrag._quelle("aktuell"), (("chat",), None, []))


if __name__ == "__main__":
    unittest.main()
