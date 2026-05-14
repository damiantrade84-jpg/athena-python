---
name: run-test-subset
description: >
  Map a keyword (engine_b, engine_d, scalp, execution, risk, backtest, …) to a focused
  pytest invocation for ATHENA. Use when the user asks to run a subset of tests without
  the full suite. Invoke as /run-test-subset <keyword> or ask with a keyword.
---

# Run test subset (ATHENA)

Run from repo root: `c:\dev\athena-python` (or your checkout).

Default flags: `-q --tb=short`. Add `-x` to stop on first failure when debugging.

## Keyword → command

| Keyword | Command |
|---------|---------|
| `engine_b`, `naked`, `b_only` | `python -m pytest tests/ -q --tb=short -k "engine_b or naked or zone_registry"` |
| `engine_d`, `scalp`, `scalp_engine` | `python -m pytest tests/ -q --tb=short -k "engine_d or scalp_engine or scalp_execution or scalp_backtest or eodhd_volume"` |
| `engine_a`, `scoring`, `factor` | `python -m pytest tests/ -q --tb=short -k "engine_a or factor_scoring or scoring"` |
| `engine_c` | `python -m pytest tests/ -q --tb=short -k "engine_c"` |
| `execution`, `executor` | `python -m pytest tests/ -q --tb=short -k "execution or executor or golden_execution"` |
| `risk` | `python -m pytest tests/test_risk_engine.py -q --tb=short` |
| `auto_trader`, `autopilot` | `python -m pytest tests/test_auto_trader.py tests/test_autopilot_regression.py -q --tb=short` |
| `bybit` | `python -m pytest tests/test_bybit_executor_safety.py -q --tb=short` |
| `timed_exit`, `trail` | `python -m pytest tests/ -q --tb=short -k "timed_exit"` |
| `backtest`, `bt_runner` | `python -m pytest tests/ -q --tb=short -k "backtest or scan_backtest"` |
| `routes`, `api` | `python -m pytest tests/ -q --tb=short -k "test_routes or api_contract"` |
| `safety`, `gate` | `python -m pytest tests/ -q --tb=short -k "scanner_safety or execution_safety or safety_regressions or safety_audit"` |

For **routes/api**, prefer explicit files: `tests/test_routes_audit.py`, `tests/test_health_routes.py`, etc., when you know the failing area.

## Single file

User gives a path: run `python -m pytest path/to/test_file.py -q --tb=short`.

## Notes

- Never import `athena.py` in tests (project invariant).
- SQLite tests: WAL + timeout expectations per `AGENTS.md`.
