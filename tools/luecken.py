"""
luecken.py - Bekannte Sicherheitslücken und Schadpakete im venv (OSV.dev).

Fragt die offene Datenbank OSV.dev (sammelt GitHub-Advisories, PyPI-Meldungen und die
OpenSSF-Liste schädlicher Pakete) nach jedem installierten Paket samt Version. Je Fund:
Kennung (CVE, wenn es eine gibt), Schwere, Kurzbeschreibung und die erste Version, die
die Lücke behebt. Einträge MAL-… sind Schadpakete – die gehören entfernt, nicht aktualisiert.
Behoben wird mit einer neueren Version; ältere Versionen haben in aller Regel mehr Lücken.
"""
from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

BATCH = "https://api.osv.dev/v1/querybatch"
EINZELN = "https://api.osv.dev/v1/vulns/{}"
ABFRAGE = "https://api.osv.dev/v1/query"
LLAMACPP_REPO = "https://github.com/ggml-org/llama.cpp"
MAX_DETAILS = 80
PARALLEL = 4
FRIST_S = 60.0
MAX_BYTES = 32 * 2**20
_SCHWERE = {"CRITICAL": "kritisch", "HIGH": "hoch", "MODERATE": "mittel", "MEDIUM": "mittel", "LOW": "niedrig"}


@dataclass
class Luecke:
    id: str
    cve: str = ""
    schwere: str = "?"
    text: str = ""
    behoben: str = ""          # erste Version über der installierten, die sie schließt – leer: keine
    schadcode: bool = False
    gguf: bool = False         # betrifft das Einlesen von GGUF-Dateien (llama.cpp)

    @property
    def kennung(self) -> str:
        return self.cve or self.id


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _behoben(details: dict, paket: str, version: str) -> str:
    from packaging.version import InvalidVersion, Version
    try:
        jetzt = Version(version)
    except InvalidVersion:
        return ""
    kandidaten = []
    for a in details.get("affected", []):
        p = a.get("package") or {}
        if p.get("ecosystem") != "PyPI" or norm(p.get("name", "")) != paket:
            continue
        for r in a.get("ranges", []):
            for e in r.get("events", []):
                if "fixed" in e:
                    try:
                        v = Version(e["fixed"])
                    except InvalidVersion:
                        continue
                    if v > jetzt:
                        kandidaten.append(v)
    return str(min(kandidaten)) if kandidaten else ""


# CVSS 3.x Basiswert (FIRST, Spezifikation 3.1, Abschnitt 7)
_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}


def _aufrunden(x: float) -> float:
    ganz = round(x * 100000)
    return ganz / 100000.0 if ganz % 10000 == 0 else (math.floor(ganz / 10000) + 1) / 10.0


def cvss3_basiswert(vektor: str) -> Optional[float]:
    """Basiswert eines CVSS:3.0/3.1-Vektors – None bei anderem oder unvollständigem Vektor."""
    teile = str(vektor or "").strip().split("/")
    if not teile or teile[0] not in ("CVSS:3.0", "CVSS:3.1"):
        return None
    m = dict(t.split(":", 1) for t in teile[1:] if ":" in t)
    try:
        if m["S"] not in ("U", "C"):
            return None
        geaendert = m["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if geaendert else 0.62, "H": 0.5 if geaendert else 0.27}[m["PR"]]
        iss = 1 - (1 - _CIA[m["C"]]) * (1 - _CIA[m["I"]]) * (1 - _CIA[m["A"]])
        ausnutzbar = 8.22 * _AV[m["AV"]] * _AC[m["AC"]] * pr * _UI[m["UI"]]
    except KeyError:
        return None
    wirkung = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if geaendert else 6.42 * iss
    if wirkung <= 0:
        return 0.0
    return _aufrunden(min((1.08 if geaendert else 1.0) * (wirkung + ausnutzbar), 10.0))


def stufe_aus_wert(wert: Optional[float]) -> str:
    if wert is None or wert <= 0:
        return "?"
    if wert >= 9.0:
        return "kritisch"
    if wert >= 7.0:
        return "hoch"
    return "mittel" if wert >= 4.0 else "niedrig"


def _schwere(details: dict) -> str:
    """Schwere aus der Einstufung der Quelle, sonst aus dem CVSS-3-Vektor (severity[].score)."""
    schwere = _SCHWERE.get(str((details.get("database_specific") or {}).get("severity", "")).upper(), "?")
    if schwere != "?":
        return schwere
    werte = [cvss3_basiswert(s.get("score", "")) for s in details.get("severity") or [] if isinstance(s, dict)]
    werte = [w for w in werte if w is not None]
    return stufe_aus_wert(max(werte)) if werte else "?"


def _luecke(vid: str, details: Optional[dict], paket: str, version: str) -> Luecke:
    if not details:
        return Luecke(id=vid, schadcode=vid.startswith("MAL-"),
                      text=f"Details nicht geladen – Eintrag selbst ansehen: https://osv.dev/vulnerability/{vid}")
    cve = next((a for a in details.get("aliases", []) if a.startswith("CVE-")), "")
    text = " ".join(str(details.get("summary") or details.get("details") or "").split())[:200]
    return Luecke(id=vid, cve=cve, schwere=_schwere(details), text=text, behoben=_behoben(details, paket, version),
                  schadcode=vid.startswith("MAL-"))


_geteilt = None


def _client():
    """Gemeinsamer Client des Moduls."""
    global _geteilt
    if _geteilt is None:
        import netzhilfe
        _geteilt = netzhilfe.GeteilterClient(headers={"User-Agent": "NemiCLI-Paketpruefung"}, timeout=30.0)
    return _geteilt()


def _post_get(post: Optional[Callable], get: Optional[Callable]) -> tuple[Callable, Callable]:
    import netzhilfe
    if post is None:
        def post(url, daten):
            return netzhilfe.abrufen(_client(), "POST", url, json=daten, frist_s=FRIST_S,
                                     max_bytes=MAX_BYTES).raise_for_status().json()
    if get is None:
        def get(url):
            return netzhilfe.abrufen(_client(), "GET", url, frist_s=FRIST_S,
                                     max_bytes=MAX_BYTES).raise_for_status().json()
    return post, get


def _details_holen(ids: list[str], get: Callable) -> dict[str, dict]:
    """Details je Kennung, PARALLEL gleichzeitig; was nicht lädt, fehlt im Ergebnis."""
    def eins(vid):
        try:
            return vid, get(EINZELN.format(vid))
        except Exception:
            return vid, None
    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        return {vid: d for vid, d in pool.map(eins, ids) if isinstance(d, dict)}


def pruefen(pakete: dict[str, str], post: Optional[Callable] = None,
            get: Optional[Callable] = None) -> dict[str, list[Luecke]]:
    """{Paket: [Lücken]} für {Paket: Version}; Pakete ohne Fund fehlen im Ergebnis.
    OSError, wenn OSV nicht erreichbar ist."""
    if not pakete:
        return {}
    namen = sorted(pakete)
    post, get = _post_get(post, get)
    try:
        antwort = post(BATCH, {"queries": [{"package": {"name": n, "ecosystem": "PyPI"},
                                            "version": pakete[n]} for n in namen]})
    except Exception as e:
        raise OSError(f"Lücken-Datenbank (OSV.dev) nicht erreichbar: {e}") from None
    funde = {n: [v["id"] for v in (r or {}).get("vulns", []) if v.get("id")]
             for n, r in zip(namen, antwort.get("results", []))}
    # Über MAX_DETAILS hinaus ohne Details: die Einträge bleiben, mit Hinweis statt Beschreibung
    details = _details_holen(sorted({v for ids in funde.values() for v in ids})[:MAX_DETAILS], get)
    return {n: zusammenfassen([_luecke(v, details.get(v), norm(n), pakete[n]) for v in ids])
            for n, ids in funde.items() if ids}


def zusammenfassen(liste: list[Luecke]) -> list[Luecke]:
    """Dieselbe Lücke aus mehreren Quellen (GitHub, PyPI) zu einem Eintrag: bekannte Schwere,
    späteste Behebung, erste Beschreibung."""
    from packaging.version import InvalidVersion, Version

    def v(s):
        try:
            return Version(s)
        except InvalidVersion:
            return Version("0")
    raus: dict[str, Luecke] = {}
    for l in liste:
        alt = raus.get(l.kennung)
        if alt is None:
            raus[l.kennung] = l
            continue
        if alt.schwere == "?":
            alt.schwere = l.schwere
        if l.behoben and (not alt.behoben or v(l.behoben) > v(alt.behoben)):
            alt.behoben = l.behoben
        alt.text = alt.text or l.text
        alt.schadcode = alt.schadcode or l.schadcode
        alt.gguf = alt.gguf or l.gguf
    return list(raus.values())


_RANG = {"kritisch": 4, "hoch": 3, "mittel": 2, "niedrig": 1, "?": 0}


def schwerste(liste: list[Luecke]) -> str:
    return max((l.schwere for l in liste), key=lambda s: _RANG.get(s, 0), default="?")


# --- llama.cpp (Reserve-Motor) ---------------------------------------------------------------------------------
# OSV führt llama.cpp als Git-Projekt: Tags b<Nummer>. Die erste behebende Version steht bei CVE-Einträgen in
# affected[].ranges[].database_specific.extracted_events; ohne sie zählt nur ein Eintrag, der den Build in
# affected[].versions ausdrücklich nennt.
_TAG = re.compile(r"^b(\d+)$")
_GGUF = re.compile(r"(?i)\bgguf\b")


def _nummer(tag) -> int:
    m = _TAG.match(str(tag or "").strip())
    return int(m.group(1)) if m else -1


def _llama_bereiche(details: dict) -> tuple[list[tuple[int, int]], set[str]]:
    """([(eingeführt, behoben) als Build-Nummern], genannte Tags) aus den Einträgen für llama.cpp.
    behoben -1: kein behebender Build bekannt."""
    bereiche, genannt = [], set()
    for a in details.get("affected", []) or []:
        ranges = [r for r in a.get("ranges", []) or [] if isinstance(r, dict)]
        repos = {str(r.get("repo") or "").rstrip("/").lower() for r in ranges}
        paket = str((a.get("package") or {}).get("name") or "").rstrip("/").lower()
        if LLAMACPP_REPO.lower() not in repos | {paket}:
            continue
        genannt |= {str(v) for v in a.get("versions", []) or []}
        for r in ranges:
            ereignisse = list(r.get("events", []) or [])
            for x in (r.get("database_specific") or {}).get("extracted_events", []) or []:
                ereignisse += list(x.get("range", []) or []) if isinstance(x, dict) else []
            anfang = 0
            for e in ereignisse:
                if not isinstance(e, dict):
                    continue
                if "introduced" in e:
                    anfang = 0 if str(e["introduced"]) == "0" else max(_nummer(e["introduced"]), 0)
                elif "fixed" in e and _nummer(e["fixed"]) >= 0:
                    bereiche.append((anfang, _nummer(e["fixed"])))
    return bereiche, genannt


def llamacpp_treffer(details: dict, build: str) -> Optional[Luecke]:
    """Luecke, wenn `build` sicher betroffen ist – sonst None (behoben, nicht betroffen oder unklar)."""
    nr = _nummer(build)
    if nr < 0:
        return None
    bereiche, genannt = _llama_bereiche(details)
    offen = [b for a, b in bereiche if a <= nr < b]
    if bereiche and not offen:
        return None                                  # in jedem bekannten Bereich behoben
    if not offen and build not in genannt:
        return None                                  # „introduced 0“ ohne Fix und Build nicht genannt: unklar
    vid = str(details.get("id") or "?")
    cve = vid if vid.startswith("CVE-") else next(
        (a for a in details.get("aliases", []) or [] if str(a).startswith("CVE-")), "")
    roh = " ".join(str(details.get("summary") or details.get("details") or "").split())
    return Luecke(id=vid, cve=cve, schwere=_schwere(details), text=roh[:200],
                  behoben=f"b{min(offen)}" if offen else "", gguf=bool(_GGUF.search(roh)))


def aktiver_llamacpp_build() -> str:
    """Der eingeschaltete llama.cpp-Build (llamareserve.aktiv()) – "" wenn nicht ermittelbar."""
    try:
        import llamareserve
        return str(llamareserve.aktiv() or "")
    except Exception:
        return ""


def llamacpp_pruefen(build: Optional[str] = None, post: Optional[Callable] = None,
                     get: Optional[Callable] = None) -> dict:
    """{"build", "luecken": [Luecke], "unklar": Anzahl} für den aktiven Build (oder `build`).
    Gemeldet werden nur echte Treffer, GGUF-Lücken zuerst. OSError, wenn OSV nicht erreichbar ist."""
    build = build or aktiver_llamacpp_build()
    if _nummer(build) < 0:
        raise OSError(f"Kein gültiger llama.cpp-Build ({build or 'nicht ermittelbar'}).")
    post, get = _post_get(post, get)
    eintraege, token = [], ""
    for _ in range(10):                              # Seiten der Antwort (next_page_token)
        anfrage = {"package": {"ecosystem": "GIT", "name": LLAMACPP_REPO}, "version": build}
        if token:
            anfrage["page_token"] = token
        try:
            antwort = post(ABFRAGE, anfrage) or {}
        except Exception as e:
            raise OSError(f"Lücken-Datenbank (OSV.dev) nicht erreichbar: {e}") from None
        eintraege += [v for v in antwort.get("vulns", []) or [] if isinstance(v, dict)]
        token = str(antwort.get("next_page_token") or "")
        if not token:
            break
    # Die Abfrage liefert gekürzte Einträge ohne Bereiche: fehlen sie, die vollen Details nachladen
    ohne = [v["id"] for v in eintraege if v.get("id") and not v.get("affected")][:MAX_DETAILS]
    voll = _details_holen(ohne, get)
    treffer, unklar = [], 0
    for v in eintraege:
        d = voll.get(v.get("id"), v)
        t = llamacpp_treffer(d, build)
        if t is None:
            bereiche, _ = _llama_bereiche(d)
            unklar += not bereiche
            continue
        treffer.append(t)
    treffer = zusammenfassen(treffer)
    treffer.sort(key=lambda l: (not l.gguf, -_RANG.get(l.schwere, 0), l.kennung))
    return {"build": build, "luecken": treffer, "unklar": unklar}


def llamacpp_zeilen(ergebnis: dict) -> list[str]:
    """Anzeige-Zeilen für /update bzw. /luecken."""
    build, liste = ergebnis.get("build", "?"), ergebnis.get("luecken") or []
    if not liste:
        zeilen = [f"🛡 llama.cpp {build}: keine bekannten offenen Lücken."]
    else:
        zeilen = [f"🛡 llama.cpp {build}: {len(liste)} bekannte Lücke(n), schwerste: {schwerste(liste)}"]
        for l in liste:
            zeilen.append(f"   {'⚠ GGUF · ' if l.gguf else ''}{l.kennung} · {l.schwere} · "
                          + (f"behoben ab {l.behoben}" if l.behoben else "noch ohne Fix")
                          + (f" · {l.text}" if l.text else ""))
    if ergebnis.get("unklar"):
        zeilen.append(f"   ({ergebnis['unklar']} Eintrag/Einträge ohne Versionsangabe nicht gewertet)")
    return zeilen
