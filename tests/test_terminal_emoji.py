"""Eigenes Terminal-Fenster: Emojis brauchen genau so viele Zellen wie in der Oberfläche (rich/Textual), und
nichts hinter einem Zeichen ohne eigene Zelle geht verloren.

Anlass: Verrutschte Kopfzeile des Eingabefelds – pyte verwarf nach U+FE0F/U+200D den Rest des Textstücks und
zählte Hautton und Flaggen breiter als Textual; dessen Updates trafen danach die falschen Spalten.

python -m unittest tests.test_terminal_emoji
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import pyte
    import terminal_fenster as T
    from rich.cells import cell_len
except Exception:                                       # ohne pyte/rich
    T = None


@unittest.skipIf(T is None, "pyte oder rich fehlt")
class EmojiZellenTests(unittest.TestCase):

    def _zellen(self, text: str, schirm=None, breite: int = 30) -> list[str]:
        s = schirm or T.Schirm(breite, 3)
        pyte.Stream(s).feed(text)
        return [s.buffer[0][x].data for x in range(breite)]

    def test_nichts_geht_verloren(self):
        for text in ("Herz ❤️ und weiter", "Dev 👩‍💻 ok", "Daumen 👍🏻 hoch", "Flagge 🇩🇪 da"):
            with self.subTest(text=text):
                self.assertEqual(text, "".join(self._zellen(text)).rstrip())

    def test_spalten_wie_in_der_oberflaeche(self):
        for text in ("A👍🏻B", "🇩🇪x", "Herz ❤️ und", "Café 😳 💙 Ende", "Dev 👩‍💻 ok"):
            with self.subTest(text=text):
                zellen = self._zellen(text)
                letzte = max(i for i, z in enumerate(zellen) if z.strip())
                self.assertEqual(cell_len(text), letzte + 1)        # das letzte Zeichen sitzt da, wo Textual es erwartet

    def test_auch_im_ki_reiter(self):
        zellen = self._zellen("Herz ❤️ und weiter", T.KiSchirm(30, 3))
        self.assertEqual("Herz ❤️ und weiter", "".join(zellen).rstrip())

    def test_akzente_wie_bisher(self):
        self.assertEqual("é", self._zellen("éx")[0][:2].replace("é", "é"))


if __name__ == "__main__":
    unittest.main()
