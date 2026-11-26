"""
lokalserver.py - HTTP-Server nur auf der Loopback-Adresse, mit exklusiv belegtem Port.

http.server setzt SO_REUSEADDR. Unter Windows darf ein zweiter Prozess damit denselben Port
zusätzlich belegen und Anfragen abfangen. Diese Klassen setzen es nicht und belegen den Port
unter Windows mit SO_EXCLUSIVEADDRUSE.
"""
from __future__ import annotations

import ipaddress
import socket
import sys
from http.server import HTTPServer, ThreadingHTTPServer


def _loopback(host) -> bool:
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


class _Exklusiv:
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if not _loopback(self.server_address[0]):
            raise OSError(f"Lokaler Server nur auf 127.0.0.1, nicht auf {self.server_address[0]!r}.")
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class EinzelServer(_Exklusiv, HTTPServer):
    """Eine Anfrage nach der anderen."""


class Server(_Exklusiv, ThreadingHTTPServer):
    """Je Anfrage ein Faden."""
    daemon_threads = True


class LeiserServer(Server):
    """Wie Server; abgebrochene Verbindungen (Tab geschlossen oder neu geladen) ohne Traceback."""

    def handle_error(self, request, client_address):
        art = sys.exc_info()[0]
        if art is not None and issubclass(art, (ConnectionError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)
