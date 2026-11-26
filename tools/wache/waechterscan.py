"""Wächter-Scan: markierte Dateien, Ordner oder Laufwerke aus dem Rechtsklick-Menü prüfen.

Je Datei: Größe, Attribute, zusätzliche Datenströme (ADS), Download-Herkunft (Zone.Identifier, die Adresse
auch gegen die Schutzliste), doppelte Endung; bei Code und Skripten SHA-256 und Signatur, verglichen mit dem
letzten Vollscan. Ampel: rot = gefährliches Muster, gelb = ansehen, grün = unauffällig. Nur lesend, im
Hintergrundmodus von Windows; Ordner der Schutzsoftware, Verknüpfungen/Junctions und Cloud-Platzhalter
werden nicht betreten (wie beim Vollscan).

Mehrfachauswahl: Explorer startet je markiertem Eintrag einen eigenen Prozess. Jeder legt seinen Pfad in
AUSWAHL ab; wer die Sperrdatei bekommt, wartet, bis keine neuen Pfade mehr kommen, und prüft alle in einem
Fenster – die übrigen enden sofort.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import BERICHTE, ORDNER, ordner_anlegen
from . import vollscan as VS

AUSWAHL = ORDNER / "waechterscan_auswahl"
SPERRE = "sammler.lock"
SAMMEL_RUHE_S = 0.8              # so lange ohne neuen Pfad = Auswahl vollständig
SAMMEL_MAX_S = 8.0
SPERRE_ALT_S = 60.0
MAX_DATEIEN = 500_000

ROT, GELB, GRUEN = "rot", "gelb", "gruen"
_RANG = {ROT: 0, GELB: 1, GRUEN: 2}
_DOKUMENT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".jpg", ".jpeg", ".png",
             ".gif", ".mp3", ".mp4", ".avi", ".mov", ".zip", ".rar", ".7z", ".odt", ".csv", ".html", ".htm"}
_ZONE_FREMD = ("Internet", "gesperrte Seite")


# ---------------------------------------------------------------------------- Auswahl sammeln

def sammeln(pfad: str, *, ordner: Path | None = None, ruhe_s: float = SAMMEL_RUHE_S,
            max_s: float = SAMMEL_MAX_S) -> list[str] | None:
    """Pfad ablegen; der erste Prozess bekommt die ganze Auswahl, alle anderen None."""
    ordner = ordner or AUSWAHL
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{time.time_ns()}_{os.getpid()}.pfad").write_text(pfad, encoding="utf-8")
    sperre = ordner / SPERRE
    try:
        if sperre.exists() and time.time() - sperre.stat().st_mtime > SPERRE_ALT_S:
            sperre.unlink()                                   # Rest eines abgestürzten Sammlers
    except OSError:
        pass
    try:
        fd = os.open(sperre, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.close(fd)
    try:
        anfang = zuletzt_neu = time.time()
        zahl = -1
        while time.time() - anfang < max_s:
            jetzt_zahl = len(list(ordner.glob("*.pfad")))
            if jetzt_zahl != zahl:
                zahl, zuletzt_neu = jetzt_zahl, time.time()
            elif time.time() - zuletzt_neu >= ruhe_s:
                break
            time.sleep(0.1)
        pfade = []
        for datei in sorted(ordner.glob("*.pfad")):
            try:
                pfade.append(datei.read_text(encoding="utf-8").strip())
                datei.unlink()
            except OSError:
                continue
        return list(dict.fromkeys(p for p in pfade if p))
    finally:
        try:
            sperre.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------- Dateien finden

def _nicht_betreten(eintrag: os.DirEntry) -> bool:
    try:
        if eintrag.is_symlink() or eintrag.is_junction():
            return True
        attr = getattr(eintrag.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attr & VS._NICHT_LOKAL)


def dateien(pfade: list[str], abbruch=lambda: False, schutzrevier=None):
    """Alle Dateien unter den Pfaden (Ordner rekursiv). Liefert (pfad, None) oder (pfad, Grund) für Lücken."""
    from .herkunft import im_schutzrevier
    schutz = schutzrevier or im_schutzrevier
    n = 0
    stapel = [p for p in pfade]
    while stapel:
        if abbruch() or n >= MAX_DATEIEN:
            return
        p = stapel.pop()
        if schutz(p):
            yield p, "Ordner der Schutzsoftware – nicht betreten"
            continue
        if os.path.isfile(p):
            n += 1
            yield p, None
            continue
        try:
            with os.scandir(VS._lang(p)) as it:
                eintraege = list(it)
        except OSError as exc:
            yield p, VS.grund(exc)
            continue
        for e in eintraege:
            voll = VS._kurz(e.path)
            if _nicht_betreten(e):
                continue
            try:
                ist_ordner = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if ist_ordner:
                stapel.append(voll)
            else:
                n += 1
                yield voll, None
                if n >= MAX_DATEIEN:
                    return


# ---------------------------------------------------------------------------- eine Datei prüfen

@dataclass
class Fund:
    pfad: str
    groesse: int = 0
    ampel: str = GRUEN
    gruende: list[str] = field(default_factory=list)
    sha256: str = ""
    signatur: str = ""
    signierer: str = ""
    herkunft: str = ""
    stroeme: list[tuple[str, int]] = field(default_factory=list)

    def melden(self, ampel: str, grund: str) -> None:
        self.gruende.append(grund)
        if _RANG[ampel] < _RANG[self.ampel]:
            self.ampel = ampel


def doppelte_endung(name: str) -> bool:
    """rechnung.pdf.exe: ein Dokument-Name, hinter dem ein Programm steckt."""
    stamm, endung = os.path.splitext(name.lower())
    return endung in VS.CODE_ENDUNGEN and os.path.splitext(stamm)[1] in _DOKUMENT


def _herkunft_adressen(herkunft: str) -> list[str]:
    return [t.removeprefix("über ").strip() for t in herkunft.split(" · ")[1:] if "://" in t]


def _vollscan_stand(c: sqlite3.Connection | None, pfad: str) -> tuple[bool, str]:
    """(beim letzten Vollscan bekannt, damaliger SHA-256)."""
    if c is None:
        return False, ""
    try:
        d = c.execute("SELECT 1 FROM dateien WHERE pfad=?", (pfad,)).fetchone()
        k = c.execute("SELECT sha256 FROM code WHERE pfad=?", (pfad,)).fetchone()
    except sqlite3.Error:
        return False, ""
    return bool(d), (k[0] or "") if k else ""


def pruefen(pfad: str, *, vollscan_db: sqlite3.Connection | None = None, herkunft_modul=None,
            schutzliste=None) -> Fund:
    if herkunft_modul is None:
        from . import herkunft as _herkunft
        herkunft_modul = _herkunft
    f = Fund(pfad)
    name = os.path.basename(pfad)
    endung = os.path.splitext(name)[1].lower()
    try:
        st = os.stat(VS._lang(pfad))
    except OSError as exc:
        f.melden(GELB, f"nicht lesbar ({VS.grund(exc)})")
        return f
    f.groesse = st.st_size
    attr = getattr(st, "st_file_attributes", 0)
    code = endung in VS.CODE_ENDUNGEN

    if doppelte_endung(name):
        f.melden(ROT, "doppelte Endung – sieht aus wie ein Dokument, ist aber ein Programm")
    f.herkunft = VS.download_herkunft(pfad)
    if f.herkunft:
        if schutzliste is not None:
            for adresse in _herkunft_adressen(f.herkunft):
                liste = schutzliste(adresse)
                if liste:
                    f.melden(ROT, f"heruntergeladen von einer Adresse auf der Schutzliste ({liste})")
                    break
    for strom, groesse in VS.stroeme(pfad):
        if strom.lower() in VS._STROM_UEBLICH:
            continue
        f.stroeme.append((strom, groesse))
        if VS._strom_kopf(pfad, strom) == b"MZ":
            f.melden(ROT, f"Programm versteckt im Datenstrom „{strom}“")
        else:
            f.melden(GELB, f"zusätzlicher Datenstrom „{strom}“ ({groesse} Byte)")
    if code:
        f.sha256, f.signatur, f.signierer = VS._fingerabdruck(pfad, herkunft_modul)
        aus_dem_netz = f.herkunft.split(" · ")[0] in _ZONE_FREMD
        if f.signatur == "HashMismatch":
            f.melden(ROT, "Signatur passt nicht zum Inhalt – die Datei wurde nach dem Signieren verändert")
        elif f.signatur != "Valid":
            art = ("Skript" if f.signatur == "Skript" else
                   ("Programm" if endung in VS._PE else "Skript") + " ohne gültige Signatur")
            if aus_dem_netz:
                f.melden(ROT, f"{art} aus dem Internet")
            elif f.signatur not in ("Skript", herkunft_modul.SCHUTZ_STATUS):
                f.melden(GELB, f"{art} ({f.signatur})")
        if attr & (VS._VERSTECKT | VS._SYSTEM) and not herkunft_modul.ist_systemort(pfad):
            f.melden(GELB, "versteckt außerhalb der Systemordner")
        bekannt, sha_vorher = _vollscan_stand(vollscan_db, pfad)
        if sha_vorher and f.sha256 and sha_vorher != f.sha256:
            f.melden(GELB, "Inhalt seit dem letzten Vollscan verändert")
        elif vollscan_db is not None and not bekannt:
            f.gruende.append("seit dem letzten Vollscan neu")
    return f



# ---------------------------------------------------------------------------- ganzer Lauf

@dataclass
class Ergebnis:
    pfade: list[str]
    start: float = field(default_factory=time.time)
    ende: float = 0.0
    funde: list[Fund] = field(default_factory=list)
    luecken: list[tuple[str, str]] = field(default_factory=list)
    abgebrochen: bool = False

    def zaehlen(self) -> dict[str, int]:
        z = {ROT: 0, GELB: 0, GRUEN: 0}
        for f in self.funde:
            z[f.ampel] += 1
        return z

    def auffaellig(self) -> list[Fund]:
        return sorted((f for f in self.funde if f.ampel != GRUEN), key=lambda f: (_RANG[f.ampel], f.pfad.lower()))


def _vollscan_verbinden() -> sqlite3.Connection | None:
    try:
        if not VS.DB.exists():
            return None
        return sqlite3.connect(f"file:{VS.DB}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    except sqlite3.Error:
        return None


def _schutzliste():
    try:
        import schutzliste
        return schutzliste.gesperrt
    except Exception:
        return None


def laufen(pfade: list[str], *, abbruch=lambda: False, fortschritt=None) -> Ergebnis:
    """Alle Pfade prüfen; `fortschritt(n, pfad)` nach jeder Datei."""
    erg = Ergebnis(pfade=list(pfade))
    c = _vollscan_verbinden()
    liste = _schutzliste()
    try:
        for p, luecke in dateien(pfade, abbruch):
            if luecke:
                erg.luecken.append((p, luecke))
                continue
            erg.funde.append(pruefen(p, vollscan_db=c, schutzliste=liste))
            if fortschritt:
                fortschritt(len(erg.funde), p)
        erg.abgebrochen = abbruch()
    finally:
        if c is not None:
            c.close()
        erg.ende = time.time()
    return erg


_AMPEL_ZEICHEN = {ROT: "🔴", GELB: "🟡", GRUEN: "🟢"}


def zusammenfassung(erg: Ergebnis) -> str:
    z = erg.zaehlen()
    return (f"{len(erg.funde):,} Dateien · 🔴 {z[ROT]} · 🟡 {z[GELB]} · 🟢 {z[GRUEN]}".replace(",", ".")
            + (" · abgebrochen" if erg.abgebrochen else ""))


def bericht(erg: Ergebnis) -> str:
    zeilen = [f"# 🛡 Wächter-Scan {datetime.fromtimestamp(erg.start):%d.%m.%Y %H:%M}", "",
              f"**Geprüft:** {', '.join(erg.pfade)}", "",
              f"**Ergebnis:** {zusammenfassung(erg)} · {erg.ende - erg.start:.1f} s", ""]
    auff = erg.auffaellig()
    if auff:
        zeilen += ["## Auffällig", ""]
        for f in auff:
            zeilen.append(f"- {_AMPEL_ZEICHEN[f.ampel]} `{f.pfad}` – {'; '.join(f.gruende)}")
            details = [x for x in (f"Signatur: {f.signatur}" + (f" ({f.signierer})" if f.signierer else "")
                                   if f.signatur else "", f"Herkunft: {f.herkunft}" if f.herkunft else "",
                                   f"SHA-256: {f.sha256}" if f.sha256 else "") if x]
            if details:
                zeilen.append("  " + " · ".join(details))
        zeilen.append("")
    else:
        zeilen += ["Nichts Auffälliges gefunden.", ""]
    if erg.luecken:
        zeilen += ["## Nicht geprüft", ""] + [f"- `{p}` – {g}" for p, g in erg.luecken[:200]] + [""]
    zeilen.append("Rot = gefährliches Muster, Gelb = ansehen, Grün = unauffällig. Der Scan liest nur; "
                  "er ersetzt den Virenschutz nicht.")
    return "\n".join(zeilen)


def bericht_speichern(erg: Ergebnis, ordner: Path | None = None) -> Path:
    ordner = ordner or BERICHTE
    if ordner is BERICHTE:
        ordner_anlegen()
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"Waechterscan_{datetime.fromtimestamp(erg.start):%Y-%m-%d_%H-%M-%S}.md"
    pfad.write_text(bericht(erg), encoding="utf-8")
    return pfad


def nachricht_fuer(bericht_pfad: Path, erg: Ergebnis) -> str:
    return (f"Ich habe einen Wächter-Scan gemacht ({zusammenfassung(erg)}). Bitte lies den Bericht, beurteile die "
            f"auffälligen Dateien und sag mir, was ich tun soll: {bericht_pfad}")


def hintergrundmodus() -> None:
    VS._hintergrundmodus()
