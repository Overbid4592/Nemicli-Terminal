"""Wer durch /einrichten führt: Gemma-Helfer nur mit ausgeliefertem LoRA, sonst der klassische Ablauf.
_helfer_waehlen und run_wizard aus main_dienste.py mit gespielten Menüs. Ohne Modell und ohne Netz."""
import ast
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools", "ui", "tests"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import main_quelle                                     # noqa: E402
from einrichtungshelfer import gemmahelfer as GH       # noqa: E402


class _Klassisch(Exception):
    """Der klassische Ablauf hat begonnen (wizard.check_all wurde gerufen)."""


def _funktionen(*namen: str, **ns) -> dict:
    knoten = [n for n in ast.parse(main_quelle.text()).body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in namen]
    ns.setdefault("asyncio", asyncio)
    exec(compile(ast.Module(body=knoten, type_ignores=[]), "main.py", "exec"), ns)
    return ns


def _waehlen(angebot, wahlen, torch=True, grundmodell=None, laden_ok=True):
    protokoll = {"fragen": [], "geladen": [], "config": None, "infos": []}
    wahlen = list(wahlen)

    async def ask_confirm(frage, optionen):
        protokoll["fragen"].append([w for w, _ in optionen])
        return wahlen.pop(0)

    async def teil_laden(t):
        protokoll["geladen"].append(t.id)
        return laden_ok

    ns = _funktionen("_helfer_waehlen", ask_confirm=ask_confirm, _modell_teil_laden=teil_laden,
                     ui=NS(info=protokoll["infos"].append), _HELFER_TEXT={}, _einrichtung_melden=lambda t: None)
    with mock.patch.object(GH, "angeboten", lambda: angebot), \
            mock.patch.object(GH, "grundmodell", lambda w: grundmodell), \
            mock.patch.dict(sys.modules, {"extlibs": NS(enable=lambda: {"aktiv": torch}),
                                          "config": NS(update=lambda **k: protokoll.__setitem__("config", k))}):
        ergebnis = asyncio.run(ns["_helfer_waehlen"]())
    return ergebnis, protokoll


def _wizard(angebot, wahl, bauen=None):
    """run_wizard bis zur Entscheidung: Rückgabe ('gespraech', helfer), ('klassisch', warnungen) oder ('ende', [])."""
    warnungen = []

    async def helfer_waehlen():
        return wahl

    async def run_with_status(_titel, fn):
        return fn(None)

    async def helfer_einrichten(helfer):
        return ["gespraech", helfer]

    def check_all():
        raise _Klassisch

    ns = _funktionen("run_wizard", _helfer_waehlen=helfer_waehlen, _run_with_status=run_with_status,
                     _helfer_einrichten=helfer_einrichten, ui=NS(info=lambda t: None, warn=warnungen.append))
    with mock.patch.object(GH, "angeboten", lambda: angebot), \
            mock.patch.object(GH, "helfer_bauen", bauen or (lambda w: f"helfer:{w}")), \
            mock.patch.dict(sys.modules, {"wizard": NS(check_all=check_all)}):
        try:
            ergebnis = asyncio.run(ns["run_wizard"]())
        except _Klassisch:
            return "klassisch", warnungen
    return (tuple(ergebnis) if ergebnis else ("ende", []))


class HelferWahlTests(unittest.TestCase):
    def test_nichts_angeboten_klassisch(self):
        ergebnis, p = _waehlen([], [])
        self.assertEqual("", ergebnis)
        self.assertEqual([], p["fragen"])

    def test_gemma_wird_geladen_und_gemerkt(self):
        ergebnis, p = _waehlen(["gemma4-e4b"], ["gemma4-e4b", "ja"])
        self.assertEqual("gemma4-e4b", ergebnis)
        self.assertEqual(["gemma4-e4b"], p["geladen"])
        self.assertEqual({"einrichtungs_helfer": "gemma4-e4b"}, p["config"])
        self.assertTrue(any("Apache 2.0" in i for i in p["infos"]))           # Lizenz vor dem Laden

    def test_ohne_torch_klassisch(self):
        ergebnis, p = _waehlen(["gemma4-e2b"], ["gemma4-e2b"], torch=False)
        self.assertEqual("", ergebnis)
        self.assertEqual([], p["geladen"])
        self.assertIsNone(p["config"])

    def test_laden_abgelehnt_oder_gescheitert_klassisch(self):
        self.assertEqual("", _waehlen(["gemma4-e2b"], ["gemma4-e2b", "nein"])[0])
        self.assertEqual("", _waehlen(["gemma4-e2b"], ["gemma4-e2b", "ja"], laden_ok=False)[0])

    def test_schon_da_laedt_nicht(self):
        ergebnis, p = _waehlen(["gemma4-e4b"], ["gemma4-e4b"], grundmodell=Path("da.gguf"))
        self.assertEqual("gemma4-e4b", ergebnis)
        self.assertEqual([], p["geladen"])

    def test_abbrechen(self):
        ergebnis, p = _waehlen(["gemma4-e2b", "gemma4-e4b"], ["__abbruch__"])
        self.assertIsNone(ergebnis)
        self.assertEqual([["gemma4-e2b", "gemma4-e4b", "__ohne__", "__abbruch__"]], p["fragen"])

    def test_ohne_ki_helfer_gewaehlt(self):
        ergebnis, p = _waehlen(["gemma4-e2b"], ["__ohne__"])
        self.assertEqual("", ergebnis)
        self.assertEqual([], p["geladen"])
        self.assertIsNone(p["config"])


class RunWizardTests(unittest.TestCase):
    def test_nichts_angeboten_klassisch(self):
        self.assertEqual("klassisch", _wizard([], "gemma4-e2b")[0])

    def test_abgebrochen_endet(self):
        self.assertEqual(("ende", []), _wizard(["gemma4-e2b"], None))

    def test_ohne_ki_klassisch(self):
        self.assertEqual(("klassisch", []), _wizard(["gemma4-e2b"], ""))

    def test_gemma_fuehrt_das_gespraech(self):
        self.assertEqual(("gespraech", "helfer:gemma4-e4b"), _wizard(["gemma4-e4b"], "gemma4-e4b"))

    def test_ladefehler_warnt_und_laeuft_klassisch(self):
        def kaputt(_w):
            raise RuntimeError("LoRA fehlt")
        art, warnungen = _wizard(["gemma4-e2b"], "gemma4-e2b", bauen=kaputt)
        self.assertEqual("klassisch", art)
        self.assertTrue(any("LoRA fehlt" in w for w in warnungen))


if __name__ == "__main__":
    unittest.main()
