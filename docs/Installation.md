# Installation

[Handbuch](Handbuch.md) · **Installation** · [Erste Schritte](Erste-Schritte.md) · [Modelle](Modelle.md) ·
[Funktionen](Funktionen.md) · [Befehle](Befehle.md)

## Voraussetzungen

| | Minimum | Empfohlen |
|---|---|---|
| Betriebssystem | Windows 11, 64 Bit (Windows 10 ungetestet) | Windows 11, 64 Bit |
| Grafikkarte (lokale Modelle, Bilder, Video) | NVIDIA, 8 GB VRAM | NVIDIA, 16 GB VRAM oder mehr |
| Arbeitsspeicher | 32 GB | 64 GB |
| Rechte | normales Benutzerkonto | normales Benutzerkonto |

Nur mit Cloud-Modellen genügt deutlich weniger. 8 GB VRAM sind eine Untergrenze, keine Garantie: Quantisierung,
Kontextlänge und gleichzeitig geladene Motoren zählen mit. Krea 2 belegt beim Malen 20–27 GB RAM, mit 16 GB RAM
gehen weder Bilder noch Video.

## Setup-Datei (empfohlen)

1. `NemiCLI-Setup-<Build>.exe` und `NemiCLI-Setup-<Build>.exe.sha256` von der
   [Release-Seite](https://github.com/Overbid4592/Nemicli-Terminal/releases) laden.
2. Prüfsumme vergleichen – beide Werte müssen gleich sein:

   ```powershell
   Get-FileHash .\NemiCLI-Setup-<Build>.exe -Algorithm SHA256
   Get-Content .\NemiCLI-Setup-<Build>.exe.sha256
   ```

3. Setup starten. Es installiert ohne Administratorrechte nach `C:\Users\<Benutzer>\.nemicli`. Die Datei ist mit
   einem eigenen, nicht öffentlich anerkannten Zertifikat (`CN=NemiCLI`) signiert; SmartScreen oder ein Virenschutz
   können deshalb nachfragen.
4. NemiCLI starten und den Ordner für die Daten wählen (später änderbar mit `/start`).
5. `/einrichten` eingeben: holt ein isoliertes Python, legt die Arbeitsumgebung an, zeigt den passenden
   PyTorch-Befehl und bietet optional Modelle an (Hugging Face, mit SHA-256-Prüfung).
6. Mit `/model` ein Sprachmodell wählen.

**Aktualisieren:** eine neuere Setup-Datei ausführen. Sie ersetzt nur das Programm, Daten und Einstellungen bleiben.

## Aus dem Quellcode

Voraussetzung: Python 3.14.

```powershell
git clone https://github.com/Overbid4592/Nemicli-Terminal.git
cd Nemicli-Terminal
.\start.bat
```

`start.bat` legt das `venv` an und installiert `requirements.txt`. Von Hand:

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python main.py
```

PyTorch wird getrennt installiert: `/venv` öffnet ein Fenster mit dem passenden Befehl für die Grafikkarte.
`/update` aktualisiert den Quellcode (`git pull --ff-only`) und prüft die Pakete auf bekannte Lücken.

**Tests:**

```powershell
$env:PYTHONIOENCODING = "utf-8"
venv\Scripts\python -m unittest discover -s tests -p "test_*.py"
```

**Eigene exe:** `venv\Scripts\python build_exe.py` erzeugt `dist/NemiCLI/` und `dist/NemiCLI-Setup-<Build>.exe`
mit `.sha256`. Mit einem Zertifikat `CN=NemiCLI` (einmalig `python zertifikat.py`) wird signiert.

## Optional

- [Ollama](https://ollama.com/download), LM Studio, Jan, ein llama.cpp-Server oder vLLM – erscheinen von selbst
  unter `/model`
- Chrome für die [Erweiterung](../chrome-erweiterung/LIESMICH.md)
- NVIDIA TensorRT for RTX (eigene Lizenz, selbst laden) – siehe [Modelle](Modelle.md#tensorrt-rtx)
