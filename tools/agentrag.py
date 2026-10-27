"""agentrag.py – die Persönlichkeit durchsucht ihr Gedächtnis selbst.

Klassisches RAG hängt vor jeder Antwort die Treffer zur letzten Nachricht an –
bei „und was war da nochmal?“ fehlt der Zusammenhang, und das Modell bekommt,
was kommt. Hier entscheidet die Persönlichkeit: ob sie sucht, womit, in welcher
Quelle und welchem Zeitraum, und ob sie eine gefundene Quelle ganz liest.
Mehrere Suchen hintereinander sind ausdrücklich gewollt.

  gedaechtnis_suchen   frage, fragen, vermutung, quelle, seit, bis, anzahl
                       → Treffer mit Quelle, Datum und dem Text davor/danach
  gedaechtnis_lesen    ref, ab   → die Quelle (Chat, Bericht, Wissen …), lange stückweise

Beide nur lesend; die Suche selbst ist hybrid (indexdb.suchen): mehrere Fassungen einer
Frage (Multi-Query) und eine vermutete Antwort (HyDE) gehen in dieselbe Fusion. Sind die
Treffer schwach, sagt das Ergebnis es und schlägt vor, wie weiter (Corrective RAG). Was
danach gelesen wird, lernt die Rangliste (rangliste.py).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import fremddaten
import indexdb

ANZAHL_STANDARD = 8
ANZAHL_MAX = 20
SNIPPET = 420
NACHBAR = 160                     # so viel vom Text davor und danach (Small-to-Big)
NACHBARN_FUER = 5                 # die ersten so vielen Treffer bekommen ihn
SCHWACH_AB = 0.5                  # bester Kosinus darunter (und kein Stichwort): schwache Treffer
RELEVANZ_AB = 0.3                 # mit Reranker: beste Relevanz darunter = schwache Treffer

# Quelle (wie die Persönlichkeit sie nennt) → Arten in memory.db und optionaler ref-Anfang.
QUELLEN = {
    "chat": (("chat", "chatmd"), None), "chats": (("chat", "chatmd"), None),
    "notiz": (("note",), None), "notizen": (("note",), None),
    "bericht": (("bericht",), None), "berichte": (("bericht",), None),
    "wache": (("bericht",), ("bericht:Wache_", "bericht:Vollscan_")),
    "vollscan": (("bericht",), ("bericht:Vollscan_",)),
    "skill": (("skill",), None), "skills": (("skill",), None),
    "code": (("code",), None),
    "wissen": (("wissen",), None), "dokument": (("wissen",), None), "dokumente": (("wissen",), None),
    "aktuell": (("chat",), ("<laufender Chat>",)), "hier": (("chat",), ("<laufender Chat>",)),
}
_LAUFEND = "<laufender Chat>"          # wird beim Suchen durch den ref des laufenden Chats ersetzt


def _laufender_ref() -> str:
    try:
        import chatstore
        chat = chatstore.aktueller_chat()
        return chatstore.index_ref(int(chat)) + "$" if chat else ""
    except Exception:
        return ""
_ART_NAME = {"chat": "Chat", "chatmd": "Chat-Mitschrift", "note": "Notiz", "bericht": "Bericht",
             "skill": "Skill", "code": "Code", "wissen": "Wissen"}


def datum_lesen(text: str, heute: date | None = None) -> str:
    """'2026-09-20', '20.09.2026', '20.09.', 'heute', 'gestern', '7 tage', '2 wochen'
    → 'YYYY-MM-DD'; unverständlich → ''."""
    heute = heute or date.today()
    t = (text or "").strip().lower()
    if not t:
        return ""
    if t == "heute":
        return heute.isoformat()
    if t == "gestern":
        return (heute - timedelta(days=1)).isoformat()
    m = re.fullmatch(r"(\d+)\s*(t|tag|tage|tagen|d|w|woche|wochen|m|monat|monate|monaten)", t)
    if m:
        n, einheit = min(int(m.group(1)), 36500), m.group(2)       # höchstens 100 Jahre zurück
        tage = n * (7 if einheit.startswith("w") else 30 if einheit.startswith("m") else 1)
        return (heute - timedelta(days=tage)).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", t)
    if m:
        jahr = int(m.group(3)) if m.group(3) else heute.year
        try:
            return date(jahr, int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return ""
    return ""


def _quelle(text: str) -> tuple[tuple | None, tuple | None, list[str]]:
    """'chat, wache' → (Arten, ref-Anfänge, unbekannte Namen)."""
    arten: list[str] = []
    praefixe: list[str] = []
    unbekannt: list[str] = []
    for teil in re.split(r"[,\s/]+", (text or "").strip().lower()):
        if not teil or teil in ("alle", "alles"):
            continue
        if teil not in QUELLEN:
            unbekannt.append(teil)
            continue
        a, p = QUELLEN[teil]
        arten += [x for x in a if x not in arten]
        if p:
            praefixe += list(p)
    # Ein ref-Filter gilt nur, wenn ALLE gewählten Quellen einen haben –
    # „chat, wache“ soll Chats nicht wegfiltern.
    alle_mit_praefix = all(QUELLEN[t][1] for t in re.split(r"[,\s/]+", text.strip().lower())
                           if t in QUELLEN) if arten else False
    if _LAUFEND in praefixe:
        laufend = _laufender_ref()
        praefixe = [laufend if p == _LAUFEND else p for p in praefixe if p != _LAUFEND or laufend]
        if not laufend:                      # kein laufender Chat: dann eben alle Chats
            alle_mit_praefix = alle_mit_praefix and bool(praefixe)
    return (tuple(arten) or None, tuple(praefixe) if alle_mit_praefix else None, unbekannt)


def _kopf(h: dict) -> str:
    art = _ART_NAME.get(h.get("kind", ""), h.get("kind", ""))
    datum = h.get("datum", "")[:10]
    if datum:
        try:
            datum = datetime.strptime(datum, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            pass
    return f"{art} · ref={h.get('ref', '')}" + (f" · {datum}" if datum else "")


def _fragen(a: dict) -> list[str]:
    """frage (Text) und fragen (Liste oder durch | getrennt) → alle Fassungen."""
    raus = [str(a.get("frage") or a.get("suche") or "")]
    mehr = a.get("fragen")
    if isinstance(mehr, str):
        mehr = mehr.split("|")
    raus += [str(f) for f in (mehr or []) if isinstance(f, (str, int, float))]
    return [f.strip() for f in raus if f and f.strip()]


def _flach(text: str) -> str:
    return " ".join((text or "").split())


def _ausschnitt(text: str) -> str:
    text = _flach(text)
    return text if len(text) <= SNIPPET else text[:SNIPPET] + " …"


def _mit_nachbarn(h: dict) -> str:
    """Der Treffer mit dem Text davor (↑) und danach (↓) aus derselben Quelle."""
    text = _ausschnitt(h.get("text"))
    vorher, nachher = indexdb.nachbarn(h["rowid"]) if h.get("rowid") is not None else ("", "")
    teile = []
    if vorher:
        teile.append("↑ …" + _flach(vorher)[-NACHBAR:])
    teile.append(text)
    if nachher:
        teile.append("↓ " + _flach(nachher)[:NACHBAR] + " …")
    return "\n".join(teile)


def _schwach(treffer: list[dict]) -> bool:
    """Die Suche hat wohl nicht das Richtige: der Reranker hält nichts für passend – oder, ohne ihn,
    kein Stichwort-Treffer und keine gute Ähnlichkeit."""
    relevanz = [h["relevanz"] for h in treffer if h.get("relevanz") is not None]
    if relevanz:
        return max(relevanz) < RELEVANZ_AB
    if any("wort" in (h.get("wie") or "") for h in treffer):
        return False
    werte = [h["aehnlich"] for h in treffer if h.get("aehnlich") is not None]
    return not werte or max(werte) < SCHWACH_AB


_WEITER_SUCHEN = ("Andere Worte oder Synonyme versuchen, mehrere Fassungen auf einmal in „fragen“ schicken, eine "
                  "vermutete Antwort in „vermutung“ (sucht nach Bedeutung), Filter lockern – oder dem Nutzer sagen, "
                  "dass es dazu nichts im Gedächtnis gibt. Nicht raten.")


def suchen(a: dict) -> str:
    fragen = _fragen(a)
    vermutung = str(a.get("vermutung") or "").strip()
    if not fragen and not vermutung:
        return "Keine Frage angegeben – Feld „frage“ mit den Suchworten füllen."
    try:
        anzahl = max(1, min(int(a.get("anzahl") or ANZAHL_STANDARD), ANZAHL_MAX))
    except (TypeError, ValueError):
        anzahl = ANZAHL_STANDARD
    arten, praefixe, unbekannt = _quelle(str(a.get("quelle") or ""))
    seit = datum_lesen(str(a.get("seit") or ""))
    bis = datum_lesen(str(a.get("bis") or ""))
    treffer = indexdb.suchen(fragen, anzahl, arten=arten, seit=seit, bis=bis,
                             ref_praefix=praefixe, laden=True, vermutung=vermutung)
    titel = " | ".join(fragen) or vermutung[:80]
    filter_text = ", ".join(x for x in (
        f"Quelle {a.get('quelle')}" if arten else "", f"seit {seit}" if seit else "",
        f"bis {bis}" if bis else "", "mit Vermutung" if vermutung else "") if x)
    hinweis = (f"Unbekannte Quelle(n) ignoriert: {', '.join(unbekannt)} – möglich: "
               "chat, notiz, bericht, wache, vollscan, skill, code, wissen, aktuell.\n" if unbekannt else "")
    if not treffer:
        return (hinweis + f"Nichts gefunden zu „{titel}“" + (f" ({filter_text})" if filter_text else "")
                + ". " + _WEITER_SUCHEN)
    try:
        import rangliste
        rangliste.gezeigt(treffer)
    except Exception:
        pass
    zeilen = []
    for n, h in enumerate(treffer, 1):
        aehnl = (f" · Relevanz {h['relevanz']:.2f}" if h.get("relevanz") is not None else
                 f" · Ähnlichkeit {h['aehnlich']:.2f}" if h.get("aehnlich") is not None else "")
        text = _mit_nachbarn(h) if n <= NACHBARN_FUER else _ausschnitt(h.get("text"))
        zeilen.append(f"[{n}] {_kopf(h)} · gefunden über {h.get('wie') or '?'}{aehnl}\n{text}")
    kopf = (hinweis + f"{len(treffer)} Treffer zu „{titel}“" + (f" ({filter_text})" if filter_text else "")
            + ". ↑/↓ = Text davor/danach in derselben Quelle. Ganze Quelle: gedaechtnis_lesen mit ref.")
    if _schwach(treffer):
        kopf += "\n⚠ Die Treffer sind schwach – wohl nicht das Gesuchte. " + _WEITER_SUCHEN
    netz = any(_von_aussen(h.get("ref")) for h in treffer)
    return kopf + "\n" + fremddaten.rahmen("\n\n".join(zeilen), "suche", titel, netz=netz)


def _von_aussen(ref) -> bool:
    """Berichte (Recherche, Zeitplan-Aufträge, Wache) sind aus Fremdinhalt entstanden: sie behalten die
    Netz-Marke, auch wenn sie nur noch im Gedächtnis liegen – und die Runde gilt dann als „von außen“."""
    if str(ref or "").startswith("bericht:"):
        try:
            import actions
            actions.fremdinhalt()
        except Exception:
            pass
        return True
    return False


def lesen(a: dict) -> str:
    ref = str(a.get("ref") or "").strip()
    if not ref:
        return "Kein ref angegeben – den ref aus einem Treffer von gedaechtnis_suchen nehmen (z. B. chat:152)."
    try:
        ab = max(1, int(a.get("ab") or 1))
    except (TypeError, ValueError):
        ab = 1
    q = indexdb.quelle_lesen(ref, ab=ab)
    if not q:
        return f"Keine Quelle „{ref}“ im Gedächtnis. Den ref genau so übernehmen, wie die Suche ihn nennt."
    if ab == 1:
        try:
            import rangliste
            rangliste.gelesen(ref)                 # der Treffer war nützlich: lernt die Rangliste
        except Exception:
            pass
    kopf = f"{_kopf(q)} · {q['brocken']} Abschnitte"
    if q["ab"] > 1 or q["weiter"]:
        kopf += f" · hier Abschnitt {q['ab']}–{(q['weiter'] - 1) if q['weiter'] else q['brocken']}"
    if q["weiter"]:
        kopf += f" · weiter mit ab={q['weiter']}"
    elif q["gekuerzt"]:
        kopf += " · gekürzt"
    return kopf + "\n" + fremddaten.rahmen(q["text"], "datei", ref, netz=_von_aussen(ref))
