"""
webui.py - Browser-Oberfläche, die mit der laufenden CLI verbunden ist.

Idee: Die CLI bleibt der „Motor" (Modelle, Aktionen, Gedächtnis laufen weiter im
Terminal-Prozess). `/webui` startet hier einen winzigen lokalen Server und öffnet
eine schön lesbare Seite im Browser. Alles, was du auf der Seite tust, geht über
denselben Sitzungs-Status (`Ctx`) wie das Terminal – derselbe Chat, dasselbe Modell.

Gedacht für bessere Lesbarkeit (u.a. bei LRS): umschaltbare Schrift (inkl.
OpenDyslexic), Größe & Abstände, kontrastreiche Themes und Vorlesen (Text-to-Speech).

Technik bewusst schlank: nur die Standard-Library.
  • `http.server` (threaded) liefert die Seite und nimmt Eingaben an.
  • Streaming der Antwort läuft über **SSE** (Server-Sent Events) – eine offene
    GET-Verbindung, in die der Server Ereignis-Zeilen schreibt.
  • Eingaben/Bestätigungen kommen per POST zurück.
Der Server läuft in einem Hintergrund-Thread; Arbeit wird per
`run_coroutine_threadsafe` auf die asyncio-Schleife der CLI geschoben.

SICHERHEIT: nur an 127.0.0.1 gebunden (nie nach außen), und jeder API-Aufruf
braucht ein Sitzungs-Token (steckt in der geöffneten URL). So kann kein anderer
Prozess/keine Webseite die Aktionen (Datei/Shell!) der CLI fernsteuern.
"""

from __future__ import annotations

import json
import queue
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class WebBridge:
    """Vermittelt zwischen dem Web-Server (Threads) und der asyncio-CLI.

    - broadcast(): schiebt ein Ereignis (dict) an alle offenen Browser-Tabs.
    - ask(): stellt eine Bestätigungs-Frage im Browser und wartet (async) auf die
      Antwort – aufgelöst, sobald der Browser per POST /confirm antwortet.
    """

    def __init__(self, loop):
        self.loop = loop                       # asyncio-Schleife der CLI
        self.token = secrets.token_urlsafe(18)
        self.url = ""                          # wird von start() gesetzt
        self.httpd = None
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._confirms: dict[int, object] = {}  # id -> asyncio.Future
        self._cid = 0

    # --- SSE-Clients (je offener Tab eine Queue) ---------------------------
    def add_client(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._clients.add(q)
        return q

    def remove_client(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def broadcast(self, event: dict) -> None:
        """Ereignis an alle Tabs senden. Aus jedem Thread aufrufbar."""
        data = json.dumps(event, ensure_ascii=False)
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass

    # --- Bestätigungen (async, vom Server-Thread aufgelöst) ----------------
    async def ask(self, question: str, options: list[tuple[str, str]],
                  preview: str | None = None) -> str:
        """Im Browser nachfragen und auf die Wahl warten. Läuft auf der CLI-Schleife."""
        self._cid += 1
        cid = self._cid
        fut = self.loop.create_future()
        self._confirms[cid] = fut
        self.broadcast({"t": "confirm", "id": cid, "question": question,
                        "options": [[v, l] for v, l in options], "preview": preview})
        try:
            return await fut
        finally:
            self._confirms.pop(cid, None)

    def answer(self, cid: int, choice: str) -> None:
        """Aus dem Server-Thread: die wartende Bestätigung auflösen."""
        fut = self._confirms.get(cid)
        if fut is not None and not fut.done():
            self.loop.call_soon_threadsafe(fut.set_result, choice)

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()


# --- HTTP-Server ------------------------------------------------------------

def _make_handler(bridge: WebBridge, handlers: dict):
    """Baut die Request-Handler-Klasse (Closure über bridge + handlers).

    handlers: {
      "send":   fn(text, images)  -> plant einen Chat-Zug ein (non-blocking),
      "switch": fn(ref)           -> wechselt das Modell (non-blocking),
      "models": fn() -> list[dict]  (blockierend ok; liefert Modell-Liste),
      "state":  fn() -> dict        (aktueller Sitzungs-Status),
    }
    """

    def token_ok(parsed, headers) -> bool:
        q = parse_qs(parsed.query)
        tok = (q.get("token", [""])[0]) or headers.get("X-Token", "")
        return secrets.compare_digest(tok, bridge.token)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):           # keine Konsolen-Spam-Logs
            pass

        # --- kleine Antwort-Helfer ---
        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                return {}

        # --- GET: Seite, SSE-Stream, Modell-Liste ---
        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/" or path == "/index.html":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/events":
                if not token_ok(parsed, self.headers):
                    self._send(403); return
                self._sse(parsed)
                return
            if path == "/models":
                if not token_ok(parsed, self.headers):
                    self._send(403); return
                self._json(handlers["models"]())
                return
            if path == "/state":
                if not token_ok(parsed, self.headers):
                    self._send(403); return
                self._json(handlers["state"]())
                return
            if path == "/chats":
                if not token_ok(parsed, self.headers):
                    self._send(403); return
                fn = handlers.get("chats")
                self._json(fn() if fn else {"current": 0, "chats": []})
                return
            self._send(404)

        # --- SSE: offene Verbindung, schreibt Ereignis-Zeilen ---
        def _sse(self, parsed):
            # Diese Verbindung lebt bis der Tab schließt; danach NICHT auf eine
            # nächste Anfrage warten (sonst Reset-Fehler beim Neuladen/Schließen).
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            q = bridge.add_client()
            # aktuellen Stand sofort schicken, damit der Tab gleich „weiß", wo er ist
            try:
                self.wfile.write(_sse_line(json.dumps(handlers["state"](),
                                                       ensure_ascii=False)))
                self.wfile.flush()
            except OSError:
                bridge.remove_client(q); return
            try:
                while True:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")   # Keepalive
                        self.wfile.flush()
                        continue
                    self.wfile.write(_sse_line(data))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bridge.remove_client(q)

        # --- POST: senden, bestätigen, Modell wechseln ---
        def do_POST(self):
            parsed = urlparse(self.path)
            if not token_ok(parsed, self.headers):
                self._send(403); return
            path = parsed.path
            data = self._body()
            if path == "/send":
                handlers["send"](data.get("text", ""), data.get("images") or [])
                self._send(204); return
            if path == "/confirm":
                try:
                    bridge.answer(int(data.get("id")), str(data.get("choice")))
                except Exception:
                    pass
                self._send(204); return
            if path == "/switch":
                handlers["switch"](data.get("ref", ""))
                self._send(204); return
            if path == "/resume":
                fn = handlers.get("resume")
                if fn:
                    fn(data.get("id"))
                self._send(204); return
            if path == "/newchat":
                fn = handlers.get("newchat")
                if fn:
                    fn()
                self._send(204); return
            self._send(404)

    return Handler


def _sse_line(data: str) -> bytes:
    # SSE: mehrzeilige Daten brauchen je Zeile ein "data:"
    out = "".join(f"data: {ln}\n" for ln in data.split("\n")) + "\n"
    return out.encode("utf-8")


class _QuietServer(ThreadingHTTPServer):
    """Wie ThreadingHTTPServer, aber abgebrochene Verbindungen (Tab geschlossen /
    neu geladen) werden geschluckt statt als Traceback ins Terminal zu poltern."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        et = sys.exc_info()[0]
        if et is not None and issubclass(et, (ConnectionError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def start(bridge: WebBridge, handlers: dict, host: str = "127.0.0.1", port: int = 0) -> str:
    """Startet den Server in einem Hintergrund-Thread und gibt die URL (mit Token) zurück."""
    httpd = _QuietServer((host, port), _make_handler(bridge, handlers))
    bridge.httpd = httpd
    real_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="nemicli-webui").start()
    bridge.url = f"http://{host}:{real_port}/?token={bridge.token}"
    return bridge.url


# --- Die Seite (eine Datei, keine externen Pflicht-Assets) ------------------
# Schriften kommen optional von einem CDN; offline fällt der Browser sauber auf
# System-Schriften zurück.
PAGE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NemiCLI · WebUI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<link href="https://fonts.cdnfonts.com/css/opendyslexic" rel="stylesheet">
<style>
:root{
  --bg:#0b0d14; --panel:#141824; --panel2:#1c2130; --text:#e8eaf2; --muted:#8b93a7;
  --accent:#41e0d0; --accent2:#8b7cff; --line:#2a3144; --ok:#54d18c; --warn:#f0c674;
  --err:#ff6b6b; --bubble-user:#1a2a3a; --bubble-ai:#161a26;
  --fs:18px; --lh:1.7; --ls:0px; --ws:0px;
  --side:268px;
  --font:'Atkinson Hyperlegible', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:
  radial-gradient(1200px 500px at -10% -20%, #8b7cff22, transparent 50%),
  radial-gradient(900px 420px at 110% 0%, #41e0d018, transparent 45%),
  var(--bg);
  color:var(--text);font-family:var(--font);
  font-size:var(--fs);line-height:var(--lh);letter-spacing:var(--ls);word-spacing:var(--ws);}
button{font-family:inherit}
.shell{height:100vh;display:grid;grid-template-columns:minmax(0,1fr) 290px;overflow:hidden}
.side{display:none !important}
.side,.inspector{background:rgba(15,20,30,.92);backdrop-filter:blur(14px);overflow:auto}
.side{border-right:1px solid var(--line);padding:16px;display:flex;flex-direction:column;gap:12px}
.inspector{border-left:1px solid var(--line);padding:16px}
.brand{display:flex;align-items:center;gap:10px}
.mark{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;font-weight:800;color:#071814;
  background:linear-gradient(135deg,var(--accent),#85c8ff 55%,var(--accent2));flex-shrink:0}
.logo{font-weight:700;font-size:1rem;letter-spacing:.02em;
  background:linear-gradient(90deg,#41e0d0,#8badff,#8b7cff);-webkit-background-clip:text;background-clip:text;color:transparent}
.tag{color:var(--muted);font-size:.72em}
.primary{width:100%;border:none;border-radius:14px;padding:11px 12px;cursor:pointer;
  color:#06221d;font-weight:800;background:linear-gradient(90deg,var(--accent),#78d6ef)}
.section-title{font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.11em;margin:8px 4px 6px}
.chatlist{display:flex;flex-direction:column;gap:4px;flex:1;overflow:auto}
.chat{width:100%;border:1px solid transparent;background:transparent;color:var(--text);
  border-radius:12px;padding:9px 10px;cursor:pointer;text-align:left;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.82em}
.chat:hover{background:var(--panel2);border-color:var(--line)}
.chat.active{background:linear-gradient(90deg,rgba(65,224,208,.12),rgba(139,124,255,.08));border-color:#36505b}
.pill{max-width:420px;background:var(--panel2);border:1px solid var(--line);color:var(--text);
  border-radius:13px;padding:8px 12px;cursor:pointer;font-size:.8em;text-align:left;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill:hover{border-color:var(--accent);box-shadow:0 0 0 3px #41e0d01a}
.stat{font-size:.72rem;color:var(--muted);border:1px solid var(--line);padding:6px 9px;border-radius:999px;background:#0f1520}
.iconbtn{background:var(--panel2);border:1px solid var(--line);color:var(--text);
  border-radius:11px;width:36px;height:36px;font-size:1.05em;cursor:pointer;flex-shrink:0}
.iconbtn:hover{border-color:var(--accent)}
.side-foot{margin-top:auto;display:flex;gap:8px;align-items:center;font-size:.72em;color:var(--muted)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 12px rgba(84,209,140,.55);flex-shrink:0}
.center{min-width:0;display:flex;flex-direction:column;height:100vh}
.helfer{display:none;align-items:center;gap:7px;font-size:.8em;color:var(--text);
  background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:4px 10px}
.helfer.on{display:inline-flex}
.helfer .pdot{width:8px;height:8px;border-radius:50%;background:var(--ok);
  animation:helferpuls 1s ease-in-out infinite}
@keyframes helferpuls{0%,100%{opacity:1;box-shadow:0 0 10px rgba(84,209,140,.7)}50%{opacity:.25;box-shadow:none}}
.topbar{height:68px;flex:0 0 68px;border-bottom:1px solid var(--line);
  background:rgba(10,13,20,.72);backdrop-filter:blur(14px);
  display:flex;align-items:center;gap:10px;padding:0 18px}
.spacer{flex:1}
.card{border:1px solid var(--line);background:var(--panel2);border-radius:14px;padding:12px;margin-bottom:12px}
.kv{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:.72rem;margin:5px 0}
.kv b{color:var(--text);font-weight:650}
.ctx{height:9px;border-radius:999px;border:1px solid var(--line);background:#0d131c;overflow:hidden;margin:8px 0}
.ctx i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent2))}
.warnbox{font-size:.72rem;color:var(--warn);border:1px solid #564a22;background:#241f10;border-radius:12px;padding:9px}
.reader label{display:block;color:var(--muted);font-size:.72rem;margin:8px 0 4px}
.reader select,.reader input[type=range]{width:100%;accent-color:var(--accent)}
.reader select{background:#101722;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:7px}
.reader .switch{display:flex;align-items:center;justify-content:space-between;font-size:.74rem;color:var(--muted);padding:6px 0}
.mobile-only{display:none}

main{flex:1;overflow-y:auto;padding:22px 22px 10px;scroll-behavior:smooth;max-width:900px;width:100%;margin:0 auto}
.msg{margin:0 0 18px;max-width:100%}
.msg .who{font-size:.7em;color:var(--muted);margin:0 6px 6px;text-transform:uppercase;letter-spacing:.08em}
.bubble{padding:13px 16px;border-radius:16px;border:1px solid var(--line);box-shadow:0 8px 24px #0003}
.user .bubble{background:linear-gradient(180deg,#1e3348,#1a2a3a);margin-left:auto;max-width:88%;
  border-color:#2a4a62}
.user{display:flex;flex-direction:column;align-items:flex-end}
.ai .bubble{background:linear-gradient(180deg,#1a1e2c,#151822)}
.bubble p{margin:.4em 0}
.bubble pre{background:#0b0d13;border:1px solid var(--line);border-radius:10px;
  padding:12px;overflow:auto;font-size:.9em;line-height:1.5}
.bubble code{background:#0b0d13;border:1px solid var(--line);border-radius:5px;padding:1px 5px;font-size:.9em}
.bubble pre code{background:none;border:none;padding:0}
.bubble a{color:var(--accent2)}
.think{margin:.2em 0 .6em;border-left:3px solid var(--accent2);background:#12162a;border-radius:0 10px 10px 0}
.think summary{cursor:pointer;color:var(--accent2);font-size:.8em;padding:6px 10px}
.think .body{padding:0 12px 10px;color:var(--muted);font-size:.92em;white-space:pre-wrap}
.meta{font-size:.7em;color:var(--muted);margin:6px 4px 0;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.speak{background:none;border:1px solid var(--line);color:var(--muted);border-radius:999px;
  padding:3px 10px;cursor:pointer;font-size:.95em}
.speak:hover{border-color:var(--accent);color:var(--text)}

.note{border-radius:12px;padding:10px 13px;margin:0 0 14px;font-size:.85em;border:1px solid var(--line);white-space:pre-wrap}
.note.info{background:#13202b;border-color:#1d3a4a}
.note.warn{background:#2a2510;border-color:#4a4017;color:var(--warn)}
.note.err{background:#2a1414;border-color:#4a1d1d;color:var(--err)}
.action{border:1px solid var(--line);border-radius:14px;margin:0 0 12px;overflow:hidden;background:var(--panel)}
.action .head{background:var(--panel2);padding:8px 12px;font-size:.82em;display:flex;gap:8px;align-items:center}
.action .desc{padding:10px 12px;white-space:pre-wrap;font-size:.9em}
.result{border:1px solid var(--line);border-radius:14px;margin:0 0 14px;background:#10141c}
.result .head{padding:7px 12px;font-size:.78em;color:var(--muted);border-bottom:1px solid var(--line)}
.result pre{margin:0;padding:11px 13px;white-space:pre-wrap;font-size:.86em;max-height:340px;overflow:auto}
.result.ok .head{color:var(--ok)} .result.bad .head{color:var(--err)}
.review-preview{white-space:pre-wrap;overflow:auto;max-height:55vh;padding:12px;
  background:#0b0d13;border:1px solid var(--line);border-radius:10px;font-size:.85em}

.progress{border:1px solid var(--line);border-radius:14px;margin:0 0 12px;overflow:hidden;background:var(--panel)}
.progress .head{background:var(--panel2);padding:8px 12px;font-size:.82em;color:var(--accent)}
.progress .msg{padding:8px 12px 4px;font-size:.88em;color:var(--muted)}
.progress .track{margin:0 12px 8px;height:10px;background:var(--panel2);border:1px solid var(--line);border-radius:999px;overflow:hidden}
.progress .fill{display:block;height:100%;width:0%;background:linear-gradient(90deg,#41e0d0,#8b7cff);border-radius:999px;transition:width .2s ease}
.progress .pct{padding:0 12px 10px;font-size:.75em;color:var(--muted)}
.progress.done .head{color:var(--ok)}
.progress.spin .fill{width:30%;animation:progslide 1.1s ease-in-out infinite}
@keyframes progslide{0%{margin-left:-30%}100%{margin-left:100%}}

footer{border-top:1px solid var(--line);background:rgba(20,24,36,.9);padding:12px 18px 14px;
  width:100%}
.imgs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.imgs .chip{position:relative;width:54px;height:54px;border-radius:8px;overflow:hidden;border:1px solid var(--line)}
.imgs .chip img{width:100%;height:100%;object-fit:cover}
.imgs .chip span{position:absolute;top:0;right:0;background:#000a;color:#fff;cursor:pointer;
  width:18px;height:18px;text-align:center;line-height:18px;font-size:.7em}
.inrow{display:flex;gap:8px;align-items:flex-end}
textarea{flex:1;resize:none;background:var(--panel2);color:var(--text);border:1px solid var(--line);
  border-radius:14px;padding:12px 14px;font-family:inherit;font-size:1em;line-height:1.5;max-height:200px}
textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px #41e0d01a}
.send{background:linear-gradient(90deg,#41e0d0,#6fd0ee);color:#04201c;border:none;border-radius:14px;padding:0 18px;height:48px;
  font-weight:700;cursor:pointer;font-size:.95em}
.send:disabled{opacity:.5;cursor:default}
.hint{color:var(--muted);font-size:.7em;margin:6px 2px 0}

/* Modale (Modellwahl, Bestätigung) */
.overlay{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;z-index:20;padding:16px}
.overlay.open{display:flex}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:16px;max-width:560px;width:100%;
  max-height:80vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px #0008}
.modal h3{margin:0;padding:14px 16px;border-bottom:1px solid var(--line);font-size:1em}
.modal .scroll{overflow-y:auto;padding:10px}
.opt{display:block;width:100%;text-align:left;background:var(--panel2);color:var(--text);
  border:1px solid var(--line);border-radius:10px;padding:11px 13px;margin:6px 0;cursor:pointer;font-size:.92em}
.opt:hover{border-color:var(--accent)}
.opt .g{color:var(--muted);font-size:.75em;display:block}
.modal .foot{padding:10px 16px;border-top:1px solid var(--line);text-align:right}
.ghost{background:none;border:1px solid var(--line);color:var(--muted);border-radius:10px;padding:8px 14px;cursor:pointer}

@media (max-width:1100px){
  .shell{grid-template-columns:minmax(0,1fr)}
  .inspector{display:none}
  .inspector.open{display:block;position:fixed;right:0;top:0;bottom:0;width:min(320px,90vw);z-index:8}
}
@media (max-width:760px){
  .shell{display:block}
  .center{height:100vh}
  .mobile-only{display:inline-grid;place-items:center}
  .stat{display:none}
  main{padding:18px 12px 10px}
  .user .bubble{max-width:92%}
}

/* Lese-Themes */
body.t-light{--bg:#f4f1ea;--panel:#fffdf7;--panel2:#efe9dc;--text:#23211c;--muted:#6b6557;
  --line:#d9d2c2;--bubble-user:#e3ecf7;--bubble-ai:#fffdf7;--accent:#1a8f7d;--accent2:#2f6fd6}
body.t-cream{--bg:#fbf0d9;--panel:#fff6e6;--panel2:#f3e6c9;--text:#3a2f1c;--muted:#7a6a4c;
  --line:#e3d2ac;--bubble-user:#efe0bd;--bubble-ai:#fff6e6;--accent:#b5641a;--accent2:#9a5a12}
body.t-hc{--bg:#000;--panel:#0a0a0a;--panel2:#111;--text:#ffe600;--muted:#cccc00;--line:#444400;
  --bubble-user:#101000;--bubble-ai:#0a0a0a;--accent:#ffe600;--accent2:#fff;--ok:#7CFC00;--err:#ff5050}
</style>
</head>
<body>
<div class="shell">
<aside class="side">
  <div class="brand">
    <div class="mark">✦</div>
    <div>
      <div class="logo">NemiCLI</div>
      <div class="tag">dein Coding-Agent</div>
    </div>
  </div>
  <button id="newChatBtn" class="primary" type="button">＋ Neuer Chat</button>
  <div class="section-title">Chats</div>
  <div id="chatList" class="chatlist"></div>
  <div class="side-foot">
    <span class="dot"></span>
    <span id="hintSide">Verbinde …</span>
  </div>
</aside>

<section class="center">
  <header class="topbar">
    <button id="menuBtn" class="iconbtn mobile-only" type="button" title="Chats">☰</button>
    <button id="modelBtn" class="pill" title="Modell wechseln">Modell …</button>
    <span id="tokens" class="stat">—</span>
    <span id="helfer" class="helfer" title="Helfer-Agenten laufen"><span class="pdot"></span><span id="helferN">Subagenten aktiv: 0</span></span>
    <div class="spacer"></div>
    <button id="readBtn" class="iconbtn" type="button" title="Lesbarkeit">A𝐚</button>
    <button id="inspBtn" class="iconbtn mobile-only" type="button" title="Sitzung">ⓘ</button>
  </header>

  <main id="log"></main>

  <footer>
    <div id="imgs" class="imgs"></div>
    <div class="inrow">
      <button id="attachBtn" class="iconbtn" title="Bild anhängen">📎</button>
      <input id="file" type="file" accept="image/*" multiple hidden>
      <textarea id="box" rows="1" placeholder="Schreib etwas …  (Enter sendet, Shift+Enter = neue Zeile)"></textarea>
      <button id="send" class="send">Senden</button>
    </div>
    <div class="hint" id="hint">Verbinde …</div>
  </footer>
</section>

<aside class="inspector" id="inspector">
  <div class="section-title">Sitzung</div>
  <div class="card">
    <div class="kv"><span>Modell</span><b id="sModel">—</b></div>
    <div class="kv"><span>Chat</span><b id="sChat">—</b></div>
    <div class="kv"><span>Kontext</span><b id="sCtx">—</b></div>
    <div class="ctx"><i id="sCtxBar"></i></div>
    <div class="kv"><span>Tokens</span><b id="sTok">—</b></div>
  </div>
  <div class="section-title">Lesehilfen</div>
  <div id="reader" class="card reader">
    <label>Schrift
      <select id="fFont">
        <option value="'Atkinson Hyperlegible', system-ui, sans-serif">Atkinson (gut lesbar)</option>
        <option value="'OpenDyslexic', system-ui, sans-serif">OpenDyslexic (LRS)</option>
        <option value="system-ui, -apple-system, Segoe UI, sans-serif">System serifenlos</option>
        <option value="Georgia, 'Times New Roman', serif">Serif</option>
      </select>
    </label>
    <label>Größe <input id="fSize" type="range" min="14" max="30" step="1"></label>
    <label>Zeilenabstand <input id="fLh" type="range" min="12" max="26" step="1"></label>
    <label>Buchstabenabstand <input id="fLs" type="range" min="0" max="30" step="1"></label>
    <label>Wortabstand <input id="fWs" type="range" min="0" max="40" step="2"></label>
    <label>Theme
      <select id="fTheme">
        <option value="">Dunkel</option>
        <option value="t-light">Hell</option>
        <option value="t-cream">Creme (LRS)</option>
        <option value="t-hc">Hoher Kontrast</option>
      </select>
    </label>
    <label class="switch"><span>Antworten automatisch vorlesen</span>
      <input id="fTts" type="checkbox">
    </label>
  </div>
  <div class="warnbox">🛡 Verändernde Aktionen fragen weiter nach. WebUI nur auf 127.0.0.1.</div>
</aside>
</div>

<div id="overlay" class="overlay"><div class="modal">
  <h3 id="mTitle">…</h3>
  <div class="scroll" id="mBody"></div>
  <div class="foot"><button class="ghost" id="mCancel">Schließen</button></div>
</div></div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const $ = s => document.querySelector(s);
const log = $('#log');
let busy = false, autoTts = false;

function api(path, body){
  return fetch(path+'?token='+encodeURIComponent(TOKEN), {
    method:'POST', headers:{'Content-Type':'application/json','X-Token':TOKEN},
    body: JSON.stringify(body||{})
  });
}
function atBottom(){ return log.scrollHeight - log.scrollTop - log.clientHeight < 80; }
function toBottom(){ log.scrollTop = log.scrollHeight; }

/* --- winziger Markdown-Renderer (genug für Chat) --- */
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function md(src){
  const blocks=[];
  src = src.replace(/```([\s\S]*?)```/g,(m,c)=>{ blocks.push(c.replace(/^[a-zA-Z0-9]*\n/,'')); return '\uE000'+(blocks.length-1)+'\uE000'; });
  let h = esc(src);
  h = h.replace(/`([^`]+)`/g,(m,c)=>'<code>'+c+'</code>');
  h = h.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  h = h.replace(/(^|[^*])\*([^*]+)\*/g,'$1<em>$2</em>');
  h = h.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  h = h.replace(/^### (.*)$/gm,'<h4>$1</h4>').replace(/^## (.*)$/gm,'<h3>$1</h3>').replace(/^# (.*)$/gm,'<h3>$1</h3>');
  h = h.replace(/^[-*] (.*)$/gm,'<li>$1</li>');
  h = h.replace(/(<li>[\s\S]*?<\/li>)/g, m=>'<ul>'+m+'</ul>');
  h = h.split(/\n{2,}/).map(p=> p.match(/^<(h\d|ul|pre|li)/)? p : '<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
  h = h.replace(/\uE000(\d+)\uE000/g,(m,i)=>'<pre><code>'+esc(blocks[+i])+'</code></pre>');
  return h;
}

function addUser(text){
  const d=document.createElement('div'); d.className='msg user';
  d.innerHTML='<div class="who">Du</div><div class="bubble"></div>';
  d.querySelector('.bubble').textContent=text;
  log.appendChild(d); toBottom();
}
let cur=null, curRaw='', curThink='';
function startAI(){
  const d=document.createElement('div'); d.className='msg ai';
  d.innerHTML='<div class="who">NemiCLI</div>'
    +'<details class="think" style="display:none"><summary>💭 Gedanken</summary><div class="body"></div></details>'
    +'<div class="bubble"></div><div class="meta" style="display:none"></div>';
  log.appendChild(d); cur=d; curRaw=''; curThink='';
  return d;
}
function ensureAI(){ if(!cur) startAI(); return cur; }
function renderAI(){ ensureAI().querySelector('.bubble').innerHTML = md(curRaw||''); }
function pushThink(t){
  const d=ensureAI(); curThink+=t;
  const det=d.querySelector('.think'); det.style.display='block';
  det.querySelector('.body').textContent=curThink;
}
function finishAI(ev){
  const d=ensureAI(); curRaw = ev.clean!=null? ev.clean : curRaw; renderAI();
  const meta=d.querySelector('.meta');
  const parts=[];
  if(ev.output) parts.push('🔢 '+ev.output+' Tokens');
  if(ev.elapsed!=null) parts.push('⏱ '+ev.elapsed+'s');
  if(ev.tps) parts.push('⚡ '+ev.tps+' T/s');
  meta.innerHTML = parts.map(p=>'<span>'+p+'</span>').join('')
    + '<button class="speak" title="Vorlesen">🔊 vorlesen</button>';
  meta.style.display='flex';
  const txt = curRaw;
  meta.querySelector('.speak').onclick=()=>speak(txt, meta.querySelector('.speak'));
  if(autoTts && txt.trim()) speak(txt, meta.querySelector('.speak'));
  cur=null;
}
function note(cls,text){
  const d=document.createElement('div'); d.className='note '+cls; d.textContent=text;
  log.appendChild(d); if(atBottom())toBottom();
}
function actionCard(ev){
  const d=document.createElement('div'); d.className='action';
  d.innerHTML='<div class="head">⚙ Aktion'+(ev.needs_confirm?' · braucht Bestätigung':'')+'</div>'
             +'<div class="desc"></div>';
  d.querySelector('.desc').textContent=ev.desc; log.appendChild(d); toBottom(); cur=null;
}
function resultCard(ev){
  const d=document.createElement('div'); d.className='result '+(ev.ok===true?'ok':ev.ok===false?'bad':'');
  d.innerHTML='<div class="head">'+(ev.ok===true?'✓ Ergebnis':ev.ok===false?'✗ Fehler':'◇ Ausgabe · Status ungeprüft')+'</div><pre></pre>';
  d.querySelector('pre').textContent=ev.text; log.appendChild(d); if(atBottom())toBottom();
}
function receiptCard(ev){
  const labels={success:'erfolgreich',failed:'fehlgeschlagen',unverified:'Status ungeprüft',
                rejected:'abgelehnt · nicht ausgeführt',not_run:'nicht ausgeführt'};
  const rows=(ev.records||[]).map(r=>r.tool+': '+(labels[r.status]||'Abschluss unbestätigt'));
  note('info','Werkzeugablauf dieser Anfrage\n'+(rows.length?rows.join('\n'):'Keine Werkzeugaktion ausgeführt.'));
}
let progEl=null;
function showProgress(ev){
  if(!progEl){
    progEl=document.createElement('div'); progEl.className='progress spin';
    progEl.innerHTML='<div class="head"></div><div class="msg"></div>'
      +'<div class="track"><i class="fill"></i></div><div class="pct"></div>';
    log.appendChild(progEl);
  }
  progEl.querySelector('.head').textContent=ev.title||'🎨 male dein Bild';
  progEl.querySelector('.msg').textContent=ev.msg||'';
  const fill=progEl.querySelector('.fill');
  const pct=progEl.querySelector('.pct');
  if(ev.n){
    progEl.classList.remove('spin');
    fill.style.width=(ev.pct||0)+'%';
    pct.textContent=(ev.pct||0)+'%  ·  '+ev.i+'/'+ev.n;
  } else {
    progEl.classList.add('spin');
    fill.style.width='';
    pct.textContent='';
  }
  $('#hint').textContent=ev.msg||'male …';
  if(atBottom())toBottom();
}
function endProgress(){
  if(!progEl) return;
  progEl.classList.remove('spin'); progEl.classList.add('done');
  const fill=progEl.querySelector('.fill'); if(fill) fill.style.width='100%';
  const pct=progEl.querySelector('.pct'); if(pct && !pct.textContent) pct.textContent='fertig';
  progEl=null;
}

/* --- Vorlesen (Web Speech API) --- */
function speak(text, btn){
  if(!('speechSynthesis' in window)){ note('warn','Vorlesen wird vom Browser nicht unterstützt.'); return; }
  if(speechSynthesis.speaking){ speechSynthesis.cancel(); if(btn)btn.textContent='🔊 vorlesen'; return; }
  const u=new SpeechSynthesisUtterance(text.replace(/```[\s\S]*?```/g,' (Codeblock) '));
  u.lang='de-DE'; u.rate=.98;
  if(btn){ btn.textContent='⏹ stop'; u.onend=()=>btn.textContent='🔊 vorlesen'; }
  speechSynthesis.speak(u);
}

/* --- Confirm-Dialog --- */
function showConfirm(ev){
  $('#mTitle').textContent=ev.question; const body=$('#mBody'); body.innerHTML='';
  if(ev.preview!=null){
    const pre=document.createElement('pre'); pre.className='review-preview'; pre.tabIndex=0;
    pre.textContent=ev.preview; body.appendChild(pre);
  }
  ev.options.forEach(([val,label])=>{
    const b=document.createElement('button'); b.className='opt'; b.textContent=label;
    b.onclick=()=>{ api('/confirm',{id:ev.id,choice:val}); closeModal(); };
    body.appendChild(b);
  });
  $('#mCancel').onclick=()=>{ api('/confirm',{id:ev.id,choice:'no'}); closeModal(); };
  openModal();
  if(ev.preview!=null) body.querySelector('.review-preview').focus();
}
function openModal(){ $('#overlay').classList.add('open'); }
function closeModal(){ $('#overlay').classList.remove('open'); }

/* --- Modellwahl --- */
$('#modelBtn').onclick=async()=>{
  $('#mTitle').textContent='Modell wählen'; const body=$('#mBody');
  body.innerHTML='<p style="color:var(--muted);padding:8px">lade Modelle …</p>';
  $('#mCancel').onclick=closeModal; openModal();
  try{
    const r=await fetch('/models?token='+encodeURIComponent(TOKEN)); const list=await r.json();
    body.innerHTML='';
    if(!list.length){ body.innerHTML='<p style="padding:8px">Keine Modelle gefunden. Im Terminal mit /model einrichten.</p>'; return; }
    list.forEach(m=>{
      const b=document.createElement('button'); b.className='opt';
      b.innerHTML='<span class="g">'+esc(m.group)+'</span>'+esc(m.label);
      b.onclick=()=>{ api('/switch',{ref:m.ref}); closeModal(); };
      body.appendChild(b);
    });
  }catch(e){ body.innerHTML='<p style="padding:8px">Fehler beim Laden.</p>'; }
};

/* --- Bilder anhängen --- */
let pendImgs=[];
$('#attachBtn').onclick=()=>$('#file').click();
$('#file').onchange=e=>{
  [...e.target.files].forEach(f=>{
    const rd=new FileReader();
    rd.onload=()=>{ pendImgs.push(rd.result); drawImgs(); };
    rd.readAsDataURL(f);
  });
  e.target.value='';
};
function drawImgs(){
  const box=$('#imgs'); box.innerHTML='';
  pendImgs.forEach((src,i)=>{
    const c=document.createElement('div'); c.className='chip';
    c.innerHTML='<img src="'+src+'"><span>✕</span>';
    c.querySelector('span').onclick=()=>{ pendImgs.splice(i,1); drawImgs(); };
    box.appendChild(c);
  });
}

/* --- Senden --- */
const box=$('#box');
box.addEventListener('input',()=>{ box.style.height='auto'; box.style.height=Math.min(box.scrollHeight,200)+'px'; });
box.addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); doSend(); }});
$('#send').onclick=doSend;
function setBusy(b){ busy=b; $('#send').disabled=b; $('#hint').textContent=b?'NemiCLI denkt …':'Bereit.'; }
function setHelfer(n){ n=n|0; const h=$('#helfer'); if(!h) return; h.classList.toggle('on',n>0); $('#helferN').textContent='Subagenten aktiv: '+n; }
function doSend(){
  const t=box.value.trim(); if((!t && !pendImgs.length)||busy) return;
  addUser(t || '(Bild)'); api('/send',{text:t,images:pendImgs});
  box.value=''; box.style.height='auto'; pendImgs=[]; drawImgs(); setBusy(true);
}

/* --- SSE-Verbindung --- */
function connect(){
  const es=new EventSource('/events?token='+encodeURIComponent(TOKEN));
  es.onopen=()=>{ $('#hint').textContent='Bereit.'; if($('#hintSide')) $('#hintSide').textContent='verbunden'; };
  es.onerror=()=>{ $('#hint').textContent='Verbindung verloren – verbinde neu …'; };
  es.onmessage=e=>{
    let ev; try{ ev=JSON.parse(e.data); }catch(_){ return; }
    const stick=atBottom();
    switch(ev.t){
      case 'state': updateState(ev); break;
      case 'chat_switch': onChatSwitch(ev); break;
      case 'thinking': pushThink(ev.delta); break;
      case 'answer': curRaw+=ev.delta; renderAI(); break;
      case 'answer_end': finishAI(ev); break;
      case 'action': actionCard(ev); break;
      case 'progress': showProgress(ev); break;
      case 'progress_end': endProgress(); break;
      case 'action_result': resultCard(ev); break;
      case 'receipt': receiptCard(ev); break;
      case 'confirm': showConfirm(ev); break;
      case 'info': note('info',ev.text); break;
      case 'warn': note('warn',ev.text); break;
      case 'error': note('err',ev.text); break;
      case 'busy': setBusy(ev.on); break;
      case 'helfer': setHelfer(ev.n); break;
      case 'turn_end': setBusy(false); setHelfer(0); cur=null; break;
    }
    if(stick) toBottom();
  };
}
function updateState(ev){
  if('helfer' in ev) setHelfer(ev.helfer);
  const name = (ev.model||'—') + (ev.strength?(' · '+ev.strength):'');
  $('#modelBtn').textContent = name;
  $('#tokens').textContent = (ev.tokens? ('Σ '+ev.tokens+' Tok'):'') + (ev.chat?('  ·  Chat #'+ev.chat):'');
  if($('#sModel')) $('#sModel').textContent = ev.model||'—';
  if($('#sChat')) $('#sChat').textContent = ev.chat?('#'+ev.chat):'—';
  if($('#sTok')) $('#sTok').textContent = ev.tokens? (ev.tokens+' Tok'):'—';
  const max=ev.ctx_max||0, cur=ev.ctx||0;
  if($('#sCtx')) $('#sCtx').textContent = max? (cur+' / '+max) : '—';
  if($('#sCtxBar')) $('#sCtxBar').style.width = (max? Math.min(100, Math.round(100*cur/max)) : 0)+'%';
  renderChats(ev.chats||[], ev.chat);
}
function renderChats(list, current){
  const box=$('#chatList'); if(!box) return;
  box.innerHTML='';
  if(!list.length){
    box.innerHTML='<div style="padding:8px;font-size:.75em;color:var(--muted)">Noch keine gespeicherten Chats.</div>';
    return;
  }
  list.forEach(c=>{
    const b=document.createElement('button');
    b.type='button';
    b.className='chat'+(c.id===current?' active':'');
    b.textContent='#'+c.id+' · '+(c.title||'(leer)');
    b.title=c.updated||'';
    b.onclick=()=>{ if(busy) return; api('/resume',{id:c.id}); };
    box.appendChild(b);
  });
}
function onChatSwitch(ev){
  log.innerHTML=''; cur=null; curRaw=''; curThink=''; progEl=null;
  $('.side') && $('.side').classList.remove('open');
}

/* --- Lese-Einstellungen (im Browser gespeichert) --- */
const R=$(':root'); const LS=localStorage;
function setVar(k,v){ R.style.setProperty(k,v); }
function applyReader(){
  setVar('--font', $('#fFont').value);
  setVar('--fs', $('#fSize').value+'px');
  setVar('--lh', ($('#fLh').value/10));
  setVar('--ls', $('#fLs').value/100+'em');
  setVar('--ws', $('#fWs').value/100+'em');
  document.body.className = $('#fTheme').value;
  autoTts = $('#fTts').checked;
  const s={font:$('#fFont').value,size:$('#fSize').value,lh:$('#fLh').value,
    ls:$('#fLs').value,ws:$('#fWs').value,theme:$('#fTheme').value,tts:autoTts};
  LS.setItem('nemi-reader', JSON.stringify(s));
}
function loadReader(){
  let s={}; try{ s=JSON.parse(LS.getItem('nemi-reader')||'{}'); }catch(_){}
  if(s.font)$('#fFont').value=s.font;
  $('#fSize').value=s.size||18; $('#fLh').value=s.lh||17;
  $('#fLs').value=s.ls||0; $('#fWs').value=s.ws||0;
  if(s.theme)$('#fTheme').value=s.theme; $('#fTts').checked=!!s.tts;
  applyReader();
}
['#fFont','#fSize','#fLh','#fLs','#fWs','#fTheme','#fTts'].forEach(id=>$(id).addEventListener('input',applyReader));
$('#readBtn').onclick=()=>{
  const ins=$('#inspector');
  if(ins){ ins.classList.add('open'); ins.scrollIntoView({block:'nearest'}); }
};
$('#inspBtn') && ($('#inspBtn').onclick=()=>$('#inspector').classList.toggle('open'));
$('#menuBtn') && ($('#menuBtn').onclick=()=>$('.side').classList.toggle('open'));
$('#newChatBtn') && ($('#newChatBtn').onclick=()=>{ if(!busy) api('/newchat',{}); });
$('#overlay').addEventListener('click',e=>{ if(e.target.id==='overlay') closeModal(); });

loadReader(); connect();
</script>
</body>
</html>"""
