"""
cloud.py - OpenAI-kompatibler Cloud-Motor.

Ein einziger Motor für alle Anbieter, die das OpenAI-Protokoll sprechen
(OpenAI, Google/Gemini, Mistral, Cohere, Perplexity, DeepSeek, xAI, Groq,
OpenRouter). Nur base_url + Key unterscheiden sich - die kommen aus providers.py.

Gleiche Schnittstelle wie chat.Chat:
    stream(text) -> async-Iterator von {"type": "text", "text": ...}
    ask_once(prompt, system) -> str
    reset(), .messages
So funktioniert die Aktions-Schleife in main.py für jeden Anbieter gleich.
"""

from __future__ import annotations

import re
from typing import AsyncIterator

import persona
import pricing
import providers as P
import reasoning
from textproc import think_splitter
from vision import user_content as _user_content

# Hinweis: `openai` wird BEWUSST erst in __init__ importiert (nicht hier oben).
# Das Paket braucht rund 0,9 Sekunden zum Laden – beim Start von NemiCLI weiß
# aber noch niemand, ob überhaupt ein Cloud-Modell benutzt wird.


def _usage_option_rejected(exc: Exception) -> bool:
    """Nur eine ausdrücklich unbekannte Usage-Option rechtfertigt den Retry."""
    if getattr(exc, "status_code", None) not in (400, 422):
        return False
    body = getattr(exc, "body", None)
    texts = [str(exc).lower()]
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            if (error.get("param") in ("stream_options", "stream_options.include_usage", "include_usage")
                    and error.get("code") in ("unsupported_parameter", "unknown_parameter",
                                              "unrecognized_request_argument", "extra_forbidden")):
                return True
            if isinstance(error.get("message"), str):
                texts.append(error["message"].lower())
        detail = body.get("detail")
        if isinstance(detail, list):
            for item in detail:
                if (isinstance(item, dict) and item.get("type") == "extra_forbidden"
                        and any(part in ("stream_options", "include_usage")
                                for part in item.get("loc", []))):
                    return True
    field = r"(?:stream_options(?:\.include_usage)?|include_usage)"
    unknown = (r"(?:unknown|unrecognized|unrecognised|unsupported|unexpected)\s+"
               r"(?:(?:request|body)\s+)?(?:field|parameter|argument|option|property|key)"
               r"(?:\s+supplied)?\s*[:=]?\s*[\"'`]*")
    return any(re.search(unknown + field + r"\b", text)
               or re.search(field + r"[\"'`]*\s+(?:is\s+)?(?:not supported|not allowed|unknown)\b", text)
               for text in texts)


class CloudChat:
    def __init__(self, provider_id: str, model_id: str,
                 strength: str = reasoning.DEFAULT_STRENGTH):
        from openai import AsyncOpenAI      # erst jetzt laden (siehe oben)

        prov = P.get(provider_id)
        if prov is None:
            raise RuntimeError(f"Unbekannter Anbieter: {provider_id}")
        key = prov.key()
        if not key:
            if prov.keyless:
                key = "nemicli"   # lokales Ollama ignoriert den Key, braucht aber einen
            else:
                raise RuntimeError(f"Kein API-Key gefunden ({prov.env} in der .env setzen).")

        self.provider_id = provider_id
        self.model_id = model_id
        self.model = f"{provider_id}:{model_id}"
        self.strength = (reasoning.normalize_strength(strength)
                         if reasoning.profile(self.model) else None)
        self.messages: list[dict] = []

        # --- Auto-Stark-Modus (nur lokales Ollama, nur leichtes Gemma) ----------
        # Bei einer schweren Frage schalten wir für DIESE eine Antwort auf das
        # große Gemma-Gegenstück um. Beide laufen über denselben Ollama-Client –
        # es ändert sich nur der Modellname, der Verlauf bleibt derselbe.
        self.auto_stark = False
        self.auto_ziel: str | None = None
        # Vision macht der kleine Qwen-4B-Helfer (laden/gucken/entladen),
        # nicht mehr Gemma-4-12B. Auto-Stark bleibt das große Gemma.
        self.vision_ziel: str | None = None
        if provider_id == "ollama":
            try:
                import config
                if config.load().get("auto_stark"):
                    ziel = P.gemma_upgrade_ziel(model_id)
                    if ziel:
                        self.auto_stark = True
                        self.auto_ziel = ziel
            except Exception:
                pass

        base_url = prov.chat_base
        if provider_id == "ollama":         # base_url ist dynamisch (OLLAMA_HOST)
            base_url = P.ollama_base()
        headers = {}
        if provider_id == "openrouter":     # OpenRouter mag eine Kennung (optional)
            headers = {"HTTP-Referer": "https://localhost/nemicli", "X-Title": "NemiCLI"}
        self.client = AsyncOpenAI(base_url=base_url, api_key=key,
                                  default_headers=headers or None)

    def reset(self) -> None:
        self.messages = []

    async def ask_once(self, prompt: str, system: str) -> str:
        """Fokussierte Einzel-Antwort (für Helfer-Agenten), ohne den Hauptverlauf."""
        r = await self.client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            **reasoning.request_options(self.provider_id, self.model_id,
                                        self.strength, helper=True),
        )
        if r.choices and getattr(r.choices[0], "finish_reason", None) == "length":
            raise RuntimeError("Tokenbudget erreicht: Die Helferantwort ist unvollständig.")
        return r.choices[0].message.content or ""

    async def ask_messages(self, system: str, messages: list[dict]) -> str:
        """Eine Antwort auf einen EIGENEN Verlauf (Helfer-Agent mit mehreren
        Schritten) – der Hauptverlauf bleibt unberührt.

        Helfer-Budget (reasoning.HELPER_BUDGET, 4096): Helfer helfen kurz und
        berichten – Romane schreibt der Haupt-Agent. Reicht es nicht, kommt das
        Vorhandene mit Hinweis zurück statt eines Fehlers."""
        r = await self.client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "system", "content": system}] + messages,
            **reasoning.request_options(self.provider_id, self.model_id,
                                        self.strength, helper=True),
        )
        text = (r.choices[0].message.content or "") if r.choices else ""
        if r.choices and getattr(r.choices[0], "finish_reason", None) == "length":
            text = (text.rstrip() + "\n\n[System] Antwort abgeschnitten – Tokenbudget erreicht. "
                    "Fasse dich kürzer.")
        return text

    async def _open(self, msgs, model_id: str | None = None):
        """Öffnet den Stream. Mit include_usage liefert der letzte Chunk echte
        Token-Zahlen; kennt der Anbieter die Option nicht -> ohne sie erneut.

        `model_id` überschreibt das Modell nur für DIESEN Aufruf (Auto-Stark)."""
        mid = model_id or self.model_id
        kwargs = dict(model=mid, messages=msgs, stream=True,
                      **reasoning.request_options(self.provider_id, mid, self.strength))
        try:
            return await self.client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True})
        except Exception as exc:
            if not _usage_option_rejected(exc):
                raise
            # Denkparameter und Budget bleiben bei diesem einen Retry erhalten.
            return await self.client.chat.completions.create(**kwargs)

    async def _ist_schwer(self, user_text: str) -> bool:
        """Fragt das LEICHTE Modell selbst, ob die Anfrage schwer ist. Ein kurzer
        Ein-Wort-Aufruf (~1 s) – bei einem Ja übernimmt danach das große Modell.
        Im Zweifel (Fehler, leerer Text) bleiben wir beim leichten Modell."""
        text = (user_text or "").strip()
        if len(text) < 8:                    # „hi", „danke" usw. sind nie schwer
            return False
        sys = (
            "Du bist ein Einstufer. Antworte mit GENAU EINEM Wort, sonst nichts:\n"
            "SCHWER = Programmieren/Code, Mathe/Rechnen, mehrschrittige Logik, "
            "Analyse, Recherche, Planung, Übersetzung, oder eine lange bzw. "
            "knifflige Erklärung.\n"
            "EINFACH = Smalltalk, Begrüßung, Gefühle, kurze Frage, kurze Fakten."
        )
        try:
            r = await self.client.chat.completions.create(
                model=self.model_id, temperature=0, max_tokens=4,
                messages=[{"role": "system", "content": sys},
                          {"role": "user", "content": text[:2000]}])
            ans = (r.choices[0].message.content or "").strip().lower()
            return "schwer" in ans or "hard" in ans or "komplex" in ans
        except Exception:
            return False

    async def stream(self, user_text: str, images: list[str] | None = None) -> AsyncIterator[dict]:
        self.messages.append({"role": "user", "content": _user_content(user_text, images)})

        # Verlauf auf das Kontextfenster dieses Modells kürzen
        self.messages = pricing.trim_history(self.messages, self.model)

        use_model = self.model_id
        if images and self.vision_ziel:
            # Bild da + kleines Gemma aktiv → großer Bruder (12b) übernimmt das Sehen.
            use_model = self.vision_ziel
            kurz = self.vision_ziel.split(":")[-1].split("-")[0]      # z.B. "12b"
            yield {"type": "note",
                   "text": f"🔎 Bild erkannt – ich schau mit {kurz} genauer hin"}
        elif self.auto_stark and self.auto_ziel and not images:
            # Auto-Stark: bei schweren Fragen für DIESE Antwort das große Modell nehmen.
            if await self._ist_schwer(user_text):
                use_model = self.auto_ziel
                kurz = self.auto_ziel.split(":")[-1].split("-")[0]   # z.B. "12b"
                yield {"type": "note",
                       "text": f"🧠 knifflige Frage – ich schalte auf {kurz} hoch"}

        sys_msg = {"role": "system",
                   "content": await persona.build_system_prompt_async(user_text)}
        full = ""
        try:
            stream = await self._open([sys_msg] + self.messages, model_id=use_model)
        except Exception as exc:
            # Modell ohne Vision lehnt das Bild ab -> ohne Bild als reinen Text nachreichen.
            if images and "image" in str(exc).lower():
                self.messages[-1] = {"role": "user", "content": user_text}
                try:
                    stream = await self._open([sys_msg] + self.messages)
                except Exception:
                    self.messages.pop()
                    raise
                note = ("⚠ Dieses Modell kann keine Bilder ansehen – ich gehe nur "
                        "auf den Text ein.\n\n")
                full += note
                yield {"type": "text", "text": note}
            else:
                if self.messages and self.messages[-1]["role"] == "user":
                    self.messages.pop()
                raise
        # Denk-Modelle liefern ihr Nachdenken auf zwei Wegen: als eigenes Feld
        # (DeepSeek & Co: reasoning_content) oder als <think>…</think> mitten im
        # Text (viele offene Modelle, auch über Ollama). Beides trennen wir ab,
        # damit im Verlauf nur die echte Antwort landet.
        feed, flush = think_splitter()
        truncated = False
        try:
            async for chunk in stream:
                u = getattr(chunk, "usage", None)
                if u:
                    yield {"type": "usage",
                           "input": getattr(u, "prompt_tokens", 0) or 0,
                           "output": getattr(u, "completion_tokens", 0) or 0}
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if getattr(choice, "finish_reason", None) == "length":
                    truncated = True
                d = choice.delta
                think = getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None)
                if think:
                    yield {"type": "thinking", "text": think}
                delta = d.content or ""
                if delta:
                    for kind, txt in feed(delta):
                        if kind == "text":
                            full += txt
                        yield {"type": kind, "text": txt}
            for kind, txt in flush():
                if kind == "text":
                    full += txt
                yield {"type": kind, "text": txt}
            if truncated:
                yield {"type": "note", "text": (
                    "⚠ Tokenbudget erreicht – die Antwort ist möglicherweise unvollständig."
                    if full else
                    "⚠ Tokenbudget erreicht, bevor eine Antwort fertig war.")}
        except Exception:
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            raise

        placeholder = "(Tokenbudget erreicht, keine fertige Antwort)" if truncated else "(keine Antwort)"
        self.messages.append({"role": "assistant", "content": full or placeholder})
