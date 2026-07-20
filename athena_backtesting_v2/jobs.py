from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from .catalog import BacktestCatalog
from .config_snapshot import build_config_snapshot
from .hashing import content_hash
from .models import CostModel, PortfolioPlan, RunRequest, ValidationPlan, utc_now_iso
from .portfolio import simulate_portfolio
from .runner import run_symbol_task
from .statistics import summarize_r


_SPAWN_MAIN_LOCK = threading.RLock()


class _SupervisorProcessLock:
    """One durable supervisor owner across concurrently started app processes."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None


@contextmanager
def _spawn_main_bootstrap():
    """Prevent Windows spawn workers from re-executing the Athena monolith."""

    main_module = sys.modules.get("__main__")
    original_file = getattr(main_module, "__file__", None) if main_module else None
    original_spec = getattr(main_module, "__spec__", None) if main_module else None
    bootstrap = Path(__file__).with_name("spawn_bootstrap.py").resolve()
    if main_module is None or original_file is None or not bootstrap.is_file():
        yield
        return
    with _SPAWN_MAIN_LOCK:
        main_module.__file__ = str(bootstrap)
        main_module.__spec__ = None
        try:
            yield
        finally:
            main_module.__file__ = original_file
            main_module.__spec__ = original_spec


class _AthenaSpawnProcessPoolExecutor(ProcessPoolExecutor):
    def _spawn_process(self) -> None:
        # ProcessPoolExecutor calls this for initial and replacement workers.
        # The temporary bootstrap contains no Flask routes or schedulers.
        with _spawn_main_bootstrap():
            super()._spawn_process()


def _normalized_curves(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cumulative = 0.0
    peak = 0.0
    equity: list[dict[str, Any]] = []
    underwater: list[dict[str, Any]] = []
    for index, row in enumerate(sorted(rows, key=lambda item: (str(item.get("exit_time") or ""), str(item.get("symbol") or "")))):
        cumulative += float(row.get("net_r") or 0.0)
        peak = max(peak, cumulative)
        point = {"idx": index + 1, "time": row.get("exit_time"), "equity": round(cumulative, 8)}
        equity.append(point)
        underwater.append({**point, "drawdown": round(cumulative - peak, 8)})
    return equity, underwater


def _grouped_trade_metrics(rows: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(key_fn(row) or "UNKNOWN").upper()
        groups.setdefault(key, []).append(row)
    return {
        key: summarize_r(
            [float(row.get("net_r") or 0.0) for row in group],
            [float(row.get("cost_r") or 0.0) for row in group],
        )
        for key, group in sorted(groups.items())
    }


def _resolve_run_outcome(
    successful: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    if successful:
        return "COMPLETED", None
    if excluded and not failed:
        return "FAILED", {
            "code": "ALL_TASKS_EXCLUDED",
            "error": "No Engine A/B task reached strategy evaluation because all requested tasks were excluded.",
        }
    return "FAILED", {
        "code": "NO_SUCCESSFUL_TASKS",
        "error": "No Engine A/B task completed strategy evaluation.",
    }


class LocalJobSupervisor:
    """Durable single-job supervisor with a Windows spawn process pool."""

    def __init__(
        self,
        *,
        catalog: BacktestCatalog,
        prepare_dataset: Callable[[RunRequest], dict[str, Any]],
        config: Mapping[str, Any],
        repo_root: Path,
    ):
        self.catalog = catalog
        self.prepare_dataset = prepare_dataset
        self.config = config
        self.repo_root = repo_root
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._process_lock = _SupervisorProcessLock(self.catalog.path.parent / "supervisor.lock")

    @staticmethod
    def worker_count() -> int:
        logical = os.cpu_count() or 4
        reserve_two = max(1, logical - 2)
        # Conservative 700 MiB/task envelope keeps aggregate workers below 8 GiB.
        memory_cap = max(1, int((8 * 1024 - 768) / 700))
        return min(reserve_two, memory_cap, 10)

    def start(self) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            if not self._process_lock.acquire():
                return
            self.catalog.recover_incomplete_runs()
            self._thread = threading.Thread(target=self._loop, name="AthenaBacktestV2Supervisor", daemon=True)
            self._thread.start()
            self._wake.set()

    def notify(self) -> None:
        self.start()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            queued = self.catalog.queued_run_ids()
            if not queued:
                self._wake.wait(timeout=5.0)
                self._wake.clear()
                continue
            self._execute_run(queued[0])

    @staticmethod
    def _request_from_dict(raw: dict[str, Any]) -> RunRequest:
        return RunRequest(
            engines=tuple(raw["engines"]),
            styles=dict(raw["styles"]),
            symbols=tuple(raw["symbols"]),
            start_date=date.fromisoformat(raw["start_date"]),
            end_date=date.fromisoformat(raw["end_date"]),
            mode=raw["mode"],
            dataset_id=raw.get("dataset_id"),
            validation=ValidationPlan(**raw["validation"]),
            cost_model=CostModel(**raw["cost_model"]),
            portfolio=PortfolioPlan(**raw["portfolio"]),
            comparison_family=raw.get("comparison_family"),
            variant_id=raw.get("variant_id"),
            random_seed=int(raw["random_seed"]),
            force=bool(raw.get("force", False)),
        )

    def _execute_run(self, run_id: str) -> None:
        run = self.catalog.get_run(run_id)
        if not run:
            return
        request = self._request_from_dict(run["request"])
        data_started = time.perf_counter()
        self.catalog.update_run(run_id, status="PREPARING_DATA", phase="PREPARING_DATA", started_at=utc_now_iso())
        try:
            dataset = None
            existing_dataset_id = str(run.get("dataset_id") or "").strip()
            if existing_dataset_id:
                existing_dataset = self.catalog.get_dataset(existing_dataset_id)
                if existing_dataset and existing_dataset.get("status") == "READY":
                    dataset = self.prepare_dataset(replace(request, dataset_id=existing_dataset_id))
            if dataset is None:
                dataset = self.prepare_dataset(request)
            data_seconds = time.perf_counter() - data_started
            snapshot = build_config_snapshot(
                config=self.config,
                policies=dataset.get("policies") or {},
                cost_model=request.cost_model,
                random_seed=request.random_seed,
                repo_root=self.repo_root,
            )
            snapshot_dict = asdict(snapshot)
            reusable_hash = content_hash(
                {
                    "request": {**request.to_dict(), "force": False},
                    "datasetId": dataset["datasetId"],
                    "configSnapshotId": snapshot.snapshot_id,
                }
            )
            self.catalog.update_run(
                run_id,
                reusable_hash=reusable_hash,
                dataset_id=dataset["datasetId"],
                config_snapshot_id=snapshot.snapshot_id,
                config_snapshot_json=snapshot_dict,
                certification=dataset["certification"],
            )
            if not request.force:
                reused = self.catalog.find_reusable_run(reusable_hash)
                reused_result = (reused or {}).get("result") or {}
                if reused and reused["run_id"] != run_id and reused_result.get("tasks"):
                    result = dict(reused.get("result") or {})
                    result.update({"runId": run_id, "reused": True, "reusedFromRunId": reused["run_id"]})
                    self.catalog.clone_trades(reused["run_id"], run_id)
                    for task in run.get("tasks") or []:
                        self.catalog.update_task(task["task_id"], status="REUSED", completed_at=utc_now_iso())
                    self.catalog.update_run(
                        run_id,
                        status="COMPLETED",
                        phase="COMPLETED_REUSED",
                        progress_completed=run["progress_total"],
                        completed_at=utc_now_iso(),
                        result_json=result,
                    )
                    return
            if self.catalog.cancel_requested(run_id):
                self.catalog.update_run(run_id, status="CANCELLED", phase="CANCELLED", completed_at=utc_now_iso())
                return
            self.catalog.update_run(run_id, status="RUNNING", phase="RUNNING")
            pending = self.catalog.pending_tasks(run_id)
            trial_count = int((run.get("request") or {}).get("trial_count", 1))
            futures: dict[Future, dict[str, Any]] = {}
            refreshed_run = self.catalog.get_run(run_id) or run
            task_results: list[dict[str, Any]] = [
                dict(task["result"])
                for task in (refreshed_run.get("tasks") or [])
                if task.get("status") in {"COMPLETED", "REUSED"} and isinstance(task.get("result"), dict)
            ]
            context = multiprocessing.get_context("spawn")
            with _AthenaSpawnProcessPoolExecutor(max_workers=self.worker_count(), mp_context=context) as pool:
                for task in pending:
                    self.catalog.update_task(task["task_id"], status="RUNNING", started_at=utc_now_iso())
                    payload = {
                        "run_id": run_id,
                        "task": {"task_id": task["task_id"], "engine": task["engine"], "symbol": task["symbol"], "style": task["style"]},
                        "request": request.to_dict(),
                        "dataset": dataset,
                        "config_snapshot": snapshot_dict,
                        "trial_count": trial_count,
                    }
                    futures[pool.submit(run_symbol_task, payload)] = task
                completed = int(run.get("progress_completed") or 0)
                for future in as_completed(futures):
                    task = futures[future]
                    if self.catalog.cancel_requested(run_id):
                        for pending_future in futures:
                            pending_future.cancel()
                        break
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            "success": False,
                            "excluded": False,
                            "code": "WORKER_PROCESS_FAILED",
                            "error": str(exc),
                            "task": {key: task[key] for key in ("task_id", "engine", "symbol", "style")},
                            "durationSec": 0.0,
                            "peakMemoryBytes": 0,
                        }
                    task_results.append(result)
                    completed += 1
                    status = "COMPLETED" if result.get("success") else ("EXCLUDED" if result.get("excluded") else "FAILED")
                    self.catalog.update_task(
                        task["task_id"],
                        status=status,
                        completed_at=utc_now_iso(),
                        duration_sec=float((result.get("timing") or {}).get("computeSec", result.get("durationSec", 0.0))),
                        peak_memory_bytes=int(result.get("peakMemoryBytes", 0)),
                        result_json=result if result.get("success") else None,
                        error_json=None if result.get("success") else {"code": result.get("code"), "error": result.get("error")},
                    )
                    self.catalog.update_run(run_id, progress_completed=completed)
            if self.catalog.cancel_requested(run_id):
                self.catalog.update_run(run_id, status="CANCELLED", phase="CANCELLED", completed_at=utc_now_iso())
                return
            self.catalog.update_run(run_id, status="VALIDATING", phase="VALIDATING")
            successful = [result for result in task_results if result.get("success")]
            failed = [result for result in task_results if not result.get("success") and not result.get("excluded")]
            excluded = [result for result in task_results if result.get("excluded")]
            trades = [trade for result in successful for trade in result.get("trades", [])]
            self.catalog.save_trades(run_id, trades)
            by_engine = {}
            for engine in request.engines:
                rows = [trade for trade in trades if trade.get("engine") == engine]
                equity_curve, underwater_curve = _normalized_curves(rows)
                by_engine[engine] = {
                    "metrics": summarize_r([float(row["net_r"]) for row in rows], [float(row["cost_r"]) for row in rows]),
                    "taskCount": sum(1 for result in successful if (result.get("task") or {}).get("engine") == engine),
                    "tradeCount": len(rows),
                    "equityCurve": equity_curve,
                    "underwaterCurve": underwater_curve,
                    "analysis": {
                        "direction": _grouped_trade_metrics(rows, lambda row: row.get("direction")),
                        "symbol": _grouped_trade_metrics(rows, lambda row: row.get("symbol")),
                        "regime": _grouped_trade_metrics(
                            rows,
                            lambda row: (row.get("decision") or {}).get("regime")
                            or ((row.get("decision") or {}).get("confidence") or {}).get("regime"),
                        ),
                    },
                }
            portfolio = simulate_portfolio(trades, request.portfolio)
            result_payload = {
                "runId": run_id,
                "status": "COMPLETED",
                "certification": dataset["certification"],
                "datasetId": dataset["datasetId"],
                "configSnapshotId": snapshot.snapshot_id,
                "engines": by_engine,
                "tasks": [
                    {key: value for key, value in result.items() if key != "trades"}
                    for result in successful
                ],
                "exclusions": list(dataset.get("exclusions") or []) + [
                    {"task": row.get("task"), "code": row.get("code"), "message": row.get("error")}
                    for row in excluded
                ],
                "failures": [
                    {"task": row.get("task"), "code": row.get("code"), "message": row.get("error")}
                    for row in failed
                ],
                "portfolio": portfolio,
                "costModel": asdict(request.cost_model),
                "validationPlan": asdict(request.validation),
                "timing": {
                    "dataAcquisitionSec": round(data_seconds, 4),
                    "computeSec": round(sum(float((row.get("timing") or {}).get("computeSec", 0.0)) for row in successful), 4),
                    "jitCompilationSec": round(sum(float((row.get("timing") or {}).get("jitCompilationSec", 0.0)) for row in successful), 4),
                },
                "peakWorkerMemoryBytes": max((int(row.get("peakMemoryBytes", 0)) for row in task_results), default=0),
                "assurance": {
                    "aiExcluded": True,
                    "operationalGatesExcluded": ["kill_switch", "account_state", "broker_availability", "execution_approval"],
                    "sameBarPolicy": "ADVERSE_STOP_FIRST",
                    "engineIndependence": True,
                    "legacyBacktestImports": False,
                },
            }
            final_status, final_error = _resolve_run_outcome(successful, failed, excluded)
            result_payload["status"] = final_status
            self.catalog.update_run(
                run_id,
                status=final_status,
                phase=final_status,
                completed_at=utc_now_iso(),
                result_json=result_payload,
                error_json=(
                    {
                        **(final_error or {}),
                        "taskFailures": result_payload["failures"],
                        "taskExclusions": result_payload["exclusions"],
                    }
                    if final_status == "FAILED"
                    else None
                ),
            )
        except Exception as exc:
            self.catalog.update_run(
                run_id,
                status="FAILED",
                phase="FAILED",
                completed_at=utc_now_iso(),
                error_json={"code": "RUN_FAILED", "error": str(exc)},
            )
