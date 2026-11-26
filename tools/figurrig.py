"""
figurrig.py - aus einem Ganzkörper-Bild eine bewegliche Figur für das Terminal-Fenster: Skelett, Hülle, Aussehen.

Eingang: freigestelltes Bild der Persönlichkeit, gerade von vorn (und eins von hinten), am besten in T-Haltung
(Arme waagerecht seitlich ausgestreckt, Beine leicht gegrätscht – kugelbilder.figur_malen malt sie so). Daraus:

1. Skelett (`gelenke_schaetzen`): alle 22 Gelenke des Bewegungs-Skeletts (engines/bewegung.py) aus dem Umriss – Hals
   = schmalste Stelle unter dem Kopf, Arme waagerecht, schräg oder am Körper (Hand = weitester Punkt des Arms), Beine
   ab dem Schritt, Rücken und Schlüsselbeine dazwischen. Keine fremde Erkennung.
   Von vorn liegt die rechte Seite der Figur im Bild links, von hinten rechts.
2. Teile (`teile_zuordnen`): Rumpf mit Kopf, zwei Arme, zwei Beine – ein Arm beginnt außen am Schultergelenk, ein Bein
   unter dem Hüftgelenk.
3. Hülle (`huelle`): um jeden Knochen eine Röhre – das Stück Bild, das zu ihm gehört, ABSTAND_PX breiter –, an den
   Gelenken runde Kappen wie Knorpel, die beim Beugen die Lücke füllen. Jedes Stück bewegt sich starr mit seinem
   Knochen: kein Gummi, keine Falten.
4. Ablage (`bauen`): je Ansicht ein Ordner mit den Stücken (`stueck_<n>.png`, zugeschnitten) und `rig.json`
   (Gelenke, Knochen, je Stück Knochen und Lage im Bild).

Das Fenster legt jedes Stück auf seinen Knochen der Bewegung, hintere zuerst (ui/terminal_fenster.py).
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

RIG_VERSION = 8                          # 8: Gesichter (der Kopf je Ausdruck, tools/figurgesicht.py)
ALPHA_MIN = 128
# Das Bewegungs-Skelett: Gelenk -> Elterngelenk (SprongMotion, 22 Gelenke)
ELTERN = {"l_hip": "pelvis", "r_hip": "pelvis", "spine1": "pelvis", "l_knee": "l_hip", "r_knee": "r_hip",
          "spine2": "spine1", "l_ankle": "l_knee", "r_ankle": "r_knee", "spine3": "spine2", "l_foot": "l_ankle",
          "r_foot": "r_ankle", "neck": "spine3", "l_collar": "spine3", "r_collar": "spine3", "head": "neck",
          "l_shoulder": "l_collar", "r_shoulder": "r_collar", "l_elbow": "l_shoulder", "r_elbow": "r_shoulder",
          "l_wrist": "l_elbow", "r_wrist": "r_elbow"}
KNOCHEN = [(eltern, kind) for kind, eltern in ELTERN.items()]
# Die Bewegung kennt keine Hand – die Hand ist ein eigener Knochen vom Handgelenk zur Handmitte, den das Fenster aus
# dem Unterarm ableitet (locker hängend, beim Hängen schmal wie von der Seite)
KNOCHEN += [("l_wrist", "l_hand"), ("r_wrist", "r_hand")]
# Wo ein Knochen im Bild für die Zuordnung weiterreicht als sein Gelenk (Kopf bis zum Scheitel, Unterarm bis zur Hand)
VERLAENGERT = {("neck", "head"): "kopf_oben", ("l_elbow", "l_wrist"): "l_hand", ("r_elbow", "r_wrist"): "r_hand"}
TEILE = ["rumpf", "arm_l", "arm_r", "bein_l", "bein_r"]   # Rumpf mit Kopf, zwei Arme, zwei Beine
# Röhren: (Knochen, der sie bewegt; Strecke im Bild von – bis). Hüft- und Schlüsselbeine tragen keine eigene Röhre,
# ihr Stück Bild gehört zum Rücken.
ROEHREN = [(("pelvis", "spine1"), "pelvis", "spine1"), (("spine1", "spine2"), "spine1", "spine2"),
           (("spine2", "spine3"), "spine2", "spine3"), (("spine3", "neck"), "spine3", "neck"),
           (("neck", "head"), "neck", "kopf_oben")]
for _s in ("l", "r"):
    ROEHREN += [((f"{_s}_shoulder", f"{_s}_elbow"), f"{_s}_shoulder", f"{_s}_elbow"),
                ((f"{_s}_elbow", f"{_s}_wrist"), f"{_s}_elbow", f"{_s}_wrist"),
                ((f"{_s}_wrist", f"{_s}_hand"), f"{_s}_wrist", f"{_s}_hand"),
                ((f"{_s}_hip", f"{_s}_knee"), f"{_s}_hip", f"{_s}_knee"),
                ((f"{_s}_knee", f"{_s}_ankle"), f"{_s}_knee", f"{_s}_ankle"),
                ((f"{_s}_ankle", f"{_s}_foot"), f"{_s}_ankle", f"{_s}_foot")]
# Kappen: (Gelenk, Knochen, der sie bewegt) – sie liegen unter den Röhren und füllen die Lücke beim Beugen
KAPPEN = [("neck", ("neck", "head"))]
for _s in ("l", "r"):
    KAPPEN += [(f"{_s}_shoulder", (f"{_s}_shoulder", f"{_s}_elbow")), (f"{_s}_elbow", (f"{_s}_elbow", f"{_s}_wrist")),
               (f"{_s}_hip", (f"{_s}_hip", f"{_s}_knee")), (f"{_s}_knee", (f"{_s}_knee", f"{_s}_ankle")),
               (f"{_s}_ankle", (f"{_s}_ankle", f"{_s}_foot"))]
ABSTAND_PX = 2                           # jedes Stück reicht so weit über seine Grenze: keine Fugen
GELENK_ZONE = 1.5                        # so weit (× halbe Dicke des Glieds) biegt sich eine Röhre am Gelenk weich
ROEHRE_ZEILEN = 10                       # Zeilen des Netzes entlang einer Röhre (dazu die Grenzen der Gelenk-Zonen)


def _maske(bild):
    import numpy as np
    return np.asarray(bild.convert("RGBA"))[:, :, 3] >= ALPHA_MIN


def _laeufe(zeile, mindest: int = 2) -> list[tuple[int, int]]:
    """Zusammenhängende belegte Stücke einer Zeile (oder Spalte) als (von, bis) einschließlich."""
    import numpy as np
    z = np.concatenate([[False], zeile, [False]]).astype(np.int8)
    d = np.diff(z)
    anfang, ende = np.where(d == 1)[0], np.where(d == -1)[0] - 1
    return [(int(a), int(e)) for a, e in zip(anfang, ende) if e - a + 1 >= mindest]


def _lauf_bei(laeufe, x: float):
    for a, e in laeufe:
        if a <= x <= e:
            return a, e
    return None


def _arme_t(maske, mitte, hals_y, oben, hoehe):
    """T-Haltung: Arme waagerecht. Spaltenweise die Mittellinie jedes Arms bis zur Hand. None, wenn es keine ist."""
    import numpy as np
    band_von, band_bis = max(oben, int(hals_y - 0.06 * hoehe)), int(hals_y + 0.22 * hoehe)
    band = maske[band_von:band_bis]
    spalten = np.where(band.any(axis=0))[0]
    if not len(spalten) or mitte - spalten[0] < 0.3 * hoehe or spalten[-1] - mitte < 0.3 * hoehe:
        return None
    # Achsel: erste Zeile unter dem Hals, in der das Stück um die Mitte wieder schmal ist (nur noch Rumpf)
    achsel_y = next((yy for yy in range(int(hals_y + 0.03 * hoehe), int(hals_y + 0.35 * hoehe))
                     if (l := _lauf_bei(_laeufe(maske[yy]), mitte)) and l[1] - l[0] < 0.45 * hoehe), None)
    if achsel_y is None:
        return None
    rumpf = _lauf_bei(_laeufe(maske[min(achsel_y + int(0.02 * hoehe), maske.shape[0] - 1)]), mitte)
    if rumpf is None:
        return None

    def mittellinie(x):
        zeilen = np.where(maske[band_von:achsel_y + 1, int(x)])[0]
        return float(zeilen.mean()) + band_von if len(zeilen) else None

    def arm(rumpf_rand, aussen_x, richtung):
        schulter_x = rumpf_rand - richtung * 0.03 * hoehe
        handgelenk_x = aussen_x - richtung * 0.08 * hoehe
        ellbogen_x = (schulter_x + handgelenk_x) / 2
        punkte = {}
        for name, x in (("schulter", schulter_x), ("ellbogen", ellbogen_x), ("handgelenk", handgelenk_x),
                        ("hand", aussen_x - richtung * 0.01 * hoehe)):
            yy = mittellinie(x)
            if yy is None:
                return None
            punkte[name] = (float(x), yy)
        return punkte

    links = arm(rumpf[0], float(spalten[0]), -1)
    rechts = arm(rumpf[1], float(spalten[-1]), 1)
    return (links, rechts) if links and rechts else None


def _arme_schraeg(maske, mitte, schulter_y, hoehe):
    """Arme schräg vom Körper weg (zwischen T- und A-Haltung): unter der Achsel ist zwischen Arm und Rumpf Luft.
    Je Seite das Stück außerhalb des Rumpfs, die Hand ist sein von der Schulter am weitesten entfernter Punkt;
    Ellbogen und Handgelenk liegen auf der Mitte des Arms. None, wenn die Arme am Körper liegen."""
    import numpy as np
    from PIL import Image, ImageDraw
    brust_y = min(maske.shape[0] - 1, int(schulter_y + 0.08 * hoehe))
    rumpf = _lauf_bei(_laeufe(maske[brust_y]), mitte)
    if rumpf is None or rumpf[1] - rumpf[0] > 0.45 * hoehe:
        return None
    unten = min(maske.shape[0], int(schulter_y + 0.6 * hoehe))

    def arm(richtung):
        rand = rumpf[0] if richtung < 0 else rumpf[1]
        laeufe = [l for l in _laeufe(maske[brust_y]) if (l[1] < rand - 2 if richtung < 0 else l[0] > rand + 2)]
        if not laeufe:
            return None
        start = laeufe[0] if richtung < 0 else laeufe[-1]           # der äußerste: Haare hängen innen
        # nur die Seite außerhalb des Rumpfs, darin das zusammenhängende Stück ab der Brust-Zeile
        von, bis = (0, rand - 1) if richtung < 0 else (rand + 2, maske.shape[1])
        seite = Image.fromarray((maske[:unten, von:bis] * 255).astype(np.uint8)).copy()   # copy: beschreibbar
        ImageDraw.floodfill(seite, ((start[0] + start[1]) // 2 - von, brust_y), 128)
        ys, xs = np.where(np.asarray(seite) == 128)
        if len(xs) < 50 or ys.max() >= unten - 2:                 # reicht bis unten: Hand am Bein, kein freier Arm
            return None
        xs = xs + von
        schulter = (float(rand - richtung * 0.01 * hoehe), float(schulter_y))
        d = np.hypot(xs - schulter[0], ys - schulter[1])
        weit = int(d.argmax())
        hand = (float(xs[weit]), float(ys[weit]))
        laenge = float(d[weit])
        if not 0.25 * hoehe <= laenge <= 0.55 * hoehe:
            return None
        ux, uy = (hand[0] - schulter[0]) / laenge, (hand[1] - schulter[1]) / laenge
        t = (xs - schulter[0]) * ux + (ys - schulter[1]) * uy

        def auf_der_mitte(anteil):
            nah = np.abs(t - anteil * laenge) < 0.015 * hoehe
            if not nah.any():
                return schulter[0] + ux * anteil * laenge, schulter[1] + uy * anteil * laenge
            return float(xs[nah].mean()), float(ys[nah].mean())

        handgelenk = auf_der_mitte(max(0.5, 1 - 0.09 * hoehe / laenge))
        return {"schulter": schulter, "ellbogen": auf_der_mitte(0.5 * (1 - 0.09 * hoehe / laenge) + 0.04),
                "handgelenk": handgelenk, "hand": hand}

    links, rechts = arm(-1), arm(1)
    return (links, rechts) if links and rechts else None


def _arme_a(maske, mitte, schulter_y, oben, hoehe):
    """A-Haltung (Arme nah am Körper): zeilenweise die Stücke links und rechts vom Rumpf; sonst nach Proportion."""
    def y(anteil):
        return int(oben + anteil * hoehe)

    trenn_y, links_arm, rechts_arm = None, [], []
    for yy in range(schulter_y, y(0.62)):
        laeufe = _laeufe(maske[yy])
        rumpf = _lauf_bei(laeufe, mitte)
        if rumpf is None:
            continue
        links = [l for l in laeufe if l[1] < rumpf[0]]
        rechts = [l for l in laeufe if l[0] > rumpf[1]]
        if links and rechts:
            if trenn_y is None:
                trenn_y = yy
            links_arm.append((yy, (links[-1][0] + links[-1][1]) / 2))
            rechts_arm.append((yy, (rechts[0][0] + rechts[0][1]) / 2))
        elif trenn_y is not None and yy - trenn_y > 0.05 * hoehe:
            break
    rumpf_schulter = _lauf_bei(_laeufe(maske[schulter_y]), mitte) or (mitte - hoehe * 0.08, mitte + hoehe * 0.08)

    def arm(punkte, schulter, aussen):
        if len(punkte) >= 4:
            hand_y, hand_x = punkte[-1]
        else:
            hand_y, hand_x = y(0.52), schulter[0] + aussen * 0.06 * hoehe
        handgelenk_y = hand_y - 0.06 * hoehe
        nah = min(punkte, key=lambda p: abs(p[0] - handgelenk_y)) if punkte else (handgelenk_y, hand_x)
        handgelenk = (float(nah[1]), float(handgelenk_y))
        return {"schulter": schulter, "ellbogen": ((schulter[0] + handgelenk[0]) / 2, (schulter[1] + handgelenk[1]) / 2),
                "handgelenk": handgelenk, "hand": (float(hand_x), float(hand_y))}

    return (arm(links_arm, (float(rumpf_schulter[0]) + 0.02 * hoehe, float(schulter_y)), -1),
            arm(rechts_arm, (float(rumpf_schulter[1]) - 0.02 * hoehe, float(schulter_y)), 1))


def gelenke_schaetzen(maske, ansicht: str = "vorne") -> dict[str, tuple[float, float]]:
    """Alle 22 Gelenke (Bildpunkte x, y) plus Hilfspunkte (kopf_oben, *_hand) aus dem Umriss einer stehenden Figur."""
    import numpy as np
    zeilen = np.where(maske.any(axis=1))[0]
    if len(zeilen) < 40:
        raise ValueError("Zu wenig Figur im Bild")
    oben, unten = int(zeilen[0]), int(zeilen[-1])
    hoehe = unten - oben + 1
    spalten = np.where(maske[oben:oben + max(2, hoehe // 10)].any(axis=0))[0]
    mitte = float(spalten.mean())

    def y(anteil: float) -> int:
        return int(oben + anteil * hoehe)

    # Hals: in T-Haltung knapp über der Oberkante der ausgestreckten Arme (lange Haare verdecken ihn oft);
    # sonst die schmalste Stelle des mittleren Stücks zwischen 13 % und 28 % der Höhe
    breit = [yy for yy in range(y(0.08), y(0.3))
             if (s := np.where(maske[yy])[0]).size and s[-1] - s[0] > 0.6 * hoehe]
    if breit:
        hals_y = int(breit[0] - 0.02 * hoehe)
    else:
        hals_y, hals_breite = y(0.165), 10 ** 9
        for yy in range(y(0.15), y(0.19)):
            lauf = _lauf_bei(_laeufe(maske[yy]), mitte)
            if lauf and lauf[1] - lauf[0] < hals_breite:
                hals_y, hals_breite = yy, lauf[1] - lauf[0]
    kopf_spalten = np.where(maske[oben:hals_y].any(axis=0))[0]
    mitte = float(kopf_spalten.mean()) if len(kopf_spalten) else mitte
    schulter_y = min(unten, hals_y + int(0.045 * hoehe))
    arme = (_arme_t(maske, mitte, hals_y, oben, hoehe) or _arme_schraeg(maske, mitte, schulter_y, hoehe)
            or _arme_a(maske, mitte, schulter_y, oben, hoehe))
    arm_l, arm_r = arme

    # Beine: ab dem Schritt (unter der Mitte wird es leer), je Seite das Stück nächst der Mitte innerhalb der
    # Rumpfbreite – Hände hängen weiter außen
    rumpf = _lauf_bei(_laeufe(maske[y(0.45)]), mitte) or (mitte - 0.1 * hoehe, mitte + 0.1 * hoehe)
    innen_von, innen_bis = rumpf[0] - 0.08 * hoehe, rumpf[1] + 0.08 * hoehe
    schritt_y = next((yy for yy in range(y(0.40), unten + 1) if not maske[yy, int(mitte)]), None)
    bein_l, bein_r = [], []
    for yy in range(schritt_y or unten + 1, unten + 1):
        laeufe = [l for l in _laeufe(maske[yy]) if innen_von <= (l[0] + l[1]) / 2 <= innen_bis]
        links = [l for l in laeufe if (l[0] + l[1]) / 2 < mitte]
        rechts = [l for l in laeufe if (l[0] + l[1]) / 2 >= mitte]
        if links and rechts:
            bein_l.append((yy, (links[-1][0] + links[-1][1]) / 2))
            bein_r.append((yy, (rechts[0][0] + rechts[0][1]) / 2))
    if schritt_y is not None and not bein_l:
        schritt_y = None
    if schritt_y is None:
        schritt_y = y(0.52)
        bein_l = [(yy, mitte - 0.05 * hoehe) for yy in range(schritt_y, unten + 1)]
        bein_r = [(yy, mitte + 0.05 * hoehe) for yy in range(schritt_y, unten + 1)]

    def bein(punkte):
        def x_bei(yy):
            return min(punkte, key=lambda p: abs(p[0] - yy))[1]
        huefte = (float(x_bei(schritt_y + 0.02 * hoehe)), float(min(schritt_y - 0.03 * hoehe, oben + 0.53 * hoehe)))
        knoechel_y = oben + 0.93 * hoehe
        knoechel = (float(x_bei(knoechel_y)), float(knoechel_y))
        return {"huefte": huefte, "knie": ((huefte[0] + knoechel[0]) / 2, (huefte[1] + knoechel[1]) / 2),
                "knoechel": knoechel, "fuss": (knoechel[0], float(unten))}

    bl, br = bein(bein_l), bein(bein_r)
    becken = ((bl["huefte"][0] + br["huefte"][0]) / 2, (bl["huefte"][1] + br["huefte"][1]) / 2)
    hals = (mitte, float(hals_y))

    def zwischen(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    links, rechts = dict(arm_l, **bl), dict(arm_r, **br)
    # von vorn ist die rechte Seite der Figur im Bild links, von hinten ist es umgekehrt
    r, l = (links, rechts) if ansicht == "vorne" else (rechts, links)
    g = {"pelvis": becken, "neck": hals, "head": (mitte, hals_y - 0.35 * (hals_y - oben)), "kopf_oben": (mitte, float(oben)),
         "spine1": zwischen(becken, hals, 0.22), "spine2": zwischen(becken, hals, 0.48),
         "spine3": zwischen(becken, hals, 0.74)}
    for seite, s in (("r", r), ("l", l)):
        g.update({f"{seite}_shoulder": s["schulter"], f"{seite}_elbow": s["ellbogen"], f"{seite}_wrist": s["handgelenk"],
                  f"{seite}_hand": s["hand"], f"{seite}_hip": s["huefte"], f"{seite}_knee": s["knie"],
                  f"{seite}_ankle": s["knoechel"], f"{seite}_foot": s["fuss"],
                  f"{seite}_collar": zwischen(hals, s["schulter"], 0.45)})
        g[f"{seite}_collar"] = (g[f"{seite}_collar"][0], max(g[f"{seite}_collar"][1], hals_y + 0.025 * hoehe))
    return {k: (float(v[0]), float(v[1])) for k, v in g.items()}


def _abstand(xx, yy, a, b):
    """Abstand jedes Punkts zur Strecke a–b (numpy-Felder)."""
    import numpy as np
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    laenge2 = dx * dx + dy * dy or 1e-9
    t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / laenge2, 0.0, 1.0)
    return np.hypot(xx - (ax + t * dx), yy - (ay + t * dy))


def _gruppe(knochen: tuple[str, str]) -> str:
    kind = knochen[1]
    for seite in ("l", "r"):
        if kind in (f"{seite}_elbow", f"{seite}_wrist", f"{seite}_hand"):
            return f"arm_{seite}"
        if kind in (f"{seite}_knee", f"{seite}_ankle", f"{seite}_foot"):
            return f"bein_{seite}"
    return "rumpf"


def teile_zuordnen(maske, gelenke: dict):
    """Jedes Pixel der Figur einem Teil (TEILE) zuordnen: dem Teil des nächsten Knochens. Die Grenze zum Rumpf ist
    eine klare Schnittlinie: ein Arm beginnt außen am Schultergelenk, ein Bein unter dem Hüftgelenk – was innen bzw.
    darüber liegt (Achsel, Hüfte, Kopf), gehört zum Rumpf; unter der Achsel auch alles, was am Rumpf hängt (die Seite
    eines weiten Pullovers). Ergebnis: Feld mit Teil-Nummer + 1 (0 = leer)."""
    import numpy as np
    ys, xs = np.nonzero(maske)
    xs_f, ys_f = xs.astype(np.float32), ys.astype(np.float32)
    abst = np.stack([_abstand(xs_f, ys_f, gelenke[e], gelenke[VERLAENGERT.get((e, k), k)]) for e, k in KNOCHEN])
    nummer = np.array([TEILE.index(_gruppe(k)) for k in KNOCHEN])[abst.argmin(axis=0)]
    rumpf = TEILE.index("rumpf")
    nummer[ys_f < gelenke["neck"][1]] = rumpf
    mitte = gelenke["neck"][0]
    for seite in ("l", "r"):
        sx = gelenke[f"{seite}_shoulder"][0]
        aussen = 1.0 if sx >= mitte else -1.0
        nummer[(nummer == TEILE.index(f"arm_{seite}")) & ((xs_f - sx) * aussen < 0)] = rumpf
        nummer[(nummer == TEILE.index(f"bein_{seite}")) & (ys_f < gelenke[f"{seite}_hip"][1])] = rumpf
    feld = np.zeros(maske.shape, dtype=np.uint8)
    feld[ys, xs] = nummer + 1
    # Unter der Achsel gehört, was am Rumpf hängt (das Stück um die Mitte), zum Rumpf – ein weiter Pullover ist oft
    # breiter als die Schultern; zählte seine Seite zum Arm, klappte sie beim Armheben wie ein Flügel mit. Nur in
    # Zeilen, in denen der Arm frei ist: liegt sein Knochen in der Zeile im selben Stück wie die Mitte (waagerechter
    # Arm in T-Haltung), bleibt der Arm Arm.
    huefte = int(min(gelenke["l_hip"][1], gelenke["r_hip"][1]))
    for seite in ("l", "r"):
        arm = TEILE.index(f"arm_{seite}") + 1
        knochen_x = _arm_spur(gelenke, seite)
        for yy in range(int(gelenke[f"{seite}_shoulder"][1]) + 1, huefte):
            lauf = _lauf_bei(_laeufe(maske[yy]), mitte)
            if not lauf or any(lauf[0] <= x <= lauf[1] for x in knochen_x.get(yy, ())):
                continue
            zeile = feld[yy, lauf[0]:lauf[1] + 1]
            zeile[zeile == arm] = rumpf + 1
    return feld


def _arm_spur(gelenke: dict, seite: str) -> dict[int, list[float]]:
    """Wo der Arm-Knochen (Schulter – Ellbogen – Handgelenk – Hand) jede Bildzeile kreuzt: Zeile -> x-Werte."""
    spur: dict[int, list[float]] = {}
    namen = [f"{seite}_shoulder", f"{seite}_elbow", f"{seite}_wrist", f"{seite}_hand"]
    for von, bis in zip(namen, namen[1:]):
        (x0, y0), (x1, y1) = gelenke[von], gelenke[bis]
        schritte = max(2, int(math.hypot(x1 - x0, y1 - y0)))
        for i in range(schritte + 1):
            x, y = x0 + (x1 - x0) * i / schritte, y0 + (y1 - y0) * i / schritte
            spur.setdefault(int(round(y)), []).append(x)
    return spur


def _breiter(m, um: int):
    """Maske um `um` Pixel nach allen Seiten wachsen lassen (Überlappung an den Nähten)."""
    m = m.copy()
    for _ in range(um):
        w = m.copy()
        w[1:] |= m[:-1]
        w[:-1] |= m[1:]
        w[:, 1:] |= m[:, :-1]
        w[:, :-1] |= m[:, 1:]
        m = w
    return m


def _quer_laengs(xs, ys, a, b):
    """Lage von Punkten zur Strecke a–b: (Anteil längs 0 … 1, Abstand quer mit Vorzeichen)."""
    import numpy as np
    dx, dy = b[0] - a[0], b[1] - a[1]
    laenge = math.hypot(dx, dy) or 1e-9
    ux, uy = dx / laenge, dy / laenge
    rx, ry = xs - a[0], ys - a[1]
    return (rx * ux + ry * uy) / laenge, rx * -uy + ry * ux


def huelle(feld, gelenke: dict) -> list[dict]:
    """Die Hülle um das Skelett: je Röhre und Kappe die Pixel, die dazugehören (Maske), und der Knochen, der sie
    bewegt. Jedes Pixel eines Teils gehört zur nächsten Röhre seines Teils; über dem Hals nur zum Kopf. Kappen: die
    Pixel des Glieds (an der Hüfte auch des Rumpfs) in einem Kreis um das Gelenk, so groß wie das Glied dort
    dick ist."""
    import numpy as np
    raus = []
    hals_y = gelenke["neck"][1]
    kopf = ROEHREN.index((("neck", "head"), "neck", "kopf_oben"))
    dicke = {}
    h, w = feld.shape
    yy, xx = np.ogrid[:h, :w]
    for teil_nr, teil in enumerate(TEILE):
        ys, xs = np.nonzero(feld == teil_nr + 1)
        if not len(xs):
            continue
        xs_f, ys_f = xs.astype(np.float32), ys.astype(np.float32)
        eigene = [i for i, (kn, _v, _b) in enumerate(ROEHREN) if _gruppe(kn) == teil]
        abst = np.stack([_abstand(xs_f, ys_f, gelenke[ROEHREN[i][1]], gelenke[ROEHREN[i][2]]) for i in eigene])
        if teil == "rumpf":                               # klare Grenze am Hals: darüber Kopf, darunter Rücken
            k = eigene.index(kopf)
            abst[k, ys_f >= hals_y] = np.inf
            abst[:, ys_f < hals_y] = np.inf
            abst[k, ys_f < hals_y] = 0
        wahl = abst.argmin(axis=0)
        for j, i in enumerate(eigene):
            sel = wahl == j
            if not sel.any():
                continue
            m = np.zeros(feld.shape, dtype=bool)
            m[ys[sel], xs[sel]] = True
            _t, quer = _quer_laengs(xs_f[sel], ys_f[sel], gelenke[ROEHREN[i][1]], gelenke[ROEHREN[i][2]])
            dicke[i] = float(np.percentile(np.abs(quer), 90))
            raus.append({"art": "roehre", "knochen": KNOCHEN.index(ROEHREN[i][0]), "maske": m, "teil": teil_nr,
                         "roehre": i, "dicke": dicke[i]})
    for gelenk, kn in KAPPEN:
        teil_nr = TEILE.index(_gruppe(kn))
        roehre = next(i for i, (k, _v, _b) in enumerate(ROEHREN) if k == kn)
        radius = dicke.get(roehre)
        if not radius:
            continue
        gx, gy = gelenke[gelenk]
        # Pixel des Glieds um das Gelenk; an der Hüfte auch die des Rumpfs (die Hose) – die Kappe liegt unter allem
        # und füllt nur die Lücke, die sich beim Beugen zwischen den Röhren öffnet (der Keil zwischen Hüfte und Bein)
        eigen = (feld == teil_nr + 1) | ((feld == TEILE.index("rumpf") + 1) if gelenk.endswith("_hip") else False)
        m = ((xx - gx) ** 2 + (yy - gy) ** 2 <= (radius + ABSTAND_PX) ** 2) & eigen
        if m.any():
            raus.append({"art": "kappe", "knochen": KNOCHEN.index(kn), "maske": m, "teil": teil_nr})
    return raus


def _nachbar_knochen(knochen: tuple[str, str]) -> tuple[int | None, int | None]:
    """Der Knochen davor (zum Elterngelenk hin) und danach (die Röhre, die am Kindgelenk beginnt) – mit ihnen
    überblendet eine Röhre an ihren Gelenken."""
    e, k = knochen
    davor = (ELTERN[e], e) if e in ELTERN else None
    danach = next((kn for kn, _v, _b in ROEHREN if kn[0] == k), None)
    return (KNOCHEN.index(davor) if davor in KNOCHEN else None,
            KNOCHEN.index(danach) if danach in KNOCHEN else None)


def roehren_netz(stueck: dict, gelenke: dict) -> dict:
    """Ein Netz entlang der Röhre: Zeilen quer zum Knochen über die ganze Länge ihrer Pixel. In der Mitte hängt jeder
    Punkt nur an seinem Knochen (starr); in der Zone um ein Gelenk (GELENK_ZONE × halbe Dicke) wird zum Nachbarknochen
    überblendet – am Gelenk selbst halb und halb. So biegt sich die Hülle dort rund statt zu knicken."""
    import numpy as np
    kn, von, bis = ROEHREN[stueck["roehre"]]
    a, b = gelenke[von], gelenke[bis]
    laenge = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    ux, uy = (b[0] - a[0]) / laenge, (b[1] - a[1]) / laenge
    ys, xs = np.nonzero(stueck["maske"])
    t, q = _quer_laengs(xs.astype(np.float32), ys.astype(np.float32), a, b)
    rand = ABSTAND_PX + 2
    t0, t1 = float(t.min()) - rand / laenge, float(t.max()) + rand / laenge
    q0, q1 = float(q.min()) - rand, float(q.max()) + rand
    # höchstens eine halbe Röhre: an jeder Naht mischen dann genau zwei Knochen halb und halb – auf beiden Seiten
    # gleich, sonst passten kurze Röhren (der Rücken) an der Naht nicht aufeinander
    zone = min(0.5, GELENK_ZONE * max(stueck["dicke"], 2.0) / laenge)
    zeilen = set(np.linspace(t0, t1, ROEHRE_ZEILEN).round(5).tolist())
    zeilen |= {round(z, 5) for z in (-zone, 0.0, zone, 1 - zone, 1.0, 1 + zone) if t0 < z < t1}
    zeilen = sorted(zeilen)
    davor, danach = _nachbar_knochen(kn)
    eigen = KNOCHEN.index(kn)
    punkte, gewichte = [], []
    for tt in zeilen:
        w_davor = min(1.0, max(0.0, 0.5 - 0.5 * tt / zone)) if davor is not None else 0.0
        w_danach = min(1.0, max(0.0, 0.5 - 0.5 * (1 - tt) / zone)) if danach is not None else 0.0
        w_eigen = max(0.0, 1.0 - w_davor - w_danach)
        summe = w_eigen + w_davor + w_danach
        g = [[k, round(w / summe, 4)] for k, w in ((eigen, w_eigen), (davor, w_davor), (danach, w_danach))
             if k is not None and w > 1e-4]
        for qq in (q0, q1):
            punkte.append([round(a[0] + ux * tt * laenge - uy * qq, 2), round(a[1] + uy * tt * laenge + ux * qq, 2)])
            gewichte.append(g)
    dreiecke = []
    for z in range(len(zeilen) - 1):
        p, r = 2 * z, 2 * z + 2
        dreiecke += [[p, p + 1, r + 1], [p, r + 1, r]]
    return {"punkte": punkte, "dreiecke": dreiecke, "gewichte": gewichte}


def bauen(bild_pfad: Path, ziel: Path, ansicht: str) -> Path:
    """Eine Ansicht zur beweglichen Figur machen: je Röhre und Kappe ein zugeschnittenes Bild (stueck_<n>.png, an
    den Rändern ABSTAND_PX breiter, mit durchsichtigem Rand für das ganze Netz) und rig.json in `ziel`."""
    import numpy as np
    from PIL import Image
    with Image.open(bild_pfad) as roh:
        bild = roh.convert("RGBA")
    maske = _maske(bild)
    gelenke = gelenke_schaetzen(maske, ansicht)
    feld = teile_zuordnen(maske, gelenke)
    neu = ziel.with_name(ziel.name + ".neu")
    shutil.rmtree(neu, ignore_errors=True)
    neu.mkdir(parents=True)
    pixel = np.asarray(bild)
    gesichter = _gesichter(bild_pfad, bild.size) if ansicht == "vorne" else {}
    kopf = KNOCHEN.index(("neck", "head"))
    stuecke = []
    for nr, s in enumerate(huelle(feld, gelenke)):
        deckung = s["maske"].astype(np.float32)
        if s["art"] == "roehre":                         # über die Grenze hinaus, deckend: an den Gelenken liegen
            deckung[_breiter(s["maske"], ABSTAND_PX) & maske] = 1.0   # die Nachbarn genau aufeinander
        ys, xs = np.nonzero(deckung > 0)
        netz = roehren_netz(s, gelenke) if s["art"] == "roehre" else None
        # der Ausschnitt umfasst auch das ganze Netz (+ Rand): außerhalb des Bildes würde der Pinsel es wiederholen
        nx = [p[0] for p in netz["punkte"]] if netz else []
        ny = [p[1] for p in netz["punkte"]] if netz else []
        x0, y0 = int(math.floor(min([xs.min()] + nx))) - 3, int(math.floor(min([ys.min()] + ny))) - 3
        x1, y1 = int(math.ceil(max([xs.max() + 1] + nx))) + 3, int(math.ceil(max([ys.max() + 1] + ny))) + 3

        def zuschneiden(quelle):
            teil = quelle[qy0:qy1, qx0:qx1].copy()
            teil[:, :, 3] = (teil[:, :, 3] * deckung[qy0:qy1, qx0:qx1]).astype(np.uint8)
            stueck = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
            stueck[qy0 - y0:qy1 - y0, qx0 - x0:qx1 - x0] = teil
            return Image.fromarray(stueck)
        hh, ww = pixel.shape[:2]
        qy0, qy1, qx0, qx1 = max(0, y0), min(hh, y1), max(0, x0), min(ww, x1)
        datei = f"stueck_{nr}.png"
        zuschneiden(pixel).save(neu / datei, optimize=True)
        eintrag = {"art": s["art"], "knochen": s["knochen"], "bild": datei, "ursprung": [x0, y0]}
        if netz:
            eintrag["netz"] = netz
        if s["art"] == "roehre" and s["knochen"] == kopf and gesichter:
            # der Kopf je Ausdruck: genau wie die Kopf-Röhre zugeschnitten, das Fenster tauscht nur ihn
            eintrag["gesichter"] = {}
            for name, quelle in gesichter.items():
                zuschneiden(quelle).save(neu / f"stueck_{nr}_{name}.png", optimize=True)
                eintrag["gesichter"][name] = f"stueck_{nr}_{name}.png"
        stuecke.append(eintrag)
    ys = [p[1] for p in gelenke.values()]
    daten = {"version": RIG_VERSION, "ansicht": ansicht, "hoehe": max(ys) - min(ys) + 1, "gelenke": gelenke,
             "knochen": KNOCHEN, "stuecke": stuecke}
    (neu / "rig.json").write_text(json.dumps(daten, separators=(",", ":")), encoding="utf-8")
    shutil.rmtree(ziel, ignore_errors=True)
    neu.replace(ziel)
    return ziel / "rig.json"


def _gesichter(bild_pfad: Path, groesse: tuple[int, int]) -> dict:
    """Die Gesichter neben dem Körper-Bild (tools/figurgesicht.py), die zu ihm passen: Name -> Pixel (RGBA)."""
    import numpy as np
    from PIL import Image
    try:
        import figurgesicht
    except Exception:
        return {}
    raus = {}
    for name, pfad in figurgesicht.vorhanden(Path(bild_pfad).parent).items():
        try:
            with Image.open(pfad) as roh:
                if roh.size == groesse:
                    raus[name] = np.asarray(roh.convert("RGBA"))
        except Exception:
            continue
    return raus


def aktuell(rig: Path) -> bool:
    """Ist die gebaute Figur in der aktuellen Form (Version)?"""
    try:
        return json.loads(Path(rig).read_text(encoding="utf-8")).get("version") == RIG_VERSION
    except Exception:
        return False
