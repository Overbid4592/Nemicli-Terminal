"""Einstellungen der Wache – und was die Persönlichkeit davon selbst ändern darf.

Alles steht in `nemicli.config.json` unter "wache". Der Nutzer darf jeden gültigen
Wert setzen (/wache …, `nutzer=True`). Die Persönlichkeit darf über `wache_justieren`
nur vier Dinge, und nur in den Grenzen, die der Nutzer unter "grenzen" festlegt:

  schwelle      ML-Alarmschwelle (Perzentil), z. B. 0,90 … 0,995
  regel_stumm   eine Regel R001–R014 stumm schalten oder wieder anschalten
  cooldown      Sekunden, in denen derselbe Alarm (Regel + Subjekt) nicht erneut
                meldet, sondern nur den Zähler am ersten hochsetzt („×37“)
  trigger_ab    nur strenger: mittel oder hoch. „kritisch“ und „nie“ hießen,
                dass sie sich selbst seltener weckt – das setzt nur der Nutzer.

Jede Änderung braucht eine Begründung, landet in der Tabelle `justierungen`
(alt → neu) und im Aktions-Protokoll. `/wache justierungen` zeigt sie,
`/wache rueckgaengig` nimmt die letzte zurück. Nichts davon ist stumm: Lässt sich
die Config nicht schreiben, gibt es SpeicherFehler und keinen Eintrag.
"""

from __future__ import annotations

from datetime import datetime

from . import TRAINING_LOG
from .regeln import BESCHREIBUNG

STANDARD = {
    "aktiv": True,                      # Hintergrund-Wache automatisch mit NemiCLI starten?
    "kugel": True,                      # Schwebekugel auf dem Desktop, wenn kein Terminal offen ist
    "autostart_windows": False,         # Eintrag in HKCU\…\Run
    "prozess_intervall": 3.0,
    "netz_intervall": 5.0,
    "pfade": None,                      # None = Autostart, Temp, Downloads des Nutzers
    "endungen": None,                   # None = Standard-Liste
    "aufbewahrung_tage": 14,
    "max_ereignisse": 200_000,
    "ml": True,
    "lernen": True,                     # automatisch nachtrainieren
    "min_training": 200,                # erstes Training ab so vielen Ereignissen
    "min_training_dateien": 60,
    "training_alle": 200,               # danach alle N Ereignisse
    "ml_karenz_h": 24,                  # so alt muss ein Ereignis sein, bevor der Isolation Forest daraus lernt
    "kontamination": 0.02,
    "schwelle": 0.97,
    "cooldown": 300,
    # Dämpfung: so oft als harmlos beurteilt (und nie als echt) → Regel + Subjekt
    # meldet nicht mehr, wird nur gezählt. 0 = aus.
    "daempfen_ab": 3,
    "inventar_beim_start": True,
    "inventar_stunden": 6.0,
    "inventar_aufgaben": True,
    "stumme_regeln": [],
    # Wann wird die aktive Persönlichkeit geweckt?  "mittel" | "hoch" | "kritisch" | "nie"
    "trigger_ab": "hoch",
    "trigger_ml": True,                 # auch bei ML-Anomalien (ab Schwelle) wecken
    "trigger_buendel_s": 45,            # so lange sammeln, dann EIN Auftrag für alle Alarme
    "pruefen_ab": 5,                    # so viele ungeprüfte Alarme (jede Stufe) → wecken; nur der Nutzer
    # Folgen-Netz (folgen.py) – alles nur für den Nutzer, nicht über wache_justieren.
    "dl": True,                         # Prozess-Ketten bewerten und lernen
    "dl_alarm": False,                  # False = Schattenmodus: Funde nur ins Protokoll, kein Alarm
    "dl_schwelle": 0.995,               # Perzentil, ab dem ein Schritt auffällig ist
    "dl_karenz_h": 24,                  # so alt muss ein Ereignis sein, bevor das Netz daraus lernt
    "dl_training_stunden": 6.0,         # höchstens so oft automatisch nachtrainieren
    # Grenzen für die Justierungen der Persönlichkeit
    "grenzen": {"schwelle_min": 0.90, "schwelle_max": 0.995, "cooldown_min": 60,
                "cooldown_max": 3600, "regeln_stumm_max": 3},
}

_TRIGGER = ("mittel", "hoch", "kritisch", "nie")
_TRIGGER_KI = ("mittel", "hoch")
PRUEFEN_MIN, PRUEFEN_MAX = 5, 20
# Was für den Nutzer gilt: nur technisch sinnvolle Werte, keine Grenzen der Persönlichkeit.
NUTZER_GRENZEN = {"schwelle_min": 0.5, "schwelle_max": 0.9999, "cooldown_min": 0,
                  "cooldown_max": 86_400, "regeln_stumm_max": len(BESCHREIBUNG)}


def _vermerken(text: str) -> None:
    """Fehler beim Protokollieren nicht verschlucken: wenigstens ins Training-Log."""
    try:
        TRAINING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAINING_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Justierung: {text}\n")
    except OSError:
        pass


def _config():
    import config
    return config


def einstellungen() -> dict:
    """Standard + gespeicherte Werte (flach gemischt, grenzen tief)."""
    try:
        roh = _config().load().get("wache") or {}
    except Exception:
        roh = {}
    e = dict(STANDARD)
    e["grenzen"] = dict(STANDARD["grenzen"])
    for k, v in roh.items():
        if k == "grenzen" and isinstance(v, dict):
            e["grenzen"].update(v)
        elif k in STANDARD:
            e[k] = v
    return e


def speichern(neu: dict) -> bool:
    """Werte unter "wache" speichern. False, wenn die Config nicht geschrieben werden konnte."""
    try:
        cfg = _config()
        daten = cfg.load()
        alt = daten.get("wache") or {}
        alt.update({k: v for k, v in neu.items() if k != "grenzen"})
        if "grenzen" in neu:
            alt["grenzen"] = {**(alt.get("grenzen") or {}), **neu["grenzen"]}
        daten["wache"] = alt
        return cfg.save(daten) is not False
    except Exception as exc:
        _vermerken(f"Speichern fehlgeschlagen: {type(exc).__name__}: {exc}")
        return False


class Grenzverletzung(ValueError):
    pass


class SpeicherFehler(Grenzverletzung):
    """Die Änderung war erlaubt, ließ sich aber nicht speichern – sie gilt nicht."""


def _speicher_grund() -> str:
    try:
        return getattr(_config(), "letzter_fehler", "") or "Config nicht schreibbar"
    except Exception:
        return "Config nicht schreibbar"


def _protokollieren(text: str, wer: str, **k) -> None:
    try:
        import protokoll
        protokoll.schreibe("wache_justieren", text, "success", veraendernd=True, wer=wer, **k)
    except Exception as exc:
        _vermerken(f"Protokoll nicht geschrieben ({type(exc).__name__}): {text}")


def _merken(speicher, wer: str, was: str, alt, neu, begruendung: str) -> None:
    if speicher is None:
        return
    try:
        speicher.justierung_merken(wer, was, alt, neu, begruendung)
    except Exception as exc:
        _vermerken(f"nicht in der Tabelle vermerkt ({type(exc).__name__}): {was} {alt} → {neu}")


def pruefen(was: str, wert, e: dict | None = None, *, nutzer: bool = False) -> tuple[str, object, object]:
    """Prüft eine Justierung. Für die Persönlichkeit gelten die Grenzen des Nutzers, für den
    Nutzer (`nutzer=True`) nur NUTZER_GRENZEN. Gibt (schluessel, alt, neu) zurück oder wirft
    Grenzverletzung mit einem Text, den die Persönlichkeit versteht."""
    e = e or einstellungen()
    g = NUTZER_GRENZEN if nutzer else e["grenzen"]
    was = (was or "").strip().lower()
    if was in ("schwelle", "threshold", "ml_schwelle"):
        try:
            neu = round(float(str(wert).replace(",", ".")), 3)
        except ValueError:
            raise Grenzverletzung("Die Schwelle muss eine Zahl sein, z. B. 0.95.")
        if not (g["schwelle_min"] <= neu <= g["schwelle_max"]):
            raise Grenzverletzung(f"Die Schwelle darf nur zwischen {g['schwelle_min']} und "
                                  f"{g['schwelle_max']} liegen{'' if nutzer else ' (Grenze des Nutzers)'}.")
        return "schwelle", e["schwelle"], neu
    if was in ("cooldown", "pause"):
        try:
            neu = int(float(wert))
        except (TypeError, ValueError):
            raise Grenzverletzung("Cooldown in Sekunden angeben, z. B. 600.")
        if not (g["cooldown_min"] <= neu <= g["cooldown_max"]):
            raise Grenzverletzung(f"Cooldown nur zwischen {g['cooldown_min']} und "
                                  f"{g['cooldown_max']} Sekunden.")
        return "cooldown", e["cooldown"], neu
    if was in ("regel_stumm", "stumm", "regel"):
        text = str(wert).strip().upper().replace(" ", "")
        an = True
        for suffix in ("=AN", "=AUS", ":AN", ":AUS", "-AN", "-AUS"):
            if text.endswith(suffix):
                an = suffix.endswith("AUS")          # "R004=aus" = stumm schalten → stumm=True
                text = text[: -len(suffix)]
                break
        if text not in BESCHREIBUNG:
            raise Grenzverletzung("Unbekannte Regel. Gültig: " + ", ".join(sorted(BESCHREIBUNG)))
        stumm = set(e.get("stumme_regeln") or [])
        neu = set(stumm)
        if an:
            neu.add(text)
        else:
            neu.discard(text)
        if len(neu) > g["regeln_stumm_max"]:
            raise Grenzverletzung(f"Höchstens {g['regeln_stumm_max']} Regeln dürfen gleichzeitig "
                                  "stumm sein (Grenze des Nutzers).")
        return "stumme_regeln", sorted(stumm), sorted(neu)
    if was in ("trigger_ab", "trigger", "wecken_ab"):
        neu = str(wert).strip().lower()
        if neu not in _TRIGGER:
            raise Grenzverletzung("trigger_ab muss mittel, hoch, kritisch oder nie sein.")
        if not nutzer and neu not in _TRIGGER_KI:
            raise Grenzverletzung(f"„{neu}“ hieße seltener wecken – das setzt nur der Nutzer "
                                  f"(/wache trigger {neu}). Erlaubt: mittel oder hoch.")
        return "trigger_ab", e["trigger_ab"], neu
    raise Grenzverletzung("Justierbar sind nur: schwelle, cooldown, regel_stumm, trigger_ab.")


def pruefen_ab(e: dict | None = None) -> int:
    """Zähl-Trigger des Weckers, auf PRUEFEN_MIN…PRUEFEN_MAX begrenzt."""
    e = e or einstellungen()
    try:
        n = int(float(e.get("pruefen_ab", STANDARD["pruefen_ab"])))
    except (TypeError, ValueError):
        n = STANDARD["pruefen_ab"]
    return max(PRUEFEN_MIN, min(PRUEFEN_MAX, n))


def pruefen_ab_setzen(wert, *, speicher=None, motor=None, wer: str = "Nutzer") -> str:
    """Zähl-Trigger setzen. Nicht über `wache_justieren` erreichbar: Die Persönlichkeit
    soll sich ihre Prüfarbeit nicht selbst verringern können."""
    try:
        neu = int(str(wert).strip())
    except ValueError:
        raise Grenzverletzung(f"Eine ganze Zahl von {PRUEFEN_MIN} bis {PRUEFEN_MAX} angeben.")
    if not (PRUEFEN_MIN <= neu <= PRUEFEN_MAX):
        raise Grenzverletzung(f"Prüfen ab nur {PRUEFEN_MIN} bis {PRUEFEN_MAX} Alarmen.")
    alt = pruefen_ab()
    if alt == neu:
        return f"Prüfen ab {neu} Alarmen – steht schon so."
    if not speichern({"pruefen_ab": neu}):
        raise SpeicherFehler(f"Nicht gespeichert: {_speicher_grund()}. Prüfen ab bleibt bei {alt}.")
    _merken(speicher, wer, "pruefen_ab", alt, neu, "über /wache trigger gesetzt")
    _protokollieren(f"pruefen_ab: {alt} → {neu}", wer, stufe="aendert")
    _uebernehmen(motor)
    return f"Prüfen ab {neu} ungeprüften Alarmen (vorher {alt})."


def _uebernehmen(motor) -> None:
    if motor is None:
        return
    try:
        motor.einstellungen_uebernehmen()
    except Exception as exc:
        _vermerken(f"laufende Wache hat die Änderung nicht übernommen ({type(exc).__name__}: {exc})")


def anwenden(was: str, wert, *, wer: str, begruendung: str, speicher=None, motor=None,
             nutzer: bool = False) -> str:
    """Justierung prüfen, speichern, protokollieren, live übernehmen. Gibt den Bestätigungstext.
    `nutzer=True` nur aus /wache – dann gelten NUTZER_GRENZEN statt der Grenzen der Persönlichkeit."""
    begruendung = " ".join(str(begruendung or "").split())
    if len(begruendung) < 10:
        raise Grenzverletzung("Jede Justierung braucht eine Begründung (mindestens ein Satz).")
    schluessel, alt, neu = pruefen(was, wert, nutzer=nutzer)
    if alt == neu:
        return f"{schluessel} steht schon auf {neu} – nichts geändert."
    if not speichern({schluessel: neu}):
        raise SpeicherFehler(f"Nicht gespeichert: {_speicher_grund()}. {schluessel} bleibt {alt}.")
    _merken(speicher, wer, schluessel, alt, neu, begruendung)
    _protokollieren(f"{schluessel}: {alt} → {neu} – {begruendung}", wer, stufe="aendert")
    _uebernehmen(motor)
    return f"Justiert: {schluessel} {alt} → {neu}. Begründung festgehalten."


def letzte_rueckgaengig(speicher, motor=None, wer: str = "Nutzer") -> str:
    """Die jüngste Justierung zurücknehmen (alt wieder setzen)."""
    liste = speicher.justierungen(1)
    if not liste:
        return "Keine Justierung vorhanden."
    j = liste[0]
    import json
    try:
        alt = json.loads(j["alt"].replace("'", '"')) if j["was"] == "stumme_regeln" else \
            (float(j["alt"]) if j["was"] == "schwelle" else
             int(j["alt"]) if j["was"] in ("cooldown", "pruefen_ab") else j["alt"])
    except (ValueError, json.JSONDecodeError):
        alt = j["alt"]
    if not speichern({j["was"]: alt}):
        return f"Nicht zurückgenommen: {_speicher_grund()}. {j['was']} bleibt {j['neu']}."
    _merken(speicher, wer, j["was"], j["neu"], alt, f"Rückgängig ({j['wer']}: {j['begruendung'][:80]})")
    _protokollieren(f"Rückgängig: {j['was']} → {alt}", wer)
    _uebernehmen(motor)
    return f"Zurückgenommen: {j['was']} steht wieder auf {alt}."


def trigger_stufe(name: str) -> int:
    return {"mittel": 2, "hoch": 3, "kritisch": 4, "nie": 99}.get((name or "hoch").lower(), 3)
