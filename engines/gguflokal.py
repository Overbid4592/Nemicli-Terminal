"""
gguflokal.py - Lokale Sprachmodelle über den eigenen GGUF-Motor (ggufengine).

Kein Server, kein Kindprozess: das Modell läuft im NemiCLI-Prozess auf torch.
Referenz: `gguf:<Ordner>` – jeder Unterordner von `ModelGGUF/` im Programm-Ordner
ist ein Modell (die .gguf darin; eine mmproj-*.gguf ist der Bild-/Audio-Teil).

Der Motor bleibt nach dem ersten Laden im Speicher, damit der Präfix-Cache wirkt:
Systemprompt und bisheriges Gespräch werden nur einmal gelesen. Der Systemprompt
bleibt dafür ohne Gedächtnis-Treffer; die stehen vor der Nutzernachricht.

Bildermalen: `gpu_fuer_bild()` lagert das Sprachmodell für die Dauer in den RAM
aus, das Bildmodell hat die GPU allein; danach kommt es zurück.

Sehen: liegt eine mmproj-*.gguf im Modell-Ordner, kann das Modell Bilder ansehen.
Der Bild-Encoder bleibt im RAM und kommt nur zum Kodieren eines Bildes auf die GPU.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import threading
from pathlib import Path
from typing import AsyncIterator

try:
    from paths import INSTALL as _INSTALL
except Exception:                                    # Selbsttest ohne Bootstrap
    _INSTALL = Path(__file__).resolve().parent.parent

ORDNER = _INSTALL / "ModelGGUF"
PROVIDER = "gguf"
VORGABE_KONTEXT = 131072
VORGABE_TOK_S = 30
MAX_ANTWORT = 8192
ANLEITUNG_MARKE = "📘 Anleitung „{}“ (von NemiCLI nachgereicht, gilt ab jetzt):"
# Stehen Treffer oder Anleitungen vor der Nachricht, markiert das, worauf geantwortet wird.
NACHRICHT_MARKE = "# Die Nachricht deines Gegenübers – darauf antwortest du (alles darüber ist Hintergrund):\n"
KURZ_OHNE_SUCHE = 4                 # weniger Wörter und kein „?“: keine Gedächtnissuche

# Ein fertiger Werkzeug-Block: Zaun, JSON mit "tool", schließender Zaun
_BLOCK_FERTIG = re.compile(r'```(?:aktion|json)?[ \t]*\n\s*\{.*?"tool".*?\}\s*```', re.DOTALL)

_lock = threading.Lock()                             # ein Modell, eine Rechnung zur Zeit
_motor = None                                        # (Pfad, Kontext, Engine)


# ---------------------------------------------------------------------------
# Einstellungen und Modelle
# ---------------------------------------------------------------------------

def _config() -> dict:
    try:
        import config
        return config.load()
    except Exception:
        return {}


KONTEXT_STUFEN = (8192, 16384, 32768, 65536, 131072)


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


def kv_bytes_pro_token(name: str) -> int | None:
    """Wie viel der Gesprächsspeicher (KV) pro Token Kontext belegt – nur die Schichten,
    die mit dem Kontext wachsen (Gemma 4: Voll-Schichten mit eigenem KV, Qwen3.5: jede
    `full_attention_interval`-te Schicht); mit 8 Bit int8 plus eine bf16-Skala je 32 Werte."""
    bf16 = _kopf_werte(name)[1]
    return bf16 * 17 // 32 if bf16 and kv_bits() == 8 else bf16


def kv_bits() -> int:
    """Gesprächsspeicher in 16 Bit (Standard, schneller) oder 8 Bit (`gguf_kv_8bit`, etwa
    halber VRAM je Token, in langen Chats langsamer)."""
    return 8 if _config().get("gguf_kv_8bit") else 16


def setze_kv_bits(bits: int) -> None:
    import config
    config.update(gguf_kv_8bit=bits == 8)


def experten_vram(name: str) -> int | None:
    """Anteil der MoE-Experten an `gewichte_vram`."""
    return _kopf_werte(name)[3]


def gewichte_vram(name: str) -> int | None:
    """VRAM der Gewichte in der Engine (nicht die Dateigröße, s. `_gewichte_bytes`)."""
    return _kopf_werte(name)[2]


_VIER_BIT = {"Q4_0", "Q4_1", "Q4_K"}
_ACHT_BIT = {"Q8_0", "Q5_0", "Q5_1", "Q5_K", "Q6_K"}


def _gewichte_bytes(gg, nur_experten: bool = False) -> int | None:
    """Gewichte, wie `ggufengine.qlinear` sie ablegt: 4-Bit-Blöcke als int4 mit bf16-Skala und
    -Nullpunkt je 32 Werte (5 Bit/Wert), 5- bis 8-Bit-Blöcke als zwei solche Matrizen
    (10 Bit/Wert, Q6_K also mehr als in der Datei), Einbettungstabellen und alle Formate ohne int4-Weg
    (cudakern.IMMER_GEPACKT) unverändert im Blockformat (die Tabelle je Schicht von Gemma 4 im RAM),
    der Rest bf16. `nur_experten`: nur die MoE-Experten (`*_exps`)."""
    try:
        from ggufengine.cudakern import IMMER_GEPACKT
        from ggufengine.gguf import GGML_TYPES
        gebunden = "output.weight" not in gg.tensors
        summe = 0
        for name, ti in gg.tensors.items():
            if nur_experten and not (len(ti.shape) == 3 and "_exps." in name):
                continue
            art, block, _ = GGML_TYPES.get(ti.ggml_type, ("", 1, 0))
            n_aus, n_ein = (ti.shape[-2], ti.shape[-1]) if len(ti.shape) >= 2 else (1, ti.n_elements)
            experten = len(ti.shape) == 3 and "_exps." in name           # gepackt ohne Mindestgröße
            packbar = (art in _VIER_BIT | _ACHT_BIT and n_aus % 8 == 0 and n_ein % 128 == 0
                       and (experten or n_aus * n_ein >= 1 << 20))       # wie qlinear.quantizable
            gepackt = ti.n_elements * (5 if art in _VIER_BIT else 10) // 8
            if name == "per_layer_token_embd.weight" and block > 1:
                continue
            if name.endswith(("attn_k_b.weight", "attn_v_b.weight", "attn_kv_b.weight")):   # MLA: float32
                summe += ti.n_elements * 4
                continue
            if art in IMMER_GEPACKT:                                     # bleiben im Dateiformat
                summe += ti.n_bytes
                continue
            if name == "token_embd.weight" and block > 1 and packbar:
                summe += ti.n_bytes + (gepackt if gebunden else 0)
            elif packbar:
                summe += gepackt
            else:
                summe += ti.n_elements * (4 if len(ti.shape) < 2 else 2)
        return summe
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
        elif a == "qwen35":
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


def _gguf_in(ordner: Path) -> Path | None:
    """Die Sprachmodell-Datei eines Modell-Ordners (mmproj-Dateien ausgenommen)."""
    dateien = sorted(p for p in ordner.glob("*.gguf") if not p.name.lower().startswith("mmproj"))
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
    treffer = sorted(datei.parent.glob("mmproj*.gguf"))
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
KONTEXT_NEBEN_RAM_EXPERTEN = 16384        # so viel Kontext bleibt frei, bevor Experten den VRAM füllen


def _frei_vram() -> int | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        # plus den Vorrat, den torch in diesem Prozess hält, aber nicht nutzt (z. B. nach Embeddings)
        return torch.cuda.mem_get_info()[0] + max(0, torch.cuda.memory_reserved() - torch.cuda.memory_allocated())
    except Exception:
        return None


def experten_budget(name: str, ctx: int, frei: int | None = None) -> int | None:
    """VRAM für MoE-Experten (None: alle im VRAM, 0: alle im RAM). `gguf_experten` in der Config:
    "vram", "ram" oder "auto" – passt das Modell samt Kontext nicht, bleiben so viele Experten im
    VRAM, wie neben den übrigen Gewichten, dem Kontext und der Reserve Platz haben."""
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
    kv = (kv_bytes_pro_token(name) or 0) * min(ctx, KONTEXT_NEBEN_RAM_EXPERTEN)
    if gesamt + kv + RESERVE <= frei:
        return None
    return max(0, frei - (gesamt - experten) - kv - RESERVE)


def passender_kontext(name: str, ctx: int, frei: int | None = None, budget: int | None = None) -> int:
    """Größte Stufe bis `ctx`, deren Gesprächsspeicher neben dem Modell in den freien
    VRAM passt (Gewichte wie die Engine sie ablegt, Experten nur bis `budget`, 1,5 GB Reserve).
    Ohne GPU-Angaben: `ctx`."""
    datei = modelle().get(name)
    pro_token = kv_bytes_pro_token(name)
    frei = _frei_vram() if frei is None else frei
    if frei is None or datei is None or not pro_token:
        return ctx
    gewichte = gewichte_vram(name) or datei.stat().st_size
    if budget is not None and (experten := experten_vram(name)):
        gewichte = gewichte - experten + min(experten, budget)
    platz = frei - gewichte - RESERVE
    if pro_token * ctx <= platz:
        return ctx
    stufen = [s for s in KONTEXT_STUFEN if s < ctx and pro_token * s <= platz]
    return stufen[-1] if stufen else min(ctx, 4096)


def wirksamer_kontext(name: str) -> int:
    """Kontext, mit dem das Modell wirklich läuft (nach der VRAM-Automatik)."""
    if _motor is not None and _motor[0] == modelle().get(name):
        return _motor[2].n_ctx
    return kontext(name)


def _schluessel(name: str) -> tuple:
    """Einstellungen, mit denen eine geladene Engine weiterverwendet werden kann."""
    return kontext(name), kv_bits(), str(_config().get("gguf_experten", "auto")).lower()


def motor(name: str):
    """Die geladene Engine für Modell `name` (lädt beim ersten Mal, ~10 s)."""
    global _motor
    datei = modelle().get(name)
    if datei is None:
        raise RuntimeError(f"Kein Modell '{name}' in {ORDNER} (Ordner mit einer .gguf darin).")
    ctx = kontext(name)
    if _motor is not None and _motor[0] == datei and _motor[1] == _schluessel(name):
        return _motor[2]
    entladen()
    _torch_bereit()
    from ggufengine import Engine
    frei = _frei_vram()
    budget = experten_budget(name, ctx, frei)
    engine = Engine(str(datei), n_ctx=passender_kontext(name, ctx, frei, budget), verbose=False, max_tok_s=tok_s(),
                    kv_bits=kv_bits(), experts_vram=budget)
    if sieht(name) and (mm := _mmproj(name)) is not None:
        try:
            engine.enable_vision(str(mm))
        except Exception:
            engine.vision = None                  # Modell ohne Bildeingang: nur Text
    _motor = (datei, _schluessel(name), engine)
    return engine


def geladen() -> bool:
    return _motor is not None


def entladen() -> None:
    """Gibt VRAM und RAM des Sprachmodells frei (Modellwechsel), samt Bildbeschreiber."""
    global _motor
    try:
        import bildbeschreiber
        bildbeschreiber.entladen()
    except Exception:
        pass
    if _motor is None:
        return
    with _lock:
        engine = _motor[2]
        _motor = None
        try:
            engine.gg.close()
        except Exception:
            pass
        del engine
    try:
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:
        pass


_bild_tiefe = 0


@contextlib.contextmanager
def gpu_fuer_bild():
    """Beim Bildermalen: Sprachmodell in den RAM, Bildmodell hat die GPU allein,
    danach zurück auf die GPU. Ein Embedding-Modell auf der GPU wird entladen.
    Verschachtelt zählt nur der äußerste Aufruf."""
    global _bild_tiefe
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
    _bild_tiefe += 1
    if _bild_tiefe == 1:
        with _lock:
            engine.offload()
    try:
        yield
    finally:
        _bild_tiefe -= 1
        if _bild_tiefe == 0:
            with _lock:
                engine.restore()


# ---------------------------------------------------------------------------
# Denktext abtrennen (Gemma 4: <|channel>thought … <channel|>)
# ---------------------------------------------------------------------------

class _Denktrenner:
    """Trennt Denktext von der Antwort: Gemma `<|channel>thought…<channel|>`,
    K2 `<ifm|think>…</ifm|think>`, gpt-oss Kanal `analysis` bis zum Beginn von `final`,
    sonst `<think>…</think>`. `offen`: der Prompt hat den Denkblock schon geöffnet, die
    Antwort beginnt darin."""
    AUF, ZU = "<|channel>", "<channel|>"

    KANAL = "thought"

    def __init__(self, format: str = "gemma4", offen: bool = False):
        if format == "k2":
            self.AUF, self.ZU, self.KANAL = "<ifm|think>", "</ifm|think>", ""
        elif format == "harmony":
            from ggufengine.chat import HARMONY_FINAL
            self.AUF, self.ZU, self.KANAL = "<|channel|>analysis<|message|>", HARMONY_FINAL, ""
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


def _trenner(engine) -> _Denktrenner:
    fmt = engine.chat_format
    fmt.thinking = denken()
    return _Denktrenner(fmt.fmt, offen=fmt.opens_thinking)


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

    def reset(self) -> None:
        self.messages = []
        self._zusatz.clear()

    # -- intern ---------------------------------------------------------------
    def _anleitungen_fuer(self, user_text: str) -> str:
        """Anleitungen, die zu dieser Nachricht passen und im Verlauf noch fehlen:
        Stichworte des Nutzers und das erste Ergebnis eines Werkzeugs mit eigenem Thema."""
        import persona
        if user_text.startswith("Ergebnis von '"):             # Werkzeug-Ergebnisse, kein Nutzertext
            import re
            werkzeuge = re.findall(r"(?m)^Ergebnis von '(\w+)'", user_text)
            themen = [t for t in map(persona.thema_fuer_werkzeug, werkzeuge) if t]
        else:
            themen = persona.passende_anleitungen(user_text)
        gezeigt = {t for m in self.messages if m.get("role") == "user"
                   for t in persona.ANLEITUNGEN
                   if ANLEITUNG_MARKE.format(t) in self._zusatz.get(m, "gesendet", "")}
        teile = []
        for t in dict.fromkeys(themen):
            if t not in gezeigt:
                teile.append(ANLEITUNG_MARKE.format(t) + "\n" + persona.anleitung(t))
        return "\n\n".join(teile)

    def _nachrichten(self, system: str, verlauf: list[dict], denk_weg: bool = False):
        """Verlauf für die Engine. Antworten tragen ihre erzeugten Token (samt Denktext).
        `denk_weg` (Vorlage verwirft alten Denktext): bei Antworten vor der letzten echten
        Nutzernachricht fällt er weg; innerhalb der laufenden Werkzeug-Kette (nur
        Werkzeug-Ergebnisse seither) bleibt er – das Modell kennt so noch den Grund für
        jeden Schritt (interleaved thinking, wie in den Vorlagen von Qwen3, GLM-4.5, gpt-oss)."""
        from ggufengine import Message
        raus = [Message("system", system)] if system else []
        letzte_echte = max((i for i, m in enumerate(verlauf) if m.get("role") == "user"
                            and not _ist_ergebnis(m.get("content"))), default=-1)
        for i, m in enumerate(verlauf):
            rolle, inhalt = m.get("role"), m.get("content")
            if not isinstance(inhalt, str):                  # Bilder o. Ä.: nur der Text
                inhalt = " ".join(b.get("text", "") for b in inhalt or [] if isinstance(b, dict))
            if rolle == "assistant":
                ids = self._zusatz.get(m, "ids")
                if denk_weg and i < letzte_echte and self._zusatz.get(m, "gedacht"):
                    ids = None                               # als Text, ohne Denktext
                raus.append(Message("assistant", inhalt, ids=ids))
            elif rolle == "user":
                raus.append(Message("user", self._zusatz.get(m, "gesendet", inhalt),
                                    images=self._zusatz.get(m, "bilder")))
        return raus

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

    async def _laden(self):
        if _motor is not None and _motor[0] == modelle().get(self.name) and _motor[1] == _schluessel(self.name):
            return _motor[2]
        return await asyncio.to_thread(motor, self.name)

    async def _erzeugen(self, engine, nachrichten, max_tokens, abbruch: threading.Event):
        """Lässt die Engine in einem Thread laufen und reicht die Textstücke weiter."""
        loop = asyncio.get_running_loop()
        schlange: asyncio.Queue = asyncio.Queue()
        ENDE = object()

        def arbeiten():
            try:
                with _lock:
                    engine.chat_format.thinking = denken()
                    engine.denk_budget = denk_budget()
                    gen = engine.chat(nachrichten, max_tokens=max_tokens, sampler=self._sampler(engine))
                    bisher = ""
                    try:
                        for stueck in gen:
                            if abbruch.is_set():
                                break
                            loop.call_soon_threadsafe(schlange.put_nowait, stueck)
                            # Nach einem fertigen Werkzeug-Block ist Schluss: was danach käme,
                            # wären vorweggenommene oder erfundene Ergebnisse.
                            bisher += stueck
                            if "```" in stueck and werkzeug_block_fertig(bisher):
                                break
                    finally:
                        gen.close()
            except BaseException as exc:                     # an den Aufrufer weiterreichen
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

    async def _einmal(self, system: str, verlauf: list[dict], max_tokens: int = 2048) -> str:
        engine = await self._laden()
        teile, trenner = [], _trenner(engine)
        async for stueck in self._erzeugen(engine, self._nachrichten(system, verlauf, engine.chat_format.drops_old_thinking),
                                           max_tokens, threading.Event()):
            teile += [t for art, t in trenner(stueck) if art == "text"]
        teile += [t for art, t in trenner.ende() if art == "text"]
        return "".join(teile).strip()

    # -- Schnittstelle ----------------------------------------------------------
    async def ask_once(self, prompt: str, system: str) -> str:
        return await self._einmal(system, [{"role": "user", "content": prompt}])

    async def ask_messages(self, system: str, messages: list[dict]) -> str:
        return await self._einmal(system, messages, max_tokens=4096)

    async def stream(self, user_text: str, images: list[str] | None = None) -> AsyncIterator[dict]:
        import persona
        import pricing
        frage = {"role": "user", "content": user_text}
        self.messages.append(frage)
        pricing.kuerzen(self)
        self._zusatz.aufraeumen(self.messages)
        if hinweis := pricing.aufraeum_hinweis(self):
            yield {"type": "note", "text": hinweis}
        try:
            if _motor is None or _motor[0] != modelle().get(self.name):
                yield {"type": "note", "text": f"🧠 lade {self.name} in die Grafikkarte …"}
            engine = await self._laden()
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
            system = await persona.build_system_prompt_async("", kompakt_fuer_lokal=True)
            treffer = await asyncio.to_thread(_gedaechtnis, user_text)
            vorsatz = [t.strip() for t in (treffer, self._anleitungen_fuer(user_text)) if t and t.strip()]
            if vorsatz:
                self._zusatz.von(frage)["gesendet"] = "\n\n".join(vorsatz + [NACHRICHT_MARKE + user_text])
        except BaseException:
            self.messages.pop()
            raise

        if await asyncio.to_thread(prefix_laden, self.name, engine, system):
            yield {"type": "note", "text": "⚡ Anleitung aus dem Zwischenspeicher – kein erneutes Einlesen."}
        trenner, voll, abbruch = _trenner(engine), "", threading.Event()
        gedacht = False
        try:
            async for stueck in self._erzeugen(engine, self._nachrichten(system, self.messages,
                                                                         engine.chat_format.drops_old_thinking),
                                               MAX_ANTWORT, abbruch):
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
        yield {"type": "usage", "input": max(0, engine._pos - erzeugt), "output": erzeugt}
        antwort = voll.strip() or "(keine Antwort)"
        erwiderung = {"role": "assistant", "content": antwort}
        # die erzeugten Token samt Denktext; ob er beim nächsten Mal mitgeht, entscheidet _nachrichten
        self._zusatz.von(erwiderung)["ids"] = list(engine.last_ids)
        self._zusatz.von(erwiderung)["gedacht"] = gedacht
        self.messages.append(erwiderung)
        if engine.checkpoint_new:
            await asyncio.to_thread(prefix_speichern, self.name, engine)


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
    return f"{datei.name}|{st.st_size}|{st.st_mtime_ns}|{engine.dtype}|{engine.device.type}"


def prefix_laden(name: str, engine, system: str) -> bool:
    """Frisch geladenes Modell: gespeicherten Stand nach der Anleitung übernehmen,
    wenn Modell und Anleitung genau passen (spart das erste Einlesen)."""
    pfad = _prefix_datei(name)
    if engine._pos != 0 or pfad is None or not pfad.is_file() or not system:
        return False
    from ggufengine import Message
    ids = engine.chat_format.encode([Message("system", system)], add_generation_prompt=False)
    return engine.load_prefix(str(pfad), _prefix_kennung(name, engine), ids)


def prefix_speichern(name: str, engine) -> bool:
    """Neuen Stand nach der Anleitung auf die Platte schreiben (einmal je Anleitung)."""
    pfad = _prefix_datei(name)
    if pfad is None or not engine.checkpoint_new:
        return False
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        return engine.save_prefix(str(pfad), _prefix_kennung(name, engine))
    except Exception:
        return False


def werkzeug_block_fertig(text: str) -> bool:
    """Steht im Text ein vollständiger Werkzeug-Block?"""
    return _BLOCK_FERTIG.search(text) is not None


def _bild_aus_uri(uri: str):
    """data:image/…;base64,… -> PIL-Bild."""
    import base64
    import io
    from PIL import Image
    kopf, _, daten = uri.partition(",")
    if not kopf.startswith("data:image/") or ";base64" not in kopf:
        raise ValueError("kein Bild (data:image/…;base64 erwartet)")
    return Image.open(io.BytesIO(base64.b64decode(daten))).convert("RGB")


def _kodieren(engine, uris: list[str]) -> list:
    """Bilder -> Embeddings; der Bild-Encoder ist nur dafür auf der GPU."""
    with _lock:
        return [engine.encode_image(_bild_aus_uri(u)) for u in uris]


def _gedaechtnis(frage: str) -> str:
    """Gedächtnis-Treffer zur Nachricht; bei Kurzem ohne Frage (Gruß, „ok“) keine – die
    Treffer wären nur ähnlich kurze Nachrichten, und kleine Modelle antworten dann darauf."""
    if len(frage.split()) < KURZ_OHNE_SUCHE and "?" not in frage:
        return ""
    try:
        import indexdb
        return indexdb.guide_block(frage) or ""
    except Exception:
        return ""
