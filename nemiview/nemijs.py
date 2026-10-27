"""
nemijs.py - eigener JavaScript-Interpreter für NemiCLI-Apps (NemiView).

Ein Baum-Interpreter über nemijs_parser. Werte: UNDEF, None (= null), bool, float, str, JSObject (mit JSArray,
JSFunktion, Nativ, Promise, Fehler …) und Wirts-Objekte (DOM, siehe nemidom) mit js_get/js_set.

Sicherheit durch Weglassen: Es gibt kein Netz (fetch, XMLHttpRequest, WebSocket), keine Dateien, kein eval,
keinen Function-Konstruktor und keinen Weg zu Python-Attributen – Eigenschaften gibt es nur in den eigenen
Tabellen. Grenzen: Rechenschritte je Einstieg (Rechenbremse), Aufruftiefe, Länge von Texten und Listen.
await wartet synchron: Mikroaufgaben und Zeitgeber laufen weiter, bis das Versprechen erfüllt ist.
"""
from __future__ import annotations

import heapq
import math
import re
import sys
import time
from collections import deque
from datetime import datetime

from .nemijs_parser import SyntaxFehler, _zahl_text, parsen

MAX_SCHRITTE = 5_000_000         # je Einstieg (Skript, Ereignis, Aufruf)
MAX_TIEFE = 150                  # JS-Aufrufe ineinander
MAX_TEXT = 5_000_000             # Zeichen
MAX_LISTE = 1_000_000            # Einträge
MAX_ZEITGEBER = 10_000
REGEX_MAX_EINGABE = 200_000

sys.setrecursionlimit(max(sys.getrecursionlimit(), 30_000))


class _Undef:
    __slots__ = ()

    def __repr__(self):
        return "undefined"

    def __bool__(self):
        return False


UNDEF = _Undef()


class Grenze(Exception):
    """Rechenbremse, Tiefe oder Größe überschritten – JS kann das nicht abfangen."""


class JSFehler(Exception):
    """Von JS geworfener Wert (throw)."""

    def __init__(self, wert):
        super().__init__(wert)
        self.wert = wert

    def __str__(self):
        w = self.wert
        if isinstance(w, JSObject) and w.klasse == "Error":
            name, text = w.props.get("name", "Error"), w.props.get("message", "")
            return f"{name}: {text}" if text else str(name)
        return zu_text(w)


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    def __init__(self, wert):
        self.wert = wert


# ---------------------------------------------------------------------------
# Objekte
# ---------------------------------------------------------------------------

class JSObject:
    __slots__ = ("props", "proto", "klasse", "eingefroren", "__weakref__")

    def __init__(self, proto=None, klasse="Object", props=None):
        self.props: dict = props if props is not None else {}
        self.proto = proto
        self.klasse = klasse
        self.eingefroren = False

    def suchen(self, key):
        o = self
        while o is not None:
            if key in o.props:
                return o.props[key]
            o = o.proto
        return UNDEF

    def hat(self, key) -> bool:
        o = self
        while o is not None:
            if key in o.props:
                return True
            o = o.proto
        return False


class JSArray(JSObject):
    __slots__ = ("items",)

    def __init__(self, items=None, proto=None):
        super().__init__(proto, "Array")
        self.items: list = items if items is not None else []


class JSFunktion(JSObject):
    __slots__ = ("knoten", "scope", "this_fest", "name", "ist_klasse", "basis", "felder", "heim")

    def __init__(self, knoten, scope, proto, name="", this_fest=None):
        super().__init__(proto, "Function")
        self.knoten, self.scope, self.name, self.this_fest = knoten, scope, name, this_fest
        self.ist_klasse, self.basis, self.felder, self.heim = False, None, [], None


class Nativ(JSObject):
    __slots__ = ("fn", "name", "konstruktor")

    def __init__(self, fn, name="", proto=None, konstruktor=None):
        super().__init__(proto, "Function")
        self.fn, self.name, self.konstruktor = fn, name, konstruktor


class Promise(JSObject):
    __slots__ = ("zustand", "wert", "folgen", "behandelt")

    def __init__(self, proto):
        super().__init__(proto, "Promise")
        self.zustand, self.wert, self.folgen, self.behandelt = "offen", UNDEF, [], False


class Wirt:
    """Basisklasse für Objekte des Gastgebers (DOM). Nur js_get/js_set/js_keys – nie Python-Attribute."""

    def js_get(self, key, js):
        return UNDEF

    def js_set(self, key, wert, js) -> None:
        raise JSFehler(js.fehler("TypeError", f"Eigenschaft „{key}“ ist nur lesbar"))

    def js_keys(self) -> list:
        return []

    js_klasse = "Object"


def ist_aufrufbar(v) -> bool:
    return isinstance(v, (JSFunktion, Nativ))


# ---------------------------------------------------------------------------
# Umwandlungen
# ---------------------------------------------------------------------------

def zu_text(v) -> str:
    if v is UNDEF:
        return "undefined"
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float):
        return _zahl_text(v)
    if isinstance(v, int):
        return _zahl_text(float(v))
    if isinstance(v, str):
        return v
    if isinstance(v, JSArray):
        return ",".join("" if x is UNDEF or x is None else zu_text(x) for x in v.items)
    if isinstance(v, (JSFunktion, Nativ)):
        return f"function {getattr(v, 'name', '')}() {{ [code] }}"
    if isinstance(v, JSObject):
        if v.klasse == "Error":
            return JSFehler(v).__str__()
        if v.klasse == "Date":
            return _datum_text(v)
        if v.klasse == "RegExp":
            return f"/{v.props.get('source', '')}/{v.props.get('flags', '')}"
        return "[object Object]"
    if isinstance(v, Wirt):
        return f"[object {v.js_klasse}]"
    return str(v)


def zu_zahl(v) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, float):
        return v
    if isinstance(v, int):
        return float(v)
    if v is None:
        return 0.0
    if v is UNDEF:
        return math.nan
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0.0
        try:
            if s[:2].lower() == "0x":
                return float(int(s[2:], 16))
            if s[:2].lower() == "0b":
                return float(int(s[2:], 2))
            if s in ("Infinity", "+Infinity"):
                return math.inf
            if s == "-Infinity":
                return -math.inf
            if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", s):
                return float(s)
        except ValueError:
            pass
        return math.nan
    if isinstance(v, JSArray):
        if not v.items:
            return 0.0
        if len(v.items) == 1:
            return zu_zahl(v.items[0])
        return math.nan
    if isinstance(v, JSObject) and v.klasse == "Date":
        return v.props.get("__zeit", math.nan)
    return math.nan


def zu_bool(v) -> bool:
    if v is UNDEF or v is None or v is False:
        return False
    if v is True:
        return True
    if isinstance(v, float):
        return not (v == 0 or v != v)
    if isinstance(v, str):
        return v != ""
    return True


def zu_int32(v) -> int:
    x = zu_zahl(v)
    if x != x or x in (math.inf, -math.inf):
        return 0
    n = int(x) & 0xFFFFFFFF
    return n - 0x100000000 if n >= 0x80000000 else n


def zu_uint32(v) -> int:
    x = zu_zahl(v)
    if x != x or x in (math.inf, -math.inf):
        return 0
    return int(x) & 0xFFFFFFFF


def typ_von(v) -> str:
    if v is UNDEF:
        return "undefined"
    if v is None:
        return "object"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (float, int)):
        return "number"
    if isinstance(v, str):
        return "string"
    if ist_aufrufbar(v):
        return "function"
    return "object"


def schluessel(v) -> str:
    if isinstance(v, str):
        return v
    return zu_text(v)


def _index(key):
    """'3' → 3 (Listen-Index), sonst None."""
    if isinstance(key, float) and key == int(key) and key >= 0:
        return int(key)
    if isinstance(key, str) and key.isdigit() and (key == "0" or not key.startswith("0")):
        return int(key)
    return None


def streng_gleich(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (float, int)) and isinstance(b, (float, int)):
        return float(a) == float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return a is b


def lose_gleich(a, b) -> bool:
    if (a is None or a is UNDEF) and (b is None or b is UNDEF):
        return True
    if a is None or a is UNDEF or b is None or b is UNDEF:
        return False
    if type(a) is type(b) or (isinstance(a, (float, int)) and isinstance(b, (float, int)) and
                              not isinstance(a, bool) and not isinstance(b, bool)):
        return streng_gleich(a, b)
    if isinstance(a, bool):
        return lose_gleich(zu_zahl(a), b)
    if isinstance(b, bool):
        return lose_gleich(a, zu_zahl(b))
    if isinstance(a, (float, int)) and isinstance(b, str):
        return float(a) == zu_zahl(b)
    if isinstance(a, str) and isinstance(b, (float, int)):
        return zu_zahl(a) == float(b)
    if isinstance(a, (JSObject, Wirt)) and not isinstance(b, (JSObject, Wirt)):
        return lose_gleich(zu_text(a) if isinstance(b, str) else zu_zahl(a), b)
    if isinstance(b, (JSObject, Wirt)) and not isinstance(a, (JSObject, Wirt)):
        return lose_gleich(a, zu_text(b) if isinstance(a, str) else zu_zahl(b))
    return a is b


def _datum_text(d: JSObject) -> str:
    z = d.props.get("__zeit", math.nan)
    if z != z:
        return "Invalid Date"
    return datetime.fromtimestamp(z / 1000).strftime("%a %b %d %Y %H:%M:%S")


# ---------------------------------------------------------------------------
# Gültigkeitsbereiche
# ---------------------------------------------------------------------------

class Scope:
    __slots__ = ("vars", "konst", "eltern", "funktion", "this", "fn")

    def __init__(self, eltern=None, funktion=False, this=UNDEF, fn=None):
        self.vars: dict = {}
        self.konst: set = set()
        self.eltern = eltern
        self.funktion = funktion
        self.this = this
        self.fn = fn

    def finden(self, name):
        s = self
        while s is not None:
            if name in s.vars:
                return s
            s = s.eltern
        return None

    def funktions_scope(self):
        s = self
        while s.eltern is not None and not s.funktion:
            s = s.eltern
        return s


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

class NemiJS:
    """Ein JS-Kosmos: globale Namen, Zeitgeber, Mikroaufgaben, Konsole."""

    def __init__(self, virtuelle_zeit: bool = True, max_schritte: int = MAX_SCHRITTE):
        self.max_schritte = max_schritte
        self.schritte = 0
        self.tiefe = 0
        self.virtuell = virtuelle_zeit
        self.uhr = 0.0 if virtuelle_zeit else time.monotonic() * 1000
        self.zeitgeber: list = []
        self._tnr = 0
        self._aktiv: set = set()
        self.mikro: deque = deque()
        self.konsole: list[str] = []
        self.unbehandelt: list[str] = []
        self.storage: dict | None = None            # localStorage-Inhalt (vom Gastgeber gesetzt)
        self.storage_max = 1_000_000
        self.storage_geaendert = None               # Rückruf nach Änderung (Speichern)
        self._aufbauen()

    # -- Grundlagen ---------------------------------------------------------------
    def schritt(self):
        self.schritte += 1
        if self.schritte > self.max_schritte:
            raise Grenze("Rechenbremse: das Skript hat zu lange gerechnet (Endlosschleife?)")

    def einstieg(self):
        """Neuer Einstieg von außen (Skript, Ereignis, Funktionsaufruf): Rechenbremse neu."""
        self.schritte = 0

    def fehler(self, art: str, text: str) -> JSObject:
        proto = self.protos.get(art, self.protos["Error"])
        return JSObject(proto, "Error", {"name": art, "message": text, "stack": ""})

    def werfen(self, art: str, text: str):
        raise JSFehler(self.fehler(art, text))

    def objekt(self, props: dict | None = None) -> JSObject:
        return JSObject(self.protos["Object"], "Object", props if props is not None else {})

    def liste(self, items=None) -> JSArray:
        items = list(items) if items is not None else []
        if len(items) > MAX_LISTE:
            raise Grenze("Liste zu groß")
        return JSArray(items, self.protos["Array"])

    def nativ(self, fn, name="") -> Nativ:
        return Nativ(fn, name, self.protos["Function"])

    def text_pruefen(self, s: str) -> str:
        if len(s) > MAX_TEXT:
            raise Grenze("Text zu lang")
        return s

    # -- Ausführen ----------------------------------------------------------------
    def ausfuehren(self, quelle: str, name: str = "skript"):
        """Skript im globalen Bereich ausführen (mit Rechenbremse); danach die Mikroaufgaben."""
        try:
            baum = parsen(quelle)
        except SyntaxFehler as e:
            raise JSFehler(self.fehler("SyntaxError", f"{name}: {e}")) from None
        self.einstieg()
        self._block(baum["body"], self.global_scope)
        self.mikro_abarbeiten()

    def aufrufen(self, f, this=UNDEF, args=()):
        """Von außen: JS-Funktion aufrufen (eigener Einstieg), Mikroaufgaben danach."""
        self.einstieg()
        erg = self.rufen(f, this, list(args))
        self.mikro_abarbeiten()
        return erg

    def mikro_abarbeiten(self):
        n = 0
        while self.mikro:
            n += 1
            if n > 100_000:
                raise Grenze("Zu viele Mikroaufgaben")
            fn = self.mikro.popleft()
            fn()

    def warten_auf(self, p: Promise):
        """await: Mikroaufgaben und Zeitgeber laufen, bis p erfüllt ist (die Zeit springt dabei vor)."""
        runden = 0
        while p.zustand == "offen":
            runden += 1
            if runden > 1_000_000:
                raise Grenze("await wartet zu lange")
            if self.mikro:
                self.mikro_abarbeiten()
                continue
            if not self.naechster_zeitgeber(springen=True):
                self.werfen("Error", "await wartet auf ein Versprechen, das nie erfüllt wird")
        p.behandelt = True
        if p.zustand == "erfuellt":
            return p.wert
        raise JSFehler(p.wert)

    # -- Zeitgeber ----------------------------------------------------------------
    def jetzt(self) -> float:
        return self.uhr if self.virtuell else time.monotonic() * 1000

    def zeitgeber_setzen(self, fn, ms, args, wiederholen: bool) -> float:
        if len(self._aktiv) >= MAX_ZEITGEBER:
            raise Grenze("Zu viele Zeitgeber")
        self._tnr += 1
        ms = max(0.0, zu_zahl(ms) if ms is not UNDEF else 0.0)
        if ms != ms:
            ms = 0.0
        heapq.heappush(self.zeitgeber, (self.jetzt() + ms, self._tnr, fn, args, ms if wiederholen else None))
        self._aktiv.add(self._tnr)
        return float(self._tnr)

    def zeitgeber_loeschen(self, nr):
        try:
            self._aktiv.discard(int(zu_zahl(nr)))
        except (ValueError, OverflowError):
            pass

    def naechster_zeitgeber(self, springen: bool = False) -> bool:
        """Den nächsten fälligen Zeitgeber ausführen. springen: auch wenn er erst später fällig wäre."""
        while self.zeitgeber:
            faellig, nr, fn, args, intervall = self.zeitgeber[0]
            if nr not in self._aktiv:
                heapq.heappop(self.zeitgeber)
                continue
            if not springen and faellig > self.jetzt():
                return False
            heapq.heappop(self.zeitgeber)
            if self.virtuell:
                self.uhr = max(self.uhr, faellig)
            if intervall is not None:
                heapq.heappush(self.zeitgeber, (max(faellig, self.jetzt()) + max(intervall, 4.0), nr, fn, args,
                                                intervall))
            else:
                self._aktiv.discard(nr)
            self.rufen(fn, UNDEF, list(args))
            self.mikro_abarbeiten()
            return True
        return False

    def zeitgeber_laufen(self, bis_ms: float = 10_000, max_anzahl: int = 10_000) -> None:
        """Headless: alle Zeitgeber bis bis_ms (virtuelle Zeit) abarbeiten."""
        ende = self.jetzt() + bis_ms
        for _ in range(max_anzahl):
            if not self.zeitgeber or self.zeitgeber[0][0] > ende:
                break
            self.einstieg()
            if not self.naechster_zeitgeber(springen=True):
                break

    def faellige_ausfuehren(self) -> None:
        """Viewer: alles ausführen, was jetzt (echte Zeit) fällig ist."""
        for _ in range(1000):
            self.einstieg()
            if not self.naechster_zeitgeber():
                break

    # -- Eigenschaften ------------------------------------------------------------
    def holen(self, obj, key, z=0):
        if isinstance(obj, JSArray):
            i = _index(key)
            if i is not None:
                return obj.items[i] if i < len(obj.items) else UNDEF
            k = schluessel(key)
            if k == "length":
                return float(len(obj.items))
            return obj.suchen(k)
        if isinstance(obj, JSObject):
            k = schluessel(key)
            if isinstance(obj, (JSFunktion, Nativ)) and k == "name":
                return obj.props.get("name", obj.name or "")
            if isinstance(obj, JSFunktion) and k == "prototype" and "prototype" not in obj.props:
                proto = self.objekt()
                proto.props["constructor"] = obj
                obj.props["prototype"] = proto
            return obj.suchen(k)
        if isinstance(obj, str):
            i = _index(key)
            if i is not None:
                return obj[i] if i < len(obj) else UNDEF
            k = schluessel(key)
            if k == "length":
                return float(len(obj))
            return self.protos["String"].suchen(k)
        if isinstance(obj, bool):
            return self.protos["Boolean"].suchen(schluessel(key))
        if isinstance(obj, (float, int)):
            return self.protos["Number"].suchen(schluessel(key))
        if isinstance(obj, Wirt):
            return obj.js_get(schluessel(key), self)
        if obj is UNDEF or obj is None:
            self.werfen("TypeError", f"Kann „{schluessel(key)}“ von {zu_text(obj)} nicht lesen")
        return UNDEF

    def setzen(self, obj, key, wert):
        if isinstance(obj, JSArray):
            i = _index(key)
            if i is not None:
                if obj.eingefroren:
                    return
                if i >= MAX_LISTE:
                    raise Grenze("Liste zu groß")
                if i < len(obj.items):
                    obj.items[i] = wert
                else:
                    obj.items.extend([UNDEF] * (i - len(obj.items)))
                    obj.items.append(wert)
                return
            k = schluessel(key)
            if k == "length":
                n = int(zu_zahl(wert))
                if n < 0 or n > MAX_LISTE:
                    self.werfen("RangeError", "Ungültige Listenlänge")
                del obj.items[n:]
                obj.items.extend([UNDEF] * (n - len(obj.items)))
                return
            if not obj.eingefroren:
                obj.props[k] = wert
            return
        if isinstance(obj, JSObject):
            k = schluessel(key)
            if k == "__proto__" or obj.eingefroren:
                return                                        # keine Prototyp-Verschmutzung
            obj.props[k] = wert
            return
        if isinstance(obj, Wirt):
            obj.js_set(schluessel(key), wert, self)
            return
        if obj is UNDEF or obj is None:
            self.werfen("TypeError", f"Kann „{schluessel(key)}“ von {zu_text(obj)} nicht setzen")

    def schluessel_von(self, obj) -> list[str]:
        if isinstance(obj, JSArray):
            return [str(i) for i in range(len(obj.items))] + [k for k in obj.props if not k.startswith("__")]
        if isinstance(obj, JSObject):
            return [k for k in obj.props if not k.startswith("__")]
        if isinstance(obj, str):
            return [str(i) for i in range(len(obj))]
        if isinstance(obj, Wirt):
            return list(obj.js_keys())
        return []

    # -- Aufrufen ------------------------------------------------------------------
    def rufen(self, f, this, args):
        if isinstance(f, Nativ):
            return f.fn(self, this, args)
        if not isinstance(f, JSFunktion):
            self.werfen("TypeError", f"{zu_text(f) if not isinstance(f, JSObject) else 'Objekt'} ist keine Funktion")
        if f.ist_klasse:
            self.werfen("TypeError", f"Klasse {f.name} braucht „new“")
        return self._fn_rufen(f, this, args)

    def _fn_rufen(self, f: JSFunktion, this, args, neu_this=None):
        self.tiefe += 1
        if self.tiefe > MAX_TIEFE:
            self.tiefe -= 1
            raise Grenze("Zu tief verschachtelte Aufrufe (Endlos-Rekursion?)")
        try:
            k = f.knoten
            if k["pfeil"]:
                scope = Scope(f.scope, funktion=True, this=_this_von(f.scope), fn=f)
            else:
                scope = Scope(f.scope, funktion=True, this=this if f.this_fest is None else f.this_fest, fn=f)
            scope.vars["arguments"] = self.liste(args) if not k["pfeil"] else _arguments_von(f.scope)
            self._parameter(k["params"], args, scope)
            if k["async"]:
                p = Promise(self.protos["Promise"])
                try:
                    erg = self._body(k["body"], scope)
                    if isinstance(erg, Promise):
                        self._uebernehmen(p, erg)
                    else:
                        self._erfuellen(p, erg)
                except JSFehler as e:
                    self._ablehnen(p, e.wert)
                return p
            return self._body(k["body"], scope)
        finally:
            self.tiefe -= 1

    def _body(self, body, scope):
        try:
            self._block(body, scope, eigener=False)
        except _Return as r:
            return r.wert
        return UNDEF

    def _parameter(self, params, args, scope):
        for i, (art, ziel, vorgabe) in enumerate(params):
            if art == "rest":
                self._binden(ziel, self.liste(args[i:]), scope, "let")
                break
            wert = args[i] if i < len(args) else UNDEF
            if wert is UNDEF and vorgabe is not None:
                wert = self.wert(vorgabe, scope)
            self._binden(ziel, wert, scope, "let")

    def konstruieren(self, f, args):
        if isinstance(f, Nativ):
            if f.konstruktor is None:
                self.werfen("TypeError", f"{f.name} ist kein Konstruktor")
            return f.konstruktor(self, args)
        if not isinstance(f, JSFunktion) or f.knoten["pfeil"]:
            self.werfen("TypeError", "Das ist kein Konstruktor")
        proto = self.holen(f, "prototype")
        obj = JSObject(proto if isinstance(proto, JSObject) else self.protos["Object"], "Object")
        return self._konstruieren_mit(f, obj, args)

    def _konstruieren_mit(self, f, obj, args):
        if isinstance(f, Nativ):
            erg = f.konstruktor(self, args)
            if isinstance(erg, JSObject):          # eingebaute Basis (Error …): Felder übernehmen
                obj.klasse = erg.klasse
                for k, v in erg.props.items():
                    obj.props.setdefault(k, v)
            return obj
        if f.basis is None:                    # abgeleitete Klassen setzen ihre Felder nach super()
            self._felder_setzen(f, obj)
        erg = self._fn_rufen(f, obj, args)
        return erg if isinstance(erg, (JSObject, Wirt)) else obj

    def _felder_setzen(self, f: JSFunktion, obj):
        for name, wert_knoten, scope in f.felder:
            s = Scope(scope, funktion=True, this=obj, fn=f)
            self.setzen(obj, name, self.wert(wert_knoten, s) if wert_knoten is not None else UNDEF)

    # -- Versprechen -----------------------------------------------------------------
    def _erfuellen(self, p: Promise, wert):
        if p.zustand != "offen":
            return
        if isinstance(wert, Promise):
            self._uebernehmen(p, wert)
            return
        if isinstance(wert, JSObject) and ist_aufrufbar(wert.suchen("then")):
            dann = wert.suchen("then")
            self.mikro.append(lambda: self.rufen(dann, wert, [self.nativ(lambda js, t, a: self._erfuellen(p, a[0] if a else UNDEF)),
                                                               self.nativ(lambda js, t, a: self._ablehnen(p, a[0] if a else UNDEF))]))
            return
        p.zustand, p.wert = "erfuellt", wert
        self._folgen(p)

    def _ablehnen(self, p: Promise, grund):
        if p.zustand != "offen":
            return
        p.zustand, p.wert = "abgelehnt", grund
        if not p.folgen:
            self.mikro.append(lambda: (not p.behandelt) and self.unbehandelt.append(zu_text(grund)))
        self._folgen(p)

    def _uebernehmen(self, p: Promise, q: Promise):
        q.behandelt = True
        q.folgen.append((lambda v: self._erfuellen(p, v), lambda g: self._ablehnen(p, g)))
        if q.zustand != "offen":
            self._folgen(q)

    def _folgen(self, p: Promise):
        if p.zustand == "offen":
            return
        folgen, p.folgen = p.folgen, []
        for ok, nein in folgen:
            p.behandelt = True
            cb = ok if p.zustand == "erfuellt" else nein
            self.mikro.append(lambda cb=cb, w=p.wert: cb(w))

    def then(self, p: Promise, bei_ok, bei_fehler) -> Promise:
        neu = Promise(self.protos["Promise"])

        def lauf(cb, wert, abgelehnt):
            if not ist_aufrufbar(cb):
                (self._ablehnen if abgelehnt else self._erfuellen)(neu, wert)
                return
            try:
                self._erfuellen(neu, self.rufen(cb, UNDEF, [wert]))
            except JSFehler as e:
                self._ablehnen(neu, e.wert)

        p.folgen.append((lambda v: lauf(bei_ok, v, False), lambda g: lauf(bei_fehler, g, True)))
        p.behandelt = True
        self._folgen(p)
        return neu

    def promise_aufgeloest(self, wert) -> Promise:
        p = Promise(self.protos["Promise"])
        self._erfuellen(p, wert)
        return p

    # -- Anweisungen -------------------------------------------------------------------
    def _block(self, body, scope, eigener: bool = True):
        if eigener:
            scope = Scope(scope)
        for a in body:                                   # Funktionen sind im ganzen Block bekannt
            if a["t"] == "FunktionDekl":
                scope.vars[a["name"]] = self._funktion(a["f"], scope)
        for a in body:
            self.anweisung(a, scope)

    def anweisung(self, a, scope):
        self.schritt()
        t = a["t"]
        if t == "Ausdruck":
            self.wert(a["e"], scope)
        elif t == "Var":
            ziel_scope = scope.funktions_scope() if a["art"] == "var" else scope
            for ziel, init in a["teile"]:
                wert = self.wert(init, scope) if init is not None else UNDEF
                if init is None and a["art"] == "var" and ziel["t"] == "Name" and ziel_scope.finden(ziel["name"]):
                    continue
                self._binden(ziel, wert, ziel_scope, a["art"])
        elif t == "If":
            if zu_bool(self.wert(a["bed"], scope)):
                self.anweisung(a["dann"], scope)
            elif a["sonst"] is not None:
                self.anweisung(a["sonst"], scope)
        elif t == "Block":
            self._block(a["body"], scope)
        elif t == "Return":
            raise _Return(self.wert(a["wert"], scope) if a["wert"] is not None else UNDEF)
        elif t == "For":
            s = Scope(scope)
            if a["init"] is not None:
                self.anweisung(a["init"], s)
            je_runde = _hat_let(a["init"])          # let: jede Runde eigene Bindung (Funktionen darin behalten sie)
            while True:
                if je_runde:
                    neu = Scope(scope)
                    neu.vars, neu.konst = dict(s.vars), set(s.konst)
                    s = neu
                if a["bed"] is not None and not zu_bool(self.wert(a["bed"], s)):
                    break
                self.schritt()
                try:
                    self.anweisung(a["body"], s)
                except _Break:
                    break
                except _Continue:
                    pass
                if je_runde:
                    neu = Scope(scope)
                    neu.vars, neu.konst = dict(s.vars), set(s.konst)
                    s = neu
                if a["schritt"] is not None:
                    self.wert(a["schritt"], s)
        elif t == "ForOf":
            for x in self.iterieren(self.wert(a["quelle"], scope)):
                self.schritt()
                s = Scope(scope)
                self._ziel_setzen(a, x, s)
                try:
                    self.anweisung(a["body"], s)
                except _Break:
                    break
                except _Continue:
                    continue
        elif t == "ForIn":
            obj = self.wert(a["quelle"], scope)
            for k in list(self.schluessel_von(obj)):
                self.schritt()
                s = Scope(scope)
                self._ziel_setzen(a, k, s)
                try:
                    self.anweisung(a["body"], s)
                except _Break:
                    break
                except _Continue:
                    continue
        elif t == "While":
            while zu_bool(self.wert(a["bed"], scope)):
                self.schritt()
                try:
                    self.anweisung(a["body"], scope)
                except _Break:
                    break
                except _Continue:
                    continue
        elif t == "DoWhile":
            while True:
                self.schritt()
                try:
                    self.anweisung(a["body"], scope)
                except _Break:
                    break
                except _Continue:
                    pass
                if not zu_bool(self.wert(a["bed"], scope)):
                    break
        elif t == "Break":
            raise _Break()
        elif t == "Continue":
            raise _Continue()
        elif t == "Throw":
            raise JSFehler(self.wert(a["wert"], scope))
        elif t == "Try":
            self._try(a, scope)
        elif t == "Switch":
            self._switch(a, scope)
        elif t == "FunktionDekl":
            pass                                          # schon am Blockanfang gebunden
        elif t == "KlasseDekl":
            scope.vars[a["name"]] = self._klasse(a["k"], scope)
        elif t == "Leer":
            pass
        else:
            self.werfen("SyntaxError", f"Unbekannte Anweisung {t}")

    def _ziel_setzen(self, a, wert, s):
        if a["art"] is not None:
            self._binden(a["ziel"], wert, s, a["art"])
        else:
            self._zuweisen(a["ziel"], wert, s)

    def _try(self, a, scope):
        try:
            try:
                self._block(a["block"]["body"], scope)
            except JSFehler as e:
                if a["fang"] is None:
                    raise
                s = Scope(scope)
                if a["param"] is not None:
                    self._binden(a["param"], e.wert, s, "let")
                self._block(a["fang"]["body"], s)
        finally:
            if a["schluss"] is not None:
                self._block(a["schluss"]["body"], scope)

    def _switch(self, a, scope):
        wert = self.wert(a["wert"], scope)
        s = Scope(scope)
        start = None
        for i, (test, _body) in enumerate(a["faelle"]):
            if test is not None and streng_gleich(wert, self.wert(test, s)):
                start = i
                break
        if start is None:
            start = next((i for i, (test, _b) in enumerate(a["faelle"]) if test is None), None)
        if start is None:
            return
        try:
            for _test, body in a["faelle"][start:]:
                for x in body:
                    self.anweisung(x, s)
        except _Break:
            pass

    # -- Bindungen und Zuweisungen ---------------------------------------------------------
    def _binden(self, ziel, wert, scope, art):
        t = ziel["t"]
        if t == "Name":
            scope.vars[ziel["name"]] = wert
            if art == "const":
                scope.konst.add(ziel["name"])
            else:
                scope.konst.discard(ziel["name"])
        elif t == "ObjMuster":
            if wert is UNDEF or wert is None:
                self.werfen("TypeError", f"Kann {zu_text(wert)} nicht zerlegen")
            benutzt = set()
            for k, berechnet, unterziel, vorgabe in ziel["props"]:
                k = schluessel(self.wert(k, scope)) if berechnet else k
                benutzt.add(k)
                v = self.holen(wert, k)
                if v is UNDEF and vorgabe is not None:
                    v = self.wert(vorgabe, scope)
                self._binden(unterziel, v, scope, art)
            if ziel["rest"]:
                rest = self.objekt({k: self.holen(wert, k) for k in self.schluessel_von(wert) if k not in benutzt})
                self._binden({"t": "Name", "name": ziel["rest"]}, rest, scope, art)
        elif t == "ListMuster":
            items = list(self.iterieren(wert))
            for i, el in enumerate(ziel["elemente"]):
                if el is None:
                    continue
                unterziel, vorgabe = el
                v = items[i] if i < len(items) else UNDEF
                if v is UNDEF and vorgabe is not None:
                    v = self.wert(vorgabe, scope)
                self._binden(unterziel, v, scope, art)
            if ziel["rest"] is not None:
                self._binden(ziel["rest"], self.liste(items[len(ziel["elemente"]):]), scope, art)
        elif t == "MitVorgabe":
            if wert is UNDEF:
                wert = self.wert(ziel["vorgabe"], scope)
            self._binden(ziel["ziel"], wert, scope, art)
        else:
            self.werfen("SyntaxError", "Ungültiges Bindungsziel")

    def _zuweisen(self, ziel, wert, scope):
        t = ziel["t"]
        if t == "Name":
            name = ziel["name"]
            s = scope.finden(name)
            if s is None:
                self.global_scope.vars[name] = wert           # wie im nicht-strikten Modus
                return
            if name in s.konst:
                self.werfen("TypeError", f"„{name}“ ist eine Konstante")
            s.vars[name] = wert
        elif t == "Glied":
            obj = self.wert(ziel["obj"], scope)
            self.setzen(obj, self._prop(ziel, scope), wert)
        elif t in ("ObjMuster", "ListMuster", "MitVorgabe"):
            self._muster_zuweisen(ziel, wert, scope)
        else:
            self.werfen("SyntaxError", "Ungültiges Ziel")

    def _muster_zuweisen(self, ziel, wert, scope):
        t = ziel["t"]
        if t == "ObjMuster":
            benutzt = set()
            for k, berechnet, unterziel, vorgabe in ziel["props"]:
                k = schluessel(self.wert(k, scope)) if berechnet else k
                benutzt.add(k)
                v = self.holen(wert, k)
                if v is UNDEF and vorgabe is not None:
                    v = self.wert(vorgabe, scope)
                self._zuweisen(unterziel, v, scope)
            if ziel["rest"]:
                rest = self.objekt({k: self.holen(wert, k) for k in self.schluessel_von(wert) if k not in benutzt})
                self._zuweisen({"t": "Name", "name": ziel["rest"]}, rest, scope)
        elif t == "ListMuster":
            items = list(self.iterieren(wert))
            for i, el in enumerate(ziel["elemente"]):
                if el is None:
                    continue
                unterziel, vorgabe = el
                v = items[i] if i < len(items) else UNDEF
                if v is UNDEF and vorgabe is not None:
                    v = self.wert(vorgabe, scope)
                self._zuweisen(unterziel, v, scope)
            if ziel["rest"] is not None:
                self._zuweisen(ziel["rest"], self.liste(items[len(ziel["elemente"]):]), scope)
        else:
            if wert is UNDEF:
                wert = self.wert(ziel["vorgabe"], scope)
            self._zuweisen(ziel["ziel"], wert, scope)

    def _prop(self, glied, scope):
        p = glied["prop"]
        return p["v"] if p["t"] == "Wert" else self.wert(p, scope)

    def iterieren(self, v):
        if isinstance(v, JSArray):
            return list(v.items)
        if isinstance(v, str):
            return list(v)
        if isinstance(v, JSObject) and v.klasse in ("Map", "Set"):
            daten = v.props["__daten"]
            if v.klasse == "Set":
                return [w for (_k, w) in daten.values()]
            return [self.liste([k2, w]) for (k2, w) in daten.values()]
        if isinstance(v, Wirt):
            laenge = v.js_get("length", self)
            if isinstance(laenge, float):
                return [v.js_get(str(i), self) for i in range(int(laenge))]
        self.werfen("TypeError", f"{typ_von(v)} ist nicht durchlaufbar")

    # -- Ausdrücke ------------------------------------------------------------------------
    def wert(self, e, scope):
        self.schritt()
        t = e["t"]
        if t == "Wert":
            return e["v"]
        if t == "Name":
            name = e["name"]
            s = scope
            while s is not None:
                v = s.vars.get(name, _FEHLT)
                if v is not _FEHLT:
                    return v
                s = s.eltern
            self.werfen("ReferenceError", f"„{name}“ ist nicht definiert")
        if t == "Glied":
            if e["obj"]["t"] == "Super":
                return self._super_holen(scope, self._prop(e, scope))
            obj = self.wert(e["obj"], scope)
            if e["opt"] and (obj is None or obj is UNDEF):
                raise _KetteAus()
            return self.holen(obj, self._prop(e, scope))
        if t == "Aufruf":
            return self._aufruf(e, scope)
        if t == "Binaer":
            return self.binaer(e["op"], self.wert(e["l"], scope), self.wert(e["r"], scope))
        if t == "Logisch":
            links = self.wert(e["l"], scope)
            op = e["op"]
            if op == "&&":
                return self.wert(e["r"], scope) if zu_bool(links) else links
            if op == "||":
                return links if zu_bool(links) else self.wert(e["r"], scope)
            return self.wert(e["r"], scope) if links is None or links is UNDEF else links
        if t == "Zuweisung":
            return self._zuweisung(e, scope)
        if t == "Unaer":
            return self._unaer(e, scope)
        if t == "Aendern":
            ziel = e["ziel"]
            alt = zu_zahl(self.wert(ziel, scope))
            neu = alt + 1 if e["op"] == "++" else alt - 1
            self._zuweisen(ziel, neu, scope)
            return neu if e["vorn"] else alt
        if t == "Bedingt":
            return self.wert(e["dann"] if zu_bool(self.wert(e["bed"], scope)) else e["sonst"], scope)
        if t == "Vorlage":
            return self.text_pruefen("".join(x if isinstance(x, str) else self._zu_text(self.wert(x, scope))
                                             for x in e["teile"]))
        if t == "Liste":
            items = []
            for x in e["elemente"]:
                if x["t"] == "Spread":
                    items.extend(self.iterieren(self.wert(x["e"], scope)))
                else:
                    items.append(self.wert(x, scope))
            return self.liste(items)
        if t == "Objekt":
            return self._objekt(e, scope)
        if t == "Funktion":
            return self._funktion(e, scope)
        if t == "Undef":
            return UNDEF
        if t == "This":
            return _this_von(scope)
        if t == "Neu":
            f = self.wert(e["ziel"], scope)
            return self.konstruieren(f, self._argumente(e["args"], scope))
        if t == "Kette":
            try:
                return self.wert(e["e"], scope)
            except _KetteAus:
                return UNDEF
        if t == "Folge":
            erg = UNDEF
            for x in e["teile"]:
                erg = self.wert(x, scope)
            return erg
        if t == "Regex":
            return self.regex(e["muster"], e["flags"])
        if t == "Await":
            v = self.wert(e["e"], scope)
            if isinstance(v, Promise):
                return self.warten_auf(v)
            return v
        if t == "Klasse":
            return self._klasse(e, scope)
        if t == "Super":
            self.werfen("SyntaxError", "super nur in Klassen")
        self.werfen("SyntaxError", f"Unbekannter Ausdruck {t}")

    def _zu_text(self, v):
        if isinstance(v, JSObject) and not isinstance(v, (JSArray, JSFunktion, Nativ)) and v.klasse == "Object":
            ts = v.suchen("toString")
            if isinstance(ts, JSFunktion):
                return zu_text(self.rufen(ts, v, []))
        return zu_text(v)

    def _argumente(self, args, scope) -> list:
        out = []
        for x in args:
            if x["t"] == "Spread":
                out.extend(self.iterieren(self.wert(x["e"], scope)))
            else:
                out.append(self.wert(x, scope))
        return out

    def _aufruf(self, e, scope):
        fk = e["f"]
        if fk["t"] == "Super":
            return self._super_rufen(scope, self._argumente(e["args"], scope))
        if fk["t"] == "Glied":
            if fk["obj"]["t"] == "Super":
                f = self._super_holen(scope, self._prop(fk, scope))
                this = _this_von(scope)
            else:
                obj = self.wert(fk["obj"], scope)
                if fk["opt"] and (obj is None or obj is UNDEF):
                    raise _KetteAus()
                key = self._prop(fk, scope)
                f = self.holen(obj, key)
                this = obj
                if not ist_aufrufbar(f):
                    if e["opt"] and (f is None or f is UNDEF):
                        raise _KetteAus()
                    self.werfen("TypeError", f"{_name_von(fk)} ist keine Funktion")
        else:
            f = self.wert(fk, scope)
            this = UNDEF
            if e["opt"] and (f is None or f is UNDEF):
                raise _KetteAus()
            if not ist_aufrufbar(f):
                self.werfen("TypeError", f"{_name_von(fk)} ist keine Funktion")
        return self.rufen(f, this, self._argumente(e["args"], scope))

    def _zuweisung(self, e, scope):
        op = e["op"]
        ziel = e["ziel"]
        if op == "=":
            wert = self.wert(e["wert"], scope)
            if ziel["t"] == "Name" and isinstance(wert, JSFunktion) and not wert.name:
                wert.name = ziel["name"]
            self._zuweisen(ziel, wert, scope)
            return wert
        if ziel["t"] == "Glied":                          # Objekt nur einmal auswerten
            obj = self.wert(ziel["obj"], scope)
            key = self._prop(ziel, scope)
            alt = self.holen(obj, key)
            neu = self._verbinden(op, alt, e, scope)
            if neu is not _FEHLT:
                self.setzen(obj, key, neu)
                return neu
            return alt
        alt = self.wert(ziel, scope)
        neu = self._verbinden(op, alt, e, scope)
        if neu is not _FEHLT:
            self._zuweisen(ziel, neu, scope)
            return neu
        return alt

    def _verbinden(self, op, alt, e, scope):
        if op == "&&=":
            return self.wert(e["wert"], scope) if zu_bool(alt) else _FEHLT
        if op == "||=":
            return _FEHLT if zu_bool(alt) else self.wert(e["wert"], scope)
        if op == "??=":
            return self.wert(e["wert"], scope) if alt is None or alt is UNDEF else _FEHLT
        return self.binaer(op[:-1], alt, self.wert(e["wert"], scope))

    def _unaer(self, e, scope):
        op = e["op"]
        if op == "typeof":
            if e["e"]["t"] == "Name" and scope.finden(e["e"]["name"]) is None:
                return "undefined"
            return typ_von(self.wert(e["e"], scope))
        if op == "delete":
            z = e["e"]
            if z["t"] == "Glied":
                obj = self.wert(z["obj"], scope)
                key = schluessel(self._prop(z, scope))
                if isinstance(obj, JSArray) and _index(key) is not None:
                    i = _index(key)
                    if i < len(obj.items):
                        obj.items[i] = UNDEF
                elif isinstance(obj, JSObject) and not obj.eingefroren:
                    obj.props.pop(key, None)
            return True
        v = self.wert(e["e"], scope)
        if op == "!":
            return not zu_bool(v)
        if op == "-":
            return -zu_zahl(v)
        if op == "+":
            return zu_zahl(v)
        if op == "~":
            return float(~zu_int32(v))
        if op == "void":
            return UNDEF
        self.werfen("SyntaxError", f"Unbekannter Operator {op}")

    def binaer(self, op, a, b):
        if op == "+":
            if isinstance(a, (float, int)) and isinstance(b, (float, int)) and not isinstance(a, bool) \
                    and not isinstance(b, bool):
                return float(a) + float(b)
            pa, pb = self._primitiv(a), self._primitiv(b)
            if isinstance(pa, str) or isinstance(pb, str):
                return self.text_pruefen(zu_text(pa) + zu_text(pb))
            return zu_zahl(pa) + zu_zahl(pb)
        if op == "-":
            return zu_zahl(a) - zu_zahl(b)
        if op == "*":
            return zu_zahl(a) * zu_zahl(b)
        if op == "/":
            x, y = zu_zahl(a), zu_zahl(b)
            if y == 0:
                if x == 0 or x != x:
                    return math.nan
                return math.copysign(math.inf, x) * (math.copysign(1, y))
            return x / y
        if op == "%":
            x, y = zu_zahl(a), zu_zahl(b)
            if y == 0 or x != x or y != y or x in (math.inf, -math.inf):
                return math.nan
            return math.fmod(x, y)
        if op == "**":
            try:
                return float(zu_zahl(a) ** zu_zahl(b))
            except (OverflowError, ZeroDivisionError):
                return math.inf
            except (TypeError, ValueError):
                return math.nan
        if op == "===":
            return streng_gleich(a, b)
        if op == "!==":
            return not streng_gleich(a, b)
        if op == "==":
            return lose_gleich(a, b)
        if op == "!=":
            return not lose_gleich(a, b)
        if op in ("<", ">", "<=", ">="):
            pa, pb = self._primitiv(a), self._primitiv(b)
            if isinstance(pa, str) and isinstance(pb, str):
                x, y = pa, pb
            else:
                x, y = zu_zahl(pa), zu_zahl(pb)
                if x != x or y != y:
                    return False
            return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]
        if op == "&":
            return float(zu_int32(a) & zu_int32(b))
        if op == "|":
            return float(zu_int32(a) | zu_int32(b))
        if op == "^":
            return float(zu_int32(a) ^ zu_int32(b))
        if op == "<<":
            return float(zu_int32(zu_int32(a) << (zu_uint32(b) & 31)))
        if op == ">>":
            return float(zu_int32(a) >> (zu_uint32(b) & 31))
        if op == ">>>":
            return float(zu_uint32(a) >> (zu_uint32(b) & 31))
        if op == "instanceof":
            return self._instanz(a, b)
        if op == "in":
            if isinstance(b, JSArray):
                i = _index(a)
                return (i is not None and i < len(b.items)) or b.hat(schluessel(a)) or schluessel(a) == "length"
            if isinstance(b, JSObject):
                return b.hat(schluessel(a))
            if isinstance(b, Wirt):
                return b.js_get(schluessel(a), self) is not UNDEF
            self.werfen("TypeError", "„in“ braucht ein Objekt")
        self.werfen("SyntaxError", f"Unbekannter Operator {op}")

    def _primitiv(self, v):
        if isinstance(v, JSObject) and not isinstance(v, (JSArray, JSFunktion, Nativ)):
            if v.klasse == "Date":
                return zu_text(v)
            vo = v.suchen("valueOf")
            if isinstance(vo, JSFunktion):
                r = self.rufen(vo, v, [])
                if not isinstance(r, JSObject):
                    return r
            ts = v.suchen("toString")
            if isinstance(ts, JSFunktion):
                return self.rufen(ts, v, [])
        if isinstance(v, (JSObject, Wirt)):
            return zu_text(v)
        return v

    def _instanz(self, a, b):
        if not ist_aufrufbar(b):
            self.werfen("TypeError", "Rechte Seite von instanceof ist keine Funktion")
        if isinstance(b, Nativ) and b.props.get("__pruefe"):
            return bool(b.props["__pruefe"](a))
        proto = self.holen(b, "prototype")
        if not isinstance(a, JSObject):
            return False
        o = a.proto
        while o is not None:
            if o is proto:
                return True
            o = o.proto
        return False

    def _objekt(self, e, scope):
        obj = self.objekt()
        for art, k, wert in e["props"]:
            if art == "spread":
                quelle = self.wert(wert, scope)
                if isinstance(quelle, (JSObject, str, Wirt)):
                    for key in self.schluessel_von(quelle):
                        obj.props[key] = self.holen(quelle, key)
                continue
            if art == "vorgabe":
                self.werfen("SyntaxError", "„=“ im Objekt nur beim Zerlegen")
            name, berechnet = k
            key = schluessel(self.wert(name, scope)) if berechnet else name
            if key == "__proto__":
                continue
            v = self.wert(wert, scope)
            if isinstance(v, JSFunktion) and not v.name:
                v.name = key
                if wert.get("methode"):
                    v.heim = obj
            obj.props[key] = v
        return obj

    def _funktion(self, k, scope) -> JSFunktion:
        f = JSFunktion(k, scope, self.protos["Function"], k.get("name") or "")
        return f

    def _klasse(self, k, scope) -> JSFunktion:
        basis = self.wert(k["basis"], scope) if k["basis"] is not None else None
        if basis is not None and not ist_aufrufbar(basis):
            self.werfen("TypeError", "extends braucht eine Klasse")
        ks = Scope(scope)
        konstr = None
        for art, name, statisch, f in k["glieder"]:
            if art == "methode" and name == "constructor" and not statisch:
                konstr = f
        if konstr is None:
            konstr = {"t": "Funktion", "name": k["name"], "pfeil": False, "async": False, "z": k["z"],
                      "params": [("rest", {"t": "Name", "name": "args"}, None)] if basis is not None else [],
                      "body": ([{"t": "Ausdruck", "e": {"t": "Aufruf", "f": {"t": "Super"},
                                                         "args": [{"t": "Spread", "e": {"t": "Name", "name": "args"}}],
                                                         "opt": False}}] if basis is not None else [])}
        cls = JSFunktion(konstr, ks, self.protos["Function"], k["name"] or "")
        cls.ist_klasse = True
        cls.basis = basis
        proto = JSObject(self.holen(basis, "prototype") if basis is not None else self.protos["Object"], "Object")
        proto.props["constructor"] = cls
        cls.props["prototype"] = proto
        cls.heim = proto
        if basis is not None:
            cls.proto = basis
        if k["name"]:
            ks.vars[k["name"]] = cls
        for art, name, statisch, f in k["glieder"]:
            if art == "methode" and name != "constructor":
                m = JSFunktion(f, ks, self.protos["Function"], name)
                m.heim = cls if statisch else proto
                (cls if statisch else proto).props[name] = m
            elif art == "feld":
                if statisch:
                    s = Scope(ks, funktion=True, this=cls)
                    cls.props[name] = self.wert(f, s) if f is not None else UNDEF
                else:
                    cls.felder.append((name, f, ks))
        return cls

    def _super_rufen(self, scope, args):
        fs = scope
        while fs is not None and not (fs.funktion and fs.fn is not None and not fs.fn.knoten["pfeil"]):
            fs = fs.eltern
        if fs is None or not isinstance(fs.fn, JSFunktion) or fs.fn.basis is None:
            self.werfen("SyntaxError", "super() nur im Konstruktor einer abgeleiteten Klasse")
        this = fs.this
        basis = fs.fn.basis
        if isinstance(basis, JSFunktion):
            if basis.basis is None:
                self._felder_setzen(basis, this)
            self._fn_rufen(basis, this, args)
        else:
            self._konstruieren_mit(basis, this, args)
        self._felder_setzen(fs.fn, this)
        return UNDEF

    def _super_holen(self, scope, key):
        fs = scope
        while fs is not None and not (fs.funktion and fs.fn is not None and not fs.fn.knoten["pfeil"]):
            fs = fs.eltern
        heim = fs.fn.heim if fs is not None and isinstance(fs.fn, JSFunktion) else None
        if heim is None or heim.proto is None:
            self.werfen("SyntaxError", "super.… nur in Klassen-Methoden")
        return heim.proto.suchen(schluessel(key))

    # -- Regulärer Ausdruck ------------------------------------------------------------------
    def regex(self, muster: str, flags: str) -> JSObject:
        py = muster.replace("(?<", "(?P<").replace("(?P<=", "(?<=").replace("(?P<!", "(?<!").replace("[^]", r"[\s\S]")
        py = re.sub(r"\\u\{([0-9a-fA-F]+)\}", lambda m: chr(int(m.group(1), 16)), py)
        py = re.sub(r"\\k<(\w+)>", r"(?P=\1)", py)
        f = 0
        if "i" in flags:
            f |= re.I
        if "m" in flags:
            f |= re.M
        if "s" in flags:
            f |= re.S
        try:
            komp = re.compile(py, f)
        except re.error as e:
            self.werfen("SyntaxError", f"Ungültiger regulärer Ausdruck /{muster}/: {e}")
        return JSObject(self.protos["RegExp"], "RegExp", {"source": muster, "flags": flags, "global": "g" in flags,
                                                          "lastIndex": 0.0, "__re": komp})

    # -- Aufbau der Umgebung -------------------------------------------------------------------
    def _aufbauen(self):
        from . import nemijs_eingebaut
        self.protos: dict[str, JSObject] = {}
        self.global_scope = Scope(None, funktion=True)
        nemijs_eingebaut.einrichten(self)


class _KetteAus(Exception):
    """a?.b mit a = null/undefined: der Rest der Kette ergibt undefined."""


_FEHLT = object()


def _this_von(scope):
    s = scope
    while s is not None:
        if s.funktion and (s.fn is None or not s.fn.knoten["pfeil"]):
            return s.this
        s = s.eltern
    return UNDEF


def _arguments_von(scope):
    s = scope
    while s is not None:
        if "arguments" in s.vars and s.funktion and (s.fn is None or not s.fn.knoten["pfeil"]):
            return s.vars["arguments"]
        s = s.eltern
    return UNDEF


def _hat_let(init) -> bool:
    return bool(init and init.get("t") == "Var" and init.get("art") in ("let", "const"))


def _name_von(k) -> str:
    if k["t"] == "Name":
        return k["name"]
    if k["t"] == "Glied" and k["prop"]["t"] == "Wert":
        return f"{_name_von(k['obj'])}.{k['prop']['v']}"
    return "Ausdruck"
