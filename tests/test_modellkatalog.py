"""Modell-Katalog für /einrichten modelle: Einträge vollständig und passend zu den Motoren, Laden mit fester
Revision, Ablauf freiwillig. Ohne Netz."""
import ast
import asyncio
import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import hfladen as H                                    # noqa: E402
import main_quelle                                     # noqa: E402
import modellkatalog as K                              # noqa: E402


def _sha_liste(datei: Path) -> dict:
    """SHA256 = {...} einer Motor-Datei, ohne sie zu importieren (lädt torch)."""
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.AnnAssign) and getattr(knoten.target, "id", "") == "SHA256":
            return ast.literal_eval(knoten.value)
    raise AssertionError(f"keine SHA256-Liste in {datei}")


class KatalogTests(unittest.TestCase):

    def test_eintraege_vollstaendig(self):
        ids = [t.id for t in K.KATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        for t in K.KATALOG:
            self.assertTrue(t.lizenz and t.lizenz_link.startswith("https://"), t.id)
            self.assertIsInstance(t.ziel(), Path)
            for d in t.dateien:
                self.assertRegex(d.sha256, r"^[0-9a-f]{64}$", d.name)
                self.assertRegex(d.revision, r"^[0-9a-f]{40}$", d.name)       # angeheftet, nicht „main“
                self.assertGreater(d.groesse, 0)
                self.assertIn("/", d.repo)
                self.assertTrue(K.quelle(d).startswith(f"https://huggingface.co/{d.repo}/blob/{d.revision}/"))

    def test_krea_nur_hinweis(self):
        krea = K.teil("krea")
        self.assertFalse(krea.ladbar)
        self.assertTrue(krea.link.startswith("https://huggingface.co/krea/"))
        self.assertTrue(all(t.ladbar for t in K.KATALOG if t.id != "krea"))

    def test_gleiche_pruefsummen_wie_die_motoren(self):
        for teil, datei in (("musik", "engines/musik/gewichte.py"), ("portraet", "engines/liveportrait/gewichte.py")):
            motor = _sha_liste(ROOT / datei)
            for d in K.teil(teil).dateien:
                self.assertEqual(motor.get(d.name), d.sha256, d.name)

    def test_ziele_wie_die_motoren_suchen(self):
        video = K.teil("video")
        self.assertEqual({"", "vae", "text_encoders", "tokenizer"}, {d.unter for d in video.dateien})
        self.assertEqual("tokenizer.json", next(d.name for d in video.dateien if d.unter == "tokenizer"))
        self.assertEqual(("Models", "reranker"), K.teil("reranker").ziel().parts[-3:-1])
        self.assertEqual(1, len(K.teil("reranker").dateien))                  # Ordner mit genau einer .gguf
        sehen = [d.name for d in K.teil("sehen").dateien]
        self.assertEqual(1, sum("mmproj" in n for n in sehen))
        self.assertEqual("Vision", K.teil("stimme").ziel().parent.name)

    def test_eingerichtet_erkannt(self):
        with tempfile.TemporaryDirectory() as tmp:
            basis = Path(tmp)
            with mock.patch.object(K, "_INSTALL", basis), mock.patch.object(K, "_MUSIK", [basis / "Models" / "Music"]), \
                    mock.patch.object(K, "_PORTRAET", [basis / "Models" / "LivePortrait"]):
                self.assertFalse(K._reranker_da() or K._sehen_da() or K._stimme_da() or K._musik_da())
                for name in ("Models/reranker/eigen/r.gguf", "Vision/mein/m.gguf", "Vision/mein/mmproj-m.gguf",
                             "Vision/tts/qwen-talker-x.gguf", "Vision/tts/qwen-tokenizer-12hz.gguf",
                             "Models/Music/a/acestep-v15-turbo-Q8_0.gguf", "Models/Music/a/Qwen3-Embedding-0.6B-Q8_0.gguf",
                             "Models/Music/a/vae-BF16.gguf"):
                    (basis / name).parent.mkdir(parents=True, exist_ok=True)
                    (basis / name).write_bytes(b"x")
                self.assertTrue(K._reranker_da() and K._sehen_da() and K._stimme_da() and K._musik_da())
                self.assertFalse(K._portraet_da())
        self.assertEqual(len(K.KATALOG), len(K.stand()))


class _Antwort:
    def __init__(self, daten):
        self.daten, self.status_code = daten, 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, n):
        yield self.daten


class RevisionTests(unittest.TestCase):
    def test_feste_revision_in_der_adresse(self):
        inhalt = b"abc" * 100
        urls = []
        netz = NS(stream=lambda m, url, headers=None: urls.append(url) or _Antwort(inhalt))
        with tempfile.TemporaryDirectory() as tmp:
            d = H.Datei("VAE/v.safetensors", len(inhalt), hashlib.sha256(inhalt).hexdigest())
            H.laden("a/b", d, Path(tmp), lambda *a: None, threading.Event(), session=netz, revision="1" * 40)
            H.laden("a/c", d, Path(tmp) / "x", lambda *a: None, threading.Event(), session=netz)
        self.assertEqual(f"https://huggingface.co/a/b/resolve/{'1' * 40}/VAE/v.safetensors", urls[0])
        self.assertIn("/resolve/main/", urls[1])


def _ablauf(wahlen, stand=None, frei=10**13):
    """modelle_einrichten mit gespielter Oberfläche; gibt (geladen, Meldungen) zurück."""
    namen = {"modelle_einrichten", "_modelle_anbieten", "_modell_teil_laden"}
    knoten = [n for n in ast.parse(main_quelle.text()).body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in namen]
    geladen, meldungen, wahlen = [], [], list(wahlen)

    async def ask_confirm(frage, optionen):
        meldungen.append(("frage", frage, [w for w, _ in optionen]))
        return wahlen.pop(0)

    async def hf_laden(H_, repo, datei, ordner, nr, von, revision="main"):
        geladen.append((repo, datei.pfad, datei.sha256, Path(ordner), revision))

    ui = NS(info=lambda m: meldungen.append(("info", m)), success=lambda m: meldungen.append(("ok", m)),
            error=lambda m: meldungen.append(("fehler", m)))
    ns = {"ask_confirm": ask_confirm, "_hf_datei_laden": hf_laden, "ui": ui, "asyncio": asyncio,
          "_einrichtung_melden": lambda t: meldungen.append(("debug", t))}
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), ns)
    with mock.patch.object(K, "stand", lambda: stand or [(t, False) for t in K.KATALOG]), \
            mock.patch.object(H, "frei_auf_platte", lambda o: frei):
        asyncio.run(ns["_modelle_anbieten"]())
    return geladen, meldungen


class AblaufTests(unittest.TestCase):
    def test_spaeter_laedt_nichts(self):
        geladen, meldungen = _ablauf(["spaeter"])
        self.assertEqual([], geladen)
        self.assertEqual(1, sum(m[0] == "frage" for m in meldungen))

    def test_ein_teil_laden(self):
        geladen, meldungen = _ablauf(["ja", "video", "ja", "__fertig__"])
        video = K.teil("video")
        self.assertEqual([(d.repo, d.pfad, d.sha256, K.ziel_ordner(video, d), d.revision) for d in video.dateien],
                         geladen)
        texte = " ".join(str(m[1]) for m in meldungen)
        self.assertIn("Lizenz: Apache 2.0", texte)                             # Lizenz vor dem Laden
        self.assertIn(("debug", "Modell video geladen"), meldungen)
        liste = next(m[2] for m in meldungen if m[0] == "frage" and m[1].startswith("Modelle für die Motoren"))
        self.assertEqual([t.id for t in K.KATALOG] + ["__fertig__"], liste)

    def test_zurueck_laedt_nicht(self):
        geladen, _ = _ablauf(["ja", "musik", "nein", "__fertig__"])
        self.assertEqual([], geladen)

    def test_krea_zeigt_ordner_und_link(self):
        geladen, meldungen = _ablauf(["ja", "krea", "__fertig__"])
        self.assertEqual([], geladen)
        texte = [str(m[1]) for m in meldungen if m[0] == "info"]
        self.assertTrue(any(t.startswith("📁 Ordner:") for t in texte))
        self.assertTrue(any("huggingface.co/krea/" in t for t in texte))

    def test_zu_wenig_platz(self):
        geladen, meldungen = _ablauf(["ja", "sehen", "__fertig__"], frei=0)
        self.assertEqual([], geladen)
        self.assertTrue(any(m[0] == "fehler" and "Nicht genug Platz" in m[1] for m in meldungen))


if __name__ == "__main__":
    unittest.main()
