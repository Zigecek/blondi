@echo off
REM Smaze vystupy PyInstalleru (dist\, intermediate build\_pyinstaller\).
REM Ponecha build\specs a build\runtime_hooks.

setlocal enableextensions

if exist dist (
  echo [INFO] Mazu dist\...
  rmdir /s /q dist
)

if exist build\_pyinstaller (
  echo [INFO] Mazu build\_pyinstaller\...
  rmdir /s /q build\_pyinstaller
)

echo [OK] Hotovo.
