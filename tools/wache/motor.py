"""Der Motor: sammeln → anreichern → bewerten → Regeln → speichern → lernen.

Läuft in eigenen Threads, kennt keine Oberfläche. Wer wissen will, was los ist,
fragt `stand()`; wer bei Alarmen gerufen werden will, hängt sich an `bei_alarm`.

Lernen ohne Zutun: erstes Training, sobald
`min_training` Ereignisse da sind, danach alle `training_alle` Ereignisse –
auf den letzten 7 Tagen, nur aus Ereignissen älter als `ml_karenz_h` und ohne
Ereignisse mit offenem, unklarem oder echtem Alarm samt ihrer Prozess-Kette
(Speicher.lern_ereignisse). Vor jedem Lauf werden die Profile in die Merkmale
gespiegelt und das „harmlos“-Feedback übernommen. Jeder Lauf steht in
Wache/training.log und im Aktions-Protokoll. Nach einem Zurückdrehen
(`/wache ml|dl zurueck`) lernt das jeweilige Modell erst nach einem Training
durch den Nutzer wieder automatisch.

Profile (Pfade, Eltern, Kinder, Ports, Uhrzeiten) werden erst nach der Bewertung
fortgeschrieben – nicht für Ereignisse mit Alarm, nicht für deren Prozess-Kette
und nicht für Programme mit unbeurteiltem Alarm. Sonst gälte ein gemeldeter
Prozess Minuten später schon als bekannt.

Folgen-Netz (folgen.py): bewertet Schritte in Prozess-Ketten. Es lernt in einem
eigenen Thread höchstens alle `dl_training_stunden` und nur aus Ereignissen,
die älter als `dl_karenz_h` sind. Solange `dl_alarm` aus ist (Schattenmodus),
landen Funde nur in Wache/modelle/folgen_schatten.log – kein Alarm, kein Weckruf.

Netzwächter (netzwaechter.py): Verbindungen nach draußen bekommen beim Aufnehmen den Namen des
Ziels (netzauskunft) und werden von einem eigenen Netz-Modell (ML-002) und Netz-Folgen (DL-002)
bewertet. Solange `netz_alarm` aus ist, landen Funde nur in Wache/modelle/netz_schatten.log.
Besitzer neuer öffentlicher Netze fragt ein eigener Thread beim Adressregister nach (höchstens
eine Anfrage alle 3 s, Ergebnis gemerkt).

Fehler in der Hauptschleife beenden den Motor nicht: sie werden protokolliert,
der Durchlauf geht weiter. Häufen sie sich (FEHLER_GRENZE in FEHLER_FENSTER
Sekunden), meldet `stand()["gestoert"]` den Grund, bis es wieder ruhig läuft.

Verdichten und Dämpfen (ohne sie ist ein Großteil der offenen Alarme oft
derselbe Vorgang): Regel + Subjekt ist der Schlüssel. Kommt derselbe Alarm
innerhalb des Cooldowns wieder, wird am ersten der Zähler hochgesetzt („×37“)
statt eine neue Zeile zu schreiben – und niemand wird erneut geweckt. Ausnahmen:
Ist der neue Alarm schwerer oder der erste schon beurteilt, gibt es einen neuen.
Wurde ein Paar in den letzten 30 Tagen `daempfen_ab`-mal als harmlos beurteilt
(und nie als echt), meldet der Motor es nicht mehr, zählt es nur noch
(`gedaempft` im Stand) – außer es ist kritisch oder schwerer als alles, was dazu
harmlos war. Ist das Subjekt schon als „bekannt“ eingetragen, kommt der Alarm eine
Stufe leiser – nicht, wenn das Programm diesmal aus Temp oder von außerhalb der
installierten Software läuft.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from queue import Empty, Queue

from . import MODELL_ORDNER, TRAINING_LOG, ordner_anlegen
from .absolvierung import REGEL as ABSOLVIERUNG_REGEL, Absolvierung
from . import detektor as _detektor_modul
from . import folgen as _folgen_modul
from .detektor import Detektor
from .ereignisse import DATEI, NETZ, PROZESS, SYSTEM, Alarm, Ereignis, Schwere
from .folgen import FolgenDetektor
from .inventar import Inventar, PROZESS as INV_PROZESS, neue_als_ereignisse
from .justierung import einstellungen
from .merkmale import Burst, DateiMerkmale, ProzessMerkmale
from .netzauskunft import Auskunft
from .netzwaechter import SCHATTEN as NETZ_SCHATTEN, NetzFolgen, NetzMerkmale, SchattenLog, ZieleProMinute, domain_von
from .regeln import MENGENREGELN, Regelwerk
from .sensoren import DateiSensor, NetzSensor, ProzessSensor, SensorThread
from .speicher import Speicher
from .vollscan import Baseline


FEHLER_GRENZE = 5           # so viele Fehler …
FEHLER_FENSTER = 60.0       # … in so vielen Sekunden = gestört
RUHIG_NACH = 120.0          # so lange ohne Fehler = wieder in Ordnung
MAX_STAPEL = 5000           # nicht schreibbare Ereignisse höchstens so viele zurückhalten
MAX_BELASTET = 5000         # so viele Prozess-Ketten mit Alarm merkt sich der Motor


def _log(text: str) -> None:
    try:
        TRAINING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAINING_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {text}\n")
    except OSError:
        pass


def _protokoll(tool: str, text: str, status: str = "success", ergebnis: str = "") -> None:
    try:
        import protokoll
        protokoll.schreibe(tool, text, status, veraendernd=False, wer="Wache", ergebnis=ergebnis)
    except Exception:
        pass


class Motor:
    def __init__(self, db_pfad, einstellungen_: dict | None = None):
        ordner_anlegen()
        self.e = einstellungen_ or einstellungen()
        self.speicher = Speicher(db_pfad)
        self.queue: Queue[Ereignis] = Queue(maxsize=20_000)
        # Vollscan-Stand vor dem ersten Bewerten laden: Training und Bewertung
        # müssen dieselben vollscan_*-Werte sehen.
        self.baseline = Baseline()
        self.baseline.aktualisieren()
        self._baseline_geprueft = time.time()
        self.prozess_merkmale = ProzessMerkmale(self.baseline)
        self.detektor = Detektor(MODELL_ORDNER / "prozesse.json", self.prozess_merkmale,
                                 schwelle=self.e["schwelle"], kontamination=self.e["kontamination"])
        self.datei_detektor = Detektor(MODELL_ORDNER / "dateien.json", DateiMerkmale(self.baseline),
                                       schwelle=self.e["schwelle"],
                                       kontamination=self.e["kontamination"], label="Dateien")
        self.folgen = FolgenDetektor(MODELL_ORDNER / "folgen",
                                     schwelle=float(self.e.get("dl_schwelle", 0.995)))
        self._folgen_training = False
        self.dl_auffaellig = 0
        self.netz_detektor = Detektor(MODELL_ORDNER / "netz.json", NetzMerkmale(),
                                      schwelle=float(self.e.get("netz_schwelle", 0.99)),
                                      kontamination=self.e["kontamination"], label="Netz")
        self.netz_folgen = NetzFolgen(MODELL_ORDNER / "netzfolgen",
                                      schwelle=float(self.e.get("netz_dl_schwelle", 0.995)))
        self.netz_schatten = SchattenLog(MODELL_ORDNER / NETZ_SCHATTEN)
        self.auskunft = Auskunft(self.speicher, register=bool(self.e.get("netz_besitzer", True)))
        self.ziele_min = ZieleProMinute()
        self.netz_auffaellig = 0
        self._netz_training = False
        self._besitzer_offen: set[str] = set()
        self._besitzer: threading.Thread | None = None
        self.regeln = Regelwerk(stumm=set(self.e.get("stumme_regeln") or []))
        self.absolvierung = Absolvierung(urteile=self.speicher.urteile_zu)   # Programme auf Anweisung (R015)
        self.burst = Burst()
        self.datei_burst = Burst()
        self.inventar = Inventar(mit_aufgaben=self.e["inventar_aufgaben"])
        self.inventar_laeuft = False
        self.letztes_inventar: dict = {}

        self.prozess_sensor = ProzessSensor(self.e["prozess_intervall"])
        self.netz_sensor = NetzSensor(self.e["netz_intervall"])
        self.datei_sensor = DateiSensor(self.e.get("pfade"), self.e.get("endungen"))
        from .protokolle import ProtokollSensor
        self.protokoll_sensor = ProtokollSensor(float(self.e.get("protokoll_intervall", 30)))
        self._inventar_angestossen = 0.0
        self.sensoren: list[SensorThread] = []

        self._stopp = threading.Event()
        self._worker: threading.Thread | None = None
        self._wartung: threading.Thread | None = None
        self._lock = threading.RLock()
        # (regel, subjekt) → (alarm-id, zeit, Schwere des ersten Alarms)
        self._zuletzt: dict[tuple[str, str], tuple[str, float, int]] = {}
        self._gedaempft: dict[tuple[str, str], tuple[int, int]] = {}     # → (Anzahl harmlos, höchste Schwere)
        self._belastet: dict[tuple[int, float], float] = {}             # Ketten mit Alarm (PID, Start) → Zeit
        self._offene_pfade: set[str] = set()
        self.gedaempft_zaehler: dict[tuple[str, str], int] = {}
        self.verdichtet = 0

        self.start_zeit = 0.0
        self.verarbeitet = 0
        self.alarme = 0
        self.anomalien = 0
        self.neue_profile = 0
        self._seit_training = 0
        self.letztes_ereignis = 0.0
        self.sensor_fehler: list[str] = []
        self.gestoert = ""
        self._fehlerzeiten: deque[float] = deque(maxlen=FEHLER_GRENZE)
        self._letzter_fehler = 0.0
        self.verworfen = 0

        self.bei_alarm: list = []                     # Callbacks(list[Alarm])
        self.bei_training: list = []                  # Callbacks(dict)

        self._profile_spiegeln()
        self.daempfung_aktualisieren()
        # Was seit dem letzten Training reinkam, zählt weiter – sonst fängt der
        # „alle 200“-Takt nach jedem Neustart bei 0 an und ein Tag mit drei
        # Neustarts lernt nie.
        try:
            zuletzt = float(self.speicher.meta("training_zuletzt", "0") or 0)
            if zuletzt:
                self._seit_training = self.speicher.anzahl_ereignisse_seit(zuletzt)
        except Exception:
            pass
        # Gespeichertes Modell passte nicht (z. B. neue Merkmale): beim nächsten
        # Ereignis aus den gespeicherten Ereignissen neu lernen, nicht erst nach 200 neuen.
        if not self.detektor.trainiert and self.detektor.pfad.exists():
            self._seit_training = max(self._seit_training, self.e["min_training"])

    # --------------------------------------------------------------- Lebenslauf

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stopp.clear()
        self.start_zeit = time.time()
        self.sensoren = [SensorThread(s, self._aufnehmen, self._sensor_fehler)
                         for s in (self.prozess_sensor, self.netz_sensor, self.datei_sensor)
                         + ((self.protokoll_sensor,) if self.e.get("protokolle", True) else ())]
        for t in self.sensoren:
            t.start()
        self._worker = threading.Thread(target=self._schleife, daemon=True, name="wache-motor")
        self._worker.start()
        self._wartung = threading.Thread(target=self._wartungsschleife, daemon=True, name="wache-wartung")
        self._wartung.start()
        self._besitzer = threading.Thread(target=self._besitzer_schleife, daemon=True, name="wache-netzauskunft")
        self._besitzer.start()
        _protokoll("wache", "Wache gestartet", ergebnis=f"{len(self.sensoren)} Sensoren, "
                   f"Modell {'trainiert' if self.detektor.trainiert else 'Kaltstart'}")
        if self.e["inventar_beim_start"]:
            self.inventar_starten()

    def stop(self) -> None:
        self._stopp.set()
        for t in self.sensoren:
            t.stop()
        self.datei_sensor.stop()
        if self._worker:
            self._worker.join(timeout=4)
        if self._wartung:
            self._wartung.join(timeout=2)
        if self._besitzer:
            self._besitzer.join(timeout=2)
        _protokoll("wache", "Wache gestoppt",
                   ergebnis=f"{self.verarbeitet} Ereignisse, {self.alarme} Alarme")

    def beenden(self) -> None:
        self.stop()
        self.absolvierung.speichern()
        self.detektor.speichern()
        self.datei_detektor.speichern()
        self.netz_detektor.speichern()
        self.speicher.schliessen()

    @property
    def laeuft(self) -> bool:
        return bool(self._worker and self._worker.is_alive() and not self._stopp.is_set())

    def einstellungen_uebernehmen(self) -> None:
        """Nach einer Justierung: Schwelle, Cooldown, stumme Regeln live setzen."""
        self.e = einstellungen()
        self.detektor.schwelle = self.datei_detektor.schwelle = float(self.e["schwelle"])
        self.folgen.schwelle = float(self.e.get("dl_schwelle", 0.995))
        self.netz_detektor.schwelle = float(self.e.get("netz_schwelle", 0.99))
        self.netz_folgen.schwelle = float(self.e.get("netz_dl_schwelle", 0.995))
        self.auskunft.register = bool(self.e.get("netz_besitzer", True))
        self.regeln.stumm = set(self.e.get("stumme_regeln") or [])
        self.daempfung_aktualisieren()

    def daempfung_aktualisieren(self) -> None:
        """Nach jedem Urteil (und in der Wartung): welche (Regel, Subjekt)-Paare sind oft genug
        harmlos, welche Programmpfade dämpfen den ML-Score, welche haben noch offene Alarme?"""
        try:
            self._gedaempft = self.speicher.gedaempft(int(self.e.get("daempfen_ab", 3)), mit_schwere=True)
        except Exception:
            self._gedaempft = {}
        try:
            self.detektor.feedback(self.speicher.fehlalarm_pfade())
            self._offene_pfade = self.speicher.offene_pfade()
        except Exception as exc:
            self._fehler("urteile", exc)

    # --------------------------------------------------------------- Pipeline

    def _aufnehmen(self, liste: list[Ereignis]) -> None:
        # Ohne Warten einreihen: bei voller Schlange hing der Sensor sonst je Ereignis bis zu 1 s und sah
        # dabei nichts Neues. Verworfenes wird gezählt und gemeldet – nie still.
        weg = 0
        for e in liste:
            try:
                self.queue.put_nowait(e)
            except Exception:
                weg += 1
        if weg:
            self.verworfen += weg
            self._sensor_fehler("schlange", f"Ereignis-Flut: {weg} Ereignisse verworfen (Schlange voll)")

    def _sensor_fehler(self, name: str, text: str) -> None:
        self.sensor_fehler.append(f"{name}: {text.strip().splitlines()[-1][:160]}")
        del self.sensor_fehler[:-20]

    def _fehler(self, wo: str, exc: BaseException) -> None:
        """Fehler im Motor: protokollieren und zählen. Häufen sie sich, gilt die Wache als gestört."""
        jetzt = time.time()
        text = f"{wo}: {type(exc).__name__}: {str(exc)[:160]}"
        self._sensor_fehler("motor", text)
        self._letzter_fehler = jetzt
        self._fehlerzeiten.append(jetzt)
        if (not self.gestoert and len(self._fehlerzeiten) >= FEHLER_GRENZE
                and jetzt - self._fehlerzeiten[0] <= FEHLER_FENSTER):
            self.gestoert = text
            _log(f"Motor gestört – {text}")
            _protokoll("wache", "Wache gestört", "error", ergebnis=text)

    def _ruhig_pruefen(self) -> None:
        if self.gestoert and time.time() - self._letzter_fehler > RUHIG_NACH:
            _log(f"Motor läuft wieder ohne Fehler (vorher: {self.gestoert})")
            _protokoll("wache", "Wache läuft wieder normal", ergebnis=self.gestoert)
            self.gestoert = ""
            self._fehlerzeiten.clear()

    def _bewerten(self, e: Ereignis, alarme: list[Alarm]) -> None:
        """Ein Ereignis bewerten; neue Alarme hinten an `alarme`. Verdichtet werden nur die
        neuen – Alarme, die beim letzten Schreiben hängen blieben, sind schon verdichtet."""
        neue: list[Alarm] = []
        self._bewerten_in(e, neue)
        gemeldet = self._verdichten(neue, e)
        alarme.extend(gemeldet)
        belastet = bool(gemeldet) or "alarm" in (e.extra or {})
        if belastet:
            if e.pid and e.start:
                self._belastet[(e.pid, round(e.start, 3))] = e.zeit
                if len(self._belastet) > MAX_BELASTET:
                    del self._belastet[next(iter(self._belastet))]
            if e.exe:
                self._offene_pfade.add(e.exe.lower())
        self._profil_lernen(e, belastet)

    def _bewerten_in(self, e: Ereignis, alarme: list[Alarm]) -> None:
        # Jede Stufe einzeln abgesichert: scheitert Anreichern oder ML (z. B. Datenbank gesperrt),
        # laufen die festen Regeln trotzdem – fail-closed statt eines stillen Ereignisses.
        try:
            self._anreichern(e)
        except Exception as exc:
            self._fehler("anreichern", exc)
        if e.aktion.startswith("evtlog_"):
            # Aus den Ereignisprotokollen: nur die festen Regeln. Für die gelernten Modelle sind das
            # unbekannte Ereignisarten – sie wären allein deshalb „ungewöhnlich“.
            from .protokolle import INSTALLATION
            if e.aktion in INSTALLATION:
                self._inventar_anstossen()
            alarme.extend(self.regeln.pruefen(e))
            return
        if self.e["ml"]:
            try:
                d = self.datei_detektor if e.kategorie == DATEI else self.detektor
                score, gruende = d.bewerten(e)
                e.score = round(score, 4)
                e.anomalie = d.ist_anomalie(score)
                if e.anomalie:
                    self.anomalien += 1
                    d.anomalien += 1
                    if e.schwere < Schwere.MITTEL:
                        e.schwere = Schwere.MITTEL
                    alarme.append(self._ml_alarm(e, score, gruende))
            except Exception as exc:
                self._fehler("ml", exc)
        if self.e.get("dl", True):
            try:
                self._folgen_bewerten(e, alarme)
            except Exception as exc:
                self._fehler("folgen-netz", exc)
        if self.e.get("netz", True) and (e.kategorie == NETZ or e.aktion == "process_stop"):
            try:
                self._netz_bewerten(e, alarme)
            except Exception as exc:
                self._fehler("netzwaechter", exc)
        alarme.extend(self.regeln.pruefen(e))
        if e.kategorie == PROZESS:
            try:
                neu = self.absolvierung.verarbeiten(e)
            except Exception as exc:
                self._fehler("programm-absolvierung", exc)
            else:
                if ABSOLVIERUNG_REGEL not in self.regeln.stumm:
                    alarme.extend(neu)

    def _folgen_bewerten(self, e: Ereignis, alarme: list[Alarm]) -> None:
        score, beschreibung = self.folgen.bewerten(e)
        if not score:
            return
        e.extra = {**(e.extra or {}), "dl": round(score, 4)}
        if not self.folgen.ist_auffaellig(score):
            return
        self.dl_auffaellig += 1
        self.folgen.auffaellig += 1
        if self.e.get("dl_alarm", False):
            alarme.append(self._dl_alarm(e, score, beschreibung))
        else:
            e.extra = {**e.extra, "dl_fund": True}        # kein Alarm, aber auch kein Lernstoff
            self.folgen.schatten_melden(e, score, beschreibung)

    def _netz_bewerten(self, e: Ereignis, alarme: list[Alarm]) -> None:
        """Netzwächter: ML (erst nach dem ersten Training) und DL. Schattenmodus = nur Protokoll."""
        if e.aktion != "conn_open":
            self.netz_folgen.bewerten(e)                 # Prozess-Ende schließt seine Kette
            return
        ml, gruende = self.netz_detektor.bewerten(e) if self.netz_detektor.trainiert else (0.0, [])
        dl, schritt = self.netz_folgen.bewerten(e)
        e.extra = {**e.extra, "netz_ml": round(ml, 4), **({"netz_dl": round(dl, 4)} if dl else {})}
        ml_auff = self.netz_detektor.trainiert and self.netz_detektor.ist_anomalie(ml)
        dl_auff = bool(dl) and self.netz_folgen.ist_auffaellig(dl)
        if not (ml_auff or dl_auff):
            return
        self.netz_auffaellig += 1
        if ml_auff:
            self.netz_detektor.anomalien += 1
        if dl_auff:
            self.netz_folgen.auffaellig += 1
        if self.e.get("netz_alarm", False):
            if ml_auff:
                alarme.append(self._netz_alarm(e, "ML-002", ml, gruende))
            if dl_auff:
                alarme.append(self._netz_alarm(e, "DL-002", dl, [f"ungewöhnlicher Schritt: {schritt}"]))
        else:
            e.extra = {**e.extra, "netz_fund": True}    # kein Alarm, aber auch kein Lernstoff
            self.netz_schatten.melden(e, ml, dl, gruende, schritt)

    def _netz_alarm(self, e: Ereignis, regel: str, score: float, gruende: list[str]) -> Alarm:
        ziel = (e.extra or {}).get("name") or e.ziel
        quelle = "Netz-Modell" if regel == "ML-002" else "Netz-Folgen"
        return Alarm(
            titel=f"Ungewöhnliche Verbindung: {e.prozess or '?'} → {ziel}",
            text=(f"{quelle}: auffälliger als {score:.1%} des Gelernten. Auslöser: "
                  f"{'; '.join(gruende) or 'Abweichung vom Gelernten'}. Ziel {e.ziel}:{e.zielport}. Ereignis: {e.text}"),
            schwere=Schwere.HOCH if score >= 0.999 else Schwere.MITTEL,
            regel=regel, ereignis_id=e.id, quelle="netz", score=round(score, 4), zeit=e.zeit,
            subjekt=f"{e.prozess or '?'} → {domain_von(e)}")

    def _besitzer_schleife(self) -> None:
        """Besitzer neuer öffentlicher Netze nachfragen – eins alle 3 s, Bekanntes kommt aus dem Speicher."""
        while not self._stopp.wait(3.0):
            if not self._besitzer_offen or not self.e.get("netz_besitzer", True):
                continue
            try:
                self.auskunft.besitzer(self._besitzer_offen.pop())
            except KeyError:
                continue
            except Exception as exc:
                self._sensor_fehler("netz-auskunft", f"{type(exc).__name__}: {exc}")

    def _durchlauf(self, stapel: list[Ereignis], alarme: list[Alarm], zuletzt: float) -> float:
        """Ein Takt der Hauptschleife. Gibt die Zeit des letzten Schreibens zurück."""
        try:
            e = self.queue.get(timeout=0.5)
        except Empty:
            e = None
        if e is not None:
            try:
                self._bewerten(e, alarme)
            except Exception as exc:
                self._fehler("bewerten", exc)
            stapel.append(e)
            self.verarbeitet += 1
            self._seit_training += 1
            self.letztes_ereignis = e.zeit
        jetzt = time.time()
        if jetzt - self._baseline_geprueft > 300:
            self._baseline_geprueft = jetzt
            if self.baseline.aktualisieren():
                # Neuer Vollscan: das Modell lernt mit dem neuen Stand neu.
                _log(f"Neuer Vollscan-Stand (Lauf {self.baseline.lauf}) – Training folgt")
                self._seit_training = max(self._seit_training, self.e["training_alle"])
        if stapel and (len(stapel) >= 50 or jetzt - zuletzt > 1.5):
            zuletzt = jetzt
            if len(stapel) > MAX_STAPEL:
                self.verworfen += len(stapel) - MAX_STAPEL
                del stapel[:-MAX_STAPEL]
            self.speicher.ereignisse_schreiben(stapel)
            stapel.clear()
        if alarme:
            self.speicher.alarme_schreiben(alarme)
            self.alarme += len(alarme)
            for cb in self.bei_alarm:
                try:
                    cb(list(alarme))
                except Exception:
                    pass
            alarme.clear()
        if (self.e["ml"] and self.e["lernen"] and not self.detektor.pausiert and (
                self._seit_training >= self.e["training_alle"]
                or (not self.detektor.trainiert and self._seit_training >= self.e["min_training"]))):
            self._seit_training = 0
            self._vielleicht_trainieren()
        return zuletzt

    def _schleife(self) -> None:
        stapel: list[Ereignis] = []
        alarme: list[Alarm] = []
        zuletzt = time.time()
        while not self._stopp.is_set():
            try:
                zuletzt = self._durchlauf(stapel, alarme, zuletzt)
                self._ruhig_pruefen()
            except Exception as exc:
                # Ereignisse und Alarme bleiben im Stapel und werden im nächsten Takt
                # erneut geschrieben; nur das Ereignis, das den Fehler auslöste, fehlt.
                self._fehler("schleife", exc)
                self._stopp.wait(0.5)
        try:
            if stapel:
                self.speicher.ereignisse_schreiben(stapel)
            if alarme:
                self.speicher.alarme_schreiben(alarme)
        except Exception as exc:
            self._fehler("beenden", exc)

    def _anreichern(self, e: Ereignis) -> None:
        """Vor der Bewertung: Ereignisrate stempeln, Erstsichtung markieren. Das Profil
        schreibt erst `_profil_lernen` – nach der Bewertung."""
        x = e.extra or {}
        if e.kategorie == DATEI:
            burst = self.datei_burst.beobachten((x.get("ordner") or "").lower(), e.zeit)
        else:
            burst = self.burst.beobachten(e.prozess, e.zeit)
        e.extra = {**x, "burst": round(burst, 4)}
        if e.kategorie == NETZ and e.aktion == "conn_open" and self.e.get("netz", True):
            neu: dict = {"ziele_min": self.ziele_min.beobachten(e.prozess, e.ziel, e.zeit)}
            if x.get("extern") and e.ziel:
                name = self.auskunft.name(e.ziel)
                if name:
                    neu["name"] = name
                if self.e.get("netz_besitzer", True) and len(self._besitzer_offen) < 500:
                    self._besitzer_offen.add(e.ziel)
            e.extra = {**e.extra, **neu}
        if (e.kategorie == PROZESS and e.prozess and e.aktion == "process_start"
                and not self.speicher.profil_bekannt(e.prozess)):
            e.extra = {**e.extra, "erstmals": True}
            if e.schwere < Schwere.NIEDRIG:
                e.schwere = Schwere.NIEDRIG

    def _kette_belastet(self, e: Ereignis) -> bool:
        if e.pid and e.start and (e.pid, round(e.start, 3)) in self._belastet:
            return True
        if e.ppid and e.eltern_start and (e.ppid, round(e.eltern_start, 3)) in self._belastet:
            return True
        return bool(e.exe) and e.exe.lower() in self._offene_pfade

    def _profil_lernen(self, e: Ereignis, belastet: bool) -> None:
        """Profil fortschreiben – nur, wenn weder das Ereignis noch seine Kette einen Alarm hat."""
        if belastet or self._kette_belastet(e):
            return
        x = e.extra or {}
        stunde = datetime.fromtimestamp(e.zeit).hour
        if e.kategorie == NETZ and e.prozess and e.zielport:
            self.speicher.profil_fortschreiben(e.prozess, stunde=stunde, port=e.zielport)
            return
        if e.kategorie != PROZESS or not e.prozess:
            return
        if e.eltern and e.aktion == "process_start":
            self.speicher.profil_fortschreiben(e.eltern, stunde=stunde, kind=e.prozess)
        neu = self.speicher.profil_fortschreiben(
            e.prozess, exe=e.exe, eltern=e.eltern, nutzer=e.nutzer,
            cpu=float(x.get("cpu", 0.0)), mem=float(x.get("mem_mb", 0.0)), stunde=stunde)
        if neu and e.aktion == "process_start":
            self.neue_profile += 1

    def _ml_alarm(self, e: Ereignis, score: float, gruende: list[str]) -> Alarm:
        subjekt = e.prozess or e.datei or e.ziel or "Systemereignis"
        return Alarm(
            titel=f"Anomalie: {subjekt}",
            text=(f"Auffälliger als {score:.1%} des gelernten Normalbilds. Auslöser: "
                  f"{'; '.join(gruende) if gruende else 'Abweichung vom Normalbild'}. "
                  f"Ereignis: {e.text}"),
            schwere=Schwere.HOCH if score >= 0.995 else Schwere.MITTEL,
            regel="ML-001", ereignis_id=e.id, quelle="ml", score=round(score, 4), zeit=e.zeit,
            subjekt=f"{subjekt} · {e.aktion}")

    def _dl_alarm(self, e: Ereignis, score: float, beschreibung: str) -> Alarm:
        subjekt = e.prozess or "Prozess"
        schritt = beschreibung.split(": ", 1)[-1].split(" (p=")[0]
        return Alarm(
            titel=f"Ungewöhnlicher Ablauf: {subjekt}",
            text=(f"Das Folgen-Netz hält diesen Schritt in der Prozess-Kette für überraschender als "
                  f"{score:.1%} der gelernten Abläufe. Schritt: {beschreibung}. Ereignis: {e.text}"),
            schwere=Schwere.HOCH if score >= 0.999 else Schwere.MITTEL,
            regel="DL-001", ereignis_id=e.id, quelle="dl", score=round(score, 4), zeit=e.zeit,
            subjekt=f"{subjekt} · {schritt}")

    def _verdichten(self, alarme: list[Alarm], e: Ereignis) -> list[Alarm]:
        """Gleicher Alarm (Regel + Subjekt) innerhalb des Cooldowns → Zähler am ersten hoch,
        keine neue Zeile; das Ereignis merkt sich den Alarm (`extra.alarm`), damit es nicht
        gelernt wird, solange der Alarm offen ist. Schwerer als der erste oder der erste schon
        beurteilt → neuer Alarm. Nur bei Mengen-Regeln: gedämpfte Paare werden bloß gezählt,
        bekannte Subjekte kommen eine Stufe leiser. Muster-Regeln (R001 …) bleiben immer laut."""
        out: list[Alarm] = []
        bekannt: bool | None = None
        for a in alarme:
            key = (a.regel, a.subjekt.lower())
            menge = a.regel in MENGENREGELN
            gedaempft = self._gedaempft.get(key) if menge else None
            if gedaempft and a.schwere < Schwere.KRITISCH and int(a.schwere) <= gedaempft[1]:
                self.gedaempft_zaehler[key] = self.gedaempft_zaehler.get(key, 0) + 1
                continue
            roh = int(a.schwere)                          # vor dem Leiserstellen – Maß für „schwerer“
            alt = self._zuletzt.get(key)
            if alt and 0 <= a.zeit - alt[1] < self.e["cooldown"] and roh <= alt[2]:
                try:
                    stand = self.speicher.alarm_zaehlen(alt[0], a.zeit)
                except Exception as exc:                  # Zählen gescheitert: Alarm lieber neu zeigen
                    self._fehler("alarm-zaehlen", exc)
                    stand = "beurteilt"
                if stand != "beurteilt":
                    self._zuletzt[key] = (alt[0], a.zeit, alt[2])   # gleitend: solange es weitergeht, EIN Alarm
                    self.verdichtet += 1
                    e.extra = {**(e.extra or {}), "alarm": alt[0]}
                    continue
            if menge and bekannt is None:
                try:
                    bekannt = self.speicher.programm_bekannt(e) and not self._fremder_ort(e)
                except Exception:
                    bekannt = False
            if menge and bekannt and a.schwere > Schwere.NIEDRIG:
                a.schwere = Schwere(int(a.schwere) - 1)
            self._zuletzt[key] = (a.id, a.zeit, roh)
            out.append(a)
        if len(self._zuletzt) > 2000:
            self._zuletzt.clear()
        return out

    def _fremder_ort(self, e: Ereignis) -> bool:
        """Läuft das Programm aus Temp oder von außerhalb der installierten Software? Dann gilt
        „bekannt“ (am Namen) nicht – derselbe Name an fremdem Ort ist ein anderes Programm."""
        exe = (e.exe or "").lower()
        if not exe:
            return False
        return "\\temp\\" in exe or bool(self.prozess_merkmale._ausserhalb(e.exe))

    # --------------------------------------------------------------- Training

    def _profile_spiegeln(self) -> None:
        self.prozess_merkmale.profile_setzen(self.speicher.profile(), self.speicher.software_pfade())

    def _vielleicht_trainieren(self) -> dict:
        n = self.speicher.anzahl_ereignisse()
        if n < self.e["min_training"]:
            return {"ok": False, "grund": f"nur {n} Ereignisse, {self.e['min_training']} nötig"}
        return self.trainieren()

    def trainieren(self, wer: str = "Wache") -> dict:
        """Beide Detektoren nachtrainieren, danach das Folgen-Netz. Trainiert der Nutzer selbst,
        endet eine Pause nach dem Zurückdrehen."""
        if wer == "Nutzer":
            for d in (self.detektor, self.datei_detektor, self.netz_detektor):
                d.pause_aufheben()
            for o in (self.folgen.ordner, self.netz_folgen.ordner):
                _folgen_modul.pause_marke(o).unlink(missing_ok=True)
        with self._lock:
            self._profile_spiegeln()
            self.daempfung_aktualisieren()
            jetzt = time.time()
            karenz = float(self.e.get("ml_karenz_h", 24.0)) * 3600
            alle = self.speicher.lern_ereignisse(jetzt - 7 * 86400, jetzt - karenz, limit=20_000)
            dateien = [x for x in alle if x.kategorie == DATEI]
            andere = [x for x in alle if x.kategorie != DATEI]
            ergebnis = self.detektor.trainieren(andere)
            if len(dateien) >= self.e["min_training_dateien"]:
                ergebnis["dateien"] = self.datei_detektor.trainieren(dateien)
            else:
                ergebnis["dateien"] = {"ok": False, "grund": f"nur {len(dateien)} Datei-Ereignisse, "
                                                             f"{self.e['min_training_dateien']} nötig"}
            netz = [x for x in andere if x.kategorie == NETZ and x.aktion == "conn_open"]
            mindest = int(self.e.get("min_training_netz", 200))
            if not self.e.get("netz", True):
                ergebnis["netz"] = {"ok": False, "grund": "Netzwächter ist aus"}
            elif self.netz_detektor.pausiert and wer != "Nutzer":
                ergebnis["netz"] = {"ok": False, "grund": "zurückgedreht – lernt erst nach einem Training durch den Nutzer"}
            elif len(netz) >= mindest:
                ergebnis["netz"] = self.netz_detektor.trainieren(netz)
            else:
                ergebnis["netz"] = {"ok": False, "grund": f"nur {len(netz)} Verbindungen, {mindest} nötig"}
        self.speicher.meta_setzen("training_zuletzt", str(time.time()))
        kurz = (f"Prozesse: {ergebnis.get('ereignisse', 0)} Ereignisse in {ergebnis.get('sekunden', 0)}s "
                f"(Lauf {ergebnis.get('laeufe', 0)}) · Dateien: "
                + (f"{ergebnis['dateien'].get('ereignisse')} Ereignisse" if ergebnis["dateien"].get("ok")
                   else ergebnis["dateien"].get("grund", "?"))
                + " · Netz: " + (f"{ergebnis['netz'].get('ereignisse')} Verbindungen" if ergebnis["netz"].get("ok")
                                 else ergebnis["netz"].get("grund", "?")))
        _log(f"Training ({wer}) – {kurz}")
        _protokoll("wache_training", f"Modell nachtrainiert ({wer})", ergebnis=kurz)
        ergebnis["folgen"] = self.folgen_trainieren(wer, erzwingen=(wer == "Nutzer"))
        ergebnis["netz_folgen"] = self.netz_folgen_trainieren(wer, erzwingen=(wer == "Nutzer"))
        for cb in self.bei_training:
            try:
                cb(ergebnis)
            except Exception:
                pass
        return ergebnis

    def folgen_trainieren(self, wer: str = "Wache", *, erzwingen: bool = False, warten: bool = False) -> str:
        """Folgen-Netz im eigenen Thread trainieren (dauert Sekunden bis Minuten).
        Ohne `erzwingen` höchstens alle `dl_training_stunden`."""
        if not self.e.get("dl", True):
            return "Folgen-Netz ist aus"
        if self._folgen_training:
            return "Folgen-Netz trainiert schon"
        abstand = float(self.e.get("dl_training_stunden", 6.0)) * 3600
        if not erzwingen and _folgen_modul.pausiert(self.folgen.ordner):
            return "Folgen-Netz: zurückgedreht, lernt erst nach einem Training durch den Nutzer wieder"
        if not erzwingen and self.folgen.zuletzt and time.time() - self.folgen.zuletzt < abstand:
            return "Folgen-Netz: nächstes Training später"
        self._folgen_training = True

        def _lauf():
            try:
                jetzt = time.time()
                karenz = float(self.e.get("dl_karenz_h", 24.0)) * 3600
                ev = self.speicher.lern_ereignisse(jetzt - 7 * 86400, jetzt - karenz, limit=60_000)
                r = self.folgen.trainieren(ev, abbruch=self._stopp.is_set)
                if r.get("ok"):
                    kurz = (f"{r['version']}: {r['folgen']} Ketten aus {r['ereignisse']} Ereignissen, "
                            f"{r['vokabular']} Zeichen, Verlust {r['verlust']}, {r['sekunden']}s")
                else:
                    kurz = r.get("grund", "?")
                _log(f"Folgen-Netz ({wer}) – {kurz}")
                _protokoll("wache_training", f"Folgen-Netz trainiert ({wer})", ergebnis=kurz)
            except Exception as exc:
                self._sensor_fehler("folgen-training", f"{type(exc).__name__}: {exc}")
            finally:
                self._folgen_training = False

        t = threading.Thread(target=_lauf, daemon=True, name="wache-folgen")
        t.start()
        if warten:
            t.join()
        return "Folgen-Netz-Training gestartet"

    def netz_folgen_trainieren(self, wer: str = "Wache", *, erzwingen: bool = False, warten: bool = False) -> str:
        """Netz-Folgen (DL-002) im eigenen Thread – gleicher Takt und gleiche Karenz wie das Folgen-Netz."""
        if not self.e.get("netz", True):
            return "Netzwächter ist aus"
        if self._netz_training:
            return "Netz-Folgen trainieren schon"
        if not erzwingen and _folgen_modul.pausiert(self.netz_folgen.ordner):
            return "Netz-Folgen: zurückgedreht, lernen erst nach einem Training durch den Nutzer wieder"
        abstand = float(self.e.get("dl_training_stunden", 6.0)) * 3600
        if not erzwingen and self.netz_folgen.zuletzt and time.time() - self.netz_folgen.zuletzt < abstand:
            return "Netz-Folgen: nächstes Training später"
        self._netz_training = True

        def _lauf():
            try:
                jetzt = time.time()
                karenz = float(self.e.get("dl_karenz_h", 24.0)) * 3600
                ev = [x for x in self.speicher.lern_ereignisse(jetzt - 7 * 86400, jetzt - karenz, limit=60_000)
                      if (x.kategorie == NETZ and x.aktion == "conn_open") or x.aktion == "process_stop"]
                r = self.netz_folgen.trainieren(ev, abbruch=self._stopp.is_set)
                if r.get("ok"):
                    kurz = (f"{r['version']}: {r['folgen']} Ketten aus {r['ereignisse']} Ereignissen, "
                            f"{r['vokabular']} Zeichen, Verlust {r['verlust']}, {r['sekunden']}s")
                else:
                    kurz = r.get("grund", "?")
                _log(f"Netz-Folgen ({wer}) – {kurz}")
                _protokoll("wache_training", f"Netz-Folgen trainiert ({wer})", ergebnis=kurz)
            except Exception as exc:
                self._sensor_fehler("netz-training", f"{type(exc).__name__}: {exc}")
            finally:
                self._netz_training = False

        t = threading.Thread(target=_lauf, daemon=True, name="wache-netzfolgen")
        t.start()
        if warten:
            t.join()
        return "Netz-Folgen-Training gestartet"

    def ml_zurueckdrehen(self) -> str:
        """Alle Isolation-Forest-Modelle auf die vorige Version, automatisches Lernen pausiert."""
        with self._lock:
            modelle = (self.detektor, self.datei_detektor, self.netz_detektor)
            texte = [_detektor_modul.zurueckdrehen(d.pfad) for d in modelle]
            for d in modelle:
                d.neu_laden_wenn_geaendert()
        _protokoll("wache", "ML-Modell zurückgedreht", ergebnis=" · ".join(texte))
        return "\n".join(texte)

    def lernen_zuruecksetzen(self) -> None:
        with self._lock:
            self.speicher.lernen_zuruecksetzen()
            self.detektor.zuruecksetzen()
            self.datei_detektor.zuruecksetzen()
            self.folgen.zuruecksetzen()
            self.netz_detektor.zuruecksetzen()
            self.netz_folgen.zuruecksetzen()
            self.neue_profile = 0
            self._seit_training = 0
            self._profile_spiegeln()
        _log("Lernen zurückgesetzt (Modell, Profile, Inventar)")
        _protokoll("wache", "Lernen zurückgesetzt – Modell, Profile und Inventar gelöscht")

    # --------------------------------------------------------------- Inventar

    def inventar_scannen(self) -> dict:
        eintraege = self.inventar.scan()
        bekannt = self.speicher.inventar_ids()
        erster = not bekannt
        ergebnis = self.speicher.inventar_abgleichen(eintraege)
        ergebnis["erster"] = erster
        ergebnis["sekunden"] = round(self.inventar.dauer, 2)
        ergebnis["zaehlung"] = dict(self.inventar.zaehlung)
        ergebnis["fehler"] = list(self.inventar.fehler)
        stunde = datetime.now().hour
        gelernt = 0
        for e in eintraege:
            if e.art != INV_PROZESS:
                continue
            if e.detail and e.detail.lower() in self._offene_pfade:
                continue                                  # unbeurteilter Alarm: nicht ins Profil
            self.speicher.profil_fortschreiben(e.name, exe=e.detail, eltern=e.extra.get("eltern", ""),
                                               nutzer=e.extra.get("nutzer", ""),
                                               mem=float(e.extra.get("mem_mb", 0.0)), stunde=stunde,
                                               aus_inventar=True)
            if e.extra.get("eltern"):
                self.speicher.profil_fortschreiben(e.extra["eltern"], stunde=stunde, kind=e.name)
            gelernt += 1
        ergebnis["profile"] = gelernt
        self._profile_spiegeln()
        self.letztes_inventar = ergebnis
        ereignisse: list[Ereignis] = []
        if erster:
            zusammen = ", ".join(f"{n} {art}" for art, n in sorted(ergebnis["zaehlung"].items()))
            ereignisse.append(Ereignis(SYSTEM, "inventory_baseline",
                                       f"Systeminventar aufgenommen: {ergebnis['gesamt']} Einträge ({zusammen})",
                                       extra={"erster": True, **ergebnis["zaehlung"]}))
        else:
            ereignisse = neue_als_ereignisse(eintraege, bekannt)
        if ereignisse:
            self.speicher.ereignisse_schreiben(ereignisse)
            neue_alarme = []
            for ev in ereignisse:
                neue_alarme.extend(self.regeln.pruefen(ev))
            if neue_alarme:
                self.speicher.alarme_schreiben(neue_alarme)
                self.alarme += len(neue_alarme)
                for cb in self.bei_alarm:
                    try:
                        cb(list(neue_alarme))
                    except Exception:
                        pass
        _protokoll("wache_inventar", "Systeminventar gescannt",
                   ergebnis=f"{ergebnis['gesamt']} Einträge, {ergebnis['neu']} neu, "
                            f"{ergebnis['verschwunden']} verschwunden, {ergebnis['sekunden']}s")
        return ergebnis

    def _inventar_anstossen(self, warten_s: float = 15.0) -> None:
        """Ein Dienst oder eine Aufgabe wurde laut Protokoll gerade installiert: Inventar gleich neu statt
        erst beim nächsten 6-Stunden-Lauf – R011 meldet dann mit allen Angaben. Höchstens einmal je Minute;
        kurz warten, bis die Installation fertig ist."""
        jetzt = time.time()
        if jetzt - self._inventar_angestossen < 60:
            return
        self._inventar_angestossen = jetzt
        t = threading.Timer(warten_s, self.inventar_starten)
        t.daemon = True
        t.start()

    def inventar_starten(self) -> bool:
        if self.inventar_laeuft:
            return False
        self.inventar_laeuft = True

        def _lauf():
            try:
                self.inventar_scannen()
            except Exception as exc:
                self.sensor_fehler.append(f"inventar: {exc!r}")
            finally:
                self.inventar_laeuft = False

        threading.Thread(target=_lauf, daemon=True, name="wache-inventar").start()
        return True

    # ---------------------------------------------------------------- Wartung

    def _wartungsschleife(self) -> None:
        while not self._stopp.wait(300):
            try:
                self.speicher.aufraeumen(self.e["aufbewahrung_tage"], self.e["max_ereignisse"])
                self.speicher.verdichten()
                self._profile_spiegeln()
                self.daempfung_aktualisieren()        # Urteile aus dem Hauptprogramm übernehmen
                for d in (self.detektor, self.datei_detektor, self.netz_detektor):
                    d.neu_laden_wenn_geaendert()
                self.netz_folgen.zeiger_pruefen()
                stunden = float(self.e["inventar_stunden"])
                if stunden > 0 and self.inventar.letzter_scan and \
                        time.time() - self.inventar.letzter_scan >= stunden * 3600:
                    self.inventar_starten()
            except Exception as exc:
                self.sensor_fehler.append(f"wartung: {exc!r}")

    # --------------------------------------------------------------- Zustand

    def stand(self) -> dict:
        laufzeit = time.time() - self.start_zeit if self.start_zeit else 0.0
        return {
            "laeuft": self.laeuft, "laufzeit": laufzeit, "verarbeitet": self.verarbeitet,
            "gespeichert": self.speicher.anzahl_ereignisse(), "alarme": self.alarme,
            "anomalien": self.anomalien, "profile": self.speicher.anzahl_profile(),
            "verdichtet": self.verdichtet,
            "gedaempft": {f"{r}·{s}": (self._gedaempft[(r, s)][0], self.gedaempft_zaehler.get((r, s), 0))
                          for r, s in sorted(self._gedaempft)},
            "neue_profile": self.neue_profile, "queue": self.queue.qsize(),
            "pro_sekunde": self.verarbeitet / laufzeit if laufzeit > 1 else 0.0,
            "sensoren": [{"name": t.sensor.name, "laeuft": t.laeuft, "ereignisse": t.ereignisse,
                          "fehler": t.fehler} for t in self.sensoren],
            "ml": self.detektor.stand(), "ml_dateien": self.datei_detektor.stand(),
            "alarm_statistik": self.speicher.alarm_statistik(),
            "ueberwacht": list(self.datei_sensor.ueberwacht),
            "inventar": {"zaehlung": self.speicher.inventar_zaehlung(), "laeuft": self.inventar_laeuft,
                         "zuletzt": self.inventar.letzter_scan, "dauer": round(self.inventar.dauer, 2),
                         **{k: v for k, v in self.letztes_inventar.items() if k in ("neu", "verschwunden", "profile")}},
            "seit_training": self._seit_training, "training_alle": self.e["training_alle"],
            "fehler": list(self.sensor_fehler[-5:]),
            "gestoert": self.gestoert, "verworfen": self.verworfen,
            "dl": {**self.folgen.stand(), "an": bool(self.e.get("dl", True)),
                   "alarm": bool(self.e.get("dl_alarm", False)), "auffaellig_seit_start": self.dl_auffaellig,
                   "training_laeuft": self._folgen_training},
            "programme": self.absolvierung.stand(),
            "netz": {"an": bool(self.e.get("netz", True)), "alarm": bool(self.e.get("netz_alarm", False)),
                     "ml": self.netz_detektor.stand(), "dl": self.netz_folgen.stand(),
                     "auffaellig_seit_start": self.netz_auffaellig, "besitzer_offen": len(self._besitzer_offen),
                     "training_laeuft": self._netz_training},
        }
