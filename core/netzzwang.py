"""
netzzwang.py - Netz-Zwang: ins Netz nur verschlüsselt, unverschlüsselt nur kontrolliert zum eigenen Rechner.

Nach `installieren()` gilt für den ganzen Prozess:
- http:// und ws:// zu einem anderen Rechner: Die Verbindung wird abgebrochen, nicht umgewandelt.
- Unverschlüsselt nur zu 127.0.0.1 bzw. ::1 und nur zu Ports der Liste (`ports()`): bekannte lokale
  Dienste, die Einstellung `netz_lokale_ports` (/web lokal) und Ports, auf denen dieser Prozess selbst
  lauscht. „localhost“ zählt nicht – die hosts-Datei kann den Namen umbiegen.
- 0.0.0.0 und :: sind weder Ziel noch Lausch-Adresse.

Abgefangen werden httpx (auch in den Anbieter-Bibliotheken), http.client (urllib), urllib3 (requests)
und socket.bind/connect. Befehle und Code der KI prüft `text_pruefen`: Dort steht nie http://; einen
lokalen Dienst spricht die KI mit dem Codewort `lokal:PORT` an, `codewort_einsetzen` setzt die Adresse ein.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import threading
from pathlib import Path

STANDARD = {11434: "Ollama", 1234: "LM Studio", 1337: "Jan", 8080: "llama.cpp-Server", 8000: "vLLM",
            8188: "ComfyUI", 7860: "SD WebUI"}
EINSTELLUNG = "netz_lokale_ports"
LOKAL_IP = "127.0.0.1"
UMGEBUNG_PORTS = "NEMICLI_NETZ_PORTS"
KIND_ORDNER = "_netzzwang"
_SITECUSTOMIZE = ("# Netz-Zwang auch in Python-Programmen, die NemiCLI startet (angelegt von netzzwang.py).\n"
                  "try:\n    import netzzwang as _nz\n    _nz.installieren()\nexcept Exception:\n    pass\n")

MELDUNG_NULL = "🔒 Netz-Zwang: 0.0.0.0 ist gesperrt – weder als Ziel noch zum Lauschen (nur 127.0.0.1)."

_eigene: dict[int, str] = {}
_sperre = threading.Lock()
_installiert = False


class NetzGesperrt(ConnectionRefusedError):
    """Vom Netz-Zwang abgebrochene Verbindung."""


def _rein(host) -> str:
    return str(host or "").strip().strip("[]").rstrip(".").lower()


def unbestimmt(host) -> bool:
    """0.0.0.0, ::, leer – die Adresse „alle Schnittstellen“."""
    h = _rein(host)
    if h in ("", "0", "0.0.0.0", "::"):
        return True
    try:
        return ipaddress.ip_address(h).is_unspecified
    except ValueError:
        return False


def loopback(host) -> bool:
    """Eine Loopback-IP (127.0.0.0/8, ::1) – der Name „localhost“ nicht."""
    try:
        return ipaddress.ip_address(_rein(host)).is_loopback
    except ValueError:
        return False


def lokal_erlauben(port: int, name: str = "eigener Dienst") -> None:
    try:
        port = int(port)
    except (TypeError, ValueError):
        return
    if 0 < port < 65536:
        with _sperre:
            _eigene.setdefault(port, name)


def ports() -> dict[int, str]:
    """Freigegebene Ports auf 127.0.0.1 → Name des Dienstes."""
    raus = dict(STANDARD)
    try:
        import config
        for p in config.load().get(EINSTELLUNG) or []:
            if isinstance(p, int) and 0 < p < 65536:
                raus.setdefault(p, "eigene Freigabe")
    except Exception:
        pass
    for t in os.environ.get(UMGEBUNG_PORTS, "").split(","):     # in Kindprozessen: Liste von NemiCLI
        if t.strip().isdigit() and 0 < int(t) < 65536:
            raus.setdefault(int(t), "von NemiCLI freigegeben")
    with _sperre:
        for p, n in _eigene.items():
            raus.setdefault(p, n)
    return raus


def ports_text() -> str:
    return ", ".join(f"{p} {n}" for p, n in sorted(ports().items()))


def ziel_pruefen(schema: str, host, port: int | None) -> str | None:
    """Meldung, wenn die Verbindung gesperrt ist – sonst None."""
    schema = (schema or "").lower()
    if unbestimmt(host):
        return MELDUNG_NULL
    if schema in ("https", "wss"):
        return None
    if schema not in ("http", "ws"):
        return f"🔒 Netz-Zwang: {schema or '?'}:// ist gesperrt – ins Netz nur https://."
    if _rein(host) == "localhost":
        return "🔒 Netz-Zwang: unverschlüsselt nur zu 127.0.0.1, nicht zu „localhost“ (die hosts-Datei kann ihn umbiegen)."
    if not loopback(host):
        return f"🔒 Netz-Zwang: unverschlüsselte Verbindung zu {_rein(host)} gesperrt – ins Netz nur https://."
    p = port or 80
    if p not in ports():
        return (f"🔒 Netz-Zwang: Port {p} auf 127.0.0.1 ist nicht freigegeben (frei: {ports_text()}). "
                f"Freigeben kann ihn nur der Nutzer: /web lokal {p}")
    return None


def url_pruefen(url: str) -> str | None:
    from urllib.parse import urlsplit
    try:
        t = urlsplit(str(url or "").strip())
        return ziel_pruefen(t.scheme, t.hostname, t.port)
    except ValueError:
        return "🔒 Netz-Zwang: Adresse nicht lesbar."


# --- Befehle und Code der KI ----------------------------------------------------------------

_UNVERSCHLUESSELT = re.compile(r"(?i)\b(?:http|ws)\s*:\s*//")
_UNVERSCHLUESSELT_FUGE = re.compile(r"(?i)(?:http|ws):\s*//")     # zusammengesetzte Werte: ohne Wortgrenze
_NULL = re.compile(r"(?<![\w.])0\.0\.0\.0(?![\w.])|\[::\]")
_LOOPBACK_DIREKT = re.compile(r"(?i)(?<![\w.:/-])(localhost|127(?:\.\d{1,3}){3}|\[::1\])\s*:\s*\d{1,5}\b")
_CODEWORT = re.compile(r"(?i)(?<![\w/.:-])lokal:(\d{1,5})\b")
_NULL_TEXT = re.compile(r"""["']::["']""")                       # "::" als Lausch-Adresse
# Lausch-Adresse eines Servers auf der Befehlszeile (uvicorn --host, http.server -b, jupyter --ip …).
_BIND_SCHALTER = re.compile(r"""(?i)(?<![\w-])(?:--host|--bind|--listen|--address|--addr|--ip|--server\.address|-b)"""
                            r"""(?:\s*=\s*|\s+)["']?(\[[^\]\s]*\]|[^\s"',;)]+)""")


def _bind_grund(text: str) -> str | None:
    """Lausch-Adresse außerhalb von 127.0.0.1/::1: alle Schnittstellen oder eine IP im Netz."""
    for m in _BIND_SCHALTER.finditer(text):
        wert = m.group(1)
        if wert == "*" or unbestimmt(wert):
            return MELDUNG_NULL
        try:
            ip = ipaddress.ip_address(_rein(wert))
        except ValueError:
            continue                                   # Name (localhost, Dienstname): prüft der Netz-Zwang im Prozess
        if not ip.is_loopback:
            return f"🔒 Netz-Zwang: ein Server lauscht nur auf 127.0.0.1, nicht auf {wert}."
    return None


def text_pruefen(text: str, lokal_erreichbar: bool = True) -> str | None:
    """Prüft Befehl oder Code der KI: kein http://, kein ws://, kein 0.0.0.0 oder ::, Server nur auf 127.0.0.1,
    eigener Rechner nur über das Codewort lokal:PORT mit freigegebenem Port. `lokal_erreichbar`: False in der
    Sandbox (kein Loopback). Auch ohne PowerShell-Maskierzeichen (`) geprüft: ht`tp:// ist http://."""
    text = str(text or "")
    if "`" in text and (grund := _text_pruefen(text.replace("`", ""), lokal_erreichbar)):
        return grund
    return _text_pruefen(text, lokal_erreichbar)


def befehl_pruefen(cmd: str) -> str | None:
    """text_pruefen für einen PowerShell-Befehl – zusätzlich für jeden Zeichenkettenwert, wie PowerShell ihn
    auflöst (Syntaxbaum aus shellfrei): Maskierungen und Verkettungen im Text sieht die Textprüfung nicht."""
    if (grund := text_pruefen(cmd)):
        return grund
    try:
        import shellfrei
        werte = shellfrei.zeichenketten(cmd)
    except ImportError:
        return None
    for i in range(len(werte)):                       # einzeln und aneinandergehängt ("ht" + "tp://…")
        for j in range(i + 1, min(i + 4, len(werte)) + 1):
            if (grund := text_pruefen("".join(werte[i:j]))):
                return grund
    if _UNVERSCHLUESSELT_FUGE.search("".join(werte)):
        return f"🔒 Netz-Zwang: http:// und ws:// sind gesperrt – ins Netz nur https://. {_lokal_hinweis(True)}"
    return None


def _lokal_hinweis(lokal_erreichbar: bool) -> str:
    return (f"Einen lokalen Dienst sprichst du mit dem Codewort lokal:PORT an (frei: {ports_text()}); "
            "NemiCLI setzt die Adresse ein." if lokal_erreichbar else
            "Die Sandbox erreicht den eigenen Rechner nicht.")


def _text_pruefen(text: str, lokal_erreichbar: bool) -> str | None:
    if _NULL.search(text) or _NULL_TEXT.search(text):
        return MELDUNG_NULL
    if (grund := _bind_grund(text)):
        return grund
    if UMGEBUNG_PORTS.lower() in text.lower() or KIND_ORDNER in text.lower():
        return "🔒 Netz-Zwang: die Freigaben des Netz-Zwangs ändert nur der Nutzer (/web lokal)."
    hinweis = _lokal_hinweis(lokal_erreichbar)
    if _UNVERSCHLUESSELT.search(text):
        return f"🔒 Netz-Zwang: http:// und ws:// sind gesperrt – ins Netz nur https://. {hinweis}"
    if _LOOPBACK_DIREKT.search(text):
        return f"🔒 Netz-Zwang: den eigenen Rechner nicht direkt mit Adresse und Port ansprechen. {hinweis}"
    for m in _CODEWORT.finditer(text):
        if not lokal_erreichbar:
            return f"🔒 Netz-Zwang: lokal:{m.group(1)} geht hier nicht. {hinweis}"
        port = int(m.group(1))
        if port not in ports():
            return (f"🔒 Netz-Zwang: Port {port} ist nicht freigegeben (frei: {ports_text()}). "
                    f"Freigeben kann ihn nur der Nutzer: /web lokal {port}")
    return None


def codewort_einsetzen(text: str) -> str:
    """lokal:PORT → http://127.0.0.1:PORT (nur nach text_pruefen aufrufen)."""
    return _CODEWORT.sub(lambda m: f"http://{LOKAL_IP}:{int(m.group(1))}", str(text or ""))


# --- Python-Programme, die NemiCLI startet ---------------------------------------------------

def kind_ordner() -> Path | None:
    """Ordner mit sitecustomize.py und netzzwang.py für Python-Kindprozesse. Er liegt im Programm-Ordner,
    den die Datei-Werkzeuge der KI weder lesen noch schreiben."""
    try:
        import paths
        quelle = Path(paths.ASSETS) / "netzzwang.py" if paths.FROZEN else Path(__file__)
        ziel = Path(paths.INSTALL) / KIND_ORDNER
        ziel.mkdir(exist_ok=True)
        for name, inhalt in (("netzzwang.py", quelle.read_text(encoding="utf-8")), ("sitecustomize.py", _SITECUSTOMIZE)):
            datei = ziel / name
            if not datei.is_file() or datei.read_text(encoding="utf-8") != inhalt:
                datei.write_text(inhalt, encoding="utf-8")
        return ziel
    except Exception:
        return None


def kind_umgebung(env: dict) -> dict:
    """Umgebung für einen Kindprozess: Python-Programme darin laden den Netz-Zwang beim Start
    (sitecustomize über PYTHONPATH) und kennen die freigegebenen Ports."""
    if (ordner := kind_ordner()) is not None:
        alt = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(ordner) + (os.pathsep + alt if alt else "")
    env[UMGEBUNG_PORTS] = ",".join(str(p) for p in sorted(ports()))
    return env


# --- Abfangen im Prozess --------------------------------------------------------------------

def _httpx_patchen() -> None:
    try:
        import httpx
    except ImportError:
        return

    def pruefen(request) -> None:
        u = request.url
        if (m := ziel_pruefen(u.scheme, u.host, u.port)):
            raise httpx.ConnectError(m, request=request)

    alt = httpx.HTTPTransport.handle_request
    alt_async = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self, request):
        pruefen(request)
        return alt(self, request)

    async def handle_async_request(self, request):
        pruefen(request)
        return await alt_async(self, request)

    httpx.HTTPTransport.handle_request = handle_request
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request


def _verbindung_pruefen(conn, tls: bool) -> None:
    """http.client/urllib3: Klartext nur zu freigegebenen lokalen Ports. Ein CONNECT-Tunnel über einen
    Proxy trägt TLS und zählt als verschlüsselt."""
    if unbestimmt(conn.host):
        raise NetzGesperrt(MELDUNG_NULL)
    if not tls and not getattr(conn, "_tunnel_host", None):
        if (m := ziel_pruefen("http", conn.host, conn.port)):
            raise NetzGesperrt(m)


def _httpclient_patchen() -> None:
    import http.client
    alt = http.client.HTTPConnection.connect

    def connect(self):
        _verbindung_pruefen(self, isinstance(self, http.client.HTTPSConnection))
        return alt(self)

    http.client.HTTPConnection.connect = connect


def _urllib3_patchen() -> None:
    try:
        import urllib3.connection as U
    except ImportError:
        return
    alt = U.HTTPConnection.connect

    def connect(self):
        _verbindung_pruefen(self, isinstance(self, U.HTTPSConnection))
        return alt(self)

    U.HTTPConnection.connect = connect


def _socket_patchen() -> None:
    inet = (socket.AF_INET, socket.AF_INET6)
    alt_bind, alt_connect, alt_connect_ex = socket.socket.bind, socket.socket.connect, socket.socket.connect_ex

    def bind(self, adresse):
        if self.family in inet and isinstance(adresse, tuple) and unbestimmt(adresse[0]):
            raise NetzGesperrt(MELDUNG_NULL)
        alt_bind(self, adresse)
        if self.family in inet and isinstance(adresse, tuple) and loopback(adresse[0]):
            try:
                lokal_erlauben(self.getsockname()[1])
            except OSError:
                pass

    def connect(self, adresse):
        if self.family in inet and isinstance(adresse, tuple) and unbestimmt(adresse[0]):
            raise NetzGesperrt(MELDUNG_NULL)
        return alt_connect(self, adresse)

    def connect_ex(self, adresse):
        if self.family in inet and isinstance(adresse, tuple) and unbestimmt(adresse[0]):
            raise NetzGesperrt(MELDUNG_NULL)
        return alt_connect_ex(self, adresse)

    socket.socket.bind, socket.socket.connect, socket.socket.connect_ex = bind, connect, connect_ex


_ORIGINALE: list[tuple[object, str, object]] = []
_GEERBT = object()


def _merken() -> None:
    """Die Stellen, die installieren() ersetzt – damit Tests den Prozess sauber zurücklassen."""
    ziele = [(socket.socket, "bind"), (socket.socket, "connect"), (socket.socket, "connect_ex")]
    import http.client
    ziele.append((http.client.HTTPConnection, "connect"))
    try:
        import httpx
        ziele += [(httpx.HTTPTransport, "handle_request"), (httpx.AsyncHTTPTransport, "handle_async_request")]
    except ImportError:
        pass
    try:
        import urllib3.connection as U
        ziele.append((U.HTTPConnection, "connect"))
    except ImportError:
        pass
    _ORIGINALE[:] = [(k, n, k.__dict__.get(n, _GEERBT)) for k, n in ziele]   # socket.socket erbt bind & Co.


def installieren() -> None:
    """Einmal je Prozess, so früh wie möglich."""
    global _installiert
    with _sperre:
        if _installiert:
            return
        _installiert = True
    _merken()
    _socket_patchen()
    _httpclient_patchen()
    _httpx_patchen()
    _urllib3_patchen()


def entfernen() -> None:
    """Nur für Tests: den Zustand vor installieren() wiederherstellen."""
    global _installiert
    for klasse, name, wert in _ORIGINALE:
        if wert is _GEERBT:
            if name in klasse.__dict__:
                delattr(klasse, name)
        else:
            setattr(klasse, name, wert)
    _ORIGINALE.clear()
    _installiert = False


def installiert() -> bool:
    return _installiert
