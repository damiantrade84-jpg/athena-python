from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine_a_v3.contract import ValidationArtifact
from engine_a_v3.routing import SpecialistRoute, route_specialist
from engine_a_v3.profile import EngineAV3Profile, baseline_profile, scorer_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PromotionDecision:
    qualified: bool
    reasons: tuple[str, ...]
    artifact: ValidationArtifact | None
    profile: EngineAV3Profile | None = None


def default_registry_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "Athena" / "models" / "engine_a_v3"


class PromotionRegistry:
    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        allow_demo_unvalidated: bool = False,
    ):
        self.root = Path(root) if root is not None else default_registry_root()
        self.allow_demo_unvalidated = bool(allow_demo_unvalidated)

    def _paths(
        self, route: SpecialistRoute, horizon: str, symbol: str | None
    ) -> tuple[tuple[Path, bool], ...]:
        family_path = self.root / route.family / route.subclass / horizon
        pair_key = re.sub(r"[^A-Z0-9]+", "", str(symbol or "").upper())
        paths: list[tuple[Path, bool]] = []
        if pair_key:
            paths.append((family_path / "pairs" / pair_key / "promotion.json", True))
        paths.append((family_path / "promotion.json", False))
        return tuple(paths)

    def resolve(
        self,
        route: SpecialistRoute,
        horizon: str,
        *,
        symbol: str | None,
        now: datetime | None = None,
    ) -> PromotionDecision:
        for path, pair_adapter in self._paths(route, horizon, symbol):
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                if self.allow_demo_unvalidated:
                    return self._demo_unvalidated(route, horizon, ("promotion_artifact_malformed",))
                return PromotionDecision(False, ("promotion_artifact_malformed",), None)
            decision = validate_promotion_artifact(
                raw,
                route,
                horizon,
                pair_adapter=pair_adapter,
                now=now,
            )
            if decision.qualified or not self.allow_demo_unvalidated:
                return decision
            return self._demo_unvalidated(route, horizon, decision.reasons)
        if self.allow_demo_unvalidated:
            return self._demo_unvalidated(route, horizon, ("promotion_artifact_missing",))
        return PromotionDecision(False, ("promotion_artifact_missing",), None)

    @staticmethod
    def _demo_unvalidated(
        route: SpecialistRoute, horizon: str, reasons: tuple[str, ...]
    ) -> PromotionDecision:
        identity = re.sub(
            r"[^a-z0-9]+", "-",
            f"demo-unvalidated-{route.family}-{route.subclass}-{horizon}".lower(),
        ).strip("-")
        profile = baseline_profile(route.score_group, horizon)
        return PromotionDecision(
            True,
            tuple(dict.fromkeys(reasons + ("demo_unvalidated_activation",))),
            ValidationArtifact(
                artifactId=identity, family=route.family, subclass=route.subclass,
                horizon=horizon, status="DEMO_UNVALIDATED", metrics=(),
                provenanceSha256="0" * 64, profileSha256=profile.profile_sha256,
            ),
            profile,
        )


def demo_unvalidated_registry(
    root: str | os.PathLike[str] | None = None,
) -> PromotionRegistry:
    return PromotionRegistry(root, allow_demo_unvalidated=True)


def production_registry() -> PromotionRegistry:
    enabled = False
    try:
        from config import CONFIG

        enabled = bool(CONFIG.get("ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED", False))
        enabled = enabled and str(
            CONFIG.get("EXECUTOR_MODE", "") or ""
        ).strip().lower() == "demo"
    except Exception:
        enabled = False
    return PromotionRegistry(allow_demo_unvalidated=enabled)


def _number(mapping: dict[str, Any], key: str) -> float | None:
    try:
        value = float(mapping[key])
    except (KeyError, TypeError, ValueError):
        return None
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def validate_promotion_artifact(
    raw: dict[str, Any],
    route: SpecialistRoute,
    horizon: str,
    *,
    pair_adapter: bool = False,
    now: datetime | None = None,
) -> PromotionDecision:
    if not isinstance(raw, dict):
        return PromotionDecision(False, ("promotion_artifact_malformed",), None)

    reasons: list[str] = []
    if raw.get("schemaVersion") != 3:
        reasons.append("promotion_schema_invalid")
    if not str(raw.get("artifactId") or "").strip():
        reasons.append("promotion_artifact_id_missing")
    if raw.get("family") != route.family or raw.get("subclass") != route.subclass:
        reasons.append("promotion_route_mismatch")
    if raw.get("horizon") != horizon:
        reasons.append("promotion_horizon_mismatch")
    if raw.get("status") != "PROMOTED":
        reasons.append("promotion_status_not_promoted")

    scope = raw.get("validationScope")
    if not isinstance(scope, dict):
        reasons.append("promotion_scope_missing")
        scope = {}
    scope_type = str(scope.get("type") or "")
    expected_symbols = scope.get("expectedSymbols")
    validated_symbols = scope.get("validatedSymbols")
    if scope_type not in {"family", "pair"}:
        reasons.append("promotion_scope_invalid")
    if not isinstance(expected_symbols, list) or not expected_symbols:
        reasons.append("promotion_expected_symbols_missing")
        expected_symbols = []
    if not isinstance(validated_symbols, list) or not validated_symbols:
        reasons.append("promotion_validated_symbols_missing")
        validated_symbols = []
    normalized_expected = {
        re.sub(r"[^A-Z0-9]+", "", str(value).upper()) for value in expected_symbols
    }
    normalized_validated = {
        re.sub(r"[^A-Z0-9]+", "", str(value).upper()) for value in validated_symbols
    }
    if (
        not normalized_expected
        or normalized_expected != normalized_validated
        or "" in normalized_expected
    ):
        reasons.append("promotion_cohort_incomplete")
    if pair_adapter:
        if scope_type != "pair" or len(normalized_validated) != 1:
            reasons.append("promotion_pair_scope_invalid")
    elif scope_type != "family":
        reasons.append("promotion_family_scope_invalid")

    current = now or datetime.now(timezone.utc)
    try:
        valid_until = datetime.fromisoformat(
            str(raw.get("validUntil") or "").replace("Z", "+00:00")
        )
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if valid_until <= current:
            reasons.append("promotion_artifact_stale")
    except (TypeError, ValueError):
        reasons.append("promotion_valid_until_invalid")

    provenance = raw.get("provenance")
    if not isinstance(provenance, dict):
        reasons.append("promotion_provenance_missing")
        provenance = {}
    if not provenance.get("datasetId"):
        reasons.append("promotion_dataset_identity_missing")
    provider = str(provenance.get("provider") or "")
    expected_provider = "bybit_rest" if route.family == "crypto" else "mt5"
    if provider != expected_provider:
        reasons.append("promotion_provider_invalid")
    sha256 = str(provenance.get("sha256") or "").lower()
    if not _SHA256.fullmatch(sha256):
        reasons.append("promotion_provenance_hash_invalid")
    implementation_sha256 = str(provenance.get("implementationSha256") or "").lower()
    if not _SHA256.fullmatch(implementation_sha256):
        reasons.append("promotion_implementation_hash_invalid")
    elif implementation_sha256 != scorer_sha256():
        reasons.append("promotion_implementation_hash_mismatch")
    if provenance.get("confirmedOnly") is not True:
        reasons.append("promotion_confirmed_only_not_proven")
    if provenance.get("untouchedHoldoutPct") != 20:
        reasons.append("promotion_holdout_not_20pct")
    if provenance.get("walkForwardFolds") != 4:
        reasons.append("promotion_walk_forward_not_four_folds")
    purge_bars = _number(provenance, "purgeBars")
    max_hold_bars = _number(provenance, "maxHoldBars")
    if purge_bars is None or purge_bars < 1:
        reasons.append("promotion_purge_bars_invalid")
    if max_hold_bars is None or max_hold_bars < 1:
        reasons.append("promotion_max_hold_bars_invalid")
    if purge_bars is not None and max_hold_bars is not None and purge_bars < max_hold_bars + 1:
        reasons.append("promotion_purge_embargo_insufficient")
    costs = provenance.get("costs")
    required_costs = {
        "spreadBps",
        "commissionBps",
        "slippageBps",
        "swapBpsPerDay",
        "adverseSameBarOrdering",
    }
    if not isinstance(costs, dict) or not required_costs.issubset(costs):
        reasons.append("promotion_cost_model_incomplete")
    elif costs.get("adverseSameBarOrdering") is not True:
        reasons.append("promotion_same_bar_ordering_not_adverse")
    else:
        try:
            cost_values = [
                float(costs[key])
                for key in (
                    "spreadBps",
                    "commissionBps",
                    "slippageBps",
                    "swapBpsPerDay",
                )
            ]
        except (TypeError, ValueError):
            cost_values = []
        if len(cost_values) != 4 or any(value < 0 for value in cost_values):
            reasons.append("promotion_cost_model_invalid")

    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        reasons.append("promotion_metrics_missing")
        metrics = {}
    trade_floor = 40 if pair_adapter else 60
    oos_trades = _number(metrics, "oosTrades")
    if oos_trades is None or oos_trades < trade_floor:
        reasons.append("promotion_oos_trade_floor_failed")
    fold_values = metrics.get("foldExpectancyR")
    try:
        parsed_folds = [float(value) for value in fold_values]
    except (TypeError, ValueError):
        parsed_folds = []
    if len(parsed_folds) != 4 or sum(1 for value in parsed_folds if value > 0) < 3:
        reasons.append("promotion_positive_fold_gate_failed")
    holdout = _number(metrics, "holdoutExpectancyR")
    if holdout is None or holdout <= 0:
        reasons.append("promotion_holdout_expectancy_failed")
    holdout_trades = _number(metrics, "holdoutTrades")
    if holdout_trades is None or holdout_trades < 15:
        reasons.append("promotion_holdout_trade_floor_failed")
    expectancy = _number(metrics, "expectancyR")
    if expectancy is None or expectancy < 0.08:
        reasons.append("promotion_expectancy_failed")
    profit_factor = _number(metrics, "profitFactor")
    if profit_factor is None or profit_factor < 1.20:
        reasons.append("promotion_profit_factor_failed")
    sqn = _number(metrics, "sqn")
    if sqn is None or sqn < 1.5:
        reasons.append("promotion_sqn_failed")
    max_dd = _number(metrics, "maxDrawdownR")
    if max_dd is None or max_dd > 12:
        reasons.append("promotion_drawdown_failed")
    lower = _number(metrics, "bootstrapLower95ExpectancyR")
    if lower is None or lower <= 0:
        reasons.append("promotion_bootstrap_lower_bound_failed")

    profile: EngineAV3Profile | None = None
    raw_profile = raw.get("profile")
    if not isinstance(raw_profile, dict):
        reasons.append("promotion_profile_missing")
    else:
        try:
            profile = EngineAV3Profile.create(
                score_group=str(raw_profile.get("scoreGroup") or ""),
                horizon=str(raw_profile.get("horizon") or ""),
                indicator_periods=raw_profile.get("indicatorPeriods") or {},
                weights=raw_profile.get("weights") or {},
                direction_deadband=raw_profile.get("directionDeadband"),
                trade_threshold=raw_profile.get("tradeThreshold"),
                exit_policy=str(raw_profile.get("exitPolicy") or ""),
                status=str(raw_profile.get("status") or ""),
                profile_id=str(raw_profile.get("profileId") or ""),
                created_at=raw_profile.get("createdAt"),
                valid_until=raw_profile.get("validUntil"),
                expected_scorer_sha256=str(raw_profile.get("scorerSha256") or ""),
            )
            if profile.score_group != route.score_group or profile.horizon != horizon:
                reasons.append("promotion_profile_route_mismatch")
            if profile.status != "PROMOTED":
                reasons.append("promotion_profile_status_invalid")
            if profile.scorer_sha256 != scorer_sha256():
                reasons.append("promotion_scorer_hash_mismatch")
            if profile.profile_sha256 != str(raw_profile.get("profileSha256") or ""):
                reasons.append("promotion_profile_hash_mismatch")
            if profile.created_at != raw.get("createdAt") or profile.valid_until != raw.get("validUntil"):
                reasons.append("promotion_profile_validity_mismatch")
        except (TypeError, ValueError):
            reasons.append("promotion_profile_invalid")

    if reasons:
        return PromotionDecision(False, tuple(dict.fromkeys(reasons)), None)
    artifact = ValidationArtifact(
        artifactId=str(raw["artifactId"]),
        family=route.family,
        subclass=route.subclass,
        horizon=horizon,
        status="PROMOTED",
        metrics=tuple((key, _freeze(value)) for key, value in sorted(metrics.items())),
        provenanceSha256=sha256,
        profileSha256=profile.profile_sha256 if profile else None,
    )
    return PromotionDecision(True, (), artifact, profile)


def promote_candidate(
    candidate_path: str | os.PathLike[str],
    *,
    registry: PromotionRegistry,
    pair: dict[str, Any],
    expected_artifact_id: str,
    now: datetime | None = None,
) -> Path:
    path = Path(candidate_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("promotion_candidate_malformed") from exc
    if str(raw.get("artifactId") or "") != str(expected_artifact_id or ""):
        raise ValueError("promotion_confirmation_mismatch")

    route = route_specialist(pair)
    horizon = str(raw.get("horizon") or "")
    pair_adapter = bool(raw.get("pairAdapter"))
    decision = validate_promotion_artifact(
        raw,
        route,
        horizon,
        pair_adapter=pair_adapter,
        now=now,
    )
    if not decision.qualified:
        raise ValueError(",".join(decision.reasons))

    symbol = str(pair.get("symbol") or "")
    destination = registry._paths(route, horizon, symbol)[0 if pair_adapter else -1][0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(raw, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination
