from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from typing import Literal

import numpy as np
import pandas as pd

from athena_research.forex_edge.config import validate_empirical_config
from athena_research.forex_edge.fixing.backtest import run_fixing_backtest
from athena_research.forex_edge.models import BlockedDataError
from athena_research.forex_edge.portfolio.backtest import run_monthly_portfolio
from athena_research.forex_edge.portfolio.construction import build_currency_weights
from athena_research.forex_edge.portfolio.signals import (
    blend_rank_scores,
    carry_proxy_scores,
    centered_rank_scores,
    momentum_12_1_scores,
    reer_value_5y_scores,
)
from athena_research.forex_edge.reporting import write_run_artifacts
from athena_research.forex_edge.universe import CANONICAL_USD_PAIRS
from athena_research.forex_edge.validation import (
    block_bootstrap_lower_bound,
    classify_result,
    cscv_pbo,
    chronological_split,
    deflated_sharpe,
    max_positive_contribution_share,
)
from athena_research.reproducibility import hash_stable_json


PORTFOLIO_CONFIGS = (
    "carry_proxy",
    "momentum_12_1",
    "reer_value_5y",
    "equal_weight_three_factor_blend",
)
FIXING_CONFIGS = (
    ("london", "pre_continuation"),
    ("london", "post_reversal"),
    ("tokyo", "pre_continuation"),
    ("tokyo", "post_reversal"),
)
FIXING_PAIRS = ("EURUSD", "GBPUSD", "USDJPY")
COST_MULTIPLIERS = (1.0, 1.5, 2.0)


@dataclass(frozen=True)
class RunRequest:
    lane: Literal["portfolio", "fixing", "both"]
    dataset_manifests: dict[str, str]
    output_root: Path

    def __post_init__(self) -> None:
        if self.lane == "portfolio":
            required = {"fred_spot", "fred_rates", "bis_reer"}
        elif self.lane == "fixing":
            required = {"dukascopy_m5"}
        elif self.lane == "both":
            required = {"fred_spot", "fred_rates", "bis_reer", "dukascopy_m5"}
        else:
            raise ValueError(f"unknown lane: {self.lane}")
        if not required.issubset(self.dataset_manifests):
            raise BlockedDataError("PINNED_MANIFEST_REQUIRED")


def build_trial_registry() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for config in PORTFOLIO_CONFIGS:
        for multiplier in COST_MULTIPLIERS:
            rows.append(
                {
                    "trial_id": f"portfolio:{config}:{multiplier:.1f}",
                    "lane": "portfolio",
                    "configuration": config,
                    "cost_multiplier": multiplier,
                }
            )
    for location, mode in FIXING_CONFIGS:
        for pair in FIXING_PAIRS:
            for multiplier in COST_MULTIPLIERS:
                rows.append(
                    {
                        "trial_id": (
                            f"fixing:{location}:{mode}:{pair}:{multiplier:.1f}"
                        ),
                        "lane": "fixing",
                        "location": location,
                        "mode": mode,
                        "pair": pair,
                        "cost_multiplier": multiplier,
                    }
                )
    return rows


def _manifest_paths(value: str) -> list[Path]:
    return [Path(item) for item in str(value).split(",") if item.strip()]


def _load_manifest_frame(manifest_path: Path) -> pd.DataFrame:
    if not manifest_path.exists():
        raise BlockedDataError("PINNED_MANIFEST_REQUIRED")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = str(payload["dataset"])
    key = str(payload["key"])
    version = str(payload["version"])
    root = manifest_path.parents[3]
    base = root / "normalized" / dataset / key / version
    parts = sorted(base.glob("*/data.parquet"))
    if not parts:
        raise BlockedDataError("PINNED_MANIFEST_REQUIRED")
    return pd.concat([pd.read_parquet(path) for path in parts], ignore_index=True)


def _load_manifest_group(
    manifests: Mapping[str, str],
    name: str,
) -> pd.DataFrame:
    frames = [_load_manifest_frame(path) for path in _manifest_paths(manifests[name])]
    if not frames:
        raise BlockedDataError("PINNED_MANIFEST_REQUIRED")
    return pd.concat(frames, ignore_index=True)


def _pair_returns_from_spot(spot: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = spot.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    work["value"] = pd.to_numeric(work["value"], errors="coerce")
    currency_monthly_rows = []
    pair_rows = []
    for currency, group in work.sort_values("timestamp").groupby("currency"):
        currency = str(currency)
        clean = group.dropna(subset=["value"]).copy()
        clean["return"] = clean["value"].pct_change()
        monthly = (
            clean.set_index("timestamp")
            .resample("ME")
            .agg({"value": "last", "available_time": "max"})
            .dropna(subset=["value"])
            .reset_index()
        )
        monthly["currency"] = currency
        monthly["return"] = monthly["value"].pct_change()
        currency_monthly_rows.append(
            monthly[["timestamp", "available_time", "currency", "return"]]
        )
        if currency not in CANONICAL_USD_PAIRS:
            continue
        spec = CANONICAL_USD_PAIRS[currency]
        pair_price = clean["value"] if spec.usd_per_currency else 1.0 / clean["value"]
        pair_rows.append(
            pd.DataFrame(
                {
                    "timestamp": clean["timestamp"],
                    "symbol": spec.pair,
                    "return": pair_price.pct_change(),
                }
            )
        )
    return (
        pd.concat(pair_rows, ignore_index=True).dropna(subset=["return"]),
        pd.concat(currency_monthly_rows, ignore_index=True).dropna(subset=["return"]),
    )


def _portfolio_decisions(
    config_name: str,
    spot: pd.DataFrame,
    rates: pd.DataFrame,
    reer: pd.DataFrame,
    currency_returns: pd.DataFrame,
    config: Mapping[str, Any],
) -> dict[pd.Timestamp, pd.Series]:
    portfolio = config["portfolio"]
    timestamps = pd.DatetimeIndex(pd.to_datetime(spot["timestamp"], utc=True)).sort_values()
    month_labels = pd.Series(timestamps).dt.strftime("%Y-%m").drop_duplicates()
    month_ends = pd.DatetimeIndex(
        [pd.Timestamp(label, tz="UTC") + pd.offsets.MonthEnd(0) for label in month_labels]
    )
    decisions: dict[pd.Timestamp, pd.Series] = {}
    for decision in month_ends:
        parts: dict[str, pd.Series] = {}
        if config_name in {"carry_proxy", "equal_weight_three_factor_blend"}:
            parts["carry"] = centered_rank_scores(carry_proxy_scores(rates, decision))
        if config_name in {"momentum_12_1", "equal_weight_three_factor_blend"}:
            parts["momentum"] = centered_rank_scores(
                momentum_12_1_scores(currency_returns, decision)
            )
        if config_name in {"reer_value_5y", "equal_weight_three_factor_blend"}:
            parts["value"] = centered_rank_scores(reer_value_5y_scores(reer, decision))
        scores = blend_rank_scores(parts) if len(parts) > 1 else next(iter(parts.values()), pd.Series(dtype=float))
        if scores.empty:
            continue
        try:
            decisions[decision] = build_currency_weights(
                scores,
                top_n=int(portfolio["top_n"]),
                min_currencies=int(portfolio["min_currencies"]),
            )
        except ValueError:
            continue
    return decisions


def _pair_costs(config: Mapping[str, Any]) -> dict[str, float]:
    portfolio = config["portfolio"]
    raw = dict(portfolio["round_trip_bps"])
    major = float(raw["major"])
    other = float(raw["other"])
    majors = {str(item) for item in portfolio["major_pairs"]}
    return {
        spec.pair: (major if spec.pair in majors else other)
        for spec in CANONICAL_USD_PAIRS.values()
    }


def _year_count(returns: pd.DataFrame) -> int:
    if returns.empty:
        return 0
    work = returns.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    yearly = work.groupby(work["timestamp"].dt.year)["net_return"].sum()
    return int((yearly > 0).sum())


def _summarize_returns(
    returns: pd.DataFrame,
    *,
    n_trials: int,
    config: Mapping[str, Any],
    group_column: str | None = None,
) -> dict[str, Any]:
    validation = config["validation"]
    if returns.empty or "net_return" not in returns:
        scored = pd.DataFrame(columns=["timestamp", "net_return"])
    else:
        holdout_start = pd.Timestamp(config["portfolio"]["holdout_start"], tz="UTC")
        scored = returns.copy()
        scored["timestamp"] = pd.to_datetime(scored["timestamp"], utc=True)
        scored = scored[scored["timestamp"] >= holdout_start].copy()
    values = scored["net_return"] if "net_return" in scored else pd.Series(dtype=float)
    boot = block_bootstrap_lower_bound(
        values,
        block_size=int(validation["bootstrap_block"]),
        resamples=int(validation["bootstrap_resamples"]),
        seed=int(validation["bootstrap_seed"]),
    )
    dsr = deflated_sharpe(values, n_trials=n_trials)
    groups = (
        scored[group_column]
        if group_column and group_column in scored
        else pd.Series(["all"] * len(scored))
    )
    share = max_positive_contribution_share(values, groups) if len(values) else None
    return {
        "holdout_net_return": float(values.sum()) if len(values) else 0.0,
        "bootstrap_lb": boot["lower_bound"],
        "cost_2x_net_return": float(values.sum()) if len(values) else 0.0,
        "dsr_z": dsr["dsr_z"],
        "pbo": None,
        "max_positive_contribution_share": share,
        "positive_holdout_years": _year_count(scored),
        "scored_start": config["portfolio"]["holdout_start"],
        "n_observations": int(len(values)),
    }


def run_research(request: RunRequest, config: dict[str, object]) -> list[Path]:
    registry = build_trial_registry()
    config_hash = hash_stable_json(config)
    manifest_hash = hash_stable_json(request.dataset_manifests)
    run_id = hash_stable_json(
        {
            "lane": request.lane,
            "config_hash": config_hash,
            "dataset_manifests": request.dataset_manifests,
        }
    )[:16]
    try:
        validate_empirical_config(config, request.lane)
    except BlockedDataError:
        payload = {
            "run_id": f"forex-edge-{run_id}",
            "manifest": {
                "lane": request.lane,
                "config_hash": config_hash,
                "dataset_manifests": request.dataset_manifests,
                "dataset_manifest_hash": manifest_hash,
                "trial_count": len(registry),
                "production_eligible": False,
            },
            "eligibility": {
                "eligible": False,
                "reason_codes": ["UNREGISTERED_QUALITY_LIMIT"],
                "details": {"message": "Empirical quality limits are not pre-registered."},
            },
            "quality": {"passed": False, "issues": ["UNREGISTERED_QUALITY_LIMIT"]},
            "trials": registry,
            "metrics": {
                "study_status": "BLOCKED_DATA",
                "production_eligible": False,
                "evidence_flags": [],
            },
            "returns": pd.DataFrame(columns=["timestamp", "net_return"]),
        }
        return list(write_run_artifacts(request.output_root, payload))

    try:
        returns_parts: list[pd.DataFrame] = []
        if request.lane in {"portfolio", "both"}:
            spot = _load_manifest_group(request.dataset_manifests, "fred_spot")
            rates = _load_manifest_group(request.dataset_manifests, "fred_rates")
            reer = _load_manifest_group(request.dataset_manifests, "bis_reer")
            pair_returns, currency_returns = _pair_returns_from_spot(spot)
            for trial in registry:
                if trial["lane"] != "portfolio":
                    continue
                decisions = _portfolio_decisions(
                    str(trial["configuration"]),
                    spot,
                    rates,
                    reer,
                    currency_returns,
                    config,
                )
                if not decisions:
                    continue
                backtest = run_monthly_portfolio(
                    pair_returns,
                    decisions,
                    roundtrip_cost_bps=_pair_costs(config),
                    cost_multiplier=float(trial["cost_multiplier"]),
                )
                frame = backtest.daily_returns.copy()
                frame["trial_id"] = str(trial["trial_id"])
                frame["configuration"] = str(trial["configuration"])
                returns_parts.append(frame)
        if request.lane in {"fixing", "both"}:
            bars = _load_manifest_group(request.dataset_manifests, "dukascopy_m5")
            fixing = config["fixing"]
            bar_times = pd.to_datetime(bars["timestamp"], utc=True)
            start = max(
                pd.Timestamp(fixing["target_start"], tz="UTC"),
                bar_times.min().normalize(),
            )
            end = bar_times.max()
            event_dates = pd.date_range(start.normalize(), end.normalize(), freq="D", tz="UTC")
            for trial in registry:
                if trial["lane"] != "fixing":
                    continue
                anchor = fixing["anchors"][trial["location"]]
                events = run_fixing_backtest(
                    bars,
                    symbol=str(trial["pair"]),
                    event_dates=event_dates,
                    timezone_name=str(anchor["timezone"]),
                    local_time=f"{int(anchor['hour']):02d}:{int(anchor['minute']):02d}",
                    mode=str(trial["mode"]),
                    roundtrip_commission_bps=float(fixing["commission_round_trip_bps"]),
                    cost_multiplier=float(trial["cost_multiplier"]),
                )
                if events.empty:
                    continue
                frame = events.rename(columns={"fixing_time": "timestamp"}).copy()
                frame["trial_id"] = str(trial["trial_id"])
                frame["configuration"] = f"{trial['location']}:{trial['mode']}:{trial['pair']}"
                returns_parts.append(frame)
        all_returns = (
            pd.concat(returns_parts, ignore_index=True)
            if returns_parts
            else pd.DataFrame(columns=["timestamp", "net_return", "trial_id"])
        )
    except (FileNotFoundError, KeyError, ValueError, BlockedDataError) as exc:
        payload = {
            "run_id": f"forex-edge-{run_id}",
            "manifest": {
                "lane": request.lane,
                "config_hash": config_hash,
                "dataset_manifests": request.dataset_manifests,
                "dataset_manifest_hash": manifest_hash,
                "trial_count": len(registry),
                "production_eligible": False,
            },
            "eligibility": {
                "eligible": False,
                "reason_codes": ["BLOCKED_DATA"],
                "details": {"message": str(exc)},
            },
            "quality": {"passed": False, "issues": ["BLOCKED_DATA"]},
            "trials": registry,
            "metrics": {
                "study_status": "BLOCKED_DATA",
                "production_eligible": False,
                "evidence_flags": [],
            },
            "returns": pd.DataFrame(columns=["timestamp", "net_return"]),
        }
        return list(write_run_artifacts(request.output_root, payload))

    metrics = _summarize_returns(
        all_returns,
        n_trials=len(registry),
        config=config,
        group_column="configuration",
    )
    trial_matrix = (
        all_returns.pivot_table(
            index="timestamp",
            columns="trial_id",
            values="net_return",
            aggfunc="sum",
        )
        .fillna(0.0)
        .to_numpy(dtype=float)
        if not all_returns.empty
        else np.empty((0, 0))
    )
    metrics.update(cscv_pbo(trial_matrix, n_partitions=int(config["validation"]["cscv_partitions"])))
    if not all_returns.empty:
        holdout_start = pd.Timestamp(config["portfolio"]["holdout_start"], tz="UTC")
        scored_returns = all_returns.copy()
        scored_returns["timestamp"] = pd.to_datetime(scored_returns["timestamp"], utc=True)
        scored_returns = scored_returns[scored_returns["timestamp"] >= holdout_start]
        metrics["cost_2x_net_return"] = float(
            scored_returns[
                scored_returns.get("trial_id", pd.Series(dtype=str))
                .astype(str)
                .str.endswith(":2.0")
            ]["net_return"].sum()
        )
    else:
        metrics["cost_2x_net_return"] = 0.0
    classification = classify_result(metrics, quality_passed=True, evidence_flags=())
    payload = {
        "run_id": f"forex-edge-{run_id}",
        "manifest": {
            "lane": request.lane,
            "config_hash": config_hash,
            "dataset_manifests": request.dataset_manifests,
            "dataset_manifest_hash": manifest_hash,
            "trial_count": len(registry),
            "production_eligible": False,
        },
        "eligibility": {
            "eligible": classification["study_status"] == "RESEARCH_CANDIDATE",
            "reason_codes": [],
            "details": {"criteria": classification["criteria"]},
        },
        "quality": {"passed": True, "issues": []},
        "trials": registry,
        "metrics": {
            **metrics,
            "study_status": classification["study_status"],
            "production_eligible": False,
            "evidence_flags": classification["evidence_flags"],
        },
        "returns": all_returns,
    }
    return list(write_run_artifacts(request.output_root, payload))
