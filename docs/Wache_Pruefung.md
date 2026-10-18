# 🛡 Wache – Prüfung (Phase 1–3)

Stand: 18.10.2026, Code-Stand `Version 8.8: CHANGELOG Build 8.8.623`. Nur gelesen und mit
simulierten Daten nachgerechnet – **am Code ist nichts geändert**.

Geprüft: `tools/wache/` `detektor.py`, `folgen.py`, `gru.py`, `justierung.py`, `merkmale.py`,
`motor.py`, `wald.py`, dazu `tools/foldersense.py` und die Stellen, an denen sie hängen
(`speicher.py`, `regeln.py`, `main_start.py` /wache, `core/config.py`). Eine `foldersense(2).py`
gibt es nicht.

---

## Phase 1 – Bestandsaufnahme

### Was schon gut schützt

| Schutz | Wo |
|---|---|
| Training ohne Ereignisse mit Alarm `offen`, `gesehen`, `echt`, `erledigt` – samt Prozess (PID + Startzeit) | `speicher.lern_ereignisse` |
| Folgen-Netz lernt nur aus Ereignissen älter als 24 h | `motor.folgen_trainieren` (`dl_karenz_h`) |
| Folgen-Netz im Schattenmodus, scharf nur durch den Nutzer | `justierung` `dl_alarm`, `/wache dl` |
| Folgen-Netz: Versionen (letzte 3) und Zurückdrehen | `folgen.py` |
| Muster-Regeln (R001 …) werden nie gedämpft | `regeln.MENGENREGELN` |
| Skript-Hosts gelten nur mit Elternprozess als „bekannt“ | `speicher.bekannt_schluessel` |
| `pruefen_ab` setzt nur der Nutzer | `justierung.pruefen_ab_setzen` |
| Jede Justierung mit Begründung im Protokoll | `justierung.anwenden` |
| Fehler in der Hauptschleife → „gestört“ statt stillem Tod | `motor._fehler` |
| Config mit `.bak`, beschädigte Datei überschreibt nichts | `core/config.py` |
| Vollscan nur auf Befehl des Nutzers, mit Rückfrage | `main_start._vollscan_befehl` |

### Rechnung und Mathematik – geprüft, in Ordnung

| Prüfung | Ergebnis |
|---|---|
| GRU-Gradient gegen numerische Ableitung | größte relative Abweichung 2,3·10⁻⁵ → **korrekt** |
| GRU numerisch stabil | Sigmoid geklemmt, Softmax mit Max-Abzug, log mit 1e-12, Gradienten-Kappung bei Norm 5 → **ok** |
| Perzentil bei gleichen Rohwerten (Isolation Forest) | Gruppe mit 80 % → Score 0,0 · Gruppe mit 20 % → 0,79 → **korrekt** (`side="right"`) |
| Perzentil Folgen-Netz | `side="left"` auf Überraschung = gleiche Bedeutung wie oben → **konsistent** |
| Training ↔ Erkennung | Merkmale sind reine Funktionen, `burst` wird beim Aufnehmen gestempelt, Folgen-Netz nutzt in beiden Fällen dasselbe `Kettenbuch` → **konsistent** |
| Unbekannte Prozessfolgen | seltene Namen → Sammelzeichen `S:~`, unbekannte Art → `?` → **kein Absturz, kein Automatik-Alarm** |
| Isolation Forest | Pfadlänge, Korrektur c(n), Normierung wie Liu/Ting/Zhou → **ok** |

Nebenbefund: `kontamination` wird gespeichert, aber nirgends benutzt (keine Wirkung).

### Systemprüfungen (Auftrag F)

| Teil | Wann | Wer startet |
|---|---|---|
| Sensoren (Prozesse, Netz, Dateien) | dauernd, alle 3–5 s | Wache |
| Inventar (Autostart, Dienste, Aufgaben, Software, Prozesse) | beim Start + alle 6 h | Wache |
| Vollscan (alle Laufwerke, Hashes, Systemstand) | nur auf `/wache fullscan` + Rückfrage | **nur der Nutzer** |

Die drei Teile sind sauber getrennt. Keine Hintergrund-Aufgabe startet einen Vollscan, die KI hat
kein Werkzeug dafür. **Eine Grenze „höchstens 3 pro Woche“ steht nirgends im Code** und ist auch
nicht als Regel festgehalten → siehe offene Frage unten.

---

## Phase 2 – Fehlerprotokoll

Risiko: HOCH · MITTEL · NIEDRIG. „Nachgewiesen“ = mit simulierten Daten nachgerechnet.

### W1 · HOCH · Ein „harmlos“ macht einen Programmnamen für das ML blind
- **Datei/Funktion:** `detektor.py` `bewerten`, `feedback` · `speicher.py` `fehlalarm_prozesse`
- **Ursache:** Ein einziges „harmlos“ zu irgendeinem Alarm eines Prozessnamens setzt den Namen auf
  die Dämpfliste. Danach wird jeder Score dieses Namens × 0,35 gerechnet – für immer, nur nach
  **Name** (nicht Pfad), und ein späteres „echt“ hebt es nicht auf.
- **Auswirkung:** Höchster möglicher Score 0,35, kleinste erlaubte Schwelle 0,90 → das ML kann für
  diesen Namen nie mehr warnen. Ein Schadprogramm `svchost.exe` im Temp-Ordner wäre unsichtbar,
  sobald einmal ein echtes svchost als harmlos beurteilt wurde. Der Kommentar „echte Ausreißer
  kommen durch“ stimmt nicht.
- **Nachgewiesen:** Score 1,000 → 0,350, Alarm ja → nein.

### W2 · HOCH · Cooldown verschluckt Eskalationen
- **Datei/Funktion:** `motor.py` `_verdichten` · `speicher.py` `alarm_zaehlen`
- **Ursache:** Gleiche Regel + Subjekt im Cooldown wird nur am ersten Alarm gezählt – auch wenn der
  neue Alarm **schwerer** ist, und auch wenn der erste schon als **harmlos** abgeschlossen ist. Das
  Fenster gleitet: solange es weitergeht, bleibt es EIN Alarm.
- **Auswirkung:** ML-Alarm MITTEL, danach HOCH (Score ≥ 0,995) → kein neuer Alarm, kein Weckruf.
  Nach „harmlos“ zählt der geschlossene Alarm still weiter.
- **Nachgewiesen:** neuer HOCH-Alarm gemeldet: nein · erster Alarm: Mittel, harmlos, ×2.

### W3 · HOCH · Verdächtiges gelangt ins Training (schleichende Manipulation)
- **Datei/Funktion:** `speicher.py` `lern_ereignisse` · `motor.py` `_verdichten`, `trainieren`
- **Ursache:** (a) Wiederholungen, die nur den Zähler erhöhen, hängen an keinem Alarm. Läuft dasselbe
  mit neuer PID wieder (Neustart), schützt auch die Prozess-Kette nicht. (b) Der Isolation Forest
  lernt alle 200 Ereignisse und nimmt dabei auch Ereignisse von vor wenigen Minuten – anders als
  das Folgen-Netz (24 h Karenz). (c) Ereignisse mit ML-Anomalie oder Schatten-Fund ohne eigenen Alarm
  werden ebenfalls gelernt.
- **Auswirkung:** Ein Ablauf, der offen gemeldet ist, wird über seine Wiederholungen trotzdem zum
  Normalbild – nach dem nächsten Training fällt er nicht mehr auf.
- **Nachgewiesen:** Ereignis mit offenem Alarm → nicht im Training · Wiederholung (andere PID) → **im Training**.

### W4 · HOCH · Dämpfen schaltet R009 (Massen-Dateiänderungen) global ab
- **Datei/Funktion:** `speicher.py` `gedaempft` · `regeln.py` R009, `MENGENREGELN`
- **Ursache:** R009 hat immer das Subjekt „überwachte Ordner“. Nach 3 × harmlos (und nie echt) ist
  R009 für **alle** Ordner gedämpft. Dämpfung läuft nie ab und kennt keine Schwere-Grenze; auch
  ML-001 und DL-001 gehören dazu.
- **Auswirkung:** Das Erkennungsmerkmal für Verschlüsselungs-Trojaner meldet nach drei harmlosen
  Backups/Entpack-Vorgängen nie wieder.
- **Nachgewiesen:** `gedaempft` enthält `('R009', 'überwachte ordner')`.

### W5 · MITTEL · Normalprofile übernehmen auch Verdächtiges sofort
- **Datei/Funktion:** `motor.py` `_anreichern`, `inventar_scannen` · `speicher.py` `profil_fortschreiben`
- **Ursache:** Jedes Ereignis schreibt das Profil fort (Pfad, Eltern, Kinder, Ports, Uhrzeiten), bevor
  feststeht, ob es einen Alarm gibt. Das Inventar trägt alle laufenden Prozesse ein. Nach dem nächsten
  Spiegeln (≤ 5 min) gilt der Prozess als bekannt. Die Spalte `vertraut` existiert, wird aber nicht genutzt.
- **Auswirkung:** Der erste Auftritt meldet. Ab dem zweiten fehlen „Prozess neu“, „Pfad neu“,
  „Elternprozess neu“, „Kind neu“, „Ziel neu“ – das ML wird leiser, obwohl der Alarm noch offen ist.
- **Belegt:** aus dem Code (Reihenfolge `_anreichern` → `bewerten`).

### W6 · MITTEL · Fehlgeschlagene Justierung wird als Erfolg gemeldet
- **Datei/Funktion:** `justierung.py` `speichern`, `anwenden`, `pruefen_ab_setzen`, `letzte_rueckgaengig`
- **Ursache:** `speichern` verschluckt jede Ausnahme und beachtet nicht, dass `config.save` `False`
  zurückgibt. Danach wird trotzdem in `justierungen` und ins Protokoll geschrieben.
- **Auswirkung:** „Justiert: schwelle 0.97 → 0.95“ + Eintrag im Protokoll – gespeichert ist nichts.
  Rückgängig hätte denselben Fehler. Fehler beim Protokollieren bleiben ebenfalls still.
- **Nachgewiesen:** simulierter Schreibfehler → Antwort „Justiert …“.

### W7 · MITTEL · Rechte unterscheiden nicht zwischen Nutzer und KI
- **Datei/Funktion:** `justierung.py` `pruefen`
- **Ursache:** `pruefen` kennt kein `wer`. Der Nutzer läuft über dieselben Grenzen wie die KI.
- **Auswirkung:** `/wache trigger nie` ist unmöglich – die Meldung sagt „darf nur der Nutzer setzen
  (/wache trigger nie)“, genau das schlägt aber fehl. Schwelle und Cooldown kann auch der Nutzer
  nur innerhalb der KI-Grenzen setzen (Docstring: „Der Nutzer darf alles ändern“).
- **Nachgewiesen:** `pruefen("trigger_ab", "nie")` → abgelehnt.

### W8 · MITTEL · `trigger_ab` – Widerspruch in den Rechten der KI
- **Datei/Funktion:** `justierung.py` (Docstring: drei Dinge) ↔ `pruefen`, `werkzeuge.py`, `core/persona.py` (vier Dinge)
- **Ursache:** Die KI darf `trigger_ab` auf „kritisch“ stellen. Damit weckt sie sich selbst seltener –
  dieselbe Lücke, die bei `pruefen_ab` bewusst geschlossen wurde. Für `trigger_ab` gibt es keine Grenze.
- **Auswirkung:** begrenzt – der Zähl-Trigger (ab 5 ungeprüften Alarmen) und ML-Alarme wecken weiter.

### W9 · NIEDRIG · Modelle: Fehler still, Isolation Forest ohne Versionen
- **Datei/Funktion:** `detektor.py` `speichern`, `laden` · `folgen.py` `zurueckdrehen`
- **Ursache:** Speichern und Laden schlucken jeden Fehler ohne Protokoll; passt ein Modell nicht,
  fällt der Detektor still in den Kaltstart. Der Isolation Forest überschreibt sein Modell ohne
  Vorversion. Beim Folgen-Netz hält ein Zurückdrehen nur bis zum nächsten automatischen Training (≤ 6 h).

### W10 · NIEDRIG · Kinder eines alarmierten Prozesses werden gelernt
- **Datei/Funktion:** `speicher.py` `lern_ereignisse`
- **Ursache:** Herausgenommen wird nur der Prozess selbst, nicht die Prozesse, die er startet.
- **Auswirkung:** Das Folgen-Netz lernt „verdächtiger Prozess startet X“ als normalen Kettenanfang.

### W11 · NIEDRIG · „Bekannt“ macht Mengen-Alarme leiser – nur nach Name
- **Datei/Funktion:** `motor.py` `_verdichten` · `speicher.py` `bekannt_schluessel`
- **Ursache:** Ist ein Prozessname als bekannt eingetragen, kommt der Alarm eine Stufe leiser – auch
  wenn dasselbe Programm diesmal aus dem Temp-Ordner läuft.

### W12 · NIEDRIG · FolderSense
- **Datei/Funktion:** `foldersense.py` `features`, `learn_folder`, `_save_json`
- **Ursache/Auswirkung:**
  - `list(os.scandir(...))` liest den ganzen Ordner, `MAX_ENTRIES` greift erst danach (gemessen: 60.000 Einträge = 52 ms lokal; auf Netzlaufwerken mehr).
  - `is_dir()` folgt Verknüpfungen.
  - Ist `folder_examples.json` beschädigt, überschreibt das nächste `learn_folder` alle gelernten Beispiele.
  - Schreiben ist nicht atomar.
  - Einträge ohne `label` lassen `stats`/`_rebuild` abstürzen.
- Kein Sicherheitsbauteil der Wache – Ordner-Erkennung für die KI.

---

## Phase 3 – Verbesserungsplan

Alle Änderungen klein und an Ort und Stelle. Ausgangszustand vorher als Git-Tag sichern.
Neue Abhängigkeiten: keine.

| Nr | Empfohlene Lösung | Alternative | Nebenwirkung |
|---|---|---|---|
| W1 | Dämpfung an **Pfad** binden (exe, klein geschrieben) statt Name; ein „echt“ hebt sie auf; Scores ab 0,995 und „nie gesehen“-Achsen werden **nicht** gedämpft | Dämpfung ganz entfernen (ML-001 hat schon die Subjekt-Dämpfung ab 3 × harmlos) | Etwas mehr ML-Alarme bei Programmen, die schon mal harmlos waren, aber nur bei echten Ausreißern |
| W2 | Nicht verdichten, wenn der neue Alarm **schwerer** ist; nicht an abgeschlossene Alarme zählen (`alarm_zaehlen` nur bei `offen`/`gesehen`, sonst neuer Alarm) | nur die Schwere-Regel | Nach einem „harmlos“ kann derselbe Vorgang einmal neu auftauchen; danach verdichtet er wieder |
| W3 | (a) Verdichtete Wiederholungen merken sich ihren Alarm (`extra.alarm`), `lern_ereignisse` nimmt sie mit heraus. (b) Isolation Forest mit Karenz wie das Folgen-Netz (`ml_karenz_h`, Standard 24 h, nur Nutzer). (c) Ereignisse mit `anomalie` oder Schatten-Fund ≥ Schwelle nicht lernen, solange es dazu kein „harmlos“ gibt | Karenz kürzer, z. B. 6 h | Neue Programme bleiben bis zu 24 h „ungewohnt“ → am ersten Tag nach einer Installation etwas mehr Meldungen |
| W4 | R009 nie dämpfen (nur verdichten); Dämpfung zählt nur Urteile der letzten 30 Tage; KRITISCH wird nie gedämpft; schwerer als alles bisher harmlos Beurteilte → meldet | R009-Subjekt je Ordner | Seltene Fälle melden nach 30 Tagen wieder |
| W5 | Profil erst **nach** der Bewertung fortschreiben und nicht für Ereignisse mit Alarm oder aus einer Kette mit offenem Alarm; Inventar überspringt solche Prozesse ebenso | Profileinträge erst nach 24 h als „vertraut“ zählen (Spalte `vertraut`) | Ein offen gemeldeter Prozess bleibt „neu“, bis er beurteilt ist – gewollt |
| W6 | `speichern` gibt Erfolg zurück; bei Fehler: Ausnahme mit Grund, **kein** Eintrag in `justierungen`, Antwort „Nicht gespeichert: …“; Protokollfehler im Training-Log vermerken; gilt für alle vier Wege (+ `/wache an|aus|kugel|dl`) | – | keine |
| W7 | `pruefen(..., wer)`: Nutzer darf alle gültigen Werte (Schwelle 0,5–0,9999, Cooldown 0–86400, `nie` erlaubt, keine Stumm-Obergrenze); KI weiter in den Grenzen | Nutzer weiter an Grenzen, nur `nie` freigeben | keine |
| W8 | KI darf `trigger_ab` nur **strenger** stellen (mittel/hoch), nicht auf kritisch/nie; Docstring + `persona.py` angleichen | `trigger_ab` ganz aus den KI-Rechten nehmen (wie `pruefen_ab`) · alles lassen und nur den Docstring korrigieren | KI kann sich nicht mehr selbst seltener wecken lassen |
| W9 | Speicher-/Ladefehler ins Training-Log und in `stand()["fehler"]`; Isolation Forest behält 3 Vorversionen + `/wache ml zurueck`; nach einem Zurückdrehen lernt das jeweilige Modell erst nach dem nächsten Training durch den Nutzer wieder automatisch | nur Fehler sichtbar machen, Versionen später | Etwas Plattenplatz für 2 × 3 Modell-Kopien |
| W10 | `lern_ereignisse` nimmt auch Ereignisse heraus, deren Elternprozess (PPID + Elternstart) belastet ist | – | keine |
| W11 | Nicht leiser stellen, wenn der Pfad außerhalb bekannter Installationsorte oder im Temp liegt | lassen | keine |
| W12 | `with os.scandir()` + `islice(MAX_ENTRIES)`, `is_dir(follow_symlinks=False)`, atomares Schreiben, beschädigte Beispieldatei vor dem Überschreiben als `.kaputt` sichern, Einträge prüfen | lassen (kein Sicherheitsbauteil) | keine |

Tests (Phase 5) je Punkt: simulierte harmlose und verdächtige Ereignisse wie in den
Nachweis-Skripten, als Unit-Tests; danach die bestehenden Wache-Tests und auf Zuruf
die volle Reihe.

### Offene Frage an den Nutzer
„Höchstens 3 Systemprüfungen pro Woche“ ist weder im Code noch als Regel festgehalten. Gemeint
ist vermutlich der Vollscan – den startet ohnehin nur der Nutzer. Soll eine Sperre/Warnung ab dem
4. Start in 7 Tagen eingebaut werden, oder zählt auch das Inventar (heute alle 6 h) dazu?

### Risiko-Einschätzung (Skala 10 = hoch · 1 = bester Prüfwert)
- **Ausgangszustand: 6,4** – vier bestätigte Lücken (W1–W4), mit denen ein einmal als harmlos
  beurteiltes oder sich wiederholendes Verhalten dauerhaft aus der Erkennung fallen kann.
- **Nach W1–W8 (Erwartung): 3,6** – Rest: neue Software braucht Zeit, Erkennung bleibt
  Ergänzung zu Defender und Bitdefender.
