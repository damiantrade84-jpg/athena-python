#!/usr/bin/env python3
"""Print facts about the interpreter and WebSocket-related packages.

Run with the same executable you use to start the app, e.g.:
  .venv\\Scripts\\python.exe tools\\check_ws_env.py

This script does not guess your intent; it only reports what *this* process sees.
"""

from __future__ import annotations

import importlib.metadata
import ssl
import sys

# Must match requirements.txt
_EXPECTED_WEBSOCKETS = "16.0"
_EXPECTED_PYTHON_MINOR = 14


def _ver(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "(not installed)"


def main() -> None:
    print("Athena environment check (this process only)")
    print("=" * 50)
    print(f"executable: {sys.executable}")
    print(f"version:    {sys.version.split()[0]} ({sys.version})")

    vi = sys.version_info
    if vi < (3, 11):
        print("NOTICE:     Python <3.11 — below project requires-python (>=3.11,<3.15).")
    elif vi >= (3, 15):
        print("NOTICE:     Python >=3.15 — above project requires-python (>=3.11,<3.15).")
    elif vi[:2] != (3, _EXPECTED_PYTHON_MINOR):
        print(
            f"NOTICE:     Python {vi.major}.{vi.minor} — .python-version pins {_EXPECTED_PYTHON_MINOR}. "
            "Recreate .venv with the pinned interpreter."
        )

    ws = _ver("websockets")
    print(f"websockets: {ws}")
    if ws != _EXPECTED_WEBSOCKETS:
        print(
            f"WARNING:    websockets is {ws!r}; requirements.txt pins {_EXPECTED_WEBSOCKETS!r}. Reinstall from requirements.txt."
        )

    print(f"certifi:    {_ver('certifi')}")
    print(f"ccxt:       {_ver('ccxt')}")
    print(f"eodhd:      {_ver('eodhd')}")
    print(f"numba:      {_ver('numba')}")
    print(f"llvmlite:   {_ver('llvmlite')}")
    print(f"telegram:   {_ver('python-telegram-bot')}")
    print(f"OpenSSL:    {ssl.OPENSSL_VERSION}")
    print()
    print(
        f"If .python-version says {_EXPECTED_PYTHON_MINOR} but version above differs, "
        "this .venv was not created with the expected Python."
    )


if __name__ == "__main__":
    main()
