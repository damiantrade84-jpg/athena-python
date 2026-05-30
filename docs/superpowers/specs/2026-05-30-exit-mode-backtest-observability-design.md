# Exit-Mode Backtest Observability (Plan 3) — Design Spec

Date: 2026-05-30
Branch: `exit-mode-selector`
Status: Approved for plan-writing

## Background

Plans 1–2 added the Engine-A exit-mode selector (`traditional_static` default,
`adaptive_trail`, `manual`, `time_based`) to live execution + the timed-exit
monitor; Plan 4 added the frontend (Exit Strategy tab + per-trade field). The
remaining item was "backtest parity (Plan 3)".

Exploration (2026-05-30) found the original premise — *"the `live_exit` sim in
`athena_research/metrics.py` must call the same `_engine_a_exit_dispatch`"* —
rests on a wrong mental model:

- The Research Lab backtest does **not** replay Engine-A live signals. It runs
  strategy *families* (`trend_momentum`, `engine_b_proxy`, `volatility`, …) and
  attributes each to an engine **post-hoc** in
  `athena_research/research_context.py::annotate_research_results` — *after* the
  portfolio simulation. At exit-simulation time the code does not know "this is
  engine_a, score_group=forex_majors".
- `backtest_exits.py::calculate_backtest_exit` only ever models **fixed SL/TP +
  timeout** (`atr_baseline` / `triple_barrier`). There is no trailing simulation
  anywhere in research, and never was. `_engine_a_exit_dispatch` is a live
  per-tick decision (`trail`/`hold`/`timed_close`) that produces no backtest
  prices.

Two consequences:

1. **The research backtest is already `traditional_static` by construction.** For
   the static modes, the exit *math* is already in parity — nothing to change there.
2. Faithfully simulating `adaptive_trail` would require building a chandelier /
   profit-protect simulator in research — a large, separate piece that introduces
   new simulated-outcome logic needing its own validation. **Out of scope**
   (user-declined).

## Goal & scope

Make Research Lab reports **show**, per Engine-A row, which exit mode the *live*
config would apply and whether the backtest faithfully represents it. This is an
**observability / transparency** layer only:

- **No exit-math change.** `backtest_exits.py` and the portfolio simulators are
  untouched.
- Engine A rows only. Engine B/C/D and `RESEARCH` rows get empty annotation fields.
- Stays inside the research module's isolation contract (`research_context.py`
  "must stay isolated from live execution and live engine imports"). The only new
  import is `exit_policy` — a pure module that imports nothing from the project.

Non-goals (YAGNI): trailing simulation; changing `time_based` max-hold in the
sim; coupling `backtest_exits.py` to live config/scoring; any change to live
execution, gates, scoring, or thresholds.

## Architecture (4 isolated pieces)

### 1. Pure parity label (`exit_policy.py`)

Add one pure helper:

```
exit_parity_label(mode) -> str
  traditional_static -> "faithful"          # research fixed SL/TP == live static bracket
  manual             -> "faithful"          # research uses ATR levels as a proxy for user levels
  time_based         -> "timeout_proxy"     # research times out, but at the research max-hold,
                                            #   not necessarily ENGINE_A_TIME_EXIT_BARS
  adaptive_trail     -> "trail_not_simulated"  # live trailing is not modeled in research
  (unknown/None)     -> ""
```

`resolve_exit_mode` / `group_default_for` already exist (Plan 1) and are reused.

### 2. Annotation (`research_context.py::annotate_research_results`)

For each enriched row whose resolved `engine == "ENGINE_A"`:

- Resolve the live mode: `exit_policy.resolve_exit_mode(per_trade=None,
  group_default=exit_policy.group_default_for(pair_group, group_map),
  global_default=global_default)` where `group_map` / `global_default` come from
  `cfg` (see Config source).
- Stamp `engine_a_exit_mode = <resolved>` and
  `engine_a_exit_parity = exit_policy.exit_parity_label(<resolved>)`.

Non-Engine-A rows: both fields stay `""`.

Note on group keys: research `pair_group` (e.g. `forex_majors`, `crypto_majors`,
`metals`) overlaps but is **not** 1:1 with live `ENGINE_A_KNOWN_SCORE_GROUPS`
(e.g. `crypto_btc`/`crypto_eth` vs research `crypto_majors`). When a `pair_group`
has no entry in `ENGINE_A_EXIT_MODE_BY_SCORE_GROUP`, resolution falls through to
the global default — the honest, safe behavior. (The shipped config has an empty
per-group map + global `traditional_static`, so every Engine-A row resolves to
`traditional_static` until the user sets per-group modes.)

### 3. New `StrategyMetrics` fields (`athena_research/metrics.py`)

Beside the existing `backtest_exit_mode`:

```python
engine_a_exit_mode: str = ""
engine_a_exit_parity: str = ""
```

### 4. Reporting (`athena_research/reporting.py`)

- Add `engine_a_exit_mode`, `engine_a_exit_parity` to the saved-column allowlist
  (the `_COLUMN`/metadata list near lines 36–42).
- Emit `by_engine_a_exit_mode.csv` (group-agg by `engine_a_exit_mode`) when the
  column is present, mirroring the existing `by_backtest_exit_mode.csv`.
- One-line note in the markdown summary: rows with
  `engine_a_exit_parity == "trail_not_simulated"` are a **static proxy** — the
  live `adaptive_trail` trailing/profit-protect is not modeled, so their backtest
  understates trail behavior.

## Config source

Research `cfg` does not carry the live exit-mode maps (they live in `config.yaml`).
To make reports reflect the Exit Strategy tab:

- `run_manager` reads the two keys **read-only** from `config.yaml` once at run
  start (pure `yaml.safe_load` — no live-engine import) and injects them into the
  research `cfg` under:
  - `cfg["engine_a_exit_mode_by_score_group"]` (default `{}`)
  - `cfg["engine_a_exit_mode_global_default"]` (default `"traditional_static"`)
- `annotate_research_results` reads those two `cfg` keys with the same safe
  defaults, so it stays a pure function of its inputs (no file/CONFIG access).

If the keys are absent (e.g. a research-only harness), everything resolves to
`traditional_static` — the documented live default.

## Testing (targeted only)

- `tests/test_exit_policy.py` (extend): `exit_parity_label` for all four modes +
  unknown/None.
- `tests/test_research_context.py` (new or extend): an `ENGINE_A` row resolves
  `engine_a_exit_mode`/`engine_a_exit_parity` from injected maps (global default,
  and a per-group override); a non-Engine-A row keeps both empty.
- `tests/` reporting: the new columns survive into the saved breakdown when present.

All pure; no live execution, gates, scoring, or thresholds touched.

## Safety & governance

- Observability only — cannot execute, size, gate, or mutate live config. AI plays
  no part.
- No scoring/threshold/floor/DI change → outside the calibration-evidence gate.
- Engine-A scoped; B/C/D/RESEARCH rows untouched. `adaptive_trail` honesty flag
  prevents misreading an un-simulated mode as faithful.
