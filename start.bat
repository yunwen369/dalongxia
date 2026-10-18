@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Keep this file ASCII-only: cmd.exe parses .bat with the OEM code page and
rem Chinese literals can break command parsing on Chinese Windows.
rem All Chinese user-facing messages are printed by backend\app\main.py.
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
    echo Please run in this directory:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

rem Force UTF-8 for Python console output so Chinese messages are readable.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

".venv\Scripts\python.exe" "backend\app\main.py"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Service exited with code %EXIT_CODE%. See logs\app.log
    pause
)

endlocal & exit /b %EXIT_CODE%
