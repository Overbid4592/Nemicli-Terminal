"""
msi.py - Baut aus dist/NemiCLI einen Windows Installer (MSI).

Installiert pro Benutzer ohne Adminrechte nach %LOCALAPPDATA%\\Programs\\NemiCLI,
legt Startmenü-Einträge an und trägt NemiCLI unter „Apps“ zum Deinstallieren ein.
Eine neuere MSI ersetzt eine ältere (gleicher UpgradeCode). Config, Schlüssel,
venv und Daten legt erst NemiCLI selbst an – die entfernt das Deinstallieren nicht.

Nutzt msilib aus der Standardbibliothek (bis Python 3.12).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import msilib
    from msilib import schema, sequence

UPGRADE_CODE = "{3F6B9C2E-8D41-4A7F-B5E0-6C2D9A1E7F43}"
HERSTELLER = "NemiCLI"
KOMPONENTE_64BIT = 256
OHNE_ADMIN = 8                      # Summary-Info Word Count: keine Rechteerhöhung nötig


def _msi_version(build: str) -> str:
    """'5.1.211' bleibt, alles andere wird auf Zahlen gekürzt."""
    teile = [t for t in build.split(".") if t.isdigit()][:3]
    return ".".join(teile + ["0"] * (3 - len(teile)))


def bauen(quelle: Path, ziel: Path, build: str, icon: Path) -> Path:
    version = _msi_version(build)
    datei = ziel / f"NemiCLI-{version}.msi"
    datei.unlink(missing_ok=True)

    db = msilib.init_database(str(datei), schema, "NemiCLI", msilib.gen_uuid(), version, HERSTELLER)
    msilib.add_tables(db, sequence)
    si = db.GetSummaryInformation(20)
    si.SetProperty(msilib.PID_WORDCOUNT, 2 | OHNE_ADMIN)
    si.SetProperty(msilib.PID_APPNAME, "NemiCLI")
    si.Persist()

    msilib.add_data(db, "Property", [
        ("UpgradeCode", UPGRADE_CODE),
        ("ALLUSERS", "2"),
        ("MSIINSTALLPERUSER", "1"),
        ("ARPPRODUCTICON", "nemicli.ico"),
        ("ARPNOMODIFY", "1"),
        ("ARPNOREPAIR", "1"),
        ("SecureCustomProperties", "ALTEVERSION"),
    ])
    msilib.add_data(db, "Icon", [("nemicli.ico", msilib.Binary(str(icon)))])
    # bis einschließlich gleicher Version: ein Neubau mit derselben Nummer ersetzt den alten
    msilib.add_data(db, "Upgrade", [(UPGRADE_CODE, None, version, None, 256 | 512, None, "ALTEVERSION")])

    msilib.add_data(db, "Directory", [
        ("TARGETDIR", None, "SourceDir"),
        ("LocalAppDataFolder", "TARGETDIR", "."),
        ("PROGRAMME", "LocalAppDataFolder", "Programs"),
        ("ProgramMenuFolder", "TARGETDIR", "."),
        ("STARTMENUE", "ProgramMenuFolder", "NemiCLI"),
    ])
    feature = msilib.Feature(db, "NemiCLI", "NemiCLI", "Programm", 1, directory="INSTALLDIR")

    cab = msilib.CAB("nemicli")
    wurzel = msilib.Directory(db, cab, None, str(quelle), "INSTALLDIR", "NemiCLI|NemiCLI",
                              componentflags=KOMPONENTE_64BIT)
    # Directory legt INSTALLDIR unterhalb von TARGETDIR an – hier unter PROGRAMME
    db.OpenView("UPDATE `Directory` SET `Directory_Parent`='PROGRAMME' "
                "WHERE `Directory`='INSTALLDIR'").Execute(None)
    offen = [wurzel]
    while offen:
        ordner = offen.pop()
        eintraege = sorted(os.scandir(ordner.absolute), key=lambda e: e.name.lower())
        dateien = [e for e in eintraege if e.is_file()]
        if dateien:
            ordner.start_component(ordner.logical, feature)
            for e in dateien:
                ordner.add_file(e.name)
        for e in eintraege:
            if e.is_dir():
                kurz = ordner.make_short(e.name)
                offen.append(msilib.Directory(db, cab, ordner, e.name, e.name,
                                              f"{kurz}|{e.name}", componentflags=KOMPONENTE_64BIT))

    # Startmenü: eigene Komponente mit HKCU-Schlüssel als KeyPath (pro Benutzer)
    msilib.add_data(db, "Component", [
        ("Startmenue", msilib.gen_uuid(), "STARTMENUE", KOMPONENTE_64BIT | 4, None, "reg_start"),
    ])
    msilib.add_data(db, "FeatureComponents", [("NemiCLI", "Startmenue")])
    msilib.add_data(db, "Registry", [
        ("reg_start", 1, r"Software\NemiCLI", "installiert", version, "Startmenue"),
    ])
    msilib.add_data(db, "Shortcut", [
        ("sc_nemicli", "STARTMENUE", "NemiCLI|NemiCLI", "Startmenue", "[INSTALLDIR]NemiCLI.exe",
         None, "NemiCLI starten", None, "nemicli.ico", 0, None, "INSTALLDIR"),
        ("sc_deinst", "STARTMENUE", "NEMIDE~1|NemiCLI deinstallieren", "Startmenue",
         "[SystemFolder]msiexec.exe", "/x [ProductCode]", "NemiCLI deinstallieren",
         None, None, None, None, None),
    ])
    msilib.add_data(db, "CreateFolder", [("STARTMENUE", "Startmenue")])
    msilib.add_data(db, "RemoveFile", [("rm_start", "Startmenue", None, "STARTMENUE", 2)])
    # Alte Fassung früh entfernen, damit keine Dateien beider Fassungen gemischt bleiben
    db.OpenView("DELETE FROM `InstallExecuteSequence` WHERE `Action`='RemoveExistingProducts'"
                ).Execute(None)
    msilib.add_data(db, "InstallExecuteSequence", [("RemoveExistingProducts", None, 1401)])
    cab.commit(db)
    db.Commit()
    return datei
