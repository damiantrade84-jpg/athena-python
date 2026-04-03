"""Prompt builders for chart-vision analysis.

These builders keep the parser footer contract stable while enforcing
structured right-edge candle reading rules.
"""

from __future__ import annotations


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


def _ae_framework(authority_tf: str) -> str:
    return (
        f"RIGHT EDGE A-E FRAMEWORK (authoritative TF: {authority_tf}):\n"
        "A. Pattern ID: Name visible candle pattern(s) and structural location "
        "(at support/resistance, order block, FVG edge, BOS/CHoCH retest, range boundary).\n"
        "B. Wick Analysis: State wick direction, wick-to-body ratio, and interpretation "
        "(rejection, absorption, liquidity grab, or EMA test).\n"
        "C. Body Conviction: State whether candle bodies are expanding, contracting, or alternating, "
        "and what that implies for momentum.\n"
        "D. Sequence Narrative: The first sentence of RIGHT EDGE MUST synthesize A+B+C in one line.\n"
        "E. Candle Rules: Engulfing calls require explicit volume confirmation. "
        "Counter-trend move plus rising volume must map to RIGHT EDGE: POTENTIAL REVERSAL.\n"
    )


def build_system_prompt() -> str:
    return (
        "You are a professional market-structure analyst reviewing chart screenshots.\n"
        "You are advisory-only: no guarantees, no certainty language.\n\n"
        "WORKFLOW (mandatory order):\n"
        "1. Read the chart image(s) first.\n"
        "2. Read instrument/timeframe from visible chart UI; if unreadable, use request metadata only.\n"
        "3. Read right-edge candles on the authoritative timeframe and interpret momentum/control.\n"
        "4. Cross-check algorithmic context after the visual read.\n"
        "5. If chart facts conflict with algorithmic context, chart evidence is authoritative.\n\n"
        "ABSOLUTE RULES:\n"
        "1. Only describe what is visible in image/context. If unclear, say not clearly visible.\n"
        "2. Do not invent patterns, levels, or indicators.\n"
        "3. Use exact prices from visible axis/overlays; use context prices only when unreadable on chart.\n"
        "4. Keep output concise and structured.\n"
        "5. Keep the final parser footer exactly as requested.\n\n"
        "CANDLE CONTEXT RULES:\n"
        "1. Trend structure first: higher highs/higher lows, lower highs/lower lows, or range.\n"
        "2. Candle anatomy next: body size, wick length, close location, and visible gaps.\n"
        "3. Pattern context: reversal candles only count in valid context; doji/spinning tops are indecision unless confirmed.\n"
        "4. Continuation logic: pauses/consolidations are potential continuation zones only after breakout confirmation.\n"
        "5. Confirmation rule: use volume/levels/other visible evidence and explicitly guard against fakeouts.\n"
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
        "STEP 1 - VISUAL READ FIRST:\n"
        "- Read instrument/timeframe from chart UI. If unreadable after checking labels, write exactly "
        "'chart label not legible - from request' and use request symbol/timeframe only.\n"
        "- Build a context-first read: trend structure, then candle behavior, then confirmation.\n\n"
        "STEP 2 - ALGORITHMIC CONTEXT (cross-check after STEP 1; image wins on conflict):\n"
        f"{algo_context}\n\n"
        f"CONTEXT: asset={asset_type.upper()} symbol={symbol} timeframe={tf} algorithmic_direction={direction_str}\n\n"
        "Use this exact body order:\n"
        "TRADE SNAPSHOT\n"
        "MARKET STRUCTURE\n"
        "RIGHT EDGE\n"
        "BULLISH FACTORS\n"
        "BEARISH FACTORS\n"
        "KEY RISKS\n"
        "ENTRY QUALITY\n"
        "FINAL VERDICT\n"
        "ACTIONABLE IMPROVEMENT\n\n"
        f"{_ae_framework(tf)}\n"
        "ENTRY QUALITY rules:\n"
        "- State whether entry is tactical (pullback/retest) or chasing into congestion.\n"
        "- Flag low-vol breakout/breakdown risk when regime text indicates low volatility.\n"
        "- Verify RR from visible entry/SL/TP distances.\n\n"
        "FINAL VERDICT rules:\n"
        "- Use HOLD / ADJUST / CLOSE.\n"
        "- HOLD is allowed only when RIGHT EDGE confirms direction.\n\n"
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
        "STEP 1 - VISUAL READ FIRST:\n"
        "- Read instrument/timeframe from chart labels on both images.\n"
        "- If unreadable after checking labels, write exactly 'chart label not legible - from request' and use request metadata.\n"
        "- D1 is macro bias. H4 is tactical structure and authoritative RIGHT EDGE timeframe.\n\n"
        "STEP 2 - ALGORITHMIC CONTEXT (cross-check after STEP 1; image wins on conflict):\n"
        f"{algo_context}\n\n"
        f"CONTEXT: asset={asset_type.upper()} symbol={symbol} algorithmic_direction={direction_str}\n\n"
        "Use this exact body order:\n"
        "TRADE SNAPSHOT\n"
        "MARKET STRUCTURE\n"
        "RIGHT EDGE\n"
        "BULLISH FACTORS\n"
        "BEARISH FACTORS\n"
        "KEY RISKS\n"
        "ENTRY QUALITY\n"
        "FINAL VERDICT\n"
        "ACTIONABLE IMPROVEMENT\n\n"
        f"{_ae_framework('H4')}\n"
        "Multi-TF rules:\n"
        "- D1 sets directional bias; H4 decides tactical validity.\n"
        "- If H4 right edge does not confirm direction, FINAL VERDICT cannot be HOLD.\n"
        "- Note nearest obstacle between entry and TP first.\n\n"
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
        "STEP 1 - VISUAL READ FIRST:\n"
        "- Read instrument/timeframe from chart labels on all images.\n"
        "- If unreadable after checking labels, write exactly 'chart label not legible - from request' and use request metadata only.\n"
        "- D1 is macro bias. H4 is tactical structure. H1 is authoritative RIGHT EDGE timeframe.\n\n"
        "STEP 2 - ALGORITHMIC CONTEXT (cross-check after STEP 1; image wins on conflict):\n"
        f"{algo_context}\n\n"
        f"CONTEXT: asset={asset_type.upper()} symbol={symbol} algorithmic_direction={direction_str}\n\n"
        "Use this exact body order:\n"
        "TRADE SNAPSHOT\n"
        "MARKET STRUCTURE\n"
        "RIGHT EDGE\n"
        "BULLISH FACTORS\n"
        "BEARISH FACTORS\n"
        "KEY RISKS\n"
        "ENTRY QUALITY\n"
        "FINAL VERDICT\n"
        "ACTIONABLE IMPROVEMENT\n\n"
        f"{_ae_framework('H1')}\n"
        "Multi-TF rules:\n"
        "- D1 sets macro bias, H4 validates path/obstacles, H1 validates trigger quality.\n"
        "- If H1 right edge shows counter-trend with rising volume, classify POTENTIAL REVERSAL and verdict cannot be HOLD.\n"
        "- If EMA reclaim against trade is visible on H1, HOLD is not allowed.\n\n"
        "End with exactly these 8 lines, with nothing after:\n"
        f"{_STRUCTURED_FOOTER}\n"
    )

