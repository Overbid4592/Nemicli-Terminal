"""Härtungen aus der Code-Durchsicht (mittlere Funde): Gegenbeweis der Chrome-Brücke, Downloads, Anhänge,
Bilder für Cloud-Modelle, Steuerzeichen, Rückfragen ohne Fenster, Konfiguration, Netz-Marken, Wache."""
import asyncio
import hashlib
import hmac
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import actions                                         # noqa: E402
import anhang                                          # noqa: E402
import chromebridge as C                               # noqa: E402
import hfladen                                         # noqa: E402
import vision                                          # noqa: E402
import webui                                           # noqa: E402


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BrueckeTests(unittest.TestCase):
    def test_gegenbeweis_ohne_schluessel_zu_senden(self):
        async def go():
            async def beantworten(auftrag, fremd):
                return ""
            port = _freier_port()
            br = C.Bridge(asyncio.get_running_loop(), beantworten, key="geheim123", port=port)
            self.assertTrue(br.start())
            try:
                def holen(pfad):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{pfad}", timeout=10) as r:
                        return json.loads(r.read().decode("utf-8"))
                nonce = "ab" * 16
                antwort = await asyncio.to_thread(holen, f"/hallo?nonce={nonce}")
                soll = hmac.new(b"geheim123", f"nemicli:{nonce}".encode(), hashlib.sha256).hexdigest()
                self.assertEqual(soll, antwort["beweis"])
            finally:
                br.stop()
        asyncio.run(go())


class DownloadTests(unittest.TestCase):
    def test_hf_name_bleibt_im_ordner(self):
        with tempfile.TemporaryDirectory() as t:
            for name in (r"..\..\x.gguf", "C:x.gguf", "a/b.gguf"):
                datei = NS(name=name, groesse=1, sha256="0", pfad=name)
                with self.subTest(name=name), self.assertRaises(hfladen.PruefFehler):
                    hfladen.laden("x/y", datei, Path(t), lambda *a: None, threading.Event())

    def test_vollstaendige_teildatei_ohne_anfrage(self):
        with tempfile.TemporaryDirectory() as t:
            inhalt = b"x" * 10
            (Path(t) / "m.gguf.part").write_bytes(inhalt)
            datei = NS(name="m.gguf", groesse=10, sha256=hashlib.sha256(inhalt).hexdigest(), pfad="m.gguf")
            with mock.patch.object(hfladen, "_session", side_effect=AssertionError("keine Anfrage")):
                ziel = hfladen.laden("x/y", datei, Path(t), lambda *a: None, threading.Event())
            self.assertEqual(inhalt, ziel.read_bytes())


class AnhangTests(unittest.TestCase):
    def test_zugangsdaten_nie_automatisch(self):
        for name in (".env", "id_ed25519", "server.pem", ".git-credentials"):
            with self.subTest(name=name):
                self.assertIsNotNone(anhang.geheim(Path("C:/x") / name))
        self.assertIsNotNone(anhang.geheim(Path("C:/Users/user/.ssh/config")))
        self.assertIsNone(anhang.geheim(Path("C:/x/bericht.txt")))

    def test_netzwerkpfad_wird_nicht_angefasst(self):
        echt = Path.exists

        def exists(self):
            if str(self).startswith("\\\\"):
                raise AssertionError(f"Zugriff auf {self}")
            return echt(self)

        with mock.patch("pathlib.Path.exists", exists):
            self.assertEqual([], anhang.finde_pfade(r'schau "\\fremd.example\share\a.txt" an'))


class BildTests(unittest.TestCase):
    def test_art_am_inhalt(self):
        self.assertEqual("image/jpeg", vision._art_aus_bytes(b"\xff\xd8\xff\xe0" + b"0" * 10))
        self.assertEqual("image/png", vision._art_aus_bytes(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual("image/webp", vision._art_aus_bytes(b"RIFF1234WEBPVP8 "))

    def test_grosses_jpeg_ohne_endlosschleife(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("ohne Pillow")
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "gross.jpg"
            Image.new("RGB", (64, 64), (200, 30, 30)).save(p, "JPEG")
            with mock.patch.object(vision, "GROSS_BYTES", 10):          # gilt als zu groß
                uri = vision.to_data_uri(p)
            self.assertTrue(uri.startswith("data:image/jpeg;base64,"))


class AnzeigeTests(unittest.TestCase):
    def test_steuerzeichen_fliegen_raus(self):
        import screen
        sauber = screen.terminal_safe("a\x1b]52;c;eA==\x07bc\x1b[2A\x1b[2K\ndef\t\x9b!")
        self.assertEqual("abc\ndef\t!", sauber)                         # OSC 52 und Cursor-Folgen ganz weg
        self.assertEqual("\x1b[1;31mrot\x1b[0m", screen.terminal_safe("\x1b[1;31mrot\x1b[0m"))   # Farben bleiben

    def test_meldung_mit_klammern_stuerzt_nicht(self):
        import ui
        with mock.patch.object(ui, "console") as c:
            ui.error("Datei [/b]kaputt[link=file:///x].txt")
        self.assertIn("\\[/b]", c.print.call_args.args[0])


class RueckfrageTests(unittest.TestCase):
    def test_ohne_fenster_gilt_die_sichere_wahl(self):
        async def go():
            br = webui.WebBridge(asyncio.get_running_loop())
            return await br.ask("Datei löschen?", [("yes", "Ja"), ("no", "Nein")])
        self.assertEqual("no", asyncio.run(go()))


class NetzMarkeTests(unittest.TestCase):
    def test_befehl_und_code_aus_dem_netz(self):
        self.assertTrue(actions._holt_aus_dem_netz({"tool": "befehl", "befehl": "iwr https://example.org"}))
        self.assertTrue(actions._holt_aus_dem_netz({"tool": "code_ausfuehren", "code": "import urllib.request"}))
        self.assertFalse(actions._holt_aus_dem_netz({"tool": "befehl", "befehl": "Get-ChildItem"}))

    def test_berichte_behalten_die_netz_marke(self):
        import agentrag
        import fremddaten
        with mock.patch.object(agentrag.indexdb, "quelle_lesen",
                               return_value={"text": "Inhalt", "brocken": 1, "ab": 1, "weiter": 0, "gekuerzt": False,
                                             "ref": "bericht:x.md", "kind": "bericht"}), \
             mock.patch.object(actions, "fremdinhalt") as markiert:
            text = agentrag.lesen({"ref": "bericht:x.md"})
        self.assertIn(fremddaten.NETZ_MARKE, text)
        markiert.assert_called()


class KonfigTests(unittest.TestCase):
    def test_gleichzeitige_updates_verlieren_nichts(self):
        import config
        with tempfile.TemporaryDirectory() as t, mock.patch.object(config, "_FILE", Path(t) / "c.json"):
            faeden = [threading.Thread(target=config.update, kwargs={f"k{i}": i}) for i in range(20)]
            for f in faeden:
                f.start()
            for f in faeden:
                f.join()
            self.assertEqual({f"k{i}": i for i in range(20)}, json.loads((Path(t) / "c.json").read_text("utf-8")))


class KonfigLesenTests(unittest.TestCase):
    def test_nur_bei_aenderung_neu_lesen(self):
        import config
        with tempfile.TemporaryDirectory() as t, mock.patch.object(config, "_FILE", Path(t) / "c.json"):
            config._FILE.write_text('{"a": 1}', encoding="utf-8")
            echt = config._lesen
            with mock.patch.object(config, "_lesen", side_effect=echt) as lesen:
                self.assertEqual({"a": 1}, config.load())
                stand = config.load()
                stand["a"] = 99                                       # Kopie: der Merker bleibt unberührt
                self.assertEqual({"a": 1}, config.load())
                self.assertEqual(1, lesen.call_count)
                config.update(b=2)                                    # Datei geändert → neu lesen
                self.assertEqual({"a": 1, "b": 2}, config.load())


class WacheTests(unittest.TestCase):
    def test_kodier_kurzformen(self):
        from wache import regeln
        for kurz in ("-e", "-en", "-ec", "/enc", "-encodedc"):
            with self.subTest(kurz=kurz):
                gefunden = regeln._flags_in(f"powershell {kurz} QQBCAEMA")
                self.assertTrue({"-e", "-enc"} & set(gefunden), gefunden)
                self.assertEqual(1, len({"-e", "-enc"} & set(gefunden)))          # nicht doppelt gezählt
        self.assertFalse({"-e", "-enc"} & set(regeln._flags_in("powershell -ex bypass")))


if __name__ == "__main__":
    unittest.main()
