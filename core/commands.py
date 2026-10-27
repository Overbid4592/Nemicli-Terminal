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
    Cmd("/model", "KI-Modell wechseln (Cloud oder Lokal) · Modell von Hugging Face holen · Ersatzbank",
        "[name · huggingface · ersatz]",
        "KI-Modell wechseln (Cloud oder Lokal) – ohne Name: Auswahl-Menü mit 37 Cloud-Anbietern; lange Listen "
        "(Anbieter, Modelle) haben oben 🔍 Suchen. '/model huggingface' holt ein "
        "lokales Modell: Größe und MoE wählen, Q4_K_M vorgeschlagen, Prüfung per SHA-256, fortsetzbar. "
        "'/model ersatz' legt bis zu zwei Cloud-Modelle auf die Ersatzbank: Scheitert das aktive Modell "
        "innerhalb von 30 Minuten fünfmal an einer Schutzsperre (geschützter Befehl, gesperrter Bereich), "
        "wird es herausgefiltert und der erste Ersatz "
        "springt ein – im Terminal, in der Kugel und beim Weckruf der Wache. "
        "Das laufende Gespräch geht weiter – das neue Modell liest den Verlauf als Text neu; "
        "frisch anfangen mit /reset. In der Liste steht auch ✨ Funke Groß, NemiCLIs eigenes kleines Modell "
        "(ohne Schlüssel, ohne Werkzeuge und Bilder; du/Sie, Länge und Emojis wählbar)."),
    Cmd("/staerke", "Denk-Stärke oder Denkbudget wählen", "[stufe]",
        "Zeigt die passenden Stufen für dein Modell. Bei Kimi steuern sie das "
        "gemeinsame Tokenbudget für Denken und Antwort."),
    Cmd("/theme", "Farbschema wechseln", "[name]"),
    Cmd("/modus", "Arbeitsmodus: chat · lesen · normal · auto · autoan (Shift+Tab)", "[name]",
        "💬 Chatten (alles fragt, Merken frei) · 👁 Nur Lesen (Ändern gesperrt) · "
        "⚙ Normal (Ändern fragt) · ⚡ Auto (ohne Rückfragen im Ordner, 5-min-Zünder) · "
        "🚀 Auto ON (wie Auto, bleibt an bis zum Umschalten). "
        "Shift+Tab schaltet durch."),
    Cmd("/persönlichkeiten", "Persönlichkeit wählen oder eigene anlegen 🎭", "[name · neu]",
        "🎭 Wer spricht? Ohne Name: Menü (eingebaute Nemi + deine eigenen). "
        "'/persönlichkeiten neu' legt per Fragen eine neue an – als Markdown-Datei "
        "in Persoenlichkeiten/, die du frei umschreiben kannst. Gilt sofort, bleibt gespeichert."),
    Cmd("/bild", "Bild erzeugen 🎨 (Krea 2 · WebUI · ComfyUI)", "<beschreibung> | schritte",
        "Malt ein Bild mit dem aktiven Motor. '/bild schritte' stellt die Krea-2-Schritte "
        "dauerhaft ein (8 bis 16, Auswahl mit Enter)."),
    Cmd("/video", "Video erzeugen 🎬 (eigener Motor, bis 20 s)",
        "<beschreibung> [--sekunden 10] [--start bild.png] [--seed 1] [--schaerfe mittel] | modell [name] | "
        "cloud [google|xai|runway|fal|minimax|luma|aus|neu] | ordner",
        "🎬 Erzeugt ein Video mit dem eigenen Video-Motor (Wan 2.2 TI2V): 24 Bilder/s, Clips à 5 s aneinander "
        "bis 20 s. Ein Startbild (z. B. von /bild) hält die Figur; das letzte Bild liegt als .png daneben – "
        "Startbild für eine Fortsetzung. Modelle in VideoGen/<Name>/ (.gguf + VAE), geteilt: text_encoders/, tokenizer/. "
        "Ohne Zusatz: Menü (Video 5/10/20 s, Modell wählen, Ordner öffnen). `/video cloud google|xai|runway|fal`: "
        "Videos aus der Cloud statt lokal (Google bis 10 s, xAI bis 15 s, Runway 5/10 s, MiniMax H3 bis 15 s, Luma Ray 3.2 5/10 s, fal.ai je nach Modell; mit "
        "Startbild; ohne Schlüsselbilder, Seed, Schärfe), `/video cloud neu` richtet einen Anbieter mit Schlüssel ein, "
        "`/video cloud aus` zurück."),
    Cmd("/bildmodel", "Bild-Modell wählen 🎨 (Krea 2, externe WebUI/ComfyUI oder Cloud)", "[name|cloud …]",
        "🟣 Krea 2 (eigene Pipeline) ODER 🌐 Modelle einer laufenden Forge/A1111-WebUI bzw. "
        "🧩 ComfyUI ODER ☁ Cloud ohne Grafikkarte (OpenAI, Google, xAI, Together mit dem Schlüssel von /model; "
        "Black Forest Labs/FLUX.2, Ideogram, Runway, fal.ai, Stability, Recraft, Replicate, Luma mit eigenem Schlüssel – "
        "'/bildmodel cloud neu' richtet ihn ein; "
        "'/bildmodel cloud <anbieter>|aus'). Ohne Name: Menü (mit 🔍 Suchen). Die Wahl bleibt gespeichert; "
        "'/bildmodel webui-adresse' ändert den WebUI-Host."),
    Cmd("/venv", "Fenster mit NemiCLIs venv öffnen 🪟 (torch selbst installieren)", "",
        "Öffnet eine Konsole mit aktiviertem venv und zeigt den torch-Befehl für diese Grafikkarte "
        "(NVIDIA: pip3 install torch torchvision --index-url …/cu132)."),
    Cmd("/wissen", "Zeigt, was NemiCLI gelernt hat"),
    Cmd("/gedaechtnis",
        "Langzeitgedächtnis ansehen/löschen · `indexieren` = Vektor-Rückstand jetzt aufholen"),
    Cmd("/embeddings", "Embedding-Modell fürs Gedächtnis 🧬: wählen · Hugging Face",
        "[<Ordner>|huggingface]",
        "🧬 Welches Modell aus Models/embeddings die Texte fürs Gedächtnis in Vektoren "
        "umrechnet: ein Ordner mit safetensors (transformers) oder mit genau einer .gguf "
        "(eigener Motor, gepackt auf der GPU). 'huggingface' öffnet die Modellsuche."),
    Cmd("/reflektieren", "Über das Gespräch nachdenken & Lehren merken 🪞 · auto an|aus", "[auto an|aus]"),
    Cmd("/aufraeumen", "Gedächtnis verdichten: doppelte Notizen verschmelzen 🗜"),
    Cmd("/start", "📁 Wo dein NemiCLI-Ordner liegt (Chats, Bilder, Modelle)", "",
        "📁 Öffnet das Fenster, in dem du deinen NemiCLI-Ordner wählst – den Ort "
        "für Chats, Bilder, Modelle und alles Gelernte. Der ist absichtlich vom "
        "Programm-Ordner getrennt: so darf NemiCLI umziehen oder neu gebaut "
        "werden, ohne dass deine Daten das mitmachen. Es wird nichts gelöscht – "
        "beim Wechsel wird kopiert, das Original bleibt liegen."),
    Cmd("/einrichten", "Einrichtungs-Assistent 🧰: Funke ✨ führt durchs Einrichten, installiert Fehlendes", "[modelle]",
        "🧰 Funke, NemiCLIs eigener kleiner Helfer (von null trainiert, läuft ohne Grafikkarte), fragt im "
        "Gespräch Anrede, Persönlichkeit und Cloud/lokal ab, prüft, was auf dem PC fehlt, und "
        "installiert es auf Wunsch selbst (Python, venv, Pakete). torch installierst du selbst im "
        "venv-Fenster, das es dir öffnet (/venv). `/einrichten modelle` (auch am Ende der Einrichtung): "
        "die Modelle der Motoren freiwillig nachladen – Gedächtnis, Reranker, Bildbeschreiber, Stimme, Musik, "
        "Video, Porträt – mit Lizenz und Größe vorab, aus der offiziellen Quelle, SHA-256 geprüft, direkt in "
        "den richtigen Ordner; Esc hält an, nochmal wählen setzt fort. Krea 2 nur als Hinweis (Ordner und "
        "Link). Sprach-Modelle: /model. Wer durch die Einrichtung führt, wählst du am Anfang: Funke oder, sobald "
        "ihr LoRA mitgeliefert ist, Gemma 4 E2B/E4B (Grundmodell Q4_K_M von unsloth, lädt /einrichten; braucht torch)"),
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
    Cmd("/doku", "Offline-Doku 📚: Python und MDN für den Coding-Assistenten", "[laden python|mdn · loeschen <quelle>]",
        "📚 Lädt die offizielle Python-Doku (~4 MB, passend zur Python-Version) oder MDN (HTML, CSS, "
        "JavaScript, Web-APIs; großer Download) und legt einen Volltext-Index an. Die KI sucht darin mit "
        "doku_suchen, statt aus dem Gedächtnis zu raten. Ohne Zusatz: was geladen ist."),
    Cmd("/charakter", "Charakter-Datei 🧬: feste Figur der Persönlichkeit für Bilder", "[oeffnen]",
        "🧬 Zeigt die Charakter-Datei der aktiven Persönlichkeit (Profile/<Name>/<name>.json): Kern-Look, "
        "fester Seed, Stil, Outfits, Posen, Regeln. `/charakter oeffnen` öffnet sie im Editor. Anlegen und "
        "ändern kann auch die KI (charakter_aendern, mit Bestätigung)."),
    Cmd("/kugel", "Schwebekugel 🔮: Gesicht der Persönlichkeit malen, ansehen, entfernen",
        "[malen [beschreibung|bereich|alle] · motive · impuls an|aus · ankommen an|aus · leben an|aus · "
        "musik an|aus · erinnerungen · weg · ordner]",
        "🔮 Ohne Bild ist die Kugel eine Kugel. `/kugel malen` lässt die aktive Persönlichkeit sich "
        "selbst beschreiben und malt vier Stimmungsbilder (neutral, froh, ernst, denkt) mit festem "
        "Seed über den Bild-Motor, stellt sie frei und legt sie in Persoenlichkeiten/<Name>/ ab. "
        "`/kugel malen <beschreibung>` nimmt deine Beschreibung. `/kugel motive` zeigt 20 Bereiche "
        "mit 400 Motiven; `/kugel malen <nummer|bereich>` oder `alle` malt die fehlenden mit derselben "
        "Figur (10 Schritte je Bild). `/kugel impuls an`: ab und an (alle 1–3 h) ein freier Moment – "
        "sie entscheidet selbst, ob sie etwas sagt, malt oder still bleibt; das steht auch im NemiCLI-Chat, und schaust "
        "du nicht hin, kommt ein Hinweis neben der Uhr. Malen nur, wenn mehr als 5 GB Grafikspeicher frei sind. `/kugel ankommen` (Vorgabe an): kommst du nach "
        "mindestens 3 Stunden zurück, gibt es vielleicht eine Begrüßung an der Kugel. `/kugel leben` (Vorgabe an): "
        "sie schläft, wenn du weg bist, gähnt nachts, freut sich übers Streicheln (Maus kreisen) und setzt sich ab "
        "und zu auf die Kante des aktiven Fensters. `/kugel musik` (Vorgabe an): läuft Musik länger, sagt sie ab und zu "
        "etwas zum Lied (Spotify, YouTube, VLC …). Was ihre Figur trägt, wählt sie selbst (Outfits). Bilder und Dateien kannst du ihr auf die Kugel ziehen. "
        "`/kugel erinnerungen` zeigt offene Erinnerungen („erinnere mich in 20 Minuten an …“). "
        "`/kugel weg` = zurück zur Kugel."),
    Cmd("/wache", "Systemwache 🛡: Sensoren, Regeln, lernendes Modell – im Hintergrund",
        "[status · alarme · start · stop · …]",
        "🛡 Die Wache läuft als Hintergrund-Prozess mit Symbol in der Taskleiste: Prozesse, "
        "Netz und Dateien werden beobachtet, 15 Regeln und ein eigener Isolation Forest "
        "bewerten, ab 200 Ereignissen wird trainiert, dann alle 200 von selbst. Dazu ein "
        "Folgen-Netz (GRU), das ungewöhnliche Abläufe in Prozess-Ketten erkennt – zuerst im "
        "Schattenmodus (nur Protokoll); scharf schaltest nur du mit /wache dl warnen. Bei Alarm "
        "wird die aktive Persönlichkeit geweckt, prüft nach, urteilt und darf in deinen "
        "Grenzen justieren – alles im Protokoll. Ohne Zusatz: Lage. Weitere: alarme [id], "
        "ereignisse [prozess], inventar [art], regeln, justierungen, rueckgaengig, schwelle <x>, "
        "trigger <mittel|hoch|kritisch|nie>, trigger <5–20> (ab so vielen ungeprüften Alarmen jeder "
        "Stufe wird sie geweckt und muss jeden bewerten; Standard 5), stumm/laut <R00x>, grenzen, training, selbsttest, "
        "dl [schatten|warnen|aus|an|zurueck|schwelle <x>] (Folgen-Netz: Stand, Schatten-Funde, Version zurückdrehen), "
        "programm [<name> [tage] · stop <name> · status <name> · erlauben <name> <prozess>] (Programm-Absolvierung: die Wache lernt, welche Hintergrund-Prozesse ein Programm startet, und meldet danach Neues als R015 – oder der KI sagen „pass auf Steam auf“), "
        "netz [adresse|Meldung einfügen|warnen|schatten|aus|an|zurueck|register an|aus] (Netzwächter: wer steckt hinter einer Adresse – Name, Besitzer laut Adressregister, ob NemiCLI selbst sie nutzt; ohne Zusatz die Verbindungen nach draußen mit Ampel; ML + DL beginnen im Schattenmodus), "
        "ml zurueck (Isolation Forest auf die vorige Version; nach jedem Zurückdrehen lernt das Modell erst nach deinem nächsten Training (Tray oder /wache training) wieder von selbst), "
        "start, stop, an, aus, autostart an|aus, kugel an|aus, reset. "
        "fullscan [status|stop] (höchstens 3 neue Vollscans in 7 Tagen, Fortsetzen zählt nicht): jede Festplatte mit Partitionen und Volumes (auch ohne "
        "Laufwerksbuchstaben, auch versteckte) als Ausgangsstand – jede Datei mit Attributen, für Programme "
        "und Skripte Hash, Signatur und Download-Herkunft (Änderungen zum letzten Lauf im Bericht), "
        "zusätzliche Datenströme, Verweise und nicht lesbare Ordner mit Grund; je Laufwerk Dateiarten und "
        "größte Ordner; dazu Autostart, Dienste, Aufgaben, Treiber und Netz mit Ampel; mit Windows-Build "
        "und Updates, fortsetzbar, das Modell lernt den Stand mit. Von Windows gesperrte Systembereiche "
        "sind Revier des Virenschutzes und bleiben zu. Die Schwebekugel schwebt "
        "auf dem Desktop, wenn kein Terminal offen ist – Klick öffnet ein kleines Chatfenster."),
    Cmd("/backup", "Backup 💾: Teil (Bilder, Persönlichkeiten, Profile) oder vollständig – Zip mit SHA-256, danach Nachträge · auspacken",
        "[teil · voll · voll modelle · … ganz · auto [jetzt] · pruefen · auspacken <name> · ordner]",
        "💾 Sichert als Zip mit .sha256 daneben in den Ordner Backup/ im Daten-Ordner. 'teil' = Bilder, "
        "Persönlichkeiten, Profile. 'voll' = ganzer Daten-Ordner und Programm (ohne venv, Cache, Modelle); "
        "'voll modelle' auch mit Modelldateien (sehr groß). Gibt es schon ein volles Backup derselben Art "
        "(höchstens 30 Tage alt), entsteht ein Nachtrag mit nur dem, was seitdem neu oder geändert ist; "
        "'ganz' sichert trotzdem alles neu. 'auspacken <name>' stellt in einen neuen Ordner wieder her "
        "(Nachtrag + sein volles Backup, jede Datei geprüft). 'pruefen' rechnet die Prüfsummen nach. "
        "Esc bricht ab. Ohne Zusatz: vorhandene Backups. 'auto': einmal am Tag sichert NemiCLI von selbst "
        "Gedächtnis, Profile, Persönlichkeiten, Agenten, Skills, Zeitplan und Config (ohne Bilder, Modelle, "
        "Datenbanken) und überschreibt dabei NemiCLI_auto_aktuell.zip – der Stand davor bleibt als "
        "NemiCLI_auto_vorher.zip. Ist das Gedächtnis kaputt oder plötzlich halb leer, hält sie an und sagt es; "
        "'auto jetzt' sichert sofort, auch dann."),
    Cmd("/undo", "Papierkorb 🗑: Gelöschtes/Überschriebenes zurückholen", "",
        "🗑 Vor jedem loeschen, datei_schreiben, datei_bearbeiten und Überschreiben beim "
        "verschieben landet der alte Stand in Papierkorb/ (im Daten-Ordner). Hier siehst du "
        "die letzten zehn Sicherungen und holst eine zurück. Nach 30 Tagen räumt sich der "
        "Papierkorb selbst auf."),
    Cmd("/schluessel", "Schlüssel 🔑: Programm-Ordner auf Zeit für die KI öffnen (jede Änderung fragt)",
        "<minuten> [aufgabe] · aus",
        "🔑 Der NemiCLI-Programm-Ordner ist für die KI zu. '/schluessel 30 Tippfehler in ui.py' öffnet "
        "ihn 30 Minuten für genau diese Aufgabe – jede Änderung fragt dich trotzdem einzeln, danach ist "
        "wieder zu. '/schluessel aus' schließt sofort. Ihr Daten-Ordner ist davon unabhängig: dort "
        "schreibt sie ohne Rückfrage (Protokoll + Papierkorb)."),
    Cmd("/limit", "Obergrenze 🛑: wie viele Dateien die KI pro Sitzung löschen darf", "[zahl]",
        "🛑 Mehr als das Limit (Standard 20) auf einmal oder in Summe → STOPP, ohne Rückfrage. "
        "Nur du änderst es: '/limit 50'. Die KI hat dafür kein Werkzeug."),
    Cmd("/whitelist", "Befehls-Whitelist ✅: welche `befehl`-Kommandos als harmlos gelten", "[befehl]",
        "✅ `befehl` fragt einmal, wenn das Kommando nur liest oder auf der Whitelist steht "
        "(git status, pip list …) – sonst zweimal, rot markiert. '/whitelist git push' prüft "
        "einen Befehl. Die Liste liegt in befehl_whitelist.json im Programm-Ordner."),
    Cmd("/update", "Aktualisieren ⬇: venv prüfen – Lücken, fehlende Pakete, Updates", "",
        "⬇ Prüft das venv neben NemiCLI gegen requirements.txt und die Bild-Bausteine: fehlt etwas, "
        "passt eine Version nicht, stimmt der torch-Bau nicht zur Grafikkarte, gibt es erlaubte Updates? "
        "Dazu bekannte Sicherheitslücken und Schadpakete aller Pakete (OSV.dev) – behoben wird mit der "
        "ersten reparierten Version. Zeigt eine Liste und fragt; nach dem Installieren prüft ein Import-Test, "
        "ob alles lädt, sonst kommt die alte Version zurück. Mit Cloud-Modell kann die KI die Lücken erklären. "
        "Pakete, die gerade benutzt werden, kommen beim nächsten Start. torch wird nur repariert. "
        "Im Quelltext-Modus mit Git-Remote vorher git pull."),
    Cmd("/code", "Coding-Assistent 🟦: Programmieren auf festem Weg mit Todo-Liste", "[aufgabe · status]",
        "🟦 Startet den Coding-Assistenten (startet sonst auch von selbst bei Coding-Aufgaben): "
        "Auftragskarte → Recherche → Todo-Liste (🔴 offen · 🟠 Frage · 🟢 geprüft) → Punkt für Punkt "
        "abarbeiten. Grün setzt nur die Prüfung, nie die KI. Ablage im Projekt unter .nemicli/ "
        "(mit memory.md fürs Projekt-Gedächtnis). Dateiaktionen außerhalb des Projekts legt "
        "NemiCLI dir in jedem Modus zur Freigabe vor. "
        "Bleibt an, bis du /codeend tippst oder „Coding aus“ sagst. '/code status' zeigt die Liste."),
    Cmd("/codeend", "Coding-Assistent beenden", "",
        "🟦 Schaltet den Coding-Assistenten ab. Die Ablage .nemicli/ bleibt beim Projekt liegen."),
    Cmd("/systemcheck", "PC prüfen 🩺: CPU, Grafikkarten, RAM, Pakete, was noch fehlt",
        "",
        "🩺 Steckbrief des PCs (CPU mit AVX, alle Grafikkarten, RAM, Windows, womit lokale Modelle "
        "rechnen), prüft die Grafikkarte (auch die Rechen-Stufe wie sm_120), sagt den "
        "passenden torch-Installationsbefehl für genau diesen PC und listet, "
        "welche Modelle und Pakete noch fehlen"),
    Cmd("/ml", "ML-Bericht: was mein Ordner-Sinn erkennt & gelernt hat"),
    Cmd("/ordner", "Ordner-Typ per ML einschätzen (aktueller oder [pfad])", "[pfad]",
        "Ordner-Typ per ML einschätzen"),
    Cmd("/vision", "Bildbeschreiber 👁: Seh-Modell für Modelle, die selbst nicht sehen",
        "[<ordner>|restart|debug|protokoll|ordner]",
        "👁 Sieht das aktive Modell (lokal, Ollama oder Cloud) keine Bilder, schaut ein Seh-Modell aus "
        "Vision/<Ordner>/ (.gguf + mmproj-*.gguf) hin und gibt ihm die Beschreibung als Text – das Bild selbst "
        "geht nirgendwohin. Je Modell ein eigener Ordner; '/vision <ordner>' wählt dauerhaft, ohne Zusatz: "
        "Liste und Stand. Nach 2 Minuten ohne Bild wird er ganz entladen. 'restart': entladen und neu laden. "
        "'debug': Fehlerbericht nach Debugbericht/vision_bericht.txt. 'protokoll': welche Bilder beschrieben "
        "wurden – je Persönlichkeit; dasselbe Bild mit derselben Frage kommt von dort, ohne neu zu laden."),
    Cmd("/voice", "Stimme 🔊: an (jede Antwort) · knopf (nur auf Anfrage) · aus · jetzt · text",
        "[an|knopf|aus|jetzt|text]",
        "🔊 Stimme der Persönlichkeit (Qwen3-TTS unter Vision/). 'an': jede Antwort wird vorgelesen, auf dem "
        "Bildschirm steht nur der Anfang. 'knopf' (Vorgabe): spricht nur auf Anfrage – '/voice jetzt' "
        "oder 🔊 unter einer Antwort im /gui-Fenster liest die Antwort einmal vor – und gibt den Grafikspeicher danach sofort frei. 'aus': bleibt aus, bis du "
        "wieder einschaltest; nichts lädt, auch nicht auf Wunsch der KI. '/voice text' zeigt die letzte Antwort "
        "ganz, Esc stoppt das Sprechen."),
    Cmd("/web", "Web-Allowlist zeigen/filtern · key = Ollama-Suche · fremdseiten frei|fragen|aus",
        "[wort · key · fremdseiten frei|fragen|aus]",
        "🌐 Erlaubte Internet-Seiten nach Vertrauens-Tier. '/web python' filtert, "
        "'/web key' trägt den Ollama-API-Key für die Web-Suche ein. '/web fremdseiten frei|fragen|aus': "
        "Seiten außerhalb der Allowlist über Ollama lesen (als unbekannte Seite, niedrigstes Vertrauen) – frei "
        "(Vorgabe, aus Suchtreffern ohne Rückfrage), fragen (jede vorher) oder aus."),
    Cmd("/name", "Über dich 👤: Name, Wunsch-Anrede, was die KI wissen soll – gilt ab der ersten Nachricht",
        "[zeigen|loeschen]"),
    Cmd("/skills", "Skills 🧩: Liste mit Bilanz und Wächter-Bericht · selbst an|aus · alte Notizen übernehmen",
        "[selbst an|aus | alte]",
        "🧩 Skills sind Anleitungen für die KI. Sie legt sie selbst an und bessert sie aus – ohne Prüffenster; "
        "der Skill-Wächter hält Unsinniges auf (u. a. riskante Aktionen, private Daten, Doppelte, nach Inhalt "
        "aus dem Netz) und fragt eine KI-Zweitmeinung. Jeder Skill hat eine Strichliste (benutzt · Probleme · "
        "verbessert); nach Problemen wird er im Hintergrund nachgebessert. '/skills selbst aus' holt das "
        "Prüffenster zurück. 'alte' zeigt alte Notizen aus learned/skills zum Übernehmen."),
    Cmd("/gui", "Desktop-Fenster öffnen (Chat | Work, Galerie, Persönlichkeiten, ⚙ Einstellungen) – mit dieser Sitzung verbunden"),
    Cmd("/musik", "Musik 🎵: Fenster für Songwünsche – die KI arbeitet Stil und Liedtext aus, dann komponieren",
        "",
        "🎵 Wunsch auf Deutsch eintippen (Länge, mit oder ohne Gesang). ✨ Ausarbeiten: die KI macht daraus einen "
        "englischen Stil und einen Liedtext mit [Verse]/[Chorus]-Marken – beides lässt sich ändern. 🎵 Komponieren: "
        "der Musik-Motor (ACE-Step 1.5, Models/Musik) rechnet den Song als WAV nach Profile/<Name>/Musik/. "
        "🎙 Als Cover: den markierten Song (oder eine eigene Datei: WAV, MP3, M4A, FLAC) im Stil oben neu "
        "einspielen – Melodie und Aufbau bleiben, „Nähe“ hält auch den Klang näher am Original. "
        "Ohne Fenster: einfach der KI sagen, welchen Song sie machen oder covern soll."),
    Cmd("/webui", "Browser-Oberfläche öffnen (gut lesbar, mit Vorlesen)"),
    Cmd("/spiel", "Spielen 🎲: Schach · Mühle · Dame · TicTacToe im Browser gegen die Persönlichkeit"),
    Cmd("/appstore", "Appstore 🛍: Apps aus HTML/CSS/JS, deren Funktionen die KI nutzt · 🐞 debugAI",
        "[<app> | debug <app> | liste | ordner]",
        "🛍 Öffnet den Store in NemiView – NemiCLIs eigener Engine (eigenes JavaScript, HTML, CSS, Zeichnen; kein "
        "Chrome, kein Internet, keine Grafikkarte): Kacheln mit Icon, „Öffnen“ und „🐞 debugAI“. Eine App ist ein "
        "Ordner unter Apps/ (app.json, index.html, style.css, app.js, icon.svg); ihre Funktionen ruft die KI mit "
        "app_aufrufen auf, app_pruefen meldet Fehler. In jeder App sitzt unten rechts ein 🐞: debugAI zeigt, welche "
        "App es ist (ID, Version, Dateien), fragt, was stört, und du tippst auf die Stelle (oder schickst ohne "
        "Stelle). Die KI ändert sie (Rückfragen im debugAI-Bereich); vorher wird die App gesichert. Danach ⇆ vorher "
        "→ nachher nebeneinander, ✓ Behalten oder ↶ Zurück. Zwei Beispiel-Apps kommen mit (Würfel, Farbmischer)."),
    Cmd("/chrome", "Chrome-Erweiterung 🐈: Seitenleisten-Chat + Rechtsklick · Port & Schlüssel · 'neu' = Schlüssel neu",
        "[neu]",
        "🐈 Seitenleiste: Klick aufs Symbol in Chrome öffnet einen Chat mit dieser Sitzung (wie /gui im Modus "
        "„Chat“, Antwort live, Kugel-Bild der Persönlichkeit). 📄 Seite schickt die Seite oder den markierten "
        "Text mit, 📸 Foto ein Bildschirmfoto – beides als Fremdinhalt. Vorgeschriebene Texte stehen dort als "
        "Entwurf mit „Ins Textfeld“ und „Kopieren“. Auf Chromes eigenen Seiten (Neuer Tab, chrome://, Web "
        "Store) erlaubt Chrome weder Lesen noch Foto. Rechtsklick in Chrome → „Nemi antwortet“ / „Nemi, "
        "erklär mir das“. Die Erweiterung "
        "liegt in chrome-erweiterung/ (chrome://extensions → Entwicklermodus → Entpackte "
        "Erweiterung laden). NemiCLI hört dafür NUR auf 127.0.0.1 (Port aus der Config, "
        "Standard 9000) und verlangt den Geheimschlüssel. '/chrome neu' würfelt ihn neu."),
    Cmd("/emoji", "Smiley- & Emoji-Kürzel anzeigen"),
    Cmd("/nemi", "Maskottchen sagt was 💬"),
    Cmd("/uebung", "Übungsmodus: im Leerlauf selbst üben & lernen 🎓", "an · aus · jetzt",
        "🎓 Übungsmodus: im Leerlauf (~10 min) baut & testet NemiCLI Code "
        "in NemiSandbox/ und lernt daraus. 'aus' bleibt gespeichert – auch nach Neustart"),
    Cmd("/kontext", "Kontext 📊: wie voll (Anleitung, Gedächtnis, Skills, Nachrichten) · lokal: 25k (Vorgabe) "
        "· 8k … 128k · max",
        "[anzeigen|8k|16k|25k|32k|64k|128k|max]",
        "📊 Zeigt, womit der Kontext gefüllt ist – wie /context bei Claude Code. Beim lokalen Modell "
        "stellt es außerdem ein, wie viele Token es hält (Vorgabe 25k). Ist er nach einer Antwort zu drei "
        "Vierteln voll, räumt NemiCLI im Hintergrund auf (Doppelpuffer, Preview; eigener Motor und lokales "
        "Ollama): die KI schreibt einen „Stand des Gesprächs“ (Ziel, Wünsche, Erledigtes, Fehler, Offenes, "
        "nächster Schritt, Fakten – dazu deine Nachrichten wörtlich), die ältesten Nachrichten wandern ins "
        "Gedächtnis und bleiben auffindbar, die neuesten bleiben. Der Stand wird mit dem Chat gespeichert. "
        "Bei Ollama gilt der Kontext, mit dem Ollama das Modell geladen hat. "
        "Alles andere entscheidet NemiCLI beim Laden selbst, damit der eingestellte Kontext ganz gilt: "
        "passt alles in die Grafikkarte, bleibt es dort; sonst erst der Gesprächsspeicher in 8 Bit (halber "
        "Platz), dann wandern Schichten in den RAM (Hybrid, über PCIe, langsamer) – bei MoE-Modellen bleiben "
        "die meistgebrauchten Experten in der Grafikkarte (Experten-Lager, lernt im Gespräch mit), zuletzt ein "
        "Teil des Gesprächsspeichers. Die Anzeige zeigt, was gewählt wurde."),
    Cmd("/charaktere", "Charakter-Galerie 🎭: fertige Persönlichkeiten übernehmen oder eigene erschaffen", "",
        "🎭 Eigenes Fenster: 9 fertige Persönlichkeiten mit Porträt zum Ansehen und Übernehmen – oder eine eigene erschaffen: Geschlecht (weiblich, männlich, nichtbinär), Alter (ab 18), bis zu 8 Eigenschaften; die KI schreibt Vorschläge, Krea malt je ein Porträt. Beim Ansehen hilft ✨ die KI, ein Feld zu verbessern (Wesen, Aussehen, Stil), 🎨 malt neu (gleiches oder neues Gesicht). Übernehmen legt die Persönlichkeit an, dazu im Profil die Charakter-Datei (Aussehen, fester Seed) und das Porträt als Profilbild – und macht sie aktiv. „Meinen Charakter verbessern“ öffnet die Charakter-Datei der aktiven Persönlichkeit. Auch als /galerie."),
    Cmd("/compact", "Gespräch jetzt zusammenfassen 📌 (Stand des Gesprächs) · zeigen", "[jetzt|zeigen]",
        "📌 Wie /compact bei Claude Code: die KI fasst das bisherige Gespräch im „Stand des Gesprächs“ zusammen "
        "(Ziel, Wünsche, Erledigtes, Fehler & Lösungen, Offenes, nächster Schritt, Fakten – deine Nachrichten "
        "wörtlich dazu), nur die neuesten Nachrichten bleiben im Kontext. Nichts geht verloren: alles bleibt im "
        "Chat gespeichert und im Gedächtnis auffindbar. Macht NemiCLI bei lokalen Modellen auch selbst, wenn der "
        "Kontext voll wird. 'zeigen': den aktuellen Stand anzeigen. Auch als /kompakt."),
    Cmd("/debug", "Debugbericht 🐞: läuft immer – die KI liest ihn (Cloud) · aus · an · motor", "aus · an · motor an|aus",
        "🐞 NemiCLI schreibt immer mit, was es mit dem Grafikspeicher macht – Modelle, Stimme, Bildbeschreiber, "
        "Embedding, Bildmotor, Runden, Werkzeuge, alle Fehler – dazu alle 10 s den Stand je Prozess, auch den "
        "Arbeitsspeicher (eigener RAM, festgenagelter RAM, größte RAM-Nutzer aller Programme). Eine Datei je "
        "Programm-Start unter Debugbericht/ (die 30 neuesten bleiben), auch für die Wache. Mit einem Cloud-Modell "
        "liest die KI ihn selbst: frag einfach „Was sagt der Debug?“ – ihre Auswertung legt sie als Markdown unter "
        "Berichte/ ab. 'aus' schaltet ab; 'motor an' schreibt zusätzlich jeden Prompt des lokalen Motors mit."),
    Cmd("/sandbox", "Sandbox 🧪: Status · Projektordner freigeben/entziehen",
        "[freigeben <Ordner> [schreiben] | entziehen <Ordner>]",
        "🧪 Code, den die KI ausführt, läuft im Windows-AppContainer: Internet ja, deine Dateien, "
        "Registry und andere Programme nein. '/sandbox freigeben <Ordner>' lässt Code aus diesem "
        "Projekt dort laufen (nur lesen, mit 'schreiben' auch ändern); nur du kannst freigeben."),
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
    Cmd("/restart", "NemiCLI und Wache neu starten", "",
        "🔄 Beendet die Wache und startet NemiCLI im selben Fenster neu – nach Updates oder wenn etwas "
        "hängt. Greift sofort, auch mitten in der Arbeit. Nur über deine eigene Eingabe."),
    Cmd("/exit", "NemiCLI beenden"),
]

# Name -> Kurzbeschreibung (das Vorschlags-Menü beim Tippen nutzt genau das)
COMMANDS: dict[str, str] = {c.name: c.short for c in COMMAND_LIST}

# Schreibweisen, die alle /persönlichkeiten meinen (ohne Umlaut tippt sich's leichter).
PERSONA_CMDS = ("/persönlichkeiten", "/persoenlichkeiten", "/persönlichkeit",
                "/persoenlichkeit", "/persona")


# ---------------------------------------------------------------------------
# Auswahl, wenn ein Befehl ohne Zusatz getippt wird (↑/↓, 1–9, Enter, Esc)
# ---------------------------------------------------------------------------
# Eintrag: (Zusatz, Anzeige, Frage). Zusatz "" = wie ohne Zusatz (Status/Anzeige).
# "{}" im Zusatz wird durch die Antwort auf die Frage ersetzt; leere Antwort bricht ab.
UNTERMENUES: dict[str, list[tuple[str, str, str | None]]] = {
    "/wache": [
        ("status", "🛡 Lage: Sensoren, Modell, offene Alarme", None),
        ("alarme", "🚨 Alarme ansehen", None),
        ("ereignisse", "📋 Letzte Ereignisse", None),
        ("inventar", "📦 Systeminventar", None),
        ("fullscan", "🔍 Vollscan (ganzes System)", None),
        ("start", "▶ Wache starten", None),
        ("stop", "⏹ Wache stoppen", None),
        ("regeln", "📏 Regeln", None),
        ("bekannt", "✅ Bekanntes (als harmlos gemerkt)", None),
        ("trigger {}", "⏰ Prüfen ab … Alarmen (5–20)", "Ab wie vielen ungeprüften Alarmen prüfen? (5–20)"),
        ("justierungen", "🎚 Justierungen", None),
        ("rueckgaengig", "↶ Letzte Justierung zurücknehmen", None),
        ("grenzen", "🧱 Grenzen der Justierung", None),
        ("training", "🧠 Modell neu lernen", None),
        ("ml zurueck", "↶ ML-Modell zurückdrehen", None),
        ("dl", "🔗 Folgen-Netz (Prozess-Ketten)", None),
        ("netz", "🌐 Netzwächter (Verbindungen)", None),
        ("autostart", "🚀 Autostart", None),
        ("selbsttest", "🧪 Selbsttest", None),
    ],
    "/sandbox": [
        ("", "🧪 Status: Ordner, Python, Projekte, Pakete", None),
        ("freigeben {}", "📂 Projekt freigeben – nur lesen", "Welcher Ordner?"),
        ("freigeben {} schreiben", "✏ Projekt freigeben – lesen + schreiben", "Welcher Ordner?"),
        ("entziehen {}", "🔒 Freigabe entziehen", "Welcher Ordner?"),
    ],
    "/reflektieren": [
        ("", "🪞 Jetzt reflektieren", None),
        ("auto", "Automatisch: Stand anzeigen", None),
        ("auto an", "Automatisch: an (nach Korrekturen, Fehlern, größeren Aufgaben)", None),
        ("auto aus", "Automatisch: aus", None),
    ],
    "/name": [
        ("", "✏ Eintragen oder ändern", None),
        ("zeigen", "👤 Anzeigen", None),
        ("loeschen", "🗑 Alles löschen", None),
    ],
    "/skills": [
        ("", "🧩 Liste: Name, Beschreibung, wann", None),
        ("alte", "📝 Alte Notizen (learned/skills) durchsehen und übernehmen", None),
        ("selbst an", "Selbst anlegen/ändern: an (ohne Rückfrage)", None),
        ("selbst aus", "Selbst anlegen/ändern: aus (Prüffenster)", None),
    ],
    "/uebung": [
        ("", "🎓 Status", None),
        ("jetzt", "▶ Jetzt eine Übung", None),
        ("an", "An", None),
        ("aus", "Aus", None),
    ],
    "/kontextmenue": [
        ("", "🖱 Status", None),
        ("an", "Ins Rechtsklick-Menü eintragen", None),
        ("aus", "Aus dem Rechtsklick-Menü entfernen", None),
    ],
    "/web": [
        ("", "🌐 Allowlist zeigen", None),
        ("key", "🔑 Ollama-Schlüssel eintragen (Suche + Fremdseiten)", None),
        ("fremdseiten frei", "◐ Seiten außerhalb der Allowlist über Ollama lesen", None),
        ("fremdseiten fragen", "❓ Seiten außerhalb der Allowlist nur nach Rückfrage", None),
        ("fremdseiten aus", "✗ Nur Allowlist-Seiten lesen", None),
    ],
    "/voice": [
        ("", "🔊 Status", None),
        ("an", "An – jede Antwort hören", None),
        ("knopf", "Knopf – nur auf Anfrage, danach sofort entladen", None),
        ("aus", "Aus – bleibt aus, nichts lädt", None),
        ("jetzt", "🔊 Letzte Antwort einmal vorlesen", None),
        ("text", "📄 Letzte Antwort ganz lesen", None),
    ],
    "/subagenten": [
        ("", "🤝 Status", None),
        ("an", "An – fragt vor jedem Helfer", None),
        ("auto", "Auto – Helfer nach Bedarf", None),
        ("off", "Aus – gesperrt", None),
    ],
    "/auto": [
        ("", "Status", None),
        ("an", "Auto-Stark an", None),
        ("aus", "Auto-Stark aus", None),
    ],
    "/bild": [
        ("", "🎨 Status: Bild-Motor und Nutzung", None),
        ("{}", "🖌 Bild malen", "Was soll gemalt werden?"),
        ("schritte", "🔢 Krea-2-Schritte einstellen (8 bis max. 16, bleibt gespeichert)", None),
    ],
    "/doku": [
        ("", "📚 Was ist geladen?", None),
        ("laden python", "🐍 Python-Doku laden (~4 MB)", None),
        ("laden mdn", "🌐 MDN laden: HTML, CSS, JavaScript, Web-APIs (großer Download)", None),
    ],
    "/backup": [
        ("", "💾 Vorhandene Backups", None),
        ("teil", "🖼 Teilbackup: Bilder, Persönlichkeiten, Profile", None),
        ("voll", "📦 Vollständig: Daten-Ordner und Programm (ohne Modelle)", None),
        ("voll modelle", "🧱 Vollständig mit Modellen (sehr groß)", None),
        ("teil ganz", "🖼 Teilbackup ganz neu statt Nachtrag", None),
        ("voll ganz", "📦 Vollständig ganz neu statt Nachtrag", None),
        ("auto", "🔁 Automatische Sicherung: Stand", None),
        ("auto jetzt", "🔁 Automatische Sicherung jetzt", None),
        ("pruefen", "🔍 Prüfsummen aller Backups nachrechnen", None),
        ("auspacken", "♻ Wiederherstellen in einen neuen Ordner (Name aus der Liste)", None),
        ("ordner", "📁 Backup-Ordner öffnen", None),
    ],
    "/charakter": [
        ("", "🧬 Charakter-Datei ansehen", None),
        ("oeffnen", "📝 Im Editor öffnen", None),
    ],
    "/kugel": [
        ("", "🔮 Status", None),
        ("malen {}", "🎨 Gesicht malen lassen", "Wie soll sie aussehen? (leer = aus der Charakter-Datei, sonst beschreibt sie sich selbst)"),
        ("motive", "🖼 Motive: 20 Bereiche, was schon gemalt ist", None),
        ("motive {}", "🎨 Motive malen", "Bereich (Nummer oder Name) oder alle"),
        ("impuls an", "🌈 Freier Moment an", None),
        ("impuls aus", "Freier Moment aus", None),
        ("ankommen an", "👋 Begrüßen nach längerer Pause an", None),
        ("ankommen aus", "Begrüßen nach längerer Pause aus", None),
        ("leben an", "🐾 Desktop-Leben an", None),
        ("leben aus", "Desktop-Leben aus", None),
        ("musik an", "🎵 Musik spüren an", None),
        ("musik aus", "Musik spüren aus", None),
        ("erinnerungen", "⏰ Offene Erinnerungen", None),
        ("weg", "⚪ Zurück zur Kugel", None),
        ("ordner", "📁 Ordner öffnen", None),
    ],
    "/code": [
        ("status", "🟨 Todo-Liste zeigen", None),
        ("{}", "🟦 Mit Aufgabe starten", "Was soll gebaut werden?"),
        ("end", "Coding-Assistent beenden", None),
    ],
    "/schluessel": [
        ("{}", "🔑 Programm-Ordner auf Zeit öffnen", "Wie viele Minuten? (danach optional die Aufgabe)"),
        ("aus", "🔒 Schlüssel abziehen", None),
    ],
    "/limit": [
        ("", "🛑 Lösch-Limit anzeigen", None),
        ("{}", "Limit ändern", "Wie viele Dateien pro Sitzung?"),
    ],
    "/protokoll": [
        ("", "📜 Letzte 30", None),
        ("10", "Letzte 10", None),
        ("100", "Letzte 100", None),
    ],
    "/statistik": [
        ("", "📊 Anzeigen", None),
        ("reset", "Zurücksetzen", None),
    ],
    "/chrome": [
        ("", "🐈 Port und Schlüssel anzeigen", None),
        ("neu", "Neuen Schlüssel erzeugen", None),
    ],
}
UNTERMENUES["/schlüssel"] = UNTERMENUES["/schluessel"]
ZURUECK = "__zurueck__"


def help_rows() -> list[tuple[str, str]]:
    """Zeilen für die /help-Tabelle: (Befehl mit Argument, Beschreibung)."""
    return [(c.usage, c.help_text) for c in COMMAND_LIST]


def _fold(s: str) -> str:
    """Vergleichsform: klein, Umlaute ausgeschrieben."""
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return s


def find_commands(text: str, ausfuehrlich: bool = False) -> list[tuple[str, str]]:
    """Befehle zu einer Eingabe wie "/mod": erst Treffer am Namensanfang, dann
    im Namen, dann im Kurztext (ab 3 Zeichen; `ausfuehrlich`: auch im langen Text).
    Liefert (Name, Kurztext)."""
    q = _fold(text.lstrip("/"))
    if not q:
        return [(c.name, c.short) for c in COMMAND_LIST]
    anfang, im_namen, in_text = [], [], []
    for c in COMMAND_LIST:
        name = _fold(c.name[1:])
        if name.startswith(q):
            anfang.append(c)
        elif q in name:
            im_namen.append(c)
        elif len(q) >= 3 and q in _fold(f"{c.short} {c.long}" if ausfuehrlich else c.short):
            in_text.append(c)
    return [(c.name, c.short) for c in anfang + im_namen + in_text]


TASTEN = ("Enter senden · Strg+J neue Zeile · Esc abbrechen · F2 Denktext · F8 Änderung freigeben · "
          "F12 Gespräch sichern · Shift+Tab Modus · Tab/↑↓ im /-Menü · Strg+C markierten Text kopieren · "
          "Strg+K Suche (alle Befehle, Einstellungen, Modelle; Tippfehler okay) · F3 Cockpit (Grafikkarte, Tempo, "
          "Kontext, Hintergrund-Aufgaben, Todo) · Klick auf ✓/⚙-Zeilen klappt Werkzeug-Ergebnisse auf")

EINSTELLUNGEN = """\
## Einstellungen und Dateien
Selbst ändern (nur auf direkten Wunsch, der Nutzer bestätigt): einstellung_aendern mit
was "internet_erlauben" oder "internet_sperren" und wert = Domain. Eigene Einträge liegen in
allowlist_eigen.json im Programm-Ordner, die mitgelieferte Liste in allowlist.json; /web zeigt alle.
Gefällt kein Farbschema: mit theme_erstellen ein eigenes entwerfen (themes_eigen.json im Programm-Ordner),
danach /theme mit menue_oeffnen öffnen.
Nur der Nutzer selbst – nenne ihm den Weg:
- Im Desktop-Fenster (/gui) unter ⚙ Einstellungen per Klick: Stimme, Arbeitsweise, Denkstufe, Farben; unter
  „Für Profis“ Kontext, Gedächtnis-Modell, Reflektieren, Skills, Übung, Helfer, Fremdseiten, Kugel,
  Rechtsklick-Menü, Debugbericht
- Modell, Cloud-Key: /model → „Cloud-Anbieter hinzufügen“ (Key wird verschlüsselt gespeichert)
- Lösch-Limit /limit · Befehls-Whitelist /whitelist (befehl_whitelist.json) · Projekt für die
  Sandbox freigeben /sandbox · Programm-Ordner auf Zeit öffnen /schluessel · Sperrliste
  schreibsperre.json im Programm-Ordner (nur von Hand)
- Daten-Ordner wählen /start: chats, Bilder, Persoenlichkeiten, Skills, Papierkorb (/undo),
  Protokoll learned/aktionen.log
- Lokales Modell holen: /model → „Modell von Hugging Face holen“ (oder /model huggingface) –
  Größe und MoE wählen, landet in ModelGGUF/<Name>/; danach /model → Lokal.
  Gedächtnis-Modelle: Models/embeddings (/embeddings)
- Charakter-LoRA beim Malen (Krea 2, nur wenn die Persönlichkeit selbst im Bild ist): Config-Schlüssel
  bild_lora (Vorgabe an) und bild_lora_staerke (Vorgabe 1.0); Bilder mit LoRA laufen ohne TensorRT
- Mausrad-Tempo im Vollbild: Config-Schlüssel scroll_zeilen (Zeilen je Raste, 1–20, Vorgabe 4),
  gilt ab dem nächsten Start
- Mitdenken: Config-Schlüssel beobachter (meldet der KI, was sich seit ihrem letzten Blick geändert hat:
  Dateien, Ordner, Modell, Persönlichkeit, lange Pause) und gegenprobe (prüft die fertige Antwort gegen die
  Werkzeug-Ergebnisse der Runde, z. B. „Erledigt!“ obwohl etwas scheiterte) – beide Vorgabe an, false schaltet ab
- Netz-Sperre: Config-Schlüssel netz_sperre – "verlauf" (Vorgabe: gilt, solange Netz-Inhalt im Gespräch
  steht) oder "runde" (nur die Runde mit dem Abruf). Fremdseiten: /web fremdseiten frei|fragen|aus.
  Seiten, die Befehle einschmuggeln wollten, stehen in web_verdaechtig.json im Programm-Ordner
- Eigenes Terminal-Fenster (NemiCLI.exe): Reiter wie bei GNOME-Terminal – Strg+Umschalt+T neue
  Kommandozeile, Strg+Umschalt+W schließen, Strg+Bild↑/↓ wechseln. Reiter „🤖 KI“ (Strg+Umschalt+K)
  zeigt jeden Befehl der KI mit Ausgabe live, nur zum Lesen; geht beim ersten Befehl von selbst auf.
  Aus: Config-Schlüssel terminal_ki_reiter false. Aussehen: terminal_thema (breeze | mint),
  terminal_hintergrund (Durchsicht 0.3–1.0), terminal_schrift, terminal_groesse – ab dem nächsten Start"""


# Auswahl nach laufendem Stand (Theme, Modus, Stärke …); main.py setzt das beim Start.
AUSWAHL_JETZT: Callable[[str], list] | None = None


def auswahl(name: str) -> list[tuple[str, str, str | None]]:
    """Einträge des Auswahlmenüs eines Befehls: laufender Stand, sonst die festen."""
    if AUSWAHL_JETZT is not None:
        try:
            return list(AUSWAHL_JETZT(name) or [])
        except Exception:
            pass
    return list(UNTERMENUES.get(name, []))


# Werkzeug menue_oeffnen: Die KI öffnet ein Auswahlmenü, der Nutzer wählt selbst.
# Sicherheit, Modus und Arbeitsordner bleiben beim Nutzer.
# /compact braucht TURN_LOCK, den der Zug der KI selbst hält (asyncio.Lock ist nicht wiedereintrittsfähig).
MENUE_GESPERRT = {"/schluessel", "/schlüssel", "/limit", "/whitelist", "/sandbox", "/modus", "/auto",
                  "/wache", "/code", "/compact", "/restart", "/neustart"}
# /appstore öffnet nur das Store-Fenster – Bauen, Entfernen und debugAI löst dort der Nutzer selbst aus.
MENUE_EIGENE = {"/theme", "/staerke", "/kontext", "/embeddings", "/model", "/bildmodel", "/video", "/debug",
                "/vision", "/appstore"}
MENUE_UNTERPUNKTE = {"/bild": {"schritte"}, "/model": {"huggingface", "ersatz"}}   # Zusatz öffnet ein weiteres Menü


def menue_befehle() -> list[str]:
    """Befehle, deren Menü die KI öffnen darf."""
    namen = (set(UNTERMENUES) | MENUE_EIGENE) - MENUE_GESPERRT
    return sorted(namen & {c.name for c in COMMAND_LIST})


def menue_pruefen(befehl) -> tuple[str | None, str]:
    """("/befehl [zusatz]", "") wenn die KI dieses Menü öffnen darf, sonst (None, Grund)."""
    teile = str(befehl or "").strip().split()
    if not teile:
        return None, "Feld 'befehl' fehlt (z. B. \"/theme\")."
    cmd = teile[0].lower()
    cmd = cmd if cmd.startswith("/") else "/" + cmd
    erlaubt = menue_befehle()
    if cmd not in erlaubt:
        return None, (f"{cmd} kann ich nicht öffnen – nenne dem Nutzer den Befehl. "
                      "Öffnen kann ich: " + " ".join(erlaubt))
    zusatz = " ".join(teile[1:]).lower()
    if zusatz and zusatz not in MENUE_UNTERPUNKTE.get(cmd, set()):
        return None, (f"'{cmd} {zusatz}' würde direkt etwas ändern – öffne nur '{cmd}', der Nutzer wählt selbst."
                      + "".join(f" Erlaubt: {cmd} {u}." for u in sorted(MENUE_UNTERPUNKTE.get(cmd, ()))))
    return f"{cmd} {zusatz}".strip(), ""


def _werte_kurz(name: str) -> str:
    """Gültige Werte eines Befehls in einer Zeile ("cyan (aktiv) · matrix"), nur bei kurzen,
    reinen Wertelisten; sonst leer."""
    eintraege = auswahl(name)
    if not eintraege or len(eintraege) > 8 or any(not z or "{}" in z or " " in z for z, _, _ in eintraege):
        return ""
    return " · ".join(z + (" (aktiv)" if t.lstrip().startswith("✓") else "") for z, t, _ in eintraege)


def hilfe_fuer_ki(thema: str = "") -> str:
    """Bedienhilfe zu NemiCLI für das Modell. Leer: alle Befehle kurz, mit den gültigen Werten.
    "/befehl" oder Suchwort: die passenden Befehle ausführlich, mit Untermenü."""
    thema = (thema or "").strip()
    if not thema:
        zeilen = []
        for c in COMMAND_LIST:
            werte = _werte_kurz(c.name)
            zeilen.append(f"- {c.usage} — {c.short}" + (f"  [Werte: {werte}]" if werte else ""))
        return ("# NemiCLI-Bedienung\nSlash-Befehle tippt der Nutzer selbst. Diese Menüs kannst du ihm "
                "mit menue_oeffnen öffnen (er wählt selbst): " + " ".join(menue_befehle())
                + ", dazu /bild schritte und /model huggingface. Sonst nenne den passenden Befehl und was "
                "dort zu wählen ist. Bei [Werte: …] gibt es genau diese Werte – keine anderen nennen; die "
                "Werte anderer Befehle stehen unter anleitung_lesen \"/name\".\n\n"
                + "\n".join(zeilen)
                + f"\n\nTasten: {TASTEN}\n\n" + EINSTELLUNGEN
                + "\n\nDetails zu einem Befehl: anleitung_lesen mit thema \"/name\".")
    namen = [n for n, _ in find_commands(thema if thema.startswith("/") else "/" + thema,
                                         ausfuehrlich=True)][:5]
    if not namen:
        return ""
    nach_name = {c.name: c for c in COMMAND_LIST}
    teile = []
    for n in namen:
        c = nach_name[n]
        teil = f"## {c.usage}\n{c.help_text}"
        eintraege = auswahl(c.name)
        if eintraege:
            teil += "\nAuswahl ohne Zusatz (✓ = jetzt aktiv): " + " · ".join(
                f"{c.name} {z}".replace("{}", "<…>").strip() + f" ({t.strip()})"
                for z, t, _ in eintraege)
        teile.append(teil)
    return "\n\n".join(teile)


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
                 get_checkpoints: Callable[[], dict] | None = None,
                 get_personas: Callable[[], dict] | None = None):
        self._get_themes = get_themes
        self._get_models = get_models
        self._get_strengths = get_strengths or (lambda: {})
        self._get_checkpoints = get_checkpoints or (lambda: {})
        self._get_personas = get_personas or (lambda: {})

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # Noch beim ersten Wort -> Befehle vorschlagen
        if " " not in text:
            for cmd, desc in find_commands(text):
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
            return ([("huggingface", "🤗 Modell von Hugging Face holen"),
                     ("ersatz", "🪑 Ersatzbank: bis zu 2 Cloud-KIs, die einspringen")]
                    + list(self._get_models().items()))
        if cmd == "/staerke":
            return list(self._get_strengths().items())
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
                    ("normal", "⚙ Normal"), ("auto", "⚡ Auto (5-min-Zünder)"),
                    ("autoan", "🚀 Auto ON (dauerhaft)")]
        if cmd in PERSONA_CMDS:
            return ([("neu", "Neue Persönlichkeit anlegen (Fragen beantworten)")]
                    + list(self._get_personas().items()))
        if cmd == "/bild":
            flags = [("schritte", "Krea-2-Schritte dauerhaft einstellen (8–16)"),
                     ("--steps", "Schritte nur für dieses Bild"),
                     ("--size", "Größe, z.B. 1024x1024"),
                     ("--seed", "Zufalls-Startwert"),
                     ("--model", "welches Bild-Modell"),
                     ("--neg", "Negativ-Prompt (nur WebUI/ComfyUI)"),
                     ("--cfg", "Prompt-Treue (nur WebUI/ComfyUI)")]
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
