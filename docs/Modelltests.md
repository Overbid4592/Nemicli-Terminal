# Modelltests – lokale Sprachmodelle für NemiCLI

Welche lokalen Modelle taugen für NemiCLI? Getestet im eigenen GGUF-Motor (`ggufengine/`,
`/model` → „Lokal (eigener Motor)“) mit NemiCLIs vollständiger Anleitung (~11.300 Token,
~40 Werkzeuge) auf einer RTX 5060 Ti (16 GB).

## Grundregeln

- **Sprachmodelle unter 4B sind tabu** – zu schwach für die Agenten-Aufgaben.
  Ausgenommen: Embedding-Modelle und ähnliche Hilfsmodelle.
- Jede Empfehlung stützt sich auf echte, mit F12 gesicherte Gespräche (`Gespraeche/`).

## Übersicht

| Modell | Größe | Quant | VRAM (128k) | Tempo | Sehen | Urteil |
|---|---|---|---|---|---|---|
| Gemma-4-E4B-it | 4,5B eff. / 8B | Q4_K_M | ~6 GB | 30 tok/s (Grenze), ~57 ohne | ✅ (mmproj) | ⚠️ **nur mit Vorsicht – eher für Erfahrene** |
| Qwen3.5-9B | 9B | Q4_K_M | ~12 GB | – | ✅ (mmproj) | 🧪 Test läuft |
| K2-Horizon-7B | 9B | Q4_K_M | ~9 GB bei 16k | ~50 tok/s ohne Grenze | – (Bildbeschreiber) | ❌ **raus** – vom Nutzer wieder gelöscht |
| DeepSeek-R1-Distill-Qwen-7B | 7B | Q4_K_M | – | ~57 tok/s ohne Grenze | – (Bildbeschreiber) | ❌ **nicht empfohlen** – halluziniert |

## Gemma-4-E4B-it (Unsloth, Q4_K_M) – 26.09.2026

**Urteil: nur mit Vorsicht empfehlenswert, eher für erfahrene Nutzer**, die Aussetzer erkennen
und gegensteuern können.

**Stärken**
- Werkzeuge meist richtig gewählt: `bild_malen`, `bildschirm_ansehen`, `oeffnen`, `web_suche`,
  `datei_kopieren`.
- Ehrlich bei Grenzen: ohne Sehen hat es keinen Bildinhalt erfunden.
- Web-Recherche sauber aus den Quellen zusammengefasst.
- Sehen (mmproj): erkennt Farben, Formen, Positionen und groben Bildschirminhalt richtig;
  großen Text auf Testbildern liest es.
- Sehr sparsam: 128k Kontext in ~6 GB VRAM; nach der ersten Runde antwortet es in 0,1–0,5 s.

**Schwächen** (Chat 21 und 22)
- Verwechselt die eingeblendeten Gedächtnis-Treffer mit der eigentlichen Frage und antwortet
  am Thema vorbei.
- Versteht kurze Anweisungen („nochmal“, „jetzt“, „Wie bitte?“) oft falsch – einmal mit einer
  ungefragten Aktion (Datei kopiert).
- Befolgt lange Anleitungen nur teilweise (z. B. Krea-Prompts als Schlagwortliste statt Sätzen).
- Kleine Behauptungen ohne Deckung (etwas „notiert“, ohne `merken`).
- Text auf 4K-Screenshots ist mit 280 Bild-Token zu klein zum Lesen.
- Verspricht, Halluzinationen selbst zu melden – das kann ein Modell dieser Größe nicht verlässlich.

**Einstellungen im Test:** Temperatur 1,0, top_p 0,95, top_k 64 (Google-Vorgabe), Denkmodus aus,
Kontext 131072, Grenze 30 Token/s.

## Qwen3.5-9B (Q4_K_M, 5,3 GB)

- Erster Versuch in NemiCLI: **leere Antworten** – ein Fehler im Chat-Format des Motors
  (Denkblock mit einzelnen statt doppeltem Zeilenumbruch), nicht im Modell. Behoben;
  auf der CPU antwortet es danach sauber auf Deutsch.
- Speicher: 32 KB Gesprächsspeicher pro Token (doppelt so viel wie Gemma-4-E4B) – bei 128k
  ~4 GB zusätzlich zu den Gewichten; mit `/kontext 32k` ~1 GB.
- Sehen: ja, mit eigenem Qwen3-VL-Encoder (mmproj BF16, 0,86 GB). Testbild mit Kreis, Quadrat,
  Dreieck und Text: Formen, Farben, Lage und „NEMI 42“ richtig; Bild mit 1000 Token in 1,6 s
  kodiert, Antwort in 10,6 s, Nachfrage 0,4 s.

_Test im Alltag folgt._

## K2-Horizon-7B (IFM, Q4_K_M, 5,6 GB)

**Urteil: raus – wieder gelöscht.** Die Unterstützung im Motor bleibt erhalten.

- Neue Bauart `k2-horizon`, im Motor nachgebaut; Tokenizer gegen das Original geprüft (identisch).
- 144 KB Gesprächsspeicher pro Token: 32k ≈ 4,5 GB, 64k ≈ 9 GB – mehr als 32k passt auf 16 GB
  kaum; die VRAM-Automatik begrenzt dann selbst.
- Kurztest: klares Deutsch, Folgefragen aus dem Cache, mit NemiCLIs Anleitung ein sauberer
  Werkzeug-Aufruf (`ordner_auflisten`), auf „Hi“ eine normale Begrüßung.
- **Ohne Denken** Rechenfehler („3 Katzen haben 6 Beine“), **mit Denken** richtig (16 Beine,
  17:25 Uhr) – das Denken ist kurz (1–2 s). Empfehlung laut Modellkarte: Denkmodus an
  (`gguf_denken`), Temperatur 1,0, top_p 0,95.

## DeepSeek-R1-Distill-Qwen-7B (Unsloth, Q4_K_M, 4,7 GB)

**Urteil: nicht empfohlen – wieder gelöscht.** In NemiCLI (Chat 27) kam auf ein einfaches
„Hi du da 🙂 ❤️“ ein wirrer Denktext auf Deutsch mit erfundenen Wörtern („Bronnenplan“,
„Filialkämpfe“) und erfundenen Vorlieben des Nutzers. Mit kurzer Anleitung war es im Test klar;
mit NemiCLIs ~11.300-Token-Anleitung nicht. Ob allein das Modell schuld ist oder auch der Motor
bei langem Kontext mit Qwen2, ist nicht geprüft.

- Erster Versuch in NemiCLI: Fehler „model has no special token '<|im_start|>'“ – der Motor
  kannte das DeepSeek-Format noch nicht. Behoben.
- Kurztest: denkt immer zuerst (auf Englisch, landet auf F2), antwortet auf Deutsch; Folgefragen
  aus dem Cache. Rechenfehler im ersten Test (Hühner mit 4 Beinen) – typisch für 7B.
- Die Datei nennt 131072 Token Kontext, DeepSeek gibt 32k an; mehr als 32k ist nicht getestet.
- Kein eigenes Sehen (keine mmproj) – Bilder über den Bildbeschreiber (Gemma-4-E2B): im Test
  hat es Tisch-Inhalt und Einkaufszettel („Milch, Brot, Eier“) aus der Beschreibung richtig
  wiedergegeben.

## Gemma-4-E2B-it als Bildbeschreiber (Q4_K_M + mmproj, `Vision/GME2BVision`)

Nur Hilfsmodell (Ausnahme von der 4B-Regel), kein Chat-Modell. Test mit vier Krea-Bildern
(Küche, Straße, Wohnzimmer, Plakat) und einem NemiCLI-Screenshot, 280 Bild-Token:

| Test | Ergebnis |
|---|---|
| Text lesen (Zettel, Straßenschild, Plakat, Fehlermeldung im Screenshot) | ✅ wörtlich, kleine Tippfehler |
| Fangfragen (Auto, Fernseher, Messer – nicht im Bild) | ✅ 5/5 „nicht zu erkennen“ |
| Tiere, Farben, Wetter, Lampe an | ✅ |
| Zählen ab 3 (3 Äpfel) | ❌ meist 2 |
| Links/rechts | ⚠️ manchmal vertauscht |

~2 s pro Beschreibung (Screenshot ~6 s), ~0,3 s pro Nachfrage, VRAM höchstens ~3 GB.
1120 statt 280 Bild-Token brachten keine Verbesserung.

_Test im Alltag folgt._
