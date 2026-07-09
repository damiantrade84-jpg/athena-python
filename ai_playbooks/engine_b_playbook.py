"""Engine B structure/liquidity/zone trade playbook."""

from __future__ import annotations

from ai_playbooks.contracts import PLAYBOOK_SCHEMA_VERSION


def get_engine_b_playbook() -> dict:
    return {
        "schemaVersion": PLAYBOOK_SCHEMA_VERSION,
        "engine": "B",
        "name": "Engine B Structure Liquidity Zone Review",
        "reviewOrder": "Structure -> Liquidity -> Zone Location -> Invalidation -> Decision",
        "principles": [
            "Review market structure: BOS, CHOCH, order blocks, FVGs.",
            "Engine B is a zone-retest engine: retest/rejection at the active zone (support for LONG, resistance for SHORT) is the intended setup when locationOk=true.",
            "Do not downgrade entry timing as a reflex because nearestResistance or nearestSupport exists — check locationOk, entryOk, and roomOk first.",
            "Distinguish direction valid from entry timing poor — a passing Engine B score does not imply ENTRY_NOW.",
            "Judge zone location on zone_tf and entry triggers on trigger_tf from timeframeMatrix; macro swing sequence is always H4.",
            "Chart screenshot timeframe may differ from zone_tf — do not reject H4/D1 zone quality using only a lower-TF chart image.",
            "Assess liquidity pools, supply/demand, support/resistance.",
            "Evaluate sweep/reclaim and whether structure supports direction.",
            "Nearest zone must support acceptable RR before ENTRY_NOW.",
            "Structure signal after invalidation must be rejected.",
            "Use server-supplied engineBContext flags and canonical gates only — never invent BOS/OB/FVG confirmation from the chart image alone.",
            "Engine B score/gates are deterministic; AI is advisory and must never mutate or override them.",
        ],
        "timeframeMatrix": {
            "macroSwing": "H4 (always, separate from struct_tf)",
            "scalp": {
                "struct": "H1",
                "zone": "H4",
                "trigger": "H1",
                "atr": "H1",
            },
            "intraday": {
                "struct": "H4",
                "zone": "H4",
                "trigger": "H1",
                "atr": "H4",
            },
            "swing": {
                "struct": "D1",
                "zone": "D1",
                "trigger": "H4",
                "atr": "D1",
            },
        },
        "entryModels": [
            "STRUCTURE_CONTINUATION",
            "LIQUIDITY_SWEEP_RECLAIM",
            "ORDER_BLOCK_REJECTION",
            "FVG_FILL_CONTINUATION",
            "NO_TRADE",
        ],
        "strategyMapping": {
            "STRUCTURE_CONTINUATION": "BOS or CHoCH confirmed (bosConfirmed/chochConfirmed) with trigger on trigger_tf and locationOk=true at active zone or BOS retest.",
            "LIQUIDITY_SWEEP_RECLAIM": "liquiditySweep=true, sweepDirection aligned, reclaim at active zone with locationOk=true.",
            "ORDER_BLOCK_REJECTION": "obAtZone=true with rejection trigger at active zone — required server evidence.",
            "FVG_FILL_CONTINUATION": "fvgOverlap=true or activeFvgCount > 0 at active zone with continuation trigger.",
            "NO_TRADE": "Gates false, post-invalidation, structure contradicts direction, or missing required server evidence.",
        },
        "structureUsage": {
            "engineBContext.available": "True only when real Engine B structure context is present. If false/absent, do not apply Engine B entry models.",
            "engineBContext.bosConfirmed / chochConfirmed": "Server-confirmed break/change of structure. Only treat BOS/CHoCH as confirmed when these flags are true — do not infer confirmation from the image alone.",
            "engineBContext.liquiditySweep / sweepDirection": "Server-detected liquidity sweep and its direction (LONG/SHORT). Require reclaim evidence and direction alignment before LIQUIDITY_SWEEP_RECLAIM.",
            "engineBContext.obAtZone": "True when a qualifying order block overlaps the active zone — required evidence for ORDER_BLOCK_REJECTION.",
            "engineBContext.fvgOverlap / activeFvgCount / nearestFvgMid": "FVG confluence at the active zone. Use for FVG_FILL_CONTINUATION only when fvgOverlap is true or activeFvgCount > 0.",
            "engineBContext.nearestSupport / nearestResistance": "Reference structural zone levels on zone_tf. Active entry zone for LONG is support/demand; for SHORT is resistance/supply. Opposing zone above/below is for room/RR checks — not an automatic reject when locationOk=true at the active zone.",
            "engineBContext.locationOk": "True when price is at/near the active zone (zone retest). Entry at the active zone is valid — do not label bad entry solely because price is inside a zone band.",
            "engineBContext.entryOk": "True when trigger_tf shows rejection, engulfing, inside-break, or structural catalyst (BOS volume, sweep, CHoCH) at zone.",
            "engineBContext.roomOk / spaceGateOk": "True when distance to opposing zone supports acceptable RR. False roomOk or resistance_too_close/support_too_close is concrete evidence to WAIT or reject.",
            "engineBContext.breakerLevel": "Breaker block level for breakout retest/acceptance checks.",
            "engineBContext.structuralVerdict": "Engine B's overall structural read; contradiction with the proposed direction lowers tradeability.",
            "engineBContext.structureOk / locationOk / entryOk / roomOk / rrOk / spaceGateOk": "Canonical six-gate checklist (advisory display). False gates are hard evidence against ENTRY_NOW; do not override them. When locationOk=true and entryOk=true, do not reflex-downgrade as inside resistance/support.",
            "engineBContext.executionSl / executionTp / rr": "Deterministic execution levels and RR used for the room/RR gates — advisory levels review only.",
            "engineBContext.volumeProfileContext": "POC/VAH/VAL availability per asset class. When disabled, do not cite volume-profile levels for this asset.",
            "engineBContext.score / maxScore / threshold / passed / direction": "Engine B's deterministic result — advisory context only; never mutate or override it.",
        },
        "invalidations": [
            "Identify exact invalidation level or zone.",
            "State what would prove the structure setup wrong.",
            "Verify stop is structurally valid and RR acceptable.",
        ],
        "mustRejectIf": [
            "SHORT with locationOk=false while entry is through active support/demand (shorting into demand without rejection).",
            "LONG with locationOk=false while entry is through active resistance/supply without rejection/acceptance evidence.",
            "locationOk=false or entryOk=false — gates failed regardless of chart appearance.",
            "roomOk=false or nearest opposing zone blocks acceptable RR (resistance_too_close for LONG, support_too_close for SHORT).",
            "Taking structure signal after invalidation already occurred.",
            "Structure and liquidity context contradict proposed direction.",
            "ORDER_BLOCK_REJECTION or FVG_FILL_CONTINUATION selected without matching obAtZone / fvgOverlap evidence.",
            "Chasing extended displacement with no trigger on trigger_tf and entryOk=false.",
        ],
        "requiredOutputFields": [
            "tradeSkillVersion",
            "reviewType",
            "decision",
            "direction",
            "confidence",
            "entryAllowedNow",
            "locationAssessment",
            "invalidationLevel",
            "invalidationReason",
            "chartReadSummary",
        ],
    }
