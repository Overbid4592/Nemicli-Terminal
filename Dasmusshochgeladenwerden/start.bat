@echo off
REM ==== NemiCLI Starter ====
REM Doppelklick startet NemiCLI (Slash-Befehle).
REM
REM Seit 15.09.2026 prueft der Starter ZUERST, ob die Installation vollstaendig
REM ist, und baut Fehlendes selbst nach. Anlass: beim Umzug des Programm-Ordners
REM (Desktop -> AppData) blieb das venv auf der Strecke - NemiCLI fiel dann
REM stumm auf das System-Python zurueck, wo die Pakete fehlen, und startete
REM einfach nicht mehr. Lieber einmal zwei Minuten warten als ratlos dastehen.
REM
REM Geprueft wird in dieser Reihenfolge:
REM   1. Ist ueberhaupt ein Python da?      (venv, sonst py -3, sonst python)
REM   2. Gibt es das venv?                  (sonst wird es angelegt)
REM   3. Sind die Pakete drin?              (sonst pip install -r requirements.txt)
REM Erst danach startet main.py.

chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

set "VENV_PY=%~dp0venv\Scripts\python.exe"

REM ---------------------------------------------------------------- 1. Python
REM Der Interpreter, mit dem das venv notfalls GEBAUT wird. Das venv selbst
REM kann sich nicht erschaffen, wenn es noch nicht existiert.
set "BOOT_PY="
if exist "%VENV_PY%" goto :venv_da

py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "BOOT_PY=py -3"
if defined BOOT_PY goto :boot_ok

python -c "import sys" >nul 2>&1
if not errorlevel 1 set "BOOT_PY=python"
if defined BOOT_PY goto :boot_ok

echo.
echo   [X] Auf diesem PC ist kein Python zu finden.
echo.
echo       NemiCLI braucht Python 3.11 oder neuer:
echo         https://www.python.org/downloads/
echo       Beim Installieren "Add python.exe to PATH" ankreuzen.
echo.
goto :ende

:boot_ok
REM -------------------------------------------------------------------- 2. venv
echo.
echo   [1/3] Lege die Arbeits-Umgebung an (venv) ... das dauert einen Moment.
%BOOT_PY% -m venv "%~dp0venv"
if not exist "%VENV_PY%" (
    echo.
    echo   [X] Das venv liess sich nicht anlegen.
    echo       Versuch es von Hand:   %BOOT_PY% -m venv venv
    echo.
    goto :ende
)
echo   [1/3] Arbeits-Umgebung steht.

:venv_da
REM ------------------------------------------------------------------ 3. Pakete
REM Stichprobe statt kompletter Pruefliste: fehlt eines davon, ist die
REM Installation ohnehin unvollstaendig.
"%VENV_PY%" -c "import httpx, rich, prompt_toolkit, dotenv, textual" >nul 2>&1
if not errorlevel 1 goto :starten

echo.
echo   [2/3] Es fehlen Pakete - ich installiere sie jetzt.
echo         (Einmalig, kann ein paar Minuten dauern.)
echo.
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo   [X] Die Installation ist schiefgegangen - siehe Meldungen oben.
    echo       Haeufigste Ursache: keine Internet-Verbindung.
    echo.
    goto :ende
)

"%VENV_PY%" -c "import httpx, rich, prompt_toolkit, dotenv, textual" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [X] Es fehlen immer noch Pakete. Von Hand:
    echo       venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    goto :ende
)
echo   [2/3] Pakete sind vollstaendig.

:starten
echo.
echo   [3/3] Starte NemiCLI...  (Tippe "/" fuer das Menue, /exit zum Beenden)
echo.

"%VENV_PY%" "%~dp0main.py" %*

:ende
echo.
echo   Fenster bleibt offen. Druecke eine Taste zum Schliessen.
pause >nul
