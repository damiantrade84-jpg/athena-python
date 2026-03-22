@echo off
echo Starting Athena with Python 3.13...
echo.
echo Using Python 3.13.12 for full compatibility...
echo.

REM Try multiple ways to find Python 3.13
py -3.13 --version >nul 2>&1
if %errorlevel% equ 0 (
    echo Found Python 3.13 via py launcher
    py -3.13 athena.py
    goto :end
)

REM Fallback: direct path
"C:\Users\damia\AppData\Local\Programs\Python\Python313\python.exe" athena.py 2>nul
if %errorlevel% equ 0 (
    echo Found Python 3.13 via direct path
    goto :end
)

echo ERROR: Python 3.13 not found!
echo Please install Python 3.13 or run: py -3.13 athena.py
pause
exit /b 1

:end
pause
