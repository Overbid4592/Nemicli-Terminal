"""High-Level-API: GGUF-Modell laden und Text erzeugen."""
from __future__ import annotations

import gc
import itertools
import sys
import time
from typing import Callable, Iterator, List, Optional

import torch

from . import debuglog as dbg
from . import hostmem, schichten
from .chat import ChatFormatter, Message, detect_format
from .gguf import GGUFFile
from .models import build_model
from .models.common import (GPU_SPERRE, PREFILL_CHUNK, ZeilenMerken, graphen_aktiv, release_pinned_memory,
                            schliesst_bei_fehler)
from .qlinear import UNPACK_FROM, caches_leeren
from .sampling import Sampler, SamplerConfig
from .spekulativ import MtpEntwurf, NGramEntwurf, Nutzen
from .tokenizer import StreamDecoder, Tokenizer

RELEASE_FROM = UNPACK_FROM   # Einlesevorgänge dieser Länge belegen Scratch, den man danach freigeben sollte
# Einlesen: Kopien wachsen mit jedem Stück (vervielfachte K/V-Köpfe), der Allocator kann die kleineren
# freien Blöcke nicht wiederverwenden. Wächst der leere Vorrat über diese Grenze, geht er zurück an den Treiber.
VORRAT_GRENZE = 1 << 30
SPEKULATIV = 8               # vorgeschlagene Tokens je Prüfung (Prompt-Lookup)
VORSCHLAG_STUFEN = (8, 4, 3, 2, 1)  # ein Vorschlag wird auf eine dieser Längen gekürzt
HOSTMEM_AB = 1 << 20          # Offload: Tensoren ab dieser Größe kommen in passgenaue pinned Blöcke
LAGER_TAKT = 32              # MoE-Lager: VRAM-Slots alle so viele Antwort-Tokens neu sortieren (0: aus)

_BILD_NR = itertools.count(1)


def _frei_reserviert(device: torch.device) -> int:
    """Reservierter, aber nicht belegter Grafikspeicher des Allocators (Bytes)."""
    return torch.cuda.memory_reserved(device) - torch.cuda.memory_allocated(device)


def _bild_nicht_teilen(keys: list, anfang: int, ende: int) -> int:
    """Ende eines Lese-Stücks so vorziehen, dass kein Bild (Schlüssel ("img", nr, zeile))
    über die Grenze läuft."""
    if ende >= len(keys):
        return ende
    a, b = keys[ende - 1], keys[ende]
    if not (isinstance(a, tuple) and isinstance(b, tuple) and a[1] == b[1]):
        return ende
    j = ende - 1
    while j > anfang and isinstance(keys[j - 1], tuple) and keys[j - 1][1] == a[1]:
        j -= 1
    if j == anfang:
        raise ValueError("image has more tokens than one forward call can read")
    return j


def bild_nr(img) -> int:
    """Feste Nummer eines Bild-Embeddings für den Präfix-Cache. `id()` taugt nicht:
    Python vergibt die Kennung eines freigegebenen Objekts sofort neu, ein neues
    Bild sähe dann aus wie das alte an derselben Stelle."""
    nr = getattr(img, "bild_nr", None)
    if nr is None:
        nr = next(_BILD_NR)
        img.bild_nr = nr
    return nr


# Objekte dieser Pakete enthalten Gewichte: auch die LoRA-Hüllen (loramotor.adapter.LoraSchicht),
# die eingeklinkt an Stelle der Original-Schichten stehen – sonst blieben sie beim Auslagern im VRAM.
_EIGENE_MODULE = (__package__, "loramotor")

_DTYPES = {"bf16": torch.bfloat16, "bfloat16": torch.bfloat16, "fp16": torch.float16,
           "float16": torch.float16, "fp32": torch.float32, "float32": torch.float32}


def _move_tensors(root, select, convert, ansichten: bool = False) -> set:
    """Ersetzt jeden von `root` erreichbaren Tensor (Attribute, __slots__, Listen, Tupel,
    Dicts von Engine-Objekten), für den `select(t)` gilt, durch `convert(t)`.  Ein zweifach
    referenzierter Tensor wird einmal umgewandelt und bleibt geteilt; mit `ansichten` (Verschieben zwischen
    Geräten) bleiben auch Tensoren, die Views auf einen Storage sind, Views auf einen Storage.  Gibt die ids
    der neuen Tensoren zurück."""
    memo: dict = {}
    seen: set = set()
    speicher: dict = {}            # Speicher -> verschobener Speicher: Ansichten bleiben Ansichten

    def neu(v: torch.Tensor) -> torch.Tensor:
        """Den ganzen Speicher einmal verschieben, `v` als Ansicht darauf neu anlegen (z. B. teilen sich
        Einbettung und Ausgabe-Kopf denselben Speicher – getrennt verschoben wären es zwei Kopien)."""
        schluessel = (v.device, v.untyped_storage().data_ptr())
        if schluessel not in speicher:
            ganz = torch.empty(0, dtype=torch.uint8, device=v.device).set_(v.untyped_storage())
            speicher[schluessel] = convert(ganz)
        ziel = speicher[schluessel]
        return torch.empty(0, dtype=v.dtype, device=ziel.device).set_(
            ziel.untyped_storage(), v.storage_offset(), v.size(), v.stride())

    def conv(v):
        if isinstance(v, torch.Tensor):
            if not select(v):
                return v
            if id(v) not in memo:
                memo[id(v)] = neu(v) if ansichten else convert(v)
            return memo[id(v)]
        if isinstance(v, list):
            v[:] = [conv(x) for x in v]
            return v
        if isinstance(v, tuple):
            new = tuple(conv(x) for x in v)
            return v if all(a is b for a, b in zip(new, v)) else new
        if isinstance(v, dict):
            for k in list(v):
                v[k] = conv(v[k])
            return v
        if type(v).__module__.startswith(_EIGENE_MODULE) and id(v) not in seen:
            walk(v)
        return v

    def walk(obj):
        seen.add(id(obj))
        names = list(getattr(obj, "__dict__", {}))
        for cls in type(obj).__mro__:
            names += [n for n in getattr(cls, "__slots__", ()) if n not in names]
        for name in names:
            if hasattr(obj, name):
                old = getattr(obj, name)
                new = conv(old)
                if new is not old:                  # eingefrorene Objekte werden nur gelesen
                    setattr(obj, name, new)

    walk(root)
    return {id(t) for t in memo.values()}


def _umziehen(wurzeln, select, convert) -> set:
    """Wie `_move_tensors` mit Ansichten über mehrere Wurzeln, aber ganz oder gar nicht: erst wird jeder
    betroffene Speicher kopiert (`convert` auf den ganzen Speicher als uint8), dann alles eingesetzt.
    Scheitert eine Kopie (Speicher voll), bleibt jeder Tensor, wo er war, und der Fehler geht weiter."""
    kopien: dict = {}

    def kopieren(t: torch.Tensor) -> torch.Tensor:
        schluessel = (t.device, t.untyped_storage().data_ptr())
        if schluessel not in kopien:
            ganz = torch.empty(0, dtype=torch.uint8, device=t.device).set_(t.untyped_storage())
            kopien[schluessel] = convert(ganz)
        return t                                        # noch nichts ersetzen

    try:
        for wurzel in wurzeln:
            _move_tensors(wurzel, select, kopieren)
    except BaseException:
        try:
            torch.cuda.synchronize()                    # laufende Kopien abwarten, bevor ihr Ziel frei wird
        except Exception:
            pass
        kopien.clear()
        raise

    def einsetzen(ganz: torch.Tensor) -> torch.Tensor:
        return kopien.get((ganz.device, ganz.untyped_storage().data_ptr()), ganz)

    neu: set = set()
    for wurzel in wurzeln:
        neu |= _move_tensors(wurzel, select, einsetzen, ansichten=True)
    return neu


def _haelt_tensor(v, tiefe: int = 8) -> bool:
    """Enthält `v` (ein Tensor oder eine Liste/ein Tupel/ein Dict einige Ebenen tief) einen Tensor?"""
    if isinstance(v, torch.Tensor):
        return True
    if tiefe <= 0:
        return False
    if isinstance(v, (list, tuple)):
        return any(_haelt_tensor(x, tiefe - 1) for x in v)
    if isinstance(v, dict):
        return any(_haelt_tensor(x, tiefe - 1) for x in v.values())
    return False


class _Pruefpunkte:
    """Die Prüfpunkte des Motors (nach dem Systemteil, vor der Antwort) – sie hängen nicht am Modell und
    werden beim Auslagern über dieses Objekt mitgenommen."""

    def __init__(self, eng):
        self.system = getattr(eng, "_checkpoint", None)
        self.antwort = getattr(eng, "_answer_checkpoint", None)

    def zurueck(self, eng) -> None:
        eng._checkpoint, eng._answer_checkpoint = self.system, self.antwort


def _leere_statistik() -> dict:
    """`last_stats` eines Aufrufs, der nichts erzeugt hat."""
    return dict(prompt_tokens=0, generated=0, seconds=0.0, prefill_seconds=0.0, decode_seconds=1e-9,
                vorgeschlagen=0, angenommen=0)


def _dry_frei(erzeugt: bytes, marken: tuple) -> bool:
    """Steht das Ende der Antwort in einem Code-Block oder einem offenen Werkzeug-Aufruf (DRY dort aus)?"""
    if erzeugt.count(b"```") % 2 == 1:
        return True
    for auf, zu in marken:
        a = erzeugt.rfind(auf)
        if a >= 0 and (not zu or erzeugt.rfind(zu) < a):
            return True
    return False


def _gleiche_form(geladen, soll, n_ctx: int) -> bool:
    """Hat ein gespeicherter Modellzustand genau die Struktur des aktuellen (Verschachtelung, Shapes,
    dtypes)?  Integer-Tensoren sind Positionen und müssen in [-1, n_ctx] liegen."""
    if soll is None or geladen is None:
        return soll is None and geladen is None
    if isinstance(soll, torch.Tensor):
        if not isinstance(geladen, torch.Tensor) or geladen.shape != soll.shape or geladen.dtype != soll.dtype:
            return False
        if not geladen.dtype.is_floating_point and geladen.dtype != torch.bool and geladen.numel():
            return bool(int(geladen.min()) >= -1 and int(geladen.max()) <= n_ctx)
        return True
    if isinstance(soll, (list, tuple)):
        return (isinstance(geladen, (list, tuple)) and len(geladen) == len(soll)
                and all(_gleiche_form(a, b, n_ctx) for a, b in zip(geladen, soll)))
    return False


def _im_vram(t: torch.Tensor) -> bool:
    """Tensor im VRAM – kein aus pinned RAM abgebildetes Gewicht (Schichten im RAM), das kein
    VRAM belegt und an seinem Platz bleibt, wenn das Modell geparkt wird."""
    return t.device.type == "cuda" and not hostmem.abgebildet(t)


class Engine:
    @schliesst_bei_fehler()
    def __init__(self, path: str, device: Optional[str] = None, dtype: str = "bf16",
                 n_ctx: int = 8192, thinking: bool = False, verbose: bool = True, weights: str = "quant",
                 progress=None, max_tok_s: Optional[float] = None, kv_bits: int = 16,
                 experts_vram: Optional[int] = None, kv_gpu_tokens: Optional[int] = None,
                 weights_vram: Optional[int] = None):
        """`denk_budget` (Attribut): max. Denk-Tokens je Antwort in chat() (None: keine Grenze).
        progress: optionaler callback(Anteil 0..1) für den Ladefortschritt (von der GUI genutzt).
        max_tok_s: Obergrenze für erzeugte Tokens pro Sekunde (None = so schnell wie möglich);
        die GPU pausiert zwischen den Schritten, statt unter Volllast zu laufen.
        kv_bits: 16 = KV-Cache im Rechen-dtype, 8 = int8 mit einer Skala je 32 Werte
        (etwa halbes VRAM je Kontext-Token), 4 = rotierte 4-Bit-Stufen (etwa 28 %).
        experts_vram: VRAM-Budget in Bytes für MoE-Experts (None: alle im VRAM, 0: alle im pinned
        RAM); Experts darüber hinaus bleiben im RAM, und die GPU liest die gewählten über PCIe.
        kv_gpu_tokens: Positionen jedes vollen KV-Cache, die im VRAM bleiben (None: alle); der Rest von
        n_ctx liegt im RAM und wird je Attention-Aufruf auf die GPU kopiert (dann keine CUDA-Graphs).
        weights_vram: VRAM-Budget in Bytes für die Gewichte (None: alle im VRAM, 0: alles, was in
        pinned RAM wandern kann); siehe ggufengine.schichten – die GPU liest diese Matrizen über PCIe."""
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        if dtype not in _DTYPES:
            raise ValueError(f"unknown dtype {dtype!r}")
        self.dtype = _DTYPES[dtype]
        if self.device.type == "cpu" and self.dtype == torch.float16:
            self.dtype = torch.bfloat16   # fp16-matmul ist auf der CPU sehr langsam
        self.verbose = verbose
        self.max_tok_s = max_tok_s if max_tok_s and max_tok_s > 0 else None
        t0 = time.time()
        self.load_phases: dict = {}   # Sekunden je Phase: Datei, Tokenizer, Gewichte, Warmup (+ Vision)
        self.gg = GGUFFile(path)
        self.load_phases["file"] = time.time() - t0
        t = time.time()
        self.tokenizer = Tokenizer(self.gg)
        self.load_phases["tokenizer"] = time.time() - t
        total = sum(t.n_bytes for t in self.gg.tensor_list)
        self._log_model_header(path, weights, n_ctx)

        user_progress = progress

        def progress(name, done):
            frac = done / max(total, 1)
            if user_progress is not None:
                user_progress(frac)
            elif verbose:
                sys.stderr.write(f"\rloading weights: {100 * frac:5.1f}%   ")
                sys.stderr.flush()

        if weights not in ("quant", "bf16"):
            raise ValueError("weights must be 'quant' or 'bf16'")
        t = time.time()
        # pinned Allokationen, Freigaben und synchronize würden die Graph-Aufnahme einer anderen
        # Engine in diesem Prozess zerstören
        from .models import common as _common
        self.schichten_plan = None
        if weights_vram is not None and self.device.type == "cuda":
            self.schichten_plan = schichten.planen(self.gg, weights_vram)
        with GPU_SPERRE:
            _common.KV_GPU_SLOTS = kv_gpu_tokens if kv_gpu_tokens and kv_gpu_tokens < n_ctx else None
            _common.SCHICHTEN_RAM = self.schichten_plan.ram if self.schichten_plan else frozenset()
            try:
                self.model = build_model(self.gg, self.device, self.dtype, n_ctx, progress,
                                         quant=(weights == "quant"), kv_bits=kv_bits, experten_vram=experts_vram)
            finally:
                _common.KV_GPU_SLOTS = None
                _common.SCHICHTEN_RAM = frozenset()
            if any(c.geteilt for c in self._full_caches()):
                self.model.graph = None                  # RAM-Kopien lassen sich nicht aufnehmen
            if self.device.type == "cuda":
                torch.cuda.synchronize(self.device)      # anstehende Umwandlungen zählen als Gewichte
                release_pinned_memory()
        self.load_phases["weights"] = time.time() - t
        t = time.time()
        with GPU_SPERRE:
            self._warmup()
            if self.device.type == "cuda":
                torch.cuda.synchronize(self.device)
        self.load_phases["warmup"] = time.time() - t
        self.load_seconds = time.time() - t0
        self._log_loaded()
        if verbose:
            mem = f", {torch.cuda.memory_allocated() / 2**30:.2f} GiB VRAM" if self.device.type == "cuda" else ""
            mode = (f"{self.model.n_quant} matrices quantised in VRAM" if self.model.n_quant
                    else f"weights in {self.dtype}")
            sys.stderr.write(f"\rloaded {self.gg.get('general.name', path)} ({self.gg.architecture}) "
                             f"in {time.time() - t0:.1f}s on {self.device}{mem}; {mode}\n")
        vorlage = self.gg.get("tokenizer.chat_template")
        fmt = detect_format(vorlage, self.gg.architecture)
        self.chat_format = ChatFormatter(self.tokenizer, fmt, thinking=thinking, template=vorlage)
        self.n_ctx = n_ctx
        self._pos = 0
        self._history: List[int] = []
        self._own: List[bool] = []        # je Position: vom Modell erzeugt (DRY)
        self._breaker = None              # Bool-Array über das Vokabular, beim ersten Gebrauch gebaut
        # DRY bleibt zwischen diesen Marken der eigenen Modellausgabe aus (neben ```-Codeblöcken): ein Tool-Call
        # wiederholt Namen und Werte absichtlich – (Anfang, Ende) als Bytes, Ende b"" = bis zum Ende der Antwort
        self.dry_frei: tuple = ()
        # ein Schlüssel je gecachter Position: die Token-id oder ("img", bild_nr(embeddings), Zeile) für eine
        # Bildposition – verschiedene Bilder teilen die Platzhalter-id, aber nicht den Schlüssel
        self._keys: list = []
        self._images: dict = {}          # Bildschlüssel -> (Embeddings-Tensor, Zeile)
        self.pad_id = int(self.gg.get("tokenizer.ggml.padding_token_id", 0) or 0)
        # M-RoPE-Modelle: Rotary-Position des nächsten Text-Tokens und des Anfangs des
        # gerade gelesenen Bildes (Bilder belegen weniger Positionen als Cache-Slots)
        self._mrope = bool(getattr(self.model, "uses_mrope", False))
        self._rope_state = (0, 0)
        self.vision = None
        self._checkpoint = None          # (Position, Präfix-Schlüssel, Modellzustand) nach dem System-Turn
        self._answer_checkpoint = None   # dasselbe vor dem Generierungs-Prompt des letzten chat()
        self.checkpoint_new = False      # gesetzt, wenn chat() einen neuen Checkpoint angelegt hat (save_prefix)
        self.last_cached = 0             # Prompt-Tokens, die im letzten chat() aus dem Cache kamen
        self.last_ids: List[int] = []    # im letzten Aufruf erzeugte Token-ids
        self.last_stats: dict = _leere_statistik()
        # spekulatives Decoding: max. vorgeschlagene Tokens je Durchgang geprüft (0 = aus), siehe spekulativ.py
        self.spekulativ = SPEKULATIV
        self._vorrat: list = []          # (vorgeschlagenes Token, Logits danach), noch nicht übernommen

    # -- Debug-Log -------------------------------------------------------------
    def _log_model_header(self, path, weights, n_ctx):
        gg = self.gg
        arch = gg.architecture
        dbg.section(f"Modell laden: {gg.get('general.name', path)}")
        dbg.kv({"Datei": path, "Größe": f"{gg.file_size / 2**30:.2f} GiB", "GGUF-Version": gg.version,
                "Architektur": arch, "Tensoren": len(gg.tensor_list), "Metadaten-Keys": len(gg.metadata),
                "Quant (file_type)": gg.get("general.file_type"), "Gerät": str(self.device), "dtype": str(self.dtype),
                "weights-Modus": weights, "n_ctx": n_ctx,
                "Tokenizer": f"{gg.get('tokenizer.ggml.model')} / pre={gg.get('tokenizer.ggml.pre')} / "
                             f"vocab={self.tokenizer.n_vocab} / bos={self.tokenizer.bos_id} eos={self.tokenizer.eos_id} "
                             f"stop={sorted(self.tokenizer.stop_ids)}"})
        hp = {k: v for k, v in gg.metadata.items()
              if k.startswith(arch + ".") and not isinstance(v, list)}
        hp.update({k: f"[{len(v)} Einträge] {v[:8]}" for k, v in gg.metadata.items()
                   if k.startswith(arch + ".") and isinstance(v, list)})
        dbg.kv(hp, "Hyperparameter")
        types = {}
        for t in gg.tensor_list:
            types[t.type_name] = types.get(t.type_name, 0) + 1
        dbg.kv(types, "Tensor-Typen")
        if self.device.type == "cuda":
            dbg.event(f"VRAM vor dem Laden: {torch.cuda.memory_allocated() / 2**30:.2f} GiB")

    def _log_loaded(self):
        mem = f"{torch.cuda.memory_allocated() / 2**30:.2f} GiB" if self.device.type == "cuda" else "-"
        dbg.kv({"Ladezeit inkl. Warmup": f"{self.load_seconds:.1f}s", "VRAM nach dem Laden": mem,
                "quantisierte Matrizen": self.model.n_quant,
                "Schichten im RAM": (f"{len(self.schichten_plan.ram)} Matrizen, "
                                     f"{self.schichten_plan.ram_bytes / 2**30:.2f} GiB" if self.schichten_plan
                                     and self.schichten_plan.ram else "keine"),
                "Modellklasse": type(self.model).__name__}, "Geladen")

    def _warmup(self):
        """Lazy CUDA/cuBLAS-Initialisierung und die erste Graph-Aufnahme vorab auslösen."""
        ids = self.tokenizer.encode("warm up") or [0]
        self.model.forward(ids, 0)
        self.model.forward([ids[0]], len(ids))
        self.model.reset()

    # -- Low-Level -----------------------------------------------------------
    def reset(self):
        self.model.reset()
        self._answer_checkpoint = None
        self._pos = 0
        self._history = []
        self._own = []
        self._keys = []
        self._rope_state = (0, 0)

    def _rope_positions(self, keys: list, state: tuple):
        """(t, h, w)-Rotary-Positionen für `keys` ab `state`; gibt sie mit dem neuen
        Zustand zurück.  Text: dieselbe Position auf allen Achsen.  Bild aus r x c Tokens
        ab s: (s, s + row, s + col); der folgende Text beginnt bei s + max(r, c)."""
        nxt, start = state
        out = []
        for k in keys:
            if not isinstance(k, tuple):
                out.append((nxt, nxt, nxt))
                nxt += 1
                continue
            img, row = self._images[k]
            rows, cols = img.grid
            if row == 0:
                start = nxt
            out.append((start, start + row // cols, start + row % cols))
            if row == rows * cols - 1:
                nxt = start + max(rows, cols)
        return out, (nxt, start)

    def feed(self, tokens: List[int], chunk: Optional[int] = None, keys: Optional[list] = None,
             own: Optional[list] = None, abbruch: Optional[Callable[[], bool]] = None) -> torch.Tensor:
        """Tokens durch das Modell schicken (in Stücken); gibt die Logits des letzten Tokens zurück.
        `keys` markiert Bildpositionen (siehe `chat`); ihre Embeddings kommen aus `_images`.
        `own`: je Token, vom Modell erzeugt (Standard: nein).  `abbruch()` True stoppt
        vor dem nächsten Stück; nur das Gelesene zählt."""
        if self.offloaded:
            self.restore()
        keys = list(tokens) if keys is None else list(keys)
        logits = None
        grenze = int(getattr(self.model, "prefill_chunk", PREFILL_CHUNK))
        chunk = max(1, min(chunk or grenze, grenze))
        self._kv_room(self._pos + len(tokens))
        ganz = bool(getattr(self.model, "images_bidirectional", False))
        i = 0
        while i < len(tokens):
            if abbruch is not None and abbruch():
                break
            ende = min(i + chunk, len(tokens))
            if ganz:                                    # ein Bild wird in einem Aufruf gelesen (bidirektional)
                ende = _bild_nicht_teilen(keys, i, ende)
            part, kpart = tokens[i:ende], keys[i:ende]
            i = ende
            rows = [j for j, k in enumerate(kpart) if isinstance(k, tuple)]
            extra = {}
            if self._mrope:
                pos3, self._rope_state = self._rope_positions(kpart, self._rope_state)
                if rows:
                    extra["pos3"] = pos3
                else:                           # reiner Text: Slot + Offset, behält den CUDA-Graph
                    self.model.set_rope_offset(pos3[0][0] - self._pos)
            if rows:
                emb = torch.stack([self._images[kpart[j]][0][self._images[kpart[j]][1]] for j in rows])
                model_tokens = [self.pad_id if isinstance(k, tuple) else t for t, k in zip(part, kpart)]
                inject = (torch.tensor(rows, device=self.device, dtype=torch.long), emb.to(self.device))
                logits = self.model.forward(model_tokens, self._pos, inject, **extra)
            else:
                logits = self.model.forward(part, self._pos)
            self._pos += len(part)
            if i < len(tokens) and self.device.type == "cuda" and _frei_reserviert(self.device) > VORRAT_GRENZE:
                self._release_scratch()
        if i < len(tokens):                             # abgebrochen: nur der gelesene Teil wird gecacht
            tokens, keys = tokens[:i], keys[:i]
            own = own[:i] if own is not None else None
        if len(tokens) >= RELEASE_FROM and self.device.type == "cuda":
            self._release_scratch()
        self._history.extend(tokens)
        self._own.extend(own if own is not None else [False] * len(tokens))
        self._keys.extend(keys)
        return logits

    @staticmethod
    def _release_scratch() -> None:
        """Gibt den Scratch-Speicher eines langen Einlesens (bf16-entpackte Matrizen, Aktivierungen)
        an den Treiber zurück; der Caching-Allocator hielte ihn sonst für den Rest der
        Sitzung reserviert.  Nicht während ein CUDA-Graph aufgenommen wird."""
        with GPU_SPERRE:
            torch.cuda.empty_cache()

    # -- Bilder ------------------------------------------------------------------
    def enable_vision(self, mmproj_path: str) -> None:
        """Hängt einen Vision-Projektor (mmproj) an; der Encoder wird anhand der Datei gewählt
        (siehe vision.load_projector).  Seine Gewichte bleiben im RAM und kommen nur
        innerhalb von `encode_image` auf die GPU."""
        if not getattr(self.model, "supports_images", False):
            raise ValueError(f"{self.gg.architecture} does not take image inputs")
        from .vision import load_projector
        t = time.time()
        vision = load_projector(mmproj_path, self.device)
        self.load_phases["vision"] = time.time() - t
        width = self.gg.get(f"{self.gg.architecture}.embedding_length")
        if width is not None and int(width) != vision.n_out:
            raise ValueError(f"mmproj width {vision.n_out} does not match the model ({width})")
        if not all(m in self.tokenizer.special for m in vision.marks):
            raise ValueError("the model has no image tokens for this mmproj")
        if vision.uses_grid and not self._mrope:
            raise ValueError("this mmproj needs a model with M-RoPE")
        if getattr(vision, "n_deepstack", 0) and not getattr(self.model, "uses_deepstack", False):
            raise ValueError("this mmproj has DeepStack features (Qwen3-VL) the model cannot take")
        self.chat_format.image_marks = vision.marks
        self.vision = vision

    def encode_image(self, image, tokens: Optional[int] = None) -> torch.Tensor:
        """PIL-Bild -> Bild-Embeddings (CPU-Tensor) für `Message.images`.  Die Vision-
        Gewichte werden nur für diesen Aufruf auf die GPU geladen.  Encoder mit Token-Raster
        hinterlegen es in `.grid` (rows, cols)."""
        if self.vision is None:
            raise RuntimeError("no vision projector attached (enable_vision)")
        t0 = time.time()
        try:
            with GPU_SPERRE:
                self.vision.load()            # OOM mitten im Laden: finally räumt die halben Gewichte ab
                if self.device.type == "cuda":
                    release_pinned_memory()
            out = self.vision.encode(image, tokens or self.vision.default_tokens)
        finally:
            self.vision.unload()
        grid = None
        if isinstance(out, tuple):
            out, grid = out
        emb = out.cpu()
        bild_nr(emb)
        if grid is not None:
            emb.grid = tuple(grid)
        dbg.event(f"Bild kodiert: {emb.shape[0]} Token in {time.time() - t0:.2f}s")
        return emb

    def _breakers(self):
        """Für DRY, über das Vokabular: (Tokens, die einen Treffer beenden – Zeilenumbruch, ':', '"', '*'
        und Spezial-Tokens; Tokens, die ein Wort beginnen – Leerzeichen oder Satzzeichen zuerst)."""
        if self._breaker is None:
            import numpy as np
            tok = self.tokenizer
            n = max(tok.n_vocab, self.model.n_vocab)
            b, w = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
            for tid in range(tok.n_vocab):
                if tok.types[tid] != 1:                      # Steuerzeichen, benutzerdefiniert, Byte …
                    b[tid] = True
                    continue
                try:
                    t = tok.token_bytes(tid)
                except Exception:
                    b[tid] = True
                    continue
                b[tid] = any(c in t for c in (b"\n", b":", b'"', b"*"))
                w[tid] = bool(t) and (t[:1].isspace() or (t[0] < 0x80 and not t[:1].isalnum()))
            self._breaker = (b, w)
        return self._breaker

    # -- spekulatives Decoding -----------------------------------------------------
    # Ein Vorschlag aus k Tokens wird zusammen mit dem gesampelten Token in einem Forward-Pass gelesen
    # (Logits jeder Zeile).  Jede Position wird weiterhin aus den eigenen Logits des Modells gesampelt,
    # mit dem Verlauf bis dorthin; ein vorgeschlagenes Token bleibt nur, wenn es diesem Sample gleicht.
    def _entwurf(self) -> Optional[NGramEntwurf]:
        model = getattr(self, "model", None)
        n = getattr(self, "spekulativ", 0)
        if not n or not hasattr(model, "alle_logits"):
            return None
        if getattr(model, "mtp", None) is not None:
            return MtpEntwurf(self._history, model, lambda: self._pos, max_neu=n)
        return NGramEntwurf(self._history, max_neu=n)

    def _rekurrent(self) -> bool:
        """Der Modellzustand kann nicht zurückgehen (rekurrente Schichten): er wird vor einer Prüfung gesichert."""
        return self.model.resume_from(1, 2) != 1

    def _vorwaerts(self, tokens: List[int], pos: int, alle: bool = False) -> torch.Tensor:
        """Text-Tokens an den Cache-Slots pos.. ohne Buchführung; alle: Logits jeder Zeile."""
        if self._mrope:                     # Text: der Rotary-Offset ist für jeden Slot gleich
            self.model.set_rope_offset(self._rope_state[0] - self._pos)
        self._kv_room(pos + len(tokens))
        graph = getattr(self.model, "graph", None)
        if len(tokens) > 1 and graph is not None:
            with torch.inference_mode():
                out = graph.mehrere(tokens, pos)
            if out is not None:
                return out if alle else out[-1]
        self.model.alle_logits = alle
        try:
            if alle:                                 # eine Prüfung: rekurrente Schichten behalten ihre Zeilen
                with ZeilenMerken():
                    return self.model.forward(tokens, pos)
            return self.model.forward(tokens, pos)
        finally:
            self.model.alle_logits = False

    def _eintragen(self, tokens: List[int]) -> None:
        n = len(tokens)
        self._pos += n
        self._history.extend(tokens)
        self._own.extend([True] * n)
        self._keys.extend(tokens)
        if self._mrope:
            self._rope_state = (self._rope_state[0] + n, self._rope_state[1])

    def _uhr(self) -> float:
        if self.device.type == "cuda":      # der Sampler wartet gleich danach ohnehin auf die GPU
            with GPU_SPERRE:                # nicht während einer Graph-Aufnahme in einem anderen Thread
                torch.cuda.synchronize()
        return time.perf_counter()

    def _graph_aufnahmen(self) -> int:
        graph = getattr(getattr(self, "model", None), "graph", None)
        return getattr(graph, "aufnahmen", 0)

    def _spek_ende(self, entwurf: Optional[NGramEntwurf], angenommen: int) -> None:
        if entwurf is None:
            return
        if self._graph_aufnahmen() != getattr(self, "_aufnahmen", 0):
            self._nutzen.ungueltig()                 # diese Prüfung hat einen Graph aufgenommen: keine typische
        entwurf.ergebnis(angenommen)
        pause = self._nutzen.pause(angenommen)
        if pause:
            entwurf.pausieren(pause)

    def _weiter(self, tid: int, entwurf: Optional[NGramEntwurf], vorschlagen: bool = True) -> torch.Tensor:
        """Übernimmt das gesampelte `tid` an Position `_pos`; gibt die Logits für die nächste Position zurück."""
        if entwurf is not None:
            entwurf.zufuegen(tid)
        if self._vorrat:
            gedacht, zeile = self._vorrat.pop(0)
            if gedacht == tid:                       # schon im Cache
                self._eintragen([tid])
                self._spek_stat[1] += 1
                self._nutzen.pruefung(0.0, 1)
                if not self._vorrat:
                    self._spek_ende(entwurf, self._spek_n)
                return zeile
            angenommen = self._spek_n - len(self._vorrat) - 1
            self._vorrat = []
            basis, zustand = self._spek_basis
            logits = None
            if zustand is not None:                  # Zustand der behaltenen Zeilen: neu gebaut, sonst neu gelesen
                t0 = self._uhr()
                self.model.restore_state(zustand)
                if not self._nachholen(basis):
                    logits = self._vorwaerts(self._history[basis:self._pos] + [tid], basis)
                    self._eintragen([tid])
                self._nutzen.pruefung(self._uhr() - t0, 0 if logits is None else 1)
            self._spek_ende(entwurf, angenommen)     # nach dem Neuaufbau: er gehört zur Prüfung
            if logits is not None:
                return logits
            # Attention-Caches: die Slots hinter _pos sind maskiert und werden überschrieben; `tid` wird
            # als Nächstes gelesen, wie nach einer Prüfung ohne Vorschläge
        platz = min(self.n_ctx - self._pos - 1, PREFILL_CHUNK - 1)
        # der Sampler hat gerade auf die GPU gewartet: die Uhr startet ohne synchronize
        t0 = time.perf_counter()                     # Vorschlagen zählt zur Prüfung
        vorher = self._graph_aufnahmen() if entwurf is not None else 0   # vor dem Vorschlagen: auch MTP nimmt auf
        if isinstance(entwurf, MtpEntwurf) and vorschlagen and platz > 0:
            self._kv_room(self._pos + min(entwurf.mtp_n, platz))   # MTP schreibt seinen KV-Cache ab _pos
        vorschlag = entwurf.vorschlag(platz) if entwurf is not None and vorschlagen else []
        grenze = self._nutzen.erlaubt(len(vorschlag)) if vorschlag else 0
        k = next((s for s in VORSCHLAG_STUFEN if s <= min(len(vorschlag), grenze)), 0)
        vorschlag = vorschlag[:k]                    # wenige Größen: wenige aufgenommene Graphs
        if not vorschlag:
            logits = self.feed([tid], own=[True])
            if entwurf is not None and self._nutzen.einzeln_messen():   # ein synchronize nur ab und zu
                dauer = self._uhr() - t0
                if self._graph_aufnahmen() == vorher:   # kein Schritt, der seinen Graph aufgenommen hat
                    self._nutzen.einzeln(dauer)
            return logits
        self._nutzen.start(len(vorschlag))
        self._aufnahmen = vorher
        zustand = self.model.snapshot_state() if self._rekurrent() else None
        self._spek_basis = (self._pos, zustand)
        self._spek_L = 1 + len(vorschlag)
        zeilen = self._vorwaerts([tid] + vorschlag, self._pos, alle=True)
        self._eintragen([tid])
        self._vorrat = list(zip(vorschlag, zeilen[1:]))
        self._spek_n = len(vorschlag)
        self._spek_stat[0] += len(vorschlag)
        self._nutzen.pruefung(self._uhr() - t0, 1)
        return zeilen[0]

    def _verwerfen(self) -> None:
        """Verwirft nicht übernommene vorgeschlagene Tokens (Ende der Antwort, erzwungenes Denk-Ende)."""
        if not self._vorrat:
            return
        self._vorrat = []
        basis, zustand = self._spek_basis
        if zustand is not None:
            self.model.restore_state(zustand)
            if not self._nachholen(basis):
                self._vorwaerts(self._history[basis:self._pos], basis)

    def _nachholen(self, basis: int) -> bool:
        """Rekurrenter Zustand nach den Zeilen der letzten Prüfung, behalten bis `_pos` (Zustand vor der
        Prüfung wiederhergestellt), aus den Eingaben je Zeile, die die Schichten behalten haben – ohne erneutes
        Lesen."""
        f = getattr(self.model, "zeilen_nachholen", None)
        return f is not None and f(self._spek_L, self._pos - basis)

    def generate_tokens(self, prompt_ids: List[int], max_tokens: int = 512,
                        sampler: Optional[SamplerConfig] = None, stop_ids=None,
                        prompt_keys: Optional[list] = None, prompt_own: Optional[list] = None,
                        denk_budget: Optional[tuple] = None,
                        abbruch: Optional[Callable[[], bool]] = None) -> Iterator[int]:
        """denk_budget = (Tokens, Öffnen-ids, Schließen-ids, beginnt innen): nach `tokens` Denk-Tokens
        werden die Schließen-ids eingefügt und die Generierung geht mit der Antwort weiter (Budget
        Forcing).  Das eigene Schließen des Modells (erstes Schließen-Token) beendet das Denken ebenfalls.
        `abbruch()` True stoppt schon beim Einlesen des Prompts (zwischen Stücken), ohne Token."""
        cfg = sampler or SamplerConfig()
        if denk_budget:
            grenze, auf, zu, drin = denk_budget
            gedacht, zuletzt = 0, []
        smp = Sampler(cfg, self.device, getattr(self.tokenizer, "n_vocab", None))
        stop = set(self.tokenizer.stop_ids)
        if stop_ids:
            stop |= set(stop_ids)
        if self._pos + len(prompt_ids) + 1 > self.n_ctx:
            raise RuntimeError("prompt does not fit into the context window")
        t0 = time.time()
        if abbruch is not None:
            vorher = len(self._history)
            logits = self.feed(prompt_ids, keys=prompt_keys, own=prompt_own, abbruch=abbruch)
            if len(self._history) - vorher < len(prompt_ids):   # Prompt nur zum Teil gelesen
                self._prefill_seconds = time.time() - t0
                return
        else:
            logits = self.feed(prompt_ids, keys=prompt_keys, own=prompt_own)
        self._lager_umsortieren()                            # was der Prompt gewählt hat, kommt ins VRAM
        self._prefill_seconds = time.time() - t0
        step = 1.0 / self.max_tok_s if self.max_tok_s else 0.0
        next_at = time.perf_counter()
        dry = cfg.dry_multiplier > 0
        breaker, word_start = self._breakers() if dry else (None, None)
        erzeugt = b""                                        # für Codeblöcke: DRY bleibt darin aus
        entwurf = self._entwurf()
        self._vorrat, self._spek_stat = [], [0, 0]           # vorgeschlagen, übernommen
        if entwurf is not None and getattr(self, "_nutzen", None) is None:
            self._nutzen = Nutzen()                          # bleibt über Antworten hinweg (gleiches Modell)
        try:
            for nr in range(1, max_tokens + 1):
                if LAGER_TAKT and nr % LAGER_TAKT == 0:
                    self._lager_umsortieren(melden=False)   # Experts dieser Antwort ins VRAM
                code = dry and _dry_frei(erzeugt, self.dry_frei)
                tid = smp.sample(logits, self._history, self._own if dry and not code else None, breaker,
                                 word_start)
                if tid in stop:
                    break
                yield tid
                if self._pos + 1 > self.n_ctx:
                    break
                if denk_budget:
                    zuletzt = (zuletzt + [tid])[-len(auf):]
                    if not drin and zuletzt == auf:
                        drin, gedacht = True, 0
                    elif drin and tid == zu[0]:
                        drin = False
                    elif drin:
                        gedacht += 1
                        if gedacht >= grenze and self._pos + 1 + len(zu) <= self.n_ctx:
                            self._weiter(tid, entwurf, vorschlagen=False)   # letztes Denk-Token
                            self._verwerfen()
                            for t in zu:                                     # dann den Block schließen
                                yield t
                                if entwurf is not None:
                                    entwurf.zufuegen(t)
                            logits = self.feed(zu, own=[True] * len(zu))
                            drin, denk_budget = False, None
                            continue
                if step:
                    next_at += step
                    wait = next_at - time.perf_counter()
                    if wait > 0:
                        time.sleep(wait)
                    else:
                        next_at = time.perf_counter()   # langsamer als die Grenze: nicht aufholen
                if dry:
                    erzeugt += self.tokenizer.token_bytes(tid)
                logits = self._weiter(tid, entwurf)
        finally:
            self._verwerfen()
            self._spek_basis = None                          # sein Zustands-Snapshot bliebe im VRAM

    # -- High-Level ----------------------------------------------------------
    def chat(self, messages: List[Message], reuse: bool = True, checkpoint_system: bool = True,
             abbruch: Optional[Callable[[], bool]] = None, **kw) -> Iterator[str]:
        """Chat über das ganze Gespräch.  Mit `reuse` bleiben die Tokens, die schon im KV-
        Cache liegen, erhalten, soweit sie zum neuen Prompt passen (Präfix-Cache), und nur der
        Rest wird gelesen.  `checkpoint_system` sichert den kleinen Modellzustand direkt nach dem
        System-Turn, sodass ein Gespräch, das später abzweigt, den System-Prompt nie erneut
        liest.  `self.last_ids` enthält danach die erzeugten Token-ids (für
        `Message.ids`).  `abbruch()` True stoppt das Einlesen zwischen Stücken (ohne Antwort);
        das Gelesene bleibt gecacht."""
        ids = self.chat_format.encode(messages, add_generation_prompt=True)
        keys = self._image_keys(messages, ids)
        own = self._own_mask(messages, ids)
        start = self._resume(keys) if reuse else self._start_over()
        self.last_cached = start
        self.last_ids = []
        self.last_stats = _leere_statistik()  # bricht das Einlesen ab, gilt nicht die Statistik des letzten Aufrufs
        ab = {} if abbruch is None else {"abbruch": abbruch}
        if checkpoint_system and (cut := self._system_end(messages, ids)) and start < cut < len(ids):
            self.feed(ids[start:cut], keys=keys[start:cut], own=own[start:cut], **ab)
            if len(self._history) < cut:
                return
            self._checkpoint = (cut, keys[:cut], self.model.checkpoint_state())
            self.checkpoint_new = True
            start = cut
        # Checkpoint vor dem Generierungs-Prompt: wenn der nächste Prompt diese Antwort
        # anders darstellt (Denken weggelassen), setzen Modelle, deren Zustand nicht zurückgehen kann (rekurrente
        # Schichten, Sliding-Window-Ringe) hier fort, statt das Gespräch erneut zu lesen
        ohne = self.chat_format.encode(messages, add_generation_prompt=False)
        vor = len(ohne)
        if start < vor < len(ids) and ids[:vor] == ohne:
            self.feed(ids[start:vor], keys=keys[start:vor], own=own[start:vor], **ab)
            if len(self._history) < vor:
                return
            zustand = self.model.checkpoint_state()
            self._answer_checkpoint = (vor, keys[:vor], zustand) if zustand is not None else None
            start = vor
        dbg.section(f"Chat-Anfrage ({len(messages)} Nachrichten, {len(ids)} Prompt-Tokens, "
                    f"{start} aus dem Cache)")
        smp = kw.get("sampler")
        dbg.kv({"Template": self.chat_format.fmt, "Thinking": self.chat_format.thinking,
                "max_tokens": kw.get("max_tokens"), "Sampler": smp.__dict__ if smp else "default",
                "Rollen": " → ".join(m.role for m in messages)})
        if dbg.path():                       # den ganzen Prompt zurückverwandeln nur, wenn das Log an ist
            dbg.code(self.tokenizer.decode(ids, skip_special=False), "text",
                     "Gerenderter Prompt (Template + Nachrichten)", limit=4000)
        dbg.code(" ".join(map(str, ids[:48])) + (" …" if len(ids) > 48 else ""), "text", "Erste Prompt-Token-IDs")
        marken = self.chat_format.denk_marken() if self.chat_format.thinking else None
        if getattr(self, "denk_budget", None) and marken and "denk_budget" not in kw:
            kw["denk_budget"] = (self.denk_budget, marken[0], marken[1], self.chat_format.opens_thinking)
        yield from self._stream(ids[start:], keys=keys[start:], own=own[start:], **ab, **kw)

    def vorlesen(self, messages: List[Message], abbruch: Optional[Callable[[], bool]] = None) -> int:
        """Liest `messages` in den Cache, ohne zu antworten – der Anfang des nächsten
        Prompts, sodass der nächste chat(), der damit beginnt, nur seine neue Nachricht liest (Präfix-
        Cache).  `abbruch()` True stoppt zwischen Stücken; das Gelesene bleibt gecacht.
        Gibt die gelesenen Tokens zurück."""
        ids = self.chat_format.encode(messages, add_generation_prompt=False)
        if not ids:
            return 0
        keys = self._image_keys(messages, ids)
        own = self._own_mask(messages, ids)
        start = self._resume(keys)
        anfang = len(self._history)
        if (cut := self._system_end(messages, ids)) and start < cut < len(ids):
            self.feed(ids[start:cut], keys=keys[start:cut], own=own[start:cut], abbruch=abbruch)
            if len(self._history) < cut:
                return len(self._history) - anfang
            self._checkpoint = (cut, keys[:cut], self.model.checkpoint_state())
            self.checkpoint_new = True
            start = cut
        if start < len(ids):
            self.feed(ids[start:], keys=keys[start:], own=own[start:], abbruch=abbruch)
        self._lager_umsortieren()
        return len(self._history) - anfang

    @staticmethod
    def _own_mask(messages: List[Message], ids: List[int]) -> List[bool]:
        """True an den Positionen früherer Antworten, die diese Engine erzeugt hat (Message.ids)."""
        own = [False] * len(ids)
        pos = 0
        for m in messages:
            if m.role != "assistant" or not m.ids:
                continue
            seq, n = list(m.ids), len(m.ids)
            i = pos
            while i + n <= len(ids):
                try:
                    i = ids.index(seq[0], i)
                except ValueError:
                    break
                if i + n <= len(ids) and ids[i:i + n] == seq:
                    own[i:i + n] = [True] * n
                    pos = i + n
                    break
                i += 1
        return own

    def _image_keys(self, messages: List[Message], ids: List[int]) -> list:
        """Schlüssel für `ids`: die Token-id oder ("img", bild_nr(embeddings), row) an den Bild-
        Platzhaltern (in Nachrichtenreihenfolge).  Registriert die Embeddings in `_images`."""
        images = [img for m in messages if m.role == "user" for img in (m.images or [])]
        if not images:
            return list(ids)
        if not getattr(self.model, "supports_images", False):
            raise ValueError(f"{self.gg.architecture} does not take image inputs")
        if self._mrope and any(getattr(img, "grid", None) is None for img in images):
            raise ValueError("image embeddings without a token grid")
        slots = [(img, r) for img in images for r in range(int(img.shape[0]))]
        marks = self.chat_format.image_marks
        if not marks:
            raise ValueError("this model has no image tokens")
        placeholder = self.tokenizer.special_id(marks[1])
        where = [i for i, t in enumerate(ids) if t == placeholder]
        if len(where) != len(slots):
            raise ValueError("image placeholders and embeddings do not match")
        keys = list(ids)
        self._images = {}
        for i, (img, r) in zip(where, slots):
            key = ("img", bild_nr(img), r)
            keys[i] = key
            self._images[key] = (img, r)
        return keys

    # -- System-Prompt auf der Platte -----------------------------------------------
    PREFIX_FORMAT = 2                # 2: Qwen3.5-Zustand mit dem MTP-Hidden-State

    def _kv_room(self, n: int) -> None:
        """Die vollen KV-Caches auf n Positionen vergrößern; aufgenommene Decode-Graphs verwerfen, wenn
        ein Cache verschoben wurde."""
        if getattr(self, "_kv_list", None) is None:
            self._kv_list = self._full_caches()
        moved = False
        for c in self._kv_list:
            moved |= c.ensure(n)
        graph = getattr(self.model, "graph", None)
        if moved and graph is not None:            # Graphs und ihr Speicherpool, wie in offload()
            with GPU_SPERRE:
                torch.cuda.synchronize()
                graph.leeren()

    def _full_caches(self) -> list:
        """Volle Kontext-KV-Caches des Modells in fester Reihenfolge (Ring-Caches gehören zum
        Checkpoint-Zustand)."""
        from .models.common import KVCache
        return self._objekte(KVCache)

    def _lager(self) -> list:
        """Lager der MoE-Schichten (VRAM-Slots für die meistgenutzten Experts)."""
        if getattr(self, "_lager_liste", None) is None:
            from .models.moe import ExpertenLager
            self._lager_liste = self._objekte(ExpertenLager) if getattr(self, "model", None) is not None else []
        return self._lager_liste

    def _lager_umsortieren(self, melden: bool = True) -> None:
        """Meistgenutzte Experts in die VRAM-Slots – zwischen den Schritten, nie innerhalb eines Graphs.
        Innerhalb einer Antwort (`melden=False`) alle LAGER_TAKT Tokens: die Slots folgen dem Thema."""
        lager = self._lager()
        if not lager:
            return
        t0 = time.time()
        with GPU_SPERRE:
            bewegt = sum(l.umsortieren() for l in lager)
        if bewegt and melden:
            treffer = sum(l.treffer() for l in lager) / len(lager)
            dbg.event(f"Experten-Lager: {bewegt} Experten getauscht in {time.time() - t0:.2f}s, "
                      f"Treffer danach {treffer:.0%}")

    def experten_zaehlung(self) -> Optional[List[List[float]]]:
        """Wie oft jeder Expert gewählt wurde, je MoE-Schicht mit Lager (None: kein Lager)
        – zum Aufbewahren für den nächsten Start."""
        lager = self._lager()
        if not lager:
            return None
        with GPU_SPERRE:
            return [[round(x, 3) for x in l.zaehler.tolist()] for l in lager]

    def experten_zaehlung_setzen(self, zaehlung) -> bool:
        """Zählungen aus einem früheren Lauf: die Lager starten warm (die meistgenutzten Experts kommen
        sofort ins VRAM).  False, wenn sie nicht zu diesem Modell passen."""
        lager = self._lager()
        if not lager or not isinstance(zaehlung, list) or len(zaehlung) != len(lager):
            return False
        werte = []
        for l, z in zip(lager, zaehlung):
            if not isinstance(z, list) or len(z) != l.n_exp:
                return False
            t = torch.tensor([float(x) if isinstance(x, (int, float)) else -1.0 for x in z])
            if not torch.isfinite(t).all() or (t < 0).any():
                return False
            werte.append(t)
        with GPU_SPERRE:
            for l, t in zip(lager, werte):
                l.zaehler.copy_(t)
        self._lager_umsortieren()
        return True

    def _objekte(self, typ) -> list:
        """Vom Modell erreichbare Objekte vom Typ `typ`, in fester Reihenfolge."""
        found, seen = [], set()

        def walk(obj, wurzel=False):
            if id(obj) in seen:
                return
            seen.add(id(obj))
            if isinstance(obj, typ):
                found.append(obj)
                return
            if isinstance(obj, (list, tuple)):
                for x in obj:
                    walk(x)
            elif wurzel or type(obj).__module__.startswith(_EIGENE_MODULE):
                names = list(getattr(obj, "__dict__", {}))
                for cls in type(obj).__mro__:
                    names += [n for n in getattr(cls, "__slots__", ()) if n not in names]
                for n in names:
                    if hasattr(obj, n):
                        walk(getattr(obj, n))
        walk(self.model, wurzel=True)
        return found

    def save_prefix(self, path: str, fingerprint: str) -> bool:
        """Schreibt den System-Turn-Checkpoint (Tokens, KV dieser Positionen, Modellzustand)
        als safetensors – nur Daten, nichts in der Datei wird je ausgeführt."""
        if self._checkpoint is None:
            return False
        import json
        import os
        from safetensors.torch import save_file
        cut, keys, state = self._checkpoint
        if any(not isinstance(k, int) for k in keys):
            return False                                     # Bilder im System-Turn: nicht gespeichert
        tensors = {}
        for i, c in enumerate(self._full_caches()):
            k, v = c.kv_echt(cut)                            # unrotiert: gültig für jedes KV-Format
            tensors[f"kv.{i}.k"] = k.contiguous()
            tensors[f"kv.{i}.v"] = v.contiguous()

        def flat(obj, name):
            if obj is None:
                return None
            if isinstance(obj, torch.Tensor):
                tensors[name] = obj.contiguous()
                return name
            if isinstance(obj, (list, tuple)):
                return [flat(x, f"{name}.{j}") for j, x in enumerate(obj)]
            raise TypeError(f"cannot store {type(obj).__name__}")
        form = flat(state, "state")
        meta = {"format": str(self.PREFIX_FORMAT), "fingerprint": fingerprint, "cut": str(cut),
                "keys": json.dumps(keys), "state": json.dumps(form)}
        tmp = path + ".tmp"
        save_file({k: v.detach().cpu() for k, v in tensors.items()}, tmp, metadata=meta)
        os.replace(tmp, path)
        self.checkpoint_new = False
        return True

    def load_prefix(self, path: str, fingerprint: str, system_ids: List[int]) -> bool:
        """Lädt einen gespeicherten System-Turn-Checkpoint, wenn er zu dieser Modelldatei und genau
        diesen System-Tokens gehört; gibt sonst False zurück (und ändert nichts)."""
        import json
        from safetensors import safe_open
        try:
            with safe_open(path, framework="pt", device="cpu") as f:
                meta = f.metadata() or {}
                if (meta.get("format") != str(self.PREFIX_FORMAT) or meta.get("fingerprint") != fingerprint
                        or json.loads(meta.get("keys", "[]")) != list(system_ids)):
                    return False
                cut = int(meta["cut"])
                caches = self._full_caches()
                if not 0 < cut < self.n_ctx:
                    return False
                kv = []
                for i, c in enumerate(caches):
                    k, v = f.get_tensor(f"kv.{i}.k"), f.get_tensor(f"kv.{i}.v")
                    want = (c.heads, cut, c.head_dim)
                    if tuple(k.shape) != want or tuple(v.shape) != want:
                        return False
                    kv.append((k, v))

                def build(form):
                    if form is None:
                        return None
                    if isinstance(form, str):
                        return f.get_tensor(form).to(self.device)
                    return [build(x) for x in form]
                state = build(json.loads(meta["state"]))
                if not _gleiche_form(state, self.model.checkpoint_state(), self.n_ctx):
                    return False                     # anderer Aufbau oder Positionen außerhalb des Kontexts
        except Exception:
            return False
        self.reset()
        self._kv_room(cut)
        with torch.inference_mode():
            for c, (k, v) in zip(caches, kv):
                c.fill(k, v)
            if state is not None:
                self.model.restore_checkpoint(state)
        keys = list(system_ids)
        self._pos, self._history, self._keys, self._own = cut, list(keys), keys, [False] * cut
        if self._mrope:
            self._rope_state = (cut, 0)
        self._checkpoint = (cut, list(keys), self.model.checkpoint_state())
        self.checkpoint_new = False
        return True

    # -- GPU <-> RAM ----------------------------------------------------------
    @property
    def offloaded(self) -> bool:
        return bool(getattr(self, "_moved", None))

    def offload(self) -> float:
        """Verschiebt jeden CUDA-Tensor des Modells (Gewichte, KV-Caches, Buffer) in pinned
        RAM und gibt das VRAM frei.  Der Gesprächszustand bleibt erhalten; `restore()` holt ihn
        zurück.  Gibt die benötigten Sekunden zurück."""
        if self.device.type != "cuda" or self.offloaded:
            return 0.0
        t0 = time.time()

        def to_ram(t):
            n = t.numel() * t.element_size()
            host = None
            if not hostmem.ram_reicht(n):    # zu wenig RAM zum Festsetzen: normaler RAM (langsamere Kopie)
                host = torch.empty(t.shape, dtype=t.dtype, device="cpu")
            elif n >= HOSTMEM_AB:            # passgenau (torch rundet pinned Blöcke auf 2^k auf)
                try:
                    host = hostmem.empty(n, self.device).view(t.dtype).view(t.shape)
                except RuntimeError:
                    host = torch.empty(t.shape, dtype=t.dtype, device="cpu")
            if host is None:
                host = torch.empty(t.shape, dtype=t.dtype, device="cpu", pin_memory=True)
            host.copy_(t, non_blocking=True)
            return host
        with GPU_SPERRE:                             # synchronize, Verschieben, empty_cache: nicht beim Aufnehmen
            torch.cuda.synchronize()
            from .models.common import GraphDecoder
            for decoder in self._objekte(GraphDecoder):  # aufgenommene Graphs zeigen auf VRAM-Adressen – alle,
                decoder.leeren()                         # auch die MTP-Vorschläge (qwen35 _mtp_graph)
            if (leser := getattr(self.model, "schnell", None)) is not None:
                leser.parken()                          # TensorRT-Leser: Gewichte aus dem VRAM
            punkte = _Pruefpunkte(self)
            try:
                self._moved = _umziehen((self.model, punkte), _im_vram, to_ram)
            except BaseException:                    # nichts verschoben: das Modell bleibt auf der GPU
                if leser is not None:
                    leser.aufwecken()
                release_pinned_memory()
                raise
            punkte.zurueck(self)
            torch.cuda.synchronize()
            # die alten VRAM-Tensoren hängen teils in Referenz-Kreisen: ohne Einsammeln blieben sie belegt
            gc.collect()
            torch.cuda.empty_cache()
        dbg.event(f"ausgelagert in {time.time() - t0:.2f}s, VRAM {torch.cuda.memory_allocated() / 2**30:.2f} GiB")
        return time.time() - t0

    def restore(self) -> float:
        """Holt ein ausgelagertes Modell zurück auf die GPU (nur die verschobenen Tensoren;
        Tabellen, die absichtlich im RAM liegen, bleiben dort)."""
        if not self.offloaded:
            return 0.0
        t0 = time.time()
        moved = self._moved
        zurueck = lambda t: t.to(self.device, non_blocking=True)          # noqa: E731
        with GPU_SPERRE:                             # Verschieben und synchronize: nicht während einer Aufnahme
            punkte = _Pruefpunkte(self)
            # ganz oder gar nicht: scheitert eine Kopie (VRAM voll), bleibt das Modell ausgelagert und
            # ein späteres restore() versucht es erneut
            try:
                _umziehen((self.model, punkte), lambda t: t.device.type == "cpu" and id(t) in moved, zurueck)
            except BaseException:
                gc.collect()
                torch.cuda.empty_cache()             # die schon angelegten Kopien gehen zurück an den Treiber
                raise
            punkte.zurueck(self)
            self._moved = None
            if (leser := getattr(self.model, "schnell", None)) is not None:
                leser.aufwecken()
            for lager in self._lager():              # die Slots haben neue Adressen bekommen
                lager.verankern()
            torch.cuda.synchronize()
            release_pinned_memory()                  # die RAM-Kopien werden nicht mehr gebraucht
        dbg.event(f"zurück auf der GPU in {time.time() - t0:.2f}s")
        return time.time() - t0

    # -- Entladen --------------------------------------------------------------
    @property
    def geleert(self) -> bool:
        return bool(getattr(self, "_geleert", False))

    def leeren(self) -> None:
        """Gibt allen Speicher frei, den die Engine hält (VRAM, pinned RAM, Datei-Views), auch während anderer Code
        das Engine-Objekt noch referenziert – das In-Process-Gegenstück zum Beenden eines Runner-Prozesses.
        Aufgenommene Graphs werden verworfen, der TensorRT-Leser wird abgehängt, und jeder von der Engine
        erreichbare Tensor wird durch einen leeren Platzhalter ersetzt. Die Engine ist danach nicht mehr
        nutzbar."""
        if self.geleert:
            return
        self._geleert = True
        if self.device.type == "cuda":
            with GPU_SPERRE:
                torch.cuda.synchronize(self.device)
        for name in ("graph", "_mtp_graph"):
            if (decoder := getattr(self.model, name, None)) is not None:
                decoder.leeren()
        if (hilfskopf_weg := getattr(self.model, "hilfskopf_weg", None)) is not None:
            hilfskopf_weg()                          # schließt die GGUF-Datei des Hilfskopfs
        if getattr(self.model, "schnell", None) is not None:
            self.model.schnell = None
        leer = torch.empty(0, device="meta")
        for wurzel in (self.model, getattr(self, "vision", None)):
            if wurzel is not None:
                _move_tensors(wurzel, lambda t: True, lambda t: leer)
        for name, wert in list(vars(self).items()):  # Checkpoints, Vorschläge, letzte Logits
            if name not in ("model", "vision", "device") and _haelt_tensor(wert):
                setattr(self, name, None)
        self._moved = None
        if (bild_gg := getattr(getattr(self, "vision", None), "gg", None)) is not None:
            try:
                bild_gg.close()                      # mmproj-Datei frei (noch offene Sichten schließen mit ihr)
            except Exception:
                pass
        if not graphen_aktiv():                      # sonst lesen aufgenommene Graphs anderer Modelle die Tabellen
            caches_leeren()

    # -- Präfix-Cache ----------------------------------------------------------
    def _start_over(self) -> int:
        self.reset()
        return 0

    def _resume(self, ids: list) -> int:
        """Behält das mit `ids` gemeinsame gecachte Präfix (Schlüssel, siehe `_image_keys`); gibt
        die Position zurück, ab der gelesen wird."""
        old = self._keys
        common = 0
        for a, b in zip(old, ids):
            if a != b:
                break
            common += 1
        common = min(common, len(ids) - 1)          # mindestens ein Token liefert die Logits
        start = self.model.resume_from(common, len(old)) if common > 0 else None
        cp = self._checkpoint
        if cp is not None and (len(ids) < cp[0] or ids[:cp[0]] != cp[1]):
            cp = self._checkpoint = None             # sein Präfix wird gleich überschrieben
        acp = getattr(self, "_answer_checkpoint", None)
        if acp is not None and (len(ids) < acp[0] or ids[:acp[0]] != acp[1]):
            acp = self._answer_checkpoint = None
        for punkt in (acp, cp):                      # der spätere zuerst
            if start is None and punkt is not None and punkt[0] <= common:
                self.model.restore_checkpoint(punkt[2])
                start = punkt[0]
        if start is None:
            return self._start_over()
        if acp is not None and acp[0] > start:
            self._answer_checkpoint = None           # die Slots hinter `start` werden überschrieben
        self._pos = start
        self._history = self._history[:start]
        self._own = self._own[:start]
        self._keys = old[:start]
        if self._mrope:
            _, self._rope_state = self._rope_positions(self._keys, (0, 0))
        return start

    def _system_end(self, messages: List[Message], ids: List[int]) -> int:
        """Token-Anzahl des System-Turns am Anfang von `ids` (0, wenn es keinen gibt)."""
        system = [m for m in messages if m.role == "system"]
        if not system:
            return 0
        head = self.chat_format.encode(system[:1], add_generation_prompt=False)
        return len(head) if ids[:len(head)] == head else 0

    def _stream(self, ids: List[int], keys: Optional[list] = None, own: Optional[list] = None,
                **kw) -> Iterator[str]:
        dec = StreamDecoder(self.tokenizer)
        t0 = time.time()
        n = 0
        self._prefill_seconds = 0.0
        reply = []
        self.last_ids = []
        try:
            for tid in self.generate_tokens(ids, prompt_keys=keys, prompt_own=own, **kw):
                n += 1
                self.last_ids.append(tid)
                s = dec.push(tid)
                if s:
                    reply.append(s)
                    yield s
            tail = dec.flush()
            if tail:
                reply.append(tail)
                yield tail
        except GeneratorExit:
            dbg.event("Generierung vom Aufrufer abgebrochen (Stop)")
            dbg.code("".join(reply), "text", "Teil-Antwort bis zum Stop")
            raise
        except Exception:
            dbg.exception("Generierung")
            raise
        else:
            dbg.code("".join(reply), "text", "Antwort")
            self._lager_umsortieren()                       # die Wahl dieser Antwort zählt für die nächste
        finally:   # läuft auch, wenn der Verbraucher früher aufhört zu iterieren
            total = time.time() - t0
            pre = self._prefill_seconds
            self.last_stats = dict(prompt_tokens=len(ids), generated=n, seconds=total,
                                   prefill_seconds=pre, decode_seconds=max(total - pre, 1e-9))
            st = self.last_stats
            st["vorgeschlagen"], st["angenommen"] = getattr(self, "_spek_stat", (0, 0))
            dbg.kv({"Prompt-Tokens": st["prompt_tokens"],
                    "Prefill": f"{pre:.2f}s ({st['prompt_tokens'] / pre if pre > 0 else 0:.0f} tok/s)",
                    "generiert": f"{n} Tokens in {st['decode_seconds']:.2f}s ({n / st['decode_seconds']:.1f} tok/s)",
                    "Position danach": self._pos,
                    "Vorschläge angenommen": f"{st['angenommen']} von {st['vorgeschlagen']}",
                    "VRAM": f"{torch.cuda.memory_allocated() / 2**30:.2f} GiB" if self.device.type == "cuda" else "-"},
                   "Antwort-Statistik")
