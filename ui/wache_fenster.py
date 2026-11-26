"""wache_fenster.py – 🛡 Wache-Fenster (/wache fenster): Alarme sichten, Steckbrief, Urteil per Klick, Wache-KI.

Links die Alarme (Filter, Suche), rechts der Steckbrief des gewählten Alarms mit Signatur und Belegen und darunter
die Knöpfe fürs Urteil. Unten rechts die Kugel: die Wache-KI mit eigenem Cloud-Modell (oben wählbar). Sie liest,
prüft und schlägt vor – ein Vorschlag erscheint als Karte mit Ja/Nein; geurteilt wird nur mit einem Klick.
ESC hält die KI an. Gelöscht oder beendet wird hier nichts.
"""
from __future__ import annotations

import os

from design import FARBE, schrift
from gui_fenster import Knopf, _dunkler_rahmen, app_vorbereiten, label, rollbereich
from office_kugel import OfficeAI
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

FILTER = [("aktiv", "Aktiv (offen · unklar · gesehen · echt)"), ("offen", "🟠 Offen"), ("unklar", "🟡 Unklar"),
          ("gesehen", "⚪ Gesehen"), ("echt", "🔴 Echt"), ("harmlos", "🟢 Harmlos"), ("alle", "Alle")]
URTEIL_KNOEPFE = [("harmlos", "🟢", "Harmlos"), ("gesehen", "⚪", "Gesehen"), ("unpruefbar", "🟡", "Unklar"),
                  ("echt", "🔴", "Echt"), ("erledigt", "✔", "Erledigt")]
URTEIL_TEXT = {"harmlos": "🟢 harmlos", "gesehen": "⚪ gesehen", "unpruefbar": "🟡 unklar", "echt": "🔴 echt",
               "erledigt": "✔ erledigt"}
AUFTRAEGE = [
    ("🔎", "Diesen prüfen", "Prüf den gewählten Alarm: Belege sammeln, wenn nötig recherchieren, einordnen und "
                           "bewerten. Sag mir kurz, was es ist und warum."),
    ("📋", "Alle offenen", "Geh alle offenen Alarme durch: prüfen, wenn nötig recherchieren, die belegten bewerten. "
                          "Zum Schluss eine kurze Tabelle Alarm · Einordnung · Beleg."),
    ("🌐", "Recherchieren", "Recherchier im Netz, was das Programm des gewählten Alarms ist und wer es herstellt – "
                           "und vergleiche das mit Pfad und Signatur hier auf dem Rechner."),
    ("🔐", "Signatur prüfen", "Prüf die Signatur der Datei des gewählten Alarms und sag mir, was sie bedeutet."),
    ("🧭", "Was ist neu?", "Was hat die Wache in den letzten 24 Stunden gemeldet? Kurz, nach Einordnung sortiert."),
]


def _rahmen(name: str) -> str:
    return (f"QFrame#{name} {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; "
            f"border-radius: 14px; }}")


def _feld_stil() -> str:
    return (f"QLineEdit, QComboBox {{ background: {FARBE['flaeche']}; color: {FARBE['text']}; "
            f"border: 1px solid {FARBE['rand']}; border-radius: 8px; padding: 4px 8px; }}")


class WacheAI(OfficeAI):
    """Die Kugel des Wache-Fensters: eigene schnelle Aufträge, eigener Gruß."""

    def stand(self, name: str, modell: bool, gesichter: dict | None = None) -> None:
        super().stand(name, modell, gesichter)
        self.blase.titel.setText(f"{self.name} · Wache")
        self.kugel.setToolTip(f"{self.name} – Wache-KI: Alarme erklären, prüfen, vorschlagen")

    def app_setzen(self, app: str) -> None:
        self.app = app
        lay = self.blase.chips_lay
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for zeichen, text, auftrag in AUFTRAEGE:
            k = Knopf(text, zeichen, klein=True)
            k.setToolTip(auftrag)
            k.clicked.connect(lambda _=False, a=auftrag, t=text: self.fragen(a, t))
            lay.addWidget(k)
        lay.addStretch(1)
        self.blase.chips.setVisible(True)
        self.feld.eingabe.setPlaceholderText("z. B. „Ist dieser Alarm gefährlich?“ oder „Was war heute Nacht los?“")

    def _begruessung(self) -> str:
        return ("Ich schaue mir die Alarme mit dir an – mit Belegen, nicht mit Vermutungen. Wähl links einen Alarm "
                "oder nimm einen Knopf. Ich prüfe, recherchiere und bewerte – jedes Urteil kannst du per Knopf "
                "ändern. ESC hält mich an.")

    def ausklappen(self, fokus: bool = False) -> None:
        erster = not self.offen
        super().ausklappen(fokus)
        if erster and not self.blase.isVisible() and not self.laufend:
            self.blase.zeigen(status="", text=self._begruessung())
            self.blase.show()
            self.anordnen()


class WacheFenster(QWidget):
    def __init__(self, leitung=None):
        super().__init__()
        self.leitung = leitung
        self.gewaehlt = ""
        self.alarm: dict = {}
        self.vorschlag: dict = {}
        self.setObjectName("wache")
        self.setWindowTitle("Nemi Wache")
        self.resize(1360, 860)
        self.setStyleSheet(f"QWidget#wache {{ background: {FARBE['grund']}; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        kopf = QFrame()
        kopf.setObjectName("kopf")
        kopf.setStyleSheet(f"QFrame#kopf {{ background: {FARBE['leiste']}; border-bottom: 1px solid {FARBE['rand']}; }}")
        k = QHBoxLayout(kopf)
        k.setContentsMargins(18, 10, 18, 10)
        k.addWidget(label("🛡 Wache", 16, "text", fett=True, umbruch=False))
        self.zahlen = label("", 10.5, "dim", umbruch=False)
        k.addWidget(self.zahlen)
        k.addStretch(1)
        k.addWidget(label("Wache-KI:", 10, "dim", umbruch=False))
        self.modell = QComboBox()
        self.modell.setMinimumWidth(300)
        self.modell.setStyleSheet(_feld_stil())
        self.modell.setToolTip("Eigenes Cloud-Modell für die Wache-KI – läuft neben dem Chat")
        self.modell.activated.connect(self._modell_gewaehlt)
        k.addWidget(self.modell)
        neu = Knopf("", "⟳", klein=True)
        neu.setToolTip("Alarme neu laden")
        neu.clicked.connect(self._liste_holen)
        k.addWidget(neu)
        v.addWidget(kopf)

        koerper = QHBoxLayout()
        koerper.setContentsMargins(16, 14, 16, 16)
        koerper.setSpacing(14)
        links = QFrame()
        links.setObjectName("links")
        links.setStyleSheet(_rahmen("links"))
        links.setFixedWidth(470)
        lv = QVBoxLayout(links)
        lv.setContentsMargins(12, 12, 12, 12)
        zeile = QHBoxLayout()
        self.filter = QComboBox()
        for schluessel, text in FILTER:
            self.filter.addItem(text, schluessel)
        self.filter.setStyleSheet(_feld_stil())
        self.filter.currentIndexChanged.connect(lambda _i: self._liste_holen())
        zeile.addWidget(self.filter, 1)
        lv.addLayout(zeile)
        self.suche = QLineEdit()
        self.suche.setPlaceholderText("🔍 Regel, Programm oder Pfad …")
        self.suche.setStyleSheet(_feld_stil())
        self.suche.returnPressed.connect(self._liste_holen)
        lv.addWidget(self.suche)
        self.liste = QListWidget()
        self.liste.setFont(schrift(10))
        self.liste.setStyleSheet(f"QListWidget {{ background: transparent; border: none; color: {FARBE['text']}; }}"
                                 f" QListWidget::item {{ padding: 6px 4px; border-bottom: 1px solid {FARBE['rand']}; }}"
                                 f" QListWidget::item:selected {{ background: {FARBE['flaeche_hoch']}; "
                                 f"border-radius: 8px; }}")
        self.liste.currentItemChanged.connect(self._alarm_gewaehlt)
        lv.addWidget(self.liste, 1)
        koerper.addWidget(links)

        self.rechts = QWidget()
        self.rv = QVBoxLayout(self.rechts)
        self.rv.setContentsMargins(4, 0, 4, 90)            # unten Platz für die Kugel
        self.rv.setSpacing(10)
        koerper.addWidget(rollbereich(self.rechts), 1)
        v.addLayout(koerper, 1)
        self._leer("Wähl links einen Alarm.")

        self.nemi = WacheAI(self, self._kontext)
        self.nemi.frage_senden.connect(self.senden)
        self.nemi.stopp.connect(lambda nr: self.senden({"op": "stopp", "id": nr}))
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.nemi.anhalten)

        if leitung is not None:
            leitung.ereignis.connect(self._ereignis)
            leitung.getrennt.connect(self._getrennt)
            leitung.senden({"op": "start"})

    # -- Verbindung ------------------------------------------------------------------------
    def senden(self, auftrag: dict) -> None:
        if self.leitung is None:
            self.nemi.hinweis("Nicht mit NemiCLI verbunden – im Terminal /wache fenster eingeben.")
            return
        self.leitung.senden(auftrag)

    def _kontext(self) -> dict:
        return {"app": "wache", "gewaehlt": self.gewaehlt}

    def _liste_holen(self) -> None:
        self.senden({"op": "filtern", "filter": self.filter.currentData(), "suche": self.suche.text()})

    def _modell_gewaehlt(self, _i: int) -> None:
        self.senden({"op": "modell", "ref": self.modell.currentData() or ""})

    # -- Liste -----------------------------------------------------------------------------
    def _liste_setzen(self, alarme: list[dict], zahl: dict) -> None:
        self.zahlen.setText("   ".join(f"{s} {zahl.get(k, 0)}" for k, s in (("offen", "🟠"), ("unpruefbar", "🟡"),
                                                                            ("gesehen", "⚪"), ("echt", "🔴"))))
        self.liste.blockSignals(True)
        self.liste.clear()
        wieder = None
        for a in alarme:
            mal = f" ×{a['anzahl']}" if a.get("anzahl", 1) > 1 else ""
            it = QListWidgetItem(f"{a['symbol']}  {a['zeit']} · {a['regel']}{mal}\n      {a['subjekt'] or a['titel']}")
            it.setData(Qt.ItemDataRole.UserRole, a["id"])
            it.setToolTip(f"{a['titel']} · {a['status_text']}")
            self.liste.addItem(it)
            if a["id"] == self.gewaehlt:
                wieder = it
        self.liste.blockSignals(False)
        if wieder is not None:
            self.liste.setCurrentItem(wieder)
        elif not alarme:
            self._leer("Keine Alarme in dieser Ansicht. 🎉")

    def _alarm_gewaehlt(self, it, _alt=None) -> None:
        if it is None:
            return
        self.gewaehlt = str(it.data(Qt.ItemDataRole.UserRole) or "")
        self._leer("Steckbrief wird geladen – die Signatur wird gerade geprüft …")
        self.senden({"op": "steckbrief", "id": self.gewaehlt})

    def _auswaehlen(self, aid: str) -> None:
        for i in range(self.liste.count()):
            if self.liste.item(i).data(Qt.ItemDataRole.UserRole) == aid:
                self.liste.setCurrentRow(i)
                return
        self.gewaehlt = aid                                  # nicht in dieser Ansicht: trotzdem zeigen
        self.senden({"op": "steckbrief", "id": aid})

    # -- Steckbrief ------------------------------------------------------------------------
    def _rechts_leeren(self) -> None:
        while self.rv.count():
            it = self.rv.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            elif it.layout() is not None:
                self._layout_leeren(it.layout())

    def _layout_leeren(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().hide()
                it.widget().deleteLater()
            elif it.layout() is not None:
                self._layout_leeren(it.layout())

    def _leer(self, text: str) -> None:
        self._rechts_leeren()
        self.rv.addWidget(label(text, 11, "dim"))
        self.rv.addStretch(1)

    def _steckbrief(self, a: dict) -> None:
        self.alarm = a
        self._rechts_leeren()
        self.rv.addWidget(label(f"{a.get('symbol', '')} {a.get('titel', '')}", 15, "text", fett=True))
        stand = f"{a.get('regel', '')} · {a.get('status_text', '')} · zuletzt {a.get('zeit', '')}"
        if a.get("anzahl", 1) > 1:
            stand += f" · {a['anzahl']}× (zuerst {a.get('erstmals', '')})"
        if a.get("score"):
            stand += f" · Score {a['score']}"
        self.rv.addWidget(label(stand, 10, "dim"))
        if a.get("regel_text"):
            self.rv.addWidget(label(f"📏 {a['regel_text']}", 10, "dim"))

        karte = QFrame()
        karte.setObjectName("brief")
        karte.setStyleSheet(_rahmen("brief"))
        kv = QVBoxLayout(karte)
        kv.setContentsMargins(16, 12, 16, 12)
        for titel, wert in a.get("felder") or []:
            z = label(f"<b>{titel}:</b> {self._html(wert)}", 10.5, "text")
            z.setTextFormat(Qt.TextFormat.RichText)
            z.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            kv.addWidget(z)
        if a.get("signatur"):
            farbe = ("ok" if a["signatur"].startswith("signiert:") else
                     "fehler" if a["signatur"].startswith(("VORSICHT", "NICHT signiert")) else "warn")
            kv.addWidget(label(f"🔐 {a['signatur']}", 10.5, farbe))
        for b in a.get("bekannt") or []:
            kv.addWidget(label(f"✅ bekannt: {b}", 10, "ok"))
        if a.get("einordnung"):
            kv.addWidget(label(f"📊 {a['einordnung']}", 10, "dim"))
        if a.get("urteil_von"):
            kv.addWidget(label(f"Urteil von {a['urteil_von']}: {a.get('begruendung') or '–'}", 10, "dim"))
        self.rv.addWidget(karte)
        if a.get("aufgabe"):
            self._aufgabe_zeile(a)

        if self.vorschlag.get("id") == a.get("id"):
            self.rv.addWidget(self._vorschlag_karte(self.vorschlag))

        self.grund = QLineEdit()
        self.grund.setPlaceholderText("Warum? (freiwillig – sonst „Im Wache-Fenster vom Nutzer geprüft.“)")
        self.grund.setStyleSheet(_feld_stil())
        self.rv.addWidget(self.grund)
        self.bezeichnung = QLineEdit()
        self.bezeichnung.setPlaceholderText("Was ist das? (bei Harmlos, freiwillig – z. B. „Chrome-Update von Google“)")
        self.bezeichnung.setStyleSheet(_feld_stil())
        self.rv.addWidget(self.bezeichnung)
        knoepfe = QHBoxLayout()
        for urteil, zeichen, text in URTEIL_KNOEPFE:
            if urteil == "erledigt" and a.get("status") != "echt":
                continue
            k = Knopf(text, zeichen, klein=True)
            k.clicked.connect(lambda _=False, u=urteil: self._urteilen(u))
            knoepfe.addWidget(k)
        knoepfe.addStretch(1)
        pruefen = Knopf("Neu prüfen", "🔍", klein=True)
        pruefen.setToolTip("Steckbrief und Signatur neu laden")
        pruefen.clicked.connect(lambda: self._alarm_gewaehlt(self.liste.currentItem()))
        knoepfe.addWidget(pruefen)
        if a.get("exe"):
            ordner = Knopf("Ordner", "📂", klein=True)
            ordner.setToolTip("Die Datei im Explorer zeigen")
            ordner.clicked.connect(lambda: self.senden({"op": "ordner", "id": a.get("id")}))
            knoepfe.addWidget(ordner)
        weg = Knopf("Löschen", "🗑", klein=True)
        weg.setToolTip("Diesen Eintrag aus der Wache entfernen")
        weg.clicked.connect(self._loeschen)
        knoepfe.addWidget(weg)
        self.rv.addLayout(knoepfe)
        self.meldung = label("", 10, "dim")
        self.rv.addWidget(self.meldung)
        self.rv.addStretch(1)

    @staticmethod
    def _html(text: str) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _vorschlag_karte(self, vs: dict) -> QFrame:
        karte = QFrame()
        karte.setObjectName("vorschlag")
        karte.setStyleSheet(f"QFrame#vorschlag {{ background: {FARBE['flaeche_hell']}; border: 1px solid "
                            f"{FARBE['lila']}; border-radius: 14px; }}")
        kv = QVBoxLayout(karte)
        kv.setContentsMargins(16, 10, 16, 10)
        kv.addWidget(label(f"💡 Vorschlag der Wache-KI: {URTEIL_TEXT.get(vs.get('urteil'), vs.get('urteil'))}", 11,
                           "text", fett=True))
        if vs.get("grund"):
            kv.addWidget(label(vs["grund"], 10.5, "text"))
        if vs.get("bezeichnung"):
            kv.addWidget(label(f"Was es ist: {vs['bezeichnung']}", 10, "dim"))
        h = QHBoxLayout()
        ja = Knopf("Übernehmen", "✓", klein=True)
        ja.akzent = True
        ja.clicked.connect(lambda: self._urteilen(vs.get("urteil"), vs.get("grund"), vs.get("bezeichnung"),
                                                  vs.get("id")))
        nein = Knopf("Nein", "✕", klein=True)
        nein.clicked.connect(self._vorschlag_weg)
        h.addWidget(ja)
        h.addWidget(nein)
        h.addStretch(1)
        kv.addLayout(h)
        return karte

    def _vorschlag_weg(self) -> None:
        self.vorschlag = {}
        if self.alarm:
            self._steckbrief(self.alarm)

    def _urteilen(self, urteil, grund: str | None = None, bezeichnung: str | None = None,
                  aid: str | None = None) -> None:
        aid = aid or self.alarm.get("id")
        if not aid:
            return
        if grund is None:
            grund = self.grund.text() if hasattr(self, "grund") else ""
        if bezeichnung is None:
            bezeichnung = self.bezeichnung.text() if hasattr(self, "bezeichnung") else ""
        self.vorschlag = {}
        self.senden({"op": "urteilen", "id": aid, "urteil": urteil, "grund": grund, "bezeichnung": bezeichnung})

    def _aufgabe_zeile(self, a: dict) -> None:
        """Die geplante Aufgabe des Alarms: Zustand in Windows und – solange es sie gibt – deaktivieren oder löschen."""
        zustand = a.get("aufgabe_zustand")
        text = (f"📅 Aufgabe in Windows: {a['aufgabe']} · "
                + (f"Zustand: {zustand or 'unbekannt'}" if zustand is not None else "gibt es nicht mehr"))
        self.rv.addWidget(label(text, 10.5, "text" if zustand is not None else "dim"))
        if zustand is None:
            return
        h = QHBoxLayout()
        for art, zeichen, titel in (("deaktivieren", "⏸", "Aufgabe deaktivieren"), ("loeschen", "🗑", "Aufgabe löschen")):
            if art == "deaktivieren" and "deaktiv" in str(zustand).lower():
                continue
            k = Knopf(titel, zeichen, klein=True)
            k.setToolTip("Ändert die geplante Aufgabe in Windows – vielleicht fragt Windows nach Adminrechten")
            k.clicked.connect(lambda _=False, x=art: self._aufgabe(x))
            h.addWidget(k)
        h.addStretch(1)
        self.rv.addLayout(h)

    def _aufgabe(self, art: str) -> None:
        aid, name = self.alarm.get("id"), self.alarm.get("aufgabe")
        if not aid or not name:
            return
        if art == "deaktivieren":
            frage = (f"Diese geplante Aufgabe in Windows deaktivieren?\n\n{name}\n\nSie läuft dann nicht mehr, "
                     "bleibt aber erhalten – in der Aufgabenplanung lässt sie sich wieder aktivieren.")
        else:
            frage = (f"Diese geplante Aufgabe in Windows LÖSCHEN?\n\n{name}\n\nDas lässt sich nicht rückgängig "
                     "machen. Deaktivieren wäre die vorsichtigere Wahl.")
        if QMessageBox.question(self, "📅 Geplante Aufgabe", frage) != QMessageBox.StandardButton.Yes:
            return
        if hasattr(self, "meldung"):
            self.meldung.setText("⏳ Windows wird gefragt – vielleicht musst du die Adminrechte bestätigen …")
        self.senden({"op": "aufgabe", "id": aid, "art": art})

    def _loeschen(self) -> None:
        aid = self.alarm.get("id")
        if not aid:
            return
        frage = (f"Diesen Eintrag aus der Wache löschen?\n\n{self.alarm.get('titel', '')}\n"
                 f"{self.alarm.get('regel', '')} · {self.alarm.get('status_text', '')}\n\n"
                 "Das lässt sich nicht rückgängig machen.")
        if QMessageBox.question(self, "🗑 Eintrag löschen", frage) == QMessageBox.StandardButton.Yes:
            self.vorschlag = {}
            self.senden({"op": "loeschen", "id": aid})

    # -- Ereignisse der Sitzung ------------------------------------------------------------
    def _ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "stand":
            self.nemi.stand(str(ev.get("name") or ""), True, ev.get("gesichter"))
        elif t == "modell":
            self.modell.blockSignals(True)
            self.modell.clear()
            aktiv = str(ev.get("aktiv") or "")
            self.modell.addItem(f"Wie die Wache ({aktiv or 'kein Cloud-Modell'})", "")
            for ref in ev.get("auswahl") or []:
                self.modell.addItem(ref, ref)
            i = self.modell.findData(str(ev.get("gewaehlt") or ""))
            self.modell.setCurrentIndex(max(i, 0))
            self.modell.blockSignals(False)
        elif t == "liste":
            self._liste_setzen(ev.get("alarme") or [], ev.get("zahl") or {})
        elif t == "steckbrief" and isinstance(ev.get("alarm"), dict):
            if ev["alarm"].get("id") == self.gewaehlt:
                self._steckbrief(ev["alarm"])
        elif t == "geurteilt":
            if ev.get("von_ki"):
                if self.gewaehlt and self.gewaehlt in str(ev.get("text") or "") + str(ev.get("id") or ""):
                    self.senden({"op": "steckbrief", "id": self.gewaehlt})     # neuer Stand mit dem Urteil der KI
                return
            if hasattr(self, "meldung"):
                self.meldung.setText(("✅ " if ev.get("ok") else "⚠ ") + str(ev.get("text") or "")[:400])
        elif t == "geloescht":
            if ev.get("ok") and ev.get("id") == self.gewaehlt:
                self.gewaehlt, self.alarm = "", {}
                self._leer("🗑 " + str(ev.get("text") or "Eintrag gelöscht."))
            elif hasattr(self, "meldung") and self.alarm:
                self.meldung.setText("⚠ " + str(ev.get("text") or ""))
        elif t == "zeigen":
            self._auswaehlen(str(ev.get("id") or ""))
        elif t == "vorschlag":
            self.vorschlag = {k: ev.get(k) for k in ("id", "urteil", "grund", "bezeichnung")}
            if self.alarm.get("id") == self.vorschlag["id"]:
                self._steckbrief(self.alarm)
            else:
                self._auswaehlen(str(self.vorschlag["id"]))
        elif t == "status":
            self.nemi.status(ev.get("id"), str(ev.get("text") or ""))
        elif t == "antwort":
            self.nemi.antwort(ev.get("id"), str(ev.get("text") or ""))
        elif t == "fehler":
            self.nemi.fehler(ev.get("id"), str(ev.get("text") or ""))
        elif t == "hinweis":
            if hasattr(self, "meldung") and self.alarm:
                self.meldung.setText("⚠ " + str(ev.get("text") or ""))
            else:
                self.nemi.hinweis(str(ev.get("text") or ""))
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _getrennt(self) -> None:
        self.leitung = None
        self.nemi.fehler(self.nemi.laufend, "Verbindung zur Sitzung getrennt – im Terminal /wache fenster neu öffnen.")

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
    app = app_vorbereiten("NemiCLI.Wache")
    fenster = WacheFenster(_leitung())
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
