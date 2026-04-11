"""
timed_exit_monitor.py — Hybrid Triple-Barrier Timed Exit (C-barrier)

Daemon thread that wakes every 30 s and applies timed exit logic to open positions.

Hybrid logic per style:
  SCALP   : move SL to breakeven at 5 min → close at 10 min if still in profit
  INTRADAY: move SL to breakeven at 15 min → close at 30 min if still in profit
  SWING   : move SL to breakeven at 2.5 days → close at 5 days if in profit
            AND price has not yet covered >= 50% of TP distance (exempt if it has)

If profit <= 0 at close-trigger time → do NOT force close (SL is now at breakeven,
protecting the trade). The trade closes naturally via SL or TP.

All windows are read from CONFIG["TIMED_EXIT"] — edit in config.yaml, not here.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("timed_exit")

# Tracks which tickets have already had SL moved to breakeven this session.
# Reset on process restart (intentional — BE state is re-evaluated from position data).
_be_done: set = set()

_DEFAULT_CFG: dict = {
    "enabled": True,
    "check_interval_sec": 30,
    "scalp":    {"breakeven_min": 5,   "close_min": 10},
    "intraday": {"breakeven_min": 15,  "close_min": 30},
    "swing":    {"breakeven_days": 2.5, "close_days": 5.0, "tp_progress_exempt": 0.50},
}


def _get_timed_cfg(config_fn) -> dict:
    cfg = config_fn() if config_fn else {}
    raw = cfg.get("TIMED_EXIT", {})
    merged: dict = {}
    for style in ("scalp", "intraday", "swing"):
        merged[style] = {**_DEFAULT_CFG[style], **(raw.get(style) or {})}
    merged["enabled"] = raw.get("enabled", True)
    merged["check_interval_sec"] = raw.get("check_interval_sec", 30)
    return merged


def _load_recent_audit_rows(db_path: str) -> list[dict]:
    """Load recent non-error audit rows for ticket and fallback position matching."""
    try:
        with sqlite3.connect(db_path, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT id, ticket, pair, style, ts, direction, entry_price, sl, tp, asset_class, exit_time
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

    if pos_ticket:
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
        "ts": audit.get("ts"),
        "direction": audit.get("direction") or position.get("direction"),
        "entry_price": audit.get("entry_price") or position.get("entry") or position.get("entryPrice"),
        "sl": audit.get("sl") or position.get("sl"),
        "tp": audit.get("tp") or position.get("tp"),
        "asset_class": audit.get("asset_class"),
    }


def _mark_timed_close(db_path: str, row: dict, venue: str) -> None:
    """Persist a timed-close marker so outcome logging preserves exit_reason.

    MT5: prefer exact-ticket match; fall back to id-based mark when the audit row was
    matched by proximity (split legs, fallback matching) to avoid silent no-ops.
    Bybit: always mark by id (live ticket is not stable on Bybit).
    """
    audit_id = row.get("audit_id")
    audit_ticket = str(row.get("audit_ticket") or "").strip()
    live_ticket = str(row.get("ticket") or "").strip()

    if venue == "mt5":
        if audit_ticket and audit_ticket == live_ticket:
            # Exact ticket match — most reliable
            query = "UPDATE audit_log SET exit_reason=? WHERE ticket=? AND exit_price IS NULL"
            params = ("TIMED_CLOSE", audit_ticket)
        elif audit_id is not None:
            # Fallback: matched by proximity (split legs, etc.) — use stable row id
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
    style  = (row.get("style") or "intraday").lower()
    entry  = float(row.get("entry_price") or 0)

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
    cur_price = float(live.get("entry", entry))  # best proxy without live tick
    tp        = float(row.get("tp") or live.get("tp") or 0)
    direction = live.get("direction", row.get("direction", "LONG"))

    if style == "swing":
        be_trigger_min  = scfg["breakeven_days"] * 24 * 60
        close_trigger_min = scfg["close_days"] * 24 * 60
        exempt_threshold  = scfg.get("tp_progress_exempt", 0.50)
    else:
        be_trigger_min    = float(scfg["breakeven_min"])
        close_trigger_min = float(scfg["close_min"])
        exempt_threshold  = 1.0  # not used for scalp/intraday

    # ── Step 1: Breakeven trigger ─────────────────────────────────────────────
    if mins >= be_trigger_min and ticket not in _be_done and profit > 0:
        result = mt5_move_sl_to_breakeven(ticket, entry)
        if result.get("success") and not result.get("skipped"):
            _be_done.add(ticket)
            log.info(
                f"[TIMED_EXIT] BE set: {row['pair']} ticket={ticket} "
                f"style={style} mins_open={mins:.1f}"
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
    if mins >= close_trigger_min:
        if profit <= 0:
            log.debug(
                f"[TIMED_EXIT] {row['pair']} ticket={ticket} at {mins:.0f}min "
                f"— not in profit (${profit:.2f}), SL protecting"
            )
            return

        # Swing exemption: if >= 50% toward TP, let TP manage it
        if style == "swing":
            progress = _tp_progress(cur_price, entry, tp, direction)
            if progress >= exempt_threshold:
                log.info(
                    f"[TIMED_EXIT] SWING EXEMPT: {row['pair']} ticket={ticket} "
                    f"TP progress={progress:.0%} — letting TP manage"
                )
                return

        result = mt5_close_position(ticket)
        if result.get("success"):
            if db_path:
                _mark_timed_close(db_path, row, "mt5")
            log.info(
                f"[TIMED_EXIT] TIMED CLOSE: {row['pair']} ticket={ticket} "
                f"style={style} mins={mins:.0f} profit=${profit:.2f}"
            )
            try:
                telegram_notify.notify_trade_closed(
                    pair=row["pair"],
                    pnl_r=0.0,
                    is_win=True,
                    duration_minutes=mins,
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
            bybit_map_symbol,
            bybit_get_positions,
        )
        import telegram_notify
    except ImportError as e:
        log.debug(f"[TIMED_EXIT] Bybit import error: {e}")
        return

    ticket    = str(row["ticket"])
    pair      = row["pair"]
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
         if p.get("pair", "").upper() == pair.upper()),
        None,
    )
    if not live:
        return  # already closed

    profit    = float(live.get("profit", 0))
    cur_price = float(live.get("entry", entry))
    tp        = float(row.get("tp") or live.get("tp") or 0)
    direction = live.get("direction", row.get("direction", "LONG"))
    volume    = float(live.get("volume", 0))

    if style == "swing":
        be_trigger_min    = scfg["breakeven_days"] * 24 * 60
        close_trigger_min = scfg["close_days"] * 24 * 60
        exempt_threshold  = scfg.get("tp_progress_exempt", 0.50)
    else:
        be_trigger_min    = float(scfg["breakeven_min"])
        close_trigger_min = float(scfg["close_min"])
        exempt_threshold  = 1.0

    ccxt_sym = bybit_map_symbol(pair)

    # ── Step 1: Breakeven trigger ─────────────────────────────────────────────
    if mins >= be_trigger_min and ticket not in _be_done and profit > 0 and ccxt_sym:
        result = bybit_move_sl_to_breakeven(ccxt_sym, direction, entry, volume)
        if result.get("success"):
            _be_done.add(ticket)
            log.info(
                f"[TIMED_EXIT] BE set: {pair} style={style} mins_open={mins:.1f}"
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
    if mins >= close_trigger_min:
        if profit <= 0:
            log.debug(
                f"[TIMED_EXIT] {pair} at {mins:.0f}min — not in profit (${profit:.2f}), SL protecting"
            )
            return

        if style == "swing":
            progress = _tp_progress(cur_price, entry, tp, direction)
            if progress >= exempt_threshold:
                log.info(
                    f"[TIMED_EXIT] SWING EXEMPT: {pair} TP progress={progress:.0%}"
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
                telegram_notify.notify_trade_closed(
                    pair=pair,
                    pnl_r=0.0,
                    is_win=True,
                    duration_minutes=mins,
                )
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
