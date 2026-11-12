"""webanzeige.py - was auf dem Bildschirm steht, wenn die KI im Netz sucht oder liest.

Die KI bekommt weiter den vollen, gerahmten Text aus webfetch/fachquellen. Hier entstehen nur kleine
Anzeige-Daten (Titel, Domain, Ampel, kurzer Auszug) für Terminal und Desktop-Fenster - ohne rich und ohne Qt.
Alles davon stammt aus dem Netz: Steuer- und unsichtbare Zeichen fallen weg, Längen sind begrenzt, und die
Oberflächen zeigen es nur als reinen Text.
"""
from __future__ import annotations

import unicodedata
from urllib.parse import urlparse

WERKZEUGE = ("web_suche", "web_lesen", "web_wiki", "fach_suche")
FREMD_TIER = "fremd"                        # wie webfetch.FREMD_TIER (hier ohne httpx-Import)
TITEL_MAX = 90
AUSZUG_MAX = 110
TREFFER_MAX = 10
RADAR = "◐◓◑◒"

# Ampel: Zeichen und Bedeutung - gleich in Terminal und Fenster
AMPEL = {
    "gruen": ("🟢", "lesbar"),
    "gelb": ("🟡", "unbekannte Seite"),
    "grau": ("⚪", "nur Schnipsel"),
    "warn": ("⚠", "wollte schon einmal steuern"),
}


def sauber(text, grenze: int) -> str:
    """Eine Zeile reiner Text: ohne Steuer-, Format- und unsichtbare Zeichen, höchstens `grenze` Zeichen."""
    roh = "".join(z if unicodedata.category(z)[0] != "C" else " " for z in str(text or ""))
    zeile = " ".join(roh.split())
    if len(zeile) <= grenze:
        return zeile
    schnitt = zeile[:grenze - 1]
    if (pos := schnitt.rfind(" ")) > grenze * 0.6:
        schnitt = schnitt[:pos]
    return schnitt.rstrip(" ,;:-–") + " …"


def host(url: str) -> str:
    try:
        name = (urlparse(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        name = ""
    name = name[4:] if name.startswith("www.") else name
    return sauber(name, 60) or "?"


def ampel(tier, warn: bool = False) -> str:
    if warn:
        return "warn"
    if tier is None:
        return "grau"
    return "gelb" if str(tier) == FREMD_TIER else "gruen"


def zahl(n: int) -> str:
    """4200 → „4.200“."""
    return f"{int(n):,}".replace(",", ".")


def laeuft(act: dict) -> dict:
    """Die Zeile, solange das Werkzeug arbeitet: Zeichen, Tätigkeit, Ziel."""
    tool = str(act.get("tool") or "")
    if tool == "web_lesen":
        teil = act.get("teil")
        ziel = host(str(act.get("url") or ""))
        return {"art": "lesen", "symbol": "📖", "verb": "liest", "ziel": ziel,
                "zusatz": f"Teil {sauber(teil, 4)}" if teil and str(teil) != "1" else ""}
    if tool == "web_wiki":
        return {"art": "lesen", "symbol": "📖", "verb": "liest Wikipedia",
                "ziel": f"„{sauber(act.get('suche'), 60)}“", "zusatz": ""}
    suche = sauber(act.get("suche") or act.get("query"), 70)
    if tool == "fach_suche":
        try:
            import fachquellen
            name = fachquellen.QUELLEN.get(fachquellen.quelle_von(str(act.get("quelle") or "")) or "",
                                           ("Fachquellen", 0))[0]
        except Exception:
            name = "Fachquellen"
        return {"art": "suche", "symbol": "📚", "verb": f"sucht in {name}", "ziel": f"„{suche}“", "zusatz": ""}
    return {"art": "suche", "symbol": "🌐", "verb": "sucht im Netz", "ziel": f"„{suche}“", "zusatz": ""}


def radar(sekunden: float) -> str:
    return RADAR[int(max(0.0, sekunden) * 6) % len(RADAR)]


def suche(query: str, treffer: list[dict], quelle: str = "", warn=lambda _u: False) -> dict:
    """Trefferliste für die Anzeige. treffer: {titel, url, text, tier} wie webfetch.treffer/fachquellen.treffer."""
    zeilen = []
    for i, t in enumerate(treffer[:TREFFER_MAX], 1):
        url = str(t.get("url") or "")
        try:
            verdacht = bool(warn(url))
        except Exception:
            verdacht = False
        zeilen.append({"nr": i, "titel": sauber(t.get("titel"), TITEL_MAX) or host(url),
                       "host": host(url), "auszug": sauber(t.get("text") or t.get("info"), AUSZUG_MAX),
                       "ampel": ampel(t.get("tier"), verdacht)})
    return {"art": "suche", "quelle": sauber(quelle, 40), "suche": sauber(query, 70), "treffer": zeilen,
            "gesamt": len(zeilen), "vertraut": sum(1 for z in zeilen if z["ampel"] == "gruen")}


def lesen(url: str, text: str, tier, teil: int = 1, teile: int = 1, titel: str = "", warn: bool = False) -> dict:
    """Eine gelesene Seite: Domain, Teil, ungefähre Wortzahl, erste Zeile als Auszug."""
    anfang = titel or next((z for z in str(text or "").splitlines() if len(z.strip()) > 3), "")
    return {"art": "lesen", "host": host(url), "titel": sauber(anfang, TITEL_MAX),
            "teil": max(1, int(teil or 1)), "teile": max(1, int(teile or 1)),
            "woerter": len(str(text or "").split()), "ampel": ampel(tier, warn)}


def kopf(daten: dict) -> str:
    """Überschrift einer Trefferliste, z. B. „5 Treffer · 3 vertrauenswürdig“."""
    n, v = daten.get("gesamt", 0), daten.get("vertraut", 0)
    teile = ([daten["quelle"]] if daten.get("quelle") else []) + [f"{n} Treffer" if n != 1 else "1 Treffer"]
    if n:
        teile.append(f"{v} vertrauenswürdig")
    return " · ".join(teile)


def legende(daten: dict) -> str:
    """Nur die Ampeln, die in der Liste vorkommen - erklärt, was die Farben heißen."""
    da = [a for a in AMPEL if any(z["ampel"] == a for z in daten.get("treffer", []))]
    return "  ·  ".join(f"{AMPEL[a][0]} {AMPEL[a][1]}" for a in da) if da and da != ["gruen"] else ""


def lese_zeile(daten: dict) -> str:
    """„computerbase.de · Teil 1 von 3 · ≈ 4.200 Wörter“."""
    teile = [daten.get("host", "?")]
    if daten.get("ganz"):
        teile.append("in einem Zug")
    elif daten.get("teile", 1) > 1:
        teile.append(f"Teil {daten['teil']} von {daten['teile']}")
    if daten.get("woerter"):
        teile.append(f"≈ {zahl(daten['woerter'])} Wörter")
    return " · ".join(teile)


# --- Recherche: Schritt-Leiste -------------------------------------------------------------------------
SCHRITTE = ("plan", "suchen", "lesen", "bericht")
SCHRITT_NAMEN = {"plan": "Plan", "suchen": "Suchen", "lesen": "Lesen", "bericht": "Bericht"}


def recherche_stand(phase: str, teilfragen: int = 0, bearbeitet: int = 0, gelesen: int = 0,
                    lese_ziel: int = 0, quellen: int = 0) -> dict:
    """Stand für die Schritt-Leiste. phase: plan · suchen · pruefen · bericht · fertig."""
    return {"phase": phase, "teilfragen": teilfragen, "bearbeitet": bearbeitet, "gelesen": gelesen,
            "lese_ziel": max(lese_ziel, gelesen), "quellen": quellen}


def schritt_zustand(stand: dict) -> list[tuple[str, str, str]]:
    """[(Name, Zustand, Zähler)] - Zustand: fertig · laeuft · offen."""
    phase = stand.get("phase", "plan")
    fertig = phase == "fertig"
    raus = []
    for s in SCHRITTE:
        if fertig:
            z = "fertig"
        elif s == "plan":
            z = "laeuft" if phase == "plan" else "fertig"
        elif s in ("suchen", "lesen"):
            if phase == "plan":
                z = "offen"
            elif phase in ("suchen", "pruefen"):
                z = "laeuft" if (s == "suchen" or stand.get("gelesen")) else "offen"
            else:
                z = "fertig"
        else:
            z = "laeuft" if phase == "bericht" else "offen"
        zaehler = ""
        if s == "suchen" and stand.get("teilfragen"):
            zaehler = f"{stand.get('bearbeitet', 0)}/{stand['teilfragen']}"
        elif s == "lesen" and stand.get("lese_ziel"):
            zaehler = f"{stand.get('gelesen', 0)}/{stand['lese_ziel']}"
        raus.append((SCHRITT_NAMEN[s], z, zaehler))
    return raus
