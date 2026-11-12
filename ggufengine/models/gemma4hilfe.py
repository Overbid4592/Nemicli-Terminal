"""
Gemma-4-Hilfskopf (MTP-Assistent „gemma-4-*-it-assistant“, llama.cpp arch „gemma4-assistant“,
src/models/gemma4-assistant.cpp; transformers Gemma4AssistantForCausalLM): ein kleines Modell schlägt
vom zuletzt gelesenen Token aus mehrere Folgetokens vor, das Hauptmodell prüft sie in einem Durchgang.

* Eingabe: [Embedding des Tokens aus dem Hauptmodell, mal sqrt(E) | Hidden-State des Hauptmodells nach
  der Schluss-Norm an der Stelle davor] -> pre_projection (2E -> e)
* jede Schicht rechnet nur Q; K/V liest sie aus dem Cache der letzten KV-Schicht gleicher Art im
  Hauptmodell (Fenster oder voll), mit deren RoPE. Alle Vorschläge einer Runde stehen auf derselben
  Position, der Hilfskopf schreibt keinen Cache.
* Schicht wie Gemma 4: Q-Norm, Skala 1.0, GELU-tanh-FFN, Nach-Normen, Ausgabe-Skalar
* Schluss-Norm -> Logits über die an die Embedding-Tabelle gebundene Ausgabe (mit Zentroiden nur die
  Tokens der besten Gruppen) und post_projection (e -> E) als Hidden-State des nächsten Vorschlags

Gelesen wird die GGUF-Datei (llama.cpp) oder die safetensors-Datei von Hugging Face – nur Daten.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

import torch
import torch.nn.functional as F

from ..gguf import GGUFError, GGUFFile
from .common import GPU_SPERRE, Linear, WeightLoader, attention, release_pinned_memory, rms_norm

ARCH = "gemma4-assistant"

# GGUF-Name -> Name in der safetensors-Datei von Hugging Face
_HF = {"token_embd.weight": "model.embed_tokens.weight", "output_norm.weight": "model.norm.weight",
       "nextn.pre_projection.weight": "pre_projection.weight",
       "nextn.post_projection.weight": "post_projection.weight",
       "masked_embd_centroids.weight": "masked_embedding.centroids.weight",
       "masked_embd_ordering": "masked_embedding.token_ordering"}
_HF_SCHICHT = {"attn_norm.weight": "input_layernorm.weight", "attn_q.weight": "self_attn.q_proj.weight",
               "attn_q_norm.weight": "self_attn.q_norm.weight", "attn_output.weight": "self_attn.o_proj.weight",
               "post_attention_norm.weight": "post_attention_layernorm.weight",
               "ffn_norm.weight": "pre_feedforward_layernorm.weight", "ffn_gate.weight": "mlp.gate_proj.weight",
               "ffn_up.weight": "mlp.up_proj.weight", "ffn_down.weight": "mlp.down_proj.weight",
               "post_ffw_norm.weight": "post_feedforward_layernorm.weight",
               "layer_output_scale.weight": "layer_scalar"}
_SCHICHT = re.compile(r"blk\.(\d+)\.(.+)")
MAX_SCHICHTEN = 16


def _gelu(x: torch.Tensor) -> torch.Tensor:
    return F.gelu(x, approximate="tanh")


class _StLader:
    """safetensors-Datei mit den Zugriffen, die der Hilfskopf von WeightLoader braucht."""

    def __init__(self, pfad: str, device: torch.device, dtype: torch.dtype):
        from safetensors import safe_open
        self.device, self.dtype = device, dtype
        with safe_open(str(pfad), framework="pt", device="cpu") as f:   # klein: ganz lesen, Datei wieder frei
            self._t = {n: f.get_tensor(n) for n in f.keys()}
        self._namen = set(self._t)
        self.config = {}                    # config.json daneben (Hugging Face), falls vorhanden
        cfg = Path(pfad).with_name("config.json")
        if cfg.is_file() and cfg.stat().st_size < 1 << 20:
            try:
                wert = json.loads(cfg.read_text(encoding="utf-8"))
                self.config = wert if isinstance(wert, dict) else {}
            except (OSError, ValueError):
                pass

    @staticmethod
    def _hf(name: str) -> str:
        if (m := _SCHICHT.fullmatch(name)) and m.group(2) in _HF_SCHICHT:
            return f"model.layers.{m.group(1)}.{_HF_SCHICHT[m.group(2)]}"
        return _HF.get(name, name)

    def has(self, name: str) -> bool:
        return self._hf(name) in self._namen or (name == "token_embd.weight" and "lm_head.weight" in self._namen)

    def get(self, name: str, dtype: Optional[torch.dtype] = None) -> torch.Tensor:
        hf = self._hf(name)
        if hf not in self._namen and name == "token_embd.weight":
            hf = "lm_head.weight"
        if hf not in self._namen:
            raise GGUFError(f"missing tensor {hf!r}")
        t = self._t[hf]
        return t.to(self.device) if not t.is_floating_point() else t.to(self.device, dtype or self.dtype)

    def norm(self, name: str) -> torch.Tensor:
        return self.get(name, torch.float32)

    def linear(self, name: str) -> Linear:
        w = self.get(name)
        if w.dim() != 2:
            raise GGUFError(f"tensor {name!r} is not a matrix")
        return Linear(w)


def ist_hilfskopf(pfad: Path) -> bool:
    """Eine Hilfskopf-Datei: GGUF mit arch gemma4-assistant oder safetensors mit pre_projection."""
    try:
        if pfad.suffix.lower() == ".gguf":
            with GGUFFile(pfad) as gg:
                return gg.architecture == ARCH
        if pfad.suffix.lower() == ".safetensors":
            from safetensors import safe_open
            with safe_open(str(pfad), framework="pt", device="cpu") as f:
                namen = set(f.keys())
            return "pre_projection.weight" in namen and "post_projection.weight" in namen
    except Exception:
        return False
    return False


class _Schicht:
    def __init__(self, ld, i: int, e: int, quelle, eps: float):
        p = f"blk.{i}."
        self.quelle = quelle                       # KV-Schicht des Hauptmodells (Cache, RoPE, Fenster)
        self.eps = eps
        self.attn_norm = ld.norm(p + "attn_norm.weight")
        self.q = ld.linear(p + "attn_q.weight")
        self.q_norm = ld.norm(p + "attn_q_norm.weight")
        self.D = self.q_norm.numel()
        if self.q.n_in != e or self.q.n_out % self.D:
            raise GGUFError(f"assistant layer {i}: attn_q has an unexpected size")
        self.H = self.q.n_out // self.D
        if self.D != quelle.D or self.H % quelle.Hkv or quelle.window == 1:
            raise GGUFError(f"assistant layer {i}: no matching KV layer in the main model")
        self.o = ld.linear(p + "attn_output.weight")
        self.post_attn_norm = ld.norm(p + "post_attention_norm.weight")
        self.ffn_norm = ld.norm(p + "ffn_norm.weight")
        self.gate = ld.linear(p + "ffn_gate.weight")
        self.up = ld.linear(p + "ffn_up.weight")
        self.down = ld.linear(p + "ffn_down.weight")
        self.post_ffn_norm = ld.norm(p + "post_ffw_norm.weight")
        self.out_scale = ld.norm(p + "layer_output_scale.weight").reshape(-1)
        if (self.o.n_out != e or self.o.n_in != self.H * self.D or self.down.n_out != e
                or self.gate.n_in != e or self.out_scale.numel() != 1):
            raise GGUFError(f"assistant layer {i}: inconsistent tensor sizes")

    def __call__(self, h: torch.Tensor, pos_idx: torch.Tensor, maske_pos: torch.Tensor, kv_len: int) -> torch.Tensor:
        q_l = self.quelle
        x = rms_norm(h, self.attn_norm, self.eps)
        q = self.q(x).float().reshape(1, self.H, self.D)
        q = q_l.rope(rms_norm(q, self.q_norm, self.eps), pos_idx)
        cache = q_l.cache
        # nur die Positionen vor pos_idx (die eigene ist noch nicht gelesen): Maske ab pos_idx - 1; im
        # Fenster gilt pos_idx - k < window wie für das Hauptmodell an pos_idx
        fenster = q_l.window - 1 if q_l.window > 0 else 0
        o = cache.o_zurueck(attention(cache.q_drehen(q), *cache.kv(kv_len), maske_pos, kv_len, 1.0,
                                      fenster, key_pos=cache.pos))
        h = h + rms_norm(self.o(o.reshape(1, -1)), self.post_attn_norm, self.eps)
        f = rms_norm(h, self.ffn_norm, self.eps)
        f = self.down(_gelu(self.gate(f)) * self.up(f))
        h = h + rms_norm(f, self.post_ffn_norm, self.eps)
        return h * self.out_scale


class Hilfskopf:
    """Der Hilfskopf zu einem geladenen Gemma4Model (`haupt`)."""

    def __init__(self, pfad: str, haupt):
        dev, dtype = haupt.device, haupt.dtype
        if str(pfad).lower().endswith(".gguf"):
            self._gg = GGUFFile(pfad)
            if self._gg.architecture != ARCH:
                raise GGUFError(f"not a Gemma 4 assistant ({self._gg.architecture})")
            ld = WeightLoader(self._gg, dev, dtype)
        else:
            self._gg = None
            ld = _StLader(pfad, dev, dtype)
        E = haupt.hp["n_embd"]
        eps = haupt.hp["eps"]
        self.pre = ld.linear("nextn.pre_projection.weight")
        self.e = self.pre.n_out
        self.post = ld.linear("nextn.post_projection.weight")
        if self.pre.n_in != 2 * E or self.post.n_in != self.e or self.post.n_out != E:
            raise GGUFError("assistant does not fit the main model (hidden size)")
        n = 0
        while ld.has(f"blk.{n}.attn_q.weight"):
            n += 1
        if not 0 < n <= MAX_SCHICHTEN:
            raise GGUFError("assistant: implausible layer count")
        letzte = {}                                 # Fenster/voll -> letzte KV-Schicht dieser Art
        for l in haupt.layers:
            if l.has_kv:
                letzte[l.is_swa] = l
        quellen = {l.D: l for l in letzte.values()}   # die Art einer Hilfsschicht folgt aus ihrer Kopfbreite
        if len(quellen) != len(letzte):
            raise GGUFError("assistant: window and full layers of the main model have the same head size")
        self.schichten: List[_Schicht] = []
        for i in range(n):
            D = ld.norm(f"blk.{i}.attn_q_norm.weight").numel()
            if D not in quellen:
                raise GGUFError(f"assistant layer {i}: no KV layer with head size {D} in the main model")
            self.schichten.append(_Schicht(ld, i, self.e, quellen[D], eps))
        self.eps = eps
        self.out_norm = ld.norm("output_norm.weight")
        self.tabelle = ld.get("token_embd.weight")             # (V, e), zugleich der Ausgabekopf
        if self.tabelle.shape != (haupt.n_vocab, self.e):
            raise GGUFError("assistant vocabulary does not match the main model")
        self.zentren = self.ordnung = None
        if isinstance(ld, _StLader) and ld.has("masked_embd_centroids.weight") and ld.has("masked_embd_ordering"):
            # Zentroiden-Kopf (die GGUF-Umwandlung von llama.cpp lässt ihn weg und rechnet den vollen Kopf)
            zentren = ld.get("masked_embd_centroids.weight")
            ordnung = ld.get("masked_embd_ordering").long().reshape(-1)
            V, C = haupt.n_vocab, zentren.shape[0]
            if (zentren.dim() == 2 and zentren.shape[1] == self.e and 0 < C <= V and V % C == 0
                    and ordnung.numel() == V
                    and torch.equal(ordnung.sort().values, torch.arange(V, device=ordnung.device))):
                self.zentren, self.ordnung = Linear(zentren), ordnung.view(C, V // C)
        k = getattr(ld, "config", {}).get("centroid_intermediate_top_k", 32)
        self.top_k = k if isinstance(k, int) and not isinstance(k, bool) and k > 0 else 32
        self.haupt_embed, self.embd_scale = haupt.embed, haupt.embd_scale
        del ld                                      # its pinned read buffers become free
        if dev.type == "cuda":
            with GPU_SPERRE:
                torch.cuda.synchronize(dev)
                release_pinned_memory()

    def schritt(self, tok: torch.Tensor, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> tuple:
        """tok (1,), h (1, E): Hidden-State davor -> (Logits der gerechneten Tokens, ihre Ids oder None,
        neuer Hidden-State (1, E))."""
        x = (self.haupt_embed(tok) * self.embd_scale).float()
        x = self.pre(torch.cat([x, h.float()], dim=-1)).float()
        maske_pos = pos_idx - 1
        for s in self.schichten:
            x = s(x, pos_idx, maske_pos, kv_len).float()
        x = rms_norm(x, self.out_norm, self.eps)
        h_neu = self.post(x).float()
        xd = x.to(self.tabelle.dtype)
        if self.zentren is None:
            return F.linear(xd, self.tabelle)[0].float(), None, h_neu
        k = min(self.top_k, self.ordnung.shape[0])
        gruppen = self.zentren(x)[0].float().topk(k).indices                  # (k,)
        ids = self.ordnung.index_select(0, gruppen).reshape(-1)                # (k * je Gruppe,)
        return (self.tabelle.index_select(0, ids) @ xd[0]).float(), ids, h_neu

    def logits(self, tok: torch.Tensor, h: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> tuple:
        """Volle Logits (V,) (nicht gerechnete Tokens -inf) und neuer Hidden-State."""
        werte, ids, h_neu = self.schritt(tok, h, pos_idx, kv_len)
        if ids is None:
            return werte, h_neu
        voll = torch.full((self.tabelle.shape[0],), float("-inf"), device=werte.device)
        return voll.index_copy(0, ids, werte), h_neu

    def schliessen(self) -> None:
        """Gibt die GGUF-Datei frei. Auf der CPU sind die Tensoren Sichten auf die Datei: erst sie loslassen."""
        gg = self._gg
        self.__dict__.clear()
        self._gg = None
        if gg is not None:
            import gc
            gc.collect()
            try:
                gg.close()
            except BufferError:               # noch eine Sicht in Benutzung: das Abbild schließt mit ihr
                pass


class HilfeKette:
    """`n` Vorschläge hintereinander in einem Lauf (für einen CUDA-Graphen): liest den gemerkten Hidden-State
    an pos - 1 aus dem Hauptmodell, jeder Vorschlag ist der Eingang des nächsten. Gibt (n + 1, 2) float32:
    Zeile 0 = (1, 0), wenn das Hauptmodell pos - 1 zuletzt gerechnet hat, sonst (0, 0); danach je
    (Token-Id, seine Wahrscheinlichkeit). So braucht eine Runde nur ein Lesen auf dem Host."""

    def __init__(self, kopf: Hilfskopf, model, n: int):
        self.kopf, self.model, self.n = kopf, model, n
        self.device = model.device
        self.alle_logits = False

    def snapshot_state(self):
        return None

    def restore_state(self, snap):
        pass

    def _run(self, tok: torch.Tensor, pos_idx: torch.Tensor, kv_len: int) -> torch.Tensor:
        m = self.model
        von, bis = m._h_bereich[0:1], m._h_bereich[1:2]
        gueltig = ((von <= pos_idx - 1) & (pos_idx - 1 < bis)).float()
        zeile = (pos_idx - 1 - von).clamp(0, m._h_zeilen.shape[0] - 1)
        h = m._h_zeilen.index_select(0, zeile)
        raus = [torch.cat([gueltig, torch.zeros_like(gueltig)])]
        for _ in range(self.n):
            werte, ids, h = self.kopf.schritt(tok, h, pos_idx, kv_len)
            p, wo = torch.softmax(werte, dim=-1).max(dim=-1)
            tid = wo if ids is None else ids.index_select(0, wo.reshape(1))[0]   # ohne Host-Lesen (Graph)
            raus.append(torch.stack([tid.float(), p]))
            tok = tid.reshape(1)
        return torch.stack(raus)
