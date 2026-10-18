"""
config.py - Merkt sich Einstellungen zwischen den Starts (Modell, Theme).

Speichert in einer kleinen JSON-Datei neben dem Programm.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import keyvault

try:                 # Config und .env gehoeren zum PROGRAMM, nicht zu den Daten.
    from paths import INSTALL as _ROOT     # (sonst dreht es sich im Kreis:
except Exception:                          #  paths liest die Config, um DATEN zu finden)
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


def load() -> dict:
    """Einstellungen. Ist die Datei beschädigt (Absturz beim Schreiben), gilt die letzte gute
    Fassung nemicli.config.json.bak – sonst schriebe das nächste Speichern eine Config mit nur
    dem einen neuen Wert, und alle anderen Einstellungen wären weg."""
    d = _lesen(_FILE)
    if d is not None:
        return d
    if _FILE.exists():                   # beschädigt – gelöscht heißt dagegen: frisch anfangen
        return _lesen(_FILE.with_name(_FILE.name + ".bak")) or {}
    return {}


def _melden(text: str) -> None:
    """Einmal je Fehlerart sichtbar machen – nicht still schlucken."""
    if text in _gemeldet:
        return
    _gemeldet.add(text)
    try:
        import ui
        ui.warn(f"⚙ Einstellungen nicht gespeichert: {text} ({_FILE})")
    except Exception:
        pass


def save(data: dict) -> bool:
    """Sicher speichern: Zwischendatei, dann tauschen; die bisherige Fassung bleibt als .bak.
    Ist die Datei gerade von einem anderen Prozess offen (die Wache liest sie oft), wird der
    Tausch kurz wiederholt und zuletzt direkt geschrieben. False + Hinweis, wenn nichts ging."""
    global letzter_fehler
    tmp = _FILE.with_name(_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if _lesen(_FILE) is not None:
            shutil.copyfile(_FILE, _FILE.with_name(_FILE.name + ".bak"))
        for versuch in range(6):
            try:
                os.replace(tmp, _FILE)
                break
            except PermissionError:
                if versuch == 5:
                    _FILE.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
                    tmp.unlink(missing_ok=True)
                    break
                time.sleep(0.05)
        letzter_fehler = ""
        return True
    except Exception as e:
        letzter_fehler = f"{type(e).__name__}: {e}"
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        _melden(letzter_fehler)
        return False


def update(**kwargs) -> bool:
    """Einzelne Werte ändern und speichern, der Rest bleibt erhalten. False = nicht gespeichert."""
    data = load()
    data.update(kwargs)
    return save(data)


def set_env(name: str, value: str) -> bool:
    """Schreibt/aktualisiert einen Key in der .env UND setzt ihn sofort live
    (os.environ), damit kein Neustart nötig ist. Sensible Keys werden dabei in
    den Windows-Umschlag (DPAPI) gepackt – in der Datei steht nur Buchstabensalat,
    in os.environ aber der nutzbare Klartext. True bei Erfolg."""
    value = value.strip()
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
        _ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ[name] = value          # Klartext sofort wirksam, ohne Neustart
        return True
    except Exception:
        return False


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


def secure_existing_keys() -> int:
    """Wandelt im .env noch im Klartext liegende API-Keys einmalig in den
    verschlüsselten Umschlag um (os.environ behält den Klartext). Gibt zurück,
    wie viele Keys dabei neu geschützt wurden."""
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
        name, val = name.strip(), val.strip()
        if _is_secret(name) and val and not keyvault.is_encrypted(val):
            enc = keyvault.encrypt(val)
            if enc:
                lines[i] = f"{name}={enc}"
                os.environ[name] = val          # Klartext bleibt live
                changed = True
                n += 1
    if changed:
        try:
            _ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            return 0
    return n
