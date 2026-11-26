"""
config.py - Merkt sich Einstellungen zwischen den Starts (Modell, Theme).

Speichert in einer kleinen JSON-Datei neben dem Programm.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import keyvault

try:                 # Config und .env gehoeren zum PROGRAMM, nicht zu den Daten.
    import paths as _paths                 # (sonst dreht es sich im Kreis:
    _ROOT = _paths.INSTALL                 #  paths liest die Config, um DATEN zu finden)
    if _paths._testlauf():                 # Test-Suite: echte Einstellungen und Schlüssel bleiben unberührt
        _ROOT = _paths.TESTDATEN
except Exception:
    _ROOT = Path(__file__).resolve().parent.parent            # Projekt-Wurzel
_FILE = _ROOT / "nemicli.config.json"
_ENV = _ROOT / ".env"


_GEHEIM_NAME = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")


def umgebung_ohne_geheimnisse(**extra: str) -> dict:
    """os.environ ohne Schlüssel, Tokens und Passwörter – für Programme, die die KI startet
    (PowerShell, Projekt-Python, Browser). Die Schlüssel braucht nur NemiCLI selbst."""
    env = {k: v for k, v in os.environ.items()
           if not (any(g in k.upper() for g in _GEHEIM_NAME) or k.upper().endswith("_KEY")
                   or k.upper().startswith(("NEMICLI_GUI_", "NEMICLI_KI_")))}
    env.update(extra)
    return env


def _is_secret(name: str) -> bool:
    """Sensibler Wert, der verschlüsselt gehört (alle API-Keys)."""
    return name.endswith("API_KEY")


letzter_fehler = ""          # warum das letzte Speichern scheiterte ("" = es ging)
_gemeldet: set[str] = set()


def _lesen(pfad: Path) -> dict | None:
    try:
        d = json.loads(pfad.read_text(encoding="utf-8"))
    except Exception:
        return None
    return d if isinstance(d, dict) else None


_GELESEN: tuple | None = None    # (Pfad, mtime_ns, Größe, Inhalt) – load() läuft auch beim Zeichnen der Oberfläche


def load() -> dict:
    """Einstellungen. Ist die Datei beschädigt (Absturz beim Schreiben), gilt die letzte gute
    Fassung nemicli.config.json.bak – sonst schriebe das nächste Speichern eine Config mit nur
    dem einen neuen Wert, und alle anderen Einstellungen wären weg.
    Neu gelesen wird nur, wenn sich die Datei geändert hat (auch durch die Wache); sonst kostete jeder
    Aufruf einen Plattenzugriff, den ein Virenscan gelegentlich über eine Sekunde aufhält."""
    global _GELESEN
    try:
        st = _FILE.stat()
        kennung = (str(_FILE), st.st_mtime_ns, st.st_size)
    except OSError:
        kennung = None
    if kennung is not None and _GELESEN is not None and _GELESEN[:3] == kennung:
        return copy.deepcopy(_GELESEN[3])
    d = _lesen(_FILE)
    if d is not None:
        if kennung is not None:
            _GELESEN = (*kennung, copy.deepcopy(d))
        return d
    if _FILE.exists():                   # beschädigt – gelöscht heißt dagegen: frisch anfangen
        return _lesen(_FILE.with_name(_FILE.name + ".bak")) or {}
    return {}


def _melden(text: str, art: str | None = None) -> None:
    """Einmal je Fehlerart sichtbar machen – nicht still schlucken. `art`: woran Gleiches erkannt wird (sonst
    der Text; Zwischendateien tragen wechselnde Namen)."""
    if (art or text) in _gemeldet:
        return
    _gemeldet.add(art or text)
    try:
        import ui
        ui.warn(f"⚙ Einstellungen nicht gespeichert: {text} ({_FILE})")
    except Exception:
        pass


def _hinweis(text: str) -> None:
    """Wie _melden, für Fälle, in denen trotzdem gespeichert wird."""
    if text in _gemeldet:
        return
    _gemeldet.add(text)
    try:
        import ui
        ui.warn(f"⚙ Einstellungen: {text} ({_FILE})")
    except Exception:
        pass


def _direkt_schreiben(pfad: Path, text: str) -> None:
    """Rückfall, wenn kein Tausch geht (die Datei ist von einem anderen Prozess ohne Löschrecht offen): an Ort und
    Stelle schreiben, bis auf die Platte. Die .bak daneben ist dann schon der alte, vollständige Stand."""
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def save(data: dict) -> bool:
    """Sicher speichern: Zwischendatei bis auf die Platte, dann tauschen (core/atomar.py); die bisherige Fassung
    bleibt als .bak. Ist die Datei gerade von einem anderen Prozess offen (die Wache liest sie oft), wird der Tausch
    nach einer Pause noch einmal versucht und zuletzt direkt geschrieben. False + Hinweis, wenn nichts ging."""
    global letzter_fehler, _GELESEN
    import atomar
    # Gleich große Fassung im selben Zeittakt hätte dieselbe Kennung: der eigene Stand gilt sofort.
    _GELESEN = None
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        if _lesen(_FILE) is not None:
            bak = _FILE.with_name(_FILE.name + ".bak")
            try:
                atomar.text_schreiben(bak, _FILE.read_text(encoding="utf-8"))
            except PermissionError:
                shutil.copyfile(_FILE, bak)
        try:
            atomar.text_schreiben(_FILE, text)
        except PermissionError:
            time.sleep(0.2)
            try:
                atomar.text_schreiben(_FILE, text)
            except PermissionError:
                _direkt_schreiben(_FILE, text)
        _GELESEN = None
        letzter_fehler = ""
        return True
    except Exception as e:
        letzter_fehler = f"{type(e).__name__}: {e}"
        _melden(letzter_fehler, f"{type(e).__name__} {getattr(e, 'errno', '')}")
        return False


_UPDATE_LOCK = threading.Lock()


class _Dateisperre:
    """Lesen–Ändern–Schreiben über Prozesse hinweg (Hauptprogramm und Wache schreiben beide):
    eine Sperrdatei, exklusiv angelegt; eine verwaiste (älter als 10 s) wird übernommen."""

    def __init__(self):
        self.pfad = _FILE.with_name(_FILE.name + ".lock")
        self.fd = None

    WARTEN_S = (3.0, 2.0)          # erster Versuch, nach einer Meldung ein zweiter

    def __enter__(self):
        for nr, warten in enumerate(self.WARTEN_S):
            ergebnis = self._versuchen(warten)
            if ergebnis != "belegt":
                return self
            if nr == 0:
                _hinweis("Sperrdatei belegt – zweiter Versuch")
        # Weiter ohne Sperre (sonst ginge die Änderung ganz verloren) – aber nicht still
        _hinweis("Sperrdatei weiter belegt – gespeichert ohne Sperre")
        return self

    def _versuchen(self, warten: float) -> str:
        """"frei" (Sperre gehalten), "ohne" (Ordner nicht beschreibbar) oder "belegt" (Zeit um)."""
        ende = time.time() + warten
        while time.time() < ende:
            try:
                self.fd = os.open(self.pfad, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return "frei"
            except FileExistsError:
                try:
                    if time.time() - self.pfad.stat().st_mtime > 10:
                        self.pfad.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                time.sleep(0.02)
            except OSError:
                return "ohne"                              # Ordner nicht beschreibbar: ohne Sperre weiter
        return "belegt"

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.pfad.unlink(missing_ok=True)


def update(**kwargs) -> bool:
    """Einzelne Werte ändern und speichern, der Rest bleibt erhalten. False = nicht gespeichert."""
    with _UPDATE_LOCK, _Dateisperre():
        data = load()
        data.update(kwargs)
        return save(data)


TRESOR_BEREICH = "schluessel"


def _tresor():
    """Der Tresor, wenn die Windows-Kryptografie da ist – sonst None (dann wie früher die .env)."""
    try:
        import tresor
        return tresor if tresor.verfuegbar() else None
    except Exception:
        return None


def _env_zeilen_ohne(namen: set[str]) -> int:
    """Zeilen dieser Namen aus der .env entfernen; die alte Datei wird geschreddert. Zahl der entfernten Zeilen."""
    if not _ENV.exists():
        return 0
    lines = _ENV.read_text(encoding="utf-8").splitlines()
    bleiben = [z for z in lines if z.split("=", 1)[0].strip().removeprefix("export ").strip() not in namen
               or "=" not in z]
    weg = len(lines) - len(bleiben)
    if weg:
        import tresor
        tmp = _ENV.with_name(f"{_ENV.name}.{os.getpid()}.tmp")
        tmp.write_text("\n".join(bleiben) + ("\n" if bleiben else ""), encoding="utf-8")
        tresor.schreddern(_ENV)
        os.replace(tmp, _ENV)
    return weg


def geheimnis_setzen(name: str, value: str) -> bool:
    """Schlüssel in den Tresor (verschlüsselt, Siegel neu) und sofort live in os.environ. Leer = entfernen."""
    t = _tresor()
    if t is None:
        return set_env(name, value) if value else False
    value = (value or "").strip()
    t.setzen(TRESOR_BEREICH, name, value or None)
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    _env_zeilen_ohne({name})                          # nie zusätzlich offen in der .env
    return True


def tresor_namen() -> list[str]:
    """Namen der Schlüssel im Tresor (ohne Werte)."""
    t = _tresor()
    return sorted(t.bereich(TRESOR_BEREICH)) if t is not None else []


def geheimnisse_laden() -> int:
    """Beim Start (nach load_dotenv): Schlüssel aus der .env in den Tresor umziehen (die .env verliert sie, die alte
    Datei wird geschreddert), dann alle Tresor-Schlüssel in os.environ. Gibt die Zahl der umgezogenen zurück."""
    t = _tresor()
    if t is None:
        decrypt_env()
        return 0
    umzug: dict[str, str] = {}
    if _ENV.exists():
        try:
            for line in _ENV.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                name, _, val = s.partition("=")
                name, val = name.removeprefix("export ").strip(), _env_wert(val)
                if not _is_secret(name) or not val:
                    continue
                klar = keyvault.decrypt(val) if keyvault.is_encrypted(val) else val
                if klar:
                    umzug[name] = klar
        except OSError:
            umzug = {}
    if umzug:
        b = t.bereich(TRESOR_BEREICH)
        b.update(umzug)
        t.bereich_setzen(TRESOR_BEREICH, b)
        _env_zeilen_ohne(set(umzug))
    for name in list(os.environ):                     # was load_dotenv aus der alten .env geladen hatte
        if _is_secret(name) and keyvault.is_encrypted(os.environ[name]):
            os.environ.pop(name, None)
    for name, wert in t.bereich(TRESOR_BEREICH).items():
        os.environ[name] = wert
    return len(umzug)


def _tresor_vernichtet(_grund: str) -> None:
    """Rückruf des Tresors: alle Schlüssel sind vernichtet – auch aus dem laufenden Prozess nehmen."""
    for name in list(os.environ):
        if _is_secret(name):
            os.environ.pop(name, None)


try:
    import tresor as _tresor_modul
    _tresor_modul.BEI_VERNICHTUNG.append(_tresor_vernichtet)
except Exception:
    pass


def set_env(name: str, value: str) -> bool:
    """Schreibt/aktualisiert einen Key in der .env UND setzt ihn sofort live
    (os.environ), damit kein Neustart nötig ist. Schlüssel (*API_KEY) gehen in den Tresor
    (geheimnis_setzen); nur ohne Windows-Kryptografie in die .env, dort im DPAPI-Umschlag. True bei Erfolg."""
    value = value.strip()
    if _is_secret(name) and _tresor() is not None:
        return geheimnis_setzen(name, value)
    stored = value
    if _is_secret(name) and value and keyvault.available():
        enc = keyvault.encrypt(value)
        if enc:
            stored = enc                  # verschlüsselt auf die Platte
    try:
        lines = _ENV.read_text(encoding="utf-8").splitlines() if _ENV.exists() else []
        found = False
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(f"{name}=") or stripped.startswith(f"{name} ="):
                lines[i] = f"{name}={stored}"
                found = True
                break
        if not found:
            lines.append(f"{name}={stored}")
        _env_schreiben(lines)
        os.environ[name] = value          # Klartext sofort wirksam, ohne Neustart
        return True
    except Exception:
        return False


def _env_schreiben(lines: list[str]) -> None:
    """.env atomar ersetzen – ein Absturz mitten im Schreiben löscht keine Schlüssel."""
    tmp = _ENV.with_name(f"{_ENV.name}.{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, _ENV)


def decrypt_env() -> None:
    """Nach dem Laden der .env: verschlüsselte Werte (MARKER) in os.environ durch
    ihren Klartext ersetzen, damit der Rest der App (os.getenv) sie normal nutzt.
    Beim Start einmal aufrufen (direkt nach load_dotenv())."""
    for name, val in list(os.environ.items()):
        if keyvault.is_encrypted(val):
            plain = keyvault.decrypt(val)
            if plain is not None:
                os.environ[name] = plain
            else:
                # Konnte nicht entschlüsselt werden (anderes Konto/PC) -> entfernen,
                # damit nicht versehentlich der Salat als Key verwendet wird.
                os.environ.pop(name, None)


def _env_wert(roh: str) -> str:
    """Wert einer .env-Zeile wie python-dotenv: ohne Anführungszeichen, ungequotet ohne „ # Kommentar“."""
    roh = roh.strip()
    if roh[:1] in ("'", '"'):
        ende = roh.find(roh[0], 1)
        return roh[1:ende] if ende > 0 else roh[1:]
    return re.sub(r"\s+#.*", "", roh).strip()


def secure_existing_keys() -> int:
    """Wandelt im .env noch im Klartext liegende API-Keys einmalig in den
    verschlüsselten Umschlag um (os.environ behält den Klartext). Gibt zurück,
    wie viele Keys dabei neu geschützt wurden. Mit Tresor: der Umzug dorthin (geheimnisse_laden)."""
    if _tresor() is not None:
        return geheimnisse_laden()
    if not keyvault.available() or not _ENV.exists():
        return 0
    try:
        lines = _ENV.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 0
    n = 0
    changed = False
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        name, _, val = line.partition("=")
        name, val = name.strip(), _env_wert(val)
        if _is_secret(name) and val and not keyvault.is_encrypted(val):
            enc = keyvault.encrypt(val)
            if enc:
                lines[i] = f"{name}={enc}"
                os.environ[name.removeprefix("export ").strip()] = val          # Klartext bleibt live
                changed = True
                n += 1
    if changed:
        try:
            _env_schreiben(lines)
        except Exception:
            return 0
    return n
