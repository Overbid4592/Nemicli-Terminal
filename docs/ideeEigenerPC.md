# Idee: NemiCLI bekommt einen eigenen PC

Stand: 04.10.2026 · Idee des Autors · besprochen mit Claude · **noch nicht begonnen – großes Projekt**

## Der Gedanke
Wie bei **Grok Bot** (xAI + Cursor, Beta seit 11.08.2026): Die KI hat ihren **eigenen Computer**.
Dort darf sie forschen und sich austoben – installieren, ausprobieren, auch mal etwas kaputt machen.
Der echte PC bleibt dabei unberührt. In NemiCLI sieht man ihren Bildschirm live
(bei Grok Bot: Reiter „Computer“ → „Bildschirm von Architekt“).

Vorbild Grok Bot kurz:
- Jeder Bot hat einen eigenen Cloud-PC (laut Tests Ubuntu, ~16 GB RAM, 128 GB SSD).
- Er bedient Programme wie ein Mensch (klicken, tippen, anmelden) – auch ohne Schnittstelle.
- Arbeitet rund um die Uhr, lernt Abläufe durch einmal Zeigen (Routinen).
- Mehrere Bots arbeiten im Team, einer verteilt die Aufgaben.
- Offiziell nur macOS + iOS. ⚠️ GitHub „grok-bot-app/grok-bot“ ist **nicht offiziell** – nicht laden.

## Bei uns: lokal statt Cloud
| Baustein | Wie |
|---|---|
| Ihr PC | Ubuntu als virtueller PC in **Hyper-V** (bei Windows 11 Pro dabei) |
| Befehle | NemiCLI schickt Befehle per **SSH** an ihren PC – nur innerhalb des Rechners |
| Bildschirm | NemiCLI holt regelmäßig ein Bild vom Ubuntu-Desktop und zeigt es an |
| Klicken/Tippen | Sie sieht das Bild und klickt per Werkzeug (Computer Use) |
| Denken | bleibt auf Windows – GPU lässt sich an Hyper-V nur schwer durchreichen; sie **denkt** hier und **handelt** dort |
| Zurücksetzen | Prüfpunkt (Snapshot) der VM – geht etwas schief, ein Klick zurück |

Die **gelbe Todo-Liste** gilt auch dort: Man sieht immer, was sie auf ihrem PC vorhat.

## Passt zu
- **Labor-VHD** (`F:\NemicliLABOR.vhd`): abgeschottetes Labor für Experimente.
- **Eigenes System auf Linux-Basis**: Erfahrung mit NemiCLI unter Ubuntu sammeln.

## Offene Fragen (eine nach der anderen klären)
1. Was soll sie dort machen? Forschen · Programmieren · ganz frei · alles zusammen
2. Darf sie auf **ihrem** PC Adminrechte (sudo) haben? (Auf dem echten PC: nie.)
3. Internet: frei, oder nur bestimmte Seiten? Darf die VM ins Heimnetz?
4. Wie viel RAM/Platte bekommt sie (64 GB RAM vorhanden)?
5. Darf sie arbeiten, wenn niemand am PC ist? Wenn ja: wie berichtet sie?
6. Wie kommen Ergebnisse auf den echten PC – nur auf Zuruf, mit Prüfung?

## Erste Schritte (wenn es losgeht)
1. Hyper-V einschalten, Ubuntu-VM anlegen (von Hand – braucht Adminrechte).
2. SSH in der VM, Verbindung von NemiCLI testen.
3. Werkzeug für Befehle auf ihrem PC, dann Bildschirm-Anzeige, dann Klicken/Tippen.
