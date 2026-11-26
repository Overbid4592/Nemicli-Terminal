"""Lern-LoRA: Beispiele aus Gesprächen, Themen, Training je Thema und die Wahl vor jeder Antwort –
ohne Grafikkarte (das Training selbst ist ersetzt)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for d in ("", "core", "tools", "engines", "ui"):
    p = str(ROOT / d) if d else str(ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

from loramotor import adapter as A      # noqa: E402
from loramotor import lernlora as LL    # noqa: E402
from loramotor import training as T     # noqa: E402

W = "Ergebnis von 'web_suche': irgendwas"


def u(t):
    return {"role": "user", "content": t}


def k(t):
    return {"role": "assistant", "content": t}


class Runden(unittest.TestCase):
    def test_nur_direkte_antworten_ohne_korrektur(self):
        msgs = [u("Wie heißt die Hauptstadt von Frankreich?"), k("<think>kurz</think>Paris ist die Hauptstadt."),
                u("Such mir das Wetter"), k("```aktion web_suche```"), u(W), k("Morgen wird es sonnig und warm."),
                u("Erzähl mir einen Witz über Katzen"), k("Warum mögen Katzen Computer? Wegen der Maus!"),
                u("Nein, das war falsch und doof."), k("Entschuldige, ich versuche es nochmal anders."),
                u("ok"), k("kurz")]
        self.assertEqual(LL.runden(msgs), [("Wie heißt die Hauptstadt von Frankreich?", "Paris ist die Hauptstadt."),
                                           ("Nein, das war falsch und doof.",
                                            "Entschuldige, ich versuche es nochmal anders.")])

    def test_lange_frage_ist_anhang(self):
        self.assertEqual(LL.runden([u("x" * (LL.FRAGE_MAX + 1)), k("Eine ausreichend lange Antwort hier.")]), [])


class Beispiele(unittest.TestCase):
    def test_nur_eigene_gespraeche_mit_neuheit_als_gewicht(self):
        chats = [{"id": 1, "profil": "Eigen"}, {"id": 2, "profil": ""}, {"id": 3, "profil": "Andere"}]
        daten = {1: {"messages": [u("Was ist ein LoRA genau?"), k("Ein kleiner Zusatz zu den Gewichten.")]},
                 2: {"messages": [u("Gemeinsam von früher?"), k("Das sollte nicht dabei sein.")]},
                 3: {"messages": [u("Fremde Persönlichkeit?"), k("Das gehört nicht hierher.")]}}
        import chatstore
        import profilordner
        with mock.patch.object(chatstore, "list_chats", return_value=chats), \
                mock.patch.object(chatstore, "load", side_effect=lambda n: daten[n]), \
                mock.patch.object(chatstore, "index_ref", side_effect=lambda n: f"chat:Eigen:{n}"), \
                mock.patch.object(profilordner, "ordner", return_value=Path("Profile/Eigen/Chats")), \
                mock.patch.object(LL, "_persona", return_value="Test"), \
                mock.patch.object(LL, "_neuheit_je_chat", return_value={"chat:Eigen:1": 0.5}):
            bsp = LL.beispiele()
        self.assertEqual(len(bsp), 1)
        self.assertEqual(bsp[0]["frage"], "Was ist ein LoRA genau?")
        self.assertEqual(bsp[0]["gewicht"], 1.5)
        self.assertIn("Test", bsp[0]["system"])


def _bsp(n, thema):
    return [{"system": "s", "frage": f"{thema} Frage {i} zu {thema}", "antwort": "a" * 30, "gewicht": 1.0}
            for i in range(n)]


class Themen(unittest.TestCase):
    def test_zwei_gruppen_nach_bedeutung(self):
        bsp = _bsp(30, "Python") + _bsp(30, "Garten")
        vek = [[1.0, 0.0] if "Python" in b["frage"] else [0.0, 1.0] for b in bsp]
        import memory
        with mock.patch.object(memory, "_embed_texts", return_value=vek):
            themen = LL.themen_bilden(bsp)
        self.assertEqual(len(themen), 2)
        self.assertEqual(sorted(len(t["beispiele"]) for t in themen), [30, 30])
        namen = " ".join(t["name"] for t in themen)
        self.assertIn("python", namen)
        self.assertIn("garten", namen)

    def test_wenig_oder_ohne_encoder_ein_thema(self):
        self.assertEqual(len(LL.themen_bilden(_bsp(30, "Python"))), 1)
        import memory
        with mock.patch.object(memory, "_embed_texts", return_value=None):
            t = LL.themen_bilden(_bsp(60, "Python"))
        self.assertEqual((len(t), t[0]["mitte"]), (1, None))


class _Ablage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ziel = Path(tmp.name)
        self.cfg = {LL.SCHALTER: True}
        for p in (mock.patch.object(LL, "ordner", return_value=self.ziel),
                  mock.patch.object(LL, "_stand_datei", return_value=self.ziel / "stand.json"),
                  mock.patch.object(LL, "_config", side_effect=lambda: self.cfg),
                  mock.patch.object(LL, "_persona", return_value="Test"),
                  mock.patch.object(A, "basis_kennung", return_value="m.gguf|1")):
            p.start()
            self.addCleanup(p.stop)
        LL._gewaehlt.clear()

    def themen(self, mitten=True):
        themen = [{"datei": "thema_1.safetensors", "name": "code", "stichworte": ["python", "fehler"],
                   "mitte": [1.0, 0.0] if mitten else None, "beispiele": 40},
                  {"datei": "thema_2.safetensors", "name": "garten", "stichworte": ["garten", "blumen"],
                   "mitte": [0.0, 1.0] if mitten else None, "beispiele": 20}]
        for t in themen:
            (self.ziel / t["datei"]).write_bytes(b"x")
        (self.ziel / "themen.json").write_text(json.dumps({"basis": "m.gguf|1", "embed": "e1", "themen": themen}),
                                               encoding="utf-8")


class Wahl(_Ablage):
    def test_nach_bedeutung_mit_beharrung(self):
        self.themen()
        vektor = {"v": [0.0, 1.0]}
        with mock.patch.object(LL, "_frage_vektor", side_effect=lambda f, kennung: vektor["v"]):
            self.assertEqual(LL.wahl("m.gguf", "Blumen")[0].name, "thema_2.safetensors")
            vektor["v"] = [0.71, 0.70]                     # knapp anders: bleibt beim Thema
            self.assertEqual(LL.wahl("m.gguf", "?")[0].name, "thema_2.safetensors")
            vektor["v"] = [1.0, 0.1]                       # deutlich anders: wechselt
            self.assertEqual(LL.wahl("m.gguf", "Python")[0].name, "thema_1.safetensors")

    def test_ohne_encoder_nach_stichworten_sonst_das_groesste(self):
        self.themen()
        with mock.patch.object(LL, "_frage_vektor", return_value=None):
            self.assertEqual(LL.wahl("m.gguf", "Wie geht es dir?")[0].name, "thema_1.safetensors")
            self.assertEqual(LL.wahl("m.gguf", "Meine Blumen im Garten welken")[0].name, "thema_2.safetensors")

    def test_aus_oder_ohne_staerke_nichts(self):
        self.themen()
        self.cfg[LL.SCHALTER] = False
        self.assertIsNone(LL.wahl("m.gguf", "x"))
        self.cfg.update({LL.SCHALTER: True, LL.STAERKE: 0})
        self.assertIsNone(LL.wahl("m.gguf", "x"))

    def test_anderer_stand_der_gguf_zaehlt_nicht(self):
        self.themen()
        with mock.patch.object(A, "basis_kennung", return_value="m.gguf|2"):
            self.assertIsNone(LL.wahl("m.gguf", "x"))

    def test_fremde_dateinamen_zaehlen_nicht(self):
        self.themen()
        d = json.loads((self.ziel / "themen.json").read_text(encoding="utf-8"))
        for t in d["themen"]:
            t["datei"] = "../" + t["datei"]
        (self.ziel / "themen.json").write_text(json.dumps(d), encoding="utf-8")
        self.assertIsNone(LL.wahl("m.gguf", "x"))

    def test_coding_lora_hat_vorrang(self):
        self.themen()
        import coding
        with mock.patch.object(coding, "aktiv", return_value=True), \
                mock.patch.object(A, "_config", return_value={"coding_lora": True}), \
                mock.patch.object(A, "datei_fuer", return_value=self.ziel / "fehlt.safetensors"):
            self.assertIsNone(A.gewuenscht("m.gguf", "Python"))      # Coding an, aber kein Coding-LoRA: nichts
        with mock.patch.object(coding, "aktiv", return_value=False), \
                mock.patch.object(LL, "_frage_vektor", return_value=None):
            self.assertEqual(A.gewuenscht("m.gguf", "Garten")[0].name, "thema_2.safetensors")


class Trainieren(_Ablage):
    def test_je_thema_ein_lora_und_aufraeumen(self):
        (self.ziel / "thema_9.safetensors").write_bytes(b"alt")
        (self.ziel / "thema_1_stand.safetensors").write_bytes(b"zwischen")
        gerufen = []

        def falsch_trainieren(**kw):
            gerufen.append((kw["ziel"].name, len(kw["beispiele"]), kw["schritte"]))
            kw["ziel"].write_bytes(b"lora")
            return kw["ziel"]
        bsp = _bsp(30, "Python") + _bsp(30, "Garten")
        import memory
        with mock.patch.object(LL, "modell_pruefen"), mock.patch.object(LL, "beispiele", return_value=bsp), \
                mock.patch.object(T, "trainieren", side_effect=falsch_trainieren), \
                mock.patch.object(memory, "_embed_texts",
                                  return_value=[[1.0, 0.0]] * 30 + [[0.0, 1.0]] * 30), \
                mock.patch.object(memory, "modell_kennung", return_value="e1"):
            erg = LL.trainieren(self.ziel / "m.gguf")
        self.assertEqual(erg["beispiele"], 60)
        # trainiert wird neben die alten Themen, an ihren Platz kommen sie erst zusammen mit themen.json
        self.assertEqual(sorted(g[0] for g in gerufen), ["thema_1.neu.safetensors", "thema_2.neu.safetensors"])
        self.assertEqual(sorted(p.name for p in self.ziel.glob("thema_?.safetensors")),
                         ["thema_1.safetensors", "thema_2.safetensors"])
        self.assertEqual(list(self.ziel.glob("*.neu.safetensors")), [])
        self.assertTrue(all(g[2] == 90 for g in gerufen))           # 3 Schritte je Beispiel
        self.assertFalse((self.ziel / "thema_9.safetensors").exists())
        self.assertTrue((self.ziel / "thema_1_stand.safetensors").exists())   # Zwischenstand bleibt
        d = json.loads((self.ziel / "themen.json").read_text(encoding="utf-8"))
        self.assertEqual((d["basis"], d["embed"], len(d["themen"])), ("m.gguf|1", "e1", 2))
        self.assertEqual(LL.stand()["beispiele"], 60)

    def test_zu_wenig_beispiele(self):
        with mock.patch.object(LL, "modell_pruefen"), mock.patch.object(LL, "beispiele", return_value=_bsp(5, "x")):
            with self.assertRaises(ValueError):
                LL.trainieren(self.ziel / "m.gguf")

    def test_nacht_einmal(self):
        import time
        jetzt = time.strptime("2026-11-22 03:30", "%Y-%m-%d %H:%M")
        self.assertTrue(LL.nacht_faellig(jetzt))
        LL._stand_merken(versucht="2026-11-22")
        self.assertFalse(LL.nacht_faellig(jetzt))
        self.assertFalse(LL.nacht_faellig(time.strptime("2026-11-23 12:00", "%Y-%m-%d %H:%M")))
        self.cfg[LL.NACHT] = 23
        self.assertTrue(LL.im_nachtfenster(1, 23))
        self.assertFalse(LL.im_nachtfenster(3, 23))


class Gewicht(unittest.TestCase):
    def test_eigenes_gewicht_beim_kodieren(self):
        tok = NS(special={}, eos_id=0, encode=lambda s: [1] * min(5, len(s)))
        fmt = NS(encode=lambda msgs, add_generation_prompt: [2, 2, 2])
        daten = T.kodieren([{"frage": "f", "antwort": "antwort", "gewicht": 1.7},
                            {"frage": "f", "antwort": "antwort"}], tok, fmt)
        self.assertEqual([d[2] for d in daten], [1.7, 1])


class LetzteFrage(unittest.TestCase):
    def test_werkzeug_ergebnis_zaehlt_nicht(self):
        import gguflokal
        msgs = [u("Wie wächst Basilikum am besten?"), k("```aktion web_suche```"), u(W)]
        self.assertEqual(gguflokal._letzte_frage(msgs), "Wie wächst Basilikum am besten?")
        self.assertEqual(gguflokal._letzte_frage([NS(role="user", content="Hallo")]), "Hallo")
        self.assertEqual(gguflokal._letzte_frage([]), "")


if __name__ == "__main__":
    unittest.main()
