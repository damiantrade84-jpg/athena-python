# Exit-Mode Selector — Plan 2: Live Execution + Exit Integration (Engine A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Plan-1 `exit_policy` module into live Engine A execution and exit management so each Engine A trade is placed and managed according to its resolved exit mode (`traditional_static` default, `adaptive_trail`, `manual`, `time_based`), with the advisable-pip clamp applied and the mode persisted on the trade.

**Architecture:** Three integration points, all keyed to `engine_a` only: (1) `execution.py` resolves the mode + applies the RR-preserving advisable-pip clamp right after `symbol_info` is available and before the risk/RR gates, and persists `exit_mode`; (2) the `audit_log` schema gains an `exit_mode` column (+ migration) and the monitor SELECT reads it; (3) `timed_exit_monitor.py` short-circuits Engine A `static`/`manual` trades to a pure broker bracket and runs an N-bar timed close for `time_based`, while `adaptive_trail` and all non-Engine-A trades keep today's behavior. Backtest parity is Plan 3.

**Tech Stack:** Python 3.13, pytest, sqlite3. Touches `config.py`, `config.yaml`, `execution.py`, `timed_exit_monitor.py`, `athena.py` (audit schema), tests under `tests/`.

**Spec:** `docs/superpowers/specs/2026-05-29-exit-mode-selector-design.md`. **Depends on:** Plan 1 (`exit_policy.py`, committed).

---

## ⛔ PRE-IMPLEMENTATION GATE — `/athena-audit`

This plan edits `execution.py` and `timed_exit_monitor.py` (execution-safety files). Per CLAUDE.md mandatory routing, **the user must run `/athena-audit` before these edits begin** (Claude cannot self-invoke it). Only **Task 1 (config-only: `config.py`/`config.yaml`)** is un-gated and may proceed first. **Tasks 2–5 are gated** — Task 2 already edits the `execution.py:1100` INSERT, and Tasks 3–5 edit `execution.py`/`timed_exit_monitor.py`. Do not start Task 2 until `/athena-audit` has run and its findings are reconciled into this plan.

**Verified facts (from tracing current source, 2026-05-29):**
- `engine` column value for Engine A = `engine_a` (also present: `engine_b`, `scalp`).
- Execute-path audit INSERT: `execution.py:1100` (22 columns, ends `max_score,score_pct`).
- `get_pair_score_group` is imported at `execution.py:31` and used at `:1228` (`get_pair_score_group(pair)`).
- `audit_log` CREATE TABLE: `athena.py:5087`; 42 columns, no `exit_mode`.
- Monitor open-rows SELECT: `timed_exit_monitor.py:786`.
- MT5 single-row handler starts `:1600`; Engine D early-return `:1642`; trail block gated by `tcfg.get("tp_mode") == "trailing_atr"` at `:1675`. Bybit handler is the parallel one near `:1954`.
- `symbol_info` (mt5) exposes `point`, `digits`, `trade_tick_size` (`mt5_executor.py:1135-1142`); available in the execute path at `execution.py:992` (bybit) / `:1013` (mt5).

**Confirm at implementation start:**
- Whether a second Engine A execute/persist path exists (there is another INSERT near `execution.py:2041`); if it also persists Engine A fills, apply the Task-2 INSERT change there too.
- Bybit `symbol_info` pip/point field names (mirror the MT5 `point`/`digits` handling in `_pip_size`).

---

### Task 1: Config keys + loader defaults

**Files:**
- Modify: `config.py` (defaults block, near the other `ENGINE_A_*` defaults)
- Modify: `config.yaml` (near the other `ENGINE_A_*` keys, e.g. after `ENGINE_A_STRUCTURAL_SL_FLOOR_ATR`)
- Test: `tests/test_exit_mode_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_mode_config.py
import yaml


def _load():
    with open("config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_exit_mode_config_keys_present_and_typed():
    cfg = _load()
    assert cfg["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"] == "traditional_static"
    assert isinstance(cfg["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"], dict)
    assert isinstance(cfg["ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP"], dict)
    bars = cfg["ENGINE_A_TIME_EXIT_BARS"]
    assert set(bars) == {"scalp", "intraday", "swing"}
    assert all(isinstance(v, int) and v > 0 for v in bars.values())


def test_global_default_is_a_valid_exit_mode():
    import exit_policy as ep
    cfg = _load()
    assert ep.normalize_mode(cfg["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"]) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_mode_config.py -q`
Expected: FAIL with `KeyError: 'ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT'`.

- [ ] **Step 3: Add config keys**

In `config.yaml`, add (place next to the other `ENGINE_A_*` keys):

```yaml
# Engine A exit-mode selector (Plan 2). Per-score-group default exit mode + global
# fallback. Ships global=traditional_static and an empty per-group map, so every
# Engine A score group defaults to traditional_static (user-authorized Engine-A
# default flip — see spec). Valid values:
#   traditional_static | adaptive_trail | manual | time_based
ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT: traditional_static
ENGINE_A_EXIT_MODE_BY_SCORE_GROUP: {}        # e.g. {forex_majors: adaptive_trail}

# Engine A advisable-pip guardrail (Plan 2). Per-score-group SL min/max in pips.
# Ships empty = no clamp. Applied to every mode EXCEPT manual.
ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP: {}    # e.g. {forex_majors: {min_pip: 8, max_pip: 60}}

# time_based exit: bars of the style timeframe to hold before the timed close.
ENGINE_A_TIME_EXIT_BARS:
  scalp: 12
  intraday: 18
  swing: 10
```

In `config.py`, add matching defaults so `CONFIG.get(...)` is safe even if the YAML key is removed (mirror how other `ENGINE_A_*` defaults are declared in that file):

```python
"ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
"ENGINE_A_EXIT_MODE_BY_SCORE_GROUP": {},
"ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP": {},
"ENGINE_A_TIME_EXIT_BARS": {"scalp": 12, "intraday": 18, "swing": 10},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_mode_config.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add config.py config.yaml tests/test_exit_mode_config.py
git commit -m "feat(exit_mode): add Engine A exit-mode + advisable-pip config keys" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `audit_log.exit_mode` column, migration, persist, and monitor SELECT

**Files:**
- Modify: `athena.py:5087` (CREATE TABLE — add column) and the schema-migration block that ALTERs `audit_log` for existing DBs
- Modify: `execution.py:1100` (INSERT column list + value)
- Modify: `timed_exit_monitor.py:786` (SELECT column list)
- Test: `tests/test_exit_mode_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_mode_persistence.py
import sqlite3


def _audit_ddl():
    # The new column must be in the CREATE TABLE text AND the monitor SELECT.
    with open("athena.py", encoding="utf-8") as fh:
        athena_src = fh.read()
    with open("timed_exit_monitor.py", encoding="utf-8") as fh:
        mon_src = fh.read()
    with open("execution.py", encoding="utf-8") as fh:
        exec_src = fh.read()
    return athena_src, mon_src, exec_src


def test_exit_mode_in_schema_select_and_insert():
    athena_src, mon_src, exec_src = _audit_ddl()
    assert "exit_mode" in athena_src                      # CREATE TABLE + migration
    # monitor SELECT pulls exit_mode so the row dict carries it
    assert "exit_mode" in mon_src and "FROM   audit_log" in mon_src
    # execute-path INSERT persists exit_mode
    assert "exit_mode" in exec_src


def test_alter_adds_exit_mode_when_missing():
    # Reproduce the migration on a minimal table: adding the column is idempotent-safe.
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_log)")}
    assert "exit_mode" not in cols
    con.execute("ALTER TABLE audit_log ADD COLUMN exit_mode TEXT")
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_log)")}
    assert "exit_mode" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_mode_persistence.py -q`
Expected: FAIL on `test_exit_mode_in_schema_select_and_insert` (no `exit_mode` yet). (`test_alter_adds_exit_mode_when_missing` passes — it's an invariant guard.)

- [ ] **Step 3: Implement the schema + persistence**

1. `athena.py:5087` CREATE TABLE — add a column (place after `exit_reason`):

```sql
            exit_mode             TEXT,
```

2. In the schema-migration block (where other `ALTER TABLE audit_log ADD COLUMN` statements run for existing DBs — search `ALTER TABLE audit_log ADD COLUMN`), add the idempotent migration following the file's existing pattern (typically a try/except per column):

```python
try:
    con.execute("ALTER TABLE audit_log ADD COLUMN exit_mode TEXT")
except sqlite3.OperationalError:
    pass  # column already exists
```

3. `execution.py:1100` INSERT — append `exit_mode` to the column list, add one `?`, and append `sig.get("exit_mode")` to the per-row tuple built into `_audit_rows`:

```python
"INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
"entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
"max_score,score_pct,exit_mode) "
"VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
```
(and add `sig.get("exit_mode")` as the final element of each row appended to `_audit_rows`.)

4. `timed_exit_monitor.py:786` SELECT — add `exit_mode` to the column list:

```python
    select_cols = """
        SELECT id, ticket, pair, engine, style, ts, direction, entry_price, sl, tp,
               tp_partial, volume, risk_amount, asset_class, exit_time, grade, exit_mode
        FROM   audit_log
        WHERE  pair IS NOT NULL
          AND  grade NOT LIKE '%ERR%'
    """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_mode_persistence.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add athena.py execution.py timed_exit_monitor.py tests/test_exit_mode_persistence.py
git commit -m "feat(exit_mode): persist exit_mode on audit_log and read it in the monitor" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Resolve mode + advisable-pip clamp at order time (`execution.py`)  — GATED on `/athena-audit`

**Files:**
- Modify: `execution.py` (new `_pip_size` helper near `_apply_level_override`; resolve + clamp block after `_hydrate_execution_candle_quality(sig, _r=_r)` at `:1018`, before the risk/RR gate)
- Test: `tests/test_execution_exit_mode.py`

- [ ] **Step 1: Write the failing test (pure helper)**

```python
# tests/test_execution_exit_mode.py
import execution


def test_pip_size_fx_5digit_uses_ten_points():
    # 5-digit FX: pip = 10 * point
    si = {"point": 0.00001, "digits": 5}
    assert execution._pip_size(si, asset_class="forex") == 0.0001


def test_pip_size_fx_3digit_uses_ten_points():
    si = {"point": 0.001, "digits": 3}
    assert execution._pip_size(si, asset_class="forex") == 0.01


def test_pip_size_non_fx_uses_point():
    si = {"point": 0.1, "digits": 1}
    assert execution._pip_size(si, asset_class="index") == 0.1


def test_pip_size_missing_returns_zero():
    assert execution._pip_size({}, asset_class="forex") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_exit_mode.py -q`
Expected: FAIL with `AttributeError: module 'execution' has no attribute '_pip_size'`.

- [ ] **Step 3: Implement `_pip_size` + the resolve/clamp block**

Add the helper near `_apply_level_override` in `execution.py`:

```python
def _pip_size(symbol_info: dict, asset_class: str | None) -> float:
    """Derive pip size from broker symbol info. FX (3/5-digit) pip = 10*point;
    other classes use the raw point. Returns 0.0 when point is unavailable."""
    try:
        point = float((symbol_info or {}).get("point") or 0)
    except (TypeError, ValueError):
        return 0.0
    if point <= 0:
        return 0.0
    digits = (symbol_info or {}).get("digits")
    if str(asset_class or "").lower() in ("forex", "fx") and digits in (3, 5):
        return point * 10
    return point
```

Add the resolve + clamp block immediately after `_hydrate_execution_candle_quality(sig, _r=_r)` (`:1018`). It runs **only for Engine A** and **before** the risk/RR gate:

```python
import exit_policy

_audit_engine = str(sig.get("engine") or "").lower()
if _audit_engine == "engine_a":
    _score_group = get_pair_score_group(sig.get("pair") or sig.get("display"))
    _group_map = _r.CONFIG.get("ENGINE_A_EXIT_MODE_BY_SCORE_GROUP") or {}
    _per_trade = (level_override or {}).get("exit_mode") or sig.get("exit_mode")
    _mode = exit_policy.resolve_exit_mode(
        per_trade=_per_trade,
        group_default=exit_policy.group_default_for(_score_group, _group_map),
        global_default=_r.CONFIG.get("ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT", exit_policy.DEFAULT_EXIT_MODE),
    )
    sig["exit_mode"] = _mode
    # Advisable-pip clamp — all modes except manual; before the risk/RR gate.
    if _mode != exit_policy.EXIT_MODE_MANUAL:
        _bounds = (_r.CONFIG.get("ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP") or {}).get(_score_group) or {}
        _pip = _pip_size(symbol_info, sig.get("type") or sig.get("asset_class"))
        try:
            _entry = float(sig.get("price") or sig.get("livePrice") or 0)
        except (TypeError, ValueError):
            _entry = 0.0
        if _pip > 0 and _entry > 0 and sig.get("sl") and sig.get("tp1"):
            _clamped = exit_policy.clamp_to_advisable_pip(
                str(sig.get("direction") or "").upper(),
                _entry, float(sig["sl"]), float(sig["tp1"]),
                float(sig.get("tp2") or sig["tp1"]),
                _pip, _bounds.get("min_pip"), _bounds.get("max_pip"),
            )
            if _clamped["clamped"]:
                sig["sl"], sig["tp1"], sig["tp2"] = _clamped["sl"], _clamped["tp1"], _clamped["tp2"]
                _r.log.warning(
                    f"[QUICK EXEC] {sig.get('pair')}: advisable-pip clamp applied "
                    f"(group={_score_group}) SL={sig['sl']} TP1={sig['tp1']}"
                )
```

**Safety note (carry into the audit):** the clamp sits BEFORE `risk_check`/RR/`min_rr`/broker validation — it never bypasses them; a clamped trade that then fails RR/min_rr is rejected exactly as today. `exit_mode` is set only for `engine_a`; other engines are untouched and `sig.get("exit_mode")` stays `None` (column persists NULL → monitor treats as today's behavior).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_exit_mode.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add execution.py tests/test_execution_exit_mode.py
git commit -m "feat(exit_mode): resolve Engine A exit mode and apply advisable-pip clamp at order time" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Monitor dispatch — MT5 handler  — GATED on `/athena-audit`

**Files:**
- Modify: `timed_exit_monitor.py` (add `_is_engine_a_engine`, `_engine_a_exit_dispatch`, `_time_close_after_min`; wire into the MT5 handler after the Engine D return at `:1642`)
- Test: `tests/test_exit_mode_monitor.py`

The trail/profit-protect path (the regression source) runs only for `tcfg.tp_mode == "trailing_atr"`. We add a single early branch so Engine A `static`/`manual` become a pure broker bracket (no monitor management) and `time_based` gets an N-bar close — leaving `adaptive_trail` and all non-Engine-A trades on today's exact path.

- [ ] **Step 1: Write the failing test (pure helpers)**

```python
# tests/test_exit_mode_monitor.py
import timed_exit_monitor as tem


def test_is_engine_a_engine():
    assert tem._is_engine_a_engine("engine_a") is True
    assert tem._is_engine_a_engine("Engine A") is True
    for e in ("engine_b", "scalp", "", None):
        assert tem._is_engine_a_engine(e) is False


def test_dispatch_non_engine_a_always_trails():
    assert tem._engine_a_exit_dispatch("engine_b", "traditional_static", 999, 10) == "trail"


def test_dispatch_engine_a_adaptive_trails():
    assert tem._engine_a_exit_dispatch("engine_a", "adaptive_trail", 999, 10) == "trail"


def test_dispatch_engine_a_static_and_manual_hold():
    assert tem._engine_a_exit_dispatch("engine_a", "traditional_static", 999, 10) == "hold"
    assert tem._engine_a_exit_dispatch("engine_a", "manual", 999, 10) == "hold"


def test_dispatch_engine_a_unknown_mode_trails():
    # NULL/blank exit_mode (legacy rows) -> safe default to today's behavior.
    assert tem._engine_a_exit_dispatch("engine_a", None, 999, 10) == "trail"
    assert tem._engine_a_exit_dispatch("engine_a", "junk", 999, 10) == "trail"


def test_dispatch_engine_a_time_based():
    assert tem._engine_a_exit_dispatch("engine_a", "time_based", 5, 10) == "hold"        # not yet due
    assert tem._engine_a_exit_dispatch("engine_a", "time_based", 10, 10) == "timed_close"  # due
    assert tem._engine_a_exit_dispatch("engine_a", "time_based", 99, 10) == "timed_close"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_exit_mode_monitor.py -q`
Expected: FAIL with `AttributeError: module 'timed_exit_monitor' has no attribute '_is_engine_a_engine'`.

- [ ] **Step 3: Implement the pure helpers + MT5 wiring**

Add near `_is_engine_d_engine` (`:679`) in `timed_exit_monitor.py`:

```python
import exit_policy

_TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _is_engine_a_engine(engine: str | None) -> bool:
    return str(engine or "").strip().lower() in ("engine_a", "engine a")


def _engine_a_exit_dispatch(engine, exit_mode, mins_open: float, close_after_min: float) -> str:
    """Decide monitor handling for an Engine A row. Returns:
      'trail'       -> run today's trail/profit-protect/timed logic (adaptive, or non-Engine-A,
                       or unknown/legacy mode — fail safe to current behavior)
      'hold'        -> broker SL/TP bracket only; monitor takes no action this tick
      'timed_close' -> close now (time_based and the N-bar window has elapsed)
    """
    if not _is_engine_a_engine(engine):
        return "trail"
    em = exit_policy.normalize_mode(exit_mode)
    if em is None or exit_policy.uses_trail_management(em):
        return "trail"
    if exit_policy.uses_timed_close(em):
        return "timed_close" if mins_open >= close_after_min else "hold"
    return "hold"  # traditional_static / manual


def _time_close_after_min(tcfg: dict, style: str, trail_tf_by_style: dict | None = None) -> float:
    """N bars * style-timeframe minutes. Bars from ENGINE_A_TIME_EXIT_BARS, timeframe
    from trail_timeframe[style] (falls back to H4=240)."""
    bars_map = tcfg.get("engine_a_time_exit_bars") or {"scalp": 12, "intraday": 18, "swing": 10}
    bars = float(bars_map.get(style, bars_map.get("intraday", 18)))
    tf_map = trail_tf_by_style or tcfg.get("trail_timeframe") or {}
    tf = str(tf_map.get(style, "H4")).upper()
    return bars * float(_TF_MINUTES.get(tf, 240))
```

Note: `engine_a_time_exit_bars` must be threaded into the merged `tcfg` from `ENGINE_A_TIME_EXIT_BARS` in the config-merge function (`_merge_*` near `:560-676`); add it there alongside the other merged keys.

Wire into the MT5 handler, immediately after the Engine D early-return (`:1642-1643`):

```python
    _ea_dispatch = _engine_a_exit_dispatch(
        engine, row.get("exit_mode"), _minutes_open(row["ts"]),
        _time_close_after_min(tcfg, style),
    )
    if _ea_dispatch == "hold":
        return  # static/manual: broker SL+TP bracket manages the trade
    if _ea_dispatch == "timed_close":
        live = next((p for p in (mt5_get_positions().get("positions") or [])
                     if p.get("ticket") == ticket), None)
        if live:
            result = mt5_close_position(ticket)
            if result.get("success") and db_path:
                _mark_timed_close(db_path, row, "mt5",
                                  actual_close_price=result.get("closePrice"),
                                  live_pnl=result.get("liveProfit", 0.0),
                                  reason="TIME_EXIT")
        return
    # _ea_dispatch == "trail": fall through to today's logic unchanged
```

(Place this so it runs once per tick before the `tp_mode == "trailing_atr"` block. The existing `mt5_get_positions()`/`live` fetch above already happened at `:1622`; reuse that `live` instead of re-fetching if the branch is placed after it — finalize exact placement during the audited edit.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_exit_mode_monitor.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add timed_exit_monitor.py tests/test_exit_mode_monitor.py
git commit -m "feat(exit_mode): Engine A exit-mode dispatch in the MT5 timed-exit handler" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Monitor dispatch — Bybit handler + regression guard  — GATED on `/athena-audit`

**Files:**
- Modify: `timed_exit_monitor.py` (Bybit handler near `:1954`, after its Engine D early-return)
- Test: `tests/test_timed_exit_phases.py` (add a regression assertion)

- [ ] **Step 1: Write the failing/guard test**

```python
# append to tests/test_timed_exit_phases.py
def test_adaptive_and_non_engine_a_still_trail():
    import timed_exit_monitor as tem
    # adaptive Engine A and any non-Engine-A row must keep today's path ("trail")
    assert tem._engine_a_exit_dispatch("engine_a", "adaptive_trail", 9999, 1) == "trail"
    assert tem._engine_a_exit_dispatch("engine_b", "traditional_static", 9999, 1) == "trail"
    assert tem._engine_a_exit_dispatch("scalp", "time_based", 9999, 1) == "trail"
```

- [ ] **Step 2: Run test to verify current state**

Run: `python -m pytest tests/test_timed_exit_phases.py::test_adaptive_and_non_engine_a_still_trail -q`
Expected: PASS once Task 4 helpers exist (this is a guard that Task 5's Bybit edit doesn't regress the dispatch contract).

- [ ] **Step 3: Implement the Bybit wiring**

Mirror the Task-4 dispatch block in the Bybit handler, immediately after its Engine D early-return (near `:1991`), using `bybit_close_position`/the bybit position fetch and `_mark_timed_close(db_path, row, "bybit", ...)`. The pure helpers from Task 4 are reused unchanged.

- [ ] **Step 4: Run targeted tests**

Run: `python -m pytest tests/test_exit_mode_monitor.py tests/test_timed_exit_phases.py -q`
Expected: PASS (all green; pre-existing `.pytest_tmp` teardown warnings on Windows are unrelated).

- [ ] **Step 5: Commit**

```bash
git add timed_exit_monitor.py tests/test_timed_exit_phases.py
git commit -m "feat(exit_mode): Engine A exit-mode dispatch in the Bybit timed-exit handler" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Per-group default + per-trade override resolution — Task 3 (`resolve_exit_mode` with `level_override`/`sig` per-trade, `ENGINE_A_EXIT_MODE_BY_SCORE_GROUP` group, global default). ✅
- `traditional_static` ships as the Engine A default — Task 1 (global default + empty group map). ✅
- Advisable-pip clamp, manual-exempt, before gates — Task 3. ✅
- `static`/`manual` = pure broker bracket; `adaptive_trail` unchanged; `time_based` = N-bar close — Task 4/5 dispatch. ✅
- Engine A only; B/C/D untouched — `_is_engine_a_engine` guard in Task 3 (set) and Task 4/5 (dispatch); legacy/NULL modes fail safe to `trail`. ✅
- Persist `exit_mode`; monitor reads it — Task 2. ✅
- Never bypass risk/RR/broker gates; AI not involved — Task 3 safety note (clamp before `risk_check`). ✅
- Backtest parity — **deferred to Plan 3** (out of scope here; noted).

**Placeholder scan:** No TBD/TODO. Two explicit "confirm at implementation start" items (second INSERT path; bybit pip field) are verification steps, not placeholders, and are listed in the gate section. The MT5 branch placement note ("finalize exact placement during the audited edit") is required because the edit is `/athena-audit`-gated.

**Type consistency:** `_engine_a_exit_dispatch` returns the string set `{"trail","hold","timed_close"}` used identically in Task 4 wiring and Task 5 guard test. `_pip_size(symbol_info, asset_class)` signature matches its tests. `exit_policy.resolve_exit_mode` / `group_default_for` / `clamp_to_advisable_pip` / `uses_trail_management` / `uses_timed_close` / `normalize_mode` / `DEFAULT_EXIT_MODE` / `EXIT_MODE_MANUAL` are all used with the exact signatures defined in Plan 1.
