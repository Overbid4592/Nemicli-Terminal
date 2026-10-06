# Sicherheitsrichtlinie / Security Policy

🇩🇪 Deutsch · 🇬🇧 [English below](#english)

## Unterstützte Versionen

NemiCLI ist in der Alpha-Phase. Sicherheitskorrekturen gibt es nur für die
jeweils neueste Version.

| Version         | Unterstützt |
|-----------------|-------------|
| 6.6 Alpha (neu) | ✅          |
| ältere          | ❌          |

## Sicherheitslücke melden

Bitte **nicht** als öffentliches Issue, Diskussion oder Pull Request melden.

Stattdessen privat über GitHub:
**Reiter „Security“ → „Report a vulnerability“.**

Hilfreiche Angaben:

- NemiCLI-Version und Build (`/version` oder `--version`)
- Betriebssystem
- Art des Starts: Quelltext oder exe
- Beschreibung der Lücke
- Schritte zum Nachstellen
- Mögliche Folgen (z. B. Code-Ausführung außerhalb der Sandbox, Zugriff auf Schlüssel)

**Nicht mitschicken:** Passwörter, API-Schlüssel, Tokens, private IP-Adressen,
persönliche Daten oder andere Geheimnisse. Schlüssel in Protokollen bitte unkenntlich machen.

## Was danach passiert

- Die Meldung wird gelesen und geprüft.
- Bestätigte Lücken werden in einer neuen Version behoben und im `CHANGELOG.md` genannt.
- Wer meldet, wird auf Wunsch erwähnt.

NemiCLI ist ein Ein-Personen-Projekt. Feste Antwortzeiten gibt es deshalb nicht.

## Besonders relevant

- Ausbruch aus der Sandbox, in der KI-erzeugter Code läuft
- Ausführen von Befehlen ohne Freigabe
- Auslesen oder Weitergeben gespeicherter API-Schlüssel
- Schadhafte Modelldateien (GGUF, safetensors), die beim Laden Code ausführen
- Prompt-Injection, die Freigaben oder Sperren umgeht

## Nicht im Rahmen

- Fehler in externen Diensten oder Modellen (z. B. Cloud-Anbieter)
- Falsche oder unpassende Antworten eines Modells ohne Sicherheitsfolge
- Angriffe, die bereits volle Kontrolle über den Rechner voraussetzen

---

<a id="english"></a>

## English

**Supported versions:** Only the latest release (currently 6.6 Alpha) receives security fixes.

**Reporting:** Please do **not** open a public issue. Use GitHub's private reporting instead:
**Security tab → "Report a vulnerability".**

Please include: NemiCLI version and build, operating system, source or exe,
description, steps to reproduce and possible impact.

**Do not include** passwords, API keys, tokens, private IP addresses, personal data
or other secrets.

Confirmed issues are fixed in a new release and listed in `CHANGELOG.md`.
NemiCLI is maintained by a single developer, so there are no guaranteed response times.
