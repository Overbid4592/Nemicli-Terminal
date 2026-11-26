"""Qt in Tests: nach den Tests eines Moduls alle Fenster löschen, solange die QApplication noch lebt.

Tests lassen Fenster stehen (deleteLater wird ohne Ereignisschleife nie ausgeführt). Beim Beenden zerstört PySide6
die QApplication über sein eigenes atexit – mit noch lebenden Fenstern und einem App-weiten Stylesheet (Wache-
Oberfläche) endete das in einer Zugriffsverletzung, nachdem alle Tests durch waren. Darum räumt jedes Qt-Testmodul
selbst auf:  tearDownModule = qt_testhilfe.aufraeumen
"""

from __future__ import annotations


def aufraeumen() -> None:
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    try:
        app.closeAllWindows()
        for w in list(QApplication.topLevelWidgets()):
            w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    except RuntimeError:                          # Objekt schon von Qt gelöscht
        pass
