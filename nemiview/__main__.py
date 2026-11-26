"""python -m nemiview <Apps-Ordner> [--app NAME] [--debug] [--speicher ORDNER]"""
import sys

from .fenster import starten

raise SystemExit(starten(sys.argv[1:]))
