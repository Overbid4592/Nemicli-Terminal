"""
luecken.py - Bekannte Sicherheitslücken und Schadpakete im venv (OSV.dev).

Fragt die offene Datenbank OSV.dev (sammelt GitHub-Advisories, PyPI-Meldungen und die
OpenSSF-Liste schädlicher Pakete) nach jedem installierten Paket samt Version. Je Fund:
Kennung (CVE, wenn es eine gibt), Schwere, Kurzbeschreibung und die erste Version, die
die Lücke behebt. Einträge MAL-… sind Schadpakete – die gehören entfernt, nicht aktualisiert.
Behoben wird mit einer neueren Version; ältere Versionen haben in aller Regel mehr Lücken.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

BATCH = "https://api.osv.dev/v1/querybatch"
EINZELN = "https://api.osv.dev/v1/vulns/{}"
MAX_DETAILS = 80
_SCHWERE = {"CRITICAL": "kritisch", "HIGH": "hoch", "MODERATE": "mittel", "MEDIUM": "mittel", "LOW": "niedrig"}


@dataclass
class Luecke:
    id: str
    cve: str = ""
    schwere: str = "?"
    text: str = ""
    behoben: str = ""          # erste Version über der installierten, die sie schließt – leer: keine
    schadcode: bool = False

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


def _luecke(vid: str, details: Optional[dict], paket: str, version: str) -> Luecke:
    if not details:
        return Luecke(id=vid, schadcode=vid.startswith("MAL-"))
    cve = next((a for a in details.get("aliases", []) if a.startswith("CVE-")), "")
    schwere = _SCHWERE.get(str((details.get("database_specific") or {}).get("severity", "")).upper(), "?")
    text = " ".join(str(details.get("summary") or details.get("details") or "").split())[:200]
    return Luecke(id=vid, cve=cve, schwere=schwere, text=text, behoben=_behoben(details, paket, version),
                  schadcode=vid.startswith("MAL-"))


def _client():
    import httpx
    return httpx.Client(headers={"User-Agent": "NemiCLI-Paketpruefung"}, timeout=30.0)


def pruefen(pakete: dict[str, str], post: Optional[Callable] = None,
            get: Optional[Callable] = None) -> dict[str, list[Luecke]]:
    """{Paket: [Lücken]} für {Paket: Version}; Pakete ohne Fund fehlen im Ergebnis.
    OSError, wenn OSV nicht erreichbar ist."""
    if not pakete:
        return {}
    namen = sorted(pakete)
    client = None
    if post is None or get is None:
        client = _client()
        post = post or (lambda url, daten: client.post(url, json=daten).raise_for_status().json())
        get = get or (lambda url: client.get(url).raise_for_status().json())
    try:
        try:
            antwort = post(BATCH, {"queries": [{"package": {"name": n, "ecosystem": "PyPI"},
                                                "version": pakete[n]} for n in namen]})
        except Exception as e:
            raise OSError(f"Lücken-Datenbank (OSV.dev) nicht erreichbar: {e}") from None
        funde = {n: [v["id"] for v in (r or {}).get("vulns", []) if v.get("id")]
                 for n, r in zip(namen, antwort.get("results", []))}
        details: dict[str, dict] = {}
        for vid in sorted({v for ids in funde.values() for v in ids})[:MAX_DETAILS]:
            try:
                details[vid] = get(EINZELN.format(vid))
            except Exception:
                pass
    finally:
        if client is not None:
            client.close()
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
    return list(raus.values())


_RANG = {"kritisch": 4, "hoch": 3, "mittel": 2, "niedrig": 1, "?": 0}


def schwerste(liste: list[Luecke]) -> str:
    return max((l.schwere for l in liste), key=lambda s: _RANG.get(s, 0), default="?")
