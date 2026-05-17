# Engine B full-scan diagnostics patch report

## Goal

Expose a **report-only** per-row `engine_b_scan_gate_funnel` object during `run_full_scan` so operators can distinguish Engine A omission, TF/ATR pre-structure skips, RR/execution-level rejection, space gate, and final scan tier classification—without changing pass/fail logic, thresholds, execution, or AI.

## Constraints honored

- No threshold or strategy logic changes beyond reading existing gates into a dict.
- No execution-path edits.
- No AI edits.
- Skipped tiers are **not** promoted to trade; funnel only annotates outcomes.

## Code changes

| Area | Change |
|------|--------|
| `config.py` | `ENGINE_B_SCAN_GATE_FUNNEL_ENABLED` default **`True`**; added to tracked config-key list |
| `scanner.py` | `_scalar_float_gate`, `_attach_engine_b_scan_gate_funnel`, `_patch_engine_b_funnel_final_tier`; Engine B overlay records ATR attribution, candles TF availability, forex Asian early return, atr=0 skips, snapshots `conf/res` post-`analyze_structure`; final attach runs once per buffered row (plus Asian/service early returns / exception fallback); Tier loop calls `_patch_engine_b_funnel_final_tier`; skip rows optionally copy funnel |

## Operational notes

1. Disable payload via `ENGINE_B_SCAN_GATE_FUNNEL_ENABLED: false` (e.g. in `config.local.yaml`).
2. Pairs that return `sig_a is None` still appear under `skipped` with `"No data"` only—no funnel (no Engine A payload to attach).

## Verification

```bash
pytest tests/test_engine_b_full_scan_audit.py -q
```

Result: **7 passed** (local run).

See primary audit narrative: **`tasks/engine_b_full_scan_blocker_audit.md`**.
