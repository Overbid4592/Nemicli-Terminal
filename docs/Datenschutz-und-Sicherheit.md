# Datenschutz und Sicherheit

Dieses Dokument beschreibt, welche Daten NemiCLI verarbeitet, wo sie gespeichert werden, wann Daten den Rechner
verlassen und welche Schutzmechanismen im Quellcode umgesetzt sind. Zu jeder Aussage ist das Modul angegeben, in dem
das Verhalten implementiert ist.

NemiCLI ist Alpha-Software. Die beschriebenen Mechanismen verringern Risiken, sie bieten keine absolute Sicherheit.

## 1. Grundsatz: lokal oder Cloud

NemiCLI kann mit lokalen Modellen und mit Cloud-Modellen arbeiten. Welche Daten den Rechner verlassen, hängt vom
gewählten Modell ab:

| Modellart | Verarbeitung des Gesprächs |
|---|---|
| Lokales Modell im eigenen GGUF-Motor, Ollama, LM Studio, Jan, llama.cpp-Server, vLLM | auf dem eigenen Rechner (Verbindungen nur zu `127.0.0.1`) |
| Cloud-Modell (z. B. Anthropic, OpenAI, Google, Mistral) | beim jeweiligen Anbieter; es gelten dessen Datenschutz- und Nutzungsbedingungen |

Auch bei lokalen Modellen können einzelne Werkzeuge Verbindungen ins Internet aufbauen, etwa die Websuche
(siehe [Abschnitt 4](#4-netzwerkverbindungen)).

## 2. Lokal gespeicherte Daten

NemiCLI trennt Programm und Daten (`core/paths.py`):

| Ort | Inhalt |
|---|---|
| Programmordner (Setup: `C:\Users\<Benutzer>\.nemicli`) | Programm, Einstellungen (`nemicli.config.json`), Arbeitsumgebung (`venv`) |
| Datenordner (beim ersten Start gewählt, änderbar mit `/start`) | Gespräche (`chats/`, `Gespraeche/`), Gedächtnis (`learned/`), Profile der Persönlichkeiten (`Profile/`), Bilder, Videos, Musik, Berichte, Wache-Daten, Papierkorb, Sicherungen |
| `%LOCALAPPDATA%` (versteckter Unterordner) | verschlüsselter Schlüsselspeicher (`.tresor`, siehe [Abschnitt 5](#5-api-schlüssel)) |

Alle Daten bleiben in diesen Ordnern. Beim Wechsel des Datenordners wird kopiert, nicht gelöscht. Sicherungen
(`/backup`, tägliche Autosicherung, `tools/autobackup.py`) werden lokal als Zip mit SHA-256-Prüfsumme abgelegt.

## 3. Datenübertragung an Cloud-Anbieter

Ist ein Cloud-Modell aktiv, sendet NemiCLI für jede Anfrage an diesen Anbieter:

- den Systemprompt (Persönlichkeit, Regeln, Werkzeugbeschreibungen),
- den Gesprächsverlauf der laufenden Sitzung,
- Stellen aus dem Gedächtnis, die für die Anfrage gefunden wurden,
- Ergebnisse von Werkzeugen, die die KI in dieser Runde genutzt hat (z. B. gelesene Dateiinhalte, Befehlsausgaben,
  Webseiten),
- Bilder, die angehängt oder mit einem Werkzeug angesehen werden.

Einige Funktionen arbeiten ausschließlich mit Cloud-Modellen und übertragen dabei zusätzliche Daten:

| Funktion | Übertragene Daten |
|---|---|
| Wache-KI im Wache-Fenster (`tools/wachezentrale.py`) und Bewertung von Wache-Alarmen | Angaben zum Alarm: Programmname, Pfad, Kommandozeile, Signatur, Regel |
| Auswertung des Debugberichts (Werkzeug `debugbericht`, `core/debugbericht.py`) | Auszüge des Berichts: Speicherauslastung, Modellwechsel, Fehler, Prozessliste der größten Speicherverbraucher |
| Bild- und Videodienste aus der Cloud (`/bildmodel cloud`, `/video cloud`) | Prompt und gegebenenfalls Eingabebild |

Werden diese Funktionen nicht genutzt oder ist kein Cloud-Modell eingerichtet, findet keine solche Übertragung statt.

## 4. Netzwerkverbindungen

Im Quellcode gibt es keine Telemetrie, keine Nutzungsstatistik und keine automatische Fehlermeldung an Dritte.
Verbindungen entstehen ausschließlich durch die folgenden Funktionen:

| Verbindung | Auslöser | Gesendete Daten |
|---|---|---|
| Cloud-Anbieter der Sprach-, Bild- und Videomodelle | Nutzung eines Cloud-Modells, Abruf der Modellliste unter `/model` | siehe Abschnitt 3; API-Schlüssel zur Anmeldung |
| Webseiten aus der Allowlist (`allowlist.json`, 251 Domains) | Web-Werkzeuge der KI | Abruf der Seite |
| Andere Webseiten | nur über Ollama (`web_fetch`), Einstellung `/web fremdseiten frei\|fragen\|aus` | Abruf der Seite |
| Fachquellen (arXiv, PubMed, OpenAlex, Stack Exchange, GitHub) | Recherche-Werkzeuge der KI | Suchbegriffe |
| Sicherheitslisten von uBlock Origin (`tools/schutzliste.py`) | automatisch, wenn der lokale Stand älter als 24 Stunden ist | nur der Abruf der Listen; Adressen des Nutzers werden nicht übertragen, der Abgleich läuft lokal |
| Hugging Face | Modell-Downloads (`/einrichten modelle`, Hugging-Face-Seite im Controllcenter) erst nach Bestätigung | Abruf der Modellangaben und Dateien |
| GitHub | `/llamacpp aktualisieren` | Abruf der Release-Angaben und Dateien |
| PyTorch-Paketindex, PyPI, OSV.dev | `/update`, `/update torch`, Paketprüfung | Paketnamen und Versionen |
| RDAP-Adressregister | Netzwächter, wenn eine Adresse nachgeschlagen wird | die nachgeschlagene IP-Adresse |
| Google Fonts, cdnfonts | WebUI im Browser, solange `webui_fremde_schriften` aktiv ist (Vorgabe: an) | der Browser lädt Schriftdateien; dabei wird die IP-Adresse an diese Dienste übertragen |
| Git-Remote des Repositorys | `/update` im Skriptmodus | `git pull` |

**Netz-Zwang** (`core/netzzwang.py`): In allen Prozessen von NemiCLI, in Befehlen der KI und in Python-Programmen, die
NemiCLI startet, werden unverschlüsselte Verbindungen (`http://`, `ws://`) zu anderen Rechnern abgebrochen.
Unverschlüsselt sind nur Verbindungen zu `127.0.0.1` auf freigegebenen Ports erlaubt (lokale Modellserver,
`/web lokal`). `0.0.0.0` ist weder als Ziel noch als Lausch-Adresse zulässig.

Alle Dienste, die NemiCLI selbst anbietet (WebUI, Brücke zur Chrome-Erweiterung, Fenster-Verbindungen), lauschen nur
auf `127.0.0.1` und verlangen ein Token oder einen Schlüssel.

## 5. API-Schlüssel

API-Schlüssel und Anmeldedaten liegen im Tresor (`core/tresor.py`):

- Verschlüsselung mit AES-256-GCM über die Windows-Kryptografie (CNG).
- Der AES-Schlüssel ist mit DPAPI an das Windows-Benutzerkonto und den Rechner gebunden. Eine kopierte Tresor-Datei
  ist auf einem anderen Rechner oder unter einem anderen Konto nicht lesbar.
- Ein Siegel (SHA-256 der Tresor-Datei, gespeichert in der Registry unter `HKCU`) erkennt Veränderungen von außen.
  Passt die Datei nicht zum Siegel, wird der Tresor verworfen und die Schlüssel müssen neu eingetragen werden.
- Schlüssel aus einer älteren `.env` werden beim Start in den Tresor übernommen; die alte Datei wird überschrieben
  und gelöscht.
- Programme, die die KI startet, erhalten keine Umgebungsvariablen mit Schlüsseln, Tokens oder Passwörtern
  (`core/config.py`).
- In der Oberfläche werden Schlüssel nur gekürzt angezeigt (`core/keyvault.py`).

Programme, die unter demselben Windows-Konto laufen, können DPAPI-geschützte Daten grundsätzlich entschlüsseln.
Der Schutz richtet sich gegen das Kopieren der Dateien, nicht gegen Schadsoftware im selben Benutzerkonto.

## 6. Berechtigungen

- NemiCLI wird ohne Administratorrechte installiert und ausgeführt.
- Die KI erhält keine Administratorrechte. Rechteerhöhungen in Befehlen werden abgewiesen (`tools/sicherheit.py`).
- Eine Rückfrage der Benutzerkontensteuerung (UAC) kann nur entstehen, wenn der Nutzer im Wache-Fenster selbst eine
  geplante Windows-Aufgabe deaktiviert oder löscht, die Administratorrechte erfordert.
- Die Systemwache liest mit Benutzerrechten Prozesse, Dienste, geplante Aufgaben, Autostart-Einträge,
  Ereignisprotokolle und Netzwerkverbindungen. Sie greift nicht in Virenschutzprogramme ein.
- Code der KI zum Ausprobieren läuft in einem Windows-AppContainer (`tools/sandbox.py`): Internetzugang ja, Zugriff
  auf Nutzerdateien nein.
- Die Chrome-Erweiterung verbindet sich ausschließlich mit der lokalen Brücke auf `127.0.0.1`.

## 7. Persönlichkeiten, Charakterprofile und Gedächtnis

- Persönlichkeiten und Charakterdateien sind Textdateien im Datenordner. Sie bestimmen nur den Prompt; die
  Schutzregeln sitzen im Code und gelten für jede Persönlichkeit.
- Jede Persönlichkeit arbeitet in ihrem eigenen Profil. Gespräche, Gedächtnis, Erinnerungen und Aufgaben anderer
  Profile sind für sie nicht zugänglich.
- Das Gedächtnis (SQLite-Datenbanken und Vektoren in `learned/`) wird lokal berechnet und gespeichert. Jeder Eintrag
  trägt seine Herkunft (Nutzer, KI, Werkzeug, Netz, Import). Werkzeug- und Webinhalte gelten nie als Aussage des
  Nutzers.
- Wird eine Persönlichkeit gelöscht, werden ihre Einträge aus dem Suchindex entfernt.
- Chat-Exporte anderer Dienste (ZIP) werden erst nach Bestätigung entpackt und eingelesen.
- An ein Cloud-Modell gehen Gedächtnisinhalte nur, soweit sie für die aktuelle Anfrage gefunden wurden.

## 8. Protokolle

| Protokoll | Ort | Inhalt |
|---|---|---|
| Debugbericht (`core/debugbericht.py`) | `Debugbericht/` im Datenordner, die 30 neuesten Dateien | Speicherauslastung, Modellwechsel, Werkzeugaufrufe, Fehler, größte Speicherverbraucher; abschaltbar mit der Einstellung `debug` |
| Motor-Protokoll | neben dem Debugbericht | Prompts und Rechenschritte des lokalen Motors; nur mit der Einstellung `debug_motor` |
| Aktions-Protokoll | `learned/` | ausgeführte Werkzeuge und Befehle der KI |
| Wache | `Wache/` | Ereignisse, Alarme, Urteile |

Protokolle werden nicht automatisch übertragen. Zur Weitergabe an ein Cloud-Modell siehe Abschnitt 3.

## 9. Schutzmechanismen gegenüber der KI

Eine Auswahl der im Code umgesetzten Grenzen (Überblick: [Funktionen](Funktionen.md#sicherheit)).

- Verändernde Aktionen zeigen vorher ein Prüffenster mit Vorher/Nachher und werden erst mit F8 freigegeben.
- Gelöschte und überschriebene Dateien landen im Papierkorb (`/undo`); mehr als 20 Löschungen je Sitzung werden
  gestoppt.
- Systemordner und die System-Registry sind nur lesbar. Eigene Sperrorte lassen sich in `schreibsperre.json` festlegen;
  der Programmordner ist fest gesperrt.
- Inhalte aus dem Netz, aus Dateien und aus dem Gedächtnis werden als Daten gerahmt, nicht als Anweisungen behandelt.
  Solange Netzinhalt im Gespräch steht, fragen Befehle, Schreiben und Merken nach.
- Netzfreigaben (`\\rechner\…`) und Gerätepfade werden abgewiesen, bevor ein Werkzeug sie öffnet.
- Bilder und Videos zeigen ausschließlich Erwachsene; dies ist fest im Code verankert und nicht abschaltbar.
- Modelldateien werden mit Grenzprüfungen gelesen; eingebettete Chat-Vorlagen werden nicht ausgeführt.

## 10. Bekannte Grenzen

- Die Prüfung von Befehlen ist ein Textfilter. Ein Textfilter kann nicht jede Umgehung erkennen.
- DPAPI schützt nicht vor Programmen, die im selben Windows-Konto laufen (Abschnitt 5).
- Die exe ist mit einem eigenen Zertifikat (`CN=NemiCLI`) signiert, nicht mit einem öffentlich anerkannten. Prüfsummen
  (SHA-256) liegen jedem Release bei.
- Die Systemwache ergänzt einen Virenschutz, sie ersetzt ihn nicht.
- Für die Verarbeitung durch Cloud-Anbieter gelten deren Bedingungen; NemiCLI hat darauf keinen Einfluss.

## 11. Sicherheitslücken melden

Bitte nicht öffentlich als Issue, sondern wie in [SECURITY.md](../SECURITY.md) beschrieben.
