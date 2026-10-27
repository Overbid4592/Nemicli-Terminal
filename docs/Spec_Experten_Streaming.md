# Spec: Experten-Streaming im eigenen GGUF-Motor

Stand: 30.09.2026 · Idee von Hoffmann Vibecoder · Grundgerüst mit Claude · **Umsetzung mit Claude Code**

## Der Gedanke
Ein großes MoE-Modell wird **nicht komplett geladen, sondern gestreamt** – nach dem Prinzip
von Spiel-Engines (Borderlands 4 / Unreal Engine 5: World Partition, Mipmaps, Texture-Pool).
Im VRAM liegt nur ein **fester Kern** plus ein **Cache der gerade gebrauchten Experten**.
Alles andere wird bei Bedarf aus RAM oder NVMe nachgeladen.

Nicht kopieren, sondern **übersetzen**: Die Spiel-Begriffe bekommen eine KI-Bedeutung,
der Code wird selbst geschrieben.

## Begriffe (Spiel → KI)

| Spiel | Bedeutung im Motor |
|---|---|
| Welt | komplettes Modell auf der NVMe (GGUF, read-only per mmap) |
| Zelle / Zone | ein Experte in einer Schicht (`schicht`, `experte`) |
| Spielerposition | aktueller Token + Router-Entscheidung |
| Sichtweite | Experten, die die nächsten Schichten wahrscheinlich brauchen |
| Mipmap | derselbe Experte in mehreren Stufen (grob / scharf) |
| Texture-Pool | fester VRAM-Cache mit festem Budget |
| Nachladen | NVMe → RAM → VRAM |
| Pop-in | kurz mit grober Stufe rechnen, bis die scharfe da ist |
| Shader-Kompilierung | Kernels / CUDA Graphs beim Start vorbereiten |

## Drei Ebenen

| Ebene | Inhalt | Tempo (RTX 5060 Ti / 990 EVO Plus) |
|---|---|---|
| 🟢 VRAM | fester Kern (Attention, Router, Norms, Embeddings, Shared Experts) + heiße Experten | ~448 GB/s |
| 🟡 RAM | warme Experten | ~50 GB/s (DDR4-3200, Dual-Channel) |
| 🔴 NVMe | alle Experten, per mmap | ~7 GB/s |

Faustformel: **Zeit pro Token ≈ Rechenzeit + Σ (verpasste Bytes ÷ Bandbreite der Ebene)**.
Alles hängt an der **Trefferquote** im VRAM.

---

## Phase 0 – Erst messen, dann bauen

Ziel: wissen, ob sich Streaming lohnt, **ohne** eine Zeile Streaming-Code.

- Testmodell: **Mixtral 8x7B** (Q4_K_M, ~26 GB – passt komplett in 64 GB RAM).
- Der Router jeder Schicht wird mitgeloggt: pro Token `(token_nr, schicht, [experten])`.
- Ausgabe als Datei im Debug-Log-Ordner (nur Daten, keine Prompts nötig).
- Offline-Auswertung (eigenes Skript):
  - Häufigkeit je Experte und Schicht
  - Wie oft wechselt die Auswahl von Token zu Token?
  - **Simulierte Trefferquote** für Cache-Größen 3 / 5 / 8 / 10 GB
  - Vergleich der Rauswurf-Regeln: LRU (zuletzt benutzt) vs. LFU (am häufigsten) vs. Mischung
- Verschiedene Gesprächsarten testen: Chat, Code, NemiCLI-Anleitung mit Werkzeugen.

**Ergebnis von Phase 0:** eine Tabelle Cache-Größe → Trefferquote → geschätzte Token/s.

## Phase 1 – Cache mit festem Budget

- VRAM-Budget fest einstellbar (z. B. `stream_vram_gb = 5`), die VRAM-Automatik rechnet mit.
- Fester Kern wird beim Laden in den VRAM gelegt und bleibt dort.
- Experten-Cache als Slots fester Größe (alle Experten einer Schicht sind gleich groß →
  keine Fragmentierung, Slots werden nur überschrieben).
- Ablauf pro Schicht:
  1. Router wählt Experten
  2. im VRAM? → rechnen
  3. sonst aus RAM kopieren (gepinnter Speicher, eigener CUDA-Stream, `non_blocking`)
  4. sonst aus mmap lesen → RAM → VRAM
  5. Rauswurf nach der Regel, die in Phase 0 gewonnen hat
- RAM-Ebene ebenfalls mit Budget.

## Phase 2 – Vorladen (Sichtweite)

- Während Schicht N rechnet, im Hintergrund die wahrscheinlichen Experten für Schicht N+1
  holen.
- Erste Schätzung: Router von N+1 schon mit dem Zwischenergebnis von N befragen und prüfen,
  wie oft das stimmt (Messung wie in Phase 0).
- Trifft die Schätzung nicht: normal nachladen, nichts geht kaputt – nur langsamer.

## Phase 3 – Experten-Mipmaps

- Jeder Experte existiert in zwei Stufen:
  - **grob** (z. B. 2–3 Bit): **alle** Experten, dauerhaft im VRAM/RAM
  - **scharf** (Q4/Q6 aus der GGUF): nur die heißen im Cache
- Fehlt die scharfe Stufe, wird **sofort mit der groben** gerechnet („Pop-in“),
  die scharfe lädt im Hintergrund nach. Das Modell wartet nie auf die NVMe.
- Voraussetzung: der Motor braucht eine grobe Stufe (eigene Umrechnung beim Laden oder
  Q2_K/Q3_K-Unterstützung).
- **Qualität messen** wie im Motor üblich: Top-5-Tokens und KL-Divergenz gegen die
  Referenz ohne Streaming. Grenze vorher festlegen.

---

## Regeln für die Umsetzung

- Reines Python + torch, **keine neuen Abhängigkeiten**. Kein npm, kein Node.js, kein Docker.
- Die GGUF-Datei bleibt **read-only gemappt**. Jeder Offset und jede Größe eines Experten wird
  – wie im gehärteten Leser – vor dem Lesen gegen die Dateilänge und die Limits geprüft.
- Kein Hintergrund-Thread darf ungeprüfte Zahlen aus der Datei benutzen.
- Streaming ist **abschaltbar** (`stream = aus` → Verhalten wie heute).
- Jede Phase bekommt eigene Tests (Testsuite, nicht im Repository): Cache-Treffer, Rauswurf,
  Budget wird nie überschritten, Ergebnis identisch zur Variante ohne Streaming (Phase 1–2).
- Messwerte kommen ins Debug-Log und in `Modelltests.md`.

## Messgrößen

| Messgröße | Wofür |
|---|---|
| Trefferquote VRAM / RAM | Kern der ganzen Idee |
| Token/s (Decode) | Ziel realistisch: spürbar schneller als reines RAM-Offloading |
| Nachgeladene GB pro Token | zeigt, wo die Zeit hingeht |
| Top-5 / KL gegen Referenz | nur Phase 3 (Mipmaps) |
| VRAM-Spitze | Budget eingehalten? |

## Offene Fragen

- Lohnt sich Streaming schon bei Mixtral, oder erst bei Modellen, die nicht in den RAM passen?
- Wie gut lässt sich die nächste Schicht vorhersagen?
- Welche grobe Stufe ist noch brauchbar (2 Bit? 3 Bit?)
- Später: echte DeepSeek-MoE-Architektur (z. B. DeepSeek-V2-Lite, braucht MLA-Attention im Motor).

## Nächster Schritt
**Phase 0** – Router-Log einbauen, mit Mixtral ein paar Gespräche führen, Trefferquote simulieren.
