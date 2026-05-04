"""EODHD news + AI-provider structured sentiment score for optional confluence use.

Tickers must come from the caller via ``eodhd_ticker_for_pair(pair)`` (e.g. athena's
``_eodhd_ticker_for_pair``) so vendor overrides and display/symbol rules stay single-sourced.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config import CONFIG, create_ai_client, get_ai_api_key, get_ai_provider_label
from data_feeds import http_requests

log = logging.getLogger("athena")

ASSET_CONTEXT = {
    "forex": (
        "This is a FOREX pair. Most impactful news: central bank rate decisions, "
        "inflation prints (CPI/PCE), GDP releases, NFP/employment data, geopolitical "
        "risk, trade balances, and PMI readings. Rate hike expectations are bullish "
        "for the base currency. Safe-haven flows (risk-off) strengthen JPY, CHF, USD."
    ),
    "crypto": (
        "This is a CRYPTOCURRENCY pair. Most impactful news: SEC/regulatory actions, "
        "exchange health (hacks, solvency), ETF approvals/rejections, network upgrades, "
        "whale movements, macro risk-off events, and institutional adoption. "
        "Regulatory crackdowns are strongly bearish. ETF approvals are strongly bullish."
    ),
    "stock": (
        "This is an EQUITY or ETF. Most impactful news: earnings beats/misses, "
        "revenue guidance revisions, analyst upgrades/downgrades, M&A activity, "
        "management changes, sector-specific events, and macro conditions. "
        "Earnings misses are more bearish than beats are bullish."
    ),
    "commodity": (
        "This is a COMMODITY (precious metal or energy). Most impactful news: "
        "Fed rate decisions (inverse USD relationship for gold/silver), geopolitical "
        "conflict, supply disruptions, inflation expectations, and DXY movements. "
        "Gold is a safe-haven — risk-off events are bullish."
    ),
    "index": (
        "This is a MARKET INDEX. Most impactful news: macro data (CPI, NFP, GDP), "
        "Fed/central bank policy, earnings season breadth, geopolitical events, "
        "and sector rotation. Indexes are broad — weight news that affects multiple sectors."
    ),
}


def _asset_class_for_pair(pair: dict) -> str:
    ptype = (pair.get("type") or "stock").lower()
    if ptype in ASSET_CONTEXT:
        return ptype
    return "stock"


def _log_news_ai_review(
    *,
    display: str,
    asset_class: str,
    model: str,
    user_prompt: str,
    result: dict | None,
    parse_success: bool,
    schema_valid: bool,
) -> None:
    try:
        from ai_review_logger import (
            AI_STATE_CAUTION,
            AI_STATE_REVIEW_INCOMPLETE,
            REVIEW_TYPE_NEWS_SENTIMENT,
            log_ai_review,
        )

        confidence = None
        if isinstance(result, dict):
            try:
                confidence = float(result.get("confidence"))
            except (TypeError, ValueError):
                confidence = None
        log_ai_review(
            symbol=display or "?",
            asset_type=asset_class or "?",
            review_type=REVIEW_TYPE_NEWS_SENTIMENT,
            model=model,
            provider=get_ai_provider_label(CONFIG),
            prompt_version="news_sentiment_v1",
            input_packet=user_prompt,
            has_chart_image=False,
            candle_freshness_status="not_applicable",
            engine_a_state=None,
            engine_b_state=None,
            engine_c_state=None,
            engine_d_state=None,
            risk_state={
                "major_event_detected": (result or {}).get("major_event_detected")
                if isinstance(result, dict)
                else None
            },
            ai_review_state=AI_STATE_CAUTION if parse_success else AI_STATE_REVIEW_INCOMPLETE,
            ai_confidence=confidence,
            contradictions_count=0,
            missing_information_count=0 if parse_success else 1,
            parse_success=parse_success,
            schema_valid=schema_valid,
            execution_allowed_before_ai=True,
            execution_allowed_after_ai=True,
            final_action="advisory",
        )
    except Exception as _log_exc:
        log.debug("[NewsAI] audit log failed for %s: %s", display, _log_exc)


def fetch_news(
    eodhd_ticker: str,
    api_key: str,
    limit: int = 8,
    *,
    timeout: float = 12.0,
) -> list:
    url = "https://eodhd.com/api/news"
    params = {
        "s": eodhd_ticker,
        "limit": limit,
        "api_token": api_key,
        "fmt": "json",
    }
    try:
        r = http_requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error("[NewsAI] News fetch failed [%s]: %s", eodhd_ticker, e)
        return []


def _latest_normalized_sentiment(data: Any, ticker: str) -> Optional[float]:
    """Parse EODHD /api/sentiments payload (dict or list of rows) — align with fetch_news_context."""
    if not data:
        return None
    sentiment_by_ticker: dict[str, Any] = {}
    if isinstance(data, dict):
        sentiment_by_ticker = data
    elif isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            key = (
                row.get("code")
                or row.get("symbol")
                or row.get("ticker")
                or row.get("s")
            )
            if key:
                sentiment_by_ticker[str(key)] = row

    raw_scores = sentiment_by_ticker.get(ticker, [])
    if isinstance(raw_scores, dict):
        scores = [raw_scores]
    elif isinstance(raw_scores, list):
        scores = raw_scores
    else:
        scores = []

    if not scores:
        return None

    dated = [s for s in scores if isinstance(s, dict) and s.get("date")]
    pool = dated if dated else [s for s in scores if isinstance(s, dict)]
    if not pool:
        return None

    latest = sorted(pool, key=lambda x: str(x.get("date", "")), reverse=True)[0]
    n = latest.get("normalized")
    if n is None:
        return None
    try:
        return float(n)
    except (TypeError, ValueError):
        return None


def fetch_eodhd_sentiment(
    eodhd_ticker: str,
    api_key: str,
    *,
    days: int = 3,
    timeout: float = 12.0,
) -> Optional[float]:
    date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    url = "https://eodhd.com/api/sentiments"
    params = {
        "s": eodhd_ticker,
        "from": date_from,
        "to": date_to,
        "api_token": api_key,
        "fmt": "json",
    }
    try:
        r = http_requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return _latest_normalized_sentiment(r.json(), eodhd_ticker)
    except Exception as e:
        log.error("[NewsAI] Sentiment fetch failed [%s]: %s", eodhd_ticker, e)
        return None


def build_news_block(articles: list) -> str:
    blocks = []
    for i, art in enumerate(articles[:8], 1):
        if not isinstance(art, dict):
            continue
        date = str(art.get("date", ""))[:10]
        title = str(art.get("title", "")).strip()
        content = str(art.get("content", "")).strip()
        snippet = content[:450].replace("\n", " ") if content else "(no content)"
        blocks.append(f"[{i}] {date} | {title}\n{snippet}")
    return "\n\n".join(blocks)


SYSTEM_PROMPT = """You are a senior quantitative analyst and market strategist
embedded in a live algorithmic trading system called Athena.

Your sole function is to read financial news articles and produce a precise
structured sentiment signal that feeds directly into the confluence scoring engine.

Hard rules:
1. Use chain-of-thought reasoning before scoring — work through each article.
2. Be asset-class specific — know what moves each market.
3. Negative/bearish news is weighted more heavily than positive (markets fall faster).
4. Score near 0.0 for vague, speculative, or noise articles.
5. Only score above 0.6 or below -0.6 for major confirmed events.
6. Never hallucinate facts not present in the articles.
7. Output ONLY valid JSON. No markdown. No preamble. No explanation outside the JSON."""


def build_user_prompt(
    pair_display: str,
    asset_class: str,
    news_block: str,
    eodhd_score: Optional[float],
    current_price: Optional[float] = None,
) -> str:
    price_line = (
        f"Current price: {current_price}"
        if current_price is not None
        else "Current price: not provided"
    )
    eodhd_line = (
        f"EODHD pre-scored sentiment (-1 to 1): {eodhd_score:.4f}"
        if eodhd_score is not None
        else "EODHD pre-score: unavailable"
    )
    ctx = ASSET_CONTEXT.get(asset_class, ASSET_CONTEXT["stock"])

    return f"""PAIR: {pair_display}
ASSET CLASS: {asset_class.upper()}
{price_line}
{eodhd_line}

CONTEXT:
{ctx}

=== NEWS ARTICLES (most recent first) ===
{news_block}
==========================================

TASK — work through these steps:

STEP 1 — PER-ARTICLE ANALYSIS:
For each article, state:
  - Directional implication for {pair_display}: BULLISH / BEARISH / NEUTRAL
  - Confidence in that direction: HIGH / MEDIUM / LOW
  - Why (one sentence max)
  - Is this a major market-moving event? YES / NO

STEP 2 — AGGREGATE:
Summarise the overall picture. Note any conflicting signals.
State whether the EODHD pre-score aligns or conflicts with your reading.

STEP 3 — FINAL SCORE:
Score from -1.0 to +1.0:
  -1.0 = strongly bearish, high confidence
  -0.5 = moderately bearish
   0.0 = neutral / noise / conflicting
  +0.5 = moderately bullish
  +1.0 = strongly bullish, high confidence

STEP 4 — JSON OUTPUT:
Return ONLY this exact JSON structure:

{{
  "pair": "{pair_display}",
  "sentiment_score": <float -1.0 to 1.0, 2 decimal places>,
  "confidence": <float 0.0 to 1.0>,
  "direction": "<bullish|bearish|neutral>",
  "key_themes": ["<theme1>", "<theme2>", "<theme3>"],
  "major_event_detected": <true|false>,
  "major_event_description": "<one sentence describing the event, or null>",
  "reasoning_summary": "<2-3 sentences summarising your reasoning>",
  "article_count_used": <int>,
  "eodhd_pre_score": <float or null>,
  "eodhd_agreement": "<agree|disagree|unavailable>"
}}"""


def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if "```" not in text:
        return text
    parts = text.split("```")
    for part in parts:
        part = part.strip()
        if part.startswith("json"):
            part = part[4:].strip()
        if part.startswith("{"):
            return part
    return text


def _extract_balanced_json_object(text: str) -> Optional[str]:
    """First top-level `{` … `}` slice, respecting JSON string escapes (nested `{` inside strings)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _parse_news_ai_json(raw: str) -> dict:
    """Parse model output: fenced JSON, raw JSON, or prose + trailing JSON object."""
    s = _strip_json_fences(raw.strip())
    if not s:
        raise ValueError("empty model text")
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    extracted = _extract_balanced_json_object(s)
    if extracted:
        return json.loads(extracted)
    raise json.JSONDecodeError("no JSON object in model output", s, 0)


def get_news_sentiment(
    pair: dict,
    *,
    eodhd_api_key: str,
    xai_api_key: str,
    eodhd_ticker_for_pair: Callable[[dict], Optional[str]],
    current_price: Optional[float] = None,
    news_limit: int = 8,
    model: str = "grok-4.3",
) -> Optional[dict]:
    """
    Full pipeline: resolve EODHD ticker -> news -> optional EODHD sentiment -> JSON.

    Returns parsed result dict or None on failure / no articles.
    """
    ticker = eodhd_ticker_for_pair(pair)
    if not ticker:
        log.warning("[NewsAI] No EODHD ticker for pair display=%s", pair.get("display"))
        return None

    display = pair.get("display") or pair.get("symbol") or ticker
    asset_class = _asset_class_for_pair(pair)
    articles = fetch_news(ticker, eodhd_api_key, limit=news_limit)

    if not articles:
        log.warning("[NewsAI] No articles for %s (%s)", display, ticker)
        return None

    eodhd_score = fetch_eodhd_sentiment(ticker, eodhd_api_key)
    news_block = build_news_block(articles)
    user_prompt = build_user_prompt(
        display, asset_class, news_block, eodhd_score, current_price
    )

    try:
        client = create_ai_client(CONFIG, api_key=xai_api_key)
        _temp = float(CONFIG.get("AI_TEMPERATURE", 0.3))
        response = client.chat.completions.create(
            model=model,
            max_tokens=1200,
            temperature=_temp,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        try:
            result = _parse_news_ai_json(raw)
        # JSONDecodeError must precede ValueError (JSONDecodeError subclasses ValueError).
        except json.JSONDecodeError as e:
            preview = (raw[:280] + "…") if len(raw) > 280 else raw
            log.error(
                "[NewsAI] JSON parse failed for %s: %s preview=%r",
                display,
                e,
                preview,
            )
            _log_news_ai_review(
                display=display,
                asset_class=asset_class,
                model=model,
                user_prompt=user_prompt,
                result=None,
                parse_success=False,
                schema_valid=False,
            )
            return None
        except ValueError as e:
            if "empty model text" in str(e).lower():
                log.warning(
                    "[NewsAI] empty AI text for %s",
                    display,
                )
            else:
                log.warning("[NewsAI] unexpected parse error for %s: %s", display, e)
            _log_news_ai_review(
                display=display,
                asset_class=asset_class,
                model=model,
                user_prompt=user_prompt,
                result=None,
                parse_success=False,
                schema_valid=False,
            )
            return None
        _required = {
            "sentiment_score",
            "confidence",
            "direction",
            "major_event_detected",
            "reasoning_summary",
        }
        _log_news_ai_review(
            display=display,
            asset_class=asset_class,
            model=model,
            user_prompt=user_prompt,
            result=result,
            parse_success=True,
            schema_valid=_required.issubset(result.keys()),
        )
        log.info(
            "[NewsAI] %s: score=%s dir=%s conf=%s major=%s",
            display,
            result.get("sentiment_score"),
            result.get("direction"),
            result.get("confidence"),
            result.get("major_event_detected"),
        )
        return result
    except Exception as e:
        log.error("[NewsAI] AI provider error for %s: %s", display, e)
        _log_news_ai_review(
            display=display,
            asset_class=asset_class,
            model=model,
            user_prompt=user_prompt,
            result=None,
            parse_success=False,
            schema_valid=False,
        )
        return None


def news_to_confluence_vote(result: Optional[dict]) -> Optional[float]:
    """Map structured result to a single vote; None if low confidence or missing."""
    if not result:
        return None
    try:
        score = float(result.get("sentiment_score", 0.0))
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if confidence < 0.4:
        return None
    return round(score * confidence, 4)


# ── Scan integration: TTL cache + per-display lock (parallel symbols, one flight each) ──

_news_confluence_cache: dict[str, tuple[float, tuple[Optional[float], Optional[dict]]]] = {}
_cache_registry_lock = threading.Lock()
_display_fetch_locks: dict[str, threading.Lock] = {}


def _display_lock(display: str) -> threading.Lock:
    with _cache_registry_lock:
        if display not in _display_fetch_locks:
            _display_fetch_locks[display] = threading.Lock()
        return _display_fetch_locks[display]


def get_cached_news_confluence_vote(
    pair: dict,
    *,
    eodhd_api_key: str,
    xai_api_key: str,
    eodhd_ticker_for_pair: Callable[[dict], Optional[str]],
    ttl_sec: float,
    model: str,
    current_price: Optional[float] = None,
) -> tuple[Optional[float], Optional[dict]]:
    """Return (vote, structured_result) using TTL cache; refreshes on expiry per display."""
    display = pair.get("display") or ""
    if not display:
        return None, None

    now = time.time()
    ttl = max(60.0, float(ttl_sec))
    with _cache_registry_lock:
        hit = _news_confluence_cache.get(display)
        if hit and (now - hit[0]) < ttl:
            return hit[1]

    dlock = _display_lock(display)
    with dlock:
        now = time.time()
        with _cache_registry_lock:
            hit = _news_confluence_cache.get(display)
            if hit and (now - hit[0]) < ttl:
                return hit[1]

        result = get_news_sentiment(
            pair,
            eodhd_api_key=eodhd_api_key,
            xai_api_key=xai_api_key,
            eodhd_ticker_for_pair=eodhd_ticker_for_pair,
            current_price=current_price,
            model=model,
        )
        vote = news_to_confluence_vote(result)
        with _cache_registry_lock:
            _news_confluence_cache[display] = (time.time(), (vote, result))
        return vote, result


def apply_news_sentiment_to_scan_result(
    res: dict,
    pair: dict,
    *,
    config: dict,
    eodhd_ticker_for_pair: Callable[[dict], Optional[str]],
    current_price: Optional[float] = None,
) -> None:
    """If enabled and keys present, blend cached News AI vote into ``res['score']`` (mutates ``res``).

    Applied after structural gates so Engine B uses the pre-news technical score.
    Requires ``EODHD_KEY`` and an AI API key (env or config).
    """
    if not config.get("NEWS_SENTIMENT_CONFLUENCE_ENABLED"):
        return

    eod = os.environ.get("EODHD_KEY", "").strip()
    xai = get_ai_api_key(config)
    if not eod or not xai:
        return

    ttl = float(config.get("NEWS_SENTIMENT_CACHE_TTL_SEC", 900))
    model = str(
        config.get("NEWS_SENTIMENT_MODEL")
        or config.get("AI_MODEL")
        or config.get("VISION_MODEL", "grok-4.3")
    )
    vote, detail = get_cached_news_confluence_vote(
        pair,
        eodhd_api_key=eod,
        xai_api_key=xai,
        eodhd_ticker_for_pair=eodhd_ticker_for_pair,
        ttl_sec=ttl,
        model=model,
        current_price=current_price,
    )

    res["newsSentimentVote"] = vote
    if detail and config.get("NEWS_SENTIMENT_ATTACH_SUMMARY", True):
        res["newsSentimentSummary"] = {
            "direction": detail.get("direction"),
            "confidence": detail.get("confidence"),
            "major_event_detected": detail.get("major_event_detected"),
            "reasoning_summary": (detail.get("reasoning_summary") or "")[:400],
        }
    elif vote is not None:
        res["newsSentimentSummary"] = None

    if vote is None:
        return

    max_s = float(res.get("maxScoreOverride") or 3.0)
    impact = float(config.get("NEWS_SENTIMENT_SCORE_IMPACT", 0.06))
    delta = impact * max_s * float(vote)
    old = float(res["score"])
    new = max(0.0, min(max_s, old + delta))
    res["score"] = round(new, 4)
    if res.get("final_score") is not None:
        res["final_score"] = res["score"]

    res["newsSentimentDelta"] = round(delta, 6)
    res.setdefault("warnings", []).append(
        f"News AI: vote {vote:+.4f} → score change {'+' if delta >= 0 else ''}{delta:.4f} (max {max_s})"
    )
