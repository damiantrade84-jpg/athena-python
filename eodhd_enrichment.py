"""eodhd_enrichment.py — Insider Trading + Fundamentals enrichment for Sentinel Pro.

For stock-type signals, enriches the signal with:
1. Recent insider transactions (SEC Form 4) from EODHD
2. Key fundamental metrics from EODHD

This data is added to the signal dict for display in the UI and
for use by the signal debate system and AI analysis prompts.

Does NOT block trades — purely informational enrichment.
"""
import logging
import os
import time
from datetime import datetime, timedelta

log = logging.getLogger("sentinel.enrichment")

# Cache: { "AAPL.US_insider": { "data": {...}, "ts": time.time() } }
_cache: dict = {}
_CACHE_TTL = 86400  # 24 hours (insider + fundamentals don't change often)


def enrich_signal(signal: dict) -> dict:
    """Enrich a stock signal with insider trading + fundamental data.

    Only applies to stock-type signals. Returns the signal dict
    with added "_insider" and "_fundamentals" keys.
    """
    asset_type = signal.get("type", "")
    if asset_type not in ("stock", "index"):
        return signal  # Only enrich stock signals

    pair = signal.get("pair", "")
    if not pair:
        return signal

    api_key = os.environ.get("EODHD_KEY", "")
    if not api_key:
        return signal

    try:
        from eodhd import APIClient
        api = APIClient(api_key)

        # Map pair to EODHD ticker
        ticker = pair.replace(".US", "") + ".US"

        # 1. Insider Trading
        insider = _get_insider_data(api, ticker)
        if insider:
            signal["_insider"] = insider

        # 2. Fundamentals
        fundamentals = _get_fundamentals_data(api, ticker)
        if fundamentals:
            signal["_fundamentals"] = fundamentals

    except ImportError:
        log.debug("[ENRICH] eodhd library not available")
    except Exception as e:
        log.warning(f"[ENRICH] Error enriching {pair}: {e}")

    return signal


def _get_insider_data(api, ticker: str) -> dict | None:
    """Get recent insider transactions from EODHD."""
    cache_key = f"{ticker}_insider"
    if cache_key in _cache and time.time() - _cache[cache_key]["ts"] < _CACHE_TTL:
        return _cache[cache_key]["data"]

    try:
        date_from = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
        transactions = api.get_insider_transactions_data(
            code=ticker,
            date_from=date_from,
            limit="20"
        )

        if not isinstance(transactions, list) or not transactions:
            return None

        # Analyze insider activity
        buys = [t for t in transactions if str(t.get("transactionType", "")).lower() in ("buy", "purchase", "p-purchase")]
        sells = [t for t in transactions if str(t.get("transactionType", "")).lower() in ("sell", "sale", "s-sale")]

        total_buy_value = sum(float(t.get("transactionAmount", 0) or 0) for t in buys)
        total_sell_value = sum(float(t.get("transactionAmount", 0) or 0) for t in sells)

        # Net sentiment: positive = more buying, negative = more selling
        net_sentiment = "BULLISH" if total_buy_value > total_sell_value * 1.5 else (
            "BEARISH" if total_sell_value > total_buy_value * 1.5 else "NEUTRAL"
        )

        result = {
            "total_transactions": len(transactions),
            "buys": len(buys),
            "sells": len(sells),
            "buy_value": round(total_buy_value, 0),
            "sell_value": round(total_sell_value, 0),
            "net_sentiment": net_sentiment,
            "recent": [
                {
                    "name": t.get("officerTitle", t.get("reportedBy", "?")),
                    "type": t.get("transactionType", "?"),
                    "shares": t.get("transactionShares", 0),
                    "value": t.get("transactionAmount", 0),
                    "date": t.get("date", ""),
                }
                for t in transactions[:5]  # Last 5 transactions
            ]
        }

        _cache[cache_key] = {"data": result, "ts": time.time()}
        log.info(f"[INSIDER] {ticker}: {result['net_sentiment']} ({result['buys']} buys, {result['sells']} sells)")
        return result

    except Exception as e:
        log.debug(f"[INSIDER] Error for {ticker}: {e}")
        return None


def _get_fundamentals_data(api, ticker: str) -> dict | None:
    """Get key fundamental metrics from EODHD."""
    cache_key = f"{ticker}_fundamentals"
    if cache_key in _cache and time.time() - _cache[cache_key]["ts"] < _CACHE_TTL:
        return _cache[cache_key]["data"]

    try:
        data = api.get_fundamentals_data(ticker=ticker)

        if not isinstance(data, dict):
            return None

        # Extract key metrics
        highlights = data.get("Highlights", {})
        valuation = data.get("Valuation", {})
        technicals = data.get("Technicals", {})

        result = {
            "market_cap": highlights.get("MarketCapitalization"),
            "pe_ratio": highlights.get("PERatio"),
            "eps": highlights.get("EarningsShare"),
            "dividend_yield": highlights.get("DividendYield"),
            "revenue_growth_yoy": highlights.get("QuarterlyRevenueGrowthYOY"),
            "earnings_growth_yoy": highlights.get("QuarterlyEarningsGrowthYOY"),
            "profit_margin": highlights.get("ProfitMargin"),
            "beta": technicals.get("Beta"),
            "52wk_high": technicals.get("52WeekHigh"),
            "52wk_low": technicals.get("52WeekLow"),
            "short_ratio": technicals.get("ShortRatio"),
            "enterprise_value": valuation.get("EnterpriseValue"),
            "forward_pe": valuation.get("ForwardPE"),
        }

        # Remove None values
        result = {k: v for k, v in result.items() if v is not None}

        if result:
            _cache[cache_key] = {"data": result, "ts": time.time()}
            log.info(f"[FUNDAMENTALS] {ticker}: PE={result.get('pe_ratio','?')}, "
                     f"EPS={result.get('eps','?')}, Beta={result.get('beta','?')}")

        return result if result else None

    except Exception as e:
        log.debug(f"[FUNDAMENTALS] Error for {ticker}: {e}")
        return None
