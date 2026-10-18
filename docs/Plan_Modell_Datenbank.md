# Bauplan: Modell-Datenbank und `ausfüll.py`

*Idee des Autors, 13. Oktober 2026 · Bauplan von Claude · Stand: Phasen 1–4 gebaut (13.10.), Phase 5 offen*

## Die Idee in einem Satz

Statt für jede Modellfamilie eine eigene Datei (`qwen35.py`, `gemma4.py` …) gibt es **eine Steine-Kiste** (die Rechen-Bausteine als Code), **eine Datenbank** (welche Bauart aus welchen Steinen besteht, wie sie redet, welche Hardware was kann) und **`ausfüll.py`**, das beim Laden alles zusammensteckt: Modell erkannt → Einträge aus der DB → Steine verbunden → passende Einstellungen für diesen PC → geladen.

Bild dazu: **LEGO**. Die Steine sind Code, die Bauanleitungen stehen in der Datenbank. Ein neues Modell aus bekannten Steinen braucht nur eine neue Anleitung, keinen neuen Code.

---

## 1. Was es heute schon gibt

| Baustein heute | Wo | Was er schon kann |
|---|---|---|
| **Baukasten** | `ggufengine/models/baukasten.py` | 26 Bauarten (Llama, Mistral, Qwen 2/3, Gemma 1–3, Phi, Granite, OLMo, gpt-oss, GLM …) aus **einer** Datei, gesteuert über kleine Tabellen (`_PAARE`, `_GELU`, `_FENSTER_MUSTER` …) – das ist schon die halbe Idee |
| Eigene Modellklassen | `qwen35.py`, `gemma4.py`, `hybrid.py`, `deepseek2.py`, `bailing3.py`, `llama4.py`, `qwen3vl.py` | Bauarten mit besonderen Steinen (DeltaNet, Mamba, MLA, Per-Layer-Einbettung, MTP …) |
| Format-Erkennung | `ggufengine/chat.py` `detect_format` | Chat-Format aus dem Jinja-Template (nur gelesen, nie ausgeführt) + Bauart |
| System-Erkenner | `engines/systemprofil.py` | CPU (AVX2/AVX-512, Kerne), RAM, Grafikkarten (VRAM, Generation sm_xx, Treiber), CUDA-Version |
| Erkenner fürs Verteilen | `engines/gguflokal.py` `verteilen` | 16/8 Bit, Hybrid, Experten-Lager je nach freiem Grafikspeicher |

**Entdeckt beim Planen:** Lokale Modelle rufen Werkzeuge heute **alle über dasselbe Textformat** auf (ein Block ` ```aktion ` mit JSON `{"tool": …}`), nicht in ihrer **antrainierten** Sprache (Qwen: `<tool_call>…</tool_call>`, Mistral: `[TOOL_CALLS]…`, Gemma 4: eigene Marken). Modelle, die stark auf ihr eigenes Format trainiert sind, tun sich damit schwer – sehr wahrscheinlich ein Grund, warum Ministral-3-14B keine Werkzeuge benutzt hat.

---

## 2. Die Datenbank – vier Tabellen

Reine **Daten (JSON)**, nie Code. Liegt im Programm-Ordner, kommt mit der exe, wird beim Laden auf Plausibilität geprüft.

### 2.1 `bauarten` – die Bauanleitungen
| Feld | Beispiel (Qwen 3.5) |
|---|---|
| `arch` (aus dem GGUF-Kopf) | `qwen35` |
| Schicht-Muster | 3 × DeltaNet + 1 × Aufmerksamkeit, wiederholt |
| Steine je Schicht | `deltanet`, `aufmerksamkeit_gated`, `ffn_gated` bzw. `moe` |
| Aktivierung, Norm | SiLU · RMSNorm (+1) |
| RoPE | mrope [11, 11, 10], teilweise (64 von 256) |
| Besonderes | MTP-Kopf (`nextn_predict_layers`), Bild-Eingang |
| Prefill-Block | 2048 |

### 2.2 `formate` – wie ein Modell redet
| Feld | Beispiel (Mistral v7 Tekken) |
|---|---|
| Erkennungsmerkmale im Template | `[SYSTEM_PROMPT]`, `"[INST]" +` |
| Rollen-Marken | `[INST]`, `[/INST]`, `[SYSTEM_PROMPT]` |
| Denk-Marken | `[THINK]`, `[/THINK]` |
| **Werkzeuge nativ** | Aufruf `[TOOL_CALLS]name[ARGS]{…}`, Antwort `[TOOL_RESULTS]…` |
| Stop-Token | `</s>` |

### 2.3 `modelle` – Erfahrung je Modellfamilie
| Feld | Beispiel |
|---|---|
| Familie / Release | Qwen3.8-27B (unsloth) |
| Empfohlene Werte | Denken: temp 1.0, top_p 0.95, top_k 20 · ohne Denken: temp 0.7, top_p 0.8 |
| Kontext-Grenze | 262.144 |
| Erfahrung (aus unseren Tests) | Werkzeuge: gut · MTP: 71 % (deutsch) · Hybrid 25k: ~4–6 Token/s |

### 2.4 `hardware` – was ein PC kann
| Feld | Beispiel |
|---|---|
| GPU-Generation | sm_120 = Blackwell · FP4/FP8-Tensorkerne · bf16 |
| Mindest-Treiber / CUDA | CUDA 13.2 für torch cu132 |
| PCIe | 3.0 x8 ≈ 7 GB/s · 4.0 x8 ≈ 14 · 5.0 x8 ≈ 28 |
| CPU | AVX2/AVX-512 (für spätere CPU-Pfade) |

---

## 3. `ausfüll.py` – der Ablauf beim Laden

1. **GGUF-Kopf lesen** (wie heute): Bauart, Schichten, Tensoren, Template
2. **Bauart suchen** in `bauarten` → Steine zusammenstecken (Prüfung: jeder Tensor, den die Anleitung erwartet, ist da – sonst klare Meldung)
3. **Format suchen** in `formate` → Chat, Denken, **Werkzeuge in der Muttersprache des Modells**
4. **Modell-Eintrag** suchen → empfohlene Werte, Erfahrungen
5. **Hardware** aus dem System-Erkenner → `verteilen` (Grafikkarte, 8 Bit, Hybrid, Lager)
6. Unbekannt? → **ehrliche Meldung**: „Bauart X kennt NemiCLI noch nicht – Stein Y fehlt“, statt Absturz

---

## 4. Die Steine-Kiste

Was es heute schon als Code gibt (bleibt Code, wird nur besser sortiert):
Aufmerksamkeit (voll, Fenster, Sinks, Softcap, MLA), RoPE-Arten (NeoX, Paare, mrope, YaRN, LongRoPE), FFN (gated, ungated, MoE mit geteiltem Experten, Experten-Lager), DeltaNet, Mamba 2, KDA, Kurzfaltung (LFM2), MTP-Kopf, Bild-Encoder, Stimme.

**Ehrlich:** Erfindet ein Hersteller einen ganz neuen Rechenweg (wie die Hyper-Verbindungen von `qwen4_exp`), muss genau dieser **eine Stein** einmal gebaut werden. Die DB beschreibt, sie rechnet nicht.

---

## 5. Phasen

| # | Schritt | Gewinn | Risiko |
|---|---|---|---|
| 1 | Tabellen aus `baukasten.py` in `bauarten.json` | Grundgerüst, kein neues Verhalten | gering – Vergleichstests gegen transformers müssen gleich bleiben |
| 2 | `detect_format` + Formate nach `formate.json` | neue Formate ohne Code | gering |
| 3 | **Native Werkzeug-Formate** (Qwen, Mistral, Gemma 4 zuerst) | Werkzeuge in der Muttersprache → vermutlich deutlich zuverlässiger | mittel – vorher/nachher messen (Werkzeug-Treffer je Modell) |
| 4 | `hardware.json` + Erkenner nutzt sie | bessere Entscheidungen je PC | gering |
| 5 | Eigene Modellklassen schrittweise in Steine + Anleitung überführen | weniger Dateien, neue Modelle schneller | hoch – nur Stück für Stück, jede Bauart mit identischen Logits |

Phase 3 ist der größte Nutzen für den Alltag und kann auch zuerst kommen.

**Stand 13.10.:** Phasen 1–4 gebaut, jede mit Vorher/Nachher-Vergleich (Ergebnis gleich).

| Datei in `ggufengine/daten/` | Inhalt | gelesen von |
|---|---|---|
| `werkzeugformate.json` | Werkzeug-Sprachen (Qwen-XML, Granite, Gemma 4, GLM, Mistral v7) | `engines/werkzeugsprache.py` |
| `chatformate.json` | Format-Erkennung aus dem Template + Rollen-Marken | `ggufengine/chat.py` |
| `bauarten.json` | 41 Bauarten: welche Steine-Datei, MoE ja/nein, Bauanleitung des Baukastens | `models/__init__.py`, `baukasten.py`, `tools/hfladen.py` |
| `hardware.json` | NVIDIA-Generationen, torch-Bau je Rechen-Stufe, nötige Treiber-CUDA | `engines/systemprofil.py`, `engines/syscheck.py` |

Geprüft beim Lesen in `ggufengine/ausfuellen.py`. `modelle` (2.3) ist noch nicht angelegt.

## 6. Sicherheit

- Die DB ist **nur Daten**: JSON, nie `eval`, nie Pickle
- Jinja-Templates werden weiterhin **nie ausgeführt**, nur nach Merkmalen durchsucht
- Jede Zahl aus der DB wird geprüft wie heute die aus der GGUF-Datei
- Die DB kommt mit der exe, kein Nachladen aus dem Internet
