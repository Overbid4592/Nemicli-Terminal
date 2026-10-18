"""steuerung.py – die Persönlichkeit steuert ihre Kugel selbst (Aktion `kugel`).

Die Kugel lebt im Wache-Prozess, die Persönlichkeit antwortet mal dort (Weckruf,
Kugel-Fenster) und mal im großen NemiCLI. Deshalb geht alles über EINE kleine
Datei: `Wache/kugel_zustand.json`. Die Aktion schreibt sie, die Kugel liest sie
alle 1,5 s und setzt um, was neu ist.

  stimmung   neutral · froh · ernst · denkt … oder ein Motiv (kugelmotive.py), auch frei formuliert
             („isst Popcorn“) → Persoenlichkeiten/<Name>/<stimmung>.png
             (ältere Bilder: Persoenlichkeiten/<Name>_<stimmung>.png)
             (fehlt es: <Name>.png; fehlt auch das: die gemalte Kugel)
  outfit     ein schon gemaltes Outfit anziehen (kugelbilder.py, Werkzeug kugel_anziehen) oder
             „standard“ = Grundkleidung; Motive ohne Outfit-Bild bleiben in Grundkleidung
  sagen      Text für eine Sprechblase (+ sekunden)
  bewegung   huepfen · wackeln · nicken
  ecke       oben_links · oben_rechts · unten_links · unten_rechts · mitte
  position   [x, y] in Bildschirm-Pixeln
  versteckt  true/false – sich zurückziehen / wieder zeigen
  groesse    48 … 200 Pixel
  bewegt     Gesichts-WebP von portraet_bewegen (<Video>_kugel.webp im eigenen Videos-Ordner) –
             die Kugel spielt es zweimal rund ab und zeigt dann wieder ihr Bild

GRENZEN (damit ein Desktop-Wesen nicht zur Nervensäge wird):
  • von sich aus reden höchstens alle `kugel_reden_min` Minuten (Config, Standard 10)
  • nachts (23–8 Uhr) nur mit wichtig=true
  • Text der Blase höchstens 200 Zeichen
Jede Steuerung steht im Aktions-Protokoll.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from . import ORDNER, _ROOT

ZUSTAND = ORDNER / "kugel_zustand.json"
BILDER_ORDNER = _ROOT / "Persoenlichkeiten"

STIMMUNGEN = ("neutral", "froh", "ernst", "denkt", "muede", "ueberrascht", "traurig", "stolz")
BEWEGUNGEN = ("huepfen", "wackeln", "nicken")
ECKEN = ("oben_links", "oben_rechts", "unten_links", "unten_rechts", "mitte")
GROESSE_MIN, GROESSE_MAX = 48, 200
SAGEN_MAX = 200
RUNDEN_MAX = 5                 # so oft spielt die Kugel ein bewegtes Porträt höchstens


_hintergrund = 0
_hintergrund_lock = threading.Lock()


def hintergrund(an: bool) -> None:
    """Die Persönlichkeit arbeitet gerade im Hintergrund (Weckruf, freier Moment) – die
    Kugel zeigt das in Regenbogenfarben. Gezählt, weil mehreres gleichzeitig laufen kann."""
    global _hintergrund
    with _hintergrund_lock:
        _hintergrund = max(0, _hintergrund + (1 if an else -1))


def im_hintergrund() -> bool:
    return _hintergrund > 0


def lesen() -> dict:
    try:
        return json.loads(ZUSTAND.read_text(encoding="utf-8"))
    except Exception:
        return {}


def schreiben(z: dict) -> None:
    try:
        ZUSTAND.parent.mkdir(parents=True, exist_ok=True)
        ZUSTAND.write_text(json.dumps(z, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _slug(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def _outfit_bilder(ordner: Path) -> dict[str, Path]:
    """Bilder des aktiven Outfits (outfit.json → Outfits/<outfit>/). Sie gelten vor den Grundbildern;
    ein Motiv ohne Outfit-Bild bleibt in der Grundkleidung – der Ausdruck wechselt nie."""
    try:
        aktiv = str(json.loads((ordner / "outfit.json").read_text(encoding="utf-8")).get("aktiv") or "")
    except Exception:
        return {}
    if not aktiv or _slug(aktiv) != aktiv:
        return {}
    out: dict[str, Path] = {}
    for p in (ordner / "Outfits" / aktiv).glob("*.png"):
        k = _slug(p.stem)
        out["" if k == "neutral" else k] = p
    return out


def outfit_stand(name: str) -> tuple[str, set[str]]:
    """(aktives Outfit oder "", Motive, die es darin schon gibt)."""
    wer = _slug(name)
    if BILDER_ORDNER.exists():
        for ordner in BILDER_ORDNER.iterdir():
            if ordner.is_dir() and _slug(ordner.name) == wer:
                da = _outfit_bilder(ordner)
                if da:
                    return next(iter(da.values())).parent.name, set(da)
                try:
                    aktiv = str(json.loads((ordner / "outfit.json").read_text(encoding="utf-8")).get("aktiv") or "")
                except Exception:
                    aktiv = ""
                return aktiv, set()
    return "", set()


def bilder(name: str, outfit: bool = True) -> dict[str, Path]:
    """Alle Bilder einer Persönlichkeit: {"": <Name>.png, "froh": <Name>_froh.png, …}
    (Groß/klein egal: „Nemi_froh.png“, „nemi-froh.PNG“, „Maia_ernst.png“ passen alle).
    outfit=True: Bilder des aktiven Outfits ersetzen die gleichnamigen Grundbilder."""
    out: dict[str, Path] = {}
    if not BILDER_ORDNER.exists():
        return out
    wer = _slug(name)
    for p in BILDER_ORDNER.iterdir():
        if p.suffix.lower() not in (".png", ".gif", ".webp") or not p.is_file():
            continue
        stamm = _slug(p.stem.replace("-", "_").split("_")[0])
        if stamm != wer:
            continue
        teile = p.stem.replace("-", "_").split("_", 1)
        stimmung = _slug(teile[1]) if len(teile) > 1 else ""
        out[stimmung] = p
    for ordner in BILDER_ORDNER.iterdir():           # Persoenlichkeiten/<Name>/ – gilt vor den alten
        if ordner.is_dir() and _slug(ordner.name) == wer:
            for p in ordner.glob("*.png"):
                k = _slug(p.stem)
                out["" if k == "neutral" else k] = p
            if outfit:
                out.update(_outfit_bilder(ordner))
    return out


def _reden_min() -> float:
    try:
        import config
        return float(config.load().get("kugel_reden_min", 10))
    except Exception:
        return 10.0


def bewegt_pruefen(pfad: str, wer: str) -> str | None:
    """Nur das Gesichts-WebP aus dem eigenen Videos-Ordner der Persönlichkeit."""
    try:
        import profilordner
        p = Path(pfad).resolve()
        eigen = profilordner.ordner("Videos", wer, anlegen=False).resolve()
    except Exception:
        return "bewegtes Porträt nicht lesbar"
    if not p.name.endswith("_kugel.webp") or not p.is_relative_to(eigen) or not p.is_file():
        return "bewegtes Porträt nur aus deinem Videos-Ordner (…_kugel.webp von portraet_bewegen)"
    return None


def steuern(a: dict, wer: str = "Persönlichkeit") -> str:
    """Die Aktion `kugel`: prüft, begrenzt, schreibt den Zustand. Gibt zurück, was passiert."""
    z = lesen()
    jetzt = time.time()
    getan: list[str] = []
    abgelehnt: list[str] = []

    outfit = str(a.get("outfit") or "").strip()
    if outfit:
        try:
            import kugelbilder as KB
            key = KB.outfit_schluessel(outfit)
            liste = KB.outfits(wer)["liste"]
            if not key or key in KB.OUTFIT_STANDARD:
                KB.outfit_anziehen(wer, "")
                z["outfit_zeit"] = jetzt; getan.append("Grundkleidung")
            elif key in liste:
                KB.outfit_anziehen(wer, key)
                z["outfit_zeit"] = jetzt; getan.append(f"Outfit {key}")
            else:
                abgelehnt.append(f"Outfit „{key}“ gibt es noch nicht – kugel_anziehen malt es"
                                 + (f" (gemalt: {', '.join(liste)})" if liste else ""))
        except Exception as exc:
            abgelehnt.append(f"Outfit nicht gewechselt: {exc}")

    stimmung = str(a.get("stimmung") or a.get("bild") or "").strip().lower()
    if stimmung:
        stimmung = _slug(stimmung.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe"))
        if stimmung == "kugel":
            z["stimmung"] = "kugel"; getan.append("wieder die Kugel")
        else:
            da = bilder(wer)
            if stimmung not in da:
                try:
                    import kugelmotive
                    stimmung = kugelmotive.finden(str(a.get("stimmung") or a.get("bild")), da) or stimmung
                except Exception:
                    pass
            if stimmung in da or "" in da:
                z["stimmung"] = stimmung
                eigenes = stimmung in da or stimmung == "neutral"      # neutral.png ist das Standardbild
                getan.append(f"Stimmung {stimmung}" + ("" if eigenes else f" (kein eigenes Bild dafür – zeige {da[''].name})"))
                kleid, im_outfit = outfit_stand(wer)
                if kleid and eigenes and ("" if stimmung == "neutral" else stimmung) not in im_outfit:
                    getan.append(f"im Outfit {kleid} noch nicht gemalt – zeigt es in Grundkleidung "
                                 "(kugel_anziehen mit motive malt es dazu)")
            else:
                abgelehnt.append(f"kein Bild für „{stimmung}“ – /kugel malen legt Bilder in "
                                 f"Persoenlichkeiten/{wer}/ an (neutral.png = Standard)")

    sagen = " ".join(str(a.get("sagen") or "").split())
    if sagen:
        stunde = datetime.now().hour
        wichtig = bool(a.get("wichtig"))
        seit = jetzt - float(z.get("letztes_sagen") or 0)
        pause = _reden_min() * 60
        if (stunde >= 23 or stunde < 8) and not wichtig:
            abgelehnt.append("nachts (23–8 Uhr) redet die Kugel nur mit wichtig=true")
        elif seit < pause and not wichtig:
            abgelehnt.append(f"zuletzt vor {int(seit // 60)} min geredet – Pause ist {int(pause // 60)} min "
                             "(wichtig=true nur, wenn es wirklich nicht warten kann)")
        else:
            z["sagen"] = sagen[:SAGEN_MAX]
            z["sagen_zeit"] = jetzt
            try:
                z["sagen_sekunden"] = max(3.0, min(60.0, float(a.get("sekunden") or 10)))
            except (TypeError, ValueError):
                z["sagen_sekunden"] = 10.0
            z["letztes_sagen"] = jetzt
            getan.append(f"Blase „{z['sagen'][:60]}“")

    bewegung = str(a.get("bewegung") or "").strip().lower()
    if bewegung:
        bewegung = bewegung.replace("ü", "ue")
        if bewegung in BEWEGUNGEN:
            z["bewegung"] = bewegung; z["bewegung_zeit"] = jetzt
            getan.append(bewegung)
        else:
            abgelehnt.append(f"Bewegung „{bewegung}“ kenne ich nicht ({', '.join(BEWEGUNGEN)})")

    ecke = str(a.get("ecke") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if ecke:
        if ecke in ECKEN:
            z["ecke"] = ecke; z.pop("position", None); z["lage_zeit"] = jetzt
            getan.append(f"Ecke {ecke}")
        else:
            abgelehnt.append(f"Ecke „{ecke}“ kenne ich nicht ({', '.join(ECKEN)})")
    pos = a.get("position")
    if pos is not None:
        try:
            x, y = (int(v) for v in (pos if isinstance(pos, (list, tuple)) else str(pos).replace(";", ",").split(",")))
            z["position"] = [x, y]; z.pop("ecke", None); z["lage_zeit"] = jetzt
            getan.append(f"Position {x},{y}")
        except Exception:
            abgelehnt.append("position muss [x, y] sein")

    if "versteckt" in a:
        v = a.get("versteckt")
        v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "ja", "yes")
        z["versteckt"] = v
        getan.append("versteckt" if v else "wieder da")

    if a.get("groesse") is not None:
        try:
            g = int(a.get("groesse"))
            if GROESSE_MIN <= g <= GROESSE_MAX:
                z["groesse"] = g; getan.append(f"Größe {g}px")
            else:
                abgelehnt.append(f"Größe nur {GROESSE_MIN}–{GROESSE_MAX} px")
        except (TypeError, ValueError):
            abgelehnt.append("groesse muss eine Zahl sein")

    bewegt = str(a.get("bewegt") or "").strip().strip('"')
    if bewegt:
        if (grund := bewegt_pruefen(bewegt, wer)):
            abgelehnt.append(grund)
        else:
            z["bewegt"] = str(Path(bewegt).resolve()); z["bewegt_zeit"] = jetzt
            try:
                z["bewegt_runden"] = max(1, min(RUNDEN_MAX, int(a.get("runden") or 2)))
            except (TypeError, ValueError):
                z["bewegt_runden"] = 2
            getan.append(f"bewegtes Porträt ({z['bewegt_runden']}×)")

    if not getan and not abgelehnt:
        da = bilder(wer)
        return ("Nichts angegeben. Felder: stimmung, outfit, sagen (+sekunden, wichtig), bewegung, ecke, position, "
                "versteckt, groesse. " + (f"Bilder für dich: {', '.join(sorted(k or '(Standard)' for k in da))}."
                                          if da else f"Noch kein Bild für dich – /kugel malen legt sie in "
                                                     f"Persoenlichkeiten/{wer}/ an."))
    if getan:
        z["zeit"] = jetzt
        z["wer"] = wer
        z["name"] = wer
        schreiben(z)
        try:
            import protokoll
            protokoll.schreibe("kugel", "Kugel: " + ", ".join(getan), "success", veraendernd=False, wer=wer,
                               stufe="harmlos")
        except Exception:
            pass
    return ("Kugel: " + ", ".join(getan) + "." if getan else "Kugel: nichts geändert.") \
        + (" Nicht gemacht: " + "; ".join(abgelehnt) + "." if abgelehnt else "")
