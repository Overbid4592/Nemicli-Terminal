"""
appjs.py - Funktionen einer NemiCLI-App ohne Fenster aufrufen (für das Werkzeug app_aufrufen).

app.js läuft in NemiJS mit einem leeren `document` (getElementById → null): Apps melden ihre Funktionen mit
NemiApp.funktion(name, fn, beschreibung) an und fassen das DOM nur an, wenn es da ist. Kein Browser, kein Netz,
keine Dateien außer dem eigenen Skript; Rechen- und Zeitgrenzen wie überall in NemiJS.
"""
from __future__ import annotations

from pathlib import Path

from .nemijs import UNDEF, Grenze, JSFehler, JSObject, NemiJS, Wirt, ist_aufrufbar, zu_text

MAX_SKRIPT = 1_000_000
SCHRITTE_AUFRUF = 3_000_000
ZEIT_VIRTUELL_MS = 10_000          # so weit dürfen Zeitgeber (setTimeout) beim Warten vorspringen


class _LeeresDokument(Wirt):
    """Ohne Fenster gibt es keine Elemente: Suchen liefert null bzw. leere Listen, Anmelden tut nichts."""
    js_klasse = "Document"

    def js_get(self, key, js):
        def nichts(_js, _t, _a):
            return None

        def leer(_js, _t, _a):
            return _js.liste([])

        def nop(_js, _t, _a):
            return UNDEF
        tabelle = {"getElementById": nichts, "querySelector": nichts, "querySelectorAll": leer,
                   "getElementsByClassName": leer, "getElementsByTagName": leer, "addEventListener": nop,
                   "removeEventListener": nop}
        if key in tabelle:
            return js.nativ(tabelle[key], key)
        if key in ("body", "documentElement", "head", "activeElement"):
            return None
        if key == "readyState":
            return "complete"
        if key == "title":
            return ""
        return UNDEF

    def js_set(self, key, wert, js):
        pass


def nemiapp_objekt(js: NemiJS, funktionen: dict) -> JSObject:
    def anmelden(js_, t, a):
        name = zu_text(a[0]) if a else ""
        fn = a[1] if len(a) > 1 else UNDEF
        if not name or not ist_aufrufbar(fn):
            js_.werfen("TypeError", "NemiApp.funktion(name, funktion, beschreibung)")
        funktionen[name] = (fn, zu_text(a[2]) if len(a) > 2 and a[2] is not UNDEF else "")
        return fn

    obj = js.objekt()
    obj.props["funktion"] = js.nativ(anmelden, "funktion")
    obj.props["funktionen"] = js.objekt()
    return obj


def laden(app_js: Path, js: NemiJS | None = None, dokument=None, storage: dict | None = None):
    """(js, funktionen) – app.js ausgeführt, Funktionen angemeldet. Wirft JSFehler/Grenze/OSError."""
    js = js or NemiJS(virtuelle_zeit=True, max_schritte=SCHRITTE_AUFRUF)
    funktionen: dict = {}
    js.global_scope.vars["NemiApp"] = nemiapp_objekt(js, funktionen)
    js.global_scope.vars["document"] = dokument if dokument is not None else _LeeresDokument()
    js.storage = storage
    if app_js.is_file():
        if app_js.stat().st_size > MAX_SKRIPT:
            raise OSError("app.js ist zu groß (höchstens 1 MB)")
        js.ausfuehren(app_js.read_text(encoding="utf-8", errors="replace"), app_js.name)
    return js, funktionen


def aufrufen(app_js: Path, funktion: str, felder: dict, storage: dict | None = None) -> tuple[bool, object]:
    """(ok, Ergebnis als Python-Wert bzw. Fehlertext)."""
    try:
        js, funktionen = laden(app_js, storage=storage)
    except (JSFehler, Grenze) as e:
        return False, f"Fehler beim Laden von app.js: {e}"
    except OSError as e:
        return False, str(e)
    if funktion not in funktionen:
        return False, "Funktion nicht angemeldet (NemiApp.funktion fehlt in app.js)"
    fn = funktionen[funktion][0]
    try:
        erg = js.aufrufen(fn, UNDEF, [js.json_von_py(felder or {})])
        from .nemijs import Promise
        if isinstance(erg, Promise):
            js.einstieg()
            erg = js.warten_auf(erg)
        js.zeitgeber_laufen(ZEIT_VIRTUELL_MS)
        return True, js.json_nach_py(erg) if erg is not UNDEF else None
    except JSFehler as e:
        return False, str(e)
    except Grenze as e:
        return False, str(e)
