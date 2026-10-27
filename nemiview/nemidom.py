"""
nemidom.py - HTML lesen und das DOM für NemiJS (NemiView).

Dokument → Elemente/Texte. Elemente sind Wirt-Objekte: JavaScript sieht nur, was hier in js_get/js_set steht
(getElementById, querySelector, textContent, value, classList, style, addEventListener, createElement, innerHTML …).
Dateien (style.css, app.js, Bilder) kommen nur aus dem eigenen App-Ordner; on…-Attribute nur aus der index.html,
nie aus innerHTML. Jede Änderung setzt `dok.geaendert` – das Fenster zeichnet dann neu.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

from . import nemicss
from .nemijs import UNDEF, JSFehler, JSObject, NemiJS, Wirt, ist_aufrufbar, zu_bool, zu_text, zu_zahl

LEER_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
MAX_KNOTEN = 100_000
_ATTR_ALS_EIGENSCHAFT = {"id": "id", "className": "class", "type": "type", "name": "name", "placeholder": "placeholder",
                         "min": "min", "max": "max", "step": "step", "src": "src", "href": "href", "alt": "alt",
                         "title": "title", "htmlFor": "for", "lang": "lang", "role": "role", "tabIndex": "tabindex",
                         "rows": "rows", "cols": "cols", "maxLength": "maxlength", "pattern": "pattern"}


class Knoten(Wirt):
    def __init__(self, dok):
        self.dok = dok
        self.eltern: Element | None = None

    def eltern_element(self):
        e = self.eltern
        return e if isinstance(e, Element) and not isinstance(e, Dokument) else None

    def vorheriges_element(self):
        if self.eltern is None:
            return None
        k = self.eltern.kinder
        i = k.index(self) if self in k else -1
        for j in range(i - 1, -1, -1):
            if isinstance(k[j], Element):
                return k[j]
        return None

    def naechstes_element(self):
        if self.eltern is None:
            return None
        k = self.eltern.kinder
        i = k.index(self) if self in k else len(k)
        for j in range(i + 1, len(k)):
            if isinstance(k[j], Element):
                return k[j]
        return None


class Text(Knoten):
    js_klasse = "Text"

    def __init__(self, dok, text: str):
        super().__init__(dok)
        self.text = text

    def js_get(self, key, js):
        if key in ("textContent", "nodeValue", "data", "wholeText"):
            return self.text
        if key == "nodeType":
            return 3.0
        if key == "nodeName":
            return "#text"
        if key in ("parentNode", "parentElement"):
            return self.eltern
        if key == "remove":
            return js.nativ(lambda js_, t, a: self.dok.entfernen(self), "remove")
        return UNDEF

    def js_set(self, key, wert, js):
        if key in ("textContent", "nodeValue", "data"):
            self.text = zu_text(wert)
            self.dok.aendern()


class Element(Knoten):
    js_klasse = "HTMLElement"

    def __init__(self, dok, tag: str, attrs: dict | None = None):
        super().__init__(dok)
        self.tag = tag.lower()
        self.attrs: dict[str, str] = dict(attrs or {})
        self.kinder: list[Knoten] = []
        self.stil_js: dict[str, str] = {}
        self.hoerer: dict[str, list] = {}
        self.wert: str = self.attrs.get("value", "") if self.tag != "textarea" else ""
        self.checked = "checked" in self.attrs
        self.box = None                       # vom Layout: (x, y, breite, höhe) im Dokument
        self.stil: dict = {}                  # berechneter Stil (Kaskade)

    # -- Baum ------------------------------------------------------------------
    def anhaengen(self, k: Knoten, vor: Knoten | None = None):
        if isinstance(k, Element) and (k is self or k.enthaelt(self)):
            raise JSFehler(self.dok.js.fehler("Error", "Ein Element kann sich nicht selbst enthalten"))
        if k.eltern is not None:
            k.eltern.kinder.remove(k)
        k.eltern = self
        if vor is not None and vor in self.kinder:
            self.kinder.insert(self.kinder.index(vor), k)
        else:
            self.kinder.append(k)
        self.dok.zaehlen()
        self.dok.aendern()

    def enthaelt(self, k) -> bool:
        while k is not None:
            if k is self:
                return True
            k = k.eltern
        return False

    def alle(self):
        for k in self.kinder:
            if isinstance(k, Element):
                yield k
                yield from k.alle()

    def text_inhalt(self) -> str:
        teile = []
        for k in self.kinder:
            if isinstance(k, Text):
                teile.append(k.text)
            elif isinstance(k, Element) and k.tag not in ("script", "style"):
                teile.append(k.text_inhalt())
        return "".join(teile)

    def elemente(self) -> list:
        return [k for k in self.kinder if isinstance(k, Element)]

    def html_text(self) -> str:
        out = []
        for k in self.kinder:
            if isinstance(k, Text):
                out.append(html.escape(k.text, quote=False))
            elif isinstance(k, Element):
                out.append(k.aussen_html())
        return "".join(out)

    def aussen_html(self) -> str:
        attrs = "".join(f' {n}="{html.escape(v)}"' if v != "" else f" {n}" for n, v in self.attrs.items())
        if self.tag in LEER_TAGS:
            return f"<{self.tag}{attrs}>"
        return f"<{self.tag}{attrs}>{self.html_text()}</{self.tag}>"

    def klassen(self) -> list[str]:
        return self.attrs.get("class", "").split()

    # -- Ereignisse --------------------------------------------------------------
    def hoeren(self, typ, fn, einmal=False):
        liste = self.hoerer.setdefault(typ, [])
        if not any(f is fn for f, _e in liste):
            liste.append((fn, einmal))

    def nicht_mehr_hoeren(self, typ, fn):
        self.hoerer[typ] = [(f, e) for f, e in self.hoerer.get(typ, []) if f is not fn]

    # -- JavaScript ----------------------------------------------------------------
    def js_get(self, key, js):
        d = self.dok
        if key in ("tagName", "nodeName"):
            return self.tag.upper()
        if key == "nodeType":
            return 1.0
        if key in ("textContent", "innerText"):
            return self.text_inhalt()
        if key == "innerHTML":
            return self.html_text()
        if key == "outerHTML":
            return self.aussen_html()
        if key == "value":
            if self.tag == "select":
                opt = self._gewaehlte_option()
                return opt.attrs.get("value", opt.text_inhalt()) if opt else ""
            return self.wert if self.tag in ("input", "textarea") else self.attrs.get("value", "")
        if key == "valueAsNumber":
            return zu_zahl(self.wert) if self.wert != "" else float("nan")
        if key == "checked":
            return self.checked
        if key in ("disabled", "hidden", "required", "readOnly", "multiple", "autofocus"):
            return (key.lower() if key != "readOnly" else "readonly") in self.attrs
        if key in _ATTR_ALS_EIGENSCHAFT:
            return self.attrs.get(_ATTR_ALS_EIGENSCHAFT[key], "")
        if key == "classList":
            return Klassenliste(self)
        if key == "style":
            return StilObjekt(self)
        if key == "dataset":
            return Datensatz(self)
        if key == "children":
            return js.liste(self.elemente())
        if key == "childNodes":
            return js.liste(list(self.kinder))
        if key == "childElementCount":
            return float(len(self.elemente()))
        if key in ("parentElement", "parentNode"):
            return self.eltern if key == "parentNode" else self.eltern_element()
        if key == "firstElementChild":
            e = self.elemente()
            return e[0] if e else None
        if key == "lastElementChild":
            e = self.elemente()
            return e[-1] if e else None
        if key == "firstChild":
            return self.kinder[0] if self.kinder else None
        if key == "lastChild":
            return self.kinder[-1] if self.kinder else None
        if key == "nextElementSibling":
            return self.naechstes_element()
        if key == "previousElementSibling":
            return self.vorheriges_element()
        if key in ("offsetWidth", "clientWidth", "scrollWidth"):
            return float(self.box[2]) if self.box else 0.0
        if key in ("offsetHeight", "clientHeight", "scrollHeight"):
            return float(self.box[3]) if self.box else 0.0
        if key == "offsetLeft":
            return float(self.box[0]) if self.box else 0.0
        if key == "offsetTop":
            return float(self.box[1]) if self.box else 0.0
        if key in ("scrollTop", "scrollLeft"):
            return 0.0
        if key == "options" and self.tag == "select":
            return js.liste([o for o in self.alle() if o.tag == "option"])
        if key == "selectedIndex" and self.tag == "select":
            opts = [o for o in self.alle() if o.tag == "option"]
            g = self._gewaehlte_option()
            return float(opts.index(g)) if g in opts else -1.0
        if key == "selected" and self.tag == "option":
            return "selected" in self.attrs
        if key == "text" and self.tag == "option":
            return self.text_inhalt()
        if key == "ownerDocument":
            return d
        if key.startswith("on") and key[2:] in self.hoerer:
            eigen = [f for f, _ in self.hoerer[key[2:]] if getattr(f, "_eigenschaft", False)]
            return eigen[0] if eigen else None
        methoden = _ELEMENT_METHODEN.get(key)
        if methoden is not None:
            return js.nativ(lambda js_, t, a, m=methoden: m(self, js_, a), key)
        return UNDEF

    def js_set(self, key, wert, js):
        d = self.dok
        if key in ("textContent", "innerText"):
            for k in self.kinder:
                k.eltern = None
            self.kinder = [Text(d, zu_text(wert))] if zu_text(wert) else []
            for k in self.kinder:
                k.eltern = self
            d.aendern()
        elif key == "innerHTML":
            for k in self.kinder:
                k.eltern = None
            self.kinder = []
            for k in fragment(d, zu_text(wert)):
                self.anhaengen(k)
            d.aendern()
        elif key == "value":
            if self.tag == "select":
                for o in self.alle():
                    if o.tag == "option":
                        if o.attrs.get("value", o.text_inhalt()) == zu_text(wert):
                            o.attrs["selected"] = ""
                        else:
                            o.attrs.pop("selected", None)
            else:
                self.wert = zu_text(wert)
            d.aendern()
        elif key == "checked":
            self.checked = zu_bool(wert)
            d.aendern()
        elif key in ("disabled", "hidden", "required", "readOnly"):
            name = key.lower() if key != "readOnly" else "readonly"
            if zu_bool(wert):
                self.attrs[name] = ""
            else:
                self.attrs.pop(name, None)
            d.aendern()
        elif key in _ATTR_ALS_EIGENSCHAFT:
            self.attrs[_ATTR_ALS_EIGENSCHAFT[key]] = zu_text(wert)
            d.aendern()
        elif key == "style":
            self.stil_js = {n: w for n, w, _i in nemicss.deklarationen(zu_text(wert))}
            d.aendern()
        elif key == "selectedIndex" and self.tag == "select":
            opts = [o for o in self.alle() if o.tag == "option"]
            for i, o in enumerate(opts):
                if i == int(zu_zahl(wert)):
                    o.attrs["selected"] = ""
                else:
                    o.attrs.pop("selected", None)
            d.aendern()
        elif key.startswith("on") and len(key) > 2:
            typ = key[2:]
            self.hoerer[typ] = [(f, e) for f, e in self.hoerer.get(typ, []) if not getattr(f, "_eigenschaft", False)]
            if ist_aufrufbar(wert):
                wert.props["_eigenschaft"] = True
                self.hoeren(typ, wert)
        else:
            raise JSFehler(js.fehler("TypeError", f"Eigenschaft „{key}“ von <{self.tag}> lässt sich nicht setzen"))

    def js_keys(self):
        return []

    def _gewaehlte_option(self):
        opts = [o for o in self.alle() if o.tag == "option"]
        for o in opts:
            if "selected" in o.attrs:
                return o
        return opts[0] if opts else None


def _methode_attr(name):
    def f(el, js, a):
        k = zu_text(a[0]).lower() if a else ""
        if name == "get":
            return el.attrs.get(k, None)
        if name == "has":
            return k in el.attrs
        if name == "remove":
            el.attrs.pop(k, None)
            if k == "value":
                el.wert = ""
        elif name == "set":
            if k.startswith("on"):
                raise JSFehler(js.fehler("TypeError", "on…-Attribute per setAttribute gibt es nicht – addEventListener nehmen"))
            el.attrs[k] = zu_text(a[1]) if len(a) > 1 else ""
            if k == "value" and el.tag in ("input",):
                el.wert = el.attrs[k]
            if k == "checked":
                el.checked = True
        elif name == "toggle":
            an = zu_bool(a[1]) if len(a) > 1 else k not in el.attrs
            if an:
                el.attrs[k] = ""
            else:
                el.attrs.pop(k, None)
            el.dok.aendern()
            return an
        el.dok.aendern()
        return UNDEF
    return f


def _anhaengen(el, js, a):
    for k in a:
        el.anhaengen(_als_knoten(el.dok, k))
    return a[0] if a else UNDEF


def _als_knoten(dok, k):
    if isinstance(k, Knoten):
        return k
    return Text(dok, zu_text(k))


def _einfuegen_vor(el, js, a):
    neu = _als_knoten(el.dok, a[0])
    ref = a[1] if len(a) > 1 else None
    el.anhaengen(neu, ref if isinstance(ref, Knoten) else None)
    return neu


def _voranstellen(el, js, a):
    erster = el.kinder[0] if el.kinder else None
    for k in a:
        el.anhaengen(_als_knoten(el.dok, k), erster)
    return UNDEF


def _entfernen_kind(el, js, a):
    k = a[0] if a else None
    if isinstance(k, Knoten) and k.eltern is el:
        el.dok.entfernen(k)
    return k


def _ersetzen_kinder(el, js, a):
    for k in list(el.kinder):
        k.eltern = None
    el.kinder = []
    for k in a:
        el.anhaengen(_als_knoten(el.dok, k))
    el.dok.aendern()
    return UNDEF


def _klonen(el, js, a, tief=None):
    tief = zu_bool(a[0]) if a else False

    def kopie(k):
        if isinstance(k, Text):
            return Text(el.dok, k.text)
        n = Element(el.dok, k.tag, k.attrs)
        n.stil_js = dict(k.stil_js)
        n.wert, n.checked = k.wert, k.checked
        if tief:
            for kind in k.kinder:
                c = kopie(kind)
                c.eltern = n
                n.kinder.append(c)
        return n
    return kopie(el)


def _suche(el, js, a, alle=False):
    sel = zu_text(a[0]) if a else ""
    _selektor_pruefen(js, sel)
    treffer = []
    for e in el.alle():
        if any(nemicss.passt(e, s.strip(), el.dok.zustand) for s in nemicss._teilen(sel, ",")):
            if not alle:
                return e
            treffer.append(e)
    return js.liste(treffer) if alle else None


def _selektor_pruefen(js, sel):
    for s in nemicss._teilen(sel, ","):
        try:
            nemicss.selektor_lesen(s)
        except ValueError:
            raise JSFehler(js.fehler("SyntaxError", f"„{sel}“ ist kein gültiger Selektor")) from None


def _naechster(el, js, a):
    sel = zu_text(a[0]) if a else ""
    _selektor_pruefen(js, sel)
    e = el
    while isinstance(e, Element) and not isinstance(e, Dokument):
        if nemicss.passt(e, sel, el.dok.zustand):
            return e
        e = e.eltern
    return None


def _hoeren(el, js, a):
    typ, fn = zu_text(a[0]) if a else "", a[1] if len(a) > 1 else UNDEF
    opt = a[2] if len(a) > 2 else UNDEF
    einmal = isinstance(opt, JSObject) and zu_bool(opt.props.get("once", False))
    if ist_aufrufbar(fn):
        el.hoeren(typ, fn, einmal)
    return UNDEF


def _rect(el, js, a):
    x, y, b, h = el.box or (0, 0, 0, 0)
    return js.objekt({"x": float(x), "y": float(y), "left": float(x), "top": float(y), "width": float(b),
                      "height": float(h), "right": float(x + b), "bottom": float(y + h)})


_ELEMENT_METHODEN = {
    "getAttribute": _methode_attr("get"), "setAttribute": _methode_attr("set"),
    "removeAttribute": _methode_attr("remove"), "hasAttribute": _methode_attr("has"),
    "toggleAttribute": _methode_attr("toggle"),
    "appendChild": lambda el, js, a: (el.anhaengen(_als_knoten(el.dok, a[0])), a[0])[1] if a else UNDEF,
    "append": _anhaengen, "prepend": _voranstellen, "insertBefore": _einfuegen_vor,
    "removeChild": _entfernen_kind, "remove": lambda el, js, a: el.dok.entfernen(el),
    "replaceChildren": _ersetzen_kinder,
    "replaceWith": lambda el, js, a: (el.eltern.anhaengen(_als_knoten(el.dok, a[0]), el), el.dok.entfernen(el))
    if el.eltern is not None and a else UNDEF,
    "cloneNode": _klonen, "querySelector": _suche, "querySelectorAll": lambda el, js, a: _suche(el, js, a, True),
    "getElementsByClassName": lambda el, js, a: js.liste([e for e in el.alle() if all(
        k in e.klassen() for k in zu_text(a[0] if a else "").split())]),
    "getElementsByTagName": lambda el, js, a: js.liste([e for e in el.alle() if
                                                        zu_text(a[0] if a else "").lower() in ("*", e.tag)]),
    "closest": _naechster,
    "matches": lambda el, js, a: nemicss.passt(el, zu_text(a[0]) if a else "", el.dok.zustand),
    "contains": lambda el, js, a: bool(a) and isinstance(a[0], Knoten) and el.enthaelt(a[0]),
    "addEventListener": _hoeren,
    "removeEventListener": lambda el, js, a: el.nicht_mehr_hoeren(zu_text(a[0]), a[1]) if len(a) > 1 else UNDEF,
    "dispatchEvent": lambda el, js, a: el.dok.ausloesen(el, a[0]) if a else False,
    "click": lambda el, js, a: el.dok.klick(el),
    "focus": lambda el, js, a: el.dok.fokus_setzen(el), "blur": lambda el, js, a: el.dok.fokus_setzen(None),
    "select": lambda el, js, a: UNDEF, "scrollIntoView": lambda el, js, a: UNDEF,
    "getBoundingClientRect": _rect,
    "insertAdjacentHTML": lambda el, js, a: _neben_html(el, js, a),
}


def _neben_html(el, js, a):
    wo = zu_text(a[0]).lower() if a else "beforeend"
    neu = fragment(el.dok, zu_text(a[1]) if len(a) > 1 else "")
    if wo == "beforeend":
        for k in neu:
            el.anhaengen(k)
    elif wo == "afterbegin":
        erster = el.kinder[0] if el.kinder else None
        for k in neu:
            el.anhaengen(k, erster)
    elif wo in ("beforebegin", "afterend") and el.eltern is not None:
        ref = el if wo == "beforebegin" else el.naechstes_element()
        for k in neu:
            el.eltern.anhaengen(k, ref)
    return UNDEF


class Klassenliste(Wirt):
    js_klasse = "DOMTokenList"

    def __init__(self, el):
        self.el = el

    def _setzen(self, k):
        self.el.attrs["class"] = " ".join(k)
        self.el.dok.aendern()

    def js_get(self, key, js):
        el = self.el
        k = el.klassen()
        if key == "length":
            return float(len(k))
        if key.isdigit():
            return k[int(key)] if int(key) < len(k) else UNDEF

        def add(js_, t, a):
            self._setzen(k + [zu_text(x) for x in a if zu_text(x) not in k])
            return UNDEF

        def remove(js_, t, a):
            weg = {zu_text(x) for x in a}
            self._setzen([x for x in k if x not in weg])
            return UNDEF

        def toggle(js_, t, a):
            n = zu_text(a[0]) if a else ""
            an = zu_bool(a[1]) if len(a) > 1 else n not in k
            self._setzen([x for x in k if x != n] + ([n] if an else []))
            return an

        def replace(js_, t, a):
            alt, neu = (zu_text(a[0]), zu_text(a[1])) if len(a) > 1 else ("", "")
            if alt in k:
                self._setzen([neu if x == alt else x for x in k])
                return True
            return False
        tabelle = {"add": add, "remove": remove, "toggle": toggle, "replace": replace,
                   "contains": lambda js_, t, a: (zu_text(a[0]) if a else "") in k}
        if key in tabelle:
            return js.nativ(tabelle[key], key)
        if key == "value":
            return " ".join(k)
        return UNDEF


class StilObjekt(Wirt):
    js_klasse = "CSSStyleDeclaration"

    def __init__(self, el):
        self.el = el

    @staticmethod
    def _css_name(key):
        if key.startswith("--"):
            return key
        return re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), key)

    def js_get(self, key, js):
        if key == "setProperty":
            return js.nativ(lambda js_, t, a: self.js_set(zu_text(a[0]), a[1] if len(a) > 1 else "", js_), key)
        if key == "removeProperty":
            return js.nativ(lambda js_, t, a: (self.el.stil_js.pop(self._css_name(zu_text(a[0])), None),
                                               self.el.dok.aendern(), "")[2], key)
        if key == "getPropertyValue":
            return js.nativ(lambda js_, t, a: self.el.stil_js.get(self._css_name(zu_text(a[0])), ""), key)
        if key == "cssText":
            return "; ".join(f"{k}: {v}" for k, v in self.el.stil_js.items())
        return self.el.stil_js.get(self._css_name(key), "")

    def js_set(self, key, wert, js):
        if key == "cssText":
            self.el.stil_js = {n: w for n, w, _i in nemicss.deklarationen(zu_text(wert))}
        else:
            name = self._css_name(key)
            text = zu_text(wert) if wert is not None and wert is not UNDEF else ""
            if isinstance(wert, float) and name not in ("opacity", "z-index", "flex-grow", "flex-shrink", "order",
                                                        "line-height", "font-weight"):
                text = text + "px" if text != "0" else text
            if text:
                self.el.stil_js[name] = text
            else:
                self.el.stil_js.pop(name, None)
        self.el.dok.aendern()


class Datensatz(Wirt):
    js_klasse = "DOMStringMap"

    def __init__(self, el):
        self.el = el

    @staticmethod
    def _attr(key):
        return "data-" + re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), key)

    def js_get(self, key, js):
        return self.el.attrs.get(self._attr(key), UNDEF)

    def js_set(self, key, wert, js):
        self.el.attrs[self._attr(key)] = zu_text(wert)
        self.el.dok.aendern()

    def js_keys(self):
        return [re.sub(r"-(\w)", lambda m: m.group(1).upper(), k[5:]) for k in self.el.attrs if k.startswith("data-")]


class Ereignis(Wirt):
    js_klasse = "Event"

    def __init__(self, typ: str, ziel=None, **felder):
        self.typ, self.ziel, self.felder = typ, ziel, felder
        self.aktuell = None
        self.abgebrochen = False
        self.gestoppt = False

    def js_get(self, key, js):
        if key == "type":
            return self.typ
        if key in ("target", "srcElement"):
            return self.ziel
        if key == "currentTarget":
            return self.aktuell
        if key == "defaultPrevented":
            return self.abgebrochen
        if key == "preventDefault":
            return js.nativ(lambda js_, t, a: setattr(self, "abgebrochen", True), key)
        if key in ("stopPropagation", "stopImmediatePropagation"):
            return js.nativ(lambda js_, t, a: setattr(self, "gestoppt", True), key)
        if key in self.felder:
            return self.felder[key]
        if key in ("bubbles", "cancelable", "isTrusted"):
            return True
        return UNDEF

    def js_set(self, key, wert, js):
        self.felder[key] = wert


# ---------------------------------------------------------------------------
# Dokument
# ---------------------------------------------------------------------------

class Dokument(Element):
    js_klasse = "HTMLDocument"

    def __init__(self, js: NemiJS, ordner: Path | None = None):
        self.js = js
        self.ordner = Path(ordner).resolve() if ordner else None
        self.einstieg = "index.html"
        self.geaendert = True
        self.knoten_zahl = 0
        self.titel = ""
        self.fokus = None
        self.ueber = None                     # Element unter der Maus (:hover)
        self.gedrueckt = None                 # :active
        self.css_texte: list[str] = []
        self.skripte: list[tuple[str, str]] = []
        self.on_geaendert = None
        super().__init__(self, "#document")
        self.dok = self

    def aendern(self):
        self.geaendert = True
        if self.on_geaendert:
            self.on_geaendert()

    def zaehlen(self):
        self.knoten_zahl += 1
        if self.knoten_zahl > MAX_KNOTEN:
            from .nemijs import Grenze
            raise Grenze("Zu viele Elemente im Dokument")

    def zustand(self, el, pseudo) -> bool:
        if pseudo == "hover":
            e = self.ueber
            while e is not None:
                if e is el:
                    return True
                e = e.eltern
            return False
        if pseudo in ("focus", "focus-visible"):
            return el is self.fokus
        if pseudo == "focus-within":
            f = self.fokus
            while f is not None:
                if f is el:
                    return True
                f = f.eltern
            return False
        if pseudo == "active":
            return el is self.gedrueckt
        return False

    @property
    def html_el(self):
        return next((k for k in self.kinder if isinstance(k, Element) and k.tag == "html"), None)

    def finden(self, tag):
        return next((e for e in self.alle() if e.tag == tag), None)

    def entfernen(self, k):
        if k.eltern is not None and k in k.eltern.kinder:
            k.eltern.kinder.remove(k)
        k.eltern = None
        if self.fokus is not None and isinstance(k, Element) and k.enthaelt(self.fokus):
            self.fokus = None
        self.aendern()
        return UNDEF

    def fokus_setzen(self, el):
        if el is self.fokus:
            return UNDEF
        alt, self.fokus = self.fokus, el
        if alt is not None:
            self.ausloesen(alt, Ereignis("blur", alt), blasen=False)
            if getattr(alt, "_geaendert", False):
                alt._geaendert = False
                self.ausloesen(alt, Ereignis("change", alt))
        if el is not None:
            self.ausloesen(el, Ereignis("focus", el), blasen=False)
        self.aendern()
        return UNDEF

    def klick(self, el):
        if "disabled" in el.attrs:
            return UNDEF
        ev = Ereignis("click", el)
        self.ausloesen(el, ev)
        if ev.abgebrochen:
            return UNDEF
        if el.tag == "input" and el.attrs.get("type") in ("checkbox", "radio"):
            if el.attrs.get("type") == "radio":
                for r in self.alle():
                    if r.tag == "input" and r.attrs.get("type") == "radio" and r.attrs.get("name") == el.attrs.get("name"):
                        r.checked = False
                el.checked = True
            else:
                el.checked = not el.checked
            self.ausloesen(el, Ereignis("input", el))
            self.ausloesen(el, Ereignis("change", el))
            self.aendern()
        elif el.tag == "label":
            ziel = self.nach_id(el.attrs.get("for", "")) or next((e for e in el.alle() if e.tag in ("input", "select",
                                                                                                     "textarea")), None)
            if ziel is not None:
                if ziel.tag == "input" and ziel.attrs.get("type") in ("checkbox", "radio"):
                    self.klick(ziel)
                else:
                    self.fokus_setzen(ziel)
        elif el.tag == "button" or (el.tag == "input" and el.attrs.get("type") in ("submit", "button")):
            form = el.eltern
            while form is not None and not (isinstance(form, Element) and form.tag == "form"):
                form = form.eltern
            if form is not None and el.attrs.get("type", "submit") == "submit":
                self.ausloesen(form, Ereignis("submit", form))     # Absenden selbst gibt es nicht (kein Netz)
        return UNDEF

    def ausloesen(self, el, ev, blasen: bool = True):
        """Ereignis an el und (blasen) seine Eltern bis zum Dokument und window. → nicht abgebrochen?"""
        if not isinstance(ev, Ereignis):
            if isinstance(ev, JSObject):
                ev = Ereignis(zu_text(ev.props.get("type", "")), el)
            else:
                return False
        ev.ziel = ev.ziel or el
        pfad = []
        k = el
        while k is not None:
            pfad.append(k)
            k = k.eltern if blasen else None
        if blasen and getattr(self, "fenster", None) is not None:
            pfad.append(self.fenster)
        for knoten in pfad:
            ev.aktuell = knoten
            hoerer = list(getattr(knoten, "hoerer", {}).get(ev.typ, []))
            for fn, einmal in hoerer:
                if einmal:
                    knoten.nicht_mehr_hoeren(ev.typ, fn)
                self.js.rufen(fn, knoten, [ev])
            if ev.gestoppt:
                break
        self.js.mikro_abarbeiten()
        return not ev.abgebrochen

    def nach_id(self, i):
        if not i:
            return None
        return next((e for e in self.alle() if e.attrs.get("id") == i), None)

    # -- Dateien aus dem App-Ordner --------------------------------------------------
    def datei(self, pfad: str, max_bytes: int = 5_000_000) -> bytes | None:
        """Datei relativ zum App-Ordner – nichts außerhalb, keine Adressen (http, file, data …)."""
        if self.ordner is None or not pfad or re.match(r"^[a-z][a-z0-9+.-]*:", pfad, re.I) or pfad.startswith(("/", "\\")):
            return None
        ziel = (self.ordner / pfad.split("?")[0].split("#")[0]).resolve()
        if not ziel.is_relative_to(self.ordner) or not ziel.is_file() or ziel.stat().st_size > max_bytes:
            return None
        return ziel.read_bytes()

    # -- JavaScript ------------------------------------------------------------------
    def js_get(self, key, js):
        if key == "body":
            return self.finden("body")
        if key == "head":
            return self.finden("head")
        if key == "documentElement":
            return self.html_el
        if key == "title":
            return self.titel
        if key == "activeElement":
            return self.fokus or self.finden("body")
        if key == "readyState":
            return "complete"
        if key == "getElementById":
            return js.nativ(lambda js_, t, a: self.nach_id(zu_text(a[0]) if a else ""), key)
        if key == "createElement":
            return js.nativ(lambda js_, t, a: self._neu(zu_text(a[0]) if a else "div"), key)
        if key == "createTextNode":
            return js.nativ(lambda js_, t, a: Text(self, zu_text(a[0]) if a else ""), key)
        if key == "createDocumentFragment":
            return js.nativ(lambda js_, t, a: Element(self, "#fragment"), key)
        if key == "createEvent":
            return js.nativ(lambda js_, t, a: Ereignis(""), key)
        if key in ("cookie", "domain", "referrer", "URL", "location"):
            return "" if key != "location" else js.global_scope.vars.get("location", UNDEF)
        return super().js_get(key, js)

    def js_set(self, key, wert, js):
        if key == "title":
            self.titel = zu_text(wert)
            self.aendern()
        elif key == "cookie":
            return                                              # keine Cookies
        else:
            super().js_set(key, wert, js)

    def _neu(self, tag):
        tag = tag.lower().strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]*", tag):
            raise JSFehler(self.js.fehler("SyntaxError", f"Ungültiger Tag „{tag}“"))
        self.zaehlen()
        return Element(self, tag)


# ---------------------------------------------------------------------------
# HTML lesen
# ---------------------------------------------------------------------------

class _Leser(HTMLParser):
    def __init__(self, dok: Dokument, wurzel: Element, fragment: bool):
        super().__init__(convert_charrefs=True)
        self.dok, self.stapel, self.fragment = dok, [wurzel], fragment
        self.in_roh = None
        self.roh: list[str] = []

    @property
    def oben(self):
        return self.stapel[-1]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if self.fragment:
            if tag in ("script", "style", "link", "iframe", "object", "embed", "base", "meta"):
                if tag in ("script", "style"):
                    self.in_roh, self.roh = "weg", []
                return
            a = {k: v for k, v in a.items() if not k.startswith("on")}      # nie Code aus innerHTML
        if tag in ("p", "li", "option") and self.oben.tag == tag:
            self.stapel.pop()
        el = Element(self.dok, tag, a)
        self.dok.zaehlen()
        el.eltern = self.oben
        self.oben.kinder.append(el)
        if tag in ("script", "style"):
            self.in_roh, self.roh = el, []
            self.stapel.append(el)
            return
        if tag == "textarea":
            self.stapel.append(el)
            return
        if tag not in LEER_TAGS:
            self.stapel.append(el)
        if tag == "link" and "stylesheet" in a.get("rel", "").lower():
            daten = self.dok.datei(a.get("href", ""))
            if daten is not None:
                self.dok.css_texte.append(daten.decode("utf-8", "replace"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in LEER_TAGS and self.oben.tag == tag.lower():
            self.stapel.pop()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.in_roh is not None and tag in ("script", "style"):
            text = "".join(self.roh)
            el = self.in_roh
            self.in_roh = None
            if el == "weg":
                return
            if tag == "style":
                self.dok.css_texte.append(text)
            elif tag == "script" and not self.fragment:
                if el.attrs.get("src"):
                    daten = self.dok.datei(el.attrs["src"], 1_000_000)
                    if daten is not None:
                        self.dok.skripte.append((daten.decode("utf-8", "replace"), el.attrs["src"]))
                elif el.attrs.get("type", "text/javascript") in ("text/javascript", "module", ""):
                    self.dok.skripte.append((text, self.dok.einstieg))
            if self.oben is el:
                self.stapel.pop()
            return
        for i in range(len(self.stapel) - 1, 0, -1):
            if self.stapel[i].tag == tag:
                del self.stapel[i:]
                return

    def handle_data(self, data):
        if self.in_roh is not None:
            self.roh.append(data)
            return
        if self.oben.tag == "textarea":
            self.oben.wert += data
            return
        if self.oben.tag == "title":
            self.dok.titel += data
            return
        if not data:
            return
        t = Text(self.dok, data)
        t.eltern = self.oben
        self.oben.kinder.append(t)


def laden(js: NemiJS, quelle: str, ordner: Path | None = None, einstieg: str = "index.html") -> Dokument:
    """index.html → Dokument (mit html/head/body, auch wenn sie fehlen); Stile und Skripte gesammelt."""
    dok = Dokument(js, ordner)
    dok.einstieg = einstieg
    leser = _Leser(dok, dok, False)
    leser.feed(quelle)
    leser.close()
    html_el = dok.html_el
    if html_el is None:                                    # Bruchstück ohne <html>: Gerüst darum bauen
        html_el = Element(dok, "html")
        oben = list(dok.kinder)
        kopf = next((k for k in oben if isinstance(k, Element) and k.tag == "head"), None) or Element(dok, "head")
        koerper = next((k for k in oben if isinstance(k, Element) and k.tag == "body"), None) or Element(dok, "body")
        for k in oben:
            if k is not kopf and k is not koerper:
                k.eltern = koerper
                koerper.kinder.append(k)
        dok.kinder = [html_el]
        html_el.eltern = dok
        html_el.kinder = []
        for k in (kopf, koerper):
            k.eltern = html_el
            html_el.kinder.append(k)
    elif dok.finden("body") is None:
        koerper = Element(dok, "body")
        for k in [k for k in html_el.kinder if not (isinstance(k, Element) and k.tag == "head")]:
            html_el.kinder.remove(k)
            k.eltern = koerper
            koerper.kinder.append(k)
        koerper.eltern = html_el
        html_el.kinder.append(koerper)
    for el in dok.alle():                                 # on…-Attribute aus der Datei → Hörer
        for name, code in list(el.attrs.items()):
            if name.startswith("on") and len(name) > 2 and code.strip():
                el.hoeren(name[2:], _attr_hoerer(js, code, f"{el.tag}.{name}"))
    dok.titel = dok.titel.strip()
    dok.geaendert = True
    return dok


def _attr_hoerer(js: NemiJS, code: str, name: str):
    from .nemijs_parser import parsen
    try:
        knoten = parsen(f"(function (event) {{\n{code}\n}})")
        f = js.wert(knoten["body"][0]["e"], js.global_scope)
    except Exception:
        return js.nativ(lambda js_, t, a: js_.konsole.append(f"❌ {name}: Code im Attribut nicht lesbar"), name)
    return f


def fragment(dok: Dokument, quelle: str) -> list[Knoten]:
    halter = Element(dok, "#fragment")
    leser = _Leser(dok, halter, True)
    leser.feed(quelle)
    leser.close()
    kinder = list(halter.kinder)
    for k in kinder:
        k.eltern = None
    return kinder


_ = (UNDEF, zu_zahl)
