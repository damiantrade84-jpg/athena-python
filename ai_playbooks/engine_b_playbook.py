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
            "Assess liquidity pools, supply/demand, support/resistance.",
            "Evaluate sweep/reclaim and whether structure supports direction.",
            "Nearest zone must support acceptable RR before ENTRY_NOW.",
            "Structure signal after invalidation must be rejected.",
            "Use server-supplied engineBContext flags and canonical gates only — never invent BOS/OB/FVG confirmation from the chart image alone.",
            "Engine B score/gates are deterministic; AI is advisory and must never mutate or override them.",
        ],
        "entryModels": [
            "STRUCTURE_CONTINUATION",
            "LIQUIDITY_SWEEP_RECLAIM",
            "ORDER_BLOCK_REJECTION",
            "FVG_FILL_CONTINUATION",
            "NO_TRADE",
        ],
        "structureUsage": {
            "engineBContext.available": "True only when real Engine B structure context is present. If false/absent, do not apply Engine B entry models.",
            "engineBContext.bosConfirmed / chochConfirmed": "Server-confirmed break/change of structure. Only treat BOS/CHoCH as confirmed when these flags are true — do not infer confirmation from the image alone.",
            "engineBContext.liquiditySweep / sweepDirection": "Server-detected liquidity sweep and its direction (LONG/SHORT). Require reclaim evidence and direction alignment before LIQUIDITY_SWEEP_RECLAIM.",
            "engineBContext.obAtZone": "True when a qualifying order block overlaps the active zone — required evidence for ORDER_BLOCK_REJECTION.",
            "engineBContext.fvgOverlap / activeFvgCount / nearestFvgMid": "FVG confluence at the active zone. Use for FVG_FILL_CONTINUATION only when fvgOverlap is true or activeFvgCount > 0.",
            "engineBContext.nearestSupport / nearestResistance": "Nearest structural zone levels. Check the proposed entry and TP path against them for location and RR — see mustRejectIf for shorting into demand / longing into supply.",
            "engineBContext.breakerLevel": "Breaker block level for breakout retest/acceptance checks.",
            "engineBContext.structuralVerdict": "Engine B's overall structural read; contradiction with the proposed direction lowers tradeability.",
            "engineBContext.structureOk / locationOk / entryOk / roomOk / rrOk / spaceGateOk": "Canonical six-gate checklist (advisory display). False gates are hard evidence against ENTRY_NOW; do not override them.",
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
            "Shorting directly into demand or support zone.",
            "Longing directly into supply or resistance zone.",
            "Taking structure signal after invalidation already occurred.",
            "Nearest opposing zone makes RR poor for the proposed entry.",
            "Structure and liquidity context contradict proposed direction.",
            "ORDER_BLOCK_REJECTION or FVG_FILL_CONTINUATION selected without matching obAtZone / fvgOverlap evidence.",
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
