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
from engine_a_v3.validation import validate_specialist_cohort


DEFAULT_MANIFEST = Path("configs/engine_a_v3_validation.yaml")


def default_candidate_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "Athena" / "research" / "engine_a_v3" / "candidates"


def load_manifest(path: str | os.PathLike[str] = DEFAULT_MANIFEST) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), dict):
        raise ValueError("validation_manifest_invalid")
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


def _dataset_id(provenance: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(provenance, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"engine-a-v3-{digest[:20]}"


def validate_manifest_group(
    group_name: str,
    horizon: str,
    *,
    manifest_path: str | os.PathLike[str] = DEFAULT_MANIFEST,
    candidate_root: str | os.PathLike[str] | None = None,
    force_refresh: bool = False,
) -> Path:
    manifest = load_manifest(manifest_path)
    group = manifest["groups"].get(group_name)
    if not isinstance(group, dict):
        raise ValueError("validation_group_unknown")
    if horizon not in {"intraday", "swing"}:
        raise ValueError("validation_horizon_invalid")

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
        for timeframe in ("H1", "H4", "D1"):
            frame, provenance = load_ohlcv(
                data_symbol,
                timeframe,
                limit=int(limits.get(timeframe, 1000)),
                force_refresh=force_refresh,
                allow_yfinance=False,
            )
            if frame is None or provenance.data_source == "DATA_UNAVAILABLE":
                raise ValueError(
                    f"validation_data_unavailable:{data_symbol}:{timeframe}"
                )
            candles[timeframe] = _frame_to_confirmed_candles(frame)
            pair_provenance[timeframe] = provenance.to_dict()
        symbol = str(pair.get("symbol") or "")
        datasets.append({"pair": pair, "candles": candles})
        provenance_manifest[symbol] = pair_provenance

    costs = group.get("costs") or {}
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
    )
    artifact["provenance"]["sourceManifest"] = provenance_manifest
    root = Path(candidate_root) if candidate_root else default_candidate_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{group_name}-{horizon}-{artifact['artifactId']}.json"
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
