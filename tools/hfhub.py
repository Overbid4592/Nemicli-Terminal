"""hfhub.py – Hugging Face im Controllcenter: Modelle suchen, Dateien wählen und laden.

Die Verbindung („Tor“) ist zu, bis der Nutzer sie im Controllcenter öffnet, und schließt sich mit dem Fenster oder
von Hand. Solange sie zu ist, geht kein Abruf hinaus; beim Schließen enden laufende Downloads (sie lassen sich
später fortsetzen) und alle Verbindungen werden getrennt.

Zwei Wege: nur freie Modelle (ohne Anmeldung) oder mit dem eigenen Schlüssel HF_TOKEN aus dem Tresor – damit gehen
auch Modelle, deren Bedingungen der Nutzer auf huggingface.co angenommen hat. Der Schlüssel geht nur an
huggingface.co (bei Weiterleitungen zu anderen Servern entfernt httpx ihn) und steht nie in einer Meldung.

Geladen wird über hfladen.laden: große Dateien mit der SHA-256 von Hugging Face, kleine (Konfiguration, Tokenizer,
Lizenz) mit ihrer Git-Prüfsumme; erst geprüft kommen sie in den Modellordner der Art (modellorte). Dateien im
Pickle-Format (.pth, .ckpt, .bin …) werden nicht geladen – sie könnten beim Öffnen Code ausführen.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

import hfladen

API = hfladen.API
DATEI_URL = hfladen.DATEI_URL
USER_AGENT = "NemiCLI-Modelllader"
SCHLUESSEL = "HF_TOKEN"
MAX_SUCHE = 100                    # Zeichen im Suchfeld
TREFFER = 30
MAX_KLEIN = 5 * 2**20              # kleine Dateien (ohne LFS) höchstens so groß
MAX_DATEIEN = 400                  # Einträge eines Dateibaums
VERSUCHE_429 = 3
WARTEN_429_S = 20.0

# Art -> (Anzeige, Filter der Hub-Suche)
ARTEN: dict[str, tuple[str, dict]] = {
    "chat": ("💬 Chat", {"filter": "gguf"}),
    "bild": ("🎨 Bild", {"pipeline_tag": "text-to-image"}),
    "video": ("🎬 Video", {"pipeline_tag": "text-to-video"}),
    "sehen": ("👁 Sehen", {"pipeline_tag": "image-text-to-text"}),
    "stimme": ("🗣 Stimme", {"pipeline_tag": "text-to-speech"}),
    "musik": ("🎵 Musik", {"pipeline_tag": "text-to-audio"}),
    "suche": ("🧠 Gedächtnis", {"pipeline_tag": "sentence-similarity"}),
}
PICKLE = (".pth", ".pt", ".ckpt", ".bin", ".pkl", ".pickle", ".joblib", ".mar")
MEDIEN = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov", ".wav", ".mp3")
_NICHT_VORWAEHLEN = re.compile(r"(?i)(?:^|/)(?:assets|examples?|images?|samples?|docs?|\.github)/")
_REPO = re.compile(r"[A-Za-z0-9][\w.\-]{0,95}/[\w.\-]{1,95}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}")


class Geschlossen(Exception):
    """Die Verbindung zu Hugging Face ist zu."""


class _Tor:
    """Die eine Verbindung zu Hugging Face: zu, bis der Nutzer sie öffnet; schließen trennt alles."""

    def __init__(self):
        self._sperre = threading.Lock()
        self._client = None
        self._mit = False
        self._stopps: set[threading.Event] = set()

    def oeffnen(self, mit_schluessel: bool = False, client_fabrik=None) -> None:
        schluessel = schluessel_wert() if mit_schluessel else ""
        if mit_schluessel and not schluessel:
            raise ValueError(f"Im Tresor steht noch kein {SCHLUESSEL} – dort eintragen, dann mit Schlüssel öffnen.")
        self.schliessen()
        import httpx
        kopf = {"User-Agent": USER_AGENT}
        if schluessel:
            kopf["Authorization"] = f"Bearer {schluessel}"
        fabrik = client_fabrik or httpx.Client
        client = fabrik(headers=kopf, follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0))
        with self._sperre:
            self._client, self._mit = client, bool(schluessel)

    def schliessen(self) -> None:
        with self._sperre:
            client, self._client, self._mit = self._client, None, False
            stopps, self._stopps = list(self._stopps), set()
        for s in stopps:
            s.set()
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def offen(self) -> bool:
        return self._client is not None

    def mit_schluessel(self) -> bool:
        return self._mit and self.offen()

    def client(self):
        c = self._client
        if c is None:
            raise Geschlossen("Die Verbindung zu Hugging Face ist zu – im Controllcenter öffnen.")
        return c

    def stopp(self) -> threading.Event:
        """Ein Abbruch-Signal, das beim Schließen der Verbindung gesetzt wird."""
        ev = threading.Event()
        with self._sperre:
            if self._client is None:
                ev.set()
            else:
                self._stopps.add(ev)
        return ev

    def frei(self, ev: threading.Event) -> None:
        with self._sperre:
            self._stopps.discard(ev)


TOR = _Tor()


def schluessel_wert() -> str:
    return (os.environ.get(SCHLUESSEL) or "").strip()


def hat_schluessel() -> bool:
    return bool(schluessel_wert())


def _json(url: str, params=None):
    import netzhilfe
    r = netzhilfe.abrufen(TOR.client(), "GET", url, params=params, frist_s=hfladen.JSON_FRIST_S,
                          max_bytes=hfladen.JSON_MAX_BYTES)
    if r.status_code in (401, 403):
        raise PermissionError("Kein Zugriff – bei Modellen mit Bedingungen erst auf huggingface.co zustimmen und "
                              "mit Schlüssel öffnen.")
    if r.status_code == 404:
        raise FileNotFoundError("Nicht gefunden (oder nur mit Anmeldung sichtbar).")
    r.raise_for_status()
    return r.json()


def schluessel_pruefen(holen: Callable = _json) -> bool:
    """Mit Schlüssel geöffnet: kennt Hugging Face ihn? (Name des Kontos wird nicht weitergegeben.)"""
    try:
        return isinstance(holen(f"{API}/whoami-v2"), dict)
    except PermissionError:
        return False


# ---------------------------------------------------------------------------
# Suchen
# ---------------------------------------------------------------------------

def suchen(text: str, art: str = "", holen: Callable = _json, anzahl: int = TREFFER) -> list[dict]:
    """Modelle nach Text und Art, die meistgeladenen zuerst: [{id, downloads, likes, gated, art}]."""
    text = " ".join(str(text or "").split())[:MAX_SUCHE]
    params: dict = {"sort": "downloads", "direction": "-1", "limit": int(anzahl)}
    if text:
        params["search"] = text
    params.update(ARTEN.get(art, ("", {}))[1])
    roh = holen(f"{API}/models", params)
    raus = []
    for e in roh if isinstance(roh, list) else []:
        repo = str(e.get("id") or e.get("modelId") or "")
        if not _REPO.fullmatch(repo):
            continue
        raus.append({"id": repo, "downloads": int(e.get("downloads") or 0), "likes": int(e.get("likes") or 0),
                     "gated": bool(e.get("gated")), "art": str(e.get("pipeline_tag") or "")})
    return raus


# ---------------------------------------------------------------------------
# Dateien eines Modells
# ---------------------------------------------------------------------------

@dataclass
class Eintrag:
    pfad: str
    groesse: int
    sha256: str = ""                 # große Dateien (LFS)
    git: str = ""                    # kleine Dateien: Git-Prüfsumme
    gesperrt: str = ""               # Grund, warum sie nicht geladen wird
    vorgewaehlt: bool = False

    def als_dict(self) -> dict:
        return {"pfad": self.pfad, "groesse": self.groesse, "gesperrt": self.gesperrt, "vorgewaehlt": self.vorgewaehlt,
                "geprueft": "SHA-256" if self.sha256 else "Git-Prüfsumme"}


def _pfad_sicher(pfad: str) -> bool:
    p = PurePosixPath(pfad)
    return bool(pfad) and not p.is_absolute() and ".." not in p.parts and "\\" not in pfad and ":" not in pfad \
        and all(t not in (".", "") for t in p.parts)


def lizenz(info: dict) -> str:
    karte = info.get("cardData") if isinstance(info.get("cardData"), dict) else {}
    wert = karte.get("license") or next((t.split(":", 1)[1] for t in info.get("tags") or []
                                         if isinstance(t, str) and t.startswith("license:")), "")
    return str(wert or "").strip()[:80]


def lizenz_hinweis(name: str) -> str:
    """Kurzer, freundlicher Hinweis zur Lizenz (keine Rechtsberatung)."""
    n = name.lower()
    if not n:
        return "Keine Lizenz angegeben – auf der Modellseite nachsehen."
    if "nc" in re.split(r"[-_.]", n) or "non-commercial" in n or "noncommercial" in n:
        return "Nur für nicht-kommerzielle Nutzung."
    if n in ("other", "unknown"):
        return "Eigene Lizenz – auf der Modellseite nachsehen."
    return ""


def dateien(repo: str, art: str = "", holen: Callable = _json) -> dict:
    """{revision, lizenz, hinweis, gated, eintraege: [Eintrag]} – mit Vorauswahl je Art."""
    if not _REPO.fullmatch(repo or ""):
        raise ValueError("Ungültiger Modellname.")
    info = holen(f"{API}/models/{repo}")
    info = info if isinstance(info, dict) else {}
    rev = str(info.get("sha") or "")
    rev = rev if _GIT.fullmatch(rev) else "main"
    baum = holen(f"{API}/models/{repo}/tree/{rev}", {"recursive": "true"})
    eintraege: list[Eintrag] = []
    for e in (baum if isinstance(baum, list) else [])[:MAX_DATEIEN]:
        pfad = str(e.get("path") or "")
        if e.get("type") != "file" or not _pfad_sicher(pfad):
            continue
        lfs = e.get("lfs") if isinstance(e.get("lfs"), dict) else {}
        groesse = int(lfs.get("size") or e.get("size") or 0)
        ein = Eintrag(pfad=pfad, groesse=groesse)
        sha = str(lfs.get("oid") or "")
        if _SHA256.fullmatch(sha):
            ein.sha256 = sha
        elif _GIT.fullmatch(str(e.get("oid") or "")) and groesse <= MAX_KLEIN:
            ein.git = str(e["oid"])
        else:
            ein.gesperrt = "ohne prüfbare Prüfsumme"
        endung = PurePosixPath(pfad).suffix.lower()
        if endung in PICKLE:
            ein.gesperrt = "Pickle-Format – wird aus Sicherheitsgründen nicht geladen"
        eintraege.append(ein)
    _vorwaehlen(eintraege, art)
    name = lizenz(info)
    return {"revision": rev, "lizenz": name, "hinweis": lizenz_hinweis(name), "gated": bool(info.get("gated")),
            "eintraege": eintraege}


def _vorwaehlen(eintraege: list[Eintrag], art: str) -> None:
    """Chat (GGUF): die empfohlene Größe und das Bild-Modul; sonst alles Ladbare außer Bildern und Beispielen."""
    ladbar = [e for e in eintraege if not e.gesperrt]
    ggufs = [e for e in ladbar if e.pfad.lower().endswith(".gguf")]
    if art == "chat" and ggufs:
        als_datei = [hfladen.Datei(e.pfad, e.groesse, e.sha256) for e in ggufs if e.sha256]
        sprach = [d for d in als_datei if "mmproj" not in d.name.lower()]
        bild = [d for d in als_datei if "mmproj" in d.name.lower()]
        wahl = {d.pfad for d in (hfladen.empfohlen(sorted(sprach, key=lambda d: d.groesse)),
                                 hfladen.mmproj_wahl(bild)) if d}
        for e in ladbar:
            e.vorgewaehlt = e.pfad in wahl or (not e.pfad.lower().endswith(".gguf") and e.groesse <= MAX_KLEIN
                                               and e.pfad.lower().endswith((".md", ".txt", ".json")))
        return
    for e in ladbar:
        e.vorgewaehlt = not (_NICHT_VORWAEHLEN.search(e.pfad) or e.pfad.lower().endswith(MEDIEN))


def ziel(repo: str, art: str) -> Path:
    import modellorte
    return modellorte.ordner(art if art in modellorte.BEREICHE else "chat") / hfladen.ordnername(repo)


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------

def _git_pruefsumme(daten: bytes) -> str:
    """Git-Blob-ID (SHA-1, so rechnet Git) – Abgleich mit dem Hub, kein Schutz vor Fälschung (dafür die LFS-SHA-256)."""
    return hashlib.sha1(b"blob %d\0" % len(daten) + daten, usedforsecurity=False).hexdigest()


def _klein_laden(repo: str, revision: str, e: Eintrag, ordner: Path, stopp: threading.Event) -> Path:
    import netzhilfe
    if stopp.is_set():
        raise hfladen.Abbruch()
    url = DATEI_URL.format(repo=repo, revision=revision, pfad=e.pfad)
    r = netzhilfe.abrufen(TOR.client(), "GET", url, frist_s=60.0, max_bytes=MAX_KLEIN)
    if r.status_code != 200:
        raise OSError(f"Hugging Face antwortet mit {r.status_code}")
    daten = r.content
    if len(daten) != e.groesse or _git_pruefsumme(daten) != e.git:
        raise hfladen.PruefFehler(f"{e.pfad}: Größe oder Prüfsumme stimmen nicht – die Datei wurde verworfen.")
    ordner.mkdir(parents=True, exist_ok=True)
    zieldatei = ordner / PurePosixPath(e.pfad).name
    zieldatei.write_bytes(daten)
    return zieldatei


def laden(repo: str, revision: str, eintraege: list[Eintrag], basis: Path,
          fortschritt: Callable[[int, str, str, int, int], None], stopp: threading.Event) -> list[Path]:
    """Lädt die Einträge nacheinander in `basis` (Unterordner wie im Modell). fortschritt(nr, pfad, phase,
    erledigt, gesamt). Bei „zu vielen Anfragen“ (429) wird kurz gewartet und weitergemacht."""
    raus = []
    for nr, e in enumerate(eintraege, 1):
        if e.gesperrt:
            continue
        ordner = basis / PurePosixPath(e.pfad).parent
        if not ordner.resolve().is_relative_to(basis.resolve()):
            raise hfladen.PruefFehler(f"Ungültiger Pfad: {e.pfad!r}")
        for versuch in range(VERSUCHE_429 + 1):
            if stopp.is_set():
                raise hfladen.Abbruch()
            try:
                if e.sha256:
                    d = hfladen.Datei(e.pfad, e.groesse, e.sha256, revision)
                    raus.append(hfladen.laden(repo, d, ordner, lambda ph, a, g, nr=nr, e=e:
                                              fortschritt(nr, e.pfad, ph, a, g), stopp, session=TOR.client(),
                                              revision=revision))
                else:
                    raus.append(_klein_laden(repo, revision, e, ordner, stopp))
                    fortschritt(nr, e.pfad, "lade", e.groesse, e.groesse)
                break
            except OSError as fehler:
                if "429" not in str(fehler) or versuch >= VERSUCHE_429:
                    raise
                ende = time.monotonic() + WARTEN_429_S * (versuch + 1)
                fortschritt(nr, e.pfad, "warte", 0, e.groesse)
                while time.monotonic() < ende:
                    if stopp.wait(0.5):
                        raise hfladen.Abbruch() from None
    return raus
