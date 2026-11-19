"""eigenbereich.py – Der eigene Bereich jeder Persönlichkeit: Profile/<Name>/Eigenes/.

Dort legt die KI ohne Rückfrage an, was sie will (Notizen, Ideen, Pläne, Entwürfe). Jeder Ordner darin trägt
eine Datei `.siegel`: HMAC-SHA256 über Kennung des Bereichs und Pfad des Ordners, mit einem Geheimnis je
Persönlichkeit. Das Geheimnis liegt außerhalb des Daten-Ordners (%LOCALAPPDATA%\\NemiCLI\\bereiche.json),
verschlüsselt mit Windows DPAPI (an das Windows-Konto gebunden); die KI sieht es nie.

- Ordner, die die KI mit ihren Werkzeugen anlegt, verschiebt oder kopiert, versiegelt NemiCLI danach.
- Ordner ohne gültiges Siegel (von außen angelegt, hineingeschoben, Siegel kopiert) sind gesperrt: kein
  Lesen, kein Schreiben, unsichtbar in Listen. Der Nutzer prüft und gibt sie mit /eigenes frei.
- Der Pfad steckt im Siegel: ein kopiertes Siegel gilt an anderer Stelle nicht.
- Siegel sind normale Dateien (keine versteckten oder System-Attribute).
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ORDNER = "Eigenes"
SIEGEL = ".siegel"
MAX_TIEFE = 6                          # Ordner-Ebenen unter Eigenes/
MAX_BYTES = 500 * 2 ** 20              # Gesamtgröße des Bereichs
MAX_ORDNER = 2000                      # so viele Ordner versiegelt NemiCLI auf einmal höchstens
_ENTROPIE = b"NemiCLI-Eigenbereich-1"  # zusätzlicher DPAPI-Schlüsselteil, nur für diesen Zweck
_GROESSE_TTL = 30.0

# Werkzeuge, die im Bereich etwas anlegen oder ändern (Grenzen prüfen, danach versiegeln)
SCHREIBEND = {"datei_schreiben", "datei_bearbeiten", "ordner_erstellen", "pdf_erstellen", "verschieben",
              "datei_kopieren"}
PFAD_FELDER = ("pfad", "von", "nach", "ziel", "quelle", "path")

_GEHEIM: dict[str, bytes] = {}
_SIEGEL_CACHE: dict[str, tuple[tuple, bool]] = {}
_GROESSE: dict[str, tuple[float, int]] = {}


# --- Geheimnis je Persönlichkeit ------------------------------------------------------------------------

_LOCALAPPDATA_START = os.environ.get("LOCALAPPDATA")


def schluessel_datei() -> Path:
    basis = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    try:
        import paths
        if paths._testlauf() and os.environ.get("LOCALAPPDATA") == _LOCALAPPDATA_START:
            basis = str(paths.TESTDATEN / "_lokal")          # Tests fassen die echten Schlüssel nie an
    except ImportError:
        pass
    return Path(basis) / "NemiCLI" / "bereiche.json"


def _dpapi(daten: bytes, schuetzen: bool) -> bytes:
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(d: bytes):
        puffer = ctypes.create_string_buffer(d, len(d))
        return _Blob(len(d), ctypes.cast(puffer, ctypes.POINTER(ctypes.c_char))), puffer

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    rein, _p1 = blob(daten)
    zusatz, _p2 = blob(_ENTROPIE)
    raus = _Blob()
    f = crypt32.CryptProtectData if schuetzen else crypt32.CryptUnprotectData
    if not f(ctypes.byref(rein), None, ctypes.byref(zusatz), None, None, 0x1, ctypes.byref(raus)):  # UI_FORBIDDEN
        raise OSError("DPAPI fehlgeschlagen")
    try:
        return ctypes.string_at(raus.pbData, raus.cbData)
    finally:
        kernel32.LocalFree(raus.pbData)


def _register_lesen() -> dict | None:
    """Einträge der Schlüsseldatei; {} nur, wenn es sie noch nicht gibt. None = da, aber nicht lesbar –
    dann wird nichts angelegt, sonst überschriebe der neue Schlüssel alle anderen."""
    datei = schluessel_datei()
    try:
        d = json.loads(datei.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return d.get("schluessel") if isinstance(d, dict) and isinstance(d.get("schluessel"), dict) else None


_SPERRE_ALT_S = 30.0                   # so alte Sperrdateien stammen von einem abgebrochenen Lauf
_REGISTER_LOCK = threading.Lock()


def _register_sperre(ziel: Path, warten_s: float = 5.0) -> Path | None:
    """Sperrdatei neben der Schlüsseldatei (über Prozesse hinweg). None, wenn sie nicht zu bekommen ist."""
    sperre = ziel.with_name(ziel.name + ".lock")
    ende = time.monotonic() + warten_s
    while True:
        try:
            os.close(os.open(sperre, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return sperre
        except FileExistsError:
            try:
                if time.time() - sperre.stat().st_mtime > _SPERRE_ALT_S:
                    sperre.unlink()
                    continue
            except OSError:
                pass
        except OSError:
            return None
        if time.monotonic() > ende:
            return None
        time.sleep(0.05)


def _entschluesseln(wert) -> bytes | None:
    try:
        g = _dpapi(base64.b64decode(str(wert)), schuetzen=False)
    except (OSError, ValueError, AttributeError):
        return None
    return g if len(g) == 32 else None


def geheimnis(key: str, anlegen: bool = False) -> bytes | None:
    """Geheimnis der Persönlichkeit `key`. Fehlt es, legt `anlegen` eines an. Ist der Eintrag da, lässt sich
    aber nicht entschlüsseln (anderes Windows-Konto, verändert), bleibt es bei None – nie still ersetzen.
    Ebenso, wenn die Schlüsseldatei da, aber nicht lesbar ist."""
    key = str(key or "").casefold()
    if not key:
        return None
    if key in _GEHEIM:
        return _GEHEIM[key]
    register = _register_lesen()
    if register is None:
        return None
    if key in register:
        if (g := _entschluesseln(register[key])) is not None:
            _GEHEIM[key] = g
        return g
    if not anlegen:
        return None
    ziel = schluessel_datei()
    with _REGISTER_LOCK:
        try:
            ziel.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        if (sperre := _register_sperre(ziel)) is None:
            return None
        try:
            register = _register_lesen()             # unter der Sperre neu lesen: ein anderer Lauf kann geschrieben haben
            if register is None:
                return None
            if key in register:
                if (g := _entschluesseln(register[key])) is not None:
                    _GEHEIM[key] = g
                return g
            g = secrets.token_bytes(32)
            register[key] = base64.b64encode(_dpapi(g, schuetzen=True)).decode("ascii")
            tmp = ziel.with_name(f"{ziel.name}.{os.getpid()}.{secrets.token_hex(4)}.neu")
            try:
                tmp.write_text(json.dumps({"v": 1, "schluessel": register}, indent=1), encoding="utf-8")
                os.replace(tmp, ziel)
            finally:
                if tmp.exists():
                    tmp.unlink()
        except (OSError, AttributeError):
            return None
        finally:
            try:
                sperre.unlink()
            except OSError:
                pass
    _GEHEIM[key] = g
    return g


_SCHLUESSEL_STR: dict[str, str] = {}


def ist_schluessel_datei(pfad: Path) -> bool:
    """Liegt `pfad` (schon aufgelöst) im Schlüsselordner – Schlüssel der Bereiche, Ordnerkennung, Zeitplan des
    Vollscans – oder ist es der Ordner selbst? Ohne Dateizugriff – läuft in Suchläufen je Datei."""
    ort = os.environ.get("LOCALAPPDATA", "")
    if ort not in _SCHLUESSEL_STR:
        try:
            _SCHLUESSEL_STR[ort] = str(schluessel_datei().parent.resolve()).casefold()
        except OSError:
            return False
    p, ordner = str(pfad).casefold(), _SCHLUESSEL_STR[ort]
    return p == ordner or p.startswith(ordner + os.sep)


# --- Siegel ---------------------------------------------------------------------------------------------

def _rel(root: Path, ordner: Path) -> str:
    """Pfad unter dem Bereich, so wie er ins Siegel eingeht (Windows: Groß/Klein egal)."""
    return "/".join(ordner.relative_to(root).parts).casefold()


def _mac(g: bytes, kennung: str, rel: str) -> str:
    return hmac.new(g, f"v1|{kennung}|{rel}".encode("utf-8"), hashlib.sha256).hexdigest()


def _lesen(ordner: Path) -> dict | None:
    try:
        d = json.loads((ordner / SIEGEL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("siegel"), str) else None


def _schreiben(ordner: Path, daten: dict) -> None:
    (ordner / SIEGEL).write_text(json.dumps(daten), encoding="utf-8")
    _SIEGEL_CACHE.pop(str(ordner).casefold(), None)
    _KENNUNG_CACHE.pop(str(ordner).casefold(), None)


def _stand(ordner: Path) -> tuple:
    try:
        st = (ordner / SIEGEL).stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return (0, 0)


_KENNUNG_CACHE: dict[str, tuple[tuple, str | None]] = {}


def _kennung(root: Path, g: bytes) -> str | None:
    """Kennung des Bereichs aus dem Wurzel-Siegel – None, wenn es fehlt oder nicht passt."""
    schluessel = str(root).casefold()
    stand = (_stand(root), g)
    if (c := _KENNUNG_CACHE.get(schluessel)) and c[0] == stand:
        return c[1]
    d = _lesen(root)
    k = None
    if d and isinstance(d.get("kennung"), str) and len(d["kennung"]) == 32 \
            and hmac.compare_digest(d["siegel"], _mac(g, d["kennung"], "")):
        k = d["kennung"]
    _KENNUNG_CACHE[schluessel] = (stand, k)
    return k


def _ok(ordner: Path, root: Path, g: bytes, kennung: str) -> bool:
    schluessel = str(ordner).casefold()
    stand = (_stand(ordner), kennung)
    if (c := _SIEGEL_CACHE.get(schluessel)) and c[0] == stand:
        return c[1]
    if ordner == root:
        ok = _kennung(root, g) == kennung
    else:
        d = _lesen(ordner)
        ok = bool(d) and hmac.compare_digest(d["siegel"], _mac(g, kennung, _rel(root, ordner)))
    _SIEGEL_CACHE[schluessel] = (stand, ok)
    return ok


def _versiegeln(ordner: Path, root: Path, g: bytes, kennung: str) -> None:
    _schreiben(ordner, {"v": 1, "siegel": _mac(g, kennung, _rel(root, ordner))})


def _unter(p: Path, root: Path) -> bool:
    pl, rl = str(p).casefold(), str(root).casefold().rstrip("\\/")
    return pl == rl or pl.startswith(rl + os.sep)


# --- Wo, und wem -------------------------------------------------------------------------------------------

def wurzel(profil_ordner: str | None = None) -> Path:
    """Profile/<Name>/Eigenes – für die aktive Persönlichkeit, wenn kein Ordnername angegeben ist."""
    import profilordner
    return (profilordner.wurzel() / (profil_ordner or profilordner.aktiv()) / ORDNER).resolve()


def _aktiv() -> tuple[str, Path]:
    import persoenlichkeiten as PS
    import profilordner
    p = PS.active()
    return p.key, wurzel(profilordner.ordnername(p.name))


def bereit(key: str | None = None, root: Path | None = None) -> tuple[Path, bytes, str] | None:
    """Bereich anlegen und das Wurzel-Siegel setzen, wenn er fehlt (oder leer und ohne Siegel ist).
    (Wurzel, Geheimnis, Kennung) – None, wenn der Bereich da, aber nicht gültig versiegelt ist."""
    if key is None or root is None:
        key, root = _aktiv()
    g = geheimnis(key, anlegen=True)
    if g is None:
        return None
    if root.is_dir():
        if (k := _kennung(root, g)) is not None:
            return root, g, k
        if any(root.iterdir()):
            return None                          # Inhalt ohne gültiges Wurzel-Siegel: der Nutzer entscheidet
    root.mkdir(parents=True, exist_ok=True)
    kennung = secrets.token_hex(16)
    _schreiben(root, {"v": 1, "kennung": kennung, "siegel": _mac(g, kennung, "")})
    return root, g, kennung


def sperre(pfad: Path, root: Path, key: str) -> Path | None:
    """Liegt `pfad` (aufgelöst) im Bereich und dort unter einem Ordner ohne gültiges Siegel – oder ist es ein
    Siegel selbst? Dann den gesperrten Ort, sonst None. Ein Bereich, den es noch nicht gibt, sperrt nichts."""
    if not _unter(pfad, root):
        return None
    teile = pfad.relative_to(root).parts
    if any(t.casefold() == SIEGEL for t in teile):
        return pfad
    if not root.is_dir():
        return None
    g = geheimnis(key)
    kennung = _kennung(root, g) if g else None
    if kennung is None:
        return root
    ordner = root
    for t in teile:
        ordner = ordner / t
        if not ordner.is_dir():
            break
        if not _ok(ordner, root, g, kennung):
            return ordner
    return None


def sperre_aktiv(pfad: str) -> Path | None:
    """sperre() für die aktive Persönlichkeit."""
    try:
        key, root = _aktiv()
        return sperre(Path(pfad).resolve(), root, key)
    except Exception:
        return None


# --- Vor und nach einer Aktion -------------------------------------------------------------------------

@dataclass
class Plan:
    root: Path | None = None
    g: bytes = b""
    kennung: str = ""
    neu: list[Path] = field(default_factory=list)          # Ordner, die es vorher nicht gab
    baum: Path | None = None                                 # verschobener/kopierter Ordner am Ziel
    gueltig: set[str] | None = None                          # dessen schon versiegelte Unterordner (relativ)


def _ziel(tool: str, action: dict) -> list[tuple[Path, bool]]:
    """(Pfad, ist_ordner) je Feld, das im Bereich etwas anlegt."""
    raus = []
    for f in PFAD_FELDER:
        v = action.get(f)
        if not isinstance(v, str) or not v.strip() or (tool in ("verschieben", "datei_kopieren") and f == "von"):
            continue
        p = Path(os.path.expandvars(v.strip().strip('"'))).expanduser()
        if tool in ("verschieben", "datei_kopieren") and f == "nach":
            von = Path(str(action.get("von") or ""))
            raus.append((p / von.name if p.is_dir() else p, von.is_dir()))
        else:
            raus.append((p, tool == "ordner_erstellen"))
    return raus


def groesse(root: Path) -> int:
    schluessel = str(root).casefold()
    if (c := _GROESSE.get(schluessel)) and time.monotonic() - c[0] < _GROESSE_TTL:
        return c[1]
    summe = 0
    for d, _ordner, dateien in os.walk(root):
        for n in dateien:
            try:
                summe += os.path.getsize(os.path.join(d, n))
            except OSError:
                pass
    _GROESSE[schluessel] = (time.monotonic(), summe)
    return summe


def _groesse_von(p: Path, grenze: int) -> int:
    """Größe einer Datei oder eines Ordners; zählt nur, bis `grenze` überschritten ist."""
    try:
        if p.is_file():
            return p.stat().st_size
    except OSError:
        return 0
    summe = 0
    for d, _ordner, dateien in os.walk(p):
        for n in dateien:
            try:
                summe += os.path.getsize(os.path.join(d, n))
            except OSError:
                pass
            if summe > grenze:
                return summe
    return summe


# Pfad unter Profile/ (auch mit Punkten am Ende oder als Kurzname): ohne Prüfung im Zweifel gesperrt.
_PROFIL_TEXT = re.compile(r"(?i)(?:^|[\\/])(?:profile[ .]*|profil~\d+)(?:[\\/]|$)")


def vorbereiten(action: dict) -> tuple[str | None, Plan | None]:
    """Vor einer schreibenden Aktion im eigenen Bereich: Grenzen prüfen und merken, was neu entsteht.
    (Fehlermeldung, None) sperrt; (None, None) = betrifft den Bereich nicht."""
    tool = action.get("tool", "")
    if tool not in SCHREIBEND:
        return None, None
    try:
        key, root = _aktiv()
        ziele = [(p.resolve(), o) for p, o in _ziel(tool, action)]
    except Exception:
        if any(isinstance(v := action.get(f), str) and _PROFIL_TEXT.search(v) for f in PFAD_FELDER):
            return ("Fehler: 🔒 Ob das Ziel in deinem eigenen Bereich liegt, ließ sich gerade nicht prüfen – "
                    "unter Profile/ ändert NemiCLI dann nichts. Sag dem Nutzer Bescheid."), None
        return None, None
    ziele = [(p, o) for p, o in ziele if _unter(p, root)]
    if not ziele:
        return None, None
    if (b := bereit(key, root)) is None:
        return ("Fehler: 🔒 Dein eigener Bereich ist gerade nicht gültig versiegelt – ändern geht nicht. "
                "Sag dem Nutzer Bescheid: Er prüft ihn mit /eigenes."), None
    plan = Plan(*b)
    for p, ist_ordner in ziele:
        ordner = p if ist_ordner else p.parent
        if ordner != root and len(ordner.relative_to(root).parts) > MAX_TIEFE:
            return (f"Fehler: Dein eigener Bereich geht höchstens {MAX_TIEFE} Ordner tief. Verweigert: {p}."), None
        o = ordner
        while o != root and not o.exists():
            plan.neu.append(o)
            o = o.parent
    von_roh = str(action.get("von") or "").strip() if tool in ("verschieben", "datei_kopieren") else ""
    von = Path(von_roh).resolve() if von_roh else None
    # Verschieben innerhalb des Bereichs macht ihn nicht größer; von außen hereingeholt schon.
    innen = von is not None and tool == "verschieben" and _unter(von, root)
    jetzt = 0 if innen else groesse(root)
    if not innen and (jetzt >= MAX_BYTES
                      or (von is not None and jetzt + _groesse_von(von, MAX_BYTES - jetzt) > MAX_BYTES)):
        return (f"Fehler: Dein eigener Bereich ist voll (höchstens {MAX_BYTES // 2 ** 20} MB). Räum erst auf – "
                "Löschen geht mit loeschen."), None
    if von is not None:
        ziel = next((p for p, o in ziele if o), None)
        if ziel is not None and von.is_dir():
            plan.baum = ziel
            if _unter(von, root):                 # innerhalb: nur, was schon versiegelt war, bleibt es
                plan.gueltig = {"/".join(Path(d).relative_to(von).parts).casefold()
                                for d, _o, _f in os.walk(von)
                                if _ok(Path(d), root, plan.g, plan.kennung)}
    return None, plan


def versiegeln(plan: Plan | None) -> None:
    """Nach einer gelungenen Aktion: neue Ordner und einen hereingeholten Ordner versiegeln."""
    if plan is None or plan.root is None:
        return
    _GROESSE.pop(str(plan.root).casefold(), None)
    for o in reversed(plan.neu):
        if o.is_dir():
            _versiegeln(o, plan.root, plan.g, plan.kennung)
    if plan.baum is None or not plan.baum.is_dir():
        return
    for n, (d, _ordner, _dateien) in enumerate(os.walk(plan.baum)):
        if n >= MAX_ORDNER:
            break
        rel = "/".join(Path(d).relative_to(plan.baum).parts).casefold()
        if plan.gueltig is None or rel in plan.gueltig:
            _versiegeln(Path(d), plan.root, plan.g, plan.kennung)


# --- Für den Nutzer (/eigenes) ------------------------------------------------------------------------------

def status() -> dict:
    """Stand des Bereichs der aktiven Persönlichkeit: Ort, gültig, Ordner, Größe, ungesiegelte Ordner."""
    key, root = _aktiv()
    raus = {"pfad": root, "da": root.is_dir(), "gueltig": False, "ordner": 0, "bytes": 0, "offen": []}
    if not root.is_dir():
        return raus
    g = geheimnis(key)
    kennung = _kennung(root, g) if g else None
    raus["gueltig"] = kennung is not None
    raus["bytes"] = groesse(root)
    for d, ordner, _dateien in os.walk(root):
        p = Path(d)
        if p != root:
            raus["ordner"] += 1
            if kennung is None or not _ok(p, root, g, kennung):
                raus["offen"].append(p)
                ordner.clear()                   # darunter ist ohnehin alles gesperrt
    return raus


def freigeben(ordner: Path | None = None) -> int:
    """Nur der Nutzer: `ordner` (oder den ganzen Bereich) samt Unterordnern als vertrauenswürdig versiegeln.
    Ohne gültiges Wurzel-Siegel bekommt der Bereich eine neue Kennung. Gibt die Zahl der Siegel zurück."""
    key, root = _aktiv()
    g = geheimnis(key, anlegen=True)
    if g is None:
        raise OSError("Der Schlüssel dieser Persönlichkeit lässt sich nicht lesen.")
    root.mkdir(parents=True, exist_ok=True)
    kennung = _kennung(root, g)
    if kennung is None:
        kennung = secrets.token_hex(16)
        _schreiben(root, {"v": 1, "kennung": kennung, "siegel": _mac(g, kennung, "")})
        ordner = root
    ziel = (ordner or root).resolve()
    if not _unter(ziel, root) or not ziel.is_dir():
        raise ValueError(f"{ziel} liegt nicht im eigenen Bereich.")
    n = 0
    for d, _o, _f in os.walk(ziel):
        p = Path(d)
        if p != root:
            _versiegeln(p, root, g, kennung)
            n += 1
        if n >= MAX_ORDNER:
            break
    _GROESSE.pop(str(root).casefold(), None)
    return n


def hinweis() -> str:
    """Für den System-Prompt: wo der eigene Bereich liegt und was dort gilt."""
    try:
        _key, root = _aktiv()
    except Exception:
        return ""
    return ("\n\n# Dein eigener Bereich 🗝️\n"
            f"`{root}` gehört nur dir. Hier legst du ab, was du für dich festhalten willst: Notizen, Ideen, Pläne, "
            "Entwürfe – in Ordnern, wie du magst, ohne Rückfrage (datei_schreiben, ordner_erstellen, verschieben, "
            "datei_kopieren). Leg deine eigenen Sachen hier ab, nicht verstreut im Daten-Ordner. Jeder Ordner, den du "
            "mit deinen Werkzeugen anlegst, wird versiegelt; ein Ordner ohne gültiges Siegel wurde nicht von dir "
            "angelegt und ist gesperrt – sag dem Nutzer Bescheid, er prüft ihn mit /eigenes. Grenzen: "
            f"{MAX_TIEFE} Ordner tief, {MAX_BYTES // 2 ** 20} MB. Löschen fragt wie überall.\n")
