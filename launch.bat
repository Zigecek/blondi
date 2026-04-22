@echo off
REM Jednim klikem: setup (pokud treba) + spusteni aplikace.
if not exist .venv (
  echo [INFO] Virtualni prostredi neexistuje, spoustim setup...
  call setup_venv.bat
  if errorlevel 1 exit /b 1
)
call run_app.bat
