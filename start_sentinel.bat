@echo off
title Sentinel Pro v4.0
cd /d "%~dp0"

echo Stopping any process on port 5000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM Prevent Windows display sleep from throttling/pausing the process
powershell -NoProfile -Command "Add-Type -Namespace Util -Name Power -MemberDefinition '[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint flags);'; [Util.Power]::SetThreadExecutionState(0x80000003)" >nul 2>&1

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo.
echo ============================================================
echo  Sentinel Pro v4.0
echo  TIP: To run without a visible CMD window that can throttle,
echo       double-click run_background.vbs instead.
echo ============================================================
echo.
echo Starting Sentinel Pro (Python 3.13)...
echo Press Ctrl-C to stop.
echo.

REM HIGH priority prevents Windows from throttling background console apps
start /HIGH /B /WAIT "" py -3.13 athena.py

echo.
echo Sentinel Pro stopped.
timeout /t 3 /nobreak >nul
