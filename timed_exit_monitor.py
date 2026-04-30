"""
timed_exit_monitor.py — Hybrid Triple-Barrier Timed Exit (C-barrier)

Daemon thread that wakes every 30 s and applies timed exit logic to open positions.

Hybrid logic per style:
  SCALP   : staged profit-lock SL (trigger/lock tiers) or buffered BE after breakeven_min;
            optional timed close at close_min (config)
  INTRADAY: move SL to breakeven at 15 min → close at 30 min if still in profit
  SWING   : move SL to breakeven at 2.5 days → close at 5 days if in profit

All styles support tp_progress_exempt: skip timed close if price has covered
>= threshold fraction of entry→TP distance (letting TP manage the trade).

Phase 2 — Trailing ATR TP (Chandelier exit):
  When tp_mode="trailing_atr", after a trade reaches +trail_activation_r in profit,
  the timed-close path is replaced by a Chandelier-style ATR trail. Trade closes when
  price breaks below the trail level. SL stays ATR-based as always.

Phase 3 — Indicator confirmation:
  When trail_indicator_confirm=true, a trail-triggered close also requires RSI reversal
  and/or MACD histogram flip to confirm the direction change. Prevents whipsaws.

All windows are read from CONFIG["TIMED_EXIT"] — edit in config.yaml, not here.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger("timed_exit")

_be_done: set = set()
_trail_state: dict = {}
# Engine A/B style=scalp: last applied lock_r tier (0.0 = fallback BE only). Upgrades only.
_scalp_profit_lock_state: dict[str, float] = {}

_DEFAULT_CFG: dict = {
    "enabled": True,
    "check_interval_sec": 30,
    "breakeven_min_profit_r": 0.20,
    "breakeven_buffer_r": 0.05,
    "scalp":    {
        "breakeven_min": 5,
        "close_min": 10,
        "timed_close_enabled": True,
        "tp_progress_exempt": 0.40,
        "profit_lock_enabled": True,
        "profit_lock_stages": [
            {"trigger_r": 0.50, "lock_r": 0.10},
            {"trigger_r": 0.75, "lock_r": 0.25},
            {"trigger_r": 1.00, "lock_r": 0.50},
            {"trigger_r": 1.50, "lock_r": 1.00},
        ],
    },
    "intraday": {"breakeven_min": 15,  "close_min": 30, "timed_close_enabled": True, "tp_progress_exempt": 0.50},
    "swing":    {"breakeven_days": 2.5, "close_days": 5.0, "timed_close_enabled": True, "tp_progress_exempt": 0.50},
    "tp_mode": "fixed",
    "trail_activation_r": 1.0,
    "trail_atr_mult": {"scalp": 2.0, "intraday": 2.5, "swing": 3.0},
    "trail_lookback": 14,
    "trail_timeframe": {"scalp": "H1", "intraday": "H4", "swing": "D1"},
    "trail_indicator_confirm": False,
    "trail_confirm_rsi_threshold": 40,
    "trail_confirm_rsi_period": 14,
    "trail_confirm_macd": True,
}

_PROFIT_LOCK_STAGES_DEFAULT: tuple[dict, ...] = (
    {"trigger_r": 0.50, "lock_r": 0.10},
    {"trigger_r": 0.75, "lock_r": 0.25},
    {"trigger_r": 1.00, "lock_r": 0.50},
    {"trigger_r": 1.50, "lock_r": 1.00},
)


def _normalize_profit_lock_stages(raw) -> list[dict]:
    """Return sorted valid stages; empty/malformed input → defaults."""
    if not raw:
        return [dict(x) for x in _PROFIT_LOCK_STAGES_DEFAULT]
    out: list[dict] = []
    try:
        for item in raw:
            if not isinstance(item, dict):
                continue
            tr = item.get("trigger_r", item.get("trigger_R"))
            lr = item.get("lock_r", item.get("lock_R"))
            if tr is None or lr is None:
                continue
            out.append({"trigger_r": float(tr), "lock_r": float(lr)})
    except (TypeError, ValueError):
        return [dict(x) for x in _PROFIT_LOCK_STAGES_DEFAULT]
    out.sort(key=lambda s: (s["trigger_r"], s["lock_r"]))
    return out if out else [dict(x) for x in _PROFIT_LOCK_STAGES_DEFAULT]


def _current_r_multiple(
    entry: float, audit_sl: float, cur_price: float, direction: str
) -> float | None:
    sl_dist = abs(entry - audit_sl) if audit_sl > 0 else 0.0
    if sl_dist <= 0 or cur_price <= 0 or entry <= 0:
        return None
    d = str(direction).upper()
    if d == "LONG":
        return (cur_price - entry) / sl_dist
    if d == "SHORT":
        return (entry - cur_price) / sl_dist
    return None


def _best_eligible_lock_r(stages: list[dict], current_r: float) -> float | None:
    """Among stages satisfied by current_r, return maximum lock_r."""
    best: float | None = None
    for st in stages:
        try:
            tr = float(st["trigger_r"])
            lr = float(st["lock_r"])
        except (KeyError, TypeError, ValueError):
            continue
        if current_r + 1e-12 >= tr:
            best = lr if best is None else max(best, lr)
    return best


def _sl_for_locked_profit_r(
    entry: float, sl_dist: float, direction: str, lock_r: float
) -> float:
    """SL price locking lock_r × initial risk below/above breakeven (Fabio ladder)."""
    d = str(direction).upper()
    if d == "LONG":
        return entry + lock_r * sl_dist
    return entry - lock_r * sl_dist


def _protective_sl_tightens(
    direction: str, new_sl: float, ref_sl: float, entry: float
) -> bool:
    """True if new_sl is strictly tighter (never widens vs ref_sl). LONG: higher SL."""
    if new_sl <= 0 or entry <= 0:
        return False
    d = str(direction).upper()
    ref = float(ref_sl)
    if ref <= 0:
        # No live SL yet — sanity on correct side of entry
        return (new_sl > entry) if d == "LONG" else (new_sl < entry)
    if d == "LONG":
        return new_sl > ref
    return new_sl < ref


def _try_scalp_profit_lock_sl(
    *,
    ticket_key: str,
    risk_amount: float,
    scfg: dict,
    tcfg: dict,
    entry: float,
    cur_price: float,
    direction: str,
    profit: float,
    live_sl: float,
    audit_sl: float,
    be_due_now: bool,
    mins_open: float,
    pair_label: str,
    move_sl: Callable[[float], dict],
    telegram_notify,
) -> None:
    """Engine A/B style=scalp: staged profit-lock or buffered BE fallback.

    When ``profit_lock_enabled`` is false, callers use the legacy ``_be_done`` path.
    """
    if not be_due_now:
        return
    if not scfg.get("profit_lock_enabled", True):
        return

    _be_min_profit_r = float(tcfg.get("breakeven_min_profit_r", 0.20))
    _be_buffer_r = float(tcfg.get("breakeven_buffer_r", 0.05))
    _be_min_profit = (risk_amount * _be_min_profit_r) if risk_amount > 0 else 0.0

    if profit < max(0.01, _be_min_profit):
        return

    _sl_dist = abs(entry - audit_sl) if audit_sl > 0 else 0.0
    if _sl_dist <= 0:
        return

    stages = _normalize_profit_lock_stages(scfg.get("profit_lock_stages"))
    current_r = _current_r_multiple(entry, audit_sl, cur_price, direction)

    ref_sl = live_sl if live_sl > 0 else audit_sl
    last = _scalp_profit_lock_state.get(ticket_key)

    if _be_buffer_r > 0:
        buf = _sl_dist * _be_buffer_r
        is_long = str(direction).upper() != "SHORT"
        fallback_sl = entry + buf if is_long else entry - buf
    else:
        fallback_sl = entry

    target_sl: float | None = None
    record_lock: float | None = None
    log_mode = ""

    if current_r is not None:
        eligible = _best_eligible_lock_r(stages, current_r)
        if eligible is not None and (last is None or eligible > last + 1e-12):
            cand = _sl_for_locked_profit_r(entry, _sl_dist, direction, eligible)
            if _protective_sl_tightens(direction, cand, ref_sl, entry):
                target_sl = cand
                record_lock = eligible
                log_mode = "PROFIT_LOCK"

    if target_sl is None and last is None:
        if _protective_sl_tightens(direction, fallback_sl, ref_sl, entry):
            target_sl = fallback_sl
            record_lock = 0.0
            log_mode = "BE_FALLBACK"

    if target_sl is None:
        return

    result = move_sl(target_sl)
    if result.get("success") and not result.get("skipped"):
        _scalp_profit_lock_state[ticket_key] = float(record_lock if record_lock is not None else 0.0)
        cr_txt = f"{current_r:.2f}" if current_r is not None else "?"
        if log_mode == "PROFIT_LOCK":
            log.info(
                f"[TIMED_EXIT] PROFIT_LOCK: {pair_label} ticket={ticket_key} "
                f"mins_open={mins_open:.1f} current_r={cr_txt} lock_r={record_lock:.2f} "
                f"new_sl={target_sl:.5f} (entry={entry:.5f})"
            )
        else:
            log.info(
                f"[TIMED_EXIT] BE set: {pair_label} ticket={ticket_key} "
                f"style=scalp mins_open={mins_open:.1f} be_price={target_sl:.5f} "
                f"(entry={entry:.5f} buffer={_be_buffer_r:.0%} of SL dist fallback)"
            )
        try:
            telegram_notify._send_message_async(
                f"🔒 *Breakeven / profit-lock SL* — {pair_label}\n"
                f"Style: `scalp` | Open: `{mins_open:.0f} min` | "
                f"Profit: `${profit:.2f}`"
            )
        except Exception:
            pass


def _get_timed_cfg(config_fn) -> dict:
    cfg = config_fn() if config_fn else {}
    raw = cfg.get("TIMED_EXIT", {})
    merged: dict = {}
    for style in ("scalp", "intraday", "swing"):
        merged[style] = {**_DEFAULT_CFG[style], **(raw.get(style) or {})}
    merged["scalp"]["profit_lock_stages"] = _normalize_profit_lock_stages(
        merged["scalp"].get("profit_lock_stages")
    )
    merged["scalp"]["profit_lock_enabled"] = bool(merged["scalp"].get("profit_lock_enabled", True))
    merged["enabled"] = raw.get("enabled", True)
    merged["check_interval_sec"] = raw.get("check_interval_sec", 30)
    merged["breakeven_min_profit_r"] = float(
        raw.get("breakeven_min_profit_r", _DEFAULT_CFG["breakeven_min_profit_r"])
    )
    merged["breakeven_buffer_r"] = float(
        raw.get("breakeven_buffer_r", _DEFAULT_CFG["breakeven_buffer_r"])
    )
    merged["tp_mode"] = str(raw.get("tp_mode", _DEFAULT_CFG["tp_mode"])).lower()
    merged["trail_activation_r"] = float(raw.get("trail_activation_r", _DEFAULT_CFG["trail_activation_r"]))
    trail_mult_raw = raw.get("trail_atr_mult") or {}
    merged["trail_atr_mult"] = {
        s: float(trail_mult_raw.get(s, _DEFAULT_CFG["trail_atr_mult"][s]))
        for s in ("scalp", "intraday", "swing")
    }
    merged["trail_lookback"] = int(raw.get("trail_lookback", _DEFAULT_CFG["trail_lookback"]))
    trail_tf_raw = raw.get("trail_timeframe") or {}
    merged["trail_timeframe"] = {
        s: str(trail_tf_raw.get(s, _DEFAULT_CFG["trail_timeframe"][s])).upper()
        for s in ("scalp", "intraday", "swing")
    }
    merged["trail_indicator_confirm"] = bool(raw.get("trail_indicator_confirm", _DEFAULT_CFG["trail_indicator_confirm"]))
    merged["trail_confirm_rsi_threshold"] = int(raw.get("trail_confirm_rsi_threshold", _DEFAULT_CFG["trail_confirm_rsi_threshold"]))
    merged["trail_confirm_rsi_period"] = int(raw.get("trail_confirm_rsi_period", _DEFAULT_CFG["trail_confirm_rsi_period"]))
    merged["trail_confirm_macd"] = bool(raw.get("trail_confirm_macd", _DEFAULT_CFG["trail_confirm_macd"]))
    return merged


def _load_recent_audit_rows(db_path: str) -> list[dict]:
    """Load recent non-error audit rows for ticket and fallback position matching."""
    try:
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT id, ticket, pair, engine, style, ts, direction, entry_price, sl, tp,
                       tp_partial, volume, risk_amount, asset_class, exit_time
                FROM   audit_log
                WHERE  pair IS NOT NULL
                  AND  grade NOT LIKE '%ERR%'
                ORDER  BY ts DESC
                LIMIT  400
                """
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.debug(f"[TIMED_EXIT] audit read failed: {e}")
        return []


def _safe_float(value) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_iso_utc(ts_iso: str | None) -> datetime | None:
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
    except Exception:
        return None


def _match_audit_row_for_position(position: dict, audit_rows: list[dict]) -> dict | None:
    """Best-effort audit row match for a live position.

    Prefers exact ticket matches. Falls back to pair/direction/entry proximity so
    split MT5 legs and Bybit `ticket=0` positions still inherit the correct style.
    """
    pos_ticket = str(position.get("ticket", "")).strip()
    pos_pair = str(position.get("pair") or position.get("symbol") or "").upper()
    pos_dir = str(position.get("direction") or position.get("side") or "").upper()
    pos_entry = _safe_float(
        position.get("entry")
        or position.get("entryPrice")
        or position.get("price_open")
    )

    if pos_ticket and pos_ticket != "0":
        for row in audit_rows:
            if str(row.get("ticket", "")).strip() == pos_ticket and row.get("exit_time") is None:
                return row
        for row in audit_rows:
            if str(row.get("ticket", "")).strip() == pos_ticket:
                return row

    if not pos_pair or pos_entry <= 0:
        return None

    now_utc = datetime.now(timezone.utc)
    candidates: list[tuple[float, dict]] = []

    for row in audit_rows:
        row_pair = str(row.get("pair") or "").upper()
        if row_pair != pos_pair:
            continue

        row_dir = str(row.get("direction") or "").upper()
        if row_dir and pos_dir and row_dir != pos_dir:
            continue

        row_entry = _safe_float(row.get("entry_price"))
        if row_entry <= 0:
            continue

        rel_entry_diff = abs(row_entry - pos_entry) / max(abs(pos_entry), 1e-12)
        if rel_entry_diff > 0.01:
            continue

        row_ts = _parse_iso_utc(row.get("ts"))
        if row_ts is None:
            continue
        age_min = max(0.0, (now_utc - row_ts).total_seconds() / 60.0)
        if age_min > (7 * 24 * 60):
            continue

        # Prefer still-open rows, then the closest entry, then the most recent row.
        score = rel_entry_diff
        if row.get("exit_time") is not None:
            score += 0.25
        score += min(age_min, 24 * 60) / 100000.0
        candidates.append((score, row))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _row_for_live_position(position: dict, audit_rows: list[dict]) -> dict | None:
    audit = _match_audit_row_for_position(position, audit_rows)
    if not audit:
        return None
    return {
        "audit_id": audit.get("id"),
        "audit_ticket": audit.get("ticket"),
        "ticket": position.get("ticket"),
        "pair": audit.get("pair") or position.get("pair") or position.get("symbol"),
        "style": audit.get("style"),
        "engine": audit.get("engine"),
        "ts": audit.get("ts"),
        "direction": audit.get("direction") or position.get("direction"),
        "entry_price": audit.get("entry_price") or position.get("entry") or position.get("entryPrice"),
        "sl": audit.get("sl") or position.get("sl"),
        "tp": audit.get("tp") or position.get("tp"),
        "tp_partial": audit.get("tp_partial"),
        "volume": audit.get("volume") or position.get("volume"),
        "risk_amount": audit.get("risk_amount"),
        "asset_class": audit.get("asset_class"),
    }


def _mark_timed_close(db_path: str, row: dict, venue: str, *, actual_close_price: float | None = None, live_pnl: float | None = None) -> None:
    """Persist a timed-close marker so outcome logging preserves exit_reason.

    MT5: prefer exact-ticket match; fall back to id-based mark when the audit row was
    matched by proximity (split legs, fallback matching) to avoid silent no-ops.
    Bybit: always mark by id (live ticket is not stable on Bybit).
    
    When actual_close_price and live_pnl are provided (from timed exit), write them immediately
    to prevent the outcome monitor from overwriting with incorrect deal data.
    """
    audit_id = row.get("audit_id")
    audit_ticket = str(row.get("audit_ticket") or "").strip()
    live_ticket = str(row.get("ticket") or "").strip()
    r_multiple = None
    try:
        if live_pnl is not None and row.get("risk_amount") and float(row["risk_amount"]) > 0:
            r_multiple = round(float(live_pnl) / float(row["risk_amount"]), 2)
    except (TypeError, ValueError):
        r_multiple = None

    if venue == "mt5":
        if audit_ticket and audit_ticket == live_ticket:
            # Exact ticket match — most reliable
            if actual_close_price is not None and live_pnl is not None:
                # Write actual close price and PnL immediately
                query = "UPDATE audit_log SET exit_reason=?, exit_price=?, pnl=?, r_multiple=?, exit_time=? WHERE ticket=? AND exit_price IS NULL"
                params = (
                    "TIMED_CLOSE",
                    actual_close_price,
                    live_pnl,
                    r_multiple,
                    datetime.now(timezone.utc).isoformat(),
                    audit_ticket,
                )
            else:
                query = "UPDATE audit_log SET exit_reason=? WHERE ticket=? AND exit_price IS NULL"
                params = ("TIMED_CLOSE", audit_ticket)
        elif audit_id is not None:
            # Fallback: matched by proximity (split legs, etc.) — use stable row id
            if actual_close_price is not None and live_pnl is not None:
                query = "UPDATE audit_log SET exit_reason=?, exit_price=?, pnl=?, r_multiple=?, exit_time=? WHERE id=? AND exit_price IS NULL"
                params = (
                    "TIMED_CLOSE",
                    actual_close_price,
                    live_pnl,
                    r_multiple,
                    datetime.now(timezone.utc).isoformat(),
                    audit_id,
                )
            else:
                query = "UPDATE audit_log SET exit_reason=? WHERE id=? AND exit_price IS NULL"
                params = ("TIMED_CLOSE", audit_id)
        else:
            log.debug(f"[TIMED_EXIT] _mark_timed_close: no usable key for MT5 {row.get('pair')} — skipping")
            return
    else:
        if audit_id is None:
            log.debug(f"[TIMED_EXIT] _mark_timed_close: no audit_id for Bybit {row.get('pair')} — skipping")
            return
        query = "UPDATE audit_log SET exit_reason=? WHERE id=? AND exit_price IS NULL"
        params = ("TIMED_CLOSE", audit_id)

    try:
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.execute(query, params)
            con.commit()
    except Exception as e:
        log.debug(f"[TIMED_EXIT] failed to mark timed close for {row.get('pair')}: {e}")


def _minutes_open(open_ts_iso: str) -> float:
    """Return how many minutes ago the trade was opened."""
    try:
        opened = datetime.fromisoformat(open_ts_iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - opened
        return delta.total_seconds() / 60.0
    except Exception:
        return 0.0


def _tp_progress(current_price: float, entry: float, tp: float, direction: str) -> float:
    """Return 0.0–1.0 fraction of TP distance covered. Returns 0 on bad data."""
    try:
        if tp == 0 or entry == 0 or current_price == 0:
            return 0.0
        if direction == "LONG":
            total = tp - entry
            covered = current_price - entry
        else:
            total = entry - tp
            covered = entry - current_price
        if total <= 0:
            return 0.0
        return max(0.0, min(1.0, covered / total))
    except Exception:
        return 0.0


def _live_current_price(live: dict, fallback: float = 0.0) -> float:
    """Return live mark/current price from a normalized broker position."""
    for key in ("currentPrice", "markPrice", "lastPrice", "price_current", "price"):
        try:
            value = float(live.get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    try:
        return float(fallback or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _compute_chandelier_trail(
    pair: str, style: str, direction: str, entry: float, sl: float,
    tcfg: dict, ticket_key: str,
) -> float | None:
    """Compute Chandelier-style trailing stop from recent candle data.

    Returns the trail price level, or None if candle data is unavailable.
    Updates _trail_state[ticket_key] with the running best trail level
    (ratchets in the trade's favour; never widens).
    """
    try:
        from candles_cache import fetch_candles
        from indicators import calc_atr
    except ImportError:
        return None

    tf = tcfg["trail_timeframe"].get(style, "H4")
    lookback = tcfg["trail_lookback"]
    mult = tcfg["trail_atr_mult"].get(style, 2.5)

    try:
        candles = fetch_candles({"pair": pair, "source": "auto"}, tf, lookback + 20)
    except Exception:
        candles = None

    if not candles or len(candles) < lookback + 1:
        return None

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    atr_series = calc_atr(highs, lows, closes, 14)
    atr_val = next((v for v in reversed(atr_series) if v is not None), None)
    if atr_val is None or atr_val <= 0:
        return None

    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    highest = max(recent_highs)
    lowest = min(recent_lows)

    if direction == "LONG":
        raw_trail = highest - mult * atr_val
    else:
        raw_trail = lowest + mult * atr_val

    prev = _trail_state.get(ticket_key)
    if prev is not None:
        if direction == "LONG":
            raw_trail = max(raw_trail, prev)
        else:
            raw_trail = min(raw_trail, prev)

    _trail_state[ticket_key] = raw_trail
    return raw_trail


def _check_indicator_confirmation(
    pair: str, style: str, direction: str, tcfg: dict,
) -> bool:
    """Check RSI and/or MACD for direction-change confirmation.

    Returns True if indicators confirm the reversal (safe to close).
    Returns True on any data error (fail-open: allow close if we can't check).
    """
    if not tcfg.get("trail_indicator_confirm", False):
        return True

    try:
        from candles_cache import fetch_candles
        from indicators import calc_rsi, calc_macd
    except ImportError:
        return True

    tf = tcfg["trail_timeframe"].get(style, "H4")
    rsi_period = tcfg.get("trail_confirm_rsi_period", 14)
    rsi_thresh = tcfg.get("trail_confirm_rsi_threshold", 40)
    check_macd = tcfg.get("trail_confirm_macd", True)

    try:
        candles = fetch_candles({"pair": pair, "source": "auto"}, tf, max(rsi_period + 30, 60))
    except Exception:
        return True

    if not candles or len(candles) < rsi_period + 5:
        return True

    closes = [float(c[4]) for c in candles]

    rsi_series = calc_rsi(closes, rsi_period)
    rsi_val = next((v for v in reversed(rsi_series) if v is not None), None)
    if rsi_val is None:
        return True

    rsi_confirms = False
    if direction == "LONG" and rsi_val < rsi_thresh:
        rsi_confirms = True
    elif direction == "SHORT" and rsi_val > (100 - rsi_thresh):
        rsi_confirms = True

    if not rsi_confirms:
        return False

    if not check_macd:
        return True

    macd_data = calc_macd(closes)
    hist = macd_data.get("hist", [])
    recent_hist = [v for v in hist[-3:] if v is not None]
    if len(recent_hist) < 2:
        return True

    if direction == "LONG" and recent_hist[-1] < 0 and recent_hist[-2] >= 0:
        return True
    if direction == "SHORT" and recent_hist[-1] > 0 and recent_hist[-2] <= 0:
        return True

    if direction == "LONG" and recent_hist[-1] < 0:
        return True
    if direction == "SHORT" and recent_hist[-1] > 0:
        return True

    return False


def _should_trail_close(
    row: dict, style: str, direction: str, entry: float, sl: float,
    cur_price: float, tcfg: dict,
) -> bool:
    """Evaluate whether a trailing ATR TP close should fire.

    Returns True if price has broken the Chandelier trail AND indicator
    confirmation passes (when enabled). Returns False otherwise.
    """
    _risk_dist = abs(entry - sl) if sl > 0 else 0.0
    if _risk_dist <= 0:
        return False

    if direction == "LONG":
        current_r = (cur_price - entry) / _risk_dist
    else:
        current_r = (entry - cur_price) / _risk_dist

    activation_r = tcfg.get("trail_activation_r", 1.0)
    if current_r < activation_r * 0.5:
        return False

    pair = row.get("pair") or ""
    ticket_key = f"{row.get('ticket')}_{pair}"

    trail_level = _compute_chandelier_trail(
        pair, style, direction, entry, sl, tcfg, ticket_key,
    )
    if trail_level is None:
        return False

    breached = False
    if direction == "LONG" and cur_price < trail_level:
        breached = True
    elif direction == "SHORT" and cur_price > trail_level:
        breached = True

    if not breached:
        return False

    if not _check_indicator_confirmation(pair, style, direction, tcfg):
        log.debug(
            f"[TIMED_EXIT] Trail breached but indicator confirmation failed: {pair} "
            f"style={style} trail={trail_level:.5f} price={cur_price:.5f}"
        )
        return False

    log.info(
        f"[TIMED_EXIT] TRAIL CLOSE signal: {pair} style={style} direction={direction} "
        f"trail={trail_level:.5f} price={cur_price:.5f} R={current_r:.2f}"
    )
    return True


def _handle_mt5_row(row: dict, tcfg: dict, db_path: str | None = None) -> None:
    """Apply timed exit logic to a single MT5 trade row."""
    try:
        from mt5_executor import (
            mt5_close_position,
            mt5_move_sl_to_breakeven,
            mt5_get_positions,
        )
        import telegram_notify
    except ImportError as e:
        log.debug(f"[TIMED_EXIT] MT5 import error: {e}")
        return

    ticket = int(row["ticket"])
    engine = str(row.get("engine") or "").lower()
    style  = (row.get("style") or "intraday").lower()
    entry  = float(row.get("entry_price") or 0)

    # Engine D / Scalp Lab trades use broker TP1/SL management.
    # Do not treat generic Engine A/B "style=scalp" trades as Engine D.
    if engine in ("scalp", "engine d", "scalp_vp"):
        return

    if style not in ("scalp", "intraday", "swing"):
        style = "intraday"

    scfg = tcfg[style]
    mins = _minutes_open(row["ts"])

    # Get live position data
    pos_result = mt5_get_positions()
    if pos_result.get("error"):
        return
    live = next(
        (p for p in pos_result.get("positions", []) if p.get("ticket") == ticket),
        None,
    )
    if not live:
        return  # position already closed

    profit    = float(live.get("profit", 0))
    cur_price = _live_current_price(live, entry)
    tp        = float(row.get("tp") or live.get("tp") or 0)
    direction = live.get("direction", row.get("direction", "LONG"))

    if style == "swing":
        be_trigger_min  = scfg["breakeven_days"] * 24 * 60
        close_trigger_min = scfg["close_days"] * 24 * 60
    else:
        be_trigger_min    = float(scfg["breakeven_min"])
        close_trigger_min = float(scfg["close_min"])

    exempt_threshold = float(scfg.get("tp_progress_exempt", 1.0))
    timed_close_enabled = scfg.get("timed_close_enabled", True)

    close_due_now = mins >= close_trigger_min
    be_due_now = mins >= be_trigger_min

    _sl_raw = float(row.get("sl") or 0)

    # ── Phase 2: Trailing ATR TP ──────────────────────────────────────────────
    if tcfg.get("tp_mode") == "trailing_atr" and profit > 0:
        if _should_trail_close(row, style, direction, entry, _sl_raw, cur_price, tcfg):
            result = mt5_close_position(ticket)
            if result.get("success"):
                actual_close_price = result.get("closePrice")
                live_pnl = result.get("liveProfit", 0.0)
                pnl_r = 0.0
                if live_pnl != 0 and row.get("risk_amount") and row["risk_amount"] > 0:
                    pnl_r = round(live_pnl / float(row["risk_amount"]), 2)
                if db_path:
                    _mark_timed_close(db_path, row, "mt5", actual_close_price=actual_close_price, live_pnl=live_pnl)
                log.info(
                    f"[TIMED_EXIT] TRAIL CLOSE: {row['pair']} ticket={ticket} "
                    f"style={style} mins={mins:.0f} profit=${live_pnl:.2f} R={pnl_r}"
                )
                try:
                    telegram_notify.notify_trade_closed(pair=row["pair"], pnl_r=pnl_r, is_win=live_pnl > 0, duration_minutes=mins)
                except Exception:
                    pass
                return

    # ── Degenerate close-before-BE path (scalp/intraday with close_min <= be_min)
    if (
        style != "swing"
        and timed_close_enabled
        and close_due_now
        and be_due_now
        and close_trigger_min <= be_trigger_min
        and profit > 0
    ):
        progress = _tp_progress(cur_price, entry, tp, direction)
        if progress >= exempt_threshold:
            log.info(
                f"[TIMED_EXIT] {style.upper()} EXEMPT: {row['pair']} ticket={ticket} "
                f"TP progress={progress:.0%} >= {exempt_threshold:.0%} — letting TP manage"
            )
        else:
            result = mt5_close_position(ticket)
            if result.get("success"):
                actual_close_price = result.get("closePrice")
                live_pnl = result.get("liveProfit", 0.0)
                entry_price = result.get("entryPrice")
                pnl_r = 0.0
                if live_pnl != 0 and row.get("risk_amount") and row["risk_amount"] > 0:
                    pnl_r = round(live_pnl / float(row["risk_amount"]), 2)
                if db_path:
                    _mark_timed_close(db_path, row, "mt5", actual_close_price=actual_close_price, live_pnl=live_pnl)
                log.info(
                    f"[TIMED_EXIT] TIMED CLOSE: {row['pair']} ticket={ticket} "
                    f"style={style} mins={mins:.0f} profit=${live_pnl:.2f} R={pnl_r} "
                    f"fill={actual_close_price} entry={entry_price}"
                )
                try:
                    telegram_notify.notify_trade_closed(pair=row["pair"], pnl_r=pnl_r, is_win=live_pnl > 0, duration_minutes=mins)
                except Exception:
                    pass
                return
            log.warning(
                f"[TIMED_EXIT] Immediate close failed before BE: {row['pair']} ticket={ticket} "
                f"— {result.get('error')}"
            )

    # ── Step 1: Breakeven / scalp profit-lock trigger ─────────────────────────
    _risk_amount = float(row.get("risk_amount") or 0)
    _live_sl = _safe_float(live.get("sl"))
    _be_min_profit_r = tcfg.get("breakeven_min_profit_r", 0.20)
    _be_buffer_r = tcfg.get("breakeven_buffer_r", 0.05)

    use_scalp_lock = style == "scalp" and scfg.get("profit_lock_enabled", True)

    if use_scalp_lock:
        _try_scalp_profit_lock_sl(
            ticket_key=str(ticket),
            risk_amount=_risk_amount,
            scfg=scfg,
            tcfg=tcfg,
            entry=entry,
            cur_price=cur_price,
            direction=str(direction),
            profit=profit,
            live_sl=_live_sl,
            audit_sl=_sl_raw,
            be_due_now=be_due_now,
            mins_open=mins,
            pair_label=str(row.get("pair") or ""),
            move_sl=lambda sl_px: mt5_move_sl_to_breakeven(ticket, sl_px),
            telegram_notify=telegram_notify,
        )
    elif be_due_now and ticket not in _be_done and profit >= max(
        0.01,
        ((_risk_amount * _be_min_profit_r) if _risk_amount > 0 else 0.0),
    ):
        _sl_dist_legacy = abs(entry - _sl_raw) if _sl_raw > 0 else 0.0
        if _sl_dist_legacy > 0 and _be_buffer_r > 0:
            _be_buffer = _sl_dist_legacy * _be_buffer_r
            _direction_long = str(direction).upper() != "SHORT"
            _be_price = entry + _be_buffer if _direction_long else entry - _be_buffer
        else:
            _be_price = entry

        result = mt5_move_sl_to_breakeven(ticket, _be_price)
        if result.get("success") and not result.get("skipped"):
            _be_done.add(ticket)
            log.info(
                f"[TIMED_EXIT] BE set: {row['pair']} ticket={ticket} "
                f"style={style} mins_open={mins:.1f} be_price={_be_price:.5f} "
                f"(entry={entry:.5f} buffer={_be_buffer_r:.0%} of SL dist)"
            )
            try:
                telegram_notify._send_message_async(
                    f"🔒 *Breakeven SL set* — {row['pair']}\n"
                    f"Style: `{style}` | Open: `{mins:.0f} min` | "
                    f"Profit: `${profit:.2f}`"
                )
            except Exception:
                pass

    # ── Step 2: Close trigger ─────────────────────────────────────────────────
    if not timed_close_enabled:
        return

    if close_due_now:
        if profit <= 0:
            log.debug(
                f"[TIMED_EXIT] {row['pair']} ticket={ticket} at {mins:.0f}min "
                f"— not in profit (${profit:.2f}), SL protecting"
            )
            return

        progress = _tp_progress(cur_price, entry, tp, direction)
        if progress >= exempt_threshold:
            log.info(
                f"[TIMED_EXIT] {style.upper()} EXEMPT: {row['pair']} ticket={ticket} "
                f"TP progress={progress:.0%} >= {exempt_threshold:.0%} — letting TP manage"
            )
            return

        result = mt5_close_position(ticket)
        if result.get("success"):
            actual_close_price = result.get("closePrice")
            live_pnl = result.get("liveProfit", 0.0)
            entry_price = result.get("entryPrice")
            pnl_r = 0.0
            if live_pnl != 0 and row.get("risk_amount") and row["risk_amount"] > 0:
                pnl_r = round(live_pnl / float(row["risk_amount"]), 2)
            elif live_pnl != 0 and entry_price and row.get("sl"):
                risk_dist = abs(float(entry_price) - float(row["sl"]))
                if risk_dist > 0:
                    pnl_r = round(live_pnl / (risk_dist * float(row.get("volume", 1))), 2)
            if db_path:
                _mark_timed_close(db_path, row, "mt5", actual_close_price=actual_close_price, live_pnl=live_pnl)
            log.info(
                f"[TIMED_EXIT] TIMED CLOSE: {row['pair']} ticket={ticket} "
                f"style={style} mins={mins:.0f} profit=${live_pnl:.2f} R={pnl_r} "
                f"fill={actual_close_price} entry={entry_price}"
            )
            try:
                telegram_notify.notify_trade_closed(pair=row["pair"], pnl_r=pnl_r, is_win=live_pnl > 0, duration_minutes=mins)
            except Exception:
                pass
        else:
            log.warning(
                f"[TIMED_EXIT] Close failed: {row['pair']} ticket={ticket} "
                f"— {result.get('error')}"
            )


def _handle_bybit_row(row: dict, tcfg: dict, db_path: str | None = None) -> None:
    """Apply timed exit logic to a single Bybit trade row."""
    try:
        from bybit_executor import (
            bybit_close_position,
            bybit_move_sl_to_breakeven,
            bybit_map_symbol,
            bybit_get_positions,
        )
        import telegram_notify
    except ImportError as e:
        log.debug(f"[TIMED_EXIT] Bybit import error: {e}")
        return

    ticket    = str(row["ticket"])
    pair      = row["pair"]
    engine    = str(row.get("engine") or "").lower()
    style     = (row.get("style") or "intraday").lower()
    entry     = float(row.get("entry_price") or 0)

    # Engine D / Scalp Lab trades use broker TP1/SL management.
    # Do not treat generic Engine A/B "style=scalp" trades as Engine D.
    if engine in ("scalp", "engine d", "scalp_vp"):
        return

    if style not in ("scalp", "intraday", "swing"):
        style = "intraday"

    scfg = tcfg[style]
    mins = _minutes_open(row["ts"])

    # Get live position data
    pos_result = bybit_get_positions()
    if pos_result.get("error"):
        return
    live = next(
        (p for p in pos_result.get("positions", [])
         if p.get("pair", "").upper() == pair.upper()),
        None,
    )
    if not live:
        return  # already closed

    profit    = float(live.get("profit", 0))
    cur_price = _live_current_price(live, entry)
    tp        = float(row.get("tp") or live.get("tp") or 0)
    direction = live.get("direction", row.get("direction", "LONG"))
    volume    = float(live.get("volume", 0))

    if style == "swing":
        be_trigger_min    = scfg["breakeven_days"] * 24 * 60
        close_trigger_min = scfg["close_days"] * 24 * 60
    else:
        be_trigger_min    = float(scfg["breakeven_min"])
        close_trigger_min = float(scfg["close_min"])

    exempt_threshold = float(scfg.get("tp_progress_exempt", 1.0))
    timed_close_enabled = scfg.get("timed_close_enabled", True)

    ccxt_sym = bybit_map_symbol(pair)

    close_due_now = mins >= close_trigger_min
    be_due_now = mins >= be_trigger_min

    _sl_raw = float(row.get("sl") or 0)

    # ── Phase 2: Trailing ATR TP ──────────────────────────────────────────────
    if tcfg.get("tp_mode") == "trailing_atr" and profit > 0:
        if _should_trail_close(row, style, direction, entry, _sl_raw, cur_price, tcfg):
            result = bybit_close_position(pair, direction, volume)
            if result.get("success"):
                if db_path:
                    _mark_timed_close(db_path, row, "bybit")
                log.info(
                    f"[TIMED_EXIT] TRAIL CLOSE: {pair} style={style} "
                    f"mins={mins:.0f} profit=${profit:.2f}"
                )
                try:
                    telegram_notify.notify_trade_closed(pair=pair, pnl_r=0.0, is_win=True, duration_minutes=mins)
                except Exception:
                    pass
                return

    # ── Degenerate close-before-BE path (scalp/intraday with close_min <= be_min)
    if (
        style != "swing"
        and timed_close_enabled
        and close_due_now
        and be_due_now
        and close_trigger_min <= be_trigger_min
        and profit > 0
    ):
        progress = _tp_progress(cur_price, entry, tp, direction)
        if progress >= exempt_threshold:
            log.info(
                f"[TIMED_EXIT] {style.upper()} EXEMPT: {pair} "
                f"TP progress={progress:.0%} >= {exempt_threshold:.0%} — letting TP manage"
            )
        else:
            result = bybit_close_position(pair, direction, volume)
            if result.get("success"):
                if db_path:
                    _mark_timed_close(db_path, row, "bybit")
                log.info(
                    f"[TIMED_EXIT] TIMED CLOSE: {pair} style={style} "
                    f"mins={mins:.0f} profit=${profit:.2f}"
                )
                try:
                    telegram_notify.notify_trade_closed(pair=pair, pnl_r=0, is_win=profit > 0, duration_minutes=mins)
                except Exception:
                    pass
                return
            log.warning(
                f"[TIMED_EXIT] Immediate close failed before BE: {pair} — {result.get('error')}"
            )

    # ── Step 1: Breakeven / scalp profit-lock trigger ─────────────────────────
    _risk_amount = float(row.get("risk_amount") or 0)
    _live_sl = _safe_float(live.get("sl"))
    _be_min_profit_r = tcfg.get("breakeven_min_profit_r", 0.20)
    _be_buffer_r = tcfg.get("breakeven_buffer_r", 0.05)

    use_scalp_lock = style == "scalp" and scfg.get("profit_lock_enabled", True)

    if use_scalp_lock and ccxt_sym:
        _try_scalp_profit_lock_sl(
            ticket_key=ticket,
            risk_amount=_risk_amount,
            scfg=scfg,
            tcfg=tcfg,
            entry=entry,
            cur_price=cur_price,
            direction=str(direction),
            profit=profit,
            live_sl=_live_sl,
            audit_sl=_sl_raw,
            be_due_now=be_due_now,
            mins_open=mins,
            pair_label=str(pair),
            move_sl=lambda sl_px: bybit_move_sl_to_breakeven(ccxt_sym, direction, sl_px, volume),
            telegram_notify=telegram_notify,
        )
    elif be_due_now and ticket not in _be_done and profit >= max(
        0.01,
        ((_risk_amount * _be_min_profit_r) if _risk_amount > 0 else 0.0),
    ) and ccxt_sym:
        _sl_dist_legacy = abs(entry - _sl_raw) if _sl_raw > 0 else 0.0
        if _sl_dist_legacy > 0 and _be_buffer_r > 0:
            _be_buffer = _sl_dist_legacy * _be_buffer_r
            _direction_long = str(direction).upper() != "SHORT"
            _be_price = entry + _be_buffer if _direction_long else entry - _be_buffer
        else:
            _be_price = entry

        result = bybit_move_sl_to_breakeven(ccxt_sym, direction, _be_price, volume)
        if result.get("success"):
            _be_done.add(ticket)
            log.info(
                f"[TIMED_EXIT] BE set: {pair} style={style} mins_open={mins:.1f} "
                f"be_price={_be_price:.5f} (entry={entry:.5f} buffer={_be_buffer_r:.0%} of SL dist)"
            )
            try:
                telegram_notify._send_message_async(
                    f"🔒 *Breakeven SL set* — {pair}\n"
                    f"Style: `{style}` | Open: `{mins:.0f} min` | "
                    f"Profit: `${profit:.2f}`"
                )
            except Exception:
                pass

    # ── Step 2: Close trigger ─────────────────────────────────────────────────
    if not timed_close_enabled:
        return

    if close_due_now:
        if profit <= 0:
            log.debug(
                f"[TIMED_EXIT] {pair} at {mins:.0f}min — not in profit (${profit:.2f}), SL protecting"
            )
            return

        progress = _tp_progress(cur_price, entry, tp, direction)
        if progress >= exempt_threshold:
            log.info(
                f"[TIMED_EXIT] {style.upper()} EXEMPT: {pair} "
                f"TP progress={progress:.0%} >= {exempt_threshold:.0%} — letting TP manage"
            )
            return

        result = bybit_close_position(pair, direction, volume)
        if result.get("success"):
            if db_path:
                _mark_timed_close(db_path, row, "bybit")
            log.info(
                f"[TIMED_EXIT] TIMED CLOSE: {pair} style={style} "
                f"mins={mins:.0f} profit=${profit:.2f}"
            )
            try:
                telegram_notify.notify_trade_closed(pair=pair, pnl_r=0.0, is_win=True, duration_minutes=mins)
            except Exception:
                pass
        else:
            log.warning(
                f"[TIMED_EXIT] Close failed: {pair} — {result.get('error')}"
            )


def _run_check(db_path: str, config_fn) -> None:
    """Single check cycle — called every interval by the monitor thread."""
    tcfg = _get_timed_cfg(config_fn)
    if not tcfg.get("enabled", True):
        return

    audit_rows = _load_recent_audit_rows(db_path)
    if not audit_rows:
        return

    try:
        from mt5_executor import mt5_get_positions
        mt5_result = mt5_get_positions()
        mt5_positions = [] if mt5_result.get("error") else mt5_result.get("positions", [])
    except Exception as e:
        log.debug(f"[TIMED_EXIT] mt5 live fetch failed: {e}")
        mt5_positions = []

    for pos in mt5_positions:
        row = _row_for_live_position(pos, audit_rows)
        if not row:
            continue
        try:
            _handle_mt5_row(row, tcfg, db_path)
        except Exception as e:
            log.debug(f"[TIMED_EXIT] mt5 row error pair={row.get('pair')}: {e}")

    try:
        from bybit_executor import bybit_get_positions
        bybit_result = bybit_get_positions()
        bybit_positions = [] if bybit_result.get("error") else bybit_result.get("positions", [])
    except Exception as e:
        log.debug(f"[TIMED_EXIT] bybit live fetch failed: {e}")
        bybit_positions = []

    for pos in bybit_positions:
        row = _row_for_live_position(pos, audit_rows)
        if not row:
            continue
        try:
            _handle_bybit_row(row, tcfg, db_path)
        except Exception as e:
            log.debug(f"[TIMED_EXIT] bybit row error pair={row.get('pair')}: {e}")


class TimedExitMonitor:
    """Daemon thread that runs the timed exit check every N seconds."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._db_path: str = ""
        self._config_fn = None

    def start(self, db_path: str, config_fn) -> None:
        self._db_path  = db_path
        self._config_fn = config_fn
        self._thread   = threading.Thread(
            target=self._loop, name="TimedExitMonitor", daemon=True
        )
        self._thread.start()
        log.info("[TIMED_EXIT] Monitor thread started")

    def _loop(self) -> None:
        while True:
            try:
                tcfg = _get_timed_cfg(self._config_fn)
                interval = int(tcfg.get("check_interval_sec", 30))
                time.sleep(interval)
                _run_check(self._db_path, self._config_fn)
            except Exception as e:
                log.debug(f"[TIMED_EXIT] loop error: {e}")
                time.sleep(30)


# Module-level singleton — started once by auto_trader
_monitor = TimedExitMonitor()


def start_monitor(db_path: str, config_fn) -> None:
    """Called once at startup from auto_trader.py."""
    _monitor.start(db_path, config_fn)
