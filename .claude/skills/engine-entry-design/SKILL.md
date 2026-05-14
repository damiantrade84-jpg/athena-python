---
name: engine-entry-design
disable-model-invocation: true
description: >
  Deprecated/manual-only historical Engine A structure-first entry redesign. Use only
  when explicitly invoked or when the user explicitly asks to revisit the old
  structure-first design.
---

# Engine A Structure-First Entry Model

Deprecated/manual only. This skill must not trigger for normal Engine A or Engine B work. It only applies if the user explicitly asks to revisit the old structure-first design.

Historical context: a prior design explored a structure-first entry model where Engine B structural confirmation was used alongside the existing Engine A score gate. Treat this as historical until re-verified against current code and config.

## Historical Design Contract

If the user explicitly asks to revisit this design, re-verify the current code before applying any of these ideas:

1. Engine A score gate: `final_score >= threshold`.
2. Engine B structural confirmation: BOS or CHoCH in the correct direction.
3. Structural recency: configurable lookback from the signal bar.
4. Direction agreement: Engine A direction agrees with Engine B BOS/CHoCH direction.

## Verification Checklist

- Confirm the current Engine A and Engine B paths are independent before changing coordination behavior.
- Confirm no forming-bar lookahead in any structure check.
- Confirm direction mapping between Engine A and Engine B is explicit and tested.
- Run only targeted tests covering the explicitly changed behavior.
