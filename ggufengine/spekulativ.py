"""Speculative decoding: proposers for tokens that the model checks in one pass.

The engine samples every position from the model's own logits exactly as without
speculation and keeps a proposed token only if it equals the sample, so the output
does not change – a correct proposal just saves the forward pass for that token."""
from typing import Callable, Dict, List, Tuple


class NGramEntwurf:
    """Prompt lookup: when the last `n` tokens occurred before, the tokens that followed
    them then are proposed (repeated code, quotes, names, lists)."""

    def __init__(self, tokens: List[int], n: int = 3, max_neu: int = 8, fenster: int = 16384):
        self.n, self.max_neu = n, max_neu
        self._toks: List[int] = list(tokens[-fenster:])
        self._index: Dict[Tuple[int, ...], int] = {}   # n-gram -> position after its last occurrence
        for ende in range(n, len(self._toks)):
            self._index[tuple(self._toks[ende - n:ende])] = ende
        self._pause = 0                   # no proposals at all (the checks did not pay off)
        self._ngram_pause = 0             # no prompt lookup (its last proposal was wrong)
        self._quelle = ""

    def zufuegen(self, tid: int) -> None:
        t = self._toks
        if len(t) >= self.n:              # the n-gram ending before the new token now has a successor
            self._index[tuple(t[len(t) - self.n:])] = len(t)
        t.append(tid)

    def _ngram(self, platz: int) -> List[int]:
        if self._ngram_pause:
            self._ngram_pause -= 1
            return []
        t = self._toks
        if len(t) < self.n or platz < 1:
            return []
        start = self._index.get(tuple(t[len(t) - self.n:]))
        if start is None:
            return []
        self._quelle = "ngram"
        return t[start:start + min(self.max_neu, platz)]

    def vorschlag(self, platz: int) -> List[int]:
        """Up to min(max_neu, platz) tokens following the current end."""
        if self._pause:
            self._pause -= 1
            return []
        return self._ngram(platz)

    def ergebnis(self, angenommen: int) -> None:
        """No token of a lookup was right: no lookup for a few tokens (the pass costs more than
        a single step, and a recurrent model has to read the tokens again)."""
        if angenommen == 0 and self._quelle == "ngram":
            self._ngram_pause = 4

    def pausieren(self, tokens: int) -> None:
        self._pause = max(self._pause, tokens)


class MtpEntwurf(NGramEntwurf):
    """Prompt lookup when it finds at least two tokens, otherwise the model's MTP head
    (`model.mtp_vorschlag`) proposes `mtp_n` tokens after the last one."""

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
    """Which proposal length pays off on this model and device: seconds per kept token of a
    check (proposing, the pass, rebuilding or reading again after a wrong proposal) per length,
    against seconds of a single step.  The longest length is tried first (`PRUEFUNGEN` checks);
    a shorter one only when the longer one is slower than a single step or hits less than
    `QUOTE_GUT` of its proposals.  Of the lengths tried the fastest one is used (longer
    proposals are cut); when none beats a single step, proposals pause and afterwards every
    length is judged anew by one check."""
    PRUEFUNGEN = 3                    # checks of one length before it is judged
    QUOTE_GUT = 0.5                   # share of proposed tokens kept that makes shorter ones pointless
    STUFEN = (8, 4, 3, 2, 1)
    PAUSE = 64                        # tokens without proposals when no length paid off,
    PAUSE_MAX = 2048                  # doubled each time in a row up to this
    MESSEN = 4                        # single steps to measure when none was timed yet
    EINZELN_JE = 8                    # single steps per timed one

    def __init__(self):
        self.schritt = None           # seconds of a single step (moving average)
        self._je = self._leer()       # length -> [seconds per kept token, checks, share kept]
        self._k, self._zeit, self._tokens = 0, 0.0, 0     # the running check
        self._naechste = self.PAUSE
        self._laengste = 0                                 # longest proposal seen
        self._einzeln_n = 0

    @classmethod
    def _leer(cls) -> dict:
        return {k: [0.0, 0, 0.0, True] for k in cls.STUFEN}   # ..., next check replaces the average

    def einzeln_messen(self) -> bool:
        """Time this single step?  Every `EINZELN_JE`-th one (timing needs a synchronize, which
        stops the CPU from preparing the next step while the GPU works)."""
        self._einzeln_n += 1
        return self.schritt is None or self._einzeln_n % self.EINZELN_JE == 0

    def einzeln(self, sekunden: float) -> None:
        self.schritt = sekunden if self.schritt is None else 0.8 * self.schritt + 0.2 * sekunden

    def start(self, k: int) -> None:
        """A check with `k` proposed tokens begins."""
        self._k, self._zeit, self._tokens = k, 0.0, 0
        self._gueltig = True

    def ungueltig(self) -> None:
        """The running check took longer for a one-off reason (a CUDA graph was captured): not timed."""
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
        """Proposal length to check for a proposal of `laenge` tokens (0: the longest seen); 0: none."""
        if laenge:
            self._laengste = max(self._laengste, laenge)
        laenge = laenge or self._laengste or self.STUFEN[0]
        moeglich = [k for k in self.STUFEN if k <= laenge]
        if not moeglich or self.schritt is None:
            return moeglich[0] if moeglich else 0
        for k in moeglich:                                 # longest first
            if not self._gepruefte(k):
                return k                                   # try it
            if self._schnell(k) and self._je[k][2] >= self.QUOTE_GUT:
                break                                      # good enough: shorter ones are not tried
        gut = [k for k in moeglich if self._gepruefte(k) and self._schnell(k)]
        return min(gut, key=lambda k: self._je[k][0]) if gut else 0

    def pause(self) -> int:
        """Called when a check is finished: tokens to go without proposals (0 = go on)."""
        if self._k in self._je and self._tokens and getattr(self, "_gueltig", True):
            eintrag = self._je[self._k]
            rate = self._zeit / self._tokens
            quote = max(0, self._tokens - 1) / self._k
            if eintrag[3]:
                eintrag[0], eintrag[2], eintrag[3] = rate, quote, False
            else:
                eintrag[0] = 0.7 * eintrag[0] + 0.3 * rate
                eintrag[2] = 0.7 * eintrag[2] + 0.3 * quote
            eintrag[1] += 1
        if self.schritt is None:
            return self.MESSEN if sum(e[1] for e in self._je.values()) >= self.PRUEFUNGEN else 0
        bis_hier = [k for k in self.STUFEN if k <= self._k]          # what proposals this long could use
        if any(self._lohnt(k) for k in bis_hier):
            if self._gepruefte(self._k) and self._schnell(self._k):
                self._naechste = self.PAUSE
            return 0
        for eintrag in self._je.values():                          # after the pause: one fresh check each
            eintrag[1] = min(eintrag[1], self.PRUEFUNGEN - 1)
            eintrag[3] = True
        pause, self._naechste = self._naechste, min(2 * self._naechste, self.PAUSE_MAX)
        return pause
