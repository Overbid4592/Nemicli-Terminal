"""liveportrait – Porträts bewegen (Kopf, Augen, Mund), eigener Motor in reinem PyTorch.

Getrennt von Krea, Wan und dem GGUF-Motor; nichts dort hängt hiervon ab. Bewegt wird das
Originalbild selbst (Verformen über Schlüsselpunkte, kein Diffusionsmodell) – die Identität
bleibt, der Körper steht still. Gewichte: Models/LivePortrait (gewichte.py).
Werkzeug: portraet_bewegen (tools/actions.py).
"""
