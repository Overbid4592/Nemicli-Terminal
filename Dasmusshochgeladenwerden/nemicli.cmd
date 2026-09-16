@echo off
REM ==== NemiCLI - globaler Starter ====
REM Liegt im Projekt-Ordner. Sobald dieser Ordner im PATH steht, kann man von
REM ueberall einfach "nemicli" tippen. %~dp0 = der Ordner dieser Datei.

chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM venv-Python bevorzugen, sonst globales Python
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM WICHTIG: KEIN pushd/cd hierhin! Hier stand frueher "pushd %~dp0", damit
REM main.py gefunden wird - dadurch war der aktuelle Ordner aber IMMER der
REM NemiCLI-Ordner, egal wo man "nemicli" getippt hat. Dann nahm /workspace den
REM falschen Ordner, die Ordner-Erkennung analysierte NemiCLI statt des
REM Projekts, und der Auto-Modus galt fuer den falschen Ort.
REM main.py mit vollem Pfad aufrufen loest das: NemiCLI wird gefunden UND der
REM Ordner, aus dem du startest, bleibt dein Ordner.
"%PY%" "%~dp0main.py" %*
