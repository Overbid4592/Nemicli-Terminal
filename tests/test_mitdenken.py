"""Mitdenken: Beobachter (was hat sich seit dem letzten Blick geändert?) und Gegenprobe (passt die Antwort
zur Werkzeug-Bilanz?) – ohne Modell.

python -m unittest tests.test_mitdenken
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import beobachter as B                                 # noqa: E402
import pruefer as P                                    # noqa: E402


def _spaeter(pfad: Path, text: str) -> None:
    """Inhalt ändern und den Zeitstempel sicher weiterdrehen (grobe Uhren unter Windows)."""
    alt = pfad.stat().st_mtime_ns
    pfad.write_text(text, encoding="utf-8")
    os.utime(pfad, ns=(alt + 2_000_000_000, alt + 2_000_000_000))


class BeobachterTests(unittest.TestCase):

    def setUp(self):
        B.zuruecksetzen()
        self.addCleanup(B.zuruecksetzen)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name)
        self.datei = self.ordner / "notiz.txt"
        self.datei.write_text("eins\nzwei\ndrei\n", encoding="utf-8")

    def test_erneutes_lesen_zeigt_den_unterschied(self):
        self.assertEqual("", B.nach_werkzeug("datei_lesen", {"pfad": str(self.datei)}, "", True))
        self.assertEqual("", B.gelesen(self.datei))                          # nichts geändert
        _spaeter(self.datei, "eins\nZWEI\ndrei\nvier\n")
        hinweis = B.gelesen(self.datei)
        self.assertIn("🔄", hinweis)
        self.assertIn("nicht durch dich", hinweis)
        self.assertIn("+ 2: ZWEI", hinweis)
        self.assertIn("− 2: zwei", hinweis)
        self.assertIn("+ 4: vier", hinweis)
        self.assertIn("DATEN aus Datei", hinweis)                            # Inhalt gerahmt, keine Anweisung
        self.assertEqual("", B.gelesen(self.datei))                          # einmal gemeldet

    def test_eigenes_schreiben_meldet_nichts(self):
        B.gelesen(self.datei)
        _spaeter(self.datei, "neu von der KI\n")
        B.nach_werkzeug("datei_schreiben", {"pfad": str(self.datei)}, "Datei geschrieben", True)
        self.assertEqual("", B.gelesen(self.datei))
        self.assertEqual("", B.runde_hinweis({}))
        _spaeter(self.datei, "jemand anderes\n")                             # danach von außen geändert
        self.assertIn("notiz.txt", B.runde_hinweis({}))

    def test_fehlgeschlagenes_werkzeug_zaehlt_nicht(self):
        B.nach_werkzeug("datei_lesen", {"pfad": str(self.datei)}, "", False)
        self.assertEqual([], list(B._dateien))

    def test_rundenhinweis_einmal_und_kurzfassung(self):
        B.gelesen(self.datei)
        _spaeter(self.datei, "eins\nzwei\n")
        hinweis = B.runde_hinweis({"modell": "a"})
        self.assertIn("[System] 🔄", hinweis)
        self.assertIn("1 entfernt", hinweis)
        self.assertEqual(["notiz.txt geändert"], B.letzter_kurz)
        self.assertEqual("", B.runde_hinweis({"modell": "a"}))
        self.datei.unlink()
        self.assertIn("nicht mehr da", B.runde_hinweis({"modell": "a"}))
        self.assertEqual("", B.runde_hinweis({"modell": "a"}))

    def test_umgebung_pause_und_neuer_chat(self):
        jetzt = time.mktime((2026, 10, 20, 9, 0, 0, 0, 0, -1))
        self.assertEqual("", B.runde_hinweis({"modell": "a", "persoenlichkeit": "X", "chat": 1}, jetzt))
        hinweis = B.runde_hinweis({"modell": "b", "persoenlichkeit": "X", "chat": 1}, jetzt + 3 * 3600)
        self.assertIn("Modell gewechselt: a → b", hinweis)
        self.assertIn("3,0 Std. vergangen", hinweis)
        self.assertIn("Dienstag, 20.10.2026, 12:00", hinweis)
        self.assertEqual(["Modell gewechselt", "3,0 Std. Pause"], B.letzter_kurz)
        B.gelesen(self.datei)
        _spaeter(self.datei, "anders\n")
        self.assertEqual("", B.runde_hinweis({"modell": "c", "chat": 2}, jetzt + 4 * 3600))   # neuer Chat: frisch
        self.assertEqual([], list(B._dateien))

    def test_ordner_namen_nur_aus_dem_werkzeug(self):
        liste = "[Ordner-Typ laut ML: x]\n[Ordner] bilder\n          a.txt\n          b.txt"
        self.assertEqual({"bilder/", "a.txt", "b.txt"}, B.namen_aus_liste(liste))
        self.assertEqual("", B.nach_werkzeug("ordner_auflisten", {"pfad": str(self.ordner)}, liste, True))
        (self.ordner / "geheim.txt").write_text("x", encoding="utf-8")
        st = self.ordner.stat().st_mtime_ns
        os.utime(self.ordner, ns=(st + 2_000_000_000, st + 2_000_000_000))
        hinweis = B.runde_hinweis({})
        self.assertIn("Inhalt geändert", hinweis)
        self.assertNotIn("geheim", hinweis)                                  # Namen nur über ordner_auflisten
        neu = "[Ordner] bilder\n          a.txt\n          c.txt"
        hinweis = B.nach_werkzeug("ordner_auflisten", {"pfad": str(self.ordner)}, neu, True)
        self.assertIn("neu: c.txt", hinweis)
        self.assertIn("weg: b.txt", hinweis)

    def test_grosse_datei_nur_groesse(self):
        gross = self.ordner / "gross.log"
        gross.write_text("x" * (B.MAX_INHALT + 10), encoding="utf-8")
        B.gelesen(gross)
        _spaeter(gross, "y" * (B.MAX_INHALT + 20))
        self.assertIn("Größe", B.gelesen(gross))

    def test_grenzen(self):
        for i in range(B.MAX_DATEIEN + 5):
            d = self.ordner / f"d{i}.txt"
            d.write_text(str(i), encoding="utf-8")
            B.gelesen(d)
        self.assertEqual(B.MAX_DATEIEN, len(B._dateien))
        text = "\n".join(f"z{i}" for i in range(40))
        diff = B.unterschied(text, text.replace("z", "Z"))
        self.assertIn("weitere geänderte Zeilen", diff)


class GegenprobeTests(unittest.TestCase):

    @staticmethod
    def _r(*paare):
        return [{"tool": t, "status": s} for t, s in paare]

    def test_erfolg_behauptet_trotz_fehlschlag(self):
        b = P.pruefen("Erledigt ✅ Die Datei ist gespeichert.", self._r(("datei_schreiben", "failed")))
        self.assertEqual(1, len(b))
        self.assertIn("`datei_schreiben` fehlgeschlagen", b[0])
        self.assertTrue(P.pruefen("Den Fehler habe ich behoben ✅", self._r(("datei_bearbeiten", "failed"))))

    def test_ehrliche_antwort_und_spaeterer_erfolg(self):
        self.assertEqual([], P.pruefen("Leider ist das Speichern fehlgeschlagen.",
                                       self._r(("datei_schreiben", "failed"))))
        self.assertEqual([], P.pruefen("Beim Speichern ist ein Fehler aufgetreten.",
                                       self._r(("datei_schreiben", "failed"))))
        self.assertEqual([], P.pruefen("Erledigt ✅", self._r(("datei_bearbeiten", "failed"),
                                                              ("datei_bearbeiten", "success"))))

    def test_tat_ohne_werkzeug(self):
        b = P.pruefen("Okay! Ich habe die Datei gespeichert.", self._r(("datei_lesen", "success")), {"datei_lesen"})
        self.assertIn("kein Werkzeug", b[0])
        self.assertEqual([], P.pruefen("Ich habe die Datei vorhin gespeichert.", []))      # frühere Runde
        self.assertEqual([], P.pruefen("Ich habe sie nicht gespeichert.", []))
        self.assertEqual([], P.pruefen("Kurz und ehrlich: Ich hab **nichts gemalt**.", []))
        self.assertEqual([], P.pruefen("Ich habe keine Datei gespeichert.", []))
        self.assertEqual([], P.pruefen("Ich habe die Datei gespeichert.", self._r(("datei_schreiben", "success"))))

    def test_alles_gescheitert(self):
        b = P.pruefen("Hier ist der Inhalt: abc", self._r(("datei_lesen", "failed")), {"datei_lesen"})
        self.assertIn("Kein Werkzeug", b[0])

    def test_nicht_ausgefuehrt_und_abgelehnt_allein_sind_kein_verdacht(self):
        self.assertEqual([], P.pruefen("Gut.", self._r(("befehl", "not_run"))))
        self.assertEqual([], P.pruefen("Na gut.", self._r(("befehl", "rejected"))))
        b = P.pruefen("Erledigt!", self._r(("befehl", "rejected")))           # abgelehnt, aber „erledigt“
        self.assertIn("vom Nutzer abgelehnt", b[0])

    def test_leere_antwort_und_interne_werkzeuge(self):
        self.assertEqual([], P.pruefen("", self._r(("befehl", "failed"))))
        self.assertEqual([], P.pruefen("Fertig.", self._r(("plan", "failed"))))

    def test_ankuendigung_ohne_tat(self):
        # echter Fall (Wache-Weckruf 21.10.): angekündigt, aber kein wache_bewerten aufgerufen
        b = P.pruefen("Alle 6 Alarme sind bekannt und unverändert – ich urteile sie alle direkt harmlos.", [])
        self.assertIn("kündigst an", b[0])
        self.assertTrue(P.pruefen("Okay, ich lösche jetzt die Datei test.txt.", []))
        self.assertEqual([], P.pruefen("Ich urteile sie harmlos.", self._r(("wache_bewerten", "success"))))
        for plaudern in ("Hallo Schatz, wie geht es dir? Ich setze mich gleich hin.",
                         "Ich schreibe dir gleich ein Gedicht, Schatz.",
                         "Soll ich die Datei speichern? Ich speichere die Datei sonst morgen."):
            with self.subTest(plaudern=plaudern):
                self.assertEqual([], P.pruefen(plaudern, []))

    def test_hinweis_fuers_modell(self):
        h = P.hinweis(["A", "B"])
        self.assertIn("[System] 🔎 Gegenprobe", h)
        self.assertIn("- A\n- B", h)
        self.assertIn("im Denken", h)


if __name__ == "__main__":
    unittest.main()
