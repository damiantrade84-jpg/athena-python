#!/usr/bin/env python3
"""
ATHENA Backtest Matrix Runner — safe, resumable CLI batch backtest runner.

Generates clean historical closed-trade data for Strategy Lab by running a
configurable matrix of asset groups × symbols × engines × timeframes.

Rules observed:
- Read-only / backtest-only (no live execution, no trades placed).
- Uses existing backtest functions without modifying scoring logic.
- Saves partial results after every job.
- Resumes from manifest and per-job JSON files.
- Continues on single-job failures.
- Supports dry-run for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# YAML is optional at import time so tests can import without it installed.
# ---------------------------------------------------------------------------
try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Engine compatibility matrix — maps requested engine + timeframe to the
# actual backtest function call spec.  Unsupported combos are dropped during
# job expansion.
# ---------------------------------------------------------------------------
_COMPATIBILITY: dict[str, dict[str, dict[str, Any]]] = {
    "A": {
        "H1": {"style": "scalp", "validation_mode": "standard", "purge_gap": 200, "folds": 3},
        "H4": {"style": "intraday", "validation_mode": "standard", "purge_gap": 200, "folds": 3},
        "D1": {"style": "swing", "validation_mode": "standard", "purge_gap": 200, "folds": 3},
    },
    "B": {
        "H1": {"style": "naked", "validation_mode": "standard", "purge_gap": 200, "folds": 3},
        "H4": {"style": "naked", "validation_mode": "standard", "purge_gap": 200, "folds": 3},
    },
    "C": {
        "H4": {"style": "intraday", "validation_mode": "standard", "purge_gap": 200, "folds": 3},
    },
    "D": {
        "M15": {"validation_mode": "standard"},
    },
}

_STRATEGY_FAMILY_MAP = {
    "A": "ENGINE_A_ONLY",
    "B": "ENGINE_B_ONLY",
    "C": "ENGINE_C_CONSENSUS",
    "D": "ENGINE_D_SCALP",
}

_BACKTEST_FUNC_MAP = {
    "A": "backtest_pair",
    "B": "backtest_pair_naked",
    "C": "backtest_pair_consensus",
    "D": "backtest_pair_scalp",
}


# ---------------------------------------------------------------------------
# Paths (updated once --output is parsed)
# ---------------------------------------------------------------------------
_LOG_DIR: Path = Path("logs/backtest_matrix")
_JOBS_DIR: Path = _LOG_DIR / "jobs"
_MANIFEST_PATH: Path = _LOG_DIR / "manifest.json"
_SUMMARY_PATH: Path = _LOG_DIR / "backtest_matrix_summary.json"
_FAILURES_PATH: Path = _LOG_DIR / "backtest_matrix_failures.json"
_PROGRESS_PATH: Path = _LOG_DIR / "backtest_matrix_progress.json"
_STRATEGY_LAB_PATH: Path = _LOG_DIR / "bt_results_strategy_lab.json"
_RUN_LOG_PATH: Path = _LOG_DIR / "run.log"


def _update_paths(output_dir: str) -> None:
    global _LOG_DIR, _JOBS_DIR, _MANIFEST_PATH, _SUMMARY_PATH
    global _FAILURES_PATH, _PROGRESS_PATH, _STRATEGY_LAB_PATH, _RUN_LOG_PATH
    _LOG_DIR = Path(output_dir)
    _JOBS_DIR = _LOG_DIR / "jobs"
    _MANIFEST_PATH = _LOG_DIR / "manifest.json"
    _SUMMARY_PATH = _LOG_DIR / "backtest_matrix_summary.json"
    _FAILURES_PATH = _LOG_DIR / "backtest_matrix_failures.json"
    _PROGRESS_PATH = _LOG_DIR / "backtest_matrix_progress.json"
    _STRATEGY_LAB_PATH = _LOG_DIR / "bt_results_strategy_lab.json"
    _RUN_LOG_PATH = _LOG_DIR / "run.log"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _setup_logging() -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(_RUN_LOG_PATH, encoding="utf-8", mode="a"))
    except Exception as exc:
        print(f"[WARN] Could not open run.log: {exc}", file=sys.stderr)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    log = logging.getLogger("backtest_matrix")
    # Unbuffered stdout so background-task heartbeat stays alive
    for h in log.handlers:
        if isinstance(h, logging.StreamHandler) and h.stream == sys.stdout:
            h.flush = lambda: sys.stdout.flush()
    return log


def _start_heartbeat(log: logging.Logger, interval: float = 30.0):
    """Print a keepalive line every interval seconds so background workers don't expire."""
    import threading
    _stop_evt = threading.Event()
    _start_t = time.time()

    def _beat():
        while not _stop_evt.is_set():
            _stop_evt.wait(interval)
            if not _stop_evt.is_set():
                elapsed = int(time.time() - _start_t)
                log.info("[HEARTBEAT] runner alive, elapsed=%ds", elapsed)
                sys.stdout.flush()

    t = threading.Thread(target=_beat, daemon=True, name="btm-heartbeat")
    t.start()
    return _stop_evt


# ---------------------------------------------------------------------------
# Athena module loader
# ---------------------------------------------------------------------------
def _load_athena_module() -> Any:
    import importlib.util
    import os
    import sys
    # Backtest-only runtime: keep auto_trader.configure() from starting the
    # TimedExitMonitor broker thread (guard at auto_trader.py). Without this,
    # exec'ing athena.py here spins up a monitor that closes open broker tickets.
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
    athena_path = Path("athena.py").resolve()
    if not athena_path.exists():
        raise FileNotFoundError(f"athena.py not found at {athena_path}")
    # Ensure repo root is on path so athena.py can import sibling modules
    repo_root = str(athena_path.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    spec = importlib.util.spec_from_file_location("athena_mod", athena_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not create module spec for athena.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------
def _load_matrix(path: str) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load the matrix config.")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Matrix YAML must be a mapping (dict).")
    return data


# ---------------------------------------------------------------------------
# Job expansion
# ---------------------------------------------------------------------------
def _safe_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "_").replace("\\", "_").replace(" ", "_").replace(":", "_")


def _job_id(job: dict) -> str:
    return f"{job['asset_group']}_{_safe_symbol(job['symbol'])}_{job['engine']}_{job['timeframe']}"


def expand_jobs(matrix: dict, filters: dict) -> list[dict]:
    defaults = matrix.get("defaults", {})
    jobs: list[dict] = []
    for group_name, group_cfg in matrix.get("asset_groups", {}).items():
        for symbol in group_cfg.get("symbols", []):
            for engine in group_cfg.get("engines", []):
                for timeframe in group_cfg.get("timeframes", []):
                    # Filter checks
                    if filters.get("engines") and engine not in filters["engines"]:
                        continue
                    if filters.get("groups") and group_name not in filters["groups"]:
                        continue
                    if filters.get("symbols") and symbol not in filters["symbols"]:
                        continue
                    if filters.get("timeframes") and timeframe not in filters["timeframes"]:
                        continue

                    # Engine/timeframe compatibility
                    compat = _COMPATIBILITY.get(engine, {}).get(timeframe)
                    if compat is None:
                        continue

                    job = {
                        "asset_group": group_name,
                        "symbol": symbol,
                        "engine": engine,
                        "timeframe": timeframe,
                        "start_date": filters.get("start_date") or defaults.get("start_date"),
                        "end_date": filters.get("end_date") or defaults.get("end_date"),
                        "initial_balance": defaults.get("initial_balance", 10000),
                        "save_after_each_job": defaults.get("save_after_each_job", True),
                        "fail_fast": defaults.get("fail_fast", False),
                        "call_spec": dict(compat),
                        "strategy_family": _STRATEGY_FAMILY_MAP.get(engine, "UNKNOWN"),
                    }
                    job["job_id"] = _job_id(job)
                    jobs.append(job)
    return jobs


# ---------------------------------------------------------------------------
# Resume logic
# ---------------------------------------------------------------------------
def _job_output_path(job: dict) -> Path:
    return _JOBS_DIR / job["asset_group"] / _safe_symbol(job["symbol"]) / f"{job['engine']}_{job['timeframe']}.json"


def _should_skip_job(job: dict, resume: bool) -> bool:
    if not resume:
        return False
    path = _job_output_path(job)
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("status") == "success"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pair resolution
# ---------------------------------------------------------------------------
def resolve_pair(all_pairs: list[dict], symbol: str) -> dict | None:
    sym_upper = str(symbol).upper().strip()
    for p in all_pairs:
        if p.get("display", "").upper() == sym_upper or p.get("symbol", "").upper() == sym_upper:
            return p
    return None


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------
def run_single_job(
    job: dict,
    all_pairs: list[dict],
    backtest_funcs: dict[str, Any],
    log: logging.Logger,
) -> dict:
    t0 = time.time()
    result: dict[str, Any] = {
        "job_id": job["job_id"],
        "asset_group": job["asset_group"],
        "symbol": job["symbol"],
        "engine": job["engine"],
        "strategy_family": job["strategy_family"],
        "timeframe": job["timeframe"],
        "start_date": job.get("start_date"),
        "end_date": job.get("end_date"),
        "status": "running",
        "error": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "runtime_seconds": 0.0,
        "closed_trades": [],
        "rejected_setups": [],
        "summary_metrics": {},
        "data_source": None,
        "candle_count": None,
        "telemetry_version": "unknown",
    }

    pair = resolve_pair(all_pairs, job["symbol"])
    if not pair:
        result["status"] = "failed"
        result["error"] = f"Symbol {job['symbol']} not found in ALL_PAIRS"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["runtime_seconds"] = round(time.time() - t0, 2)
        return result

    result["data_source"] = pair.get("source", "unknown")

    try:
        func_entry = backtest_funcs[job["engine"]]
        func = func_entry["func"]
        func_name = func_entry["name"]
        kwargs = dict(job["call_spec"])

        log.info(
            "[JOB START] %s -> %s(%s)",
            job["job_id"],
            func_name,
            ", ".join(f"{k}={v!r}" for k, v in kwargs.items()),
        )

        bt_result = func(pair, **kwargs)

        if bt_result is None:
            result["status"] = "failed"
            result["error"] = "Backtest returned None"
        elif not isinstance(bt_result, dict):
            result["status"] = "failed"
            result["error"] = f"Backtest returned {type(bt_result).__name__}, expected dict"
        elif bt_result.get("error"):
            result["status"] = "failed"
            result["error"] = str(bt_result["error"])
        else:
            result["status"] = "success"
            trades = bt_result.get("trades") or []
            result["closed_trades"] = trades
            # Strip heavy nested objects from summary to keep files small
            result["summary_metrics"] = {
                k: v
                for k, v in bt_result.items()
                if k not in ("trades", "equityCurve", "calibrationReport", "researchMetrics", "metaReport")
            }
            if trades:
                result["telemetry_version"] = trades[0].get("telemetry_version", "unknown")
            # Funnel / rejected setup data
            funnel = (
                bt_result.get("funnel")
                or bt_result.get("engineBFunnel")
                or bt_result.get("engineCFunnel")
                or {}
            )
            result["rejected_setups"] = funnel

    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        log.error("[JOB ERROR] %s: %s", job["job_id"], exc)
        log.debug(traceback.format_exc())

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["runtime_seconds"] = round(time.time() - t0, 2)
    return result


def _save_job_result(result: dict, job: dict) -> None:
    path = _job_output_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Manifest / reports
# ---------------------------------------------------------------------------
def _write_manifest(
    jobs: list[dict],
    completed: int,
    failed: int,
    skipped: int,
    args: argparse.Namespace,
    matrix_hash: str,
) -> None:
    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_jobs": len(jobs),
        "completed_jobs": completed,
        "failed_jobs": failed,
        "skipped_jobs": skipped,
        "running_started_at": getattr(_write_manifest, "_started_at", datetime.now(timezone.utc).isoformat()),
        "last_updated_at": datetime.now(timezone.utc).isoformat(),
        "matrix_hash": matrix_hash,
        "command_args": {
            "matrix": args.matrix,
            "output": args.output,
            "resume": args.resume,
            "max_workers": args.max_workers,
            "engines": args.engines,
            "groups": args.groups,
            "symbols": args.symbols,
            "timeframes": args.timeframes,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "dry_run": args.dry_run,
        },
    }
    _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)


def build_combined_strategy_lab_output(jobs: list[dict]) -> list[dict]:
    flat: list[dict] = []
    for job in jobs:
        path = _job_output_path(job)
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("status") != "success":
            continue
        for trade in data.get("closed_trades", []):
            enriched = dict(trade)
            enriched["asset_group"] = job["asset_group"]
            enriched["matrix_job_id"] = job["job_id"]
            if not enriched.get("timeframe"):
                enriched["timeframe"] = job["timeframe"]
            flat.append(enriched)
    return flat


def build_summary_report(jobs: list[dict]) -> dict:
    by_group: dict[str, dict] = {}
    by_engine: dict[str, dict] = {}
    total_runtime = 0.0
    run_count = 0
    total_trades = 0

    for job in jobs:
        path = _job_output_path(job)
        data = None
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        g = by_group.setdefault(job["asset_group"], {"total": 0, "completed": 0, "failed": 0, "trades": 0})
        e = by_engine.setdefault(job["engine"], {"total": 0, "completed": 0, "failed": 0, "trades": 0})
        g["total"] += 1
        e["total"] += 1

        if data:
            if data.get("status") == "success":
                g["completed"] += 1
                e["completed"] += 1
                n_trades = len(data.get("closed_trades", []))
                g["trades"] += n_trades
                e["trades"] += n_trades
                total_trades += n_trades
                if data.get("runtime_seconds"):
                    total_runtime += data["runtime_seconds"]
                    run_count += 1
            elif data.get("status") == "failed":
                g["failed"] += 1
                e["failed"] += 1

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_jobs": len(jobs),
        "total_trades": total_trades,
        "avg_runtime_seconds": round(total_runtime / run_count, 2) if run_count else 0.0,
        "by_group": by_group,
        "by_engine": by_engine,
    }


def build_failures_report(jobs: list[dict]) -> dict:
    failures = []
    for job in jobs:
        path = _job_output_path(job)
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("status") == "failed":
            failures.append({
                "job_id": job["job_id"],
                "asset_group": job["asset_group"],
                "symbol": job["symbol"],
                "engine": job["engine"],
                "timeframe": job["timeframe"],
                "error": data.get("error"),
                "runtime_seconds": data.get("runtime_seconds"),
            })
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "failure_count": len(failures),
        "failures": failures,
    }


def build_progress_report(
    completed: int,
    failed: int,
    skipped: int,
    total: int,
    running_times: list[float],
) -> dict:
    avg = sum(running_times) / len(running_times) if running_times else 0.0
    remaining = total - completed - failed - skipped
    eta_sec = remaining * avg if avg > 0 and remaining > 0 else 0
    pct = round((completed + failed + skipped) / total * 100, 1) if total else 0.0
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "remaining": remaining,
        "percent_complete": pct,
        "avg_runtime_seconds": round(avg, 2),
        "eta_seconds": round(eta_sec, 0),
    }


def _write_progress(progress: dict) -> None:
    _PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Progress logging
# ---------------------------------------------------------------------------
def _log_progress(
    completed: int,
    failed: int,
    skipped: int,
    total: int,
    running_times: list[float],
    log: logging.Logger,
) -> None:
    avg = sum(running_times) / len(running_times) if running_times else 0.0
    remaining = total - completed - failed - skipped
    eta_sec = remaining * avg if avg > 0 and remaining > 0 else 0
    if eta_sec < 60:
        eta_str = f"{eta_sec:.0f}s"
    elif eta_sec < 3600:
        eta_str = f"{eta_sec/60:.0f}m"
    else:
        eta_str = f"{eta_sec/3600:.1f}h"
    log.info(
        "Progress: %d/%d OK | %d failed | %d skipped | avg=%.1fs | ETA %s",
        completed,
        total,
        failed,
        skipped,
        avg,
        eta_str,
    )


# ---------------------------------------------------------------------------
# Dry-run printer
# ---------------------------------------------------------------------------
def _print_dry_run(jobs: list[dict]) -> None:
    print("\n=== DRY RUN ===")
    by_group: dict[str, int] = {}
    by_engine: dict[str, int] = {}
    for job in jobs:
        by_group[job["asset_group"]] = by_group.get(job["asset_group"], 0) + 1
        by_engine[job["engine"]] = by_engine.get(job["engine"], 0) + 1

    print(f"\nTotal jobs: {len(jobs)}")
    print("\nBy group:")
    for g, c in sorted(by_group.items()):
        print(f"  {g}: {c}")
    print("\nBy engine:")
    for e, c in sorted(by_engine.items()):
        print(f"  Engine {e}: {c}")
    print("\nFirst 10 jobs:")
    for job in jobs[:10]:
        print(f"  {job['job_id']}")
    if len(jobs) > 10:
        print(f"  ... and {len(jobs) - 10} more")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ATHENA Backtest Matrix Runner")
    parser.add_argument("--matrix", required=True, help="Path to matrix YAML config")
    parser.add_argument("--output", default="logs/backtest_matrix", help="Output directory")
    parser.add_argument("--resume", action="store_true", help="Skip already-successful jobs")
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel workers (default 1)")
    parser.add_argument("--engines", nargs="*", default=None, help="Optional engine filter (e.g. A B)")
    parser.add_argument("--groups", nargs="*", default=None, help="Optional asset-group filter")
    parser.add_argument("--symbols", nargs="*", default=None, help="Optional symbol filter")
    parser.add_argument("--timeframes", nargs="*", default=None, help="Optional timeframe filter")
    parser.add_argument("--start-date", default=None, help="Start date YYYY-MM-DD (recorded in metadata)")
    parser.add_argument("--end-date", default=None, help="End date YYYY-MM-DD (recorded in metadata)")
    parser.add_argument("--dry-run", action="store_true", help="Print jobs without executing")
    args = parser.parse_args(argv)

    _update_paths(args.output)
    log = _setup_logging()
    _hb_stop = _start_heartbeat(log)

    log.info("=" * 60)
    log.info("ATHENA Backtest Matrix Runner")
    log.info("Matrix : %s", args.matrix)
    log.info("Output : %s", args.output)
    log.info("Resume : %s", args.resume)
    log.info("Workers: %d", args.max_workers)
    log.info("=" * 60)

    # Load matrix
    try:
        matrix = _load_matrix(args.matrix)
    except Exception as exc:
        log.error("Failed to load matrix: %s", exc)
        _hb_stop.set()
        return 1

    matrix_hash = hashlib.sha256(json.dumps(matrix, sort_keys=True, default=str).encode()).hexdigest()[:16]

    # Expand jobs
    filters = {
        "engines": args.engines,
        "groups": args.groups,
        "symbols": args.symbols,
        "timeframes": args.timeframes,
        "start_date": args.start_date,
        "end_date": args.end_date,
    }
    jobs = expand_jobs(matrix, filters)
    log.info("Total jobs after expansion: %d", len(jobs))

    if args.dry_run:
        _print_dry_run(jobs)
        _write_manifest._started_at = datetime.now(timezone.utc).isoformat()  # type: ignore[attr-defined]
        _write_manifest(jobs, 0, 0, 0, args, matrix_hash)
        _hb_stop.set()
        return 0

    if not jobs:
        log.warning("No jobs to run after filtering.")
        _hb_stop.set()
        return 0

    # Load athena module once
    log.info("Loading athena module (this may take a moment)...")
    t_load = time.time()
    try:
        athena_mod = _load_athena_module()
        all_pairs = list(athena_mod.ALL_PAIRS)
    except Exception as exc:
        log.error("Failed to load athena module: %s", exc)
        _hb_stop.set()
        return 1
    log.info("Athena loaded in %.1fs. Total pairs: %d", time.time() - t_load, len(all_pairs))

    # Bind backtest functions
    try:
        from backtest_runner import (
            backtest_pair,
            backtest_pair_naked,
            backtest_pair_consensus,
            backtest_pair_scalp,
        )
    except Exception as exc:
        log.error("Failed to import backtest_runner: %s", exc)
        _hb_stop.set()
        return 1

    backtest_funcs = {
        "A": {"name": "backtest_pair", "func": backtest_pair},
        "B": {"name": "backtest_pair_naked", "func": backtest_pair_naked},
        "C": {"name": "backtest_pair_consensus", "func": backtest_pair_consensus},
        "D": {"name": "backtest_pair_scalp", "func": backtest_pair_scalp},
    }

    # Resume filtering
    jobs_to_run = []
    skipped_count = 0
    for job in jobs:
        if _should_skip_job(job, args.resume):
            skipped_count += 1
        else:
            jobs_to_run.append(job)

    log.info("Jobs to run: %d (skipped: %d)", len(jobs_to_run), skipped_count)

    if not jobs_to_run:
        log.info("Nothing to run. Building outputs from existing jobs...")
        _build_and_write_outputs(jobs, args, matrix_hash, log)
        _hb_stop.set()
        return 0

    # Track start time for manifest
    _write_manifest._started_at = datetime.now(timezone.utc).isoformat()  # type: ignore[attr-defined]

    completed = 0
    failed = 0
    running_times: list[float] = []

    if args.max_workers > 1:
        log.warning("Running with %d workers. Engine B cache contention is possible.", args.max_workers)
        with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
            futures = {
                pool.submit(run_single_job, job, all_pairs, backtest_funcs, log): job
                for job in jobs_to_run
            }
            for fut in as_completed(futures):
                job = futures[fut]
                try:
                    result = fut.result()
                except Exception as exc:
                    result = {
                        "job_id": job["job_id"],
                        "status": "failed",
                        "error": f"Thread exception: {exc}",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "runtime_seconds": 0.0,
                    }
                _save_job_result(result, job)
                completed += 1 if result["status"] == "success" else 0
                failed += 1 if result["status"] == "failed" else 0
                if result.get("runtime_seconds"):
                    running_times.append(result["runtime_seconds"])
                _log_progress(completed, failed, skipped_count, len(jobs_to_run), running_times, log)
                _write_progress(
                    build_progress_report(completed, failed, skipped_count, len(jobs_to_run), running_times)
                )
    else:
        for job in jobs_to_run:
            result = run_single_job(job, all_pairs, backtest_funcs, log)
            _save_job_result(result, job)
            completed += 1 if result["status"] == "success" else 0
            failed += 1 if result["status"] == "failed" else 0
            if result.get("runtime_seconds"):
                running_times.append(result["runtime_seconds"])
            _log_progress(completed, failed, skipped_count, len(jobs_to_run), running_times, log)
            _write_progress(
                build_progress_report(completed, failed, skipped_count, len(jobs_to_run), running_times)
            )

    _build_and_write_outputs(jobs, args, matrix_hash, log)
    _hb_stop.set()
    log.info("Done.")
    return 0


def _build_and_write_outputs(jobs: list[dict], args: argparse.Namespace, matrix_hash: str, log: logging.Logger) -> None:
    log.info("Building combined outputs...")

    # Count final state for manifest
    completed = 0
    failed = 0
    skipped = 0
    for job in jobs:
        path = _job_output_path(job)
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("status") == "success":
            completed += 1
        elif data.get("status") == "failed":
            failed += 1
        if args.resume and data.get("status") == "success":
            skipped += 1  # Approximate; manifest uses total skipped from resume pass

    _write_manifest(jobs, completed, failed, skipped, args, matrix_hash)

    strategy_lab_trades = build_combined_strategy_lab_output(jobs)
    with open(_STRATEGY_LAB_PATH, "w", encoding="utf-8") as f:
        json.dump(strategy_lab_trades, f, indent=2, default=str)
    log.info("Strategy Lab output: %s (%d trades)", _STRATEGY_LAB_PATH, len(strategy_lab_trades))

    summary = build_summary_report(jobs)
    with open(_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    log.info("Summary report: %s", _SUMMARY_PATH)

    failures = build_failures_report(jobs)
    with open(_FAILURES_PATH, "w", encoding="utf-8") as f:
        json.dump(failures, f, indent=2, default=str)
    log.info("Failures report: %s (%d failures)", _FAILURES_PATH, failures.get("failure_count", 0))


if __name__ == "__main__":
    sys.exit(main())
