"""Prompt builders for chart-vision analysis.

These builders keep the parser footer contract stable while enforcing
structured right-edge candle reading rules. The free-form prose body
(TRADE SNAPSHOT / BULLISH FACTORS / FINAL VERDICT, etc.) was removed
because only the structured footer is consumed downstream — the body
generated 500-700 tokens per call with no parsed signal.
"""

from __future__ import annotations


def _request_metadata(
    symbol: str,
    direction_str: str,
    *,
    tf: str | None = None,
    frames: str | None = None,
) -> str:
    """Stable request metadata so prompts are anchored even when algo_context is sparse."""
    lines = [
        f"REQUEST_METADATA: symbol={symbol}",
        f"REQUEST_METADATA: direction_bias={direction_str}",
    ]
    if tf:
        lines.append(f"REQUEST_METADATA: primary_chart_tf={tf}")
    if frames:
        lines.append(f"REQUEST_METADATA: chart_frames={frames}")
    return "\n".join(lines)


_STRUCTURED_FOOTER = (
    "RIGHT EDGE: <CONFIRMS|REVIEW|POTENTIAL REVERSAL>\n"
    "TF ALIGNMENT: ALIGNED or CONFLICTED\n"
    "SCALP RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
    "SCALP LEVELS: SL=<KEEP|price> TP=<KEEP|price>\n"
    "INTRADAY RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
    "INTRADAY LEVELS: SL=<KEEP|price> TP=<KEEP|price>\n"
    "SWING RATING: <STRONG|MODERATE|WEAK|AVOID|CONTRADICTS>\n"
    "SWING LEVELS: SL=<KEEP|price> TP=<KEEP|price>"
)

_VISION_TRADE_READ_JSON = (
    "VISION_TRADE_READ_JSON (place before footer, valid compact JSON):\n"
    "{\"schema_version\":\"vision_trade_read.v1\","
    "\"right_edge_status\":\"CONFIRMS|REVIEW|POTENTIAL_REVERSAL|UNKNOWN\","
    "\"tf_alignment\":\"ALIGNED|CONFLICTED|UNKNOWN\","
    "\"confirms_direction\":true,"
    "\"chart_identity_confidence\":\"confirmed|from_request|unclear\","
    "\"freshness_status\":\"unknown\","
    "\"allowed_for_execution_context\":false,"
    "\"last_candle_read\":{\"body_size\":\"\",\"close_location\":\"upper_third|middle|lower_third|unknown\",\"upper_wick\":\"\",\"lower_wick\":\"\",\"read\":\"\"},"
    "\"last_3_5_candles\":{\"sequence\":\"\",\"auction_behavior\":\"\",\"momentum_state\":\"\"},"
    "\"visible_structure\":{\"support\":[],\"resistance\":[],\"trendline_or_channel\":\"\",\"obstacles_before_tp\":[]},"
    "\"style_ratings\":{\"scalp\":\"STRONG|MODERATE|WEAK|AVOID|CONTRADICTS|NA\",\"intraday\":\"STRONG|MODERATE|WEAK|AVOID|CONTRADICTS|NA\",\"swing\":\"STRONG|MODERATE|WEAK|AVOID|CONTRADICTS|NA\"},"
    "\"level_suggestions\":{\"sl\":{\"action\":\"KEEP|ADJUST|UNKNOWN\",\"suggested\":null,\"reason\":\"\"},\"tp1\":{\"action\":\"KEEP|ADJUST|UNKNOWN\",\"suggested\":null,\"reason\":\"\"}},"
    "\"memo\":\"concise auditable visible-chart memo\","
    "\"missing_visual_information\":[],\"warnings\":[]}"
)


def _ae_framework(authority_tf: str) -> str:
    return (
        f"RIGHT EDGE A-H FRAMEWORK (authoritative TF: {authority_tf}):\n"
        "A. Regime + Location: regime state, ADX if known, distance to POC/VAH/VAL/named liquidity.\n"
        "B. Last Candle Anatomy: body %, close location, wick dominance (use structured facts if provided).\n"
        "C. Last 3/5 Candle Sequence: expansion, contraction, control shift, higher/lower closes.\n"
        "D. Pattern ID: Name visible/OHLC-supported pattern ONLY if valid.\n"
        "E. Auction Behaviour: Rejection, absorption, liquidity sweep, failed breakout, displacement.\n"
        "F. Confirmation: Volume, level reaction, structure, indicator context.\n"
        "G. Style Suitability (entry quality): Rate scalp, intraday, and swing suitability separately, "
        "considering whether entry is tactical or chasing congestion.\n"
        "H. Verdict: Map your read directly to RIGHT EDGE classification (CONFIRMS / REVIEW / POTENTIAL REVERSAL).\n"
    )


def build_system_prompt() -> str:
    return (
        "You are a professional market-structure analyst reviewing chart screenshots.\n"
        "You are advisory-only: no guarantees, no certainty language.\n\n"
        "WORKFLOW (mandatory order):\n"
        "1. Read the chart image(s) first.\n"
        "2. Read instrument/timeframe from visible chart UI; if unreadable, use request metadata only.\n"
        "3. If CANDLE_UNDERSTANDING structured facts are present in algorithmic context, use them "
        "as authoritative for regime, location, anatomy, sweep, and effort-vs-result — do NOT invent "
        "candle patterns when structured facts exist.\n"
        "4. Candle read order: regime → location → last 3 anatomy → sweep/BOS/FVG/OB → volume/effort "
        "→ directional bias (advisory only).\n"
        "5. Cross-check algorithmic context after the visual read.\n"
        "6. If chart facts conflict with algorithmic context, chart evidence is authoritative.\n\n"
        "ABSOLUTE RULES:\n"
        "1. Only describe what is visible in image/context. If unclear, say not clearly visible.\n"
        "2. Do not invent patterns, levels, or indicators.\n"
        "3. Use exact prices from visible axis/overlays; use context prices only when unreadable on chart.\n"
        "4. Keep output concise and structured.\n"
        "5. Output ONLY the A-H framework reasoning, VISION_TRADE_READ_JSON, and the structured footer — nothing else.\n"
        "6. The JSON is advisory only and must set allowed_for_execution_context=false unless timestamps are explicit in request/context.\n\n"
        "CANDLE CONTEXT RULES:\n"
        "1. Regime first: trending, ranging, or unknown — counter-trend candles are noise unless at a named level.\n"
        "2. Location second: VAH/VAL/POC, session H/L, PDH/PDL, OB/FVG edges — wicks away from levels are noise.\n"
        "3. Last 3 candle anatomy: body/wick ratios, rejection vs displacement — not pattern names alone.\n"
        "4. SMC context: confirmed sweep requires named pool + reclaim; distinguish from clean BOS acceptance.\n"
        "5. Volume/effort-vs-result: high effort + small result suggests absorption at qualified levels only.\n"
        "6. Directional view last: advisory only; never grant execution permission from candle facts alone.\n"
    )


def build_single_prompt(
    *,
    symbol: str,
    tf: str,
    direction_str: str,
    algo_context: str,
    asset_type: str,
) -> str:
    return (
        "The chart screenshot is attached above this message.\n\n"
        f"{_request_metadata(symbol, direction_str, tf=tf)}\n\n"
        "STEP 1 - VISUAL READ FIRST:\n"
        "- Read instrument/timeframe from chart UI. If unreadable after checking labels, write exactly "
        "'chart label not legible - from request' and use request symbol/timeframe only.\n"
        "- Build a context-first read: trend structure, then candle behavior, then confirmation.\n\n"
        "STEP 2 - ALGORITHMIC CONTEXT (cross-check after STEP 1; image wins on conflict):\n"
        f"{algo_context}\n\n"
        f"CONTEXT: asset={asset_type.upper()}\n\n"
        f"{_ae_framework(tf)}\n"
        f"{_VISION_TRADE_READ_JSON}\n\n"
        "End with exactly these 8 lines, with nothing after:\n"
        f"{_STRUCTURED_FOOTER}\n"
    )


def build_dual_prompt(
    *,
    symbol: str,
    direction_str: str,
    algo_context: str,
    asset_type: str,
) -> str:
    return (
        "Two chart images are attached above this text: IMAGE 1 = D1, IMAGE 2 = H4.\n\n"
        f"{_request_metadata(symbol, direction_str, frames='D1+H4')}\n\n"
        "STEP 1 - VISUAL READ FIRST:\n"
        "- Read instrument/timeframe from chart labels on both images.\n"
        "- If unreadable after checking labels, write exactly 'chart label not legible - from request' and use request metadata.\n"
        "- D1 is macro bias. H4 is tactical structure and authoritative RIGHT EDGE timeframe.\n\n"
        "STEP 2 - ALGORITHMIC CONTEXT (cross-check after STEP 1; image wins on conflict):\n"
        f"{algo_context}\n\n"
        f"CONTEXT: asset={asset_type.upper()}\n\n"
        f"{_ae_framework('H4')}\n"
        "Multi-TF rules:\n"
        "- D1 sets directional bias; H4 decides tactical validity.\n"
        "- If H4 right edge does not confirm direction, classify RIGHT EDGE as POTENTIAL REVERSAL or REVIEW (never CONFIRMS).\n"
        "- Note nearest obstacle between entry and TP first.\n\n"
        f"{_VISION_TRADE_READ_JSON}\n\n"
        "End with exactly these 8 lines, with nothing after:\n"
        f"{_STRUCTURED_FOOTER}\n"
    )


def build_triple_prompt(
    *,
    symbol: str,
    direction_str: str,
    algo_context: str,
    asset_type: str,
) -> str:
    return (
        "Three chart images are attached above this text: IMAGE 1 = D1, IMAGE 2 = H4, IMAGE 3 = H1.\n\n"
        f"{_request_metadata(symbol, direction_str, frames='D1+H4+H1')}\n\n"
        "STEP 1 - VISUAL READ FIRST:\n"
        "- Read instrument/timeframe from chart labels on all images.\n"
        "- If unreadable after checking labels, write exactly 'chart label not legible - from request' and use request metadata only.\n"
        "- D1 is macro bias. H4 is tactical structure. H1 is authoritative RIGHT EDGE timeframe.\n\n"
        "STEP 2 - ALGORITHMIC CONTEXT (cross-check after STEP 1; image wins on conflict):\n"
        f"{algo_context}\n\n"
        f"CONTEXT: asset={asset_type.upper()}\n\n"
        f"{_ae_framework('H1')}\n"
        "Multi-TF rules:\n"
        "- D1 sets macro bias, H4 validates path/obstacles, H1 validates trigger quality.\n"
        "- If H1 right edge shows counter-trend with rising volume, classify POTENTIAL REVERSAL.\n"
        "- If EMA reclaim against trade is visible on H1, RIGHT EDGE cannot be CONFIRMS.\n\n"
        f"{_VISION_TRADE_READ_JSON}\n\n"
        "End with exactly these 8 lines, with nothing after:\n"
        f"{_STRUCTURED_FOOTER}\n"
    )
