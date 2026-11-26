"""zeitplan_fenster.py – Zeitplan-Aufträge ansehen, anlegen, ändern und löschen (/zeitplan), eigener Prozess (`--zeitplan`).

Links ein Monatskalender (Tage mit Läufen sind markiert, ein Klick zeigt, was an dem Tag startet) und die Liste der
Aufträge, rechts der Auftrag selbst. Mit `--vorschlag <datei>` öffnet das Fenster einen Vorschlag der KI: Gespeichert
oder gelöscht wird erst mit dem Klick des Nutzers. Eingetragen wird über tools/zeitplan.py (Windows-Aufgabenplanung).
"""
from __future__ import annotations

import os
import sys
from datetime import date

from gui_fenster import FARBE, Flaeche, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label, schrift
from PySide6.QtCore import QDate, QTime, QTimer, Qt
from PySide6.QtGui import QTextCharFormat
from PySide6.QtWidgets import (QCalendarWidget, QDateEdit, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QSpinBox, QTimeEdit, QVBoxLayout, QWidget)
from termin_fenster import Schalter

import zeitplan as Z

STIL = f"""
QWidget#zeitplan {{ background: {FARBE['grund']}; }}
QLineEdit, QPlainTextEdit, QTimeEdit, QDateEdit, QSpinBox, QListWidget {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px 10px; selection-background-color: {FARBE['lila']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTimeEdit:focus, QDateEdit:focus, QSpinBox:focus {{
    border: 1px solid {FARBE['cyan']}; }}
QListWidget::item {{ padding: 7px 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {FARBE['flaeche_hoch']}; color: {FARBE['text']}; }}
QCalendarWidget QWidget {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; }}
QCalendarWidget QAbstractItemView {{ selection-background-color: {FARBE['lila']}; selection-color: {FARBE['text']}; }}
QCalendarWidget QToolButton {{ color: {FARBE['text']}; background: transparent; padding: 4px 8px; }}
QCalendarWidget #qt_calendar_navigationbar {{ background: {FARBE['flaeche_hell']}; }}
"""

ARTEN = (("taeglich", "Täglich", "☀"), ("woechentlich", "Wöchentlich", "📆"), ("intervall", "Alle …", "🔁"),
         ("einmal", "Einmal", "📌"), ("anmeldung", "Bei Anmeldung", "⚡"))
TAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


class ZeitplanFenster(QWidget):
    def __init__(self, vorschlag: dict | None = None):
        super().__init__()
        self.setObjectName("zeitplan")
        self.setWindowTitle("NemiCLI – Zeitplan")
        self.setStyleSheet(STIL)
        self.resize(1080, 700)
        self.setMinimumSize(920, 620)
        self.auftraege: dict[str, dict] = {}
        self.windows: dict[str, dict] = {}
        self.geladen = ""                       # Name des Auftrags im Editor („“ = neu)
        self.art = "taeglich"

        haupt = QHBoxLayout(self)
        haupt.setContentsMargins(22, 20, 22, 20)
        haupt.setSpacing(18)
        haupt.addLayout(self._links(), 0)
        haupt.addLayout(self._rechts(), 1)
        self.laden()
        self.neu()
        if vorschlag:
            self._vorschlag(vorschlag)
        QTimer.singleShot(50, self._windows_stand)

    # -- Aufbau ----------------------------------------------------------------------
    def _links(self) -> QVBoxLayout:
        v = QVBoxLayout()
        v.setSpacing(10)
        v.addWidget(label("📅 Zeitplan", 17, "text", fett=True))
        v.addWidget(label("Aufträge, die NemiCLI zu festen Zeiten von selbst erledigt. Der Bericht landet danach "
                          "im Ordner Berichte.", 9.5, "dim"))
        self.kalender = QCalendarWidget()
        self.kalender.setGridVisible(False)
        self.kalender.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.kalender.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.kalender.setMinimumSize(360, 260)
        kopf = QTextCharFormat()                   # Wochentage: lesbar statt Weiß auf Hell
        kopf.setForeground(farbe("dim"))
        kopf.setBackground(farbe("flaeche_hell"))
        self.kalender.setHeaderTextFormat(kopf)
        tag = QTextCharFormat()                    # Wochenende in Textfarbe (Rot auf Lila liest sich schlecht)
        tag.setForeground(farbe("text"))
        for wt in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.kalender.setWeekdayTextFormat(wt, tag)
        self.kalender.clicked.connect(self._tag_gewaehlt)
        self.kalender.currentPageChanged.connect(lambda *_: self._kalender_markieren())
        v.addWidget(self.kalender)
        self.tag_text = label("", 10, "text")
        v.addWidget(self.tag_text)
        v.addWidget(label("Aufträge", 10, "dim"))
        self.liste = QListWidget()
        self.liste.setFont(schrift(10.5))
        self.liste.currentItemChanged.connect(self._auswahl)
        v.addWidget(self.liste, 1)
        neu = Knopf("Neuer Auftrag", "＋", mitte=True)
        neu.clicked.connect(self.neu)
        v.addWidget(neu)
        return v

    def _rechts(self) -> QVBoxLayout:
        v = QVBoxLayout()
        v.setSpacing(10)
        self.banner = Flaeche(radius=12)
        self.banner.leuchten = True
        bl = QVBoxLayout(self.banner)
        bl.setContentsMargins(14, 10, 14, 10)
        self.banner_text = label("", 10.5, "text")
        bl.addWidget(self.banner_text)
        self.banner.hide()
        v.addWidget(self.banner)

        v.addWidget(label("Name", 10, "dim"))
        self.name = QLineEdit()
        self.name.setFont(schrift(11))
        self.name.setMaxLength(60)
        self.name.setPlaceholderText("z. B. Morgenbericht")
        self.name.textChanged.connect(lambda _t: self._anzeigen())
        v.addWidget(self.name)

        v.addWidget(label("Was soll NemiCLI tun?", 10, "dim"))
        self.auftrag = QPlainTextEdit()
        self.auftrag.setFont(schrift(10.5))
        self.auftrag.setPlaceholderText("z. B. Schau dir das System an und schreib mir einen kurzen Bericht.")
        self.auftrag.setMinimumHeight(110)
        v.addWidget(self.auftrag, 1)

        v.addWidget(label("Wann?", 10, "dim"))
        zeile = QHBoxLayout()
        zeile.setSpacing(6)
        self.art_knoepfe = {}
        for schluessel, text, symbol in ARTEN:
            k = Knopf(text, symbol, klein=True, mitte=True)
            k.clicked.connect(lambda _=False, s=schluessel: self._art_setzen(s))
            self.art_knoepfe[schluessel] = k
            zeile.addWidget(k)
        zeile.addStretch(1)
        v.addLayout(zeile)

        self.tage_zeile = QWidget()
        tz = QHBoxLayout(self.tage_zeile)
        tz.setContentsMargins(0, 0, 0, 0)
        tz.setSpacing(6)
        self.tage = []
        for name in TAGE:
            k = Schalter(name, klein=True)
            k.setMinimumWidth(48)
            k.bei_wechsel = self._anzeigen
            self.tage.append(k)
            tz.addWidget(k)
        tz.addStretch(1)
        v.addWidget(self.tage_zeile)

        felder = QHBoxLayout()
        felder.setSpacing(12)
        self.uhr = QTimeEdit()
        self.uhr.setDisplayFormat("HH:mm")
        self.uhr.setFont(schrift(13, True))
        self.uhr.setMinimumWidth(110)
        self.uhr.setTime(QTime(9, 0))
        self.uhr.timeChanged.connect(lambda _t: self._anzeigen())
        self.datum = QDateEdit()
        self.datum.setDisplayFormat("dd.MM.yyyy")
        self.datum.setCalendarPopup(True)
        self.datum.setFont(schrift(11))
        self.datum.setDate(QDate.currentDate().addDays(1))
        self.datum.dateChanged.connect(lambda _d: self._anzeigen())
        self.abstand = QSpinBox()
        self.abstand.setRange(1, 999)
        self.abstand.setValue(2)
        self.abstand.setFont(schrift(11))
        self.abstand.valueChanged.connect(lambda _n: self._anzeigen())
        self.in_stunden = Schalter("Stunden", klein=True)
        self.in_stunden.akzent = True
        self.in_stunden.bei_wechsel = self._anzeigen
        for w in (self.uhr, self.datum, self.abstand, self.in_stunden):
            felder.addWidget(w)
        felder.addStretch(1)
        v.addLayout(felder)

        self.vorschau = label("", 10.5, "text")
        v.addWidget(self.vorschau)
        self.stand = label("", 9.5, "dim")
        v.addWidget(self.stand)
        bericht = QHBoxLayout()
        self.bericht_text = label("", 9.5, "dim")
        self.bericht_k = Knopf("Letzten Bericht öffnen", "📄", klein=True)
        self.bericht_k.clicked.connect(self._bericht_oeffnen)
        bericht.addWidget(self.bericht_text, 1)
        bericht.addWidget(self.bericht_k)
        v.addLayout(bericht)
        self.status = label("", 10, "dim")
        v.addWidget(self.status)

        unten = QHBoxLayout()
        self.loeschen_k = Knopf("Löschen", "🗑", mitte=True)
        self.loeschen_k.clicked.connect(self.loeschen)
        schliessen = Knopf("Schließen", mitte=True)
        schliessen.clicked.connect(self.close)
        self.speichern_k = Knopf("Speichern", "💾", mitte=True)
        self.speichern_k.akzent = True
        self.speichern_k.clicked.connect(self.speichern)
        unten.addWidget(self.loeschen_k)
        unten.addStretch(1)
        unten.addWidget(schliessen)
        unten.addWidget(self.speichern_k)
        v.addLayout(unten)
        return v

    # -- Daten -----------------------------------------------------------------------
    def laden(self, waehlen: str = "") -> None:
        self.auftraege = {a["name"]: a for a in Z.alle_auftraege() if isinstance(a.get("wann"), dict)}
        self.liste.blockSignals(True)
        self.liste.clear()
        for n in sorted(self.auftraege, key=str.lower):
            item = QListWidgetItem(self._zeile(n))
            item.setData(Qt.ItemDataRole.UserRole, n)
            self.liste.addItem(item)
            if n == waehlen:
                self.liste.setCurrentItem(item)
        self.liste.blockSignals(False)
        self._kalender_markieren()
        self._tag_gewaehlt(self.kalender.selectedDate())

    def _zeile(self, n: str) -> str:
        w = self.windows.get(n)
        zusatz = f"\n   nächster Lauf: {w['naechster']}" if w and w.get("naechster") else ""
        return f"{n}  ·  {self.auftraege[n].get('wann_text', '')}{zusatz}"

    def _windows_stand(self) -> None:
        try:
            self.windows = Z._windows_liste()
        except Exception:
            self.windows = {}
        for i in range(self.liste.count()):
            item = self.liste.item(i)
            item.setText(self._zeile(item.data(Qt.ItemDataRole.UserRole)))
        self._stand_zeigen()

    # -- Kalender --------------------------------------------------------------------
    def _kalender_markieren(self) -> None:
        jahr, monat = self.kalender.yearShown(), self.kalender.monthShown()
        leer, mark = QTextCharFormat(), QTextCharFormat()
        leer.setForeground(farbe("text"))
        mark.setForeground(farbe("text"))
        mark.setBackground(farbe("lila", 70))
        mark.setFontWeight(700)
        tag = QDate(jahr, monat, 1)
        while tag.month() == monat:
            d = date(tag.year(), tag.month(), tag.day())
            laeuft = any(Z.laeuft_am(a["wann"], d) for a in self.auftraege.values())
            self.kalender.setDateTextFormat(tag, mark if laeuft else leer)
            tag = tag.addDays(1)

    def _tag_gewaehlt(self, qd: QDate) -> None:
        d = date(qd.year(), qd.month(), qd.day())
        eintraege = sorted((zeit, n) for n, a in self.auftraege.items() for zeit in Z.laeuft_am(a["wann"], d))
        if self.art == "einmal" and qd >= QDate.currentDate():
            self.datum.setDate(qd)                 # Klick im Kalender wählt den Tag für „Einmal“
        kopf = f"{TAGE[d.weekday()]}, {d:%d.%m.%Y}"
        if not eintraege:
            self.tag_text.setText(f"{kopf}: nichts geplant.")
            return
        self.tag_text.setText(kopf + ":\n" + "\n".join(
            f"  • {'laufend' if zeit == 'laufend' else zeit + ' Uhr'}  {n}" for zeit, n in eintraege))

    # -- Editor ----------------------------------------------------------------------
    def neu(self) -> None:
        self.liste.blockSignals(True)
        self.liste.clearSelection()
        self.liste.setCurrentItem(None)
        self.liste.blockSignals(False)
        self.geladen = ""
        self.name.setText("")
        self.auftrag.setPlainText("")
        self._wann_setzen({"art": "taeglich", "zeit": "09:00"})
        self.loeschen_k.setEnabled(False)
        self._melden("", "dim")
        self._stand_zeigen()

    def _auswahl(self, item, _vorher=None) -> None:
        if item is None:
            return
        n = item.data(Qt.ItemDataRole.UserRole)
        a = self.auftraege.get(n)
        if not a:
            return
        self.geladen = n
        self.name.setText(n)
        self.auftrag.setPlainText(a.get("auftrag", ""))
        self._wann_setzen(a["wann"])
        self.loeschen_k.setEnabled(True)
        self._melden("", "dim")
        self._stand_zeigen()

    def _wann_setzen(self, w: dict) -> None:
        art = w.get("art", "taeglich")
        if "zeit" in w:
            h, m = (int(x) for x in w["zeit"].split(":"))
            self.uhr.setTime(QTime(h, m))
        for i, k in enumerate(self.tage):
            k.akzent = Z.WOCHE[i] in w.get("tage", [])
            k.update()
        if art == "intervall":
            minuten = int(w.get("minuten", 120))
            stunden = minuten % 60 == 0
            self.in_stunden.akzent = stunden
            self.in_stunden.update()
            self.abstand.setValue(minuten // 60 if stunden else minuten)
        if art == "einmal" and w.get("datum"):
            self.datum.setDate(QDate.fromString(w["datum"], "yyyy-MM-dd"))
        self._art_setzen(art)

    def _art_setzen(self, art: str) -> None:
        self.art = art
        for s, k in self.art_knoepfe.items():
            k.akzent = s == art
            k.update()
        self.tage_zeile.setVisible(art == "woechentlich")
        self.uhr.setVisible(art in ("taeglich", "woechentlich", "einmal"))
        self.datum.setVisible(art == "einmal")
        self.abstand.setVisible(art == "intervall")
        self.in_stunden.setVisible(art == "intervall")
        self._anzeigen()

    def eingabe(self) -> str:
        """Die Zeitangabe so, wie parse_wann sie versteht."""
        zeit = self.uhr.time().toString("HH:mm")
        if self.art == "taeglich":
            return f"täglich {zeit}"
        if self.art == "woechentlich":
            tage = [Z._KURZTAG[Z.WOCHE[i]] for i, k in enumerate(self.tage) if k.akzent]
            return f"wöchentlich {','.join(tage)} {zeit}" if tage else "wöchentlich"
        if self.art == "intervall":
            return f"alle {self.abstand.value()} {'stunden' if self.in_stunden.akzent else 'minuten'}"
        if self.art == "einmal":
            return f"einmal {self.datum.date().toString('yyyy-MM-dd')} {zeit}"
        return "anmeldung"

    def _anzeigen(self) -> None:
        try:
            self.vorschau.setText("🕒 " + Z.wann_text(Z.parse_wann(self.eingabe())))
            farbname = "text"
        except ValueError as e:
            self.vorschau.setText("⚠ " + str(e).split("\n")[0])
            farbname = "warn"
        pal = self.vorschau.palette()
        pal.setColor(self.vorschau.foregroundRole(), farbe(farbname))
        self.vorschau.setPalette(pal)

    def _stand_zeigen(self) -> None:
        n = self.geladen
        w = self.windows.get(n) if n else None
        if not n:
            self.stand.setText("Neuer Auftrag – erst mit „Speichern“ trägt Windows ihn ein.")
        elif w:
            self.stand.setText(f"Windows: {w.get('status') or '?'}  ·  letzter Lauf: {w.get('letzter') or '–'}  ·  "
                               f"nächster: {w.get('naechster') or '–'}")
        else:
            self.stand.setText("In Windows (noch) nicht eingetragen – „Speichern“ trägt ihn neu ein.")
        b = Z.letzter_bericht(n) if n else None
        self.bericht_text.setText(f"Letzter Bericht: {b.name} – {Z.bericht_kopfzeile(b)}" if b else
                                  ("Noch kein Bericht." if n else ""))
        self.bericht_k.setVisible(b is not None)
        self._bericht = b

    def _bericht_oeffnen(self) -> None:
        if getattr(self, "_bericht", None) and self._bericht.is_file():
            os.startfile(str(self._bericht))           # der Nutzer klickt: Standardprogramm für .md

    # -- Vorschlag der KI --------------------------------------------------------------
    def _vorschlag(self, d: dict) -> None:
        wer = d.get("wer") or "Die KI"
        if d["aktion"] == "loeschen":
            self._waehlen(d["name"])
            self.banner_text.setText(f"💡 {wer} schlägt vor, „{d['name']}“ zu löschen. Wenn du das willst, "
                                     "klick auf „Löschen“ – sonst einfach schließen.")
        else:
            if d["name"] in self.auftraege:
                self._waehlen(d["name"])
            else:
                self.neu()
            self.name.setText(d["name"])
            self.auftrag.setPlainText(d["auftrag"])
            self._wann_setzen(d["wann"])
            self.banner_text.setText(f"💡 Vorschlag von {wer}. Schau ihn dir an, ändere, was du willst, und klick "
                                     "auf „Speichern“. Ohne deinen Klick wird nichts eingetragen.")
        self.banner.show()

    def _waehlen(self, n: str) -> None:
        for i in range(self.liste.count()):
            item = self.liste.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == n:
                self.liste.setCurrentItem(item)
                return

    # -- Speichern / Löschen -----------------------------------------------------------
    def speichern(self) -> None:
        name = Z.sauberer_name(self.name.text())
        if not name:
            self._melden("⚠ Der Name darf nur Buchstaben, Ziffern, Leerzeichen, - und _ enthalten.", "fehler")
            return
        if name != self.geladen and name in self.auftraege and QMessageBox.question(
                self, "Zeitplan", f"Es gibt schon einen Auftrag „{name}“. Ersetzen?") != QMessageBox.StandardButton.Yes:
            return
        self._melden("… trage in Windows ein", "dim")
        self.repaint()
        try:
            text = Z.anlegen(name, self.eingabe(), self.auftrag.toPlainText())
            if self.geladen and self.geladen != name:      # umbenannt: den alten Eintrag entfernen
                Z.loeschen(self.geladen)
        except (ValueError, OSError) as e:
            self._melden(f"⚠ Nicht gespeichert: {str(e).splitlines()[0]}", "fehler")
            return
        self.banner.hide()
        self.geladen = name
        self.laden(waehlen=name)
        self.loeschen_k.setEnabled(True)
        self._melden("✅ " + text.split(" Der Bericht")[0], "ok")
        QTimer.singleShot(50, self._windows_stand)

    def loeschen(self) -> None:
        n = self.geladen
        if not n or QMessageBox.question(self, "Zeitplan", f"Auftrag „{n}“ wirklich löschen?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            text = Z.loeschen(n)
        except (ValueError, OSError) as e:
            self._melden(f"⚠ Nicht gelöscht: {e}", "fehler")
            return
        self.banner.hide()
        self.laden()
        self.neu()
        self._melden("🗑 " + text, "ok")

    def _melden(self, text: str, farbname: str) -> None:
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(self.status.foregroundRole(), farbe(farbname))
        self.status.setPalette(pal)


def starten() -> int:
    vorschlag = None
    if "--vorschlag" in sys.argv:
        i = sys.argv.index("--vorschlag")
        if i + 1 < len(sys.argv):
            vorschlag = Z.vorschlag_lesen(sys.argv[i + 1])
    app = app_vorbereiten("NemiCLI.Zeitplan")
    fenster = ZeitplanFenster(vorschlag)
    _dunkler_rahmen(fenster)
    fenster.show()
    fenster.raise_()
    fenster.activateWindow()
    return app.exec()


if __name__ == "__main__":                      # Probe ohne Sitzung: python ui/zeitplan_fenster.py
    raise SystemExit(starten())
