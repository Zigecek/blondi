@echo off
REM Postavi tri nezavisle .exe: autonomy, ocr, spot.
REM Spoustet z root projektu: build_all.bat [autonomy^|ocr^|spot]
REM
REM Detekce venv: pouzije aktivovany VIRTUAL_ENV, jinak zkusi .venv\ v root,
REM jinak autonomy\.venv\, jinak chyba.

setlocal enableextensions

if defined VIRTUAL_ENV goto :venv_ok
if exist .venv\Scripts\activate.bat goto :activate_root
if exist autonomy\.venv\Scripts\activate.bat goto :activate_autonomy
echo [CHYBA] Zadny venv nenalezen. Spust nejprve: setup_venv.bat
exit /b 1

:activate_root
echo [INFO] Aktivuji root .venv\...
call .venv\Scripts\activate.bat
goto :venv_ok

:activate_autonomy
echo [WARN] Root .venv neexistuje, zkousim autonomy\.venv\
echo        POZOR: build ocr.exe a spot.exe pravdepodobne selze.
echo        Doporuceni: ukonci venv a spust setup_venv.bat v root.
call autonomy\.venv\Scripts\activate.bat
goto :venv_ok

:venv_ok
echo [INFO] Venv aktivni: %VIRTUAL_ENV%
echo %VIRTUAL_ENV% | findstr /i "autonomy" >nul
if not errorlevel 1 echo [WARN] Aktivovan autonomy\.venv — build ocr/spot pravdepodobne selze.

where pyinstaller >nul 2>nul
if errorlevel 1 goto :install_pyinstaller
goto :args

:install_pyinstaller
echo [INFO] Instaluji pyinstaller...
python -m pip install "pyinstaller>=6.0"
if errorlevel 1 goto :err

:args
set WORKPATH=build\_pyinstaller
set DISTPATH=dist

set TARGET=%1
if "%TARGET%"=="" set TARGET=all

if "%TARGET%"=="all"      goto :build_autonomy
if "%TARGET%"=="autonomy" goto :build_autonomy
if "%TARGET%"=="ocr"      goto :build_ocr
if "%TARGET%"=="spot"     goto :build_spot
echo [CHYBA] Neznamy target: %TARGET%. Pouzij: all, autonomy, ocr, spot
exit /b 1

:build_autonomy
echo.
echo === autonomy.exe ===
pyinstaller --noconfirm --clean --workpath %WORKPATH% --distpath %DISTPATH% build\specs\autonomy.spec
if errorlevel 1 goto :err
if "%TARGET%"=="autonomy" goto :ok

:build_ocr
echo.
echo === ocr.exe ===
pyinstaller --noconfirm --clean --workpath %WORKPATH% --distpath %DISTPATH% build\specs\ocr.spec
if errorlevel 1 goto :err
if "%TARGET%"=="ocr" goto :ok

:build_spot
echo.
echo === spot.exe ===
pyinstaller --noconfirm --clean --workpath %WORKPATH% --distpath %DISTPATH% build\specs\spot.spec
if errorlevel 1 goto :err

:ok
echo.
echo === BUILD OK ===
if exist dist\autonomy\autonomy.exe echo   dist\autonomy\autonomy.exe
if exist dist\ocr\ocr.exe           echo   dist\ocr\ocr.exe
if exist dist\spot\spot.exe         echo   dist\spot\spot.exe
goto :eof

:err
echo.
echo === BUILD SELHAL ===
exit /b 1
