"""Die Werkzeuge der Persönlichkeit für die Wache (werden in actions.ACTIONS eingehängt).

  wache_status       Lage: läuft sie, wie viele Ereignisse/Alarme, Modellstand, Sensoren
  wache_alarme       Alarme auflisten (status, anzahl) – oder EINEN mit `id` samt Ereignis
  wache_ereignisse   Ereignisse filtern: prozess, kategorie, stunden, nur_anomalien, anzahl
  wache_inventar     Inventar einer Art: autostart, dienst, aufgabe, port, software, prozess
  wache_bewerten     Urteil: harmlos · echt · gesehen · unpruefbar (+ begruendung) – zu EINEM Alarm (`id`),
                     zu mehreren (`id: "a,b,c"` oder `ids: [...]`) oder zu allen offenen
                     einer Regel (`regel`, wahlweise + `subjekt`). Bei harmlos mit
                     `bezeichnung` = WAS das ist → landet in der Liste bekannter Dinge
                     und steht beim nächsten Alarm gleich dabei. Nach `daempfen_ab`
                     harmlos-Urteilen zum selben Paar meldet die Wache es nicht mehr.
  wache_netz         Netzwächter: Auskunft zu einer Adresse (ip oder eingefügte Meldung in text) oder
                     Übersicht der Verbindungen nach draußen
  wache_justieren    schwelle · cooldown · regel_stumm · trigger_ab (nur mittel/hoch) – in den
                     Grenzen des Nutzers

Alle Texte kommen als Fremddaten eingerahmt zurück (Prozessnamen und
Kommandozeilen sind Daten, keine Anweisungen).
"""

from __future__ import annotations

import re
import time

from . import herkunft, vollscan, zugang
from .ereignisse import PROZESS, SYSTEM, Schwere
from .justierung import Grenzverletzung, anwenden, einstellungen, pruefen_ab
from .regeln import BESCHREIBUNG

# Nach Lage harmlos, aber der Beleg liegt in einem Bereich, in den die Wache nicht darf (Systembereich,
# Registry, Schutzsoftware, Admin). Zählt getrennt, dämpft nicht, lernt nicht (speicher.py).
UNPRUEFBAR = "unpruefbar"
_UNPRUEFBAR_WORTE = {"unpruefbar", "unprüfbar", "nicht_pruefbar", "nicht pruefbar", "nicht prüfbar",
                     "nicht_prüfbar", "nicht voll prüfbar", "nicht voll pruefbar"}
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


def einordnung_zeile(score) -> str:
    """Score (Perzentil des Modells) in Worten – für die Erklärung an den Nutzer."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return ""
    if not 0 < s <= 1:
        return ""
    stufe = "sehr ungewöhnlich" if s >= 0.99 else "ungewöhnlich" if s >= 0.95 else "etwas auffällig"
    return f"Einordnung: {stufe} – auffälliger als {s * 100:.0f} % von dem, was die Wache als normal gelernt hat."


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
        rw = getattr(zugang.motor, "regeln", None)
        if rw is not None and hasattr(rw, "sigma"):
            weg = rw.sigma_uebersprungen
            zeilen.append(f"Sigma-Regeln: {len(rw.sigma)} aktiv (mitgeliefert + Wache-Ordner sigma/)"
                          + (f", {len(weg)} nicht geladen (Feld oder Bedingung unbekannt): "
                             + "; ".join(weg[:5]) if weg else ""))
        ml = m["ml"]
        zeilen.append(f"Modell Prozesse/Netz: {'trainiert' if ml['trainiert'] else 'KALTSTART'} – "
                      f"{ml['ereignisse']} Ereignisse, {ml['laeufe']} Läufe, Schwelle {ml['schwelle']}, "
                      + ("zurückgedreht – lernt erst nach einem Training durch den Nutzer wieder" if ml.get("pausiert") else
                         f"nächstes Training in {m['training_alle'] - m['seit_training']} Ereignissen")
                      + f", lernt nur aus Ereignissen älter als {e.get('ml_karenz_h', 24)} h")
        mld = m["ml_dateien"]
        zeilen.append(f"Modell Dateien: {'trainiert' if mld['trainiert'] else 'Kaltstart'} – {mld['ereignisse']} Ereignisse")
        for d in (ml, mld):
            if d.get("fehler"):
                zeilen.append(f"Modell {d['label']}: {d['fehler']}")
        zeilen.append(folgen_zeile(m.get("dl") or {}))
        zeilen.append(netz_zeile(m.get("netz") or {}))
        if m.get("gestoert"):
            zeilen.append(f"GESTÖRT – der Motor meldet wiederholt Fehler: {m['gestoert']}")
        inv = m["inventar"]
        zeilen.append(f"Inventar: {sum(inv['zaehlung'].values())} Einträge "
                      + ", ".join(f"{n} {k}" for k, n in sorted(inv["zaehlung"].items()))
                      + (f" · zuletzt vor {_dauer(time.time() - inv['zuletzt'])}" if inv["zuletzt"] else ""))
        if m["fehler"]:
            zeilen.append("Fehler: " + " | ".join(m["fehler"]))
    else:
        zeilen.append(f"Gespeichert: {sp.anzahl_ereignisse()} Ereignisse, {sp.anzahl_profile()} Prozess-Profile")
        from . import MODELL_ORDNER, folgen
        fi = folgen.info(MODELL_ORDNER / "folgen")
        zeilen.append(folgen_zeile({**fi, "an": e.get("dl", True), "alarm": e.get("dl_alarm", False),
                                    "schwelle": e.get("dl_schwelle")}))
        inv = sp.inventar_zaehlung()
        zeilen.append("Inventar: " + (", ".join(f"{n} {k}" for k, n in sorted(inv.items())) or "noch keins"))
    st = sp.alarm_statistik()
    zeilen.append(f"Alarme: {st.get('offen', 0)} offen, {st.get('gesehen', 0)} gesehen, "
                  f"{st.get('harmlos', 0)} harmlos, {st.get('unpruefbar', 0)} nicht voll prüfbar, "
                  f"{st.get('echt', 0)} ECHT, {st.get('erledigt', 0)} erledigt ({st.get('gesamt', 0)} gesamt)")
    zeilen.append(f"Einstellungen: Schwelle {e['schwelle']}, Cooldown {e['cooldown']}s (gleicher Alarm = "
                  f"Zähler statt neue Zeile), Dämpfung ab {e.get('daempfen_ab', 3)}× harmlos, wecken ab "
                  f"{e['trigger_ab']} und ab {pruefen_ab(e)} ungeprüften Alarmen (setzt nur der Nutzer), Training alle {e['training_alle']}, stumme Regeln: "
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
        from .regeln import ATTACK
        if al.regel in ATTACK:
            zeilen.append(f"ATT&CK: {ATTACK[al.regel]}")
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
            if (einordnung := einordnung_zeile(ev.score)):
                zeilen.append(einordnung)
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
    stat = UNPRUEFBAR if stat in _UNPRUEFBAR_WORTE else stat
    liste = sp.alarme(limit=n, status=stat if stat in ("offen", "gesehen", "harmlos", UNPRUEFBAR, "echt", "erledigt")
                      else "")
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
    regel = str(a.get("regel") or "").strip().upper()
    for alt, neu in (("ML001", "ML-001"), ("DL001", "DL-001"), ("ML002", "ML-002"), ("DL002", "DL-002")):
        regel = regel.replace(alt, neu)
    if not regel:
        return [], "Fehler: id (eine oder mehrere, kommagetrennt), ids oder regel angeben."
    if regel not in BESCHREIBUNG and regel not in ("ML-001", "DL-001", "ML-002", "DL-002"):
        return [], f"Fehler: unbekannte Regel {regel} (R001–R017, VS-001, ML-001/002 oder DL-001/002)."
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
    if urteil in ("behoben", "bereinigt", "abgeschlossen", "erledigt"):
        urteil = "erledigt"
    if urteil in _UNPRUEFBAR_WORTE:
        urteil = UNPRUEFBAR
    if urteil not in ("harmlos", "echt", "gesehen", UNPRUEFBAR, "erledigt"):
        return "Fehler: urteil muss harmlos, echt, gesehen, unpruefbar oder erledigt sein."
    if urteil != "gesehen" and len(grund) < 10:
        return "Fehler: ein Urteil braucht eine Begründung (mindestens ein Satz)."
    liste, was = _alarme_auswaehlen(sp, a)
    if not liste:
        return was
    if urteil == "erledigt":
        # Abschließen gibt es nur für echte Funde – sie bleiben im Verlauf, das Schild wird wieder grün.
        liste = [al for al in liste if al.status == "echt"]
        if not liste:
            return "Fehler: als erledigt abschließen lassen sich nur Alarme, die als echt beurteilt sind."
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
        zugang.motor.daempfung_aktualisieren()        # Dämpfung, Pfad-Feedback, offene Pfade
    try:
        import protokoll
        al = liste[0]
        text = (f"{was} → {urteil}: {grund}" if was
                else f"Alarm {al.id} ({al.regel} · {al.titel}) → {urteil}: {grund}")
        protokoll.schreibe("wache_bewerten", text, "success", veraendernd=True, wer=wer,
                           stufe="riskant" if urteil in ("echt", "erledigt") else "harmlos")
        for paar in neu_gedaempft:
            protokoll.schreibe("wache_daempfung", f"{paar} meldet nicht mehr – wiederholt als harmlos "
                               f"beurteilt ({wer}). Aufheben: einen Alarm des Paars als echt bewerten.",
                               "success", veraendernd=True, wer="Wache", stufe="harmlos")
    except Exception:
        pass
    kopf = f"{was} → {urteil}." if was else f"Alarm {liste[0].id} → {urteil}."
    return (kopf + (" Hat das Programm einen lesbaren Pfad, wird es an genau diesem Pfad künftig leiser bewertet "
                    "(ohne Pfad, z. B. „System“, nicht) – Ausreißer melden weiter – und fließt beim nächsten Training "
                    "als normal ein." if urteil == "harmlos" else "")
            + (f" Als bekannt gemerkt: {', '.join(gemerkt)} = {bezeichnung}." if gemerkt else "")
            + (f" Ab jetzt still (oft genug harmlos): {', '.join(neu_gedaempft)}." if neu_gedaempft else "")
            + (" (Tipp: mit bezeichnung=… merkst du dir, WAS das ist – dann steht es beim nächsten "
               "Alarm gleich dabei.)" if urteil == "harmlos" and not bezeichnung else "")
            + (" Bleibt für den Nutzer sichtbar (rot im Tray), bis er ihn als erledigt abschließt."
               if urteil == "echt" else "")
            + (" Abgeschlossen: bleibt im Verlauf, das Schild ist dafür nicht mehr rot." if urteil == "erledigt" else "")
            + (" Zählt getrennt als „nicht voll prüfbar“: dämpft nichts, geht nicht ins Normalbild – kommt es "
               "wieder, meldet die Wache es wieder." if urteil == UNPRUEFBAR else ""))


def justieren(a: dict, wer: str = "Persönlichkeit") -> str:
    was = str(a.get("was") or "").strip()
    wert = a.get("wert")
    grund = str(a.get("begruendung") or "")
    try:
        return anwenden(was, wert, wer=wer, begruendung=grund, speicher=zugang.speicher(),
                        motor=zugang.motor)
    except Grenzverletzung as exc:
        return f"Nicht justiert: {exc}"


def folgen_zeile(dl: dict) -> str:
    """Eine Zeile zum Folgen-Netz für Status und Bericht."""
    if not dl.get("an", True):
        return "Folgen-Netz (DL-001): aus"
    modus = "WARNT (Alarme DL-001)" if dl.get("alarm") else "Schattenmodus (nur Protokoll, keine Alarme)"
    if not dl.get("trainiert"):
        return f"Folgen-Netz (DL-001): {modus} – noch nicht trainiert (lernt aus Ereignissen, die älter als die Karenzzeit sind)"
    zuletzt = dl.get("zuletzt") or 0
    teile = [f"Folgen-Netz (DL-001): {modus} – Version {dl.get('version', '?')}",
             f"{dl.get('folgen', '?')} Prozess-Ketten",
             f"Schwelle {dl.get('schwelle', '?')}"]
    if zuletzt:
        teile.append(f"trainiert vor {_dauer(time.time() - float(zuletzt))}")
    if dl.get("pausiert"):
        teile.append("zurückgedreht – lernt erst nach einem Training durch den Nutzer wieder")
    if "auffaellig_seit_start" in dl:
        teile.append(f"{dl['auffaellig_seit_start']} auffällige Schritte seit Start")
    return ", ".join(teile)


def regeln_text() -> str:
    e = einstellungen()
    stumm = set(e.get("stumme_regeln") or [])
    zeilen = [f"{k}  {'STUMM ' if k in stumm else '      '} {v}" for k, v in sorted(BESCHREIBUNG.items())]
    zeilen.append("ML-001         Isolation Forest: einzelnes Ereignis weicht vom gelernten Normalbild ab")
    zeilen.append("DL-001         Folgen-Netz: ungewöhnlicher Schritt in einer Prozess-Kette"
                  + ("" if e.get("dl_alarm") else " (Schattenmodus – meldet nur ins Protokoll)"))
    netz_hinweis = "" if e.get("netz_alarm") else " (Schattenmodus – meldet nur ins Protokoll)"
    zeilen.append("ML-002         Netz-Modell: einzelne Verbindung weicht vom Gelernten ab" + netz_hinweis)
    zeilen.append("DL-002         Netz-Folgen: ungewöhnliche Abfolge oder Takt der Verbindungen eines Programms"
                  + netz_hinweis)
    return "\n".join(zeilen)

_IP = re.compile(r"(?<![\w.:])(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7})(?![\w.:])")


def _adressen(text: str) -> list[str]:
    import ipaddress
    out = []
    for m in _IP.findall(text or ""):
        try:
            out.append(str(ipaddress.ip_address(m)))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


def netz_zeile(n: dict) -> str:
    """Eine Zeile zum Netzwächter für Status und Bericht."""
    if not n.get("an", True):
        return "Netzwächter (ML-002/DL-002): aus"
    modus = "WARNT (Alarme ML-002/DL-002)" if n.get("alarm") else "Schattenmodus (nur Protokoll, keine Alarme)"
    ml, dl = n.get("ml") or {}, n.get("dl") or {}
    teile = [f"Netzwächter: {modus}",
             f"Netz-Modell {'trainiert' if ml.get('trainiert') else 'noch nicht trainiert'}",
             f"Netz-Folgen {('Version ' + str(dl.get('version'))) if dl.get('trainiert') else 'noch nicht trainiert'}"]
    if "auffaellig_seit_start" in n:
        teile.append(f"{n['auffaellig_seit_start']} auffällige Verbindungen seit Start")
    return ", ".join(teile)


def netz(a: dict) -> str:
    """Netzwächter: Auskunft zu Adressen (`ip`, oder ein eingefügter Text wie eine Firewall-Meldung in
    `text`) – sonst die Übersicht der letzten Verbindungen nach draußen."""
    from . import MODELL_ORDNER, netzwaechter as NW
    from .netzauskunft import Auskunft, bericht_text
    sp = zugang.speicher()
    e = einstellungen()
    if zugang.motor is not None:
        auskunft = zugang.motor.auskunft
    else:
        auskunft = Auskunft(sp, register=bool(e.get("netz_besitzer", True)))
    adressen = _adressen(" ".join(str(a.get(k) or "") for k in ("ip", "adresse", "text", "meldung")))
    if adressen:
        teile = [bericht_text(auskunft.bericht(ip)) for ip in adressen[:5]]
        return _rahmen("\n\n".join(teile), quelle=f"Netz-Auskunft {', '.join(adressen[:5])}")
    if a.get("ip") or a.get("text"):
        return "Keine gültige IP-Adresse gefunden – ip=\"1.2.3.4\" oder den Text der Meldung in text= angeben."
    try:
        stunden = max(1.0, min(168.0, float(a.get("stunden") or 24)))
    except (TypeError, ValueError):
        stunden = 24.0
    s_ml, s_dl = float(e.get("netz_schwelle", 0.99)), float(e.get("netz_dl_schwelle", 0.995))
    liste = NW.uebersicht(sp.netz_verbindungen(time.time() - stunden * 3600),
                          besitzer=lambda ip: auskunft.besitzer(ip, abfragen=False), n=int(a.get("anzahl") or 25))
    zeilen = [f"Verbindungen nach draußen der letzten {stunden:g} h (je Programm und Ziel, neueste zuerst):"]
    for g in liste:
        zeilen.append(f"{NW.ampel(g['ml'], g['dl'], s_ml, s_dl)} {g['prozess']} → {g['name'] or g['ziel']}"
                      + (f" ({g['ziel']})" if g["name"] else "") + f":{g['port']}"
                      + (f" · {g['besitzer']}" if g["besitzer"] else "")
                      + f" · {g['anzahl']}× · zuletzt {time.strftime('%d.%m. %H:%M', time.localtime(g['zuletzt']))}"
                      + f" · ML {NW.score_text(g['ml'])} DL {NW.score_text(g['dl'])}")
    if not liste:
        zeilen.append("(keine – läuft die Wache?)")
    schatten = NW.SchattenLog(MODELL_ORDNER / NW.SCHATTEN).lesen(10)
    if schatten:
        zeilen.append("")
        zeilen.append("Letzte Funde im Schattenmodus (kein Alarm):")
        zeilen += [f"  {z}" for z in schatten]
    return _rahmen("\n".join(zeilen), quelle="Netzwächter")



def _programm_zeilen(key: str, mit_laufenden: bool = True) -> list[str]:
    from . import absolvierung as AB
    a, b = AB.auftrag(key), AB.bild(key)
    anzeige = a.get("anzeige") or key
    if a.get("aus"):
        phase = "⏹ beendet"
    elif AB.lernt(a):
        phase = f"📖 lernt noch bis {time.strftime('%d.%m. %H:%M', time.localtime(float(a['lernen_bis'])))}"
    else:
        phase = "🛡 wacht (Neues meldet R015)"
    kinder = b.get("kinder") or {}
    zeilen = [f"{anzeige} ({key}) · {phase} · {len(kinder)} Hintergrund-Prozess(e) im Normalbild"
              + (f" · {b['laeufe']}× gestartet gesehen" if b.get("laeufe") else "")]
    for k in sorted(kinder, key=lambda x: -int(kinder[x].get("anzahl", 0)))[:25]:
        v = kinder[k]
        zeilen.append(f"  ✓ {v.get('name') or k} · {v.get('anzahl', 0)}× · von {v.get('eltern') or '?'}"
                      + (f" · {v['exe']}" if v.get("exe") else "") + f" · {v.get('quelle', '')}")
    if len(kinder) > 25:
        zeilen.append(f"  … und {len(kinder) - 25} weitere")
    if a.get("erlaubt"):
        zeilen.append("  von Hand erlaubt: " + ", ".join(a["erlaubt"]))
    if mit_laufenden:
        jetzt = AB.laufende(key)
        if jetzt:
            zeilen.append(f"  Läuft gerade ({len(jetzt)}):")
            for p in jetzt[:20]:
                k = AB.kind_schluessel(p["name"], p["exe"])
                zeilen.append(f"    {'✓' if k in kinder else '★ neu'} {p['name']}" + (f" · {p['exe']}" if p["exe"] else ""))
        else:
            zeilen.append("  Läuft gerade: nichts davon")
    return zeilen


def programm(a: dict, wer: str = "Persönlichkeit") -> str:
    """Programm-Absolvierung: ein Programm beobachten, sein Normalbild an Hintergrund-Prozessen lernen, danach
    Neues melden (R015). aktion: start (programm, tage) · stop · erlauben (prozess) · status · liste."""
    from . import absolvierung as AB
    aktion = str(a.get("aktion") or ("status" if a.get("programm") else "liste")).strip().lower()
    programm_ = str(a.get("programm") or a.get("name") or "").strip()
    try:
        if aktion == "liste" or (aktion == "status" and not programm_):
            alle = AB.auftraege()
            if not alle:
                return ("Noch kein Programm in Beobachtung. Mit aktion=\"start\" und programm=\"steam\" lernt die Wache, "
                        "welche Hintergrund-Prozesse es startet.")
            zeilen = ["Programme in Beobachtung (Programm-Absolvierung):"]
            for x in alle:
                zeilen += _programm_zeilen(x["programm"], mit_laufenden=False)
            return _rahmen("\n".join(zeilen), quelle="Programm-Absolvierung")
        if not programm_:
            return "Welches Programm? programm=\"steam\" (Name der exe, mit oder ohne .exe)."
        key = AB.schluessel(programm_)
        if aktion in ("start", "starten", "beobachten", "lernen"):
            try:
                tage = float(a.get("tage") if a.get("tage") not in (None, "") else AB.LERNEN_TAGE)
            except (TypeError, ValueError):
                return "tage muss eine Zahl sein (Lernphase in Tagen)."
            neu = AB.starten(programm_, tage, von=wer, anzeige=str(a.get("anzeige") or ""))
            bis = time.strftime("%d.%m. %H:%M", time.localtime(neu["lernen_bis"]))
            text = (f"🔍 Ich passe ab jetzt auf {neu['anzeige']} ({key}) auf: bis {bis} lerne ich, welche Hintergrund-"
                    "Prozesse es startet, danach melde ich Neues als Alarm R015. Läuft es gerade, zählt die Wache "
                    "sofort mit.")
            if not zugang.laeuft():
                text += " Die Wache läuft gerade nicht – sie beginnt, sobald sie startet."
            return text
        if aktion in ("stop", "stoppen", "beenden", "aus"):
            alt = AB.stoppen(programm_)
            return (f"⏹ {alt.get('anzeige') or key} wird nicht mehr beobachtet – das gelernte Normalbild bleibt "
                    "gespeichert, ein neuer Start macht dort weiter." if alt else f"{key} war nicht in Beobachtung.")
        if aktion in ("erlauben", "erlaubt", "aufnehmen"):
            prozess = str(a.get("prozess") or "").strip()
            neu = AB.erlauben(programm_, prozess)
            return (f"✓ {prozess} gehört ab jetzt zum Normalbild von {neu.get('anzeige') or key}." if neu
                    else f"{key} ist nicht in Beobachtung – erst aktion=\"start\".")
        if aktion == "status":
            if not AB.auftrag(key):
                return f"{key} ist nicht in Beobachtung – mit aktion=\"start\" fängt die Wache an zu lernen."
            return _rahmen("\n".join(_programm_zeilen(key)), quelle=f"Programm-Absolvierung {key}")
        return "aktion: start · stop · erlauben · status · liste"
    except ValueError as exc:
        return f"Fehler: {exc}"
