"""netze.py – die fünf Netze von LivePortrait, eigener Nachbau in reinem PyTorch.

Aufbau und Parameternamen folgen dem Original (KlingAIResearch/LivePortrait, MIT-Lizenz,
Copyright (c) 2024 Kuaishou Visual Generation and Interaction Center – siehe LIZENZ.txt),
damit die veröffentlichten Gewichte passen. Abweichungen:
- Spektral-Normierung ist beim Umwandeln in die Gewichte eingerechnet (gewichte.py); hier
  stehen gewöhnliche Faltungen.
- Nur Inferenz: kein DropPath, keine Initialisierung, keine Trainingspfade.

F  AppearanceFeatureExtractor   Bild 256² -> Merkmalsvolumen 32×16×64×64
M  MotionExtractor              Bild 256² -> Schlüsselpunkte, Kopfhaltung, Ausdruck
W  WarpingNetwork               Volumen + Schlüsselpunkte -> verformtes Merkmalsbild 256×64×64
G  SPADEDecoder                 Merkmalsbild -> Bild 512²
S  StitchingRetargetingNetwork  kleine MLPs: Nähte, Augen, Lippen
"""
from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn

ANZAHL_KP = 21


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------

def koordinatengitter(d: int, h: int, w: int, ref: torch.Tensor) -> torch.Tensor:
    """(d, h, w, 3) mit x, y, z in [-1, 1] – x nach rechts, y nach unten, z nach innen."""
    def achse(n):
        return 2 * (torch.arange(n, dtype=ref.dtype, device=ref.device) / (n - 1)) - 1
    z, y, x = torch.meshgrid(achse(d), achse(h), achse(w), indexing="ij")
    return torch.stack([x, y, z], dim=-1)


def kp_gauss(kp: torch.Tensor, groesse: tuple[int, int, int], varianz: float = 0.01) -> torch.Tensor:
    """Schlüsselpunkte (B, K, 3) als Glocken im Volumen (B, K, d, h, w)."""
    gitter = koordinatengitter(*groesse, ref=kp)
    diff = gitter[None, None] - kp[:, :, None, None, None, :]
    return torch.exp(-0.5 * (diff ** 2).sum(-1) / varianz)


class SameBlock2d(nn.Module):
    def __init__(self, ein, aus, kernel_size=3, padding=1, lrelu=False):
        super().__init__()
        self.conv = nn.Conv2d(ein, aus, kernel_size=kernel_size, padding=padding)
        self.norm = nn.BatchNorm2d(aus, affine=True)
        self.lrelu = lrelu

    def forward(self, x):
        x = self.norm(self.conv(x))
        return F.leaky_relu(x) if self.lrelu else F.relu(x)


class DownBlock2d(nn.Module):
    def __init__(self, ein, aus):
        super().__init__()
        self.conv = nn.Conv2d(ein, aus, kernel_size=3, padding=1)
        self.norm = nn.BatchNorm2d(aus, affine=True)

    def forward(self, x):
        return F.avg_pool2d(F.relu(self.norm(self.conv(x))), 2)


class ResBlock3d(nn.Module):
    def __init__(self, kanaele):
        super().__init__()
        self.conv1 = nn.Conv3d(kanaele, kanaele, kernel_size=3, padding=1)
        self.conv2 = nn.Conv3d(kanaele, kanaele, kernel_size=3, padding=1)
        self.norm1 = nn.BatchNorm3d(kanaele, affine=True)
        self.norm2 = nn.BatchNorm3d(kanaele, affine=True)

    def forward(self, x):
        out = self.conv1(F.relu(self.norm1(x)))
        return self.conv2(F.relu(self.norm2(out))) + x


class DownBlock3d(nn.Module):
    def __init__(self, ein, aus):
        super().__init__()
        self.conv = nn.Conv3d(ein, aus, kernel_size=3, padding=1)
        self.norm = nn.BatchNorm3d(aus, affine=True)

    def forward(self, x):
        return F.avg_pool3d(F.relu(self.norm(self.conv(x))), (1, 2, 2))


class UpBlock3d(nn.Module):
    def __init__(self, ein, aus):
        super().__init__()
        self.conv = nn.Conv3d(ein, aus, kernel_size=3, padding=1)
        self.norm = nn.BatchNorm3d(aus, affine=True)

    def forward(self, x):
        x = F.interpolate(x, scale_factor=(1, 2, 2))
        return F.relu(self.norm(self.conv(x)))


class Encoder(nn.Module):
    def __init__(self, expansion, ein, bloecke, max_merkmale):
        super().__init__()
        self.down_blocks = nn.ModuleList(
            DownBlock3d(ein if i == 0 else min(max_merkmale, expansion * 2 ** i),
                        min(max_merkmale, expansion * 2 ** (i + 1))) for i in range(bloecke))

    def forward(self, x):
        outs = [x]
        for b in self.down_blocks:
            outs.append(b(outs[-1]))
        return outs


class Decoder(nn.Module):
    def __init__(self, expansion, ein, bloecke, max_merkmale):
        super().__init__()
        up = []
        for i in reversed(range(bloecke)):
            rein = (1 if i == bloecke - 1 else 2) * min(max_merkmale, expansion * 2 ** (i + 1))
            up.append(UpBlock3d(rein, min(max_merkmale, expansion * 2 ** i)))
        self.up_blocks = nn.ModuleList(up)
        self.out_filters = expansion + ein
        self.conv = nn.Conv3d(self.out_filters, self.out_filters, kernel_size=3, padding=1)
        self.norm = nn.BatchNorm3d(self.out_filters, affine=True)

    def forward(self, x: list):
        out = x.pop()
        for b in self.up_blocks:
            out = torch.cat([b(out), x.pop()], dim=1)
        return F.relu(self.norm(self.conv(out)))


class Hourglass(nn.Module):
    def __init__(self, expansion, ein, bloecke, max_merkmale):
        super().__init__()
        self.encoder = Encoder(expansion, ein, bloecke, max_merkmale)
        self.decoder = Decoder(expansion, ein, bloecke, max_merkmale)
        self.out_filters = self.decoder.out_filters

    def forward(self, x):
        return self.decoder(self.encoder(x))


# ---------------------------------------------------------------------------
# F – Aussehen
# ---------------------------------------------------------------------------

class AppearanceFeatureExtractor(nn.Module):
    def __init__(self, image_channel=3, block_expansion=64, num_down_blocks=2, max_features=512,
                 reshape_channel=32, reshape_depth=16, num_resblocks=6):
        super().__init__()
        self.reshape_channel, self.reshape_depth = reshape_channel, reshape_depth
        self.first = SameBlock2d(image_channel, block_expansion)
        self.down_blocks = nn.ModuleList(
            DownBlock2d(min(max_features, block_expansion * 2 ** i), min(max_features, block_expansion * 2 ** (i + 1)))
            for i in range(num_down_blocks))
        self.second = nn.Conv2d(min(max_features, block_expansion * 2 ** num_down_blocks), max_features, 1)
        self.resblocks_3d = nn.Sequential()
        for i in range(num_resblocks):
            self.resblocks_3d.add_module(f"3dr{i}", ResBlock3d(reshape_channel))

    def forward(self, bild):
        out = self.first(bild)
        for b in self.down_blocks:
            out = b(out)
        out = self.second(out)
        b, _, h, w = out.shape
        return self.resblocks_3d(out.view(b, self.reshape_channel, self.reshape_depth, h, w))


# ---------------------------------------------------------------------------
# M – Bewegung (ConvNeXt V2 tiny)
# ---------------------------------------------------------------------------

class LayerNorm(nn.Module):
    def __init__(self, dim, eps=1e-6, kanaele_zuerst=False):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.bias = nn.Parameter(torch.zeros(dim))
        self.eps, self.kanaele_zuerst, self.dim = eps, kanaele_zuerst, (dim,)

    def forward(self, x):
        if not self.kanaele_zuerst:
            return F.layer_norm(x, self.dim, self.weight, self.bias, self.eps)
        u = x.mean(1, keepdim=True)
        s = (x - u).pow(2).mean(1, keepdim=True)
        return self.weight[:, None, None] * ((x - u) / torch.sqrt(s + self.eps)) + self.bias[:, None, None]


class GRN(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.gamma = nn.Parameter(torch.zeros(1, 1, 1, dim))
        self.beta = nn.Parameter(torch.zeros(1, 1, 1, dim))

    def forward(self, x):
        gx = torch.norm(x, p=2, dim=(1, 2), keepdim=True)
        nx = gx / (gx.mean(dim=-1, keepdim=True) + 1e-6)
        return self.gamma * (x * nx) + self.beta + x


class ConvNeXtBlock(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dwconv = nn.Conv2d(dim, dim, kernel_size=7, padding=3, groups=dim)
        self.norm = LayerNorm(dim)
        self.pwconv1 = nn.Linear(dim, 4 * dim)
        self.grn = GRN(4 * dim)
        self.pwconv2 = nn.Linear(4 * dim, dim)

    def forward(self, x):
        y = self.dwconv(x).permute(0, 2, 3, 1)
        y = self.pwconv2(self.grn(F.gelu(self.pwconv1(self.norm(y)))))
        return x + y.permute(0, 3, 1, 2)


class ConvNeXtV2(nn.Module):
    def __init__(self, num_kp=ANZAHL_KP, num_bins=66, depths=(3, 3, 9, 3), dims=(96, 192, 384, 768)):
        super().__init__()
        self.downsample_layers = nn.ModuleList([nn.Sequential(
            nn.Conv2d(3, dims[0], kernel_size=4, stride=4), LayerNorm(dims[0], kanaele_zuerst=True))])
        for i in range(3):
            self.downsample_layers.append(nn.Sequential(
                LayerNorm(dims[i], kanaele_zuerst=True), nn.Conv2d(dims[i], dims[i + 1], kernel_size=2, stride=2)))
        self.stages = nn.ModuleList(nn.Sequential(*[ConvNeXtBlock(dims[i]) for _ in range(depths[i])])
                                    for i in range(4))
        self.norm = nn.LayerNorm(dims[-1], eps=1e-6)
        self.fc_kp = nn.Linear(dims[-1], 3 * num_kp)
        self.fc_scale = nn.Linear(dims[-1], 1)
        self.fc_pitch = nn.Linear(dims[-1], num_bins)
        self.fc_yaw = nn.Linear(dims[-1], num_bins)
        self.fc_roll = nn.Linear(dims[-1], num_bins)
        self.fc_t = nn.Linear(dims[-1], 3)
        self.fc_exp = nn.Linear(dims[-1], 3 * num_kp)

    def forward(self, x):
        for i in range(4):
            x = self.stages[i](self.downsample_layers[i](x))
        x = self.norm(x.mean([-2, -1]))
        return {"pitch": self.fc_pitch(x), "yaw": self.fc_yaw(x), "roll": self.fc_roll(x), "t": self.fc_t(x),
                "exp": self.fc_exp(x), "scale": self.fc_scale(x), "kp": self.fc_kp(x)}


class MotionExtractor(nn.Module):
    def __init__(self, num_kp=ANZAHL_KP, **_):
        super().__init__()
        self.detector = ConvNeXtV2(num_kp=num_kp)

    def forward(self, x):
        return self.detector(x)


# ---------------------------------------------------------------------------
# W – Verformen
# ---------------------------------------------------------------------------

class DenseMotionNetwork(nn.Module):
    def __init__(self, block_expansion=32, num_blocks=5, max_features=1024, num_kp=ANZAHL_KP, feature_channel=32,
                 reshape_depth=16, compress=4):
        super().__init__()
        self.hourglass = Hourglass(block_expansion, (num_kp + 1) * (compress + 1), num_blocks, max_features)
        self.mask = nn.Conv3d(self.hourglass.out_filters, num_kp + 1, kernel_size=7, padding=3)
        self.compress = nn.Conv3d(feature_channel, compress, kernel_size=1)
        self.norm = nn.BatchNorm3d(compress, affine=True)
        self.occlusion = nn.Conv2d(self.hourglass.out_filters * reshape_depth, 1, kernel_size=7, padding=3)
        self.num_kp = num_kp

    def forward(self, merkmal, kp_ziel, kp_quelle):
        b, _, d, h, w = merkmal.shape
        merkmal = F.relu(self.norm(self.compress(merkmal)))                       # (b, 4, d, h, w)
        k = self.num_kp + 1
        ident = koordinatengitter(d, h, w, ref=kp_quelle)[None, None]             # (1, 1, d, h, w, 3)
        zur_quelle = ident - kp_ziel.view(b, self.num_kp, 1, 1, 1, 3) + kp_quelle.view(b, self.num_kp, 1, 1, 1, 3)
        bewegung = torch.cat([ident.expand(b, 1, d, h, w, 3), zur_quelle], dim=1)  # (b, k, d, h, w, 3)
        wiederholt = merkmal.unsqueeze(1).expand(b, k, *merkmal.shape[1:]).reshape(b * k, -1, d, h, w)
        verformt = F.grid_sample(wiederholt, bewegung.reshape(b * k, d, h, w, 3), align_corners=False)
        verformt = verformt.view(b, k, -1, d, h, w)
        waerme = kp_gauss(kp_ziel, (d, h, w)) - kp_gauss(kp_quelle, (d, h, w))
        waerme = torch.cat([torch.zeros_like(waerme[:, :1]), waerme], dim=1).unsqueeze(2)
        vorhersage = self.hourglass(torch.cat([waerme, verformt], dim=2).view(b, -1, d, h, w))
        maske = F.softmax(self.mask(vorhersage), dim=1).unsqueeze(2)              # (b, k, 1, d, h, w)
        verformung = (bewegung.permute(0, 1, 5, 2, 3, 4) * maske).sum(dim=1).permute(0, 2, 3, 4, 1)
        verdeckt = torch.sigmoid(self.occlusion(vorhersage.view(b, -1, h, w)))
        return verformung, verdeckt


class WarpingNetwork(nn.Module):
    def __init__(self, num_kp=ANZAHL_KP, block_expansion=64, max_features=512, num_down_blocks=2,
                 reshape_channel=32, dense_motion_params=None, **_):
        super().__init__()
        self.dense_motion_network = DenseMotionNetwork(num_kp=num_kp, feature_channel=reshape_channel,
                                                       **(dense_motion_params or {}))
        breite = block_expansion * 2 ** num_down_blocks
        self.third = SameBlock2d(max_features, breite, lrelu=True)
        self.fourth = nn.Conv2d(breite, breite, kernel_size=1)

    def forward(self, merkmal, kp_ziel, kp_quelle):
        verformung, verdeckt = self.dense_motion_network(merkmal, kp_ziel, kp_quelle)
        out = F.grid_sample(merkmal, verformung, align_corners=False)
        b, c, d, h, w = out.shape
        return self.fourth(self.third(out.view(b, c * d, h, w))) * verdeckt


# ---------------------------------------------------------------------------
# G – Bild erzeugen
# ---------------------------------------------------------------------------

class SPADE(nn.Module):
    def __init__(self, kanaele, label_kanaele, versteckt=128):
        super().__init__()
        self.mlp_shared = nn.Sequential(nn.Conv2d(label_kanaele, versteckt, kernel_size=3, padding=1))
        self.mlp_gamma = nn.Conv2d(versteckt, kanaele, kernel_size=3, padding=1)
        self.mlp_beta = nn.Conv2d(versteckt, kanaele, kernel_size=3, padding=1)

    def forward(self, x, karte):
        karte = F.interpolate(karte, size=x.shape[2:], mode="nearest")
        a = F.relu(self.mlp_shared(karte))
        return F.instance_norm(x) * (1 + self.mlp_gamma(a)) + self.mlp_beta(a)


class SPADEResnetBlock(nn.Module):
    def __init__(self, ein, aus, label_kanaele):
        super().__init__()
        mitte = min(ein, aus)
        self.lernt_abkuerzung = ein != aus
        self.conv_0 = nn.Conv2d(ein, mitte, kernel_size=3, padding=1)
        self.conv_1 = nn.Conv2d(mitte, aus, kernel_size=3, padding=1)
        self.norm_0 = SPADE(ein, label_kanaele)
        self.norm_1 = SPADE(mitte, label_kanaele)
        if self.lernt_abkuerzung:
            self.conv_s = nn.Conv2d(ein, aus, kernel_size=1, bias=False)
            self.norm_s = SPADE(ein, label_kanaele)

    def forward(self, x, karte):
        kurz = self.conv_s(self.norm_s(x, karte)) if self.lernt_abkuerzung else x
        dx = self.conv_0(F.leaky_relu(self.norm_0(x, karte), 0.2))
        dx = self.conv_1(F.leaky_relu(self.norm_1(dx, karte), 0.2))
        return kurz + dx


class SPADEDecoder(nn.Module):
    def __init__(self, upscale=2, max_features=512, block_expansion=64, out_channels=64, num_down_blocks=2):
        super().__init__()
        ein = min(max_features, block_expansion * 2 ** num_down_blocks)          # 256
        self.fc = nn.Conv2d(ein, 2 * ein, 3, padding=1)
        for i in range(6):
            setattr(self, f"G_middle_{i}", SPADEResnetBlock(2 * ein, 2 * ein, ein))
        self.up_0 = SPADEResnetBlock(2 * ein, ein, ein)
        self.up_1 = SPADEResnetBlock(ein, out_channels, ein)
        self.hoch = upscale > 1
        self.conv_img = (nn.Sequential(nn.Conv2d(out_channels, 12, kernel_size=3, padding=1), nn.PixelShuffle(2))
                         if self.hoch else nn.Conv2d(out_channels, 3, 3, padding=1))

    def forward(self, merkmal):
        x = self.fc(merkmal)
        for i in range(6):
            x = getattr(self, f"G_middle_{i}")(x, merkmal)
        x = self.up_0(F.interpolate(x, scale_factor=2), merkmal)
        x = self.up_1(F.interpolate(x, scale_factor=2), merkmal)
        return torch.sigmoid(self.conv_img(F.leaky_relu(x, 0.2)))


# ---------------------------------------------------------------------------
# S – Nähte und Nachführen
# ---------------------------------------------------------------------------

class StitchingRetargetingNetwork(nn.Module):
    def __init__(self, input_size, hidden_sizes, output_size):
        super().__init__()
        schichten, vorher = [], input_size
        for g in hidden_sizes:
            schichten += [nn.Linear(vorher, g), nn.ReLU(inplace=True)]
            vorher = g
        schichten.append(nn.Linear(vorher, output_size))
        self.mlp = nn.Sequential(*schichten)

    def forward(self, x):
        return self.mlp(x)


NAEHTE = {"stitching": (126, (128, 128, 64), 65),
          "lip": (65, (128, 128, 64), 63),
          "eye": (66, (256, 256, 128, 128, 64), 63)}


def bauen() -> dict[str, nn.Module]:
    """Alle Netze mit den Größen aus models.yaml des Originals."""
    netze = {
        "F": AppearanceFeatureExtractor(),
        "M": MotionExtractor(),
        "W": WarpingNetwork(dense_motion_params={"block_expansion": 32, "max_features": 1024, "num_blocks": 5,
                                                 "reshape_depth": 16, "compress": 4}),
        "G": SPADEDecoder(),
    }
    for name, (ein, versteckt, aus) in NAEHTE.items():
        netze[name] = StitchingRetargetingNetwork(ein, versteckt, aus)
    return netze
