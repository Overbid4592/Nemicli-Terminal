"""Debugbericht: zeichnet auf, was ein NemiCLI-Prozess mit dem Grafikspeicher macht –
Modelle laden/entladen/auslagern, Stimme, Bildbeschreiber, Embedding, Bildmotor, Runden,
Werkzeuge, Modellwechsel und jeden Fehler (auch aus Threads und Hintergrund-Aufgaben).
Dazu alle 10 s der Stand des Grafikspeichers, je Prozess (Windows-Leistungszähler über pdh.dll).

Eine Datei je Prozess und Start: Debugbericht/<Datum>_<Rolle>_<PID>.log im Datenordner. Läuft immer
(Konfiguration `debug` false schaltet ab; dann kosten alle Aufrufe nichts); die BEHALTEN neuesten Dateien
bleiben. Das Motor-Log daneben (Prompts, Graphen) nur mit `debug_motor`. Die KI liest den Bericht über das
Werkzeug `debugbericht` (nur Cloud-Modelle) und schreibt ihre Auswertung als Markdown nach Berichte/.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

ORDNER_NAME = "Debugbericht"
LESEN_MAX = 14_000                # Zeichen, die das Werkzeug `debugbericht` der KI gibt
BEHALTEN = 30                     # so viele Berichte bleiben im Ordner (die neuesten)
TAKT_BIS_MB = 8                   # ab dieser Dateigröße hört der Takt auf (Ereignisse laufen weiter)
SICHTEN = ("kurz", "fehler", "speicher", "alles")
LOKAL = ("", "gguf", "ollama")    # Anbieter, deren Modelle den Bericht nicht lesen (zu ungenau in Logs)
TAKT_S = 10                       # Abstand der Speicher-Zeilen
PROZESSE_ALLE_S = 60              # Abstand der Prozess-Tabelle, wenn sich nichts bewegt
BEWEGUNG_MB = 100                 # Prozess-Tabelle sofort, wenn ein Prozess so viel gewinnt/verliert

_lock = threading.Lock()
_datei: Path | None = None
_rolle = ""
_t0 = 0.0
_takt: threading.Thread | None = None
_zuletzt_prozesse: dict[int, int] = {}
_zuletzt_tabelle = 0.0


# ---------------------------------------------------------------------------
# Schalter
# ---------------------------------------------------------------------------

def an() -> bool:
    """Läuft immer – außer `debug` steht in der Konfiguration auf false. --debug erzwingt an."""
    if "--debug" in sys.argv:
        return True
    try:
        import config
        return bool(config.load().get("debug", True))
    except Exception:
        return True


def motor_log_an() -> bool:
    """Das Motor-Log (jeder Prompt, jede Antwort) nur auf Wunsch: Konfiguration `debug_motor`."""
    try:
        import config
        return bool(config.load().get("debug_motor"))
    except Exception:
        return False


def setzen(wert: bool) -> None:
    import config
    config.update(debug=bool(wert))


def laeuft() -> bool:
    return _datei is not None


def datei() -> Path | None:
    return _datei


def ordner() -> Path:
    import paths
    return paths.DATEN / ORDNER_NAME


# ---------------------------------------------------------------------------
# Werkzeug für die KI (Entwicklung): den Bericht lesen – nur Cloud-Modelle
# ---------------------------------------------------------------------------

def cloud_modell() -> bool:
    """Läuft das aktive Modell in der Cloud? Nur dann liest die KI den Bericht: kleine lokale Modelle
    (4B–12B) halluzinieren in Logs zu viel, die Auswertung würde falsch."""
    try:
        import config
        import models
        ref = str(config.load().get("model") or "")
        return models.split_ref(ref)[0] not in LOKAL
    except Exception:
        return False


def werkzeug_erlaubt() -> bool:
    return an() and cloud_modell()


def neueste(rollen: tuple[str, ...] = ("haupt", "wache"), ordner_pfad: Path | None = None) -> list[Path]:
    """Je Rolle die zuletzt geänderte Datei."""
    basis = ordner_pfad or ordner()
    aus = []
    for rolle in rollen:
        try:
            kandidaten = sorted(basis.glob(f"*_{rolle}_*.log"), key=lambda p: p.stat().st_mtime)
        except OSError:
            kandidaten = []
        if kandidaten:
            aus.append(kandidaten[-1])
    return aus


_TAKT = "  Takt   ["
_TABELLE = "Prozesse mit Grafikspeicher"


def verdichten(text: str, grenze: int = LESEN_MAX, tabelle_zeilen: int = 4) -> str:
    """Für die KI eindampfen: gleiche Takt-Zeilen hintereinander werden eine Spanne, Prozess-Tabellen
    behalten nur die größten Einträge; ist es noch zu lang, bleiben Anfang und (vor allem) Ende."""
    aus: list[str] = []
    takt_von = takt_bis = takt_stand = None
    takt_n = 0
    in_tabelle, tabelle_n = False, 0

    def takt_schliessen() -> None:
        nonlocal takt_von, takt_bis, takt_stand, takt_n
        if takt_stand is None:
            return
        if takt_n == 1:
            aus.append(f"{takt_von}  Takt   [{takt_stand}")
        else:
            aus.append(f"{takt_von[:12]} – {takt_bis[:12]}  Takt ×{takt_n} unverändert   [{takt_stand}")
        takt_von = takt_bis = takt_stand = None
        takt_n = 0

    for zeile in text.splitlines():
        if _TAKT in zeile:
            zeit, _, stand = zeile.partition(_TAKT)
            if stand == takt_stand:
                takt_bis, takt_n = zeit, takt_n + 1
            else:
                takt_schliessen()
                takt_von = takt_bis = zeit
                takt_stand, takt_n = stand, 1
            in_tabelle = False
            continue
        takt_schliessen()
        if _TABELLE in zeile:
            in_tabelle, tabelle_n = True, 0
            aus.append(zeile)
            continue
        if in_tabelle and zeile.startswith(" " * 20):
            tabelle_n += 1
            if tabelle_n <= tabelle_zeilen:
                aus.append(zeile)
            elif tabelle_n == tabelle_zeilen + 1:
                aus.append(" " * 24 + "… (kleinere Prozesse weggelassen)")
            continue
        in_tabelle = False
        aus.append(zeile)
    takt_schliessen()
    ergebnis = "\n".join(aus)
    if len(ergebnis) > grenze:
        kopf, schwanz = int(grenze * 0.35), int(grenze * 0.6)
        ergebnis = (ergebnis[:kopf].rsplit("\n", 1)[0] + "\n\n… [Mitte gekürzt – Anfang und das jüngste Ende "
                    "stehen oben und unten] …\n\n" + ergebnis[-schwanz:].split("\n", 1)[-1])
    return ergebnis


def _sicht(text: str, was: str, ab: int, grenze: int) -> str:
    """Eine Sicht auf eine Datei: kurz (verdichtet), fehler (nur FEHLER-Blöcke und ⚠), speicher (Laden/
    Entladen/Auslagern und Prozess-Tabellen, Takte als Spannen), alles (die Rohdatei ab Zeile `ab`)."""
    zeilen = text.splitlines()
    if was == "alles":
        rest = zeilen[max(0, ab):]
        aus, laenge = [], 0
        for z in rest:
            if laenge + len(z) + 1 > grenze:
                break
            aus.append(z)
            laenge += len(z) + 1
        weiter = ab + len(aus)
        schluss = (f"\n… weiter ab Zeile {weiter} (Feld ab={weiter})" if weiter < len(zeilen)
                   else "\n(Ende der Datei)")
        return "\n".join(aus) + schluss
    if was == "fehler":
        aus, drin = [], False
        for z in zeilen:
            if "  FEHLER " in z or "⚠" in z:
                aus.append(z)
                drin = "  FEHLER " in z or "⚠ BEFUND" in z
            elif drin and z.startswith("    "):
                aus.append(z)
            else:
                drin = False
        return "\n".join(aus) if aus else "(keine Fehler im Bericht)"
    if was == "speicher":
        merk = ("geladen", "entladen", "freigegeben", "in den RAM", "auf die GPU", "Bildmotor", "Modell laden",
                "Takt", _TABELLE, "Doppelpuffer", "Zwischenspeicher", "Modellwechsel")
        aus, in_tabelle = [], False
        for z in zeilen:
            if z.startswith(" " * 20):
                if in_tabelle:
                    aus.append(z)
                continue
            in_tabelle = _TABELLE in z
            if any(m in z for m in merk):
                aus.append(z)
        return verdichten("\n".join(aus), grenze, tabelle_zeilen=6)
    return verdichten(text, grenze)


def fuer_ki(grenze: int = LESEN_MAX, ordner_pfad: Path | None = None, was: str = "kurz", ab: int = 0,
            rolle: str | None = None) -> str:
    """Was das Werkzeug der KI gibt: je Rolle (haupt, wache – oder nur `rolle`) die neueste Datei in der
    gewünschten Sicht. Leer, wenn es noch keinen Bericht gibt."""
    was = was if was in SICHTEN else "kurz"
    rollen = (rolle,) if rolle in ("haupt", "wache") else ("haupt", "wache")
    dateien = neueste(rollen, ordner_pfad=ordner_pfad)
    if not dateien:
        return ""
    je = max(2000, grenze // len(dateien))
    teile = []
    for p in dateien:
        try:
            inhalt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        teile.append(f"=== {p.name} ({len(inhalt.splitlines())} Zeilen, geändert "
                     f"{datetime.fromtimestamp(p.stat().st_mtime):%H:%M:%S}, Sicht {was}) ===\n"
                     + _sicht(inhalt, was, ab, je))
    return "\n\n".join(teile)


# ---------------------------------------------------------------------------
# Grafikspeicher
# ---------------------------------------------------------------------------

def torch_speicher() -> dict | None:
    """belegt/reserviert (torch) und frei/gesamt (Treiber) in MB – None ohne CUDA."""
    t = sys.modules.get("torch")
    if t is None or not t.cuda.is_available() or not t.cuda.is_initialized():
        return None
    try:
        frei, gesamt = t.cuda.mem_get_info()
        mb = 2 ** 20
        return {"belegt": t.cuda.memory_allocated() // mb, "reserviert": t.cuda.memory_reserved() // mb,
                "frei": frei // mb, "gesamt": gesamt // mb}
    except Exception:
        return None


class _Zaehler:
    """\\GPU Process Memory(*)\\Dedicated Usage über pdh.dll – VRAM je Prozess, auch unter WDDM."""
    MEHR_DATEN = 0x800007D2
    FMT_LARGE = 0x00000400

    class _Wert(ctypes.Structure):                # PDH_FMT_COUNTERVALUE (Status + größter Zweig)
        _fields_ = [("status", ctypes.c_uint32), ("wert", ctypes.c_longlong)]

    def __init__(self) -> None:
        self.pdh = ctypes.WinDLL("pdh") if os.name == "nt" else None
        self.abfrage = ctypes.c_void_p()
        self.zaehler = ctypes.c_void_p()
        self.bereit = False
        if self.pdh is None:
            return
        for f in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData",
                  "PdhGetFormattedCounterArrayW", "PdhCloseQuery"):
            getattr(self.pdh, f).restype = ctypes.c_uint32        # PDH_STATUS ist vorzeichenlos
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.abfrage)) != 0:
            return
        pfad = "\\GPU Process Memory(*)\\Dedicated Usage"
        if self.pdh.PdhAddEnglishCounterW(self.abfrage, pfad, 0, ctypes.byref(self.zaehler)) != 0:
            self.pdh.PdhCloseQuery(self.abfrage)
            return
        self.bereit = True

    def je_prozess(self) -> dict[int, int]:
        """PID → Bytes. Leer, wenn der Zähler nicht verfügbar ist."""
        if not self.bereit:
            return {}
        if self.pdh.PdhCollectQueryData(self.abfrage) != 0:
            return {}

        class Eintrag(ctypes.Structure):
            _fields_ = [("name", ctypes.c_wchar_p), ("wert", _Zaehler._Wert)]
        groesse, anzahl = ctypes.c_uint32(0), ctypes.c_uint32(0)
        rc = self.pdh.PdhGetFormattedCounterArrayW(self.zaehler, self.FMT_LARGE, ctypes.byref(groesse),
                                                    ctypes.byref(anzahl), None)
        if rc != self.MEHR_DATEN or not groesse.value:
            return {}
        puffer = ctypes.create_string_buffer(groesse.value)
        rc = self.pdh.PdhGetFormattedCounterArrayW(self.zaehler, self.FMT_LARGE, ctypes.byref(groesse),
                                                    ctypes.byref(anzahl), puffer)
        if rc != 0:
            return {}
        eintraege = ctypes.cast(puffer, ctypes.POINTER(Eintrag * anzahl.value)).contents
        summen: dict[int, int] = {}
        for e in eintraege:
            name = e.name or ""
            if not name.startswith("pid_") or e.wert.wert <= 0:
                continue
            try:
                pid = int(name.split("_")[1])
            except (IndexError, ValueError):
                continue
            summen[pid] = summen.get(pid, 0) + int(e.wert.wert)
        return summen

    def schliessen(self) -> None:
        if self.bereit:
            self.pdh.PdhCloseQuery(self.abfrage)
            self.bereit = False


_zaehler: _Zaehler | None = None


def prozesse() -> list[tuple[str, int, int]]:
    """[(Name, PID, MB)] aller Prozesse mit Grafikspeicher, größte zuerst."""
    global _zaehler
    try:
        if _zaehler is None:
            _zaehler = _Zaehler()
        werte = _zaehler.je_prozess()
    except Exception:
        return []
    aus = []
    for pid, b in werte.items():
        aus.append((_prozessname(pid), pid, b // 2 ** 20))
    return sorted(aus, key=lambda x: -x[2])


def _prozessname(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def _zeit() -> str:
    return f"{datetime.now():%H:%M:%S.%f}"[:-3] + f" +{time.time() - _t0:8.1f}s"


def _schreiben(zeile: str) -> None:
    if _datei is None:
        return
    with _lock:
        try:
            with open(_datei, "a", encoding="utf-8", errors="replace") as f:
                f.write(zeile.rstrip("\n") + "\n")
        except OSError:
            pass


def _speicher_kurz() -> str:
    s = torch_speicher()
    if s is None:
        return "VRAM: torch nicht aktiv"
    return (f"VRAM torch belegt {s['belegt']:,} MB, reserviert {s['reserviert']:,} MB | "
            f"frei {s['frei']:,} / {s['gesamt']:,} MB").replace(",", ".")


def ereignis(text: str, speicher: bool = True) -> None:
    """Eine Zeile: Zeit, Text und (Vorgabe) der Speicherstand."""
    if _datei is None:
        return
    zusatz = f"   [{_speicher_kurz()}]" if speicher else ""
    _schreiben(f"{_zeit()}  {text}{zusatz}")
    if speicher:
        _prozesse_bei_bewegung()


def tabelle(grund: str = "") -> None:
    """Alle Prozesse mit Grafikspeicher."""
    global _zuletzt_prozesse, _zuletzt_tabelle
    if _datei is None:
        return
    liste = prozesse()
    _zuletzt_prozesse = {pid: mb for _, pid, mb in liste}
    _zuletzt_tabelle = time.time()
    eigener = os.getpid()
    zeilen = [f"{_zeit()}  Prozesse mit Grafikspeicher{' – ' + grund if grund else ''}:"]
    for name, pid, mb in liste[:12]:
        markierung = "  <- dieser Prozess" if pid == eigener else ""
        zeilen.append(f"{'':>24}{mb:>8,} MB  {name} ({pid}){markierung}".replace(",", "."))
    if not liste:
        zeilen.append(f"{'':>24}(Leistungszähler nicht verfügbar)")
    _schreiben("\n".join(zeilen))


def _prozesse_bei_bewegung() -> None:
    """Prozess-Tabelle, wenn sich ein Prozess um BEWEGUNG_MB bewegt hat oder sie alt ist."""
    if time.time() - _zuletzt_tabelle >= PROZESSE_ALLE_S:
        tabelle()
        return
    try:
        jetzt = {pid: mb for _, pid, mb in prozesse()}
    except Exception:
        return
    for pid in set(jetzt) | set(_zuletzt_prozesse):
        if abs(jetzt.get(pid, 0) - _zuletzt_prozesse.get(pid, 0)) >= BEWEGUNG_MB:
            tabelle("Bewegung")
            return


def fehler(wo: str, exc: BaseException | None = None) -> None:
    """Fehler mit Rückverfolgung – der gerade behandelte oder `exc`."""
    if _datei is None:
        return
    if exc is not None:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        text = traceback.format_exc()
    _schreiben(f"{_zeit()}  FEHLER {wo}   [{_speicher_kurz()}]\n" + "\n".join("    " + z for z in text.splitlines()))
    try:
        import fehlerwaechter
        fehlerwaechter.fehler_gesehen(wo, type(exc).__name__ if exc is not None else "Fehler", text)
    except Exception:
        pass


class messen:
    """`with messen("Modell laden"):` – Anfang und Ende mit Dauer, Fehler mit Rückverfolgung."""

    def __init__(self, was: str) -> None:
        self.was, self.t = was, 0.0

    def __enter__(self):
        self.t = time.time()
        ereignis(f"{self.was} …")
        return self

    def __exit__(self, art, exc, tb):
        if exc is not None:
            fehler(self.was, exc)
        else:
            ereignis(f"{self.was} fertig ({time.time() - self.t:.1f} s)")
        return False


# ---------------------------------------------------------------------------
# Start, Takt, Fehler-Haken
# ---------------------------------------------------------------------------

def starten(rolle: str, ordner_pfad: Path | None = None, takt: bool = True) -> Path | None:
    """Beginnt den Bericht für diesen Prozess, wenn der Schalter an ist. `rolle`: haupt, wache …"""
    global _datei, _rolle, _t0, _takt
    if not an() or _datei is not None:
        return _datei
    try:
        ziel = ordner_pfad or ordner()
        ziel.mkdir(parents=True, exist_ok=True)
        _rolle, _t0 = rolle, time.time()
        _datei = ziel / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{rolle}_{os.getpid()}.log"
        _datei.write_text("", encoding="utf-8")
        aufraeumen(ziel)
    except OSError:
        _datei = None
        return None
    _kopf()
    _fehlerhaken()
    try:
        import atexit
        import fehlerwaechter
        fehlerwaechter.absturz_schutz()          # frühere Abstürze melden, Spur und Marke für diesen Lauf
        atexit.register(beenden)
    except Exception:
        pass
    if takt:
        _takt = threading.Thread(target=_takt_lauf, name="debugbericht", daemon=True)
        _takt.start()
    return _datei


def _kopf() -> None:
    try:
        import version
        v = f"{getattr(version, 'VERSION', '?')} ({getattr(version, 'STAND', '?')})"
    except Exception:
        v = "?"
    zeilen = [f"NemiCLI Debugbericht – Rolle {_rolle}, PID {os.getpid()}, Version {v}",
              f"Start {datetime.now():%d.%m.%Y %H:%M:%S}   Python {sys.version.split()[0]}   "
              f"exe {bool(getattr(sys, 'frozen', False))}",
              f"Aufruf: {' '.join(sys.argv)}", ""]
    _schreiben("\n".join(zeilen))
    tabelle("Start")


def aufraeumen(ziel: Path, behalten: int = BEHALTEN) -> int:
    """Nur die `behalten` neuesten Berichte bleiben; gibt die Zahl der entfernten zurück."""
    try:
        dateien = sorted(ziel.glob("*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return 0
    weg = 0
    for p in dateien[:-behalten] if behalten > 0 else dateien:
        try:
            p.unlink()
            weg += 1
        except OSError:
            pass
    for p in ziel.glob("*.absturz"):                   # Absturz-Spuren gehen mit ihrem Bericht
        if not p.with_suffix(".log").exists():
            try:
                p.unlink()
            except OSError:
                pass
    return weg


def _takt_lauf() -> None:
    while _datei is not None:
        time.sleep(TAKT_S)
        try:
            if _datei.stat().st_size > TAKT_BIS_MB * 2 ** 20:
                ereignis(f"Takt beendet – Datei über {TAKT_BIS_MB} MB, Ereignisse laufen weiter", speicher=False)
                return
            ereignis("Takt")
        except Exception:
            pass


def motor_log() -> None:
    """Das Motor-Log (Prompts, Graphen, Auslagern) daneben anlegen – nur mit `debug_motor`, und erst
    wenn ein Modell geladen wird, denn sein Kopf weckt CUDA (ein Prozess ohne Modell soll keinen
    Grafikspeicher belegen)."""
    if _datei is None or not motor_log_an():
        return
    try:
        from ggufengine import debuglog
        if debuglog.path() is None:
            debuglog.enable(str(_datei.parent))
            ereignis(f"Motor-Log: {Path(debuglog.path()).name}", speicher=False)
    except Exception:
        pass


_haken_gesetzt = False


def _fehlerhaken() -> None:
    global _haken_gesetzt
    if _haken_gesetzt:
        return
    _haken_gesetzt = True
    alt_sys = sys.excepthook

    def sys_haken(art, exc, tb):
        fehler("unbehandelt", exc)
        return alt_sys(art, exc, tb)
    sys.excepthook = sys_haken

    alt_thread = threading.excepthook

    def thread_haken(args):
        fehler(f"Thread {args.thread.name if args.thread else '?'}", args.exc_value)
        return alt_thread(args)
    threading.excepthook = thread_haken


def schleife(loop) -> None:
    """Fehler aus asyncio-Aufgaben (Hintergrund, Timer) mit aufzeichnen."""
    if _datei is None or getattr(loop, "_debugbericht", False):
        return
    loop._debugbericht = True
    alt = loop.get_exception_handler()

    def haken(lp, kontext):
        exc = kontext.get("exception")
        fehler(f"asyncio: {kontext.get('message', '')}", exc)
        if alt is not None:
            alt(lp, kontext)
        else:
            lp.default_exception_handler(kontext)
    loop.set_exception_handler(haken)
    try:
        import fehlerwaechter
        fehlerwaechter.starten(loop)             # Hänger und Bremsen der Hauptschleife
    except Exception:
        pass


def beenden() -> None:
    global _datei
    if _datei is None:
        return
    ereignis("Ende")
    try:
        import fehlerwaechter
        fehlerwaechter.sauber_beendet()
    except Exception:
        pass
    _datei = None
    if _zaehler is not None:
        _zaehler.schliessen()
