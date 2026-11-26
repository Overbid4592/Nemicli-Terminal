"""BPE-Verschmelzen: das schnelle Verfahren (Vorrangliste) liefert dasselbe wie Runde für Runde – auch mit
Merges, die nicht nach Lernreihenfolge sortiert sind (wie bei Gemma) – und bleibt bei langen Wörtern schnell."""
import random
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ggufengine import tokenizer as T                  # noqa: E402


def _ranks(zeichen: str, n: int, rng: random.Random, sortiert: bool) -> dict:
    """Zufällige Merges über `zeichen`; unsortiert: späte Verschmelzungen bekommen auch kleine Ränge."""
    vokab, merges = list(zeichen), []
    for _ in range(n):
        a, b = rng.choice(vokab), rng.choice(vokab)
        if (a, b) in merges or len(a + b) > 12:
            continue
        merges.append((a, b))
        vokab.append(a + b)
    if not sortiert:
        rng.shuffle(merges)
    return {m: r for r, m in enumerate(merges)}


class Verschmelzen(unittest.TestCase):

    def test_gleich_wie_runde_fuer_runde(self):
        rng = random.Random(3)
        for sortiert in (True, False):
            for zeichen in ("ab", "abc▁", "▁\n x", "abcdefgh"):
                ranks = _ranks(zeichen, 60, rng, sortiert)
                for _ in range(150):
                    wort = [rng.choice(zeichen) for _ in range(rng.randint(2, 300))]
                    self.assertEqual(T._verschmelzen_runden(list(wort), ranks),
                                     T._verschmelzen_heap(list(wort), ranks), (sortiert, zeichen, "".join(wort)))

    def test_leerzeichen_folgen_wie_gemma(self):
        # Gemmas Merges für Leerzeichen-Folgen (465 Paare bis 31 Zeichen): das längste Ergebnis zuerst, dann der
        # längere linke Teil – (30, 1) hat Rang 1. Eine Vorrangliste ohne Runden verschmolz 60 Leerzeichen zu
        # 31 + 29 statt wie Runde für Runde zu 16er-Stücken.
        merges = [("▁" * a, "▁" * b) for a in range(1, 31) for b in range(1, 32 - a)]
        merges.sort(key=lambda m: (-len(m[0] + m[1]), -len(m[0])))
        ranks = {m: r + 1 for r, m in enumerate(merges)}
        self.assertEqual(465, len(ranks))
        self.assertEqual(1, ranks[("▁" * 30, "▁")])
        for n in (2, 3, 17, 60, 100, 257):
            wort = ["▁"] * n
            self.assertEqual(T._verschmelzen_runden(list(wort), ranks), T._verschmelzen_heap(list(wort), ranks), n)

    def test_langes_wort_bleibt_schnell(self):
        rng = random.Random(9)
        zeichen = "abcdefghijklmnopqrstuvwxyz0123456789+/"
        ranks = _ranks(zeichen, 3000, rng, False)
        wort = [rng.choice(zeichen) for _ in range(40_000)]
        t = time.perf_counter()
        T._verschmelzen(wort, ranks)
        self.assertLess(time.perf_counter() - t, 5.0)                 # Runde für Runde: weit über einer Minute


if __name__ == "__main__":
    unittest.main()
