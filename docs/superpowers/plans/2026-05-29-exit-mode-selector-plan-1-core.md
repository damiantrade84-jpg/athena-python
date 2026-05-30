# Exit-Mode Selector — Plan 1: Core `exit_policy.py` Module

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure, unit-tested `exit_policy.py` module that resolves the effective exit mode for an Engine A trade, clamps SL/TP to a per-group advisable-pip band (RR-preserving), and exposes management predicates — with zero coupling to config/execution so consumers (Plans 2–4) inject data.

**Architecture:** A single top-level module of pure functions and string constants. It imports nothing from the project (in particular **not** `config.py`, whose import aborts on the real-orders safety gate) and performs no I/O. Callers pass in config maps, prices, and `pip_size`. This is the single source of truth that `execution.py`, `timed_exit_monitor.py`, and the backtester will all consume.

**Tech Stack:** Python 3.13, pytest. New file `exit_policy.py` at repo root (alongside `indicators.py`, `execution.py`, `timed_exit_monitor.py`). Tests in `tests/test_exit_policy.py`.

**Spec:** `docs/superpowers/specs/2026-05-29-exit-mode-selector-design.md` (Phase 2, sections "The four modes", "Mode resolution & storage", "Advisable-pip guardrail", "Architecture").

**Commit convention:** every commit ends with the repo trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Mode constants + `normalize_mode`

**Files:**
- Create: `exit_policy.py`
- Test: `tests/test_exit_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_policy.py
import exit_policy as ep


def test_mode_constants_are_distinct_and_known():
    modes = {
        ep.EXIT_MODE_STATIC,
        ep.EXIT_MODE_ADAPTIVE,
        ep.EXIT_MODE_MANUAL,
        ep.EXIT_MODE_TIME,
    }
    assert len(modes) == 4
    assert modes == set(ep.VALID_EXIT_MODES)
    assert ep.DEFAULT_EXIT_MODE == ep.EXIT_MODE_STATIC


def test_normalize_mode_accepts_known_with_case_and_whitespace():
    assert ep.normalize_mode("  Traditional_Static ") == ep.EXIT_MODE_STATIC
    assert ep.normalize_mode("ADAPTIVE_TRAIL") == ep.EXIT_MODE_ADAPTIVE


def test_normalize_mode_rejects_unknown_and_empty():
    assert ep.normalize_mode("nonsense") is None
    assert ep.normalize_mode("") is None
    assert ep.normalize_mode(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'exit_policy'`.

- [ ] **Step 3: Write minimal implementation**

```python
# exit_policy.py
"""Pure exit-mode policy for Engine A trades.

Single source of truth for: resolving the effective exit mode (per-trade ->
per-group -> global), clamping SL/TP to a per-group advisable-pip band, and
the management predicates consumers branch on. Imports nothing from the
project (notably NOT config.py, whose import aborts on the real-orders gate)
and does no I/O — callers inject config maps, prices, and pip_size.
"""

from __future__ import annotations

EXIT_MODE_STATIC = "traditional_static"
EXIT_MODE_ADAPTIVE = "adaptive_trail"
EXIT_MODE_MANUAL = "manual"
EXIT_MODE_TIME = "time_based"

VALID_EXIT_MODES = frozenset(
    {EXIT_MODE_STATIC, EXIT_MODE_ADAPTIVE, EXIT_MODE_MANUAL, EXIT_MODE_TIME}
)

# Ultimate fallback when neither per-trade, per-group, nor global config yields a
# recognized mode. traditional_static matches the user-authorized Engine-A default
# (see spec). Consumers still pass the config global default explicitly.
DEFAULT_EXIT_MODE = EXIT_MODE_STATIC


def normalize_mode(mode: str | None) -> str | None:
    """Return the canonical exit-mode string, or None if unrecognized."""
    if not mode:
        return None
    m = str(mode).strip().lower()
    return m if m in VALID_EXIT_MODES else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add exit_policy.py tests/test_exit_policy.py
git commit -m "feat(exit_policy): add mode constants and normalize_mode" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `resolve_exit_mode` + `group_default_for`

**Files:**
- Modify: `exit_policy.py`
- Test: `tests/test_exit_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_exit_policy.py
def test_group_default_for_reads_map_and_normalizes():
    gmap = {"forex_majors": "ADAPTIVE_TRAIL", "crypto_majors": "bogus"}
    assert ep.group_default_for("forex_majors", gmap) == ep.EXIT_MODE_ADAPTIVE
    assert ep.group_default_for("crypto_majors", gmap) is None  # invalid value
    assert ep.group_default_for("missing", gmap) is None
    assert ep.group_default_for(None, gmap) is None
    assert ep.group_default_for("forex_majors", None) is None


def test_resolve_precedence_per_trade_wins():
    assert (
        ep.resolve_exit_mode(
            per_trade="manual",
            group_default="adaptive_trail",
            global_default="traditional_static",
        )
        == ep.EXIT_MODE_MANUAL
    )


def test_resolve_falls_through_invalid_to_group_then_global():
    # invalid per-trade -> use group
    assert (
        ep.resolve_exit_mode(per_trade="junk", group_default="time_based")
        == ep.EXIT_MODE_TIME
    )
    # invalid per-trade and group -> use global
    assert (
        ep.resolve_exit_mode(
            per_trade="junk", group_default="junk", global_default="adaptive_trail"
        )
        == ep.EXIT_MODE_ADAPTIVE
    )
    # nothing valid anywhere -> DEFAULT_EXIT_MODE
    assert ep.resolve_exit_mode(per_trade="junk", group_default="junk", global_default="junk") == ep.DEFAULT_EXIT_MODE


def test_resolve_defaults_to_static_when_all_none():
    assert ep.resolve_exit_mode() == ep.EXIT_MODE_STATIC
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: FAIL with `AttributeError: module 'exit_policy' has no attribute 'group_default_for'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to exit_policy.py
def group_default_for(group_key: str | None, group_map: dict | None) -> str | None:
    """Look up a group's default exit mode from a config map; normalized or None."""
    if not group_key or not isinstance(group_map, dict):
        return None
    return normalize_mode(group_map.get(group_key))


def resolve_exit_mode(
    per_trade: str | None = None,
    group_default: str | None = None,
    global_default: str | None = DEFAULT_EXIT_MODE,
) -> str:
    """Effective mode = per-trade override -> per-group default -> global default.

    Unrecognized values at any level are skipped (fall through). Always returns a
    valid mode; final fallback is DEFAULT_EXIT_MODE.
    """
    for candidate in (per_trade, group_default, global_default):
        norm = normalize_mode(candidate)
        if norm is not None:
            return norm
    return DEFAULT_EXIT_MODE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add exit_policy.py tests/test_exit_policy.py
git commit -m "feat(exit_policy): add resolve_exit_mode and group_default_for" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `clamp_to_advisable_pip` (RR-preserving)

**Files:**
- Modify: `exit_policy.py`
- Test: `tests/test_exit_policy.py`

Semantics: clamp the **SL distance** (in price) to `[min_pip*pip_size, max_pip*pip_size]`,
then re-derive TP1/TP2 to preserve the original RR. No-op when `pip_size <= 0`,
when SL distance is 0, or when neither bound is a positive number. If a misconfigured
`max_pip < min_pip` is supplied, the max bound wins (conservative — never widens past max).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_exit_policy.py
import math


def _close(a, b, tol=1e-9):
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def test_clamp_noop_when_no_bounds():
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.0, 102.0, 103.0, pip_size=0.01)
    assert r["clamped"] is False
    assert (r["sl"], r["tp1"], r["tp2"]) == (99.0, 102.0, 103.0)


def test_clamp_noop_when_pip_size_nonpositive():
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.0, 102.0, 103.0, pip_size=0.0, min_pip=200)
    assert r["clamped"] is False


def test_clamp_min_widens_long_and_preserves_rr():
    # entry 100, sl 99.5 -> sl_dist 0.5; pip_size 0.01 -> 50 pips.
    # min_pip 100 -> min_dist 1.0 -> SL widens to 99.0.
    # original rr1 = (102-100)/0.5 = 4.0 -> new tp1 = 100 + 4*1.0 = 104.0
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.5, 102.0, 103.0, pip_size=0.01, min_pip=100)
    assert r["clamped"] is True
    assert _close(r["sl"], 99.0)
    assert _close(r["tp1"], 104.0)        # rr1 4.0 preserved
    assert _close(r["tp2"], 100.0 + 6.0)  # rr2 = (103-100)/0.5 = 6.0 -> 106.0


def test_clamp_max_tightens_short_and_preserves_rr():
    # SHORT entry 100, sl 103 -> sl_dist 3.0; pip_size 0.01 -> 300 pips.
    # max_pip 100 -> max_dist 1.0 -> SL tightens to 101.0.
    # rr1 = (100-98)/3.0 -> new tp1 = 100 - rr1*1.0
    rr1 = (100.0 - 98.0) / 3.0
    r = ep.clamp_to_advisable_pip("SHORT", 100.0, 103.0, 98.0, 97.0, pip_size=0.01, max_pip=100)
    assert r["clamped"] is True
    assert _close(r["sl"], 101.0)
    assert _close(r["tp1"], 100.0 - rr1 * 1.0)


def test_clamp_noop_when_within_band():
    # sl_dist 0.5 = 50 pips, band [10, 100] pips -> within -> no change.
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.5, 102.0, 103.0, pip_size=0.01, min_pip=10, max_pip=100)
    assert r["clamped"] is False


def test_clamp_noop_when_sl_dist_zero():
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 100.0, 102.0, 103.0, pip_size=0.01, min_pip=10)
    assert r["clamped"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: FAIL with `AttributeError: module 'exit_policy' has no attribute 'clamp_to_advisable_pip'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to exit_policy.py
def clamp_to_advisable_pip(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    pip_size: float,
    min_pip: float | None = None,
    max_pip: float | None = None,
) -> dict:
    """Clamp SL distance to a per-group advisable-pip band, RR-preserving.

    Returns {"sl", "tp1", "tp2", "clamped": bool}. No-op (clamped=False) when
    pip_size<=0, SL distance is 0, or neither bound is a positive number.
    direction: "LONG" or "SHORT". If max_pip < min_pip, max wins.
    """
    out = {"sl": sl, "tp1": tp1, "tp2": tp2, "clamped": False}
    if not pip_size or pip_size <= 0:
        return out
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return out

    lo = min_pip * pip_size if (min_pip is not None and min_pip > 0) else None
    hi = max_pip * pip_size if (max_pip is not None and max_pip > 0) else None
    if lo is None and hi is None:
        return out

    new_dist = sl_dist
    if lo is not None and new_dist < lo:
        new_dist = lo
    if hi is not None and new_dist > hi:  # max wins even if hi < lo
        new_dist = hi
    if new_dist == sl_dist:
        return out

    rr1 = abs(tp1 - entry) / sl_dist
    rr2 = abs(tp2 - entry) / sl_dist
    d = str(direction).upper()
    if d == "LONG":
        new_sl = entry - new_dist
        new_tp1 = entry + rr1 * new_dist
        new_tp2 = entry + rr2 * new_dist
    else:
        new_sl = entry + new_dist
        new_tp1 = entry - rr1 * new_dist
        new_tp2 = entry - rr2 * new_dist
    return {"sl": new_sl, "tp1": new_tp1, "tp2": new_tp2, "clamped": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add exit_policy.py tests/test_exit_policy.py
git commit -m "feat(exit_policy): add RR-preserving advisable-pip clamp" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Management predicates

**Files:**
- Modify: `exit_policy.py`
- Test: `tests/test_exit_policy.py`

These three predicates are the only branch points consumers need, keeping mode
logic out of `execution.py` / `timed_exit_monitor.py` / the backtester.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_exit_policy.py
def test_uses_trail_management_only_for_adaptive():
    assert ep.uses_trail_management("adaptive_trail") is True
    for m in ("traditional_static", "manual", "time_based", "junk", None):
        assert ep.uses_trail_management(m) is False


def test_uses_fixed_broker_tp_for_static_manual_time():
    for m in ("traditional_static", "manual", "time_based"):
        assert ep.uses_fixed_broker_tp(m) is True
    for m in ("adaptive_trail", "junk", None):
        assert ep.uses_fixed_broker_tp(m) is False


def test_uses_timed_close_only_for_time_based():
    assert ep.uses_timed_close("time_based") is True
    for m in ("traditional_static", "manual", "adaptive_trail", "junk", None):
        assert ep.uses_timed_close(m) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: FAIL with `AttributeError: module 'exit_policy' has no attribute 'uses_trail_management'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to exit_policy.py
def uses_trail_management(mode: str | None) -> bool:
    """True only for adaptive_trail — the only mode the chandelier/profit-protect runs for."""
    return normalize_mode(mode) == EXIT_MODE_ADAPTIVE


def uses_fixed_broker_tp(mode: str | None) -> bool:
    """True when the broker order carries a fixed TP and no trailing manages it."""
    return normalize_mode(mode) in (EXIT_MODE_STATIC, EXIT_MODE_MANUAL, EXIT_MODE_TIME)


def uses_timed_close(mode: str | None) -> bool:
    """True only for time_based — a timed close runs alongside the fixed SL/TP bracket."""
    return normalize_mode(mode) == EXIT_MODE_TIME
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_policy.py -q`
Expected: PASS (16 passed).

- [ ] **Step 5: Commit**

```bash
git add exit_policy.py tests/test_exit_policy.py
git commit -m "feat(exit_policy): add management predicates" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## What Plans 2–4 will consume from this module

- `resolve_exit_mode(per_trade, group_default, global_default)` — Plan 2 (execution: resolve at order time), Plan 3 (backtest).
- `group_default_for(group_key, group_map)` — Plan 2, where `group_map` = new `config.yaml` `ENGINE_A_EXIT_MODE_BY_SCORE_GROUP` and `group_key` = `get_pair_score_group(...)`.
- `clamp_to_advisable_pip(direction, entry, sl, tp1, tp2, pip_size, min_pip, max_pip)` — Plan 2, with `pip_size` from `mt5_get_symbol_info`/`bybit_get_symbol_info` and bounds from a new `ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP` map. **Not** applied to `manual` mode (spec Open Q3).
- `uses_trail_management(mode)` — Plan 2 (`timed_exit_monitor._evaluate_trail` short-circuit), Plan 3.
- `uses_fixed_broker_tp(mode)` — Plan 2 (`execution.py` attach fixed broker TP vs clear-for-trail).
- `uses_timed_close(mode)` — Plan 2/3 (timed close path).

---

## Self-Review

**Spec coverage (Plan 1's slice):**
- Four modes as constants — Task 1. ✅
- Resolution precedence per-trade→group→global — Task 2. ✅
- Advisable-pip clamp, RR-preserving, no-op when unset, manual-exempt (exemption enforced by caller in Plan 2; module is mode-agnostic) — Task 3. ✅
- Management semantics (static/manual/time = fixed bracket; adaptive = trail; time = timed close) exposed as predicates — Task 4. ✅
- Module imports no config (testable without the real-orders gate) — module docstring + Task 1 impl. ✅
- Live/backtest parity — guaranteed structurally by both consuming this one module (Plans 2 & 3).

**Placeholder scan:** none — every step has real test + impl code and exact commands.

**Type consistency:** `normalize_mode` (returns `str|None`) is used by `group_default_for`, `resolve_exit_mode`, and all three predicates consistently. `clamp_to_advisable_pip` returns the documented `{"sl","tp1","tp2","clamped"}` dict; tests assert those exact keys. Function names match between the impl, tests, and the "Plans 2–4 will consume" section.
