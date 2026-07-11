"""Policy for bypassing TP / SL / RR gates in demo and backtest contexts only."""

from __future__ import annotations

from typing import Any


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
