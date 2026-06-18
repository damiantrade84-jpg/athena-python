"""Read-only AI chart review (Engine A/B + native chart PNG)."""

from ai_review.concordance import compute_engine_a_ai_concordance, compute_engine_b_ai_concordance
from ai_review.engine_a_context import assemble_engine_a_context, build_engine_a_prompt_context
from ai_review.engine_a_verdict import build_engine_a_verdict_comparison
from ai_review.engine_b_context import assemble_engine_b_context
from ai_review.engine_b_verdict import build_engine_b_verdict_comparison
from ai_review.freshness import classify_atr_freshness
from ai_review.normalizer import normalize_chart_review_response
from ai_review.summary import build_ai_review_summary
from ai_review.payload_schema import ChartReviewPayload, build_payload
from ai_review.persistence import (
    ensure_schema,
    find_recent_review_by_hash,
    record_review,
)
from ai_review.prompt_builder import build_chart_review_prompt
from ai_review.providers.router import run_chart_review
from ai_review.timestamp_contract import evaluate_timestamp_mismatch
from ai_review.validation import ValidationError, validate_request

__all__ = [
    "ChartReviewPayload",
    "ValidationError",
    "assemble_engine_a_context",
    "assemble_engine_b_context",
    "build_engine_a_prompt_context",
    "build_engine_a_verdict_comparison",
    "build_engine_b_verdict_comparison",
    "build_chart_review_prompt",
    "build_ai_review_summary",
    "build_payload",
    "classify_atr_freshness",
    "compute_engine_a_ai_concordance",
    "compute_engine_b_ai_concordance",
    "ensure_schema",
    "find_recent_review_by_hash",
    "evaluate_timestamp_mismatch",
    "normalize_chart_review_response",
    "record_review",
    "run_chart_review",
    "validate_request",
]
