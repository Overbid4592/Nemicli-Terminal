"""Einrichtungs-Helfer – ein kleines, von null trainiertes Sprachmodell für /einrichten.

Der Code führt die Schritte (welche es gibt, was installiert wird); das Modell versteht die Antworten des Nutzers
und formuliert selbst. Laufzeit (`laufzeit.py`) in reinem Python – beim ersten /einrichten gibt es weder numpy
noch torch. Training (`training.py`) braucht torch und läuft nur in der Werkstatt.
"""
