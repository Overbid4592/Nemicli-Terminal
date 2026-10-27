"""Netz-Auskunft: Wer steckt hinter einer Adresse?

Drei Quellen, alle ohne Adminrechte:

  Name      a) was NemiCLI selbst nachgeschlagen hat (netzspur, mit Zweck),
            b) der DNS-Speicher von Windows (`ipconfig /displaydns`): welchen Namen ein Programm
               aufgelöst hat, bevor es sich zu dieser Adresse verbunden hat,
            c) der beim Ereignis gestempelte Name (`extra.name`), wenn der Speicher längst leer ist.
  Besitzer  das offizielle Adressregister (RDAP: ARIN, RIPE, APNIC, LACNIC, AFRINIC) – nur für
            öffentliche Adressen, nur diese fünf Server, Ergebnis im Speicher der Wache gemerkt
            (BESITZER_TAGE).
  Zweck     für Dienste, die NemiCLI selbst nutzt (ZWECK).

Kein Urteil: Die Auskunft liefert Fakten, bewerten tut die Wache bzw. die Persönlichkeit.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import netzspur

def _system32(name: str) -> str:
    """Systemprogramm mit vollem Pfad: sonst suchte Windows zuerst im (beschreibbaren) Arbeitsordner."""
    import os
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", name)


DNS_ALTER_S = 30.0             # DNS-Speicher höchstens so oft neu lesen
BESITZER_TAGE = 30
RDAP_START = "https://rdap.arin.net/registry/ip/"   # leitet für fremde Netze ans zuständige Register weiter
RDAP_SERVER = {"rdap.arin.net", "rdap.db.ripe.net", "rdap.apnic.net", "rdap.lacnic.net", "rdap.afrinic.net"}

# Dienste, die NemiCLI selbst anspricht: (Namensende, Zweck)
ZWECK = [
    ("ollama.com", "Ollama: Web-Suche, Seiten lesen, Cloud-Modelle, Modell-Liste"),
    ("anthropic.com", "Claude (Anthropic) – Cloud-Modell"),
    ("claude.ai", "Claude (Anthropic)"),
    ("openai.com", "OpenAI – Cloud-Modell"),
    ("generativelanguage.googleapis.com", "Google Gemini – Cloud-Modell"),
    ("mistral.ai", "Mistral – Cloud-Modell"),
    ("cohere.com", "Cohere – Cloud-Modell"),
    ("cohere.ai", "Cohere – Cloud-Modell"),
    ("perplexity.ai", "Perplexity – Cloud-Modell mit Internet"),
    ("deepseek.com", "DeepSeek – Cloud-Modell"),
    ("x.ai", "xAI (Grok) – Cloud-Modell"),
    ("groq.com", "Groq – Cloud-Modell"),
    ("openrouter.ai", "OpenRouter – Cloud-Modelle"),
    ("huggingface.co", "Hugging Face – Modelle herunterladen"),
    ("hf.co", "Hugging Face – Modelle herunterladen"),
    ("githubusercontent.com", "GitHub – Dateien und Updates herunterladen"),
    ("github.com", "GitHub – Updates und Downloads"),
    ("pypi.org", "Python-Pakete (/update)"),
    ("pythonhosted.org", "Python-Pakete (/update)"),
    ("pytorch.org", "PyTorch-Pakete (/update)"),
    ("wikipedia.org", "Wikipedia (web_wiki)"),
    ("ncbi.nlm.nih.gov", "PubMed (fach_suche)"),
    ("rdap.arin.net", "Adressregister – Netz-Auskunft der Wache"),
    ("rdap.db.ripe.net", "Adressregister – Netz-Auskunft der Wache"),
    ("rdap.apnic.net", "Adressregister – Netz-Auskunft der Wache"),
    ("rdap.lacnic.net", "Adressregister – Netz-Auskunft der Wache"),
    ("rdap.afrinic.net", "Adressregister – Netz-Auskunft der Wache"),
]
_ZWEITSTUFE = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go"}
_WERT = re.compile(r"\.\s*:\s*(\S+)\s*$")


def oeffentlich(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def registriert(name: str) -> str:
    """Registrierte Domain zu einem Namen: „cdn-lfs.hf.co“ → „hf.co“, „www.bbc.co.uk“ → „bbc.co.uk“."""
    teile = [t for t in (name or "").lower().strip(".").split(".") if t]
    if len(teile) <= 2:
        return ".".join(teile)
    if len(teile[-1]) == 2 and teile[-2] in _ZWEITSTUFE:
        return ".".join(teile[-3:])
    return ".".join(teile[-2:])


def zweck(name: str) -> str:
    n = (name or "").lower().rstrip(".")
    for ende, text in ZWECK:
        if n == ende or n.endswith("." + ende):
            return text
    return ""


# ---------------------------------------------------------------- DNS-Speicher

def dns_speicher_parsen(text: str) -> dict[str, str]:
    """Ausgabe von `ipconfig /displaydns` → {Adresse: Name}. Sprachunabhängig: Ein Abschnitt beginnt
    mit dem nachgefragten Namen über einer Strichlinie; jede Adresse darin gehört zu diesem Namen
    (auch über CNAME-Ketten von CDNs hinweg)."""
    zeilen = text.splitlines()
    karte: dict[str, str] = {}
    kopf = ""
    for i, z in enumerate(zeilen):
        s = z.strip()
        if i + 1 < len(zeilen) and set(zeilen[i + 1].strip()) == {"-"} and s:
            kopf = "" if s.lower().endswith(".arpa") else s.lower().rstrip(".")
            continue
        if not kopf:
            continue
        m = _WERT.search(s)
        if not m:
            continue
        try:
            ip = str(ipaddress.ip_address(m.group(1)))
        except ValueError:
            continue
        karte.setdefault(ip, kopf)
    return karte


class DnsSpeicher:
    def __init__(self):
        self.karte: dict[str, str] = {}
        self.gelesen = 0.0
        self._lock = threading.Lock()

    def lesen(self) -> dict[str, str]:
        try:
            r = subprocess.run([_system32("ipconfig.exe"), "/displaydns"], capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return {}
        try:
            text = r.stdout.decode("oem", errors="replace")
        except LookupError:
            text = r.stdout.decode("cp850", errors="replace")
        return dns_speicher_parsen(text)

    def name(self, ip: str, auffrischen: bool = True) -> str:
        with self._lock:
            if ip in self.karte:
                return self.karte[ip]
            if not auffrischen or time.time() - self.gelesen < DNS_ALTER_S:
                return ""
            self.gelesen = time.time()
        neu = self.lesen()
        with self._lock:
            self.karte.update(neu)
            if len(self.karte) > 20_000:
                self.karte = dict(list(self.karte.items())[-10_000:])
            return self.karte.get(ip, "")


# ---------------------------------------------------------------- Adressregister

class _NurRegister(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen nur zu den fünf Adressregistern folgen."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).hostname not in RDAP_SERVER:
            raise urllib.error.HTTPError(newurl, code, "Weiterleitung außerhalb der Register", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def rdap_lesen(daten: dict) -> dict:
    """RDAP-Antwort → {"von", "bis", "netz", "org", "land"}. Besitzer: zuerst der Inhaber
    (registrant), dann Verwaltung/Technik – Abuse-Kontakte und reine Kürzel (RIPE: „…-MNT“) nicht,
    wenn die Beschreibung des Netzes einen Klartext-Namen hat."""
    kandidaten: list[tuple[int, str]] = []
    for ent in daten.get("entities") or []:
        rollen = ent.get("roles") or []
        vcard = (ent.get("vcardArray") or [None, []])[1] or []
        fn = str(next((f[3] for f in vcard if isinstance(f, list) and len(f) > 3 and f[0] == "fn"), "") or "")
        if not fn or rollen == ["abuse"]:
            continue
        rang = 0 if "registrant" in rollen else 1 if "administrative" in rollen else 2
        if re.fullmatch(r"[A-Z0-9-]+", fn):
            rang += 5
        kandidaten.append((rang, fn))
    org = min(kandidaten)[1] if kandidaten else ""
    if not org or re.fullmatch(r"[A-Z0-9-]+", org):
        for r in daten.get("remarks") or []:
            beschreibung = [str(z) for z in (r.get("description") or []) if str(z).strip()]
            if beschreibung and str(r.get("title") or "").lower() in ("description", "beschreibung", ""):
                org = beschreibung[0].strip()
                break
    return {"von": str(daten.get("startAddress") or ""), "bis": str(daten.get("endAddress") or ""),
            "netz": str(daten.get("name") or daten.get("handle") or ""), "org": org,
            "land": str(daten.get("country") or "")}


def rdap_abfragen(ip: str, timeout: float = 8.0) -> dict | None:
    if not oeffentlich(ip):
        return None
    oeffner = urllib.request.build_opener(_NurRegister)
    anfrage = urllib.request.Request(RDAP_START + urllib.parse.quote(ip),
                                     headers={"Accept": "application/rdap+json", "User-Agent": "NemiCLI-Wache"})
    try:
        with oeffner.open(anfrage, timeout=timeout) as r:
            if urllib.parse.urlparse(r.geturl()).hostname not in RDAP_SERVER:
                return None
            daten = json.loads(r.read(512_000).decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    d = rdap_lesen(daten)
    return d if d["von"] and d["bis"] else None


# ---------------------------------------------------------------- Auskunft

class Auskunft:
    """Bündelt die Quellen. `speicher` ist der Speicher der Wache (Besitzer-Cache, Ereignisse)."""

    def __init__(self, speicher, *, register: bool = True):
        self.speicher = speicher
        self.register = register
        self.dns = DnsSpeicher()
        self._eigene: dict[str, tuple[str, str]] = {}      # ip → (name, programm)
        self._eigene_gelesen = 0.0

    def _eigene_auffrischen(self) -> None:
        if time.time() - self._eigene_gelesen < DNS_ALTER_S:
            return
        self._eigene_gelesen = time.time()
        for d in netzspur.lesen():
            for ip in d["ips"]:
                self._eigene[str(ip)] = (str(d["name"]), str(d.get("programm") or ""))

    def eigener(self, ip: str) -> tuple[str, str] | None:
        self._eigene_auffrischen()
        return self._eigene.get(ip)

    def name(self, ip: str) -> str:
        """Name zur Adresse für den Stempel am Ereignis – nur lokale Quellen, keine Netzabfrage."""
        if not ip:
            return ""
        e = self.eigener(ip)
        return e[0] if e else self.dns.name(ip)

    def besitzer(self, ip: str, abfragen: bool = True) -> dict | None:
        if not oeffentlich(ip):
            return None
        try:
            d = self.speicher.besitzer_fuer(ip, max_alter_s=BESITZER_TAGE * 86400)
        except Exception:
            d = None
        if d or not (abfragen and self.register):
            return d
        d = rdap_abfragen(ip)
        if d:
            try:
                self.speicher.besitzer_merken(d)
            except Exception:
                pass
        return d

    def bericht(self, ip: str, abfragen: bool = True) -> dict:
        """Alles, was über eine Adresse bekannt ist."""
        ip = str(ipaddress.ip_address(ip.strip().strip("[]")))
        eigen = self.eigener(ip)
        name = eigen[0] if eigen else self.dns.name(ip) or self.speicher.netz_name_fuer(ip)
        return {"ip": ip, "oeffentlich": oeffentlich(ip), "name": name,
                "domain": registriert(name) if name else "",
                "zweck": zweck(name) if name else "", "eigen": bool(eigen),
                "eigen_programm": eigen[1] if eigen else "",
                "besitzer": self.besitzer(ip, abfragen),
                "programme": self.speicher.netz_programme_fuer(ip)}


def bericht_text(b: dict) -> str:
    """Kurzer Text für die Persönlichkeit und /wache netz <adresse>."""
    zeilen = [f"Adresse: {b['ip']}" + ("" if b["oeffentlich"] else " (privat/lokal)")]
    if b["name"]:
        zeilen.append(f"Name: {b['name']}" + (f" (Domain {b['domain']})" if b["domain"] != b["name"] else ""))
    else:
        zeilen.append("Name: unbekannt – kein Programm hat dazu einen Namen nachgeschlagen (direkte Verbindung "
                      "oder der DNS-Speicher ist schon geleert)")
    if b["eigen"]:
        zeilen.append(f"NemiCLI selbst hat diesen Namen aufgelöst ({b['eigen_programm'] or 'NemiCLI'})"
                      + (f" – Zweck: {b['zweck']}" if b["zweck"] else ""))
    elif b["zweck"]:
        zeilen.append(f"Bekannter Dienst: {b['zweck']}")
    bs = b.get("besitzer")
    if bs:
        zeilen.append(f"Besitzer laut Register: {bs.get('org') or '?'} · Netz {bs.get('netz') or '?'} "
                      f"({bs.get('von')} – {bs.get('bis')})" + (f" · Land {bs['land']}" if bs.get("land") else ""))
    elif b["oeffentlich"]:
        zeilen.append("Besitzer: nicht abgefragt oder Register nicht erreichbar")
    progs = b.get("programme") or []
    if progs:
        zeilen.append("Gesehen bei: " + "; ".join(
            f"{p['prozess'] or '?'} ({p['anzahl']}×, zuletzt {time.strftime('%d.%m. %H:%M', time.localtime(p['zuletzt']))})"
            for p in progs[:6]))
    else:
        zeilen.append("Von der Wache bisher nicht als Verbindung gesehen.")
    return "\n".join(zeilen)
