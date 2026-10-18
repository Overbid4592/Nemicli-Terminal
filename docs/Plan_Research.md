# Plan: Research-Modus, Fachquellen, Ollama web_fetch

Stand: 8. Oktober 2026 · gebaut (exe-Build siehe CHANGELOG)

## Ausgangslage

| Vorhanden | Grenze |
|---|---|
| `web_suche` (Ollama Web Search, max. 10 Treffer) | nur Schnipsel, einziger Suchdienst |
| `web_lesen` (HTML/Text/PDF, Teile, Cache) | nur Allowlist (231 Domains, 4 Tiers) |
| `web_wiki` | nur Wikipedia |
| `subagenten` (bis 5 parallel) | nur Cloud-Modell |
| `pdf_erstellen`, `Berichte/` | – |

Vorbilder: GPT Researcher, Stanford STORM, LangChain Open Deep Research. Gemeinsamer
Ablauf: planen → parallel suchen → lesen → Lücken prüfen → Bericht mit Quellen.
Kein Fremdprojekt einbauen (MCP, Docker, viele Abhängigkeiten) – die Bausteine sind da.

## Teil 1: Ollama web_fetch

`POST https://ollama.com/api/web_fetch` mit `{"url": …}` → `title`, `content`, `links`.
Gleicher Schlüssel wie die Suche (`OLLAMA_API_KEY`).

- `web_lesen` bleibt wie es ist für Allowlist-Seiten (direkt, mit allen Prüfungen).
- Seite **nicht** in der Allowlist → Abruf über Ollama `web_fetch` (Ollama lädt, nicht der PC).
- Markierung als eigene Stufe „unbekannte Seite“: Fremddaten-Rahmen, Injection-Prüfung,
  Web-Taint wie bisher (kein Schreiben ohne Rückfrage in derselben Runde).
- Nur `https`, keine privaten Adressen, Länge in Teilen wie bei `web_lesen`.
- Schalter `/web fremdseiten an|aus` (Vorgabe: an). Ohne Schlüssel: Hinweis wie bei der Suche.
- Ampel in `web_suche`: „✓ lesbar“ / „◐ lesbar über Ollama (unbekannte Seite)“.

## Teil 2: Fachquellen ohne Schlüssel

Neues Werkzeug `fach_suche` (Felder: `quelle`, `suche`, optional `anzahl`):

| quelle | Schnittstelle | Liefert |
|---|---|---|
| `arxiv` | export.arxiv.org/api/query (Atom) | Titel, Autoren, Datum, Abstract, PDF-Link |
| `pubmed` | eutils.ncbi.nlm.nih.gov esearch + esummary | Titel, Journal, Datum, PMID-Link |
| `github` | api.github.com/search/repositories | Name, Sterne, Beschreibung, letzte Änderung |
| `stackoverflow` | api.stackexchange.com/2.3/search/advanced | Frage, Score, beantwortet?, Link |
| `openalex` | api.openalex.org/works | Titel, Jahr, Zitate, DOI |

- Allowlist: `openalex.org` als Tier 1 ergänzen (Rest ist schon drin).
- Ergebnisse als Fremddaten, mit Lese-Ampel wie `web_suche`.
- Freie Limits beachten (GitHub ohne Schlüssel 10/min, Stack Exchange 300/Tag): kurzer Cache.

## Teil 3: Research-Modus

Werkzeug `recherche` (Felder: `frage`, optional `tiefe` 1–3) – ohne eigenen Befehl: die KI entscheidet selbst zwischen Schnellsuche und Recherche.
Eigenes Modul `tools/recherche.py`, Ablauf im Code (nicht dem Modell überlassen):

1. **Planen:** Modell zerlegt die Frage in 3–6 Teilfragen. Anzeige im Terminal/`/gui`;
   startet ohne Rückfrage, Esc stoppt.
2. **Suchen:** je Teilfrage `web_suche` + passende `fach_suche` (Modell wählt die Quelle).
3. **Lesen:** die besten 2–4 Treffer je Teilfrage ganz lesen (`web_lesen`, sonst web_fetch).
   Pro Quelle eine kurze Notiz mit Quellennummer – nur das, was zur Teilfrage passt.
   Lokale Modelle: Notizen statt Volltext im Kontext (passt in kleine Fenster).
4. **Prüfen:** Lücken und Widersprüche suchen; bei `tiefe` > 1 neue Teilfragen, nächste Runde.
   Aussagen mit nur einer Quelle als solche kennzeichnen.
5. **Bericht:** Markdown mit `[1]`-Verweisen und Quellenliste (Titel, URL, Tier, Abrufdatum)
   nach `Berichte/Recherche_<JJJJ-MM-TT>_<thema>.md`; auf Wunsch PDF.

- Parallel über die Helfer-Mechanik von `subagenten`, wenn ein Cloud-Modell aktiv ist;
  lokal nacheinander.
- Fortschritt live (Teilfrage x/y, gelesene Quellen), Abbruch mit Esc, Zwischenstand bleibt.
- Obergrenzen: Seiten je Lauf, Runden, Zeit – im Code, nicht im Prompt.
- Gehört der aktiven Persönlichkeit nicht exklusiv: Berichte liegen im gemeinsamen `Berichte/`.
- Selbstwissen nachziehen: `persona.py` (Werkzeuge) und `commands.py` (`/web`).

## Reihenfolge

1. web_fetch (klein, macht Teil 3 stärker)
2. `fach_suche`
3. Research-Modus
4. Tests je Teil (Netz gemockt), Selbsttest, exe

## Offen

- Bericht standardmäßig auch als PDF? (Vorschlag: nein, nur auf Wunsch)
