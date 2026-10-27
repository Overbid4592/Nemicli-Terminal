"""
videobildcloud.py - Bilder und Videos über Cloud-Anbieter, ohne eigene Grafikkarte.

Gedacht für Rechner ohne passende Grafikkarte und für eine spätere App fürs Handy: Krea 2 und Wan 2.2
laufen lokal, diese Anbieter in der Cloud. Ein Anbieter zählt, wenn sein Schlüssel eingetragen ist –
derselbe wie für die Chat-Modelle (/model).

  Bilder   openai    Images API        POST /v1/images/generations           data[0].b64_json
           google    Interactions API  POST /v1beta/interactions             steps[].content[] type image
           xai       Images API        POST /v1/images/generations           data[0].url oder b64_json
           bfl       FLUX              POST /v1/<modell>, polling_url        result.sample (10 Min. gültig)
           ideogram  Generate          POST /v1/ideogram-v4/generate         data[0].url
           runway    Text to Image     POST /v1/text_to_image, /v1/tasks/id  output[0]
           fal       Queue             POST queue.fal.run/<modell>           images[0].url
           stability Stable Image      POST /v2beta/stable-image/generate/<core|ultra|sd3> (Formular)  Bild-Bytes
           recraft   Images API        POST /v1/images/generations           data[0].url
           together  Images API        POST /v1/images/generations           data[0].b64_json
           replicate Predictions       POST /v1/models/<modell>/predictions  output (URL oder Liste)
           luma      Agents API        POST /v1/generations, GET /v1/generations/<id>  output[0].url
  Videos   google    Interactions API  POST /v1beta/interactions (synchron)  steps[].content[] type video
           xai       Videos API        POST /v1/videos/generations, dann GET /v1/videos/{request_id}
           runway    Text/Image to Video  POST /v1/text_to_video | image_to_video, /v1/tasks/id  output[0]
           fal       Queue             POST queue.fal.run/<modell>           video.url
           minimax   Video v2          POST /v2/video_generation, GET /v2/query/video_generation/<id>  content.url
           luma      Agents API        POST /v1/generations (type video), GET /v1/generations/<id>  output[0].url

Chat-Anbieter (openai, google, xai, together, minimax) nutzen den Schlüssel aus /model; die reinen Bild-/Video-Anbieter haben einen
eigenen (Feld `env`), eingetragen über /bildmodel bzw. /video → ☁ Anbieter hinzufügen.

Stand der Schnittstellen und Modelle: Oktober 2026; FLUX, Ideogram, Runway und fal nach den offiziellen Anleitungen
vom 25.10.2026 (OpenAI-Videos/Sora am 24.09.2026 abgeschaltet,
Veo-3.1-Vorschau am 22.10.2026). Modelle lassen sich über die Config ändern (cloud_bild_modell,
cloud_video_modell), ohne den Code anzufassen.

Die Anbieter haben eigene Inhaltsregeln; was lokal geht, kann dort abgelehnt werden. Eine Ablehnung
kommt als Fehlermeldung des Anbieters zurück.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

BILD = {
    "openai": {"titel": "OpenAI", "provider": "openai", "modell": "gpt-image-2.5-flare"},
    "google": {"titel": "Google (Nano Banana)", "provider": "google", "modell": "gemini-nano-banana-2.1"},
    "xai": {"titel": "xAI (Grok Imagine)", "provider": "xai", "modell": "grok-imagine-image-2.0"},
    "bfl": {"titel": "Black Forest Labs (FLUX.2)", "env": "BFL_API_KEY", "modell": "flux-2-pro",
            "url": "https://dashboard.bfl.ai"},
    "ideogram": {"titel": "Ideogram 4.0", "env": "IDEOGRAM_API_KEY", "modell": "ideogram-v4",
                 "url": "https://developer.ideogram.ai"},
    "runway": {"titel": "Runway (Gen-4 Image)", "env": "RUNWAY_API_KEY", "modell": "gen4_image",
               "url": "https://dev.runwayml.com"},
    "fal": {"titel": "fal.ai (viele Modelle)", "env": "FAL_API_KEY", "modell": "fal-ai/flux/schnell",
            "url": "https://fal.ai/dashboard/keys"},
    "stability": {"titel": "Stability AI (Stable Image)", "env": "STABILITY_API_KEY", "modell": "core",
                  "url": "https://platform.stability.ai/account/keys"},
    "recraft": {"titel": "Recraft (V4.1)", "env": "RECRAFT_API_KEY", "modell": "recraftv4_1",
                "url": "https://www.recraft.ai/profile/api"},
    "together": {"titel": "Together AI (FLUX.2)", "provider": "together", "modell": "black-forest-labs/FLUX.2-dev"},
    "replicate": {"titel": "Replicate (viele Modelle)", "env": "REPLICATE_API_KEY",
                  "modell": "black-forest-labs/flux-schnell", "url": "https://replicate.com/account/api-tokens"},
    "luma": {"titel": "Luma (Uni-1)", "env": "LUMA_AGENTS_API_KEY", "modell": "uni-1",
             "url": "https://platform.lumalabs.ai"},
}
VIDEO = {
    "google": {"titel": "Google (Gemini Omni)", "provider": "google", "modell": "gemini-omni-1.1-flash",
               "max_s": 10},
    "xai": {"titel": "xAI (Grok Imagine)", "provider": "xai", "modell": "grok-imagine-video-1.5", "max_s": 15},
    "runway": {"titel": "Runway (Gen-4.5)", "env": "RUNWAY_API_KEY", "modell": "gen4.5", "max_s": 10,
               "url": "https://dev.runwayml.com"},
    "fal": {"titel": "fal.ai (Veo, Kling …)", "env": "FAL_API_KEY", "modell": "fal-ai/veo3.1", "max_s": 8,
            "url": "https://fal.ai/dashboard/keys"},
    "minimax": {"titel": "MiniMax (H3)", "provider": "minimax", "modell": "MiniMax-H3", "max_s": 15},
    "luma": {"titel": "Luma (Ray 3.2)", "env": "LUMA_AGENTS_API_KEY", "modell": "ray-3.2", "max_s": 10,
             "url": "https://platform.lumalabs.ai"},
}
OPENAI = "https://api.openai.com/v1"
GOOGLE = "https://generativelanguage.googleapis.com/v1beta"
XAI = "https://api.x.ai/v1"
BFL = "https://api.bfl.ai/v1"
IDEOGRAM = "https://api.ideogram.ai/v1"
RUNWAY = "https://api.dev.runwayml.com/v1"
RUNWAY_VERSION = "2024-11-06"
FAL = "https://queue.fal.run"
STABILITY = "https://api.stability.ai/v2beta"
RECRAFT = "https://external.api.recraft.ai/v1"
TOGETHER = "https://api.together.ai/v1"
REPLICATE = "https://api.replicate.com/v1"
MINIMAX = "https://api.minimax.io"
LUMA = "https://agents.lumalabs.ai/v1"
_SEITEN_LUMA_BILD = ("16:9", "3:2", "1:1", "2:3", "9:16", "2:1", "1:2", "3:1", "1:3")
_SEITEN_STABILITY = ("1:1", "16:9", "9:16", "3:2", "2:3", "4:5", "5:4", "21:9", "9:21")
_SEITEN_RUNWAY_BILD = ("1920:1080", "1080:1920", "1024:1024", "1440:1080", "1080:1440")
ZEIT_BILD = 300.0
ZEIT_VIDEO = 900.0
WARTEN_S = 5.0                     # Abstand der Nachfragen bei xAI-Videos
_SEITEN_GOOGLE = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9")
_SEITEN_XAI = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9")


class CloudFehler(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Schlüssel, Wahl, Stand
# ---------------------------------------------------------------------------

def _schluessel(anbieter: str, tabelle: dict) -> str | None:
    eintrag = tabelle[anbieter]
    if eintrag.get("env"):
        import os
        return os.getenv(eintrag["env"]) or None
    import providers
    p = providers.PROVIDERS.get(eintrag["provider"])
    return p.key() if p is not None else None


def env_name(anbieter: str, art: str = "bild") -> str:
    """Name der Umgebungsvariable mit dem Schlüssel (eigener oder der des Chat-Anbieters)."""
    eintrag = (BILD if art == "bild" else VIDEO)[anbieter]
    if eintrag.get("env"):
        return eintrag["env"]
    import providers
    return providers.PROVIDERS[eintrag["provider"]].env


def schluessel_url(anbieter: str, art: str = "bild") -> str:
    eintrag = (BILD if art == "bild" else VIDEO)[anbieter]
    if eintrag.get("url"):
        return eintrag["url"]
    import providers
    return providers.KEY_URLS.get(eintrag.get("provider", ""), "")


def ohne_schluessel(art: str = "bild") -> list[str]:
    """Anbieter, die man noch einrichten kann."""
    tabelle = BILD if art == "bild" else VIDEO
    return [a for a in tabelle if not _schluessel(a, tabelle)]


def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


def bereit(art: str = "bild") -> list[str]:
    """Anbieter mit eingetragenem Schlüssel."""
    tabelle = BILD if art == "bild" else VIDEO
    return [a for a in tabelle if _schluessel(a, tabelle)]


def gewaehlt(art: str = "bild") -> str | None:
    """Gewählter Anbieter (Config cloud_bild_anbieter / cloud_video_anbieter) – sonst der erste bereite."""
    tabelle = BILD if art == "bild" else VIDEO
    wunsch = str(_config().get(f"cloud_{art}_anbieter") or "")
    da = bereit(art)
    return wunsch if wunsch in da else (da[0] if da else None)


def modell(anbieter: str, art: str = "bild") -> str:
    tabelle = BILD if art == "bild" else VIDEO
    eigen = _config().get(f"cloud_{art}_modell")
    if isinstance(eigen, dict) and eigen.get(anbieter):
        return str(eigen[anbieter])
    return tabelle[anbieter]["modell"]


def available(timeout: float = 0.0) -> bool:
    """Für imagegen: kann die Cloud gerade malen?"""
    return gewaehlt("bild") is not None


def host() -> str:
    a = gewaehlt("bild")
    return BILD[a]["titel"] if a else "kein Cloud-Schlüssel"


def chosen_model() -> str | None:
    a = gewaehlt("bild")
    return f"{BILD[a]['titel']} · {modell(a)}" if a else None


def video_aktiv() -> bool:
    """Videos aus der Cloud? (Config video_backend = cloud und ein Anbieter mit Schlüssel)"""
    return str(_config().get("video_backend") or "") == "cloud" and gewaehlt("video") is not None


def waehlen(art: str, anbieter: str | None) -> str:
    """Cloud für Bilder bzw. Videos mit `anbieter` einschalten – None schaltet zurück auf lokal.
    Gibt die Meldung für den Nutzer zurück; ValueError bei unbekanntem Anbieter oder fehlendem Schlüssel."""
    import config
    tabelle = BILD if art == "bild" else VIDEO
    schalter = "bild_backend" if art == "bild" else "video_backend"
    if anbieter is None:
        config.update(**{schalter: "krea" if art == "bild" else "lokal"})
        return "lokal (Krea 2)" if art == "bild" else "lokal (Wan 2.2)"
    if anbieter not in tabelle:
        raise ValueError(f"Unbekannter Anbieter „{anbieter}“ – möglich: {', '.join(tabelle)}.")
    if anbieter not in bereit(art):
        wo = "/bildmodel" if art == "bild" else "/video"
        raise ValueError(f"Für {tabelle[anbieter]['titel']} ist kein Schlüssel eingetragen – über {wo} → "
                         "☁ Anbieter hinzufügen.")
    config.update(**{schalter: "cloud", f"cloud_{art}_anbieter": anbieter})
    return f"☁ {tabelle[anbieter]['titel']} · {modell(anbieter, art)}"


def label(art: str = "bild") -> str:
    a = gewaehlt(art)
    if a is None:
        return "☁ Cloud · kein Schlüssel"
    tabelle = BILD if art == "bild" else VIDEO
    return f"☁ Cloud · {tabelle[a]['titel']} · {modell(a, art)}"


# ---------------------------------------------------------------------------
# Netz
# ---------------------------------------------------------------------------

def _client(zeit: float):
    import httpx
    return httpx.Client(timeout=httpx.Timeout(30.0, read=zeit), follow_redirects=True,
                        headers={"User-Agent": "NemiCLI"})


def _fehlertext(r) -> str:
    """Meldung des Anbieters ohne Kopfzeilen oder Schlüssel."""
    try:
        j = r.json()
        e = j.get("error") if isinstance(j, dict) else None
        if isinstance(e, dict):
            return str(e.get("message") or e.get("code") or e)[:400]
        if isinstance(e, str):
            return e[:400]
        return str(j)[:400]
    except Exception:
        return (r.text or "")[:400]


def _fehlertext_json(j) -> str:
    if isinstance(j, dict):
        return str(j.get("error") or (j.get("base_resp") or {}).get("status_msg") or j)[:300]
    return str(j)[:300]


def _pruefen(r, wer: str):
    if r.status_code >= 400:
        raise CloudFehler(f"{wer} antwortet mit {r.status_code}: {_fehlertext(r)}")
    return r.json()


def _seitenverhaeltnis(groesse, erlaubt) -> str:
    if not groesse:
        return erlaubt[0]
    w, h = groesse
    ziel = w / max(1, h)
    return min(erlaubt, key=lambda s: abs(int(s.split(":")[0]) / int(s.split(":")[1]) - ziel))


def _openai_groesse(groesse) -> str:
    """WIDTHxHEIGHT in Vielfachen von 16, höchstens 2048 je Seite."""
    if not groesse:
        return "1024x1536"
    w, h = groesse
    f = min(1.0, 2048 / max(w, h))
    return f"{max(256, int(w * f) // 16 * 16)}x{max(256, int(h * f) // 16 * 16)}"


def _warten(c, url: str, kopf: dict, wer: str, fertig, *, zeit: float, warten: float = 2.0, on_status=None,
            abbruch=None, text: str = "wartet"):
    """Auftrag abfragen, bis `fertig(antwort)` ein Ergebnis liefert (oder CloudFehler wirft)."""
    ende = time.monotonic() + zeit
    while time.monotonic() < ende:
        if abbruch is not None and abbruch():
            raise CloudFehler("Abgebrochen.")
        j = _pruefen(c.get(url, headers=kopf), wer)
        erg = fertig(j)
        if erg is not None:
            return erg
        if on_status:
            stand = j.get("status") if isinstance(j, dict) else None
            on_status(f"{text} in der Cloud ({wer}) – {stand or '…'} …")
        time.sleep(warten)
    raise CloudFehler(f"{wer} hat nach {zeit / 60:.0f} Minuten nichts geliefert.")


def _runway_kopf(schluessel: str) -> dict:
    return {"Authorization": f"Bearer {schluessel}", "X-Runway-Version": RUNWAY_VERSION}


def _runway_fertig(j) -> str | None:
    stand = str(j.get("status") or "").upper()
    if stand == "SUCCEEDED":
        ausgabe = j.get("output") or []
        if ausgabe:
            return ausgabe[0]
        raise CloudFehler("Runway hat nichts geliefert.")
    if stand in ("FAILED", "CANCELLED", "CANCELED"):
        raise CloudFehler(f"Runway: {j.get('failure') or j.get('failureCode') or 'fehlgeschlagen'}")
    return None


def _runway(c, pfad: str, koerper: dict, schluessel: str, *, zeit: float, on_status=None, abbruch=None,
            text: str = "male") -> str:
    kopf = _runway_kopf(schluessel)
    auftrag = _pruefen(c.post(f"{RUNWAY}/{pfad}", headers=kopf, json=koerper), "Runway").get("id")
    if not auftrag:
        raise CloudFehler("Runway hat keinen Auftrag angenommen.")
    return _warten(c, f"{RUNWAY}/tasks/{auftrag}", kopf, "Runway", _runway_fertig, zeit=zeit, warten=WARTEN_S,
                   on_status=on_status, abbruch=abbruch, text=text)


def _fal(c, modell_id: str, koerper: dict, schluessel: str, *, zeit: float, on_status=None, abbruch=None,
         text: str = "male") -> dict:
    """fal-Warteschlange: einreichen, status_url abfragen bis COMPLETED, dann response_url holen."""
    kopf = {"Authorization": f"Key {schluessel}"}
    j = _pruefen(c.post(f"{FAL}/{modell_id.strip('/')}", headers=kopf, json=koerper), "fal.ai")
    status_url, antwort_url = j.get("status_url"), j.get("response_url")
    if not status_url or not antwort_url:
        raise CloudFehler("fal.ai hat keinen Auftrag angenommen.")
    _warten(c, status_url, kopf, "fal.ai", lambda s: True if s.get("status") == "COMPLETED" else None,
            zeit=zeit, warten=2.0, on_status=on_status, abbruch=abbruch, text=text)
    erg = _pruefen(c.get(antwort_url, headers=kopf), "fal.ai")
    if isinstance(erg, dict) and erg.get("error"):
        raise CloudFehler(f"fal.ai: {erg['error']}")
    return erg if isinstance(erg, dict) else {}


def _replicate(c, modell_id: str, eingabe: dict, schluessel: str, *, zeit: float, on_status=None,
               abbruch=None, text: str = "male") -> str:
    """Prediction anlegen (wartet bis 60 s), sonst urls.get abfragen; gibt die erste Ausgabe-URL zurück."""
    kopf = {"Authorization": f"Bearer {schluessel}"}
    j = _pruefen(c.post(f"{REPLICATE}/models/{modell_id.strip('/')}/predictions", headers={**kopf, "Prefer": "wait"},
                        json={"input": eingabe}), "Replicate")

    def fertig(a):
        stand = a.get("status")
        if stand == "succeeded":
            aus = a.get("output")
            url = aus[0] if isinstance(aus, list) and aus else aus
            if isinstance(url, str) and url:
                return url
            raise CloudFehler("Replicate hat nichts geliefert.")
        if stand in ("failed", "canceled"):
            raise CloudFehler(f"Replicate: {a.get('error') or stand}")
        return None
    erg = fertig(j)
    if erg is not None:
        return erg
    abfrage = (j.get("urls") or {}).get("get") or (f"{REPLICATE}/predictions/{j['id']}" if j.get("id") else None)
    if not abfrage:
        raise CloudFehler("Replicate hat keinen Auftrag angenommen.")
    return _warten(c, abfrage, kopf, "Replicate", fertig, zeit=zeit, warten=2.0, on_status=on_status,
                   abbruch=abbruch, text=text)


def _luma(c, koerper: dict, schluessel: str, *, zeit: float, on_status=None, abbruch=None,
          text: str = "male") -> str:
    """Luma Agents API: Generierung anlegen, alle 2 s abfragen, presigned URL aus output[0].url."""
    kopf = {"Authorization": f"Bearer {schluessel}"}
    auftrag = _pruefen(c.post(f"{LUMA}/generations", headers=kopf, json=koerper), "Luma").get("id")
    if not auftrag:
        raise CloudFehler("Luma hat keinen Auftrag angenommen.")

    def fertig(a):
        stand = a.get("state")
        if stand == "completed":
            ausgabe = a.get("output") or []
            if ausgabe and isinstance(ausgabe[0], dict) and ausgabe[0].get("url"):
                return ausgabe[0]["url"]
            raise CloudFehler("Luma hat nichts geliefert.")
        if stand == "failed":
            raise CloudFehler(f"Luma: {a.get('failure_reason') or a.get('failure_code') or 'fehlgeschlagen'}")
        return None
    return _warten(c, f"{LUMA}/generations/{auftrag}", kopf, "Luma", fertig, zeit=zeit, warten=2.0,
                   on_status=on_status, abbruch=abbruch, text=text)


def _bild_daten_uri(pfad: str) -> str:
    daten64, mime = _bild_b64(pfad)
    return f"data:{mime};base64,{daten64}"


def _inhalte(antwort, art: str) -> list[dict]:
    """Inhalts-Blöcke vom Typ `art` aus einer Interactions-Antwort (steps[] mit model_output)."""
    gefunden = []
    for schritt in (antwort.get("steps") or []) if isinstance(antwort, dict) else []:
        if isinstance(schritt, dict) and schritt.get("type") == "model_output":
            gefunden += [c for c in schritt.get("content") or [] if isinstance(c, dict) and c.get("type") == art]
    return gefunden


MAX_LADEN = 512 * 1024 * 1024
_MAGIE = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"GIF8")          # PNG, JPEG, WebP, GIF


def _laden(url: str, kopf: dict | None = None) -> bytes:
    """Ergebnis eines Anbieters holen: nur https, Weiterleitungen von Hand – der Schlüssel im Kopf geht
    nie an einen anderen Rechner als den ersten –, höchstens MAX_LADEN, und es muss ein Bild bzw.
    Video sein (eine Fehlerseite mit Status 200 wird keine .png)."""
    from urllib.parse import urljoin, urlparse
    erster = urlparse(url).hostname
    for _ in range(5):
        if urlparse(url).scheme != "https":
            raise CloudFehler("Herunterladen abgelehnt: keine https-Adresse.")
        senden = kopf if (kopf and urlparse(url).hostname == erster) else {}
        with _client(ZEIT_VIDEO) as c, c.stream("GET", url, headers=senden, follow_redirects=False) as r:
            if r.is_redirect and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code >= 400:
                raise CloudFehler(f"Herunterladen fehlgeschlagen ({r.status_code}).")
            daten = bytearray()
            for stueck in r.iter_bytes():
                daten += stueck
                if len(daten) > MAX_LADEN:
                    raise CloudFehler("Herunterladen abgebrochen: Datei zu groß.")
        kopfbytes = bytes(daten[:12])
        if not (kopfbytes.startswith(_MAGIE) or kopfbytes[4:8] == b"ftyp" or kopfbytes.startswith(b"\x1aE\xdf\xa3")):
            raise CloudFehler("Die Antwort war kein Bild und kein Video.")
        return bytes(daten)
    raise CloudFehler("Herunterladen fehlgeschlagen: zu viele Weiterleitungen.")


def _ablegen(daten: bytes, endung: str, video: bool = False) -> str:
    import profilordner
    ziel = profilordner.video_pfad(endung) if video else profilordner.bild_pfad(endung)
    Path(ziel).parent.mkdir(parents=True, exist_ok=True)
    Path(ziel).write_bytes(daten)
    return str(ziel)


def _bild_endung(daten: bytes) -> str:
    if daten[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if daten[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if daten[:4] == b"RIFF" and daten[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _bild_b64(pfad: str) -> tuple[str, str]:
    p = Path(pfad)
    daten = p.read_bytes()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(p.suffix.lower(), "image/png")
    return base64.b64encode(daten).decode("ascii"), mime


# ---------------------------------------------------------------------------
# Bilder
# ---------------------------------------------------------------------------

def generate(prompt: str, *, model: str | None = None, neg: str | None = None, steps=None, cfg=None,
             size: tuple[int, int] | None = None, seed=None, sampler=None, on_status=None,
             anbieter: str | None = None) -> str:
    """Ein Bild beim gewählten Anbieter malen und im Bilder-Ordner der Persönlichkeit ablegen. Schritte,
    CFG, Seed und Sampler gibt es in der Cloud nicht; ein Negativ-Prompt wird als „ohne …“ angehängt."""
    anbieter = anbieter or gewaehlt("bild")
    if anbieter is None:
        raise CloudFehler("Kein Cloud-Anbieter für Bilder: über /bildmodel → ☁ Anbieter hinzufügen einen Schlüssel "
                          "eintragen.")
    schluessel = _schluessel(anbieter, BILD)
    m = model or modell(anbieter)
    text = prompt.strip() + (f"\n\nAvoid: {neg.strip()}" if neg and neg.strip() else "")
    if on_status:
        on_status(f"male in der Cloud ({BILD[anbieter]['titel']}) …")
    with _client(ZEIT_BILD) as c:
        if anbieter == "openai":
            j = _pruefen(c.post(f"{OPENAI}/images/generations", headers={"Authorization": f"Bearer {schluessel}"},
                                json={"model": m, "prompt": text, "size": _openai_groesse(size), "n": 1}), "OpenAI")
            stueck = (j.get("data") or [{}])[0]
            daten = base64.b64decode(stueck["b64_json"]) if stueck.get("b64_json") else None
        elif anbieter == "google":
            j = _pruefen(c.post(f"{GOOGLE}/interactions", headers={"x-goog-api-key": schluessel},
                                json={"model": m, "input": [{"type": "text", "text": text}],
                                      "response_format": {"type": "image", "mime_type": "image/png",
                                                          "aspect_ratio": _seitenverhaeltnis(size, _SEITEN_GOOGLE)}}),
                         "Google")
            bilder = _inhalte(j, "image")
            daten = base64.b64decode(bilder[-1]["data"]) if bilder and bilder[-1].get("data") else None
        elif anbieter == "bfl":
            breite, hoehe = (int(x) for x in _openai_groesse(size or (1024, 1024)).split("x"))
            j = _pruefen(c.post(f"{BFL}/{m}", headers={"x-key": schluessel, "accept": "application/json"},
                                json={"prompt": text, "width": breite, "height": hoehe}), "Black Forest Labs")
            if not j.get("polling_url"):
                raise CloudFehler("Black Forest Labs hat keinen Auftrag angenommen.")

            def _bfl_fertig(a):
                stand = a.get("status")
                if stand == "Ready":
                    return ((a.get("result") or {}).get("sample")) or ""
                if stand in ("Error", "Failed", "Request Moderated", "Content Moderated"):
                    raise CloudFehler(f"Black Forest Labs: {stand}")
                return None
            url = _warten(c, j["polling_url"], {"x-key": schluessel, "accept": "application/json"},
                          "Black Forest Labs", _bfl_fertig, zeit=ZEIT_BILD, warten=1.0, on_status=on_status)
            daten = _laden(url) if url else None           # Link gilt nur 10 Minuten: gleich holen
        elif anbieter == "ideogram":
            j = _pruefen(c.post(f"{IDEOGRAM}/{m}/generate", headers={"Api-Key": schluessel},
                                json={"text_prompt": text}), "Ideogram")
            stueck = (j.get("data") or [{}])[0]
            daten = _laden(stueck["url"]) if stueck.get("url") else None
        elif anbieter == "runway":
            url = _runway(c, "text_to_image", {"model": m, "promptText": text[:1000],
                                               "ratio": _seitenverhaeltnis(size, _SEITEN_RUNWAY_BILD)},
                          schluessel, zeit=ZEIT_BILD, on_status=on_status)
            daten = _laden(url)
        elif anbieter == "fal":
            koerper = {"prompt": text}
            if size and m.startswith("fal-ai/flux"):
                breite, hoehe = (int(x) for x in _openai_groesse(size).split("x"))
                koerper["image_size"] = {"width": breite, "height": hoehe}
            erg = _fal(c, m, koerper, schluessel, zeit=ZEIT_BILD, on_status=on_status)
            bilder = erg.get("images") or ([erg["image"]] if isinstance(erg.get("image"), dict) else [])
            daten = _laden(bilder[0]["url"]) if bilder and bilder[0].get("url") else None
        elif anbieter == "stability":
            r = c.post(f"{STABILITY}/stable-image/generate/{m}",
                       headers={"Authorization": f"Bearer {schluessel}", "Accept": "image/*"}, files={"none": ""},
                       data={"prompt": text, "output_format": "png",
                             "aspect_ratio": _seitenverhaeltnis(size, _SEITEN_STABILITY)})
            if r.status_code >= 400:
                raise CloudFehler(f"Stability AI antwortet mit {r.status_code}: {_fehlertext(r)}")
            if str((r.headers or {}).get("finish-reason") or "").upper() == "CONTENT_FILTERED":
                raise CloudFehler("Stability AI hat das Bild gefiltert (Inhaltsregeln).")
            daten = r.content or None
        elif anbieter in ("recraft", "together"):
            basis, wer = (RECRAFT, "Recraft") if anbieter == "recraft" else (TOGETHER, "Together AI")
            koerper = {"model": m, "prompt": text}
            if anbieter == "together":
                breite, hoehe = (int(x) for x in _openai_groesse(size or (1024, 1024)).split("x"))
                koerper.update(width=breite, height=hoehe, n=1, response_format="base64")
            j = _pruefen(c.post(f"{basis}/images/generations", headers={"Authorization": f"Bearer {schluessel}"},
                                json=koerper), wer)
            stueck = (j.get("data") or [{}])[0]
            daten = (base64.b64decode(stueck["b64_json"]) if stueck.get("b64_json")
                     else _laden(stueck["url"]) if stueck.get("url") else None)
        elif anbieter == "luma":
            koerper = {"model": m, "type": "image", "prompt": text[:6000], "output_format": "png"}
            if size:
                koerper["aspect_ratio"] = _seitenverhaeltnis(size, _SEITEN_LUMA_BILD)
            daten = _laden(_luma(c, koerper, schluessel, zeit=ZEIT_BILD, on_status=on_status))
        elif anbieter == "replicate":
            eingabe = {"prompt": text}
            if size:
                eingabe["aspect_ratio"] = _seitenverhaeltnis(size, _SEITEN_STABILITY)
            url = _replicate(c, m, eingabe, schluessel, zeit=ZEIT_BILD, on_status=on_status)
            daten = _laden(url, {"Authorization": f"Bearer {schluessel}"})
        else:
            j = _pruefen(c.post(f"{XAI}/images/generations", headers={"Authorization": f"Bearer {schluessel}"},
                                json={"model": m, "prompt": text, "n": 1,
                                      "aspect_ratio": _seitenverhaeltnis(size, _SEITEN_XAI)}), "xAI")
            stueck = (j.get("data") or [{}])[0]
            daten = (base64.b64decode(stueck["b64_json"]) if stueck.get("b64_json")
                     else _laden(stueck["url"]) if stueck.get("url") else None)
    if not daten:
        raise CloudFehler(f"{BILD[anbieter]['titel']} hat kein Bild geliefert (vielleicht von den Inhaltsregeln abgelehnt).")
    return _ablegen(daten, _bild_endung(daten))


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------

def video_generate(prompt: str, *, sekunden: float = 5.0, startbild: str | None = None, hochkant: bool = False,
                   on_status=None, abbruch=None, anbieter: str | None = None) -> str:
    """Ein Video beim gewählten Anbieter erzeugen und im Video-Ordner der Persönlichkeit ablegen.
    `startbild`: das erste Bild des Videos (Bild zu Video). Länge wird auf das Höchstmaß des Anbieters gekürzt."""
    anbieter = anbieter or gewaehlt("video")
    if anbieter is None:
        raise CloudFehler("Kein Cloud-Anbieter für Videos: über /video → ☁ Anbieter hinzufügen einen Schlüssel "
                          "eintragen.")
    schluessel = _schluessel(anbieter, VIDEO)
    m = modell(anbieter, "video")
    if on_status:
        on_status(f"Video in der Cloud ({VIDEO[anbieter]['titel']}) – das dauert ein paar Minuten …")
    if anbieter == "runway":
        dauer = 5 if sekunden <= 7.5 else 10
        koerper = {"model": m, "promptText": prompt.strip()[:1000], "ratio": "720:1280" if hochkant else "1280:720",
                   "duration": dauer}
        pfad = "text_to_video"
        if startbild:
            koerper["promptImage"] = _bild_daten_uri(startbild)
            pfad = "image_to_video"
        with _client(ZEIT_VIDEO) as c:
            url = _runway(c, pfad, koerper, schluessel, zeit=ZEIT_VIDEO, on_status=on_status, abbruch=abbruch,
                          text="Video")
        return _ablegen(_laden(url), ".mp4", video=True)
    if anbieter == "luma":
        # 10 s gehen laut Luma nicht zusammen mit einem Startbild
        video = {"resolution": "720p", "duration": "10s" if sekunden > 7.5 and not startbild else "5s"}
        if startbild:
            daten64, mime = _bild_b64(startbild)
            video["start_frame"] = {"data": daten64, "media_type": mime}
        koerper = {"model": m, "type": "video", "prompt": prompt.strip()[:6000],
                   "aspect_ratio": "9:16" if hochkant else "16:9", "video": video}
        with _client(ZEIT_VIDEO) as c:
            url = _luma(c, koerper, schluessel, zeit=ZEIT_VIDEO, on_status=on_status, abbruch=abbruch, text="Video")
        return _ablegen(_laden(url), ".mp4", video=True)
    if anbieter == "minimax":
        inhalt: list = [{"type": "text", "text": prompt.strip()}]
        if startbild:
            inhalt.append({"type": "image_url", "image_url": {"url": _bild_daten_uri(startbild)}, "role": "first_frame"})
        koerper = {"model": m, "content": inhalt, "duration": max(4, min(VIDEO["minimax"]["max_s"], int(round(sekunden))))}
        kopf = {"Authorization": f"Bearer {schluessel}"}

        def _minimax_fertig(a):
            stand = str(a.get("status") or "").lower()
            if stand == "succeeded":
                url = (a.get("content") or {}).get("url") if isinstance(a.get("content"), dict) else None
                if url:
                    return url
                raise CloudFehler("MiniMax hat kein Video geliefert.")
            if stand in ("failed", "cancelled", "canceled"):
                fehler = a.get("error") or (a.get("base_resp") or {}).get("status_msg") or stand
                raise CloudFehler(f"MiniMax: {fehler}")
            return None
        with _client(ZEIT_VIDEO) as c:
            j = _pruefen(c.post(f"{MINIMAX}/v2/video_generation", headers=kopf, json=koerper), "MiniMax")
            auftrag = j.get("task_id") or j.get("id")
            if not auftrag:
                raise CloudFehler(f"MiniMax hat keinen Auftrag angenommen: {_fehlertext_json(j)}")
            url = _warten(c, f"{MINIMAX}/v2/query/video_generation/{auftrag}", kopf, "MiniMax", _minimax_fertig,
                          zeit=ZEIT_VIDEO, warten=10.0, on_status=on_status, abbruch=abbruch, text="Video")
        return _ablegen(_laden(url), ".mp4", video=True)
    if anbieter == "fal":
        koerper = {"prompt": prompt.strip()}
        modell_id = m
        if startbild:
            koerper["image_url"] = _bild_daten_uri(startbild)
            if not modell_id.rstrip("/").endswith("image-to-video"):
                modell_id = modell_id.rstrip("/").removesuffix("/text-to-video") + "/image-to-video"
        if hochkant:
            koerper["aspect_ratio"] = "9:16"
        with _client(ZEIT_VIDEO) as c:
            erg = _fal(c, modell_id, koerper, schluessel, zeit=ZEIT_VIDEO, on_status=on_status, abbruch=abbruch,
                       text="Video")
        video = erg.get("video") if isinstance(erg.get("video"), dict) else {}
        if not video.get("url"):
            raise CloudFehler("fal.ai hat kein Video geliefert (vielleicht von den Inhaltsregeln abgelehnt).")
        return _ablegen(_laden(video["url"]), ".mp4", video=True)
    if anbieter == "google":
        eingabe: list = []
        if startbild:
            daten64, mime = _bild_b64(startbild)
            eingabe.append({"type": "image", "data": daten64, "mime_type": mime})
        eingabe.append({"type": "text", "text": prompt.strip()})
        with _client(ZEIT_VIDEO) as c:
            j = _pruefen(c.post(f"{GOOGLE}/interactions", headers={"x-goog-api-key": schluessel},
                                json={"model": m, "input": eingabe,
                                      "response_format": {"type": "video", "aspect_ratio": "9:16" if hochkant else "16:9"}}),
                         "Google")
        videos = _inhalte(j, "video")
        if videos and videos[-1].get("data"):
            return _ablegen(base64.b64decode(videos[-1]["data"]), ".mp4", video=True)
        if videos and videos[-1].get("uri"):
            return _ablegen(_laden(videos[-1]["uri"], {"x-goog-api-key": schluessel}), ".mp4", video=True)
        raise CloudFehler("Google hat kein Video geliefert (vielleicht von den Inhaltsregeln abgelehnt).")
    koerper = {"model": m, "prompt": prompt.strip(),
               "duration": max(1, min(VIDEO["xai"]["max_s"], int(round(sekunden)))), "resolution": "720p"}
    if startbild:
        daten64, mime = _bild_b64(startbild)
        koerper["image"] = {"url": f"data:{mime};base64,{daten64}"}
    else:
        koerper["aspect_ratio"] = "9:16" if hochkant else "16:9"
    kopf = {"Authorization": f"Bearer {schluessel}"}
    with _client(ZEIT_BILD) as c:
        auftrag = _pruefen(c.post(f"{XAI}/videos/generations", headers=kopf, json=koerper), "xAI").get("request_id")
        if not auftrag:
            raise CloudFehler("xAI hat keinen Auftrag angenommen.")
        ende = time.monotonic() + ZEIT_VIDEO
        while time.monotonic() < ende:
            if abbruch is not None and abbruch():
                raise CloudFehler("Abgebrochen.")
            j = _pruefen(c.get(f"{XAI}/videos/{auftrag}", headers=kopf), "xAI")
            stand = j.get("status")
            if stand == "done" and (j.get("video") or {}).get("url"):
                return _ablegen(_laden(j["video"]["url"]), ".mp4", video=True)
            if stand == "failed":
                e = j.get("error") or {}
                raise CloudFehler(f"xAI: {e.get('message') or e.get('code') or 'fehlgeschlagen'}")
            if on_status:
                on_status(f"Video in der Cloud (xAI) – wartet ({stand or '…'}) …")
            time.sleep(WARTEN_S)
    raise CloudFehler(f"xAI hat nach {ZEIT_VIDEO / 60:.0f} Minuten kein Video geliefert.")
