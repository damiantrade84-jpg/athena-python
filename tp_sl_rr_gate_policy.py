"""Central policy for TP / SL / RR gate enforcement."""

from __future__ import annotations

from typing import Any


_ENGINE_A_NAMES = {
    "a",
    "engine_a",
    "engine_a_v3",
}
_ENGINE_B_NAMES = {
    "b",
    "engine_b",
    "naked",
    "naked_structure",
    "structure",
    "smc",
}


def resolve_profitability_gate_engine(
    signal: dict[str, Any] | None = None,
    *,
    engine: str | None = None,
) -> str | None:
    """Return the selected A/B execution engine without trusting overlay data."""
    data = signal if isinstance(signal, dict) else {}
    candidates = (
        engine,
        data.get("engine"),
        data.get("source_engine"),
        data.get("engine_source"),
        data.get("engine_name"),
    )
    for raw in candidates:
        value = str(raw or "").strip().lower().replace(" ", "_")
        if value in _ENGINE_A_NAMES or value.startswith("engine_a"):
            return "engine_a"
        if value in _ENGINE_B_NAMES or value.startswith("engine_b"):
            return "engine_b"
        if value in {"c", "engine_c", "engine_c_consensus", "consensus"}:
            return None

    verdict = str(data.get("verdict") or "").strip().upper()
    if verdict.startswith("A_ONLY"):
        return "engine_a"
    if verdict.startswith("B_ONLY"):
        return "engine_b"
    if bool(data.get("is_naked")):
        return "engine_b"
    return None


def resolve_execution_profitability_gate_engine(
    signal: dict[str, Any] | None = None,
) -> str | None:
    """Resolve A/B for execution, including legacy Engine A payloads."""
    data = signal if isinstance(signal, dict) else {}
    selected = resolve_profitability_gate_engine(data)
    if selected is not None:
        return selected
    explicit = str(
        data.get("engine")
        or data.get("source_engine")
        or data.get("engine_source")
        or data.get("engine_name")
        or ""
    ).strip().lower()
    if explicit:
        return explicit
    if data.get("aseExecution") is True or data.get("fxFactorExecution") is True:
        return None
    # Engine A predates the explicit engine identity field on execution rows.
    return "engine_a"


def engine_ab_profitability_gates_enforced(
    cfg: dict[str, Any] | None = None,
    *,
    signal: dict[str, Any] | None = None,
    engine: str | None = None,
) -> bool:
    """Return whether RR and maximum-SL thresholds hard-block Engine A/B."""
    if cfg is None:
        from config import CONFIG

        cfg = CONFIG
    selected = resolve_profitability_gate_engine(signal, engine=engine)
    if selected not in {"engine_a", "engine_b"}:
        return True
    return bool(cfg.get("ENGINE_AB_PROFITABILITY_GATES_ENFORCED", False))


def add_level_advisory(
    signal: dict[str, Any] | None,
    code: str,
    *,
    actual: float | None = None,
    threshold: float | None = None,
    source: str | None = None,
) -> None:
    """Attach one de-duplicated, non-blocking level advisory to a signal."""
    if not isinstance(signal, dict):
        return
    advisories = signal.setdefault("levelAdvisories", [])
    if not isinstance(advisories, list):
        advisories = []
        signal["levelAdvisories"] = advisories
    item = {
        "code": str(code),
        "blocking": False,
    }
    if actual is not None:
        item["actual"] = round(float(actual), 6)
    if threshold is not None:
        item["threshold"] = round(float(threshold), 6)
    if source:
        item["source"] = str(source)
    for index, existing in enumerate(advisories):
        if isinstance(existing, dict) and existing.get("code") == item["code"]:
            advisories[index] = item
            break
    else:
        advisories.append(item)
    signal["levelGateMode"] = "advisory"


def _demo_execution_context(cfg: dict[str, Any]) -> bool:
    mode = str(cfg.get("EXECUTOR_MODE", "") or "").strip().lower()
    if mode in {"demo", "paper"}:
        return True
    exec_cfg = cfg.get("EXECUTION") or {}
    if isinstance(exec_cfg, dict) and bool(exec_cfg.get("DEMO_MODE", False)):
        return True
    return False


def _research_context(
    cfg: dict[str, Any],
    *,
    signal: dict[str, Any] | None = None,
    backtest: bool = False,
    historical: bool = False,
) -> bool:
    if backtest or historical:
        return True
    if bool(cfg.get("RESEARCH_MODE") or cfg.get("BACKTEST_RUNNING")):
        return True
    if isinstance(signal, dict):
        if signal.get("backtestRunning") or signal.get("researchMode"):
            return True
        if signal.get("historical_mode"):
            return True
    return False


def tp_sl_rr_gates_disabled(
    cfg: dict[str, Any] | None = None,
    *,
    signal: dict[str, Any] | None = None,
    backtest: bool = False,
    historical: bool = False,
) -> bool:
    """Return True when TP/SL/RR gates should be bypassed.

    Requires ``DISABLE_TP_SL_RR_GATES: true`` and a non-live context (demo,
    paper, backtest, or research). Never bypasses when ``EXECUTOR_MODE`` is
    ``live`` (fail-closed for production).
    """
    if cfg is None:
        from config import CONFIG

        cfg = CONFIG
    if not bool(cfg.get("DISABLE_TP_SL_RR_GATES", False)):
        return False
    mode = str(cfg.get("EXECUTOR_MODE", "") or "").strip().lower()
    if mode == "live":
        return False
    if _research_context(cfg, signal=signal, backtest=backtest, historical=historical):
        return True
    return _demo_execution_context(cfg)
