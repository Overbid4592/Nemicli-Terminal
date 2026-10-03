"""Tokenizer-Regeln (Vorzerlegung) gegen die tokenizers-Bibliothek – ohne Modell-Download.

python -m unittest tests.test_tokenizer_regeln
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import regex
    from tokenizers import Regex, pre_tokenizers
except Exception:
    regex = None

from ggufengine import tokenizer as TK              # noqa: E402

TEXTE = [
    "Hallo Welt! Wie geht's dir? I'm fine, THEY'LL come.",
    "Preis: 1.299,99 € – Nummer 1234567 und 3.14159",
    "def f(x):\n    return x**2  # Quadrat\n\n\nprint(f(12))\r\n\ttab",
    "Straße, Größe, café, naïve, e\u0301 kombiniert, 👨\u200d👩\u200d👧 und 😀😀",
    "  mehrere   Leerzeichen  \n\n  und Zeilen \n   ",
    "C:\\Users\\user\\datei.txt – „Anführung“ … <tag attr=\"1\">",
    "日本語のテキスト、中文、한국어, العربية, हिन्दी, ไทย",
    "CamelCaseWord HTTPServer iPhone XMLHttpRequest",
]


@unittest.skipIf(regex is None, "regex/tokenizers fehlen")
class RegelnTests(unittest.TestCase):

    def test_jede_regel_wie_tokenizers(self):
        for typ, muster in TK._REGELN.items():
            ref = pre_tokenizers.Sequence([pre_tokenizers.Split(Regex(m), behavior="isolated") for m in muster])
            eigen = [regex.compile(m) for m in muster]
            for text in TEXTE:
                erwartet = [s for s, _ in ref.pre_tokenize_str(text)]
                self.assertEqual(TK.regex_split(text, eigen), erwartet, f"{typ}: {text[:30]!r}")

    def test_qwen_scanner_wie_qwen2_regel(self):
        qwen2 = regex.compile(TK._KONTRAKTION + r"|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*"
                                                r"|\s*[\r\n]+|\s+(?!\S)|\s+")
        for text in TEXTE:
            self.assertEqual(TK._pretokenize_qwen(text, letters_with_marks=False), TK.regex_split(text, [qwen2]))

    def test_namen_zuordnung(self):
        self.assertIs(TK._PRE_TYP["llama-bpe"], "llama3")
        self.assertIs(TK._PRE_TYP["smollm"], "starcoder")
        self.assertIsNone(TK.regeln_fuer("qwen2"))                  # eigener Scanner
        # unbekannter Name: feste Standardregel, nie etwas aus der Datei
        unbekannt = TK.regeln_fuer("(a+)+$")
        self.assertEqual([r.pattern for r in unbekannt], TK._REGELN["default"])


if __name__ == "__main__":
    unittest.main()
