"""Der Motor: sammeln → anreichern → bewerten → Regeln → speichern → lernen.

Läuft in eigenen Threads, kennt keine Oberfläche. Wer wissen will, was los ist,
fragt `stand()`; wer bei Alarmen gerufen werden will, hängt sich an `bei_alarm`.

Lernen ohne Zutun: erstes Training, sobald
`min_training` Ereignisse da sind, danach alle `training_alle` Ereignisse –
auf den letzten 7 Tagen. Vor jedem Lauf werden die Profile in die Merkmale
gespiegelt und das „harmlos“-Feedback übernommen. Jeder Lauf steht in
Wache/training.log und im Aktions-Protokoll.

Verdichten und Dämpfen (sonst erzeugt derselbe Vorgang Hunderte offene
Alarme): Regel + Subjekt ist der Schlüssel. Kommt derselbe Alarm
innerhalb des Cooldowns wieder, wird am ersten der Zähler hochgesetzt („×37“)
statt eine neue Zeile zu schreiben – und niemand wird erneut geweckt. Wurde ein
Paar `daempfen_ab`-mal als harmlos beurteilt (und nie als echt), meldet der Motor
es gar nicht mehr, zählt es nur noch (`gedaempft` im Stand). Ist das Subjekt
schon als „bekannt“ eingetragen, kommt der Alarm eine Stufe leiser – erst leise,
dann laut.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from queue import Empty, Queue

from . import MODELL_ORDNER, TRAINING_LOG, ordner_anlegen
from .detektor import Detektor
from .ereignisse import DATEI, NETZ, PROZESS, SYSTEM, Alarm, Ereignis, Schwere
from .inventar import Inventar, PROZESS as INV_PROZESS, neue_als_ereignisse
from .justierung import einstellungen
from .merkmale import Burst, DateiMerkmale, ProzessMerkmale
from .regeln import MENGENREGELN, Regelwerk
from .sensoren import DateiSensor, NetzSensor, ProzessSensor, SensorThread
from .speicher import Speicher
from .vollscan import Baseline


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
        self.regeln = Regelwerk(stumm=set(self.e.get("stumme_regeln") or []))
        self.burst = Burst()
        self.datei_burst = Burst()
        self.inventar = Inventar(mit_aufgaben=self.e["inventar_aufgaben"])
        self.inventar_laeuft = False
        self.letztes_inventar: dict = {}

        self.prozess_sensor = ProzessSensor(self.e["prozess_intervall"])
        self.netz_sensor = NetzSensor(self.e["netz_intervall"])
        self.datei_sensor = DateiSensor(self.e.get("pfade"), self.e.get("endungen"))
        self.sensoren: list[SensorThread] = []

        self._stopp = threading.Event()
        self._worker: threading.Thread | None = None
        self._wartung: threading.Thread | None = None
        self._lock = threading.RLock()
        self._zuletzt: dict[tuple[str, str], tuple[str, float]] = {}   # (regel, subjekt) → (alarm-id, zeit)
        self._gedaempft: dict[tuple[str, str], int] = {}
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

        self.bei_alarm: list = []                     # Callbacks(list[Alarm])
        self.bei_training: list = []                  # Callbacks(dict)

        self._profile_spiegeln()
        self.detektor.feedback(self.speicher.fehlalarm_prozesse())
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
                         for s in (self.prozess_sensor, self.netz_sensor, self.datei_sensor)]
        for t in self.sensoren:
            t.start()
        self._worker = threading.Thread(target=self._schleife, daemon=True, name="wache-motor")
        self._worker.start()
        self._wartung = threading.Thread(target=self._wartungsschleife, daemon=True, name="wache-wartung")
        self._wartung.start()
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
        _protokoll("wache", "Wache gestoppt",
                   ergebnis=f"{self.verarbeitet} Ereignisse, {self.alarme} Alarme")

    def beenden(self) -> None:
        self.stop()
        self.detektor.speichern()
        self.datei_detektor.speichern()
        self.speicher.schliessen()

    @property
    def laeuft(self) -> bool:
        return bool(self._worker and self._worker.is_alive() and not self._stopp.is_set())

    def einstellungen_uebernehmen(self) -> None:
        """Nach einer Justierung: Schwelle, Cooldown, stumme Regeln live setzen."""
        self.e = einstellungen()
        self.detektor.schwelle = self.datei_detektor.schwelle = float(self.e["schwelle"])
        self.regeln.stumm = set(self.e.get("stumme_regeln") or [])
        self.daempfung_aktualisieren()

    def daempfung_aktualisieren(self) -> None:
        """Nach jedem Urteil: welche (Regel, Subjekt)-Paare sind oft genug harmlos?"""
        try:
            self._gedaempft = self.speicher.gedaempft(int(self.e.get("daempfen_ab", 3)))
        except Exception:
            self._gedaempft = {}

    # --------------------------------------------------------------- Pipeline

    def _aufnehmen(self, liste: list[Ereignis]) -> None:
        for e in liste:
            try:
                self.queue.put(e, timeout=1.0)
            except Exception:
                pass

    def _sensor_fehler(self, name: str, text: str) -> None:
        self.sensor_fehler.append(f"{name}: {text.strip().splitlines()[-1][:160]}")
        del self.sensor_fehler[:-20]

    def _schleife(self) -> None:
        stapel: list[Ereignis] = []
        alarme: list[Alarm] = []
        zuletzt = time.time()
        while not self._stopp.is_set():
            try:
                e = self.queue.get(timeout=0.5)
            except Empty:
                e = None
            if e is not None:
                self._anreichern(e)
                if self.e["ml"]:
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
                alarme.extend(self.regeln.pruefen(e))
                alarme[:] = self._verdichten(alarme, e)
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
                self.speicher.ereignisse_schreiben(stapel)
                stapel.clear()
                zuletzt = jetzt
            if alarme:
                self.speicher.alarme_schreiben(alarme)
                self.alarme += len(alarme)
                for cb in self.bei_alarm:
                    try:
                        cb(list(alarme))
                    except Exception:
                        pass
                alarme.clear()
            if (self.e["ml"] and self.e["lernen"] and (
                    self._seit_training >= self.e["training_alle"]
                    or (not self.detektor.trainiert and self._seit_training >= self.e["min_training"]))):
                self._seit_training = 0
                self._vielleicht_trainieren()
        if stapel:
            self.speicher.ereignisse_schreiben(stapel)
        if alarme:
            self.speicher.alarme_schreiben(alarme)

    def _anreichern(self, e: Ereignis) -> None:
        x = e.extra or {}
        if e.kategorie == DATEI:
            burst = self.datei_burst.beobachten((x.get("ordner") or "").lower(), e.zeit)
        else:
            burst = self.burst.beobachten(e.prozess, e.zeit)
        e.extra = {**x, "burst": round(burst, 4)}
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
            e.extra = {**e.extra, "erstmals": True}
            if e.schwere < Schwere.NIEDRIG:
                e.schwere = Schwere.NIEDRIG

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

    def _verdichten(self, alarme: list[Alarm], e: Ereignis) -> list[Alarm]:
        """Gleicher Alarm (Regel + Subjekt) innerhalb des Cooldowns → Zähler am ersten hoch,
        keine neue Zeile. Nur bei Mengen-Regeln: gedämpfte Paare werden bloß gezählt, bekannte
        Subjekte kommen eine Stufe leiser. Muster-Regeln (R001 …) bleiben immer laut."""
        out: list[Alarm] = []
        bekannt: bool | None = None
        for a in alarme:
            key = (a.regel, a.subjekt.lower())
            menge = a.regel in MENGENREGELN
            if menge and key in self._gedaempft:
                self.gedaempft_zaehler[key] = self.gedaempft_zaehler.get(key, 0) + 1
                continue
            alt = self._zuletzt.get(key)
            if alt and 0 <= a.zeit - alt[1] < self.e["cooldown"]:
                self.speicher.alarm_zaehlen(alt[0], a.zeit)
                self._zuletzt[key] = (alt[0], a.zeit)     # gleitend: solange es weitergeht, EIN Alarm
                self.verdichtet += 1
                continue
            if menge and bekannt is None:
                try:
                    bekannt = bool(self.speicher.bekannt_fuer(e))
                except Exception:
                    bekannt = False
            if menge and bekannt and a.schwere > Schwere.NIEDRIG:
                a.schwere = Schwere(int(a.schwere) - 1)
            self._zuletzt[key] = (a.id, a.zeit)
            out.append(a)
        if len(self._zuletzt) > 2000:
            self._zuletzt.clear()
        return out

    # --------------------------------------------------------------- Training

    def _profile_spiegeln(self) -> None:
        self.prozess_merkmale.profile_setzen(self.speicher.profile(), self.speicher.software_pfade())

    def _vielleicht_trainieren(self) -> dict:
        n = self.speicher.anzahl_ereignisse()
        if n < self.e["min_training"]:
            return {"ok": False, "grund": f"nur {n} Ereignisse, {self.e['min_training']} nötig"}
        return self.trainieren()

    def trainieren(self, wer: str = "Wache") -> dict:
        with self._lock:
            self._profile_spiegeln()
            self.detektor.feedback(self.speicher.fehlalarm_prozesse())
            alle = self.speicher.ereignisse_seit(time.time() - 7 * 86400, limit=20_000)
            dateien = [x for x in alle if x.kategorie == DATEI]
            andere = [x for x in alle if x.kategorie != DATEI]
            ergebnis = self.detektor.trainieren(andere)
            if len(dateien) >= self.e["min_training_dateien"]:
                ergebnis["dateien"] = self.datei_detektor.trainieren(dateien)
            else:
                ergebnis["dateien"] = {"ok": False, "grund": f"nur {len(dateien)} Datei-Ereignisse, "
                                                             f"{self.e['min_training_dateien']} nötig"}
        self.speicher.meta_setzen("training_zuletzt", str(time.time()))
        kurz = (f"Prozesse: {ergebnis.get('ereignisse', 0)} Ereignisse in {ergebnis.get('sekunden', 0)}s "
                f"(Lauf {ergebnis.get('laeufe', 0)}) · Dateien: "
                + (f"{ergebnis['dateien'].get('ereignisse')} Ereignisse" if ergebnis["dateien"].get("ok")
                   else ergebnis["dateien"].get("grund", "?")))
        _log(f"Training ({wer}) – {kurz}")
        _protokoll("wache_training", f"Modell nachtrainiert ({wer})", ergebnis=kurz)
        for cb in self.bei_training:
            try:
                cb(ergebnis)
            except Exception:
                pass
        return ergebnis

    def lernen_zuruecksetzen(self) -> None:
        with self._lock:
            self.speicher.lernen_zuruecksetzen()
            self.detektor.zuruecksetzen()
            self.datei_detektor.zuruecksetzen()
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
            "gedaempft": {f"{r}·{s}": (self._gedaempft[(r, s)], self.gedaempft_zaehler.get((r, s), 0))
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
        }
