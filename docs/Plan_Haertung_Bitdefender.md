# Plan: Härtung und Vertrauen gegenüber Bitdefender

Stand: 25.09.2026 · festgelegt im Gespräch, am selben Tag umgesetzt (siehe „Umsetzung“ unten).

## Anlass

Am 25.09.2026 gegen 05 Uhr hat Bitdefender (Erweiterte Gefahrenabwehr) NemiCLI blockiert und fast alles
in Quarantäne gesetzt. Erkennung: `SuspiciousBehavior.8296190040D3F832`.

Kette laut Bitdefender:

```
NemiCLI.exe (Fenster-App, ohne Konsole)
  → NemiCLIc.exe (unsichtbar in der Pseudokonsole)   ← als „Malware“ markiert
    → conhost.exe
      → powershell.exe + conhost.exe  (mehrfach)
        → nvidia-smi.exe
```

Muster „unsichtbarer Starter → verstecktes Programm → wiederholt PowerShell“ – für die
Verhaltensüberwachung ein typisches Schädlingsmuster. Vorher, sichtbar im Windows Terminal, kein Alarm.

## Entscheidungen

1. **Klare Trennung nach Rechten**
   - **NemiCLI Standard** – alles ohne Adminrechte (Chat, Gedächtnis, Bilder, eigenes Fenster, eigene Dateien).
     Verhärtet: keine PowerShell im Normalbetrieb, keine versteckten Prozessketten, kein Schreiben in den
     Autostart, nur feste, geprüfte Werkzeuge.
   - **Keine Adminrechte für die KI** – die KI von NemiCLI kommt mit Admin-Sachen nie in Berührung.
     `befehl` weist jede Rechte-Erhöhung ab (RunAs, runas, sudo …); läuft NemiCLI selbst mit
     Adminrechten, führt die KI keine Befehle aus. Alles andere darf sie. Keine eigene Admin-exe.

2. **SHA-256-Prüfsummen** – jeder Build schreibt die Prüfsummen seiner exe-Dateien in eine Datei.

3. **Code-Signatur (Authenticode)** – die exe wird mit dem Zertifikat des Herausgebers signiert.
   Zuerst kostenlos mit einem selbst erstellten Zertifikat (gilt nur auf diesem PC), später ein echtes
   (z. B. Microsoft Trusted Signing), wenn NemiCLI an andere geht.

4. **Einreichen bei Bitdefender** – ein fertiger, signierter Build geht mit seiner SHA-256 über das
   Falsch-Positiv-Formular an Bitdefender.

## Technische Hebel für die Verhärtung

- `abfragen` ohne PowerShell: feste Python-Werkzeuge (psutil, winreg, Ereignisprotokoll, Signaturprüfung).
- `nvidia-smi` durch die direkte NVIDIA-Schnittstelle (NVML) ersetzen.
- Keine unsichtbare Kette ohne Grund; Autostart nur einmal eintragen, nicht umschreiben.
- Offene Sicherheitsbefunde (A1, C1, M1, K1) mitlösen.

## Arbeitsregeln

- Bei Bitdefender-Themen erst reden, dann programmieren.
- Projektstand nur lokal in Git festhalten – nichts auf GitHub.

## Umsetzung (25.09.2026)

- [x] `abfragen` ohne PowerShell – feste Python-Abfragen (`tools/systemabfrage.py`).
- [x] Zwischenablage (user32), Zeitplan (`schtasks` + XML), GPU-Name (Registry) ohne PowerShell.
- [x] Keine Adminrechte für die KI; statt einer Admin-exe: `befehl` sperrt Rechte-Erhöhung.
- [x] SHA-256-Prüfsummen je Build (`SHA256SUMS.txt`).
- [x] Code-Signatur mit selbst erstelltem Zertifikat (`zertifikat.py`, `CN=NemiCLI`, gültig bis 2031).
- [x] Sandbox (AppContainer) für Code der KI; Python/pip/pytest nur dort.
- [x] Schutzsoftware ist für die KI tabu; `befehl` sperrt typische Systemwerkzeuge.
- [ ] Signierten Build beim Bitdefender-Falsch-Positiv-Formular einreichen.
- [ ] Später: Zertifikat einer anerkannten Stelle, wenn NemiCLI an andere geht.

`nvidia-smi` bleibt: es war nur das letzte Glied unter PowerShell, nicht der Auslöser.
