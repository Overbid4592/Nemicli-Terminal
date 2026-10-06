"""
hfladen.py - GGUF-Modelle von Hugging Face holen (Downloader in /model).

Nur die öffentliche API und die Dateien selbst; kein Login, keine gesperrten Modelle.
Gezeigt wird nur, was der eigene Motor laden kann (Bauart aus den GGUF-Metadaten) und
mindestens MIN_B Milliarden Parameter hat – MoE zählt nach Gesamtgröße, Gemmas „E…B“ nach
der effektiven Größe im Namen. Jede Datei wird
beim Laden mit SHA-256 geprüft (Wert von Hugging Face) und erst danach unter
ModelGGUF/<Name>/ abgelegt; ein abgebrochener Download wird fortgesetzt.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

API = "https://huggingface.co/api"
DATEI_URL = "https://huggingface.co/{repo}/resolve/main/{pfad}"
ANBIETER = ("unsloth", "bartowski", "ggml-org", "lmstudio-community")
MIN_B = 4.0
GROESSEN = {                      # Schlüssel: (von, bis) in Milliarden Parametern, Anzeige
    "4-8": (4.0, 8.0, "4–8B"),
    "8-15": (8.0, 15.0, "8–15B"),
    "15-35": (15.0, 35.0, "15–35B"),
    "35+": (35.0, math.inf, "über 35B"),
}
# Reihenfolge der Empfehlung, wenn es kein Q4_K_M gibt
_QUANT_VORZUG = ("Q4_K_M", "UD-Q4_K_XL", "Q4_K_S", "IQ4_XS", "IQ4_NL", "Q5_K_M", "UD-Q5_K_XL", "Q5_K_S",
                 "Q6_K", "UD-Q6_K_XL", "Q8_0", "Q4_0", "Q3_K_M", "UD-Q3_K_XL")
_MMPROJ_VORZUG = ("BF16", "F16", "F32")
_MOE_BAUARTEN = {"qwen2moe", "qwen3moe", "granitemoe", "ernie4_5-moe", "glm4moe", "gpt-oss", "deepseek2",
                 "bailingmoe3"}
_MOE_NAME = re.compile(r"(?i)(?:[-_ ]A(\d+(?:\.\d+)?)B\b|\b\d+x\d+(?:\.\d+)?B\b|\bMoE\b)")
_EFFEKTIV = re.compile(r"(?i)[-_]E(\d+(?:\.\d+)?)B\b")     # Gemma „E4B“: effektive Größe zählt
_QUANT = re.compile(r"(?i)(UD-)?((?:I?Q\d(?:_[A-Z0-9]+)+)|Q\d_\d|BF16|F16|F32|MXFP4(?:_MOE)?)(?=\.gguf$|-)")
_TEILDATEI = re.compile(r"-\d{5}-of-\d{5}\.gguf$")
_BEIWERK = re.compile(r"(?i)(?:^|[/_.-])(?:mtp|imatrix|draft|eagle\d*)(?:[/_.-]|$)")   # Hilfsdateien, kein Modell
WINDOWS_VRAM = int(1.7 * 2**30)       # belegt Windows selbst dauerhaft
RESERVE = int(1.5 * 2**30)            # Kontext und Arbeitsspeicher des Motors
_BLOCK = 4 * 2**20


class Abbruch(Exception):
    """Download angehalten; die angefangene Datei bleibt zum Fortsetzen liegen."""


class PruefFehler(Exception):
    """Größe oder SHA-256 stimmen nicht mit Hugging Face überein."""


@dataclass
class Modell:
    repo: str
    bauart: str
    parameter: int
    moe: bool
    aktiv: str            # aktive Parameter laut Name, z. B. "3B" – leer wenn unbekannt
    kontext: int
    downloads: int
    sieht: bool

    @property
    def name(self) -> str:
        return ordnername(self.repo)

    @property
    def anbieter(self) -> str:
        return self.repo.split("/")[0]

    @property
    def milliarden(self) -> float:
        return self.parameter / 1e9

    @property
    def groesse_text(self) -> str:
        b = f"{self.milliarden:.0f}B" if self.milliarden >= 10 else \
            f"{self.milliarden:.1f}".replace(".0", "").replace(".", ",") + "B"
        if self.moe:
            return f"{b} MoE" + (f" (aktiv {self.aktiv})" if self.aktiv else "")
        return b

    @property
    def q4_schaetzung(self) -> int:
        """Ungefähre Größe als Q4_K_M (~4,9 Bit je Parameter)."""
        return int(self.parameter * 4.9 / 8)


@dataclass
class Datei:
    pfad: str
    groesse: int
    sha256: str

    @property
    def name(self) -> str:
        return self.pfad.rsplit("/", 1)[-1]

    @property
    def quant(self) -> str:
        m = _QUANT.search(self.name)
        return ((m.group(1) or "").upper() + m.group(2).upper()) if m else "?"


# ---------------------------------------------------------------------------
# Netz
# ---------------------------------------------------------------------------

def _session():
    import httpx
    return httpx.Client(headers={"User-Agent": "NemiCLI-Modelllader"}, follow_redirects=True,
                        timeout=httpx.Timeout(30.0, read=120.0))


def _json(url: str, params=None):
    with _session() as s:
        r = s.get(url, params=params)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Suche
# ---------------------------------------------------------------------------

# Bauarten des Motors ohne torch abfragbar (ggufengine lädt torch schon beim Import) –
# vor dem Einrichten soll man schon laden können. tests/test_hfladen.py hält die Liste
# gleich mit ggufengine.models.SUPPORTED_ARCHS.
BAUARTEN = ("qwen35", "gemma4", "bailingmoe3", "deepseek2", "llama", "mistral", "mistral3", "qwen2", "qwen3",
            "qwen2moe", "qwen3moe", "k2-horizon", "gemma", "gemma2", "gemma3", "phi3", "granite", "granitemoe",
            "olmo2", "olmo3", "gpt-oss", "ernie4_5", "ernie4_5-moe", "seed_oss", "exaone4", "cohere2",
            "command-r", "glm4", "glm4moe", "smollm3")


def unterstuetzt() -> set[str]:
    return set(BAUARTEN)


def ordnername(repo: str) -> str:
    name = repo.split("/")[-1]
    name = re.sub(r"(?i)[-_.]gguf$", "", name)
    name = re.sub(r"^[^-_]+_(?=.)", "", name)        # „Qwen_Qwen3-8B“ (Hersteller vorangestellt) → „Qwen3-8B“
    return re.sub(r"[^\w.\-]+", "_", name).strip("._") or "Modell"


def ist_moe(bauart: str, repo: str) -> tuple[bool, str]:
    """(MoE?, aktive Parameter laut Name)."""
    m = _MOE_NAME.search(repo.split("/")[-1])
    aktiv = f"{m.group(1)}B" if m and m.group(1) else ""
    return (bauart in _MOE_BAUARTEN or m is not None), aktiv.replace(".", ",")


def _modell(eintrag: dict, bauarten: set[str]) -> Optional[Modell]:
    g = eintrag.get("gguf") or {}
    bauart, total = g.get("architecture"), g.get("total")
    if eintrag.get("gated") or eintrag.get("private") or not isinstance(total, int):
        return None
    if "gguf" not in eintrag["id"].lower() or bauart not in bauarten or total < MIN_B * 1e9:
        return None
    if (e := _EFFEKTIV.search(eintrag["id"])) and float(e.group(1)) < MIN_B:
        return None
    moe, aktiv = ist_moe(bauart, eintrag["id"])
    return Modell(repo=eintrag["id"], bauart=bauart, parameter=total, moe=moe, aktiv=aktiv,
                  kontext=int(g.get("context_length") or 0), downloads=int(eintrag.get("downloads") or 0),
                  sieht=eintrag.get("pipeline_tag") == "image-text-to-text")


_KATALOG: list[Modell] | None = None


def katalog(holen: Callable = _json, neu: bool = False) -> list[Modell]:
    """Alle passenden Modelle der bekannten Anbieter, je Grundmodell das meistgeladene."""
    global _KATALOG
    if _KATALOG is not None and not neu:
        return _KATALOG
    bauarten = unterstuetzt()
    beste: dict[str, Modell] = {}
    fehler: Exception | None = None
    erreicht = False
    for autor in ANBIETER:
        try:
            liste = holen(f"{API}/models", params={
                "author": autor, "filter": "gguf", "sort": "downloads", "direction": "-1", "limit": "200",
                "expand[]": ["gguf", "downloads", "gated", "pipeline_tag"]})
            erreicht = True
        except Exception as e:
            fehler = e
            continue
        for e in liste if isinstance(liste, list) else []:
            m = _modell(e, bauarten)
            if m is None:
                continue
            schluessel = m.name.lower()
            if schluessel not in beste or m.downloads > beste[schluessel].downloads:
                beste[schluessel] = m
    if not erreicht:
        raise OSError(f"Hugging Face nicht erreichbar ({fehler})")
    _KATALOG = sorted(beste.values(), key=lambda m: -m.downloads)
    return _KATALOG


def suchen(groesse: str, moe: Optional[bool], holen: Callable = _json, anzahl: int = 20) -> list[Modell]:
    von, bis, _ = GROESSEN[groesse]
    return [m for m in katalog(holen)
            if von <= m.milliarden < bis and (moe is None or m.moe == moe)][:anzahl]


# ---------------------------------------------------------------------------
# Dateien eines Modells
# ---------------------------------------------------------------------------

def dateien(repo: str, holen: Callable = _json) -> tuple[list[Datei], list[Datei]]:
    """(Sprachmodell-Dateien, mmproj-Dateien) – nur ganze .gguf mit SHA-256."""
    baum = holen(f"{API}/models/{repo}/tree/main", params={"recursive": "true"})
    sprach, bild = [], []
    for e in baum if isinstance(baum, list) else []:
        pfad, lfs = e.get("path", ""), e.get("lfs") or {}
        if e.get("type") != "file" or not pfad.lower().endswith(".gguf") or _TEILDATEI.search(pfad) \
                or _BEIWERK.search(pfad):
            continue
        sha = str(lfs.get("oid") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            continue
        d = Datei(pfad=pfad, groesse=int(lfs.get("size") or e.get("size") or 0), sha256=sha)
        (bild if "mmproj" in d.name.lower() else sprach).append(d)
    return sorted(sprach, key=lambda d: d.groesse), bild


def empfohlen(sprach: list[Datei]) -> Optional[Datei]:
    nach_quant = {}
    for d in sprach:
        nach_quant.setdefault(d.quant, d)
    for q in _QUANT_VORZUG:
        if q in nach_quant:
            return nach_quant[q]
    return sprach[0] if sprach else None


def mmproj_wahl(bild: list[Datei]) -> Optional[Datei]:
    for q in _MMPROJ_VORZUG:
        for d in bild:
            if q in d.name.upper():
                return d
    return bild[0] if bild else None


# ---------------------------------------------------------------------------
# Passt das?
# ---------------------------------------------------------------------------

def passt(groesse: int, vram_mb: int, ram_mb: int = 0) -> tuple[str, str]:
    """(Zeichen, Text) – wo die Gewichte samt Reserve Platz haben. vram_mb: Speicher der
    NVIDIA-Karte (0 = keine; AMD/Intel zählen nicht, der Motor rechnet dann auf der CPU),
    ram_mb: Arbeitsspeicher (0 = unbekannt)."""
    ram = int(ram_mb * 2**20 * 0.7)                 # Rest braucht Windows und NemiCLI selbst
    if vram_mb <= 0:
        if ram and groesse + RESERVE > ram:
            return "❌", "zu groß für den Arbeitsspeicher"
        return "🐢", "ohne NVIDIA-Karte – läuft langsam auf der CPU"
    frei = vram_mb * 2**20 - WINDOWS_VRAM
    if groesse + RESERVE <= frei:
        return "✅", "passt in den Grafikspeicher"
    if groesse <= frei:
        return "⚠", "knapp – wenig Platz für langen Kontext"
    if ram and groesse + RESERVE > max(frei, 0) + ram:
        return "❌", "zu groß für diesen PC (Grafik- und Arbeitsspeicher zusammen)"
    return "🐢", "größer als der Grafikspeicher – Teile laufen im RAM, langsamer"


def frei_auf_platte(ordner: Path) -> int:
    p = Path(ordner)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def gb(n: int) -> str:
    return f"{n / 2**30:.1f} GB".replace(".", ",")


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------

def laden(repo: str, datei: Datei, ordner: Path, fortschritt: Callable[[str, int, int], None],
          stopp: threading.Event, session=None) -> Path:
    """Lädt `datei` nach `ordner`, prüft Größe und SHA-256 und legt sie erst dann ab.
    fortschritt(phase, erledigt, gesamt) mit phase "pruefe" (angefangene Datei) oder "lade".
    Abbruch über `stopp`: die .part-Datei bleibt liegen und wird beim nächsten Mal fortgesetzt."""
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / datei.name
    if ziel.is_file() and ziel.stat().st_size == datei.groesse:
        if _hash_datei(ziel, datei.groesse, fortschritt, stopp) == datei.sha256:
            return ziel
    teil = ordner / (datei.name + ".part")
    h = hashlib.sha256()
    start = 0
    if teil.is_file():
        start = teil.stat().st_size
        if start > datei.groesse:
            teil.unlink()
            start = 0
        else:
            h = _hash_datei(teil, datei.groesse, fortschritt, stopp, nur_hash=False)
    s = session or _session()
    try:
        kopf = {"Range": f"bytes={start}-"} if start else {}
        url = DATEI_URL.format(repo=repo, pfad=datei.pfad)
        with s.stream("GET", url, headers=kopf) as r:
            if start and r.status_code == 200:          # Server setzt nicht fort: von vorn
                start, h = 0, hashlib.sha256()
            elif r.status_code not in (200, 206):
                raise OSError(f"Hugging Face antwortet mit {r.status_code}")
            with open(teil, "ab" if start else "wb") as f:
                erledigt = start
                for stueck in r.iter_bytes(_BLOCK):
                    if stopp.is_set():
                        raise Abbruch()
                    f.write(stueck)
                    h.update(stueck)
                    erledigt += len(stueck)
                    fortschritt("lade", erledigt, datei.groesse)
    finally:
        if session is None:
            s.close()
    groesse = teil.stat().st_size
    if groesse != datei.groesse or h.hexdigest() != datei.sha256:
        teil.unlink()
        raise PruefFehler(f"{datei.name}: Größe oder SHA-256 stimmen nicht mit Hugging Face überein "
                          "– die Datei wurde verworfen.")
    teil.replace(ziel)
    return ziel


def _hash_datei(p: Path, gesamt: int, fortschritt, stopp: threading.Event, nur_hash: bool = True):
    h = hashlib.sha256()
    erledigt = 0
    with open(p, "rb") as f:
        while stueck := f.read(_BLOCK):
            if stopp.is_set():
                raise Abbruch()
            h.update(stueck)
            erledigt += len(stueck)
            fortschritt("pruefe", erledigt, gesamt)
    return h.hexdigest() if nur_hash else h
