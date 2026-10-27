"""Hintergrund-Aufgaben: lange Werkzeuge (Video, Bild-Serie) laufen nebenbei, der Nutzer redet weiter.
Ist eine Aufgabe fertig oder gescheitert, erfährt es die Persönlichkeit bei der nächsten Nachricht
(Hinweise der Lern-Schleife) und der Nutzer sofort im Terminal; den Stand zeigt das Werkzeug `aufgaben`
und die rechte Seite der Eingabezeile."""
from __future__ import annotations

import asyncio
import time

_AUFGABEN: dict[int, dict] = {}
_NAECHSTE = [1]
BEI_FERTIG: list = []          # Rückrufe(aufgabe) – z. B. die Kugel im Wache-Prozess zeigt fertige Bilder
BEHALTEN = 20                 # so viele erledigte Aufgaben bleiben in der Übersicht


def starten(tool: str, titel: str, arbeit, wer: str) -> int:
    """Startet `arbeit()` (liefert eine Coroutine, z. B. actions.run(act)) als Aufgabe; gibt die Nummer."""
    nr = _NAECHSTE[0]
    _NAECHSTE[0] += 1
    e = {"nr": nr, "tool": tool, "titel": titel, "start": time.time(), "ende": None,
         "ergebnis": None, "ok": None, "wer": wer}
    _AUFGABEN[nr] = e
    for alt in sorted(_AUFGABEN)[:-BEHALTEN]:
        if _AUFGABEN[alt]["ende"] is not None:
            del _AUFGABEN[alt]

    async def lauf():
        try:
            res = await arbeit()
            e["ergebnis"] = str(getattr(res, "text", res))
            e["ok"] = getattr(res, "ok", True)
        except asyncio.CancelledError:
            e["ergebnis"], e["ok"] = "abgebrochen", False
            raise
        except Exception as exc:
            e["ergebnis"], e["ok"] = f"{type(exc).__name__}: {exc}", False
        finally:
            e["ende"] = time.time()
            _melden(e)
    e["task"] = asyncio.get_running_loop().create_task(lauf())
    return nr


def start_text(nr: int) -> str:
    """Antwort des Werkzeugs an die KI, wenn die Aufgabe im Hintergrund gestartet ist."""
    e = _AUFGABEN[nr]
    return (f"⏳ Aufgabe #{nr} läuft im Hintergrund: {e['titel']}. Das dauert Minuten. Sag dem Nutzer kurz, "
            "dass sie läuft, und mach normal weiter – warte NICHT darauf und ruf das Werkzeug nicht noch einmal "
            "auf. Fertig: du erfährst es bei seiner nächsten Nachricht; den Stand zeigt das Werkzeug aufgaben.")


def _dauer(e: dict) -> str:
    s = int((e["ende"] or time.time()) - e["start"])
    return f"{s // 60}:{s % 60:02d} min" if s >= 60 else f"{s} s"


FUER_KI_MAX = 3000                 # so viel vom Ergebnis bekommt die Persönlichkeit (alle Bildpfade einer Serie)


def _melden(e: dict) -> None:
    """Fertig/gescheitert: Hinweis an die Persönlichkeit (ganzes Ergebnis) und kurze Zeile im Terminal."""
    voll = " ".join(str(e["ergebnis"] or "").split())
    kopf = (f"Hintergrund-Aufgabe #{e['nr']} ({e['titel']}) ist nach {_dauer(e)} "
            f"{'fertig' if e['ok'] else 'gescheitert'}: ")
    kurz = voll[:200]
    text = kopf + kurz
    try:
        import lernschleife
        lernschleife.melden(kopf + (voll if len(voll) <= FUER_KI_MAX else voll[:FUER_KI_MAX] + " …"), e["wer"],
                            max_zeichen=len(kopf) + FUER_KI_MAX + 2)
    except Exception:
        pass
    try:
        import ui
        (ui.success if e["ok"] else ui.warn)(("✅ " if e["ok"] else "⚠ ") + text)
        if e["ok"] and e["tool"] in ("musik_machen", "musik_cover"):
            ui.musik_karte_aus(e["ergebnis"])
    except Exception:
        pass
    try:
        import debugbericht
        debugbericht.ereignis(f"Hintergrund-Aufgabe #{e['nr']} {e['tool']} {'fertig' if e['ok'] else 'gescheitert'} "
                              f"nach {_dauer(e)}: {kurz}", speicher=False)
    except Exception:
        pass
    for rueckruf in list(BEI_FERTIG):
        try:
            rueckruf(dict(e))
        except Exception:
            pass


def _fortschritt() -> str:
    try:
        import actions
        return actions.bild_status() or ""
    except Exception:
        return ""


def laufend() -> list[dict]:
    return [e for e in _AUFGABEN.values() if e["ende"] is None]


# Werkzeuge, die die Grafikkarte belegen: neben einer laufenden Aufgabe starten sie nicht –
# zwei Grafikkarten-Arbeiten zugleich können den Grafikspeicher sprengen.
GPU_WERKZEUGE = {"bild_malen", "bild_serie", "comic_malen", "tagesrueckblick", "musik_machen", "musik_cover", "kugel_anziehen", "video_erstellen", "portraet_bewegen", "lora_trainieren",
                 "coding_lora_trainieren",
                 "stimme_probe", "stimme_festlegen", "vorlesen"}


def gpu_belegt() -> dict | None:
    """Die laufende Aufgabe, die die Grafikkarte belegt – sonst None (andere Hintergrund-Arbeit sperrt nichts)."""
    l = [e for e in laufend() if e["tool"] in GPU_WERKZEUGE]
    return l[-1] if l else None


def sperre(tool: str) -> str:
    """Antwort an die KI, wenn `tool` jetzt nicht starten darf – sonst leer."""
    e = gpu_belegt() if tool in GPU_WERKZEUGE else None
    if e is None:
        return ""
    return (f"⛔ Nicht gestartet: Im Hintergrund läuft gerade Aufgabe #{e['nr']} ({e['titel']}, seit {_dauer(e)}). "
            "Die Grafikkarte ist belegt – eine zweite Aufgabe daneben kann das Programm abstürzen lassen. Starte jetzt "
            "nichts mit Bild, Video, Stimme oder Training und ruf das Werkzeug nicht noch einmal auf. Du kannst normal "
            "schreiben und reden; ist die Aufgabe fertig, erfährst du es bei der nächsten Nachricht (Stand: aufgaben).")


def prompt_hinweis() -> str:
    """Block für den System-Prompt, solange eine Aufgabe läuft – damit die KI es vorher weiß."""
    l = laufend()
    if not l:
        return ""
    was = ", ".join(f"#{e['nr']} {e['titel']} (seit {_dauer(e)})" for e in l)
    return (f"\n\n# ⏳ Gerade läuft im Hintergrund: {was}\n"
            "Die Grafikkarte ist belegt. Bis die Aufgabe fertig ist, startest du keine Bilder, Bild-Serien, Videos, "
            "Stimme oder Trainings – du kannst nur normal schreiben, nachsehen und reden.\n")


def stand_text() -> str:
    """Kurz für die Eingabezeile: „⏳ #3 Video: Schritt 12/30 (14.8s)“ – leer ohne laufende Aufgabe."""
    l = laufend()
    if not l:
        return ""
    e = l[-1]
    f = _fortschritt()
    return f"⏳ #{e['nr']} {f}" if f else f"⏳ #{e['nr']} {e['titel']} ({_dauer(e)})"


_SYMBOL = {"video_erstellen": "🎬", "bild_serie": "🎨", "comic_malen": "📖", "tagesrueckblick": "🌙", "musik_machen": "🎵", "musik_cover": "🎵", "kugel_anziehen": "👗", "lora_trainieren": "🧬", "coding_lora_trainieren": "🧠"}


def plakette() -> tuple[str, str] | None:
    """Kurz für die Kopfzeile über der Eingabe: („🎬“, „#2 Video 22/30 · noch 1:43“) – None ohne Aufgabe."""
    import re
    l = laufend()
    if not l:
        return None
    e = l[-1]
    f = _fortschritt()
    m = re.search(r"(\d+)\s*/\s*(\d+)", f)
    if m:
        art = f.split(":")[0].strip()[:12] if ":" in f[:m.start()] else ""
        noch = re.search(r"noch (?:etwa |ca\. )?([\d:]+(?:\s*min)?)", f)
        text = (f"#{e['nr']} {art + ' ' if art else ''}{m.group(1)}/{m.group(2)}"
                + (f" · noch {noch.group(1)}" if noch else ""))
    else:
        titel = e["titel"].split(" ", 1)[-1] if not e["titel"][:1].isalnum() else e["titel"]
        text = f"#{e['nr']} {titel} ({_dauer(e)})"
    if (n := len(laufend())) > 1:
        text += f" (+{n - 1})"
    return _SYMBOL.get(e["tool"], "⏳"), text


def uebersicht() -> str:
    """Für das Werkzeug `aufgaben`: laufende und erledigte Aufgaben."""
    if not _AUFGABEN:
        return "Keine Hintergrund-Aufgaben."
    zeilen = []
    f = _fortschritt()
    for e in sorted(_AUFGABEN.values(), key=lambda x: x["nr"]):
        if e["ende"] is None:
            zeilen.append(f"#{e['nr']} läuft seit {_dauer(e)}: {e['titel']}" + (f" – {f}" if f else ""))
        else:
            kurz = " ".join(str(e["ergebnis"] or "").split())[:160]
            zeilen.append(f"#{e['nr']} {'fertig' if e['ok'] else 'gescheitert'} ({_dauer(e)}): {e['titel']} – {kurz}")
    return "\n".join(zeilen)


def _test_reset() -> None:                                             # nur für Tests
    _AUFGABEN.clear()
    _NAECHSTE[0] = 1
