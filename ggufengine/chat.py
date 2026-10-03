"""
Chat prompt formatting with templates that live in *this* code, not in the
model file.  llama-cpp-python's CVE-2024-34359 was an SSTI through the Jinja
template stored in GGUF metadata; we never evaluate that template.  The
metadata template is only *inspected* (substring checks) to pick a format.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

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


IMAGE_MARKS = {                                   # begin, placeholder, end
    "gemma4": ("<|image>", "<|image|>", "<image|>"),
    "qwen": ("<|vision_start|>", "<|image_pad|>", "<|vision_end|>"),
}
IMAGE_BEGIN, IMAGE_TOKEN, IMAGE_END = IMAGE_MARKS["gemma4"]


def detect_image_marks(tok: Tokenizer) -> Optional[tuple]:
    """The image marks the tokenizer knows (None: no image tokens)."""
    for marks in IMAGE_MARKS.values():
        if all(m in tok.special for m in marks):
            return marks
    return None


def detect_format(template: Optional[str], arch: str) -> str:
    """Chat format from substrings of the template (never executed) and the architecture."""
    t = template or ""
    if "<|ifm|im_start|>" in t or arch == "k2-horizon":
        return "k2"
    if "<|turn>" in t or arch == "gemma4":
        return "gemma4"
    if ("<|start|>" in t and "<|channel|>" in t) or arch == "gpt-oss":
        return "harmony"
    if "[gMASK]" in t or arch in ("glm4", "glm4moe"):            # GLM-4 / GLM-4.5
        return "glm4"
    if "<|begin_of_sentence|>" in t and "Assistant: " in t:     # ERNIE 4.5
        return "ernie4_5"
    if "<｜User｜>" in t:                        # DeepSeek-R1 (and its distills)
        return "deepseek"
    if "<|im_start|>" in t:
        if "<|im_sep|>" in t:
            return "phi4"
        return "granite4" if "truncate_history_thinking" in t else "chatml"
    if "<role>HUMAN</role>" in t or arch == "bailingmoe3":
        return "bailing"
    if "[INST]" in t:
        if "[SYSTEM_PROMPT]" in t:
            return "mistral-v7"
        if "' [INST] ' + system_message" in t or "[AVAILABLE_TOOLS]" in t:
            if " [INST]" in t:
                return "mistral-v1"
            return "mistral-v3-tekken" if '"[INST]"' in t else "mistral-v3"
        return "llama2" if "<<SYS>>" in t else "mistral-v1"
    if "<|start_header_id|>" in t:
        return "llama3"
    if "<|header_start|>" in t and "<|header_end|>" in t:
        return "llama4"
    if "<|START_OF_TURN_TOKEN|>" in t and "<|USER_TOKEN|>" in t:
        return "command-r"
    if "<|start_of_role|>" in t:
        return "granite"
    if "[|system|]" in t and "[|assistant|]" in t and "[|endofturn|]" in t:
        return "exaone3"
    if "<|assistant|>" in t and "<|end|>" in t:
        return "phi3"
    if "<|assistant|>" in t and "<|user|>" in t and "</s>" in t:
        return "falcon3"
    if "<|user|>" in t and "<|endoftext|>" in t:
        return "zephyr"
    if "<start_of_turn>" in t:
        return "gemma"
    by_arch = {"phi3": "phi3", "granite": "granite", "granitemoe": "granite", "llama": "llama3",
               "gemma": "gemma", "gemma2": "gemma", "gemma3": "gemma"}
    if arch.startswith("qwen"):
        return "chatml"
    return by_arch.get(arch, "chatml")


# Formats that are only "role header + content + end mark" (llama.cpp llm_chat_apply_template).
# Pieces: a string that is a special token of the model is inserted as that token, everything
# else is encoded as text; "{role}" is the role name.  strip = content is trimmed.
_ROLLEN_FORMATE = {
    "phi3": dict(pre=("<|{role}|>", "\n"), post=("<|end|>", "\n"), gen=("<|assistant|>", "\n")),
    "phi4": dict(pre=("<|im_start|>", "{role}", "<|im_sep|>"), post=("<|im_end|>",),
                 gen=("<|im_start|>", "assistant", "<|im_sep|>")),
    "falcon3": dict(pre=("<|{role}|>", "\n"), post=("\n",), gen=("<|assistant|>", "\n")),
    "zephyr": dict(pre=("<|{role}|>", "\n"), post=("<|endoftext|>", "\n"), gen=("<|assistant|>", "\n")),
    "granite": dict(pre=("<|start_of_role|>", "{role}", "<|end_of_role|>"), post=("<|end_of_text|>", "\n"),
                    gen=("<|start_of_role|>", "assistant", "<|end_of_role|>")),
    "llama4": dict(pre=("<|header_start|>", "{role}", "<|header_end|>", "\n\n"), post=("<|eot|>",),
                   gen=("<|header_start|>", "assistant", "<|header_end|>", "\n\n"), strip=True),
    "command-r": dict(pre=("<|START_OF_TURN_TOKEN|>", "<|{ROLE}_TOKEN|>"), post=("<|END_OF_TURN_TOKEN|>",),
                      gen=("<|START_OF_TURN_TOKEN|>", "<|CHATBOT_TOKEN|>"), strip=True,
                      namen={"assistant": "CHATBOT"}),
    "exaone3": dict(pre=("[|{role}|]",), post={"system": ("[|endofturn|]", "\n"), "user": ("\n",),
                                              "assistant": ("[|endofturn|]", "\n")},
                    gen=("[|assistant|]",), strip=True),
}


# gpt-oss: identity line of the model's own template; end of the thinking and start of the
# final answer as the detokenised stream shows them
HARMONY_IDENTITAET = "You are ChatGPT, a large language model trained by OpenAI."
HARMONY_FINAL = "<|end|><|start|>assistant<|channel|>final<|message|>"


class ChatFormatter:
    def __init__(self, tok: Tokenizer, fmt: str, thinking: bool = False):
        self.tok = tok
        self.fmt = fmt
        self.thinking = thinking
        self.image_marks = detect_image_marks(tok)

    def _image_ids(self, images) -> List[int]:
        if not self.image_marks:
            raise ValueError("this model has no image tokens")
        b, p, e = (self.tok.special_id(m) for m in self.image_marks)
        ids: List[int] = []
        for img in images:
            ids += [b] + [p] * int(img.shape[0]) + [e]
        return ids

    @property
    def opens_thinking(self) -> bool:
        """Does the generation prompt open a thinking block (the reply starts inside it)?"""
        if not self.thinking:
            return False
        return self.fmt in ("k2", "bailing", "granite4", "harmony") or             (self.fmt == "chatml" and "<think>" in self.tok.special)

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
        if "<think>" in self.tok.special and "</think>" in self.tok.special:
            return self._stuecke(("<think>",)), self._stuecke(("</think>", "\n\n"))
        return None

    # Each formatter returns token ids; special tokens are inserted by id and
    # user-supplied text goes through the plain encoder (no special parsing).
    def encode(self, messages: List[Message], add_generation_prompt: bool = True) -> List[int]:
        if self.fmt in _ROLLEN_FORMATE:
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

    def _bos(self) -> List[int]:
        tok = self.tok
        return [tok.bos_id] if getattr(tok, "add_bos", False) and tok.bos_id is not None else []

    def _rollen(self, messages, gen):
        f = _ROLLEN_FORMATE[self.fmt]
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
                if m.role == "assistant" and "</think>" in content:
                    content = content.split("</think>")[-1].lstrip("\n")
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
            return list(m.ids) if (m.role == "assistant" and m.ids is not None) else tok.encode(m.content.strip())
        if fmt.startswith("mistral-v7"):
            sp = " " if fmt == "mistral-v7" else ""
            for m in messages:
                if m.role == "system":
                    ids += self._stuecke(("[SYSTEM_PROMPT]", sp)) + inhalt(m) + self._stuecke(("[/SYSTEM_PROMPT]",))
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
            ids += self._stuecke(("<role>", "SYSTEM", "</role>")) + self.tok.encode(rest[0].content + "\n" + denken)
            ids += ende
            rest = rest[1:]
        else:
            ids += self._stuecke(("<role>", "SYSTEM", "</role>")) + self.tok.encode(denken) + ende
        namen = {"system": "SYSTEM", "user": "HUMAN", "assistant": "ASSISTANT"}
        for m in rest:
            if m.role not in namen:
                raise ValueError(f"unsupported role {m.role!r}")
            ids += self._stuecke(("<role>", namen[m.role], "</role>"))
            if m.role == "assistant":
                if m.ids is not None:
                    ids += self._stuecke(oeffnen) + list(m.ids)
                else:
                    inhalt = m.content.split("</think>")[-1].lstrip("\n") if "</think>" in m.content else m.content
                    ids += self._stuecke(("\n", "<think>", "</think>")) + self.tok.encode(inhalt)
            else:
                ids += self.tok.encode(m.content)
            ids += ende
        if gen:
            ids += self._stuecke(("<role>", "ASSISTANT", "</role>") + oeffnen)
        return ids

    def _glm4(self, messages, gen):
        """GLM-4 / GLM-4.5 as llama.cpp's chatglm4: [gMASK]<sop>, then "<|role|>" + newline +
        content per message, the reply after "<|assistant|>" + newline."""
        tok = self.tok
        ids = self._stuecke(("[gMASK]", "<sop>"))
        for m in messages:
            if m.role not in ("system", "user", "assistant"):
                raise ValueError(f"unsupported role {m.role!r}")
            ids += self._stuecke((f"<|{m.role}|>", "\n"))
            if m.role == "assistant" and m.ids is not None:
                ids += list(m.ids)
            else:
                inhalt = m.content.split("</think>")[-1].lstrip("\n") if "</think>" in m.content else m.content
                ids += tok.encode(inhalt)
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
                ids += leer + tok.encode(content.strip()) + [e] + nl
                continue
            ids += tok.encode(m.content) + [e] + nl
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
            ids += tok.encode(content) + [e] + nl
        if gen:
            ids += [s] + tok.encode("assistant\n") + self._chatml_think(nl)
        return ids

    def _chatml_think(self, nl):
        tok = self.tok
        if "<think>" not in tok.special:
            return []
        if self.thinking:
            return [tok.special_id("<think>")] + nl
        # "\n\n" as the tokenizer splits it (one token for Qwen) – two single newlines
        # read to the model like an unclosed think block
        nl2 = tok.encode("\n\n")
        return [tok.special_id("<think>")] + nl2 + [tok.special_id("</think>")] + nl2

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
                ids += tok.encode(system[0].content.strip())
            ids += [eot] + nl
        for m in messages:
            if m.role == "system":
                continue
            role = "model" if m.role == "assistant" else "user"
            if role == "model" and m.ids is not None:
                ids += [sot] + tok.encode("model\n") + list(m.ids) + [eot] + nl
                continue
            if role == "user" and m.images:
                ids += [sot] + tok.encode("user\n") + self._image_ids(m.images)
                ids += tok.encode(m.content.strip()) + [eot] + nl
                continue
            content = m.content.strip()
            if role == "model" and "<channel|>" in content:          # drop previous thinking
                content = content.split("<channel|>")[-1].strip()
            ids += [sot] + tok.encode(role + "\n" + content) + [eot] + nl
        if gen:
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
