"""
reich.py - Wo soll der NemiCLI-Ordner liegen?  (Fenster + Umzug)

WARUM ES DAS GIBT
Bis zum 15.09.2026 lagen Programm und Daten im selben Ordner. Dann zog das
Programm um (Desktop -> AppData) und das venv blieb auf der Strecke. Genauso
hätte es die Chats, Bilder und das Gelernte erwischen können. Seitdem trennt
paths.py die beiden Wurzeln - und HIER entscheidet der Nutzer, wo seine
Hälfte liegen soll.

WIE ES ABLÄUFT
  1. Beim allerersten Start (und jederzeit über /start) geht ein Fenster auf.
  2. Der Nutzer wählt einen Ordner - oder sagt "bleibt beim Programm".
  3. Ist schon etwas da, wird angezeigt, was mitkommt und wie groß es ist.
     Erst nach seinem OK wird kopiert.
  4. Der Pfad landet in nemicli.config.json unter "daten_ordner".

WAS ES BEWUSST NICHT TUT
  * Es schlägt KEINEN Ordner vor. Der Ort ist die Entscheidung des Nutzers,
    nicht die von NemiCLI. Das Feld bleibt leer, bis er wählt.
  * Es LÖSCHT NICHTS. Umziehen heißt hier kopieren; das Original bleibt
    liegen, bis der Nutzer es selbst wegräumt. Bei ein paar Gigabyte Modellen
    ist ein halber Umzug sonst ein sehr teurer Fehler.
  * Es tut nicht so, als sei der Wechsel sofort überall angekommen. Die
    meisten Module merken sich ihren Pfad beim Import - deshalb: Neustart.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import paths


# ===========================================================================
#  Was liegt da eigentlich?
# ===========================================================================

def _groesse(p: Path) -> int:
    """Bytes eines Ordners oder einer Datei. Unlesbares zählt als 0."""
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def lesbare_groesse(n: int) -> str:
    for einheit in ("B", "KB", "MB", "GB"):
        if n < 1024 or einheit == "GB":
            return f"{n:.0f} {einheit}" if einheit == "B" else f"{n:,.1f} {einheit}".replace(",", ".")
        n /= 1024.0
    return f"{n:.1f} GB"


def inhalt(quelle: Path | None = None) -> list[tuple[str, int]]:
    """Was im Quell-Ordner liegt und mitkäme - [(name, bytes), ...]."""
    quelle = quelle or paths.DATEN
    raus = []
    for name in paths.DATEN_INHALT:
        p = quelle / name
        if p.exists():
            raus.append((name, _groesse(p)))
    return raus


# ===========================================================================
#  Das Fenster
# ===========================================================================

_ERKLAERUNG = (
    "NemiCLI trennt zwei Dinge:\n\n"
    "  •  Das PROGRAMM – Quelltext, Einstellungen, venv.\n"
    "     Liegt in:\n     {install}\n\n"
    "  •  DEIN ORDNER – Chats, Bilder, Modelle, alles Gelernte.\n"
    "     Der darf woanders liegen, damit er nicht mit umzieht,\n"
    "     wenn das Programm mal verschoben oder neu gebaut wird.\n\n"
    "Wo soll dein Ordner liegen?"
)


def fenster(aktuell: str | None = None) -> str | None:
    """Fragt nach dem Ordner.

    Rückgabe:
        Pfad   – der Nutzer hat einen Ordner gewählt
        ""     – "bleibt beim Programm" (wird gespeichert, es fragt nicht mehr)
        None   – abgebrochen, nichts ändern (beim Start fragt es dann erneut)

    Ohne Bildschirm (kein tkinter, Server, Remote) gibt es None zurück,
    statt den Start zu blockieren.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    ergebnis: dict[str, str | None] = {"wert": None}

    try:
        win = tk.Tk()
        win.title("NemiCLI – wo soll dein Ordner hin?")
        win.resizable(False, False)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass

        rahmen = tk.Frame(win, padx=24, pady=20)
        rahmen.pack()

        tk.Label(rahmen, text="Dein NemiCLI-Ordner",
                 font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(rahmen, text=_ERKLAERUNG.format(install=paths.INSTALL),
                 justify="left", anchor="w", font=("Segoe UI", 9),
                 ).pack(fill="x", pady=(10, 14))

        # Bewusst LEER, auch wenn schon etwas eingestellt ist: der Ort ist die
        # Entscheidung des Nutzers. Ein vorbelegtes Feld ist ein Vorschlag, und
        # ein Vorschlag wird durchgeklickt.
        feld = tk.Entry(rahmen, width=58, font=("Consolas", 10))
        feld.pack(fill="x")

        if aktuell:
            tk.Label(rahmen, text=f"Bisher eingestellt:  {aktuell}",
                     font=("Segoe UI", 8), fg="#666", anchor="w"
                     ).pack(fill="x", pady=(4, 0))

        hinweis = tk.Label(rahmen, text="", font=("Segoe UI", 8), fg="#a00", anchor="w")
        hinweis.pack(fill="x", pady=(4, 0))

        def waehlen():
            p = filedialog.askdirectory(title="Ordner für NemiCLI wählen",
                                        mustexist=False, parent=win)
            if p:
                feld.delete(0, "end")
                feld.insert(0, str(Path(p)))

        def uebernehmen():
            wert = feld.get().strip().strip('"')
            if not wert:
                hinweis.config(text="Bitte einen Ordner wählen – oder unten "
                                    "„Beim Programm lassen“.")
                return
            ergebnis["wert"] = wert
            win.destroy()

        def hierlassen():
            ergebnis["wert"] = ""
            win.destroy()

        knoepfe = tk.Frame(rahmen)
        knoepfe.pack(fill="x", pady=(16, 0))
        tk.Button(knoepfe, text="Durchsuchen …", width=16, command=waehlen
                  ).pack(side="left")
        tk.Button(knoepfe, text="Übernehmen", width=16, default="active",
                  command=uebernehmen).pack(side="right")
        tk.Button(rahmen, text="Beim Programm lassen", command=hierlassen,
                  relief="flat", fg="#555", font=("Segoe UI", 8)
                  ).pack(pady=(10, 0))

        feld.focus_set()
        win.bind("<Return>", lambda _e: uebernehmen())
        win.bind("<Escape>", lambda _e: win.destroy())

        win.update_idletasks()
        b, h = win.winfo_width(), win.winfo_height()
        x = (win.winfo_screenwidth() - b) // 2
        y = (win.winfo_screenheight() - h) // 3
        win.geometry(f"+{x}+{y}")

        win.mainloop()
    except Exception:
        return None
    return ergebnis["wert"]


# ===========================================================================
#  Umzug
# ===========================================================================

def pruefe_ziel(ziel: Path) -> str | None:
    """Fehlertext, wenn der Ordner nicht taugt – sonst None."""
    try:
        ziel.mkdir(parents=True, exist_ok=True)
        probe = ziel / ".nemicli.schreibprobe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        return f"In diesen Ordner kann ich nicht schreiben: {ziel}  ({e})"
    try:
        if ziel.resolve() == paths.INSTALL.resolve():
            return None                      # derselbe Ort - kein Umzug nötig
        if paths.INSTALL.resolve() in ziel.resolve().parents:
            return ("Dieser Ordner liegt IM Programm-Ordner. Genau das wollen "
                    "wir ja trennen – bitte einen Ort außerhalb wählen.")
    except Exception:
        pass
    return None


def umziehen(ziel: Path, quelle: Path | None = None, melde=None) -> list[str]:
    """Kopiert die Daten nach `ziel`. Das Original bleibt liegen.

    Gibt die Zeilen des Berichts zurück. Jeder Posten wird nach dem Kopieren
    nachgemessen – steht am Ende weniger da als vorher, sagt der Bericht das,
    statt Erfolg zu melden.
    """
    quelle = quelle or paths.DATEN
    bericht: list[str] = []

    def sag(t: str) -> None:
        if melde:
            melde(t)

    for name, vorher in inhalt(quelle):
        von = quelle / name
        nach = ziel / name
        sag(f"kopiere {name} ({lesbare_groesse(vorher)}) …")
        try:
            if von.is_dir():
                shutil.copytree(von, nach, dirs_exist_ok=True)
            else:
                nach.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(von, nach)
        except Exception as e:
            bericht.append(f"FEHLER  {name}: {e}")
            continue
        nachher = _groesse(nach)
        if nachher < vorher:
            bericht.append(f"UNVOLLSTAENDIG  {name}: "
                           f"{lesbare_groesse(nachher)} statt {lesbare_groesse(vorher)}")
        else:
            bericht.append(f"ok  {name}  ({lesbare_groesse(nachher)})")
    return bericht


def speichern(pfad: str) -> None:
    """Den gewählten Ordner in die Config schreiben.

    "" ist ein gültiger Wert und heißt "bleibt beim Programm" – deshalb wird
    der Schlüssel immer gesetzt, nie weggelassen. paths.py unterscheidet
    genau daran, ob der Nutzer schon gefragt wurde.
    """
    import config                       # spät, damit die Import-Reihenfolge egal ist
    config.update(daten_ordner=str(pfad))


def schon_gefragt() -> bool:
    return paths.GEWAEHLT is not None
