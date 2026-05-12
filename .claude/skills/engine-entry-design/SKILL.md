---
name: engine-entry-design
description: >
  Active Engine A structure-first entry redesign for Athena. Use this skill when working
  on backtest_runner.py entry model changes, adding Engine B structural confirmation to
  Engine A, designing or reviewing the score-gate + structure-gate sequencing, evaluating
  whether an entry model has directional edge, or any task involving Engine A/B coordination
  at the signal generation or backtest level. Also trigger for "implement structure-first",
  "Engine A entry redesign", "BOS/CHoCH gate", or "Engine B confirmation".
---

# Engine A — Structure-First Entry Model

## Context

Engine A's scorer has been confirmed as statistically near-random for directional hit-rate.
The fix is a **structure-first entry model**: Engine B structural confirmation is required
alongside (not replaced by) the existing Engine A score gate.

## Design Contract

Signal must pass ALL of the following to be a valid entry candidate:

1. **Engine A score gate:** `final_score >= threshold` (existing, unchanged)
2. **Engine B structural confirmation:** BOS or CHoCH in the correct direction, validated by `market_structure.py` / `zone_registry.py`
3. **Structural recency:** BOS/CHoCH must be within N candles of signal bar (configurable, default: 5 candles)
4. **Direction agreement:** Engine A directional score direction must match Engine B BOS/CHoCH direction

## Implementation Target: `backtest_runner.py`

The structure gate must be added as a pre-filter in the backtest signal loop, before the score gate is evaluated:

```python
# Pseudocode — verify actual function signatures before implementing
for bar in candles:
    # Step 1: Engine B structural check
    structure_ok = check_structure_confirmation(bar, direction, lookback=5)
    if not structure_ok:
        continue  # fail closed — no structure = no trade
    
    # Step 2: Engine A score gate (existing)
    score = engine_a_score(bar)
    if score < threshold:
        continue
    
    # Record as valid signal
    signals.append(...)
```

## Config Gate

Add to `config.yaml` under `ENGINE_A`:
```yaml
ENGINE_A:
  structure_first_entry:
    enabled: true          # default true for new entries
    lookback_bars: 5       # how many bars back to accept a BOS/CHoCH
    require_bos: true      # BOS is sufficient
    require_choch: false   # CHoCH optional upgrade
```

## Verification Checklist

- [ ] Backtest with structure gate on: compare hit rate vs random baseline
- [ ] Backtest with structure gate off: reproduce near-random baseline
- [ ] Confirm no forming-bar lookahead in structure check
- [ ] Confirm structure check uses closed bars only
- [ ] Confirm direction mapping between Engine A `trend_score` direction and Engine B BOS direction is consistent
- [ ] Test with `require_bos: false, require_choch: false` → must produce same result as gate disabled
