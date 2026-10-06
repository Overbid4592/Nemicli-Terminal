"""
systemprofil.py - Steckbrief des Rechners: CPU, Grafikkarten, RAM, Platte, Windows.

Ein Ort für alle, die wissen wollen, was der PC kann: Hugging-Face-Downloader, /systemcheck
und die KI (`abfragen` mit was "system"). Ohne PowerShell – Registry, Windows-API über
ctypes, psutil und nvidia-smi. Daraus abgeleitet: womit der eigene Motor rechnet (CUDA gibt
es nur mit NVIDIA, sonst die CPU) und wie viel Speicher ein Modell dort nutzen kann.
"""
from __future__ import annotations

import ctypes
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

WINDOWS_VRAM_MB = 1740           # belegt Windows selbst dauerhaft auf der Hauptkarte
_GRAFIK_KLASSE = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
_CPU_SCHLUESSEL = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
_KEINE_KARTE = ("basic", "virtual", "remote", "parsec", "idd", "mirror", "displaylink", "citrix", "vmware")
_PF_AVX2, _PF_AVX512F = 40, 41    # IsProcessorFeaturePresent
# Rechen-Stufe → Generation (genaue Stufe vor der Hauptnummer)
_GENERATION = {"8.9": "Ada", "7.5": "Turing", "12": "Blackwell", "10": "Blackwell", "9": "Hopper",
               "8": "Ampere", "7": "Volta", "6": "Pascal"}


@dataclass
class Grafikkarte:
    name: str
    hersteller: str               # nvidia · amd · intel · ?
    vram_mb: int = 0
    integriert: bool = False
    cuda_cap: str = ""            # z. B. "12.0" (= sm_120), nur NVIDIA
    treiber: str = ""

    @property
    def generation(self) -> str:
        """NVIDIA-Generation aus der Rechen-Stufe."""
        if not self.cuda_cap:
            return ""
        if self.cuda_cap in _GENERATION:
            return _GENERATION[self.cuda_cap]
        return _GENERATION.get(self.cuda_cap.split(".")[0], "")


@dataclass
class Profil:
    cpu: str = "?"
    cpu_hersteller: str = "?"     # amd · intel · arm · ?
    kerne: int = 0
    threads: int = 0
    avx2: bool = False
    avx512: bool = False
    ram_mb: int = 0
    ram_frei_mb: int = 0
    gpus: list[Grafikkarte] = field(default_factory=list)
    cuda_max: float = 0.0         # höchste CUDA-Version, die der NVIDIA-Treiber kann
    windows: str = ""
    build: int = 0
    platte_frei: int = 0          # Bytes auf dem Laufwerk des Programm-Ordners

    @property
    def nvidia(self) -> Optional[Grafikkarte]:
        karten = [g for g in self.gpus if g.hersteller == "nvidia"]
        return max(karten, key=lambda g: g.vram_mb) if karten else None

    @property
    def haupt_gpu(self) -> Optional[Grafikkarte]:
        if self.nvidia:
            return self.nvidia
        eigene = [g for g in self.gpus if not g.integriert] or self.gpus
        return max(eigene, key=lambda g: g.vram_mb) if eigene else None

    @property
    def motor(self) -> str:
        """Womit der eigene GGUF-Motor rechnet: "cuda" (NVIDIA) oder "cpu"."""
        return "cuda" if self.nvidia else "cpu"

    @property
    def vram_nutzbar_mb(self) -> int:
        g = self.nvidia
        return max(0, g.vram_mb - WINDOWS_VRAM_MB) if g else 0

    def zeilen(self) -> list[str]:
        """Kurzer Text, z. B. für die KI."""
        z = [f"CPU:     {self.cpu} · {self.kerne} Kerne / {self.threads} Threads"
             + (" · AVX-512" if self.avx512 else " · AVX2" if self.avx2 else ""),
             f"RAM:     {self.ram_mb / 1024:.0f} GB (frei {self.ram_frei_mb / 1024:.0f} GB)"]
        for g in self.gpus:
            teile = [g.name]
            if g.vram_mb:
                teile.append(f"{g.vram_mb / 1024:.0f} GB")
            if g.integriert:
                teile.append("integriert")
            if g.cuda_cap:
                teile.append(f"sm_{g.cuda_cap.replace('.', '')}" + (f" {g.generation}" if g.generation else ""))
            if g.treiber:
                teile.append(f"Treiber {g.treiber}")
            z.append("GPU:     " + " · ".join(teile))
        if not self.gpus:
            z.append("GPU:     keine erkannt")
        z.append("Motor:   " + (f"CUDA auf {self.nvidia.name}" + (f" (bis CUDA {self.cuda_max:g})" if self.cuda_max
                                                                     else "") if self.nvidia
                                else "CPU – CUDA braucht eine NVIDIA-Karte"))
        z.append(f"System:  {self.windows} (Build {self.build}) · frei auf der Platte {self.platte_frei / 2**30:.0f} GB")
        return z


# ---------------------------------------------------------------------------
# Einzelne Teile
# ---------------------------------------------------------------------------

def _reg(schluessel: str, wert: str):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, schluessel) as k:
            return winreg.QueryValueEx(k, wert)[0]
    except Exception:
        return None


def _cpu(p: Profil) -> None:
    name = _reg(_CPU_SCHLUESSEL, "ProcessorNameString")
    p.cpu = " ".join(str(name).split()) if name else "?"
    hersteller = str(_reg(_CPU_SCHLUESSEL, "VendorIdentifier") or "").lower()
    p.cpu_hersteller = ("amd" if "amd" in hersteller else "intel" if "intel" in hersteller
                        else "arm" if "arm" in hersteller or "qualcomm" in p.cpu.lower() else "?")
    try:
        import psutil
        p.kerne = psutil.cpu_count(logical=False) or 0
        p.threads = psutil.cpu_count() or 0
        m = psutil.virtual_memory()
        p.ram_mb, p.ram_frei_mb = m.total // 2**20, m.available // 2**20
    except Exception:
        import os
        p.threads = os.cpu_count() or 0
    try:
        k32 = ctypes.windll.kernel32
        p.avx2 = bool(k32.IsProcessorFeaturePresent(_PF_AVX2))
        p.avx512 = bool(k32.IsProcessorFeaturePresent(_PF_AVX512F))
    except Exception:
        pass


def _hersteller(name: str) -> str:
    n = name.lower()
    if any(x in n for x in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(x in n for x in ("radeon", "amd", "firepro")):
        return "amd"
    if any(x in n for x in ("intel", "arc", "iris", "uhd")):
        return "intel"
    return "?"


def _integriert(name: str, hersteller: str) -> bool:
    n = name.lower()
    if hersteller == "intel":                      # Arc A770/B580 sind eigene Karten, „Arc Graphics“ nicht
        return not re.search(r"arc(?:\(tm\))?\s+[ab]\d{3}", n)
    if hersteller == "amd":
        return bool(re.search(r"radeon\(tm\)\s+graphics|radeon\s+graphics|vega\s+\d+\s+graphics", n)) \
            and not re.search(r"\brx\b", n)
    return False


def _registry_karten() -> list[Grafikkarte]:
    try:
        import winreg
    except ImportError:
        return []
    karten: list[Grafikkarte] = []
    try:
        klasse = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _GRAFIK_KLASSE)
    except OSError:
        return []
    with klasse:
        i = 0
        while True:
            try:
                unter = winreg.EnumKey(klasse, i)
            except OSError:
                break
            i += 1
            if not unter.isdigit():
                continue
            try:
                with winreg.OpenKey(klasse, unter) as k:
                    def wert(n):
                        try:
                            return winreg.QueryValueEx(k, n)[0]
                        except OSError:
                            return None
                    name = str(wert("DriverDesc") or "").strip()
                    if not name or any(x in name.lower() for x in _KEINE_KARTE):
                        continue
                    if any(g.name == name for g in karten):
                        continue
                    speicher = wert("HardwareInformation.qwMemorySize") or wert("HardwareInformation.MemorySize")
                    if isinstance(speicher, (bytes, bytearray)):
                        speicher = int.from_bytes(speicher[:8], "little")
                    h = _hersteller(name)
                    karten.append(Grafikkarte(name=name, hersteller=h,
                                              vram_mb=int(speicher or 0) // 2**20,
                                              integriert=_integriert(name, h),
                                              treiber=str(wert("DriverVersion") or "")))
            except OSError:
                continue
    return karten


def _nvidia_karten() -> tuple[list[Grafikkarte], float]:
    import setup as S
    aus = S._run(["nvidia-smi", "--query-gpu=name,memory.total,compute_cap,driver_version",
                  "--format=csv,noheader,nounits"])
    karten = []
    for zeile in aus.strip().splitlines():
        teile = [t.strip() for t in zeile.split(",")]
        if len(teile) < 4 or not teile[0]:
            continue
        vram = int(re.sub(r"\D", "", teile[1]) or 0)
        cap = teile[2] if re.fullmatch(r"\d+\.\d+", teile[2]) else ""
        karten.append(Grafikkarte(name=teile[0], hersteller="nvidia", vram_mb=vram, cuda_cap=cap, treiber=teile[3]))
    cuda = 0.0
    if karten:
        m = re.search(r"CUDA(?:\s+UMD)?\s+Version:\s*([\d.]+)", S._run(["nvidia-smi"]))
        cuda = float(m.group(1)) if m else 0.0
    return karten, cuda


def _windows(p: Profil) -> None:
    try:
        v = sys.getwindowsversion()
        p.build = v.build
        p.windows = "Windows 11" if v.build >= 22000 else f"Windows {v.major}"
    except Exception:
        import platform
        p.windows = platform.platform()


def _platte(p: Profil) -> None:
    try:
        from paths import INSTALL
        p.platte_frei = shutil.disk_usage(Path(INSTALL).anchor or str(INSTALL)).free
    except Exception:
        p.platte_frei = 0


# ---------------------------------------------------------------------------

_PROFIL: Optional[Profil] = None


def erkennen(neu: bool = False, prozesse: bool = True) -> Profil:
    """Den Rechner einmal ansehen (Ergebnis bleibt für die Sitzung, RAM frei/Platte neu).
    prozesse=False (Werkzeug `abfragen`, das keine Prozesse startet): ohne nvidia-smi, nur
    Registry – oder das Ergebnis, das schon da ist; so ein Teilbild wird nicht gemerkt."""
    global _PROFIL
    if (_PROFIL is None or neu) and not prozesse:
        p = Profil()
        _cpu(p)
        p.gpus = _registry_karten()
        _windows(p)
        _platte(p)
        return p
    if _PROFIL is None or neu:
        p = Profil()
        _cpu(p)
        nv, p.cuda_max = _nvidia_karten()
        andere = [g for g in _registry_karten() if g.hersteller != "nvidia" or not nv]
        p.gpus = nv + andere
        _windows(p)
        _platte(p)
        _PROFIL = p
    else:
        try:
            import psutil
            _PROFIL.ram_frei_mb = psutil.virtual_memory().available // 2**20
        except Exception:
            pass
        _platte(_PROFIL)
    return _PROFIL
