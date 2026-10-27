"""Netzwächter: Verbindungen nach draußen mit ML und DL bewerten.

Grundlage sind die `conn_open`-Ereignisse des Netz-Sensors. Beim Aufnehmen stempelt der Motor
den Namen des Ziels (`extra.name`, aus netzauskunft) und die Zahl verschiedener Ziele des Programms
in der letzten Minute (`extra.ziele_min`) – so sehen Training und Bewertung dieselben Werte.

  ML  eigener Isolation Forest (Detektor mit NetzMerkmale): ein einzelnes Ziel – neu für dieses
      Programm, seltene Domain, ohne Namen direkt an eine Adresse, zufällig wirkender Name,
      ungewöhnliche Uhrzeit oder Port, viele verschiedene Ziele in kurzer Zeit.
  DL  GRU auf der Abfolge je Programm (NetzFolgen): welche Domains in welcher Reihenfolge und in
      welchem Takt („T:“-Zeichen für den Abstand) – fällt auf, wenn ein Programm plötzlich anders
      spricht oder gleichmäßig wie ein Uhrwerk funkt.

Beide beginnen im Schattenmodus (`netz_alarm` aus): Funde stehen in Wache/modelle/netz_schatten.log,
es gibt keinen Alarm und keinen Weckruf; scharf schaltet nur der Nutzer (/wache netz warnen).
Gelernt wird wie beim Rest der Wache nur aus Ereignissen nach der Karenzzeit und ohne offene Alarme.
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .ereignisse import NETZ, PROZESS, Ereignis
from .folgen import (KOPF, KOPF_WEITER, FolgenDetektor, Schritt, Vokabular, schluessel)
from .merkmale import _zeitmerkmale, entropie, seltenheit
from .netzauskunft import registriert

PORTS_AUFFAELLIG = {22, 23, 445, 1433, 3306, 3389, 4444, 5432, 5900, 6667, 9001, 9050}
FENSTER = 64
SCHATTEN = "netz_schatten.log"

NETZ_MERKMALE = [
    "stunde_sin", "stunde_cos", "wochenende", "nacht",
    "extern", "port_web", "port_dns", "port_hoch", "port_tief", "port_auffaellig",
    "ohne_name", "name_entropie", "name_laenge", "name_ziffern",
    "domain_seltenheit", "domain_neu", "domain_neu_fuer_prozess", "ziel_neu_fuer_prozess",
    "prozess_seltenheit", "prozess_neu", "ziele_pro_minute", "burst",
]
NETZ_KALTSTART = {"domain_neu_fuer_prozess": 0.2, "ohne_name": 0.2, "port_auffaellig": 0.25,
                  "name_entropie": 0.15, "ziele_pro_minute": 0.1, "prozess_neu": 0.1}
NETZ_ERKLAERUNG = [
    ("domain_neu_fuer_prozess", "dieses Programm sprach diese Domain bisher nie an"),
    ("domain_neu", "Domain bisher unbekannt"), ("ziel_neu_fuer_prozess", "neue Adresse für dieses Programm"),
    ("ohne_name", "Verbindung direkt an eine Adresse, ohne Namen"),
    ("name_entropie", "zufällig wirkender Name"), ("port_auffaellig", "auffälliger Zielport"),
    ("ziele_pro_minute", "viele verschiedene Ziele in kurzer Zeit"), ("nacht", "Verbindung zur Nachtzeit"),
    ("prozess_neu", "Programm hat bisher nie nach draußen gesprochen"),
    ("burst", "auffällig viele Verbindungen in kurzer Zeit"),
]


def domain_von(e: Ereignis) -> str:
    """Wofür das Ziel steht: registrierte Domain, sonst „ip“ (öffentlich, ohne Namen) oder „intern“."""
    x = e.extra or {}
    name = str(x.get("name") or "")
    if name:
        return registriert(name)
    return "ip" if x.get("extern") else "intern"


def _prozess(e: Ereignis) -> str:
    return (e.prozess or "?").lower()


class NetzMerkmale:
    namen = NETZ_MERKMALE
    kaltstart = NETZ_KALTSTART

    def __init__(self):
        self.domains: dict[str, int] = {}
        self.prozesse: dict[str, int] = {}
        self.paare: set[str] = set()          # prozess|domain
        self.ziele: set[str] = set()          # prozess|ip

    def anpassen(self, ereignisse: list[Ereignis]) -> None:
        self.domains, self.prozesse, self.paare, self.ziele = {}, {}, set(), set()
        for e in ereignisse:
            d, p = domain_von(e), _prozess(e)
            self.domains[d] = self.domains.get(d, 0) + 1
            self.prozesse[p] = self.prozesse.get(p, 0) + 1
            self.paare.add(f"{p}|{d}")
            self.ziele.add(f"{p}|{e.ziel}")

    def zustand(self) -> dict:
        return {"domains": self.domains, "prozesse": self.prozesse, "paare": sorted(self.paare),
                "ziele": sorted(self.ziele)[-50_000:]}

    def zustand_laden(self, z: dict) -> None:
        self.domains = dict(z.get("domains") or {})
        self.prozesse = dict(z.get("prozesse") or {})
        self.paare = set(z.get("paare") or [])
        self.ziele = set(z.get("ziele") or [])

    def vektor(self, e: Ereignis) -> list[float]:
        zeit, _ = _zeitmerkmale(e.zeit)
        x = e.extra or {}
        name = str(x.get("name") or "")
        links = name.split(".")[0] if name else ""
        d, p = domain_von(e), _prozess(e)
        port = int(e.zielport or 0)
        trainiert = bool(self.domains)
        nd, np_ = self.domains.get(d, 0), self.prozesse.get(p, 0)
        return zeit + [
            1.0 if x.get("extern") else 0.0,
            1.0 if port in (80, 443) else 0.0, 1.0 if port == 53 else 0.0,
            1.0 if port >= 1024 else 0.0, 1.0 if 0 < port < 1024 and port not in (53, 80, 443) else 0.0,
            1.0 if port in PORTS_AUFFAELLIG else 0.0,
            1.0 if (x.get("extern") and not name) else 0.0,
            entropie(links), min(len(name) / 60.0, 1.0),
            (sum(ch.isdigit() for ch in links) / len(links)) if links else 0.0,
            seltenheit(nd), 1.0 if (trainiert and nd == 0) else 0.0,
            1.0 if (trainiert and f"{p}|{d}" not in self.paare) else 0.0,
            1.0 if (trainiert and f"{p}|{e.ziel}" not in self.ziele) else 0.0,
            seltenheit(np_), 1.0 if (trainiert and np_ == 0) else 0.0,
            min(float(x.get("ziele_min", 0)) / 30.0, 1.0), float(x.get("burst", 0.0)),
        ]

    def erklaeren(self, vektor: list[float]) -> list[str]:
        idx = {n: i for i, n in enumerate(self.namen)}
        gefunden = sorted(((vektor[idx[k]], t) for k, t in NETZ_ERKLAERUNG if vektor[idx[k]] > 0.5), reverse=True)
        return [t for _, t in gefunden[:4]]


def takt(sekunden: float) -> str:
    """Abstand zur vorigen Verbindung desselben Programms als Zeichen."""
    for grenze, zeichen in ((1, "T:0"), (10, "T:1"), (60, "T:2"), (300, "T:3"), (1800, "T:4")):
        if sekunden < grenze:
            return zeichen
    return "T:5"


class NetzKetten:
    """Wie folgen.Kettenbuch, aber für Verbindungen: eine Kette je Programm-Instanz (PID + Start),
    ohne Startzeit je Programmname. Zeichen: ^ P:<programm>, dann je Verbindung T:<abstand> D:<domain>."""

    def __init__(self, fenster: int = FENSTER, max_ketten: int = 5000):
        self.fenster = fenster
        self.max_ketten = max_ketten
        self._k: dict[tuple, dict] = {}

    def __len__(self) -> int:
        return len(self._k)

    def name(self, kette: tuple) -> str:
        k = self._k.get(kette)
        return k["name"] if k else ""

    def schritte(self, e: Ereignis) -> list[Schritt]:
        if e.kategorie == PROZESS and e.aktion == "process_stop":
            kette = schluessel(e.pid, e.start)
            if kette is not None and self._k.pop(kette, None) is not None:
                return [Schritt(kette, ende=True)]
            return []
        if e.kategorie != NETZ or e.aktion != "conn_open":
            return []
        kette = schluessel(e.pid, e.start) or ("prozess", _prozess(e))
        ziel = "D:" + domain_von(e)
        k = self._k.get(kette)
        out: list[Schritt] = []
        if k is None:
            self._k[kette] = {"name": _prozess(e), "zeit": e.zeit, "laenge": 3}
            while len(self._k) > self.max_ketten:
                alt = next(iter(self._k))
                del self._k[alt]
                out.append(Schritt(alt, ende=True))
            out.append(Schritt(kette, [KOPF, "P:" + _prozess(e), ziel], [False, False, True], neu=True))
            return out
        abstand = takt(max(0.0, e.zeit - k["zeit"]))
        k["zeit"] = e.zeit
        if k["laenge"] + 2 > self.fenster:
            k["laenge"] = 4
            return [Schritt(kette, [KOPF_WEITER, "P:" + k["name"], abstand, ziel],
                            [False, False, True, True], neu=True)]
        k["laenge"] += 2
        return [Schritt(kette, [abstand, ziel], [True, True])]


class NetzVokabular(Vokabular):
    SPEZIAL = ("<leer>", KOPF, "^?", KOPF_WEITER, "P:~", "D:~", "?")
    NAMEN = ("P:", "D:")


class NetzFolgen(FolgenDetektor):
    KETTENBUCH = NetzKetten
    VOKABULAR = NetzVokabular
    SCHATTEN = SCHATTEN

    def __init__(self, ordner: Path, *, schwelle: float = 0.995):
        super().__init__(ordner, schwelle=schwelle, fenster=FENSTER)


# ---------------------------------------------------------------- Ziele pro Minute

class ZieleProMinute:
    """Verschiedene Ziele je Programm in den letzten 60 s – beim Aufnehmen gestempelt."""

    def __init__(self, fenster: float = 60.0):
        self.fenster = fenster
        self._k: dict[str, deque] = {}

    def beobachten(self, prozess: str, ziel: str, ts: float) -> int:
        if not prozess:
            return 0
        w = self._k.setdefault(prozess.lower(), deque())
        w.append((ts, ziel))
        while w and w[0][0] < ts - self.fenster:
            w.popleft()
        if len(self._k) > 1000:
            self._k.clear()
        return len({z for _, z in w})


# ---------------------------------------------------------------- Schatten-Protokoll

class SchattenLog:
    """Funde ohne Alarm: dasselbe Programm mit derselben Domain höchstens einmal je Stunde."""

    def __init__(self, pfad: Path):
        self.pfad = Path(pfad)
        self._zuletzt: dict[tuple, float] = {}

    def melden(self, e: Ereignis, ml: float, dl: float, gruende: list[str], schritt: str = "") -> bool:
        key = (_prozess(e), domain_von(e))
        jetzt = time.time()
        if jetzt - self._zuletzt.get(key, 0.0) < 3600:
            return False
        self._zuletzt[key] = jetzt
        if len(self._zuletzt) > 2000:
            self._zuletzt.clear()
        name = (e.extra or {}).get("name") or ""
        try:
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            if self.pfad.exists() and self.pfad.stat().st_size > 2_000_000:
                os.replace(self.pfad, self.pfad.with_suffix(".log.1"))
            with open(self.pfad, "a", encoding="utf-8") as f:
                f.write(f"{datetime.fromtimestamp(e.zeit).strftime('%Y-%m-%d %H:%M:%S')} | ML {ml:.3f} · DL {dl:.3f} | "
                        f"{e.prozess or '?'} → {name or e.ziel} ({e.ziel}:{e.zielport}) | "
                        f"{'; '.join(gruende) or '-'}{(' | ' + schritt) if schritt else ''}\n")
        except OSError:
            return False
        return True

    def lesen(self, anzahl: int = 20) -> list[str]:
        try:
            return self.pfad.read_text(encoding="utf-8").splitlines()[-anzahl:]
        except OSError:
            return []


# ---------------------------------------------------------------- Übersicht

def uebersicht(verbindungen: list[dict], besitzer=None, n: int = 25) -> list[dict]:
    """Verbindungen (Speicher.netz_verbindungen) je Programm und Ziel zusammengefasst, neueste zuerst."""
    gruppen: dict[tuple, dict] = {}
    for v in verbindungen:
        x = v.get("extra") or {}
        key = ((v.get("prozess") or "?").lower(), v.get("ziel") or "")
        g = gruppen.get(key)
        if g is None:
            g = gruppen[key] = {"prozess": v.get("prozess") or "?", "ziel": v.get("ziel") or "",
                                "port": v.get("port") or 0, "name": "", "anzahl": 0, "zuletzt": 0.0,
                                "erstmals": v["zeit"], "ml": 0.0, "dl": 0.0, "extern": bool(x.get("extern"))}
        g["anzahl"] += 1
        g["zuletzt"] = max(g["zuletzt"], v["zeit"])
        g["erstmals"] = min(g["erstmals"], v["zeit"])
        g["name"] = g["name"] or str(x.get("name") or "")
        g["ml"] = max(g["ml"], float(x.get("netz_ml") or 0.0))
        g["dl"] = max(g["dl"], float(x.get("netz_dl") or 0.0))
    liste = sorted((g for g in gruppen.values() if g["extern"]), key=lambda g: -g["zuletzt"])[:n]
    for g in liste:
        b = besitzer(g["ziel"]) if besitzer else None
        g["besitzer"] = (b or {}).get("org", "") if b else ""
    return liste


def ampel(ml: float, dl: float, schwelle_ml: float, schwelle_dl: float) -> str:
    if ml >= schwelle_ml or dl >= schwelle_dl:
        return "🔴"
    if ml >= schwelle_ml - 0.05 or dl >= schwelle_dl - 0.01:
        return "🟡"
    return "🟢"


def score_text(score: float) -> str:
    return f"{score:.2f}" if score and not math.isnan(score) else "–"
