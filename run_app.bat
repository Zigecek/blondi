@echo off
REM Spusti blondi aplikaci ve venv.
if not exist .venv (
  echo [CHYBA] Venv neexistuje. Spust nejdriv setup_venv.bat.
  pause
  exit /b 1
)
call .venv\Scripts\activate
python main.py %*
