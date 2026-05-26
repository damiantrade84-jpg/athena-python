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
  When tp_mode="trailing_atr", after price reaches +trail_activation_r (vs audit or
  live SL), the timed-close path is replaced by a Chandelier-style ATR trail. Trade
  closes when price breaches the trail level (plus optional indicator confirm).
  The trailing branch runs regardless of broker floating P&L sign so fees/spread
  cannot move the trade back into fixed-mode timers.

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

from symbol_matching import symbols_match

log = logging.getLogger("timed_exit")

_be_done: set = set()
_trail_state: dict = {}
# Peak R-multiple reached per ticket while chandelier is active. Used by the
# give-back close to fire when current_r drops below peak by giveback_r.
_peak_r_state: dict[str, float] = {}
# Set of state_keys for which the fixed broker TP has already been cleared
# (one-shot per ticket — avoids re-sending clear-TP on every ratchet tick).
_tp_cleared_state: set[str] = set()
# Engine A/B style=scalp: last applied lock_r tier (0.0 = fallback BE only). Upgrades only.
_scalp_profit_lock_state: dict[str, float] = {}
# Per-process flag: True once the in-memory state has been hydrated from SQLite.
_state_hydrated: bool = False
# DB path captured by the monitor; persistence helpers use this when set.
_state_db_path: str | None = None
# Throttle unmatched-position warnings (key -> last warn monotonic time).
_unmatched_position_last_warn: dict[str, float] = {}

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
    "trail_activation_r": {"scalp": 0.7, "intraday": 1.0, "swing": 1.5},
    "trail_atr_mult": {"scalp": 2.0, "intraday": 2.5, "swing": 3.0},
    "pre_activation_profit_protect_enabled": False,
    "pre_activation_profit_arm_r": {"scalp": 0.20, "intraday": 0.25, "swing": 0.35},
    "pre_activation_profit_close_r": {"scalp": 0.0, "intraday": 0.0, "swing": 0.0},
    "pre_activation_profit_giveback_r": {"scalp": 0.25, "intraday": 0.35, "swing": 0.50},
    "trail_lookback": 14,
    "trail_timeframe": {"scalp": "H1", "intraday": "H4", "swing": "D1"},
    "trail_indicator_confirm": False,
    "trail_confirm_rsi_threshold": 40,
    "trail_confirm_rsi_period": 14,
    "trail_confirm_macd": True,
    "timer_tightens_trail": False,
    "timer_tighten_factor": 0.6,
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


def _pair_label_key(value: str | None) -> str:
    return str(value or "").replace("/", "").replace(" ", "").upper()


def _ensure_state_table(db_path: str) -> None:
    """Idempotent DDL for the trail/lock state side-table. Called lazily."""
    try:
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS timed_exit_state (
                    state_key       TEXT PRIMARY KEY,
                    trail_level     REAL,
                    lock_r          REAL,
                    peak_r          REAL,
                    tp_cleared      INTEGER DEFAULT 0,
                    last_update_ts  TEXT
                )
                """
            )
            for stmt in (
                "ALTER TABLE timed_exit_state ADD COLUMN peak_r REAL",
                "ALTER TABLE timed_exit_state ADD COLUMN tp_cleared INTEGER DEFAULT 0",
            ):
                try:
                    con.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            con.commit()
    except Exception as e:
        log.debug(f"[TIMED_EXIT] _ensure_state_table failed: {e}")


def _persist_trail_state(state_key: str, trail_level: float) -> None:
    """Best-effort persist of one trail level. Silent on failure (in-memory remains canonical)."""
    if not _state_db_path:
        return
    try:
        with sqlite3.connect(_state_db_path, timeout=10.0) as con:
            con.execute(
                """
                INSERT INTO timed_exit_state (state_key, trail_level, last_update_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    trail_level = excluded.trail_level,
                    last_update_ts = excluded.last_update_ts
                """,
                (state_key, float(trail_level), datetime.now(timezone.utc).isoformat()),
            )
            con.commit()
    except Exception as e:
        log.debug(f"[TIMED_EXIT] _persist_trail_state failed key={state_key}: {e}")


def _persist_peak_r(state_key: str, peak_r: float) -> None:
    """Best-effort persist of the highest R reached. Silent on failure."""
    if not _state_db_path:
        return
    try:
        with sqlite3.connect(_state_db_path, timeout=10.0) as con:
            con.execute(
                """
                INSERT INTO timed_exit_state (state_key, peak_r, last_update_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    peak_r = excluded.peak_r,
                    last_update_ts = excluded.last_update_ts
                """,
                (state_key, float(peak_r), datetime.now(timezone.utc).isoformat()),
            )
            con.commit()
    except Exception as e:
        log.debug(f"[TIMED_EXIT] _persist_peak_r failed key={state_key}: {e}")


def _persist_tp_cleared(state_key: str) -> None:
    """Best-effort persist of the broker-TP-cleared marker. Silent on failure."""
    if not _state_db_path:
        return
    try:
        with sqlite3.connect(_state_db_path, timeout=10.0) as con:
            con.execute(
                """
                INSERT INTO timed_exit_state (state_key, tp_cleared, last_update_ts)
                VALUES (?, 1, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    tp_cleared = 1,
                    last_update_ts = excluded.last_update_ts
                """,
                (state_key, datetime.now(timezone.utc).isoformat()),
            )
            con.commit()
    except Exception as e:
        log.debug(f"[TIMED_EXIT] _persist_tp_cleared failed key={state_key}: {e}")


def _persist_lock_state(state_key: str, lock_r: float) -> None:
    if not _state_db_path:
        return
    try:
        with sqlite3.connect(_state_db_path, timeout=10.0) as con:
            con.execute(
                """
                INSERT INTO timed_exit_state (state_key, lock_r, last_update_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    lock_r = excluded.lock_r,
                    last_update_ts = excluded.last_update_ts
                """,
                (state_key, float(lock_r), datetime.now(timezone.utc).isoformat()),
            )
            con.commit()
    except Exception as e:
        log.debug(f"[TIMED_EXIT] _persist_lock_state failed key={state_key}: {e}")


def _hydrate_state_from_db(db_path: str) -> None:
    """One-shot hydration of in-memory state dicts from SQLite at monitor start."""
    global _state_hydrated
    if _state_hydrated:
        return
    _ensure_state_table(db_path)
    try:
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT state_key, trail_level, lock_r, peak_r, tp_cleared FROM timed_exit_state"
            ).fetchall()
        for r in rows:
            sk = r["state_key"]
            if r["trail_level"] is not None:
                _trail_state[sk] = float(r["trail_level"])
            if r["lock_r"] is not None:
                _scalp_profit_lock_state[sk] = float(r["lock_r"])
            if r["peak_r"] is not None:
                _peak_r_state[sk] = float(r["peak_r"])
            if r["tp_cleared"]:
                _tp_cleared_state.add(sk)
        log.info(
            f"[TIMED_EXIT] State hydrated from db: {len(_trail_state)} trail entries, "
            f"{len(_scalp_profit_lock_state)} lock entries, "
            f"{len(_peak_r_state)} peak_r entries, "
            f"{len(_tp_cleared_state)} tp_cleared entries"
        )
    except Exception as e:
        log.debug(f"[TIMED_EXIT] _hydrate_state_from_db failed: {e}")
    finally:
        _state_hydrated = True


def _resolve_timed_exit_pair(pair_label: str) -> dict | None:
    label = str(pair_label or "").strip()
    if not label:
        return None
    label_key = _pair_label_key(label)
    try:
        from athena_runtime import rt

        all_pairs = getattr(rt(), "ALL_PAIRS", []) or []
    except Exception as exc:
        log.debug("[TIMED_EXIT] Pair registry unavailable for candle fetch: %s", exc)
        all_pairs = []
    for pair in all_pairs:
        if not isinstance(pair, dict):
            continue
        for field in ("display", "symbol", "pair"):
            raw = pair.get(field)
            if raw == label or _pair_label_key(raw) == label_key:
                return dict(pair)
    log.debug("[TIMED_EXIT] No configured pair found for candle fetch: %s", label)
    return None


def _fetch_timed_exit_candles(pair_label: str, tf: str, limit: int) -> list | None:
    pair = _resolve_timed_exit_pair(pair_label)
    if not pair:
        return None
    try:
        from candle_manager import fetch_candles
    except ImportError:
        return None
    try:
        return fetch_candles(pair, tf, limit)
    except Exception as exc:
        log.debug(
            "[TIMED_EXIT] Candle fetch failed pair=%s tf=%s limit=%s: %s",
            pair_label,
            tf,
            limit,
            exc,
        )
        return None


def _ohlc_series_from_candles(candles: list | None) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for candle in candles or []:
        try:
            if isinstance(candle, dict):
                high = candle.get("high")
                low = candle.get("low")
                close = candle.get("close")
            else:
                high = candle[2]
                low = candle[3]
                close = candle[4]
            highs.append(float(high))
            lows.append(float(low))
            closes.append(float(close))
        except (IndexError, KeyError, TypeError, ValueError):
            continue
    return highs, lows, closes


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


def _try_trailing_mode_breakeven(
    *,
    state_key: str,
    row: dict,
    style: str,
    be_due_now: bool,
    profit: float,
    tcfg: dict,
    entry: float,
    sl_raw: float,
    direction: str,
    live_sl: float,
    mins: float,
    move_sl: Callable[[float], dict],
    pair_label: str,
) -> None:
    """Time-based BE under trailing_atr when trade is green but below chandelier activation."""
    if not be_due_now or state_key in _be_done:
        return
    risk_amount = float(row.get("risk_amount") or 0)
    be_min_profit_r = tcfg.get("breakeven_min_profit_r", 0.20)
    be_buffer_r = tcfg.get("breakeven_buffer_r", 0.05)
    if profit < max(
        0.01,
        ((risk_amount * be_min_profit_r) if risk_amount > 0 else 0.0),
    ):
        return
    sl_dist = abs(entry - sl_raw) if sl_raw > 0 else 0.0
    if sl_dist <= 0:
        return
    if be_buffer_r > 0:
        be_buffer = sl_dist * be_buffer_r
        direction_long = str(direction).upper() != "SHORT"
        be_price = entry + be_buffer if direction_long else entry - be_buffer
    else:
        be_price = entry
    if not _protective_sl_tightens(direction, be_price, live_sl, entry):
        return
    result = move_sl(be_price)
    if result.get("success") and not result.get("skipped"):
        _be_done.add(state_key)
        log.info(
            f"[TIMED_EXIT] trailing BE set: {pair_label} style={style} "
            f"mins_open={mins:.1f} be_price={be_price:.5f} "
            f"(entry={entry:.5f} buffer={be_buffer_r:.0%} of SL dist)"
        )


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
        _lock_val = float(record_lock if record_lock is not None else 0.0)
        _scalp_profit_lock_state[ticket_key] = _lock_val
        _persist_lock_state(ticket_key, _lock_val)
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
    # trail_activation_r accepts either a scalar (applied to all styles) or a per-style dict.
    _act_raw = raw.get("trail_activation_r", _DEFAULT_CFG["trail_activation_r"])
    if isinstance(_act_raw, dict):
        _act_defaults = _DEFAULT_CFG["trail_activation_r"]
        merged["trail_activation_r"] = {
            s: float(_act_raw.get(s, _act_defaults[s]))
            for s in ("scalp", "intraday", "swing")
        }
    else:
        try:
            _act_scalar = float(_act_raw)
        except (TypeError, ValueError):
            _act_scalar = 1.0
        merged["trail_activation_r"] = {
            s: _act_scalar for s in ("scalp", "intraday", "swing")
        }
    trail_mult_raw = raw.get("trail_atr_mult") or {}
    merged["trail_atr_mult"] = {
        s: float(trail_mult_raw.get(s, _DEFAULT_CFG["trail_atr_mult"][s]))
        for s in ("scalp", "intraday", "swing")
    }
    # Per-venue trail rope overrides. Each venue bucket fills missing styles
    # from the default trail_atr_mult so a partial override still works.
    trail_mult_by_venue_raw = raw.get("trail_atr_mult_by_venue") or {}
    merged["trail_atr_mult_by_venue"] = {}
    if isinstance(trail_mult_by_venue_raw, dict):
        for venue, bucket in trail_mult_by_venue_raw.items():
            if not isinstance(bucket, dict):
                continue
            merged["trail_atr_mult_by_venue"][str(venue).lower()] = {
                s: float(bucket.get(s, merged["trail_atr_mult"][s]))
                for s in ("scalp", "intraday", "swing")
            }
    # Peak-R give-back budget per style (default) and optional per-venue.
    giveback_raw = raw.get("trail_giveback_r") or {}
    if isinstance(giveback_raw, dict):
        merged["trail_giveback_r"] = {
            s: float(giveback_raw.get(s, 0.0))
            for s in ("scalp", "intraday", "swing")
        }
    else:
        try:
            _gb_scalar = float(giveback_raw)
        except (TypeError, ValueError):
            _gb_scalar = 0.0
        merged["trail_giveback_r"] = {
            s: _gb_scalar for s in ("scalp", "intraday", "swing")
        }
    giveback_by_venue_raw = raw.get("trail_giveback_r_by_venue") or {}
    merged["trail_giveback_r_by_venue"] = {}
    if isinstance(giveback_by_venue_raw, dict):
        for venue, bucket in giveback_by_venue_raw.items():
            if not isinstance(bucket, dict):
                continue
            merged["trail_giveback_r_by_venue"][str(venue).lower()] = {
                s: float(bucket.get(s, merged["trail_giveback_r"][s]))
                for s in ("scalp", "intraday", "swing")
            }
    merged["pre_activation_profit_protect_enabled"] = bool(
        raw.get(
            "pre_activation_profit_protect_enabled",
            _DEFAULT_CFG["pre_activation_profit_protect_enabled"],
        )
    )
    for key in (
        "pre_activation_profit_arm_r",
        "pre_activation_profit_close_r",
        "pre_activation_profit_giveback_r",
    ):
        raw_map = raw.get(key, _DEFAULT_CFG[key])
        defaults = _DEFAULT_CFG[key]
        if isinstance(raw_map, dict):
            merged[key] = {
                s: float(raw_map.get(s, defaults[s]))
                for s in ("scalp", "intraday", "swing")
            }
        else:
            try:
                scalar = float(raw_map)
            except (TypeError, ValueError):
                scalar = float(defaults["intraday"])
            merged[key] = {s: scalar for s in ("scalp", "intraday", "swing")}
        by_venue_key = f"{key}_by_venue"
        by_venue_raw = raw.get(by_venue_key) or {}
        merged[by_venue_key] = {}
        if isinstance(by_venue_raw, dict):
            for venue, bucket in by_venue_raw.items():
                if not isinstance(bucket, dict):
                    continue
                merged[by_venue_key][str(venue).lower()] = {
                    s: float(bucket.get(s, merged[key][s]))
                    for s in ("scalp", "intraday", "swing")
                }
    merged["trail_clear_broker_tp_on_activation"] = bool(
        raw.get("trail_clear_broker_tp_on_activation", True)
    )
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
    merged["timer_tightens_trail"] = bool(raw.get("timer_tightens_trail", _DEFAULT_CFG["timer_tightens_trail"]))
    try:
        merged["timer_tighten_factor"] = float(raw.get("timer_tighten_factor", _DEFAULT_CFG["timer_tighten_factor"]))
    except (TypeError, ValueError):
        merged["timer_tighten_factor"] = float(_DEFAULT_CFG["timer_tighten_factor"])
    scalp_raw = cfg.get("SCALP_ENGINE") or {}
    merged["scalp_live_profit_protect"] = bool(scalp_raw.get("LIVE_PROFIT_PROTECT", True))
    return merged


def _is_engine_d_engine(engine: str) -> bool:
    return str(engine or "").strip().lower() in ("scalp", "engine d", "scalp_vp")


def _activation_r_for(tcfg: dict, style: str) -> float:
    """Per-style activation R-multiple, with scalar back-compat handled in _get_timed_cfg."""
    act = tcfg.get("trail_activation_r")
    if isinstance(act, dict):
        try:
            return float(act.get(style, act.get("intraday", 1.0)))
        except (TypeError, ValueError):
            return 1.0
    try:
        return float(act)
    except (TypeError, ValueError):
        return 1.0


def _trail_atr_mult_for(tcfg: dict, style: str, venue: str | None) -> float:
    """Resolve trail ATR multiplier. Per-venue override wins when defined; else default per-style."""
    by_venue = tcfg.get("trail_atr_mult_by_venue") or {}
    if venue and isinstance(by_venue, dict):
        bucket = by_venue.get(venue) or by_venue.get(venue.lower())
        if isinstance(bucket, dict):
            try:
                return float(bucket.get(style, bucket.get("intraday", 2.5)))
            except (TypeError, ValueError):
                pass
    default = tcfg.get("trail_atr_mult") or {}
    if isinstance(default, dict):
        try:
            return float(default.get(style, default.get("intraday", 2.5)))
        except (TypeError, ValueError):
            return 2.5
    try:
        return float(default)
    except (TypeError, ValueError):
        return 2.5


def _giveback_r_for(tcfg: dict, style: str, venue: str | None) -> float:
    """Resolve peak-R give-back budget. Per-venue override wins; 0 disables give-back close."""
    by_venue = tcfg.get("trail_giveback_r_by_venue") or {}
    if venue and isinstance(by_venue, dict):
        bucket = by_venue.get(venue) or by_venue.get(venue.lower())
        if isinstance(bucket, dict):
            try:
                return float(bucket.get(style, bucket.get("intraday", 0.0)))
            except (TypeError, ValueError):
                pass
    default = tcfg.get("trail_giveback_r") or {}
    if isinstance(default, dict):
        try:
            return float(default.get(style, default.get("intraday", 0.0)))
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(default)
    except (TypeError, ValueError):
        return 0.0


def _venue_style_r_for(
    tcfg: dict,
    key: str,
    style: str,
    venue: str | None,
    default: float = 0.0,
) -> float:
    by_venue = tcfg.get(f"{key}_by_venue") or {}
    if venue and isinstance(by_venue, dict):
        bucket = by_venue.get(venue) or by_venue.get(str(venue).lower())
        if isinstance(bucket, dict):
            try:
                return float(bucket.get(style, bucket.get("intraday", default)))
            except (TypeError, ValueError):
                pass
    default_map = tcfg.get(key) or {}
    if isinstance(default_map, dict):
        try:
            return float(default_map.get(style, default_map.get("intraday", default)))
        except (TypeError, ValueError):
            return default
    try:
        return float(default_map)
    except (TypeError, ValueError):
        return default


def _state_key(venue: str, audit_id, ticket) -> str:
    """Stable per-trade key. audit_id is the canonical identity; ticket is fallback only.

    Two Bybit positions with ticket=0 (split legs / fallback matching) would otherwise
    collide on a ticket-only key.
    """
    if audit_id not in (None, "", 0):
        return f"{venue}:aid:{audit_id}"
    return f"{venue}:tkt:{ticket}"


def _load_recent_audit_rows(db_path: str) -> list[dict]:
    """Load audit rows for ticket/fallback matching.

    Includes all open executed rows (exit_time IS NULL with ticket) plus the
    400 most recent rows so long-held swings stay correlated with live positions.
    """
    select_cols = """
        SELECT id, ticket, pair, engine, style, ts, direction, entry_price, sl, tp,
               tp_partial, volume, risk_amount, asset_class, exit_time, grade
        FROM   audit_log
        WHERE  pair IS NOT NULL
          AND  grade NOT LIKE '%ERR%'
    """
    try:
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            open_rows = con.execute(
                select_cols
                + """
                  AND exit_time IS NULL
                  AND ticket IS NOT NULL
                  AND TRIM(ticket) != ''
                  AND TRIM(ticket) != '0'
                ORDER BY ts DESC
                """
            ).fetchall()
            recent_rows = con.execute(
                select_cols + " ORDER BY ts DESC LIMIT 400"
            ).fetchall()
            merged: dict[int, dict] = {}
            for row in open_rows + recent_rows:
                merged[int(row["id"])] = dict(row)
            return list(merged.values())
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


def _risk_sl_for_activation(audit_sl: float, live_sl: float) -> float:
    """SL price used for R-multiples and chandelier context.

    Prefer the broker live SL when known so activation matches the stop the
    position is working against; otherwise use audit (fill) SL.
    """
    a = _safe_float(audit_sl)
    l = _safe_float(live_sl)
    return l if l > 0 else a


def _log_trail_ratchet_broker_outcome(
    *,
    venue: str,
    pair: str,
    detail: str,
    style: str,
    new_sl: float,
    current_r: float,
    trail_reason: str | None,
    ratchet_res: dict,
) -> None:
    """Log broker outcome for trail SL ratchet.

    ``success=True`` + ``skipped=True`` (MT5) or ``alreadySet`` (Bybit) means the
    protective SL is already at or beyond the target — not an error.
    """
    if ratchet_res.get("success") and not ratchet_res.get("skipped"):
        if ratchet_res.get("alreadySet"):
            log.info(
                f"[TIMED_EXIT] TRAIL RATCHET no-op: {venue} {detail} pair={pair} "
                f"style={style} target_sl={new_sl:.5f} R={current_r:.2f} trail_reason={trail_reason} "
                f"reason=SL already at broker target"
            )
            return
        log.info(
            f"[TIMED_EXIT] TRAIL RATCHET: {venue} {detail} pair={pair} "
            f"style={style} new_sl={new_sl:.5f} R={current_r:.2f} reason={trail_reason}"
        )
        return
    if ratchet_res.get("success") and ratchet_res.get("skipped"):
        log.info(
            f"[TIMED_EXIT] TRAIL RATCHET no-op: {venue} {detail} pair={pair} "
            f"style={style} target_sl={new_sl:.5f} R={current_r:.2f} trail_reason={trail_reason} "
            f"broker_reason={ratchet_res.get('reason')!r}"
        )
        return
    log.warning(
        f"[TIMED_EXIT] TRAIL RATCHET failed/skipped: {venue} {detail} pair={pair} "
        f"style={style} target_sl={new_sl:.5f} R={current_r:.2f} trail_reason={trail_reason} "
        f"success={ratchet_res.get('success')} skipped={ratchet_res.get('skipped')} "
        f"error={ratchet_res.get('error')!r} broker_reason={ratchet_res.get('reason')!r}"
    )


def _timeframe_minutes(tf: str) -> float:
    raw = str(tf or "").strip().upper()
    if not raw:
        return 60.0
    if raw[0].isalpha():
        unit = raw[0]
        value_raw = raw[1:] or "1"
    else:
        unit = raw[-1]
        value_raw = raw[:-1] or "1"
    try:
        value = max(float(value_raw), 1.0)
    except (TypeError, ValueError):
        value = 1.0
    if unit == "M":
        return value
    if unit == "H":
        return value * 60.0
    if unit == "D":
        return value * 24.0 * 60.0
    if unit == "W":
        return value * 7.0 * 24.0 * 60.0
    return 60.0


def _live_pnl_r(row: dict, entry: float, sl: float, volume: float, live_pnl: float) -> float:
    try:
        risk_amount = float(row.get("risk_amount") or 0)
    except (TypeError, ValueError):
        risk_amount = 0.0
    if live_pnl != 0 and risk_amount > 0:
        return round(float(live_pnl) / risk_amount, 2)
    risk_dist = abs(float(entry) - float(sl)) if entry and sl else 0.0
    if live_pnl != 0 and risk_dist > 0 and volume > 0:
        return round(float(live_pnl) / (risk_dist * float(volume)), 2)
    return 0.0


def _audit_row_is_executed_like(row: dict) -> bool:
    grade = str(row.get("grade") or "").strip().upper()
    if grade in ("EXECUTED", "AUTO"):
        return True
    ticket = str(row.get("ticket") or "").strip()
    return ticket not in ("", "0", "None") and _safe_float(row.get("volume")) > 0


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

    if not pos_pair or pos_entry <= 0:
        return None

    now_utc = datetime.now(timezone.utc)
    candidates: list[tuple[int, float, dict]] = []

    for row in audit_rows:
        if row.get("exit_time") is not None:
            continue
        row_pair = str(row.get("pair") or "")
        if not symbols_match(row_pair, pos_pair):
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

        # Prefer executed/live-like rows over signal-only rows, then closest entry.
        score = rel_entry_diff
        score += min(age_min, 24 * 60) / 100000.0
        executed_rank = 0 if _audit_row_is_executed_like(row) else 1
        candidates.append((executed_rank, score, row))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


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


def _mark_timed_close(
    db_path: str,
    row: dict,
    venue: str,
    *,
    actual_close_price: float | None = None,
    live_pnl: float | None = None,
    reason: str = "TIMED_CLOSE",
) -> None:
    """Persist an exit-reason marker so outcome logging preserves how the trade closed.

    `reason` (suggestion #11) distinguishes:
      - TRAIL_CLOSE   : chandelier trail breached + confirmed
      - TIMED_CLOSE   : timer-driven close (legacy fixed-mode path)
      - LOCK_SL_HIT   : reserved for future broker-detected lock SL hits

    MT5: prefer exact-ticket match; fall back to id-based mark when the audit row was
    matched by proximity (split legs, fallback matching) to avoid silent no-ops.
    Bybit: always mark by id (live ticket is not stable on Bybit).

    When actual_close_price and live_pnl are provided (from timed exit), write them
    immediately to prevent the outcome monitor from overwriting with incorrect deal data.
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
                    reason,
                    actual_close_price,
                    live_pnl,
                    r_multiple,
                    datetime.now(timezone.utc).isoformat(),
                    audit_ticket,
                )
            else:
                query = "UPDATE audit_log SET exit_reason=? WHERE ticket=? AND exit_price IS NULL"
                params = (reason, audit_ticket)
        elif audit_id is not None:
            # Fallback: matched by proximity (split legs, etc.) — use stable row id
            if actual_close_price is not None and live_pnl is not None:
                query = "UPDATE audit_log SET exit_reason=?, exit_price=?, pnl=?, r_multiple=?, exit_time=? WHERE id=? AND exit_price IS NULL"
                params = (
                    reason,
                    actual_close_price,
                    live_pnl,
                    r_multiple,
                    datetime.now(timezone.utc).isoformat(),
                    audit_id,
                )
            else:
                query = "UPDATE audit_log SET exit_reason=? WHERE id=? AND exit_price IS NULL"
                params = (reason, audit_id)
        else:
            log.debug(f"[TIMED_EXIT] _mark_timed_close: no usable key for MT5 {row.get('pair')} — skipping")
            return
    else:
        if audit_id is None:
            log.debug(f"[TIMED_EXIT] _mark_timed_close: no audit_id for Bybit {row.get('pair')} — skipping")
            return
        if actual_close_price is not None and live_pnl is not None:
            query = "UPDATE audit_log SET exit_reason=?, exit_price=?, pnl=?, r_multiple=?, exit_time=? WHERE id=? AND exit_price IS NULL"
            params = (
                reason,
                actual_close_price,
                live_pnl,
                r_multiple,
                datetime.now(timezone.utc).isoformat(),
                audit_id,
            )
        else:
            query = "UPDATE audit_log SET exit_reason=? WHERE id=? AND exit_price IS NULL"
            params = (reason, audit_id)

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
    tcfg: dict, ticket_key: str, *, mins_open: float = 0.0,
    venue: str | None = None,
) -> float | None:
    """Compute Chandelier-style trailing stop from recent candle data.

    Returns the trail price level. On candle/data failure returns the *previous*
    `_trail_state[ticket_key]` if present (so a transient candle fetch hiccup
    does not collapse the trade into the timed-close path); only returns None
    when no prior level exists. Successful computations ratchet in the trade's
    favour and are persisted to disk via _persist_state.
    """
    prev = _trail_state.get(ticket_key)

    def _fail(reason: str) -> float | None:
        if prev is not None:
            log.warning(
                f"[TIMED_EXIT] Trail data unavailable ({reason}) — holding previous "
                f"level {prev:.5f}: pair={pair} style={style} key={ticket_key}"
            )
            return prev
        log.warning(
            f"[TIMED_EXIT] Trail data unavailable ({reason}) and no prior level — "
            f"trail inactive this tick: pair={pair} style={style} key={ticket_key}"
        )
        return None

    try:
        from indicators import calc_atr
    except ImportError:
        return _fail("indicators import failed")

    tf = tcfg["trail_timeframe"].get(style, "H4")
    lookback = tcfg["trail_lookback"]
    mult = _trail_atr_mult_for(tcfg, style, venue)

    # Timer-tightens-trail: once mins_open exceeds the style's close threshold,
    # tighten the chandelier multiplier instead of force-closing the trade.
    if tcfg.get("timer_tightens_trail") and mins_open > 0:
        scfg = tcfg.get(style) or {}
        if style == "swing":
            close_min = float(scfg.get("close_days", 0.0)) * 24 * 60
        else:
            close_min = float(scfg.get("close_min", 0.0))
        if close_min > 0 and mins_open >= close_min:
            mult = mult * float(tcfg.get("timer_tighten_factor", 0.6))

    candles = _fetch_timed_exit_candles(pair, tf, lookback + 20)

    if not candles or len(candles) < lookback + 1:
        return _fail("insufficient candles")

    highs, lows, closes = _ohlc_series_from_candles(candles)
    if len(closes) < lookback + 1:
        return _fail("insufficient parsed closes")

    atr_series = calc_atr(highs, lows, closes, 14)
    atr_val = next((v for v in reversed(atr_series) if v is not None), None)
    if atr_val is None or atr_val <= 0:
        return _fail("ATR not computable")

    tf_minutes = _timeframe_minutes(tf)
    try:
        bars_since_entry = int(float(mins_open or 0.0) // tf_minutes)
    except (TypeError, ValueError, ZeroDivisionError):
        bars_since_entry = 1
    effective_lookback = max(1, min(lookback, max(1, bars_since_entry)))
    recent_highs = highs[-effective_lookback:]
    recent_lows = lows[-effective_lookback:]
    highest = max(recent_highs)
    lowest = min(recent_lows)

    if direction == "LONG":
        raw_trail = highest - mult * atr_val
    else:
        raw_trail = lowest + mult * atr_val

    if prev is not None:
        if direction == "LONG":
            raw_trail = max(raw_trail, prev)
        else:
            raw_trail = min(raw_trail, prev)

    _trail_state[ticket_key] = raw_trail
    _persist_trail_state(ticket_key, raw_trail)
    return raw_trail


def _check_indicator_confirmation(
    pair: str, style: str, direction: str, tcfg: dict,
) -> bool:
    """Check RSI and/or MACD for direction-change confirmation.

    Returns True if indicators confirm the reversal (safe to close).
    Returns False on any data error (fail-closed: keep trailing if we can't check).
    """
    if not tcfg.get("trail_indicator_confirm", False):
        return True

    try:
        from indicators import calc_rsi, calc_macd
    except ImportError:
        return False

    tf = tcfg["trail_timeframe"].get(style, "H4")
    rsi_period = tcfg.get("trail_confirm_rsi_period", 14)
    rsi_thresh = tcfg.get("trail_confirm_rsi_threshold", 40)
    check_macd = tcfg.get("trail_confirm_macd", True)

    candles = _fetch_timed_exit_candles(pair, tf, max(rsi_period + 30, 60))

    if not candles or len(candles) < rsi_period + 5:
        return False

    _highs, _lows, closes = _ohlc_series_from_candles(candles)
    if len(closes) < rsi_period + 5:
        return False

    rsi_series = calc_rsi(closes, rsi_period)
    rsi_val = next((v for v in reversed(rsi_series) if v is not None), None)
    if rsi_val is None:
        return False

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
        return False

    if direction == "LONG" and recent_hist[-1] < 0 and recent_hist[-2] >= 0:
        return True
    if direction == "SHORT" and recent_hist[-1] > 0 and recent_hist[-2] <= 0:
        return True

    if direction == "LONG" and recent_hist[-1] < 0:
        return True
    if direction == "SHORT" and recent_hist[-1] > 0:
        return True

    return False


def _evaluate_trail(
    row: dict, style: str, direction: str, entry: float, sl: float,
    cur_price: float, tcfg: dict, *, mins_open: float = 0.0,
    state_key: str | None = None,
    live_sl_for_r: float | None = None,
    venue: str | None = None,
    trail_mode: str = "full",
) -> dict:
    """Single decision point for the chandelier trail state machine.

    ``sl`` is the audit (opening) stop; ``live_sl_for_r`` when >0 overrides for
    R-multiples and chandelier inputs so activation matches the broker stop.

    Returns one of:
        {"action": "none",    "reason": ...}
        {"action": "ratchet", "new_sl": float, "reason": ..., "trail_level",
                              "clear_broker_tp": bool}   -- ratchet (and optionally clear TP)
        {"action": "close",   "trail_level": float, "reason": ...}

    The "ratchet" path may set ``clear_broker_tp`` once per ticket on first
    activation (when trail_clear_broker_tp_on_activation is true) so the
    handler can drop the fixed broker TP. Subsequent ticks omit the flag.

    The "close" reasons:
        - "breach_confirmed" — price crossed the chandelier line (+ optional indicator confirm)
        - "peak_giveback"    — current R fell below peak R by trail_giveback_r
        - "profit_roundtrip" — sub-activation profit was armed, then gave back to floor
    """
    pair = row.get("pair") or ""
    if state_key is None:
        state_key = f"{row.get('ticket')}_{pair}"

    sl_for_r = _risk_sl_for_activation(
        sl, live_sl_for_r if live_sl_for_r is not None else 0.0
    )
    risk_dist = abs(entry - sl_for_r) if sl_for_r > 0 else 0.0
    if risk_dist <= 0:
        return {"action": "none", "reason": "no_risk_dist"}

    if direction == "LONG":
        current_r = (cur_price - entry) / risk_dist
    else:
        current_r = (entry - cur_price) / risk_dist

    activation_r = _activation_r_for(tcfg, style)
    if tcfg.get("pre_activation_profit_protect_enabled"):
        arm_r = _venue_style_r_for(
            tcfg, "pre_activation_profit_arm_r", style, venue, default=0.0
        )
        close_r = _venue_style_r_for(
            tcfg, "pre_activation_profit_close_r", style, venue, default=0.0
        )
        giveback_r_pre = _venue_style_r_for(
            tcfg, "pre_activation_profit_giveback_r", style, venue, default=0.0
        )
        prev_peak_pre = _peak_r_state.get(state_key)
        peak_r_pre = prev_peak_pre if prev_peak_pre is not None else current_r
        if current_r > peak_r_pre:
            peak_r_pre = current_r
        if arm_r > 0 and peak_r_pre >= arm_r:
            if prev_peak_pre is None or current_r > prev_peak_pre:
                _peak_r_state[state_key] = current_r
                _persist_peak_r(state_key, current_r)
                peak_r_pre = current_r
            else:
                _peak_r_state.setdefault(state_key, prev_peak_pre)

            roundtrip_close = current_r <= close_r
            giveback_close_pre = (
                giveback_r_pre > 0 and (peak_r_pre - current_r) >= giveback_r_pre
            )
            if current_r < activation_r and (roundtrip_close or giveback_close_pre):
                log.info(
                    f"[TIMED_EXIT] PROFIT ROUNDTRIP close: {pair} style={style} dir={direction} "
                    f"peak_r={peak_r_pre:.2f} current_r={current_r:.2f} "
                    f"arm_r={arm_r:.2f} close_r={close_r:.2f} giveback_r={giveback_r_pre:.2f}"
                )
                return {
                    "action": "close",
                    "trail_level": None,
                    "current_r": current_r,
                    "peak_r": peak_r_pre,
                    "giveback_r": giveback_r_pre,
                    "reason": "profit_roundtrip",
                }

    if current_r < activation_r:
        peak_watch = _peak_r_state.get(state_key)
        if current_r > 0:
            log.info(
                f"[TIMED_EXIT] below_activation watch: {pair} style={style} dir={direction} "
                f"current_r={current_r:.2f} peak_r={peak_watch} activation_r={activation_r:.2f} "
                f"mode={trail_mode}"
            )
        if trail_mode == "pre_activation_only":
            return {"action": "none", "reason": "below_activation", "current_r": current_r}
        return {"action": "none", "reason": "below_activation", "current_r": current_r}

    if trail_mode == "pre_activation_only":
        return {"action": "none", "reason": "pre_activation_only_cap", "current_r": current_r}

    first_activation_tick = _trail_state.get(state_key) is None
    trail_level = _compute_chandelier_trail(
        pair, style, direction, entry, sl_for_r, tcfg, state_key,
        mins_open=mins_open, venue=venue,
    )
    if trail_level is None:
        return {"action": "none", "reason": "no_trail_data", "current_r": current_r}

    if first_activation_tick:
        epsilon = max(abs(cur_price) * 1e-8, 1e-8)
        clamped_trail = trail_level
        if direction == "LONG" and trail_level >= cur_price:
            clamped_trail = cur_price - epsilon
        elif direction == "SHORT" and trail_level <= cur_price:
            clamped_trail = cur_price + epsilon
        if clamped_trail != trail_level:
            log.info(
                f"[TIMED_EXIT] First activation trail clamped: {pair} style={style} "
                f"raw={trail_level:.5f} clamped={clamped_trail:.5f} price={cur_price:.5f}"
            )
            trail_level = clamped_trail
            _trail_state[state_key] = trail_level
            _persist_trail_state(state_key, trail_level)

    # Peak-R tracking. Ratchets up monotonically. Persisted so a monitor
    # restart does not erase the peak and reset the give-back budget.
    prev_peak = _peak_r_state.get(state_key, current_r)
    if current_r > prev_peak:
        _peak_r_state[state_key] = current_r
        _persist_peak_r(state_key, current_r)
        peak_r = current_r
    else:
        _peak_r_state.setdefault(state_key, prev_peak)
        peak_r = prev_peak

    giveback_r = _giveback_r_for(tcfg, style, venue)
    giveback_close = (
        giveback_r > 0 and (peak_r - current_r) >= giveback_r and peak_r >= activation_r
    )

    breached = (
        (direction == "LONG" and cur_price < trail_level)
        or (direction == "SHORT" and cur_price > trail_level)
    )

    # Whether to signal the broker TP drop. One-shot per ticket; gated by config.
    clear_broker_tp = bool(
        tcfg.get("trail_clear_broker_tp_on_activation", True)
        and state_key not in _tp_cleared_state
    )
    if clear_broker_tp:
        # Mark as cleared *before* returning so a subsequent tick in the same
        # process doesn't re-send the clear if the handler ignored it.
        _tp_cleared_state.add(state_key)
        _persist_tp_cleared(state_key)

    if giveback_close:
        log.info(
            f"[TIMED_EXIT] PEAK GIVE-BACK close: {pair} style={style} dir={direction} "
            f"peak_r={peak_r:.2f} current_r={current_r:.2f} giveback_r={giveback_r:.2f}"
        )
        return {
            "action": "close",
            "trail_level": trail_level,
            "current_r": current_r,
            "peak_r": peak_r,
            "giveback_r": giveback_r,
            "reason": "peak_giveback",
        }

    if not breached:
        return {
            "action": "ratchet",
            "new_sl": trail_level,
            "trail_level": trail_level,
            "current_r": current_r,
            "peak_r": peak_r,
            "clear_broker_tp": clear_broker_tp,
            "reason": "active",
        }

    if not _check_indicator_confirmation(pair, style, direction, tcfg):
        log.debug(
            f"[TIMED_EXIT] Trail breached but indicator confirmation failed: {pair} "
            f"style={style} trail={trail_level:.5f} price={cur_price:.5f}"
        )
        # Breach without confirmation — keep ratcheting; do not close.
        return {
            "action": "ratchet",
            "new_sl": trail_level,
            "trail_level": trail_level,
            "current_r": current_r,
            "peak_r": peak_r,
            "clear_broker_tp": clear_broker_tp,
            "reason": "breach_no_confirm",
        }

    log.info(
        f"[TIMED_EXIT] TRAIL CLOSE signal: {pair} style={style} direction={direction} "
        f"trail={trail_level:.5f} price={cur_price:.5f} R={current_r:.2f}"
    )
    return {
        "action": "close",
        "trail_level": trail_level,
        "current_r": current_r,
        "peak_r": peak_r,
        "reason": "breach_confirmed",
    }


def _should_trail_close(
    row: dict, style: str, direction: str, entry: float, sl: float,
    cur_price: float, tcfg: dict,
) -> bool:
    """Backward-compat boolean wrapper. New code should call _evaluate_trail() directly."""
    res = _evaluate_trail(row, style, direction, entry, sl, cur_price, tcfg)
    return res.get("action") == "close"


def _warn_unmatched_live_position(pos: dict, *, venue: str) -> None:
    """Log when a live broker position has no audit correlation (throttled)."""
    ticket = str(pos.get("ticket") or "").strip()
    pair = str(pos.get("pair") or pos.get("symbol") or "").strip()
    key = f"{venue}:{ticket or pair}"
    now = time.monotonic()
    last = _unmatched_position_last_warn.get(key, 0.0)
    if now - last < 300.0:
        return
    _unmatched_position_last_warn[key] = now
    log.warning(
        "[TIMED_EXIT] No audit match for live %s position ticket=%s pair=%s — "
        "software exits (trail/BE/timed) will not run for this position",
        venue,
        ticket or "?",
        pair or "?",
    )


def _maybe_repair_mt5_broker_sl(row: dict, live: dict) -> None:
    """Re-apply audit SL when broker stop is missing (fail-closed protective repair)."""
    audit_sl = _safe_float(row.get("sl"))
    live_sl = _safe_float(live.get("sl"))
    if audit_sl <= 0 or live_sl > 0:
        return
    try:
        from mt5_executor import mt5_modify_protective_stops
    except ImportError:
        return
    ticket = int(row["ticket"])
    audit_tp = _safe_float(row.get("tp"))
    live_tp = _safe_float(live.get("tp"))
    tp = live_tp if live_tp > 0 else audit_tp
    res = mt5_modify_protective_stops(
        ticket,
        sl=audit_sl,
        tp=tp if tp > 0 else None,
    )
    if res.get("success"):
        log.warning(
            "[TIMED_EXIT] Repaired missing broker SL ticket=%s pair=%s sl=%.5f",
            ticket,
            row.get("pair"),
            audit_sl,
        )
    else:
        log.warning(
            "[TIMED_EXIT] Failed to repair missing broker SL ticket=%s pair=%s: %s",
            ticket,
            row.get("pair"),
            res.get("error") or res.get("detail") or "unknown",
        )


def _handle_mt5_row(row: dict, tcfg: dict, db_path: str | None = None) -> None:
    """Apply timed exit logic to a single MT5 trade row."""
    try:
        from mt5_executor import (
            mt5_close_position,
            mt5_move_sl_to_breakeven,
            mt5_modify_protective_stops,
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

    if style not in ("scalp", "intraday", "swing"):
        style = "intraday"

    # Get live position data (once per tick — also used for SL repair on all engines)
    pos_result = mt5_get_positions()
    if pos_result.get("error"):
        log.warning(
            "[TIMED_EXIT] mt5_get_positions error for ticket=%s pair=%s — skip tick (%s)",
            ticket,
            row.get("pair"),
            pos_result.get("error"),
        )
        return
    live = next(
        (p for p in pos_result.get("positions", []) if p.get("ticket") == ticket),
        None,
    )
    if not live:
        return  # position already closed

    _maybe_repair_mt5_broker_sl(row, live)

    # Engine D / Scalp Lab trades use broker TP1/SL management.
    # Do not treat generic Engine A/B "style=scalp" trades as Engine D.
    if engine in ("scalp", "engine d", "scalp_vp"):
        return

    scfg = tcfg[style]
    mins = _minutes_open(row["ts"])

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
    _live_sl_for_trail = _safe_float(live.get("sl"))
    state_key = _state_key("mt5", row.get("audit_id"), ticket)

    # ── Trailing ATR TP (mode-dispatched single exit policy) ──────────────────
    # When tp_mode=="trailing_atr", the chandelier trail is the only profit-side
    # exit (unconditional on broker profit sign so spreads/fees cannot drop the
    # trade back into timed-close / BE paths). Below activation_r the original
    # ATR SL protects; above it, the broker SL ratchets with the trail (#6).
    if tcfg.get("tp_mode") == "trailing_atr":
        trail_eval = _evaluate_trail(
            row,
            style,
            direction,
            entry,
            _sl_raw,
            cur_price,
            tcfg,
            mins_open=mins,
            state_key=state_key,
            live_sl_for_r=_live_sl_for_trail,
            venue="mt5",
        )
        action = trail_eval.get("action")
        close_reason = "TRAIL_CLOSE"
        if trail_eval.get("reason") == "peak_giveback":
            close_reason = "TRAIL_GIVEBACK"
        elif trail_eval.get("reason") == "profit_roundtrip":
            close_reason = "TRAIL_PROFIT_ROUNDTRIP"

        if action == "close":
            result = mt5_close_position(ticket)
            if result.get("success"):
                actual_close_price = result.get("closePrice")
                live_pnl = result.get("liveProfit", 0.0)
                pnl_r = 0.0
                if live_pnl != 0 and row.get("risk_amount") and row["risk_amount"] > 0:
                    pnl_r = round(live_pnl / float(row["risk_amount"]), 2)
                if db_path:
                    _mark_timed_close(
                        db_path, row, "mt5",
                        actual_close_price=actual_close_price,
                        live_pnl=live_pnl,
                        reason=close_reason,
                    )
                log.info(
                    f"[TIMED_EXIT] {close_reason}: {row['pair']} ticket={ticket} "
                    f"style={style} mins={mins:.0f} profit=${live_pnl:.2f} R={pnl_r}"
                )
                try:
                    telegram_notify.notify_trade_closed(
                        pair=row["pair"],
                        pnl_r=pnl_r,
                        is_win=live_pnl > 0,
                        duration_minutes=mins,
                        exit_reason=close_reason,
                        style=style,
                        exchange="mt5",
                    )
                except Exception:
                    pass
            return

        if action == "ratchet":
            new_sl = float(trail_eval["new_sl"])
            tr = trail_eval.get("reason")
            cr = float(trail_eval.get("current_r", 0) or 0)
            clear_tp = bool(trail_eval.get("clear_broker_tp"))
            sl_tightens = _protective_sl_tightens(direction, new_sl, _live_sl_for_trail, entry)
            if sl_tightens or clear_tp:
                ratchet_res = mt5_modify_protective_stops(
                    ticket,
                    sl=new_sl if sl_tightens else None,
                    clear_tp=clear_tp,
                )
                if clear_tp and ratchet_res.get("success"):
                    log.info(
                        f"[TIMED_EXIT] Broker TP cleared (chandelier active): "
                        f"{row['pair']} ticket={ticket} style={style} R={cr:.2f}"
                    )
                _log_trail_ratchet_broker_outcome(
                    venue="MT5",
                    pair=str(row.get("pair") or ""),
                    detail=f"ticket={ticket}",
                    style=style,
                    new_sl=new_sl,
                    current_r=cr,
                    trail_reason=str(tr),
                    ratchet_res=ratchet_res,
                )
        _try_trailing_mode_breakeven(
            state_key=state_key,
            row=row,
            style=style,
            be_due_now=be_due_now,
            profit=profit,
            tcfg=tcfg,
            entry=entry,
            sl_raw=_sl_raw,
            direction=str(direction),
            live_sl=_live_sl_for_trail,
            mins=mins,
            move_sl=lambda sl_px: mt5_move_sl_to_breakeven(ticket, sl_px),
            pair_label=str(row.get("pair") or ""),
        )
        # Mode-dispatch: in trailing_atr mode, the trail block is the *only* profit-side
        # exit. Suppress lock ladder and timed-close branches by returning here.
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
                    _mark_timed_close(db_path, row, "mt5", actual_close_price=actual_close_price, live_pnl=live_pnl, reason="TIMED_CLOSE")
                log.info(
                    f"[TIMED_EXIT] TIMED CLOSE: {row['pair']} ticket={ticket} "
                    f"style={style} mins={mins:.0f} profit=${live_pnl:.2f} R={pnl_r} "
                    f"fill={actual_close_price} entry={entry_price}"
                )
                try:
                    telegram_notify.notify_trade_closed(
                        pair=row["pair"],
                        pnl_r=pnl_r,
                        is_win=live_pnl > 0,
                        duration_minutes=mins,
                        exit_reason="TIMED_CLOSE",
                        style=style,
                        exchange="mt5",
                    )
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
            ticket_key=_state_key("mt5", row.get("audit_id"), ticket),
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
                _mark_timed_close(db_path, row, "mt5", actual_close_price=actual_close_price, live_pnl=live_pnl, reason="TIMED_CLOSE")
            log.info(
                f"[TIMED_EXIT] TIMED CLOSE: {row['pair']} ticket={ticket} "
                f"style={style} mins={mins:.0f} profit=${live_pnl:.2f} R={pnl_r} "
                f"fill={actual_close_price} entry={entry_price}"
            )
            try:
                telegram_notify.notify_trade_closed(
                    pair=row["pair"],
                    pnl_r=pnl_r,
                    is_win=live_pnl > 0,
                    duration_minutes=mins,
                    exit_reason="TIMED_CLOSE",
                    style=style,
                    exchange="mt5",
                )
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
            bybit_modify_protective_sl,
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
         if symbols_match(p.get("pair"), pair)),
        None,
    )
    if not live:
        return  # already closed

    profit    = float(live.get("profit", 0))
    cur_price = _live_current_price(live, entry)
    tp        = float(row.get("tp") or live.get("tp") or 0)
    direction = live.get("direction", row.get("direction", "LONG"))
    volume    = float(live.get("volume", 0))

    _sl_raw = float(row.get("sl") or 0)
    _live_sl_for_trail = _safe_float(live.get("sl"))
    state_key = _state_key("bybit", row.get("audit_id"), ticket)

    # Engine D / Scalp Lab: broker TP1/SL + optional milestones; pre_activation
    # giveback when LIVE_PROFIT_PROTECT is enabled (no chandelier / timed close).
    if _is_engine_d_engine(engine):
        if not tcfg.get("scalp_live_profit_protect", True):
            return
        trail_eval = _evaluate_trail(
            row,
            style,
            direction,
            entry,
            _sl_raw,
            cur_price,
            tcfg,
            mins_open=mins,
            state_key=state_key,
            live_sl_for_r=_live_sl_for_trail,
            venue="bybit",
            trail_mode="pre_activation_only",
        )
        if trail_eval.get("action") == "close":
            result = bybit_close_position(pair, direction, volume)
            if result.get("success"):
                pnl_r = _live_pnl_r(row, entry, _sl_raw, volume, profit)
                if db_path:
                    _mark_timed_close(
                        db_path, row, "bybit",
                        actual_close_price=cur_price,
                        live_pnl=profit,
                        reason="TRAIL_PROFIT_ROUNDTRIP",
                    )
                log.info(
                    f"[TIMED_EXIT] ENGINE_D PROFIT_PROTECT: {pair} style={style} "
                    f"mins={mins:.0f} profit=${profit:.2f} R={pnl_r}"
                )
                try:
                    telegram_notify.notify_trade_closed(
                        pair=pair,
                        pnl_r=pnl_r,
                        is_win=profit > 0,
                        duration_minutes=mins,
                        exit_reason="TRAIL_PROFIT_ROUNDTRIP",
                        style=style,
                        exchange="bybit",
                    )
                except Exception:
                    pass
        return

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

    # ── Trailing ATR TP (mode-dispatched single exit policy) ──────────────────
    # Same pattern as MT5: chandelier-only when tp_mode trailing_atr regardless of
    # broker profit sign; broker SL ratchets with the trail; timed-close suppressed.
    if tcfg.get("tp_mode") == "trailing_atr":
        trail_eval = _evaluate_trail(
            row,
            style,
            direction,
            entry,
            _sl_raw,
            cur_price,
            tcfg,
            mins_open=mins,
            state_key=state_key,
            live_sl_for_r=_live_sl_for_trail,
            venue="bybit",
        )
        action = trail_eval.get("action")
        close_reason = "TRAIL_CLOSE"
        if trail_eval.get("reason") == "peak_giveback":
            close_reason = "TRAIL_GIVEBACK"
        elif trail_eval.get("reason") == "profit_roundtrip":
            close_reason = "TRAIL_PROFIT_ROUNDTRIP"

        if action == "close":
            result = bybit_close_position(pair, direction, volume)
            if result.get("success"):
                pnl_r = _live_pnl_r(row, entry, _sl_raw, volume, profit)
                if db_path:
                    _mark_timed_close(
                        db_path, row, "bybit",
                        actual_close_price=cur_price,
                        live_pnl=profit,
                        reason=close_reason,
                    )
                log.info(
                    f"[TIMED_EXIT] {close_reason}: {pair} style={style} "
                    f"mins={mins:.0f} profit=${profit:.2f} R={pnl_r}"
                )
                try:
                    telegram_notify.notify_trade_closed(
                        pair=pair,
                        pnl_r=pnl_r,
                        is_win=profit > 0,
                        duration_minutes=mins,
                        exit_reason=close_reason,
                        style=style,
                        exchange="bybit",
                    )
                except Exception:
                    pass
            return

        if action == "ratchet":
            new_sl = float(trail_eval["new_sl"])
            tr = trail_eval.get("reason")
            cr = float(trail_eval.get("current_r", 0) or 0)
            clear_tp = bool(trail_eval.get("clear_broker_tp"))
            sl_tightens = _protective_sl_tightens(direction, new_sl, _live_sl_for_trail, entry)
            if not ccxt_sym:
                log.warning(
                    f"[TIMED_EXIT] TRAIL RATCHET blocked: Bybit ccxt symbol unresolved "
                    f"pair={pair} style={style} target_sl={new_sl:.5f} R={cr:.2f} "
                    f"trail_reason={tr}"
                )
            elif sl_tightens or clear_tp:
                ratchet_res = bybit_modify_protective_sl(
                    ccxt_sym,
                    direction,
                    new_sl if sl_tightens else 0.0,
                    ref_sl=_live_sl_for_trail,
                    entry=entry,
                    clear_tp=clear_tp,
                )
                if clear_tp and ratchet_res.get("success"):
                    log.info(
                        f"[TIMED_EXIT] Broker TP cleared (chandelier active): "
                        f"{pair} symbol={ccxt_sym} style={style} R={cr:.2f}"
                    )
                _log_trail_ratchet_broker_outcome(
                    venue="BYBIT",
                    pair=str(pair),
                    detail=f"symbol={ccxt_sym}",
                    style=style,
                    new_sl=new_sl,
                    current_r=cr,
                    trail_reason=str(tr),
                    ratchet_res=ratchet_res,
                )
        _try_trailing_mode_breakeven(
            state_key=state_key,
            row=row,
            style=style,
            be_due_now=be_due_now,
            profit=profit,
            tcfg=tcfg,
            entry=entry,
            sl_raw=_sl_raw,
            direction=str(direction),
            live_sl=_live_sl_for_trail,
            mins=mins,
            move_sl=lambda sl_px: bybit_move_sl_to_breakeven(
                ccxt_sym, direction, sl_px, volume,
            ),
            pair_label=str(pair),
        )
        # Mode-dispatch: only the trail manages exits in trailing_atr mode.
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
                pnl_r = _live_pnl_r(row, entry, _sl_raw, volume, profit)
                if db_path:
                    _mark_timed_close(
                        db_path, row, "bybit",
                        actual_close_price=cur_price,
                        live_pnl=profit,
                        reason="TIMED_CLOSE",
                    )
                log.info(
                    f"[TIMED_EXIT] TIMED CLOSE: {pair} style={style} "
                    f"mins={mins:.0f} profit=${profit:.2f} R={pnl_r}"
                )
                try:
                    telegram_notify.notify_trade_closed(
                        pair=pair,
                        pnl_r=pnl_r,
                        is_win=profit > 0,
                        duration_minutes=mins,
                        exit_reason="TIMED_CLOSE",
                        style=style,
                        exchange="bybit",
                    )
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
            ticket_key=_state_key("bybit", row.get("audit_id"), ticket),
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
            pnl_r = _live_pnl_r(row, entry, _sl_raw, volume, profit)
            if db_path:
                _mark_timed_close(
                    db_path, row, "bybit",
                    actual_close_price=cur_price,
                    live_pnl=profit,
                    reason="TIMED_CLOSE",
                )
            log.info(
                f"[TIMED_EXIT] TIMED CLOSE: {pair} style={style} "
                f"mins={mins:.0f} profit=${profit:.2f} R={pnl_r}"
            )
            try:
                telegram_notify.notify_trade_closed(
                    pair=pair,
                    pnl_r=pnl_r,
                    is_win=profit > 0,
                    duration_minutes=mins,
                    exit_reason="TIMED_CLOSE",
                    style=style,
                    exchange="bybit",
                )
            except Exception:
                pass
        else:
            log.warning(
                f"[TIMED_EXIT] Close failed: {pair} — {result.get('error')}"
            )


def _run_check(db_path: str, config_fn) -> None:
    """Single check cycle — called every interval by the monitor thread."""
    global _state_db_path
    tcfg = _get_timed_cfg(config_fn)
    if not tcfg.get("enabled", True):
        return

    # Capture db_path for persistence helpers and hydrate state on first run.
    if _state_db_path is None and db_path:
        _state_db_path = db_path
        _hydrate_state_from_db(db_path)

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
            _warn_unmatched_live_position(pos, venue="mt5")
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
            _warn_unmatched_live_position(pos, venue="bybit")
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
