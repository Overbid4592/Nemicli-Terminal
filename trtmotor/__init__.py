"""trtmotor – TensorRT for RTX als Zusatz-Motor (NVIDIA, eigene Lizenz).

Das SDK gehört nicht zu NemiCLI: Der entpackte Ordner liegt unter Models/TensorRT-RTX/
(Programm- oder Daten-Ordner), das Python-Paket daraus kommt ins venv.
Fehlt es oder scheitert etwas, rechnen die gewohnten Motoren weiter.

  laufzeit.py    SDK finden, DLL anbinden, gebaute Netze je Grafikkarte merken, ausführen
  bausteine.py   gemeinsame Bausteine der Netze (Formen, NVFP4/FP8, Normen)
  chat.py        langes Prompt-Lesen der Chat-Modelle aus GGUF (Baukasten-Bauarten)
  bild.py        DiT-Blöcke von Krea 2
  video.py       DiT-Blöcke von Wan 2.2 5B
"""
