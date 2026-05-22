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
            "Assess trend, momentum, volatility, and volume coherence with direction.",
            "High Engine A score does not imply entry is acceptable now.",
            "Distinguish direction valid from entry timing poor.",
            "Score high but location poor requires WAIT, not ENTRY_NOW.",
            "Signal valid but execution should wait when chart contradicts timing.",
            "No trade when evidence is weak, conflicted, or missing required context.",
        ],
        "entryModels": [
            "CONFLUENCE_CONTINUATION",
            "PULLBACK_TO_STRUCTURE",
            "BREAKOUT_RETEST",
            "MEAN_REVERSION_AT_VALUE",
            "NO_TRADE",
        ],
        "invalidations": [
            "Identify what would invalidate the directional thesis.",
            "Check whether SL is structurally valid relative to ATR and structure.",
            "Reject when RR is unacceptable after confirmation requirements.",
        ],
        "mustRejectIf": [
            "Direction valid but entry timing is extended, late, or chasing.",
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
