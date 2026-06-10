# Percent-of-Peak Giveback Close (adaptive_trail) — Design

**Date:** 2026-06-10
**Surface:** Engine A/B live trade management — `timed_exit_monitor.py` (`tp_mode: trailing_atr`)
**Status:** Approved by user 2026-06-10

## Problem

The adaptive_trail exit mode rides winners on a Chandelier ATR trail with no TP cap,
but the two "protect gains once the trade picks up" mechanisms were disabled on
2026-06-10 because they were miscalibrated:

- `pre_activation_profit_protect` closed every slightly-green trade at ~0R on noise.
- Fixed `trail_giveback_r` budgets (0.25–0.7R) were ~4× tighter than the chandelier
  rope (~1.5R in R terms), so the giveback always fired first and the trail never
  managed the trade.

The user wants: no TP cap, full retest/false-breakout room before the trade is in
profit, then a fast close once a profitable trade starts giving back its gains.

## Decisions (user-confirmed)

1. **Giveback model:** percent of peak (~40%), not a fixed R budget.
2. **Arm point:** existing `trail_activation_r` thresholds (scalp 0.7R / intraday
   1.0R / swing 1.5R). Below activation, only the original ATR stop applies.
3. **Initial SL:** unchanged — engine ATR stop is wide enough for retests.
4. **Hybrid scale-out:** stays ON (50% banks at TP1, runner trails uncapped).
5. **Calibration:** ship with defaults 0.40 / 0.30R floor; tune from live behavior.
   Not backtest-calibrated — accepted by user; the arithmetic bound (worst close =
   `activation_r − max(frac × activation_r, min_r)` = +0.40R for scalp) keeps every
   giveback close above breakeven.

## Mechanism

In `_evaluate_trail` (post-activation branch, where the fixed `trail_giveback_r`
check lives today), the giveback budget becomes dynamic:

```
frac     = trail_giveback_frac_of_peak (per style; 0 disables)
budget_r = max(frac × peak_r, trail_giveback_min_r)
close when: peak_r ≥ activation_r AND (peak_r − current_r) ≥ budget_r
```

- When `frac > 0` for the style, it **replaces** the fixed `trail_giveback_r`
  budget (which stays 0/disabled in config). When `frac == 0`, behavior is
  exactly today's (fixed budget, currently off).
- `trail_giveback_min_r` is a **minimum budget** (floor on tolerance, default
  0.30R). It prevents noise-closes immediately after arming — the failure mode
  that got the old fixed version disabled.
- Examples: peak +1.0R → close at +0.6R; peak +3.0R → close at +1.8R; scalp
  peak +0.7R → budget max(0.28, 0.30) = 0.30R → close at +0.4R.

### New config keys (`TIMED_EXIT` in config.yaml)

```yaml
trail_giveback_frac_of_peak:   # 0 = off; scalar or per-style dict
  scalp: 0.40
  intraday: 0.40
  swing: 0.40
trail_giveback_min_r: 0.30     # minimum giveback budget in R (floor)
```

Parsed in the monitor's config merge with the same scalar-or-dict handling as
`trail_giveback_r`. No per-venue override in v1 (YAGNI; add only if live tuning
demands it).

## Interaction with existing exits

- **Chandelier trail:** unchanged. It keeps ratcheting the broker SL (gap and
  monitor-downtime backstop). For typical winners the giveback fires before the
  trail line is hit (rope ≈ 1.5R vs 40%-of-peak, tighter until peak ≈ 3.7R).
  Giveback = fast profit-protect close; trail SL = hard backstop. They do not
  fight: both are close paths, first to trigger wins.
- **Close path:** reuses the existing `peak_giveback` reason →
  `TRAIL_GIVEBACK` close_reason, so audit logging, Telegram notify, and the
  hybrid runner-leg close work unchanged.
- **Engine D:** untouched. Its `trail_mode="pre_activation_only"` path returns
  before the post-activation giveback logic.
- **Pre-activation protect / stagnation exit / BE arming:** untouched.

## Files touched

- `timed_exit_monitor.py` — config merge (`_merge_cfg`) for the two new keys;
  `_evaluate_trail` budget computation (replace `_giveback_r_for` result with
  the dynamic budget when frac > 0). `_DEFAULT_CFG` gets the new keys with
  frac 0.0 (off by default in code; enabled via config.yaml).
- `config.yaml` — new keys under `TIMED_EXIT`, comment block updated (the
  "DISABLED (0 = off, 2026-06-10)" note on `trail_giveback_r` gains a pointer
  to the percent-of-peak replacement).
- `tests/test_chandelier_giveback.py` — extended: frac fires at 40% drawdown
  of peak, min_r floor applies near activation, frac=0 falls back to fixed
  behavior, close never below BE for armed trades.

## Testing

One pytest file per repo budget: `pytest tests/test_chandelier_giveback.py -q`
after implementation. New tests use `tempfile.mkdtemp` (not `tmp_path`) per the
known environment issue.

## Out of scope

- Backtest calibration of frac/floor (deferred; live_exit mode quantiles).
- Per-venue frac overrides.
- Any change to initial SL sizing, BE arming, activation thresholds, hybrid
  scale-out fraction, or Engine D.
