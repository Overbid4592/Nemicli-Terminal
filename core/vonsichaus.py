"""Von sich aus: was die Persönlichkeit im freien Moment (Kugel) sagt oder malt, erscheint auch im NemiCLI-Chat.

Die Kugel läuft im Wache-Prozess und hängt Einträge an eine Datei an; das laufende NemiCLI holt sie ab – läuft es
nicht, beim nächsten Start. Dazu die Regel für spontane Runden: ein Bild nur, wenn genug Grafikspeicher frei ist,
gemessen in dem Moment, in dem gemalt werden soll; andere Grafikkarten-Arbeit (Video, Serien) gar nicht.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    from paths import INSTALL as _ORT
except Exception:
    _ORT = Path(__file__).resolve().parents[1]

DATEI = Path(_ORT) / ".nemicli.vonsichaus"
BILD_AB_MB = 5 * 1024             # mehr als 5 GB frei: malen erlaubt
HOECHSTENS = 20                   # so viele Einträge warten höchstens
ALTER_S = 24 * 3600               # ältere verfallen

_runde = False


def runde(an: bool) -> None:
    """Markiert eine spontane Runde (freier Moment, Ankommen, Musik) – dort gilt die Grafikspeicher-Regel."""
    global _runde
    _runde = bool(an)


def in_runde() -> bool:
    return _runde


def vram_frei_mb() -> int | None:
    """Freier Grafikspeicher der ersten NVIDIA-Karte (NVML, auch unter WDDM) – None, wenn nicht messbar."""
    try:
        import cockpit
        g = cockpit.gpu(0.0)
    except Exception:
        return None
    if not g or not g.get("gesamt"):
        return None
    return max(0, int(g["gesamt"]) - int(g["belegt"])) // 2 ** 20


def _gb(mb: int) -> str:
    return f"{mb / 1024:.1f}".replace(".", ",")


def bild_erlaubt() -> tuple[bool, str]:
    """(erlaubt, Grund) für ein Bild in einer spontanen Runde. Malt die Cloud, braucht es keinen Grafikspeicher."""
    try:
        import config
        import videobildcloud
        if config.load().get("bild_backend") == "cloud" and videobildcloud.available():
            return True, ""
    except Exception:
        pass
    frei = vram_frei_mb()
    if frei is None:
        return False, "der freie Grafikspeicher lässt sich gerade nicht messen"
    if frei > BILD_AB_MB:
        return True, ""
    return False, f"nur {_gb(frei)} GB Grafikspeicher frei – malen erst ab mehr als {BILD_AB_MB // 1024} GB"


def hinweis() -> str:
    """Zeile für den Auftrag des freien Moments: darf sie gerade malen?"""
    ok, grund = bild_erlaubt()
    if ok:
        frei = vram_frei_mb() or 0
        return f"\nGrafikspeicher: {_gb(frei)} GB frei – ein Bild (bild_malen) geht."
    return f"\nGrafikspeicher: {grund}. Heute kein Bild – sag lieber etwas oder bleib still."


def schreiben(name: str, text: str, bilder: list[str] | None = None) -> None:
    """Eintrag anhängen (Wache-Prozess)."""
    text = (text or "").strip()
    bilder = [str(b) for b in bilder or []]
    if not text and not bilder:
        return
    eintrag = {"zeit": time.time(), "name": name, "text": text[:4000], "bilder": bilder[:8]}
    try:
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        with open(DATEI, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except OSError:
        pass


def abholen() -> list[dict]:
    """Alle wartenden Einträge (NemiCLI-Prozess) – die Datei wird dabei geleert. Erst umbenennen, dann lesen:
    hängt die Kugel gleichzeitig etwas an, landet es in einer neuen Datei und geht nicht verloren."""
    if not DATEI.exists():
        return []
    zwischen = DATEI.with_name(f"{DATEI.name}.{os.getpid()}")
    try:
        os.replace(DATEI, zwischen)
    except OSError:
        return []                                      # gerade in Benutzung: beim nächsten Mal
    eintraege: list[dict] = []
    try:
        for zeile in zwischen.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(zeile)
            except ValueError:
                continue
            if isinstance(e, dict) and time.time() - float(e.get("zeit") or 0) < ALTER_S:
                eintraege.append(e)
    except OSError:
        pass
    finally:
        try:
            zwischen.unlink()
        except OSError:
            pass
    return eintraege[-HOECHSTENS:]


def kontext(eintraege: list[dict]) -> str:
    """Vorspann für die nächste Nachricht im NemiCLI-Chat: worauf der Nutzer vielleicht antwortet."""
    teile = []
    for e in eintraege:
        uhr = time.strftime("%H:%M", time.localtime(float(e.get("zeit") or 0)))
        was = e.get("text") or ""
        if e.get("bilder"):
            was = (was + " " if was else "") + "(dazu ein Bild: " + ", ".join(Path(b).name for b in e["bilder"]) + ")"
        teile.append(f"um {uhr}: „{was}“")
    if not teile:
        return ""
    return ("[Vorhin hast du dem Nutzer von dir aus geschrieben – " + "; ".join(teile)
            + ". Seine Nachricht kann sich darauf beziehen.]\n\n")
