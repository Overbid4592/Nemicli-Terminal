"""LoRA-Motor: Coding-LoRA für lokale GGUF-Modelle, getrennt vom GGUF-Motor.

Der GGUF-Motor bleibt unverändert. Der LoRA-Motor liest dessen geladene Modelle nur:
`adapter` klinkt ein LoRA von außen an die Schichten eines laufenden Modells an und
wieder ab, `lernweg` rechnet mit denselben Gewichten einen eigenen, differenzierbaren
Vorwärtsweg, `training` trainiert daraus ein LoRA aus der Coding-Sammlung.
"""
