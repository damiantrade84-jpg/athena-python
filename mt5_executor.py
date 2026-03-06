"""mt5_executor.py — MetaTrader 5 execution module for Athena.

Handles connection, symbol mapping, and order placement.
This module is DELIBERATELY DUMB — it cannot size orders.
All orders must arrive as a pre-validated RiskApproval from risk_engine.py.
"""
import os
import logging
import time

log = logging.getLogger("athena")

# ── Lazy MT5 import (only needed when execution is used) ─────────────────────
_mt5 = None

def _get_mt5():
    """Lazy-import MetaTrader5 so the rest of Athena works without it installed."""
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5
            _mt5 = MetaTrader5
        except ImportError:
            log.error("[MT5] MetaTrader5 package not installed. Run: pip install MetaTrader5")
            return None
    return _mt5


# ── Symbol mapping: Athena display name → MT5 symbol ─────────────────────────
_MT5_SYMBOL_MAP = {
    # Forex
    "EUR/USD":   "EURUSD",
    "GBP/USD":   "GBPUSD",
    "USD/JPY":   "USDJPY",
    "AUD/USD":   "AUDUSD",
    "NZD/USD":   "NZDUSD",
    "USD/CAD":   "USDCAD",
    "USD/CHF":   "USDCHF",
    "EUR/JPY":   "EURJPY",
    "GBP/JPY":   "GBPJPY",
    "EUR/AUD":   "EURAUD",
    "EUR/GBP":   "EURGBP",
    "AUD/JPY":   "AUDJPY",
    "GBP/AUD":   "GBPAUD",
    "USD/ZAR":   "USDZAR",
    "USD/MXN":   "USDMXN",
    "EUR/CHF":   "EURCHF",
    "USD/SGD":   "USDSGD",
    # Commodities
    "XAU/USD":   "XAUUSD",
    "XAG/USD":   "XAGUSD",
    "WTI Oil":   "USOUSD",       # Pepperstone WTI CFD — verify in MT5 Market Watch
    # Indices
    "S&P 500":   "US500",
    "Nasdaq":    "USTEC",
    "Dow Jones": "US30",
    "FTSE 100":  "UK100",
    # US Stocks (CFDs on Pepperstone)
    "AAPL":  "AAPL",
    "TSLA":  "TSLA",
    "NVDA":  "NVDA",
    "MSFT":  "MSFT",
    "AMZN":  "AMZN",
    "META":  "META",
    "GOOG":  "GOOG",
    "JPM":   "JPM",
    "V":     "V",
    "XOM":   "XOM",
    "SPY":   "SPY",
    "QQQ":   "QQQ",
    "GLD":   "GLD",
    "TLT":   "TLT",
}

# Connection state
_connected = False
_last_connect_attempt = 0
_RECONNECT_COOLDOWN = 30  # seconds between reconnect attempts


def mt5_map_symbol(athena_display: str) -> str | None:
    """Map Athena display name to MT5 symbol. Returns None if no mapping exists."""
    mt5_sym = _MT5_SYMBOL_MAP.get(athena_display)
    if mt5_sym:
        return mt5_sym
    # Fallback: try stripping / and common suffixes
    stripped = athena_display.replace("/", "").replace(" ", "")
    return stripped if stripped else None


def mt5_connect() -> bool:
    """Initialize MT5 terminal connection. Uses env vars for credentials."""
    global _connected, _last_connect_attempt
    mt5 = _get_mt5()
    if mt5 is None:
        return False

    now = time.time()
    if _connected and mt5.terminal_info() is not None:
        return True
    if now - _last_connect_attempt < _RECONNECT_COOLDOWN:
        return False

    _last_connect_attempt = now

    if not mt5.initialize():
        log.error(f"[MT5] initialize() failed: {mt5.last_error()}")
        _connected = False
        return False

    # Login if credentials provided
    login = os.environ.get("MT5_LOGIN", "")
    password = os.environ.get("MT5_PASSWORD", "")
    server = os.environ.get("MT5_SERVER", "")

    if login and password and server:
        try:
            login_int = int(login)
        except ValueError:
            log.error(f"[MT5] MT5_LOGIN must be a number, got: {login}")
            _connected = False
            return False

        if not mt5.login(login=login_int, password=password, server=server):
            log.error(f"[MT5] login failed: {mt5.last_error()}")
            _connected = False
            return False
        log.info(f"[MT5] Logged in to {server} (account {login_int})")
    else:
        log.info("[MT5] Connected to terminal (no explicit login — using terminal's active account)")

    _connected = True
    info = mt5.account_info()
    if info:
        log.info(f"[MT5] Account: {info.login} | Balance: {info.balance} | Equity: {info.equity} | Server: {info.server}")
    return True


def mt5_disconnect():
    """Shutdown MT5 connection."""
    global _connected
    mt5 = _get_mt5()
    if mt5:
        mt5.shutdown()
    _connected = False
    log.info("[MT5] Disconnected")


def mt5_get_account() -> dict | None:
    """Get current account info (balance, equity, etc.)."""
    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return None
    info = mt5.account_info()
    if not info:
        return None
    return {
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "freeMargin": info.margin_free,
        "profit": info.profit,
        "currency": info.currency,
    }


def mt5_get_positions() -> list:
    """Get all open positions. Returns list of dicts with pair + risk info."""
    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return []
    positions = mt5.positions_get()
    if not positions:
        return []

    result = []
    # Reverse map: MT5 symbol → Athena display name
    reverse_map = {v: k for k, v in _MT5_SYMBOL_MAP.items()}

    for pos in positions:
        athena_pair = reverse_map.get(pos.symbol, pos.symbol)
        sl_dist = abs(pos.price_open - pos.sl) if pos.sl > 0 else 0
        # Approximate risk amount from SL distance
        risk_amount = sl_dist * pos.volume * 100_000 * 0.00001  # Simplified
        result.append({
            "ticket": pos.ticket,
            "pair": athena_pair,
            "symbol": pos.symbol,
            "direction": "LONG" if pos.type == 0 else "SHORT",
            "volume": pos.volume,
            "entry": pos.price_open,
            "sl": pos.sl,
            "tp": pos.tp,
            "profit": pos.profit,
            "risk_amount": risk_amount,
        })
    return result


def mt5_get_symbol_info(athena_display: str) -> dict | None:
    """Get MT5 symbol info for risk engine calculations."""
    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return None

    mt5_symbol = mt5_map_symbol(athena_display)
    if not mt5_symbol:
        log.warning(f"[MT5] No symbol mapping for '{athena_display}'")
        return None

    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        # Try enabling the symbol in Market Watch
        if not mt5.symbol_select(mt5_symbol, True):
            log.warning(f"[MT5] Symbol '{mt5_symbol}' not found in MT5")
            return None
        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            return None

    return {
        "symbol": info.name,
        "description": info.description,
        "point": info.point,
        "digits": info.digits,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "trade_contract_size": info.trade_contract_size,
        "trade_tick_value": info.trade_tick_value,
        "trade_tick_size": info.trade_tick_size,
        "bid": info.bid,
        "ask": info.ask,
        "spread": info.spread,
    }


def mt5_execute(signal: dict, approval: "RiskApproval") -> dict:
    """Execute a trade on MT5. ONLY accepts a pre-validated RiskApproval.

    Args:
        signal: Full signal from analyze_pair() — needs pair, direction, price, sl, tp1
        approval: RiskApproval from risk_engine.risk_check() — MUST be approved

    Returns:
        dict with success, ticket, volume, entry_price, error
    """
    from risk_engine import RiskApproval  # Type check

    if not isinstance(approval, RiskApproval):
        return {"success": False, "error": "INVALID_APPROVAL: must be RiskApproval from risk_engine"}

    if not approval.approved:
        return {"success": False, "error": f"NOT_APPROVED: {approval.reason}"}

    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return {"success": False, "error": "MT5_NOT_CONNECTED"}

    pair = signal.get("pair", "")
    mt5_symbol = mt5_map_symbol(pair)
    if not mt5_symbol:
        return {"success": False, "error": f"NO_SYMBOL_MAPPING: {pair}"}

    # Ensure symbol is in Market Watch
    if not mt5.symbol_select(mt5_symbol, True):
        return {"success": False, "error": f"SYMBOL_NOT_AVAILABLE: {mt5_symbol}"}

    direction = signal.get("direction", "")
    if direction not in ("LONG", "SHORT"):
        return {"success": False, "error": f"INVALID_DIRECTION: {direction}"}

    # Get live price for order
    tick = mt5.symbol_info_tick(mt5_symbol)
    if not tick:
        return {"success": False, "error": f"NO_TICK_DATA: {mt5_symbol}"}

    order_type = mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "LONG" else tick.bid

    # Get symbol info for proper rounding
    sym_info = mt5.symbol_info(mt5_symbol)
    digits = sym_info.digits if sym_info else 5

    sl = round(float(signal.get("sl", 0)), digits)
    tp = round(float(signal.get("tp1", 0)), digits)  # Use TP1 as primary target
    price = round(price, digits)

    # Build order request
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": mt5_symbol,
        "volume": approval.volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,  # Max slippage in points
        "magic": 240601,  # Athena magic number for trade identification
        "comment": f"Athena|{pair}|Score:{signal.get('confluenceScore', 0)}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    log.info(f"[MT5] Sending order: {direction} {approval.volume} {mt5_symbol} @ {price} | SL: {sl} | TP: {tp}")

    result = mt5.order_send(request)
    if result is None:
        error = mt5.last_error()
        log.error(f"[MT5] order_send returned None: {error}")
        return {"success": False, "error": f"ORDER_SEND_FAILED: {error}"}

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error(f"[MT5] Order rejected: retcode={result.retcode} comment={result.comment}")
        return {
            "success": False,
            "error": f"ORDER_REJECTED: {result.comment}",
            "retcode": result.retcode,
        }

    log.info(f"[MT5] ORDER FILLED: ticket={result.order} | {direction} {result.volume} {mt5_symbol} @ {result.price}")
    return {
        "success": True,
        "ticket": result.order,
        "volume": result.volume,
        "entryPrice": result.price,
        "symbol": mt5_symbol,
        "direction": direction,
        "sl": sl,
        "tp": tp,
        "riskAmount": approval.risk_amount,
        "riskPct": approval.risk_pct,
    }


def mt5_list_symbols() -> list:
    """List all available MT5 symbols (useful for debugging symbol mapping)."""
    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return []
    symbols = mt5.symbols_get()
    if not symbols:
        return []
    return [{"name": s.name, "description": s.description, "path": s.path} for s in symbols[:200]]
