"""Lern-LoRA: das lokale Modell lernt aus den Gesprächen einer Persönlichkeit – je Thema ein LoRA.

Drei Tempi des Lernens:
  schnell  der Kontext des laufenden Gesprächs,
  mittel   Gedächtnis und Skills (sofort nach jeder Runde),
  langsam  dieses LoRA: in einer ruhigen Stunde (Vorgabe nachts) aus allen Gesprächen der Persönlichkeit.

Beispiele: Frage des Nutzers → direkte Antwort der KI, nur Runden ohne Werkzeug (damit ohne Netz- und
Datei-Inhalt) und ohne Korrektur danach („nein“, „falsch“ …). Was neu war, zählt mehr: das Gewicht eines
Beispiels ist 1 + die mittlere Neuheit seines Gesprächs im Gedächtnis.

Themen: Die Fragen werden eingebettet und per k-Means gruppiert (höchstens THEMEN_MAX, je Thema mindestens
JE_THEMA Beispiele); jedes Thema bekommt ein eigenes LoRA und seine Mitte. Vor jeder Antwort wählt `wahl`
das Thema der Frage – nach Bedeutung, wenn der Encoder geladen ist, sonst nach Stichworten. Ein Wechsel
liest das Gespräch neu ein (das LoRA ändert den Rechenweg), deshalb bleibt das Thema, bis ein anderes
deutlich besser passt.

Ablage je Persönlichkeit und Grundmodell: Profile/<Name>/LoRA/Sprache/<Modell>/ mit themen.json und
thema_<n>.safetensors. Die Daten verlassen den Rechner nie; aus (Vorgabe) heißt: nichts wird trainiert
und nichts angeklinkt.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

from . import adapter as A

SCHALTER = "lern_lora"                 # an: nachts trainieren und im Chat anklinken
NACHT = "lern_lora_nacht"              # Stunde, ab der trainiert werden darf (0–23), oder "aus"
STAERKE = "lern_lora_staerke"          # 0–2
NACHT_VORGABE = 3
NACHT_FENSTER_H = 4                    # so viele Stunden ab NACHT
RUHE_S = 600                           # so lange ohne Eingabe, dann darf es losgehen
MIN_BEISPIELE = 20
MIN_NEU = 10                           # so viele neue Beispiele seit dem letzten Training, sonst nicht nochmal
BEISPIELE_MAX = 600                    # die neuesten
CHATS_MAX = 300
FRAGE_MAX = 4000                       # längere Fragen sind angehängte Dateien
ANTWORT_MIN = 20
THEMEN_MAX = 4
JE_THEMA = 25
SCHRITTE_JE_BEISPIEL = 3
SCHRITTE_MIN, SCHRITTE_MAX = 60, 300
RANG = 16
LERNRATE = 1e-4
WECHSEL_ABSTAND = 0.05                 # so viel besser (Kosinus) muss ein anderes Thema passen
STICHWORTE = 20                        # je Thema für die Wahl ohne Encoder
SYSTEM = "Du bist {name}. Antworte so, wie du es in euren Gesprächen tust."
_DENKEN = re.compile(r"<(think|thinking|denken)>.*?</\1>", re.S | re.I)
_DATEI = re.compile(r"thema_\d{1,2}\.safetensors")    # nur eigene Dateien im Themen-Ordner

_gewaehlt: dict[str, str] = {}         # Persönlichkeit|Modell → Datei des angeklinkten Themas
_sperre = threading.Lock()


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def an() -> bool:
    return bool(_config().get(SCHALTER, False))


def staerke() -> float:
    try:
        return min(2.0, max(0.0, float(_config().get(STAERKE, 1.0))))
    except (TypeError, ValueError):
        return 1.0


def nacht_stunde() -> int | None:
    wert = _config().get(NACHT, NACHT_VORGABE)
    if str(wert).strip().lower() in ("aus", "off", "none", ""):
        return None
    try:
        return int(wert) % 24
    except (TypeError, ValueError):
        return NACHT_VORGABE


def im_nachtfenster(stunde: int, beginn: int | None) -> bool:
    if beginn is None:
        return False
    return (stunde - beginn) % 24 < NACHT_FENSTER_H


# ---------------------------------------------------------------------------
# Ablage
# ---------------------------------------------------------------------------

def _persona() -> str:
    try:
        import persoenlichkeiten
        return persoenlichkeiten.active().name
    except Exception:
        return "KI"


def ordner(gguf: str | Path, anlegen: bool = False) -> Path:
    import profilordner
    return profilordner.ordner("LoRA", anlegen=anlegen) / "Sprache" / Path(gguf).stem


def themen_laden(gguf: str | Path) -> dict | None:
    try:
        d = json.loads((ordner(gguf) / "themen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("themen"), list):
        return None
    if not A.basis_passt(d.get("basis"), gguf):
        return None                                        # zu einem anderen Stand der GGUF-Datei
    d["themen"] = [t for t in d["themen"] if isinstance(t, dict) and _DATEI.fullmatch(str(t.get("datei") or ""))
                   and (ordner(gguf) / t["datei"]).is_file()]
    return d if d["themen"] else None


# ---------------------------------------------------------------------------
# Beispiele aus den Gesprächen
# ---------------------------------------------------------------------------

def _text(m: dict) -> str:
    import indexdb
    return indexdb._msg_text(m)


def _ist_werkzeug(m: dict) -> bool:
    import indexdb
    t = _text(m)
    return m.get("role") == "user" and (t.startswith(indexdb.ERGEBNIS_VORSATZ) or indexdb._fremd_markiert(t))


def runden(messages: list) -> list[tuple[str, str]]:
    """(Frage, Antwort) – nur direkte Antworten ohne Werkzeug und ohne Korrektur in der nächsten Frage."""
    import lernschleife
    raus = []
    ms = [m for m in messages or [] if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    for i in range(len(ms) - 1):
        frage, antwort = ms[i], ms[i + 1]
        if frage.get("role") != "user" or antwort.get("role") != "assistant" or _ist_werkzeug(frage):
            continue
        if i + 2 < len(ms) and _ist_werkzeug(ms[i + 2]):
            continue                                        # die Antwort war ein Werkzeug-Aufruf
        if i > 0 and ms[i - 1].get("role") == "user":
            continue                                        # mitten in einer Werkzeug-Kette
        folgende = next((_text(m) for m in ms[i + 2:] if m.get("role") == "user" and not _ist_werkzeug(m)), "")
        if folgende and lernschleife.KORREKTUR.search(folgende):
            continue
        f = _text(frage)
        a = _DENKEN.sub("", _text(antwort)).strip()
        if f and len(f) <= FRAGE_MAX and len(a) >= ANTWORT_MIN and "```aktion" not in a:
            raus.append((f, a))
    return raus


def _neuheit_je_chat(refs: list[str]) -> dict[str, float]:
    if not refs:
        return {}
    try:
        import indexdb
        con = indexdb._connect()
        try:
            return {r: float(n) for r, n in con.execute(
                f"SELECT ref, avg(neuheit) FROM chunks WHERE neuheit IS NOT NULL AND ref IN "
                f"({','.join('?' * len(refs))}) GROUP BY ref", refs)}
        finally:
            con.close()
    except Exception:
        return {}


def beispiele() -> list[dict]:
    """Beispiele der aktiven Persönlichkeit (nur ihre eigenen Gespräche, nicht die gemeinsamen von früher)."""
    import chatstore
    import profilordner
    eigen = profilordner.ordner("Chats", anlegen=False).parent.name
    chats = [c for c in chatstore.list_chats() if c.get("profil") == eigen][:CHATS_MAX]
    neuheit = _neuheit_je_chat([chatstore.index_ref(c["id"]) for c in chats])
    system = SYSTEM.format(name=_persona())
    raus: list[dict] = []
    for c in chats:
        daten = chatstore.load(c["id"])
        if not daten:
            continue
        gewicht = 1.0 + neuheit.get(chatstore.index_ref(c["id"]), 0.0)
        for f, a in runden(daten.get("messages") or []):
            raus.append({"system": system, "frage": f, "antwort": a, "gewicht": round(gewicht, 3)})
        if len(raus) >= BEISPIELE_MAX:
            break
    return raus[:BEISPIELE_MAX]


# ---------------------------------------------------------------------------
# Themen
# ---------------------------------------------------------------------------

def _kmeans(x, k: int, wdh: int = 30):
    """Kosinus-k-Means (Zeilen normiert), Start nach k-Means++ mit festem Zufall. → Zuordnung, Mitten."""
    import numpy as np
    rng = np.random.default_rng(7)
    mitten = [x[int(rng.integers(len(x)))]]
    for _ in range(1, k):
        abstand = 1.0 - np.max(x @ np.vstack(mitten).T, axis=1)
        p = np.clip(abstand, 1e-9, None)
        mitten.append(x[int(rng.choice(len(x), p=p / p.sum()))])
    m = np.vstack(mitten)
    for _ in range(wdh):
        zu = np.argmax(x @ m.T, axis=1)
        neu = np.vstack([x[zu == j].mean(axis=0) if (zu == j).any() else m[j] for j in range(k)])
        neu /= np.linalg.norm(neu, axis=1, keepdims=True).clip(1e-9)
        if np.allclose(neu, m):
            break
        m = neu
    return np.argmax(x @ m.T, axis=1), m


def _stichworte(fragen: list[str], andere: list[str]) -> list[str]:
    """Begriffe, die in diesem Thema häufiger sind als in den anderen."""
    import indexdb
    hier, dort = {}, {}
    for f in fragen:
        for b in set(indexdb.begriffe(f)) | {w for w in re.findall(r"\w{5,}", f.casefold())}:
            hier[b] = hier.get(b, 0) + 1
    for f in andere:
        for b in set(indexdb.begriffe(f)) | {w for w in re.findall(r"\w{5,}", f.casefold())}:
            dort[b] = dort.get(b, 0) + 1
    n_h, n_d = max(1, len(fragen)), max(1, len(andere))
    wert = {b: n / n_h - dort.get(b, 0) / n_d for b, n in hier.items() if n >= 2}
    return [b for b, _ in sorted(wert.items(), key=lambda x: -x[1])][:STICHWORTE]


def themen_bilden(bsp: list[dict]) -> list[dict]:
    """[{name, stichworte, mitte (oder None), beispiele}] – ein Thema, wenn es zu wenig gibt oder kein Encoder
    da ist."""
    k = min(THEMEN_MAX, len(bsp) // JE_THEMA)
    vektoren = None
    if k >= 2:
        try:
            import memory
            vektoren = memory._embed_texts([b["frage"][:1500] for b in bsp], query=True)
        except Exception:
            vektoren = None
    if k < 2 or not vektoren or len(vektoren) != len(bsp):
        return [{"name": "allgemein", "stichworte": _stichworte([b["frage"] for b in bsp], []), "mitte": None,
                 "beispiele": bsp}]
    import numpy as np
    x = np.asarray(vektoren, dtype="float32")
    x /= np.linalg.norm(x, axis=1, keepdims=True).clip(1e-9)
    zu, mitten = _kmeans(x, k)
    themen = []
    for j in range(k):
        teil = [b for b, z in zip(bsp, zu) if z == j]
        if len(teil) < JE_THEMA // 2:
            continue                                         # zu klein: geht in kein LoRA
        rest = [b["frage"] for b, z in zip(bsp, zu) if z != j]
        worte = _stichworte([b["frage"] for b in teil], rest)
        themen.append({"name": " · ".join(worte[:3]) or f"Thema {j + 1}", "stichworte": worte,
                       "mitte": [round(float(v), 5) for v in mitten[j]], "beispiele": teil})
    return themen or [{"name": "allgemein", "stichworte": [], "mitte": None, "beispiele": bsp}]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _stand_datei() -> Path:
    import profilordner
    return profilordner.ordner("LoRA", anlegen=False) / "Sprache" / "stand.json"


def stand() -> dict:
    try:
        d = json.loads(_stand_datei().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _stand_merken(**felder) -> None:
    import atomar
    d = {**stand(), **felder}
    _stand_datei().parent.mkdir(parents=True, exist_ok=True)
    atomar.text_schreiben(_stand_datei(), json.dumps(d, ensure_ascii=False, indent=1))


def modell_pruefen(gguf: str | Path) -> None:
    """ValueError, wenn für dieses Grundmodell kein Lernweg existiert."""
    from ggufengine.gguf import GGUFFile
    gg = GGUFFile(str(gguf))
    try:
        if gg.architecture not in A.BAUARTEN:
            raise ValueError(f"Für die Bauart {gg.architecture} gibt es noch keinen Lernweg "
                             f"(geht mit: {', '.join(A.BAUARTEN)}).")
    finally:
        gg.close()


def trainieren(gguf: str | Path, on_status=None, abbruch=None) -> dict:
    """Beispiele sammeln, Themen bilden, je Thema ein LoRA trainieren, themen.json schreiben.
    Gibt {themen: [(name, beispiele)], beispiele} zurück. Angehalten: RuntimeError – der Zwischenstand bleibt,
    die bisherigen Themen gelten weiter (neue LoRAs entstehen als thema_<n>.neu.safetensors)."""
    from . import training as T
    gguf = Path(gguf)
    modell_pruefen(gguf)
    bsp = beispiele()
    if len(bsp) < MIN_BEISPIELE:
        raise ValueError(f"Erst {len(bsp)} geeignete Gesprächsrunden – mindestens {MIN_BEISPIELE} "
                         "(direkte Antworten ohne Werkzeug und ohne Korrektur danach).")
    themen = themen_bilden(bsp)
    ziel = ordner(gguf, anlegen=True)
    ziel.mkdir(parents=True, exist_ok=True)
    fertig = []
    neu: list[Path] = []                                     # erst nach allen Themen an ihren Platz (zu themen.json)
    try:
        for i, t in enumerate(themen, 1):
            schritte = max(SCHRITTE_MIN, min(SCHRITTE_MAX, SCHRITTE_JE_BEISPIEL * len(t["beispiele"])))
            neu.append(ziel / f"thema_{i}.neu.safetensors")
            T.trainieren(gguf=gguf, beispiele=t["beispiele"], ziel=neu[-1],
                         titel=f"Lern-LoRA {i}/{len(themen)} ({t['name']})", schritte=schritte, rang=RANG,
                         lr=LERNRATE, on_status=on_status, abbruch=abbruch)
            fertig.append({"datei": f"thema_{i}.safetensors", "name": t["name"], "stichworte": t["stichworte"],
                           "mitte": t["mitte"], "beispiele": len(t["beispiele"])})
    except BaseException:
        for p in neu:                                        # die alten Themen bleiben gültig
            p.unlink(missing_ok=True)
        raise
    kennung = ""
    if any(t["mitte"] for t in fertig):
        try:
            import memory
            kennung = memory.modell_kennung()
        except Exception:
            kennung = ""
    import atomar
    inhalt = json.dumps({"basis": A.basis_kennung(gguf), "modell": gguf.name, "embed": kennung,
                         "zeit": time.strftime("%Y-%m-%d %H:%M"), "beispiele": len(bsp), "themen": fertig},
                        ensure_ascii=False)
    for p, t in zip(neu, fertig):                            # direkt hintereinander: Dateien und themen.json
        p.replace(ziel / t["datei"])
    atomar.text_schreiben(ziel / "themen.json", inhalt)
    for alt in ziel.glob("thema_*.safetensors"):            # Themen eines früheren Trainings, die es nicht mehr gibt
        if alt.name not in {t["datei"] for t in fertig} and not alt.name.endswith("_stand.safetensors"):
            alt.unlink(missing_ok=True)
    _stand_merken(zuletzt=time.strftime("%Y-%m-%d"), beispiele=len(bsp), modell=gguf.name)
    with _sperre:
        _gewaehlt.clear()
    return {"themen": [(t["name"], t["beispiele"]) for t in fertig], "beispiele": len(bsp)}


def nacht_faellig(jetzt: time.struct_time | None = None) -> bool:
    """An, im Nachtfenster, heute noch nicht versucht."""
    jetzt = jetzt or time.localtime()
    if not an() or not im_nachtfenster(jetzt.tm_hour, nacht_stunde()):
        return False
    return stand().get("versucht") != time.strftime("%Y-%m-%d", jetzt)


def lohnt(gguf: str | Path) -> bool:
    """Genug neue Beispiele seit dem letzten Training (oder noch keins für dieses Modell)?"""
    s = stand()
    if themen_laden(gguf) is None:
        return True
    try:
        return len(beispiele()) - int(s.get("beispiele") or 0) >= MIN_NEU
    except Exception as exc:
        A._melden(f"Lern-LoRA: neue Beispiele nicht zählbar ({exc}) – kein Training")
        return False


def versucht() -> None:
    _stand_merken(versucht=time.strftime("%Y-%m-%d"))


def vergessen() -> int:
    """Alle Lern-LoRAs der aktiven Persönlichkeit löschen. Gibt die Zahl der Dateien zurück."""
    import shutil
    import profilordner
    wurzel = profilordner.ordner("LoRA", anlegen=False) / "Sprache"
    n = sum(1 for _ in wurzel.rglob("*.safetensors")) if wurzel.is_dir() else 0
    if wurzel.is_dir() and not wurzel.is_symlink():
        shutil.rmtree(wurzel)
    with _sperre:
        _gewaehlt.clear()
    return n


# ---------------------------------------------------------------------------
# Wahl vor jeder Antwort
# ---------------------------------------------------------------------------

def _frage_vektor(frage: str, kennung: str):
    try:
        import memory
        if not frage or not memory.encoder_loaded() or memory.modell_kennung() != kennung:
            return None
        v = memory.frage_einbetten(frage[:1500])
        return v[0] if v else None
    except Exception:
        return None


def _stichwort_wert(frage: str, t: dict) -> int:
    import indexdb
    worte = set(indexdb.begriffe(frage)) | set(re.findall(r"\w{5,}", (frage or "").casefold()))
    return len(worte & set(t.get("stichworte") or []))


def wahl(gguf: str | Path, frage: str = "") -> tuple[Path, float] | None:
    """(LoRA-Datei, Stärke) für diese Frage – oder None (aus, keins da, Stärke 0)."""
    if not an() or staerke() <= 0:
        return None
    d = themen_laden(gguf)
    if d is None:
        return None
    themen = d["themen"]
    schluessel = f"{_persona()}|{A.basis_kennung(gguf)}"
    with _sperre:
        jetzt = _gewaehlt.get(schluessel)
    namen = [t["datei"] for t in themen]
    if jetzt not in namen:
        jetzt = None
    beste = None
    if len(themen) > 1:
        v = _frage_vektor(frage, d.get("embed") or "")
        if v is not None and all(t.get("mitte") for t in themen):
            norm = math.sqrt(sum(a * a for a in v)) or 1.0
            werte = {t["datei"]: sum(a * b for a, b in zip(v, t["mitte"])) / norm for t in themen}
            beste = max(werte, key=werte.get)
            if jetzt and beste != jetzt and werte[beste] - werte[jetzt] < WECHSEL_ABSTAND:
                beste = jetzt
        else:
            werte = {t["datei"]: _stichwort_wert(frage, t) for t in themen}
            spitze = max(werte, key=werte.get)
            if werte[spitze] > 0 and (jetzt is None or werte[spitze] > werte.get(jetzt, 0) + 1):
                beste = spitze
    if beste is None:
        beste = jetzt or max(themen, key=lambda t: int(t.get("beispiele") or 0))["datei"]
    with _sperre:
        _gewaehlt[schluessel] = beste
    return ordner(gguf) / beste, staerke()


def stand_zeilen(gguf: str | Path | None) -> list[str]:
    s = stand()
    zeilen = [f"🌙 Lern-LoRA: {'an' if an() else 'aus'} · Stärke {staerke():g} · nachts ab "
              + (f"{nacht_stunde()}:00 Uhr ({NACHT_FENSTER_H} h)" if nacht_stunde() is not None else "– (aus)")]
    if s.get("zuletzt"):
        zeilen.append(f"   zuletzt trainiert: {s['zuletzt']} ({s.get('beispiele', '?')} Beispiele, "
                      f"{s.get('modell', '?')})")
    if gguf is None:
        zeilen.append("   Kein lokales GGUF-Modell aktiv – das Lern-LoRA gilt nur für den eigenen GGUF-Motor.")
        return zeilen
    d = themen_laden(gguf)
    if d is None:
        zeilen.append(f"   Für {Path(gguf).name} gibt es noch kein Lern-LoRA dieser Persönlichkeit.")
    else:
        with _sperre:
            jetzt = _gewaehlt.get(f"{_persona()}|{A.basis_kennung(gguf)}")
        for t in d["themen"]:
            zeilen.append(f"   {'▶' if t['datei'] == jetzt else '·'} {t['name']} – {t.get('beispiele', '?')} Beispiele")
    return zeilen
