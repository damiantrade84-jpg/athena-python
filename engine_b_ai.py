"""
engine_b_ai.py - AI integration for Engine B (Naked Structure Engine)

Adapts Engine A's AI design pattern for Engine B structural analysis.
"""

import logging
import time
from typing import Optional

from ai_schemas import EngineBResponse
from ai_utils import parse_json_object
from config import CONFIG, AITemperatureConfig, create_ai_client, get_ai_model, resolve_ai_review_runtime

log = logging.getLogger("athena")
VALID_ENGINE_B_GRADES = {"A+", "A", "B", "C", "D", "F"}
VALID_ENGINE_B_RISK_LEVELS = {"Low", "Medium", "High"}
REQUIRED_STYLE_KEYS = {"scalp", "intraday", "swing"}


def _is_retryable_ai_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    markers = (
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "connection refused",
        "temporarily unavailable",
        "server error",
        "rate limit",
        "too many requests",
        "502",
        "503",
        "504",
    )
    return any(marker in text for marker in markers)


def _call_ai_with_retry(client, model: str, messages: list, temperature: float):
    """Call AI with timeout and exponential backoff retry on transient provider failures.

    Prefers Pydantic-validated structured output via beta.chat.completions.parse;
    falls back to legacy json_object response_format if structured-output is unavailable.
    Returns a tuple (parsed_dict_or_none, raw_text) — parsed_dict_or_none is set when
    structured-output succeeded; raw_text is set when only the legacy path returned.
    """
    timeout_sec = float(CONFIG.get("ENGINE_B_AI_TIMEOUT_SEC", 30.0) or 30.0)
    max_retries = int(CONFIG.get("ENGINE_B_AI_MAX_RETRIES", 2) or 2)
    backoff_base = float(CONFIG.get("ENGINE_B_AI_RETRY_BACKOFF_SEC", 1.0) or 1.0)
    use_structured = bool(CONFIG.get("ENGINE_B_AI_STRUCTURED_OUTPUT", True))
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            if use_structured:
                try:
                    completion = client.beta.chat.completions.parse(
                        model=model,
                        max_tokens=800,
                        temperature=temperature,
                        messages=messages,
                        response_format=EngineBResponse,
                        timeout=timeout_sec,
                    )
                    parsed = completion.choices[0].message.parsed
                    if parsed is not None:
                        return parsed.model_dump(), None
                except Exception as _so_err:
                    log.debug("[ENGINE_B_AI] structured output unavailable: %s", _so_err)
            completion = client.chat.completions.create(
                model=model,
                max_tokens=800,
                temperature=temperature,
                messages=messages,
                response_format={"type": "json_object"},
                timeout=timeout_sec,
            )
            return None, (completion.choices[0].message.content or "").strip()
        except Exception as e:
            last_exc = e
            if attempt >= max_retries or not _is_retryable_ai_error(e):
                break
            sleep_sec = backoff_base * (2 ** attempt)
            log.warning(
                "[ENGINE_B_AI] transient AI failure on attempt %s/%s: %s; retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                e,
                sleep_sec,
            )
            time.sleep(sleep_sec)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("engine_b_ai retry exhausted without exception")


def _validate_engine_b_ai_payload(parsed: dict, pair: str) -> dict:
    """Validate and sanitize AI response fields after normalization."""
    out = dict(parsed)

    # edgeProbability must be numeric 0-100
    ep = out.get("edgeProbability")
    try:
        ep_val = float(ep) if ep is not None else 50.0
        ep_val = max(0.0, min(100.0, ep_val))
    except (TypeError, ValueError):
        log.warning(f"[ENGINE_B_AI] {pair}: Invalid edgeProbability {ep!r}; using 50")
        ep_val = 50.0
    out["edgeProbability"] = ep_val

    # riskLevel must be valid
    rl = str(out.get("riskLevel", "Medium")).strip().title()
    if rl not in VALID_ENGINE_B_RISK_LEVELS:
        log.warning(f"[ENGINE_B_AI] {pair}: Invalid riskLevel {rl!r}; using Medium")
        rl = "Medium"
    out["riskLevel"] = rl

    # style_ratings must contain all three styles with valid fields
    style_ratings = out.get("style_ratings")
    if not isinstance(style_ratings, dict):
        style_ratings = {}
    for style in REQUIRED_STYLE_KEYS:
        if style not in style_ratings or not isinstance(style_ratings[style], dict):
            style_ratings[style] = {
                "grade": _normalise_engine_b_grade(out.get("grade", "C"), pair),
                "edgeProbability": ep_val,
                "riskLevel": rl,
            }
        else:
            sr = dict(style_ratings[style])
            sr["grade"] = _normalise_engine_b_grade(sr.get("grade", out.get("grade", "C")), pair)
            try:
                sr_ep = float(sr.get("edgeProbability", ep_val))
                sr_ep = max(0.0, min(100.0, sr_ep))
            except (TypeError, ValueError):
                sr_ep = ep_val
            sr["edgeProbability"] = sr_ep
            sr_rl = str(sr.get("riskLevel", rl)).strip().title()
            if sr_rl not in VALID_ENGINE_B_RISK_LEVELS:
                sr_rl = rl
            sr["riskLevel"] = sr_rl
            style_ratings[style] = sr
    out["style_ratings"] = style_ratings
    return out


def _get_present_value(payload: dict | None, *keys, default=None):
    if not isinstance(payload, dict):
        return default
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def _normalise_engine_b_ai_payload(parsed: dict) -> dict:
    """Accept common model key aliases before falling back to safe defaults."""
    if not isinstance(parsed, dict):
        return parsed

    out = dict(parsed)
    aliases = {
        "edge_probability": "edgeProbability",
        "edge_prob": "edgeProbability",
        "risk_level": "riskLevel",
        "summary": "verdict",
        "reasoning": "verdict",
        "analysis": "verdict",
    }
    for src, dst in aliases.items():
        if dst not in out and src in out:
            out[dst] = out[src]

    style_ratings = out.get("style_ratings")
    if isinstance(style_ratings, dict):
        normalised_styles = {}
        for style, row in style_ratings.items():
            if not isinstance(row, dict):
                continue
            item = dict(row)
            if "edgeProbability" not in item:
                item["edgeProbability"] = (
                    item.get("edge_probability")
                    or item.get("edge_prob")
                    or out.get("edgeProbability")
                )
            if "riskLevel" not in item:
                item["riskLevel"] = item.get("risk_level") or out.get("riskLevel")
            normalised_styles[str(style).lower()] = item
        out["style_ratings"] = normalised_styles

    return out


def _normalise_engine_b_grade(value, pair: str = "?") -> str:
    grade = str(value or "C").strip().upper()
    if grade in VALID_ENGINE_B_GRADES:
        return grade
    log.warning(f"[ENGINE_B_AI] {pair}: Invalid grade {value!r}; using C")
    return "C"


def build_engine_b_signal_message(
    pair: str,
    direction: str,
    current_price: float,
    structure_result: dict,
    confidence_result: dict,
    learning_ctx: Optional[dict] = None,
    engine_a_ctx: Optional[dict] = None,
    news_ctx: Optional[dict] = None,
    freshness_ctx: Optional[dict] = None,
) -> str:
    """
    Build AI prompt message for Engine B structural signals.
    Follows Engine A pattern but emphasizes price action structure over indicators.

    Args:
        pair: Trading pair display name
        direction: LONG or SHORT
        current_price: Current market price
        structure_result: Output from NakedEngine.analyze_structure()
        confidence_result: Output from NakedEngine.calculate_confidence()
        learning_ctx: AI learning context from trade outcomes
        engine_a_ctx: Optional Engine A signal dict for cross-engine alignment check
        news_ctx: Optional news/event context for AI advisory narrative (never affects checklist)

    Returns:
        Formatted message string for AI analysis
    """
    lines = []

    _signal_proxy = dict(engine_a_ctx or {})
    _signal_proxy.update({
        "pair": pair,
        "display": pair,
        "direction": direction,
        "price": current_price,
        "engine_b": structure_result,
        "engine_b_overlay": structure_result,
    })

    try:
        from ai_context import build_ai_calibration_context_string
        lines.append(build_ai_calibration_context_string(_signal_proxy, engine_source="Engine B naked market structure"))
        lines.append("")
    except Exception:
        pass

    # === ENGINE A CROSS-CHECK (only when compare mode) ===
    if engine_a_ctx and isinstance(engine_a_ctx, dict):
        a_dir = engine_a_ctx.get("direction", "?")
        a_score = _get_present_value(engine_a_ctx, "confluenceScore", "score", default=0)
        a_max = _get_present_value(engine_a_ctx, "maxScore", "max_score", default=3.0)
        a_pct = _get_present_value(engine_a_ctx, "confluencePct")
        if a_pct is None:
            a_pct = round((a_score / a_max * 100) if a_max else 0)
        a_trend = engine_a_ctx.get("trendState") or engine_a_ctx.get("trend_state", "?")
        a_sl = engine_a_ctx.get("sl")
        a_tp = engine_a_ctx.get("tp1")
        a_entry = engine_a_ctx.get("price", current_price)
        agree = a_dir == direction
        lines.append("=== ENGINE A CROSS-CHECK ===")
        lines.append(
            f"Engine A Direction: {a_dir} | Agreement: {'YES ✓' if agree else 'NO — CONFLICT ✗'}"
        )
        lines.append(f"Engine A Confluence: {a_score:.2f} / {a_max} ({a_pct}%)")
        lines.append(f"Engine A Trend State: {a_trend}")
        lines.append(f"Engine A Entry: {a_entry} | SL: {a_sl} | TP1: {a_tp}")
        if not agree:
            lines.append(
                "WARNING: Engines disagree on direction — higher risk, comment explicitly."
            )
        lines.append("")

    # === SIGNAL ===
    conf_score = confidence_result.get("score", 0)
    max_score = confidence_result.get("max_possible", 5.0)
    score_pct = round((conf_score / max_score * 100)) if max_score else 0

    lines.append("=== ENGINE B SIGNAL (NAKED STRUCTURE) ===")
    lines.append(f"Pair: {pair} | Direction: {direction} | Price: {current_price}")
    lines.append(f"Confidence: {conf_score:.2f} / {max_score} ({score_pct}%)")
    lines.append(f"Verdict: {structure_result.get('structural_verdict', 'UNCLEAR')}")
    lines.append(f"Actionable: {'YES' if confidence_result.get('passed', False) else 'NO'}")

    # === STRUCTURE ===
    lines.append("")
    lines.append("=== MARKET STRUCTURE ===")

    # Swing sequences
    lines.append(
        f"H1 Swing: {structure_result.get('current_swing_sequence', 'RANGING')}"
    )
    lines.append(f"H4 Swing: {structure_result.get('macro_swing_sequence', 'RANGING')}")

    # BOS and sweeps
    bos = structure_result.get("bos_data", {})
    sweep = structure_result.get("sweep_data", {})
    lines.append(
        f"Break of Structure: Bull={bos.get('bos_bull', False)} Bear={bos.get('bos_bear', False)}"
    )
    lines.append(
        f"Liquidity Sweep: Bull={sweep.get('bull_sweep', False)} Bear={sweep.get('bear_sweep', False)}"
    )

    # FVG
    fvg_overlap = structure_result.get("fvg_overlap", False)
    lines.append(f"Fair Value Gap overlap at key zone: {fvg_overlap}")

    # === LEVELS ===
    lines.append("")
    lines.append("=== STRUCTURAL LEVELS ===")

    res_zone = structure_result.get("nearest_resistance_zone")
    sup_zone = structure_result.get("nearest_support_zone")

    if res_zone:
        lines.append(
            f"Resistance: {res_zone.get('lower', 0):.6f} - {res_zone.get('upper', 0):.6f}"
        )
    else:
        lines.append("Resistance: None detected")

    if sup_zone:
        lines.append(
            f"Support: {sup_zone.get('lower', 0):.6f} - {sup_zone.get('upper', 0):.6f}"
        )
    else:
        lines.append("Support: None detected")

    dist_res = structure_result.get("distance_to_res", 0)
    dist_sup = structure_result.get("distance_to_sup", 0)
    lines.append(f"Distance to Resistance: {float(dist_res or 0):.6f}")
    lines.append(f"Distance to Support: {float(dist_sup or 0):.6f}")

    # === TRADE PARAMETERS ===
    lines.append("")
    lines.append("=== TRADE PARAMETERS ===")
    sl = structure_result.get("recommended_stop_loss")
    tp = structure_result.get("recommended_take_profit")
    rr = 0.0
    if sl is not None and tp is not None:
        sl_dist = abs(float(current_price) - float(sl))
        tp_dist = abs(float(tp) - float(current_price))
        rr = (tp_dist / sl_dist) if sl_dist > 0 else 0.0

    lines.append(f"Entry: {current_price}")
    lines.append(f"Stop Loss: {sl}")
    lines.append(f"Take Profit: {tp}")
    lines.append(f"Risk:Reward: 1:{rr:.2f}")

    # === CONFIDENCE BREAKDOWN ===
    lines.append("")
    lines.append("=== CONFIDENCE BREAKDOWN ===")
    lines.append(f"Structure Score: {confidence_result.get('struct_points', 0):.2f}")
    lines.append(f"Room Score: {confidence_result.get('room_points', 0):.2f}")
    lines.append(f"RR Score: {confidence_result.get('rr_points', 0):.2f}")
    lines.append(f"Catalyst Bonus: {confidence_result.get('catalyst_bonus', 0):.2f}")
    _ai_adj = confidence_result.get("ai_adjustment")
    try:
        _ai_adj_f = float(_ai_adj) if _ai_adj is not None else 0.0
    except (TypeError, ValueError):
        _ai_adj_f = 0.0
    if _ai_adj_f:
        lines.append(f"AI Adjustment: {_ai_adj_f:.2f}")

    # === LEARNING CONTEXT ===
    if learning_ctx and learning_ctx.get("sample_size", 0) >= 5:
        lines.append("")
        lines.append("=== LEARNING CONTEXT (from live outcomes) ===")

        pair_stats = learning_ctx.get("pair_stats")
        if pair_stats:
            lines.append(
                f"This pair history: {pair_stats['win_rate'] * 100:.0f}% WR over "
                f"{pair_stats['total_trades']} trades (avg {pair_stats['avg_r']:+.2f}R)"
            )

        asset_stats = learning_ctx.get("asset_type_stats")
        if asset_stats:
            lines.append(
                f"Asset class: {asset_stats['win_rate'] * 100:.0f}% WR over "
                f"{asset_stats['total_trades']} trades"
            )

        # Recent failures
        recent_fails = learning_ctx.get("recent_failures", [])
        if recent_fails:
            fail_strs = [
                f"{f.get('pair', '?')} {f.get('grade', '?')} {f.get('r', 0):+.1f}R"
                if isinstance(f, dict)
                else str(f)
                for f in recent_fails[:3]
            ]
            lines.append(f"Recent failures: {', '.join(fail_strs)}")

    # === NEWS / EVENT CONTEXT (advisory only — does not affect pass/fail) ===
    if news_ctx and isinstance(news_ctx, dict):
        lines.append("")
        lines.append("=== NEWS / EVENT CONTEXT ===")
        lines.append("NOTE: This context is for your advisory review only.")
        lines.append("Engine B pass/fail is already decided by price-action checklist.")
        lines.append("Use this to add warnings, timing notes, or risk context to your narrative.")
        lines.append("")

        # Economic events (high-impact: NFP, CPI, FOMC, rate decisions)
        econ = news_ctx.get("economic_events") or news_ctx.get("events") or news_ctx.get("forexEvents") or []
        if econ:
            lines.append("Upcoming economic events:")
            for ev in econ[:5]:  # cap at 5 most relevant
                ev_name = ev.get("name") or ev.get("event") or "Unknown"
                ev_time = ev.get("time") or ev.get("date") or ""
                ev_impact = ev.get("impact") or ev.get("importance") or ""
                ev_currency = ev.get("currency") or ev.get("country") or ""
                lines.append(f"  - {ev_name} | {ev_time} | Impact: {ev_impact} | {ev_currency}")
            lines.append("")

        # Pair-specific sentiment
        sentiment = news_ctx.get("sentiment") or news_ctx.get("pair_sentiment")
        if not sentiment and news_ctx.get("pairSentiment"):
            pair_sent = news_ctx["pairSentiment"]
            sentiment = pair_sent.get(pair) if isinstance(pair_sent, dict) else None
        if sentiment:
            if isinstance(sentiment, dict):
                s_score = sentiment.get("score") or sentiment.get("value") or "N/A"
                s_label = sentiment.get("label") or sentiment.get("sentiment") or ""
                lines.append(f"Pair sentiment: {s_label} ({s_score})")
            elif isinstance(sentiment, (int, float)):
                lines.append(f"Pair sentiment score: {sentiment}")
            lines.append("")

        # Market headlines (top 3)
        headlines = (
            news_ctx.get("headlines")
            or news_ctx.get("news")
            or news_ctx.get("marketNews")
            or []
        )
        if headlines:
            lines.append("Recent market headlines:")
            for hl in headlines[:3]:
                if isinstance(hl, dict):
                    title = hl.get("title") or hl.get("headline") or str(hl)
                    lines.append(f"  - {title}")
                elif isinstance(hl, str):
                    lines.append(f"  - {hl}")
            lines.append("")

        # Crypto-specific news (only for crypto pairs)
        crypto_news = news_ctx.get("crypto_news") or news_ctx.get("crypto") or news_ctx.get("cryptoNews") or []
        if crypto_news:
            lines.append("Crypto-specific news:")
            for cn in crypto_news[:3]:
                if isinstance(cn, dict):
                    title = cn.get("title") or cn.get("headline") or str(cn)
                    lines.append(f"  - {title}")
                elif isinstance(cn, str):
                    lines.append(f"  - {cn}")
            lines.append("")

    # === CANDLE DATA FRESHNESS ===
    try:
        from ai_utils import build_freshness_ai_context as _build_freshness
        _signal_proxy = freshness_ctx or {}
        _freshness_str = _build_freshness(_signal_proxy)
        if _freshness_str:
            lines.append("")
            lines.append(_freshness_str)
    except Exception:
        pass

    return "\n".join(lines)


def get_engine_b_ai_verdict(
    pair: str,
    direction: str,
    current_price: float,
    structure_result: dict,
    confidence_result: dict,
    learning_ctx: Optional[dict] = None,
    xai_api_key: Optional[str] = None,
    xai_model: Optional[str] = None,
    engine_a_ctx: Optional[dict] = None,
    news_ctx: Optional[dict] = None,
    freshness_ctx: Optional[dict] = None,
    asset_type: Optional[str] = None,
) -> dict:
    """
    Get AI analysis for Engine B signal using the configured AI provider.

    Returns dict with:
        - grade: A+ to F
        - edgeProbability: 0-100
        - riskLevel: LOW/MEDIUM/HIGH
        - verdict: text analysis
        - error: if failed
    """
    runtime = resolve_ai_review_runtime(CONFIG)
    active_provider = str(runtime.get("provider") or runtime.get("selectedProvider") or "grok")
    api_key = str(xai_api_key or "").strip()
    if not api_key:
        log.info("[ENGINE_B_AI] AI API key not provided, skipping AI analysis")
        return {
            "error": "API key not configured",
            "provider": active_provider,
            "selectedProvider": runtime.get("selectedProvider") or active_provider,
            "fallbackUsed": False,
        }

    try:
        client = create_ai_client(CONFIG, api_key=api_key, provider=active_provider)
        model_override = None if runtime.get("fallbackUsed") else xai_model
        model = str(model_override or get_ai_model(CONFIG, "AI_MODEL", provider=active_provider)).strip()

        message = build_engine_b_signal_message(
            pair,
            direction,
            current_price,
            structure_result,
            confidence_result,
            learning_ctx,
            engine_a_ctx=engine_a_ctx,
            news_ctx=news_ctx,
            freshness_ctx=freshness_ctx,
        )

        cross_engine_note = (
            (
                " When Engine A context is present: say whether Engine A momentum quality "
                "(nondirectionalScore, directionalConfidenceMultiplier if given) supports this structural trade; "
                "whether Engine A's headline score is consistent with the zone and structure quality you see in the input; "
                "and explicitly which engine you trust more for THIS setup and why — not only whether they agree."
            )
            if engine_a_ctx
            else ""
        )

        expert_prompt = (
            "You are Marcus Reid, veteran SMC/ICT structural trader analyzing naked price action setups. "
            "Focus on structure and liquidity evidence: swing alignment, BOS, sweeps, FVG overlap, zone quality, "
            "trigger quality, and risk:reward. "
            "Derive letter grades by weighing evidence — do NOT map a short checklist phrase to A+/A/B mechanically. "
            "Evaluate the trade setup based on the 'Resolved AI style' and 'Asset type' provided in the AI CALIBRATION CONTEXT. "
            "Do NOT judge a Scalp setup by Swing criteria (or vice versa). "
            "Evaluate Risk:Reward per style rules (SCALP: RR >= 1.5 acceptable; INTRADAY: RR >= 2.0 preferred; SWING: RR >= 3.0 preferred). "
            "Do not automatically penalize Crypto for wide SL unless it exceeds MAX_SL_PCT. "
            + cross_engine_note
            + " Weigh: overall structural conviction; distance to boundary; "
            "trigger quality; multi-TF alignment (explicit Y/N with evidence from available swing sequences). "
            "Rate all three styles independently: SCALP(H1, 1.5-2R), INTRADAY(H4, 2-3R), SWING(D1, 3-6R). "
            "Top-level grade/edgeProbability/riskLevel must represent the best style. "
            "Output strict JSON only with exactly these top-level keys in this precise order: "
            "reasoning, verdict, reviewSource, resolvedStyle, bestValidStyle, grade, edgeProbability, riskLevel, style_ratings. "
            "style_ratings must contain scalp, intraday, and swing objects with grade, edgeProbability, riskLevel."
        )
        _temp = float(AITemperatureConfig.get_temperature("engine_b_ai"))

        parsed_dict, raw_text = _call_ai_with_retry(
            client,
            model=model,
            messages=[
                {"role": "system", "content": expert_prompt},
                {"role": "user", "content": message},
            ],
            temperature=_temp,
        )

        if parsed_dict is not None:
            parsed = parsed_dict
        else:
            parsed = parse_json_object(raw_text or "")

        if parsed is None:
            log.error(f"[ENGINE_B_AI] {pair}: Failed to parse JSON from AI response")
            return {"error": "Invalid AI response format"}

        parsed = _normalise_engine_b_ai_payload(parsed)
        parsed.setdefault("provider", active_provider)
        parsed.setdefault("selectedProvider", runtime.get("selectedProvider") or active_provider)
        parsed.setdefault("model", model)
        parsed.setdefault("fallbackUsed", bool(runtime.get("fallbackUsed")))
        if runtime.get("providerFailure"):
            parsed.setdefault("providerFailure", runtime.get("providerFailure"))

        # Validate required keys
        required = {"grade", "edgeProbability", "riskLevel", "verdict", "reviewSource"}
        missing = required - set(parsed.keys())
        if missing:
            log.warning(f"[ENGINE_B_AI] {pair}: Missing keys {missing} in AI response")
            parsed.setdefault("grade", "C")
            parsed.setdefault("edgeProbability", 50)
            parsed.setdefault("riskLevel", "Medium")
            parsed.setdefault("reviewSource", "engine_b_marcus")
            parsed.setdefault("verdict", "Model response missing fields; using safe defaults.")


        parsed = _validate_engine_b_ai_payload(parsed, pair)
        parsed["grade"] = _normalise_engine_b_grade(parsed.get("grade"), pair)

        log.info(
            f"[ENGINE_B_AI] {pair} => Grade:{parsed.get('grade', '?')} "
            f"Prob:{parsed.get('edgeProbability', '?')}% Risk:{parsed.get('riskLevel', '?')}"
        )

        try:
            from ai_review_logger import (
                log_ai_review,
                map_engine_b_grade_to_ai_state,
                REVIEW_TYPE_ENGINE_B_AI,
            )
            from prompt_versions import get_prompt_version

            _freshness_reason = (
                (freshness_ctx or {}).get("dataFreshness", {}) or {}
            ).get("reason") or "unknown"
            log_ai_review(
                symbol=pair,
                asset_type=str(
                    asset_type
                    or (engine_a_ctx.get("type") if isinstance(engine_a_ctx, dict) else None)
                    or "unknown"
                ),
                review_type=REVIEW_TYPE_ENGINE_B_AI,
                model=model,
                provider=active_provider,
                prompt_version=get_prompt_version("engine_b_ai"),
                input_packet={
                    "pair": pair,
                    "direction": direction,
                    "system_prompt": expert_prompt,
                    "user_message": message,
                },
                has_chart_image=False,
                candle_freshness_status=_freshness_reason,
                engine_a_state=(engine_a_ctx or {}).get("confluenceScore") if isinstance(engine_a_ctx, dict) else None,
                engine_b_state=confidence_result.get("pct"),
                engine_c_state=None,
                engine_d_state=None,
                risk_state=None,
                ai_review_state=map_engine_b_grade_to_ai_state(parsed.get("grade", "")),
                ai_confidence=parsed.get("edgeProbability"),
                contradictions_count=0,
                missing_information_count=0,
                parse_success=True,
                schema_valid=not bool(missing),
                execution_allowed_before_ai=True,
                execution_allowed_after_ai=True,
                final_action="advisory",
                trace_id=(
                    (engine_a_ctx or {}).get("trace_id")
                    if isinstance(engine_a_ctx, dict)
                    else None
                ),
            )
        except Exception as _log_err:
            log.debug("[AI_AUDIT] Engine B AI audit log failed: %s", _log_err)

        return parsed

    except Exception as e:
        err_text = str(e).strip() or e.__class__.__name__
        if "timed out" in err_text.lower() or "timeout" in err_text.lower():
            err_text = "AI request timed out after retry attempts"
        log.error(f"[ENGINE_B_AI] {pair}: AI analysis failed - {err_text}")
        return {"error": err_text}
