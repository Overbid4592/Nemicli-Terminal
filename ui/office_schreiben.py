"""office_schreiben.py – ✍️ Schreiben in Nemi Office: Texteditor mit Seite, Formatleiste und Markdown-Quelltext.

Öffnet .md .txt .html .docx .odt .rtf (.pdf als Text), speichert als .md .odt .docx .txt .html .rtf .pdf.
Bilder im Dokument kommen nur aus dem Dokument selbst oder von lokalen Laufwerken – nie aus dem Netz
und nie von Netzwerkfreigaben (\\\\server\\…).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QKeySequence, QPageLayout, QPageSize, QPainter, QPdfWriter, QShortcut,
                           QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextFrameFormat, QTextImageFormat,
                           QTextLength, QTextListFormat, QTextTable, QTextTableFormat)
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QMenu,
                               QPlainTextEdit,
                               QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from buero import ablage
import office_markdown as MD
import office_seiten as S
from buero.textmodell import Absatz, Bild, Dokument, Lauf, Linie, Tabelle, bild_endung, nach_html
from design import FARBE, schrift
from office_teile import Leiste, Werkzeugknopf, frage_speichern, melden, waehlen

MAX_BILD_PX = 640
BILD_SCHEMA = "nemi-bild"


def lokal_erlaubt(url: QUrl) -> bool:
    """Nur Dateien auf lokalen Laufwerken: kein Host, kein \\\\server, kein \\\\?\\-Präfix."""
    if not url.isLocalFile() or url.host():
        return False
    pfad = url.toLocalFile()
    return not pfad.startswith(("//", "\\\\")) and len(pfad) > 2 and pfad[1] == ":"


class Seite(QTextEdit):
    """Das Blatt: weiß, mit Rand; lädt Bilder nur aus dem Dokument oder von lokalen Laufwerken. Markdown wird
    beim Tippen umgewandelt (office_markdown.Live), Checkboxen sind anklickbar, Zitate haben einen Balken."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.live = MD.Live(self)
        self.seiten = True                       # Blätter wie im Druck; aus = ein durchgehendes Blatt
        self.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
        self.setLineWrapColumnOrWidth(S.SEITE_W)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.papier = QColor("#fdfdfb")
        self.lineal = S.Lineal(self)
        self.horizontalScrollBar().valueChanged.connect(lambda _w: self._lineal_setzen())
        self._raender = (-1, -1)

    def seite_pruefen(self) -> None:
        """Seitenformat am Dokument halten (QTextEdit setzt beim Umbruch die Höhe sonst auf „endlos“)."""
        doc = self.document()
        soll = QSizeF(S.SEITE_W, S.SEITE_H if self.seiten else -1)
        if doc.pageSize() != soll:
            doc.setPageSize(soll)
        if doc.documentMargin() != S.RAND:
            doc.setDocumentMargin(S.RAND)

    def _raender_setzen(self) -> None:
        """Blatt mittig: der Platz links und rechts ist Rand der Bildlaufleiste, oben sitzt das Lineal."""
        rahmen = self.frameWidth()
        frei = self.width() - 2 * rahmen - self.verticalScrollBar().sizeHint().width()
        links = max(0, (frei - S.SEITE_W) // 2)
        rechts = max(0, frei - S.SEITE_W - links)
        if (links, rechts) != self._raender:
            self._raender = (links, rechts)
            self.setViewportMargins(links, S.OBEN, rechts, 0)
        self._lineal_setzen()

    def _lineal_setzen(self) -> None:
        rahmen = self.frameWidth()
        links = max(0, self._raender[0])
        self.lineal.setGeometry(rahmen + links, rahmen, max(0, self.viewport().width()), S.LINEAL_H)
        self.lineal.versatz = self.horizontalScrollBar().value()
        self.lineal.update()

    def resizeEvent(self, e):
        self._raender_setzen()
        super().resizeEvent(e)
        self.seite_pruefen()
        self._lineal_setzen()

    def keyPressEvent(self, e):
        if self.live.vorher(e):
            return
        super().keyPressEvent(e)
        self.live.nachher(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and MD.checkbox_klick(self, e.position().toPoint()):
            return
        super().mousePressEvent(e)

    def paintEvent(self, e):
        self.seite_pruefen()
        grund = QPainter(self.viewport())                     # Papier zuerst, darauf der Text
        grund.fillRect(e.rect(), self.papier)
        grund.end()
        super().paintEvent(e)
        p = QPainter(self.viewport())
        MD.malen(self, p)
        S.blatt_malen(self, p, self.seiten)
        p.end()

    def loadResource(self, typ, name):
        url = name if isinstance(name, QUrl) else QUrl(str(name))
        if url.scheme() == BILD_SCHEMA:
            return super().loadResource(typ, url)
        if url.isRelative() and self.document().baseUrl().isLocalFile():
            url = self.document().baseUrl().resolved(url)
        if typ == QTextDocument.ResourceType.ImageResource and lokal_erlaubt(url):
            bild = QImage(url.toLocalFile())
            return bild if not bild.isNull() else None
        return None

    def insertFromMimeData(self, quelle):
        if quelle.hasImage():                             # Bild aus der Zwischenablage als Ressource
            bild = QImage(quelle.imageData())
            if not bild.isNull():
                bild_einfuegen(self, bild)
                return
        if quelle.hasHtml():                              # fremdes HTML: ohne externe Bilder und Skripte
            self.textCursor().insertHtml(quelle.html())
            return
        super().insertFromMimeData(quelle)


def bild_einfuegen(feld: QTextEdit, bild: QImage) -> None:
    name = f"{BILD_SCHEMA}:{uuid.uuid4().hex}"
    feld.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), bild)
    fmt = QTextImageFormat()
    fmt.setName(name)
    breite = min(bild.width(), MAX_BILD_PX)
    if bild.width() > 0:
        fmt.setWidth(breite)
        fmt.setHeight(bild.height() * breite / bild.width())
    feld.textCursor().insertImage(fmt)


# ---------------------------------------------------------------------------
# QTextDocument <-> Textmodell
# ---------------------------------------------------------------------------

_PUNKT = {QTextListFormat.Style.ListDisc, QTextListFormat.Style.ListCircle, QTextListFormat.Style.ListSquare}


def ausrichtung(al) -> str:
    if al & Qt.AlignmentFlag.AlignHCenter:
        return "mitte"
    if al & Qt.AlignmentFlag.AlignRight:
        return "rechts"
    if al & Qt.AlignmentFlag.AlignJustify:
        return "block"
    return ""


def _bild_bytes(doc: QTextDocument, name: str) -> bytes | None:
    res = doc.resource(QTextDocument.ResourceType.ImageResource, QUrl(name))
    bild = res if isinstance(res, QImage) else QImage()
    if bild.isNull() and isinstance(res, (bytes, QByteArray)):
        bild = QImage.fromData(bytes(res))
    if bild.isNull():
        url = QUrl(name)
        if url.isRelative() and doc.baseUrl().isLocalFile():
            url = doc.baseUrl().resolved(url)
        if lokal_erlaubt(url):
            bild = QImage(url.toLocalFile())
    if bild.isNull():
        return None
    puffer = QBuffer()
    puffer.open(QIODevice.OpenModeFlag.WriteOnly)
    bild.save(puffer, "PNG")
    return bytes(puffer.data())


def _absatz(block, doc: QTextDocument) -> tuple[Absatz, list[Bild]]:
    bf = block.blockFormat()
    a = Absatz(stufe=max(0, min(6, bf.headingLevel())))
    a.ausrichtung = ausrichtung(bf.alignment())
    liste = block.textList()
    if liste is not None:
        lf = liste.format()
        a.liste = "punkt" if lf.style() in _PUNKT else "zahl"
        a.ebene = max(0, min(8, lf.indent() - 1))
        if bf.marker() != QTextBlockFormat.MarkerType.NoMarker:
            a.aufgabe = "erledigt" if bf.marker() == QTextBlockFormat.MarkerType.Checked else "offen"
            a.liste = "punkt"
    a.zitat = MD.ist_zitat(bf)
    bilder = []
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            cf = frag.charFormat()
            if cf.isImageFormat():
                img = cf.toImageFormat()
                daten = _bild_bytes(doc, img.name())
                if daten and (art := bild_endung(daten)):
                    bilder.append(Bild(daten, art, img.width() * 0.75, img.height() * 0.75))
            else:
                text = frag.text().replace("\u2028", "\n").replace("\ufffc", "")
                a.laeufe.append(Lauf(text, fett=cf.fontWeight() >= QFont.Weight.DemiBold, kursiv=cf.fontItalic(),
                                     unter=cf.fontUnderline() and not cf.isAnchor(), durch=cf.fontStrikeOut(),
                                     link=cf.anchorHref() if cf.isAnchor() else "",
                                     farbe="" if cf.isAnchor() else pinsel_farbe(cf, QTextFormat.Property.ForegroundBrush),
                                     marker="" if MD.ist_code(cf) and pinsel_farbe(
                                         cf, QTextFormat.Property.BackgroundBrush) == MD.CODE_HG.name()
                                     else pinsel_farbe(cf, QTextFormat.Property.BackgroundBrush),
                                     code=MD.ist_code(cf)))
        it += 1
    return a.zusammenfassen(), bilder


def _rahmen(rahmen, doc: QTextDocument, ziel: list) -> None:
    it = rahmen.begin()
    while not it.atEnd():
        f = it.currentFrame()
        if f is not None:
            if isinstance(f, QTextTable):
                t = Tabelle()
                for r in range(f.rows()):
                    zeile = []
                    for c in range(f.columns()):
                        zelle = f.cellAt(r, c)
                        absaetze = []
                        b = zelle.firstCursorPosition().block()
                        ende = zelle.lastCursorPosition().block()
                        while b.isValid():
                            absatz, _ = _absatz(b, doc)
                            absaetze.append(absatz)
                            if b == ende:
                                break
                            b = b.next()
                        zeile.append(absaetze)
                    t.zeilen.append(zeile)
                ziel.append(t)
            else:
                _rahmen(f, doc, ziel)
        else:
            block = it.currentBlock()
            if block.isValid() and MD.ist_linie(block.blockFormat()) and not block.text().strip():
                ziel.append(Linie())
            elif block.isValid():
                absatz, bilder = _absatz(block, doc)
                if absatz.laeufe or not bilder:
                    ziel.append(absatz)
                ziel.extend(bilder)
        it += 1


def pinsel_farbe(fmt, eigenschaft) -> str:
    """Farbe eines gesetzten Pinsels (Text- oder Hintergrundfarbe) als "#rrggbb", sonst ""."""
    if not fmt.hasProperty(eigenschaft):
        return ""
    pinsel = fmt.brushProperty(eigenschaft)
    return pinsel.color().name() if pinsel.style() != Qt.BrushStyle.NoBrush else ""


def papier_von(doc: QTextDocument) -> str:
    return pinsel_farbe(doc.rootFrame().frameFormat(), QTextFormat.Property.BackgroundBrush)


def papier_setzen(doc: QTextDocument, farbe: str) -> None:
    rahmen = doc.rootFrame()
    ff = rahmen.frameFormat()
    if farbe:
        ff.setBackground(QColor(farbe))
    else:
        ff.clearBackground()
    rahmen.setFrameFormat(ff)


def hat_farben(doc: QTextDocument) -> bool:
    """Papier-, Text- oder Markerfarbe irgendwo im Dokument?"""
    if papier_von(doc):
        return True
    b = doc.begin()
    while b.isValid():
        it = b.begin()
        while not it.atEnd():
            cf = it.fragment().charFormat()
            if pinsel_farbe(cf, QTextFormat.Property.BackgroundBrush) or (
                    not cf.isAnchor() and pinsel_farbe(cf, QTextFormat.Property.ForegroundBrush)):
                return True
            it += 1
        b = b.next()
    return False


def qt_zu_modell(doc: QTextDocument) -> Dokument:
    dok = Dokument(papier=papier_von(doc))
    _rahmen(doc.rootFrame(), doc, dok.bloecke)
    while dok.bloecke and isinstance(dok.bloecke[-1], Absatz) and not dok.bloecke[-1].text and \
            not dok.bloecke[-1].liste:
        dok.bloecke.pop()                                 # leere Absätze am Ende
    return dok


def modell_laden(doc: QTextDocument, dok: Dokument) -> None:
    text, bilder = nach_html(dok)
    for name, b in bilder.items():
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), QImage.fromData(b.daten))
    doc.setHtml(text)
    papier_setzen(doc, dok.papier)


def pdf_schreiben(doc: QTextDocument, pfad: str) -> None:
    """Dokument als PDF: A4 mit 2 cm Rand, Seitenumbrüche genau wie die Blätter am Bildschirm."""
    import atomar
    puffer = QBuffer()
    puffer.open(QIODevice.OpenModeFlag.WriteOnly)
    w = QPdfWriter(puffer)
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter))
    w.setResolution(300)
    w.setTitle(Path(pfad).stem)
    w.setCreator("Nemi Office")
    kopie = doc.clone()
    kopie.setDefaultStyleSheet("")
    kopie.setDefaultFont(doc.defaultFont())
    kopie.setDocumentMargin(S.RAND)
    kopie.setPageSize(QSizeF(S.SEITE_W, S.SEITE_H))       # in Seiten gesetzt: print_ skaliert sie auf A4
    kopie.print_(w)
    del w
    puffer.close()
    atomar.bytes_schreiben(pfad, bytes(puffer.data()))


# ---------------------------------------------------------------------------
# Die Seite der App
# ---------------------------------------------------------------------------

class SchreibSeite(QWidget):
    titel_geaendert = Signal(str)
    gespeichert = Signal(str)            # Pfad
    bild_wunsch = Signal(str)            # „Bild malen“: Beschreibung an die OfficeAI

    app = "schreiben"

    def __init__(self, ordner: str, parent=None):
        super().__init__(parent)
        self.ordner = ordner
        self.pfad: str | None = None
        self.quelle_an = False
        self._quelle_basis = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.leiste = Leiste()
        v.addWidget(self.leiste)

        self.stapel = QStackedWidget()
        v.addWidget(self.stapel, 1)

        # Seite mittig auf dunklem Grund
        grund = QWidget()
        grund.setObjectName("seitengrund")
        grund.setStyleSheet(f"QWidget#seitengrund {{ background: {FARBE['grund']}; }}")
        h = QHBoxLayout(grund)
        h.setContentsMargins(0, 0, 0, 0)
        self.feld = Seite()
        self.feld.setObjectName("blatt")
        self.feld.setMinimumWidth(320)
        self.feld.setAcceptRichText(True)
        self.feld.setTabChangesFocus(False)
        self._papier_anzeigen()
        self.feld.document().setDocumentMargin(S.RAND)
        self.feld.document().setDefaultFont(schrift(11.5))
        self.feld.document().setDefaultStyleSheet("h1 { font-size: 22pt; } h2 { font-size: 17pt; } h3 { font-size: 14pt; }"
                                                  "a { color: #5b3fd6; } table { border-collapse: collapse; }")
        h.addWidget(self.feld, 1)
        self.stapel.addWidget(grund)

        self.quelle = QPlainTextEdit()
        self.quelle.setObjectName("quelle")
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSizeF(10.5)
        self.quelle.setFont(f)
        self.quelle.setStyleSheet(f"QPlainTextEdit#quelle {{ background: {FARBE['flaeche']}; color: {FARBE['text']};"
                                  f" border: none; padding: 18px; }}")
        self.stapel.addWidget(self.quelle)

        self.status = melden(self)
        v.addWidget(self.status)
        self._knoepfe()
        self.feld.document().contentsChanged.connect(self._geaendert)
        self._huebsch_von: int | None = None
        self._huebsch_bis = 0
        self.feld.document().contentsChange.connect(self._huebsch_merken)
        self.feld.cursorPositionChanged.connect(self._format_zeigen)
        self.feld.cursorPositionChanged.connect(self._seite_zeigen)
        self.feld.document().documentLayout().documentSizeChanged.connect(lambda _g: self._seite_zeigen())
        self.quelle.textChanged.connect(self._geaendert)
        self.feld.document().setModified(False)
        self._zaehlen()

    # -- Leiste ----------------------------------------------------------------------------
    def _knoepfe(self) -> None:
        L = self.leiste
        L.knopf("💾", "Speichern (Strg+S)", self.speichern)
        L.knopf("📥", "Speichern unter …", lambda: self.speichern(unter=True))
        L.knopf("📄", "Als PDF speichern", self.als_pdf)
        L.trenner()
        L.knopf("↶", "Rückgängig (Strg+Z)", self.feld.undo)
        L.knopf("↷", "Wiederholen (Strg+Y)", self.feld.redo)
        L.trenner()
        self.k_stil = Werkzeugknopf("Text ▾", "Absatzart")
        menue = QMenu(self.k_stil)
        for text, stufe in (("Normaler Text", 0), ("Überschrift 1", 1), ("Überschrift 2", 2), ("Überschrift 3", 3)):
            menue.addAction(text, lambda s=stufe: self.ueberschrift(s))
        self.k_stil.setMenu(menue)
        self.k_stil.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        L.dazu(self.k_stil)
        self.k_fett = L.knopf("B", "Fett (Strg+B)", self.fett, umschalt=True, fett=True)
        self.k_kursiv = L.knopf("I", "Kursiv (Strg+I)", self.kursiv, umschalt=True, kursiv=True)
        self.k_unter = L.knopf("U", "Unterstrichen (Strg+U)", self.unter, umschalt=True, unter=True)
        self.k_durch = L.knopf("S", "Durchgestrichen", self.durch, umschalt=True, durch=True)
        L.farbknopf("A ▾", "Textfarbe", self.textfarbe, "Textfarbe", "Standardfarbe")
        L.farbknopf("🖍 ▾", "Textmarker (Hintergrund des Textes)", self.marker, "Textmarker", "Kein Marker")
        L.trenner()
        L.knopf("•", "Aufzählung", lambda: self.liste(QTextListFormat.Style.ListDisc))
        L.knopf("1.", "Nummerierung", lambda: self.liste(QTextListFormat.Style.ListDecimal))
        L.knopf("⇤", "Einrückung verringern", lambda: self.einruecken(-1))
        L.knopf("⇥", "Einrücken", lambda: self.einruecken(1))
        L.knopf("☐", "Checkbox (auch: in einer Liste [ ] tippen) – zum Abhaken anklicken", self.aufgabe)
        L.knopf("❝", "Zitat an/aus (auch: > am Zeilenanfang tippen)", self.zitat)
        L.knopf("―", "Trennlinie einfügen (auch: --- und Enter)", self.linie)
        L.knopf("`", "Code: Schreibmaschinenschrift an/aus (auch: `so` tippen)", self.code)
        L.trenner()
        for zeichen, tipp, ausr in (("⯇", "Links", Qt.AlignmentFlag.AlignLeft),
                                    ("≡", "Zentriert", Qt.AlignmentFlag.AlignHCenter),
                                    ("⯈", "Rechts", Qt.AlignmentFlag.AlignRight),
                                    ("☰", "Blocksatz", Qt.AlignmentFlag.AlignJustify)):
            L.knopf(zeichen, tipp, lambda a=ausr: self.feld.setAlignment(a))
        L.trenner()
        k_tab = Werkzeugknopf("▦", "Tabelle einfügen")
        menue_t = QMenu(k_tab)
        for z, s in ((2, 2), (3, 3), (4, 3), (5, 4), (8, 4)):
            menue_t.addAction(f"{z} Zeilen × {s} Spalten", lambda z=z, s=s: self.tabelle(z, s))
        k_tab.setMenu(menue_t)
        k_tab.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        L.dazu(k_tab)
        L.knopf("🖼", "Bild einfügen", self.bild)
        L.farbknopf("📄 ▾", "Papierfarbe der ganzen Seite (auch in .docx, .odt, .html und PDF)", self.papier,
                    "Papierfarbe", "Weißes Papier")
        L.knopf("🎨 Bild malen", "Die OfficeAI malt ein Bild (Krea 2) und setzt es an der Schreibmarke ein",
                self.bild_malen, breit=True)
        L.dehnen()
        self.k_seiten = L.knopf("📄 Seiten", "A4-Blätter wie im Druck (aus: ein durchgehendes Blatt)",
                                self.seiten_umschalten, umschalt=True, breit=True)
        self.k_seiten.setChecked(True)
        self.k_quelle = L.knopf("</> Markdown", "Zwischen Ansicht und Markdown-Quelltext umschalten",
                                self.quelle_umschalten, umschalt=True, breit=True)
        for kurz, fn in (("Ctrl+S", self.speichern), ("Ctrl+B", self.fett_kurz), ("Ctrl+I", self.kursiv_kurz),
                         ("Ctrl+U", self.unter_kurz), ("Ctrl+Shift+S", lambda: self.speichern(unter=True))):
            QShortcut(QKeySequence(kurz), self, fn, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

    # -- Markdown-Bausteine ----------------------------------------------------------------
    def aufgabe(self):
        c = self.feld.textCursor()
        c.beginEditBlock()
        hat = c.blockFormat().marker() != QTextBlockFormat.MarkerType.NoMarker
        MD.aufgabe_setzen(c, QTextBlockFormat.MarkerType.NoMarker if hat else QTextBlockFormat.MarkerType.Unchecked)
        c.endEditBlock()
        self.feld.setFocus()

    def zitat(self):
        c = self.feld.textCursor()
        c.beginEditBlock()
        MD.zitat_setzen(c, not MD.ist_zitat(c.blockFormat()))
        c.endEditBlock()
        self.feld.viewport().update()
        self.feld.setFocus()

    def linie(self):
        c = self.feld.textCursor()
        MD.linie_einfuegen(c)
        self.feld.setTextCursor(c)
        self.feld.setFocus()

    def code(self):
        c = self.feld.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.SelectionType.WordUnderCursor)
        an = not MD.ist_code(c.charFormat())
        c.beginEditBlock()
        if an:
            c.mergeCharFormat(MD.code_format(True))
        else:
            fmt = c.charFormat()
            fmt.setFontFixedPitch(False)
            fmt.clearProperty(QTextFormat.Property.FontFamilies)
            fmt.clearBackground()
            c.setCharFormat(fmt)
        c.endEditBlock()
        self.feld.setFocus()

    def seiten_umschalten(self, an: bool):
        self.feld.seiten = bool(an)
        self.feld.seite_pruefen()
        self.feld.viewport().update()
        self._seite_zeigen()

    def bild_malen(self):
        text, ok = QInputDialog.getText(self, "Bild malen", "Was soll die OfficeAI malen? (z. B. „ein gemütliches "
                                                            "Café im Herbst“)")
        if ok and text.strip():
            self.bild_wunsch.emit(text.strip()[:500])

    def _huebsch_merken(self, pos: int, _weg: int, dazu: int) -> None:
        """Geänderten Bereich merken; aufgehübscht wird gleich danach (nicht mitten in der Änderung)."""
        if self._huebsch_von is None:
            self._huebsch_von, self._huebsch_bis = pos, pos + dazu
            QTimer.singleShot(0, self, self._huebsch_jetzt)
        else:
            self._huebsch_von = min(self._huebsch_von, pos)
            self._huebsch_bis = max(self._huebsch_bis, pos + dazu)

    def _huebsch_jetzt(self) -> None:
        von, bis = self._huebsch_von, self._huebsch_bis
        self._huebsch_von = None
        if von is not None:
            geaendert = self.feld.document().isModified()
            MD.aufhuebschen(self.feld.document(), von, bis + 1)
            self.feld.document().setModified(geaendert)

    # -- Farben ----------------------------------------------------------------------------
    def textfarbe(self, farbe: str) -> None:
        self._pinsel(QTextFormat.Property.ForegroundBrush, farbe)

    def marker(self, farbe: str) -> None:
        self._pinsel(QTextFormat.Property.BackgroundBrush, farbe)

    def _pinsel(self, eigenschaft, farbe: str) -> None:
        """Text- oder Markerfarbe für die Markierung (sonst das Wort an der Schreibmarke); "" entfernt sie."""
        c = self.feld.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.SelectionType.WordUnderCursor)
        aktuell = self.feld.currentCharFormat()
        if farbe:
            fmt = QTextCharFormat()
            if eigenschaft == QTextFormat.Property.ForegroundBrush:
                fmt.setForeground(QColor(farbe))
            else:
                fmt.setBackground(QColor(farbe))
            c.mergeCharFormat(fmt)
            aktuell.merge(fmt)
        else:
            self._eigenschaft_weg(c, eigenschaft)
            aktuell.clearProperty(eigenschaft)
        self.feld.setCurrentCharFormat(aktuell)
        self.feld.setFocus()

    def _eigenschaft_weg(self, c: QTextCursor, eigenschaft) -> None:
        """Eine Zeichen-Eigenschaft im markierten Bereich entfernen (Stück für Stück, andere Formate bleiben)."""
        doc = self.feld.document()
        start, ende = c.selectionStart(), c.selectionEnd()
        k = QTextCursor(doc)
        k.beginEditBlock()
        b = doc.findBlock(start)
        while b.isValid() and b.position() <= ende:
            it = b.begin()
            while not it.atEnd():
                frag = it.fragment()
                a, e = max(frag.position(), start), min(frag.position() + frag.length(), ende)
                if frag.isValid() and a < e:
                    fmt = frag.charFormat()
                    fmt.clearProperty(eigenschaft)
                    k.setPosition(a)
                    k.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
                    k.setCharFormat(fmt)
                it += 1
            b = b.next()
        k.endEditBlock()

    def papier(self, farbe: str) -> None:
        papier_setzen(self.feld.document(), farbe)
        self.feld.document().setModified(True)
        self._papier_anzeigen()
        self._titel()

    def _papier_anzeigen(self) -> None:
        """Hintergrund des Blatts nach der Papierfarbe des Dokuments."""
        farbe = papier_von(self.feld.document()) or "#fdfdfb"
        self.feld.papier = QColor(farbe)
        self.feld.setStyleSheet(
            "QTextEdit#blatt { background: transparent; color: #1f1f24; border: none;"
            " selection-background-color: #c7b8f5; selection-color: #1f1f24; }")
        self.feld.viewport().update()

    # -- Format ----------------------------------------------------------------------------
    def _zeichen(self, fmt: QTextCharFormat) -> None:
        c = self.feld.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.SelectionType.WordUnderCursor)
        c.mergeCharFormat(fmt)
        self.feld.mergeCurrentCharFormat(fmt)

    def fett(self, an: bool | None = None):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if self.k_fett.isChecked() else QFont.Weight.Normal)
        self._zeichen(fmt)

    def kursiv(self, *_):
        fmt = QTextCharFormat()
        fmt.setFontItalic(self.k_kursiv.isChecked())
        self._zeichen(fmt)

    def unter(self, *_):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(self.k_unter.isChecked())
        self._zeichen(fmt)

    def durch(self, *_):
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(self.k_durch.isChecked())
        self._zeichen(fmt)

    def fett_kurz(self):
        self.k_fett.setChecked(not self.k_fett.isChecked())
        self.fett()

    def kursiv_kurz(self):
        self.k_kursiv.setChecked(not self.k_kursiv.isChecked())
        self.kursiv()

    def unter_kurz(self):
        self.k_unter.setChecked(not self.k_unter.isChecked())
        self.unter()

    def ueberschrift(self, stufe: int):
        c = self.feld.textCursor()
        c.beginEditBlock()
        bf = c.blockFormat()
        bf.setHeadingLevel(stufe)
        c.setBlockFormat(bf)
        groesse = {0: 11.5, 1: 22, 2: 17, 3: 14}[stufe]
        cf = QTextCharFormat()
        cf.setFontWeight(QFont.Weight.Bold if stufe else QFont.Weight.Normal)
        cf.setFontPointSize(groesse)
        c.select(QTextCursor.SelectionType.BlockUnderCursor)
        c.mergeCharFormat(cf)
        c.endEditBlock()
        self.feld.mergeCurrentCharFormat(cf)

    def liste(self, stil):
        c = self.feld.textCursor()
        aktuell = c.currentList()
        c.beginEditBlock()
        if aktuell is not None and aktuell.format().style() == stil:
            for i in range(aktuell.count() - 1, -1, -1):        # gleiche Liste noch mal: aufheben
                b = aktuell.item(i)
                if b.position() <= c.selectionEnd() and b.position() + b.length() > c.selectionStart():
                    aktuell.remove(b)
                    bc = QTextCursor(b)
                    bf = bc.blockFormat()
                    bf.setIndent(0)
                    bc.setBlockFormat(bf)
        else:
            lf = QTextListFormat()
            lf.setStyle(stil)
            lf.setIndent(aktuell.format().indent() if aktuell is not None else 1)
            c.createList(lf)
        c.endEditBlock()

    def einruecken(self, d: int):
        c = self.feld.textCursor()
        aktuell = c.currentList()
        if aktuell is None:
            bf = c.blockFormat()
            bf.setIndent(max(0, bf.indent() + d))
            c.setBlockFormat(bf)
            return
        lf = QTextListFormat(aktuell.format())
        lf.setIndent(max(1, min(9, lf.indent() + d)))
        stile = [QTextListFormat.Style.ListDisc, QTextListFormat.Style.ListCircle, QTextListFormat.Style.ListSquare]
        if lf.style() in stile:
            lf.setStyle(stile[(lf.indent() - 1) % 3])
        c.createList(lf)

    def tabelle(self, zeilen: int, spalten: int):
        fmt = QTextTableFormat()
        fmt.setBorder(0.8)
        fmt.setBorderBrush(QColor("#9a9aa6"))
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
        fmt.setCellPadding(5)
        fmt.setCellSpacing(0)
        fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
        self.feld.textCursor().insertTable(zeilen, spalten, fmt)

    def bild(self):
        pfad, _ = QFileDialog.getOpenFileName(self, "Bild einfügen", self.ordner,
                                              "Bilder (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if not pfad:
            return
        url = QUrl.fromLocalFile(pfad)
        if not lokal_erlaubt(url):
            self.status.zeigen("Bilder nur von lokalen Laufwerken.", "warn")
            return
        bild = QImage(pfad)
        if bild.isNull():
            self.status.zeigen("Das Bild ließ sich nicht lesen.", "warn")
            return
        bild_einfuegen(self.feld, bild)

    def _format_zeigen(self):
        cf = self.feld.currentCharFormat()
        for k, an in ((self.k_fett, cf.fontWeight() >= QFont.Weight.DemiBold), (self.k_kursiv, cf.fontItalic()),
                      (self.k_unter, cf.fontUnderline()), (self.k_durch, cf.fontStrikeOut())):
            k.blockSignals(True)
            k.setChecked(an)
            k.blockSignals(False)
        stufe = self.feld.textCursor().blockFormat().headingLevel()
        self.k_stil.setText(f"Überschrift {stufe} ▾" if stufe else "Text ▾")

    # -- Markdown-Quelltext ----------------------------------------------------------------
    def quelle_umschalten(self, *_):
        if self.k_quelle.isChecked():
            self._quelle_basis = MD.markdown(self.feld.document())
            self.quelle.blockSignals(True)
            self.quelle.setPlainText(self._quelle_basis)
            self.quelle.blockSignals(False)
            self.stapel.setCurrentIndex(1)
            self.quelle.setFocus()
        else:
            self._quelle_uebernehmen()
            self.stapel.setCurrentIndex(0)
            self.feld.setFocus()
        self.quelle_an = self.k_quelle.isChecked()

    def _quelle_uebernehmen(self):
        text = self.quelle.toPlainText()
        if text != self._quelle_basis:                 # nur bei echter Änderung neu aufbauen (Formate bleiben sonst)
            papier = papier_von(self.feld.document())
            self.feld.document().setMarkdown(text)
            papier_setzen(self.feld.document(), papier)
            self.feld.document().setModified(True)
            self._quelle_basis = text

    # -- Laden / Speichern -----------------------------------------------------------------
    def laden(self, pfad: str) -> None:
        art, inhalt = ablage.text_laden(pfad)
        doc = self.feld.document()
        doc.setBaseUrl(QUrl.fromLocalFile(str(Path(pfad).parent) + "/"))
        if art == "markdown":
            doc.setMarkdown(inhalt)
        elif art == "text":
            doc.setPlainText(inhalt)
        elif art == "html":
            doc.setHtml(inhalt)
        else:
            modell_laden(doc, inhalt)
        MD.aufhuebschen(doc)
        self._papier_anzeigen()
        self.pfad = pfad if Path(pfad).suffix.lower() in ablage.TEXT_SPEICHERN and \
            Path(pfad).suffix.lower() != ".pdf" else None
        doc.setModified(False)
        self.feld.moveCursor(QTextCursor.MoveOperation.Start)
        self._titel()

    def ungespeichert(self) -> bool:
        return self.feld.document().isModified() or self.quelle.document().isModified()

    def speichern(self, unter: bool = False) -> bool:
        if self.k_quelle.isChecked():
            self._quelle_uebernehmen()
        pfad = self.pfad
        if unter or not pfad:
            vorschlag = pfad or str(ablage.neuer_pfad(self.ordner, self._name_vorschlag(), ".md"))
            pfad, _ = QFileDialog.getSaveFileName(self, "Speichern unter", vorschlag,
                                                  ablage.filter_text(ablage.TEXT_SPEICHERN, alle=False))
            if not pfad:
                return False
            if not Path(pfad).suffix:
                pfad += ".md"
        end = Path(pfad).suffix.lower()
        if end == ".pdf":
            return self.als_pdf(pfad)
        doc = self.feld.document()
        try:
            if end in (".md", ".markdown"):
                mit = self._md_bilder_wahl(pfad, unter)
                if mit is None:
                    return False
                text, dateien = MD.markdown_speichern(doc, pfad, mit)
                for rel, daten in dateien:                      # Bilder in „<Name>_Bilder“ neben die .md
                    import atomar
                    ziel = Path(pfad).parent / rel
                    ziel.parent.mkdir(parents=True, exist_ok=True)
                    atomar.bytes_schreiben(str(ziel), daten)
                ablage.text_speichern(pfad, "markdown", text)
            elif end in (".txt", ".log"):
                ablage.text_speichern(pfad, "text", doc.toPlainText())
            elif end in (".html", ".htm"):
                ablage.text_speichern(pfad, "html", MD.html_mit_bildern(doc))
            elif end in (".docx", ".odt", ".rtf"):
                ablage.text_speichern(pfad, end[1:], qt_zu_modell(doc))
            else:
                self.status.zeigen(f"Als {end} kann Schreiben nicht speichern.", "warn")
                return False
        except Exception as e:
            self.status.zeigen(f"Nicht gespeichert: {e}", "fehler")
            return False
        self.pfad = pfad
        doc.setModified(False)
        self.quelle.document().setModified(False)
        self._titel()
        if end in (".md", ".markdown", ".txt", ".log") and hat_farben(doc):
            self.status.zeigen(f"✓ Gespeichert: {Path(pfad).name} – Farben kann {end} nicht speichern; dafür .docx, "
                               ".odt oder .html wählen.", "warn", 12)
        else:
            self.status.zeigen(f"✓ Gespeichert: {Path(pfad).name}", "ok")
        self.gespeichert.emit(pfad)
        return True

    def _md_bilder_wahl(self, pfad: str, unter: bool) -> bool | None:
        """Hat das Dokument Bilder: mit (Ordner daneben) oder ohne speichern? Gilt für weitere Strg+S in diese
        Datei; „Speichern unter“ fragt neu. None = abgebrochen."""
        n = len(MD.bilder_namen(self.feld.document()))
        if not n:
            return True
        if not unter and getattr(self, "_md_bilder", None) and self._md_bilder[0] == pfad:
            return self._md_bilder[1]
        wahl = waehlen(self, f"Das Dokument hat {n} {'Bild' if n == 1 else 'Bilder'}. Wie soll die Markdown-Datei "
                             f"gespeichert werden?\n\nMit Bildern: als PNG im Ordner „{MD.bilder_ordner(pfad)}“ neben "
                             "der Datei, im Text verlinkt.\nOhne Bilder: nur der Text.",
                       [("🖼 Mit Bildern", "mit", True), ("Nur Text", "ohne", False), ("Abbrechen", "ab", False)], "ab")
        if wahl == "ab":
            return None
        self._md_bilder = (pfad, wahl == "mit")
        return wahl == "mit"

    def als_pdf(self, pfad: str | None = None) -> bool:
        if self.k_quelle.isChecked():
            self._quelle_uebernehmen()
        if not pfad:
            basis = Path(self.pfad).with_suffix(".pdf") if self.pfad else \
                ablage.neuer_pfad(self.ordner, self._name_vorschlag(), ".pdf")
            pfad, _ = QFileDialog.getSaveFileName(self, "Als PDF speichern", str(basis), "PDF (*.pdf)")
            if not pfad:
                return False
        try:
            pdf_schreiben(self.feld.document(), pfad)
        except Exception as e:
            self.status.zeigen(f"PDF nicht geschrieben: {e}", "fehler")
            return False
        self.status.zeigen(f"✓ PDF gespeichert: {Path(pfad).name}", "ok")
        self.gespeichert.emit(pfad)
        return True

    def _name_vorschlag(self) -> str:
        erste = self.feld.document().firstBlock().text().strip()
        name = "".join(ch for ch in erste if ch.isalnum() or ch in " -_")[:40].strip()
        return name or "Dokument"

    def name(self) -> str:
        return Path(self.pfad).name if self.pfad else "Neues Dokument"

    def _titel(self):
        self.titel_geaendert.emit(("● " if self.ungespeichert() else "") + self.name())

    def _geaendert(self):
        self._titel()
        self._zaehlen()

    def _zaehlen(self):
        text = self.quelle.toPlainText() if self.quelle_an else self.feld.toPlainText()
        self._zahlen = f"{len(text.split())} Wörter · {len(text)} Zeichen"
        self._seite_zeigen()

    def _seite_zeigen(self):
        zahlen = getattr(self, "_zahlen", "")
        if self.quelle_an or not self.feld.seiten:
            self.status.rechts(zahlen)
            return
        jetzt, gesamt = S.seite_von(self.feld)
        self.status.rechts(f"Seite {jetzt} von {gesamt} · {zahlen}")

    # -- für Nemi --------------------------------------------------------------------------
    def kontext(self, grenze: int = 24000) -> dict:
        if self.k_quelle.isChecked():
            text = self.quelle.toPlainText()
            auswahl = self.quelle.textCursor().selectedText().replace("\u2029", "\n")
        else:
            text = MD.markdown(self.feld.document())
            c = self.feld.textCursor()
            auswahl = c.selection().toMarkdown() if c.hasSelection() else ""
        if len(text) > grenze:                       # langer Text: Anfang und Ende
            text = text[: grenze * 2 // 3] + "\n\n[… gekürzt …]\n\n" + text[-grenze // 3:]
        return {"app": self.app, "name": self.name(), "kontext": text, "auswahl": auswahl[:8000]}

    def uebernehmen(self, text_md: str) -> str:
        """Text von Nemi einsetzen: ersetzt die Markierung, sonst an der Schreibmarke."""
        if self.k_quelle.isChecked():
            self.quelle.textCursor().insertText(text_md)
            return "eingefügt"
        c = self.feld.textCursor()
        ersetzt = c.hasSelection()
        c.beginEditBlock()
        if ersetzt:
            c.removeSelectedText()
        c.insertMarkdown(text_md)
        c.endEditBlock()
        self.feld.setTextCursor(c)
        self.feld.setFocus()
        return "ersetzt" if ersetzt else "eingefügt"

    def schliessen_ok(self) -> bool:
        if not self.ungespeichert():
            return True
        wahl = frage_speichern(self, self.name())
        if wahl == "speichern":
            return self.speichern()
        return wahl == "verwerfen"

