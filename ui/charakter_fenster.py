"""charakter_fenster.py – Charakter-Galerie (/charaktere), eigener Prozess an der laufenden Sitzung.

Seiten: Galerie (9 Vorlagen) · Person (Geschlecht, Alter ab 18) · Aussehen (Haare, Augen, Körper, Körperform) ·
Wesen (bis zu 8 Eigenschaften, eigene Wünsche) · Varianten der KI mit Porträt · Ansehen & Verbessern (✨ = die KI
hilft beim Feld, 🎨 = Krea malt neu) · Name (6 Vorschläge der KI oder eigener) · Übernehmen.
Zwei Wege: ⚡ Schnell (die Seiten oben, mit Schritt-Leiste, Steckbrief und 💬 Gespräch – die KI stellt dieselben
Felder ein, alles bleibt per Klick änderbar) und 💬 Frei mit der KI (großes Gespräch, Steckbrief in eigenen Worten,
frei änderbar). ↺ nimmt die letzte Änderung der KI zurück.
Die Arbeit (KI, Krea, Speichern ins Profil) macht die Sitzung (main_dienste.start_charaktere).
"""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QIntValidator, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

import charaktergalerie as CG
from gui_fenster import FARBE, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label, leitung_oeffnen, rollbereich

STIL = f"""
QWidget#charaktere {{ background: {FARBE['grund']}; }}
QFrame#karte {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; border-radius: 12px; }}
QFrame#karte:hover {{ border: 1px solid {FARBE['lila']}; }}
QFrame#tafel {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; border-radius: 14px; }}
QPlainTextEdit, QLineEdit, QComboBox {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px; selection-background-color: {FARBE['lila']}; }}
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {{ border: 1px solid {FARBE['cyan']}; }}
QComboBox QAbstractItemView {{ background: {FARBE['flaeche']}; color: {FARBE['text']};
    selection-background-color: {FARBE['lila']}; }}
"""
BILD = 190                                  # Kantenlänge der Porträts in den Karten
GROSS = 340                                 # Porträt auf der Seite „Ansehen“
TUPFER = 26                                 # Farbtupfer auf der Seite „Aussehen“
RECHTS = 330                                # Breite von Steckbrief und Gespräch
EINBLENDEN_MS = 180
GESCHLECHT_KNOEPFE = (("w", "♀", "Weiblich"), ("m", "♂", "Männlich"), ("n", "⚧", "Nichtbinär"))
PRONOMEN = {"w": "sie", "m": "er", "n": "die Person", "": "dein Charakter"}
SCHRITTE = ("Person", "Aussehen", "Wesen", "Varianten", "Name")
FELD_TITEL = {"geschlecht": "Geschlecht", "alter": "Alter", "eigenschaften": "Eigenschaften", "idee": "Wünsche",
              "haar_laenge": "Haare", "haar_art": "Haare", "haar_farbe": "Haarfarbe", "augen_farbe": "Augen",
              "busen_groesse": "Busen", "busen_form": "Busen", "brust": "Brust", "taille": "Taille",
              "po_groesse": "Po", "po_form": "Po", "po_spannung": "Po", "koerperform": "Körperform"}


def _bild_setzen(ziel: QLabel, pfad: str, kante: int) -> None:
    pix = QPixmap(pfad) if pfad and Path(pfad).is_file() else QPixmap()
    if pix.isNull():
        ziel.setPixmap(QPixmap())
        ziel.setText("🎨\nwird gemalt …" if pfad != "-" else "🖼")
        return
    pix = pix.scaled(kante, kante, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    x, y = max(0, (pix.width() - kante) // 2), max(0, (pix.height() - kante) // 2)
    ziel.setPixmap(pix.copy(x, y, kante, kante))


def _bildflaeche(kante: int) -> QLabel:
    lb = QLabel("🎨\nwird gemalt …")
    lb.setFixedSize(kante, kante)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setStyleSheet(f"background: {FARBE['flaeche_hell']}; border-radius: 10px; color: {FARBE['dim']};")
    return lb


def _wahl(werte: dict) -> QComboBox:
    """Auswahlliste mit „egal“ vorn; der Wert ist der deutsche Schlüssel."""
    box = QComboBox()
    box.addItem("– egal –", "")
    for k in werte:
        box.addItem(k, k)
    return box


def _tafel() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("tafel")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 12, 14, 12)
    v.setSpacing(8)
    return f, v


class Karte(QFrame):
    """Ein Charakter in der Galerie: Porträt, Name, Kurzbeschreibung, „Ansehen“; leuchtet unter der Maus."""

    def __init__(self, entwurf: dict, bei_klick):
        super().__init__()
        self.setObjectName("karte")
        self.entwurf, self.bild = dict(entwurf), str(entwurf.get("bild") or "")
        self.glanz = QGraphicsDropShadowEffect(self)
        self.glanz.setBlurRadius(34)
        self.glanz.setOffset(0, 0)
        self.glanz.setColor(QColor(FARBE["lila"]))
        self.glanz.setEnabled(False)
        self.setGraphicsEffect(self.glanz)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        self.flaeche = _bildflaeche(BILD)
        v.addWidget(self.flaeche, 0, Qt.AlignmentFlag.AlignHCenter)
        _bild_setzen(self.flaeche, self.bild, BILD)
        v.addWidget(label(entwurf.get("name", "?"), 12, "text", fett=True))
        kurz = label(entwurf.get("kurz", ""), 9.5, "dim")
        kurz.setFixedWidth(BILD)
        v.addWidget(kurz)
        k = Knopf("Ansehen", "👁", klein=True)
        k.clicked.connect(lambda: bei_klick(dict(self.entwurf), self.bild))
        v.addWidget(k)

    def enterEvent(self, ev):
        self.glanz.setEnabled(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.glanz.setEnabled(False)
        super().leaveEvent(ev)

    def bild_setzen(self, pfad: str) -> None:
        self.bild = pfad
        _bild_setzen(self.flaeche, pfad, BILD)


class Farbwahl(QWidget):
    """Farbtupfer aus der Tabelle + 🎨 eigene Farbe; noch einmal auf den gewählten Tupfer = egal."""

    def __init__(self, tabelle: list[tuple[str, str, str]], bei_aenderung=None):
        super().__init__()
        self.tabelle, self.bei_aenderung, self.hex = tabelle, bei_aenderung, ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        raster = QGridLayout()
        raster.setSpacing(6)
        self.tupfer: dict[str, QPushButton] = {}
        for i, (de, h, _) in enumerate(tabelle):
            t = QPushButton()
            t.setFixedSize(TUPFER, TUPFER)
            t.setToolTip(de)
            t.setCursor(Qt.CursorShape.PointingHandCursor)
            t.clicked.connect(lambda _=False, x=h: self.setzen("" if self.hex == x else x))
            raster.addWidget(t, i // 9, i % 9)
            self.tupfer[h] = t
        v.addLayout(raster)
        h = QHBoxLayout()
        eigene = Knopf("Eigene Farbe", "🎨", klein=True)
        eigene.clicked.connect(self._eigene)
        h.addWidget(eigene)
        self.anzeige = label("egal", 10, "dim", umbruch=False)
        h.addWidget(self.anzeige)
        h.addStretch(1)
        v.addLayout(h)
        self._zeichnen()

    def _eigene(self) -> None:
        c = QColorDialog.getColor(QColor(self.hex or "#888888"), self, "Eigene Farbe")
        if c.isValid():
            self.setzen(c.name())

    def setzen(self, hexwert: str, melden: bool = True) -> None:
        self.hex = (hexwert or "").lower()
        self._zeichnen()
        if melden and self.bei_aenderung:
            self.bei_aenderung()

    def _zeichnen(self) -> None:
        for h, t in self.tupfer.items():
            rand = f"3px solid {FARBE['cyan']}" if h == self.hex else f"1px solid {FARBE['rand']}"
            t.setStyleSheet(f"QPushButton {{ background: {h}; border: {rand}; border-radius: {TUPFER // 2}px; }}")
        text = CG.farbe_anzeige(self.hex, self.tabelle)
        self.anzeige.setText(text if not self.hex or self.hex in self.tupfer else f"{text} · {self.hex}")


def _blase_in(layout: QVBoxLayout, rolle, breite: int, text: str, von_ki: bool, zusatz: str = "") -> None:
    """Sprechblase ins Gespräch: die KI links, die Person rechts; danach nach unten rollen."""
    lb = QLabel(text + (f"\n{zusatz}" if zusatz else ""))
    lb.setWordWrap(True)
    lb.setTextFormat(Qt.TextFormat.PlainText)
    lb.setMaximumWidth(breite)
    lb.setStyleSheet(f"background: {FARBE['flaeche_hell'] if von_ki else FARBE['blase_a']}; "
                     f"color: {FARBE['text']}; border-radius: 10px; padding: 8px; font-size: 10.5pt;")
    zeile = QHBoxLayout()
    if not von_ki:
        zeile.addStretch(1)
    zeile.addWidget(lb)
    if von_ki:
        zeile.addStretch(1)
    layout.insertLayout(layout.count() - 1, zeile)
    leiste = rolle.verticalScrollBar()
    QTimer.singleShot(30, leiste, lambda: leiste.setValue(leiste.maximum()))   # entfällt mit dem Fenster


def _raster() -> tuple[QWidget, QGridLayout]:
    w = QWidget()
    g = QGridLayout(w)
    g.setSpacing(14)
    g.setContentsMargins(0, 0, 0, 0)
    return w, g


def _zeile(titel: str, *felder: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    kopf = label(titel, 10, "dim", umbruch=False)
    kopf.setFixedWidth(70)
    h.addWidget(kopf)
    for f in felder:
        h.addWidget(f)
    h.addStretch(1)
    return w


class CharakterFenster(QWidget):
    def __init__(self, leitung):
        super().__init__()
        self.leitung = leitung
        self.setObjectName("charaktere")
        self.setWindowTitle("NemiCLI – Charakter-Galerie")
        self.setStyleSheet(STIL)
        self.resize(1220, 940)
        self.vorlagen: dict[str, Karte] = {}
        self.vorschlag_karten: list[Karte] = []
        self.vorschlag_nr = 0
        self.kategorien: dict[str, list[str]] = {}
        self.max_eig = 8
        self.alter_min, self.alter_max = CG.ALTER_MIN, CG.ALTER_MAX
        self.chips: dict[str, Knopf] = {}
        self.auswahl = {"geschlecht": "", "alter": 25, "aussehen": {}, "eigenschaften": [], "idee": ""}
        self.entwurf: dict = {}
        self.art = "vorlage"                     # vorlage | vorschlag | bestehend
        self.im_ablauf = False                   # beim Erschaffen: Schritt-Leiste, Steckbrief, Gespräch
        self.bild = ""
        self.laeuft = False
        self.form_fest = ""
        self.aussehen_g, self.aussehen_modus, self.aussehen_geaendert = "n", "neu", False
        self.namen_nr, self.namen_bisher, self.namen = 0, [], []
        self.verlauf: list[dict] = []
        self.editor_nr = 0
        self.vor_ki: dict | None = None          # Stand vor der letzten Änderung der KI (↺)
        self.weg = "schnell"                     # schnell (Klicken) | frei (in eigenen Worten mit der KI)
        self.frei_verlauf: list[dict] = []
        self.frei_nr = 0
        self.frei_vor: dict | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        self.kopf = label("🎭 Charakter-Galerie", 17, "text", fett=True)
        v.addWidget(self.kopf)
        self.leiste = self._schritt_leiste()
        v.addWidget(self.leiste)
        self.status = label("", 10, "dim")
        v.addWidget(self.status)
        mitte = QHBoxLayout()
        mitte.setSpacing(18)
        self.stapel = QStackedWidget()
        mitte.addWidget(self.stapel, 1)
        self.rechts = self._spalte_rechts()
        mitte.addWidget(self.rechts)
        v.addLayout(mitte, 1)
        self.seite_start = self._seite_start()
        self.seite_person = self._seite_person()
        self.seite_aussehen = self._seite_aussehen()
        self.seite_eig = self._seite_eigenschaften()
        self.seite_vorschlaege = self._seite_vorschlaege()
        self.seite_ansehen = self._seite_ansehen()
        self.seite_namen = self._seite_namen()
        self.seite_frei = self._seite_frei()
        for s in (self.seite_start, self.seite_person, self.seite_aussehen, self.seite_eig,
                  self.seite_vorschlaege, self.seite_ansehen, self.seite_namen, self.seite_frei):
            self.stapel.addWidget(s)
        self._rahmen_auffrischen()

        leitung.ereignis.connect(self._ereignis)
        leitung.getrennt.connect(lambda: self._melden("Verbindung zur Sitzung getrennt – /charaktere neu öffnen.",
                                                      "fehler", False))
        leitung.senden({"op": "start"})

    # -- Rahmen: Schritt-Leiste, Steckbrief, Gespräch, Übergänge -----------------------
    def _schritt_leiste(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 4)
        self.schritt_knoepfe: list[Knopf] = []
        for i, titel in enumerate(SCHRITTE):
            if i:
                h.addWidget(label("›", 12, "blass", umbruch=False))
            k = Knopf(titel, str(i + 1), klein=True)
            k.clicked.connect(lambda _=False, x=i: self._schritt(x))
            h.addWidget(k)
            self.schritt_knoepfe.append(k)
        h.addStretch(1)
        return w

    def _spalte_rechts(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(RECHTS)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        brief, bv = _tafel()
        bv.addWidget(label("📋 Steckbrief", 12, "text", fett=True))
        self.steckbrief = QLabel()
        self.steckbrief.setTextFormat(Qt.TextFormat.RichText)
        self.steckbrief.setWordWrap(True)
        self.steckbrief.setStyleSheet(f"color: {FARBE['text']}; font-size: 10pt; line-height: 140%;")
        bv.addWidget(self.steckbrief)
        self.chat_k = Knopf("Mit der KI reden", "💬", klein=True)
        self.chat_k.setToolTip("Erzähl, wie du dir den Charakter vorstellst – die KI stellt die Felder ein")
        self.chat_k.clicked.connect(self._chat_umschalten)
        bv.addWidget(self.chat_k)
        v.addWidget(brief)

        self.chat_box, cv = _tafel()
        kopf = QHBoxLayout()
        kopf.addWidget(label("💬 Gespräch", 11, "text", fett=True, umbruch=False))
        kopf.addStretch(1)
        self.rueck_k = Knopf("", "↺", klein=True)
        self.rueck_k.setToolTip("Letzte Änderung der KI zurücknehmen")
        self.rueck_k.setEnabled(False)
        self.rueck_k.clicked.connect(self._ki_zuruecknehmen)
        kopf.addWidget(self.rueck_k)
        cv.addLayout(kopf)
        self.chat_inhalt = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_inhalt)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch(1)
        self.chat_rolle = rollbereich(self.chat_inhalt)
        cv.addWidget(self.chat_rolle, 1)
        eingabe = QHBoxLayout()
        self.chat_feld = QLineEdit()
        self.chat_feld.setPlaceholderText("Erzähl, wie du sie dir vorstellst …")
        self.chat_feld.returnPressed.connect(self._chat_senden)
        eingabe.addWidget(self.chat_feld, 1)
        self.senden_k = Knopf("", "➤", klein=True)
        self.senden_k.clicked.connect(self._chat_senden)
        eingabe.addWidget(self.senden_k)
        cv.addLayout(eingabe)
        self.chat_box.setVisible(False)
        v.addWidget(self.chat_box, 100)
        v.addStretch(1)
        return w

    def _zeigen(self, seite: QWidget) -> None:
        if seite is self.seite_start:
            self.im_ablauf = False
        if self.stapel.currentWidget() is not seite:
            self.stapel.setCurrentWidget(seite)
            if seite not in (self.seite_start, self.seite_vorschlaege):   # Karten haben eigene Effekte (Glanz)
                self._einblenden(seite)
        self._rahmen_auffrischen()

    @staticmethod
    def _einblenden(w: QWidget) -> None:
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", w)
        anim.setDuration(EINBLENDEN_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: w.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _schritt_jetzt(self) -> int | None:
        seite = self.stapel.currentWidget()
        if (not self.im_ablauf or self.weg == "frei"
                or (seite is self.seite_aussehen and self.aussehen_modus != "neu")):
            return None
        return {self.seite_person: 0, self.seite_aussehen: 1, self.seite_eig: 2, self.seite_vorschlaege: 3,
                self.seite_ansehen: 3, self.seite_namen: 4}.get(seite)

    def _rahmen_auffrischen(self) -> None:
        jetzt = self._schritt_jetzt()
        self.leiste.setVisible(jetzt is not None)
        self.rechts.setVisible(jetzt is not None and jetzt <= 3 and self.stapel.currentWidget() is not
                               self.seite_ansehen)
        offen = [True, self._person_ok(), self._person_ok(), bool(self.vorschlag_karten),
                 self.art == "vorschlag" and bool(self.entwurf)]
        for i, k in enumerate(self.schritt_knoepfe):
            k.akzent = i == jetzt
            k.symbol = "✓" if jetzt is not None and i < jetzt else str(i + 1)
            k.setEnabled(offen[i])
            k.update()
        self._steckbrief_auffrischen()

    def _schritt(self, i: int) -> None:
        if i == 0:
            self._zeigen(self.seite_person)
        elif i == 1:
            self._person_weiter()
        elif i == 2 and self._person_pruefen():
            self._zaehlen()
            self._zeigen(self.seite_eig)
        elif i == 3 and self.vorschlag_karten:
            self._zeigen(self.seite_vorschlaege)
        elif i == 4 and self.art == "vorschlag" and self.entwurf:
            self._namen_oeffnen()

    def _steckbrief_auffrischen(self) -> None:
        s, a = self.auswahl, self.auswahl.get("aussehen") or {}

        def farbe_html(hexwert: str, tabelle) -> str:
            return (f"<span style='color:{hexwert}'>●</span> {html.escape(CG.farbe_anzeige(hexwert, tabelle))}"
                    if hexwert else "")

        g = {"w": "♀ Weiblich", "m": "♂ Männlich", "n": "⚧ Nichtbinär"}.get(s.get("geschlecht", ""), "–")
        alter = self._alter_wert()
        zeilen = [f"<b>{g}</b>" + (f" · {alter} Jahre" if alter else "")]
        haar = " ".join(x for x in (a.get("haar_laenge", ""), a.get("haar_art", ""),
                                    farbe_html(a.get("haar_farbe", ""), CG.HAAR_FARBEN)) if x)
        zeilen.append("💇 " + (haar or "<i>egal</i>"))
        zeilen.append("👁 " + (farbe_html(a.get("augen_farbe", ""), CG.AUGEN_FARBEN) or "<i>egal</i>"))
        koerper = [f"{titel} {' '.join(a[f] for f in felder if a.get(f))}"
                   for titel, felder in (("Busen", ("busen_groesse", "busen_form")), ("Brust", ("brust",)),
                                         ("Taille", ("taille",)), ("Po", ("po_groesse", "po_form", "po_spannung")))
                   if any(a.get(f) for f in felder)]
        zeilen.append("🧍 " + (html.escape(" · ".join(koerper)) or "<i>egal</i>"))
        if a.get("koerperform"):
            zeilen.append(f"Körperform: <b>{html.escape(a['koerperform'])}</b>")
        zeilen.append("✨ " + (html.escape(", ".join(s.get("eigenschaften") or [])) or "<i>noch keine</i>"))
        if s.get("idee"):
            zeilen.append("💭 " + html.escape(s["idee"]))
        self.steckbrief.setText("<br>".join(zeilen))

    # -- Gespräch mit der KI ----------------------------------------------------------------
    def _chat_umschalten(self) -> None:
        an = not self.chat_box.isVisible()
        self.chat_box.setVisible(an)
        self.chat_k.setText("Gespräch schließen" if an else "Mit der KI reden")
        if an:
            self.chat_feld.setFocus()

    def _blase(self, text: str, von_ki: bool, zusatz: str = "") -> None:
        _blase_in(self.chat_layout, self.chat_rolle, RECHTS - 70, text, von_ki, zusatz)

    def _stand(self) -> dict:
        s = self.auswahl
        return {"geschlecht": s.get("geschlecht") or "", "alter": self._alter_wert() or None,
                "aussehen": dict(s.get("aussehen") or {}), "eigenschaften": list(s.get("eigenschaften") or []),
                "idee": s.get("idee") or ""}

    def _chat_senden(self) -> None:
        text = self.chat_feld.text().strip()
        if not text:
            return
        self._blase(text, False)
        self.editor_nr += 1
        self.leitung.senden({"op": "editor", "text": text, "verlauf": self.verlauf[-16:], "stand": self._stand(),
                             "nr": self.editor_nr})
        self.verlauf.append({"wer": "du", "text": text})
        self.chat_feld.clear()

    def _ki_antwort(self, ev: dict) -> None:
        if ev.get("nr") not in (None, self.editor_nr):
            return
        antwort = str(ev.get("antwort") or "")
        aend = ev.get("aenderungen") if isinstance(ev.get("aenderungen"), dict) else {}
        felder = list(dict.fromkeys(FELD_TITEL.get(k, k) for k in
                                    [*(k for k in aend if k != "aussehen"), *(aend.get("aussehen") or {})]))
        self._blase(antwort, True, ("✏ " + ", ".join(felder)) if felder else "")
        self.verlauf.append({"wer": "ki", "text": antwort})
        if aend:
            self.vor_ki = self._stand()
            self._stand_setzen(CG.stand_anwenden(self._stand(), aend))
            self.rueck_k.setEnabled(True)

    def _ki_zuruecknehmen(self) -> None:
        if self.vor_ki is not None:
            self._stand_setzen(self.vor_ki)
            self.vor_ki = None
            self.rueck_k.setEnabled(False)
            self._melden("↺ Letzte Änderung der KI zurückgenommen.", "dim", False)

    def _stand_setzen(self, stand: dict) -> None:
        """Einen Stand auf alle Seiten übertragen – danach bleibt jedes Feld per Klick frei änderbar."""
        g = stand.get("geschlecht") or ""
        self.auswahl.update(geschlecht=g, aussehen=dict(stand.get("aussehen") or {}),
                            eigenschaften=list(stand.get("eigenschaften") or [])[:self.max_eig],
                            idee=str(stand.get("idee") or ""))
        self.alter.setText(str(stand["alter"]) if stand.get("alter") else "")
        self._geschlecht_zeigen()
        if self.aussehen_modus == "neu":
            self._aussehen_vorbereiten(g or "n")
            self._aussehen_setzen(self.auswahl["aussehen"])
        for e, k in self.chips.items():
            an = e in self.auswahl["eigenschaften"]
            k.blockSignals(True)
            k.setChecked(an)
            k.blockSignals(False)
            k.akzent = an
            k.update()
        self.idee_feld.blockSignals(True)
        self.idee_feld.setText(self.auswahl["idee"])
        self.idee_feld.blockSignals(False)
        self._zaehlen()
        self._rahmen_auffrischen()

    # -- Freier Weg: in eigenen Worten mit der KI -------------------------------------------
    def _frei_starten(self) -> None:
        self.weg, self.art, self.entwurf, self.im_ablauf = "frei", "vorschlag", {}, True
        self._zeigen(self.seite_frei)
        self.frei_feld.setFocus()

    def _frei_stand(self) -> dict:
        try:
            alter = int(self.frei_alter.text().strip())
        except ValueError:
            alter = None
        return {"geschlecht": self.frei_geschlecht.currentData() or "", "alter": alter,
                **{k: w.toPlainText().strip() for k, w in self.frei_felder.items()}}

    def _frei_setzen(self, stand: dict) -> None:
        self.frei_geschlecht.setCurrentIndex(max(0, self.frei_geschlecht.findData(stand.get("geschlecht") or "")))
        self.frei_alter.setText(str(stand["alter"]) if stand.get("alter") else "")
        for k, w in self.frei_felder.items():
            if w.toPlainText() != (stand.get(k) or ""):
                w.setPlainText(stand.get(k) or "")

    def _frei_senden(self) -> None:
        text = self.frei_feld.text().strip()
        if not text:
            return
        _blase_in(self.frei_layout, self.frei_rolle, 520, text, False)
        self.frei_nr += 1
        self.leitung.senden({"op": "frei", "text": text, "verlauf": self.frei_verlauf[-16:],
                             "stand": self._frei_stand(), "nr": self.frei_nr})
        self.frei_verlauf.append({"wer": "du", "text": text})
        self.frei_feld.clear()

    def _frei_antwort(self, ev: dict) -> None:
        if ev.get("nr") not in (None, self.frei_nr):
            return
        antwort = str(ev.get("antwort") or "")
        aend = ev.get("aenderungen") if isinstance(ev.get("aenderungen"), dict) else {}
        titel = {**{k: v[0].split(" ", 1)[1] for k, v in CG.STECKBRIEF.items()}, "geschlecht": "Geschlecht",
                 "alter": "Alter"}
        geaendert = [titel[k] for k in titel if k in aend]
        _blase_in(self.frei_layout, self.frei_rolle, 520, antwort, True,
                  ("✏ " + ", ".join(geaendert)) if geaendert else "")
        self.frei_verlauf.append({"wer": "ki", "text": antwort})
        if aend:
            self.frei_vor = self._frei_stand()
            self._frei_setzen({**self.frei_vor, **aend})
            self.frei_rueck_k.setEnabled(True)

    def _frei_zuruecknehmen(self) -> None:
        if self.frei_vor is not None:
            self._frei_setzen(self.frei_vor)
            self.frei_vor = None
            self.frei_rueck_k.setEnabled(False)
            self._melden("↺ Letzte Änderung der KI zurückgenommen.", "dim", False)

    # -- Seiten ------------------------------------------------------------------------
    def _seite_start(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(label("Fertige Persönlichkeiten – anklicken, ansehen, übernehmen. Oder erschaffe deine eigene.",
                          10, "dim"))
        self.vorlagen_raster_w, self.vorlagen_raster = _raster()
        v.addWidget(rollbereich(self.vorlagen_raster_w), 1)
        unten = QHBoxLayout()
        schnell = Knopf("Schnell erschaffen", "⚡")
        schnell.setToolTip("Zusammenklicken: Aussehen, Eigenschaften, fertig")
        schnell.clicked.connect(self._erschaffen)
        frei = Knopf("Frei mit der KI", "💬")
        frei.setToolTip("Erzählen, wie du ihn dir vorstellst – ganz in deinen Worten, ohne Auswahllisten")
        frei.akzent = True
        frei.clicked.connect(self._frei_starten)
        meine = Knopf("Meinen Charakter verbessern", "✏")
        meine.clicked.connect(lambda: self.leitung.senden({"op": "laden"}))
        unten.addWidget(meine)
        unten.addStretch(1)
        unten.addWidget(schnell)
        unten.addWidget(frei)
        v.addLayout(unten)
        return s

    def _seite_frei(self) -> QWidget:
        """Freier Weg: großes Gespräch links, rechts der Steckbrief in eigenen Worten (frei änderbar)."""
        s = QWidget()
        v = QVBoxLayout(s)
        v.setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout()
        h.setSpacing(18)
        gespraech, gv = _tafel()
        kopf = QHBoxLayout()
        kopf.addWidget(label("💬 Erzähl mir von deinem Charakter", 13, "text", fett=True, umbruch=False))
        kopf.addStretch(1)
        self.frei_rueck_k = Knopf("", "↺", klein=True)
        self.frei_rueck_k.setToolTip("Letzte Änderung der KI am Steckbrief zurücknehmen")
        self.frei_rueck_k.setEnabled(False)
        self.frei_rueck_k.clicked.connect(self._frei_zuruecknehmen)
        kopf.addWidget(self.frei_rueck_k)
        gv.addLayout(kopf)
        gv.addWidget(label("Alles ist erlaubt – beschreib sie, wie du willst. Die KI fragt nach, denkt mit und "
                           "schreibt rechts mit. Du kannst dort jederzeit selbst ändern.", 10, "dim"))
        self.frei_inhalt = QWidget()
        self.frei_layout = QVBoxLayout(self.frei_inhalt)
        self.frei_layout.setContentsMargins(0, 0, 0, 0)
        self.frei_layout.setSpacing(8)
        self.frei_layout.addStretch(1)
        self.frei_rolle = rollbereich(self.frei_inhalt)
        gv.addWidget(self.frei_rolle, 1)
        eingabe = QHBoxLayout()
        self.frei_feld = QLineEdit()
        self.frei_feld.setPlaceholderText("z. B. „Sie hat pinke Locken mit einer silbernen Strähne und summt beim "
                                          "Nachdenken …“")
        self.frei_feld.returnPressed.connect(self._frei_senden)
        eingabe.addWidget(self.frei_feld, 1)
        self.frei_senden_k = Knopf("", "➤", klein=True)
        self.frei_senden_k.clicked.connect(self._frei_senden)
        eingabe.addWidget(self.frei_senden_k)
        gv.addLayout(eingabe)
        h.addWidget(gespraech, 1)

        brief, bv = _tafel()
        brief.setFixedWidth(RECHTS + 60)
        bv.addWidget(label("📋 Steckbrief – frei änderbar", 12, "text", fett=True))
        person = QHBoxLayout()
        self.frei_geschlecht = QComboBox()
        self.frei_geschlecht.addItem("– wer? –", "")
        for key, symbol, text in GESCHLECHT_KNOEPFE:
            self.frei_geschlecht.addItem(f"{symbol} {text}", key)
        person.addWidget(self.frei_geschlecht)
        self.frei_alter = QLineEdit()
        self.frei_alter.setValidator(QIntValidator(0, 999, self.frei_alter))
        self.frei_alter.setPlaceholderText("Alter (ab 18)")
        self.frei_alter.setFixedWidth(120)
        person.addWidget(self.frei_alter)
        person.addStretch(1)
        bv.addLayout(person)
        self.frei_felder: dict[str, QPlainTextEdit] = {}
        for key, (titel, hinweis) in CG.STECKBRIEF.items():
            bv.addWidget(label(titel, 10, "dim", umbruch=False))
            w = QPlainTextEdit()
            w.setPlaceholderText(hinweis)
            bv.addWidget(w, 1)
            self.frei_felder[key] = w
        self.frei_varianten_k = Knopf(f"{CG.ANZAHL_VORSCHLAEGE} Varianten erschaffen", "✨")
        self.frei_varianten_k.akzent = True
        self.frei_varianten_k.clicked.connect(self._vorschlaege_holen)
        bv.addWidget(self.frei_varianten_k)
        h.addWidget(brief)
        v.addLayout(h, 1)
        v.addWidget(self._zurueck(lambda: self.seite_start), 0, Qt.AlignmentFlag.AlignLeft)
        return s

    def _zurueck(self, ziel) -> Knopf:
        k = Knopf("Zurück", "←", klein=True)
        k.clicked.connect(lambda: self._zeigen(ziel()))
        return k

    def _seite_person(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.addWidget(label("Was suchst du?", 14, "text", fett=True))
        h = QHBoxLayout()
        self.g_knoepfe: dict[str, Knopf] = {}
        for key, symbol, text in GESCHLECHT_KNOEPFE:
            k = Knopf(text, symbol)
            k.setMinimumHeight(60)
            k.clicked.connect(lambda _=False, g=key: self._geschlecht(g))
            h.addWidget(k)
            self.g_knoepfe[key] = k
        v.addLayout(h)
        v.addSpacing(18)
        self.alter_titel = label("Wie alt soll dein Charakter sein?", 14, "text", fett=True)
        v.addWidget(self.alter_titel)
        v.addWidget(label("Charaktere sind immer erwachsen – ab 18 Jahren.", 10, "dim"))
        self.alter = QLineEdit()
        self.alter.setValidator(QIntValidator(0, 999, self.alter))
        self.alter.setPlaceholderText("z. B. 25")
        self.alter.setFixedWidth(160)
        self.alter.returnPressed.connect(self._person_weiter)
        self.alter.textChanged.connect(lambda _: self._rahmen_auffrischen())
        v.addWidget(self.alter)
        v.addStretch(1)
        h = QHBoxLayout()
        h.addWidget(self._zurueck(lambda: self.seite_start))
        h.addStretch(1)
        weiter = Knopf("Weiter", "→")
        weiter.akzent = True
        weiter.clicked.connect(self._person_weiter)
        h.addWidget(weiter)
        v.addLayout(h)
        return s

    def _seite_aussehen(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        self.aussehen_titel = label("Wie soll dein Charakter aussehen?", 14, "text", fett=True)
        v.addWidget(self.aussehen_titel)
        v.addWidget(label("Alles ist freiwillig – was auf „egal“ bleibt, entscheidet die KI.", 10, "dim"))
        inhalt = QWidget()
        iv = QVBoxLayout(inhalt)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(8)
        self.wahlen: dict[str, QComboBox] = {f: _wahl(w) for f, w in CG.AUSWAHL_FELDER.items() if f != "koerperform"}
        for box in self.wahlen.values():
            box.currentIndexChanged.connect(self._form_auffrischen)
        iv.addWidget(label("💇 Haare", 11, "text", fett=True))
        iv.addWidget(_zeile("Länge", self.wahlen["haar_laenge"], label("Art", 10, "dim", umbruch=False),
                            self.wahlen["haar_art"]))
        self.haar_farbe = Farbwahl(CG.HAAR_FARBEN)
        iv.addWidget(_zeile("Farbe", self.haar_farbe))
        iv.addWidget(label("👁 Augen", 11, "text", fett=True))
        self.augen_farbe = Farbwahl(CG.AUGEN_FARBEN)
        iv.addWidget(_zeile("Farbe", self.augen_farbe))
        iv.addWidget(label("🧍 Körper", 11, "text", fett=True))
        self.box_busen = _zeile("Busen", label("Größe", 10, "dim", umbruch=False), self.wahlen["busen_groesse"],
                                label("Form", 10, "dim", umbruch=False), self.wahlen["busen_form"])
        self.box_brust = _zeile("Brust", self.wahlen["brust"])
        iv.addWidget(self.box_busen)
        iv.addWidget(self.box_brust)
        iv.addWidget(_zeile("Taille", self.wahlen["taille"]))
        iv.addWidget(_zeile("Po", label("Größe", 10, "dim", umbruch=False), self.wahlen["po_groesse"],
                            label("Form", 10, "dim", umbruch=False), self.wahlen["po_form"],
                            label("Spannung", 10, "dim", umbruch=False), self.wahlen["po_spannung"]))
        iv.addWidget(label("Körperform", 11, "text", fett=True))
        self.form_info = label("", 10, "dim")
        iv.addWidget(self.form_info)
        reihe = QHBoxLayout()
        self.form_chips: dict[str, Knopf] = {}
        for form in CG.KOERPERFORMEN:
            k = Knopf(form, klein=True)
            k.clicked.connect(lambda _=False, x=form: self._form_klick(x))
            reihe.addWidget(k)
            self.form_chips[form] = k
        reihe.addStretch(1)
        iv.addLayout(reihe)
        iv.addStretch(1)
        v.addWidget(rollbereich(inhalt), 1)
        h = QHBoxLayout()
        self.aussehen_zurueck = Knopf("Zurück", "←", klein=True)
        self.aussehen_zurueck.clicked.connect(
            lambda: self._zeigen(self.seite_person if self.aussehen_modus == "neu" else self.seite_ansehen))
        h.addWidget(self.aussehen_zurueck)
        h.addStretch(1)
        self.aussehen_weiter = Knopf("Weiter", "→")
        self.aussehen_weiter.akzent = True
        self.aussehen_weiter.clicked.connect(self._aussehen_weiter)
        h.addWidget(self.aussehen_weiter)
        v.addLayout(h)
        self.haar_farbe.bei_aenderung = self.augen_farbe.bei_aenderung = self._form_auffrischen
        self._form_auffrischen()
        return s

    def _seite_eigenschaften(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.addWidget(label("Was macht den Charakter aus?", 14, "text", fett=True))
        self.eig_zaehler = label("", 10, "dim")
        v.addWidget(self.eig_zaehler)
        self.idee_feld = QLineEdit()
        self.idee_feld.setMaxLength(CG.MAX_IDEE)
        self.idee_feld.setPlaceholderText("💭 Eigene Wünsche, ganz frei (z. B. „spricht gern in Reimen“, "
                                          "„liebt Regenwetter“)")
        self.idee_feld.textChanged.connect(self._idee)
        v.addWidget(self.idee_feld)
        self.eig_inhalt = QWidget()
        self.eig_layout = QVBoxLayout(self.eig_inhalt)
        self.eig_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(rollbereich(self.eig_inhalt), 1)
        h = QHBoxLayout()
        zurueck = Knopf("Zurück", "←", klein=True)
        zurueck.clicked.connect(lambda: self._schritt(1))
        h.addWidget(zurueck)
        h.addStretch(1)
        self.eig_weiter = Knopf(f"{CG.ANZAHL_VORSCHLAEGE} Varianten erschaffen", "✨")
        self.eig_weiter.akzent = True
        self.eig_weiter.clicked.connect(self._vorschlaege_holen)
        h.addWidget(self.eig_weiter)
        v.addLayout(h)
        return s

    def _seite_vorschlaege(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.addWidget(label("Varianten – klick eine an, um sie anzusehen und zu verbessern.", 10, "dim"))
        self.vorschlag_raster_w, self.vorschlag_raster = _raster()
        v.addWidget(rollbereich(self.vorschlag_raster_w), 1)
        h = QHBoxLayout()
        h.addWidget(self._zurueck(lambda: self.seite_frei if self.weg == "frei" else self.seite_eig))
        h.addStretch(1)
        neu = Knopf("Neue Varianten", "🔄")
        neu.clicked.connect(self._vorschlaege_holen)
        h.addWidget(neu)
        v.addLayout(h)
        return s

    def _seite_ansehen(self) -> QWidget:
        s = QWidget()
        h = QHBoxLayout(s)
        links = QVBoxLayout()
        self.gross = _bildflaeche(GROSS)
        links.addWidget(self.gross)
        self.malen_k = Knopf("Neu malen (gleiches Gesicht)", "🎨", klein=True)
        self.malen_k.clicked.connect(lambda: self._neu_malen(False))
        self.gesicht_k = Knopf("Neues Gesicht", "🎲", klein=True)
        self.gesicht_k.clicked.connect(lambda: self._neu_malen(True))
        self.aussehen_k = Knopf("Aussehen ändern", "👗", klein=True)
        self.aussehen_k.clicked.connect(self._aussehen_aendern)
        for k in (self.malen_k, self.gesicht_k, self.aussehen_k):
            links.addWidget(k)
        links.addStretch(1)
        links.addWidget(self._zurueck(lambda: self.zurueck_von_ansehen))
        h.addLayout(links)

        rechts = QVBoxLayout()
        self.felder: dict[str, QLineEdit | QPlainTextEdit] = {}
        self.funken: dict[str, Knopf] = {}
        self.zeilen: dict[str, QWidget] = {}
        for feld, titel, mehrzeilig, hinweis in (
                ("name", "Name", False, ""), ("kurz", "Kurzbeschreibung", False, "4–10 Worte"),
                ("wesen", "Wesen", True, "wie die Persönlichkeit redet und was sie ausmacht"),
                ("kern", "Aussehen (englisch, fürs Bild)", True, "Haare, Augen, Gesicht, Körper – ohne Kleidung"),
                ("outfit", "Kleidung (englisch, fürs Bild)", False, "was die Figur trägt"),
                ("stil", "Bildstil (englisch)", False, "Licht, Stimmung")):
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            kopf = QHBoxLayout()
            kopf.addWidget(label(titel, 10, "dim", umbruch=False))
            kopf.addStretch(1)
            if feld != "name":
                f = Knopf("", "✨", klein=True)
                f.setToolTip("Die KI hilft, dieses Feld zu verbessern (oben dein Wunsch, optional)")
                f.clicked.connect(lambda _=False, x=feld: self._verbessern(x))
                self.funken[feld] = f
                kopf.addWidget(f)
            bv.addLayout(kopf)
            w = QPlainTextEdit() if mehrzeilig else QLineEdit()
            w.setPlaceholderText(hinweis)
            if mehrzeilig:
                w.setFixedHeight(90)
            bv.addWidget(w)
            self.felder[feld] = w
            self.zeilen[feld] = box
        self.wunsch = QLineEdit()
        self.wunsch.setPlaceholderText("✨ Was soll die KI ändern? (optional, z. B. „mehr Humor“, „Sommersprossen“)")
        rechts.addWidget(self.wunsch)
        for feld in self.zeilen:
            rechts.addWidget(self.zeilen[feld])
        rechts.addStretch(1)
        self.uebernehmen_k = Knopf("Übernehmen", "✅")
        self.uebernehmen_k.akzent = True
        self.uebernehmen_k.clicked.connect(self._fertig)
        rechts.addWidget(self.uebernehmen_k, 0, Qt.AlignmentFlag.AlignRight)
        h.addLayout(rechts, 1)
        self.zurueck_von_ansehen = self.seite_start
        return s

    def _seite_namen(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        self.namen_titel = label("Wie soll dein Charakter heißen?", 14, "text", fett=True)
        v.addWidget(self.namen_titel)
        v.addWidget(label("✨ passt zum Charakter · 🌈 frei erfunden – anklicken oder selbst tippen.", 10, "dim"))
        raster_w, raster = _raster()
        self.namen_knoepfe: list[Knopf] = []
        for i in range(CG.ANZAHL_NAMEN):
            k = Knopf("…", klein=True)
            k.setMinimumHeight(44)
            k.clicked.connect(lambda _=False, x=i: self._name_gewaehlt(x))
            raster.addWidget(k, i // 3, i % 3)
            self.namen_knoepfe.append(k)
        v.addWidget(raster_w)
        v.addSpacing(10)
        self.name_feld = QLineEdit()
        self.name_feld.setPlaceholderText("Eigener Name")
        self.name_feld.setFixedWidth(320)
        self.name_feld.returnPressed.connect(self._namen_fertig)
        v.addWidget(self.name_feld)
        v.addStretch(1)
        h = QHBoxLayout()
        h.addWidget(self._zurueck(lambda: self.seite_ansehen))
        h.addStretch(1)
        self.wuerfel_k = Knopf("Neue Namen", "🎲")
        self.wuerfel_k.clicked.connect(self._namen_holen)
        h.addWidget(self.wuerfel_k)
        self.namen_ok = Knopf("Übernehmen", "✅")
        self.namen_ok.akzent = True
        self.namen_ok.clicked.connect(self._namen_fertig)
        h.addWidget(self.namen_ok)
        v.addLayout(h)
        self._namen_zeigen([])
        return s

    # -- Abläufe: Person und Aussehen ----------------------------------------------------
    def _erschaffen(self) -> None:
        self.im_ablauf, self.weg = True, "schnell"
        self.art, self.entwurf = "vorschlag", {}
        self._zeigen(self.seite_person)

    def _alter_wert(self) -> int:
        try:
            return int(self.alter.text().strip())
        except ValueError:
            return 0

    def _person_ok(self) -> bool:
        return bool(self.auswahl["geschlecht"]) and self._alter_wert() >= self.alter_min

    def _geschlecht(self, g: str) -> None:
        self.auswahl["geschlecht"] = g
        self.auswahl["aussehen"] = CG.aussehen_pruefen(self.auswahl.get("aussehen"), g)
        self._geschlecht_zeigen()
        self.alter.setFocus()
        self._rahmen_auffrischen()

    def _geschlecht_zeigen(self) -> None:
        g = self.auswahl["geschlecht"]
        for key, k in self.g_knoepfe.items():
            k.akzent = key == g
            k.update()
        self.alter_titel.setText(f"Wie alt soll {PRONOMEN.get(g, PRONOMEN[''])} sein?")

    def _person_pruefen(self) -> bool:
        if not self.auswahl["geschlecht"]:
            self._melden("Wähl zuerst, wen du suchst: weiblich, männlich oder nichtbinär.", "warn", False)
            return False
        alter = self._alter_wert()
        if not self.alter.text().strip():
            self._melden("Tipp das Alter als Zahl ein, z. B. 25.", "warn", False)
            return False
        if alter < self.alter_min:
            self._melden(f"Charaktere sind immer erwachsen – mindestens {self.alter_min} Jahre.", "warn", False)
            return False
        self.auswahl["alter"] = min(alter, self.alter_max)
        if str(self.auswahl["alter"]) != self.alter.text().strip():
            self.alter.setText(str(self.auswahl["alter"]))
        self._melden("", "dim", False)
        return True

    def _person_weiter(self) -> None:
        if self._person_pruefen():
            self._aussehen_oeffnen(self.auswahl["geschlecht"], self.auswahl.get("aussehen") or {}, "neu")

    def _aussehen_vorbereiten(self, g: str) -> None:
        self.aussehen_g = g
        self.aussehen_titel.setText(f"Wie soll {PRONOMEN.get(g, PRONOMEN[''])} aussehen?")
        self.box_busen.setVisible(g in ("w", "n"))
        self.box_brust.setVisible(g in ("m", "n"))

    def _aussehen_oeffnen(self, g: str, aussehen: dict, modus: str) -> None:
        self.aussehen_modus = modus
        self._aussehen_vorbereiten(g)
        self.aussehen_weiter.setText("Weiter" if modus == "neu" else "Aussehen übernehmen")
        self.aussehen_weiter.symbol = "→" if modus == "neu" else "✔"
        self._aussehen_setzen(aussehen)
        self._zeigen(self.seite_aussehen)

    def _aussehen_setzen(self, a: dict) -> None:
        for feld, box in self.wahlen.items():
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findData(a.get(feld, ""))))
            box.blockSignals(False)
        self.haar_farbe.setzen(str(a.get("haar_farbe") or ""), melden=False)
        self.augen_farbe.setzen(str(a.get("augen_farbe") or ""), melden=False)
        self.form_fest = str(a.get("koerperform") or "") if a.get("koerperform_fest") else ""
        self._form_auffrischen()

    def _aussehen_roh(self) -> dict:
        a = {feld: box.currentData() or "" for feld, box in self.wahlen.items()}
        a.update(haar_farbe=self.haar_farbe.hex, augen_farbe=self.augen_farbe.hex)
        if self.form_fest:
            a.update(koerperform=self.form_fest, koerperform_fest=True)
        return a

    def _form_klick(self, form: str) -> None:
        self.form_fest = "" if self.form_fest == form else form
        self._form_auffrischen()

    def _form_auffrischen(self, *_) -> None:
        roh = self._aussehen_roh()
        if self.aussehen_modus == "neu":             # Klicks gelten sofort, auch für Steckbrief und Gespräch
            self.auswahl["aussehen"] = CG.aussehen_pruefen(roh, self.aussehen_g)
        for feld in (CG._NUR_W if self.aussehen_g == "m" else CG._NUR_M if self.aussehen_g == "w" else ()):
            roh[feld] = ""
        auto = CG.koerperform(roh)
        gezeigt = self.form_fest or auto
        for form, k in self.form_chips.items():
            k.akzent = form == gezeigt
            k.update()
        if self.form_fest:
            self.form_info.setText(f"Von dir gewählt: {self.form_fest} – noch einmal anklicken = automatisch.")
        elif auto:
            self.form_info.setText(f"Automatisch aus deinen Angaben: {auto} – anklicken zum Ändern.")
        else:
            self.form_info.setText("Wird aus Busen/Brust, Taille und Po errechnet – oder selbst anklicken.")
        if hasattr(self, "steckbrief"):
            self._steckbrief_auffrischen()

    def _aussehen_weiter(self) -> None:
        a = CG.aussehen_pruefen(self._aussehen_roh(), self.aussehen_g)
        if self.aussehen_modus == "neu":
            self.auswahl["aussehen"] = a
            self._zaehlen()
            self._zeigen(self.seite_eig)
            return
        w = self.felder["kern"]
        w.setPlainText(CG.kern_neu(w.toPlainText(), self.entwurf.get("aussehen") or {}, a))
        self.entwurf["aussehen"] = {**a, "geschlecht": self.aussehen_g} if self.art == "bestehend" else a
        self.aussehen_geaendert = True
        self._zeigen(self.seite_ansehen)
        self._melden("👗 Aussehen geändert – 🎨 malt das Porträt neu.", "ok", False)

    def _aussehen_aendern(self) -> None:
        g = self.entwurf.get("geschlecht") if self.entwurf.get("geschlecht") in CG.GESCHLECHTER else "n"
        self.entwurf["kern"] = self.felder["kern"].toPlainText()
        self._aussehen_oeffnen(g, self.entwurf.get("aussehen") or {}, "aendern")

    # -- Abläufe: Wesen und Varianten -------------------------------------------------------
    def _chips_bauen(self) -> None:
        while self.eig_layout.count():
            it = self.eig_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.chips.clear()
        for kategorie, liste in self.kategorien.items():
            self.eig_layout.addWidget(label(kategorie, 11, "text", fett=True))
            w, g = _raster()
            for i, e in enumerate(liste):
                k = Knopf(e, klein=True)
                k.setCheckable(True)
                k.toggled.connect(lambda an, x=e: self._chip(x, an))
                g.addWidget(k, i // 5, i % 5)
                self.chips[e] = k
            self.eig_layout.addWidget(w)
        self.eig_layout.addStretch(1)

    def _chip(self, eigenschaft: str, an: bool) -> None:
        gewaehlt = self.auswahl["eigenschaften"]
        if an and eigenschaft not in gewaehlt:
            if len(gewaehlt) >= self.max_eig:
                self.chips[eigenschaft].setChecked(False)
                return
            gewaehlt.append(eigenschaft)
        elif not an and eigenschaft in gewaehlt:
            gewaehlt.remove(eigenschaft)
        k = self.chips[eigenschaft]
        k.akzent = an and eigenschaft in gewaehlt          # gewählt: kräftiger Verlauf
        k.update()
        self._zaehlen()
        self._steckbrief_auffrischen()

    def _idee(self, text: str) -> None:
        self.auswahl["idee"] = " ".join(text.split())[:CG.MAX_IDEE]
        self._steckbrief_auffrischen()

    def _zaehlen(self) -> None:
        n = len(self.auswahl["eigenschaften"])
        self.eig_zaehler.setText(f"{n} von höchstens {self.max_eig} gewählt" + (" – mindestens eine" if not n else ""))
        self.eig_weiter.setEnabled(n > 0 and not self.laeuft)

    def _vorschlaege_holen(self) -> None:
        if self.weg == "frei":
            stand = self._frei_stand()
            self.leitung.senden({"op": "vorschlaege", "geschlecht": stand["geschlecht"], "alter": stand["alter"],
                                 "frei": {k: stand[k] for k in CG.STECKBRIEF}})
        elif not self.auswahl["eigenschaften"]:
            return
        else:
            self.leitung.senden({"op": "vorschlaege", **self.auswahl,
                                 "alter": self._alter_wert() or self.auswahl["alter"]})
        self._leeren(self.vorschlag_raster)
        self.vorschlag_karten = []
        self._zeigen(self.seite_vorschlaege)

    @staticmethod
    def _leeren(raster: QGridLayout) -> None:
        while raster.count():
            it = raster.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    # -- Abläufe: Ansehen, Name, Übernehmen ------------------------------------------------
    def _ansehen(self, entwurf: dict, bild: str, zurueck: QWidget) -> None:
        self.entwurf, self.bild = dict(entwurf), bild
        self.zurueck_von_ansehen = zurueck
        self.art = "bestehend" if entwurf.get("bestehend") else "vorlage" if entwurf.get("key") else "vorschlag"
        self.im_ablauf = self.art == "vorschlag"
        self.aussehen_geaendert = False
        self.namen_bisher = []
        for feld, w in self.felder.items():
            text = str(entwurf.get(feld) or "")
            w.setPlainText(text) if isinstance(w, QPlainTextEdit) else w.setText(text)
        sichtbar = {"vorlage": ("name", "kurz", "wesen", "kern", "stil"),
                    "vorschlag": ("kurz", "wesen", "kern", "outfit", "stil"),
                    "bestehend": ("kern", "stil")}[self.art]
        for feld, box in self.zeilen.items():
            box.setVisible(feld in sichtbar)
        self.aussehen_k.setVisible(self.art != "vorlage" and not entwurf.get("frei"))   # frei: im Steckbrief ändern
        text, symbol = {"vorlage": ("Übernehmen", "✅"), "vorschlag": ("Name wählen", "→"),
                        "bestehend": ("Speichern", "💾")}[self.art]
        self.uebernehmen_k.setText(text)
        self.uebernehmen_k.symbol = symbol
        _bild_setzen(self.gross, bild or "-", GROSS)
        self._zeigen(self.seite_ansehen)

    def _felder(self) -> dict:
        d = dict(self.entwurf)
        for feld, w in self.felder.items():
            d[feld] = (w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()).strip()
        return d

    def _verbessern(self, feld: str) -> None:
        self.funken[feld].setEnabled(False)
        self._melden(f"✨ Die KI verbessert „{feld}“ …", "dim", True)
        self.leitung.senden({"op": "verbessern", "feld": feld, "entwurf": self._felder(),
                             "wunsch": self.wunsch.text().strip()})

    def _neu_malen(self, neues_gesicht: bool) -> None:
        self.leitung.senden({"op": "neu_malen", "entwurf": self._felder(), "neues_gesicht": neues_gesicht})

    def _fertig(self) -> None:
        d = self._felder()
        if self.art == "vorschlag":
            self._namen_oeffnen()
            return
        if self.art == "bestehend":
            self.leitung.senden({"op": "speichern", "kern": d.get("kern"), "stil": d.get("stil"),
                                 "seed": d.get("seed"), "bild": self.bild,
                                 "aussehen": d.get("aussehen") if self.aussehen_geaendert else None})
        else:
            self.leitung.senden({"op": "uebernehmen", "entwurf": d, "bild": self.bild})
        self._melden("💾 wird gespeichert …", "dim", True)

    def _namen_oeffnen(self) -> None:
        g = self.entwurf.get("geschlecht", "")
        self.namen_titel.setText(f"Wie soll {PRONOMEN.get(g, PRONOMEN[''])} heißen?")
        self._zeigen(self.seite_namen)
        if not self.namen_bisher:                # neuer Entwurf: Name der Karte vorbelegen, Namen holen
            self.name_feld.setText(str(self.entwurf.get("name") or ""))
            self._namen_holen()

    def _namen_holen(self) -> None:
        self.namen_nr += 1
        self._namen_zeigen([])
        self.leitung.senden({"op": "namen", "entwurf": self._felder(), "bisher": list(self.namen_bisher),
                             "nr": self.namen_nr})

    def _namen_zeigen(self, liste: list[dict]) -> None:
        self.namen = liste[:len(self.namen_knoepfe)]
        for i, k in enumerate(self.namen_knoepfe):
            if i < len(self.namen):
                n = self.namen[i]
                k.setText(n["name"])
                k.symbol = "🌈" if n.get("art") == "frei" else "✨"
                k.setToolTip(n.get("warum") or "")
            else:
                k.setText("…")
                k.symbol = ""
                k.setToolTip("")
            k.setEnabled(i < len(self.namen))
            k.akzent = False
            k.update()

    def _name_gewaehlt(self, i: int) -> None:
        if i < len(self.namen):
            self.name_feld.setText(self.namen[i]["name"])
            for j, k in enumerate(self.namen_knoepfe):
                k.akzent = j == i
                k.update()

    def _namen_fertig(self) -> None:
        name = CG.name_sauber(self.name_feld.text())
        if not name:
            self._melden("Wähl einen Namen oder tipp einen eigenen ein.", "warn", False)
            return
        self.leitung.senden({"op": "uebernehmen", "entwurf": {**self._felder(), "name": name}, "bild": self.bild})
        self._melden("💾 wird gespeichert …", "dim", True)

    # -- Ereignisse der Sitzung ------------------------------------------------------------
    def _ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "start":
            self.kategorien = ev.get("kategorien") or {}
            self.max_eig = int(ev.get("max_eigenschaften") or 8)
            self.alter_min = int(ev.get("alter_min") or CG.ALTER_MIN)
            self.alter_max = int(ev.get("alter_max") or CG.ALTER_MAX)
            self._chips_bauen()
            self._leeren(self.vorlagen_raster)
            self.vorlagen = {}
            for i, v in enumerate(ev.get("vorlagen") or []):
                karte = Karte(v, lambda e, b: self._ansehen(e, b, self.seite_start))
                self.vorlagen[v["key"]] = karte
                self.vorlagen_raster.addWidget(karte, i // 3, i % 3)
            if ev.get("aktiv"):
                self.kopf.setText(f"🎭 Charakter-Galerie · aktiv: {ev['aktiv']}")
        elif t == "vorlage_bild" and ev.get("key") in self.vorlagen:
            self.vorlagen[ev["key"]].bild_setzen(str(ev.get("pfad") or ""))
        elif t == "vorschlaege":
            self.vorschlag_nr = int(ev.get("nr") or 0)
            self._leeren(self.vorschlag_raster)
            self.vorschlag_karten = []
            for i, v in enumerate(ev.get("liste") or []):
                karte = Karte({**v, "bild": ""}, lambda e, b: self._ansehen(e, b, self.seite_vorschlaege))
                self.vorschlag_karten.append(karte)
                self.vorschlag_raster.addWidget(karte, i // 3, i % 3)
            self._rahmen_auffrischen()
        elif t == "vorschlag_bild":
            i = int(ev.get("index", -1))
            if int(ev.get("nr") or 0) == self.vorschlag_nr and 0 <= i < len(self.vorschlag_karten):
                self.vorschlag_karten[i].bild_setzen(str(ev.get("pfad") or ""))
        elif t == "verbessert":
            feld, text = str(ev.get("feld") or ""), str(ev.get("text") or "")
            w = self.felder.get(feld)
            if w is not None and text:
                w.setPlainText(text) if isinstance(w, QPlainTextEdit) else w.setText(text)
            if feld in self.funken:
                self.funken[feld].setEnabled(True)
            self._melden("✨ verbessert – gefällt es dir? Sonst noch einmal ✨ oder selbst ändern.", "ok", False)
        elif t == "editor":
            self._ki_antwort(ev)
        elif t == "frei":
            self._frei_antwort(ev)
        elif t == "namen":
            if ev.get("nr") in (None, self.namen_nr):
                liste = [n for n in ev.get("liste") or [] if isinstance(n, dict) and n.get("name")]
                self.namen_bisher += [n["name"] for n in liste]
                self._namen_zeigen(liste)
        elif t == "bild":
            self.bild = str(ev.get("pfad") or "")
            self.entwurf["seed"] = ev.get("seed")
            _bild_setzen(self.gross, self.bild, GROSS)
        elif t == "uebernommen":
            self._melden(f"🎭 {ev.get('name')} ist angelegt und jetzt aktiv – Profil, Charakter-Datei und "
                         "Profilbild sind gespeichert.", "ok", False)
            self.kopf.setText(f"🎭 Charakter-Galerie · aktiv: {ev.get('name')}")
            self._zeigen(self.seite_start)
        elif t == "bearbeiten":
            daten = ev.get("daten")
            if not daten:
                self._melden(f"{ev.get('name')} hat noch keine Charakter-Datei – erschaffe einen Charakter "
                             "(✨ Eigene erschaffen) oder lass die KI im Chat eine anlegen.", "warn", False)
                return
            self._ansehen({**daten, "bestehend": True}, str(ev.get("bild") or ""), self.seite_start)
        elif t == "gespeichert":
            self._melden(f"💾 Charakter von {ev.get('name')} gespeichert (alter Stand im Papierkorb).", "ok", False)
        elif t == "status":
            self._melden(str(ev.get("text") or ""), "dim", bool(ev.get("laeuft")))
        elif t == "fehler":
            for f in self.funken.values():
                f.setEnabled(True)
            self._melden("⚠ " + str(ev.get("text") or ""), "fehler", False)
        elif t == "vorne":
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _melden(self, text: str, farbname: str, laeuft: bool) -> None:
        self.laeuft = laeuft
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(self.status.foregroundRole(), farbe(farbname))
        self.status.setPalette(pal)
        for k in (self.uebernehmen_k, self.malen_k, self.gesicht_k, self.wuerfel_k, self.namen_ok):
            k.setEnabled(not laeuft)
        self._zaehlen()


def starten() -> int:
    app = app_vorbereiten("NemiCLI.Charaktere")
    leitung = leitung_oeffnen("/charaktere")
    if leitung is None:
        return 1
    fenster = CharakterFenster(leitung)
    _dunkler_rahmen(fenster)
    fenster.show()
    return app.exec()
