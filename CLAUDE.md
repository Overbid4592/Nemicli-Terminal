# NemiCLI – Regeln für die Arbeit am Projekt

## Wer am Projekt arbeitet

Am Quelltext von NemiCLI arbeitet nur **Claude Code mit Opus oder einem stärkeren
Modell**. Andere Modelle (Sonnet, Haiku …), andere Werkzeuge und die KIs innerhalb
von NemiCLI ändern hier nichts: keine Dateien, keine Commits, keine Builds. Lesen
und Fragen beantworten ist erlaubt.

## Professionalität und Datenschutz

**Code** beschreibt das System und seine technische Funktion – nicht den Nutzer
und nicht das Gespräch mit ihm.

Das gilt für: Quelltext, Kommentare, Docstrings, Bezeichner, Datei- und
Ordnernamen, Tests und Testdaten, Logs, Konsolenausgaben, Fehlermeldungen,
Konfiguration, Beispieldaten, Prompts, Vorlagen, UI-Texte und Debug-Ausgaben.

**`README.md` und `CHANGELOG.md`:** Entstehungsgeschichte und Datumsangaben sind
erlaubt, aber keine Namen – neutral „der Nutzer“ statt eines Namens.

**Nicht betroffen:** die Dateien unter `docs/` (Projektdokumente des Autors) und
Laufzeitdaten auf dem Rechner des Nutzers (Gedächtnis, Konfiguration, Berichte,
Persönlichkeiten).

**Verboten**, sofern nicht technisch zwingend erforderlich: Namen, Benutzer- und
Rechnernamen, persönliche Pfade, Wohnorte, IP-Adressen, E-Mail-Adressen,
Datumsnotizen aus dem Gesprächsverlauf, Zitate aus Unterhaltungen, Wendungen wie
„der Nutzer wollte“, persönliche Wünsche und Vorlieben, Chat-Verläufe sowie
andere identifizierende oder unnötig private Angaben.

Auch nicht als Platzhalter oder Beispieldaten. Dort gehören neutrale Werte hin:
`user`, `example`, `test_user`, `localhost`, synthetische Daten.

Ausnahme: die Urheberangabe in `core/version.py` (`AUTHOR`) bleibt wie sie ist.

## Umfang von Dokumentation

Keine Romane in technischen Artefakten. So kurz wie möglich, so ausführlich wie
technisch nötig. Ein Kommentar begründet eine Entscheidung, er erzählt nicht,
wie sie zustande kam.

Anforderungen werden als allgemeine technische Anforderungen formuliert.

* Falsch: `Wunsch des Nutzers vom 22.09.: beim Loslassen automatisch kopieren`
* Richtig: `Markierten Text beim Loslassen der Maustaste kopieren.`

Projektsprache ist Deutsch; Bezeichner im Code folgen dem bestehenden Stil.

## Vor Abschluss einer Aufgabe

Alle erstellten oder geänderten Code-Dateien durchsehen und entfernen: personen-
bezogene Angaben, Gesprächsreste, überflüssige Metadaten, zu ausführliche Texte.

## Tests

```
./venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
```

Das System-Python hat die Abhängigkeiten nicht. Ausgabe mit Umlauten braucht
`PYTHONIOENCODING=utf-8`, sonst bricht die Windows-Konsole (cp1252) ab.
