"""Engine A v3 explicit cross-sectional ranking.

Pair scoring stays independent: ``quant_scorer.score_pair`` / ``evaluate_engine_a_v3``
still emit a continuous 0–3.0 quality score and a direction. This module ranks an
already-scored cohort and restricts TRADE *promotion* to the strongest names in
each universe group.

It never changes component scores, never upgrades WATCH/NO_SIGNAL to TRADE, and
is a no-op when disabled so absolute-threshold behavior is unchanged.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("athena.engine_a_v3.cross_sectional")

CONFIG_KEY = "ENGINE_A_V3_CROSS_SECTIONAL"
RANKING_FIELD = "crossSectionalRanking"

_SURFACES = frozenset({"live_scan", "backtest"})
_GROUP_BY = frozenset({"score_group", "asset_type", "custom"})
_METHODS = frozenset({"top_n", "percentile"})
_KNOWN_TIE_BREAKERS = (
    "conviction",
    "direction_confidence",
    "location_quality",
    "regime_alignment",
)

REASON_DISABLED = "ranking_disabled"
REASON_SURFACE_OFF = "surface_not_enabled"
REASON_NOT_V3 = "not_engine_a_v3"
REASON_NO_SIGNAL = "no_signal"
REASON_NO_DIRECTION = "no_direction"
REASON_SCORE_INVALID = "score_invalid"
REASON_BELOW_FLOOR = "cross_sectional_below_min_score_floor"
REASON_NOT_TRADE = "not_trade_decision"
REASON_NOT_QUALIFIED = "not_qualified"
REASON_GROUP_TOO_SMALL = "group_below_min_size"
REASON_ACCEPTED = "rank_within_cutoff"
REASON_BELOW_CUTOFF = "cross_sectional_rank_below_cutoff"


@dataclass(frozen=True)
class CrossSectionalConfig:
    enabled: bool
    method: str
    top_n: int
    percentile: float
    group_by: str
    custom_groups: tuple[tuple[str, str], ...]
    min_score_floor: float
    min_group_size: int
    require_trade_decision: bool
    require_qualified: bool
    apply_in_live_scan: bool
    apply_in_backtest: bool
    tie_breakers: tuple[str, ...]
    valid: bool
    invalid_reason: str | None = None

    def applies_to(self, surface: str) -> bool:
        if not self.enabled or not self.valid:
            return False
        key = str(surface or "").strip().lower()
        if key == "live_scan":
            return bool(self.apply_in_live_scan)
        if key == "backtest":
            return bool(self.apply_in_backtest)
        return False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "method": self.method,
            "topN": self.top_n,
            "percentile": self.percentile,
            "groupBy": self.group_by,
            "minScoreFloor": self.min_score_floor,
            "minGroupSize": self.min_group_size,
            "requireTradeDecision": self.require_trade_decision,
            "requireQualified": self.require_qualified,
            "applyInLiveScan": self.apply_in_live_scan,
            "applyInBacktest": self.apply_in_backtest,
            "tieBreakers": list(self.tie_breakers),
            "valid": self.valid,
            "invalidReason": self.invalid_reason,
        }


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _cfg_mapping(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if isinstance(config, Mapping):
        return config
    try:
        from config import CONFIG

        return CONFIG if isinstance(CONFIG, Mapping) else {}
    except Exception:
        return {}


def resolve_cross_sectional_config(
    config: Mapping[str, Any] | None = None,
) -> CrossSectionalConfig:
    """Normalize ``ENGINE_A_V3_CROSS_SECTIONAL``. Invalid enabled blocks stay off."""
    root = _cfg_mapping(config)
    raw = root.get(CONFIG_KEY)
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        return CrossSectionalConfig(
            enabled=False,
            method="top_n",
            top_n=3,
            percentile=0.20,
            group_by="score_group",
            custom_groups=(),
            min_score_floor=0.0,
            min_group_size=1,
            require_trade_decision=True,
            require_qualified=True,
            apply_in_live_scan=True,
            apply_in_backtest=True,
            tie_breakers=("direction_confidence", "location_quality", "conviction"),
            valid=False,
            invalid_reason="config_not_a_mapping",
        )

    enabled = _as_bool(raw.get("ENABLED"), False)
    method = str(raw.get("METHOD") or "top_n").strip().lower()
    group_by = str(raw.get("GROUP_BY") or "score_group").strip().lower()
    invalid: list[str] = []
    if method not in _METHODS:
        invalid.append("method_invalid")
        method = "top_n"
    if group_by not in _GROUP_BY:
        invalid.append("group_by_invalid")
        group_by = "score_group"

    try:
        top_n = int(raw.get("TOP_N", 3) or 3)
    except (TypeError, ValueError):
        top_n = 3
        invalid.append("top_n_invalid")
    if top_n < 1:
        top_n = 3
        invalid.append("top_n_invalid")

    try:
        percentile = float(raw.get("PERCENTILE", 0.20))
    except (TypeError, ValueError):
        percentile = 0.20
        invalid.append("percentile_invalid")
    if not math.isfinite(percentile) or percentile <= 0.0 or percentile > 1.0:
        percentile = 0.20
        invalid.append("percentile_invalid")

    try:
        min_score_floor = float(raw.get("MIN_SCORE_FLOOR", 0.0) or 0.0)
    except (TypeError, ValueError):
        min_score_floor = 0.0
        invalid.append("min_score_floor_invalid")
    if not math.isfinite(min_score_floor) or min_score_floor < 0.0:
        min_score_floor = 0.0
        invalid.append("min_score_floor_invalid")

    try:
        min_group_size = int(raw.get("MIN_GROUP_SIZE", 1) or 1)
    except (TypeError, ValueError):
        min_group_size = 1
        invalid.append("min_group_size_invalid")
    if min_group_size < 1:
        min_group_size = 1
        invalid.append("min_group_size_invalid")

    custom_raw = raw.get("CUSTOM_GROUPS") or {}
    custom_pairs: list[tuple[str, str]] = []
    if custom_raw and not isinstance(custom_raw, Mapping):
        invalid.append("custom_groups_invalid")
    elif isinstance(custom_raw, Mapping):
        for key, value in custom_raw.items():
            label = str(value or "").strip()
            name = str(key or "").strip()
            if name and label:
                custom_pairs.append((name, label))

    raw_breakers = raw.get("TIE_BREAKERS")
    tie_breakers: list[str] = []
    if raw_breakers is None:
        tie_breakers = ["direction_confidence", "location_quality", "conviction"]
    elif isinstance(raw_breakers, (list, tuple)):
        for item in raw_breakers:
            key = str(item or "").strip().lower()
            if key in _KNOWN_TIE_BREAKERS and key not in tie_breakers:
                tie_breakers.append(key)
    else:
        invalid.append("tie_breakers_invalid")
        tie_breakers = ["direction_confidence", "location_quality", "conviction"]

    valid = not invalid
    if enabled and not valid:
        log.error(
            "[ENGINE_A_V3_XSEC] enabled config invalid (%s); ranking will not apply",
            ",".join(invalid),
        )
    return CrossSectionalConfig(
        enabled=enabled,
        method=method,
        top_n=top_n,
        percentile=percentile,
        group_by=group_by,
        custom_groups=tuple(custom_pairs),
        min_score_floor=min_score_floor,
        min_group_size=min_group_size,
        require_trade_decision=_as_bool(raw.get("REQUIRE_TRADE_DECISION"), True),
        require_qualified=_as_bool(raw.get("REQUIRE_QUALIFIED"), True),
        apply_in_live_scan=_as_bool(raw.get("APPLY_IN_LIVE_SCAN"), True),
        apply_in_backtest=_as_bool(raw.get("APPLY_IN_BACKTEST"), True),
        tie_breakers=tuple(tie_breakers),
        valid=valid,
        invalid_reason=",".join(invalid) if invalid else None,
    )


def ranking_enabled(config: Mapping[str, Any] | None = None) -> bool:
    cfg = resolve_cross_sectional_config(config)
    return bool(cfg.enabled and cfg.valid)


def _is_engine_a_v3_signal(signal: Mapping[str, Any] | None) -> bool:
    if not isinstance(signal, Mapping):
        return False
    engine = str(signal.get("engine") or "").upper()
    version = str(signal.get("contractVersion") or "")
    return engine == "ENGINE_A_V3" and version.startswith("3.")


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _norm_symbol(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _pair_name(signal: Mapping[str, Any], pair: Mapping[str, Any] | None) -> str:
    pair = pair or {}
    return str(
        signal.get("display")
        or signal.get("pair")
        or pair.get("display")
        or pair.get("pair")
        or signal.get("symbol")
        or pair.get("symbol")
        or ""
    )


def _score_group(signal: Mapping[str, Any], pair: Mapping[str, Any] | None) -> str:
    pair = pair or {}
    return str(
        signal.get("scoreGroup")
        or signal.get("score_group")
        or pair.get("scoreGroup")
        or pair.get("score_group")
        or "unknown"
    ).strip() or "unknown"


def _asset_type(signal: Mapping[str, Any], pair: Mapping[str, Any] | None) -> str:
    pair = pair or {}
    return str(
        signal.get("type")
        or signal.get("asset_type")
        or pair.get("type")
        or pair.get("asset_type")
        or "other"
    ).strip().lower() or "other"


def resolve_group_key(
    signal: Mapping[str, Any],
    pair: Mapping[str, Any] | None,
    cfg: CrossSectionalConfig,
) -> str:
    if cfg.group_by == "asset_type":
        return _asset_type(signal, pair)
    if cfg.group_by == "custom":
        lookup = {}
        for raw_key, label in cfg.custom_groups:
            lookup[str(raw_key).strip()] = label
            lookup[_norm_symbol(raw_key)] = label
        candidates = (
            _pair_name(signal, pair),
            signal.get("symbol"),
            (pair or {}).get("symbol"),
            (pair or {}).get("display"),
        )
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text and text in lookup:
                return lookup[text]
            normalized = _norm_symbol(text)
            if normalized and normalized in lookup:
                return lookup[normalized]
        return _score_group(signal, pair)
    return _score_group(signal, pair)


def _components(signal: Mapping[str, Any]) -> Mapping[str, Any]:
    diagnostics = signal.get("factorDiagnostics")
    if isinstance(diagnostics, Mapping):
        comps = diagnostics.get("components")
        if isinstance(comps, Mapping):
            return comps
    comps = signal.get("componentScores")
    return comps if isinstance(comps, Mapping) else {}


def _component_quality(signal: Mapping[str, Any], name: str) -> float:
    comp = _components(signal).get(name)
    if not isinstance(comp, Mapping):
        return 0.0
    value = _finite_float(comp.get("quality"))
    return 0.0 if value is None else value


def _component_abs_signal(signal: Mapping[str, Any], name: str) -> float:
    comp = _components(signal).get(name)
    if not isinstance(comp, Mapping):
        return 0.0
    value = _finite_float(comp.get("signal"))
    return 0.0 if value is None else abs(value)


def extract_ranking_score(signal: Mapping[str, Any]) -> float | None:
    for key in ("confluenceScore", "score"):
        value = _finite_float(signal.get(key))
        if value is not None:
            return value
    return None


def extract_tie_breaker_values(
    signal: Mapping[str, Any],
    names: Sequence[str],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for name in names:
        if name == "conviction":
            value = _finite_float(signal.get("conviction"))
            if value is None:
                value = _finite_float(signal.get("scoreNorm"))
            out[name] = 0.0 if value is None else value
        elif name == "direction_confidence":
            out[name] = _component_abs_signal(signal, "trend") * _component_quality(
                signal, "trend"
            )
        elif name == "location_quality":
            out[name] = _component_quality(signal, "location")
        elif name == "regime_alignment":
            out[name] = _component_quality(signal, "trend")
    return out


def _cutoff_count(eligible_n: int, cfg: CrossSectionalConfig) -> int:
    if eligible_n <= 0:
        return 0
    if cfg.method == "percentile":
        raw = int(math.ceil(eligible_n * cfg.percentile))
        return max(1, min(eligible_n, raw))
    return min(cfg.top_n, eligible_n)


def _iter_cohort(
    cohort: Sequence[Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in cohort:
        if isinstance(item, tuple) and len(item) == 2:
            pair, signal = item
            if isinstance(signal, dict):
                rows.append((pair if isinstance(pair, dict) else {}, signal))
            continue
        if isinstance(item, dict):
            rows.append(({}, item))
    return rows


def _eligibility(
    signal: Mapping[str, Any],
    cfg: CrossSectionalConfig,
) -> tuple[bool, str, float | None]:
    if not _is_engine_a_v3_signal(signal):
        return False, REASON_NOT_V3, None
    decision = str(signal.get("decision") or "").upper()
    if decision == "NO_SIGNAL":
        return False, REASON_NO_SIGNAL, extract_ranking_score(signal)
    direction = str(signal.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, REASON_NO_DIRECTION, extract_ranking_score(signal)
    score = extract_ranking_score(signal)
    if score is None:
        return False, REASON_SCORE_INVALID, None
    if score + 1e-12 < cfg.min_score_floor:
        return False, REASON_BELOW_FLOOR, score
    if cfg.require_trade_decision and decision != "TRADE":
        return False, REASON_NOT_TRADE, score
    if cfg.require_qualified and signal.get("qualified") is not True:
        return False, REASON_NOT_QUALIFIED, score
    return True, REASON_ACCEPTED, score


def _annotation(
    *,
    cfg: CrossSectionalConfig,
    surface: str,
    group_key: str,
    eligible: bool,
    rank: int | None,
    eligible_count: int,
    group_size: int,
    cutoff: int,
    accepted: bool,
    reason: str,
    ranking_score: float | None,
    tie_breakers: Mapping[str, float] | None,
    applied: bool,
) -> dict[str, Any]:
    payload = {
        "enabled": True,
        "applied": bool(applied),
        "surface": surface,
        "groupKey": group_key,
        "groupBy": cfg.group_by,
        "method": cfg.method,
        "topN": cfg.top_n,
        "percentile": cfg.percentile,
        "minScoreFloor": cfg.min_score_floor,
        "groupSize": group_size,
        "eligibleCount": eligible_count,
        "cutoff": cutoff,
        "rank": rank,
        "eligible": bool(eligible),
        "accepted": bool(accepted),
        "reason": reason,
        "rankingScore": None if ranking_score is None else round(float(ranking_score), 6),
        "tieBreakers": dict(tie_breakers or {}),
    }
    return payload


def rank_cross_sectional_cohort(
    cohort: Sequence[Any],
    *,
    surface: str,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Pure ranking. Returns one annotation per input row; does not mutate."""
    cfg = resolve_cross_sectional_config(config)
    rows = _iter_cohort(cohort)
    annotations: list[dict[str, Any]] = [
        {
            "enabled": False,
            "applied": False,
            "surface": surface,
            "reason": REASON_DISABLED,
        }
        for _ in rows
    ]
    if not rows:
        return annotations
    if not cfg.enabled or not cfg.valid:
        if cfg.enabled and not cfg.valid:
            for item in annotations:
                item["reason"] = cfg.invalid_reason or "config_invalid"
                item["enabled"] = True
        return annotations
    if not cfg.applies_to(surface):
        for item in annotations:
            item["enabled"] = True
            item["reason"] = REASON_SURFACE_OFF
        return annotations

    grouped: dict[str, list[int]] = defaultdict(list)
    meta: list[dict[str, Any]] = []
    for index, (pair, signal) in enumerate(rows):
        eligible, reason, score = _eligibility(signal, cfg)
        breakers = extract_tie_breaker_values(signal, cfg.tie_breakers)
        group_key = resolve_group_key(signal, pair, cfg) if _is_engine_a_v3_signal(signal) else ""
        meta.append(
            {
                "eligible": eligible,
                "reason": reason,
                "score": score,
                "tie_breakers": breakers,
                "group_key": group_key or "ungrouped",
                "pair_name": _pair_name(signal, pair),
                "is_v3": _is_engine_a_v3_signal(signal),
            }
        )
        if meta[-1]["is_v3"]:
            grouped[meta[-1]["group_key"]].append(index)

    for group_key, indexes in grouped.items():
        group_size = len(indexes)
        eligible_idxs = [i for i in indexes if meta[i]["eligible"]]
        eligible_count = len(eligible_idxs)
        cutoff = _cutoff_count(eligible_count, cfg)
        rank_skipped = eligible_count < cfg.min_group_size
        ordered = sorted(
            eligible_idxs,
            key=lambda i: (
                -(meta[i]["score"] or 0.0),
                *(-(meta[i]["tie_breakers"].get(name, 0.0)) for name in cfg.tie_breakers),
                str(meta[i]["pair_name"]),
            ),
        )
        rank_by_index = {idx: rank for rank, idx in enumerate(ordered, start=1)}
        for index in indexes:
            row = meta[index]
            if not row["eligible"]:
                accepted = False
                rank = None
                reason = row["reason"]
            elif rank_skipped:
                accepted = True
                rank = rank_by_index.get(index)
                reason = REASON_GROUP_TOO_SMALL
            else:
                rank = rank_by_index.get(index)
                accepted = rank is not None and rank <= cutoff
                reason = REASON_ACCEPTED if accepted else REASON_BELOW_CUTOFF
            annotations[index] = _annotation(
                cfg=cfg,
                surface=surface,
                group_key=group_key,
                eligible=bool(row["eligible"]),
                rank=rank,
                eligible_count=eligible_count,
                group_size=group_size,
                cutoff=0 if rank_skipped else cutoff,
                accepted=accepted,
                reason=reason,
                ranking_score=row["score"],
                tie_breakers=row["tie_breakers"],
                applied=True,
            )
    return annotations


def _attach_annotation(signal: dict[str, Any], annotation: Mapping[str, Any]) -> None:
    payload = dict(annotation)
    signal[RANKING_FIELD] = payload
    diagnostics = signal.get("factorDiagnostics")
    if isinstance(diagnostics, dict):
        diagnostics[RANKING_FIELD] = payload
    elif diagnostics is None:
        signal["factorDiagnostics"] = {RANKING_FIELD: payload}
    diagnostics_list = signal.get("scanDiagnostics")
    if isinstance(diagnostics_list, list) and payload.get("applied"):
        rank = payload.get("rank")
        eligible = payload.get("eligibleCount")
        detail = (
            f"rank {rank}/{eligible} {payload.get('reason')}"
            if rank is not None
            else str(payload.get("reason") or "cross_sectional")
        )
        diagnostics_list.append(
            {
                "code": "cross_sectional_rank",
                "detail": detail,
                "group": payload.get("groupKey"),
                "accepted": payload.get("accepted"),
            }
        )


def _demote_rejected_trade(signal: dict[str, Any], reason: str) -> bool:
    if str(signal.get("decision") or "").upper() != "TRADE":
        return False
    from athena_app.services.engine_a_v3_classify import demote_v3_trade_to_watch

    demote_v3_trade_to_watch(signal, reason=reason)
    if str(signal.get("setupLabel") or "").upper() == "TRADE":
        signal["setupLabel"] = "WATCH"
        signal["setup_label"] = "WATCH"
    return True


def apply_cross_sectional_ranking(
    cohort: Sequence[Any],
    *,
    surface: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Annotate a scored cohort and demote TRADE rows that miss the cutoff.

    ``cohort`` is a sequence of signal dicts or ``(pair, signal)`` tuples.
    Non-V3 rows are left untouched. Disabled / wrong-surface calls are identity
    (no fields added) so existing payloads stay byte-for-byte unchanged.
    """
    cfg = resolve_cross_sectional_config(config)
    rows = _iter_cohort(cohort)
    report: dict[str, Any] = {
        "enabled": bool(cfg.enabled and cfg.valid),
        "applied": False,
        "surface": surface,
        "config": cfg.to_public_dict(),
        "considered": 0,
        "eligible": 0,
        "accepted": 0,
        "rejected": 0,
        "demoted": 0,
        "groups": {},
    }
    if not cfg.applies_to(surface):
        return report

    annotations = rank_cross_sectional_cohort(cohort, surface=surface, config=config)
    report["applied"] = True
    group_summaries: dict[str, dict[str, int]] = {}
    for (pair, signal), annotation in zip(rows, annotations):
        if not annotation.get("applied"):
            continue
        if not _is_engine_a_v3_signal(signal):
            continue
        report["considered"] += 1
        _attach_annotation(signal, annotation)
        group_key = str(annotation.get("groupKey") or "unknown")
        summary = group_summaries.setdefault(
            group_key,
            {"size": 0, "eligible": 0, "accepted": 0, "rejected": 0},
        )
        summary["size"] += 1
        pair_name = _pair_name(signal, pair)
        if annotation.get("eligible"):
            report["eligible"] += 1
            summary["eligible"] += 1
        if annotation.get("accepted"):
            report["accepted"] += 1
            summary["accepted"] += 1
            log.info(
                "[ENGINE_A_V3_XSEC] %s group=%s rank=%s/%s score=%s ACCEPTED (%s)",
                pair_name or "?",
                group_key,
                annotation.get("rank"),
                annotation.get("eligibleCount"),
                annotation.get("rankingScore"),
                annotation.get("reason"),
            )
        else:
            report["rejected"] += 1
            summary["rejected"] += 1
            if _demote_rejected_trade(signal, str(annotation.get("reason") or REASON_BELOW_CUTOFF)):
                report["demoted"] += 1
            log.info(
                "[ENGINE_A_V3_XSEC] %s group=%s rank=%s/%s score=%s REJECTED (%s)",
                pair_name or "?",
                group_key,
                annotation.get("rank"),
                annotation.get("eligibleCount"),
                annotation.get("rankingScore"),
                annotation.get("reason"),
            )
    report["groups"] = group_summaries
    if group_summaries:
        log.info(
            "[ENGINE_A_V3_XSEC] surface=%s method=%s groups=%d considered=%d eligible=%d accepted=%d demoted=%d",
            surface,
            cfg.method,
            len(group_summaries),
            report["considered"],
            report["eligible"],
            report["accepted"],
            report["demoted"],
        )
    return report


def _payload_from_setup_signal(signal: Any, pair: Mapping[str, Any] | None) -> dict[str, Any]:
    pair = pair or {}
    return {
        "engine": getattr(signal, "engine", None),
        "contractVersion": getattr(signal, "contractVersion", None),
        "decision": getattr(signal, "decision", None),
        "qualified": getattr(signal, "qualified", None),
        "direction": getattr(signal, "direction", None),
        "confluenceScore": getattr(signal, "confluenceScore", None),
        "conviction": getattr(signal, "conviction", None),
        "scoreNorm": getattr(signal, "scoreNorm", None),
        "scoreGroup": getattr(signal, "scoreGroup", None),
        "type": getattr(signal, "type", None) or pair.get("type"),
        "pair": getattr(signal, "pair", None),
        "display": getattr(signal, "pair", None) or pair.get("display"),
        "symbol": getattr(signal, "symbol", None) or pair.get("symbol"),
        "factorDiagnostics": getattr(signal, "factorDiagnostics", None),
        "componentScores": getattr(signal, "componentScores", None),
        "rejectionReasons": list(getattr(signal, "rejectionReasons", ()) or ()),
    }


def evaluate_backtest_ranking(
    signal: Any,
    pair: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Universe-of-one ranking for a single backtest/replay bar.

    Does not mutate the frozen ``EngineASetupSignal``. Callers skip the trade
    when the diagnostic is applied and ``accepted`` is false.
    """
    cfg = resolve_cross_sectional_config(config)
    if not cfg.applies_to("backtest"):
        return {"enabled": bool(cfg.enabled and cfg.valid), "applied": False, "reason": REASON_SURFACE_OFF if cfg.enabled else REASON_DISABLED}
    if isinstance(signal, dict):
        payload = dict(signal)
        pair_payload = dict(pair or {})
    else:
        payload = _payload_from_setup_signal(signal, pair)
        pair_payload = dict(pair or {})
    annotations = rank_cross_sectional_cohort(
        [(pair_payload, payload)],
        surface="backtest",
        config=config,
    )
    return annotations[0] if annotations else {"enabled": True, "applied": False}


def backtest_ranking_blocks_trade(
    signal: Any,
    pair: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """True when this bar would have been taken but ranking rejects it."""
    diagnostic = evaluate_backtest_ranking(signal, pair, config=config)
    if not diagnostic.get("applied"):
        return False, diagnostic
    decision = (
        signal.get("decision")
        if isinstance(signal, Mapping)
        else getattr(signal, "decision", None)
    )
    qualified = (
        signal.get("qualified")
        if isinstance(signal, Mapping)
        else getattr(signal, "qualified", None)
    )
    would_take = str(decision or "").upper() == "TRADE" and qualified is True
    return bool(would_take and not diagnostic.get("accepted")), diagnostic
