"""chatgpt_login.py – „Sign in with ChatGPT“ für die ChatGPT-Plan-Nutzung (offizieller OAuth-Weg für Open-Source-Apps).

Ablauf (developers.openai.com/siwc/token-sharing-open-source):
- Anmeldung im Browser: OpenID Connect mit PKCE (S256), ohne Client-Secret. Der Rückruf geht an einen kurzlebigen
  Empfänger nur auf 127.0.0.1 (Pfad /auth/callback), frische state- und nonce-Werte je Versuch.
- Erste Anmeldung über client_id „dynamic_agent_client“; die ausgegebene ID (oaiapp_…) wird gespeichert und danach
  verwendet. Je Installation eine zufällige Host-ID (urn:uuid:…), kein Bezug zur Person.
- Scopes: openid profile email offline_access resource.invoke chatgpt.tokens.use.direct, resource
  https://api.openai.com/v1. Ohne chatgpt.tokens.use.direct bleibt die Plan-Nutzung aus.
- Access-Token 1 h, Refresh-Token 30 Tage; jedes Erneuern liefert ein neues Refresh-Token.

Gespeichert wird in KONTO_DATEI (Programm-Ordner): Tokens nur mit dem Windows-Umschlag (DPAPI) verschlüsselt. Ohne
DPAPI bleiben sie nur im Speicher dieses Prozesses. Tokens erscheinen nie in Meldungen oder Logs.
Das ID-Token kommt direkt per TLS vom Token-Endpunkt; geprüft werden iss, aud, exp und nonce (OIDC Core 3.1.3.7).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx

import keyvault
import paths

AUSSTELLER = "https://auth.openai.com"
AUTH_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
RESSOURCE = "https://api.openai.com/v1"
MODELLE_URL = "https://api.openai.com/v1/models"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
REGISTRIERUNG = "dynamic_agent_client"
RUECKRUF_PFAD = "/auth/callback"
WUNSCH_PORT = 1455
APP_NAME = "NemiCLI"
WARTEN_S = 300.0
ERNEUERN_VORHER_S = 120                 # so lange vor Ablauf wird erneuert
NUTZUNG_URL = "https://chatgpt.com/#settings"

KONTO_DATEI = paths.INSTALL / "chatgpt_konto.json"
_GEHEIM = ("access_token", "refresh_token", "id_token")
# Antworten des Token-Endpunkts, nach denen nur eine neue Anmeldung hilft (Errors and recovery)
_NEU_ANMELDEN = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                 "refresh_token_invalidated", "refresh_token_reused"}

_sperre = threading.RLock()
_im_speicher: dict = {}                 # Tokens ohne DPAPI: nur für diesen Prozess


class AnmeldungNoetig(RuntimeError):
    """Kein gültiges Konto (nie angemeldet, abgemeldet oder Refresh-Token verbraucht)."""


class AnmeldeFehler(RuntimeError):
    """Die Anmeldung im Browser ist gescheitert oder wurde abgelehnt."""


# --- Speichern ---------------------------------------------------------------------------------------------

def _lesen() -> dict:
    try:
        roh = json.loads(KONTO_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        roh = {}
    if not isinstance(roh, dict):
        roh = {}
    for k in _GEHEIM:
        wert = roh.get(k)
        if isinstance(wert, str) and keyvault.is_encrypted(wert):
            roh[k] = keyvault.decrypt(wert) or ""
        elif k in _im_speicher:
            roh[k] = _im_speicher[k]
        else:
            roh.pop(k, None)                       # Klartext-Tokens auf der Platte gelten nicht
    return roh


def _schreiben(daten: dict) -> None:
    raus = dict(daten)
    for k in _GEHEIM:
        wert = raus.pop(k, None)
        if not wert:
            _im_speicher.pop(k, None)
            continue
        verschluesselt = keyvault.encrypt(wert) if keyvault.available() else None
        if verschluesselt:
            raus[k] = verschluesselt
        else:
            _im_speicher[k] = wert
    tmp = KONTO_DATEI.with_suffix(".tmp")
    tmp.write_text(json.dumps(raus, ensure_ascii=False, indent=1), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(KONTO_DATEI)


def _host_id(daten: dict) -> str:
    if not str(daten.get("ext_agent_host_id") or "").startswith("urn:uuid:"):
        daten["ext_agent_host_id"] = f"urn:uuid:{uuid.uuid4()}"
    return daten["ext_agent_host_id"]


# --- Hilfen ------------------------------------------------------------------------------------------------

def _b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode("ascii")


def challenge_von(verifier: str) -> str:
    """code_challenge zu einem code_verifier (RFC 7636, S256)."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def pkce_paar() -> tuple[str, str]:
    """(code_verifier, code_challenge) nach RFC 7636, Methode S256."""
    verifier = _b64url(secrets.token_bytes(48))          # 64 Zeichen
    return verifier, challenge_von(verifier)


def jwt_inhalt(token: str) -> dict:
    """Die Angaben (payload) eines JWT – ohne Signaturprüfung, nur für Tokens direkt vom Token-Endpunkt."""
    try:
        teil = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4)))
    except (IndexError, ValueError):
        return {}


def id_token_pruefen(id_token: str, client_id: str, nonce: str | None, jetzt: float | None = None) -> dict:
    """iss, aud, exp und (bei der Anmeldung) nonce des ID-Tokens prüfen; gibt die Angaben zurück."""
    a = jwt_inhalt(id_token)
    jetzt = time.time() if jetzt is None else jetzt
    aud = a.get("aud")
    if a.get("iss") != AUSSTELLER:
        raise AnmeldeFehler("Das ID-Token stammt nicht von OpenAI.")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise AnmeldeFehler("Das ID-Token gehört zu einer anderen App.")
    if not isinstance(a.get("exp"), (int, float)) or a["exp"] < jetzt - 60:
        raise AnmeldeFehler("Das ID-Token ist abgelaufen.")
    if nonce is not None and a.get("nonce") != nonce:
        raise AnmeldeFehler("Das ID-Token passt nicht zu dieser Anmeldung (nonce).")
    return a


def _fehlercode(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return ""
    if isinstance(d, dict):
        e = d.get("error")
        if isinstance(e, dict):
            return str(e.get("code") or e.get("type") or "")
        return str(e or d.get("code") or "")
    return ""


# --- Status ------------------------------------------------------------------------------------------------

def status() -> dict:
    """angemeldet (Konto mit Refresh-Token), plan (Plan-Nutzung freigegeben), email."""
    with _sperre:
        d = _lesen()
    return {"angemeldet": bool(d.get("refresh_token")), "plan": PLAN_SCOPE in str(d.get("scope") or "").split(),
            "email": str(d.get("email") or "")}


def nutzbar() -> bool:
    """Angemeldet und Plan-Nutzung freigegeben – ohne Netzanfrage."""
    s = status()
    return s["angemeldet"] and s["plan"]


def abmelden() -> None:
    """Tokens löschen. Client-ID und Host-ID bleiben: die nächste Anmeldung nutzt sie wieder."""
    with _sperre:
        d = _lesen()
        for k in (*_GEHEIM, "scope", "expires_at", "earliest_refresh_at", "email"):
            d.pop(k, None)
        _im_speicher.clear()
        if KONTO_DATEI.exists() or d:
            _schreiben(d)


# --- Tokens ------------------------------------------------------------------------------------------------

def _uebernehmen(d: dict, antwort: dict, nonce: str | None) -> None:
    """Token-Antwort in den Kontodatensatz (ID-Token geprüft, Ablauf berechnet)."""
    if not antwort.get("access_token"):
        raise AnmeldeFehler("OpenAI hat kein Zugangs-Token geliefert.")
    jetzt = time.time()
    if antwort.get("id_token"):
        angaben = id_token_pruefen(antwort["id_token"], d["client_id"], nonce, jetzt)
        d["id_token"] = antwort["id_token"]
        d["email"] = str(angaben.get("email") or d.get("email") or "")
        d["subject"] = str(angaben.get("sub") or d.get("subject") or "")
    d["access_token"] = antwort["access_token"]
    if antwort.get("refresh_token"):
        d["refresh_token"] = antwort["refresh_token"]
    if antwort.get("scope"):
        d["scope"] = antwort["scope"]
    d["expires_at"] = jetzt + float(antwort.get("expires_in") or 3600)
    frueh = antwort.get("earliest_refresh_at")
    d["earliest_refresh_at"] = float(frueh) if isinstance(frueh, (int, float)) else None
    d["saved_at"] = jetzt
    d["issuer"] = AUSSTELLER


def _erneuern(d: dict) -> None:
    r = httpx.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": d["client_id"],
                                    "refresh_token": d["refresh_token"], "resource": RESSOURCE}, timeout=30.0)
    if r.status_code >= 400:
        code = _fehlercode(r)
        if code in _NEU_ANMELDEN or r.status_code in (400, 401):
            for k in ("access_token", "refresh_token"):      # ID-Token und E-Mail bleiben als Hinweis für die neue Anmeldung
                d.pop(k, None)
            _schreiben(d)
            raise AnmeldungNoetig("Die ChatGPT-Anmeldung ist abgelaufen – bitte neu anmelden (/model chatgpt).")
        raise RuntimeError(f"ChatGPT-Anmeldung: Erneuern gescheitert ({r.status_code} {code}).".strip())
    _uebernehmen(d, r.json(), None)
    _schreiben(d)


def zugang() -> str:
    """Gültiges Access-Token für api.openai.com – vor Ablauf von selbst erneuert."""
    with _sperre:
        d = _lesen()
        if not d.get("refresh_token") or not d.get("client_id"):
            raise AnmeldungNoetig("Noch nicht mit ChatGPT angemeldet – /model → ChatGPT-Konto.")
        if PLAN_SCOPE not in str(d.get("scope") or "").split():
            raise AnmeldungNoetig("Die Nutzung deines ChatGPT-Plans ist nicht freigegeben – bitte neu anmelden "
                                  "und zustimmen (/model chatgpt).")
        jetzt = time.time()
        ablauf = float(d.get("expires_at") or 0)
        frueh = d.get("earliest_refresh_at")
        if not d.get("access_token") or jetzt >= ablauf - ERNEUERN_VORHER_S:
            if not frueh or jetzt >= float(frueh) or jetzt >= ablauf:
                _erneuern(d)
        return d["access_token"]


def modelle() -> list[tuple[str, str]]:
    """(slug, Anzeigename) der Modelle, die dieses Konto nutzen darf – in der Reihenfolge des Servers."""
    r = httpx.get(MODELLE_URL, headers={"Authorization": f"Bearer {zugang()}"}, timeout=20.0)
    if r.status_code >= 400:
        raise RuntimeError(f"ChatGPT-Konto: Modell-Liste gescheitert ({r.status_code} {_fehlercode(r)}).".strip())
    return modelle_aus(r.json())


def modelle_aus(daten) -> list[tuple[str, str]]:
    raus = []
    for m in (daten.get("models") or []) if isinstance(daten, dict) else []:
        if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str):
            raus.append((m["slug"], str(m.get("display_name") or m["slug"])))
    return raus


# --- Anmelden im Browser -----------------------------------------------------------------------------------

def _freier_port() -> int:
    for port in (WUNSCH_PORT, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise AnmeldeFehler("Kein freier Port auf 127.0.0.1 für die Anmeldung.")


_SEITE = ("<!doctype html><meta charset='utf-8'><title>NemiCLI</title>"
          "<body style='font-family:Segoe UI,sans-serif;background:#0c0c10;color:#ececf1;display:flex;"
          "align-items:center;justify-content:center;height:90vh'><div style='text-align:center'>"
          "<h2>{titel}</h2><p>{text}</p></div></body>")


def _empfaenger(port: int, ergebnis: dict, fertig: threading.Event) -> http.server.HTTPServer:
    class Rueckruf(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                           # noqa: N802 (Name von http.server vorgegeben)
            teile = urllib.parse.urlsplit(self.path)
            if teile.path != RUECKRUF_PFAD or fertig.is_set():
                self.send_response(404)
                self.end_headers()
                return
            ergebnis.update({k: v[0] for k, v in urllib.parse.parse_qs(teile.query).items() if v})
            ok = "code" in ergebnis and "error" not in ergebnis
            seite = _SEITE.format(titel="✓ Angemeldet" if ok else "Anmeldung abgebrochen",
                                  text="Du kannst dieses Fenster schließen und zu NemiCLI zurückkehren." if ok
                                  else "Es wurde nichts gespeichert. Du kannst es in NemiCLI neu versuchen.")
            daten = seite.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(daten)))
            self.end_headers()
            self.wfile.write(daten)
            fertig.set()

        def log_message(self, *_args):              # nichts ins Terminal (die Adresse trägt den Code)
            pass

    return http.server.HTTPServer(("127.0.0.1", port), Rueckruf)


def anmelden(browser_oeffnen=None, warten_s: float = WARTEN_S) -> dict:
    """Im Browser bei ChatGPT anmelden und der Plan-Nutzung zustimmen. Gibt status() zurück.
    `browser_oeffnen(url)` öffnet die Seite (Vorgabe: webbrowser.open)."""
    if browser_oeffnen is None:
        import webbrowser
        browser_oeffnen = webbrowser.open
    with _sperre:
        d = _lesen()
        host = _host_id(d)
        client_id = str(d.get("client_id") or "")
    verifier, challenge = pkce_paar()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    port = _freier_port()
    rueckruf = f"http://127.0.0.1:{port}{RUECKRUF_PFAD}"
    param = {"client_id": client_id or REGISTRIERUNG, "ext_agent_host_id": host, "response_type": "code",
             "redirect_uri": rueckruf, "scope": SCOPES, "resource": RESSOURCE, "state": state, "nonce": nonce,
             "code_challenge_method": "S256", "code_challenge": challenge}
    if client_id:
        if d.get("id_token"):
            param["id_token_hint"] = d["id_token"]
        if d.get("email"):
            param["login_hint"] = d["email"]
    else:
        param["agent_name_hint"] = APP_NAME
    ergebnis: dict = {}
    fertig = threading.Event()
    server = _empfaenger(port, ergebnis, fertig)       # erst lauschen, dann den Browser öffnen
    faden = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2},
                             name="chatgpt-anmeldung", daemon=True)
    faden.start()
    try:
        browser_oeffnen(f"{AUTH_URL}?{urllib.parse.urlencode(param)}")
        if not fertig.wait(warten_s):
            raise AnmeldeFehler("Keine Antwort aus dem Browser – die Anmeldung ist abgelaufen.")
    finally:
        server.shutdown()
        server.server_close()
    if ergebnis.get("state") != state:
        raise AnmeldeFehler("Die Antwort aus dem Browser gehört nicht zu dieser Anmeldung (state).")
    if ergebnis.get("error"):
        raise AnmeldeFehler("Die Anmeldung wurde abgelehnt." if ergebnis["error"] == "access_denied"
                            else f"Die Anmeldung ist gescheitert ({ergebnis['error']}).")
    if not ergebnis.get("code"):
        raise AnmeldeFehler("Die Antwort aus dem Browser enthält keinen Code.")
    ausgegeben = ergebnis.get("client_id") or client_id
    if not ausgegeben or ausgegeben == REGISTRIERUNG:
        raise AnmeldeFehler("OpenAI hat keine Client-ID für NemiCLI ausgegeben.")
    r = httpx.post(TOKEN_URL, data={"grant_type": "authorization_code", "client_id": ausgegeben,
                                    "code": ergebnis["code"], "code_verifier": verifier,
                                    "redirect_uri": rueckruf, "resource": RESSOURCE}, timeout=30.0)
    if r.status_code >= 400:
        raise AnmeldeFehler(f"OpenAI lehnt die Anmeldung ab ({r.status_code} {_fehlercode(r)}).".strip())
    with _sperre:
        d = _lesen()
        d["client_id"] = ausgegeben
        d["ext_agent_host_id"] = host
        _uebernehmen(d, r.json(), nonce)
        if ergebnis.get("scope") and not d.get("scope"):
            d["scope"] = ergebnis["scope"]
        _schreiben(d)
    return status()
