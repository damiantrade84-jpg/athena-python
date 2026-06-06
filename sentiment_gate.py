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
from datetime import datetime, timedelta, timezone

from eodhd_ticker_resolver import eodhd_ticker_from_signal
from news_sentiment_feed import _parse_article_dt

log = logging.getLogger("sentinel.sentiment_gate")

# In-memory cache: { "AAPL.US": { "score": 0.42, "count": 5, "ts": time.time() } }
_cache: dict = {}
_CACHE_TTL = 1800  # 30 minutes


def _sentiment_api_fail_closed() -> bool:
    try:
        from config import CONFIG
        return bool(CONFIG.get("SENTIMENT_API_FAIL_CLOSED", False))
    except Exception:
        return False


def _max_article_age_hours() -> float:
    try:
        from config import CONFIG

        raw = CONFIG.get(
            "SENTIMENT_GATE_MAX_ARTICLE_AGE_HOURS",
            CONFIG.get("NEWS_SENTIMENT_MAX_ARTICLE_AGE_HOURS", 72),
        )
        return max(1.0, float(raw or 72))
    except (TypeError, ValueError):
        return 72.0


def _fresh_articles(
    articles: list,
    *,
    now: datetime,
    max_age_hours: float,
) -> tuple[list[dict], int]:
    fresh: list[dict] = []
    stale_count = 0
    for article in articles or []:
        if not isinstance(article, dict):
            continue
        dt = _parse_article_dt(article)
        if dt and (now - dt).total_seconds() > max_age_hours * 3600.0:
            stale_count += 1
            continue
        fresh.append(article)
    fresh.sort(
        key=lambda art: _parse_article_dt(art) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return fresh, stale_count


def check_sentiment(
    pair: str,
    direction: str,
    asset_type: str,
    *,
    symbol: str | None = None,
) -> dict:
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

        ticker = eodhd_ticker_from_signal(pair, asset_type, symbol=symbol)
        if not ticker:
            return {
                "allowed": True,
                "score": 0.0,
                "count": 0,
                "reason": f"No ticker mapping for {pair}",
            }

        api = APIClient(api_key)

        now = datetime.now(timezone.utc)
        max_age_hours = _max_article_age_hours()
        date_to = now.strftime("%Y-%m-%d")
        date_from = (now - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d")

        try:
            news = api.financial_news(
                s=ticker, from_date=date_from, to_date=date_to, limit="20"
            )
        except Exception as e:
            fail_closed = _sentiment_api_fail_closed()
            log.warning(f"[SENTIMENT] News API error for {ticker}: {e}")
            return {
                "allowed": not fail_closed,
                "score": 0.0,
                "count": 0,
                "reason": f"API error: {e} — fail closed: {fail_closed}",
            }

        if not news or not isinstance(news, list):
            return {
                "allowed": True,
                "score": 0.0,
                "count": 0,
                "reason": "No news articles found",
            }

        fresh_news, stale_count = _fresh_articles(
            news,
            now=now,
            max_age_hours=max_age_hours,
        )
        if not fresh_news:
            result = {
                "allowed": True,
                "score": 0.0,
                "count": 0,
                "reason": f"No fresh articles within {max_age_hours:.0f}h ({stale_count} stale skipped)",
            }
            _cache[cache_key] = {"result": result, "ts": time.time()}
            log.info(f"[SENTIMENT] {pair} {direction}: {result['reason']}")
            return result

        # Extract sentiment scores from fresh articles only
        sentiments = []
        for article in fresh_news:
            sent = article.get("sentiment", {})
            if isinstance(sent, dict):
                polarity = sent.get("polarity", 0)
                if isinstance(polarity, (int, float)):
                    sentiments.append(polarity)

        if not sentiments:
            result = {
                "allowed": True,
                "score": 0.0,
                "count": len(fresh_news),
                "reason": f"{len(fresh_news)} fresh articles, no sentiment scores",
            }
        else:
            avg_score = sum(sentiments) / len(sentiments)
            count = len(sentiments)

            from config import CONFIG
            # Decision logic:
            # If direction is LONG and sentiment is very bearish (< -threshold): BLOCK
            # If direction is SHORT and sentiment is very bullish (> threshold): BLOCK
            # Otherwise: ALLOW
            try:
                threshold = float(CONFIG.get("SENTIMENT_BLOCK_THRESHOLD", 0.4) or 0.4)
            except (TypeError, ValueError):
                threshold = 0.4
            try:
                align_threshold = float(
                    CONFIG.get(
                        "SENTIMENT_ALIGN_THRESHOLD",
                        max(0.0, threshold - 0.1),
                    )
                    or max(0.0, threshold - 0.1)
                )
            except (TypeError, ValueError):
                align_threshold = max(0.0, threshold - 0.1)
            align_threshold = max(0.0, min(threshold, align_threshold))
            blocked = False
            reason = ""

            if direction == "LONG" and avg_score < -threshold:
                blocked = True
                reason = (
                    f"SENTIMENT BLOCK: strongly bearish ({avg_score:.2f}) opposes LONG"
                )
            elif direction == "SHORT" and avg_score > threshold:
                blocked = True
                reason = (
                    f"SENTIMENT BLOCK: strongly bullish ({avg_score:.2f}) opposes SHORT"
                )
            elif direction == "LONG" and avg_score > align_threshold:
                reason = f"Sentiment aligned: bullish ({avg_score:.2f}) supports LONG"
            elif direction == "SHORT" and avg_score < -align_threshold:
                reason = f"Sentiment aligned: bearish ({avg_score:.2f}) supports SHORT"
            else:
                reason = f"Sentiment neutral ({avg_score:.2f}), {count} articles"

            result = {
                "allowed": not blocked,
                "score": round(avg_score, 3),
                "count": count,
                "reason": reason,
                "staleArticleCount": stale_count,
                "maxArticleAgeHours": max_age_hours,
            }

        # Cache the result
        _cache[cache_key] = {"result": result, "ts": time.time()}

        log.info(f"[SENTIMENT] {pair} {direction}: {result['reason']}")
        return result

    except ImportError:
        fail_closed = _sentiment_api_fail_closed()
        log.warning(f"[SENTIMENT] API failed — fail closed: {fail_closed}")
        return {
            "allowed": not fail_closed,
            "score": 0.0,
            "count": 0,
            "reason": f"Sentiment API unavailable — fail closed: {fail_closed}",
        }
    except Exception as e:
        fail_closed = _sentiment_api_fail_closed()
        log.warning(f"[SENTIMENT] API error — fail closed: {fail_closed} | Error: {e}")
        return {"allowed": not fail_closed, "score": 0.0, "count": 0, "reason": f"Sentiment API error — fail closed: {fail_closed}"}


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
        from config import CONFIG
        is_aligned = (score > 0 and direction == "LONG") or (
            score < 0 and direction == "SHORT"
        )
        try:
            threshold = float(CONFIG.get("SENTIMENT_BLOCK_THRESHOLD", 0.4) or 0.4)
        except (TypeError, ValueError):
            threshold = 0.4
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
