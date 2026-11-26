"""office_tabelle.py – 📊 Tabelle in Nemi Office: Raster mit Formelzeile, Blättern, Rückgängig und Zwischenablage.

Öffnet .ods .xlsx .csv .tsv und Markdown-Tabellen, speichert als .ods .xlsx .csv .tsv .md .html .pdf.
Formeln deutsch (=SUMME(A1:A3)) oder englisch (=SUM(A1:A3)); gerechnet wird mit buero.formel.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QMenu, QTabBar, QTableView, QToolButton, QVBoxLayout, QWidget)

from buero import ablage, formel as F
from buero.tabelle import Mappe, aus_markdown, format_sauber, ist_formel
from design import FARBE, schrift
import office_seiten as S
from office_teile import Leiste, Werkzeugknopf, WERKZEUG_STIL, frage_speichern, ja_nein, melden

MIN_ZEILEN, MIN_SPALTEN = 200, 26
RESERVE_ZEILEN, RESERVE_SPALTEN = 100, 8


class MappenModell(QAbstractTableModel):
    geaendert = Signal()

    def __init__(self, mappe: Mappe):
        super().__init__()
        self.mappe = mappe
        self.blatt_nr = 0
        self.rueck: list[list[tuple]] = []      # je Schritt: [("wert"|"format", blatt, zeile, spalte, alt, neu)]
        self.vor: list[list[tuple]] = []
        self.schrift = schrift(10.5)
        self._zeilen = self._spalten = 0
        self._masse()

    @property
    def blatt(self):
        return self.mappe.blaetter[self.blatt_nr]

    def _masse(self) -> None:
        z, s = self.blatt.groesse()
        self._zeilen = max(MIN_ZEILEN, z + RESERVE_ZEILEN)
        self._spalten = max(MIN_SPALTEN, s + RESERVE_SPALTEN)

    def neu_aufbauen(self) -> None:
        self.beginResetModel()
        self._masse()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._zeilen

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._spalten

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        z, s = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.mappe.anzeige(self.blatt, z, s)
        if role == Qt.ItemDataRole.EditRole:
            return self.mappe.roh(self.blatt, z, s)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            v = self.mappe.wert(self.blatt, z, s)
            if isinstance(v, (float, int)) and not isinstance(v, bool):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if isinstance(v, (bool, F.Fehler)):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            v = self.mappe.wert(self.blatt, z, s)
            if isinstance(v, F.Fehler):
                return QColor("#d93636")
            fg = self.mappe.format(self.blatt, z, s).get("fg")
            return QColor(fg) if fg else None
        if role == Qt.ItemDataRole.BackgroundRole:
            hg = self.mappe.format(self.blatt, z, s).get("hg")
            return QColor(hg) if hg else None
        if role == Qt.ItemDataRole.FontRole:
            if self.mappe.format(self.blatt, z, s).get("fett"):
                f = QFont(self.schrift)
                f.setBold(True)
                return f
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            roh = self.mappe.roh(self.blatt, z, s)
            return roh if ist_formel(roh) else None
        return None

    def headerData(self, nr, ausrichtung, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return F.spalte_name(nr) if ausrichtung == Qt.Orientation.Horizontal else str(nr + 1)

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable

    def setData(self, index, wert, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        self.setzen([(index.row(), index.column(), str(wert if wert is not None else ""))])
        return True

    def setzen(self, aenderungen: list[tuple[int, int, str]],
               formate: list[tuple[int, int, dict]] | None = None) -> None:
        """Mehrere Zellen (und auf Wunsch ihre Formate) in einem Rückgängig-Schritt setzen."""
        schritt = []
        b = self.blatt
        for z, s, neu in aenderungen:
            alt = self.mappe.roh(b, z, s)
            if alt != neu:
                self.mappe.setzen(b, z, s, neu)
                schritt.append(("wert", b.name, z, s, alt, neu))
        for z, s, neu_f in formate or []:
            alt_f = dict(self.mappe.format(b, z, s))
            if format_sauber(neu_f) != alt_f:
                self.mappe.format_setzen(b, z, s, neu_f)
                schritt.append(("format", b.name, z, s, alt_f, format_sauber(neu_f)))
        self._schritt(schritt)

    def formatieren(self, zellen: list[tuple[int, int]], aenderung: dict) -> None:
        """Farben/Fett für Zellen ändern; aenderung z. B. {"hg": "#d9d9d9"}, {"fg": ""} entfernt, {"fett": True}.
        Ein Rückgängig-Schritt."""
        schritt = []
        b = self.blatt
        for z, s in zellen:
            alt = dict(self.mappe.format(b, z, s))
            neu = {**alt, **aenderung}
            neu = {k: v for k, v in neu.items() if v}
            if format_sauber(neu) != alt:
                self.mappe.format_setzen(b, z, s, neu)
                schritt.append(("format", b.name, z, s, alt, format_sauber(neu)))
        self._schritt(schritt)

    def _schritt(self, schritt: list[tuple]) -> None:
        if not schritt:
            return
        self.rueck.append(schritt)
        self.rueck = self.rueck[-500:]
        self.vor.clear()
        self._nach_aenderung()

    def _nach_aenderung(self) -> None:
        self.mappe.berechnen()
        z, s = self.blatt.groesse()
        if z + RESERVE_ZEILEN // 2 > self._zeilen or s + RESERVE_SPALTEN // 2 > self._spalten:
            self.neu_aufbauen()
        else:
            self.dataChanged.emit(self.index(0, 0), self.index(self._zeilen - 1, self._spalten - 1))
        self.geaendert.emit()

    def _anwenden(self, schritt: list[tuple], rueckwaerts: bool) -> None:
        for art, name, z, s, alt, neu in schritt:
            b = self.mappe.blatt(name)
            if b is None:
                continue
            if art == "format":
                self.mappe.format_setzen(b, z, s, alt if rueckwaerts else neu)
            else:
                self.mappe.setzen(b, z, s, alt if rueckwaerts else neu)
        self._nach_aenderung()

    def rueckgaengig(self) -> bool:
        if not self.rueck:
            return False
        schritt = self.rueck.pop()
        self.vor.append(schritt)
        self._anwenden(schritt, True)
        return True

    def wiederholen(self) -> bool:
        if not self.vor:
            return False
        schritt = self.vor.pop()
        self.rueck.append(schritt)
        self._anwenden(schritt, False)
        return True


class Raster(QTableView):
    """Tabellenansicht: Enter geht nach unten, Entf leert, Strg+C/V tauscht Tab-Text mit anderen Programmen."""

    def __init__(self, seite: "TabellenSeite"):
        super().__init__()
        self.seite = seite
        self.seiten = True               # Druckgrenzen (A4) gestrichelt zeigen

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.seiten:
            from PySide6.QtGui import QPainter
            p = QPainter(self.viewport())
            S.grenzen_malen(self, p)
            p.end()

    def keyPressEvent(self, e):
        m = self.model()
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.state() != QAbstractItemView.State.EditingState:
            m.setzen([(i.row(), i.column(), "") for i in self.selectedIndexes()])
            return
        if e.matches(QKeySequence.StandardKey.Copy):
            self.seite.kopieren()
            return
        if e.matches(QKeySequence.StandardKey.Paste):
            self.seite.einfuegen()
            return
        if e.matches(QKeySequence.StandardKey.Cut):
            self.seite.kopieren()
            m.setzen([(i.row(), i.column(), "") for i in self.selectedIndexes()])
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.state() != QAbstractItemView.State.EditingState:
            i = self.currentIndex()
            if i.isValid():
                self.setCurrentIndex(m.index(min(i.row() + 1, m.rowCount() - 1), i.column()))
            return
        super().keyPressEvent(e)

    def closeEditor(self, editor, hint):
        super().closeEditor(editor, hint)
        if hint == QAbstractItemView.EndEditHint.SubmitModelCache:      # nach Enter eine Zeile tiefer
            i = self.currentIndex()
            if i.isValid():
                self.setCurrentIndex(self.model().index(min(i.row() + 1, self.model().rowCount() - 1), i.column()))


class TabellenSeite(QWidget):
    titel_geaendert = Signal(str)
    gespeichert = Signal(str)

    app = "tabelle"

    def __init__(self, ordner: str, parent=None):
        super().__init__(parent)
        self.ordner = ordner
        self.pfad: str | None = None
        self.modell = MappenModell(Mappe())
        self._veraendert = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.leiste = Leiste()
        v.addWidget(self.leiste)
        self._knoepfe()

        # Formelzeile
        zeile = QWidget()
        zeile.setObjectName("formelzeile")
        zeile.setStyleSheet(f"QWidget#formelzeile {{ background: {FARBE['leiste']}; border-bottom: 1px solid {FARBE['rand']}; }}"
                            f"QLineEdit {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid "
                            f"{FARBE['rand']}; border-radius: 7px; padding: 4px 8px; }}"
                            f"QLineEdit:focus {{ border: 1px solid {FARBE['cyan']}; }}")
        h = QHBoxLayout(zeile)
        h.setContentsMargins(10, 6, 10, 6)
        self.namensfeld = QLineEdit("A1")
        self.namensfeld.setFixedWidth(84)
        self.namensfeld.setToolTip("Zelle – hier tippen und Enter, um hinzuspringen")
        self.namensfeld.returnPressed.connect(self._springen)
        h.addWidget(self.namensfeld)
        fx = QToolButton()
        fx.setText("ƒx")
        fx.setStyleSheet(WERKZEUG_STIL)
        fx.setToolTip("Formel: mit = anfangen, z. B. =SUMME(B2:B9)")
        h.addWidget(fx)
        self.formelfeld = QLineEdit()
        self.formelfeld.setPlaceholderText("Wert oder Formel, z. B. =SUMME(B2:B9) · =WENN(B2>100;\"teuer\";\"ok\")")
        self.formelfeld.returnPressed.connect(self._formel_setzen)
        h.addWidget(self.formelfeld, 1)
        v.addWidget(zeile)

        self.raster = Raster(self)
        self.raster.setModel(self.modell)
        self.raster.setObjectName("raster")
        self.raster.setFont(schrift(10.5))
        self.raster.horizontalHeader().setDefaultSectionSize(110)
        self.raster.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.raster.verticalHeader().setDefaultSectionSize(28)
        self.raster.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.raster.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                                    | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.raster.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.raster.setStyleSheet(
            "QTableView#raster { background: #fdfdfb; color: #1f1f24; gridline-color: #dcdce3; border: none;"
            " selection-background-color: rgba(139, 92, 246, 60); selection-color: #1f1f24; }"
            f"QHeaderView::section {{ background: {FARBE['flaeche']}; color: {FARBE['dim']}; border: none;"
            f" border-right: 1px solid {FARBE['rand']}; border-bottom: 1px solid {FARBE['rand']}; padding: 3px; }}"
            f"QTableCornerButton::section {{ background: {FARBE['flaeche']}; border: none; }}"
            "QTableView QLineEdit { background: #ffffff; color: #1f1f24; border: 2px solid #8b5cf6; padding: 0 3px; }")
        self.raster.selectionModel().currentChanged.connect(lambda *_: self._zelle_zeigen())
        self.raster.horizontalHeader().sectionResized.connect(self._breite_merken)
        v.addWidget(self.raster, 1)

        # Blätter unten
        unten = QWidget()
        unten.setObjectName("blaetter")
        unten.setStyleSheet(f"QWidget#blaetter {{ background: {FARBE['leiste']}; border-top: 1px solid {FARBE['rand']}; }}"
                            f"QTabBar::tab {{ background: transparent; color: {FARBE['dim']}; padding: 5px 14px;"
                            f" border-radius: 7px; margin: 3px 2px; }}"
                            f"QTabBar::tab:selected {{ background: {FARBE['flaeche_hoch']}; color: {FARBE['text']}; }}"
                            f"QTabBar::tab:hover {{ background: {FARBE['flaeche_hell']}; }}" + WERKZEUG_STIL)
        hu = QHBoxLayout(unten)
        hu.setContentsMargins(8, 0, 8, 0)
        self.reiter = QTabBar()
        self.reiter.setDrawBase(False)
        self.reiter.setExpanding(False)
        self.reiter.currentChanged.connect(self._blatt_wechseln)
        self.reiter.tabBarDoubleClicked.connect(self._blatt_umbenennen)
        self.reiter.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.reiter.customContextMenuRequested.connect(self._blatt_menue)
        hu.addWidget(self.reiter)
        plus = Werkzeugknopf("＋", "Neues Blatt")
        plus.clicked.connect(self._blatt_dazu)
        hu.addWidget(plus)
        hu.addStretch(1)
        v.addWidget(unten)

        self.status = melden(self)
        v.addWidget(self.status)
        self.modell.geaendert.connect(self._geaendert)
        self._reiter_fuellen()
        self.raster.setCurrentIndex(self.modell.index(0, 0))
        for kurz, fn in (("Ctrl+S", self.speichern), ("Ctrl+Z", self.rueckgaengig), ("Ctrl+Y", self.wiederholen),
                         ("Ctrl+Shift+S", lambda: self.speichern(unter=True))):
            QShortcut(QKeySequence(kurz), self, fn, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _knoepfe(self) -> None:
        L = self.leiste
        L.knopf("💾", "Speichern (Strg+S)", self.speichern)
        L.knopf("📥", "Speichern unter …", lambda: self.speichern(unter=True))
        L.knopf("📄", "Als PDF speichern", self.als_pdf)
        L.trenner()
        L.knopf("↶", "Rückgängig (Strg+Z)", self.rueckgaengig)
        L.knopf("↷", "Wiederholen (Strg+Y)", self.wiederholen)
        L.trenner()
        L.knopf("Σ", "Summe der Zahlen darüber (oder links) in die Zelle", self.summe)
        L.trenner()
        L.farbknopf("🎨 Füllung ▾", "Markierte Zellen einfärben (z. B. grau hinterlegen)",
                    lambda c: self.formatieren({"hg": c}), "Füllfarbe", "Keine Füllung")
        L.farbknopf("A ▾", "Schriftfarbe der markierten Zellen", lambda c: self.formatieren({"fg": c}), "Schriftfarbe",
                    "Standardfarbe")
        L.knopf("B", "Fett an/aus für die markierten Zellen", self.fett_umschalten, fett=True)
        L.knopf("⌫", "Farben und Fett der markierten Zellen entfernen (Inhalt bleibt)",
                lambda: self.formatieren({"hg": "", "fg": "", "fett": False}))
        L.trenner()
        k_zeile = Werkzeugknopf("Zeile ▾", "Zeilen einfügen oder löschen")
        mz = QMenu(k_zeile)
        mz.addAction("Zeile darüber einfügen", lambda: self._achse("zeile", 0, True))
        mz.addAction("Zeile darunter einfügen", lambda: self._achse("zeile", 1, True))
        mz.addAction("Zeile löschen", lambda: self._achse("zeile", 0, False))
        k_zeile.setMenu(mz)
        k_zeile.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        L.dazu(k_zeile)
        k_spalte = Werkzeugknopf("Spalte ▾", "Spalten einfügen oder löschen")
        ms = QMenu(k_spalte)
        ms.addAction("Spalte links einfügen", lambda: self._achse("spalte", 0, True))
        ms.addAction("Spalte rechts einfügen", lambda: self._achse("spalte", 1, True))
        ms.addAction("Spalte löschen", lambda: self._achse("spalte", 0, False))
        k_spalte.setMenu(ms)
        k_spalte.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        L.dazu(k_spalte)
        L.trenner()
        L.knopf("⇅ A–Z", "Markierte Zeilen nach der aktuellen Spalte sortieren (aufsteigend)",
                lambda: self.sortieren(False), breit=True)
        L.knopf("⇅ Z–A", "Absteigend sortieren", lambda: self.sortieren(True), breit=True)
        L.dehnen()
        self.k_seiten = L.knopf("📄 Seiten", "Gestrichelt: wo eine gedruckte A4-Seite endet (wie im PDF)",
                                self.seiten_umschalten, umschalt=True, breit=True)
        self.k_seiten.setChecked(True)

    def seiten_umschalten(self, an: bool):
        if not hasattr(self, "raster"):         # beim Aufbau der Leiste gibt es das Raster noch nicht
            return
        self.raster.seiten = bool(an)
        self.raster.viewport().update()

    # -- Zellen ----------------------------------------------------------------------------
    def _zelle_zeigen(self) -> None:
        i = self.raster.currentIndex()
        if not i.isValid():
            return
        self.namensfeld.setText(F.zelle_name(i.row(), i.column()))
        self.formelfeld.setText(self.modell.mappe.roh(self.modell.blatt, i.row(), i.column()))
        self._auswahl_zeigen()

    def _auswahl_zeigen(self) -> None:
        zahlen = []
        for i in self.raster.selectedIndexes()[:100_000]:
            v = self.modell.mappe.wert(self.modell.blatt, i.row(), i.column())
            if isinstance(v, float) and not isinstance(v, bool):
                zahlen.append(v)
        if len(zahlen) > 1:
            self.status.rechts(f"Summe {F.zahl_text(sum(zahlen))} · Mittel {F.zahl_text(sum(zahlen) / len(zahlen))}"
                               f" · Anzahl {len(zahlen)}")
        else:
            self.status.rechts(self.modell.blatt.name)

    def _springen(self) -> None:
        pos = F.zelle_aus(self.namensfeld.text())
        if pos is None:
            self.status.zeigen("Zelle wie B7 eingeben.", "warn")
            return
        while pos[0] >= self.modell.rowCount() or pos[1] >= self.modell.columnCount():
            self.modell._zeilen = max(self.modell._zeilen, pos[0] + RESERVE_ZEILEN)
            self.modell._spalten = max(self.modell._spalten, pos[1] + RESERVE_SPALTEN)
            self.modell.neu_aufbauen()
        i = self.modell.index(*pos)
        self.raster.setCurrentIndex(i)
        self.raster.scrollTo(i)
        self.raster.setFocus()

    def _formel_setzen(self) -> None:
        i = self.raster.currentIndex()
        if not i.isValid():
            return
        self.modell.setzen([(i.row(), i.column(), self.formelfeld.text())])
        self.raster.setCurrentIndex(self.modell.index(min(i.row() + 1, self.modell.rowCount() - 1), i.column()))
        self.raster.setFocus()

    def rueckgaengig(self) -> None:
        if not self.modell.rueckgaengig():
            self.status.zeigen("Nichts mehr rückgängig zu machen.")
        self._zelle_zeigen()

    def wiederholen(self) -> None:
        self.modell.wiederholen()
        self._zelle_zeigen()

    def summe(self) -> None:
        i = self.raster.currentIndex()
        if not i.isValid():
            return
        m, b = self.modell.mappe, self.modell.blatt

        def zahl(z, s):
            v = m.wert(b, z, s)
            return isinstance(v, float) and not isinstance(v, bool)

        z, s = i.row(), i.column()
        oben = z
        while oben > 0 and zahl(oben - 1, s):
            oben -= 1
        if oben < z:
            text = f"=SUMME({F.zelle_name(oben, s)}:{F.zelle_name(z - 1, s)})"
        else:
            links = s
            while links > 0 and zahl(z, links - 1):
                links -= 1
            if links == s:
                self.status.zeigen("Über oder links der Zelle stehen keine Zahlen.", "warn")
                return
            text = f"=SUMME({F.zelle_name(z, links)}:{F.zelle_name(z, s - 1)})"
        self.modell.setzen([(z, s, text)])
        self._zelle_zeigen()

    def _markiert(self) -> list[tuple[int, int]]:
        zellen = [(i.row(), i.column()) for i in self.raster.selectedIndexes()]
        if not zellen and (i := self.raster.currentIndex()).isValid():
            zellen = [(i.row(), i.column())]
        return zellen[:200_000]

    def formatieren(self, aenderung: dict) -> None:
        zellen = self._markiert()
        if zellen:
            self.modell.formatieren(zellen, aenderung)
            self.raster.setFocus()

    def fett_umschalten(self) -> None:
        zellen = self._markiert()
        if zellen:
            alle_fett = all(self.modell.mappe.format(self.modell.blatt, z, s).get("fett") for z, s in zellen)
            self.modell.formatieren(zellen, {"fett": not alle_fett})

    def _achse(self, achse: str, versatz: int, einfuegen: bool) -> None:
        i = self.raster.currentIndex()
        if not i.isValid():
            return
        ab = (i.row() if achse == "zeile" else i.column()) + (versatz if einfuegen else 0)
        self.struktur(achse, ab, 1, einfuegen)
        self.raster.setCurrentIndex(self.modell.index(i.row(), i.column()))

    def struktur(self, achse: str, ab: int, anzahl: int, einfuegen: bool) -> None:
        """Zeilen/Spalten einfügen oder löschen; Formeln und Farben wandern mit."""
        if einfuegen:
            self.modell.mappe.einfuegen(self.modell.blatt, achse, ab, anzahl)
        else:
            self.modell.mappe.loeschen(self.modell.blatt, achse, ab, anzahl)
        self.modell.rueck.clear()                        # Struktur geändert: alte Schritte passen nicht mehr
        self.modell.vor.clear()
        self.modell.mappe.berechnen()
        self.modell.neu_aufbauen()
        self._breiten_setzen()
        self._geaendert()
        self.status.zeigen(("Eingefügt" if einfuegen else "Gelöscht") + " – Formeln sind angepasst.", "ok")

    def sortieren(self, absteigend: bool) -> None:
        auswahl = self.raster.selectedIndexes()
        i = self.raster.currentIndex()
        if not auswahl or not i.isValid():
            return
        zeilen = sorted({x.row() for x in auswahl})
        if len(zeilen) < 2:
            self.status.zeigen("Erst mehrere Zeilen markieren.", "warn")
            return
        ok, text = self.sortieren_bereich(zeilen[0], zeilen[-1], i.column(), absteigend, zeilen)
        if not ok:
            self.status.zeigen(text, "warn")

    def sortieren_bereich(self, z1: int, z2: int, spalte: int, absteigend: bool,
                          zeilen: list[int] | None = None) -> tuple[bool, str]:
        """Zeilen z1..z2 (oder die genannten) nach einer Spalte sortieren; Farben gehen mit. Ein Rückgängig-Schritt."""
        zeilen = zeilen or list(range(z1, z2 + 1))
        if len(zeilen) < 2:
            return False, "Zum Sortieren braucht es mindestens zwei Zeilen."
        m, b = self.modell.mappe, self.modell.blatt
        spalten = max(b.groesse()[1], spalte + 1)
        if any(ist_formel(m.roh(b, z, s)) for z in zeilen for s in range(spalten)):
            return False, "Zeilen mit Formeln sortiere ich nicht – die Bezüge würden verrutschen."

        def schluessel(z):
            v = m.wert(b, z, spalte)
            if v is None or v == "":
                return (2, 0.0, "")
            if isinstance(v, float) and not isinstance(v, bool):
                return (0, v, "")
            return (1, 0.0, str(v).lower())

        reihenfolge = sorted(zeilen, key=schluessel, reverse=absteigend)
        inhalt = {z: [m.roh(b, z, s) for s in range(spalten)] for z in zeilen}
        formate = {z: [dict(m.format(b, z, s)) for s in range(spalten)] for z in zeilen}
        paare = list(zip(zeilen, reihenfolge, strict=True))
        self.modell.setzen([(ziel, s, inhalt[quelle][s]) for ziel, quelle in paare for s in range(spalten)],
                           [(ziel, s, formate[quelle][s]) for ziel, quelle in paare for s in range(spalten)])
        return True, f"{len(zeilen)} Zeilen nach Spalte {F.spalte_name(spalte)} sortiert."

    # -- Zwischenablage --------------------------------------------------------------------
    def _bereich(self) -> tuple[int, int, int, int] | None:
        auswahl = self.raster.selectedIndexes()
        if not auswahl:
            return None
        zs, ss = [i.row() for i in auswahl], [i.column() for i in auswahl]
        return min(zs), min(ss), max(zs), max(ss)

    def kopieren(self) -> None:
        bereich = self._bereich()
        if bereich is None:
            return
        z1, s1, z2, s2 = bereich
        m, b = self.modell.mappe, self.modell.blatt
        text = "\n".join("\t".join(m.anzeige(b, z, s) for s in range(s1, s2 + 1)) for z in range(z1, z2 + 1))
        QApplication.clipboard().setText(text)

    def einfuegen(self) -> None:
        text = QApplication.clipboard().text()
        i = self.raster.currentIndex()
        if not text or not i.isValid():
            return
        zeilen = aus_markdown(text) if text.lstrip().startswith("|") else \
            [z.split("\t") for z in text.rstrip("\r\n").replace("\r\n", "\n").split("\n")]
        self._zeilen_setzen(zeilen, i.row(), i.column())

    def _zeilen_setzen(self, zeilen: list[list[str]], z0: int, s0: int) -> int:
        aenderungen = []
        for dz, zeile in enumerate(zeilen[:100_000]):
            for ds, wert in enumerate(zeile[:2000]):
                aenderungen.append((z0 + dz, s0 + ds, wert.strip()))
        self.modell.setzen(aenderungen)
        return len(aenderungen)

    # -- Blätter ---------------------------------------------------------------------------
    def _reiter_fuellen(self) -> None:
        self.reiter.blockSignals(True)
        while self.reiter.count():
            self.reiter.removeTab(0)
        for b in self.modell.mappe.blaetter:
            self.reiter.addTab(b.name)
        self.reiter.setCurrentIndex(self.modell.blatt_nr)
        self.reiter.blockSignals(False)

    def _blatt_wechseln(self, nr: int) -> None:
        if 0 <= nr < len(self.modell.mappe.blaetter):
            self.modell.blatt_nr = nr
            self.modell.neu_aufbauen()
            self._breiten_setzen()
            self.raster.setCurrentIndex(self.modell.index(0, 0))
            self._zelle_zeigen()

    def _blatt_dazu(self) -> None:
        self.modell.mappe.blatt_dazu()
        self._reiter_fuellen()
        self.reiter.setCurrentIndex(len(self.modell.mappe.blaetter) - 1)
        self._geaendert()

    def _blatt_umbenennen(self, nr: int) -> None:
        if nr < 0:
            return
        alt = self.modell.mappe.blaetter[nr].name
        neu, ok = QInputDialog.getText(self, "Blatt umbenennen", "Neuer Name:", text=alt)
        if not ok or not neu.strip() or neu == alt:
            return
        try:
            self.modell.mappe.umbenennen(alt, neu)
        except ValueError as e:
            self.status.zeigen(str(e), "warn")
            return
        self.modell.mappe.berechnen()
        self._reiter_fuellen()
        self.modell.neu_aufbauen()
        self._geaendert()

    def _blatt_menue(self, punkt) -> None:
        nr = self.reiter.tabAt(punkt)
        if nr < 0:
            return
        menue = QMenu(self)
        menue.setStyleSheet(WERKZEUG_STIL)
        menue.addAction("Umbenennen …", lambda: self._blatt_umbenennen(nr))
        if len(self.modell.mappe.blaetter) > 1:
            menue.addAction("Blatt löschen", lambda: self._blatt_loeschen(nr))
        menue.exec(self.reiter.mapToGlobal(punkt))

    def _blatt_loeschen(self, nr: int) -> None:
        b = self.modell.mappe.blaetter[nr]
        if b.zellen and not ja_nein(self, f"Blatt „{b.name}“ mit {len(b.zellen)} Zellen löschen?", "Löschen", "Behalten"):
            return
        del self.modell.mappe.blaetter[nr]
        self.modell.blatt_nr = max(0, min(self.modell.blatt_nr, len(self.modell.mappe.blaetter) - 1))
        self.modell.rueck.clear()
        self.modell.vor.clear()
        self.modell.mappe.berechnen()
        self._reiter_fuellen()
        self.modell.neu_aufbauen()
        self._geaendert()

    def _breite_merken(self, spalte: int, _alt: int, neu: int) -> None:
        self.modell.blatt.breiten[spalte] = neu

    def _breiten_setzen(self) -> None:
        kopf = self.raster.horizontalHeader()
        kopf.blockSignals(True)
        for s in range(self.modell.columnCount()):
            kopf.resizeSection(s, self.modell.blatt.breiten.get(s, kopf.defaultSectionSize()))
        kopf.blockSignals(False)

    # -- Laden / Speichern -----------------------------------------------------------------
    def laden(self, pfad: str) -> None:
        mappe = ablage.tabelle_laden(pfad)
        self.modell.mappe = mappe
        self.modell.blatt_nr = 0
        self.modell.rueck.clear()
        self.modell.vor.clear()
        self.modell.neu_aufbauen()
        self._reiter_fuellen()
        self._breiten_setzen()
        end = Path(pfad).suffix.lower()
        self.pfad = pfad if end in ablage.TABELLE_SPEICHERN and end not in (".pdf", ".html") else None
        if end == ".xlsm":
            self.pfad = None                                 # Makro-Mappen nicht ohne Makros überschreiben
        self._veraendert = False
        self.raster.setCurrentIndex(self.modell.index(0, 0))
        self._zelle_zeigen()
        self._titel()

    def ungespeichert(self) -> bool:
        return self._veraendert

    def speichern(self, unter: bool = False) -> bool:
        if self.raster.state() == QAbstractItemView.State.EditingState:
            self.raster.setFocus()
        pfad = self.pfad
        if unter or not pfad:
            vorschlag = pfad or str(ablage.neuer_pfad(self.ordner, "Tabelle", ".ods"))
            pfad, _ = QFileDialog.getSaveFileName(self, "Speichern unter", vorschlag,
                                                  ablage.filter_text(ablage.TABELLE_SPEICHERN, alle=False))
            if not pfad:
                return False
            if not Path(pfad).suffix:
                pfad += ".ods"
        end = Path(pfad).suffix.lower()
        if end == ".pdf":
            return self.als_pdf(pfad)
        if end in (".csv", ".tsv", ".md") and len(self.modell.mappe.blaetter) > 1:
            self.status.zeigen(f"{end} speichert nur das Blatt „{self.modell.blatt.name}“.", "warn", 10)
        try:
            ablage.tabelle_speichern(pfad, self.modell.mappe, self.modell.blatt_nr)
        except Exception as e:
            self.status.zeigen(f"Nicht gespeichert: {e}", "fehler")
            return False
        if end in (".ods", ".xlsx", ".csv", ".tsv", ".md"):
            self.pfad = pfad
            self._veraendert = False
        self._titel()
        self.status.zeigen(f"✓ Gespeichert: {Path(pfad).name}", "ok")
        self.gespeichert.emit(pfad)
        return True

    def als_pdf(self, pfad: str | None = None) -> bool:
        if not pfad:
            basis = Path(self.pfad).with_suffix(".pdf") if self.pfad else ablage.neuer_pfad(self.ordner, "Tabelle", ".pdf")
            pfad, _ = QFileDialog.getSaveFileName(self, "Als PDF speichern", str(basis), "PDF (*.pdf)")
            if not pfad:
                return False
        try:
            import atomar
            daten = S.tabelle_pdf_bytes(self.modell.mappe, self.raster.horizontalHeader().defaultSectionSize(),
                                        self.raster.verticalHeader().defaultSectionSize())
            atomar.bytes_schreiben(pfad, daten)
        except Exception as e:
            self.status.zeigen(f"PDF nicht geschrieben: {e}", "fehler")
            return False
        self.status.zeigen(f"✓ PDF gespeichert: {Path(pfad).name}", "ok")
        self.gespeichert.emit(pfad)
        return True

    def name(self) -> str:
        return Path(self.pfad).name if self.pfad else "Neue Tabelle"

    def _titel(self) -> None:
        self.titel_geaendert.emit(("● " if self._veraendert else "") + self.name())

    def _geaendert(self) -> None:
        self._veraendert = True
        self._titel()
        self._zelle_zeigen()

    # -- für Nemi --------------------------------------------------------------------------
    def kontext(self, grenze: int = 24000) -> dict:
        m, b = self.modell.mappe, self.modell.blatt
        text = m.als_markdown(b, formeln=True)
        if len(text) > grenze:
            text = text[:grenze] + "\n[… gekürzt …]"
        i = self.raster.currentIndex()
        auswahl = ""
        bereich = self._bereich()
        if bereich:
            z1, s1, z2, s2 = bereich
            auswahl = f"Markiert: {F.zelle_name(z1, s1)}:{F.zelle_name(z2, s2)}"
        if i.isValid():
            roh = m.roh(b, i.row(), i.column())
            auswahl += f"\nAktuelle Zelle: {F.zelle_name(i.row(), i.column())}" + (f" = {roh}" if roh else " (leer)")
        blaetter = ", ".join(x.name for x in m.blaetter)
        return {"app": self.app, "name": f"{self.name()} · Blatt {b.name} (Blätter: {blaetter})",
                "kontext": text, "auswahl": auswahl.strip()}

    def uebernehmen(self, text: str) -> str:
        """Tabelle von Nemi ab der aktuellen Zelle eintragen; ein einzelner Wert oder eine Formel in die Zelle."""
        i = self.raster.currentIndex()
        if not i.isValid():
            i = self.modell.index(0, 0)
        zeilen = aus_markdown(text)
        if zeilen:
            n = self._zeilen_setzen(zeilen, i.row(), i.column())
            return f"{n} Zellen ab {F.zelle_name(i.row(), i.column())} eingetragen"
        wert = text.strip().splitlines()[0].strip() if text.strip() else ""
        if not wert:
            return "nichts zum Eintragen"
        self.modell.setzen([(i.row(), i.column(), wert)])
        self._zelle_zeigen()
        return f"in {F.zelle_name(i.row(), i.column())} eingetragen"

    def schliessen_ok(self) -> bool:
        if not self.ungespeichert():
            return True
        wahl = frage_speichern(self, self.name())
        if wahl == "speichern":
            return self.speichern()
        return wahl == "verwerfen"

