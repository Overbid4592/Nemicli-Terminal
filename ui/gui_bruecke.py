"""gui_bruecke.py – Verbindung zwischen der laufenden NemiCLI-Sitzung und dem GUI-Fenster.

Das GUI-Fenster ist ein eigener Prozess, der sich über eine Windows Named Pipe mit
der Sitzung verbindet. Jede Verbindung muss einen geheimen Schlüssel beweisen
(HMAC-Challenge in beide Richtungen, eigener Thread je Verbindung – eine hängende
Gegenstelle blockiert keine anderen); danach gehen nur JSON-Nachrichten über die
Leitung (send_bytes/recv_bytes, nie pickle).

Sitzung -> Fenster: Ereignisse wie bei web_converse ({"t": "answer", "delta": …}).
Fenster -> Sitzung: Aufträge {"op": "<name>", …}; `handlers` ordnet sie Funktionen zu.
Ist kein Fenster verbunden, gilt jede offene Nachfrage als abgelehnt.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from multiprocessing.connection import Listener, answer_challenge, deliver_challenge

MAX_NACHRICHT = 4 * 1024 * 1024           # Aufträge sind Text; Anhänge gehen als Pfad
ABGELEHNT = "no"                          # gilt in jeder Freigabe als Nein


class GuiBruecke:
    def __init__(self, loop, handlers: dict, bei_wechsel=None):
        self.loop = loop                       # asyncio-Schleife der Sitzung
        self.handlers = handlers
        self.bei_wechsel = bei_wechsel or (lambda _n: None)   # Anzahl verbundener Fenster (Lese-Thread)
        self.adresse = r"\\.\pipe\nemicli-gui-" + secrets.token_hex(8)
        self.schluessel = secrets.token_bytes(32)
        self._listener = Listener(self.adresse, family="AF_PIPE")   # Schlüssel prüft _lesen
        self._clients: list = []
        self._lock = threading.Lock()
        self._senden_lock = threading.Lock()   # eine Nachricht nach der anderen
        self._fragen: dict[int, object] = {}   # id -> asyncio.Future
        self._fid = 0
        self._aus = False
        threading.Thread(target=self._annehmen, name="nemicli-gui-annehmen", daemon=True).start()

    # -- Verbindungen ------------------------------------------------------------
    def _annehmen(self) -> None:
        while not self._aus:
            try:
                conn = self._listener.accept()
            except Exception:
                if self._aus:
                    return
                time.sleep(0.2)                            # Dauerfehler: nicht im Kreis drehen
                continue
            threading.Thread(target=self._lesen, args=(conn,), name="nemicli-gui-lesen", daemon=True).start()

    def _lesen(self, conn) -> None:
        try:
            deliver_challenge(conn, self.schluessel)       # Reihenfolge wie Listener(authkey=…)
            answer_challenge(conn, self.schluessel)
        except Exception:
            try:
                conn.close()
            except OSError:
                pass
            return
        with self._lock:
            self._clients.append(conn)
            anzahl = len(self._clients)
        self._gewechselt(anzahl)
        try:
            while True:
                roh = conn.recv_bytes(MAX_NACHRICHT)
                try:
                    auftrag = json.loads(roh.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(auftrag, dict):
                    self._ausfuehren(auftrag)
        except (EOFError, OSError):
            pass
        finally:
            self._entfernen(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _entfernen(self, conn) -> None:
        with self._lock:
            weg = conn in self._clients
            if weg:
                self._clients.remove(conn)
            anzahl = len(self._clients)
        if anzahl == 0:                                    # niemand mehr da, der antworten kann
            for fid in list(self._fragen):
                self.antworten(fid, ABGELEHNT)
        if weg:
            self._gewechselt(anzahl)

    def _gewechselt(self, anzahl: int) -> None:
        try:
            self.bei_wechsel(anzahl)
        except Exception:                                  # z. B. Schleife beim Beenden schon zu
            pass

    def _ausfuehren(self, auftrag: dict) -> None:
        op = auftrag.get("op")
        if op == "antwort":                                # Antwort auf eine Nachfrage
            self.antworten(auftrag.get("id"), str(auftrag.get("wahl", "")))
            return
        fn = self.handlers.get(op) if isinstance(op, str) else None
        if fn is None:
            return
        try:
            fn(auftrag)
        except Exception as exc:
            self.senden({"t": "error", "text": f"{op}: {exc}"})

    def verbunden(self) -> bool:
        with self._lock:
            return bool(self._clients)

    # -- senden ----------------------------------------------------------------------
    def senden(self, ereignis: dict) -> None:
        """Ereignis an alle offenen Fenster. Aus jedem Thread aufrufbar."""
        daten = json.dumps(ereignis, ensure_ascii=False, default=str).encode("utf-8")
        with self._lock:
            ziele = list(self._clients)
        for conn in ziele:
            try:
                with self._senden_lock:
                    conn.send_bytes(daten)
            except (OSError, ValueError):
                self._entfernen(conn)

    # -- Nachfragen (async, aus dem Lese-Thread beantwortet) ---------------------------
    async def fragen(self, frage: str, optionen: list[tuple[str, str]], preview: str | None = None) -> str:
        self._fid += 1
        fid = self._fid
        fut = self.loop.create_future()
        self._fragen[fid] = fut
        if not self.verbunden():                           # erst eintragen, dann prüfen: kein Rennen
            self._fragen.pop(fid, None)
            return ABGELEHNT
        self.senden({"t": "confirm", "id": fid, "question": frage,
                     "options": [[w, t] for w, t in optionen], "preview": preview})
        try:
            return await fut
        finally:
            self._fragen.pop(fid, None)

    def antworten(self, fid, wahl: str) -> None:
        fut = self._fragen.get(fid) if isinstance(fid, int) else None
        if fut is None:
            return

        def setzen():                                      # erst in der Schleife prüfen: zwei Antworten
            if not fut.done():                             # (Klick + Trennen) dürfen sich treffen
                fut.set_result(wahl)
        try:
            self.loop.call_soon_threadsafe(setzen)
        except RuntimeError:                               # Schleife beendet
            pass

    def stop(self) -> None:
        self._aus = True
        with self._lock:
            ziele, self._clients = list(self._clients), []
        for conn in ziele:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._listener.close()
        except OSError:
            pass
