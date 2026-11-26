"""sicher.py – Office-Pakete (ZIP) und ihr XML mit festen Grenzen lesen.

Einer Größenangabe im Paket wird nicht vertraut: gelesen wird höchstens die Grenze + 1 Byte. XML mit DTD oder
Entitäten wird abgelehnt (Office-Formate brauchen keine), damit sind Entitäts-Angriffe ausgeschlossen.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_XML = 40 * 2**20             # ein XML-Teil entpackt
MAX_BILD = 25 * 2**20            # ein eingebettetes Bild
MAX_GESAMT = 300 * 2**20         # alles, was aus einem Paket gelesen wird
MAX_TEILE = 5000                 # Einträge im Paket
MAX_DATEI = 200 * 2**20          # Größe einer Datei auf der Platte (Text, CSV, RTF …)


class FormatFehler(ValueError):
    """Datei ist kaputt, zu groß oder hat einen nicht unterstützten Aufbau."""


def _mb(n: int) -> str:
    return f"{n / 2**20:.0f} MB"


def datei_lesen(pfad: str | Path, grenze: int | None = None) -> bytes:
    grenze = MAX_DATEI if grenze is None else grenze
    p = Path(pfad)
    with p.open("rb") as f:
        roh = f.read(grenze + 1)
    if len(roh) > grenze:
        raise FormatFehler(f"{p.name} ist größer als {_mb(grenze)}")
    return roh


def xml_aus(roh: bytes, name: str = "XML") -> ET.Element:
    if b"<!DOCTYPE" in roh or b"<!ENTITY" in roh:
        raise FormatFehler(f"{name} enthält eine DTD und wird nicht gelesen")
    try:
        return ET.fromstring(roh)
    except ET.ParseError as e:
        raise FormatFehler(f"{name} ist kein gültiges XML: {e}") from None


class Paket:
    """Lesender Zugriff auf ein ZIP-Paket mit Grenzen je Teil und insgesamt."""

    def __init__(self, pfad: str | Path):
        self.pfad = Path(pfad)
        try:
            self.zip = zipfile.ZipFile(self.pfad)
        except (zipfile.BadZipFile, OSError) as e:
            raise FormatFehler(f"{self.pfad.name} ist kein gültiges Office-Paket: {e}") from None
        if len(self.zip.infolist()) > MAX_TEILE:
            self.zip.close()
            raise FormatFehler(f"{self.pfad.name} hat mehr als {MAX_TEILE} Teile")
        self.namen = set(self.zip.namelist())
        self._gelesen = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.zip.close()

    def hat(self, name: str) -> bool:
        return name in self.namen

    def roh(self, name: str, grenze: int | None = None) -> bytes:
        grenze = MAX_XML if grenze is None else grenze
        if name not in self.namen:
            raise FormatFehler(f"Teil fehlt: {name}")
        try:
            with self.zip.open(name) as f:
                daten = f.read(grenze + 1)
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as e:
            raise FormatFehler(f"{name} nicht lesbar: {e}") from None
        if len(daten) > grenze:
            raise FormatFehler(f"{name} ist entpackt größer als {_mb(grenze)}")
        self._gelesen += len(daten)
        if self._gelesen > MAX_GESAMT:
            raise FormatFehler(f"Paket ist entpackt größer als {_mb(MAX_GESAMT)}")
        return daten

    def xml(self, name: str) -> ET.Element:
        return xml_aus(self.roh(name), name)
