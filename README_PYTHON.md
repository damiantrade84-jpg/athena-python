# Python Version Instructions

## Important: Use Python 3.13

This application requires Python 3.13.12 for full compatibility with all libraries.

## Quick Start Options

### Option 1: Use Batch Files
- `start_athena.bat` - Normal startup
- `start_athena_debug.bat` - Debug mode

### Option 2: Command Line
```powershell
py -3.13 athena.py
```

### Option 3: Set Python 3.13 as Default
```powershell
# Check current default
py --version

# Set Python 3.13 as default (if needed)
py -3.13 -m pip install --upgrade pip
```

## Why Python 3.13?
- Telegram bot library compatibility
- All dependencies tested and working
- Stable LTS version

## Dependencies Status
- ✅ python-telegram-bot v22.7
- ✅ MetaTrader5 v5.0.5640
- ✅ ccxt v4.5.42
- ✅ All other packages
