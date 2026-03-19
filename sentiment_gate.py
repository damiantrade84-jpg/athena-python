"""sentiment_gate.py — EODHD-powered news sentiment filter for Sentinel Pro.

Checks financial news sentiment for a trading pair before auto-execution.
Uses the EODHD `financial_news()` endpoint to get recent headlines with
sentiment scores, then aggregates to a -1.0 to +1.0 signal.

If aggregate sentiment strongly opposes the signal direction, the trade
is blocked. If it aligns, a boost note is attached.

Caches results for 30 minutes per symbol to limit API calls.
"""

import logging
import os
import time
from datetime import datetime, timedelta

log = logging.getLogger("sentinel.sentiment_gate")

# In-memory cache: { "AAPL.US": { "score": 0.42, "count": 5, "ts": time.time() } }
_cache: dict = {}
_CACHE_TTL = 1800  # 30 minutes


def _get_eodhd_ticker(pair: str, asset_type: str) -> str | None:
    """Convert internal pair symbol to EODHD news ticker format."""
    if asset_type == "crypto":
        # Crypto: BTCUSD -> BTC-USD.CC
        base = pair.replace("USDT", "").replace("USD", "").replace("/", "")
        return f"{base}-USD.CC"
    elif asset_type == "forex":
        # Forex: EURUSD=X -> EURUSD.FOREX
        clean = pair.replace("=X", "").replace("/", "")
        return f"{clean}.FOREX"
    elif asset_type in ("stock", "index"):
        # Stocks: AAPL -> AAPL.US
        clean = pair.replace(".US", "")
        return f"{clean}.US"
    elif asset_type == "commodity":
        # Commodities: GC=F -> GC.COMEX  (gold)
        mapping = {
            "GC=F": "GC.COMEX",
            "SI=F": "SI.COMEX",
            "CL=F": "CL.COMEX",
            "BZ=F": "BZ.COMEX",
            "NG.US": "NG.COMEX",
            "PL=F": "PL.COMEX",
            "PA=F": "PA.COMEX",
            "HG=F": "HG.COMEX",
        }
        return mapping.get(pair, None)
    return None


def check_sentiment(pair: str, direction: str, asset_type: str) -> dict:
    """Check news sentiment for a trading signal.

    Returns:
        {
            "allowed": bool,
            "score": float (-1 to 1),
            "count": int (number of articles),
            "reason": str (human-readable)
        }
    """
    cache_key = f"{pair}_{direction}"

    # Check cache
    if cache_key in _cache:
        entry = _cache[cache_key]
        if time.time() - entry["ts"] < _CACHE_TTL:
            return entry["result"]

    try:
        from eodhd import APIClient

        api_key = os.environ.get("EODHD_KEY", "")
        if not api_key:
            return {
                "allowed": True,
                "score": 0.0,
                "count": 0,
                "reason": "No EODHD_KEY — sentiment gate skipped",
            }

        ticker = _get_eodhd_ticker(pair, asset_type)
        if not ticker:
            return {
                "allowed": True,
                "score": 0.0,
                "count": 0,
                "reason": f"No ticker mapping for {pair}",
            }

        api = APIClient(api_key)

        # Get news from the last 3 days
        date_to = datetime.utcnow().strftime("%Y-%m-%d")
        date_from = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")

        try:
            news = api.financial_news(
                s=ticker, from_date=date_from, to_date=date_to, limit="20"
            )
        except Exception as e:
            log.warning(f"[SENTIMENT] News API error for {ticker}: {e}")
            return {
                "allowed": True,
                "score": 0.0,
                "count": 0,
                "reason": f"API error: {e}",
            }

        if not news or not isinstance(news, list):
            return {
                "allowed": True,
                "score": 0.0,
                "count": 0,
                "reason": "No news articles found",
            }

        # Extract sentiment scores from articles
        sentiments = []
        for article in news:
            sent = article.get("sentiment", {})
            if isinstance(sent, dict):
                polarity = sent.get("polarity", 0)
                if isinstance(polarity, (int, float)):
                    sentiments.append(polarity)

        if not sentiments:
            result = {
                "allowed": True,
                "score": 0.0,
                "count": len(news),
                "reason": f"{len(news)} articles, no sentiment scores",
            }
        else:
            avg_score = sum(sentiments) / len(sentiments)
            count = len(sentiments)

            # Decision logic:
            # If direction is LONG and sentiment is very bearish (< -0.4): BLOCK
            # If direction is SHORT and sentiment is very bullish (> 0.4): BLOCK
            # Otherwise: ALLOW
            blocked = False
            reason = ""

            if direction == "LONG" and avg_score < -0.4:
                blocked = True
                reason = (
                    f"SENTIMENT BLOCK: strongly bearish ({avg_score:.2f}) opposes LONG"
                )
            elif direction == "SHORT" and avg_score > 0.4:
                blocked = True
                reason = (
                    f"SENTIMENT BLOCK: strongly bullish ({avg_score:.2f}) opposes SHORT"
                )
            elif direction == "LONG" and avg_score > 0.3:
                reason = f"Sentiment aligned: bullish ({avg_score:.2f}) supports LONG"
            elif direction == "SHORT" and avg_score < -0.3:
                reason = f"Sentiment aligned: bearish ({avg_score:.2f}) supports SHORT"
            else:
                reason = f"Sentiment neutral ({avg_score:.2f}), {count} articles"

            result = {
                "allowed": not blocked,
                "score": round(avg_score, 3),
                "count": count,
                "reason": reason,
            }

        # Cache the result
        _cache[cache_key] = {"result": result, "ts": time.time()}

        log.info(f"[SENTIMENT] {pair} {direction}: {result['reason']}")
        return result

    except ImportError:
        log.warning("[SENTIMENT] eodhd library not installed — gate skipped")
        return {
            "allowed": True,
            "score": 0.0,
            "count": 0,
            "reason": "eodhd library not available",
        }
    except Exception as e:
        log.error(f"[SENTIMENT] Unexpected error: {e}")
        return {"allowed": True, "score": 0.0, "count": 0, "reason": f"Error: {e}"}


def inject_external_sentiment(
    pair: str, score: float, source: str = "external"
) -> None:
    """Inject external sentiment score (e.g., from LunarCrush) into the cache.

    Args:
        pair: Display name e.g. "BTC/USDT"
        score: Sentiment score (-1.0 to +1.0), positive = bullish
        source: Label for the data source
    """
    global _cache
    # Store for both LONG and SHORT directions
    for direction in ("LONG", "SHORT"):
        cache_key = f"{pair}_{direction}"
        is_aligned = (score > 0 and direction == "LONG") or (
            score < 0 and direction == "SHORT"
        )
        threshold = 0.4  # Same as SENTIMENT_BLOCK_THRESHOLD
        allowed = is_aligned or abs(score) < threshold
        _cache[cache_key] = {
            "ts": time.time(),
            "result": {
                "allowed": allowed,
                "score": score,
                "count": 1,
                "source": source,
                "reason": f"{source} sentiment {'aligned' if is_aligned else 'opposing'}: {score:+.2f}",
            },
        }
    log.info(f"[SENTIMENT] Injected {source} score for {pair}: {score:+.2f}")
