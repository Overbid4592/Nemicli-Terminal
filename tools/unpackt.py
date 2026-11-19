r"""Unpackt – Universal Archive & File Organizer

Designed and programmed by Opus 5.5 and D. Hoffmann.

Entpacker mit Sortierung nach Art – ChatGPT-Exporte werden erkannt.

Nimmt ein Archiv (ZIP, 7z, RAR, TAR, TAR.GZ, ISO, …) oder einen Ordner und macht daraus
einen ordentlich sortierten Ordner:

  entpackt_<Name>/
    Bilder/  Videos/  Audio/  Texte/  Dokumente/  Tabellen/  JSON/  Code/  …   <- nach Art
    Chats/<Datum>_<Titel>.md   <- nur bei einem ChatGPT-Export: die Chats als Text,
                                  ihre Links zeigen auf die sortierten Dateien
    Übersicht.md               <- welche Ordner es gibt (+ Liste aller Chats)
    Zuordnung.csv              <- jede Datei: woher, wohin, Art, Prüfergebnis, SHA-256
    SHA256.txt                 <- SHA-256 des Archivs vorher/nachher
    Protokoll.txt              <- was beim Entpacken passiert ist

Das Archiv selbst wird NICHT verändert (nur gelesen). Ablauf:
  1. SHA-256 des ganzen Archivs (vorher)
  2. Eine ZIP wird direkt gelesen. Andere Formate werden erst in einen Arbeitsordner im Ziel
     ausgepackt (TAR & Co. mit Python, 7z/RAR/ISO/CAB … mit dem in Windows eingebauten tar.exe)
  3. In Abschnitten (Standard: 2000 Dateien): einsortieren, dann jede Datei prüfen
     (Größe, CRC32, SHA-256), dann der nächste Abschnitt
  4. Bei einem ChatGPT-Export: die Chats schreiben
  5. SHA-256 des Archivs (nachher) – Gegenprüfung: muss gleich sein wie vorher
Nur Python-Standardbibliothek (+ tar.exe von Windows für 7z/RAR/ISO …).

Aufruf:
  python unpackt.py <Archiv oder Ordner> [Ziel-Ordner] [--abschnitt 2000]
                    [--hardlink] [--probe]
Ohne Ziel-Ordner landet das Ergebnis in entpackt_<Name> neben dem Archiv (wird automatisch angelegt).
Mit Fenster (ohne Angaben):
  python unpackt.py            (ohne Konsolenfenster: pythonw unpackt.py)
  python unpackt.py --gui <Archiv>
Ziehen & Ablegen ins Fenster braucht das Paket tkinterdnd2 (optional); ohne geht es per Klick.
"""
from __future__ import annotations

import argparse
import bz2
import csv
import fnmatch
import gzip
import hashlib
import json
import lzma
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import zipfile
import zlib
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable

try:
    from compression import zstd   # ab Python 3.14
except ImportError:
    zstd = None

try:   # Fenster (fehlt tkinter, geht trotzdem die Kommandozeile)
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:
    tk = None
try:   # Ziehen & Ablegen (optional: pip install tkinterdnd2)
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None

__version__ = "1.1.0"

# ---------------------------------------------------------------- Hilfen

WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}

# Dateisignaturen -> Endung (für Dateien ohne brauchbaren Namen)
MAGIC = [
    (b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"),
    (b"%PDF", ".pdf"), (b"PK\x03\x04", ".zip"), (b"\x1aE\xdf\xa3", ".webm"),
    (b"OggS", ".ogg"), (b"ID3", ".mp3"), (b"SQLite format 3", ".db"),
]

MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
    "application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md",
    "text/x-python": ".py", "text/x-script.python": ".py", "application/json": ".json",
    "text/html": ".html", "text/csv": ".csv", "video/mp4": ".mp4", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "application/zip": ".zip",
}

CITE_RE = re.compile(r".*?", re.S)   # ChatGPT-Quellenmarker (unsichtbare Zeichen)
PRIVATE_RE = re.compile(r"[-]")


def safe_name(name: str, maxlen: int = 120) -> str:
    """Macht einen Namen für Windows gültig."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    if not name:
        name = "unbenannt"
    stem, ext = os.path.splitext(name)
    if stem.upper() in WIN_RESERVED:
        stem = "_" + stem
    if len(stem) + len(ext) > maxlen:
        stem = stem[: maxlen - len(ext)].rstrip(". ")
    return stem + ext


def sniff_ext(head: bytes) -> str:
    """Erkennt die Endung an den ersten 16 Bytes der Datei."""
    for sig, ext in MAGIC:
        if head.startswith(sig):
            return ext
    if head[4:8] == b"ftyp":
        return ".mp4"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    return ""


GOOD_EXT = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def fix_ext(name: str, lib_ext: str | None, mime: str | None, head: Callable[[], bytes],
            category: str = "") -> str:
    """Hängt eine passende Endung an, wenn der Name keine echte hat.

    head() liefert die ersten Bytes der Datei (wird nur gelesen, wenn nötig)."""
    ext = os.path.splitext(name)[1]
    if GOOD_EXT.match(ext):
        return name
    new = ""
    if lib_ext and GOOD_EXT.match("." + lib_ext):
        new = "." + lib_ext.lower()
    elif mime and mime.split(";")[0] in MIME_EXT:
        new = MIME_EXT[mime.split(";")[0]]
    else:
        new = sniff_ext(head()) or (".txt" if category == "text" else ".bin")
    return name + new


def parse_time(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fmt_time(ts: float | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not ts:
        return "unbekannt"
    return datetime.fromtimestamp(ts).strftime(fmt)


def unique(path: Path, taken: set[str]) -> Path:
    """image.png -> image (2).png, falls schon vergeben."""
    stem, ext = path.stem, path.suffix
    cand, n = path, 2
    while str(cand).lower() in taken or cand.exists():
        cand = path.with_name(f"{stem} ({n}){ext}")
        n += 1
    taken.add(str(cand).lower())
    return cand


# ---------------------------------------------------------------- Arten (Ordner nach Dateityp)

CATEGORIES: dict[str, str] = {
    "Bilder": "png jpg jpeg jpe jfif webp gif bmp tif tiff heic heif avif ico svg psd xcf "
              "raw cr2 cr3 nef arw dng orf rw2",
    "Videos": "mp4 m4v mov mkv webm avi wmv flv mpg mpeg 3gp 3g2 mts m2ts vob ogv",
    "Audio": "mp3 wav ogg oga opus m4a aac flac wma aif aiff mid midi amr",
    "Texte": "txt md markdown rst rtf log nfo text",
    "Dokumente": "pdf doc docx docm odt pages epub mobi tex xps djvu",
    "Tabellen": "xls xlsx xlsm xlsb ods csv tsv numbers",
    "Präsentationen": "ppt pptx pptm odp key",
    "JSON": "json jsonl ndjson geojson json5",
    "Daten": "xml yaml yml toml ini cfg conf env properties sql db sqlite sqlite3 parquet "
             "feather pkl pickle npy npz h5 hdf5 vcf ics",
    "Code": "py pyw ipynb js mjs cjs ts tsx jsx html htm css scss sass less c h cpp cc hpp cs "
            "java kt kts go rs rb php sh bash zsh bat cmd ps1 lua r swift dart vue svelte pl "
            "scala sol",
    "Archive": "zip 7z rar tar gz tgz bz2 tbz2 xz txz zst lz lzma cab iso dmg",
    "Schriften": "ttf otf woff woff2 eot fon",
    "3D": "obj fbx stl glb gltf blend 3mf ply dae usdz",
    "E-Mails": "eml msg mbox",
    "Programme": "exe msi dll apk deb rpm jar",
}
EXT_CAT: dict[str, str] = {}
for _cat, _exts in CATEGORIES.items():
    for _e in _exts.split():
        EXT_CAT.setdefault(_e, _cat)
# Dateien ohne Endung, die man trotzdem kennt
NAME_CAT = {"readme": "Texte", "license": "Texte", "licence": "Texte", "changelog": "Texte",
            "makefile": "Code", "dockerfile": "Code"}
OTHER = "Sonstiges"


def category_of(name: str) -> str:
    """In welchen Ordner gehört die Datei? (nach Endung)"""
    stem, ext = os.path.splitext(name)
    if ext:
        return EXT_CAT.get(ext[1:].lower(), OTHER)
    return NAME_CAT.get(stem.lower(), OTHER)


JUNK_NAMES = {".ds_store", "thumbs.db", "ehthumbs.db", "desktop.ini"}


def is_junk(rel: str) -> bool:
    """System-Müll aus Archiven (macOS-/Windows-Hilfsdateien)."""
    parts = rel.replace("\\", "/").split("/")
    return "__MACOSX" in parts or parts[-1].lower() in JUNK_NAMES or parts[-1].startswith("._")


FILE_ID_RE = re.compile(r"file[_-][0-9A-Za-z]+")   # ChatGPT-Datei-ID im Dateinamen


# ---------------------------------------------------------------- Quellen (Ordner, ZIP, Arbeitsordner)

class FolderSource:
    """Ein Ordner. movable=True heißt: unser eigener Arbeitsordner, Dateien dürfen
    verschoben statt kopiert werden."""
    is_zip = False

    def __init__(self, root: Path, movable: bool = False):
        self.root = root
        self.movable = movable
        self.symlinks = 0

    def files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if p.is_symlink():
                    self.symlinks += 1
                elif p.is_file():
                    out.append(p.relative_to(self.root).as_posix())
        return out

    def open(self, name: str):
        return open(self.root / name, "rb")

    def size(self, name: str) -> int:
        return (self.root / name).stat().st_size

    def crc(self, name: str) -> int | None:
        return None   # steht nicht vorher fest, wird beim Kopieren berechnet

    def mtime(self, name: str) -> float | None:
        return (self.root / name).stat().st_mtime

    def path(self, name: str) -> Path:
        return self.root / name

    def close(self) -> None:
        pass


ZIP_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
if hasattr(zipfile, "ZIP_ZSTANDARD"):
    ZIP_METHODS.add(zipfile.ZIP_ZSTANDARD)


class ZipSource:
    """Eine ZIP, direkt gelesen (ohne Zwischen-Entpacken)."""
    is_zip = True
    movable = False
    symlinks = 0

    def __init__(self, zpath: Path):
        self.zf = zipfile.ZipFile(zpath)
        self.members = {i.filename: i for i in self.zf.infolist() if not i.is_dir()}

    def readable(self) -> bool:
        """Kann Python alle Einträge lesen? (nicht bei Passwort oder z. B. Deflate64)"""
        return all(i.compress_type in ZIP_METHODS and not i.flag_bits & 1
                   for i in self.members.values())

    def files(self) -> list[str]:
        return sorted(self.members)

    def open(self, name: str):
        return self.zf.open(self.members[name])

    def size(self, name: str) -> int:
        return self.members[name].file_size

    def crc(self, name: str) -> int | None:
        return self.members[name].CRC   # Prüfsumme, die in der ZIP steht

    def mtime(self, name: str) -> float | None:
        try:
            return time.mktime(self.members[name].date_time + (0, 0, -1))
        except (OverflowError, ValueError):
            return None

    def path(self, name: str) -> Path:
        raise OSError("In einer ZIP gibt es keinen Dateipfad")

    def close(self) -> None:
        self.zf.close()


def find_export_prefix(files: list[str]) -> str | None:
    """Ordner im Archiv, in dem der ChatGPT-Export liegt ("" = ganz oben).
    None = kein ChatGPT-Export."""
    prefix = None
    for f in files:
        folder, _, name = f.replace("\\", "/").rpartition("/")
        if name == "library_files.json" or fnmatch.fnmatch(name, "conversations*.json"):
            folder = folder + "/" if folder else ""
            if prefix is None or len(folder) < len(prefix):
                prefix = folder
    return prefix


# ---------------------------------------------------------------- Andere Archive (TAR, 7z, RAR, ISO …)

class ExtractError(Exception):
    pass


NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


@lru_cache(maxsize=1)
def find_bsdtar() -> str | None:
    """Sucht bsdtar (in Windows 10/11 als tar.exe eingebaut). GNU-tar (z. B. aus Git)
    kann kein 7z/RAR und wird übergangen."""
    cands = []
    if sys.platform == "win32":
        cands.append(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"))
    for n in ("bsdtar", "tar"):
        w = shutil.which(n)
        if w:
            cands.append(w)
    for c in cands:
        try:
            r = subprocess.run([c, "--version"], capture_output=True, timeout=15, **NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if b"bsdtar" in r.stdout:
            return c
    return None


SINGLE: dict[str, Callable] = {   # einzelne gepackte Datei, z. B. notizen.txt.gz
    ".gz": lambda f: gzip.GzipFile(fileobj=f, mode="rb"),
    ".bz2": bz2.BZ2File,
    ".xz": lzma.LZMAFile,
    ".lzma": lzma.LZMAFile,
}
if zstd is not None:
    SINGLE[".zst"] = zstd.ZstdFile

TAR_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lzma", ".tar.lz")


def archive_stem(p: Path) -> str:
    """export.tar.gz -> export"""
    if p.is_dir():
        return p.name
    low = p.name.lower()
    for s in TAR_SUFFIXES:
        if low.endswith(s):
            return p.name[: -len(s)]
    return p.stem


COMPRESS_MAGIC = [(b"\x1f\x8b", ".gz"), (b"BZh", ".bz2"), (b"\xfd7zXZ\x00", ".xz"),
                  (b"\x28\xb5\x2f\xfd", ".zst")]


def looks_like_tar(p: Path) -> bool:
    """Echtes TAR (auch gepackt)? Prüft die Kennung "ustar" im ersten Block.
    (tarfile.is_tarfile hält z. B. ISO-Dateien fälschlich für TAR.)"""
    try:
        with open(p, "rb") as f:
            head = f.read(512)
        if head[257:262] == b"ustar":
            return True
        for magic, suffix in COMPRESS_MAGIC:
            if head.startswith(magic) and suffix in SINGLE:
                with open(p, "rb") as raw, SINGLE[suffix](raw) as f:
                    return f.read(512)[257:262] == b"ustar"
    except Exception:
        pass
    return False


def detect_kind(p: Path) -> str:
    """folder, zip, tar, single oder bsdtar"""
    if p.is_dir():
        return "folder"
    if not p.is_file():
        raise ValueError(f"Nicht gefunden: {p}")
    if zipfile.is_zipfile(p):
        z = ZipSource(p)
        try:
            ok = z.readable()
        finally:
            z.close()
        return "zip" if ok or not find_bsdtar() else "bsdtar"
    if looks_like_tar(p):
        return "tar"
    if p.suffix.lower() in SINGLE:
        return "single"
    if find_bsdtar():
        return "bsdtar"
    raise ValueError("Dieses Format geht nur mit bsdtar (tar.exe ist in Windows 10/11 eingebaut).")


class CountingReader:
    """Zählt beim Lesen die Bytes mit (für den Ladebalken)."""

    def __init__(self, f, on_bytes: Callable[[int], None]):
        self.f, self.on_bytes, self.n = f, on_bytes, 0

    def read(self, size: int = -1) -> bytes:
        b = self.f.read(size)
        self.n += len(b)
        self.on_bytes(self.n)
        return b

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- Schutz vor Entpack-Bomben

RESERVE_BYTES = 5 * 1024 ** 3     # so viel bleibt auf der Ziel-Festplatte immer frei
SUSPICIOUS_RATIO = 100            # ausgepackt größer als 100 × Archiv = verdächtig …
SUSPICIOUS_MIN = 1024 ** 3        # … aber erst ab 1 GB
MAX_FILES = 1_000_000             # mehr Dateien = verdächtig
LIMIT_SLACK = 64 * 1024 ** 2      # Spielraum, wenn das Archiv seine Größe angibt
DISK_CHECK_BYTES = 128 * 1024 ** 2   # so oft (in Bytes) den freien Platz nachmessen


class SchutzStopp(Exception):
    """Der Bomben-Schutz hat angehalten."""


def fmt_bytes(n: float) -> str:
    """1536 -> '1,5 KB'"""
    for unit in ("Bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit == "Bytes" else f"{n:.1f} {unit}").replace(".", ",")
        n /= 1024
    return f"{n:.1f} TB"


def _existing(p: Path) -> Path:
    """Nächster vorhandener Ordner (für den freien Platz, auch wenn das Ziel noch fehlt)."""
    p = p.resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _bsdtar_sizes(p: Path) -> tuple[int, int] | None:
    """Ausgepackte Größe und Anzahl Dateien laut 7z/RAR/ISO-Verzeichnis (über tar -tvf)."""
    tar = find_bsdtar()
    if not tar:
        return None
    r = subprocess.run([tar, "-tvf", str(p)], capture_output=True, **NO_WINDOW)
    if r.returncode != 0:
        return None
    total = files = 0
    for line in r.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split(None, 8)
        if len(parts) >= 9 and parts[0].startswith("-") and parts[4].isdigit():
            total += int(parts[4])
            files += 1
    return total, files


def estimate_size(kind: str, p: Path) -> tuple[int, int] | None:
    """Wie groß wird es ausgepackt (Bytes, Dateien)? None = steht nicht im Archiv
    (z. B. .gz, .tar.gz) – dann passt nur der Wächter beim Auspacken auf."""
    try:
        if kind == "zip":
            with zipfile.ZipFile(p) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
            return sum(i.file_size for i in infos), len(infos)
        if kind == "bsdtar":
            return _bsdtar_sizes(p)
        if kind == "tar":
            with open(p, "rb") as f:
                plain = f.read(512)[257:262] == b"ustar"
            if plain:   # ungepacktes TAR: die Köpfe lesen geht schnell
                with tarfile.open(p, "r:") as tf:
                    ms = [m for m in tf.getmembers() if m.isfile()]
                return sum(m.size for m in ms), len(ms)
            return None
        if kind == "folder":
            total = files = 0
            for dirpath, _, filenames in os.walk(p):
                for fn in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, fn))
                        files += 1
                    except OSError:
                        pass
            return total, files
    except (OSError, zipfile.BadZipFile, tarfile.TarError):
        return None
    return None


class Guard:
    """Passt beim Auspacken auf: nicht mehr Daten als angekündigt, nicht zu viele Dateien,
    die Festplatte nie ganz voll. Fragt einmal nach (confirm), wenn eine weiche Grenze
    überschritten wird; harte Grenzen stoppen sofort."""

    def __init__(self, disk: Path, known_total: int | None, archive_size: int,
                 confirm: Callable[[str], bool] | None, allowed: bool = False):
        self.disk = _existing(disk)
        self.confirm = confirm
        self.allowed = allowed            # schon bestätigt: weiche Grenzen aus
        self.known = known_total
        # Hart: angekündigte Größe (+ Spielraum). Weich (nur ohne Angabe): 100 × Archiv, mind. 1 GB
        self.hard = None if known_total is None else known_total + known_total // 20 + LIMIT_SLACK
        self.soft = None if known_total is not None else max(archive_size * SUSPICIOUS_RATIO,
                                                             SUSPICIOUS_MIN)
        self.written = 0
        self.files = 0
        self._next_disk = 0

    def free(self) -> int:
        return shutil.disk_usage(self.disk).free

    def check_disk(self) -> None:
        free = self.free()
        if free < RESERVE_BYTES:
            raise SchutzStopp(f"Die Festplatte ist fast voll (nur noch {fmt_bytes(free)} frei). "
                              f"Unpackt lässt immer {fmt_bytes(RESERVE_BYTES)} frei und hat angehalten.")

    def _ask(self, text: str) -> None:
        if self.allowed:
            return
        if self.confirm is not None and self.confirm(text):
            self.allowed = True
            return
        raise SchutzStopp(text + " – nicht bestätigt, deshalb angehalten.")

    def add(self, nbytes: int = 0, nfiles: int = 0) -> None:
        self.written += nbytes
        self.files += nfiles
        if self.hard is not None and self.written > self.hard:
            raise SchutzStopp(f"Das Archiv erzeugt mehr Daten, als es angibt "
                              f"({fmt_bytes(self.written)} statt {fmt_bytes(self.known)}). "
                              f"Das ist typisch für eine Entpack-Bombe.")
        if self.soft is not None and self.written > self.soft:
            self.soft = None
            self._ask(f"Das Archiv wird ausgepackt mindestens {fmt_bytes(self.written)} groß – "
                      f"viel mehr als üblich. Möglicherweise eine Entpack-Bombe")
        if self.files > MAX_FILES:
            self._ask(f"Das Archiv enthält mehr als {num(MAX_FILES)} Dateien")
            self.files = -10 ** 12   # nur einmal fragen
        if self.written >= self._next_disk:
            self._next_disk = self.written + DISK_CHECK_BYTES
            self.check_disk()


def precheck(kind: str, src_path: Path, dst: Path, est: tuple[int, int] | None,
             confirm: Callable[[str], bool] | None, say) -> bool:
    """Vor dem Auspacken: reicht der Platz, ist das Archiv verdächtig?
    Gibt zurück, ob weiche Grenzen schon bestätigt sind. Wirft SchutzStopp."""
    free = shutil.disk_usage(_existing(dst)).free
    if est is None:
        say(f"Ausgepackte Größe steht nicht im Archiv – der Wächter zählt beim Auspacken mit. "
            f"Frei: {fmt_bytes(free)}")
        return False
    total, files = est
    say(f"Ausgepackt etwa {fmt_bytes(total)} in {num(files)} Dateien · frei: {fmt_bytes(free)}")
    if total > free - RESERVE_BYTES:
        raise SchutzStopp(f"Nicht genug Platz: braucht etwa {fmt_bytes(total)}, frei sind "
                          f"{fmt_bytes(free)} (davon bleiben {fmt_bytes(RESERVE_BYTES)} immer frei).")
    reasons = []
    if kind != "folder":
        packed = max(src_path.stat().st_size, 1)
        if total > SUSPICIOUS_MIN and total > packed * SUSPICIOUS_RATIO:
            reasons.append(f"aus {fmt_bytes(packed)} werden {fmt_bytes(total)} "
                           f"({num(total // packed)}-mal so groß)")
    if files > MAX_FILES:
        reasons.append(f"{num(files)} Dateien")
    if not reasons:
        return False
    text = "Verdächtiges Archiv: " + " und ".join(reasons) + ". Das könnte eine Entpack-Bombe sein"
    if confirm is not None and confirm(text):
        say("Trotz Warnung bestätigt: " + text, "warn")
        return True
    raise SchutzStopp(text + " – nicht bestätigt, deshalb nicht entpackt.")


def _extract_tar(p: Path, staging: Path, on_frac, check_cancel, say, guard: Guard) -> list[str]:
    size = max(p.stat().st_size, 1)
    problems, skipped = [], 0
    with open(p, "rb") as raw:
        reader = CountingReader(raw, lambda n: (check_cancel(), on_frac(n / size)))
        try:
            with tarfile.open(fileobj=reader, mode="r|*") as tf:
                for m in tf:
                    if m.isdir():
                        continue
                    if not m.isfile():
                        skipped += 1          # Verknüpfungen, Geräte usw.
                        continue
                    guard.add(m.size, 1)      # vor dem Schreiben: Größe steht im Kopf
                    try:
                        tf.extract(m, staging, filter="data")   # "data": nichts außerhalb
                    except (tarfile.FilterError, OSError) as e:
                        problems.append(f"{m.name}: {e}")
                        say(f"Nicht ausgepackt: {m.name} – {e}", "warn")
        except tarfile.TarError as e:
            raise ExtractError(f"TAR-Archiv kaputt oder unvollständig: {e}") from e
    if skipped:
        say(f"{num(skipped)} Verknüpfungen/Sondereinträge im TAR übersprungen", "warn")
    return problems


def _extract_single(p: Path, staging: Path, on_frac, check_cancel, say, guard: Guard) -> list[str]:
    size = max(p.stat().st_size, 1)
    out = staging / safe_name(p.stem or "entpackt")
    guard.add(0, 1)
    with open(p, "rb") as raw:
        reader = CountingReader(raw, lambda n: (check_cancel(), on_frac(n / size)))
        try:
            with SINGLE[p.suffix.lower()](reader) as f, open(out, "wb") as o:
                while chunk := f.read(CHUNK):
                    guard.add(len(chunk))     # vor dem Schreiben zählen
                    o.write(chunk)
        except (Abgebrochen, SchutzStopp):
            raise
        except Exception as e:
            raise ExtractError(f"Datei lässt sich nicht entpacken: {e}") from e
    return []


def _tar_text(b: bytes) -> str:
    lines = [l.strip() for l in b.decode("utf-8", errors="replace").splitlines() if l.strip()]
    return " | ".join(lines[-3:])


def _extract_bsdtar(p: Path, staging: Path, on_frac, check_cancel, say, guard: Guard) -> list[str]:
    tar = find_bsdtar()
    if not tar:
        raise ExtractError("Für dieses Format wird bsdtar gebraucht (tar.exe, in Windows 10/11 eingebaut).")
    lst = subprocess.run([tar, "-tf", str(p)], capture_output=True, **NO_WINDOW)
    if lst.returncode != 0:
        msg = _tar_text(lst.stderr) or "Archiv nicht lesbar"
        if "Unrecognized archive format" in msg:
            msg = "Das ist kein Archiv, das ich kenne (oder es ist beschädigt)."
        raise ExtractError(msg)
    total = max(1, sum(1 for l in lst.stdout.splitlines() if l.strip()))
    proc = subprocess.Popen([tar, "-xvf", str(p), "-C", str(staging)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **NO_WINDOW)

    # tar.exe schreibt selbst – darum misst ein Wächter-Faden laufend den belegten Platz.
    # So wird auch eine einzelne riesige Datei gestoppt, bei der tar.exe nichts mehr meldet.
    start_free = guard.free()
    stop_reason: list[BaseException] = []

    def watch() -> None:
        while proc.poll() is None:
            try:
                check_cancel()
                used = max(0, start_free - guard.free())
                guard.add(max(0, used - guard.written))   # nur den Zuwachs melden
            except (Abgebrochen, SchutzStopp) as e:
                stop_reason.append(e)
                proc.kill()
                return
            except OSError:
                pass
            time.sleep(0.2)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    problems, count = [], 0
    for line in proc.stderr:
        text = line.decode("utf-8", errors="replace").rstrip()
        if text.startswith("x "):
            count += 1
            on_frac(min(count / total, 1.0))
        elif text:
            problems.append(text)
    proc.wait()
    watcher.join()
    if stop_reason:
        raise stop_reason[0]
    # Schlussmessung: falls tar.exe schneller fertig war, als der Wächter nachsehen konnte
    guard.add(max(0, (start_free - guard.free()) - guard.written))
    if proc.returncode != 0:
        msg = " | ".join(problems[-3:]) or f"tar.exe Fehlercode {proc.returncode}"
        if "assphrase" in msg or "ncrypt" in msg:
            msg = "Das Archiv ist mit einem Passwort geschützt – das geht noch nicht. (" + msg + ")"
        if count == 0:
            raise ExtractError(msg)
        say(f"tar.exe meldet Probleme: {msg}", "warn")
        return problems or [msg]
    return []


EXTRACTORS = {"tar": _extract_tar, "single": _extract_single, "bsdtar": _extract_bsdtar}

# ---------------------------------------------------------------- Kopieren und Prüfen

CHUNK = 1 << 20   # 1 MB


def copy_with_hash(fsrc, target: Path) -> tuple[int, int, str]:
    """Kopiert und rechnet dabei Größe, CRC32 und SHA-256 der Quelle mit."""
    size, crc, sha = 0, 0, hashlib.sha256()
    with open(target, "wb") as fdst:
        while chunk := fsrc.read(CHUNK):
            crc = zlib.crc32(chunk, crc)
            sha.update(chunk)
            size += len(chunk)
            fdst.write(chunk)
    return size, crc, sha.hexdigest()


def file_hashes(p: Path) -> tuple[int, str]:
    """CRC32 und SHA-256 einer fertigen Datei."""
    crc, sha = 0, hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(CHUNK):
            crc = zlib.crc32(chunk, crc)
            sha.update(chunk)
    return crc, sha.hexdigest()


def sha256_file(p: Path, on_progress: Callable[[int], None] | None = None) -> str:
    """SHA-256 einer (auch sehr großen) Datei. on_progress(gelesene Bytes)."""
    sha, done = hashlib.sha256(), 0
    with open(p, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            sha.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done)
    return sha.hexdigest()


def check_file(target: Path, size: int, crc: int | None, sha: str | None) -> str | None:
    """Liest die fertige Datei noch einmal. None = in Ordnung, sonst der Grund."""
    try:
        real = target.stat().st_size
        if real != size:
            return f"Größe falsch ({real} statt {size} Bytes)"
        if crc is None and sha is None:
            return None
        real_crc, real_sha = file_hashes(target)
        if crc is not None and real_crc != crc:
            return "Prüfsumme (CRC32) stimmt nicht"
        if sha is not None and real_sha != sha:
            return "SHA-256 stimmt nicht"
    except OSError as e:
        return f"Datei nicht lesbar: {e}"
    return None


# ---------------------------------------------------------------- Virenscanner (jeder, über Windows)
#
# Unpackt bringt keinen eigenen Virenscanner mit, sondern nutzt den, der auf dem PC läuft
# (z. B. Avira, Bitdefender, Kaspersky, Microsoft Defender …):
#   1. Windows (Security Center) sagt, welcher Scanner aktiv ist – das steht im Protokoll.
#   2. Jede Datei kommt erst in die Quarantäne (Arbeitsordner im Ziel).
#   3. Beim Prüfen wird jede Datei gelesen; dabei schaut der Echtzeitschutz hinein.
#      Zusätzlich fragt Unpackt den Scanner direkt über AMSI (die Prüf-Schnittstelle von Windows).
#   4. Blockiert oder entfernt der Scanner eine Datei, wird sie NICHT einsortiert.
# Dateien aus dem Archiv werden dabei nie geöffnet oder gestartet – nur gelesen.

AMSI_MAX = 64 * 1024 ** 2    # bis zu dieser Größe wird der Scanner direkt (AMSI) gefragt
VIRUS_MARK = "🦠"            # Kennzeichen in Fehlertexten: vermutlich der Virenscanner


@lru_cache(maxsize=1)
def antivirus_products() -> tuple[tuple[str, bool], ...]:
    """Virenscanner, die Windows kennt: (Name, Echtzeitschutz an?). Leer außerhalb von Windows."""
    if sys.platform != "win32":
        return ()
    ps = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
          "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | "
          "ForEach-Object { \"$($_.displayName)|$($_.productState)\" }")
    # PSModulePath von PowerShell 7 würde Windows PowerShell fremde Module laden lassen
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=30, env=env, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ()
    out = []
    for line in r.stdout.decode("utf-8", errors="replace").splitlines():
        name, _, state = line.strip().rpartition("|")
        if name and state.isdigit():
            out.append((name, ((int(state) >> 8) & 0xFF) in (0x10, 0x11)))
    return tuple(out)


class Amsi:
    """Direkte Frage an den aktiven Virenscanner über AMSI (nur Windows)."""

    def __init__(self) -> None:
        self.ok = False
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            dll = ctypes.WinDLL("amsi.dll")
            H = wintypes.HANDLE
            dll.AmsiInitialize.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(H)]
            dll.AmsiInitialize.restype = ctypes.HRESULT
            dll.AmsiOpenSession.argtypes = [H, ctypes.POINTER(H)]
            dll.AmsiOpenSession.restype = ctypes.HRESULT
            dll.AmsiScanBuffer.argtypes = [H, ctypes.c_void_p, ctypes.c_ulong, wintypes.LPCWSTR, H,
                                           ctypes.POINTER(ctypes.c_int)]
            dll.AmsiScanBuffer.restype = ctypes.HRESULT
            dll.AmsiCloseSession.argtypes = [H, H]
            dll.AmsiCloseSession.restype = None
            dll.AmsiUninitialize.argtypes = [H]
            dll.AmsiUninitialize.restype = None
            ctx, ses = H(), H()
            if dll.AmsiInitialize("Unpackt", ctypes.byref(ctx)) != 0:
                return
            if dll.AmsiOpenSession(ctx, ctypes.byref(ses)) != 0:
                dll.AmsiUninitialize(ctx)
                return
        except (OSError, AttributeError):
            return
        self._ct, self.dll, self.ctx, self.ses = ctypes, dll, ctx, ses
        self.ok = True

    def scan(self, data: bytes, name: str) -> bool | None:
        """True = der Scanner meldet etwas, False = sauber, None = konnte nicht fragen."""
        if not self.ok:
            return None
        ct = self._ct
        buf = ct.create_string_buffer(data, len(data))
        res = ct.c_int(0)
        if self.dll.AmsiScanBuffer(self.ctx, buf, len(data), name, self.ses, ct.byref(res)) != 0:
            return None
        # 32768 und mehr = erkannt; 16384–20479 = vom Administrator gesperrt
        return res.value >= 32768 or 16384 <= res.value <= 20479

    def close(self) -> None:
        if self.ok:
            self.ok = False
            self.dll.AmsiCloseSession(self.ctx, self.ses)
            self.dll.AmsiUninitialize(self.ctx)


def _av_reason(e: OSError, ours: bool) -> str | None:
    """Sieht der Fehler nach einem Eingriff des Virenscanners aus? ours = Datei liegt in
    unserer Quarantäne (dort löscht oder sperrt sonst niemand)."""
    if getattr(e, "winerror", None) in (225, 226):   # ERROR_VIRUS_INFECTED / _DELETED
        return "vom Virenscanner blockiert"
    if isinstance(e, PermissionError):
        return "Zugriff verweigert – vermutlich vom Virenscanner blockiert"
    if ours and isinstance(e, FileNotFoundError):
        return "Datei ist verschwunden – vermutlich vom Virenscanner in Quarantäne genommen"
    return None


def check_quarantine(qpath: Path, expect, amsi: Amsi | None, label: str,
                     av_name: str = "") -> tuple[str | None, bool]:
    """Prüft eine Datei in der Quarantäne: Virenscanner, Größe, CRC32, SHA-256.
    Rückgabe: (Problem oder None, ist es ein Fund des Virenscanners?)."""
    if isinstance(expect, str):
        if expect.startswith(VIRUS_MARK):
            return expect[len(VIRUS_MARK):] + av_name, True
        return expect, False
    size, crc, sha = expect
    try:
        real = qpath.stat().st_size
        if real != size:
            return f"Größe falsch ({real} statt {size} Bytes)", False
        if amsi is not None and amsi.ok and size <= AMSI_MAX:
            data = qpath.read_bytes()
            if amsi.scan(data, label):
                return f"vom Virenscanner gemeldet{av_name} (AMSI)", True
            real_crc, real_sha = zlib.crc32(data), hashlib.sha256(data).hexdigest()
        elif crc is None and sha is None:
            return None, False
        else:
            real_crc, real_sha = file_hashes(qpath)
        if crc is not None and real_crc != crc:
            return "Prüfsumme (CRC32) stimmt nicht", False
        if sha is not None and real_sha != sha:
            return "SHA-256 stimmt nicht", False
    except OSError as e:
        r = _av_reason(e, ours=True)
        return (r + av_name, True) if r else (f"Datei nicht lesbar: {e}", False)
    return None, False


# ---------------------------------------------------------------- Laden (ChatGPT-Export)

def load_json(src, name: str):
    with src.open(name) as f:
        return json.load(f)   # json erkennt UTF-8 (auch mit BOM) selbst


def load_export(src, prefix: str, all_files: list[str]):
    """Liest die JSON-Dateien des ChatGPT-Exports aus dem Ordner prefix."""
    root = {f[len(prefix):]: f for f in all_files
            if f.startswith(prefix) and "/" not in f[len(prefix):]}
    files: dict[str, dict] = {}          # file_id -> Infos

    if "library_files.json" in root:
        for e in load_json(src, root["library_files.json"]):
            files[e["file_id"]] = {
                "name": e.get("file_name") or "",
                "ext": e.get("file_extension"),
                "mime": e.get("mime_type"),
                "category": e.get("library_file_category") or "other",
                "created": parse_time(e.get("created_at")),
                "thread": e.get("origination_thread_id"),
            }

    if "conversation_asset_file_names.json" in root:
        for dat_name, real in load_json(src, root["conversation_asset_file_names.json"]).items():
            fid = dat_name[:-4] if dat_name.endswith(".dat") else dat_name
            info = files.setdefault(fid, {"name": "", "ext": None, "mime": None,
                                          "category": "other", "created": None, "thread": None})
            if not info["name"]:
                info["name"] = real

    convs = []
    for name in sorted(n for n in root if fnmatch.fnmatch(n, "conversations*.json")):
        data = load_json(src, root[name])
        convs.extend(data if isinstance(data, list) else [data])
    return files, convs


# ---------------------------------------------------------------- Chats

def message_chain(conv: dict) -> list[dict]:
    """Folgt dem aktuellen Gesprächsverlauf (current_node zurück bis zum Anfang)."""
    mapping = conv.get("mapping") or {}
    node = conv.get("current_node")
    chain = []
    seen = set()
    while node and node in mapping and node not in seen:
        seen.add(node)
        n = mapping[node]
        if n.get("message"):
            chain.append(n["message"])
        node = n.get("parent")
    chain.reverse()
    if not chain:  # Fallback: alle Nachrichten nach Zeit
        chain = sorted((n["message"] for n in mapping.values() if n.get("message")),
                       key=lambda m: m.get("create_time") or 0)
    return chain


def file_ids_in(msg: dict) -> list[str]:
    ids = []
    md = msg.get("metadata") or {}
    for a in md.get("attachments") or []:
        if a.get("id"):
            ids.append(a["id"])
    for p in (msg.get("content") or {}).get("parts") or []:
        if isinstance(p, dict):
            for ptr in re.findall(r"file[_-][0-9A-Za-z]+", json.dumps(p)):
                ids.append(ptr.replace("file-", "file_", 1) if ptr.startswith("file-") else ptr)
    return list(dict.fromkeys(ids))


def clean_text(t: str) -> str:
    t = CITE_RE.sub("", t)
    return PRIVATE_RE.sub("", t).strip()


ROLE = {"user": "Du", "assistant": "ChatGPT", "tool": "Werkzeug", "system": "System"}


def render_chat(conv: dict, chain: list[dict], links: dict[str, str]) -> str:
    title = conv.get("title") or "Ohne Titel"
    out = [f"# {title}", "",
           f"- Erstellt: {fmt_time(conv.get('create_time'))}",
           f"- Zuletzt: {fmt_time(conv.get('update_time'))}",
           f"- Modell: {conv.get('default_model_slug') or 'unbekannt'}",
           f"- ID: `{conv.get('conversation_id') or conv.get('id')}`", ""]
    for msg in chain:
        role = (msg.get("author") or {}).get("role", "?")
        md = msg.get("metadata") or {}
        if role == "system" or md.get("is_visually_hidden_from_conversation"):
            continue
        c = msg.get("content") or {}
        ctype = c.get("content_type")
        if ctype in ("thoughts", "reasoning_recap", "model_editable_context", "user_editable_context"):
            continue
        body = []
        if ctype in ("text", "multimodal_text"):
            for p in c.get("parts") or []:
                if isinstance(p, str):
                    if p.strip():
                        body.append(clean_text(p))
                elif isinstance(p, dict):
                    pt = p.get("content_type")
                    if pt == "audio_transcription" and p.get("text"):
                        body.append(f"🎙️ {clean_text(p['text'])}")
                    else:
                        for fid in file_ids_in({"content": {"parts": [p]}}):
                            if fid in links:
                                rel = links[fid]
                                if pt == "image_asset_pointer":
                                    body.append(f"![{Path(rel).name}](<{rel}>)")
                                else:
                                    body.append(f"[{Path(rel).name}](<{rel}>)")
        elif ctype == "code":
            body.append(f"```\n{c.get('text', '')}\n```")
        elif ctype == "execution_output":
            body.append(f"```\n{c.get('text', '')}\n```")
        elif isinstance(c.get("text"), str) and c["text"].strip():
            body.append(clean_text(c["text"]))
        # Anhänge (Uploads), die nicht schon als Bild drin stehen
        shown = "\n".join(body)
        for a in md.get("attachments") or []:
            fid = a.get("id")
            if fid in links and links[fid] not in shown:
                body.append(f"📎 [{Path(links[fid]).name}](<{links[fid]}>)")
        body = [b for b in body if b]
        if not body:
            continue
        out.append(f"## {ROLE.get(role, role)} · {fmt_time(msg.get('create_time'))}")
        out.append("")
        out.append("\n\n".join(body))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- Hauptteil

BATCH = 2000             # so viele Dateien pro Abschnitt; danach wird der Abschnitt geprüft
STAGING = "_arbeitsordner"   # im Ziel; nur für Formate, die erst ausgepackt werden müssen

KIND_TEXT = {"zip": "ZIP-Archiv", "tar": "TAR-Archiv", "single": "gepackte Einzeldatei",
             "bsdtar": "Archiv (wird mit tar.exe ausgepackt)", "folder": "Ordner"}


class Abgebrochen(Exception):
    pass


def num(n: int) -> str:
    """1234 -> '1.234'"""
    return f"{n:,}".replace(",", ".")


def _print_log(msg: str, level: str = "info") -> None:
    prefix = {"error": "FEHLER: ", "warn": "Achtung: "}.get(level, "")
    print(prefix + msg, flush=True)


def default_target(src: Path, parent: Path | None = None) -> Path:
    """Ziel: entpackt_<Name> neben dem Archiv (oder in parent). Ist er belegt: (2) usw."""
    base = (parent or src.parent) / f"entpackt_{archive_stem(src)}"
    cand, n = base, 2
    while cand.exists() and (cand.is_file() or any(cand.iterdir())):
        cand = base.with_name(f"{base.name} ({n})")
        n += 1
    return cand


class Progress:
    """Ein Ladebalken über alle Schritte. Jeder Schritt hat ein Gewicht."""

    def __init__(self, tick: Callable[[int, int, str], None], weights: dict[str, float]):
        self.tick, self.w = tick, weights
        self.total = sum(weights.values()) or 1.0
        self.base = 0.0
        self.last = (-1, 0.0)

    def set(self, phase: str, frac: float, text: str, force: bool = False) -> None:
        val = int((self.base + self.w.get(phase, 0) * min(max(frac, 0.0), 1.0)) / self.total * 1000)
        now = time.monotonic()
        if force or val != self.last[0] or now - self.last[1] > 0.2:
            self.last = (val, now)
            self.tick(val, 1000, text)

    def done(self, phase: str, text: str = "") -> None:
        self.base += self.w.get(phase, 0)
        self.set(phase, 0.0, text, force=True)


def run(src_path: Path, dst: Path, hardlink: bool = False, probe: bool = False,
        batch: int = BATCH, log: Callable[[str, str], None] | None = None,
        progress: Callable[[int, int, str], None] | None = None, cancel=None,
        confirm: Callable[[str], bool] | None = None) -> int:
    """Entpackt und sortiert.

    Rückgabe: 0 = fertig, 1 = fertig mit Fehlern, 2 = konnte nicht starten, 3 = abgebrochen,
    4 = vom Bomben-Schutz angehalten, 5 = fertig, aber der Virenscanner hat Dateien blockiert.
    log(text, art): art ist "info", "head", "ok", "warn" oder "error".
    progress(erledigt, gesamt, text) wird laufend aufgerufen.
    cancel: ein threading.Event; ist es gesetzt, wird sauber abgebrochen.
    confirm(frage) -> bool: fragt bei einem verdächtigen Archiv nach. Fehlt es,
    wird bei Verdacht angehalten (sicherer Standard).
    """
    lines: list[str] = []
    out = log or _print_log

    def say(msg: str, level: str = "info") -> None:
        lines.append(f"{datetime.now():%H:%M:%S}  {msg}")
        out(msg, level)

    state = {"started": False, "staging": None, "close": []}
    try:
        return _run(src_path, dst, hardlink, probe, max(1, batch), say,
                    progress or (lambda *a: None), cancel, state, confirm)
    except Abgebrochen:
        say("Abgebrochen. Was schon einsortiert ist, bleibt im Ziel-Ordner.", "warn")
        return 3
    except SchutzStopp as e:
        say(f"🛡 Schutz: {e}", "error")
        return 4
    except ExtractError as e:
        say(f"Auspacken fehlgeschlagen: {e}", "error")
        return 2
    finally:
        for close in state["close"]:
            try:
                close()
            except Exception:
                pass
        if state["staging"] is not None:   # Arbeitsordner weg; das Archiv selbst bleibt
            _remove_tree(state["staging"])
        if state["started"] and not probe:
            try:
                (dst / "Protokoll.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError:
                pass


def _make_writable(p: Path | str) -> None:
    """Schreibschutz entfernen (z. B. haben Dateien aus einer ISO immer Schreibschutz)."""
    try:
        os.chmod(p, os.stat(p).st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _remove_tree(p: Path) -> None:
    def retry(func, path, _exc):
        _make_writable(path)
        try:
            func(path)
        except OSError:
            pass
    shutil.rmtree(p, onexc=retry)


def _copy_one(src, name: str, qpath: Path, hardlink: bool):
    """Holt eine Datei in die Quarantäne (qpath). Gibt (Größe, CRC32, SHA-256) für die Prüfung
    zurück oder einen Fehlertext (mit VIRUS_MARK vorne, wenn wohl der Virenscanner eingriff)."""
    try:
        if src.movable:
            # liegt schon in unserem Arbeitsordner = Quarantäne: nur Prüfsummen nehmen
            p = src.path(name)
            size = p.stat().st_size
            crc, sha = file_hashes(p)
            return size, crc, sha
        qpath.parent.mkdir(parents=True, exist_ok=True)
        if hardlink:
            try:
                if qpath.exists():
                    qpath.unlink()
                os.link(src.path(name), qpath)
                return src.size(name), None, None   # gleiche Datei: nur Größe prüfen
            except OSError:
                pass                                # z. B. anderes Laufwerk -> kopieren
        with src.open(name) as f:
            _, crc, sha = copy_with_hash(f, qpath)
        # Bei einer ZIP zählt die CRC32 aus der ZIP, sonst die beim Lesen berechnete.
        # Die SHA-256 wird beim Lesen der Quelle berechnet (eine ZIP prüft dabei
        # selbst ihre CRC32, also stimmt auch die Quelle).
        want = src.crc(name)
        return src.size(name), (crc if want is None else want), sha
    except OSError as e:
        r = _av_reason(e, ours=src.movable)
        return (VIRUS_MARK + r) if r else f"Kopieren fehlgeschlagen: {e}"
    except (zipfile.BadZipFile, EOFError, NotImplementedError, RuntimeError) as e:
        return f"Kopieren fehlgeschlagen: {e}"


def _release(src, name: str, qpath: Path, target: Path, created: float | None) -> None:
    """Geprüfte Datei aus der Quarantäne an ihren Platz im sortierten Ordner."""
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(qpath, target)
    _make_writable(target)
    t = created or (None if src.movable else src.mtime(name))
    if t:
        os.utime(target, (t, t))


def _run(src_path: Path, dst: Path, hardlink: bool, probe: bool, batch: int,
         say, tick, cancel, state, confirm=None) -> int:
    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise Abgebrochen

    # ---- Was ist das, und darf es los gehen?
    try:
        kind = detect_kind(src_path)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        say(f"Kann die Quelle nicht öffnen: {e}", "error")
        return 2
    if kind == "folder":
        s, d = src_path.resolve(), dst.resolve()
        if d == s or s in d.parents:
            say("Das Ziel darf nicht im Quell-Ordner liegen.", "error")
            return 2
    if not probe and dst.exists() and (dst.is_file() or any(dst.iterdir())):
        say(f"Der Ziel-Ordner ist nicht leer: {dst} – bitte einen neuen oder leeren nehmen.",
            "error")
        return 2
    if probe and kind not in ("zip", "folder"):
        say("Ein Probelauf geht nur mit einer ZIP oder einem Ordner.", "error")
        return 2
    if hardlink and kind != "folder":
        say("Harte Links gehen nur mit einem Ordner. Es wird kopiert.", "warn")
        hardlink = False

    hashing = kind != "folder" and not probe
    staged = kind in EXTRACTORS
    prog = Progress(tick, {"hash1": 1 if hashing else 0, "extract": 1 if staged else 0,
                           "plan": 0.2, "files": 0 if probe else 2, "chats": 0.3,
                           "hash2": 1 if hashing else 0})

    def archive_sha(phase: str, label: str) -> str:
        size = max(src_path.stat().st_size, 1)

        def on_bytes(b: int) -> None:
            check_cancel()
            prog.set(phase, b / size, f"{label} · {b * 100 // size} %")
        return sha256_file(src_path, on_bytes)

    say(f"Quelle: {src_path}", "head")
    say(f"Erkannt: {KIND_TEXT[kind]}")

    # ---- Bomben-Schutz: vorher schätzen, Platz und Verdacht prüfen
    est = estimate_size(kind, src_path)
    if probe:
        if est:
            say(f"Ausgepackt etwa {fmt_bytes(est[0])} in {num(est[1])} Dateien")
        allowed = True
    else:
        allowed = precheck(kind, src_path, dst, est, confirm, say)
    packed = src_path.stat().st_size if kind != "folder" else 0
    guard = Guard(dst, est[0] if est else None, packed, confirm, allowed)

    # ---- Virenscanner: welcher läuft? (jeder, den Windows kennt)
    amsi, av_active, av_label = None, False, ""
    if not probe:
        active = [name for name, on in antivirus_products() if on]
        av_active = bool(active)
        amsi = Amsi()
        state["close"].append(amsi.close)
        if active:
            av_label = f" ({', '.join(active)})"
            say(f"🛡 Virenscanner: {', '.join(active)} – prüft jede Datei in der Quarantäne"
                + (", auch direkt über AMSI" if amsi.ok else ""), "ok")
        elif sys.platform == "win32":
            say("⚠ Kein aktiver Virenscanner gefunden – die Dateien werden NICHT auf Viren geprüft.",
                "warn")

    # ---- 1. SHA-256 vorher
    sha_before = ""
    if hashing:
        say("SHA-256 des Archivs vorher berechnen …", "head")
        sha_before = archive_sha("hash1", "SHA-256 vorher")
        prog.done("hash1")
        say(f"SHA-256 vorher:  {sha_before}")

    # ---- 2. Quelle öffnen (andere Formate erst in den Arbeitsordner auspacken)
    problems: list[str] = []
    if staged:
        state["started"] = True
        dst.mkdir(parents=True, exist_ok=True)
        staging = dst / STAGING
        staging.mkdir()
        state["staging"] = staging
        say("Archiv in den Arbeitsordner auspacken …", "head")
        problems += EXTRACTORS[kind](
            src_path, staging,
            lambda f: prog.set("extract", f, f"Auspacken · {int(f * 100)} %"),
            check_cancel, say, guard)
        prog.done("extract")
        say("Auspacken fertig.", "ok")
        src = FolderSource(staging, movable=True)
    elif kind == "zip":
        src = ZipSource(src_path)
        state["close"].append(src.close)
    else:
        src = FolderSource(src_path)

    all_files = src.files()
    if src.symlinks:
        say(f"{num(src.symlinks)} Verknüpfungen (Symlinks) übersprungen", "warn")
    files_list = [f for f in all_files if not is_junk(f)]
    if len(files_list) < len(all_files):
        say(f"{num(len(all_files) - len(files_list))} System-Dateien übersprungen "
            f"(__MACOSX, Thumbs.db …)")

    # ---- ChatGPT-Export?
    prefix = find_export_prefix(files_list)
    chatgpt = prefix is not None
    files_info, convs = load_export(src, prefix, files_list) if chatgpt else ({}, [])
    conv_by_id = {(c.get("conversation_id") or c.get("id")): c for c in convs}
    if chatgpt:
        say("ChatGPT-Export erkannt ✓ – die Chats werden mit geschrieben.", "ok")
    say(f"Gefunden: {num(len(files_list))} Dateien"
        + (f", {num(len(conv_by_id))} Chats" if chatgpt else ""))
    if not files_list:
        say("Keine Dateien gefunden.", "warn")

    chains = {cid: message_chain(c) for cid, c in conv_by_id.items()}
    seen_in = defaultdict(list)
    for cid, chain in chains.items():
        for msg in chain:
            for fid in file_ids_in(msg):
                if cid not in seen_in[fid]:
                    seen_in[fid].append(cid)

    def owner(fid: str) -> str | None:
        t = (files_info.get(fid) or {}).get("thread")
        if t in conv_by_id:
            return t
        return seen_in[fid][0] if seen_in.get(fid) else None

    # ---- Plan: jede Datei bekommt Namen und Ordner nach Art
    say("Sortiere nach Art …", "head")
    taken: set[str] = set()
    plan: list[tuple[str, Path, float | None]] = []
    rows = []
    fid_link: dict[str, str] = {}
    nf = len(files_list)
    for k, rel in enumerate(files_list):
        check_cancel()
        prog.set("plan", k / max(nf, 1), f"Sortiere Datei {num(k + 1)} von {num(nf)}")
        base = rel.replace("\\", "/").rsplit("/", 1)[-1]

        def head(rel: str = rel) -> bytes:
            try:
                with src.open(rel) as f:
                    return f.read(16)
            except (OSError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
                return b""

        fid, info = None, None
        if chatgpt:
            if base.lower().endswith(".dat"):
                # file_....dat: echten Namen aus dem Export holen
                fid = base[:-4]
                info = files_info.get(fid) or {"name": "", "ext": None, "mime": None,
                                               "category": "other", "created": None,
                                               "thread": None}
                name = fix_ext(info["name"] or fid, info["ext"], info["mime"], head,
                               info["category"])
            elif m := FILE_ID_RE.match(base):
                fid = m.group(0).replace("file-", "file_", 1)
        if info is None:
            info = {"name": base, "created": None}
            name = base
            if not GOOD_EXT.match(os.path.splitext(base)[1]):
                name = base + sniff_ext(head())   # Endung nur ergänzen, wenn erkannt
        name = safe_name(name)
        cat = category_of(name)
        target = unique(dst / cat / name, taken)
        cid = owner(fid) if (chatgpt and fid) else None
        plan.append((rel, target, info.get("created")))
        rows.append([rel, info.get("name") or "", str(target.relative_to(dst)), cat,
                     (conv_by_id[cid].get("title") or "") if cid else "",
                     fmt_time(info["created"]) if info.get("created") else ""])
        if fid:
            fid_link.setdefault(fid, "../" + target.relative_to(dst).as_posix())
    prog.done("plan")

    # Links für die Chats: jeder Chat, der eine Datei erwähnt, bekommt einen Link
    links_by_conv: dict[str, dict[str, str]] = defaultdict(dict)
    for fid, link in fid_link.items():
        cids = set(seen_in.get(fid, []))
        o = owner(fid)
        if o:
            cids.add(o)
        for cid in cids:
            links_by_conv[cid][fid] = link

    # ---- 3. Einsortieren und prüfen, Abschnitt für Abschnitt
    #      (erst in die Quarantäne, dann prüfen – Virenscanner, Größe, CRC32, SHA-256 –,
    #       erst dann in den sortierten Ordner)
    n = len(plan)
    status, shas, errors, viruses = [""] * n, [""] * n, [], []
    verb = "prüfe in der Quarantäne" if src.movable else "kopiere in die Quarantäne"
    if probe:
        say("Probelauf: es wird nichts geschrieben.", "warn")
    else:
        state["started"] = True
        dst.mkdir(parents=True, exist_ok=True)
        staging = dst / STAGING
        staging.mkdir(exist_ok=True)
        state["staging"] = staging
        quarantine = staging / "quarantaene"
        checks = "Virenscanner, Größe, CRC32, SHA-256" if av_active else "Größe, CRC32, SHA-256"
        steps = 0
        sections = (n + batch - 1) // batch
        for k in range(sections):
            a, b = k * batch, min(n, (k + 1) * batch)
            label = f"Abschnitt {k + 1}/{sections}"
            say(f"{label}: Dateien {num(a + 1)}–{num(b)} in die Quarantäne …", "head")
            expect, qpaths = {}, {}
            for i in range(a, b):
                check_cancel()
                rel, target, created = plan[i]
                qpaths[i] = src.path(rel) if src.movable else quarantine / f"{i:07d}"
                if not src.movable and not hardlink:
                    guard.add(src.size(rel), 1)   # Kopie braucht neuen Platz: vorher prüfen
                expect[i] = _copy_one(src, rel, qpaths[i], hardlink)
                steps += 1
                prog.set("files", steps / (2 * n), f"{label} · {verb}: Datei {num(i + 1)} von {num(n)}")

            if av_active:
                time.sleep(0.5)   # dem Echtzeitschutz einen Augenblick geben
            say(f"{label}: prüfe {num(b - a)} Dateien ({checks}) …")
            bad = found = 0
            for i in range(a, b):
                check_cancel()
                rel, target, created = plan[i]
                problem, virus = check_quarantine(qpaths[i], expect[i], amsi, rel, av_label)
                retried = False
                if problem and not virus and not src.movable:
                    say(f"{rel}: {problem} – noch ein Versuch …", "warn")
                    expect[i] = _copy_one(src, rel, qpaths[i], hardlink)
                    problem, virus = check_quarantine(qpaths[i], expect[i], amsi, rel, av_label)
                    retried = True
                if not problem:
                    try:
                        _release(src, rel, qpaths[i], target, created)
                    except OSError as e:
                        r = _av_reason(e, ours=True)
                        problem, virus = ((r + av_label, True) if r
                                          else (f"Einsortieren fehlgeschlagen: {e}", False))
                if virus:
                    found += 1
                    status[i] = f"VIRUS: {problem}"
                    viruses.append(rel)
                    say(f"🦠 {rel}: {problem} – NICHT einsortiert", "error")
                elif problem:
                    bad += 1
                    status[i] = f"FEHLER: {problem}"
                    errors.append(rel)
                    say(f"{rel} → {target.relative_to(dst)}: {problem}", "error")
                else:
                    status[i] = "ok (2. Versuch)" if retried else "ok"
                    shas[i] = expect[i][2] or ""
                    if retried:
                        say(f"{rel}: beim 2. Versuch in Ordnung", "ok")
                steps += 1
                prog.set("files", steps / (2 * n), f"{label} · prüfe Datei {num(i + 1)} von {num(n)}")
            if bad or found:
                say(f"{label}: {num(b - a - bad - found)} von {num(b - a)} in Ordnung"
                    + (f", {num(bad)} Fehler" if bad else "")
                    + (f", 🦠 {num(found)} vom Virenscanner blockiert" if found else ""), "error")
            else:
                say(f"{label}: alle {num(b - a)} Dateien geprüft ✓", "ok")
    prog.done("files")

    # ---- 4. Chats (nur ChatGPT-Export)
    chat_rows = []
    if chatgpt:
        say(f"Schreibe {num(len(conv_by_id))} Chats …", "head")
        chat_file: dict[str, Path] = {}
        taken_chats: set[str] = set()
        for cid, c in sorted(conv_by_id.items(), key=lambda kv: kv[1].get("create_time") or 0):
            date = fmt_time(c.get("create_time"), "%Y-%m-%d")
            title = safe_name(c.get("title") or "Ohne Titel", 70)
            chat_file[cid] = unique(dst / "Chats" / f"{date}_{title}.md", taken_chats)
        ordered = sorted(conv_by_id.items(), key=lambda kv: kv[1].get("create_time") or 0,
                         reverse=True)
        for j, (cid, c) in enumerate(ordered, 1):
            check_cancel()
            links = links_by_conv.get(cid, {})
            text = render_chat(c, chains[cid], links)
            f = chat_file[cid]
            title = (c.get("title") or "Ohne Titel").replace("|", "/")
            chat_rows.append(f"| {fmt_time(c.get('create_time'), '%Y-%m-%d')} "
                             f"| [{title}](<{f.relative_to(dst).as_posix()}>) "
                             f"| {text.count(chr(10) + '## ')} | {len(links)} |")
            if not probe:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
                if c.get("update_time"):
                    os.utime(f, (c["update_time"], c["update_time"]))
            prog.set("chats", j / len(ordered), f"Schreibe Chat {num(j)} von {num(len(ordered))}")
    prog.done("chats")

    # ---- Übersicht und Zuordnung
    # gezählt wird, was wirklich im sortierten Ordner liegt (beim Probelauf: der Plan)
    cat_count = Counter(r[3] for r, st in zip(rows, status) if probe or st.startswith("ok"))
    cats_sorted = sorted(cat_count.items(), key=lambda kv: (-kv[1], kv[0]))
    if not probe:
        ov = [f"# Übersicht: {src_path.name}", "",
              f"- Entpackt: {datetime.now():%Y-%m-%d %H:%M}",
              f"- Dateien: {num(n)} in {num(len(cat_count))} Ordnern"
              + (f", {num(len(conv_by_id))} Chats" if chatgpt else ""), "",
              "## Ordner nach Art", "", "| Ordner | Dateien |", "|---|---|"]
        ov += [f"| [{c}](<{c}/>) | {num(k)} |" for c, k in cats_sorted]
        if chatgpt:
            ov += ["", "## Chats", "", "| Datum | Chat | Nachrichten | Dateien |", "|---|---|---|---|"]
            ov += chat_rows
        (dst / "Übersicht.md").write_text("\n".join(ov) + "\n", encoding="utf-8")
        with open(dst / "Zuordnung.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["quelle", "original_name", "neuer_pfad", "art", "chat", "erstellt",
                        "pruefung", "sha256"])
            w.writerows(row + [st, sh] for row, st, sh in zip(rows, status, shas))

    # ---- 5. Gegenprüfung: SHA-256 nachher
    zip_ok = True
    if sha_before:
        say("SHA-256 des Archivs nachher berechnen (Gegenprüfung) …", "head")
        sha_after = archive_sha("hash2", "SHA-256 nachher")
        prog.done("hash2", "Gegenprüfung fertig")
        say(f"SHA-256 nachher: {sha_after}")
        zip_ok = sha_after == sha_before
        if zip_ok:
            say("Gegenprüfung: Das Archiv ist unverändert ✓", "ok")
        else:
            say("Gegenprüfung: Das Archiv hat sich während des Entpackens verändert!", "error")
        (dst / "SHA256.txt").write_text(
            f"SHA-256 des Archivs: {src_path.name}\n"
            f"vorher:   {sha_before}\n"
            f"nachher:  {sha_after}\n"
            f"Ergebnis: {'gleich – Archiv unverändert' if zip_ok else 'VERSCHIEDEN – Archiv wurde verändert!'}\n\n"
            f"{sha_before} *{src_path.name}\n", encoding="utf-8")

    # ---- Zusammenfassung
    placed = sum(cat_count.values())
    say(f"Fertig: {num(placed)} Dateien in {num(len(cat_count))} Ordnern"
        + (f", {num(len(conv_by_id))} Chats" if chatgpt else "") + ".", "ok")
    if cats_sorted:
        say(" · ".join(f"{c} {num(k)}" for c, k in cats_sorted))
    if not probe:
        if errors:
            say(f"Prüfung: {num(n - len(errors) - len(viruses))} in Ordnung, {num(len(errors))} mit "
                f"Fehler (siehe Zuordnung.csv, Spalte pruefung).", "error")
        elif not viruses:
            say(f"Prüfung: alle {num(n)} Dateien in Ordnung ✓", "ok")
        if viruses:
            say(f"🦠 Der Virenscanner{av_label} hat {num(len(viruses))} Datei(en) blockiert. Sie wurden "
                f"NICHT einsortiert und mit der Quarantäne gelöscht (Unpackt verändert das Archiv nicht). "
                f"Liste: Zuordnung.csv, Spalte pruefung = VIRUS.", "error")
            for rel in viruses[:10]:
                say(f"   🦠 {rel}", "error")
            if len(viruses) > 10:
                say(f"   … und {num(len(viruses) - 10)} weitere", "error")
    if problems:
        say(f"Beim Auspacken gab es {num(len(problems))} Probleme (siehe oben).", "error")
    say(f"Ziel: {dst}")
    if viruses:
        return 5
    return 1 if errors or problems or not zip_ok else 0


# ---------------------------------------------------------------- Fenster (tkinter)

APP_TITLE = "Unpackt"
APP_TAGLINE = "Universal Archive & File Organizer"
APP_CREDITS = f"Designed and programmed by Opus 5.5 and D. Hoffmann. · v{__version__}"
ARCHIVE_TYPES = ("Archive", "*.zip *.7z *.rar *.tar *.tgz *.gz *.bz2 *.tbz2 *.xz *.txz *.zst "
                            "*.lzma *.iso *.cab *.cpio *.xar *.lha *.lzh")

COLORS = {
    "bg": "#f4f6f9", "card": "#ffffff", "line": "#c3ccd8", "accent": "#1f9d74",
    "accent_light": "#e2f4ec", "text": "#1f2933", "muted": "#677483",
    "trough": "#e2e7ee", "log_bg": "#1e2430", "log_fg": "#d8dee9",
}
LEVEL_COLOR = {"info": "#d8dee9", "head": "#88c0d0", "ok": "#a3be8c",
               "warn": "#ebcb8b", "error": "#f28b82"}
DONE_TEXT = {
    0: ("Fertig! Alles entpackt und geprüft ✓", "ok"),
    1: ("Fertig – aber mit Fehlern. Bitte ins Protokoll schauen.", "error"),
    2: ("Konnte nicht starten. Bitte ins Protokoll schauen.", "error"),
    3: ("Abgebrochen.", "warn"),
    4: ("🛡 Vom Schutz angehalten – siehe Protokoll.", "error"),
    5: ("🦠 Fertig – der Virenscanner hat Dateien blockiert. Siehe Protokoll.", "error"),
}


def assets_dir() -> Path:
    """Ordner mit Icon usw. – neben unpackt.py oder in der EXE (PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets"


def set_icon(root) -> None:
    ico, png = assets_dir() / "unpackt.ico", assets_dir() / "unpackt.png"
    try:
        if sys.platform == "win32" and ico.exists():
            root.iconbitmap(default=str(ico))     # alle Größen, scharf in der Taskleiste
        elif png.exists():
            root.iconphoto(True, tk.PhotoImage(file=str(png)))
    except Exception:
        pass   # ohne Icon geht es auch


def make_root() -> tuple[tk.Tk, bool]:
    """Fenster anlegen. Zweiter Wert: geht Ziehen & Ablegen?"""
    if sys.platform == "win32":
        try:   # scharfe Schrift bei Bildschirm-Skalierung
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # eigenes Taskleisten-Symbol statt dem von Python
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Unpackt.Unpackt")
        except Exception:
            pass
    root, dnd = None, False
    if TkinterDnD is not None:
        try:
            root, dnd = TkinterDnD.Tk(), True
        except Exception:   # tkdnd lässt sich nicht laden
            root = None
    if root is None:
        root = tk.Tk()
    set_icon(root)
    return root, dnd


def open_path(p: Path) -> None:
    if sys.platform == "win32":
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


class App:
    def __init__(self, root: tk.Tk, dnd: bool):
        self.root, self.dnd = root, dnd
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self.src: Path | None = None
        self.dst: Path | None = None
        self.target_parent: Path | None = None   # None = neben der ZIP
        self.hover = False
        self.closing = False

        root.title(f"{APP_TITLE} – {APP_TAGLINE}")
        root.configure(bg=COLORS["bg"])
        root.geometry("780x780")
        root.minsize(600, 600)
        self._style()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self.poll)

        self.write("Bereit. Zieh ein Archiv (ZIP, 7z, RAR, TAR …) in die Fläche oben." if dnd else
                   "Bereit. Klick auf die Fläche oben und wähl ein Archiv aus.")
        if not dnd:
            self.write("Ziehen & Ablegen ist aus, weil das Paket tkinterdnd2 fehlt.", "warn")

    # ------------------------------------------------------------ Aufbau

    def _style(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
        s.configure("TFrame", background=COLORS["bg"])
        s.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        s.configure("Muted.TLabel", foreground=COLORS["muted"])
        s.configure("Title.TLabel", font=("Segoe UI Semibold", 17))
        s.configure("Status.TLabel", font=("Segoe UI Semibold", 10))
        s.configure("Tagline.TLabel", foreground=COLORS["accent"], font=("Segoe UI Semibold", 11))
        s.configure("Credits.TLabel", foreground="#98a2ae", font=("Segoe UI", 8))
        s.configure("TButton", padding=(14, 6), background=COLORS["card"], bordercolor=COLORS["line"],
                    lightcolor=COLORS["card"], darkcolor=COLORS["card"])
        s.map("TButton", background=[("disabled", COLORS["bg"]), ("active", COLORS["accent_light"])],
              foreground=[("disabled", "#a7b0bb")])
        s.configure("Accent.TButton", background=COLORS["accent"], foreground="#ffffff",
                    bordercolor=COLORS["accent"], lightcolor=COLORS["accent"], darkcolor=COLORS["accent"])
        s.map("Accent.TButton", background=[("disabled", COLORS["bg"]), ("active", "#188461")],
              foreground=[("disabled", "#a7b0bb")])
        s.configure("Bar.Horizontal.TProgressbar", troughcolor=COLORS["trough"], background=COLORS["accent"],
                    bordercolor=COLORS["trough"], lightcolor=COLORS["accent"], darkcolor=COLORS["accent"],
                    thickness=20)

    def _build(self) -> None:
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=20, pady=(16, 4))
        head = ttk.Frame(top)
        head.pack(anchor="w", fill="x")
        ttk.Label(head, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=APP_TAGLINE, style="Tagline.TLabel").pack(side="left", padx=(10, 0),
                                                                      pady=(8, 0))
        sub = ttk.Label(top, style="Muted.TLabel",
                        text="Packt aus, sortiert nach Art und prüft alles. "
                             "ChatGPT-Exporte werden erkannt.")
        sub.pack(anchor="w", fill="x")
        sub.bind("<Configure>", lambda e: sub.configure(wraplength=max(200, e.width)))

        # Ablage-Fläche
        self.drop = tk.Canvas(self.root, height=150, bg=COLORS["bg"], highlightthickness=0,
                              cursor="hand2")
        self.drop.pack(fill="x", padx=20, pady=10)
        self.drop.bind("<Configure>", lambda e: self._draw_drop())
        self.drop.bind("<Button-1>", lambda e: self.pick())
        if self.dnd:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<DropEnter>>", self._enter)
            self.drop.dnd_bind("<<DropLeave>>", self._leave)
            self.drop.dnd_bind("<<Drop>>", self._dropped)

        # Ziel
        row = ttk.Frame(self.root)
        row.pack(fill="x", padx=20)
        self.target_var = tk.StringVar(value="Ziel: entpackt_<Name> neben dem Archiv")
        ttk.Label(row, textvariable=self.target_var, style="Muted.TLabel").pack(side="left")
        self.btn_target = ttk.Button(row, text="Ziel ändern …", command=self.pick_target)
        self.btn_target.pack(side="right")

        # Ladebalken
        pr = ttk.Frame(self.root)
        pr.pack(fill="x", padx=20, pady=(14, 2))
        self.bar = ttk.Progressbar(pr, style="Bar.Horizontal.TProgressbar",
                                   mode="determinate", maximum=1, value=0)
        self.bar.pack(fill="x")
        info = ttk.Frame(pr)
        info.pack(fill="x", pady=(5, 0))
        self.status_var = tk.StringVar(value="Bereit.")
        self.pct_var = tk.StringVar(value="")
        ttk.Label(info, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        ttk.Label(info, textvariable=self.pct_var, style="Muted.TLabel").pack(side="right")

        # Ganz unten: wer es gemacht hat
        ttk.Label(self.root, text=APP_CREDITS, style="Credits.TLabel").pack(side="bottom",
                                                                            pady=(0, 8))
        # Knöpfe (zuerst unten anheften, damit das Protokoll den Rest bekommt)
        btns = ttk.Frame(self.root)
        btns.pack(side="bottom", fill="x", padx=20, pady=(10, 6))
        self.btn_cancel = ttk.Button(btns, text="Abbrechen", command=self.do_cancel,
                                     state="disabled")
        self.btn_cancel.pack(side="left")
        self.btn_over = ttk.Button(btns, text="Übersicht öffnen", style="Accent.TButton",
                                   command=self.open_overview, state="disabled")
        self.btn_over.pack(side="right")
        self.btn_open = ttk.Button(btns, text="Ordner öffnen", command=self.open_folder,
                                   state="disabled")
        self.btn_open.pack(side="right", padx=(0, 8))

        # Protokoll
        ttk.Label(self.root, text="Protokoll", style="Muted.TLabel").pack(anchor="w", padx=20,
                                                                          pady=(12, 3))
        self.log = ScrolledText(self.root, height=12, bg=COLORS["log_bg"], fg=COLORS["log_fg"],
                                relief="flat", borderwidth=0, font=("Consolas", 10),
                                wrap="word", padx=10, pady=8, insertbackground=COLORS["log_fg"])
        self.log.pack(fill="both", expand=True, padx=20)
        for level, color in LEVEL_COLOR.items():
            self.log.tag_configure(level, foreground=color)
        self.log.tag_configure("time", foreground="#6b7785")
        self.log.configure(state="disabled")

    def _draw_drop(self) -> None:
        c = self.drop
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        busy = self.busy()
        lit = self.hover or busy
        c.create_rectangle(3, 3, w - 3, h - 3, width=2, dash=(6, 4),
                           outline=COLORS["accent"] if lit else COLORS["line"],
                           fill=COLORS["accent_light"] if lit else COLORS["card"])
        if busy:
            icon, main, sub = "⏳", "Wird entpackt …", self.src.name if self.src else ""
        elif self.hover:
            icon, main, sub = "📥", "Loslassen zum Entpacken", ""
        elif self.dnd:
            icon, main, sub = ("📦", "Archiv hier ablegen",
                               "ZIP, 7z, RAR, TAR, ISO … – oder klicken zum Auswählen")
        else:
            icon, main, sub = "📦", "Klicken und Archiv auswählen", "ZIP, 7z, RAR, TAR, ISO …"
        c.create_text(w / 2, h / 2 - 34, text=icon, font=("Segoe UI Emoji", 28))
        c.create_text(w / 2, h / 2 + 14, text=main, fill=COLORS["text"],
                      font=("Segoe UI Semibold", 14))
        c.create_text(w / 2, h / 2 + 42, text=sub, fill=COLORS["muted"], font=("Segoe UI", 10))

    # ------------------------------------------------------------ Ablegen / Auswählen

    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _enter(self, e):
        if not self.busy():
            self.hover = True
            self._draw_drop()
        return e.action

    def _leave(self, e):
        self.hover = False
        self._draw_drop()
        return e.action

    def _dropped(self, e):
        self.hover = False
        paths = self.root.tk.splitlist(e.data)
        if len(paths) > 1:
            self.write("Mehrere Dateien abgelegt – ich nehme die erste.", "warn")
        if paths:
            self.start(Path(paths[0]))
        self._draw_drop()
        return e.action

    def pick(self) -> None:
        if self.busy():
            return
        p = filedialog.askopenfilename(title="Archiv auswählen", filetypes=[ARCHIVE_TYPES,
                                                                           ("Alle Dateien", "*.*")])
        if p:
            self.start(Path(p))

    def pick_target(self) -> None:
        p = filedialog.askdirectory(title="Wo soll der entpackte Ordner hin?")
        if p:
            self.target_parent = Path(p)
            self.target_var.set(f"Ziel: entpackt_<Name> in {Path(p)}")

    # ------------------------------------------------------------ Ablauf

    def start(self, src: Path) -> None:
        if self.busy():
            self.write("Es läuft schon ein Entpacken. Bitte warten oder abbrechen.", "warn")
            return
        if not src.exists():
            self.write(f"Nicht gefunden: {src}", "error")
            return
        self.src = src
        self.dst = default_target(src, self.target_parent)
        self.cancel.clear()

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.bar.configure(maximum=1, value=0)
        self.pct_var.set("0 %")
        self.status_var.set("Starte …")
        self.btn_cancel.configure(state="normal")
        for b in (self.btn_open, self.btn_over, self.btn_target):
            b.configure(state="disabled")
        self.write(f"Ziel: {self.dst}")

        self.worker = threading.Thread(target=self._work, args=(src, self.dst), daemon=True)
        self.worker.start()
        self._draw_drop()

    def _work(self, src: Path, dst: Path) -> None:
        """Läuft im Hintergrund. Spricht nur über die Warteschlange mit dem Fenster."""
        try:
            code = run(src, dst,
                            log=lambda m, lvl: self.q.put(("log", m, lvl)),
                            progress=lambda d, t, s: self.q.put(("prog", d, t, s)),
                            cancel=self.cancel, confirm=self._confirm)
        except Exception as e:
            self.q.put(("log", f"Unerwarteter Fehler: {e!r}", "error"))
            for line in traceback.format_exc().rstrip().splitlines():
                self.q.put(("log", line, "error"))
            code = 2
        self.q.put(("done", code))

    def _confirm(self, text: str) -> bool:
        """Bomben-Schutz fragt nach. Läuft im Hintergrund-Faden: die Frage stellt das Fenster,
        hier wird auf die Antwort gewartet."""
        answer = {"ok": False}
        done = threading.Event()
        self.q.put(("ask", text, answer, done))
        while not done.wait(0.2):
            if self.cancel.is_set():
                return False
        return answer["ok"]

    def poll(self) -> None:
        last = None
        try:
            for _ in range(1000):
                item = self.q.get_nowait()
                if item[0] == "log":
                    self.write(item[1], item[2])
                elif item[0] == "prog":
                    last = item          # nur den neuesten Stand anzeigen
                elif item[0] == "ask":
                    _, text, answer, done = item
                    self.write("🛡 " + text, "warn")
                    answer["ok"] = (not self.closing) and messagebox.askyesno(
                        "Unpackt – Schutz",
                        text.rstrip(".?") + ".\n\nTrotzdem entpacken? (Nur wenn du dem Archiv vertraust.)",
                        icon="warning", default="no")
                    done.set()
                elif item[0] == "done":
                    if last:
                        self._show_progress(*last[1:])
                        last = None
                    self.finish(item[1])
        except queue.Empty:
            pass
        if last:
            self._show_progress(*last[1:])
        if not self.closing or self.busy():
            self.root.after(50, self.poll)

    def _show_progress(self, done: int, total: int, text: str) -> None:
        total = max(total, 1)
        pct = int(done * 100 / total)
        self.bar.configure(maximum=total, value=done)
        self.status_var.set(text)
        self.pct_var.set(f"{pct} %")
        self.root.title(f"{pct} % – {APP_TITLE}")

    def finish(self, code: int) -> None:
        self.worker = None
        text, level = DONE_TEXT.get(code, (f"Beendet (Code {code}).", "warn"))
        self.status_var.set(text)
        self.root.title(f"{APP_TITLE} – {APP_TAGLINE}")
        if code in (0, 1, 5):
            self.bar.configure(value=self.bar.cget("maximum"))
            self.pct_var.set("100 %")
        self.btn_cancel.configure(state="disabled")
        self.btn_target.configure(state="normal")
        if self.dst and self.dst.exists():
            self.btn_open.configure(state="normal")
            if (self.dst / "Übersicht.md").exists():
                self.btn_over.configure(state="normal")
        self._draw_drop()
        if self.closing:
            self.root.destroy()
            return
        self.root.bell()

    def write(self, msg: str, level: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  "), "time")
        self.log.insert("end", msg + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ Knöpfe

    def do_cancel(self) -> None:
        if self.busy():
            self.cancel.set()
            self.btn_cancel.configure(state="disabled")
            self.status_var.set("Breche ab …")

    def open_folder(self) -> None:
        if self.dst and self.dst.exists():
            open_path(self.dst)

    def open_overview(self) -> None:
        if not self.dst:
            return
        try:
            open_path(self.dst / "Übersicht.md")
        except OSError:   # kein Programm für .md eingerichtet
            open_path(self.dst)

    def on_close(self) -> None:
        if not self.busy():
            self.root.destroy()
            return
        if messagebox.askyesno(APP_TITLE, "Das Entpacken läuft noch.\nAbbrechen und schließen?"):
            self.closing = True
            self.cancel.set()


def gui_main(start: Path | None = None) -> int:
    """Öffnet das Fenster. start: Archiv, das gleich entpackt werden soll."""
    if tk is None:
        print("tkinter fehlt – das Fenster geht nicht. Kommandozeile: unpackt.py <Archiv>")
        return 2
    root, dnd = make_root()
    app = App(root, dnd)
    if start is not None:
        root.after(300, lambda: app.start(start))
    root.mainloop()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="unpackt",
        description="Unpackt – Universal Archive & File Organizer. "
                    "Archiv (ZIP, 7z, RAR, TAR, ISO …) oder Ordner entpacken und nach Art sortieren. "
                    "ChatGPT-Exporte werden erkannt. Ohne Angaben öffnet sich das Fenster.",
        epilog="Designed and programmed by Opus 5.5 and D. Hoffmann.")
    ap.add_argument("quelle", type=Path, nargs="?", help="Das Archiv oder ein Ordner")
    ap.add_argument("ziel", type=Path, nargs="?",
                    help="Neuer Ordner für das Ergebnis (Standard: entpackt_<Name> neben dem Archiv)")
    ap.add_argument("--gui", action="store_true",
                    help="Fenster öffnen (mit Quelle: gleich damit anfangen)")
    ap.add_argument("--abschnitt", type=int, default=BATCH,
                    help=f"Dateien pro Abschnitt, danach wird geprüft (Standard: {BATCH})")
    ap.add_argument("--hardlink", action="store_true",
                    help="Harte Links statt Kopien (spart Platz, nur bei Ordner und gleichem Laufwerk)")
    ap.add_argument("--probe", action="store_true", help="Nur zählen, nichts schreiben")
    ap.add_argument("--gross", action="store_true",
                    help="Auch verdächtig große Archive entpacken (Bomben-Schutz fragt dann nicht; "
                         "der Platz-Schutz bleibt)")
    ap.add_argument("--version", action="version", version=f"Unpackt {__version__}")
    a = ap.parse_args()
    # Ohne Konsole (Unpackt.exe, pythonw, Archiv auf die EXE gezogen): immer das Fenster
    if a.gui or a.quelle is None or sys.stdout is None:
        return gui_main(a.quelle)
    dst = a.ziel or default_target(a.quelle)
    code = run(a.quelle, dst, a.hardlink, a.probe, a.abschnitt,
               confirm=(lambda text: True) if a.gross else None)
    if code == 4 and not a.gross:
        print("Tipp: Wenn du dem Archiv vertraust, mit --gross noch einmal starten "
              "(der Platz-Schutz bleibt trotzdem an).")
    return code


if __name__ == "__main__":
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
