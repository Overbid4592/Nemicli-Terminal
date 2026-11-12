"""Adressen lokaler Dienste: unverschlüsselt (http) nur zum eigenen Rechner, sonst https."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


def lokal(name: str) -> bool:
    """Zeigt der Rechnername auf diesen Rechner (localhost, 127.0.0.0/8, ::1)?"""
    name = (name or "").strip().strip("[]").rstrip(".").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def sicher(url: str) -> str:
    """Ohne Schema: http nur für den eigenen Rechner, sonst https. Ein ausdrückliches http:// zu einem
    anderen Rechner wird zu https://."""
    url = (url or "").strip()
    if not url:
        return url
    if "://" not in url:
        roh = urlsplit("//" + url)
        return ("http://" if lokal(roh.hostname or "") else "https://") + url
    teile = urlsplit(url)
    if teile.scheme.lower() == "http" and not lokal(teile.hostname or ""):
        return urlunsplit(("https",) + tuple(teile)[1:])
    return url
