"""Clean-room Engine A V3 specialist engine."""

from engine_a_v3.audit import AUDIT_BRIEF_VERSION, run_audit_brief
from engine_a_v3.contract import CONTRACT_VERSION, EngineASetupSignal
from engine_a_v3.cross_sectional import (
    apply_cross_sectional_ranking,
    resolve_cross_sectional_config,
)
from engine_a_v3.evaluator import evaluate_engine_a_v3

__all__ = [
    "AUDIT_BRIEF_VERSION",
    "CONTRACT_VERSION",
    "EngineASetupSignal",
    "apply_cross_sectional_ranking",
    "evaluate_engine_a_v3",
    "resolve_cross_sectional_config",
    "run_audit_brief",
]
