"""
engine_b_ai.py - AI integration for Engine B (Naked Structure Engine)

Adapts Engine A's AI design pattern for Engine B structural analysis.
"""

import logging
from typing import Optional

from ai_utils import parse_json_object
from config import CONFIG, create_ai_client, get_ai_model

log = logging.getLogger("athena")


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


def build_engine_b_signal_message(
    pair: str,
    direction: str,
    current_price: float,
    structure_result: dict,
    confidence_result: dict,
    learning_ctx: Optional[dict] = None,
    engine_a_ctx: Optional[dict] = None,
    news_ctx: Optional[dict] = None,
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
    lines.append(f"AI Adjustment: {confidence_result.get('ai_adjustment', 0):.2f}")

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
    if not xai_api_key:
        log.warning("[ENGINE_B_AI] AI API key not provided, skipping AI analysis")
        return {"error": "API key not configured"}

    try:
        client = create_ai_client(CONFIG, api_key=xai_api_key)

        message = build_engine_b_signal_message(
            pair,
            direction,
            current_price,
            structure_result,
            confidence_result,
            learning_ctx,
            engine_a_ctx=engine_a_ctx,
            news_ctx=news_ctx,
        )

        cross_engine_note = (
            (
                " When Engine A context is present, explicitly comment on whether both engines agree "
                "and how that affects conviction."
            )
            if engine_a_ctx
            else ""
        )

        expert_prompt = (
            "You are Marcus Reid, veteran SMC/ICT structural trader analyzing naked price action setups. "
            "Focus only on structure and liquidity evidence: swing alignment, BOS, sweeps, FVG overlap, zone quality, "
            "trigger quality, and risk:reward."
            + cross_engine_note
            + " Grade rubric: "
            "A+=BOS aligned + clean zone + clear trigger + RR>=2.5 + multi-TF alignment; "
            "A=strong structure with minor weakness and RR>=2.0; "
            "B=tradable but mixed structure or RR>=1.5; "
            "C=marginal/incomplete setup; "
            "D/F=reject."
            " Rate all three styles independently: SCALP(H1, 1.5-2R), INTRADAY(H4, 2-3R), SWING(D1, 3-6R). "
            "Top-level grade/edgeProbability/riskLevel must represent the best style. "
            "Output strict JSON only with exactly these top-level keys: "
            "grade, edgeProbability, riskLevel, verdict, style_ratings. "
            "style_ratings must contain scalp, intraday, and swing objects with grade, edgeProbability, riskLevel."
        )
        _temp = float(CONFIG.get("AI_TEMPERATURE", 0.3))

        completion = client.chat.completions.create(
            model=str(xai_model or get_ai_model(CONFIG, "AI_MODEL")).strip(),
            max_tokens=800,
            temperature=_temp,
            messages=[
                {"role": "system", "content": expert_prompt},
                {"role": "user", "content": message},
            ],
            response_format={"type": "json_object"},
        )

        text = (completion.choices[0].message.content or "").strip()
        parsed = parse_json_object(text)

        if parsed is None:
            log.error(f"[ENGINE_B_AI] {pair}: Failed to parse JSON from AI response")
            return {"error": "Invalid AI response format"}

        parsed = _normalise_engine_b_ai_payload(parsed)

        # Validate required keys
        required = {"grade", "edgeProbability", "riskLevel", "verdict"}
        missing = required - set(parsed.keys())
        if missing:
            log.warning(f"[ENGINE_B_AI] {pair}: Missing keys {missing} in AI response")
            parsed.setdefault("grade", "C")
            parsed.setdefault("edgeProbability", 50)
            parsed.setdefault("riskLevel", "Medium")
            parsed.setdefault("verdict", "Model response missing fields; using safe defaults.")
        parsed["riskLevel"] = str(parsed.get("riskLevel", "Medium")).title()
        parsed["style_ratings"] = parsed.get("style_ratings") or {
            "scalp": {
                "grade": parsed.get("grade", "C"),
                "edgeProbability": parsed.get("edgeProbability", 50),
                "riskLevel": parsed.get("riskLevel", "Medium"),
            },
            "intraday": {
                "grade": parsed.get("grade", "C"),
                "edgeProbability": parsed.get("edgeProbability", 50),
                "riskLevel": parsed.get("riskLevel", "Medium"),
            },
            "swing": {
                "grade": parsed.get("grade", "C"),
                "edgeProbability": parsed.get("edgeProbability", 50),
                "riskLevel": parsed.get("riskLevel", "Medium"),
            },
        }

        log.info(
            f"[ENGINE_B_AI] {pair} => Grade:{parsed.get('grade', '?')} "
            f"Prob:{parsed.get('edgeProbability', '?')}% Risk:{parsed.get('riskLevel', '?')}"
        )

        return parsed

    except Exception as e:
        log.error(f"[ENGINE_B_AI] {pair}: AI analysis failed - {e}")
        return {"error": str(e)}
