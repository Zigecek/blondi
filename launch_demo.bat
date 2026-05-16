@echo off
REM ============================================================================
REM Blondi DEMO režim — pro screenshoty bez fyzického Spota.
REM
REM Před prvním spuštěním:
REM   1) Vytvoř prázdnou demo databázi v PostgreSQL:
REM      psql -h kozohorsky.com -p 6767 -U spot_operator -c "CREATE DATABASE blondi_demo OWNER spot_operator;"
REM      (nebo pomocí pgAdmin / setup_demo_db.bat)
REM   2) Zkontroluj heslo v BLONDI_DEMO_DATABASE_URL níže (musí odpovídat .env).
REM
REM Demo režim:
REM   - Mock Spot SDK (žádné reálné připojení)
REM   - Statické left.png/right.png místo live view
REM   - Seed: 5 map, 10 runů, 50 fotek, 30 SPZ
REM   - F12 = uložit screenshot do screens/ s automatickým názvem
REM ============================================================================

setlocal

set "BLONDI_DEMO=1"

REM Demo DB — POVINNÉ. Upravit heslo a host podle vlastní instance.
set "BLONDI_DEMO_DATABASE_URL=postgresql+psycopg://spot_operator:dcef052d8fce53cb0b1f38fb399bf5e247d17c5a54c813f7d2acdd8517405791@kozohorsky.com:6767/blondi_demo"

REM Pro forced reseed při startu (smaže a znovu naplní demo data) nastav 1:
set "BLONDI_DEMO_RESEED=1"

cd /d "%~dp0"
".venv\Scripts\python.exe" main.py
echo.
echo Aplikace skoncila s kodem %ERRORLEVEL%.
pause
endlocal
