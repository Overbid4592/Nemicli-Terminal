"""Merkmale für das KI-Modell (ML-003): Taten einer KI → Zahlenvektor.

Das Normalbild gilt je KI: welche Werkzeuge und Programme sie nutzt, in welchen Arbeitsordnern sie
arbeitet, welche Bereiche der Platte sie anfasst, welche Domains sie aufruft, zu welchen Stunden.

Gezählt wird, wie oft die KI etwas schon hatte (`selten` = 1/(1+n)), beim Lernen der Reihe nach mit
dem Stand vor der Tat, im Betrieb laufend weiter (`gesehen`). So sieht der Wald, wie oft Neues bei
einer KI normal ist – ein Agent, der täglich neue Ordner liest, fällt nicht auf, ein Programm aus
AppData bei einer KI, die sonst nur `git` startet, schon. Ja/Nein-Merkmale allein gingen unter den
übrigen Achsen unter; die stufenlose Seltenheit isoliert der Wald früh.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from .ereignisse import Ereignis
from .merkmale import _SYSTEMORDNER, _erklaeren, _zeitmerkmale, entropie

LERNBAR = ("ki_tat", "ki_prozess")              # Ereignisarten, die das Modell lernt und bewertet
ARTEN = ("befehl", "schreiben", "lesen", "loeschen", "suchen", "web", "prozess")
_ART_GLEICH = {"websuche": "web"}

KI_MERKMALE = (
    ["stunde_sin", "stunde_cos", "wochenende", "nacht"]
    + [f"art_{a}" for a in ARTEN] + ["art_andere"]
    + ["ki_neu", "werkzeug_selten", "befehl_selten", "ordner_selten", "bereich_selten", "domain_selten",
       "kombi_selten", "stunde_untypisch",
       "ort_projekt", "ort_benutzer", "ort_daten", "ort_system", "ort_fremd",
       "cmd_laenge", "cmd_entropie", "burst", "nebenagent", "von_aussen",
       # Verknüpfungen: einzeln sind „schreiben“, „System“ oder „neu“ Alltag, zusammen selten
       "schreiben_ausserhalb", "schreiben_system", "programm_ausserhalb", "neu_ausserhalb"]
)
KI_ERKLAERUNG = [
    ("schreiben_system", "schreibt oder löscht im Windows- oder Programmordner"),
    ("programm_ausserhalb", "startet ein Programm von außerhalb der installierten Software"),
    ("schreiben_ausserhalb", "schreibt oder löscht außerhalb des Arbeitsordners"),
    ("neu_ausserhalb", "neuer Bereich außerhalb des Arbeitsordners"),
    ("ki_neu", "diese KI war bisher nicht im Normalbild"),
    ("werkzeug_selten", "Werkzeug oder Programm, das diese KI bisher nie nutzte"),
    ("befehl_selten", "Befehl, den diese KI bisher nie aufrief"),
    ("ordner_selten", "Arbeitsordner, in dem diese KI bisher nie arbeitete"),
    ("bereich_selten", "Bereich der Platte, den diese KI bisher nie anfasste"),
    ("domain_selten", "Domain, die diese KI bisher nie aufrief"),
    ("kombi_selten", "diese Art Tat an diesem Ort gab es bei dieser KI noch nie"),
    ("stunde_untypisch", "für diese KI untypische Uhrzeit"),
    ("ort_system", "Ziel im Windows- oder Programmordner"),
    ("ort_daten", "Ziel in den Anwendungsdaten (AppData/ProgramData)"),
    ("ort_fremd", "Ziel außerhalb des Benutzerordners"),
    ("art_loeschen", "Löschen"),
    ("nacht", "Aktivität zur Nachtzeit"),
]
MIN_HISTOGRAMM = 100
BEREICH_TIEFE = 5                               # Bereich = die ersten Ordner des Pfads (Laufwerk zählt mit)
ZAEHLFELDER = ("werkzeuge", "befehle", "ordner", "bereiche", "domains", "kombi")
ORTE = ("projekt", "benutzer", "daten", "system", "fremd")


def _norm(pfad: str) -> str:
    return str(pfad or "").strip().strip('"').replace("/", "\\").rstrip("\\").lower()


def _unter(pfad: str, ordner: str) -> bool:
    return bool(ordner) and (pfad == ordner or pfad.startswith(ordner + "\\"))


def _orte() -> tuple[str, tuple[str, ...]]:
    heim = _norm(os.path.expanduser("~"))
    daten = tuple(p for p in (_norm(os.environ.get(v, "")) for v in ("APPDATA", "LOCALAPPDATA", "ProgramData")) if p)
    return heim, daten


def ki_name(e: Ereignis) -> str:
    return str((e.extra or {}).get("ki") or e.prozess or "")


def werkzeug(e: Ereignis) -> str:
    """Werkzeug einer Tat oder Programm, das die KI startete."""
    if e.aktion == "ki_prozess":
        return "programm:" + (e.prozess or "?").lower()
    return str((e.extra or {}).get("werkzeug") or e.aktion)


_ERSTES = re.compile(r"""^[&.\s(]*(?:"([^"]+)"|'([^']+)'|(\S+))""")
_WORT = re.compile(r"[a-z][a-z0-9_\-]*(?:\.(?:py|ps1|cmd|bat|sh))?")
_PROGRAMM = (".exe", ".cmd", ".bat", ".ps1", ".py", ".sh")
_SCHLUESSELWOERTER = frozenset({"function", "foreach", "for", "if", "while", "do", "try", "param", "switch",
                                "begin", "filter", "trap", "then", "set", "export", "cd"})


def befehl(e: Ereignis) -> str:
    """Erstes Wort eines Befehls der KI (ohne Pfad und .exe), z. B. git, python, curl. Programmstarts
    zählen über `werkzeug`; Zuweisungen, Ausdrücke und Sprachwörter ($x = …, foreach, function) haben
    kein Befehlswort, ein Pfad in Anführungszeichen nur, wenn er auf ein Programm zeigt."""
    if e.aktion == "ki_prozess" or not e.cmdline:
        return ""
    m = _ERSTES.match(e.cmdline)
    if not m:
        return ""
    roh = m.group(1) or m.group(2)
    if roh is not None and not roh.lower().endswith(_PROGRAMM):
        return ""
    wort = os.path.basename((roh or m.group(3)).replace("/", "\\")).lower()
    wort = wort[:-4] if wort.endswith(".exe") else wort
    return wort if _WORT.fullmatch(wort) and wort not in _SCHLUESSELWOERTER else ""


def ziel_pfad(e: Ereignis) -> str:
    if e.aktion == "ki_prozess":
        return _norm(e.exe)
    return _norm((e.datei or "").split(", ")[0])


def bereich(e: Ereignis) -> str:
    """Grobe Lage des Ziels: im Arbeitsordner der erste Unterordner, sonst die ersten Ordner des Pfads."""
    p = ziel_pfad(e)
    if not p:
        return ""
    ordner = os.path.dirname(p) if "." in os.path.basename(p) else p
    cwd = _norm((e.extra or {}).get("ordner", ""))
    if _unter(ordner, cwd):
        rest = ordner[len(cwd):].strip("\\")
        return "projekt:" + (rest.split("\\")[0] if rest else "")
    return "\\".join(ordner.split("\\")[:BEREICH_TIEFE])


def domain(e: Ereignis) -> str:
    if not e.ziel:
        return ""
    try:
        host = urlsplit(e.ziel if "://" in e.ziel else "https://" + e.ziel).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def art(e: Ereignis) -> str:
    if e.aktion == "ki_prozess":
        return "prozess"
    a = str((e.extra or {}).get("art") or "")
    return _ART_GLEICH.get(a, a)


def ort(e: Ereignis) -> str:
    """Lage des Ziels: projekt (Arbeitsordner), benutzer, daten (AppData/ProgramData), system, fremd – ohne Ziel ""."""
    p = ziel_pfad(e)
    if not p:
        return ""
    if _unter(p, _norm((e.extra or {}).get("ordner", ""))):
        return "projekt"
    heim, daten = _orte()
    if any(_unter(p, d) for d in daten):
        return "daten"
    if any(_unter(p, s.rstrip("\\")) for s in _SYSTEMORDNER):
        return "system"
    return "benutzer" if _unter(p, heim) else "fremd"


def subjekt(e: Ereignis) -> str:
    """Schlüssel für Verdichten und Dämpfen von ML-003: KI + Werkzeug (+ Bereich)."""
    teile = [ki_name(e) or "KI", werkzeug(e)]
    b = bereich(e) or domain(e) or befehl(e)
    if b:
        teile.append(b)
    return " · ".join(teile)


class KiMerkmale:
    namen = KI_MERKMALE
    kaltstart: dict = {}                        # vor dem ersten Training bewertet das Modell nicht

    def __init__(self):
        # KI → {Feld: {Wert: Anzahl}} für werkzeuge, befehle, ordner, bereiche, domains, kombi; stunden: [24]
        self.bild: dict[str, dict] = {}
        # Seit dem Training gesehen: gezählt wird wie beim Lernen der Reihe nach – das zweite Mal im neuen
        # Ordner ist nicht mehr „nie“. Nicht gespeichert; das nächste Training lernt aus den gespeicherten Taten.
        self.live: dict[str, dict] = {}
        self._lern: dict[str, list[float]] = {}

    # ------------------------------------------------------------- Normalbild

    @staticmethod
    def _leer() -> dict:
        return {**{f: {} for f in ZAEHLFELDER}, "stunden": [0] * 24}

    def anpassen(self, ereignisse: list[Ereignis]) -> None:
        """Normalbild der Reihe nach aufbauen; die Lernvektoren entstehen dabei mit dem Stand VOR dem Ereignis."""
        self.bild, self.live, self._lern = {}, {}, {}
        for e in sorted(ereignisse, key=lambda x: x.zeit):
            self._lern[e.id] = self._vektor(e)
            self._merken(e, self.bild)
            self.bild[ki_name(e)]["stunden"][_zeitmerkmale(e.zeit)[1].hour] += 1

    def gesehen(self, e: Ereignis) -> None:
        """Nach dem Bewerten: die Tat zählt zum Bild dieser KI (bis zum nächsten Training)."""
        self._merken(e, self.live)

    @staticmethod
    def _werte(e: Ereignis) -> dict[str, str]:
        return {"werkzeuge": werkzeug(e), "befehle": befehl(e), "ordner": _norm((e.extra or {}).get("ordner", "")),
                "bereiche": bereich(e), "domains": domain(e), "kombi": f"{art(e)}|{ort(e)}"}

    def _merken(self, e: Ereignis, ziel: dict) -> None:
        b = ziel.setdefault(ki_name(e), self._leer())
        for feld, wert in self._werte(e).items():
            if wert:
                b[feld][wert] = b[feld].get(wert, 0) + 1

    def zustand(self) -> dict:
        return self.bild

    def zustand_laden(self, z: dict) -> None:
        self.bild = {}
        for ki, b in (z or {}).items():
            neu = self._leer()
            for k in ZAEHLFELDER:
                w = b.get(k) or {}
                neu[k] = {str(x): int(n) for x, n in w.items()} if isinstance(w, dict) else {str(x): 1 for x in w}
            neu["stunden"] = (list(b.get("stunden") or []) + [0] * 24)[:24]
            self.bild[ki] = neu
        self.live, self._lern = {}, {}

    # ---------------------------------------------------------------- Vektor

    def vektor(self, e: Ereignis) -> list[float]:
        v = self._lern.pop(e.id, None)
        return v if v is not None else self._vektor(e)

    def _vektor(self, e: Ereignis) -> list[float]:
        zeit, dt = _zeitmerkmale(e.zeit)
        x = e.extra or {}
        b = self.bild.get(ki_name(e))
        l = self.live.get(ki_name(e))
        a = art(e)
        werte = self._werte(e)

        def selten(feld: str) -> float:
            """1 = bei dieser KI noch nie, 1/2 = einmal, 1/11 = zehnmal – ohne Bild der KI 0."""
            wert = werte[feld]
            if not wert or (b is None and l is None):
                return 0.0
            n = (b[feld].get(wert, 0) if b is not None else 0) + (l[feld].get(wert, 0) if l is not None else 0)
            return 1.0 / (1.0 + n)

        o = ort(e)
        orte = [1.0 if o == k else 0.0 for k in ORTE]
        projekt, system = orte[0], orte[3]
        cmd = e.cmdline or ""
        bereich_selten = selten("bereiche")
        schreibt = 1.0 if a in ("schreiben", "loeschen") else 0.0
        return zeit + [1.0 if a == k else 0.0 for k in ARTEN] + [0.0 if a in ARTEN else 1.0] + [
            1.0 if (b is None and l is None) else 0.0,
            selten("werkzeuge"), selten("befehle"), selten("ordner"), bereich_selten, selten("domains"),
            selten("kombi"), self._stunde_untypisch(b, dt.hour),
        ] + orte + [
            min(len(cmd) / 500.0, 1.0), entropie(cmd[:200]), float(x.get("burst", 0.0)),
            1.0 if x.get("nebenagent") else 0.0, 1.0 if x.get("quelle") == "wache" else 0.0,
            schreibt * (1.0 - projekt) if o else 0.0, schreibt * system,
            1.0 if (a == "prozess" and o and not system) else 0.0,
            bereich_selten * (1.0 - projekt),
        ]

    @staticmethod
    def _stunde_untypisch(b, stunde: int) -> float:
        if not b:
            return 0.0
        hist = b["stunden"]
        gesamt = sum(hist)
        if gesamt < MIN_HISTOGRAMM:
            return 0.0
        anteil = (hist[stunde] + 1.0) / (gesamt + 24.0)          # Laplace
        return float(min(max(1.0 - anteil * 24.0, 0.0), 1.0))

    def erklaeren(self, vektor: list[float]) -> list[str]:
        return _erklaeren(KI_ERKLAERUNG, self.namen, vektor)
