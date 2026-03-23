#!/usr/bin/env python3
"""Print Python + WebSocket-related package versions after a venv or Python upgrade.

Run: python tools/check_ws_env.py
Compare output before/after changing interpreters to spot websockets/ssl drift.
"""

from __future__ import annotations

import importlib.metadata
import ssl
import sys


def _ver(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "(not installed)"


def main() -> None:
    print("Athena WebSocket / TLS environment check")
    print("=" * 50)
    print(f"Python:     {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info >= (3, 14):
        print(
            "WARNING:    Python 3.14+ — full `pip install -r requirements.txt` may fail (numba/pandas-ta)."
        )
        print("            Use Python 3.13 or 3.12 for a clean venv until upstream adds 3.14 wheels.")
    print(f"websockets: {_ver('websockets')}")
    print(f"certifi:    {_ver('certifi')}")
    print(f"ccxt:       {_ver('ccxt')}")
    print(f"eodhd:      {_ver('eodhd')}")
    print(f"OpenSSL:    {ssl.OPENSSL_VERSION}")
    print()
    print("Expected: websockets==16.0 per requirements.txt")
    print("If handshakes fail after a Python change: reinstall venv from requirements.txt.")


if __name__ == "__main__":
    main()
