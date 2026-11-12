"""lora_fenster.py – LoRA-Werkstatt (/lora), eigener Prozess an der laufenden Sitzung.

Bilder hineinziehen oder wählen -> 🔍 prüfen (doppelt, unscharf, Gesichter) -> auswählen -> Name, Schritte,
Testbilder, Probebilder, Charakter wählen -> trainieren mit Fortschritt (Prozent, Schritt, Restzeit),
⏸ Pause / ▶ Weiter -> fertig mit SHA-256, Probebildern und Bindung. Trainiert wird in der Sitzung (Krea 2);
das Fenster zeigt nur. Texte aus Dateien und aus der Sitzung erscheinen als reiner Text. Leitung wie gui_fenster.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QProgressBar, QSpinBox, QVBoxLayout,
                               QWidget)

from gui_fenster import FARBE, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label, leitung_oeffnen, vorschau

KACHEL = 112
NICHT_BINDEN = "— nicht binden —"
STIL = f"""
QWidget#lora {{ background: {FARBE['grund']}; }}
QLineEdit, QSpinBox, QComboBox, QListWidget {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px; selection-background-color: {FARBE['lila']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {FARBE['cyan']}; }}
QListWidget::item {{ padding: 4px; border-radius: 8px; color: {FARBE['dim']}; }}
QListWidget::item:selected {{ background: {FARBE['flaeche_hell']}; color: {FARBE['text']}; }}
QCheckBox {{ color: {FARBE['dim']}; spacing: 8px; }}
QProgressBar {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 9px; height: 18px; text-align: center; font-weight: bold; }}
QProgressBar::chunk {{ border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {FARBE['cyan']}, stop:1 {FARBE['lila']}); }}
"""


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


def _linie() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {FARBE['rand']};")
    return f


def _uhr(sekunden) -> str:
    try:
        s = max(0, int(sekunden))
    except (TypeError, ValueError):
        return "?"
    m = s // 60
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min" if m else f"{s} s"


class Bilderliste(QListWidget):
    """Kacheln der Bilder zum An- und Abwählen; nimmt Dateien und Ordner per Ziehen an."""

    def __init__(self, bei_dateien):
        super().__init__()
        self.bei_dateien = bei_dateien
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(KACHEL, KACHEL))
        self.setGridSize(QSize(KACHEL + 22, KACHEL + 40))
        self.setWordWrap(True)
        self.setSpacing(4)
        self.setAcceptDrops(True)
        self.setMinimumHeight(260)

    def _urls(self, ev) -> list[str]:
        md = ev.mimeData()
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []

    def dragEnterEvent(self, ev):
        if self._urls(ev):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if self._urls(ev):
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        pfade = self._urls(ev)
        if pfade:
            ev.acceptProposedAction()
            self.bei_dateien(pfade)


class LoraFenster(QWidget):
    def __init__(self, leitung):
        super().__init__()
        import lorabild
        self.lorabild = lorabild
        self.leitung = leitung
        self.setObjectName("lora")
        self.setWindowTitle("NemiCLI – LoRA-Werkstatt")
        self.setStyleSheet(STIL)
        self.resize(900, 940)
        self.laeuft = False
        self.krea_grund = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        v.addWidget(label("🧬 LoRA-Werkstatt", 17, "text", fett=True))
        v.addWidget(label("Eigene Bilder hineinziehen, prüfen und auswählen – daraus lernt Krea 2 eine Figur. "
                          "Im Prompt holst du sie mit [name] dazu.", 9.5, "dim"))

        self.name = QLineEdit()
        self.name.setPlaceholderText("z. B. beispiel  →  im Prompt: [beispiel] sitzt im Café")
        self.name.textChanged.connect(self._name_zeigen)
        self.name_hinweis = label("", 9, "blass", umbruch=False)
        v.addLayout(_zeile("Name", self.name, self.name_hinweis, None, label("🎨 Bild (Krea 2)", 9.5, "cyan", umbruch=False)))

        self.liste = Bilderliste(self.dazu)
        self.liste.itemChanged.connect(lambda _it: self._zaehlen())
        self.liste.itemDoubleClicked.connect(lambda it: self._oeffnen(it.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(label("Bilder  ⬇ hier hineinziehen (Dateien oder Ordner)", 10, "dim"))
        v.addWidget(self.liste, 1)
        waehlen = Knopf("Bilder wählen …", "📁", klein=True)
        waehlen.clicked.connect(self._waehlen)
        self.pruefen_k = Knopf("Prüfen", "🔍", klein=True)
        self.pruefen_k.clicked.connect(self.pruefen)
        alle = Knopf("Alle an", "✔️", klein=True)
        alle.clicked.connect(lambda: self._alle(True))
        keine = Knopf("Alle aus", "🛑", klein=True)
        keine.clicked.connect(lambda: self._alle(False))
        leeren = Knopf("Leeren", "🧹", klein=True)
        leeren.clicked.connect(self._leeren)
        self.zaehler = label("0 Bilder", 9.5, "dim", umbruch=False)
        v.addLayout(_zeile(waehlen, self.pruefen_k, alle, keine, leeren, None, self.zaehler))
        self.pruef_text = label("", 9, "blass")
        v.addWidget(self.pruef_text)

        v.addSpacing(4)
        v.addWidget(_linie())
        self.schritte = QSpinBox()
        self.schritte.setRange(200, 4000)
        self.schritte.setSingleStep(100)
        self.schritte.setValue(1000)
        self.schritte.setToolTip("Mehr Schritte lernen genauer, dauern länger (Krea 2: etwa 5 s je Schritt)")
        self.schritte.valueChanged.connect(lambda _w: self._zaehlen())
        self.vorschau_an = QCheckBox("🖼 Testbild alle 200 Schritte")
        self.vorschau_an.setToolTip("Man sieht die LoRA lernen – kostet je Testbild etwas Zeit")
        self.proben_an = QCheckBox("🎨 Probebilder danach (Stärke 0,6 · 0,8 · 1,0)")
        self.proben_an.setChecked(True)
        v.addLayout(_zeile("Schritte", self.schritte, self.vorschau_an, self.proben_an, None))
        self.binden = QComboBox()
        self.binden.addItem(NICHT_BINDEN)
        self.binden.setMinimumWidth(220)
        v.addLayout(_zeile("Binden an 👤", self.binden, label("🔒 mit SHA-256", 9, "blass", umbruch=False), None))

        self.start_k = Knopf("Training starten", "🧬")
        self.start_k.akzent = True
        self.start_k.clicked.connect(self.starten)
        self.pause_k = Knopf("Pause", "⏸", klein=True)
        self.pause_k.setToolTip("Hält an und sichert den Stand – ▶ Weiter macht genau dort weiter")
        self.pause_k.clicked.connect(lambda: self.leitung.senden({"op": "pause"}))
        self.weiter_k = Knopf("Weiter", "▶", klein=True)
        self.weiter_k.clicked.connect(lambda: self.leitung.senden({"op": "weiter"}))
        v.addLayout(_zeile(self.start_k, self.pause_k, self.weiter_k, None))

        self.balken = QProgressBar()
        self.balken.setRange(0, 1000)
        self.balken.setFormat("0 %")
        v.addWidget(self.balken)
        self.schritt_text = label("", 9.5, "dim")
        v.addWidget(self.schritt_text)
        self.status = label("", 10, "dim")
        v.addWidget(self.status)

        bilder = QHBoxLayout()
        bilder.setSpacing(12)
        self.vorschau_bild = QLabel()
        self.vorschau_bild.setFixedSize(150, 210)
        self.vorschau_bild.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.vorschau_bild.setStyleSheet(f"background: {FARBE['flaeche']}; border-radius: 10px;")
        spalte = QVBoxLayout()
        spalte.addWidget(label("Testbild", 9, "blass", umbruch=False))
        spalte.addWidget(self.vorschau_bild)
        self.vorschau_text = label("", 8.5, "blass", umbruch=False)
        spalte.addWidget(self.vorschau_text)
        bilder.addLayout(spalte)
        self.proben: list[tuple[QLabel, QLabel]] = []
        for _ in range(3):
            s = QVBoxLayout()
            bild, unter = QLabel(), label("", 8.5, "blass", umbruch=False)
            bild.setFixedSize(150, 210)
            bild.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bild.setStyleSheet(f"background: {FARBE['flaeche']}; border-radius: 10px;")
            s.addWidget(label("Probe", 9, "blass", umbruch=False))
            s.addWidget(bild)
            s.addWidget(unter)
            bilder.addLayout(s)
            self.proben.append((bild, unter))
        bilder.addStretch(1)
        v.addLayout(bilder)

        v.addWidget(_linie())
        v.addWidget(label("Fertige LoRAs", 10, "dim"))
        self.bibliothek = QListWidget()
        self.bibliothek.setFixedHeight(110)
        v.addWidget(self.bibliothek)
        self.ziel = QComboBox()
        self.ziel.setMinimumWidth(200)
        bind_k = Knopf("Binden", "🔗", klein=True)
        bind_k.clicked.connect(self._binden)
        loes_k = Knopf("Bindung lösen", "✂", klein=True)
        loes_k.clicked.connect(self._loesen)
        ordner_k = Knopf("Ordner", "📂", klein=True)
        ordner_k.clicked.connect(self._ordner)
        v.addLayout(_zeile("an", self.ziel, bind_k, loes_k, None, ordner_k))

        leitung.ereignis.connect(self._ereignis)
        leitung.getrennt.connect(lambda: self._melden("Verbindung zur Sitzung getrennt – /lora neu öffnen.",
                                                      "fehler", False))
        self._knoepfe()
        leitung.senden({"op": "stand"})

    # -- Bilder --------------------------------------------------------------------------------------
    def dazu(self, pfade: list[str]) -> None:
        vorhanden = {self.liste.item(i).data(Qt.ItemDataRole.UserRole).lower() for i in range(self.liste.count())}
        neu = 0
        for p in self.lorabild.bilder_sammeln(pfade):
            s = str(p)
            if s.lower() in vorhanden:
                continue
            vorhanden.add(s.lower())
            it = QListWidgetItem(QIcon(vorschau(s, KACHEL)), p.name[:18])
            it.setData(Qt.ItemDataRole.UserRole, s)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            it.setToolTip(s)
            self.liste.addItem(it)
            neu += 1
        if neu:
            self.pruef_text.setText("Neue Bilder – 🔍 Prüfen zeigt, welche taugen.")
        self._zaehlen()

    def _waehlen(self):
        pfade, _ = QFileDialog.getOpenFileNames(self, "Bilder für die LoRA", "",
                                                "Bilder (*.png *.jpg *.jpeg *.webp *.bmp)")
        if pfade:
            self.dazu(pfade)

    def _alle(self, an: bool):
        for i in range(self.liste.count()):
            self.liste.item(i).setCheckState(Qt.CheckState.Checked if an else Qt.CheckState.Unchecked)

    def _leeren(self):
        if not self.laeuft:
            self.liste.clear()
            self.pruef_text.setText("")
            self._zaehlen()

    def gewaehlt(self) -> list[str]:
        return [self.liste.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.liste.count())
                if self.liste.item(i).checkState() == Qt.CheckState.Checked]

    def _zaehlen(self):
        n, alle = len(self.gewaehlt()), self.liste.count()
        minuten = round(self.schritte.value() * 5 / 60)
        self.zaehler.setText(f"{n} von {alle} gewählt  ·  etwa {_uhr(minuten * 60)} Training")
        pal = self.zaehler.palette()
        pal.setColor(self.zaehler.foregroundRole(), farbe("warn" if n < self.lorabild.MIN_BILDER else "dim"))
        self.zaehler.setPalette(pal)

    def _name_zeigen(self, text: str):
        n = self.lorabild.name_sauber(text)
        self.name_hinweis.setText(f"→ [{n}]" if n else "")

    def pruefen(self):
        pfade = [self.liste.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.liste.count())]
        if pfade and not self.laeuft:
            self.leitung.senden({"op": "pruefen", "pfade": pfade})

    def _pruefung(self, liste: list, gesichter: bool):
        nach_pfad = {str(e.get("pfad")).lower(): e for e in liste if isinstance(e, dict)}
        zaehl = {"✔️": 0, "⚠": 0, "🛑": 0}
        self.liste.blockSignals(True)
        for i in range(self.liste.count()):
            it = self.liste.item(i)
            e = nach_pfad.get(it.data(Qt.ItemDataRole.UserRole).lower())
            if not e:
                continue
            zeichen = str(e.get("zeichen") or "")
            zaehl[zeichen] = zaehl.get(zeichen, 0) + 1
            name = Path(it.data(Qt.ItemDataRole.UserRole)).name[:14]
            it.setText(f"{zeichen} {name}")
            it.setToolTip(f"{it.data(Qt.ItemDataRole.UserRole)}\n{zeichen} {e.get('hinweis') or 'passt'}")
            if not e.get("ok"):
                it.setCheckState(Qt.CheckState.Unchecked)
        self.liste.blockSignals(False)
        self._zaehlen()
        teile = [f"✔️ {zaehl['✔️']} passen", f"⚠ {zaehl['⚠']} prüfen", f"🛑 {zaehl['🛑']} abgewählt"]
        self.pruef_text.setText("  ·  ".join(teile) + ("" if gesichter else
                                "  ·  Gesichter nicht geprüft (kein Seh-Modell unter Vision/)")
                                + "  –  Maus über ein Bild zeigt den Grund.")

    # -- Training ------------------------------------------------------------------------------------
    def starten(self):
        if self.laeuft:
            return
        name = self.lorabild.name_sauber(self.name.text())
        bilder = self.gewaehlt()
        if not name:
            self._melden("Gib der LoRA zuerst einen Namen.", "warn", False)
            return
        if len(bilder) < self.lorabild.MIN_BILDER:
            self._melden(f"Mindestens {self.lorabild.MIN_BILDER} Bilder wählen (besser 15–40).", "warn", False)
            return
        ziel = self.binden.currentText()
        self.balken.setValue(0)
        self.balken.setFormat("0 %")
        self._melden("🧬 sende den Auftrag …", "dim", True)
        self.leitung.senden({"op": "starten", "name": name, "pfade": bilder, "schritte": self.schritte.value(),
                             "vorschau": self.vorschau_an.isChecked(), "proben": self.proben_an.isChecked(),
                             "binden": "" if ziel == NICHT_BINDEN else ziel})

    def _fortschritt(self, ev: dict):
        if ev.get("vorschau"):
            self._bild(self.vorschau_bild, str(ev["vorschau"]))
            self.vorschau_text.setText(f"nach Schritt {ev.get('schritt')}")
            return
        try:
            prozent = float(ev.get("prozent") or 0.0)
        except (TypeError, ValueError):
            prozent = 0.0
        self.balken.setValue(int(prozent * 10))
        self.balken.setFormat(f"{prozent:.0f} %")
        self.schritt_text.setText(f"Schritt {ev.get('schritt')}/{ev.get('schritte')}  ·  noch etwa "
                                  f"{_uhr(ev.get('rest_s'))}  ·  Verlust {ev.get('verlust')}")
        self.laeuft = True
        self._knoepfe()

    def _fertig(self, ev: dict):
        self.balken.setValue(1000)
        self.balken.setFormat("100 %")
        for (bild, unter), p in zip(self.proben, ev.get("proben") or []):
            self._bild(bild, str(p.get("pfad") or ""))
            unter.setText(f"Stärke {p.get('staerke')}")
        sha = str(ev.get("sha256") or "")
        gebunden = f" · gebunden an {ev['gebunden']}" if ev.get("gebunden") else ""
        self._melden(f"✅ „{ev.get('name')}“ fertig{gebunden} · SHA-256 {sha[:16]}… · im Prompt: [{ev.get('name')}] …",
                     "ok", False)
        self.leitung.senden({"op": "stand"})

    # -- Bibliothek ----------------------------------------------------------------------------------
    def _stand(self, ev: dict):
        personas = [str(n) for n in ev.get("personas") or []]
        for box, erste in ((self.binden, NICHT_BINDEN), (self.ziel, None)):
            alt = box.currentText()
            box.clear()
            if erste:
                box.addItem(erste)
            box.addItems(personas)
            if alt in personas or alt == erste:
                box.setCurrentText(alt)
        self.bibliothek.clear()
        for e in ev.get("loras") or []:
            gebunden = ", ".join(e.get("gebunden") or []) or "nicht gebunden"
            it = QListWidgetItem(f"🧬 [{e.get('name')}]  ·  {gebunden}  ·  SHA-256 {str(e.get('sha256') or '?')[:12]}…")
            it.setData(Qt.ItemDataRole.UserRole, str(e.get("name") or ""))
            it.setData(Qt.ItemDataRole.UserRole + 1, str(e.get("datei") or ""))
            self.bibliothek.addItem(it)
        self.krea_grund = str(ev.get("krea") or "")
        if self.krea_grund:
            self._melden("⚠ Krea 2 fehlt: " + self.krea_grund, "warn", False)
        self.laeuft = bool(ev.get("laeuft")) or self.laeuft
        self._knoepfe()

    def _binden(self):
        it = self.bibliothek.currentItem()
        if it and self.ziel.currentText():
            self.leitung.senden({"op": "binden", "lora": it.data(Qt.ItemDataRole.UserRole),
                                 "charakter": self.ziel.currentText()})

    def _loesen(self):
        if self.ziel.currentText():
            self.leitung.senden({"op": "loesen", "charakter": self.ziel.currentText()})

    def _ordner(self):
        it = self.bibliothek.currentItem()
        datei = it.data(Qt.ItemDataRole.UserRole + 1) if it else ""
        if datei and Path(datei).parent.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(datei).parent)))

    # -- Ereignisse ----------------------------------------------------------------------------------
    def _ereignis(self, ev: dict):
        t = ev.get("t")
        if t == "status":
            self._melden(str(ev.get("text") or ""), "dim", bool(ev.get("laeuft")))
        elif t == "fehler":
            self._melden("⚠ " + str(ev.get("text") or ""), "fehler", False)
        elif t == "pausiert":
            self._melden("⏸ angehalten – der Stand ist gesichert. ▶ Weiter macht dort weiter.", "warn", False)
        elif t == "pruefung":
            self._melden("🔍 geprüft", "ok", False)
            self._pruefung(ev.get("bilder") or [], bool(ev.get("gesichter")))
        elif t == "fortschritt":
            self._fortschritt(ev)
        elif t == "fertig":
            self._fertig(ev)
        elif t == "gebunden":
            self._melden(f"🔗 [{ev.get('lora')}] an {ev.get('charakter')} gebunden · SHA-256 "
                         f"{str(ev.get('sha256') or '')[:16]}…", "ok", False)
        elif t == "stand":
            self._stand(ev)
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()

    # -- Anzeige -------------------------------------------------------------------------------------
    def _melden(self, text: str, farbname: str, laeuft: bool):
        self.laeuft = laeuft
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(self.status.foregroundRole(), farbe(farbname))
        self.status.setPalette(pal)
        self._knoepfe()

    def _knoepfe(self):
        self.start_k.setEnabled(not self.laeuft and not self.krea_grund)
        self.pruefen_k.setEnabled(not self.laeuft)
        self.pause_k.setEnabled(self.laeuft)
        self.weiter_k.setEnabled(not self.laeuft)

    @staticmethod
    def _bild(ziel: QLabel, pfad: str):
        if pfad and Path(pfad).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and Path(pfad).is_file():
            ziel.setPixmap(vorschau(pfad, max(ziel.width(), ziel.height())).scaled(
                ziel.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            ziel.setToolTip(pfad)

    @staticmethod
    def _oeffnen(pfad: str):
        # Nur Bilder öffnen: der Pfad kommt aus der Liste – eine .bat oder .lnk würde sonst gestartet.
        if pfad and Path(pfad).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp") and Path(pfad).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(pfad))


def starten() -> int:
    app = app_vorbereiten("NemiCLI.LoRA")
    leitung = leitung_oeffnen("/lora")
    if leitung is None:
        return 1
    fenster = LoraFenster(leitung)
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
