"""
nemicli_setup.py - Die Setup-Datei: NemiCLI installieren oder aktualisieren (Fenster mit PySide6).

Installiert immer in den festen Programmordner `.nemicli` im Benutzerordner. Ist NemiCLI dort
schon drauf, ersetzt es nur die Programmteile; auf Wunsch legt es eine Desktop-Verknüpfung an.
Läuft NemiCLI noch aus dem Ordner, wird nichts beendet – das Fenster bittet ums Schließen.

  --nutzlast <zip>      anderes Paket (Entwicklung; sonst nutzlast.zip im Bündel)
  --pruefen <datei>     nur prüfen (Paket lesbar, Stand im Programmordner) und Bericht schreiben, nichts ändern
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import aufspielen as A


def _buendel() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def nutzlast() -> Path:
    if "--nutzlast" in sys.argv:
        return Path(sys.argv[sys.argv.index("--nutzlast") + 1])
    return _buendel() / "nutzlast.zip"


def _zahlen(build: str) -> tuple:
    return tuple(int(t) for t in build.split(".") if t.isdigit())


def pruefen(bericht: Path) -> int:
    """Prüft das Paket und die Suche, ohne etwas zu ändern."""
    zeilen = []
    try:
        paket = nutzlast()
        zeilen.append(f"Paket: {paket.name}, Build {A.nutzlast_build(paket)}")
        with zipfile.ZipFile(paket) as z:
            kaputt = z.testzip()
            zeilen.append(f"Dateien: {len(z.namelist())}, " + ("alle lesbar" if kaputt is None else f"KAPUTT: {kaputt}"))
        i = A.installation()
        zeilen.append(f"Programmordner: {i.ordner} – " + (f"Build {i.build}" if i.build else "noch kein Programm"))
        ok = kaputt is None
    except Exception as exc:
        zeilen.append(f"FEHLER: {exc}")
        ok = False
    zeilen.append("Ergebnis: " + ("ok" if ok else "Fehler"))
    bericht.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return 0 if ok else 1


def fenster() -> int:
    from PySide6 import QtCore, QtGui, QtWidgets

    class Arbeiter(QtCore.QThread):
        fortschritt = QtCore.Signal(int)
        fertig = QtCore.Signal(str)

        def __init__(self, paket: Path, ziel: Path):
            super().__init__()
            self.paket, self.ziel = paket, ziel

        def run(self):
            try:
                A.aufspielen(self.paket, self.ziel, lambda f, g: self.fortschritt.emit(int(f * 100 / g)))
                self.fertig.emit("")
            except Exception as exc:
                self.fertig.emit(str(exc) or type(exc).__name__)

    class Setup(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.paket = nutzlast()
            self.build = A.nutzlast_build(self.paket)
            self.ziel: Path | None = None
            self.setWindowTitle(f"NemiCLI {self.build} – Setup")
            if (icon := _buendel() / "nemicli.ico").exists():
                self.setWindowIcon(QtGui.QIcon(str(icon)))
            self.resize(720, 460)
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(22, 20, 22, 20)
            lay.setSpacing(12)
            kopf = QtWidgets.QLabel(f"NemiCLI {self.build}")
            kopf.setObjectName("kopf")
            lay.addWidget(kopf)
            self.inst = A.installation()
            ort = QtWidgets.QLabel(
                f"Programmordner: {self.inst.ordner}\n"
                + (f"Dort ist NemiCLI {self.inst.build} drauf – wird aktualisiert." if self.inst.build
                   else "Dort ist noch kein NemiCLI – wird neu installiert."))
            ort.setWordWrap(True)
            ort.setObjectName("ort")
            lay.addWidget(ort)
            lay.addStretch(1)
            hinweis = QtWidgets.QLabel("Ersetzt werden nur die Programmteile (NemiCLI.exe, NemiCLIc.exe, NemiCLIT2, "
                                       "chrome-erweiterung). venv, Modelle, Config und Daten im Ordner bleiben.")
            hinweis.setWordWrap(True)
            hinweis.setObjectName("leise")
            lay.addWidget(hinweis)
            self.verknuepfung = QtWidgets.QCheckBox("Verknüpfung auf dem Desktop anlegen")
            self.verknuepfung.setChecked(True)
            lay.addWidget(self.verknuepfung)
            self.balken = QtWidgets.QProgressBar()
            self.balken.hide()
            lay.addWidget(self.balken)
            self.meldung = QtWidgets.QLabel("")
            self.meldung.setWordWrap(True)
            lay.addWidget(self.meldung)
            knoepfe = QtWidgets.QHBoxLayout()
            knoepfe.addStretch(1)
            self.los = QtWidgets.QPushButton("Los")
            self.los.setObjectName("haupt")
            self.los.clicked.connect(self._los)
            self.zu = QtWidgets.QPushButton("Schließen")
            self.zu.clicked.connect(self.close)
            knoepfe.addWidget(self.zu)
            knoepfe.addWidget(self.los)
            lay.addLayout(knoepfe)
            self.los.setText("Aktualisieren" if self.inst.build else "Installieren")

        def _frage(self, text: str) -> bool:
            antwort = QtWidgets.QMessageBox.question(self, "NemiCLI Setup", text)
            return antwort == QtWidgets.QMessageBox.Yes

        def _los(self):
            i = self.inst = A.installation()             # neu lesen: zwischendurch geändert?
            laufend = A.laeuft(i.ordner)
            if laufend:
                QtWidgets.QMessageBox.information(
                    self, "NemiCLI Setup",
                    f"NemiCLI läuft noch aus {i.ordner}:\n{', '.join(laufend)}\n\nBitte NemiCLI und die Wache "
                    "(Symbol unten rechts → Beenden) schließen und dann nochmal auf „Aktualisieren“ klicken.")
                return
            if i.build and _zahlen(i.build) == _zahlen(self.build) and not self._frage(
                    f"In {i.ordner} ist NemiCLI {i.build} schon drauf. Trotzdem neu aufspielen?"):
                return
            if i.build and _zahlen(i.build) > _zahlen(self.build) and not self._frage(
                    f"In {i.ordner} liegt die neuere Version {i.build}. Wirklich die ältere {self.build} aufspielen?"):
                return
            self.ziel = i.ordner
            self.los.setEnabled(False)
            self.zu.setEnabled(False)
            self.balken.setValue(0)
            self.balken.show()
            self.meldung.setText(f"Packe aus nach {i.ordner} …")
            self.arbeiter = Arbeiter(self.paket, i.ordner)
            self.arbeiter.fortschritt.connect(self.balken.setValue)
            self.arbeiter.fertig.connect(self._fertig)
            self.arbeiter.start()

        def _fertig(self, fehler: str):
            self.zu.setEnabled(True)
            if fehler:
                self.balken.hide()
                self.los.setEnabled(True)
                self.meldung.setText(f"⚠ Das ging nicht: {fehler}\nDie bisherigen Programmteile sind unverändert.")
                return
            self.balken.setValue(100)
            text = f"✅ Fertig: NemiCLI {self.build} liegt in {self.ziel}."
            if self.verknuepfung.isChecked():
                try:
                    A.verknuepfung(self.ziel / "NemiCLI.exe")
                    text += "\nDie Verknüpfung liegt auf dem Desktop."
                except OSError as exc:
                    text += f"\n⚠ Verknüpfung ging nicht: {exc}"
            self.meldung.setText(text)
            self.los.setText("NemiCLI starten")
            self.los.setEnabled(True)
            self.los.clicked.disconnect()
            self.los.clicked.connect(self._starten)

        def _starten(self):
            subprocess.Popen([str(self.ziel / "NemiCLI.exe")], cwd=str(self.ziel))
            self.close()

    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet("""
        QWidget { background: #0d1117; color: #e6edf3; font-size: 11pt; }
        QLabel#kopf { font-size: 18pt; font-weight: bold; color: #58a6ff; }
        QLabel#leise { color: #8b949e; }
        QLabel#ort { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
        QPushButton { background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 8px 18px; }
        QPushButton#haupt { background: #238636; border-color: #2ea043; font-weight: bold; }
        QPushButton:disabled { color: #6e7681; }
        QProgressBar { border: 1px solid #30363d; border-radius: 6px; text-align: center; height: 20px; }
        QProgressBar::chunk { background: #2ea043; border-radius: 6px; }
    """)
    w = Setup()
    w.show()
    return app.exec()


def main() -> int:
    if "--pruefen" in sys.argv:
        return pruefen(Path(sys.argv[sys.argv.index("--pruefen") + 1]))
    return fenster()


if __name__ == "__main__":
    raise SystemExit(main())
