"""
Debug-Log in Markdown: eine Datei je Sitzung in <projekt>/debuglog/log_<datum>_<zeit>.md.

Alles, was Motor und Oberfläche tun, wird hier gut lesbar angehängt: Umgebung, gefundene
Modelle, Ladedetails (Hyperparameter, Tensor-Typen, VRAM, Zeiten), jeder Prompt und jede
Antwort mit Tokenzahlen und Sampler-Einstellungen sowie vollständige Tracebacks.
Ausgeschaltet (alle Aufrufe tun nichts), bis `enable()` aufgerufen wird.
"""
from __future__ import annotations

import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_path: Optional[str] = None
_t0 = time.time()


def enable(log_dir: str) -> str:
    """Legt die Log-Datei dieser Sitzung an und schreibt den Kopf mit der Umgebung."""
    global _path, _t0
    os.makedirs(log_dir, exist_ok=True)
    _t0 = time.time()
    _path = os.path.join(log_dir, datetime.now().strftime("log_%Y-%m-%d_%H-%M-%S.md"))
    with open(_path, "w", encoding="utf-8") as f:
        f.write(f"# ggufengine Debug-Log {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
    env = {
        "python": sys.version.split()[0], "platform": platform.platform(),
        "argv": " ".join(sys.argv), "cwd": os.getcwd(),
    }
    try:
        import torch
        env["torch"] = torch.__version__
        env["cuda"] = str(torch.cuda.is_available())
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            env["gpu"] = f"{p.name}, {p.total_memory / 2**30:.1f} GiB"
    except Exception as e:  # pragma: no cover
        env["torch"] = f"import failed: {e}"
    section("Umgebung")
    kv(env)
    return _path


def path() -> Optional[str]:
    return _path


def _write(text: str) -> None:
    global _path
    if _path is None:
        return
    with _lock:
        if _path is None:
            return
        try:
            with open(_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(text)
        except OSError:          # Platte voll, Datei gesperrt oder weg: das Log schaltet sich ab, der Motor läuft
            _path = None


def _stamp() -> str:
    return f"{datetime.now():%H:%M:%S} (+{time.time() - _t0:7.1f}s)"


def section(title: str) -> None:
    _write(f"\n## {title}  <sub>{_stamp()}</sub>\n\n")


def event(msg: str) -> None:
    _write(f"- `{_stamp()}` {msg}\n")


def text(msg: str) -> None:
    _write(msg.rstrip() + "\n\n")


def kv(d: Dict[str, Any], title: Optional[str] = None) -> None:
    """Tabelle aus Schlüssel und Wert."""
    lines = []
    if title:
        lines.append(f"**{title}**\n")
    lines.append("| Schlüssel | Wert |\n|---|---|")
    for k, v in d.items():
        v = str(v).replace("\n", " ").replace("|", "\\|")
        if len(v) > 300:
            v = v[:300] + " …"
        lines.append(f"| {k} | {v} |")
    _write("\n".join(lines) + "\n\n")


def table(headers: List[str], rows: List[List[Any]], title: Optional[str] = None) -> None:
    lines = []
    if title:
        lines.append(f"**{title}**\n")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))
    for r in rows:
        lines.append("| " + " | ".join(str(c).replace("\n", " ").replace("|", "\\|") for c in r) + " |")
    _write("\n".join(lines) + "\n\n")


def code(body: str, lang: str = "", title: Optional[str] = None, limit: int = 6000) -> None:
    if len(body) > limit:
        body = body[:limit] + f"\n… [{len(body) - limit} Zeichen gekürzt]"
    head = f"**{title}**\n\n" if title else ""
    _write(f"{head}```{lang}\n{body}\n```\n\n")


def exception(where: str) -> None:
    """Schreibt die gerade behandelte Ausnahme mit vollständigem Traceback ins Log."""
    section(f"❌ Fehler: {where}")
    code(traceback.format_exc(), "text")
