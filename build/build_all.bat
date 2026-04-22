@echo off
REM Postavi tri nezavisle .exe: autonomy, ocr, spot.
REM Spoustet z root projektu: build\build_all.bat
REM Predpoklad: aktivovane venv s nainstalovanymi requirements.txt + autonomy\requirements.txt.

setlocal enableextensions

if not exist .venv\Scripts\activate.bat (
  echo [CHYBA] .venv neexistuje. Spust nejprve setup_venv.bat v root.
  exit /b 1
)
call .venv\Scripts\activate.bat

where pyinstaller >nul 2>nul
if errorlevel 1 (
  echo [INFO] Instaluji pyinstaller...
  python -m pip install "pyinstaller>=6.0"
  if errorlevel 1 goto :err
)

set WORKPATH=build\_pyinstaller
set DISTPATH=dist

echo.
echo === [1/3] autonomy.exe ===
pyinstaller --noconfirm --clean --workpath %WORKPATH% --distpath %DISTPATH% build\specs\autonomy.spec
if errorlevel 1 goto :err

echo.
echo === [2/3] ocr.exe ===
pyinstaller --noconfirm --clean --workpath %WORKPATH% --distpath %DISTPATH% build\specs\ocr.spec
if errorlevel 1 goto :err

echo.
echo === [3/3] spot.exe ===
pyinstaller --noconfirm --clean --workpath %WORKPATH% --distpath %DISTPATH% build\specs\spot.spec
if errorlevel 1 goto :err

echo.
echo === BUILD OK ===
echo   dist\autonomy\autonomy.exe
echo   dist\ocr\ocr.exe
echo   dist\spot\spot.exe
goto :eof

:err
echo.
echo === BUILD SELHAL ===
exit /b 1
