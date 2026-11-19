"""figur.py – die Figur der aktiven Persönlichkeit im eigenen Terminal-Fenster.

Sie sitzt rechts auf der Kopfzeile der Eingabe und zeigt, was gerade passiert: ruhig sitzen, nachdenken,
prüfen, arbeiten, sich freuen, winken. Das Fenster malt sie (ui/terminal_fenster.py); von hier kommen nur
Steuerbefehle über denselben Weg wie Puls und Bilder:

  posen=<base64 JSON>   {"art": "figur"|"buste", "bilder": {pose: Pfad}} – leer: keine Figur
  pose=<name>           aktuelle Pose (kurze Posen wie „freude“ gehen im Fenster von selbst zurück)
  sitz=<n>              Zeilen vom unteren Rand bis zur Kopfzeile der Eingabe, auf der sie sitzt

Bilder: „figur“ = ganze sitzende Figur je Pose (Persoenlichkeiten/<Name>/Figur/<pose>.png, malt
/kugel malen figur). Fehlen sie, „buste“: passende Kugel-Motive oder Stimmungsbilder, die es schon gibt.

Strichmännchen („strich“, wenn SprongMotion eingerichtet ist – engines/bewegung.py, Schalter figur_strich):
Der Körper bewegt sich nach Text, das Gesicht ist ein Kugel-Bild der Persönlichkeit. Jede Pose wird zu einer
Bewegung (BEWEGUNG_JE_POSE); die KI kann mit dem Werkzeug figur_bewegen jede andere verlangen.

  rig=<base64 JSON>       {"vorne": rig.json, "hinten": rig.json} – ihr echtes Bild als Hülle um das Skelett (tools/figurrig.py),
                          aus /kugel malen figur; im aktiven Outfit, wenn es dort gemalt ist. Leer: nur Striche + Gesicht
  justierung=<base64 JSON> {"datei": …/Figur/justierung.json, "werte": {…}} – Größe, Längen und Platz
                          (ui/figurmass.py); das Fenster schreibt Änderungen seiner Regler dorthin zurück
  bewegung=<base64 Pfad>  JSON mit Gelenken je Bild (im Cache, gleiche Anfrage = gleiche Datei), Schleife ja/nein,
                          Gesicht, Vorrang (die der KI wird nicht von Status-Bewegungen unterbrochen)
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path

# Pose: (Wort, Satz für das Bildmodell, kurz?, passende Kugel-Motive, Kugel-Stimmung als Ersatz)
POSEN: dict[str, tuple[str, str, bool, tuple[str, ...], str]] = {
    "sitzen": ("sitzt und lächelt", "sitting relaxed with a soft, warm smile, hands resting in the lap",
               False, ("laecheltsanft", "bereit", "zufrieden"), ""),
    "winken": ("winkt", "waving hello with one hand raised, smiling brightly",
               True, ("winkt", "begruesstmorgens"), "froh"),
    "nachdenken": ("denkt nach", "thinking, one hand on the chin, looking slightly upward",
                   False, ("denktuebercodenach", "gluehbirneueberkopf", "vertraeumt"), "denkt"),
    "pruefen": ("prüft", "concentrating, looking closely at a small glowing tablet held in both hands",
                False, ("schautlupe", "analysiert", "recherchiert", "konzentrierterblick"), "ernst"),
    "arbeiten": ("arbeitet", "typing on a small laptop resting on the knees, focused",
                 False, ("tipptamlaptop", "programmiert", "sitztampc"), "ernst"),
    "freude": ("freut sich", "cheering with both arms raised, laughing happily",
               True, ("jubelt", "aufgabeerledigt", "victorypose", "daumenhoch"), "froh"),
    "herz": ("Herz-Moment", "forming a heart shape with both hands, smiling lovingly",
             True, ("herzmithaenden", "verliebt", "luftkuss"), "froh"),
    "grinsen": ("grinst frech", "grinning cheekily with a playful wink",
                True, ("grinst", "frech", "schelmischesgrinsen", "zwinkert"), "froh"),
    "neugierig": ("legt den Kopf schief", "tilting the head to one side, curious and attentive",
                  True, ("neugierig", "stelltfrage", "fragezeichen"), "denkt"),
    "schlafen": ("döst", "dozing peacefully with closed eyes, head slightly lowered",
                 False, ("schlaeft", "sitztverschlafen", "gaehnt"), ""),
}
KURZ = frozenset(k for k, v in POSEN.items() if v[2])
# Status der Startseite (startseite.taetig) -> Pose
STATUS_POSE = {"denkt": "nachdenken", "prueft": "pruefen", "arbeitet": "arbeiten"}
HERZ = ("❤", "♥", "💜", "💖", "💕", "😍", "🥰", "<3")
FRECH = ("😏", "😜", "😝", "😉", "😈", "🤭")

_STAND = {"pose": None, "posen": None, "herz": False, "antwort": "", "art": ""}

# Strichmännchen: Pose -> (Bewegung auf Englisch, Schleife?, Gesicht). Sie steht auf der Kopfzeile der Eingabe.
BEWEGUNG_JE_POSE: dict[str, tuple[str, bool, str]] = {
    "sitzen": ("stands relaxed with the arms hanging at the sides", True, "neutral"),
    "winken": ("waves hello with the right hand", False, "froh"),
    "nachdenken": ("thinks with one hand on the chin", True, "denkt"),
    "pruefen": ("looks closely at something held in both hands", True, "ernst"),
    "arbeiten": ("types quickly on a keyboard in front of the body", True, "ernst"),
    "freude": ("jumps for joy with both arms up", False, "froh"),
    "herz": ("puts both hands on the heart and sways happily", False, "verliebt"),
    "grinsen": ("shrugs the shoulders playfully", False, "grinst"),
    "neugierig": ("leans forward curiously and tilts the head", False, "neugierig"),
    "schlafen": ("stands still with the head lowered, breathing slowly", True, "schlaeft"),
}
SEKUNDEN_JE_POSE = {"winken": 2.5, "freude": 2.4, "herz": 2.8, "grinsen": 2.4, "neugierig": 3.0}
# Gesichter: die Grundbilder der Kugel und Motive, die als Gesicht taugen (nur, was gemalt ist)
GESICHT_MOTIVE = ("grinst", "zwinkert", "lacht", "kichert", "verlegen", "erroetet", "staunt", "ueberrascht", "schmollt",
                  "traurig", "verliebt", "frech", "laecheltsanft", "gluecklich", "neugierig", "skeptischerblick",
                  "genervt", "zufrieden", "stolz", "gaehnt", "zungeraus", "wuetend", "erschrocken", "geruehrt")
# Wunsch -> Ersatz in dieser Reihenfolge, wenn es das Gesicht nicht gibt (Kugel-Bilder oder Köpfe des echten Bilds)
GESICHT_ERSATZ = {"verliebt": ("froh",), "grinst": ("froh",), "lacht": ("froh",), "zwinkert": ("grinst", "froh"),
                  "neugierig": ("denkt", "staunt"), "denkt": ("ernst",), "staunt": ("neugierig",),
                  "schlaeft": ("neutral",)}
CACHE_MAX = 300                                # gemerkte Bewegungen (Dateien) höchstens
_ARBEIT = {"liste": [], "faden": None}
_ARBEIT_BEREIT = threading.Condition()


def ordner(name: str) -> Path:
    import kugelbilder as KB
    return KB.motiv_ordner(name) / "Figur"


def bilder(name: str) -> tuple[str, dict[str, Path]]:
    """("figur", {pose: Bild}) mit gemalten Ganzkörper-Posen, sonst ("buste", …) aus Kugel-Motiven und
    Stimmungsbildern, sonst ("", {})."""
    fig = ordner(name)
    ganz = {k: fig / f"{k}.png" for k in POSEN if (fig / f"{k}.png").is_file()}
    if "sitzen" in ganz:
        return "figur", {k: ganz.get(k, ganz["sitzen"]) for k in POSEN}
    try:
        import kugelbilder as KB
        motive = KB.motiv_ordner(name)
        stimmungen = KB.vorhandene(name)
    except Exception:
        return "", {}
    raus = {}
    for k, (_w, _s, _kurz, kandidaten, stimmung) in POSEN.items():
        treffer = next((motive / f"{m}.png" for m in kandidaten if (motive / f"{m}.png").is_file()), None)
        treffer = treffer or stimmungen.get(stimmung) or stimmungen.get("")
        if treffer is not None:
            raus[k] = Path(treffer)
    return ("buste", raus) if "sitzen" in raus else ("", {})


def _strich_bereit() -> bool:
    """SprongMotion eingerichtet und nicht abgeschaltet (Config figur_strich, Vorgabe an)?"""
    try:
        import config
        if not bool(config.load().get("figur_strich", True)):
            return False
        import bewegung
        return bewegung.stand()["bereit"]
    except Exception:
        return False


def gesichter(name: str) -> dict[str, Path]:
    """Gesichter für das Strichmännchen: neutral/froh/ernst/denkt der Kugel und gemalte Gesichts-Motive."""
    try:
        import kugelbilder as KB
        motive = KB.motiv_ordner(name)
        stimmungen = KB.vorhandene(name)
    except Exception:
        return {}
    raus = {("neutral" if k == "" else k): Path(v) for k, v in stimmungen.items() if k in ("", "froh", "ernst", "denkt")}
    for m in GESICHT_MOTIVE:
        if (motive / f"{m}.png").is_file():
            raus[m] = motive / f"{m}.png"
    return raus


def rig_pfade(name: str) -> dict[str, str]:
    """Die bewegliche Figur: {"vorne": rig.json, "hinten": rig.json} – erst im aktiven Outfit, sonst die Grundfigur."""
    try:
        import kugelbilder as KB
        orte = []
        aktiv = KB.outfits(name).get("aktiv")
        if aktiv:
            orte.append(KB.outfit_ordner(name, aktiv) / "Figur")
        orte.append(ordner(name))
    except Exception:
        return {}
    try:
        import figurrig
    except Exception:
        return {}
    for ort in orte:
        if (ort / "koerper_vorne.png").is_file():
            try:
                KB.koerper_bauen(ort)          # fehlt oder ältere Form: aus dem Bild neu bauen
            except Exception:
                pass
        pfade = {a: ort / f"rig_{a}" / "rig.json" for a in ("vorne", "hinten")}
        if figurrig.aktuell(pfade["vorne"]):
            return {a: str(v) for a, v in pfade.items() if figurrig.aktuell(v)}
    return {}


def justierung_pfad(name: str) -> Path:
    import figurmass
    return ordner(name) / figurmass.DATEI


def justierung_melden(name: str) -> None:
    import figurmass
    pfad = justierung_pfad(name)
    _senden("justierung=" + _kodiert({"datei": str(pfad), "werte": figurmass.lesen(pfad)}))


# Werkzeug figur_justieren: Feld -> (Schlüssel in figurmass, Teiler: Prozent -> Faktor)
JUSTIER_FELDER = {"groesse": ("groesse", 1), "kopf": ("kopf", 100), "rumpf": ("rumpf", 100), "arme": ("arme", 100),
                  "beine": ("beine", 100), "fuesse": ("fuesse", 100), "links_rechts": ("x", 100), "hoch": ("y", 1)}


def _justierung_text(w: dict) -> str:
    return (f"Größe {w['groesse']} Zeilen · Kopf {round(w['kopf'] * 100)} % · Rumpf {round(w['rumpf'] * 100)} % · "
            f"Arme {round(w['arme'] * 100)} % · Beine {round(w['beine'] * 100)} % · Füße {round(w['fuesse'] * 100)} % · "
            f"links_rechts {round(w['x'] * 100)} (0 links, 100 rechts) · hoch {w['y']} Zeilen")


def justieren(felder: dict, name: str | None = None) -> str:
    """Werkzeug figur_justieren: Größe in Zeilen, Längen in Prozent, links_rechts 0–100, hoch in Zeilen über dem
    Rahmen; standard: true setzt alles zurück. Ohne Felder: nur den Stand nennen. Gibt die Rückmeldung."""
    import figurmass
    if name is None:
        import persoenlichkeiten as PS
        name = PS.active().name
    pfad = justierung_pfad(name)
    werte = dict(figurmass.VORGABE) if felder.get("standard") in (True, "true", "ja", 1, "1") else figurmass.lesen(pfad)
    geaendert = False
    for feld, (schluessel, teiler) in JUSTIER_FELDER.items():
        if felder.get(feld) in (None, ""):
            continue
        try:
            werte[schluessel] = float(felder[feld]) / teiler
            geaendert = True
        except (TypeError, ValueError):
            raise ValueError(f"{feld}: keine Zahl ({felder[feld]!r})")
    if geaendert or felder.get("standard"):
        werte = figurmass.schreiben(pfad, werte)
        justierung_melden(name)
        return "Figur angepasst: " + _justierung_text(werte) + "."
    return "Figur jetzt: " + _justierung_text(werte) + "."


def gesicht_finden(wunsch: str, vorhanden) -> str:
    """Freier Wunsch („lächelt“, „zwinkert“) -> ein vorhandenes Gesicht; sonst ein Ersatz, sonst neutral."""
    vorhanden = list(vorhanden)
    wunsch = str(wunsch or "").strip().lower()
    if not wunsch:
        return "neutral"
    if wunsch in vorhanden:
        return wunsch
    for ersatz in GESICHT_ERSATZ.get(wunsch, ()):
        if ersatz in vorhanden:
            return ersatz
    try:
        import kugelmotive as KM
        treffer = KM.finden(wunsch, vorhanden)
    except Exception:
        treffer = None
    if treffer:
        return treffer
    froh = ("lächel", "laechel", "smile", "fröhlich", "froh", "happy", "freu")
    return "froh" if any(w in wunsch for w in froh) and "froh" in vorhanden else "neutral"


def _cache_ordner() -> Path:
    try:
        from paths import DATEN
        return Path(DATEN) / "Cache" / "figur"
    except Exception:
        return Path(__file__).resolve().parent / "_figur_cache"


def _bewegung_datei(prompt: str, sekunden: float, seed: int, schleife: bool) -> Path:
    """Die Bewegung als JSON im Cache – gerechnet nur, wenn es sie noch nicht gibt (gleiche Anfrage, gleiche Bewegung)."""
    import bewegung
    schluessel = f"{bewegung.VERSION}|{bewegung.MODELL_SHA}|{prompt}|{sekunden:.2f}|{seed}|{int(schleife)}"
    ziel = _cache_ordner() / (hashlib.sha256(schluessel.encode("utf-8")).hexdigest()[:24] + ".json")
    if ziel.is_file():
        return ziel
    daten = bewegung.MOTOR.rechnen(prompt, sekunden, seed, schleife)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    teil = ziel.with_suffix(".part")
    teil.write_text(json.dumps(daten, separators=(",", ":")), encoding="utf-8")
    teil.replace(ziel)
    alte = sorted(ziel.parent.glob("*.json"), key=lambda f: f.stat().st_mtime)
    for f in alte[:max(0, len(alte) - CACHE_MAX)]:
        f.unlink(missing_ok=True)
    return ziel


def _arbeiten() -> None:
    """Rechnet die Bewegungen nacheinander; von mehreren wartenden Status-Schleifen zählt nur die letzte."""
    while True:
        with _ARBEIT_BEREIT:
            while not _ARBEIT["liste"]:
                _ARBEIT_BEREIT.wait()
            auftrag = _ARBEIT["liste"].pop(0)
            if not auftrag["vorrang"] and auftrag["schleife"] and any(
                    a["schleife"] and not a["vorrang"] for a in _ARBEIT["liste"]):
                continue
        try:
            datei = _bewegung_datei(auftrag["prompt"], auftrag["sekunden"], auftrag["seed"], auftrag["schleife"])
        except Exception:
            continue
        kopf = {"datei": str(datei), "gesicht": auftrag["gesicht"], "vorrang": auftrag["vorrang"]}
        _senden("bewegung=" + _kodiert(kopf))


def bewegung_anfordern(prompt: str, *, sekunden: float = 3.0, schleife: bool = False, gesicht: str = "neutral",
                       vorrang: bool = False, seed: int = 7) -> None:
    """Eine Bewegung im Hintergrund rechnen lassen und ans Fenster schicken – nur, wenn es das eigene Fenster gibt,
    das sie zeigt (sonst wird nichts gerechnet und keine Laufzeit geladen)."""
    try:
        import startseite
        if not startseite.echtes_fenster():
            return
    except Exception:
        return
    with _ARBEIT_BEREIT:
        _ARBEIT["liste"].append({"prompt": prompt, "sekunden": float(sekunden), "schleife": bool(schleife),
                                 "gesicht": gesicht, "vorrang": bool(vorrang), "seed": int(seed)})
        if _ARBEIT["faden"] is None or not _ARBEIT["faden"].is_alive():
            _ARBEIT["faden"] = threading.Thread(target=_arbeiten, name="figur-bewegung", daemon=True)
            _ARBEIT["faden"].start()
        _ARBEIT_BEREIT.notify()


def _gesichter_da() -> list[str]:
    """Gesichter, die die Figur gerade zeigen kann: die ihres echten Bilds (Kopf je Ausdruck), sonst die Kugel-Bilder
    am Strichmännchen."""
    if _STAND.get("rig_gesichter"):
        return ["neutral", *_STAND["rig_gesichter"]]
    return list((_STAND.get("posen") or {}).get("bilder", {}))


def rig_gesichter(rig: dict[str, str]) -> list[str]:
    """Die Ausdrücke im Kopf der Vorderansicht (tools/figurgesicht.py, in rig.json je Stück „gesichter“)."""
    try:
        d = json.loads(Path(rig["vorne"]).read_text(encoding="utf-8"))
        return sorted({n for s in d.get("stuecke", []) for n in (s.get("gesichter") or {})})
    except Exception:
        return []


def _pose_bewegen(name: str) -> None:
    prompt, schleife, gesicht = BEWEGUNG_JE_POSE[name]
    vorhanden = _gesichter_da()
    bewegung_anfordern(prompt, sekunden=4.0 if schleife else SEKUNDEN_JE_POSE.get(name, 3.0), schleife=schleife,
                       gesicht=gesicht_finden(gesicht, vorhanden))


def bewegen(text: str, gesicht: str = "", sekunden: float = 3.0, seed: int = 7) -> str:
    """Werkzeug figur_bewegen: eine freie Bewegung (englischer Satz) und/oder ein Gesicht. Gibt die Rückmeldung."""
    import bewegung
    if _STAND.get("art") != "strich":
        st = bewegung.stand()
        if not st["bereit"]:
            raise RuntimeError("Die Figur kann sich noch nicht frei bewegen: " + " · ".join(st["zeilen"]))
        raise RuntimeError("Die Figur ist gerade nicht als Strichmännchen da (eigenes Terminal-Fenster, "
                           "/terminal figur an, /bewegung an).")
    vorhanden = _gesichter_da()
    wahl = gesicht_finden(gesicht, vorhanden) if gesicht else "neutral"
    prompt = bewegung.prompt_sauber(text)
    if not prompt:
        prompt = BEWEGUNG_JE_POSE["sitzen"][0]
    sekunden = min(bewegung.SEKUNDEN_MAX, max(bewegung.SEKUNDEN_MIN, float(sekunden or 3.0)))
    bewegung_anfordern(prompt, sekunden=sekunden, gesicht=wahl, vorrang=True, seed=seed)
    teile = [f"Bewegung „{prompt}“ ({sekunden:.1f} s)" if text else "", f"Gesicht {wahl}" if gesicht else ""]
    hinweis = ""
    if gesicht and _STAND.get("rig") and not _STAND.get("rig_gesichter"):
        hinweis = (" Dein Körper zeigt dein gemaltes Bild – Gesichter hat er noch nicht (/kugel malen figur rechnet sie), "
                   "zeig den Ausdruck mit der Bewegung oder mit Worten.")
    elif gesicht and _STAND.get("rig_gesichter") and wahl == "neutral":
        hinweis = " Dieses Gesicht gibt es nicht; da sind: " + ", ".join(_STAND["rig_gesichter"]) + "."
    return "Figur: " + ", ".join(t for t in teile if t) + "." + hinweis


def _senden(befehl: str) -> None:
    try:
        import startseite
        if startseite.echtes_fenster():
            startseite.STEUERN(befehl)
    except Exception:
        pass


def _kodiert(daten: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(daten, ensure_ascii=False).encode("utf-8")).decode("ascii")


def melden(name: str | None = None) -> None:
    """Bilder der aktiven Persönlichkeit ans Fenster; neue Persönlichkeit (oder Start): sie winkt."""
    if name is None:
        try:
            import persoenlichkeiten as PS
            name = PS.active().name
        except Exception:
            return
    if _strich_bereit():
        art, liste = "strich", gesichter(name)
    else:
        art, liste = bilder(name)
    daten = {"art": art, "bilder": {k: str(v) for k, v in liste.items()}} if art else {}
    if daten == _STAND["posen"]:
        return
    _STAND["posen"], _STAND["art"] = daten, art
    _senden("posen=" + (_kodiert(daten) if daten else ""))
    if art == "strich":
        justierung_melden(name)
        rig = rig_pfade(name)
        _STAND["rig"] = bool(rig)
        _STAND["rig_gesichter"] = rig_gesichter(rig) if rig else []
        _senden("rig=" + (_kodiert(rig) if rig else ""))
        _pose_bewegen("sitzen")              # die Ruhe-Schleife zuerst: dahin kehrt jede kurze Bewegung zurück
    _STAND["pose"] = None
    pose("winken" if daten else "sitzen")


def pose(name: str) -> None:
    if name in POSEN and name != _STAND["pose"]:
        _STAND["pose"] = name
        _senden(f"pose={name}")
        if _STAND.get("art") == "strich":
            _pose_bewegen(name)


def status(art: str | None) -> None:
    """Status der Startseite (denkt/prueft/arbeitet, sonst Ruhe)."""
    pose(STATUS_POSE.get(art or "", "sitzen"))


def nutzer(text: str) -> None:
    """Die Nachricht des Nutzers: Herz darin? Dann gibt es am Ende des Zugs einen Herz-Moment."""
    _STAND["herz"] = any(z in (text or "") for z in HERZ)
    _STAND["antwort"] = ""


def antwort(text: str) -> None:
    _STAND["antwort"] = text or ""


def pose_nach_zug(herz: bool, text: str) -> str:
    """Kurze Pose am Ende eines Zugs: Herz zurück, Rückfrage, freches Zeichen – sonst Freude."""
    rest = (text or "").rstrip()
    if herz:
        return "herz"
    if rest.endswith("?"):
        return "neugierig"
    if any(z in rest[-40:] for z in FRECH):
        return "grinsen"
    return "freude"


def fertig() -> None:
    """Der Zug ist zu Ende: eine kurze Pose; das Fenster kehrt danach von selbst zum Sitzen zurück."""
    _STAND["pose"] = None                     # danach gilt wieder „sitzen“, auch wenn es vorher schon saß
    pose(pose_nach_zug(_STAND["herz"], _STAND["antwort"]))
    _STAND["pose"] = "sitzen"
    _STAND["herz"] = False


def sitz(zeilen: int) -> None:
    _senden(f"sitz={max(1, min(99, int(zeilen)))}")
