"""atelier_fenster.py – 🎨 Krea-Atelier (/atelier, /pic): eigenes Fenster mit allen Einstellungen von Krea 2.

Links Prompt und Regler, in der Mitte das große Bild (beim A/B-Vergleich zwei nebeneinander), rechts die Galerie.
Unten rechts die Kugel: die KI bedient das Atelier sichtbar – der Geist-Zeiger fliegt zum Feld, dann ändert sie es.
Gemalt wird in der Sitzung (atelier.py, imagegen.atelier); ESC hält das Bild nach dem laufenden Schritt an.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import atelier as AT
from design import FARBE, schrift
from gui_fenster import Knopf, _dunkler_rahmen, app_vorbereiten, label, rollbereich, vorschau
from office_agent import FLUG_MS, PAUSE_MS, Geistzeiger
from office_kugel import OfficeAI

GALERIE_SEITE = 150
LADEN_JE_TAKT = 4                      # Vorschaubilder je Takt (die Galerie füllt sich, ohne das Fenster aufzuhalten)
BILD_ANGABEN = ("prompt", "model", "params", "negative_prompt")
NAMEN = {"prompt": "Prompt", "negativ": "Gegen-Prompt", "modell": "Bildmodell", "text": "Text-Encoder", "vae": "VAE",
         "sampler": "Sampler", "zeitplan": "Zeitplan", "schritte": "Schritte", "cfg": "CFG", "shift": "Verschiebung",
         "breite": "Breite", "hoehe": "Höhe", "seed": "Seed", "detail": "Detail-Satz", "detail_satz": "Detail-Satz",
         "gesicht": "Gesicht nachmalen", "figur": "Figur"}


def _rahmen(name: str) -> str:
    return (f"QFrame#{name} {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; "
            f"border-radius: 14px; }}")


def _feld_stil() -> str:
    return (f"QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{ background: {FARBE['flaeche']}; "
            f"color: {FARBE['text']}; border: 1px solid {FARBE['rand']}; border-radius: 8px; padding: 4px 8px; }}")


class AtelierAI(OfficeAI):
    """Die Kugel des Ateliers: eigene schnelle Aufträge, eigener Gruß."""

    def stand(self, name: str, modell: bool, gesichter: dict | None = None) -> None:
        super().stand(name, modell, gesichter)
        self.blase.titel.setText(f"{self.name} · Atelier")
        self.kugel.setToolTip(f"{self.name} – hilft beim Prompt, stellt ein, malt und vergleicht")

    def app_setzen(self, app: str) -> None:
        self.app = app
        lay = self.blase.chips_lay
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for zeichen, text, auftrag in AT.AUFTRAEGE:
            k = Knopf(text, zeichen, klein=True)
            k.setToolTip(auftrag)
            k.clicked.connect(lambda _=False, a=auftrag, t=text: self.fragen(a, t))
            lay.addWidget(k)
        lay.addStretch(1)
        self.blase.chips.setVisible(True)
        self.feld.eingabe.setPlaceholderText("z. B. „Mach mir ein Porträt im Abendlicht“ oder „Vergleich euler mit "
                                             "euler_ancestral“")

    def _begruessung(self) -> str:
        if not self.modell:
            return "Kein Modell verbunden – im Terminal mit /model eins wählen."
        return ("Ich helfe dir im Atelier: Prompt verbessern, passend einstellen, malen und vergleichen. Du siehst "
                "jeden Handgriff – mein Zeiger fliegt zum Feld. ESC hält mich an.")

    def ausklappen(self, fokus: bool = False) -> None:
        erster = not self.offen
        super().ausklappen(fokus)
        if erster and not self.blase.isVisible() and not self.laufend:
            self.blase.zeigen(status="", text=self._begruessung())
            self.blase.show()
            self.anordnen()


class _Bild(QLabel):
    """Das große Bild: passt sich der Fläche an und behält das Seitenverhältnis."""

    def __init__(self, leer: str):
        super().__init__(leer)
        self.pfad = ""
        self._pix: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(240, 240)
        self.setFont(schrift(11))
        self.setStyleSheet(f"color: {FARBE['blass']}; background: {FARBE['flaeche']}; border-radius: 14px;")

    def zeigen(self, pfad: str) -> None:
        pix = QPixmap(pfad)
        if pix.isNull():
            return
        self.pfad, self._pix = pfad, pix
        self._skalieren()

    def leeren(self, text: str) -> None:
        self.pfad, self._pix = "", None
        self.clear()
        self.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._skalieren()

    def _skalieren(self) -> None:
        if self._pix is not None:
            self.setPixmap(self._pix.scaled(self.size() - QSize(8, 8), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))


class AtelierFenster(QWidget):
    def __init__(self, leitung=None):
        super().__init__()
        self.leitung = leitung
        self.wahl: dict = {}
        self.letztes = ""
        self.vergleich: dict = {}
        self.malt = False
        self.seed_zuletzt = -1
        self.warten: tuple | None = None                   # (Auftrag, Schritt) der KI, die auf ein Bild wartet
        self.w: dict[str, QWidget] = {}
        self.vorlagen: dict = {}
        self._laden: list[QListWidgetItem] = []
        self.setObjectName("atelier")
        self.setWindowTitle("Nemi Atelier")
        self.resize(1560, 960)
        self.setStyleSheet(f"QWidget#atelier {{ background: {FARBE['grund']}; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._kopf())
        koerper = QHBoxLayout()
        koerper.setContentsMargins(16, 14, 16, 16)
        koerper.setSpacing(14)
        links = rollbereich(self._regler())
        links.setFixedWidth(450)
        koerper.addWidget(links)
        koerper.addLayout(self._mitte(), 1)
        koerper.addWidget(self._galerie())
        v.addLayout(koerper, 1)

        self.zeiger = Geistzeiger(self)
        self.nemi = AtelierAI(self, self._kontext)
        self.nemi.frage_senden.connect(self.senden)
        self.nemi.stopp.connect(lambda nr: self.senden({"op": "stopp", "id": nr}))
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._anhalten)
        self._pruef_uhr = QTimer(self)
        self._pruef_uhr.setSingleShot(True)
        self._pruef_uhr.timeout.connect(lambda: self.senden({"op": "pruefen", "prompt": self._prompt()}))
        self._lade_uhr = QTimer(self)
        self._lade_uhr.timeout.connect(self._vorschau_laden)

        if leitung is not None:
            leitung.ereignis.connect(self._ereignis)
            leitung.getrennt.connect(self._getrennt)
            leitung.senden({"op": "start"})

    # -- Aufbau ----------------------------------------------------------------------------
    def _kopf(self) -> QFrame:
        kopf = QFrame()
        kopf.setObjectName("kopf")
        kopf.setStyleSheet(f"QFrame#kopf {{ background: {FARBE['leiste']}; border-bottom: 1px solid {FARBE['rand']}; }}")
        k = QHBoxLayout(kopf)
        k.setContentsMargins(18, 10, 18, 10)
        k.addWidget(label("🎨 Atelier", 16, "text", fett=True, umbruch=False))
        k.addWidget(label("· Krea 2", 11, "dim", umbruch=False))
        self.zustand = label("", 10.5, "dim", umbruch=False)
        k.addWidget(self.zustand, 1)
        self.vorlagen_box = QComboBox()
        self.vorlagen_box.setMinimumWidth(200)
        self.vorlagen_box.setStyleSheet(_feld_stil())
        self.vorlagen_box.setToolTip("Gemerkte Einstellungen übernehmen (Prompt und Seed bleiben)")
        self.vorlagen_box.activated.connect(self._vorlage_gewaehlt)
        k.addWidget(self.vorlagen_box)
        for text, zeichen, tipp, fn in (("Vorlage merken", "💾", "Die Einstellungen unter einem Namen merken",
                                         self._vorlage_merken),
                                        ("", "🗑", "Die gewählte Vorlage löschen", self._vorlage_loeschen),
                                        ("Als Standard", "⭐", "Für alle Krea-Bilder übernehmen (auch 🎛 Krea 2 und "
                                                               "die KI im Chat)", self._als_standard)):
            b = Knopf(text, zeichen, klein=True)
            b.setToolTip(tipp)
            b.clicked.connect(fn)
            k.addWidget(b)
        return kopf

    def _gruppe(self, lay: QVBoxLayout, titel: str) -> QVBoxLayout:
        rahmen = QFrame()
        rahmen.setObjectName("gruppe")
        rahmen.setStyleSheet(_rahmen("gruppe"))
        g = QVBoxLayout(rahmen)
        g.setContentsMargins(14, 10, 14, 12)
        g.setSpacing(6)
        g.addWidget(label(titel, 11.5, "text", fett=True))
        lay.addWidget(rahmen)
        return g

    def _zeile(self, g: QVBoxLayout, *felder: tuple[str, QWidget]) -> None:
        h = QHBoxLayout()
        for titel, widget in felder:
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(label(titel, 9.5, "blass"))
            widget.setStyleSheet(_feld_stil())
            widget.setFont(schrift(10.5))
            box.addWidget(widget)
            h.addLayout(box, 1)
        g.addLayout(h)

    def _regler(self) -> QWidget:
        inhalt = QWidget()
        lay = QVBoxLayout(inhalt)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)

        g = self._gruppe(lay, "✍️ Prompt")
        self.w["prompt"] = QPlainTextEdit()
        self.w["prompt"].setPlaceholderText("Beschreib dein Bild in ganzen Sätzen (englisch) – Motiv, Details, Licht, "
                                            "Kamera …")
        self.w["prompt"].setFixedHeight(170)
        self.w["prompt"].textChanged.connect(lambda: self._pruef_uhr.start(600))
        self._zeile(g, ("", self.w["prompt"]))
        self.token = label("", 9.5, "dim")
        g.addWidget(self.token)
        self.funde = label("", 9.5, "warn")
        self.funde.hide()
        g.addWidget(self.funde)
        self.w["negativ"] = QPlainTextEdit()
        self.w["negativ"].setPlaceholderText("Gegen-Prompt – wirkt erst ab CFG über 1")
        self.w["negativ"].setFixedHeight(60)
        self._zeile(g, ("🚫 Gegen-Prompt", self.w["negativ"]))
        haken = QHBoxLayout()
        for feld, text, tipp in (("detail", "✨ Detail-Satz", "Schärfe und Haut bei Bildern mit Person"),
                                 ("gesicht", "😊 Gesicht", "Gesichter danach vergrößert nachmalen"),
                                 ("figur", "🧍 Figur", "Der Kern der Persönlichkeit kommt vor den Prompt")):
            box = QCheckBox(text)
            box.setToolTip(tipp)
            box.setFont(schrift(10))
            box.setStyleSheet(f"QCheckBox {{ color: {FARBE['text']}; }}")
            self.w[feld] = box
            haken.addWidget(box)
        haken.addStretch(1)
        g.addLayout(haken)
        self.w["detail_satz"] = QPlainTextEdit()
        self.w["detail_satz"].setFixedHeight(52)
        self.w["detail"].toggled.connect(self.w["detail_satz"].setEnabled)
        self._zeile(g, ("Detail-Satz", self.w["detail_satz"]))

        g = self._gruppe(lay, "🧠 Modelle")
        for feld in ("modell", "text", "vae"):
            self.w[feld] = QComboBox()
            self._zeile(g, (NAMEN[feld], self.w[feld]))

        g = self._gruppe(lay, "⚙️ Rechnen")
        self.w["sampler"], self.w["zeitplan"] = QComboBox(), QComboBox()
        self._zeile(g, ("Sampler", self.w["sampler"]), ("Zeitplan", self.w["zeitplan"]))
        self.w["schritte"] = QSpinBox()
        self.w["cfg"], self.w["shift"] = QDoubleSpinBox(), QDoubleSpinBox()
        for feld, schritt in (("cfg", 0.1), ("shift", 0.05)):
            self.w[feld].setSingleStep(schritt)
        self._zeile(g, ("Schritte", self.w["schritte"]), ("CFG (1 = aus)", self.w["cfg"]),
                    ("Verschiebung", self.w["shift"]))

        g = self._gruppe(lay, "📐 Größe & Seed")
        formate = QHBoxLayout()
        formate.setSpacing(4)
        for name, b, h in AT.FORMATE:
            k = Knopf(name, "", klein=True)
            k.setToolTip(f"{b} × {h}")
            k.clicked.connect(lambda _=False, b=b, h=h: (self.w["breite"].setValue(b), self.w["hoehe"].setValue(h)))
            formate.addWidget(k)
        formate.addStretch(1)
        g.addLayout(formate)
        self.w["breite"], self.w["hoehe"], self.w["seed"] = QSpinBox(), QSpinBox(), QSpinBox()
        for feld in ("breite", "hoehe"):
            self.w[feld].setRange(256, 2048)
            self.w[feld].setSingleStep(16)
        self.w["seed"].setRange(-1, AT.SEED_MAX)
        self.w["seed"].setSpecialValueText("🎲 zufällig")
        self._zeile(g, ("Breite", self.w["breite"]), ("Höhe", self.w["hoehe"]), ("Seed", self.w["seed"]))
        seed_knoepfe = QHBoxLayout()
        zufall = Knopf("Zufällig", "🎲", klein=True)
        zufall.clicked.connect(lambda: self.w["seed"].setValue(-1))
        halten = Knopf("Letzten Seed festhalten", "📌", klein=True)
        halten.clicked.connect(lambda: self.seed_zuletzt >= 0 and self.w["seed"].setValue(self.seed_zuletzt))
        seed_knoepfe.addWidget(zufall)
        seed_knoepfe.addWidget(halten)
        seed_knoepfe.addStretch(1)
        g.addLayout(seed_knoepfe)

        g = self._gruppe(lay, "⚖️ A/B-Vergleich")
        g.addWidget(label("Gleicher Seed, eine Einstellung anders – beide Bilder nebeneinander.", 9.5, "dim"))
        self.ab_feld = QComboBox()
        for feld in AT.VERGLEICHBAR:
            self.ab_feld.addItem(NAMEN[feld], feld)
        self.ab_feld.currentIndexChanged.connect(self._ab_feld_gewechselt)
        self.ab_wert = QComboBox()
        self.ab_wert.setEditable(True)
        self._zeile(g, ("Was anders?", self.ab_feld), ("B-Wert", self.ab_wert))
        self.ab_knopf = Knopf("A/B malen", "⚖️", klein=True)
        self.ab_knopf.clicked.connect(lambda: self._ab_malen())
        g.addWidget(self.ab_knopf)
        lay.addStretch(1)
        return inhalt

    def _mitte(self) -> QVBoxLayout:
        m = QVBoxLayout()
        m.setSpacing(8)
        knoepfe = QHBoxLayout()
        self.malen_knopf = Knopf("Malen", "🎨")
        self.malen_knopf.akzent = True
        self.malen_knopf.setToolTip("Mit allen Einstellungen links malen (ESC hält an)")
        self.malen_knopf.clicked.connect(lambda: self._malen())
        self.stopp_knopf = Knopf("Anhalten", "⏹")
        self.stopp_knopf.setToolTip("Nach dem laufenden Schritt anhalten (ESC)")
        self.stopp_knopf.clicked.connect(lambda: self.senden({"op": "stopp_malen"}))
        self.stopp_knopf.setEnabled(False)
        knoepfe.addWidget(self.malen_knopf)
        knoepfe.addWidget(self.stopp_knopf)
        knoepfe.addStretch(1)
        m.addLayout(knoepfe)
        self.balken = QProgressBar()
        self.balken.setTextVisible(False)
        self.balken.setFixedHeight(8)
        self.balken.setRange(0, 1)
        self.balken.setValue(0)
        m.addWidget(self.balken)
        self.status = label("", 10, "dim")
        m.addWidget(self.status)
        bilder = QHBoxLayout()
        self.bild_a = _Bild("Hier erscheint dein Bild 🎨")
        self.bild_b = _Bild("")
        self.bild_b.hide()
        spalte_a, spalte_b = QVBoxLayout(), QVBoxLayout()
        self.titel_a, self.titel_b = label("", 10, "dim"), label("", 10, "dim")
        spalte_a.addWidget(self.titel_a)
        spalte_a.addWidget(self.bild_a, 1)
        spalte_b.addWidget(self.titel_b)
        spalte_b.addWidget(self.bild_b, 1)
        bilder.addLayout(spalte_a, 1)
        bilder.addLayout(spalte_b, 1)
        m.addLayout(bilder, 1)
        unten = QHBoxLayout()
        for text, zeichen, tipp, fn in (("", "👍", "Gefällt mir – der Prompt-Schmied lernt daraus",
                                         lambda: self._urteil("gut")),
                                        ("", "👎", "Gefällt mir nicht", lambda: self._urteil("schlecht")),
                                        ("Einstellungen übernehmen", "↩", "Prompt und Einstellungen dieses Bildes "
                                                                         "links eintragen", self._uebernehmen),
                                        ("Öffnen", "🔍", "Im Bildbetrachter öffnen", self._oeffnen)):
            b = Knopf(text, zeichen, klein=True)
            b.setToolTip(tipp)
            b.clicked.connect(fn)
            unten.addWidget(b)
        unten.addStretch(1)
        m.addLayout(unten)
        return m

    def _galerie(self) -> QFrame:
        rahmen = QFrame()
        rahmen.setObjectName("galerie")
        rahmen.setStyleSheet(_rahmen("galerie"))
        rahmen.setFixedWidth(GALERIE_SEITE + 60)
        g = QVBoxLayout(rahmen)
        g.setContentsMargins(10, 10, 10, 10)
        g.addWidget(label("🖼 Galerie", 11.5, "text", fett=True))
        self.galerie = QListWidget()
        self.galerie.setViewMode(QListView.ViewMode.IconMode)
        self.galerie.setIconSize(QSize(GALERIE_SEITE, GALERIE_SEITE))
        self.galerie.setResizeMode(QListView.ResizeMode.Adjust)
        self.galerie.setMovement(QListView.Movement.Static)
        self.galerie.setSpacing(4)
        self.galerie.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.galerie.currentItemChanged.connect(self._galerie_gewaehlt)
        g.addWidget(self.galerie, 1)
        return rahmen

    # -- Felder ----------------------------------------------------------------------------
    def _prompt(self) -> str:
        return self.w["prompt"].toPlainText().strip()

    def einst_lesen(self) -> dict:
        d = {}
        for feld, w in self.w.items():
            if isinstance(w, QPlainTextEdit):
                d[feld] = " ".join(w.toPlainText().split())
            elif isinstance(w, QComboBox):
                d[feld] = w.currentText()
            elif isinstance(w, QCheckBox):
                d[feld] = w.isChecked()
            else:
                d[feld] = w.value()
        return d

    def wert_setzen(self, feld: str, wert) -> bool:
        w = self.w.get(feld)
        if w is None or wert is None:
            return False
        if isinstance(w, QPlainTextEdit):
            w.setPlainText(str(wert))
        elif isinstance(w, QComboBox):
            if w.findText(str(wert)) < 0:
                return False
            w.setCurrentText(str(wert))
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(wert))
        elif isinstance(w, QSpinBox):
            w.setValue(int(wert))
        else:
            w.setValue(float(wert))
        return True

    def einst_setzen(self, einst: dict) -> None:
        for feld, wert in (einst or {}).items():
            self.wert_setzen(feld, wert)

    def _auswahl_setzen(self, wahl: dict) -> None:
        self.wahl = wahl or {}
        for feld, schluessel in (("modell", "modelle"), ("text", "text"), ("vae", "vae"), ("sampler", "sampler"),
                                 ("zeitplan", "zeitplan")):
            box = self.w[feld]
            alt = box.currentText()
            box.blockSignals(True)
            box.clear()
            box.addItems([str(x) for x in self.wahl.get(schluessel) or []])
            if alt:
                box.setCurrentText(alt)
            box.blockSignals(False)
        bereich = self.wahl.get("bereich") or {}
        for feld in ("schritte", "cfg", "shift"):
            lo, hi = (bereich.get(feld) or [0, 100])[:2]
            self.w[feld].setRange(lo, hi)
        self._ab_feld_gewechselt()

    def _ab_feld_gewechselt(self, _i: int = 0) -> None:
        feld = self.ab_feld.currentData()
        art = AT.FELDER.get(feld, ("text",))
        self.ab_wert.clear()
        if art[0] == "wahl":
            self.ab_wert.addItems([str(x) for x in self.wahl.get(art[1]) or []])
        elif art[0] == "bool":
            self.ab_wert.addItems(["true", "false"])
        self.ab_wert.setEditText("" if art[0] not in ("wahl", "bool") else self.ab_wert.currentText())

    # -- Malen -----------------------------------------------------------------------------
    def _malen(self, ab: dict | None = None) -> bool:
        if self.malt:
            self.status.setText("Es malt gerade schon – ESC hält an.")
            return False
        if not self._prompt():
            self.status.setText("✍️ Erst einen Prompt schreiben.")
            return False
        auftrag = {"op": "malen", "einst": self.einst_lesen()}
        if ab:
            auftrag["ab"] = ab
        self.senden(auftrag)
        return True

    def _ab_malen(self, feld: str | None = None, wert=None) -> bool:
        feld = feld or self.ab_feld.currentData()
        wert = self.ab_wert.currentText() if wert is None else wert
        if str(wert).strip() == "":
            self.status.setText("⚖️ Für B fehlt noch der Wert.")
            return False
        return self._malen({"feld": feld, "wert": wert})

    def _anhalten(self) -> None:
        if self.malt:
            self.senden({"op": "stopp_malen"})
        self.nemi.anhalten()

    def _urteil(self, wertung: str) -> None:
        pfad = self._gezeigt()
        if pfad:
            self.senden({"op": "urteil", "pfad": pfad, "wertung": wertung})

    def _gezeigt(self) -> str:
        return self.bild_a.pfad if not self.bild_b.isVisible() else (self.letztes or self.bild_a.pfad)

    def _oeffnen(self) -> None:
        if (pfad := self._gezeigt()):
            QDesktopServices.openUrl(QUrl.fromLocalFile(pfad))

    def _uebernehmen(self) -> None:
        """Prompt und Einstellungen aus den Angaben des gezeigten Bildes links eintragen."""
        pfad = self._gezeigt()
        if not pfad:
            return
        leser = QImageReader(pfad)
        meta = {k: leser.text(k) for k in BILD_ANGABEN if leser.text(k)}
        felder = AT.angaben_lesen(meta)
        if not felder:
            self.status.setText("Dieses Bild trägt keine Angaben.")
            return
        gesetzt = [NAMEN.get(f, f) for f, wert in felder.items() if self.wert_setzen(f, wert)]
        if meta.get("negative_prompt"):
            self.wert_setzen("negativ", meta["negative_prompt"])
        self.status.setText("↩ Übernommen: " + ", ".join(gesetzt))

    # -- Vorlagen --------------------------------------------------------------------------
    def _vorlagen_setzen(self, vorlagen: dict) -> None:
        self.vorlagen = vorlagen if isinstance(vorlagen, dict) else {}
        self.vorlagen_box.blockSignals(True)
        self.vorlagen_box.clear()
        self.vorlagen_box.addItem("📋 Vorlage wählen …", "")
        for name in sorted(self.vorlagen):
            self.vorlagen_box.addItem(name, name)
        self.vorlagen_box.blockSignals(False)

    def _vorlage_gewaehlt(self, _i: int) -> None:
        name = self.vorlagen_box.currentData()
        if name and name in self.vorlagen:
            self.einst_setzen(self.vorlagen[name])
            self.status.setText(f"📋 Vorlage „{name}“ übernommen.")

    def _vorlage_merken(self) -> None:
        name, ok = QInputDialog.getText(self, "💾 Vorlage merken", "Name der Vorlage (z. B. „Euler fein“):")
        if ok and name.strip():
            self.senden({"op": "vorlage", "name": name.strip(), "einst": self.einst_lesen()})

    def _vorlage_loeschen(self) -> None:
        name = self.vorlagen_box.currentData()
        if name and QMessageBox.question(self, "🗑 Vorlage löschen", f"Die Vorlage „{name}“ löschen?") == \
                QMessageBox.StandardButton.Yes:
            self.senden({"op": "vorlage", "name": name, "loeschen": True})

    def _als_standard(self) -> None:
        if QMessageBox.question(self, "⭐ Als Standard", "Diese Einstellungen für alle Krea-Bilder übernehmen – auch "
                                                         "für 🎛 Krea 2 und die KI im Chat?") == \
                QMessageBox.StandardButton.Yes:
            self.senden({"op": "als_standard", "einst": self.einst_lesen()})

    # -- Galerie ---------------------------------------------------------------------------
    def _galerie_setzen(self, bilder: list) -> None:
        aktuell = self.galerie.currentItem().data(Qt.ItemDataRole.UserRole) if self.galerie.currentItem() else ""
        self.galerie.blockSignals(True)
        self.galerie.clear()
        self._laden = []
        for b in bilder or []:
            it = QListWidgetItem(str(b.get("zeit") or ""))
            it.setData(Qt.ItemDataRole.UserRole, str(b.get("pfad") or ""))
            it.setToolTip(str(b.get("pfad") or ""))
            it.setSizeHint(QSize(GALERIE_SEITE + 8, GALERIE_SEITE + 26))
            self.galerie.addItem(it)
            self._laden.append(it)
            if it.data(Qt.ItemDataRole.UserRole) == aktuell:
                self.galerie.setCurrentItem(it)
        self.galerie.blockSignals(False)
        self._lade_uhr.start(30)

    def _vorschau_laden(self) -> None:
        for _ in range(LADEN_JE_TAKT):
            if not self._laden:
                self._lade_uhr.stop()
                return
            it = self._laden.pop(0)
            try:
                it.setIcon(QIcon(vorschau(it.data(Qt.ItemDataRole.UserRole), GALERIE_SEITE)))
            except RuntimeError:                           # Eintrag inzwischen weg
                pass

    def _galerie_gewaehlt(self, it, _alt=None) -> None:
        if it is None:
            return
        pfad = str(it.data(Qt.ItemDataRole.UserRole) or "")
        self.bild_b.hide()
        self.titel_a.setText("")
        self.titel_b.setText("")
        self.bild_a.zeigen(pfad)
        self.letztes = pfad

    # -- KI: bedient das Atelier sichtbar ----------------------------------------------------
    def _kontext(self) -> dict:
        return {"app": "atelier", "einst": self.einst_lesen(), "letztes": self.letztes, "vergleich": self.vergleich}

    def _aktion(self, ev: dict) -> None:
        """Werkzeug der KI: erst fliegt der Geist-Zeiger zum Feld, dann wird es gesetzt (oder gemalt)."""
        nr, schritt = ev.get("id"), ev.get("schritt")
        aktion = ev.get("aktion") if isinstance(ev.get("aktion"), dict) else {}
        tool = aktion.get("tool")

        def melden(ok: bool, text: str) -> None:
            self.senden({"op": "ergebnis", "id": nr, "schritt": schritt, "ok": ok, "text": text,
                         "kontext": self._kontext()})

        if self.nemi.laufend != nr:
            melden(False, "Der Auftrag läuft nicht mehr.")
            return
        feld = str(aktion.get("feld") or "")
        if tool == "einstellen":
            ziel = self.w.get(feld)
            if ziel is None:
                melden(False, f"Das Feld '{feld}' gibt es nicht.")
                return
            self._hin(ziel, f"{NAMEN.get(feld, feld)} …")
            QTimer.singleShot(FLUG_MS + PAUSE_MS, self, lambda: melden(*(
                (True, f"{NAMEN.get(feld, feld)} = {str(aktion.get('wert'))[:200]}")
                if self.wert_setzen(feld, aktion.get("wert")) else (False, f"{feld}: Wert passt nicht."))))
            return
        if tool in ("malen", "vergleichen"):
            if self.malt:
                melden(False, "Es malt gerade schon.")
                return
            self._hin(self.malen_knopf if tool == "malen" else self.ab_knopf,
                      "malt …" if tool == "malen" else "vergleicht …")

            def los():
                if tool == "vergleichen":
                    i = self.ab_feld.findData(feld)
                    if i >= 0:
                        self.ab_feld.setCurrentIndex(i)
                    self.ab_wert.setEditText(str(aktion.get("wert")))
                    gestartet = self._ab_malen(feld, aktion.get("wert"))
                else:
                    gestartet = self._malen()
                if gestartet:
                    self.warten = (nr, schritt)
                else:
                    melden(False, self.status.text() or "Malen ging nicht.")
            QTimer.singleShot(FLUG_MS + PAUSE_MS, self, los)
            return
        melden(False, f"'{tool}' gibt es im Fenster nicht.")

    def _hin(self, ziel: QWidget, text: str) -> None:
        punkt = ziel.mapTo(self, QPoint(min(40, ziel.width() // 2), ziel.height() // 2))
        self.zeiger.fliegen(punkt, text)
        QTimer.singleShot(FLUG_MS + 2500, self.zeiger, self.zeiger.ausblenden)

    # -- Ereignisse der Sitzung ------------------------------------------------------------
    def senden(self, auftrag: dict) -> None:
        if self.leitung is None:
            self.status.setText("Nicht mit NemiCLI verbunden – im Terminal /atelier eingeben.")
            return
        self.leitung.senden(auftrag)

    def _ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "stand":
            self._auswahl_setzen(ev.get("auswahl") or {})
            self.einst_setzen(ev.get("einst") or {})
            self._vorlagen_setzen(ev.get("vorlagen") or {})
            self.nemi.stand(str(ev.get("name") or ""), bool(ev.get("modell")), ev.get("gesichter"))
            fehlt = str(ev.get("fehlt") or "")
            self.zustand.setText(f"⚠ {fehlt}" if fehlt else "")
            self.malen_knopf.setEnabled(not fehlt)
        elif t == "galerie":
            self._galerie_setzen(ev.get("bilder") or [])
        elif t == "vorlagen":
            self._vorlagen_setzen(ev.get("vorlagen") or {})
        elif t == "geprueft":
            zuviel = int(ev.get("zuviel") or 0)
            self.token.setText(f"{ev.get('token', 0)} / {ev.get('max', 512)} Token"
                               + (f" – {zuviel} fallen weg ✂" if zuviel else ""))
            funde = [str(f) for f in ev.get("funde") or []]
            self.funde.setText("🔨 " + "\n🔨 ".join(funde) if funde else "")
            self.funde.setVisible(bool(funde))
        elif t == "malt":
            self._malt(bool(ev.get("an")), ev)
        elif t == "fortschritt":
            von, schritt = int(ev.get("von") or 0), int(ev.get("schritt") or 0)
            if von:
                self.balken.setRange(0, von)
                self.balken.setValue(schritt)
            self.status.setText(str(ev.get("text") or ""))
        elif t == "bild":
            self._bild(str(ev.get("pfad") or ""), ev.get("seite"), ev.get("einst") or {})
        elif t == "fertig":
            self.status.setText(str(ev.get("text") or ""))
            if self.warten is not None:
                nr, schritt = self.warten
                self.warten = None
                self.senden({"op": "ergebnis", "id": nr, "schritt": schritt, "ok": bool(ev.get("ok")),
                             "text": str(ev.get("text") or ""), "kontext": self._kontext()})
        elif t == "hinweis":
            self.status.setText(str(ev.get("text") or ""))
        elif t == "aktion":
            self._aktion(ev)
        elif t == "status":
            self.nemi.status(ev.get("id"), str(ev.get("text") or ""))
        elif t == "antwort":
            self.nemi.antwort(ev.get("id"), str(ev.get("text") or ""), geaendert=True)
        elif t == "fehler":
            self.nemi.fehler(ev.get("id"), str(ev.get("text") or ""))
            self.zeiger.ausblenden()
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _malt(self, an: bool, ev: dict) -> None:
        self.malt = an
        self.nemi.kugel.arbeiten(an, "malen")               # Ring dreht, solange ein Bild entsteht
        self.malen_knopf.setEnabled(not an)
        self.ab_knopf.setEnabled(not an)
        self.stopp_knopf.setEnabled(an)
        if an:
            self.balken.setRange(0, 0)                      # läuft, Schritte kommen gleich
            if ev.get("seed") is not None:
                self.seed_zuletzt = int(ev["seed"])
            if ev.get("vergleich"):
                self.vergleich = {}
                self.bild_a.leeren("A malt …")
                self.bild_b.leeren("B wartet …")
                self.bild_b.show()
        else:
            self.balken.setRange(0, 1)
            self.balken.setValue(1)

    def _bild(self, pfad: str, seite, einst: dict) -> None:
        if not pfad:
            return
        self.letztes = pfad
        if seite in ("A", "B"):
            feld = self.ab_feld.currentData()
            (self.bild_a if seite == "A" else self.bild_b).zeigen(pfad)
            (self.titel_a if seite == "A" else self.titel_b).setText(
                f"{seite}: {NAMEN.get(feld, feld)} = {einst.get(feld, '')}")
            self.vergleich[seite] = pfad
            self.vergleich["feld"] = feld
            self.vergleich[f"wert_{seite}"] = einst.get(feld)
        else:
            self.bild_b.hide()
            self.titel_a.setText(f"Seed {einst.get('seed', '')} · {einst.get('sampler', '')} · "
                                 f"{einst.get('zeitplan', '')} · {einst.get('schritte', '')} Schritte")
            self.titel_b.setText("")
            self.bild_a.zeigen(pfad)

    def _getrennt(self) -> None:
        self.leitung = None
        self.status.setText("Verbindung zur Sitzung getrennt – im Terminal /atelier neu öffnen.")
        self.nemi.kugel.arbeiten(False, "malen")
        self.nemi.fehler(self.nemi.laufend, "Verbindung zur Sitzung getrennt.")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.nemi.anordnen()

    def showEvent(self, e):
        super().showEvent(e)
        self.nemi.anordnen()


def _leitung():
    adresse = os.environ.pop("NEMICLI_GUI_PIPE", "")
    schluessel = os.environ.pop("NEMICLI_GUI_KEY", "")
    if not adresse or not schluessel:
        return None
    try:
        from gui_fenster import Leitung
        return Leitung(adresse, bytes.fromhex(schluessel))
    except Exception:
        return None


def starten() -> int:
    app = app_vorbereiten("NemiCLI.Atelier")
    fenster = AtelierFenster(_leitung())
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
