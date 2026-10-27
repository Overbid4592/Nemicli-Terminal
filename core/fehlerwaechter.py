"""Fehler-Wächter: erkennt, was der Debugbericht sonst nur mitschreibt.

* Hänger und Bremsen der Hauptschleife – mit der Stelle im Code, bei Hängern jeder Faden
* Modelle, die nach dem Entladen festgehalten werden – und von wem (Funktion, Aufgabe, Attribut)
* harte Abstürze (faulthandler, auch im C-Teil: CUDA, TensorRT) und ein unsauberes Ende, beim nächsten Start
* wiederholte Fehler und Rechenfehler (NaN)

Jede Auffälligkeit wird ein Befund: eine „⚠ BEFUND“-Zeile im Debugbericht und ein Eintrag in
Debugbericht/befunde.json (über Starts hinweg), den die Selbstdiagnose zeigt. Läuft nur, wenn der Debugbericht
läuft. Kostet im Normalfall fast nichts: ein Faden schaut viermal je Sekunde auf eine Uhr.
"""
from __future__ import annotations

import gc
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import debugbericht as D

PULS_S = 0.25                 # Herzschlag der Hauptschleife
BREMSE_S = 1.5                # so lange blockiert = Bremse (Zeile im Bericht)
BREMSE_BEFUND_S = 3.0         # ab hier auch ein Befund
HAENGER_S = 10.0              # so lange blockiert = Hänger (alle Fäden in den Bericht)
BREMSEN_MAX = 20              # Bremsen-Zeilen je 10 Minuten
WIEDERHOLT = 3                # gleicher Fehler so oft in 10 Minuten = Befund
BEFUNDE_MAX = 60              # Einträge in befunde.json
PAUSE_S = 5.0                 # kam der Prüf-Faden selbst so spät dran, stand der ganze PC (Standby): kein Hänger
BEFUNDE_DATEI = "befunde.json"

_sperre = threading.Lock()
_puls: float | None = None
_haupt_ident: int | None = None
_faden: threading.Thread | None = None
_fehler_zeiten: dict[str, list[float]] = {}
_bremsen_zeiten: list[float] = []


# ---------------------------------------------------------------------------
# Befunde
# ---------------------------------------------------------------------------

def _ordner() -> Path:
    """Der Ordner des laufenden Berichts (sonst der übliche)."""
    datei = D.datei()
    return datei.parent if datei is not None else D.ordner()


def befunde_pfad() -> Path:
    return _ordner() / BEFUNDE_DATEI


def befunde() -> list[dict]:
    """Alle gespeicherten Befunde, älteste zuerst."""
    try:
        daten = json.loads(befunde_pfad().read_text(encoding="utf-8"))
        return [b for b in daten if isinstance(b, dict)] if isinstance(daten, list) else []
    except (OSError, ValueError):
        return []


def befund(art: str, text: str, einzelheiten: str = "") -> None:
    """Eine Auffälligkeit: Zeile im Bericht (Einzelheiten eingerückt darunter) und Eintrag in befunde.json."""
    if D.datei() is None:
        return
    zeile = f"⚠ BEFUND {art}: {text}"
    if einzelheiten:
        zeile += "\n" + "\n".join("    " + z for z in einzelheiten.splitlines())
    D.ereignis(zeile, speicher=False)
    eintrag = {"zeit": datetime.now().isoformat(timespec="seconds"), "rolle": D._rolle, "pid": os.getpid(),
               "art": art, "text": text}
    with _sperre:
        liste = befunde() + [eintrag]
        try:
            pfad = befunde_pfad()
            teil = pfad.with_suffix(".tmp")
            teil.write_text(json.dumps(liste[-BEFUNDE_MAX:], ensure_ascii=False, indent=1), encoding="utf-8")
            teil.replace(pfad)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Stellen im Code
# ---------------------------------------------------------------------------

_FREMD = {"site-packages", "dist-packages", "<frozen", "<string>"}
# Im gebauten Programm sind Dateinamen nur noch relativ (z. B. httpcore/_sync/connection.py), dort
# entscheidet der Modulname. Pakete, die nicht über Metadaten auffindbar sind, stehen hier.
_FREMDE_WURZELN = frozenset((
    "textual", "rich", "httpx", "httpcore", "h11", "h2", "anyio", "sniffio", "certifi", "idna", "urllib3",
    "requests", "websockets", "aiohttp", "prompt_toolkit", "pygments", "markdown_it", "mdurl", "pydantic",
    "torch", "numpy", "safetensors", "tokenizers", "transformers", "gguf", "PIL", "cv2", "PySide6",
    "shiboken6", "psutil", "openai", "anthropic", "jinja2", "tensorrt", "cuda", "nvidia", "PyInstaller",
    "pyimod01_archive", "pyimod02_importers", "pyimod03_ctypes", "pyimod04_pywin32", "win32com", "pythoncom",
))
_fremde: frozenset[str] | None = None


def _fremde_wurzeln() -> frozenset[str]:
    global _fremde
    if _fremde is None:
        try:
            from importlib.metadata import packages_distributions
            weitere = set(packages_distributions())
        except Exception:
            weitere = set()
        _fremde = _FREMDE_WURZELN | weitere | set(sys.stdlib_module_names)
    return _fremde


def _eigen(dateiname: str, modul: str = "") -> bool:
    """Gehört der Rahmen zu NemiCLI (nicht zu Python selbst oder einer Bibliothek)?"""
    if any(f in dateiname for f in _FREMD):
        return False
    if modul and modul != "__main__" and modul.split(".")[0] in _fremde_wurzeln():
        return False
    teile = Path(dateiname).parts
    if any(t.lower() == "lib" for t in teile):
        return False
    return not any(Path(t).stem in sys.stdlib_module_names for t in teile[-2:])


def _eigener_rahmen(f) -> bool:
    return _eigen(f.f_code.co_filename, f.f_globals.get("__name__") or "")


def _rahmen_text(f) -> str:
    return f"{Path(f.f_code.co_filename).name}:{f.f_lineno} in {f.f_code.co_name}"


def stelle(frame, eigene: int = 4) -> str:
    """Wo steckt ein Faden: die innersten NemiCLI-Stellen (innen zuerst) und worin er gerade wartet."""
    if frame is None:
        return "?"
    innen = frame
    eigen = []
    f = frame
    while f is not None and len(eigen) < eigene:
        if _eigener_rahmen(f):
            eigen.append(_rahmen_text(f))
        f = f.f_back
    text = " ← ".join(eigen) or _rahmen_text(innen)
    if eigen and not _eigener_rahmen(innen):
        text += f"   (gerade in {_rahmen_text(innen)})"
    return text


def alle_faeden() -> str:
    """Jeder Faden mit seiner Stelle (für Hänger)."""
    namen = {t.ident: t.name for t in threading.enumerate()}
    zeilen = []
    for ident, frame in list(sys._current_frames().items())[:20]:
        if ident == threading.get_ident():
            continue
        name = namen.get(ident, "?") + (" [Hauptschleife]" if ident == _haupt_ident else "")
        zeilen.append(f"{name}: {stelle(frame)}")
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Hänger und Bremsen der Hauptschleife
# ---------------------------------------------------------------------------

def starten(loop) -> None:
    """Herzschlag in der Hauptschleife und den Prüf-Faden anwerfen (einmal je Prozess)."""
    global _faden
    if D.datei() is None or (_faden is not None and _faden.is_alive()):
        return

    def schlag():
        global _puls, _haupt_ident
        _haupt_ident = threading.get_ident()
        _puls = time.monotonic()
        loop.call_later(PULS_S, schlag)
    loop.call_soon_threadsafe(schlag)
    _faden = threading.Thread(target=_pruefen, name="fehlerwaechter", daemon=True)
    _faden.start()


def _bremse_melden(dauer: float, wo: str, prozess_stand: bool, faeden: str = "") -> None:
    """faeden: alle Fäden direkt nach einem Prozess-Stillstand – der Verursacher steht meist
    gleich hinter dem langen Aufruf, der Python festhielt."""
    jetzt = time.monotonic()
    _bremsen_zeiten[:] = [t for t in _bremsen_zeiten if jetzt - t < 600]
    if prozess_stand:
        wo = "der ganze Prozess stand (eine Bibliothek hielt Python fest) – zuletzt in " + wo
    if len(_bremsen_zeiten) < BREMSEN_MAX:
        _bremsen_zeiten.append(jetzt)
        zeile = f"⚠ Bremse: Hauptschleife {dauer:.1f} s blockiert – {wo}"
        if prozess_stand and faeden:
            zeile += "\n" + "\n".join("    " + z for z in faeden.splitlines())
        D.ereignis(zeile, speicher=False)
    if dauer >= BREMSE_BEFUND_S:
        befund("bremse", f"Oberfläche {dauer:.1f} s blockiert", wo + ("\n" + faeden if prozess_stand and faeden else ""))


def _pruefen() -> None:
    blockiert = False
    wo, haenger, prozess_stand, laengste, faeden = "", False, False, 0.0, ""
    zuletzt = time.monotonic()
    while D.datei() is not None:
        time.sleep(PULS_S)
        jetzt = time.monotonic()
        luecke = jetzt - zuletzt - PULS_S
        selbst_spaet = luecke > 1.0                         # dieser Faden kam selbst nicht dran
        zuletzt = jetzt
        if _puls is None:
            continue
        if luecke > PAUSE_S:                                # Standby / Ruhezustand: zählt nicht
            blockiert = False
            continue
        alter = jetzt - _puls
        try:
            if alter >= 1.0:
                if not blockiert:
                    blockiert, haenger, laengste = True, False, alter
                    prozess_stand = selbst_spaet
                    wo = stelle(sys._current_frames().get(_haupt_ident))
                    faeden = alle_faeden() if selbst_spaet else ""
                laengste = max(laengste, alter)
                if alter >= HAENGER_S and not haenger:
                    haenger = True
                    befund("haenger", f"Hauptschleife reagiert seit {alter:.0f} s nicht – {wo}", alle_faeden())
            elif blockiert:
                blockiert = False
                if haenger:
                    D.ereignis(f"Hänger vorbei nach {laengste:.1f} s", speicher=False)
                elif laengste >= BREMSE_S:
                    _bremse_melden(laengste, wo, prozess_stand, faeden)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Festgehaltene Modelle
# ---------------------------------------------------------------------------

def _halter_text(r, ziel) -> str | None:
    """Ein Verweis, der eine erkennbare Stelle ist – sonst None (dann weiter nach außen suchen)."""
    import asyncio
    import types
    if isinstance(r, types.FrameType):
        return f"Funktion {r.f_code.co_qualname} ({Path(r.f_code.co_filename).name}:{r.f_lineno})"
    if isinstance(r, types.MethodType):
        return f"Methode {type(r.__self__).__name__}.{r.__func__.__name__}"
    if isinstance(r, types.FunctionType):
        return f"Abschluss der Funktion {r.__qualname__}"
    if isinstance(r, asyncio.Task):
        coro = r.get_coro()
        return f"Aufgabe {r.get_name()} ({getattr(coro, '__qualname__', '?')})"
    if isinstance(r, dict):
        schluessel = next((k for k, v in r.items() if v is ziel), "?")
        for halter in gc.get_referrers(r):
            if isinstance(halter, types.ModuleType) and halter.__dict__ is r:
                return f"Modul-Variable {halter.__name__}.{schluessel}"
            if getattr(halter, "__dict__", None) is r:
                return f"Attribut {type(halter).__name__}.{schluessel}"
        return None
    if isinstance(r, (list, tuple, set, frozenset, types.CellType, types.ModuleType, type)):
        return None
    try:                                           # Attribut ohne eigenes __dict__-Objekt (Python 3.11+)
        for k, v in vars(r).items():
            if v is ziel:
                return f"Attribut {type(r).__name__}.{k}"
    except TypeError:
        pass
    return None


def wer_haelt(obj, tiefe: int = 4, max_halter: int = 6) -> list[str]:
    """Verweise auf `obj` bis zu erkennbaren Stellen (Funktion, Methode, Attribut, Modul-Variable)."""
    gc.collect()
    eigen = set()                                  # die eigene Aufrufkette hält obj auch – zählt nicht
    f = sys._getframe()
    while f is not None:
        eigen.add(id(f))
        f = f.f_back
    gefunden: list[str] = []
    gesehen = {id(obj)}
    ebene = [obj]
    for _ in range(tiefe):
        naechste = []
        for ziel in ebene:
            for r in gc.get_referrers(ziel):
                if id(r) in gesehen or id(r) in eigen or r is ebene or r is naechste:
                    continue
                gesehen.add(id(r))
                text = _halter_text(r, ziel)
                if text is not None:
                    if text not in gefunden:
                        gefunden.append(text)
                else:
                    naechste.append(r)
                if len(gefunden) >= max_halter:
                    return gefunden
        if not naechste:
            break
        ebene = naechste[:50]
    return gefunden


def festgehalten(obj, name: str, sekunden: float) -> None:
    """Ein entladenes Modell ist nach `sekunden` noch da: Befund mit denen, die es halten."""
    try:
        halter = wer_haelt(obj)
    except Exception as exc:
        halter = [f"(nicht ermittelbar: {exc})"]
    befund("leck", f"{name} nach dem Entladen {sekunden:.0f} s später noch im Speicher",
           "gehalten von: " + ("; ".join(halter) if halter else "?"))


# ---------------------------------------------------------------------------
# Wiederholte Fehler und Rechenfehler (aus debugbericht.fehler)
# ---------------------------------------------------------------------------

_NAN = re.compile(r"\bNaN\b|invalid values|ungültige Werte", re.IGNORECASE)


def fehler_gesehen(wo: str, art: str, text: str) -> None:
    jetzt = time.monotonic()
    if _NAN.search(text):
        befund("rechenfehler", f"{wo}: ungültige Werte (NaN/Inf)")
    schluessel = f"{wo}|{art}"
    zeiten = [t for t in _fehler_zeiten.get(schluessel, []) if jetzt - t < 600] + [jetzt]
    _fehler_zeiten[schluessel] = zeiten
    if len(zeiten) == WIEDERHOLT:
        befund("wiederholt", f"{wo}: {art} {WIEDERHOLT}× in 10 Minuten")


# ---------------------------------------------------------------------------
# Abstürze und unsauberes Ende
# ---------------------------------------------------------------------------

_absturz_datei = None
_spur_vorher = False                               # faulthandler war schon an (z. B. -X faulthandler)
_marke: Path | None = None
_strg_haken = None


def _marke_pfad(rolle: str, pid: int) -> Path:
    return _ordner() / f"laeuft_{rolle}_{pid}.json"


def _pid_lebt(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


# Windows beendet Programme beim Herunterfahren und Abmelden: 1074 Herunterfahren/Neustart angefordert, 7002 Abmelden,
# 13 Betriebssystem fährt herunter, 109 Kernel fährt herunter, 6006 Ereignisprotokoll beendet.
_WINDOWS_ENDE = (1074, 7002, 13, 109, 6006)


def windows_beendet(zeit: float, vor: float = 15.0, nach: float = 120.0) -> bool:
    """Hat Windows um `zeit` herunterfahren oder abmelden lassen (Ereignisprotokoll „System“)? Über die
    Ereignis-API (wevtapi), ohne PowerShell. Im Zweifel False – dann bleibt es beim Befund „unsauber“."""
    if os.name != "nt" or not zeit:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        from datetime import timezone
        api = ctypes.WinDLL("wevtapi")
        api.EvtQuery.restype = wintypes.HANDLE
        api.EvtQuery.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        api.EvtNext.restype = wintypes.BOOL
        api.EvtNext.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        api.EvtClose.argtypes = [wintypes.HANDLE]

        def utc(s: float) -> str:
            return datetime.fromtimestamp(s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        ids = " or ".join(f"EventID={i}" for i in _WINDOWS_ENDE)
        abfrage = (f"*[System[({ids}) and TimeCreated[@SystemTime>='{utc(zeit - vor)}' and "
                   f"@SystemTime<='{utc(zeit + nach)}']]]")
        h = api.EvtQuery(None, "System", abfrage, 1)          # EvtQueryChannelPath
        if not h:
            return False
        try:
            ereignis, anzahl = wintypes.HANDLE(), wintypes.DWORD()
            if api.EvtNext(h, 1, ctypes.byref(ereignis), 1000, 0, ctypes.byref(anzahl)) and anzahl.value:
                api.EvtClose(ereignis)
                return True
            return False
        finally:
            api.EvtClose(h)
    except Exception:
        return False


def letzter_lauf(rolle: str) -> list[dict]:
    """Frühere Starts dieser Rolle ohne normales Ende (Marke liegt noch, Prozess lebt nicht mehr): je Start
    Zeit, PID, ob die Absturz-Spur Text hat, das Ende des Berichts, wann der Bericht zuletzt schrieb. Die Marken
    werden dabei weggeräumt."""
    aus = []
    for marke in sorted(_ordner().glob(f"laeuft_{rolle}_*.json")):
        try:
            daten = json.loads(marke.read_text(encoding="utf-8"))
            pid = int(daten.get("pid", 0))
        except (OSError, ValueError, TypeError):
            pid, daten = 0, {}
        if pid == os.getpid() or (pid and _pid_lebt(pid)):
            continue
        spur = ""
        if daten.get("absturz"):
            try:
                spur = Path(daten["absturz"]).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                pass
        ende, zuletzt = "", 0.0
        if daten.get("log"):
            try:
                ende = "\n".join(Path(daten["log"]).read_text(encoding="utf-8", errors="replace")
                                 .splitlines()[-8:])
                zuletzt = Path(daten["log"]).stat().st_mtime        # der Takt schreibt alle 10 s
            except OSError:
                pass
        aus.append({"start": daten.get("start", "?"), "pid": pid, "spur": spur, "ende": ende,
                    "log": Path(daten.get("log", "")).name, "zuletzt": zuletzt})
        try:
            marke.unlink()
        except OSError:
            pass
    return aus


def absturz_schutz() -> None:
    """Beim Start des Berichts: frühere unsaubere Enden melden, Marke für diesen Lauf legen, Abstürze im
    C-Teil in eine Spur-Datei schreiben lassen, das Schließen des Fensters erkennen."""
    global _absturz_datei, _marke, _spur_vorher
    datei = D.datei()
    if datei is None or _marke is not None:
        return
    for lauf in letzter_lauf(D._rolle):
        if lauf["spur"]:
            befund("absturz", f"Start {lauf['start']} (PID {lauf['pid']}) ist abgestürzt – Bericht {lauf['log']}",
                   lauf["spur"][-3000:])
        elif windows_beendet(lauf.get("zuletzt") or 0.0):
            # Herunterfahren/Abmelden: Windows beendet Programme ohne Bescheid, wenn der Qt-Haken nicht ankam
            D.ereignis(f"Start {lauf['start']} (PID {lauf['pid']}) endete mit dem Herunterfahren oder Abmelden von "
                       f"Windows – kein Fehler (Bericht {lauf['log']})", speicher=False)
        else:
            befund("unsauber", f"Start {lauf['start']} (PID {lauf['pid']}) endete ohne normales Beenden "
                   f"(beendet von außen oder Absturz ohne Spur) – Bericht {lauf['log']}", lauf["ende"])
    spur = datei.with_suffix(".absturz")
    try:
        import faulthandler
        _spur_vorher = faulthandler.is_enabled()
        _absturz_datei = open(spur, "w", encoding="utf-8")
        faulthandler.enable(file=_absturz_datei, all_threads=True)
    except Exception:
        _absturz_datei, spur = None, None
    _marke = _marke_pfad(D._rolle, os.getpid())
    try:
        _marke.write_text(json.dumps({"pid": os.getpid(), "start": f"{datetime.now():%d.%m.%Y %H:%M:%S}",
                                      "log": str(datei), "absturz": str(spur) if spur else ""}), encoding="utf-8")
    except OSError:
        _marke = None
    _fenster_haken()


def sauber_beendet(grund: str = "") -> None:
    """Normales Ende (atexit, /debug aus) oder Fenster zu: Marke weg, leere Spur-Datei weg."""
    global _absturz_datei, _marke
    if grund:
        D.ereignis(grund, speicher=False)
    if _marke is not None:
        try:
            _marke.unlink()
        except OSError:
            pass
        _marke = None
    if _absturz_datei is not None:
        try:
            import faulthandler
            faulthandler.disable()
            if _spur_vorher and sys.stderr is not None:
                faulthandler.enable()                  # wie vorher: Spur wieder auf die Konsole
            pfad = Path(_absturz_datei.name)
            _absturz_datei.close()
            if pfad.stat().st_size == 0:
                pfad.unlink()
        except Exception:
            pass
        _absturz_datei = None


def _fenster_haken() -> None:
    """Windows: Schließen des Konsolenfensters, Abmelden, Herunterfahren gelten als normales Ende."""
    global _strg_haken
    if os.name != "nt" or _strg_haken is not None:
        return
    try:
        import ctypes
        art = {2: "Fenster geschlossen", 5: "Abgemeldet", 6: "Windows fährt herunter"}

        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def haken(signal):
            if signal in art:
                sauber_beendet(art[signal])
            return 0                               # weiter an die übrigen Handler
        if ctypes.windll.kernel32.SetConsoleCtrlHandler(haken, True):
            _strg_haken = haken                    # festhalten, sonst räumt Python den Rückruf weg
    except Exception:
        pass


def kurz_text(b: dict) -> str:
    try:
        zeit = datetime.fromisoformat(b["zeit"]).strftime("%d.%m. %H:%M")
    except (KeyError, ValueError):
        zeit = "?"
    eigen = " (dieser Start)" if b.get("pid") == os.getpid() else ""
    return f"{zeit} {b.get('rolle', '?')}{eigen} – {b.get('art', '?')}: {b.get('text', '')}"


def hinweis_beim_start() -> str:
    """Ein kurzer Satz für die Oberfläche, wenn der letzte Start abgestürzt ist (sonst leer)."""
    gemeldet = [b for b in befunde() if b.get("art") == "absturz" and b.get("pid") == os.getpid()
                and b.get("rolle") == D._rolle]          # beim Start dieses Laufs über den letzten gemeldet
    if not gemeldet:
        return ""
    return ("⚠ NemiCLI ist beim letzten Mal abgestürzt – die Spur steht im Debugbericht. "
            "Mit einem Cloud-Modell: „Prüf dich mal“ (Selbstdiagnose).")


