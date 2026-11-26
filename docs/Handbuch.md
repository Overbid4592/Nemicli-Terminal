# NemiCLI – Handbuch

NemiCLI ist ein KI-Agent für das Windows-Terminal: Gespräche mit lokalen und Cloud-Modellen, Werkzeuge mit
Freigabe, Gedächtnis und eigene Motoren für Bilder, Video, Musik und Stimme. Kurzüberblick: [README](../README.md).

| Kapitel | Inhalt |
|---|---|
| [Installation](Installation.md) | Voraussetzungen, Setup-Datei, Quellcode, Tests, eigene exe |
| [Erste Schritte](Erste-Schritte.md) | Modell wählen, Bedienung, Arbeitsmodi, Daten-Ordner, Persönlichkeiten |
| [Modelle](Modelle.md) | Ordner, unterstützte Modelle, Video-Modelle selbst laden, TensorRT-RTX |
| [Funktionen](Funktionen.md) | alle Bereiche im Überblick |
| [Befehle](Befehle.md) | Befehle nach Thema, Tasten, Start ohne Oberfläche |

Weitere Dokumente:

- [Datenschutz und Sicherheit](Datenschutz-und-Sicherheit.md) – welche Daten wohin gehen, Schutzmechanismen
- [Architektur](Architektur.md) – Aufbau des Programms
- [Charakterdatei](Charakterdatei.md) – Aufbau der Charakter-Datei einer Persönlichkeit
- [CHANGELOG](../CHANGELOG.md) · [SECURITY](../SECURITY.md) · [CONTRIBUTING](../CONTRIBUTING.md)

## Geplant

- Eigener Windows-Benutzer für NemiCLI, damit Windows selbst den Programm-Ordner sperrt
- Signatur mit einem öffentlich anerkannten Zertifikat
- Den Build bei Herstellern von Virenschutz als Fehlalarm melden

## Version und Lizenz

Die Version besteht aus Haupt- und Paketnummer (z. B. 10.29 Alpha), die Build-Nummer zählt die einzelnen Stände;
`/version` zeigt beides. NemiCLI steht unter der [GPL-3.0](../LICENSE) · © 2026 D. Hoffmann. Das eingebaute
Unpackt (`tools/unpackt.py`) steht unter MIT. Erstellt mit Claude Code (Claude Opus 5.5, Anthropic).
