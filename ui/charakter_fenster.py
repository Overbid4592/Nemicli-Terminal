"""charakter_fenster.py – Charakter-Galerie (/charaktere), eigener Prozess an der laufenden Sitzung.

Seiten: Galerie (9 Vorlagen) · Geschlecht · Alter (ab 18) · Eigenschaften (bis zu 8) · Vorschläge der KI mit
Porträt · Ansehen & Verbessern (✨ = die KI hilft beim Feld, 🎨 = Krea malt neu) · Übernehmen. Die Arbeit (KI, Krea,
Speichern ins Profil) macht die Sitzung (main_dienste.start_charaktere); das Fenster zeigt nur an.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from gui_fenster import FARBE, Knopf, _dunkler_rahmen, app_vorbereiten, farbe, label, leitung_oeffnen, rollbereich

STIL = f"""
QWidget#charaktere {{ background: {FARBE['grund']}; }}
QFrame#karte {{ background: {FARBE['flaeche']}; border: 1px solid {FARBE['rand']}; border-radius: 12px; }}
QFrame#karte:hover {{ border: 1px solid {FARBE['lila']}; }}
QPlainTextEdit, QLineEdit, QSpinBox {{
    background: {FARBE['flaeche']}; color: {FARBE['text']}; border: 1px solid {FARBE['rand']};
    border-radius: 8px; padding: 6px; selection-background-color: {FARBE['lila']}; }}
QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {FARBE['cyan']}; }}
"""
BILD = 190                                  # Kantenlänge der Porträts in den Karten
GROSS = 340                                 # Porträt auf der Seite „Ansehen“
GESCHLECHT_KNOEPFE = (("w", "♀", "Weiblich"), ("m", "♂", "Männlich"), ("n", "⚧", "Nichtbinär"))


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


class Karte(QFrame):
    """Ein Charakter in der Galerie: Porträt, Name, Kurzbeschreibung, „Ansehen“."""

    def __init__(self, entwurf: dict, bei_klick):
        super().__init__()
        self.setObjectName("karte")
        self.entwurf, self.bild = dict(entwurf), str(entwurf.get("bild") or "")
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

    def bild_setzen(self, pfad: str) -> None:
        self.bild = pfad
        _bild_setzen(self.flaeche, pfad, BILD)


def _raster(spalten: int) -> tuple[QWidget, QGridLayout]:
    w = QWidget()
    g = QGridLayout(w)
    g.setSpacing(14)
    g.setContentsMargins(0, 0, 0, 0)
    return w, g


class CharakterFenster(QWidget):
    def __init__(self, leitung):
        super().__init__()
        self.leitung = leitung
        self.setObjectName("charaktere")
        self.setWindowTitle("NemiCLI – Charakter-Galerie")
        self.setStyleSheet(STIL)
        self.resize(900, 900)
        self.vorlagen: dict[str, Karte] = {}
        self.vorschlag_karten: list[Karte] = []
        self.vorschlag_nr = 0
        self.kategorien: dict[str, list[str]] = {}
        self.max_eig = 8
        self.chips: dict[str, Knopf] = {}
        self.auswahl = {"geschlecht": "", "alter": 25, "eigenschaften": []}
        self.entwurf: dict = {}
        self.bild = ""
        self.laeuft = False

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        self.kopf = label("🎭 Charakter-Galerie", 17, "text", fett=True)
        v.addWidget(self.kopf)
        self.status = label("", 10, "dim")
        v.addWidget(self.status)
        self.stapel = QStackedWidget()
        v.addWidget(self.stapel, 1)
        self.seite_start = self._seite_start()
        self.seite_geschlecht = self._seite_geschlecht()
        self.seite_alter = self._seite_alter()
        self.seite_eig = self._seite_eigenschaften()
        self.seite_vorschlaege = self._seite_vorschlaege()
        self.seite_ansehen = self._seite_ansehen()
        for s in (self.seite_start, self.seite_geschlecht, self.seite_alter, self.seite_eig,
                  self.seite_vorschlaege, self.seite_ansehen):
            self.stapel.addWidget(s)

        leitung.ereignis.connect(self._ereignis)
        leitung.getrennt.connect(lambda: self._melden("Verbindung zur Sitzung getrennt – /charaktere neu öffnen.",
                                                      "fehler", False))
        leitung.senden({"op": "start"})

    # -- Seiten ------------------------------------------------------------------------
    def _seite_start(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(label("Fertige Persönlichkeiten – anklicken, ansehen, übernehmen. Oder erschaffe deine eigene.",
                          10, "dim"))
        self.vorlagen_raster_w, self.vorlagen_raster = _raster(3)
        v.addWidget(rollbereich(self.vorlagen_raster_w), 1)
        unten = QHBoxLayout()
        eigene = Knopf("Eigene erschaffen", "✨")
        eigene.akzent = True
        eigene.clicked.connect(lambda: self.stapel.setCurrentWidget(self.seite_geschlecht))
        meine = Knopf("Meinen Charakter verbessern", "✏")
        meine.clicked.connect(lambda: self.leitung.senden({"op": "laden"}))
        unten.addWidget(meine)
        unten.addStretch(1)
        unten.addWidget(eigene)
        v.addLayout(unten)
        return s

    def _zurueck(self, ziel) -> Knopf:
        k = Knopf("Zurück", "←", klein=True)
        k.clicked.connect(lambda: self.stapel.setCurrentWidget(ziel()))
        return k

    def _seite_geschlecht(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.addWidget(label("Wer soll es sein?", 14, "text", fett=True))
        h = QHBoxLayout()
        for key, symbol, text in GESCHLECHT_KNOEPFE:
            k = Knopf(text, symbol)
            k.setMinimumHeight(60)
            k.clicked.connect(lambda _=False, g=key: self._geschlecht(g))
            h.addWidget(k)
        v.addLayout(h)
        v.addStretch(1)
        v.addWidget(self._zurueck(lambda: self.seite_start), 0, Qt.AlignmentFlag.AlignLeft)
        return s

    def _seite_alter(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.addWidget(label("Wie alt?", 14, "text", fett=True))
        v.addWidget(label("Charaktere sind immer erwachsen – ab 18 Jahren.", 10, "dim"))
        self.alter = QSpinBox()
        self.alter.setRange(18, 99)
        self.alter.setValue(25)
        self.alter.setSuffix(" Jahre")
        self.alter.setFixedWidth(160)
        v.addWidget(self.alter)
        v.addStretch(1)
        h = QHBoxLayout()
        h.addWidget(self._zurueck(lambda: self.seite_geschlecht))
        h.addStretch(1)
        weiter = Knopf("Weiter", "→")
        weiter.akzent = True
        weiter.clicked.connect(self._alter_weiter)
        h.addWidget(weiter)
        v.addLayout(h)
        return s

    def _seite_eigenschaften(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.addWidget(label("Was macht den Charakter aus?", 14, "text", fett=True))
        self.eig_zaehler = label("", 10, "dim")
        v.addWidget(self.eig_zaehler)
        self.eig_inhalt = QWidget()
        self.eig_layout = QVBoxLayout(self.eig_inhalt)
        self.eig_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(rollbereich(self.eig_inhalt), 1)
        h = QHBoxLayout()
        h.addWidget(self._zurueck(lambda: self.seite_alter))
        h.addStretch(1)
        self.eig_weiter = Knopf("Charaktere erschaffen", "✨")
        self.eig_weiter.akzent = True
        self.eig_weiter.clicked.connect(self._vorschlaege_holen)
        h.addWidget(self.eig_weiter)
        v.addLayout(h)
        return s

    def _seite_vorschlaege(self) -> QWidget:
        s = QWidget()
        v = QVBoxLayout(s)
        v.addWidget(label("Vorschläge – klick einen an, um ihn anzusehen und zu verbessern.", 10, "dim"))
        self.vorschlag_raster_w, self.vorschlag_raster = _raster(2)
        v.addWidget(rollbereich(self.vorschlag_raster_w), 1)
        h = QHBoxLayout()
        h.addWidget(self._zurueck(lambda: self.seite_eig))
        h.addStretch(1)
        neu = Knopf("Neue Vorschläge", "🔄")
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
        links.addWidget(self.malen_k)
        links.addWidget(self.gesicht_k)
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
                ("kern", "Aussehen (englisch, fürs Bild)", True, "Haare, Augen, Gesicht, Kleidung"),
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
        self.wunsch.setPlaceholderText("✨ Was soll die KI ändern? (optional, z. B. „mehr Humor“, „rote Haare“)")
        rechts.addWidget(self.wunsch)
        for feld in ("name", "kurz", "wesen", "kern", "stil"):
            rechts.addWidget(self.zeilen[feld])
        rechts.addStretch(1)
        self.uebernehmen_k = Knopf("Übernehmen", "✅")
        self.uebernehmen_k.akzent = True
        self.uebernehmen_k.clicked.connect(self._fertig)
        rechts.addWidget(self.uebernehmen_k, 0, Qt.AlignmentFlag.AlignRight)
        h.addLayout(rechts, 1)
        self.zurueck_von_ansehen = self.seite_start
        return s

    # -- Abläufe ----------------------------------------------------------------------
    def _geschlecht(self, g: str) -> None:
        self.auswahl["geschlecht"] = g
        self.stapel.setCurrentWidget(self.seite_alter)

    def _alter_weiter(self) -> None:
        self.auswahl["alter"] = int(self.alter.value())
        self._zaehlen()
        self.stapel.setCurrentWidget(self.seite_eig)

    def _chips_bauen(self) -> None:
        while self.eig_layout.count():
            it = self.eig_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.chips.clear()
        for kategorie, liste in self.kategorien.items():
            self.eig_layout.addWidget(label(kategorie, 11, "text", fett=True))
            w, g = _raster(5)
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

    def _zaehlen(self) -> None:
        n = len(self.auswahl["eigenschaften"])
        self.eig_zaehler.setText(f"{n} von höchstens {self.max_eig} gewählt" + (" – mindestens eine" if not n else ""))
        self.eig_weiter.setEnabled(n > 0 and not self.laeuft)

    def _vorschlaege_holen(self) -> None:
        if not self.auswahl["eigenschaften"]:
            return
        self.leitung.senden({"op": "vorschlaege", **self.auswahl})
        self._leeren(self.vorschlag_raster)
        self.vorschlag_karten = []
        self.stapel.setCurrentWidget(self.seite_vorschlaege)

    @staticmethod
    def _leeren(raster: QGridLayout) -> None:
        while raster.count():
            it = raster.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _ansehen(self, entwurf: dict, bild: str, zurueck: QWidget) -> None:
        self.entwurf, self.bild = dict(entwurf), bild
        self.zurueck_von_ansehen = zurueck
        bestehend = bool(entwurf.get("bestehend"))
        for feld, w in self.felder.items():
            text = str(entwurf.get(feld) or "")
            w.setPlainText(text) if isinstance(w, QPlainTextEdit) else w.setText(text)
        for feld in ("name", "kurz", "wesen"):
            self.zeilen[feld].setVisible(not bestehend)
        self.uebernehmen_k.setText("Speichern" if bestehend else "Übernehmen")
        self.uebernehmen_k.symbol = "💾" if bestehend else "✅"
        _bild_setzen(self.gross, bild or "-", GROSS)
        self.stapel.setCurrentWidget(self.seite_ansehen)

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
        if d.get("bestehend"):
            self.leitung.senden({"op": "speichern", "kern": d.get("kern"), "stil": d.get("stil"),
                                 "seed": d.get("seed"), "bild": self.bild})
        else:
            self.leitung.senden({"op": "uebernehmen", "entwurf": d, "bild": self.bild})
        self._melden("💾 wird gespeichert …", "dim", True)

    # -- Ereignisse der Sitzung ------------------------------------------------------------
    def _ereignis(self, ev: dict) -> None:
        t = ev.get("t")
        if t == "start":
            self.kategorien = ev.get("kategorien") or {}
            self.max_eig = int(ev.get("max_eigenschaften") or 8)
            self.alter.setRange(int(ev.get("alter_min") or 18), int(ev.get("alter_max") or 99))
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
                self.vorschlag_raster.addWidget(karte, i // 2, i % 2)
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
        elif t == "bild":
            self.bild = str(ev.get("pfad") or "")
            self.entwurf["seed"] = ev.get("seed")
            _bild_setzen(self.gross, self.bild, GROSS)
        elif t == "uebernommen":
            self._melden(f"🎭 {ev.get('name')} ist angelegt und jetzt aktiv – Profil, Charakter-Datei und "
                         "Profilbild sind gespeichert.", "ok", False)
            self.kopf.setText(f"🎭 Charakter-Galerie · aktiv: {ev.get('name')}")
            self.stapel.setCurrentWidget(self.seite_start)
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
        for k in (self.uebernehmen_k, self.malen_k, self.gesicht_k):
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
