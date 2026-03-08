"""bybit_executor.py — Bybit USDT Perpetual Futures execution module for Athena.

Replaces ccxt_executor.py for crypto signal execution.
Supports LONG and SHORT via Bybit Linear (USDT-M) Perpetual contracts.
Leverage: 1x, Margin: ISOLATED (safest configuration).
All orders must arrive as a pre-validated RiskApproval from risk_engine.py.
"""
import os
import logging

log = logging.getLogger("athena")

_exchange = None

# Symbol mapping: Athena display/internal → ccxt linear futures format
_SYMBOL_MAP = {
    "BTC/USDT":    "BTC/USDT:USDT",
    "ETH/USDT":    "ETH/USDT:USDT",
    "XRP/USDT":    "XRP/USDT:USDT",
    "SOL/USDT":    "SOL/USDT:USDT",
    "ADA/USDT":    "ADA/USDT:USDT",
    "DOGE/USDT":   "DOGE/USDT:USDT",
    "AVAX/USDT":   "AVAX/USDT:USDT",
    "LINK/USDT":   "LINK/USDT:USDT",
    "MATIC/USDT":  "MATIC/USDT:USDT",
    "BNB/USDT":    "BNB/USDT:USDT",
    "DOT/USDT":    "DOT/USDT:USDT",
    "LTC/USDT":    "LTC/USDT:USDT",
    "SUI/USDT":    "SUI/USDT:USDT",
    "NEAR/USDT":   "NEAR/USDT:USDT",
    "APT/USDT":    "APT/USDT:USDT",
    "INJ/USDT":    "INJ/USDT:USDT",
    "FET/USDT":    "FET/USDT:USDT",
    "RENDER/USDT": "RENDER/USDT:USDT",
}

# Reverse map from internal symbol (BTCUSDT) to ccxt format
_INTERNAL_MAP = {
    "BTCUSDT":    "BTC/USDT:USDT",
    "ETHUSDT":    "ETH/USDT:USDT",
    "XRPUSDT":    "XRP/USDT:USDT",
    "SOLUSDT":    "SOL/USDT:USDT",
    "ADAUSDT":    "ADA/USDT:USDT",
    "DOGEUSDT":   "DOGE/USDT:USDT",
    "AVAXUSDT":   "AVAX/USDT:USDT",
    "LINKUSDT":   "LINK/USDT:USDT",
    "MATICUSDT":  "MATIC/USDT:USDT",
    "BNBUSDT":    "BNB/USDT:USDT",
    "DOTUSDT":    "DOT/USDT:USDT",
    "LTCUSDT":    "LTC/USDT:USDT",
    "SUIUSDT":    "SUI/USDT:USDT",
    "NEARUSDT":   "NEAR/USDT:USDT",
    "APTUSDT":    "APT/USDT:USDT",
    "INJUSDT":    "INJ/USDT:USDT",
    "FETUSDT":    "FET/USDT:USDT",
    "RENDERUSDT": "RENDER/USDT:USDT",
}

_LEVERAGE = 1  # 1x — enables SHORT without amplified risk


def _get_exchange():
    """Initialize Bybit Linear Futures connection."""
    global _exchange
    if _exchange is not None:
        return _exchange

    try:
        import ccxt
    except ImportError:
        log.error("[BYBIT] ccxt not installed. Run: pip install ccxt")
        return None

    api_key    = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")

    if not api_key or not api_secret or api_key == "REVOKE_AND_REPLACE":
        log.warning("[BYBIT] BYBIT_API_KEY / BYBIT_API_SECRET not set or placeholder")
        return None

    use_testnet = os.environ.get("BYBIT_TESTNET", "false").lower() in ("true", "1", "yes")
    use_demo    = os.environ.get("BYBIT_DEMO", "false").lower() in ("true", "1", "yes")

    try:
        _exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "linear",   # USDT Perpetual
                "defaultSettle": "USDT",
            },
        })

        if use_testnet:
            _exchange.set_sandbox_mode(True)

        if use_demo:
            _exchange.enable_demo_trading(True)

        env_label = "DEMO" if use_demo else ("TESTNET" if use_testnet else "LIVE")
        log.info(f"[BYBIT] Bybit Linear Futures {env_label} connected")
        return _exchange

    except Exception as e:
        log.error(f"[BYBIT] Connection failed: {e}")
        _exchange = None
        return None


def bybit_map_symbol(athena_display: str) -> str | None:
    """Map Athena display name or internal symbol to ccxt futures format."""
    sym = _SYMBOL_MAP.get(athena_display)
    if sym:
        return sym
    sym = _INTERNAL_MAP.get(athena_display)
    if sym:
        return sym
    # Fallback: auto-construct
    if "USDT" in athena_display and "/" not in athena_display:
        base = athena_display.replace("USDT", "")
        return f"{base}/USDT:USDT"
    if "/USDT" in athena_display:
        return athena_display + ":USDT"
    return None


def _ensure_leverage(exchange, symbol: str):
    """Set leverage=1x and margin=ISOLATED for a symbol. Silent if already set."""
    try:
        exchange.set_leverage(_LEVERAGE, symbol, params={"marginMode": "ISOLATED"})
        log.info(f"[BYBIT] leverage={_LEVERAGE}x ISOLATED set for {symbol}")
    except Exception as e:
        # Often throws if already set — not a fatal error
        log.debug(f"[BYBIT] set_leverage note for {symbol}: {e}")


def bybit_connect() -> bool:
    """Test Bybit connection."""
    exchange = _get_exchange()
    if not exchange:
        return False
    try:
        exchange.fetch_time()
        return True
    except Exception as e:
        log.error(f"[BYBIT] Connection test failed: {e}")
        return False


def bybit_get_account() -> dict | None:
    """Get Bybit futures wallet balance (USDT)."""
    exchange = _get_exchange()
    if not exchange:
        return None
    try:
        balance = exchange.fetch_balance(params={"type": "linear"})
        usdt = balance.get("USDT", {})
        total = usdt.get("total", 0) or 0
        free  = usdt.get("free",  0) or 0
        return {
            "exchange": "Bybit",
            "testnet": os.environ.get("BYBIT_TESTNET", "true").lower() in ("true", "1", "yes"),
            "balance": total,
            "equity": total,
            "freeBalance": free,
            "currency": "USDT",
        }
    except Exception as e:
        log.error(f"[BYBIT] Failed to fetch balance: {e}")
        return None


def bybit_get_positions() -> list:
    """Get open Bybit Linear Futures positions with risk estimates."""
    exchange = _get_exchange()
    if not exchange:
        return []
    try:
        raw = exchange.fetch_positions(params={"type": "linear"})
        positions = []
        for pos in raw:
            size = abs(float(pos.get("contracts", 0) or 0))
            if size <= 0:
                continue
            entry  = float(pos.get("entryPrice", 0) or 0)
            sl_est = entry * 0.98  # fallback 2% SL estimate if no SL set
            notional = size * entry
            est_risk = round(notional * 0.02, 2)
            symbol_raw = pos.get("symbol", "")
            # Convert BTC/USDT:USDT back to display format BTC/USDT
            display = symbol_raw.split(":")[0] if ":" in symbol_raw else symbol_raw
            positions.append({
                "pair": display,
                "symbol": symbol_raw,
                "volume": size,
                "side": pos.get("side", ""),
                "entryPrice": entry,
                "unrealizedPnl": float(pos.get("unrealizedPnl", 0) or 0),
                "notional": round(notional, 2),
                "risk_amount": est_risk,
            })
        return positions
    except Exception as e:
        log.error(f"[BYBIT] Failed to fetch positions: {e}")
        return []


def bybit_get_symbol_info(athena_display: str) -> dict | None:
    """Get Bybit symbol info for risk engine calculations."""
    exchange = _get_exchange()
    if not exchange:
        return None

    ccxt_symbol = bybit_map_symbol(athena_display)
    if not ccxt_symbol:
        log.warning(f"[BYBIT] No symbol mapping for '{athena_display}'")
        return None

    try:
        exchange.load_markets()
        market = exchange.market(ccxt_symbol)
        if not market:
            return None

        ticker = exchange.fetch_ticker(ccxt_symbol)
        price = ticker.get("last", 0) or 0

        tick_size   = market["precision"].get("price", 0.01)
        tick_value  = tick_size  # For USDT pairs: 1 unit move = tick_size USDT
        vol_min     = market["limits"]["amount"].get("min", 0.001) or 0.001
        vol_max     = market["limits"]["amount"].get("max", 9999) or 9999
        vol_step    = market["precision"].get("amount", 0.001) or 0.001

        return {
            "symbol": ccxt_symbol,
            "description": market.get("baseId", ccxt_symbol),
            "point": tick_size,
            "digits": len(str(tick_size).split(".")[-1]) if isinstance(tick_size, float) else 2,
            "volume_min": vol_min,
            "volume_max": vol_max,
            "volume_step": vol_step,
            "trade_contract_size": 1,
            "trade_tick_value": tick_value,
            "trade_tick_size": tick_size,
            "bid": price,
            "ask": price,
            "spread": 0,
        }
    except Exception as e:
        log.error(f"[BYBIT] Failed to get symbol info for {athena_display}: {e}")
        return None


def bybit_execute(signal: dict, approval: "RiskApproval") -> dict:
    """Execute a crypto trade on Bybit USDT Perpetual Futures.

    LONG  signal → market BUY  order (open long position)
    SHORT signal → market SELL order (open short position)
    SL placed as stop-market order after entry fill.
    TP placed as take-profit-market order after entry fill.

    Args:
        signal:   Full signal from analyze_pair()
        approval: RiskApproval from risk_engine.risk_check() — must be approved

    Returns:
        dict with success, ticket, volume, entryPrice, direction, sl, tp, error
    """
    from risk_engine import RiskApproval

    if not isinstance(approval, RiskApproval):
        return {"success": False, "error": "INVALID_APPROVAL"}
    if not approval.approved:
        return {"success": False, "error": f"NOT_APPROVED: {approval.reason}"}

    exchange = _get_exchange()
    if not exchange:
        return {"success": False, "error": "BYBIT_NOT_CONNECTED"}

    pair        = signal.get("pair", "")
    ccxt_symbol = bybit_map_symbol(pair) or bybit_map_symbol(signal.get("symbol", ""))
    if not ccxt_symbol:
        return {"success": False, "error": f"NO_SYMBOL_MAPPING: {pair}"}

    direction = signal.get("direction", "")
    if direction not in ("LONG", "SHORT"):
        return {"success": False, "error": f"INVALID_DIRECTION: {direction}"}

    # Ensure 1x leverage + isolated margin before placing order
    _ensure_leverage(exchange, ccxt_symbol)

    try:
        volume = approval.volume

        # Resolve live price
        ticker = exchange.fetch_ticker(ccxt_symbol)
        price  = ticker.get("ask" if direction == "LONG" else "bid", 0) or ticker.get("last", 0)
        if price <= 0:
            return {"success": False, "error": f"NO_PRICE_DATA: {ccxt_symbol}"}

        # Recalculate volume in base units if needed (risk_amount / SL distance)
        sl = signal.get("sl", 0)
        if volume < 1 and price > 100 and sl:
            sl_dist = abs(signal.get("price", price) - sl)
            if sl_dist > 0:
                volume = round(approval.risk_amount / sl_dist, 6)

        side = "buy" if direction == "LONG" else "sell"
        log.info(f"[BYBIT] Placing {side.upper()} market order: {volume} {ccxt_symbol} @ ~{price}")

        # Market entry order — 3-attempt retry on transient errors
        import time as _time
        order = None
        _last_err = None
        for _attempt in range(3):
            try:
                order = exchange.create_market_order(
                    ccxt_symbol, side, volume,
                    params={"positionIdx": 0}  # one-way mode
                )
                break
            except Exception as _oe:
                _oe_name = type(_oe).__name__
                if any(s in _oe_name for s in ("NetworkError", "RequestTimeout", "ExchangeNotAvailable")):
                    _last_err = _oe
                    log.warning(f"[BYBIT] Order attempt {_attempt + 1}/3 failed ({_oe_name}), retrying...")
                    _time.sleep(1)
                else:
                    raise
        if order is None:
            raise _last_err

        order_id      = order.get("id", "")
        filled_price  = float(order.get("average") or order.get("price") or price)
        filled_amount = float(order.get("filled") or volume)

        log.info(f"[BYBIT] ENTRY FILLED: id={order_id} | {direction} {filled_amount} {ccxt_symbol} @ {filled_price}")

        # Stop-loss order
        sl_order_id = None
        if sl and sl > 0:
            try:
                sl_side = "sell" if direction == "LONG" else "buy"
                # triggerDirection: 1=ascending (price rises to trigger), 2=descending (price falls to trigger)
                sl_trigger = "2" if direction == "LONG" else "1"  # LONG SL triggers on price falling
                sl_order = exchange.create_order(
                    ccxt_symbol, "stop_market", sl_side, filled_amount,
                    params={
                        "stopPrice": sl,
                        "triggerDirection": sl_trigger,
                        "reduceOnly": True,
                        "positionIdx": 0,
                    }
                )
                sl_order_id = sl_order.get("id")
                log.info(f"[BYBIT] SL order placed: id={sl_order_id} @ {sl}")
            except Exception as sle:
                log.warning(f"[BYBIT] SL order failed (manage manually): {sle}")

        # Take-profit order (TP1)
        tp1 = signal.get("tp1", 0)
        tp_order_id = None
        if tp1 and tp1 > 0:
            try:
                tp_side = "sell" if direction == "LONG" else "buy"
                # triggerDirection: LONG TP triggers on price rising, SHORT TP on price falling
                tp_trigger = "1" if direction == "LONG" else "2"
                tp_order = exchange.create_order(
                    ccxt_symbol, "take_profit_market", tp_side, filled_amount,
                    params={
                        "stopPrice": tp1,
                        "triggerDirection": tp_trigger,
                        "reduceOnly": True,
                        "positionIdx": 0,
                    }
                )
                tp_order_id = tp_order.get("id")
                log.info(f"[BYBIT] TP order placed: id={tp_order_id} @ {tp1}")
            except Exception as tpe:
                log.warning(f"[BYBIT] TP order failed (manage manually): {tpe}")

        return {
            "success": True,
            "ticket": order_id,
            "volume": filled_amount,
            "entryPrice": filled_price,
            "symbol": ccxt_symbol,
            "direction": direction,
            "sl": sl,
            "tp": tp1,
            "slOrderId": sl_order_id,
            "tpOrderId": tp_order_id,
            "riskAmount": approval.risk_amount,
            "riskPct": approval.risk_pct,
        }

    except Exception as e:
        log.error(f"[BYBIT] Order failed: {e}")
        return {"success": False, "error": f"ORDER_FAILED: {str(e)}"}


def bybit_move_sl_to_breakeven(ccxt_symbol: str, direction: str, entry_price: float,
                                volume: float) -> dict:
    """Move stop-loss to breakeven (entry price) for an open position.

    Called by the outcome monitor when a position reaches 1R profit.
    Cancels existing SL orders and places a new one at entry price.
    """
    exchange = _get_exchange()
    if not exchange:
        return {"success": False, "error": "BYBIT_NOT_CONNECTED"}
    try:
        # Cancel all existing conditional orders for this symbol
        open_orders = exchange.fetch_open_orders(ccxt_symbol, params={"type": "linear"})
        for o in open_orders:
            o_type = (o.get("type") or "").lower()
            if "stop" in o_type:
                try:
                    exchange.cancel_order(o["id"], ccxt_symbol, params={"type": "linear"})
                    log.info(f"[BYBIT] Cancelled old SL order {o['id']}")
                except Exception:
                    pass
        # Place new SL at breakeven (entry price)
        sl_side = "sell" if direction == "LONG" else "buy"
        sl_order = exchange.create_order(
            ccxt_symbol, "stop_market", sl_side, volume,
            params={
                "stopPrice": entry_price,
                "reduceOnly": True,
                "positionIdx": 0,
            }
        )
        log.info(f"[BYBIT] BREAKEVEN SL placed: {ccxt_symbol} @ {entry_price} (was profitable at 1R)")
        return {"success": True, "slOrderId": sl_order.get("id"), "newSl": entry_price}
    except Exception as e:
        log.warning(f"[BYBIT] Failed to move SL to breakeven for {ccxt_symbol}: {e}")
        return {"success": False, "error": str(e)}


def bybit_disconnect():
    """Close exchange connection."""
    global _exchange
    _exchange = None
    log.info("[BYBIT] Disconnected")
