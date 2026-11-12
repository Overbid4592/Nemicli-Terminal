"""FolderSense: „leer" ist eine Regel in classify(), kein lernbarer Typ."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import main_quelle  # noqa: E402,F401
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import foldersense as FS  # noqa: E402


class LeerNichtLernbar(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for name, wert in (("LEARN_DIR", self.tmp / "learned"), ("MODEL_PATH", self.tmp / "learned" / "m.json"),
                           ("EXAMPLES_PATH", self.tmp / "learned" / "ex.json"), ("_MODEL_CACHE", None)):
            p = patch.object(FS, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def test_leer_wird_abgelehnt(self):
        ordner = self.tmp / "ordner"
        ordner.mkdir()
        (ordner / "a.mp3").touch()
        self.assertFalse(FS.learn_folder(ordner, "leer"))
        self.assertFalse(FS.EXAMPLES_PATH.exists())


if __name__ == "__main__":
    unittest.main()
