"""
denkweise.py - Ob die KI nachdenkt, je Persönlichkeit (/denken, Werkzeug denkweise).

  auto  (Vorgabe) nachdenken, wenn es etwas bringt: bei kniffligen Fragen (Denkwerk) und bei Bitten, etwas
        zu tun – dort plant das Modell den Werkzeug-Aufruf, statt ihn nur anzukündigen. Plaudern bleibt schnell.
  an    jede Antwort mit Nachdenken.
  aus   nie nachdenken, so schnell wie möglich.

Gilt für jedes Modell: Modelle mit schaltbarem Nachdenken (eigener Motor) bekommen es eingeschaltet, alle
anderen den Denkanstoß des Denkwerks. Wie tief gedacht wird, regelt /staerke.

Config `denkweise`: {Persönlichkeits-Kennung: stufe}; fehlt der Eintrag, gilt auto. Die alte Config
`gguf_denken: true` zählt als „an“.
"""
from __future__ import annotations

STUFEN = ("auto", "an", "aus")
VORGABE = "auto"
_ALIAS = {"automatisch": "auto", "standard": "auto", "frei": "auto", "bedarf": "auto", "ein": "an", "immer": "an",
          "on": "an", "true": "an", "ja": "an", "off": "aus", "false": "aus", "nie": "aus", "nein": "aus"}
TEXT = {"auto": "🧭 auto – denkt nach, wenn es etwas bringt (knifflige Fragen, Bitten etwas zu tun)",
        "an": "🧠 an – denkt vor jeder Antwort nach",
        "aus": "⚡ aus – antwortet sofort, ohne Nachdenken"}


def normal(wert) -> str | None:
    w = str(wert or "").strip().lower()
    w = _ALIAS.get(w, w)
    return w if w in STUFEN else None


def _kennung() -> str:
    import config
    import persoenlichkeiten as PS
    return str(config.load().get(PS.CONFIG_KEY) or PS.NEMI_KEY)


def stufe(kennung: str | None = None) -> str:
    """Die Stufe der (aktiven) Persönlichkeit."""
    try:
        import config
        cfg = config.load()
        eintraege = cfg.get("denkweise") if isinstance(cfg.get("denkweise"), dict) else {}
        s = normal(eintraege.get(kennung or _kennung()))
        if s:
            return s
        return "an" if cfg.get("gguf_denken") is True else VORGABE
    except Exception:
        return VORGABE


def setzen(wert, kennung: str | None = None) -> str:
    """Speichert die Stufe für die (aktive) Persönlichkeit. ValueError bei unbekanntem Wert."""
    w = normal(wert)
    if w is None:
        raise ValueError(f"Unbekannte Denkweise {wert!r}. Möglich: {', '.join(STUFEN)}.")
    import config
    cfg = config.load()
    eintraege = dict(cfg.get("denkweise") or {}) if isinstance(cfg.get("denkweise"), dict) else {}
    eintraege[kennung or _kennung()] = w
    if not config.update(denkweise=eintraege):
        raise ValueError("Die Einstellung ließ sich nicht speichern.")
    return w


def nativ(fmt, runde, s: str | None = None) -> bool:
    """Schaltbares Nachdenken des Modells (Chat-Format des eigenen Motors) für diese Runde einschalten?
    Steht der Schalter in der Anleitung selbst (Bailing, Harmony, Gemma 4 …), bleibt er unter auto an:
    Umschalten je Nachricht hieße, die ganze Anleitung neu einzulesen. Ebenso bei Modellen, die ohne
    Nachdenken unzuverlässig antworten (`denkt_immer`)."""
    if not getattr(fmt, "denken_schaltbar", False):
        return False
    s = s or stufe()
    if s == "aus":
        return False
    if s == "an" or getattr(fmt, "denken_im_system", False) or getattr(fmt, "denkt_immer", False):
        return True
    return runde is not None and (runde.stufe != "einfach" or bool(getattr(runde, "handlung", False)))


def vorgabe(fmt, s: str | None = None) -> bool:
    """Der Schalter, wenn noch keine Runde läuft (Vorlesen der nächsten Anleitung, Nebenfragen)."""
    s = s or stufe()
    if not getattr(fmt, "denken_schaltbar", False) or s == "aus":
        return False
    return s == "an" or bool(getattr(fmt, "denken_im_system", False) or getattr(fmt, "denkt_immer", False))
