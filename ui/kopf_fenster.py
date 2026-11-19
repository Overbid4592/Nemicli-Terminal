"""kopf_fenster.py – Kopf-Fenster (/kern kopf): Gesichter für den Kern finden, eigener Prozess an der Sitzung.

Wünsche (deutsch) eintippen → „Gesichter erschaffen“: Die KI der Sitzung schreibt Varianten des Kerns, in denen
nur das Gesicht anders ist; der Bild-Motor malt jede als Nahaufnahme mit eigenem Seed. Ein Klick auf ein Gesicht
übernimmt Kern und Seed (nach Rückfrage). Mit „Gesichts-Korrektur“ wird jedes Gesicht danach vergrößert
nachgemalt; jede Karte schaltet dann zwischen vorher und nachher um. Leitung wie gui_fenster.
"""
from __future__ import annotations

from pathlib import Path

from gui_fenster import (
    FARBE,
    Knopf,
    _dunkler_rahmen,
    app_vorbereiten,
    farbe,
    label,
    leitung_oeffnen,
    vorschau,
)
from kern_fenster import ja_nein
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

BILD = 230
SPALTEN = 3

STIL = f"""
QWidget#kopf {{ background: {FARBE['grund']}; }}
QPlainTextEdit {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px; selection-background-color: {FARBE['lila']}; }}
QPlainTextEdit:focus {{ border: 1px solid {FARBE['cyan']}; }}
QFrame#karte {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; border-radius: 12px; }}
QFrame#karte:hover {{ border: 1px solid {FARBE['cyan']}; }}
QFrame#karte[gewaehlt="true"] {{ border: 2px solid {FARBE['lila']}; }}
QCheckBox {{ color: {FARBE['dim']}; spacing: 8px; }}
"""


class Karte(QFrame):
    """Ein Gesicht: Bild, Titel, bei Korrektur „vorher/nachher“; Klick aufs Bild wählt es."""
    gewaehlt = Signal(int)

    def __init__(self, i: int):
        super().__init__()
        self.i = i
        self.pfad = ""
        self.besser = ""
        self.nachher = True
        self.setObjectName("karte")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        self.bild = QLabel()
        self.bild.setFixedSize(BILD, BILD)
        self.bild.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bild.setStyleSheet(f"background: {FARBE['grund']}; border-radius: 8px; color: {FARBE['dim']};")
        v.addWidget(self.bild, 0, Qt.AlignmentFlag.AlignHCenter)
        self.titel = label("", 10, "text", fett=True)
        self.titel.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self.titel)
        self.umschalten_k = Knopf("", klein=True, mitte=True)
        self.umschalten_k.clicked.connect(self.umschalten)
        v.addWidget(self.umschalten_k)
        self.leeren()

    def leeren(self, titel: str = "", kern: str = "") -> None:
        self.pfad = self.besser = ""
        self.nachher = True
        self.umschalten_k.setVisible(False)
        self.bild.clear()
        self.bild.setText("🎨 wartet …" if titel else "")
        self.titel.setText(titel)
        self.setToolTip(kern)
        self.setVisible(bool(titel))
        self.markieren(False)

    def malt(self) -> None:
        if not self.pfad:
            self.bild.setText("🎨 malt …")

    def bild_setzen(self, pfad: str, besser: str = "") -> None:
        self.pfad, self.besser, self.nachher = pfad, besser, True
        self.umschalten_k.setVisible(bool(besser))
        self.zeigen()

    def zeigen(self) -> None:
        nachher = bool(self.besser) and self.nachher
        self.bild.setText("")
        self.bild.setPixmap(vorschau(self.besser if nachher else self.pfad, BILD))
        self.umschalten_k.setText("✨ nachher – vorher zeigen" if nachher else "👁 vorher – nachher zeigen")

    def umschalten(self) -> None:
        if self.besser:
            self.nachher = not self.nachher
            self.zeigen()

    def markieren(self, an: bool) -> None:
        self.setProperty("gewaehlt", "true" if an else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.pfad:
            self.gewaehlt.emit(self.i)
        super().mouseReleaseEvent(ev)


class KopfFenster(QWidget):
    def __init__(self, leitung, anzahl: int = 6):
        super().__init__()
        self.leitung = leitung
        self.setObjectName("kopf")
        self.setWindowTitle("NemiCLI – Gesicht finden")
        self.setStyleSheet(STIL)
        self.name = ""
        self.nr = 0                       # Runde der Sitzung: Bilder alter Runden zählen nicht
        self.varianten: list[dict] = []
        self.laeuft = False
        self.verbunden = True

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        self.kopf = label("🙂 Gesicht finden", 17, "text", fett=True)
        v.addWidget(self.kopf)
        v.addWidget(label("Beschreib auf Deutsch, wie das Gesicht sein soll. Die KI macht daraus Varianten – Haare "
                          "und Körper bleiben, wie sie sind. Gefällt dir ein Gesicht, klick es an: Kern und Seed "
                          "werden übernommen, der alte Kern bleibt gemerkt (/kern zurueck).", 9.5, "dim"))
        self.kern = label("", 9, "blass")
        v.addWidget(self.kern)
        self.hinweis = label("", 9.5, "warn")
        self.hinweis.setVisible(False)
        v.addWidget(self.hinweis)

        self.wunsch = QPlainTextEdit()
        self.wunsch.setPlaceholderText("z. B. rundes Gesicht, Stupsnase, volle Lippen, große Augen, ein paar "
                                       "Sommersprossen – oder leer lassen für Überraschungen")
        self.wunsch.setFixedHeight(70)
        f = QFont(self.wunsch.font())
        f.setPointSizeF(10.5)
        self.wunsch.setFont(f)
        v.addWidget(self.wunsch)
        h = QHBoxLayout()
        self.status = label("", 10, "dim")
        h.addWidget(self.status, 1)
        self.korrektur = QCheckBox("✨ Gesichts-Korrektur")
        self.korrektur.setToolTip("Jedes Gesicht nach dem Malen vergrößert nachmalen – je Karte vorher/nachher. "
                                  "Gilt nur hier; für alle Bilder: /bild gesicht")
        h.addWidget(self.korrektur)
        self.erschaffen_k = Knopf("Gesichter erschaffen", "✨")
        self.erschaffen_k.akzent = True
        self.erschaffen_k.clicked.connect(self.erschaffen)
        h.addWidget(self.erschaffen_k)
        v.addLayout(h)

        raster = QGridLayout()
        raster.setSpacing(12)
        self.karten = [Karte(i) for i in range(anzahl)]
        for k in self.karten:
            k.gewaehlt.connect(self.waehlen)
            raster.addWidget(k, k.i // SPALTEN, k.i % SPALTEN)
        v.addLayout(raster)
        v.addStretch(1)
        self.resize(SPALTEN * (BILD + 34) + 44, 900)

        leitung.ereignis.connect(self._ereignis)
        leitung.getrennt.connect(self._getrennt)
        leitung.senden({"op": "start"})

    # -- Aufträge an die Sitzung ---------------------------------------------------
    def erschaffen(self):
        if self.laeuft or not self.verbunden:
            return
        self._melden("✨ Die KI denkt sich Gesichter aus …", "dim", True)
        self.leitung.senden({"op": "erschaffen", "wunsch": self.wunsch.toPlainText().strip(),
                             "korrektur": self.korrektur.isChecked()})

    def waehlen(self, i: int):
        if not self.verbunden or i >= len(self.varianten):
            return
        for k in self.karten:
            k.markieren(k.i == i)
        titel = self.varianten[i].get("titel") or "dieses Gesicht"
        if ja_nein(self, f"„{titel}“ übernehmen?\n\nKern und Seed werden ersetzt, Haare und Körper bleiben. Der "
                         "alte Kern bleibt gemerkt (/kern zurueck).", "Übernehmen", "Abbrechen"):
            self._melden("💾 übernehme …", "dim", self.laeuft)
            self.leitung.senden({"op": "uebernehmen", "nr": self.nr, "i": i})
        else:
            self.karten[i].markieren(False)

    # -- Ereignisse der Sitzung ------------------------------------------------------
    def _ereignis(self, ev: dict):
        t = ev.get("t")
        if t == "start":
            self.name = str(ev.get("name") or "")
            self.kopf.setText(f"🙂 Gesicht für {self.name}" if self.name else "🙂 Gesicht finden")
            self.korrektur.setChecked(bool(ev.get("korrektur")))
            self.kern.setText("Kern jetzt: " + (str(ev.get("kern") or "") or "– noch keiner –"))
            hinweise = [str(ev[k]) for k in ("malen", "lora") if ev.get(k)]
            self.hinweis.setText("\n".join(hinweise))
            self.hinweis.setVisible(bool(hinweise))
            if not ev.get("kern"):
                self._melden("Erst einen Kern eintragen (/kern → Kern ändern).", "warn", False)
                self.erschaffen_k.setEnabled(False)
        elif t == "status":
            self._melden(str(ev.get("text") or ""), "dim", bool(ev.get("laeuft")))
        elif t == "fehler":
            self._melden("⚠ " + str(ev.get("text") or ""), "fehler", False)
        elif t == "varianten":
            self.nr = int(ev.get("nr") or 0)
            self.varianten = [d for d in ev.get("liste") or [] if isinstance(d, dict)]
            for k in self.karten:
                d = self.varianten[k.i] if k.i < len(self.varianten) else {}
                k.leeren(str(d.get("titel") or ""), str(d.get("kern") or ""))
        elif t == "malt" and ev.get("nr") == self.nr:
            if 0 <= int(ev.get("i", -1)) < len(self.karten):
                self.karten[int(ev["i"])].malt()
        elif t == "bild" and ev.get("nr") == self.nr:
            i, pfad, besser = int(ev.get("i", -1)), str(ev.get("pfad") or ""), str(ev.get("besser") or "")
            if 0 <= i < len(self.karten) and _png(pfad):
                self.karten[i].bild_setzen(pfad, besser if _png(besser) else "")
        elif t == "hinweis":
            self._melden("⚠ " + str(ev.get("text") or ""), "warn", self.laeuft)
        elif t == "uebernommen":
            self.kern.setText("Kern jetzt: " + str(ev.get("kern") or ""))
            self._melden(f"✅ „{ev.get('titel') or 'Gesicht'}“ übernommen (Seed {ev.get('seed')}).", "ok", self.laeuft)
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()
            if ev.get("name") and ev.get("name") != self.name and not self.laeuft:
                self.leitung.senden({"op": "start"})

    # -- Anzeige ---------------------------------------------------------------------
    def _getrennt(self):
        self.verbunden = False
        self.erschaffen_k.setEnabled(False)
        self._melden("Verbindung zur Sitzung getrennt – im Terminal /kern kopf neu öffnen.", "fehler", False)

    def _melden(self, text: str, farbname: str, laeuft: bool):
        self.laeuft = laeuft
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(self.status.foregroundRole(), farbe(farbname))
        self.status.setPalette(pal)
        if self.verbunden:
            self.erschaffen_k.setEnabled(not laeuft)


def _png(pfad: str) -> bool:
    return bool(pfad) and Path(pfad).suffix.lower() == ".png" and Path(pfad).is_file()


def starten() -> int:
    app = app_vorbereiten("NemiCLI.Kopf")
    leitung = leitung_oeffnen("/kern kopf")
    if leitung is None:
        return 1
    fenster = KopfFenster(leitung)
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
