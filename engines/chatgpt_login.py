"""chatgpt_login.py – „Sign in with ChatGPT“ für die ChatGPT-Plan-Nutzung (offizieller OAuth-Weg für Open-Source-Apps).

Ablauf (developers.openai.com/siwc/token-sharing-open-source):
- Anmeldung im Browser: OpenID Connect mit PKCE (S256), ohne Client-Secret. Der Rückruf geht an einen kurzlebigen
  Empfänger nur auf 127.0.0.1 (Pfad /auth/callback), frische state- und nonce-Werte je Versuch.
- Erste Anmeldung über client_id „dynamic_agent_client“; die ausgegebene ID (oaiapp_…) wird gespeichert und danach
  verwendet. Je Installation eine zufällige Host-ID (urn:uuid:…), kein Bezug zur Person.
- Scopes: openid profile email offline_access resource.invoke chatgpt.tokens.use.direct, resource
  https://api.openai.com/v1. Ohne chatgpt.tokens.use.direct bleibt die Plan-Nutzung aus.
- Access-Token 1 h, Refresh-Token 30 Tage; jedes Erneuern liefert ein neues Refresh-Token.

Gespeichert wird das ganze Konto (auch E-Mail und Kennungen) im Tresor (tresor.py, Bereich „chatgpt“): AES-256 und
das Windows-Schloss, mit Siegel. Eine alte KONTO_DATEI (früher im Programm-Ordner, Tokens im DPAPI-Umschlag,
der Rest offen) zieht beim ersten Lesen dorthin um und wird geschreddert. Ohne Windows-Kryptografie bleibt alles nur
im Speicher dieses Prozesses. Tokens erscheinen nie in Meldungen oder Logs.
Das ID-Token kommt direkt per TLS vom Token-Endpunkt; geprüft werden iss, aud, exp und nonce (OIDC Core 3.1.3.7).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import uuid

import httpx

import keyvault
import paths

AUSSTELLER = "https://auth.openai.com"
AUTH_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
RESSOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
REGISTRIERUNG = "dynamic_agent_client"
RUECKRUF_PFAD = "/auth/callback"
WUNSCH_PORT = 1455
APP_NAME = "NemiCLI"
WARTEN_S = 300.0
ABBRUCH_TAKT_S = 0.2                    # so oft fragt das Warten auf den Browser nach einem Abbruch
ERNEUERN_VORHER_S = 120                 # so lange vor Ablauf wird erneuert
NUTZUNG_URL = "https://chatgpt.com/#settings"
SPERRE_WARTEN_S = 75.0                  # länger als ein Erneuern (Zeitgrenze 30 s) in einem anderen Prozess

KONTO_DATEI = paths.INSTALL / "chatgpt_konto.json"   # alter Ort – nur noch für den Umzug in den Tresor
TRESOR_BEREICH = "chatgpt"
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


class AnmeldungAbgebrochen(AnmeldeFehler):
    """Das Warten auf den Browser wurde abgebrochen (ESC)."""


# --- Speichern ---------------------------------------------------------------------------------------------

def _alte_datei_lesen() -> dict:
    """Das Konto aus der alten Datei (Tokens im DPAPI-Umschlag entschlüsselt) – {} ohne Datei."""
    try:
        roh = json.loads(KONTO_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(roh, dict):
        return {}
    for k in _GEHEIM:
        wert = roh.get(k)
        if isinstance(wert, str) and keyvault.is_encrypted(wert):
            roh[k] = keyvault.decrypt(wert) or ""
        else:
            roh.pop(k, None)                       # Klartext-Tokens auf der Platte gelten nicht
    return {k: v for k, v in roh.items() if v not in (None, "")}


def umziehen() -> bool:
    """Alte KONTO_DATEI (und ihre .lock) in den Tresor; danach geschreddert. True, wenn etwas umgezogen ist."""
    import tresor
    alt = KONTO_DATEI
    sperre = alt.with_name(alt.name + ".lock")
    if not alt.exists() and not sperre.exists():
        return False
    daten = _alte_datei_lesen()
    if daten and tresor.verfuegbar() and not tresor.bereich(TRESOR_BEREICH):
        tresor.bereich_setzen(TRESOR_BEREICH, daten)
    elif daten and not tresor.verfuegbar():
        _im_speicher.update(daten)
    tresor.schreddern(alt)
    tresor.schreddern(sperre)
    return bool(daten)


def _lesen() -> dict:
    import tresor
    umziehen()
    if not tresor.verfuegbar():
        return dict(_im_speicher)
    return tresor.bereich(TRESOR_BEREICH)


def _schreiben(daten: dict) -> None:
    import tresor
    werte = {k: v for k, v in daten.items() if v not in (None, "")}
    if not tresor.verfuegbar():
        _im_speicher.clear()
        _im_speicher.update(werte)
        return
    tresor.bereich_setzen(TRESOR_BEREICH, werte)


@contextlib.contextmanager
def _dateisperre(warten_s: float = SPERRE_WARTEN_S):
    """Sperre über Prozesse hinweg (Lesen – Erneuern – Schreiben): zwei NemiCLI-Fenster dürfen nicht
    gleichzeitig mit demselben Refresh-Token erneuern."""
    import tresor
    tresor._anlegen()
    pfad = tresor.ordner() / "konto.sperre"
    f = open(pfad, "a+b")
    ende = time.monotonic() + warten_s
    try:
        while True:
            try:
                f.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > ende:
                    raise RuntimeError("ChatGPT-Anmeldung: ein anderes NemiCLI-Fenster erneuert gerade – "
                                       "bitte gleich noch einmal versuchen.") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                f.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        f.close()


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
    with _sperre, _dateisperre():
        d = _lesen()
        for k in (*_GEHEIM, "scope", "expires_at", "earliest_refresh_at", "email"):
            d.pop(k, None)
        _im_speicher.clear()
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


def _gueltig(d: dict, jetzt: float | None = None) -> bool:
    jetzt = time.time() if jetzt is None else jetzt
    return bool(d.get("access_token")) and jetzt < float(d.get("expires_at") or 0) - ERNEUERN_VORHER_S


def _erneuern(d: dict) -> None:
    """Nur unter _dateisperre aufrufen. Lehnt der Server ab, wird die Datei neu gelesen: hat ein anderer
    Prozess inzwischen erneuert, gilt dessen Stand, und nichts wird gelöscht. Gelöscht werden die Tokens
    nur bei einem Code aus _NEU_ANMELDEN; andere 400/401 lassen sie für den nächsten Versuch stehen."""
    gesendet = d["refresh_token"]
    r = httpx.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": d["client_id"],
                                    "refresh_token": gesendet, "resource": RESSOURCE}, timeout=30.0)
    if r.status_code >= 400:
        code = _fehlercode(r)
        if code in _NEU_ANMELDEN or r.status_code in (400, 401):
            frisch = _lesen()
            if frisch.get("refresh_token") and frisch["refresh_token"] != gesendet:
                d.clear()
                d.update(frisch)
                if _gueltig(d):
                    return
                raise RuntimeError("ChatGPT-Anmeldung: ein anderes Fenster hat gerade erneuert – "
                                   "bitte noch einmal versuchen.")
        if code in _NEU_ANMELDEN:
            for k in ("access_token", "refresh_token"):      # ID-Token und E-Mail bleiben als Hinweis für die neue Anmeldung
                d.pop(k, None)
            _schreiben(d)
            raise AnmeldungNoetig("Die ChatGPT-Anmeldung ist abgelaufen – bitte neu anmelden (/model chatgpt).")
        raise RuntimeError(f"ChatGPT-Anmeldung: Erneuern gescheitert ({r.status_code} {code}).".strip())
    antwort = r.json()
    if not isinstance(antwort, dict):
        raise RuntimeError("ChatGPT-Anmeldung: unerwartete Antwort beim Erneuern.")
    if antwort.get("refresh_token"):        # das gesendete gilt nicht mehr: das neue sichern, bevor Prüfungen werfen
        d["refresh_token"] = antwort["refresh_token"]
        _schreiben(d)
    _uebernehmen(d, antwort, None)
    _schreiben(d)


def zugang() -> str:
    """Gültiges Access-Token für api.openai.com – vor Ablauf von selbst erneuert."""
    def faellig(d: dict) -> bool:
        if not d.get("refresh_token") or not d.get("client_id"):
            raise AnmeldungNoetig("Noch nicht mit ChatGPT angemeldet – /model → ChatGPT-Konto.")
        if PLAN_SCOPE not in str(d.get("scope") or "").split():
            raise AnmeldungNoetig("Die Nutzung deines ChatGPT-Plans ist nicht freigegeben – bitte neu anmelden "
                                  "und zustimmen (/model chatgpt).")
        jetzt = time.time()
        ablauf = float(d.get("expires_at") or 0)
        frueh = d.get("earliest_refresh_at")
        return (not d.get("access_token") or jetzt >= ablauf - ERNEUERN_VORHER_S) and \
            (not frueh or jetzt >= float(frueh) or jetzt >= ablauf)

    with _sperre:
        d = _lesen()
        if faellig(d):
            with _dateisperre():
                d = _lesen()                 # ein anderer Prozess kann schon erneuert haben
                if faellig(d):
                    _erneuern(d)
        return d["access_token"]


# --- Anmelden im Browser -----------------------------------------------------------------------------------


_SEITE = ("<!doctype html><meta charset='utf-8'><title>NemiCLI</title>"
          "<body style='font-family:Segoe UI,sans-serif;background:#0c0c10;color:#ececf1;display:flex;"
          "align-items:center;justify-content:center;height:90vh'><div style='text-align:center'>"
          "<h2>{titel}</h2><p>{text}</p></div></body>")


def _empfaenger(port: int, ergebnis: dict, fertig: threading.Event,
                state: str | None = None) -> http.server.HTTPServer:
    class Rueckruf(http.server.BaseHTTPRequestHandler):
        timeout = 5                                 # eine stumme Verbindung blockiert den Einzel-Server nicht

        def do_GET(self):                           # noqa: N802 (Name von http.server vorgegeben)
            teile = urllib.parse.urlsplit(self.path)
            if teile.path != RUECKRUF_PFAD or fertig.is_set():
                self.send_response(404)
                self.end_headers()
                return
            antwort = {k: v[0] for k, v in urllib.parse.parse_qs(teile.query).items() if v}
            # fremde Aufrufe (falscher state) beenden das Warten nicht
            if state is not None and not secrets.compare_digest(str(antwort.get("state", "")).encode("utf-8"),
                                                                state.encode("utf-8")):
                self.send_response(400)
                self.end_headers()
                return
            ergebnis.update(antwort)
            fertig.set()                     # vor der Antwort: wer die Seite gesehen hat, findet das Ergebnis schon vor
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

        def log_message(self, *_args):              # nichts ins Terminal (die Adresse trägt den Code)
            pass

    import lokalserver
    return lokalserver.EinzelServer(("127.0.0.1", port), Rueckruf)


def _empfaenger_starten(ergebnis: dict, fertig: threading.Event, state: str) -> http.server.HTTPServer:
    """Empfänger auf WUNSCH_PORT, sonst auf einem freien Port – gleich exklusiv gebunden, ohne Probe-Socket."""
    for port in (WUNSCH_PORT, 0):
        try:
            return _empfaenger(port, ergebnis, fertig, state)
        except OSError:
            continue
    raise AnmeldeFehler("Kein freier Port auf 127.0.0.1 für die Anmeldung.")


def anmelden(browser_oeffnen=None, warten_s: float = WARTEN_S, abbruch=None) -> dict:
    """Im Browser bei ChatGPT anmelden und der Plan-Nutzung zustimmen. Gibt status() zurück.
    `browser_oeffnen(url)` öffnet die Seite (Vorgabe: webbrowser.open). `abbruch()` wird beim Warten
    alle ABBRUCH_TAKT_S gefragt; True beendet die Anmeldung (AnmeldungAbgebrochen), ein späterer Rückruf
    wird verworfen."""
    if browser_oeffnen is None:
        import webbrowser
        browser_oeffnen = webbrowser.open
    with _sperre:
        d = _lesen()
        host = _host_id(d)
        client_id = str(d.get("client_id") or "")
    verifier, challenge = pkce_paar()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    ergebnis: dict = {}
    fertig = threading.Event()
    server = _empfaenger_starten(ergebnis, fertig, state)   # erst lauschen, dann den Browser öffnen
    port = server.server_address[1]
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
    faden = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2},
                             name="chatgpt-anmeldung", daemon=True)
    try:
        faden.start()
        browser_oeffnen(f"{AUTH_URL}?{urllib.parse.urlencode(param)}")
        ende = time.monotonic() + warten_s
        while not fertig.wait(max(0.0, min(ABBRUCH_TAKT_S, ende - time.monotonic()))):
            if abbruch is not None and abbruch():
                fertig.set()                         # ein späterer Rückruf bekommt 404 und zählt nicht
                raise AnmeldungAbgebrochen("Die Anmeldung wurde abgebrochen – es wurde nichts gespeichert.")
            if time.monotonic() >= ende:
                raise AnmeldeFehler("Keine Antwort aus dem Browser – die Anmeldung ist abgelaufen.")
    finally:
        if faden.is_alive():
            server.shutdown()
        server.server_close()
    if abbruch is not None and abbruch():
        raise AnmeldungAbgebrochen("Die Anmeldung wurde abgebrochen – es wurde nichts gespeichert.")
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
    with _sperre, _dateisperre():
        d = _lesen()
        d["client_id"] = ausgegeben
        d["ext_agent_host_id"] = host
        _uebernehmen(d, r.json(), nonce)
        if ergebnis.get("scope") and not d.get("scope"):
            d["scope"] = ergebnis["scope"]
        _schreiben(d)
    return status()
