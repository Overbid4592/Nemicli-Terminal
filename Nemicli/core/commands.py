"""
commands.py - Slash-Befehle (/theme, /model, ...) inkl. Autovervollständigung.

Alle Befehle beginnen mit "/".  Beim Tippen von "/" erscheint ein Menü,
bei "/m" bleibt nur noch "/model" usw. Argumente (Theme-Namen, Modelle)
werden ebenfalls vorgeschlagen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from prompt_toolkit.completion import Completer, Completion


# ---------------------------------------------------------------------------
# Die Befehlsliste - EINZIGE Quelle
# ---------------------------------------------------------------------------
# Früher gab es sie zweimal: hier (fürs Vorschlags-Menü) und nochmal als
# hartcodierte Tabelle in ui.help_panel(). Die zwei sind auseinandergedriftet –
# /webui und /gedaechtnis fehlten in der Hilfe, /help und /bild fehlen dort bis
# heute. Jetzt steht alles hier, die Hilfe baut sich daraus (siehe help_rows()).


@dataclass(frozen=True)
class Cmd:
    name: str            # "/model"
    short: str           # kurze Fassung fürs Vorschlags-Menü beim Tippen
    arg: str = ""        # Argument-Hinweis für die Hilfe, z.B. "[name]"
    long: str = ""       # ausführlichere Fassung für /help (leer = short)

    @property
    def usage(self) -> str:
        return f"{self.name} {self.arg}".strip()

    @property
    def help_text(self) -> str:
        return self.long or self.short


COMMAND_LIST: list[Cmd] = [
    Cmd("/help", "Alle Befehle anzeigen"),
    Cmd("/model", "KI-Modell wechseln (Cloud oder Lokal)", "[name]",
        "KI-Modell wechseln (Cloud oder Lokal) – ohne Name: Auswahl-Menü"),
    Cmd("/staerke", "Denk-Stärke oder Denkbudget wählen", "[stufe]",
        "Zeigt die passenden Stufen für dein Modell. Bei Kimi steuern sie das "
        "gemeinsame Tokenbudget für Denken und Antwort."),
    Cmd("/theme", "Farbschema wechseln", "[name]"),
    Cmd("/modus", "Arbeitsmodus: chat · lesen · normal · auto (Shift+Tab)", "[name]",
        "💬 Chatten (alles fragt, Merken frei) · 👁 Nur Lesen (Ändern gesperrt) · "
        "⚙ Normal (Ändern fragt) · ⚡ Auto (ohne Rückfragen im Ordner, 5-min-Zünder). "
        "Shift+Tab schaltet durch."),
    Cmd("/persönlichkeiten", "Persönlichkeit wählen oder eigene anlegen 🎭", "[name · neu]",
        "🎭 Wer spricht? Ohne Name: Menü (eingebaute Nemi + deine eigenen). "
        "'/persönlichkeiten neu' legt per Fragen eine neue an – als Markdown-Datei "
        "in Persoenlichkeiten/, die du frei umschreiben kannst. Gilt sofort, bleibt gespeichert."),
    Cmd("/bild", "Bild erzeugen 🎨 (eigene Stable-Diffusion-Pipeline)", "<beschreibung>"),
    Cmd("/bildmodel", "Bild-Modell wählen 🎨 (eigene Pipeline oder externe WebUI)", "[name]",
        "🖥 eigene Checkpoints (SD 1.5 / SDXL) ODER 🌐 Modelle einer laufenden "
        "Forge/A1111-WebUI (Krea/Qwen/Flux …). Ohne Name: Menü. Die Wahl bleibt "
        "gespeichert; '/bildmodel webui-adresse' ändert den WebUI-Host."),
    Cmd("/bearbeiten", "Bild nachbessern 🖼 – Bereich markieren & neu malen", "[pfad]",
        "Öffnet ein Fenster: Rahmen um Gesicht/Augen ziehen, neu malen lassen, "
        "speichern. Ohne Pfad wird das zuletzt gemalte Bild genommen."),
    Cmd("/resume", "Früheren Chat fortsetzen", "[#]",
        "Früheren Chat fortsetzen – ohne #: Liste"),
    Cmd("/wissen", "Zeigt, was NemiCLI gelernt hat"),
    Cmd("/gedaechtnis",
        "Langzeitgedächtnis ansehen/löschen (was sie sich über dich merkt)"),
    Cmd("/reflektieren", "Über das Gespräch nachdenken & Lehren merken 🪞"),
    Cmd("/aufraeumen", "Gedächtnis verdichten: doppelte Notizen verschmelzen 🗜"),
    Cmd("/start", "📁 Wo dein NemiCLI-Ordner liegt (Chats, Bilder, Modelle)", "",
        "📁 Öffnet das Fenster, in dem du deinen NemiCLI-Ordner wählst – den Ort "
        "für Chats, Bilder, Modelle und alles Gelernte. Der ist absichtlich vom "
        "Programm-Ordner getrennt: so darf NemiCLI umziehen oder neu gebaut "
        "werden, ohne dass deine Daten das mitmachen. Es wird nichts gelöscht – "
        "beim Wechsel wird kopiert, das Original bleibt liegen."),
    Cmd("/einrichten", "Einrichtungs-Assistent 🧰: installiert alles Fehlende", "",
        "🧰 Führt dich durch die Einrichtung: prüft, was auf dem PC fehlt, und "
        "installiert es auf Wunsch selbst (Python, torch, Bild-Bausteine). "
        "Nur die Modelle suchst du dir selbst aus"),
    Cmd("/selbsttest", "Selbsttest 🔧: Module, Ordner, Einrichtung prüfen", "",
        "🔧 Prüft ohne Neustart, ob alle Module laden, die Ordner da sind und die "
        "Einrichtung stimmt – dasselbe wie `NemiCLI.exe --selftest`, nur im Chat."),
    Cmd("/statistik", "📊 Was du mit NemiCLI machst (lokal, alle Sitzungen)", "[reset]",
        "📊 Sammelt lokal, wie du NemiCLI nutzt: Befehle, Werkzeuge, Modelle, "
        "Persönlichkeiten, Modi, Tokens, Kosten, Bilder, aktivste Zeiten. Keine "
        "Chat-Inhalte. '/statistik reset' setzt zurück."),
    Cmd("/version", "Version, Build, Python, Modell anzeigen"),
    Cmd("/protokoll", "Aktions-Protokoll 📜: was wann lief, mit Befehl", "[anzahl]",
        "📜 Jede Aktion steht dauerhaft in learned/aktionen.log – Zeit, ändert/liest, "
        "Werkzeug, ausgeführt/abgelehnt/gesperrt, wer (auch Helfer), Beschreibung. "
        "Auch lesende Aktionen, denn die fragen nicht nach. Ohne Zahl: die letzten 30."),
    Cmd("/update", "NemiCLI per Git aktualisieren ⬇", "",
        "⬇ git pull (nur Fast-Forward) und bei Bedarf Abhängigkeiten nachziehen – "
        "fragt vorher. Geht nur im Skript-Modus mit Git-Repo + Remote."),
    Cmd("/workspace", "Projektordner festnageln 📌 – nur noch dort arbeiten", "[pfad · status]",
        "📌 Nagelt einen Ordner fest: ab dann arbeitet die Persönlichkeit NUR dort "
        "(lesen, schreiben, suchen, Befehle) – gegen das Abdriften in zwanzig Ordner. "
        "Ohne Pfad wird der aktuelle Ordner genommen. Der Pfad steht gelb in der "
        "Kopfzeile. Im Projekt entsteht .workspace/ mit memory.md, Absprache.md und "
        "Dateien.md – das Projekt-Gedächtnis wandert mit dem Ordner mit. Danach liest "
        "sie Agenten/workspace.md: Ordner ansehen → besprechen → Plan → dein Ja → "
        "README, venv, coden. Nur DU schaltest das; sie hat kein Werkzeug dafür. "
        "'/workspace status' zeigt den aktuellen."),
    Cmd("/workspaceend", "Workspace aufheben – NemiCLI wieder normal", "",
        "📌 Hebt den Riegel auf. Der Ordner .workspace/ bleibt beim Projekt liegen."),
    Cmd("/systemcheck", "PC prüfen 🩺: Grafikkarte, Pakete, was noch fehlt",
        "",
        "🩺 Prüft die Grafikkarte (auch die Rechen-Stufe wie sm_120), sagt den "
        "passenden torch-Installationsbefehl für genau diesen PC und listet, "
        "welche Modelle und Pakete noch fehlen"),
    Cmd("/ml", "ML-Bericht: was mein Ordner-Sinn erkennt & gelernt hat"),
    Cmd("/ordner", "Ordner-Typ per ML einschätzen (aktueller oder [pfad])", "[pfad]",
        "Ordner-Typ per ML einschätzen"),
    Cmd("/web", "Web-Allowlist zeigen/filtern · '/web key' = Ollama-Suche einrichten", "[wort · key]",
        "🌐 Erlaubte Internet-Seiten nach Vertrauens-Tier. '/web python' filtert, "
        "'/web key' trägt den Ollama-API-Key für die Web-Suche ein."),
    Cmd("/webui", "Browser-Oberfläche öffnen (gut lesbar, mit Vorlesen)"),
    Cmd("/chrome", "Chrome-Erweiterung 🐈: Port & Schlüssel zeigen · 'neu' = Schlüssel neu",
        "[neu]",
        "🐈 Rechtsklick in Chrome → „Nemi antwortet“ / „Nemi, erklär mir das“. Die Erweiterung "
        "liegt in chrome-erweiterung/ (chrome://extensions → Entwicklermodus → Entpackte "
        "Erweiterung laden). NemiCLI hört dafür NUR auf 127.0.0.1 (Port aus der Config, "
        "Standard 9000) und verlangt den Geheimschlüssel. '/chrome neu' würfelt ihn neu."),
    Cmd("/emoji", "Smiley- & Emoji-Kürzel anzeigen"),
    Cmd("/nemi", "Maskottchen sagt was 💬"),
    Cmd("/uebung", "Übungsmodus: im Leerlauf selbst üben & lernen 🎓", "an · aus · jetzt",
        "🎓 Übungsmodus: im Leerlauf (~10 min) baut & testet NemiCLI Code "
        "in NemiSandbox/ und lernt daraus"),
    Cmd("/kontextmenue", "Rechtsklick-Menü von Windows 🖱: Bildschirm erfassen · Bild ansehen",
        "an · aus",
        "🖱 Trägt NemiCLI ins Windows-Rechtsklick-Menü ein (nur dein Benutzer, kein Admin): "
        "Desktop/Ordner → „Bildschirm erfassen“ (Screenshot + Pfad in die Zwischenablage), "
        "auf Bildern → „Mit NemiCLI ansehen“. Ein Klick öffnet NemiCLI und schickt „Schau dir "
        "dieses Bild an“ sofort ab (nemicli --sag). Windows 11: unter „Weitere Optionen anzeigen“."),
    Cmd("/subagenten", "Helfer-Agenten 🤝: an (fragt) · auto (nach Bedarf) · off",
        "an · auto · off",
        "🤝 Helfer-Agenten: bis zu 5 eigenständige Helfer, die der Haupt-Agent bei "
        "Bedarf losschickt. an = du wirst vor jedem Losschicken gefragt · auto = "
        "ohne Nachfrage · off = gesperrt. Bleibt über Neustarts gespeichert."),
    Cmd("/auto", "Auto-Stark 🧠: bei schweren Fragen aufs große Gemma schalten",
        "an · aus",
        "🧠 Auto-Stark: läuft ein leichtes Gemma (e4b), schätzt es vor jeder "
        "Antwort selbst ein – bei kniffligen Fragen übernimmt automatisch das "
        "große Gemma (12b). Bleibt über Neustarts gespeichert."),
    Cmd("/reset", "Neuen Chat starten"),
    Cmd("/clear", "Bildschirm leeren"),
    Cmd("/exit", "NemiCLI beenden"),
]

# Name -> Kurzbeschreibung (das Vorschlags-Menü beim Tippen nutzt genau das)
COMMANDS: dict[str, str] = {c.name: c.short for c in COMMAND_LIST}

# Schreibweisen, die alle /persönlichkeiten meinen (ohne Umlaut tippt sich's leichter).
PERSONA_CMDS = ("/persönlichkeiten", "/persoenlichkeiten", "/persönlichkeit",
                "/persoenlichkeit", "/persona")


def help_rows() -> list[tuple[str, str]]:
    """Zeilen für die /help-Tabelle: (Befehl mit Argument, Beschreibung)."""
    return [(c.usage, c.help_text) for c in COMMAND_LIST]


# ---------------------------------------------------------------------------
# Der Vorschlags-Motor für prompt_toolkit
# ---------------------------------------------------------------------------

class SlashCompleter(Completer):
    """
    Zeigt Vorschläge:
      - "/..."        -> passende Befehle (mit Beschreibung)
      - "/theme ..."  -> Theme-Namen
      - "/model ..."  -> Modelle (Cloud + Lokal)
    Normaler Text (ohne "/" am Anfang) bekommt keine Vorschläge.
    """

    def __init__(self, get_themes: Callable[[], dict],
                 get_models: Callable[[], dict],
                 get_strengths: Callable[[], dict] | None = None,
                 get_chats: Callable[[], dict] | None = None,
                 get_checkpoints: Callable[[], dict] | None = None,
                 get_personas: Callable[[], dict] | None = None):
        self._get_themes = get_themes
        self._get_models = get_models
        self._get_strengths = get_strengths or (lambda: {})
        self._get_chats = get_chats or (lambda: {})
        self._get_checkpoints = get_checkpoints or (lambda: {})
        self._get_personas = get_personas or (lambda: {})

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # Noch beim ersten Wort -> Befehle vorschlagen
        if " " not in text:
            for cmd, desc in COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=desc,
                    )
            return

        # Zweites Wort -> Argumente zum jeweiligen Befehl
        cmd, _, rest = text.partition(" ")
        arg = rest.split(" ")[-1]
        for name, meta in self._args_for(cmd):
            if name.startswith(arg):
                yield Completion(
                    name,
                    start_position=-len(arg),
                    display=name,
                    display_meta=meta,
                )

    def _args_for(self, cmd: str):
        if cmd == "/theme":
            return [(k, v["label"]) for k, v in self._get_themes().items()]
        if cmd == "/model":
            return list(self._get_models().items())
        if cmd == "/staerke":
            return list(self._get_strengths().items())
        if cmd == "/resume":
            return list(self._get_chats().items())
        if cmd == "/bildmodel":
            return list(self._get_checkpoints().items())
        if cmd == "/kontextmenue":
            return [("an", "🖱 Einträge ins Rechtsklick-Menü eintragen"),
                    ("aus", "🖱 Einträge wieder entfernen")]
        if cmd == "/subagenten":
            return [("an", "🤝 fragt vor jedem Losschicken"),
                    ("auto", "🤝 Helfer nach Bedarf, ohne Frage"),
                    ("off", "🤝 gesperrt")]
        if cmd == "/modus":
            return [("chat", "💬 Chatten"), ("lesen", "👁 Nur Lesen"),
                    ("normal", "⚙ Normal"), ("auto", "⚡ Auto (5-min-Zünder)")]
        if cmd in PERSONA_CMDS:
            return ([("neu", "Neue Persönlichkeit anlegen (Fragen beantworten)")]
                    + list(self._get_personas().items()))
        if cmd == "/bild":
            flags = [("--neg", "Negativ-Prompt (was NICHT)"),
                     ("--steps", "Schritte (z.B. 28)"),
                     ("--cfg", "Prompt-Treue (z.B. 7)"),
                     ("--size", "Größe, z.B. 768x768"),
                     ("--seed", "Zufalls-Startwert"),
                     ("--model", "welcher Checkpoint")]
            return list(self._get_checkpoints().items()) + flags
        return []


# ---------------------------------------------------------------------------
# Parsen  ->  (befehl, argument)  oder  (None, None) bei normalem Text
# ---------------------------------------------------------------------------

def parse(text: str) -> tuple[str | None, str]:
    """Zerlegt eine Eingabe in (Befehl, Argument). Kein Slash -> (None, text)."""
    text = text.strip()
    if not text.startswith("/"):
        return None, text
    cmd, _, arg = text.partition(" ")
    return cmd.lower(), arg.strip()
