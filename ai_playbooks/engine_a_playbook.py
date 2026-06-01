"""Engine A confluence/context trade playbook."""

from __future__ import annotations

from ai_playbooks.contracts import PLAYBOOK_SCHEMA_VERSION


def get_engine_a_playbook() -> dict:
    return {
        "schemaVersion": PLAYBOOK_SCHEMA_VERSION,
        "engine": "A",
        "name": "Engine A Confluence Context Review",
        "reviewOrder": "Confluence -> Factor Alignment -> Direction Quality -> Entry Timing -> Decision",
        "principles": [
            "Review confluence and factor alignment before approving entry.",
            "Ground every judgment in the supplied engineAContext.diagnostics indicators, not a freehand chart read; see indicatorUsage.",
            "High Engine A score does not imply entry is acceptable now.",
            "Distinguish direction valid from entry timing poor.",
            "Acceptable timing is a valid and common outcome: a confirmed BOS with acceptance/retest, a pullback to structure, or a breakout retest are tradeable continuation entries, not chasing.",
            "Downgrade timing only on concrete evidence — measure price (diagnostics.entry) distance from diagnostics.ema50/ema200 in ATR units (diagnostics.atrH4) plus momentum (rsi, adx); for crypto also use vwapExtended/vwapDistanceAtr. Do not downgrade as a reflex because the score passed.",
            "Score high but location/timing poor requires WAIT, not ENTRY_NOW; a signal can be directionally valid while execution waits.",
            "No trade when evidence is weak, conflicted, or missing required context.",
        ],
        "entryModels": [
            "CONFLUENCE_CONTINUATION",
            "PULLBACK_TO_STRUCTURE",
            "BREAKOUT_RETEST",
            "MEAN_REVERSION_AT_VALUE",
            "NO_TRADE",
        ],
        "indicatorUsage": {
            "diagnostics.trendScore": "Trend factor strength in the proposed direction; weak/negative trend contradicts a continuation thesis.",
            "diagnostics.adxD1 / adxH4": "Trend strength / regime. Low ADX = ranging; do not treat range chop as a continuation entry, and prefer mean-reversion logic at value.",
            "diagnostics.momentumScore / rsi": "Momentum confirmation. RSI extreme against direction or momentum divergence is a timing warning, not an automatic reject.",
            "diagnostics.ema50 / ema200 / dema200 (H4 basis)": "Engine A's trend EMAs. Confirm direction matches EMA stacking, and gauge extension by how far diagnostics.entry sits above/below them in ATR units (diagnostics.atrH4) — a large gap with no pullback is the cross-asset extended/late test.",
            "diagnostics.emaTrendPeriod / emaMomentumPeriod / emaLongPeriod / rsiPeriod / rsiTimeframe": "Per-group periods Engine A actually scored with (e.g. forex 26/60, crypto 18/40, rsi 18 vs 12). The chart draws fixed EMA50/EMA200/RSI14 lines that may differ — reconcile against these diagnostics values and emaTimeframe/rsiTimeframe, not the chart's drawn lines. indicatorParity.mismatches with engine_a_ema_periods present means the chart period differs from the scoring period and is expected, not a contradiction.",
            "diagnostics.vwapExtended / vwapDistanceAtr (crypto only)": "Crypto late-trend extension flag: when vwapExtended is true or vwapDistanceAtr is large the entry is extended. Null for non-crypto — use EMA distance + RSI/ADX there instead.",
            "diagnostics.atrD1 / atrH4": "Volatility for SL/TP sizing and room to target; confirm SL is structurally valid relative to ATR.",
            "diagnostics.volumeScore / volumeRatio (volumeType)": "Volume coherence with direction. When volumeType is tick (forex) or mixed (commodity) do NOT penalize on volume.",
            "diagnostics.rr / sl / tp / entry": "Risk geometry; reject when RR is unacceptable after confirmation requirements.",
            "conviction / timeframeBias (D1/H4/H1)": "Directional alignment across timeframes; conflicting timeframes lower tradeability and can justify WAIT.",
            "engineBContext (bosConfirmed, chochConfirmed, liquiditySweep, nearestSupport/Resistance, breakerLevel, structuralVerdict)": "Structure for location and entry-model selection; confirmed BOS/CHoCH with acceptance supports continuation, not chasing.",
            "nonVisualContext / intermarket / newsSentiment": "Explains why Engine A scored the setup. Advisory, non-visual — never reject only because a non-visual driver is not visible on the chart.",
        },
        "strategyMapping": {
            "CONFLUENCE_CONTINUATION": "Trending regime (ADX up) with trendScore and momentum aligned and BOS confirmed in direction; enter on continuation, not after exhaustion.",
            "PULLBACK_TO_STRUCTURE": "Price pulling back toward nearestSupport/Resistance, value, or VWAP in trend direction — preferred timing, not late.",
            "BREAKOUT_RETEST": "Break of structure then retest/acceptance of breakerLevel or zone before continuation.",
            "MEAN_REVERSION_AT_VALUE": "Ranging/balancing regime (low ADX) with RSI extreme at a value edge; counter-trend only with clear rejection.",
            "NO_TRADE": "Conflicted factors, missing required context, or hard invalidation.",
        },
        "invalidations": [
            "Identify what would invalidate the directional thesis.",
            "Check whether SL is structurally valid relative to ATR and structure.",
            "Reject when RR is unacceptable after confirmation requirements.",
        ],
        "mustRejectIf": [
            "Direction valid but entry is measurably extended (far from value/structure with no pullback AND RR degraded), exhausted, or chasing — not merely continuing after a confirmed BOS with acceptance.",
            "Score high but location is poor for the proposed direction.",
            "Factor alignment is conflicted or weak across trend/momentum/volume.",
            "Required context is missing and blocks confident tradeability.",
            "Visual chart contradicts Engine A direction or entry timing.",
        ],
        "requiredOutputFields": [
            "tradeSkillVersion",
            "reviewType",
            "decision",
            "direction",
            "confidence",
            "entryAllowedNow",
            "waitReason",
            "noTradeReason",
            "chartReadSummary",
        ],
    }
