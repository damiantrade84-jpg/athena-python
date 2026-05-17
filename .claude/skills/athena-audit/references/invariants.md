# Athena System Invariants

These invariants apply to every audit finding and every code change.

## Execution safety

- Paper-only unless the user explicitly approves live trading.
- Never bypass risk gates, freshness gates, kill switches, execution approval gates, broker safety checks, RR checks, SL/TP validation, audit logging, or deterministic safety rules.
- AI is advisory-only. AI review, Marcus, Vision, Strategist, AI Agent chat, and similar-setup logic cannot execute trades, approve orders, mutate config, or override deterministic gates.

## Engine separation

- Engine A and Engine B are independent signal engines. Do not let Engine A suppress Engine B or Engine B suppress Engine A unless an explicit config-gated design says so.
- Engine C owns A/B agreement, conflict, A-only, and B-only comparison.
- Engine D is separate scalp logic.

## Config discipline

- Threshold, scoring, and safety changes must be config-gated unless the user explicitly asks otherwise.
- Do not hardcode trading thresholds or safety gates in Python when a config path is expected.
- Live/backtest thresholds are not automatically interchangeable. Backtest/discovery gates are diagnostic unless the user explicitly asks to change live gates.

## Freshness and candle-state

- Freshness and candle-state policy must be traced by route: live scan, backtest, Engine A, Engine B, Engine C, Engine D, Vision, and execution may have different contracts.
