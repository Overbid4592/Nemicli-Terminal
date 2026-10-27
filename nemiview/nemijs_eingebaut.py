"""
nemijs_eingebaut.py - eingebaute Objekte von NemiJS: Object, Array, String, Number, Boolean, Math, JSON, Promise,
Error-Arten, Date, RegExp, Map, Set, console, Zeitgeber, localStorage.

Bewusst nicht vorhanden: fetch, XMLHttpRequest, WebSocket, eval, Function, import(), Worker, document.cookie.
"""
from __future__ import annotations

import json
import math
import random
import re
import time
from datetime import datetime, timezone

from .nemijs import (MAX_LISTE, REGEX_MAX_EINGABE, UNDEF, Grenze, JSArray, JSFehler, JSObject, Nativ,
                     Promise, Wirt, _index, ist_aufrufbar, lose_gleich, schluessel, streng_gleich, typ_von, zu_bool,
                     zu_int32, zu_text, zu_zahl)


def _arg(a, i):
    return a[i] if i < len(a) else UNDEF


def einrichten(js) -> None:
    P = js.protos
    P["Object"] = JSObject(None, "Object")
    P["Function"] = JSObject(P["Object"], "Object")
    for name in ("Array", "String", "Number", "Boolean", "Promise", "RegExp", "Date", "Map", "Set"):
        P[name] = JSObject(P["Object"], "Object")
    P["Error"] = JSObject(P["Object"], "Object")
    for art in ("TypeError", "RangeError", "SyntaxError", "ReferenceError"):
        P[art] = JSObject(P["Error"], "Object")
    g = js.global_scope.vars

    def nativ(fn, name=""):
        return Nativ(fn, name, P["Function"])

    def methoden(proto: JSObject, tabelle: dict):
        for name, fn in tabelle.items():
            proto.props[name] = nativ(fn, name)

    def konstruktor(name, fn, konstr=None, statisch: dict | None = None, proto_name=None):
        k = Nativ(fn, name, P["Function"], konstr)
        p = P.get(proto_name or name)
        if p is not None:
            k.props["prototype"] = p
            p.props["constructor"] = k
        for n, f in (statisch or {}).items():
            k.props[n] = nativ(f, n)
        g[name] = k
        return k

    # -- Object -----------------------------------------------------------------
    def _keys(js_, t, a):
        return js_.liste(js_.schluessel_von(_arg(a, 0)))

    def _values(js_, t, a):
        o = _arg(a, 0)
        return js_.liste([js_.holen(o, k) for k in js_.schluessel_von(o)])

    def _entries(js_, t, a):
        o = _arg(a, 0)
        return js_.liste([js_.liste([k, js_.holen(o, k)]) for k in js_.schluessel_von(o)])

    def _assign(js_, t, a):
        ziel = _arg(a, 0)
        for q in a[1:]:
            if isinstance(q, (JSObject, Wirt)):
                for k in js_.schluessel_von(q):
                    js_.setzen(ziel, k, js_.holen(q, k))
        return ziel

    def _freeze(js_, t, a):
        o = _arg(a, 0)
        if isinstance(o, JSObject):
            o.eingefroren = True
        return o

    def _from_entries(js_, t, a):
        o = js_.objekt()
        for paar in js_.iterieren(_arg(a, 0)):
            k = js_.holen(paar, 0.0)
            js_.setzen(o, k, js_.holen(paar, 1.0))
        return o

    def _create(js_, t, a):
        p = _arg(a, 0)
        return JSObject(p if isinstance(p, JSObject) else None, "Object")

    def _get_proto(js_, t, a):
        o = _arg(a, 0)
        return o.proto if isinstance(o, JSObject) and o.proto is not None else None

    konstruktor("Object", lambda js_, t, a: _arg(a, 0) if isinstance(_arg(a, 0), JSObject) else js_.objekt(),
                lambda js_, a: _arg(a, 0) if isinstance(_arg(a, 0), JSObject) else js_.objekt(),
                {"keys": _keys, "values": _values, "entries": _entries, "assign": _assign, "freeze": _freeze,
                 "fromEntries": _from_entries, "create": _create, "getPrototypeOf": _get_proto,
                 "isFrozen": lambda js_, t, a: bool(isinstance(_arg(a, 0), JSObject) and _arg(a, 0).eingefroren)})

    methoden(P["Object"], {
        "hasOwnProperty": lambda js_, t, a: (isinstance(t, JSObject) and schluessel(_arg(a, 0)) in t.props) or
        (isinstance(t, JSArray) and _index(_arg(a, 0)) is not None and _index(_arg(a, 0)) < len(t.items)),
        "toString": lambda js_, t, a: zu_text(t) if not isinstance(t, JSObject) or t.klasse != "Object"
        else "[object Object]",
        "valueOf": lambda js_, t, a: t,
    })

    # -- Function ---------------------------------------------------------------
    def _call(js_, t, a):
        return js_.rufen(t, _arg(a, 0), list(a[1:]))

    def _apply(js_, t, a):
        args = js_.iterieren(_arg(a, 1)) if _arg(a, 1) not in (UNDEF, None) else []
        return js_.rufen(t, _arg(a, 0), list(args))

    def _bind(js_, t, a):
        ziel, this, fest = t, _arg(a, 0), list(a[1:])
        return nativ(lambda js2, t2, a2: js2.rufen(ziel, this, fest + list(a2)), f"bound {getattr(t, 'name', '')}")

    methoden(P["Function"], {"call": _call, "apply": _apply, "bind": _bind,
                             "toString": lambda js_, t, a: zu_text(t)})

    # -- Array --------------------------------------------------------------------
    def _liste(t, js_) -> JSArray:
        if not isinstance(t, JSArray):
            js_.werfen("TypeError", "Das ist keine Liste")
        return t

    def _rueckruf(js_, a):
        f = _arg(a, 0)
        if not ist_aufrufbar(f):
            js_.werfen("TypeError", f"{zu_text(f)} ist keine Funktion")
        return f, _arg(a, 1)

    def _each(js_, t, a):
        f, this = _rueckruf(js_, a)
        for i, x in enumerate(list(_liste(t, js_).items)):
            js_.rufen(f, this, [x, float(i), t])
        return UNDEF

    def _map(js_, t, a):
        f, this = _rueckruf(js_, a)
        return js_.liste([js_.rufen(f, this, [x, float(i), t]) for i, x in enumerate(list(_liste(t, js_).items))])

    def _filter(js_, t, a):
        f, this = _rueckruf(js_, a)
        return js_.liste([x for i, x in enumerate(list(_liste(t, js_).items))
                          if zu_bool(js_.rufen(f, this, [x, float(i), t]))])

    def _reduce(js_, t, a, rechts=False):
        f = _arg(a, 0)
        items = list(_liste(t, js_).items)
        idx = list(range(len(items)))
        if rechts:
            idx.reverse()
        if len(a) > 1:
            acc = a[1]
        else:
            if not idx:
                js_.werfen("TypeError", "reduce auf leerer Liste ohne Startwert")
            acc = items[idx.pop(0)]
        for i in idx:
            acc = js_.rufen(f, UNDEF, [acc, items[i], float(i), t])
        return acc

    def _find(js_, t, a, index=False, letzter=False):
        f, this = _rueckruf(js_, a)
        items = list(_liste(t, js_).items)
        reihe = range(len(items) - 1, -1, -1) if letzter else range(len(items))
        for i in reihe:
            if zu_bool(js_.rufen(f, this, [items[i], float(i), t])):
                return float(i) if index else items[i]
        return -1.0 if index else UNDEF

    def _some(js_, t, a):
        f, this = _rueckruf(js_, a)
        return any(zu_bool(js_.rufen(f, this, [x, float(i), t])) for i, x in enumerate(list(_liste(t, js_).items)))

    def _every(js_, t, a):
        f, this = _rueckruf(js_, a)
        return all(zu_bool(js_.rufen(f, this, [x, float(i), t])) for i, x in enumerate(list(_liste(t, js_).items)))

    def _start_ende(n, a, i0=0):
        def norm(v, vorgabe):
            if v is UNDEF:
                return vorgabe
            x = zu_zahl(v)
            if x != x:
                return 0
            x = int(x) if abs(x) != math.inf else (n if x > 0 else -n)
            return max(0, n + x) if x < 0 else min(x, n)
        return norm(_arg(a, i0), 0), norm(_arg(a, i0 + 1), n)

    def _slice(js_, t, a):
        items = _liste(t, js_).items
        s, e = _start_ende(len(items), a)
        return js_.liste(items[s:e])

    def _splice(js_, t, a):
        items = _liste(t, js_).items
        n = len(items)
        s, _ = _start_ende(n, a)
        anzahl = n - s if len(a) < 2 else max(0, min(int(zu_zahl(a[1]) or 0), n - s))
        weg = items[s:s + anzahl]
        items[s:s + anzahl] = list(a[2:])
        return js_.liste(weg)

    def _sort(js_, t, a):
        items = _liste(t, js_).items
        f = _arg(a, 0)
        import functools
        undef = [x for x in items if x is UNDEF]
        rest = [x for x in items if x is not UNDEF]
        if ist_aufrufbar(f):
            def vgl(x, y):
                r = zu_zahl(js_.rufen(f, UNDEF, [x, y]))
                return 0 if r != r else (-1 if r < 0 else (1 if r > 0 else 0))
            rest.sort(key=functools.cmp_to_key(vgl))
        else:
            rest.sort(key=zu_text)
        items[:] = rest + undef
        return t

    def _index_of(js_, t, a, letzter=False):
        items = _liste(t, js_).items
        x = _arg(a, 0)
        reihe = range(len(items) - 1, -1, -1) if letzter else range(len(items))
        for i in reihe:
            if streng_gleich(items[i], x):
                return float(i)
        return -1.0

    def _includes(js_, t, a):
        x = _arg(a, 0)
        for y in _liste(t, js_).items:
            if streng_gleich(x, y) or (isinstance(x, float) and isinstance(y, float) and x != x and y != y):
                return True
        return False

    def _join(js_, t, a):
        sep = "," if _arg(a, 0) is UNDEF else zu_text(a[0])
        return js_.text_pruefen(sep.join("" if x is UNDEF or x is None else js_._zu_text(x)
                                         for x in _liste(t, js_).items))

    def _push(js_, t, a):
        items = _liste(t, js_).items
        if len(items) + len(a) > MAX_LISTE:
            raise Grenze("Liste zu groß")
        items.extend(a)
        return float(len(items))

    def _flat(js_, t, a):
        tiefe = 1 if _arg(a, 0) is UNDEF else int(zu_zahl(a[0]))

        def flach(items, d):
            out = []
            for x in items:
                if isinstance(x, JSArray) and d > 0:
                    out.extend(flach(x.items, d - 1))
                else:
                    out.append(x)
            return out
        return js_.liste(flach(_liste(t, js_).items, tiefe))

    def _concat(js_, t, a):
        out = list(_liste(t, js_).items)
        for x in a:
            out.extend(x.items if isinstance(x, JSArray) else [x])
        return js_.liste(out)

    def _fill(js_, t, a):
        items = _liste(t, js_).items
        s, e = _start_ende(len(items), a, 1)
        for i in range(s, e):
            items[i] = _arg(a, 0)
        return t

    def _at(js_, t, a):
        items = _liste(t, js_).items if isinstance(t, JSArray) else t
        i = int(zu_zahl(_arg(a, 0)) or 0)
        if i < 0:
            i += len(items)
        return items[i] if 0 <= i < len(items) else UNDEF

    methoden(P["Array"], {
        "push": _push,
        "pop": lambda js_, t, a: _liste(t, js_).items.pop() if _liste(t, js_).items else UNDEF,
        "shift": lambda js_, t, a: _liste(t, js_).items.pop(0) if _liste(t, js_).items else UNDEF,
        "unshift": lambda js_, t, a: (_liste(t, js_).items.__setitem__(slice(0, 0), list(a)),
                                      float(len(t.items)))[1],
        "slice": _slice, "splice": _splice, "map": _map, "filter": _filter, "forEach": _each,
        "reduce": _reduce, "reduceRight": lambda js_, t, a: _reduce(js_, t, a, True),
        "find": _find, "findIndex": lambda js_, t, a: _find(js_, t, a, True),
        "findLast": lambda js_, t, a: _find(js_, t, a, False, True),
        "findLastIndex": lambda js_, t, a: _find(js_, t, a, True, True),
        "some": _some, "every": _every, "includes": _includes, "indexOf": _index_of,
        "lastIndexOf": lambda js_, t, a: _index_of(js_, t, a, True), "join": _join,
        "reverse": lambda js_, t, a: (_liste(t, js_).items.reverse(), t)[1], "sort": _sort,
        "concat": _concat, "flat": _flat,
        "flatMap": lambda js_, t, a: _flat(js_, _map(js_, t, a), []), "fill": _fill, "at": _at,
        "keys": lambda js_, t, a: js_.liste([float(i) for i in range(len(_liste(t, js_).items))]),
        "entries": lambda js_, t, a: js_.liste([js_.liste([float(i), x]) for i, x in enumerate(_liste(t, js_).items)]),
        "toString": lambda js_, t, a: _join(js_, t, []),
    })

    def _array_neu(js_, a):
        if len(a) == 1 and isinstance(a[0], float):
            n = a[0]
            if n != int(n) or n < 0 or n > MAX_LISTE:
                js_.werfen("RangeError", "Ungültige Listenlänge")
            return js_.liste([UNDEF] * int(n))
        return js_.liste(list(a))

    def _from(js_, t, a):
        q = _arg(a, 0)
        f = _arg(a, 1)
        if isinstance(q, JSObject) and not isinstance(q, JSArray) and q.klasse not in ("Map", "Set"):
            n = int(zu_zahl(js_.holen(q, "length")) or 0)
            if n > MAX_LISTE:
                raise Grenze("Liste zu groß")
            items = [js_.holen(q, float(i)) for i in range(n)]
        else:
            items = js_.iterieren(q)
        if ist_aufrufbar(f):
            items = [js_.rufen(f, UNDEF, [x, float(i)]) for i, x in enumerate(items)]
        return js_.liste(items)

    ar = konstruktor("Array", lambda js_, t, a: _array_neu(js_, a), _array_neu,
                     {"isArray": lambda js_, t, a: isinstance(_arg(a, 0), JSArray), "from": _from,
                      "of": lambda js_, t, a: js_.liste(list(a))})
    ar.props["__pruefe"] = lambda v: isinstance(v, JSArray)

    # -- String --------------------------------------------------------------------
    def _s(t, js_) -> str:
        if not isinstance(t, str):
            return zu_text(t)
        return t

    def _replace(js_, t, a, alle=False):
        s = _s(t, js_)
        muster, ersatz = _arg(a, 0), _arg(a, 1)
        if isinstance(muster, JSObject) and muster.klasse == "RegExp":
            komp = muster.props["__re"]
            if len(s) > REGEX_MAX_EINGABE:
                js_.werfen("RangeError", "Text zu lang für einen regulären Ausdruck")
            anzahl = 0 if (muster.props.get("global") or alle) else 1

            def ers(m):
                if ist_aufrufbar(ersatz):
                    return zu_text(js_.rufen(ersatz, UNDEF, [m.group(0), *[g if g is not None else UNDEF
                                                                         for g in m.groups()], float(m.start()), s]))
                return _ersatz_text(zu_text(ersatz), m)
            return js_.text_pruefen(komp.sub(ers, s, count=anzahl))
        such = zu_text(muster)
        if ist_aufrufbar(ersatz):
            def eins(pos):
                return zu_text(js_.rufen(ersatz, UNDEF, [such, float(pos), s]))
            if alle:
                teile, i, out = s.split(such), 0, []
                for n, teil in enumerate(teile):
                    out.append(teil)
                    if n < len(teile) - 1:
                        out.append(eins(i + len(teil)))
                    i += len(teil) + len(such)
                return "".join(out)
            pos = s.find(such)
            return s if pos < 0 else s[:pos] + eins(pos) + s[pos + len(such):]
        e = zu_text(ersatz).replace("$&", such)
        return js_.text_pruefen(s.replace(such, e) if alle else s.replace(such, e, 1))

    def _split(js_, t, a):
        s = _s(t, js_)
        sep = _arg(a, 0)
        grenze = _arg(a, 1)
        if sep is UNDEF:
            teile = [s]
        elif isinstance(sep, JSObject) and sep.klasse == "RegExp":
            teile = sep.props["__re"].split(s)
            teile = [x if x is not None else UNDEF for x in teile]
        else:
            sep = zu_text(sep)
            teile = list(s) if sep == "" else s.split(sep)
        if grenze is not UNDEF:
            teile = teile[:int(zu_zahl(grenze))]
        return js_.liste(teile)

    def _match(js_, t, a):
        s = _s(t, js_)
        r = _arg(a, 0)
        if not (isinstance(r, JSObject) and r.klasse == "RegExp"):
            r = js_.regex(re.escape(zu_text(r)), "")
        if r.props.get("global"):
            treffer = [m.group(0) for m in r.props["__re"].finditer(s)]
            return js_.liste(treffer) if treffer else None
        return _exec_ergebnis(js_, r.props["__re"].search(s), s)

    def _match_all(js_, t, a):
        s = _s(t, js_)
        r = _arg(a, 0)
        return js_.liste([_exec_ergebnis(js_, m, s) for m in r.props["__re"].finditer(s)])

    def _pad(js_, t, a, vorn):
        s = _s(t, js_)
        n = int(zu_zahl(_arg(a, 0)) or 0)
        f = " " if _arg(a, 1) is UNDEF else zu_text(a[1])
        if n <= len(s) or not f:
            return s
        if n > 10_000_000:
            raise Grenze("Text zu lang")
        fuell = (f * (n // len(f) + 1))[:n - len(s)]
        return fuell + s if vorn else s + fuell

    def _sub(js_, t, a):
        s = _s(t, js_)
        n = len(s)
        x = 0 if _arg(a, 0) is UNDEF else zu_zahl(a[0])
        y = n if _arg(a, 1) is UNDEF else zu_zahl(a[1])
        x = 0 if x != x else max(0, min(int(x) if abs(x) != math.inf else (n if x > 0 else 0), n))
        y = 0 if y != y else max(0, min(int(y) if abs(y) != math.inf else (n if y > 0 else 0), n))
        return s[min(x, y):max(x, y)]

    def _slice_s(js_, t, a):
        s = _s(t, js_)
        x, y = _start_ende(len(s), a)
        return s[x:y]

    def _repeat(js_, t, a):
        n = int(zu_zahl(_arg(a, 0)) or 0)
        s = _s(t, js_)
        if n < 0:
            js_.werfen("RangeError", "repeat braucht eine Zahl ≥ 0")
        if len(s) * n > 10_000_000:
            raise Grenze("Text zu lang")
        return s * n

    def _locale_vgl(js_, t, a):
        x, y = _s(t, js_).casefold(), zu_text(_arg(a, 0)).casefold()
        return -1.0 if x < y else (1.0 if x > y else 0.0)

    methoden(P["String"], {
        "toUpperCase": lambda js_, t, a: _s(t, js_).upper(), "toLowerCase": lambda js_, t, a: _s(t, js_).lower(),
        "toLocaleUpperCase": lambda js_, t, a: _s(t, js_).upper(),
        "toLocaleLowerCase": lambda js_, t, a: _s(t, js_).lower(),
        "trim": lambda js_, t, a: _s(t, js_).strip(), "trimStart": lambda js_, t, a: _s(t, js_).lstrip(),
        "trimEnd": lambda js_, t, a: _s(t, js_).rstrip(),
        "includes": lambda js_, t, a: zu_text(_arg(a, 0)) in _s(t, js_),
        "startsWith": lambda js_, t, a: _s(t, js_).startswith(zu_text(_arg(a, 0)),
                                                              int(zu_zahl(_arg(a, 1)) or 0) if _arg(a, 1) is not UNDEF else 0),
        "endsWith": lambda js_, t, a: _s(t, js_).endswith(zu_text(_arg(a, 0))),
        "indexOf": lambda js_, t, a: float(_s(t, js_).find(zu_text(_arg(a, 0)),
                                                           int(zu_zahl(_arg(a, 1)) or 0) if _arg(a, 1) is not UNDEF else 0)),
        "lastIndexOf": lambda js_, t, a: float(_s(t, js_).rfind(zu_text(_arg(a, 0)))),
        "slice": _slice_s, "substring": _sub,
        "substr": lambda js_, t, a: _s(t, js_)[int(zu_zahl(_arg(a, 0)) or 0):][:None if _arg(a, 1) is UNDEF
                                                                                else max(0, int(zu_zahl(a[1])))],
        "replace": _replace, "replaceAll": lambda js_, t, a: _replace(js_, t, a, True),
        "split": _split, "match": _match, "matchAll": _match_all,
        "search": lambda js_, t, a: float(m.start()) if (m := (_arg(a, 0).props["__re"] if isinstance(_arg(a, 0), JSObject)
                                                                else re.compile(re.escape(zu_text(_arg(a, 0))))).search(_s(t, js_))) else -1.0,
        "padStart": lambda js_, t, a: _pad(js_, t, a, True), "padEnd": lambda js_, t, a: _pad(js_, t, a, False),
        "repeat": _repeat,
        "charAt": lambda js_, t, a: (lambda s, i: s[i] if 0 <= i < len(s) else "")(_s(t, js_), int(zu_zahl(_arg(a, 0)) or 0)),
        "charCodeAt": lambda js_, t, a: (lambda s, i: float(ord(s[i])) if 0 <= i < len(s) else math.nan)(
            _s(t, js_), int(zu_zahl(_arg(a, 0)) or 0)),
        "codePointAt": lambda js_, t, a: (lambda s, i: float(ord(s[i])) if 0 <= i < len(s) else UNDEF)(
            _s(t, js_), int(zu_zahl(_arg(a, 0)) or 0)),
        "at": lambda js_, t, a: _at(js_, _s(t, js_), a),
        "concat": lambda js_, t, a: js_.text_pruefen(_s(t, js_) + "".join(zu_text(x) for x in a)),
        "localeCompare": _locale_vgl, "normalize": lambda js_, t, a: _s(t, js_),
        "toString": lambda js_, t, a: _s(t, js_), "valueOf": lambda js_, t, a: _s(t, js_),
    })
    konstruktor("String", lambda js_, t, a: js_._zu_text(_arg(a, 0)) if a else "",
                lambda js_, a: js_._zu_text(_arg(a, 0)) if a else "",
                {"fromCharCode": lambda js_, t, a: "".join(chr(int(zu_zahl(x)) & 0xFFFF) for x in a),
                 "fromCodePoint": lambda js_, t, a: "".join(chr(int(zu_zahl(x))) for x in a)})

    # -- Number / Boolean ------------------------------------------------------------
    def _to_fixed(js_, t, a):
        x = zu_zahl(t)
        n = int(zu_zahl(_arg(a, 0)) or 0)
        if not 0 <= n <= 100:
            js_.werfen("RangeError", "toFixed: 0 bis 100 Stellen")
        if x != x:
            return "NaN"
        if abs(x) >= 1e21:
            return zu_text(x)
        from decimal import ROUND_HALF_UP, Decimal
        return str(Decimal(repr(x)).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)) if n else \
            str(int(Decimal(repr(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP)))

    def _zahl_to_string(js_, t, a):
        x = zu_zahl(t)
        basis = _arg(a, 0)
        if basis is UNDEF or zu_zahl(basis) == 10:
            return zu_text(x)
        b = int(zu_zahl(basis))
        if not 2 <= b <= 36:
            js_.werfen("RangeError", "toString: Basis 2 bis 36")
        if x != x:
            return "NaN"
        ganz = int(abs(x))
        ziffern = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = ""
        while True:
            ganz, r = divmod(ganz, b)
            out = ziffern[r] + out
            if not ganz:
                break
        bruch = abs(x) - int(abs(x))
        if bruch:
            out += "."
            for _ in range(20):
                bruch *= b
                out += ziffern[int(bruch)]
                bruch -= int(bruch)
                if not bruch:
                    break
        return ("-" if x < 0 else "") + out

    def _locale_zahl(js_, t, a):
        x = zu_zahl(t)
        opt = _arg(a, 1)
        stellen = None
        if isinstance(opt, JSObject):
            m = opt.props.get("maximumFractionDigits", opt.props.get("minimumFractionDigits"))
            if isinstance(m, float):
                stellen = int(m)
        text = f"{x:,.{stellen}f}" if stellen is not None else (f"{x:,.0f}" if x == int(x) else f"{x:,}")
        if zu_text(_arg(a, 0)).startswith("en"):
            return text
        return text.replace(",", " ").replace(".", ",").replace(" ", ".")

    methoden(P["Number"], {"toFixed": _to_fixed, "toString": _zahl_to_string, "toLocaleString": _locale_zahl,
                           "valueOf": lambda js_, t, a: zu_zahl(t),
                           "toPrecision": lambda js_, t, a: f"{zu_zahl(t):.{int(zu_zahl(_arg(a, 0)) or 1)}g}"})
    methoden(P["Boolean"], {"toString": lambda js_, t, a: zu_text(t), "valueOf": lambda js_, t, a: t})

    def _parse_int(js_, t, a):
        s = zu_text(_arg(a, 0)).strip()
        b = int(zu_zahl(_arg(a, 1))) if _arg(a, 1) is not UNDEF and zu_zahl(a[1]) == zu_zahl(a[1]) else 0
        neg = s.startswith("-")
        s = s.lstrip("+-")
        if (b in (0, 16)) and s[:2].lower() == "0x":
            s, b = s[2:], 16
        b = b or 10
        if not 2 <= b <= 36:
            return math.nan
        ziffern = "0123456789abcdefghijklmnopqrstuvwxyz"[:b]
        n = ""
        for c in s.lower():
            if c in ziffern:
                n += c
            else:
                break
        if not n:
            return math.nan
        return float(-int(n, b) if neg else int(n, b))

    def _parse_float(js_, t, a):
        m = re.match(r"\s*([+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?|[+-]?Infinity)", zu_text(_arg(a, 0)))
        if not m:
            return math.nan
        return zu_zahl(m.group(1))

    g["parseInt"] = nativ(_parse_int, "parseInt")
    g["parseFloat"] = nativ(_parse_float, "parseFloat")
    g["isNaN"] = nativ(lambda js_, t, a: zu_zahl(_arg(a, 0)) != zu_zahl(_arg(a, 0)), "isNaN")
    g["isFinite"] = nativ(lambda js_, t, a: math.isfinite(zu_zahl(_arg(a, 0))), "isFinite")
    g["NaN"], g["Infinity"] = math.nan, math.inf
    konstruktor("Number", lambda js_, t, a: zu_zahl(_arg(a, 0)) if a else 0.0,
                lambda js_, a: zu_zahl(_arg(a, 0)) if a else 0.0,
                {"isInteger": lambda js_, t, a: isinstance(_arg(a, 0), float) and math.isfinite(a[0]) and a[0] == int(a[0]),
                 "isFinite": lambda js_, t, a: isinstance(_arg(a, 0), float) and math.isfinite(a[0]),
                 "isNaN": lambda js_, t, a: isinstance(_arg(a, 0), float) and a[0] != a[0],
                 "parseFloat": _parse_float, "parseInt": _parse_int})
    g["Number"].props.update({"MAX_SAFE_INTEGER": 9007199254740991.0, "MIN_SAFE_INTEGER": -9007199254740991.0,
                              "EPSILON": 2.220446049250313e-16, "MAX_VALUE": 1.7976931348623157e308,
                              "MIN_VALUE": 5e-324, "POSITIVE_INFINITY": math.inf, "NEGATIVE_INFINITY": -math.inf,
                              "NaN": math.nan})
    konstruktor("Boolean", lambda js_, t, a: zu_bool(_arg(a, 0)), lambda js_, a: zu_bool(_arg(a, 0)))

    # -- Math ----------------------------------------------------------------------
    def m1(fn):
        def f(js_, t, a):
            x = zu_zahl(_arg(a, 0))
            try:
                return float(fn(x))
            except (ValueError, OverflowError):
                return math.nan
        return f

    def _rund(x):
        return math.floor(x + 0.5) if math.isfinite(x) else x

    def _minmax(fn, leer):
        def f(js_, t, a):
            werte = [zu_zahl(x) for x in a]
            if any(w != w for w in werte):
                return math.nan
            return float(fn(werte)) if werte else leer
        return f

    mathe = JSObject(P["Object"], "Object")
    for name, fn in {"floor": math.floor, "ceil": math.ceil, "round": _rund, "abs": abs, "sqrt": math.sqrt,
                     "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x), "sin": math.sin, "cos": math.cos,
                     "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan, "exp": math.exp,
                     "log": lambda x: math.log(x) if x > 0 else (-math.inf if x == 0 else math.nan),
                     "log2": lambda x: math.log2(x) if x > 0 else (-math.inf if x == 0 else math.nan),
                     "log10": lambda x: math.log10(x) if x > 0 else (-math.inf if x == 0 else math.nan),
                     "trunc": math.trunc, "sign": lambda x: x if x != x or x == 0 else math.copysign(1.0, x),
                     "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh}.items():
        mathe.props[name] = nativ(m1(lambda x, fn=fn: x if not math.isfinite(x) and fn in (math.floor, math.ceil,
                                                                                            math.trunc, _rund) else fn(x)), name)
    mathe.props["max"] = nativ(_minmax(max, -math.inf), "max")
    mathe.props["min"] = nativ(_minmax(min, math.inf), "min")
    mathe.props["pow"] = nativ(lambda js_, t, a: js_.binaer("**", _arg(a, 0), _arg(a, 1)), "pow")
    mathe.props["atan2"] = nativ(lambda js_, t, a: math.atan2(zu_zahl(_arg(a, 0)), zu_zahl(_arg(a, 1))), "atan2")
    mathe.props["hypot"] = nativ(lambda js_, t, a: math.hypot(*[zu_zahl(x) for x in a]), "hypot")
    mathe.props["random"] = nativ(lambda js_, t, a: random.random(), "random")
    mathe.props.update({"PI": math.pi, "E": math.e, "LN2": math.log(2), "LN10": math.log(10), "SQRT2": math.sqrt(2),
                        "LOG2E": 1 / math.log(2), "LOG10E": 1 / math.log(10), "SQRT1_2": math.sqrt(0.5)})
    g["Math"] = mathe

    # -- JSON ----------------------------------------------------------------------
    def _nach_py(js_, v, gesehen, tiefe=0):
        if tiefe > 100:
            js_.werfen("RangeError", "JSON zu tief verschachtelt")
        if v is None or isinstance(v, (bool, str)):
            return v
        if isinstance(v, float):
            if not math.isfinite(v):
                return None
            return int(v) if v == int(v) and abs(v) < 1e16 else v
        if isinstance(v, JSArray):
            if id(v) in gesehen:
                js_.werfen("TypeError", "JSON: Kreis im Objekt")
            gesehen.add(id(v))
            out = [None if x is UNDEF or ist_aufrufbar(x) else _nach_py(js_, x, gesehen, tiefe + 1) for x in v.items]
            gesehen.discard(id(v))
            return out
        if isinstance(v, JSObject) and not ist_aufrufbar(v):
            if id(v) in gesehen:
                js_.werfen("TypeError", "JSON: Kreis im Objekt")
            tj = v.suchen("toJSON")
            if ist_aufrufbar(tj):
                return _nach_py(js_, js_.rufen(tj, v, []), gesehen, tiefe + 1)
            if v.klasse == "Date":
                return _iso(v)
            if v.klasse in ("Map", "Set"):
                return {}
            gesehen.add(id(v))
            out = {k: _nach_py(js_, x, gesehen, tiefe + 1) for k, x in v.props.items()
                   if not k.startswith("__") and x is not UNDEF and not ist_aufrufbar(x)}
            gesehen.discard(id(v))
            return out
        return None

    def _stringify(js_, t, a):
        v = _arg(a, 0)
        if v is UNDEF or ist_aufrufbar(v):
            return UNDEF
        ein = _arg(a, 2)
        indent = None
        if isinstance(ein, float):
            indent = max(0, min(10, int(ein))) or None
        elif isinstance(ein, str) and ein:
            indent = ein[:10]
        text = json.dumps(_nach_py(js_, v, set()), ensure_ascii=False, indent=indent,
                          separators=None if indent else (",", ":"))
        return js_.text_pruefen(text)

    def _von_py(js_, v):
        if isinstance(v, dict):
            return js_.objekt({str(k): _von_py(js_, x) for k, x in v.items() if k != "__proto__"})
        if isinstance(v, list):
            return js_.liste([_von_py(js_, x) for x in v])
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return v
        if isinstance(v, (int, float)):
            return float(v)
        return UNDEF

    def _parse(js_, t, a):
        text = zu_text(_arg(a, 0))
        if len(text) > 20_000_000:
            raise Grenze("JSON zu groß")
        try:
            return _von_py(js_, json.loads(text))
        except (ValueError, RecursionError) as e:
            js_.werfen("SyntaxError", f"JSON.parse: {str(e)[:120]}")

    js_json = JSObject(P["Object"], "Object")
    js_json.props["stringify"] = nativ(_stringify, "stringify")
    js_json.props["parse"] = nativ(_parse, "parse")
    g["JSON"] = js_json
    js.json_nach_py = lambda v: _nach_py(js, v, set())
    js.json_von_py = lambda v: _von_py(js, v)
    g["structuredClone"] = nativ(lambda js_, t, a: _von_py(js_, _nach_py(js_, _arg(a, 0), set())), "structuredClone")

    # -- Fehler ---------------------------------------------------------------------
    def fehler_art(art):
        def neu(js_, a):
            o = js_.fehler(art, "" if _arg(a, 0) is UNDEF else zu_text(a[0]))
            return o
        k = konstruktor(art, lambda js_, t, a: neu(js_, a), neu)
        P[art].props["name"] = art
        P[art].props["message"] = ""
        return k

    for art in ("Error", "TypeError", "RangeError", "SyntaxError", "ReferenceError"):
        fehler_art(art)
    P["Error"].props["toString"] = nativ(lambda js_, t, a: zu_text(t), "toString")

    # -- Promise ---------------------------------------------------------------------
    def _promise_neu(js_, a):
        f = _arg(a, 0)
        if not ist_aufrufbar(f):
            js_.werfen("TypeError", "Promise braucht eine Funktion")
        p = Promise(P["Promise"])
        ok = nativ(lambda js2, t2, a2: js2._erfuellen(p, _arg(a2, 0)), "resolve")
        nein = nativ(lambda js2, t2, a2: js2._ablehnen(p, _arg(a2, 0)), "reject")
        try:
            js_.rufen(f, UNDEF, [ok, nein])
        except JSFehler as e:
            js_._ablehnen(p, e.wert)
        return p

    def _alle(js_, t, a, art):
        items = js_.iterieren(_arg(a, 0))
        p = Promise(P["Promise"])
        werte = [UNDEF] * len(items)
        offen = [len(items)]
        if not items:
            js_._erfuellen(p, js_.liste([]))
            return p
        for i, x in enumerate(items):
            q = x if isinstance(x, Promise) else js_.promise_aufgeloest(x)

            def ok(js2, t2, a2, i=i):
                werte[i] = _arg(a2, 0) if art != "allSettled" else js2.objekt({"status": "fulfilled", "value": _arg(a2, 0)})
                offen[0] -= 1
                if art == "race":
                    js2._erfuellen(p, _arg(a2, 0))
                elif offen[0] == 0:
                    js2._erfuellen(p, js2.liste(werte))

            def nein(js2, t2, a2, i=i):
                if art == "allSettled":
                    werte[i] = js2.objekt({"status": "rejected", "reason": _arg(a2, 0)})
                    offen[0] -= 1
                    if offen[0] == 0:
                        js2._erfuellen(p, js2.liste(werte))
                else:
                    js2._ablehnen(p, _arg(a2, 0))
            js_.then(q, nativ(ok), nativ(nein))
        return p

    def _abgelehnt(js_, t, a):
        p = Promise(P["Promise"])
        js_._ablehnen(p, _arg(a, 0))
        return p

    konstruktor("Promise", lambda js_, t, a: js_.werfen("TypeError", "Promise braucht „new“"), _promise_neu,
                {"resolve": lambda js_, t, a: _arg(a, 0) if isinstance(_arg(a, 0), Promise)
                 else js_.promise_aufgeloest(_arg(a, 0)),
                 "reject": _abgelehnt, "all": lambda js_, t, a: _alle(js_, t, a, "all"),
                 "allSettled": lambda js_, t, a: _alle(js_, t, a, "allSettled"),
                 "race": lambda js_, t, a: _alle(js_, t, a, "race")})

    def _p(t, js_) -> Promise:
        if not isinstance(t, Promise):
            js_.werfen("TypeError", "Das ist kein Promise")
        return t

    def _finally(js_, t, a):
        f = _arg(a, 0)

        def ok(js2, t2, a2):
            if ist_aufrufbar(f):
                js2.rufen(f, UNDEF, [])
            return _arg(a2, 0)

        def nein(js2, t2, a2):
            if ist_aufrufbar(f):
                js2.rufen(f, UNDEF, [])
            raise JSFehler(_arg(a2, 0))
        return js_.then(_p(t, js_), nativ(ok), nativ(nein))

    methoden(P["Promise"], {"then": lambda js_, t, a: js_.then(_p(t, js_), _arg(a, 0), _arg(a, 1)),
                            "catch": lambda js_, t, a: js_.then(_p(t, js_), UNDEF, _arg(a, 0)),
                            "finally": _finally})

    # -- Zeitgeber, Konsole ------------------------------------------------------------
    def _timeout(js_, t, a, wiederholen=False):
        f = _arg(a, 0)
        if not ist_aufrufbar(f):
            js_.werfen("TypeError", "setTimeout braucht eine Funktion (Text wird nicht ausgeführt)")
        return js_.zeitgeber_setzen(f, _arg(a, 1), list(a[2:]), wiederholen)

    g["setTimeout"] = nativ(_timeout, "setTimeout")
    g["setInterval"] = nativ(lambda js_, t, a: _timeout(js_, t, a, True), "setInterval")
    g["clearTimeout"] = nativ(lambda js_, t, a: js_.zeitgeber_loeschen(_arg(a, 0)), "clearTimeout")
    g["clearInterval"] = g["clearTimeout"]
    g["requestAnimationFrame"] = nativ(lambda js_, t, a: js_.zeitgeber_setzen(_arg(a, 0), 16.0,
                                                                              [js_.jetzt()], False), "requestAnimationFrame")
    g["cancelAnimationFrame"] = g["clearTimeout"]
    g["queueMicrotask"] = nativ(lambda js_, t, a: js_.mikro.append(lambda f=_arg(a, 0): js_.rufen(f, UNDEF, [])),
                                "queueMicrotask")

    def _log(stufe):
        def f(js_, t, a):
            zeile = " ".join(x if isinstance(x, str) else _anzeigen(js_, x) for x in a)
            js_.konsole.append((stufe + zeile)[:2000])
            del js_.konsole[:-500]
            return UNDEF
        return f

    konsole = JSObject(P["Object"], "Object")
    for name, stufe in (("log", ""), ("info", ""), ("debug", ""), ("warn", "⚠ "), ("error", "❌ ")):
        konsole.props[name] = nativ(_log(stufe), name)
    g["console"] = konsole

    # -- Date --------------------------------------------------------------------------
    def _date_neu(js_, a):
        if not a:
            z = time.time() * 1000
        elif len(a) == 1:
            x = a[0]
            if isinstance(x, float):
                z = x
            elif isinstance(x, JSObject) and x.klasse == "Date":
                z = x.props["__zeit"]
            else:
                z = _datum_lesen(zu_text(x))
        else:
            from datetime import timedelta
            teile = [int(zu_zahl(x)) for x in a[:7]] + [0] * (7 - len(a[:7]))
            try:
                jahr, monat = teile[0] + teile[1] // 12, teile[1] % 12 + 1     # Monat 0–11, Überlauf ins Jahr
                d = datetime(jahr, monat, 1) + timedelta(days=(teile[2] if len(a) > 2 else 1) - 1, hours=teile[3],
                                                         minutes=teile[4], seconds=teile[5], milliseconds=teile[6])
                z = d.timestamp() * 1000
            except (ValueError, OverflowError):
                z = math.nan
        return JSObject(P["Date"], "Date", {"__zeit": float(z)})

    def _dt(t, js_) -> datetime | None:
        if not (isinstance(t, JSObject) and t.klasse == "Date"):
            js_.werfen("TypeError", "Das ist kein Datum")
        z = t.props["__zeit"]
        return None if z != z else datetime.fromtimestamp(z / 1000)

    def holer(fn):
        def f(js_, t, a):
            d = _dt(t, js_)
            return math.nan if d is None else float(fn(d))
        return f

    def _lokal(js_, t, a, art):
        d = _dt(t, js_)
        if d is None:
            return "Invalid Date"
        if art == "datum":
            return f"{d.day}.{d.month}.{d.year}"
        if art == "zeit":
            return d.strftime("%H:%M:%S")
        return f"{d.day}.{d.month}.{d.year}, {d.strftime('%H:%M:%S')}"

    methoden(P["Date"], {
        "getTime": lambda js_, t, a: t.props["__zeit"], "valueOf": lambda js_, t, a: t.props["__zeit"],
        "getFullYear": holer(lambda d: d.year), "getMonth": holer(lambda d: d.month - 1),
        "getDate": holer(lambda d: d.day), "getDay": holer(lambda d: (d.weekday() + 1) % 7),
        "getHours": holer(lambda d: d.hour), "getMinutes": holer(lambda d: d.minute),
        "getSeconds": holer(lambda d: d.second), "getMilliseconds": holer(lambda d: d.microsecond // 1000),
        "toISOString": lambda js_, t, a: _iso(t), "toJSON": lambda js_, t, a: _iso(t),
        "toString": lambda js_, t, a: zu_text(t),
        "toLocaleDateString": lambda js_, t, a: _lokal(js_, t, a, "datum"),
        "toLocaleTimeString": lambda js_, t, a: _lokal(js_, t, a, "zeit"),
        "toLocaleString": lambda js_, t, a: _lokal(js_, t, a, "beides"),
        "getTimezoneOffset": lambda js_, t, a: float(-(datetime.now().astimezone().utcoffset().total_seconds() // 60)),
    })
    dk = konstruktor("Date", lambda js_, t, a: zu_text(_date_neu(js_, [])), _date_neu,
                     {"now": lambda js_, t, a: float(int(time.time() * 1000)),
                      "parse": lambda js_, t, a: _datum_lesen(zu_text(_arg(a, 0)))})
    del dk

    # -- RegExp ------------------------------------------------------------------------
    def _regexp_neu(js_, a):
        m = _arg(a, 0)
        if isinstance(m, JSObject) and m.klasse == "RegExp":
            return js_.regex(m.props["source"], zu_text(_arg(a, 1)) if _arg(a, 1) is not UNDEF else m.props["flags"])
        return js_.regex(zu_text(m) if m is not UNDEF else "(?:)", "" if _arg(a, 1) is UNDEF else zu_text(a[1]))

    def _r(t, js_) -> JSObject:
        if not (isinstance(t, JSObject) and t.klasse == "RegExp"):
            js_.werfen("TypeError", "Das ist kein regulärer Ausdruck")
        return t

    def _exec(js_, t, a):
        r = _r(t, js_)
        s = zu_text(_arg(a, 0))
        if len(s) > REGEX_MAX_EINGABE:
            js_.werfen("RangeError", "Text zu lang für einen regulären Ausdruck")
        start = int(zu_zahl(r.props.get("lastIndex", 0.0)) or 0) if r.props.get("global") else 0
        m = r.props["__re"].search(s, start) if start <= len(s) else None
        if r.props.get("global"):
            r.props["lastIndex"] = float(m.end() if m and m.end() > m.start() else (m.end() + 1 if m else 0))
        return _exec_ergebnis(js_, m, s)

    def _test(js_, t, a):
        return _exec(js_, t, a) is not None

    methoden(P["RegExp"], {"test": _test, "exec": _exec, "toString": lambda js_, t, a: zu_text(t)})
    konstruktor("RegExp", lambda js_, t, a: _regexp_neu(js_, a), _regexp_neu)

    # -- Map / Set ---------------------------------------------------------------------
    def _k(v):
        if isinstance(v, (JSObject, Wirt)):
            return ("o", id(v))
        if isinstance(v, float) and v != v:
            return ("nan",)
        return (type(v).__name__, v)

    def _map_neu(js_, a, art="Map"):
        o = JSObject(P[art], art, {"__daten": {}})
        q = _arg(a, 0)
        if q not in (UNDEF, None):
            for x in js_.iterieren(q):
                if art == "Map":
                    k, v = js_.holen(x, 0.0), js_.holen(x, 1.0)
                    o.props["__daten"][_k(k)] = (k, v)
                else:
                    o.props["__daten"][_k(x)] = (x, x)
        return o

    def _d(t, js_, art):
        if not (isinstance(t, JSObject) and t.klasse == art):
            js_.werfen("TypeError", f"Das ist kein {art}")
        return t.props["__daten"]

    def _map_set(js_, t, a):
        d = _d(t, js_, "Map")
        if len(d) >= MAX_LISTE:
            raise Grenze("Map zu groß")
        d[_k(_arg(a, 0))] = (_arg(a, 0), _arg(a, 1))
        return t

    def _set_add(js_, t, a):
        d = _d(t, js_, "Set")
        if len(d) >= MAX_LISTE:
            raise Grenze("Set zu groß")
        d[_k(_arg(a, 0))] = (_arg(a, 0), _arg(a, 0))
        return t

    def _for_each(art):
        def f(js_, t, a):
            fn = _arg(a, 0)
            for k, v in list(_d(t, js_, art).values()):
                js_.rufen(fn, UNDEF, [v, k, t])
            return UNDEF
        return f

    for art in ("Map", "Set"):
        methoden(P[art], {
            "has": lambda js_, t, a, art=art: _k(_arg(a, 0)) in _d(t, js_, art),
            "delete": lambda js_, t, a, art=art: _d(t, js_, art).pop(_k(_arg(a, 0)), None) is not None,
            "clear": lambda js_, t, a, art=art: _d(t, js_, art).clear(),
            "forEach": _for_each(art),
            "keys": lambda js_, t, a, art=art: js_.liste([k for k, _v in _d(t, js_, art).values()]),
            "values": lambda js_, t, a, art=art: js_.liste([v for _k2, v in _d(t, js_, art).values()]),
            "entries": lambda js_, t, a, art=art: js_.liste([js_.liste([k, v]) for k, v in _d(t, js_, art).values()]),
        })
    P["Map"].props["get"] = nativ(lambda js_, t, a: _d(t, js_, "Map").get(_k(_arg(a, 0)), (UNDEF, UNDEF))[1], "get")
    P["Map"].props["set"] = nativ(_map_set, "set")
    P["Set"].props["add"] = nativ(_set_add, "add")
    konstruktor("Map", lambda js_, t, a: js_.werfen("TypeError", "Map braucht „new“"), lambda js_, a: _map_neu(js_, a))
    konstruktor("Set", lambda js_, t, a: js_.werfen("TypeError", "Set braucht „new“"),
                lambda js_, a: _map_neu(js_, a, "Set"))

    # -- localStorage (je App eine Datei, vom Gastgeber gesetzt) -------------------------
    class Speicher(Wirt):
        js_klasse = "Storage"

        def _daten(self) -> dict:
            if js.storage is None:
                js.storage = {}
            return js.storage

        def js_get(self, key, js_):
            d = self._daten()
            tabelle = {
                "getItem": lambda js2, t, a: d.get(zu_text(_arg(a, 0)), None),
                "setItem": lambda js2, t, a: self._setzen(zu_text(_arg(a, 0)), zu_text(_arg(a, 1))),
                "removeItem": lambda js2, t, a: self._weg(zu_text(_arg(a, 0))),
                "clear": lambda js2, t, a: self._leeren(),
                "key": lambda js2, t, a: (list(d)[int(zu_zahl(_arg(a, 0)))] if 0 <= int(zu_zahl(_arg(a, 0)) or 0) < len(d)
                                          else None),
            }
            if key in tabelle:
                return nativ(tabelle[key], key)
            if key == "length":
                return float(len(d))
            return d.get(key, UNDEF)

        def js_set(self, key, wert, js_):
            self._setzen(key, zu_text(wert))

        def js_keys(self):
            return list(self._daten())

        def _setzen(self, k, v):
            d = self._daten()
            groesse = sum(len(a) + len(b) for a, b in d.items()) - len(d.get(k, "")) + len(k) + len(v)
            if groesse > js.storage_max:
                js.werfen("RangeError", "localStorage voll (höchstens 1 MB je App)")
            d[k] = v
            if js.storage_geaendert:
                js.storage_geaendert(d)
            return UNDEF

        def _weg(self, k):
            self._daten().pop(k, None)
            if js.storage_geaendert:
                js.storage_geaendert(self._daten())
            return UNDEF

        def _leeren(self):
            self._daten().clear()
            if js.storage_geaendert:
                js.storage_geaendert(self._daten())
            return UNDEF

    g["localStorage"] = Speicher()
    g["sessionStorage"] = Speicher()

    class Global(Wirt):
        js_klasse = "Window"

        def js_get(self, key, js_):
            return js_.global_scope.vars.get(key, UNDEF)

        def js_set(self, key, wert, js_):
            js_.global_scope.vars[key] = wert

        def js_keys(self):
            return [k for k in js.global_scope.vars]

    g["globalThis"] = g["window"] = g["self"] = Global()


def _ersatz_text(e: str, m) -> str:
    """Ersatz-Muster von String.replace: $& $1 … $<name> $$"""
    out, i = [], 0
    while i < len(e):
        c = e[i]
        if c == "$" and i + 1 < len(e):
            n = e[i + 1]
            if n == "$":
                out.append("$")
                i += 2
                continue
            if n == "&":
                out.append(m.group(0))
                i += 2
                continue
            if n.isdigit():
                j = i + 2 if not (i + 2 < len(e) and e[i + 2].isdigit() and int(e[i + 1:i + 3]) <= (m.re.groups or 0)) \
                    else i + 3
                nr = int(e[i + 1:j])
                if 0 < nr <= (m.re.groups or 0):
                    out.append(m.group(nr) or "")
                    i = j
                    continue
            if n == "<":
                j = e.find(">", i)
                if j > 0 and e[i + 2:j] in m.re.groupindex:
                    out.append(m.group(e[i + 2:j]) or "")
                    i = j + 1
                    continue
        out.append(c)
        i += 1
    return "".join(out)


def _exec_ergebnis(js, m, s):
    if m is None:
        return None
    erg = js.liste([m.group(0)] + [g if g is not None else UNDEF for g in m.groups()])
    erg.props["index"] = float(m.start())
    erg.props["input"] = s
    erg.props["groups"] = js.objekt({k: (v if v is not None else UNDEF) for k, v in m.groupdict().items()}) \
        if m.re.groupindex else UNDEF
    return erg


def _iso(d) -> str:
    z = d.props.get("__zeit", math.nan)
    if z != z:
        return "Invalid Date"
    t = datetime.fromtimestamp(z / 1000, tz=timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(z % 1000):03d}Z"


def _datum_lesen(s: str) -> float:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d",
                "%d.%m.%Y", "%d.%m.%Y %H:%M"):
        try:
            d = datetime.strptime(s, fmt)
            if fmt.endswith("Z") or fmt == "%Y-%m-%d":
                d = d.replace(tzinfo=timezone.utc)
            return d.timestamp() * 1000
        except ValueError:
            continue
    return math.nan


def _anzeigen(js, v, tiefe=0) -> str:
    """console.log-Darstellung."""
    if isinstance(v, str):
        return repr(v) if tiefe else v
    if isinstance(v, JSArray):
        if tiefe > 2:
            return "[…]"
        return "[" + ", ".join(_anzeigen(js, x, tiefe + 1) for x in v.items[:50]) + (", …" if len(v.items) > 50 else "") + "]"
    if isinstance(v, JSObject) and not ist_aufrufbar(v) and v.klasse in ("Object",):
        if tiefe > 2:
            return "{…}"
        teile = [f"{k}: {_anzeigen(js, x, tiefe + 1)}" for k, x in list(v.props.items())[:30] if not k.startswith("__")]
        return "{" + ", ".join(teile) + "}"
    return zu_text(v)


_ = (lose_gleich, typ_von, zu_int32)                  # gemeinsame Hilfen, hier nicht alle gebraucht
