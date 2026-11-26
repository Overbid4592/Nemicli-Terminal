# Erste Schritte

[Handbuch](Handbuch.md) · [Installation](Installation.md) · **Erste Schritte** · [Modelle](Modelle.md) ·
[Funktionen](Funktionen.md) · [Befehle](Befehle.md)

## Modell wählen

`/model` öffnet die Auswahl, beim ersten Start ohne Modell von selbst.

- **Cloud:** „Cloud-Anbieter hinzufügen“, API-Schlüssel eingeben. Er wird verschlüsselt im Tresor gespeichert.
  Mit `/model chatgpt` geht auch ein ChatGPT-Plus/Pro-Konto statt eines Schlüssels.
- **Lokal:** `/model huggingface` lädt ein GGUF-Modell mit SHA-256-Prüfung, oder eine `.gguf` liegt schon in
  `KI-Modelle/KI-Chat/Chatmodell/<Name>/`.
- **Ollama, LM Studio, Jan, llama.cpp-Server, vLLM:** laufen separat und erscheinen von selbst.

## Bedienung

- Nachrichten direkt eintippen, Befehle beginnen mit `/`. Beim Tippen von `/` erscheint die Befehlsliste,
  `Strg+K` sucht in allen Befehlen, Einstellungen und Modellen. Oder einfach die KI fragen.
- Will die KI etwas ändern, zeigt ein **Prüffenster** die Änderung vorher; **F8** gibt sie frei.
- **Esc** hält alles sofort an (Antwort, Werkzeug, Bild, Video) – gelöscht wird nichts, „weiter“ setzt fort.
- **Arbeitsmodus** mit `/modus` oder `Shift+Tab`:

| Modus | Verhalten |
|---|---|
| 💬 `chat` | jede Aktion fragt |
| 👁 `lesen` | nur lesen, Ändern gesperrt |
| ⚙ `normal` | Lesen läuft sofort, Ändern fragt |
| ⚡ `auto` | ohne Rückfrage im aktuellen Ordner, nach 5 Minuten zurück auf `chat`; Riskantes fragt weiter |
| 🚀 `autoan` | wie `auto`, bleibt an bis zum Umschalten |

## Die ersten Befehle

| Befehl | Zweck |
|---|---|
| `/control` | Controllcenter: alle Einstellungen, Gesundheit, Favoriten, Tresor |
| `/persönlichkeiten` | Persönlichkeit wählen oder anlegen |
| `/name` | was die KI über dich wissen soll |
| `/gui` | Desktop-Fenster |
| `/help` · `/help <befehl>` | Befehle nach Thema · einen Befehl erklären |
| `/selbsttest` · `/systemcheck` | Installation · Rechner prüfen |
| `/backup` · `/undo` | Sicherung · Gelöschtes zurückholen |

Alle Befehle: [Befehle](Befehle.md).

## Wo deine Daten liegen

Den Ort wählst du beim ersten Start (`/start` zeigt und ändert ihn). Die wichtigsten Ordner:

```
<Daten-Ordner>/
├─ chats/ · Gespraeche/   Gespräche · mit F12 gesicherte Gespräche
├─ learned/               Gedächtnis, Abläufe, Aktions-Protokoll
├─ Profile/<Name>/        je Persönlichkeit: Chats, Bilder, Videos, Musik, Skills, Charakter-Datei
├─ Persoenlichkeiten/     eigene Persönlichkeiten (.md)
├─ Bilder/ · Berichte/    Bilder und Screenshots · Berichte von Wache und Zeitplan
├─ NemiSandbox/           Sandbox für Code der KI
├─ Papierkorb/            Gelöschtes und Überschriebenes (30 Tage, /undo)
└─ Backup/                Sicherungen (Zip + .sha256)
```

Ist der Daten-Ordner beim Start nicht erreichbar, sagt NemiCLI das, statt mit leerem Gedächtnis zu starten.

## Persönlichkeiten

Nemi ist eingebaut; eigene legst du mit `/persönlichkeiten neu` oder als `.md`-Datei an. Jede Persönlichkeit hat
ihr eigenes Profil mit Chats, Gedächtnis und Bildern und sieht die Bereiche der anderen nicht. Das feste Aussehen
für Bilder steht in der [Charakterdatei](Charakterdatei.md).
