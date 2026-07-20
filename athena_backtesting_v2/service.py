from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Mapping

from .catalog import BacktestCatalog
from .comparisons import build_comparison
from .constants import (
    CERTIFICATION_STATES,
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_MAX_CONCURRENT_POSITIONS,
    DEFAULT_RISK_PER_TRADE,
    ENGINES,
    ENGINE_STYLES,
    PLATFORM_VERSION,
    RUN_STATUSES,
    SIMULATOR_VERSION,
)
from .datasets import DatasetBuilder
from .hashing import content_hash
from .jobs import LocalJobSupervisor
from .models import RequestValidationError, RunRequest, utc_now_iso
from .providers import RuntimeProviderAdapter


class BacktestingV2Service:
    def __init__(
        self,
        *,
        providers: RuntimeProviderAdapter,
        config: Mapping[str, Any],
        repo_root: Path,
        catalog: BacktestCatalog | None = None,
    ):
        self.catalog = catalog or BacktestCatalog()
        self.providers = providers
        self.config = config
        self.repo_root = repo_root
        self.datasets = DatasetBuilder(self.catalog, providers)
        self.supervisor = LocalJobSupervisor(
            catalog=self.catalog,
            prepare_dataset=self.datasets.build,
            config=config,
            repo_root=repo_root,
        )
        if self.enabled:
            self.supervisor.start()

    @property
    def enabled(self) -> bool:
        return str(os.environ.get("ATHENA_BACKTESTING_V2_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}

    def capabilities(self) -> dict[str, Any]:
        pairs = self.providers.list_pairs()
        universe = [
            {
                "symbol": str(pair.get("display") or pair.get("symbol") or ""),
                "providerSymbol": str(pair.get("symbol") or pair.get("display") or ""),
                "assetType": str(pair.get("type") or "other"),
                "enabled": bool(pair.get("enabled", True)),
            }
            for pair in pairs
            if pair.get("display") or pair.get("symbol")
        ]
        return {
            "enabled": self.enabled,
            "platformVersion": PLATFORM_VERSION,
            "simulatorVersion": SIMULATOR_VERSION,
            "engines": {engine: {"styles": list(ENGINE_STYLES[engine])} for engine in ENGINES},
            "certificationStates": list(CERTIFICATION_STATES),
            "runStatuses": list(RUN_STATUSES),
            "universe": universe,
            "assetTypes": sorted({item["assetType"] for item in universe}),
            "portfolioDefaults": {
                "initialCapital": DEFAULT_INITIAL_CAPITAL,
                "riskPerTrade": DEFAULT_RISK_PER_TRADE,
                "maxConcurrentPositions": DEFAULT_MAX_CONCURRENT_POSITIONS,
                "enabled": False,
            },
            "workerPolicy": {
                "model": "windows_spawn_process_pool",
                "oneActiveJob": True,
                "reservedLogicalCores": 2,
                "maxWorkers": self.supervisor.worker_count(),
                "memoryCeilingBytes": 8 * 1024**3,
            },
            "assurance": {
                "legacyRulesUsed": False,
                "aiIncluded": False,
                "enginesBlended": False,
                "simulationFetchesLiveData": False,
            },
        }

    def preflight(self, payload: Any) -> dict[str, Any]:
        request = RunRequest.from_payload(payload)
        return {"success": True, "request": request.to_dict(), **self.datasets.preflight(request)}

    def prepare_dataset(self, payload: Any) -> dict[str, Any]:
        request = RunRequest.from_payload(payload)
        return self.datasets.build(request)

    def list_datasets(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        return self.catalog.list_datasets(limit=limit, offset=offset)

    def submit(self, payload: Any) -> tuple[dict[str, Any], bool]:
        request = RunRequest.from_payload(payload)
        preflight = self.datasets.preflight(request)
        if not preflight["instruments"]:
            raise RequestValidationError(
                "NO_VIABLE_INSTRUMENTS",
                "every requested engine/instrument was excluded by provider or policy preflight",
            )
        request_dict = request.to_dict()
        existing_runs = self.catalog.list_runs(limit=10_000, offset=0)
        family_trials = 1
        if request.comparison_family:
            family_variants = {
                str((row.get("request") or {}).get("variant_id") or row.get("request_hash"))
                for row in existing_runs
                if (row.get("request") or {}).get("comparison_family") == request.comparison_family
            }
            current_variant = str(
                request.variant_id
                or content_hash({**request_dict, "force": False, "trial_count": None})
            )
            family_variants.add(current_variant)
            family_trials = len(family_variants)
        request_dict["trial_count"] = family_trials
        request_hash = content_hash(request_dict)
        initial_reuse_hash = content_hash({"request": request_dict, "datasetPending": request.dataset_id or "PREPARE"})
        if not request.force and request.dataset_id:
            # Final config/data hashing occurs in the supervisor.  This quick
            # check only avoids duplicate queued requests with the same explicit snapshot.
            for row in existing_runs:
                if row.get("request_hash") == request_hash and row.get("status") in {"QUEUED", "PREPARING_DATA", "RUNNING", "VALIDATING"}:
                    return {"runId": row["run_id"], "status": row["status"], "reused": True}, True
        run_id = f"btv2_{uuid.uuid4().hex}"
        created_at = utc_now_iso()
        tasks = [
            {
                "task_id": f"{run_id}_{engine}_{content_hash(symbol)[:12]}",
                "engine": engine,
                "symbol": symbol,
                "style": request.styles[engine],
            }
            for engine in request.engines
            for symbol in request.symbols
        ]
        self.catalog.insert_run(
            {
                "run_id": run_id,
                "request_hash": request_hash,
                "reusable_hash": initial_reuse_hash,
                "created_at": created_at,
                "status": "QUEUED",
                "phase": "QUEUED",
                "request": request_dict,
                "certification": preflight["certification"],
            },
            tasks,
        )
        self.supervisor.notify()
        return {
            "runId": run_id,
            "status": "QUEUED",
            "phase": "QUEUED",
            "createdAt": created_at,
            "progress": {"completed": 0, "total": len(tasks)},
            "preflight": preflight,
            "reused": False,
        }, False

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.catalog.get_run(run_id)

    def list_runs(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        return self.catalog.list_runs(limit=limit, offset=offset)

    def cancel(self, run_id: str) -> bool:
        cancelled = self.catalog.request_cancel(run_id)
        if cancelled:
            self.supervisor.notify()
        return cancelled

    def trades(self, run_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        return self.catalog.list_trades(run_id, limit=limit, offset=offset)

    def create_comparison(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("runIds"), list):
            raise RequestValidationError("INVALID_COMPARISON", "runIds must be a list")
        run_ids = list(dict.fromkeys(str(value).strip() for value in payload["runIds"] if str(value).strip()))
        if len(run_ids) < 2:
            raise RequestValidationError("INVALID_COMPARISON", "at least two distinct runs are required")
        runs = []
        trades_by_run = {}
        for run_id in run_ids:
            run = self.catalog.get_run(run_id)
            if run is None:
                raise RequestValidationError("RUN_NOT_FOUND", f"run {run_id} not found")
            if run.get("status") != "COMPLETED":
                raise RequestValidationError("RUN_NOT_COMPLETED", f"run {run_id} is not completed")
            runs.append(run)
            page = self.catalog.list_trades(run_id, limit=1_000_000, offset=0)
            trades_by_run[run_id] = page["items"]
        result = build_comparison(runs, trades_by_run, seed=int(payload.get("randomSeed", 17_071_986)))
        comparison_id = f"btcmp_{uuid.uuid4().hex}"
        compatibility_hash = str(result.get("compatibilityHash") or content_hash(result.get("incompatibilityReasons") or []))
        self.catalog.save_comparison(comparison_id, run_ids, compatibility_hash, result)
        return {"comparisonId": comparison_id, "createdAt": utc_now_iso(), **result}

    def list_comparisons(self, *, limit: int) -> list[dict[str, Any]]:
        return self.catalog.list_comparisons(limit=limit)
