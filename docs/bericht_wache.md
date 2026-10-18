# 🛡 Bericht: Wache – Sicherheitsoptimierung

Stand 18.10.2026 · Ausgangszustand gesichert als Git-Tag `vor-wache-pruefung`.
Fehlerprotokoll und Plan im Detail: [Wache_Pruefung.md](Wache_Pruefung.md).

## 1. Ausgangszustand

Die Architektur (Sensoren → Motor → Isolation Forest + Folgen-Netz + Regeln → Speicher →
Wecker) war solide: Die Mathematik von GRU und Isolation Forest ist korrekt (Gradient numerisch
geprüft, Perzentil bei gleichen Werten richtig), Training und Erkennung rechnen gleich, und das
Training nahm alarmierte Ereignisse samt Prozess heraus. Schwach waren die **Ränder**: wie Urteile,
Wiederholungen und Profile ins Lernen und Dämpfen einfließen.

## 2. Bestätigte Fehler und Risiken

| Nr | Risiko | Kurz | Nachweis vorher |
|---|---|---|---|
| W1 | HOCH | Ein „harmlos“ machte einen Programm**namen** für das ML dauerhaft blind (Score × 0,35) | Score 1,00 → 0,35 |
| W2 | HOCH | Cooldown verschluckte schwerere Alarme und zählte an schon beurteilten weiter | ✅ simuliert |
| W3 | HOCH | Wiederholungen eines offenen Alarms (neue PID) und Schatten-Funde flossen ins Training; IF ohne Karenz | ✅ simuliert |
| W4 | HOCH | R009 (Massen-Dateiänderungen) nach 3 × harmlos global und für immer gedämpft | ✅ simuliert |
| W5 | MITTEL | Profile übernahmen auch alarmiertes Verhalten sofort | Code |
| W6 | MITTEL | Fehlgeschlagene Justierung meldete „Justiert“ | ✅ simuliert |
| W7 | MITTEL | `/wache trigger nie` für den Nutzer unmöglich, Nutzer an KI-Grenzen gebunden | ✅ simuliert |
| W8 | MITTEL | KI durfte sich über `trigger_ab` selbst seltener wecken | Code |
| W9 | NIEDRIG | Modellfehler still, Isolation Forest ohne Versionen, Folgen-Netz-Rückdrehen hielt nur bis zum nächsten Training | Code |
| W10 | NIEDRIG | Kinder eines alarmierten Prozesses wurden gelernt | Code |
| W11 | NIEDRIG | „Bekannt“ (am Namen) stellte auch Programme am fremden Ort leiser | Code |
| W12 | NIEDRIG | FolderSense: ganzer Ordner eingelesen, Verknüpfungen verfolgt, kaputte Beispieldatei überschrieben | Code |
| F | — | Vollscan-Grenze „höchstens 3 pro Woche“ fehlte im Code | Code |

## 3. Durchgeführte Änderungen

| Nr | Änderung | Dateien |
|---|---|---|
| W1 | Dämpfung am **Pfad** statt am Namen, ein „echt“ hebt sie auf, nie ab Score 0,995 und nie bei „im Training nie gesehen“ | `detektor.py`, `speicher.py` (`fehlalarm_pfade`) |
| W2 | Kein Verdichten, wenn der neue Alarm schwerer ist; `alarm_zaehlen` zählt nur an offenen/gesehenen Alarmen, sonst neuer Alarm | `motor.py`, `speicher.py` |
| W3 | Verdichtete Ereignisse merken ihren Alarm (`extra.alarm`), Schatten-Funde `extra.dl_fund` – beides bleibt aus dem Training; Isolation Forest lernt nur aus Ereignissen älter als `ml_karenz_h` (24 h, nur Nutzer) | `motor.py`, `speicher.py`, `justierung.py` |
| W4 | `NIE_GEDAEMPFT = {"R009"}`; Dämpfung zählt nur Urteile der letzten 30 Tage; KRITISCH und „schwerer als bisher harmlos“ melden immer | `regeln.py`, `speicher.py`, `motor.py` |
| W5 | Profil erst nach der Bewertung; nicht bei Alarm, nicht in belasteter Kette (auch Elternkette), nicht für Programme mit offenem Alarm; Inventar ebenso | `motor.py`, `speicher.py` |
| W6 | `speichern` gibt Erfolg zurück; `SpeicherFehler` statt Erfolgsmeldung, kein Tabelleneintrag; Protokollfehler ins Training-Log; `/wache an/aus/kugel/dl` melden Speicherfehler | `justierung.py`, `main_start.py` |
| W7 | `pruefen(..., nutzer=True)` mit technisch sinnvollen Grenzen (Schwelle 0,5–0,9999, Cooldown 0–86400 s, `nie` erlaubt) | `justierung.py`, `main_start.py` |
| W8 | KI darf `trigger_ab` nur auf mittel/hoch stellen; Doku angeglichen | `justierung.py`, `werkzeuge.py`, `core/persona.py` |
| W9 | Isolation Forest: 3 Vorversionen, `/wache ml zurueck`; nach jedem Zurückdrehen (ML und DL) Pause-Marke – automatisches Lernen erst nach einem Training durch den Nutzer; Lade-/Speicherfehler im Training-Log und in `wache_status` | `detektor.py`, `folgen.py`, `motor.py`, `werkzeuge.py`, `main_start.py`, `core/commands.py` |
| W10 | `lern_ereignisse` nimmt Ereignisse mit belasteter Elternkette heraus | `speicher.py` |
| W11 | Kein Leiserstellen, wenn das Programm aus Temp oder außerhalb installierter Software läuft | `motor.py` |
| W12 | `with os.scandir` + `islice(MAX_ENTRIES)`, `is_dir(follow_symlinks=False)`, atomares Schreiben, kaputte Beispieldatei → `.kaputt`, Einträge geprüft | `foldersense.py` |
| F | Höchstens 3 **neue** Vollscans in 7 Tagen (rollierend), Fortsetzen zählt nicht; Inventar und Sensoren sind keine Vollprüfung | `vollscan.py`, `main_start.py` |

Neue Abhängigkeiten: keine. Defender, Bitdefender, Registry, Smart App Control: nicht berührt.
Die Urteile über Alarme trifft weiter allein die Wache bzw. die Persönlichkeit.

## 4. Testergebnisse

| Prüfung | Ergebnis |
|---|---|
| Syntaxprüfung aller geänderten Dateien (`py_compile`) | ✅ bestanden |
| Wache-Tests (`test_wache*`, `test_luecken_fixes2`) – 142, davon 11 neu, 5 angepasst | ✅ bestanden |
| Neue Tests Vollscan-Grenze und FolderSense (`test_vollscan_foldersense`) | ✅ bestanden (1 übersprungen: Verknüpfungen brauchen Rechte) |
| Angrenzende Module (Hilfe, Menüs, Aktionen, Ablauf, Selbstdiagnose, Vollscan …) – 292 Tests | ✅ bestanden |
| Wache-Selbsttest (`/wache selbsttest`): 8/8 Angriffsmuster, 4/4 Angriffsketten, Fehlalarme 2,7 % / 5,0 % / 1,0 % | ✅ bestanden |
| Nachweis-Skript erneut: W1 (Score bleibt 1,00), W2 (HOCH-Alarm gemeldet), W4 (R009 nicht gedämpft), W6 (SpeicherFehler) | ✅ behoben |
| Simulierte Manipulation: Wiederholung mit neuer PID, Kind, Schatten-Fund → nicht im Training, nach „harmlos“ wieder lernbar | ✅ bestanden |
| Cooldown/Dämpfung/Eskalation: schwerer, kritisch, 30-Tage-Fenster, beurteilt | ✅ bestanden |
| Modellversionen und Rollback inkl. Pause und Neuladen aus anderem Prozess | ✅ bestanden |
| Volle Testreihe | ⏳ offen – nur nach Ankündigung (NemiCLI vorher schließen) |

## 5. Offene Probleme

- Ein Verhalten, das **länger als 24 h** unter der Alarmschwelle bleibt, wird weiterhin gelernt –
  das ist das Wesen eines lernenden Normalbilds. Gegenmittel bleiben Regeln, Vollscan und der
  Virenschutz.
- „Bekannt“ ist weiter am Namen geführt (bei Skript-Hosts mit Elternprozess); W11 fängt nur den
  fremden Ort ab.
- Schatten-Funde werden nie gelernt, auch harmlose. Wird das Folgen-Netz scharf geschaltet,
  meldet es sie einmal als DL-001 – danach lernt es sie über das „harmlos“-Urteil.
- Die volle Testreihe und ein Lauf auf echten Daten stehen noch aus.

## 6. Mögliche Regressionen

| Änderung | Spürbar als |
|---|---|
| Karenz 24 h für den Isolation Forest | Neuinstallation: erstes Training erst nach einem Tag (Kaltstart solange); neue Programme bleiben bis zu 24 h „ungewohnt“ |
| Pfad-Dämpfung, keine Dämpfung von Ausreißern | etwas mehr ML-Alarme bei Programmen, die schon einmal harmlos waren |
| Nicht an beurteilte Alarme zählen | läuft ein harmlos beurteilter Vorgang weiter, entsteht einmal ein neuer Alarm |
| R009 nie gedämpft | häufige Backups/Entpacken melden wieder (verdichtet über den Cooldown) |
| Profile frieren bei offenem Alarm ein | Prozess bleibt „neu“, bis geurteilt ist |
| KI nur mittel/hoch | Persönlichkeit kann sich nicht mehr auf „kritisch“ stellen |
| Vollscan-Grenze | der 4. neue Vollscan in 7 Tagen wird abgelehnt |

## 7. Risikoscore

Skala: 10,0 = hohes Risiko · 5,0 = mittel · 1,0 = bester definierter Prüfwert.

- **Vorher: 6,4** – vier bestätigte Wege, auf denen ein einmal als harmlos beurteiltes oder sich
  wiederholendes Verhalten dauerhaft aus der Erkennung fallen konnte.
- **Nachher: 3,4** – alle bestätigten Fehler behoben und getestet. Rest: langsames Lernen über
  24 h hinaus, Namensbezug bei „bekannt“, ausstehende volle Testreihe.

Der Score ist eine nachvollziehbare Einschätzung, kein Sicherheitszertifikat. Die Wache ergänzt
Microsoft Defender und Bitdefender und ersetzt sie nicht.
