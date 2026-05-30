# Exit-Mode Selector (Engine A) — Design Spec

Date: 2026-05-29
Branch: `exit-mode-selector`
Status: Draft for user review

## Background

Recent commit `02f1cfa5` ("Fix Engine A stop-outs by flooring structural SL at
ATR and widening pre-activation giveback") made executed trades worse: winners
were round-tripping to scratch / small losses on small pullbacks.

Read-only diagnosis (`tools/diagnose_engine_a_stops.py`, 348 closed Engine A
trades in `audit.db`) showed the dominant loss/scratch mechanism is the
**pre-activation profit-protect "round-trip" close** in `timed_exit_monitor.py`,
not SL/TP geometry (median RR ratio vs ATR baseline = 1.00). Two triggers fire in
the pre-activation zone (before the chandelier trail arms at 0.7–1.5R):

- `roundtrip_close`: once a trade has been green, if R falls back to ≤ 0.0
  (break-even) it closes → scratch → small loss after fees.
- `giveback_close`: if peak ≥ `arm_r` and it gives back ≥ `giveback_r` while still
  positive, it closes locking a small profit.

Diagnosis: 62 round-trip/giveback closes, **peak median 0.28R**, realized median
0.13R (worst −0.73R). The commit *widened* `giveback_r` (e.g. swing 0.30→0.50) on
the assumption winners peak ~0.61R. Because real peaks are ~0.28R, the wider
budget stopped the early small-profit lock-in (`giveback_close`) from firing, so
modest-peak winners rode back to the break-even `roundtrip_close` (≈0.0R → small
loss after fees) instead of banking ~+0.08–0.15R.

Caveats (not fully verified): the 348 trades span history, not just post-commit;
candle replay covered only 8 old trades (candle cache stale to ~2026-04-03), so
the false-breakout confirmation is thin.

## Phase 1 — Stabilization (DONE, in working tree)

Surgical config revert in `config.yaml`, evidence-backed:

- `pre_activation_profit_giveback_r` reverted to pre-commit **0.15 / 0.20 / 0.30**.
- `pre_activation_profit_giveback_r_by_venue` reverted to **bybit 0.10/0.15/0.25,
  mt5 0.15/0.20/0.30**.
- `ENGINE_A_STRUCTURAL_SL_FLOOR_ATR` left **`true`** — the diagnosis shows it helps
  the pullback symptom (it only widens stops; turning it off makes stops tighter
  → more pullback stop-outs; 34% of stop-outs already had stops tighter than the
  ATR baseline, which this floor prevents).

Verified: `config.yaml` parses with the baseline values; `tests/test_timed_exit_phases.py`
→ 74 passed (4 "errors" are an unrelated Windows `.pytest_tmp` rmdir teardown quirk).
Not yet committed (awaiting user go-ahead on Phase-1 commit/placement).

## Phase 2 — Exit-Mode Selector

### Goal & scope

Give **Engine A** trades a selectable exit strategy instead of one hard-coded
management path, chosen at two levels: a **per-asset-group default** and a
**per-trade override**. Engine B/C/D execution and exit paths are untouched in
this phase (preserves engine separation). Backtest honors the same modes.

### The four modes

| Mode | SL/TP geometry | Post-entry management |
|------|----------------|------------------------|
| `traditional_static` *(new default for all Engine A groups)* | `calc_levels` ATR per group, then advisable-pip clamp | None. Fixed broker SL + TP. No profit-protect, no trail, no giveback. Runs to TP or SL. |
| `adaptive_trail` | `calc_levels` ATR per group | Today's exact behavior (chandelier + pre-activation profit-protect + giveback). Unchanged. |
| `manual` | User-entered SL/TP via existing `_apply_level_override` (validated SL<entry<TP) | Static (fixed bracket, **no trailing** in v1). |
| `time_based` | `calc_levels` ATR SL backstop + fixed TP (honored if hit first) | Close after **N bars** of the trade's style timeframe (configurable per style). |

Explicit decisions (previously open):
- `time_based` closes after **N bars of the style timeframe**, not a wall-clock
  duration — aligns with the existing bar/timeframe-oriented `TIMED_EXIT` machinery.
- `manual` is managed as **static (no trailing)** in v1. Trailing-on-manual is a
  possible later enhancement, out of scope here.

### Mode resolution & storage

Effective mode = **per-trade override → per-group default → global default**.

- Per-group defaults: new `config.yaml` map (Engine-A asset/score groups). **Ships
  = `traditional_static` for every group.** This is a deliberate, user-authorized,
  Engine-A-scoped change of the live default (see Governance). The old behavior is
  one selection away per group/trade.
- Per-trade override: carried in the execute payload (the same payload that already
  carries `level_override` / volume mode).
- Persistence: the resolved `exit_mode` is stored on the trade (new `audit_log`
  column) so `timed_exit_monitor.py` reads it deterministically across restarts.

### Advisable-pip guardrail

New per-group **min/max pip** map. After geometry resolves (any mode **except
`manual`** — see Open Q3), the SL distance is clamped to `[min_pip, max_pip]` for
the group and TP is re-derived to preserve the original RR. **Ships empty = no-op**; the user populates per-group
values in the Exit Strategy tab, so the clamp only activates where numbers are set
(avoids an ungated floor change). Runs **before** the existing `MAX_SL_PCT` /
`min_rr` / `min_room_atr` gates and never bypasses them.

### Architecture (single-source module)

New `exit_policy.py`, pure and unit-testable:

- `resolve_exit_mode(ctx) -> mode` — precedence per-trade → per-group → global.
- `clamp_to_advisable_pip(levels, group) -> levels` — min/max pip clamp + RR-preserving TP.
- mode constants + `validate_mode()`.

Consumers (Engine A path only):

- `execution.py` — static/manual/time: attach fixed broker TP; adaptive: today's
  path (which clears the broker TP to trail). Invoked only on the Engine A path.
- `timed_exit_monitor.py` — reads persisted `exit_mode`; `static`/`manual` →
  short-circuit to "no management"; `time_based` → timed close; `adaptive_trail` →
  current logic.
- `backtest_runner.py` — imports the same `exit_policy` so a group set to static
  backtests as static (live/backtest parity, per CLAUDE.md).

Exact integration hooks (where `execution.py` attaches/clears the broker TP, the
short-circuit point in `timed_exit_monitor.resolve_exit`, the Engine-A gating in
`execution.py`/`auto_trader.py`) will be traced producer-to-consumer during plan
writing before any edit; an `/athena-audit` pass precedes edits to the execution
files.

### Frontend

- **New "Exit Strategy" tab/panel** (React, under `static/react-app/app/src/components/panels/`):
  one row per Engine-A asset group → default-mode dropdown + advisable-pip min/max
  inputs. Persists via the existing config/settings API.
- **Per-trade `ExitModeField`** mirroring `VolumeModeField.tsx` (+ a `useExitModeState`
  hook mirroring `useExecutionVolumeState.ts`), shown only on **Engine-A** execute
  surfaces (Signals / TV Chart / relevant cockpit). `time_based` reveals a bars
  input; `manual` reveals the existing SL/TP inputs.

### Safety & governance (non-negotiable)

- Every mode still passes risk_engine, guardian, RR / `min_rr` / `min_room_atr`,
  broker SL/TP validation, freshness, and kill switch. The selector chooses
  **geometry + management only** — it cannot bypass a gate, cannot execute, and AI
  plays no part.
- The global default flip to `traditional_static` is an intentional, user-authorized,
  **Engine-A-scoped** gated change (per the calibration-governance rule that
  threshold/floor changes are gated behind closed-trade evidence). It is recorded
  here as deliberate, not silent. The advisable-pip clamp ships inert (empty) to
  avoid an ungated floor change.

### Testing (targeted only)

- `exit_policy`: resolution precedence; each mode's geometry; advisable-pip clamp +
  RR preservation; empty-map no-op.
- `timed_exit_monitor`: `static`/`manual` short-circuit (no profit-protect/trail);
  `adaptive_trail` unchanged (existing tests stay green).
- `execution`: static/manual/time attach a fixed broker TP; adaptive clears it.
- parity: backtest honors the selected mode.

### Out of scope (YAGNI)

No DB service layer, no Engine B/C/D wiring, no AI involvement, no exotic
geometries beyond the four modes, no trailing-on-manual.

## Open questions for user review

1. Per-group default map keyed by **score_group** (fine-grained, matches existing
   `ENGINE_A_SCORE_GROUP_*` config) vs. coarse **asset class** (forex/crypto/index/
   commodity/stock/etf)? Default assumption: score_group, to match existing config.
2. `time_based` seed values per style (tunable in the tab): proposed
   scalp = 12 bars, intraday = 18 bars, swing = 10 bars. Confirm or adjust.
3. Should `manual` mode bypass the advisable-pip clamp (you set exact levels) or
   still be clamped? Default assumption: manual is **not** clamped (your levels win,
   still subject to the hard safety gates).
