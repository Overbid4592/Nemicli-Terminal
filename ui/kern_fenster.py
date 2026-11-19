"""kern_fenster.py – Kern-Fenster (/kern → ändern), eigener Prozess an der laufenden Sitzung.

Zeigt den ganzen Kern-Stand der aktiven Persönlichkeit als JSON (kern, sprechweise, gesperrt, vorher),
direkt bearbeitbar. „Speichern“ schickt den Text an die Sitzung; dort wird er geprüft und mit Sicherung
geschrieben. Keine KI dazwischen. Leitung wie gui_fenster.
"""
from __future__ import annotations

import json

from gui_fenster import (
    FARBE,
    Knopf,
    _dunkler_rahmen,
    app_vorbereiten,
    farbe,
    label,
    leitung_oeffnen,
)
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

STIL = f"""
QWidget#kern {{ background: {FARBE['grund']}; }}
QDialog {{ background: {FARBE['grund']}; }}
QPlainTextEdit {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 8px; selection-background-color: {FARBE['lila']}; }}
QPlainTextEdit:focus {{ border: 1px solid {FARBE['cyan']}; }}
"""

HILFE = ("„kern“: das feste Aussehen, englisch – Gesicht, Augen, Haare, Haut, Körperbau, Bleibendes wie Tattoos. "
         "Keine Kleidung und keine Frisur.  ·  „sprechweise“: wie die Persönlichkeit redet.  ·  "
         "„gesperrt“: true = eine KI ändert nur nach deinem Ja.  ·  „vorher“: frühere Kerne für /kern zurueck.")


def ja_nein(eltern: QWidget, frage: str, ja: str, nein: str) -> bool:
    """Kleine dunkle Rückfrage; True, wenn „ja“ gewählt wurde."""
    d = QDialog(eltern)
    d.setWindowTitle("NemiCLI – Kern")
    d.setStyleSheet(STIL)
    v = QVBoxLayout(d)
    v.setContentsMargins(24, 20, 24, 20)
    v.setSpacing(14)
    v.addWidget(label(frage, 10.5))
    h = QHBoxLayout()
    h.addStretch(1)
    k_nein = Knopf(nein, mitte=True)
    k_nein.clicked.connect(d.reject)
    k_ja = Knopf(ja, mitte=True)
    k_ja.akzent = True
    k_ja.clicked.connect(d.accept)
    h.addWidget(k_nein)
    h.addWidget(k_ja)
    v.addLayout(h)
    _dunkler_rahmen(d)
    return d.exec() == QDialog.DialogCode.Accepted


class KernFenster(QWidget):
    def __init__(self, leitung):
        super().__init__()
        self.leitung = leitung
        self.setObjectName("kern")
        self.setWindowTitle("NemiCLI – Kern")
        self.setStyleSheet(STIL)
        self.resize(780, 640)
        self.name = ""
        self.basis: dict | None = None       # Stand beim Laden: schützt vor Überschreiben fremder Änderungen
        self.geladen_text = ""
        self.verbunden = True

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        self.kopf = label("🧬 Kern", 17, "text", fett=True)
        v.addWidget(self.kopf)
        self.sperre = label("", 9.5, "dim")
        v.addWidget(self.sperre)
        v.addWidget(label(HILFE, 9.5, "dim"))

        self.text = QPlainTextEdit()
        schrift = QFont("Cascadia Mono")
        schrift.setStyleHint(QFont.StyleHint.Monospace)
        schrift.setPointSizeF(10.5)
        self.text.setFont(schrift)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text.setTabChangesFocus(False)
        self.text.textChanged.connect(self._geaendert)
        v.addWidget(self.text, 1)

        self.status = label("Lade den Kern …", 10, "dim")
        v.addWidget(self.status)
        h = QHBoxLayout()
        h.setSpacing(8)
        self.laden_k = Knopf("Neu laden", "↻")
        self.laden_k.clicked.connect(self.neu_laden)
        self.speichern_k = Knopf("Speichern", "💾")
        self.speichern_k.akzent = True
        self.speichern_k.clicked.connect(lambda: self.speichern(False))
        h.addWidget(self.laden_k)
        h.addStretch(1)
        h.addWidget(self.speichern_k)
        v.addLayout(h)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=lambda: self.speichern(False))

        leitung.ereignis.connect(self._ereignis)
        leitung.getrennt.connect(self._getrennt)
        leitung.senden({"op": "laden"})

    # -- Aufträge an die Sitzung ---------------------------------------------------
    def ungespeichert(self) -> bool:
        return self.basis is not None and self.text.toPlainText() != self.geladen_text

    def neu_laden(self):
        if self.ungespeichert() and not ja_nein(self, "Deine Änderungen sind noch nicht gespeichert. Verwerfen und "
                                                      "neu laden?", "Verwerfen", "Abbrechen"):
            return
        self._melden("Lade den Kern …", "dim")
        self.leitung.senden({"op": "laden"})

    def speichern(self, trotzdem: bool):
        if not self.verbunden or self.basis is None:
            return
        roh = self.text.toPlainText()
        try:
            json.loads(roh)                    # Tippfehler gleich hier zeigen; die Sitzung prüft alles
        except ValueError as e:
            self._melden(f"⚠ Kein gültiges JSON (Zeile {getattr(e, 'lineno', '?')}): {getattr(e, 'msg', e)}",
                         "fehler")
            return
        self._melden("💾 speichere …", "dim")
        self.leitung.senden({"op": "speichern", "json": roh, "basis": self.basis, "name": self.name,
                             "trotzdem": trotzdem})

    # -- Ereignisse der Sitzung ------------------------------------------------------
    def _ereignis(self, ev: dict):
        t = ev.get("t")
        if t in ("stand", "gespeichert"):
            self.name = str(ev.get("name") or "")
            self.basis = ev.get("basis") if isinstance(ev.get("basis"), dict) else {}
            self.geladen_text = str(ev.get("json") or "")
            self.text.setPlainText(self.geladen_text)
            self.kopf.setText(f"🧬 Kern von {self.name}" if self.name else "🧬 Kern")
            self.sperre.setText("🔒 gesperrt – eine KI ändert Kern und Sprechweise nur nach deinem Ja."
                                if self.basis.get("gesperrt") else "🔓 offen – „gesperrt“: true schützt den Kern.")
            self._melden("✅ Gespeichert – der alte Stand ist gesichert (/kern zurueck)." if t == "gespeichert"
                         else "Bearbeiten, dann „Speichern“ (Strg+S).", "ok" if t == "gespeichert" else "dim")
        elif t == "kleidung":
            woerter = ", ".join(str(w) for w in ev.get("woerter") or [])
            if ja_nein(self, f"Kleidung im Kern erkannt ({woerter}) – die gehört eigentlich in die Outfits. "
                             "Trotzdem so speichern?", "Ja, so speichern", "Nein, nochmal"):
                self.speichern(True)
            else:
                self._melden("Nichts gespeichert.", "dim")
        elif t == "fehler":
            self._melden("⚠ " + str(ev.get("text") or ""), "fehler")
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()
            if ev.get("name") and ev.get("name") != self.name:
                if self.ungespeichert():
                    self._melden(f"Im Terminal ist jetzt {ev['name']} aktiv – „Neu laden“ holt diesen Kern.", "warn")
                else:
                    self.leitung.senden({"op": "laden"})

    # -- Anzeige ---------------------------------------------------------------------
    def _geaendert(self):
        if self.ungespeichert():
            self.setWindowTitle("NemiCLI – Kern ●")
        else:
            self.setWindowTitle("NemiCLI – Kern")

    def _getrennt(self):
        self.verbunden = False
        self.speichern_k.setEnabled(False)
        self.laden_k.setEnabled(False)
        self._melden("Verbindung zur Sitzung getrennt – im Terminal /kern neu öffnen.", "fehler")

    def _melden(self, text: str, farbname: str):
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(self.status.foregroundRole(), farbe(farbname))
        self.status.setPalette(pal)

    def closeEvent(self, ev):
        if self.verbunden and self.ungespeichert() and not ja_nein(
                self, "Deine Änderungen sind noch nicht gespeichert. Fenster trotzdem schließen?",
                "Schließen", "Abbrechen"):
            ev.ignore()
            return
        ev.accept()


def starten() -> int:
    app = app_vorbereiten("NemiCLI.Kern")
    leitung = leitung_oeffnen("/kern")
    if leitung is None:
        return 1
    fenster = KernFenster(leitung)
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
