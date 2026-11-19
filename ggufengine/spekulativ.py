"""Spekulatives Dekodieren: Vorschläge für Tokens, die das Modell in einem Durchlauf prüft.

Die Engine sampelt jede Position aus den eigenen Logits des Modells genau wie ohne Spekulation
und behält ein vorgeschlagenes Token nur, wenn es dem Sample gleicht. Die Ausgabe ändert sich
also nicht – ein richtiger Vorschlag spart nur den Forward-Pass für dieses Token."""
from typing import Callable, Dict, List, Optional, Tuple


class NGramEntwurf:
    """Prompt-Lookup: Kamen die letzten `n` Tokens schon einmal vor, werden die Tokens
    vorgeschlagen, die ihnen damals folgten (wiederholter Code, Zitate, Namen, Listen)."""

    def __init__(self, tokens: List[int], n: int = 3, max_neu: int = 8, fenster: int = 16384):
        if fenster < 1:
            raise ValueError("fenster must be positive")
        self.n, self.max_neu = n, max_neu
        self._fenster = fenster
        self._trim = min(1024, fenster)  # amortisiertes Kürzen: begrenzter Puffer statt Verschieben je Token
        self._toks: List[int] = list(tokens[-fenster:])
        self._index: Dict[Tuple[int, ...], int] = {}   # n-Gramm -> Position nach seinem letzten Vorkommen
        for ende in range(n, len(self._toks)):
            self._index[tuple(self._toks[ende - n:ende])] = ende
        self._pause = 0                   # gar keine Vorschläge (die Prüfungen haben sich nicht gelohnt)
        self._ngram_pause = 0             # kein Prompt-Lookup (sein letzter Vorschlag war falsch)
        self._quelle = ""

    def zufuegen(self, tid: int) -> None:
        t = self._toks
        if len(t) >= self.n:              # das n-Gramm, das vor dem neuen Token endet, hat jetzt einen Nachfolger
            self._index[tuple(t[len(t) - self.n:])] = len(t)
        t.append(tid)
        if len(t) >= self._fenster + self._trim:
            # Nur gelegentlich neu aufbauen, damit nicht je erzeugtem Token die ganze Liste verschoben wird.
            self._toks = t = t[-self._fenster:]
            self._index = {}
            for ende in range(self.n, len(t)):
                self._index[tuple(t[ende - self.n:ende])] = ende

    def _ngram(self, platz: int) -> List[int]:
        if self._ngram_pause:
            self._ngram_pause -= 1
            return []
        t = self._toks
        if len(t) < self.n or platz < 1:
            return []
        start = self._index.get(tuple(t[len(t) - self.n:]))
        # Der Puffer dahinter kann _trim ältere Tokens enthalten; abgelaufene n-Gramme ignorieren.
        if start is None or start - self.n < max(0, len(t) - self._fenster):
            return []
        self._quelle = "ngram"
        return t[start:start + min(self.max_neu, platz)]

    def vorschlag(self, platz: int) -> List[int]:
        """Bis zu min(max_neu, platz) Tokens, die auf das aktuelle Ende folgen."""
        if self._pause:
            self._pause -= 1
            return []
        return self._ngram(platz)

    def ergebnis(self, angenommen: int) -> None:
        """Kein Token eines Lookups war richtig: für einige Tokens kein Lookup (der Durchlauf kostet mehr
        als ein Einzelschritt, und ein rekurrentes Modell muss die Tokens erneut lesen)."""
        if angenommen == 0 and self._quelle == "ngram":
            self._ngram_pause = 4

    def pausieren(self, tokens: int) -> None:
        self._pause = max(self._pause, tokens)


class MtpEntwurf(NGramEntwurf):
    """Prompt-Lookup, wenn er mindestens zwei Tokens findet; sonst schlägt der MTP-Kopf des Modells
    (`model.mtp_vorschlag`) `mtp_n` Tokens nach dem letzten vor."""

    def __init__(self, tokens: List[int], model, position: Callable[[], int], mtp_n: int = 3, **kw):
        super().__init__(tokens, **kw)
        self.model, self.position, self.mtp_n = model, position, mtp_n

    def vorschlag(self, platz: int) -> List[int]:
        if self._pause:
            self._pause -= 1
            return []
        v = self._ngram(platz)
        if len(v) >= 2 or platz < 1:
            return v
        self._quelle = "mtp"
        return self.model.mtp_vorschlag(self._toks[-1], self.position(), min(self.mtp_n, platz))


class Nutzen:
    """Welche Vorschlagslänge sich auf diesem Modell und Gerät lohnt: Sekunden je behaltenem Token
    einer Prüfung (Vorschlagen, der Durchlauf, Neuaufbau oder erneutes Lesen nach einem falschen
    Vorschlag) je Länge, verglichen mit den Sekunden eines Einzelschritts.  Die größte Länge wird
    zuerst probiert (`PRUEFUNGEN` Prüfungen); eine kürzere nur, wenn die längere langsamer als ein
    Einzelschritt ist oder weniger als `QUOTE_GUT` ihrer Vorschläge trifft.  Von den probierten
    Längen wird die schnellste genutzt (längere Vorschläge werden gekürzt); schlägt keine den
    Einzelschritt, pausieren die Vorschläge, und danach wird jede Länge durch eine Prüfung neu
    bewertet."""
    PRUEFUNGEN = 3                    # Prüfungen einer Länge, bevor sie bewertet wird
    QUOTE_GUT = 0.5                   # Anteil behaltener Vorschlags-Tokens, ab dem kürzere sinnlos sind
    STUFEN = (8, 4, 3, 2, 1)
    PAUSE = 64                        # Tokens ohne Vorschläge, wenn sich keine Länge gelohnt hat,
    PAUSE_MAX = 2048                  # bei jeder Wiederholung verdoppelt, bis höchstens hierhin
    MESSEN = 4                        # zu messende Einzelschritte, solange noch keiner gemessen ist
    EINZELN_JE = 8                    # Einzelschritte je gemessenem

    def __init__(self):
        self.schritt = None           # Sekunden eines Einzelschritts (gleitender Mittelwert)
        self._je = self._leer()       # Länge -> [Sekunden je behaltenem Token, Prüfungen, Anteil behalten]
        self._k, self._zeit, self._tokens = 0, 0.0, 0     # die laufende Prüfung
        self._naechste = self.PAUSE
        self._laengste = 0                                 # längster bisheriger Vorschlag
        self._einzeln_n = 0

    @classmethod
    def _leer(cls) -> dict:
        return {k: [0.0, 0, 0.0, True] for k in cls.STUFEN}   # ..., nächste Prüfung ersetzt den Mittelwert

    def einzeln_messen(self) -> bool:
        """Diesen Einzelschritt messen?  Jeden `EINZELN_JE`-ten (Messen braucht ein synchronize, das die
        CPU daran hindert, den nächsten Schritt vorzubereiten, während die GPU arbeitet)."""
        self._einzeln_n += 1
        return self.schritt is None or self._einzeln_n % self.EINZELN_JE == 0

    def einzeln(self, sekunden: float) -> None:
        self.schritt = sekunden if self.schritt is None else 0.8 * self.schritt + 0.2 * sekunden

    def start(self, k: int) -> None:
        """Eine Prüfung mit `k` vorgeschlagenen Tokens beginnt."""
        self._k, self._zeit, self._tokens = k, 0.0, 0
        self._gueltig = True

    def ungueltig(self) -> None:
        """Die laufende Prüfung dauerte aus einmaligem Grund länger (CUDA-Graph aufgenommen): nicht gemessen."""
        self._gueltig = False

    def pruefung(self, sekunden: float, tokens: int = 0) -> None:
        self._zeit += sekunden
        self._tokens += tokens

    def _gepruefte(self, k: int) -> bool:
        return self._je[k][1] >= self.PRUEFUNGEN

    def _schnell(self, k: int) -> bool:
        return self.schritt is None or self._je[k][0] < self.schritt

    def _lohnt(self, k: int) -> bool:
        return not self._gepruefte(k) or self._schnell(k)

    def erlaubt(self, laenge: int = 0) -> int:
        """Zu prüfende Vorschlagslänge für einen Vorschlag von `laenge` Tokens (0: die längste bisher); 0: keine."""
        if laenge:
            self._laengste = max(self._laengste, laenge)
        laenge = laenge or self._laengste or self.STUFEN[0]
        moeglich = [k for k in self.STUFEN if k <= laenge]
        if not moeglich or self.schritt is None:
            return moeglich[0] if moeglich else 0
        for k in moeglich:                                 # längste zuerst
            if not self._gepruefte(k):
                return k                                   # probieren
            if self._schnell(k) and self._je[k][2] >= self.QUOTE_GUT:
                break                                      # gut genug: kürzere werden nicht probiert
        gut = [k for k in moeglich if self._gepruefte(k) and self._schnell(k)]
        return min(gut, key=lambda k: self._je[k][0]) if gut else 0

    def pause(self, angenommen: Optional[int] = None) -> int:
        """Prüfung abschließen (Rückgabe: Anzahl Tokens Pause). Gezählt werden tatsächlich angenommene
        Vorschläge, keine Ersatz-Tokens. Ohne Zählwert wird er erschlossen – für eigenständige Zeitmessungs-Tests."""
        if self._k in self._je and self._tokens and getattr(self, "_gueltig", True):
            eintrag = self._je[self._k]
            rate = self._zeit / self._tokens
            quote = (min(self._k, max(0, angenommen)) / self._k if angenommen is not None
                     else min(self._k, max(0, self._tokens - 1)) / self._k)
            if eintrag[3]:
                eintrag[0], eintrag[2], eintrag[3] = rate, quote, False
            else:
                eintrag[0] = 0.7 * eintrag[0] + 0.3 * rate
                eintrag[2] = 0.7 * eintrag[2] + 0.3 * quote
            eintrag[1] += 1
        if self.schritt is None:
            return self.MESSEN if sum(e[1] for e in self._je.values()) >= self.PRUEFUNGEN else 0
        bis_hier = [k for k in self.STUFEN if k <= self._k]          # was Vorschläge dieser Länge nutzen könnten
        if any(self._lohnt(k) for k in bis_hier):
            if self._gepruefte(self._k) and self._schnell(self._k):
                self._naechste = self.PAUSE
            return 0
        for eintrag in self._je.values():                          # nach der Pause: je eine frische Prüfung
            eintrag[1] = min(eintrag[1], self.PRUEFUNGEN - 1)
            eintrag[3] = True
        pause, self._naechste = self._naechste, min(2 * self._naechste, self.PAUSE_MAX)
        return pause
