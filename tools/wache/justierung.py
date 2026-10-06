"""Einstellungen der Wache – und was die Persönlichkeit davon selbst ändern darf.

Alles steht in `nemicli.config.json` unter "wache". Der Nutzer darf alles
ändern (/wache …). Die Persönlichkeit darf über `wache_justieren` nur drei Dinge, und nur in
den Grenzen, die der Nutzer unter "grenzen" festlegt:

  schwelle      ML-Alarmschwelle (Perzentil), z. B. 0,90 … 0,995
  regel_stumm   eine Regel R001–R014 stumm schalten oder wieder anschalten
  cooldown      Sekunden, in denen derselbe Alarm (Regel + Subjekt) nicht erneut
                meldet, sondern nur den Zähler am ersten hochsetzt („×37“)

Jede Änderung braucht eine Begründung, landet in der Tabelle `justierungen`
(alt → neu) und im Aktions-Protokoll. `/wache justierungen` zeigt sie,
`/wache rueckgaengig` nimmt die letzte zurück. Nichts davon ist stumm.
"""

from __future__ import annotations


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
    "trigger_pause_s": 600,             # danach frühestens wieder wecken
    "trigger_max_pro_tag": 24,
    # Grenzen für die Justierungen der Persönlichkeit
    "grenzen": {"schwelle_min": 0.90, "schwelle_max": 0.995, "cooldown_min": 60,
                "cooldown_max": 3600, "regeln_stumm_max": 3},
}

_TRIGGER = ("mittel", "hoch", "kritisch", "nie")


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


def speichern(neu: dict) -> None:
    try:
        cfg = _config()
        daten = cfg.load()
        alt = daten.get("wache") or {}
        alt.update({k: v for k, v in neu.items() if k != "grenzen"})
        if "grenzen" in neu:
            alt["grenzen"] = {**(alt.get("grenzen") or {}), **neu["grenzen"]}
        daten["wache"] = alt
        cfg.save(daten)
    except Exception:
        pass


class Grenzverletzung(ValueError):
    pass


def pruefen(was: str, wert, e: dict | None = None) -> tuple[str, object, object]:
    """Prüft eine Justierung gegen die Grenzen. Gibt (schluessel, alt, neu) zurück
    oder wirft Grenzverletzung mit einem Text, den die Persönlichkeit versteht."""
    e = e or einstellungen()
    g = e["grenzen"]
    was = (was or "").strip().lower()
    if was in ("schwelle", "threshold", "ml_schwelle"):
        try:
            neu = round(float(str(wert).replace(",", ".")), 3)
        except ValueError:
            raise Grenzverletzung("Die Schwelle muss eine Zahl sein, z. B. 0.95.")
        if not (g["schwelle_min"] <= neu <= g["schwelle_max"]):
            raise Grenzverletzung(f"Die Schwelle darf nur zwischen {g['schwelle_min']} und "
                                  f"{g['schwelle_max']} liegen (Grenze des Nutzers).")
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
        if neu == "nie":
            raise Grenzverletzung("„nie“ darf nur der Nutzer setzen (/wache trigger nie).")
        return "trigger_ab", e["trigger_ab"], neu
    raise Grenzverletzung("Justierbar sind nur: schwelle, cooldown, regel_stumm, trigger_ab.")


def anwenden(was: str, wert, *, wer: str, begruendung: str, speicher=None, motor=None) -> str:
    """Justierung prüfen, speichern, protokollieren, live übernehmen. Gibt den Bestätigungstext."""
    begruendung = " ".join(str(begruendung or "").split())
    if len(begruendung) < 10:
        raise Grenzverletzung("Jede Justierung braucht eine Begründung (mindestens ein Satz).")
    schluessel, alt, neu = pruefen(was, wert)
    if alt == neu:
        return f"{schluessel} steht schon auf {neu} – nichts geändert."
    speichern({schluessel: neu})
    if speicher is not None:
        try:
            speicher.justierung_merken(wer, schluessel, alt, neu, begruendung)
        except Exception:
            pass
    try:
        import protokoll
        protokoll.schreibe("wache_justieren", f"{schluessel}: {alt} → {neu} – {begruendung}",
                           "success", veraendernd=True, wer=wer, stufe="aendert")
    except Exception:
        pass
    if motor is not None:
        try:
            motor.einstellungen_uebernehmen()
        except Exception:
            pass
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
             int(j["alt"]) if j["was"] == "cooldown" else j["alt"])
    except (ValueError, json.JSONDecodeError):
        alt = j["alt"]
    speichern({j["was"]: alt})
    speicher.justierung_merken(wer, j["was"], j["neu"], alt, f"Rückgängig ({j['wer']}: {j['begruendung'][:80]})")
    try:
        import protokoll
        protokoll.schreibe("wache_justieren", f"Rückgängig: {j['was']} → {alt}", "success",
                           veraendernd=True, wer=wer)
    except Exception:
        pass
    if motor is not None:
        motor.einstellungen_uebernehmen()
    return f"Zurückgenommen: {j['was']} steht wieder auf {alt}."


def trigger_stufe(name: str) -> int:
    return {"mittel": 2, "hoch": 3, "kritisch": 4, "nie": 99}.get((name or "hoch").lower(), 3)
