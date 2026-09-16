# 🐈 NemiCLI – Nemi antwortet (Chrome-Erweiterung)

Rechtsklick auf einer Webseite → **„Nemi antwortet (ins Textfeld)“**: Lara liest die Seite
(E-Mail, Chat, Formular), schreibt in deinem Namen eine Antwort und setzt sie direkt in das
Textfeld, in dem dein Cursor steht. Text markieren → **„Nemi, erklär mir das“** zeigt eine
Erklärung als Kästchen auf der Seite.

Die Erweiterung redet **nur** mit deinem laufenden NemiCLI auf `127.0.0.1` (Port 9000) und
braucht dafür einen Geheimschlüssel. Nichts geht ins Internet.

## Einrichten (einmalig, 1 Minute)

1. NemiCLI starten, `/chrome` tippen → zeigt Port und Schlüssel.
2. Chrome → Adresse `chrome://extensions` → oben rechts **Entwicklermodus** an.
3. **„Entpackte Erweiterung laden“** → diesen Ordner (`chrome-erweiterung`) wählen.
4. Bei der Erweiterung → **Details** → **Erweiterungsoptionen** → Port und Schlüssel eintragen
   → **Speichern**, dann **„Verbindung testen“**.

## Nutzen

- Cursor ins Antwortfeld setzen → Rechtsklick → „🐈 Nemi antwortet (ins Textfeld)“.
  Unten rechts erscheint „Nemi liest und schreibt …“, dann steht die Antwort im Feld.
  Im NemiCLI-Fenster siehst du die Runde wie immer.
- Text markieren → Rechtsklick → „🐈 Nemi, erklär mir das“.

Läuft NemiCLI nicht, sagt die Seite: „NemiCLI läuft nicht“.

## Dateien

- `manifest.json` – Beschreibung der Erweiterung
- `background.js` – Rechtsklick-Menü, spricht mit NemiCLI
- `content.js` – liest die Seite, setzt die Antwort ins Feld, zeigt Hinweise
- `options.html` / `options.js` – Port und Schlüssel
