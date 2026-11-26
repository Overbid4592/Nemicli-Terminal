"""office_markdown.py – Markdown live im ✍️ Schreiben von Nemi Office.

Beim Tippen wird Markdown sofort zu Format: „# “ Überschrift, „- “/„1. “ Liste, „> “ Zitat, „[ ] “ in einer
Liste Checkbox, „---“ + Enter Trennlinie, **fett**, *kursiv*, ~~durch~~, `code`. Jede Umwandlung ist ein eigener
Rückgängig-Schritt: Strg+Z holt die Zeichen zurück. Dazu: Checkboxen anklicken, Zitat-Balken, Code in
Schreibmaschinenschrift, Tabellen mit Polster und Markdown-Export mit Spaltenausrichtung und Bildern.
"""
from __future__ import annotations

import base64
import html as _html
import re
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt, QUrl
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor,
                           QTextDocument, QTextFormat, QTextLength, QTextListFormat, QTextTable)

CODE_SCHRIFTEN = ["Cascadia Mono", "Consolas", "Courier New", "monospace"]
CODE_HG = QColor("#f2f2f4")
CODE_FAMILIEN = {"monospace", "consolas", "cascadia mono", "cascadia code", "courier new", "lucida console",
                 "liberation mono", "dejavu sans mono"}
ZITAT_FARBE = QColor("#8b5cf6")
ZITAT_EINZUG = 28

_BLOCK_REGELN = (
    (re.compile(r"^(#{1,6}) $"), "ueberschrift"),
    (re.compile(r"^[-*+] \[( |x|X)\] $"), "aufgabe"),
    (re.compile(r"^[-*+] $"), "punkt"),
    (re.compile(r"^\d{1,3}[.)] $"), "zahl"),
    (re.compile(r"^> $"), "zitat"),
)
_IN_LISTE_AUFGABE = re.compile(r"^\[( |x|X)\] $")
_INLINE = (
    (re.compile(r"\*\*([^*\n]+?)\*\*$"), "fett"),
    (re.compile(r"__([^_\n]+?)__$"), "fett"),
    (re.compile(r"(?<![*\w])\*([^*\s](?:[^*\n]*[^*\s])?)\*$"), "kursiv"),
    (re.compile(r"~~([^~\n]+?)~~$"), "durch"),
    (re.compile(r"`([^`\n]+)`$"), "code"),
)
_LINIE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")


def ist_code(fmt: QTextCharFormat) -> bool:
    if fmt.fontFixedPitch():
        return True
    familien = fmt.fontFamilies() or []
    return bool(familien) and str(familien[0]).lower() in CODE_FAMILIEN


def code_format(an: bool = True) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setFontFixedPitch(an)
    if an:
        f.setFontFamilies(CODE_SCHRIFTEN)
        f.setBackground(CODE_HG)
    return f


def ist_zitat(bf: QTextBlockFormat) -> bool:
    return bf.hasProperty(QTextFormat.Property.BlockQuoteLevel) and \
        bf.intProperty(QTextFormat.Property.BlockQuoteLevel) > 0


def ist_linie(bf: QTextBlockFormat) -> bool:
    return bf.hasProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth)


def zitat_setzen(c: QTextCursor, an: bool) -> None:
    bf = c.blockFormat()
    if an:
        bf.setProperty(QTextFormat.Property.BlockQuoteLevel, 1)
        bf.setLeftMargin(ZITAT_EINZUG)
    else:
        bf.clearProperty(QTextFormat.Property.BlockQuoteLevel)
        bf.setLeftMargin(0)
    c.setBlockFormat(bf)


def linie_einfuegen(c: QTextCursor) -> None:
    """Trennlinie als eigener Block, danach ein normaler Absatz."""
    c.beginEditBlock()
    if c.block().text():
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        c.insertBlock()
    bf = QTextBlockFormat()
    bf.setProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth,
                   QTextLength(QTextLength.Type.PercentageLength, 100))
    c.setBlockFormat(bf)
    c.insertBlock(QTextBlockFormat())
    c.endEditBlock()


def aufgabe_setzen(c: QTextCursor, marker) -> None:
    """Block wird Checkbox-Punkt (legt bei Bedarf eine Liste an); marker NoMarker nimmt die Checkbox weg."""
    if c.currentList() is None and marker != QTextBlockFormat.MarkerType.NoMarker:
        lf = QTextListFormat()
        lf.setStyle(QTextListFormat.Style.ListDisc)
        c.createList(lf)
    bf = c.blockFormat()
    bf.setMarker(marker)
    c.setBlockFormat(bf)


class Live:
    """Wandelt Markdown beim Tippen um. `an` schaltet es ab (Quelltext-Ansicht braucht es nicht)."""

    def __init__(self, feld):
        self.feld = feld
        self.an = True

    # -- vor der Taste: Enter in leerer Liste/Zitat beendet sie ---------------------------
    def vorher(self, ev) -> bool:
        """True = Taste erledigt (nicht weiterreichen)."""
        if not self.an or ev.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or \
                ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return False
        c = self.feld.textCursor()
        b = c.block()
        if b.text() or c.hasSelection():
            return False
        bf = b.blockFormat()
        if ist_zitat(bf):
            c.beginEditBlock()
            zitat_setzen(c, False)
            c.endEditBlock()
            return True
        if b.textList() is not None:
            c.beginEditBlock()
            b.textList().remove(b)
            bf = c.blockFormat()
            bf.setIndent(0)
            bf.setMarker(QTextBlockFormat.MarkerType.NoMarker)
            c.setBlockFormat(bf)
            c.endEditBlock()
            return True
        return False

    # -- nach der Taste: Muster am Zeilenanfang oder am Ende des Getippten ------------------
    def nachher(self, ev) -> None:
        if not self.an:
            return
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._nach_enter()
            return
        text = ev.text()
        if text == " ":
            self._block_regel()
        elif text in ("*", "_", "~", "`"):
            self._inline()

    def _block_regel(self) -> None:
        c = self.feld.textCursor()
        if c.hasSelection():
            return
        b = c.block()
        vor = b.text()[:c.positionInBlock()]
        if b.textList() is not None and (m := _IN_LISTE_AUFGABE.match(vor)):
            self._ersetzen_am_anfang(c, len(vor), lambda k: aufgabe_setzen(k, self._marker(m.group(1))))
            return
        if b.blockFormat().headingLevel() or b.textList() is not None or ist_zitat(b.blockFormat()):
            return                                        # schon formatiert: nichts doppelt umwandeln
        for regel, art in _BLOCK_REGELN:
            m = regel.match(vor)
            if not m:
                continue
            if art == "ueberschrift":
                stufe = len(m.group(1))
                self._ersetzen_am_anfang(c, len(vor), lambda k: _ueberschrift(k, stufe))
            elif art == "aufgabe":
                self._ersetzen_am_anfang(c, len(vor), lambda k: aufgabe_setzen(k, self._marker(m.group(1))))
            elif art in ("punkt", "zahl"):
                stil = QTextListFormat.Style.ListDisc if art == "punkt" else QTextListFormat.Style.ListDecimal
                self._ersetzen_am_anfang(c, len(vor), lambda k: _liste(k, stil))
            elif art == "zitat":
                self._ersetzen_am_anfang(c, len(vor), lambda k: zitat_setzen(k, True))
            return

    @staticmethod
    def _marker(zeichen: str):
        return QTextBlockFormat.MarkerType.Checked if zeichen in "xX" else QTextBlockFormat.MarkerType.Unchecked

    def _ersetzen_am_anfang(self, c: QTextCursor, laenge: int, formatieren) -> None:
        k = QTextCursor(c)
        k.beginEditBlock()                                 # eigener Schritt: Strg+Z holt die Zeichen zurück
        k.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        k.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, laenge)
        k.removeSelectedText()
        formatieren(k)
        k.endEditBlock()
        self.feld.setTextCursor(k)

    def _nach_enter(self) -> None:
        c = self.feld.textCursor()
        vorige = c.block().previous()
        if not vorige.isValid():
            return
        bf_vorher = vorige.blockFormat()
        if _LINIE.match(vorige.text().strip()) and vorige.textList() is None:
            k = QTextCursor(vorige)
            k.beginEditBlock()
            k.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            k.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            k.removeSelectedText()
            bf = QTextBlockFormat()
            bf.setProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth,
                           QTextLength(QTextLength.Type.PercentageLength, 100))
            k.setBlockFormat(bf)
            k.endEditBlock()
            return
        # die neue Zeile erbt sonst Überschrift, Linie und den Haken der Checkbox
        bf = c.blockFormat()
        aendern = False
        if bf_vorher.headingLevel():
            bf.setHeadingLevel(0)
            aendern = True
        if ist_linie(bf):
            bf.clearProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth)
            aendern = True
        if bf.marker() == QTextBlockFormat.MarkerType.Checked:
            bf.setMarker(QTextBlockFormat.MarkerType.Unchecked)
            aendern = True
        if aendern:
            c.joinPreviousEditBlock()
            c.setBlockFormat(bf)
            if bf_vorher.headingLevel():
                cf = QTextCharFormat()
                cf.setFontWeight(QFont.Weight.Normal)
                cf.setFontPointSize(11.5)
                c.setBlockCharFormat(cf)
                self.feld.setCurrentCharFormat(cf)
            c.endEditBlock()

    def _inline(self) -> None:
        c = self.feld.textCursor()
        if c.hasSelection():
            return
        b = c.block()
        vor = b.text()[:c.positionInBlock()]
        aktuell = c.charFormat()
        if ist_code(aktuell):
            return                                         # in Code bleibt alles wörtlich
        for regel, art in _INLINE:
            m = regel.search(vor)
            if not m:
                continue
            fmt = QTextCharFormat(aktuell)
            if art == "fett":
                fmt.setFontWeight(QFont.Weight.Bold)
            elif art == "kursiv":
                fmt.setFontItalic(True)
            elif art == "durch":
                fmt.setFontStrikeOut(True)
            else:
                fmt.merge(code_format(True))
            k = QTextCursor(c)
            k.beginEditBlock()
            k.setPosition(b.position() + m.start())
            k.setPosition(b.position() + m.end(), QTextCursor.MoveMode.KeepAnchor)
            k.insertText(m.group(1), fmt)
            weiter = QTextCharFormat(aktuell)               # danach normal weiterschreiben
            weiter.setFontWeight(QFont.Weight.Normal if art == "fett" else aktuell.fontWeight())
            if art == "kursiv":
                weiter.setFontItalic(False)
            if art == "durch":
                weiter.setFontStrikeOut(False)
            if art == "code":
                weiter.setFontFixedPitch(False)
                weiter.clearProperty(QTextFormat.Property.FontFamilies)
                weiter.clearBackground()
            k.endEditBlock()
            self.feld.setTextCursor(k)
            self.feld.setCurrentCharFormat(weiter)
            return


def _ueberschrift(c: QTextCursor, stufe: int) -> None:
    bf = c.blockFormat()
    bf.setHeadingLevel(stufe)
    c.setBlockFormat(bf)
    cf = QTextCharFormat()
    cf.setFontWeight(QFont.Weight.Bold)
    cf.setFontPointSize({1: 22, 2: 17, 3: 14}.get(stufe, 12.5))
    c.setBlockCharFormat(cf)
    c.setCharFormat(cf)


def _liste(c: QTextCursor, stil) -> None:
    lf = QTextListFormat()
    lf.setStyle(stil)
    c.createList(lf)


# ---------------------------------------------------------------------------
# Aussehen: Code, Tabellen
# ---------------------------------------------------------------------------

def aufhuebschen(doc: QTextDocument, von: int = 0, bis: int | None = None) -> None:
    """Code (aus Markdown/HTML) in Schreibmaschinenschrift mit grauem Grund, Tabellen mit Polster – ohne eigenen
    Rückgängig-Schritt (an den letzten angehängt)."""
    bis = doc.characterCount() if bis is None else min(bis, doc.characterCount())
    c = QTextCursor(doc)
    begonnen = False
    tabellen = set()
    b = doc.findBlock(max(0, von))
    while b.isValid() and b.position() <= bis:
        it = b.begin()
        while not it.atEnd():
            frag = it.fragment()
            cf = frag.charFormat()
            if frag.isValid() and ist_code(cf) and (not cf.fontFixedPitch()
                                                    or cf.background().style() == Qt.BrushStyle.NoBrush
                                                    or (cf.fontFamilies() or [""])[0] != CODE_SCHRIFTEN[0]):
                if not begonnen:
                    c.joinPreviousEditBlock()
                    begonnen = True
                c.setPosition(frag.position())
                c.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                neu = code_format(True)
                if cf.background().style() != Qt.BrushStyle.NoBrush:
                    neu.setBackground(cf.background())
                c.mergeCharFormat(neu)
            it += 1
        tabelle = QTextCursor(b).currentTable()
        if tabelle is not None:
            tabellen.add(tabelle)
        b = b.next()
    for t in tabellen:
        tf = t.format()
        if tf.cellPadding() < 4:
            if not begonnen:
                c.joinPreviousEditBlock()
                begonnen = True
            tf.setCellPadding(5)
            tf.setCellSpacing(0)
            tf.setBorder(0.8)
            tf.setBorderBrush(QBrush(QColor("#9a9aa6")))
            tf.setBorderCollapse(True)
            t.setFormat(tf)
    if begonnen:
        c.endEditBlock()


# ---------------------------------------------------------------------------
# Malen: Zitat-Balken, Seitenlinien
# ---------------------------------------------------------------------------

def malen(feld, p: QPainter) -> None:
    """Zitat-Balken über den sichtbaren Blöcken."""
    doc = feld.document()
    layout = doc.documentLayout()
    dy = feld.verticalScrollBar().value()
    dx = feld.horizontalScrollBar().value()
    hoehe = feld.viewport().height()
    b = feld.cursorForPosition(feld.viewport().rect().topLeft()).block()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    while b.isValid():
        r = layout.blockBoundingRect(b).translated(-dx, -dy)
        if r.top() > hoehe:
            break
        bf = b.blockFormat()
        if ist_zitat(bf):
            x = r.left() + max(0.0, bf.leftMargin() - 14)
            p.fillRect(QRectF(r.left() + bf.leftMargin() - 6, r.top(), r.width() - bf.leftMargin() + 6, r.height()),
                       QColor(139, 92, 246, 16))
            p.fillRect(QRectF(x, r.top() + 2, 3, max(4.0, r.height() - 4)), ZITAT_FARBE)
        b = b.next()


def checkbox_klick(feld, pos) -> bool:
    """Klick links neben einem Checkbox-Punkt schaltet ihn um. True = Klick verbraucht."""
    c = feld.cursorForPosition(pos)
    b = c.block()
    bf = b.blockFormat()
    if bf.marker() == QTextBlockFormat.MarkerType.NoMarker or b.textList() is None:
        return False
    start = feld.cursorRect(QTextCursor(b))
    if not (start.left() - 26 <= pos.x() <= start.left() + 2 and start.top() - 4 <= pos.y() <= start.bottom() + 4):
        return False
    k = QTextCursor(b)
    neu = QTextBlockFormat.MarkerType.Unchecked if bf.marker() == QTextBlockFormat.MarkerType.Checked \
        else QTextBlockFormat.MarkerType.Checked
    k.beginEditBlock()
    bf.setMarker(neu)
    k.setBlockFormat(bf)
    k.endEditBlock()
    return True


# ---------------------------------------------------------------------------
# Markdown-Export mit Spaltenausrichtung
# ---------------------------------------------------------------------------

_TRENNER = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$", re.MULTILINE)


def markdown(doc: QTextDocument) -> str:
    """doc.toMarkdown(), dazu die Ausrichtung der Tabellenspalten (:--, :-:, --:) in den Trennzeilen."""
    text = doc.toMarkdown()
    ausrichtungen = []
    it = doc.rootFrame().begin()
    while not it.atEnd():
        f = it.currentFrame()
        if isinstance(f, QTextTable) and f.rows():
            zeile = 1 if f.rows() > 1 else 0
            spalten = []
            for s in range(f.columns()):
                al = f.cellAt(zeile, s).firstCursorPosition().blockFormat().alignment()
                spalten.append("---:" if al & Qt.AlignmentFlag.AlignRight else
                               ":---:" if al & Qt.AlignmentFlag.AlignHCenter else "---")
            ausrichtungen.append(spalten)
        it += 1
    if not ausrichtungen:
        return text
    teile, pos, n = [], 0, 0
    for m in _TRENNER.finditer(text):
        if n >= len(ausrichtungen):
            break
        spalten = ausrichtungen[n]
        if m.group(0).count("|") - 1 == len(spalten) and any(s != "---" for s in spalten):
            teile.append(text[pos:m.start()])
            teile.append("|" + "|".join(spalten) + "|")
            pos = m.end()
        n += 1
    teile.append(text[pos:])
    return "".join(teile)


# ---------------------------------------------------------------------------
# Bilder beim Speichern als Markdown und HTML
# ---------------------------------------------------------------------------

_MD_BILD = re.compile(r'!\[[^\]\n]*\]\((?:<([^>\n]*)>|([^)\s]*))(?:\s+"[^"\n]*")?\)')


def bilder_namen(doc: QTextDocument) -> list[str]:
    """Namen aller Bilder im Dokument (auch in Tabellenzellen), in Reihenfolge, ohne Doppelte."""
    namen = []
    b = doc.begin()
    while b.isValid():
        it = b.begin()
        while not it.atEnd():
            f = it.fragment()
            if f.isValid() and f.charFormat().isImageFormat():
                n = f.charFormat().toImageFormat().name()
                if n and n not in namen:
                    namen.append(n)
            it += 1
        b = b.next()
    return namen


def bild_von(doc: QTextDocument, name: str) -> QImage | None:
    r = doc.resource(QTextDocument.ResourceType.ImageResource, QUrl(name))
    if isinstance(r, QPixmap):
        r = r.toImage()
    elif isinstance(r, (QByteArray, bytes)):
        r = QImage.fromData(bytes(r))
    return r if isinstance(r, QImage) and not r.isNull() else None


def png_bytes(bild: QImage) -> bytes:
    puffer = QBuffer()
    puffer.open(QIODevice.OpenModeFlag.WriteOnly)
    bild.save(puffer, "PNG")
    return bytes(puffer.data())


def bilder_ordner(md_pfad) -> str:
    """Ordner neben der .md: „<Name>_Bilder“."""
    return f"{Path(md_pfad).stem}_Bilder"


def markdown_speichern(doc: QTextDocument, md_pfad, mit_bildern: bool) -> tuple[str, list[tuple[str, bytes]]]:
    """Markdown fürs Speichern. Mit Bildern: jedes Bild als PNG im Ordner „<Name>_Bilder“ daneben, Links relativ.
    Ohne Bilder: alle Bild-Verweise fallen weg. Rückgabe (Text, [(relativer Pfad, PNG-Bytes)])."""
    text = markdown(doc)
    if not mit_bildern:
        text = _MD_BILD.sub("", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n", []
    ordner = bilder_ordner(md_pfad)
    dateien, neu = [], {}
    for i, name in enumerate(bilder_namen(doc), 1):
        bild = bild_von(doc, name)
        if bild is None:
            continue
        rel = f"{ordner}/bild-{i:02d}.png"
        dateien.append((rel, png_bytes(bild)))
        neu[name] = quote(rel)

    def ersetzen(m):
        ziel = m.group(1) if m.group(1) is not None else m.group(2)
        if ziel not in neu:
            return m.group(0)
        alt = m.group(0)
        return alt[:alt.index("](") + 1] + f"({neu[ziel]})"
    return _MD_BILD.sub(ersetzen, text), dateien


def html_mit_bildern(doc: QTextDocument) -> str:
    """doc.toHtml() mit allen Bildern eingebettet (data:-Adressen): die .html ist eine einzige Datei."""
    text = doc.toHtml()
    for name in bilder_namen(doc):
        bild = bild_von(doc, name)
        if bild is None:
            continue
        daten = "data:image/png;base64," + base64.b64encode(png_bytes(bild)).decode("ascii")
        text = text.replace(f'src="{_html.escape(name, quote=True)}"', f'src="{daten}"')
    return text

