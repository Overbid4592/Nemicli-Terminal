"""
laufzeit.py - eine NemiCLI-App in NemiView: index.html lesen, Stile sammeln, window/document/NemiApp
bereitstellen, Skripte ausführen, localStorage als Datei je App.

Der Gastgeber (Fenster oder Test) gibt Rückrufe für alert/confirm/prompt, Neu-Laden und Konsole. Alles, was
JavaScript erreichen kann, steht hier oder in nemidom/nemijs_eingebaut – sonst nichts.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import nemicss, nemidom
from .appjs import nemiapp_objekt
from .nemijs import UNDEF, Grenze, JSFehler, JSObject, NemiJS, Wirt, ist_aufrufbar, zu_text

MAX_INDEX = 2_000_000
STORAGE_MAX = 1_000_000


class Fenster(Wirt):
    """window: Globale Namen plus Größe, Ereignisse (resize, keydown …), Dialoge, matchMedia."""
    js_klasse = "Window"

    def __init__(self, lz: "AppLaufzeit"):
        self.lz = lz
        self.hoerer: dict[str, list] = {}
        self.eltern = None

    def hoeren(self, typ, fn, einmal=False):
        self.hoerer.setdefault(typ, []).append((fn, einmal))

    def nicht_mehr_hoeren(self, typ, fn):
        self.hoerer[typ] = [(f, e) for f, e in self.hoerer.get(typ, []) if f is not fn]

    def js_get(self, key, js):
        lz = self.lz
        if key in ("innerWidth", "outerWidth"):
            return float(lz.breite)
        if key in ("innerHeight", "outerHeight"):
            return float(lz.hoehe)
        if key == "devicePixelRatio":
            return 1.0
        if key in ("scrollX", "scrollY", "pageXOffset", "pageYOffset"):
            return float(lz.scroll_y) if key in ("scrollY", "pageYOffset") else 0.0
        if key == "addEventListener":
            return js.nativ(lambda js_, t, a: self.hoeren(zu_text(a[0]), a[1]) if len(a) > 1 and ist_aufrufbar(a[1])
                            else UNDEF, key)
        if key == "removeEventListener":
            return js.nativ(lambda js_, t, a: self.nicht_mehr_hoeren(zu_text(a[0]), a[1]) if len(a) > 1 else UNDEF, key)
        if key == "document":
            return lz.dok
        return js.global_scope.vars.get(key, UNDEF)

    def js_set(self, key, wert, js):
        if key.startswith("on") and ist_aufrufbar(wert):
            self.hoeren(key[2:], wert)
            return
        js.global_scope.vars[key] = wert


class AppLaufzeit:
    def __init__(self, ordner: Path, breite: int = 1000, hoehe: int = 700, dunkel: bool = True,
                 speicher_ordner: Path | None = None, gastgeber=None, virtuelle_zeit: bool = False,
                 einstieg: str = "index.html"):
        self.ordner = Path(ordner).resolve()
        self.einstieg = einstieg
        self.breite, self.hoehe, self.dunkel = breite, hoehe, dunkel
        self.scroll_y = 0.0
        self.speicher_datei = (Path(speicher_ordner) / f"{self.ordner.name}.json") if speicher_ordner else None
        self.gastgeber = gastgeber
        self.js = NemiJS(virtuelle_zeit=virtuelle_zeit)
        self.funktionen: dict = {}
        self.fehler: list[str] = []
        self.kaskade: nemicss.Kaskade | None = None
        self.dok: nemidom.Dokument | None = None
        self.fenster = Fenster(self)

    # -- Laden ----------------------------------------------------------------------
    def laden(self) -> None:
        index = self.ordner / self.einstieg
        if not index.is_file() or index.stat().st_size > MAX_INDEX:
            raise OSError(f"{self.einstieg} fehlt oder ist zu groß")
        js = self.js
        self.dok = nemidom.laden(js, index.read_text(encoding="utf-8", errors="replace"), self.ordner, self.einstieg)
        self.dok.fenster = self.fenster
        self._globale(js)
        self.kaskade = nemicss.Kaskade(self.dok.css_texte, self.breite, self.dunkel)
        for code, name in self.dok.skripte:
            try:
                js.ausfuehren(code, name)
            except (JSFehler, Grenze) as e:
                self.melden(f"❌ {name}: {e}")
        for typ in ("DOMContentLoaded", "load"):
            ziel = self.dok if typ == "DOMContentLoaded" else None
            self._ereignis_global(typ, ziel)

    def _globale(self, js: NemiJS):
        g = js.global_scope.vars
        g["document"] = self.dok
        g["window"] = g["globalThis"] = g["self"] = self.fenster
        g["NemiApp"] = nemiapp_objekt(js, self.funktionen)
        js.storage = self._speicher_lesen()
        js.storage_max = STORAGE_MAX
        js.storage_geaendert = self._speicher_schreiben

        def dialog(art):
            def f(js_, t, a):
                text = zu_text(a[0]) if a else ""
                vorgabe = zu_text(a[1]) if len(a) > 1 else ""
                if self.gastgeber is not None and hasattr(self.gastgeber, "dialog"):
                    return self.gastgeber.dialog(art, text, vorgabe)
                self.melden(f"[{art}] {text}")
                return {"alert": UNDEF, "confirm": False, "prompt": None}[art]
            return f
        for art in ("alert", "confirm", "prompt"):
            g[art] = js.nativ(dialog(art), art)

        def neu_laden(js_, t, a):
            if self.gastgeber is not None and hasattr(self.gastgeber, "neu_laden"):
                self.gastgeber.neu_laden()
            return UNDEF
        g["location"] = js.objekt({"href": f"nemiapp://{self.ordner.name}/", "hash": "", "search": "",
                                   "pathname": "/", "protocol": "nemiapp:", "host": self.ordner.name,
                                   "reload": js.nativ(neu_laden, "reload")})
        g["navigator"] = js.objekt({"language": "de-DE", "languages": js.liste(["de-DE", "de"]),
                                    "userAgent": "NemiView", "onLine": False, "platform": "NemiView"})

        def match_media(js_, t, a):
            q = zu_text(a[0]) if a else ""
            try:
                treffer = nemicss.media_passt(q.strip().removeprefix("@media"), self.breite, self.dunkel)
            except Exception:
                treffer = False
            return js_.objekt({"matches": treffer, "media": q,
                               "addEventListener": js_.nativ(lambda *x: UNDEF, "addEventListener"),
                               "removeEventListener": js_.nativ(lambda *x: UNDEF, "removeEventListener")})
        g["matchMedia"] = js.nativ(match_media, "matchMedia")

        def berechneter_stil(js_, t, a):
            el = a[0] if a else None
            stil = dict(getattr(el, "stil", {}) or {})

            class Berechnet(Wirt):
                js_klasse = "CSSStyleDeclaration"

                def js_get(self, key, js2):
                    import re
                    name = re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), key)
                    if key == "getPropertyValue":
                        return js2.nativ(lambda j, tt, aa: stil.get(zu_text(aa[0]) if aa else "", ""), key)
                    return stil.get(name, "")
            return Berechnet()
        g["getComputedStyle"] = js.nativ(berechneter_stil, "getComputedStyle")

        def ereignis_neu(js_, a):
            typ = zu_text(a[0]) if a else ""
            felder = {}
            if len(a) > 1 and isinstance(a[1], JSObject):
                felder = dict(a[1].props)
            return nemidom.Ereignis(typ, None, **felder)
        for name in ("Event", "CustomEvent", "KeyboardEvent", "MouseEvent", "InputEvent"):
            g[name] = js.nativ(lambda js_, t, a: js_.werfen("TypeError", "Event braucht „new“"), name)
            g[name].konstruktor = ereignis_neu

    def _ereignis_global(self, typ, ziel=None):
        ev = nemidom.Ereignis(typ, ziel)
        try:
            if ziel is not None:
                self.dok.ausloesen(ziel, ev)
            else:
                for fn, _e in list(self.fenster.hoerer.get(typ, [])):
                    self.js.einstieg()
                    self.js.rufen(fn, self.fenster, [ev])
                self.js.mikro_abarbeiten()
        except (JSFehler, Grenze) as e:
            self.melden(f"❌ {typ}: {e}")

    # -- Speicher -----------------------------------------------------------------------
    def _speicher_lesen(self) -> dict:
        if self.speicher_datei is None or not self.speicher_datei.is_file():
            return {}
        try:
            d = json.loads(self.speicher_datei.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _speicher_schreiben(self, daten: dict) -> None:
        if self.speicher_datei is None:
            return
        try:
            self.speicher_datei.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.speicher_datei.with_suffix(".tmp")
            tmp.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.speicher_datei)
        except OSError:
            pass

    # -- Ablauf ---------------------------------------------------------------------------
    def melden(self, text: str) -> None:
        self.fehler.append(text[:500])
        del self.fehler[:-200]
        self.js.konsole.append(text[:500])

    def sicher(self, fn, *args):
        """Ein Einstieg von außen (Ereignis, Zeitgeber): Fehler landen in der Konsole, nie im Fenster-Code."""
        try:
            self.js.einstieg()
            return fn(*args)
        except (JSFehler, Grenze) as e:
            self.melden(f"❌ {e}")
        return None

    def ausloesen(self, el, typ: str, **felder) -> bool:
        ev = nemidom.Ereignis(typ, el, **felder)
        erg = self.sicher(self.dok.ausloesen, el, ev)
        return bool(erg)

    def groesse(self, breite: int, hoehe: int) -> None:
        if (breite, hoehe) == (self.breite, self.hoehe):
            return
        self.breite, self.hoehe = breite, hoehe
        if self.kaskade is not None:
            self.kaskade.breite = breite
        self.dok.aendern()
        self._ereignis_global("resize")

    def zeitgeber(self) -> None:
        self.sicher(self.js.faellige_ausfuehren)
