"""beobachter.py – merkt sich, was die KI gesehen hat, und meldet, was sich seitdem geändert hat.

Erfasst werden gelesene Dateien (Zeitstempel, Größe, Inhalt bis MAX_INHALT für den Unterschied),
aufgelistete Ordner (die Namen, die die KI zu sehen bekam) und die Umgebung (Modell, Persönlichkeit,
Modus, Arbeitsordner). Zu Beginn jeder Runde prüft `runde_hinweis()` nur die Zeitstempel und meldet
Änderungen einmal; liest die KI eine geänderte Datei erneut, steht der Unterschied über dem Ergebnis.
Eigene Schreib-Werkzeuge melden sich mit `selbst_geaendert()`, damit sie keinen Hinweis auslösen.

Ordner werden beim Rundenbeginn nie neu gelesen – sonst kämen Namen gesperrter Einträge in den
Hinweis; ein geänderter Ordner wird nur genannt, die Namen zeigt erst das nächste `ordner_auflisten`.
"""

from __future__ import annotations

import difflib
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

MAX_DATEIEN = 40                  # so viele Dateien und Ordner merkt er sich je Art (die zuletzt gesehenen)
MAX_INHALT = 120_000              # bis zu dieser Größe (Bytes) wird der Inhalt für den Unterschied gemerkt
MAX_DIFF_ZEILEN = 14              # so viele geänderte Zeilen zeigt ein Hinweis höchstens
MAX_MELDUNGEN = 6                 # so viele Dateien/Ordner nennt ein Rundenhinweis höchstens
PAUSE_S = 30 * 60                 # ab dieser Pause zwischen zwei Runden: Hinweis mit der neuen Uhrzeit
UMGEBUNG_NAMEN = {"modell": "Modell", "persoenlichkeit": "Persönlichkeit", "modus": "Modus",
                  "arbeitsordner": "Arbeitsordner"}

_lock = threading.Lock()
_dateien: OrderedDict[str, dict] = OrderedDict()
_ordner: OrderedDict[str, dict] = OrderedDict()
_umgebung: dict = {}
_letzte_runde = 0.0
letzter_kurz: list[str] = []          # Kurzfassung des letzten Rundenhinweises (für die Anzeige)


def zuruecksetzen() -> None:
    global _letzte_runde
    with _lock:
        _dateien.clear()
        _ordner.clear()
        _umgebung.clear()
        _letzte_runde = 0.0


def _schluessel(pfad) -> str:
    return os.path.normcase(os.path.abspath(str(pfad)))


def _stand(pfad: str) -> dict | None:
    """Zeitstempel und Größe; mit Inhalt, wenn die Datei klein genug und Text ist."""
    try:
        st = os.stat(pfad)
    except OSError:
        return None
    eintrag = {"mtime": st.st_mtime_ns, "groesse": st.st_size, "text": None, "zeit": time.time()}
    if st.st_size <= MAX_INHALT:
        try:
            roh = Path(pfad).read_bytes()
            if b"\x00" not in roh[:2048]:
                eintrag["text"] = roh.decode("utf-8", errors="replace").replace("\r\n", "\n")
        except OSError:
            pass
    return eintrag


def _merken(sammlung: OrderedDict, schluessel: str, wert: dict) -> None:
    sammlung[schluessel] = wert
    sammlung.move_to_end(schluessel)
    while len(sammlung) > MAX_DATEIEN:
        sammlung.popitem(last=False)


_WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def _dauer(sekunden: float) -> str:
    if sekunden < 5400:
        return f"{max(1, round(sekunden / 60))} Min."
    if sekunden < 2 * 86400:
        return f"{sekunden / 3600:.1f} Std.".replace(".", ",", 1)
    return f"{round(sekunden / 86400)} Tage"


def _vor(sekunden: float) -> str:
    return "gerade eben" if sekunden < 90 else f"vor {_dauer(sekunden)}"


def _uhrzeit(zeit: float) -> str:
    t = time.localtime(zeit)
    return f"{_WOCHENTAGE[t.tm_wday]}, {time.strftime('%d.%m.%Y, %H:%M', t)}"


def unterschied(alt: str, neu: str, name: str = "") -> str:
    """Kurzer Unterschied (+ neue, − entfernte Zeilen mit Zeilennummer), als Fremdinhalt gerahmt."""
    a, b = alt.splitlines(), neu.splitlines()
    zeilen, plus, minus = [], 0, 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for k in range(i1, i2):
            minus += 1
            zeilen.append(f"− {k + 1}: {a[k]}")
        for k in range(j1, j2):
            plus += 1
            zeilen.append(f"+ {k + 1}: {b[k]}")
    if not zeilen:
        return ""
    gezeigt = [z if len(z) <= 200 else z[:199] + "…" for z in zeilen[:MAX_DIFF_ZEILEN]]
    if len(zeilen) > MAX_DIFF_ZEILEN:
        gezeigt.append(f"… und {len(zeilen) - MAX_DIFF_ZEILEN} weitere geänderte Zeilen")
    try:
        import fremddaten
        rumpf = fremddaten.rahmen("\n".join(gezeigt), "datei", name)
    except Exception:
        rumpf = "\n".join(gezeigt)
    return f"{plus} Zeile(n) neu, {minus} entfernt:\n{rumpf}"


def _aenderung(alt: dict, neu: dict | None, name: str) -> str:
    if neu is None:
        return "nicht mehr da (gelöscht oder verschoben)"
    if alt["text"] is not None and neu["text"] is not None:
        diff = unterschied(alt["text"], neu["text"], name)
        if diff:
            return diff
    return f"Größe {alt['groesse']:,} → {neu['groesse']:,} Bytes".replace(",", ".")


def gelesen(pfad) -> str:
    """Die KI hat die Datei gelesen. Gibt einen Hinweis zurück, wenn sie seit dem letzten Lesen von
    jemand anderem geändert wurde (sonst leer); der neue Stand wird gemerkt."""
    s = _schluessel(pfad)
    neu = _stand(s)
    if neu is None:
        return ""
    with _lock:
        alt = _dateien.get(s)
        _merken(_dateien, s, neu)
    if alt is None or (alt["mtime"], alt["groesse"]) == (neu["mtime"], neu["groesse"]):
        return ""
    if alt["text"] is not None and alt["text"] == neu["text"]:
        return ""
    return (f"🔄 Diese Datei hat sich seit deinem letzten Lesen ({_vor(time.time() - alt['zeit'])}) "
            f"geändert – nicht durch dich: {_aenderung(alt, neu, Path(pfad).name)}\n\n")


def _ordner_nachziehen(ordner: str) -> None:
    """Eigene Änderung in einem beobachteten Ordner: Zeitstempel übernehmen. Nur mit `_lock` aufrufen."""
    if ordner in _ordner:
        try:
            _ordner[ordner]["mtime"] = os.stat(ordner).st_mtime_ns
        except OSError:
            pass


def selbst_geaendert(pfad) -> None:
    """Ein eigenes Schreib-Werkzeug hat die Datei geändert: neuer Stand (ab jetzt beobachtet), kein Hinweis."""
    s = _schluessel(pfad)
    neu = _stand(s)
    with _lock:
        if neu is None:
            _dateien.pop(s, None)
        else:
            _merken(_dateien, s, neu)
        _ordner_nachziehen(_schluessel(Path(s).parent))


def entfernt(pfad) -> None:
    """Ein eigenes Werkzeug hat die Datei gelöscht oder verschoben."""
    s = _schluessel(pfad)
    with _lock:
        _dateien.pop(s, None)
        _ordner.pop(s, None)
        _ordner_nachziehen(_schluessel(Path(s).parent))


def namen_aus_liste(text: str) -> set[str]:
    """Die Namen aus der Ausgabe von ordner_auflisten (Zeilen „[Ordner] name“ bzw. eingerückt)."""
    namen = set()
    for z in text.splitlines():
        if z.startswith("[Ordner] "):
            namen.add(z[9:].strip() + "/")
        elif z.startswith(" " * 10) and z.strip():
            namen.add(z.strip())
    return namen


def gelistet(pfad, namen: set[str]) -> str:
    """Die KI hat den Ordner aufgelistet und `namen` gesehen. Hinweis auf neue/weggefallene Einträge."""
    s = _schluessel(pfad)
    try:
        mtime = os.stat(s).st_mtime_ns
    except OSError:
        return ""
    with _lock:
        alt = _ordner.get(s)
        _merken(_ordner, s, {"namen": frozenset(namen), "mtime": mtime, "zeit": time.time()})
    if alt is None or alt["namen"] == frozenset(namen):
        return ""
    neu, weg = sorted(namen - alt["namen"]), sorted(alt["namen"] - namen)
    teile = []
    if neu:
        teile.append("neu: " + ", ".join(neu[:12]) + (" …" if len(neu) > 12 else ""))
    if weg:
        teile.append("weg: " + ", ".join(weg[:12]) + (" …" if len(weg) > 12 else ""))
    return (f"🔄 Seit du diesen Ordner zuletzt angesehen hast ({_vor(time.time() - alt['zeit'])}): "
            + " · ".join(teile) + "\n\n")


def runde_hinweis(umgebung: dict | None = None, jetzt: float | None = None) -> str:
    """Zu Beginn einer Runde: was hat sich seit dem letzten Blick geändert? Leer, wenn nichts.
    `umgebung` = {modell, persoenlichkeit, modus, arbeitsordner, chat}; ein neuer Chat beginnt frisch."""
    global _letzte_runde, letzter_kurz
    jetzt = time.time() if jetzt is None else jetzt
    umgebung = dict(umgebung or {})
    kurz: list[str] = []
    with _lock:
        if _umgebung.get("chat") is not None and umgebung.get("chat") != _umgebung.get("chat"):
            _dateien.clear()
            _ordner.clear()
            _umgebung.clear()
            _letzte_runde = 0.0
        punkte = []
        for k, name in UMGEBUNG_NAMEN.items():
            alt, neu = _umgebung.get(k), umgebung.get(k)
            if alt is not None and neu is not None and alt != neu:
                punkte.append(f"- {name} gewechselt: {alt} → {neu}")
                kurz.append(f"{name} gewechselt")
        _umgebung.update({k: v for k, v in umgebung.items() if v is not None})
        if _letzte_runde and jetzt - _letzte_runde >= PAUSE_S:
            punkte.append(f"- Seit der letzten Nachricht sind {_dauer(jetzt - _letzte_runde)} vergangen – "
                          f"jetzt ist {_uhrzeit(jetzt)}")
            kurz.append(f"{_dauer(jetzt - _letzte_runde)} Pause")
        _letzte_runde = jetzt
        dateien = list(_dateien.items())
        ordner = list(_ordner.items())
    geaendert = []
    for s, alt in reversed(dateien):                   # zuletzt gesehene zuerst
        try:
            st = os.stat(s)
            gleich = (st.st_mtime_ns, st.st_size) == (alt["mtime"], alt["groesse"])
        except OSError:
            gleich = False
        if gleich:
            continue
        neu = _stand(s)
        if neu is not None and alt["text"] is not None and alt["text"] == neu["text"]:
            with _lock:
                _merken(_dateien, s, {**neu, "zeit": alt["zeit"]})
            continue
        geaendert.append(f"- Datei {s} ({'gelesen ' + _vor(jetzt - alt['zeit'])}): "
                         f"{_aenderung(alt, neu, Path(s).name)}")
        kurz.append(f"{Path(s).name} {'weg' if neu is None else 'geändert'}")
        with _lock:
            if neu is None:
                _dateien.pop(s, None)
            else:
                _merken(_dateien, s, {**neu, "zeit": alt["zeit"]})
    for s, alt in reversed(ordner):
        try:
            mtime = os.stat(s).st_mtime_ns
        except OSError:
            geaendert.append(f"- Ordner {s}: nicht mehr da")
            kurz.append(f"Ordner {Path(s).name} weg")
            with _lock:
                _ordner.pop(s, None)
            continue
        if mtime != alt["mtime"]:
            geaendert.append(f"- Ordner {s}: Inhalt geändert (neu ansehen mit ordner_auflisten)")
            kurz.append(f"Ordner {Path(s).name} geändert")
            with _lock:
                if s in _ordner:
                    _ordner[s]["mtime"] = mtime
    if len(geaendert) > MAX_MELDUNGEN:
        geaendert = geaendert[:MAX_MELDUNGEN] + [f"- … und {len(geaendert) - MAX_MELDUNGEN} weitere"]
    punkte += geaendert
    letzter_kurz = kurz
    if not punkte:
        return ""
    return ("\n\n[System] 🔄 Seit deinem letzten Blick hat sich etwas geändert (nicht durch dich). Bedenke es "
            "beim Denken – was du vorher gesehen hast, stimmt so vielleicht nicht mehr:\n" + "\n".join(punkte))


def nach_werkzeug(tool: str, act: dict, text: str, ok) -> str:
    """Nach jedem Werkzeug aufrufen. Gibt einen Hinweis zurück, der VOR das Ergebnis gehört (oder leer)."""
    if ok is not True:
        return ""
    try:
        if tool == "datei_lesen" and act.get("pfad"):
            return gelesen(act["pfad"])
        if tool == "ordner_auflisten":
            return gelistet(act.get("pfad") or ".", namen_aus_liste(text))
        if tool in ("datei_schreiben", "datei_bearbeiten", "pdf_erstellen") and act.get("pfad"):
            selbst_geaendert(act["pfad"])
        elif tool == "loeschen" and act.get("pfad"):
            entfernt(act["pfad"])
        elif tool == "verschieben" and act.get("von"):
            entfernt(act["von"])
            if act.get("nach"):
                selbst_geaendert(act["nach"])
        elif tool == "datei_kopieren" and act.get("nach"):
            selbst_geaendert(act["nach"])
        elif tool == "ordner_erstellen" and act.get("pfad"):
            with _lock:
                _ordner_nachziehen(_schluessel(Path(str(act["pfad"])).parent))
    except Exception:
        return ""
    return ""
