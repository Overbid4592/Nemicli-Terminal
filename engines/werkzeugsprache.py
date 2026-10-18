"""Werkzeuge in der Muttersprache des Modells (lokaler GGUF-Motor).

NemiCLI spricht intern ```aktion-Blöcke (JSON mit "tool"), Ergebnisse kommen als
„Ergebnis von '…'“-Nachricht. Viele Modelle sind aber auf ein eigenes Werkzeug-Format
trainiert, das in ihrer Chat-Vorlage steht. Ein Dialekt übersetzt an den Rändern:

* Systemprompt: Werkzeugliste wie in der Vorlage, Beispiel-Blöcke in der Muttersprache
* frühere Antworten (als Text): ```aktion → Aufruf in der Muttersprache
* Werkzeug-Ergebnisse: in die Antwort-Hülle der Vorlage
* erzeugter Text: Aufruf in der Muttersprache → ```aktion (für den Rest von NemiCLI)

Die Formate selbst (Marken, Reihenfolge, Hüllen, Erkennungsmerkmale) stehen in der Modell-Datenbank
(ggufengine/daten/werkzeugformate.json); hier stehen nur die Bausteine, die rechnen müssen:
Werkzeuglisten (JSON-Zeilen, Gemma-Deklarationen, JSON-Liste), Werte (Text, Gemma, JSON) und Leser.

Was zum Modell geht, sind Teile (Text, besonders): Die Marken des Formats (<tool_call>, <|"|> …)
werden zu Sonder-Token – aber nur die, die hier gesetzt werden. Werkzeug-Ergebnisse, Webseiten,
Dateien und Werte der Aufrufe bleiben immer reiner Text und können nie zu einer Marke werden.

Die Vorlage wird nie ausgeführt, nur nach Merkmalen erkannt.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from ggufengine import ausfuellen

Teile = list[tuple[str, bool]]

_AKTION = re.compile(r"```(?:aktion|json)?[ \t]*\n(\{.*?\})\s*```", re.DOTALL)
_ERGEBNIS = re.compile(r"(?m)^(?=Ergebnis von ')")
_WERKZEUG = re.compile(r"^Ergebnis von '(\w+)'")
_PLATZ = re.compile(r"%\{(\w+)\}")              # Platzhalter in den Vorlagen der Datenbank
_JSON_WERT = re.compile(r"^(?:[\[{].*|true|false|null|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)$", re.DOTALL)


def als_text(teile: Teile) -> str:
    return "".join(t for t, _ in teile)


def _zerlegen(text: str, marken: tuple[str, ...]) -> Teile:
    """Selbst gebauter Text (nie fremder Inhalt): jede Marke wird ein besonderes Teil."""
    if not marken:
        return [(text, False)]
    muster = re.compile("(" + "|".join(re.escape(m) for m in sorted(marken, key=len, reverse=True)) + ")")
    return [(t, t in marken) for t in muster.split(text) if t]


def _verschmelzen(teile: Teile) -> Teile:
    """Benachbarte Text-Teile zu einem (so wird Text am Stück kodiert wie in der Vorlage), leere weg."""
    raus: Teile = []
    for t, besonders in teile:
        if not t:
            continue
        if raus and not besonders and not raus[-1][1]:
            raus[-1] = (raus[-1][0] + t, False)
        else:
            raus.append((t, besonders))
    return raus


def _wert_text(wert) -> str:
    """Wie die Qwen-Vorlage: Listen und Objekte als JSON, alles andere als Text."""
    if isinstance(wert, (dict, list)):
        return json.dumps(wert, ensure_ascii=False)
    if isinstance(wert, bool):
        return "true" if wert else "false"
    return "" if wert is None else str(wert)


def _wert_lesen(text: str):
    """Gegenstück: JSON-artige Werte (Zahlen, true/false, Listen, Objekte) zurück, sonst Text."""
    if _JSON_WERT.match(text.strip()):
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
    return text


class Dialekt:
    """Ein Werkzeug-Format aus der Modell-Datenbank. `anfang`/`ende`: Marken eines Aufrufs im erzeugten
    Text (ohne Ende-Marke endet der Aufruf mit seinem JSON); `marken`: die Sonder-Token des Formats."""
    LISTEN = ("json_zeilen", "gemma", "json_liste")
    ORTE = ("vor_text", "nach_text", "danach")
    WERTE = ("text", "gemma", "json_ganz")
    LESER = ("xml", "gemma", "json")

    def __init__(self, eintrag: dict):
        try:
            self.e = eintrag
            self.name = eintrag["name"]
            self.anfang = eintrag["anfang"]
            self.ende = eintrag.get("ende", "")
            self.marken = tuple(eintrag["marken"])
            self.antwort_im_zug = bool(eintrag.get("antwort_im_zug"))
            self.hinweise_in_antwort = bool(eintrag.get("hinweise_in_antwort"))
            self.zwischen_antworten = eintrag["antwort"].get("zwischen", "")
            liste, aufruf, lesen = eintrag["liste"], eintrag["aufruf"], eintrag["lesen"]
            ok = (liste["art"] in self.LISTEN and liste["ort"] in self.ORTE and aufruf["werte"] in self.WERTE
                  and lesen["art"] in self.LESER and self.anfang and (self.ende or lesen["art"] == "json")
                  and "%{ergebnis}" in eintrag["antwort"]["vorlage"] and "%{liste}" in liste["vorlage"]
                  and (aufruf["werte"] == "json_ganz" or "feld" in aufruf)
                  and ("kopf" in lesen if lesen["art"] == "json" else "aufruf" in lesen)
                  and (lesen["art"] != "xml" or "feld" in lesen))
            self._aufruf_re = re.compile(lesen["aufruf"], re.DOTALL) if "aufruf" in lesen else None
            self._feld_re = re.compile(lesen["feld"], re.DOTALL) if "feld" in lesen else None
            self._kopf_re = re.compile(lesen["kopf"]) if "kopf" in lesen else None
        except (KeyError, TypeError, re.error) as exc:
            raise ausfuellen.DbFehler(f"Werkzeug-Format {eintrag.get('name', '?')!r}: {exc}") from exc
        if not ok:
            raise ausfuellen.DbFehler(f"Werkzeug-Format {self.name!r}: unbekannte Art oder fehlender Platzhalter")

    def _fuellen(self, vorlage: str, werte: dict) -> Teile:
        """Vorlage der Datenbank mit Werten: Text der Vorlage darf Marken enthalten, Werte nie."""
        teile: Teile = []
        pos = 0
        for m in _PLATZ.finditer(vorlage):
            teile += _zerlegen(vorlage[pos:m.start()], self.marken)
            wert = werte[m.group(1)]
            teile += [(wert, False)] if isinstance(wert, str) else list(wert)
            pos = m.end()
        teile += _zerlegen(vorlage[pos:], self.marken)
        return _verschmelzen(teile)

    # --- Systemprompt ------------------------------------------------------------------------------
    def kopf(self, werkzeuge: list[dict]) -> str:
        """Werkzeugliste mit Anleitung, wie die Vorlage sie schreibt (selbst gebaut, Marken erlaubt)."""
        liste = self.e["liste"]
        if liste["art"] == "json_zeilen":
            ws = werkzeuge if liste.get("huelle", True) else [w.get("function", w) for w in werkzeuge]
            text = "".join("\n" + json.dumps(w, ensure_ascii=False) for w in ws)
        elif liste["art"] == "gemma":
            text = "".join(f"<|tool>{_gemma_deklaration(w)}<tool|>" for w in werkzeuge)
        else:
            text = json.dumps(werkzeuge, ensure_ascii=False)
        return liste["vorlage"].replace("%{liste}", text)

    def system_teile(self, text: str, werkzeuge: list[dict]) -> Teile:
        liste = self.e["liste"]
        trenner = liste.get("trenner", "")
        if liste["ort"] == "vor_text":
            teile = _zerlegen(self.kopf(werkzeuge) + trenner, self.marken) + aktion_teile(self, text.strip())
        elif liste["ort"] == "nach_text":
            teile = aktion_teile(self, text.strip()) + _zerlegen(trenner + self.kopf(werkzeuge), self.marken)
        else:
            teile = aktion_teile(self, text.strip())
        return _verschmelzen(teile)

    def system_danach(self, werkzeuge: list[dict]) -> Teile | None:
        """Teile nach dem Ende des System-Zugs (Mistral: [AVAILABLE_TOOLS]); sonst None."""
        if self.e["liste"]["ort"] != "danach":
            return None
        return _zerlegen(self.kopf(werkzeuge), self.marken)

    # --- Aufrufe ---------------------------------------------------------------------------------------
    def aufruf_teile(self, name: str, felder: dict) -> Teile:
        a = self.e["aufruf"]
        if a["werte"] == "json_ganz":
            inhalt: Teile | str = json.dumps(felder, ensure_ascii=False)
        else:
            inhalt = []
            for i, k in enumerate(sorted(felder) if a.get("sortiert") else list(felder)):
                if i and a.get("felder_trenner"):
                    inhalt += _zerlegen(a["felder_trenner"], self.marken)
                wert = _gemma_wert(felder[k]) if a["werte"] == "gemma" else _wert_text(felder[k])
                inhalt += self._fuellen(a["feld"], {"schluessel": k, "wert": wert})
        return self._fuellen(a["vorlage"], {"name": name, "felder": inhalt})

    def aufrufe_finden(self, text: str) -> list[tuple[int, int, str, dict]]:
        """(Anfang, Ende, Werkzeug, Felder) je vollständigem Aufruf im erzeugten Text."""
        art, raus = self.e["lesen"]["art"], []
        if art == "json":
            for m in self._kopf_re.finditer(text):
                if (gelesen := self._json_aufruf(text, m.start())) is not None:
                    raus.append((m.start(), gelesen[2], gelesen[0], gelesen[1]))
            return raus
        for m in self._aufruf_re.finditer(text):
            if art == "gemma":
                try:
                    felder = _GemmaLeser(m.group(2) + "}").felder("}")
                except ValueError:
                    continue
            else:
                text = _text_felder(m.group(1).strip())
                felder = {k.strip(): v if k.strip() in text else _wert_lesen(v)
                          for k, v in self._feld_re.findall(m.group(2))}
            raus.append((m.start(), m.end(), m.group(1).strip(), felder))
        return raus

    def _json_aufruf(self, text: str, start: int):
        """KOPF{json} ab `start`: (Name, Felder, Ende) oder None."""
        m = self._kopf_re.match(text, start)
        if m is None:
            return None
        start = m.end()
        while start < len(text) and text[start].isspace():
            start += 1
        try:
            felder, ende = json.JSONDecoder().raw_decode(text, start)
        except json.JSONDecodeError:
            return None
        return (m.group(1), felder, ende) if isinstance(felder, dict) else None

    def aufruf_laenge(self, text: str) -> int | None:
        """Länge des vollständigen Aufrufs am Anfang von `text` (None: noch nicht fertig)."""
        if not self.ende:
            gelesen = self._json_aufruf(text, 0)
            return gelesen[2] if gelesen else None
        j = text.find(self.ende)
        return None if j < 0 else j + len(self.ende)

    # --- Ergebnisse ------------------------------------------------------------------------------------
    def antwort_teile(self, ergebnis: str, werkzeug: str) -> Teile:
        return self._fuellen(self.e["antwort"]["vorlage"], {"ergebnis": ergebnis, "werkzeug": werkzeug})

    def ergebnisse_teile(self, inhalt: str) -> Teile:
        """Nachricht mit Werkzeug-Ergebnissen: jedes Ergebnis in die Hülle der Vorlage, voranstehende
        Hinweise (Anleitungen, Gedächtnis) davor – mit `hinweise_in_antwort` (Antworten ohne eigenen
        Zug: Gemma 4, Mistral) in die erste Antwort, sonst stünden sie im Zug des Modells oder ohne
        Hülle. Alles bleibt reiner Text."""
        teile = _ERGEBNIS.split(inhalt)
        vor, bloecke = teile[0], [t.strip() for t in teile[1:] if t.strip()]
        if not bloecke:
            return [(inhalt, False)]
        hinein = bool(vor.strip()) and self.hinweise_in_antwort
        raus: Teile = [(vor.rstrip() + "\n\n", False)] if vor.strip() and not hinein else []
        for i, b in enumerate(bloecke):
            if i:
                raus.append((self.zwischen_antworten, False))
            m = _WERKZEUG.match(b)
            if i == 0 and hinein:
                b = vor.rstrip() + "\n\n" + b
            raus += self.antwort_teile(b, m.group(1) if m else "werkzeug")
        return _verschmelzen(raus)

    # --- als Text (Tests, Anzeige) ---------------------------------------------------------------------
    def aufruf(self, name: str, felder: dict) -> str:
        return als_text(self.aufruf_teile(name, felder))

    def system(self, text: str, werkzeuge: list[dict]) -> str:
        return als_text(self.system_teile(text, werkzeuge))

    def ergebnisse(self, inhalt: str) -> str:
        return als_text(self.ergebnisse_teile(inhalt))


def aktion_teile(dialekt: Dialekt, text: str) -> Teile:
    """Text mit ```aktion-Blöcken als Teile: Text bleibt Text, jeder Block wird ein Aufruf in der
    Muttersprache; nicht lesbare Blöcke (Vorlagen wie {"tool": "<name>", ...felder...}) werden zum
    Muster des Formats."""
    raus: Teile = []
    pos = 0
    for m in _AKTION.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:                 # Muster: ein Modell schrieb „<name>“ sonst wörtlich ab
            if not re.match(r'\{+\s*"tool"\s*:', m.group(1)):
                continue                             # anderer Code-Block: bleibt Text
            obj = {"tool": "werkzeug_name", "feld_1": "wert_1"}
        if not isinstance(obj, dict) or "tool" not in obj:
            continue
        raus.append((text[pos:m.start()], False))
        raus += dialekt.aufruf_teile(str(obj["tool"]), {k: v for k, v in obj.items() if k != "tool"})
        pos = m.end()
    raus.append((text[pos:], False))
    return [t for t in raus if t[0]]


def aktion_zu_nativ(dialekt: Dialekt, text: str) -> str:
    return als_text(aktion_teile(dialekt, text))


def nativ_zu_aktion(dialekt: Dialekt, text: str) -> str:
    """Aufrufe der Muttersprache im erzeugten Text als ```aktion-Blöcke (wie NemiCLI sie liest)."""
    teile, pos = [], 0
    for a, e, name, felder in dialekt.aufrufe_finden(text):
        teile.append(text[pos:a])
        teile.append("```aktion\n" + json.dumps({"tool": name, **felder}, ensure_ascii=False) + "\n```")
        pos = e
    teile.append(text[pos:])
    return "".join(teile)


class Strom:
    """Übersetzt erzeugten Text Stück für Stück: Text geht sofort weiter, ein Aufruf in der
    Muttersprache wird vom Anfang bis zur Ende-Marke zurückgehalten und dann als ```aktion-Block
    weitergegeben. `aufrufe`: so viele vollständige Aufrufe kamen bisher."""

    def __init__(self, dialekt: Dialekt):
        self.d = dialekt
        self.puffer = ""
        self.drin = False
        self.aufrufe = 0

    def __call__(self, stueck: str) -> str:
        self.puffer += stueck
        raus = []
        while True:
            if not self.drin:
                i = self.puffer.find(self.d.anfang)
                if i < 0:                                    # ein angefangener Anfang bleibt im Puffer
                    halten = next((k for k in range(min(len(self.d.anfang) - 1, len(self.puffer)), 0, -1)
                                   if self.d.anfang.startswith(self.puffer[-k:])), 0)
                    raus.append(self.puffer[:len(self.puffer) - halten])
                    self.puffer = self.puffer[len(self.puffer) - halten:]
                    break
                raus.append(self.puffer[:i])
                self.puffer = self.puffer[i:]
                self.drin = True
            n = self.d.aufruf_laenge(self.puffer)
            if n is None:
                break
            aufruf, self.puffer = self.puffer[:n], self.puffer[n:]
            umgesetzt = nativ_zu_aktion(self.d, aufruf)
            self.aufrufe += umgesetzt != aufruf
            raus.append(umgesetzt)
            self.drin = False
        return "".join(raus)

    def ende(self) -> str:
        """Rest am Ende: ein unvollständiger Aufruf bleibt sichtbar, wie er kam."""
        rest, self.puffer, self.drin = self.puffer, "", False
        return rest


# --- Bausteine für Gemma 4 ----------------------------------------------------------------------------------

_Q = '<|"|>'                                         # Gemmas Anführungszeichen (ein Sonder-Token)


def _gemma_wert(wert) -> Teile:
    """format_argument der Gemma-Vorlage (Schlüssel ohne Anführung): Text in <|"|>, Rest wie gesehen."""
    if wert is None:
        return [("null", False)]
    if isinstance(wert, bool):
        return [("true" if wert else "false", False)]
    if isinstance(wert, str):
        return [(_Q, True), (wert, False), (_Q, True)]
    if isinstance(wert, dict):
        raus: Teile = [("{", False)]
        for i, k in enumerate(sorted(wert)):
            raus += ([(",", False)] if i else []) + [(f"{k}:", False)] + _gemma_wert(wert[k])
        return raus + [("}", False)]
    if isinstance(wert, (list, tuple)):
        raus = [("[", False)]
        for i, x in enumerate(wert):
            raus += ([(",", False)] if i else []) + _gemma_wert(x)
        return raus + [("]", False)]
    return [(str(wert), False)]


class _GemmaLeser:
    """Liest die Argumente eines Gemma-Aufrufs: schluessel:wert, … mit <|"|>Text<|"|>, {…}, […],
    Zahlen, true/false/null."""

    def __init__(self, text: str):
        self.t, self.i = text, 0

    def _leer(self):
        while self.i < len(self.t) and self.t[self.i] in " \n\t":
            self.i += 1

    def wert(self):
        self._leer()
        t = self.t
        if t.startswith(_Q, self.i):
            ende = t.find(_Q, self.i + len(_Q))
            if ende < 0:
                raise ValueError("offener Text")
            wert, self.i = t[self.i + len(_Q):ende], ende + len(_Q)
            return wert
        if t.startswith("{", self.i):
            self.i += 1
            return self.felder("}")
        if t.startswith("[", self.i):
            self.i += 1
            raus = []
            self._leer()
            while not t.startswith("]", self.i):
                raus.append(self.wert())
                self._leer()
                if t.startswith(",", self.i):
                    self.i += 1
                    self._leer()
                elif not t.startswith("]", self.i):
                    raise ValueError("Liste ohne ] oder Komma")
            self.i += 1
            return raus
        m = re.compile(r"[^,}\]]*").match(t, self.i)
        roh, self.i = m.group(0).strip(), m.end()
        return {"true": True, "false": False, "null": None}.get(roh, _wert_lesen(roh) if roh else "")

    def felder(self, schluss: str) -> dict:
        raus = {}
        self._leer()
        while self.i < len(self.t) and not self.t.startswith(schluss, self.i):
            doppel = self.t.find(":", self.i)
            if doppel < 0:
                raise ValueError("Schlüssel ohne Wert")
            schluessel = self.t[self.i:doppel].strip().strip(_Q).strip('"')
            self.i = doppel + 1
            raus[schluessel] = self.wert()
            self._leer()
            if self.t.startswith(",", self.i):
                self.i += 1
                self._leer()
        self.i += len(schluss)
        return raus


def _gemma_deklaration(w: dict) -> str:
    """Gemmas Werkzeug-Deklaration (format_function_declaration der Vorlage)."""
    f = w.get("function", w)
    s = f"declaration:{f['name']}{{description:{_Q}{f.get('description', '')}{_Q}"
    p = f.get("parameters")
    if p:
        s += ",parameters:{"
        if p.get("properties"):
            s += "properties:{" + ",".join(f"{k}:{{type:{_Q}{str(v.get('type', 'string')).upper()}{_Q}}}"
                                           for k, v in sorted(p["properties"].items())) + "},"
        if p.get("required"):
            s += "required:[" + ",".join(f"{_Q}{r}{_Q}" for r in p["required"]) + "],"
        if p.get("type"):
            s += f"type:{_Q}{str(p['type']).upper()}{_Q}}}"
    return s + "}"


# --- Zugang über die Datenbank --------------------------------------------------------------------------

@lru_cache(maxsize=None)
def dialekt(name: str) -> Dialekt:
    """Das Werkzeug-Format `name` aus der Modell-Datenbank."""
    return Dialekt(ausfuellen.werkzeugformat(name))


def erkennen(vorlage: str | None) -> Dialekt | None:
    """Der Dialekt aus den Merkmalen der Chat-Vorlage (None: kein bekanntes eigenes Format)."""
    eintrag = ausfuellen.erkennen(ausfuellen.werkzeugformate(), vorlage or "")
    return dialekt(eintrag["name"]) if eintrag else None


def _text_felder(werkzeug: str) -> frozenset:
    """Felder, die werkzeug_liste als Text erklärt (die Pflichtfelder): ihr Wert bleibt Text, auch wenn
    er wie JSON aussieht (Dateiinhalt „{…}“, Suchtext „true“) – wie die Parser der Vorlagen."""
    try:
        import actions
    except ImportError:
        return frozenset()
    return frozenset((actions.ACTIONS.get(werkzeug) or {}).get("felder", ()))


def werkzeug_liste() -> list[dict]:
    """NemiCLIs Werkzeuge als Funktions-Schemas wie in den Vorlagen, schlank: Pflichtfelder mit Namen,
    Bedeutung und weitere Felder erklärt die Werkzeug-Liste im Systemprompt. Gemessen (Qwen3.5-9B,
    12 Aufgaben): so 12/12 richtig wie mit Typ-Angaben für jedes Werkzeug, nur mit Namen 11/12."""
    import actions
    liste = []
    for name, a in actions.ACTIONS.items():
        felder = list(a.get("felder", []))
        funktion = {"name": name}
        if felder:
            funktion["parameters"] = {"type": "object", "properties": {f: {"type": "string"} for f in felder},
                                      "required": felder}
        liste.append({"type": "function", "function": funktion})
    return liste
