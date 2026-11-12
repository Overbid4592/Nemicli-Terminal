"""
providers.py - Anbieter: lokale Server (offline) und die gemeinsame Registry.

Hier stehen die lokalen Server ohne Schlüssel – Ollama, LM Studio, Jan, llama.cpp-Server/LocalAI
und vLLM: erkennen, Modelle abfragen, Kontextlänge auslesen, Grafikspeicher freigeben. Die
Cloud-Anbieter mit API-Schlüssel stehen in cloudprovider.py; PROVIDERS führt beide zusammen,
alle übrigen Module fragen nur hier.

Anbindung: eigene schlanke Adapter, kein Zusatzpaket. Fast alle sprechen das OpenAI-Protokoll
(nur eine andere base_url) -> ein einziger Cloud-Motor (cloud.py) deckt sie ab. Anthropic ist
nativ (chat.py).
"""

from __future__ import annotations

import os
import re
import time

import cloudprovider as CP
import httpx
from cloudprovider import (
    _KEIN_CHAT,
    CLOUD,
    CLOUD_NEUER_VERSUCH_S,
    CLOUD_SHOW_URL,
    KEY_URLS,
    Provider,
    _any,
    _auth_headers,
    _chat,
    _cloud_caps_cache,
    _cloud_caps_fehler,
    _offen,
    _parse_models,
    cloud_name_vision,
    ollama_cloud_capabilities,
    status_text,
)


def _lokal(pid, label, port):
    """Lokaler OpenAI-kompatibler Server ohne Schlüssel – erscheint nur, wenn er gerade antwortet."""
    base = f"http://127.0.0.1:{port}/v1"
    return Provider(pid, label, f"{pid.upper()}_LOCAL_UNUSED", chat_base=base, list_url=base + "/models",
                    keep=_chat, keyless=True, note=f"lokal (Port {port}), kein Key")


# --- Lokale Server (laufen sie, stehen sie unter /model) ------------------------
LOKAL: dict[str, Provider] = {
    "lmstudio": _lokal("lmstudio", "LM Studio", 1234),
    "jan": _lokal("jan", "Jan", 1337),
    "llamacpp": _lokal("llamacpp", "llama.cpp-Server / LocalAI", 8080),
    "vllm": _lokal("vllm", "vLLM", 8000),
    "ollama": Provider(
        "ollama", "Ollama (lokal)", "OLLAMA_LOCAL_UNUSED",  # Platzhalter; kein Key nötig
        chat_base=None,                          # wird dynamisch gesetzt (ollama_host)
        kind="openai",
        list_url=None,                           # Modelle kommen aus /api/tags (Sonderweg)
        keyless=True,
        note="lokal & offline, kein Key nötig",
    ),
}

# Reihenfolge = Reihenfolge im Menü: erst die Cloud, dann die lokalen Server.
PROVIDERS: dict[str, Provider] = {**CLOUD, **LOKAL}


def get(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


_LOKAL_CACHE: dict[str, tuple[float, bool]] = {}
_LOKAL_TTL = 15.0


def lokal_erreichbar(url: str | None) -> bool:
    """Antwortet ein lokaler Server auf /models? Kurz gemerkt, damit /model nicht bei jedem Öffnen wartet."""
    if not url:
        return False
    hit = _LOKAL_CACHE.get(url)
    if hit and time.monotonic() - hit[0] < _LOKAL_TTL:
        return hit[1]
    try:
        ok = httpx.get(url, timeout=0.4).status_code < 400
    except Exception:
        ok = False
    _LOKAL_CACHE[url] = (time.monotonic(), ok)
    return ok


def lokale_vorpruefen() -> None:
    """Alle lokalen Server gleichzeitig ansprechen – unter Windows dauert ein „hier läuft nichts“ fast eine
    Sekunde je Port, nacheinander summierte sich das."""
    offen = [p.list_url for p in PROVIDERS.values() if p.keyless and p.id != "ollama" and p.list_url
             and not (p.list_url in _LOKAL_CACHE and time.monotonic() - _LOKAL_CACHE[p.list_url][0] < _LOKAL_TTL)]
    if len(offen) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(len(offen)) as pool:
            list(pool.map(lokal_erreichbar, offen))


def detected() -> dict[str, Provider]:
    """Nur Anbieter, deren API-Key gesetzt ist (Reihenfolge der Registry)."""
    lokale_vorpruefen()
    return {pid: p for pid, p in PROVIDERS.items() if p.available()}


# --- Ollama (lokal, OpenAI-kompatibel, ohne Key) ----------------------------
# Ollama spricht das OpenAI-Protokoll auf /v1 -> cloud.py kann es fahren.
# Die Modell-Liste holen wir aber aus /api/tags, weil die (anders als /v1/models)
# die Capabilities mitliefert: so filtern wir Embeddings raus und erkennen Vision.
def _norm_host(raw: str) -> str:
    host = (raw or "http://127.0.0.1:11434").strip().rstrip("/")   # nicht localhost: Ollama lauscht nur auf IPv4
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    from urllib.parse import urlparse
    teile = urlparse(host)
    name = teile.hostname or "127.0.0.1"
    if name in ("0.0.0.0", "::", "[::]"):               # Lausch-Adresse, als Ziel unter Windows nicht erreichbar
        name = "127.0.0.1"
    if teile.port is not None:
        port = f":{teile.port}"
    else:                                              # ohne Port: Ollama-Vorgabe – außer bei https (Vermittler)
        port = "" if teile.scheme == "https" else ":11434"
    return f"{teile.scheme}://{name}{port}{teile.path}".rstrip("/")


OLLAMA_HOST = _norm_host(os.getenv("OLLAMA_HOST", ""))
_ollama_cache: dict = {"t": -1e9, "tags": None}
_OLLAMA_TTL = 8.0          # Sekunden: so lange gilt eine Erreichbarkeits-Abfrage
_OLLAMA_TTL_AUS = 30.0     # „nicht erreichbar“ gilt länger: jede neue Abfrage kostet Wartezeit


def ollama_host() -> str:
    return OLLAMA_HOST


def ollama_base() -> str:
    """OpenAI-kompatible base_url für den Chat-Motor."""
    return f"{OLLAMA_HOST}/v1"


def _ollama_tags(force: bool = False) -> list | None:
    """Gecachte /api/tags-Antwort. None = Ollama nicht erreichbar."""
    now = time.monotonic()
    ttl = _OLLAMA_TTL if _ollama_cache["tags"] is not None else _OLLAMA_TTL_AUS
    if not force and now - _ollama_cache["t"] < ttl:
        return _ollama_cache["tags"]
    try:
        # Kurzer Verbindungsaufbau: ein laufendes Ollama nimmt sofort an. Ein abgelehnter
        # Aufbau zu localhost dauert unter Windows sonst rund 2 s je Adresse (::1, 127.0.0.1).
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=httpx.Timeout(0.8, connect=0.25))
        r.raise_for_status()
        tags = r.json().get("models") or []
    except Exception:
        tags = None
    _ollama_cache.update(t=now, tags=tags)
    return tags


def ollama_reachable() -> bool:
    return _ollama_tags() is not None


def ollama_kontext(model_id: str) -> int | None:
    """Kontext, mit dem Ollama das Modell gerade geladen hat (/api/ps, `context_length`) – die Einstellung
    von Ollama gilt, nicht der Höchstwert des Modells. None = nicht geladen oder unbekannt."""
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/ps", timeout=httpx.Timeout(1.0, connect=0.25))
        r.raise_for_status()
        geladen = r.json().get("models") or []
    except Exception:
        return None
    gesucht = {model_id, model_id if ":" in model_id else model_id + ":latest"}
    for m in geladen:
        if {m.get("name"), m.get("model")} & gesucht:
            try:
                n = int(m.get("context_length") or 0)
            except (TypeError, ValueError):
                return None
            return n if n > 0 else None
    return None


def _ollama_lokal() -> bool:
    from urllib.parse import urlparse
    return (urlparse(OLLAMA_HOST).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")


def _ollama_im_vram() -> list[str] | None:
    """Namen der Modelle, die Ollama gerade im Grafikspeicher hält (/api/ps, size_vram > 0). None = keine Antwort."""
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/ps", timeout=httpx.Timeout(1.0, connect=0.25))
        r.raise_for_status()
        geladen = r.json().get("models") or []
    except Exception:
        return None
    return [str(m.get("name") or m.get("model")) for m in geladen
            if (m.get("name") or m.get("model")) and int(m.get("size_vram") or 0) > 0]


def ollama_gpu_freigeben(warten_s: float = 5.0) -> list[str]:
    """Vor GPU-Arbeit (Bild, Video, Musik, LoRA): alle Modelle entladen, die das lokale Ollama im Grafikspeicher
    hält (keep_alive 0). Ollama kann ein Modell nicht in den RAM verschieben; die Datei bleibt aber im
    Datei-Zwischenspeicher von Windows, der nächste Chat lädt sie von dort zügig neu. Ein entferntes Ollama
    bleibt unberührt, ebenso reine CPU- und Cloud-Modelle. Rückgabe: entladene Modelle."""
    if not _ollama_lokal():
        return []
    if _ollama_cache["tags"] is None and time.monotonic() - _ollama_cache["t"] < _OLLAMA_TTL_AUS:
        return []                                      # eben erst nicht erreichbar: nicht erneut warten
    namen = _ollama_im_vram()
    if not namen:
        return []
    for name in namen:
        try:
            httpx.post(f"{OLLAMA_HOST}/api/generate", json={"model": name, "keep_alive": 0},
                       timeout=httpx.Timeout(10.0, connect=0.25))
        except Exception:
            pass
    ende = time.monotonic() + warten_s                 # Ollama gibt den Speicher kurz nach der Antwort frei
    while time.monotonic() < ende:
        noch = _ollama_im_vram()
        if not noch or not set(noch) & set(namen):
            break
        time.sleep(0.25)
    return namen


def _ollama_chatable(m: dict) -> bool:
    caps = m.get("capabilities") or []
    if caps:                              # moderne Ollama: Capabilities nutzen
        return "completion" in caps and "embedding" not in caps
    return "embed" not in (m.get("name") or "").lower()   # ältere: am Namen raten


def ollama_models() -> list[str]:
    """Namen der nutzbaren Chat-Modelle (Embeddings rausgefiltert)."""
    tags = _ollama_tags() or []
    return sorted(m["name"] for m in tags if m.get("name") and _ollama_chatable(m))


# Reihenfolge = Vorliebe: das GRÖSSTE installierte Gegenstück gewinnt.
_GEMMA_GROSS = ("27b", "12b", "9b")
_GEMMA_KLEIN = ("e2b", "e4b", "1b", "2b", "4b")


def _hat_groesse(text: str, groesse: str) -> bool:
    """Prüft eine Modellgröße mit Ziffern-Grenze, damit '2b' NICHT in '12b'
    trifft (sonst hielte die Erkennung ein 12B-Modell für ein 2B-Modell)."""
    return re.search(r"(?<![0-9])" + re.escape(groesse) + r"(?![0-9])", text) is not None


def gemma_upgrade_ziel(model_id: str) -> str | None:
    """Für den Auto-Stark-Modus: zu einem LEICHTEN Gemma (e2b/e4b/…) das
    installierte SCHWERE Gegenstück (27b/12b/9b) finden.

    Gibt die model_id des schweren Modells zurück – oder None, wenn das aktive
    Modell schon groß ist oder gar kein passendes zweites Gemma da ist. So greift
    der Modus von allein nur beim leichten Modell.
    """
    low = (model_id or "").lower()
    if "gemma" not in low:
        return None
    if not any(_hat_groesse(low, k) for k in _GEMMA_KLEIN):
        return None                              # schon ein großes Modell
    # familien-Stamm (z.B. "gemma4") grob festhalten, damit gemma3 nicht gemma4 upgradet
    stamm = re.match(r"[a-z]*gemma[0-9]*", low)
    stamm = stamm.group(0) if stamm else "gemma"

    best_rang, best = 99, None
    for m in ollama_models():
        ml = m.lower()
        if stamm not in ml or m == model_id:
            continue
        if any(_hat_groesse(ml, k) for k in _GEMMA_KLEIN):   # anderes kleines Modell -> weg
            continue
        for rang, size in enumerate(_GEMMA_GROSS):
            if _hat_groesse(ml, size) and rang < best_rang:
                best_rang, best = rang, m
    return best


_ollama_caps_cache: dict = {}      # model_id -> capabilities-Liste (pro Modell gecacht)


def _ollama_capabilities(model_id: str) -> list:
    """Die vollständigen Capabilities eines Ollama-Modells über /api/show – /api/tags meldet
    vision/audio nicht zuverlässig (bei Gemma z. B. nur `completion, tools, thinking`)."""
    if model_id in _ollama_caps_cache:
        return _ollama_caps_cache[model_id]
    try:
        r = httpx.post(f"{OLLAMA_HOST}/api/show", json={"model": model_id},
                       timeout=httpx.Timeout(3.0, connect=0.25))
        r.raise_for_status()
        caps = r.json().get("capabilities") or []
    except Exception:
        return []                  # Fehler nicht cachen – nächstes Mal neu versuchen
    _ollama_caps_cache[model_id] = caps
    return caps


def ollama_is_vision(model_id: str) -> bool:
    return "vision" in _ollama_capabilities(model_id)


def ist_vision_schwach(model_id: str) -> bool:
    """True für kleine Gemmas (e2b/e4b): Die MELDEN zwar 'vision', sind aber zu
    klein, um Bilder wirklich brauchbar zu lesen (Text/Zahlen/Details gehen unter).
    Sie gelten deshalb als nicht sehend."""
    low = (model_id or "").lower()
    return "gemma" in low and any(_hat_groesse(low, k) for k in ("e2b", "e4b"))


LISTE_MAX = 2000           # /model zeigt davon 30 auf einmal, 🔍 Suchen findet den Rest
_models_cache: dict[str, tuple[float, list[str]]] = {}
_MODELS_TTL = 600.0        # 10 Minuten – Anbieter bringen selten stündlich neue Modelle


async def list_models(provider_id: str, limit: int = 40,
                      force: bool = False) -> list[str]:
    """
    Fragt die verfügbaren Chat-Modelle eines Anbieters live ab.
    Bei Anbietern ohne Listen-Endpunkt: die statische Fallback-Liste.
    Wirft bei Netz-/Auth-Fehlern eine Exception (vom Aufrufer angezeigt).

    Ergebnisse werden 10 Minuten gecacht: sonst wartet man bei JEDEM Öffnen
    von /model erneut aufs Netz. `force=True` holt frisch.
    """
    p = PROVIDERS.get(provider_id)
    if p is None:
        raise ValueError(f"Unbekannter Anbieter: {provider_id}")

    if provider_id == "ollama":               # Sonderweg: /api/tags mit Capabilities
        return ollama_models()[:limit]        # (hat schon einen eigenen Kurz-Cache)

    if not p.list_url:
        return list(p.static_models)

    ck = f"{provider_id}:{limit}"
    hit = _models_cache.get(ck)
    if not force and hit and time.monotonic() - hit[0] < _MODELS_TTL:
        return list(hit[1])

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(p.list_url, headers=_auth_headers(p))
        if r.status_code >= 400:
            raise RuntimeError(status_text(p.label, r.status_code))
        data = r.json()
    if not p.keyless:                         # Kontextlängen aus derselben Liste merken
        CP._liste_merken(p, data)

    ids = [mid for mid in _parse_models(p, data) if p.keep(mid)]
    # ChatGPT-Konto: Reihenfolge des Servers (empfohlene zuerst), sonst alphabetisch
    ids = list(dict.fromkeys(ids)) if p.kind == "chatgpt" else sorted(set(ids))
    # Lange Listen (OpenRouter!) kappen, sonst sprengt das Menü den Bildschirm.
    if len(ids) > limit:
        ids = ids[:limit]
    if ids:                                   # nur Brauchbares merken
        _models_cache[ck] = (time.monotonic(), list(ids))
    return ids


# --- Kontextlänge ---------------------------------------------------------------
# Cloud: wie der Anbieter sie in seiner Modell-Liste nennt (cloudprovider.kontext). Lokale Server: der
# Kontext, mit dem das Modell gerade geladen ist – LM Studio `loaded_context_length` (/api/v0/models),
# llama.cpp `n_ctx` (/props), vLLM `max_model_len` und Jan aus /v1/models. Lokal nur kurz gemerkt: ein
# neu geladenes Modell kann einen anderen Kontext haben. Lokales Ollama misst cloud.py (ollama_kontext).
_LOKAL_KONTEXT: dict[str, tuple[float, int]] = {}
LOKAL_KONTEXT_TTL = 60.0


def _lokal_kontext_holen(p: Provider, model_id: str) -> bool:
    wurzel = p.list_url.rsplit("/v1", 1)[0]
    n = None
    zeit = httpx.Timeout(2.0, connect=0.4)
    if p.id == "lmstudio":
        daten = httpx.get(wurzel + "/api/v0/models", timeout=zeit).json()
        for m in daten.get("data", []) if isinstance(daten, dict) else []:
            if isinstance(m, dict) and m.get("id") == model_id:
                n = CP._zahl(m.get("loaded_context_length")) or CP.kontext_aus_eintrag(m)
    elif p.id == "llamacpp":
        try:
            daten = httpx.get(wurzel + "/props", timeout=zeit).json()
            n = CP._zahl((daten.get("default_generation_settings") or {}).get("n_ctx")) or CP._zahl(daten.get("n_ctx"))
        except Exception:
            n = None                              # LocalAI kennt /props nicht: dann aus /v1/models
    if not n:
        daten = httpx.get(p.list_url, timeout=zeit).json()
        n = next((CP.kontext_aus_eintrag(m) for mid, m in CP._eintraege(p, daten) if mid == model_id), None)
    if n:
        _LOKAL_KONTEXT[f"{p.id}:{model_id}"] = (time.monotonic(), n)
    return bool(n)


def kontext(provider_id: str, model_id: str) -> int | None:
    """Kontextlänge, wie Anbieter oder lokaler Server sie nennen – None, solange unbekannt (dann gilt der
    Richtwert in pricing). Nachgefragt wird im Hintergrund; die Oberfläche wartet nie aufs Netz."""
    p = PROVIDERS.get(provider_id)
    if p is None or provider_id == "ollama":
        return None
    if not p.keyless:
        return CP.kontext(provider_id, model_id)
    schluessel = f"{provider_id}:{model_id}"
    hit = _LOKAL_KONTEXT.get(schluessel)
    if hit is None or time.monotonic() - hit[0] > LOKAL_KONTEXT_TTL:
        CP.im_hintergrund("lokal:" + schluessel, lambda: _lokal_kontext_holen(p, model_id))
    return hit[1] if hit else None
