@echo off
REM ============================================================================
REM Setup demo databáze pro Blondi.
REM
REM Vytvoří prázdnou DB blondi_demo na stejném serveru jako produkce.
REM Vyžaduje psql v PATH (případně uprav cestu k psql.exe).
REM Heslo se zeptá interaktivně (PGPASSWORD env nebo .pgpass jinak).
REM
REM Po vytvoření spusť launch_demo.bat — Alembic migrace a demo seed
REM proběhnou automaticky při prvním startu.
REM ============================================================================

setlocal

set "PGHOST=kozohorsky.com"
set "PGPORT=6767"
set "PGUSER=spot_operator"
set "DEMO_DB=blondi_demo"

echo.
echo Vytvarim prazdnou demo databazi %DEMO_DB%@%PGHOST%:%PGPORT% jako %PGUSER%...
echo Pokud DB jiz existuje, dostanes chybu — to je v poradku, muzes pokracovat.
echo.

psql -h %PGHOST% -p %PGPORT% -U %PGUSER% -c "CREATE DATABASE %DEMO_DB% OWNER %PGUSER%;"

if errorlevel 1 (
    echo.
    echo Pokud chyba "database already exists" — vse OK, pokracuj launch_demo.bat.
    echo Jine chyby — zkontroluj heslo, dostupnost serveru a opravneni uzivatele.
)

echo.
echo Pokud probehlo bez chyby, demo DB je pripravena.
echo Spust launch_demo.bat pro spusteni aplikace v demo rezimu.
echo.
pause
endlocal
