"""
doku.py - Offline-Doku für den Coding-Assistenten: offizielle Python-Doku und MDN (HTML, CSS, JS,
Web-APIs) einmal laden, in Abschnitte zerlegen und in einen Volltext-Index (SQLite FTS5) legen.

  /doku laden python   docs.python.org/<Version>/archives/python-<Version>-docs-text.zip (Textfassung)
  /doku laden mdn      GitHub mdn/content (nur files/en-us/web/{html,css,javascript,api})
  doku_suchen          Werkzeug der KI: Suche (frage) oder einen Treffer ganz lesen (id)

Volltext statt Vektoren: Funktions- und Eigenschaftsnamen werden wörtlich gefunden, das Einlesen
dauert Sekunden statt Stunden und braucht keine GPU. Ablage: <Datenordner>/Doku/.
"""
from __future__ import annotations

import io
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PYTHON_URL = "https://docs.python.org/{v}/archives/python-{v}-docs-text.zip"
MDN_URL = "https://codeload.github.com/mdn/content/tar.gz/refs/heads/main"
MDN_BEREICHE = ("html", "css", "javascript", "api")
MAX_DOWNLOAD = 600 * 1024 * 1024
ABSCHNITT = 4000
TREFFER = 6
LESEN_MAX = 6000
QUELLEN = ("python", "mdn")


def ordner() -> Path:
    try:
        import paths
        return Path(paths.DATEN) / "Doku"
    except Exception:
        return Path(tempfile.gettempdir()) / "NemiCLI-Doku"


def _db_pfad() -> Path:
    return ordner() / "doku.db"


def _verbinden() -> sqlite3.Connection:
    ordner().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_db_pfad())
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS seiten USING fts5("
                "quelle UNINDEXED, titel, pfad UNINDEXED, text, tokenize=\"unicode61 tokenchars '_'\")")
    con.execute("CREATE TABLE IF NOT EXISTS stand (quelle TEXT PRIMARY KEY, version TEXT, anzahl INTEGER)")
    return con


def stand() -> dict[str, tuple[str, int]]:
    """{Quelle: (Version, Abschnitte)} der geladenen Doku."""
    if not _db_pfad().is_file():
        return {}
    con = _verbinden()
    try:
        return {q: (v, n) for q, v, n in con.execute("SELECT quelle, version, anzahl FROM stand")}
    finally:
        con.close()


# --- Zerlegen ------------------------------------------------------------------

_RST_LINIE = re.compile(r"^([*=\-~^\"#+])\1{3,}\s*$")


def abschnitte_rst(text: str, titel_datei: str) -> list[tuple[str, str]]:
    """Python-Textdoku: Überschrift = Zeile mit Unterstrich aus *, =, -, ~ … → (Titel, Text)."""
    zeilen = text.splitlines()
    teile: list[tuple[str, list[str]]] = [(titel_datei, [])]
    i = 0
    while i < len(zeilen):
        z = zeilen[i]
        if i + 1 < len(zeilen) and z.strip() and _RST_LINIE.match(zeilen[i + 1]) and len(zeilen[i + 1].strip()) >= len(z.strip()) - 2:
            teile.append((z.strip(), []))
            i += 2
            continue
        if not _RST_LINIE.match(z):
            teile[-1][1].append(z)
        i += 1
    return _zuschneiden([(t, "\n".join(z).strip()) for t, z in teile])


_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_MAKRO = re.compile(r"\{\{\s*([A-Za-z_]+)(?:\(([^}]*)\))?\s*\}\}")


def _makro(m: re.Match) -> str:
    name, args = m.group(1).lower(), (m.group(2) or "")
    erstes = re.findall(r'"([^"]*)"|\'([^\']*)\'', args)
    wert = next((a or b for a, b in erstes), "")
    if name in ("htmlelement", "cssxref", "jsxref", "domxref", "httpheader", "svgelement", "glossary", "htmlattrxref"):
        return f"`{wert.split('/')[-1]}`" if wert else ""
    return ""


def abschnitte_md(text: str, pfad: str) -> list[tuple[str, str]]:
    """MDN-Markdown: Titel aus dem Kopf, Makros {{…}} entfernt, Abschnitte an '## '."""
    titel = pfad
    if (m := _FRONT.match(text)):
        if (t := re.search(r"^title:\s*(.+)$", m.group(1), re.M)):
            titel = t.group(1).strip().strip("'\"")
        text = text[m.end():]
    text = _MAKRO.sub(_makro, text)
    teile: list[tuple[str, list[str]]] = [(titel, [])]
    for z in text.splitlines():
        if z.startswith("## "):
            teile.append((f"{titel} – {z[3:].strip()}", []))
        else:
            teile[-1][1].append(z)
    return _zuschneiden([(t, "\n".join(z).strip()) for t, z in teile if not t.endswith(_MDN_OHNE)])


# Reine Linklisten und Tabellen-Platzhalter – im Index nur Rauschen.
_MDN_OHNE = ("– See also", "– Specifications", "– Browser compatibility")


def _zuschneiden(teile: list[tuple[str, str]]) -> list[tuple[str, str]]:
    raus = []
    for titel, text in teile:
        if len(text) < 40:
            continue
        while len(text) > ABSCHNITT:
            schnitt = text.rfind("\n\n", 0, ABSCHNITT)
            schnitt = schnitt if schnitt > ABSCHNITT // 2 else ABSCHNITT
            raus.append((titel, text[:schnitt].strip()))
            text = text[schnitt:].strip()
        raus.append((titel, text))
    return raus


# --- Laden -----------------------------------------------------------------------

def _herunterladen(url: str, ziel: Path, melde=None) -> None:
    import httpx
    geladen = 0
    with httpx.Client(headers={"User-Agent": "NemiCLI-Doku"}, timeout=60.0, follow_redirects=True) as c:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            with open(ziel, "wb") as f:
                for stueck in r.iter_bytes(1 << 20):
                    geladen += len(stueck)
                    if geladen > MAX_DOWNLOAD:
                        raise OSError(f"Download größer als {MAX_DOWNLOAD >> 20} MB – abgebrochen.")
                    f.write(stueck)
                    if melde:
                        melde(f"lade … {geladen >> 20} MB")


def _speichern(quelle: str, version: str, eintraege, melde=None) -> int:
    con = _verbinden()
    try:
        con.execute("DELETE FROM seiten WHERE quelle = ?", (quelle,))
        n = 0
        for titel, pfad, text in eintraege:
            con.execute("INSERT INTO seiten (quelle, titel, pfad, text) VALUES (?, ?, ?, ?)", (quelle, titel, pfad, text))
            n += 1
            if melde and n % 2000 == 0:
                melde(f"lege Index an … {n} Abschnitte")
        con.execute("INSERT OR REPLACE INTO stand VALUES (?, ?, ?)", (quelle, version, n))
        con.commit()
        return n
    finally:
        con.close()


def python_version() -> str:
    """Python-Version für die Doku: die des Projekt-Pythons im Coding-Assistenten, sonst NemiCLIs."""
    try:
        import coding
        import pyumgebung
        if (p := coding.arbeitsordner()) is not None:
            v = pyumgebung.umgebung(p).get("python") or ""
            if (m := re.match(r"(3\.\d+)", v)):
                return m.group(1)
    except Exception:
        pass
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def python_eintraege(zip_daten: bytes):
    with zipfile.ZipFile(io.BytesIO(zip_daten)) as z:
        for name in z.namelist():
            if not name.endswith(".txt") or "/whatsnew/" in name and not name.endswith("/index.txt"):
                continue
            rel = name.split("/", 1)[-1]
            text = z.read(name).decode("utf-8", errors="replace")
            for titel, abschnitt in abschnitte_rst(text, rel):
                yield titel, rel, abschnitt


def laden_python(version: str | None = None, melde=None) -> str:
    v = version or python_version()
    with tempfile.TemporaryDirectory() as tmp:
        ziel = Path(tmp) / "python.zip"
        _herunterladen(PYTHON_URL.format(v=v), ziel, melde)
        if melde:
            melde("lege Index an …")
        n = _speichern("python", v, python_eintraege(ziel.read_bytes()), melde)
    return f"Python-Doku {v}: {n} Abschnitte im Index."


def mdn_eintraege(tar_pfad: Path):
    muster = re.compile(r"^[^/]+/files/en-us/web/(" + "|".join(MDN_BEREICHE) + r")/(.+)/index\.md$")
    with tarfile.open(tar_pfad, "r|gz") as t:
        for eintrag in t:
            m = muster.match(eintrag.name)
            if not m or not eintrag.isfile():
                continue
            f = t.extractfile(eintrag)
            if f is None:
                continue
            text = f.read().decode("utf-8", errors="replace")
            rel = f"{m.group(1)}/{m.group(2)}"
            for titel, abschnitt in abschnitte_md(text, rel):
                yield titel, rel, abschnitt


def laden_mdn(melde=None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        ziel = Path(tmp) / "mdn.tar.gz"
        _herunterladen(MDN_URL, ziel, melde)
        if melde:
            melde("lege Index an …")
        n = _speichern("mdn", "main", mdn_eintraege(ziel), melde)
    return f"MDN (HTML, CSS, JavaScript, Web-APIs): {n} Abschnitte im Index."


def loeschen(quelle: str) -> str:
    if quelle not in stand():
        return f"'{quelle}' ist nicht geladen."
    con = _verbinden()
    try:
        con.execute("DELETE FROM seiten WHERE quelle = ?", (quelle,))
        con.execute("DELETE FROM stand WHERE quelle = ?", (quelle,))
        con.commit()
        con.execute("INSERT INTO seiten(seiten) VALUES('optimize')")
        con.commit()
    finally:
        con.close()
    return f"'{quelle}' aus dem Doku-Index entfernt."


# --- Suchen ----------------------------------------------------------------------

def _anfrage(frage: str, verbinder: str) -> str:
    woerter = re.findall(r"[A-Za-z0-9_]+", frage)[:12]
    return f" {verbinder} ".join(f'"{w}"' for w in woerter)


def suchen(frage: str, quelle: str = "", anzahl: int = TREFFER) -> str:
    da = stand()
    if not da:
        return ("Noch keine Offline-Doku geladen. Der Nutzer kann sie mit /doku laden python bzw. "
                "/doku laden mdn holen (oder du öffnest ihm das Menü mit menue_oeffnen \"/doku\").")
    quelle = (quelle or "").strip().lower()
    if quelle and quelle not in da:
        return f"'{quelle}' ist nicht geladen. Geladen: {', '.join(da)}."
    if not re.search(r"[A-Za-z0-9_]", frage or ""):
        return "Feld 'frage' fehlt, z. B. \"pathlib glob\" oder \"css grid-template-columns\"."
    con = _verbinden()
    try:
        zeilen = []
        for verbinder in ("AND", "OR"):
            sql = ("SELECT rowid, quelle, titel, pfad, snippet(seiten, 3, '«', '»', ' … ', 30) FROM seiten "
                   "WHERE seiten MATCH ?" + (" AND quelle = ?" if quelle else "")
                   + " ORDER BY bm25(seiten, 0.0, 10.0, 0.0, 1.0) LIMIT ?")
            werte = [_anfrage(frage, verbinder)] + ([quelle] if quelle else []) + [max(1, min(int(anzahl), 12))]
            try:
                zeilen = con.execute(sql, werte).fetchall()
            except sqlite3.OperationalError:
                zeilen = []
            if zeilen:
                break
    finally:
        con.close()
    if not zeilen:
        return f"Nichts gefunden zu '{frage}'. Andere Wörter versuchen (englische Begriffe, Funktionsnamen)."
    version = {q: v for q, (v, _) in da.items()}
    teile = [f"[{rid}] {q} {version.get(q, '')} · {titel} ({pfad})\n    {' '.join(schnipsel.split())}"
             for rid, q, titel, pfad, schnipsel in zeilen]
    return "\n".join(teile) + "\n\nGanzen Abschnitt lesen: doku_suchen mit id (Zahl in eckigen Klammern)."


def lesen(rid) -> str:
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return "id muss eine Zahl aus den Suchtreffern sein."
    if not _db_pfad().is_file():
        return "Noch keine Offline-Doku geladen."
    con = _verbinden()
    try:
        z = con.execute("SELECT quelle, titel, pfad, text FROM seiten WHERE rowid = ?", (rid,)).fetchone()
    finally:
        con.close()
    if not z:
        return f"Keinen Abschnitt mit id {rid}."
    text = z[3][:LESEN_MAX] + (" …" if len(z[3]) > LESEN_MAX else "")
    return f"{z[0]} · {z[1]} ({z[2]})\n\n{text}"


def status_text() -> str:
    da = stand()
    if not da:
        return f"Keine Offline-Doku geladen. Ablage: {ordner()}"
    return "Offline-Doku: " + " · ".join(f"{q} {v} ({n} Abschnitte)" for q, (v, n) in da.items())
