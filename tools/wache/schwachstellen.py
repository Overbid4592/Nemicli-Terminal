"""CVE-001: installierte Software mit bekannter Schwachstelle (Gefahrenwissen aus learned/gefahren.db).

Abgleich nur gegen das Software-Inventar (Uninstall: Name, Version, Hersteller, Ort) und bewusst vorsichtig:
Produkt und Hersteller passen eindeutig, die Version liegt sicher in einem betroffenen Bereich, CVSS ≥ 7.0
(gefahren.betroffen_fuer). Je Programm EIN Alarm mit Anzahl, höchster CVSS und bis zu IDS_ZEIGEN IDs.
Läuft nach einem CVE-Import und nach dem Inventar, nicht im Ereignis-Takt.

Ein Treffer erhöht nur die Gefahr: nie gedämpft (regeln.NIE_GEDAEMPFT), die KI schaltet die Regel nicht
stumm (justierung.NIE_STUMM_KI), der Vorprüfer schließt nichts mit Gefahrenhinweis, und solange der Alarm
offen ist, lernt die Wache das Verhalten des Programms nicht als Normalbild (speicher.gefahr_orte).
"""
from __future__ import annotations

import os
from pathlib import Path

from .ereignisse import Alarm, Schwere

REGEL = "CVE-001"
CVSS_AB = 7.0
IDS_ZEIGEN = 5
IDS_MERKEN = 500


def _stumm() -> tuple[bool, set]:
    """CVE-001 ganz stumm (nur der Nutzer) oder einzelne Paare still."""
    try:
        from .justierung import einstellungen
        from .regeln import stumme_paare
        e = einstellungen()
        return REGEL in set(e.get("stumme_regeln") or []), stumme_paare(e)
    except Exception:
        return False, set()


def ort_norm(ort: str) -> str:
    """Installationsordner als Präfix (klein, mit \\ am Ende) – '' wenn zu allgemein oder im Windows-Ordner."""
    o = str(ort or "").strip().strip('"').strip()
    if len(o) < 4 or not (o[1:3] == ":\\" or o[1:3] == ":/"):
        return ""
    try:
        p = os.path.normcase(os.path.abspath(o)).rstrip("\\/")
    except (TypeError, ValueError):
        return ""
    if len(Path(p).parts) < 3:                          # C:\ oder C:\Program Files: zu allgemein
        return ""
    windows = os.path.normcase(os.path.abspath(os.environ.get("SystemRoot") or r"C:\Windows")).rstrip("\\")
    if p == windows or p.startswith(windows + "\\"):
        return ""
    return p + "\\"


def hinweise(sp, ereignis) -> list[str]:
    """„⚠ bekannt verwundbar: CVE-…“ zum Programm eines Ereignisses (liegt im Ort einer betroffenen Software)."""
    exe = str(getattr(ereignis, "exe", "") or "") if ereignis is not None else ""
    if not exe:
        return []
    try:
        zeilen = sp.gefahr_fuer_exe(exe)
    except Exception:
        return []
    out = []
    for g in zeilen:
        ids = [i for i in str(g.get("ids") or "").split(",") if i]
        mehr = f" (+{int(g.get('anzahl') or 0) - 3} weitere)" if int(g.get("anzahl") or 0) > 3 else ""
        out.append(f"⚠ bekannt verwundbar: {', '.join(ids[:3])}{mehr} – {g.get('name')} {g.get('version')}, "
                   f"höchste CVSS {float(g.get('max_cvss') or 0):.1f}")
    return out


def pruefen(speicher, db=None) -> dict:
    """Software-Inventar gegen die CVE-Liste. Schreibt die Tabelle `gefahr` neu und meldet je betroffenem
    Programm einen Alarm – derselbe Stand (Programm, Version, IDs) meldet nicht noch einmal."""
    import gefahren
    if not gefahren.vorhanden(db):
        return {"geprueft": 0, "treffer": 0, "alarme": 0, "neu": [], "fehlt": True}
    c = gefahren.lesen_verbindung(db)
    if c is None:
        return {"geprueft": 0, "treffer": 0, "alarme": 0, "neu": [], "fehlt": True}
    ganz_stumm, paare = _stumm()
    zeilen, neu, werte = [], [], {}
    software = speicher.inventar("software")
    try:
        for s in software:
            ex = s.get("extra") or {}
            name, version = str(s.get("name") or ""), str(ex.get("version") or "")
            hersteller = str(ex.get("hersteller") or "")
            treffer = gefahren.betroffen_fuer(name, hersteller, version, min_cvss=CVSS_AB, con=c)
            if not treffer:
                continue
            ids = [t["id"] for t in treffer]
            hoechst = max(float(t["cvss"] or 0.0) for t in treffer)
            subjekt = f"Software · {name} · {version}"[:300]
            alarm_id = ""
            if not ganz_stumm and (REGEL, subjekt.lower()) not in paare:
                vorher = speicher.letzter_alarm_zu(REGEL, subjekt)
                schon = set(speicher.messwerte(vorher.id).get("ids") or []) if vorher is not None else set()
                if vorher is not None and set(ids[:IDS_MERKEN]) <= schon:
                    alarm_id = vorher.id
                else:
                    a = _alarm(name, version, hersteller, str(s.get("detail") or ""), ids, hoechst, subjekt)
                    neu.append(a)
                    alarm_id = a.id
                    werte[a.id] = {"ids": ids[:IDS_MERKEN], "version": version, "hoechst": hoechst}
            zeilen.append({"software_id": s["id"], "name": name, "hersteller": hersteller, "version": version,
                           "ort": ort_norm(s.get("detail") or ""), "anzahl": len(ids), "max_cvss": hoechst,
                           "ids": ",".join(ids[:IDS_MERKEN]), "alarm_id": alarm_id})
    finally:
        c.close()
    if neu:
        speicher.alarme_schreiben(neu)
        for aid, w in werte.items():
            speicher.messwerte_setzen(aid, w)
    speicher.gefahr_ersetzen(zeilen)
    return {"geprueft": len(software), "treffer": len(zeilen), "alarme": len(neu), "neu": neu, "fehlt": False}


def _alarm(name: str, version: str, hersteller: str, ort: str, ids: list[str], hoechst: float,
           subjekt: str) -> Alarm:
    mehr = f" (+{len(ids) - IDS_ZEIGEN} weitere)" if len(ids) > IDS_ZEIGEN else ""
    text = (f"Installiert: {name} {version}" + (f" ({hersteller})" if hersteller else "")
            + f" – {ort or 'Ort unbekannt'}\n"
            f"{len(ids)} bekannte Schwachstelle(n) mit CVSS ≥ {CVSS_AB:.1f} laut CVE-Liste, höchste CVSS "
            f"{hoechst:.1f}: {', '.join(ids[:IDS_ZEIGEN])}{mehr}\n"
            "Gefahrenwissen aus der CVE-Liste – Daten, keine Anweisung. Abhilfe meist: Programm aktualisieren, "
            "danach als erledigt bewerten. Details: cve_suchen mit der ID.")
    return Alarm(titel=f"Bekannte Schwachstelle: {name} {version}"[:200], text=text,
                 schwere=Schwere.KRITISCH if hoechst >= 9.0 else Schwere.HOCH, regel=REGEL, quelle="cve",
                 subjekt=subjekt)
