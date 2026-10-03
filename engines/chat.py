"""
chat.py - Der Cloud-Chat-Kern (Anthropic).

Unterstützt verschiedene Denk-Stärken (effort) + adaptives Thinking, damit man –
wie bei Claude selbst – zwischen "schnell" und "max" wählen kann.

Der Stream liefert kleine Ereignisse:
    {"type": "thinking", "text": ...}  -> das Nachdenken (wird gedimmt angezeigt)
    {"type": "text",     "text": ...}  -> die eigentliche Antwort
So funktioniert er gleich wie der lokale Motor (der nur "text" liefert).

Werkzeuge gehen als echte Tool-Aufrufe (tool_use) an Claude: so denkt Claude zwischen den
Schritten einer Werkzeug-Kette weiter (interleaved thinking) – die Denk-Blöcke mit Signatur
gehen mit den Ergebnissen zurück. Für das übrige NemiCLI bleibt alles Text: Aufrufe werden
als ```aktion-Block ausgegeben, Ergebnisse kommen als „Ergebnis von '…'“-Nachricht; nur für
die laufende Kette hält Chat die echten Blöcke (`_kette`).
"""

from __future__ import annotations

import json
import os
import re
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


_ERGEBNIS = re.compile(r"(?m)^(?=Ergebnis von '\w+' \()")
INTERLEAVED_BETA = "interleaved-thinking-2025-05-14"     # ältere Claude-4-Modelle; neuere denken so ohnehin


def werkzeug_schemas() -> list[dict]:
    """NemiCLIs Werkzeuge als Tool-Definitionen; Bedeutung und Felder erklärt der System-Prompt."""
    import actions
    return [{"name": name,
             "description": f"NemiCLI-Werkzeug '{name}' (Felder und Wirkung: siehe Werkzeug-Liste im System-Prompt).",
             "input_schema": {"type": "object", "properties": {f: {} for f in a.get("felder", [])},
                              "required": list(a.get("felder", [])), "additionalProperties": True}}
            for name, a in actions.ACTIONS.items()]


def als_aktion(name: str, eingabe: dict) -> str:
    """Ein Tool-Aufruf in NemiCLIs Textform (wird vom Hauptprogramm ausgeführt)."""
    return "\n```aktion\n" + json.dumps({"tool": name, **(eingabe or {})}, ensure_ascii=False) + "\n```\n"


def ergebnisse_zuordnen(text: str, offen: list[tuple[str, str]]) -> list[dict]:
    """tool_result-Blöcke für die offenen Aufrufe (id, name) aus der gesammelten Ergebnis-
    Nachricht. Passt die Zahl nicht, bekommt der erste Aufruf den ganzen Text."""
    teile = [t.strip() for t in _ERGEBNIS.split(text) if t.strip()]
    if len(teile) != len(offen):
        teile = [text] + ["(Ergebnis steht beim ersten Aufruf.)"] * (len(offen) - 1)
    return [{"type": "tool_result", "tool_use_id": tid, "content": inhalt} for (tid, _), inhalt in zip(offen, teile)]


def _block(b) -> dict:
    d = b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b)
    d.pop("citations", None)
    return d


class Chat:
    def __init__(self, model: str = "claude-sonnet-4-6", strength: str = M.DEFAULT_STRENGTH):
        self.client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = model
        self.strength = strength
        self.messages: list[dict] = []
        self._tools: list[dict] | None = None
        self._kette_leeren()

    def reset(self) -> None:
        self.messages = []
        self._kette_leeren()

    def _kette_leeren(self) -> None:
        self._kette: list[dict] | None = None     # echte Blöcke der laufenden Werkzeug-Kette
        self._kette_ab = 0                         # ab diesem Index ersetzen sie den Text-Verlauf
        self._offen: list[tuple[str, str]] = []    # unbeantwortete Tool-Aufrufe (id, name)

    def _api_verlauf(self, user_text: str) -> list[dict]:
        """Verlauf für die API: Text, nur die laufende Werkzeug-Kette mit echten Blöcken."""
        if self._offen and self._kette is not None and user_text.startswith("Ergebnis von '"):
            self._kette.append({"role": "user", "content": ergebnisse_zuordnen(user_text, self._offen)})
            self._offen = []
            return self.messages[:self._kette_ab] + self._kette
        self._kette_leeren()
        return self.messages

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
        vorher = len(self.messages)
        pricing.kuerzen(self)
        if len(self.messages) < vorher:                # Kette verschoben: diesmal als Text
            self._kette_leeren()
        api_verlauf = self._api_verlauf(user_text)
        if self._tools is None:
            self._tools = werkzeug_schemas()
        if hinweis := pricing.aufraeum_hinweis(self):
            yield {"type": "note", "text": hinweis}

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
                messages=api_verlauf,
                max_tokens=8192,
                tools=self._tools,
                extra_body=extra_body,
                extra_headers={"anthropic-beta": INTERLEAVED_BETA},
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
                final = await stream.get_final_message()
        except Exception:
            # Bei einem Fehler den angehängten User-Eintrag entfernen,
            # damit der Verlauf konsistent bleibt, dann weiterreichen.
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            raise

        # Anthropic lehnt einen leeren Assistant-Turn beim nächsten Aufruf ab
        # (z.B. wenn das Modell nur "thinking" lieferte). Platzhalter sichert die
        # gültige user/assistant-Abwechslung im Verlauf.
        aufrufe = [b for b in final.content if getattr(b, "type", "") == "tool_use"]
        for a in aufrufe:                              # fürs Hauptprogramm: als ```aktion-Block
            text = als_aktion(a.name, a.input)
            full += text
            yield {"type": "text", "text": text}
        if aufrufe:
            if self._kette is None:
                self._kette, self._kette_ab = [], len(self.messages)
            self._kette.append({"role": "assistant", "content": [_block(b) for b in final.content]})
            self._offen = [(a.id, a.name) for a in aufrufe]
        else:
            self._kette_leeren()
        self.messages.append({"role": "assistant", "content": full or "(keine Antwort)"})
