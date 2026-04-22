@echo off
REM Vytvori virtualni prostredi a nainstaluje zavislosti projektu spot_operator.
REM Vyzaduje Python 3.10 nainstalovany (py -3.10 --version).

where py >nul 2>&1
if errorlevel 1 (
  echo [CHYBA] Py launcher ^(py.exe^) neni dostupny. Nainstaluj Python 3.10 x64 z python.org.
  pause
  exit /b 1
)

py -3.10 --version >nul 2>&1
if errorlevel 1 (
  echo [CHYBA] Python 3.10 neni dostupny. Nainstaluj ho vedle Python 3.12.
  echo Z oficialniho installeru https://www.python.org/downloads/release/python-3100/
  pause
  exit /b 1
)

if exist .venv (
  echo [INFO] Slozka .venv jiz existuje. Pokud chces cisty setup, smaz ji a spust znovu.
) else (
  echo [INFO] Vytvarim .venv s Python 3.10...
  py -3.10 -m venv .venv
  if errorlevel 1 (
    echo [CHYBA] Vytvoreni venv selhalo.
    pause
    exit /b 1
  )
)

call .venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel

REM requirements.txt obsahuje VSE vcetne nomeroff_net a pyinstaller —
REM sjednocený source of truth pro reprodukovatelnost.
pip install -r requirements.txt
if errorlevel 1 (
  echo [CHYBA] Instalace zavislosti selhala.
  pause
  exit /b 1
)

echo.
echo [OK] Venv pripraven (vcetne bundler deps).
echo.
echo Dalsi kroky:
echo   1) Zkopiruj .env.example na .env a doplnte prihlasovaci udaje k Postgres.
echo   2) Spust aplikaci prikazem run_app.bat nebo launch.bat.
echo   3) Nebo spust bundler prikazem build\build_all.bat pro tri .exe.
echo.
pause
