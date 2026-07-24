from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from athena_research.data_loader import load_ohlcv
from engine_a_v3.promotion import PromotionRegistry, promote_candidate
from engine_a_v3.routing import route_specialist
from engine_a_v3.routing import KNOWN_SCORE_GROUPS
from engine_a_v3.validation import validate_specialist_cohort


DEFAULT_MANIFEST = Path("configs/engine_a_v3_validation.yaml")


def default_candidate_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "Athena" / "research" / "engine_a_v3" / "candidates"


def load_manifest(path: str | os.PathLike[str] = DEFAULT_MANIFEST) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), dict):
        raise ValueError("validation_manifest_invalid")
    if set(raw["groups"]) != set(KNOWN_SCORE_GROUPS):
        raise ValueError("validation_manifest_group_coverage_incomplete")
    for name, group in raw["groups"].items():
        if not isinstance(group, dict) or group.get("horizons") != ["intraday", "swing"]:
            raise ValueError(f"validation_manifest_horizons_invalid:{name}")
        enabled = group.get("validationEnabled") is True
        provider = str(group.get("requiredProvider") or "")
        if enabled and (provider not in {"bybit", "mt5"} or group.get("costsVerified") is not True):
            raise ValueError(f"validation_manifest_provider_or_costs_unverified:{name}")
        if not enabled and provider != "unverified":
            raise ValueError(f"validation_manifest_disabled_provider_invalid:{name}")
    return raw


def _frame_to_confirmed_candles(frame) -> list[dict[str, Any]]:
    if frame is None or len(frame) < 2:
        raise ValueError("validation_data_insufficient")
    confirmed = frame.iloc[:-1]
    rows: list[dict[str, Any]] = []
    for timestamp, row in confirmed.iterrows():
        rows.append(
            {
                "time": timestamp.isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "vol": float(row.get("volume", 0.0)),
            }
        )
    return rows


def _policy_validation_timeframes(pair: dict[str, Any], horizon: str) -> list[str]:
    """Core D1/H4/H1 stack plus any lower policy rungs Engine A scores with.

    The lane historically loaded D1/H4/H1 only, so every promoted profile was
    validated on a timeframe stack the live scorer no longer uses in full — the
    setup rung (and the trigger rung behind ``triggerEvidence``) can resolve to
    M30/M15/M5. Lower rungs are additive: they are loaded when the required
    provider has them and recorded in provenance, and their absence does not
    fail a lane, because trend and momentum remain on the D1/H4/H1 core.
    """
    timeframes = ["H1", "H4", "D1"]
    try:
        from engine_a_v3.routing import route_specialist
        from timeframe_policy import resolve_timeframe_policy

        policy = resolve_timeframe_policy(
            str(pair.get("display") or pair.get("symbol") or ""),
            str(pair.get("type") or pair.get("asset_type") or ""),
            route_specialist(pair).score_group,
            horizon,
            engine_id="engine_a",
        )
    except Exception:
        return timeframes
    for tf in (policy.setup_tf.value, policy.trigger_tf.value):
        if tf and tf not in timeframes:
            timeframes.append(tf)
    return timeframes


def _dataset_id(provenance: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(provenance, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"engine-a-v3-{digest[:20]}"


def _record_validation_attempt(root: Path, group_name: str, horizon: str) -> int:
    """Increment and return the 1-based attempt count for this validation lane.

    Keyed by (group, horizon, scorer sha) so a scorer change resets the count.
    Best-effort per-process registry: concurrent runs may race, but the count
    is diagnostic provenance, not a gate.
    """
    from engine_a_v3.profile import scorer_sha256

    registry_path = root / "validation_attempts.json"
    try:
        attempts = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(attempts, dict):
            attempts = {}
    except (OSError, json.JSONDecodeError):
        attempts = {}
    key = f"{group_name}|{horizon}|{scorer_sha256()}"
    attempt_index = int(attempts.get(key) or 0) + 1
    attempts[key] = attempt_index
    temporary = registry_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(attempts, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(registry_path)
    return attempt_index


def validate_manifest_group(
    group_name: str,
    horizon: str,
    *,
    manifest_path: str | os.PathLike[str] = DEFAULT_MANIFEST,
    candidate_root: str | os.PathLike[str] | None = None,
    force_refresh: bool = False,
    data_as_of: str | None = None,
) -> Path:
    """Validate one manifest lane and write a candidate artifact.

    ``data_as_of`` opts into frozen-data validation: every series is read from
    the sha256-verified frozen store for that date (frozen_data.py) and the run
    fails closed if any series is missing or tampered. Default (None) keeps the
    live-provider path.
    """
    manifest = load_manifest(manifest_path)
    group = manifest["groups"].get(group_name)
    if not isinstance(group, dict):
        raise ValueError("validation_group_unknown")
    if horizon not in {"intraday", "swing"}:
        raise ValueError("validation_horizon_invalid")
    if group.get("validationEnabled") is not True:
        raise ValueError(f"validation_lane_unvalidated:{group_name}:{horizon}")

    limits = manifest.get("limits") or {}
    datasets = []
    provenance_manifest: dict[str, Any] = {}
    pairs = group.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("validation_group_pairs_missing")
    for pair_config in pairs:
        pair = dict(pair_config)
        data_symbol = str(pair.pop("dataSymbol", pair.get("display") or ""))
        candles: dict[str, list[dict[str, Any]]] = {}
        pair_provenance: dict[str, Any] = {}
        expected_provider = "bybit_rest" if group["requiredProvider"] == "bybit" else "mt5"
        _lane_timeframes = _policy_validation_timeframes(pair, horizon)
        _core_timeframes = {"H1", "H4", "D1"}
        for timeframe in _lane_timeframes:
            _optional = timeframe not in _core_timeframes
            if data_as_of:
                from frozen_data import read_frozen_candles

                try:
                    rows = read_frozen_candles(
                        data_as_of,
                        {"display": data_symbol},
                        timeframe,
                        expected_provider,
                        limit=int(limits.get(timeframe, 1000)),
                        min_bars=2,
                    )
                except Exception:
                    if not _optional:
                        raise
                    pair_provenance[timeframe] = {"data_source": "UNAVAILABLE"}
                    continue
                # Frozen rows are cutoff-filtered but the final bar may have
                # been forming at freeze time; drop it for confirmed-only
                # parity with the live path.
                candles[timeframe] = rows[:-1]
                if len(candles[timeframe]) < 1:
                    if _optional:
                        candles.pop(timeframe, None)
                        pair_provenance[timeframe] = {"data_source": "UNAVAILABLE"}
                        continue
                    raise ValueError(
                        f"validation_data_insufficient:{data_symbol}:{timeframe}"
                    )
                pair_provenance[timeframe] = {
                    "data_source": expected_provider,
                    "frozen_data_as_of": data_as_of,
                }
                continue
            frame, provenance = load_ohlcv(
                data_symbol,
                timeframe,
                limit=int(limits.get(timeframe, 1000)),
                force_refresh=force_refresh,
                allow_yfinance=False,
                required_source=("bybit_rest" if group["requiredProvider"] == "bybit" else "mt5"),
            )
            expected_source = "bybit_rest" if group["requiredProvider"] == "bybit" else "mt5"
            _unavailable = frame is None or provenance.data_source == "DATA_UNAVAILABLE"
            _mismatched = not _unavailable and provenance.data_source != expected_source
            if _unavailable or _mismatched:
                # Lower policy rungs are additive context: record that they were
                # unavailable rather than failing a lane whose scored core
                # (D1/H4/H1) is fully present.
                if _optional:
                    pair_provenance[timeframe] = {
                        "data_source": provenance.data_source if not _unavailable else "UNAVAILABLE",
                        "policyRungSkipped": True,
                    }
                    continue
                if _unavailable:
                    raise ValueError(
                        f"validation_data_unavailable:{data_symbol}:{timeframe}"
                    )
                raise ValueError(
                    f"validation_provider_mismatch:{data_symbol}:{timeframe}:"
                    f"expected={expected_source}:actual={provenance.data_source}"
                )
            candles[timeframe] = _frame_to_confirmed_candles(frame)
            pair_provenance[timeframe] = provenance.to_dict()
        symbol = str(pair.get("symbol") or "")
        datasets.append({"pair": pair, "candles": candles})
        provenance_manifest[symbol] = pair_provenance

    costs = group.get("costs") or {}
    root = Path(candidate_root) if candidate_root else default_candidate_root()
    root.mkdir(parents=True, exist_ok=True)
    attempt_index = _record_validation_attempt(root, group_name, horizon)
    artifact = validate_specialist_cohort(
        datasets,
        horizon=horizon,
        dataset_id=_dataset_id(provenance_manifest),
        expected_symbols=[str(pair.get("symbol") or "") for pair in pairs],
        spread_bps=float(costs["spreadBps"]),
        commission_bps=float(costs["commissionBps"]),
        slippage_bps=float(costs["slippageBps"]),
        swap_bps_per_day=float(costs["swapBpsPerDay"]),
        purge_bars=int(group.get("purgeBars", 5)),
        max_hold_bars=int(group.get("maxHoldBars", {}).get(horizon, 24)),
        provider=("bybit_rest" if group["requiredProvider"] == "bybit" else "mt5"),
    )
    artifact["provenance"]["sourceManifest"] = provenance_manifest
    # Trial accounting: how many validation runs this lane has seen with the
    # current scorer. A holdout pass after many attempts is weaker evidence
    # than a first-try pass; reviewers can now see the difference.
    artifact["provenance"]["attemptIndex"] = attempt_index
    artifact["provenance"]["dataFrozen"] = bool(data_as_of)
    if data_as_of:
        artifact["provenance"]["dataAsOf"] = str(data_as_of)
    path = root / f"{group_name}-{horizon}-{artifact['artifactId']}.json"
    # Reproducibility: persist the exact candles the artifact was computed on,
    # so the run can be replayed byte-for-byte against the recorded hashes.
    dataset_path = path.with_name(path.stem + ".dataset.json")
    dataset_snapshot = {
        str(item["pair"].get("symbol") or ""): item["candles"] for item in datasets
    }
    dataset_tmp = dataset_path.with_suffix(".tmp")
    dataset_tmp.write_text(
        json.dumps(dataset_snapshot, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    dataset_tmp.replace(dataset_path)
    artifact["provenance"]["datasetSnapshotPath"] = dataset_path.name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(artifact, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def promote_manifest_candidate(
    candidate_path: str | os.PathLike[str],
    *,
    expected_artifact_id: str,
    manifest_path: str | os.PathLike[str] = DEFAULT_MANIFEST,
    registry_root: str | os.PathLike[str] | None = None,
) -> Path:
    raw = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    manifest = load_manifest(manifest_path)
    for group in manifest["groups"].values():
        pairs = group.get("pairs") if isinstance(group, dict) else None
        if not isinstance(pairs, list) or not pairs:
            continue
        pair = dict(pairs[0])
        pair.pop("dataSymbol", None)
        route = route_specialist(pair)
        if raw.get("family") == route.family and raw.get("subclass") == route.subclass:
            return promote_candidate(
                candidate_path,
                registry=PromotionRegistry(registry_root),
                pair=pair,
                expected_artifact_id=expected_artifact_id,
            )
    raise ValueError("promotion_manifest_route_missing")
