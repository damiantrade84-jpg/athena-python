@echo off
title Stop Sentinel Pro
cd /d "%~dp0"

echo Stopping Sentinel Pro v4.0...

REM 1. Kill any process on port 5000 (Flask)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /f /pid %%a >nul 2>&1
)

REM 2. Kill any Python process running athena.py (more specific than killing all python.exe)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*athena.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo.
echo ============================================================
echo  Sentinel Pro has been stopped.
echo ============================================================
echo.
timeout /t 2 /nobreak >nul
