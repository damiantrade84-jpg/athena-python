"""
telegram_notify.py — Non-blocking Telegram notification system

Provides notification functions for:
- Signal fired events
- Trade opened/closed events
- System alerts (WS disconnect, rate limiting)
- Daily summary at 22:00 UTC

All notifications are non-blocking using background threads.
If TELEGRAM.enabled is false, all notifications silently do nothing.
"""

import logging
import queue
import requests
import threading
import time
from datetime import datetime, timezone, time as dt_time
from typing import Dict, List, Optional, Any
import yaml
from pathlib import Path

log = logging.getLogger("sentinel.telegram")

# Global notification state
_config: Dict[str, Any] = {}
_daily_stats: Dict[str, Any] = {
    "signals_fired": [],
    "trades_opened": [],
    "trades_closed": [],
    "open_positions": [],
    "last_summary_time": None,
}
_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "token": "",
    "chat_id": "",
    "timeout_sec": 10,
    "retry_attempts": 3,
    "retry_backoff_sec": 2.0,
    "queue_max_size": 200,
    "max_message_chars": 3900,
    "parse_mode": "Markdown",
    "fallback_plaintext": True,
    "notify_exit_reason": True,
    "notify_trail_only": False,
}

_EXIT_REASON_LABELS: Dict[str, str] = {
    "TRAIL_CLOSE": "Chandelier trail",
    "TRAIL_GIVEBACK": "Trail giveback",
    "TRAIL_PROFIT_ROUNDTRIP": "Trail profit roundtrip",
    "TIMED_CLOSE": "Timed close",
    "LOCK_SL_HIT": "Profit lock SL",
}
_delivery_queue: Optional[queue.Queue] = None
_delivery_thread: Optional[threading.Thread] = None
_delivery_lock = threading.Lock()
_delivery_state_lock = threading.Lock()
_dotenv_loaded = False
_delivery_state: Dict[str, Any] = {
    "queued": 0,
    "sent": 0,
    "failed": 0,
    "dropped": 0,
    "last_success_time": None,
    "last_failure_time": None,
    "last_error": "",
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on", "enabled"):
            return True
        if normalized in ("0", "false", "no", "off", "disabled"):
            return False
    return default


def _coerce_int(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _coerce_float(value: Any, default: float, minimum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _load_dotenv_once() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


def _apply_env_overrides(config: Dict[str, Any]) -> None:
    import os

    _load_dotenv_once()

    env_map = {
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_CHAT_ID": "chat_id",
        "TELEGRAM_ENABLED": "enabled",
        "TELEGRAM_TIMEOUT_SEC": "timeout_sec",
        "TELEGRAM_RETRY_ATTEMPTS": "retry_attempts",
        "TELEGRAM_RETRY_BACKOFF_SEC": "retry_backoff_sec",
        "TELEGRAM_QUEUE_MAX_SIZE": "queue_max_size",
        "TELEGRAM_MAX_MESSAGE_CHARS": "max_message_chars",
        "TELEGRAM_PARSE_MODE": "parse_mode",
        "TELEGRAM_FALLBACK_PLAINTEXT": "fallback_plaintext",
        "TELEGRAM_NOTIFY_EXIT_REASON": "notify_exit_reason",
        "TELEGRAM_NOTIFY_TRAIL_ONLY": "notify_trail_only",
    }
    for env_name, key in env_map.items():
        value = os.environ.get(env_name)
        if value not in (None, ""):
            config[key] = value


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in _DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    config["enabled"] = _coerce_bool(config.get("enabled"), _DEFAULT_CONFIG["enabled"])
    config["timeout_sec"] = _coerce_float(
        config.get("timeout_sec"), _DEFAULT_CONFIG["timeout_sec"], 1.0
    )
    config["retry_attempts"] = _coerce_int(
        config.get("retry_attempts"), _DEFAULT_CONFIG["retry_attempts"], 1
    )
    config["retry_backoff_sec"] = _coerce_float(
        config.get("retry_backoff_sec"), _DEFAULT_CONFIG["retry_backoff_sec"], 0.0
    )
    config["queue_max_size"] = _coerce_int(
        config.get("queue_max_size"), _DEFAULT_CONFIG["queue_max_size"], 1
    )
    config["max_message_chars"] = _coerce_int(
        config.get("max_message_chars"), _DEFAULT_CONFIG["max_message_chars"], 1000
    )
    config["fallback_plaintext"] = _coerce_bool(
        config.get("fallback_plaintext"), _DEFAULT_CONFIG["fallback_plaintext"]
    )
    config["notify_exit_reason"] = _coerce_bool(
        config.get("notify_exit_reason"), _DEFAULT_CONFIG["notify_exit_reason"]
    )
    config["notify_trail_only"] = _coerce_bool(
        config.get("notify_trail_only"), _DEFAULT_CONFIG["notify_trail_only"]
    )
    if config.get("parse_mode") in (None, ""):
        config["parse_mode"] = None
    return config


def _load_config() -> Dict[str, Any]:
    """Load Telegram configuration from config.yaml"""
    global _config
    # Keep applying env overrides because dotenv may load after this module imports.
    if _config:
        _apply_env_overrides(_config)
        return _normalize_config(_config)

    config_path = Path(__file__).parent / "config.yaml"
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            _config = config.get("TELEGRAM", {})
    except Exception as exc:
        log.warning("[TELEGRAM] Config load failed: %s", exc)
        _config = {}

    if not isinstance(_config, dict):
        _config = {}
    _config = {**_DEFAULT_CONFIG, **_config}
    _apply_env_overrides(_config)
    return _normalize_config(_config)


def _is_enabled() -> bool:
    """Check if Telegram notifications are enabled"""
    config = _load_config()
    return bool(config.get("enabled", False))


def get_runtime_config() -> Dict[str, Any]:
    """Return the normalized Telegram runtime config."""
    return dict(_load_config())


def get_configured_chat_ids(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return configured Telegram chat IDs as strings."""
    cfg = config or _load_config()
    raw = cfg.get("chat_ids", cfg.get("chat_id", ""))
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = str(raw).split(",")
    return [str(value).strip() for value in values if str(value).strip()]


def get_delivery_state() -> Dict[str, Any]:
    """Return delivery counters for diagnostics without exposing credentials."""
    with _delivery_state_lock:
        state = dict(_delivery_state)
    q = _delivery_queue
    state["queue_size"] = q.qsize() if q is not None else 0
    state["queue_max_size"] = q.maxsize if q is not None else _load_config().get("queue_max_size")
    return state


def _record_delivery_state(
    *,
    queued: int = 0,
    sent: int = 0,
    failed: int = 0,
    dropped: int = 0,
    error: str = "",
) -> None:
    with _delivery_state_lock:
        _delivery_state["queued"] += queued
        _delivery_state["sent"] += sent
        _delivery_state["failed"] += failed
        _delivery_state["dropped"] += dropped
        now = datetime.now(timezone.utc).isoformat()
        if sent:
            _delivery_state["last_success_time"] = now
        if failed or dropped or error:
            _delivery_state["last_failure_time"] = now
            _delivery_state["last_error"] = error[:300]


def _ensure_delivery_worker(config: Dict[str, Any]) -> None:
    global _delivery_queue, _delivery_thread
    with _delivery_lock:
        if _delivery_queue is None:
            _delivery_queue = queue.Queue(maxsize=int(config.get("queue_max_size", 200)))
        if _delivery_thread is None or not _delivery_thread.is_alive():
            _delivery_thread = threading.Thread(
                target=_delivery_worker,
                daemon=True,
                name="telegram-delivery",
            )
            _delivery_thread.start()


def _delivery_worker() -> None:
    while True:
        message = _delivery_queue.get()
        try:
            _deliver_message(message)
        except Exception as exc:
            _record_delivery_state(failed=1, error=str(exc))
            log.warning("[TELEGRAM] Delivery worker error: %s", exc)
        finally:
            _delivery_queue.task_done()


def _send_message_async(message: str) -> bool:
    """Queue a Telegram message without blocking the trading path."""
    config = _load_config()
    if not config.get("enabled"):
        return False

    token = config.get("token")
    chat_ids = get_configured_chat_ids(config)
    if not token or not chat_ids:
        _record_delivery_state(failed=1, error="missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        log.warning("[TELEGRAM] Missing token or chat_id; notification skipped")
        return False

    _ensure_delivery_worker(config)
    try:
        _delivery_queue.put_nowait(str(message))
    except queue.Full:
        _record_delivery_state(dropped=1, error="telegram delivery queue full")
        log.warning("[TELEGRAM] Delivery queue full; notification dropped")
        return False
    _record_delivery_state(queued=1)
    return True


def _split_message(message: str, max_chars: int) -> List[str]:
    text = str(message)
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if current:
                chunks.append(current.rstrip())
                current = ""
            for idx in range(0, len(line), max_chars):
                chunks.append(line[idx : idx + max_chars].rstrip())
            continue
        if len(current) + len(line) > max_chars:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current:
        chunks.append(current.rstrip())
    return chunks


def _response_error(response) -> str:
    text = (getattr(response, "text", "") or "").strip()
    try:
        data = response.json()
        if isinstance(data, dict):
            text = str(data.get("description") or data.get("error") or text)
    except Exception:
        pass
    status = getattr(response, "status_code", "?")
    return f"HTTP {status}: {text[:200] or 'Telegram request failed'}"


def _is_markdown_parse_error(response) -> bool:
    detail = _response_error(response).lower()
    return "parse" in detail and (
        "entity" in detail or "entities" in detail or "markdown" in detail
    )


def _post_message(token: str, chat_id: str, text: str, config: Dict[str, Any]) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    parse_mode = config.get("parse_mode")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    response = requests.post(url, json=payload, timeout=config.get("timeout_sec", 10))
    if getattr(response, "ok", False):
        return

    if parse_mode and config.get("fallback_plaintext") and _is_markdown_parse_error(response):
        fallback_payload = dict(payload)
        fallback_payload.pop("parse_mode", None)
        response = requests.post(url, json=fallback_payload, timeout=config.get("timeout_sec", 10))
        if getattr(response, "ok", False):
            return

    raise RuntimeError(_response_error(response))


def _send_chunk_with_retry(
    token: str,
    chat_id: str,
    chunk: str,
    config: Dict[str, Any],
) -> bool:
    attempts = int(config.get("retry_attempts", 3))
    backoff = float(config.get("retry_backoff_sec", 2.0))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            _post_message(token, chat_id, chunk, config)
            _record_delivery_state(sent=1)
            return True
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts and backoff > 0:
                time.sleep(backoff * attempt)
    _record_delivery_state(failed=1, error=last_error)
    log.warning("[TELEGRAM] Message delivery failed after %s attempts: %s", attempts, last_error)
    return False


def _deliver_message(message: str) -> bool:
    config = _load_config()
    if not config.get("enabled"):
        return False

    token = config.get("token")
    chat_ids = get_configured_chat_ids(config)
    if not token or not chat_ids:
        _record_delivery_state(failed=1, error="missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    chunks = _split_message(message, int(config.get("max_message_chars", 3900)))
    ok = True
    for chat_id in chat_ids:
        for chunk in chunks:
            ok = _send_chunk_with_retry(str(token), str(chat_id), chunk, config) and ok
    return ok


def send_test_message(message: Optional[str] = None) -> bool:
    """Synchronously deliver a Telegram test message for diagnostics."""
    body = message or f"Athena Telegram test - {datetime.now(timezone.utc).isoformat()}"
    return _deliver_message(body)


def send_signal_alert(signal: Dict[str, Any]) -> None:
    """Send a proactive Telegram alert for a trade signal (e.g. auto-executed).
    Honours TELEGRAM.enabled and token/chat from config.yaml or env.
    """
    if not _is_enabled():
        return
    pair = signal.get("pair", "?")
    direction = signal.get("direction", "?")
    score = float(signal.get("confluenceScore", 0) or 0)
    max_score = float(signal.get("maxScore", 3.0) or 3.0)
    entry = signal.get("price", "?")
    sl = signal.get("sl", "?")
    tp1 = signal.get("tp1", "?")
    rr = signal.get("rr1", "?")
    dir_emoji = "🟢" if direction == "LONG" else "🔴"

    text = (
        f"{dir_emoji} *AUTO-EXECUTED: {pair} {direction}*\n"
        f"Score: `{score:.2f}/{max_score:.1f}` | Entry: `{entry}`\n"
        f"SL: `{sl}` | TP: `{tp1}` | RR `1:{rr}`"
    )
    if signal.get("regime"):
        text += f"\nRegime: `{signal['regime']}`"
    momentum_tf = signal.get("momentum_tf")
    trigger_tf = signal.get("trigger_tf")
    if momentum_tf or trigger_tf:
        tf_bits = []
        if momentum_tf:
            tf_bits.append(f"Momentum `{momentum_tf}`")
        if trigger_tf:
            tf_bits.append(f"Trigger `{trigger_tf}`")
        text += "\nTFs: " + " | ".join(tf_bits)
    meta_bits = []
    if signal.get("ai_edge_prob") is not None:
        meta_bits.append(f"Edge: `{signal['ai_edge_prob']}%`")
    if signal.get("ai_risk"):
        meta_bits.append(f"Risk: `{signal['ai_risk']}`")
    if meta_bits:
        text += "\n" + " | ".join(meta_bits)

    _send_message_async(text)


def notify_signal_fired(
    pair: str,
    direction: str,
    score: float,
    dir_score: float,
    nondir_score: float,
    top_factors: List[str],
    regime: str,
) -> None:
    """Legacy notify — kept for backward compatibility. Prefer notify_signal_with_ai."""
    if not _is_enabled():
        return

    emoji = "Long" if direction == "LONG" else "Short"
    factors_str = ", ".join(top_factors[:3])

    message = (
        f"[{emoji}] *Signal — {pair}*\n"
        f"Score: `{score:.3f}` | Regime: `{regime}`\n"
        f"Factors: `{factors_str}`"
    )

    _send_message_async(message)
    _daily_stats["signals_fired"].append(
        {
            "pair": pair,
            "direction": direction,
            "score": score,
            "timestamp": datetime.now(timezone.utc),
        }
    )


def notify_signal_with_ai(
    pair: str,
    direction: str,
    score: float,
    max_score: float,
    entry: float,
    sl: float,
    tp1: float,
    rr1: float,
    regime: str,
    signal_class: str,
    ai_grade: str,
    ai_verdict: str,
    ai_edge_prob: int,
    ai_risk: str,
    ai_warnings: Optional[List[str]] = None,
    is_auto_executed: bool = False,
    momentum_tf: Optional[str] = None,
    trigger_tf: Optional[str] = None,
) -> None:
    """Send Telegram only for auto-executed high-confluence signals (70%+ score).
    Called from a background thread — never blocks the scan loop."""
    if not _is_enabled():
        return
    
    # Only notify if auto-executed AND high confluence (70%+ score)
    if not is_auto_executed:
        return

    score_pct = round(score / max_score * 100) if max_score else 0
    if score_pct < 70:
        return

    send_signal_alert(
        {
            "pair": pair,
            "direction": direction,
            "confluenceScore": score,
            "maxScore": max_score,
            "price": entry,
            "sl": sl,
            "tp1": tp1,
            "rr1": rr1,
            "regime": regime,
            "ai_edge_prob": ai_edge_prob,
            "ai_risk": ai_risk,
            "momentum_tf": momentum_tf,
            "trigger_tf": trigger_tf,
        }
    )
    _daily_stats["signals_fired"].append(
        {
            "pair": pair,
            "direction": direction,
            "score": score,
            "timestamp": datetime.now(timezone.utc),
        }
    )


def notify_trade_opened(
    pair: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    *,
    style: Optional[str] = None,
    engine: Optional[str] = None,
    exchange: Optional[str] = None,
) -> None:
    """Notify when a trade is opened"""
    if not _is_enabled():
        return

    emoji = "🟢" if direction == "LONG" else "🔴"

    message = (
        f"{emoji} *Trade Opened — {pair}*\n"
        f"Direction: `{direction}`\n"
        f"Entry: `{entry_price:.5f}`\n"
        f"Stop Loss: `{stop_loss:.5f}`\n"
        f"Take Profit: `{take_profit:.5f}`"
    )
    meta_bits = []
    if style:
        meta_bits.append(f"Style: `{style}`")
    if engine:
        meta_bits.append(f"Engine: `{engine}`")
    if exchange:
        meta_bits.append(f"Exch: `{exchange.upper()}`")
    if meta_bits:
        message += "\n" + " | ".join(meta_bits)

    _send_message_async(message)

    # Track for daily summary
    _daily_stats["trades_opened"].append(
        {
            "pair": pair,
            "direction": direction,
            "entry_price": entry_price,
            "timestamp": datetime.now(timezone.utc),
        }
    )


def notify_trade_closed(
    pair: str,
    pnl_r: float,
    is_win: bool,
    duration_minutes: float,
    *,
    exit_reason: Optional[str] = None,
    style: Optional[str] = None,
    exchange: Optional[str] = None,
) -> None:
    """Notify when a trade is closed"""
    if not _is_enabled():
        return

    config = _load_config()
    reason_key = str(exit_reason or "").strip().upper()
    if config.get("notify_trail_only") and reason_key and not reason_key.startswith("TRAIL"):
        return

    emoji = "✅" if is_win else "❌"
    result = "WIN" if is_win else "LOSS"

    message = (
        f"{emoji} *Trade Closed — {pair}*\n"
        f"P&L: `{pnl_r:+.2f}R` | Result: `{result}`\n"
        f"Duration: `{duration_minutes:.0f} min`"
    )
    if reason_key and config.get("notify_exit_reason", True):
        label = _EXIT_REASON_LABELS.get(reason_key, reason_key.replace("_", " ").title())
        message += f"\nExit: `{label}`"
    meta_bits = []
    if style:
        meta_bits.append(f"Style: `{style}`")
    if exchange:
        meta_bits.append(f"Exch: `{exchange.upper()}`")
    if meta_bits:
        message += "\n" + " | ".join(meta_bits)

    _send_message_async(message)

    # Track for daily summary
    _daily_stats["trades_closed"].append(
        {
            "pair": pair,
            "pnl_r": pnl_r,
            "is_win": is_win,
            "duration_minutes": duration_minutes,
            "timestamp": datetime.now(timezone.utc),
        }
    )


def notify_daily_summary() -> None:
    """Send daily summary at 22:00 UTC"""
    if not _is_enabled():
        return

    now = datetime.now(timezone.utc)

    # Only send at 22:00 UTC
    if now.time() < dt_time(22, 0) or now.time() >= dt_time(22, 1):
        return

    # Only send once per day
    if _daily_stats["last_summary_time"]:
        last_summary = _daily_stats["last_summary_time"]
        if now.date() == last_summary.date():
            return

    # Calculate daily stats
    signals_count = len(_daily_stats["signals_fired"])
    trades_opened_count = len(_daily_stats["trades_opened"])
    trades_closed_count = len(_daily_stats["trades_closed"])
    open_positions_count = len(_daily_stats["open_positions"])

    # Calculate P&L for closed trades
    total_pnl = sum(trade["pnl_r"] for trade in _daily_stats["trades_closed"])
    wins = sum(1 for trade in _daily_stats["trades_closed"] if trade["is_win"])
    win_rate = (wins / trades_closed_count * 100) if trades_closed_count > 0 else 0

    message = (
        f"📊 *Daily Summary — {now.strftime('%Y-%m-%d')}*\n"
        f"Signals Fired: `{signals_count}`\n"
        f"Trades Opened: `{trades_opened_count}`\n"
        f"Trades Closed: `{trades_closed_count}`\n"
        f"Open Positions: `{open_positions_count}`\n"
        f"Daily P&L: `{total_pnl:+.2f}R`\n"
        f"Win Rate: `{win_rate:.1f}%`"
    )

    _send_message_async(message)

    # Update last summary time and reset daily stats
    _daily_stats["last_summary_time"] = now
    _daily_stats["signals_fired"] = []
    _daily_stats["trades_opened"] = []
    _daily_stats["trades_closed"] = []
    _daily_stats["open_positions"] = []


def notify_score_decay(
    pair: str,
    direction: str,
    entry_score: float,
    cur_score: float,
    decay: float,
    direction_flip: bool = False,
    engine: str = "engine_a",
    entry_pct: Optional[float] = None,
    cur_pct: Optional[float] = None,
    ai_verdict: Optional[str] = None,
    ai_urgency: Optional[str] = None,
) -> None:
    """Send Telegram alert when an open position's score decays significantly."""
    if not _is_enabled():
        return

    # Determine display scale and labels based on engine
    is_engine_b = str(engine).lower() == "engine_b"
    _engine_label = "Engine B" if is_engine_b else "Engine A"
    
    if is_engine_b and entry_pct is not None and cur_pct is not None:
        _emoji = "🔴" if (entry_pct - cur_pct) >= 30 else "🟡"
        _score_path = f"`{entry_pct:.0f}%` → Now: `{cur_pct:.0f}%`"
        _decay_val = entry_pct - cur_pct
        _decay_label = f"Δ{_decay_val:.1f}%"
    else:
        _emoji = "🔴" if decay >= 3.0 else "🟡"
        _score_path = f"`{entry_score:.2f}` → Now: `{cur_score:.2f}`"
        _decay_label = f"Δ{decay:.1f}"

    flip_note = "\n⚠️ *DIRECTION FLIP — consider exiting*" if direction_flip else ""
    ai_note = ""
    if ai_verdict:
        urgency_emoji = {"HIGH": "🚨", "MEDIUM": "⚠️", "LOW": "ℹ️"}.get(ai_urgency or "", "")
        ai_note = f"\nAI: {urgency_emoji} `{ai_verdict}`"

    message = (
        f"{_emoji} *Score Decay Alert — {pair}*\n"
        f"Source: `{_engine_label}` | Direction: `{direction}`\n"
        f"Decay: `{_decay_label}` | Context: {_score_path}"
        f"{flip_note}{ai_note}"
    )

    _send_message_async(message)


def notify_bybit_ws_disconnect(symbol: Optional[str] = None) -> None:
    """Notify that the Bybit websocket disconnected and is reconnecting."""
    if not _is_enabled():
        return
    label = f" for `{symbol}`" if symbol else ""
    _send_message_async(f"Bybit websocket disconnected{label}. Reconnect backoff is active.")


def notify_polygon_rate_limit(symbol: Optional[str] = None) -> None:
    """Notify that Polygon rate limits are blocking candle fetches."""
    if not _is_enabled():
        return
    label = f" for `{symbol}`" if symbol else ""
    _send_message_async(f"Polygon rate limit hit{label}. Athena will use configured fallbacks.")


def update_open_positions(positions: List[Dict[str, Any]]) -> None:
    """Update current open positions for daily summary"""
    _daily_stats["open_positions"] = positions


# Background thread to check for daily summary time
def _daily_summary_worker():
    """Background worker that checks daily summary time"""
    time.sleep(
        10
    )  # Wait for main thread to finish load_dotenv() before first config read
    while True:
        try:
            notify_daily_summary()
        except Exception:
            pass
        time.sleep(60)  # Check every minute


def start_daily_summary_worker() -> None:
    """Start the daily summary worker during explicit application startup."""
    threading.Thread(target=_daily_summary_worker, daemon=True, name="TelegramDailySummary").start()
