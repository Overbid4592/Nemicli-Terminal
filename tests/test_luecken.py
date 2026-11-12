"""Lücken-Prüfung (tools/luecken.py) mit gespielten OSV-Antworten.

python -m unittest tests.test_luecken
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import luecken as L                                    # noqa: E402


def details(vid, paket, *bereiche, cve="", schwere="HIGH", text="Beschreibung"):
    return {"id": vid, "aliases": [cve] if cve else [], "summary": text,
            "database_specific": {"severity": schwere},
            "affected": [{"package": {"name": paket, "ecosystem": "PyPI"},
                          "ranges": [{"type": "ECOSYSTEM", "events": list(b)} for b in bereiche]}]}


DATENBANK = {
    "GHSA-1": details("GHSA-1", "urllib3", [{"introduced": "1.0"}, {"fixed": "1.26.19"}],
                      [{"introduced": "2.0"}, {"fixed": "2.2.2"}], cve="CVE-2024-1"),
    "PYSEC-1": details("PYSEC-1", "urllib3", [{"introduced": "0"}, {"fixed": "1.26.19"}], cve="CVE-2024-1",
                       schwere=""),
    "GHSA-2": details("GHSA-2", "urllib3", [{"introduced": "0"}], cve="CVE-2025-2", schwere="MODERATE"),
    "MAL-2026-7": details("MAL-2026-7", "reqeusts", [{"introduced": "0"}], schwere=""),
}
FUNDE = {"urllib3": ["GHSA-1", "PYSEC-1", "GHSA-2"], "reqeusts": ["MAL-2026-7"], "rich": []}


def post(url, daten):
    return {"results": [{"vulns": [{"id": v} for v in FUNDE.get(q["package"]["name"], [])]}
                        for q in daten["queries"]]}


def get(url):
    return DATENBANK[url.rsplit("/", 1)[-1]]


class LueckenTests(unittest.TestCase):
    def test_funde(self):
        f = L.pruefen({"urllib3": "1.26.0", "reqeusts": "1.0", "rich": "14.0.0"}, post=post, get=get)
        self.assertEqual(sorted(f), ["reqeusts", "urllib3"])
        u = {l.kennung: l for l in f["urllib3"]}
        self.assertEqual(sorted(u), ["CVE-2024-1", "CVE-2025-2"])              # zwei Quellen, eine Lücke
        self.assertEqual((u["CVE-2024-1"].behoben, u["CVE-2024-1"].schwere), ("1.26.19", "hoch"))
        self.assertEqual((u["CVE-2025-2"].behoben, u["CVE-2025-2"].schwere), ("", "mittel"))
        self.assertTrue(f["reqeusts"][0].schadcode)
        self.assertEqual(L.schwerste(f["urllib3"]), "hoch")

    def test_behebung_im_eigenen_zweig(self):
        f = L.pruefen({"urllib3": "2.1.0"}, post=post, get=get)
        self.assertEqual({l.kennung: l.behoben for l in f["urllib3"]}["CVE-2024-1"], "2.2.2")

    def test_nicht_erreichbar(self):
        def kaputt(url, daten):
            raise OSError("kein Netz")
        with self.assertRaises(OSError):
            L.pruefen({"rich": "14.0.0"}, post=kaputt, get=get)


if __name__ == "__main__":
    unittest.main()
