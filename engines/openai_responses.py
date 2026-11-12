"""openai_responses.py – OpenAI über die Responses API, mit API-Schlüssel („openai“) oder mit dem ChatGPT-Konto
(„chatgpt“, Plan-Nutzung über Sign in with ChatGPT).

Gleiche Schnittstelle wie cloud.CloudChat (stream, ask_once, ask_messages, kompakt, messages): Verlauf, Stand des
Gesprächs, Kürzen, Bilder und Werkzeug-Protokoll bleiben dort, getauscht wird nur der Transport.

- Die Anleitung (Persönlichkeit, Gedächtnis, Werkzeuge) geht als `instructions`, der Verlauf als `input`
  (input_text, input_image, output_text) – ohne system-Einträge, die das ChatGPT-Konto ablehnt.
- Immer `stream: true` und `store: false`: nichts bleibt bei OpenAI liegen, jede Anfrage trägt ihren ganzen Verlauf.
- Erfolgreich ist eine Antwort erst mit `response.completed`; `response.incomplete` (Budget) wird ein Hinweis,
  `response.failed` und Fehler-Codes des ChatGPT-Kontos werden verständliche Meldungen.
- ChatGPT-Konto: ohne max_output_tokens, temperature & Co. (Preview limitations); das Zugangs-Token wird vor
  jeder Anfrage frisch geholt (chatgpt_login.zugang erneuert es vor Ablauf).
"""
from __future__ import annotations

from typing import AsyncIterator

import reasoning
from cloud import CloudChat

# Fehler-Codes beim ChatGPT-Konto (developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
FEHLER_TEXTE = {
    "subscription_sharing_usage_limit_exceeded":
        "Das Kontingent deines ChatGPT-Plans ist gerade aufgebraucht. In ChatGPT unter Einstellungen → Nutzung "
        "siehst du mehr. Bis dahin: ein anderes Modell oder dein OpenAI-API-Schlüssel (/model).",
    "subscription_sharing_user_not_eligible":
        "Dieses ChatGPT-Konto darf seinen Plan hier nicht nutzen (Plus oder Pro nötig, oder der Arbeitsbereich "
        "erlaubt es nicht).",
    "subscription_sharing_invalid_user":
        "OpenAI erkennt die ChatGPT-Anmeldung gerade nicht an. Hilft ein zweiter Versuch nicht: /model chatgpt "
        "abmelden und neu anmelden.",
    "subscription_sharing_usage_unavailable": "Die Plan-Nutzung ist bei OpenAI gerade nicht erreichbar – gleich nochmal.",
    "subscription_sharing_user_unavailable": "Die Plan-Nutzung ist bei OpenAI gerade nicht erreichbar – gleich nochmal.",
    "subscription_sharing_route_not_supported": "Diese Anfrage-Art kennt die Plan-Nutzung nicht.",
    "subscription_sharing_unsupported_capability": "Das kann das ChatGPT-Konto mit diesem Modell nicht",
}


class ResponsesFehler(RuntimeError):
    def __init__(self, text: str, code: str = ""):
        super().__init__(text)
        self.code = code


def _fehler_aus(code: str, text: str, param: str = "") -> ResponsesFehler:
    if code in FEHLER_TEXTE:
        meldung = FEHLER_TEXTE[code]
        if code == "subscription_sharing_unsupported_capability":
            meldung += f" ({param})." if param else "."
        return ResponsesFehler(meldung, code)
    return ResponsesFehler(f"OpenAI: {text or code or 'Anfrage gescheitert'}", code)


def _teile(inhalt, rolle: str) -> list[dict]:
    """Inhalt im Chat-Format (Text oder Liste mit text/image_url) → Teile der Responses API."""
    art = "output_text" if rolle == "assistant" else "input_text"
    if isinstance(inhalt, str):
        return [{"type": art, "text": inhalt}]
    raus = []
    for b in inhalt or []:
        if not isinstance(b, dict):
            continue
        typ = b.get("type")
        if typ == "text":
            raus.append({"type": art, "text": str(b.get("text") or "")})
        elif typ == "image_url" and rolle != "assistant":
            bild = b.get("image_url")
            url = bild.get("url") if isinstance(bild, dict) else bild
            if url:
                raus.append({"type": "input_image", "image_url": str(url)})
        elif typ in ("input_text", "input_image", "output_text"):
            raus.append(b)
    return raus or [{"type": art, "text": ""}]


def _text(inhalt) -> str:
    if isinstance(inhalt, str):
        return inhalt
    return "\n".join(str(b.get("text") or "") for b in inhalt or [] if isinstance(b, dict) and b.get("type") == "text")


def als_eingabe(nachrichten: list[dict]) -> tuple[str, list[dict]]:
    """(instructions, input): system-Nachrichten am Anfang werden die Anleitung, spätere zu developer-Einträgen."""
    anleitung: list[str] = []
    eingabe: list[dict] = []
    for m in nachrichten:
        rolle = m.get("role")
        if rolle == "system" and not eingabe:
            if (t := _text(m.get("content")).strip()):
                anleitung.append(t)
            continue
        if rolle in ("system", "developer"):
            rolle = "developer"
        elif rolle not in ("user", "assistant"):
            rolle = "user"
        eingabe.append({"role": rolle, "content": _teile(m.get("content"), rolle)})
    return "\n\n".join(anleitung), eingabe


def _feld(obj, name, vorgabe=None):
    if isinstance(obj, dict):
        return obj.get(name, vorgabe)
    return getattr(obj, name, vorgabe)


class ResponsesChat(CloudChat):
    """OpenAI (API-Schlüssel) oder ChatGPT-Konto über die Responses API."""

    def __init__(self, provider_id: str, model_id: str, strength: str = reasoning.DEFAULT_STRENGTH):
        super().__init__(provider_id, model_id, strength)
        self._konto = provider_id == "chatgpt"
        self._mit_zusammenfassung = True              # Denk-Zusammenfassung, solange der Anbieter sie zulässt

    def _client(self):
        if self._konto:
            import chatgpt_login
            self.client.api_key = chatgpt_login.zugang()
        return self.client

    def _anfrage(self, msgs: list[dict], model_id: str | None = None, *, helper: bool = False) -> dict:
        mid = model_id or self.model_id
        anleitung, eingabe = als_eingabe(msgs)
        kwargs = dict(model=mid, input=eingabe, stream=True, store=False,
                      **reasoning.responses_options(self.provider_id, mid, self.strength, helper=helper))
        if anleitung:
            kwargs["instructions"] = anleitung
        if "reasoning" in kwargs and self._mit_zusammenfassung and not helper:
            kwargs["reasoning"] = {**kwargs["reasoning"], "summary": "auto"}
        return kwargs

    async def _erstellen(self, kwargs: dict):
        from openai import APIStatusError
        try:
            return await self._client().responses.create(**kwargs)
        except APIStatusError as exc:
            koerper = exc.body if isinstance(exc.body, dict) else {}
            fehler = koerper.get("error", koerper) if isinstance(koerper, dict) else {}
            fehler = fehler if isinstance(fehler, dict) else {}
            code, text = str(fehler.get("code") or ""), str(fehler.get("message") or exc)
            if (exc.status_code == 400 and "summary" in (kwargs.get("reasoning") or {})
                    and ("summary" in text.lower() or "verif" in text.lower())):
                self._mit_zusammenfassung = False      # z. B. Organisation nicht verifiziert: ohne Zusammenfassung
                kwargs = dict(kwargs, reasoning={k: v for k, v in kwargs["reasoning"].items() if k != "summary"})
                return await self._client().responses.create(**kwargs)
            if code in FEHLER_TEXTE:
                raise _fehler_aus(code, text, str(fehler.get("param") or "")) from None
            raise

    async def _open(self, msgs, model_id: str | None = None):
        return await self._erstellen(self._anfrage(msgs, model_id))

    async def _auswerten(self, stream) -> AsyncIterator[dict]:
        fertig = False
        zusammenfassung = False
        async for ev in stream:
            typ = _feld(ev, "type", "")
            if typ == "response.output_text.delta":
                yield {"type": "text", "text": _feld(ev, "delta", "") or ""}
            elif typ in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                zusammenfassung = True
                yield {"type": "thinking", "text": _feld(ev, "delta", "") or ""}
            elif typ == "response.reasoning_summary_part.done" and zusammenfassung:
                yield {"type": "thinking", "text": "\n\n"}
            elif typ in ("response.completed", "response.incomplete"):
                fertig = True
                antwort = _feld(ev, "response")
                nutzung = _feld(antwort, "usage")
                if nutzung:
                    yield {"type": "usage", "input": _feld(nutzung, "input_tokens", 0) or 0,
                           "output": _feld(nutzung, "output_tokens", 0) or 0}
                if typ == "response.incomplete":
                    yield {"type": "length"}
            elif typ == "response.failed":
                fehler = _feld(_feld(ev, "response"), "error") or {}
                raise _fehler_aus(str(_feld(fehler, "code", "") or ""), str(_feld(fehler, "message", "") or ""))
            elif typ == "error":
                raise _fehler_aus(str(_feld(ev, "code", "") or ""), str(_feld(ev, "message", "") or ""),
                                  str(_feld(ev, "param", "") or ""))
        if not fertig:
            raise ResponsesFehler("Die Verbindung zu OpenAI brach vor dem Ende der Antwort ab – bitte nochmal.")

    async def _einmal(self, msgs: list[dict]) -> tuple[str, bool]:
        """Eine Antwort ohne Hauptverlauf (Helfer, Zusammenfassen) – immer als Stream (ChatGPT-Konto verlangt es)."""
        stream = await self._erstellen(self._anfrage(msgs, helper=True))
        text, abgeschnitten = "", False
        async for teil in self._auswerten(stream):
            if teil["type"] == "text":
                text += teil["text"]
            elif teil["type"] == "length":
                abgeschnitten = True
        return text, abgeschnitten

    async def _fragen(self, system: str, text: str) -> str:
        antwort, _ = await self._einmal([{"role": "system", "content": system}, {"role": "user", "content": text}])
        return antwort

    async def ask_once(self, prompt: str, system: str) -> str:
        antwort, abgeschnitten = await self._einmal([{"role": "system", "content": system},
                                                     {"role": "user", "content": prompt}])
        if abgeschnitten:
            raise RuntimeError("Tokenbudget erreicht: Die Helferantwort ist unvollständig.")
        return antwort

    async def ask_messages(self, system: str, messages: list[dict]) -> str:
        antwort, abgeschnitten = await self._einmal([{"role": "system", "content": system}] + messages)
        if abgeschnitten:
            antwort = (antwort.rstrip() + "\n\n[System] Antwort abgeschnitten – Tokenbudget erreicht. "
                       "Fasse dich kürzer.")
        return antwort
