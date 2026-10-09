@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 app.py --takeover
) else (
  python app.py --takeover
)
if errorlevel 1 pause
