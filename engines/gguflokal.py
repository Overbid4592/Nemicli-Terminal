"""
gguflokal.py - Lokale Sprachmodelle über den eigenen GGUF-Motor (ggufengine).

Kein Server, kein Kindprozess: das Modell läuft im NemiCLI-Prozess auf torch.
Referenz: `gguf:<Ordner>` – jeder Unterordner von `ModelGGUF/` im Programm-Ordner
ist ein Modell (die .gguf darin; eine .gguf mit „mmproj“ im Namen ist der Bild-/Audio-Teil).

Der Motor bleibt nach dem ersten Laden im Speicher, damit der Präfix-Cache wirkt:
Systemprompt und bisheriges Gespräch werden nur einmal gelesen. Der Systemprompt
bleibt dafür ohne Gedächtnis-Treffer; die stehen vor der Nutzernachricht.

Bildermalen: `gpu_fuer_bild()` lagert das Sprachmodell für die Dauer in den RAM
aus, das Bildmodell hat die GPU allein; danach kommt es zurück.

Sehen: liegt eine mmproj-Datei im Modell-Ordner, kann das Modell Bilder ansehen.
Der Bild-Encoder bleibt im RAM und kommt nur zum Kodieren eines Bildes auf die GPU.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
import time
import weakref
from pathlib import Path

try:
    import debugbericht as _DBG
except Exception:                                    # Selbsttest ohne core/ im Pfad
    class _DBG:                                      # type: ignore[no-redef]
        @staticmethod
        def ereignis(*a, **k): pass

        @staticmethod
        def fehler(*a, **k): pass

        @staticmethod
        def motor_log(): pass

        class messen:
            def __init__(self, *a): pass

            def __enter__(self): return self

            def __exit__(self, *a): return False
from typing import AsyncIterator, NamedTuple

import gespraechsstand as GS

try:
    import gpuzustand
except Exception:                                    # Selbsttest ohne core/ im Pfad
    class gpuzustand:                                # type: ignore[no-redef]
        @staticmethod
        def kaputt(): return False

        @staticmethod
        def sicherstellen(): pass

        @staticmethod
        def pruefen(*a): return False

try:
    from paths import INSTALL as _INSTALL
except Exception:                                    # Selbsttest ohne Bootstrap
    _INSTALL = Path(__file__).resolve().parent.parent

ORDNER = _INSTALL / "ModelGGUF"
PROVIDER = "gguf"
VORGABE_KONTEXT = 25600                # 25k: schnelles Vorlesen des Verlaufs, wenig VRAM; mehr über /kontext
VORGABE_TOK_S = 30
MAX_ANTWORT = 8192
ANLEITUNG_MARKE = GS.ANLEITUNG_MARKE
# Stehen Treffer oder Anleitungen vor der Nachricht, markiert das, worauf geantwortet wird.
NACHRICHT_MARKE = GS.NACHRICHT_MARKE
KURZ_OHNE_SUCHE = GS.KURZ_OHNE_SUCHE
# Werkzeug-Ergebnisse vor der letzten echten Nachricht: nur der Anfang bleibt im Kontext
ERGEBNIS_LANG = 1200                # ab so vielen Zeichen wird gekürzt
ERGEBNIS_ANFANG = 400               # so viel bleibt stehen
# Stand des Gesprächs: Zusammenfassung des ausgelagerten Teils, vor der ersten Nachricht (gespraechsstand)
STAND_MARKE = GS.MARKE
STAND_MAX = GS.MAX_ZEICHEN
STAND_SYSTEM = GS.SYSTEM
# Doppelpuffer: Platz für Nachrichten = Kontext ohne Anleitung (Systemprompt, Werkzeuge) und ohne
# Antwort-Reserve. Belegen die Nachrichten nach einer Antwort mehr als PUFFER_AB davon, räumt NemiCLI im
# Hintergrund auf – Älteres in den Stand des Gesprächs, die neuesten (bis PUFFER_BEHALTEN des Platzes)
# bleiben – und liest den neuen Kontext schon vor. Der Abstand dazwischen ist Puffer für die nächsten
# Runden; die nächste Nachricht liest nur sich.
PUFFER_AB = GS.PUFFER_AB
PUFFER_BEHALTEN = GS.PUFFER_BEHALTEN


def _ergebnis_kurz(text: str) -> str:
    """Langes Werkzeug-Ergebnis → Anfang + Vermerk. Nachgereichte Anleitungen und Gedächtnis-
    Treffer davor (bis NACHRICHT_MARKE) bleiben ganz."""
    vor, marke, nach = text.rpartition(NACHRICHT_MARKE)
    if not marke:
        vor, nach = "", text
    teile = []
    for block in re.split(r"(?m)^(?=Ergebnis von ')", nach):
        if len(block) > ERGEBNIS_LANG:
            block = (block[:ERGEBNIS_ANFANG].rstrip() + f"\n… [{len(block) - ERGEBNIS_ANFANG:,} Zeichen gekürzt – das "
                     "ganze Ergebnis steht im Chat-Verlauf (gedaechtnis_suchen) oder erneut über das "
                     "Werkzeug]\n").replace(",", ".")
        teile.append(block)
    return vor + marke + "".join(teile)

# Ein fertiger Werkzeug-Block: Zaun, JSON mit "tool", schließender Zaun
_BLOCK_FERTIG = re.compile(r'```(?:aktion|json)?[ \t]*\n\s*\{.*?"tool".*?\}\s*```', re.DOTALL)

_lock = threading.Lock()                             # ein Modell, eine Rechnung zur Zeit
_lade_lock = threading.Lock()                        # ein Ladevorgang zur Zeit
_stopp = threading.Event()                           # entladen() läuft an: Hintergrund-Arbeit hört auf
_motor = None                                        # (Pfad, Kontext, Engine)
# Jedes entladen() zählt hoch: ein Ladevorgang, der währenddessen lief (Modellwechsel mitten im Laden),
# behält sein Modell danach nicht, sonst bliebe es im Grafikspeicher, obwohl es keiner mehr will.
_abbestellt = 0


# ---------------------------------------------------------------------------
# Einstellungen und Modelle
# ---------------------------------------------------------------------------

def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


KONTEXT_STUFEN = (8192, 16384, 25600, 32768, 65536, 131072)


def kontext(name: str | None = None) -> int:
    """Eingestellter Kontext (`gguf_kontext`, 0 = max); mit `name` höchstens so viel,
    wie das Modell kann."""
    try:
        wert = int(_config().get("gguf_kontext", VORGABE_KONTEXT))
    except (TypeError, ValueError):
        wert = VORGABE_KONTEXT
    grenze = kontext_max(name) if name else None
    if wert <= 0:
        return grenze or VORGABE_KONTEXT
    wert = max(2048, wert)
    return min(wert, grenze) if grenze else wert


def setze_kontext(token: int) -> None:
    """0 = so viel, wie das Modell kann."""
    import config
    config.update(gguf_kontext=int(token))


_kennwerte: dict = {}              # (Datei, Änderungszeit) -> (max, KV/Token, Gewichte im VRAM)


def _kopf_werte(name: str) -> tuple[int | None, int | None, int | None, int | None]:
    """(Kontextgrenze, KV-Bytes pro Token, Gewichte im VRAM, davon Experten) aus dem Kopf der
    Modelldatei, je Datei gemerkt."""
    datei = modelle().get(name)
    if datei is None:
        return None, None, None, None
    schluessel = (str(datei), datei.stat().st_mtime)
    if schluessel not in _kennwerte:
        try:
            from ggufengine.gguf import GGUFFile
            gg = GGUFFile(str(datei))
            try:
                grenze = gg.get(f"{gg.architecture}.context_length")
                _kennwerte[schluessel] = (int(grenze) if grenze else None, _kv_bytes(gg), _gewichte_bytes(gg),
                                          _gewichte_bytes(gg, nur_experten=True))
            finally:
                gg.close()
        except Exception:
            _kennwerte[schluessel] = (None, None, None, None)
    return _kennwerte[schluessel]


def kontext_max(name: str) -> int | None:
    return _kopf_werte(name)[0]


def kv_bytes_pro_token(name: str, bits: int = 16) -> int | None:
    """Wie viel der Gesprächsspeicher (KV) pro Token Kontext belegt – nur die Schichten,
    die mit dem Kontext wachsen (Gemma 4: Voll-Schichten mit eigenem KV, Qwen3.5: jede
    `full_attention_interval`-te Schicht); mit 8 Bit int8 plus eine bf16-Skala je 32 Werte."""
    bf16 = _kopf_werte(name)[1]
    return bf16 * 17 // 32 if bf16 and bits == 8 else bf16


def werkzeug_nativ() -> bool:
    """Werkzeuge in der Muttersprache des Modells (`gguf_werkzeug_nativ`, Vorgabe an): Modelle mit
    eigenem Werkzeug-Format in der Vorlage rufen Werkzeuge darin auf (engines/werkzeugsprache.py).
    Gemessen mit Qwen3.5-9B, 12 Aufgaben: 12/12 statt 8/12 im ```aktion-Format."""
    return bool(_config().get("gguf_werkzeug_nativ", True))


def sprache(engine):
    """Der Werkzeug-Dialekt des geladenen Modells (None: ```aktion wie bisher)."""
    if not werkzeug_nativ():
        return None
    if not hasattr(engine, "_nemicli_sprache"):
        try:
            import werkzeugsprache
            engine._nemicli_sprache = werkzeugsprache.erkennen(engine.gg.get("tokenizer.chat_template"))
        except Exception:
            engine._nemicli_sprache = None
    return engine._nemicli_sprache


def system_nachricht(system: str, dialekt=None):
    """Die System-Nachricht für die Engine; mit Dialekt samt Werkzeugliste und Beispielen in der
    Muttersprache des Modells (als Teile: die Marken des Formats werden Sonder-Token)."""
    from ggufengine import Message
    if dialekt is None:
        return Message("system", system)
    import werkzeugsprache
    liste = werkzeugsprache.werkzeug_liste()
    teile = dialekt.system_teile(system, liste)
    return Message("system", werkzeugsprache.als_text(teile), teile=teile, danach=dialekt.system_danach(liste))


def doppelpuffer() -> bool:
    """Hintergrund-Aufräumen ab PUFFER_AB (`gguf_doppelpuffer`, Preview, Vorgabe an; gilt auch für Ollama)."""
    return bool(_config().get("gguf_doppelpuffer", True))


def kv_gpu_token(name: str, ctx: int, frei: int | None = None, budget: int | None = None,
                 schichten: int | None = None, bits: int = 16) -> int | None:
    """Wie viele Positionen des Gesprächsspeichers in den freien VRAM passen (None: alle `ctx`)."""
    datei = modelle().get(name)
    pro_token = kv_bytes_pro_token(name, bits)
    frei = _frei_vram() if frei is None else frei
    if frei is None or datei is None or not pro_token:
        return None
    passt = max(1024, (frei - _gewichte_im_vram(name, datei, budget, schichten) - RESERVE) // pro_token)
    return None if passt >= ctx else int(passt)


def experten_vram(name: str) -> int | None:
    """Anteil der MoE-Experten an `gewichte_vram`."""
    return _kopf_werte(name)[3]


def gewichte_vram(name: str) -> int | None:
    """VRAM der Gewichte in der Engine (nicht die Dateigröße, s. `_gewichte_bytes`)."""
    return _kopf_werte(name)[2]


def _gewichte_bytes(gg, nur_experten: bool = False) -> int | None:
    """Gewichte, wie die Engine sie im VRAM ablegt (ggufengine.schichten.vram_bytes).
    `nur_experten`: nur die MoE-Experten (`*_exps`)."""
    try:
        from ggufengine import schichten
        return schichten.gewichte_bytes(gg, nur_experten)
    except Exception:
        return None


def _kv_bytes(gg) -> int | None:
    try:
        a = gg.architecture
        n = int(gg.require(f"{a}.block_count"))
        if a == "deepseek2":                      # MLA: je Schicht nur Latent + RoPE-Teil (als K und V)
            breite = int(gg.require(f"{a}.attention.kv_lora_rank")) + int(gg.require(f"{a}.rope.dimension_count"))
            return n * breite * 2 * 2
        kv = gg.get(f"{a}.attention.head_count_kv", gg.get(f"{a}.attention.head_count", 1))
        kv = [int(x) for x in kv] if isinstance(kv, list) else [int(kv)] * n       # je Schicht möglich
        d = int(gg.get(f"{a}.attention.key_length", 0))
        if not d:
            koepfe = gg.require(f"{a}.attention.head_count")
            d = int(gg.require(f"{a}.embedding_length")) // int(koepfe[0] if isinstance(koepfe, list) else koepfe)
        if a == "gemma4":
            fenster = gg.require(f"{a}.attention.sliding_window_pattern")
            eigene = n - int(gg.get(f"{a}.attention.shared_kv_layers", 0))
            schichten = [i for i in range(eigene) if not fenster[i]]
        elif a in ("qwen35", "qwen35moe", "qwen3next"):          # nur jede n-te Schicht mit Attention
            schritt = int(gg.get(f"{a}.full_attention_interval", 4))
            schichten = [i for i in range(n) if (i + 1) % schritt == 0]
        else:
            schichten = range(n)
        return sum(kv[i] for i in schichten) * d * 2 * 2
    except Exception:
        return None


def tok_s() -> float | None:
    """Obergrenze Token/s (schont die Karte); 0 = ohne Grenze."""
    try:
        wert = float(_config().get("gguf_tok_s", VORGABE_TOK_S))
    except (TypeError, ValueError):
        wert = VORGABE_TOK_S
    return wert if wert > 0 else None


def denken() -> bool:
    return bool(_config().get("gguf_denken", False))


VORGABE_DENK_BUDGET = 8192


def denk_budget() -> int | None:
    """Höchstens so viele Denk-Token je Antwort, dann wird der Denkblock geschlossen und das
    Modell antwortet (Config `gguf_denk_budget`, 0 = ohne Grenze)."""
    try:
        wert = int(_config().get("gguf_denk_budget", VORGABE_DENK_BUDGET))
    except (TypeError, ValueError):
        wert = VORGABE_DENK_BUDGET
    return wert if wert > 0 else None


def _ist_mmproj(p: Path) -> bool:
    """Bild-/Audio-Teil: „mmproj“ irgendwo im Namen (mmproj-….gguf, …-BF16-mmproj.gguf)."""
    return "mmproj" in p.name.lower()


def _ist_beiwerk(p: Path) -> bool:
    """Keine Sprachmodell-Datei: Bild-Teil, Wichtigkeits-Matrix der Quantisierung (imatrix…gguf),
    eigener MTP-Kopf (mtp-….gguf, der Motor nimmt den aus der Modelldatei)."""
    name = p.name.lower()
    return _ist_mmproj(p) or "imatrix" in name or name.startswith("mtp-")


def _gguf_in(ordner: Path) -> Path | None:
    """Die Sprachmodell-Datei eines Modell-Ordners (Beiwerk wie mmproj ausgenommen)."""
    dateien = sorted(p for p in ordner.glob("*.gguf") if not _ist_beiwerk(p))
    return dateien[0] if dateien else None


def modelle() -> dict[str, Path]:
    """{Ordnername: .gguf} für alle Unterordner von ModelGGUF/ mit einer Modelldatei."""
    if not ORDNER.is_dir():
        return {}
    raus = {}
    for d in sorted(ORDNER.iterdir()):
        if d.is_dir() and (datei := _gguf_in(d)) is not None:
            raus[d.name] = datei
    return raus


def _mmproj(name: str) -> Path | None:
    datei = modelle().get(name)
    if datei is None:
        return None
    treffer = sorted(p for p in datei.parent.glob("*.gguf") if _ist_mmproj(p))
    return treffer[0] if treffer else None


_mmproj_typ: dict = {}                                # (Datei, Änderungszeit) -> Projektor-Typ


def sieht(name: str) -> bool:
    """Kann das Modell Bilder ansehen? Nur mit einer mmproj, deren Bild-Encoder der
    Motor kennt (vision.ENCODERS, aus der Datei erkannt) – andere bleiben unbenutzt."""
    mm = _mmproj(name)
    return mm is not None and projektor_bekannt(mm)


def projektor_bekannt(mm: Path) -> bool:
    """Kennt der Motor den Bild-Encoder dieser mmproj?"""
    schluessel = (str(mm), mm.stat().st_mtime)
    if schluessel not in _mmproj_typ:
        try:
            from ggufengine.gguf import GGUFFile
            from ggufengine.vision import projector_type
            gg = GGUFFile(str(mm))
            try:
                _mmproj_typ[schluessel] = projector_type(gg)
            finally:
                gg.close()
        except Exception:
            _mmproj_typ[schluessel] = None
    from ggufengine.vision import ENCODERS
    return _mmproj_typ[schluessel] in ENCODERS


def beschreibung(name: str) -> str:
    datei = modelle().get(name)
    if datei is None:
        return name
    gb = datei.stat().st_size / 2**30
    sehen = "  ·  👁 sieht Bilder" if sieht(name) else ""
    return f"{name}  ·  {datei.name}  ·  {gb:.1f} GB{sehen}"


# ---------------------------------------------------------------------------
# Motor laden / behalten / entladen
# ---------------------------------------------------------------------------

def _torch_bereit() -> None:
    import extlibs
    zustand = extlibs.enable()
    if not zustand.get("aktiv"):
        raise RuntimeError("Für lokale Modelle fehlt torch: " + zustand.get("grund", "") +
                           " – /einrichten installiert es.")


RESERVE = int(1.5 * 2**30)


def _frei_vram() -> int | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        # plus den Vorrat, den torch in diesem Prozess hält, aber nicht nutzt (z. B. nach Embeddings)
        return torch.cuda.mem_get_info()[0] + max(0, torch.cuda.memory_reserved() - torch.cuda.memory_allocated())
    except Exception:
        return None


def experten_budget(name: str, ctx: int, frei: int | None = None, bits: int = 16) -> int | None:
    """VRAM für MoE-Experten (None: alle im VRAM, 0: alle im RAM). `gguf_experten` in der Config:
    "vram", "ram" oder "auto" – passt das Modell samt Kontext nicht, bleiben so viele Experten im
    VRAM, wie neben den übrigen Gewichten, dem Kontext, dem Bild-Teil und der Reserve Platz haben."""
    modus = str(_config().get("gguf_experten", "auto")).lower()
    if modus == "vram":
        return None
    gesamt, experten = gewichte_vram(name), experten_vram(name)
    if not experten or gesamt is None:
        return None
    if modus == "ram":
        return 0
    frei = _frei_vram() if frei is None else frei
    if frei is None:
        return None
    kv = (kv_bytes_pro_token(name, bits) or 0) * ctx + _sehen_bytes(name)
    if gesamt + kv + RESERVE <= frei:
        return None
    return max(0, frei - (gesamt - experten) - kv - RESERVE)


SCHICHTEN_MODI = ("auto", "vram", "ram")


def schichten_modus() -> str:
    """Wo die Schichten dichter Modelle liegen. Vorgabe "auto" (kein Menüpunkt): erst der
    eingestellte Kontext, was vom Modell daneben nicht in den Grafikspeicher passt, bleibt im RAM
    (die GPU holt es über PCIe, langsamer). Nur über die Config (`gguf_schichten`): "vram" – alles
    in der Grafikkarte, der Kontext wird kleiner; "ram" – alles Auslagerbare im RAM."""
    modus = str(_config().get("gguf_schichten", "auto")).lower()
    return modus if modus in SCHICHTEN_MODI else "auto"


def modell_ort(name: str) -> str:
    """Wo das geladene Modell liegt, in Worten (für /kontext)."""
    if _motor is None or _motor[0] != modelle().get(name):
        return "wird beim Laden automatisch verteilt"
    engine = _motor[2]
    plan = getattr(engine, "schichten_plan", None)
    lager = engine._lager() if hasattr(engine, "_lager") else []
    if lager:
        ort = (f"Experten-Lager – je Schicht {lager[0].plaetze} von {lager[0].n_exp} Experten in der Grafikkarte "
               "(die meistgebrauchten, lernt mit), der Rest im RAM")
    elif plan is None or not plan.ram:
        ort = "ganz in der Grafikkarte – schnell"
    else:
        ort = (f"Hybrid – {plan.ram_bytes / 2**30:.1f} GB im RAM, die Grafikkarte holt sie über PCIe "
               "(langsamer, dafür passt der ganze Kontext)")
    ort += f"  ·  Gesprächsspeicher {getattr(engine, 'kv_bits_wahl', 16)} Bit"
    if (kv := getattr(engine, "kv_im_vram", None)):
        ort += f", davon {kv // 1024}k in der Grafikkarte, der Rest im RAM"
    return ort


def _sehen_bytes(name: str) -> int:
    """Grafikspeicher des Bild-Übersetzers (mmproj), wenn das Modell sieht."""
    try:
        mm = _mmproj(name) if sieht(name) else None
        return mm.stat().st_size if mm is not None else 0
    except Exception:
        return 0


def schichten_budget(name: str, ctx: int, frei: int | None = None, bits: int = 16) -> int | None:
    """VRAM für die Gewichte eines dichten Modells (None: alle im VRAM, 0: alles Auslagerbare im
    RAM), siehe `schichten_modus`. MoE-Modelle haben das Experten-Budget."""
    modus = schichten_modus()
    if modus == "vram" or experten_vram(name) or modelle().get(name) is None:
        return None
    if modus == "ram":
        return 0
    gesamt = gewichte_vram(name)
    frei = _frei_vram() if frei is None else frei
    if gesamt is None or frei is None:
        return None
    kv = (kv_bytes_pro_token(name, bits) or 0) * ctx
    platz = frei - kv - RESERVE - _sehen_bytes(name)
    return None if gesamt <= platz else max(0, platz)


def schichten_plan(name: str, budget: int | None):
    """Plan der Engine für `budget` (ggufengine.schichten.Plan) oder None."""
    datei = modelle().get(name)
    if budget is None or datei is None:
        return None
    try:
        from ggufengine import schichten
        from ggufengine.gguf import GGUFFile
        gg = GGUFFile(str(datei))
        try:
            return schichten.planen(gg, budget)
        finally:
            gg.close()
    except Exception:
        return None


def _gewichte_im_vram(name: str, datei, budget: int | None, schichten: int | None) -> int:
    """Gewichte im VRAM samt Bild-Teil (mmproj), nach Experten- und Schichten-Budget."""
    gewichte = gewichte_vram(name) or datei.stat().st_size
    if budget is not None and (experten := experten_vram(name)):
        gewichte = gewichte - experten + min(experten, budget)
    if (plan := schichten_plan(name, schichten)) is not None:
        gewichte = min(gewichte, plan.vram_bytes)
    return gewichte + _sehen_bytes(name)


def passender_kontext(name: str, ctx: int, frei: int | None = None, budget: int | None = None,
                      schichten: int | None = None, bits: int = 16) -> int:
    """Größte Stufe bis `ctx`, deren Gesprächsspeicher neben dem Modell in den freien
    VRAM passt (Gewichte wie die Engine sie ablegt, Experten nur bis `budget`, Schichten nach
    `schichten_budget`, 1,5 GB Reserve). Ohne GPU-Angaben: `ctx`."""
    datei = modelle().get(name)
    pro_token = kv_bytes_pro_token(name, bits)
    frei = _frei_vram() if frei is None else frei
    if frei is None or datei is None or not pro_token:
        return ctx
    platz = frei - _gewichte_im_vram(name, datei, budget, schichten) - RESERVE
    if pro_token * ctx <= platz:
        return ctx
    stufen = [s for s in KONTEXT_STUFEN if s < ctx and pro_token * s <= platz]
    return stufen[-1] if stufen else min(ctx, 4096)


class Verteilung(NamedTuple):
    bits: int                    # Gesprächsspeicher 16 oder 8 Bit
    experten: int | None         # VRAM-Budget der MoE-Experten (None: alle im VRAM)
    schichten: int | None        # VRAM-Budget der Gewichte dichter Modelle (None: alle im VRAM)
    n_ctx: int
    kv_vram: int | None          # Positionen des Gesprächsspeichers im VRAM (None: alle)


def verteilen(name: str, ctx: int, frei: int | None = None) -> Verteilung:
    """Der Erkenner: die schnellste Verteilung, mit der der eingestellte Kontext ganz gilt.
    1. alles in der Grafikkarte, Gesprächsspeicher 16 Bit
    2. alles in der Grafikkarte, Gesprächsspeicher 8 Bit (halber Platz, kaum langsamer)
    3. 8 Bit, Experten oder Schichten so weit nötig im RAM (über PCIe, langsamer)
    4. reicht auch das nicht: ein Teil des Gesprächsspeichers im RAM (ohne CUDA-Graphen)"""
    frei = _frei_vram() if frei is None else frei
    for bits in (16, 8):
        if passender_kontext(name, ctx, frei, bits=bits) >= ctx:
            return Verteilung(bits, None, None, ctx, None)
    budget, schicht = experten_budget(name, ctx, frei, 8), schichten_budget(name, ctx, frei, 8)
    if passender_kontext(name, ctx, frei, budget, schicht, 8) >= ctx:
        return Verteilung(8, budget, schicht, ctx, None)
    return Verteilung(8, budget, schicht, ctx, kv_gpu_token(name, ctx, frei, budget, schicht, 8))


def antwort_reserve(n_ctx: int) -> int:
    """So viel Kontext bleibt für die Antwort frei (bei 25k gut 4k)."""
    return min(MAX_ANTWORT, max(2048, n_ctx // 6))


def wirksamer_kontext(name: str) -> int:
    """Kontext, mit dem das Modell wirklich läuft (nach der VRAM-Automatik)."""
    if _motor is not None and _motor[0] == modelle().get(name):
        return _motor[2].n_ctx
    return kontext(name)


def _schluessel(name: str) -> tuple:
    """Einstellungen, mit denen eine geladene Engine weiterverwendet werden kann."""
    return kontext(name), str(_config().get("gguf_experten", "auto")).lower(), schichten_modus()


def motor(name: str):
    """Die geladene Engine für Modell `name` (lädt beim ersten Mal, ~10 s). Ein Ladevorgang zur Zeit:
    eine Antwort und das Vorbereiten im Hintergrund laden nie zwei Modelle nebeneinander."""
    with _lade_lock:
        return _motor_laden(name)


def _motor_laden(name: str):
    global _motor
    datei = modelle().get(name)
    if datei is None:
        raise RuntimeError(f"Kein Modell '{name}' in {ORDNER} (Ordner mit einer .gguf darin).")
    ctx = kontext(name)
    if _motor is not None and _motor[0] == datei and _motor[1] == _schluessel(name):
        return _motor[2]
    gpuzustand.sicherstellen()
    entladen()
    auftrag = _abbestellt
    _torch_bereit()
    from ggufengine import Engine
    frei = _frei_vram()
    v = verteilen(name, ctx, frei)
    if frei is not None and v != Verteilung(16, None, None, ctx, None):
        frei = _stimme_platz_machen() or frei     # die Stimme räumt, dann neu verteilen
        v = verteilen(name, ctx, frei)
    _DBG.motor_log()
    _DBG.ereignis(f"Modell laden: {name} (Kontext {v.n_ctx}, Gesprächsspeicher {v.bits} Bit, KV im VRAM "
                  f"{v.kv_vram}, frei {frei}, Experten-Budget {v.experten}, Schichten-Budget {v.schichten})")
    meldung = None
    try:
        engine = Engine(str(datei), n_ctx=v.n_ctx, verbose=False, max_tok_s=tok_s(), kv_bits=v.bits,
                        experts_vram=v.experten, kv_gpu_tokens=v.kv_vram, weights_vram=v.schichten)
    except Exception as e:
        # Nur den Text behalten: der Traceback hielte die halb geladene Engine (Datei-Abbild, festgenagelter
        # Speicher, Tensoren) fest – unter Windows bliebe sogar die GGUF-Datei gesperrt.
        meldung, art = str(e) or type(e).__name__, type(e)
    if meldung is not None:
        _nach_fehlschlag_aufraeumen()
        raise (art if art in (RuntimeError, ValueError, MemoryError, OSError) else RuntimeError)(meldung)
    engine.kv_im_vram = v.kv_vram
    engine.kv_bits_wahl = v.bits
    _DBG.ereignis(f"Modell geladen: {name}")
    if sieht(name) and (mm := _mmproj(name)) is not None:
        try:
            engine.enable_vision(str(mm))
        except Exception:
            engine.vision = None                  # Modell ohne Bildeingang: nur Text
    if experten_laden(name, engine):
        _DBG.ereignis("Experten-Lager: Zählung vom letzten Mal übernommen")
    _trt_anbinden(engine, datei)
    _motor = (datei, _schluessel(name), engine)
    if _abbestellt != auftrag:                       # während des Ladens abgewählt: gleich wieder frei
        del engine
        entladen("während des Ladens abgewählt")
        raise MotorWeg(f"{name} wurde während des Ladens abgewählt")
    _leerlauf_starten()
    _lora_abgleich(engine, datei)
    return engine


def _trt_anbinden(engine, datei) -> None:
    """TensorRT-RTX abgelegt (Models/TensorRT-RTX): lange Prompts liest ein TensorRT-Netz, die Antwort
    schreibt der GGUF-Motor (trtmotor.chat). Abschalten: Config trt_chat = false."""
    if not _config().get("trt_chat", True):
        return
    try:
        from trtmotor import chat as TC
        grund = TC.anbinden(engine, datei)
        if grund:
            _DBG.ereignis(f"TensorRT-Leser: {grund}")
    except Exception as exc:
        _DBG.ereignis(f"TensorRT-Leser nicht angebunden: {exc}")


def _lora_abgleich(engine, datei) -> None:
    """Coding-Assistent an: der LoRA-Motor klinkt das Coding-LoRA dieses Modells an, aus: wieder ab."""
    try:
        from loramotor import adapter
        if adapter.abgleich(engine, datei):
            _DBG.ereignis(f"Coding-LoRA: {getattr(engine, '_nemicli_lora_soll', '') or 'abgeklinkt'}")
    except Exception as exc:
        _DBG.ereignis(f"Coding-LoRA-Abgleich fehlgeschlagen: {exc}")


def _stimme_platz_machen() -> int | None:
    """Passt das Modell mit dem eingestellten Kontext nicht neben die geladene Stimme, räumt sie
    die GPU (sie lädt beim nächsten Satz neu). Gibt den neuen freien VRAM zurück."""
    import sys
    st = sys.modules.get("stimme")
    if st is None or getattr(st, "_modell", None) is None:
        return None
    try:
        st.gpu_freigeben()
    except Exception:
        return None
    return _frei_vram()


def geladen() -> bool:
    return _motor is not None


class MotorWeg(RuntimeError):
    """Das Modell wurde inzwischen entladen (oder wird es gerade) – die Arbeit hört still auf."""


# Leihe (wie Ollamas refCount): wer mit dem Modell rechnet, leiht es sich; entladen() nimmt keine neuen Leihen
# mehr an, gibt das Stopp-Signal und wartet, bis alle zurück sind.
_leih = threading.Condition()
_leihen = 0
_entladen_laeuft = False
_zuletzt = time.monotonic()                          # letzte Leihe (fürs Entladen im Leerlauf)
_runden = 0                                          # laufende Runden: zwischen zwei Leihen kein Leerlauf
LEIHE_WARTEN_S = 5.0                                 # so lange wartet entladen() auf laufende Arbeit
LEERLAUF_PRUEFEN_S = 30.0


def _nach_fehlschlag_aufraeumen() -> None:
    """Nach einem gescheiterten Laden: Reste einsammeln, festgenagelten Speicher und CUDA-Zwischenspeicher frei."""
    import gc
    gc.collect()
    try:
        import gpuzustand
        if gpuzustand.kaputt():
            return
    except Exception:
        pass
    try:
        import torch
        from ggufengine.models.common import release_pinned_memory
        release_pinned_memory()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


@contextlib.contextmanager
def geliehen(engine):
    """`with geliehen(engine):` – rechnen mit dem geladenen Modell. MotorWeg, wenn es nicht mehr das geladene
    ist oder gerade entladen wird."""
    global _leihen, _zuletzt
    with _leih:
        if _entladen_laeuft or _motor is None or _motor[2] is not engine:
            raise MotorWeg("Modell inzwischen entladen")
        _leihen += 1
        _zuletzt = time.monotonic()
    try:
        yield engine
    finally:
        with _leih:
            _leihen -= 1
            _zuletzt = time.monotonic()
            _leih.notify_all()


@contextlib.contextmanager
def versucht_geliehen(engine):
    """Wie geliehen(), ohne Ausnahme: liefert False, wenn das Modell nicht mehr das geladene ist."""
    try:
        leihe = geliehen(engine)
        leihe.__enter__()
    except MotorWeg:
        yield False
        return
    try:
        yield True
    finally:
        leihe.__exit__(None, None, None)


@contextlib.contextmanager
def runde():
    """Eine Runde (Antwort mit Werkzeugen) läuft: das Modell wird in der Zeit nicht wegen Leerlaufs entladen."""
    global _runden
    with _ZAEHLER_SPERRE:
        _runden += 1
    try:
        yield
    finally:
        with _ZAEHLER_SPERRE:
            _runden -= 1


def _belegt() -> int:
    try:
        import torch
        return torch.cuda.memory_allocated() if torch.cuda.is_available() else 0
    except Exception:
        return 0


def entladen(grund: str = "") -> None:
    """Gibt VRAM und RAM des Sprachmodells frei (Modellwechsel, Leerlauf), samt Bildbeschreiber – wie Ollama:
    keine neuen Leihen, laufende Arbeit hört auf (Stopp-Signal) und wird abgewartet (höchstens LEIHE_WARTEN_S),
    dann wird die Engine geleert. Ihr Speicher ist damit frei, auch wenn noch irgendwo jemand sie hält."""
    global _motor, _entladen_laeuft, _abbestellt
    _abbestellt += 1                                 # auch ohne geladenes Modell: ein laufendes Laden behält es nicht
    if gpuzustand.kaputt():                          # Freigeben in einer unbrauchbaren CUDA-Sitzung kann abstürzen
        if _motor is not None:
            _DBG.ereignis(f"Entladen übersprungen{' (' + grund + ')' if grund else ''}: CUDA-Sitzung unbrauchbar")
        return
    try:
        import bildbeschreiber
        bildbeschreiber.entladen()
    except Exception:
        pass
    if _motor is None:
        return
    t0, vorher = time.monotonic(), _belegt()
    with _leih:
        _entladen_laeuft = True
    _stopp.set()                                     # Doppelpuffer und Hintergrund-Arbeit hören auf
    try:
        with _leih:
            _leih.wait_for(lambda: _leihen == 0, timeout=LEIHE_WARTEN_S)
            offen = _leihen
        with _lock:
            if _motor is None:
                return
            datei, _, engine = _motor
            _motor = None
            try:
                from trtmotor import chat as TC
                TC.abbinden(engine)
            except Exception:
                pass
            try:
                experten_speichern(Path(datei).parent.name, engine)
            except Exception:                        # nur ein Vorteil fürs nächste Laden
                pass
            try:
                engine.leeren()
            except Exception as exc:
                _DBG.fehler("Entladen: leeren", exc)
            gg = getattr(engine, "gg", None)
            del engine
            import gc
            gc.collect()                             # erst ohne die Tensoren lässt sich die Datei schließen
            try:
                if gg is not None:
                    gg.close()
            except Exception as exc:                 # noch eine Sicht auf die Datei: sie bleibt offen
                _DBG.fehler("Entladen: Modelldatei schließen", exc)
    finally:
        _stopp.clear()
        with _leih:
            _entladen_laeuft = False
    try:
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
        from ggufengine.models.common import release_pinned_memory
        release_pinned_memory()                      # Experten im RAM, ausgelagerte Gewichte
    except Exception:
        pass
    frei = max(0, vorher - _belegt())
    text = f"Modell entladen{' (' + grund + ')' if grund else ''}: {frei / 2**30:.1f} GB zurück in " \
           f"{time.monotonic() - t0:.1f} s"
    if offen:
        text += f" – {offen} Arbeit(en) hörten nach {LEIHE_WARTEN_S:.0f} s nicht auf, trotzdem geleert"
        try:
            import fehlerwaechter
            fehlerwaechter.befund("entladen", f"{offen} Arbeit(en) hörten beim Entladen nicht auf das Stopp-Signal")
        except Exception:
            pass
    _DBG.ereignis(text)


def leerlauf_minuten() -> int:
    """Config `gguf_leerlauf_min`: so viele Minuten ohne Arbeit, dann wird das Modell entladen (0 = nie)."""
    try:
        return max(0, min(1440, int(_config().get("gguf_leerlauf_min", 0) or 0)))
    except (TypeError, ValueError):
        return 0


def leerlauf_pruefen() -> bool:
    """Einmal nachsehen: Modell geladen, niemand rechnet, keine Runde, kein Bild, lange genug still? Dann
    entladen. True, wenn entladen wurde."""
    minuten = leerlauf_minuten()
    if not minuten or _motor is None or _leihen or _runden or _bild_tiefe:
        return False
    if time.monotonic() - _zuletzt < minuten * 60:
        return False
    entladen(f"{minuten} Min. ohne Arbeit")
    return True


_leerlauf_faden: threading.Thread | None = None


def _leerlauf_starten() -> None:
    global _leerlauf_faden
    if _leerlauf_faden is not None and _leerlauf_faden.is_alive():
        return

    def lauf():
        while True:
            time.sleep(LEERLAUF_PRUEFEN_S)
            try:
                leerlauf_pruefen()
            except Exception as exc:
                _DBG.fehler("Leerlauf-Entladen", exc)
    _leerlauf_faden = threading.Thread(target=lauf, daemon=True, name="gguf-leerlauf")
    _leerlauf_faden.start()


_bild_tiefe = 0
_ZAEHLER_SPERRE = threading.Lock()


@contextlib.contextmanager
def gpu_fuer_bild():
    """Beim Bildermalen: Sprachmodell in den RAM, Bildmodell hat die GPU allein,
    danach zurück auf die GPU. Ein Embedding-Modell auf der GPU wird entladen.
    Verschachtelt zählt nur der äußerste Aufruf."""
    global _bild_tiefe
    gpuzustand.sicherstellen()
    if _bild_tiefe == 0:
        try:
            import memory
            memory.gpu_freigeben()                   # Embedding-Modell auf der GPU räumt auch
        except Exception:
            pass
    engine = _motor[2] if _motor is not None else None
    if engine is None:
        yield
        return
    with _ZAEHLER_SPERRE:                            # Bildserie und Chat-Bild können gleichzeitig malen
        _bild_tiefe += 1
        erster = _bild_tiefe == 1
    if erster:
        with versucht_geliehen(engine) as da, _lock:
            if da:
                engine.offload()
        if da:
            _DBG.ereignis("Sprachmodell für das Bild in den RAM")
    try:
        yield
    finally:
        with _ZAEHLER_SPERRE:
            _bild_tiefe -= 1
            letzter = _bild_tiefe == 0
        if letzter:
            with versucht_geliehen(engine) as da, _lock:
                if da:                               # inzwischen entladen: nichts zurückzuholen
                    engine.restore()
            if da:
                _DBG.ereignis("Sprachmodell zurück auf die GPU")


# ---------------------------------------------------------------------------
# Denktext abtrennen (Gemma 4: <|channel>thought … <channel|>)
# ---------------------------------------------------------------------------

class _Denktrenner:
    """Trennt Denktext von der Antwort: Gemma `<|channel>thought…<channel|>`,
    K2 `<ifm|think>…</ifm|think>`, gpt-oss Kanal `analysis` bis zum Beginn von `final`,
    Mistral `[THINK]…[/THINK]`, sonst `<think>…</think>`. `offen`: der Prompt hat den Denkblock schon geöffnet, die
    Antwort beginnt darin."""
    AUF, ZU = "<|channel>", "<channel|>"

    KANAL = "thought"

    def __init__(self, format: str = "gemma4", offen: bool = False):
        if format == "k2":
            self.AUF, self.ZU, self.KANAL = "<ifm|think>", "</ifm|think>", ""
        elif format == "harmony":
            from ggufengine.chat import HARMONY_FINAL
            self.AUF, self.ZU, self.KANAL = "<|channel|>analysis<|message|>", HARMONY_FINAL, ""
        elif format.startswith("mistral"):                  # Magistral / Ministral 3 Reasoning
            self.AUF, self.ZU, self.KANAL = "[THINK]", "[/THINK]", ""
        elif format == "seed_oss":
            self.AUF, self.ZU, self.KANAL = "<seed:think>", "</seed:think>", ""
        elif format != "gemma4":
            self.AUF, self.ZU, self.KANAL = "<think>", "</think>", ""
        self.denkt = offen
        self.anfang = False
        self.rest = ""
        self.kopf = ""                               # Anfang des Denkkanals, bis der Name klar ist

    def __call__(self, stueck: str) -> list[tuple[str, str]]:
        raus: list[tuple[str, str]] = []
        text = self.rest + stueck
        self.rest = ""
        while text:
            marke = self.ZU if self.denkt else self.AUF
            i = text.find(marke)
            if i < 0:
                # eine angefangene Marke am Ende zurückhalten
                for n in range(min(len(marke) - 1, len(text)), 0, -1):
                    if marke.startswith(text[-n:]):
                        self.rest, text = text[-n:], text[:-n]
                        break
                self._raus(raus, text)
                break
            self._raus(raus, text[:i])
            if self.denkt and self.kopf and (kopf := self._kopf_fertig()):
                raus.append(("thinking", kopf))
            text = text[i + len(marke):]
            self.denkt = not self.denkt
            self.anfang = self.denkt
        return raus

    def _raus(self, raus, text):
        if not text:
            return
        if self.denkt and self.anfang:               # Kanalname "thought" am Anfang
            self.kopf += text
            kopf = self.kopf.lstrip()
            if len(kopf) <= len(self.KANAL) and self.KANAL.startswith(kopf):
                return                               # noch unklar: weiter sammeln
            text = self._kopf_fertig()
            if not text:
                return
        raus.append(("thinking" if self.denkt else "text", text))

    def _kopf_fertig(self) -> str:
        kopf, self.kopf = self.kopf.lstrip(), ""
        self.anfang = False
        if kopf.startswith(self.KANAL):
            kopf = kopf[len(self.KANAL):]
        return kopf.lstrip("\n")

    def ende(self) -> list[tuple[str, str]]:
        raus = []
        if self.kopf:
            kopf = self._kopf_fertig()
            if kopf:
                raus.append(("thinking", kopf))
        rest, self.rest = self.rest, ""
        if rest:
            raus.append(("thinking" if self.denkt else "text", rest))
        return raus


class _Kettentrenner:
    """Erst die eigenen Denk-Marken des Modells, im Antworttext danach <think>…</think> (Denkwerk:
    erbetenes Nachdenken bei Modellen, deren eigene Marken anders heißen)."""

    def __init__(self, erster: _Denktrenner):
        self.erster, self.zweiter = erster, _Denktrenner("plain")

    def _weiter(self, teile) -> list[tuple[str, str]]:
        raus: list[tuple[str, str]] = []
        for art, text in teile:
            raus += [(art, text)] if art == "thinking" else self.zweiter(text)
        return raus

    def __call__(self, stueck: str) -> list[tuple[str, str]]:
        return self._weiter(self.erster(stueck))

    def ende(self) -> list[tuple[str, str]]:
        return self._weiter(self.erster.ende()) + self.zweiter.ende()


def _trenner(engine):
    fmt = engine.chat_format
    fmt.thinking = denken()
    eigen = _Denktrenner(fmt.fmt, offen=fmt.opens_thinking)
    return eigen if eigen.AUF == "<think>" else _Kettentrenner(eigen)


def _ist_ergebnis(inhalt) -> bool:
    """Werkzeug-Ergebnisse, die NemiCLI als Nutzernachricht zurückschickt (keine echte Nachricht)."""
    return isinstance(inhalt, str) and inhalt.startswith("Ergebnis von '")


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class _Zusatz:
    """Daten je Nachricht: erzeugte Token, gesendeter Text (mit Gedächtnis/Anleitung),
    Bild-Embeddings. Zugeordnet über die Nachricht selbst (Objekt-Identität), nicht über
    ihren Text – zwei gleiche Texte (z. B. zweimal nur ein Bild abgelegt) bleiben getrennt."""

    def __init__(self):
        self._eintraege: list = []                   # [(nachricht, daten)]

    def von(self, nachricht: dict) -> dict:
        for m, d in self._eintraege:
            if m is nachricht:
                return d
        d: dict = {}
        self._eintraege.append((nachricht, d))
        return d

    def get(self, nachricht: dict, schluessel: str, standard=None):
        for m, d in self._eintraege:
            if m is nachricht:
                return d.get(schluessel, standard)
        return standard

    def aufraeumen(self, verlauf: list) -> None:
        """Einträge von Nachrichten, die nicht mehr im Verlauf stehen, freigeben."""
        self._eintraege = [(m, d) for m, d in self._eintraege if any(m is v for v in verlauf)]

    def clear(self) -> None:
        self._eintraege.clear()


class GgufChat:
    """Backend wie cloud.CloudChat: messages, reset, stream, ask_once, ask_messages."""

    def __init__(self, name: str):
        self.name = name
        self.model = f"{PROVIDER}:{name}"
        self.messages: list[dict] = []
        self.strength = None
        self._zusatz = _Zusatz()                     # je Nachricht: ids, gesendet, bilder
        self.stand = ""                              # Zusammenfassung des ausgelagerten Teils
        self._puffer: asyncio.Task | None = None    # laufendes Hintergrund-Aufräumen / Vorlesen
        self._puffer_hinweis = ""
        self._puffer_system = ""                     # Anleitung der letzten Antwort (fürs Vorlesen)
        self._kompakt_text = ""                      # Bilanz des letzten Aufräumens (/compact)
        self._platz_cache: tuple = (None, (0, 0))
        self.denk_hinweis = ""                       # Denkwerk: nur mit der nächsten gesendeten Nachricht

    @property
    def denkt_selbst(self) -> bool:
        """Eigenes Nachdenken des Modells ist eingeschaltet (Config `gguf_denken`)."""
        return denken()

    async def schliessen(self, warten_s: float = 5.0) -> None:
        """Modellwechsel: die Hintergrund-Arbeit (Doppelpuffer: Vorlesen, Zusammenfassen) anhalten – sie hält das
        Modell sonst fest, und entladen() gäbe den Grafikspeicher nicht frei. Erst das Stopp-Signal (ihr Faden
        hört nur darauf), dann abbrechen; höchstens `warten_s` warten, danach geht der Wechsel trotzdem weiter
        (entladen() räumt nach). Das Signal gilt nur fürs Warten: scheitert der Wechsel, arbeitet der Puffer weiter."""
        puffer, self._puffer = self._puffer, None
        if puffer is None or puffer.done():
            return
        _stopp.set()
        try:
            puffer.cancel()
            await asyncio.wait({puffer}, timeout=warten_s)
        finally:
            _stopp.clear()

    def reset(self) -> None:
        self.messages = []
        self._zusatz.clear()
        self.stand = ""
        if self._puffer is not None and not self._puffer.done():
            self._puffer.cancel()
        self._puffer, self._puffer_hinweis, self._puffer_system = None, "", ""

    # -- intern ---------------------------------------------------------------
    def _anleitungen_fuer(self, user_text: str) -> str:
        """Anleitungen, die zu dieser Nachricht passen und im Verlauf noch fehlen (gespraechsstand)."""
        return GS.anleitungen_fuer(user_text, [self._zusatz.get(m, "gesendet", "") for m in self.messages
                                               if m.get("role") == "user"])

    @staticmethod
    def _letzte_echte(verlauf: list[dict]) -> int:
        return max((i for i, m in enumerate(verlauf) if m.get("role") == "user"
                    and not _ist_ergebnis(m.get("content"))), default=-1)

    def _nachrichten(self, system: str, verlauf: list[dict], denk_weg: bool = False, stand: bool = False,
                     dialekt=None):
        """Verlauf für die Engine. Antworten tragen ihre erzeugten Token (samt Denktext).
        `denk_weg` (Vorlage verwirft alten Denktext): bei Antworten vor der letzten echten
        Nutzernachricht fällt er weg; innerhalb der laufenden Werkzeug-Kette (nur
        Werkzeug-Ergebnisse seither) bleibt er – das Modell kennt so noch den Grund für
        jeden Schritt (interleaved thinking, wie in den Vorlagen von Qwen3, GLM-4.5, gpt-oss).
        Werkzeug-Ergebnisse vor der letzten echten Nachricht sind gekürzt (`_ergebnis_kurz`);
        mit `stand` steht der Stand des Gesprächs vor der ersten Nachricht. Mit `dialekt` (Muttersprache
        des Modells): Antworten als Text und Werkzeug-Ergebnisse in seinem Werkzeug-Format."""
        from ggufengine import Message
        if dialekt is not None:
            import werkzeugsprache
        raus = [system_nachricht(system, dialekt)] if system else []
        letzte_echte = self._letzte_echte(verlauf)
        erste = next((i for i, m in enumerate(verlauf) if m.get("role") == "user"), -1) if stand and self.stand else -1
        for i, m in enumerate(verlauf):
            rolle, inhalt = m.get("role"), m.get("content")
            if not isinstance(inhalt, str):                  # Bilder o. Ä.: nur der Text
                inhalt = " ".join(b.get("text", "") for b in inhalt or [] if isinstance(b, dict))
            if rolle == "assistant":
                ids = self._zusatz.get(m, "ids")
                if denk_weg and i < letzte_echte and self._zusatz.get(m, "gedacht"):
                    ids = None                               # als Text, ohne Denktext
                if dialekt is not None and ids is None:
                    teile = werkzeugsprache.aktion_teile(dialekt, inhalt)
                    raus.append(Message("assistant", werkzeugsprache.als_text(teile), teile=teile))
                    continue
                raus.append(Message("assistant", inhalt, ids=ids))
            elif rolle == "user":
                text = self._zusatz.get(m, "gesendet", inhalt)
                if i < letzte_echte and _ist_ergebnis(inhalt):
                    text = _ergebnis_kurz(text)
                if i == erste:
                    text = STAND_MARKE + self.stand + "\n\n" + text
                if dialekt is not None and _ist_ergebnis(inhalt):
                    teile = dialekt.ergebnisse_teile(text)
                    raus.append(Message("user", werkzeugsprache.als_text(teile), images=self._zusatz.get(m, "bilder"),
                                        teile=teile, werkzeug_antwort=True))
                    continue
                raus.append(Message("user", text, images=self._zusatz.get(m, "bilder")))
        return raus

    def sichtbar(self) -> list[dict]:
        """Der Verlauf, wie das Modell ihn sieht: ältere Werkzeug-Ergebnisse gekürzt."""
        letzte = self._letzte_echte(self.messages)
        return [dict(m, content=_ergebnis_kurz(m["content"])) if i < letzte and _ist_ergebnis(m.get("content"))
                else m for i, m in enumerate(self.messages)]

    def _kontext_melden(self) -> None:
        """Für die Suche „vorhin in diesem Chat“: was wirklich im Kontext steht (gekürzte
        Ergebnisse nur mit ihrem Anfang – der Rest ist so wieder auffindbar)."""
        try:
            import chatstore
            chatstore.im_kontext_setzen(self.sichtbar())
        except Exception:
            pass

    async def _stand_fortschreiben(self, weg: list[dict], n_ctx: int, hintergrund: bool = False,
                                   fehler_melden: bool = False) -> bool:
        """Stand des Gesprächs um die ausgelagerten Nachrichten `weg` ergänzen (eigener Modell-
        Aufruf ohne Denken). Schlägt er fehl, bleibt der alte Stand. True = fortgeschrieben.
        `fehler_melden`: Fehler des Modell-Aufrufs weitergeben statt False (/compact)."""
        grenze = max(4000, int((n_ctx - antwort_reserve(n_ctx) - 1000) * 2.5))   # Zeichen Eingabe

        async def fragen(system: str, text: str) -> str:
            return await self._einmal(system, [{"role": "user", "content": text}], max_tokens=GS.ANTWORT_TOKEN,
                                      denken_aus=True, hintergrund=hintergrund)
        try:
            neu = await GS.fortschreiben(fragen, self.stand, weg, grenze, kuerzen=_ergebnis_kurz)
        except Exception:
            if fehler_melden:
                raise
            return False
        if not neu:
            return False
        self.stand = neu
        return True

    def _behalten_ab(self, budget: int) -> int:
        """Index der ersten Nachricht, die stehen bleibt: die neuesten, zusammen höchstens `budget`
        Token (so wie sie gesendet werden), mindestens die letzten zwei."""
        import pricing
        return GS.behalten_ab(self.messages, budget,
                              lambda m: pricing.est_tokens(self._zusatz.get(m, "gesendet", m.get("content"))))

    def _naechster_anfang(self, system: str, engine) -> list:
        """Der Anfang des nächsten Prompts – so dargestellt, wie ihn die nächste echte Nachricht
        sieht (ältere Ergebnisse gekürzt, alter Denktext weg)."""
        platzhalter = {"role": "user", "content": ""}
        return self._nachrichten(system, self.messages + [platzhalter], engine.chat_format.drops_old_thinking,
                                 stand=True, dialekt=sprache(engine))[:-1]

    async def _vorlesen(self, engine, system: str) -> int:
        anfang = self._naechster_anfang(system, engine)

        def lesen():
            with versucht_geliehen(engine) as da, _lock:
                if (not da or _stopp.is_set() or gpuzustand.kaputt()               # entladen / Wechsel / GPU-Fehler
                        or _bild_tiefe or engine.offloaded):                       # ein Bild entsteht gerade
                    return 0
                engine.chat_format.thinking = denken()
                with _DBG.messen("Doppelpuffer: vorlesen"):
                    return engine.vorlesen(anfang, abbruch=_stopp.is_set)
        return await asyncio.to_thread(lesen)

    async def _aufraeumen(self, engine, system: str, behalten: float = PUFFER_BEHALTEN,
                          kompakt: bool = False) -> int:
        """Doppelpuffer: Älteres in den Stand des Gesprächs (und über `weggefallen` ins Gedächtnis),
        die neuesten Nachrichten bleiben (`behalten` des Platzes), der neue Kontext wird schon vorgelesen.
        `kompakt` (/compact): Fehler beim Zusammenfassen gehen an den Aufrufer. Gibt die Zahl der
        ausgelagerten Nachrichten zurück."""
        import pricing
        t0, vorher = time.time(), engine._pos
        alt = self.messages
        n = pricing.schnitt(alt, self._behalten_ab(int(behalten * self._platz(engine, system)[1])))
        if n <= 0:
            return 0
        _DBG.ereignis(f"{'/compact' if kompakt else 'Doppelpuffer'}: {n} Nachrichten in den Stand, "
                      f"{len(alt) - n} bleiben")
        # erst der Stand, dann der Schnitt: bricht die Zusammenfassung ab (Entladen, Bild), fehlt nichts
        if (not await self._stand_fortschreiben(alt[:n], engine.n_ctx, hintergrund=True, fehler_melden=kompakt)
                or self.messages is not alt):
            if kompakt:
                raise RuntimeError("Das Modell hat keine Zusammenfassung geschrieben – der Verlauf bleibt, wie er war.")
            return 0
        gezaehlt = getattr(self, "aufgeraeumt", 0)
        pricing.abschneiden(self, n)
        self.aufgeraeumt = gezaehlt                  # eigener Hinweis statt „Kontext voll“
        self._zusatz.aufraeumen(self.messages)
        gelesen = await self._vorlesen(engine, system)
        self._kontext_melden()
        text = (f"{n} ältere Nachrichten stehen jetzt im Stand des Gesprächs (und im Gedächtnis), "
                f"{len(self.messages)} bleiben – {vorher:,} → {engine._pos:,} Token, {gelesen:,} vorgelesen in "
                f"{time.time() - t0:.1f} s.").replace(",", ".")
        self._puffer_hinweis = ("" if kompakt else "📌 Kontext im Hintergrund aufgeräumt: " + text)
        self._kompakt_text = text
        return n

    async def kompakt(self) -> str:
        """/compact: jetzt zusammenfassen – Älteres in den Stand des Gesprächs, nur die neuesten
        Nachrichten (KOMPAKT_BEHALTEN des Platzes, mindestens zwei) bleiben. "" = nichts zu tun."""
        import persona
        async for _ in self._puffer_abwarten():
            pass
        if len(self.messages) < 3:
            return ""
        with runde():
            engine = await self._laden()
            system = await persona.build_system_prompt_async("", kompakt_fuer_lokal=True, mit_gedaechtnis=False)
            n = await self._aufraeumen(engine, system, behalten=GS.KOMPAKT_BEHALTEN, kompakt=True)
            self._puffer_system = system
        return f"📌 Zusammengefasst: {self._kompakt_text}" if n else ""

    def _hintergrund(self, engine, system: str, aufraeumen: bool) -> None:
        """Aufräumen (ab PUFFER_AB) oder nur den Gesprächsanfang wieder vorlesen – im Hintergrund,
        hinter einer schon laufenden Arbeit; die nächste Nachricht wartet darauf (`_puffer_abwarten`)."""
        if not doppelpuffer() or not system or not self.messages or gpuzustand.kaputt():
            return
        if aufraeumen:
            anleitung, platz = self._platz(engine, system)
            aufraeumen = engine._pos - anleitung > PUFFER_AB * platz
        vorher = self._puffer

        async def laufen():
            if vorher is not None:
                with contextlib.suppress(Exception):
                    await vorher
            if _motor is None or _motor[2] is not engine or _bild_tiefe or gpuzustand.kaputt():
                return                                 # entladen, gewechselt, Bild, GPU-Fehler: nichts tun
            try:
                if aufraeumen:
                    await self._aufraeumen(engine, system)
                else:
                    await self._vorlesen(engine, system)
            except (asyncio.CancelledError, MotorWeg):
                return
            except Exception as exc:                   # ein Bonus: nie das Gespräch stören
                _DBG.fehler("Doppelpuffer", exc)
                self._puffer_hinweis = f"⚠ Kontext im Hintergrund vorbereiten ging nicht: {exc}"
        self._puffer = asyncio.get_running_loop().create_task(laufen())

    def _platz(self, engine, system: str) -> tuple[int, int]:
        """(Anleitung, Platz für Nachrichten) in Token: der Kontext ohne die Anleitung und ohne die
        Antwort-Reserve, gezählt mit dem Tokenizer des Modells."""
        schluessel = (id(engine), engine.n_ctx, system)
        if self._platz_cache[0] != schluessel:
            fmt = engine.chat_format
            anleitung = len(fmt.encode([system_nachricht(system, sprache(engine))], add_generation_prompt=False))
            platz = max(1024, engine.n_ctx - anleitung - antwort_reserve(engine.n_ctx))
            self._platz_cache = (schluessel, (anleitung, platz))
        return self._platz_cache[1]

    def vorbereiten(self, fertig=None) -> bool:
        """Passt der Verlauf nicht in den Kontext (nach einem Modellwechsel, kleinerem Kontext, einem
        fortgesetzten Gespräch): Modell im Hintergrund laden und gleich aufräumen wie der Doppelpuffer,
        statt bis zur nächsten Nachricht zu warten. Die nächste Nachricht wartet darauf. `fertig` wird
        danach aufgerufen. True = gestartet."""
        if (not doppelpuffer() or not self.messages or gpuzustand.kaputt()
                or (self._puffer is not None and not self._puffer.done())):
            return False
        try:
            import hintergrund
            if hintergrund.gpu_belegt():               # Video/Bild-Serie rechnet: nicht dazwischen laden
                return False
        except ImportError:
            pass
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False

        async def laufen():
            import persona
            try:
                engine = await self._laden()
                system = await persona.build_system_prompt_async("", kompakt_fuer_lokal=True, mit_gedaechtnis=False)
                await self._aufraeumen(engine, system)
                self._puffer_system = system
            except (asyncio.CancelledError, MotorWeg):
                return
            except Exception as exc:                   # die nächste Nachricht räumt dann selbst auf
                _DBG.fehler("Kontext vorbereiten", exc)
            if fertig is not None:
                with contextlib.suppress(Exception):
                    fertig()
        self._puffer = loop.create_task(laufen())
        return True

    def _nach_nebenfrage(self) -> None:
        """Eine Einzelfrage (Reflexion, Recherche, Spiele …) hat den Zwischenspeicher mit ihrem
        eigenen Prompt belegt: den Gesprächsanfang im Hintergrund wieder vorlesen."""
        if _motor is not None and _motor[0] == modelle().get(self.name) and self._puffer_system:
            self._hintergrund(_motor[2], self._puffer_system, aufraeumen=False)

    async def _puffer_abwarten(self) -> AsyncIterator[dict]:
        """Vor einer neuen Nachricht: ein laufendes Aufräumen erst fertig werden lassen."""
        if self._puffer is not None:
            if not self._puffer.done():
                yield {"type": "note", "text": "📌 einen Moment – der Kontext wird gerade vorbereitet …"}
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._puffer
            self._puffer = None
        if self._puffer_hinweis:
            yield {"type": "note", "text": self._puffer_hinweis}
            self._puffer_hinweis = ""

    def _einpassen(self, engine, system: str) -> None:
        """Anleitung + Verlauf + Platz für die Antwort müssen in den Kontext passen – gezählt mit
        dem Tokenizer des Modells. Sonst fallen die ältesten Nachrichten heraus (ins Gedächtnis,
        wie beim normalen Aufräumen). Passt nicht einmal die Anleitung: klare Meldung statt Absturz."""
        import pricing
        n_ctx, reserve = engine.n_ctx, antwort_reserve(engine.n_ctx)
        fmt = engine.chat_format
        anleitung = len(fmt.encode([system_nachricht(system, sprache(engine))], add_generation_prompt=False))
        if anleitung + reserve + 512 > n_ctx:
            raise RuntimeError(
                f"Der Kontext ({n_ctx // 1024}k) ist zu klein: die Anleitung allein braucht ~{anleitung:,} Token "
                f"und für die Antwort bleiben {reserve:,} frei. /kontext höher stellen oder Grafikspeicher "
                "freimachen.".replace(",", "."))

        def laenge() -> int:
            return len(fmt.encode(self._nachrichten(system, self.messages, fmt.drops_old_thinking, stand=True,
                                                    dialekt=sprache(engine))))
        while laenge() + reserve > n_ctx and len(self.messages) > 1:
            if not pricing.abschneiden(self, max(1, len(self.messages) // 4)):
                break
        if laenge() + reserve > n_ctx:
            raise RuntimeError(f"Die Nachricht ist zu lang für den Kontext ({n_ctx // 1024}k) – kürzer fassen "
                               "oder /kontext höher stellen.")

    @staticmethod
    def _sampler(engine):
        from ggufengine import SamplerConfig
        # Floskel-Bremse (DRY): früh und kräftig, nur am Wortanfang – schwächer oder mitten
        # im Wort weicht das Modell auf Tippfehler aus („Freundn“, „Präfix-Cape“)
        dry = dict(dry_multiplier=2.5, dry_allowed_length=1)
        if engine.chat_format.fmt == "deepseek":             # Vorgabe von DeepSeek-R1
            return SamplerConfig(temperature=0.6, top_p=0.95, top_k=0, min_p=0.0, **dry)
        if engine.chat_format.fmt == "bailing":              # Vorgabe von Ling 3.0
            return SamplerConfig(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, **dry)
        return SamplerConfig(temperature=1.0, top_p=0.95, top_k=64, min_p=0.0, **dry)

    def _geladen(self):
        """Der Motor, wenn er schon mit diesem Modell (und diesen Einstellungen) läuft – lädt nie nach."""
        if _motor is not None and _motor[0] == modelle().get(self.name) and _motor[1] == _schluessel(self.name):
            return _motor[2]
        return None

    async def _laden(self):
        if (engine := self._geladen()) is not None:
            return engine
        return await asyncio.to_thread(motor, self.name)

    async def _erzeugen(self, engine, nachrichten, max_tokens, abbruch: threading.Event, denken_aus: bool = False,
                        hintergrund: bool = False, sprache=None):
        """Lässt die Engine in einem Thread laufen und reicht die Textstücke weiter. Im Hintergrund
        (Doppelpuffer) endet sie, sobald entladen() anläuft, und wartet nicht auf ein Bild.
        Mit `sprache` (Werkzeug-Dialekt) gehen Aufrufe in der Muttersprache als ```aktion-Block weiter."""
        loop = asyncio.get_running_loop()
        schlange: asyncio.Queue = asyncio.Queue()
        ENDE = object()

        def arbeiten():
            try:
                gpuzustand.sicherstellen()
                while _bild_tiefe and not abbruch.is_set():   # das Bildmodell hat die GPU allein
                    if hintergrund:
                        raise RuntimeError("Ein Bild entsteht gerade")
                    time.sleep(0.2)
                with geliehen(engine), _lock:
                    if hintergrund and _stopp.is_set():
                        raise MotorWeg("Modell wird entladen")
                    if not hintergrund and _motor is not None and _motor[2] is engine:
                        _lora_abgleich(engine, _motor[0])
                    engine.chat_format.thinking = False if denken_aus else denken()
                    engine.denk_budget = denk_budget()
                    gen = engine.chat(nachrichten, max_tokens=max_tokens, sampler=self._sampler(engine))
                    bisher = ""
                    import werkzeugsprache
                    strom = werkzeugsprache.Strom(sprache) if sprache is not None else None
                    try:
                        for stueck in gen:
                            if abbruch.is_set() or (hintergrund and _stopp.is_set()):
                                break
                            if strom is not None:
                                stueck = strom(stueck)
                            if stueck:
                                loop.call_soon_threadsafe(schlange.put_nowait, stueck)
                            # Nach einem fertigen Werkzeug-Block ist Schluss: was danach käme,
                            # wären vorweggenommene oder erfundene Ergebnisse.
                            bisher += stueck
                            if (strom is not None and strom.aufrufe) or ("```" in stueck and werkzeug_block_fertig(bisher)):
                                break
                    finally:
                        gen.close()
                        if strom is not None and (rest := strom.ende()):
                            loop.call_soon_threadsafe(schlange.put_nowait, rest)
            except BaseException as exc:                     # an den Aufrufer weiterreichen
                _DBG.fehler("Erzeugen", exc)
                if gpuzustand.pruefen(exc):                  # statt der CUDA-Meldung: was jetzt zu tun ist
                    exc = gpuzustand.GpuKaputt()
                loop.call_soon_threadsafe(schlange.put_nowait, exc)
            loop.call_soon_threadsafe(schlange.put_nowait, ENDE)

        faden = threading.Thread(target=arbeiten, daemon=True)
        faden.start()
        try:
            while True:
                stueck = await schlange.get()
                if stueck is ENDE:
                    break
                if isinstance(stueck, BaseException):
                    raise stueck
                yield stueck
        finally:
            abbruch.set()
            await asyncio.to_thread(faden.join)

    async def _einmal(self, system: str, verlauf: list[dict], max_tokens: int = 2048,
                      denken_aus: bool = False, hintergrund: bool = False) -> str:
        """Ein Aufruf ohne Verlauf. `hintergrund` (Doppelpuffer): nur mit dem schon geladenen Motor,
        nie nachladen; wird er währenddessen entladen, ist das Ergebnis ungültig (Fehler)."""
        if hintergrund:
            engine = self._geladen()
            if engine is None:
                raise RuntimeError("Modell nicht geladen")
        else:
            engine = await self._laden()
        teile, trenner = [], _trenner(engine)
        async for stueck in self._erzeugen(engine, self._nachrichten(system, verlauf, engine.chat_format.drops_old_thinking),
                                           max_tokens, threading.Event(), denken_aus, hintergrund):
            teile += [t for art, t in trenner(stueck) if art == "text"]
        teile += [t for art, t in trenner.ende() if art == "text"]
        if hintergrund and (_stopp.is_set() or self._geladen() is not engine):
            raise MotorWeg("Modell inzwischen entladen")
        return "".join(teile).strip()

    # -- Schnittstelle ----------------------------------------------------------
    async def ask_once(self, prompt: str, system: str) -> str:
        try:
            return await self._einmal(system, [{"role": "user", "content": prompt}])
        finally:
            self._nach_nebenfrage()

    async def ask_messages(self, system: str, messages: list[dict]) -> str:
        try:
            return await self._einmal(system, messages, max_tokens=4096)
        finally:
            self._nach_nebenfrage()

    async def stream(self, user_text: str, images: list[str] | None = None) -> AsyncIterator[dict]:
        with runde():
            async for teil in self._stream(user_text, images):
                yield teil

    async def _stream(self, user_text: str, images: list[str] | None = None) -> AsyncIterator[dict]:
        import persona
        import pricing
        async for hinweis in self._puffer_abwarten():
            yield hinweis
        frage = {"role": "user", "content": user_text}
        self.messages.append(frage)
        bisher = list(self.messages)                 # was ausgelagert wird, kommt in den Stand
        pricing.kuerzen(self)
        self._zusatz.aufraeumen(self.messages)
        if hinweis := pricing.aufraeum_hinweis(self):
            yield {"type": "note", "text": hinweis}
        try:
            # schwacher Verweis: entladen() muss das alte Modell freigeben können
            vorher = weakref.ref(_motor[2]) if _motor is not None else None
            if _motor is None or _motor[0] != modelle().get(self.name):
                yield {"type": "note", "text": f"🧠 lade {self.name} in die Grafikkarte …"}
            zeiten, t = {}, time.time()
            engine = await self._laden()
            zeiten["laden"] = time.time() - t
            frisch = vorher is None or vorher() is not engine   # auch nach geänderten Einstellungen
            if frisch and getattr(engine, "kv_im_vram", None):
                yield {"type": "note", "text": f"🧠 Gesprächsspeicher: {engine.kv_im_vram // 1024}k im Grafikspeicher, "
                                               f"bis {engine.n_ctx // 1024}k im RAM – ab da langsamer."}
            if engine.n_ctx < kontext(self.name):
                yield {"type": "note", "text": f"⚠ Kontext auf {engine.n_ctx // 1024}k begrenzt – mehr passt "
                                               "neben dem Modell nicht in den Grafikspeicher (/kontext)."}
            if images:
                if engine.vision is None:
                    yield {"type": "note", "text": "⚠ Dieses Modell sieht keine Bilder (keine mmproj im "
                                                   "Modell-Ordner) – ich gehe nur auf den Text ein."}
                else:
                    yield {"type": "note", "text": f"👁 sehe mir {len(images)} Bild(er) an …"}
                    self._zusatz.von(frage)["bilder"] = await asyncio.to_thread(_kodieren, engine, images)
            # Systemprompt ohne Gedächtnis-Treffer (bleibt gleich -> Präfix-Cache);
            # die Treffer und nachgereichte Anleitungen gehören zu dieser Nachricht.
            t = time.time()
            system = await persona.build_system_prompt_async("", kompakt_fuer_lokal=True, mit_gedaechtnis=False)
            treffer = await asyncio.to_thread(_gedaechtnis, user_text)
            zeiten["vorbereitung"] = time.time() - t
            denk, self.denk_hinweis = self.denk_hinweis, ""
            vorsatz = [t.strip() for t in (treffer, self._anleitungen_fuer(user_text)) if t and t.strip()]
            if vorsatz or denk:                          # Denkwerk-Hinweis hinter die Nachricht
                gesendet = "\n\n".join(vorsatz + [NACHRICHT_MARKE + user_text]) if vorsatz else user_text
                self._zusatz.von(frage)["gesendet"] = f"{gesendet}\n\n{denk}" if denk else gesendet
            zusammengefasst: set[int] = set()
            for _ in range(3):                           # der neue Stand braucht selbst etwas Platz
                await asyncio.to_thread(self._einpassen, engine, system)
                drin = {id(m) for m in self.messages}
                weg = [m for m in bisher if id(m) not in drin and id(m) not in zusammengefasst]
                if not weg:
                    break
                yield {"type": "note", "text": "📌 Kontext voll – ich schreibe den Stand des Gesprächs fort …"}
                await self._stand_fortschreiben(weg, engine.n_ctx)
                zusammengefasst |= {id(m) for m in weg}
            self._kontext_melden()
            if hinweis := pricing.aufraeum_hinweis(self):
                yield {"type": "note", "text": hinweis}
        except BaseException:
            self.messages.pop()
            raise

        t = time.time()
        if await asyncio.to_thread(prefix_laden, self.name, engine, system):
            yield {"type": "note", "text": "⚡ Anleitung aus dem Zwischenspeicher – kein erneutes Einlesen."}
        zeiten["zwischenspeicher"] = time.time() - t
        trenner, voll, abbruch = _trenner(engine), "", threading.Event()
        gedacht = False
        try:
            t = time.time()
            async for stueck in self._erzeugen(engine, self._nachrichten(system, self.messages,
                                                                         engine.chat_format.drops_old_thinking,
                                                                         stand=True, dialekt=sprache(engine)),
                                               MAX_ANTWORT, abbruch, sprache=sprache(engine)):
                zeiten.setdefault("erstes_token", time.time() - t)
                for art, text in trenner(stueck):
                    if art == "text":
                        voll += text
                    gedacht = gedacht or (art == "thinking" and bool(text.strip()))
                    yield {"type": art, "text": text}
            for art, text in trenner.ende():
                if art == "text":
                    voll += text
                gedacht = gedacht or (art == "thinking" and bool(text.strip()))
                yield {"type": art, "text": text}
        except BaseException:
            abbruch.set()
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            raise
        erzeugt = len(engine.last_ids)
        eingabe = max(0, engine._pos - erzeugt)
        yield {"type": "usage", "input": eingabe, "output": erzeugt}
        if frisch:
            zeiten["gelesen"] = max(0, eingabe - engine.last_cached)
            yield {"type": "note", "text": ladezeiten_text(engine, zeiten)}
        antwort = voll.strip() or "(keine Antwort)"
        erwiderung = {"role": "assistant", "content": antwort}
        # die erzeugten Token samt Denktext; ob er beim nächsten Mal mitgeht, entscheidet _nachrichten
        self._zusatz.von(erwiderung)["ids"] = list(engine.last_ids)
        self._zusatz.von(erwiderung)["gedacht"] = gedacht
        self.messages.append(erwiderung)
        if engine.checkpoint_new:
            await asyncio.to_thread(prefix_speichern, self.name, engine)
        if _hat_lager(engine):
            await asyncio.to_thread(experten_speichern, self.name, engine)
        self._puffer_system = system
        self._hintergrund(engine, system, aufraeumen=True)


LADE_PHASEN = {"file": "Datei", "tokenizer": "Tokenizer", "weights": "Gewichte", "warmup": "Aufwärmen",
               "vision": "Bildteil"}


def _sek(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def ladezeiten_text(engine, zeiten: dict) -> str:
    """Eine Zeile mit der Dauer jedes Schritts vom Laden bis zum ersten Token.
    `zeiten`: laden, vorbereitung, zwischenspeicher, erstes_token (Sekunden), gelesen (Token)."""
    phasen = getattr(engine, "load_phases", {}) or {}
    teile = [f"{LADE_PHASEN.get(k, k)} {_sek(v)}" for k, v in phasen.items()]
    rest = zeiten.get("laden", 0.0) - sum(phasen.values())
    if rest >= 0.05:
        teile.append(f"Sonstiges {_sek(rest)}")
    zeile = f"⏱ Laden {_sek(zeiten.get('laden', 0.0))} s: " + " · ".join(teile)
    danach = [f"Gedächtnis+Anleitung {_sek(zeiten.get('vorbereitung', 0.0))} s",
              f"Zwischenspeicher {_sek(zeiten.get('zwischenspeicher', 0.0))} s"]
    if "erstes_token" in zeiten:
        gelesen = f"{zeiten.get('gelesen', 0):,}".replace(",", ".")
        danach.append(f"{gelesen} Token gelesen bis zum ersten Wort {_sek(zeiten['erstes_token'])} s")
    return zeile + " │ " + " · ".join(danach)


def _prefix_datei(name: str) -> Path | None:
    try:
        from paths import DATEN
    except Exception:
        return None
    return Path(DATEN) / "Cache" / "gguf" / f"{name}.safetensors"


def _prefix_kennung(name: str, engine) -> str:
    """Modelldatei (Name, Größe, Zeitstempel) + Rechenweise – passt sie nicht, wird neu gelesen."""
    datei = modelle().get(name)
    st = datei.stat()
    return (f"{datei.name}|{st.st_size}|{st.st_mtime_ns}|{engine.dtype}|{engine.device.type}"
            f"|{getattr(engine, '_nemicli_lora_soll', '')}")


def _experten_datei(name: str) -> Path | None:
    pfad = _prefix_datei(name)
    return pfad.with_name(f"{name}.experten.json") if pfad is not None else None


def _modell_kennung(name: str) -> str:
    datei = modelle().get(name)
    st = datei.stat()
    return f"{datei.name}|{st.st_size}|{st.st_mtime_ns}"


def _hat_lager(engine) -> bool:
    lager = getattr(engine, "_lager", None)
    return bool(lager()) if callable(lager) else False


def experten_laden(name: str, engine) -> bool:
    """Zählung des Experten-Lagers vom letzten Mal: die meistgebrauchten Experten liegen gleich
    in der Grafikkarte (erste Antwort so schnell wie spätere). Nur für dieselbe Modelldatei."""
    pfad = _experten_datei(name)
    if pfad is None or not pfad.is_file() or not _hat_lager(engine):
        return False
    try:
        if pfad.stat().st_size > 16 * 2**20:
            return False
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        if not isinstance(daten, dict) or daten.get("kennung") != _modell_kennung(name):
            return False
        return engine.experten_zaehlung_setzen(daten.get("zaehlung"))
    except (OSError, ValueError):
        return False


def experten_speichern(name: str, engine) -> bool:
    """Zählung des Experten-Lagers für den nächsten Start sichern (klein: Zahlen je Schicht)."""
    pfad = _experten_datei(name)
    zaehlung = engine.experten_zaehlung() if pfad is not None and _hat_lager(engine) else None
    if zaehlung is None:
        return False
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        neu = pfad.with_suffix(".tmp")
        neu.write_text(json.dumps({"kennung": _modell_kennung(name), "zaehlung": zaehlung}), encoding="utf-8")
        neu.replace(pfad)
        return True
    except OSError:
        return False


def prefix_laden(name: str, engine, system: str) -> bool:
    """Frisch geladenes Modell: gespeicherten Stand nach der Anleitung übernehmen,
    wenn Modell und Anleitung genau passen (spart das erste Einlesen)."""
    pfad = _prefix_datei(name)
    if engine._pos != 0 or pfad is None or not pfad.is_file() or not system:
        return False
    ids = engine.chat_format.encode([system_nachricht(system, sprache(engine))], add_generation_prompt=False)
    # Wie jede Rechnung: geliehen und unter der Sperre – load_prefix setzt KV-Speicher und Ringe zurück.
    with geliehen(engine), _lock:
        if engine._pos != 0:
            return False
        try:
            return engine.load_prefix(str(pfad), _prefix_kennung(name, engine), ids)
        except Exception:
            # Unpassender oder kaputter Stand: weg damit, sonst scheitert jede Nachricht daran.
            engine.reset()
            pfad.unlink(missing_ok=True)
            return False


def prefix_speichern(name: str, engine) -> bool:
    """Neuen Stand nach der Anleitung auf die Platte schreiben (einmal je Anleitung)."""
    pfad = _prefix_datei(name)
    if pfad is None or not engine.checkpoint_new:
        return False
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with geliehen(engine), _lock:
            return engine.save_prefix(str(pfad), _prefix_kennung(name, engine))
    except Exception:
        return False


def werkzeug_block_fertig(text: str) -> bool:
    """Steht im Text ein vollständiger Werkzeug-Block?"""
    return _BLOCK_FERTIG.search(text) is not None


def _bild_aus_uri(uri: str):
    """data:image/…;base64,… -> PIL-Bild (aufgerichtet, Transparenz auf Weiß)."""
    import base64
    import io
    from PIL import Image
    import vision
    kopf, _, daten = uri.partition(",")
    if not kopf.startswith("data:image/") or ";base64" not in kopf:
        raise ValueError("kein Bild (data:image/…;base64 erwartet)")
    return vision.aufrecht(Image.open(io.BytesIO(base64.b64decode(daten))))


def _kodieren(engine, uris: list[str]) -> list:
    """Bilder -> Embeddings; der Bild-Encoder ist nur dafür auf der GPU."""
    with geliehen(engine), _lock:
        return [engine.encode_image(_bild_aus_uri(u)) for u in uris]


def _gedaechtnis(frage: str) -> str:
    """Gedächtnis-Treffer zur Nachricht aus der Suche der Runde (persona, läuft schon seit dem Absenden im
    Hintergrund, gewartet wird höchstens GS.TREFFER_WARTEN_S). Bei Kurzem ohne Frage keine, bei Werkzeug-
    Ergebnissen auch nicht – die Treffer stehen schon an der Nachricht der Runde."""
    if GS.ohne_treffer(frage) or GS.ist_ergebnis(frage):
        return ""
    try:
        import persona
        return persona.gedaechtnis_treffer(frage, GS.TREFFER_WARTEN_S) or ""
    except Exception:
        return ""
