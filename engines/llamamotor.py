"""
llamamotor.py - llama.cpp (llamareserve) hinter der Schnittstelle des eigenen Motors.

Die Modelldatei liest zuerst der gehärtete GGUF-Leser von ggufengine (Türsteher), Tokenizer und
Chat-Format kommen ebenfalls von dort. llama-server bekommt nur fertige Token-Folgen über
/completion: kein Jinja, und Sondermarken im Nutzertext bleiben Text wie im eigenen Motor.
Präfix-Cache: cache_prompt im Server. Auslagern beim Bildermalen beendet den Server, danach
startet er neu (das Gespräch wird dann einmal neu gelesen).

Bilder (mmproj im Modell-Ordner): llama-server nimmt sie nur mit einem Text-Prompt. Der entsteht aus den
eigenen Token (Platzhalter MEDIEN je Bild); vorher wird geprüft, dass jede Sondermarke im Text ein echtes
Sondertoken ist und jeder Platzhalter ein Bild – sonst wird die Anfrage abgelehnt.

Nicht über llama.cpp: Experten-Lager, TensorRT, LoRA, Stand auf der Platte.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import llamareserve as R

LESE_ZEIT_S = 3600.0                   # eine Antwort darf so lange dauern
MEDIEN = "<__media__>"                 # Platzhalter für ein Bild im Text-Prompt (mtmd_default_marker)


class _Bild:
    """Ein Bild für llama-server: PNG als base64 – den Bildteil rechnet der Server selbst."""
    __slots__ = ("b64",)

    def __init__(self, b64: str):
        self.b64 = b64
LADE_PHASEN = {"file": "Datei", "tokenizer": "Tokenizer", "llamacpp": f"llama.cpp {R.BUILD}"}


def _sampler_werte(cfg) -> dict:
    """SamplerConfig -> /completion. DRY bleibt aus: llama.cpp wendet es auf den ganzen Kontext an,
    nicht nur auf die eigenen Antworten – Werkzeugnamen und Zitate aus dem Prompt würden bestraft."""
    if cfg is None:
        return {"dry_multiplier": 0.0}
    werte = {"temperature": float(cfg.temperature), "top_k": int(cfg.top_k), "top_p": float(cfg.top_p),
             "min_p": float(cfg.min_p), "repeat_penalty": float(cfg.repeat_penalty),
             "repeat_last_n": int(cfg.repeat_last_n), "presence_penalty": float(cfg.presence_penalty),
             "dry_multiplier": 0.0}
    if cfg.seed is not None:
        werte["seed"] = int(cfg.seed)
    return werte


class LlamaCppEngine:
    """Die Teile der ggufengine.Engine, die gguflokal braucht – gerechnet von llama-server."""

    fremd = True
    motor_name = f"llama.cpp {R.BUILD}"

    def __init__(self, path: str, n_ctx: int = 0, bildteil: str | None = None):
        from ggufengine.chat import ChatFormatter, detect_format
        from ggufengine.gguf import GGUFFile
        from ggufengine.tokenizer import Tokenizer
        self.load_phases: dict = {}
        t = time.time()
        self.gg = GGUFFile(path)                     # Türsteher: Kopf, Tensor-Grenzen, Metadaten-Budget
        self.load_phases["file"] = time.time() - t
        t = time.time()
        self.tokenizer = Tokenizer(self.gg)
        self.load_phases["tokenizer"] = time.time() - t
        vorlage = self.gg.get("tokenizer.chat_template")
        self.chat_format = ChatFormatter(self.tokenizer, detect_format(vorlage, self.gg.architecture),
                                         thinking=False, template=vorlage)
        if bildteil:
            GGUFFile(bildteil).close()               # auch der Bildteil geht durch den Türsteher
        self.server = R.Server(Path(path), n_ctx, Path(bildteil) if bildteil else None)
        self.load_phases["llamacpp"] = self.server.starten()
        self.n_ctx = self.server.n_ctx or n_ctx
        self.vision = "llama.cpp" if bildteil else None
        self._marken_re = None
        self.dtype = "llamacpp"
        self.device = SimpleNamespace(type="llamacpp")
        self.kv_im_vram = None
        self.kv_bits_wahl = 16
        self.dry_frei: tuple = ()
        self.denk_budget = None
        self.checkpoint_new = False
        self.last_cached = 0
        self.last_ids: list[int] = []
        self.last_stats: dict = {}
        self._keys: list[int] = []
        self._pos = 0
        self._aus = False
        self._geleert = False

    # -- Zustand ----------------------------------------------------------------------
    @property
    def offloaded(self) -> bool:
        return self._aus

    @property
    def geleert(self) -> bool:
        return self._geleert

    def reset(self) -> None:
        self._keys, self._pos = [], 0

    def offload(self) -> float:
        """Für ein Bild: Server beenden (auch wenn ein anderer NemiCLI-Prozess ihn mitbenutzt – der wartet,
        bis das Bild fertig ist), die Grafikkarte ist dann frei."""
        t = time.time()
        self.server.stoppen(fuer_bild=True)
        self._aus = True
        self.reset()
        return time.time() - t

    def restore(self) -> float:
        """Bild fertig: die Pause endet, gestartet wird erst bei der nächsten Anfrage. So startet der Server
        zwischen zwei Bildern nicht umsonst (Ladezeit, neuer Prozess für die Wache)."""
        if not self._aus:
            return 0.0
        self._aus = False
        self.server.pause_ende()
        return 0.0

    def leeren(self) -> None:
        if self._geleert:
            return
        self._geleert = True
        self.server.stoppen()
        self.reset()

    def _sicher_laufend(self) -> float:
        if self._geleert:
            raise RuntimeError("llama.cpp-Motor wurde entladen")
        if self.server.laeuft():
            self.server.benutzt()
            return 0.0
        self.reset()                                 # neuer Prozess: sein Cache ist leer
        sekunden = self.server.starten()
        self.n_ctx = self.server.n_ctx or self.n_ctx
        return sekunden

    # nichts auf der Platte: der Server hält den Präfix selbst
    def load_prefix(self, *a, **k) -> bool:
        return False

    def save_prefix(self, *a, **k) -> bool:
        return False

    def enable_vision(self, *a, **k) -> None:
        raise RuntimeError("Den Bildteil gibt die Reserve beim Start an llama-server")

    def encode_image(self, image, tokens=None) -> _Bild:
        if not self.vision:
            raise RuntimeError("Dieses Modell sieht über llama.cpp keine Bilder (keine mmproj-Datei im Ordner)")
        import base64
        import io
        puffer = io.BytesIO()
        image.convert("RGB").save(puffer, "PNG")
        return _Bild(base64.b64encode(puffer.getvalue()).decode("ascii"))

    def _marken(self):
        """Alle Sondermarken und der Bild-Platzhalter als ein Muster (einmal je Modell)."""
        if self._marken_re is None:
            marken = sorted({m for m in self.tokenizer.special if m} | {MEDIEN}, key=len, reverse=True)
            self._marken_re = re.compile("|".join(map(re.escape, marken)))
        return self._marken_re

    def _bild_prompt(self, messages) -> tuple[list[int], dict]:
        """(eigene Token, Prompt für llama-server) für Nachrichten mit Bildern. Wirft, wenn im Text
        etwas stünde, das der Server als Sondermarke oder Bild-Platzhalter lesen würde."""
        from dataclasses import replace
        bilder, neu = [], []
        for m in messages:
            imgs = getattr(m, "images", None) or []
            if imgs:
                if not all(isinstance(b, _Bild) for b in imgs):
                    raise RuntimeError("Bild nicht für llama.cpp vorbereitet")
                bilder += [b.b64 for b in imgs]
                platz = MEDIEN * len(imgs) + "\n"
                teile = [(platz, False)] + list(m.teile) if getattr(m, "teile", None) else None
                m = replace(m, images=None, content=platz + (m.content or ""), teile=teile)
            neu.append(m)
        ids = self.chat_format.encode(neu, add_generation_prompt=True)
        tok = self.tokenizer
        rumpf = ids[1:] if tok.add_bos and tok.bos_id is not None and ids[:1] == [tok.bos_id] else ids
        sonder = set(tok.special.values())
        lauf: list[int] = []
        platzhalter = 0
        for t in rumpf + [None]:                     # jede Folge normaler Token für sich prüfen
            if t is not None and t not in sonder:
                lauf.append(t)
                continue
            if lauf:
                stueck = tok.decode(lauf, skip_special=False)
                treffer = self._marken().findall(stueck)
                platzhalter += treffer.count(MEDIEN)
                if any(x != MEDIEN for x in treffer):
                    raise RuntimeError("Bild über llama.cpp abgelehnt: im Text steht eine Steuermarke des Modells")
                lauf = []
        if platzhalter != len(bilder):
            raise RuntimeError("Bild über llama.cpp abgelehnt: der Bild-Platzhalter steht im Text")
        return ids, {"prompt_string": tok.decode(rumpf, skip_special=False), "multimodal_data": bilder}

    # -- Anfragen ---------------------------------------------------------------------
    def _gemeinsam(self, ids: list[int]) -> int:
        n = 0
        for a, b in zip(self._keys, ids):
            if a != b:
                break
            n += 1
        return n

    def _ids_pruefen(self, tokens) -> list[int]:
        if not isinstance(tokens, list):
            raise RuntimeError("llama-server lieferte keine Token-Liste")
        for t in tokens:
            if not isinstance(t, int) or isinstance(t, bool) or not 0 <= t < self.tokenizer.n_vocab:
                raise RuntimeError(f"llama-server lieferte ein ungültiges Token: {t!r}")
        return tokens

    def _anfrage(self, ids: list[int], n: int, werte: dict, extra: dict | None = None):
        """Strom der erzeugten Token-IDs für den Prompt `ids` (höchstens n). Am Ende steht in
        self._timings, was der Server gemessen hat."""
        import httpx
        self._timings = {}
        koerper = {"prompt": ids if isinstance(ids, dict) else list(ids), "n_predict": int(n), "stream": True, "cache_prompt": True,
                   "return_tokens": True, **werte, **(extra or {})}
        zeit = httpx.Timeout(LESE_ZEIT_S, connect=10.0)
        with httpx.Client(timeout=zeit, trust_env=False) as c, contextlib.ExitStack() as stapel:
            try:
                r = stapel.enter_context(c.stream("POST", self.server.basis + "/completion", json=koerper,
                                                  headers=self.server.kopf))
            except httpx.ConnectError:
                # geteilter Server inzwischen weg (der andere Prozess malt oder hat aufgehört): einmal neu verbinden
                self._sicher_laufend()
                r = stapel.enter_context(c.stream("POST", self.server.basis + "/completion", json=koerper,
                                                  headers=self.server.kopf))
            if r.status_code != 200:
                r.read()
                raise RuntimeError(f"llama-server: HTTP {r.status_code} {r.text[:300]}")
            for zeile in r.iter_lines():
                if not zeile.startswith("data:"):
                    continue
                daten = json.loads(zeile[5:].strip() or "{}")
                if "error" in daten:
                    raise RuntimeError(f"llama-server: {daten['error']}")
                for t in self._ids_pruefen(daten.get("tokens") or []):
                    yield t, daten
                if daten.get("stop"):
                    self._timings = daten.get("timings") or {}
                    return

    def _strom(self, ids: list[int], max_tokens: int, sampler=None, denk_budget=None, prompt=None):
        """Token-IDs wie Engine.generate_tokens: Stopp-Token, Kontextgrenze, Denk-Budget
        (nach `grenze` Denk-Token wird der Schluss eingefügt und weitergeschrieben)."""
        werte = _sampler_werte(sampler)
        stop = set(self.tokenizer.stop_ids)
        if len(ids) + 1 > self.n_ctx:
            raise RuntimeError("prompt does not fit into the context window")
        if denk_budget:
            grenze, auf, zu, drin = denk_budget
            gedacht, zuletzt = 0, []
        erzeugt: list[int] = []
        t0 = time.time()
        self._erstes = None
        while True:
            rest = min(max_tokens - len(erzeugt), self.n_ctx - len(ids) - len(erzeugt) - 1)
            if rest <= 0:
                break
            budget_voll = False
            for tid, _ in self._anfrage(prompt if prompt is not None else ids + erzeugt, rest, werte):
                if self._erstes is None:
                    self._erstes = time.time() - t0
                if tid in stop:
                    return erzeugt
                erzeugt.append(tid)
                self._keys = ids + erzeugt
                yield tid
                if denk_budget:
                    zuletzt = (zuletzt + [tid])[-len(auf):]
                    if not drin and zuletzt == auf:
                        drin, gedacht = True, 0
                    elif drin and tid == zu[0]:
                        drin = False
                    elif drin:
                        gedacht += 1
                        if gedacht >= grenze:
                            budget_voll = True
                            break
            if not budget_voll:
                break
            for t in zu:                             # Denken schließen, dann geht die Antwort weiter
                erzeugt.append(t)
                yield t
            denk_budget = None
        return erzeugt

    def chat(self, messages, max_tokens: int = 512, sampler=None, **kw):
        """Wie Engine.chat: Textstücke; danach stehen die erzeugten IDs in last_ids."""
        from ggufengine.tokenizer import StreamDecoder
        bild = any(getattr(m, "images", None) for m in messages)
        if bild and not self.vision:
            raise RuntimeError("Dieses Modell sieht über llama.cpp keine Bilder (keine mmproj-Datei im Ordner)")
        self._sicher_laufend()
        if bild:
            ids, prompt = self._bild_prompt(messages)
        else:
            ids, prompt = self.chat_format.encode(messages, add_generation_prompt=True), None
        self.last_cached = self._gemeinsam(ids)
        budget = None
        marken = self.chat_format.denk_marken() if self.chat_format.thinking else None
        if self.denk_budget and marken and prompt is None:   # mit Bild ein Durchgang: kein Weiterschreiben
            budget = (self.denk_budget, marken[0], marken[1], self.chat_format.opens_thinking)
        dec = StreamDecoder(self.tokenizer)
        self.last_ids = []
        self._keys = list(ids)
        t0 = time.time()
        try:
            for tid in self._strom(ids, max_tokens, sampler, budget, prompt):
                self.last_ids.append(tid)
                if s := dec.push(tid):
                    yield s
            if tail := dec.flush():
                yield tail
        finally:
            self._pos = len(ids) + len(self.last_ids)
            # mit Bild zerlegt der Server den Text selbst: die eigenen Token sagen dann nichts über seinen Cache
            self._keys = [] if prompt is not None else self._keys[:self._pos]
            zeiten = getattr(self, "_timings", {}) or {}
            if "cache_n" in zeiten:
                self.last_cached = int(zeiten["cache_n"])
            gesamt = time.time() - t0
            self.last_stats = dict(prompt_tokens=len(ids), generated=len(self.last_ids), seconds=gesamt,
                                   prefill_seconds=float(zeiten.get("prompt_ms", 0)) / 1000,
                                   decode_seconds=max(float(zeiten.get("predicted_ms", 0)) / 1000, 1e-9),
                                   vorgeschlagen=0, angenommen=0)

    def vorlesen(self, messages, abbruch=None) -> int:
        """Liest `messages` in den Cache des Servers (n_predict 0), ohne zu antworten."""
        if any(getattr(m, "images", None) for m in messages):
            return 0                                 # mit Bild liest erst die nächste Antwort ein
        ids = self.chat_format.encode(messages, add_generation_prompt=False)
        if not ids or (abbruch is not None and abbruch()) or self._aus:
            return 0
        self._sicher_laufend()
        vorher = self._gemeinsam(ids)
        for _ in self._anfrage(ids, 0, {}):
            pass
        self._keys, self._pos = list(ids), len(ids)
        return len(ids) - vorher

    # -- Vergleich mit dem eigenen Motor -----------------------------------------------
    def gierig(self, ids: list[int], n: int, n_probs: int = 5) -> dict:
        """Greedy-Antwort auf `ids` ohne Präfix-Cache: Token, die besten Kandidaten je Schritt
        (Wahrscheinlichkeiten nach llama.cpp) und die Zeiten des Servers."""
        import httpx
        self._sicher_laufend()
        koerper = {"prompt": list(ids), "n_predict": int(n), "stream": False, "cache_prompt": False,
                   "return_tokens": True, "n_probs": int(n_probs), "temperature": 0.0, "top_k": 1,
                   "dry_multiplier": 0.0, "repeat_penalty": 1.0}
        with httpx.Client(timeout=httpx.Timeout(LESE_ZEIT_S, connect=10.0), trust_env=False) as c:
            r = c.post(self.server.basis + "/completion", json=koerper, headers=self.server.kopf)
        if r.status_code != 200:
            raise RuntimeError(f"llama-server: HTTP {r.status_code} {r.text[:300]}")
        daten = r.json()
        tokens = self._ids_pruefen(daten.get("tokens") or [])
        stop = set(self.tokenizer.stop_ids)
        if (ende := next((i for i, t in enumerate(tokens) if t in stop), None)) is not None:
            tokens = tokens[:ende]
        kandidaten = [[(int(x.get("id", -1)), float(x.get("logprob", 0.0))) for x in (p.get("top_logprobs") or [])]
                      for p in (daten.get("completion_probabilities") or [])]
        self.reset()
        return {"tokens": tokens, "kandidaten": kandidaten[:len(tokens)], "timings": dict(daten.get("timings") or {})}
