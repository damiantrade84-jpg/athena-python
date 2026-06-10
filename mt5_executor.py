"""mt5_executor.py — MetaTrader 5 execution module for Athena.



Handles connection, symbol mapping, and order placement.

This module is DELIBERATELY DUMB — it cannot size orders.

All orders must arrive as a pre-validated RiskApproval from risk_engine.py.

"""

import math
import os

import logging

import time

from config import CONFIG
import telegram_notify


log = logging.getLogger("athena")


def _is_engine_d_signal(signal: dict) -> bool:
    engine = str((signal or {}).get("engine", "")).strip().lower()
    return engine in ("scalp", "engine d", "scalp_vp")


def _timed_exit_cfg() -> dict:
    cfg = CONFIG.get("TIMED_EXIT") or {}
    return cfg if isinstance(cfg, dict) else {}


def _uses_trailing_atr_exit(signal: dict) -> bool:
    """True when Engine A/B exits are managed by the timed-exit chandelier trail."""
    mode = str(_timed_exit_cfg().get("tp_mode", "fixed")).strip().lower()
    return mode == "trailing_atr" and not _is_engine_d_signal(signal)


def _mt5_should_send_broker_tp(signal: dict) -> bool:
    """MT5 can still attach TP1 while timed_exit manages trailing SL ratchets."""
    if not _uses_trailing_atr_exit(signal):
        return True
    return bool(_timed_exit_cfg().get("mt5_attach_broker_tp_when_trailing_atr", True))


def _mt5_max_tick_age_sec() -> float | None:
    """Return the configured MT5 broker tick-age limit in seconds, or None when disabled."""
    cfg = CONFIG.get("MAX_BROKER_TICK_AGE_SEC")
    if not isinstance(cfg, dict):
        return None
    raw = cfg.get("mt5")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return None


def _mt5_max_spread_pct(signal: dict) -> float | None:
    """Return the configured per-asset-type spread ceiling, or None when disabled."""
    cfg = CONFIG.get("MAX_EXECUTION_SPREAD_PCT")
    if not isinstance(cfg, dict):
        return None
    asset_type = str((signal or {}).get("type") or "").strip().lower()
    if not asset_type:
        return None
    raw = cfg.get(asset_type)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return None


def _mt5_tick_age_seconds(tick) -> float | None:
    """Compute now − tick.time in seconds. Returns None when tick time is missing/zero."""
    try:
        tick_time = float(getattr(tick, "time", 0) or 0)
    except (TypeError, ValueError):
        return None
    if tick_time <= 0:
        return None
    return max(0.0, time.time() - tick_time)


def _mt5_spread_pct(tick) -> float | None:
    """Compute (ask-bid)/mid from an MT5 tick. Returns None when either side is missing."""
    try:
        ask = float(getattr(tick, "ask", 0) or 0)
        bid = float(getattr(tick, "bid", 0) or 0)
    except (TypeError, ValueError):
        return None
    if ask <= 0 or bid <= 0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid


def _mt5_entry_slippage_bps(
    direction: str, ref_price: float, fill_price: float
) -> tuple[float | None, float | None]:
    """Return (reference_price, slippage_bps). Positive bps = adverse for the trader."""
    try:
        ref = float(ref_price or 0)
        fill = float(fill_price or 0)
    except (TypeError, ValueError):
        return None, None
    if ref <= 0 or fill <= 0:
        return (round(ref, 8) if ref > 0 else None, None)
    ref = round(ref, 8)
    if direction == "LONG":
        bps = round((fill - ref) / ref * 10000.0, 2)
    else:
        bps = round((ref - fill) / ref * 10000.0, 2)
    return ref, bps


def _mt5_position_fee_cost(
    mt5,
    position_ticket: int | None,
    *,
    retries: int = 3,
    delay_s: float = 0.15,
) -> float | None:
    """Return summed commission+fee cost for one MT5 position from deal history.

    Verified against the official MQL5 Python docs: history_deals_get(position=...)
    returns deal records with ``commission`` and ``fee`` fields.
    """
    if not position_ticket:
        return None

    for attempt in range(max(1, int(retries))):
        try:
            deals = mt5.history_deals_get(position=int(position_ticket))
        except Exception as exc:
            log.debug(f"[MT5] fee lookup failed for position {position_ticket}: {exc}")
            deals = None

        if deals:
            total_cost = 0.0
            for deal in deals:
                for field_name in ("commission", "fee"):
                    try:
                        total_cost += abs(float(getattr(deal, field_name, 0.0) or 0.0))
                    except (TypeError, ValueError):
                        continue
            return round(total_cost, 8)

        if attempt < max(1, int(retries)) - 1:
            time.sleep(min(5.0, max(0.0, float(delay_s))))

    return None


def _mt5_total_fee_cost(mt5, position_tickets: list[int | None]) -> float | None:
    """Aggregate broker-reported entry costs across one or more opened legs."""
    total = 0.0
    found_any = False
    seen: set[int] = set()
    for raw_ticket in position_tickets or []:
        try:
            ticket = int(raw_ticket)
        except (TypeError, ValueError):
            continue
        if ticket <= 0 or ticket in seen:
            continue
        seen.add(ticket)
        fee_cost = _mt5_position_fee_cost(mt5, ticket)
        if fee_cost is None:
            continue
        total += float(fee_cost)
        found_any = True
    return round(total, 8) if found_any else None


def _mt5_trade_state(mt5, mt5_symbol: str, sym_info=None) -> dict:
    """Return MT5 trade-permission flags needed before a live order send."""
    state = {"symbol": mt5_symbol}
    try:
        terminal = mt5.terminal_info()
        if terminal is not None:
            state["terminal_trade_allowed"] = bool(
                getattr(terminal, "trade_allowed", False)
            )
            state["terminal_tradeapi_disabled"] = bool(
                getattr(terminal, "tradeapi_disabled", False)
            )
    except Exception as exc:
        state["terminal_error"] = str(exc)

    try:
        account = mt5.account_info()
        if account is not None:
            state["account_trade_allowed"] = bool(
                getattr(account, "trade_allowed", False)
            )
            state["account_trade_expert"] = bool(getattr(account, "trade_expert", False))
            state["account_trade_mode"] = getattr(account, "trade_mode", None)
    except Exception as exc:
        state["account_error"] = str(exc)

    try:
        if sym_info is None:
            sym_info = mt5.symbol_info(mt5_symbol)
        if sym_info is not None:
            state["symbol_trade_mode"] = getattr(sym_info, "trade_mode", None)
            state["symbol_trade_mode_disabled"] = (
                state["symbol_trade_mode"]
                == getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0)
            )
            state["symbol_trade_mode_full"] = getattr(
                mt5, "SYMBOL_TRADE_MODE_FULL", 4
            )
            state["symbol_visible"] = bool(getattr(sym_info, "visible", False))
    except Exception as exc:
        state["symbol_error"] = str(exc)

    return state


def _mt5_trade_state_block_reason(state: dict, direction: str, mt5) -> str | None:
    if state.get("terminal_tradeapi_disabled") is True:
        return "terminal_tradeapi_disabled"
    if state.get("terminal_trade_allowed") is False:
        return "terminal_trade_not_allowed"
    if state.get("account_trade_allowed") is False:
        return "account_trade_not_allowed"
    if state.get("account_trade_expert") is False:
        return "account_expert_trading_not_allowed"
    if state.get("symbol_trade_mode_disabled") is True:
        return "symbol_trade_disabled"

    mode = state.get("symbol_trade_mode")
    _closeonly = getattr(mt5, "SYMBOL_TRADE_MODE_CLOSEONLY", 3)
    if mode == _closeonly:
        return "symbol_close_only_no_new_positions"

    if direction == "LONG" and mode == getattr(mt5, "SYMBOL_TRADE_MODE_SHORTONLY", 2):
        return "symbol_short_only"
    if direction == "SHORT" and mode == getattr(mt5, "SYMBOL_TRADE_MODE_LONGONLY", 1):
        return "symbol_long_only"
    return None


def _mt5_scalar(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return value.item()
    except Exception:
        return str(value)


def _mt5_order_request_summary(request: dict) -> dict:
    allowed = (
        "action",
        "symbol",
        "volume",
        "type",
        "price",
        "sl",
        "tp",
        "deviation",
        "magic",
        "comment",
        "type_time",
        "type_filling",
    )
    return {key: _mt5_scalar(request[key]) for key in allowed if key in request}


def _mt5_order_check_summary(order_check) -> dict | None:
    if order_check is None:
        return None
    fields = ("retcode", "comment", "margin", "margin_free", "margin_level", "profit")
    out = {}
    for field in fields:
        if hasattr(order_check, field):
            out[field] = _mt5_scalar(getattr(order_check, field))
    return out


# ── Lazy MT5 import (only needed when execution is used) ─────────────────────

def _mt5_is_success_retcode(retcode, mt5) -> bool:
    return retcode in (0, getattr(mt5, "TRADE_RETCODE_DONE", 10009))


def _mt5_is_unsupported_filling_result(result) -> bool:
    retcode = getattr(result, "retcode", None)
    comment = str(getattr(result, "comment", "") or "")
    return retcode == 10030 or "filling" in comment.lower()


def _mt5_filling_candidates(mt5, mt5_symbol: str, sym_info=None, preferred=None) -> list[int]:
    candidates: list[int] = []

    def _add(raw_value) -> None:
        if raw_value is None:
            return
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return
        if value not in candidates:
            candidates.append(value)

    _add(preferred)
    _add(_SYMBOL_FILLING_MODES.get(mt5_symbol))

    raw_flags = getattr(sym_info, "filling_mode", None)
    try:
        flags = int(raw_flags) if raw_flags is not None else None
    except (TypeError, ValueError):
        flags = None

    if flags is not None:
        if flags & 1:
            _add(getattr(mt5, "ORDER_FILLING_FOK", None))
        if flags & 2:
            _add(getattr(mt5, "ORDER_FILLING_IOC", None))
    else:
        _add(getattr(mt5, "ORDER_FILLING_IOC", None))

    _add(getattr(mt5, "ORDER_FILLING_RETURN", None))

    if not candidates:
        _add(getattr(mt5, "ORDER_FILLING_IOC", None))

    return candidates


def _mt5_cache_filling_mode(mt5_symbol: str, filling_mode: int) -> None:
    _SYMBOL_FILLING_MODES[mt5_symbol] = int(filling_mode)
    log.info(f"[MT5] Saved filling mode {int(filling_mode)} for {mt5_symbol}")


def _mt5_order_check_with_filling_fallback(mt5, request: dict, sym_info=None):
    if not hasattr(mt5, "order_check"):
        return None

    mt5_symbol = request.get("symbol")
    candidates = _mt5_filling_candidates(
        mt5,
        mt5_symbol,
        sym_info=sym_info,
        preferred=request.get("type_filling"),
    )
    last_check = None
    for filling_mode in candidates:
        request["type_filling"] = filling_mode
        try:
            order_check = mt5.order_check(dict(request))
        except Exception as check_exc:
            log.warning(f"[MT5] order_check failed before send on {mt5_symbol}: {check_exc}")
            return None

        last_check = order_check
        check_retcode = getattr(order_check, "retcode", None)
        if _mt5_is_success_retcode(check_retcode, mt5):
            _mt5_cache_filling_mode(mt5_symbol, filling_mode)
            return order_check
        if _mt5_is_unsupported_filling_result(order_check):
            log.warning(
                f"[MT5] order_check unsupported filling for {mt5_symbol} with mode={filling_mode}; trying next candidate"
            )
            continue
        return order_check

    return last_check


_mt5 = None


def _get_mt5():
    """Lazy-import MetaTrader5 so the rest of Athena works without it installed."""

    global _mt5, _SYMBOL_FILLING_MODES

    if _mt5 is None:
        try:
            import MetaTrader5

            _mt5 = MetaTrader5

            # Initialize filling modes now that MT5 is available

            _SYMBOL_FILLING_MODES = {
                "USO.US": MetaTrader5.ORDER_FILLING_FOK,
                "XLE.US": MetaTrader5.ORDER_FILLING_FOK,
                "SLV.US": MetaTrader5.ORDER_FILLING_FOK,
            }

        except ImportError:
            log.error(
                "[MT5] MetaTrader5 package not installed. Run: pip install MetaTrader5"
            )

            return None

    return _mt5


# ── Symbol mapping: Athena display name → MT5 symbol ─────────────────────────

_MT5_SYMBOL_MAP = {
    # Forex
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD",
    "AUD/CHF": "AUDCHF",
    "AUD/NZD": "AUDNZD",
    "NZD/USD": "NZDUSD",
    "USD/CAD": "USDCAD",
    "USD/CHF": "USDCHF",
    "EUR/JPY": "EURJPY",
    "GBP/JPY": "GBPJPY",
    "EUR/AUD": "EURAUD",
    "EUR/GBP": "EURGBP",
    "AUD/JPY": "AUDJPY",
    "GBP/AUD": "GBPAUD",
    "USD/ZAR": "USDZAR",
    "USD/MXN": "USDMXN",
    "EUR/CHF": "EURCHF",
    "USD/SGD": "USDSGD",
    "USD/BRL": "USDBRL",
    "USD/INR": "USDINR",
    # Commodities
    "XAU/USD": "XAUUSD",
    "XAG/USD": "XAGUSD",
    "WTI Oil": "SpotCrude",  # Pepperstone WTI CFD — user verified 'SpotCrude'
    "Brent Oil": "SpotBrent",  # Pepperstone Brent Crude CFD
    "Nat Gas": "NatGas",  # Pepperstone Natural Gas CFD
    "XPT/USD": "XPTUSD",  # Pepperstone Platinum CFD
    "XPD/USD": "XPDUSD",  # Pepperstone Palladium CFD
    "Copper": "Copper",  # Pepperstone Copper CFD
    "Aluminium": "Aluminium",  # Pepperstone Aluminium CFD
    "Lead": "Lead",  # Pepperstone Lead CFD
    "Nickel": "Nickel",  # Pepperstone Nickel CFD
    "Zinc": "Zinc",  # Pepperstone Zinc CFD
    "Gasoline": "Gasoline",  # Pepperstone Gasoline CFD
    "Cattle": "Cattle",  # Pepperstone Live Cattle CFD
    "Cocoa": "Cocoa",  # Pepperstone Cocoa CFD
    "Coffee": "Coffee",  # Pepperstone Coffee CFD
    "Corn": "Corn",  # Pepperstone Corn CFD
    "Cotton": "Cotton",  # Pepperstone Cotton CFD
    "Soybeans": "Soybeans",  # Pepperstone Soybeans CFD
    "Sugar": "Sugar",  # Pepperstone Sugar CFD
    "Wheat": "Wheat",  # Pepperstone Wheat CFD
    # Indices
    "S&P 500": "US500",
    "US2000": "US2000",
    "Nasdaq": "NAS100",
    "NASDAQ-100": "NAS100",
    "Dow Jones": "US30",
    "DAX 40": "GER40",
    "UK100": "UK100",
    "ASX 200": "AUS200",
    "Nikkei 225": "JPN225",
    "Hang Seng": "HK50",
    "Euro Stoxx 50": "EUSTX50",
    "EURX": "EURX",
    "JPYX": "JPYX",
    "USDX": "USDX",
    # US Stocks (CFDs on Pepperstone)
    "AAPL": "AAPL.US",
    "TSLA": "TSLA.US",
    "NVDA": "NVDA.US",
    "MSFT": "MSFT.US",
    "AMZN": "AMZN.US",
    "META": "META.US",
    "GOOGL": "GOOGL.US",
    "AVGO": "AVGO.US",
    "JPM": "JPM.US",
    "V": "V.US",
    "XOM": "XOM.US",
    "NFLX": "NFLX.US",
    "AMD": "AMD.US",
    "CRM": "CRM.US",
    "DIS": "DIS.US",
    "BA": "BA.US",
    "COIN": "COIN.US",
    "PYPL": "PYPL.US",
    "INTC": "INTC.US",
    "UBER": "UBER.US",
    "PLTR": "PLTR.US",
    # ETFs
    "SPY": "SPY.US",
    "QQQ": "QQQ.US",
    "TQQQ": "TQQQ.US",
    "SQQQ": "SQQQ.US",
    "GLD": "GLD.US",
    "TLT": "TLT.US",
    "IWM": "IWM.US",
    "EEM": "EEM.US",
    "DIA": "DIA.US",
    "GDX": "GDX.US",
    "SOXX": "SOXX.US",
    "XLF": "XLF.US",
    "XLE": "XLE.US",  # Common MT5 suffix for US energy ETF
    "SLV": "SLV.US",  # Common MT5 suffix for silver ETF
    "USO": "USO.US",  # Many MT5 brokers expose USO as USO.US
}


# Symbol-specific filling modes (some ETFs reject IOC)

# Will be initialized after MT5 import

_SYMBOL_FILLING_MODES = {}


# Connection state

_connected = False

_last_connect_attempt = 0

_RECONNECT_COOLDOWN = 30  # seconds between reconnect attempts


def mt5_map_symbol(athena_display: str) -> str | None:
    """Map Athena display name to MT5 symbol. Returns None if no mapping exists."""

    mt5_sym = _MT5_SYMBOL_MAP.get(athena_display)

    if mt5_sym:
        return mt5_sym

    # Fallback: try stripping / and whitespace

    stripped = athena_display.replace("/", "").replace(" ", "")

    if stripped:
        # Many MT5 brokers append country suffix for US equities/ETFs (e.g., USO.US)

        if stripped.isupper() and 1 < len(stripped) <= 5:
            return f"{stripped}.US"

        return stripped

    return None


def mt5_connect() -> bool:
    """Initialize MT5 terminal connection. Uses env vars for terminal path and credentials."""

    global _connected, _last_connect_attempt

    mt5 = _get_mt5()

    if mt5 is None:
        return False

    mt5 = _get_mt5()

    if not mt5:
        return False

    now = time.time()

    if _connected and mt5.terminal_info() is not None:
        return True

    if now - _last_connect_attempt < _RECONNECT_COOLDOWN:
        return False

    _last_connect_attempt = now

    terminal_path = os.environ.get("MT5_PATH", "").strip()

    if terminal_path and not os.path.exists(terminal_path):
        log.error(f"[MT5] MT5_PATH does not exist: {terminal_path}")

        _connected = False

        return False

    init_kwargs = {"path": terminal_path} if terminal_path else {}

    if not mt5.initialize(**init_kwargs):
        target = terminal_path or "default active terminal"
        log.error(f"[MT5] initialize() failed for {target}: {mt5.last_error()}")

        _connected = False

        return False

    if terminal_path:
        log.info(f"[MT5] Connected via pinned terminal: {terminal_path}")

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
        log.info(
            "[MT5] Connected to terminal (no explicit login — using terminal's active account)"
        )

    _connected = True

    info = mt5.account_info()

    if info:
        log.info(
            f"[MT5] Account: {info.login} | Balance: {info.balance} | Equity: {info.equity} | Server: {info.server}"
        )

    return True


def mt5_disconnect():
    """Shutdown MT5 connection."""

    global _connected

    mt5 = _get_mt5()

    if mt5:
        mt5.shutdown()

    _connected = False

    log.info("[MT5] Disconnected")


def mt5_get_account() -> dict:
    """Get current account info (balance, equity, etc.).

    Returns dict with standardized error format."""

    mt5 = _get_mt5()

    if not mt5 or not mt5_connect():
        return {"error": True, "symbol": "MT5", "detail": "MT5 not connected"}

    info = mt5.account_info()

    if not info:
        return {"error": True, "symbol": "MT5", "detail": "Failed to get account info"}

    return {
        "error": False,
        "symbol": "MT5",
        "detail": "",
        "login": info.login,
        "server": info.server,
        "risk_domain": f"forex:mt5:{info.server}:{info.login}",
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "freeMargin": info.margin_free,
        "profit": info.profit,
        "currency": info.currency,
    }


def _mt5_position_risk_amount(
    price_open: float,
    sl: float,
    volume: float,
    tick_size: float,
    tick_value: float,
    fallback_sl_pct: float,
) -> float:
    """Risk in account currency for an open MT5 position.

    When the broker SL is unset (``sl <= 0``) the true risk is unbounded, so for
    portfolio-heat accounting we assume the position risks up to
    ``fallback_sl_pct`` of its entry price. This prevents SL-less positions from
    contributing 0 heat and stacking without limit (audit C4).
    """
    if not tick_size or not tick_value or volume <= 0 or price_open <= 0:
        return 0.0
    if sl > 0:
        sl_dist = abs(price_open - sl)
    else:
        sl_dist = abs(price_open) * max(0.0, fallback_sl_pct)
    return (sl_dist / tick_size) * tick_value * volume


def mt5_get_positions(*, attempt_connect: bool = True) -> dict:
    """Get all open positions. Returns dict with standardized error format.

    When ``attempt_connect`` is False (dashboard polling), skip MT5 initialize/
    reconnect so a slow/offline terminal cannot block Bybit position reads.
    """

    mt5 = _get_mt5()

    if not mt5:
        return {
            "error": False,
            "symbol": "MT5",
            "detail": "MT5 unavailable",
            "positions": [],
        }

    if not _connected:
        if not attempt_connect:
            return {
                "error": False,
                "symbol": "MT5",
                "detail": "not_connected",
                "positions": [],
            }
        if not mt5_connect():
            return {
                "error": True,
                "symbol": "MT5",
                "detail": "MT5 not connected",
                "positions": [],
            }

    positions = mt5.positions_get()

    if not positions:
        return {"error": False, "symbol": "MT5", "detail": "", "positions": []}

    result = []

    # Reverse map: MT5 symbol → Athena display name

    reverse_map = {v: k for k, v in _MT5_SYMBOL_MAP.items()}

    # C4: an SL-less position would otherwise contribute 0 to portfolio heat,
    # allowing unlimited stacking. Assume the worst-case configured cap distance.
    _heat_fallback_sl_pct = float(CONFIG.get("MAX_SL_PCT", {}).get("forex", 0.05))

    for pos in positions:
        athena_pair = reverse_map.get(pos.symbol, pos.symbol)

        risk_amount = 0.0

        info = mt5.symbol_info(pos.symbol)
        if info:
            tick_size = info.trade_tick_size or info.point or 0

            tick_value = info.trade_tick_value

            if tick_value == 0 and info.trade_contract_size and info.point:
                tick_value = info.trade_contract_size * info.point

            risk_amount = _mt5_position_risk_amount(
                pos.price_open,
                pos.sl,
                pos.volume,
                tick_size,
                tick_value,
                _heat_fallback_sl_pct,
            )

        result.append(
            {
                "ticket": pos.ticket,
                "pair": athena_pair,
                "symbol": pos.symbol,
                "direction": "LONG" if pos.type == 0 else "SHORT",
                "volume": pos.volume,
                "entry": pos.price_open,
                "currentPrice": getattr(pos, "price_current", 0.0),
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "risk_amount": round(risk_amount, 2),
                "open_time": int(pos.time) if pos.time else 0,
            }
        )

    return {"error": False, "symbol": "MT5", "detail": "", "positions": result}


def mt5_close_position(ticket: int, volume: float | None = None) -> dict:
    """Close an open MT5 position by ticket number."""

    mt5 = _get_mt5()

    if not mt5 or not mt5_connect():
        return {"success": False, "error": "MT5 not connected"}

    positions = mt5.positions_get(ticket=ticket)

    if not positions:
        return {"success": False, "error": f"Position {ticket} not found"}

    pos = positions[0]

    close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY

    close_volume = float(pos.volume)
    if volume is not None:
        try:
            requested_volume = max(0.0, min(float(volume), float(pos.volume)))
        except (TypeError, ValueError):
            requested_volume = 0.0
        sym_info = mt5.symbol_info(pos.symbol)
        vol_step = float(getattr(sym_info, "volume_step", 0.01) or 0.01)
        vol_min = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        close_volume = math.floor(requested_volume / vol_step) * vol_step
        close_volume = round(close_volume, 2)
        if close_volume < vol_min:
            return {
                "success": False,
                "error": f"INVALID_CLOSE_VOLUME: {requested_volume} below minimum {vol_min}",
            }

    tick = mt5.symbol_info_tick(pos.symbol)

    if not tick:
        return {"success": False, "error": f"No tick data for {pos.symbol}"}

    price = tick.bid if pos.type == 0 else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": close_volume,
        "type": close_type,
        "price": price,
        "position": ticket,
        "magic": 240601,
        "comment": "Athena|CLOSE",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _SYMBOL_FILLING_MODES.get(pos.symbol, mt5.ORDER_FILLING_IOC),
    }

    result = _send_order_with_filling_fallback(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        raw_fill_price = float(result.price) if result.price else price
        deal_ticket = int(result.deal) if result.deal else None
        live_profit = float(pos.profit) if hasattr(pos, "profit") else 0.0
        entry_price = float(pos.price_open)
        direction = "LONG" if pos.type == 0 else "SHORT"
        volume = float(pos.volume)
        
        # Workaround: Pepperstone sometimes records entry_price as deal.price
        # Detect when deal price equals entry price (within tick tolerance) and compute correct price
        fill_price = raw_fill_price
        if abs(raw_fill_price - entry_price) < 1e-8 and live_profit != 0:
            # Deal price is wrong (equals entry), compute correct price from profit
            # For forex: profit = (close - entry) * volume * contract_size / tick_size * tick_value
            # Simplified: close = entry + profit / (volume * pip_value)
            try:
                sym_info = mt5.symbol_info(pos.symbol)
                if sym_info:
                    tick_value = float(sym_info.trade_tick_value or 0)
                    tick_size = float(sym_info.trade_tick_size or 0)
                    contract_size = float(sym_info.trade_contract_size or 1)
                    
                    if tick_value > 0 and tick_size > 0:
                        # Compute price change per pip/tick
                        pip_value = tick_value / tick_size * contract_size
                        if pip_value > 0:
                            price_change = live_profit / (volume * pip_value)
                            if direction == "LONG":
                                computed_close = entry_price + price_change
                            else:
                                computed_close = entry_price - price_change
                            
                            fill_price = computed_close
                            log.warning(
                                f"[MT5] Pepperstone price bug detected: ticket={ticket} "
                                f"deal_price={raw_fill_price} (equals entry) -> computed_close={fill_price:.5f} "
                                f"profit={live_profit} entry={entry_price}"
                            )
                        else:
                            log.warning(f"[MT5] Cannot compute close price: zero pip_value for {pos.symbol}")
                    else:
                        log.warning(f"[MT5] Cannot compute close price: missing tick_value/tick_size for {pos.symbol}")
                else:
                    log.warning(f"[MT5] Cannot compute close price: no symbol_info for {pos.symbol}")
            except Exception as e:
                log.error(f"[MT5] Error computing correct close price: {e}")
                # Fall back to raw fill price
                fill_price = raw_fill_price
        
        if abs(fill_price - price) > 1e-8:
            log.warning(
                f"[MT5] Close fill price mismatch: ticket={ticket} "
                f"requested={price} fill={fill_price} (delta={fill_price - price})"
            )
        log.info(
            f"[MT5] Close ticket={ticket} fill={fill_price} requested={price} "
            f"deal={deal_ticket} liveProfit={live_profit}"
        )

        return {
            "success": True,
            "ticket": ticket,
            "closePrice": fill_price,
            "requestedPrice": price,
            "dealTicket": deal_ticket,
            "liveProfit": live_profit,
            "entryPrice": float(pos.price_open),
            "symbol": pos.symbol,
            "volume": close_volume,
            "direction": "LONG" if pos.type == 0 else "SHORT",
        }

    err = result.comment if result else "order_send failed"

    log.error(f"[MT5] Close failed ticket={ticket}: {err}")

    return {"success": False, "error": err}


def mt5_move_sl_to_breakeven(ticket: int, entry_price: float) -> dict:
    """Move stop-loss to breakeven (entry price) for an open MT5 position.

    Uses TRADE_ACTION_SLTP to modify SL only. Does not close the position.
    Called by timed_exit_monitor when a position reaches the breakeven trigger window.
    """
    mt5 = _get_mt5()

    if not mt5 or not mt5_connect():
        return {"success": False, "error": "MT5 not connected"}

    positions = mt5.positions_get(ticket=ticket)

    if not positions:
        return {"success": False, "error": f"Position {ticket} not found"}

    pos = positions[0]

    # Get symbol info for stops_level validation
    sym_info = mt5.symbol_info(pos.symbol)
    if not sym_info:
        return {"success": False, "error": f"Cannot get symbol info for {pos.symbol}"}

    point = sym_info.point or 0.00001
    stops_level = sym_info.trade_stops_level if hasattr(sym_info, 'trade_stops_level') else 0
    current_price = pos.price_current if hasattr(pos, 'price_current') else 0

    # Only move SL if entry_price is better than current SL
    # (do not move SL to a worse level)
    direction_long = pos.type == 0
    current_sl = pos.sl

    # Add tolerance for floating point comparison (within 10 points)
    tolerance = point * 10
    if direction_long and current_sl >= (entry_price - tolerance):
        return {"success": True, "skipped": True, "reason": "SL already at or above breakeven"}

    if not direction_long and current_sl > 0 and current_sl <= (entry_price + tolerance):
        return {"success": True, "skipped": True, "reason": "SL already at or below breakeven"}

    # Validate stops_level: SL must be at least stops_level away from current price
    if stops_level > 0 and current_price > 0:
        min_sl_distance = stops_level * point
        if direction_long:
            min_allowed_sl = current_price - min_sl_distance
            if entry_price > min_allowed_sl:
                log.warning(
                    f"[MT5] BE SL too close to price: ticket={ticket} "
                    f"requested_sl={entry_price:.5f} min_allowed={min_allowed_sl:.5f} "
                    f"(stops_level={stops_level})"
                )
                return {"success": False, "error": f"SL too close to price (min: {min_allowed_sl:.5f})"}
        else:
            min_allowed_sl = current_price + min_sl_distance
            if entry_price < min_allowed_sl:
                log.warning(
                    f"[MT5] BE SL too close to price: ticket={ticket} "
                    f"requested_sl={entry_price:.5f} min_allowed={min_allowed_sl:.5f} "
                    f"(stops_level={stops_level})"
                )
                return {"success": False, "error": f"SL too close to price (min: {min_allowed_sl:.5f})"}

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos.symbol,
        "sl": entry_price,
        "tp": pos.tp,
        "position": ticket,
        "magic": 240601,
        "comment": "Athena|BE",
    }

    result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(f"[MT5] BREAKEVEN SL set: ticket={ticket} sl={entry_price} (was {current_sl})")
        return {"success": True, "newSl": entry_price}

    err = result.comment if result else "order_send failed"
    log.warning(
        f"[MT5] Breakeven SL failed ticket={ticket}: {err} "
        f"(requested={entry_price:.5f} current_sl={current_sl:.5f} current_price={current_price:.5f})"
    )
    return {"success": False, "error": err}


def mt5_modify_protective_stops(
    ticket: int,
    sl: float | None = None,
    tp: float | None = None,
    clear_tp: bool = False,
) -> dict:
    """Modify SL and/or TP on an open MT5 position via TRADE_ACTION_SLTP.

    Args:
        ticket: position ticket.
        sl: new stop-loss price. None leaves the broker SL unchanged
            (validated like mt5_move_sl_to_breakeven — only moves in the
            trade's favour). Pass 0.0 to clear the SL (rare).
        tp: new take-profit price. None leaves the broker TP unchanged.
        clear_tp: when True, forces TP to 0.0 (removes the fixed broker TP).
            Mutually exclusive with passing a positive tp value; clear_tp
            wins if both are set.

    Returns dict with success/error keys. Skipped (no-op) operations come
    back as success=True, skipped=True so callers can treat them as benign.
    """
    mt5 = _get_mt5()

    if not mt5 or not mt5_connect():
        return {"success": False, "error": "MT5 not connected"}

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return {"success": False, "error": f"Position {ticket} not found"}

    pos = positions[0]
    sym_info = mt5.symbol_info(pos.symbol)
    if not sym_info:
        return {"success": False, "error": f"Cannot get symbol info for {pos.symbol}"}

    point = sym_info.point or 0.00001
    stops_level = sym_info.trade_stops_level if hasattr(sym_info, 'trade_stops_level') else 0
    current_price = pos.price_current if hasattr(pos, 'price_current') else 0
    direction_long = pos.type == 0
    current_sl = pos.sl
    current_tp = pos.tp

    target_sl = float(current_sl)
    if sl is not None:
        # Apply the same "only-improves" guard as mt5_move_sl_to_breakeven so
        # we never widen the broker stop. A zero current_sl means no stop set
        # yet — accept any positive sl in that case.
        tolerance = point * 10
        if float(sl) <= 0:
            target_sl = 0.0
        elif current_sl <= 0:
            target_sl = float(sl)
        elif direction_long and float(sl) > current_sl + tolerance:
            target_sl = float(sl)
        elif (not direction_long) and float(sl) < current_sl - tolerance:
            target_sl = float(sl)
        else:
            # Requested SL is not protective — keep current.
            target_sl = float(current_sl)

        # Validate distance from price when actually changing the stop.
        if target_sl != current_sl and target_sl > 0 and stops_level > 0 and current_price > 0:
            min_sl_distance = stops_level * point
            if direction_long:
                min_allowed_sl = current_price - min_sl_distance
                if target_sl > min_allowed_sl:
                    log.warning(
                        f"[MT5] modify_stops SL too close: ticket={ticket} "
                        f"req={target_sl:.5f} min_allowed={min_allowed_sl:.5f}"
                    )
                    return {"success": False, "error": f"SL too close to price (min: {min_allowed_sl:.5f})"}
            else:
                min_allowed_sl = current_price + min_sl_distance
                if target_sl < min_allowed_sl:
                    log.warning(
                        f"[MT5] modify_stops SL too close: ticket={ticket} "
                        f"req={target_sl:.5f} min_allowed={min_allowed_sl:.5f}"
                    )
                    return {"success": False, "error": f"SL too close to price (min: {min_allowed_sl:.5f})"}

    if clear_tp:
        target_tp = 0.0
    elif tp is not None:
        target_tp = float(tp)
    else:
        target_tp = float(current_tp)

    sl_changed = abs(target_sl - float(current_sl)) > (point * 0.5)
    tp_changed = abs(target_tp - float(current_tp)) > (point * 0.5)
    if not sl_changed and not tp_changed:
        return {"success": True, "skipped": True, "reason": "no change"}

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos.symbol,
        "sl": target_sl,
        "tp": target_tp,
        "position": ticket,
        "magic": 240601,
        "comment": "Athena|TrailMod",
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            f"[MT5] modify_stops: ticket={ticket} sl {current_sl}->{target_sl} "
            f"tp {current_tp}->{target_tp}"
        )
        return {"success": True, "newSl": target_sl, "newTp": target_tp,
                "slChanged": sl_changed, "tpChanged": tp_changed}

    err = result.comment if result else "order_send failed"
    log.warning(
        f"[MT5] modify_stops failed ticket={ticket}: {err} "
        f"(sl_req={target_sl:.5f} tp_req={target_tp:.5f})"
    )
    return {"success": False, "error": err}


def mt5_get_symbol_info(athena_display: str) -> dict:
    """Get MT5 symbol info for risk engine calculations.

    Returns dict with standardized error format."""

    mt5 = _get_mt5()

    if not mt5 or not mt5_connect():
        return {"error": True, "symbol": athena_display, "detail": "MT5 not connected"}

    mt5_symbol = mt5_map_symbol(athena_display)

    if not mt5_symbol:
        log.warning(f"[MT5] No symbol mapping for '{athena_display}'")

        return {"error": True, "symbol": athena_display, "detail": "No symbol mapping"}

    info = mt5.symbol_info(mt5_symbol)

    if info is None:
        # Try enabling the symbol in Market Watch

        if not mt5.symbol_select(mt5_symbol, True):
            log.warning(f"[MT5] Symbol '{mt5_symbol}' not found in MT5")

            return {
                "error": True,
                "symbol": athena_display,
                "detail": "Symbol not found in MT5",
            }

        info = mt5.symbol_info(mt5_symbol)

        if info is None:
            return {
                "error": True,
                "symbol": athena_display,
                "detail": "Symbol not found after selection",
            }

    return {
        "error": False,
        "symbol": athena_display,
        "detail": "",
        "mt5_symbol": info.name,
        "description": info.description,
        "point": info.point,
        "digits": info.digits,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "trade_contract_size": info.trade_contract_size,
        "trade_tick_value": info.trade_tick_value,
        "trade_tick_size": info.trade_tick_size,
        "trade_mode": getattr(info, "trade_mode", None),
        "trade_mode_disabled": getattr(info, "trade_mode", None)
        == getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0),
        "order_mode": getattr(info, "order_mode", None),
        "filling_mode": getattr(info, "filling_mode", None),
        "bid": info.bid,
        "ask": info.ask,
        "spread": info.spread,
    }


def _send_order_with_filling_fallback(request: dict):
    """Attempt order send and dynamically fallback if filling mode unsupported."""
    mt5 = _get_mt5()
    mt5_symbol = request.get("symbol")
    if not mt5 or not mt5_symbol:
        return None

    sym_info = mt5.symbol_info(mt5_symbol)
    last_result = None
    for filling_mode in _mt5_filling_candidates(
        mt5,
        mt5_symbol,
        sym_info=sym_info,
        preferred=request.get("type_filling"),
    ):
        request["type_filling"] = filling_mode
        result = mt5.order_send(request)
        last_result = result
        if result and _mt5_is_success_retcode(result.retcode, mt5):
            _mt5_cache_filling_mode(mt5_symbol, filling_mode)
            return result
        if result and _mt5_is_unsupported_filling_result(result):
            log.warning(
                f"[MT5] order_send unsupported filling for {mt5_symbol} with mode={filling_mode}; trying next candidate"
            )
            continue
        return result
    return last_result


def _mt5_resolve_position_ticket(
    mt5,
    mt5_symbol: str,
    *,
    magic: int,
    volume: float,
    order_ticket: int,
) -> int:
    """Map a fresh market deal to the holding position ticket (best-effort)."""
    try:
        time.sleep(0.15)
        plist = mt5.positions_get(symbol=mt5_symbol) or []
        vol_f = float(volume)
        for pos in plist:
            if getattr(pos, "magic", 0) != magic:
                continue
            if abs(float(pos.volume) - vol_f) < 1e-8:
                return int(pos.ticket)
        for pos in plist:
            if getattr(pos, "magic", 0) == magic:
                return int(pos.ticket)
    except Exception as _e:
        log.debug(f"[MT5] position ticket resolve: {_e}")
    return int(order_ticket)


def mt5_cancel_pending_athena_orders(
    athena_display: str, magic: int = 240601
) -> dict:
    """Remove stale pending orders (limits/stops) tagged with Athena magic for this symbol.

    Prevents orphaned child pendings before a new parent entry on the same instrument.
    """
    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return {"error": True, "detail": "MT5 not connected", "cancelled": 0}
    mt5_sym = mt5_map_symbol(athena_display)
    if not mt5_sym:
        return {"error": True, "detail": "no_symbol_mapping", "cancelled": 0}
    if not mt5.symbol_select(mt5_sym, True):
        return {"error": True, "detail": "symbol_unavailable", "cancelled": 0}
    pending_types = (
        mt5.ORDER_TYPE_BUY_LIMIT,
        mt5.ORDER_TYPE_SELL_LIMIT,
        mt5.ORDER_TYPE_BUY_STOP,
        mt5.ORDER_TYPE_SELL_STOP,
        mt5.ORDER_TYPE_BUY_STOP_LIMIT,
        mt5.ORDER_TYPE_SELL_STOP_LIMIT,
    )
    cancelled = 0
    try:
        for o in mt5.orders_get(symbol=mt5_sym) or []:
            if getattr(o, "magic", 0) != magic:
                continue
            if o.type not in pending_types:
                continue
            r2 = mt5.order_send(
                {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": o.ticket,
                    "symbol": mt5_sym,
                }
            )
            if r2 and r2.retcode == mt5.TRADE_RETCODE_DONE:
                cancelled += 1
                log.info(
                    f"[MT5] Cancelled stale pending order ticket={o.ticket} type={o.type} on {mt5_sym}"
                )
    except Exception as e:
        log.warning(f"[MT5] pending order cleanup failed for {mt5_sym}: {e}")
        return {"error": True, "detail": str(e), "cancelled": cancelled, "mt5_symbol": mt5_sym}
    return {"error": False, "cancelled": cancelled, "mt5_symbol": mt5_sym}


def mt5_reconcile_after_open(exec_result: dict, signal: dict) -> dict:
    """Verify open position(s) still have SL/TP attached after fill (child hygiene).

    Does not modify MT5 state here — reports only so callers can act. (Repairs are
    broker-specific and risk mis-clicks; log loudly if missing.)
    """
    if not exec_result.get("success"):
        return {"skipped": True}
    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        return {"error": True, "detail": "MT5 not connected"}
    pair = signal.get("pair", "")
    mt5_sym = mt5_map_symbol(pair)
    if not mt5_sym:
        return {"error": True, "detail": "no_mapping"}
    legs = []
    try:
        plist = mt5.positions_get(symbol=mt5_sym) or []
        magic = 240601
        want_vol = float(exec_result.get("volume") or 0)
        for pos in plist:
            if getattr(pos, "magic", 0) != magic:
                continue
            requires_tp = not _uses_trailing_atr_exit(signal)
            sl_ok = (pos.sl or 0) > 0
            tp_ok = (pos.tp or 0) > 0
            legs.append(
                {
                    "ticket": int(pos.ticket),
                    "volume": float(pos.volume),
                    "sl": float(pos.sl or 0),
                    "tp": float(pos.tp or 0),
                    "ok": sl_ok and (tp_ok or not requires_tp),
                }
            )
        if not legs:
            return {
                "error": False,
                "legs": [],
                "note": "no_magic_positions_found_after_fill",
                "expectedVolumeApprox": want_vol,
            }
        all_ok = all(x["ok"] for x in legs)
        if not all_ok:
            log.warning(
                f"[MT5] RECONCILE: {mt5_sym} position(s) missing SL/TP after open: {legs}"
            )
        return {
            "error": False,
            "legs": legs,
            "allProtectionsPresent": all_ok,
            "takeProfitRequired": not _uses_trailing_atr_exit(signal),
            "expectedVolumeApprox": want_vol,
        }
    except Exception as e:
        log.warning(f"[MT5] reconcile_after_open failed: {e}")
        return {"error": True, "detail": str(e)}


def mt5_execute(signal: dict, approval: "RiskApproval") -> dict:  # noqa: F821
    """Execute a trade on MT5. ONLY accepts a pre-validated RiskApproval.



    Args:

        signal: Full signal from analyze_pair() — needs pair, direction, price, sl, tp1

        approval: RiskApproval from risk_engine.risk_check() — MUST be approved



    Returns:

        dict with success, ticket, volume, entry_price, error

    """

    from risk_engine import RiskApproval  # Type check

    if not isinstance(approval, RiskApproval):
        return {
            "success": False,
            "error": "INVALID_APPROVAL: must be RiskApproval from risk_engine",
        }

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
        return {
            "success": False,
            "error": f"MARKET_CLOSED: {mt5_symbol} price is 0 — market likely closed",
        }

    # Fix #3 — broker tick age check (default disabled via config).
    _tick_age_limit = _mt5_max_tick_age_sec()
    if _tick_age_limit is not None:
        _tick_age = _mt5_tick_age_seconds(tick)
        if _tick_age is None:
            log.warning(
                f"[MT5] {mt5_symbol}: tick timestamp missing; "
                f"MAX_BROKER_TICK_AGE_SEC.mt5={_tick_age_limit:.2f}s is enabled; rejecting"
            )
            return {
                "success": False,
                "error": "BROKER_TICK_TIMESTAMP_MISSING",
                "tickAgeLimitSec": _tick_age_limit,
            }
        if _tick_age > _tick_age_limit:
            log.warning(
                f"[MT5] {mt5_symbol}: tick age {_tick_age:.2f}s exceeds "
                f"MAX_BROKER_TICK_AGE_SEC.mt5={_tick_age_limit:.2f}s — rejecting"
            )
            return {
                "success": False,
                "error": (
                    f"BROKER_TICK_STALE: {_tick_age:.2f}s > "
                    f"{_tick_age_limit:.2f}s limit"
                ),
                "tickAgeSec": round(_tick_age, 3),
                "tickAgeLimitSec": _tick_age_limit,
            }

    # Fix #4 — execution spread cap (default disabled via per-asset config).
    _spread_limit = _mt5_max_spread_pct(signal)
    if _spread_limit is not None:
        _spread_pct = _mt5_spread_pct(tick)
        if _spread_pct is None:
            return {
                "success": False,
                "error": "EXECUTABLE_SPREAD_UNAVAILABLE",
                "spreadLimitPct": _spread_limit,
            }
        if _spread_pct is not None and _spread_pct > _spread_limit:
            log.warning(
                f"[MT5] {mt5_symbol}: spread {_spread_pct*100:.4f}% exceeds "
                f"MAX_EXECUTION_SPREAD_PCT cap {_spread_limit*100:.4f}% — rejecting"
            )
            return {
                "success": False,
                "error": (
                    f"SPREAD_TOO_WIDE: {_spread_pct*100:.4f}% > "
                    f"{_spread_limit*100:.4f}% limit"
                ),
                "spreadPct": round(_spread_pct, 6),
                "spreadLimitPct": _spread_limit,
            }

    # Get symbol info for proper rounding

    sym_info = mt5.symbol_info(mt5_symbol)

    digits = sym_info.digits if sym_info else 5

    trade_state = _mt5_trade_state(mt5, mt5_symbol, sym_info)
    block_reason = _mt5_trade_state_block_reason(trade_state, direction, mt5)
    if block_reason:
        log.error(f"[MT5] {mt5_symbol}: trade disabled before order_send: {trade_state}")
        return {
            "success": False,
            "error": f"MT5_TRADE_DISABLED:{block_reason}",
            "retcode": getattr(mt5, "TRADE_RETCODE_TRADE_DISABLED", 10017),
            "tradeState": trade_state,
        }

    sl = round(float(signal.get("sl", 0)), digits)

    tp = round(float(signal.get("tp1", 0)), digits)  # Use TP1 as primary target

    price = round(price, digits)

    # ── Shift SL/TP to live fill price if signal price has drifted ─────────

    # Handles data-source mismatch (e.g. EODHD vs MT5 live feed for commodities)

    signal_price = float(signal.get("price") or signal.get("livePrice") or 0)
    override_meta = signal.get("level_override")
    if not isinstance(override_meta, dict):
        override_meta = {}

    # F4: structural rebase observability marker (default off).
    _structural_rebase_event: dict | None = None

    if override_meta.get("entry_rebase") and sl != 0 and tp != 0:
        try:
            anchor_entry = float(override_meta.get("anchor_entry") or 0)
            sl_offset = float(override_meta.get("sl_offset"))
            tp_offset = float(override_meta.get("tp1_offset"))
        except (TypeError, ValueError):
            anchor_entry = 0.0
            sl_offset = None
            tp_offset = None

        if anchor_entry > 0 and sl_offset is not None and tp_offset is not None:
            sl = round(price + sl_offset, digits)

            tp = round(price + tp_offset, digits)

            log.info(
                f"[MT5] {mt5_symbol}: rebased AI levels from anchor {anchor_entry} to live entry {price} "
                f"-> SL={sl} TP={tp}"
            )

    elif signal_price > 0 and sl != 0 and tp != 0:
        drift = abs(price - signal_price) / signal_price

        if drift > 0.01:  # >1% drift — rebase levels to live price
            sl_offset = float(signal.get("sl", 0)) - signal_price

            tp_offset = float(signal.get("tp1", 0)) - signal_price

            _pre_rebase_sl = float(sl)
            _pre_rebase_tp = float(tp)

            sl = round(price + sl_offset, digits)

            tp = round(price + tp_offset, digits)

            log.info(
                f"[MT5] {mt5_symbol}: price drift {drift:.1%} ({signal_price}→{price}) — rebased SL={sl} TP={tp}"
            )
            # F4: classify the rebase. Engine B / naked structural levels were
            # placed at specific market structure; parallel-shifting them by
            # drift loses that meaning. Default behaviour is unchanged — we
            # just surface the event for audit logs. Optional fail-closed via
            # ATR_FRESHNESS.BLOCK_STRUCTURAL_REBASE.
            try:
                from execution import _is_structural_engine_b_execution
                _is_structural = _is_structural_engine_b_execution(signal)
            except Exception:
                _is_structural = False
            _structural_rebase_event = {
                "drift_pct": round(drift, 6),
                "signal_price": signal_price,
                "broker_price": float(price),
                "pre_rebase_sl": _pre_rebase_sl,
                "pre_rebase_tp": _pre_rebase_tp,
                "post_rebase_sl": float(sl),
                "post_rebase_tp": float(tp),
                "is_structural": bool(_is_structural),
                "sl_method": str(
                    signal.get("sl_method")
                    or (signal.get("engine_b") or {}).get("sl_method")
                    or ""
                ).lower()
                or None,
            }
            try:
                from config import CONFIG as _mt5_cfg
                _block_structural = bool(
                    (_mt5_cfg.get("ATR_FRESHNESS") or {}).get(
                        "BLOCK_STRUCTURAL_REBASE", False
                    )
                )
            except Exception:
                _block_structural = False
            if _block_structural and _is_structural:
                return {
                    "success": False,
                    "error": (
                        "STRUCTURAL_DRIFT_REQUIRES_REFRESH: Engine B/structural levels parallel-shifted"
                        f" by drift {drift*100:.4f}% — refuse fail-closed per config"
                    ),
                    "structuralRebase": _structural_rebase_event,
                    "providerDrift": round(drift, 6),
                    "signalPrice": signal_price,
                    "brokerPrice": float(price),
                }

    # ── SL/TP validation against live price ────────────────────────────────

    # Ensure SL/TP are on the correct side of entry price

    if direction == "LONG":
        if sl >= price:
            log.error(
                f"[MT5] {mt5_symbol} LONG: SL {sl} >= entry {price} — invalid (SL must be below entry)"
            )

            return {
                "success": False,
                "error": f"INVALID_SL: SL {sl} is above entry {price} for LONG",
            }

        if tp <= price:
            log.error(
                f"[MT5] {mt5_symbol} LONG: TP {tp} <= entry {price} — invalid (TP must be above entry)"
            )

            return {
                "success": False,
                "error": f"INVALID_TP: TP {tp} is below entry {price} for LONG",
            }

    else:  # SHORT
        if sl <= price:
            log.error(
                f"[MT5] {mt5_symbol} SHORT: SL {sl} <= entry {price} — invalid (SL must be above entry)"
            )

            return {
                "success": False,
                "error": f"INVALID_SL: SL {sl} is below entry {price} for SHORT",
            }

        if tp >= price:
            log.error(
                f"[MT5] {mt5_symbol} SHORT: TP {tp} >= entry {price} — invalid (TP must be below entry)"
            )

            return {
                "success": False,
                "error": f"INVALID_TP: TP {tp} is above entry {price} for SHORT",
            }

    # Ensure SL distance within MAX_SL_PCT for asset class (config — possible mismatch if scale wrong)

    sl_dist_pct = abs(price - sl) / price

    try:
        from risk_engine import resolve_max_sl_pct

        _max_sl_pct, _max_sl_source = resolve_max_sl_pct(signal, signal.get("type", ""), CONFIG)
    except Exception:
        _max_sl_pct = CONFIG.get("MAX_SL_PCT", {}).get(signal.get("type", ""), 0.05)
        _max_sl_source = f"asset:{signal.get('type', '')}"
    if sl_dist_pct > _max_sl_pct:
        log.error(
            f"[MT5] {mt5_symbol}: SL distance {sl_dist_pct:.1%} exceeds configured cap {_max_sl_pct:.1%} ({_max_sl_source})"
        )
        return {
            "success": False,
            "error": f"SL_TOO_FAR: SL is {sl_dist_pct:.0%} from entry (max {_max_sl_pct:.0%})",
        }

    # Check broker minimum stop distance

    if sym_info:
        min_stop_pts = sym_info.trade_stops_level  # in points

        point = sym_info.point

        min_stop_price = min_stop_pts * point if min_stop_pts and point else 0

        if min_stop_price > 0 and abs(price - sl) < min_stop_price:
            log.error(
                f"[MT5] {mt5_symbol}: SL too close — {abs(price - sl):.{digits}f} < min {min_stop_price:.{digits}f}"
            )

            return {
                "success": False,
                "error": f"SL_TOO_CLOSE: distance {abs(price - sl):.{digits}f} < broker min {min_stop_price:.{digits}f}",
            }

    # ── Multi-Target Volume Splitting ──────────────────────────────────────
    # To prevent orphaned pending TP2 orders, we split the total approved volume
    # into two separate positions, each with its own attached TP.
    # This ensures MT5 natively clears all orders on SL or manual close.

    total_vol = approval.volume
    tp2_raw = signal.get("tp2", 0)
    tp2 = round(float(tp2_raw), digits) if tp2_raw else 0
    vol_step = sym_info.volume_step if sym_info else 0.01
    vol_min = sym_info.volume_min if sym_info else 0.01

    _is_scalp = _is_engine_d_signal(signal)
    _uses_trailing_exit = _uses_trailing_atr_exit(signal)
    _use_broker_tp = _mt5_should_send_broker_tp(signal)
    # Engine D / Scalp Lab uses one protected broker position with TP1 and SL.
    if _is_scalp:
        do_split = False
    else:
        # Only split if TP2 is distinct and total volume allows for at least 2 units of min_step
        do_split = (
            not _uses_trailing_exit
            and _use_broker_tp
            and tp > 0
            and tp2 > 0
            and abs(tp2 - tp) > (10 * sym_info.point)
            and total_vol >= (vol_min * 2)
        )

    # Hybrid scale-out (trailing_atr only, mutually exclusive with do_split):
    # banker leg keeps broker TP1 (banks the fraction); runner leg carries no
    # broker TP and is managed by the timed-exit chandelier trail.
    hybrid_split = False
    vols = [(total_vol, tp if _use_broker_tp else 0)]
    if do_split:
        vol1 = round(math.floor((total_vol / 2) / vol_step) * vol_step, 2)
        if vol1 < vol_min: vol1 = vol_min
        vol2 = round(total_vol - vol1, 2)
        vols = [(vol1, tp), (vol2, tp2)]
        log.info(f"[MT5] {mt5_symbol}: splitting {total_vol} lots into targets TP1={tp} ({vol1}) and TP2={tp2} ({vol2})")
    else:
        _hybrid_cfg = _timed_exit_cfg().get("hybrid_scaleout") or {}
        if (
            not _is_scalp
            and _uses_trailing_exit
            and _use_broker_tp
            and isinstance(_hybrid_cfg, dict)
            and bool(_hybrid_cfg.get("enabled"))
            and tp > 0
            and total_vol >= (vol_min * 2)
        ):
            try:
                _hybrid_frac = float(_hybrid_cfg.get("fraction", 0.5) or 0.5)
            except (TypeError, ValueError):
                _hybrid_frac = 0.5
            _hybrid_frac = min(max(_hybrid_frac, 0.0), 1.0)
            vol1 = round(math.floor((total_vol * _hybrid_frac) / vol_step) * vol_step, 2)
            if vol1 < vol_min:
                vol1 = vol_min
            vol2 = round(total_vol - vol1, 2)
            if vol2 >= vol_min:
                hybrid_split = True
                vols = [(vol1, tp), (vol2, 0)]
                log.info(
                    f"[MT5] {mt5_symbol}: hybrid scale-out — banker {vol1} lots TP1={tp}, "
                    f"runner {vol2} lots on chandelier (no broker TP)"
                )

    # ── Execution Loop ─────────────────────────────────────────────────────
    results = []
    for v_size, v_tp in vols:
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": mt5_symbol,
            "volume": v_size,
            "type": order_type,
            "price": price,
            "sl": sl,
            "deviation": 20,
            "magic": 240601,
            "comment": f"Ath|{pair[:12]}|{signal.get('confluenceScore', 0):.2f}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _SYMBOL_FILLING_MODES.get(mt5_symbol, mt5.ORDER_FILLING_IOC),
        }
        # Only include TP if valid (some brokers reject tp=0 as invalid)
        if v_tp and v_tp > 0:
            request["tp"] = v_tp

        order_check = _mt5_order_check_with_filling_fallback(mt5, request, sym_info=sym_info)
        if order_check is not None:
            check_retcode = getattr(order_check, "retcode", None)
            if not _mt5_is_success_retcode(check_retcode, mt5):
                check_comment = getattr(order_check, "comment", "")
                log.error(
                    f"[MT5] order_check rejected {mt5_symbol}: "
                    f"retcode={check_retcode} comment={check_comment}"
                )
                return {
                    "success": False,
                    "error": f"ORDER_CHECK_REJECTED: {check_comment}",
                    "retcode": check_retcode,
                    "request": _mt5_order_request_summary(request),
                    "orderCheck": _mt5_order_check_summary(order_check),
                    "tradeState": _mt5_trade_state(mt5, mt5_symbol, sym_info),
                }

        log.info(f"[MT5] Sending order: {direction} {v_size} {mt5_symbol} @ {price} | SL: {sl} | TP: {v_tp}")
        result = _send_order_with_filling_fallback(request)

        if result is None:
            error = mt5.last_error()
            log.error(f"[MT5] order_send returned None: {error}")
            return {"success": False, "error": f"ORDER_SEND_FAILED: {error}"}

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(f"[MT5] Order rejected: retcode={result.retcode} comment={result.comment}")
            trade_state = None
            if result.retcode == getattr(mt5, "TRADE_RETCODE_TRADE_DISABLED", 10017):
                trade_state = _mt5_trade_state(mt5, mt5_symbol, sym_info)
            rollback_results = []
            if results:
                log.error(
                    f"[MT5] Multi-leg: earlier leg(s) filled but this leg failed — "
                    f"rolling back {len(results)} open position(s) on {mt5_symbol}"
                )
                for rb in results:
                    try:
                        _tk = _mt5_resolve_position_ticket(
                            mt5,
                            mt5_symbol,
                            magic=240601,
                            volume=float(rb.volume),
                            order_ticket=int(rb.order),
                        )
                        _cr = mt5_close_position(_tk)
                        rollback_results.append(
                            {
                                "order_ticket": int(rb.order),
                                "position_ticket": _tk,
                                "result": _cr,
                                "success": bool(isinstance(_cr, dict) and _cr.get("success")),
                            }
                        )
                        log.warning(
                            f"[MT5] Rollback leg order_ticket={rb.order} position_ticket={_tk} -> {_cr}"
                        )
                    except Exception as _rb_e:
                        rollback_results.append(
                            {
                                "order_ticket": int(getattr(rb, "order", 0) or 0),
                                "success": False,
                                "error": str(_rb_e),
                            }
                        )
                        log.error(f"[MT5] Rollback failed for partial leg: {_rb_e}")
            rollback_complete = bool(results) and all(r.get("success") for r in rollback_results)
            rejected = {
                "success": False,
                "error": f"ORDER_REJECTED: {result.comment}",
                "retcode": result.retcode,
                "partialLegsRolledBack": sum(1 for r in rollback_results if r.get("success")),
                "rollbackComplete": rollback_complete if results else None,
                "rollbackResults": rollback_results,
                "request": _mt5_order_request_summary(request),
                "orderCheck": _mt5_order_check_summary(order_check),
            }
            if trade_state is not None:
                rejected["tradeState"] = trade_state
            return rejected

        log.info(f"[MT5] ORDER FILLED: ticket={result.order} | {direction} {result.volume} {mt5_symbol} @ {result.price}")
        results.append(result)

    # Resolve broker position tickets for every filled leg. MT5 order tickets and
    # position tickets can differ; audit/reconciliation must track positions.
    legs = []
    for idx, leg_result in enumerate(results):
        leg_position_ticket = _mt5_resolve_position_ticket(
            mt5,
            mt5_symbol,
            magic=240601,
            volume=float(leg_result.volume),
            order_ticket=int(leg_result.order),
        )
        leg_tp = vols[idx][1] if idx < len(vols) else tp
        legs.append(
            {
                "ticket": leg_position_ticket,
                "order_ticket": int(leg_result.order),
                "volume": float(leg_result.volume),
                "entryPrice": float(leg_result.price),
                "tp": leg_tp,
            }
        )

    # Secondary operations for the primary position
    result = results[0]
    position_ticket = legs[0]["ticket"]
    try:
        if position_ticket != int(result.order):
            log.info(
                f"[MT5] Resolved position ticket={position_ticket} (order_ticket={result.order})"
            )
    except Exception as _pt_err:
        log.debug(f"[MT5] position ticket lookup note: {_pt_err}")

    # Send Telegram notification for trade opened
    try:
        telegram_notify.notify_trade_opened(
            pair=pair,
            direction=direction,
            entry_price=result.price,
            stop_loss=sl,
            take_profit=tp,
            style=str(signal.get("style") or ""),
            engine=str(signal.get("engine") or ""),
            exchange="mt5",
        )
    except Exception as _tn_e:
        log.debug(f"[TELEGRAM] Trade open notification failed: {_tn_e}")

    _ref_slip = float(signal_price) if signal_price > 0 else float(price)
    _sref, _sbps = _mt5_entry_slippage_bps(direction, _ref_slip, float(result.price))
    _fee_cost = _mt5_total_fee_cost(mt5, [leg.get("ticket") for leg in legs])

    # Calculate total volume from all partial fills
    total_filled_vol = sum(float(r.volume) for r in results)

    return {
        "success": True,
        "ticket": position_ticket,
        "order_ticket": int(result.order),
        "volume": total_filled_vol,
        "entryPrice": result.price,
        "symbol": mt5_symbol,
        "direction": direction,
        "sl": sl,
        "tp": tp,
        "brokerTp": tp if _use_broker_tp else 0,
        "tpPartial": None,
        "tp2": tp2 if do_split else None,
        "tp2Ticket": int(results[1].order) if (do_split and len(results) > 1) else None,
        "tp2PositionTicket": legs[1]["ticket"] if (do_split and len(legs) > 1) else None,
        "hybridScaleout": hybrid_split,
        "legs": legs,
        "riskAmount": approval.risk_amount,
        "riskPct": approval.risk_pct,
        "feeCost": _fee_cost,
        "signalPriceRef": _sref,
        "slippageBps": _sbps,
        # F4: structural rebase event (None when no drift > 1% rebase fired).
        "structuralRebase": _structural_rebase_event,
    }


def mt5_list_symbols() -> list:
    """List all available MT5 symbols (useful for debugging symbol mapping)."""

    mt5 = _get_mt5()

    if not mt5 or not mt5_connect():
        return []

    symbols = mt5.symbols_get()

    if not symbols:
        return []

    return [
        {"name": s.name, "description": s.description, "path": s.path}
        for s in symbols[:200]
    ]
