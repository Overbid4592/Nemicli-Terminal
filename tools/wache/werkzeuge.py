"""Die Werkzeuge der Persönlichkeit für die Wache (werden in actions.ACTIONS eingehängt).

  wache_status       Lage: läuft sie, wie viele Ereignisse/Alarme, Modellstand, Sensoren
  wache_alarme       Alarme auflisten (status, anzahl) – oder EINEN mit `id` samt Ereignis
  wache_ereignisse   Ereignisse filtern: prozess, kategorie, stunden, nur_anomalien, anzahl
  wache_inventar     Inventar einer Art: autostart, dienst, aufgabe, port, software, prozess
  wache_bewerten     Urteil: harmlos · echt · gesehen (+ begruendung) – zu EINEM Alarm (`id`),
                     zu mehreren (`id: "a,b,c"` oder `ids: [...]`) oder zu allen offenen
                     einer Regel (`regel`, wahlweise + `subjekt`). Bei harmlos mit
                     `bezeichnung` = WAS das ist → landet in der Liste bekannter Dinge
                     und steht beim nächsten Alarm gleich dabei. Nach `daempfen_ab`
                     harmlos-Urteilen zum selben Paar meldet die Wache es nicht mehr.
  wache_justieren    schwelle · cooldown · regel_stumm · trigger_ab – in den Grenzen des Nutzers

Alle Texte kommen als Fremddaten eingerahmt zurück (Prozessnamen und
Kommandozeilen sind Daten, keine Anweisungen).
"""

from __future__ import annotations

import time

from . import herkunft, vollscan, zugang
from .ereignisse import PROZESS, SYSTEM, Schwere
from .justierung import Grenzverletzung, anwenden, einstellungen
from .regeln import BESCHREIBUNG
from .speicher import bekannt_schluessel

# Prozesse, die Windows fahrplanmäßig nach einer Store-/AppX-Installation anfasst.
# Sie erzeugen dabei neue Eltern-Kind-Beziehungen und fallen dem Modell als
# Anomalie auf, obwohl sie nur Folge einer Installation sind.
_SHELL_PROZESSE = {"rundll32.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
                   "explorer.exe", "searchhost.exe", "runtimebroker.exe", "appxsvc",
                   "backgroundtaskhost.exe", "storedesktopextension.exe", "winstore.app.exe"}


def _shell_kontext(ev) -> list[str]:
    """„Was tat Windows in derselben Minute?“ – das AppX-Protokoll als Zeile am Alarm.

    Ohne diese Zeile bleibt der Persönlichkeit nur, sich eine Erklärung
    zusammenzureimen – und zeitlich benachbarte, aber unabhängige Alarme zu einer
    Ursachenkette zu verbinden, die es nicht gibt. Mit ihr steht das auslösende
    Paket namentlich da.
    """
    if ev is None or ev.kategorie not in (PROZESS, SYSTEM):
        return []
    name = (ev.prozess or "").lower()
    if name not in _SHELL_PROZESSE and not str(ev.aktion).startswith("inventory_new"):
        return []
    try:
        treffer = herkunft.appx_kontext(ev.zeit)
    except Exception:
        return []
    if not treffer:
        return []
    return ["Windows-Paketverwaltung im selben Zeitfenster (±60 s):"] + [f"  {z}" for z in treffer]


# Prozesse, die Windows Update und die Nacharbeit danach fahren.
_UPDATE_PROZESSE = {"tiworker.exe", "trustedinstaller.exe", "mousocoreworker.exe", "usoclient.exe",
                    "wuauclt.exe", "musnotification.exe", "musnotificationux.exe", "dismhost.exe",
                    "setuphost.exe", "ngen.exe", "ngentask.exe", "mscorsvw.exe", "sihclient.exe",
                    "wuaucltcore.exe", "uhssvc.exe", "compattelrunner.exe"}


def _update_kontext(ev) -> list[str]:
    """„Hat Windows Update gerade etwas installiert?“ – belegt aus den Protokollen.

    Ohne diese Zeilen erklärt die Persönlichkeit Neues gern mit „nach einem
    Update“, ohne dass ein Update nachgewiesen ist. Steht hier nichts, ist diese
    Erklärung nicht belegt.
    """
    if ev is None or ev.kategorie not in (PROZESS, SYSTEM):
        return []
    name = (ev.prozess or "").lower()
    if not (name in _UPDATE_PROZESSE or str(ev.aktion).startswith("inventory_new")
            or herkunft.ist_systemort(str(ev.exe or ""))):
        return []
    try:
        treffer = herkunft.update_kontext(ev.zeit)
    except Exception:
        return []
    if not treffer:
        return ["Windows Update in den 24 h davor: laut Protokoll nichts installiert."]
    return ["Windows Update in den 24 h davor (Setup-/WindowsUpdateClient-Protokoll):"] + [
        f"  {z}" for z in treffer]


def bekannt_zeile(b: dict) -> str:
    seit = time.strftime("%d.%m.", time.localtime(b["erstmals"]))
    n = f", {b['anzahl']}×" if b.get("anzahl", 1) > 1 else ""
    return (f"{b['name']} = {b['bezeichnung']} – harmlos seit {seit}{n}"
            + (f" ({b['wer']}: {b['begruendung']})" if b.get("begruendung") else ""))


def bekannt_hinweise(sp, ereignis) -> list[str]:
    """Zeilen „bekannt: …“ zu einem Ereignis – leer, wenn nichts bekannt ist."""
    try:
        return ["bekannt: " + bekannt_zeile(b) for b in sp.bekannt_fuer(ereignis)]
    except Exception:
        return []


def _rahmen(text: str, art: str = "ausgabe", quelle: str = "Wache") -> str:
    try:
        import fremddaten
        return fremddaten.rahmen(text, art, quelle)
    except Exception:
        return text


def _dauer(s: float) -> str:
    s = int(s)
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h {(s % 3600) // 60} min"
    return f"{s // 86400} d {(s % 86400) // 3600} h"


def status(a: dict) -> str:
    sp = zugang.speicher()
    e = einstellungen()
    info = zugang.laeuft()
    zeilen = []
    if info:
        zeilen.append(f"Wache läuft (PID {info['pid']}, seit {_dauer(time.time() - float(info.get('start') or time.time()))})")
    else:
        zeilen.append("Wache läuft NICHT (der Nutzer startet sie mit /wache start).")
    if zugang.motor is not None:
        m = zugang.motor.stand()
        zeilen.append(f"Verarbeitet: {m['verarbeitet']} Ereignisse ({m['pro_sekunde']:.2f}/s), "
                      f"{m['alarme']} Alarme, {m['anomalien']} Anomalien seit Start"
                      + (f", {m['verdichtet']} Wiederholungen nur gezählt" if m.get("verdichtet") else ""))
        if m.get("gedaempft"):
            zeilen.append("Gedämpft (meldet nicht mehr, nur gezählt): "
                          + ", ".join(f"{k} ({h}× harmlos, seit Start {n}× still)"
                                      for k, (h, n) in m["gedaempft"].items())
                          + " – aufheben: einen alten Alarm des Paars als echt bewerten")
        zeilen.append("Sensoren: " + ", ".join(f"{s['name']} {'läuft' if s['laeuft'] else 'STEHT'} "
                                                f"({s['ereignisse']} Ereignisse, {s['fehler']} Fehler)"
                                                for s in m["sensoren"]))
        ml = m["ml"]
        zeilen.append(f"Modell Prozesse/Netz: {'trainiert' if ml['trainiert'] else 'KALTSTART'} – "
                      f"{ml['ereignisse']} Ereignisse, {ml['laeufe']} Läufe, Schwelle {ml['schwelle']}, "
                      f"nächstes Training in {m['training_alle'] - m['seit_training']} Ereignissen")
        mld = m["ml_dateien"]
        zeilen.append(f"Modell Dateien: {'trainiert' if mld['trainiert'] else 'Kaltstart'} – {mld['ereignisse']} Ereignisse")
        inv = m["inventar"]
        zeilen.append(f"Inventar: {sum(inv['zaehlung'].values())} Einträge "
                      + ", ".join(f"{n} {k}" for k, n in sorted(inv["zaehlung"].items()))
                      + (f" · zuletzt vor {_dauer(time.time() - inv['zuletzt'])}" if inv["zuletzt"] else ""))
        if m["fehler"]:
            zeilen.append("Fehler: " + " | ".join(m["fehler"]))
    else:
        zeilen.append(f"Gespeichert: {sp.anzahl_ereignisse()} Ereignisse, {sp.anzahl_profile()} Prozess-Profile")
        inv = sp.inventar_zaehlung()
        zeilen.append("Inventar: " + (", ".join(f"{n} {k}" for k, n in sorted(inv.items())) or "noch keins"))
    st = sp.alarm_statistik()
    zeilen.append(f"Alarme: {st.get('offen', 0)} offen, {st.get('gesehen', 0)} gesehen, "
                  f"{st.get('harmlos', 0)} harmlos, {st.get('echt', 0)} ECHT ({st.get('gesamt', 0)} gesamt)")
    zeilen.append(f"Einstellungen: Schwelle {e['schwelle']}, Cooldown {e['cooldown']}s (gleicher Alarm = "
                  f"Zähler statt neue Zeile), Dämpfung ab {e.get('daempfen_ab', 3)}× harmlos, wecken ab "
                  f"{e['trigger_ab']}, Training alle {e['training_alle']}, stumme Regeln: "
                  f"{', '.join(e.get('stumme_regeln') or []) or 'keine'}")
    g = e["grenzen"]
    zeilen.append(f"Deine Grenzen: Schwelle {g['schwelle_min']}–{g['schwelle_max']}, Cooldown "
                  f"{g['cooldown_min']}–{g['cooldown_max']}s, höchstens {g['regeln_stumm_max']} stumme Regeln")
    return _rahmen("\n".join(zeilen), quelle="Wache-Status")


def alarme(a: dict) -> str:
    sp = zugang.speicher()
    aid = str(a.get("id") or "").strip().lstrip("#")
    if aid:
        al = sp.alarm(aid)
        if al is None:
            return f"Kein Alarm mit id {aid}."
        zeilen = [al.kurz(), al.text, f"Status: {al.status}"
                  + (f" ({al.urteil_von}: {al.begruendung})" if al.urteil_von else "")]
        ev = sp.ereignis(al.ereignis_id) if al.ereignis_id else None
        if ev:
            zeilen.extend(bekannt_hinweise(sp, ev))
            zeilen.append("Ereignis: " + ev.kurz())
            for feld in ("prozess", "pid", "ppid", "eltern", "nutzer", "exe", "cmdline", "lokal",
                         "ziel", "zielport", "protokoll", "datei", "score"):
                w = getattr(ev, feld)
                if w:
                    zeilen.append(f"  {feld}: {str(w)[:400]}")
                # Signatur und Vollscan-Beleg direkt unter den Pfad, ungefragt –
                # nachfragen würde die Persönlichkeit bei vielen Alarmen nicht mehr.
                if feld == "exe" and w:
                    zeile = herkunft.signatur_zeile(str(w))
                    if zeile:
                        zeilen.append(f"  herkunft: {zeile}")
                if feld in ("exe", "datei") and w:
                    beleg = vollscan.zeile(str(w))
                    if beleg:
                        zeilen.append(f"  vollscan: {beleg}")
            if ev.extra:
                zeilen.append(f"  extra: {ev.extra}")
            for z in _shell_kontext(ev):
                zeilen.append(z)
            for z in _update_kontext(ev):
                zeilen.append(z)
            if ev.prozess:
                p = sp.profile().get(ev.prozess)
                if p:
                    zeilen.append(f"Profil {ev.prozess}: {p['anzahl']}× gesehen, Pfade: {p['exes'] or '-'}, "
                                  f"Eltern: {p['eltern'] or '-'}, Kinder: {p['kinder'] or '-'}, Ports: {p['ports'] or '-'}")
        return _rahmen("\n".join(zeilen), quelle=f"Alarm {al.id}")
    try:
        n = max(1, min(int(a.get("anzahl", 20)), 100))
    except (TypeError, ValueError):
        n = 20
    stat = str(a.get("status") or "").strip().lower()
    liste = sp.alarme(limit=n, status=stat if stat in ("offen", "gesehen", "harmlos", "echt") else "")
    if not liste:
        return "Keine Alarme" + (f" mit Status {stat}" if stat else "") + "."
    zeilen = []
    gruppen: dict[tuple[str, str], int] = {}
    for x in liste:
        zeilen.append(x.kurz())
        if x.subjekt and x.status in ("offen", "gesehen"):
            gruppen[(x.regel, x.subjekt)] = gruppen.get((x.regel, x.subjekt), 0) + 1
        try:
            ev = sp.ereignis(x.ereignis_id) if x.ereignis_id else None
            for b in sp.bekannt_fuer(ev):
                zeilen.append(f"      ↳ bekannt: {b['name']} = {b['bezeichnung']}")
        except Exception:
            pass
    mehrfach = [(k, n) for k, n in gruppen.items() if n >= 3]
    if mehrfach:
        # Dieselbe Regel zum selben Subjekt, mehrfach offen – das geht in einem Rutsch.
        zeilen.append("Mehrfach offen – in EINEM Aufruf bewertbar: "
                      + "; ".join(f'{n}× {r} zu "{s}" → wache_bewerten regel="{r}" subjekt="{s}"'
                                  for (r, s), n in sorted(mehrfach, key=lambda p: -p[1])))
    return _rahmen("\n".join(zeilen), quelle=f"{len(liste)} Alarme")


def ereignisse(a: dict) -> str:
    sp = zugang.speicher()
    try:
        n = max(1, min(int(a.get("anzahl", 30)), 200))
    except (TypeError, ValueError):
        n = 30
    seit = None
    try:
        std = float(a.get("stunden", 0) or 0)
        if std > 0:
            seit = time.time() - std * 3600
    except (TypeError, ValueError):
        pass
    mind = 0
    if a.get("mindestens"):
        mind = int(Schwere.aus_text(str(a["mindestens"])))
    liste = sp.ereignisse(limit=n, seit=seit, kategorie=str(a.get("kategorie") or ""),
                          prozess=str(a.get("prozess") or ""),
                          nur_anomalien=bool(a.get("nur_anomalien")), mindestens=mind)
    if not liste:
        return "Keine passenden Ereignisse."
    zeilen = []
    for e in liste:
        z = e.kurz()
        if e.score:
            z += f" · Score {e.score:.3f}"
        if e.exe:
            z += f" · {e.exe}"
        if e.ziel:
            z += f" · → {e.ziel}:{e.zielport}"
        zeilen.append(z)
    return _rahmen("\n".join(zeilen), quelle=f"{len(liste)} Ereignisse")


def inventar(a: dict) -> str:
    sp = zugang.speicher()
    art = str(a.get("art") or "").strip().lower()
    if art in ("bekannt", "harmlos", "bekanntes"):
        return bekannt_text(sp)
    if not art:
        z = sp.inventar_zaehlung()
        return _rahmen("\n".join(f"{k}: {n}" for k, n in sorted(z.items())) or "Noch kein Inventar.",
                       quelle="Inventar")
    liste = sp.inventar(art=art)
    if not liste:
        return f"Keine Einträge der Art {art}. Arten: prozess, autostart, dienst, aufgabe, software, port, schnittstelle, nutzer."
    zeilen = []
    for e in liste[:300]:
        seit = time.strftime("%d.%m.", time.localtime(e["erstmals"]))
        zeilen.append(f"{e['name']}" + (f"  →  {e['detail']}" if e["detail"] else "") + f"  (seit {seit})")
    if len(liste) > 300:
        zeilen.append(f"… {len(liste) - 300} weitere")
    return _rahmen("\n".join(zeilen), quelle=f"Inventar {art} ({len(liste)})")


def bekannt_text(sp, art: str = "") -> str:
    liste = sp.bekannt_liste(art)
    if not liste:
        return "Noch nichts als bekannt eingetragen – kommt mit wache_bewerten (harmlos + bezeichnung)."
    return _rahmen("\n".join(f"[{b['art']}] " + bekannt_zeile(b) for b in liste),
                   quelle=f"Bekannt ({len(liste)})")


def _alarme_auswaehlen(sp, a: dict) -> tuple[list, str]:
    """Welche Alarme meint das Urteil? (Liste, Beschreibung) – oder ([], Fehlertext).
    Reihenfolge: ids → id (auch „a,b,c“) → regel (+ subjekt)."""
    roh = a.get("ids")
    if roh is None:
        roh = a.get("id") or ""
    if isinstance(roh, (list, tuple)):
        ids = [str(x) for x in roh]
    else:
        ids = str(roh).replace(";", ",").replace(" ", ",").split(",")
    ids = [x.strip().lstrip("#") for x in ids if x.strip().lstrip("#")]
    if ids:
        gefunden, fehlend = [], []
        for x in ids:
            al = sp.alarm(x)
            (gefunden if al else fehlend).append(al or x)
        if fehlend and not gefunden:
            return [], f"Kein Alarm mit id {', '.join(fehlend)}."
        if len(gefunden) == 1 and not fehlend:
            return gefunden, ""
        return gefunden, (f"{len(gefunden)} Alarm{'e' if len(gefunden) != 1 else ''}"
                          + (f" (nicht gefunden: {', '.join(fehlend)})" if fehlend else ""))
    regel = str(a.get("regel") or "").strip().upper().replace("ML001", "ML-001")
    if not regel:
        return [], "Fehler: id (eine oder mehrere, kommagetrennt), ids oder regel angeben."
    if regel not in BESCHREIBUNG and regel != "ML-001":
        return [], f"Fehler: unbekannte Regel {regel} (R001–R014 oder ML-001)."
    subjekt = " ".join(str(a.get("subjekt") or a.get("prozess") or a.get("ziel") or "").split())
    liste = sp.alarme_unbeurteilt(regel, subjekt)
    if not liste:
        return [], f"Keine unbeurteilten Alarme zu {regel}" + (f' mit Subjekt "{subjekt}"' if subjekt else "") + "."
    return liste, (f"{len(liste)} Alarm{'e' if len(liste) != 1 else ''} zu {regel}"
                   + (f' · "{subjekt}"' if subjekt else "") + " (offen/gesehen)")


def bewerten(a: dict, wer: str = "Persönlichkeit") -> str:
    sp = zugang.speicher()
    urteil = str(a.get("urteil") or "").strip().lower()
    grund = " ".join(str(a.get("begruendung") or "").split())
    bezeichnung = " ".join(str(a.get("bezeichnung") or "").split())
    if urteil in ("fehlalarm", "ok", "normal", "unbedenklich"):
        urteil = "harmlos"
    if urteil in ("wahr", "true", "bestaetigt", "bestätigt", "verdaechtig", "verdächtig"):
        urteil = "echt"
    if urteil not in ("harmlos", "echt", "gesehen"):
        return "Fehler: urteil muss harmlos, echt oder gesehen sein."
    if urteil != "gesehen" and len(grund) < 10:
        return "Fehler: ein Urteil braucht eine Begründung (mindestens ein Satz)."
    liste, was = _alarme_auswaehlen(sp, a)
    if not liste:
        return was
    ab = int(einstellungen().get("daempfen_ab", 3))
    vorher = sp.gedaempft(ab) if urteil == "harmlos" else {}
    gemerkt: list[str] = []
    gesehen: set[str] = set()
    for al in liste:
        sp.alarm_urteil(al.id, urteil, wer, grund)
        if urteil == "harmlos" and bezeichnung and al.ereignis_id:
            # Bezeichnung = WAS das ist. Bleibt am Prozess/Ziel/Pfad kleben und steht
            # beim nächsten Alarm gleich dabei – nichts muss zweimal geprüft werden.
            # Bei einer Gruppe zählt jeder Schlüssel einmal, nicht je Alarm.
            ev = sp.ereignis(al.ereignis_id)
            for schluessel, art, name in bekannt_schluessel(ev):
                if schluessel in gesehen:
                    continue
                gesehen.add(schluessel)
                sp.bekannt_merken(schluessel, art, name, bezeichnung, grund, wer)
                gemerkt.append(name)
    neu_gedaempft = []
    if urteil == "harmlos":
        namen = {(al.regel, al.subjekt.lower()): al.subjekt for al in liste if al.subjekt}
        neu_gedaempft = [f"{r} · {namen.get((r, s), s)}" for r, s in sp.gedaempft(ab) if (r, s) not in vorher]
    if zugang.motor is not None:
        if urteil == "harmlos":
            zugang.motor.detektor.feedback(sp.fehlalarm_prozesse())
        zugang.motor.daempfung_aktualisieren()
    try:
        import protokoll
        al = liste[0]
        text = (f"{was} → {urteil}: {grund}" if was
                else f"Alarm {al.id} ({al.regel} · {al.titel}) → {urteil}: {grund}")
        protokoll.schreibe("wache_bewerten", text, "success", veraendernd=True, wer=wer,
                           stufe="riskant" if urteil == "echt" else "harmlos")
        for paar in neu_gedaempft:
            protokoll.schreibe("wache_daempfung", f"{paar} meldet nicht mehr – wiederholt als harmlos "
                               f"beurteilt ({wer}). Aufheben: einen Alarm des Paars als echt bewerten.",
                               "success", veraendernd=True, wer="Wache", stufe="harmlos")
    except Exception:
        pass
    kopf = f"{was} → {urteil}." if was else f"Alarm {liste[0].id} → {urteil}."
    return (kopf + (" Der Prozess wird künftig gedämpft und fließt beim "
                    "nächsten Training als normal ein." if urteil == "harmlos" else "")
            + (f" Als bekannt gemerkt: {', '.join(gemerkt)} = {bezeichnung}." if gemerkt else "")
            + (f" Ab jetzt still (oft genug harmlos): {', '.join(neu_gedaempft)}." if neu_gedaempft else "")
            + (" (Tipp: mit bezeichnung=… merkst du dir, WAS das ist – dann steht es beim nächsten "
               "Alarm gleich dabei.)" if urteil == "harmlos" and not bezeichnung else "")
            + (" Bleibt für den Nutzer sichtbar (rot im Tray)." if urteil == "echt" else ""))


def justieren(a: dict, wer: str = "Persönlichkeit") -> str:
    was = str(a.get("was") or "").strip()
    wert = a.get("wert")
    grund = str(a.get("begruendung") or "")
    try:
        return anwenden(was, wert, wer=wer, begruendung=grund, speicher=zugang.speicher(),
                        motor=zugang.motor)
    except Grenzverletzung as exc:
        return f"Nicht justiert: {exc}"


def regeln_text() -> str:
    e = einstellungen()
    stumm = set(e.get("stumme_regeln") or [])
    return "\n".join(f"{k}  {'STUMM ' if k in stumm else '      '} {v}" for k, v in sorted(BESCHREIBUNG.items()))
