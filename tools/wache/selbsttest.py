"""Selbsttest der Wache: Pipeline, Regeln und Modell – ohne Wartezeit, ohne echte Daten.

Erzeugt synthetischen Alltag (sieben Tage Prozesse, Verbindungen, Dateien) plus
sieben klare Angriffsmuster, trainiert darauf und prüft:

  1. Fehlalarmquote auf ungesehenem Normalverkehr ≈ eingestellte Kontamination
  2. Angriffsmuster: Regel und/oder Modell schlagen an
  3. Modell speichert und lädt identisch
  4. Feedback („harmlos“) dämpft den Score
  5. Dateimodell: Alltag ruhig, Fremdkörper laut

Fester Zeitanker (15.06.2026 14:30): die Tageszeit ist ein Merkmal – an der
Wanduhr aufgehängt liefe der Test zu jeder Stunde anders.

Aufruf: /wache selbsttest oder  python -c "import wache.selbsttest as S; print(S.laufen())"
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime
from pathlib import Path

from .detektor import Detektor
from .ereignisse import DATEI, NETZ, PROZESS, Ereignis, Schwere
from .merkmale import Burst, DateiMerkmale, ProzessMerkmale
from .regeln import Regelwerk
from .speicher import Speicher

ANKER = datetime(2026, 6, 15, 14, 30).timestamp()
_NORMAL = [("chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe", "explorer.exe"),
           ("code.exe", r"C:\Users\test\AppData\Local\Programs\Microsoft VS Code\Code.exe", "explorer.exe"),
           ("python.exe", r"C:\Python312\python.exe", "code.exe"),
           ("svchost.exe", r"C:\Windows\System32\svchost.exe", "services.exe"),
           ("explorer.exe", r"C:\Windows\explorer.exe", "userinit.exe"),
           ("OneDrive.exe", r"C:\Users\test\AppData\Local\Microsoft\OneDrive\OneDrive.exe", "explorer.exe"),
           ("Teams.exe", r"C:\Users\test\AppData\Local\Microsoft\Teams\current\Teams.exe", "explorer.exe"),
           ("git.exe", r"C:\Program Files\Git\cmd\git.exe", "code.exe")]
_ZIELE = ["11.22.33.1", "11.22.33.2", "11.22.33.3", "11.22.33.5"]
_PORTS = [443] * 16 + [80, 80, 8080, 53]
_SELTEN = [(f"tool{i}.exe", rf"C:\Program Files\Tools\tool{i}.exe", "explorer.exe") for i in range(30)]
_DOWNLOADS = r"C:\Users\test\Downloads"
_TEMP = r"C:\Users\test\AppData\Local\Temp"
_STARTUP = r"C:\Users\test\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"


def _stempel(burst: Burst, e: Ereignis, key: str) -> Ereignis:
    e.extra = {**(e.extra or {}), "burst": round(burst.beobachten(key, e.zeit), 4)}
    return e


def _prozess(name, exe, eltern, ts, cmd="", nutzer="test\\user") -> Ereignis:
    return Ereignis(PROZESS, "process_start", f"Prozess gestartet: {name}", zeit=ts, prozess=name,
                    eltern=eltern, exe=exe, cmdline=cmd or f'"{exe}"', nutzer=nutzer,
                    extra={"cpu": random.uniform(0, 8), "mem_mb": random.uniform(40, 400)})


def _netz(name, ziel, port, ts) -> Ereignis:
    return Ereignis(NETZ, "conn_open", f"Verbindung: {name} → {ziel}:{port}", zeit=ts, prozess=name,
                    ziel=ziel, zielport=port, protokoll="TCP", extra={"extern": True})


def _datei(ordner, ext, aktion, ts, name=None) -> Ereignis:
    name = name or random.choice(["setup", "update", "helper", "cache", "lib"]) + str(random.randint(1, 40))
    pfad = f"{ordner}\\{name}{ext}"
    low = pfad.lower()
    sensibel = any(h in low for h in ("startup", "\\temp", "downloads"))
    schwere = Schwere.NIEDRIG if aktion == "file_created" else Schwere.INFO
    return Ereignis(DATEI, aktion, f"Datei: {name}{ext}", zeit=ts, datei=pfad, schwere=schwere,
                    extra={"ordner": ordner, "endung": ext, "sensibel": sensibel})


def alltag(n: int = 1500) -> list[Ereignis]:
    """Sieben Tage Alltag – mit dem Rauschen eines echten Rechners: seltene Programme,
    andere Ports, gelegentlich Nacht oder Wochenende, mal ein anderer Nutzer."""
    random.seed(7)
    burst = Burst()
    out = []
    for i in range(n):
        ts = ANKER - 7 * 86400 + (7 * 86400) * i / n
        st = datetime.fromtimestamp(ts)
        if (st.hour < 8 or st.hour >= 22) and random.random() > 0.03:
            ts += 10 * 3600                        # fast alles tags, 3 % nachts
        w = random.random()
        if w < 0.55:
            name, exe, eltern = random.choice(_NORMAL)
            e = _prozess(name, exe, eltern, ts)
        elif w < 0.62:
            name, exe, eltern = random.choice(_SELTEN)
            e = _prozess(name, exe, eltern, ts, nutzer=random.choice(["test\\user", "test\\user", "NT AUTHORITY\\SYSTEM"]))
        else:
            name = random.choice(["chrome.exe", "Teams.exe", "OneDrive.exe", "svchost.exe"])
            e = _netz(name, random.choice(_ZIELE), random.choice(_PORTS), ts)
        out.append(_stempel(burst, e, e.prozess))
    return out


def datei_alltag(n: int = 400) -> list[Ereignis]:
    random.seed(11)
    burst = Burst()
    out = []
    mix = [(_TEMP, [".dll", ".ps1", ".js"], 0.70), (_DOWNLOADS, [".exe", ".dll"], 0.28), (_STARTUP, [".lnk"], 0.02)]
    for i in range(n):
        ts = ANKER - 5 * 86400 + (5 * 86400) * i / n
        r = random.random()
        acc = 0.0
        for ordner, exts, p in mix:
            acc += p
            if r <= acc:
                break
        e = _datei(ordner, random.choice(exts), random.choice(["file_created", "file_modified", "file_deleted"]), ts)
        out.append(_stempel(burst, e, ordner.lower()))
    return out


def angriffe() -> list[tuple[str, Ereignis]]:
    ts = ANKER
    return [
        ("Makro startet PowerShell", _prozess("powershell.exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                                              "winword.exe", ts, cmd="powershell -w hidden -enc SQBFAFgA")),
        ("Dropper aus Temp", _prozess("a8f3k2p9.exe", rf"{_TEMP}\a8f3k2p9.exe", "explorer.exe", ts + 60)),
        ("C2 auf Port 4444", _netz("svchost.exe", "55.66.77.88", 4444, ts + 120)),
        ("Bekannter Prozess, neues Ziel", _netz("chrome.exe", "55.66.77.99", 6667, ts + 180)),
        ("Systemprozess falsche Herkunft", _prozess("svchost.exe", r"C:\Windows\System32\svchost.exe", "chrome.exe", ts + 240)),
        ("Nachts unbekanntes Programm", _prozess("updater_x.exe", r"C:\Users\test\AppData\Roaming\x\updater_x.exe",
                                                  "explorer.exe", ANKER - 11 * 3600, nutzer="SYSTEM")),
        ("Autostart-Persistenz", _datei(_STARTUP, ".vbs", "file_created", ts + 300, name="sys_upd")),
        # Windows-Name, aber nicht der Windows-Ort. Ohne Pfad im Ereignis bleibt
        # davon nur eine ML-Anomalie unter vielen; mit Pfad ist es ein Regeltreffer.
        ("Systemprogramm am falschen Ort",
         _prozess("TextInputHost.exe", rf"{_TEMP}\TextInputHost.exe", "explorer.exe", ts + 360)),
    ]


def laufen() -> str:
    zeilen = []
    ok = True
    tmp = Path(tempfile.mkdtemp(prefix="nemi_wache_test_"))
    try:
        sp = Speicher(tmp / "test.db")
        ereignisse = alltag(1500)
        sp.ereignisse_schreiben(ereignisse)
        zeilen.append(f"[1] Speicher            {sp.anzahl_ereignisse()} Ereignisse geschrieben")

        for e in ereignisse:
            if e.kategorie == PROZESS:
                sp.profil_fortschreiben(e.prozess, exe=e.exe, eltern=e.eltern, nutzer=e.nutzer,
                                        stunde=datetime.fromtimestamp(e.zeit).hour)
            elif e.kategorie == NETZ:
                sp.profil_fortschreiben(e.prozess, port=e.zielport, stunde=datetime.fromtimestamp(e.zeit).hour)
        zeilen.append(f"[2] Profile gelernt     {sp.anzahl_profile()} Prozesse")

        merk = ProzessMerkmale()
        merk.profile_setzen(sp.profile(), set())
        det = Detektor(tmp / "prozesse.json", merk, schwelle=0.97, kontamination=0.03)
        misch = list(ereignisse)
        random.Random(3).shuffle(misch)
        train, hold = misch[:1125], misch[1125:]
        r = det.trainieren(train)
        zeilen.append(f"[3] Training            {r.get('ereignisse')} Ereignisse, {r.get('merkmale')} Merkmale, {r.get('sekunden')}s")

        fehl = sum(1 for e in hold if det.ist_anomalie(det.bewerten(e)[0]))
        quote = fehl / len(hold)
        gut = quote <= 0.08
        ok &= gut
        zeilen.append(f"[4] Fehlalarmquote      {quote:.1%} auf ungesehenem Normalverkehr ({'ok' if gut else 'ZU HOCH'})")

        regeln = Regelwerk()
        burst = Burst()
        treffer = 0
        zeilen.append("[5] Angriffserkennung")
        datei_merk = DateiMerkmale()
        for label, e in angriffe():
            _stempel(burst, e, e.prozess or (e.extra or {}).get("ordner", ""))
            if e.kategorie == DATEI:
                score, gruende = 0.0, []
                via_ml = False
            else:
                score, gruende = det.bewerten(e)
                via_ml = det.ist_anomalie(score)
            alarme = regeln.pruefen(e)
            ids = ",".join(sorted({a.regel for a in alarme})) or "-"
            erkannt = via_ml or bool(alarme)
            treffer += erkannt
            zeilen.append(f"      {label:<34} Score {score:.3f}  Regeln {ids:<12} {'ERKANNT' if erkannt else 'nur Rang'}")
            if gruende and via_ml:
                zeilen.append(f"        Begründung: {'; '.join(gruende)}")
        gesamt = len(angriffe())
        gut = treffer >= gesamt - 1
        ok &= gut
        zeilen.append(f"      {treffer} von {gesamt} Angriffsmustern mit Alarm ({'ok' if gut else 'ZU WENIG'})")

        det2 = Detektor(tmp / "prozesse.json", ProzessMerkmale(), schwelle=0.97)
        det2.merkmale.profile_setzen(sp.profile(), set())
        probe = angriffe()[1][1]
        a, b = det.bewerten(probe)[0], det2.bewerten(probe)[0]
        gut = det2.trainiert and abs(a - b) < 1e-9
        ok &= gut
        zeilen.append(f"[6] Persistenz          Modell neu geladen, Score {a:.3f} = {b:.3f} ({'ok' if gut else 'FEHLER'})")

        vorher = det.bewerten(probe)[0]
        det.feedback({probe.prozess})
        nachher = det.bewerten(probe)[0]
        gut = nachher < vorher
        ok &= gut
        zeilen.append(f"[7] Feedback-Schleife   Score {vorher:.2f} → {nachher:.2f} nach „harmlos“ ({'ok' if gut else 'FEHLER'})")

        zeilen.append("[8] Dateimodell")
        dateien = datei_alltag(400)
        fdet = Detektor(tmp / "dateien.json", datei_merk, schwelle=0.97, kontamination=0.03, label="Dateien")
        dmisch = list(dateien)
        random.Random(5).shuffle(dmisch)
        r = fdet.trainieren(dmisch[:300])
        zeilen.append(f"      Training                        {r.get('ereignisse')} Ereignisse, {r.get('sekunden')}s")
        fehl = sum(1 for e in dmisch[300:] if fdet.ist_anomalie(fdet.bewerten(e)[0]))
        fq = fehl / 100
        gut = fq <= 0.08
        ok &= gut
        zeilen.append(f"      Fehlalarmquote                  {fq:.1%} auf ungesehenem Datei-Alltag ({'ok' if gut else 'ZU HOCH'})")
        fb = Burst()
        for label, ordner, ext, name in (("Skript im Autostart", _STARTUP, ".vbs", "sys_upd"),
                                         ("Zufallsname in Temp", _TEMP, ".exe", "q7x9k2mzp4"),
                                         ("Alltag: DLL in Temp", _TEMP, ".dll", "helper3")):
            e = _stempel(fb, _datei(ordner, ext, "file_created", ANKER, name=name), ordner.lower())
            s, g = fdet.bewerten(e)
            regel = ",".join(sorted({a.regel for a in Regelwerk().pruefen(e)})) or "-"
            zeilen.append(f"      {label:<32}Score {s:.3f}  Regeln {regel}")
        zeilen.append("")
        zeilen.append("Ergebnis: " + ("alle Prüfungen bestanden ✅" if ok else "FEHLER aufgetreten ❌"))
        sp.schliessen()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return "\n".join(zeilen)


if __name__ == "__main__":
    print(laufen())
