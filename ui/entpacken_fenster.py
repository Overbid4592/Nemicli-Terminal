"""entpacken_fenster.py – Sichtbares Fenster zum Entpacken (`--entpacken`, tools/entpacken.py).

Aufruf: --entpacken <quelle> <ziel> <ergebnis.json> [--profil NAME] [--einlesen]
Schritt 1: Unpackt entpackt, sortiert und prüft (eigener Faden, Anzeige über Qt-Signale).
Schritt 2 (nur mit --einlesen und Code 0/1): indexdb.entpackt_einlesen liest das Ergebnis ins Gedächtnis.
Die Schutzfrage von Unpackt (verdächtiges Archiv) wird immer sichtbar gestellt; ohne Antwort gilt Nein.
Schließen während der Arbeit bricht ab. Das Ergebnis-JSON enthält nur Zahlen und Status.
"""
from __future__ import annotations

import argparse
import html
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

import entpacken
from gui_fenster import FARBE, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label


STIL = f"""
QWidget#entpacken {{ background: {FARBE['grund']}; }}
QPlainTextEdit {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px; selection-background-color: {FARBE['lila']}; }}
QProgressBar {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 9px; height: 18px; text-align: center; font-weight: bold; }}
QProgressBar::chunk {{ border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {FARBE['cyan']}, stop:1 {FARBE['lila']}); }}
"""

ART_FARBE = {"info": "text", "head": "cyan", "ok": "ok", "warn": "warn", "error": "fehler"}


def argumente(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="--entpacken", add_help=False)
    p.add_argument("quelle")
    p.add_argument("ziel")
    p.add_argument("ergebnis")
    p.add_argument("--profil", default=None)
    p.add_argument("--einlesen", action="store_true")
    return p.parse_args(argv)


class Bote(QObject):
    """Signale vom Arbeitsfaden in den GUI-Faden."""
    protokoll = Signal(str, str)
    fortschritt = Signal(int, int, str)
    datei = Signal(str, int, int)
    phase = Signal(str)
    frage = Signal(str)
    fertig = Signal(dict)


class EntpackenFenster(QWidget):
    def __init__(self, quelle: Path, ziel: Path, ergebnis: Path, profil: str | None, einlesen: bool):
        super().__init__()
        self.quelle, self.ziel, self.ergebnisdatei = quelle, ziel, ergebnis
        self.profil, self.einlesen = profil, einlesen
        self.abbruch = threading.Event()
        self.antwort_da = threading.Event()
        self.antwort = False
        self.laeuft = False
        self.schliessen_nach_ende = False
        self._box: QMessageBox | None = None
        self.bote = Bote()
        self.bote.protokoll.connect(self._protokoll)
        self.bote.fortschritt.connect(self._fortschritt)
        self.bote.datei.connect(self._datei)
        self.bote.phase.connect(self._phase)
        self.bote.frage.connect(self._frage_zeigen)
        self.bote.fertig.connect(self._fertig)

        self.setObjectName("entpacken")
        self.setWindowTitle(f"NemiCLI – Entpacken: {quelle.name}")
        self.setStyleSheet(STIL)
        self.resize(780, 640)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        v.addWidget(label(f"📦 {quelle.name}", 16, "text", fett=True))
        v.addWidget(label(f"Ziel: {ziel}", 9.5, "dim"))
        self.phase_text = label("", 10.5, "cyan", fett=True)
        v.addWidget(self.phase_text)
        self.balken = QProgressBar()
        self.balken.setRange(0, 1000)
        self.balken.setFormat("%p %")
        v.addWidget(self.balken)
        self.status = label("Startet …", 10, "dim")
        v.addWidget(self.status)
        self.datei_text = label("", 10, "text")
        v.addWidget(self.datei_text)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        schrift = QFont("Cascadia Mono")
        schrift.setStyleHint(QFont.StyleHint.Monospace)
        schrift.setPointSizeF(9.5)
        self.log.setFont(schrift)
        v.addWidget(self.log, 1)

        self.ende_text = label("", 11, "text", fett=True)
        self.ende_text.hide()
        v.addWidget(self.ende_text)

        knoepfe = QHBoxLayout()
        knoepfe.addStretch(1)
        self.ordner_k = Knopf("Ordner zeigen", "📂")
        self.ordner_k.clicked.connect(self._ordner_zeigen)
        self.ordner_k.hide()
        self.abbrechen_k = Knopf("Abbrechen", "✕")
        self.abbrechen_k.clicked.connect(self._abbrechen)
        self.schliessen_k = Knopf("Schließen", "✓")
        self.schliessen_k.akzent = True
        self.schliessen_k.clicked.connect(self.close)
        self.schliessen_k.hide()
        for k in (self.ordner_k, self.abbrechen_k, self.schliessen_k):
            knoepfe.addWidget(k)
        v.addLayout(knoepfe)

    # -- Arbeit (eigener Faden) --------------------------------------------------------
    def starten(self) -> None:
        self.laeuft = True
        threading.Thread(target=self._arbeit, name="entpacken", daemon=True).start()

    def _ergebnis_melden(self, ergebnis: dict) -> None:
        """Ergebnis einmal schreiben: der Aufrufer wartet nur bis hierher, das Einlesen läuft sichtbar weiter."""
        if self._gemeldet:
            return
        self._gemeldet = True
        try:
            entpacken.ergebnis_schreiben(self.ergebnisdatei, ergebnis)
        except Exception as e:
            self.bote.protokoll.emit(f"Ergebnis nicht gespeichert: {e}", "error")

    def _arbeit(self) -> None:
        b = self.bote
        ergebnis = entpacken.leeres_ergebnis(self.ziel, code=2)
        self._gemeldet = False
        try:
            b.phase.emit("Schritt 1" + (" von 2" if self.einlesen else "") + ": Entpacken, sortieren, prüfen")
            modul = entpacken.laden()
            code = modul.run(self.quelle, self.ziel, log=b.protokoll.emit, progress=b.fortschritt.emit,
                             cancel=self.abbruch, confirm=self._frage)
            ergebnis = entpacken.auswerten(self.ziel, code)
            self._ergebnis_melden(ergebnis)
            if self.einlesen and code in entpacken.CODES_FERTIG:
                if self.abbruch.is_set():
                    b.protokoll.emit("Einlesen übersprungen (abgebrochen).", "warn")
                else:
                    ergebnis["eingelesen"] = self._einlesen()
        except Exception as e:
            b.protokoll.emit(f"Fehler: {e}", "error")
        finally:
            self._ergebnis_melden(ergebnis)
            b.fertig.emit(ergebnis)

    def _einlesen(self) -> int:
        b = self.bote
        b.phase.emit("Schritt 2 von 2: Einlesen ins Gedächtnis")
        b.fortschritt.emit(0, 1000, "Gedächtnis wird vorbereitet …")
        b.protokoll.emit("Einlesen ins Gedächtnis …", "head")
        try:
            import indexdb
            einlesen = getattr(indexdb, "entpackt_einlesen", None)
        except Exception as e:
            b.protokoll.emit(f"Gedächtnis nicht verfügbar: {e}", "warn")
            return 0
        if einlesen is None:
            b.protokoll.emit("Einlesen ins Gedächtnis ist in dieser Fassung nicht verfügbar.", "warn")
            return 0

        def fortschritt(name, erledigt, gesamt) -> None:
            b.datei.emit(str(name), int(erledigt), int(gesamt))

        b.protokoll.emit(f"Rechnet auf: {self._geraet_festlegen()}", "info")
        import profilordner
        try:
            r = einlesen(self.ziel, self.profil or profilordner.aktiv(), fortschritt=fortschritt, stopp=self.abbruch)
        except Exception as e:
            b.protokoll.emit(f"Einlesen fehlgeschlagen: {e}", "error")
            return 0
        try:
            n = int((r or {}).get("eingelesen", 0))
        except (TypeError, ValueError, AttributeError):
            n = 0
        if self.abbruch.is_set():
            b.protokoll.emit(f"Einlesen abgebrochen – {n} Dateien sind schon im Gedächtnis.", "warn")
        else:
            b.protokoll.emit(f"Ins Gedächtnis eingelesen: {n} Dateien ✓", "ok")
        return n

    @staticmethod
    def _geraet_festlegen() -> str:
        """Wie der Bibliothekar: Einlesen auf der GPU, wenn eine da ist und genug Grafikspeicher frei ist
        (nicht beim Malen); sonst CPU. Einzelne Dateien erreichen die „auto“-Schwelle nie."""
        try:
            import memory
            ja, grund = (memory.gpu_platz() if memory.hat_gpu() else (False, "keine Grafikkarte"))
            memory.NUR_GPU = bool(ja)
            return "GPU" if ja else f"CPU ({grund})" if grund else "CPU"
        except Exception:
            return "CPU"

    def _frage(self, text: str) -> bool:
        """Schutzfrage von Unpackt: sichtbar im GUI-Faden; Abbruch oder Schließen = Nein."""
        if self.abbruch.is_set():
            return False
        self.antwort, self.antwort_da = False, threading.Event()
        da = self.antwort_da
        self.bote.frage.emit(str(text))
        while not da.wait(0.2):
            if self.abbruch.is_set():
                return False
        return self.antwort is True

    # -- Anzeige (GUI-Faden) ---------------------------------------------------------
    def _frage_zeigen(self, text: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Entpacken – Schutz")
        box.setText(text)
        box.setInformativeText("Trotzdem weiter entpacken?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        self._box = box
        box.exec()
        self._box = None
        self.antwort = box.clickedButton() == box.button(QMessageBox.StandardButton.Yes) and not self.abbruch.is_set()
        self.antwort_da.set()

    def _protokoll(self, text: str, art: str) -> None:
        f = farbe(ART_FARBE.get(art, "text")).name()
        dick = "font-weight:bold;" if art == "head" else ""
        self.log.appendHtml(f'<span style="color:{f};{dick}">{html.escape(text)}</span>')

    def _fortschritt(self, erledigt: int, gesamt: int, text: str) -> None:
        self.balken.setValue(int(erledigt * 1000 / max(gesamt, 1)))
        if text:
            self.status.setText(text)

    def _datei(self, name: str, erledigt: int, gesamt: int) -> None:
        self.balken.setValue(int(erledigt * 1000 / max(gesamt, 1)))
        self.status.setText(f"{erledigt} von {gesamt}")
        self.datei_text.setText(f"📖 {name}")

    def _phase(self, text: str) -> None:
        self.phase_text.setText(text)

    def _fertig(self, ergebnis: dict) -> None:
        self.laeuft = False
        self.antwort_da.set()
        code = ergebnis.get("code", 3)
        if code in (0, 1, 5):
            self.balken.setValue(1000)
        self.phase_text.setText(entpacken.CODE_TEXT.get(code, f"Ende (Code {code})"))
        self.status.setText("")
        self.datei_text.setText("")
        zeilen = [f"Dateien: {ergebnis.get('dateien', 0)} in {ergebnis.get('ordner', 0)} Ordnern"
                  + (f" · Chats: {ergebnis['chats']}" if ergebnis.get("chats") else "")]
        if ergebnis.get("fehler"):
            zeilen.append(f"Fehler: {ergebnis['fehler']} (siehe Zuordnung.csv)")
        zeilen.append("Geprüft ✓ (Größe, CRC32, SHA-256)" if ergebnis.get("geprueft") else "Geprüft: nicht vollständig")
        if self.einlesen:
            zeilen.append(f"Ins Gedächtnis eingelesen: {ergebnis.get('eingelesen', 0)}")
        zeilen.append(f"Zielordner: {ergebnis.get('ziel') or self.ziel}")
        self.ende_text.setText("\n".join(zeilen))
        self.ende_text.show()
        self.abbrechen_k.hide()
        self.schliessen_k.show()
        if self.ziel.is_dir():
            self.ordner_k.show()
        if self.schliessen_nach_ende:
            self.close()

    def _abbrechen(self) -> None:
        if self.laeuft:
            self.abbruch.set()
            self.antwort_da.set()
            if self._box is not None:
                self._box.reject()
            self.abbrechen_k.setEnabled(False)
            self.status.setText("Wird abgebrochen …")

    def _ordner_zeigen(self) -> None:
        # Nur den Ordner zeigen – keine entpackte Datei öffnen.
        if self.ziel.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ziel)))

    def closeEvent(self, ereignis) -> None:
        if self.laeuft:                       # erst sauber abbrechen, dann schließen
            self.schliessen_nach_ende = True
            self._abbrechen()
            ereignis.ignore()
            return
        ereignis.accept()


def hauptprogramm(argv: list[str]) -> int:
    try:
        a = argumente(argv)
    except SystemExit:
        return 2
    app = app_vorbereiten("NemiCLI.Entpacken")
    fenster = EntpackenFenster(Path(a.quelle), Path(a.ziel), Path(a.ergebnis), a.profil, a.einlesen)
    _dunkler_rahmen(fenster)
    fenster.show()
    fenster.raise_()
    fenster.activateWindow()
    QTimer.singleShot(0, fenster.starten)
    app.exec()
    return 0
