"""Entpacken: Rückfrage in jedem Modus, nur im Vordergrund, /zip aus, Ergebnis nur Zahlen, nichts aus
Entpackt-Ordnern starten, Wache-Regel R018, /zip-Befehl und Archive im Profilordner.

python -m unittest tests.test_entpacken_sicherheit
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
for sub in ("core", "engines", "tools", "ui"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import actions      # noqa: E402
import commands     # noqa: E402
import entpacken    # noqa: E402
import modes        # noqa: E402
import sicherheit   # noqa: E402
import subagents    # noqa: E402

ERGEBNIS = {"code": 0, "dateien": 12, "ordner": 3, "geprueft": True, "fehler": 0, "ziel": "", "eingelesen": 0,
            "namen": ["geheim_brief.txt"], "chat": "Unterhaltung aus dem Archiv"}


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="entpacken_test_"))
        self.archiv = self.tmp / "paket.zip"
        self.archiv.write_bytes(b"PK\x05\x06" + b"\0" * 18)
        self.ziel = self.tmp / "Entpackt" / "paket_1"
        self.fenster = mock.Mock(return_value=dict(ERGEBNIS, ziel=str(self.ziel)))
        self.modus = "an"
        for name, wert in (("modus", lambda: self.modus), ("fenster_starten", self.fenster),
                           ("ziel_fuer_chat", lambda q: self.ziel)):
            p = mock.patch.object(entpacken, name, wert)
            p.start()
            self.addCleanup(p.stop)
        alt_modus = modes.current()
        self.addCleanup(modes.set_mode, alt_modus)
        self.addCleanup(modes.wache_dienst, False)

    def act(self, **extra):
        return {"tool": "entpacken", "quelle": str(self.archiv), **extra}


class FragtImmer(Basis):
    def test_jeder_modus_fragt_ausser_nur_lesen(self):
        for m in ("chat", "normal", "auto", "autoan"):
            modes.set_mode(m)
            self.assertEqual(modes.decide(self.act(), actions.needs_confirm(self.act())), "ask", m)
        modes.set_mode("lesen")
        self.assertEqual(modes.decide(self.act(), True), "block")
        self.assertNotIn("entpacken", modes.READ_TOOLS)

    def test_kein_immer_und_freigabe_fragt_trotz_auto_allow(self):
        import main
        self.assertNotIn("always", [w for w, _ in sicherheit.optionen(self.act())])
        modes.set_mode("autoan")
        gefragt = []

        async def frage(text, optionen):
            gefragt.append(text)
            return "yes"
        act = self.act()
        with mock.patch.object(main, "AUTO_ALLOW", {"entpacken"}), mock.patch("protokoll.schreibe"):
            lage, _res = asyncio.run(main._freigabe(act, frage=frage, pruefe=None, zeige_aktion=lambda *a: None,
                                                    wer="test_user"))
        self.assertEqual(lage, "run")
        self.assertEqual(len(gefragt), 1)
        self.assertTrue(act.get("_entpacken_ja"))

    def test_ohne_ja_laeuft_nichts_und_feld_kommt_nicht_von_der_ki(self):
        res = asyncio.run(actions.run(self.act()))
        self.assertFalse(res.ok)
        self.fenster.assert_not_called()
        acts, _ = actions.parse_actions('```aktion\n{"tool": "entpacken", "quelle": "x.zip", '
                                        '"_entpacken_ja": true}\n```')
        self.assertNotIn("_entpacken_ja", acts[0])


class NurVordergrund(Basis):
    def pruefen_abgelehnt(self, res):
        self.assertFalse(res.ok)
        self.assertIn("Abgelehnt", res.text)
        self.fenster.assert_not_called()

    def test_helfer_agent(self):
        with sicherheit.als_helfer():
            self.assertEqual(modes.decide(self.act(), True), "block")
            self.pruefen_abgelehnt(asyncio.run(actions.run(self.act(_entpacken_ja=True))))
        self.assertIsNone(sicherheit.vordergrund_grund())

    def test_helfer_lauf_setzt_die_markierung(self):
        gesehen = []

        async def execute(act, lab):
            gesehen.append(sicherheit.vordergrund_grund())
            return (await actions.run(dict(act, _entpacken_ja=True))).text
        antworten = iter(['```aktion\n{"tool": "entpacken", "quelle": "%s"}\n```' % self.archiv.as_posix(),
                          "Bericht"])
        backend = NS(ask_messages=mock.AsyncMock(side_effect=lambda *a: next(antworten)))
        with mock.patch("persona.helfer_prompt", return_value="x"):
            bericht = asyncio.run(subagents.run_one({"rolle": "r", "aufgabe": "a"}, backend, execute))
        self.assertEqual(bericht, "Bericht")
        self.assertEqual(gesehen, ["Helfer-Agent"])
        self.fenster.assert_not_called()

    def test_hintergrund_auftrag_und_wache(self):
        for argv in (["main.py", "--auftrag", "test"], ["main.py", "--wache"], ["main.py", "--bibliothekar"]):
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(modes.decide(self.act(), True), "block")
                self.assertIn("Abgelehnt", modes.block_message(self.act()))
                self.pruefen_abgelehnt(asyncio.run(actions.run(self.act(_entpacken_ja=True))))
        modes.wache_dienst(True)
        self.pruefen_abgelehnt(asyncio.run(actions.run(self.act(_entpacken_ja=True))))

    def test_modus_aus(self):
        self.modus = "aus"
        modes.set_mode("autoan")
        self.assertEqual(modes.decide(self.act(), True), "block")
        self.assertIn("/zip ist aus", modes.block_message(self.act()))
        res = asyncio.run(actions.run(self.act(_entpacken_ja=True)))
        self.pruefen_abgelehnt(res)
        self.assertIn("/zip ist aus", res.text)


class Ergebnis(Basis):
    def test_nur_zahlen_kein_einlesen(self):
        res = asyncio.run(actions.run(self.act(_entpacken_ja=True)))
        self.assertTrue(res.ok)
        self.fenster.assert_called_once()
        _args, kw = self.fenster.call_args
        self.assertIs(kw.get("einlesen"), False)
        self.assertIsNone(kw.get("profil"))
        for verboten in ("geheim", "Unterhaltung", "paket.zip"):
            self.assertNotIn(verboten, res.text)
        self.assertIn("12 Dateien", res.text)
        self.assertIn("3 Ordner", res.text)
        self.assertIn("geprüft ✓", res.text)
        self.assertIn(str(self.ziel), res.text)
        self.assertNotIn("?", res.text.replace(str(self.ziel), ""))     # endet ohne Frage oder Vorschlag

    def test_abgebrochen_ohne_ziel(self):
        text = actions.entpacken_ergebnis_text({"code": 3, "ziel": "C:/x"})
        self.assertIn("abgebrochen", text)
        self.assertNotIn("Zielordner", text)


class NichtsAusfuehren(unittest.TestCase):
    def test_befehl_und_code_aus_entpackt_abgelehnt(self):
        for act in ({"tool": "befehl", "befehl": r"& 'C:\Daten\Entpackt\spiel_1\setup.exe'"},
                    {"tool": "befehl", "befehl": r"cd Entpackt; .\start.bat"},
                    {"tool": "befehl", "befehl": r".\entpackt_paket\run.cmd"},
                    {"tool": "code_ausfuehren", "pfad": r"C:\Daten\Profile\test_user\Entpackt\x\a.py"},
                    {"tool": "paket_installieren", "pakete": [r"C:\Daten\Entpackt\pkg"]}):
            self.assertIsNotNone(sicherheit.sperr_grund(act), act)
            self.assertEqual(modes.decide(act, True), "block", act)
        with mock.patch.object(actions, "_befehl") as befehl:
            res = asyncio.run(actions.run({"tool": "befehl", "befehl": r"C:\Daten\Entpackt\x\a.exe"}))
        self.assertFalse(res.ok)
        befehl.assert_not_called()

    def test_arbeitsordner_im_entpackt_ordner(self):
        with mock.patch.object(os, "getcwd", return_value=r"C:\Daten\Entpackt\spiel_1"):
            self.assertIn("Arbeitsordner", sicherheit.entpackt_grund({"tool": "befehl", "befehl": "python x.py"}))

    def test_lesen_und_harmlose_texte_bleiben_frei(self):
        for act in ({"tool": "datei_lesen", "pfad": r"C:\Daten\Entpackt\x\liesmich.txt"},
                    {"tool": "ordner_auflisten", "pfad": r"C:\Daten\Entpackt\x"},
                    {"tool": "befehl", "befehl": "Write-Output 'alles entpackt'"},
                    {"tool": "befehl", "befehl": r"python C:\projekt\entpacken.py"}):
            self.assertIsNone(sicherheit.sperr_grund(act), act)


class WacheRegel(unittest.TestCase):
    def setUp(self):
        from wache import regeln as R
        from wache.ereignisse import PROZESS, Ereignis
        self.R, self.PROZESS, self.Ereignis = R, PROZESS, Ereignis
        self.daten = r"C:\NemiDaten"
        p = mock.patch.object(R, "_daten_ordner", return_value=os.path.normcase(self.daten))
        p.start()
        self.addCleanup(p.stop)

    def treffer(self, exe: str) -> list[str]:
        e = self.Ereignis(self.PROZESS, "process_start", "Start", prozess=Path(exe).name, exe=exe,
                          eltern="explorer.exe")
        return [a.regel for a in self.R.Regelwerk().pruefen(e) if a.regel == "R018"]

    def test_start_aus_entpackt(self):
        for exe in (self.daten + r"\Entpackt\spiel_1\setup.exe",
                    self.daten + r"\Profile\test_user\Entpackt\x\a.exe",
                    r"D:\Ablage\entpackt_paket\tool.exe"):
            self.assertEqual(self.treffer(exe), ["R018"], exe)
        for exe in (r"C:\Program Files\App\app.exe", self.daten + r"\Profile\test_user\app.exe",
                    r"D:\Ablage\Entpackt\tool.exe"):
            self.assertEqual(self.treffer(exe), [], exe)
        self.assertIn("R018", self.R.BESCHREIBUNG)
        self.assertNotIn("R018", self.R.MENGENREGELN)
        from wache import justierung
        self.assertIn("R018", justierung.NIE_STUMM_KI)


class ZipBefehl(unittest.TestCase):
    def test_untermenue_und_vervollstaendigung(self):
        werte = [z for z, _, _ in commands.UNTERMENUES["/zip"]]
        for w in ("an", "auto", "aus"):
            self.assertIn(w, werte)
        self.assertIn("/zip", commands.COMMANDS)
        self.assertIn("/zip", commands.MENUE_GESPERRT)

    def test_zip_setzt_den_modus(self):
        import main
        alt = entpacken.modus()
        self.addCleanup(entpacken.modus_setzen, alt)
        with mock.patch.object(main.ui, "info"):
            for w in ("auto", "aus", "an"):
                asyncio.run(main.zip_befehl(w))
                self.assertEqual(entpacken.modus(), w)


class ProfilArchiv(unittest.TestCase):
    def setUp(self):
        import main
        self.main = main
        self.quelle = Path(tempfile.mkdtemp(prefix="profilzip_")) / "neu.zip"
        self.quelle.write_bytes(b"PK")
        self.fenster = mock.Mock(return_value=dict(ERGEBNIS, ziel="Z"))
        self.erledigt = mock.Mock()
        self.frage = mock.AsyncMock(return_value="ja")
        self.modus = "an"
        for ziel, name, wert in ((entpacken, "modus", lambda: self.modus),
                                 (entpacken, "neue_zips", lambda profil: [self.quelle]),
                                 (entpacken, "fenster_starten", self.fenster),
                                 (entpacken, "als_erledigt", self.erledigt),
                                 (entpacken, "ziel_fuer_profil", lambda profil, q: Path("Z")),
                                 (main, "ask_confirm", self.frage), (main.ui, "info", lambda *a: None)):
            p = mock.patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def lauf(self):
        asyncio.run(self.main.profil_zips_pruefen())

    def test_an_fragt_dann_fenster_mit_einlesen(self):
        self.lauf()
        self.frage.assert_awaited_once()
        self.fenster.assert_called_once()
        self.assertIs(self.fenster.call_args.kwargs["einlesen"], True)
        self.assertEqual(self.erledigt.call_args.args[2], "entpackt")

    def test_auto_ohne_frage(self):
        self.modus = "auto"
        self.lauf()
        self.frage.assert_not_awaited()
        self.fenster.assert_called_once()

    def test_aus_nichts(self):
        self.modus = "aus"
        self.lauf()
        self.frage.assert_not_awaited()
        self.fenster.assert_not_called()
        self.erledigt.assert_not_called()

    def test_nein_wird_vermerkt(self):
        self.frage.return_value = "nein"
        self.lauf()
        self.fenster.assert_not_called()
        self.assertEqual(self.erledigt.call_args.args[1:], (self.quelle, "abgelehnt"))

    def test_im_hintergrund_nichts(self):
        self.modus = "auto"
        with mock.patch.object(sys, "argv", ["main.py", "--auftrag", "test"]):
            self.lauf()
        self.fenster.assert_not_called()


class PersonaHinweis(unittest.TestCase):
    def test_hinweis_nur_wenn_an(self):
        import persona
        with mock.patch.object(entpacken, "modus", return_value="an"):
            text = persona._entpacken_hinweis()
        self.assertIn("entpacken", text)
        self.assertIn("quelle", text)
        with mock.patch.object(entpacken, "modus", return_value="aus"):
            self.assertEqual(persona._entpacken_hinweis(), "")


if __name__ == "__main__":
    unittest.main()
