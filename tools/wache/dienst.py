"""Der Hintergrund-Prozess: `main.py --wache`.

Hier laufen Motor (Sensoren, Regeln, Modell), Wecker (Alarme → Persönlichkeit),
Tray (Schild neben der Uhr) und Kugel (Schwebekugel auf dem Desktop) zusammen.

Threads: Qt will den Hauptthread – dort laufen Kugel UND Tray in einer
QApplication (oberflaeche.py). Die
asyncio-Schleife für Gespräche der Persönlichkeit läuft in einem eigenen
Thread, der Motor in seinen Sensor-Threads. Was aus diesen Threads an die
Oberfläche will, geht über `oberflaeche.im_gui`. Beendet wird über das
Tray-Menü, /wache stop (Stopp-Datei) oder Strg+C. Ohne Qt (fehlt PySide6)
läuft die Wache kopflos weiter – ohne Schild und Kugel.

main.py stellt: `wecken(alarme) -> str` (Weckruf, Bericht zurück),
`chat(text, bilder) -> (antwort, neue_bilder, todo_text)` (Gespräch über die Kugel),
`chat_neu()` (Kugel-Gespräch leeren),
den Programm-Ordner und den Namen der aktiven Persönlichkeit.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import BERICHTE, DB_PFAD, ordner_anlegen, zugang
from .justierung import einstellungen
from .motor import Motor
from .wecker import Wecker


class Dienst:
    def __init__(self, wecken, install_ordner: Path, *, chat=None, chat_neu=None, name: str = "Persönlichkeit",
                 nutzer: str = "du", mit_tray: bool = True, mit_kugel: bool = True):
        ordner_anlegen()
        self.install = install_ordner
        self.name = name
        self.nutzer = nutzer
        self.motor = Motor(DB_PFAD)
        zugang.motor = self.motor
        self.loop = asyncio.new_event_loop()
        self.wecker = Wecker(self.motor.speicher, wecken, self.loop)
        self.motor.bei_alarm.append(self.wecker.alarm)
        self.motor.bei_alarm.append(self._tray_hinweis)
        self.pausiert = False
        self.kugel_erlaubt = mit_kugel
        self._kugel_schalter = mit_kugel      # --ohne-kugel gilt, bis der Nutzer sie selbst zeigt
        self._ende = threading.Event()
        self._einstellungs_stand = ""
        self._chat = chat
        self._chat_neu = chat_neu
        self.tray = None
        self.kugel = None
        self.gui = None
        try:
            from . import oberflaeche
            self.gui = oberflaeche.anwendung()
        except Exception as exc:
            print(f"Oberfläche nicht verfügbar (PySide6?): {exc} – Wache läuft ohne Schild und Kugel.")
        if mit_tray and self.gui is not None:
            from .tray import Tray
            self.tray = Tray(stand=self.stand, aktion=self.aktion, install_ordner=install_ordner,
                             berichte=BERICHTE, extra_menue=self._extra_menue)

    # ------------------------------------------------------------- Zustand

    def stand(self) -> dict:
        # Beim Herunterfahren ist die Datenbank schon zu – das Tray-Menü fragt
        # aber weiter nach dem Stand, solange es offen ist. Dann ohne Datenbank.
        if self._ende.is_set():
            return {"farbe": "grau", "zahl": 0, "text": "🛡 NemiCLI-Wache · beendet",
                    "pausiert": True, "kugel": False}
        try:
            st = self.motor.speicher.alarm_statistik()
        except Exception:
            return {"farbe": "grau", "zahl": 0, "text": "🛡 NemiCLI-Wache · beendet",
                    "pausiert": True, "kugel": False}
        offen, echt = st.get("offen", 0), st.get("echt", 0)
        if self.pausiert:
            farbe, text = "grau", "🛡 NemiCLI-Wache · pausiert"
        elif echt:
            farbe, text = "rot", f"🛡 NemiCLI-Wache · {echt} ECHTE Auffälligkeit(en)!"
        elif offen:
            farbe, text = "gelb", f"🛡 NemiCLI-Wache · {offen} offene Alarme"
        else:
            m = self.motor.stand()
            text = (f"🛡 NemiCLI-Wache · ruhig · {m['gespeichert']} Ereignisse · "
                    f"Modell {'trainiert' if m['ml']['trainiert'] else 'lernt noch'}")
            farbe = "gruen"
        return {"farbe": farbe, "zahl": offen + echt, "text": text, "pausiert": self.pausiert,
                "kugel": self.kugel_erlaubt}

    def _tray_hinweis(self, alarme) -> None:
        if self.tray is None:
            return
        hohe = [a for a in alarme if int(a.schwere) >= 3]
        if hohe:
            a = hohe[0]
            mehr = f" (+{len(hohe) - 1} weitere)" if len(hohe) > 1 else ""
            self.tray.melden(f"Wache: {a.schwere.label}", f"{a.titel}{mehr}")
        self.tray.aktualisieren()

    def kugel_sichtbar(self) -> bool:
        """Kugel nur, wenn erlaubt, nicht pausiert und kein NemiCLI-Terminal offen ist."""
        if not self.kugel_erlaubt or self._ende.is_set():
            return False
        try:
            import kontextmenue
            if kontextmenue.nemicli_laeuft():
                return False
        except Exception:
            pass
        return True

    # ------------------------------------------------------------- Aktionen

    def _extra_menue(self) -> list[tuple]:
        return [
            (lambda: "Kugel verstecken" if self.kugel_erlaubt else "Kugel zeigen", lambda: self.aktion("kugel")),
            ("Mit ihr reden (Kugel-Fenster)", lambda: self.aktion("chat")),
        ]

    def aktion(self, name: str) -> None:
        if name == "inventar":
            self.motor.inventar_starten()
        elif name == "training":
            threading.Thread(target=self.motor.trainieren, args=("Nutzer",), daemon=True).start()
        elif name == "pause":
            if self.pausiert:
                self.motor.start()
                self.pausiert = False
            else:
                self.motor.stop()
                self.pausiert = True
            if self.tray:
                self.tray.aktualisieren()
        elif name == "kugel":
            self.kugel_erlaubt = not self.kugel_erlaubt
            self._kugel_schalter = True
            try:
                from .justierung import speichern
                speichern({"kugel": self.kugel_erlaubt})
            except Exception:
                pass
        elif name == "chat":
            if self.kugel is not None:
                self.kugel_erlaubt = self._kugel_schalter = True
                self.kugel.im_gui(self.kugel.panel_umschalten)
        elif name == "beenden":
            self.beenden()

    def nemicli_oeffnen(self) -> None:
        cmd = self.install / "nemicli.cmd"
        try:
            if getattr(sys, "frozen", False):
                # NemiCLI im eigenen Terminal-Fenster (NemiCLI.exe ohne Auftrag öffnet es);
                # fehlt die fensterlose exe, die Terminal-exe in einer neuen Konsole.
                from paths import exe_mit_fenster, exe_ohne_fenster
                w = exe_ohne_fenster()
                if w.name.lower() == "nemicli.exe":
                    subprocess.Popen([str(w)], cwd=str(Path.home()))
                else:
                    subprocess.Popen([str(exe_mit_fenster())], cwd=str(Path.home()),
                                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            elif cmd.exists():
                subprocess.Popen(["cmd", "/c", "start", "", str(cmd)], cwd=str(Path.home()))
            else:
                subprocess.Popen(["cmd", "/c", "start", "", "python", str(self.install / "main.py")],
                                 cwd=str(Path.home()))
        except OSError:
            pass

    def screenshot(self) -> Path | None:
        """Bildschirmfoto für die Kugel – wird aus dem GUI-Thread aufgerufen (Knopf im Fenster)."""
        from datetime import datetime
        from . import _ROOT, oberflaeche
        pfad = _ROOT / "Bilder" / "Screenshots" / f"screen_{datetime.now():%Y%m%d_%H%M%S}.png"
        # Kugel und Fenster kurz weg, damit sie nicht selbst drauf sind
        if self.kugel is not None:
            self.kugel.verstecken(True)
            time.sleep(0.35)
        try:
            return oberflaeche.bildschirmfoto(pfad)
        finally:
            if self.kugel is not None:
                self.kugel.verstecken(False)

    def beenden(self) -> None:
        if self._ende.is_set():
            return
        self._ende.set()
        # Erst das Schild weg, dann die Datenbank zu – andersherum griff das
        # Tray-Menü noch auf die geschlossene Datenbank zu.
        if self.tray:
            self.tray.stop()
        if self.gui is not None:
            from . import oberflaeche
            oberflaeche.beenden()
        try:
            self.motor.beenden()
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        zugang.lock_loeschen()

    # ------------------------------------------------------------- Laufen

    def _waechter(self) -> None:
        """Stopp-Datei jede Sekunde, Einstellungen und Lebenszeichen alle 10 s."""
        takt = 0
        while not self._ende.wait(1):
            if zugang.stopp_gewuenscht():
                try:
                    zugang.STOPP.unlink(missing_ok=True)
                except OSError:
                    pass
                self.beenden()
                return
            takt += 1
            if takt % 10:
                continue
            self._einstellungen_pruefen()
            zugang.lock_schreiben()          # Lebenszeichen

    def _einstellungen_pruefen(self) -> None:
        try:
            e = einstellungen()
            stand = repr(sorted((k, repr(v)) for k, v in e.items()))
            if stand != self._einstellungs_stand:
                self._einstellungs_stand = stand
                self.motor.einstellungen_uebernehmen()
                self.kugel_erlaubt = bool(e.get("kugel", True)) and self._kugel_schalter
        except Exception:
            pass

    def _asyncio_thread(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def laufen(self) -> int:
        zugang.lock_schreiben()
        self.kugel_erlaubt = bool(einstellungen().get("kugel", True)) and self.kugel_erlaubt
        threading.Thread(target=self._asyncio_thread, daemon=True, name="wache-asyncio").start()
        self.motor.start()
        threading.Thread(target=self._waechter, daemon=True, name="wache-waechter").start()
        try:
            if self.tray is not None:
                self.tray.starten()
            if self._chat is not None and self.gui is not None:
                try:
                    from .kugel import Kugel
                    self.kugel = Kugel(stand=self.stand, chat=self._chat, schleife=self.loop,
                                       sichtbar=self.kugel_sichtbar, name=self.name, nutzer=self.nutzer,
                                       nemicli_oeffnen=self.nemicli_oeffnen, screenshot=self.screenshot,
                                       chat_neu=self._chat_neu)
                except Exception as exc:
                    print(f"Kugel nicht verfügbar: {exc}")
                    self.kugel = None
            if self.gui is not None:
                self.gui.exec()              # blockiert bis beenden()
            else:
                while not self._ende.wait(1):
                    pass
        except KeyboardInterrupt:
            pass
        finally:
            self.beenden()
        return 0
