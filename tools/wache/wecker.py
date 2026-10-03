"""Der Wecker: Alarme sammeln, bündeln, die Persönlichkeit wecken.

Die Wache meldet Alarme im Sekundentakt – ein Prozess-Schwall kann zwanzig
auf einmal liefern. Für jeden einzeln ein Gespräch zu starten wäre teuer
(Tokens) und nutzlos (zwanzig halbe Bilder statt eines ganzen). Deshalb:

  1. Alarm kommt an → passt er zur Weck-Stufe (trigger_ab / trigger_ml)?
  2. Ja → in den Korb. Der Korb wartet `trigger_buendel_s` Sekunden auf Nachzügler.
  3. Dann EIN Auftrag mit allen Alarmen im Korb an die aktive Persönlichkeit.
  4. Danach `trigger_pause_s` Ruhe; höchstens `trigger_max_pro_tag` Wecks am Tag.
     Was in der Pause ankommt, wird gesammelt und beim nächsten Weck mitgegeben –
     nichts geht verloren, es steht ohnehin in der Datenbank.

Was die Persönlichkeit mit den Alarmen tun soll, steht in
DATEN/Agenten/wache_alarm.md (der Nutzer kann es ändern). Fehlt die Datei,
wird sie mit einer Vorgabe angelegt.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable

from . import ANLEITUNG
from .ereignisse import Alarm
from .justierung import einstellungen, trigger_stufe

VORGABE_ANLEITUNG = """\
# Wache: ein Alarm – was du tust

Du wurdest von deiner Systemwache geweckt. Unten stehen ein oder mehrere
Alarme. Niemand sitzt gerade vor dem Bildschirm; du arbeitest allein und
schreibst am Ende einen Bericht. Du kannst lesen und abfragen, aber nichts
am System ändern.

1. **Verstehen.** Für jeden Alarm: `wache_alarme` mit der `id` zeigt dir das
   Ereignis dahinter (Prozess, Pfad, Elternprozess, Ziel, Kommandozeile) und
   unter `herkunft:` bereits die geprüfte Signatur samt Ort. Diese Zeile ist
   gemessen, nicht geraten – urteile nach ihr, nicht nach dem Prozessnamen.
   Steht dort „NICHT signiert“ oder „kein Systemordner“ bei einem Namen, den du
   für Windows hältst, ist das der Alarm und nicht das Drumherum. Fehlt die
   Zeile, ist der Pfad nicht lesbar (ohne Adminrechte bei fremden Prozessen
   normal) – dann erst selbst nachsehen.
2. **Nachschauen.** Mit `abfragen` prüfst du, was wirklich läuft:
   `was: prozess, pid: <pid>` (Pfad, Start, Eltern, Signatur, Verbindungen),
   `was: verbindungen, pid: <pid>`, `was: datei, pfad: <pfad>`. `wache_ereignisse` zeigt
   dir, was der Prozess sonst so tut. Behaupte nie eine Verbindung zwischen
   zwei Alarmen, nur weil sie zeitlich nah beieinander liegen – entweder du
   findest den gemeinsamen Auslöser (Installationsprotokoll, gemeinsamer
   Elternprozess), oder es sind zwei getrennte Vorgänge. Bei Shell- und
   Paket-Alarmen legt die Wache dir das AppX-Protokoll des Zeitfensters
   ungefragt dazu; steht dort ein Paketname, ist das deine Ursache.
   Bei Systemprogrammen steht unter „Windows Update in den 24 h davor“, was
   Windows Update laut Protokoll installiert hat (KB-Nummer, Uhrzeit). „Nach
   einem Update“ ist als Begründung NUR erlaubt, wenn dort ein passendes Update
   steht – nenne es dann mit KB-Nummer. Steht dort „nichts installiert“, ist
   diese Erklärung nicht belegt: dann nicht behaupten, sondern anders prüfen
   oder `gesehen` urteilen.
3. **Einordnen.** Was ist auf DIESEM Rechner normal? Wenn es eine Datei
   `Agenten/systemwache.md` gibt, steht es dort (Virenschutz, VPN, eigene
   Dienste) – lies sie zuerst. Ein neuer Autostart-Eintrag nach einer
   Installation, die der Nutzer selbst gemacht hat, ist harmlos.
   Schutzsoftware (Virenschutz, Firewall – ihr Ordner steht im Security Center)
   untersuchst du nicht: keine Signaturen, Prozesse, Dateien oder Befehle dazu.
   Zeigt die `herkunft:`-Zeile eine gültige Signatur ihres Herstellers, ist sie
   bekannt; sonst `gesehen` und dem Nutzer melden.
   Verdächtig bleibt: Skript-Host aus Office, Programme aus Temp/Downloads mit
   zufälligem Namen, Verbindungen auf 4444/6667/9001, unsignierte Binärdateien
   an ungewöhnlichen Orten, Systemprozesse mit falscher Herkunft.
   Schau bei powershell.exe/cmd.exe IMMER auf die Eltern: `python.exe` oder
   `pythonw.exe` aus dem NemiCLI-venv mit einer Kommandozeile, die mit
   `$ProgressPreference='SilentlyContinue'; $OutputEncoding …` beginnt, ist
   NemiCLI selbst (ein vom Nutzer bestätigter `befehl`). Nenne es
   dann auch so – nicht raten, welches Programm es sein könnte. Ein Urteil
   „harmlos“ mit falscher Begründung ist schlimmer als „gesehen“.
4. **Urteilen.** Jeder Alarm bekommt ein Urteil mit `wache_bewerten`. Bei `harmlos`
   gib IMMER eine `bezeichnung` mit – WAS das ist, in drei bis acht Worten
   („Microsoft Store, signiert von Microsoft“, „Akamai-CDN für Windows-Updates“).
   Das merkt sich die Wache; beim nächsten Alarm zum selben Prozess/Ziel steht
   es als „bekannt: …“ gleich dabei, und du musst nichts zweimal prüfen.
   Steht bei einem Alarm schon „bekannt: …“ und nichts ist anders (gleicher Pfad,
   gleiche Eltern), bewerte ihn direkt harmlos – ohne neue Abfragen.
   Mehrere gleiche Alarme (dieselbe Regel, dasselbe Programm) bewertest du in
   EINEM Aufruf: `id="a,b,c"` oder `regel="R008" subjekt="chrome.exe"` – nicht
   jeden einzeln. „×37“ an einem Alarm heißt: kam 37-mal, ist aber EIN Eintrag.
   Sonst:
   `harmlos` (mit Begründung – das dämpft künftige Meldungen desselben
   Prozesses), `echt` (bleibt offen, der Nutzer muss es sehen) oder `gesehen`
   (unklar, beobachten).
5. **Justieren – nur mit gutem Grund.** Schlägt eine Regel wiederholt auf
   dieselbe harmlose Sache an, darfst du sie mit `wache_justieren` stumm
   schalten oder die ML-Schwelle in den Grenzen des Nutzers anheben. Immer
   mit Begründung; es steht im Protokoll und lässt sich zurücknehmen.
6. **Bericht.** Deine letzte Antwort ist der Bericht. Erste Zeile: `✅ Alles
   harmlos` oder `⚠️ n echte Auffälligkeiten`. Dann pro Alarm zwei Zeilen: was
   es war, warum dein Urteil. Kurz, klar, ohne Floskeln.
7. **Zeig es auf dem Desktop.** Gab es etwas Echtes: `kugel` mit
   stimmung="ernst", bewegung="huepfen", sagen="…" und wichtig=true – der Nutzer
   soll es sehen, ohne das Terminal zu öffnen. War alles harmlos: höchstens
   stimmung="froh", keine Blase (die Kugel ist grün, das reicht).
"""


def anleitung_text() -> str:
    try:
        if not ANLEITUNG.exists():
            ANLEITUNG.parent.mkdir(parents=True, exist_ok=True)
            ANLEITUNG.write_text(VORGABE_ANLEITUNG, encoding="utf-8")
        return ANLEITUNG.read_text(encoding="utf-8")
    except OSError:
        return VORGABE_ANLEITUNG


def auftrag_text(alarme: list[Alarm], speicher, name: str) -> str:
    """Die Nachricht, mit der die Persönlichkeit geweckt wird."""
    zeilen = [f"[Weckruf deiner Systemwache – {time.strftime('%d.%m.%Y %H:%M')}. "
              f"{len(alarme)} Alarm(e). Es ist niemand da, der antwortet oder etwas freigibt: "
              "du kannst lesen, abfragen, bewerten und in Grenzen justieren. Deine LETZTE "
              "Antwort wird als Bericht gespeichert.]", "",
              anleitung_text().strip(), "", "## Die Alarme", ""]
    for a in alarme:
        zeilen.append(f"### Alarm {a.id} · {a.schwere.label} · {a.regel}"
                      + (f" · Score {a.score:.3f}" if a.score else ""))
        zeilen.append(f"{a.titel}")
        zeilen.append(a.text)
        try:
            ev = speicher.ereignis(a.ereignis_id) if a.ereignis_id else None
        except Exception:
            ev = None
        if ev is not None:
            details = [f"Ereignis {ev.id}: {ev.kurz()}"]
            try:
                from .werkzeuge import bekannt_hinweise
                for h in bekannt_hinweise(speicher, ev):
                    details.append("  ⭑ " + h + " – wenn nichts Neues dazukommt, direkt harmlos bewerten.")
            except Exception:
                pass
            for feld in ("prozess", "pid", "eltern", "nutzer", "exe", "cmdline", "ziel", "zielport", "datei"):
                w = getattr(ev, feld)
                if w:
                    details.append(f"  {feld}: {str(w)[:300]}")
            zeilen.extend(details)
        zeilen.append("")
    return "\n".join(zeilen)


class Wecker:
    """Sammelt Alarme und ruft `wecken(alarme)` – eine Coroutine, die main.py stellt."""

    def __init__(self, speicher, wecken: Callable[[list[Alarm]], Awaitable[str]],
                 schleife: asyncio.AbstractEventLoop):
        self.speicher = speicher
        self._wecken = wecken
        self._loop = schleife
        self._lock = threading.Lock()
        self._korb: list[Alarm] = []
        self._timer: threading.Timer | None = None
        self._zuletzt = 0.0
        self._heute = time.strftime("%Y-%m-%d")
        self._heute_n = 0
        self.laeuft = False
        self.geweckt = 0
        self.letzter_bericht = ""
        self.ausgelassen = 0

    def passt(self, a: Alarm, e: dict) -> bool:
        if a.quelle == "ml":
            return bool(e.get("trigger_ml", True)) and trigger_stufe(e["trigger_ab"]) <= 4
        return int(a.schwere) >= trigger_stufe(e["trigger_ab"])

    def alarm(self, alarme: list[Alarm]) -> None:
        """Vom Motor aus einem Thread gerufen."""
        e = einstellungen()
        passende = [a for a in alarme if self.passt(a, e)]
        if not passende:
            return
        with self._lock:
            self._korb.extend(passende)
            if self._timer is None:
                warten = float(e.get("trigger_buendel_s", 45))
                rest_pause = self._zuletzt + float(e.get("trigger_pause_s", 600)) - time.time()
                if rest_pause > 0:
                    warten = max(warten, rest_pause)
                self._timer = threading.Timer(warten, self._feuern)
                self._timer.daemon = True
                self._timer.start()

    def _feuern(self) -> None:
        with self._lock:
            self._timer = None
            alarme, self._korb = self._korb, []
        if not alarme:
            return
        tag = time.strftime("%Y-%m-%d")
        if tag != self._heute:
            self._heute, self._heute_n = tag, 0
        e = einstellungen()
        if self._heute_n >= int(e.get("trigger_max_pro_tag", 24)):
            self.ausgelassen += len(alarme)
            return
        if self.laeuft:                          # gerade schon ein Gespräch – nachlegen
            with self._lock:
                self._korb = alarme + self._korb
                self._timer = threading.Timer(30, self._feuern)
                self._timer.daemon = True
                self._timer.start()
            return
        self._heute_n += 1
        self._zuletzt = time.time()
        self.laeuft = True
        fut = asyncio.run_coroutine_threadsafe(self._ausfuehren(alarme), self._loop)
        fut.add_done_callback(lambda _f: setattr(self, "laeuft", False))

    async def _ausfuehren(self, alarme: list[Alarm]) -> None:
        try:
            for a in alarme:
                if a.status == "offen":
                    self.speicher.alarm_urteil(a.id, "gesehen", "Wache", "an die Persönlichkeit übergeben")
            self.letzter_bericht = await self._wecken(alarme) or ""
            self.geweckt += 1
        except Exception as exc:
            self.letzter_bericht = f"Weckruf fehlgeschlagen: {exc}"

    def offen(self) -> int:
        with self._lock:
            return len(self._korb)
