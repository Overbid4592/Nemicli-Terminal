"""
fachquellen.py - Suche in Fachquellen über ihre offiziellen, freien Schnittstellen (ohne Schlüssel).

  arxiv          export.arxiv.org/api/query           Preprints (Atom)
  pubmed         eutils.ncbi.nlm.nih.gov              Medizin (esearch + esummary)
  github         api.github.com/search/repositories   Projekte
  stackoverflow  api.stackexchange.com/2.3            Fragen und Antworten
  openalex       api.openalex.org/works               wissenschaftliche Werke

Alle Hosts liegen in der Allowlist. Ergebnisse sind Fremddaten (webfetch._untrusted).
Freie Limits (GitHub 10/min, Stack Exchange 300/Tag) schont ein kurzer Zwischenspeicher.
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET

import httpx

import webfetch

QUELLEN = {
    "arxiv": ("arXiv", 1),
    "pubmed": ("PubMed", 1),
    "github": ("GitHub", 2),
    "stackoverflow": ("Stack Overflow", 2),
    "openalex": ("OpenAlex", 1),
}
_CACHE_S = 600
_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _kurz(t: str, n: int = 400) -> str:
    t = " ".join(html.unescape(t or "").split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …"


def _get(url: str, params: dict, headers: dict | None = None) -> httpx.Response:
    with httpx.Client(timeout=20.0, headers={**webfetch._UA, **(headers or {})}) as c:
        r = c.get(url, params=params)
    r.raise_for_status()
    return r


def _arxiv(q: str, n: int) -> list[dict]:
    r = _get("https://export.arxiv.org/api/query",
             {"search_query": f"all:{q}", "start": 0, "max_results": n, "sortBy": "relevance"})
    raus = []
    for e in ET.fromstring(r.content).findall("a:entry", _ATOM):
        def feld(name: str) -> str:
            return (e.findtext(f"a:{name}", "", _ATOM) or "").strip()
        autoren = [a.findtext("a:name", "", _ATOM) for a in e.findall("a:author", _ATOM)]
        raus.append({"titel": _kurz(feld("title"), 200), "url": feld("id").replace("http://", "https://"),
                     "info": f"{feld('published')[:10]} · {', '.join(autoren[:3])}"
                             + (" u. a." if len(autoren) > 3 else ""),
                     "text": _kurz(feld("summary"))})
    return raus


def _pubmed(q: str, n: int) -> list[dict]:
    basis = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    ids = _get(basis + "esearch.fcgi", {"db": "pubmed", "term": q, "retmode": "json",
                                         "retmax": n}).json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    daten = _get(basis + "esummary.fcgi", {"db": "pubmed", "id": ",".join(ids),
                                           "retmode": "json"}).json().get("result", {})
    raus = []
    for i in ids:
        d = daten.get(i) or {}
        autoren = [a.get("name", "") for a in d.get("authors") or []]
        raus.append({"titel": _kurz(d.get("title", ""), 200), "url": f"https://pubmed.ncbi.nlm.nih.gov/{i}/",
                     "info": f"{d.get('pubdate', '')} · {d.get('fulljournalname') or d.get('source', '')}"
                             + (f" · {', '.join(autoren[:3])}" if autoren else ""),
                     "text": ""})
    return raus


def _github(q: str, n: int) -> list[dict]:
    items = _get("https://api.github.com/search/repositories", {"q": q, "per_page": n},
                 {"Accept": "application/vnd.github+json"}).json().get("items", [])
    return [{"titel": i.get("full_name", ""), "url": i.get("html_url", ""),
             "info": f"★ {i.get('stargazers_count', 0)} · {i.get('language') or '–'} · "
                     f"zuletzt {str(i.get('pushed_at') or '')[:10]}"
                     + (" · archiviert" if i.get("archived") else ""),
             "text": _kurz(i.get("description") or "")} for i in items[:n]]


def _stackoverflow(q: str, n: int) -> list[dict]:
    items = _get("https://api.stackexchange.com/2.3/search/advanced",
                 {"order": "desc", "sort": "relevance", "q": q, "site": "stackoverflow",
                  "pagesize": n}).json().get("items", [])
    return [{"titel": _kurz(i.get("title", ""), 200), "url": i.get("link", ""),
             "info": f"Score {i.get('score', 0)} · {i.get('answer_count', 0)} Antworten"
                     + (" · ✓ gelöst" if i.get("is_answered") else "")
                     + f" · {', '.join((i.get('tags') or [])[:4])}",
             "text": ""} for i in items[:n]]


def _openalex(q: str, n: int) -> list[dict]:
    items = _get("https://api.openalex.org/works", {"search": q, "per-page": n}).json().get("results", [])
    raus = []
    for i in items[:n]:
        autoren = [(a.get("author") or {}).get("display_name", "") for a in i.get("authorships") or []]
        ort = (i.get("primary_location") or {}).get("landing_page_url") or i.get("doi") or i.get("id", "")
        raus.append({"titel": _kurz(i.get("display_name") or "", 200), "url": ort,
                     "info": f"{i.get('publication_year', '')} · {i.get('cited_by_count', 0)}× zitiert"
                             + (f" · {', '.join(autoren[:3])}" if autoren else ""),
                     "text": ""})
    return raus


_SUCHER = {"arxiv": _arxiv, "pubmed": _pubmed, "github": _github,
           "stackoverflow": _stackoverflow, "openalex": _openalex}


def quelle_von(name: str) -> str | None:
    n = re.sub(r"[^a-z]", "", (name or "").lower())
    n = {"stack": "stackoverflow", "so": "stackoverflow", "stackexchange": "stackoverflow"}.get(n, n)
    return n if n in QUELLEN else None


def treffer(quelle: str, suche: str, anzahl: int = 5) -> list[dict] | str:
    """Treffer als Liste aus {titel, url, info, text, tier} – oder eine Fehlermeldung."""
    q = quelle_von(quelle)
    if q is None:
        return f"Unbekannte Fachquelle „{quelle}“. Möglich: {', '.join(QUELLEN)}."
    suche = " ".join((suche or "").split())
    if not suche:
        return "Bitte gib einen Suchbegriff an."
    try:
        n = max(1, min(int(float(anzahl or 5)), 10))
    except (TypeError, ValueError):
        n = 5
    schluessel = (q, suche.lower(), n)
    if (alt := _CACHE.get(schluessel)) and time.time() - alt[0] <= _CACHE_S:
        return alt[1]
    try:
        raus = _SUCHER[q](suche, n)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 429):
            return f"❌ {QUELLEN[q][0]}: Anfrage-Limit erreicht – später erneut versuchen."
        return f"Fehler bei {QUELLEN[q][0]}: HTTP {e.response.status_code}"
    except Exception as e:
        return f"Fehler bei {QUELLEN[q][0]}: {e}"
    for t in raus:
        t["url"] = re.sub(r"^http://", "https://", t["url"].strip())
        e = webfetch._match(t["url"]) if t["url"].startswith("https://") else None
        t["tier"] = e.get("tier", QUELLEN[q][1]) if e else (
            webfetch.FREMD_TIER if webfetch.fremdseiten_an() and t["url"].startswith("https://") else None)
    webfetch.bekannt_machen(t["url"] for t in raus)
    _CACHE[schluessel] = (time.time(), raus)
    return raus


def suchen(quelle: str, suche: str, anzahl: int = 5) -> str:
    """Werkzeug `fach_suche`: Treffer als Fremddaten mit Lese-Ampel."""
    raus = treffer(quelle, suche, anzahl)
    if isinstance(raus, str):
        return raus
    q = quelle_von(quelle)
    name, tier = QUELLEN[q]
    if not raus:
        return f"Keine Treffer in {name} zu „{suche}“."
    bloecke = []
    for i, t in enumerate(raus, 1):
        ampel = ("✓ lesbar" if t["tier"] not in (None, webfetch.FREMD_TIER)
                 else "◐ lesbar über Ollama" if t["tier"] == webfetch.FREMD_TIER else "✗ nicht lesbar")
        b = f"{i}. {t['titel']}\n   {t['url']}  ({ampel})\n   {t['info']}"
        if t["text"]:
            b += f"\n   {t['text']}"
        bloecke.append(b)
    return webfetch._untrusted(f"{name} – Suche „{suche}“", webfetch._cut("\n\n".join(bloecke)), tier)
