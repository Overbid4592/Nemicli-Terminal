# NemiCLI – Regeln für die Arbeit am Projekt

## Projektleitung und Zuständigkeiten

Der Repository-Eigentümer leitet NemiCLI und entscheidet über Aufgaben,
Zugriffsrechte, Freigaben, Änderungen und Veröffentlichungen.

**Quellcode, Tests und Builds:** Nur ausdrücklich vom Eigentümer zugelassene
KI-Modelle dürfen sie ändern oder erstellen. Ihr Leistungsniveau muss nach
Einschätzung des Eigentümers mindestens Claude Opus 5.5 entsprechen.
Modellname, Selbsteinschätzung oder vorhandene GitHub-Rechte allein sind
keine Arbeitsfreigabe.

**Repository-Pflege:** Der Eigentümer darf ausgewählten Assistenten Aufgaben
wie Dokumentationspflege, Metadaten, Issues und Pull-Request-Prüfungen
übertragen. Sie arbeiten nur im beauftragten Umfang. Änderungen an
Sicherheitsregeln, CI-Workflows, Zugriffsrechten und Releases benötigen
einen konkreten Auftrag. Nicht für Quellcode freigegebene Assistenten dürfen
ihn lesen, prüfen und Verbesserungsvorschläge machen, aber nicht verändern.

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
