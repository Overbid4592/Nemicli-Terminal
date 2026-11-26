"""sigma_regeln.py - mitgelieferte Sigma-Regeln der Wache (eigene, vorsichtige Regeln im Sigma-Format).

Nur Muster, die im Alltag praktisch nie harmlos vorkommen: Windows-Werkzeuge, die Dateien aus dem Netz holen
oder fremden Code ausführen, und das Löschen von Schattenkopien. Weitere Regeln (z. B. aus SigmaHQ, Lizenz
Detection Rule License 1.1) legt der Nutzer als .yml in den Wache-Ordner `sigma/`.
"""

MITGELIEFERT = [
    r"""
title: Certutil lädt eine Datei aus dem Netz
id: nemicli-sigma-001
description: certutil mit -urlcache und einer Adresse – ein bekannter Weg, Schadcode nachzuladen.
tags: [attack.t1105]
level: high
logsource: {category: process_creation, product: windows}
detection:
  werkzeug:
    - Image|endswith: '\certutil.exe'
    - OriginalFileName: 'certutil.exe'
  befehl:
    CommandLine|contains|windash: '-urlcache'
  adresse:
    CommandLine|contains: ['http://', 'https://']
  condition: werkzeug and befehl and adresse
falsepositives: [Administratoren, die certutil bewusst zum Herunterladen nutzen]
""",
    r"""
title: Mshta führt Code aus einer Adresse oder einem Skript-Text aus
id: nemicli-sigma-002
description: mshta mit http(s)-, javascript- oder vbscript-Ziel startet fremden Code am Browser vorbei.
tags: [attack.t1218.005]
level: high
logsource: {category: process_creation, product: windows}
detection:
  werkzeug:
    - Image|endswith: '\mshta.exe'
    - OriginalFileName: 'mshta.exe'
  ziel:
    CommandLine|contains: ['http://', 'https://', 'javascript:', 'vbscript:']
  condition: werkzeug and ziel
falsepositives: [Alte Firmen-Software, die HTA-Seiten aus dem Intranet lädt]
""",
    r"""
title: Regsvr32 lädt ein Skriptobjekt (scrobj.dll)
id: nemicli-sigma-003
description: regsvr32 mit scrobj.dll und /i führt Skript-Code aus einer Datei oder Adresse aus.
tags: [attack.t1218.010]
level: high
logsource: {category: process_creation, product: windows}
detection:
  werkzeug:
    - Image|endswith: '\regsvr32.exe'
    - OriginalFileName: 'regsvr32.exe'
  objekt:
    CommandLine|contains: 'scrobj.dll'
  schalter:
    CommandLine|contains|windash: '-i:'
  condition: werkzeug and objekt and schalter
falsepositives: [Unbekannt]
""",
    r"""
title: Rundll32 führt JavaScript aus
id: nemicli-sigma-004
description: rundll32 mit „javascript:“ in der Kommandozeile – Code ohne eigene Datei.
tags: [attack.t1218.011]
level: high
logsource: {category: process_creation, product: windows}
detection:
  werkzeug:
    - Image|endswith: '\rundll32.exe'
    - OriginalFileName: 'rundll32.exe'
  skript:
    CommandLine|contains: 'javascript:'
  condition: werkzeug and skript
falsepositives: [Unbekannt]
""",
    r"""
title: Bitsadmin überträgt eine Datei
id: nemicli-sigma-005
description: bitsadmin /transfer lädt Dateien im Hintergrund – Windows Update nutzt dafür nicht bitsadmin.exe.
tags: [attack.t1197]
level: medium
logsource: {category: process_creation, product: windows}
detection:
  werkzeug:
    - Image|endswith: '\bitsadmin.exe'
    - OriginalFileName: 'bitsadmin.exe'
  befehl:
    CommandLine|contains|windash: '-transfer'
  condition: werkzeug and befehl
falsepositives: [Installations-Skripte, die bewusst bitsadmin verwenden]
""",
    r"""
title: Schattenkopien werden gelöscht
id: nemicli-sigma-006
description: Löschen der Windows-Schattenkopien (vssadmin, wmic) – typischer Schritt vor einer Erpressung.
tags: [attack.t1490]
level: high
logsource: {category: process_creation, product: windows}
detection:
  vssadmin:
    Image|endswith: '\vssadmin.exe'
    CommandLine|contains|all: ['delete', 'shadows']
  wmic:
    Image|endswith: '\wmic.exe'
    CommandLine|contains|all: ['shadowcopy', 'delete']
  condition: 1 of them
falsepositives: [Bewusstes Aufräumen von Speicherplatz durch den Nutzer]
""",
]
