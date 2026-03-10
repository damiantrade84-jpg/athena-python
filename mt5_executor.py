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
    "Brent Oil": "SPOTBRENT",    # Pepperstone Brent Crude CFD
    "Nat Gas":   "NATGAS",       # Pepperstone Natural Gas CFD
    "XPT/USD":   "XPTUSD",      # Pepperstone Platinum CFD
    "XPD/USD":   "XPDUSD",      # Pepperstone Palladium CFD
    "Copper":    "COPPER",       # Pepperstone Copper CFD
    # Indices
    "S&P 500":      "US500",
    "Nasdaq":       "USTEC",
    "Dow Jones":    "US30",
    "DAX 40":       "GER40",
    "FTSE 100":     "UK100",
    "ASX 200":      "AUS200",
    "Nikkei 225":   "JPN225",
    "Hang Seng":    "HK50",
    "Euro Stoxx 50":"EUSTX50",
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
    "NFLX":  "NFLX",
    "AMD":   "AMD",
    "CRM":   "CRM",
    "DIS":   "DIS",
    "BA":    "BA",
    "COIN":  "COIN",
    "PYPL":  "PYPL",
    "INTC":  "INTC",
    "UBER":  "UBER",
    "PLTR":  "PLTR",
    # ETFs
    "SPY":   "SPY",
    "QQQ":   "QQQ",
    "GLD":   "GLD",
    "TLT":   "TLT",
    "IWM":   "IWM",
    "EEM":   "EEM",
    "XLF":   "XLF",
    "XLE":   "XLE",
    "SLV":   "SLV",
    "USO":   "USO",
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
        risk_amount = 0.0
        if sl_dist > 0:
            info = mt5.symbol_info(pos.symbol)
            if info:
                tick_size = info.trade_tick_size or info.point or 0
                tick_value = info.trade_tick_value
                if tick_value == 0 and info.trade_contract_size and info.point:
                    tick_value = info.trade_contract_size * info.point
                if tick_size and tick_value:
                    risk_amount = (sl_dist / tick_size) * tick_value * pos.volume
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
            "risk_amount": round(risk_amount, 2),
        })
    return result


def mt5_close_position(ticket: int) -> dict:
    """Close an open MT5 position by ticket number."""
    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return {"success": False, "error": "MT5 not connected"}
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return {"success": False, "error": f"Position {ticket} not found"}
    pos = positions[0]
    close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return {"success": False, "error": f"No tick data for {pos.symbol}"}
    price = tick.bid if pos.type == 0 else tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": close_type,
        "price": price,
        "position": ticket,
        "magic": 240601,
        "comment": "Athena|CLOSE",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(f"[MT5] Manual close ticket={ticket} price={price}")
        return {"success": True, "ticket": ticket, "closePrice": price}
    err = result.comment if result else "order_send failed"
    log.error(f"[MT5] Close failed ticket={ticket}: {err}")
    return {"success": False, "error": err}


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

    # Guard: reject if price is 0 (market closed / no tick)
    if not price or price <= 0:
        return {"success": False, "error": f"MARKET_CLOSED: {mt5_symbol} price is 0 — market likely closed"}

    # Get symbol info for proper rounding
    sym_info = mt5.symbol_info(mt5_symbol)
    digits = sym_info.digits if sym_info else 5

    sl = round(float(signal.get("sl", 0)), digits)
    tp = round(float(signal.get("tp1", 0)), digits)  # Use TP1 as primary target
    price = round(price, digits)

    # ── Shift SL/TP to live fill price if signal price has drifted ─────────
    # Handles data-source mismatch (e.g. EODHD vs MT5 live feed for commodities)
    signal_price = float(signal.get("price", 0))
    if signal_price > 0 and sl != 0 and tp != 0:
        drift = abs(price - signal_price) / signal_price
        if drift > 0.01:  # >1% drift — rebase levels to live price
            sl_offset = float(signal.get("sl", 0)) - signal_price
            tp_offset = float(signal.get("tp1", 0)) - signal_price
            sl = round(price + sl_offset, digits)
            tp = round(price + tp_offset, digits)
            log.info(f"[MT5] {mt5_symbol}: price drift {drift:.1%} ({signal_price}→{price}) — rebased SL={sl} TP={tp}")

    # ── SL/TP validation against live price ────────────────────────────────
    # Ensure SL/TP are on the correct side of entry price
    if direction == "LONG":
        if sl >= price:
            log.error(f"[MT5] {mt5_symbol} LONG: SL {sl} >= entry {price} — invalid (SL must be below entry)")
            return {"success": False, "error": f"INVALID_SL: SL {sl} is above entry {price} for LONG"}
        if tp <= price:
            log.error(f"[MT5] {mt5_symbol} LONG: TP {tp} <= entry {price} — invalid (TP must be above entry)")
            return {"success": False, "error": f"INVALID_TP: TP {tp} is below entry {price} for LONG"}
    else:  # SHORT
        if sl <= price:
            log.error(f"[MT5] {mt5_symbol} SHORT: SL {sl} <= entry {price} — invalid (SL must be above entry)")
            return {"success": False, "error": f"INVALID_SL: SL {sl} is below entry {price} for SHORT"}
        if tp >= price:
            log.error(f"[MT5] {mt5_symbol} SHORT: TP {tp} >= entry {price} — invalid (TP must be below entry)")
            return {"success": False, "error": f"INVALID_TP: TP {tp} is above entry {price} for SHORT"}

    # Ensure SL distance is reasonable (not more than 30% of price — data scale mismatch guard)
    sl_dist_pct = abs(price - sl) / price
    if sl_dist_pct > 0.30:
        log.error(f"[MT5] {mt5_symbol}: SL distance {sl_dist_pct:.1%} of price — likely data scale mismatch")
        return {"success": False, "error": f"SL_TOO_FAR: SL is {sl_dist_pct:.0%} from entry (max 30%) — possible data mismatch"}

    # Check broker minimum stop distance
    if sym_info:
        min_stop_pts = sym_info.trade_stops_level  # in points
        point = sym_info.point
        min_stop_price = min_stop_pts * point if min_stop_pts and point else 0
        if min_stop_price > 0 and abs(price - sl) < min_stop_price:
            log.error(f"[MT5] {mt5_symbol}: SL too close — {abs(price-sl):.{digits}f} < min {min_stop_price:.{digits}f}")
            return {"success": False, "error": f"SL_TOO_CLOSE: distance {abs(price-sl):.{digits}f} < broker min {min_stop_price:.{digits}f}"}

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

    # Place TP2 as a separate pending limit order at half the volume (second target)
    tp2_ticket = None
    tp2_raw = signal.get("tp2", 0)
    tp2 = round(float(tp2_raw), digits) if tp2_raw else 0
    if tp2 and tp2 != tp and result.volume > 0:
        half_vol = round(result.volume / 2, 2)
        if half_vol >= 0.01:
            tp2_type = mt5.ORDER_TYPE_SELL_LIMIT if direction == "LONG" else mt5.ORDER_TYPE_BUY_LIMIT
            tp2_req = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": mt5_symbol,
                "volume": half_vol,
                "type": tp2_type,
                "price": tp2,
                "sl": sl,
                "deviation": 20,
                "magic": 240601,
                "comment": f"Athena|{pair}|TP2",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            tp2_result = mt5.order_send(tp2_req)
            if tp2_result and tp2_result.retcode == mt5.TRADE_RETCODE_DONE:
                tp2_ticket = tp2_result.order
                log.info(f"[MT5] TP2 pending order placed: ticket={tp2_ticket} @ {tp2} ({half_vol} lots)")
            else:
                _rc = tp2_result.retcode if tp2_result else "None"
                log.warning(f"[MT5] TP2 order failed (retcode={_rc}) — manage TP2 manually at {tp2}")

    return {
        "success": True,
        "ticket": result.order,
        "volume": result.volume,
        "entryPrice": result.price,
        "symbol": mt5_symbol,
        "direction": direction,
        "sl": sl,
        "tp": tp,
        "tp2": tp2,
        "tp2Ticket": tp2_ticket,
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
