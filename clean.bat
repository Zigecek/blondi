@echo off
REM Clean wrapper — smaze cache a build artefakty pro distribuci.
REM Dvojklik = dry-run (jen vypise co by se smazalo).
REM Parametry se predavaji primo do clean.py (napr. clean.bat -y).

REM Pouziva system Python (ne .venv, protoze ten se sam muze mazat).
where python >nul 2>nul
if errorlevel 1 (
    echo Python neni v PATH. Nainstaluj Python 3.10+ nebo spust clean.py rucne.
    pause
    exit /b 1
)

python "%~dp0clean.py" %*

if "%~1"=="" (
    echo.
    pause
)
