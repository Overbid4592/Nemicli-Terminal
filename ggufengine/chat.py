"""
Chat-Prompt-Formatierung mit Templates, die in *diesem* Code liegen, nicht in der
Modelldatei.  CVE-2024-34359 in llama-cpp-python war eine SSTI über das Jinja-Template
in den GGUF-Metadaten; dieses Template wird nie ausgewertet.  Das Metadaten-Template
wird nur *untersucht* (Teilstring-Prüfungen), um ein Format zu wählen.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

from .tokenizer import Tokenizer


@dataclass
class Message:
    role: str      # system | user | assistant
    content: str
    # Assistant-Turns, die dieser Motor erzeugt hat: die exakten Token-IDs (nach dem Generation-
    # Prompt, ohne Stop-Token).  Unverändert wiederverwendet, damit der nächste Prompt mit genau
    # den Tokens beginnt, die schon im KV-Cache liegen (Prefix-Cache).
    ids: Optional[List[int]] = None
    # User-Turns: Bild-Embeddings (ein (n_tokens, n_embd)-Tensor je Bild, aus
    # Engine.encode_image); dargestellt als Anfangsmarke + n Platzhalter + Endmarke
    images: Optional[list] = None
    # Inhalt als Stücke (text, special): ein Spezial-Stück wird zu seinem Spezial-Token, jedes andere
    # Stück zu reinem Text – für die Marken des modelleigenen Tool-Formats, die nur der Aufrufer setzt
    # (Tool-Ergebnisse, Webseiten und Dateien bleiben reiner Text und werden nie zu einer Marke)
    teile: Optional[List[Tuple[str, bool]]] = None
    # ein Tool-Ergebnis im modelleigenen Format: Gemma 4 beantwortet es im offenen Turn, Ling liest es
    # als OBSERVATION, Mistral ohne [INST]
    werkzeug_antwort: bool = False
    # Stücke direkt nach der Schlussmarke des Turns (Mistral: [AVAILABLE_TOOLS] nach dem System-Prompt)
    danach: Optional[List[Tuple[str, bool]]] = None


IMAGE_MARKS = {                                   # Anfang, Platzhalter, Ende
    "gemma4": ("<|image>", "<|image|>", "<image|>"),
    "qwen": ("<|vision_start|>", "<|image_pad|>", "<|vision_end|>"),
    "pixtral": ("[IMG_BREAK]", "[IMG]", "[IMG_END]"),   # Zeilenende, Platzhalter, Bildende
    "lfm3": ("<|vision_start|>", "<|image|>", "<|vision_end|>"),
}
IMAGE_BEGIN, IMAGE_TOKEN, IMAGE_END = IMAGE_MARKS["gemma4"]
ZEILEN_MARKEN = IMAGE_MARKS["pixtral"]            # Bild zeilenweise, braucht `grid` (Zeilen, Spalten)


_STANDARD_SYSTEM = re.compile(r"set\s+default_system_message\s*=\s*(['\"])(.*?)(?<!\\)\1", re.S)
_DENK_MARKEN = ("[THINK]", "[/THINK]")


def denk_anleitung(template: Optional[str]) -> Optional[str]:
    """Standard-Systemprompt der Vorlage, wenn er das Denken in [THINK]…[/THINK] anleitet (Ministral 3
    Reasoning, Magistral).  Nur als Zeichenkette herausgelesen, das Template wird nicht ausgewertet."""
    m = _STANDARD_SYSTEM.search(template or "")
    if not m or not all(marke in m.group(2) for marke in _DENK_MARKEN):
        return None
    text = m.group(2).replace("\\n", "\n").replace("\\'", "'").replace('\\"', '"')
    return text.strip() or None


def detect_image_marks(tok: Tokenizer) -> Optional[tuple]:
    """Die Bildmarken, die der Tokenizer kennt (None: keine Bild-Tokens)."""
    for marks in IMAGE_MARKS.values():
        if all(m in tok.special for m in marks):
            return marks
    return None


def detect_format(template: Optional[str], arch: str) -> str:
    """Chat-Format aus Teilstrings des Templates (nie ausgeführt) und der Architektur; die Regeln
    stehen in der Modell-Datenbank (daten/chatformate.json)."""
    from . import ausfuellen
    return ausfuellen.chatformat(template or "", arch)


# Formate, die nur aus „Rollen-Kopf + Inhalt + Endmarke“ bestehen (llama.cpp llm_chat_apply_template).
# Stücke: ein String, der ein Spezial-Token des Modells ist, wird als dieses Token eingefügt, alles
# andere als Text kodiert; „{role}“ ist der Rollenname.  strip = Inhalt wird beschnitten.
def _rollen_formate() -> dict:
    """Stücke je Format (daten/chatformate.json, 'rollen_formate'): pre/post/gen, optional strip,
    namen (Rollennamen), post je Rolle."""
    from . import ausfuellen
    return ausfuellen.rollen_formate()


# gpt-oss: Identitätszeile aus dem modelleigenen Template; Ende des Denkens und Beginn der
# endgültigen Antwort, wie sie im detokenisierten Strom erscheinen
HARMONY_IDENTITAET = "You are ChatGPT, a large language model trained by OpenAI."
HARMONY_FINAL = "<|end|><|start|>assistant<|channel|>final<|message|>"
# SmolLM3: „Today Date“ als strftime("%d %B %Y") in der C-Locale
_MONATE_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December")


class ChatFormatter:
    def __init__(self, tok: Tokenizer, fmt: str, thinking: bool = False, template: Optional[str] = None):
        self.tok = tok
        self.fmt = fmt
        self.thinking = thinking
        self.image_marks = detect_image_marks(tok)
        # ChatML mit Think-Block nur, wenn das Template des Modells einen nutzt: Qwen3-VL Instruct
        # teilt das Qwen3-Vokabular (<think> eingeschlossen), wurde aber nie auf den Block trainiert
        self.chatml_denkt = "<think>" in tok.special and (template is None or "<think>" in template)
        # Mistral-Reasoning: die Denk-Anleitung steht im Standard-Systemprompt der Vorlage. Ein eigener
        # Systemprompt ersetzt ihn, ohne die Anleitung antwortet das Modell ohne Denkblock.
        self.denk_anleitung = (denk_anleitung(template) if fmt.startswith("mistral")
                               and all(m in tok.special for m in _DENK_MARKEN) else None)

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
        """Öffnet der Generation-Prompt einen Denkblock (die Antwort beginnt darin)?"""
        if not self.thinking:
            return False
        return self.fmt in ("k2", "bailing", "granite4", "lfm3", "harmony") or             (self.fmt == "chatml" and self.chatml_denkt)

    @property
    def denken_schaltbar(self) -> bool:
        """Ändert `thinking` das Verhalten des Modells (sein Template schaltet das Denken ein und aus)?"""
        if self.fmt == "chatml":
            return self.chatml_denkt
        if self.fmt == "gemma4":
            return "<|think|>" in self.tok.special
        if getattr(self, "denk_anleitung", None):
            return True
        return self.fmt in ("bailing", "harmony", "granite4", "lfm3", "k2", "smollm3")

    @property
    def denkt_immer(self) -> bool:
        """Ist das Modell aufs Nachdenken trainiert, so dass Antworten ohne es unzuverlässig werden (LFM3:
        Denken ist die Vorgabe seiner Vorlage, ohne erfindet es Werkzeug-Ergebnisse)?"""
        return self.fmt == "lfm3"

    @property
    def hintergrund_getrennt(self) -> bool:
        """Hat das Modell Kontext vom Harness als eigene Turns gelernt (LFM3: Agent-Harnesses, Tool-Ergebnisse
        in der Rolle „tool“)?  Dann kommt Hintergrund zu einer Nachricht als System-Turn davor – im
        User-Turn hielte das Modell ihn für die Anfrage."""
        return self.fmt == "lfm3"

    @property
    def denken_im_system(self) -> bool:
        """Gehört der Schalter zum System-Turn?  Dann liest eine Änderung den ganzen Prompt neu ein."""
        return self.fmt in ("bailing", "harmony", "smollm3", "gemma4") or bool(getattr(self, "denk_anleitung", None))

    @property
    def drops_old_thinking(self) -> bool:
        """Lässt das modelleigene Template das Denken früherer Antworten weg?  Dann werden
        solche Antworten als Text übergeben (Message.ids = None) und ohne es dargestellt.
        Ling behält es (preserved_thinking)."""
        return self.fmt in ("chatml", "granite4", "lfm3", "gemma4", "deepseek", "harmony")

    def denk_marken(self):
        """(open, close)-Token-IDs des Denkblocks im erzeugten Text oder None.
        `close` wird eingefügt, wenn das Denkbudget aufgebraucht ist; sein erstes Token markiert
        auch das modelleigene Ende des Denkens."""
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

    # Jeder Formatierer liefert Token-IDs; Spezial-Tokens werden per ID eingefügt und
    # vom Nutzer gelieferter Text läuft durch den einfachen Encoder (keine Spezial-Erkennung).
    def encode(self, messages: List[Message], add_generation_prompt: bool = True) -> List[int]:
        if self.fmt in _rollen_formate():
            return self._rollen(messages, add_generation_prompt)
        if self.fmt.startswith("mistral") or self.fmt == "llama2":
            return self._mistral_art(self._anleitung_davor(messages), add_generation_prompt)
        fn = getattr(self, f"_{self.fmt}")
        return fn(messages, add_generation_prompt)

    # -- Stücke: Spezial-Token, wenn das Modell es hat, sonst Text ---------------------
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
        """Inhalts-IDs: aus `m.teile` (Spezial-Stücke als Spezial-Tokens, der Rest einfach), sonst
        `text` (Standard: der Inhalt) durch den einfachen Encoder."""
        if not m.teile:
            t = m.content if text is None else text
            return self.tok.encode(t.strip() if strip else t)
        teile = list(m.teile)
        if strip:                         # nur die Enden der Nachricht; Text neben einer Marke bleibt
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

    def _anleitung_davor(self, messages):
        """Bei eingeschaltetem Denken steht die Denk-Anleitung der Vorlage vor dem eigenen Systemprompt
        (ohne einen als eigener System-Turn); [THINK] und [/THINK] darin bleiben Spezial-Tokens."""
        anleitung = getattr(self, "denk_anleitung", None)
        if not (self.thinking and anleitung):
            return messages
        teile = [(s, s in _DENK_MARKEN) for s in re.split(r"(\[/?THINK\])", anleitung) if s]
        if messages and messages[0].role == "system":
            m = messages[0]
            eigen = list(m.teile) if m.teile else [(m.content.strip(), False)]
            return [replace(m, teile=teile + [("\n\n", False)] + eigen)] + list(messages[1:])
        return [Message("system", anleitung, teile=teile)] + list(messages)

    def _mistral_art(self, messages, gen):
        """Mistral v1/v3/v3-tekken/v7/v7-tekken und Llama 2 wie in llama.cpp; [INST] usw. sind
        Spezial-Tokens, wenn das Modell sie hat."""
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
        """Ling / Bailing V3: <role>NAME</role> ... <|role_end|>; der erste System-Turn endet mit
        "detailed thinking on|off", frühere Antworten tragen einen leeren Think-Block."""
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
        """GLM-4 / GLM-4.5 wie chatglm4 in llama.cpp: [gMASK]<sop>, dann "<|role|>" + Zeilenumbruch +
        Inhalt je Nachricht, die Antwort nach "<|assistant|>" + Zeilenumbruch. Tool-Ergebnisse wie im
        GLM-4.5-Template: "<|observation|>", die Stücke beginnen mit eigenem Zeilenumbruch."""
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
        """ERNIE 4.5: <|begin_of_sentence|>, System-Text + Zeilenumbruch, "User: ...\n",
        "Assistant: ...<|end_of_sentence|>", die Antwort nach "Assistant: "."""
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
        """gpt-oss (Harmony): System-Turn mit Datum, Reasoning-Stufe und Kanälen wie im
        Template des Modells; der eigene System-Prompt wird zu den Developer-Anweisungen.  Antworten
        liegen in Kanälen: Denken öffnet "analysis", sonst beginnt die Antwort direkt in "final";
        frühere Antworten werden nur als "final" dargestellt."""
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
        """Granite 4.x: ChatML mit "<think></think>" (ohne Zeilenumbrüche) für Antworten ohne
        Denken; frühere Antworten tragen einen leeren Think-Block (truncate_history_thinking)."""
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

    def _lfm3(self, messages, gen):
        """LFM3: BOS (der Tokenizer fügt es nicht hinzu), ChatML; der Generation-Prompt öffnet "<think>"
        oder schließt es sofort ("<think></think>", ohne Zeilenumbrüche); frühere Antworten tragen
        einen leeren Think-Block vor ihrem Text; Tool-Ergebnisse sind Turns der Rolle "tool"."""
        tok = self.tok
        s, e = tok.special_id("<|im_start|>"), tok.special_id("<|im_end|>")
        nl = tok.encode("\n")
        oeffnen = self._stuecke(("<think>",) if self.thinking else ("<think>", "</think>"))
        leer = self._stuecke(("<think>", "</think>"))
        ids: List[int] = [tok.bos_id] if tok.bos_id is not None else []
        for m in messages:
            if m.role not in ("system", "user", "assistant"):
                raise ValueError(f"unsupported role {m.role!r}")
            rolle = "tool" if m.role == "user" and m.werkzeug_antwort else m.role   # Template: Ergebnisse als "tool"
            ids += [s] + tok.encode(rolle + "\n")
            if m.role == "assistant":
                if m.ids is not None:
                    ids += oeffnen + list(m.ids) + [e] + nl
                    continue
                content = m.content.split("</think>")[-1].strip() if "</think>" in m.content else m.content
                ids += leer + self._inhalt(m, content) + [e] + nl
                continue
            if m.role == "user" and m.images:
                ids += self._image_ids(m.images)
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
            # Text nach </think> behält führende Leerzeichen; Stücke: nur die Nachrichtenenden
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
        # "\n\n", wie der Tokenizer es teilt (ein Token bei Qwen) – zwei einzelne Zeilenumbrüche
        # liest das Modell wie einen nicht geschlossenen Think-Block
        nl2 = tok.encode("\n\n")
        return [tok.special_id("<think>")] + nl2 + [tok.special_id("</think>")] + nl2

    def _smollm3(self, messages, gen):
        # System-Teil mit Metadaten-Kopf; "/think" oder "/no_think" im System-Text wählt den
        # Modus.  Ohne Tool-Liste beendet das Template des Modells den System-Teil OHNE <|im_end|>.
        # Antworten bleiben wie erzeugt (Denken eingeschlossen); im /no_think-Modus steht ein leerer Block davor.
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
        # Der System-Text folgt <bos> ohne Trenner; frühere Antworten verlieren ihren
        # <think>-Teil, wie im Template.
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
        # Tool-Ergebnisse im Gemma-eigenen Format (Message.werkzeug_antwort) bleiben im offenen Modell-
        # Turn direkt nach dessen Aufruf, und das Modell macht in diesem Turn weiter (wie in seinem Template).
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
        offen = False                                    # Zug des Modells noch offen (Aufruf, Ergebnis, …)
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
                    if "<channel|>" in content:                      # früheres Denken weglassen
                        content = content.split("<channel|>")[-1].strip()
                    if m.teile or offen:
                        ids += kopf + self._inhalt(m, content)
                    else:                                            # ein Stück
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
