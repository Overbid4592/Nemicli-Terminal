"""
Byte-level BPE tokenizer (GPT-2 family: Qwen, Llama-3, ...) built purely
from the GGUF metadata.  No external dependencies, no `regex` module: the
pre-tokenisation pattern is implemented as a small hand-written scanner.

Security notes
* Special/control tokens are NEVER parsed out of user text.  They are only
  inserted by the chat template through `special()`.  A user cannot inject
  `<|im_start|>` and friends.
* Vocabulary sizes/ids come from the hardened GGUF reader, and every id is
  range-checked before use.
"""
from __future__ import annotations

import unicodedata
from typing import Dict, Iterable, List, Tuple

from .gguf import GGUFError, GGUFFile

BPE_CACHE_MAX = 200_000

TOKEN_NORMAL, TOKEN_UNKNOWN, TOKEN_CONTROL, TOKEN_USER_DEFINED, TOKEN_UNUSED, TOKEN_BYTE = 1, 2, 3, 4, 5, 6


# --- byte <-> unicode mapping (GPT-2) ---------------------------------------
def _bytes_to_unicode() -> Dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return {b: chr(c) for b, c in zip(bs, cs)}


BYTE_TO_UNI = _bytes_to_unicode()
UNI_TO_BYTE = {v: k for k, v in BYTE_TO_UNI.items()}


# --- pre-tokenisation scanners ---------------------------------------------
def _cat(ch: str) -> str:
    return unicodedata.category(ch)[0]


def _is_L(ch): return _cat(ch) == "L"
def _is_M(ch): return _cat(ch) == "M"
def _is_N(ch): return _cat(ch) == "N"
def _is_ws(ch): return ch.isspace()


_CONTRACTIONS = ("'s", "'t", "'re", "'ve", "'m", "'ll", "'d")


def _pretokenize_qwen(text: str, letters_with_marks: bool, digits: int = 1, joiners: bool = False,
                      punct_marks: bool = False) -> List[str]:
    """
    Implements (qwen35, letters_with_marks=True):
      (?i:'s|'t|'re|'ve|'m|'ll|'d)
      | [^\\r\\n\\p{L}\\p{N}]?[\\p{L}\\p{M}]+
      | \\p{N}
      |  ?[^\\s\\p{L}\\p{M}\\p{N}]+[\\r\\n]*
      | \\s*[\\r\\n]+
      | \\s+(?!\\S)
      | \\s+
    qwen2 (letters_with_marks=False) is the same without \\p{M}.
    k2-horizon: words also take U+200C/U+200D (`joiners`), numbers \\p{N}{1,3} (`digits`),
    and marks count as punctuation there (`punct_marks`).
    """
    n = len(text)
    out: List[str] = []
    i = 0

    def is_word(ch):
        return _is_L(ch) or (letters_with_marks and _is_M(ch)) or (joiners and ch in "\u200c\u200d")

    def is_punct(ch):
        return not (_is_ws(ch) or _is_L(ch) or _is_N(ch)
                    or (letters_with_marks and not punct_marks and _is_M(ch)))

    while i < n:
        ch = text[i]
        # 1. contractions
        if ch == "'":
            low = text[i:i + 3].lower()
            hit = None
            for c in _CONTRACTIONS:
                if low.startswith(c):
                    hit = c
                    break
            if hit:
                out.append(text[i:i + len(hit)])
                i += len(hit)
                continue
        # 2. [^\r\n L N]? [L M]+
        j = i
        if ch not in "\r\n" and not _is_L(ch) and not _is_N(ch):
            j = i + 1
        if j < n and is_word(text[j]):
            k = j + 1
            while k < n and is_word(text[k]):
                k += 1
            out.append(text[i:k])
            i = k
            continue
        if j != i and is_word(ch):   # backtrack: optional char not taken
            k = i + 1
            while k < n and is_word(text[k]):
                k += 1
            out.append(text[i:k])
            i = k
            continue
        # 3. number (1..`digits` digits)
        if _is_N(ch):
            k = i + 1
            while k < n and k - i < digits and _is_N(text[k]):
                k += 1
            out.append(text[i:k])
            i = k
            continue
        # 4.  ?[^\s L M N]+ [\r\n]*
        j = i + 1 if ch == " " else i
        if j < n and is_punct(text[j]):
            k = j + 1
            while k < n and is_punct(text[k]):
                k += 1
            while k < n and text[k] in "\r\n":
                k += 1
            out.append(text[i:k])
            i = k
            continue
        # whitespace alternatives 5-7
        if _is_ws(ch):
            k = i + 1
            while k < n and _is_ws(text[k]):
                k += 1
            run = text[i:k]
            last_nl = max(run.rfind("\r"), run.rfind("\n"))
            if last_nl >= 0:                       # 5. \s*[\r\n]+
                out.append(run[:last_nl + 1])
                i += last_nl + 1
                continue
            if k < n and len(run) > 1:             # 6. \s+(?!\S) -> leave one for the next token
                out.append(run[:-1])
                i = k - 1
                continue
            out.append(run)                        # 7. \s+
            i = k
            continue
        # fallback (should not happen): single char
        out.append(ch)
        i += 1
    return out


# --- regex pre-tokenisers (the other families) --------------------------------
# Patterns per pre-tokeniser type as in llama.cpp's llm_tokenizer_bpe.  They are fixed
# here in the code – nothing from the model file is ever compiled as a regex.  A list is
# applied in order: each pattern splits the pieces of the previous one further, text it
# does not match stays as its own piece (like tokenizers' Split(behavior="isolated")).
_KONTRAKTION = r"(?:'[sS]|'[tT]|'[rR][eE]|'[vV][eE]|'[mM]|'[lL][lL]|'[dD])"
_GPT2 = r"'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)"
_REGELN = {
    "llama3": [_KONTRAKTION + r"|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"],
    "gpt2": [_GPT2],
    "starcoder": [r"\p{N}", _GPT2],
    "falcon": [r"[\p{P}\$\+<=>\^~\|`]+", _GPT2, r"[0-9][0-9][0-9]"],
    "deepseek-coder": [r"[\r\n]", r"\s?\p{L}+", r"\s?\p{P}+", r"[一-龥ࠀ-一가-퟿]+", r"\p{N}"],
    "deepseek-v3": [r"\p{N}{1,3}", r"[一-龥぀-ゟ゠-ヿ]+",
                    r"[!" + '"' + r"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~][A-Za-z]+|[^\r\n\p{L}\p{P}\p{S}]?[\p{L}\p{M}]+"
                    r"| ?[\p{P}\p{S}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"],
    "tekken": [r"[^\r\n\p{L}\p{N}]?((?=[\p{L}])([^a-z]))*((?=[\p{L}])([^A-Z]))+|[^\r\n\p{L}\p{N}]?((?=[\p{L}])([^a-z]))+"
               r"((?=[\p{L}])([^A-Z]))*|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n/]*|\s*[\r\n]+|\s+(?!\S)|\s+"],
    "gpt4o": [r"[^\r\n\p{L}\p{N}]?((?=[\p{L}])([^a-z]))*((?=[\p{L}])([^A-Z]))+" + _KONTRAKTION + r"?|[^\r\n\p{L}\p{N}]?"
              r"((?=[\p{L}])([^a-z]))+((?=[\p{L}])([^A-Z]))*" + _KONTRAKTION + r"?|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n/]*"
              r"|\s*[\r\n]+|\s+(?!\S)|\s+"],
    "default": [r"[\p{P}\$\+<=>\^~\|]+", _GPT2, r"\p{N}+", r"[0-9][0-9][0-9]"],
    "bailingmoe": [r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]++[\r\n]*"
                   r"|\s*[\r\n]|\s+(?!\S)|\s+"],
}
_PRE_TYP = {
    **dict.fromkeys(("llama3", "llama-v3", "llama-bpe", "falcon3", "falcon-h1", "pixtral", "midm-2.0", "lfm2",
                     "glm4", "chatglm-bpe", "smaug-bpe", "dbrx"), "llama3"),
    **dict.fromkeys(("gpt-2", "phi-2", "mpt", "olmo", "jais", "trillion", "exaone4", "roberta-bpe"), "gpt2"),
    **dict.fromkeys(("starcoder", "refact", "command-r", "smollm", "codeshell", "exaone", "minerva-7b"), "starcoder"),
    "falcon": "falcon", "deepseek-coder": "deepseek-coder", "deepseek-v3": "deepseek-v3", "tekken": "tekken",
    **dict.fromkeys(("gpt-4o", "llama4"), "gpt4o"),
    **dict.fromkeys(("bailingmoe", "bailingmoe2", "llada-moe"), "bailingmoe"),
}
_QWEN_ARTIG = ("qwen2", "deepseek-r1-qwen", "megrez")        # eigener Scanner (_pretokenize_qwen)


def regeln_fuer(pre: str):
    """Kompilierte Regeln für `tokenizer.ggml.pre` oder None (eigener Scanner)."""
    if pre in _QWEN_ARTIG or pre in ("qwen35", "qwen3vl", "qwen3next", "k2-horizon"):
        return None
    try:
        import regex
    except ImportError:
        return None
    return [regex.compile(m) for m in _REGELN[_PRE_TYP.get(pre, "default")]]


def regex_split(text: str, muster) -> List[str]:
    teile = [text]
    for rx in muster:
        neu: List[str] = []
        for teil in teile:
            pos = 0
            for m in rx.finditer(teil):
                if m.start() > pos:
                    neu.append(teil[pos:m.start()])
                if m.end() > m.start():
                    neu.append(m.group())
                pos = m.end()
            if pos < len(teil):
                neu.append(teil[pos:])
        teile = neu
    return teile


class Tokenizer:
    def __init__(self, gg: GGUFFile):
        model = gg.get("tokenizer.ggml.model")
        if model not in ("gpt2", "gemma4"):
            raise GGUFError(f"tokenizer model {model!r} is not supported (gpt2 byte-level BPE or gemma4 SPM-BPE)")
        # gemma4: SentencePiece-style BPE on raw unicode characters with U+2581 for spaces,
        # no GPT-2 byte encoding, byte fallback through <0xXX> tokens (llama.cpp PRE_TYPE_GEMMA4)
        self.spm_style = model == "gemma4"
        self.pre = gg.get("tokenizer.ggml.pre", "gemma4" if self.spm_style else "default")
        tokens = gg.require("tokenizer.ggml.tokens")
        types = gg.get("tokenizer.ggml.token_type") or [TOKEN_NORMAL] * len(tokens)
        merges = gg.get("tokenizer.ggml.merges") or []
        if not all(isinstance(t, str) for t in tokens):
            raise GGUFError("tokenizer tokens must be strings")
        if len(types) != len(tokens):
            raise GGUFError("token_type length mismatch")
        self.tokens: List[str] = tokens
        self.types: List[int] = [int(t) for t in types]
        self.n_vocab = len(tokens)
        self.vocab: Dict[str, int] = {}
        for i, t in enumerate(tokens):
            self.vocab.setdefault(t, i)
        self.ranks: Dict[Tuple[str, str], int] = {}
        for r, m in enumerate(merges):
            if not isinstance(m, str):
                raise GGUFError("merges must be strings")
            cut = m.find(" ", 1)                      # split at the first space after position 0
            if cut < 0:
                raise GGUFError(f"malformed merge {m!r}")
            self.ranks[(m[:cut], m[cut + 1:])] = r
        self.special: Dict[str, int] = {
            t: i for i, t in enumerate(tokens) if self.types[i] in (TOKEN_CONTROL, TOKEN_USER_DEFINED)
        }

        def _id(key, default=None):
            v = gg.get(key, default)
            if v is None:
                return None
            if not isinstance(v, int) or not 0 <= v < self.n_vocab:
                raise GGUFError(f"{key} out of range")
            return v

        self.bos_id = _id("tokenizer.ggml.bos_token_id")
        self.eos_id = _id("tokenizer.ggml.eos_token_id")
        self.pad_id = _id("tokenizer.ggml.padding_token_id")
        self.add_bos = bool(gg.get("tokenizer.ggml.add_bos_token", False))
        # tokens that end a turn
        self.stop_ids = {i for i in (self.eos_id,) if i is not None}
        for name in ("<|im_end|>", "<|ifm|im_end|>", "<|endoftext|>", "<|eot_id|>", "<|end|>", "<|eot|>",
                     "<|END_OF_TURN_TOKEN|>", "[|endofturn|]", "<|end_of_text|>", "<end_of_turn>", "<turn|>", "<eos>",
                     "<|return|>", "<|call|>", "<|end_of_sentence|>", "<|user|>", "<|observation|>"):
            if name in self.special:
                self.stop_ids.add(self.special[name])
            elif name == "<eos>" and name in self.vocab:            # gemma4 stores <eos> as a normal token
                self.stop_ids.add(self.vocab[name])
        if "<|return|>" in self.special and "<|end|>" in self.special:
            self.stop_ids.discard(self.special["<|end|>"])      # Harmony: <|end|> closes the thinking channel
        self._bpe_cache: Dict[str, List[int]] = {}
        self._regeln = None                       # regex pre-tokeniser, compiled on first use

    # -- encoding ------------------------------------------------------------
    def _pretokenize(self, text: str) -> List[str]:
        if self.spm_style:
            # "[^\n]+|[\n]+" over text with spaces escaped to U+2581
            out, i, n = [], 0, len(text.replace(" ", "\u2581"))
            text = text.replace(" ", "\u2581")
            while i < n:
                j = i + 1
                nl = text[i] == "\n"
                while j < n and (text[j] == "\n") == nl:
                    j += 1
                out.append(text[i:j])
                i = j
            return out
        if self.pre in ("qwen35", "qwen3vl", "qwen3next"):
            return _pretokenize_qwen(text, letters_with_marks=True)
        if self.pre == "k2-horizon":
            return _pretokenize_qwen(unicodedata.normalize("NFC", text), letters_with_marks=True,
                                     digits=3, joiners=True, punct_marks=True)
        if self._regeln is None and self.pre not in _QWEN_ARTIG:
            self._regeln = regeln_fuer(self.pre) or False
        if self._regeln:
            return regex_split(text, self._regeln)
        # qwen2 family (and fallback without the regex package)
        return _pretokenize_qwen(text, letters_with_marks=False)

    def _bpe(self, piece: str) -> List[int]:
        cached = self._bpe_cache.get(piece)
        if cached is not None:
            return cached
        if self.spm_style:
            if piece[0] == "\n" and piece in self.vocab:          # pure newline runs: whole-token lookup
                return [self.vocab[piece]]
            word = list(piece)
        else:
            word = [BYTE_TO_UNI[b] for b in piece.encode("utf-8")]
        ranks = self.ranks
        while len(word) > 1:
            best = None
            best_rank = None
            for a, b in zip(word, word[1:]):
                r = ranks.get((a, b))
                if r is not None and (best_rank is None or r < best_rank):
                    best, best_rank = (a, b), r
            if best is None:
                break
            a, b = best
            merged = a + b
            new_word = []
            k = 0
            while k < len(word):
                if k < len(word) - 1 and word[k] == a and word[k + 1] == b:
                    new_word.append(merged)
                    k += 2
                else:
                    new_word.append(word[k])
                    k += 1
            word = new_word
        ids = []
        for sym in word:
            tid = self.vocab.get(sym)
            if tid is None:
                # fall back to single-byte tokens
                if self.spm_style:
                    for byte in sym.encode("utf-8"):
                        b = self.vocab.get(f"<0x{byte:02X}>")
                        if b is not None:
                            ids.append(b)
                    continue
                for c in sym:
                    b = self.vocab.get(c)
                    if b is None:
                        raise GGUFError(f"vocabulary lacks byte token for {c!r}")
                    ids.append(b)
            else:
                ids.append(tid)
        if len(self._bpe_cache) < 200_000:
            if len(self._bpe_cache) >= BPE_CACHE_MAX:     # long sessions with many web pages
                self._bpe_cache.clear()
            self._bpe_cache[piece] = ids
        return ids

    def encode(self, text: str) -> List[int]:
        """Encode plain text.  Special tokens in `text` are treated as ordinary text."""
        ids: List[int] = []
        for piece in self._pretokenize(text):
            ids.extend(self._bpe(piece))
        return ids

    def special_id(self, name: str) -> int:
        tid = self.special.get(name)
        if tid is None:
            raise GGUFError(f"model has no special token {name!r}")
        return tid

    # -- decoding ------------------------------------------------------------
    def token_bytes(self, tid: int) -> bytes:
        if not 0 <= tid < self.n_vocab:
            raise GGUFError(f"token id {tid} out of range")
        t = self.tokens[tid]
        if self.types[tid] in (TOKEN_CONTROL, TOKEN_USER_DEFINED):
            return t.encode("utf-8")
        if self.spm_style:
            if self.types[tid] == TOKEN_BYTE and len(t) == 6 and t.startswith("<0x") and t.endswith(">"):
                try:
                    return bytes([int(t[3:5], 16)])
                except ValueError:
                    return b""
            return t.replace("\u2581", " ").encode("utf-8")
        try:
            return bytes(UNI_TO_BYTE[c] for c in t)
        except KeyError:
            return t.encode("utf-8")

    def decode(self, ids: Iterable[int], skip_special: bool = True) -> str:
        buf = bytearray()
        for tid in ids:
            if skip_special and self.types[tid] == TOKEN_CONTROL:
                continue
            buf += self.token_bytes(tid)
        return buf.decode("utf-8", errors="replace")

    def is_control(self, tid: int) -> bool:
        return self.types[tid] == TOKEN_CONTROL


class StreamDecoder:
    """Incremental UTF-8 safe decoder for streaming output."""

    def __init__(self, tok: Tokenizer):
        self.tok = tok
        self.buf = bytearray()

    VISIBLE_CONTROL = ("<think>", "</think>", "<|channel>", "<channel|>", "[THINK]", "[/THINK]")

    def push(self, tid: int) -> str:
        if self.tok.is_control(tid):
            t = self.tok.tokens[tid]
            return t if t in self.VISIBLE_CONTROL else ""
        self.buf += self.tok.token_bytes(tid)
        # emit the longest valid UTF-8 prefix
        for cut in range(len(self.buf), max(-1, len(self.buf) - 4), -1):
            try:
                s = self.buf[:cut].decode("utf-8")
            except UnicodeDecodeError:
                continue
            del self.buf[:cut]
            return s
        if len(self.buf) > 3:            # undecodable prefix: never stall the stream
            s = self.buf[:1].decode("utf-8", errors="replace")
            del self.buf[:1]
            return s
        return ""

    def flush(self) -> str:
        s = self.buf.decode("utf-8", errors="replace")
        self.buf.clear()
        return s
