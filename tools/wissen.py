"""wissen.py – Ordner „Wissen“ im Daten-Ordner: Dokumente für alle Persönlichkeiten.

PDF, Markdown und Text werden ganz gelesen, an Absatz- und Satzgrenzen in
Brocken zerlegt und vom Bibliothekar (indexdb.ingest_wissen) vektorisiert.

Doppelte Sätze: Ein Satz, der wortgleich schon in einer älteren Datei oder
weiter vorn in derselben steht, kommt kein zweites Mal in den Index. Verglichen
wird der ganze Satz, nur Leerraum wird vereinheitlicht – „Das Auto ist rot.“ und
„Das Auto ist blau.“ bleiben beide. Sätze mit weniger als MIN_WOERTER Wörtern
(Überschriften, „Ja.“) werden nie entfernt. Die Dateien selbst bleiben unverändert.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ORDNER = "Wissen"
ENDUNGEN = {".pdf", ".md", ".markdown", ".txt"}
MIN_WOERTER = 4
LISTE_MAX = 40                     # mehr Dateien → im Prompt nur die Ordner mit Anzahl
VERFAHREN = "2"                    # ändert sich Lesen oder Zerlegung, wird alles neu eingelesen
STEUER_MAX = 0.05                  # Anteil Steuerzeichen, ab dem ein „Text“ als binär gilt

_SATZENDE = re.compile(r"(?<=[.!?…])\s+")
_WORT = re.compile(r"[^\W\d_]{2,}")
_SEITE = re.compile(r"^(--- Seite \d+ ---)\n", re.M)


def ordner() -> Path:
    import indexdb
    return Path(indexdb._ROOT) / ORDNER


def dateien(basis: Path | None = None) -> list[Path]:
    """Lesbare Dateien unter Wissen/, älteste zuerst (sie behalten doppelte Sätze)."""
    basis = basis or ordner()
    if not basis.is_dir():
        return []
    gefunden = []
    for p in basis.rglob("*"):
        try:
            rel = p.relative_to(basis)
        except ValueError:
            continue
        if any(t.startswith(".") for t in rel.parts) or p.name.upper().startswith("LIES-MICH"):
            continue
        if p.suffix.lower() in ENDUNGEN and p.is_file():
            gefunden.append(p)
    return sorted(gefunden, key=lambda p: (p.stat().st_mtime_ns, name(p, basis).casefold()))


def name(p: Path, basis: Path | None = None) -> str:
    """Pfad relativ zu Wissen/ mit „/“ – Teil des ref (wissen:<name>)."""
    return p.relative_to(basis or ordner()).as_posix()


def stempel(liste: list[Path], basis: Path | None = None) -> str:
    """Fingerabdruck des Ordners: ändert sich, sobald eine Datei dazukommt, wegfällt oder sich ändert."""
    basis = basis or ordner()
    h = hashlib.sha1(VERFAHREN.encode())
    for p in liste:
        st = p.stat()
        h.update(f"{name(p, basis)}\0{st.st_mtime_ns}\0{st.st_size}\n".encode("utf-8"))
    return h.hexdigest()[:20]


def dekodieren(roh: bytes) -> str | None:
    """Bytes → Text (BOM für UTF-8/UTF-16, sonst UTF-8, sonst cp1252). None = binär."""
    if roh.startswith(b"\xef\xbb\xbf"):
        text = roh[3:].decode("utf-8", errors="replace")
    elif roh[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = roh.decode("utf-16", errors="replace")
    else:
        try:
            text = roh.decode("utf-8")
        except UnicodeDecodeError:
            text = roh.decode("cp1252", errors="replace")
    probe = text[:8192]
    if probe:
        steuer = sum(1 for c in probe if c < " " and c not in "\t\n\r\f")
        if "\x00" in probe or steuer / len(probe) >= STEUER_MAX:
            return None
    return text


def lesen(p: Path) -> str:
    """Ganzer Text der Datei. Die Art entscheidet der Inhalt, nicht die Endung:
    eine PDF mit Endung .md wird als PDF gelesen. Binäres und PDF ohne Textebene → ''."""
    with open(p, "rb") as f:
        kopf = f.read(5)
    if kopf == b"%PDF-":
        import anhang
        text = anhang.lese_pdf(p, grenze=10**9)[0]
        return _SEITE.sub(r"\1\n\n", text)            # Seitenmarke als eigener Absatz
    return dekodieren(p.read_bytes()) or ""


def _absaetze(text: str) -> list[list[str]]:
    """Text → Absätze → Sätze (Zeilenumbrüche im Absatz werden zu Leerzeichen,
    PDF bricht Sätze mitten im Satz um)."""
    raus = []
    for absatz in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        flach = " ".join(absatz.split())
        if flach:
            raus.append([s for s in _SATZENDE.split(flach) if s])
    return raus


def _vergleichbar(satz: str) -> bool:
    return len(_WORT.findall(satz)) >= MIN_WOERTER


def entdoppeln(texte: list[str]) -> tuple[list[list[str]], list[int]]:
    """Für jeden Text (Reihenfolge = Vorrang) die Absätze ohne bereits gesehene Sätze.
    Rückgabe: (Absätze je Text, Zahl entfernter Sätze je Text)."""
    gesehen: set[str] = set()
    alle, entfernt = [], []
    for text in texte:
        absaetze, weg = [], 0
        for saetze in _absaetze(text):
            bleibt = []
            for s in saetze:
                if _vergleichbar(s):
                    if s in gesehen:
                        weg += 1
                        continue
                    gesehen.add(s)
                bleibt.append(s)
            if bleibt:
                absaetze.append(" ".join(bleibt))
        alle.append(absaetze)
        entfernt.append(weg)
    return alle, entfernt


def brocken(kopf: str, absaetze: list[str], groesse: int) -> list[str]:
    """Absätze zu Brocken bis `groesse` Zeichen (mit Kopfzeile). Zu lange Absätze
    werden an Satzgrenzen geteilt, überlange Sätze hart."""
    platz = max(100, groesse - len(kopf) - 1)
    teile: list[str] = []
    for a in absaetze:
        if len(a) <= platz:
            teile.append(a)
            continue
        for s in _SATZENDE.split(a):
            while len(s) > platz:
                teile.append(s[:platz])
                s = s[platz:]
            if s:
                teile.append(s)
    raus, aktuell = [], ""
    for t in teile:
        if aktuell and len(aktuell) + 2 + len(t) > platz:
            raus.append(aktuell)
            aktuell = ""
        aktuell = f"{aktuell}\n\n{t}" if aktuell else t
    if aktuell:
        raus.append(aktuell)
    return [f"{kopf}\n{r}" for r in raus]


def prompt_hinweis() -> str:
    """Titel-Liste für den System-Prompt – nur Namen, kein Inhalt, nur was im Gedächtnis ist."""
    try:
        import indexdb
        namen = indexdb.wissen_namen()
    except Exception:
        return ""
    if not namen:
        return ""
    if len(namen) <= LISTE_MAX:
        zeilen = [f"- {n}" for n in namen]
    else:
        zaehler: dict[str, int] = {}
        for n in namen:
            oben = n.split("/", 1)[0] if "/" in n else "(oberste Ebene)"
            zaehler[oben] = zaehler.get(oben, 0) + 1
        zeilen = [f"- {o}/ ({z} Dateien)" if o != "(oberste Ebene)" else f"- {o}: {z} Dateien"
                  for o, z in sorted(zaehler.items(), key=lambda x: x[0].casefold())]
    return ("\n\n# 📚 Wissen (für alle Persönlichkeiten)\n"
            "Diese Dokumente sind schon als Text im Gedächtnis. Suchen: gedaechtnis_suchen mit quelle "
            "„wissen“; weiterlesen: gedaechtnis_lesen mit ref wissen:<Datei>. Die Dateien im Ordner "
            "Wissen NICHT mit datei_lesen öffnen – PDFs sind dort nicht lesbar, der Text steht im Gedächtnis.\n"
            + "\n".join(zeilen) + "\n")
