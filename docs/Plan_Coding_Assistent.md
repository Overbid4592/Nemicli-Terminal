# Plan: Coding-Assistent

Stand: 06.10.2026 · Status: **Stufe 1–3 + 7 gebaut, 4–5 teilweise; Ergänzungen A–J gebaut** (siehe Bau-Reihenfolge und Ergänzungen)

---

## Warum

Testlauf „Sprout“ (Chat 68): NemiCLI sollte einen kleinen Agenten selbst bauen.
Ergebnis: Der Chat war schön, die Arbeit hatte Widersprüche.

| Muster | Beispiel aus dem Test |
|---|---|
| Ziel driftet weg | „500M + Thinking“ → 115.000 Parameter + eine „und“-Regel |
| „Fertig“ = „stürzt nicht ab“ | Kauderwelsch wurde als Erfolg gemeldet |
| Folgen nicht nachgezogen | Ollama raus, README/start.bat/alte Dateien blieben |
| Persönlichkeit färbt das Urteil | Denktext „ehrlich sein“, Antwort „unser Modell ❤️“ |
| Protokoll frisst den Kopf | halbe Seite Grübeln über `\\\\` im JSON |

Der Arbeitsablauf steht heute nur als Text im Prompt (`core/persona.py`, Schritte 1–9).
Das Modell liest ihn und hält sich nicht dran.

**Kernidee: Das Programm gibt den Weg vor. Die KI denkt.**

---

## Grundsätze

1. **Kein zweites Modell.** Der Coding-Assistent ist eine Anleitung, kein eigener Agent.
   Dieselbe KI, dasselbe Modell – nur auf einem festen Weg geführt.
2. **Universell.** Jede Coding-Aufgabe, jede Persönlichkeit, jedes Modell.
   Nichts ist auf ein Thema fest verdrahtet.
3. **Das Programm erzwingt, der Prompt erklärt.** Was wichtig ist, prüft der Code
   (Sperren, Tests, Suchen) – nicht nur ein Satz im Prompt.
4. **Die Persönlichkeit bleibt.** Charme ja. Aber die Farben in der Todo-Liste
   setzt das Programm, nicht die Laune.

---

## Ablauf

```
Du redest mit der Persönlichkeit
   │
   ▼
0. Erkennen       Coding-Aufgabe (auch klein: „leg da ein venv an“) → Assistent startet
   │              und bleibt an, bis der Nutzer ihn beendet
   ▼
╔═ 🟦 Coding-Assistent ════════════════════════════════════╗
║ 1. Auftragskarte   Ziel · Muss-Kriterien · Nicht-Ziele   ║
║ 2. Recherche       GitHub/Web: README + Code LESEN       ║
║                    Notizen, nichts kopieren              ║
║ 3. Durchdenken     → Todo-Liste mit Prüfkriterien        ║
║                    → du nickst ab                        ║
║ 4. Agentloop       Punkt für Punkt abarbeiten            ║
║ 5. Prüfen          Tests + Folgen-Check + Kopier-Check    ║
║ 6. Abschluss       Auftrag ↔ Ergebnis, ehrlicher Bericht ║
╚══════════════════════════════════════════════════════════╝
```

### 0. Erkennen

- **Jede** Coding-Aufgabe startet den Assistenten – auch kleine wie „leg da ein venv an“.
  Kein Nachfragen. Die Persönlichkeit sagt in einem Satz, dass er jetzt an ist.
- Werkzeug `coding_start` (Felder: `projekt`, `aufgabe`), das die KI selbst aufruft.
- **Bleibt aktiv**, auch über mehrere Nachrichten und Themenwechsel innerhalb des Projekts.
  Aus nur durch den Nutzer: `/codeend` oder klare Ansage („Coding aus“).
- Zusätzlich direkt: `/code <aufgabe>`.
- **Oberflächen:** Terminal und Desktop-Fenster (`/gui`). WebUI vorerst nicht.
- **Kein Workspace nötig.** Auftrag, Todo und Recherche liegen immer im Projektordner
  unter `.nemicli/`. Ist ein `/workspace` aktiv, muss das Projekt darin liegen.
  (Nicht `.workspace/`: dort gibt es schon eine `Auftrag.md` mit anderem Inhalt.)

### 1. Auftragskarte

Kurz, drei Teile:

```
Ziel:          Ein kleiner Agent, der Dateien liest und schreibt.
Muss:          - startet über start.bat
               - antwortet auf Fragen mit sinnvollen Sätzen
Nicht-Ziele:   - kein Cloud-Modell
```

- Liegt in `.nemicli/Auftrag.md`.
- Geht etwas nicht (z. B. „500M von Null“), steht das **hier** als Abweichung
  und wird als 🟠-Punkt zur Frage – nicht still durch etwas Kleineres ersetzt.

### 2. Recherche (Vorab-Informationen)

- **Die KI entscheidet selbst**, ob Recherche nötig ist (neues Thema: ja; „venv anlegen“: nein).
  Lässt sie sie weg, steht das mit einem Satz Begründung in der Auftragskarte.
- Suche auf GitHub/Web: Was gibt es zum Thema aktuell?
- 2–3 passende Projekte: README und Kern-Code **lesen**.
- Ergebnis: `.nemicli/Recherche.md` – Ideen, Aufbau, Quellen-Links. **Kein Code.**
- Gelesener fremder Code wird für den Kopier-Check (Schritt 5) gemerkt.

### 3. Durchdenken → Todo-Liste

Die KI erstellt die Liste. Jeder Punkt hat ein **Prüfkriterium**:

```
┌─ 🟨 Todo ───────────────────────────────────────────────┐
│ 🟢 venv anlegen            ✓ python --version im venv   │
│ ▶🔴 Agent-Schleife bauen    ✓ Test: 2 Aktionen in Folge  │
│ 🟠 Welches Format?          ? wartet auf Antwort         │
│ 🔴 README passend zum Code  ✓ Folgen-Check sauber        │
└─────────────────────────────────────────────────────────┘
```

| Farbe | Bedeutung | Wer setzt sie |
|---|---|---|
| 🔴 Rot | noch nicht fertig | Startzustand |
| 🟠 Orange | Frage offen | KI (mit der Frage dazu) |
| 🟢 Grün | getestet und geprüft | **nur das Programm**, wenn die Prüfung bestanden ist |
| ⚪ Grau | gestrichen | Nutzer (die KI schlägt vor, mit Grund) |

- `▶` markiert den Punkt, der gerade in Arbeit ist. Immer nur **einer**.
- Die Liste liegt in `.nemicli/todo.json` (lesbar: `Todo.md`) → nach Neustart geht es weiter.
- Du nickst die Liste ab, bevor der Agentloop startet.

### 4. Agentloop

- Arbeitet die Liste **von oben nach unten** ab.
- Die Schrittbremse zählt **pro Todo-Punkt**, nicht pro Nachricht
  (heute: `MAX_STEPS = 12` pro Nachricht – zu wenig für ein Projekt).
- Bei 🟠 hält die Schleife an und fragt dich.
- Ende: alle Punkte 🟢 – oder ein Punkt hängt fest (dann ehrlich melden).
- Esc bricht ab, die Liste bleibt erhalten.

### 5. Prüfen

Ohne zweites Modell. Drei Teile:

| Prüfung | Wie |
|---|---|
| **Test** | Das Prüfkriterium des Punktes wird ausgeführt (Befehl, Testlauf). Das Programm wertet das Ergebnis aus, nicht die KI allein. |
| **Folgen-Check** | Nach dem Schreiben sucht das Programm im Projekt nach Namen, die geändert oder gelöscht wurden (README, .bat, requirements, Importe). Treffer gehen als Aufgabe zurück an die KI. |
| **Kopier-Check** | Neuer Code wird mit dem gelesenen fremden Code verglichen. Längere gleiche Stücke → Warnung, Punkt bleibt 🔴. |

Danach ein **Prüf-Schritt derselben KI**: Sie bekommt nur Prüfkriterium und
Testausgabe und muss mit „erfüllt“ oder „nicht erfüllt + Grund“ antworten.
Ohne Persönlichkeit, ohne Chatverlauf – damit nichts schöngeredet wird.

### 6. Abschluss

Feste Form, vom Programm vorgegeben:

```
✅ Fertig und geprüft:   …
❌ Nicht geschafft:      …
⚠️ Abweichung vom Auftrag: …
❓ Ungetestet:           …
```

Erst danach darf die Persönlichkeit frei reden.

---

## Ergänzungen (aus der Arbeitsweise von Claude Code)

| # | Ergänzung | Gegen welches Muster |
|---|---|---|
| 1 | **Erst lesen, dann ändern** – `datei_bearbeiten` auf eine Datei, die in dieser Sitzung nicht gelesen wurde, wird abgewiesen. | Raten, was in der Datei steht |
| 2 | **Auftrag + Todo bei jeder Runde oben im Prompt** – frisch aus `.nemicli/`, auch nach AutoContextCleaner. | Ziel driftet weg |
| 3 | **Ein Punkt in Arbeit** – erst abschließen, dann der nächste. | Halbfertiges überall |
| 4 | **Code ohne JSON-Escaping** – Dateiinhalt in einem eigenen Block nach der Aktion, nicht im JSON-String. | Protokoll frisst den Kopf |
| 5 | **Aufräumen ist ein Todo-Punkt** – nach einem Umbau automatisch: „Alte Teile entfernen, Doku angleichen“. | README/start.bat veraltet |
| 6 | **Zahlen statt Gefühl** – Abschluss nennt Fakten (Dateien, Tests bestanden/gesamt, Größe). | „Läuft alles ❤️“ |

---

## Anknüpfung im Code

| Was | Wo |
|---|---|
| Agentloop | `main.py` → `_converse` (Zeile ~551), `web_converse` |
| Schrittbremse | `main.py` → `MAX_STEPS`, `_schritt_grenze` |
| Projektordner, Auftrag, Absprache | `core/workspace.py` (`.workspace/`, `Auftrag.md` gibt es schon) |
| Werkzeuge | `tools/actions.py` |
| Prompt-Regeln | `core/persona.py` → `_REGELN` |
| Web lesen | `tools/webfetch.py` |
| Rahmen, Farben | `main.py` → `_render` (Rich) |

---

## Bau-Reihenfolge

| # | Stufe | Stand |
|---|---|---|
| 1 | **Todo-Liste** – Datei, Werkzeug, gelber Kasten mit 🔴🟠🟢⚪, nur Programm/Nutzer setzt 🟢 | ✅ gebaut |
| 2 | **Blauer Rahmen + `/code`** – Modus an/aus, Auftragskarte, Todo am Ende des Prompts | ✅ gebaut |
| 3 | **Agentloop pro Todo-Punkt** – Schrittbremse je Punkt (gesamt 80), Halt bei 🟠 | ✅ gebaut |
| 4 | **Prüfen** – Prüfbefehl ✅, Abschluss-Form im Prompt ✅; offen: Folgen-Check, Prüf-Schritt derselben KI | ◐ teilweise |
| 5 | **Recherche** – Anweisung im Prompt ✅; offen: `Recherche.md`, Kopier-Check | ◐ teilweise |
| 6 | **Desktop-Fenster** – Rahmen und Todo-Liste auch in `/gui` | offen |
| 7 | **Erkennen** – jede Coding-Aufgabe startet den Assistenten automatisch (`coding_start`) | ✅ gebaut |
| 8 | **Ergänzungen 1, 4** – Lese-Pflicht, Code ohne JSON-Escaping | offen |

Jede Stufe einzeln testbar. Danach Sprout-Test wiederholen und vergleichen.

---

## Nächste Ideen (besprochen 02.10.2026, noch nicht geplant)

Ziel: Die KI soll in Python, HTML, CSS und JS weniger Fehler machen, die sie selbst nicht bemerkt.

| # | Idee | Kern |
|---|---|---|
| A | **Sofort-Check nach jedem Schreiben** | Python `py_compile`; HTML: offene Tags, fehlende `src`/`href`-Dateien, IDs aus dem JS; CSS/JS: Syntax mit reinen Python-Paketen, **ohne Node/npm**. Fehler mit Zeile zurück an die KI. |
| B | **Seite ansehen** | Edge (in Windows dabei) unsichtbar starten, Bildschirmfoto der Seite → die KI sieht ihr HTML/CSS. Als Prüfung für Web-Punkte nutzbar. |
| C | **Offline-Doku nachschlagen** | Offizielle Python-Doku und MDN (HTML/CSS/JS) in die vorhandene Vektor-Datenbank (Agent-RAG); vor Unbekanntem erst nachschlagen. Feste Quelle statt Websuche. |
| D | **Werkzeug-Suche** | Im Prompt nur Name + ein Satz je Werkzeug, ganze Beschreibung bei Bedarf laden (wie `anleitung_lesen`) – weniger Ballast für kleine Modelle. |
| E | **Ende vorschlagen** | Sind alle Punkte 🟢, fragt die KI, ob der Assistent aus soll. Aus bleibt trotzdem nur durch den Nutzer. |

Dazu die offenen Stufen oben: Folgen-Check, Kopier-Check, Prüf-Schritt, `/gui`, Lese-Pflicht, Code ohne JSON-Escaping.
Vorschlag für den Start: **A + B**.

**Stand 06.10.2026: A–E gebaut**, dazu F–J (Versions-Blick, Nachschlagen in der Installation, Veraltetes melden mit ruff, Neuigkeiten-Check über PyPI/OSV, Lehren zu installierten Paketen). B nimmt jeden Chromium-Browser (Edge, Chrome, Brave …), C ist ein Volltext-Index (SQLite FTS5) statt der Vektor-Datenbank – der Bibliothekar liest nur 3 Wissen-Dateien je Runde, die Doku hat Tausende.

**Bewusst ausgeschlossen: MCP.** NemiCLI bindet keine MCP-Server an – Werkzeuge bleiben eigener Code.
