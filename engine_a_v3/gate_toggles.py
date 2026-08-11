"""Runtime toggles for optional Engine A timing/session hard gates.

Defaults are OFF so scan/UI can re-enable them without code changes.
Freshness, kill-switch, risk, and quote gates are not controlled here.
"""

from __future__ import annotations

from typing import Any, Mapping


def _cfg(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if isinstance(config, Mapping):
        return config
    try:
        from config import CONFIG

        return CONFIG if isinstance(CONFIG, Mapping) else {}
    except Exception:
        return {}


def entry_timing_gates_enabled(config: Mapping[str, Any] | None = None) -> bool:
    """When True, trigger/M15–M30 oppose can hold entryReadiness at PENDING.

    When False (default), trigger mismatch and execution-timing OPPOSED/NEUTRAL
    are advisory only — readiness is not blocked for those reasons.
    """
    return bool(_cfg(config).get("ENGINE_A_ENTRY_TIMING_GATES_ENABLED", False))


def session_gates_enabled(config: Mapping[str, Any] | None = None) -> bool:
    """When True, forex session-context and equity session mult gates apply.

    When False (default), quant session gate, setup session scoring, and the
    equity/index session strengthened multiplier are skipped.
    """
    return bool(_cfg(config).get("ENGINE_A_SESSION_GATES_ENABLED", False))
