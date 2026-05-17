"""Market metadata API route handlers and registration.

Behavior-neutral extraction from athena.py. These routes are read-only market
metadata/data helpers and do not own scoring, risk, or execution decisions.
"""

from __future__ import annotations

import logging
import os
import re
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
fetch_eodhd = None
_extract_candles = None
_merge_forex_forming_ws = None
_resample_from_h1 = None
_forex_h4_resample_offset_hours = lambda: 0.0
_eodhd_ticker_for_pair = None
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

    return jsonify(
        {
            "prices": snapshot,
            "count": len(snapshot),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

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

    pair = next(
        (p for p in ALL_PAIRS if p.get("symbol") == symbol or p.get("display") == symbol),
        None,
    )
    if not pair:
        return jsonify({"error": f"Unknown symbol: {symbol}"}), 404

    # Dashboard chart: use a forex-only aligned candle path so H1/H4/D1 for Vision
    # come from one canonical H1 timeline (EODHD + optional forming WS merge).
    # ?source=live keeps the generic shared fetch path for debugging.
    ptype = pair.get("type") or ""
    source_q = (request.args.get("source") or "").strip().lower()
    chart_source = "live" if source_q == "live" else "shared"
    candles = None

    if ptype == "forex" and pair.get("source") == "eodhd" and source_q != "live":
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
        if candles and ptype == "forex" and pair.get("source") != "polygon":
            candles, ws_note = _merge_forex_forming_ws(
                candles, pair.get("display", ""), tf, limit
            )
            if ws_note and chart_source == "shared":
                chart_source = "shared+ws"

    if not candles:
        return jsonify({"error": f"No candle data for {symbol} {tf}"}), 404

    _naive_iso_utc = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$"
    )
    result = []
    for c in candles:
        _t = c.get("time", c.get("datetime", ""))
        if isinstance(_t, str):
            _ts = _t.strip().replace(" ", "T")
            if _naive_iso_utc.match(_ts):
                _ts += "Z"
            _t = _ts
        result.append(
            {
                "t": _t,
                "o": float(c.get("open", 0)),
                "h": float(c.get("high", 0)),
                "l": float(c.get("low", 0)),
                "c": float(c.get("close", 0)),
                "v": float(c.get("vol", c.get("volume", 0))),
            }
        )

    return jsonify(
        {
            "candles": result,
            "symbol": symbol,
            "display": pair.get("display", symbol),
            "tf": tf,
            "pairType": ptype,
            "candlesSource": chart_source,
        }
    )


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
    global fetch_yield_curve, http_requests, fetch_candles, fetch_eodhd
    global _extract_candles, _merge_forex_forming_ws, _resample_from_h1
    global _forex_h4_resample_offset_hours, _eodhd_ticker_for_pair, _json_safe

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
    fetch_eodhd = runtime.fetch_eodhd
    _extract_candles = runtime.extract_candles
    _merge_forex_forming_ws = runtime.merge_forex_forming_ws
    _resample_from_h1 = runtime.resample_from_h1
    _forex_h4_resample_offset_hours = runtime.forex_h4_resample_offset_hours
    _eodhd_ticker_for_pair = runtime.eodhd_ticker_for_pair
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
        "/api/news-sentiment",
        "api_news_sentiment",
        api_news_sentiment,
        methods=["GET", "POST"],
    )
