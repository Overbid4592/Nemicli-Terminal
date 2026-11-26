"""Wache ohne Konsolenfenster: Abmelden/Herunterfahren gilt als normales Ende (kein „unsauber“ beim nächsten Start).

python -m unittest tests.test_wache_sitzungsende
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_testhilfe  # noqa: E402
tearDownModule = qt_testhilfe.aufraeumen         # Qt-Fenster nach den Tests dieses Moduls löschen
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    from wache import oberflaeche as O
except Exception:                                       # ohne PySide6
    O = None
import fehlerwaechter as F                              # noqa: E402


@unittest.skipIf(O is None, "PySide6 fehlt")
class SitzungsendeTests(unittest.TestCase):

    def test_windows_meldung_beendet_sauber(self):
        app = O.anwendung()
        self.assertGreaterEqual(app.receivers("2commitDataRequest(QSessionManager&)"), 1)
        with mock.patch.object(F, "sauber_beendet") as sauber:
            O._sitzung_endet(object())
        sauber.assert_called_once()
        self.assertIn("Herunterfahren", sauber.call_args[0][0])

    def test_fehler_im_waechter_bricht_nichts(self):
        with mock.patch.object(F, "sauber_beendet", side_effect=OSError("gesperrt")):
            O._sitzung_endet()                          # darf nicht werfen


if __name__ == "__main__":
    unittest.main()
