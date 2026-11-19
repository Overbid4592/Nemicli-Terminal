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
# Vorschlags-Menü und Hilfe bauen sich beide hieraus (help_rows()).


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
        "[name · chatgpt · huggingface · ersatz]",
        "KI-Modell wechseln (Cloud oder Lokal) – ohne Name: Auswahl-Menü mit 37 Cloud-Anbietern; lange Listen "
        "(Anbieter, Modelle) haben oben 🔍 Suchen. '/model huggingface' holt ein "
        "lokales Modell: Größe und MoE wählen, Q4_K_M vorgeschlagen, Prüfung per SHA-256, fortsetzbar. "
        "'/model ersatz' legt bis zu zwei Cloud-Modelle auf die Ersatzbank: Scheitert das aktive Modell "
        "innerhalb von 30 Minuten fünfmal an einer Schutzsperre (geschützter Befehl, gesperrter Bereich), "
        "wird es herausgefiltert und der erste Ersatz "
        "springt ein – im Terminal, in der Kugel und beim Weckruf der Wache. "
        "Das laufende Gespräch geht weiter – das neue Modell liest den Verlauf als Text neu; "
        "frisch anfangen mit /reset. "
        "🔐 ChatGPT-Konto (Plus/Pro): unter „Cloud-Anbieter hinzufügen“ oder '/model chatgpt' – Anmeldung im Browser "
        "(Sign in with ChatGPT), es zählt dein ChatGPT-Plan statt eines API-Schlüssels; '/model chatgpt abmelden' "
        "löscht den Zugang. OpenAI-Modelle laufen über die Responses API (Denkstufen bis max bei GPT-5.6/6.x)."),
    Cmd("/bewegung", "Figur bewegt sich nach Text 🕺: Stand · holen · einrichten · an|aus · probe",
        "[holen · einrichten [pfad] · an · aus · probe <englischer Satz>]",
        "🕺 Die Figur im eigenen Terminal-Fenster wird ein Strichmännchen mit dem Gesicht der Persönlichkeit, das "
        "sich nach Text bewegt (SprongMotion, 20 Mio. Parameter, nur CPU, unter einer Sekunde je Bewegung). "
        "'/bewegung holen' lädt die Laufzeit 1.1.0 (173 KB) nur von GitHub, Größe und SHA-256 von Paket und DLL "
        "sind fest. Das Modell SprongMotion-14M-v1.0-Q8_0.gguf (33 MB, Apache-2.0, huggingface.co/SegerEnd/"
        "SprongMotion-14M) und die Laufzeit liegen der exe bei und kommen beim ersten Start nach KI-Modelle/KI-Bewegung/Bewegungsmodell/ im "
        "Installationsordner (neben NemiCLI.exe). Ohne die exe lädt NemiCLI das Modell nicht selbst: dorthin legen oder "
        "'/bewegung einrichten' verschiebt es aus dem Download-Ordner an seinen Platz (ein angegebener Pfad wird kopiert), nur mit passender SHA-256. "
        "'/bewegung aus' zeigt wieder die Bilder-Figur, 'an' das Strichmännchen. '/bewegung probe waves with both "
        "hands' lässt sie gleich etwas tun. Die KI bewegt sie mit dem Werkzeug figur_bewegen: „dreh dich“ → "
        "„turns around once“, „lächel mal“ → Gesicht froh. In Ruhe lässt sie die Arme hängen; Status und Zugende "
        "zeigt sie mit passenden Bewegungen (nachdenken, prüfen, tippen, Freudensprung, winken). Größe, Länge von "
        "Kopf, Rumpf, Armen, Beinen und Füßen und ihren Platz stellst du mit Rechtsklick auf die Figur → „Figur "
        "anpassen …“ ein (Regler, sie bewegt sich live mit) – oder sag es der KI („mach deine Arme etwas länger“)."),
    Cmd("/llamacpp", "llama.cpp-Reserve 🦙: Stand · holen · aktualisieren · wechseln · einrichten · vergleich",
        "[holen [auto|cuda-13.5|cuda-12.4|vulkan|cpu] · aktualisieren · wechseln [build] · einrichten · "
        "vergleich [frage]]",
        "🦙 llama.cpp als Reserve neben dem eigenen Motor: genau ein Build ist aktiv, jede Datei per SHA-256 "
        "geprüft. '/llamacpp holen' wählt das Paket nach der Grafikkarte, lädt es nur von GitHub (Ladebalken, Esc "
        "bricht ab, ein neuer Aufruf setzt fort) und richtet es ein; mit einem Cloud-Modell kann das auch die KI "
        "(nach deinem OK). '/llamacpp aktualisieren' holt den neuesten Build (SHA-256 aus den GitHub-Release-Daten), "
        "richtet ihn neben dem aktiven ein, prüft per Probelauf, dass er jeden festen Schalter kennt, und schaltet "
        "ihn erst dann ein. Drei Builds bleiben, der älteste wandert in den Papierkorb; '/llamacpp wechseln <build>' "
        "schaltet zurück. Von Hand: die offiziellen Pakete nach Motoren/llama.cpp im Datenordner legen, dann "
        "'/llamacpp einrichten' – es prüft und entpackt sie. Danach steht im /model-Menü jedes lokale Modell auch mit 🦙 "
        "(über llama.cpp; mit mmproj-Datei im Ordner sieht es auch dort Bilder). Kann der eigene Motor ein Modell "
        "nicht laden, fragt NemiCLI, ob die Reserve "
        "übernehmen darf. Der Server läuft nur auf 127.0.0.1 mit zufälligem Schlüssel, ohne Web-Oberfläche, Jinja, "
        "Werkzeuge, RPC, Slot-Speicher und Schlafmodus. '/llamacpp vergleich [frage]' stellt dem aktiven lokalen "
        "Modell dieselbe Frage in beiden Motoren (gierig): wo weichen die Token ab, was hätte llama.cpp gewählt, "
        "wie schnell ist jeder – das Ergebnis steht auch in Debugbericht/motorvergleich.md. Gesprächsspeicher (KV): "
        "llama_kv in nemicli.config.json = auto (Vorgabe: f16, wenn er für den Kontext passt, sonst q8_0, q4_0 erst "
        "wenn auch q8_0 keine 4096 Token schafft) | f16 | q8_0 | q4_0; gepresst läuft er mit Flash Attention, scheitert "
        "das, startet der Server einmal mit f16. Nach 'wechseln' beendet jeder NemiCLI-Prozess den alten Server vor "
        "seiner nächsten Anfrage. Esc bricht auch während des Einlesens eines langen Prompts ab."),
    Cmd("/staerke", "Denk-Stärke oder Denkbudget wählen", "[stufe]",
        "Zeigt die passenden Stufen für dein Modell. Bei Kimi steuern sie das "
        "gemeinsame Tokenbudget für Denken und Antwort. Eigener Motor: das Denk-Budget "
        "(schnell 1.024 · normal 8.192 · stark 16.384 · max ohne Grenze)."),
    Cmd("/denken", "Ob die KI nachdenkt – je Persönlichkeit 🧭", "auto · an · aus",
        "auto (Vorgabe): denkt nach, wenn es etwas bringt – bei kniffligen Fragen und bei Bitten, etwas zu tun "
        "(malen, zeigen, lesen, nochmal probieren); Plaudern bleibt schnell. an: vor jeder Antwort. aus: nie. "
        "Gilt für jedes Modell: schaltbares Nachdenken (eigener Motor) wird eingeschaltet, sonst gibt das Denkwerk "
        "den Denkanstoß. Jede Persönlichkeit merkt sich ihre eigene Denkweise. Wie tief: /staerke. "
        "Kündigt die KI etwas an („ich male jetzt …“), ohne es aufzurufen, bekommt sie einmal einen Stups 🤝."),
    Cmd("/theme", "Farbschema wechseln", "[name]",
        "Eingebaut: cyan · matrix · amber · cyber (nur Terminal), nachtglas · pastell · neon · tigra (Terminal und alle Fenster; tigra: Violett und Cyan, dazu Rosé und Gold, ♥ neben der Version). Die Wahl bleibt nach einem Neustart. Eigene Themes legt die KI mit theme_erstellen an. Text hat in jedem Theme mindestens 4,5 : 1 Kontrast, auch wenn der Hintergrund des Terminal-Fensters durchscheint; Rahmen dürfen leiser sein. Schrift größer: /schriftgröße."),
    Cmd("/anzeige", "Werkzeug-Anzeige: kompakt oder mit Rahmen", "kompakt · rahmen",
        "kompakt (Vorgabe): eine Zeile „● Befehl(git status)“, darunter eingerückt „⎿ Ergebnis“; "
        "nach einer Dateiänderung ein rot/grüner Diff mit Zeilennummern. Im Vollbild klappt ein Klick "
        "die ganze Ausgabe auf. rahmen: die bisherigen Kästen. Rückfragen bleiben immer groß."),
    Cmd("/modus", "Arbeitsmodus: chat · lesen · normal · auto · autoan (Shift+Tab)", "[name]",
        "💬 Chatten (alles fragt, Merken frei) · 👁 Nur Lesen (Ändern gesperrt) · "
        "⚙ Normal (Ändern fragt) · ⚡ Auto (ohne Rückfragen im Ordner, 5-min-Zünder) · "
        "🚀 Auto ON (wie Auto, bleibt an bis zum Umschalten). "
        "In beiden Auto-Modi läuft Riskantes nur von der Gästeliste (/whitelist): sonst hält "
        "NemiCLI an und fragt. Shift+Tab schaltet durch. Jeder Bereich hat seine eigene Stufe: 💬 Chat = dieser "
        "Modus; 🟦 Coding = frei (im Projekt durcharbeiten) oder vorsicht – solange der Coding-Assistent an ist, "
        "schaltet Shift+Tab diese Stufe, unten steht sie neben dem Projekt; 🎨 Bilder = frei (malen ohne Rückfrage, "
        "in jedem Bereich) oder fragen. Zusätze: coding frei|vorsicht · bild frei|fragen (auch unter ⚙ Einstellungen)."),
    Cmd("/persönlichkeiten", "Persönlichkeit wählen oder eigene anlegen 🎭", "[name · neu]",
        "🎭 Wer spricht? Ohne Name: Menü (eingebaute Nemi + deine eigenen). "
        "'/persönlichkeiten neu' legt per Fragen eine neue an – als Markdown-Datei "
        "in Persoenlichkeiten/, die du frei umschreiben kannst. Gilt sofort, bleibt gespeichert."),
    Cmd("/bild", "Bild erzeugen 🎨 (Krea 2 · WebUI · ComfyUI)", "<beschreibung> [--figur] | schritte | gesicht [an|aus]",
        "Malt ein Bild mit dem aktiven Motor, den Prompt genau so, wie er dasteht. Mit --figur malt es die Figur der "
        "aktiven Persönlichkeit: der 🔒 Kern steht davor, dazu ihr Seed und die gebundene Bild-LoRA (Kern · [name] · "
        "Prompt). '/bild schritte' stellt die Krea-2-Schritte dauerhaft ein (8 bis 16, Auswahl mit Enter). "
        "'/bild gesicht an|aus' schaltet die Gesichts-Korrektur für alle Bilder (Gesichter nach dem Malen vergrößert "
        "nachmalen); --ohne-gesicht lässt sie für ein Bild weg."),
    Cmd("/video", "Video erzeugen 🎬 (eigener Motor, bis 20 s)",
        "<beschreibung> [--sekunden 10] [--start bild.png] [--figur] [--seed 1] [--schaerfe mittel] | modell [name] | "
        "einstellungen [name wert] | cloud [google|xai|runway|fal|minimax|luma|aus|neu] | ordner | motorordner",
        "🎞 Video-Motoren mit Ton (LTX-2.5: Dateien in KI-Modelle/KI-Video/Videomodell/<Name>/, Gemma 4 mit Projektion in "
        "text_encoders/, Faltungs-VAE und Ton-VAE in vae/; `/video modell <name>` wählt, `/video einstellungen` zeigt "
        "und ändert Auflösung (auto/480p/720p/1080p), Format, Bilder/s, Schritte, CFG, Ton ✔️/🛑, VAE-Kacheln, "
        "Speicher – AUTO nach Grafikspeicher ab 8 GB; `/video motorordner` öffnet den Ordner) ODER "
        "🎬 der eigene Video-Motor Wan 2.2 TI2V: 24 Bilder/s, Clips à 5 s aneinander "
        "bis 20 s. Ein Startbild (z. B. von /bild) hält die Figur; das letzte Bild liegt als .png daneben – "
        "Startbild für eine Fortsetzung. Modelle in VideoGen/<Name>/ (.gguf + VAE), geteilt: text_encoders/, tokenizer/. "
        "Ohne Zusatz: Menü (Video 5/10/20 s, Modell wählen, Ordner öffnen). `/video cloud google|xai|runway|fal`: "
        "Videos aus der Cloud statt lokal (Google bis 10 s, xAI bis 15 s, Runway 5/10 s, MiniMax H3 bis 15 s, Luma Ray 3.2 5/10 s, fal.ai je nach Modell; mit "
        "Startbild; ohne Schlüsselbilder, Seed, Schärfe), `/video cloud neu` richtet einen Anbieter mit Schlüssel ein, "
        "`/video cloud aus` zurück. "
        "🎬 SD-Video ist ein weiterer Video-Motor: Stable Diffusion 1.5 oder SDXL mit Zeit-Schichten, 8 Bilder/s, "
        "ohne Ton, bis 20 s. Dafür liegt ein ganzer Bild-Checkpoint in KI-Modelle/KI-Video/Videomodell/<Name>/, dazu ein "
        "Bewegungs-Modul (AnimateDiff-Lightning oder AnimateDiff v3 für SD 1.5, Hotshot-XL für SDXL) im Modellordner "
        "oder in bewegung/ und der CLIP-Tokenizer in tokenizer_clip/. Die offiziellen Bewegungs-Module werden per "
        "SHA-256 geprüft. Jedes SD-Video beginnt mit einem Startbild, das die Figur hält: ohne --start malt der "
        "Bild-Motor zuerst eines aus der Beschreibung, mit --figur die Figur der aktiven Persönlichkeit mit Kern, "
        "Seed und Bild-LoRA. Abschalten: `/video einstellungen startbild aus`."),
    Cmd("/bildmodel", "Bild-Modell wählen 🎨 (Krea 2, Z-Image, FLUX.2, WebUI/ComfyUI oder Cloud)", "[name|cloud …]",
        "🟣 Krea 2 (eigene Pipeline) ODER weitere eigene Bild-Motoren (🟩 Z-Image, 🟦 FLUX.2 klein, 🟧 Qwen-Image, "
        "🟪 FLUX.1, 🟥 Chroma; Dateien in KI-Modelle/KI-Bild/BildgenKI/<Name>/, Zubehör laut Anleitung.txt im Ordner; "
        "bf16/FP8/MXFP8/NVFP4/INT8 oder GGUF – auch für Krea 2, dort mehrere Modelle im Krea2-Ordner, "
        "Raw mit 52 Schritten und CFG; Menüpunkt 'Bild-Modelle-Ordner öffnen') ODER "
        "🌐 Modelle einer laufenden Forge/A1111-WebUI bzw. "
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
        "Langzeitgedächtnis ansehen/löschen · "
        "`archiv` = Ersetztes und Vergessenes · `zurueck <Nr>` = aus dem Archiv zurückholen",
        "",
        "Notizen, Archiv und was noch offen ist. Einbetten: /einlesen."),
    Cmd("/einlesen", "Einlese-Speicher jetzt ins Gedächtnis einlesen 📦 · stand", "[stand]",
        "📦 Chats, Notizen, Skills, Berichte, Wissen und Bilder sammeln sich im Einlese-Speicher: gleich nach "
        "Stichworten findbar, nach Bedeutung erst nach dem Einlesen. Der Balken zeigt den Stand („38k │ 100k“) und füllt sich "
        "in halben Feldern, ändert er sich, leuchtet er kurz auf: bis 50k grün, dann orange, ab 80k dunkelrot. Bei 99k startet das Einlesen von selbst, sobald keine Antwort "
        "läuft und die GPU Platz hat. Es rechnet nur auf der GPU (höchstens 25 % des Grafikspeichers, Config "
        "embedding_vram_anteil, ohne Grafikkarte auf der CPU) und trennt einen festen Block ab: Was du danach "
        "schreibst, sammelt der nächste Speicher. /einlesen startet es früher. Ein Bild zählt 800 Token, Text "
        "Zeichen / 4."),
    Cmd("/embeddings", "Embedding-Modell fürs Gedächtnis 🧬: wählen · Hugging Face",
        "[<Ordner>|huggingface]",
        "🧬 Welches Modell aus KI-Modelle/KI-Gedaechtnis/Suchmodell die Texte fürs Gedächtnis in Vektoren "
        "umrechnet: ein Ordner mit safetensors (transformers) oder mit genau einer .gguf "
        "(eigener Motor, gepackt auf der GPU). Empfohlen: EmbeddingGemma 2 (klein, ~310 MB in Q8, lädt "
        "/einrichten modelle; eine mmproj-….gguf daneben ist der Bild- und Ton-Teil: Bilder, Videos und Ton werden nach "
        "Inhalt auffindbar, Werkzeug medien_suchen). Qwen3-Embedding geht weiterhin. Config embedding_dim 512/256/128 "
        "kürzt die Vektoren von EmbeddingGemma 2 (Matryoshka, weniger Platz; danach rechnet NemiCLI das Gedächtnis neu). Ein Wechsel rechnet das Gedächtnis "
        "nach und nach neu um. 'huggingface' öffnet die Modellsuche."),
    Cmd("/reflektieren", "Über das Gespräch nachdenken & Lehren merken 🪞 · auto an|aus · selbst",
        "[auto an|aus · selbst [verlauf|zurueck|an|aus]]",
        "🪞 Reflexion über das Gespräch; danach sieht die Persönlichkeit ihren Selbstentwurf an (nur Cloud oder lokal "
        "ab 6B). Im Takt zusätzlich morgens, mittags und abends je einmal im Leerlauf. 'selbst' zeigt den Stand, "
        "'selbst zurueck' holt den vorigen zurück."),
    Cmd("/aufraeumen", "Gedächtnis verdichten: mit Cloud-KI ähnliche und alte Notizen prüfen, sonst doppelte "
        "verschmelzen (alles ins Archiv) 🗜"),
    Cmd("/start", "📁 Wo dein NemiCLI-Ordner liegt (Chats, Bilder, Modelle)", "",
        "📁 Öffnet das Fenster, in dem du deinen NemiCLI-Ordner wählst – den Ort "
        "für Chats, Bilder, Modelle und alles Gelernte. Der ist absichtlich vom "
        "Programm-Ordner getrennt: so darf NemiCLI umziehen oder neu gebaut "
        "werden, ohne dass deine Daten das mitmachen. Es wird nichts gelöscht – "
        "beim Wechsel wird kopiert, das Original bleibt liegen."),
    Cmd("/einrichten", "Einrichtungs-Assistent 🧰: prüft, fragt, installiert Fehlendes", "[modelle]",
        "🧰 Prüft, was auf dem PC fehlt, und installiert es auf Wunsch selbst (Python, venv, Pakete). "
        "torch installierst du selbst im venv-Fenster, das es dir öffnet (/venv). Ist das LoRA eines "
        "Gemma-Helfers mitgeliefert, wählst du am Anfang Gemma 4 E2B/E4B (Grundmodell Q4_K_M von unsloth, "
        "lädt /einrichten; braucht torch): Gemma fragt im Gespräch Anrede, Persönlichkeit und Cloud/lokal ab. "
        "Sonst läuft der klassische Ablauf Schritt für Schritt. `/einrichten modelle` (auch am Ende der Einrichtung): "
        "die Modelle der Motoren freiwillig nachladen – Gedächtnis, Reranker, Bildbeschreiber, Stimme, Musik, "
        "Video, Porträt – mit Lizenz und Größe vorab, aus der offiziellen Quelle, SHA-256 geprüft, direkt in "
        "den richtigen Ordner; Esc hält an, nochmal wählen setzt fort. Krea 2 nur als Hinweis (Ordner und "
        "Link). Sprach-Modelle: /model."),
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
        "musik an|aus · zuschauen an|aus · auto an|aus · vorne an|aus · erinnerungen · weg · ordner]",
        "🔮 Ohne Bild ist die Kugel eine Kugel. `/kugel malen` lässt die aktive Persönlichkeit sich "
        "selbst beschreiben und malt vier Stimmungsbilder (neutral, froh, ernst, denkt) mit festem "
        "Seed über den Bild-Motor, stellt sie frei und legt sie in Persoenlichkeiten/<Name>/ ab. "
        "`/kugel malen <beschreibung>` nimmt deine Beschreibung. `/kugel motive` zeigt 20 Bereiche "
        "mit 400 Motiven; `/kugel malen <nummer|bereich>` oder `alle` malt die fehlenden mit derselben "
        "Figur (10 Schritte je Bild). `/kugel malen figur` malt zuerst den beweglichen Körper (in T-Haltung, von vorn "
        "und von hinten; NemiCLI legt alle 22 Gelenke selbst darauf und legt ihr Bild als Hülle um das Skelett: je Knochen "
        "ein starres Stück, an den Gelenken runde Kappen, dazu acht Gesichter vom Porträt-Motor – so bewegt sich die Figur im Terminal mit ihrem echten Aussehen; ältere Körper malt es neu; bei einem neuen Outfit wird er mitgemalt), "
        "dann dieselbe Figur ganz und sitzend in 10 Posen "
        "(sitzt, winkt, denkt nach, prüft, arbeitet, freut sich, Herz, grinst, neugierig, döst) für die Figur rechts "
        "im eigenen Terminal-Fenster (Persoenlichkeiten/<Name>/Figur/, /terminal figur an|aus). `/kugel impuls an`: ab und an (alle 1–3 h) ein freier Moment – "
        "sie entscheidet selbst, ob sie etwas sagt, malt oder still bleibt; das steht auch im NemiCLI-Chat, und schaust "
        "du nicht hin, kommt ein Hinweis neben der Uhr. Malen nur, wenn mehr als 5 GB Grafikspeicher frei sind. `/kugel ankommen` (Vorgabe an): kommst du nach "
        "mindestens 3 Stunden zurück, gibt es vielleicht eine Begrüßung an der Kugel. `/kugel leben` (Vorgabe an): "
        "sie schläft, wenn du weg bist, gähnt nachts und morgens, freut sich übers Streicheln (Maus kreisen, langsam "
        "drüberstreichen), kichert beim Anstupsen (Rechtsklick), wird schwindelig, wenn man wild hin und her fährt, "
        "neigt sich zum Mauszeiger, zeigt ein passendes Motiv zu dem, was du machst (Spiel, Code, Video, Langeweile), "
        "zieht nachts ihren Schlafanzug an, wenn sie einen hat, und setzt sich ab "
        "und zu auf die Kante des aktiven Fensters. `/kugel musik` (Vorgabe an): läuft Musik länger, sagt sie ab und zu "
        "etwas zum Lied (Spotify, YouTube, VLC …). `/kugel zuschauen an` (Vorgabe aus): läuft ein Spiel, schaut sie "
        "alle paar Minuten auf den Bildschirm und sagt etwas, wenn es spannend ist (Cloud-Modell mit Bild-Eingang; "
        "lokal nur, wenn genug Grafikspeicher frei ist) – sie ist dann auch beim Spielen da, wenn NemiCLI offen ist. "
        "Strg+Alt+N öffnet ihr Fenster jederzeit, auch über einem Spiel: Fragen zum Spiel schlägt sie im Netz nach "
        "(Config kugel_frage_taste, \"aus\" schaltet ab; kugel_zuschauen_min = Minuten zwischen zwei Blicken). "
        "Im exklusiven Vollbild zeigt Windows nichts darüber – „Randloses Fenster“ im Spiel wählen. Was ihre Figur trägt, wählt sie selbst (Outfits). Bilder und Dateien kannst du ihr auf die Kugel ziehen. "
        "`/kugel erinnerungen` zeigt offene Erinnerungen („erinnere mich in 20 Minuten an …“). "
        "`/kugel auto an|aus` schaltet Freier Moment, Begrüßen, Desktop-Leben, Musik und Zuschauen auf einmal "
        "(die einzelnen Schalter gibt es weiter); Erinnerungen kommen immer. `/kugel vorne` (Vorgabe an): die Kugel "
        "bleibt über allen Fenstern, NemiCLI holt sie zurück, wenn etwas sie verdeckt – aus: ein normales Fenster. "
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
        "Stufe wird sie geweckt und muss jeden bewerten; Standard 5), stumm/laut <R00x> oder ein Paar <R00x@programm.exe> "
        "(die KI darf nur Paare stumm schalten, nie ganze Regeln; R007, R012, SIGMA, WS-001 und CVE-001 nie), "
        "anleitung ok (eine außerhalb von NemiCLI geänderte wache_alarm.md bzw. Agenten/systemwache.md als deinen "
        "Stand übernehmen – bis dahin warnt der Weckruf und zeigt sie nur als fremde Daten; WS-001 meldet fremd "
        "veränderte Lern-Dateien der Wache), grenzen, training, selbsttest, "
        "dl [schatten|warnen|aus|an|zurueck|schwelle <x>] (Folgen-Netz: Stand, Schatten-Funde, Version zurückdrehen), "
        "programm [<name> [tage] · stop <name> · status <name> · erlauben <name> <prozess>] (Programm-Absolvierung: die Wache lernt, welche Hintergrund-Prozesse ein Programm startet, und meldet danach Neues als R015 – oder der KI sagen „pass auf Steam auf“), "
        "netz [adresse|Meldung einfügen|warnen|schatten|aus|an|zurueck|register an|aus] (Netzwächter: wer steckt hinter einer Adresse – Name, Besitzer laut Adressregister, ob NemiCLI selbst sie nutzt; ohne Zusatz die Verbindungen nach draußen mit Ampel; ML + DL beginnen im Schattenmodus), "
        "ki [name] [stunden] · warnen|schatten|an|aus · ml warnen|schatten|an|aus (KI-Tracker: welche KIs aktiv waren – Claude Code, Codex, NemiCLI, "
        "Programme, die mit KI-Diensten sprechen –, ihre Zeitleiste aus den eigenen Protokollen und von außen gesehen, "
        "Alarme R019–R024; der Gegencheck R024 – Programm gestartet, das im Protokoll der KI fehlt – beginnt im "
        "Schattenmodus, scharf schaltest nur du; das KI-Modell ML-003 lernt je KI, was sie sonst tut, und meldet "
        "Ungewöhnliches – auch im Schattenmodus, bis du „ki ml warnen“ wählst), "
        "ml zurueck (Isolation Forest auf die vorige Version; nach jedem Zurückdrehen lernt das Modell erst nach deinem nächsten Training (Tray oder /wache training) wieder von selbst), "
        "start, stop, an, aus, autostart an|aus, kugel an|aus, reset. "
        "termin [status] (Auto-Vollscan: ein Fenster, in dem du Wochentage, Uhrzeit und „bei Systemstart“ wählst; "
        "der Plan liegt, wo die KI nicht hinkommt; geplante Scans zählen nicht gegen die Wochengrenze), "
        "fullscan [status|stop|termin] (startet von dir immer, unabhängig vom Zeitplan; „termin“ wie /wache termin): jede Festplatte mit Partitionen und Volumes (auch ohne "
        "Laufwerksbuchstaben, auch versteckte) als Ausgangsstand – jede Datei mit Attributen, für Programme "
        "und Skripte Hash, Signatur und Download-Herkunft (Änderungen zum letzten Lauf im Bericht), "
        "zusätzliche Datenströme, Verweise und nicht lesbare Ordner mit Grund; je Laufwerk Dateiarten und "
        "größte Ordner; dazu Autostart, Dienste, Aufgaben, Treiber und Netz mit Ampel; mit Windows-Build "
        "und Updates, fortsetzbar, das Modell lernt den Stand mit. Von Windows gesperrte Systembereiche "
        "sind Revier des Virenschutzes und bleiben zu. Die Schwebekugel schwebt "
        "auf dem Desktop, wenn kein Terminal offen ist – Klick öffnet ein kleines Chatfenster."),
    Cmd("/cve", "CVE-Liste ⚠: bekannte Schwachstellen als Gefahrenwissen",
        "[status · laden <ordner> [voll] · stopp · suchen <…>]",
        "⚠ Lädt die offizielle CVE-Liste (cvelistV5, entpackt, Ordner mit Unterordner cves) als Gefahrenwissen nach "
        "learned/gefahren.db – nie als harmlos oder normal, nie ins Gedächtnis. Import nur von Hand, im eigenen "
        "Prozess, abbrechbar (stopp); wiederholt liest er nur Neueres (deltaLog.json), voll liest alles neu. Danach "
        "prüft die Wache installierte Software (CVE-001: Version sicher betroffen, CVSS ab 7; je Programm ein Alarm, "
        "nie gedämpft, die KI kann ihn nie stumm schalten oder als harmlos schließen). Die KI liest die Liste nur "
        "mit cve_suchen, gerahmt als Daten. Ohne Zusatz: Stand. suchen <text|CVE-ID|produkt version>."),
    Cmd("/kompass", "Kompass 🧭: beim Tippen von / ein Fenster mit allen Befehlen daneben",
        "[an · aus]",
        "🧭 Ist der Kompass an, öffnet sich beim Tippen von / neben dem Terminal ein Fenster mit allen Befehlen. "
        "Tippst du weiter, leuchtet der passende Befehl auf, seine Unterpunkte stehen als Knöpfe darunter. Ein Klick "
        "setzt den Befehl in die Eingabe – ausgeführt wird erst mit Enter. Das Fenster nimmt dir die Tastatur nicht "
        "weg und verschwindet, sobald die Eingabe nicht mehr mit / beginnt. Ohne Zusatz: Stand."),
    Cmd("/zeitplan", "Zeitplan 📅: Aufträge, die NemiCLI zu festen Zeiten von selbst erledigt – im Fenster mit Kalender",
        "[liste]",
        "📅 Öffnet das Zeitplan-Fenster: links ein Kalender (Tage mit Läufen sind markiert, ein Klick zeigt, was dann "
        "startet) und die Aufträge, rechts Name, Auftrag und Zeit (täglich, wöchentlich, alle … Minuten/Stunden, "
        "einmal, bei Anmeldung). Speichern trägt den Auftrag in die Windows-Aufgabenplanung ein, Löschen nimmt ihn "
        "heraus; der Bericht jedes Laufs landet in Berichte/. Schlägt die KI einen Auftrag vor, geht das Fenster mit "
        "dem Vorschlag auf – eingetragen wird erst mit deinem Klick. 'liste' zeigt alles als Text."),
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
        "🟢 Reine Abfragen von der offenen Liste (Get-Process, nvidia-smi --query, git status …) laufen in jedem Modus "
        "ohne Frage; die Liste steht hier und in tools/shellfrei.py. "
        "✅ Sonst fragt `befehl` einmal, wenn das Kommando nur liest oder auf der Whitelist steht "
        "(git status, pip list …) – sonst zweimal, rot markiert. Im Modus Auto laufen dazu die "
        "Einträge der Auto-Liste und Python-Skripte im Arbeitsordner; alles andere hält an. "
        "'/whitelist git push' prüft einen Befehl. Die Liste liegt in befehl_whitelist.json im Programm-Ordner."),
    Cmd("/update", "Aktualisieren ⬇: venv prüfen – Lücken, fehlende Pakete, Updates", "",
        "⬇ Prüft das venv neben NemiCLI gegen requirements.txt und die Bild-Bausteine: fehlt etwas, "
        "passt eine Version nicht, stimmt der torch-Bau nicht zur Grafikkarte, gibt es erlaubte Updates? "
        "Dazu bekannte Sicherheitslücken und Schadpakete aller Pakete (OSV.dev) – behoben wird mit der "
        "ersten reparierten Version. Zeigt eine Liste und fragt; nach dem Installieren prüft ein Import-Test, "
        "ob alles lädt, sonst kommt die alte Version zurück. Mit Cloud-Modell kann die KI die Lücken erklären. "
        "Pakete, die gerade benutzt werden, kommen beim nächsten Start. 🔥 torch (NVIDIA): neuere Version oder "
        "höherer CUDA-Bau, den der Treiber kann, vom PyTorch-Index – eigener Menüpunkt, nur auf Wunsch; ist torch "
        "geladen, beim nächsten Start; danach Prüfung (lädt, Karte, rechnet, FP4), sonst alte Fassung zurück. "
        "Dazu prüft es den aktiven llama.cpp-Build gegen OSV.dev (Lücken im GGUF-Lesen zuerst). "
        "Im Quelltext-Modus mit Git-Remote vorher git pull – nur von einem https- oder SSH-Remote; mit "
        "update_signaturen_pruefen: true in nemicli.config.json nur signierte Commits."),
    Cmd("/code", "Coding-Assistent 🟦: Programmieren auf festem Weg mit Todo-Liste", "[aufgabe · status · frei · vorsicht]",
        "🟦 Startet den Coding-Assistenten (startet sonst auch von selbst bei Coding-Aufgaben): "
        "Auftragskarte → Recherche → Todo-Liste (🔴 offen · 🟠 Frage · 🟢 geprüft) → Punkt für Punkt "
        "abarbeiten. Grün setzt nur die Prüfung, nie die KI. Ablage im Projekt unter .nemicli/ "
        "(mit memory.md fürs Projekt-Gedächtnis). Dateiaktionen außerhalb des Projekts legt "
        "NemiCLI dir in jedem Modus zur Freigabe vor. "
        "Eigene Stufe, unabhängig vom Arbeitsmodus – im Coding schaltet Shift+Tab sie um, unten steht 🚀 frei bzw. "
        "🛡 vorsicht: frei (Vorgabe) – im Projekt arbeitet sie ohne "
        "Rückfrage und ohne feste Schrittgrenze durch (lesen, schreiben, Tests, Prüfskripte, pip ins venv, git außer "
        "push); fragen tut nur die Rote Liste (außerhalb des Projekts, ganze Ordner löschen, git push oder Verlauf "
        "umschreiben, Downloads, Zeitpläne, Installieren für den ganzen Rechner); dreht sie sich im Kreis, hält sie "
        "an. vorsicht – fragt wie der Arbeitsmodus. Ihre Werkstatt ist .nemicli/ im Projekt (tests/, "
        "probe/, cache/ – auch der Python-Cache landet dort). Fenster-Programme sieht sie sich mit app_ansehen an: "
        "Start auf einem eigenen, unsichtbaren Desktop, Foto nur vom Fenster – bei dir geht nichts auf. "
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
        "KI-Modelle/KI-Vision/Sehmodell/<Ordner>/ (.gguf + mmproj-*.gguf) hin und gibt ihm die Beschreibung als Text – das Bild selbst "
        "geht nirgendwohin. Je Modell ein eigener Ordner; '/vision <ordner>' wählt dauerhaft, ohne Zusatz: "
        "Liste und Stand. Nach 2 Minuten ohne Bild wird er ganz entladen. 'restart': entladen und neu laden. "
        "'debug': Fehlerbericht nach Debugbericht/vision_bericht.txt. 'protokoll': welche Bilder beschrieben "
        "wurden – je Persönlichkeit; dasselbe Bild mit derselben Frage kommt von dort, ohne neu zu laden."),
    Cmd("/voice", "Stimme 🔊: an (jede Antwort) · knopf (nur auf Anfrage) · aus · jetzt · text",
        "[an|knopf|aus|jetzt|text]",
        "🔊 Stimme der Persönlichkeit (Qwen3-TTS unter KI-Modelle/KI-Stimme/Stimmmodell/). 'an': jede Antwort wird vorgelesen, auf dem "
        "Bildschirm steht nur der Anfang. 'knopf' (Vorgabe): spricht nur auf Anfrage – '/voice jetzt' "
        "oder 🔊 unter einer Antwort im /gui-Fenster liest die Antwort einmal vor – und gibt den Grafikspeicher danach sofort frei. 'aus': bleibt aus, bis du "
        "wieder einschaltest; nichts lädt, auch nicht auf Wunsch der KI. '/voice text' zeigt die letzte Antwort "
        "ganz, Esc stoppt das Sprechen."),
    Cmd("/web", "Web-Allowlist zeigen/filtern · key = Ollama-Suche · fremdseiten frei|fragen|aus · schutz",
        "[wort · key · fremdseiten frei|fragen|aus · schutz an|aus|aktualisieren · lokal [weg] <port>]",
        "🌐 Erlaubte Internet-Seiten nach Vertrauens-Tier. '/web python' filtert, "
        "'/web key' trägt den Ollama-API-Key für die Web-Suche ein. '/web fremdseiten frei|fragen|aus': "
        "Seiten außerhalb der Allowlist über Ollama lesen (als unbekannte Seite, niedrigstes Vertrauen) – frei "
        "(Vorgabe, aus Suchtreffern ohne Rückfrage), fragen (jede vorher) oder aus. '/web schutz': Schutzliste "
        "bekannter Schad- und Phishing-Adressen (Sicherheitslisten von uBlock Origin) – solche Seiten werden nie "
        "gelesen, Suchtreffer und Anhänge mit ihnen fehlen; erneuert sich täglich, 'aktualisieren' sofort; einmal gesperrt, immer gesperrt, jede exe bringt eine Momentaufnahme mit. "
        "'/web lokal': Netz-Zwang – ins Netz nur https://, unverschlüsselt nur zu 127.0.0.1 auf freigegebenen "
        "Ports (Ollama, LM Studio, ComfyUI …); '/web lokal 5000' gibt einen Port frei, 'weg 5000' nimmt ihn "
        "zurück. Die KI schreibt für lokale Dienste das Codewort lokal:PORT."),
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
    Cmd("/einstellungen", "⚙ Einstellungen: die wichtigsten Schalter auf einen Blick · Für Profis", "",
        "⚙ Dieselbe Seite wie im Desktop-Fenster, hier im Terminal: vorne Stimme, Arbeitsweise, Nachdenken, Farben, "
        "Schriftgröße, Startart und Kugel; unter „🔧 Für Profis“ der Rest. Erst den Punkt wählen, dann den Wert "
        "(✔️ = so ist es gerade)."),
    Cmd("/schriftgröße", "Schrift größer oder kleiner 🔠: ganze Oberfläche · KI · du · Terminal (in px)",
        "[oberflaeche|ki|du|terminal] <px> · zurück",
        "🔠 Ohne Zusatz: erst die Rolle wählen, dann die Größe (✔️ = so ist es gerade). Rollen: 🪟 oberflaeche "
        "(die ganzen Fenster, Vorgabe 14 px), 🤖 ki (nur die Antworten der KI, Vorgabe 15 px), 💬 du (deine "
        "Nachrichten, Vorgabe 15 px), 🖥 terminal (das eigene Terminal-Fenster, Vorgabe 15 px, ab dem nächsten "
        "Start). Beispiele: '/schriftgröße ki 18' – nur die KI größer; '/schriftgröße 16' – alles größer; "
        "'/schriftgröße zurück' – alles auf die Vorgabe. Gilt in Desktop-Fenster, Kugel, Charakter-, Musik- und "
        "Store-Fenster; neue Nachrichten sofort, schon offene Fenster beim nächsten Öffnen."),
    Cmd("/schriftgroesse", "Schriftgröße (ohne Umlaut getippt)", "[rolle] <px>"),
    Cmd("/terminal", "Eigenes Terminal-Fenster 🖥: Leuchten, weiches Rollen, geteilte Ansicht, Figur",
        "[leuchten|weich|geteilt|figur an|aus · thema auto|breeze|mint · tempo 30|60]",
        "🖥 Schalter für das eigene Terminal-Fenster (NemiCLI.exe). leuchten: sanftes Leuchten im Hintergrund, das "
        "atmet, solange die KI arbeitet, und kurz aufleuchtet, wenn sie fertig ist. weich: weiches Rollen und gleitender Cursor. geteilt: der Reiter „KI“ steht "
        "rechts neben dem Chat (auch Strg+Umschalt+G). figur: die aktive Persönlichkeit sitzt rechts auf der "
        "Eingabe und zeigt, was gerade passiert (sitzt, winkt, denkt nach, prüft, arbeitet, freut sich, Herz, "
        "grinst, legt den Kopf schief, döst nach 10 Minuten Ruhe); bewegt sie sich (/bewegung), steht sie rechts oben "
        "auf dem Rahmen der Eingabe und NemiCLI behält die volle Breite, sonst bleiben für sie rechts ein paar Spalten "
        "frei; unter 80 Spalten Text ist sie weg. Bilder: ganz und sitzend nach /kugel malen figur, sonst ihre "
        "Kugel-Bilder. Diese vier wirken sofort. thema: auto nimmt die Farben von "
        "/theme, breeze und mint sind feste Farben – ab dem nächsten Start. tempo: Bilder je Sekunde der "
        "Oberfläche im Fenster (30 sparsam, Vorgabe; 60 flüssig) – ab dem nächsten Start oder /restart. Das Fenster "
        "malt nur geänderte Zeilen neu und ruht versteckt, minimiert oder im Tray."),
    Cmd("/startart", "Wie NemiCLI startet 🚀: im Terminal oder gleich im Desktop-Fenster", "terminal · fenster",
        "🚀 terminal (Vorgabe): wie bisher. fenster: beim Start öffnet sich das Desktop-Fenster (/gui) von selbst, "
        "das Terminal wartet im Tray. Gilt ab dem nächsten Start."),
    Cmd("/kern", "Kern 🧬: das feste Aussehen und die Sprechweise der Figur ansehen, ändern, sperren (nur du)",
        "[aendern · kopf · sprechweise · sperren · entsperren · zurueck]",
        "🧬 Der Kern ist das Grundskelett der Figur: Gesicht, Augen, Haarfarbe und -länge, Haut, Körperbau, Bleibendes wie "
        "Tattoos. Er liegt in einer eigenen Datei neben der Charakter-Datei (mit Sicherung) und steht vor JEDEM "
        "Selbstbild – bei bild_malen, Serien, Comics und /kugel malen, mit Bild-LoRA zusammen mit ihrem [name]. "
        "Kleidung, Frisur, Make-up, Pose und Szene bleiben je Bild frei. 🔒 Gesperrt ändert ihn nur der Nutzer: Will "
        "eine KI ihn mit charakter_aendern ändern, fragt NemiCLI in jedem Modus und zeigt alt → neu. ⏪ holt einen der "
        "letzten fünf Stände zurück. ✏️ aendern öffnet das Kern-Fenster (ganzer Stand als JSON, Speichern ohne KI). "
        "🙂 kopf öffnet das Kopf-Fenster: Wünsche auf Deutsch, die KI schreibt Gesichts-Varianten (Haare und Körper "
        "bleiben), Krea malt sie, ein Klick übernimmt Kern und Seed. 🗣 Die Sprechweise (wie die Figur redet, z. B. frech, duzt, kurze Sätze) gehört "
        "dazu, ist mit dem Kern gesperrt und geht mit jeder Nachricht an die KI. Diesen Befehl nutzt nur der Nutzer "
        "selbst; eine KI kann ihn nicht aufrufen."),
    Cmd("/lora", "LoRA-Werkstatt 🧬: aus eigenen Bildern eine Bild-LoRA (Krea 2) trainieren und an einen Charakter binden",
        "",
        "🧬 Fenster: Bilder hineinziehen oder wählen, 🔍 prüfen (doppelt, unscharf, kein oder mehrere Gesichter – "
        "mit Seh-Modell), auswählen, Namen geben, trainieren mit Fortschritt in Prozent, Schritt und Restzeit; "
        "⏸ Pause sichert den Stand, ▶ Weiter macht dort weiter. Mit Seh-Modell bekommt jedes Bild vorher einen Satz "
        "(Kleidung, Pose, Ort, Licht – texte/<kennung>.txt, von Hand änderbar): so lernt der Name nur die Person, "
        "nicht Kleidung und Hintergrund. Hoch-, Quer- und quadratische Bilder lernen in ihrem Format statt "
        "beschnitten. Wahlweise ein Testbild alle 200 Schritte und danach "
        "Probebilder bei Stärke 0,6 · 0,8 · 1,0. Ablage LoRA/Bild/<name>/ im Daten-Ordner, die Originalbilder bleiben "
        "unberührt. Binden an einen Charakter schreibt die SHA-256 in seine Charakter-Datei (lora_bild): malt er sich "
        "selbst, kommt die LoRA immer zu seinem Kern dazu (Kern · [name] · was passiert); fehlt sie oder hat sie sich "
        "verändert, gilt nur der Kern. Im Prompt: [name] Szene – [name] wählt die LoRA, der Rest sagt nur, was passiert. "
        "Nur Bilder mit Krea 2; Video kommt später."),
    Cmd("/musik", "Musik 🎵: Fenster für Songwünsche – die KI arbeitet Stil und Liedtext aus, dann komponieren",
        "",
        "🎵 Wunsch auf Deutsch eintippen (Länge, mit oder ohne Gesang). ✨ Ausarbeiten: die KI macht daraus einen "
        "englischen Stil und einen Liedtext mit [Verse]/[Chorus]-Marken – beides lässt sich ändern. 🎵 Komponieren: "
        "der Musik-Motor (ACE-Step 1.5, KI-Modelle/KI-Musik/Musikmodell) rechnet den Song als WAV nach Profile/<Name>/Musik/. "
        "🎙 Als Cover: den markierten Song (oder eine eigene Datei: WAV, MP3, M4A, FLAC) im Stil oben neu "
        "einspielen – Melodie und Aufbau bleiben, „Nähe“ hält auch den Klang näher am Original. "
        "Ohne Fenster: einfach der KI sagen, welchen Song sie machen oder covern soll."),
    Cmd("/webui", "Browser-Oberfläche öffnen (gut lesbar, mit Vorlesen) – das Desktop-Fenster /gui kann das auch",
        "", "Die ältere Oberfläche im Browser. Ihre Stärken hat jetzt auch das Desktop-Fenster (/gui): gut lesbare, "
        "größere Schrift mit /schriftgröße, Vorlesen per 🔊 an jeder Antwort, dazu Galerie, Persönlichkeiten und ⚙."),
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
    Cmd("/emoji", "Wie viele Emojis die KI nimmt · Kürzel-Liste", "low · middle · strong · frei · kuerzel",
        "low: selten, höchstens eins · middle: ab und zu · strong: gerne und lebendig, aber passend · "
        "frei (Vorgabe): die Persönlichkeit entscheidet. kuerzel: Smileys & :kürzel: zum Tippen."),
    Cmd("/nemi", "Maskottchen sagt was 💬"),
    Cmd("/eigenes", "Eigener Bereich 🗝️: wo die KI ihre Notizen und Ideen ablegt – Stand, versiegelt, freigeben (nur du)",
        "[oeffnen · freigeben]",
        "🗝️ Jede Persönlichkeit hat unter Profile/<Name>/Eigenes/ einen Bereich nur für sich: Dort legt die KI ohne "
        "Rückfrage Notizen, Ideen, Pläne und Entwürfe in eigenen Ordnern ab. Jeder Ordner, den sie mit ihren "
        "Werkzeugen anlegt, bekommt ein Siegel (HMAC-SHA256 über Pfad und Kennung, mit einem Schlüssel je "
        "Persönlichkeit, per Windows DPAPI an das Konto gebunden). Ordner ohne gültiges Siegel – von außen angelegt, "
        "hineingeschoben oder mit kopiertem Siegel – sind für die KI gesperrt und unsichtbar. Ohne Zusatz: Ort, "
        "Stand und gesperrte Ordner. oeffnen: im Explorer. freigeben: nach eigenem Nachsehen gesperrte Ordner "
        "versiegeln. Grenzen: 6 Ordner tief, 500 MB. Diesen Befehl nutzt nur der Nutzer selbst."),
    Cmd("/uebung", "Übungsmodus: im Leerlauf selbst üben & lernen 🎓", "an · aus · jetzt",
        "🎓 Übungsmodus: im Leerlauf (~10 min) baut & testet NemiCLI Code "
        "in NemiSandbox/ und lernt daraus. 'aus' bleibt gespeichert – auch nach Neustart"),
    Cmd("/kontext", "Kontext 📊: wie voll (Anleitung, Gedächtnis, Skills, Nachrichten) · lokal: max (Vorgabe) "
        "· 8k … 128k",
        "[anzeigen|8k|16k|32k|64k|128k|max]",
        "📊 Zeigt, womit der Kontext gefüllt ist – wie /context bei Claude Code. Beim lokalen Modell "
        "stellt es außerdem ein, wie viele Token es hält (Vorgabe max: so viel, wie das Modell kann – "
        "mit 🦙 so viel, wie in den Grafikspeicher passt). Ist er nach einer Antwort zu drei "
        "Vierteln voll, räumt NemiCLI im Hintergrund auf (Doppelpuffer, Preview; eigener Motor und lokales "
        "Ollama): die KI schreibt einen „Stand des Gesprächs“ (Ziel, Wünsche, Erledigtes, Fehler, Offenes, "
        "nächster Schritt, Fakten – dazu deine Nachrichten wörtlich), die ältesten Nachrichten wandern ins "
        "Gedächtnis und bleiben auffindbar, die neuesten bleiben. Der Stand wird mit dem Chat gespeichert. "
        "Bei Ollama gilt der Kontext, mit dem Ollama das Modell geladen hat. "
        "Alles andere entscheidet NemiCLI beim Laden selbst, damit der eingestellte Kontext ganz gilt: "
        "passt alles in die Grafikkarte, bleibt es dort; sonst wird erst der Gesprächsspeicher kleiner: 8 Bit "
        "(halber Platz), 6 Bit, 4 Bit (gut ein Viertel, Werte vorher gedreht), dann wandern Schichten in den RAM (Hybrid, über PCIe, langsamer) – bei MoE-Modellen bleiben "
        "die meistgebrauchten Experten in der Grafikkarte (Experten-Lager, lernt im Gespräch mit), zuletzt ein "
        "Teil des Gesprächsspeichers. Die Anzeige zeigt, was gewählt wurde."),
    Cmd("/charaktere", "Charakter-Galerie 🎭: fertige Persönlichkeiten übernehmen oder eigene erschaffen", "",
        "🎭 Eigenes Fenster: 9 fertige Persönlichkeiten mit Porträt zum Ansehen und Übernehmen – oder eine eigene erschaffen, auf zwei Wegen. 💬 Frei mit der KI: erzählen, wie man ihn sich vorstellt, ganz in eigenen Worten ohne Auswahllisten; die KI fragt locker nach und schreibt einen Steckbrief (Aussehen, Wesen, Eigenheiten & Stil) mit, den man selbst ändern kann – daraus 6 Varianten. ⚡ Schnell: Geschlecht (weiblich, männlich, nichtbinär) und Alter (ab 18, getippt), Aussehen (Haare mit Farbwahl, Augenfarbe, Busen/Brust, Taille, Po; die Körperform wird errechnet und lässt sich ändern), bis zu 8 Eigenschaften und eigene Wünsche; die KI schreibt 6 Varianten mit genau diesem Aussehen, Krea malt je ein Porträt. Schritt-Leiste oben, rechts der Steckbrief; 💬 „Mit der KI reden“: erzählen, wie man sich den Charakter vorstellt – die KI stellt die Felder ein, alles bleibt per Klick änderbar, ↺ nimmt ihre letzte Änderung zurück. Beim Ansehen hilft ✨ die KI, ein Feld zu verbessern (Wesen, Aussehen, Kleidung, Stil), 🎨 malt neu (gleiches oder neues Gesicht), 👗 ändert die Angaben zum Aussehen. Zum Schluss schlägt die KI 6 Namen vor (🎲 neue) – oder ein eigener. Übernehmen legt die Persönlichkeit an, dazu im Profil die Charakter-Datei (Aussehen, fester Seed) und das Porträt als Profilbild – und macht sie aktiv. „Meinen Charakter verbessern“ öffnet die Charakter-Datei der aktiven Persönlichkeit (auch die Angaben zum Aussehen). Auch als /galerie."),
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
    Cmd("/zip", "Archive entpacken 📦: an (fragt) · auto · aus · <pfad> entpackt selbst",
        "[an · auto · aus · <pfad zum archiv>]",
        "📦 Entpackt Archive (zip, 7z, rar, tar …) in einen neuen Ordner, sortiert nach Art und prüft jede Datei "
        "(Größe, CRC32, SHA-256) – in einem eigenen Fenster mit Ladebalken und Abbrechen. Aus dem Entpackten startet "
        "nichts. Ein Archiv direkt im Profilordner der aktiven Persönlichkeit (Profile/<Name>/) bemerkt NemiCLI vor "
        "der nächsten Antwort: an = fragt „Entpacken?“ · auto = entpackt sofort · aus = nichts. Entpacktes aus dem "
        "Profilordner kommt ins Gedächtnis. Die KI kann mit dem Werkzeug entpacken vorschlagen, ein Archiv zu "
        "entpacken – das fragt in jedem Modus, geht bei aus gar nicht und nur im Gespräch. '/zip <pfad>' entpackt "
        "ein Archiv nach Entpackt/ im Daten-Ordner. Ohne Zusatz: Stand."),
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

# Sechs Themen für das /-Menü und /help statt einer langen Liste. Ein Befehl ohne Thema landet unter „🔧 System“;
# VERSTECKT erscheint nur beim Tippen des Namens (Gegenstück eines anderen Befehls).
THEMEN: list[tuple[str, str, tuple[str, ...]]] = [
    ("💬", "Chat & Bedienung", ("/help", "/kompass", "/einstellungen", "/gui", "/schriftgröße", "/startart", "/terminal", "/modus", "/voice", "/theme",
                                "/anzeige", "/emoji", "/reset", "/compact", "/kontext", "/clear", "/undo", "/restart",
                                "/exit")),
    ("🧠", "Modell & Gedächtnis", ("/model", "/llamacpp", "/staerke", "/denken", "/auto", "/vision", "/gedaechtnis",
                                   "/einlesen", "/embeddings", "/reflektieren", "/aufraeumen", "/wissen", "/skills",
                                   "/subagenten", "/uebung")),
    ("🎨", "Erschaffen", ("/bild", "/bildmodel", "/lora", "/kern", "/video", "/musik", "/appstore", "/spiel", "/code")),
    ("👤", "Persönlichkeit", ("/persönlichkeiten", "/charaktere", "/charakter", "/kugel", "/bewegung", "/name",
                              "/eigenes", "/nemi")),
    ("🛡️", "Sicherheit", ("/wache", "/cve", "/web", "/whitelist", "/limit", "/schluessel", "/sandbox", "/backup", "/zip")),
    ("🔧", "System", ("/update", "/zeitplan", "/einrichten", "/systemcheck", "/selbsttest", "/venv", "/start", "/statistik",
                     "/version", "/protokoll", "/debug", "/doku", "/ml", "/ordner", "/chrome", "/kontextmenue",
                     "/webui")),
]
VERSTECKT = frozenset({"/codeend", "/schriftgroesse"})


def thema_von(name: str) -> tuple[str, str]:
    """(Zeichen, Titel) des Themas eines Befehls."""
    for zeichen, titel, namen in THEMEN:
        if name in namen:
            return zeichen, titel
    return THEMEN[-1][0], THEMEN[-1][1]


def nach_themen() -> list[tuple[str, str, list[Cmd]]]:
    """Alle sichtbaren Befehle, nach Thema geordnet: (Zeichen, Titel, Befehle)."""
    nach_name = {c.name: c for c in COMMAND_LIST}
    gruppen = [(z, t, [nach_name[n] for n in namen if n in nach_name]) for z, t, namen in THEMEN]
    vergeben = {n for _, _, namen in THEMEN for n in namen} | VERSTECKT
    rest = [c for c in COMMAND_LIST if c.name not in vergeben]
    if rest:
        z, t, befehle = gruppen[-1]
        gruppen[-1] = (z, t, befehle + rest)
    return gruppen

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
        ("termin", "📅 Auto-Vollscan: Tage und Uhrzeit wählen", None),
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
        ("ki", "🤖 KI-Tracker: welche KI was getan hat", None),
        ("autostart", "🚀 Autostart", None),
        ("selbsttest", "🧪 Selbsttest", None),
    ],
    "/cve": [
        ("status", "⚠ Stand: Einträge, letzter Import, betroffene Programme", None),
        ("laden {}", "📥 CVE-Liste laden (cvelistV5-Ordner)", "Welcher Ordner? (z. B. C:\\daten\\cvelistV5)"),
        ("laden {} voll", "📥 Alles neu einlesen", "Welcher Ordner?"),
        ("stopp", "⏹ Import anhalten", None),
        ("suchen {}", "🔎 Suchen", "Was suchen? (Text, CVE-ID oder „produkt version“)"),
    ],
    "/llamacpp": [
        ("", "🦙 Stand: aktiver Build, eingerichtete Builds", None),
        ("aktualisieren", "⬆ Aktualisieren: neuester Build von GitHub, geprüft", None),
        ("wechseln {}", "↶ Build wechseln", "Welcher Build? (z. B. b12199)"),
        ("holen", "📦 Holen: Paket für diese Grafikkarte", None),
        ("einrichten", "🔧 Einrichten: Pakete in der Ablage prüfen", None),
        ("vergleich", "⚖ Motorvergleich", None),
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
        ("selbst", "🪞 Selbstentwurf ansehen", None),
        ("selbst verlauf", "📜 Selbstentwurf: frühere Stände", None),
        ("selbst zurueck", "↶ Selbstentwurf: alten Stand zurück", None),
        ("selbst an", "Selbstentwurf im Takt: an (morgens, mittags, abends)", None),
        ("selbst aus", "Selbstentwurf im Takt: aus", None),
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
        ("schutz", "⛔ Schutzliste: Stand", None),
        ("schutz aktualisieren", "⛔ Schutzliste jetzt neu laden", None),
        ("lokal", "🔒 Netz-Zwang: freigegebene lokale Ports", None),
        ("schutz an", "An", None),
        ("schutz aus", "Aus", None),
    ],
    "/voice": [
        ("", "🔊 Status", None),
        ("an", "An – jede Antwort hören", None),
        ("knopf", "Knopf – nur auf Anfrage, danach sofort entladen", None),
        ("aus", "Aus – bleibt aus, nichts lädt", None),
        ("jetzt", "🔊 Letzte Antwort einmal vorlesen", None),
        ("text", "📄 Letzte Antwort ganz lesen", None),
    ],
    "/zip": [
        ("", "📦 Stand: Modus, Zielordner", None),
        ("an", "An – fragt „Entpacken?“", None),
        ("auto", "Auto – Archive im Profilordner sofort entpacken", None),
        ("aus", "Aus – nichts entpacken", None),
        ("{}", "📂 Archiv entpacken …", "Pfad zum Archiv?"),
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
        ("gesicht an", "✨ Gesichts-Korrektur an (Gesichter nach dem Malen vergrößert nachmalen)", None),
        ("gesicht aus", "✨ Gesichts-Korrektur aus", None),
    ],
    "/doku": [
        ("", "📚 Was ist geladen?", None),
        ("laden python", "🐍 Python-Doku laden (~4 MB)", None),
        ("laden mdn", "🌐 MDN laden: HTML, CSS, JavaScript, Web-APIs (großer Download)", None),
    ],
    "/eigenes": [
        ("", "🗝️ Stand: Ort, versiegelt, gesperrte Ordner", None),
        ("oeffnen", "📂 Im Explorer öffnen", None),
        ("freigeben", "🔓 Gesperrte Ordner nach Prüfung freigeben", None),
    ],
    "/kompass": [
        ("an", "🧭 Kompass einschalten", None),
        ("aus", "⏸ Kompass ausschalten", None),
    ],
    "/zeitplan": [
        ("", "📅 Zeitplan-Fenster öffnen", None),
        ("liste", "📋 Alle Aufträge als Text", None),
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
    "/terminal": [
        ("leuchten an", "✨ Leuchten im Hintergrund an", None),
        ("leuchten aus", "✨ Leuchten im Hintergrund aus", None),
        ("weich an", "🌊 Weiches Rollen an", None),
        ("weich aus", "🌊 Weiches Rollen aus", None),
        ("geteilt an", "🪟 Reiter „KI“ neben dem Chat an", None),
        ("geteilt aus", "🪟 Reiter „KI“ neben dem Chat aus", None),
        ("figur an", "🐾 Figur der Persönlichkeit an", None),
        ("figur aus", "🐾 Figur der Persönlichkeit aus", None),
        ("thema auto", "🎨 Farben von /theme", None),
        ("thema breeze", "🎨 Feste Farben: Breeze", None),
        ("thema mint", "🎨 Feste Farben: Mint", None),
        ("tempo 30", "🎞 Bildrate 30 (sparsam)", None),
        ("tempo 60", "🎞 Bildrate 60 (flüssig)", None),
    ],
    "/kugel": [
        ("", "🔮 Status", None),
        ("malen {}", "🎨 Gesicht malen lassen", "Wie soll sie aussehen? (leer = aus der Charakter-Datei, sonst beschreibt sie sich selbst)"),
        ("motive", "🖼 Motive: 20 Bereiche, was schon gemalt ist", None),
        ("motive {}", "🎨 Motive malen", "Bereich (Nummer oder Name) oder alle"),
        ("malen figur", "🐾 Figur fürs Terminal malen (beweglicher Körper vorn/hinten + 10 Posen)", None),
        ("auto an", "✨ AUTO an", None),
        ("auto aus", "✨ AUTO aus", None),
        ("vorne an", "📌 Immer vorne an", None),
        ("vorne aus", "📌 Immer vorne aus", None),
        ("weg", "⚪ Zurück zur Kugel", None),
        ("ordner", "📁 Ordner öffnen", None),
    ],
    "/code": [
        ("status", "🟨 Todo-Liste zeigen", None),
        ("{}", "🟦 Mit Aufgabe starten", "Was soll gebaut werden?"),
        ("frei", "🚀 Freigabe frei: im Projekt durcharbeiten", None),
        ("vorsicht", "🛡 Freigabe vorsicht: fragt wie der Arbeitsmodus", None),
        ("end", "Coding-Assistent beenden", None),
    ],
    "/schluessel": [
        ("{}", "🔑 Programm-Ordner auf Zeit öffnen", "Wie viele Minuten? (danach optional die Aufgabe)"),
        ("aus", "🔒 Schlüssel abziehen", None),
    ],
    "/anzeige": [
        ("kompakt", "● Kompakt (Werkzeug-Zeile, ⎿ Ergebnis, Diff)", None),
        ("rahmen", "▢ Mit Rahmen (bisherige Kästen)", None),
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


def help_gruppen() -> list[tuple[str, list[tuple[str, str]]]]:
    """/help nach Themen: (Überschrift, [(Befehl mit Argument, Kurztext)])."""
    return [(f"{z} {t}", [(c.usage, c.short) for c in befehle]) for z, t, befehle in nach_themen()]


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
    if not q:                                    # nur „/“: nach Themen geordnet, das Thema vorne im Kurztext
        return [(c.name, f"{z} {c.short}") for z, _t, befehle in nach_themen() for c in befehle]
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


_SUCH_LISTE: list | None = None
_BEKANNT = frozenset(c.name for c in COMMAND_LIST) | frozenset(UNTERMENUES) | frozenset(PERSONA_CMDS)


def _such_liste() -> list:
    """Alle Befehle und ihre Unterpunkte für die /-Suche (feste Menüs, einmal aufgebaut)."""
    global _SUCH_LISTE
    if _SUCH_LISTE is None:
        try:
            import palette
            _SUCH_LISTE = palette.eintraege(auswahl=lambda n: UNTERMENUES.get(n, []))
        except Exception:
            _SUCH_LISTE = []
    return _SUCH_LISTE


def vorschlaege(text: str, n: int = 12) -> list[tuple[str, str]]:
    """/-Suche: erst die Befehle selbst (find_commands), dann Unterpunkte aller Befehle über die Suche der
    Palette – „/fullscan“ findet „/wache fullscan“, „/wachter fullscann“ auch; mehrere Wörter und Tippfehler
    gehen. Liefert (einzusetzender Befehl, Beschreibung)."""
    frage = text.strip()
    raus = find_commands(frage) if " " not in frage else []
    if not frage.lstrip("/"):
        return raus
    try:
        import palette
        treffer = palette.suchen(_such_liste(), frage, n=max(n, 40))   # breit suchen, unten wieder kürzen
    except Exception:
        treffer = []
    # Ein Unterpunkt, der genau so heißt wie das Getippte („/termin“ → „/wache termin“), kommt gleich nach
    # dem gleichnamigen Befehl – vor Befehlen, die nur so anfangen („/terminal“).
    wort = _fold(frage.lstrip("/"))
    genau = [e for e in treffer if " " not in wort and len(e.zeile.split()) > 1
             and _fold(e.zeile.split()[1]) == wort]
    if genau:
        vorn = [r for r in raus if _fold(r[0].lstrip("/")) == wort]
        raus = vorn + [(e.befehl, e.beschreibung) for e in genau] + [r for r in raus if r not in vorn]
    gesehen = {b.strip() for b, _ in raus}
    dazu = 0
    for e in treffer:
        if e.befehl.strip() in gesehen or dazu >= n:
            continue
        gesehen.add(e.befehl.strip())
        raus.append((e.befehl, e.beschreibung))
        dazu += 1
    return raus


TASTEN = ("Enter senden · Strg+J neue Zeile · Esc abbrechen · F2 Denktext · F8 Änderung freigeben · "
          "F12 Gespräch sichern · Shift+Tab Modus · Tab/↑↓ im /-Menü (/ + Wort findet auch Unterpunkte, z. B. /fullscan → /wache fullscan) · Strg+C markierten Text kopieren · "
          "Strg+K Suche (alle Befehle, Einstellungen, Modelle; Tippfehler okay) · F3 Cockpit (Grafikkarte, Tempo, "
          "Kontext, Hintergrund-Aufgaben, Todo) · Klick auf ✓/⚙-Zeilen klappt Werkzeug-Ergebnisse auf · "
          "Strg+F im Verlauf suchen (Enter/↑ älterer Treffer, ↓ neuerer, Esc schließt) · Rechtsklick auf einen "
          "Block kopiert ihn ganz · Klick auf die Titelzeile einer Antwort klappt sie ein und auf · "
          "Alt+↑/↓ von Antwort zu Antwort · Bild↑/↓ blättert")

EINSTELLUNGEN = """\
## Einstellungen und Dateien
Selbst ändern (nur auf direkten Wunsch, der Nutzer bestätigt): einstellung_aendern mit
was "internet_erlauben" oder "internet_sperren" und wert = Domain. Eigene Einträge liegen in
allowlist_eigen.json im Programm-Ordner, die mitgelieferte Liste in allowlist.json; /web zeigt alle.
Gefällt kein Farbschema: mit theme_erstellen ein eigenes entwerfen (themes_eigen.json im Programm-Ordner),
danach /theme mit menue_oeffnen öffnen.
Nur der Nutzer selbst – nenne ihm den Weg:
- Im Terminal mit /einstellungen oder im Desktop-Fenster (/gui) unter ⚙ Einstellungen per Klick: Stimme,
  Arbeitsweise, Denkstufe, Farben, Schriftgröße je Rolle (/schriftgröße), Start im Terminal oder Fenster (/startart),
  Terminal-Fenster (/terminal: Leuchten, weiches Rollen, geteilte Ansicht, Farben); unter
  „Für Profis“ Kontext, Gedächtnis-Modell, Gedächtnis-Takt, Reflektieren, Skills, Übung, Helfer, Fremdseiten, Kugel,
  Rechtsklick-Menü, Debugbericht
- Modell, Cloud-Key: /model → „Cloud-Anbieter hinzufügen“ (Key wird verschlüsselt gespeichert); ChatGPT-Konto
  (Plus/Pro) ohne Key: dort „ChatGPT-Konto“ oder /model chatgpt – Anmeldung im Browser, Abmelden mit
  /model chatgpt abmelden. Ist das Plan-Kontingent aufgebraucht, sagt NemiCLI das; Bilder malt weiter Krea 2
- Lösch-Limit /limit · Befehls-Whitelist /whitelist (befehl_whitelist.json) · Projekt für die
  Sandbox freigeben /sandbox · Programm-Ordner auf Zeit öffnen /schluessel · Sperrliste
  schreibsperre.json im Programm-Ordner (nur von Hand)
- Daten-Ordner wählen /start: chats, Bilder, Persoenlichkeiten, Skills, Papierkorb (/undo),
  Protokoll learned/aktionen.log
- Modellordner: KI-Modelle/ im Programm-Ordner (Config modelle_ordner setzt einen anderen Ort), ein Bereich je Art
  (KI-Chat, KI-Vision, KI-Bild, KI-Video, KI-Stimme, KI-Musik, KI-Gedaechtnis, KI-Portraet, KI-Bewegung, KI-Helfer),
  darin ein Ordner je Modell, je Bereich eine Anleitung.txt; frühere Orte (ModelGGUF/, Vision/, Models/…) gelten weiter
- Lokales Modell holen: /model → „Modell von Hugging Face holen“ (oder /model huggingface) –
  Größe und MoE wählen, landet in KI-Modelle/KI-Chat/Chatmodell/<Name>/; danach /model → Lokal.
  Modell-Familie: KI-Modelle/KI-Chat/Chatmodell/<Familie>/<Mitglied>/ (Ordner ohne eigene .gguf) erscheint in /model → Lokal
  als ein Team (🧬): es chattet das Mitglied ohne Bild-Teil, bei Bildern sieht das Mitglied mit Bild-Teil
  (der Seher wird nur dafür geladen); jedes Mitglied ist auch einzeln wählbar („Familie-Mitglied“).
  Gedächtnis-Modelle: KI-Modelle/KI-Gedaechtnis/Suchmodell (/embeddings); eingebettet wird gesammelt auf der GPU: /einlesen (Balken 100k);
  Config embedding_vram_anteil (Prozent VRAM für den Bibliothekar, Vorgabe 25)
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
  Strg+Umschalt+G stellt ihn rechts neben den Chat (geteilte Ansicht, bleibt gemerkt). Im Reiter „KI“ und in
  der Kommandozeile sucht Strg+F (Treffer als Marken am rechten Rand); der Reiter „KI“ rollt weich mit
  Mausrad, Bild↑/↓, Pos1/Ende. Strg+Klick öffnet Web-Adressen und vorhandene Pfade aus dem Text – nur
  https; Ordner, Bilder, Text, PDF und Medien gehen auf, alles andere wird nur im Ordner gezeigt.
  Schalter mit /terminal (auch auf ⚙): leuchten, weich, geteilt an|aus wirken sofort, thema auto|breeze|mint
  ab dem nächsten Start. Nur in der Config: terminal_ki_reiter false (Reiter „KI“ aus), terminal_hintergrund
  (Durchsicht 0.3–1.0), terminal_schrift, terminal_groesse – ab dem nächsten Start"""


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
# /compact und /uebung brauchen TURN_LOCK, den der Zug der KI selbst hält (asyncio.Lock ist nicht
# wiedereintrittsfähig).
MENUE_GESPERRT = {"/schluessel", "/schlüssel", "/limit", "/whitelist", "/sandbox", "/modus", "/auto",
                  "/wache", "/code", "/compact", "/uebung", "/restart", "/neustart", "/kern", "/eigenes", "/zip",
                  "/cve"}
# /appstore öffnet nur das Store-Fenster – Bauen, Entfernen und debugAI löst dort der Nutzer selbst aus.
MENUE_EIGENE = {"/theme", "/emoji", "/staerke", "/denken", "/kontext", "/embeddings", "/model",
                "/bildmodel",
                "/video", "/debug", "/vision", "/appstore"}
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

        # Erstes Wort – oder ein unbekannter Befehl mit weiteren Wörtern („/wachter fullscann“):
        # Befehle und Unterpunkte aller Befehle vorschlagen, der Vorschlag ersetzt die ganze Eingabe.
        cmd, _, rest = text.partition(" ")
        if " " not in text or cmd.lower() not in _BEKANNT:
            for befehl, desc in vorschlaege(text):
                yield Completion(
                    befehl,
                    start_position=-len(text),
                    display=befehl.strip(),
                    display_meta=desc,
                )
            return

        # Zweites Wort -> Argumente zum jeweiligen Befehl
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
        if cmd == "/zip":
            return [("an", "📦 fragt vor dem Entpacken"),
                    ("auto", "📦 Archive im Profilordner sofort entpacken"),
                    ("aus", "📦 nichts entpacken")]
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
                     ("gesicht", "Gesichts-Korrektur an/aus (alle Bilder)"),
                     ("--ohne-gesicht", "Gesichts-Korrektur nur für dieses Bild weglassen"),
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
    teile = text.split(maxsplit=1)                    # an jedem Leerraum, auch am Zeilenumbruch
    return teile[0].lower(), (teile[1].strip() if len(teile) > 1 else "")
