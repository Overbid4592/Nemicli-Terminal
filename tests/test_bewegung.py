"""Bewegung nach Text für die Figur: SprongMotion festgenagelt laden und prüfen, Strichmännchen im Fenster,
Posen und Werkzeug der KI. Ohne echte Laufzeit; der Lauf mit DLL und Modell nur mit NEMICLI_SLOW_TESTS=1."""

import base64
import hashlib
import io
import json
import math
import os
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for d in ("core", "tools", "engines", "ui"):
    p = str(ROOT / d)
    if p not in sys.path:
        sys.path.insert(0, p)

import bewegung as BW            # noqa: E402
import figur                     # noqa: E402
import startseite                # noqa: E402
import terminal_fenster as TF    # noqa: E402

NAMEN = ["pelvis", "l_hip", "r_hip", "spine1", "l_knee", "r_knee", "spine2", "l_ankle", "r_ankle", "spine3", "l_foot",
         "r_foot", "neck", "l_collar", "r_collar", "head", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist",
         "r_wrist"]
ELTERN = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19]


def _koerper(dreh: float = 0.0) -> list:
    """Ein stehender Körper, um `dreh` (Bogenmaß) um die Hochachse gedreht; Blick zu Beginn nach +Z."""
    roh = {n: (0.0, 1.0, 0.0) for n in NAMEN}
    roh.update(head=(0.0, 1.65, 0.0), neck=(0.0, 1.5, 0.0), l_shoulder=(0.18, 1.45, 0.0),
               r_shoulder=(-0.18, 1.45, 0.0), l_foot=(0.1, 0.0, 0.05), r_foot=(-0.1, 0.0, 0.05))
    c, s = math.cos(dreh), math.sin(dreh)
    return [(x * c + z * s, y, -x * s + z * c) for x, y, z in (roh[n] for n in NAMEN)]


def _bewegung(tmp: Path, bilder: int = 10, schleife: bool = False, name: str = "b.json") -> Path:
    pfad = tmp / name
    pfad.write_text(json.dumps({"fps": 20, "namen": NAMEN, "eltern": ELTERN, "schleife": schleife,
                                "bilder": [[list(p) for p in _koerper()] for _ in range(bilder)]}), encoding="utf-8")
    return pfad


def _paket(eintraege: dict) -> bytes:
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w:gz") as tar:
        for name, inhalt in eintraege.items():
            info = tarfile.TarInfo(name)
            info.size = len(inhalt)
            tar.addfile(info, io.BytesIO(inhalt))
    return puffer.getvalue()


class Grundlage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        for name, wert in (("_install", lambda: self.tmp), ("_neuer_ordner", lambda: self.tmp / "neu")):
            p = patch.object(BW, name, wert)
            p.start()
            self.addCleanup(p.stop)


class Festgenagelt(unittest.TestCase):
    def test_pruefsummen_vollstaendig(self):
        for name in ("PAKET_SHA", "DLL_SHA", "MODELL_SHA"):
            wert = getattr(BW, name)
            with self.subTest(name=name):
                self.assertRegex(wert, r"^[0-9a-f]{64}$")
        self.assertTrue(BW.URL.startswith("https://github.com/"))


class Stand(Grundlage):
    def test_ohne_alles(self):
        st = BW.stand()
        self.assertFalse(st["bereit"])
        self.assertIn("/bewegung holen", " ".join(st["zeilen"]))

    def test_mit_laufzeit_und_modell(self):
        BW.dll_pfad().parent.mkdir(parents=True)
        BW.dll_pfad().write_bytes(b"x")
        BW.modell_ordner().mkdir(parents=True, exist_ok=True)
        with open(BW.modell_pfad(), "wb") as f:
            f.truncate(BW.MODELL_GROESSE)
        self.assertTrue(BW.stand()["bereit"])

    def test_motor_laedt_nur_gepruefte_dateien(self):
        BW.dll_pfad().parent.mkdir(parents=True)
        BW.dll_pfad().write_bytes(b"kein echtes dll")
        with self.assertRaises(RuntimeError):
            BW.Motor()._laden()

    def test_prompt_sauber(self):
        self.assertEqual(BW.prompt_sauber("  waves\n with \x00 the   hand "), "waves with the hand")
        self.assertEqual(len(BW.prompt_sauber("a" * 500)), 200)


class Auspacken(Grundlage):
    def _mit_sha(self, inhalt: bytes, dll: bytes):
        return patch.multiple(BW, PAKET_SHA=hashlib.sha256(inhalt).hexdigest(), DLL_SHA=hashlib.sha256(dll).hexdigest())

    def test_nur_die_erlaubten_dateien(self):
        dll = b"MZ-dll"
        stamm = f"sprongmotion-{BW.VERSION}-windows-x86_64"
        paket = _paket({f"{stamm}/lib/{BW.DLL}": dll, f"{stamm}/LICENSE": b"apache", f"{stamm}/NOTICE": b"hinweis",
                        f"{stamm}/README.md": b"nicht", "../boese.dll": b"nein", f"{stamm}/../../x.dll": b"nein"})
        with self._mit_sha(paket, dll):
            dateien = BW.auspacken(paket, self.tmp / "ziel")
        self.assertEqual(dateien, ["LICENSE", "NOTICE", BW.DLL])
        self.assertEqual(sorted(f.name for f in (self.tmp / "ziel").iterdir()), ["LICENSE", "NOTICE", BW.DLL])
        self.assertFalse((self.tmp / "boese.dll").exists())
        self.assertFalse(any(self.tmp.parent.glob("x.dll")))

    def test_falsche_dll_wird_verworfen(self):
        stamm = f"sprongmotion-{BW.VERSION}-windows-x86_64"
        paket = _paket({f"{stamm}/lib/{BW.DLL}": b"anders"})
        with self._mit_sha(paket, b"erwartet"), self.assertRaises(ValueError):
            BW.auspacken(paket, self.tmp / "ziel")
        self.assertFalse((self.tmp / "ziel").exists())

    def test_falsches_paket_wird_nicht_geoeffnet(self):
        with self.assertRaises(ValueError):
            BW.auspacken(b"irgendwas", self.tmp / "ziel")

    def test_holen_legt_ab(self):
        dll = b"MZ-dll"
        stamm = f"sprongmotion-{BW.VERSION}-windows-x86_64"
        paket = _paket({f"{stamm}/lib/{BW.DLL}": dll})
        with self._mit_sha(paket, dll), patch.object(BW, "_paket_laden", return_value=paket):
            ok, zeilen = BW.holen()
            self.assertTrue(ok, zeilen)
            self.assertTrue(BW.dll_pfad().is_file())
            ok, zeilen = BW.holen()                                   # schon da: nichts laden
            self.assertIn("schon da", zeilen[0])


class Herunterladen(Grundlage):
    def _client(self, antwort):
        import httpx
        echt = httpx.Client

        def gebaut(*a, **k):
            k["transport"] = httpx.MockTransport(antwort)
            return echt(*a, **k)
        return patch.object(httpx, "Client", gebaut)

    def test_nur_github(self):
        import httpx

        def antwort(anfrage):
            return httpx.Response(302, headers={"location": "https://boese.example/x.tar.gz"})
        with self._client(antwort), self.assertRaises(OSError):
            BW._paket_laden()

    def test_groesse_und_sha(self):
        import httpx

        def antwort(anfrage):
            return httpx.Response(200, content=b"zu klein")
        with self._client(antwort), self.assertRaises(OSError):
            BW._paket_laden()

    def test_richtiges_paket(self):
        import httpx
        inhalt = b"p" * 64

        def antwort(anfrage):
            self.assertEqual(anfrage.url.host, "github.com")
            return httpx.Response(200, content=inhalt)
        with self._client(antwort), patch.multiple(BW, PAKET_GROESSE=len(inhalt),
                                                   PAKET_SHA=hashlib.sha256(inhalt).hexdigest()):
            self.assertEqual(BW._paket_laden(), inhalt)


class ModellUebernehmen(Grundlage):
    def test_nur_mit_passender_sha(self):
        quelle = self.tmp / "geladen.gguf"
        quelle.write_bytes(b"modell" * 10)
        with patch.multiple(BW, MODELL_SHA=hashlib.sha256(b"modell" * 10).hexdigest(), MODELL_GROESSE=60):
            ok, _z = BW.modell_einrichten(quelle)
            self.assertTrue(ok)
            self.assertEqual(BW.modell_pfad().read_bytes(), b"modell" * 10)
            self.assertTrue(quelle.is_file())                          # das Original bleibt
        anders = self.tmp / "anders.gguf"
        anders.write_bytes(b"x" * 60)
        BW.modell_pfad().unlink()
        with patch.multiple(BW, MODELL_SHA="0" * 64, MODELL_GROESSE=60):
            ok, zeilen = BW.modell_einrichten(anders)
        self.assertFalse(ok)
        self.assertIn("nicht übernommen", zeilen[0])

    def test_aus_downloads_verschieben(self):
        heim = self.tmp / "heim"
        (heim / "Downloads").mkdir(parents=True)
        quelle = heim / "Downloads" / BW.MODELL
        quelle.write_bytes(b"m" * 30)
        sha = hashlib.sha256(b"m" * 30).hexdigest()
        with patch.multiple(BW, MODELL_SHA=sha, MODELL_GROESSE=30), patch.object(Path, "home", lambda: heim):
            ok, zeilen = BW.modell_einrichten()
        self.assertTrue(ok)
        self.assertIn("verschoben", zeilen[0])
        self.assertFalse(quelle.exists())
        self.assertEqual(BW.modell_pfad().read_bytes(), b"m" * 30)

    def test_fehlt(self):
        ok, zeilen = BW.modell_einrichten(self.tmp / "gibtsnicht.gguf")
        self.assertFalse(ok)
        self.assertIn(BW.MODELL_SEITE, " ".join(zeilen))


class Fenster(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_bewegung_lesen_und_pruefen(self):
        bew = TF.strich_lesen(_bewegung(self.tmp), "froh", True)
        self.assertEqual(len(bew["bilder"]), 10)
        self.assertEqual((bew["gesicht"], bew["vorrang"], bew["fps"]), ("froh", True, 20))
        kaputt = self.tmp / "k.json"
        kaputt.write_text(json.dumps({"fps": 20, "namen": NAMEN, "eltern": ELTERN, "bilder": [[[0, 0]] * 22]}),
                          encoding="utf-8")
        self.assertIsNone(TF.strich_lesen(kaputt))
        weit = self.tmp / "w.json"
        weit.write_text(json.dumps({"fps": 20, "namen": NAMEN, "eltern": ELTERN, "bilder": [[[1e9, 0, 0]] * 22]}),
                        encoding="utf-8")
        self.assertIsNone(TF.strich_lesen(weit))
        falsch = self.tmp / "f.json"
        falsch.write_text(json.dumps({"fps": 20, "namen": NAMEN, "eltern": [5] * 22, "bilder": [[[0, 0, 0]] * 22]}),
                          encoding="utf-8")
        self.assertIsNone(TF.strich_lesen(falsch))
        self.assertIsNone(TF.strich_lesen(self.tmp / "gibtsnicht.json"))
        self.assertIsNone(TF.strich_lesen(self.tmp / "bild.png"))
        self.assertEqual(TF.strich_lesen(_bewegung(self.tmp), "<b>öse!")["gesicht"], "bse")       # nur a–z

    def test_befehl_bewegung(self):
        pfad = _bewegung(self.tmp, schleife=True)
        kopf = base64.urlsafe_b64encode(json.dumps({"datei": str(pfad), "gesicht": "denkt"}).encode()).decode()
        name, bew = TF.figur_befehl("bewegung=" + kopf)
        self.assertEqual(name, "bewegung")
        self.assertTrue(bew["schleife"])
        self.assertIsNone(TF.figur_befehl("bewegung=@@@"))

    def test_gesichter_mit_freien_namen(self):
        from PIL import Image
        bild = self.tmp / "zwinkert.png"
        Image.new("RGBA", (8, 8)).save(bild)
        daten = {"art": "strich", "bilder": {"zwinkert": str(bild), "Böse Name": str(bild)}}
        art, bilder = TF.figur_befehl("posen=" + base64.urlsafe_b64encode(json.dumps(daten).encode()).decode())[1]
        self.assertEqual((art, set(bilder)), ("strich", {"zwinkert"}))
        leer = {"art": "strich", "bilder": {}}
        self.assertEqual(TF.figur_befehl("posen=" + base64.urlsafe_b64encode(json.dumps(leer).encode()).decode()),
                         ("posen", ("strich", {})))

    def test_bildnummer_schleife_und_ende(self):
        bew = {"bilder": [0] * 40, "fps": 20, "schleife": True}
        self.assertEqual(TF.strich_bild(bew, 2.5), (10, False))      # 50 Bilder -> 50 mod 40
        bew["schleife"] = False
        self.assertEqual(TF.strich_bild(bew, 1.0), (20, False))
        self.assertEqual(TF.strich_bild(bew, 3.0), (39, True))

    def test_zwischen_zwei_bildern_ueberblendet(self):
        bilder = [[(float(i), 0.0, 0.0)] for i in range(4)]             # x = Bildnummer
        bew = {"bilder": bilder, "fps": 10, "schleife": False}
        gelenke, fertig = TF.strich_zwischen(bew, 0.15)                 # Bild 1,5
        self.assertAlmostEqual(gelenke[0][0], 1.5)
        self.assertFalse(fertig)
        self.assertEqual(TF.strich_zwischen(bew, 0.5), (bilder[-1], True))
        bew["schleife"] = True
        gelenke, fertig = TF.strich_zwischen(bew, 0.35)                 # Bild 3,5: vom letzten weich ins erste
        self.assertAlmostEqual(gelenke[0][0], 1.5)
        self.assertFalse(fertig)

    def test_sanft(self):
        self.assertEqual((TF.strich_sanft(0), TF.strich_sanft(1), TF.strich_sanft(0.5)), (0.0, 1.0, 0.5))
        self.assertLess(TF.strich_sanft(0.1), 0.1)                      # läuft langsam an
        self.assertEqual(TF.strich_sanft(2), 1.0)

    def test_mischen(self):
        a, b = [(0.0, 0.0, 0.0)], [(2.0, 4.0, 6.0)]
        self.assertEqual(TF.strich_mischen(a, b, 0.5), [(1.0, 2.0, 3.0)])
        self.assertEqual(TF.strich_mischen(a, b, 2.0), b)

    def test_schraeg_gesehen(self):
        self.assertEqual(TF.strich_projektion([(1.0, 2.0, 0.0)], 0.0), [(1.0, 2.0, 0.0)])
        x, y, z = TF.strich_projektion([(0.0, 1.0, 1.0)], math.radians(90))[0]
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(z, 0.0, places=6)

    def test_von_vorn_und_hinten(self):
        self.assertTrue(TF.strich_von_vorn(_koerper(), NAMEN))
        self.assertFalse(TF.strich_von_vorn(_koerper(math.pi), NAMEN))
        self.assertTrue(TF.strich_von_vorn(_koerper()[:3], ["a", "b", "c"]))    # ohne Schultern: von vorn

    def test_steht_ueber_der_kopfzeile(self):
        zh = 27.0
        h = TF.figur_hoehe("strich", zh, 900, 5)
        self.assertLessEqual(h, TF.FIGUR_ZEILEN["strich"] * zh)
        self.assertLessEqual(h, (900 / zh - 5 - 1) * zh)
        fest = TF.FIGUR_ZEILEN["strich"] * 25.0                                  # feste Größe, egal wie groß
        self.assertEqual(TF.figur_hoehe("strich", 25.0, 1000, 5), fest)
        self.assertEqual(TF.figur_hoehe("strich", 25.0, 2400, 5), fest)


class Posen(unittest.TestCase):
    def setUp(self):
        self.alt = dict(figur._STAND)
        self.addCleanup(lambda: (figur._STAND.clear(), figur._STAND.update(self.alt)))
        self.gesendet, self.auftraege = [], []
        for ziel, name, wert in ((startseite, "STEUERN", self.gesendet.append),
                                 (startseite, "echtes_fenster", lambda: True),
                                 (figur, "bewegung_anfordern", lambda prompt, **k: self.auftraege.append((prompt, k))),
                                 (figur, "_strich_bereit", lambda: True),
                                 (figur, "gesichter", lambda name: {"neutral": Path("n.png"), "froh": Path("f.png")})):
            p = patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        figur._STAND.update(pose=None, posen=None, herz=False, antwort="", art="")

    def test_jede_pose_hat_eine_bewegung(self):
        self.assertEqual(set(figur.BEWEGUNG_JE_POSE), set(figur.POSEN))
        self.assertTrue(figur.BEWEGUNG_JE_POSE["sitzen"][1])          # Ruhe ist eine Schleife

    def test_melden_als_strichmaennchen(self):
        figur.melden("PersonA")
        posen = json.loads(base64.urlsafe_b64decode(self.gesendet[0][len("posen="):]))
        self.assertEqual(posen["art"], "strich")
        self.assertEqual(set(posen["bilder"]), {"neutral", "froh"})
        prompts = [a[0] for a in self.auftraege]
        self.assertEqual(prompts[0], figur.BEWEGUNG_JE_POSE["sitzen"][0])
        self.assertEqual(prompts[1], figur.BEWEGUNG_JE_POSE["winken"][0])
        self.assertEqual(self.auftraege[1][1]["gesicht"], "froh")

    def test_status_wird_bewegung(self):
        figur.melden("PersonA")
        self.auftraege.clear()
        figur.status("denkt")
        prompt, k = self.auftraege[0]
        self.assertEqual(prompt, figur.BEWEGUNG_JE_POSE["nachdenken"][0])
        self.assertTrue(k["schleife"])
        self.assertEqual(k["gesicht"], "neutral")                       # „denkt“ ist nicht gemalt -> neutral

    def test_bewegen_mit_vorrang(self):
        figur.melden("PersonA")
        self.auftraege.clear()
        text = figur.bewegen("turns around once\n", "lächel mal", 20)
        prompt, k = self.auftraege[0]
        self.assertEqual((prompt, k["vorrang"], k["gesicht"], k["sekunden"]), ("turns around once", True, "froh", 10.0))
        self.assertIn("turns around once", text)

    def test_ohne_strichmaennchen_sagt_es_warum(self):
        with patch.object(figur, "_strich_bereit", lambda: False):
            figur._STAND.update(posen=None, art="")
            with self.assertRaises(RuntimeError):
                figur.bewegen("jumps")

    def test_gesicht_finden(self):
        da = ["neutral", "froh", "zwinkert"]
        self.assertEqual(figur.gesicht_finden("zwinkert", da), "zwinkert")
        self.assertEqual(figur.gesicht_finden("lächel mal", da), "froh")
        self.assertEqual(figur.gesicht_finden("verliebt", da), "froh")      # Ersatz
        self.assertEqual(figur.gesicht_finden("", da), "neutral")
        self.assertEqual(figur.gesicht_finden("quatsch", da), "neutral")


class Rechnen(unittest.TestCase):
    def test_gleiche_anfrage_aus_dem_cache(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        aufrufe = []

        def rechnen(prompt, sekunden, seed, schleife):
            aufrufe.append(prompt)
            return {"fps": 20, "namen": NAMEN, "eltern": ELTERN, "bilder": [[list(p) for p in _koerper()]],
                    "schleife": schleife, "prompt": prompt}
        with patch.object(figur, "_cache_ordner", lambda: tmp), patch.object(BW.MOTOR, "rechnen", rechnen):
            a = figur._bewegung_datei("jumps", 3.0, 7, False)
            b = figur._bewegung_datei("jumps", 3.0, 7, False)
            c = figur._bewegung_datei("jumps", 3.0, 8, False)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(aufrufe, ["jumps", "jumps"])
        self.assertIsNotNone(TF.strich_lesen(a))                        # das Fenster liest, was hier entsteht

    def test_im_hintergrund_ans_fenster(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        gesendet = []
        with patch.object(figur, "_bewegung_datei", lambda *a: _bewegung(tmp)), \
                patch.object(figur, "_senden", gesendet.append), \
                patch.object(startseite, "echtes_fenster", lambda: True):
            figur.bewegung_anfordern("waves", gesicht="froh", vorrang=True)
            ende = time.monotonic() + 3
            while not gesendet and time.monotonic() < ende:
                time.sleep(0.02)
        self.assertTrue(gesendet)
        kopf = json.loads(base64.urlsafe_b64decode(gesendet[0][len("bewegung="):]))
        self.assertEqual((kopf["gesicht"], kopf["vorrang"]), ("froh", True))


class Einspeisen(Grundlage):
    def test_mitgeliefertes_nur_wo_es_fehlt(self):
        mit = self.tmp / "mit"
        (mit / f"sprongmotion-{BW.VERSION}").mkdir(parents=True)
        (mit / BW.MODELL).write_bytes(b"m" * 20)
        (mit / f"sprongmotion-{BW.VERSION}" / BW.DLL).write_bytes(b"dll")
        (mit / f"sprongmotion-{BW.VERSION}" / "LICENSE").write_bytes(b"apache")
        with patch.multiple(BW, MODELL_SHA=hashlib.sha256(b"m" * 20).hexdigest(), MODELL_GROESSE=20,
                            DLL_SHA=hashlib.sha256(b"dll").hexdigest()):
            self.assertEqual(sorted(BW.einspeisen(mit)), sorted([BW.MODELL, BW.DLL]))
            self.assertTrue((BW.ablage() / "LICENSE").is_file())
            self.assertEqual(BW.einspeisen(mit), [])                    # schon da: nichts
        BW.modell_pfad().unlink()
        with patch.multiple(BW, MODELL_SHA="0" * 64, MODELL_GROESSE=20):
            self.assertEqual(BW.einspeisen(mit), [])                    # falsche Prüfsumme: nicht übernommen
        self.assertFalse(BW.modell_pfad().exists())

    def test_ohne_exe_nichts(self):
        self.assertEqual(BW.einspeisen(None), [])


class BauPrueft(unittest.TestCase):
    def test_ohne_motion_kein_bau(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import build_exe
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        with patch.object(build_exe, "ROOT", tmp):
            fehlt = build_exe.motion_fehlt()
        self.assertEqual(len(fehlt), 4)                                 # Modell, DLL, LICENSE, NOTICE
        self.assertIn(BW.MODELL, fehlt[0])


class BauRaeumtAuf(unittest.TestCase):
    def test_setup_zwischenstand_weg(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import build_exe
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "werk" / "NemiCLI-Setup-1.2.3").mkdir(parents=True)
        (tmp / "werk" / "NemiCLI-Setup-1.2.3" / "x.pyz").write_bytes(b"x" * 100)
        (tmp / "NemiCLI-Setup-1.2.3.spec").write_text("spec", encoding="utf-8")
        (tmp / "nutzlast.zip").write_bytes(b"zip")
        build_exe.zwischenstand_weg(tmp)
        self.assertFalse((tmp / "werk").exists())
        self.assertFalse(any(tmp.glob("*.spec")))
        self.assertTrue((tmp / "nutzlast.zip").exists())                 # nur Zwischenstände, sonst nichts


class OhneFenster(unittest.TestCase):
    def test_kein_rechnen_ohne_fenster(self):
        with patch.object(startseite, "echtes_fenster", lambda: False), \
                patch.object(figur, "_bewegung_datei", side_effect=AssertionError("darf nicht rechnen")):
            figur.bewegung_anfordern("jumps")
        self.assertFalse(figur._ARBEIT["liste"])


class Werkzeug(unittest.TestCase):
    def test_figur_bewegen(self):
        import actions
        with patch.object(figur, "bewegen", return_value="Figur: Bewegung „jumps“ (3.0 s).") as b:
            erg = actions._figur_bewegen({"bewegung": "jumps", "gesicht": "froh", "sekunden": "2"})
        self.assertTrue(erg.ok)
        b.assert_called_once_with("jumps", "froh", 2.0)
        self.assertFalse(actions._figur_bewegen({}).ok)
        with patch.object(figur, "bewegen", side_effect=RuntimeError("nicht eingerichtet")):
            erg = actions._figur_bewegen({"bewegung": "jumps"})
        self.assertFalse(erg.ok)
        self.assertIn("nicht eingerichtet", erg.text)

    def test_selbstwissen(self):
        import commands
        import persona
        self.assertIn("- figur_bewegen", Path(persona.__file__).read_text(encoding="utf-8"))
        self.assertIn("/bewegung holen", commands.hilfe_fuer_ki("/bewegung"))


@unittest.skipUnless(os.environ.get("NEMICLI_SLOW_TESTS") == "1" and BW.stand()["bereit"],
                     "SprongMotion nicht eingerichtet oder langsame Tests aus")
class EchteLaufzeit(unittest.TestCase):
    def test_winken(self):
        d = BW.MOTOR.rechnen("waves with the right hand", 3.0, 7)
        self.assertEqual(len(d["bilder"]), 60)
        self.assertEqual(len(d["bilder"][0]), BW.GELENKE)
        self.assertEqual(d["namen"][15], "head")


if __name__ == "__main__":
    unittest.main()
