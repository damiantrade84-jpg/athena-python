from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .catalog import BacktestCatalog
from .constants import PLATFORM_VERSION, TIMEFRAME_SECONDS
from .hashing import canonical_json, content_hash, file_sha256
from .models import DatasetSeriesManifest, RunRequest, utc_now_iso
from .paths import assert_within_runtime, ensure_runtime_layout
from .providers import ProviderDataError, RuntimeProviderAdapter
from .quality import normalize_and_validate


def _calendar_for(asset_type: str, provider: str | None = None) -> tuple[str, str]:
    asset = str(asset_type or "").lower()
    if asset == "crypto":
        return "24/7", "UTC"
    if asset == "forex":
        if str(provider or "").lower() == "mt5":
            return "MT5_FX_UTC_24_5", "UTC"
        return "FX_24/5", "UTC"
    return "EXCHANGE_SESSION", "provider_declared"


def _next_faster_timeframe(timeframe: str) -> str | None:
    ladder = ("D1", "H4", "H1", "M30", "M15", "M5", "M1")
    try:
        return ladder[ladder.index(str(timeframe).upper()) + 1]
    except (ValueError, IndexError):
        return None


def _forming_reconstruction_source(
    engine: str,
    effective_policy: dict[str, Any],
    config: dict[str, Any],
) -> str | None:
    # Production Engine A's authoritative policy evaluation is confirmed-only;
    # its legacy active-candle shadow is not part of the v2 decision contract.
    # Engine B explicitly opts into forming-trigger reconstruction.
    if engine != "B" or not bool(config.get("ENGINE_B_USE_FORMING_FOR_TRIGGER", True)):
        return None
    return _next_faster_timeframe(str(effective_policy.get("trigger_tf") or ""))


def resolve_policy_requirements(pair: dict[str, Any], engine: str, style: str) -> dict[str, Any]:
    from config import CONFIG
    from engine_a_groups import resolve_score_group_by_type
    from timeframe_policy import resolve_timeframe_policy

    symbol = str(pair.get("display") or pair.get("symbol") or "")
    asset_type = str(pair.get("type") or pair.get("asset_type") or "")
    score_group = resolve_score_group_by_type(pair)
    policy = resolve_timeframe_policy(
        symbol,
        asset_type,
        score_group,
        style,
        engine_id="engine_a" if engine == "A" else "engine_b",
        authoritative_group=score_group,
    )
    payload = policy.to_dict()
    if engine == "B":
        from market_structure import resolve_engine_b_tfs

        effective = resolve_engine_b_tfs(
            asset_type,
            style,
            symbol=symbol,
            score_group=score_group,
        )
        effective_policy = {
            **payload,
            "regime_tf": effective["regime"],
            "bias_tf": effective["bias"],
            "structure_tf": effective["struct"],
            "setup_tf": effective["setup"],
            "trigger_tf": effective["trigger"],
            "execution_tf": effective["execution"],
            "atr_tf": effective["atr"],
            "liveConsumerPolicyMode": effective.get("policy_mode", "authoritative"),
            "proposedPolicy": effective.get("proposed"),
        }
    else:
        effective_policy = payload
    timeframes = {
        effective_policy["regime_tf"], effective_policy["bias_tf"], effective_policy["structure_tf"],
        effective_policy["setup_tf"], effective_policy["trigger_tf"], effective_policy["execution_tf"],
        *effective_policy.get("required_closed_tfs", []),
    }
    if effective_policy.get("atr_tf"):
        timeframes.add(effective_policy["atr_tf"])
    reconstruction_tf = _forming_reconstruction_source(engine, effective_policy, CONFIG)
    if reconstruction_tf:
        timeframes.add(reconstruction_tf)
    return {
        "policy": effective_policy,
        "policyPayload": policy.payload(),
        "timeframes": sorted(timeframes, key=lambda tf: -TIMEFRAME_SECONDS[tf]),
    }


class DatasetBuilder:
    def __init__(self, catalog: BacktestCatalog, providers: RuntimeProviderAdapter):
        self.catalog = catalog
        self.providers = providers
        self.layout = ensure_runtime_layout()

    def preflight(self, request: RunRequest) -> dict[str, Any]:
        instruments = []
        exclusions = []
        series_count = 0
        for symbol in request.symbols:
            pair = self.providers.resolve_pair(symbol)
            if pair is None:
                exclusions.append({"symbol": symbol, "code": "UNKNOWN_INSTRUMENT", "message": "symbol is not in the current Engine A/B universe"})
                continue
            policies = {}
            providers = {}
            missing_provider = False
            for engine in request.engines:
                try:
                    requirement = resolve_policy_requirements(pair, engine, request.styles[engine])
                    provider, provider_symbol = self.providers.provider_identity(pair, engine)
                except (ProviderDataError, Exception) as exc:
                    exclusions.append({
                        "symbol": symbol,
                        "engine": engine,
                        "code": getattr(exc, "code", "POLICY_OR_PROVIDER_RESOLUTION_FAILED"),
                        "message": str(exc),
                    })
                    missing_provider = True
                    continue
                policies[engine] = requirement
                providers[engine] = {"provider": provider, "providerSymbol": provider_symbol}
                series_count += len(requirement["timeframes"])
            if not missing_provider or request.mode == "EXPLORATORY":
                instruments.append({
                    "symbol": symbol,
                    "display": pair.get("display") or pair.get("symbol"),
                    "assetType": pair.get("type"),
                    "policies": policies,
                    "providers": providers,
                })
        certification = "CERTIFIED" if not exclusions and request.mode == "CERTIFIED" else (
            "PARTIAL_CERTIFIED" if request.mode == "CERTIFIED" and instruments else "EXPLORATORY"
        )
        return {
            "certification": certification,
            "instruments": instruments,
            "exclusions": exclusions,
            "estimatedSeries": series_count,
            "estimatedTasks": len(request.symbols) * len(request.engines),
            "estimatedBars": sum(
                self.providers.estimate_bar_count(request.start_date, request.end_date, tf, str(item.get("assetType") or ""))
                for item in instruments
                for requirement in item["policies"].values()
                for tf in requirement["timeframes"]
            ),
        }

    def build(self, request: RunRequest) -> dict[str, Any]:
        if request.dataset_id:
            existing = self.catalog.get_dataset(request.dataset_id)
            if existing is None:
                raise ValueError(f"dataset {request.dataset_id!r} not found")
            mismatches = []
            if existing.get("startDate") != request.start_date.isoformat() or existing.get("endDate") != request.end_date.isoformat():
                mismatches.append("date_contract")
            if not set(request.symbols).issubset(set(existing.get("symbols") or [])):
                mismatches.append("universe_contract")
            if not set(request.engines).issubset(set(existing.get("engines") or [])):
                mismatches.append("engine_contract")
            existing_styles = existing.get("styles") or {}
            if any(existing_styles.get(engine) != request.styles[engine] for engine in request.engines):
                mismatches.append("style_contract")
            if mismatches:
                raise ValueError(f"dataset contract incompatible with request: {', '.join(mismatches)}")
            return existing

        created_at = utc_now_iso()
        staging_id = f"staging-{uuid.uuid4().hex}"
        staging_dir = assert_within_runtime(self.layout["datasets"] / staging_id)
        staging_dir.mkdir(parents=True, exist_ok=False)
        series_rows: list[dict[str, Any]] = []
        exclusions: list[dict[str, Any]] = []
        policies: dict[str, Any] = {}
        provider_contracts: dict[str, Any] = {}
        pair_snapshots: dict[str, Any] = {}
        try:
            for symbol in request.symbols:
                pair = self.providers.resolve_pair(symbol)
                if pair is None:
                    exclusions.append({"symbol": symbol, "code": "UNKNOWN_INSTRUMENT", "message": "symbol is not in the current universe"})
                    continue
                pair_key = str(pair.get("display") or pair.get("symbol") or symbol)
                pair_snapshots[symbol] = {
                    key: value
                    for key, value in pair.items()
                    if isinstance(value, (str, int, float, bool, type(None), list, dict))
                    and not any(marker in str(key).upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                }
                for engine in request.engines:
                    try:
                        requirement = resolve_policy_requirements(pair, engine, request.styles[engine])
                        provider, provider_symbol = self.providers.provider_identity(pair, engine)
                    except Exception as exc:
                        exclusions.append({
                            "symbol": symbol, "engine": engine,
                            "code": getattr(exc, "code", "REQUIREMENT_RESOLUTION_FAILED"), "message": str(exc),
                        })
                        continue
                    policies[f"{engine}:{symbol}"] = requirement
                    provider_contracts[f"{engine}:{symbol}"] = {"provider": provider, "providerSymbol": provider_symbol}
                    for timeframe in requirement["timeframes"]:
                        identity = f"{engine}:{symbol}:{timeframe}:{provider}:{provider_symbol}"
                        # If A and B resolve to the exact same provider series, reuse
                        # the immutable file and keep one manifest row.
                        existing = next(
                            (
                                item for item in series_rows
                                if item["symbol"] == symbol and item["timeframe"] == timeframe
                                and item["provider"] == provider and item["provider_symbol"] == provider_symbol
                            ),
                            None,
                        )
                        if existing:
                            existing.setdefault("engines", []).append(engine)
                            continue
                        try:
                            fetched = self.providers.fetch(
                                pair,
                                engine=engine,
                                timeframe=timeframe,
                                start_date=request.start_date,
                                end_date=request.end_date,
                            )
                            normalized, quality = normalize_and_validate(
                                fetched.candles,
                                timeframe=timeframe,
                                asset_type=str(pair.get("type") or ""),
                                provider=provider,
                                requested_start=datetime.combine(request.start_date, time.min, tzinfo=timezone.utc),
                                requested_end=datetime.combine(request.end_date, time.max, tzinfo=timezone.utc),
                                as_of=datetime.fromisoformat(created_at),
                            )
                            limitations = list(quality.limitations)
                            corporate_actions_status = "not_applicable"
                            if str(pair.get("type") or "").lower() in {"stock", "equity", "etf"}:
                                corporate_actions_status = "covered_by_adjusted_series" if fetched.adjustment_status == "adjusted" else "missing"
                                if corporate_actions_status == "missing":
                                    limitations.append("corporate_actions_missing")
                            quality_state = "PASS" if not limitations else "FAIL"
                            if request.mode == "CERTIFIED" and quality_state != "PASS":
                                raise ProviderDataError(
                                    "CERTIFIED_DATA_QUALITY_FAILED",
                                    ", ".join(limitations),
                                    provider=provider,
                                    symbol=symbol,
                                    timeframe=timeframe,
                                )
                            series_id = content_hash({
                                "identity": identity,
                                "start": request.start_date.isoformat(),
                                "end": request.end_date.isoformat(),
                                "rows": normalized.to_dict(orient="records"),
                            })
                            path = staging_dir / f"{series_id}.parquet"
                            table = pa.Table.from_pandas(normalized, preserve_index=False)
                            pq.write_table(table, path, compression="zstd", use_dictionary=True)
                            file_hash = file_sha256(path)
                            calendar, market_tz = _calendar_for(str(pair.get("type") or ""), provider)
                            manifest = DatasetSeriesManifest(
                                series_id=series_id,
                                symbol=symbol,
                                provider=provider,
                                provider_symbol=provider_symbol,
                                timeframe=timeframe,
                                asset_type=str(pair.get("type") or ""),
                                path=str(path),
                                file_hash=file_hash,
                                row_count=quality.row_count,
                                first_timestamp=quality.first_timestamp,
                                last_timestamp=quality.last_timestamp,
                                gap_count=quality.gap_count,
                                provider_no_quote_count=quality.provider_no_quote_count,
                                duplicate_count=quality.duplicate_count,
                                calendar=calendar,
                                market_timezone=market_tz,
                                bar_semantics="UTC_BAR_OPEN",
                                adjustment_status=fetched.adjustment_status,
                                corporate_actions_status=corporate_actions_status,
                                quality_state=quality_state,
                                limitations=tuple(limitations),
                                observations=quality.observations,
                            )
                            row = asdict(manifest)
                            row["engines"] = [engine]
                            series_rows.append(row)
                        except ProviderDataError as exc:
                            exclusions.append({"symbol": symbol, "engine": engine, "timeframe": timeframe, **exc.to_dict()})
                            # A missing required role excludes the whole engine/symbol.
                            kept_rows = []
                            for item in series_rows:
                                if item["symbol"] == symbol and engine in item.get("engines", []):
                                    item["engines"] = [owner for owner in item.get("engines", []) if owner != engine]
                                if item.get("engines"):
                                    kept_rows.append(item)
                            series_rows = kept_rows
                            break

            requested_tasks = len(request.symbols) * len(request.engines)
            viable_tasks = 0
            for symbol in request.symbols:
                for engine in request.engines:
                    requirement = policies.get(f"{engine}:{symbol}")
                    if not requirement:
                        continue
                    present = {
                        item["timeframe"] for item in series_rows
                        if item["symbol"] == symbol and engine in item.get("engines", [])
                    }
                    if set(requirement["timeframes"]).issubset(present):
                        viable_tasks += 1
            if request.mode == "CERTIFIED":
                certification = "CERTIFIED" if viable_tasks == requested_tasks and not exclusions else "PARTIAL_CERTIFIED"
            else:
                certification = "EXPLORATORY"

            manifest_core = {
                "platformVersion": PLATFORM_VERSION,
                "createdAt": created_at,
                "status": "READY" if series_rows else "FAILED",
                "requestedMode": request.mode,
                "certification": certification,
                "startDate": request.start_date.isoformat(),
                "endDate": request.end_date.isoformat(),
                "symbols": list(request.symbols),
                "engines": list(request.engines),
                "styles": request.styles,
                "barSemantics": "UTC_BAR_OPEN",
                "policies": policies,
                "providerContracts": provider_contracts,
                "pairSnapshots": pair_snapshots,
                "series": series_rows,
                "exclusions": exclusions,
            }
            identity_manifest = {
                key: value for key, value in manifest_core.items()
                if key != "createdAt"
            }
            identity_manifest["series"] = [
                {key: value for key, value in item.items() if key != "path"}
                for item in series_rows
            ]
            dataset_id = content_hash(identity_manifest)
            final_dir = assert_within_runtime(self.layout["datasets"] / dataset_id)
            if final_dir.exists():
                shutil.rmtree(staging_dir)
                existing_manifest = self.catalog.get_dataset(dataset_id)
                if existing_manifest is not None:
                    return existing_manifest
            else:
                staging_dir.rename(final_dir)
            for item in series_rows:
                item["path"] = str(final_dir / Path(item["path"]).name)
            manifest_core["datasetId"] = dataset_id
            manifest_core["manifestHash"] = content_hash(manifest_core)
            manifest_path = final_dir / "manifest.json"
            manifest_path.write_text(canonical_json(manifest_core), encoding="utf-8")
            self.catalog.upsert_dataset(manifest_core)
            return manifest_core
        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            raise


def load_dataset_series(manifest: dict[str, Any], symbol: str, timeframe: str, engine: str) -> pd.DataFrame:
    row = next(
        (
            item for item in manifest.get("series", [])
            if item.get("symbol") == symbol and item.get("timeframe") == timeframe
            and engine in item.get("engines", [])
        ),
        None,
    )
    if row is None:
        raise KeyError(f"dataset missing {engine}:{symbol}:{timeframe}")
    path = Path(str(row["path"]))
    if not path.is_file() or file_sha256(path) != row["file_hash"]:
        raise ValueError(f"dataset hash mismatch for {engine}:{symbol}:{timeframe}")
    frame = pq.read_table(path).to_pandas()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame
