"""Coding-LoRA trainieren: aus der Coding-Sammlung für ein lokales GGUF-Modell.

Jedes nutzbare Beispiel der Sammlung wird ein kurzes Gespräch im Chat-Format des Modells:
Auftrag, Schritt und Datei als Frage, der freigegebene Code als Antwort. Gelernt wird nur
auf der Antwort. Geprüfte Beispiele kommen doppelt so oft dran wie saubere.

Läuft im Prozess von NemiCLI (Hintergrund-Aufgabe), sichert alle SICHERN_ALLE Schritte und
macht beim nächsten Start mit gleichem Rang dort weiter. Das Ergebnis liegt unter
learned/coding_lora/<Modell>.safetensors; der LoRA-Motor klinkt es an, sobald der
Coding-Assistent mit diesem Modell läuft.
"""

from __future__ import annotations

import gc
import random
import threading
import time
from pathlib import Path

from . import adapter as A

MIN_BEISPIELE = 10
MAX_TOKEN = 1536
SICHERN_ALLE = 50
SYSTEM = "Du bist ein sorgfältiger Coding-Assistent. Du schreibst sauberen, geprüften Code."
_ENDEN = ("<|im_end|>", "<end_of_turn>", "<|eot_id|>", "<|end|>", "<turn|>")

ANHALTEN = threading.Event()
_LAEUFT = threading.Lock()
_status: dict = {}


def laeuft() -> bool:
    return _LAEUFT.locked()


def status() -> dict:
    return dict(_status)


def _stand_datei(gguf: str | Path) -> Path:
    return A.ORDNER / f"{Path(gguf).stem}_stand.safetensors"


def lokale_modelle() -> dict[str, Path]:
    try:
        import gguflokal
        return gguflokal.modelle()
    except Exception:
        return {}


def modell_waehlen(name: str | None = None) -> tuple[str, Path]:
    """(Name, GGUF) – das genannte lokale Modell oder das einzige mit unterstützter Bauart."""
    from ggufengine.gguf import GGUFFile
    alle = lokale_modelle()
    if name:
        if name not in alle:
            raise ValueError(f"Kein lokales Modell „{name}“. Vorhanden: {', '.join(alle) or 'keine'}.")
        return name, alle[name]
    passend = []
    for n, p in alle.items():
        gg = GGUFFile(str(p))
        try:
            if gg.architecture in A.BAUARTEN:
                passend.append((n, p))
        finally:
            gg.close()
    if not passend:
        raise ValueError(f"Kein lokales Modell mit unterstützter Bauart ({', '.join(A.BAUARTEN)}).")
    if len(passend) > 1:
        raise ValueError("Mehrere passende lokale Modelle – nenne eins mit `modell`: "
                         + ", ".join(n for n, _ in passend))
    return passend[0]


def _kennung(sprache: str) -> str:
    try:
        import codesammlung
        return codesammlung._KENNUNG.get(sprache, "")
    except Exception:
        return ""


def gespraech(b: dict) -> tuple[str, str]:
    """(Frage, Antwort) eines Beispiels der Coding-Sammlung."""
    kennung = _kennung(b.get("sprache", ""))
    frage = []
    if b.get("auftrag"):
        frage.append(f"Auftrag: {b['auftrag']}")
    if b.get("punkt"):
        frage.append(f"Schritt: {b['punkt']}")
    frage.append(f"Datei: {b.get('datei', '?')} ({b.get('sprache', '?')})")
    if b.get("art") == "bearbeiten" and b.get("vorher"):
        frage.append(f"Ändere diese Stelle:\n```{kennung}\n{b['vorher']}\n```")
    else:
        frage.append("Schreibe die Datei.")
    return "\n".join(frage), f"```{kennung}\n{b['nachher']}\n```"


def kodieren(beispiele: list[dict], tokenizer, formatter) -> list[tuple[list[int], int, int]]:
    """[(Token, Beginn der Antwort, Gewicht)] – zu lange Fragen fallen weg, Antworten werden gekürzt;
    geprüfte Beispiele zählen doppelt."""
    from ggufengine.chat import Message
    ende = next((tokenizer.special[n] for n in _ENDEN if n in tokenizer.special), tokenizer.eos_id)
    raus = []
    for b in beispiele:
        frage, antwort = gespraech(b)
        vorn = formatter.encode([Message("system", SYSTEM), Message("user", frage)], add_generation_prompt=True)
        if len(vorn) > MAX_TOKEN - 64:
            continue
        ids = (vorn + tokenizer.encode(antwort) + ([ende] if ende is not None else []))[:MAX_TOKEN]
        raus.append((ids, len(vorn), 2 if b.get("status") == "geprueft" else 1))
    return raus


def _daten_kennung(daten: list) -> str:
    """Fingerabdruck der kodierten Beispiele – passt ein Zwischenstand noch zur Sammlung?"""
    import hashlib
    h = hashlib.sha256()
    for ids, ab, gewicht in daten:
        h.update(f"{ab}|{gewicht}|{','.join(map(str, ids))}\n".encode())
    return h.hexdigest()[:16]


def trainieren(modell: str | None = None, schritte: int = 300, rang: int = 16, lr: float = 1e-4,
               on_status=None, abbruch=None, *, gguf: str | Path | None = None, beispiele: list | None = None,
               geraet: str | None = None) -> Path:
    """Trainiert und speichert das Coding-LoRA. Gibt den Pfad zurück."""
    if not _LAEUFT.acquire(blocking=False):
        raise RuntimeError("Es läuft schon ein Coding-LoRA-Training.")
    try:
        return _trainieren(modell, schritte, rang, lr, on_status, abbruch or ANHALTEN.is_set, gguf,
                           beispiele, geraet)
    finally:
        _status["laeuft"] = False
        _LAEUFT.release()


def _melde(on_status, **felder) -> None:
    _status.update(felder)
    if on_status is not None and felder.get("text"):
        try:
            on_status(felder["text"])
        except Exception:
            pass


def _trainieren(modell, schritte, rang, lr, on_status, abbruch, gguf, beispiele, geraet) -> Path:
    import torch
    from ggufengine.chat import ChatFormatter, detect_format
    from ggufengine.gguf import GGUFFile
    from ggufengine.models import build_model
    from ggufengine.tokenizer import Tokenizer

    from .lernweg import Lernweg

    schritte = max(10, int(schritte))
    rang = max(2, min(64, int(rang)))
    name = Path(gguf).stem if gguf else None
    if gguf is None:
        name, gguf = modell_waehlen(modell)
    if beispiele is None:
        import codesammlung
        beispiele = codesammlung.beispiele()
    if len(beispiele) < MIN_BEISPIELE:
        raise ValueError(f"Zu wenig Beispiele in der Coding-Sammlung ({len(beispiele)}, mindestens "
                         f"{MIN_BEISPIELE}). Sie füllt sich von selbst beim Coden mit dem Coding-Assistenten.")
    _melde(on_status, laeuft=True, modell=name, schritt=0, schritte=schritte, verlust=None, start=time.time(),
           text=f"Coding-LoRA: lade {name} …")
    dev = torch.device(geraet or ("cuda" if torch.cuda.is_available() else "cpu"))
    dtype = torch.bfloat16 if dev.type == "cuda" else torch.float32
    gg = GGUFFile(str(gguf))
    model = lern = None
    try:
        if gg.architecture not in A.BAUARTEN:
            raise ValueError(f"Für die Bauart {gg.architecture} gibt es noch keinen Lernweg.")
        tok = Tokenizer(gg)
        vorlage = gg.get("tokenizer.chat_template")
        fmt = ChatFormatter(tok, detect_format(vorlage, gg.architecture), thinking=False, template=vorlage)
        daten = kodieren(beispiele, tok, fmt)
        if len(daten) < MIN_BEISPIELE:
            raise ValueError(f"Nach dem Kürzen bleiben nur {len(daten)} Beispiele.")
        model = build_model(gg, dev, dtype, 64, quant=dev.type == "cuda")
        lern = Lernweg(model, gg.architecture, rang=rang)
        opt = torch.optim.AdamW(lern.parameter(), lr=lr, weight_decay=0.0)
        meta = {"basis": A.basis_kennung(gguf), "rang": rang, "alpha": lern.alpha, "schritte": schritte,
                "beispiele": len(daten), "modell": Path(gguf).name, "lr": lr, "daten": _daten_kennung(daten)}
        stand = _stand_datei(gguf)
        beginn = 0
        if stand.is_file():
            try:
                alt, alt_meta = A.laden(stand)
                if all(alt_meta.get(k) == str(meta[k]) for k in ("basis", "rang", "schritte", "lr", "daten")):
                    lern.laden(alt)
                    A.optimierer_laden(stand, lern.gewichte(), opt)
                    beginn = int(alt_meta.get("schritt", 0))
            except Exception:
                beginn = 0
        verluste: list[float] = []
        t0 = time.time()
        for schritt in range(beginn, schritte):
            if abbruch():
                A.speichern(stand, lern.gewichte(), {**meta, "schritt": schritt}, opt)
                raise RuntimeError(f"Angehalten bei Schritt {schritt}/{schritte} – ein neuer Start mit gleichem "
                                   "Rang und gleichen Schritten macht dort weiter.")
            zufall = random.Random(7_000_003 + schritt)      # je Schritt: nach Pause dieselbe Folge
            ids, ab, _ = zufall.choices(daten, weights=[d[2] for d in daten])[0]
            t = torch.tensor(ids, device=dev)
            maske = torch.arange(len(ids), device=dev) >= ab
            opt.zero_grad(set_to_none=True)
            verlust = lern.verlust(t, maske)
            verlust.backward()
            torch.nn.utils.clip_grad_norm_(lern.parameter(), 1.0)
            opt.step()
            verluste.append(float(verlust.detach()))
            fertig = schritt + 1
            if fertig % 5 == 0 or fertig == schritte:
                mittel = sum(verluste[-20:]) / len(verluste[-20:])
                rest = (time.time() - t0) / max(1, fertig - beginn) * (schritte - fertig)
                _melde(on_status, schritt=fertig, verlust=round(mittel, 4),
                       text=f"Coding-LoRA: Schritt {fertig}/{schritte} · Verlust {mittel:.3f} · noch ~{rest / 60:.0f} min")
            if fertig % SICHERN_ALLE == 0 and fertig < schritte:
                A.speichern(stand, lern.gewichte(), {**meta, "schritt": fertig}, opt)
        mittel = sum(verluste[-20:]) / max(1, len(verluste[-20:]))
        ziel = A.speichern(A.datei_fuer(gguf), lern.gewichte(),
                           {**meta, "verlust": round(mittel, 4), "zeit": time.strftime("%Y-%m-%d %H:%M")})
        stand.unlink(missing_ok=True)
        _melde(on_status, text=f"Coding-LoRA fertig: {ziel.name}")
        _protokoll(f"Coding-LoRA für {Path(gguf).name}: {schritte} Schritte, Rang {rang}, "
                   f"{len(daten)} Beispiele, Verlust {mittel:.3f}")
        return ziel
    finally:
        del lern, model
        gc.collect()
        try:
            gg.close()
        except Exception:
            pass
        if dev.type == "cuda":
            torch.cuda.empty_cache()


def _protokoll(text: str) -> None:
    try:
        import protokoll
        protokoll.schreibe("coding_lora_trainieren", text, "success", veraendernd=True, wer="Persönlichkeit")
    except Exception:
        pass


def stand_text() -> str:
    """Stand für das Werkzeug `coding_lora`: Sammlung, fertige LoRAs, laufendes Training."""
    zeilen = []
    try:
        import codesammlung
        s = codesammlung.stand()
        zeilen.append(f"Coding-Sammlung: {s['nutzbar']} nutzbare Beispiele ({s.get('geprueft', 0)} geprüft) – "
                      f"mindestens {MIN_BEISPIELE} für ein Training.")
    except Exception:
        pass
    if laeuft():
        s = status()
        zeilen.append(f"Training läuft: {s.get('modell')} Schritt {s.get('schritt')}/{s.get('schritte')}, "
                      f"Verlust {s.get('verlust')}")
    for name, pfad in lokale_modelle().items():
        zeilen.append(A.stand_text(pfad))
        stand = _stand_datei(pfad)
        if stand.is_file():
            try:
                m = A.info(stand)
                zeilen.append(f"  angefangen: Schritt {m.get('schritt')}/{m.get('schritte')} (Rang {m.get('rang')})")
            except Exception:
                pass
    if not lokale_modelle():
        zeilen.append("Keine lokalen GGUF-Modelle gefunden.")
    zeilen.append("Angeklinkt wird das LoRA vom LoRA-Motor, sobald der Coding-Assistent mit dem lokalen Modell läuft "
                  "(Einstellungen coding_lora an/aus, coding_lora_staerke 0–2).")
    return "\n".join(zeilen)

