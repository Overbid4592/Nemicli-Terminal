"""office_agent.py – führt die Werkzeuge der OfficeAI im geöffneten Dokument aus (buero/werkzeuge.py).

Vor jeder Aktion fliegt der Geist-Zeiger (lila Pfeil) an die Stelle, dann wird geändert – sichtbar, mit
Rückgängig, nur im Dokument dieses Reiters. „ansehen“ ist der Selbstblick: was gerade sichtbar ist, als Text.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen, QTextCharFormat, QTextCursor, QTextDocument,
                           QTextFormat)
from PySide6.QtWidgets import QWidget

from buero import formel as F
from buero import werkzeuge as WZ
from buero.tabelle import aus_markdown
from design import schrift

FLUG_MS = 380                  # so lange fliegt der Zeiger zur Stelle
# Werkzeuge, die nur die Sitzung schickt (nach dem Malen mit dem Bild-Motor, für Seh-Modell und Umzeichnen)
INTERN = {("schreiben", "bild_einfuegen"), ("malen", "bild_setzen"), ("malen", "leinwand_export")}
PAUSE_MS = 120                 # danach kurz zeigen, dann ändern
SICHT_ZEILEN, SICHT_SPALTEN = 40, 12


class Geistzeiger(QWidget):
    """Lila Mauszeiger über dem Fenster; nimmt keine Klicks an."""

    def __init__(self, eltern: QWidget):
        super().__init__(eltern)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedSize(150, 44)
        self.text = ""
        self.hide()
        self._flug = QPropertyAnimation(self, b"pos", self)
        self._flug.setEasingCurve(QEasingCurve.Type.OutCubic)

    def fliegen(self, ziel: QPoint, text: str = "") -> None:
        self.text = text
        if not self.isVisible():
            self.move(self.parentWidget().width() - 120, self.parentWidget().height() - 120)
            self.show()
        self.raise_()
        self._flug.stop()
        self._flug.setDuration(FLUG_MS)
        self._flug.setStartValue(self.pos())
        self._flug.setEndValue(ziel)
        self._flug.start()
        self.update()

    def ausblenden(self) -> None:
        self._flug.stop()
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pfeil = QPainterPath()
        pfeil.moveTo(2, 2)
        pfeil.lineTo(2, 22)
        pfeil.lineTo(7.5, 17)
        pfeil.lineTo(11.5, 25)
        pfeil.lineTo(15, 23.5)
        pfeil.lineTo(11, 15.5)
        pfeil.lineTo(18, 15.5)
        pfeil.closeSubpath()
        p.setPen(QPen(QColor("#ffffff"), 1.4))
        p.setBrush(QColor("#8b5cf6"))
        p.drawPath(pfeil)
        if self.text:
            p.setFont(schrift(8, True))
            fm = p.fontMetrics()
            breite = min(126, fm.horizontalAdvance(self.text) + 14)
            r = QRectF(18, 20, breite, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(139, 92, 246, 225))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, fm.elidedText(self.text, Qt.TextElideMode.ElideRight,
                                                                      int(breite - 10)))


def _kurz(aktion: dict) -> str:
    name = aktion.get("tool", "")
    if name == "faerben":
        return f"färbt {aktion.get('bereich', '')}"
    if name == "eintragen":
        return f"schreibt in {aktion.get('zelle', '')}"
    return {"ansehen": "schaut", "ersetzen": "ersetzt", "einfuegen": "schreibt", "neu_schreiben": "schreibt neu",
            "formatieren": "formatiert", "papier": "Papier", "leeren": "leert", "zeilen": "Zeilen", "spalten": "Spalten",
            "sortieren": "sortiert", "blatt": "Blatt", "bild_einfuegen": "setzt das Bild ein",
            "zeichnen": "zeichnet", "fuellen": "füllt", "hintergrund": "Hintergrund", "groesse": "Größe",
            "bild_setzen": "setzt das Bild ein", "leinwand_export": "schaut hin"}.get(name, name)


class Ausfuehrer:
    """Führt eine Aktion auf der aktuellen Seite aus: erst Zeiger hinfliegen, dann ändern, dann melden."""

    def __init__(self, fenster, zeiger: Geistzeiger):
        self.fenster = fenster
        self.zeiger = zeiger
        self.angehalten = False

    def ausfuehren(self, seite, aktion: dict, fertig) -> None:
        """fertig(ok, text, kontext) wird nach der Änderung gerufen."""
        app = getattr(seite, "app", "start")
        intern = (app, aktion.get("tool")) in INTERN                    # kommt nur von der Sitzung
        if not intern and (grund := WZ.pruefen(app, aktion)) is not None:
            fertig(False, grund, seite.kontext() if seite else None)
            return
        if app == "malen":
            self._malen(seite, aktion, fertig)
            return
        try:
            ziel = self._ziel(seite, aktion)
        except Exception:
            ziel = None
        if ziel is not None:
            self.zeiger.fliegen(ziel, _kurz(aktion))

        def jetzt():
            if self.angehalten:
                fertig(False, "angehalten", None)
                return
            try:
                ok, text = (self._schreiben if app == "schreiben" else self._tabelle)(seite, aktion)
            except Exception as e:                       # ein Werkzeugfehler beendet nie das Fenster
                ok, text = False, f"Fehler: {type(e).__name__}: {e}"
            fertig(ok, text, seite.kontext())
        QTimer.singleShot((FLUG_MS + PAUSE_MS) if ziel is not None else 0, self.fenster, jetzt)

    def _malen(self, seite, aktion: dict, fertig) -> None:
        """Malen läuft über Zeit (Form für Form): die Seite ruft fertig selbst, mit dem Stand danach."""
        try:
            ziel = seite.ziel(aktion, self.fenster)
        except Exception:
            ziel = None
        if ziel is not None:
            self.zeiger.fliegen(ziel, _kurz(aktion))

        def gemeldet(ok: bool, text: str) -> None:
            fertig(ok, text, seite.kontext())

        def jetzt():
            if self.angehalten:
                fertig(False, "angehalten", None)
                return
            try:
                seite.ki_aktion(aktion, self, gemeldet)
            except Exception as e:                       # ein Werkzeugfehler beendet nie das Fenster
                fertig(False, f"Fehler: {type(e).__name__}: {e}", seite.kontext())
        QTimer.singleShot((FLUG_MS + PAUSE_MS) if ziel is not None else 0, self.fenster, jetzt)

    # -- Ziel des Zeigers ------------------------------------------------------------------
    def _ziel(self, seite, aktion: dict) -> QPoint | None:
        if seite.app == "tabelle":
            name = aktion.get("tool")
            stelle = aktion.get("zelle") or aktion.get("bereich")
            if name in ("zeilen", "spalten", "blatt", "ansehen") or not stelle:
                return self._mitte(seite.raster.viewport())
            b = WZ.bereich(stelle)
            if b is None:
                return None
            i = seite.modell.index(min(b[0], seite.modell.rowCount() - 1), min(b[1], seite.modell.columnCount() - 1))
            seite.raster.scrollTo(i)
            r = seite.raster.visualRect(i)
            return seite.raster.viewport().mapTo(self.fenster, r.center())
        suchen = aktion.get("suchen") or aktion.get("bezug")
        doc = seite.feld.document()
        c = None
        if suchen:
            c = doc.find(str(suchen))
            if c.isNull():
                c = None
        elif aktion.get("tool") == "einfuegen" and str(aktion.get("wo") or "ende") in ("ende", "anfang"):
            c = QTextCursor(doc)
            c.movePosition(QTextCursor.MoveOperation.End if str(aktion.get("wo") or "ende") == "ende"
                           else QTextCursor.MoveOperation.Start)
        if c is None:
            return self._mitte(seite.feld.viewport())
        seite.feld.setTextCursor(c)
        seite.feld.ensureCursorVisible()
        r = seite.feld.cursorRect(c)
        return seite.feld.viewport().mapTo(self.fenster, r.center())

    def _mitte(self, w: QWidget) -> QPoint:
        return w.mapTo(self.fenster, QPoint(w.width() // 2, w.height() // 3))

    # -- Schreiben -------------------------------------------------------------------------
    def _schreiben(self, seite, a: dict) -> tuple[bool, str]:
        name = a["tool"]
        feld = seite.feld
        doc: QTextDocument = feld.document()
        if name == "ansehen":
            return True, self._blick_schreiben(seite)
        if name == "bild_einfuegen":
            return self._bild_einsetzen(seite, str(a.get("pfad") or ""), str(a.get("wo") or "cursor"))
        if name == "papier":
            seite.papier(WZ.farbe(a.get("farbe")) or "")
            return True, "Papierfarbe gesetzt." if a.get("farbe") else "Papier wieder weiß."
        if name == "neu_schreiben":
            from office_schreiben import papier_setzen, papier_von
            papier = papier_von(doc)
            c = QTextCursor(doc)
            c.beginEditBlock()
            c.select(QTextCursor.SelectionType.Document)
            c.removeSelectedText()
            c.insertMarkdown(str(a.get("text") or ""))
            if papier_von(doc) != papier:                 # im selben Schritt: ein Rückgängig holt alles zurück
                papier_setzen(doc, papier)
            c.endEditBlock()
            return True, f"Dokument neu geschrieben ({len(doc.toPlainText())} Zeichen)."
        if name == "einfuegen":
            wo = str(a.get("wo") or "ende").lower()
            c = QTextCursor(doc)
            if wo in ("nach", "vor"):
                c = doc.find(str(a.get("bezug")))
                if c.isNull():
                    return False, f"bezug nicht gefunden: {str(a.get('bezug'))[:80]}"
            c.beginEditBlock()
            if wo == "anfang":
                c.movePosition(QTextCursor.MoveOperation.Start)
                c.insertMarkdown(str(a.get("text")))
                c.insertBlock()
            elif wo == "cursor":
                c = feld.textCursor()
                c.insertMarkdown(str(a.get("text")))
            elif wo == "vor":
                c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                c.insertMarkdown(str(a.get("text")))
                c.insertBlock()
            else:                                       # ende oder nach
                if wo == "nach":
                    c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                else:
                    c.movePosition(QTextCursor.MoveOperation.End)
                if c.block().text().strip():
                    c.insertBlock()
                c.insertMarkdown(str(a.get("text")))
            c.endEditBlock()
            feld.setTextCursor(c)
            return True, "Eingefügt."
        treffer = self._finden(doc, str(a.get("suchen")), WZ.wahr(a.get("alle")))
        if not treffer:
            return False, f"Nicht gefunden: {str(a.get('suchen'))[:80]}"
        k = QTextCursor(doc)
        k.beginEditBlock()
        try:
            for c in reversed(treffer):                 # von hinten: Positionen bleiben gültig
                if name == "ersetzen":
                    c.insertText(str(a.get("durch") or ""))
                else:
                    self._format(seite, c, a)
        finally:
            k.endEditBlock()
        return True, f"{len(treffer)}× {'ersetzt' if name == 'ersetzen' else 'formatiert'}."

    @staticmethod
    def _bild_einsetzen(seite, pfad: str, wo: str) -> tuple[bool, str]:
        """Gemaltes Bild einsetzen – nur eine lokale Bilddatei (der Pfad kommt aus der Sitzung)."""
        from pathlib import Path
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QImage
        from office_schreiben import bild_einfuegen, lokal_erlaubt
        p = Path(pfad)
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not lokal_erlaubt(QUrl.fromLocalFile(pfad)) \
                or not p.is_file():
            return False, "Kein lesbares Bild."
        bild = QImage(pfad)
        if bild.isNull():
            return False, "Das Bild ließ sich nicht lesen."
        c = seite.feld.textCursor()
        if wo == "ende":
            c.movePosition(QTextCursor.MoveOperation.End)
        c.beginEditBlock()
        if c.block().text():
            c.insertBlock()
        seite.feld.setTextCursor(c)
        bild_einfuegen(seite.feld, bild)
        c = seite.feld.textCursor()
        c.insertBlock()
        c.endEditBlock()
        return True, f"Bild eingesetzt ({bild.width()}×{bild.height()})."

    @staticmethod
    def _finden(doc: QTextDocument, suchen: str, alle: bool) -> list[QTextCursor]:
        raus, c = [], doc.find(suchen)
        while not c.isNull() and len(raus) < 500:
            raus.append(c)
            if not alle:
                break
            c = doc.find(suchen, c)
        return raus

    @staticmethod
    def _format(seite, c: QTextCursor, a: dict) -> None:
        fmt = QTextCharFormat()
        if "fett" in a:
            fmt.setFontWeight(QFont.Weight.Bold if WZ.wahr(a["fett"]) else QFont.Weight.Normal)
        if "kursiv" in a:
            fmt.setFontItalic(WZ.wahr(a["kursiv"]))
        if "unter" in a:
            fmt.setFontUnderline(WZ.wahr(a["unter"]))
        entfernen = []
        for feld, eigenschaft, schrift_ in (("farbe", QTextFormat.Property.ForegroundBrush, True),
                                            ("marker", QTextFormat.Property.BackgroundBrush, False)):
            if feld in a:
                wert = WZ.farbe(a[feld], schrift_)
                if wert:
                    (fmt.setForeground if feld == "farbe" else fmt.setBackground)(QColor(wert))
                else:
                    entfernen.append(eigenschaft)
        c.mergeCharFormat(fmt)
        for eigenschaft in entfernen:
            seite._eigenschaft_weg(c, eigenschaft)
        if "stufe" in a:
            try:
                stufe = max(0, min(3, int(a["stufe"])))
            except (TypeError, ValueError):
                stufe = 0
            bf = c.blockFormat()
            bf.setHeadingLevel(stufe)
            c.setBlockFormat(bf)
            cf = QTextCharFormat()
            cf.setFontWeight(QFont.Weight.Bold if stufe else QFont.Weight.Normal)
            cf.setFontPointSize({0: 11.5, 1: 22, 2: 17, 3: 14}[stufe])
            k = QTextCursor(c.block())
            k.select(QTextCursor.SelectionType.BlockUnderCursor)
            k.mergeCharFormat(cf)

    @staticmethod
    def _blick_schreiben(seite) -> str:
        from office_schreiben import papier_von
        feld = seite.feld
        oben = feld.cursorForPosition(QPoint(0, 0)).position()
        unten = feld.cursorForPosition(QPoint(feld.viewport().width() - 1, feld.viewport().height() - 1)).position()
        text = feld.document().toPlainText()
        c = feld.textCursor()
        zeilen = [f"Sichtbar (Zeichen {oben}–{unten} von {len(text)}):", text[oben:unten][:3000],
                  f"Schreibmarke: Absatz {c.blockNumber() + 1}, Zeichen {c.position()}",
                  f"Markiert: {c.selectedText()[:300]!r}" if c.hasSelection() else "Nichts markiert.",
                  f"Papier: {papier_von(feld.document()) or 'weiß'} · {len(text.split())} Wörter"]
        return "\n".join(zeilen)

    # -- Tabelle ---------------------------------------------------------------------------
    def _tabelle(self, seite, a: dict) -> tuple[bool, str]:
        name = a["tool"]
        m, b = seite.modell.mappe, seite.modell.blatt
        if name == "ansehen":
            return True, self._blick_tabelle(seite)
        if name == "eintragen":
            z0, s0 = F.zelle_aus(str(a["zelle"]))
            text = str(a.get("text") if a.get("text") is not None else "")
            zeilen = aus_markdown(text) if "|" in text or "\t" in text else []
            if not zeilen:
                zeilen = [[text]]
            if sum(len(z) for z in zeilen) > WZ.MAX_ZELLEN:
                return False, f"Mehr als {WZ.MAX_ZELLEN} Zellen auf einmal."
            n = seite._zeilen_setzen(zeilen, z0, s0)
            seite.raster.setCurrentIndex(seite.modell.index(z0, s0))
            return True, f"{n} Zelle(n) ab {a['zelle']} eingetragen."
        if name in ("faerben", "leeren"):
            z1, s1, z2, s2 = WZ.bereich(a["bereich"])
            zellen = [(z, s) for z in range(z1, z2 + 1) for s in range(s1, s2 + 1)]
            if name == "leeren":
                seite.modell.setzen([(z, s, "") for z, s in zellen if m.roh(b, z, s)])
                return True, f"{a['bereich']} geleert."
            aenderung = {}
            if "hg" in a:
                aenderung["hg"] = WZ.farbe(a["hg"]) or ""
            if "fg" in a:
                aenderung["fg"] = WZ.farbe(a["fg"], schrift=True) or ""
            if "fett" in a:
                aenderung["fett"] = WZ.wahr(a["fett"])
            if not aenderung:
                return False, "Keine Farbe und kein fett angegeben."
            seite.modell.formatieren(zellen, aenderung)
            return True, f"{a['bereich']} formatiert."
        if name in ("zeilen", "spalten"):
            achse = "zeile" if name == "zeilen" else "spalte"
            ab = WZ.zeile(a["ab"]) if achse == "zeile" else WZ.spalte(a["ab"])
            n = WZ.anzahl(a.get("anzahl"), 1, 500)
            einfuegen = str(a.get("aktion") or "einfuegen").lower().startswith("einf")
            seite.struktur(achse, ab, n, einfuegen)
            return True, f"{n} {'Zeile(n)' if achse == 'zeile' else 'Spalte(n)'} {'eingefügt' if einfuegen else 'gelöscht'}."
        if name == "sortieren":
            z1, _, z2, _ = WZ.bereich(a["bereich"])
            ok, text = seite.sortieren_bereich(z1, z2, WZ.spalte(a["nach"]), WZ.wahr(a.get("absteigend")))
            return ok, text
        if name == "blatt":
            ziel = str(a.get("name") or "").strip()
            if not ziel:
                return False, "name fehlt."
            vorhanden = m.blatt(ziel)
            if vorhanden is None:
                if not WZ.wahr(a.get("neu")):
                    return False, f"Blatt '{ziel}' gibt es nicht – neu: true legt es an."
                try:
                    neu = m.blatt_dazu(ziel)
                except ValueError as e:
                    return False, str(e)
                seite._reiter_fuellen()
                vorhanden = neu
            seite.reiter.setCurrentIndex(m.blaetter.index(vorhanden))
            return True, f"Blatt '{vorhanden.name}' ist offen."
        return False, f"Unbekannt: {name}"

    @staticmethod
    def _blick_tabelle(seite) -> str:
        r = seite.raster
        m, b = seite.modell.mappe, seite.modell.blatt
        z1 = max(0, r.rowAt(0))
        s1 = max(0, r.columnAt(0))
        z2 = r.rowAt(r.viewport().height() - 1)
        s2 = r.columnAt(r.viewport().width() - 1)
        z2 = min(z1 + SICHT_ZEILEN - 1, z2 if z2 >= 0 else z1 + SICHT_ZEILEN - 1)
        s2 = min(s1 + SICHT_SPALTEN - 1, s2 if s2 >= 0 else s1 + SICHT_SPALTEN - 1)
        kopf = "| | " + " | ".join(F.spalte_name(s) for s in range(s1, s2 + 1)) + " |"
        zeilen = [f"Sichtbar: {F.zelle_name(z1, s1)}:{F.zelle_name(z2, s2)} · Blatt {b.name} "
                  f"(Blätter: {', '.join(x.name for x in m.blaetter)}) · belegt bis "
                  f"{F.zelle_name(max(0, b.groesse()[0] - 1), max(0, b.groesse()[1] - 1))}",
                  kopf, "|---" * (s2 - s1 + 2) + "|"]
        farben = []
        for z in range(z1, z2 + 1):
            zellen = []
            for s in range(s1, s2 + 1):
                zellen.append(m.anzeige(b, z, s).replace("|", "\\|")[:30])
                if (f := m.format(b, z, s)):
                    farben.append(f"{F.zelle_name(z, s)} {f}")
            zeilen.append(f"| {z + 1} | " + " | ".join(zellen) + " |")
        i = r.currentIndex()
        if i.isValid():
            zeilen.append(f"Aktuelle Zelle: {F.zelle_name(i.row(), i.column())} = {m.roh(b, i.row(), i.column())!r}")
        if farben:
            zeilen.append("Farben: " + "; ".join(farben[:60]))
        return "\n".join(zeilen)
