"""
telegram_bot.py — Interactive Telegram command centre for Sentinel Pro

Commands (see /help for full list):
  /scan, /engineb, /enginec, /scalp — engine scans
  /signal, /execute — pair analysis and execution
  /positions, /trade, /close — open trades
  /status, /guardian, /feeds, /forensics, /lastscan — ops and health
  /balance, /pnl, /auto, /decay, /bt — account and tools
  /kill, /resume — emergency controls
"""

import logging
import threading
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import quote

from telegram_notify import (
    get_configured_chat_ids,
    get_delivery_state,
    get_runtime_config,
    send_signal_alert,
)

log = logging.getLogger("sentinel")

# Pending signals: signal_key -> {signal_dict, ts}
_pending_signals: dict = {}
_pending_confirmations: dict = {}  # pair -> {exchange, direction, volume, ticket}

_BASE = "http://127.0.0.1:5000"
_TIMEOUT_FAST = 10   # status/balance calls
_TIMEOUT_SCAN = 120  # scan calls
_TIMEOUT_EXEC = 30   # execution calls


def _telegram_command_specs() -> list[tuple[str, str]]:
    return [
        ("scan", "Engine A scan by asset class"),
        ("engineb", "Engine B naked-structure scan"),
        ("enginec", "Engine C consensus scan"),
        ("scalp", "Engine D scalp scan"),
        ("signal", "Analyse one pair"),
        ("positions", "Open trades and P&L"),
        ("status", "System health"),
        ("forensics", "Signal trust summary"),
        ("balance", "MT5 and Bybit balances"),
        ("pnl", "Performance stats"),
        ("auto", "Toggle auto-trader"),
        ("decay", "Score decay for open trades"),
        ("bt", "Quick backtest"),
        ("execute", "Scan pair and execute trade"),
        ("guardian", "Guardian and shield status"),
        ("feeds", "Live candle feed diagnostics"),
        ("lastscan", "Recent scan preview"),
        ("kill", "Emergency stop"),
        ("resume", "Lift kill switch"),
        ("help", "Show command help"),
    ]

# ── Pair name fuzzy matching ──────────────────────────────────────────────────

_PAIR_ALIASES = {
    "btc": "BTC/USDT", "eth": "ETH/USDT", "sol": "SOL/USDT",
    "xrp": "XRP/USDT", "ada": "ADA/USDT", "doge": "DOGE/USDT",
    "link": "LINK/USDT", "ltc": "LTC/USDT", "bnb": "BNB/USDT",
    "sui": "SUI/USDT", "apt": "APT/USDT", "near": "NEAR/USDT",
    "inj": "INJ/USDT", "render": "RENDER/USDT", "matic": "POL/USDT", "pol": "POL/USDT",
    "avax": "AVAX/USDT", "dot": "DOT/USDT",
    "eurusd": "EUR/USD", "gbpusd": "GBP/USD", "usdjpy": "USD/JPY",
    "audusd": "AUD/USD", "nzdusd": "NZD/USD", "usdcad": "USD/CAD",
    "usdchf": "USD/CHF", "eurjpy": "EUR/JPY", "gbpjpy": "GBP/JPY",
    "audjpy": "AUD/JPY", "eurgbp": "EUR/GBP", "euraud": "EUR/AUD",
    "gbpaud": "GBP/AUD", "usdzar": "USD/ZAR",
    "gold": "XAU/USD", "xauusd": "XAU/USD", "silver": "XAG/USD",
    "oil": "WTI Oil", "wti": "WTI Oil",
    "spx": "S&P 500", "sp500": "S&P 500", "nas": "Nasdaq",
    "nasdaq": "Nasdaq", "dow": "Dow Jones",
    "usdx": "USDX", "eurx": "EURX", "jpyx": "JPYX", "jpx": "JPYX",
    "aapl": "AAPL", "tsla": "TSLA", "nvda": "NVDA", "msft": "MSFT",
}


def _resolve_pair(raw: str) -> Optional[str]:
    """Fuzzy match user input to Athena display name."""
    s = raw.strip().upper()
    # Try direct match first (e.g. "ETH/USDT")
    if "/" in s or " " in s:
        return s
    # Try lowercase alias table
    lower = raw.strip().lower()
    if lower in _PAIR_ALIASES:
        return _PAIR_ALIASES[lower]
    # Try constructing USDT pair
    if not s.endswith("USDT") and not s.endswith("USD"):
        candidate = f"{s}/USDT"
        return candidate
    return s


def _prune_pending():
    """Remove signal entries older than 30 minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    stale = [k for k, v in _pending_signals.items()
             if v.get("ts", datetime.now(timezone.utc)) < cutoff]
    for k in stale:
        del _pending_signals[k]


def _store_pending_signal(sig: dict) -> str:
    """Store a signal for Telegram callback execution and return its callback key."""
    _prune_pending()
    signal_key = f"{sig.get('pair', sig.get('display', '?'))}_{sig.get('direction', '?')}_{int(datetime.now().timestamp())}"
    _pending_signals[signal_key] = {"signal": sig, "ts": datetime.now(timezone.utc)}
    return signal_key


def _safe_json(resp) -> dict:
    try:
        data = resp.json()
    except Exception:
        text = (getattr(resp, "text", "") or "")[:200]
        sc = getattr(resp, "status_code", "?")
        return {"error": f"Bad response: {text or 'HTTP ' + str(sc)}"}
    if not getattr(resp, "ok", True):
        if isinstance(data, dict):
            data.setdefault("error", f"HTTP {resp.status_code}")
        else:
            data = {"error": f"HTTP {resp.status_code}"}
    return data


def _build_quick_execute_payload(sig: dict, style: str) -> dict:
    signal = dict(sig or {})
    signal["style"] = style
    return {
        "signal": signal,
        "pip_mode": style,
        "sizing_override": 1.0,
        "execution_context": "manual",
    }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_money(value, suffix: str = "") -> str:
    n = _safe_float(value)
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.2f}{suffix}"


def _fmt_minutes(minutes) -> str:
    mins = _safe_float(minutes)
    if mins >= 1440:
        return f"{mins / 1440:.1f}d"
    if mins >= 60:
        return f"{mins / 60:.1f}h"
    return f"{mins:.0f}m"


def _position_profit(pos: dict) -> float:
    return _safe_float(pos.get("profit", pos.get("unrealizedPnl", 0)))


def _position_pair(pos: dict) -> str:
    return str(pos.get("pair") or pos.get("display") or pos.get("symbol") or "?")


def _position_volume(pos: dict) -> float:
    return _safe_float(pos.get("volume", pos.get("contracts", 0)))


def _position_ticket(pos: dict) -> str:
    return str(pos.get("ticket") or "")


def _position_exchange(pos: dict, fallback: str = "") -> str:
    return str(pos.get("exchange") or fallback or "").lower()


def _position_close_key(pos: dict, exchange: str) -> str:
    pair = _position_pair(pos)
    direction = str(pos.get("direction") or "")
    volume = _position_volume(pos)
    ticket = _position_ticket(pos)
    return f"close_{exchange}_{pair}_{direction}_{volume}_{ticket}"


def _position_matches_pair(pos: dict, pair: str) -> bool:
    want = (pair or "").upper()
    return _position_pair(pos).upper() == want


def _filter_positions(all_pos: list[tuple[dict, str]], mode: str) -> list[tuple[dict, str]]:
    mode = (mode or "all").lower()
    if mode in ("win", "wins", "winning", "green"):
        return [(p, ex) for p, ex in all_pos if _position_profit(p) > 0]
    if mode in ("loss", "losses", "losing", "red"):
        return [(p, ex) for p, ex in all_pos if _position_profit(p) < 0]
    if mode in ("flat", "breakeven", "be"):
        return [(p, ex) for p, ex in all_pos if _position_profit(p) == 0]
    return all_pos


def _summarize_positions(all_pos: list[tuple[dict, str]]) -> dict:
    total = sum(_position_profit(p) for p, _ in all_pos)
    winning = sum(1 for p, _ in all_pos if _position_profit(p) > 0)
    losing = sum(1 for p, _ in all_pos if _position_profit(p) < 0)
    flat = max(0, len(all_pos) - winning - losing)
    return {
        "count": len(all_pos),
        "total": total,
        "winning": winning,
        "losing": losing,
        "flat": flat,
    }


def _audit_orphan_reconcileable(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("broker_live"):
        return False
    return row.get("close_action_enabled") is not False


def _fmt_audit_orphan_line(row: dict) -> str:
    pair = str(row.get("pair") or row.get("symbol") or "?")
    ticket = str(row.get("ticket") or row.get("id") or "?")
    direction = str(row.get("direction") or "?")
    style = str(row.get("style") or row.get("audit_engine") or "—")
    state = "broker live" if row.get("broker_live") else "audit-only"
    return f"• *{pair}* {direction} ticket `{ticket}` | `{style}` | {state}"


def _fmt_exit_management_lines(pos: dict) -> list[str]:
    """Timed-exit / trail / Engine D management lines for position cards."""
    lines: list[str] = []
    is_engine_d = bool(pos.get("is_engine_d"))
    timed_tp_mode = str(pos.get("timed_tp_mode") or "").lower()
    timed_close_enabled = pos.get("timed_close_style_enabled")

    if is_engine_d or pos.get("live_milestone_management"):
        tp_partial = pos.get("tp_partial")
        if tp_partial not in (None, ""):
            lines.append(f"TP partial (1R): `{tp_partial}`")
        if pos.get("live_milestone_management"):
            lines.append("Mgmt: `milestone` (Engine D)")

    if timed_tp_mode == "trailing_atr" and not is_engine_d:
        act_r = pos.get("trail_activation_r")
        if act_r is not None:
            lines.append(f"Exit: `chandelier trail` @ `{act_r}R` activation")
        return lines

    be_trigger = pos.get("be_trigger_min")
    close_trigger = pos.get("close_trigger_min")
    if (
        be_trigger is not None
        and close_trigger is not None
        and timed_close_enabled is not False
    ):
        be = _fmt_minutes(pos.get("mins_to_be", 0))
        close = _fmt_minutes(pos.get("mins_to_close", 0))
        be_state = "hit" if pos.get("be_reached") else f"in {be}"
        close_state = "hit" if pos.get("close_reached") else f"in {close}"
        lines.append(f"Timed exit: BE `{be_state}` | close `{close_state}`")
    elif (
        timed_tp_mode == "fixed"
        and not is_engine_d
        and timed_close_enabled is False
    ):
        lines.append("Timed close: `disabled` for style")

    return lines


def _fmt_feed_row(row: dict) -> str:
    pair = str(row.get("display") or row.get("pair") or row.get("symbol") or "?")
    tf = str(row.get("timeframe") or row.get("tf") or "?")
    stale = str(row.get("stale_status") or row.get("stalenessSeverity") or "?")
    lag = row.get("lag_seconds")
    lag_note = f" lag `{lag}s`" if lag is not None else ""
    consistency = row.get("consistency") if isinstance(row.get("consistency"), dict) else {}
    c_status = consistency.get("status")
    c_note = f" | consistency `{c_status}`" if c_status else ""
    return f"• *{pair}* `{tf}` — `{stale}`{lag_note}{c_note}"


def _fmt_signal_card(sig: dict) -> str:
    """Format a signal dict as a clean Telegram card."""
    pair = sig.get("pair", sig.get("display", "?"))
    direction = sig.get("direction", "?")
    score = sig.get("confluenceScore", 0)
    max_score = sig.get("maxScore", 3.0)
    pct = round(score / max_score * 100) if max_score else 0
    regime = sig.get("trendState", sig.get("regimeName", "?"))
    price = sig.get("price", 0)
    sl = sig.get("sl", 0)
    tp1 = sig.get("tp1", 0)
    rr = sig.get("rr1", 0)
    dir_emoji = "🟢" if direction == "LONG" else "🔴"

    sl_pct = round(abs(price - sl) / price * 100, 2) if price and sl else 0
    tp_pct = round(abs(tp1 - price) / price * 100, 2) if price and tp1 else 0

    lines = [
        f"{dir_emoji} *{pair} — {direction}*",
        f"━━━━━━━━━━━━━━━",
        f"Score:  `{score:.2f} / {max_score:.1f}`  ({pct}%)",
        f"Regime: `{regime}`",
        f"Entry:  `{price}`",
        f"SL:     `{sl}`  (-{sl_pct}%)",
        f"TP1:    `{tp1}`  (+{tp_pct}%, RR 1:{rr:.1f})" if rr else f"TP1:    `{tp1}`",
    ]
    btc_bias = sig.get("btcBias")
    if btc_bias and btc_bias != "n/a":
        b_emoji = "✓" if (
            (direction == "LONG" and btc_bias == "bullish") or
            (direction == "SHORT" and btc_bias == "bearish")
        ) else "✗"
        lines.append(f"BTC:    `{btc_bias.capitalize()}` {b_emoji}")
    style = sig.get("style")
    if style:
        lines.append(f"Style:  `{style}`")
    score_group = sig.get("score_group") or sig.get("scoreGroup")
    if score_group:
        lines.append(f"Group:  `{score_group}`")
    freshness = sig.get("dataFreshness")
    if isinstance(freshness, dict):
        f_status = freshness.get("status") or freshness.get("allowed")
        f_reason = freshness.get("reason") or freshness.get("blockReason")
        if f_status is not None:
            if isinstance(f_status, bool):
                f_label = "OK" if f_status else "BLOCKED"
            else:
                f_label = str(f_status)
            lines.append(f"Fresh:  `{f_label}`" + (f" — {f_reason}" if f_reason else ""))
    return "\n".join(lines)


def _fmt_position_card(pos: dict) -> str:
    """Format an open position as a Telegram card."""
    pair = _position_pair(pos)
    direction = pos.get("direction", "?")
    entry = pos.get("entry", pos.get("entryPrice", 0))
    profit = _position_profit(pos)
    sl = pos.get("sl", 0)
    tp = pos.get("tp", 0)
    vol = pos.get("volume", pos.get("contracts", 0))
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    pnl_emoji = "📈" if profit >= 0 else "📉"
    pnl_sign = "+" if profit >= 0 else ""

    lines = [
        f"{dir_emoji} *{pair}* — {direction}",
        f"Entry: `{entry}` | Vol: `{vol}`",
        f"{pnl_emoji} P&L: `{pnl_sign}{profit:.2f} USDT`",
    ]
    if sl:
        lines.append(f"SL: `{sl}` | TP: `{tp}`")
    return "\n".join(lines)


def _fmt_position_detail_card(pos: dict, exchange: str = "") -> str:
    """Format an enriched open-trade card from /api/open-trades-timed or broker fallback."""
    pair = _position_pair(pos)
    direction = str(pos.get("direction") or "?")
    exchange = (_position_exchange(pos, exchange) or "?").upper()
    profit = _position_profit(pos)
    result = "WINNING" if profit > 0 else "LOSING" if profit < 0 else "FLAT"
    pnl_icon = "WIN" if profit > 0 else "LOSS" if profit < 0 else "FLAT"
    dir_icon = "LONG" if direction == "LONG" else "SHORT"
    entry = pos.get("entry", pos.get("entryPrice", 0))
    current = pos.get("markPrice") or pos.get("lastPrice") or pos.get("price")
    sl = pos.get("sl", 0)
    tp = pos.get("tp", 0)
    volume = _position_volume(pos)
    risk_amount = pos.get("risk_amount")
    style = str(pos.get("style") or "").upper()
    if not style and pos.get("is_engine_d"):
        style = "SCALP"
    elif not style and pos.get("audit_engine"):
        style = str(pos.get("audit_engine")).upper()
    ticket = _position_ticket(pos)

    lines = [
        f"*{pair}* - {dir_icon} - {result}",
        f"Exchange: `{exchange}`" + (f" | Style: `{style}`" if style else ""),
        f"P&L: `{_fmt_money(profit)}` ({pnl_icon})",
        f"Entry: `{entry}` | Vol: `{volume}`",
    ]

    if current:
        lines.append(f"Now: `{current}`")
    if sl or tp:
        lines.append(f"SL: `{sl}` | TP: `{tp}`")
    if risk_amount is not None:
        lines.append(f"Risk: `{risk_amount}`")
    if pos.get("mins_open") is not None:
        lines.append(f"Open: `{_fmt_minutes(pos.get('mins_open'))}`")
    lines.extend(_fmt_exit_management_lines(pos))
    if ticket:
        lines.append(f"Ticket: `{ticket}`")
    return "\n".join(lines)


def _fmt_engine_c_card(sig: dict) -> str:
    pair = sig.get("display") or sig.get("pair") or sig.get("symbol") or "?"
    direction = sig.get("direction") or "?"
    verdict = sig.get("verdict") or "?"
    tier = sig.get("tier") or "?"
    conviction = _safe_float(sig.get("conviction"))
    trade = "YES" if sig.get("trade") else "NO"
    a_raw = sig.get("engine_a_raw") or {}
    b_raw = sig.get("engine_b_raw") or {}
    lines = [
        f"*{pair}* - {direction} - Engine C",
        f"Verdict: `{verdict}` | Tier: `{tier}` | Trade: `{trade}`",
        f"Conviction: `{conviction:.0%}`",
    ]
    if a_raw:
        a_score = a_raw.get("score")
        a_max = a_raw.get("maxScore")
        lines.append(f"A: `{a_raw.get('direction') or '-'} {a_score}/{a_max}`")
    if b_raw:
        lines.append(f"B: `{b_raw.get('direction') or '-'} {b_raw.get('score')}/{b_raw.get('max_possible')}`")
    if sig.get("sl") or sig.get("tp"):
        lines.append(f"SL: `{sig.get('sl')}` | TP: `{sig.get('tp')}`")
    skip_reason = sig.get("skip_reason") or sig.get("reason") or sig.get("conflict_reason")
    if skip_reason and str(sig.get("verdict") or "").lower() in ("conflict", "skip", "skipped"):
        lines.append(f"Note: `{str(skip_reason)[:120]}`")
    return "\n".join(lines)


def _fmt_engine_b_card(sig: dict) -> str:
    pair = sig.get("display") or sig.get("pair") or sig.get("symbol") or "?"
    direction = sig.get("direction") or "?"
    naked = sig.get("naked_data") or {}
    score = sig.get("confluenceScore", naked.get("score", 0))
    max_score = sig.get("maxScore", naked.get("max_possible", 0))
    pct = sig.get("score_pct", naked.get("pct", naked.get("score_pct", 0)))
    style = sig.get("style") or naked.get("style") or "?"
    regime = sig.get("regime") or naked.get("regime") or sig.get("trendState") or "?"
    rr = sig.get("rr1", naked.get("rr_used_for_gate", naked.get("rr", 0)))
    sl = sig.get("sl") or naked.get("execution_sl") or naked.get("recommended_stop_loss")
    tp = sig.get("tp1") or naked.get("execution_tp") or naked.get("recommended_take_profit")
    zone_tf = sig.get("zone_tf") or naked.get("zone_tf")
    entry_tf = sig.get("entry_tf") or naked.get("entry_tf")
    tf_note = f" | TF `{zone_tf}/{entry_tf}`" if zone_tf or entry_tf else ""
    return "\n".join([
        f"*{pair}* - {direction} - Engine B",
        f"Score: `{_safe_float(score):.1f}/{_safe_float(max_score):.1f}` ({_safe_float(pct):.0f}%)",
        f"Style: `{style}` | Regime: `{regime}`{tf_note}",
        f"Entry: `{sig.get('price', naked.get('current_price', '?'))}`",
        f"SL: `{sl}` | TP: `{tp}` | RR: `{_safe_float(rr):.2f}`",
    ])


def _fmt_scalp_card(sig: dict) -> str:
    pair = sig.get("pair") or sig.get("display") or "?"
    direction = sig.get("direction") or "?"
    grade = sig.get("ai_grade") or "?"
    score = sig.get("ai_score")
    price = sig.get("price") or sig.get("entry")
    sl = sig.get("sl")
    tp1 = sig.get("tp1")
    rr = sig.get("rr1") or sig.get("rr")
    zone = sig.get("zone_type") or "?"
    trigger = sig.get("trigger_type") or "?"
    gate = sig.get("gate_result") or sig.get("gateResult")
    executable = sig.get("executable")
    strict_pass = sig.get("strict_fabio_pass")
    strict_reason = sig.get("strict_fabio_reason")
    structural_tp = sig.get("structural_tp")
    sessions = sig.get("sessions_active") or []
    session = sig.get("session")
    fidelity = sig.get("data_fidelity")

    lines = [
        f"*{pair}* - {direction} - Scalp",
        f"Grade: `{grade}` | Score: `{score}` | RR: `{rr}`",
        f"Entry: `{price}` | SL: `{sl}` | TP1: `{tp1}`",
        f"Zone: `{zone}` | Trigger: `{trigger}`",
    ]
    if gate is not None or executable is not None:
        exec_label = "YES" if executable else "NO"
        lines.append(f"Gate: `{gate or '?'}` | Exec: `{exec_label}`")
    if strict_pass is not None:
        sf = "PASS" if strict_pass else "FAIL"
        reason_note = f" — `{str(strict_reason)[:80]}`" if strict_reason and not strict_pass else ""
        lines.append(f"Strict Fabio: `{sf}`{reason_note}")
    if structural_tp:
        lines.append(f"Struct TP: `{structural_tp}`")
    sess_note = ", ".join(str(s) for s in sessions[:3]) if sessions else session
    if sess_note:
        lines.append(f"Session: `{sess_note}`")
    if fidelity:
        lines.append(f"Fidelity: `{str(fidelity)[:80]}`")
    return "\n".join(lines)


def _fetch_open_positions_sync(req) -> tuple[list[tuple[dict, str]], dict, list]:
    """Fetch enriched open trades, falling back to broker status endpoints if needed."""
    meta = {"source": "open-trades-timed", "error": ""}
    timed = _safe_json(req.get(f"{_BASE}/api/open-trades-timed", timeout=_TIMEOUT_FAST))
    if not timed.get("error") and isinstance(timed.get("positions"), list):
        out = []
        for pos in timed.get("positions", []):
            exchange = _position_exchange(pos) or "mt5"
            out.append((pos, exchange))
        audit_unresolved = list(timed.get("audit_unresolved") or [])
        meta["audit_unresolved_count"] = int(
            timed.get("audit_unresolved_count") or len(audit_unresolved)
        )
        return out, meta, audit_unresolved

    meta["source"] = "broker-fallback"
    meta["error"] = timed.get("error", "")
    meta["audit_unresolved_count"] = 0
    mt5_resp = _safe_json(req.get(f"{_BASE}/api/mt5-positions", timeout=_TIMEOUT_FAST))
    bybit_resp = _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST))
    out = []
    for pos in mt5_resp.get("positions", []) or []:
        pos.setdefault("exchange", "mt5")
        out.append((pos, "mt5"))
    for pos in bybit_resp.get("positions", []) or []:
        pos.setdefault("exchange", "bybit")
        out.append((pos, "bybit"))
    if not out and (mt5_resp.get("error") or bybit_resp.get("error")):
        meta["error"] = mt5_resp.get("error") or bybit_resp.get("error") or meta["error"]
    return out, meta, []


def _is_transient_telegram_error(error: object) -> bool:
    """Return True for Telegram polling transport errors that PTB can retry."""
    if error is None:
        return False
    name = error.__class__.__name__
    module = getattr(error.__class__, "__module__", "")
    if module.startswith("telegram.error") and name in {
        "NetworkError",
        "TimedOut",
        "RetryAfter",
    }:
        return True
    cause = getattr(error, "__cause__", None) or getattr(error, "__context__", None)
    cause_name = cause.__class__.__name__ if cause is not None else ""
    cause_module = getattr(cause.__class__, "__module__", "") if cause is not None else ""
    return cause_module.startswith(("httpx", "httpcore")) and cause_name in {
        "ReadError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "RemoteProtocolError",
    }


async def _telegram_error_handler(update, context) -> None:
    """Prevent PTB from emitting unhandled-error logs for retryable polling failures."""
    error = getattr(context, "error", None)
    if _is_transient_telegram_error(error):
        log.warning(
            "[TELEGRAM] Transient polling/update error handled: %s: %s",
            error.__class__.__name__,
            error,
        )
        return

    chat = getattr(getattr(update, "effective_chat", None), "id", None)
    if isinstance(error, BaseException):
        log.error(
            "[TELEGRAM] Update handler failed%s: %s: %s",
            f" chat_id={chat}" if chat is not None else "",
            error.__class__.__name__,
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
        return
    log.error("[TELEGRAM] Update handler failed with non-exception error: %r", error)


def start_telegram_bot():
    """Start the Telegram bot in a background thread."""
    config = get_runtime_config()
    if not config.get("enabled"):
        log.info("[TELEGRAM] Bot disabled via TELEGRAM.enabled")
        return
    token = config.get("token", "")
    chat_ids = get_configured_chat_ids(config)
    if not token or not chat_ids:
        log.warning("[TELEGRAM] Bot token or chat_id not set — bot disabled")
        return
    t = threading.Thread(target=_run_bot, args=(token, chat_ids), daemon=True)
    t.start()
    log.warning("[TELEGRAM] Bot started in background thread")


def _run_bot(token: str, chat_ids: list[str]):
    import traceback
    import time
    while True:
        try:
            _build_and_run(token, chat_ids)
            break  # Exit cleanly if it ever returns normally
        except Exception as e:
            log.error(f"[TELEGRAM] Bot failed: {e}\n{traceback.format_exc()}")
            log.info("[TELEGRAM] Restarting bot in 15 seconds due to error...")
            time.sleep(15)


def _build_and_run(token: str, chat_ids: list[str]):
    import requests as req
    from telegram import BotCommand, MenuButtonCommands, Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

    async def _configure_bot_menu(application):
        commands = [BotCommand(cmd, desc) for cmd, desc in _telegram_command_specs()]
        await application.bot.set_my_commands(commands)
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    app = (
        Application.builder()
        .token(token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .post_init(_configure_bot_menu)
        .build()
    )
    allowed_chat_ids = {str(chat_id) for chat_id in chat_ids if str(chat_id).strip()}
    app.bot_data["chat_id"] = next(iter(allowed_chat_ids), "")
    app.bot_data["allowed_chat_ids"] = allowed_chat_ids

    def _guard(update: Update) -> bool:
        """Reject messages from unknown chat IDs silently."""
        chat = getattr(update, "effective_chat", None)
        return bool(chat and str(chat.id) in allowed_chat_ids)

    async def _run_in_thread(fn):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn)

    async def _send_position_card(chat_id_val, pos: dict, exchange: str, context):
        pair = _position_pair(pos)
        direction = str(pos.get("direction") or "")
        close_key = _position_close_key(pos, exchange)
        _pending_confirmations[close_key] = {
            "exchange": exchange,
            "pair": pair,
            "direction": direction,
            "volume": _position_volume(pos),
            "ticket": _position_ticket(pos),
        }

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Refresh", callback_data=f"trade_refresh:{close_key}"),
                InlineKeyboardButton("Analyze", callback_data=f"trade_analyze:{pair}"),
            ],
            [
                InlineKeyboardButton("Decay", callback_data=f"decay_pair:{pair}"),
                InlineKeyboardButton("Close", callback_data=f"close_request:{close_key}"),
            ],
        ])
        await context.bot.send_message(
            chat_id=chat_id_val,
            text=_fmt_position_detail_card(pos, exchange),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    # ── /help ─────────────────────────────────────────────────────────────────

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        text = (
            "🤖 *Sentinel Pro — Commands*\n\n"
            "`/scan crypto`       Engine A scan by class\n"
            "`/engineb crypto`    Engine B naked scan\n"
            "`/enginec forex`      Engine C consensus scan\n"
            "`/scalp BTC/USDT`     Engine D scalp scan\n"
            "`/signal ETH/USDT`   Analyse a single pair\n"
            "`/execute ETH/USDT`  Scan and execute (SCALP/INTRADAY/SWING buttons)\n"
            "`/positions winning` Open trades + P&L\n"
            "`/trade ETH/USDT`     One open trade card\n"
            "`/close ETH/USDT`    Close a position\n"
            "`/status`            System health\n"
            "`/guardian`          Guardian + shield detail\n"
            "`/feeds [SYMBOL]`    Candle freshness diagnostics\n"
            "`/lastscan`          Recent scan preview\n"
            "`/forensics`         Trust/degradation summary\n"
            "`/balance`           MT5 + Bybit balances\n"
            "`/pnl`               Performance stats\n"
            "`/auto on|off`       Toggle auto-trader\n"
            "`/decay`             Score decay for open trades\n"
            "`/bt EUR/USD`        Quick backtest result\n"
            "`/kill`              Emergency stop\n"
            "`/resume`            Lift kill switch\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    # ── /status ───────────────────────────────────────────────────────────────

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            mt5 = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/mt5-status", timeout=_TIMEOUT_FAST)))
            bybit = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST)))
            auto = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/auto-trade", timeout=_TIMEOUT_FAST)))

            mt5_icon = "🟢" if mt5.get("connected") else "🔴"
            bybit_icon = "🟢" if bybit.get("connected") else "🔴"
            auto_icon = "🟢 ON" if auto.get("enabled") else "⚫ OFF"
            mt5_pos = mt5.get("openPositions", 0)
            bybit_pos = bybit.get("openPositions", 0)
            trades_today = auto.get("tradesToday", 0)
            max_daily = auto.get("maxDaily", 3)
            health = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/health", timeout=_TIMEOUT_FAST)))
            ks = health.get("killSwitch") if isinstance(health, dict) else None
            ks_note = f"\n🛑 Kill switch: `{'ON' if ks else 'OFF'}`" if ks is not None else ""

            mt5_bal = (mt5.get("account") or {}).get("balance", 0)
            bybit_bal = (bybit.get("account") or {}).get("balance", 0)

            forensic = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/forensics/summary", timeout=_TIMEOUT_FAST)))
            f_overall = str(forensic.get("overall", "unknown")).upper()
            f_icon = "🟢" if f_overall == "HEALTHY" else ("🔴" if f_overall == "CRITICAL" else "🟡")

            guardian = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/guardian/status", timeout=_TIMEOUT_FAST)))
            g_overall = str(guardian.get("overall", "unknown")).upper()
            g_icon = "🟢" if g_overall == "HEALTHY" else ("🔴" if g_overall == "CRITICAL" else "🟡")
            shield = guardian.get("shield") if isinstance(guardian.get("shield"), dict) else {}
            cb_open = shield.get("circuit_breaker_open")
            cb_note = ""
            if cb_open is True:
                cb_note = "\n⚡ Shield circuit breaker: `OPEN`"
            elif cb_open is False:
                cb_note = "\n⚡ Shield circuit breaker: `closed`"
            div = guardian.get("divergence") if isinstance(guardian.get("divergence"), dict) else {}
            div_crit = div.get("critical_count")
            div_note = f"\n📉 Divergence critical: `{div_crit}`" if div_crit not in (None, 0) else ""

            delivery = get_delivery_state()
            tg_note = (
                f"\n📨 Telegram queue: `{delivery.get('queue_size', 0)}` "
                f"(sent `{delivery.get('sent', 0)}` / fail `{delivery.get('failed', 0)}`)"
            )
            if delivery.get("last_error"):
                tg_note += f"\n📨 Last TG error: `{str(delivery.get('last_error'))[:80]}`"

            text = (
                f"📊 *Sentinel Pro Status*\n\n"
                f"{mt5_icon} *MT5*: `{mt5_bal:,.2f}` | {mt5_pos} pos\n"
                f"{bybit_icon} *Bybit*: `{bybit_bal:,.2f} USDT` | {bybit_pos} pos\n\n"
                f"🤖 Auto-trader: *{auto_icon}*\n"
                f"📅 Trades today: `{trades_today}/{max_daily}`\n"
                f"{f_icon} Forensics: *{f_overall}*\n"
                f"{g_icon} Guardian: *{g_overall}*{cb_note}{div_note}{ks_note}{tg_note}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Athena offline — check server\n`{str(e)[:100]}`", parse_mode="Markdown")

    # ── /forensics ────────────────────────────────────────────────────────────

    async def cmd_forensics(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/forensics/summary?hours=24", timeout=_TIMEOUT_FAST)))
            if resp.get("error"):
                await update.message.reply_text(f"⚠️ Forensics unavailable: {resp['error']}")
                return
            overall = str(resp.get("overall", "unknown")).upper()
            overall_icon = "🟢" if overall == "HEALTHY" else ("🔴" if overall == "CRITICAL" else "🟡")
            lines = resp.get("telegram_brief") or []
            if not lines:
                lines = ["No forensic detail available"]
            msg = (
                f"{overall_icon} *Forensic Observability — {overall}*\n\n"
                + "\n".join(lines[:4])
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    async def cmd_guardian(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            resp = await _run_in_thread(
                lambda: _safe_json(req.get(f"{_BASE}/api/guardian/status", timeout=_TIMEOUT_FAST))
            )
            overall = str(resp.get("overall", "unknown")).upper()
            icon = "🟢" if overall == "HEALTHY" else ("🔴" if overall == "CRITICAL" else "🟡")
            guardian = resp.get("guardian") if isinstance(resp.get("guardian"), dict) else {}
            shield = resp.get("shield") if isinstance(resp.get("shield"), dict) else {}
            divergence = resp.get("divergence") if isinstance(resp.get("divergence"), dict) else {}
            forensics = resp.get("forensics") if isinstance(resp.get("forensics"), dict) else {}

            g_pass = guardian.get("passed")
            g_status = "PASS" if g_pass is True else ("FAIL" if g_pass is False else "?")
            failures = guardian.get("failures") or []
            fail_note = f"\nGuardian checks: `{g_status}`"
            if failures:
                fail_note += f"\nFailures: `{'; '.join(str(f) for f in failures[:3])}`"

            cb = shield.get("circuit_breaker_open")
            shield_note = f"\nShield CB: `{'OPEN' if cb else 'closed' if cb is False else '?'}`"
            div_note = (
                f"\nDivergence: `{divergence.get('divergence_count', 0)}` "
                f"(critical `{divergence.get('critical_count', 0)}`)"
            )
            f_note = ""
            brief = forensics.get("telegram_brief") if isinstance(forensics, dict) else None
            if isinstance(brief, list) and brief:
                f_note = "\n" + "\n".join(str(line) for line in brief[:3])

            await update.message.reply_text(
                f"{icon} *Guardian — {overall}*{fail_note}{shield_note}{div_note}{f_note}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(
                f"Guardian unavailable: `{str(e)[:120]}`",
                parse_mode="Markdown",
            )

    async def cmd_feeds(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        symbols: list[str] = []
        if context.args:
            symbols = [_resolve_pair(" ".join(context.args)) or ""]
            symbols = [s for s in symbols if s]
        else:
            try:
                all_pos, _meta, _audit = await _run_in_thread(
                    lambda: _fetch_open_positions_sync(req)
                )
                symbols = list(dict.fromkeys(_position_pair(p) for p, _ in all_pos))[:5]
            except Exception:
                symbols = []

        params = {}
        if symbols:
            params["symbols"] = ",".join(symbols)
        try:
            url = f"{_BASE}/api/live-feed-diagnostics"
            if params:
                url += "?" + "&".join(
                    f"{k}={quote(str(v), safe=',')}" for k, v in params.items()
                )
            resp = await _run_in_thread(
                lambda: _safe_json(req.get(url, timeout=_TIMEOUT_SCAN))
            )
            if resp.get("error"):
                await update.message.reply_text(f"Feeds error: {resp['error']}")
                return
            rows = resp.get("rows") or []
            if not rows:
                await update.message.reply_text("No feed diagnostics returned")
                return
            worst = sorted(
                rows,
                key=lambda r: str(r.get("stale_status") or r.get("stalenessSeverity") or "OK"),
                reverse=True,
            )[:8]
            label = symbols[0] if len(symbols) == 1 else f"{len(symbols) or 'watchlist'} symbol(s)"
            lines = [f"*Feed diagnostics — {label}*", ""]
            lines.extend(_fmt_feed_row(row) for row in worst)
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(
                f"Feeds unavailable: `{str(e)[:120]}`",
                parse_mode="Markdown",
            )

    async def cmd_lastscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            resp = await _run_in_thread(
                lambda: _safe_json(req.get(f"{_BASE}/api/last-scan?limit=6", timeout=_TIMEOUT_FAST))
            )
            if not resp.get("available"):
                reason = resp.get("reason") or "no scan in memory"
                await update.message.reply_text(f"No recent scan: `{reason}`", parse_mode="Markdown")
                return
            signals = resp.get("signals") or []
            scanned_at = resp.get("scannedAt") or resp.get("scanned_at") or "?"
            lines = [
                f"*Last scan* — `{scanned_at}`",
                f"Signals preview: `{len(signals)}`",
                "",
            ]
            for sig in signals[:6]:
                if not isinstance(sig, dict):
                    continue
                pair = sig.get("pair") or sig.get("display") or "?"
                direction = sig.get("direction") or "?"
                score = sig.get("confluenceScore", sig.get("score", "?"))
                lines.append(f"• *{pair}* {direction} score `{score}`")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(
                f"Last scan unavailable: `{str(e)[:120]}`",
                parse_mode="Markdown",
            )

    # ── /balance ──────────────────────────────────────────────────────────────

    async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            mt5 = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/mt5-status", timeout=_TIMEOUT_FAST)))
            bybit = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST)))
            mt5_acc = mt5.get("account", {})
            bybit_acc = bybit.get("account", {})
            text = (
                f"💰 *Balance*\n\n"
                f"*MT5:*  `${mt5_acc.get('balance', 0):,.2f}` (equity `${mt5_acc.get('equity', 0):,.2f}`)\n"
                f"*Bybit:* `${bybit_acc.get('balance', 0):,.2f} USDT`"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server", parse_mode="Markdown")

    # ── /positions ────────────────────────────────────────────────────────────
    async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        mode = (context.args[0].lower() if context.args else "all").strip()
        valid_modes = (
            "all", "win", "wins", "winning", "green",
            "loss", "losses", "losing", "red",
            "flat", "breakeven", "be",
        )
        if mode not in valid_modes:
            await update.message.reply_text(
                "Usage: `/positions [all|winning|losing|flat]`",
                parse_mode="Markdown",
            )
            return
        try:
            all_pos, meta, audit_unresolved = await _run_in_thread(
                lambda: _fetch_open_positions_sync(req)
            )
            filtered = _filter_positions(all_pos, mode)
            if not filtered and not audit_unresolved:
                suffix = "" if mode == "all" else f" matching `{mode}`"
                await update.message.reply_text(f"No open positions{suffix}", parse_mode="Markdown")
                return

            summary = _summarize_positions(all_pos)
            filtered_summary = _summarize_positions(filtered)
            source_note = ""
            if meta.get("source") == "broker-fallback":
                source_note = "\nTimed-trade enrichment unavailable; showing broker fallback."
            orphan_count = int(meta.get("audit_unresolved_count") or len(audit_unresolved))
            orphan_note = ""
            if orphan_count:
                orphan_note = f"\nAudit orphans: `{orphan_count}` (no broker match)"
            await update.message.reply_text(
                "*Open Trades*\n"
                f"Showing: `{filtered_summary['count']}` / `{summary['count']}`\n"
                f"Winning: `{summary['winning']}` | Losing: `{summary['losing']}` | Flat: `{summary['flat']}`\n"
                f"Total P&L: `{_fmt_money(summary['total'])}`{source_note}{orphan_note}",
                parse_mode="Markdown",
            )

            for pos, exchange in filtered:
                await _send_position_card(update.effective_chat.id, pos, exchange, context)

            if audit_unresolved:
                reconcileable = [r for r in audit_unresolved if _audit_orphan_reconcileable(r)]
                lines = ["*Audit rows without exit price*"]
                for row in audit_unresolved[:5]:
                    lines.append(_fmt_audit_orphan_line(row))
                if len(audit_unresolved) > 5:
                    lines.append(f"_…and {len(audit_unresolved) - 5} more_")
                keyboard = None
                if reconcileable:
                    buttons = []
                    if len(reconcileable) > 1:
                        buttons.append([
                            InlineKeyboardButton(
                                "Reconcile all audit-only",
                                callback_data="audit_reconcile_bulk",
                            )
                        ])
                    first = reconcileable[0]
                    ticket = str(first.get("ticket") or first.get("id") or "")
                    if ticket:
                        buttons.append([
                            InlineKeyboardButton(
                                f"Reconcile {first.get('pair', ticket)}",
                                callback_data=f"audit_reconcile:{ticket}",
                            )
                        ])
                    keyboard = InlineKeyboardMarkup(buttons)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="\n".join(lines),
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
        except Exception as e:
            await update.message.reply_text(
                f"Athena open-trade view unavailable: `{str(e)[:120]}`",
                parse_mode="Markdown",
            )

    async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: `/trade ETH/USDT`", parse_mode="Markdown")
            return
        pair = _resolve_pair(" ".join(context.args))
        try:
            all_pos, _meta, _audit = await _run_in_thread(
                lambda: _fetch_open_positions_sync(req)
            )
            matches = [(p, ex) for p, ex in all_pos if _position_matches_pair(p, pair)]
            if not matches:
                await update.message.reply_text(f"No open trade found for *{pair}*", parse_mode="Markdown")
                return
            for pos, exchange in matches:
                await _send_position_card(update.effective_chat.id, pos, exchange, context)
        except Exception as e:
            await update.message.reply_text(
                f"Athena open-trade view unavailable: `{str(e)[:120]}`",
                parse_mode="Markdown",
            )

    async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        args = context.args
        asset_class = args[0].lower() if args else ""
        valid = {"crypto", "forex", "stock", "stocks", "commodity", "index"}
        if asset_class not in valid:
            await update.message.reply_text(
                "Usage: `/scan crypto|forex|commodity|stock|index`",
                parse_mode="Markdown"
            )
            return
        if asset_class == "stocks":
            asset_class = "stock"

        msg = await update.message.reply_text(f"⏳ Scanning *{asset_class}*...", parse_mode="Markdown")

        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/scan",
                json={"style": "auto", "asset_class": asset_class},
                timeout=_TIMEOUT_SCAN,
            )))

            if resp.get("error"):
                await msg.edit_text(f"❌ Scan error: {resp['error']}")
                return

            signals = resp.get("tradeSignals", resp.get("signals", []))
            watchlist = resp.get("watchlist", [])

            await msg.edit_text(
                f"📊 *Scan — {asset_class.upper()}*\n"
                f"✅ Signals: {len(signals)} | 👁 Watchlist: {len(watchlist)}",
                parse_mode="Markdown"
            )

            if not signals:
                if watchlist:
                    top = watchlist[0]
                    await update.message.reply_text(
                        f"Best near-miss: *{top.get('pair')}* {top.get('direction')} score={top.get('confluenceScore', 0):.2f}",
                        parse_mode="Markdown"
                    )
                return

            for sig in signals[:3]:
                await _send_signal_card(update.effective_chat.id, sig, context)

        except Exception:
            await msg.edit_text("⚠️ Athena offline — check server")

    # ── /signal ───────────────────────────────────────────────────────────────

    async def cmd_enginec(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        args = context.args
        asset_class = args[0].lower() if args else ""
        style = args[1].lower() if len(args) > 1 else "auto"
        valid = {"crypto", "forex", "stock", "stocks", "commodity", "index"}
        if asset_class not in valid:
            await update.message.reply_text(
                "Usage: `/enginec crypto|forex|commodity|stock|index [auto|scalp|intraday|swing]`",
                parse_mode="Markdown",
            )
            return
        if asset_class == "stocks":
            asset_class = "stock"

        msg = await update.message.reply_text(
            f"Scanning Engine C *{asset_class}*...", parse_mode="Markdown"
        )
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/engine-c-scan",
                json={"assetClass": asset_class, "style": style},
                timeout=_TIMEOUT_SCAN,
            )))
            if resp.get("error"):
                await msg.edit_text(f"Engine C error: {resp['error']}")
                return
            buckets = ["aligned", "b_only", "a_only", "conflict", "skipped"]
            counts = {name: len(resp.get(name, []) or []) for name in buckets}
            await msg.edit_text(
                "*Engine C Scan*\n"
                f"Aligned: `{counts['aligned']}` | B-only: `{counts['b_only']}` | A-only: `{counts['a_only']}`\n"
                f"Conflict: `{counts['conflict']}` | Skipped: `{counts['skipped']}`",
                parse_mode="Markdown",
            )
            top = (resp.get("aligned") or resp.get("b_only") or resp.get("a_only") or [])[:3]
            for sig in top:
                await update.message.reply_text(_fmt_engine_c_card(sig), parse_mode="Markdown")
        except Exception:
            await msg.edit_text("Athena offline - check server")

    async def cmd_engineb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        args = context.args
        asset_class = args[0].lower() if args else ""
        style = args[1].lower() if len(args) > 1 else "auto"
        valid_assets = {"crypto", "forex", "stock", "stocks", "commodity", "index"}
        valid_styles = {"auto", "scalp", "intraday", "swing"}
        if asset_class not in valid_assets or style not in valid_styles:
            await update.message.reply_text(
                "Usage: `/engineb crypto|forex|commodity|stock|index [auto|scalp|intraday|swing]`",
                parse_mode="Markdown",
            )
            return
        if asset_class == "stocks":
            asset_class = "stock"

        msg = await update.message.reply_text(
            f"Scanning Engine B *{asset_class}* (`{style}`)...",
            parse_mode="Markdown",
        )
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/scan-naked",
                json={"assetClass": asset_class, "style": style},
                timeout=_TIMEOUT_SCAN,
            )))
            if resp.get("error"):
                await msg.edit_text(f"Engine B error: {resp['error']}")
                return
            signals = resp.get("signals", []) or []
            debug_rows = resp.get("debugRows", []) or []
            await msg.edit_text(
                "*Engine B Scan*\n"
                f"Signals: `{len(signals)}` | Debug rows: `{len(debug_rows)}`\n"
                f"Asset: `{asset_class}` | Style: `{style}`",
                parse_mode="Markdown",
            )
            if not signals:
                await update.message.reply_text(
                    "No Engine B signals passed the current structure/checklist gates.",
                    parse_mode="Markdown",
                )
                return
            for sig in signals[:3]:
                key = _store_pending_signal(sig)
                await update.message.reply_text(
                    _fmt_engine_b_card(sig),
                    parse_mode="Markdown",
                    reply_markup=_execution_keyboard(key),
                )
        except Exception:
            await msg.edit_text("Athena offline - check server")

    async def cmd_scalp(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        requested = "all"
        label = "all pairs"
        if context.args:
            pair = _resolve_pair(" ".join(context.args))
            requested = [pair]
            label = pair

        msg = await update.message.reply_text(f"Scanning scalp setups for *{label}*...", parse_mode="Markdown")
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/scalp-scan",
                json={"pairs": requested},
                timeout=_TIMEOUT_SCAN,
            )))
            if resp.get("error"):
                await msg.edit_text(f"Scalp scan error: {resp['error']}")
                return
            signals = resp.get("signals", []) or []
            skipped = resp.get("skipped", []) or []
            scanned = resp.get("scanned", resp.get("pair_count", 0))
            await msg.edit_text(
                "*Scalp Scan*\n"
                f"Signals: `{len(signals)}` | Skipped: `{len(skipped)}` | Scanned: `{scanned}`\n"
                f"Pass: `{resp.get('pass_count', '?')}` | Skip count: `{resp.get('skip_count', len(skipped))}`\n"
                f"Session: `{resp.get('session', '?')}`",
                parse_mode="Markdown",
            )
            diag_summary = resp.get("diagnostic_summary")
            if diag_summary:
                await update.message.reply_text(
                    f"Diagnostic: `{str(diag_summary)[:350]}`",
                    parse_mode="Markdown",
                )
            if not signals and skipped and requested != "all":
                reason = skipped[0].get("reason", "?") if isinstance(skipped[0], dict) else str(skipped[0])
                await update.message.reply_text(f"No scalp setup for *{label}*: `{reason}`", parse_mode="Markdown")
                return
            for sig in signals[:3]:
                await update.message.reply_text(_fmt_scalp_card(sig), parse_mode="Markdown")
        except Exception:
            await msg.edit_text("Athena offline - check server")

    async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: `/signal ETH/USDT`", parse_mode="Markdown")
            return

        raw = " ".join(context.args)
        pair = _resolve_pair(raw)

        msg = await update.message.reply_text(f"⏳ Analysing *{pair}*...", parse_mode="Markdown")

        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/pair-scan",
                json={"symbol": pair, "style": "auto"},
                timeout=_TIMEOUT_SCAN,
            )))

            if resp.get("error"):
                await msg.edit_text(f"❌ Error: {resp['error']}")
                return

            match = resp.get("signal")

            if not match:
                await msg.edit_text(f"Signal unavailable for *{pair}*.", parse_mode="Markdown")
                return

            await msg.delete()
            await _send_signal_card(update.effective_chat.id, match, context)

        except Exception:
            await msg.edit_text("⚠️ Athena offline — check server")

    # ── /close ────────────────────────────────────────────────────────────────

    async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: `/close ETH/USDT`", parse_mode="Markdown")
            return

        raw = " ".join(context.args)
        pair = _resolve_pair(raw)

        try:
            mt5_resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/mt5-positions", timeout=_TIMEOUT_FAST)))
            bybit_resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/bybit-status", timeout=_TIMEOUT_FAST)))
            mt5_pos = mt5_resp.get("positions", [])
            bybit_pos = bybit_resp.get("positions", [])
            all_pos = [(p, "mt5") for p in mt5_pos] + [(p, "bybit") for p in bybit_pos]

            match = next((p for p, ex in all_pos if (p.get("pair", "").upper() == pair.upper())), None)
            exchange = next((ex for p, ex in all_pos if (p.get("pair", "").upper() == pair.upper())), None)

            if not match:
                await update.message.reply_text(f"❌ No open position found for *{pair}*", parse_mode="Markdown")
                return

            close_key = f"close_{exchange}_{pair}_{match.get('direction')}_{match.get('volume')}_{match.get('ticket')}"
            _pending_confirmations[close_key] = {
                "exchange": exchange, "pair": pair,
                "direction": match.get("direction"), "volume": match.get("volume"),
                "ticket": match.get("ticket")
            }

            card = _fmt_position_card(match)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirm Close", callback_data=f"close_confirm:{close_key}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"close_cancel:{close_key}"),
            ]])
            await update.message.reply_text(
                f"⚠️ *Confirm close?*\n\n{card}", parse_mode="Markdown", reply_markup=keyboard
            )

        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /kill ─────────────────────────────────────────────────────────────────

    async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            await _run_in_thread(lambda: req.post(f"{_BASE}/api/killswitch", json={"action": "on"}, timeout=_TIMEOUT_FAST))
            await update.message.reply_text("🛑 *KILL SWITCH ACTIVATED*\nAll auto-trading stopped.", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /resume ───────────────────────────────────────────────────────────────

    async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            await _run_in_thread(lambda: req.post(f"{_BASE}/api/killswitch", json={"action": "off"}, timeout=_TIMEOUT_FAST))
            await update.message.reply_text("✅ *Kill switch lifted — auto-trading resumed*", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /auto ─────────────────────────────────────────────────────────────────

    async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        args = context.args
        action = args[0].lower() if args else "toggle"
        if action not in ("on", "off", "toggle"):
            await update.message.reply_text("Usage: `/auto on|off`", parse_mode="Markdown")
            return
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/auto-trade", json={"action": action}, timeout=_TIMEOUT_FAST
            )))
            enabled = resp.get("enabled", False)
            icon = "🟢 ON" if enabled else "⚫ OFF"
            trades = resp.get("tradesToday", 0)
            max_d = resp.get("maxDaily", 3)
            await update.message.reply_text(
                f"🤖 Auto-trader: *{icon}*\n📅 Today: `{trades}/{max_d}`",
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /pnl ──────────────────────────────────────────────────────────────────

    async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/performance", timeout=_TIMEOUT_FAST)))
            if resp.get("error"):
                await update.message.reply_text(f"⚠️ {resp.get('error')}")
                return
            if resp.get("total_trades", 0) == 0:
                await update.message.reply_text("📭 No completed trades yet")
                return

            total = resp.get("total_trades", 0)
            wr = resp.get("win_rate", 0)
            total_r = resp.get("total_r", 0)
            avg_r = resp.get("average_r_multiple", 0)
            pf = resp.get("profit_factor")
            dd = resp.get("max_drawdown_pct", 0)

            text = (
                f"📈 *Performance*\n\n"
                f"Trades:  `{total}`\n"
                f"Win Rate: `{wr}%`\n"
                f"Total R:  `{'+' if total_r >= 0 else ''}{total_r:.2f}R`\n"
                f"Avg R:    `{'+' if avg_r >= 0 else ''}{avg_r:.3f}R`\n"
                f"PF:       `{pf or 'N/A'}`\n"
                f"Max DD:   `{dd:.2f}%`"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /decay ────────────────────────────────────────────────────────────────

    async def cmd_decay(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/score-decay", timeout=_TIMEOUT_FAST)))
            relevant = {k: v for k, v in resp.items() if isinstance(v, dict) and v.get("decay", 0) > 0.3}
            if not relevant:
                await update.message.reply_text("✅ No significant score decay on open positions")
                return

            lines = ["📉 *Score Decay — Open Positions*\n"]
            for pair_name, d in sorted(relevant.items(), key=lambda x: x[1].get("decay", 0), reverse=True):
                entry_dir = d.get("direction", "?")
                cur_dir = d.get("currentDirection", "?")
                flip = " ⚠️ DIRECTION FLIP" if entry_dir != cur_dir else ""
                decay_val = d.get("decay", 0)
                emoji = "🔴" if decay_val >= 1.5 else "🟡"
                
                engine = d.get("engine", "engine_a")
                engine_label = " [Eng B]" if engine == "engine_b" else ""
                score_path = d.get("scoreNote") or f"{d.get('entryScore', 0):.2f} → {d.get('currentScore', 0):.2f}"
                
                lines.append(
                    f"{emoji} *{pair_name}*{engine_label}: `{score_path}` "
                    f"(Δ{decay_val:.1f}){flip}"
                )
                ai_verdict = d.get("aiVerdict")
                if ai_verdict:
                    v_emoji = "🚨" if ai_verdict == "EXIT" else "👁" if ai_verdict == "WATCH" else "✋"
                    urgency = d.get("aiUrgency", "")
                    reasoning = d.get("aiReasoning", "")
                    lines.append(
                        f"   {v_emoji} AI: *{ai_verdict}*"
                        + (f" `[{urgency}]`" if urgency else "")
                        + (f" — _{reasoning}_" if reasoning else "")
                    )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("⚠️ Athena offline — check server")

    # ── /bt ───────────────────────────────────────────────────────────────────

    async def cmd_bt(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: `/bt EUR/USD`", parse_mode="Markdown")
            return
        raw = " ".join(context.args)
        pair = _resolve_pair(raw)
        msg = await update.message.reply_text(f"⏳ Running backtest for *{pair}*...", parse_mode="Markdown")
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/backtest", json={"pair": pair}, timeout=90
            )))
            if resp.get("error"):
                await msg.edit_text(f"❌ {resp['error']}")
                return
            sqn = resp.get("sqn", 0)
            wr = resp.get("winRate", resp.get("win_rate", 0))
            trades = resp.get("totalTrades", resp.get("total_trades", 0))
            oos_sqn = (resp.get("wfSplit") or {}).get("oos_sqn") or 0
            sqn_emoji = "✅" if sqn >= 2.0 else "⚠️" if sqn >= 1.0 else "❌"
            text = (
                f"📊 *Backtest — {pair}*\n\n"
                f"SQN: `{sqn}` {sqn_emoji}  |  WR: `{wr}%`\n"
                f"Trades: `{trades}`  |  OOS SQN: `{oos_sqn}`"
            )
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            await msg.edit_text("⚠️ Athena offline — check server")

    # ── /execute ──────────────────────────────────────────────────────────────

    async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: `/execute ETH/USDT [scalp|intraday|swing]`\n"
                "Scans the pair and shows an execute card with SCALP/INTRADAY/SWING buttons.",
                parse_mode="Markdown",
            )
            return

        raw_args = context.args
        style_hint = ""
        if raw_args[-1].lower() in ("scalp", "intraday", "swing"):
            style_hint = raw_args[-1].lower()
            raw_args = raw_args[:-1]
        pair = _resolve_pair(" ".join(raw_args))

        msg = await update.message.reply_text(f"⏳ Scanning *{pair}* for execution...", parse_mode="Markdown")
        try:
            resp = await _run_in_thread(lambda: _safe_json(req.post(
                f"{_BASE}/api/pair-scan",
                json={"symbol": pair, "style": style_hint or "auto"},
                timeout=_TIMEOUT_SCAN,
            )))
            if resp.get("error"):
                await msg.edit_text(f"❌ Error: {resp['error']}")
                return
            sig = resp.get("signal")
            if not sig:
                await msg.edit_text(
                    f"No executable signal for *{pair}* right now.\n"
                    "Run `/signal {pair}` for a detailed breakdown.",
                    parse_mode="Markdown",
                )
                return
            await msg.delete()
            await _send_signal_card(update.effective_chat.id, sig, context)
        except Exception as exc:
            await msg.edit_text(
                f"⚠️ Athena offline — check server\n`{str(exc)[:100]}`",
                parse_mode="Markdown",
            )

    # ── Helper: send signal card with execute buttons ─────────────────────────

    def _execution_keyboard(signal_key: str, *, include_ai: bool = True):
        rows = [[
            InlineKeyboardButton("⚡ SCALP", callback_data=f"exec_scalp:{signal_key}"),
            InlineKeyboardButton("📊 INTRADAY", callback_data=f"exec_intraday:{signal_key}"),
            InlineKeyboardButton("🌊 SWING", callback_data=f"exec_swing:{signal_key}"),
        ]]
        if include_ai:
            rows.append([
                InlineKeyboardButton("🧠 AI Analysis", callback_data=f"ai_analyse:{signal_key}"),
                InlineKeyboardButton("❌ Skip", callback_data=f"skip:{signal_key}"),
            ])
        else:
            rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"skip:{signal_key}")])
        return InlineKeyboardMarkup(rows)

    async def _send_signal_card(chat_id_val, sig: dict, context):
        signal_key = _store_pending_signal(sig)
        card = _fmt_signal_card(sig)
        await context.bot.send_message(
            chat_id=chat_id_val, text=card, parse_mode="Markdown", reply_markup=_execution_keyboard(signal_key)
        )

    # ── Button callbacks ──────────────────────────────────────────────────────

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _guard(update):
            return
        query = update.callback_query
        await query.answer()
        _prune_pending()

        data = query.data
        parts = data.split(":", 1)
        action = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        # ── Skip ──
        if action == "skip":
            await query.edit_message_reply_markup(reply_markup=None)
            if key in _pending_signals:
                del _pending_signals[key]
            return

        # ── Execute ──
        if action.startswith("exec_"):
            entry = _pending_signals.get(key)
            if not entry:
                await query.message.reply_text("❌ Signal expired — re-scan to refresh")
                return
            sig = entry["signal"]
            style_map = {"exec_scalp": "scalp", "exec_intraday": "intraday", "exec_swing": "swing"}
            style = style_map.get(action, "swing")
            pair_label = sig.get("pair") or sig.get("display") or "?"
            direction_label = sig.get("direction") or "?"

            await query.message.reply_text(
                f"⏳ Executing *{pair_label} {direction_label}* ({style.upper()})...",
                parse_mode="Markdown"
            )
            try:
                _sig_ref = sig
                _style_ref = style
                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/quick-execute",
                    json=_build_quick_execute_payload(_sig_ref, _style_ref),
                    timeout=_TIMEOUT_EXEC,
                )))
                if resp.get("success"):
                    ticket = resp.get("ticket", "?")
                    entry_px = resp.get("entryPrice", sig.get("price", "?"))
                    sl_out = resp.get("sl", sig.get("sl", "?"))
                    tp_out = resp.get("tp", sig.get("tp1", "?"))
                    await query.message.reply_text(
                        f"✅ *EXECUTED*\n"
                        f"*{pair_label} {direction_label}* @ `{entry_px}`\n"
                        f"SL: `{sl_out}` | TP: `{tp_out}`\n"
                        f"Ticket: `#{ticket}`",
                        parse_mode="Markdown"
                    )
                    if key in _pending_signals:
                        del _pending_signals[key]
                else:
                    err = resp.get("error", "Unknown error")
                    await query.message.reply_text(f"❌ Execution failed: `{err}`", parse_mode="Markdown")
            except Exception as exc:
                await query.message.reply_text(
                    f"⚠️ Athena offline — check server\n`{str(exc)[:120]}`",
                    parse_mode="Markdown"
                )
            return

        # ── AI Analysis ──
        if action == "ai_analyse":
            entry = _pending_signals.get(key)
            if not entry:
                await query.message.reply_text("❌ Signal expired — re-scan to refresh")
                return
            sig = entry["signal"]
            await query.message.reply_text("🧠 Running AI analysis...")
            try:
                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/analyze",
                    json={"signal": sig, "stylePreference": "auto"},
                    timeout=120,
                )))
                if resp.get("error"):
                    await query.message.reply_text(f"❌ AI error: {resp['error']}")
                    return

                grade = resp.get("grade", "?")
                edge = resp.get("edgeProbability", 0)
                verdict = resp.get("verdict", "")
                risk_level = resp.get("riskLevel", "")
                warnings = resp.get("warnings", [])

                grade_emoji = {"A": "✅", "B": "✅", "C": "⚠️", "D": "🚫", "F": "🚫"}.get(str(grade).strip()[:1].upper(), "❓")
                warn_text = "\n".join(f"• {w}" for w in warnings[:3]) if warnings else "None"

                try:
                    ev = float(edge)
                    edge_f = ev / 100.0 if ev > 1.0 else ev
                except (TypeError, ValueError):
                    edge_f = 0.0

                result_text = (
                    f"{grade_emoji} *Grade: {grade}*  |  Edge: `{edge_f:.0%}`\n"
                    f"Risk: `{risk_level}`\n\n"
                    f"_{str(verdict)[:300]}_\n\n"
                    f"*Warnings:*\n{warn_text}"
                )

                g0 = str(grade).strip()[:1].upper()
                if g0 in ("A", "B"):
                    await query.message.reply_text(
                        result_text,
                        parse_mode="Markdown",
                        reply_markup=_execution_keyboard(key, include_ai=False),
                    )
                else:
                    await query.message.reply_text(
                        f"{result_text}\n\n🚫 *Trade blocked — Grade {grade}*",
                        parse_mode="Markdown"
                    )
                    if key in _pending_signals:
                        del _pending_signals[key]

            except Exception as e:
                await query.message.reply_text(f"⚠️ Athena offline or timed out — check server\n`{str(e)[:150]}`", parse_mode="Markdown")
            return

        # ── Decay for specific pair ──
        if action == "decay_pair":
            pair_name = key
            try:
                resp = await _run_in_thread(lambda: _safe_json(req.get(f"{_BASE}/api/score-decay", timeout=_TIMEOUT_FAST)))
                d = resp.get(pair_name)
                if not d:
                    await query.message.reply_text(f"✅ No decay data for *{pair_name}*", parse_mode="Markdown")
                    return
                decay_val = d.get("decay", 0)
                entry_dir = d.get("direction", "?")
                cur_dir = d.get("currentDirection", "?")
                flip = "\n⚠️ *DIRECTION FLIP — consider exiting*" if entry_dir != cur_dir else ""
                emoji = "🔴" if decay_val >= 1.5 else "🟡"
                await query.message.reply_text(
                    f"{emoji} *{pair_name} Score Decay*\n"
                    f"Entry: `{d.get('entryScore', 0):.2f}` → Now: `{d.get('currentScore', 0):.2f}` (Δ{decay_val:.2f})\n"
                    f"Entry dir: `{entry_dir}` | Current: `{cur_dir}`{flip}",
                    parse_mode="Markdown"
                )
            except Exception:
                await query.message.reply_text("⚠️ Athena offline — check server")
            return

        # ── Close confirm ──
        if action == "trade_refresh":
            conf = _pending_confirmations.get(key)
            if not conf:
                await query.message.reply_text("Position data expired - run /positions again")
                return
            try:
                all_pos, _meta, _audit = await _run_in_thread(
                    lambda: _fetch_open_positions_sync(req)
                )
                match = next(
                    (
                        (p, ex)
                        for p, ex in all_pos
                        if ex == conf["exchange"]
                        and _position_matches_pair(p, conf["pair"])
                        and str(p.get("direction") or "") == str(conf["direction"] or "")
                    ),
                    None,
                )
                if not match:
                    await query.message.reply_text(f"No open trade found for *{conf['pair']}*", parse_mode="Markdown")
                    return
                pos, exchange = match
                await query.message.reply_text(_fmt_position_detail_card(pos, exchange), parse_mode="Markdown")
            except Exception:
                await query.message.reply_text("Athena offline - check server")
            return

        if action == "trade_analyze":
            pair_name = key
            await query.message.reply_text(f"Analyzing *{pair_name}*...", parse_mode="Markdown")
            try:
                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/pair-scan",
                    json={"symbol": pair_name, "style": "auto"},
                    timeout=_TIMEOUT_SCAN,
                )))
                if resp.get("error"):
                    await query.message.reply_text(f"Pair scan failed: {resp['error']}")
                    return
                sig = resp.get("signal")
                if not sig:
                    await query.message.reply_text(f"Signal unavailable for *{pair_name}*", parse_mode="Markdown")
                    return
                await _send_signal_card(query.message.chat.id, sig, context)
            except Exception:
                await query.message.reply_text("Athena offline - check server")
            return

        if action == "audit_reconcile_bulk":
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Confirm reconcile all", callback_data="audit_reconcile_confirm:bulk"),
                InlineKeyboardButton("Cancel", callback_data="audit_reconcile_cancel:bulk"),
            ]])
            await query.message.reply_text(
                "Close *all* audit-only rows as scratch (exit=entry, pnl=0)?\n"
                "No broker positions will be touched.",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return

        if action == "audit_reconcile":
            ticket = key
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Confirm reconcile", callback_data=f"audit_reconcile_confirm:{ticket}"),
                InlineKeyboardButton("Cancel", callback_data=f"audit_reconcile_cancel:{ticket}"),
            ]])
            await query.message.reply_text(
                f"Close audit row ticket `{ticket}` as scratch?",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return

        if action == "audit_reconcile_confirm":
            payload = (
                {"reconcile_all_audit_only": True}
                if key == "bulk"
                else {"ticket": key}
            )
            try:
                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/audit/reconcile-orphan",
                    json=payload,
                    timeout=_TIMEOUT_EXEC,
                )))
                if resp.get("success"):
                    closed = resp.get("closed", 1)
                    await query.message.reply_text(
                        f"Reconciled `{closed}` audit row(s) (scratch)",
                        parse_mode="Markdown",
                    )
                else:
                    await query.message.reply_text(
                        f"Reconcile failed: {resp.get('error', '?')}",
                        parse_mode="Markdown",
                    )
            except Exception:
                await query.message.reply_text("Athena offline - check server")
            return

        if action == "audit_reconcile_cancel":
            await query.message.reply_text("Audit reconcile cancelled")
            return

        if action == "close_request":
            conf = _pending_confirmations.get(key)
            if not conf:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("Position data expired")
                return
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Confirm Close", callback_data=f"close_confirm:{key}"),
                InlineKeyboardButton("Cancel", callback_data=f"close_cancel:{key}"),
            ]])
            await query.message.reply_text(
                f"Confirm close *{conf['pair']} {conf['direction']}*?",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return

        if action == "close_confirm":
            conf = _pending_confirmations.get(key)
            if not conf:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("❌ Position data expired")
                return
            try:
                payload = {
                    "exchange": conf["exchange"],
                    "pair": conf["pair"],
                    "direction": conf["direction"],
                    "volume": conf["volume"],
                }
                if conf["exchange"] == "mt5" and conf.get("ticket"):
                    payload["ticket"] = conf["ticket"]

                resp = await _run_in_thread(lambda: _safe_json(req.post(
                    f"{_BASE}/api/close-position", json=payload, timeout=_TIMEOUT_EXEC
                )))
                await query.edit_message_reply_markup(reply_markup=None)
                if resp.get("success"):
                    await query.message.reply_text(
                        f"✅ *Position closed*\n{conf['pair']} {conf['direction']}",
                        parse_mode="Markdown"
                    )
                else:
                    await query.message.reply_text(f"❌ Close failed: {resp.get('error', '?')}")
                if key in _pending_confirmations:
                    del _pending_confirmations[key]
            except Exception:
                await query.message.reply_text("⚠️ Athena offline — check server")
            return

        # ── Close cancel ──
        if action == "close_cancel":
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("❌ Close cancelled")
            if key in _pending_confirmations:
                del _pending_confirmations[key]
            return

    # Register all handlers
    app.add_error_handler(_telegram_error_handler)
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("engineb", cmd_engineb))
    app.add_handler(CommandHandler("enginec", cmd_enginec))
    app.add_handler(CommandHandler("scalp", cmd_scalp))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("pos", cmd_positions))
    app.add_handler(CommandHandler("trades", cmd_positions))
    app.add_handler(CommandHandler("open", cmd_positions))
    app.add_handler(CommandHandler("trade", cmd_trade))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("forensics", cmd_forensics))
    app.add_handler(CommandHandler("guardian", cmd_guardian))
    app.add_handler(CommandHandler("feeds", cmd_feeds))
    app.add_handler(CommandHandler("lastscan", cmd_lastscan))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("bal", cmd_balance))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("decay", cmd_decay))
    app.add_handler(CommandHandler("bt", cmd_bt))
    app.add_handler(CommandHandler("execute", cmd_execute))
    app.add_handler(CommandHandler("exec", cmd_execute))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CallbackQueryHandler(button_callback))

    log.warning("[TELEGRAM] Bot handlers registered, starting polling...")
    app.run_polling(drop_pending_updates=True)
