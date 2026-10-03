"""
zertifikat.py - Legt ein selbst erstelltes Code-Signatur-Zertifikat "CN=NemiCLI" an.

Aufruf:   python zertifikat.py

Einmalig. Das Zertifikat (mit privatem Schlüssel) liegt danach im
Zertifikatsspeicher des angemeldeten Nutzers; build_exe.py signiert damit.
Damit Windows die Signatur auf DIESEM Rechner als vertrauenswürdig anzeigt,
kommt der öffentliche Teil zusätzlich in "Vertrauenswürdige Stammzertifizierungs-
stellen" und "Vertrauenswürdige Herausgeber" des Nutzers – Windows fragt dabei
einmal nach. Auf anderen Rechnern gilt es nicht; dafür braucht es ein
Zertifikat einer anerkannten Stelle.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ZERTIFIKAT = "CN=NemiCLI"
JAHRE = 5


def _ps(skript: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-Command", skript],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def vorhanden() -> str:
    r = _ps(r"Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | "
            f"Where-Object {{ $_.Subject -eq '{ZERTIFIKAT}' -and $_.NotAfter -gt (Get-Date) }} | "
            "ForEach-Object { \"$($_.Thumbprint)  gültig bis $($_.NotAfter.ToString('dd.MM.yyyy'))\" }")
    return (r.stdout or "").strip()


def anlegen() -> int:
    cer = Path(tempfile.gettempdir()) / "NemiCLI-Signatur.cer"
    skript = "\n".join([
        "$ErrorActionPreference='Stop'",
        f"$z = New-SelfSignedCertificate -Type CodeSigningCert -Subject '{ZERTIFIKAT}' "
        r"-CertStoreLocation Cert:\CurrentUser\My -KeyAlgorithm RSA -KeyLength 3072 "
        f"-HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears({JAHRE})",
        f"Export-Certificate -Cert $z -FilePath '{cer}' | Out-Null",
        r"Import-Certificate -FilePath '" + str(cer) + r"' -CertStoreLocation Cert:\CurrentUser\TrustedPublisher | Out-Null",
        r"Import-Certificate -FilePath '" + str(cer) + r"' -CertStoreLocation Cert:\CurrentUser\Root | Out-Null",
        f"Remove-Item '{cer}'",
        '"$($z.Thumbprint)"',
    ])
    r = _ps(skript)
    if r.returncode != 0:
        print("Fehlgeschlagen:\n" + (r.stderr or r.stdout).strip())
        return 1
    print(f"Zertifikat angelegt: {ZERTIFIKAT}  (Fingerabdruck {r.stdout.strip()})")
    print("Jetzt 'python build_exe.py' – die exe-Dateien werden damit signiert.")
    return 0


def main() -> int:
    if not sys.platform.startswith("win"):
        print("Nur unter Windows.")
        return 1
    schon = vorhanden()
    if schon:
        print(f"Zertifikat {ZERTIFIKAT} ist schon da: {schon}")
        return 0
    print(f"Lege ein Code-Signatur-Zertifikat {ZERTIFIKAT} an ({JAHRE} Jahre gültig).")
    print("Windows fragt gleich, ob dem Zertifikat vertraut werden soll – mit 'Ja' bestätigen.")
    return anlegen()


if __name__ == "__main__":
    raise SystemExit(main())
