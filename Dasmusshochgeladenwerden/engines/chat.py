"""
chat.py - Der Cloud-Chat-Kern (Anthropic).

Unterstützt verschiedene Denk-Stärken (effort) + adaptives Thinking, damit man –
wie bei Claude selbst – zwischen "schnell" und "max" wählen kann.

Der Stream liefert kleine Ereignisse:
    {"type": "thinking", "text": ...}  -> das Nachdenken (wird gedimmt angezeigt)
    {"type": "text",     "text": ...}  -> die eigentliche Antwort
So funktioniert er gleich wie der lokale Motor (der nur "text" liefert).
"""

from __future__ import annotations

import os
from typing import AsyncIterator

from anthropic import AsyncAnthropic

import persona
import models as M
import pricing


def _claude_content(text: str, images: list[str] | None):
    """Baut den Nachrichten-Inhalt im Anthropic-Format (Bilder als base64-Blöcke)."""
    if not images:
        return text
    blocks = [{"type": "text", "text": text}]
    for uri in images:
        media, _, data = uri.partition(",")          # data:image/png;base64,XXXX
        mime = "image/png"
        if media.startswith("data:") and ";" in media:
            mime = media[5:].split(";")[0] or mime
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": mime, "data": data}})
    return blocks


class Chat:
    def __init__(self, model: str = "claude-sonnet-4-6", strength: str = M.DEFAULT_STRENGTH):
        self.client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = model
        self.strength = strength
        self.messages: list[dict] = []

    def reset(self) -> None:
        self.messages = []

    async def ask_once(self, prompt: str, system: str) -> str:
        """Eine fokussierte Einzel-Antwort (für Helfer-Agenten), ohne den Hauptverlauf."""
        r = await self.client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            extra_body={"output_config": {"effort": "low"}},  # Helfer: schnell & günstig
        )
        return "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")

    async def ask_messages(self, system: str, messages: list[dict]) -> str:
        """Eine Antwort auf einen EIGENEN Verlauf (Helfer-Agent mit mehreren
        Schritten) – der Hauptverlauf bleibt unberührt."""
        r = await self.client.messages.create(
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=4096,
            extra_body={"output_config": {"effort": "low"}},  # Helfer: schnell & günstig
        )
        return "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")

    async def stream(self, user_text: str, images: list[str] | None = None) -> AsyncIterator[dict]:
        self.messages.append({"role": "user", "content": _claude_content(user_text, images)})

        # Verlauf kürzen – sonst wächst er hier (anders als bei cloud/local)
        # unbegrenzt weiter und kostet mit jedem Zug mehr.
        self.messages = pricing.trim_history(self.messages, self.model)

        # Thinking + Stärke über extra_body -> kompatibel mit allen SDK-Versionen
        extra_body = {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": M.effort_for(self.strength)},
        }

        system_prompt = await persona.build_system_prompt_async(user_text)
        full = ""
        try:
            async with self.client.messages.stream(
                model=self.model,
                system=system_prompt,
                messages=self.messages,
                max_tokens=8192,
                extra_body=extra_body,
            ) as stream:
                async for event in stream:
                    et = event.type
                    # Anthropic schickt die Token-Zahlen live mit:
                    if et == "message_start":
                        u = getattr(event.message, "usage", None)
                        if u:
                            yield {"type": "usage",
                                   "input": getattr(u, "input_tokens", 0) or 0,
                                   "output": getattr(u, "output_tokens", 0) or 0}
                        continue
                    if et == "message_delta":
                        u = getattr(event, "usage", None)
                        if u and getattr(u, "output_tokens", None) is not None:
                            yield {"type": "usage", "output": u.output_tokens}
                        continue
                    if et != "content_block_delta":
                        continue
                    d = event.delta
                    if getattr(d, "type", None) == "thinking_delta":
                        yield {"type": "thinking", "text": getattr(d, "thinking", "")}
                    elif getattr(d, "type", None) == "text_delta":
                        full += d.text
                        yield {"type": "text", "text": d.text}
        except Exception:
            # Bei einem Fehler den angehängten User-Eintrag entfernen,
            # damit der Verlauf konsistent bleibt, dann weiterreichen.
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            raise

        # Anthropic lehnt einen leeren Assistant-Turn beim nächsten Aufruf ab
        # (z.B. wenn das Modell nur "thinking" lieferte). Platzhalter sichert die
        # gültige user/assistant-Abwechslung im Verlauf.
        self.messages.append({"role": "assistant", "content": full or "(keine Antwort)"})
