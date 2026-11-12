"""Sign in with ChatGPT: PKCE, ID-Token-Prüfung, verschlüsselte Ablage, Erneuern, Abmelden und der ganze
Browser-Ablauf mit nachgebautem Token-Endpunkt (der Rückruf geht wirklich an 127.0.0.1)."""
import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "tools", "engines", "ui"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import httpx  # noqa: E402

import chatgpt_login as L  # noqa: E402
import keyvault  # noqa: E402

CLIENT = "oaiapp_synthetisch"


def _jwt(angaben: dict) -> str:
    teil = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{teil({'alg': 'RS256'})}.{teil(angaben)}.signatur"


def _id_token(nonce=None, aud=CLIENT, iss=L.AUSSTELLER, exp=None):
    a = {"iss": iss, "aud": aud, "sub": "user-synthetisch", "email": "test_user@example.com",
         "exp": exp or time.time() + 600}
    if nonce is not None:
        a["nonce"] = nonce
    return _jwt(a)


class _Antwort:
    def __init__(self, status, daten):
        self.status_code, self._daten = status, daten

    def json(self):
        return self._daten


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(L, "KONTO_DATEI", Path(self.tmp.name) / "chatgpt_konto.json")
        p.start()
        self.addCleanup(p.stop)
        L._im_speicher.clear()
        self.addCleanup(L._im_speicher.clear)

    def angemeldet(self, ablauf_in=3600, scope=L.SCOPES):
        L._schreiben({"client_id": CLIENT, "ext_agent_host_id": "urn:uuid:test", "access_token": "alt-access",
                      "refresh_token": "alt-refresh", "id_token": _id_token(), "scope": scope,
                      "expires_at": time.time() + ablauf_in, "earliest_refresh_at": None, "email": "test_user@example.com"})


class Pkce(unittest.TestCase):
    def test_rfc7636_vektor(self):
        self.assertEqual(L.challenge_von("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_paar_frisch_und_gueltig(self):
        v1, c1 = L.pkce_paar()
        v2, _ = L.pkce_paar()
        self.assertNotEqual(v1, v2)
        self.assertTrue(43 <= len(v1) <= 128)
        self.assertEqual(c1, L.challenge_von(v1))
        self.assertNotIn("=", c1)


class IdToken(unittest.TestCase):
    def test_pruefungen(self):
        self.assertEqual(L.id_token_pruefen(_id_token("n1"), CLIENT, "n1")["email"], "test_user@example.com")
        for token, grund in ((_id_token("n1", iss="https://example.invalid"), "OpenAI"),
                             (_id_token("n1", aud="anders"), "anderen App"),
                             (_id_token("n1", exp=time.time() - 3600), "abgelaufen"),
                             (_id_token("falsch"), "nonce")):
            with self.assertRaises(L.AnmeldeFehler) as f:
                L.id_token_pruefen(token, CLIENT, "n1")
            self.assertIn(grund, str(f.exception))


class Ablage(Basis):
    @unittest.skipUnless(keyvault.available(), "braucht den Windows-Umschlag (DPAPI)")
    def test_tokens_nur_verschluesselt_auf_der_platte(self):
        self.angemeldet()
        roh = L.KONTO_DATEI.read_text(encoding="utf-8")
        for geheim in ("alt-access", "alt-refresh"):
            self.assertNotIn(geheim, roh)
        self.assertIn(keyvault.MARKER, roh)
        self.assertEqual(L._lesen()["refresh_token"], "alt-refresh")

    def test_klartext_tokens_gelten_nicht(self):
        L.KONTO_DATEI.write_text(json.dumps({"client_id": CLIENT, "refresh_token": "klartext"}), encoding="utf-8")
        self.assertNotIn("refresh_token", L._lesen())

    def test_status_und_abmelden(self):
        self.assertFalse(L.nutzbar())
        self.angemeldet()
        self.assertEqual(L.status(), {"angemeldet": True, "plan": True, "email": "test_user@example.com"})
        L.abmelden()
        d = L._lesen()
        self.assertFalse(L.status()["angemeldet"])
        self.assertEqual((d["client_id"], d["ext_agent_host_id"]), (CLIENT, "urn:uuid:test"))   # für die nächste Anmeldung

    def test_ohne_plan_freigabe_kein_zugang(self):
        self.angemeldet(scope="openid profile email offline_access")
        self.assertFalse(L.nutzbar())
        with self.assertRaises(L.AnmeldungNoetig):
            L.zugang()


class Erneuern(Basis):
    def test_gueltig_ohne_netz(self):
        self.angemeldet()
        with mock.patch.object(L.httpx, "post", side_effect=AssertionError("kein Netz")):
            self.assertEqual(L.zugang(), "alt-access")

    def test_vor_ablauf_erneuert(self):
        self.angemeldet(ablauf_in=30)
        antwort = {"access_token": "neu-access", "refresh_token": "neu-refresh", "expires_in": 3600,
                   "scope": L.SCOPES, "id_token": _id_token()}
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(200, antwort)) as post:
            self.assertEqual(L.zugang(), "neu-access")
        daten = post.call_args.kwargs["data"]
        self.assertEqual((daten["grant_type"], daten["refresh_token"], daten["client_id"], daten["resource"]),
                         ("refresh_token", "alt-refresh", CLIENT, L.RESSOURCE))
        self.assertEqual(L._lesen()["refresh_token"], "neu-refresh")          # Ersatz gespeichert

    def test_verbrauchtes_refresh_token_heisst_neu_anmelden(self):
        self.angemeldet(ablauf_in=-10)
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(400, {"error": "invalid_grant"})):
            with self.assertRaises(L.AnmeldungNoetig):
                L.zugang()
        self.assertFalse(L.status()["angemeldet"])
        self.assertEqual(L._lesen()["client_id"], CLIENT)

    def test_nicht_angemeldet(self):
        with self.assertRaises(L.AnmeldungNoetig):
            L.zugang()


class Browser(Basis):
    """Der ganze Ablauf: Anmelde-Adresse prüfen, Rückruf an den echten Empfänger auf 127.0.0.1, Token-Tausch."""

    def _ablauf(self, antwort_fn, token_antwort=None):
        gesehen = {}
        echter_post = httpx.post

        def browser(url):
            gesehen["url"] = url
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            gesehen["q"] = q

            def rueckruf():
                httpx.get(q["redirect_uri"] + "?" + urllib.parse.urlencode(antwort_fn(q)), timeout=10)
            threading.Thread(target=rueckruf, daemon=True).start()

        def post(url, data=None, timeout=None):
            gesehen["token"] = (url, data)
            if token_antwort is not None:
                return _Antwort(200, token_antwort(gesehen["q"]))
            return echter_post(url, data=data, timeout=timeout)

        with mock.patch.object(L.httpx, "post", post):
            stand = L.anmelden(browser, warten_s=15)
        return stand, gesehen

    def test_erste_anmeldung(self):
        tokens = lambda q: {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600,  # noqa: E731
                            "scope": L.SCOPES, "id_token": _id_token(q["nonce"])}
        stand, g = self._ablauf(lambda q: {"code": "code-1", "state": q["state"], "client_id": CLIENT,
                                           "scope": L.SCOPES}, tokens)
        q = g["q"]
        self.assertTrue(g["url"].startswith(L.AUTH_URL + "?"))
        self.assertEqual((q["client_id"], q["response_type"], q["code_challenge_method"], q["scope"], q["resource"]),
                         (L.REGISTRIERUNG, "code", "S256", L.SCOPES, L.RESSOURCE))
        self.assertTrue(q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback"))
        self.assertTrue(q["ext_agent_host_id"].startswith("urn:uuid:"))
        self.assertEqual(q["agent_name_hint"], "NemiCLI")
        url, daten = g["token"]
        self.assertEqual(url, L.TOKEN_URL)
        self.assertEqual(daten["grant_type"], "authorization_code")
        self.assertEqual(daten["client_id"], CLIENT)                          # die ausgegebene, nicht dynamic_agent_client
        self.assertEqual(L.challenge_von(daten["code_verifier"]), q["code_challenge"])
        self.assertNotIn("client_secret", daten)
        self.assertEqual(stand, {"angemeldet": True, "plan": True, "email": "test_user@example.com"})
        self.assertEqual(L._lesen()["client_id"], CLIENT)

    def test_falscher_state(self):
        with self.assertRaises(L.AnmeldeFehler):
            self._ablauf(lambda q: {"code": "c", "state": "fremd", "client_id": CLIENT})

    def test_abgelehnt(self):
        with self.assertRaises(L.AnmeldeFehler) as f:
            self._ablauf(lambda q: {"error": "access_denied", "state": q["state"]})
        self.assertIn("abgelehnt", str(f.exception))
        self.assertFalse(L.status()["angemeldet"])

    def test_zweite_anmeldung_nutzt_die_client_id(self):
        self.angemeldet(ablauf_in=-10)
        with mock.patch.object(L.httpx, "post", return_value=_Antwort(400, {"error": "refresh_token_expired"})):
            with self.assertRaises(L.AnmeldungNoetig):
                L.zugang()
        tokens = lambda q: {"access_token": "a2", "refresh_token": "r2", "expires_in": 3600,  # noqa: E731
                            "scope": L.SCOPES, "id_token": _id_token(q["nonce"])}
        _, g = self._ablauf(lambda q: {"code": "c2", "state": q["state"], "scope": L.SCOPES}, tokens)
        self.assertEqual(g["q"]["client_id"], CLIENT)
        self.assertNotIn("agent_name_hint", g["q"])
        self.assertEqual(g["q"]["login_hint"], "test_user@example.com")
        self.assertIn("id_token_hint", g["q"])
        L.abmelden()                                                            # abmelden: keine Hinweise mehr
        self.assertNotIn("email", L._lesen())


if __name__ == "__main__":
    unittest.main()
