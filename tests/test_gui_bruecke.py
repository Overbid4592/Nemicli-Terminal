"""Brücke Sitzung <-> GUI-Fenster: JSON über die Named Pipe, Nachfragen, Schlüsselprüfung."""
import asyncio
import json
import os
import sys
import unittest
from multiprocessing.connection import Client
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))

import gui_bruecke  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Named Pipes nur unter Windows")
class Bruecke(unittest.TestCase):
    def test_auftrag_ereignis_und_nachfrage(self):
        async def ablauf():
            loop = asyncio.get_running_loop()
            auftraege = []
            br = gui_bruecke.GuiBruecke(loop, {"senden": auftraege.append})
            try:
                conn = await asyncio.to_thread(Client, br.adresse, "AF_PIPE", br.schluessel)
                for _ in range(100):
                    if br.verbunden():
                        break
                    await asyncio.sleep(0.01)
                conn.send_bytes(json.dumps({"op": "senden", "text": "hallo"}).encode("utf-8"))
                conn.send_bytes(b"kein json")
                br.senden({"t": "answer", "delta": "ä"})
                ev = json.loads(await asyncio.to_thread(conn.recv_bytes))
                self.assertEqual(ev, {"t": "answer", "delta": "ä"})

                frage = asyncio.ensure_future(br.fragen("Darf ich?", [("ja", "Erlauben"), ("nein", "Ablehnen")]))
                ev = json.loads(await asyncio.to_thread(conn.recv_bytes))
                self.assertEqual(ev["t"], "confirm")
                self.assertEqual(ev["options"], [["ja", "Erlauben"], ["nein", "Ablehnen"]])
                conn.send_bytes(json.dumps({"op": "antwort", "id": ev["id"], "wahl": "nein"}).encode("utf-8"))
                self.assertEqual(await asyncio.wait_for(frage, 5), "nein")
                self.assertEqual(auftraege, [{"op": "senden", "text": "hallo"}])
                conn.close()
            finally:
                br.stop()

        asyncio.run(ablauf())

    def test_nachfrage_ohne_fenster_und_nach_trennen_abgelehnt(self):
        async def ablauf():
            br = gui_bruecke.GuiBruecke(asyncio.get_running_loop(), {})
            try:
                self.assertEqual(await br.fragen("Darf ich?", [("yes", "Ja")]), gui_bruecke.ABGELEHNT)
                conn = await asyncio.to_thread(Client, br.adresse, "AF_PIPE", br.schluessel)
                for _ in range(100):
                    if br.verbunden():
                        break
                    await asyncio.sleep(0.01)
                frage = asyncio.ensure_future(br.fragen("Darf ich?", [("yes", "Ja")]))
                await asyncio.to_thread(conn.recv_bytes)
                conn.close()
                self.assertEqual(await asyncio.wait_for(frage, 5), gui_bruecke.ABGELEHNT)
            finally:
                br.stop()

        asyncio.run(ablauf())

    def test_doppelte_antwort_gilt_die_erste(self):
        async def ablauf():
            loop = asyncio.get_running_loop()
            fehler = []
            loop.set_exception_handler(lambda _l, ctx: fehler.append(ctx))
            br = gui_bruecke.GuiBruecke(loop, {})
            try:
                conn = await asyncio.to_thread(Client, br.adresse, "AF_PIPE", br.schluessel)
                for _ in range(100):
                    if br.verbunden():
                        break
                    await asyncio.sleep(0.01)
                frage = asyncio.ensure_future(br.fragen("Darf ich?", [("yes", "Ja")]))
                ev = json.loads(await asyncio.to_thread(conn.recv_bytes))
                br.antworten(ev["id"], "yes")
                br.antworten(ev["id"], gui_bruecke.ABGELEHNT)
                self.assertEqual(await asyncio.wait_for(frage, 5), "yes")
                await asyncio.sleep(0.05)
                self.assertEqual(fehler, [])
                conn.close()
            finally:
                br.stop()

        asyncio.run(ablauf())

    def test_meldet_verbinden_und_trennen(self):
        async def ablauf():
            wechsel = []
            br = gui_bruecke.GuiBruecke(asyncio.get_running_loop(), {}, bei_wechsel=wechsel.append)
            try:
                conn = await asyncio.to_thread(Client, br.adresse, "AF_PIPE", br.schluessel)
                for _ in range(100):
                    if wechsel:
                        break
                    await asyncio.sleep(0.01)
                conn.close()
                for _ in range(100):
                    if len(wechsel) == 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(wechsel, [1, 0])
            finally:
                br.stop()

        asyncio.run(ablauf())

    def test_haengende_gegenstelle_blockiert_nicht(self):
        async def ablauf():
            br = gui_bruecke.GuiBruecke(asyncio.get_running_loop(), {})
            try:
                stumm = open(br.adresse, "r+b", buffering=0)     # verbindet sich, antwortet nie
                conn = await asyncio.wait_for(
                    asyncio.to_thread(Client, br.adresse, "AF_PIPE", br.schluessel), 5)
                conn.close()
                stumm.close()
            finally:
                br.stop()

        asyncio.run(ablauf())

    def test_falscher_schluessel_abgewiesen(self):
        async def ablauf():
            br = gui_bruecke.GuiBruecke(asyncio.get_running_loop(), {})
            try:
                with self.assertRaises(Exception):
                    await asyncio.to_thread(Client, br.adresse, "AF_PIPE", b"x" * 32)
                self.assertFalse(br.verbunden())
            finally:
                br.stop()

        asyncio.run(ablauf())


if __name__ == "__main__":
    unittest.main()
