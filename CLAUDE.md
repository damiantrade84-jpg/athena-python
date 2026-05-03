---
description: alwaysApply: true
---

# Sentinel Pro v4 — Claude Brief

**Safety:** Paper only. Never bypass risk/freshness/kill-switch. AI cannot override gates. No real orders without 1 week clean paper + manual approval.

**Scoring:** Locked. Do not change Engine A/B/D thresholds unless user requests. No hardcode in Python — use `config.yaml`.

**Dev:** No guessing. All changes config-gated, default-safe, with tests. Never import `athena.py` in tests. SQLite: WAL mode, 15s timeout.

**AI:** Engine B AI review-only. Preserve exact vision footer tokens. Chart Vision and Lottery AI are separate — do not mix.

**Data:** Freshness gate mandatory. H4 offsets: Binance 0h, MT5 forex 2h, MT5 stocks 3h. D1 = UTC 00:00. MT5 → `fetch_mt5()`, EODHD volume-only for Engine D.
