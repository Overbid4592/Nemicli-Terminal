# Idee: Baby-NemiCLI – eine eigene KI von null

Stand: 26.09.2026 · Idee des Autors · besprochen mit Claude · **noch nicht begonnen, Umsetzung „die Tage“**

## Der Gedanke
Keine fremde KI nachtrainieren („Fine-Tune-Lappen“), sondern eine **eigene KI von null** auf dem
eigenen PC trainieren – mit eigenen Augen zusehen, wie sie lernt. Prinzip: *Ein Baby wird geboren,
lernt vom Vater, wie alles funktioniert, und übernimmt später den Betrieb.*
Die KI wird **fest in NemiCLI verankert**.

## Was realistisch ist (RTX 5060 Ti, 16 GB)
| Größe | Text zum Lernen | Zeit (ungefähr) |
|---|---|---|
| 6B | ~120 Mrd. Wörter | Jahre – nicht machbar |
| 1B | ~20 Mrd. Wörter | 1–2 Monate Dauerbetrieb |
| 100M | ~2 Mrd. Wörter | ~1 Tag |
| 30M | ~0,5 Mrd. Wörter | wenige Stunden |

**Entscheidung: klein anfangen** – das ist das Spannendste.

## Die Stufen
1. 👶 **Sprechen lernen:** kleine KI (30M–100M) von null, mit freien deutschen Texten
   (z. B. Wikipedia). Live zusehen: Buchstabensalat → Wörter → Sätze, Fehlerkurve sinkt.
2. 🏫 **Schule:** Wissen über NemiCLI, Werkzeuge, Regeln.
3. 👨‍👦 **Lehre beim Vater:** eine große KI stellt Aufgaben, macht vor, korrigiert.
4. 🛠 **Betrieb übernehmen:** Schritt für Schritt Aufgaben in NemiCLI.

## Der Vater (Lehrer)
**DeepSeek V4.1 Flash über Ollama Cloud** (Favorit).
- Modell-Lizenz: MIT (sehr frei).
- Ollama-Bedingungen: Antworten gehören dem Nutzer, kein Trainingsverbot für eigene Modelle.
- Offener Punkt: Ollama verbietet, den Dienst für „konkurrierende Produkte“ zu nutzen. Für das
  private Lernprojekt unproblematisch – vor einer Veröffentlichung von NemiCLI nochmal prüfen.
- Große Cloud-Anbieter (auch Anthropic) verbieten das Trainieren mit ihren Antworten → nicht als
  Lehrer nutzen.

## Wichtig zu wissen
- Kleine KIs erfinden anfangs **mehr**, nicht weniger – das Baby weiß noch nichts.
- Das System zerschießen kann auch die eigene KI nicht: Die Sicherheit steckt in NemiCLI
  (Sandbox, Bestätigungen, Admin-Sperre, Schreibsperre), nicht im Modell.
- Die Regel „Sprachmodelle unter 4B tabu“: gilt für Arbeits-KIs; die Baby-KI ist ein
  Lernprojekt (Ausnahme entscheidet der Autor).
- Training in der **Labor-VHD** bzw. getrennt vom Alltags-NemiCLI.

## Nächster Schritt (wenn es losgeht)
Zuerst reden: Größe des Babys, welche Texte, wie das Zuschauen aussehen soll.
