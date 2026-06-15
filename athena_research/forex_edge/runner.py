from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from athena_research.forex_edge.models import BlockedDataError
from athena_research.forex_edge.reporting import write_run_artifacts
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
            "details": {
                "message": "Empirical dataset loading is not implemented in this bounded runner yet."
            },
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
