"""bybit_executor.py — Bybit USDT Perpetual Futures execution module for Athena.

Replaces ccxt_executor.py for crypto signal execution.
Supports LONG and SHORT via Bybit Linear (USDT-M) Perpetual contracts.
Leverage: 1x, Margin: ISOLATED (safest configuration).
All orders must arrive as a pre-validated RiskApproval from risk_engine.py.
"""

import os
import logging
import time
import threading
from datetime import datetime, timezone

import telegram_notify

log = logging.getLogger("sentinel")

try:
    from config import CONFIG
except Exception:
    CONFIG = {}

_exchange = None


def _is_engine_d_signal(signal: dict) -> bool:
    engine = str((signal or {}).get("engine", "")).strip().lower()
    return engine in ("scalp", "engine d", "scalp_vp")


def _uses_trailing_atr_exit(signal: dict) -> bool:
    """True when Engine A/B exits are managed by the timed-exit chandelier trail."""
    mode = str((CONFIG.get("TIMED_EXIT") or {}).get("tp_mode", "fixed")).strip().lower()
    return mode == "trailing_atr" and not _is_engine_d_signal(signal)

# ── Periodic clock re-sync ────────────────────────────────────────────────────
# Bybit retCode 10002 fires when local clock drifts beyond recv_window (10 000 ms).
# adjustForTimeDifference corrects at init and on-error, but a slow-running Windows
# clock can accumulate 12–18 s of drift during a session. Re-sync every 3 minutes
# keeps the measured offset fresh without hammering the Bybit time endpoint.
_RESYNC_INTERVAL_SEC = 180  # 3 minutes

def _start_clock_sync_thread() -> None:
    """Start a daemon thread that periodically calls load_time_difference()."""
    if not bool(CONFIG.get("BYBIT_TIME_SYNC_ENABLED", False)):
        return

    def _sync_loop():
        while True:
            time.sleep(_RESYNC_INTERVAL_SEC)
            ex = _exchange
            if ex is not None:
                try:
                    ex.load_time_difference()
                    log.debug("[BYBIT] Periodic clock re-sync OK")
                except Exception as e:
                    log.debug(f"[BYBIT] Periodic clock re-sync skipped: {e}")

    t = threading.Thread(target=_sync_loop, name="bybit-clock-sync", daemon=True)
    t.start()

# Symbol mapping: Athena display/internal → ccxt linear futures format
_SYMBOL_MAP = {
    "BTC/USDT": "BTC/USDT:USDT",
    "ETH/USDT": "ETH/USDT:USDT",
    "XRP/USDT": "XRP/USDT:USDT",
    "SOL/USDT": "SOL/USDT:USDT",
    "ADA/USDT": "ADA/USDT:USDT",
    "DOGE/USDT": "DOGE/USDT:USDT",
    "AVAX/USDT": "AVAX/USDT:USDT",
    "LINK/USDT": "LINK/USDT:USDT",
    "POL/USDT": "POL/USDT:USDT",
    "BNB/USDT": "BNB/USDT:USDT",
    "DOT/USDT": "DOT/USDT:USDT",
    "LTC/USDT": "LTC/USDT:USDT",
    "SUI/USDT": "SUI/USDT:USDT",
    "NEAR/USDT": "NEAR/USDT:USDT",
    "APT/USDT": "APT/USDT:USDT",
    "INJ/USDT": "INJ/USDT:USDT",
    "RENDER/USDT": "RENDER/USDT:USDT",
    "AAVE/USDT": "AAVE/USDT:USDT",
    "ALGO/USDT": "ALGO/USDT:USDT",
    "ATOM/USDT": "ATOM/USDT:USDT",
    "BCH/USDT": "BCH/USDT:USDT",
    "ETC/USDT": "ETC/USDT:USDT",
    "TRX/USDT": "TRX/USDT:USDT",
    "XLM/USDT": "XLM/USDT:USDT",
    "UNI/USDT": "UNI/USDT:USDT",
    "FIL/USDT": "FIL/USDT:USDT",
    "ICP/USDT": "ICP/USDT:USDT",
    "HBAR/USDT": "HBAR/USDT:USDT",
    "ARB/USDT": "ARB/USDT:USDT",
    "OP/USDT": "OP/USDT:USDT",
    "SEI/USDT": "SEI/USDT:USDT",
}

# Reverse map from internal symbol (BTCUSDT) to ccxt format
_INTERNAL_MAP = {
    "BTCUSDT": "BTC/USDT:USDT",
    "ETHUSDT": "ETH/USDT:USDT",
    "XRPUSDT": "XRP/USDT:USDT",
    "SOLUSDT": "SOL/USDT:USDT",
    "ADAUSDT": "ADA/USDT:USDT",
    "DOGEUSDT": "DOGE/USDT:USDT",
    "AVAXUSDT": "AVAX/USDT:USDT",
    "LINKUSDT": "LINK/USDT:USDT",
    "POLUSDT": "POL/USDT:USDT",
    "BNBUSDT": "BNB/USDT:USDT",
    "DOTUSDT": "DOT/USDT:USDT",
    "LTCUSDT": "LTC/USDT:USDT",
    "SUIUSDT": "SUI/USDT:USDT",
    "NEARUSDT": "NEAR/USDT:USDT",
    "APTUSDT": "APT/USDT:USDT",
    "INJUSDT": "INJ/USDT:USDT",
    "RENDERUSDT": "RENDER/USDT:USDT",
    "AAVEUSDT": "AAVE/USDT:USDT",
    "ALGOUSDT": "ALGO/USDT:USDT",
    "ATOMUSDT": "ATOM/USDT:USDT",
    "BCHUSDT": "BCH/USDT:USDT",
    "ETCUSDT": "ETC/USDT:USDT",
    "TRXUSDT": "TRX/USDT:USDT",
    "XLMUSDT": "XLM/USDT:USDT",
    "UNIUSDT": "UNI/USDT:USDT",
    "FILUSDT": "FIL/USDT:USDT",
    "ICPUSDT": "ICP/USDT:USDT",
    "HBARUSDT": "HBAR/USDT:USDT",
    "ARBUSDT": "ARB/USDT:USDT",
    "OPUSDT": "OP/USDT:USDT",
    "SEIUSDT": "SEI/USDT:USDT",
}

_LEVERAGE = 1  # 1x — enables SHORT without amplified risk


def _entry_slippage_bps(
    direction: str, ref_price: float, fill_price: float
) -> tuple[float | None, float | None]:
    """Return (reference_price_used, slippage_bps). Positive bps = adverse for the trader."""
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


def _validate_exit_levels(
    direction: str, entry_price: float, sl: float, tp: float
) -> str | None:
    """Ensure SL/TP are on the correct side of the current entry price."""
    if entry_price <= 0:
        return "NO_PRICE_DATA"
    if sl <= 0 or tp <= 0:
        return "INVALID_LEVELS"
    if direction == "LONG":
        if sl >= entry_price:
            return f"INVALID_SL: SL {sl} is above entry {entry_price} for LONG"
        if tp <= entry_price:
            return f"INVALID_TP: TP {tp} is below entry {entry_price} for LONG"
    else:
        if sl <= entry_price:
            return f"INVALID_SL: SL {sl} is below entry {entry_price} for SHORT"
        if tp >= entry_price:
            return f"INVALID_TP: TP {tp} is above entry {entry_price} for SHORT"
    return None


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

    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")

    if not api_key or not api_secret or api_key == "REVOKE_AND_REPLACE":
        log.warning("[BYBIT] BYBIT_API_KEY / BYBIT_API_SECRET not set or placeholder")
        return None

    use_testnet = os.environ.get("BYBIT_TESTNET", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    use_demo = os.environ.get("BYBIT_DEMO", "false").lower() in ("true", "1", "yes")

    try:
        _exchange = ccxt.bybit(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "linear",  # USDT Perpetual
                    "defaultSettle": "USDT",
                    "adjustForTimeDifference": bool(CONFIG.get("BYBIT_TIME_SYNC_ENABLED", False)),
                    "recvWindow": int(CONFIG.get("BYBIT_RECV_WINDOW_MS", 30000) or 30000),
                },
            }
        )

        if use_testnet:
            _exchange.set_sandbox_mode(True)

        if use_demo:
            _exchange.enable_demo_trading(True)

        # Sync local/client drift against Bybit server time to avoid retCode 10002
        # on signed requests like fetch_positions/fetch_balance during startup polling.
        if bool(CONFIG.get("BYBIT_TIME_SYNC_ENABLED", False)):
            try:
                _exchange.load_time_difference()
            except Exception as sync_err:
                log.debug(f"[BYBIT] time sync note: {sync_err}")

        env_label = "DEMO" if use_demo else ("TESTNET" if use_testnet else "LIVE")
        log.info(f"[BYBIT] Bybit Linear Futures {env_label} connected")
        _start_clock_sync_thread()
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


def _set_trading_stop(exchange, ccxt_symbol: str, sl: float = 0, tp: float = 0) -> None:
    """Set SL/TP on an open Bybit v5 position via trading-stop endpoint.

    Bybit v5 does not support stop_market / take_profit_market order types.
    The correct approach is POST /v5/position/trading-stop.
    ccxt_symbol format: "DOT/USDT:USDT" → raw_symbol "DOTUSDT"
    """
    raw_symbol = ccxt_symbol.split(":")[0].replace("/", "")  # DOT/USDT:USDT → DOTUSDT
    params: dict = {"category": "linear", "symbol": raw_symbol, "positionIdx": 0}
    if sl > 0:
        params["stopLoss"] = str(sl)
        params["slTriggerBy"] = "MarkPrice"
    if tp > 0:
        params["takeProfit"] = str(tp)
        params["tpTriggerBy"] = "MarkPrice"
    if sl > 0 or tp > 0:
        exchange.private_post_v5_position_trading_stop(params)


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


def _resync_time_if_needed(exchange, e: Exception) -> bool:
    """Re-sync clock on retCode 10002 (timestamp drift). Returns True if re-synced."""
    if "10002" in str(e):
        try:
            exchange.load_time_difference()
            log.info("[BYBIT] Clock re-synced after 10002 timestamp error")
            return True
        except Exception as sync_err:
            log.warning(f"[BYBIT] Clock re-sync failed: {sync_err}")
    return False


def bybit_get_account() -> dict:
    """Get Bybit futures wallet balance (USDT).
    Returns dict with standardized error format."""
    exchange = _get_exchange()
    if not exchange:
        return {"error": True, "symbol": "Bybit", "detail": "Bybit not connected"}
    for attempt in range(2):
        try:
            balance = exchange.fetch_balance(params={"type": "linear"})
            usdt = balance.get("USDT", {})
            total = usdt.get("total", 0) or 0
            free = usdt.get("free", 0) or 0
            # Include unrealized PnL so drawdown circuit breaker sees real equity
            try:
                positions = exchange.fetch_positions(
                    params={"category": "linear", "settleCoin": "USDT"}
                )
                unrealized = sum(
                    float(p.get("unrealizedPnl", 0) or 0) for p in (positions or [])
                )
            except Exception:
                unrealized = 0.0
            return {
                "error": False,
                "symbol": "Bybit",
                "detail": "",
                "exchange": "Bybit",
                "testnet": os.environ.get("BYBIT_TESTNET", "false").lower()
                in ("true", "1", "yes"),
                "balance": total,
                "equity": total + unrealized,  # ← real equity including open P&L
                "freeBalance": free,
                "currency": "USDT",
            }
        except Exception as e:
            if attempt == 0 and _resync_time_if_needed(exchange, e):
                continue
            log.error(f"[BYBIT] Failed to fetch balance: {e}")
            return {"error": True, "symbol": "Bybit", "detail": str(e)}


def bybit_get_positions() -> dict:
    """Get open Bybit Linear Futures positions with risk estimates.
    Returns dict with standardized error format."""
    exchange = _get_exchange()
    if not exchange:
        return {
            "error": True,
            "symbol": "Bybit",
            "detail": "Bybit not connected",
            "positions": [],
        }
    for attempt in range(2):
        try:
            raw = exchange.fetch_positions(
                params={"category": "linear", "settleCoin": "USDT"}
            )
            positions = []
            for pos in raw:
                size = abs(float(pos.get("contracts", 0) or 0))
                if size <= 0:
                    continue
                entry = float(pos.get("entryPrice", 0) or 0)
                notional = size * entry
                info = pos.get("info", {})
                sl_val = float(info.get("stopLoss", 0) or 0)
                tp_val = float(info.get("takeProfit", 0) or 0)
                liq_price = float(info.get("liqPrice", 0) or 0)
                if sl_val > 0 and entry > 0:
                    est_risk = round(abs(entry - sl_val) / entry * notional, 2)
                else:
                    est_risk = round(
                        notional * 1.0, 2
                    )  # fallback: 100% when no SL is set
                symbol_raw = pos.get("symbol", "")
                # Convert BTC/USDT:USDT back to display format BTC/USDT
                display = symbol_raw.split(":")[0] if ":" in symbol_raw else symbol_raw
                # markPrice: ccxt stores as "markPrice" in info or top-level
                mark_price = float(pos.get("markPrice") or info.get("markPrice") or 0)
                side = pos.get("side", "")
                direction = "LONG" if side.lower() == "long" else "SHORT"
                upnl = float(pos.get("unrealizedPnl", 0) or 0)
                positions.append(
                    {
                        # Normalised fields (match MT5 position card schema)
                        "pair": display,
                        "direction": direction,
                        "entry": entry,
                        "volume": size,
                        "profit": round(upnl, 2),
                        "sl": sl_val,
                        "tp": tp_val,
                        "ticket": info.get("positionIdx", ""),
                        # Bybit-specific extras
                        "symbol": symbol_raw,
                        "contracts": size,
                        "side": side,
                        "entryPrice": entry,
                        "markPrice": mark_price,
                        "lastPrice": mark_price,
                        "unrealizedPnl": upnl,
                        "notional": round(notional, 2),
                        "risk_amount": est_risk,
                        "liqPrice": liq_price,
                    }
                )
            return {
                "error": False,
                "symbol": "Bybit",
                "detail": "",
                "positions": positions,
            }
        except Exception as e:
            if attempt == 0 and _resync_time_if_needed(exchange, e):
                continue
            log.error(f"[BYBIT] Failed to fetch positions: {e}")
            return {"error": True, "symbol": "Bybit", "detail": str(e), "positions": []}


def bybit_audit_ts_to_ms(ts_iso: str | None) -> int:
    if not ts_iso:
        return 0
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def bybit_fetch_closed_positions_history(
    exchange,
    ccxt_symbol: str,
    since_ms: int,
    until_ms: int | None = None,
    limit: int = 100,
) -> list:
    """Closed USDT-linear rows from Bybit v5 /position/closed-pnl (via CCXT).

    Bybit allows at most ~7 days per start/end window; span is clamped here.
    """
    if not exchange:
        return []
    if until_ms is None:
        until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    max_span_ms = 7 * 24 * 3600 * 1000
    if until_ms - since_ms > max_span_ms:
        since_ms = until_ms - max_span_ms
    try:
        rows = exchange.fetch_positions_history(
            [ccxt_symbol],
            since=since_ms,
            limit=limit,
            params={"until": until_ms},
        )
        return list(rows or [])
    except Exception as e:
        log.debug(f"[BYBIT] fetch_positions_history({ccxt_symbol}) failed: {e}")
        return []


def bybit_audit_row_still_open(row: dict, open_positions_on_symbol: list) -> bool:
    """True if an open Bybit position matches this audit row (entry + size)."""
    try:
        entry = float(row.get("entry_price") or 0)
        vol = float(row.get("volume") or 0)
    except (TypeError, ValueError):
        return False
    if entry <= 0 or vol <= 0:
        return False
    for p in open_positions_on_symbol or []:
        pe = float(p.get("entryPrice", 0) or 0)
        pv = float(p.get("contracts", 0) or 0)
        if pe <= 0 or pv <= 0:
            continue
        if abs(pe - entry) / entry <= 0.008 and abs(pv - vol) / max(vol, 1e-12) <= 0.06:
            return True
    return False


def bybit_match_closed_history_row(
    row: dict,
    history_rows: list,
    used_keys: set,
    entry_tol: float = 0.02,
    vol_tol: float = 0.06,
) -> dict | None:
    """Match one audit_log open to a closed-PnL history row (CCXT parsed position)."""
    try:
        audit_entry = float(row.get("entry_price") or 0)
        audit_vol = float(row.get("volume") or 0)
    except (TypeError, ValueError):
        return None
    if audit_entry <= 0 or audit_vol <= 0:
        return None
    direction = (row.get("direction") or "").upper()
    want_side = "long" if direction == "LONG" else "short"
    entry_ts_ms = bybit_audit_ts_to_ms(row.get("ts"))

    best = None
    best_score = None
    for pos in history_rows:
        info = pos.get("info") or {}
        oid = str(info.get("orderId") or "")
        ut = str(info.get("updatedTime") or "")
        key = f"{oid}|{ut}|{info.get('avgEntryPrice')}|{info.get('qty')}"
        if key in used_keys:
            continue
        ps = (pos.get("side") or "").lower()
        if ps != want_side:
            continue
        ep = float(pos.get("entryPrice") or 0)
        ct = float(pos.get("contracts") or 0)
        if ep <= 0 or ct <= 0:
            continue
        lut = pos.get("lastUpdateTimestamp")
        if lut is None:
            try:
                lut = int(info.get("updatedTime") or 0)
            except (TypeError, ValueError):
                lut = 0
        if isinstance(lut, float):
            lut = int(lut)
        if entry_ts_ms and lut and lut < entry_ts_ms - 180_000:
            continue
        if abs(ep - audit_entry) / audit_entry > entry_tol:
            continue
        rel = abs(ct - audit_vol) / max(audit_vol, 1e-12)
        if rel > vol_tol and abs(ct - audit_vol) > 1e-6:
            continue
        score = abs(ep - audit_entry) / audit_entry + rel
        if best_score is None or score < best_score:
            best_score = score
            best = (pos, key)

    if not best:
        return None
    pos, key = best
    used_keys.add(key)
    return pos


def bybit_closed_row_to_outcome(pos: dict) -> tuple[float, str, float]:
    """Return (exit_price, exit_time_iso, pnl) from a matched closed-history position dict."""
    info = pos.get("info") or {}
    exit_px = float(
        pos.get("lastPrice")
        or info.get("avgExitPrice")
        or info.get("avg_exit_price")
        or 0
    )
    pnl = float(pos.get("realizedPnl") or info.get("closedPnl") or 0)
    lut = pos.get("lastUpdateTimestamp")
    if lut is None:
        try:
            lut = int(info.get("updatedTime") or 0)
        except (TypeError, ValueError):
            lut = 0
    if isinstance(lut, float):
        lut = int(lut)
    if lut > 1_000_000_000_000:  # ms
        exit_iso = datetime.fromtimestamp(lut / 1000.0, tz=timezone.utc).isoformat()
    elif lut > 1_000_000_000:
        exit_iso = datetime.fromtimestamp(float(lut), tz=timezone.utc).isoformat()
    else:
        exit_iso = (
            pos.get("datetime")
            or datetime.now(timezone.utc).isoformat()
        )
    return exit_px, exit_iso, pnl


def bybit_fallback_outcome_from_trades(
    exchange, ccxt_symbol: str, row: dict
) -> tuple[float, str, float] | None:
    """Last-resort: infer close from user trades after entry (no closed-pnl match)."""
    if not exchange:
        return None
    since_ms = bybit_audit_ts_to_ms(row.get("ts"))
    try:
        trades = exchange.fetch_my_trades(
            ccxt_symbol, since=max(0, since_ms - 120_000), limit=200
        )
    except Exception as e:
        log.debug(f"[BYBIT] fetch_my_trades fallback {ccxt_symbol}: {e}")
        return None
    if not trades:
        return None
    direction = (row.get("direction") or "").upper()
    need_side = "sell" if direction == "LONG" else "buy"
    try:
        vol = float(row.get("volume") or 0)
        entry = float(row.get("entry_price") or 0)
    except (TypeError, ValueError):
        return None
    last_t = None
    last_pnl = None
    last_px = None
    for t in sorted(trades, key=lambda x: x.get("timestamp") or 0):
        if (t.get("side") or "").lower() != need_side:
            continue
        ts = int(t.get("timestamp") or 0)
        if since_ms and ts + 10_000 < since_ms:
            continue
        amt = float(t.get("amount") or 0)
        if vol > 0 and abs(amt - vol) / max(vol, 1e-12) > 0.12:
            continue
        px = float(t.get("price") or 0)
        info = t.get("info") or {}
        rp = info.get("closedPnl")
        if rp is not None:
            try:
                pnl = float(rp)
            except (TypeError, ValueError):
                pnl = None
        else:
            pnl = None
        if pnl is None and entry > 0 and px > 0 and amt > 0:
            pnl = (px - entry) * amt if direction == "LONG" else (entry - px) * amt
        if pnl is None:
            continue
        last_t = t
        last_pnl = pnl
        last_px = px
    if not last_t or last_px is None or last_pnl is None:
        return None
    ts = int(last_t.get("timestamp") or 0)
    exit_iso = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
    return last_px, exit_iso, float(last_pnl)


def bybit_close_position(pair: str, direction: str, volume: float) -> dict:
    """Close an open Bybit position via reduceOnly market order."""
    exchange = _get_exchange()
    if not exchange:
        return {"success": False, "error": "Bybit not connected"}
    try:
        ccxt_symbol = bybit_map_symbol(pair)
        if not ccxt_symbol:
            return {"success": False, "error": f"No symbol mapping for {pair}"}
        close_side = "sell" if direction == "LONG" else "buy"
        order = exchange.create_market_order(
            ccxt_symbol,
            close_side,
            volume,
            params={"reduceOnly": True, "positionIdx": 0},
        )
        log.info(f"[BYBIT] Manual close {direction} {pair} vol={volume}")

        return {"success": True, "pair": pair, "direction": direction, "volume": volume}
    except Exception as e:
        log.error(f"[BYBIT] Close failed {pair}: {e}")
        return {"success": False, "error": str(e)}


def bybit_cancel_stale_entry_orders(ccxt_symbol: str | None) -> dict:
    """Cancel open non-reduce orders for the symbol (stale entry pendings).

    Live entries are market orders; anything left open here is typically a failed
    or abandoned entry child and should not sit alongside a new parent lifecycle.
    """
    if not ccxt_symbol:
        return {"skipped": True, "cancelled": 0}
    exchange = _get_exchange()
    if not exchange:
        return {"error": True, "detail": "BYBIT_NOT_CONNECTED", "cancelled": 0}
    cancelled = 0
    try:
        exchange.load_markets()
        open_orders = exchange.fetch_open_orders(ccxt_symbol) or []
        cleanup_errors = []
        for o in open_orders:
            if (o.get("status") or "").lower() not in ("open", "new", "live"):
                continue
            info = o.get("info") or {}
            reduce_only = bool(
                o.get("reduceOnly")
                or info.get("reduceOnly")
                or info.get("reduce_only")
            )
            if reduce_only:
                continue
            try:
                exchange.cancel_order(o["id"], ccxt_symbol)
                cancelled += 1
                log.info(
                    f"[BYBIT] Cancelled stale entry order id={o.get('id')} on {ccxt_symbol}"
                )
            except Exception as _ce:
                cleanup_errors.append({"order_id": o.get("id"), "error": str(_ce)})
                log.warning(f"[BYBIT] Could not cancel order {o.get('id')}: {_ce}")
        if cleanup_errors:
            return {
                "error": True,
                "detail": "STALE_ORDER_CANCEL_FAILED",
                "cancelled": cancelled,
                "symbol": ccxt_symbol,
                "cleanupErrors": cleanup_errors,
            }
    except Exception as e:
        log.debug(f"[BYBIT] stale entry order scan: {e}")
        return {"error": True, "detail": str(e), "cancelled": cancelled, "symbol": ccxt_symbol}
    return {"error": False, "cancelled": cancelled, "symbol": ccxt_symbol}


def bybit_reconcile_after_open(exec_result: dict, signal: dict) -> dict:
    """Re-fetch position; if SL/TP missing on the open position, re-apply trading-stop.

    Prevents an unmanaged child state after a successful market fill.
    """
    if not exec_result.get("success"):
        return {"skipped": True}
    exchange = _get_exchange()
    if not exchange:
        return {"error": True, "detail": "BYBIT_NOT_CONNECTED"}
    ccxt_symbol = exec_result.get("symbol") or bybit_map_symbol(
        signal.get("pair") or signal.get("symbol") or ""
    )
    if not ccxt_symbol:
        return {"error": True, "detail": "no_symbol"}
    sl = float(exec_result.get("sl") or signal.get("sl") or 0)
    requires_tp = not _uses_trailing_atr_exit(signal)
    tp = float(exec_result.get("tp") or signal.get("tp1") or 0)
    if sl <= 0 or (requires_tp and tp <= 0):
        return {"error": False, "note": "no_levels_to_verify", "repaired": False}
    try:
        raw = exchange.fetch_positions(
            params={"category": "linear", "settleCoin": "USDT"}
        )
        want = ccxt_symbol
        cur_sl, cur_tp = 0.0, 0.0
        for p in raw or []:
            if (p.get("symbol") or "") != want:
                continue
            size = abs(float(p.get("contracts", 0) or 0))
            if size <= 0:
                continue
            info = p.get("info") or {}
            cur_sl = float(info.get("stopLoss", 0) or 0)
            cur_tp = float(info.get("takeProfit", 0) or 0)
            break
        if cur_sl > 0 and (cur_tp > 0 or not requires_tp):
            return {
                "error": False,
                "hadProtections": True,
                "repaired": False,
                "stopLoss": cur_sl,
                "takeProfit": cur_tp,
                "takeProfitRequired": requires_tp,
            }
        missing_label = "SL/TP" if requires_tp else "SL"
        log.warning(
            f"[BYBIT] RECONCILE: open position on {ccxt_symbol} missing {missing_label} — reapplying"
        )
        _set_trading_stop(exchange, ccxt_symbol, sl=sl, tp=tp if requires_tp else 0)
        return {
            "error": False,
            "hadProtections": False,
            "repaired": True,
            "stopLoss": sl,
            "takeProfit": tp if requires_tp else 0,
            "takeProfitRequired": requires_tp,
        }
    except Exception as e:
        log.error(f"[BYBIT] reconcile_after_open failed: {e}")
        return {"error": True, "detail": str(e)}


def bybit_get_symbol_info(athena_display: str) -> dict:
    """Get Bybit symbol info for risk engine calculations.
    Returns dict with standardized error format."""
    exchange = _get_exchange()
    if not exchange:
        return {
            "error": True,
            "symbol": athena_display,
            "detail": "Bybit not connected",
        }

    ccxt_symbol = bybit_map_symbol(athena_display)
    if not ccxt_symbol:
        log.warning(f"[BYBIT] No symbol mapping for '{athena_display}'")
        return {"error": True, "symbol": athena_display, "detail": "No symbol mapping"}

    try:
        exchange.load_markets()
        market = exchange.market(ccxt_symbol)
        if not market:
            return {
                "error": True,
                "symbol": athena_display,
                "detail": "Market not found",
            }

        ticker = exchange.fetch_ticker(ccxt_symbol)
        price = ticker.get("last", 0) or 0

        # Correctly extract price precision and tick size from Bybit/CCXT metadata.
        # CCXT 'precision' can be TICK_SIZE (float like 0.0001) or DECIMAL_PLACES (int like 4).
        p_prec = market["precision"].get("price", 0.01)
        if isinstance(p_prec, int):
            _digits = p_prec
            _point = round(10**-_digits, 8)
        else:
            # TICK_SIZE mode (float like 0.0001)
            _point = float(p_prec)
            s_prec = f"{_point:.10f}".rstrip("0")
            _digits = len(s_prec.split(".")[1]) if "." in s_prec else 0

        # USDT Perpetual: 1 unit move = tick_size USDT
        tick_value = _point

        vol_min = market["limits"]["amount"].get("min", 0.001) or 0.001
        vol_max = market["limits"]["amount"].get("max", 9999) or 9999

        # Volume precision also needs robust handling (Amount)
        v_prec = market["precision"].get("amount", 0.001) or 0.001
        if isinstance(v_prec, int):
            vol_step = round(10**-v_prec, 8)
        else:
            vol_step = float(v_prec)

        return {
            "error": False,
            "symbol": athena_display,
            "detail": "",
            "ccxt_symbol": ccxt_symbol,
            "description": market.get("baseId", ccxt_symbol),
            "point": _point,
            "digits": _digits,
            "volume_min": vol_min,
            "volume_max": vol_max,
            "volume_step": vol_step,
            "trade_contract_size": 1,
            "trade_tick_value": tick_value,
            "trade_tick_size": _point,
            "bid": price,
            "ask": price,
            "spread": 0,
        }
    except Exception as e:
        log.error(f"[BYBIT] Failed to get symbol info for {athena_display}: {e}")
        return {"error": True, "symbol": athena_display, "detail": str(e)}


def bybit_execute(signal: dict, approval: "RiskApproval") -> dict:  # noqa: F821
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

    pair = signal.get("pair", "")
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
        price = ticker.get("ask" if direction == "LONG" else "bid", 0) or ticker.get(
            "last", 0
        )
        if price <= 0:
            return {"success": False, "error": f"NO_PRICE_DATA: {ccxt_symbol}"}

        sl = signal.get("sl", 0)
        tp1 = signal.get("tp1", 0)
        signal_price = float(signal.get("price") or signal.get("livePrice") or 0)
        override_meta = signal.get("level_override")
        if not isinstance(override_meta, dict):
            override_meta = {}

        # Chart-AI execution keeps the live fill as entry and shifts AI levels by the same offset.
        if override_meta.get("entry_rebase") and sl and tp1:
            try:
                anchor_entry = float(override_meta.get("anchor_entry") or 0)
                sl_offset = float(override_meta.get("sl_offset"))
                tp_offset = float(override_meta.get("tp1_offset"))
            except (TypeError, ValueError):
                anchor_entry = 0.0
                sl_offset = None
                tp_offset = None
            if anchor_entry > 0 and sl_offset is not None and tp_offset is not None:
                sl = round(price + sl_offset, 8)
                tp1 = round(price + tp_offset, 8)
                log.info(
                    f"[BYBIT] {ccxt_symbol}: rebased AI levels from anchor {anchor_entry} to live entry {price} "
                    f"-> SL={sl} TP={tp1}"
                )

        # Rebase levels when the scanned price is materially stale versus the live fill price.
        elif signal_price > 0 and sl and tp1:
            drift = abs(price - signal_price) / signal_price
            if drift > 0.01:
                sl_offset = float(sl) - signal_price
                tp_offset = float(tp1) - signal_price
                sl = round(price + sl_offset, 8)
                tp1 = round(price + tp_offset, 8)
                log.info(
                    f"[BYBIT] {ccxt_symbol}: price drift {drift:.1%} ({signal_price}→{price}) — rebased SL={sl} TP={tp1}"
                )

        level_error = _validate_exit_levels(
            direction, float(price), float(sl or 0), float(tp1 or 0)
        )
        if level_error:
            return {"success": False, "error": level_error}

        # Hard SL cap before sending to exchange
        try:
            from config import CONFIG as _cfg
            _max_sl_pct = _cfg.get("MAX_SL_PCT", {}).get("crypto", 0.08)
            _sl_dist_pct = abs(float(price) - float(sl)) / float(price)
            if _sl_dist_pct > _max_sl_pct:
                return {
                    "success": False,
                    "error": f"SL_TOO_WIDE: {_sl_dist_pct:.1%} exceeds {_max_sl_pct:.0%} crypto cap"
                }
        except Exception:
            pass  # graceful degradation — do not block execution on config error

        side = "buy" if direction == "LONG" else "sell"
        log.info(
            f"[BYBIT] Placing {side.upper()} market order: {volume} {ccxt_symbol} @ ~{price}"
        )

        # Market entry order — 3-attempt retry on transient errors
        import time as _time

        order = None
        _last_err = None
        for _attempt in range(3):
            try:
                order = exchange.create_market_order(
                    ccxt_symbol,
                    side,
                    volume,
                    params={"positionIdx": 0},  # one-way mode
                )
                break
            except Exception as _oe:
                _oe_name = type(_oe).__name__
                if any(
                    s in _oe_name
                    for s in ("NetworkError", "RequestTimeout")
                ):
                    _last_err = _oe
                    log.warning(
                        f"[BYBIT] Order attempt {_attempt + 1}/3 failed ({_oe_name}), retrying..."
                    )
                    _time.sleep(1)
                else:
                    raise
        if order is None:
            raise _last_err

        order_id = order.get("id", "")
        info = order.get("info") if isinstance(order.get("info"), dict) else {}
        status = str(
            order.get("status")
            or info.get("orderStatus")
            or info.get("status")
            or ""
        ).strip().lower()
        if status not in ("closed", "filled"):
            return {
                "success": False,
                "error": f"ORDER_NOT_FILLED: status={status or 'missing'}",
                "ticket": order_id,
            }
        filled_raw = order.get("filled")
        if filled_raw is None:
            filled_raw = info.get("cumExecQty") or info.get("filled")
        try:
            filled_amount = float(filled_raw)
        except (TypeError, ValueError):
            filled_amount = 0.0
        if filled_amount <= 0:
            return {
                "success": False,
                "error": "ORDER_NOT_FILLED: filled quantity missing or zero",
                "ticket": order_id,
            }
        if filled_amount + 1e-12 < float(volume):
            return {
                "success": False,
                "error": f"PARTIAL_FILL_UNSUPPORTED: filled={filled_amount} expected={volume}",
                "ticket": order_id,
                "volume": filled_amount,
            }
        filled_price = float(order.get("average") or order.get("price") or price)
        _ref_for_slip = float(signal.get("price") or 0) or float(price)
        _sig_ref, _slip_bps = _entry_slippage_bps(
            direction, _ref_for_slip, filled_price
        )

        log.info(
            f"[BYBIT] ENTRY FILLED: id={order_id} | {direction} {filled_amount} {ccxt_symbol} @ {filled_price}"
        )

        # Send Telegram notification for trade opened
        try:
            telegram_notify.notify_trade_opened(
                pair=pair,
                direction=direction,
                entry_price=filled_price,
                stop_loss=sl,
                take_profit=tp1,
            )
        except Exception as _tn_e:
            log.debug(f"[TELEGRAM] Trade open notification failed: {_tn_e}")

        fill_level_error = _validate_exit_levels(
            direction, filled_price, float(sl or 0), float(tp1 or 0)
        )
        if fill_level_error:
            try:
                close_side = "sell" if direction == "LONG" else "buy"
                exchange.create_market_order(
                    ccxt_symbol,
                    close_side,
                    filled_amount,
                    params={"reduceOnly": True, "positionIdx": 0},
                )
                log.warning(
                    f"[BYBIT] Emergency close sent after invalid post-fill levels for {ccxt_symbol}"
                )
            except Exception as close_err:
                log.error(
                    f"[BYBIT] Emergency close failed after invalid post-fill levels for {ccxt_symbol}: {close_err}"
                )
                return {
                    "success": False,
                    "error": fill_level_error,
                    "ticket": order_id,
                    "entryPrice": filled_price,
                    "volume": filled_amount,
                    "rollbackError": str(close_err),
                }
            return {
                "success": False,
                "error": fill_level_error,
                "ticket": order_id,
                "entryPrice": filled_price,
                "volume": filled_amount,
                "rolledBack": True,
            }

        _is_scalp = _is_engine_d_signal(signal)
        _use_broker_tp = not _uses_trailing_atr_exit(signal)
        _tp1 = float(signal.get("tp1", 0) or 0)
        _tp2 = float(signal.get("tp2", 0) or 0)
        _tp_partial = float(signal.get("tp_partial", 0) or 0)
        # Engine D: position TP sits at the structural target (tp1).
        # A separate reduce-only limit at tp_partial closes the first 50% clip at +1R.
        # Non-scalp signals use tp1 as the single full-position exit.
        tp_exec = _tp1 if _use_broker_tp else 0.0

        # Set SL/TP on the position via Bybit v5 trading-stop endpoint
        # (stop_market / take_profit_market order types are invalid in v5)
        sl_order_id = None
        tp_order_id = None
        _sl_tp_err = None
        for _attempt in range(2):  # 1 retry before emergency close
            try:
                _set_trading_stop(exchange, ccxt_symbol, sl=sl, tp=tp_exec)
                log.info(f"[BYBIT] SL/TP set: SL={sl} TP={tp_exec or 'disabled'}")
                _sl_tp_err = None
                break
            except Exception as ste:
                _sl_tp_err = ste
                if _attempt == 0:
                    log.warning(
                        f"[BYBIT] SL/TP set attempt 1 failed ({ste}), retrying in 2s…"
                    )
                    time.sleep(2)
        if _sl_tp_err is not None:
            log.error(
                f"[BYBIT] SL/TP set failed after retry — attempting emergency close: {_sl_tp_err}"
            )
            rollback_error = None
            try:
                close_side = "sell" if direction == "LONG" else "buy"
                exchange.create_market_order(
                    ccxt_symbol,
                    close_side,
                    filled_amount,
                    params={"reduceOnly": True, "positionIdx": 0},
                )
                log.warning(
                    f"[BYBIT] Emergency close sent for unprotected {ccxt_symbol} position"
                )
            except Exception as close_err:
                rollback_error = str(close_err)
                log.error(
                    f"[BYBIT] Emergency close failed for {ccxt_symbol}: {close_err}"
                )
            return {
                "success": False,
                "error": f"PROTECTIVE_ORDERS_FAILED: {_sl_tp_err}",
                "rolledBack": rollback_error is None,
                "rollbackError": rollback_error,
                "ticket": order_id,
                "entryPrice": filled_price,
                "volume": filled_amount,
            }

        # ── Fabio partial exit: reduce-only limit for first 50% clip at +1R ────────
        # For Engine D scalp signals: place a limit order that closes half the position
        # when price hits tp_partial (+1R). The broker TP handles the remaining 50%
        # runner. This is non-fatal; if it fails, the trade still runs to tp1 as a
        # single unit.
        partial_order_id = None
        if _is_scalp and _tp_partial > 0:
            try:
                partial_side = "sell" if direction == "LONG" else "buy"
                partial_qty = round(filled_amount * 0.5, 8)
                # Align to exchange minimum qty step
                try:
                    mkt = exchange.market(ccxt_symbol)
                    _v_prec = (mkt.get("precision") or {}).get("amount", 0.001) or 0.001
                    _step = float(_v_prec) if not isinstance(_v_prec, int) else round(10 ** -_v_prec, 8)
                    if _step > 0:
                        import math as _math
                        partial_qty = _math.floor(partial_qty / _step) * _step
                        partial_qty = round(partial_qty, 8)
                except Exception:
                    pass
                vol_min = 0.001
                try:
                    vol_min = float(exchange.market(ccxt_symbol)["limits"]["amount"].get("min") or 0.001)
                except Exception:
                    pass
                if partial_qty >= vol_min:
                    _partial_order = exchange.create_limit_order(
                        ccxt_symbol,
                        partial_side,
                        partial_qty,
                        _tp_partial,
                        params={"reduceOnly": True, "positionIdx": 0},
                    )
                    partial_order_id = _partial_order.get("id", "")
                    log.info(
                        f"[BYBIT] Partial +1R order placed: {partial_side} {partial_qty} @ {_tp_partial} "
                        f"id={partial_order_id} | runner TP={tp_exec}"
                    )
                else:
                    log.warning(
                        f"[BYBIT] Partial qty {partial_qty} < min {vol_min} — skipping partial order"
                    )
            except Exception as _pe:
                log.warning(f"[BYBIT] Partial +1R order failed (non-fatal, full position runs to TP1): {_pe}")

        fee_info = order.get("fee") or {}
        fee_cost = float(fee_info.get("cost") or 0) or None
        return {
            "success": True,
            "ticket": order_id,
            "volume": filled_amount,
            "entryPrice": filled_price,
            "symbol": ccxt_symbol,
            "direction": direction,
            "sl": sl,
            "tp": tp1,
            "tp1": _tp1,
            "tp2": _tp2 if _tp2 > 0 else None,
            "tpPartial": _tp_partial if _tp_partial > 0 else None,
            "partialOrderId": partial_order_id,
            "slOrderId": sl_order_id,
            "tpOrderId": tp_order_id,
            "riskAmount": approval.risk_amount,
            "riskPct": approval.risk_pct,
            "feeCost": fee_cost,
            "signalPriceRef": _sig_ref,
            "slippageBps": _slip_bps,
        }

    except Exception as e:
        log.error(f"[BYBIT] Order failed: {e}")
        return {"success": False, "error": f"ORDER_FAILED: {str(e)}"}


def bybit_move_sl_to_breakeven(
    ccxt_symbol: str, direction: str, entry_price: float, volume: float
) -> dict:
    """Move stop-loss to breakeven (entry price) for an open position.

    Called by the outcome monitor when a position reaches 1R profit.
    Validates the SL against the current mark price before submission —
    a race condition can cause mark price to move back past entry between
    the 1R check and the API call, which Bybit rejects (retCode 10001).
    """
    exchange = _get_exchange()
    if not exchange:
        return {"success": False, "error": "BYBIT_NOT_CONNECTED"}
    try:
        # Validate SL against live mark price to guard against the race where
        # price moves back past entry between the 1R check and the API call.
        # Bybit rule: SHORT SL must be > mark_price; LONG SL must be < mark_price.
        try:
            ticker = exchange.fetch_ticker(ccxt_symbol)
            mark_price = float(
                ticker.get("markPrice") or ticker.get("last") or 0
            )
        except Exception as tick_err:
            log.debug(f"[BYBIT] BREAKEVEN mark price fetch failed for {ccxt_symbol}: {tick_err}")
            mark_price = 0.0

        is_short = direction.upper() == "SHORT"
        if mark_price > 0:
            if is_short and entry_price <= mark_price:
                log.warning(
                    f"[BYBIT] BREAKEVEN skipped for {ccxt_symbol} SHORT: "
                    f"entry {entry_price} <= mark {mark_price} — market moved back against position"
                )
                return {"success": False, "error": "BREAKEVEN_INVALID_MARK_PRICE", "skipped": True}
            if not is_short and entry_price >= mark_price:
                log.warning(
                    f"[BYBIT] BREAKEVEN skipped for {ccxt_symbol} LONG: "
                    f"entry {entry_price} >= mark {mark_price} — market moved back against position"
                )
                return {"success": False, "error": "BREAKEVEN_INVALID_MARK_PRICE", "skipped": True}

        _set_trading_stop(exchange, ccxt_symbol, sl=entry_price)
        log.info(
            f"[BYBIT] BREAKEVEN SL placed: {ccxt_symbol} {direction} @ {entry_price} "
            f"(mark={mark_price:.4f})"
        )
        return {"success": True, "newSl": entry_price}
    except Exception as e:
        err_str = str(e)
        # retCode 34040 "not modified" — SL is already at the target price; treat as success.
        if "34040" in err_str or "not modified" in err_str.lower():
            log.debug(
                f"[BYBIT] BREAKEVEN already set for {ccxt_symbol} (34040 not modified) — no action needed"
            )
            return {"success": True, "newSl": entry_price, "alreadySet": True}
        log.warning(f"[BYBIT] Failed to move SL to breakeven for {ccxt_symbol}: {e}")
        return {"success": False, "error": err_str}


def bybit_disconnect():
    """Close exchange connection."""
    global _exchange
    _exchange = None
    log.info("[BYBIT] Disconnected")
