from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from engine_a_v3.routing import KNOWN_SCORE_GROUPS


CORE_COMPONENTS = ("trend", "momentum", "location", "volume")
EXIT_POLICIES = ("SINGLE_TP1", "SPLIT_50_50")

# Baseline fallback bar on the rescaled 0..3 confluence scale (see quant_scorer
# "aligned quality" aggregation, 2026-06-18). Live V3 resolves the per-group
# trade threshold from ENGINE_A_SCORE_GROUP_THRESHOLDS via
# _resolved_trade_threshold(); this constant is only the defensive fallback when
# neither the group nor "default" is present in config. Execution stays gated by
# engine_a_trade_gate regardless.
_BASELINE_THRESHOLD = 2.0

_FAMILY_WEIGHTS = {
    "forex": {"trend": .42, "momentum": .28, "location": .21, "volume": .09},
    "crypto": {"trend": .36, "momentum": .27, "location": .18, "volume": .19},
    "commodity": {"trend": .43, "momentum": .25, "location": .20, "volume": .12},
    "index": {"trend": .38, "momentum": .25, "location": .18, "volume": .19},
    "equity_etf": {"trend": .36, "momentum": .23, "location": .18, "volume": .23},
    "unknown": {"trend": .40, "momentum": .27, "location": .20, "volume": .13},
}


def _resolved_weights(score_group: str, family: str) -> dict[str, float]:
    """Resolve per-group component weights from config.

    Wires ``ENGINE_A_QUANT_WEIGHTS_BY_GROUP`` (config.yaml) so operator tuning of
    per-group weights takes live effect. An override entry is merged over the
    family default, restricted to CORE_COMPONENTS (unknown keys ignored), and
    renormalized to sum 1.0 so it always satisfies EngineAV3Profile.create's
    weight schema. Missing/empty/invalid entries fall back to the family default.
    """
    base = dict(_FAMILY_WEIGHTS[family])
    try:
        from config import CONFIG

        keyed = CONFIG.get("ENGINE_A_QUANT_WEIGHTS_BY_GROUP") or {}
    except Exception:
        return base
    override = keyed.get(score_group) if isinstance(keyed, dict) else None
    if not isinstance(override, dict):
        return base
    merged = dict(base)
    changed = False
    for key, value in override.items():
        if key not in CORE_COMPONENTS:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed >= 0:
            merged[key] = parsed
            changed = True
    if not changed:
        return base
    total = sum(merged.values())
    if total <= 0:
        return base
    return {key: value / total for key, value in merged.items()}


def _resolved_trade_threshold(score_group: str) -> float:
    """Resolve the per-group V3 trade threshold from config.

    Wires the V3 baseline profile to ``ENGINE_A_SCORE_GROUP_THRESHOLDS`` so
    operator tuning of per-group thresholds (forex_majors 2.1, crypto_other 2.2,
    forex_exotics 1.7, etc.) takes live effect. Resolution order mirrors
    ``scoring._configured_score_threshold``: score_group → "default" →
    ``_BASELINE_THRESHOLD`` (defensive fallback when config is absent).
    """
    from config import CONFIG

    group_thresholds = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS", {}) or {}
    for key in (score_group, "default"):
        value = group_thresholds.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            break
    return _BASELINE_THRESHOLD


_SCORER_SHA_CACHE: tuple[tuple, str] | None = None


def scorer_sha256() -> str:
    """Content hash of the scorer sources, memoized by file stat.

    The hash is recomputed whenever any source file's (mtime_ns, size)
    changes, so an edited scorer is picked up immediately; unchanged files
    are not re-read/re-hashed on every call (this runs per scan/backtest bar).
    """
    global _SCORER_SHA_CACHE
    root = Path(__file__).parent
    names = ("profile.py", "indicator_adapter.py", "quant_scorer.py")
    key_parts: list[tuple] = []
    for name in names:
        try:
            stat = (root / name).stat()
            key_parts.append((name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            key_parts.append((name, None, None))
    key = tuple(key_parts)
    if _SCORER_SHA_CACHE is not None and _SCORER_SHA_CACHE[0] == key:
        return _SCORER_SHA_CACHE[1]
    digest = hashlib.sha256()
    for name in names:
        path = root / name
        if path.exists():
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes())
    result = digest.hexdigest()
    _SCORER_SHA_CACHE = (key, result)
    return result


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EngineAV3Profile:
    profile_id: str
    score_group: str
    horizon: str
    indicator_periods: tuple[tuple[str, int], ...]
    weights: tuple[tuple[str, float], ...]
    direction_deadband: float
    trade_threshold: float
    exit_policy: str
    profile_sha256: str
    scorer_sha256: str
    status: str
    created_at: str | None = None
    valid_until: str | None = None

    @classmethod
    def create(
        cls,
        *,
        score_group: str,
        horizon: str,
        indicator_periods: Mapping[str, Any],
        weights: Mapping[str, Any],
        direction_deadband: float,
        trade_threshold: float,
        exit_policy: str,
        status: str,
        profile_id: str | None = None,
        created_at: str | None = None,
        valid_until: str | None = None,
        expected_scorer_sha256: str | None = None,
    ) -> "EngineAV3Profile":
        if score_group not in KNOWN_SCORE_GROUPS or horizon not in {"intraday", "swing"}:
            raise ValueError("profile_route_invalid")
        try:
            parsed_weights = {str(k): float(v) for k, v in weights.items()}
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("profile_weights_invalid") from exc
        if (
            set(parsed_weights) != set(CORE_COMPONENTS)
            or any(not math.isfinite(v) or v < 0 for v in parsed_weights.values())
            or not math.isclose(sum(parsed_weights.values()), 1.0, abs_tol=1e-9)
        ):
            raise ValueError("profile_weights_invalid")
        try:
            periods = {str(k): int(v) for k, v in indicator_periods.items()}
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("profile_periods_invalid") from exc
        required_periods = {"ema_trend", "ema_momentum", "ema_long", "rsi", "macd_fast", "macd_slow", "macd_signal", "atr", "adx"}
        if not required_periods.issubset(periods) or any(v <= 0 for v in periods.values()):
            raise ValueError("profile_periods_invalid")
        deadband = float(direction_deadband)
        threshold = float(trade_threshold)
        if not math.isfinite(deadband) or not 0 <= deadband <= 1:
            raise ValueError("profile_deadband_invalid")
        if not math.isfinite(threshold) or not 0 <= threshold <= 3:
            raise ValueError("profile_threshold_invalid")
        if exit_policy not in EXIT_POLICIES:
            raise ValueError("profile_exit_policy_invalid")
        scorer_hash = expected_scorer_sha256 or scorer_sha256()
        payload = {
            "scoreGroup": score_group, "horizon": horizon,
            "indicatorPeriods": periods, "weights": parsed_weights,
            "directionDeadband": deadband, "tradeThreshold": threshold,
            "exitPolicy": exit_policy, "scorerSha256": scorer_hash,
            "status": status, "createdAt": created_at, "validUntil": valid_until,
        }
        profile_hash = _canonical_hash(payload)
        return cls(
            profile_id=profile_id or f"baseline-{score_group}-{horizon}",
            score_group=score_group,
            horizon=horizon,
            indicator_periods=tuple(sorted(periods.items())),
            weights=tuple(sorted(parsed_weights.items())),
            direction_deadband=deadband,
            trade_threshold=threshold,
            exit_policy=exit_policy,
            profile_sha256=profile_hash,
            scorer_sha256=scorer_hash,
            status=status,
            created_at=created_at,
            valid_until=valid_until,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id, "scoreGroup": self.score_group,
            "horizon": self.horizon, "indicatorPeriods": dict(self.indicator_periods),
            "weights": dict(self.weights), "directionDeadband": self.direction_deadband,
            "tradeThreshold": self.trade_threshold, "exitPolicy": self.exit_policy,
            "profileSha256": self.profile_sha256, "scorerSha256": self.scorer_sha256,
            "status": self.status, "createdAt": self.created_at, "validUntil": self.valid_until,
        }


def _family_for(group: str) -> str:
    if group.startswith("forex_"): return "forex"
    if group.startswith("crypto_"): return "crypto"
    if group in {"us_indices_trackers", "eu_indices", "asian_indices", "index_other"}: return "index"
    if group in {"us_stock_single", "bond_tlt", "smallcap_em_etf", "stock_other"}: return "equity_etf"
    if group != "unknown": return "commodity"
    return "unknown"


def _resolved_periods(group: str, family: str) -> dict[str, int]:
    from factor_scoring import _resolve_atr_adx_periods, _resolve_ema_periods, _resolve_macd_params, _resolve_rsi_period
    asset = "stock" if family == "equity_etf" else family
    ema = _resolve_ema_periods(group, asset)
    macd = _resolve_macd_params(group, asset)
    aa = _resolve_atr_adx_periods(group, asset)
    return {
        "ema_trend": int(ema["trend"]), "ema_momentum": int(ema["momentum"]), "ema_long": int(ema["long"]),
        "rsi": int(_resolve_rsi_period(group, asset)), "macd_fast": int(macd["fast"]),
        "macd_slow": int(macd["slow"]), "macd_signal": int(macd["signal"]),
        "atr": int(aa["atr"]), "adx": int(aa["adx"]),
    }


def baseline_profile(score_group: str, horizon: str) -> EngineAV3Profile:
    family = _family_for(score_group)
    deadband = 0.05
    try:
        from config import CONFIG

        raw = CONFIG.get("ENGINE_A_DIRECTION_MIN_MARGIN")
        if raw is not None:
            deadband = float(raw)
    except Exception:
        pass
    return EngineAV3Profile.create(
        score_group=score_group, horizon=horizon,
        indicator_periods=_resolved_periods(score_group, family),
        weights=_resolved_weights(score_group, family), direction_deadband=deadband,
        trade_threshold=_resolved_trade_threshold(score_group),
        exit_policy="SINGLE_TP1", status="UNVALIDATED",
    )
