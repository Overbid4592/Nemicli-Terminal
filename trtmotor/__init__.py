"""trtmotor – TensorRT for RTX als Zusatz-Motor (NVIDIA, eigene Lizenz).

Das SDK gehört nicht zu NemiCLI: Der Nutzer legt den entpackten Ordner selbst unter
Models/TensorRT-RTX/ ab (Programm- oder Daten-Ordner), das Python-Paket daraus kommt ins venv.
Fehlt es oder scheitert etwas, rechnen die gewohnten Motoren weiter.

  laufzeit.py   SDK finden, DLL anbinden, gebaute Netze je Grafikkarte merken, ausführen
  chat.py       Chat-Modelle aus GGUF (Baukasten-Bauarten) als TensorRT-Netz
"""
