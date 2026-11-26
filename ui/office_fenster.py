"""office_fenster.py – 🗂 Nemi Office (/office): Startseite mit App-Kacheln, Reiter für offene Dokumente und die
OfficeAI-Kugel unten rechts, die selbst im geöffneten Dokument arbeitet (office_kugel.py, office_agent.py). Eigener Prozess an der laufenden Sitzung (Leitung wie gui_fenster); Schreiben und Tabelle laufen auch
ohne Verbindung, nur Nemi braucht die Sitzung.

Dokumente liegen standardmäßig in Profile/<Persönlichkeit>/Office/; geöffnet wird nur, was der Nutzer selbst wählt
(Dialog, Ziehen, Liste „zuletzt benutzt“).
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImageReader, QKeySequence, QPainter, QPainterPath,
                           QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QStackedWidget, QStyle, QStyledItemDelegate, QTabBar, QVBoxLayout, QWidget)

from buero import ablage
from design import FARBE, farbe, schrift
from gui_fenster import Logo, _dunkler_rahmen, app_vorbereiten, label
from office_agent import Ausfuehrer, Geistzeiger
from office_kugel import OfficeAI
from office_malen import MalSeite, mal_icon
from office_schreiben import SchreibSeite
from office_tabelle import TabellenSeite
from office_teile import APP_FARBEN, AppKachel, FlussLayout, WERKZEUG_STIL, Werkzeugknopf

ROLLE_EINTRAG = Qt.ItemDataRole.UserRole
SYMBOL = {"schreiben": "✍️ ", "tabelle": "📊 ", "malen": ""}      # Malen trägt sein eigenes Symbol im Reiter
SEITEN = {"schreiben": SchreibSeite, "tabelle": TabellenSeite, "malen": MalSeite}
_MINIATUREN: dict[str, tuple[float, QPixmap]] = {}              # Vorschaubilder der Liste „zuletzt“


def miniatur(pfad: str, groesse: QSize) -> QPixmap | None:
    """Verkleinertes Bild für die Karte (gelesen nur in Kartengröße, je Dateistand einmal)."""
    try:
        stand = Path(pfad).stat().st_mtime
    except OSError:
        return None
    alt = _MINIATUREN.get(pfad)
    if alt is not None and alt[0] == stand:
        return alt[1]
    leser = QImageReader(pfad)
    leser.setAutoTransform(True)
    g = leser.size()
    if g.isValid() and g.width() > 0 and g.height() > 0:
        leser.setScaledSize(g.scaled(groesse, Qt.AspectRatioMode.KeepAspectRatio))
    bild = leser.read()
    pm = None if bild.isNull() else QPixmap.fromImage(bild)
    _MINIATUREN[pfad] = (stand, pm)
    return pm


def standard_ordner() -> str:
    try:
        import profilordner
        return str(profilordner.ordner("Office"))
    except Exception:
        return str(Path.home() / "Documents")


# ---------------------------------------------------------------------------
# Zuletzt benutzt: Karten mit Mini-Seite
# ---------------------------------------------------------------------------

class _KartenMaler(QStyledItemDelegate):
    BREITE, HOEHE = 172, 236

    def sizeHint(self, *_):
        return QSize(self.BREITE, self.HOEHE)

    def paint(self, p: QPainter, opt, index):
        e = index.data(ROLLE_EINTRAG) or {}
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(opt.rect).adjusted(6, 6, -6, -6)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        gewaehlt = bool(opt.state & QStyle.StateFlag.State_Selected)
        if hover or gewaehlt:
            karte = QPainterPath()
            karte.addRoundedRect(r, 14, 14)
            p.fillPath(karte, farbe("flaeche_hell" if hover else "flaeche"))
        # Mini-Seite
        seite = QRectF(r.left() + 22, r.top() + 10, r.width() - 44, 150)
        pfad = QPainterPath()
        pfad.addRoundedRect(seite, 6, 6)
        p.fillPath(pfad, QColor("#fdfdfb"))
        vorschau = e.get("vorschau")
        bild = miniatur(e["pfad"], seite.size().toSize()) if e.get("app") == "malen" and e.get("pfad") else None
        if bild is not None:
            ziel = QRectF(0, 0, bild.width(), bild.height())
            ziel.moveCenter(seite.center())
            p.save()
            p.setClipPath(pfad)
            p.drawPixmap(ziel, bild, QRectF(bild.rect()))
            p.restore()
            vorschau = None
        p.setPen(QColor("#6b6b78"))
        f = QFont(schrift(5.2))
        p.setFont(f)
        if isinstance(vorschau, list) and vorschau:
            zeilen = vorschau[:8]
            spalten = max(len(z) for z in zeilen) or 1
            zb, zh = (seite.width() - 12) / spalten, 14
            p.setPen(QPen(QColor("#d6d6de"), 0.6))
            for i in range(len(zeilen) + 1):
                y = seite.top() + 8 + i * zh
                p.drawLine(int(seite.left() + 6), int(y), int(seite.right() - 6), int(y))
            for j in range(spalten + 1):
                x = seite.left() + 6 + j * zb
                p.drawLine(int(x), int(seite.top() + 8), int(x), int(seite.top() + 8 + len(zeilen) * zh))
            p.setPen(QColor("#4a4a55"))
            for i, z in enumerate(zeilen):
                for j, c in enumerate(z):
                    p.drawText(QRectF(seite.left() + 8 + j * zb, seite.top() + 8 + i * zh, zb - 4, zh),
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, str(c))
        elif bild is not None:
            pass
        elif isinstance(vorschau, str) and vorschau.strip():
            p.drawText(seite.adjusted(9, 9, -9, -9), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop |
                       Qt.TextFlag.TextWordWrap, vorschau[:420])
        else:
            p.setPen(QPen(QColor("#d6d6de"), 2))
            for i in range(7):
                y = seite.top() + 22 + i * 15
                p.drawLine(int(seite.left() + 14), int(y), int(seite.right() - 14 - (30 if i % 3 == 2 else 0)), int(y))
        # Abzeichen mit Endung
        app = e.get("app") or "schreiben"
        endung = Path(e.get("pfad") or "").suffix.lower().lstrip(".") or "?"
        von, _ = APP_FARBEN.get(app, APP_FARBEN["schreiben"])
        abz = QRectF(seite.left() - 6, seite.bottom() - 24, 46, 20)
        ap = QPainterPath()
        ap.addRoundedRect(abz, 6, 6)
        p.fillPath(ap, QColor(von))
        p.setPen(QColor("#ffffff"))
        p.setFont(schrift(7.5, True))
        p.drawText(abz, Qt.AlignmentFlag.AlignCenter, endung.upper()[:5])
        # Name und Datum
        p.setPen(farbe("text"))
        p.setFont(schrift(9.5, True))
        name = Path(e.get("pfad") or "").stem
        fm = p.fontMetrics()
        p.drawText(QRectF(r.left() + 8, seite.bottom() + 10, r.width() - 16, 20), Qt.AlignmentFlag.AlignHCenter,
                   fm.elidedText(name, Qt.TextElideMode.ElideMiddle, int(r.width() - 16)))
        p.setPen(farbe("blass"))
        p.setFont(schrift(8.5))
        zeit = e.get("zeit") or 0
        wann = _dt.datetime.fromtimestamp(zeit).strftime("%d.%m.%Y %H:%M") if zeit else ""
        p.drawText(QRectF(r.left() + 8, seite.bottom() + 30, r.width() - 16, 18), Qt.AlignmentFlag.AlignHCenter, wann)
        p.restore()


class ZuletztListe(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setMouseTracking(True)
        self.setItemDelegate(_KartenMaler(self))
        self.setGridSize(QSize(_KartenMaler.BREITE + 8, _KartenMaler.HOEHE + 8))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QListWidget { background: transparent; } QListWidget::item { background: transparent; }")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def fuellen(self, eintraege: list[dict], filter_text: str = "") -> None:
        self.clear()
        f = filter_text.strip().lower()
        for e in eintraege:
            if f and f not in Path(e["pfad"]).name.lower():
                continue
            it = QListWidgetItem()
            it.setData(ROLLE_EINTRAG, e)
            it.setToolTip(e["pfad"])
            it.setSizeHint(QSize(_KartenMaler.BREITE, _KartenMaler.HOEHE))
            self.addItem(it)


# ---------------------------------------------------------------------------
# Startseite
# ---------------------------------------------------------------------------

class StartSeite(QWidget):
    def __init__(self, fenster: "OfficeFenster"):
        super().__init__()
        self.fenster = fenster
        self.eintraege: list[dict] = []
        self.setObjectName("start")
        self.setStyleSheet(f"QWidget#start {{ background: {FARBE['grund']}; }}"
                           f"QLineEdit {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid "
                           f"{FARBE['rand']}; border-radius: 9px; padding: 6px 10px; }}"
                           f"QLineEdit:focus {{ border: 1px solid {FARBE['cyan']}; }}" + WERKZEUG_STIL)
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 30, 40, 16)
        v.setSpacing(14)
        self.gruss = label("Was möchtest du machen?", 20, "text", fett=True)
        v.addWidget(self.gruss)
        v.addWidget(label("Neues Dokument anlegen, eins öffnen – oder einfach eine Datei hier hineinziehen.", 10.5, "dim"))

        kachel_feld = QWidget()
        kacheln = FlussLayout(kachel_feld, abstand=16)      # bricht um, wenn das Fenster schmal ist
        kacheln.setContentsMargins(0, 0, 0, 0)
        for zeichen, titel, unter, app, fn in (
                ("✍️", "Schreiben", "Briefe, Texte, Notizen · Markdown, Word, OpenDocument", "schreiben",
                 lambda: fenster.neu("schreiben")),
                ("📊", "Tabelle", "Listen, Formeln, Haushalt · Excel, OpenDocument, CSV", "tabelle",
                 lambda: fenster.neu("tabelle")),
                (mal_icon(108), "Malen", "Zeichnen, malen, Bilder bearbeiten · PNG, JPEG, WebP", "malen",
                 lambda: fenster.neu("malen")),
                ("📂", "Öffnen", "Datei vom PC öffnen – alle unterstützten Formate", "oeffnen", fenster.oeffnen_dialog)):
            k = AppKachel(zeichen, titel, unter, APP_FARBEN[app])
            k.clicked.connect(fn)
            kacheln.addWidget(k)
        v.addWidget(kachel_feld)

        kopf = QHBoxLayout()
        kopf.addWidget(label("Zuletzt benutzt", 13, "text", fett=True))
        kopf.addStretch(1)
        self.suche = QLineEdit()
        self.suche.setPlaceholderText("🔍 Suchen …")
        self.suche.setFixedWidth(240)
        self.suche.textChanged.connect(lambda t: self.liste.fuellen(self.eintraege, t))
        kopf.addWidget(self.suche)
        ordner = Werkzeugknopf("📁 Ordner", "Den Office-Ordner im Explorer öffnen")
        ordner.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.fenster.ordner)))
        kopf.addWidget(ordner)
        v.addSpacing(8)
        v.addLayout(kopf)
        self.liste = ZuletztListe()
        self.liste.itemActivated.connect(self._oeffnen)
        self.liste.itemClicked.connect(self._oeffnen)
        self.liste.customContextMenuRequested.connect(self._menue)
        v.addWidget(self.liste, 1)
        self.leer = label("Hier erscheinen deine Dokumente, sobald du eins öffnest oder speicherst.", 10.5, "blass")
        v.addWidget(self.leer)
        self.auffrischen()

    def name_setzen(self, name: str) -> None:
        self.gruss.setText("Hey 😊 Was machen wir heute?" if not name else f"Was machen wir heute? · {name} ist dabei 💜")

    def auffrischen(self) -> None:
        try:
            self.eintraege = ablage.zuletzt(self.fenster.ordner)
        except Exception:
            self.eintraege = []
        self.liste.fuellen(self.eintraege, self.suche.text())
        self.leer.setVisible(not self.eintraege)

    def _oeffnen(self, it: QListWidgetItem) -> None:
        e = it.data(ROLLE_EINTRAG) or {}
        if e.get("pfad"):
            self.fenster.oeffnen(e["pfad"], e.get("app"))

    def _menue(self, punkt) -> None:
        it = self.liste.itemAt(punkt)
        if it is None:
            return
        e = it.data(ROLLE_EINTRAG) or {}
        menue = QMenu(self)
        menue.setStyleSheet(WERKZEUG_STIL)
        menue.addAction("Öffnen", lambda: self.fenster.oeffnen(e["pfad"], e.get("app")))
        menue.addAction("Im Ordner zeigen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(e["pfad"]).parent))))
        menue.addAction("Aus der Liste nehmen (Datei bleibt)", lambda: self._vergessen(e["pfad"]))
        menue.exec(self.liste.mapToGlobal(punkt))

    def _vergessen(self, pfad: str) -> None:
        try:
            ablage.vergessen(self.fenster.ordner, pfad)
        except Exception:
            pass
        self.auffrischen()


# ---------------------------------------------------------------------------
# Das Fenster
# ---------------------------------------------------------------------------

class OfficeFenster(QWidget):
    def __init__(self, leitung=None):
        super().__init__()
        self.leitung = leitung
        self.ordner = standard_ordner()
        self.setObjectName("office")
        self.setWindowTitle("Nemi Office")
        self.setAcceptDrops(True)
        self.resize(1400, 900)
        self.setStyleSheet(f"QWidget#office {{ background: {FARBE['grund']}; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        oben = QFrame()
        oben.setObjectName("oben")
        oben.setStyleSheet(f"QFrame#oben {{ background: {FARBE['leiste']}; border-bottom: 1px solid {FARBE['rand']}; }}"
                           f"QTabBar::tab {{ background: transparent; color: {FARBE['dim']}; padding: 7px 14px;"
                           f" margin: 4px 2px; border-radius: 8px; }}"
                           f"QTabBar::tab:selected {{ background: {FARBE['flaeche_hoch']}; color: {FARBE['text']}; }}"
                           f"QTabBar::tab:hover {{ background: {FARBE['flaeche_hell']}; }}" + WERKZEUG_STIL)
        ho = QHBoxLayout(oben)
        ho.setContentsMargins(12, 2, 10, 2)
        ho.addWidget(Logo(15))
        ho.addWidget(label(" Nemi Office", 12, "text", fett=True))
        ho.addSpacing(14)
        self.reiter = QTabBar()
        self.reiter.setDrawBase(False)
        self.reiter.setExpanding(False)
        self.reiter.setTabsClosable(True)
        self.reiter.setMovable(False)
        self.reiter.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.reiter.currentChanged.connect(self._reiter_gewechselt)
        self.reiter.tabCloseRequested.connect(self.reiter_schliessen)
        ho.addWidget(self.reiter, 1)
        self.k_nemi = Werkzeugknopf("🔮 OfficeAI", "Die OfficeAI-Kugel ein- oder ausblenden")
        self.k_nemi.setCheckable(True)
        self.k_nemi.setChecked(True)
        self.k_nemi.toggled.connect(self._kugel_zeigen)
        ho.addWidget(self.k_nemi)
        v.addWidget(oben)

        self.stapel = QStackedWidget()
        v.addWidget(self.stapel, 1)
        self.zeiger = Geistzeiger(self)
        self.ausfuehrer = Ausfuehrer(self, self.zeiger)
        self.nemi = OfficeAI(self, self._kontext)
        self.nemi.frage_senden.connect(self._frage)
        self.nemi.stopp.connect(self._anhalten)
        self._auftrag_seite: dict[int, QWidget] = {}
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.nemi.anhalten)
        self._esc.setEnabled(False)

        self.start = StartSeite(self)
        self.stapel.addWidget(self.start)
        self.reiter.addTab("🏠 Start")
        self.reiter.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        self.reiter.setTabButton(0, QTabBar.ButtonPosition.LeftSide, None)

        for kurz, fn in (("Ctrl+N", lambda: self.neu("schreiben")), ("Ctrl+Shift+N", lambda: self.neu("tabelle")),
                         ("Ctrl+Shift+B", lambda: self.neu("malen")),
                         ("Ctrl+O", self.oeffnen_dialog), ("Ctrl+W", lambda: self.reiter_schliessen(
                             self.reiter.currentIndex())), ("Ctrl+Tab", self._naechster_reiter)):
            QShortcut(QKeySequence(kurz), self, fn)

        if leitung is not None:
            leitung.ereignis.connect(self._ereignis)
            leitung.getrennt.connect(self._getrennt)
            leitung.senden({"op": "stand"})
        else:
            self.nemi.stand("Nemi", False)

    # -- Seiten ----------------------------------------------------------------------------
    def _seiten(self) -> list:
        return [self.stapel.widget(i) for i in range(1, self.stapel.count())]

    def aktuelle_seite(self):
        w = self.stapel.currentWidget()
        return None if w is self.start else w

    def _dazu(self, seite, titel: str) -> None:
        self.stapel.addWidget(seite)
        nr = self.reiter.addTab(titel)
        if seite.app == "malen":
            self.reiter.setTabIcon(nr, QIcon(mal_icon(32)))
        seite.titel_geaendert.connect(lambda t, s=seite: self._titel(s, t))
        seite.gespeichert.connect(lambda pfad, s=seite: self._gemerkt(s, pfad))
        if hasattr(seite, "bild_wunsch"):
            seite.bild_wunsch.connect(lambda text, s=seite: self._bild_wunsch(text, s.app))
        self.reiter.setCurrentIndex(nr)

    def _bild_wunsch(self, text: str, app: str = "schreiben") -> None:
        """„🎨 Bild malen“: die OfficeAI schreibt den Krea-Prompt, malt und setzt das Bild an der Schreibmarke ein.
        In 🎨 Malen entscheidet sie selbst: zeichnen (Form für Form) oder – für Fotos und Realistisches – bild."""
        if app == "malen":
            self.nemi.fragen(f"Mal auf die Leinwand: {text}\nZeichne es selbst mit zeichnen (erst Hintergrund und "
                             "große Flächen, dann Formen, dann Details) – nur bei Fotos oder Realistischem nimm bild. "
                             "Prüf das Ergebnis danach mit anschauen, wenn es geht.", f"🎨 {text}")
            return
        self.nemi.fragen(f"Male ein Bild: {text}\nSchreib dafür einen genauen englischen Bild-Prompt und setz das "
                         "Bild mit dem Werkzeug bild an der Schreibmarke ein (wo: cursor). Sonst nichts ändern.",
                         f"🎨 {text}")

    def _titel(self, seite, text: str) -> None:
        i = self.stapel.indexOf(seite)
        if i > 0:
            self.reiter.setTabText(i, SYMBOL.get(seite.app, "") + text)
            self.reiter.setTabToolTip(i, seite.pfad or text)

    def neu(self, app: str) -> None:
        seite = SEITEN.get(app, SchreibSeite)(self.ordner)
        self._dazu(seite, SYMBOL.get(seite.app, "") + seite.name())
        {"schreiben": lambda: seite.feld, "tabelle": lambda: seite.raster,
         "malen": lambda: seite.leinwand}[seite.app]().setFocus()

    def oeffnen_dialog(self) -> None:
        alle = {**ablage.TEXT_OEFFNEN, **ablage.TABELLE_OEFFNEN, **ablage.BILD_OEFFNEN}
        pfade, _ = QFileDialog.getOpenFileNames(self, "Datei öffnen", self.ordner, ablage.filter_text(alle))
        for p in pfade:
            self.oeffnen(p)

    def oeffnen(self, pfad: str, app: str | None = None) -> None:
        p = Path(pfad)
        roh = str(p)
        if roh.startswith(("\\\\", "//")):
            self.nemi.hinweis("Dateien auf Netzwerkfreigaben öffne ich nicht – bitte erst lokal speichern.")
            return
        for s in self._seiten():                          # schon offen: hinwechseln
            if s.pfad and Path(s.pfad).resolve() == p.resolve():
                self.reiter.setCurrentIndex(self.stapel.indexOf(s))
                return
        app = ablage._app_eintrag(p, app)
        if app is None:
            self.nemi.hinweis(f"{p.name}: dieses Format kenne ich noch nicht.")
            return
        seite = SEITEN.get(app, SchreibSeite)(self.ordner)
        try:
            seite.laden(roh)
        except Exception as e:
            seite.deleteLater()
            self.nemi.hinweis(f"{p.name} ließ sich nicht öffnen: {e}")
            return
        self._dazu(seite, seite.name())
        seite._titel()
        self._gemerkt(seite, roh, geoeffnet=True)

    def _gemerkt(self, seite, pfad: str, geoeffnet: bool = False) -> None:
        end = Path(pfad).suffix.lower()
        if not geoeffnet and (end == ".pdf" or (end in (".html", ".htm") and seite.app == "tabelle")):
            return                                         # reine Ausgabe, kein Dokument zum Weiterbearbeiten
        try:
            ablage.merken(self.ordner, pfad, self._vorschau(seite), seite.app)
        except Exception:
            pass
        self.start.auffrischen()

    @staticmethod
    def _vorschau(seite):
        if seite.app == "schreiben":
            return seite.feld.toPlainText()[:600]
        if seite.app == "malen":
            return ""                                  # die Karte zeigt das Bild selbst
        m, b = seite.modell.mappe, seite.modell.blatt
        z, s = b.groesse()
        return [[m.anzeige(b, i, j) for j in range(min(s, 5))] for i in range(min(z, 8))]

    def reiter_schliessen(self, nr: int) -> None:
        if nr <= 0:
            return
        seite = self.stapel.widget(nr)
        if not seite.schliessen_ok():
            return
        self.reiter.removeTab(nr)
        self.stapel.removeWidget(seite)
        seite.deleteLater()

    def _reiter_gewechselt(self, nr: int) -> None:
        if 0 <= nr < self.stapel.count():
            self.stapel.setCurrentIndex(nr)
            seite = self.aktuelle_seite()
            self.nemi.app_setzen(seite.app if seite else "start")
            if seite is None:
                self.start.auffrischen()

    def _naechster_reiter(self) -> None:
        if self.reiter.count() > 1:
            self.reiter.setCurrentIndex((self.reiter.currentIndex() + 1) % self.reiter.count())

    # -- OfficeAI --------------------------------------------------------------------------
    def _kontext(self) -> dict | None:
        seite = self.aktuelle_seite()
        return seite.kontext() if seite is not None else None

    def _kugel_zeigen(self, an: bool) -> None:
        for w in (self.nemi.kugel, self.nemi.feld, self.nemi.blase):
            w.setVisible(an and (w is self.nemi.kugel or self.nemi.offen))
        if not an:
            self.nemi.einklappen()

    def _frage(self, auftrag: dict) -> None:
        """Frage der Kugel: das Dokument dieses Augenblicks ist das, in dem die OfficeAI arbeiten darf."""
        self._auftrag_seite[auftrag["id"]] = self.aktuelle_seite()
        self.ausfuehrer.angehalten = False
        self._esc.setEnabled(True)
        self._senden(auftrag)

    def _anhalten(self, nr: int) -> None:
        self.ausfuehrer.angehalten = True
        self._senden({"op": "stopp", "id": nr})
        self._fertig(nr)
        self.nemi.fehler(nr, "⏹ Angehalten – was schon gemacht ist, bleibt (Rückgängig holt es zurück).")

    def _fertig(self, nr: int) -> None:
        self._auftrag_seite.pop(nr, None)
        self._esc.setEnabled(False)
        self.zeiger.ausblenden()

    def _senden(self, auftrag: dict) -> None:
        if self.leitung is None:
            if auftrag.get("op") == "frage":
                self._fertig(auftrag.get("id"))
                self.nemi.fehler(auftrag.get("id"), "Nemi ist nicht verbunden – im NemiCLI-Terminal /office eingeben.")
            return
        self.leitung.senden(auftrag)

    def _aktion(self, ev: dict) -> None:
        """Eine Aktion der OfficeAI ausführen – nur im Dokument des Auftrags, nur solange er läuft."""
        nr, schritt = ev.get("id"), ev.get("schritt")
        aktion = ev.get("aktion") if isinstance(ev.get("aktion"), dict) else {}
        seite = self._auftrag_seite.get(nr)

        def melden(ok: bool, text: str, kontext: dict | None) -> None:
            self._senden({"op": "ergebnis", "id": nr, "schritt": schritt, "ok": ok, "text": text,
                          "kontext": kontext})

        if seite is None or self.stapel.indexOf(seite) < 0 or self.nemi.laufend != nr:
            melden(False, "Das Dokument ist nicht mehr offen oder der Auftrag wurde angehalten.", None)
            return
        if self.stapel.currentWidget() is not seite:          # zum Dokument des Auftrags wechseln
            self.reiter.setCurrentIndex(self.stapel.indexOf(seite))
        self.ausfuehrer.ausfuehren(seite, aktion, melden)

    def _ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        nr = ev.get("id") if isinstance(ev.get("id"), int) else None
        if t == "stand":
            name = str(ev.get("name") or "Nemi")
            self.nemi.stand(name, bool(ev.get("modell")), ev.get("gesichter") if isinstance(ev.get("gesichter"), dict)
                            else None)
            self.start.name_setzen(name)
            ordner = str(ev.get("ordner") or "")
            if ordner and Path(ordner).is_absolute():
                self.ordner = ordner
                for s in self._seiten():
                    s.ordner = ordner
                self.start.auffrischen()
        elif t == "aktion":
            self._aktion(ev)
        elif t == "status":
            self.nemi.status(nr or 0, str(ev.get("text") or ""))
        elif t == "antwort":
            self._fertig(nr or 0)
            self.nemi.antwort(nr or 0, str(ev.get("text") or ""), bool(ev.get("geaendert")))
        elif t == "fehler":
            if nr is not None:
                self._fertig(nr)
            self.nemi.fehler(nr, str(ev.get("text") or ""))
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _getrennt(self) -> None:
        self.leitung = None
        laufend = self.nemi.laufend
        if laufend is not None:
            self._fertig(laufend)
        self.nemi.fehler(laufend, "Verbindung zur Sitzung getrennt – deine Dokumente bleiben offen. "
                                  "Für die OfficeAI im Terminal /office neu öffnen.")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.nemi.anordnen()

    def showEvent(self, e):
        super().showEvent(e)
        self.nemi.anordnen()

    # -- Ziehen und Ablegen ----------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile() and not url.host():
                self.oeffnen(url.toLocalFile())

    def closeEvent(self, e):
        for s in self._seiten():
            self.reiter.setCurrentIndex(self.stapel.indexOf(s))
            if not s.schliessen_ok():
                e.ignore()
                return
        e.accept()


def _leitung():
    """Verbindung zur Sitzung, falls das Fenster aus /office kommt; sonst None (ohne Nemi)."""
    adresse = os.environ.pop("NEMICLI_GUI_PIPE", "")
    schluessel = os.environ.pop("NEMICLI_GUI_KEY", "")
    if not adresse or not schluessel:
        return None
    try:
        from gui_fenster import Leitung
        return Leitung(adresse, bytes.fromhex(schluessel))
    except Exception:
        return None


def starten(dateien: list[str] | None = None) -> int:
    app = app_vorbereiten("NemiCLI.Office")
    fenster = OfficeFenster(_leitung())
    _dunkler_rahmen(fenster)
    fenster.show()
    for d in dateien or []:
        if Path(d).is_file():
            fenster.oeffnen(d)
    return app.exec()
