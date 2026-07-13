"""Engine A confluence/context trade playbook."""

from __future__ import annotations

from ai_playbooks.contracts import PLAYBOOK_SCHEMA_VERSION


def _live_entry_timeframes() -> dict:
    try:
        from config import CONFIG

        raw = (CONFIG.get("ENGINE_A_SCORING_PROFILE") or {}).get(
            "LIVE_ENTRY_TF_BY_STYLE"
        ) or {}
        return {
            str(style): {str(asset): str(tf).upper() for asset, tf in assets.items()}
            for style, assets in raw.items()
            if isinstance(assets, dict)
        }
    except Exception:
        return {}


def get_engine_a_playbook() -> dict:
    return {
        "schemaVersion": PLAYBOOK_SCHEMA_VERSION,
        "engine": "A",
        "name": "Engine A Confluence Context Review",
        "reviewOrder": "Confluence -> Factor Alignment -> Direction Quality -> Entry Timing -> Decision",
        "principles": [
            "Review confluence and factor alignment before approving entry.",
            "Ground every judgment in the supplied engineAContext.diagnostics and V3 factorScores, not a freehand chart read; see indicatorUsage.",
            "Live Engine A is Engine A V3: four components trend/momentum/location/volume. Do not require legacy directionalScore/activeDirectionalFactors unless supplied.",
            "High Engine A score does not imply entry is acceptable now.",
            "Distinguish direction valid from entry timing poor.",
            "Acceptable timing is a valid and common outcome: a confirmed BOS with acceptance/retest, a pullback to structure, or a breakout retest are tradeable continuation entries, not chasing.",
            "Downgrade timing only on concrete evidence — measure price (diagnostics.entry) distance from diagnostics.ema50/ema200 in ATR units (diagnostics.atrH4) plus momentum (rsi, adx); for crypto also use vwapExtended/vwapDistanceAtr. Do not downgrade as a reflex because the score passed.",
            "Score high but location/timing poor requires WAIT, not ENTRY_NOW; a signal can be directionally valid while execution waits.",
            "No trade when evidence is weak, conflicted, or missing required context.",
            "Use engineAContext.entryTimeframe as the actual live entry/scoring timeframe. D1/H4/H1 remain the confirmed structural trend stack; a configured M15/M30 entry changes timing, location, volume, and trend-health inputs without replacing the higher-timeframe trend layers.",
        ],
        "timeframeContract": {
            "confirmedStructureStack": ["D1", "H4", "H1"],
            "liveEntryTfByStyle": _live_entry_timeframes(),
            "authoritativeFields": [
                "engineAContext.entryTimeframe",
                "engineAContext.entryUsesActiveCandle",
                "engineAContext.activeEntryGate",
            ],
        },
        "entryModels": [
            "CONFLUENCE_CONTINUATION",
            "PULLBACK_TO_STRUCTURE",
            "BREAKOUT_RETEST",
            "MEAN_REVERSION_AT_VALUE",
            "NO_TRADE",
        ],
        "indicatorUsage": {
            "factorScores.trend / factorDiagnostics.components.trend": "V3 trend component strength in the proposed direction; weak/negative trend contradicts a continuation thesis. Legacy alias: diagnostics.trendScore.",
            "factorScores.momentum / factorDiagnostics.components.momentum": "V3 momentum component. RSI extreme against direction or momentum divergence is a timing warning, not an automatic reject. Legacy alias: diagnostics.momentumScore.",
            "factorScores.ortho.location / factorDiagnostics.components.location": "V3 location/value component scored on entryTimeframe. components.location.contribution is its deterministic score contribution; poor location with high confluence still warrants WAIT. Legacy flat alias: diagnostics.locationScore.",
            "factorScores.ortho.volume / factorDiagnostics.components.volume": "V3 volume/flow component scored on entryTimeframe. components.volume.contribution is its deterministic score contribution. When volumeType is tick (forex) or mixed (commodity) do NOT penalize on volume. Legacy flat alias: diagnostics.volumeScore.",
            "factorDiagnostics.components": "Authoritative per-component signal, quality, weight, contribution, and availability. Cite these values; never recompute or mutate deterministic contributions.",
            "entryTimeframe / entryUsesActiveCandle / activeEntryGate": "Actual entry-scoring timeframe and live-bucket proof. For configured lower-TF entries, activeEntryGate.passed=false is a hard timing blocker; never substitute H1 evidence.",
            "factorDiagnostics.minDirectionalFailed / confluenceScore / confluenceThreshold": "V3 gate diagnostics. If minDirectionalFailed is true, direction quality failed — do not treat as a high-quality entry.",
            "setupId / decision / qualified": "V3 setup identity and TRADE/WATCH/NO_SIGNAL decision — advisory context; never mutate deterministic scores.",
            "diagnostics.adxD1 / adxH4": "Trend strength / regime. Low ADX = ranging; do not treat range chop as a continuation entry, and prefer mean-reversion logic at value.",
            "diagnostics.rsi": "Momentum confirmation alongside factorScores.momentum.",
            "diagnostics.ema50 / ema200 / dema200 (H4 basis)": "Engine A's trend EMAs. Confirm direction matches EMA stacking, and gauge extension by how far diagnostics.entry sits above/below them in ATR units (diagnostics.atrH4) — a large gap with no pullback is the cross-asset extended/late test.",
            "diagnostics.emaTrendPeriod / emaMomentumPeriod / emaLongPeriod / rsiPeriod / rsiTimeframe": "Per-group periods Engine A actually scored with (e.g. forex 26/60, crypto 18/40, rsi 18 vs 12). The chart draws its EMA/RSI lines at these same per-group periods on confirmed bars — the legacy ema50/rsi14 key names are aliases, not literal 50/14 periods. indicatorParity compares chart vs Engine A values only when the chart is on H4; any entry in indicatorParity.mismatches means the values genuinely diverge and is a real warning, not an expected period difference.",
            "diagnostics.vwapExtended / vwapDistanceAtr (crypto only)": "Crypto late-trend extension flag: when vwapExtended is true or vwapDistanceAtr is large the entry is extended. Null for non-crypto — use EMA distance + RSI/ADX there instead.",
            "diagnostics.atrD1 / atrH4": "Volatility for SL/TP sizing and room to target; confirm SL is structurally valid relative to ATR.",
            "diagnostics.rr / sl / tp / entry": "Risk geometry; reject when RR is unacceptable after confirmation requirements.",
            "riskGeometry.maxSlFraction / slDistanceFraction / maxSlPassed / rrRequired / rrPassed": "Server-resolved execution risk gates. Fractions use 0.025=2.5%. False maxSlPassed or rrPassed blocks ENTRY_NOW; do not invent another cap or floor.",
            "conviction / timeframeBias (D1/H4/H1)": "Directional alignment across timeframes; conflicting timeframes lower tradeability and can justify WAIT.",
            "engineBContext (bosConfirmed, chochConfirmed, liquiditySweep, nearestSupport/Resistance, breakerLevel, structuralVerdict, obAtZone, fvgOverlap)": "Optional structure overlay for location and entry-model selection when available=true; confirmed BOS/CHoCH with acceptance supports continuation, not chasing.",
            "nonVisualContext / intermarket / newsSentiment": "Explains why Engine A scored the setup. Advisory, non-visual — never reject only because a non-visual driver is not visible on the chart.",
        },
        "strategyMapping": {
            "CONFLUENCE_CONTINUATION": "Trending regime (ADX up) with factorScores.trend and momentum aligned and BOS confirmed in direction; enter on continuation, not after exhaustion.",
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
