"""
einstellungen.py - Einstellungen, die die KI auf Wunsch ändern darf (Werkzeug einstellung_aendern).

Nur was in WAS steht, sonst nichts. Jede Änderung fragt in jedem Modus (auch Auto) mit
Vorher/Nachher, steht im Protokoll und lässt sich mit /undo zurückholen. Kam in der Runde
Text aus dem Netz, wird abgelehnt: eine Webseite darf keine Freigabe durchreichen.

Bewusst nicht hier, die bleiben beim Nutzer: Lösch-Limit, Sandbox-Freigaben, /schluessel,
Schreibsperre, Befehls-Whitelist.

Eigene Internet-Einträge liegen in allowlist_eigen.json im Programm-Ordner – getrennt von der
mitgelieferten allowlist.json und für die Datei-Werkzeuge der KI gesperrt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

WAS = {
    "internet_erlauben": "Domain zur Internet-Allowlist hinzufügen (web_lesen darf sie dann laden)",
    "internet_sperren": "Domain aus der Internet-Allowlist nehmen",
}

# Wie deny_by_default in allowlist.json: dort kann jeder alles ablegen oder verstecken.
_NIE_ERLAUBT = (
    # Paste-Dienste
    "pastebin.com", "paste.ee", "hastebin.com", "ghostbin.com", "rentry.co", "rentry.org", "justpaste.it",
    "dpaste.org", "dpaste.com", "controlc.com", "privatebin.net",
    # Kurz-Links
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly", "cutt.ly",
    "shorturl.at", "t.ly", "tiny.cc", "s.id",
    # freies Hosting
    "netlify.app", "gitbook.io", "readme.io", "vercel.app", "pages.dev", "github.io", "gitlab.io",
    "glitch.me", "herokuapp.com", "web.app", "firebaseapp.com", "blogspot.com", "wordpress.com",
    "wixsite.com", "weebly.com", "000webhostapp.com", "repl.co", "replit.app", "onrender.com", "surge.sh",
    "neocities.org", "carrd.co", "notion.site", "ngrok.io", "ngrok-free.app", "trycloudflare.com",
    # Datei-Hoster
    "mega.nz", "mediafire.com", "anonfiles.com", "gofile.io", "transfer.sh", "catbox.moe",
)
# Öffentliche Suffixe: eine Freigabe gälte für jede Seite darunter (Registrierung für jedermann)
_OEFFENTLICHE_SUFFIXE = (
    "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "co.kr", "or.kr", "com.br", "net.br", "org.br", "com.cn",
    "net.cn", "org.cn", "com.tw", "com.hk", "com.sg", "com.my", "co.in", "net.in", "org.in", "co.za", "org.za",
    "com.mx", "com.ar", "com.tr", "com.ua", "com.pl", "co.il", "com.es", "co.at", "or.at", "com.de", "eu.org",
    "co.com", "us.com", "uk.com", "de.com", "eu.com", "co.de",
)
# Freie Hoster und Cloud-Speicher: dort kann jeder eine Seite ablegen
_FREIE_HOSTER = (
    "workers.dev", "pages.dev", "azurewebsites.net", "azurestaticapps.net", "azureedge.net", "blob.core.windows.net",
    "web.core.windows.net", "cloudfront.net", "amazonaws.com", "on.aws", "appspot.com", "run.app",
    "cloudfunctions.net", "storage.googleapis.com", "firebaseio.com", "hf.space", "gradio.live", "streamlit.app",
    "fly.dev", "r2.dev", "codeberg.page", "deno.dev", "railway.app", "koyeb.app", "pythonanywhere.com",
    "bitbucket.io", "sourceforge.io", "ipfs.io", "dweb.link", "translate.goog", "ngrok.app", "loca.lt",
    "vercel.sh", "netlify.com", "render.com", "replit.dev", "w3spaces.com", "framer.website", "webflow.io",
)


def warnung(domain_name: str) -> str:
    """Hinweis für das Prüffenster bei öffentlichen Suffixen und freien Hostern – keine Sperre."""
    d = str(domain_name or "").lower().strip(".")
    for s in _OEFFENTLICHE_SUFFIXE:
        if d == s:
            return (f"⚠ WARNUNG: {d} ist ein öffentliches Suffix – die Freigabe gälte für JEDE Seite unter .{d}, "
                    "die sich jeder registrieren kann. Besser die genaue Domain eintragen.")
    for h in _FREIE_HOSTER:
        if d == h:
            return (f"⚠ WARNUNG: {d} ist ein freier Hoster bzw. Cloud-Speicher – die Freigabe gälte für die Seiten "
                    "ALLER Kunden dort. Besser die genaue Unterdomain eintragen.")
        if d.endswith("." + h):
            return (f"⚠ WARNUNG: {d} liegt bei {h} (freier Hoster bzw. Cloud-Speicher) – dort kann jeder eine Seite "
                    "ablegen. Nur freigeben, wenn du dem Betreiber dieser Seite vertraust.")
    return ""


def nie_erlaubt(host: str) -> str | None:
    """Der Eintrag aus _NIE_ERLAUBT, zu dem `host` gehört (Paste-Dienst, Kurz-Link, freies Hosting) – sonst None."""
    h = str(host or "").lower().strip(".")
    for d in _NIE_ERLAUBT:
        if h == d or h.endswith("." + d):
            return d
    return None


_DOMAIN = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")


def datei() -> Path:
    try:
        from paths import INSTALL
        return Path(INSTALL) / "allowlist_eigen.json"
    except Exception:
        return Path(__file__).resolve().parent.parent / "allowlist_eigen.json"


def laden() -> dict:
    """{"hinzu": [Eintrag, …], "weg": [Domain, …]} – leer, wenn die Datei fehlt oder kaputt ist."""
    try:
        d = json.loads(datei().read_text(encoding="utf-8"))
    except Exception:
        return {"hinzu": [], "weg": []}
    hinzu = [e for e in d.get("hinzu", []) if isinstance(e, dict) and isinstance(e.get("domain"), str)]
    weg = [w for w in d.get("weg", []) if isinstance(w, str)]
    return {"hinzu": hinzu, "weg": weg}


def domain(roh) -> str:
    """Eingabe (Domain oder URL) → kleingeschriebene Domain. ValueError, wenn sie nicht taugt."""
    s = str(roh or "").strip().lower()
    if not s:
        raise ValueError("Keine Domain angegeben.")
    if "*" in s:
        raise ValueError("Platzhalter (*) gibt es nicht – Subdomains sind automatisch dabei.")
    if "://" in s:
        if not s.startswith("https://"):
            raise ValueError("Nur https-Seiten sind erlaubt.")
        s = urlparse(s).hostname or ""
    else:
        s = s.split("/")[0]
    s = s.strip(".")
    if s.startswith("www."):
        s = s[4:]
    try:
        s = s.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"{roh!r} ist keine gültige Domain.") from None
    if not _DOMAIN.match(s):
        raise ValueError(f"{roh!r} ist keine gültige Domain (z. B. example.org; keine IP-Adressen).")
    import webfetch
    if webfetch.is_private(s):
        raise ValueError(f"{s} ist eine lokale Adresse – nicht erlaubt.")
    if (d := nie_erlaubt(s)):
        raise ValueError(f"{s} gehört zu {d} (Paste-Dienst, Kurz-Link oder freies Hosting) – "
                         "dort kann jeder alles ablegen. Nicht erlaubt.")
    return s


def _neu(was: str, wert, grund: str) -> tuple[dict, str]:
    """(neuer Dateiinhalt, Satz zur Änderung). ValueError bei unzulässigem Wunsch."""
    if was not in WAS:
        raise ValueError(f"Unbekannte Einstellung {was!r}. Möglich: {', '.join(WAS)}.")
    import actions
    if actions.web_tainted():
        raise ValueError("In dieser Runde kam Text aus dem Netz – Einstellungen ändere ich nur auf "
                         "direkten Wunsch des Nutzers. Bitte noch einmal ohne Websuche fragen.")
    import webfetch
    d = domain(wert)
    stand = laden()
    hinzu = [e for e in stand["hinzu"] if e["domain"] != d]
    weg = [w for w in stand["weg"] if w != d]
    mitgeliefert = d in webfetch.mitgeliefert()
    if was == "internet_erlauben":
        if webfetch.is_allowed("https://" + d):
            raise ValueError(f"{d} ist schon erlaubt.")
        if not mitgeliefert:
            hinzu.append({"domain": d, "tier": 2, "category": "eigen",
                          "note": ("eigene Freigabe: " + " ".join(str(grund or "").split()))[:120].rstrip(": ")})
        satz = (f"{d} wird wieder erlaubt." if mitgeliefert
                else f"{d} wird erlaubt (Stufe 2: Inhalt nicht automatisch vertrauen).")
    else:
        eigen = len(hinzu) < len(stand["hinzu"])
        if d in stand["weg"] or not (mitgeliefert or eigen):
            raise ValueError(f"{d} steht nicht auf der Liste.")
        if mitgeliefert:
            weg.append(d)
        satz = f"{d} wird aus der Liste genommen."
    return {"hinzu": hinzu, "weg": sorted(set(weg))}, satz


def _text(inhalt: dict) -> str:
    return json.dumps(inhalt, ensure_ascii=False, indent=2) + "\n"


def vorschau(was: str, wert, grund: str = "") -> str:
    """Vorher/Nachher für das Prüffenster. ValueError, wenn die Änderung nicht erlaubt ist."""
    neu, satz = _neu(was, wert, grund)
    p = datei()
    alt = p.read_text(encoding="utf-8") if p.is_file() else ""
    import actions
    hinweis = warnung(domain(wert)) if was == "internet_erlauben" else ""
    return (f"Einstellung: {WAS[was]}\n{satz}\n" + (f"{hinweis}\n" if hinweis else "")
            + f"Datei: {p}\n\n{actions.vergleich(alt, _text(neu))}")


def aendern(was: str, wert, grund: str = "") -> str:
    neu, satz = _neu(was, wert, grund)
    p = datei()
    import snapshot
    kopie = snapshot.sichern(p, "einstellung_aendern") if p.is_file() else None
    from atomar import text_schreiben
    text_schreiben(p, _text(neu))
    import webfetch
    webfetch.neu_laden()
    return satz.replace(" wird ", " ist jetzt ", 1) + (" Alter Stand im Papierkorb (/undo)." if kopie else "")
