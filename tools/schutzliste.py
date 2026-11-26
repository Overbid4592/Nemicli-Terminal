"""
schutzliste.py - Sperrliste bekannter Schad- und Phishing-Adressen für den Web-Zugang.

Quellen sind die Sicherheitslisten, die uBlock Origin anbietet (QUELLEN). Übernommen werden
nur Regeln, die eine Seite als Ganzes sperren: `||host^`, `||host/pfad^` und bloße Hostnamen,
ohne Optionen oder mit doc/document/all/important/popup/reason. Ausnahmen (`@@`) heben eine
gleichlautende Sperre auf. Reine Host-Regeln für Domains der mitgelieferten Allowlist gelten
nicht – deren Vertrauen ist kuratiert; Regeln für einzelne Pfade dort gelten.

Gespeichert werden nur 8-Byte-Kürzel (SHA-256) der Adressen, keine Klartext-Adressen. Der
Abgleich läuft lokal, keine Adresse geht nach außen. Ist der Stand älter als ALTER_S, lädt
ein Hintergrund-Faden die Listen neu. Einmal gesperrt, immer gesperrt: neue Stände kommen dazu,
nichts fällt heraus. Jede exe bringt eine Momentaufnahme mit (beim Bau geladen), die beim ersten
Start dazukommt; ohne jede Liste wird nichts gesperrt.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

SCHALTER = "web_schutzliste"
ALTER_S = 24 * 3600
MAX_BYTES = 64 * 1024 * 1024
HOLEN_FRIST_S = 300.0            # Gesamtfrist je Liste (httpx begrenzt nur jeden Lese-Vorgang)
MAX_TRENNER = 64                 # Pfad-Präfixe je Adresse (lange Abfragen)

# Name -> (Anzeige, Adressen in Reihenfolge der Versuche)
QUELLEN: dict[str, tuple[str, tuple[str, ...]]] = {
    "badware": ("uBlock: Badware", (
        "https://ublockorigin.github.io/uAssetsCDN/filters/badware.min.txt",
        "https://ublockorigin.pages.dev/filters/badware.min.txt")),
    "urlhaus": ("URLhaus: Schadsoftware", (
        "https://malware-filter.gitlab.io/urlhaus-filter/urlhaus-filter-vivaldi-online.txt",
        "https://curbengh.github.io/malware-filter/urlhaus-filter-vivaldi-online.txt")),
    "phishing": ("Phishing", (
        "https://malware-filter.gitlab.io/phishing-filter/phishing-filter.txt",
        "https://curbengh.github.io/phishing-filter/phishing-filter.txt")),
}
_BIT = {name: 1 << i for i, name in enumerate(QUELLEN)}

_ERLAUBT = {"doc", "document", "all", "important", "popup", "reason"}
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_TRENNER_FREI = set("abcdefghijklmnopqrstuvwxyz0123456789_-.%")

MOMENT_BIN, MOMENT_META = "schutzliste_moment.bin", "schutzliste_moment.json"
SATZ = 9                                        # je Eintrag: 8 Byte Kürzel (big-endian), 1 Byte Bits der Quellen

_sperre = threading.RLock()
_geladen: bytes | None = None                  # Einträge, nach Kürzel sortiert (binäre Suche)
_geladen_mtime: float | None = None
_hintergrund_lief = False
_moment_geprueft = False


def _ordner() -> Path:
    import paths
    return Path(paths.DATEN) / "Cache"


def _bin_pfad() -> Path:
    return _ordner() / "schutzliste.bin"


def _meta_pfad() -> Path:
    return _ordner() / "schutzliste.json"


def an() -> bool:
    try:
        import config
        return config.load().get(SCHALTER, True) is not False
    except Exception:
        return True


def kuerzel(adresse: str) -> int:
    return int.from_bytes(hashlib.sha256(adresse.encode("utf-8")).digest()[:8], "big")


def _host_gueltig(host: str) -> bool:
    if _HOST.match(host):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def regel(zeile: str) -> tuple[str, bool] | None:
    """Eine Filterzeile → (Adresse „host“ oder „host/pfad“, ist_ausnahme) oder None."""
    z = zeile.strip()
    if not z or z[0] in "![#" or "##" in z or "#@#" in z or "#?#" in z or "#$#" in z:
        return None
    ausnahme = z.startswith("@@")
    if ausnahme:
        z = z[2:]
    rumpf, dollar, optionen = z.rpartition("$")
    if not dollar:
        rumpf, optionen = z, ""
    if optionen and any(o.split("=")[0] not in _ERLAUBT for o in optionen.split(",")):
        return None
    if rumpf.startswith("||"):
        rumpf = rumpf[2:]
        if rumpf.endswith("^"):
            rumpf = rumpf[:-1]
    elif "/" in rumpf or "|" in rumpf:
        return None
    rumpf = rumpf.lower()
    if not rumpf or "*" in rumpf or "^" in rumpf or "|" in rumpf:
        return None
    host = rumpf.split("/", 1)[0]
    return (rumpf, ausnahme) if _host_gueltig(host) else None


def _allowlist_schutz() -> tuple[set[str], set[str]]:
    """(Domains der mitgelieferten Allowlist, alle ihre Oberdomains)."""
    try:
        import webfetch
        domains = webfetch.mitgeliefert()
    except Exception:
        domains = set()
    ober = {".".join(d.split(".")[i:]) for d in domains for i in range(len(d.split(".")))}
    return domains, ober


def _gilt(adresse: str, domains: set[str], ober: set[str]) -> bool:
    """Reine Host-Regeln treffen keine kuratierte Domain (auch nicht deren Ober- oder Unterdomains)."""
    if "/" in adresse:
        return True
    if adresse in ober:
        return False
    teile = adresse.split(".")
    return not any(".".join(teile[i:]) in domains for i in range(1, len(teile)))


def auswerten(text: str, schutz: tuple[set[str], set[str]] | None = None) -> set[int]:
    """Kürzel aller Sperren einer Filterliste (Ausnahmen abgezogen)."""
    domains, ober = schutz or _allowlist_schutz()
    sperren, ausnahmen = set(), set()
    for zeile in text.splitlines():
        r = regel(zeile)
        if r is None:
            continue
        adresse, ist_ausnahme = r
        if ist_ausnahme:
            ausnahmen.add(kuerzel(adresse))
        elif _gilt(adresse, domains, ober):
            sperren.add(kuerzel(adresse))
    return sperren - ausnahmen


def kandidaten(url: str) -> list[str]:
    """Alle Schreibweisen, unter denen eine Regel diese Adresse trifft: der Host und jede
    Oberdomain, je allein und mit jedem Pfad-Anfang, der vor einem Trennzeichen endet."""
    try:
        teile = urlparse(url.strip())
        host = (teile.hostname or "").lower().rstrip(".")
    except ValueError:
        return []
    if not host:
        return []
    rest = (teile.path or "") + (f"?{teile.query}" if teile.query else "")
    rest = rest.lower()
    enden = [j for j in range(1, len(rest)) if rest[j] not in _TRENNER_FREI][:MAX_TRENNER]
    if rest:
        enden.append(len(rest))
    try:
        ipaddress.ip_address(host)
        hosts = [host]
    except ValueError:
        labels = host.split(".")
        hosts = [".".join(labels[i:]) for i in range(max(1, len(labels) - 1))]
    raus = []
    for h in hosts:
        raus.append(h)
        raus += [h + rest[:j] for j in enden]
    return raus


def _schluessel(daten: bytes, i: int) -> int:
    return int.from_bytes(daten[i * SATZ:i * SATZ + 8], "big")


def _lesen(pfad: Path) -> bytes:
    """Datei → Einträge (je SATZ Byte: Kürzel, Bits), nach Kürzel aufsteigend; fehlt sie: leer."""
    try:
        daten = pfad.read_bytes()
    except OSError:
        return b""
    daten = daten[:len(daten) // SATZ * SATZ]
    vorher = -1
    for i in range(len(daten) // SATZ):
        k = _schluessel(daten, i)
        if k <= vorher:                                  # unsortiert oder doppelt: neu ordnen
            return _vereinigen([daten])
        vorher = k
    return daten


def _paare(daten: bytes):
    for i in range(len(daten) // SATZ):
        yield _schluessel(daten, i), daten[i * SATZ + 8]


def _vereinigen(teile) -> bytes:
    """Mehrere Stände (Bytes oder {Kürzel: Bits}) zu einem: jedes Kürzel einmal, die Bits aller Teile vereint."""
    alle: dict[int, int] = {}
    for teil in teile:
        for k, b in (_paare(teil) if isinstance(teil, (bytes, bytearray)) else teil.items()):
            alle[k] = alle.get(k, 0) | b
    return b"".join(k.to_bytes(8, "big") + bytes([b]) for k, b in sorted(alle.items()))


def _suchen(daten: bytes, k: int) -> int:
    """Bits des Kürzels (binäre Suche in den sortierten Einträgen), 0 = nicht gesperrt."""
    lo, hi = 0, len(daten) // SATZ
    while lo < hi:
        mitte = (lo + hi) // 2
        m = _schluessel(daten, mitte)
        if m < k:
            lo = mitte + 1
        elif m > k:
            hi = mitte
        else:
            return daten[mitte * SATZ + 8]
    return 0


def _schreiben(pfad: Path, daten: bytes) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    zwischen = pfad.with_suffix(f".{os.getpid()}.tmp")
    zwischen.write_bytes(daten)
    os.replace(zwischen, pfad)


def _meta_schreiben(stand: dict) -> None:
    zwischen = _meta_pfad().with_suffix(f".{os.getpid()}.tmp")
    zwischen.write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(zwischen, _meta_pfad())


def _mitgeliefert() -> tuple[Path, Path]:
    """Momentaufnahme im Programm (beim Bau der exe geladen): Kürzel und Stand."""
    import paths
    return Path(paths.ASSETS) / MOMENT_BIN, Path(paths.ASSETS) / MOMENT_META


def _momentaufnahme_einspielen() -> None:
    """Einmal je Momentaufnahme: ihre Kürzel kommen zum eigenen Stand dazu. Eine neue Installation ist so
    vom ersten Start an geschützt, auch ohne Netz."""
    bin_, meta_ = _mitgeliefert()
    try:
        moment = json.loads(meta_.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    m = meta()
    if not bin_.is_file() or m.get("momentaufnahme") == moment.get("stand"):
        return
    daten = _vereinigen([_lesen(_bin_pfad()), _lesen(bin_)])
    _schreiben(_bin_pfad(), daten)
    if not m:
        m = {**moment, "quellen": moment.get("quellen", {})}
    m.update(eintraege=len(daten) // SATZ, momentaufnahme=moment.get("stand"))
    _meta_schreiben(m)


def _laden():
    """Einträge aus dem Speicher, neu gelesen nach einer Aktualisierung; None ohne Liste."""
    global _geladen, _geladen_mtime, _moment_geprueft
    with _sperre:
        if not _moment_geprueft:
            _moment_geprueft = True
            try:
                _momentaufnahme_einspielen()
            except Exception:
                pass
        try:
            mtime = _bin_pfad().stat().st_mtime
        except OSError:
            return None
        if _geladen is not None and _geladen_mtime == mtime:
            return _geladen
        _geladen, _geladen_mtime = _lesen(_bin_pfad()), mtime
        return _geladen


def meta() -> dict:
    try:
        return json.loads(_meta_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gesperrt(url: str) -> str | None:
    """Name der Liste(n), auf der die Adresse steht – oder None (auch: Schalter aus, keine Liste)."""
    if not an():
        return None
    _hintergrund_pruefen()
    daten = _laden()
    if not daten:
        return None
    bits, schutz = 0, None
    for adresse in kandidaten(url):
        b = _suchen(daten, kuerzel(adresse))
        if not b:
            continue
        if "/" not in adresse:
            # Kuratierte Domains gelten auch gegen Einträge aus früheren Ständen
            schutz = schutz or _allowlist_schutz()
            if not _gilt(adresse, *schutz):
                continue
        bits |= b
    if not bits:
        return None
    return ", ".join(QUELLEN[n][0] for n, b in _BIT.items() if bits & b)


def meldung(url: str, liste: str) -> str:
    try:
        host = urlparse(url).hostname or url
    except ValueError:
        host = url
    return (f"⛔ {host} steht auf der Schutzliste ({liste}): bekannte Schad- oder Phishing-Adresse – "
            "nicht geladen. Die Adresse nicht weitergeben und nicht zum Öffnen empfehlen.")


def _holen(adressen: tuple[str, ...]) -> str:
    import netzhilfe
    fehler = ""
    for adresse in adressen:
        ende = time.monotonic() + HOLEN_FRIST_S
        try:
            with httpx.Client(timeout=60.0, follow_redirects=False) as c, c.stream("GET", adresse) as r:
                if r.status_code != 200:
                    fehler = f"HTTP {r.status_code}"
                    continue
                return netzhilfe.lesen(r, ende, MAX_BYTES).decode("utf-8", errors="replace")
        except (httpx.HTTPError, ValueError) as e:
            fehler = type(e).__name__
    raise RuntimeError(fehler or "nicht erreichbar")


def aktualisieren() -> dict:
    """Alle Listen neu laden und zum bisherigen Stand dazunehmen: einmal gesperrt, immer gesperrt – auch
    wenn eine Adresse später aus der Quelle fällt (kurzlebige Schadseiten tauchen unter neuem Namen wieder auf).
    Eine Quelle, die nicht lädt, ändert nichts. Gibt den neuen Stand (meta) zurück."""
    with _sperre:
        alt = _lesen(_bin_pfad())
    alt_meta = meta()
    schutz = _allowlist_schutz()
    teile, quellen, aktuell = [alt], {}, set()
    jetzt = time.time()
    for name, (anzeige, adressen) in QUELLEN.items():
        try:
            satz = auswerten(_holen(adressen), schutz)
        except Exception as e:
            quellen[name] = {**alt_meta.get("quellen", {}).get(name, {}), "anzeige": anzeige, "fehler": str(e)}
            continue
        quellen[name] = {"anzeige": anzeige, "eintraege": len(satz), "stand": jetzt}
        aktuell |= satz
        teile.append(dict.fromkeys(satz, _BIT[name]))
    daten = _vereinigen(teile)
    with _sperre:
        _schreiben(_bin_pfad(), daten)
    n = len(daten) // SATZ
    stand = {"stand": jetzt, "eintraege": n, "quellen": quellen,
             "frueher": n - len(aktuell) if aktuell else alt_meta.get("frueher", 0)}
    if alt_meta.get("momentaufnahme"):
        stand["momentaufnahme"] = alt_meta["momentaufnahme"]
    _meta_schreiben(stand)
    return stand


def momentaufnahme(ziel: Path) -> dict:
    """Für den Bau der exe: Listen aktualisieren und den ganzen Stand als Momentaufnahme nach `ziel` legen."""
    stand = aktualisieren()
    ziel = Path(ziel)
    _schreiben(ziel / MOMENT_BIN, _lesen(_bin_pfad()))
    (ziel / MOMENT_META).write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")
    return stand


def _hintergrund_pruefen() -> None:
    """Einmal je Prozess: veralteten oder fehlenden Stand im Hintergrund erneuern (nie in Tests)."""
    global _hintergrund_lief
    if _hintergrund_lief:
        return
    _hintergrund_lief = True
    try:
        import paths
        if paths._testlauf():
            return
    except Exception:
        return
    if time.time() - float(meta().get("stand") or 0) < ALTER_S:
        return

    def lauf() -> None:
        try:
            aktualisieren()
        except Exception:
            pass
    threading.Thread(target=lauf, name="schutzliste", daemon=True).start()


def status_text() -> str:
    """Kurzer Stand für /web schutz."""
    if not an():
        return "⛔ Schutzliste: aus  ·  /web schutz an"
    m = meta()
    if not m:
        return "⛔ Schutzliste: noch nicht geladen  ·  /web schutz aktualisieren"
    zeilen = [f"⛔ Schutzliste: an · {m.get('eintraege', 0):,} Adressen · Stand "
              f"{time.strftime('%d.%m.%Y %H:%M', time.localtime(float(m.get('stand') or 0)))}".replace(",", ".")]
    if m.get("frueher"):
        zeilen.append(f"   davon aus früheren Ständen: {m['frueher']:,} (bleiben gesperrt)".replace(",", "."))
    if m.get("momentaufnahme"):
        zeilen.append("   Momentaufnahme im Programm: "
                      + time.strftime("%d.%m.%Y", time.localtime(float(m["momentaufnahme"]))))
    for q in (m.get("quellen") or {}).values():
        zeile = f"   {q.get('anzeige')}: {q.get('eintraege', 0):,} in der aktuellen Liste".replace(",", ".")
        if q.get("fehler"):
            zeile += f"  (letzte Aktualisierung fehlgeschlagen: {q['fehler']} – alter Stand bleibt)"
        zeilen.append(zeile)
    zeilen.append("   /web schutz aktualisieren · an · aus")
    return "\n".join(zeilen)
