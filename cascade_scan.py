"""Client-driven Cascade Scan orchestration for Engine A review queues.

This module is backend-only and deliberately keeps chart review as a client
action. The server stages are: run the existing scan, build a deterministic
shortlist, and optionally apply injected JSON triage.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4


ANALYSIS_TIMEFRAMES = ["D1", "H4", "H1"]
DISPLAY_TIMEFRAME = "D1/H4/H1"
TRIAGE_VERDICTS = {"FULL_CHART_REVIEW", "WATCHLIST_ONLY", "SKIP"}

DEFAULT_RULES: dict[str, Any] = {
    "min_confluence": 2.0,
    "min_rr": 1.5,
    "min_coherence": 0.5,
    "low_source_fidelity_floor": 0.5,
    "hard_block_regimes": [],
}

TRUSTED_PAIR_SOURCES = {
    "mt5",
    "binance",
    "bybit",
    "eodhd",
    "polygon",
    "yfinance",
}

FALLBACK_SOURCE_TOKENS = (
    "fallback",
    "proxy",
    "synthetic",
    "mock",
    "placeholder",
    "routed",
    "mt5_tick",
)


def run_cascade_scan(
    asset_class: str = "forex",
    style: str = "auto",
    enable_triage: bool = False,
    top_n: int = 5,
    rules: dict | None = None,
    *,
    run_full_scan_fn: Callable[..., dict] | None = None,
    triage_client: Callable[[list[dict]], Any] | None = None,
) -> dict:
    """Run the existing scan and return a ranked review queue."""
    scan_fn = run_full_scan_fn
    if scan_fn is None:
        from scanner import run_full_scan as scan_fn

    scan_result = scan_fn(style=style, asset_class=asset_class)
    if _is_busy_scan(scan_result):
        return {
            "success": False,
            "busy": True,
            "error": "Scan already in progress",
        }
    if not isinstance(scan_result, dict) or scan_result.get("success") is False:
        return scan_result if isinstance(scan_result, dict) else {
            "success": False,
            "error": "scan_result_unavailable",
        }

    merged_rules = _merge_rules(rules)
    try:
        limit = max(1, int(top_n))
    except (TypeError, ValueError):
        limit = 5

    raw_candidates = _candidate_signals(scan_result)
    candidates = build_shortlist(
        scan_result,
        rules=merged_rules,
        asset_class=asset_class,
        top_n=limit,
        style=style,
    )
    if enable_triage:
        candidates = triage_candidates(candidates, client=triage_client)

    return {
        "success": True,
        "scanRunId": uuid4().hex,
        "assetClass": asset_class,
        "universeCount": _safe_int(scan_result.get("totalPairs"), default=0),
        "engineACandidateCount": len(raw_candidates),
        "shortlistCount": len(candidates),
        "triageEnabled": bool(enable_triage),
        "candidates": candidates,
    }


def build_shortlist(
    scan_result: dict,
    rules: dict | None,
    asset_class: str,
    top_n: int | None = None,
    style: str = "auto",
) -> list[dict]:
    """Pure deterministic shortlist builder for server-safe Engine A fields."""
    merged_rules = _merge_rules(rules)
    rows: list[dict] = []

    for signal in _candidate_signals(scan_result):
        blockers = _hard_blockers(signal, merged_rules)
        if blockers:
            continue
        row = _candidate_from_signal(signal, merged_rules, asset_class, style)
        rows.append(row)

    rows.sort(key=_rank_key)
    if top_n is not None:
        rows = rows[: max(1, int(top_n))]
    for idx, row in enumerate(rows, 1):
        row["shortlistRank"] = idx
    return rows


def triage_candidates(
    shortlist: list[dict],
    client: Callable[[list[dict]], Any] | None = None,
) -> list[dict]:
    """Apply optional provider-agnostic JSON triage to shortlist candidates."""
    out = [deepcopy(row) for row in shortlist]
    if client is None:
        for row in out:
            row["triageVerdict"] = None
            row["triageReason"] = None
            row["triageConfidence"] = None
        return out

    payload = [_triage_payload(row) for row in out]
    raw = client(payload)
    raw_items = raw if isinstance(raw, list) else [raw]

    for idx, row in enumerate(out):
        verdict_raw = raw_items[idx] if idx < len(raw_items) else {}
        verdict = verdict_raw if isinstance(verdict_raw, dict) else {}
        triage_verdict = str(verdict.get("triageVerdict") or "").strip().upper()
        if triage_verdict not in TRIAGE_VERDICTS:
            row["triageVerdict"] = "SKIP"
            row["triageReason"] = "invalid_triage_verdict"
            row["triageConfidence"] = 0.0
            row["triagePriority"] = None
            row["actionState"] = "BLOCKED"
            row["readyForChartReview"] = False
            continue

        row["triageVerdict"] = triage_verdict
        row["triageReason"] = str(verdict.get("reason") or "").strip() or None
        row["triageConfidence"] = _clamp_float(verdict.get("confidence"), 0.0, 1.0)
        row["triagePriority"] = _safe_int(verdict.get("priority"), default=None)
        if triage_verdict == "FULL_CHART_REVIEW":
            row["actionState"] = "READY_FOR_CHART_REVIEW"
            row["readyForChartReview"] = True
        elif triage_verdict == "WATCHLIST_ONLY":
            row["actionState"] = "WATCHLIST_ONLY"
            row["readyForChartReview"] = False
        else:
            row["actionState"] = "BLOCKED"
            row["readyForChartReview"] = False
    return out


def _merge_rules(rules: dict | None) -> dict:
    merged = dict(DEFAULT_RULES)
    if isinstance(rules, dict):
        merged.update(rules)
    hard_block = merged.get("hard_block_regimes")
    if not isinstance(hard_block, (list, tuple, set)):
        hard_block = []
    merged["hard_block_regimes"] = {
        str(item).strip().upper()
        for item in hard_block
        if str(item).strip()
    }
    return merged


def _candidate_signals(scan_result: dict) -> list[dict]:
    if not isinstance(scan_result, dict):
        return []
    raw = []
    signals = scan_result.get("signals")
    if not isinstance(signals, list):
        signals = scan_result.get("tradeSignals")
    if isinstance(signals, list):
        raw.extend(signals)
    watchlist = scan_result.get("watchlist")
    if isinstance(watchlist, list):
        raw.extend(watchlist)

    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _symbol_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _hard_blockers(signal: dict, rules: dict) -> list[str]:
    blockers: list[str] = []
    score = _as_float(signal.get("confluenceScore"))
    min_confluence = _as_float(rules.get("min_confluence")) or 0.0
    if score is None or score < min_confluence:
        blockers.append("low_confluence")

    direction = _normalize_direction(signal.get("direction"))
    if direction not in {"LONG", "SHORT"}:
        blockers.append("missing_or_invalid_direction")

    rr = _as_float(signal.get("rr"))
    min_rr = _as_float(rules.get("min_rr")) or 0.0
    if rr is None:
        blockers.append("missing_rr")
    elif rr < min_rr:
        blockers.append("low_rr")

    freshness_block = _freshness_blocker(signal.get("dataFreshness"))
    if freshness_block:
        blockers.append(freshness_block)

    blockers.extend(_provenance_blockers(signal))

    regime = str(signal.get("regime") or signal.get("regimeName") or "").strip().upper()
    if regime and regime in rules.get("hard_block_regimes", set()):
        blockers.append("hard_blocked_regime")

    for item in _as_list(signal.get("scanDiagnostics")):
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().lower()
        if code in {"event_risk", "kill_switch", "risk_block", "hard_block", "data_block"}:
            blockers.append(f"scan_diagnostic_{code}")

    return blockers


def _candidate_from_signal(
    signal: dict,
    rules: dict,
    asset_class: str,
    style: str,
) -> dict:
    direction = _normalize_direction(signal.get("direction"))
    coherence_score, coherence_reason, htf_alignment = _coherence(signal)
    source_fidelity = _source_fidelity(signal)
    session_score, session_reason = _session_quality(signal, asset_class)
    warnings = _string_list(signal.get("warnings"))
    soft_blockers: list[str] = []

    min_coherence = _as_float(rules.get("min_coherence"))
    if min_coherence is not None and coherence_score < min_coherence:
        soft_blockers.append("low_coherence")
    source_floor = _as_float(rules.get("low_source_fidelity_floor"))
    if source_floor is not None and source_fidelity["score"] < source_floor:
        soft_blockers.append("low_source_fidelity")

    return {
        "symbol": _symbol(signal),
        "analysisTimeframes": list(ANALYSIS_TIMEFRAMES),
        "displayTimeframe": DISPLAY_TIMEFRAME,
        "reviewTimeframe": _review_timeframe(signal, style),
        "direction": direction,
        "confluenceScore": _as_float(signal.get("confluenceScore")) or 0.0,
        "engineAVerdict": (
            signal.get("engineAVerdict")
            or signal.get("signalTier")
            or signal.get("engine_a_verdict")
        ),
        "shortlistRank": None,
        "shortlistPassed": True,
        "shortlistReasons": ["passes_hard_filters"],
        "shortlistBlockers": soft_blockers,
        "rr": _as_float(signal.get("rr")),
        "regime": signal.get("regime") or signal.get("regimeName"),
        "session": _session_value(signal),
        "freshnessDiagnostics": signal.get("dataFreshness"),
        "sourceFidelity": source_fidelity,
        "coherenceScore": coherence_score,
        "coherenceReason": coherence_reason,
        "triageVerdict": None,
        "triageReason": None,
        "triageConfidence": None,
        "readyForChartReview": True,
        "reviewAction": "OPEN_CHART_REVIEW",
        "chartAiStatus": "NOT_RUN",
        "actionState": "READY_FOR_CHART_REVIEW",
        "sessionQualityScore": session_score,
        "sessionQualityReason": session_reason,
        "htfAlignmentScore": htf_alignment,
        "regimeSuitabilityScore": _regime_suitability(signal.get("regime")),
        "warningSeverityScore": _warning_severity(warnings, soft_blockers),
        "trendScore": _trend_score(signal),
        "spreadStatus": signal.get("spreadStatus") or signal.get("spread_status"),
        "engineAReasons": _engine_a_reasons(signal),
        "warnings": warnings,
    }


def _rank_key(row: dict) -> tuple:
    return (
        -float(row.get("confluenceScore") or 0.0),
        -float(row.get("coherenceScore") or 0.0),
        -float(row.get("htfAlignmentScore") or 0.0),
        -float(row.get("rr") or 0.0),
        -float(row.get("regimeSuitabilityScore") or 0.0),
        -float((row.get("sourceFidelity") or {}).get("score") or 0.0),
        float(row.get("warningSeverityScore") or 0.0),
        str(row.get("symbol") or ""),
    )


def _triage_payload(row: dict) -> dict:
    return {
        "symbol": row.get("symbol"),
        "direction": row.get("direction"),
        "displayTimeframe": row.get("displayTimeframe"),
        "confluenceScore": row.get("confluenceScore"),
        "trendScore": row.get("trendScore"),
        "regime": row.get("regime"),
        "session": row.get("session"),
        "spreadStatus": row.get("spreadStatus"),
        "rr": row.get("rr"),
        "engineAReasons": row.get("engineAReasons") or [],
        "warnings": row.get("warnings") or [],
    }


def _is_busy_scan(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("busy") is True:
        return True
    error = str(result.get("error") or "").strip().lower()
    return "scan already in progress" in error


def _provenance_blockers(signal: dict) -> list[str]:
    blockers: list[str] = []
    meta = signal.get("candleFetchMeta")
    if not isinstance(meta, dict):
        blockers.append("missing_candle_fetch_meta")
    else:
        for tf in ANALYSIS_TIMEFRAMES:
            row = meta.get(tf)
            if not isinstance(row, dict):
                blockers.append(f"missing_candle_fetch_meta_{tf}")
                continue
            severity = _first_text(
                row,
                "stalenessSeverity",
                "severity",
                "detail",
                "reason",
                "status",
            )
            if _provenance_status_blocks(severity):
                blockers.append(f"candle_provenance_{tf}_{severity}")
            if row.get("lastBarStale") is True:
                blockers.append(f"candle_provenance_{tf}_last_bar_stale")

    atr_diag = signal.get("atrDiagnostics")
    if not isinstance(atr_diag, dict):
        blockers.append("missing_atr_diagnostics")
    else:
        atr_value = _as_float(atr_diag.get("atr_value"))
        if atr_value is None or atr_value <= 0:
            blockers.append("invalid_atr_value")
        if not str(atr_diag.get("atr_tf") or "").strip():
            blockers.append("missing_atr_tf")
        if not str(atr_diag.get("atr_source") or "").strip():
            blockers.append("missing_atr_source")
        if atr_diag.get("atr_confirmed_only") is False:
            blockers.append("atr_not_confirmed_only")

    atr_freshness = signal.get("atrFreshness")
    if isinstance(atr_freshness, dict) and atr_freshness.get("stale") is True:
        blockers.append("stale_atr")
    return blockers


def _freshness_blocker(value: Any) -> str | None:
    if not isinstance(value, dict):
        return "missing_data_freshness"
    if value.get("allowed") is False:
        return "data_freshness_blocked"
    blocked = value.get("blocked")
    if isinstance(blocked, list) and blocked:
        return "data_freshness_blocked"
    for key in ("status", "reason", "severity"):
        text = str(value.get(key) or "").strip().lower()
        if "stale" in text or "blocked" in text:
            return "data_freshness_blocked"
    return None


def _source_fidelity(signal: dict) -> dict:
    meta = signal.get("candleFetchMeta") if isinstance(signal.get("candleFetchMeta"), dict) else {}
    raw_source = str(meta.get("pairSource") or meta.get("source") or "unknown").strip()
    source = raw_source or "unknown"
    freshness = _freshness_label(signal.get("dataFreshness"))
    source_l = source.lower()

    if freshness != "fresh":
        score = 0.2
    elif not source_l or source_l == "unknown":
        score = 0.25
    elif any(token in source_l for token in FALLBACK_SOURCE_TOKENS):
        score = 0.45
    elif source_l in TRUSTED_PAIR_SOURCES:
        score = 0.9
    else:
        score = 0.55

    warning_text = " ".join(_string_list(signal.get("warnings"))).lower()
    if any(token in warning_text for token in ("proxy", "synthetic", "fallback", "stale")):
        score = min(score, 0.45)

    return {
        "score": round(float(score), 2),
        "pairSource": source,
        "dataFreshness": freshness,
        "derived": True,
    }


def _coherence(signal: dict) -> tuple[float, str, float]:
    diagnostics = signal.get("factorDiagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = signal.get("factor_diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}

    trend = diagnostics.get("trendCoherence")
    if not isinstance(trend, dict):
        trend = diagnostics.get("trend_coherence")
    if not isinstance(trend, dict):
        trend = signal.get("trendCoherence")
    if not isinstance(trend, dict):
        return 0.0, "not_available", 0.0

    ratio = _as_float(trend.get("coherence_ratio"))
    if ratio is None:
        return 0.0, "not_available", 0.0

    coverage = _as_float(trend.get("tf_coverage"))
    agreement = _as_float(trend.get("agreement_count"))
    if coverage is not None:
        htf = max(0.0, min(1.0, coverage))
    elif agreement is not None:
        htf = max(0.0, min(1.0, agreement / 3.0))
    else:
        htf = 0.0
    return max(0.0, min(1.0, ratio)), "trendCoherence.coherence_ratio", htf


def _session_quality(signal: dict, asset_class: str) -> tuple[float, str]:
    sig_class = str(signal.get("type") or asset_class or "").strip().lower()
    if sig_class == "forex":
        return 0.0, "neutral_for_forex"
    session = signal.get("session")
    if not isinstance(session, dict):
        return 0.0, "not_available"
    quality = str(session.get("quality") or session.get("sessionQuality") or "").upper()
    if quality in {"CLEAN_DELIVERY", "GOOD"}:
        return 0.75, "session_quality_positive"
    if quality in {"CHOP_RISK", "NO_TRADE_WINDOW"}:
        return 0.25, "session_quality_caution"
    return 0.0, "not_available"


def _regime_suitability(regime: Any) -> float:
    text = str(regime or "").strip().upper()
    if text in {"TRENDING", "TREND", "EXPANSION"}:
        return 1.0
    if text in {"RANGING", "RANGE"}:
        return 0.6
    if text in {"CHOP", "CHOPPY", "DEAD"}:
        return 0.25
    return 0.5


def _warning_severity(warnings: list[str], soft_blockers: list[str]) -> float:
    severity = len(warnings) + len(soft_blockers)
    for warning in warnings:
        text = warning.lower()
        if "risk" in text or "blocked" in text or "stale" in text:
            severity += 1
    return float(severity)


def _review_timeframe(signal: dict, style: str) -> str:
    effective = str(signal.get("style") or style or "").strip().lower()
    return "H1" if effective in {"scalp", "intraday"} else "H4"


def _trend_score(signal: dict) -> float | None:
    scores = signal.get("factorScores")
    if isinstance(scores, dict):
        val = _as_float(scores.get("trend"))
        if val is not None:
            return val
    diagnostics = signal.get("factorDiagnostics")
    if isinstance(diagnostics, dict):
        val = _as_float(diagnostics.get("trendScore"))
        if val is not None:
            return val
    return None


def _engine_a_reasons(signal: dict) -> list[str]:
    for key in ("engineAReasons", "engine_a_reasons", "reasons"):
        reasons = _string_list(signal.get(key))
        if reasons:
            return reasons
    diagnostics = signal.get("scanDiagnostics")
    out = []
    for item in _as_list(diagnostics):
        if isinstance(item, dict):
            detail = str(item.get("detail") or item.get("reason") or "").strip()
            if detail:
                out.append(detail)
    return out


def _freshness_label(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    if value.get("allowed") is False:
        return "stale"
    reason = str(value.get("reason") or value.get("status") or "").strip().lower()
    if "stale" in reason or "blocked" in reason:
        return "stale"
    if reason in {"fresh", "ok"} or value.get("allowed") is True:
        return "fresh"
    return reason or "unknown"


def _provenance_status_blocks(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text in {"fresh", "ok", "d1_calendar_gap_policy_ok", "confirmed_only_ok"}:
        return False
    return any(token in text for token in ("stale", "blocked", "invalid", "mismatch"))


def _normalize_direction(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text or None


def _symbol(signal: dict) -> str:
    return str(
        signal.get("symbol")
        or signal.get("pair")
        or signal.get("display")
        or ""
    ).strip()


def _symbol_key(signal: dict) -> str:
    return _symbol(signal).upper()


def _session_value(signal: dict) -> Any:
    session = signal.get("session")
    if isinstance(session, dict):
        return session.get("name") or session.get("session") or session
    return session


def _first_text(mapping: dict, *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return str(value)
    return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_float(value: Any, low: float, high: float) -> float:
    num = _as_float(value)
    if num is None:
        return low
    return max(low, min(high, num))


def _safe_int(value: Any, default: int | None = 0) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]
