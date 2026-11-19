"""waechterscan_fenster.py – Fenster des Wächter-Scans (Rechtsklick „🛡 Wächter-Scan“, wache/waechterscan.py).

Prüft im Hintergrund, zeigt Fortschritt und Funde mit Ampel. „Bericht öffnen“ zeigt den gespeicherten
Bericht, „NemiCLI fragen“ gibt ihn an die Persönlichkeit (offenes Fenster oder neues).
"""
from __future__ import annotations

import os
import threading

from gui_fenster import FARBE, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QProgressBar, QVBoxLayout, QWidget

from wache import waechterscan as WS

STIL = f"""
QWidget#scan {{ background: {FARBE['grund']}; }}
QListWidget {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px; }}
QListWidget::item {{ padding: 5px 4px; }}
QProgressBar {{
    background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; border-radius: 6px; height: 10px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {FARBE['cyan']}; border-radius: 6px; }}
"""
_ZEICHEN = {WS.ROT: "🔴", WS.GELB: "🟡", WS.GRUEN: "🟢"}


class _Bote(QObject):
    fortschritt = Signal(int, str)
    fertig = Signal(object)


class ScanFenster(QWidget):
    def __init__(self, pfade: list[str], *, starten: bool = True):
        super().__init__()
        self.pfade = pfade
        self.ergebnis: WS.Ergebnis | None = None
        self.bericht_pfad = None
        self._stopp = threading.Event()
        self.setObjectName("scan")
        self.setWindowTitle("NemiCLI – Wächter-Scan")
        self.setStyleSheet(STIL)
        self.resize(760, 560)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        v.addWidget(label("🛡 Wächter-Scan", 17, "text", fett=True))
        ziel = pfade[0] if len(pfade) == 1 else f"{len(pfade)} markierte Einträge"
        v.addWidget(label(f"Prüft: {ziel}", 10, "dim"))
        self.balken = QProgressBar()
        self.balken.setRange(0, 0)                       # läuft, bis die Zahl der Dateien feststeht
        v.addWidget(self.balken)
        self.stand = label("Suche Dateien …", 10, "dim", umbruch=False)
        v.addWidget(self.stand)
        self.liste = QListWidget()
        self.liste.setWordWrap(True)
        v.addWidget(self.liste, 1)
        self.zusammen = label("", 11, "text", fett=True)
        v.addWidget(self.zusammen)

        h = QHBoxLayout()
        self.stopp_k = Knopf("Anhalten", "⏹", mitte=True)
        self.stopp_k.clicked.connect(self._stopp.set)
        self.bericht_k = Knopf("Bericht öffnen", "📄", mitte=True)
        self.bericht_k.clicked.connect(self.bericht_oeffnen)
        self.fragen_k = Knopf("NemiCLI fragen", "💬", mitte=True)
        self.fragen_k.akzent = True
        self.fragen_k.clicked.connect(self.fragen)
        schliessen = Knopf("Schließen", mitte=True)
        schliessen.clicked.connect(self.close)
        for k in (self.bericht_k, self.fragen_k):
            k.setEnabled(False)
        h.addWidget(self.stopp_k)
        h.addStretch(1)
        h.addWidget(schliessen)
        h.addWidget(self.bericht_k)
        h.addWidget(self.fragen_k)
        v.addLayout(h)

        self.bote = _Bote()
        self.bote.fortschritt.connect(self._fortschritt)
        self.bote.fertig.connect(self._fertig)
        if starten:
            threading.Thread(target=self._lauf, daemon=True, name="waechterscan").start()

    def _lauf(self) -> None:
        WS.hintergrundmodus()
        letzte = [0.0]

        def melden(n: int, pfad: str) -> None:
            import time
            if time.time() - letzte[0] > 0.1:            # die Oberfläche nicht fluten
                letzte[0] = time.time()
                self.bote.fortschritt.emit(n, pfad)

        erg = WS.laufen(self.pfade, abbruch=self._stopp.is_set, fortschritt=melden)
        self.bote.fertig.emit(erg)

    def _fortschritt(self, n: int, pfad: str) -> None:
        self.stand.setText(f"{n:,} Dateien geprüft · {pfad[-90:]}".replace(",", "."))

    def _fertig(self, erg: WS.Ergebnis) -> None:
        self.ergebnis = erg
        self.balken.setRange(0, 1)
        self.balken.setValue(1)
        self.stopp_k.setEnabled(False)
        try:
            self.bericht_pfad = WS.bericht_speichern(erg)
        except OSError as e:
            self.stand.setText(f"⚠ Bericht nicht gespeichert: {e}")
        else:
            self.stand.setText(f"Bericht: {self.bericht_pfad}")
            self.bericht_k.setEnabled(True)
            self.fragen_k.setEnabled(True)
        self.zusammen.setText(WS.zusammenfassung(erg))
        auff = erg.auffaellig()
        for f in auff[:2000]:
            item = QListWidgetItem(f"{_ZEICHEN[f.ampel]}  {f.pfad}\n      {'; '.join(f.gruende)}")
            item.setForeground(farbe("fehler" if f.ampel == WS.ROT else "warn"))
            self.liste.addItem(item)
        if not auff:
            item = QListWidgetItem("🟢  Nichts Auffälliges gefunden.")
            item.setForeground(farbe("ok"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.liste.addItem(item)
        for p, grund in erg.luecken[:200]:
            item = QListWidgetItem(f"⚪  {p}\n      nicht geprüft: {grund}")
            item.setForeground(farbe("dim"))
            self.liste.addItem(item)

    def bericht_oeffnen(self) -> None:
        if self.bericht_pfad:
            os.startfile(str(self.bericht_pfad))           # noqa: S606 – Bericht im Standard-Programm

    def fragen(self) -> None:
        if not (self.bericht_pfad and self.ergebnis):
            return
        import kontextmenue
        if kontextmenue.uebergeben(WS.nachricht_fuer(self.bericht_pfad, self.ergebnis)):
            self.stand.setText("💬 An NemiCLI übergeben – die Persönlichkeit sieht sich den Bericht an.")
            self.fragen_k.setEnabled(False)

    def closeEvent(self, ev):
        self._stopp.set()
        ev.accept()


def starten(pfade: list[str]) -> int:
    app = app_vorbereiten("NemiCLI.Waechterscan")
    fenster = ScanFenster(pfade)
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
