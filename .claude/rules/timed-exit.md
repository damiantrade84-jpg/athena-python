---
paths:
  - "timed_exit_monitor.py"
---

# Timed Exit Monitor — Scoped Rules

**Mode dispatch (`TIMED_EXIT.tp_mode`):**
- `trailing_atr` (default): chandelier ATR trail only. Lock + timed-close branches suppressed via early-return.
- `fixed`: legacy lock + timed-close pipeline. Set to roll back without code changes.

**Defaults (locked unless user requests):**
- `intraday`/`swing` `timed_close_enabled: false`
- `scalp.profit_lock_enabled: false`
- `trail_indicator_confirm: true`
- `timer_tightens_trail: false`

**State machine:** `_evaluate_trail()` is single source of truth → `{action: none|ratchet|close}`.

**Persistence:** `timed_exit_state` SQLite (WAL, 15s). Key = `(venue, audit_id)`.

**Broker-enforced:** SL ratchets via `mt5_move_sl_to_breakeven` / `bybit_move_sl_to_breakeven`.
