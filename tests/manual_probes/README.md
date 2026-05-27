Manual probes in this directory are intentionally excluded from pytest
collection by pyproject.toml.

These scripts are useful for local diagnostics, but they are not deterministic
tests: some import athena.py at module load, call live HTTP/websocket/Telegram
services, run backtests, print-only repro output, or depend on a running Flask
server.

Before moving any probe back into the active test suite, convert it to a real
pytest test with assertions, no import-time side effects, no hardcoded local
paths or secrets, and explicit skips/mocks for optional live dependencies.
