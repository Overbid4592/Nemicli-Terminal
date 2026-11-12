"""Gesprächsabläufe mit künstlichen Antworten und Werkzeugen, ohne Modellaufruf."""

import ast
import asyncio
import itertools
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace as NS
import sys
import unittest
import unittest.mock


ROOT = Path(__file__).resolve().parents[1]
for _sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / _sub))

import beobachter      # noqa: E402  (Mitdenken: was hat sich geändert?)
import denkwerk        # noqa: E402  (Denkwerk: Hinweis und Prüfer)
import mitschrift      # noqa: E402  (F12-Mitschnitt laeuft im Ablauf mit)
import pruefer         # noqa: E402  (Mitdenken: Gegenprobe der Antwort)
import rolle           # noqa: E402  (Ich-Form: Stups bei dritter Person)
import notstopp        # noqa: E402  (Not-Stopp: ESC/STOP)
import protokoll       # noqa: E402  (Aktions-Protokoll laeuft im Ablauf mit)
import sicherheit      # noqa: E402  (Risikostufen/Whitelist/Limit entscheiden mit)
import webanzeige      # noqa: E402  (Anzeige der Web-Werkzeuge)


def extract_functions(relative, names, namespace):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name in names]
    if len(nodes) != len(names):
        raise AssertionError("Test muss die aktuellen Originalfunktionen verwenden")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), relative, "exec"), namespace)


class Backend:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.inputs = []
        self.messages = []

    async def stream(self, text, images=None):
        self.inputs.append(text)
        self.messages.append({"role": "user", "content": text})
        answer = next(self.answers)
        yield {"type": "text", "text": answer}
        self.messages.append({"role": "assistant", "content": answer})


def action(tool="befehl"):
    return '```aktion\n' + json.dumps({"tool": tool, "pfad": "synthetisch.txt"}) + '\n```'


def liste_block(punkte):
    return '```aktion\n' + json.dumps({"tool": "plan", "punkte": punkte}) + '\n```'


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, answers, result=None, approve=True, preview=None):
        self.output, self.events, self.calls, self.reviews = [], [], [], []
        self.mode_decide = lambda act, need: "ask" if need else "run"   # Modus „normal“
        self.backend = Backend(answers)
        self.preview_error = None

        @contextmanager
        def live(*args):
            yield NS(update=lambda *args: None)

        self.taste = lambda: False                 # ESC/STOP-Taste (Not-Stopp)
        notstopp._test_reset()
        self.addCleanup(notstopp._test_reset)
        self.addCleanup(setattr, notstopp, "sicherungen_seit", notstopp.sicherungen_seit)

        async def run(act):
            self.calls.append(deepcopy(act))
            if isinstance(result, BaseException):
                raise result
            return result or NS(text="synthetische Ausgabe", ok=True, returncode=0)

        async def review(title, content):
            self.assertEqual([], self.calls, "Freigabe muss vor der Ausführung liegen")
            self.reviews.append(content)
            return approve

        async def confirm(question, options, **kwargs):
            if "preview" in kwargs:
                await review(question, kwargs["preview"])
            return "yes" if approve else "no"

        def preview_for(act):
            if self.preview_error:
                raise self.preview_error
            return preview

        parser = {"json": json, "re": re,
                  "_BLOCK": re.compile(r'```(?:aktion|json)?\s*\n(\{.*?\})\s*(?:```|\Z)', re.S),
                  "_FENCE_OPEN": re.compile(r'```(?:aktion|json)\s*\n'),
                  # Backslash-Reparatur für Windows-Pfade
                  "_UNGUELTIGES_ESCAPE": re.compile(r'\\(?!["\\/bfnrtu])'),
                  "_PFAD_FELDER": ("pfad", "von", "nach", "ziel", "quelle", "path", "ordner"),
                  "_STEUER_ZURUECK": {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"},
                  "UNLESBAR_KI": "[System] unlesbar ({grund})", "MAX_JE_BLOCK": 8,
                  "_UMLAUTE": str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe",
                                             "Ü": "ue"})}
        extract_functions("tools/actions.py", {"parse_actions", "unparsed_action_note", "unlesbar_grund",
                                               "unlesbar_fuer_ki", "_json_reparieren", "_pfade_entsteuern",
                                               "_mehrere_aktionen", "_felder_ascii"}, parser)
        ui = NS(TUI=None, console=NS(print=self.output.append),
                execution_receipt=lambda rows: ("receipt", deepcopy(rows)),
                action_request=lambda desc, confirm, **kw: ("request", desc, confirm),
                action_result=lambda text, ok, sekunden=None: ("result", text, ok), anzeige_kurz=lambda text: text,
                musik_karte_aus=lambda text: False, diff_anzeige=lambda *args: None,
                new_verb_seed=lambda: 0, verb_at=lambda *args: "", live_view=live, thinking=live,
                working_meter=lambda *args: None, stream_meter=lambda *args: None,
                done_meter=lambda *args: None, warn=self.output.append, info=self.output.append,
                success=self.output.append, error=self.output.append,
                befehl_panel=lambda *args: None, recherche_live=live,
                web_laeuft=lambda info, s: ("web_laeuft", info.get("verb")),
                web_ergebnis=lambda daten, s=None: ("web", deepcopy(daten)))
        async def keine_bilder(res, model):          # Bild-Uebergabe (bild_ansehen) hier nicht Thema
            return None, res

        mitschrift.leeren()          # F12-Mitschnitt: jede Runde faengt frisch an
        # Aktions-Protokoll NICHT in die echte Datei schreiben.
        import tempfile
        self._log_tmp = tempfile.TemporaryDirectory()
        self._log_alt = protokoll.PFAD
        protokoll.PFAD = Path(self._log_tmp.name) / "aktionen.log"
        self.addCleanup(lambda: setattr(protokoll, "PFAD", self._log_alt))
        self.addCleanup(self._log_tmp.cleanup)
        # Whitelist-Datei nicht im echten Programm-Ordner anlegen.
        self._wl_alt = sicherheit.WHITELIST_DATEI
        sicherheit.WHITELIST_DATEI = Path(self._log_tmp.name) / "befehl_whitelist.json"
        sicherheit.whitelist_neu_laden()
        self.addCleanup(lambda: (setattr(sicherheit, "WHITELIST_DATEI", self._wl_alt),
                                 sicherheit.whitelist_neu_laden()))
        self.ns = dict(asyncio=asyncio, itertools=itertools, time=time, ui=ui, MAX_STEPS=4, MAX_LESE_STEPS=7,
                       Path=Path, _DIFF_WERKZEUGE=("datei_schreiben", "datei_bearbeiten"), _DIFF_MAX_BYTES=1_000_000,
                       _KOPF_IN_AUSGABE=re.compile(r"(?m)^Ergebnis von '"),
                       SERIE_MAX_STEPS=12, _TODO_OFFEN_NOTE=" [TODO OFFEN] {liste}",
                       _schritt_grenze=lambda nur_gelesen: 7 if nur_gelesen else 4,
                       AUTO_ALLOW=set(),
                       mitschrift=mitschrift, protokoll=protokoll, sicherheit=sicherheit, webanzeige=webanzeige,
                       _ABGELEHNT_TEXT=("Aktion '{tool}' wurde vom Nutzer ABGELEHNT. "
                                        "Führe sie nicht aus und frage, wie es weitergehen soll."),
                       PS=NS(active=lambda: NS(name="Test", key="test")),
                       lernschleife=NS(hinweise_abholen=lambda wer: ""), _neuigkeiten=lambda wer: "",
                       _stimme_stopp=lambda: None, _vorlesen_auto=lambda text: None,
                       _voice_an=lambda: False, _kurzfassung=lambda text: text, _LETZTE_ANTWORT=[""],
                       _bilder_aus_ergebnis=keine_bilder, _bild_vorschau=lambda *a, **k: None,
                       _DEDUPE_TOOLS={"datei_lesen"}, _STEP_LIMIT_NOTE=" [LIMIT]",
                       _HINTERGRUND_TITEL={}, _FORTSCHRITT_WERKZEUGE=("bild_malen", "bild_serie",
                                                                "kugel_anziehen", "backup_erstellen",
                                                                "video_erstellen"),
                       _STEP_LIMIT_WARN="limit",
                       _DUPLICATE_NOTE="dup", _action_key=repr,
                       # Coding-Assistent aus: Bremse und Ablauf wie ohne ihn
                       coding=NS(aktiv=lambda: False, ist_ende_ansage=lambda text: False,
                                 marke=lambda: None, pruefbefehl=lambda act: ""),
                       CODING_MAX_STEPS=80, _FRAGE_HALT_NOTE=" [FRAGE]",
                       _todo_zeigen=lambda: None, _coding_beenden=lambda: None,
                       # Todo-Liste aus: jede Aktion läuft (eigene Tests unten mit der echten Liste)
                       notstopp=notstopp, _abbruch_taste=lambda: self.taste(), _stopp_taste=lambda: False,
                       ersatzbank=NS(abholen=lambda: None, wechsel_offen=lambda: False,
                                     sperre=lambda *a, **k: False, MELDUNG="herausgefiltert"),
                       indexdb=NS(unterbrechen=lambda: None), persona=NS(runde_beginnt=lambda t: None),
                       plan=NS(runde_neu=lambda: False, marke=lambda: None, aktuell=lambda: None,
                               verwerfen=lambda: None,
                               abhaken_nach=lambda act, ok: False, text_fuer_modell=lambda: "",
                               zurueckweisen=lambda acts, frei=False: (list(acts), []),
                               sperr_text=lambda acts: "", kurzstand=lambda: "",
                               neu_seit_anzeige=lambda: False),
                       _plan_zeigen=lambda: None,
                       modes=NS(decide=lambda act, need: self.mode_decide(act, need),
                                block_message=lambda act: "blockiert",
                                READ_TOOLS={"datei_lesen"}, current=lambda: "normal"),
                       beobachter=beobachter, pruefer=pruefer, denkwerk=denkwerk, rolle=rolle, os=__import__("os"),
                       AKTIVER_CTX=None,
                       config=NS(load=lambda: self.config),
                       Group=lambda *args: args, SESSION={"tokens": 0},
                       _esc_pressed=lambda: False, _update_session_meters=lambda *args: None,
                       _render=lambda thinking, answer, *args, **kwargs: answer,
                       review_action_confirm=review, ask_confirm=confirm,
                       actions=NS(parse_actions=parser["parse_actions"],
                                  unparsed_action_note=parser["unparsed_action_note"],
                                  unlesbar_fuer_ki=parser["unlesbar_fuer_ki"],
                                  resolve_tool=lambda name: name,
                                  describe=lambda act: act["tool"],
                                  needs_confirm=lambda act: act["tool"] != "datei_lesen",
                                  confirmation_preview=preview_for, run=run,
                                  reset_taint=lambda *a: None, fremd_im_verlauf=lambda m: False,
                                  fremd_grund=lambda act: None, befehl_status=lambda: None,
                                  befehl_stoppen=lambda: None, _befehl_text=lambda act: act.get("befehl", ""),
                                  SKILL_AENDERN={"skill_schreiben", "skill_ausbessern"},
                                  ActionResult=lambda text, ok: NS(text=text, ok=ok, returncode=None)))
        self.config = {}
        beobachter.zuruecksetzen()
        self.addCleanup(beobachter.zuruecksetzen)
        extract_functions("main.py", {"_schalter", "_umgebung", "_runde_beobachten", "_werkzeug_beobachten",
                                       "_gegenprobe", "_denkwerk_start", "_denkwerk_pruefen"}, self.ns)
        extract_functions("main.py", {"_result_feedback", "_ergebnisse_verbinden", "_finish_record", "_close_records",
                                       "_befehle_vorpruefen", "_freigabe", "_buendel_freigabe", "_immer_erlaubt",
                                       "_befehl_live",
                                       "converse", "_schleife", "_antworten_mit_reserve", "_terminal_antworten", "_terminal_ausgabe", "_web_ausgabe",
                                       "_ankunft_melden", "web_converse",
                                       "_nach_notstopp", "_warten_mit_stopp", "_web_live", "_diff_pfad", "_datei_text",
                                       "_vorher_stand", "_diff_zeigen"}, self.ns)
        import llamareserve
        self.ns["llamareserve"] = llamareserve
        self.confirm = confirm

    async def run_flow(self, web):
        if web:
            await self.ns["web_converse"](self.backend, "Offline-Test", None,
                                          lambda ev: self.events.append(deepcopy(ev)), self.confirm)
            return next(ev["records"] for ev in reversed(self.events) if ev["t"] == "receipt")
        await self.ns["converse"](self.backend, "Offline-Test")
        return next(item[1] for item in reversed(self.output)
                    if isinstance(item, tuple) and item[0] == "receipt")


    # --- Not-Stopp (ESC / „STOP“) -----------------------------------------------------------------

    async def test_ankunft_von_denken_und_antwort_im_bericht(self):
        from unittest import mock
        import debugbericht
        self.setup_flow([""])
        with mock.patch.object(debugbericht, "ereignis") as ereignis:
            self.ns["_ankunft_melden"]({"denken": 4.2, "antwort": 9.0, "stuecke": 1}, 977, 12.5)
        zeile = ereignis.call_args[0][0]
        self.assertIn("Denken ab 4.2 s (1 Stücke, 977 Zeichen)", zeile)   # ein Stück = gesammelt geliefert
        self.assertIn("Antwort ab 9.0 s", zeile)
        self.assertIn("fertig nach 12.5 s", zeile)

    async def test_websuche_zeigt_karte_und_modell_bekommt_den_text(self):
        daten = {"art": "suche", "suche": "x", "treffer": [], "gesamt": 0, "vertraut": 0}
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow(['```aktion\n{"tool": "web_suche", "suche": "x"}\n```', "fertig"],
                                result=NS(text="TEXT FUERS MODELL", ok=None, returncode=None, anzeige=daten))
                self.mode_decide = lambda act, need: "run"
                await self.run_flow(web)
                if web:
                    aktion = next(ev for ev in self.events if ev["t"] == "action")
                    self.assertEqual(aktion["web"]["verb"], "sucht im Netz")
                    ergebnis = next(ev for ev in self.events if ev["t"] == "action_result")
                    self.assertEqual(ergebnis["web"], daten)
                else:
                    self.assertIn(("web", daten), self.output)
                    self.assertFalse(any(isinstance(o, tuple) and o[0] == "request" for o in self.output))
                self.assertIn("TEXT FUERS MODELL", self.backend.inputs[-1])

    async def test_gesperrte_websuche_zeigt_weiter_die_aktion(self):
        self.setup_flow(['```aktion\n{"tool": "web_suche", "suche": "x"}\n```', "fertig"])
        self.mode_decide = lambda act, need: "block"
        await self.run_flow(False)
        self.assertTrue(any(isinstance(o, tuple) and o[0] == "request" for o in self.output))
        self.assertEqual([], self.calls)

    async def test_fenster_sperre_nennt_die_freien_werkzeuge_statt_chat_modus(self):
        self.setup_flow([action("befehl"), "fertig"])
        self.mode_decide = lambda act, need: "run"
        baum = ast.parse(Path(ROOT / "main.py").read_text(encoding="utf-8"))
        for knoten in baum.body:                      # die Konstanten aus main.py, nicht nachgebaut
            if isinstance(knoten, ast.Assign) and any(getattr(z, "id", "") in
                                                      ("CHAT_WERKZEUGE", "_CHAT_GESPERRT", "_FENSTER_GESPERRT",
                                                       "APP_WERKZEUGE") for z in knoten.targets):
                exec(compile(ast.Module([knoten], []), "main.py", "exec"), self.ns)
        self.assertIn("ordner_erstellen", self.ns["APP_WERKZEUGE"])     # ➕ Neue App legt einen Ordner an
        await self.ns["web_converse"](self.backend, "Offline-Test", None,
                                      lambda ev: self.events.append(deepcopy(ev)), self.confirm,
                                      erlaubt=self.ns["APP_WERKZEUGE"])
        self.assertEqual([], self.calls)
        zurueck = self.backend.inputs[-1]
        self.assertIn("nicht freigegeben", zurueck)
        self.assertIn("datei_schreiben", zurueck)
        self.assertNotIn("Chat-Modus", zurueck)

    async def test_rauswurf_nach_dem_ersten_werkzeug_startet_nichts_mehr(self):
        zwei = action("datei_lesen") + "\n" + '```aktion\n{"tool": "datei_lesen", "pfad": "zweite.txt"}\n```'
        self.setup_flow([zwei, "sollte nie kommen"])
        self.mode_decide = lambda act, need: "run"
        self.ns["ersatzbank"].wechsel_offen = lambda: bool(self.calls)   # herausgefiltert nach dem ersten
        rows = await self.run_flow(False)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(["success", "not_run"], [r["status"] for r in rows])
        self.assertEqual(1, len(self.backend.inputs))  # Ergebnis geht nicht zurück ans Modell
        self.assertIn("herausgefiltert", self.output)

    async def test_notstopp_nach_dem_ersten_werkzeug_startet_nichts_mehr(self):
        zwei = action("datei_lesen") + "\n" + '```aktion\n{"tool": "datei_lesen", "pfad": "zweite.txt"}\n```'
        self.setup_flow([zwei, "sollte nie kommen"])
        self.mode_decide = lambda act, need: "run"
        gedrueckt = []

        def taste():                                   # ESC einmal, sobald das erste Werkzeug gelaufen ist
            if self.calls and not gedrueckt:
                gedrueckt.append(True)
                return True
            return False
        self.taste = taste
        notstopp.sicherungen_seit = lambda start: []
        rows = await self.run_flow(False)
        self.assertEqual(1, len(self.calls))           # das zweite startet nicht
        self.assertEqual(["success", "not_run"], [r["status"] for r in rows])
        self.assertEqual(1, len(self.backend.inputs))  # Ergebnis geht nicht zurück ans Modell
        self.assertTrue(any("Not-Stopp" in str(o) for o in self.output))
        # nächste Runde: Hinweis genau einmal
        self.backend = Backend(["Okay."])
        await self.ns["converse"](self.backend, "Was jetzt?")
        self.assertIn("Not-Stopp", self.backend.inputs[0])
        self.backend = Backend(["Okay."])
        await self.ns["converse"](self.backend, "Und jetzt?")
        self.assertNotIn("Not-Stopp", self.backend.inputs[0])

    async def test_stop_als_nachricht_fuehrt_nichts_aus(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow(["Ich höre auf. " + action("befehl")])
                self.mode_decide = lambda act, need: "run"
                if web:
                    await self.ns["web_converse"](self.backend, "STOP!", None,
                                                  lambda ev: self.events.append(deepcopy(ev)), self.confirm)
                else:
                    await self.ns["converse"](self.backend, "STOP!")
                self.assertEqual([], self.calls)
                self.assertIn("keine Werkzeuge", self.backend.inputs[0])

    async def test_notstopp_bietet_gesicherte_aenderungen_zum_zurueckholen(self):
        self.setup_flow([action("datei_schreiben"), "nie"])
        self.mode_decide = lambda act, need: "run"
        self.taste = lambda: bool(self.calls)
        geholt, gefragt = [], []
        notstopp.sicherungen_seit = lambda start: [{"werkzeug": "datei_schreiben", "original": "C:/x/a.py",
                                                    "kopie": "C:/p/a.py", "zeit": ""}]
        self.ns["snapshot"] = NS(wiederherstellen=lambda e: geholt.append(e["original"]) or e["original"])

        async def frage(text, optionen, **kw):
            gefragt.append(text)
            return "ja"
        self.ns["ask_confirm"] = frage
        await self.run_flow(False)
        self.assertTrue(gefragt and "a.py" in gefragt[0])     # erst fragen …
        self.assertEqual(["C:/x/a.py"], geholt)                # … dann zurückholen
        self.assertTrue(any("datei_schreiben" in str(o) for o in self.output))   # Änderung gemeldet

    def test_stop_erkennung(self):
        for ja in ("STOP", "stopp!", "Stop.", "abbrechen", "Hör auf", "hoer auf", "Halt"):
            self.assertTrue(notstopp.ist_stop(ja), ja)
        for nein in ("stop the music", "Stoppuhr bauen", "halt mal kurz die Datei offen", ""):
            self.assertFalse(notstopp.ist_stop(nein), nein)

    async def test_f12_mitschnitt_faengt_frage_antwort_und_aktion(self):
        """F12 soll das GANZE Gespraech sichern koennen - also muss der
        Mitschnitt schon waehrend des Ablaufs gefuellt werden, nicht erst
        hinterher (der Denktext existiert danach nicht mehr)."""
        self.setup_flow([action(), "Fertig."], NS(text="ausgefuehrt", ok=True, returncode=0))
        await self.run_flow(False)
        arten = [e["art"] for e in mitschrift._eintraege]
        self.assertEqual(arten[0], "nutzer")
        self.assertIn("aktion", arten)
        self.assertIn("assistent", arten)
        frage = mitschrift._eintraege[0]["text"]
        self.assertEqual(frage, "Offline-Test")
        getan = next(e for e in mitschrift._eintraege if e["art"] == "aktion")
        self.assertEqual(getan["werkzeug"], "befehl")
        self.assertTrue(getan["ok"])
        self.assertIn("ausgefuehrt", getan["ergebnis"])
        # ... und im Markdown steht es dann auch wirklich drin.
        md = mitschrift.als_markdown({"chat": 1})
        self.assertIn("Offline-Test", md)
        self.assertIn("`befehl`", md)

    async def test_abgelehnte_aktion_steht_als_abgelehnt_im_mitschnitt(self):
        self.setup_flow([action(), "Na gut."], NS(text="egal", ok=True, returncode=0),
                        approve=False)
        await self.run_flow(False)
        getan = [e for e in mitschrift._eintraege if e["art"] == "aktion"]
        self.assertTrue(getan)
        self.assertFalse(getan[0]["ok"])
        self.assertIn("abgelehnt", getan[0]["ergebnis"].lower())

    async def test_beobachter_meldet_aenderung_in_der_naechsten_runde(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / "notiz.txt"
            datei.write_text("alt\n", encoding="utf-8")
            lesen = '```aktion\n' + json.dumps({"tool": "datei_lesen", "pfad": str(datei)}) + '\n```'
            for web in (False, True):
                with self.subTest(web=web):
                    self.setup_flow([lesen, "Gelesen."])
                    self.mode_decide = lambda act, need: "run"
                    await self.run_flow(web)
                    st = datei.stat().st_mtime_ns
                    datei.write_text("neu\n", encoding="utf-8")
                    os.utime(datei, ns=(st + 2_000_000_000, st + 2_000_000_000))
                    self.backend = Backend(["Oh, die Datei ist anders."])
                    if web:
                        await self.ns["web_converse"](self.backend, "Und?", None,
                                                      lambda ev: self.events.append(deepcopy(ev)), self.confirm)
                        self.assertTrue(any("🔄" in str(ev.get("text", "")) for ev in self.events))
                    else:
                        await self.ns["converse"](self.backend, "Und?")
                        self.assertTrue(any("🔄 Seit dem letzten Blick: notiz.txt geändert" in str(o)
                                            for o in self.output))
                    self.assertIn("+ 1: neu", self.backend.inputs[0])
                    self.config = {"beobachter": False}                       # abschaltbar
                    datei.write_text("noch neuer\n", encoding="utf-8")
                    os.utime(datei, ns=(st + 4_000_000_000, st + 4_000_000_000))
                    self.backend = Backend(["Ok."])
                    await self.ns["converse"](self.backend, "Und jetzt?")
                    self.assertNotIn("🔄", self.backend.inputs[0])

    async def test_unlesbarer_block_geht_mit_grund_an_die_ki_zurueck(self):
        kaputt = 'Ich schau nach.\n```aktion\n{"tool": "befehl", "pfad": "x"}\n{"befehl": "dir"}\n```'
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([kaputt, action(), "Fertig."])
                rows = await self.run_flow(web)
                self.assertEqual([{"tool": "befehl", "status": "success"}], rows)   # beim zweiten Mal lesbar
                self.assertIn("[System] unlesbar (mehrere JSON-Objekte", self.backend.inputs[1])
                self.assertEqual(1, len(self.calls))

    async def test_unlesbarer_block_nur_einmal_nachgefragt(self):
        kaputt = '```aktion\n{"tool": "befehl", "pfad": "abgeschn'
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([kaputt, kaputt, "Ging leider nicht."])
                self.assertEqual([], await self.run_flow(web))
                self.assertEqual(2, len(self.backend.inputs))                       # kein dritter Anlauf
                self.assertIn("nicht geschlossen", self.backend.inputs[1])
                self.assertEqual([], self.calls)

    async def test_text_claim_has_no_execution_receipt(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow(["Ich habe die Datei geschrieben.", "Stimmt nicht – ich habe nichts geschrieben."])
                self.assertEqual([], await self.run_flow(web))
                self.assertEqual([], self.calls)
                self.assertIn("Gegenprobe", self.backend.inputs[1])        # Behauptung ohne Werkzeug fällt auf
                self.assertIn("kein Werkzeug", self.backend.inputs[1])

    # --- Wort halten ------------------------------------------------------------------------------

    async def test_angekuendigt_ohne_aufruf_bekommt_einen_stups(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow(["Klar! Ich male jetzt das Bild für dich! 🎨✨", action(), "Fertig."])
                rows = await self.run_flow(web)
                self.assertEqual([{"tool": "befehl", "status": "success"}], rows)
                self.assertTrue(self.backend.inputs[1].startswith("[System] Du hast eben angekündigt"))
                self.assertIn("Ich male jetzt das Bild für dich!", self.backend.inputs[1])

    async def test_stups_nur_einmal_und_nicht_bei_einer_frage(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow(["Ich starte jetzt die Malerei!", "Ich werde das jetzt sofort machen."])
                self.assertEqual([], await self.run_flow(web))
                self.assertEqual(2, len(self.backend.inputs))
                self.setup_flow(["Ich male dir gleich eins – lieber rot oder blau? 😊"])
                await self.run_flow(web)
                self.assertEqual(1, len(self.backend.inputs))

    async def test_dritte_person_bekommt_einmal_einen_rollen_stups(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow(["*Test lächelt* Test findet das schön.", "*lächelt* Ich finde das schön."])
                self.assertEqual([], await self.run_flow(web))
                self.assertEqual(2, len(self.backend.inputs))
                self.assertTrue(self.backend.inputs[1].startswith("🎭 [Rolle]"))
                self.setup_flow(["*Test lächelt*", "*Test lächelt* immer noch."])
                await self.run_flow(web)
                self.assertEqual(2, len(self.backend.inputs))             # höchstens einmal je Runde

    # --- Denkwerk ----------------------------------------------------------------------------------

    def _denk_backend(self, pruefer_sagt):
        be = self.backend
        be.denk_hinweis, be.gesehen, be.pruefungen = "", [], []

        async def stream(text, images=None, _alt=be.stream):
            be.gesehen.append(be.denk_hinweis)
            be.denk_hinweis = ""
            async for ev in _alt(text, images):
                yield ev

        async def ask_once(prompt, system):
            be.pruefungen.append(prompt)
            return pruefer_sagt
        be.stream, be.ask_once = stream, ask_once
        return be

    async def _denk_runde(self, web, text):
        if web:
            await self.ns["web_converse"](self.backend, text, None,
                                          lambda ev: self.events.append(deepcopy(ev)), self.confirm)
            return [ev["text"] for ev in self.events if ev["t"] == "note"]
        await self.ns["converse"](self.backend, text)
        return [x for x in self.output if isinstance(x, str)]

    async def test_denkwerk_pruefer_findet_fehler_und_ki_bessert_nach(self):
        lang = "Rechnung: 17 * 23 = 381, denn " + "so geht der Rechenweg Schritt für Schritt. " * 5
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([lang, "Korrigiert: 17 * 23 = 391."])
                be = self._denk_backend("FEHLER:\n- 17 * 23 ist 391, nicht 381")
                with unittest.mock.patch.object(denkwerk, "an", return_value=True):
                    zeilen = await self._denk_runde(web, "Berechne 17*23 und erkläre den Rechenweg")
                self.assertIn("Lösungswege", be.gesehen[0])                # schwere Frage: Hinweis beim ersten Senden
                self.assertEqual("", be.gesehen[1])                       # das Nachbessern trägt keinen Hinweis
                self.assertIn("17*23", be.pruefungen[0])
                self.assertTrue(be.inputs[1].startswith("[System] ⚖️"))
                self.assertIn("391, nicht 381", be.inputs[1])
                self.assertEqual(1, len(be.pruefungen))                   # höchstens eine Prüfung je Runde
                self.assertTrue(any("⚖️ Prüfer: 17 * 23 ist 391" in z for z in zeilen))
                self.assertEqual("", be.denk_hinweis)

    async def test_denkwerk_pruefer_ok_und_einfache_fragen_ohne_pruefung(self):
        lang = "Die Antwort ist ausführlich begründet. " * 6
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([lang, lang])
                be = self._denk_backend("OK")
                with unittest.mock.patch.object(denkwerk, "an", return_value=True):
                    zeilen = await self._denk_runde(web, "Analysiere die Strategie und vergleiche sie genau")
                    self.assertEqual(1, len(be.inputs))
                    self.assertTrue(any("⚖️ Prüfer: in Ordnung" in z for z in zeilen))
                    await self._denk_runde(web, "Hallo du")
                self.assertEqual(1, len(be.pruefungen))
                self.assertEqual("", be.gesehen[1])                       # einfache Frage: kein Hinweis

    async def test_denkwerk_pruefer_nicht_nach_werkzeugen(self):
        lang = "Laut der Ausgabe ist alles in Ordnung, hier die genaue Begründung. " * 4
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action(), lang])
                be = self._denk_backend("FEHLER: erfunden")
                with unittest.mock.patch.object(denkwerk, "an", return_value=True):
                    await self._denk_runde(web, "Analysiere die Strategie und vergleiche sie genau")
                self.assertEqual([], be.pruefungen)                       # Ergebnisse sieht der Prüfer nicht
                self.assertEqual(2, len(be.inputs))

    async def test_result_status_and_feedback_do_not_depend_on_output_words(self):
        for web in (False, True):
            for ok, status, text in ((False, "failed", "Access is denied."),
                                     (True, "success", "Fehlerstatistik: 0 Fehler"),
                                     (None, "unverified", "Eine externe Textantwort")):
                with self.subTest(web=web, ok=ok):
                    self.setup_flow([action(), "Fertig.", "Nein, der Befehl ist fehlgeschlagen."],
                                    NS(text=text, ok=ok, returncode=7))
                    rows = await self.run_flow(web)
                    self.assertEqual([{"tool": "befehl", "status": status}], rows)
                    gegenprobe = len(self.backend.inputs) == 3
                    self.assertEqual(ok is False, gegenprobe)               # „Fertig.“ nach Fehlschlag
                    if gegenprobe:
                        self.assertIn("`befehl` fehlgeschlagen", self.backend.inputs[2])
                    self.assertIn("Exitcode 7", self.backend.inputs[1])
                    self.assertIn(text, self.backend.inputs[1])
                    if web:
                        shown = next(ev for ev in self.events if ev["t"] == "action_result")
                        self.assertIs(ok, shown["ok"])
                    else:
                        shown = next(item for item in self.output if isinstance(item, tuple)
                                     and item[0] == "result")
                        self.assertIs(ok, shown[2])

    async def test_full_content_approved_before_execution(self):
        content = "Ziel: synthetisch.txt\n" + "Zeile\n" * 800 + "LETZTE ZEILE"
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Fertig."], preview=content)
                rows = await self.run_flow(web)
                self.assertEqual([content], self.reviews)
                self.assertEqual(1, len(self.calls))
                self.assertEqual("success", rows[0]["status"])

    async def test_rejected_preview_does_not_execute(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Abgelehnt."], approve=False,
                                preview="Vollständiger Testinhalt")
                rows = await self.run_flow(web)
                self.assertEqual([], self.calls)
                self.assertEqual("rejected", rows[0]["status"])

    async def test_preview_failure_does_not_offer_blind_approval(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_bearbeiten"), "Quelle nicht lesbar."])
                self.preview_error = OSError("Quelle nicht lesbar")
                rows = await self.run_flow(web)
                self.assertEqual([], self.calls)
                self.assertEqual([], self.reviews)
                self.assertEqual("not_run", rows[0]["status"])

    async def test_cancelled_action_never_gets_success_receipt(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action()], result=asyncio.CancelledError())
                with self.assertRaises(asyncio.CancelledError):
                    await self.run_flow(web)
                rows = (self.events[-1]["records"] if web else self.output[-1][1])
                self.assertEqual("unverified", rows[0]["status"])

    async def test_duplicate_read_action_is_skipped(self):
        """Zweites datei_lesen mit identischen Feldern läuft nicht nochmal."""
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_lesen"), action("datei_lesen"), "Fertig."])
                rows = await self.run_flow(web)
                self.assertEqual(1, len(self.calls))                       # nur einmal ausgeführt
                self.assertEqual([{"tool": "datei_lesen", "status": "success"},
                                  {"tool": "datei_lesen", "status": "not_run"}], rows)
                self.assertIn("dup", self.backend.inputs[2])               # Hinweis geht ans Modell

    async def test_step_limit_gets_summary_round_without_execution(self):
        """Nach MAX_STEPS Aktionen: eine Extra-Runde nur zum Zusammenfassen; eine dort
        geplante Aktion wird NICHT ausgeführt, die Bremse wird gemeldet."""
        for web in (False, True):
            with self.subTest(web=web):
                answers = [action("befehl")] * 4 + [action("befehl")]      # 4 = MAX_STEPS im Test
                self.setup_flow(answers)
                rows = await self.run_flow(web)
                self.assertEqual(4, len(self.calls))
                self.assertIn("[LIMIT]", self.backend.inputs[4])           # Hinweis in der Extra-Runde
                self.assertEqual("not_run", rows[-1]["status"])
                if web:
                    self.assertTrue(any(ev.get("t") == "warn" and ev["text"] == "limit" for ev in self.events))
                else:
                    self.assertIn("limit", self.output)

    def coding_an(self, fortschritt: bool, gesamt: int = 80):
        """Coding-Assistent an; fortschritt=True: jede Runde ändert die Todo-Marke."""
        zaehler = iter(range(10_000))
        self.ns["coding"] = NS(aktiv=lambda: True, ist_ende_ansage=lambda text: False,
                               marke=(lambda: next(zaehler)) if fortschritt else (lambda: ("fest",)),
                               pruefbefehl=lambda act: "")
        self.ns["CODING_MAX_STEPS"] = gesamt

    async def test_coding_fortschritt_setzt_bremse_zurueck(self):
        """Im Coding-Assistenten zählt die Bremse pro Todo-Punkt, nicht pro Nachricht."""
        self.setup_flow([action("befehl")] * 10 + ["fertig"])
        self.coding_an(fortschritt=True)
        await self.run_flow(False)
        self.assertEqual(10, len(self.calls))                          # mehr als MAX_STEPS=4
        self.assertNotIn("limit", self.output)

    async def test_coding_ohne_fortschritt_bremst_wie_immer(self):
        self.setup_flow([action("befehl")] * 5)
        self.coding_an(fortschritt=False)
        await self.run_flow(False)
        self.assertEqual(4, len(self.calls))
        self.assertIn("limit", self.output)

    async def test_coding_gesamtgrenze_gilt(self):
        self.setup_flow([action("befehl")] * 8)
        self.coding_an(fortschritt=True, gesamt=6)
        await self.run_flow(False)
        self.assertEqual(6, len(self.calls))
        self.assertIn("[LIMIT]", self.backend.inputs[6])

    async def test_coding_frage_haelt_die_schleife_an(self):
        """Nach todo frage: nur noch die Frage an den Nutzer, keine weitere Aktion."""
        frage = '```aktion\n' + json.dumps({"tool": "todo", "aktion": "frage", "nr": 1,
                                            "frage": "Welche Version?"}) + '\n```'
        for web in (False, True):                  # eine Schleife: das Fenster hält genauso an
            with self.subTest(web=web):
                self.setup_flow([frage, action("befehl")])
                self.coding_an(fortschritt=False)
                rows = await self.run_flow(web)
                self.assertEqual(["todo"], [c["tool"] for c in self.calls])
                self.assertIn("[FRAGE]", self.backend.inputs[1])
                self.assertNotIn("[LIMIT]", self.backend.inputs[1])
                self.assertEqual("not_run", rows[-1]["status"])
                gemeldet = [ev.get("text", "") for ev in self.events] if web else self.output
                self.assertTrue(any("offene Frage" in str(o) for o in gemeldet))

    async def test_fenster_bremse_beginnt_bei_fortschritt_neu(self):
        """Früher fehlte das im Fenster: auch dort zählt die Bremse je Todo-Punkt."""
        self.setup_flow([action("befehl")] * 10 + ["fertig"])
        self.coding_an(fortschritt=True)
        await self.run_flow(True)
        self.assertEqual(10, len(self.calls))
        self.assertFalse(any(ev.get("t") == "warn" and ev["text"] == "limit" for ev in self.events))

    async def test_read_only_chain_gets_longer_leash(self):
        """Nur Lese-Aktionen: die längere Grenze gilt (7 im Test statt 4). Sobald eine
        ändernde Aktion dabei war, zählt wieder die kurze Bremse."""
        for web in (False, True):
            with self.subTest(web=web):
                lese = [action("datei_lesen").replace("synthetisch.txt", f"d{i}.txt") for i in range(7)]
                self.setup_flow(lese + [action("datei_lesen").replace("synthetisch.txt", "d9.txt")])
                rows = await self.run_flow(web)
                self.assertEqual(7, len(self.calls))                       # 7 gelesen, die 8. nicht mehr
                self.assertEqual("not_run", rows[-1]["status"])
            with self.subTest(web=web, gemischt=True):
                lese = [action("datei_lesen").replace("synthetisch.txt", f"e{i}.txt") for i in range(3)]
                self.setup_flow([action("befehl")] + lese + [action("befehl")])
                rows = await self.run_flow(web)
                self.assertEqual(4, len(self.calls))                       # kurze Bremse: 1 + 3, die 5. nicht
                self.assertEqual("not_run", rows[-1]["status"])

    async def test_step_limit_summary_answer_ends_cleanly(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("befehl")] * 4 + ["Zwischenstand: …"])
                rows = await self.run_flow(web)
                self.assertEqual(4, len(self.calls))
                self.assertEqual(4, len(rows))
                self.assertTrue(all(r["status"] == "success" for r in rows))

    async def test_read_only_mode_blocks_without_asking(self):
        """Modus 👁 Nur Lesen: verändernde Aktion wird weder gefragt noch ausgeführt."""
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Verstanden, nur Vorschlag."])
                self.mode_decide = lambda act, need: "block" if need else "run"
                rows = await self.run_flow(web)
                self.assertEqual([], self.calls)
                self.assertEqual([], self.reviews)                       # keine Rückfrage
                self.assertEqual([{"tool": "datei_schreiben", "status": "not_run"}], rows)
                self.assertIn("blockiert", self.backend.inputs[1])       # Modell erfährt es

    async def test_auto_mode_runs_without_asking(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action("datei_schreiben"), "Fertig."], preview="Inhalt")
                self.mode_decide = lambda act, need: "run"
                rows = await self.run_flow(web)
                self.assertEqual(1, len(self.calls))
                self.assertEqual([], self.reviews)
                self.assertEqual("success", rows[0]["status"])


    def echte_liste(self):
        """Echte Todo-Liste im Ablauf; das Werkzeug `plan` läuft wirklich."""
        import plan
        plan.verwerfen()
        self.addCleanup(plan.verwerfen)
        self.ns["plan"] = plan
        lauf = self.ns["actions"].run

        async def run(act):
            if act.get("tool") == "plan":
                text, ok = plan.ausfuehren(act)
                return NS(text=text, ok=ok, returncode=None)
            return await lauf(act)
        self.ns["actions"].run = run
        return plan

    async def test_einzelne_aktion_braucht_keine_liste(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action(), "Gut."])
                self.echte_liste()
                rows = await self.run_flow(web)
                self.assertEqual(["befehl"], [c["tool"] for c in self.calls])
                self.assertEqual("success", rows[-1]["status"])

    async def test_zweiter_schritt_ohne_liste_laeuft_nicht(self):
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([action(), action(), "Gut."])
                self.echte_liste()
                rows = await self.run_flow(web)
                self.assertEqual(["befehl"], [c["tool"] for c in self.calls])
                self.assertEqual({"tool": "befehl", "status": "not_run"}, rows[-1])
                self.assertIn("Todo-Liste", self.backend.inputs[2])

    async def test_liste_und_aktion_im_selben_zug(self):
        liste = liste_block(["Befehl ausführen"]) + "\n"
        for web in (False, True):
            with self.subTest(web=web):
                self.setup_flow([liste + action(), "Fertig."])
                plan = self.echte_liste()
                rows = await self.run_flow(web)
                self.assertEqual(["befehl"], [c["tool"] for c in self.calls if c["tool"] != "plan"])
                self.assertEqual("success", rows[-1]["status"])
                self.assertIn("jetzt 1. Befehl ausführen", self.backend.inputs[1])
                self.assertTrue(plan.aktiv())

    async def test_fertige_liste_gilt_nur_eine_runde(self):
        fertig = '```aktion\n' + json.dumps({"tool": "plan", "punkte": ["A"], "erledigt": 1}) + '\n```'
        self.setup_flow([fertig, "Erledigt.", action(), action(), "Gut."])
        self.echte_liste()
        await self.run_flow(False)
        await self.run_flow(False)                      # ohne neue Liste: nur ein Schritt
        self.assertEqual(["befehl"], [c["tool"] for c in self.calls if c["tool"] != "plan"])

    async def test_offene_liste_laeuft_in_der_naechsten_runde_weiter(self):
        self.setup_flow([liste_block(["A", "B"]), "Liste steht.", action(), "Gut."])
        self.echte_liste()
        await self.run_flow(False)
        await self.run_flow(False)                      # „weiter“: ohne neue Liste
        self.assertEqual(["befehl"], [c["tool"] for c in self.calls if c["tool"] != "plan"])
        self.assertIn("[TODO OFFEN]", self.backend.inputs[2])
        self.assertIn("1. A", self.backend.inputs[2])

    async def test_abhaken_an_der_aktion_greift_sofort(self):
        haken = '```aktion\n' + json.dumps({"tool": "befehl", "pfad": "synthetisch.txt", "abhaken": 1}) + '\n```'
        self.setup_flow([liste_block(["Befehl", "Rest"]) + "\n" + haken, "Fertig."])
        plan = self.echte_liste()
        await self.run_flow(False)
        self.assertEqual("erledigt", plan.daten()["punkte"][0]["status"])
        self.assertIn("jetzt 2. Rest", self.backend.inputs[1])

    async def test_lange_serie_mit_liste_ueber_die_lesegrenze(self):
        schritte = [action() for _ in range(10)]
        self.setup_flow([liste_block([f"Bild {i}" for i in range(1, 11)]) + "\n" + schritte[0]]
                        + ['```aktion\n' + json.dumps({"tool": "befehl", "pfad": "s.txt", "abhaken": i}) + '\n```'
                           for i in range(1, 10)] + ["Alle fertig."])
        self.echte_liste()
        await self.run_flow(False)
        self.assertEqual(10, len([c for c in self.calls if c["tool"] == "befehl"]))   # MAX_LESE_STEPS=7


if __name__ == "__main__":
    unittest.main()
