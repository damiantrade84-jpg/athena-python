"""Market metadata API route handlers and registration.

Behavior-neutral extraction from athena.py. These routes are read-only market
metadata/data helpers and do not own scoring, risk, or execution decisions.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import jsonify, request

from config import get_ai_api_key, get_ai_model

CONFIG: dict = {}
ALL_PAIRS = []
ACTIVE_PAIRS = []
ETF_PAIRS = []
JSE_PAIRS = []
_disabled_pairs = set()
_live_prices = {}
_live_prices_lock = None
fetch_yield_curve = None
http_requests = None
fetch_candles = None
fetch_bybit_klines = None
fetch_bybit_ticker = None
fetch_eodhd = None
_extract_candles = None
_merge_forex_forming_ws = None
_resample_from_h1 = None
_forex_h4_resample_offset_hours = lambda: 0.0
_eodhd_ticker_for_pair = None
_compute_naked_analysis = None
_json_safe = lambda value: value
log = logging.getLogger(__name__)


def api_market_hours():
    """Return open/closed status for all major markets in GMT+2 (SAST/EET).

    Sessions defined in UTC, converted to GMT+2 for display.
    Crypto is always open (24/7). Forex closes Fri 22:00 UTC, reopens Sun 22:00 UTC.
    """
    from datetime import datetime, timezone, timedelta

    now_utc = datetime.now(timezone.utc)
    now_gmt2 = now_utc + timedelta(hours=2)

    utc_h = now_utc.hour
    utc_m = now_utc.minute
    utc_total = utc_h * 60 + utc_m
    utc_weekday = now_utc.weekday()  # 0=Mon, 6=Sun

    def mins_until(target_total_utc):
        """Minutes until a UTC HH:MM time (expressed as total minutes)."""
        diff = target_total_utc - utc_total
        if diff <= 0:
            diff += 24 * 60
        return diff

    def fmt_opens_in(minutes):
        if minutes < 60:
            return f"Opens in {minutes}m"
        h = minutes // 60
        m = minutes % 60
        return f"Opens in {h}h {m}m" if m else f"Opens in {h}h"

    # ── Forex (Sun 22:00 UTC - Fri 22:00 UTC) ────────────────────────────────
    if utc_weekday == 5:  # Saturday - closed all day
        forex_open = False
        forex_status = "Closed (Weekend)"
        # Opens Sun 22:00 UTC = (6-5)*24*60 - utc_total + 22*60
        mins_to_sun22 = ((6 - utc_weekday) * 1440) - utc_total + 22 * 60
        forex_opens_in = fmt_opens_in(mins_to_sun22 % (7 * 1440))
    elif utc_weekday == 6 and utc_total < 22 * 60:  # Sunday before 22:00 UTC
        forex_open = False
        forex_status = "Closed (Weekend)"
        forex_opens_in = fmt_opens_in(22 * 60 - utc_total)
    elif utc_weekday == 4 and utc_total >= 22 * 60:  # Friday after 22:00 UTC
        forex_open = False
        forex_status = "Closed (Weekend)"
        forex_opens_in = "Opens Sun 22:00 UTC"
    else:
        forex_open = True
        forex_status = "Open"
        forex_opens_in = None

    # ── Sessions (UTC) ─────────────────────────────────────────────────────────
    # Sydney:    21:00-06:00 UTC  (23:00-08:00 GMT+2)
    # Tokyo:     00:00-09:00 UTC  (02:00-11:00 GMT+2)
    # London:    07:00-16:00 UTC  (09:00-18:00 GMT+2)
    # New York:  13:00-21:00 UTC  (15:00-23:00 GMT+2)

    def session_state(start_utc, end_utc):
        """Returns (is_open, mins_to_open_or_close)."""
        s = start_utc * 60
        e = end_utc * 60
        if s <= e:
            is_open = s <= utc_total < e
            if is_open:
                return True, e - utc_total
            else:
                return False, mins_until(s)
        else:  # wraps midnight
            is_open = utc_total >= s or utc_total < e
            if is_open:
                if utc_total >= s:
                    return True, (24 * 60 - utc_total) + e
                else:
                    return True, e - utc_total
            else:
                return False, mins_until(s)

    syd_open, syd_mins = session_state(21, 6)
    tok_open, tok_mins = session_state(0, 9)
    lon_open, lon_mins = session_state(7, 16)
    ny_open, ny_mins = session_state(13, 21)

    # London/NY overlap
    overlap_open = lon_open and ny_open

    # ── Equity markets (GMT+2 local times) ────────────────────────────────────
    # JSE:       09:00-17:00 GMT+2 (Mon-Fri)
    # LSE/UK100: 09:00-17:30 GMT+2 (Mon-Fri) = 07:00-15:30 UTC
    # NYSE/DAX:  NYSE 15:30-22:00 GMT+2 | DAX 09:00-17:30 GMT+2
    gmt2_h = now_gmt2.hour
    gmt2_m = now_gmt2.minute
    gmt2_total = gmt2_h * 60 + gmt2_m
    is_weekday = utc_weekday <= 4  # Mon-Fri

    def equity_state(open_gmt2, close_gmt2):
        s = open_gmt2[0] * 60 + open_gmt2[1]
        e = close_gmt2[0] * 60 + close_gmt2[1]
        if not is_weekday:
            diff = ((7 - utc_weekday) % 7) * 1440 + s - gmt2_total
            return False, "Closed (Weekend)", f"Opens Mon {open_gmt2[0]:02d}:{open_gmt2[1]:02d}"
        is_open = s <= gmt2_total < e
        if is_open:
            diff = e - gmt2_total
            return True, "Open", f"Closes in {diff//60}h {diff%60}m" if diff >= 60 else f"Closes in {diff}m"
        elif gmt2_total < s:
            diff = s - gmt2_total
            return False, "Pre-Market", fmt_opens_in(diff)
        else:
            return False, "Closed", f"Opens tomorrow {open_gmt2[0]:02d}:{open_gmt2[1]:02d}"

    jse_open, jse_status, jse_note = equity_state((9, 0), (17, 0))
    lse_open, lse_status, lse_note = equity_state((9, 0), (17, 30))
    dax_open, dax_status, dax_note = equity_state((9, 0), (17, 30))
    nyse_open, nyse_status, nyse_note = equity_state((15, 30), (22, 0))

    # ── Build response ─────────────────────────────────────────────────────────
    def session_detail(is_open, mins):
        if not forex_open:
            return {"open": False, "status": "Forex Closed", "note": forex_opens_in}
        if is_open:
            h, m = divmod(mins, 60)
            note = f"Closes in {h}h {m}m" if h else f"Closes in {m}m"
            return {"open": True, "status": "Active", "note": note}
        else:
            return {"open": False, "status": "Closed", "note": fmt_opens_in(mins)}

    return jsonify({
        "serverTime": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "localTime": now_gmt2.strftime("%Y-%m-%d %H:%M GMT+2"),
        "sessions": {
            "sydney":    session_detail(syd_open, syd_mins),
            "tokyo":     session_detail(tok_open, tok_mins),
            "london":    session_detail(lon_open, lon_mins),
            "new_york":  session_detail(ny_open, ny_mins),
            "overlap":   {"open": overlap_open and forex_open, "status": "London/NY Overlap" if (overlap_open and forex_open) else "Inactive", "note": "Highest liquidity"},
        },
        "markets": {
            "forex":  {"open": forex_open, "status": forex_status, "note": forex_opens_in or "Closes Fri 22:00 UTC", "hours": "Sun 22:00 - Fri 22:00 UTC"},
            "crypto": {"open": True, "status": "Open 24/7", "note": "Always open", "hours": "24/7"},
            "jse":    {"open": jse_open, "status": jse_status, "note": jse_note, "hours": "09:00-17:00 GMT+2"},
            "lse":    {"open": lse_open, "status": lse_status, "note": lse_note, "hours": "09:00-17:30 GMT+2"},
            "dax":    {"open": dax_open, "status": dax_status, "note": dax_note, "hours": "09:00-17:30 GMT+2"},
            "nyse":   {"open": nyse_open, "status": nyse_status, "note": nyse_note, "hours": "15:30-22:00 GMT+2"},
        },
    })

def api_prices():

    with _live_prices_lock:
        snapshot = dict(_live_prices)

    # Decorate each entry with ageSec so the UI/diagnostics can detect stalled
    # live-price feeds. Producers in candle_feeds.py mostly write ts as seconds
    # since epoch; EODHD WS writes milliseconds — normalize by magnitude.
    import time as _time_mod
    _now_s = _time_mod.time()

    def _epoch_seconds(value):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        return ts / 1000.0 if ts > 1e12 else ts

    def _age_seconds(value):
        ts = _epoch_seconds(value)
        if ts is None:
            return None
        return round(max(0.0, _now_s - ts), 3)

    decorated: dict = {}
    for _k, _v in snapshot.items():
        if not isinstance(_v, dict):
            decorated[_k] = _v
            continue
        entry = dict(_v)
        entry["ageSec"] = _age_seconds(entry.get("ts"))
        entry["ws_age_sec"] = _age_seconds(entry.get("last_ws_ts"))
        entry["rest_age_sec"] = _age_seconds(entry.get("last_rest_ts"))
        if entry.get("preferred_source") is None and entry.get("source") is not None:
            entry["preferred_source"] = entry.get("source")
        if entry.get("overwrite_reason") is None and entry.get("source") is not None:
            entry["overwrite_reason"] = "single_source"
        decorated[_k] = entry

    resp = jsonify(
        {
            "prices": decorated,
            "count": len(decorated),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    resp.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return resp

def api_yield_curve():
    """Phase E: Yield curve data for dashboard widget."""

    try:
        yc = fetch_yield_curve()

        if not yc:
            return jsonify({"error": "Yield curve unavailable"}), 503

        return jsonify(yc)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def api_bulk_prices():
    """Phase D: Bulk live OHLCV for US stocks via EODHD real-time endpoint (1 call vs multiple WS connections)."""

    try:
        _key = os.environ.get("EODHD_KEY", "")

        if not _key:
            return jsonify({"error": "EODHD_KEY not set"}), 500

        syms = request.args.get("symbols", "GOOG.US,GLD.US,SPY.US,QQQ.US")

        r = http_requests.get(
            f"https://eodhd.com/api/real-time/{syms.split(',')[0]}?s={','.join(syms.split(',')[1:])}&api_token={_key}&fmt=json",
            timeout=8,
        )

        if r.status_code != 200:
            return jsonify({"error": f"HTTP {r.status_code}"}), 502

        data = r.json()

        results = {}

        rows = data if isinstance(data, list) else [data]

        for row in rows:
            sym = row.get("code", "")

            results[sym] = {
                "price": row.get("close") or row.get("last_trade"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "volume": row.get("volume"),
                "changePct": row.get("change_p"),
                "changeDiff": row.get("change"),
                "timestamp": row.get("timestamp"),
            }

        return jsonify(
            {"prices": results, "ts": datetime.now(timezone.utc).isoformat()}
        )

    except Exception as e:
        log.error(f"api_bulk_prices error: {e}")

        return jsonify({"error": "Bulk prices failed"}), 500

def api_pairs():
    """Return ALL_PAIRS grouped by asset type for frontend selectors (excludes JSE)."""

    type_labels = {
        "forex": "Forex",
        "commodity": "Commodities",
        "index": "Indices",
        "stock": "US Stocks",
        "etf": "ETFs",
        "crypto": "Crypto",
    }

    _etf_syms = {p["symbol"] for p in ETF_PAIRS}

    _jse_syms = {p["symbol"] for p in JSE_PAIRS}

    groups = {}

    for p in ALL_PAIRS:
        sym = p["symbol"]

        if sym in _jse_syms:
            continue  # JSE excluded from backtest selector (data quality)

        t = p.get("type", "other")

        label = "ETFs" if sym in _etf_syms else type_labels.get(t, t.title())

        if label not in groups:
            groups[label] = []

        groups[label].append(
            {"sym": sym, "label": p["display"], "enabled": p.get("enabled", True)}
        )

    bt_total = len(ALL_PAIRS) - len(JSE_PAIRS)

    return jsonify({"groups": groups, "total": bt_total, "active": len(ACTIVE_PAIRS)})

def api_intermarket_matrix():
    """Inspect the current H4 intermarket relationship matrix."""

    try:
        from intermarket import build_public_matrix_payload, build_scan_snapshot

        asset_filter = request.args.get("asset_filter") or request.args.get(
            "assetClassFilter"
        )
        try:
            limit = max(1, min(int(request.args.get("limit", 40)), 200))
        except (TypeError, ValueError):
            limit = 40

        snapshot = build_scan_snapshot(
            ALL_PAIRS,
            disabled_pairs=_disabled_pairs,
            etf_pairs=ETF_PAIRS,
            fetch_candles=fetch_candles,
            config=CONFIG,
        )
        payload = build_public_matrix_payload(
            snapshot,
            asset_class_filter=asset_filter,
            limit=limit,
        )
        return jsonify(_json_safe(payload))
    except Exception as e:
        log.error(f"api_intermarket_matrix error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _chart_symbol_key(value) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().upper()
    if not raw:
        return ""
    without_provider = raw.split(":")[-1]
    without_yahoo_fx_suffix = without_provider.replace("=X", "")
    return re.sub(r"[^A-Z0-9]", "", without_yahoo_fx_suffix)


def _find_chart_pair(symbol: str):
    exact = next(
        (p for p in ALL_PAIRS if p.get("symbol") == symbol or p.get("display") == symbol),
        None,
    )
    if exact:
        return exact

    requested_key = _chart_symbol_key(symbol)
    if not requested_key:
        return None
    return next(
        (
            p
            for p in ALL_PAIRS
            if requested_key
            in {
                _chart_symbol_key(p.get("symbol")),
                _chart_symbol_key(p.get("display")),
            }
        ),
        None,
    )


def _overlay_zone(value):
    if not isinstance(value, dict):
        return None
    lower = _safe_float(value.get("lower"))
    upper = _safe_float(value.get("upper"))
    if lower is None or upper is None:
        return None
    out = {
        "lower": lower,
        "upper": upper,
        "center": _safe_float(value.get("center"), (lower + upper) / 2.0),
    }
    for key in ("volume_strength", "fvg_overlap", "type", "source", "created_at", "mitigated"):
        if key in value:
            out[key] = value.get(key)
    return out


def _overlay_zone_pair(value):
    if not isinstance(value, dict):
        return None
    top = _safe_float(value.get("top"))
    bottom = _safe_float(value.get("bottom"))
    if top is None or bottom is None:
        return None
    return top, bottom


def _overlay_order_block(value):
    pair = _overlay_zone_pair(value)
    if pair is None:
        return None
    top, bottom = pair
    out = {
        "type": str(value.get("type") or "unknown"),
        "top": top,
        "bottom": bottom,
        "strength": _safe_float(value.get("strength"), 0.0),
        "mitigated": bool(value.get("mitigated", False)),
    }
    for key in ("created_at", "mitigated_at", "scan_count", "structure_tf"):
        if key in value:
            out[key] = value.get(key)
    return out


def _overlay_fvg(value):
    pair = _overlay_zone_pair(value)
    if pair is None:
        return None
    top, bottom = pair
    out = {
        "type": str(value.get("type") or "unknown"),
        "top": top,
        "bottom": bottom,
        "size": _safe_float(value.get("size"), abs(top - bottom)),
        "mitigated": bool(value.get("mitigated", False)),
    }
    for key in ("bar_index", "created_at", "mitigated_at", "scan_count"):
        if key in value:
            out[key] = value.get(key)
    return out


_OVERLAY_PRECISION_BY_GROUP = {
    "forex_majors": 5,
    "forex_crosses": 5,
    "forex_exotics": 5,
    "forex_other": 5,
    "crypto_btc": 1,
    "crypto_eth": 2,
    "crypto_doge": 6,
    "crypto_alt_majors": 4,
    "crypto_other": 4,
    "precious_trackers": 2,
    "pgm_metals": 2,
    "base_metals": 4,
    "copper": 4,
    "energy_oil": 2,
    "nat_gas": 3,
    "softs": 2,
    "commodity_other": 3,
    "us_indices_trackers": 2,
    "eu_indices": 2,
    "asian_indices": 1,
    "index_other": 2,
    "us_stock_single": 2,
    "bond_tlt": 2,
    "smallcap_em_etf": 2,
    "stock_other": 2,
    "unknown": 4,
}


def _overlay_precision_for(pair: dict | None) -> dict:
    """Resolve chart display precision by score_group. JPY forex -> 3."""
    pair = pair if isinstance(pair, dict) else {}
    try:
        from scoring import get_pair_score_group

        group = get_pair_score_group(pair)
    except Exception:
        group = "unknown"
    display = str(pair.get("display", "") or "")
    if pair.get("type") == "forex" and "JPY" in display.upper():
        precision = 3
    else:
        precision = _OVERLAY_PRECISION_BY_GROUP.get(group, 4)
    return {
        "score_group": group,
        "asset_type": str(pair.get("type", "") or ""),
        "precision": precision,
        "min_move": float(10 ** -precision),
    }


def _normalize_engine_b_overlay_payload(
    raw: dict | None,
    *,
    symbol: str,
    timeframe: str,
    direction: str,
    style: str,
    pair: dict | None = None,
) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    order_blocks = [
        item
        for item in (_overlay_order_block(ob) for ob in raw.get("order_blocks", []) or [])
        if item and not item.get("mitigated")
    ][:2]
    active_fvgs = [
        item
        for item in (_overlay_fvg(fvg) for fvg in raw.get("active_fvgs", []) or [])
        if item and not item.get("mitigated")
    ][:2]
    support_zone = _overlay_zone(raw.get("nearest_support_zone"))
    resistance_zone = _overlay_zone(raw.get("nearest_resistance_zone"))
    has_overlay = any(
        [
            support_zone,
            resistance_zone,
            raw.get("bos_data"),
            raw.get("choch_data"),
            order_blocks,
            active_fvgs,
            raw.get("breaker_block"),
        ]
    )
    warnings: list[str] = []
    if not has_overlay:
        warnings.append("engine_b_overlays_missing")

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "style": style,
        "confirmed_only": not bool(CONFIG.get("ENGINE_B_USE_FORMING_FOR_STRUCTURE", False)),
        "overlay_source": "engine_b",
        "overlay_version": "engine_b_legacy_v1",
        "price_precision": _overlay_precision_for(pair),
        "warnings": warnings,
        "nearest_support_zone": support_zone,
        "nearest_resistance_zone": resistance_zone,
        "bos_data": raw.get("bos_data") if isinstance(raw.get("bos_data"), dict) else {},
        "choch_data": raw.get("choch_data") if isinstance(raw.get("choch_data"), dict) else {},
        "order_blocks": order_blocks,
        "active_fvgs": active_fvgs,
        "breaker_block": raw.get("breaker_block") if isinstance(raw.get("breaker_block"), dict) else None,
        "current_swing_sequence": raw.get("current_swing_sequence"),
        "macro_swing_sequence": raw.get("macro_swing_sequence"),
        "bos_confirmed": bool(raw.get("bos_confirmed", False)),
        "choch_confirmed": bool(raw.get("choch_confirmed", False)),
        "liquidity_sweep": bool(raw.get("liquidity_sweep", False)),
        "structural_verdict": raw.get("structural_verdict"),
        "overlay_limits": {
            "order_blocks": 2,
            "active_fvgs": 2,
            "support_resistance_zones": 2,
            "mitigated_hidden": True,
        },
    }


def api_engine_b_overlays():
    """Return legacy Engine B chart overlay fields normalized for native charts."""
    symbol = request.args.get("symbol")
    tf = str(request.args.get("tf") or request.args.get("timeframe") or "H4").upper()
    direction = str(request.args.get("direction") or "LONG").upper()
    style = str(request.args.get("style") or "auto").lower()
    if direction not in {"LONG", "SHORT"}:
        direction = "LONG"

    if not symbol:
        return jsonify({"error": "Missing symbol parameter"}), 400

    pair = _find_chart_pair(symbol)
    if not pair:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 404

    if not callable(_compute_naked_analysis):
        payload = _normalize_engine_b_overlay_payload(
            None,
            symbol=symbol,
            timeframe=tf,
            direction=direction,
            style=style,
            pair=pair,
        )
        payload["warnings"].append("engine_b_overlay_source_unavailable")
        return jsonify(_json_safe(payload)), 503

    signal = {
        "symbol": pair.get("symbol") or symbol,
        "pair": pair.get("display") or symbol,
        "display": pair.get("display") or symbol,
        "type": pair.get("type"),
        "direction": direction,
        "style": style,
    }
    raw, _pair_obj, err = _compute_naked_analysis(
        signal,
        force_ai=False,
        overlay_only=True,
    )
    payload = _normalize_engine_b_overlay_payload(
        raw if isinstance(raw, dict) else None,
        symbol=symbol,
        timeframe=tf,
        direction=direction,
        style=style,
        pair=pair,
    )
    if err:
        payload["warnings"].append(str(err))
    return jsonify(_json_safe(payload))


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def _tf_seconds(tf: str) -> int | None:
    return {
        "M1": 60,
        "M5": 5 * 60,
        "M15": 15 * 60,
        "M30": 30 * 60,
        "H1": 60 * 60,
        "H4": 4 * 60 * 60,
        "D1": 24 * 60 * 60,
        "W1": 7 * 24 * 60 * 60,
    }.get(str(tf or "").upper())


def _execution_provider_for_pair(pair: dict) -> str:
    asset_group = str(pair.get("type") or "").lower()
    if asset_group == "crypto":
        explicit = CONFIG.get("CRYPTO_EXECUTION_PROVIDER")
        if explicit:
            return str(explicit).strip().lower()
        by_class = CONFIG.get("EXECUTION_PROVIDER_BY_ASSET")
        if isinstance(by_class, dict) and by_class.get("crypto"):
            return str(by_class.get("crypto")).strip().lower()
        return "bybit"
    return str(pair.get("source") or "").strip().lower()


def _bybit_category_for_pair(pair: dict) -> str:
    return str(
        pair.get("bybit_category")
        or CONFIG.get("CRYPTO_CHART_BYBIT_CATEGORY")
        or "linear"
    ).strip().lower()


def _bybit_symbol_for_pair(pair: dict) -> str:
    return str(pair.get("bybit_symbol") or pair.get("symbol") or pair.get("display") or "").replace("/", "").upper()


def _fallback_to_binance_allowed() -> bool:
    query_value = (request.args.get("allowFallback") or "").strip().lower()
    if query_value in {"1", "true", "yes", "y"}:
        return True
    return bool(CONFIG.get("CRYPTO_CHART_BYBIT_FALLBACK_ENABLED", False))


def _iso_timestamp(candle: dict):
    raw = candle.get("time", candle.get("datetime", ""))
    if not raw and candle.get("open_time") is not None:
        open_ms = _safe_float(candle.get("open_time"))
        if open_ms is not None:
            raw = datetime.fromtimestamp(open_ms / 1000.0, timezone.utc).isoformat()
    if isinstance(raw, str):
        ts = raw.strip().replace(" ", "T")
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$", ts):
            ts += "Z"
        return ts
    return raw


def _infer_confirmed(candle: dict, tf: str, now_s: float) -> bool | None:
    raw = candle.get("confirmed", candle.get("closed"))
    if raw is not None:
        return bool(raw)
    open_ms = _safe_float(candle.get("open_time"))
    tf_s = _tf_seconds(tf)
    if open_ms is None or not tf_s:
        return None
    return (open_ms / 1000.0) + tf_s <= now_s


def _volume_ma(values: list[float], period: int = 20) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        out.append(sum(window) / period)
    return out


def _vwap_values(rows: list[dict]) -> tuple[list[float | None], str]:
    use_turnover = any(_safe_float(row.get("turnover"), 0.0) > 0 for row in rows)
    cum_value = 0.0
    cum_volume = 0.0
    out: list[float | None] = []
    for row in rows:
        volume = _safe_float(row.get("v"), 0.0) or 0.0
        if volume <= 0:
            out.append(None if cum_volume <= 0 else cum_value / cum_volume)
            continue
        turnover = _safe_float(row.get("turnover"), 0.0) or 0.0
        if use_turnover and turnover > 0:
            cum_value += turnover
        else:
            typical = (row["h"] + row["l"] + row["c"]) / 3.0
            cum_value += typical * volume
        cum_volume += volume
        out.append(cum_value / cum_volume if cum_volume > 0 else None)
    formula = (
        "cumulative_turnover_divided_by_cumulative_volume"
        if use_turnover
        else "cumulative_typical_price_times_volume_divided_by_cumulative_volume"
    )
    return out, formula


def _format_chart_candles(
    candles: list[dict],
    *,
    tf: str,
    provider: str | None = None,
    category: str | None = None,
    bybit_symbol: str | None = None,
    include_indicators: bool = False,
) -> tuple[list[dict], dict]:
    now_s = time.time()
    rows: list[dict] = []
    for c in candles:
        volume = float(c.get("vol", c.get("volume", 0)) or 0)
        row = {
            "t": _iso_timestamp(c),
            "o": float(c.get("open", 0)),
            "h": float(c.get("high", 0)),
            "l": float(c.get("low", 0)),
            "c": float(c.get("close", 0)),
            "v": volume,
            "volume": volume,
        }
        if provider:
            row.update(
                {
                    "provider": provider,
                    "category": category,
                    "symbol": bybit_symbol or c.get("symbol"),
                    "open_time": c.get("open_time"),
                    "turnover": float(c.get("turnover", 0) or 0),
                    "confirmed": _infer_confirmed(c, tf, now_s),
                    "closed": _infer_confirmed(c, tf, now_s),
                }
            )
        rows.append(row)

    meta = {"vwap_formula": None}
    if include_indicators and rows:
        try:
            from indicators import calc_adx, calc_atr, calc_ema, calc_rsi

            highs = [row["h"] for row in rows]
            lows = [row["l"] for row in rows]
            closes = [row["c"] for row in rows]
            volumes = [row["v"] for row in rows]
            ema21 = calc_ema(closes, 21)
            ema50 = calc_ema(closes, 50)
            ema200 = calc_ema(closes, 200)
            rsi14 = calc_rsi(closes, 14)
            atr14 = calc_atr(highs, lows, closes, 14)
            adx = calc_adx(highs, lows, closes, 14).get("adx", [])
            vol_ma = _volume_ma(volumes, 20)
            vwap, formula = _vwap_values(rows)
            meta["vwap_formula"] = formula
            for idx, row in enumerate(rows):
                row["ema21"] = ema21[idx] if idx < len(ema21) else None
                row["ema50"] = ema50[idx] if idx < len(ema50) else None
                row["ema200"] = ema200[idx] if idx < len(ema200) else None
                row["rsi14"] = rsi14[idx] if idx < len(rsi14) else None
                row["adx14"] = adx[idx] if idx < len(adx) else None
                row["atr14"] = atr14[idx] if idx < len(atr14) else None
                row["volume_ma"] = vol_ma[idx] if idx < len(vol_ma) else None
                row["vwap"] = vwap[idx] if idx < len(vwap) else None
        except Exception as exc:
            meta["indicator_error"] = str(exc)
    return rows, meta


def _normalize_bybit_tick(
    raw: dict | None,
    *,
    symbol: str,
    category: str,
    source: str | None = None,
) -> dict | None:
    if not isinstance(raw, dict):
        return None
    price = _safe_float(raw.get("price") or raw.get("lastPrice") or raw.get("last"))
    if price is None or price <= 0:
        return None
    ts_raw = raw.get("ts", raw.get("timestamp"))
    ts = _safe_float(ts_raw)
    if ts is not None and ts > 1e12:
        ts /= 1000.0
    if ts is None or ts <= 0:
        ts = time.time()
    tick_source = str(source or raw.get("source") or "bybit_rest")
    return {
        "symbol": str(raw.get("symbol") or symbol).replace("/", "").upper(),
        "price": price,
        "bid": _safe_float(raw.get("bid")),
        "ask": _safe_float(raw.get("ask")),
        "timestamp": ts,
        "provider": tick_source,
        "source": tick_source,
        "category": str(raw.get("category") or category),
        "bybit_symbol": str(raw.get("bybit_symbol") or symbol).replace("/", "").upper(),
        "bybit_category": str(raw.get("bybit_category") or category),
        "age_seconds": round(max(0.0, time.time() - ts), 3),
    }


def _bybit_live_tick_diagnostics(
    *,
    live_tick: dict | None,
    live_tick_meta: dict,
    bybit_symbol: str,
    category: str,
) -> dict:
    tick_source = live_tick.get("source") if live_tick else None
    provider = tick_source or "bybit_rest"
    fallback_used = bool(live_tick_meta.get("fallback_used"))
    return {
        "live_tick_provider": provider,
        "live_tick_source": provider,
        "live_tick_age_seconds": live_tick.get("age_seconds") if live_tick else None,
        "websocket_active": bool(live_tick_meta.get("websocket_active")),
        "websocket_stale": bool(live_tick_meta.get("websocket_stale")),
        "websocket_last_message_ts": live_tick_meta.get("websocket_last_message_ts"),
        "fallback_used": fallback_used,
        "fallback_reason": live_tick_meta.get("fallback_reason"),
        "bybit_symbol": bybit_symbol,
        "bybit_category": category,
    }


def _fetch_bybit_chart_tick(
    pair: dict,
    *,
    bybit_symbol: str,
    category: str,
    display: str,
) -> tuple[dict | None, dict]:
    """WS-first live tick for crypto chart; REST only when configured fallback applies."""
    from candle_feeds import get_bybit_live_tick

    meta: dict = {
        "fallback_used": False,
        "fallback_reason": None,
        "websocket_active": False,
        "websocket_stale": True,
        "websocket_last_message_ts": None,
    }

    ws_enabled = bool(CONFIG.get("BYBIT_WS_ENABLED", True))
    rest_enabled = bool(CONFIG.get("BYBIT_REST_FALLBACK_ENABLED", True))
    preference = str(CONFIG.get("CRYPTO_LIVE_TICK_PROVIDER_PREFERENCE") or "bybit_ws").strip().lower()

    if ws_enabled and preference == "bybit_ws":
        ws_result = get_bybit_live_tick(
            display,
            bybit_symbol=bybit_symbol,
            category=category,
        )
        meta.update(
            {
                "websocket_active": ws_result.get("websocket_active"),
                "websocket_stale": ws_result.get("websocket_stale"),
                "websocket_last_message_ts": ws_result.get("websocket_last_message_ts"),
            }
        )
        ws_tick = ws_result.get("tick")
        if ws_tick:
            normalized = _normalize_bybit_tick(
                ws_tick,
                symbol=bybit_symbol,
                category=category,
                source="bybit_ws",
            )
            if normalized:
                meta["fallback_reason"] = None
                return normalized, meta

        ws_reason = ws_result.get("fallback_reason") or "ws_unavailable"
    else:
        ws_reason = "ws_disabled" if not ws_enabled else "ws_preference_not_bybit_ws"
        meta["websocket_stale"] = True

    if not rest_enabled:
        meta["fallback_used"] = True
        meta["fallback_reason"] = ws_reason if ws_enabled else "rest_fallback_disabled"
        return None, meta

    if not callable(fetch_bybit_ticker):
        meta["fallback_used"] = True
        meta["fallback_reason"] = "rest_fetch_unavailable"
        return None, meta

    try:
        raw = fetch_bybit_ticker(bybit_symbol, category=category)
    except TypeError:
        raw = fetch_bybit_ticker(bybit_symbol)
    except Exception as exc:
        meta["fallback_used"] = True
        meta["fallback_reason"] = f"rest_fetch_failed:{exc.__class__.__name__}"
        return None, meta

    if not raw:
        meta["fallback_used"] = True
        meta["fallback_reason"] = ws_reason if ws_enabled else "rest_fetch_empty"
        return None, meta

    normalized = _normalize_bybit_tick(
        raw,
        symbol=bybit_symbol,
        category=category,
        source="bybit_rest",
    )
    if not normalized:
        meta["fallback_used"] = True
        meta["fallback_reason"] = "rest_tick_invalid"
        return None, meta

    meta["fallback_used"] = True
    meta["fallback_reason"] = ws_reason
    if CONFIG.get("BYBIT_REST_FALLBACK_WARN", True):
        _warn = getattr(log, "warning", None) or getattr(log, "error", None)
        if _warn:
            _warn(
                "[BYBIT-TICK] REST fallback for %s (%s): %s",
                display,
                bybit_symbol,
                ws_reason,
            )
    return normalized, meta


def _generic_chart_provider(pair: dict, chart_source: str) -> str:
    source = str(pair.get("source") or chart_source or "unknown").lower()
    if chart_source.startswith("eodhd"):
        return "eodhd"
    if chart_source.startswith("shared") and source:
        return source
    return source or str(chart_source or "unknown").lower()


def _generic_candle_policy(pair: dict, chart_source: str) -> str:
    ptype = str(pair.get("type") or "").lower()
    source = str(pair.get("source") or "").lower()
    if ptype == "forex" and source == "mt5":
        return "confirmed-only"
    if "+ws" in str(chart_source or "").lower():
        return "confirmed+forming-ws"
    if ptype == "forex":
        return "confirmed-only"
    return "provider-default"


def _crypto_scoring_provider() -> str:
    from athena_app.services.crypto_signal_feed import resolve_crypto_signal_feed

    return resolve_crypto_signal_feed("A", CONFIG)


def _apply_scoring_provider_mismatch(
    *,
    chart_provider: str,
    scoring_provider: str,
    provider_mismatch: bool,
    mismatch_reason: str | None,
) -> tuple[bool, str | None]:
    if scoring_provider == chart_provider:
        return provider_mismatch, mismatch_reason
    if provider_mismatch and mismatch_reason:
        return True, mismatch_reason
    return True, f"scoring_feed_{scoring_provider}_chart_feed_{chart_provider}"


def _crypto_chart_payload(pair: dict, symbol: str, tf: str, limit: int):
    execution_provider = _execution_provider_for_pair(pair)
    if execution_provider != "bybit":
        return None

    bybit_symbol = _bybit_symbol_for_pair(pair)
    category = _bybit_category_for_pair(pair)
    scoring_provider = _crypto_scoring_provider()
    fallback_chain = ["bybit"]
    chart_provider = "bybit"
    fallback_used = False
    provider_mismatch = False
    mismatch_reason = None
    chart_status = "execution_grade"

    candles = fetch_bybit_klines(bybit_symbol, tf, limit) if callable(fetch_bybit_klines) else None
    if not candles:
        if not _fallback_to_binance_allowed():
            return {
                "error": f"No Bybit candle data for {symbol} {tf}",
                "asset_group": "crypto",
                "execution_provider": execution_provider,
                "chart_provider": "bybit",
                "candle_provider": "bybit",
                "fallback_used": False,
                "fallback_chain": fallback_chain,
                "provider_mismatch": False,
                "chart_status": "unavailable",
            }, 503
        candles = fetch_candles(pair, tf, limit)
        chart_provider = "binance" if str(pair.get("source") or "").lower() == "binance" else str(pair.get("source") or "fallback").lower()
        fallback_used = True
        provider_mismatch = True
        mismatch_reason = "crypto_execution_provider_bybit_chart_fell_back_to_binance"
        fallback_chain.append(chart_provider)
        chart_status = "non_execution_grade"

    provider_mismatch, mismatch_reason = _apply_scoring_provider_mismatch(
        chart_provider=chart_provider,
        scoring_provider=scoring_provider,
        provider_mismatch=provider_mismatch,
        mismatch_reason=mismatch_reason,
    )

    if not candles:
        return {"error": f"No candle data for {symbol} {tf}"}, 404

    rows, indicator_meta = _format_chart_candles(
        candles,
        tf=tf,
        provider=chart_provider,
        category=category if chart_provider == "bybit" else None,
        bybit_symbol=bybit_symbol if chart_provider == "bybit" else pair.get("symbol"),
        include_indicators=True,
    )
    display = pair.get("display", symbol)
    live_tick, live_tick_meta = _fetch_bybit_chart_tick(
        pair,
        bybit_symbol=bybit_symbol,
        category=category,
        display=display,
    )
    tick_diag = _bybit_live_tick_diagnostics(
        live_tick=live_tick,
        live_tick_meta=live_tick_meta,
        bybit_symbol=bybit_symbol,
        category=category,
    )
    last_candle_ts = rows[-1].get("t") if rows else None
    payload = {
        "candles": rows,
        "symbol": symbol,
        "display": display,
        "tf": tf,
        "pairType": "crypto",
        "candlesSource": chart_provider,
        "asset_group": "crypto",
        "execution_provider": execution_provider,
        "chart_provider": chart_provider,
        "candle_provider": chart_provider,
        "volume_provider": chart_provider,
        "turnover_provider": chart_provider,
        "vwap_provider": chart_provider,
        "scoring_provider": scoring_provider,
        "atr_provider": chart_provider,
        "atr_timeframe": tf,
        "indicator_provider": chart_provider,
        "indicator_source_candle_provider": chart_provider,
        "provider_mismatch": provider_mismatch,
        "provider_mismatch_reason": mismatch_reason,
        "fallback_chain": fallback_chain,
        "chart_status": chart_status,
        "candle_confirmed_policy": (
            "bybit_rest_interval_close_inferred"
            if chart_provider == "bybit"
            else "fallback_provider_policy"
        ),
        "last_candle_ts": last_candle_ts,
        "liveTick": live_tick,
        **tick_diag,
        "fallback_used": bool(fallback_used or tick_diag.get("fallback_used")),
        "volume_ma_period": 20,
        "vwap_formula": indicator_meta.get("vwap_formula"),
        "indicator_set": ["EMA21", "EMA50", "EMA200", "VWAP", "RSI14", "ADX14", "ATR14", "Volume", "Volume MA"],
        "price_precision": _overlay_precision_for(pair),
    }
    return payload, 200


def api_candles():
    """Return OHLCV candles for the chart widget."""
    symbol = request.args.get("symbol")
    tf = request.args.get("tf", "H4").upper()
    # Binance klines allow up to 1000; extra history lets H4/D1 EMA200 start further left vs TradingView.
    try:
        limit = min(int(request.args.get("limit", 300)), 1000)
    except (TypeError, ValueError):
        limit = 300

    if not symbol:
        return jsonify({"error": "Missing symbol parameter"}), 400

    pair = _find_chart_pair(symbol)
    if not pair:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 404

    # Dashboard chart: use a forex-only aligned candle path so H1/H4/D1 for Vision
    # come from one canonical H1 timeline (EODHD + optional forming WS merge).
    # ?source=live keeps the generic shared fetch path for debugging.
    ptype = pair.get("type") or ""
    pair_source = str(pair.get("source") or "").lower()
    source_q = (request.args.get("source") or "").strip().lower()
    chart_source = "live" if source_q == "live" else "shared"
    candles = None

    if ptype == "crypto":
        crypto_payload = _crypto_chart_payload(pair, symbol, tf, limit)
        if crypto_payload is not None:
            payload, status = crypto_payload
            return jsonify(_json_safe(payload)), status

    if ptype == "forex" and pair_source == "eodhd" and source_q != "live":
        # Fetch one canonical H1 stream and resample deterministically for H4/D1.
        base_limit = {
            "H1": limit,
            "H4": min(max(limit * 4 + 8, 80), 9000),
            "D1": min(max(limit * 24 + 24, 240), 9000),
        }.get(tf, limit)
        h1_series = _extract_candles(fetch_eodhd(pair, "H1", base_limit))
        if h1_series:
            h1_series, ws_note = _merge_forex_forming_ws(
                h1_series, pair.get("display", ""), "H1", base_limit
            )
            candles = _resample_from_h1(
                h1_series,
                tf,
                limit,
                alignment_offset_hours=(
                    _forex_h4_resample_offset_hours() if ptype == "forex" else 0.0
                ),
            )
            if candles:
                chart_source = "eodhd_h1_resampled"
                if ws_note:
                    chart_source += "+ws"

    if not candles:
        candles = fetch_candles(pair, tf, limit)
        # Optional forming-bar merge for forex on generic/shared path.
        if candles and ptype == "forex" and pair_source not in {"polygon", "mt5"}:
            candles, ws_note = _merge_forex_forming_ws(
                candles, pair.get("display", ""), tf, limit
            )
            if ws_note and chart_source == "shared":
                chart_source = "shared+ws"

    if not candles:
        return jsonify({"error": f"No candle data for {symbol} {tf}"}), 404

    result, _indicator_meta = _format_chart_candles(candles, tf=tf)

    provider = _generic_chart_provider(pair, chart_source)
    return jsonify(
        {
            "candles": result,
            "symbol": symbol,
            "display": pair.get("display", symbol),
            "tf": tf,
            "pairType": ptype,
            "candlesSource": chart_source,
            "asset_group": ptype,
            "chart_provider": provider,
            "candle_provider": provider,
            "live_tick_provider": provider,
            "candle_confirmed_policy": _generic_candle_policy(pair, chart_source),
            "last_candle_ts": result[-1].get("t") if result else None,
            "price_precision": _overlay_precision_for(pair),
        }
    )


def api_chart_tick():
    """Return chart-scoped live tick diagnostics without touching shared price feeds."""
    symbol = request.args.get("symbol")
    if not symbol:
        return jsonify({"error": "Missing symbol parameter"}), 400

    pair = _find_chart_pair(symbol)
    if not pair:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 404

    asset_group = str(pair.get("type") or "").lower()
    execution_provider = _execution_provider_for_pair(pair)
    if asset_group == "crypto" and execution_provider == "bybit":
        bybit_symbol = _bybit_symbol_for_pair(pair)
        category = _bybit_category_for_pair(pair)
        display = pair.get("display", symbol)
        tick, tick_meta = _fetch_bybit_chart_tick(
            pair,
            bybit_symbol=bybit_symbol,
            category=category,
            display=display,
        )
        if not tick:
            return jsonify(
                _json_safe(
                    {
                        "error": f"No Bybit live tick for {symbol}",
                        "asset_group": asset_group,
                        "execution_provider": execution_provider,
                        "live_tick_provider": "bybit_rest",
                        "fallback_used": True,
                        "fallback_reason": tick_meta.get("fallback_reason"),
                        "bybit_symbol": bybit_symbol,
                        "bybit_category": category,
                    }
                )
            ), 503
        tick_diag = _bybit_live_tick_diagnostics(
            live_tick=tick,
            live_tick_meta=tick_meta,
            bybit_symbol=bybit_symbol,
            category=category,
        )
        scoring_provider = _crypto_scoring_provider()
        tick_mismatch, tick_mismatch_reason = _apply_scoring_provider_mismatch(
            chart_provider="bybit",
            scoring_provider=scoring_provider,
            provider_mismatch=False,
            mismatch_reason=None,
        )
        return jsonify(
            _json_safe(
                {
                    "symbol": symbol,
                    "display": display,
                    "asset_group": asset_group,
                    "execution_provider": execution_provider,
                    "chart_provider": "bybit",
                    "candle_provider": "bybit",
                    "scoring_provider": scoring_provider,
                    "vwap_provider": "bybit",
                    "indicator_provider": "bybit",
                    "price": tick.get("price"),
                    "bid": tick.get("bid"),
                    "ask": tick.get("ask"),
                    "timestamp": tick.get("timestamp"),
                    "provider": tick.get("provider"),
                    "source": tick.get("source"),
                    "category": tick.get("category"),
                    "age_seconds": tick.get("age_seconds"),
                    "provider_mismatch": tick_mismatch,
                    "provider_mismatch_reason": tick_mismatch_reason,
                    "liveTick": tick,
                    **tick_diag,
                }
            )
        )

    return jsonify({"error": f"No chart tick provider for {symbol}"}), 404


def api_news_sentiment():
    """EODHD news + AI-provider structured sentiment for one pair (display or Yahoo symbol).

    Uses ``EODHD_KEY`` and the configured AI API key. Model: ``NEWS_SENTIMENT_MODEL`` or ``AI_MODEL``.
    """
    from news_sentiment_feed import get_news_sentiment, news_to_confluence_vote

    sym = request.args.get("symbol")
    if not sym and request.method == "POST":
        body = request.get_json(silent=True) or {}
        sym = body.get("symbol") or body.get("pair")
    if not sym:
        return jsonify({"error": "Missing symbol parameter"}), 400

    pair = next(
        (p for p in ALL_PAIRS if p.get("symbol") == sym or p.get("display") == sym),
        None,
    )
    if not pair:
        return jsonify({"error": f"Unknown symbol: {sym}"}), 404

    eod_key = os.environ.get("EODHD_KEY", "").strip()
    if not eod_key:
        return jsonify({"error": "EODHD_KEY not set"}), 503

    ai_key = get_ai_api_key(CONFIG)
    if not ai_key:
        return jsonify({"error": "AI API key not set"}), 500

    price = None
    disp = pair.get("display", "")
    try:
        with _live_prices_lock:
            lp = _live_prices.get(disp)
            if lp:
                price = lp.get("price")
    except Exception:
        pass

    model = get_ai_model(CONFIG, "NEWS_SENTIMENT_MODEL", "grok-4.3")
    result = get_news_sentiment(
        pair,
        eodhd_api_key=eod_key,
        xai_api_key=ai_key,
        eodhd_ticker_for_pair=_eodhd_ticker_for_pair,
        current_price=price,
        model=model,
    )
    if not result:
        return (
            jsonify(
                {
                    "error": "No sentiment result (no news, API error, or parse failure)",
                }
            ),
            502,
        )

    vote = news_to_confluence_vote(result)
    out = {
        "pair": disp,
        "eodhdTicker": _eodhd_ticker_for_pair(pair),
        "structured": result,
        "confluenceVote": vote,
    }
    return jsonify(_json_safe(out))


def register_market_data_routes(app, runtime: SimpleNamespace) -> None:
    """Register market metadata routes using runtime state supplied by athena.py."""
    global CONFIG, ALL_PAIRS, ACTIVE_PAIRS, ETF_PAIRS, JSE_PAIRS
    global _disabled_pairs, _live_prices, _live_prices_lock, log
    global fetch_yield_curve, http_requests, fetch_candles, fetch_bybit_klines, fetch_bybit_ticker, fetch_eodhd
    global _extract_candles, _merge_forex_forming_ws, _resample_from_h1
    global _forex_h4_resample_offset_hours, _eodhd_ticker_for_pair, _compute_naked_analysis, _json_safe

    CONFIG = runtime.CONFIG
    ALL_PAIRS = runtime.ALL_PAIRS
    ACTIVE_PAIRS = runtime.ACTIVE_PAIRS
    ETF_PAIRS = runtime.ETF_PAIRS
    JSE_PAIRS = runtime.JSE_PAIRS
    _disabled_pairs = runtime.disabled_pairs
    _live_prices = runtime.live_prices
    _live_prices_lock = runtime.live_prices_lock
    fetch_yield_curve = runtime.fetch_yield_curve
    http_requests = runtime.http_requests
    fetch_candles = runtime.fetch_candles
    fetch_bybit_klines = getattr(runtime, "fetch_bybit_klines", None)
    fetch_bybit_ticker = getattr(runtime, "fetch_bybit_ticker", None)
    fetch_eodhd = runtime.fetch_eodhd
    _extract_candles = runtime.extract_candles
    _merge_forex_forming_ws = runtime.merge_forex_forming_ws
    _resample_from_h1 = runtime.resample_from_h1
    _forex_h4_resample_offset_hours = runtime.forex_h4_resample_offset_hours
    _eodhd_ticker_for_pair = runtime.eodhd_ticker_for_pair
    _compute_naked_analysis = getattr(runtime, "compute_naked_analysis", None)
    _json_safe = runtime.json_safe
    log = runtime.log

    app.add_url_rule("/api/market-hours", "api_market_hours", api_market_hours)
    app.add_url_rule("/api/prices", "api_prices", api_prices)
    app.add_url_rule("/api/yield-curve", "api_yield_curve", api_yield_curve)
    app.add_url_rule("/api/bulk-prices", "api_bulk_prices", api_bulk_prices)
    app.add_url_rule("/api/pairs", "api_pairs", api_pairs)
    app.add_url_rule(
        "/api/intermarket-matrix",
        "api_intermarket_matrix",
        api_intermarket_matrix,
    )
    app.add_url_rule("/api/candles", "api_candles", api_candles, methods=["GET"])
    app.add_url_rule(
        "/api/engine-b-overlays",
        "api_engine_b_overlays",
        api_engine_b_overlays,
        methods=["GET"],
    )
    app.add_url_rule("/api/chart-tick", "api_chart_tick", api_chart_tick, methods=["GET"])
    app.add_url_rule(
        "/api/news-sentiment",
        "api_news_sentiment",
        api_news_sentiment,
        methods=["GET", "POST"],
    )
