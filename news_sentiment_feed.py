"""EODHD news + AI-provider structured sentiment score for optional confluence use.

Tickers must come from the caller via ``eodhd_ticker_for_pair(pair)`` (e.g. athena's
``_eodhd_ticker_for_pair``) so vendor overrides and display/symbol rules stay single-sourced.
"""

from __future__ import annotations

import json
import logging
import os
import re
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
    from_date: str | None = None,
    to_date: str | None = None,
) -> list:
    """Fetch news; now accepts optional from_date/to_date for freshness (EODHD supports).
    Existing callers unaffected (defaults preserve prior behavior when omitted).
    """
    url = "https://eodhd.com/api/news"
    params = {
        "s": eodhd_ticker,
        "limit": limit,
        "api_token": api_key,
        "fmt": "json",
    }
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    try:
        r = http_requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error("[NewsAI] News fetch failed [%s]: %s", eodhd_ticker, e)
        return []


def _config_value(config: dict | None, key: str, default: Any) -> Any:
    if isinstance(config, dict) and key in config:
        return config.get(key)
    return CONFIG.get(key, default)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Minimal class-keyed resolver (exact same logic as factor_scoring._resolve_class_keyed / _resolve_pair_score_group)
# so NEWS_SENTIMENT_IMPACT_BY_CLASS and MAX_DELTA_BY_CLASS can honor per-group overrides
# (forex_majors, precious_trackers, crypto_*, etc.) exactly like factor weights/ADX/periods.
# No import to avoid any cross-module coupling for this advisory post-A path.
def _resolve_class_keyed(mapping, score_group: str | None, asset_type: str, default):
    if not isinstance(mapping, dict):
        return default
    if score_group and score_group in mapping:
        return mapping[score_group]
    if asset_type in mapping:
        return mapping[asset_type]
    if "default" in mapping:
        return mapping["default"]
    return default


def _resolve_pair_score_group(pair: dict) -> str | None:
    try:
        from scoring import get_pair_score_group
        return get_pair_score_group(pair)
    except Exception:
        return pair.get("score_group")


def _source_key(kind: str, value: str) -> tuple[str, str]:
    return str(kind or "ticker").lower(), str(value or "").strip()


def _normalize_news_source(raw: Any, *, role: str = "macro") -> dict[str, Any] | None:
    if isinstance(raw, dict):
        kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
        value = raw.get("value") or raw.get("ticker") or raw.get("s") or raw.get("tag") or raw.get("topic")
        if not kind:
            kind = "tag" if raw.get("tag") or raw.get("topic") else "ticker"
        value = str(value or "").strip()
        if not value:
            return None
        return {
            "kind": kind,
            "value": value,
            "label": raw.get("label") or raw.get("name") or value,
            "role": raw.get("role") or role,
        }
    text = str(raw or "").strip()
    if not text:
        return None
    if ":" in text:
        prefix, value = text.split(":", 1)
        prefix = prefix.strip().lower()
        value = value.strip()
        if prefix in {"tag", "topic"} and value:
            return {"kind": "tag", "value": value, "label": value, "role": role}
        if prefix in {"ticker", "symbol", "s"} and value:
            return {"kind": "ticker", "value": value, "label": value, "role": role}
        if prefix in {"query", "q"} and value:
            return {"kind": "query", "value": value, "label": value, "role": role}
    kind = "tag" if re.search(r"\s", text) else "ticker"
    return {"kind": kind, "value": text, "label": text, "role": role}


def _driver_map_values(driver_map: Any, keys: list[str]) -> list[Any]:
    if not isinstance(driver_map, dict):
        return []
    out: list[Any] = []
    for key in keys:
        if key in driver_map:
            value = driver_map.get(key)
            if isinstance(value, list):
                out.extend(value)
            else:
                out.append(value)
    return out


def resolve_news_sources_for_pair(
    pair: dict,
    eodhd_ticker_for_pair: Callable[[dict], Optional[str]],
    config: dict | None = None,
) -> list[dict[str, Any]]:
    """Resolve direct plus config-driven macro news sources for a pair."""
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(src: dict[str, Any] | None) -> None:
        if not src:
            return
        kind = str(src.get("kind") or "ticker").lower()
        value = str(src.get("value") or "").strip()
        if not value:
            return
        key = _source_key(kind, value)
        if key in seen:
            return
        seen.add(key)
        sources.append({**src, "kind": kind, "value": value})

    direct = eodhd_ticker_for_pair(pair)
    if direct:
        add({"kind": "ticker", "value": direct, "label": "direct", "role": "direct"})

    if not _config_value(config, "NEWS_SENTIMENT_MACRO_CONTEXT_ENABLED", False):
        return sources

    display = str(pair.get("display") or pair.get("symbol") or "").strip()
    asset_class = _asset_class_for_pair(pair)
    base = quote = ""
    if "/" in display:
        base, quote = [part.strip().upper() for part in display.split("/", 1)]
    driver_map = _config_value(config, "NEWS_SENTIMENT_DRIVER_MAP", {}) or {}
    keys = [
        display,
        display.upper(),
        display.replace("/", ""),
        asset_class,
        base,
        quote,
        f"{asset_class}:{base}" if base else "",
        f"{asset_class}:{quote}" if quote else "",
        "default",
    ]
    for item in _driver_map_values(driver_map, [k for k in keys if k]):
        add(_normalize_news_source(item, role="macro"))
    return sources


def _parse_article_dt(article: dict[str, Any]) -> datetime | None:
    raw = article.get("date") or article.get("published_at") or article.get("publishedAt")
    if not raw:
        return None
    text = str(raw).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _article_dedupe_key(article: dict[str, Any]) -> tuple[str, str, str]:
    url = str(article.get("link") or article.get("url") or "").strip().lower()
    title = str(article.get("title") or article.get("headline") or "").strip().lower()
    date = str(article.get("date") or article.get("published_at") or "")[:10]
    return url, title, date


def fetch_news_sources(
    sources: list[dict[str, Any]],
    api_key: str,
    config: dict | None = None,
    *,
    now: datetime | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Fetch and freshness-filter EODHD news for resolved ticker/tag/query sources."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    max_age_hours = _as_float(
        _config_value(config, "NEWS_SENTIMENT_MAX_ARTICLE_AGE_HOURS", 72), 72.0
    )
    max_total = max(1, _as_int(_config_value(config, "NEWS_SENTIMENT_MAX_ARTICLES_TOTAL", 12), 12))
    per_source = max(1, _as_int(_config_value(config, "NEWS_SENTIMENT_MAX_ARTICLES_PER_SOURCE", 6), 6))
    from_date = (now - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")

    articles: list[dict[str, Any]] = []
    source_meta: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    stale_count = 0
    for source in sources:
        kind = str(source.get("kind") or "ticker").lower()
        value = str(source.get("value") or "").strip()
        if not value:
            continue
        params = {
            "limit": per_source,
            "from": from_date,
            "to": to_date,
            "api_token": api_key,
            "fmt": "json",
        }
        if kind == "tag":
            params["t"] = value
        elif kind == "query":
            params["q"] = value
        else:
            params["s"] = value
        try:
            r = http_requests.get("https://eodhd.com/api/news", params=params, timeout=timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.error("[NewsAI] News source fetch failed [%s:%s]: %s", kind, value, exc)
            source_meta.append({**source, "status": "error", "articleCount": 0})
            continue
        rows = data if isinstance(data, list) else []
        kept_for_source = 0
        for article in rows:
            if not isinstance(article, dict):
                continue
            dt = _parse_article_dt(article)
            stale = bool(dt and (now - dt).total_seconds() > max_age_hours * 3600.0)
            if stale:
                stale_count += 1
                continue
            key = _article_dedupe_key(article)
            if key in seen:
                continue
            seen.add(key)
            enriched = dict(article)
            enriched["source"] = {
                "kind": kind,
                "value": value,
                "label": source.get("label") or value,
                "role": source.get("role"),
            }
            enriched["stale"] = False
            articles.append(enriched)
            kept_for_source += 1
            if len(articles) >= max_total:
                break
        source_meta.append({**source, "status": "ok", "articleCount": kept_for_source})
        if len(articles) >= max_total:
            break
    return {
        "articles": articles[:max_total],
        "sourceMetadata": source_meta,
        "freshArticleCount": len(articles[:max_total]),
        "staleArticleCount": stale_count,
        "maxArticleAgeHours": max_age_hours,
    }


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


def _combine_llm_and_eodhd_vote(
    llm_vote: Optional[float],
    eodhd_score: Optional[float],
) -> tuple[Optional[float], str]:
    if llm_vote is None:
        return None, "unavailable"
    if eodhd_score is None:
        return llm_vote, "unavailable"
    eodhd_vote = max(-1.0, min(1.0, float(eodhd_score)))
    if abs(float(llm_vote)) < 0.05 or abs(eodhd_vote) < 0.05:
        agreement = "neutral"
    elif (float(llm_vote) > 0) == (eodhd_vote > 0):
        agreement = "agree"
    else:
        agreement = "conflict"
    llm_weight = 0.75 if agreement != "conflict" else 0.65
    eodhd_weight = 1.0 - llm_weight
    combined = (float(llm_vote) * llm_weight) + (eodhd_vote * eodhd_weight)
    return round(max(-1.0, min(1.0, combined)), 4), agreement


SYSTEM_PROMPT = """You are a senior quantitative analyst embedded in the Athena trading system.
Read financial news and produce a structured sentiment signal for the confluence engine.

Rules:
1. Be asset-class specific — know what moves each market.
2. Negative/bearish news is weighted more heavily than positive (markets fall faster).
3. Score near 0.0 for vague, speculative, or noise articles.
4. Only score above 0.6 or below -0.6 for major confirmed events.
5. Never hallucinate facts not present in the articles.
6. Output ONLY valid JSON. No markdown. No preamble. No explanation outside the JSON."""


def build_user_prompt(
    pair_display: str,
    asset_class: str,
    news_block: str,
    eodhd_score: Optional[float] = None,  # accepted for back-compat; not injected into prompt
    current_price: Optional[float] = None,  # accepted for back-compat; not injected into prompt
) -> str:
    # eodhd_score and current_price are intentionally NOT placed into the prompt:
    # the EODHD pre-score anchored the model and current_price was unused (audit fix).
    ctx = ASSET_CONTEXT.get(asset_class, ASSET_CONTEXT["stock"])

    return f"""PAIR: {pair_display}
ASSET CLASS: {asset_class.upper()}

CONTEXT:
{ctx}

=== NEWS ARTICLES (most recent first) ===
{news_block}
==========================================

TASK:
Read each article. Score from -1.0 to +1.0 (-1 = strongly bearish, +1 = strongly bullish, 0 = neutral/noise).

Return ONLY this JSON object — no prose, no markdown:

{{
  "pair": "{pair_display}",
  "sentiment_score": <float -1.0 to 1.0, 2 decimal places>,
  "confidence": <float 0.0 to 1.0>,
  "direction": "<bullish|bearish|neutral>",
  "key_themes": ["<theme1>", "<theme2>", "<theme3>"],
  "major_event_detected": <true|false>,
  "major_event_description": "<one sentence or null>",
  "reasoning_summary": "<2-3 sentences>",
  "article_count_used": <int>
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
    config: dict | None = None,
) -> Optional[dict]:
    """Resolve EODHD ticker → fetch news → LLM structured sentiment JSON.

    Returns parsed result dict or None on failure / no articles.
    """
    ticker = eodhd_ticker_for_pair(pair)
    if not ticker:
        log.warning("[NewsAI] No EODHD ticker for pair display=%s", pair.get("display"))
        return None

    display = pair.get("display") or pair.get("symbol") or ticker
    asset_class = _asset_class_for_pair(pair)
    sources = resolve_news_sources_for_pair(pair, eodhd_ticker_for_pair, config)
    if not sources and ticker:
        sources = [{"kind": "ticker", "value": ticker, "label": "direct", "role": "direct"}]
    source_bundle = fetch_news_sources(
        sources,
        eodhd_api_key,
        config,
        timeout=_as_float(_config_value(config, "NEWS_SENTIMENT_FETCH_TIMEOUT_SEC", 12.0), 12.0),
    )
    articles = list(source_bundle.get("articles") or [])[: max(1, int(news_limit or 8))]

    if not articles:
        log.warning("[NewsAI] No articles for %s (%s)", display, ticker)
        return None

    news_block = build_news_block(articles)
    user_prompt = build_user_prompt(
        display, asset_class, news_block, eodhd_score=None, current_price=None
    )

    try:
        client = create_ai_client(CONFIG, api_key=xai_api_key)
        _temp = float(CONFIG.get("AI_TEMPERATURE", 0.3))
        _max_tokens = int(CONFIG.get("NEWS_SENTIMENT_MAX_TOKENS", 400) or 400)
        response = client.chat.completions.create(
            model=model,
            max_tokens=_max_tokens,
            temperature=_temp,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
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
        result["source_metadata"] = source_bundle.get("sourceMetadata", [])
        result["fresh_article_count"] = source_bundle.get("freshArticleCount", len(articles))
        result["stale_article_count"] = source_bundle.get("staleArticleCount", 0)
        result["max_article_age_hours"] = source_bundle.get("maxArticleAgeHours")
        result["article_count_used"] = result.get("article_count_used") or len(articles)
        eodhd_normalized = None
        if _config_value(config, "NEWS_SENTIMENT_USE_EODHD_NORMALIZED", False):
            eodhd_normalized = fetch_eodhd_sentiment(ticker, eodhd_api_key)
        llm_vote = news_to_confluence_vote(result)
        combined_vote, agreement = _combine_llm_and_eodhd_vote(llm_vote, eodhd_normalized)
        result["eodhd_normalized_score"] = eodhd_normalized
        result["eodhd_agreement"] = agreement
        result["eodhd_source_date"] = None
        result["eodhd_article_count"] = len(articles) if eodhd_normalized is not None else None
        if combined_vote is not None:
            result["combined_vote"] = combined_vote
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
    if result.get("combined_vote") is not None:
        try:
            return round(max(-1.0, min(1.0, float(result["combined_vote"]))), 4)
        except (TypeError, ValueError):
            pass
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
    config: dict | None = None,
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
            config=config,
        )
        vote = news_to_confluence_vote(result)
        with _cache_registry_lock:
            _news_confluence_cache[display] = (time.time(), (vote, result))
        return vote, result


def _max_positive_news_sentiment_delta(
    pair: dict,
    *,
    config: dict,
    max_score: Optional[float],
) -> float:
    max_s = float(max_score if max_score is not None else 3.0)
    _sg = _resolve_pair_score_group(pair)
    _asset = str(pair.get("type") or "stock").lower()
    _impact_map = _config_value(config, "NEWS_SENTIMENT_IMPACT_BY_CLASS", {}) or {}
    _delta_map = _config_value(config, "NEWS_SENTIMENT_MAX_DELTA_BY_CLASS", {}) or {}
    impact = abs(
        float(
            _resolve_class_keyed(
                _impact_map,
                _sg,
                _asset,
                _config_value(config, "NEWS_SENTIMENT_SCORE_IMPACT", 0.06),
            )
            or 0.06
        )
    )
    max_delta = abs(
        float(
            _resolve_class_keyed(
                _delta_map,
                _sg,
                _asset,
                _config_value(config, "NEWS_SENTIMENT_MAX_DELTA", 0.30),
            )
            or 0.30
        )
    )
    return max(0.0, min(max_delta, impact * max_s))


def apply_news_sentiment_to_scan_result(
    res: dict,
    pair: dict,
    *,
    config: dict,
    eodhd_ticker_for_pair: Callable[[dict], Optional[str]],
    current_price: Optional[float] = None,
    threshold: Optional[float] = None,
    max_score: Optional[float] = None,
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
    max_s = float(max_score if max_score is not None else res.get("maxScoreOverride") or 3.0)
    threshold_value = float(threshold if threshold is not None else res.get("threshold") or 0.0)
    major_mode_for_filter = str(
        config.get("NEWS_SENTIMENT_MAJOR_EVENT_MODE") or "advisory"
    ).strip().lower()
    if (
        bool(config.get("NEWS_SENTIMENT_SKIP_UNREACHABLE_SCAN_ROWS", True))
        and threshold_value > 0
        and major_mode_for_filter == "advisory"
        and not bool(config.get("SCAN_QUANTILE_ENABLED", False))
    ):
        base_score = float(res.get("score", 0.0) or 0.0)
        max_positive_delta = _max_positive_news_sentiment_delta(
            pair,
            config=config,
            max_score=max_s,
        )
        if base_score < threshold_value and (base_score + max_positive_delta) < (threshold_value - 1e-9):
            res["newsSentimentVote"] = None
            if config.get("NEWS_SENTIMENT_ATTACH_SUMMARY", True):
                res["newsSentimentSummary"] = None
            res["newsSentimentSkipped"] = {
                "reason": "below_relevance_floor",
                "baseScore": round(base_score, 6),
                "threshold": round(threshold_value, 6),
                "maxPositiveDelta": round(max_positive_delta, 6),
            }
            return

    vote, detail = get_cached_news_confluence_vote(
        pair,
        eodhd_api_key=eod,
        xai_api_key=xai,
        eodhd_ticker_for_pair=eodhd_ticker_for_pair,
        ttl_sec=ttl,
        model=model,
        current_price=current_price,
        config=config,
    )

    res["newsSentimentVote"] = vote
    if detail and config.get("NEWS_SENTIMENT_ATTACH_SUMMARY", True):
        res["newsSentimentSummary"] = {
            "direction": detail.get("direction"),
            "confidence": detail.get("confidence"),
            "sentiment_score": detail.get("sentiment_score"),
            "article_count_used": detail.get("article_count_used"),
            "fresh_article_count": detail.get("fresh_article_count"),
            "stale_article_count": detail.get("stale_article_count"),
            "source_metadata": detail.get("source_metadata"),
            "key_themes": list(detail.get("key_themes") or [])[:6],
            "major_event_detected": detail.get("major_event_detected"),
            "major_event_description": detail.get("major_event_description"),
            "eodhd_normalized_score": detail.get("eodhd_normalized_score"),
            "eodhd_agreement": detail.get("eodhd_agreement"),
            "eodhd_source_date": detail.get("eodhd_source_date"),
            "eodhd_article_count": detail.get("eodhd_article_count"),
            "reasoning_summary": (detail.get("reasoning_summary") or "")[:400],
        }
    elif vote is not None:
        res["newsSentimentSummary"] = None

    major_detected = bool((detail or {}).get("major_event_detected"))
    major_mode = str(config.get("NEWS_SENTIMENT_MAJOR_EVENT_MODE") or "advisory").strip().lower()
    if major_mode not in {"advisory", "negative_delta_only", "block_auto_execution"}:
        major_mode = "advisory"
    major_reason = (detail or {}).get("major_event_description") or "major event detected"
    major_action = "none"
    blocks_auto = False
    if major_detected:
        if major_mode == "block_auto_execution":
            major_action = "block_auto_execution"
            blocks_auto = True
        elif major_mode == "negative_delta_only":
            major_action = "negative_delta_only"
        else:
            major_action = "advisory_only"
    res["majorEventRisk"] = {
        "majorEventDetected": major_detected,
        "mode": major_mode,
        "action": major_action,
        "reason": major_reason if major_detected else None,
        "blocksAutoExecution": blocks_auto,
    }

    if vote is None:
        return

    # FIX 3: group-aware impact (BY_CLASS > score_group > asset_type > default > global scalar).
    # Preserves all existing clamps, major-event guards, pre_news_score, and bounded delta exactly.
    _sg = _resolve_pair_score_group(pair)
    _asset = str(pair.get("type") or "stock").lower()
    _impact_map = _config_value(config, "NEWS_SENTIMENT_IMPACT_BY_CLASS", {}) or {}
    _delta_map = _config_value(config, "NEWS_SENTIMENT_MAX_DELTA_BY_CLASS", {}) or {}
    impact = float(_resolve_class_keyed(_impact_map, _sg, _asset, _config_value(config, "NEWS_SENTIMENT_SCORE_IMPACT", 0.06)) or 0.06)
    max_delta = abs(float(_resolve_class_keyed(_delta_map, _sg, _asset, _config_value(config, "NEWS_SENTIMENT_MAX_DELTA", 0.30)) or 0.30))

    raw_delta = impact * max_s * float(vote)
    delta = raw_delta
    base_score = float(res.get("score", 0.0) or 0.0)
    if threshold_value > 0 and base_score < threshold_value * 0.8:
        delta = min(0.0, delta)
    if major_detected and major_mode == "negative_delta_only":
        delta = min(0.0, delta)
    delta = max(-max_delta, min(max_delta, delta))
    new = max(0.0, min(max_s, base_score + delta))
    res["score"] = round(new, 4)
    if res.get("final_score") is not None:
        res["final_score"] = res["score"]

    res["pre_news_score"] = round(base_score, 4)
    res["news_adjustment"] = round(delta, 4)
    res["newsSentimentDelta"] = round(delta, 6)
    if round(raw_delta, 6) != round(delta, 6):
        res["newsSentimentRawDelta"] = round(raw_delta, 6)
    res.setdefault("warnings", []).append(
        f"News AI: vote {vote:+.4f} -> score change {'+' if delta >= 0 else ''}{delta:.4f} (max {max_s})"
    )
