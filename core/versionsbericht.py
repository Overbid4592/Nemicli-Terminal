"""
versionsbericht.py - Was NemiCLI in den letzten Versionen dazugelernt hat – für die KI selbst.

Je Paket ein Eintrag: Version, Titel und was die KI jetzt kann, mit dem Weg dahin (Werkzeug, Befehl,
Feld). Abrufbar jederzeit mit anleitung_lesen thema "neu"; fragt der Nutzer, was neu ist, kommt der
Bericht von selbst mit. Die Projektgeschichte mit allen Einzelheiten steht in CHANGELOG.md.

Mit jedem Paket wird hier ein Eintrag ergänzt (neueste zuerst).
"""
from __future__ import annotations

ANZAHL = 6                       # so viele Versionen zeigt der Bericht

# (Version, Titel, Text) – neueste zuerst
EINTRAEGE: list[tuple[str, str, str]] = [
    ("9.47", "Gedächtnis: genauere Suche, Pflege durch die Cloud-KI, Archiv",
     "Die Suche im Gedächtnis hat eigene Grenzen je Embedding-Modell; mit EmbeddingGemma 2 kommen unpassende "
     "Notizen kaum noch als Treffer, und meldet gedaechtnis_suchen „⚠ schwach“, gibt es dazu wirklich nichts. "
     "Im Vorgeschmack vor deiner Antwort steht nur noch, was sicher passt. Neu ist gedaechtnis_aufraeumen: "
     "Merkst du, dass sich Notizen doppeln oder überholt sind, merk die Pflege vor – nach der Runde prüft eine "
     "Cloud-KI ähnliche und alte Notizen, schätzt sich dafür zuerst selbst ein (sonst übernimmt die Ersatzbank), "
     "legt Gleiches zusammen und archiviert Veraltetes. Lokale Modelle machen das nie; etwa einmal am Tag läuft "
     "es von selbst, /aufraeumen sofort. Nichts geht verloren: /gedaechtnis archiv zeigt Ersetztes, Vergessenes "
     "und Zusammengelegtes, /gedaechtnis zurueck <Nr> holt eine Notiz zurück."),
    ("9.46", "Modell-Familien und LFM3 im eigenen Motor",
     "Der eigene Motor kann LFM3 (Liquid AI, um 2 B): Text mit bis zu 256K Kontext, Werkzeuge im eigenen "
     "XML-Format, Nachdenken – unter /denken auto denkt LFM3 jede Runde, weil es ohne Nachdenken unzuverlässig "
     "antwortet – und Bilder über LFM3-VL. Eine Modell-Familie (ModelGGUF/<Familie>/<Mitglied>/) steht in "
     "/model → Lokal als ein Team (🧬): Es chattet das Mitglied ohne Bild-Teil; schickt der Nutzer ein Bild, "
     "sieht das Mitglied mit Bild-Teil es sich an und gibt dir die Beschreibung – geladen wird es nur dafür. "
     "Jedes Mitglied ist auch einzeln wählbar (z. B. LFM3Familie-VL, das selbst sieht). LFM3 ist die Ausnahme "
     "von der 4B-Grenze für Chat-Modelle; holen lässt sich die ganze Familie mit /einrichten modelle. "
     "Jede NemiCLI-Kopie (Quelltext oder exe) ist für dich gesperrt wie der eigene Programm-Ordner – "
     "/schluessel öffnet nur den eigenen; dein Daten-Ordner bleibt frei."),
    ("9.45", "Sprechen 2.0 – Lippen, Körpersprache, freigestellt in der Kugel",
     "portraet_bewegen mit sagen: Die Lippen formen die Laute (A weit, O/U rund, I/E breit, M/B/P zu) – aus dem "
     "Klang, also in jeder Sprache. Der Kopf nickt auf Betonungen, bei Fragen gehen die Brauen hoch, in Pausen "
     "blinzelst du. frei=true stellt den Hintergrund frei; die Maske wandert mit der Bewegung mit. Mit zeigen "
     "\"kugel\" sprichst du in der Kugel selbst, mit Ton, als ganze Figur ohne Kreis; bild \"kugel\" nimmt das "
     "Bild, das die Kugel gerade zeigt. Deine Charakter-Datei hat jetzt eine Sicherung (Backup/Charaktere/): fehlt "
     "das Original, gilt die Sicherung, und das Original kommt von selbst zurück."),
    ("9.44", "Gedächtnis im Takt, Low-Modus, VRAM-Grenze",
     "Eingebettet wird im Takt von /embeddingtimer (aus · 15 · 30 · 45 · 60 · 90 Minuten, Vorgabe 30) – was seit "
     "dem letzten Lauf dazukam, findet die Suche erst danach. Aufgeräumter Kontext, /gedaechtnis indexieren, "
     "/reflektieren und /aufraeumen laufen sofort. Low-Modus (Vorgabe aktiv): bei Chat und Bild rechnet der "
     "Bibliothekar langsam auf der CPU weiter statt zu pausieren. Auf der GPU nimmt er höchstens 25 % des "
     "Grafikspeichers (Config embedding_vram_anteil). Menü öffnen: menue_oeffnen /embeddingtimer."),
    ("9.43", "EmbeddingGemma 2 und Bildsuche",
     "bilder_suchen (frage, anzahl) findet deine eigenen Bilder nach Inhalt, Mal-Prompt und Namen – mit "
     "EmbeddingGemma 2 und seinem Bild-Teil wirklich nach dem, was zu sehen ist. Das Modell ist mit /embeddings "
     "wählbar. Figuren in Bildern sind immer erwachsen, ab 18."),
    ("9.42", "Kontext lokal ohne feste Grenze, Werkzeug-Aufrufe repariert",
     "Lokale Modelle nutzen ihren vollen Kontext; wird er voll, fasst NemiCLI das Gespräch zusammen, von Hand "
     "mit /compact. Ein Werkzeug-Aufruf, dem der Schluss fehlt, wird ergänzt und ausgeführt. Bitten und "
     "Ankündigungen werden auch auf Englisch erkannt."),
    ("9.41", "Denkweise je Persönlichkeit und Wort halten",
     "/denken auto · an · aus (Werkzeug denkweise): auto denkt bei kniffligen Fragen und bei Bitten, etwas zu "
     "tun. Kündigst du etwas an, ohne das Werkzeug aufzurufen, kommt einmal ein Stups. /staerke ist beim eigenen "
     "Motor das Denk-Budget."),
    ("9.40", "Gedächtnis in zwei Stufen",
     "merken mit details und name legt zu einer kurzen Notiz eine eigene Details-Datei an; lesen mit "
     "gedaechtnis_lesen ref \"detail:<name>\" (\"detail:\" allein listet alle)."),
]


def bericht(anzahl: int = ANZAHL) -> str:
    """Die letzten `anzahl` Versionen als Text für die KI."""
    try:
        import version
        jetzt = version.VERSION
    except Exception:
        jetzt = ""
    kopf = "# Neu in NemiCLI" + (f" (jetzt {jetzt})" if jetzt else "")
    teile = [kopf, "Das kannst du seit den letzten Versionen – erzähl es in eigenen Worten, wenn der Nutzer fragt:"]
    for nummer, titel, text in EINTRAEGE[:max(1, anzahl)]:
        teile.append(f"\n## {nummer} – {titel}\n{text}")
    return "\n".join(teile)
