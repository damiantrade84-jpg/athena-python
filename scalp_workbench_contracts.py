"""Engine D Scalp Workbench UI contracts — liquidity, profile, advisory, phase."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config import CONFIG


def _cfg(path: str, default: Any) -> Any:
    node: Any = CONFIG.get("SCALP_ENGINE") or {}
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    return default if node is None else node


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, "", "null", "unknown"):
        return None
    if str(value).lower() in ("true", "1", "yes"):
        return True
    if str(value).lower() in ("false", "0", "no"):
        return False
    return None


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _near_levels(levels: list[float], anchor: float | None, count: int) -> list[float]:
    if anchor is None or not levels:
        return list(levels)
    ranked = sorted(levels, key=lambda x: abs(x - anchor))
    return ranked[: max(0, int(count))]


def _prior_session_block(raw_signal: dict) -> dict:
    shadow = raw_signal.get("profile_anchor_shadow")
    if not isinstance(shadow, dict):
        return {}
    candidates = shadow.get("candidates")
    if not isinstance(candidates, dict):
        return {}
    prior = candidates.get("prior_session")
    return prior if isinstance(prior, dict) else {}


def _premarket_levels(raw_signal: dict) -> tuple[float | None, float | None]:
    proxy = raw_signal.get("premarket_delta_proxy_levels")
    if not isinstance(proxy, dict):
        return None, None
    return _float(proxy.get("high")), _float(proxy.get("low"))


def _sweep_reclaim_levels(raw_signal: dict) -> tuple[float | None, float | None]:
    sweep = _float(raw_signal.get("sweep_level") or raw_signal.get("nearest_level"))
    reclaim = _float(raw_signal.get("reclaim_level"))
    if sweep is None and _bool(raw_signal.get("sweep_detected")):
        sweep = _float(raw_signal.get("zone_level") or raw_signal.get("vp_val") or raw_signal.get("vp_vah"))
    shadow = raw_signal.get("profile_anchor_shadow")
    if reclaim is None and isinstance(shadow, dict):
        candidates = shadow.get("candidates") or {}
        reclaim_leg = candidates.get("reclaim_leg") if isinstance(candidates, dict) else None
        if isinstance(reclaim_leg, dict) and reclaim_leg.get("valid"):
            reclaim = _float(reclaim_leg.get("last_close") or reclaim_leg.get("high") or reclaim_leg.get("low"))
    return sweep, reclaim


def build_scalp_liquidity_context(raw_signal: dict) -> dict:
    prior = _prior_session_block(raw_signal)
    pm_high, pm_low = _premarket_levels(raw_signal)
    sweep, reclaim = _sweep_reclaim_levels(raw_signal)
    warnings: list[str] = []

    session_high = _float(raw_signal.get("session_high"))
    session_low = _float(raw_signal.get("session_low"))
    if session_high is None or session_low is None:
        shadow = raw_signal.get("profile_anchor_shadow")
        active = shadow.get("active_anchor") if isinstance(shadow, dict) else None
        if isinstance(active, dict):
            session_high = session_high or _float(active.get("high"))
            session_low = session_low or _float(active.get("low"))

    asia_high = _float(raw_signal.get("asia_high"))
    asia_low = _float(raw_signal.get("asia_low"))
    london_high = _float(raw_signal.get("london_high"))
    london_low = _float(raw_signal.get("london_low"))
    if asia_high is None and asia_low is None:
        warnings.append("asia_session_levels_unavailable")
    if london_high is None and london_low is None:
        warnings.append("london_session_levels_unavailable")

    return {
        "priorDayHigh": _float(prior.get("high")),
        "priorDayLow": _float(prior.get("low")),
        "sessionHigh": session_high,
        "sessionLow": session_low,
        "premarketHigh": pm_high,
        "premarketLow": pm_low,
        "asiaHigh": asia_high,
        "asiaLow": asia_low,
        "londonHigh": london_high,
        "londonLow": london_low,
        "sweepLevel": sweep,
        "reclaimLevel": reclaim,
        "warnings": warnings,
    }


def build_scalp_profile_extensions(raw_signal: dict, market_location: dict) -> dict:
    prior = _prior_session_block(raw_signal)
    near_count = int(_cfg("WORKBENCH_CHART.NEAR_LVN_HVN_COUNT", 3))
    entry = _float(raw_signal.get("price") or raw_signal.get("entry"))
    lvn_levels = list(market_location.get("lvnLevels") or [])
    hvn_levels = list(market_location.get("hvnLevels") or [])

    prior_poc = _float(raw_signal.get("prior_vp_poc") or prior.get("poc"))
    prior_vah = _float(raw_signal.get("prior_vp_vah") or prior.get("vah"))
    prior_val = _float(raw_signal.get("prior_vp_val") or prior.get("val"))

    return {
        "priorPoc": prior_poc,
        "priorVah": prior_vah,
        "priorVal": prior_val,
        "lvnLevelsNear": _near_levels(lvn_levels, entry, near_count),
        "hvnLevelsNear": _near_levels(hvn_levels, entry, near_count),
    }


def build_scalp_advisory_context(raw_signal: dict, source_contract: dict | None = None) -> dict:
    warnings: list[str] = []
    fee_guard = raw_signal.get("fee_guard") if isinstance(raw_signal.get("fee_guard"), dict) else {}
    if fee_guard.get("blocked") or fee_guard.get("high_cost"):
        warnings.append("fee_guard_high_cost")
    if raw_signal.get("rr_ok") is False or raw_signal.get("rr1_below_min"):
        warnings.append("rr_below_min")
    if raw_signal.get("strict_fabio_pass") is False:
        warnings.append("strict_fabio_failed")
    source = source_contract or {}
    if source.get("strictOrderflowSourcePass") is False:
        warnings.append("proxy_orderflow")
    if source.get("orderflowSourceIsReal") is False or source.get("cvdSourceIsReal") is False:
        warnings.append("source_fidelity_weak")
    data_fidelity = raw_signal.get("data_fidelity") if isinstance(raw_signal.get("data_fidelity"), dict) else {}
    if data_fidelity.get("cvd_is_proxy") and raw_signal.get("cvd_direction"):
        warnings.append("cvd_conflict")
    gate = str(raw_signal.get("gate_result") or "").upper()
    if gate and gate not in ("PASS", ""):
        warnings.append("old_gate_result")
    if raw_signal.get("executable") is False:
        warnings.append("old_executable")
    warnings.extend(_list(source.get("warnings")))
    warnings = sorted(set(w for w in warnings if w))

    return {
        "oldGateResult": gate or None,
        "oldExecutable": bool(raw_signal.get("executable")),
        "warnings": warnings,
        "riskAdvisory": {
            "rrOk": raw_signal.get("rr_ok"),
            "feeGuard": fee_guard,
            "strictFabioPass": raw_signal.get("strict_fabio_pass"),
        },
        "sourceAdvisory": {
            "strictOrderflowSourcePass": source.get("strictOrderflowSourcePass"),
            "unavailableReasons": list(source.get("unavailableReasons") or []),
            "venueMismatch": source.get("venueMismatch"),
        },
    }


def derive_candidate_phase(raw_signal: dict) -> str:
    grade = str(raw_signal.get("ai_grade") or "C").upper()
    direction = str(raw_signal.get("direction") or "NONE").upper()
    gate = str(raw_signal.get("gate_result") or "").upper()
    if grade == "D" or direction not in ("LONG", "SHORT"):
        return "CONTEXT_ONLY"
    if grade in ("A", "B") and direction in ("LONG", "SHORT"):
        return "AI_REVIEW_REQUIRED"
    if gate == "WATCHLIST" or grade in ("B", "C"):
        return "WATCHLIST"
    return "CONTEXT_ONLY"


def map_setup_model(raw_signal: dict) -> str:
    zone = str(raw_signal.get("zone_type") or raw_signal.get("trigger_type") or raw_signal.get("setup_type") or "").lower()
    market_state = str(raw_signal.get("market_state") or "").lower()
    location = str(raw_signal.get("location") or raw_signal.get("price_location") or "").lower()
    if "middle" in location or location == "inside_va":
        if market_state == "balance":
            return "NO_TRADE_MIDDLE_OF_VALUE"
    if "mean" in zone or "reversion" in zone or "poc" in location:
        return "BALANCE_MEAN_REVERSION_TO_POC"
    if "lvn" in zone or location == "at_lvn":
        return "LVN_INITIATIVE_CONTINUATION"
    if raw_signal.get("sweep_detected") or "sweep" in zone or "reclaim" in zone:
        return "SWEEP_RECLAIM_REVERSAL"
    if raw_signal.get("absorption_count") or "absorption" in zone:
        return "ABSORPTION_REVERSAL"
    return "NO_TRADE_MIDDLE_OF_VALUE" if market_state == "balance" else "LVN_INITIATIVE_CONTINUATION"


def classify_aggression(raw_signal: dict) -> str:
    if raw_signal.get("absorption_count") or raw_signal.get("absorption_detected"):
        return "ABSORPTION"
    cvd_slope = _float(raw_signal.get("cvd_slope"))
    if cvd_slope is not None and abs(cvd_slope) > 0:
        return "INITIATIVE_AGGRESSION"
    if raw_signal.get("aaa_complete") is False and raw_signal.get("aggression_confirmed") is False:
        return "EXHAUSTION"
    if raw_signal.get("aggression_confirmed"):
        return "INITIATIVE_AGGRESSION"
    return "NO_CLEAR_FLOW"


def classify_source_quality(source_contract: dict | None) -> str:
    source = source_contract or {}
    if source.get("strictOrderflowSourcePass") is True and not source.get("unavailableReasons"):
        return "REAL"
    if source.get("orderflowSourceIsReal") is True:
        return "DELAYED_REAL"
    orderflow = str(source.get("orderflowSource") or "").lower()
    if "proxy" in orderflow or "mt5_tick" in orderflow:
        return "PROXY"
    if source.get("unavailableReasons"):
        return "WEAK"
    return "UNAVAILABLE"


def attach_scalp_workbench_extensions(row: dict, *, source_contract: dict | None = None) -> dict:
    market = row.get("marketLocation") if isinstance(row.get("marketLocation"), dict) else {}
    profile_ext = build_scalp_profile_extensions(row, market)
    if isinstance(market, dict):
        market = {**market, **profile_ext}
        row["marketLocation"] = market
    row["liquidityContext"] = build_scalp_liquidity_context(row)
    row["advisoryContext"] = build_scalp_advisory_context(row, source_contract=source_contract)
    row["candidatePhase"] = derive_candidate_phase(row)
    row["setupModel"] = map_setup_model(row)
    row["aggressionClassification"] = classify_aggression(row)
    row["sourceQualityLabel"] = classify_source_quality(source_contract)
    row["workbenchAttachedAt"] = datetime.now(timezone.utc).isoformat()
    return row
