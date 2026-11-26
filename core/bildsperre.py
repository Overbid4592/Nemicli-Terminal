"""
bildsperre.py - Feste Grenzen für jedes Bild und jedes Video, unabhängig davon, wer den Prompt schreibt
(Persönlichkeit, Nutzer, Serie, Comic) und welcher Motor malt (Krea 2, eigene Bild-Motoren, WebUI, ComfyUI,
Cloud, Video). Es gibt keinen Schalter und keine Ausnahme.

    1. Alle im Bild sind erwachsen (ab 18): Wörter für Kinder werden zur erwachsenen Entsprechung, ein Alter
       unter 18 und eindeutige Begriffe sperren.
    2. Keinerlei sexuelle Darstellung Minderjähriger: ein Wort für Kinder zusammen mit einem sexuellen Wort sperrt.
    3. Kein Gore und kein Krieg: Blut, Wunden, Verstümmelung, Leichen, Kriegs- und Kampfszenen sperren.

Gesperrt wird mit `Gesperrt` (RuntimeError) – der Motor malt dann nichts. Der Gegen-Prompt verliert Wörter, die
„erwachsen“ abwählen würden; Motoren mit Gegen-Prompt hängen FESTER_GEGENPROMPT an.
"""

from __future__ import annotations

import re

MELDUNG = ("🔒 Bildsperre: {grund}. Diese Grenze ist fest eingebaut – kein Motor malt das, auch nicht umformuliert. "
           "Nicht erneut versuchen.")

# Motoren mit Gegen-Prompt bekommen das immer dazu (nicht abschaltbar).
FESTER_GEGENPROMPT = ("child, children, kid, minor, underage, teen, teenager, young girl, young boy, loli, "
                      "childlike body, gore, blood, wound, corpse")


class Gesperrt(RuntimeError):
    """Der Prompt verstößt gegen eine feste Grenze – es wird nicht gemalt."""


def _wortliste(*woerter: str) -> re.Pattern:
    return re.compile(r"(?i)(?<![\w-])(?:" + "|".join(woerter) + r")(?![\w-])")


# --- 1. Erwachsen ------------------------------------------------------------------------------------------------
# Wird zur erwachsenen Entsprechung (häufig auch für Erwachsene benutzt: „girl“ für junge Frauen).
_ERSETZEN = {"girl": "woman", "girls": "women", "boy": "man", "boys": "men", "kid": "adult", "kids": "adults",
             "child": "adult", "children": "adults", "teen": "adult", "teens": "adults", "teenager": "adult",
             "teenagers": "adults", "schoolgirl": "woman", "schoolgirls": "women", "schoolboy": "man",
             "schoolboys": "men", "mädchen": "Frau", "toddler": "adult", "toddlers": "adults"}
_ERSETZEN_RE = _wortliste(*sorted(_ERSETZEN, key=len, reverse=True))
# Keine erwachsene Lesart: immer gesperrt.
_IMMER = _wortliste(r"loli\w*", r"shota\w*", r"under[- ]?age\w*", r"pre[- ]?teens?", r"pre[- ]?pubescent",
                    r"pubescent", "jailbait", r"minderj[äa]hrig\w*", r"kinderporn\w*", r"child[- ]?porn\w*",
                    r"kinderpornogra\w*", "minors")
# Weisen auf ein Kind hin (auch ohne Ersatz) – zusammen mit Sexuellem gesperrt.
# „girl“/„boy“/„Mädchen“ allein meinen oft Erwachsene – die werden nur ersetzt; mit „little/young“ zählen sie.
_KIND = _wortliste(*(k for k in _ERSETZEN if k not in ("girl", "girls", "boy", "boys", "mädchen")),
                   r"(?:little|young|small|tiny)\s+(?:girls?|boys?)", r"(?:kleine|junge)s?\s+mädchen",
                   "baby", "babies", "infant", "infants", "kinder", r"kleinkind\w*", r"schüler\w*",
                   r"jugendlich\w*", "minor", "little one", "elementary school", "middle school", r"grundschul\w*",
                   r"childlike", r"child-like", r"kindlich\w*", r"flat[- ]chested", "young-looking")
_SEXUELL = _wortliste(r"nude\w*", "naked", "nudity", "sex", r"sexual\w*", "sexy", r"erotic\w*", r"porn\w*", "nsfw",
                      "explicit", "topless", "bottomless", r"breasts?", r"boobs?", r"nipples?", r"genital\w*", "penis",
                      "vagina", "pussy", r"orgasm\w*", r"masturbat\w*", "cum", "lingerie", "underwear", r"panties",
                      "bra", "bikini", "seductive", "lewd", "hentai", "ecchi", "aroused", "spread legs", r"nackt\w*",
                      r"sexuell\w*", r"erotisch\w*", r"brüste?", r"nippel\w*", r"unterwäsche", r"höschen", r"lasziv\w*",
                      r"intim\w*", "oben ohne", "unbekleidet")
# Alter unter 18: „16 year old“, „16-year-old“, „16yo“, „aged 16“, „16 Jahre alt“, „16-jährig“.
_ALTER = re.compile(
    r"(?i)(?<!\d)(\d{1,2})\s*(?:-|\s)?\s*(?:years?[\s-]*old|yrs?[\s-]*old|y/?o\b|jahre?\s+alt|j[äa]hrig\w*)"
    r"|(?:\baged?|\balter\s+von|\bim\s+alter\s+von)\s+(\d{1,2})(?!\d)")
# Zieht im Gegen-Prompt „erwachsen“ ab – fliegt raus.
_NEG_ERWACHSEN = _wortliste("adult", "adults", "mature", r"grown[- ]?up", r"erwachsen\w*", "woman", "women", "man",
                            "men", "aged", "old")

# --- 3. Gore und Krieg -------------------------------------------------------------------------------------------
_GORE_AUSNAHMEN = re.compile(r"(?i)blood[- ]?(?:red|orange|moon)|blut(?:rot|orange|mond)|star\s+wars|war\s*paint"
                             r"|combat\s+boots|wound\s+up")
_GORE = _wortliste("gore", "gory", "blood", "bloody", "bleeding", "bloodied", "bloodbath", r"wounds?", "wounded",
                   r"gash(?:es)?", "guts", "entrails", r"disembowel\w*", r"dismember\w*", r"decapitat\w*", "beheaded",
                   r"mutilat\w*", "severed", r"corpses?", "cadavers?", r"dead\s+bod(?:y|ies)", r"gunshot\w*",
                   r"stabbed", "stabbing", "massacre", r"slaughter\w*", "carnage", r"tortur\w*",
                   # „war“ allein ist auch deutsch („es war“) – nur eindeutige Wendungen
                   r"(?:at|in\s+(?:a|the)|during\s+(?:a|the))\s+war", r"war[- ](?:torn|scenes?|zones?|photo\w*|time"
                   r"|crimes?|victims?|footage)", "wartime", "warfare", "warzone", r"(?:world|civil|nuclear)\s+wars?",
                   "battlefield", "battleground", "gunfire", "firefight",
                   r"shooting\s+at", r"firing\s+(?:a|the|his|her)\s+(?:gun|rifle|weapon|pistol)", "airstrike",
                   r"blut", r"blutig\w*", r"blutend\w*", r"wunden?", r"verwund\w*", r"verstümmel\w*", r"leichen?",
                   r"leichnam\w*", r"krieg", r"kriegs\w*", "schlachtfeld", "massaker", r"gemetzel", r"folter\w*",
                   r"enthaupt\w*", r"erschossen\w*", r"erstochen\w*")


def _text(t) -> str:
    return " ".join(str(t or "").split())


def grund(prompt: str) -> str | None:
    """Warum dieser Prompt gesperrt ist – None, wenn er gemalt werden darf (nach der Ersetzung)."""
    p = _text(prompt)
    if _IMMER.search(p):
        return "Minderjährige sind ausgeschlossen"
    for m in _ALTER.finditer(p):
        zahl = int(m.group(1) or m.group(2))
        if zahl < 18:
            return "alle im Bild sind erwachsen (ab 18)"
    if _KIND.search(p) and _SEXUELL.search(p):
        return "keinerlei sexuelle Darstellung Minderjähriger"
    if _GORE.search(_GORE_AUSNAHMEN.sub(" ", p)):
        return "kein Gore und kein Krieg"
    return None


def erwachsen(prompt: str) -> str:
    """Wörter für Kinder zur erwachsenen Entsprechung (Groß-/Kleinschreibung bleibt)."""
    def ersetzen(m):
        neu = _ERSETZEN[m.group(0).lower()]
        return neu[:1].upper() + neu[1:] if m.group(0)[:1].isupper() else neu
    return _ERSETZEN_RE.sub(ersetzen, str(prompt or ""))


def pruefen(prompt: str, neg: str | None = None) -> tuple[str, str | None]:
    """Prompt und Gegen-Prompt vor dem Malen: wirft Gesperrt, sonst (Prompt, Gegen-Prompt) bereinigt.
    Gesperrt wird am Original – ein ersetztes Wort für Kinder zählt mit Sexuellem trotzdem."""
    if (g := grund(prompt)) is not None:
        raise Gesperrt(MELDUNG.format(grund=g))
    sauber_neg = None
    if neg:
        sauber_neg = re.sub(r"\s*,\s*(?:,\s*)+", ", ", _NEG_ERWACHSEN.sub("", str(neg))).strip(" ,") or None
    return erwachsen(prompt), sauber_neg


def gegenprompt(neg: str | None) -> str:
    """Gegen-Prompt mit dem festen Teil (für Motoren, die einen nehmen)."""
    return ", ".join(t for t in (_text(neg), FESTER_GEGENPROMPT) if t)
