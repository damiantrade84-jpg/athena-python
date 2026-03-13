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

import requests
import threading
import time
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional, Any
import json
import yaml
from pathlib import Path

# Global notification state
_config: Dict[str, Any] = {}
_daily_stats: Dict[str, Any] = {
    "signals_fired": [],
    "trades_opened": [],
    "trades_closed": [],
    "open_positions": [],
    "last_summary_time": None
}

def _load_config() -> Dict[str, Any]:
    """Load Telegram configuration from config.yaml"""
    global _config
    if _config:
        return _config
    
    config_path = Path(__file__).parent / "config.yaml"
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            _config = config.get('TELEGRAM', {})
    except Exception:
        _config = {}

    import os
    _env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    _env_chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if _env_token:
        _config["token"] = _env_token
    if _env_chat:
        _config["chat_id"] = _env_chat

    return _config

def _is_enabled() -> bool:
    """Check if Telegram notifications are enabled"""
    config = _load_config()
    return config.get('enabled', False)

def _send_message_async(message: str) -> None:
    """Send message to Telegram in background thread"""
    if not _is_enabled():
        return
    
    def _send():
        try:
            config = _load_config()
            token = config.get('token')
            chat_id = config.get('chat_id')
            
            if not token or not chat_id:
                return
            
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            requests.post(url, json=payload, timeout=10)
        except Exception:
            # Silently fail to avoid disrupting main trading logic
            pass
    
    # Fire and forget in background thread
    threading.Thread(target=_send, daemon=True).start()

def notify_signal_fired(pair: str, direction: str, score: float, 
                       dir_score: float, nondir_score: float,
                       top_factors: List[str], regime: str) -> None:
    """Notify when a signal crosses MIN_CONFLUENCE_CLASS threshold"""
    if not _is_enabled():
        return
    
    emoji = "🟢" if direction == "LONG" else "🔴"
    
    # Format top factors
    factors_str = ", ".join(top_factors[:3])  # Show top 3
    
    message = (
        f"{emoji} *Signal Fired — {pair}*\n"
        f"Direction: `{direction}` | Score: `{score:.3f}`\n"
        f"Dir Score: `{dir_score:.3f}` | Non-Dir: `{nondir_score:.3f}`\n"
        f"Regime: `{regime}`\n"
        f"Top Factors: `{factors_str}`"
    )
    
    _send_message_async(message)
    
    # Track for daily summary
    _daily_stats["signals_fired"].append({
        "pair": pair,
        "direction": direction,
        "score": score,
        "timestamp": datetime.utcnow()
    })

def notify_trade_opened(pair: str, direction: str, entry_price: float,
                       stop_loss: float, take_profit: float) -> None:
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
    
    _send_message_async(message)
    
    # Track for daily summary
    _daily_stats["trades_opened"].append({
        "pair": pair,
        "direction": direction,
        "entry_price": entry_price,
        "timestamp": datetime.utcnow()
    })

def notify_trade_closed(pair: str, pnl_r: float, is_win: bool, 
                       duration_minutes: float) -> None:
    """Notify when a trade is closed"""
    if not _is_enabled():
        return
    
    emoji = "✅" if is_win else "❌"
    result = "WIN" if is_win else "LOSS"
    
    message = (
        f"{emoji} *Trade Closed — {pair}*\n"
        f"P&L: `{pnl_r:+.2f}R` | Result: `{result}`\n"
        f"Duration: `{duration_minutes:.0f} min`"
    )
    
    _send_message_async(message)
    
    # Track for daily summary
    _daily_stats["trades_closed"].append({
        "pair": pair,
        "pnl_r": pnl_r,
        "is_win": is_win,
        "duration_minutes": duration_minutes,
        "timestamp": datetime.utcnow()
    })

def notify_bybit_ws_disconnect() -> None:
    """Notify when Bybit WebSocket connection drops"""
    if not _is_enabled():
        return
    
    message = "⚠️ *Bybit WebSocket Disconnected*\nConnection dropped, attempting to reconnect..."
    _send_message_async(message)

def notify_polygon_rate_limit() -> None:
    """Notify when Polygon API returns 429 rate limit error"""
    if not _is_enabled():
        return
    
    message = "⚠️ *Polygon API Rate Limited*\n429 error received, backing off requests..."
    _send_message_async(message)

def notify_daily_summary() -> None:
    """Send daily summary at 22:00 UTC"""
    if not _is_enabled():
        return
    
    now = datetime.utcnow()
    
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

def update_open_positions(positions: List[Dict[str, Any]]) -> None:
    """Update current open positions for daily summary"""
    _daily_stats["open_positions"] = positions

# Background thread to check for daily summary time
def _daily_summary_worker():
    """Background worker that checks daily summary time"""
    while True:
        try:
            notify_daily_summary()
        except Exception:
            pass
        time.sleep(60)  # Check every minute

# Start daily summary worker thread
threading.Thread(target=_daily_summary_worker, daemon=True).start()
