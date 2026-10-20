@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
 echo Potreban je Python 3.10 ili noviji sa python.org. Instaliraj i ponovo pokreni ovaj fajl.
 pause
 exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe goto fail
.venv\Scripts\python.exe -c "import lxml" >nul 2>nul
if errorlevel 1 .venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe updater.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Pokretanje nije uspelo. Posalji tekst greske iz ovog prozora.
pause
