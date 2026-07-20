from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .constants import PLATFORM_VERSION, SIMULATOR_VERSION
from .hashing import content_hash
from .models import ConfigSnapshot, CostModel, utc_now_iso


_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_LEGACY_BACKTEST_PREFIXES = ("BT_", "BACKTEST_")


def _safe_config(config: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for raw_key, value in config.items():
        key = str(raw_key)
        upper = key.upper()
        if upper.startswith(_LEGACY_BACKTEST_PREFIXES) or "BACKTEST" in upper:
            continue
        if any(marker in upper for marker in _SECRET_MARKERS):
            continue
        try:
            content_hash(value)
        except Exception:
            continue
        safe[key] = value
    return safe


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "UNAVAILABLE"


def build_config_snapshot(
    *,
    config: Mapping[str, Any],
    policies: Mapping[str, Any],
    cost_model: CostModel,
    random_seed: int,
    repo_root: Path,
) -> ConfigSnapshot:
    from engine_a_v3.contract import CONTRACT_VERSION as ENGINE_A_CONTRACT_VERSION
    from engine_b_snapshot import ENGINE_B_SNAPSHOT_CONTRACT_VERSION
    from timeframe_policy import POLICY_VERSION

    safe_config = _safe_config(config)
    policy_hash = content_hash({"version": POLICY_VERSION, "policies": policies})
    payload = {
        "platformVersion": PLATFORM_VERSION,
        "gitCommit": _git_commit(repo_root),
        "engineAContractVersion": ENGINE_A_CONTRACT_VERSION,
        "engineBContractVersion": ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
        "timeframePolicyVersion": POLICY_VERSION,
        "timeframePolicyHash": policy_hash,
        "config": safe_config,
        "costModel": asdict(cost_model),
        "simulatorVersion": SIMULATOR_VERSION,
        "randomSeed": int(random_seed),
    }
    return ConfigSnapshot(
        snapshot_id=content_hash(payload),
        created_at=utc_now_iso(),
        git_commit=payload["gitCommit"],
        engine_a_contract_version=ENGINE_A_CONTRACT_VERSION,
        engine_b_contract_version=ENGINE_B_SNAPSHOT_CONTRACT_VERSION,
        timeframe_policy_version=POLICY_VERSION,
        timeframe_policy_hash=policy_hash,
        config_hash=content_hash(safe_config),
        config=safe_config,
        simulator_version=SIMULATOR_VERSION,
        cost_model=asdict(cost_model),
        random_seed=int(random_seed),
    )


def apply_worker_config_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Pin production configuration inside an isolated spawned worker."""

    from config import CONFIG

    frozen = snapshot.get("config") if isinstance(snapshot.get("config"), Mapping) else {}
    CONFIG.clear()
    CONFIG.update(dict(frozen))

