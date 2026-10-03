"""Gedächtnis-Modell: austauschbar, nur aus einem Ordner, nie aus dem Netz,
kein stilles Scheitern – und Vektoren eines anderen Modells zählen nicht."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import embedder   # noqa: E402
import indexdb    # noqa: E402
import memory     # noqa: E402


def _modell(ordner: Path, model_type="qwen3", hidden=1024, pooling: dict | None = None,
            prompts: dict | None = None, tokenizer="tokenizer.json") -> Path:
    ordner.mkdir(parents=True)
    (ordner / "config.json").write_text(json.dumps(
        {"model_type": model_type, "hidden_size": hidden, "num_hidden_layers": 28,
         "vocab_size": 151669, "max_position_embeddings": 512}))
    (ordner / "model.safetensors").write_text("x")
    (ordner / tokenizer).write_text("x")
    if pooling:
        (ordner / "1_Pooling").mkdir()
        (ordner / "1_Pooling" / "config.json").write_text(json.dumps(pooling))
    if prompts:
        (ordner / "config_sentence_transformers.json").write_text(json.dumps({"prompts": prompts}))
    return ordner


class _Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.emb = Path(self.tmp.name) / "Models" / "embeddings"
        self.config: dict = {}
        import config
        for ziel, name, wert in ((memory, "EMBED_ORDNER", self.emb), (memory, "_encoder", None),
                                 (memory, "_fehler", ""), (memory, "_fehlschlag_zeit", 0.0),
                                 (config, "load", lambda: self.config)):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)


class Modellwahl(_Basis):
    def test_kein_modell(self):
        self.assertIsNone(memory.modell_ordner())
        self.assertEqual(memory.modell_kennung(), "")
        self.assertIn("Kein Embedding-Modell", memory.encoder_status())

    def test_beliebiger_name_und_tokenizer(self):
        ziel = _modell(self.emb / "MeinModell", model_type="bert", hidden=384, tokenizer="vocab.txt")
        self.assertEqual(memory.modell_ordner(), ziel)
        self.assertEqual(memory.modell_name(), "MeinModell")

    def test_ohne_safetensors_zaehlt_nicht(self):
        ziel = _modell(self.emb / "Alt")
        (ziel / "model.safetensors").unlink()
        (ziel / "pytorch_model.bin").write_text("x")          # pickle – wird nie geladen
        self.assertIsNone(memory.modell_ordner())

    def test_config_waehlt_das_modell(self):
        _modell(self.emb / "A_Modell")
        b = _modell(self.emb / "B_Modell", model_type="bert", hidden=768)
        self.assertEqual(memory.modell_name(), "A_Modell")         # ohne Config: das erste
        self.config["embedding_modell"] = "B_Modell"
        self.assertEqual(memory.modell_ordner(), b)
        self.config["embedding_modell"] = "Gibtsnicht"
        self.assertIsNone(memory.modell_ordner())                 # gewählt, aber fehlt: kein Ersatz
        self.assertIn("Gibtsnicht", memory.encoder_status())

    def test_alte_ablage(self):
        alt = _modell(self.emb / "models--Qwen--Qwen3-Embedding-0.6B" / "snapshots" / "abc")
        self.assertEqual(memory.modell_ordner(), alt)


class Laden(_Basis):
    def test_ohne_modell_kein_download(self):
        with patch.object(embedder, "Embedder") as emb:
            self.assertIsNone(memory._get_encoder())
            emb.assert_not_called()
        self.assertFalse(memory.embedding_ready())

    def test_laedt_nur_lokal(self):
        ziel = _modell(self.emb / "Qwen3Embedding")
        aufrufe = []

        class Falsch:
            def __init__(self, ordner, **kw):
                aufrufe.append((ordner, kw))
                self.device = kw.get("device", "cpu")
        with patch.object(embedder, "Embedder", Falsch), patch.object(memory, "_geraet_waehlen", lambda *_: "cpu"), \
                patch.object(memory, "_start_idle_watch", lambda: None):
            self.assertIsNotNone(memory._get_encoder())
        self.assertEqual(aufrufe, [(str(ziel), {"device": "cpu", "local_files_only": True})])

    def test_fehler_sichtbar_und_neuer_versuch(self):
        _modell(self.emb / "Qwen3Embedding")

        def kaputt(*a, **k):
            raise OSError("Datei beschädigt")
        with patch.object(embedder, "Embedder", kaputt), patch.object(memory, "_geraet_waehlen", lambda *_: "cpu"):
            self.assertIsNone(memory._get_encoder())
        self.assertIn("Datei beschädigt", memory.encoder_status())
        with patch.object(memory, "_fehlschlag_zeit", 0.0), patch.object(embedder, "Embedder") as emb, \
                patch.object(memory, "_geraet_waehlen", lambda *_: "cpu"), patch.object(memory, "_start_idle_watch", lambda: None):
            emb.return_value.device = "cpu"
            self.assertIsNotNone(memory._get_encoder())


class Rechenweise(unittest.TestCase):
    def test_aus_den_modelldateien(self):
        with tempfile.TemporaryDirectory() as t:
            q = _modell(Path(t) / "q")                                   # Decoder ohne Angaben
            self.assertEqual(embedder.einstellungen_lesen(q)["pooling"], "last")
            b = _modell(Path(t) / "b", model_type="bert")                # Encoder ohne Angaben
            self.assertEqual(embedder.einstellungen_lesen(b)["pooling"], "mean")
            c = _modell(Path(t) / "c", model_type="bert", pooling={"pooling_mode_cls_token": True},
                        prompts={"query": "query: ", "passage": "passage: "})
            e = embedder.einstellungen_lesen(c)
            self.assertEqual((e["pooling"], e["query_prompt"], e["dokument_prompt"], e["max_tokens"]),
                             ("cls", "query: ", "passage: ", 512))
            (c / "embedding.json").write_text(json.dumps({"pooling": "mean", "query_prompt": "Frage: "}))
            e = embedder.einstellungen_lesen(c)
            self.assertEqual((e["pooling"], e["query_prompt"]), ("mean", "Frage: "))

    def test_kennung(self):
        with tempfile.TemporaryDirectory() as t:
            q = _modell(Path(t) / "q")
            self.assertEqual(embedder.kennung(q), embedder.ALT_KENNUNG)   # alte Vektoren bleiben gültig
            b = _modell(Path(t) / "b", model_type="bert", hidden=384)
            self.assertNotEqual(embedder.kennung(b), embedder.ALT_KENNUNG)


class Modellwechsel(unittest.TestCase):
    def test_notizen_vektoren(self):
        with patch.object(memory, "modell_kennung", lambda: "neu-384"):
            self.assertFalse(memory.vektor_gueltig({"vec": [1.0]}))                  # alt = Qwen
            self.assertTrue(memory.vektor_gueltig({"vec": [1.0], "vec_modell": "neu-384"}))
        with patch.object(memory, "modell_kennung", lambda: embedder.ALT_KENNUNG):
            self.assertTrue(memory.vektor_gueltig({"vec": [1.0] * 1024}))
            self.assertFalse(memory.vektor_gueltig({"vec": [1.0] * 768}))    # noch älteres Modell
        with patch.object(memory, "modell_kennung", lambda: ""):
            self.assertFalse(memory.vektor_gueltig({"vec": [1.0]}))

    def test_datenbank_rechnet_nach(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            kennung = {"wert": "modell-a"}
            dim = {"wert": 3}
            for ziel, name, wert in (
                    (indexdb, "_ROOT", root), (indexdb, "_DB_NEW", root / "learned" / "memory.db"),
                    (indexdb, "_DB_OLD", root / "learned" / "index.sqlite"),
                    (memory, "modell_kennung", lambda: kennung["wert"]),
                    (memory, "_embed_texts", lambda texts, **k: [[1.0] * dim["wert"] for _ in texts]),
                    (memory, "encoder_loaded", lambda: True), (memory, "warm_encoder", lambda: None)):
                p = patch.object(ziel, name, wert)
                p.start()
                self.addCleanup(p.stop)
            indexdb._invalidate()
            self.addCleanup(indexdb._invalidate)
            indexdb._upsert("note", "note:1", ["(fakt) Ein Satz über Tee und Kekse."])
            self.assertEqual(indexdb.offen()["ohne_vektor"], 0)
            self.assertIn("sinn", indexdb.suchen("Tee", laden=True)[0]["wie"])
            kennung["wert"], dim["wert"] = "modell-b", 5                 # Modell gewechselt
            indexdb._invalidate()
            self.assertEqual(indexdb.offen()["ohne_vektor"], 1)           # alter Vektor zählt nicht
            self.assertEqual(indexdb.suchen("Tee", laden=True)[0]["wie"], "wort")
            self.assertEqual(indexdb.nachvektorisieren(), 1)
            self.assertEqual(indexdb.offen()["ohne_vektor"], 0)
            self.assertIn("sinn", indexdb.suchen("Tee", laden=True)[0]["wie"])


class AutoGeraet(unittest.TestCase):
    """auto: kleine Aufträge im RAM, große für ihre Dauer auf der GPU."""

    def setUp(self):
        import threading
        self.wege = []

        class Falsch:
            device = "cpu"
            _lock = threading.RLock()

            def to(s, geraet):
                self.wege.append(geraet)
                s.device = geraet
                return s
        self.enc = Falsch()
        self.config = {"embedding_geraet": "auto"}
        self.frei = True
        import config
        for ziel, name, wert in ((memory, "_get_encoder", lambda: self.enc),
                                 (memory, "_gpu_frei_genug", lambda: self.frei),
                                 (config, "load", lambda: self.config)):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_grosser_auftrag_auf_die_gpu_und_zurueck(self):
        with memory.auftrag(200) as auf_gpu:
            self.assertTrue(auf_gpu)
            self.assertEqual(self.enc.device, "cuda")
        self.assertEqual(self.enc.device, "cpu")
        self.assertEqual(self.wege, ["cuda", "cpu"])

    def test_kleiner_auftrag_bleibt_im_ram(self):
        with memory.auftrag(5) as auf_gpu:
            self.assertFalse(auf_gpu)
        self.assertEqual(self.wege, [])

    def test_schwelle_aus_der_config(self):
        self.config["embedding_gpu_ab"] = 3
        with memory.auftrag(5) as auf_gpu:
            self.assertTrue(auf_gpu)

    def test_kein_wechsel_ohne_freien_vram_oder_mit_cpu(self):
        self.frei = False
        with memory.auftrag(500) as auf_gpu:
            self.assertFalse(auf_gpu)
        self.frei = True
        self.config["embedding_geraet"] = "cpu"
        with memory.auftrag(500) as auf_gpu:
            self.assertFalse(auf_gpu)
        self.assertEqual(self.wege, [])

    def test_zurueck_in_den_ram_auch_bei_fehler(self):
        with self.assertRaises(RuntimeError):
            with memory.auftrag(200):
                raise RuntimeError("mitten im Auftrag")
        self.assertEqual(self.enc.device, "cpu")


class AlteDatenbank(unittest.TestCase):
    def test_nachruesten_nach_vektorlaenge(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for ziel, name, wert in ((indexdb, "_ROOT", root), (indexdb, "_DB_NEW", root / "learned" / "memory.db"),
                                     (indexdb, "_DB_OLD", root / "learned" / "index.sqlite")):
                p = patch.object(ziel, name, wert)
                p.start()
                self.addCleanup(p.stop)
            (root / "learned").mkdir()
            con = sqlite3.connect(str(root / "learned" / "memory.db"))
            con.execute("CREATE TABLE chunks (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, "
                        "text TEXT NOT NULL, vec TEXT, updated TEXT)")
            con.executemany("INSERT INTO chunks VALUES (?, 'note', 'note:1', 'text', ?, '')",
                            [("q", json.dumps([0.1] * 1024)), ("alt", json.dumps([0.1] * 768)), ("leer", None)])
            con.commit()
            con.close()
            indexdb._connect().close()
            con = sqlite3.connect(str(root / "learned" / "memory.db"))
            tags = dict(con.execute("SELECT hash, vec_modell FROM chunks").fetchall())
            con.close()
            self.assertEqual(tags, {"q": embedder.ALT_KENNUNG, "alt": "unbekannt-768", "leer": ""})


class Platzhalter(unittest.TestCase):
    def test_in_frischem_interpreter(self):
        """Ohne sklearn in einem frischen Prozess: der Platzhalter darf nicht an
        importlib.machinery scheitern (lief nur, wenn ein anderes Paket es vorher lud)."""
        code = ("import sys; sys.modules['sklearn'] = None; "
                f"sys.path.insert(0, r'{ROOT / 'tools'}'); import embedder; "
                "print(embedder._sklearn_blockade_umgehen())")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "True")


if __name__ == "__main__":
    unittest.main()
