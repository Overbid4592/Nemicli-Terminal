"""Selbstdiagnose: NemiCLI sieht sich in einem Zug selbst an – Programm, Rechner, Grafikspeicher je Prozess,
Systemcheck (torch, Pakete, Modelle, was fehlt) und die letzten Fehler aus dem Debugbericht.

Für das Werkzeug `selbstdiagnose` (nur Cloud-Modelle, wie `debugbericht`): die KI erklärt daraus den Zustand und
schreibt ihren Befund nach Berichte/. Jeder Abschnitt steht für sich – scheitert einer, steht der Grund dort und
die anderen bleiben. Keine Schlüssel und keine Config-Werte im Text.
"""
from __future__ import annotations

from datetime import datetime

FEHLER_MAX = 6000                 # Zeichen aus dem Debugbericht (Sicht „fehler“)
PROZESSE_MAX = 8                  # Prozesse mit Grafikspeicher in der Tabelle
AUFFAELLIG_MAX = 15               # neueste Befunde des Fehler-Wächters


def _gb(mb: int | float) -> str:
    return f"{mb / 1024:.1f} GB"


def _programm() -> list[str]:
    import sys

    import debugbericht as D
    import paths
    import version
    zeilen = [f"- Version: {version.build_label()}",
              f"- Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}, "
              f"{'exe' if paths.FROZEN else 'Skript'}",
              f"- Programm-Ordner: {paths.INSTALL}",
              f"- Daten-Ordner: {paths.DATEN}"]
    try:
        import config
        import models
        ref = str(config.load().get("model") or "")
        anbieter = models.split_ref(ref)[0] if ref else ""
        zeilen.append(f"- Aktives Modell: {ref or '–'} (Anbieter {anbieter or '–'})")
    except Exception as exc:
        zeilen.append(f"- Aktives Modell: nicht ermittelbar ({exc})")
    zeilen.append(f"- Debugbericht: {'an' if D.an() else 'aus'}"
                  + (f", Datei {D.datei().name}" if D.datei() is not None else ""))
    return zeilen


def _rechner() -> list[str]:
    import systemprofil as SP
    p = SP.erkennen()
    zeilen = [f"- CPU: {p.cpu} ({p.kerne} Kerne / {p.threads} Threads"
              + (", AVX-512" if p.avx512 else ", AVX2" if p.avx2 else "") + ")",
              f"- RAM: {_gb(p.ram_mb)} gesamt, {_gb(p.ram_frei_mb)} frei",
              f"- Windows: {p.windows or '?'}" + (f" (Build {p.build})" if p.build else ""),
              f"- Platte des Programm-Ordners: {p.platte_frei / 2**30:.0f} GB frei"]
    for g in p.gpus:
        teile = [g.name, _gb(g.vram_mb)]
        if g.cuda_cap:
            teile.append(f"Rechen-Stufe {g.cuda_cap}")
        if g.treiber:
            teile.append(f"Treiber {g.treiber}")
        if g.integriert:
            teile.append("im Prozessor")
        zeilen.append("- Grafikkarte: " + ", ".join(teile))
    if p.cuda_max:
        zeilen.append(f"- Höchste CUDA-Version des Treibers: {p.cuda_max:g}")
    return zeilen


def _grafikspeicher() -> list[str]:
    import debugbericht as D
    zeilen = []
    t = D.torch_speicher()
    if t:
        zeilen.append(f"- Dieser Prozess (torch): {t['belegt']} MB belegt, {t['reserviert']} MB reserviert; "
                      f"Karte: {t['frei']} MB frei von {t['gesamt']} MB")
    else:
        zeilen.append("- Dieser Prozess hat CUDA noch nicht benutzt (kein Modell auf der Grafikkarte).")
    liste = D.prozesse()
    if liste:
        zeilen.append("- Je Prozess (Windows-Zähler; dwm.exe und andere Windows-Prozesse belegen immer einen "
                      "Grundstock):")
        zeilen += [f"  - {name} (PID {pid}): {mb} MB" for name, pid, mb in liste[:PROZESSE_MAX]]
    else:
        zeilen.append("- Je Prozess: Zähler nicht verfügbar.")
    return zeilen


def _systemcheck() -> list[str]:
    import syscheck
    rep = syscheck.report(deep=True)
    t = rep["torch"]
    if t.get("da"):
        zeilen = [f"- torch {t.get('version')} (CUDA {t.get('cuda') or '–'}), Grafikkarte "
                  f"{'nutzbar' if t.get('gpu_nutzbar') else 'NICHT nutzbar'}"
                  + ("" if rep.get("torch_passt") is None else
                     f", kennt {rep['sm']}: {'ja' if rep['torch_passt'] else 'NEIN'}")]
        if t.get("fehler"):
            zeilen.append(f"- torch-Fehler beim Import: {t['fehler']}")
    else:
        zeilen = ["- torch: nicht installiert"]
    fehlend = [p for p in rep["pakete"] if not p["da"]]
    if fehlend:
        zeilen.append("- Fehlende Pakete: " + ", ".join(
            f"{p['name']}{' (Pflicht)' if p['pflicht'] else ''}" for p in fehlend))
    else:
        zeilen.append("- Alle bekannten Pakete sind da.")
    if rep["transformers_ok"] is False:
        zeilen.append("- transformers ist veraltet (unter 5.10).")
    st = rep["stock"]
    zeilen.append(f"- Lokale Sprachmodelle (GGUF, eigener Motor): {st.get('gguf', 0)} · Bildmodelle (Krea 2): "
                  f"{st['bildmodelle']} · Ollama: {'erreichbar' if st['ollama'] else 'nicht erreichbar'}")
    if rep.get("extlibs"):
        zeilen.append(f"- Externe Bibliotheken: {rep['extlibs']}")
    offen = syscheck.todo(rep)
    zeilen.append("- Was fehlt laut Systemcheck: " + ("nichts" if not offen else ""))
    zeilen += [f"  - {x}" for x in offen]
    return zeilen


def _auffaelligkeiten() -> list[str]:
    import fehlerwaechter as FW
    liste = FW.befunde()[-AUFFAELLIG_MAX:]
    if not liste:
        return ["- keine (der Fehler-Wächter hat nichts Ungewöhnliches gesehen)"]
    return [f"- {FW.kurz_text(b)}" for b in reversed(liste)]


def _fehler() -> list[str]:
    import debugbericht as D
    if not D.an():
        return ["- Der Debugbericht ist aus – keine Fehler-Zeitleiste (der Nutzer schaltet ihn mit /debug an ein)."]
    text = D.fuer_ki(grenze=FEHLER_MAX, was="fehler")
    if not text:
        return ["- Noch kein Debugbericht vorhanden."]
    return [text, "", "(Mehr Zusammenhang: Werkzeug `debugbericht`, was = kurz · speicher · alles.)"]


ABSCHNITTE = (("Programm", _programm), ("Auffälligkeiten (Fehler-Wächter, auch frühere Starts)", _auffaelligkeiten),
              ("Rechner", _rechner), ("Grafikspeicher jetzt", _grafikspeicher),
              ("Systemcheck", _systemcheck), ("Fehler aus dem Debugbericht", _fehler))


def bericht() -> str:
    """Alle Abschnitte als Text (Markdown)."""
    teile = [f"# Selbstdiagnose {datetime.now():%Y-%m-%d %H:%M}"]
    for titel, f in ABSCHNITTE:
        try:
            zeilen = f()
        except Exception as exc:
            zeilen = [f"- nicht ermittelbar: {type(exc).__name__}: {exc}"]
        teile.append(f"\n## {titel}\n" + "\n".join(zeilen))
    return "\n".join(teile)
