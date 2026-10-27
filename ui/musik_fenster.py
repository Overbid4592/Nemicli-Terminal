"""musik_fenster.py – Fenster für Songwünsche (/musik), eigener Prozess an der laufenden Sitzung.

Wunsch eintippen -> „Ausarbeiten“ (die KI der Sitzung liefert Stil, Liedtext, Tempo, Tonart) ->
Felder anpassen -> „Komponieren“ (Musik-Motor in der Sitzung) -> abspielen. Leitung wie gui_fenster.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from gui_fenster import FARBE, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label, leitung_oeffnen

STIL = f"""
QWidget#musik {{ background: {FARBE['grund']}; }}
QPlainTextEdit, QLineEdit, QSpinBox, QListWidget {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px; selection-background-color: {FARBE['lila']}; }}
QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {FARBE['cyan']}; }}
QListWidget::item {{ padding: 5px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {FARBE['flaeche_hell']}; color: {FARBE['text']}; }}
QCheckBox {{ color: {FARBE['dim']}; spacing: 8px; }}
"""


def _feld(platzhalter: str = "", breite: int | None = None) -> QLineEdit:
    f = QLineEdit()
    f.setPlaceholderText(platzhalter)
    if breite:
        f.setFixedWidth(breite)
    return f


def _text(platzhalter: str, hoehe: int | None = None, mono: bool = False) -> QPlainTextEdit:
    t = QPlainTextEdit()
    t.setPlaceholderText(platzhalter)
    if mono:
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSizeF(10)
        t.setFont(f)
    if hoehe:
        t.setFixedHeight(hoehe)
    return t


def _zeile(*teile) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(8)
    for t in teile:
        if t is None:
            h.addStretch(1)
        elif isinstance(t, str):
            h.addWidget(label(t, 9.5, "dim", umbruch=False))
        else:
            h.addWidget(t)
    return h


class MusikFenster(QWidget):
    def __init__(self, leitung):
        super().__init__()
        self.leitung = leitung
        self.setObjectName("musik")
        self.setWindowTitle("NemiCLI – Musik")
        self.setStyleSheet(STIL)
        self.resize(760, 860)
        self.laeuft = False

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        self.kopf = label("🎵 Musik", 17, "text", fett=True)
        v.addWidget(self.kopf)
        v.addWidget(label("Beschreib, was du hören möchtest. Die KI macht daraus Stil und Liedtext – "
                          "beides kannst du danach ändern.", 9.5, "dim"))

        v.addWidget(label("Dein Wunsch", 10, "dim"))
        self.wunsch = _text("z. B. ein fröhlicher Sommer-Song über einen Roadtrip ans Meer, mit Frauenstimme", 84)
        v.addWidget(self.wunsch)
        self.laenge = QSpinBox()
        self.laenge.setRange(10, 240)
        self.laenge.setSingleStep(10)
        self.laenge.setValue(60)
        self.laenge.setSuffix(" s")
        self.gesang = QCheckBox("mit Gesang")
        self.gesang.setChecked(True)
        self.planer = QCheckBox("Song-Planer")
        self.planer.setChecked(True)
        self.planer.setToolTip("Plant zuerst Aufbau und Melodie (dauert etwas länger, klingt runder)")
        self.ausarbeiten_k = Knopf("Ausarbeiten", "✨")
        self.ausarbeiten_k.akzent = True
        self.ausarbeiten_k.clicked.connect(self.ausarbeiten)
        v.addLayout(_zeile("Länge", self.laenge, self.gesang, self.planer, None, self.ausarbeiten_k))

        linie = QFrame()
        linie.setFixedHeight(1)
        linie.setStyleSheet(f"background: {FARBE['rand']};")
        v.addSpacing(6)
        v.addWidget(linie)
        v.addSpacing(4)

        self.titel = _feld("Titel")
        v.addLayout(_zeile("Titel", self.titel))
        v.addWidget(label("Stil (englisch – Genre, Instrumente, Stimmung, Stimme)", 10, "dim"))
        self.stil = _text("upbeat pop, acoustic guitar, warm synth pads, female vocals, summer vibe", 64)
        v.addWidget(self.stil)
        v.addWidget(label("Liedtext  ([Verse] [Chorus] [Bridge] … · ohne Gesang: [Instrumental])", 10, "dim"))
        self.text = _text("[Verse]\n…\n\n[Chorus]\n…", mono=True)
        v.addWidget(self.text, 1)
        self.bpm = _feld("auto", 70)
        self.tonart = _feld("auto", 110)
        self.sprache = _feld("de", 56)
        self.seed = _feld("zufällig", 100)
        self.komponieren_k = Knopf("Komponieren", "🎵")
        self.komponieren_k.akzent = True
        self.komponieren_k.clicked.connect(self.komponieren)
        v.addLayout(_zeile("Tempo", self.bpm, "Tonart", self.tonart, "Sprache", self.sprache,
                           "Seed", self.seed, None, self.komponieren_k))
        self.status = label("", 10, "dim")
        v.addWidget(self.status)

        v.addWidget(label("Songs", 10, "dim"))
        self.liste = QListWidget()
        self.liste.setFixedHeight(150)
        self.liste.itemDoubleClicked.connect(lambda it: self._oeffnen(it.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(self.liste)
        abspielen = Knopf("Abspielen", "▶", klein=True)
        abspielen.clicked.connect(self._abspielen)
        ordner = Knopf("Ordner", "📂", klein=True)
        ordner.clicked.connect(self._ordner)
        self.cover_k = Knopf("Als Cover", "🎙", klein=True)
        self.cover_k.setToolTip("Den markierten Song im Stil oben neu einspielen – Melodie und Aufbau bleiben")
        self.cover_k.clicked.connect(lambda: self.covern(None))
        self.cover_datei_k = Knopf("Cover aus Datei …", "📁", klein=True)
        self.cover_datei_k.setToolTip("Eine eigene Audiodatei (WAV, MP3, M4A, FLAC) im Stil oben neu einspielen")
        self.cover_datei_k.clicked.connect(self._cover_datei)
        self.naehe = QSpinBox()
        self.naehe.setRange(0, 90)
        self.naehe.setSingleStep(10)
        self.naehe.setSuffix(" %")
        self.naehe.setToolTip("Nähe zum Original: 0 = nur Melodie und Aufbau, höher = auch Klang und Instrumente")
        v.addLayout(_zeile(abspielen, ordner, None, self.cover_k, self.cover_datei_k, "Nähe", self.naehe))

        leitung.ereignis.connect(self._ereignis)
        leitung.getrennt.connect(lambda: self._melden("Verbindung zur Sitzung getrennt – /musik neu öffnen.",
                                                      "fehler", False))
        leitung.senden({"op": "songs"})

    # -- Aufträge an die Sitzung ---------------------------------------------------
    def _entwurf(self) -> dict:
        return {"titel": self.titel.text().strip(), "stil": self.stil.toPlainText().strip(),
                "text": self.text.toPlainText().strip(), "sprache": self.sprache.text().strip(),
                "bpm": self.bpm.text().strip(), "tonart": self.tonart.text().strip()}

    def ausarbeiten(self):
        wunsch = self.wunsch.toPlainText().strip()
        if not wunsch or self.laeuft:
            return
        entwurf = self._entwurf() if self.stil.toPlainText().strip() else None
        self._melden("✨ sende den Wunsch …", "dim", True)
        self.leitung.senden({"op": "ausarbeiten", "wunsch": wunsch, "sekunden": self.laenge.value(),
                             "gesang": self.gesang.isChecked(), "entwurf": entwurf})

    def komponieren(self):
        if self.laeuft:
            return
        e = self._entwurf()
        if not e["stil"]:
            self._melden("Erst einen Stil eintragen – oder „Ausarbeiten“ drücken.", "warn", False)
            return
        try:
            bpm = int(e["bpm"]) if e["bpm"] else None
            seed = int(self.seed.text()) if self.seed.text().strip() else None
        except ValueError:
            self._melden("Tempo und Seed sind Zahlen.", "warn", False)
            return
        text = e["text"] if self.gesang.isChecked() else "[Instrumental]"
        self._melden("🎵 starte …", "dim", True)
        self.leitung.senden({"op": "komponieren", "titel": e["titel"], "stil": e["stil"], "text": text,
                             "sekunden": self.laenge.value(), "bpm": bpm, "tonart": e["tonart"],
                             "sprache": e["sprache"], "seed": seed, "planen": self.planer.isChecked()})

    def covern(self, vorlage: str | None):
        if self.laeuft:
            return
        if vorlage is None:
            it = self.liste.currentItem()
            if not it:
                self._melden("Erst einen Song in der Liste markieren.", "warn", False)
                return
            vorlage = it.data(Qt.ItemDataRole.UserRole)
        e = self._entwurf()
        if not e["stil"]:
            self._melden("Erst den neuen Stil oben eintragen.", "warn", False)
            return
        try:
            seed = int(self.seed.text()) if self.seed.text().strip() else None
        except ValueError:
            self._melden("Seed ist eine Zahl.", "warn", False)
            return
        self._melden(f"🎙 Cover von {Path(vorlage).stem} …", "dim", True)
        self.leitung.senden({"op": "covern", "vorlage": vorlage, "stil": e["stil"], "titel": e["titel"],
                             "naehe": self.naehe.value() / 100, "seed": seed,
                             "text": None if self.gesang.isChecked() else "[Instrumental]"})

    def _cover_datei(self):
        from PySide6.QtWidgets import QFileDialog
        pfad, _ = QFileDialog.getOpenFileName(self, "Vorlage für das Cover", "",
                                              "Audio (*.wav *.mp3 *.m4a *.aac *.flac *.wma *.ogg *.opus)")
        if pfad:
            self.covern(pfad)

    # -- Ereignisse der Sitzung ------------------------------------------------------
    def _ereignis(self, ev: dict):
        t = ev.get("t")
        if t == "status":
            self._melden(str(ev.get("text") or ""), "dim", bool(ev.get("laeuft")))
        elif t == "fehler":
            self._melden("⚠ " + str(ev.get("text") or ""), "fehler", False)
        elif t == "entwurf":
            self.titel.setText(str(ev.get("titel") or ""))
            self.stil.setPlainText(str(ev.get("stil") or ""))
            self.text.setPlainText(str(ev.get("text") or ""))
            self.sprache.setText(str(ev.get("sprache") or ""))
            self.bpm.setText(str(ev.get("bpm") or ""))
            self.tonart.setText(str(ev.get("tonart") or ""))
            try:
                if ev.get("sekunden"):
                    self.laenge.setValue(int(float(str(ev["sekunden"]).rstrip("s "))))
            except (TypeError, ValueError):
                pass                                    # „60s“ o. ä. vom Entwurf: Länge bleibt
            self._melden("✨ Entwurf da – anpassen oder gleich komponieren.", "ok", False)
        elif t == "fertig":
            pfad = str(ev.get("pfad") or "")
            self._eintrag(pfad, oben=True)
            self._melden(f"✅ fertig: {ev.get('sekunden')} s Musik in {ev.get('rechnen')} s gerechnet "
                         f"(Seed {ev.get('seed')})", "ok", False)
            self._oeffnen(pfad)
        elif t == "songs":
            if ev.get("name"):
                self.kopf.setText(f"🎵 Musik mit {ev['name']}")
            self.liste.clear()
            for p in ev.get("liste") or []:
                self._eintrag(str(p))
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()

    # -- Anzeige ---------------------------------------------------------------------
    def _melden(self, text: str, farbname: str, laeuft: bool):
        self.laeuft = laeuft
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(self.status.foregroundRole(), farbe(farbname))
        self.status.setPalette(pal)
        for k in (self.ausarbeiten_k, self.komponieren_k, self.cover_k, self.cover_datei_k):
            k.setEnabled(not laeuft)

    def _eintrag(self, pfad: str, oben: bool = False):
        it = QListWidgetItem("🎵  " + Path(pfad).stem)
        it.setData(Qt.ItemDataRole.UserRole, pfad)
        if oben:
            self.liste.insertItem(0, it)
            self.liste.setCurrentRow(0)
        else:
            self.liste.addItem(it)

    @staticmethod
    def _oeffnen(pfad: str):
        # Nur Klang öffnen: der Pfad kommt aus der Sitzung – eine .bat oder .lnk würde sonst gestartet.
        if pfad and Path(pfad).suffix.lower() in (".wav", ".mp3", ".flac", ".ogg", ".m4a") and Path(pfad).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(pfad))

    def _abspielen(self):
        it = self.liste.currentItem() or self.liste.item(0)
        if it:
            self._oeffnen(it.data(Qt.ItemDataRole.UserRole))

    def _ordner(self):
        it = self.liste.currentItem() or self.liste.item(0)
        if it:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(it.data(Qt.ItemDataRole.UserRole)).parent)))


def starten() -> int:
    app = app_vorbereiten("NemiCLI.Musik")
    leitung = leitung_oeffnen("/musik")
    if leitung is None:
        return 1
    fenster = MusikFenster(leitung)
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()

