"""
bibkind.py - Der Bibliothekar (indexdb.after_turn) in einem eigenen Prozess.

Im Chat-Prozess teilte er sich Python und Prozessor mit der Oberfläche; ein großer Auftrag auf der GPU
hinterließ dort außerdem einen CUDA-Kontext bis zum Programmende. Jetzt rechnet je Lauf ein Kindprozess
(`--bibliothekar`) mit niedriger Priorität und endet danach – mit ihm gehen Encoder, RAM und Grafikspeicher.
memory.db läuft im WAL-Modus; der Such-Index des Chats erkennt neue Einträge am Dateistand.

Austausch über Dateien in einem Ordner je Elternprozess (die Fenster-exe hat keine Standardausgabe):
auftrag.json → das Kind schreibt status.txt (eine Zeile je Meldung) und zuletzt ergebnis.json.
Unterbrechen (neue Runde, Bildermalen): indexdb.unterbrechen() legt die Datei `unterbrechen` an.
Das Kind rechnet nur auf der GPU (memory.NUR_GPU); ohne Grafikkarte auf der CPU.
Der Merkzettel, wann welcher Chat zuletzt eingebettet wurde, geht hin und zurück.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TAKT_S = 0.3
ENV = "NEMICLI_BIBKIND"

_lock = threading.Lock()


def aktiv() -> bool:
    if os.environ.get(ENV):
        return False
    try:
        import config
        return config.load().get("bibliothekar_eigener_prozess", True) is not False
    except Exception:
        return True


def ordner(pid: int | None = None) -> Path:
    return Path(tempfile.gettempdir()) / "NemiCLI" / f"bibliothekar_{pid or os.getpid()}"


def befehl(o: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        from paths import exe_ohne_fenster
        return [str(exe_ohne_fenster()), "--bibliothekar", str(o), str(os.getpid())]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    main = Path(__file__).resolve().parents[1] / "main.py"
    return [str(pythonw if pythonw.exists() else python), str(main), "--bibliothekar", str(o), str(os.getpid())]


def lauf_hier(chat_id, alles: bool, on_status, erzwingen: bool = False, nur_kurz: bool = False) -> dict:
    """after_turn in diesem Prozess + Ergebnis. `neu` = neu gespeicherte Brocken, `mit_vektor` = wie viele
    Brocken in diesem Lauf einen Vektor bekommen haben. Das bloße Sammeln läuft so: Es braucht kein Modell."""
    import indexdb
    out = {"n": 0, "err": None, "mit_vektor": 0, "neu": 0}
    try:
        vorher = indexdb.offen()
        out["n"] = indexdb.after_turn(chat_id, status=on_status, alles=alles, chat_erzwingen=erzwingen,
                                      nur_kurz=nur_kurz)
        nachher = indexdb.offen()
        out["neu"] = max(0, nachher["brocken"] - vorher["brocken"])
        out["mit_vektor"] = max(0, (nachher["brocken"] - nachher["ohne_vektor"])
                                - (vorher["brocken"] - vorher["ohne_vektor"]))
    except Exception as e:
        out["err"] = e
    return out


def _melden(text: str) -> None:
    try:
        import debugbericht
        debugbericht.ereignis(text)
    except Exception:
        pass


def _status_lesen(p: Path, schon: int, on_status) -> int:
    try:
        zeilen = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return schon
    for z in zeilen[schon:]:
        if on_status is not None:
            on_status(z)
    return len(zeilen)


def laufen(chat_id, alles: bool, on_status, erzwingen: bool = False) -> dict:
    """Ein Bibliothekar-Lauf mit Modell (blockierend, für einen Thread): im Kindprozess, sonst hier."""
    if not aktiv():
        return lauf_hier(chat_id, alles, on_status, erzwingen)
    import indexdb
    with _lock:
        o = ordner()
        shutil.rmtree(o, ignore_errors=True)
        o.mkdir(parents=True, exist_ok=True)
        auftrag = {"chat_id": chat_id, "alles": bool(alles), "erzwingen": bool(erzwingen),
                   "chat_stand": dict(indexdb._chat_stand), "nachgeholt": dict(indexdb._nachgeholt)}
        (o / "auftrag.json").write_text(json.dumps(auftrag, ensure_ascii=False), encoding="utf-8")
        try:
            kind = subprocess.Popen(befehl(o), env=dict(os.environ, **{ENV: "1"}), cwd=str(o),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            _melden(f"Bibliothekar: eigener Prozess startet nicht ({e}) – rechne hier")
            return lauf_hier(chat_id, alles, on_status, erzwingen)
        indexdb.SIGNAL_DATEI = o / "unterbrechen"
        _melden(f"Bibliothekar im eigenen Prozess (PID {kind.pid})")
        erg, schon = o / "ergebnis.json", 0
        try:
            while not erg.exists():
                schon = _status_lesen(o / "status.txt", schon, on_status)
                if kind.poll() is not None and not erg.exists():
                    return {"n": 0, "err": f"eigener Prozess unerwartet beendet (Code {kind.returncode})",
                            "mit_vektor": 0, "neu": 0}
                time.sleep(TAKT_S)
            _status_lesen(o / "status.txt", schon, on_status)
            d = json.loads(erg.read_text(encoding="utf-8"))
        except BaseException:
            if kind.poll() is None:
                kind.kill()
            raise
        finally:
            indexdb.SIGNAL_DATEI = None
        try:
            kind.wait(timeout=10)
        except subprocess.TimeoutExpired:
            kind.kill()
        shutil.rmtree(o, ignore_errors=True)
    indexdb._chat_stand.update(d.pop("chat_stand", None) or {})
    indexdb._nachgeholt.update(d.pop("nachgeholt", None) or {})
    indexdb._invalidate()
    return {"n": int(d.get("n") or 0), "err": d.get("err"), "mit_vektor": int(d.get("mit_vektor") or 0),
            "neu": int(d.get("neu") or 0)}


# --- Kind ------------------------------------------------------------------------------------------

def _niedrig() -> None:
    """Niedrige Priorität: der Chat bekommt den Prozessor zuerst."""
    try:
        import psutil
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except Exception:
        pass


def _eltern_beobachten(pid: int) -> None:
    """Endet NemiCLI, endet auch das Kind."""
    try:
        import psutil
    except Exception:
        return
    while True:
        time.sleep(2.0)
        if not psutil.pid_exists(pid):
            os._exit(0)


def hauptprogramm(argv: list[str]) -> int:
    """`--bibliothekar <ordner> <eltern-pid>`: einen Lauf rechnen, Ergebnis ablegen, enden."""
    if len(argv) < 2 or not argv[1].isdigit():
        return 2
    o, eltern = Path(argv[0]), int(argv[1])
    os.environ[ENV] = "1"
    try:
        d = json.loads((o / "auftrag.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 2
    _niedrig()
    threading.Thread(target=_eltern_beobachten, args=(eltern,), daemon=True).start()
    import indexdb
    indexdb.SIGNAL_DATEI = o / "unterbrechen"
    import memory
    memory.NUR_GPU = memory.hat_gpu()
    indexdb._chat_stand.update(d.get("chat_stand") or {})
    indexdb._nachgeholt.update(d.get("nachgeholt") or {})
    status = o / "status.txt"

    def melde(text) -> None:
        with open(status, "a", encoding="utf-8") as f:
            f.write(" ".join(str(text).split()) + "\n")

    out = lauf_hier(d.get("chat_id"), bool(d.get("alles")), melde, bool(d.get("erzwingen")))
    out["err"] = f"{out['err']}" if out["err"] is not None else None
    out["chat_stand"], out["nachgeholt"] = dict(indexdb._chat_stand), dict(indexdb._nachgeholt)
    tmp = o / "ergebnis.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(o / "ergebnis.json")
    return 0
