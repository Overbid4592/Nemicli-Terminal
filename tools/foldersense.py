"""
foldersense.py - Stufe 1 von „NemiCLI lernt deinen PC kennen".

Ein eigener, kleiner **Naive-Bayes-Klassifikator** (reines Python, keine fremde
Lib), der aus dem INHALT eines Ordners erkennt, *was für ein Ort* das ist:
Python-Projekt, Node-Projekt, Dokumente, Bilder, Musik, Downloads, System …

So funktioniert ML hier in drei Schritten:

  1) MERKMALE   features(pfad) -> ["ext:.py", "file:requirements.txt", "dir:.git", ...]
                (aus den Dateien/Endungen/Markern eines Ordners werden „Tokens")
  2) LERNEN     aus vielen Beispiel-Ordnern (Label + Tokens) zählt das Modell,
                welche Tokens bei welchem Ordner-Typ wie oft vorkommen.
  3) RATEN      classify(pfad) rechnet aus, welcher Typ am wahrscheinlichsten ist,
                inklusive einer Sicherheit in Prozent.

Das Modell ist DATEN (learned/folder_model.json), kein Code – es lässt sich
jederzeit neu trainieren. NemiCLI kann mit learn_folder() aus echten Ordnern
dazulernen; das ist die Basis für Stufe 2 (PC-Karte) und 3 (Mitwachsen).
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

# learned/ liegt in der Projekt-Wurzel (Modul ist eine Ebene tief unter tools/)
try:                                     # exe-Modus: Daten liegen neben der exe
    from paths import ROOT as _ROOT
except Exception:                        # Selbsttest ohne Bootstrap
    _ROOT = Path(__file__).resolve().parent.parent

LEARN_DIR = _ROOT / "learned"
MODEL_PATH = LEARN_DIR / "folder_model.json"
EXAMPLES_PATH = LEARN_DIR / "folder_examples.json"   # selbst dazugelernte Beispiele

MAX_ENTRIES = 800        # so viele Einträge schauen wir pro Ordner an (Performance)

# ---------------------------------------------------------------------------
# Deutsche Klartext-Namen + Marker
# ---------------------------------------------------------------------------

LABEL_DE = {
    "python_project": "Python-Projekt",
    "node_project":   "Node.js-/JavaScript-Projekt",
    "web_project":    "Web-Projekt (HTML/CSS/JS)",
    "rust_project":   "Rust-Projekt",
    "documents":      "Dokumente-Ordner",
    "bilder":         "Bilder-/Foto-Ordner",
    "musik":          "Musik-/Audio-Ordner",
    "video":          "Video-Ordner",
    "downloads":      "Downloads-Ordner (gemischt)",
    "code_generic":   "Entwicklungs-/Code-Ordner",
    "system":         "System-/Programm-Ordner",
    "leer":           "leerer oder unscheinbarer Ordner",
}

# Kurzkürzel für die schmale ML-Anzeige links neben der Eingabe
LABEL_SHORT = {
    "python_project": "py", "node_project": "js", "web_project": "web",
    "rust_project": "rs", "documents": "doc", "bilder": "img", "musik": "mus",
    "video": "vid", "downloads": "dl", "code_generic": "code", "system": "sys",
    "leer": "—",
}

# Dateinamen, die ein starkes Signal sind (Projekt-Marker)
MARK_FILES = {
    "requirements.txt", "setup.py", "pyproject.toml", "pipfile", "manage.py",
    "package.json", "package-lock.json", "yarn.lock", "tsconfig.json",
    "cargo.toml", "go.mod", "pom.xml", "build.gradle", "gemfile",
    "composer.json", "dockerfile", "makefile", "index.html",
    ".gitignore", "readme.md",
}
# Unterordner-Namen, die ein starkes Signal sind
MARK_DIRS = {
    ".git", "node_modules", "venv", ".venv", "env", "__pycache__",
    "target", "dist", "build", "src", "public", "static", ".idea", ".vscode",
}


# ---------------------------------------------------------------------------
# Schritt 1: MERKMALE – aus einem Ordner Tokens machen
# ---------------------------------------------------------------------------

def features(path: str | os.PathLike) -> list[str]:
    """Macht aus dem Inhalt eines Ordners eine Liste von Merkmal-Tokens."""
    tokens: list[str] = []
    try:
        entries = list(os.scandir(path))
    except (OSError, PermissionError):
        return tokens

    for entry in entries[:MAX_ENTRIES]:
        name = entry.name.lower()
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue

        if is_dir:
            # Nur bekannte Marker-Ordner zählen – unbekannte Unterordner sind Rauschen.
            if name in MARK_DIRS:
                tokens.append(f"dir:{name}")
        else:
            if name in MARK_FILES:
                tokens.append(f"file:{name}")
            ext = os.path.splitext(name)[1]
            if ext:                       # Dateien ohne Endung ignorieren wir
                tokens.append(f"ext:{ext}")
    return tokens


# ---------------------------------------------------------------------------
# Schritt 2: LERNEN – Naive-Bayes-Modell aus Beispielen
# ---------------------------------------------------------------------------

def _empty_model() -> dict:
    return {"labels": {}, "vocab": [], "ndocs": 0}


def _train(examples: list[tuple[str, list[str]]]) -> dict:
    """Zählt Tokens je Label. (Multinomiales Naive Bayes mit Add-1-Glättung.)"""
    labels: dict[str, dict] = {}
    vocab: set[str] = set()
    ndocs = 0

    for label, toks in examples:
        slot = labels.setdefault(label, {"docs": 0, "total": 0, "tokens": {}})
        slot["docs"] += 1
        ndocs += 1
        for tok in toks:
            slot["tokens"][tok] = slot["tokens"].get(tok, 0) + 1
            slot["total"] += 1
            vocab.add(tok)

    return {"labels": labels, "vocab": sorted(vocab), "ndocs": ndocs}


# ---------------------------------------------------------------------------
# Schritt 3: RATEN – Wahrscheinlichkeit je Label
# ---------------------------------------------------------------------------

def _score(model: dict, toks: list[str]) -> list[tuple[str, float]]:
    """Log-Wahrscheinlichkeit je Label für eine Token-Liste."""
    labels = model["labels"]
    vsize = max(len(model["vocab"]), 1)
    ndocs = max(model["ndocs"], 1)

    scored: list[tuple[str, float]] = []
    for label, slot in labels.items():
        # Vorwissen: wie häufig ist dieses Label überhaupt?
        logp = math.log(slot["docs"] / ndocs)
        denom = slot["total"] + vsize          # Nenner der Add-1-Glättung
        for tok in toks:
            count = slot["tokens"].get(tok, 0)
            logp += math.log((count + 1) / denom)
        scored.append((label, logp))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _softmax_top(scored: list[tuple[str, float]]) -> tuple[str, float]:
    """Bestes Label + Sicherheit in 0..1 (stabiler Softmax der Log-Scores)."""
    if not scored:
        return "leer", 0.0
    top_log = scored[0][1]
    exps = [math.exp(lp - top_log) for _, lp in scored]
    total = sum(exps) or 1.0
    return scored[0][0], exps[0] / total


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------

def classify(path: str | os.PathLike) -> dict:
    """Klassifiziert einen Ordner. Gibt label, name (de), confidence, tokens zurück."""
    model = _get_model()
    toks = features(path)
    if not toks:
        return {"label": "leer", "name": LABEL_DE["leer"], "confidence": 0.0,
                "tokens": 0, "empty": True}
    scored = _score(model, toks)
    label, conf = _softmax_top(scored)
    return {"label": label, "name": LABEL_DE.get(label, label),
            "confidence": conf, "tokens": len(toks), "empty": False}


def describe(path: str | os.PathLike) -> str:
    """Ein-Zeiler für den System-Prompt: „Python-Projekt (87 % sicher)"."""
    r = classify(path)
    if r["empty"]:
        return "leerer oder nicht lesbarer Ordner"
    return f"{r['name']} ({round(r['confidence'] * 100)} % sicher)"


def learn_folder(path: str | os.PathLike, label: str) -> bool:
    """NemiCLI bringt sich einen echten Ordner als Beispiel bei und trainiert neu."""
    if label not in LABEL_DE:
        return False
    toks = features(path)
    if not toks:
        return False
    examples = _load_examples()
    examples.append({"label": label, "tokens": toks})
    _save_json(EXAMPLES_PATH, examples)
    _rebuild()
    return True


def labels() -> dict[str, str]:
    """Alle Ordner-Typen, die NemiCLI kennt (label -> deutscher Name)."""
    return dict(LABEL_DE)


def top(path: str | os.PathLike, n: int = 4) -> list[tuple[str, float]]:
    """Die n wahrscheinlichsten Typen als (deutscher Name, Wahrscheinlichkeit 0..1)."""
    toks = features(path)
    if not toks:
        return []
    scored = _score(_get_model(), toks)
    top_log = scored[0][1]
    exps = [(lab, math.exp(lp - top_log)) for lab, lp in scored]
    total = sum(e for _, e in exps) or 1.0
    return [(LABEL_DE.get(lab, lab), e / total) for lab, e in exps[:n]]


# kleiner TTL-Cache, damit der ML-Balken nicht bei jedem Frame den Ordner scannt
_CUR_CACHE: dict = {"path": None, "at": 0.0, "result": None}


def current(path: str | os.PathLike | None = None, ttl: float = 4.0) -> dict:
    """Klassifikation des aktuellen Ordners – gecacht (für die Live-Anzeige)."""
    path = os.fspath(path) if path else os.getcwd()
    now = time.time()
    if _CUR_CACHE["path"] == path and now - _CUR_CACHE["at"] < ttl:
        return _CUR_CACHE["result"]
    result = classify(path)
    _CUR_CACHE.update(path=path, at=now, result=result)
    return result


def stats(path: str | os.PathLike | None = None) -> dict:
    """Alles über den aktuellen ML-Zustand (für /ml und den ML-Bericht)."""
    path = os.fspath(path) if path else os.getcwd()
    model = _get_model()
    examples = _load_examples()
    learned_by: dict[str, int] = {}
    for ex in examples:
        learned_by[ex["label"]] = learned_by.get(ex["label"], 0) + 1
    return {
        "path": path,
        "classes": len(model["labels"]),
        "vocab": len(model["vocab"]),
        "seed": len(_seed_examples()),
        "learned": len(examples),
        "learned_by": learned_by,
        "current": classify(path),
        "top": top(path, 4),
    }


def report_text(path: str | os.PathLike | None = None) -> str:
    """Kurzer Klartext-Bericht über den ML-Zustand (z.B. für die Aktion ml_status)."""
    s = stats(path)
    cur = s["current"]
    lines = ["📊 Mein Ordner-Sinn (ML-Status):",
             f"- Aktueller Ordner: {s['path']}"]
    if cur["empty"]:
        lines.append("- Einschätzung: leer/nicht lesbar (keine Merkmale)")
    else:
        lines.append(f"- Einschätzung: {cur['name']} "
                     f"({round(cur['confidence'] * 100)} % sicher)")
    lines.append(f"- Ich kenne {s['classes']} Ordner-Typen, "
                 f"{s['vocab']} Merkmale im Vokabular")
    lines.append(f"- Trainiert aus {s['seed']} eingebauten + {s['learned']} "
                 "selbst dazugelernten Beispielen")
    if s["learned_by"]:
        teile = ", ".join(f"{LABEL_DE.get(k, k)} ×{v}"
                          for k, v in s["learned_by"].items())
        lines.append(f"- Selbst dazugelernt: {teile}")
    if s["top"]:
        teile = ", ".join(f"{name} {round(p * 100)} %" for name, p in s["top"])
        lines.append(f"- Top-Einschätzungen hier: {teile}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Modell laden / bauen / speichern
# ---------------------------------------------------------------------------

_MODEL_CACHE: dict | None = None


def _get_model() -> dict:
    global _MODEL_CACHE
    if _MODEL_CACHE is None:
        if MODEL_PATH.exists():
            try:
                _MODEL_CACHE = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
            except Exception:
                _MODEL_CACHE = None
        if _MODEL_CACHE is None:
            _rebuild()
    return _MODEL_CACHE  # type: ignore[return-value]


def _rebuild() -> dict:
    """Trainiert frisch aus Seed-Daten + selbst gelernten Beispielen."""
    global _MODEL_CACHE
    examples = [(label, toks) for label, toks in _seed_examples()]
    for ex in _load_examples():
        examples.append((ex["label"], ex["tokens"]))
    model = _train(examples)
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    _save_json(MODEL_PATH, model)
    _MODEL_CACHE = model
    return model


def retrain() -> dict:
    """Öffentlich: Modell komplett neu bauen (z.B. nach dem Dazulernen)."""
    return _rebuild()


def _load_examples() -> list[dict]:
    if EXAMPLES_PATH.exists():
        try:
            return json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Start-Trainingsdaten (Seed) – kompakte Beispiel-Ordner je Typ
# ---------------------------------------------------------------------------

def _bag(*parts) -> list[str]:
    """Hilfsfunktion: baut eine Token-Liste, (token, anzahl)-Paare erlaubt."""
    out: list[str] = []
    for p in parts:
        if isinstance(p, tuple):
            tok, n = p
            out.extend([tok] * n)
        else:
            out.append(p)
    return out


def _seed_examples() -> list[tuple[str, list[str]]]:
    """Eingebaute Beispiel-Ordner, aus denen das Modell startet."""
    return [
        # ---- Python-Projekt --------------------------------------------------
        ("python_project", _bag("file:requirements.txt", "file:readme.md",
            "file:.gitignore", "dir:.git", "dir:__pycache__", "dir:src",
            ("ext:.py", 12), ("ext:.txt", 1), "ext:.md")),
        ("python_project", _bag("file:pyproject.toml", "file:setup.py",
            "dir:.git", "dir:.venv", ("ext:.py", 20), "ext:.cfg", "ext:.toml")),
        ("python_project", _bag("file:manage.py", "file:requirements.txt",
            "dir:__pycache__", ("ext:.py", 15), ("ext:.html", 3))),
        ("python_project", _bag("file:requirements.txt", "dir:venv",
            ("ext:.py", 8), "ext:.json", "ext:.md")),

        # ---- Node / JavaScript ----------------------------------------------
        ("node_project", _bag("file:package.json", "file:package-lock.json",
            "dir:node_modules", "dir:.git", ("ext:.js", 9), ("ext:.json", 2),
            "file:.gitignore", "file:readme.md")),
        ("node_project", _bag("file:package.json", "file:yarn.lock",
            "file:tsconfig.json", "dir:node_modules", "dir:src",
            ("ext:.ts", 14), ("ext:.tsx", 5), "ext:.json")),
        ("node_project", _bag("file:package.json", "dir:node_modules",
            "dir:dist", ("ext:.js", 11), "ext:.map")),

        # ---- Web (HTML/CSS/JS) ----------------------------------------------
        ("web_project", _bag("file:index.html", "dir:static", "dir:public",
            ("ext:.html", 6), ("ext:.css", 4), ("ext:.js", 3), ("ext:.png", 5))),
        ("web_project", _bag("file:index.html", ("ext:.html", 3),
            ("ext:.css", 2), ("ext:.js", 2), "ext:.svg", "ext:.ico")),

        # ---- Rust -----------------------------------------------------------
        ("rust_project", _bag("file:cargo.toml", "dir:.git", "dir:target",
            "dir:src", ("ext:.rs", 13), "ext:.toml", "ext:.lock")),
        ("rust_project", _bag("file:cargo.toml", "dir:src",
            ("ext:.rs", 7), "ext:.toml")),

        # ---- Dokumente ------------------------------------------------------
        ("documents", _bag(("ext:.pdf", 6), ("ext:.docx", 5), ("ext:.xlsx", 3),
            ("ext:.txt", 2), "ext:.pptx")),
        ("documents", _bag(("ext:.docx", 8), ("ext:.pdf", 4), "ext:.odt",
            ("ext:.txt", 3))),
        ("documents", _bag(("ext:.pdf", 12), "ext:.csv", "ext:.xlsx")),

        # ---- Bilder ---------------------------------------------------------
        ("bilder", _bag(("ext:.jpg", 18), ("ext:.png", 9), ("ext:.jpeg", 4),
            "ext:.gif")),
        ("bilder", _bag(("ext:.png", 22), ("ext:.webp", 6), "ext:.svg")),
        ("bilder", _bag(("ext:.jpg", 30), "ext:.raw", "ext:.heic")),

        # ---- Musik ----------------------------------------------------------
        ("musik", _bag(("ext:.mp3", 24), ("ext:.flac", 6), "ext:.m4a")),
        ("musik", _bag(("ext:.mp3", 15), ("ext:.wav", 8), "ext:.ogg")),

        # ---- Video ----------------------------------------------------------
        ("video", _bag(("ext:.mp4", 9), ("ext:.mkv", 4), "ext:.srt", "ext:.avi")),
        ("video", _bag(("ext:.mp4", 14), ("ext:.mov", 3))),

        # ---- Downloads (gemischt) -------------------------------------------
        ("downloads", _bag(("ext:.zip", 5), ("ext:.exe", 4), ("ext:.pdf", 3),
            ("ext:.mp3", 2), ("ext:.jpg", 3), "ext:.msi", "ext:.iso")),
        ("downloads", _bag(("ext:.exe", 6), ("ext:.zip", 4), "ext:.7z",
            ("ext:.png", 2), "ext:.docx")),

        # ---- generischer Code-Ordner ----------------------------------------
        ("code_generic", _bag("dir:.git", "file:readme.md", "file:.gitignore",
            ("ext:.c", 4), ("ext:.h", 4), "ext:.cpp", "file:makefile")),
        ("code_generic", _bag("file:makefile", ("ext:.java", 8), "ext:.xml",
            "dir:build")),
        ("code_generic", _bag("file:dockerfile", ("ext:.go", 6), "file:go.mod")),

        # ---- System / Programm ----------------------------------------------
        ("system", _bag(("ext:.dll", 20), ("ext:.exe", 4), ("ext:.sys", 3),
            "ext:.ini", "ext:.cat")),
        ("system", _bag(("ext:.dll", 14), ("ext:.exe", 6), "ext:.config",
            "ext:.manifest")),
    ]
    # Hinweis: „leer" ist KEINE gelernte Klasse, sondern eine Regel in classify()
    # (ein Ordner ohne aussagekräftige Merkmale gilt als leer/unscheinbar).


# ---------------------------------------------------------------------------
# Mini-Selbsttest:  python tools/foldersense.py [pfad]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    print(f"Ordner: {target}")
    r = classify(target)
    print(f"  -> {r['name']}  ({round(r['confidence']*100)} % sicher, "
          f"{r['tokens']} Merkmale)")

    print("\nTop-Einschätzungen:")
    model = _get_model()
    for label, log in _score(model, features(target))[:5]:
        print(f"  {LABEL_DE.get(label, label):35} log={log:8.2f}")
