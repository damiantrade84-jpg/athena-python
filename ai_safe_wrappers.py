"""ai_safe_wrappers.py — unified safe-default wrappers for AI calls (Audit CRIT-008)."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable

log = logging.getLogger("sentinel.ai_safe")

SAFE_DEFAULTS: dict[str, dict[str, Any]] = {
    "marcus": {
        "grade": "F",
        "verdict": "AI review unavailable",
        "edgeProbability": None,
        "riskLevel": "High",
        "error": True,
    },
    "debate": {
        "grade": "SKIP",
        "allowed": False,
        "reasoning": "Debate failure safe default",
        "bull_conviction": 0,
        "bear_conviction": 0,
        "score_adjustment": 0.0,
        "score_adjustment_raw": 0.0,
        "error": True,
    },
    "decay": {"verdict": "HOLD", "urgency": "LOW", "reasoning": "Decay AI failure safe default", "error": True},
}


def ai_call_with_safe_default(surface: str, max_retries: int = 0):
    """Decorator: catch failures and return SAFE_DEFAULTS[surface] without crashing callers."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            safe = dict(SAFE_DEFAULTS.get(surface, {"error": True}))
            last_err: BaseException | None = None
            attempts = max(0, int(max_retries)) + 1
            for attempt in range(attempts):
                try:
                    out = func(*args, **kwargs)
                    if out is None:
                        raise ValueError(f"{surface} returned None")
                    return out
                except Exception as e:
                    last_err = e
                    log.warning(
                        "[AI_SAFE] surface=%s attempt=%s/%s failed: %s",
                        surface,
                        attempt + 1,
                        attempts,
                        e,
                    )
            log.error("[AI_SAFE] surface=%s exhausted retries; safe default used: %s", surface, last_err)
            try:
                from ai_review_logger import log_ai_failure

                log_ai_failure(surface=surface, error=str(last_err), safe_default_used=True)
            except Exception:
                pass
            return safe

        return wrapper

    return decorator
