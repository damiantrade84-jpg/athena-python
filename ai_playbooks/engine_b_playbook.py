"""Engine B structure/liquidity/zone trade playbook."""

from __future__ import annotations

from ai_playbooks.contracts import PLAYBOOK_SCHEMA_VERSION


def _live_trigger_timeframes() -> dict:
    try:
        from config import CONFIG

        raw = (CONFIG.get("NAKED_ENGINE") or {}).get(
            "LIVE_TRIGGER_TF_BY_STYLE"
        ) or {}
        return {
            str(style): {str(asset): str(tf).upper() for asset, tf in assets.items()}
            for style, assets in raw.items()
            if isinstance(assets, dict)
        }
    except Exception:
        return {}


def get_engine_b_playbook() -> dict:
    return {
        "schemaVersion": PLAYBOOK_SCHEMA_VERSION,
        "engine": "B",
        "name": "Engine B Structure Liquidity Zone Review",
        "reviewOrder": "Structure -> Liquidity -> Zone Location -> Invalidation -> Decision",
        "principles": [
            "Review market structure: BOS, CHOCH, order blocks, FVGs.",
            "Engine B is a zone-retest engine: retest/rejection at the active zone (support for LONG, resistance for SHORT) is the intended setup when locationOk=true.",
            "Do not downgrade entry timing as a reflex because nearestResistance or nearestSupport exists — check locationOk, entryOk, and spaceGateOk first.",
            "Distinguish direction valid from entry timing poor — a passing Engine B score does not imply ENTRY_NOW.",
            "Judge zone location on zoneTf and entry triggers on triggerTf from server-supplied engineBContext. The canonical timeframeMatrix is only the fallback; configured liveTriggerOverrides may replace trigger TF without changing structure/zone roles. Macro swing sequence is always H4.",
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
        "liveTriggerOverrides": _live_trigger_timeframes(),
        "timeframeAuthority": "Server-supplied engineBContext structTf/zoneTf/triggerTf/atrTf and triggerTimeframeExpected/Actual/GateOk override the canonical matrix. Never substitute H1 for a requested M15/M30 trigger.",
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
            "engineBContext.nearestSupport / nearestResistance": "Legacy single-level zone references on zone_tf. Prefer nearestSupportZone / nearestResistanceZone with lower/upper bounds for room and TP-path checks.",
            "engineBContext.nearestSupportZone / nearestResistanceZone": "Full structural zone bounds on zone_tf. Active entry zone for LONG is support/demand; for SHORT is resistance/supply. Opposing zone is for room/RR and TP1-path checks — not an automatic reject when locationOk=true at the active zone.",
            "engineBContext.locationOk": "True when price is at/near the active zone (zone retest). Entry at the active zone is valid — do not label bad entry solely because price is inside a zone band.",
            "engineBContext.entryOk": "True when trigger_tf shows rejection, engulfing, inside-break, or structural catalyst (BOS volume, sweep, CHoCH) at zone.",
            "engineBContext.structTf / zoneTf / triggerTf / atrTf": "Actual role timeframes used by live analysis. Use these instead of assuming the canonical timeframeMatrix.",
            "engineBContext.triggerTimeframeExpected / triggerTimeframeActual / triggerTimeframeGateOk": "Fail-closed trigger provenance gate. A configured M15/M30 trigger passes only when actual equals expected; false or missing proof blocks ENTRY_NOW.",
            "engineBContext.spaceGateOk": "Authoritative deterministic room gate. False spaceGateOk is a hard blocker — do not override or mutate it.",
            "engineBContext.roomOk": "Raw diagnostic room flag only. roomOk=false is NOT an automatic reject when spaceGateOk=true through an approved and geometrically valid substitution or scale-out plan.",
            "engineBContext.support_too_close / resistance_too_close": "Warnings when spaceGateOk=true; hard blockers when spaceGateOk=false.",
            "engineBContext.tp1PathClear / tp1PathBlockReason": "Deterministic TP1 reachability before the nearest opposing zone. False tp1PathClear blocks spaceGateOk on every path (room, substitution, scale-out) — a signal you review will not carry tp1PathClear=false and spaceGateOk=true.",
            "engineBContext.tp1ClampedToOpposingZone / tp1ClampRejectReason": "When the selected TP1 would overshoot the nearest opposing wall, Engine B re-targets it to the wall's front edge. tp1ClampedToOpposingZone=true means the emitted TP1 is the clamped, reachable target — do not reject levels for the pre-clamp overshoot. tp1ClampRejectReason explains why a clamp was not possible (the signal is then deterministically blocked).",
            "engineBContext.structuralSl / executionSl / executionSlTighterThanStructural": "Structural invalidation level vs ATR/mechanical execution stop. executionSlTighterThanStructural=true is the normal Engine B design (mechanical stop inside structure for RR) — informational, not a defect. Note tighter-stop stop-out risk in prose if relevant, but do not reject levels or suggest the structural SL solely because of it.",
            "engineBContext.executionTp1 / executionTp2 / executionRr1 / executionRr2 / tp1MinRr / styleMinRr": "Scale-out: RR1 is checked against Engine B TP1 minimum RR; RR2 / rrUsedForGate is checked against style min RR. Do not reject solely because RR1 is below style min RR when RR1 passes TP1 minimum and TP1 has a clear path.",
            "engineBContext.runnerTpRequiresStructuralBreak": "When true, TP2 beyond the structural zone is allowed only when TP1 is reachable before the opposing zone.",
            "engineBContext.breakerLevel": "Breaker block level for breakout retest/acceptance checks.",
            "engineBContext.structuralVerdict": "Engine B's overall structural read; contradiction with the proposed direction lowers tradeability.",
            "engineBContext.structureOk / locationOk / entryOk / roomOk / rrOk / spaceGateOk": "Canonical six-gate checklist (advisory display). spaceGateOk is authoritative for room. False gates are hard evidence against ENTRY_NOW; do not override them. When locationOk=true and entryOk=true, do not reflex-downgrade as inside resistance/support.",
            "engineBContext.executionSl / executionTp / executionTp1 / executionTp2 / rr / rrUsedForGate": "Deterministic execution levels and RR used for gates — advisory levels review only.",
            "engineBContext.gateScore / gateMaxPossible / qualityScore / qualityMaxPossible / qualityComponents": "Deterministic score attribution. Mandatory gate completion and graded quality contribution are separate; normalize with score/maxScore, never gatePct. Cite but do not recompute or mutate them.",
            "engineBContext.maxSlFraction / slDistanceFraction / maxSlPassed / maxSlSource": "Resolved MAX_SL_PCT gate for the execution stop. Fractions use 0.025=2.5%. False maxSlPassed or invalid execution levels blocks ENTRY_NOW.",
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
            "spaceGateOk=false — authoritative room/TP-path blocker (includes tp1_blocked_by_opposing_zone, support_too_close, resistance_too_close).",
            "Reject or wait when tp1PathClear=false (TP1 beyond the nearest opposing zone and not clamped); a TP1 with tp1ClampedToOpposingZone=true is reachable and is not this case.",
            "Do not reject solely because RR1 is below style min RR when scaleOutActive=true, RR1 passes tp1MinRr, and tp1PathClear=true.",
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
