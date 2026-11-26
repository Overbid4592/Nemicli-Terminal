# Sicherheitsrichtlinie / Security Policy

🇩🇪 Deutsch · 🇬🇧 [English below](#english)

## Sicherheitslücke melden

Bitte **nicht** öffentlich als Issue, Diskussion oder Pull Request, sondern privat über GitHub:
**Reiter „Security“ → „Report a vulnerability“.**

Hilfreich sind Version und Build (`/version`), Start aus Quellcode oder exe, Beschreibung, Schritte zum Nachstellen
und mögliche Auswirkungen. Keine Passwörter, API-Schlüssel, Tokens oder persönlichen Daten mitschicken.

Sicherheitskorrekturen gibt es für die neueste Fassung ([Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases)).
Bestätigte Lücken werden in einer neuen Version behoben und im `CHANGELOG.md` genannt; wer meldet, wird auf Wunsch
erwähnt. NemiCLI ist ein Ein-Personen-Projekt, feste Antwortzeiten gibt es nicht.

**Besonders relevant:** Ausbruch aus der Sandbox · geschützte Befehle ohne Freigabe · Zugriff auf gespeicherte
API-Schlüssel · Modelldateien, die beim Laden Code ausführen · Prompt-Injection, die Freigaben oder Sperren umgeht.

**Nicht im Rahmen:** Fehler externer Dienste oder Modelle · Modellausgaben ohne Sicherheitsfolge · Angriffe, die
bereits volle Kontrolle über den Rechner voraussetzen.

---

<a id="english"></a>

## English

Please **do not** report vulnerabilities in public issues, discussions or pull requests. Use GitHub's private
reporting: **Security tab → "Report a vulnerability".**

Include version and build (`/version`), source or exe, a description, steps to reproduce and the potential impact.
Do not include passwords, API keys, tokens or personal data.

Fixes are provided for the latest version ([Releases](https://github.com/Overbid4592/Nemicli-Terminal/releases)).
Confirmed issues are fixed in a new version and listed in `CHANGELOG.md`; reporters are credited on request.
NemiCLI is maintained by one developer, so there are no guaranteed response times.

**Of particular interest:** sandbox escape · protected commands without approval · access to stored API keys ·
model files that execute code while loading · prompt injection that bypasses approvals or restrictions.

**Out of scope:** issues in third-party services or models · model output without security impact · attacks that
already require full control of the computer.
