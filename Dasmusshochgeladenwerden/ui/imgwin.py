"""
imgwin.py - Das Bild-Bearbeiten-Fenster (tkinter, gehört zur Standard-Bibliothek).

Idee: Statt zu RATEN, wo ein Gesicht ist (die Hautton-Heuristik in imagegen.py
kann blonde Haare nicht von Haut unterscheiden), ziehst DU einfach einen Rahmen
um die Stelle, die neu gemalt werden soll – Gesicht, Augen, Hand, egal.

Ablauf:
    Bild anzeigen  ->  Rahmen ziehen  ->  „Neu malen"  ->  ansehen
    ->  zufrieden? speichern.  Sonst: Rückgängig und nochmal.

Technik-Hinweis: tkinter läuft hier in einem EIGENEN Thread (NemiCLI selbst
belegt den Hauptthread mit dem Vollbild-TUI). Alle tk-Aufrufe passieren
ausschließlich in diesem Thread; das Neumalen läuft nochmal eine Ebene tiefer in
einem Arbeits-Thread und meldet sich über eine Queue zurück, damit das Fenster
währenddessen bedienbar bleibt.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path

import imagegen as IG

# Farben passend zum dunklen NemiCLI-Look
BG = "#15151f"
FG = "#d8d8e0"
ACC = "#41e0d0"
DIM = "#7d8590"
RAHMEN = "#ff5f9e"          # Farbe des Auswahl-Rahmens


class _Editor:
    def __init__(self, root: tk.Tk, path: str, auto: bool = False):
        import numpy as np
        from PIL import Image, ImageTk

        self.np, self.Image, self.ImageTk = np, Image, ImageTk
        self.root = root
        self.path = Path(path)
        self.arr = np.asarray(Image.open(path).convert("RGB"))
        self.verlauf: list = []              # für „Rückgängig"
        self.box = None                      # (x0,y0,x1,y1) im BILD-Koordinatensystem
        self._drag = None
        self._rect_id = None
        self._busy = False
        self._q: queue.Queue = queue.Queue()
        # --- autonomer Durchlauf ---
        self.auto = auto                     # sucht sich die Stellen selbst
        self._plan: list = []
        self._plan_i = 0
        self._auto_laeuft = False

        vor = IG.region_defaults(str(path))

        # --- Anzeigegröße: ins Fenster einpassen ---
        H, W = self.arr.shape[:2]
        maxw = int(root.winfo_screenwidth() * 0.55)
        maxh = int(root.winfo_screenheight() * 0.70)
        self.scale = min(maxw / W, maxh / H, 1.0)
        self.dw, self.dh = int(W * self.scale), int(H * self.scale)

        root.title(f"NemiCLI · Bild bearbeiten – {self.path.name}")
        root.configure(bg=BG)

        self.canvas = tk.Canvas(root, width=self.dw, height=self.dh,
                                bg=BG, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=10, pady=(10, 6))
        self.canvas.bind("<ButtonPress-1>", self._maus_start)
        self.canvas.bind("<B1-Motion>", self._maus_zieh)
        self.canvas.bind("<ButtonRelease-1>", self._maus_los)

        # --- Bedienleiste ---
        leiste = tk.Frame(root, bg=BG)
        leiste.pack(fill="x", padx=10)

        tk.Label(leiste, text="Was soll da hin?", bg=BG, fg=DIM,
                 anchor="w").pack(fill="x")
        self.prompt = tk.Entry(leiste, bg="#1e1e2a", fg=FG, insertbackground=FG,
                               relief="flat", font=("Segoe UI", 10))
        self.prompt.insert(0, vor["prompt"])
        self.prompt.pack(fill="x", pady=(2, 8), ipady=4)

        regler = tk.Frame(leiste, bg=BG)
        regler.pack(fill="x")

        tk.Label(regler, text="Stärke", bg=BG, fg=DIM).pack(side="left")
        self.strength = tk.DoubleVar(value=0.55)
        tk.Scale(regler, from_=0.15, to=0.85, resolution=0.05, orient="horizontal",
                 variable=self.strength, bg=BG, fg=FG, troughcolor="#1e1e2a",
                 highlightthickness=0, length=170, showvalue=True,
                 command=self._info_neu).pack(side="left", padx=(6, 14))

        tk.Label(regler, text="Schritte", bg=BG, fg=DIM).pack(side="left")
        self.steps = tk.IntVar(value=40)
        tk.Scale(regler, from_=10, to=60, resolution=2, orient="horizontal",
                 variable=self.steps, bg=BG, fg=FG, troughcolor="#1e1e2a",
                 highlightthickness=0, length=170, showvalue=True,
                 command=self._info_neu).pack(side="left", padx=(6, 14))

        # CFG war bisher fest auf 5. Gemessen an der Augenpartie bringt mehr
        # sichtbar Struktur (Schärfe 9.1 bei 4 → 10.3 bei 9); ab ~9 wird der
        # Kontrast aber hart. 7 ist der Punkt davor.
        tk.Label(regler, text="Prompt-Treue", bg=BG, fg=DIM).pack(side="left")
        self.cfg = tk.DoubleVar(value=7.0)
        tk.Scale(regler, from_=3.0, to=10.0, resolution=0.5, orient="horizontal",
                 variable=self.cfg, bg=BG, fg=FG, troughcolor="#1e1e2a",
                 highlightthickness=0, length=150, showvalue=True).pack(side="left", padx=6)

        # Gerechnet werden nur steps × stärke Schritte – das ist DER Qualitäts-
        # hebel und war vorher unsichtbar (20 × 0.45 sind magere 9 Schritte).
        self.info = tk.Label(leiste, text="", bg=BG, fg=ACC,
                             anchor="w", font=("Segoe UI", 9))
        self.info.pack(fill="x", pady=(4, 0))

        knoepfe = tk.Frame(root, bg=BG)
        knoepfe.pack(fill="x", padx=10, pady=(6, 4))

        def knopf(text, cmd, farbe=ACC):
            b = tk.Button(knoepfe, text=text, command=cmd, bg="#1e1e2a", fg=farbe,
                          activebackground="#2a2a3a", activeforeground=farbe,
                          relief="flat", padx=14, pady=6, cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            return b

        self.b_malen = knopf("✦  Neu malen", self._neu_malen)
        self.b_undo = knopf("↶  Rückgängig", self._undo, DIM)
        knopf("💾  Speichern", self._speichern)
        knopf("Schließen", self._schliessen, DIM)

        start_text = ("Ich schau mir das Bild gleich selbst an …" if auto
                      else "Zieh mit der Maus einen Rahmen um die Stelle.")
        self.status = tk.Label(root, text=start_text,
                               bg=BG, fg=DIM, anchor="w", font=("Segoe UI", 9))
        self.status.pack(fill="x", padx=12, pady=(0, 10))

        self.gespeichert_als = None
        self._info_neu()
        self._zeichne()
        self._pumpe()                        # Rückmeldungen aus dem Arbeits-Thread
        if auto:
            # Erst das Fenster fertig zeichnen lassen, dann loslegen – sonst
            # steht der Nutzer vor einem grauen Kasten, während schon gerechnet wird.
            self.root.after(400, self._auto_start)

    # -- Anzeige ----------------------------------------------------------
    def _zeichne(self) -> None:
        bild = self.Image.fromarray(self.arr).resize((self.dw, self.dh),
                                                     self.Image.LANCZOS)
        self._tkimg = self.ImageTk.PhotoImage(bild)     # Referenz halten!
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tkimg)
        self._rect_id = None
        if self.box:
            x0, y0, x1, y1 = [v * self.scale for v in self.box]
            self._rect_id = self.canvas.create_rectangle(x0, y0, x1, y1,
                                                         outline=RAHMEN, width=2)

    def _sag(self, text: str, farbe: str = DIM) -> None:
        self.status.configure(text=text, fg=farbe)

    def _info_neu(self, *_) -> None:
        """Zeigt die zwei Zahlen, auf die es wirklich ankommt: wie viele Schritte
        tatsächlich gerechnet werden und wie stark der Ausschnitt vergrößert
        wird. Ein ENGER Rahmen ist der größte Qualitätshebel – die Augen bekommen
        dann viel mehr Pixel."""
        echte = max(1, round(self.steps.get() * self.strength.get()))
        text = f"→ {echte} Schritte werden gerechnet"
        if self.box:
            bw, bh = self.box[2] - self.box[0], self.box[3] - self.box[1]
            faktor = 1024 / max(bw, bh)
            text += f"   ·   Detail: {faktor:.1f}× vergrößert ({bw}×{bh} Pixel)"
            if faktor < 1.5:
                text += "  – enger ziehen bringt mehr!"
        else:
            text += "   ·   noch kein Rahmen gezogen"
        self.info.configure(text=text)

    # -- Maus: Rahmen ziehen ----------------------------------------------
    def _maus_start(self, ev) -> None:
        if self._busy:
            return
        self._drag = (ev.x, ev.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(ev.x, ev.y, ev.x, ev.y,
                                                     outline=RAHMEN, width=2)

    def _maus_zieh(self, ev) -> None:
        if self._drag and self._rect_id:
            x, y = self._drag
            self.canvas.coords(self._rect_id, x, y, ev.x, ev.y)

    def _maus_los(self, ev) -> None:
        if not self._drag:
            return
        x, y = self._drag
        self._drag = None
        x0, x1 = sorted((x, ev.x))
        y0, y1 = sorted((y, ev.y))
        # zurück in Bild-Koordinaten
        box = tuple(int(v / self.scale) for v in (x0, y0, x1, y1))
        if box[2] - box[0] < 32 or box[3] - box[1] < 32:
            self.box = None
            self._sag("Der Rahmen ist zu klein – zieh ihn etwas größer.", "#ffb454")
            return
        self.box = box
        self._info_neu()
        self._sag("Rahmen sitzt – jetzt auf 'Neu malen' drücken.", ACC)

    # -- Autonomer Durchlauf ----------------------------------------------
    def _auto_start(self) -> None:
        """Sucht die nachbesserungswürdigen Stellen selbst und arbeitet sie ab."""
        try:
            self._plan = IG.auto_regions(str(self.path), self.arr)
        except Exception as e:
            self._plan = []
            self._sag(f"Automatik fehlgeschlagen: {e}", "#ff6b6b")
            return
        if not self._plan:
            self._sag("Nichts Auffälliges gefunden – zieh selbst einen Rahmen, "
                      "wenn du was ändern willst.", "#ffb454")
            return
        self._plan_i = 0
        self._auto_laeuft = True
        self._auto_naechster()

    def _auto_naechster(self) -> None:
        """Nächste geplante Stelle malen – oder fertig werden."""
        if self._plan_i >= len(self._plan):
            self._auto_laeuft = False
            self._speichern()
            if self.gespeichert_als:
                self._sag(f"Fertig – {len(self._plan)} Stellen nachgebessert, "
                          f"gespeichert als {Path(self.gespeichert_als).name}. "
                          f"Nicht zufrieden? → Rückgängig.", ACC)
            return

        schritt = self._plan[self._plan_i]
        self.box = schritt["box"]
        # Regler & Prompt auf die geplanten Werte stellen, damit du siehst,
        # womit gerade gearbeitet wird (und danach von Hand weitermachen kannst).
        self.prompt.delete(0, "end")
        self.prompt.insert(0, schritt["prompt"])
        self.strength.set(schritt["strength"])
        self.cfg.set(schritt.get("cfg", 6.0))
        self._zeichne()
        self._info_neu()
        self._sag(f"Automatik {self._plan_i + 1}/{len(self._plan)}: "
                  f"{schritt['name']} wird nachgebessert …", ACC)
        self._neu_malen()

    # -- Neu malen (im Arbeits-Thread) ------------------------------------
    def _neu_malen(self) -> None:
        if self._busy:
            return
        if not self.box:
            self._sag("Erst einen Rahmen ziehen.", "#ffb454")
            return
        self._busy = True
        self.b_malen.configure(state="disabled")
        self._sag("male neu … (das dauert etwas)", ACC)

        box, stark = self.box, float(self.strength.get())
        schritte, cfg = int(self.steps.get()), float(self.cfg.get())
        text = self.prompt.get().strip() or None
        stand = self.arr              # auf dem AKTUELLEN Stand weitermalen

        def arbeit():
            try:
                neu = IG.repaint_region(str(self.path), box, prompt=text,
                                        strength=stark, steps=schritte, cfg=cfg,
                                        arr=stand,
                                        on_status=lambda s: self._q.put(("status", s)))
                self._q.put(("fertig", neu))
            except Exception as e:
                self._q.put(("fehler", e))

        threading.Thread(target=arbeit, daemon=True).start()

    def _pumpe(self) -> None:
        """Holt Meldungen aus dem Arbeits-Thread – läuft im tk-Thread."""
        try:
            while True:
                art, wert = self._q.get_nowait()
                if art == "status":
                    self._sag(str(wert), ACC)
                elif art == "fertig":
                    self.verlauf.append(self.arr)
                    self.arr = wert
                    self._zeichne()
                    self._busy = False
                    self.b_malen.configure(state="normal")
                    if self._auto_laeuft:
                        self._plan_i += 1
                        self.root.after(50, self._auto_naechster)
                    else:
                        self._sag("Fertig. Zufrieden? → Speichern. Sonst: Rückgängig.", ACC)
                elif art == "fehler":
                    self._busy = False
                    self.b_malen.configure(state="normal")
                    if self._auto_laeuft:
                        # Eine Stelle misslungen ist kein Grund, den Rest liegen
                        # zu lassen – weitermachen und am Ende trotzdem speichern.
                        self._sag(f"Stelle übersprungen ({wert})", "#ffb454")
                        self._plan_i += 1
                        self.root.after(50, self._auto_naechster)
                    else:
                        self._sag(f"Fehler: {wert}", "#ff6b6b")
        except queue.Empty:
            pass
        self.root.after(120, self._pumpe)

    # -- Knöpfe -----------------------------------------------------------
    def _undo(self) -> None:
        if self._busy or not self.verlauf:
            self._sag("Nichts zum Rückgängigmachen.", DIM)
            return
        self.arr = self.verlauf.pop()
        self._zeichne()
        self._sag("Zurückgenommen.", DIM)

    def _speichern(self) -> None:
        if self._busy:
            return
        # Original NICHT überschreiben – daneben legen.
        ziel = self.path.with_name(f"{self.path.stem}_edit{int(time.time()) % 10000}.png")
        meta = IG.read_meta(str(self.path))
        try:
            from PIL import Image, PngImagePlugin
            info = PngImagePlugin.PngInfo()
            for k, v in meta.items():
                if v:
                    info.add_text(k, v)
            info.add_text("bearbeitet", "Bereich im NemiCLI-Fenster neu gemalt")
            Image.fromarray(self.arr).save(ziel, pnginfo=info)
        except Exception as e:
            self._sag(f"Speichern fehlgeschlagen: {e}", "#ff6b6b")
            return
        self.gespeichert_als = str(ziel)
        self._sag(f"Gespeichert: {ziel.name}", ACC)

    def _schliessen(self) -> None:
        if self._busy:
            self._sag("Es wird gerade gemalt – einen Moment noch.", "#ffb454")
            return
        self.root.quit()


def _oeffne_blockierend(path: str, auto: bool = False) -> str | None:
    """Baut das Fenster und läuft, bis es geschlossen wird. Gibt den Pfad der
    gespeicherten Datei zurück (oder None)."""
    root = tk.Tk()
    ed = _Editor(root, path, auto=auto)
    root.protocol("WM_DELETE_WINDOW", ed._schliessen)
    try:
        root.mainloop()
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return ed.gespeichert_als


async def open_editor(path: str, auto: bool = False) -> str | None:
    """Öffnet das Fenster, ohne NemiCLI zu blockieren.

    tkinter bekommt einen eigenen Thread – der Haupt-Thread gehört dem
    Vollbild-TUI, und zwei Event-Schleifen im selben Thread vertragen sich nicht.

    `auto=True`: Das Fenster sucht sich die Stellen selbst und bessert sie nach,
    ohne dass du einen Rahmen ziehst (so öffnet es sich nach `/bild`).
    """
    import asyncio
    return await asyncio.to_thread(_oeffne_blockierend, path, auto)


def neuestes_bild() -> str | None:
    """Der Pfad des zuletzt erzeugten Bildes (für /bearbeiten ohne Argument)."""
    bilder = sorted(IG.OUT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
    return str(bilder[-1]) if bilder else None
