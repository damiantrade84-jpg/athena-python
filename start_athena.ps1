# PowerShell script to start Athena with Python 3.13
Write-Host "Starting Athena with Python 3.13..." -ForegroundColor Green
Write-Host ""

# Try Python 3.13 via py launcher
try {
    $version = & py -3.13 --version 2>$null
    if ($version) {
        Write-Host "Found Python 3.13 via py launcher: $version" -ForegroundColor Cyan
        & py -3.13 athena.py
        exit
    }
} catch {
    # Continue to fallback
}

# Fallback: direct Python 3.13 path
$python313 = "C:\Users\damia\AppData\Local\Programs\Python\Python313\python.exe"
if (Test-Path $python313) {
    Write-Host "Found Python 3.13 via direct path" -ForegroundColor Cyan
    & $python313 athena.py
    exit
}

Write-Host "ERROR: Python 3.13 not found!" -ForegroundColor Red
Write-Host "Please install Python 3.13 or run: py -3.13 athena.py" -ForegroundColor Yellow
Read-Host "Press Enter to exit"
