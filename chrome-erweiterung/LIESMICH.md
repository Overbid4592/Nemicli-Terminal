# 🐈 NemiCLI – Chrome-Erweiterung

**Seitenleiste:** Klick aufs 🐈-Symbol oben rechts → rechts öffnet sich ein Chat mit der aktiven
Persönlichkeit. Es ist derselbe Chat wie im NemiCLI-Fenster und in `/gui`.

- **📄 Seite** schickt die Seite mit (oder nur den markierten Text, wenn du etwas markiert hast).
- **📸 Foto** macht ein Bildschirmfoto der Seite und schickt es mit.
- Die Antwort erscheint live. Gemalte Bilder siehst du direkt in der Leiste.
- **＋ Neu** startet einen neuen Chat.
- Schreibt sie dir einen Text vor (Antwort, Mail, Post), steht er umrandet da: **✍ Ins Textfeld**
  setzt ihn dort ein, wo auf der Seite zuletzt dein Cursor stand, **⧉ Kopieren** kopiert ihn.
- Hat die Persönlichkeit Bilder für die Kugel, siehst du ihr Bild – in ihrer Stimmung, beim Nachdenken mit dem Denk-Bild.

Die Leiste kann wie der Modus „Chat“ in `/gui` nur reden, Bilder malen und ansehen, das Gedächtnis
und Skills nutzen. Am PC ändert sie nichts. Seiteninhalt gilt als Fremdinhalt: Will die KI danach
etwas merken, fragt eine Karte in der Leiste erst nach; Skills ändert sie in so einer Runde gar nicht.

**Rechtsklick:** **„Nemi antwortet (ins Textfeld)“** liest die Seite (E-Mail, Chat, Formular),
schreibt in deinem Namen eine Antwort und setzt sie in das Textfeld mit dem Cursor. Text markieren →
**„Nemi, erklär mir das“** zeigt eine Erklärung als Kästchen auf der Seite.

Die Erweiterung redet **nur** mit deinem laufenden NemiCLI auf `127.0.0.1` (Port 9000) und
braucht dafür einen Geheimschlüssel. Nichts geht ins Internet.

## Einrichten (einmalig, 1 Minute)

1. NemiCLI starten, `/chrome` tippen → zeigt Port und Schlüssel.
2. Chrome → Adresse `chrome://extensions` → oben rechts **Entwicklermodus** an.
3. **„Entpackte Erweiterung laden“** → diesen Ordner (`chrome-erweiterung`) wählen.
4. Bei der Erweiterung → **Details** → **Erweiterungsoptionen** → Port und Schlüssel eintragen
   → **Speichern**, dann **„Verbindung testen“**.
5. Tipp: Das Puzzle-Symbol in Chrome → 📌 bei NemiCLI, dann ist das 🐈-Symbol immer sichtbar.

War die Erweiterung schon geladen: unter `chrome://extensions` bei NemiCLI auf **↻ Neu laden**
klicken (Chrome fragt dann einmal nach dem Zugriff auf Seiten, das braucht das Bildschirmfoto).

Läuft NemiCLI nicht, sagt die Leiste: „NemiCLI läuft nicht“.

## Dateien

- `manifest.json` – Beschreibung der Erweiterung (Chrome 116 oder neuer)
- `background.js` – Rechtsklick-Menü, Symbol öffnet die Seitenleiste
- `seitenleiste.html` / `.css` / `.js` – der Chat
- `markdown.js` – formatiert die Antworten (ohne fremde Bibliotheken)
- `content.js` – liest die Seite, setzt die Antwort ins Feld, zeigt Hinweise
- `options.html` / `options.js` – Port und Schlüssel
