"""Quelltext von main.py samt seiner Teile – für Tests, die den Code von main.py lesen."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEILE = ("main.py", "main_dienste.py", "main_befehle.py", "main_start.py")


def text() -> str:
    return "\n".join((ROOT / t).read_text(encoding="utf-8-sig") for t in TEILE)
