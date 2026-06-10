# Percent-of-Peak Giveback Close Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once the chandelier trail is active, force-close a trade that gives back ~40% of its peak R (with a 0.30R minimum budget), replacing the disabled fixed-R giveback.

**Architecture:** A new resolver `_giveback_budget_r_for` in `timed_exit_monitor.py` computes a dynamic budget `max(frac × peak_r, min_r)` when `trail_giveback_frac_of_peak > 0`, falling back to the existing fixed `_giveback_r_for` when frac is 0. Two call sites use it: (1) the existing post-activation giveback check in `_evaluate_trail` (line ~1634), and (2) a new branch inside the `below_activation` early return (line ~1581) — needed because a trade whose giveback close level sits *below* `trail_activation_r` (e.g. scalp peak 0.7R closing at 0.4R) retreats through the activation gate before the budget is consumed, and today's code returns `below_activation` without ever checking giveback. Two new config keys are parsed in `_get_timed_cfg` with the same scalar-or-dict pattern as `trail_giveback_r`. The existing `peak_giveback` close path (TRAIL_GIVEBACK close_reason, audit log, Telegram, runner-leg close — verified at `timed_exit_monitor.py:1916-1956`) is reused unchanged; the handler only needs `{"action": "close", "reason": "peak_giveback"}`.

**Tech Stack:** Python, pytest. Spec: `docs/superpowers/specs/2026-06-10-percent-of-peak-giveback-design.md`.

**Constraints (repo rules):**
- Test budget: only `pytest tests/test_chandelier_giveback.py -q` (one file). Do not run other test files or full suites.
- Engine D untouched: its `trail_mode="pre_activation_only"` calls must not reach the new logic — both call sites are gated to the `full` path (the new below-activation branch checks `trail_mode == "full"` explicitly; the post-activation site is already after the `pre_activation_only_cap` return at line ~1591).
- Do not change `trail_giveback_r` values, pre-activation protect, BE arming, activation thresholds, or `hybrid_scaleout`.
- `_safe_float(value)` already exists in this module (line 955) and takes ONE argument, returning 0.0 on failure. Do not add a second helper; use try/except where a non-zero default is needed (matches the merge-code style at lines 734-737).

---

### Task 1: Config defaults + parsing + budget resolver

**Files:**
- Modify: `timed_exit_monitor.py` (`_DEFAULT_CFG` ~line 86; `_get_timed_cfg` merge block after the `trail_giveback_r_by_venue` parsing ending ~line 684; new function after `_giveback_r_for` ~line 877)
- Test: `tests/test_chandelier_giveback.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chandelier_giveback.py` (the file already does `import timed_exit_monitor as tem`; reference the new function as `tem._giveback_budget_r_for` so the module-top import list stays unchanged):

```python
class TestPercentOfPeakBudgetResolver:
    def test_frac_budget_scales_with_peak(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 0.40,
            "trail_giveback_min_r": 0.30,
        })
        # 0.40 * 2.0R = 0.8R budget (floor 0.30 not binding)
        assert tem._giveback_budget_r_for(tcfg, "intraday", "mt5", 2.0) == pytest.approx(0.8)

    def test_min_floor_binds_near_activation(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 0.40,
            "trail_giveback_min_r": 0.30,
        })
        # 0.40 * 0.7R = 0.28 < 0.30 floor -> 0.30
        assert tem._giveback_budget_r_for(tcfg, "scalp", "mt5", 0.7) == pytest.approx(0.30)

    def test_frac_zero_falls_back_to_fixed(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 0.0,
            "trail_giveback_min_r": 0.30,
        })
        # Falls back to fixed trail_giveback_r_by_venue: mt5/intraday = 0.35
        assert tem._giveback_budget_r_for(tcfg, "intraday", "mt5", 2.0) == pytest.approx(0.35)

    def test_per_style_dict_accepted(self):
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": {"scalp": 0.5, "intraday": 0.4, "swing": 0.35},
            "trail_giveback_min_r": 0.30,
        })
        assert tem._giveback_budget_r_for(tcfg, "swing", None, 4.0) == pytest.approx(1.4)

    def test_frac_clamped_below_one(self):
        # A misconfigured frac >= 1 would let the close fire at/below 0R.
        # Parser clamps to 0.9 -> budget 0.9 * 2.0 = 1.8R.
        tcfg = _cfg({
            "trail_giveback_frac_of_peak": 1.5,
            "trail_giveback_min_r": 0.30,
        })
        assert tem._giveback_budget_r_for(tcfg, "intraday", "mt5", 2.0) == pytest.approx(1.8)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chandelier_giveback.py::TestPercentOfPeakBudgetResolver -q`
Expected: 5 failures — `AttributeError: module 'timed_exit_monitor' has no attribute '_giveback_budget_r_for'`.

- [ ] **Step 3: Implement defaults, parsing, and resolver**

3a. In `_DEFAULT_CFG`, directly after the `"trail_atr_mult"` entry (~line 86), add:

```python
    # Percent-of-peak give-back: budget = max(frac * peak_r, min_r). 0 = off
    # (fixed trail_giveback_r then applies). Enabled via config.yaml.
    "trail_giveback_frac_of_peak": {"scalp": 0.0, "intraday": 0.0, "swing": 0.0},
    "trail_giveback_min_r": 0.30,
```

3b. In `_get_timed_cfg`, immediately after the `trail_giveback_r_by_venue` merge block (the `}` closing the venue-bucket dict-comprehension loop, ~line 684), add:

```python
    # Percent-of-peak give-back fraction: scalar or per-style dict. Clamped to
    # [0, 0.9] — frac >= 1 would let the give-back close fire at or below 0R.
    _frac_defaults = _DEFAULT_CFG["trail_giveback_frac_of_peak"]
    _frac_raw = raw.get("trail_giveback_frac_of_peak", _frac_defaults)
    if isinstance(_frac_raw, dict):
        merged["trail_giveback_frac_of_peak"] = {
            s: min(max(_safe_float(_frac_raw.get(s, _frac_defaults[s])), 0.0), 0.9)
            for s in ("scalp", "intraday", "swing")
        }
    else:
        _frac_scalar = min(max(_safe_float(_frac_raw), 0.0), 0.9)
        merged["trail_giveback_frac_of_peak"] = {
            s: _frac_scalar for s in ("scalp", "intraday", "swing")
        }
    try:
        _gb_min = float(raw.get("trail_giveback_min_r", _DEFAULT_CFG["trail_giveback_min_r"]))
    except (TypeError, ValueError):
        _gb_min = float(_DEFAULT_CFG["trail_giveback_min_r"])
    merged["trail_giveback_min_r"] = max(_gb_min, 0.0)
```

Note: `_safe_float` is defined at line 955, *after* `_get_timed_cfg` in the file — that's fine (resolved at call time, and `_get_timed_cfg` is never called at import time). If the implementing engineer prefers, the try/except pattern used for `timer_tighten_factor` (lines 734-737) is equally acceptable for the frac values.

3c. New resolver directly after `_giveback_r_for` (~line 877):

```python
def _giveback_budget_r_for(
    tcfg: dict, style: str, venue: str | None, peak_r: float
) -> float:
    """Effective peak give-back budget in R.

    Percent-of-peak (trail_giveback_frac_of_peak > 0) wins over the fixed
    trail_giveback_r: budget = max(frac * peak_r, trail_giveback_min_r).
    With frac == 0 the fixed budget applies; 0 everywhere disables give-back.
    """
    frac_map = tcfg.get("trail_giveback_frac_of_peak") or {}
    if isinstance(frac_map, dict):
        frac = _safe_float(frac_map.get(style, 0.0))
    else:
        frac = _safe_float(frac_map)
    if frac > 0 and peak_r > 0:
        min_r = _safe_float(tcfg.get("trail_giveback_min_r", 0.0))
        return max(frac * peak_r, min_r)
    return _giveback_r_for(tcfg, style, venue)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chandelier_giveback.py::TestPercentOfPeakBudgetResolver -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add timed_exit_monitor.py tests/test_chandelier_giveback.py
git commit -m "feat: percent-of-peak giveback budget resolver for adaptive_trail"
```

---

### Task 2: Wire the dynamic budget into _evaluate_trail (both call sites)

**Files:**
- Modify: `timed_exit_monitor.py` — the `below_activation` branch (~line 1581) and the post-activation `giveback_r = _giveback_r_for(...)` line (~1634), both in `_evaluate_trail`
- Test: `tests/test_chandelier_giveback.py`

- [ ] **Step 1: Write the failing end-to-end tests**

Append to `tests/test_chandelier_giveback.py`:

```python
class TestPercentOfPeakGivebackClose:
    def setup_method(self):
        _clear_state()

    def _frac_cfg(self):
        return _cfg({
            "trail_giveback_frac_of_peak": 0.40,
            "trail_giveback_min_r": 0.30,
            # Fixed budgets stay 0 (disabled), as in live config.
            "trail_giveback_r": {"scalp": 0.0, "intraday": 0.0, "swing": 0.0},
            "trail_giveback_r_by_venue": {},
        })

    def test_closes_after_40pct_of_peak_given_back(self, monkeypatch):
        # Trail pinned far away so the close must come from the give-back path.
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = self._frac_cfg()
        row = {"pair": "EURUSD", "ticket": 10, "audit_id": "p1"}
        # Entry=100, SL=90 -> risk 10. Intraday activation 1.0R.

        # Peak +2.0R (price 120): budget = 0.4 * 2.0 = 0.8R.
        first = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 120.0, tcfg,
                                state_key="pp_long", venue="mt5")
        assert first["action"] == "ratchet"
        assert first["peak_r"] == pytest.approx(2.0)

        # Retreat to +1.4R (drop 0.6 < 0.8) -> still ratcheting.
        mid = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 114.0, tcfg,
                              state_key="pp_long", venue="mt5")
        assert mid["action"] == "ratchet"

        # Retreat to +1.1R (drop 0.9 >= 0.8) -> close, still well above BE.
        out = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 111.0, tcfg,
                              state_key="pp_long", venue="mt5")
        assert out["action"] == "close"
        assert out["reason"] == "peak_giveback"
        assert out["current_r"] == pytest.approx(1.1)
        assert out["current_r"] > 0

    def test_giveback_fires_below_activation_after_armed_peak(self, monkeypatch):
        # Scalp activation 0.7R; close level 0.7 - 0.30 = +0.4R sits BELOW the
        # activation gate, so this exercises the new below_activation branch.
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = self._frac_cfg()
        row = {"pair": "EURUSD", "ticket": 11, "audit_id": "p2"}
        # Peak +0.7R (price 107): frac budget 0.28 -> floored to 0.30.
        armed = _evaluate_trail(row, "scalp", "LONG", 100.0, 90.0, 107.0, tcfg,
                                state_key="pp_scalp", venue="mt5")
        assert armed["action"] == "ratchet"

        # Drop 0.25R (price 104.5, current +0.45R < activation): under budget,
        # no close — falls through to the below_activation watch.
        hold = _evaluate_trail(row, "scalp", "LONG", 100.0, 90.0, 104.5, tcfg,
                               state_key="pp_scalp", venue="mt5")
        assert hold["action"] == "none"

        # Drop 0.30R (price 104.0, current +0.40R) -> close at +0.4R (> BE).
        out = _evaluate_trail(row, "scalp", "LONG", 100.0, 90.0, 104.0, tcfg,
                              state_key="pp_scalp", venue="mt5")
        assert out["action"] == "close"
        assert out["reason"] == "peak_giveback"
        assert out["current_r"] == pytest.approx(0.40)
        assert out["current_r"] > 0

    def test_no_giveback_close_at_or_below_breakeven(self, monkeypatch):
        # Gap scenario: armed peak, then price gaps red between ticks. The
        # give-back close must NOT fire below BE — the (already BE'd/ratcheted)
        # broker SL owns that case.
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = self._frac_cfg()
        row = {"pair": "EURUSD", "ticket": 12, "audit_id": "p3"}
        _evaluate_trail(row, "scalp", "LONG", 100.0, 90.0, 107.0, tcfg,
                        state_key="pp_gap", venue="mt5")
        out = _evaluate_trail(row, "scalp", "LONG", 100.0, 90.0, 99.0, tcfg,
                              state_key="pp_gap", venue="mt5")
        assert out["action"] == "none"

    def test_short_side_percent_of_peak(self, monkeypatch):
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 200.0)
        tcfg = self._frac_cfg()
        row = {"pair": "BTCUSDT", "ticket": "pp4", "audit_id": "p4"}
        # SHORT entry=100, SL=110, risk 10. Peak +3.0R (price 70): budget 1.2R.
        _evaluate_trail(row, "intraday", "SHORT", 100.0, 110.0, 70.0, tcfg,
                        state_key="pp_short", venue="bybit")
        # Retreat to +1.7R (price 83, drop 1.3 >= 1.2) -> close.
        out = _evaluate_trail(row, "intraday", "SHORT", 100.0, 110.0, 83.0, tcfg,
                              state_key="pp_short", venue="bybit")
        assert out["action"] == "close"
        assert out["reason"] == "peak_giveback"

    def test_engine_d_pre_activation_only_unaffected(self, monkeypatch):
        # Engine D path: armed peak then retreat below activation must NOT
        # close via the new branch when trail_mode="pre_activation_only".
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = self._frac_cfg()
        row = {"pair": "EURUSD", "ticket": 13, "audit_id": "p5"}
        # Seed an armed peak as the full path would have.
        _peak_r_state["pp_d"] = 0.7
        out = _evaluate_trail(row, "scalp", "LONG", 100.0, 90.0, 104.0, tcfg,
                              state_key="pp_d", venue="mt5",
                              trail_mode="pre_activation_only")
        assert out["action"] == "none"

    def test_frac_zero_keeps_legacy_fixed_behavior(self, monkeypatch):
        # With frac 0 and fixed budgets present, behavior matches the existing
        # fixed give-back (mt5/intraday 0.35 from the base _cfg fixture).
        monkeypatch.setattr(tem, "_compute_chandelier_trail", lambda *a, **kw: 90.0)
        tcfg = _cfg({"trail_giveback_frac_of_peak": 0.0})
        row = {"pair": "EURUSD", "ticket": 14, "audit_id": "p6"}
        _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 120.0, tcfg,
                        state_key="pp_legacy", venue="mt5")
        out = _evaluate_trail(row, "intraday", "LONG", 100.0, 90.0, 116.0, tcfg,
                              state_key="pp_legacy", venue="mt5")
        assert out["action"] == "close"
        assert out["reason"] == "peak_giveback"
```

- [ ] **Step 2: Run tests to verify the new closes fail**

Run: `pytest tests/test_chandelier_giveback.py::TestPercentOfPeakGivebackClose -q`
Expected: `test_closes_after_40pct_of_peak_given_back`, `test_giveback_fires_below_activation_after_armed_peak`, `test_short_side_percent_of_peak` FAIL (no close — the fixed budget in `_frac_cfg` is 0 and the frac is not wired in yet). `test_no_giveback_close_at_or_below_breakeven`, `test_engine_d_pre_activation_only_unaffected`, `test_frac_zero_keeps_legacy_fixed_behavior` already pass.

- [ ] **Step 3: Implement both call sites**

3a. **Below-activation branch.** In `_evaluate_trail`, the block at ~line 1581 currently reads:

```python
    if current_r < activation_r:
        peak_watch = _peak_r_state.get(state_key)
        if current_r > 0:
            log.info(
                f"[TIMED_EXIT] below_activation watch: {pair} style={style} dir={direction} "
                f"current_r={current_r:.2f} peak_r={peak_watch} activation_r={activation_r:.2f} "
                f"mode={trail_mode}"
            )
        return {"action": "none", "reason": "below_activation", "current_r": current_r}
```

Replace with:

```python
    if current_r < activation_r:
        peak_watch = _peak_r_state.get(state_key)
        # Percent-of-peak give-back can owe a close BELOW the activation gate
        # (close level = peak - budget; e.g. scalp peak 0.7R closes at 0.4R).
        # Fire it here for trades whose persisted peak armed the give-back,
        # but never at/below breakeven — a gap into the red is the broker
        # SL's job (BE'd at breakeven_arm_r, ratcheted by the trail).
        if (
            trail_mode == "full"
            and peak_watch is not None
            and peak_watch >= activation_r
            and current_r > 0
        ):
            giveback_r = _giveback_budget_r_for(tcfg, style, venue, peak_watch)
            if giveback_r > 0 and (peak_watch - current_r) >= giveback_r:
                log.info(
                    f"[TIMED_EXIT] PEAK GIVE-BACK close (below activation): {pair} "
                    f"style={style} dir={direction} peak_r={peak_watch:.2f} "
                    f"current_r={current_r:.2f} giveback_r={giveback_r:.2f}"
                )
                return {
                    "action": "close",
                    "trail_level": _trail_state.get(state_key),
                    "current_r": current_r,
                    "peak_r": peak_watch,
                    "giveback_r": giveback_r,
                    "reason": "peak_giveback",
                }
        if current_r > 0:
            log.info(
                f"[TIMED_EXIT] below_activation watch: {pair} style={style} dir={direction} "
                f"current_r={current_r:.2f} peak_r={peak_watch} activation_r={activation_r:.2f} "
                f"mode={trail_mode}"
            )
        return {"action": "none", "reason": "below_activation", "current_r": current_r}
```

3b. **Post-activation call site.** At ~line 1634 (now shifted down by the insertion), replace:

```python
    giveback_r = _giveback_r_for(tcfg, style, venue)
```

with:

```python
    giveback_r = _giveback_budget_r_for(tcfg, style, venue, peak_r)
```

No other change — the close condition (`giveback_r > 0 and (peak_r - current_r) >= giveback_r and peak_r >= activation_r`), the `peak_giveback` close dict, and the `pre_activation_only` cap at ~line 1591 stay untouched.

- [ ] **Step 4: Run the full test file**

Run: `pytest tests/test_chandelier_giveback.py -q`
Expected: all tests pass. Existing fixed-budget tests still pass because their fixtures omit `trail_giveback_frac_of_peak` (parser defaults it to 0), so the resolver falls back to `_giveback_r_for`. The existing `test_giveback_closes_when_current_drops_below_peak` (peak 2.0 → 1.6, both above activation) is unaffected by the new below-activation branch.

- [ ] **Step 5: Commit**

```bash
git add timed_exit_monitor.py tests/test_chandelier_giveback.py
git commit -m "feat: close adaptive_trail trades on percent-of-peak giveback"
```

---

### Task 3: Enable in config.yaml

**Files:**
- Modify: `config.yaml` (`TIMED_EXIT` section, ~lines 4130-4148)

- [ ] **Step 1: Add the new keys and update the stale comment**

In `config.yaml`, the current block reads (~lines 4130-4148):

```yaml
  # Peak-R give-back close. Once chandelier is active, track the highest R
  # the trade reaches; if current R drops below peak by giveback_r, close
  # immediately. Independent of the trail-line breach.
  #
  # DISABLED (0 = off, 2026-06-10): the previous budgets (0.25-0.7R) were
  # ~4x tighter than the chandelier rope in R terms (e.g. MT5 intraday:
  # rope 2.2 ATR / risk 1.5 ATR ~= 1.47R vs giveback 0.35R), so the give-back
  # always fired first and the chandelier never managed the trade. The trail
  # is now the single profit-side mechanism. Re-enable only with budgets
  # calibrated from the live_exit backtest mode (post-peak retrace quantiles
  # of winners), and keep the budget >= the rope expressed in R.
  trail_giveback_r:
    scalp: 0.0
    intraday: 0.0
    swing: 0.0

  # Optional per-venue override for give-back. Disabled with the default
  # above — populate only alongside calibrated trail_giveback_r values.
  trail_giveback_r_by_venue: {}
```

Replace it with:

```yaml
  # Peak-R give-back close. Once chandelier is active, track the highest R
  # the trade reaches; if current R drops below peak by the budget, close
  # immediately. Independent of the trail-line breach.
  #
  # Fixed budgets DISABLED (0 = off, 2026-06-10): the previous fixed budgets
  # (0.25-0.7R) were ~4x tighter than the chandelier rope in R terms, so the
  # give-back always fired first and the chandelier never managed the trade.
  # Superseded by trail_giveback_frac_of_peak below (2026-06-10): the budget
  # now scales with the peak, so tolerance is wide early and locks in more as
  # the win grows. Keep these fixed values at 0 while the fraction is active.
  trail_giveback_r:
    scalp: 0.0
    intraday: 0.0
    swing: 0.0

  # Optional per-venue override for the fixed give-back. Disabled with the
  # default above — populate only alongside calibrated trail_giveback_r values.
  trail_giveback_r_by_venue: {}

  # Percent-of-peak give-back (active mechanism, 2026-06-10). Budget =
  # max(frac x peak_r, trail_giveback_min_r); overrides the fixed
  # trail_giveback_r when > 0. Examples at 0.40: peak +1.0R closes at +0.6R,
  # peak +3.0R closes at +1.8R. Arms once peak >= trail_activation_r and can
  # fire below the activation line (scalp worst close: +0.4R) but never at or
  # below breakeven. Defaults are sane-but-uncalibrated (user-approved
  # 2026-06-10); tune from live TRAIL_GIVEBACK exits. Parser clamps frac to
  # [0, 0.9].
  trail_giveback_frac_of_peak:
    scalp: 0.40
    intraday: 0.40
    swing: 0.40

  # Minimum give-back budget in R — stops noise-closes right after arming,
  # where frac x peak is smallest (scalp: 0.4 x 0.7R = 0.28R -> floored to 0.30R).
  trail_giveback_min_r: 0.30
```

- [ ] **Step 2: Verify config loads and resolves**

Run: `python -c "import yaml; c=yaml.safe_load(open('config.yaml',encoding='utf-8')); t=c['TIMED_EXIT']; print(t['trail_giveback_frac_of_peak'], t['trail_giveback_min_r'])"`
Expected: `{'scalp': 0.4, 'intraday': 0.4, 'swing': 0.4} 0.3`

- [ ] **Step 3: Final verification pass (one pytest file)**

Run: `pytest tests/test_chandelier_giveback.py -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add config.yaml
git commit -m "config: enable percent-of-peak giveback (0.40 frac, 0.30R floor)"
```

---

## Verification summary

- Only pytest file run: `tests/test_chandelier_giveback.py` (repo test budget).
- Not verified by tests (out of scope, unchanged code): broker close execution
  (`mt5_close_position`/`bybit_close_position`), Telegram notify, hybrid
  runner-leg close — all reached via the pre-existing `peak_giveback` close
  handler (`timed_exit_monitor.py:1916-1956`) that needs only
  `action="close"` + `reason="peak_giveback"`, both of which the new branch
  returns identically to the existing giveback close.
- Masked-test risk: none new — fixtures set frac explicitly per test, so
  fixed-budget tests cannot silently run under the fraction.
