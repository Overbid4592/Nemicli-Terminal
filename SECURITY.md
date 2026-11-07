# Sicherheitsrichtlinie / Security Policy

🇩🇪 Deutsch · 🇬🇧 [English below](#english)

## Unterstützte Versionen

NemiCLI befindet sich in der Alpha-Phase. Sicherheitskorrekturen werden grundsätzlich nur für die **jeweils neueste gepflegte Fassung** bereitgestellt.

Prüfe die [GitHub-Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) für die aktuell veröffentlichte Version. Der Entwicklungszweig `main` kann bereits einen neueren Stand enthalten. **Entwicklungsversion und veröffentlichtes Release sind nicht dasselbe.**

## Sicherheitslücke melden

Bitte **nicht** als öffentliches Issue, Diskussion oder Pull Request melden.

Melde Sicherheitslücken privat über GitHub:
**Reiter „Security“ → „Report a vulnerability“.**

Hilfreiche Angaben:

- NemiCLI-Version und Build (`/version` oder `--version`)
- Betriebssystem
- Art des Starts: Quelltext oder EXE
- Beschreibung der Sicherheitslücke
- Schritte zum Nachstellen
- Mögliche Auswirkungen (z. B. Code-Ausführung außerhalb der Sandbox oder Zugriff auf API-Schlüssel)

**Nicht mitschicken:** Passwörter, API-Schlüssel, Tokens, private IP-Adressen, persönliche Daten oder andere Geheimnisse. Bitte solche Angaben in Protokollen unkenntlich machen.

## Was danach passiert

- Die Meldung wird gelesen und geprüft.
- Bestätigte Sicherheitslücken werden nach Möglichkeit in einer neuen Version behoben und im `CHANGELOG.md` dokumentiert.
- Wer eine Lücke meldet, wird auf Wunsch erwähnt.

NemiCLI ist ein Ein-Personen-Projekt. Feste Antwort- oder Behebungszeiten gibt es daher nicht.

## Besonders relevant

- Ausbruch aus der Sandbox für KI-erzeugten Code
- Ausführung geschützter Befehle ohne notwendige Freigabe
- Auslesen oder Weitergeben gespeicherter API-Schlüssel
- Manipulierte Modelldateien (z. B. GGUF oder safetensors), die beim Laden unerwartet Code ausführen
- Prompt-Injection, die Freigaben oder Sperren umgeht

## Nicht im Rahmen

- Fehler in externen Diensten oder Modellen (z. B. Cloud-Anbieter)
- Falsche oder unpassende Modellausgaben ohne Sicherheitsfolge
- Angriffe, die bereits volle Kontrolle über den Rechner voraussetzen

---

<a id="english"></a>

## English

### Supported versions

NemiCLI is in the alpha stage. Security fixes are generally provided only for the **latest maintained version**.

Check [GitHub Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases) for the most recently published version. The `main` development branch may be ahead of the published release. **Development source and published releases are not the same version.**

### Reporting a vulnerability

Please **do not** disclose a security vulnerability in a public issue, discussion or pull request.

Instead, use GitHub's private vulnerability reporting:
**Security tab → "Report a vulnerability".**

Include as much of the following as is relevant:

- NemiCLI version and build (`/version` or `--version`)
- Operating system
- Whether you started from source code or an EXE
- Description of the vulnerability
- Steps to reproduce it
- Potential impact (for example, code execution outside the sandbox or access to API credentials)

**Do not include** passwords, API keys, tokens, private IP addresses, personal information or other secrets. Redact sensitive details in logs.

### What happens after a report

- The report is read and assessed.
- Confirmed vulnerabilities are addressed in a new version when possible and documented in `CHANGELOG.md`.
- Reporters may be credited if they wish.

NemiCLI is maintained by one developer. There are **no guaranteed response or remediation times**.

### Issues of particular interest

- Escape from the sandbox used to run AI-generated code
- Execution of protected commands without the required approval
- Reading or disclosing stored API keys
- Malicious model files (such as GGUF or safetensors) that unexpectedly execute code while being loaded
- Prompt injection that bypasses approvals or restrictions

### Out of scope

- Vulnerabilities in third-party services or models (such as cloud providers)
- Incorrect or unsuitable model responses without security impact
- Attacks that already require full control of the user's computer
