"""
Chat prompt formatting with templates that live in *this* code, not in the
model file.  llama-cpp-python's CVE-2024-34359 was an SSTI through the Jinja
template stored in GGUF metadata; we never evaluate that template.  The
metadata template is only *inspected* (substring checks) to pick a format.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .tokenizer import Tokenizer


@dataclass
class Message:
    role: str      # system | user | assistant
    content: str
    # assistant turns this engine generated: the exact token ids (after the generation
    # prompt, without the stop token).  Re-used verbatim so the next prompt starts with
    # exactly the tokens already in the KV cache (prefix cache).
    ids: Optional[List[int]] = None
    # user turns: image embeddings (one (n_tokens, n_embd) tensor per image, from
    # Engine.encode_image); rendered as begin mark + n placeholders + end mark
    images: Optional[list] = None
    # content as pieces (text, special): a special piece becomes its special token, every other
    # piece plain text – for the marks of the model's own tool format, which only the caller sets
    # (tool results, web pages and files stay plain text and can never turn into a mark)
    teile: Optional[List[Tuple[str, bool]]] = None
    # a tool result in the model's own format: Gemma 4 answers it inside its open turn, Ling reads it
    # as OBSERVATION, Mistral without [INST]
    werkzeug_antwort: bool = False
    # pieces right after the turn's closing mark (Mistral: [AVAILABLE_TOOLS] after the system prompt)
    danach: Optional[List[Tuple[str, bool]]] = None


IMAGE_MARKS = {                                   # begin, placeholder, end
    "gemma4": ("<|image>", "<|image|>", "<image|>"),
    "qwen": ("<|vision_start|>", "<|image_pad|>", "<|vision_end|>"),
    "pixtral": ("[IMG_BREAK]", "[IMG]", "[IMG_END]"),   # Zeilenende, Platzhalter, Bildende
}
IMAGE_BEGIN, IMAGE_TOKEN, IMAGE_END = IMAGE_MARKS["gemma4"]
ZEILEN_MARKEN = IMAGE_MARKS["pixtral"]            # Bild zeilenweise, braucht `grid` (Zeilen, Spalten)


def detect_image_marks(tok: Tokenizer) -> Optional[tuple]:
    """The image marks the tokenizer knows (None: no image tokens)."""
    for marks in IMAGE_MARKS.values():
        if all(m in tok.special for m in marks):
            return marks
    return None


def detect_format(template: Optional[str], arch: str) -> str:
    """Chat format from substrings of the template (never executed) and the architecture; the rules
    live in the model database (daten/chatformate.json)."""
    from . import ausfuellen
    return ausfuellen.chatformat(template or "", arch)


# Formats that are only "role header + content + end mark" (llama.cpp llm_chat_apply_template).
# Pieces: a string that is a special token of the model is inserted as that token, everything
# else is encoded as text; "{role}" is the role name.  strip = content is trimmed.
def _rollen_formate() -> dict:
    """Pieces per format (daten/chatformate.json, 'rollen_formate'): pre/post/gen, optional strip,
    namen (role names), post per role."""
    from . import ausfuellen
    return ausfuellen.rollen_formate()


# gpt-oss: identity line of the model's own template; end of the thinking and start of the
# final answer as the detokenised stream shows them
HARMONY_IDENTITAET = "You are ChatGPT, a large language model trained by OpenAI."
HARMONY_FINAL = "<|end|><|start|>assistant<|channel|>final<|message|>"
# SmolLM3: "Today Date" as strftime("%d %B %Y") in the C locale
_MONATE_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December")


class ChatFormatter:
    def __init__(self, tok: Tokenizer, fmt: str, thinking: bool = False, template: Optional[str] = None):
        self.tok = tok
        self.fmt = fmt
        self.thinking = thinking
        self.image_marks = detect_image_marks(tok)
        # ChatML with a think block only when the model's template uses one: Qwen3-VL Instruct
        # shares the Qwen3 vocabulary (<think> included) but was never trained on the block
        self.chatml_denkt = "<think>" in tok.special and (template is None or "<think>" in template)

    def _image_ids(self, images) -> List[int]:
        if not self.image_marks:
            raise ValueError("this model has no image tokens")
        b, p, e = (self.tok.special_id(m) for m in self.image_marks)
        ids: List[int] = []
        for img in images:
            if tuple(self.image_marks) == ZEILEN_MARKEN:      # Pixtral: [IMG]×Spalten + [IMG_BREAK] je Zeile
                rows, cols = img.grid
                if rows * cols != int(img.shape[0]):
                    raise ValueError("image grid does not match its embeddings")
                for r in range(rows):
                    ids += [p] * cols + [b if r < rows - 1 else e]
            else:
                ids += [b] + [p] * int(img.shape[0]) + [e]
        return ids

    @property
    def opens_thinking(self) -> bool:
        """Does the generation prompt open a thinking block (the reply starts inside it)?"""
        if not self.thinking:
            return False
        return self.fmt in ("k2", "bailing", "granite4", "harmony") or             (self.fmt == "chatml" and self.chatml_denkt)

    @property
    def drops_old_thinking(self) -> bool:
        """Does the model's own template leave the thinking of earlier answers out?  Then
        such answers are passed as text (Message.ids = None) and rendered without it.
        Ling keeps it (preserved_thinking)."""
        return self.fmt in ("chatml", "granite4", "gemma4", "deepseek", "harmony")

    def denk_marken(self):
        """(open, close) token ids of the thinking block in the generated text, or None.
        `close` is inserted when the thinking budget runs out; its first token also marks
        the model's own end of thinking."""
        if self.fmt == "harmony":
            return (self._stuecke(("<|channel|>", "analysis", "<|message|>")),
                    self._stuecke(("<|end|>", "<|start|>", "assistant", "<|channel|>", "final", "<|message|>")))
        if self.fmt == "gemma4" and "<|channel>" in self.tok.special:
            return self._stuecke(("<|channel>",)), self._stuecke(("<channel|>",))
        if self.fmt == "k2":
            return self._stuecke(("<ifm|think>",)), self._stuecke(("</ifm|think>", "\n"))
        if self.fmt.startswith("mistral") and "[THINK]" in self.tok.special and "[/THINK]" in self.tok.special:
            return self._stuecke(("[THINK]",)), self._stuecke(("[/THINK]",))      # Magistral / Ministral 3 Reasoning
        if self.fmt == "seed_oss" and "<seed:think>" in self.tok.special:
            return self._stuecke(("<seed:think>",)), self._stuecke(("</seed:think>",))
        if "<think>" in self.tok.special and "</think>" in self.tok.special:
            return self._stuecke(("<think>",)), self._stuecke(("</think>", "\n\n"))
        return None

    # Each formatter returns token ids; special tokens are inserted by id and
    # user-supplied text goes through the plain encoder (no special parsing).
    def encode(self, messages: List[Message], add_generation_prompt: bool = True) -> List[int]:
        if self.fmt in _rollen_formate():
            return self._rollen(messages, add_generation_prompt)
        if self.fmt.startswith("mistral") or self.fmt == "llama2":
            return self._mistral_art(messages, add_generation_prompt)
        fn = getattr(self, f"_{self.fmt}")
        return fn(messages, add_generation_prompt)

    # -- pieces: special token if the model has it, text otherwise ---------------------
    def _stuecke(self, stuecke, role: str = "") -> List[int]:
        tok = self.tok
        ids: List[int] = []
        text = ""
        for s in stuecke:
            s = s.replace("{role}", role).replace("{ROLE}", role.upper())
            if s in tok.special:
                if text:
                    ids += tok.encode(text)
                    text = ""
                ids.append(tok.special[s])
            else:
                text += s
        if text:
            ids += tok.encode(text)
        return ids

    def _inhalt(self, m: Message, text: Optional[str] = None, strip: bool = True) -> List[int]:
        """Content ids: from `m.teile` (special pieces as special tokens, the rest plain), otherwise
        `text` (default: the content) through the plain encoder."""
        if not m.teile:
            t = m.content if text is None else text
            return self.tok.encode(t.strip() if strip else t)
        teile = list(m.teile)
        if strip:                         # only the ends of the message; text next to a marker stays
            if not teile[0][1]:
                teile[0] = (teile[0][0].lstrip(), False)
            if not teile[-1][1]:
                teile[-1] = (teile[-1][0].rstrip(), False)
        ids: List[int] = []
        for t, besonders in teile:
            if besonders and t in self.tok.special:
                ids.append(self.tok.special[t])
            elif t:
                ids += self.tok.encode(t)
        return ids

    def _bos(self) -> List[int]:
        tok = self.tok
        return [tok.bos_id] if getattr(tok, "add_bos", False) and tok.bos_id is not None else []

    def _rollen(self, messages, gen):
        f = _rollen_formate()[self.fmt]
        ids = self._bos()
        for m in messages:
            if m.role not in ("system", "user", "assistant"):
                raise ValueError(f"unsupported role {m.role!r}")
            role = f.get("namen", {}).get(m.role, m.role)
            post = f["post"][m.role] if isinstance(f["post"], dict) else f["post"]
            ids += self._stuecke(f["pre"], role)
            if m.role == "assistant" and m.ids is not None:
                ids += list(m.ids)
            else:
                content = m.content
                for ende in ("</think>", "</seed:think>"):
                    if m.role == "assistant" and ende in content:
                        content = content.split(ende)[-1].lstrip("\n")
                ids += self.tok.encode(content.strip() if f.get("strip") else content)
            ids += self._stuecke(post, role)
        if gen:
            ids += self._stuecke(f["gen"])
        return ids

    def _mistral_art(self, messages, gen):
        """Mistral v1/v3/v3-tekken/v7/v7-tekken and Llama 2 as in llama.cpp; [INST] etc. are
        special tokens when the model has them."""
        fmt, tok = self.fmt, self.tok
        eos = self._stuecke(("</s>",))
        ids = self._bos()

        def inhalt(m):
            if m.role == "assistant" and m.ids is not None:
                return list(m.ids)
            bilder = self._image_ids(m.images) if (m.role == "user" and m.images) else []
            return bilder + self._inhalt(m)                               # Bilder vor dem Text
        if fmt.startswith("mistral-v7"):
            sp = " " if fmt == "mistral-v7" else ""
            for m in messages:
                if m.role == "system":
                    ids += self._stuecke(("[SYSTEM_PROMPT]", sp)) + inhalt(m) + self._stuecke(("[/SYSTEM_PROMPT]",))
                    if m.danach:
                        ids += self._inhalt(Message("system", "", teile=m.danach), strip=False)
                elif m.role == "user" and m.werkzeug_antwort:              # [TOOL_RESULTS] ohne [INST]
                    ids += inhalt(m)
                elif m.role == "user":
                    ids += self._stuecke(("[INST]", sp)) + inhalt(m) + self._stuecke(("[/INST]",))
                else:
                    ids += (self._stuecke((sp,)) if sp else []) + inhalt(m) + eos
            return ids
        if fmt == "llama2":
            drin = True
            ids += self._stuecke(("[INST]", " "))
            for m in messages:
                if not drin:
                    drin = True
                    ids += self._stuecke(("[INST]", " "))
                if m.role == "system":
                    ids += self._stuecke(("<<SYS>>\n",)) + inhalt(m) + self._stuecke(("\n<</SYS>>\n\n",))
                elif m.role == "user":
                    ids += inhalt(m) + self._stuecke((" ", "[/INST]"))
                else:
                    ids += inhalt(m) + eos
                    drin = False
            return ids
        vorne = " " if fmt == "mistral-v1" else ""
        hinten = "" if fmt == "mistral-v3-tekken" else " "
        drin = False
        for m in messages:
            if not drin:
                ids += self._stuecke(tuple(s for s in (vorne, "[INST]", hinten) if s))
                drin = True
            if m.role == "system":
                ids += inhalt(m) + tok.encode("\n\n")
            elif m.role == "user":
                ids += inhalt(m) + self._stuecke(tuple(s for s in (vorne, "[/INST]") if s))
            else:
                ids += (self._stuecke((hinten,)) if hinten else []) + inhalt(m) + eos
                drin = False
        return ids

    def _bailing(self, messages, gen):
        """Ling / Bailing V3: <role>NAME</role> ... <|role_end|>; the first system turn ends with
        "detailed thinking on|off", earlier answers carry an empty think block."""
        denken = "detailed thinking " + ("on" if self.thinking else "off")
        oeffnen = ("\n", "<think>") if self.thinking else ("\n", "<think>", "</think>")
        ende = self._stuecke(("<|role_end|>",))
        ids: List[int] = []
        rest = list(messages)
        if rest and rest[0].role == "system":
            if rest[0].teile:
                ids += self._stuecke(("<role>", "SYSTEM", "</role>")) + self._inhalt(rest[0], strip=False)
                ids += self.tok.encode("\n" + denken)
            else:
                ids += self._stuecke(("<role>", "SYSTEM", "</role>")) + self.tok.encode(rest[0].content + "\n" + denken)
            ids += ende
            rest = rest[1:]
        else:
            ids += self._stuecke(("<role>", "SYSTEM", "</role>")) + self.tok.encode(denken) + ende
        namen = {"system": "SYSTEM", "user": "HUMAN", "assistant": "ASSISTANT"}
        for m in rest:
            if m.role not in namen:
                raise ValueError(f"unsupported role {m.role!r}")
            rolle = "OBSERVATION" if m.werkzeug_antwort else namen[m.role]
            ids += self._stuecke(("<role>", rolle, "</role>"))
            if m.role == "assistant":
                if m.ids is not None:
                    ids += self._stuecke(oeffnen) + list(m.ids)
                else:
                    inhalt = m.content.split("</think>")[-1].lstrip("\n") if "</think>" in m.content else m.content
                    ids += self._stuecke(("\n", "<think>", "</think>")) + self._inhalt(m, inhalt, strip=False)
            else:
                ids += self._inhalt(m, strip=False)
            ids += ende
        if gen:
            ids += self._stuecke(("<role>", "ASSISTANT", "</role>") + oeffnen)
        return ids

    def _glm4(self, messages, gen):
        """GLM-4 / GLM-4.5 as llama.cpp's chatglm4: [gMASK]<sop>, then "<|role|>" + newline +
        content per message, the reply after "<|assistant|>" + newline. Tool results as in the
        GLM-4.5 template: "<|observation|>", the pieces start with their own newline."""
        ids = self._stuecke(("[gMASK]", "<sop>"))
        for m in messages:
            if m.role not in ("system", "user", "assistant"):
                raise ValueError(f"unsupported role {m.role!r}")
            if m.werkzeug_antwort:
                ids += self._stuecke(("<|observation|>",))
            else:
                ids += self._stuecke((f"<|{m.role}|>", "\n"))
            if m.role == "assistant" and m.ids is not None:
                ids += list(m.ids)
            else:
                inhalt = m.content.split("</think>")[-1].lstrip("\n") if "</think>" in m.content else m.content
                ids += self._inhalt(m, inhalt, strip=False)
        if gen:
            ids += self._stuecke(("<|assistant|>", "\n"))
        return ids

    def _ernie4_5(self, messages, gen):
        """ERNIE 4.5: <|begin_of_sentence|>, system text + newline, "User: ...\n",
        "Assistant: ...<|end_of_sentence|>", the reply after "Assistant: "."""
        tok = self.tok
        ids = self._stuecke(("<|begin_of_sentence|>",))
        ende = self._stuecke(("<|end_of_sentence|>",))
        for m in messages:
            if m.role == "system":
                ids += tok.encode(m.content + "\n")
            elif m.role == "user":
                ids += tok.encode("User: " + m.content + "\n")
            elif m.role == "assistant":
                ids += tok.encode("Assistant: ") + (list(m.ids) if m.ids is not None else tok.encode(m.content)) + ende
            else:
                raise ValueError(f"unsupported role {m.role!r}")
        if gen:
            ids += tok.encode("Assistant: ")
        return ids

    def _harmony(self, messages, gen):
        """gpt-oss (Harmony): system turn with date, reasoning level and channels as in the
        model's template; our system prompt becomes the developer instructions.  Answers live
        in channels: thinking opens "analysis", otherwise the reply starts directly in "final";
        earlier answers are rendered as "final" only."""
        import datetime
        tok = self.tok

        def kopf(rolle, kanal=None):
            teile = ("<|start|>", rolle) + (("<|channel|>", kanal) if kanal else ()) + ("<|message|>",)
            return self._stuecke(teile)
        ende = self._stuecke(("<|end|>",))
        system = (f"{HARMONY_IDENTITAET}\nKnowledge cutoff: 2024-06\n"
                  f"Current date: {datetime.date.today().isoformat()}\n\n"
                  f"Reasoning: {'medium' if self.thinking else 'low'}\n\n"
                  "# Valid channels: analysis, commentary, final. Channel must be included for every message.")
        ids = kopf("system") + tok.encode(system) + ende
        anweisung = [m for m in messages if m.role == "system"]
        if anweisung:
            ids += kopf("developer") + tok.encode("# Instructions\n\n" + anweisung[0].content.strip()) + ende
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "user":
                ids += kopf("user") + tok.encode(m.content) + ende
            elif m.role == "assistant" and m.ids is not None:      # mit Denkkanal, wie erzeugt
                ids += kopf("assistant", "analysis" if self.thinking else "final") + list(m.ids) + ende
            elif m.role == "assistant":
                inhalt = m.content.split(HARMONY_FINAL)[-1] if HARMONY_FINAL in m.content else m.content
                ids += kopf("assistant", "final") + tok.encode(inhalt.strip()) + ende
            else:
                raise ValueError(f"unsupported role {m.role!r}")
        if gen:
            ids += kopf("assistant", "analysis" if self.thinking else "final")
        return ids

    def _granite4(self, messages, gen):
        """Granite 4.x: ChatML with "<think></think>" (no line breaks) for answers without
        thinking; earlier answers carry an empty think block (truncate_history_thinking)."""
        tok = self.tok
        s, e = tok.special_id("<|im_start|>"), tok.special_id("<|im_end|>")
        nl = tok.encode("\n")
        oeffnen = self._stuecke(("<think>", "\n") if self.thinking else ("<think>", "</think>"))
        leer = self._stuecke(("<think>", "</think>"))
        ids: List[int] = []
        for m in messages:
            if m.role not in ("system", "user", "assistant"):
                raise ValueError(f"unsupported role {m.role!r}")
            ids += [s] + tok.encode(m.role + "\n")
            if m.role == "assistant":
                if m.ids is not None:
                    ids += oeffnen + list(m.ids) + [e] + nl
                    continue
                content = m.content.split("</think>")[-1] if "</think>" in m.content else m.content
                ids += leer + self._inhalt(m, content) + [e] + nl
                continue
            ids += self._inhalt(m, strip=False) + [e] + nl
        if gen:
            ids += [s] + tok.encode("assistant\n") + oeffnen
        return ids

    def _chatml(self, messages, gen):
        tok = self.tok
        s, e = tok.special_id("<|im_start|>"), tok.special_id("<|im_end|>")
        nl = tok.encode("\n")
        ids: List[int] = []
        for m in messages:
            if m.role not in ("system", "user", "assistant"):
                raise ValueError(f"unsupported role {m.role!r}")
            ids += [s] + tok.encode(m.role + "\n")
            if m.role == "assistant" and m.ids is not None:
                ids += self._chatml_think(nl) + list(m.ids) + [e] + nl
                continue
            content = m.content.strip()
            if m.role == "assistant" and "</think>" in content:
                content = content.split("</think>")[-1].lstrip("\n")
            if m.role == "user" and m.images:
                ids += self._image_ids(m.images)
            # text after </think> keeps leading spaces (as before); pieces: only the message ends
            ids += self._inhalt(m, content, strip=bool(m.teile)) + [e] + nl
        if gen:
            ids += [s] + tok.encode("assistant\n") + self._chatml_think(nl)
        return ids

    def _chatml_think(self, nl):
        tok = self.tok
        if not self.chatml_denkt:
            return []
        if self.thinking:
            return [tok.special_id("<think>")] + nl
        # "\n\n" as the tokenizer splits it (one token for Qwen) – two single newlines
        # read to the model like an unclosed think block
        nl2 = tok.encode("\n\n")
        return [tok.special_id("<think>")] + nl2 + [tok.special_id("</think>")] + nl2

    def _smollm3(self, messages, gen):
        # System part with a metadata header; "/think" or "/no_think" in the system text picks the
        # mode.  Without a tool list the model's template ends the system part WITHOUT <|im_end|>.
        # Answers stay as generated (thinking included); in /no_think mode an empty block leads them.
        tok = self.tok
        s, e = tok.special_id("<|im_start|>"), tok.special_id("<|im_end|>")
        nl = tok.encode("\n")
        denken = self.thinking
        system = ""
        if messages and messages[0].role == "system":
            system = messages[0].content
            if "/no_think" in system:
                denken = False
            elif "/think" in system:
                denken = True
            system = system.replace("/no_think", "").replace("/think", "").rstrip()
            messages = messages[1:]
        leer = self._stuecke(("<think>", "\n\n", "</think>", "\n")) if not denken else []
        t = time.localtime()
        heute = f"{t.tm_mday:02d} {_MONATE_EN[t.tm_mon - 1]} {t.tm_year}"
        kopf = ("## Metadata\n\nKnowledge Cutoff Date: June 2025\n"
                f"Today Date: {heute}\nReasoning Mode: {'/think' if denken else '/no_think'}\n\n"
                "## Custom Instructions\n\n"
                + (system or "You are a helpful AI assistant named SmolLM, trained by Hugging Face.") + "\n\n")
        ids: List[int] = [s] + tok.encode("system\n" + kopf)
        for m in messages:
            if m.role in ("user", "tool"):
                ids += [s] + tok.encode("user\n")
                if m.images:
                    ids += self._image_ids(m.images)
                ids += tok.encode(m.content) + [e] + nl
            elif m.role == "assistant":
                ids += [s] + tok.encode("assistant\n") + leer
                ids += (list(m.ids) if m.ids is not None else tok.encode(m.content.lstrip("\n"))) + [e] + nl
            else:
                raise ValueError(f"unsupported role {m.role!r}")
        if gen:
            ids += [s] + tok.encode("assistant\n") + leer
        return ids

    def _k2(self, messages, gen):
        # <bos><|ifm|im_start|>system\n{..}<|ifm|im_end|><|ifm|im_start|>user\n{..}<|ifm|im_end|>
        # <|ifm|im_start|>assistant\n<ifm|think>\n[</ifm|think>\n]{..}<|ifm|im_end|>  (no newline between turns)
        tok = self.tok
        s, e = tok.special_id("<|ifm|im_start|>"), tok.special_id("<|ifm|im_end|>")
        t_auf, t_zu = tok.special_id("<ifm|think>"), tok.special_id("</ifm|think>")
        nl = tok.encode("\n")
        ids = [tok.bos_id] if tok.bos_id is not None else []
        for m in messages:
            if m.role in ("system", "user"):
                ids += [s] + tok.encode(f"{m.role}\n{m.content}") + [e]
            elif m.role == "assistant":
                ids += [s] + tok.encode("assistant\n")
                if m.ids is not None:
                    ids += [t_auf] + nl + ([] if self.thinking else [t_zu] + nl) + list(m.ids) + [e]
                    continue
                content = m.content.split("</ifm|think>")[-1].lstrip("\n")
                ids += [t_auf] + nl + [t_zu] + tok.encode("\n" + content) + [e]
            else:
                raise ValueError(f"unsupported role {m.role!r}")
        if gen:
            ids += [s] + tok.encode("assistant\n") + [t_auf] + nl
            if not self.thinking:
                ids += [t_zu] + nl
        return ids

    def _deepseek(self, messages, gen):
        # <bos>{system}<｜User｜>{..}<｜Assistant｜>{answer}<｜end▁of▁sentence｜>...<｜Assistant｜>
        # The system text follows <bos> without a separator; earlier answers lose their
        # <think> part, as in the template.
        tok = self.tok
        u, a = tok.special_id("<｜User｜>"), tok.special_id("<｜Assistant｜>")
        eos = tok.special_id("<｜end▁of▁sentence｜>")
        think_end = tok.special.get("</think>")
        ids = [tok.bos_id] if tok.bos_id is not None else []
        system = [m for m in messages if m.role == "system"]
        if system:
            ids += tok.encode(system[0].content)
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "user":
                ids += [u] + tok.encode(m.content)
                continue
            if m.role != "assistant":
                raise ValueError(f"unsupported role {m.role!r}")
            if m.ids is not None:
                body = list(m.ids)
                if think_end is not None and think_end in body:
                    body = body[len(body) - body[::-1].index(think_end):]
            else:
                body = tok.encode(m.content.split("</think>")[-1])
            ids += [a] + body + [eos]
        if gen:
            ids.append(a)
        return ids

    def _llama3(self, messages, gen):
        tok = self.tok
        bot, sh, eh, eot = (tok.special_id(n) for n in
                            ("<|begin_of_text|>", "<|start_header_id|>", "<|end_header_id|>", "<|eot_id|>"))
        ids = [bot]
        for m in messages:
            if m.role == "assistant" and m.ids is not None:
                ids += [sh] + tok.encode(m.role) + [eh] + tok.encode("\n\n") + list(m.ids) + [eot]
                continue
            ids += [sh] + tok.encode(m.role) + [eh] + tok.encode("\n\n" + m.content.strip()) + [eot]
        if gen:
            ids += [sh] + tok.encode("assistant") + [eh] + tok.encode("\n\n")
        return ids

    def _gemma4(self, messages, gen):
        # <bos>[<|turn>system\n[<|think|>\n]{system}<turn|>\n]<|turn>user\n{..}<turn|>\n<|turn>model\n
        # Tool results in Gemma's own format (Message.werkzeug_antwort) stay inside the open model
        # turn right after its call, and the model goes on in that turn (as in its template).
        tok = self.tok
        sot, eot = tok.special_id("<|turn>"), tok.special_id("<turn|>")
        nl = tok.encode("\n")
        ids = [tok.bos_id] if tok.bos_id is not None else []
        system = [m for m in messages if m.role == "system"]
        if system or self.thinking:
            ids += [sot] + tok.encode("system\n")
            if self.thinking and "<|think|>" in tok.special:
                ids += [tok.special_id("<|think|>")] + nl
            if system:
                ids += self._inhalt(system[0])
            ids += [eot] + nl
        rest = [m for m in messages if m.role != "system"]
        offen = False                                    # model turn still open (call, result, …)
        for i, m in enumerate(rest):
            danach_antwort = i + 1 < len(rest) and rest[i + 1].werkzeug_antwort
            if offen and m.werkzeug_antwort:
                ids += (self._image_ids(m.images) if m.images else []) + self._inhalt(m)
                continue
            role = "model" if m.role == "assistant" else "user"
            if offen and role == "user":
                ids += [eot] + nl
                offen = False
            if role == "model":
                kopf = [] if offen else [sot] + tok.encode("model\n")
                if m.ids is not None:
                    ids += kopf + list(m.ids)
                else:
                    content = m.content.strip()
                    if "<channel|>" in content:                      # drop previous thinking
                        content = content.split("<channel|>")[-1].strip()
                    if m.teile or offen:
                        ids += kopf + self._inhalt(m, content)
                    else:                                            # one piece, as before
                        ids += [sot] + tok.encode("model\n" + content)
                offen = danach_antwort
                if not offen:
                    ids += [eot] + nl
                continue
            if m.images:
                ids += [sot] + tok.encode("user\n") + self._image_ids(m.images)
                ids += self._inhalt(m) + [eot] + nl
                continue
            if m.teile:
                ids += [sot] + tok.encode("user\n") + self._inhalt(m) + [eot] + nl
            else:
                ids += [sot] + tok.encode("user\n" + m.content.strip()) + [eot] + nl
        if gen and not offen:
            ids += [sot] + tok.encode("model\n")
        return ids

    def _gemma(self, messages, gen):
        tok = self.tok
        sot, eot = tok.special_id("<start_of_turn>"), tok.special_id("<end_of_turn>")
        ids = [tok.bos_id] if tok.bos_id is not None else []
        system = ""
        for m in messages:
            if m.role == "system":
                system = m.content.strip() + "\n\n"
                continue
            role = "model" if m.role == "assistant" else "user"
            if role == "model" and m.ids is not None:
                ids += [sot] + tok.encode("model\n") + list(m.ids) + [eot] + tok.encode("\n")
                continue
            text = (system + m.content.strip()) if role == "user" and system else m.content.strip()
            system = ""
            ids += [sot] + tok.encode(role + "\n" + text) + [eot] + tok.encode("\n")
        if gen:
            ids += [sot] + tok.encode("model\n")
        return ids
