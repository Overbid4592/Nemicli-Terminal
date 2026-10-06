"""
coding.py - Coding-Assistent: führt die KI bei Programmieraufgaben auf einem festen Weg.

Kein eigenes Modell. Das Programm hält Auftrag und Todo-Liste im Projekt
(`<Projekt>/.nemicli/`), zeigt sie an und legt sie der KI bei jeder Runde im
System-Prompt vor. So driftet das Ziel nicht weg, auch wenn der Verlauf lang wird.

Status eines Punktes:
    offen       🔴  noch nicht fertig
    frage       🟠  wartet auf eine Antwort des Nutzers
    fertig      🟢  geprüft – setzt NUR das Programm
    gestrichen  ⚪  vom Nutzer freigegeben weggelassen

Grün entsteht auf genau zwei Wegen: der Prüfbefehl des Punktes läuft mit
Exitcode 0 (und enthält ggf. den erwarteten Text), oder der Nutzer bestätigt
das Prüfkriterium selbst. Die KI kann einen Punkt nicht als fertig setzen.

Eingeschaltet wird per Werkzeug `coding_start` (die KI bei jeder Coding-Aufgabe)
oder `/code`. Aus nur durch den Nutzer: `/codeend` oder eine klare Ansage.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import config

ABLAGE = ".nemicli"
TODO_JSON = "todo.json"
TODO_MD = "Todo.md"
AUFTRAG_MD = "Auftrag.md"

OFFEN, FRAGE, FERTIG, GESTRICHEN = "offen", "frage", "fertig", "gestrichen"
ZEICHEN = {OFFEN: "🔴", FRAGE: "🟠", FERTIG: "🟢", GESTRICHEN: "⚪"}

# Aktionen von `todo`, die der Nutzer immer selbst freigibt (in jedem Modus):
# die Liste festlegen, ein abgesegnetes Kriterium ändern, einen Punkt streichen.
_NUTZER_ENTSCHEIDET = {"anlegen", "aendern", "streichen"}

_ENDE_ANSAGE = re.compile(
    r"\b(coding|coding[- ]?assistent(?:in)?|coding[- ]?modus)\s+(aus|beenden|stopp|stop|ende)\b"
    r"|\b(beende|stopp|stoppe)\s+(den\s+)?coding",
    re.IGNORECASE)


# ---------------------------------------------------------------------------
# Zustand (in der Config, damit er einen Neustart überlebt)
# ---------------------------------------------------------------------------

def _zustand() -> dict:
    z = config.load().get("coding")
    return z if isinstance(z, dict) else {}


def aktiv() -> bool:
    return bool(_zustand().get("aktiv"))


def projekt() -> Path | None:
    roh = str(_zustand().get("projekt") or "").strip()
    return Path(roh) if roh and aktiv() else None


def arbeitsordner() -> Path | None:
    """Projektordner für `befehl`, sobald es ihn gibt."""
    p = projekt()
    return p if p is not None and p.is_dir() else None


def ablage(p: Path | None = None) -> Path | None:
    p = p or projekt()
    return (p / ABLAGE) if p else None


def _sammelordner() -> list[Path]:
    import workspace
    return workspace._keine_projektordner()


def pruefe_projekt(ziel: str) -> tuple[Path | None, str]:
    """Taugt der Ordner als Projekt? Er muss noch nicht existieren."""
    if not str(ziel or "").strip():
        return None, "Kein Projektordner angegeben (Feld `projekt`)."
    try:
        p = Path(os.path.expandvars(str(ziel))).expanduser().resolve()
    except Exception as e:
        return None, f"Mit diesem Pfad kann ich nichts anfangen: {e}"
    if p.parent == p:
        return None, "Ein ganzes Laufwerk ist kein Projekt. Nimm einen Ordner darin."
    if p.exists() and not p.is_dir():
        return None, f"Das ist eine Datei, kein Ordner: {p}"
    if p in _sammelordner():
        return None, (f"'{p.name}' ist ein Sammelordner, kein Projekt. "
                      f"Nimm einen Ordner darin, z.B. {p / 'MeinProjekt'}.")
    import workspace
    if workspace.aktiv() and not workspace.drin(p):
        return None, f"Der Projektordner muss im Workspace liegen ({workspace.pfad()})."
    return p, ""


def starten(ziel: str, aufgabe: str = "") -> dict:
    """Schaltet den Assistenten an. {projekt, neu, liste} – oder ValueError."""
    p, fehler = pruefe_projekt(ziel)
    if p is None:
        raise ValueError(fehler)
    vorher = projekt()
    config.update(coding={"aktiv": True, "projekt": str(p),
                          "aufgabe": aufgabe.strip() or _zustand().get("aufgabe", ""),
                          "seit": datetime.now().isoformat(timespec="seconds")})
    return {"projekt": p, "neu": vorher != p, "liste": laden(p)}


def beenden() -> Path | None:
    """Schaltet ab. Die Ablage im Projekt bleibt liegen."""
    alt = projekt()
    config.update(coding={})
    return alt


def ist_ende_ansage(text: str) -> bool:
    return bool(_ENDE_ANSAGE.search(text or ""))


# ---------------------------------------------------------------------------
# Todo-Liste
# ---------------------------------------------------------------------------

def laden(p: Path | None = None) -> dict | None:
    a = ablage(p)
    if a is None:
        return None
    try:
        daten = json.loads((a / TODO_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return daten if isinstance(daten, dict) and isinstance(daten.get("punkte"), list) else None


def _speichern(daten: dict) -> None:
    a = ablage()
    if a is None:
        raise ValueError("Der Coding-Assistent ist nicht an.")
    a.mkdir(parents=True, exist_ok=True)
    (a / TODO_JSON).write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    (a / TODO_MD).write_text(_todo_markdown(daten), encoding="utf-8")
    (a / AUFTRAG_MD).write_text(_auftrag_markdown(daten), encoding="utf-8")


def _liste(wert) -> list[str]:
    if isinstance(wert, str):
        wert = [z for z in wert.splitlines()]
    if not isinstance(wert, list):
        return []
    return [str(z).strip().lstrip("-• ").strip() for z in wert if str(z).strip()]


def _punkt(roh, nr: int) -> dict:
    if isinstance(roh, str):
        roh = {"text": roh}
    if not isinstance(roh, dict):
        raise ValueError(f"Punkt {nr}: erwartet ein Objekt mit text und pruefung.")
    text = str(roh.get("text") or "").strip()
    pruefung = str(roh.get("pruefung") or "").strip()
    if not text:
        raise ValueError(f"Punkt {nr}: `text` fehlt.")
    if not pruefung:
        raise ValueError(f"Punkt {nr} ('{text}'): `pruefung` fehlt – woran erkennt man, "
                         "dass er erfüllt ist?")
    return {"nr": nr, "text": text, "pruefung": pruefung,
            "befehl": str(roh.get("befehl") or "").strip(),
            "erwartet": str(roh.get("erwartet") or "").strip(),
            "status": OFFEN, "frage": "", "notiz": "", "geprueft": ""}


def neue_liste(a: dict) -> dict:
    """Aus den Feldern von `todo anlegen` eine Liste bauen (ohne zu speichern)."""
    ziel = str(a.get("ziel") or "").strip()
    if not ziel:
        raise ValueError("`ziel` fehlt – was soll am Ende da sein?")
    roh = a.get("punkte")
    if not isinstance(roh, list) or not roh:
        raise ValueError("`punkte` fehlt – eine Liste aus {text, pruefung, befehl}.")
    return {"ziel": ziel, "muss": _liste(a.get("muss")), "nicht": _liste(a.get("nicht")),
            "recherche": str(a.get("recherche") or "").strip(),
            "punkte": [_punkt(r, i) for i, r in enumerate(roh, 1)],
            "aktuell": None,
            "angelegt": datetime.now().isoformat(timespec="seconds")}


def _finde(daten: dict, nr) -> dict:
    try:
        n = int(nr)
    except (TypeError, ValueError):
        raise ValueError("`nr` fehlt oder ist keine Zahl.")
    for p in daten["punkte"]:
        if p["nr"] == n:
            return p
    raise ValueError(f"Punkt {n} gibt es nicht.")


def _brauche_liste() -> dict:
    if not aktiv():
        raise ValueError("Der Coding-Assistent ist nicht an. Erst coding_start.")
    daten = laden()
    if daten is None:
        raise ValueError("Es gibt noch keine Todo-Liste. Erst todo mit aktion \"anlegen\".")
    return daten


def zaehlen(daten: dict) -> tuple[int, int]:
    """(geprüft, gesamt) – gestrichene zählen nicht mit."""
    punkte = [p for p in daten["punkte"] if p["status"] != GESTRICHEN]
    return sum(p["status"] == FERTIG for p in punkte), len(punkte)


def marke() -> tuple | None:
    """Fortschritt der Liste (Punkt in Arbeit, Anzahl geprüft) – ändert er sich,
    beginnt die Schrittbremse der Agent-Schleife neu."""
    if not aktiv():
        return None
    daten = laden()
    if daten is None:
        return ("ohne Liste",)
    return (daten.get("aktuell"), zaehlen(daten)[0])


def fertig(daten: dict) -> bool:
    gepr, gesamt = zaehlen(daten)
    return gesamt > 0 and gepr == gesamt


def braucht_bestaetigung(a: dict) -> bool:
    """Für actions.needs_confirm: diese `todo`-Aktionen entscheidet der Nutzer."""
    aktion = str(a.get("aktion") or "").lower()
    if aktion in _NUTZER_ENTSCHEIDET:
        return True
    if aktion == "pruefen":
        return True       # mit Prüfbefehl wie `befehl`, ohne: der Nutzer urteilt
    return False


def pruefbefehl(a: dict) -> str:
    """Der Prüfbefehl des Punktes, den `todo pruefen` ausführen würde – sonst ''."""
    if a.get("tool") != "todo" or str(a.get("aktion") or "").lower() != "pruefen":
        return ""
    try:
        return _finde(_brauche_liste(), a.get("nr"))["befehl"]
    except ValueError:
        return ""


def ausfuehren(a: dict, befehl_ausfuehren) -> str:
    """Die Werkzeug-Seite von `todo`. `befehl_ausfuehren(cmd)` -> (ok, text)."""
    aktion = str(a.get("aktion") or "").lower().strip()
    if aktion == "anlegen":
        if not aktiv():
            raise ValueError("Der Coding-Assistent ist nicht an. Erst coding_start.")
        daten = neue_liste(a)
        _speichern(daten)
        return (f"Todo-Liste angelegt ({len(daten['punkte'])} Punkte) in {ablage()}. "
                "Setz jetzt den ersten Punkt mit todo \"arbeiten\" in Arbeit.")
    if aktion == "zeigen":
        return text_fuer_modell(_brauche_liste())

    daten = _brauche_liste()
    if aktion == "neu":
        nr = max((p["nr"] for p in daten["punkte"]), default=0) + 1
        daten["punkte"].append(_punkt(a, nr))
        _speichern(daten)
        return f"Punkt {nr} hinzugefügt."

    punkt = _finde(daten, a.get("nr"))
    nr = punkt["nr"]
    if aktion == "arbeiten":
        if punkt["status"] in (FERTIG, GESTRICHEN):
            raise ValueError(f"Punkt {nr} ist schon {punkt['status']}.")
        if punkt["status"] == FRAGE:
            raise ValueError(f"Punkt {nr} wartet auf eine Antwort. Erst klären, dann "
                             "todo \"geklaert\".")
        daten["aktuell"] = nr
        _speichern(daten)
        return f"▶ Punkt {nr} in Arbeit: {punkt['text']}\nPrüfung: {punkt['pruefung']}"
    if aktion == "frage":
        frage = str(a.get("frage") or "").strip()
        if not frage:
            raise ValueError("`frage` fehlt.")
        punkt.update(status=FRAGE, frage=frage)
        if daten.get("aktuell") == nr:
            daten["aktuell"] = None
        _speichern(daten)
        return (f"🟠 Punkt {nr} wartet auf den Nutzer. Stell ihm jetzt genau diese Frage und "
                "warte auf seine Antwort – keine weitere Aktion in dieser Runde.")
    if aktion == "geklaert":
        if punkt["status"] != FRAGE:
            raise ValueError(f"Punkt {nr} hat keine offene Frage.")
        notiz = str(a.get("antwort") or a.get("notiz") or "").strip()
        punkt.update(status=OFFEN, frage="",
                     notiz=(punkt["notiz"] + " " if punkt["notiz"] else "") + notiz)
        _speichern(daten)
        return f"Punkt {nr} ist wieder offen. Antwort notiert: {notiz or '–'}"
    if aktion == "aendern":
        for feld in ("text", "pruefung", "befehl", "erwartet"):
            if feld in a:
                punkt[feld] = str(a.get(feld) or "").strip()
        if not punkt["text"] or not punkt["pruefung"]:
            raise ValueError("text und pruefung dürfen nicht leer sein.")
        punkt.update(status=OFFEN, geprueft="")
        _speichern(daten)
        return f"Punkt {nr} geändert und wieder offen."
    if aktion == "streichen":
        grund = str(a.get("grund") or "").strip()
        if not grund:
            raise ValueError("`grund` fehlt – warum fällt der Punkt weg?")
        punkt.update(status=GESTRICHEN, notiz=grund)
        if daten.get("aktuell") == nr:
            daten["aktuell"] = None
        _speichern(daten)
        return f"Punkt {nr} gestrichen: {grund}"
    if aktion == "pruefen":
        if punkt["status"] == GESTRICHEN:
            raise ValueError(f"Punkt {nr} ist gestrichen.")
        if punkt["befehl"]:
            ok, ausgabe = befehl_ausfuehren(punkt["befehl"])
            fehlt = punkt["erwartet"] and punkt["erwartet"] not in ausgabe
            if ok and not fehlt:
                _gruen(daten, punkt, "Prüfbefehl")
                return f"🟢 Punkt {nr} geprüft: Prüfbefehl erfolgreich.\n{ausgabe}"
            grund = (f"erwarteter Text '{punkt['erwartet']}' fehlt in der Ausgabe" if ok
                     else "Prüfbefehl fehlgeschlagen")
            punkt["status"] = OFFEN
            _speichern(daten)
            return (f"🔴 Punkt {nr} NICHT bestanden: {grund}. Finde die Ursache, "
                    f"behebe sie und prüfe erneut.\n{ausgabe}")
        # Ohne Prüfbefehl: diese Aktion läuft nur, wenn der Nutzer Ja gesagt hat.
        _gruen(daten, punkt, "Nutzer")
        return f"🟢 Punkt {nr} geprüft: vom Nutzer bestätigt."
    raise ValueError(f"Unbekannte aktion '{aktion}'. Erlaubt: anlegen, neu, arbeiten, frage, "
                     "geklaert, aendern, streichen, pruefen, zeigen.")


def _gruen(daten: dict, punkt: dict, durch: str) -> None:
    punkt.update(status=FERTIG, frage="",
                 geprueft=f"{durch}, {datetime.now().strftime('%d.%m. %H:%M')}")
    if daten.get("aktuell") == punkt["nr"]:
        daten["aktuell"] = None
    _speichern(daten)


# ---------------------------------------------------------------------------
# Texte: Freigabe, Anzeige, Prompt
# ---------------------------------------------------------------------------

def beschreibung(a: dict) -> str:
    """Für actions.describe – bei `anlegen` die ganze Liste zum Abnicken."""
    aktion = str(a.get("aktion") or "").lower()
    if aktion == "anlegen":
        try:
            return "Auftragskarte und Todo-Liste festlegen:\n\n" + text_fuer_modell(neue_liste(a))
        except ValueError as e:
            return f"Todo-Liste anlegen (unvollständig: {e})"
    nr = a.get("nr", "?")
    if aktion == "pruefen":
        try:
            p = _finde(_brauche_liste(), nr)
        except ValueError:
            return f"Todo-Punkt {nr} prüfen"
        if p["befehl"]:
            return (f"Todo-Punkt {nr} prüfen: {p['text']}\n    Prüfbefehl: {p['befehl']}"
                    + (f"\n    erwartet: {p['erwartet']}" if p["erwartet"] else ""))
        return (f"Todo-Punkt {nr} prüfen: {p['text']}\n    Kriterium: {p['pruefung']}\n"
                "    Es gibt keinen Prüfbefehl – nur du kannst den Punkt grün machen.")
    if aktion == "aendern":
        felder = ", ".join(f"{k}={a[k]!r}" for k in ("text", "pruefung", "befehl", "erwartet") if k in a)
        return f"Todo-Punkt {nr} ändern (wird wieder 🔴): {felder}"
    if aktion == "streichen":
        return f"Todo-Punkt {nr} streichen – Grund: {a.get('grund', '?')}"
    if aktion == "frage":
        return f"Todo-Punkt {nr}: Frage an dich – {a.get('frage', '?')}"
    if aktion == "neu":
        return f"Todo-Punkt hinzufügen: {a.get('text', '?')}"
    return f"Todo: {aktion or '?'} {nr if 'nr' in a else ''}".rstrip()


def frage_und_optionen(a: dict) -> tuple[str, list[tuple[str, str]]]:
    """Rückfrage für `todo` – nie mit „immer erlauben“."""
    aktion = str(a.get("aktion") or "").lower()
    if aktion == "anlegen":
        return ("So abarbeiten?", [("yes", "Ja, so abarbeiten"), ("no", "Nein, ändern")])
    if aktion == "pruefen":
        return ("Ist das Kriterium wirklich erfüllt?",
                [("yes", "Ja, erfüllt → 🟢"), ("no", "Nein, noch nicht")])
    if aktion == "streichen":
        return ("Punkt streichen?", [("yes", "Ja, streichen"), ("no", "Nein, bleibt drin")])
    return ("Änderung übernehmen?", [("yes", "Ja"), ("no", "Nein")])


def _zeile(p: dict, aktuell) -> str:
    pfeil = "▶ " if p["nr"] == aktuell else "  "
    z = f"{pfeil}{ZEICHEN.get(p['status'], '?')} {p['nr']}. {p['text']}"
    if p["status"] == FRAGE and p["frage"]:
        z += f"\n       ? {p['frage']}"
    elif p["status"] == GESTRICHEN:
        z += f"\n       gestrichen: {p['notiz']}"
    else:
        z += f"\n       ✓ {p['pruefung']}" + (f"  [{p['befehl']}]" if p["befehl"] else "")
        if p["status"] == FERTIG and p["geprueft"]:
            z += f"  – geprüft ({p['geprueft']})"
    return z


def text_fuer_modell(daten: dict) -> str:
    gepr, gesamt = zaehlen(daten)
    teile = [f"Ziel: {daten['ziel']}"]
    if daten.get("muss"):
        teile.append("Muss:\n" + "\n".join(f"  - {m}" for m in daten["muss"]))
    if daten.get("nicht"):
        teile.append("Nicht-Ziele:\n" + "\n".join(f"  - {m}" for m in daten["nicht"]))
    if daten.get("recherche"):
        teile.append(f"Recherche: {daten['recherche']}")
    teile.append(f"Todo ({gepr}/{gesamt} geprüft):\n"
                 + "\n".join(_zeile(p, daten.get("aktuell")) for p in daten["punkte"]))
    return "\n".join(teile)


def _auftrag_markdown(daten: dict) -> str:
    z = ["# Auftrag", "", f"**Ziel:** {daten['ziel']}", ""]
    if daten.get("muss"):
        z += ["## Muss", *[f"- {m}" for m in daten["muss"]], ""]
    if daten.get("nicht"):
        z += ["## Nicht-Ziele", *[f"- {m}" for m in daten["nicht"]], ""]
    if daten.get("recherche"):
        z += ["## Recherche", daten["recherche"], ""]
    return "\n".join(z)


def _todo_markdown(daten: dict) -> str:
    gepr, gesamt = zaehlen(daten)
    z = ["# Todo", "", f"{gepr}/{gesamt} geprüft · 🔴 offen · 🟠 Frage offen · "
         "🟢 geprüft · ⚪ gestrichen", "",
         "Geführt von NemiCLI (todo.json). Grün setzt nur die Prüfung.", ""]
    for p in daten["punkte"]:
        z.append(_zeile(p, daten.get("aktuell")).replace("\n       ", "\n    - "))
    return "\n".join(z) + "\n"


_WEG = """\
## Der Weg (Schritt für Schritt)
1. **Verstehen:** Auftrag in eigenen Worten, kurz. Unklar → EINE Frage.
2. **Recherche**, wenn das Thema neu ist: web_suche / web_lesen (z.B. GitHub). README und Code
   LESEN, nichts kopieren – Ideen und Aufbau übernehmen, Code selbst schreiben. Lässt du die
   Recherche weg, schreib in `recherche` einen Satz, warum.
3. **Auftragskarte + Todo-Liste:** todo mit aktion "anlegen". Kleine Punkte. Jeder Punkt braucht
   ein Prüfkriterium; wo es geht einen Prüfbefehl, der bei Erfolg Exitcode 0 liefert (z.B. ein
   Testlauf). Der Nutzer nickt die Liste ab.
4. **Agentloop:** todo "arbeiten" (nr) → umsetzen → todo "pruefen" (nr) → nächster Punkt.
   Immer nur EIN Punkt in Arbeit. Läuft die Prüfung nicht durch: Ursache suchen, beheben, erneut.
5. **Abschluss,** wenn alle Punkte 🟢 oder ⚪ sind – in dieser Form:
   ✅ Fertig und geprüft: …
   ❌ Nicht geschafft: …
   ⚠️ Abweichung vom Auftrag: …
   ❓ Ungetestet: …

## Regeln
- 🟢 setzt nur das Programm (Prüfbefehl) oder der Nutzer. Sag nie „fertig“ oder „läuft“ zu einem
  Punkt, der nicht 🟢 ist.
- Geht etwas nicht so, wie verlangt (Größe, Technik, Umfang): NICHT still kleiner oder anders
  bauen. todo "frage" am Punkt – der Nutzer entscheidet.
- Nach einem Umbau gehört ein Aufräum-Punkt dazu: alte Dateien weg, README, Startskripte und
  requirements an den echten Stand anpassen.
- Lies eine Datei, bevor du sie änderst.
- `befehl` läuft im Projektordner. Fürs Projekt immer das Python aus `venv\\Scripts`.
- Charme ja, Schönfärben nein. Ergebnisse mit Fakten beschreiben: was lief, was nicht, Zahlen.

## Aktuell bleiben (dein Wissen hat ein Datum, die Bibliotheken nicht)
- Schreib für die Versionen unter „Umgebung“, nicht für die aus deinem Gedächtnis.
- Bist du bei einer Funktion, Klasse oder einem Parameter unsicher: api_nachschlagen (z. B. "requests.get")
  – das liest Signatur und Doku aus der INSTALLIERTEN Version. Grundlagen zu Python, HTML, CSS, JS:
  doku_suchen. Neuere Version oder Lücken eines Pakets: paket_info.
- Nach dem Schreiben meldet NemiCLI Syntax- und Strukturfehler sofort. Vor todo "pruefen" eines
  Python-Punktes: code_pruefen (ruff: undefinierte Namen, veralteter Stil, typische Fehler).
- Web-Punkte (HTML/CSS/JS): seite_ansehen zeigt dir die Seite als Bild – prüf, ob sie aussieht wie verlangt.
- Meldet etwas „⚠️ Veraltet“ oder stellst du fest, dass sich eine API geändert hat: modernisieren und mit
  skill_merken festhalten, Name „<Paket> <Version>: <Thema>“, Inhalt: was alt war, was jetzt gilt.
"""


def prompt_hinweis() -> str:
    """Block für den System-Prompt, solange der Assistent an ist."""
    p = projekt()
    if p is None:
        return ""
    daten = laden(p)
    if daten is None:
        stand = ("Noch keine Todo-Liste. Nächster Schritt: verstehen, ggf. recherchieren, dann "
                 "todo \"anlegen\".")
        aufgabe = str(_zustand().get("aufgabe") or "").strip()
        if aufgabe:
            stand = f"Aufgabe beim Start: {aufgabe}\n" + stand
    else:
        stand = text_fuer_modell(daten)
        if fertig(daten):
            stand += ("\n\nAlle Punkte sind geprüft → Abschluss-Bericht in der festen Form. Frag danach, "
                      "ob der Coding-Assistent aus soll (/codeend) – ausschalten kann nur der Nutzer.")
    return ("\n\n# 🟦 Coding-Assistent ist AN\n"
            f"Projekt: {p}  (Ablage: {p / ABLAGE})\n"
            "Du arbeitest jetzt auf einem festen Weg. Das Programm führt die Todo-Liste und "
            "prüft; du denkst und baust. Der Modus bleibt an, bis der Nutzer ihn beendet.\n\n"
            + _WEG + _umgebung_block(p) + "\n## Aktueller Stand\n" + stand + "\n")


def _umgebung_block(p: Path) -> str:
    """Umgebung aus der Installation + passende Lehren. Leer, solange der Ordner fehlt."""
    if not p.is_dir():
        return ""
    try:
        import pyumgebung
        text = "\n## Umgebung (aus der Installation gelesen, nicht aus deinem Gedächtnis)\n" \
            + pyumgebung.umgebung_text(p) + "\n"
        pakete = list(pyumgebung.umgebung(p).get("relevant") or {})
    except Exception:
        return ""
    lehren = lehren_zu(pakete)
    if lehren:
        text += "\n## Deine Lehren zu diesen Paketen\n" + "\n".join(lehren) + "\n"
    return text


def lehren_zu(pakete: list[str], hoechstens: int = 5) -> list[str]:
    """Gemerkte Lehren (learn-Skills), deren Titel ein Paket nennt: '- Titel: Anfang des Inhalts'."""
    if not pakete:
        return []
    try:
        import learn
        liste = learn.list_skills()
    except Exception:
        return []
    muster = re.compile(r"\b(" + "|".join(re.escape(n) for n in pakete) + r")\b", re.I)
    raus = []
    for pfad, titel in liste:
        if not muster.search(titel or ""):
            continue
        try:
            inhalt = Path(pfad).read_text(encoding="utf-8").split("\n", 3)[-1]
        except OSError:
            inhalt = ""
        raus.append(f"- {titel.lstrip('# ').strip()}: {' '.join(inhalt.split())[:300]}")
        if len(raus) >= hoechstens:
            break
    return raus


def status_text() -> str:
    """Kurz für Statusleiste und /code status."""
    p = projekt()
    if p is None:
        return ""
    daten = laden(p)
    if daten is None:
        return f"Coding · {p.name}"
    gepr, gesamt = zaehlen(daten)
    return f"Coding · {p.name} · {gepr}/{gesamt}"
