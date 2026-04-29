# Audit 8577d0 — Evidence Gathering Runbook

This runbook implements **§7. Suggested follow-up (read-only)** of
`plans/engine-d-scoring-ai-review-audit-8577d0.md`. The plan file is itself a
read-only audit; its concluding instruction is *"Only after these confirm the
bugs in this audit should any threshold or formula be touched."*

These tools answer the audit's open questions from real logs **without
touching live code, thresholds, or scoring formulas**. Run them against the
existing JSONL artefacts under `logs/`. Each script prints a human-readable
summary by default and accepts `--json` for machine-readable output.

## Scripts and their audit-plan section

| Script | Audit section | What it answers |
|---|---|---|
| `tools/audit_engine_d_funnel.py` | §7.1 | Top skip reasons, gate-result distribution, per-asset breakdown of `logs/scalp_audit/engine_d_funnel.jsonl`. Confirms whether the §1.4 / §1.5 expectations (CVD veto, VP-proximity-driven `rr_below_min`) match observed traffic. |
| `tools/audit_ai_review_engine_d_state.py` | §7.2 | Per `review_type`, the percentage of `ai_review_audit.jsonl` rows where `engine_d_state` is `None`. Audit §4.1 expects 100%. |
| `tools/audit_score_scale_split.py` | §7.3 | Empirical distributions for Engine A/B/C scores from `signal_funnel.jsonl` vs Engine D `setup_score` from `engine_d_funnel.jsonl`. Verifies the 0-3 vs 0-1 scale split flagged in §1.9 / §4.4. |
| `tools/audit_factor_diagnostics_addon.py` | §7.4 | Per asset class, share of rows with `factor_diagnostics.addon_unsupported=true`. Validates §2.3 + §4.6 (stocks/indices addon "unsupported"). |
| `tools/audit_btc_bias_multiplier.py` | §7.5 | Resolves the effective `BTC_BIAS_MULTIPLIERS.penalty/boost` from live config and prints the asymmetry expected per audit §2.2. Forward-compatible scan of `signal_funnel.jsonl` for `btc_bias_applied` / `btc_bias_delta` (currently not propagated). |

## Suggested order of operations

1. Run `audit_ai_review_engine_d_state.py` first — it answers the most
   straightforward audit claim (§4.1) and confirms whether AI review is blind
   to Engine D in practice.

2. Run `audit_engine_d_funnel.py` for a recent window (e.g. since the last
   live scalp scan) to see which skip reasons dominate.

3. Run `audit_score_scale_split.py` to confirm the score-scale gap. If the
   Engine A and Engine D normalised distributions both fall in 0-1 range but
   the *raw* scales differ, the §4.4 prompt-formatting risk is real.

4. Run `audit_factor_diagnostics_addon.py` to verify which classes hit the
   "addon unsupported" branch. (Note: `ADDON_UNSUPPORTED_SPLIT_TO_BASE` config
   already exists and partially mitigates §2.3 — the tool will surface the
   current effective behaviour.)

5. Run `audit_btc_bias_multiplier.py` last to see the current asymmetry vs
   the audit's expectation.

## Common usage

```bash
# Top skip reasons since this morning (UTC+02 = 2026-04-28T11:00 UTC):
python tools/audit_engine_d_funnel.py --since 2026-04-28T11:00:00+00:00

# Engine D blindness in AI reviews:
python tools/audit_ai_review_engine_d_state.py

# Score scale check, JSON output for piping:
python tools/audit_score_scale_split.py --json | jq .

# Drill into one symbol:
python tools/audit_engine_d_funnel.py --symbol BTC/USDT
python tools/audit_ai_review_engine_d_state.py --symbol EUR/USD
```

## What these tools deliberately do **not** do

- Modify thresholds (`MIN_GRADE`, `MIN_RR`, `MAX_SPREAD_*`, `MARKET_TICK_MAX_AGE_SEC`).
- Modify scoring weights or any function in `factor_scoring.py`, `scoring.py`,
  `engine_c.py`, or `scalp_engine.py`.
- Reissue or replay any live signals.
- Touch any AI-prompt template or AI-review pipeline.

The goal is evidence. The next decision (whether/which improvement-input lines
from the audit to action) is left to the user.
