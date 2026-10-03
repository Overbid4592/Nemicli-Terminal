"""
spiele.py – Brettspiele im Browser gegen die Persönlichkeit: /spiel

Schach, Mühle, Dame und TicTacToe. Eigene Funktion, eigener Server, eigene
Seite – hat mit /webui nichts zu tun und teilt sich mit ihr keinen Code.

Aufteilung:
  • Die REGELN laufen im Browser (JavaScript, unten in PAGE). Der Browser weiß,
    welche Züge erlaubt sind, malt das Brett und erkennt Sieg/Remis.
  • Die PERSÖNLICHKEIT zieht selbst: sie bekommt das Brett als Text und die
    Liste der erlaubten Züge, wählt einen und sagt einen Satz dazu. Kein
    Schachprogramm im Hintergrund – sie spielt so gut oder schlecht, wie das
    Sprachmodell eben spielt. Wählt sie einen ungültigen Zug, bekommt sie es
    gesagt und darf es einmal nochmal versuchen; danach fällt ein zufälliger
    erlaubter Zug (und der Chat sagt das ehrlich).
  • Gefragt wird über `backend.ask_messages(system, messages)` – ein eigener,
    kleiner Verlauf je Partie. Der Hauptchat im Terminal bleibt unberührt,
    es laufen keine Werkzeuge, es gibt nichts freizugeben.

SICHERHEIT: nur 127.0.0.1, eigenes Sitzungs-Token in der URL. Die Seite kann
nichts außer Züge und Chat-Sätze an die Persönlichkeit schicken.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SPIELE = {
    "schach": "Schach",
    "muehle": "Mühle",
    "dame": "Dame",
    "tictactoe": "TicTacToe",
}

# Regeln in Kurzform – die Persönlichkeit kennt die Spiele, aber die Notation
# hier ist die des Browsers, und die muss sie treffen.
REGELN = {
    "schach": (
        "Schach, normale Regeln. Züge in Feld-zu-Feld-Schreibweise: e2e4, g1f3, "
        "Rochade als Königszug e1g1 / e1c1, Umwandlung mit Buchstabe dahinter: e7e8q. "
        "Das Brett unten: Großbuchstaben = Weiß, Kleinbuchstaben = Schwarz, . = leer, "
        "Zeile 8 oben."
    ),
    "muehle": (
        "Mühle auf 24 Punkten (a1 … g7, Standard-Schreibweise). Erst setzt jeder 9 "
        "Steine, dann wird entlang der Linien gezogen; wer nur noch 3 Steine hat, "
        "darf springen. Drei in einer Linie = Mühle, dann einen gegnerischen Stein "
        "nehmen (keinen aus einer Mühle, außer es gibt nur solche). Schreibweise: "
        "setzen 'd1', ziehen 'd1-d2', mit Wegnahme 'd1xf4' bzw. 'd1-d2xf4'. "
        "Verloren hat, wer weniger als 3 Steine hat oder nicht ziehen kann."
    ),
    "dame": (
        "Dame auf 8×8, deutsche Regeln: Steine ziehen ein Feld diagonal vorwärts und "
        "schlagen nur vorwärts; Schlagen ist Pflicht (Schlagzwang) und wird fortgesetzt, "
        "solange es geht. Eine Dame (Großbuchstabe) zieht und schlägt beliebig weit "
        "diagonal. Schreibweise: 'c3-d4' ziehen, 'c3xe5' schlagen, 'c3xe5xg7' Mehrfachschlag. "
        "Weiß (w/W) beginnt unten, Schwarz (b/B) oben."
    ),
    "tictactoe": (
        "TicTacToe 3×3. Felder a1 … c3 (Spalte a-c, Zeile 1-3, Zeile 3 oben). "
        "Drei in einer Reihe gewinnt."
    ),
}


def system_prompt(persona_text: str, name: str, nutzer: str, spiel: str, farbe_ki: str) -> str:
    if not spiel:                         # Plauderei im Spielemenü, noch keine Partie
        return (
            f"{persona_text}\n\n"
            f"=== Spieleraum ===\n"
            f"{nutzer} ist im Spielemenü von NemiCLI (Schach, Mühle, Dame, TicTacToe – du "
            "spielst gegen ihn, sobald er eins anklickt). Gerade wird nur geredet. "
            'Antworte NUR mit einem JSON-Objekt: {"zug": null, "text": "<deine Antwort>"}.'
        )
    titel = SPIELE.get(spiel, spiel)
    return (
        f"{persona_text}\n\n"
        f"=== Spielrunde: {titel} ===\n"
        f"Du spielst gerade {titel} gegen {nutzer} – im Browser, auf einem echten Brett. "
        f"Du spielst {farbe_ki}. {REGELN.get(spiel, '')}\n\n"
        "So läuft es: Jede Nachricht zeigt dir das Brett und die Liste der ERLAUBTEN "
        "Züge. Du wählst genau einen davon – buchstabengetreu aus der Liste – und "
        "sagst einen kurzen Satz dazu (Kommentar, Spott, Lob, wie es zu dir passt). "
        "Überleg dir ruhig, welcher Zug gut ist: Figuren decken, Drohungen sehen, "
        "nichts verschenken.\n"
        "Antworte NUR mit einem JSON-Objekt, nichts davor, nichts danach:\n"
        '{"zug": "<ein Zug aus der Liste>", "text": "<1–2 Sätze an ' + nutzer + '>"}\n'
        "Steht in der Nachricht kein Zug an (nur Plauderei oder das Spiel ist vorbei), "
        'antwortest du mit {"zug": null, "text": "<deine Antwort>"}.'
    )


def _brett_nachricht(p: dict) -> str:
    teile = []
    if p.get("verlauf"):
        teile.append("Bisherige Züge: " + ", ".join(p["verlauf"][-30:]))
    teile.append("Brett:\n" + (p.get("brett") or "").rstrip())
    if p.get("hinweis"):
        teile.append(p["hinweis"])
    if p.get("chat"):
        teile.append(f"{p.get('nutzer', 'Der Nutzer')} sagt: {p['chat']}")
    zuege = p.get("zuege") or []
    if zuege:
        teile.append("Du bist dran. ERLAUBTE Züge: " + ", ".join(zuege))
    else:
        teile.append("Kein Zug fällig – nur antworten.")
    return "\n\n".join(teile)


def messages(p: dict) -> list[dict]:
    """Eigener kurzer Verlauf: die letzten Wechsel (Brett → Antwort) plus jetzt."""
    out: list[dict] = []
    for h in (p.get("gespraech") or [])[-8:]:
        rolle = "assistant" if h.get("von") == "ki" else "user"
        out.append({"role": rolle, "content": str(h.get("text", ""))[:1500]})
    out.append({"role": "user", "content": _brett_nachricht(p)})
    return out


_JSON = re.compile(r"\{.*?\}", re.S)


def parse(antwort: str, zuege: list[str]) -> tuple[str | None, str, bool]:
    """(zug, text, gueltig). zug=None, wenn keiner erwartet war oder keiner passt."""
    antwort = (antwort or "").strip()
    zug, text = None, ""
    for m in _JSON.finditer(antwort):
        try:
            d = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(d, dict) and ("zug" in d or "text" in d):
            zug = d.get("zug")
            text = str(d.get("text") or "")
            break
    else:
        text = antwort
    if not zuege:
        return None, text or antwort, True
    zug = str(zug or "").strip()
    norm = {z.lower().replace(" ", ""): z for z in zuege}
    if zug.lower().replace(" ", "") in norm:
        return norm[zug.lower().replace(" ", "")], text, True
    # Vielleicht steht der Zug nur im Text (manche Modelle ignorieren das JSON)
    for z in sorted(zuege, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(z.lower()) + r"(?![a-z0-9])", antwort.lower()):
            return z, text or antwort, True
    return None, text or antwort, False


async def zug_holen(backend, system: str, p: dict) -> dict:
    """Fragt die Persönlichkeit; zweiter Versuch bei ungültigem Zug, dann Zufall."""
    zuege = list(p.get("zuege") or [])
    antwort = await backend.ask_messages(system, messages(p))
    zug, text, ok = parse(antwort, zuege)
    if zuege and not ok:
        p2 = dict(p)
        p2["hinweis"] = (f"Dein letzter Zug „{(zug or antwort)[:40]}“ ist NICHT erlaubt. "
                         "Nimm einen aus der Liste, buchstabengetreu.")
        antwort = await backend.ask_messages(system, messages(p2))
        zug, text, ok = parse(antwort, zuege)
    zufall = False
    if zuege and not ok:
        zug = random.choice(zuege)
        zufall = True
        text = (text or "").strip()
    return {"zug": zug, "text": text, "zufall": zufall}


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class SpieleServer:
    """Winziger lokaler Server: liefert die Seite, nimmt Züge/Chat entgegen und
    reicht sie an `denke(payload) -> dict` auf der asyncio-Schleife der CLI."""

    def __init__(self, loop, denke):
        self.loop = loop
        self.denke = denke
        self.token = secrets.token_urlsafe(18)
        self.url = ""
        self.httpd = None

    def _antwort(self, payload: dict) -> dict:
        fut = asyncio.run_coroutine_threadsafe(self.denke(payload), self.loop)
        return fut.result(timeout=300)

    def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        server = self

        def token_ok(parsed, headers) -> bool:
            q = parse_qs(parsed.query)
            tok = (q.get("token", [""])[0]) or headers.get("X-Token", "")
            return secrets.compare_digest(tok, server.token)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, obj, code=200):
                self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path in ("/", "/index.html"):
                    self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                    return
                self._send(404)

            def do_POST(self):
                parsed = urlparse(self.path)
                if not token_ok(parsed, self.headers):
                    self._send(403); return
                n = int(self.headers.get("Content-Length", 0) or 0)
                try:
                    data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n > 0 else {}
                except Exception:
                    data = {}
                if parsed.path == "/zug":
                    if data.get("spiel") not in SPIELE and data.get("spiel") != "":
                        self._json({"fehler": "unbekanntes Spiel"}, 400); return
                    try:
                        self._json(server._antwort(data))
                    except Exception as e:
                        self._json({"fehler": str(e)}, 500)
                    return
                self._send(404)

        class Quiet(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                et = sys.exc_info()[0]
                if et is not None and issubclass(et, (ConnectionError, BrokenPipeError, TimeoutError)):
                    return
                super().handle_error(request, client_address)

        self.httpd = Quiet((host, port), Handler)
        real_port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="nemicli-spiele").start()
        self.url = f"http://{host}:{real_port}/?token={self.token}"
        return self.url

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Die Seite – Regeln, Brett und Chat in einer Datei, keine fremden Dateien.
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NemiCLI · Spiele</title>
<style>
:root{
  --bg:#0b0d14; --panel:#141824; --panel2:#1c2130; --text:#e8eaf2; --muted:#8b93a7;
  --accent:#41e0d0; --accent2:#8b7cff; --line:#2a3144; --ok:#54d18c; --warn:#f0c674; --err:#ff6b6b;
  --hell:#d9c9a8; --dunkel:#6b4e3d; --side:340px;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
  font:16px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
#app{display:grid;grid-template-columns:1fr var(--side);height:100vh}
#links{display:flex;flex-direction:column;min-width:0}
#kopf{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1px solid var(--line);background:var(--panel)}
#kopf h1{font-size:18px;margin:0;color:var(--accent);letter-spacing:.5px}
#kopf .info{color:var(--muted);flex:1}
button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;
  padding:8px 14px;cursor:pointer;font:inherit}
button:hover{border-color:var(--accent)}
button.primar{background:var(--accent);color:#08121a;border-color:var(--accent);font-weight:700}
#buehne{flex:1;display:flex;align-items:center;justify-content:center;padding:18px;min-height:0}
/* Menü */
#menue{display:grid;grid-template-columns:repeat(2,minmax(220px,300px));gap:18px}
.kachel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:26px 20px;
  cursor:pointer;text-align:center;transition:.15s}
.kachel:hover{border-color:var(--accent);transform:translateY(-2px)}
.kachel .icon{font-size:52px;line-height:1}
.kachel .name{font-size:20px;margin-top:10px;font-weight:700}
.kachel .sub{color:var(--muted);font-size:14px;margin-top:4px}
#wahl{display:flex;flex-direction:column;gap:14px;align-items:center;text-align:center}
#wahl .zeile{display:flex;gap:10px}
/* Bretter */
.brett{display:grid;user-select:none;box-shadow:0 10px 40px #0008;border-radius:6px;overflow:hidden}
.brett .f{display:flex;align-items:center;justify-content:center;position:relative;cursor:pointer}
.brett .f.hell{background:var(--hell)} .brett .f.dunkel{background:var(--dunkel)}
.brett .f.wahl{outline:3px solid var(--accent);outline-offset:-3px}
.brett .f.ziel::after{content:"";position:absolute;width:26%;height:26%;border-radius:50%;background:#41e0d0aa}
.brett .f.ziel.schlag::after{width:88%;height:88%;background:none;border:4px solid #ff6b6bcc;border-radius:50%}
.brett .f.letzt{box-shadow:inset 0 0 0 100px #8b7cff33}
.brett .f .fig{font-size:min(6vh,6vw);line-height:1;text-shadow:0 2px 3px #0009}
.brett .f .fig.w{color:#fff} .brett .f .fig.b{color:#111}
.stein{width:74%;height:74%;border-radius:50%;box-shadow:0 3px 6px #0008,inset 0 -4px 0 #0004;
  display:flex;align-items:center;justify-content:center;font-weight:900;font-size:1.4em}
.stein.w{background:#f4f1e6;color:#8a6d3b} .stein.b{background:#2a2a2e;color:#e8c56d;border:1px solid #555}
.koord{position:absolute;font-size:11px;color:#0009;left:3px;top:2px} .dunkel .koord{color:#fff9}
#ttt .f{font-size:min(14vh,14vw);font-weight:900;background:var(--panel);border:2px solid var(--line)}
#ttt .f.X{color:var(--accent)} #ttt .f.O{color:var(--accent2)}
/* Mühle */
#muehle{position:relative;width:min(78vh,78vw);height:min(78vh,78vw)}
#muehle svg{width:100%;height:100%;overflow:visible}
#muehle line{stroke:#c9b48a;stroke-width:4;stroke-linecap:round}
#muehle .p{fill:#c9b48a;cursor:pointer} #muehle .p:hover{fill:var(--accent)}
#muehle .s.w{fill:#f4f1e6} #muehle .s.b{fill:#2a2a2e;stroke:#777;stroke-width:1.5}
#muehle .wahl{stroke:var(--accent);stroke-width:5} #muehle .ziel{fill:#41e0d0aa}
#muehle .schlag{stroke:#ff6b6b;stroke-width:5;stroke-dasharray:6 4}
#muehle text{fill:#8b93a7;font-size:14px;pointer-events:none}
/* Chat */
#chat{display:flex;flex-direction:column;border-left:1px solid var(--line);background:var(--panel);min-height:0}
#chat .kopf{padding:12px 14px;border-bottom:1px solid var(--line);font-weight:700;display:flex;justify-content:space-between;align-items:center}
#chat .kopf .status{font-weight:400;color:var(--muted);font-size:13px}
#verlauf{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.msg{padding:9px 12px;border-radius:12px;max-width:95%;font-size:15px;white-space:pre-wrap}
.msg.ki{background:#161a26;border:1px solid var(--line);align-self:flex-start}
.msg.du{background:#1a2a3a;align-self:flex-end}
.msg.sys{color:var(--muted);font-size:13px;align-self:center;text-align:center}
.msg .wer{font-size:12px;color:var(--accent);margin-bottom:2px}
#eingabe{display:flex;gap:8px;padding:10px;border-top:1px solid var(--line)}
#eingabe input{flex:1;background:var(--bg);border:1px solid var(--line);border-radius:8px;color:var(--text);padding:9px 11px;font:inherit}
#eingabe input:focus{outline:none;border-color:var(--accent)}
.denkt{color:var(--muted);font-style:italic;font-size:13px;padding:2px 12px}
.ende{padding:10px 14px;background:#1a2a3a;border-radius:10px;text-align:center;font-weight:700}
@media (max-width:820px){#app{grid-template-columns:1fr;grid-template-rows:1fr 40vh}#chat{border-left:0;border-top:1px solid var(--line)}}
</style>
</head>
<body>
<div id="app">
  <div id="links">
    <div id="kopf">
      <h1>🎲 NemiCLI · Spiele</h1>
      <span class="info" id="info">Wähle ein Spiel</span>
      <button id="btn-neu" style="display:none">Neue Partie</button>
      <button id="btn-menue" style="display:none">Zum Menü</button>
    </div>
    <div id="buehne"></div>
  </div>
  <div id="chat">
    <div class="kopf"><span id="kiname">…</span><span class="status" id="status"></span></div>
    <div id="verlauf"></div>
    <div id="eingabe"><input id="text" placeholder="Sag ihr was …" autocomplete="off"><button id="senden">➤</button></div>
  </div>
</div>
<script>
'use strict';
const T = new URLSearchParams(location.search).get('token') || '';
const $ = s => document.querySelector(s);
const buehne = $('#buehne'), info = $('#info'), verlauf = $('#verlauf');
let KI = 'Sie', DU = 'Du';
let spiel = null;          // aktuelles Spiel-Objekt
let beschaeftigt = false;  // wartet auf die Persönlichkeit
let gespraech = [];        // [{von:'ki'|'du', text}] – geht als kurzer Verlauf mit

// ---------------------------------------------------------------- Chat
function msg(von, text, wer){
  const d = document.createElement('div');
  d.className = 'msg ' + von;
  if (wer){ const w = document.createElement('div'); w.className='wer'; w.textContent = wer; d.appendChild(w); }
  d.appendChild(document.createTextNode(text));
  verlauf.appendChild(d); verlauf.scrollTop = verlauf.scrollHeight;
}
function sys(text){ msg('sys', text); }
function denkt(an){
  let d = $('#denkt');
  if (an && !d){ d = document.createElement('div'); d.id='denkt'; d.className='denkt'; d.textContent = KI + ' überlegt …'; verlauf.appendChild(d); verlauf.scrollTop = verlauf.scrollHeight; }
  if (!an && d) d.remove();
  $('#status').textContent = an ? 'denkt …' : '';
}
async function frage(payload){
  beschaeftigt = true; denkt(true);
  try{
    const r = await fetch('/zug', {method:'POST', headers:{'Content-Type':'application/json','X-Token':T}, body: JSON.stringify(payload)});
    const d = await r.json();
    if (d.fehler){ sys('Fehler: ' + d.fehler); return null; }
    return d;
  }catch(e){ sys('Keine Verbindung zum Terminal – läuft NemiCLI noch?'); return null; }
  finally{ beschaeftigt = false; denkt(false); }
}
function nutzlast(chat){
  const p = {spiel: spiel.id, brett: spiel.text(), zuege: [], verlauf: spiel.verlauf.slice(), gespraech: gespraech.slice(-8), chat: chat || '', farbe_ki: spiel.name(spiel.ki)};
  if (!spiel.ende && spiel.amZug === spiel.ki) p.zuege = spiel.zuege();
  return p;
}
async function kiZug(){
  if (!spiel || spiel.ende || spiel.amZug !== spiel.ki) return;
  const p = nutzlast('');
  const d = await frage(p);
  if (!d || !spiel) return;
  gespraech.push({von:'du', text:'(Brett gezeigt, Zug gefragt)'});
  if (d.text){ msg('ki', d.text, KI); gespraech.push({von:'ki', text: d.text}); }
  if (d.zug && p.zuege.includes(d.zug)){
    spiel.zieh(d.zug);
    if (d.zufall) sys(KI + ' hat zweimal danebengegriffen – der Zug ' + d.zug + ' kam per Zufall.');
    else sys(KI + ' zieht ' + d.zug);
    render();
  } else if (!spiel.ende) {
    sys('Kein gültiger Zug gekommen – ich frage nochmal.');
    setTimeout(kiZug, 600);
  }
}
async function senden(){
  const t = $('#text').value.trim();
  if (!t) return;
  if (beschaeftigt){ sys(KI + ' überlegt noch – gleich nochmal.'); return; }
  $('#text').value = '';
  msg('du', t, DU); gespraech.push({von:'du', text:t});
  const p = spiel ? nutzlast(t) : {spiel: '', brett: '', zuege: [], verlauf: [], gespraech: gespraech.slice(-8), chat: t, farbe_ki: ''};
  const d = await frage(p);
  if (!d) return;
  if (d.text){ msg('ki', d.text, KI); gespraech.push({von:'ki', text:d.text}); }
  if (spiel && d.zug && p.zuege.includes(d.zug)){ spiel.zieh(d.zug); sys(KI + ' zieht ' + d.zug); render(); }
}
$('#senden').onclick = senden;
$('#text').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); senden(); } });

// ---------------------------------------------------------------- Menü
const KACHELN = [
  ['schach','♞','Schach','König matt setzen'],
  ['muehle','◉','Mühle','Drei in einer Linie'],
  ['dame','⛂','Dame','Schlagzwang, deutsche Regeln'],
  ['tictactoe','✕','TicTacToe','Schnell und gemein'],
];
function menue(){
  spiel = null; gespraech = [];
  info.textContent = 'Wähle ein Spiel';
  $('#btn-neu').style.display = 'none'; $('#btn-menue').style.display = 'none';
  buehne.innerHTML = '<div id="menue"></div>';
  for (const [id, icon, name, sub] of KACHELN){
    const k = document.createElement('div'); k.className = 'kachel';
    k.innerHTML = `<div class="icon">${icon}</div><div class="name">${name}</div><div class="sub">${sub}</div>`;
    k.onclick = () => wahl(id);
    $('#menue').appendChild(k);
  }
}
function wahl(id){
  const name = KACHELN.find(k => k[0] === id)[2];
  info.textContent = name;
  const seiten = id === 'tictactoe' ? [['X','X (fängt an)'],['O','O']] : [['w','Weiß (fängt an)'],['b','Schwarz']];
  buehne.innerHTML = `<div id="wahl"><div style="font-size:22px;font-weight:700">${name}</div><div>Was spielst du?</div><div class="zeile"></div></div>`;
  for (const [f, label] of seiten){
    const b = document.createElement('button'); b.className = 'primar'; b.textContent = label;
    b.onclick = () => start(id, f);
    $('#wahl .zeile').appendChild(b);
  }
}
function start(id, du){
  gespraech = [];
  spiel = id === 'schach' ? new Schach(du) : id === 'dame' ? new Dame(du) : id === 'muehle' ? new Muehle(du) : new TTT(du);
  $('#btn-neu').style.display = ''; $('#btn-menue').style.display = '';
  verlauf.innerHTML = '';
  sys('Neue Partie ' + KACHELN.find(k => k[0] === id)[2] + ' – du spielst ' + spiel.name(du) + '.');
  render();
  if (spiel.amZug === spiel.ki) kiZug();
}
$('#btn-neu').onclick = () => { if (spiel) start(spiel.id, spiel.du); };
$('#btn-menue').onclick = menue;

function render(){
  if (!spiel) return;
  spiel.render();
  const wer = spiel.ende ? spiel.ende : (spiel.amZug === spiel.du ? 'Du bist dran' : KI + ' ist dran');
  info.textContent = spiel.titel + ' · ' + wer + (spiel.extra ? ' · ' + spiel.extra : '');
  if (spiel.ende && !spiel.endeGemeldet){
    spiel.endeGemeldet = true;
    const e = document.createElement('div'); e.className = 'ende'; e.textContent = spiel.ende; verlauf.appendChild(e);
    // Die Persönlichkeit darf das letzte Wort haben
    const p = nutzlast('');
    p.zuege = []; p.hinweis = 'Die Partie ist vorbei: ' + spiel.ende + '. Sag etwas dazu.';
    frage(p).then(d => { if (d && d.text){ msg('ki', d.text, KI); gespraech.push({von:'ki', text:d.text}); } });
  }
}
// Klick eines Menschen auf ein Feld – jedes Spiel setzt klick(feld)
function menschKlick(f){
  if (!spiel || spiel.ende || beschaeftigt || spiel.amZug !== spiel.du) return;
  const zug = spiel.klick(f);
  render();
  if (zug){ sys('Du ziehst ' + zug); kiZug(); }
}

// ---------------------------------------------------------------- Hilfen
const BUCHST = 'abcdefgh';
const feldName = (c, r) => BUCHST[c] + (r + 1);       // c,r 0-basiert, r=0 ist Zeile 1 (unten)
function brettGrid(id, n, aufKlick, unten){
  // n×n Felder, gezeichnet von oben (Zeile n-1) nach unten (Zeile 0); 'unten' = welche Farbe unten sitzt
  const g = document.createElement('div'); g.id = id; g.className = 'brett';
  const size = 'min(80vh,80vw)';
  g.style.gridTemplateColumns = `repeat(${n},1fr)`; g.style.width = size; g.style.height = size;
  const zeilen = [], spalten = [];
  for (let i = 0; i < n; i++){ zeilen.push(unten === 'b' ? i : n - 1 - i); spalten.push(unten === 'b' ? n - 1 - i : i); }
  for (const r of zeilen) for (const c of spalten){
    const f = document.createElement('div'); f.className = 'f ' + (((r + c) % 2) ? 'hell' : 'dunkel'); f.dataset.f = feldName(c, r);
    if (c === spalten[0] || r === zeilen[zeilen.length - 1]){ const k = document.createElement('span'); k.className = 'koord'; k.textContent = feldName(c, r); f.appendChild(k); }
    f.onclick = () => aufKlick(feldName(c, r));
    g.appendChild(f);
  }
  return g;
}

// ---------------------------------------------------------------- TicTacToe
class TTT {
  constructor(du){ this.id='tictactoe'; this.titel='TicTacToe'; this.du=du; this.ki = du==='X'?'O':'X'; this.amZug='X'; this.b = Array(9).fill(''); this.verlauf=[]; this.ende=''; }
  name(f){ return f; }
  idx(f){ return (f.charCodeAt(1) - 49) * 3 + (f.charCodeAt(0) - 97); }
  fname(i){ return 'abc'[i % 3] + (Math.floor(i / 3) + 1); }
  zuege(){ return this.b.map((v, i) => v ? null : this.fname(i)).filter(Boolean); }
  text(){ let s=''; for (let r=2;r>=0;r--){ s += (r+1)+' '; for (let c=0;c<3;c++) s += (this.b[r*3+c]||'.')+' '; s+='\n'; } return s + '  a b c'; }
  zieh(f){ const i = this.idx(f); this.b[i] = this.amZug; this.verlauf.push(this.amZug + ':' + f); this.pruefe(); this.amZug = this.amZug==='X'?'O':'X'; }
  pruefe(){
    const L=[[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
    for (const [a,b,c] of L) if (this.b[a] && this.b[a]===this.b[b] && this.b[a]===this.b[c]){ this.ende = (this.b[a]===this.du ? 'Du gewinnst' : KI + ' gewinnt') + ' mit ' + this.b[a] + '!'; return; }
    if (this.b.every(Boolean)) this.ende = 'Unentschieden.';
  }
  klick(f){ if (this.b[this.idx(f)]) return null; this.zieh(f); return f; }
  render(){
    if (!$('#ttt')){ buehne.innerHTML=''; const g = brettGrid('ttt', 3, menschKlick, 'w'); buehne.appendChild(g); }
    for (const el of document.querySelectorAll('#ttt .f')){ const v = this.b[this.idx(el.dataset.f)]; el.className = 'f ' + v; el.textContent = v; }
  }
}

// ---------------------------------------------------------------- Dame (deutsche Regeln)
class Dame {
  constructor(du){
    this.id='dame'; this.titel='Dame'; this.du=du; this.ki = du==='w'?'b':'w'; this.amZug='w'; this.verlauf=[]; this.ende='';
    this.b = {}; // feld -> 'w','b','W','B'
    for (let r=0;r<8;r++) for (let c=0;c<8;c++) if ((r+c)%2===0){ if (r<3) this.b[feldName(c,r)]='w'; if (r>4) this.b[feldName(c,r)]='b'; }
    this.wahl=null; this.teil=null; this.ohneSchlag=0;
  }
  name(f){ return f==='w'?'Weiß':'Schwarz'; }
  farbe(p){ return p ? p.toLowerCase() : null; }
  cr(f){ return [f.charCodeAt(0)-97, f.charCodeAt(1)-49]; }
  ok(c,r){ return c>=0&&c<8&&r>=0&&r<8; }
  // Schlagfolgen eines Steins von f aus (rekursiv), Brett b, bereits geschlagene 'genommen'
  schlaege(b, f, genommen){
    const p = b[f], farbe = this.farbe(p), dame = p === p.toUpperCase();
    const [c,r] = this.cr(f); const out = [];
    const richtungen = dame ? [[1,1],[-1,1],[1,-1],[-1,-1]] : (farbe==='w' ? [[1,1],[-1,1]] : [[1,-1],[-1,-1]]);
    for (const [dc,dr] of richtungen){
      let cc=c+dc, rr=r+dr;
      if (dame){ while (this.ok(cc,rr) && !b[feldName(cc,rr)]){ cc+=dc; rr+=dr; } }
      if (!this.ok(cc,rr)) continue;
      const opfer = feldName(cc,rr);
      if (!b[opfer] || this.farbe(b[opfer])===farbe || genommen.includes(opfer)) continue;
      let lc=cc+dc, lr=rr+dr;
      while (this.ok(lc,lr) && !b[feldName(lc,lr)]){
        const land = feldName(lc,lr);
        const b2 = Object.assign({}, b); delete b2[f]; b2[land] = p; delete b2[opfer]; // Opfer bleibt bis zum Ende "geblockt" – hier vereinfachend entfernt
        const weiter = this.schlaege(b2, land, genommen.concat(opfer));
        if (weiter.length) for (const w of weiter) out.push([land].concat(w)); else out.push([land]);
        if (!dame) break;
        lc+=dc; lr+=dr;
      }
    }
    return out;
  }
  zuegeVon(b, f){
    const p = b[f]; if (!p || this.farbe(p)!==this.amZug) return [];
    const s = this.schlaege(b, f, []);
    if (s.length) return s.map(seq => f + 'x' + seq.join('x'));
    return null; // kein Schlag – normale Züge nur, wenn niemand schlagen kann
  }
  zuege(){
    const b = this.b; let alle = [], schlag = false;
    for (const f in b){ if (this.farbe(b[f])!==this.amZug) continue; const z = this.zuegeVon(b,f); if (z){ alle = alle.concat(z); schlag = true; } }
    if (schlag) return alle;
    for (const f in b){ if (this.farbe(b[f])!==this.amZug) continue;
      const p=b[f], dame = p===p.toUpperCase(); const [c,r]=this.cr(f);
      const richtungen = dame ? [[1,1],[-1,1],[1,-1],[-1,-1]] : (this.amZug==='w' ? [[1,1],[-1,1]] : [[1,-1],[-1,-1]]);
      for (const [dc,dr] of richtungen){ let cc=c+dc, rr=r+dr; while (this.ok(cc,rr) && !b[feldName(cc,rr)]){ alle.push(f+'-'+feldName(cc,rr)); if (!dame) break; cc+=dc; rr+=dr; } }
    }
    return alle;
  }
  zieh(z){
    const teile = z.split(/[-x]/); const von = teile[0]; let p = this.b[von]; delete this.b[von];
    let cur = von;
    if (z.includes('x')){
      for (let i=1;i<teile.length;i++){ const [c1,r1]=this.cr(cur), [c2,r2]=this.cr(teile[i]); const dc=Math.sign(c2-c1), dr=Math.sign(r2-r1);
        let cc=c1+dc, rr=r1+dr; while (feldName(cc,rr)!==teile[i]){ delete this.b[feldName(cc,rr)]; cc+=dc; rr+=dr; } cur = teile[i]; }
      this.ohneSchlag = 0;
    } else { cur = teile[1]; this.ohneSchlag++; }
    const r = this.cr(cur)[1];
    if ((p==='w' && r===7) || (p==='b' && r===0)) p = p.toUpperCase();
    this.b[cur] = p; this.verlauf.push(z); this.letzt = [von, cur];
    this.amZug = this.amZug==='w'?'b':'w'; this.wahl=null; this.teil=null;
    const n = f => Object.values(this.b).filter(x => this.farbe(x)===f).length;
    if (n(this.amZug)===0 || this.zuege().length===0){ const g = this.amZug==='w'?'b':'w'; this.ende = (g===this.du?'Du gewinnst':KI+' gewinnt') + ' – ' + this.name(this.amZug) + ' kann nicht mehr ziehen.'; }
    else if (this.ohneSchlag >= 50) this.ende = 'Remis – 50 Züge ohne Schlag.';
  }
  klick(f){
    const alle = this.zuege();
    if (this.wahl){
      // Ziel? Bei Mehrfachschlag Schritt für Schritt: Präfix sammeln
      const pref = this.teil ? this.teil : this.wahl;
      const kand = alle.filter(z => z.startsWith(pref + '-' + f) || z.startsWith(pref + 'x' + f));
      if (kand.length){
        const voll = kand.find(z => z === pref + '-' + f || z === pref + 'x' + f);
        if (voll){ this.zieh(voll); return voll; }
        this.teil = pref + 'x' + f; return null;   // Schlag geht weiter
      }
      if (this.teil) return null;                     // mitten im Mehrfachschlag: nur weiter
    }
    if (this.b[f] && this.farbe(this.b[f])===this.amZug && alle.some(z => z.startsWith(f))){ this.wahl = f; this.teil = null; }
    else { this.wahl = null; }
    return null;
  }
  text(){ let s=''; for (let r=7;r>=0;r--){ s += (r+1)+' '; for (let c=0;c<8;c++) s += (this.b[feldName(c,r)]||'.')+' '; s+='\n'; } return s+'  a b c d e f g h'; }
  render(){
    if (!$('#dame')){ buehne.innerHTML=''; buehne.appendChild(brettGrid('dame', 8, menschKlick, this.du)); }
    const alle = this.amZug===this.du ? this.zuege() : [];
    const pref = this.teil || this.wahl;
    for (const el of document.querySelectorAll('#dame .f')){
      const f = el.dataset.f, p = this.b[f];
      el.classList.remove('wahl','ziel','schlag','letzt');
      for (const ch of Array.from(el.children)) if (!ch.classList.contains('koord')) ch.remove();
      if (p){ const s = document.createElement('div'); s.className = 'stein ' + this.farbe(p); s.textContent = p===p.toUpperCase() ? '♛' : ''; el.appendChild(s); }
      if (this.letzt && this.letzt.includes(f)) el.classList.add('letzt');
      if (this.wahl===f) el.classList.add('wahl');
      if (pref && alle.some(z => z.startsWith(pref+'-'+f) || z.startsWith(pref+'x'+f))){ el.classList.add('ziel'); if (alle.some(z => z.startsWith(pref+'x'+f))) el.classList.add('schlag'); }
    }
  }
}

// ---------------------------------------------------------------- Mühle
const M_PUNKTE = ['a1','d1','g1','b2','d2','f2','c3','d3','e3','a4','b4','c4','e4','f4','g4','c5','d5','e5','b6','d6','f6','a7','d7','g7'];
const M_MUEHLEN = [['a1','d1','g1'],['b2','d2','f2'],['c3','d3','e3'],['a4','b4','c4'],['e4','f4','g4'],['c5','d5','e5'],['b6','d6','f6'],['a7','d7','g7'],
  ['a1','a4','a7'],['b2','b4','b6'],['c3','c4','c5'],['d1','d2','d3'],['d5','d6','d7'],['e3','e4','e5'],['f2','f4','f6'],['g1','g4','g7']];
const M_NACHBARN = {};
for (const m of M_MUEHLEN){ for (let i=0;i<3;i++){ M_NACHBARN[m[i]] = M_NACHBARN[m[i]] || []; if (i>0) M_NACHBARN[m[i]].push(m[i-1]); if (i<2) M_NACHBARN[m[i]].push(m[i+1]); } }
class Muehle {
  constructor(du){ this.id='muehle'; this.titel='Mühle'; this.du=du; this.ki=du==='w'?'b':'w'; this.amZug='w'; this.b={}; this.hand={w:9,b:9}; this.verlauf=[]; this.ende=''; this.wahl=null; this.ohne=0; }
  name(f){ return f==='w'?'Weiß':'Schwarz'; }
  anzahl(f){ return Object.values(this.b).filter(x=>x===f).length; }
  inMuehle(b, f, farbe){ return M_MUEHLEN.some(m => m.includes(f) && m.every(p => b[p]===farbe)); }
  phase(f){ return this.hand[f] > 0 ? 'setzen' : (this.anzahl(f) === 3 ? 'springen' : 'ziehen'); }
  wegnehmbar(b, gegner){
    const alle = M_PUNKTE.filter(p => b[p]===gegner);
    const frei = alle.filter(p => !this.inMuehle(b, p, gegner));
    return frei.length ? frei : alle;
  }
  zuege(){
    const f = this.amZug, g = f==='w'?'b':'w', out = [];
    const mitWegnahme = (basis, b2, ziel) => { if (this.inMuehle(b2, ziel, f)) for (const w of this.wegnehmbar(b2, g)) out.push(basis + 'x' + w); else out.push(basis); };
    if (this.phase(f)==='setzen'){
      for (const p of M_PUNKTE) if (!this.b[p]){ const b2 = Object.assign({}, this.b); b2[p]=f; mitWegnahme(p, b2, p); }
    } else {
      const spring = this.phase(f)==='springen';
      for (const von of M_PUNKTE){ if (this.b[von]!==f) continue;
        const ziele = spring ? M_PUNKTE.filter(p=>!this.b[p]) : M_NACHBARN[von].filter(p=>!this.b[p]);
        for (const z of ziele){ const b2 = Object.assign({}, this.b); delete b2[von]; b2[z]=f; mitWegnahme(von+'-'+z, b2, z); }
      }
    }
    return out;
  }
  zieh(z){
    const f = this.amZug; const [bew, weg] = z.split('x');
    if (bew.includes('-')){ const [von, nach] = bew.split('-'); delete this.b[von]; this.b[nach]=f; this.letzt=[von,nach]; }
    else { this.b[bew]=f; this.hand[f]--; this.letzt=[bew]; }
    if (weg){ delete this.b[weg]; this.ohne = 0; } else this.ohne++;
    this.verlauf.push(z); this.wahl=null;
    this.amZug = f==='w'?'b':'w';
    const g = this.amZug;
    if (this.hand[g]===0 && this.anzahl(g) < 3){ this.ende = (f===this.du?'Du gewinnst':KI+' gewinnt') + ' – ' + this.name(g) + ' hat nur noch ' + this.anzahl(g) + ' Steine.'; }
    else if (this.zuege().length===0){ this.ende = (f===this.du?'Du gewinnst':KI+' gewinnt') + ' – ' + this.name(g) + ' ist eingeschlossen.'; }
    else if (this.ohne >= 60) this.ende = 'Remis – lange nichts geschlagen.';
  }
  klick(p){
    const alle = this.zuege(), f = this.amZug;
    if (this.offen){ // Mühle geschlossen: Stein wegnehmen
      const voll = this.offen + 'x' + p; if (alle.includes(voll)){ this.offen=null; this.zieh(voll); return voll; } return null;
    }
    const versuch = basis => { const ohne = alle.includes(basis); const mit = alle.filter(z => z.startsWith(basis+'x'));
      if (ohne){ this.zieh(basis); return basis; } if (mit.length){ this.offen = basis; this.wahl=null; return null; } return undefined; };
    if (this.phase(f)==='setzen'){ if (!this.b[p]){ const r = versuch(p); return r===undefined?null:r; } return null; }
    if (this.wahl){ const r = versuch(this.wahl+'-'+p); if (r!==undefined) return r; }
    if (this.b[p]===f && alle.some(z=>z.startsWith(p+'-'))) this.wahl = p; else this.wahl = null;
    return null;
  }
  text(){
    const s = p => this.b[p] || '·';
    const z = `7  ${s('a7')}-----------${s('d7')}-----------${s('g7')}
   |           |           |
6  |   ${s('b6')}-------${s('d6')}-------${s('f6')}   |
   |   |       |       |   |
5  |   |   ${s('c5')}---${s('d5')}---${s('e5')}   |   |
   |   |   |       |   |   |
4  ${s('a4')}---${s('b4')}---${s('c4')}       ${s('e4')}---${s('f4')}---${s('g4')}
   |   |   |       |   |   |
3  |   |   ${s('c3')}---${s('d3')}---${s('e3')}   |   |
   |   |       |       |   |
2  |   ${s('b2')}-------${s('d2')}-------${s('f2')}   |
   |           |           |
1  ${s('a1')}-----------${s('d1')}-----------${s('g1')}
   a   b   c   d   e   f   g`;
    return z + `\nIn der Hand: Weiß ${this.hand.w}, Schwarz ${this.hand.b}. Phase ${this.name(this.amZug)}: ${this.phase(this.amZug)}.`;
  }
  render(){
    if (!$('#muehle')){
      buehne.innerHTML = '<div id="muehle"><svg viewBox="0 0 700 700"></svg></div>';
      const svg = $('#muehle svg'); const xy = p => [ (p.charCodeAt(0)-97)*100+50, (7-(p.charCodeAt(1)-48))*100+50 ];
      for (const m of M_MUEHLEN){ const [x1,y1]=xy(m[0]), [x2,y2]=xy(m[2]); svg.insertAdjacentHTML('beforeend', `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`); }
      for (const p of M_PUNKTE){ const [x,y]=xy(p); svg.insertAdjacentHTML('beforeend', `<circle class="p" data-p="${p}" cx="${x}" cy="${y}" r="12"/><circle class="s" data-s="${p}" cx="${x}" cy="${y}" r="30" style="display:none"/><text x="${x+22}" y="${y-24}">${p}</text>`); }
      for (const el of svg.querySelectorAll('[data-p],[data-s]')) el.onclick = () => menschKlick(el.dataset.p || el.dataset.s);
    }
    const alle = this.amZug===this.du ? this.zuege() : [];
    for (const p of M_PUNKTE){
      const pt = $(`#muehle [data-p="${p}"]`), st = $(`#muehle [data-s="${p}"]`);
      const v = this.b[p]; st.style.display = v ? '' : 'none'; st.setAttribute('class', 's ' + (v||''));
      pt.setAttribute('class', 'p');
      if (this.offen && alle.includes(this.offen+'x'+p)) st.classList.add('schlag');
      if (this.wahl===p) st.classList.add('wahl');
      if (!v && !this.offen && this.amZug===this.du){ const basis = this.phase(this.amZug)==='setzen' ? p : (this.wahl ? this.wahl+'-'+p : null);
        if (basis && alle.some(z => z===basis || z.startsWith(basis+'x'))) pt.classList.add('ziel'); }
    }
    this.extra = this.offen ? 'Mühle! Nimm einen Stein von ' + KI + ' weg' : '';
  }
}

// ---------------------------------------------------------------- Schach
class Schach {
  constructor(du){
    this.id='schach'; this.titel='Schach'; this.du=du; this.ki=du==='w'?'b':'w'; this.amZug='w'; this.verlauf=[]; this.ende='';
    this.b = {}; const reihe = 'rnbqkbnr';
    for (let c=0;c<8;c++){ this.b[feldName(c,0)] = reihe[c].toUpperCase(); this.b[feldName(c,1)]='P'; this.b[feldName(c,6)]='p'; this.b[feldName(c,7)]=reihe[c]; }
    this.rochade = {K:true,Q:true,k:true,q:true}; this.ep = null; this.halb = 0; this.wahl=null; this.stellungen = {};
  }
  name(f){ return f==='w'?'Weiß':'Schwarz'; }
  farbe(p){ return p ? (p===p.toUpperCase()?'w':'b') : null; }
  cr(f){ return [f.charCodeAt(0)-97, f.charCodeAt(1)-49]; }
  ok(c,r){ return c>=0&&c<8&&r>=0&&r<8; }
  koenig(b, f){ const k = f==='w'?'K':'k'; for (const x in b) if (b[x]===k) return x; return null; }
  angegriffen(b, feld, von){ // wird 'feld' von Farbe 'von' angegriffen?
    const [c,r] = this.cr(feld);
    const gg = (dc,dr,p) => { const cc=c+dc, rr=r+dr; return this.ok(cc,rr) && b[feldName(cc,rr)]===p; };
    const P = von==='w' ? 'P':'p', dr = von==='w' ? -1 : 1;
    if (gg(-1,dr,P) || gg(1,dr,P)) return true;
    const N = von==='w'?'N':'n'; for (const [dc,dr2] of [[1,2],[2,1],[-1,2],[-2,1],[1,-2],[2,-1],[-1,-2],[-2,-1]]) if (gg(dc,dr2,N)) return true;
    const K = von==='w'?'K':'k'; for (let dc=-1;dc<=1;dc++) for (let dr2=-1;dr2<=1;dr2++) if ((dc||dr2) && gg(dc,dr2,K)) return true;
    const lauf = (richt, fig) => { for (const [dc,dr2] of richt){ let cc=c+dc, rr=r+dr2; while (this.ok(cc,rr)){ const p=b[feldName(cc,rr)]; if (p){ if (fig.includes(p)) return true; break; } cc+=dc; rr+=dr2; } } return false; };
    if (lauf([[1,0],[-1,0],[0,1],[0,-1]], von==='w'?['R','Q']:['r','q'])) return true;
    if (lauf([[1,1],[1,-1],[-1,1],[-1,-1]], von==='w'?['B','Q']:['b','q'])) return true;
    return false;
  }
  imSchach(b, f){ const k = this.koenig(b, f); return k ? this.angegriffen(b, k, f==='w'?'b':'w') : false; }
  pseudo(b, f, roch, ep){
    const out = [];
    for (const von in b){ const p = b[von]; if (this.farbe(p)!==f) continue; const [c,r]=this.cr(von); const t = p.toLowerCase();
      const push = (cc,rr,extra) => out.push(von+feldName(cc,rr)+(extra||''));
      if (t==='p'){ const dr = f==='w'?1:-1, start = f==='w'?1:6, letzte = f==='w'?7:0;
        const vor = (cc,rr) => { if (rr===letzte) for (const u of ['q','r','b','n']) push(cc,rr,u); else push(cc,rr); };
        if (this.ok(c,r+dr) && !b[feldName(c,r+dr)]){ vor(c,r+dr); if (r===start && !b[feldName(c,r+2*dr)]) push(c,r+2*dr); }
        for (const dc of [-1,1]){ const cc=c+dc, rr=r+dr; if (!this.ok(cc,rr)) continue; const z = feldName(cc,rr);
          if ((b[z] && this.farbe(b[z])!==f) || z===ep) vor(cc,rr); }
      } else if (t==='n'){ for (const [dc,dr] of [[1,2],[2,1],[-1,2],[-2,1],[1,-2],[2,-1],[-1,-2],[-2,-1]]){ const cc=c+dc, rr=r+dr; if (this.ok(cc,rr) && this.farbe(b[feldName(cc,rr)])!==f) push(cc,rr); } }
      else if (t==='k'){ for (let dc=-1;dc<=1;dc++) for (let dr=-1;dr<=1;dr++){ if (!(dc||dr)) continue; const cc=c+dc, rr=r+dr; if (this.ok(cc,rr) && this.farbe(b[feldName(cc,rr)])!==f) push(cc,rr); }
        const g = f==='w'?'b':'w', reihe = f==='w'?0:7;
        if (r===reihe && c===4 && !this.angegriffen(b, von, g)){
          if (roch[f==='w'?'K':'k'] && !b[feldName(5,reihe)] && !b[feldName(6,reihe)] && b[feldName(7,reihe)]===(f==='w'?'R':'r') && !this.angegriffen(b, feldName(5,reihe), g)) push(6,reihe);
          if (roch[f==='w'?'Q':'q'] && !b[feldName(3,reihe)] && !b[feldName(2,reihe)] && !b[feldName(1,reihe)] && b[feldName(0,reihe)]===(f==='w'?'R':'r') && !this.angegriffen(b, feldName(3,reihe), g)) push(2,reihe);
        }
      } else { const richt = t==='r' ? [[1,0],[-1,0],[0,1],[0,-1]] : t==='b' ? [[1,1],[1,-1],[-1,1],[-1,-1]] : [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]];
        for (const [dc,dr] of richt){ let cc=c+dc, rr=r+dr; while (this.ok(cc,rr)){ const q = b[feldName(cc,rr)]; if (q){ if (this.farbe(q)!==f) push(cc,rr); break; } push(cc,rr); cc+=dc; rr+=dr; } } }
    }
    return out;
  }
  anwenden(b, z, f, roch, ep){
    const b2 = Object.assign({}, b), roch2 = Object.assign({}, roch); const von = z.slice(0,2), nach = z.slice(2,4), u = z[4];
    const p = b2[von]; delete b2[von]; let ep2 = null, schlag = !!b2[nach];
    if (p.toLowerCase()==='p'){
      if (nach===ep){ delete b2[nach[0] + von[1]]; schlag = true; }
      if (Math.abs(+nach[1] - +von[1])===2) ep2 = von[0] + (f==='w'?'3':'6');
    }
    b2[nach] = u ? (f==='w'?u.toUpperCase():u) : p;
    if (p.toLowerCase()==='k'){ roch2[f==='w'?'K':'k']=false; roch2[f==='w'?'Q':'q']=false;
      if (von[0]==='e' && nach[0]==='g'){ const R = f==='w'?'R':'r'; delete b2['h'+von[1]]; b2['f'+von[1]] = R; }
      if (von[0]==='e' && nach[0]==='c'){ const R = f==='w'?'R':'r'; delete b2['a'+von[1]]; b2['d'+von[1]] = R; } }
    for (const [feld, key] of [['h1','K'],['a1','Q'],['h8','k'],['a8','q']]) if (von===feld || nach===feld) roch2[key]=false;
    return [b2, roch2, ep2, schlag || p.toLowerCase()==='p'];
  }
  zuege(){ return this.pseudo(this.b, this.amZug, this.rochade, this.ep).filter(z => { const [b2] = this.anwenden(this.b, z, this.amZug, this.rochade, this.ep); return !this.imSchach(b2, this.amZug); }); }
  zieh(z){
    const [b2, roch2, ep2, reset] = this.anwenden(this.b, z, this.amZug, this.rochade, this.ep);
    this.b=b2; this.rochade=roch2; this.ep=ep2; this.halb = reset ? 0 : this.halb+1; this.verlauf.push(z); this.letzt=[z.slice(0,2), z.slice(2,4)]; this.wahl=null;
    this.amZug = this.amZug==='w'?'b':'w';
    const key = this.text().split('\n').slice(0,8).join('') + this.amZug; this.stellungen[key] = (this.stellungen[key]||0)+1;
    const g = this.amZug==='w'?'b':'w';
    if (this.zuege().length===0){ this.ende = this.imSchach(this.b, this.amZug) ? ('Schachmatt – ' + (g===this.du?'du gewinnst!':KI+' gewinnt!')) : 'Patt – Remis.'; }
    else if (this.halb >= 100) this.ende = 'Remis – 50 Züge ohne Schlag oder Bauernzug.';
    else if (this.stellungen[key] >= 3) this.ende = 'Remis – dreimal dieselbe Stellung.';
  }
  klick(f){
    const alle = this.zuege();
    if (this.wahl){ const kand = alle.filter(z => z.startsWith(this.wahl + f));
      if (kand.length){ let z = kand[0]; if (kand.length > 1){ const u = (prompt('Umwandlung: q (Dame), r (Turm), b (Läufer), n (Springer)', 'q') || 'q').toLowerCase()[0]; z = kand.find(x => x.endsWith(u)) || kand[0]; } this.zieh(z); return z; } }
    if (this.b[f] && this.farbe(this.b[f])===this.amZug && alle.some(z => z.startsWith(f))) this.wahl = f; else this.wahl = null;
    return null;
  }
  text(){
    let s=''; for (let r=7;r>=0;r--){ s += (r+1)+' '; for (let c=0;c<8;c++) s += (this.b[feldName(c,r)]||'.')+' '; s+='\n'; } s += '  a b c d e f g h';
    if (this.imSchach(this.b, this.amZug)) s += '\n' + this.name(this.amZug) + ' steht im Schach!';
    return s;
  }
  render(){
    if (!$('#schach')){ buehne.innerHTML=''; buehne.appendChild(brettGrid('schach', 8, menschKlick, this.du)); }
    const FIG = {K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
    const alle = this.amZug===this.du ? this.zuege() : [];
    for (const el of document.querySelectorAll('#schach .f')){
      const f = el.dataset.f, p = this.b[f];
      el.classList.remove('wahl','ziel','schlag','letzt');
      for (const ch of Array.from(el.children)) if (!ch.classList.contains('koord')) ch.remove();
      if (p){ const s = document.createElement('span'); s.className = 'fig ' + this.farbe(p); s.textContent = FIG[p]; el.appendChild(s); }
      if (this.letzt && this.letzt.includes(f)) el.classList.add('letzt');
      if (this.wahl===f) el.classList.add('wahl');
      if (this.wahl && alle.some(z => z.startsWith(this.wahl+f))){ el.classList.add('ziel'); if (p) el.classList.add('schlag'); }
    }
  }
}

// ---------------------------------------------------------------- Start
(async function(){
  try{ const r = await fetch('/zug', {method:'POST', headers:{'Content-Type':'application/json','X-Token':T}, body: JSON.stringify({spiel:'tictactoe', wer:true})}); const d = await r.json(); if (d.name){ KI = d.name; DU = d.nutzer || 'Du'; } }catch(e){}
  $('#kiname').textContent = '💬 ' + KI;
  menue();
})();
</script>
</body>
</html>
"""
