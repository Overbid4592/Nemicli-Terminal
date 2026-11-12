# Test- und Sicherheitsprüfung – 12.11.2026 (Version 9.66 Alpha)

Vollständige Prüfung des Quelltexts vor dem Upload auf GitHub: alle Tests, statische
Sicherheitsanalyse, bekannte Lücken in den Paketen, eingecheckte Geheimnisse, Durchsicht der
Netz-Schnittstellen. Gefundenes wurde behoben und mit Tests abgesichert.

Die Setup-exe ist nicht Teil dieser Prüfung; sie wird getrennt geprüft. Die öffentliche Setup-exe
9.66.985 (SHA-256 `e524efa3bc6f9505cd61eaebc9d0ad9ef277cacd5cc1d09c50942ab38d5f01c3`) ist signiert
und startet mit `--selftest` fehlerfrei; ihr eigener Code enthält keine persönlichen Daten.

## Testumgebung

| | |
|---|---|
| System | Windows 11 Pro, Version 10.0.26300 (Build 26300), 64 Bit |
| Prozessor | Intel Core i5-11600K |
| Arbeitsspeicher | 64 GB |
| Grafikkarte | NVIDIA GeForce RTX 5060 Ti, 16 GB, Treiber 621.14 |
| Python | 3.14.8 |
| torch | 2.14.1+cu132 (CUDA 13.2) |
| Wichtige Pakete | PySide6 6.12.0, Pillow 13.0.0, pypdf 6.21.4 |
| Prüfwerkzeuge | bandit 1.9.4, pip-audit 2.10.1 (in einer eigenen venv, nicht im Programm) |

## Geprüfte Stände

| Stand | Was | Wozu |
|---|---|---|
| Entwicklung | Quelltext mit allen Tests | Fehler finden und beheben |
| GitHub-Stand | GitHub-Repository + alle Upload-Teile + Tests | genau das, was nach dem Upload im Repository liegt |

Der GitHub-Stand wurde dafür als eigener Ordner zusammengesetzt und dort getestet.

## 1. Tests

Aufruf (im Programmordner, mit der venv von NemiCLI):

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\venv\Scripts\python.exe -m unittest test_release_hardening test_repository_model_parts
```

`PYTHONIOENCODING=utf-8` ist nötig, weil die Windows-Konsole sonst an Umlauten und Emojis abbricht.

| Lauf | Tests | Ergebnis |
|---|---|---|
| Entwicklung, 1. Lauf | 3.461 | 3.459 grün, 2 rot (Kugel-Animation, siehe unten), 9 übersprungen |
| Entwicklung, 2. Lauf | 3.461 | **alle grün**, 9 übersprungen |
| GitHub-Stand, Tests aus `tests/` | 3.386 | **alle grün**, 32 übersprungen |
| GitHub-Stand, Tests im Hauptordner | 7 | **alle grün** |
| Entwicklung, vor dem Bau 9.66.985 | 3.462 | **alle grün**, 9 übersprungen |
| GitHub-Stand 9.66.985, Tests aus `tests/` | 3.387 | **alle grün**, 32 übersprungen |
| GitHub-Stand 9.66.985, Tests im Hauptordner | 7 | **alle grün** |

Die Läufe zu 9.66.985 enthalten die 13. Sicherheitsregel (klickbare Links nur https), daher je ein Test mehr.

**Die zwei roten Tests im 1. Lauf** (`test_kugel_leben`, `test_kugel_lebendig`) warten auf eine
Bewegung der Kugel über Qt-Zeitgeber. Unter voller Last der Testreihe reichte die Wartezeit einmal
nicht. Einzeln, in der Gruppe und im 2. Lauf sind sie grün. Kein Fehler im Programm.

Im GitHub-Stand fehlen 75 Tests der Entwicklung: die für den Einrichtungs-Helfer (auf GitHub liegt
eine ältere Fassung, die eigene Tests im Hauptordner hat) und für Teile, die nicht veröffentlicht sind.

**Übersprungen** werden Tests, für die auf dem Rechner etwas fehlt. Im GitHub-Stand:

| Anzahl | Grund |
|---|---|
| 21 | TensorRT-RTX nicht eingerichtet (16 allgemein, 5 nur für Blackwell-Karten) |
| 5 | langsame Tests mit echten Modellen, nur mit `NEMICLI_SLOW_TESTS=1` |
| 1 | kein lokaler Tokenizer für Krea 2 |
| 5 | entscheiden erst während des Laufs, ob etwas fehlt (Modelldatei, Programm oder Hardware) |

**Meldungen im Protokoll:** Während der Testreihe erscheinen Ausgaben wie
`Internal C++ object (_Kugelfenster) already deleted`. Sie entstehen, wenn ein Test das Kugel-Fenster
schließt, während dessen Zeitgeber noch einmal feuert. Sie gab es schon in früheren Läufen; kein Test
schlägt dadurch fehl.

### Neue Tests

| Datei | Tests | Prüft |
|---|---|---|
| `tests/test_sicherheit_statisch.py` | 13 | statische Sicherheitsregeln (Abschnitt 4) |
| `tests/test_netzadresse.py` | 5 | http nur zum eigenen Rechner (Abschnitt 3) |
| `tests/test_nemiview.py` | +1 | `calc()` ohne `eval` |

## 2. Statische Analyse mit bandit

```powershell
bandit -r <Ordner> -f json -o bandit.json
```

Am GitHub-Stand: 314 Dateien, rund 115.000 Zeilen.

| | Vorher | Nachher |
|---|---|---|
| hoch | 5 | **0** |
| mittel | 40 | 39 |
| niedrig | 604 | 604 |

Alle hohen und mittleren Hinweise wurden einzeln im Quelltext angesehen:

| Regel | Anzahl | Befund |
|---|---|---|
| B324 md5/sha1 | 5 | sha1 bildet nur Kennungen und erkennt doppelte Einträge. Jetzt mit `usedforsecurity=False` markiert; die Werte bleiben gleich. |
| B307 eval | 1 | In NemiView rechnete `eval` `calc()` aus dem CSS einer Webseite aus, eingezäunt auf Zahlen und `+ - * / ( )`. Ersetzt durch einen eigenen kleinen Rechner. |
| B608 SQL | 24 | Kein Fund. Werte gehen überall als `?`-Platzhalter in die Abfrage; eingesetzt werden nur feste Texte, Spaltennamen aus festen Listen und Zahlen (`int()`). |
| B314 XML | 6 | Windows-Ereignisprotokolle (vom eigenen System), Office-Anhänge und der arXiv-Feed. Pythons XML-Leser lädt keine externen Entitäten. Für Anhänge und den Feed werden Dokumente mit DTD jetzt zusätzlich abgelehnt. |
| B102 exec | 1 | `main.py` lädt seine drei eigenen Teildateien in denselben Namensraum. Kein fremder Code. |
| B615 Hugging Face | 2 | Lädt nur von der Platte (`local_files_only`, offline) und nur `safetensors`. |
| B104, B108, B610 | 6 | Fehlalarme: Vergleich mit `"0.0.0.0"` (keine Bindung), Wort `\temp` als Merkmal der Wache, `.extra` ist kein Django. |

Die niedrigen Hinweise sind überwiegend `try/except/pass` (396), `random` für Abwechslung, nicht für
Schlüssel (63), und `subprocess` ohne Shell (38). Geprüft wurde gezielt: Nirgends steht `shell=True`,
und Schlüssel entstehen überall mit `secrets`.

## 3. Netz-Schnittstellen (Durchsicht von Hand)

| Teil | Bindung | Zugriffsschutz |
|---|---|---|
| Chrome-Brücke | 127.0.0.1 | Host-Prüfung, Schlüssel (zeitkonstant verglichen), CORS nur für die eigene Erweiterung (feste ID) |
| Web-Oberfläche, Spiele | 127.0.0.1 | Zufallsschlüssel bei jeder Anfrage (zeitkonstant verglichen) |
| ChatGPT-Anmeldung | 127.0.0.1 | frische `state`- und `nonce`-Werte aus `secrets`, PKCE (S256) |
| llama.cpp-Reserve | 127.0.0.1 | Schlüssel aus `secrets` |
| GUI und Terminal-Fenster | Named Pipe | jede Verbindung muss sich ausweisen |

**Behoben:** Eine eingetragene Adresse für Ollama, ComfyUI oder SD-WebUI auf einem anderen Rechner
wurde unverschlüsselt (http) angesprochen. Jetzt gilt (`core/netzadresse.py`): http nur zu
127.0.0.1, `::1` und localhost, jede andere Adresse über https.

**Chrome-Erweiterung (2.3.7):** Antworten werden erst maskiert, dann formatiert, so landet kein fremdes
HTML in der Seitenleiste. Nachrichten nimmt sie nur von sich selbst an (kein `externally_connectable`,
kein `onMessageExternal`). Bevor Schlüssel oder Seiteninhalt hinausgehen, beweist NemiCLI mit HMAC-SHA256
über eine Zufallszahl, dass wirklich NemiCLI auf dem Port antwortet. Der Schlüssel liegt in
`chrome.storage.local` und wird nicht über das Google-Konto abgeglichen. Die Rechte (`<all_urls>`)
braucht sie, um auf Wunsch die offene Seite zu lesen.

**Behoben:** Die Erweiterung und die Web-Oberfläche machten in Antworten auch `http://`-Links klickbar.
Jetzt nur noch `https://`.

**Begründete Ausnahme:** Beim Signieren der exe (`build_exe.py`) bleibt der Zeitstempel-Server
`http://timestamp.digicert.com`. `Set-AuthenticodeSignature` in Windows PowerShell nimmt nur
http-Adressen an (getestet: https wird abgelehnt). Übertragen wird nur ein Hash, und die Antwort
(RFC 3161) ist selbst signiert. Das betrifft nur den Bau der exe, nicht das Programm.

## 4. Eigene Sicherheitsprüfung (`tests/test_sicherheit_statisch.py`)

Läuft ab jetzt in jeder Testreihe mit und prüft den ganzen Quelltext:

| Regel | Verboten |
|---|---|
| eval / exec | jedes `eval`; `exec` nur an der einen bekannten Stelle in `main.py` |
| Shell | `shell=True` |
| Unsicheres Laden | `pickle`, `marshal`, `yaml.load`, `shelve`, `dill`; `torch.load` ohne `weights_only=True` |
| TLS | `verify=False`, `CERT_NONE`, `_create_unverified_context` |
| Hash | md5/sha1 ohne `usedforsecurity=False` |
| SQL | Werte per `%` oder `.format()` im SQL |
| http | `http://` zu einem anderen Rechner (XML-Namensräume und die Ausnahme oben ausgenommen) |
| Links | klickbare `http://`-Links in der Chrome-Erweiterung und der Web-Oberfläche |
| Server | Bindung an `0.0.0.0` oder `::` |
| CORS | `Access-Control-Allow-Origin` mit dem unveränderten `Origin` der Anfrage |
| Regex | Zeichenbereiche mit echten Sonderzeichen statt Escapes |
| Geheimnisse | private Schlüssel, API-Schlüssel (OpenAI/Anthropic, GitHub, Hugging Face, AWS, Google, Slack) |

**Gegenprobe:** In einem leeren Ordner mit absichtlich schlechtem Beispielcode schlägt jede der ersten
12 Regeln an; die Link-Regel schlägt am alten Stand der Erweiterung und der Web-Oberfläche an. Im
Quelltext von NemiCLI schlägt keine an.

**Regex:** Fünf Muster mit Sonderzeichen stehen jetzt als Raw-String mit Escapes. Über alle
1.112.064 Unicode-Zeichen treffen sie genau dasselbe wie vorher.

## 5. Pakete (pip-audit)

```powershell
pip-audit --path .\venv\Lib\site-packages
```

97 Pakete geprüft, 75 verschiedene bekannte Lücken in 5 Paketen.

| Paket | Vorher | Bekannte Lücken | Nachher |
|---|---|---|---|
| Pillow | 12.3.0 | 40 | 13.0.0 |
| pypdf | 6.20.0 | 16 | 6.21.4 |
| filelock | 4.1.1 | 17 | 4.2.10 |
| certifi | 2026.7.22 | 1 | 2026.10.23 |
| setuptools | 84.0.0 | 1 | 84.2.1 |
| rembg | 2.0.85 | – | 2.0.87 (erst diese Fassung verträgt Pillow 13) |

Danach: **keine bekannte Lücke**, `pip check` ohne Konflikt. torch und torchvision kann pip-audit
nicht prüfen, weil die CUDA-Bauten nicht auf PyPI liegen.

In `requirements.txt` stehen jetzt `pillow>=13.0.0`, `pypdf>=6.21.4` und `rembg[cpu]>=2.0.87`.
pypdf bleibt bewusst bei 6.x; 7.0 ist eine neue Hauptversion und wurde nicht geprüft.
NemiCLI selbst prüft die Pakete der Nutzer mit `/update` gegen dieselbe Datenbank (OSV.dev).

## 6. Weitere Funde

| Fund | Behoben |
|---|---|
| `test_kurzfassung` fiel auf einem frischen Rechner durch: Er erwartete Werkzeuge, die nur mit einem Cloud-Modell im Prompt stehen. | Der Test legt das Cloud-Modell selbst fest. |
| `test_release_hardening` (nur auf GitHub) erwartete einen älteren Wortlaut der Fehlermeldung. Der Schutz selbst wirkt: Ein Paket mit `chats/` wird abgelehnt, die Daten bleiben. | Der Test prüft den aktuellen Wortlaut. |

## 7. Datenschutz der Upload-Fassung

Vor dem Upload wurde jede Datei auf Namen, Benutzerpfade, E-Mail-Adressen, echte IP-Adressen und
Gesprächsreste durchsucht. Testdaten nutzen neutrale Beispielnamen; echte IP-Adressen sind durch
erfundene ersetzt. Nicht im Upload: Tests für Teile, die nicht auf GitHub liegen (Einrichtungs-Helfer
in neuer Fassung, Handy-Export).
