"""aktualisieren.py – Programm-Updates über winget, auf Anfrage.

NemiCLI erhöht nie selbst die Rechte: winget läuft im Benutzerkonto. Braucht der Installer eines Updates
Adminrechte, fragt Windows selbst (UAC) – der Nutzer bestätigt oder bricht ab (Esc). Nachsehen läuft ohne
Rückfrage, Installieren nur mit Freigabe des Nutzers; es werden nur schon installierte Programme aktualisiert
(`winget upgrade`), nie neue installiert.
"""

from __future__ import annotations

import re
import subprocess

LISTE_TIMEOUT_S = 180
UPDATE_TIMEOUT_S = 1800              # je Paket
MAX_PAKETE = 15
_ID = re.compile(r"^[A-Za-z0-9][\w.+-]{1,120}$")
_GRUNDFLAGS = ["--accept-source-agreements", "--disable-interactivity"]
ABGEBROCHEN = {1223, -2147023673, 0x800704C7}            # ERROR_CANCELLED, auch als HRESULT
KEIN_UPDATE = {-1978335189, 0x8A15002B}                  # APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE
ADMIN_HINWEIS = ("Braucht ein Update Adminrechte, fragt Windows selbst nach (UAC) – bestätigen oder bei Bedenken "
                 "abbrechen (Esc). NemiCLI selbst erhöht nie die Rechte.")


class WingetFehlt(RuntimeError):
    pass


def _winget(argumente: list[str], timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(["winget", *argumente, *_GRUNDFLAGS], capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as exc:
        raise WingetFehlt("winget ist nicht installiert (App-Installer aus dem Microsoft Store).") from exc
    except subprocess.TimeoutExpired:
        return 1, f"winget hat nach {int(timeout)} s nicht geantwortet und wurde beendet – später noch einmal versuchen."
    roh = (r.stdout or b"") + (b"\n" + r.stderr if r.stderr else b"")
    return r.returncode, saeubern(roh.decode("utf-8", errors="replace"))


def saeubern(text: str) -> str:
    """Fortschritts-Kreisel und -Balken raus: von jeder Zeile zählt nur, was nach dem letzten \\r steht."""
    zeilen = []
    for z in text.replace("\r\n", "\n").split("\n"):
        z = z.rsplit("\r", 1)[-1].rstrip()
        if z.strip() in ("-", "\\", "|", "/") or set(z.strip()) <= set("█▒░ ") and z.strip():
            continue
        zeilen.append(z)
    return "\n".join(zeilen).strip()


def tabelle(text: str) -> list[dict]:
    """Die erste winget-Tabelle (Name · Id · Version · Verfügbar · Quelle) – Spalten nach den Anfängen der
    Überschrift, also unabhängig von der Sprache."""
    zeilen = text.splitlines()
    for i in range(1, len(zeilen)):
        if not re.fullmatch(r"-{10,}", zeilen[i].strip()):
            continue
        anfaenge = [m.start() for m in re.finditer(r"\S+", zeilen[i - 1])]
        if len(anfaenge) < 4:
            return []
        raus = []
        for z in zeilen[i + 1:]:
            if not z.strip():
                break                                       # Ende der Tabelle (danach kommen Hinweise)
            teile = [z[a:b].strip() for a, b in zip(anfaenge, anfaenge[1:] + [len(z)])]
            if len(teile) < 4 or not _ID.match(teile[1] or ""):
                continue                                    # Zusammenfassung o. Ä. statt Paketzeile
            raus.append({"name": teile[0], "id": teile[1], "version": teile[2], "verfuegbar": teile[3],
                         "quelle": teile[4] if len(teile) > 4 else ""})
        return raus
    return []


def liste() -> tuple[list[dict], str]:
    """Verfügbare Updates: (Einträge, winget-Ausgabe)."""
    _, text = _winget(["upgrade", "--include-unknown"], LISTE_TIMEOUT_S)
    return tabelle(text), text


def gueltige_ids(ids) -> list[str]:
    if isinstance(ids, str):
        ids = re.split(r"[\s,;]+", ids)
    raus = []
    for i in ids or []:
        i = str(i).strip()
        if not _ID.match(i):
            raise ValueError(f"„{i}“ ist keine winget-Id (z. B. Mozilla.Firefox).")
        if i not in raus:
            raus.append(i)
    if not raus:
        raise ValueError("Welche Updates? ids=[\"Mozilla.Firefox\", …] – die Ids stehen in updates_suchen.")
    if len(raus) > MAX_PAKETE:
        raise ValueError(f"Höchstens {MAX_PAKETE} auf einmal.")
    return raus


def installieren(paket_id: str) -> tuple[str, str]:
    """Ein Paket aktualisieren: (Zustand ok|kein_update|abgebrochen|fehler|zeit, letzte Zeilen der Ausgabe)."""
    try:
        code, text = _winget(["upgrade", "--id", paket_id, "--exact", "--silent", "--accept-package-agreements"],
                             UPDATE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "zeit", f"nach {UPDATE_TIMEOUT_S // 60} Minuten noch nicht fertig – winget läuft weiter im Hintergrund"
    rest = "\n".join(text.splitlines()[-4:])
    if code == 0:
        return "ok", rest
    if code in KEIN_UPDATE:
        return "kein_update", rest
    if code in ABGEBROCHEN or "1223" in text or "abgebrochen" in text.lower():
        return "abgebrochen", rest
    return "fehler", f"Code {code} (0x{code & 0xFFFFFFFF:08X}) · {rest}"
