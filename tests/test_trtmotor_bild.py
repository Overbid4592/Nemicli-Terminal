"""trtmotor.bild: die Krea-Blöcke über TensorRT – gleiche Rechnung wie torch. Ein Mini-DiT aus zwei echten
Krea-Blöcken mit Zufallsgewichten (NVFP4 im Krea-Format oder bf16), nichts wird geladen.
Läuft nur mit Grafikkarte und abgelegtem TensorRT-RTX.

python -m unittest tests.test_trtmotor_bild
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _d in ("", "core", "engines", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

try:
    import torch
    import krea as K
    from trtmotor import bild as TB, laufzeit as L
    from trtmotor.bausteine import fp4_packen
    BEREIT = torch.cuda.is_available() and L.bereit() and K._fp4_bereit("cuda")
except Exception:
    torch = K = TB = L = None
    BEREIT = False

D, HD, F, S = 512, 128, 1024, 300           # Köpfe fest wie in Krea: 48 (q) und 12 (k, v), RoPE für 128


def _matrix(sd, name, n, k, fp4: bool):
    w = torch.randn(n, k) * 0.03
    if not fp4:
        sd[name + ".weight"] = w.to("cuda", torch.bfloat16)
        return
    codes, skalen, glob = fp4_packen(w)
    c = torch.from_numpy(codes)
    sd[name + ".weight"] = (((c & 0xF) << 4) | (c >> 4)).cuda()
    sd[name + ".weight_scale"] = K._to_blocked(torch.from_numpy(skalen).view(torch.float8_e4m3fn).view(n, k // 16)).cuda()
    sd[name + ".weight_scale_2"] = torch.tensor(glob, device="cuda")
    sd[name + ".comfy_quant"] = torch.tensor(list(json.dumps({"format": "nvfp4"}).encode()), dtype=torch.uint8)


def _dit(fp4: bool, n_bloecke: int = 2):
    torch.manual_seed(1)
    bloecke = []
    for i in range(n_bloecke):
        sd, p = {}, f"blocks.{i}."
        sd[p + "mod.lin"] = (torch.randn(6 * D) * 0.05).cuda()
        sd[p + "prenorm.scale"] = (torch.randn(D) * 0.1).cuda()
        sd[p + "postnorm.scale"] = (torch.randn(D) * 0.1).cuda()
        sd[p + "attn.qknorm.qnorm.scale"] = (torch.randn(HD) * 0.1).cuda()
        sd[p + "attn.qknorm.knorm.scale"] = (torch.randn(HD) * 0.1).cuda()
        for n, (o, k) in {"attn.wq": (48 * HD, D), "attn.wk": (12 * HD, D), "attn.wv": (12 * HD, D),
                          "attn.gate": (48 * HD, D), "attn.wo": (D, 48 * HD), "mlp.gate": (F, D),
                          "mlp.up": (F, D), "mlp.down": (D, F)}.items():
            _matrix(sd, p + n, o, k, fp4)
        bloecke.append(K._Block(sd, p, torch.bfloat16))
    dit = object.__new__(K._DiT)
    dit.blocks, dit.dtype = bloecke, torch.bfloat16
    return dit


def _eingaben():
    torch.manual_seed(2)
    z = (torch.randn(1, S, D, device="cuda") * 0.5).to(torch.bfloat16)
    vec = (torch.randn(1, 1, 6 * D, device="cuda") * 0.1).to(torch.bfloat16)
    pos = torch.zeros(S, 3, device="cuda")
    pos[44:, 1] = torch.arange(S - 44, device="cuda").float() // 16
    pos[44:, 2] = torch.arange(S - 44, device="cuda").float() % 16
    return z, vec, K._rope_tab(pos)


@unittest.skipUnless(BEREIT, "Blackwell-Karte oder TensorRT-RTX fehlt")
class KreaBloecke(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(L, "engine_ordner", lambda: Path(self.tmp.name) / "engines")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(TB.entladen)
        self.pfad = Path(self.tmp.name) / "krea_test.safetensors"
        self.pfad.write_bytes(b"x")

    def _vergleich(self, fp4: bool, grenze: float):
        ram = _dit(fp4)
        z, vec, rope = _eingaben()
        with torch.no_grad():
            ref = ram.bloecke(z, vec, rope)
        self.assertIsNone(TB.bloecke(self.pfad))                # vor dem Einmessen kein Netz
        TB.vorbereiten(self.pfad, ram, warten=True)
        self.assertIsNone(TB.bloecke(self.pfad), "ohne Einmessung darf nicht gebaut werden")
        arbeit = _dit(fp4)                                       # die Arbeitskopie misst (wie in Krea)
        TB.einmessen(self.pfad, arbeit)
        with torch.no_grad():
            arbeit.bloecke(z, vec, rope)
        TB.vorbereiten(self.pfad, ram, warten=True)
        rechnen = TB.bloecke(self.pfad)
        self.assertIsNotNone(rechnen, TB._stand[str(self.pfad)].get("fehler"))
        aus = rechnen(z, vec, rope)
        d_ref, d_aus = (ref.float() - z.float()).flatten(), (aus.float() - z.float()).flatten()
        cos = float(torch.nn.functional.cosine_similarity(d_aus, d_ref, 0))
        self.assertGreater(cos, grenze)
        netz = TB._stand[str(self.pfad)]["netz"]
        self.assertGreater(netz.budget, 0)
        TB.nach_bild(self.pfad)
        self.assertEqual(0, netz.budget)
        self.assertTrue(list((Path(self.tmp.name) / "engines").glob("krea-*.cache")))

    def test_nvfp4_unveraendert_uebernommen(self):
        self._vergleich(fp4=True, grenze=0.99)

    def test_bf16_als_fp8(self):
        self._vergleich(fp4=False, grenze=0.99)

    def test_mehrere_gruppen_gleich_wie_torch(self):
        """Jeder Block ein eigenes Netz (Budget-Wechsel in kleinen Stücken): gleiche Rechnung wie torch."""
        with patch.object(TB, "GRUPPE_BYTES", 1):
            self.assertEqual(TB.gruppen(_dit(True)), [(0, 1), (1, 2)])
            self._vergleich(fp4=True, grenze=0.99)
            self.assertEqual(len(TB._stand[str(self.pfad)]["netz"].netze), 2)

    def test_altes_einzelnetz_wird_entfernt(self):
        ram = _dit(True)
        p = TB.plan(ram)
        alt = {"v": 1, "datei": TB._kennung(self.pfad), "plan": json.dumps(p, sort_keys=True),
               "s": [TB.S_MIN, TB.S_OPT, TB.S_MAX]}
        ordner = Path(self.tmp.name) / "engines"
        ordner.mkdir(parents=True, exist_ok=True)
        altes = ordner / (L.schluessel("krea", alt) + ".engine")
        fremdes = ordner / "krea-0123456789abcdef0123.engine"
        altes.write_bytes(b"alt")
        fremdes.write_bytes(b"anderes Modell")
        TB._alte_netze_weg(self.pfad, p)
        self.assertFalse(altes.exists())
        self.assertTrue(fremdes.exists())

    def test_gespeichertes_netz_ohne_einmessen(self):
        ram = _dit(True)
        arbeit = _dit(True)
        z, vec, rope = _eingaben()
        TB.einmessen(self.pfad, arbeit)
        with torch.no_grad():
            arbeit.bloecke(z, vec, rope)
        TB.vorbereiten(self.pfad, ram, warten=True)
        TB.entladen()                                            # neuer Start: nichts gemessen
        self.assertTrue(TB.gespeichert(self.pfad, ram))
        TB.vorbereiten(self.pfad, ram, warten=True)
        self.assertIsNotNone(TB.bloecke(self.pfad))


if __name__ == "__main__":
    unittest.main()
