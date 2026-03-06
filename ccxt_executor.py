"""ccxt_executor.py — Binance crypto execution module for Athena.

Handles connection to Binance (testnet or live) and order placement.
This module is DELIBERATELY DUMB — it cannot size orders.
All orders must arrive as a pre-validated RiskApproval from risk_engine.py.
"""
import os
import logging

log = logging.getLogger("athena")

# ── Lazy ccxt import ─────────────────────────────────────────────────────────
_exchange = None

# ── Symbol mapping: Athena display name → Binance symbol ─────────────────────
_BINANCE_SYMBOL_MAP = {
    "BTC/USDT":    "BTC/USDT",
    "ETH/USDT":    "ETH/USDT",
    "XRP/USDT":    "XRP/USDT",
    "SOL/USDT":    "SOL/USDT",
    "ADA/USDT":    "ADA/USDT",
    "DOGE/USDT":   "DOGE/USDT",
    "AVAX/USDT":   "AVAX/USDT",
    "LINK/USDT":   "LINK/USDT",
    "MATIC/USDT":  "MATIC/USDT",
    "BNB/USDT":    "BNB/USDT",
    "DOT/USDT":    "DOT/USDT",
    "LTC/USDT":    "LTC/USDT",
    "SUI/USDT":    "SUI/USDT",
    "NEAR/USDT":   "NEAR/USDT",
    "APT/USDT":    "APT/USDT",
    "INJ/USDT":    "INJ/USDT",
    "FET/USDT":    "FET/USDT",
    "RENDER/USDT": "RENDER/USDT",
}

# Reverse map from Athena's internal symbol format (BTCUSDT) to ccxt format
_INTERNAL_TO_CCXT = {
    "BTCUSDT":    "BTC/USDT",
    "ETHUSDT":    "ETH/USDT",
    "XRPUSDT":    "XRP/USDT",
    "SOLUSDT":    "SOL/USDT",
    "ADAUSDT":    "ADA/USDT",
    "DOGEUSDT":   "DOGE/USDT",
    "AVAXUSDT":   "AVAX/USDT",
    "LINKUSDT":   "LINK/USDT",
    "MATICUSDT":  "MATIC/USDT",
    "BNBUSDT":    "BNB/USDT",
    "DOTUSDT":    "DOT/USDT",
    "LTCUSDT":    "LTC/USDT",
    "SUIUSDT":    "SUI/USDT",
    "NEARUSDT":   "NEAR/USDT",
    "APTUSDT":    "APT/USDT",
    "INJUSDT":    "INJ/USDT",
    "FETUSDT":    "FET/USDT",
    "RENDERUSDT": "RENDER/USDT",
}


def _get_exchange():
    """Initialize Binance exchange connection (testnet by default)."""
    global _exchange
    if _exchange is not None:
        return _exchange

    try:
        import ccxt
    except ImportError:
        log.error("[CCXT] ccxt package not installed. Run: pip install ccxt")
        return None

    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    if not api_key or not api_secret:
        log.warning("[CCXT] BINANCE_API_KEY / BINANCE_API_SECRET not set")
        return None

    use_testnet = os.environ.get("BINANCE_TESTNET", "true").lower() in ("true", "1", "yes")

    try:
        _exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "sandbox": use_testnet,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
            },
        })

        env_label = "TESTNET" if use_testnet else "LIVE"
        log.info(f"[CCXT] Binance {env_label} connected")
        return _exchange

    except Exception as e:
        log.error(f"[CCXT] Binance connection failed: {e}")
        _exchange = None
        return None


def ccxt_map_symbol(athena_display: str) -> str | None:
    """Map Athena display name or internal symbol to ccxt symbol format."""
    # Try display name first (BTC/USDT)
    sym = _BINANCE_SYMBOL_MAP.get(athena_display)
    if sym:
        return sym
    # Try internal format (BTCUSDT)
    sym = _INTERNAL_TO_CCXT.get(athena_display)
    if sym:
        return sym
    # Fallback: try adding /
    if "USDT" in athena_display and "/" not in athena_display:
        base = athena_display.replace("USDT", "")
        return f"{base}/USDT"
    return None


def ccxt_connect() -> bool:
    """Test Binance connection."""
    exchange = _get_exchange()
    if not exchange:
        return False
    try:
        exchange.fetch_time()
        return True
    except Exception as e:
        log.error(f"[CCXT] Connection test failed: {e}")
        return False


def ccxt_get_account() -> dict | None:
    """Get Binance account balance info."""
    exchange = _get_exchange()
    if not exchange:
        return None
    try:
        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        total_usd = usdt.get("total", 0) or 0
        free_usd = usdt.get("free", 0) or 0
        return {
            "exchange": "Binance",
            "testnet": exchange.sandbox,
            "balance": total_usd,
            "equity": total_usd,  # Spot has no margin equity
            "freeBalance": free_usd,
            "currency": "USDT",
        }
    except Exception as e:
        log.error(f"[CCXT] Failed to fetch balance: {e}")
        return None


def ccxt_get_positions() -> list:
    """Get open positions (for spot, this is non-zero balances).

    Estimates risk_amount from position value so crypto positions count
    toward portfolio heat and max-position checks in risk_engine.
    """
    exchange = _get_exchange()
    if not exchange:
        return []
    try:
        balance = exchange.fetch_balance()
        skip_keys = {"USDT", "BUSD", "USD", "info", "free", "used", "total", "timestamp", "datetime"}
        # Only count coins we actually trade (our 18 tracked crypto pairs)
        tracked_coins = {sym.replace("USDT", "") for sym in _INTERNAL_TO_CCXT}
        positions = []
        # Fetch prices for all tracked pairs in one call for risk estimation
        prices = {}
        try:
            tracked_symbols = [f"{c}/USDT" for c in tracked_coins]
            tickers = exchange.fetch_tickers(tracked_symbols)
            for sym, tick in tickers.items():
                coin = sym.split("/")[0]
                prices[coin] = tick.get("last", 0) or 0
        except Exception as te:
            log.debug(f"[CCXT] Could not fetch tickers for risk estimation: {te}")
        for coin, amounts in balance.items():
            if not isinstance(amounts, dict) or coin in skip_keys:
                continue
            total = amounts.get("total", 0) or 0
            if total <= 0 or coin not in tracked_coins:
                continue
            # Estimate risk: position_value * assumed 2% SL distance
            price = prices.get(coin, 0)
            pos_value = total * price
            est_risk = round(pos_value * 0.02, 2) if pos_value > 0 else 0
            positions.append({
                "pair": f"{coin}/USDT",
                "symbol": f"{coin}USDT",
                "volume": total,
                "free": amounts.get("free", 0),
                "risk_amount": est_risk,
            })
        return positions
    except Exception as e:
        log.error(f"[CCXT] Failed to fetch positions: {e}")
        return []


def ccxt_get_symbol_info(athena_display: str) -> dict | None:
    """Get Binance symbol info for risk engine calculations."""
    exchange = _get_exchange()
    if not exchange:
        return None

    ccxt_symbol = ccxt_map_symbol(athena_display)
    if not ccxt_symbol:
        log.warning(f"[CCXT] No symbol mapping for '{athena_display}'")
        return None

    try:
        exchange.load_markets()
        market = exchange.market(ccxt_symbol)
        if not market:
            return None

        ticker = exchange.fetch_ticker(ccxt_symbol)
        price = ticker.get("last", 0) or ticker.get("close", 0)

        tick_size = market["precision"].get("price", 0.01)
        # For USDT pairs: a tick_size move on 1 unit = tick_size in USD
        # e.g. BTC/USDT tick=0.01 → 1 BTC moves $0.01 per tick = $0.01 value
        tick_value = tick_size

        return {
            "symbol": ccxt_symbol,
            "description": market.get("info", {}).get("baseAsset", ccxt_symbol),
            "point": tick_size,
            "digits": len(str(tick_size).split(".")[-1]) if isinstance(tick_size, float) else 2,
            "volume_min": market["limits"]["amount"].get("min", 0.001),
            "volume_max": market["limits"]["amount"].get("max", 9999),
            "volume_step": market["precision"].get("amount", 0.001),
            "trade_contract_size": 1,  # Spot: 1 unit = 1 unit
            "trade_tick_value": tick_value,
            "trade_tick_size": tick_size,
            "bid": price,
            "ask": price,
            "spread": 0,
        }
    except Exception as e:
        log.error(f"[CCXT] Failed to get symbol info for {athena_display}: {e}")
        return None


def ccxt_execute(signal: dict, approval: "RiskApproval") -> dict:
    """Execute a crypto trade on Binance. ONLY accepts a pre-validated RiskApproval.

    For spot Binance:
    - LONG = market buy
    - SHORT = not supported on spot (would need futures)
    - SL/TP via OCO order or manual monitoring

    Args:
        signal: Full signal from analyze_pair()
        approval: RiskApproval from risk_engine.risk_check() — MUST be approved

    Returns:
        dict with success, orderId, volume, entry_price, error
    """
    from risk_engine import RiskApproval

    if not isinstance(approval, RiskApproval):
        return {"success": False, "error": "INVALID_APPROVAL: must be RiskApproval from risk_engine"}

    if not approval.approved:
        return {"success": False, "error": f"NOT_APPROVED: {approval.reason}"}

    exchange = _get_exchange()
    if not exchange:
        return {"success": False, "error": "BINANCE_NOT_CONNECTED"}

    pair = signal.get("pair", "")
    ccxt_symbol = ccxt_map_symbol(pair) or ccxt_map_symbol(signal.get("symbol", ""))
    if not ccxt_symbol:
        return {"success": False, "error": f"NO_SYMBOL_MAPPING: {pair}"}

    direction = signal.get("direction", "")

    # Spot only supports BUY (LONG). SHORT requires futures.
    if direction == "SHORT":
        return {"success": False, "error": "SHORT not supported on Binance Spot — futures coming later"}

    if direction != "LONG":
        return {"success": False, "error": f"INVALID_DIRECTION: {direction}"}

    try:
        # For crypto spot, volume is in base currency units (e.g. BTC, ETH)
        # approval.volume from risk_engine is in lots — for crypto, 1 lot = 1 unit
        volume = approval.volume

        # Get current price to calculate quantity if needed
        ticker = exchange.fetch_ticker(ccxt_symbol)
        price = ticker.get("ask", 0) or ticker.get("last", 0)

        if price <= 0:
            return {"success": False, "error": f"NO_PRICE_DATA: {ccxt_symbol}"}

        # If volume looks like forex lots (e.g. 0.05), convert to crypto units
        # Risk amount / SL distance = units to buy
        sl = signal.get("sl", 0)
        if volume < 1 and price > 100 and sl > 0:
            sl_dist = abs(signal.get("price", price) - sl)
            if sl_dist > 0:
                volume = approval.risk_amount / sl_dist
                volume = round(volume, 6)

        log.info(f"[CCXT] Placing market BUY: {volume} {ccxt_symbol} @ ~{price}")

        # Market buy order
        order = exchange.create_market_buy_order(ccxt_symbol, volume)

        order_id = order.get("id", "")
        filled_price = order.get("average", order.get("price", price))
        filled_amount = order.get("filled", volume)

        log.info(f"[CCXT] ORDER FILLED: id={order_id} | BUY {filled_amount} {ccxt_symbol} @ {filled_price}")

        # Place stop-loss as a separate stop order if supported
        sl = signal.get("sl", 0)
        sl_order_id = None
        if sl and sl > 0:
            try:
                sl_order = exchange.create_order(
                    ccxt_symbol, "stop_loss_limit", "sell",
                    filled_amount, sl, {"stopPrice": sl}
                )
                sl_order_id = sl_order.get("id")
                log.info(f"[CCXT] SL order placed: id={sl_order_id} @ {sl}")
            except Exception as sle:
                log.warning(f"[CCXT] SL order failed (manage manually): {sle}")

        return {
            "success": True,
            "ticket": order_id,
            "volume": filled_amount,
            "entryPrice": filled_price,
            "symbol": ccxt_symbol,
            "direction": "LONG",
            "sl": sl,
            "tp": signal.get("tp1", 0),
            "slOrderId": sl_order_id,
            "riskAmount": approval.risk_amount,
            "riskPct": approval.risk_pct,
        }

    except Exception as e:
        log.error(f"[CCXT] Order failed: {e}")
        return {"success": False, "error": f"ORDER_FAILED: {str(e)}"}


def ccxt_disconnect():
    """Close exchange connection."""
    global _exchange
    _exchange = None
    log.info("[CCXT] Disconnected")
