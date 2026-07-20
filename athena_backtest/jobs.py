"""Async universe backtest jobs for the v3 API.

`POST /api/backtest-all` must not block a Flask request for the length of a
universe run, so jobs run on a background thread that drives a spawn-safe
``ProcessPoolExecutor``. Workers cold-import via the diagnostic-mode monolith
loader and read parquet directly -- no Flask state crosses the process
boundary (symbol strings in, plain dict rows out).
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed


def run_symbol_worker(symbol: str, engine: str, style: str, n_trials_hint: int) -> dict:
    """Top-level, pickle-safe worker: run one pair backtest in a fresh process."""
    from athena_backtest.data import fetchers
    from athena_backtest.runner import run_pair

    mod = fetchers._load_monolith()
    wanted = str(symbol).strip().upper()
    pair = next(
        (
            p
            for p in getattr(mod, "ALL_PAIRS", [])
            if str(p.get("symbol") or "").upper() == wanted
            or str(p.get("display") or "").upper() == wanted
        ),
        None,
    )
    if pair is None:
        return {"symbol": symbol, "error": "unknown_symbol"}
    result = run_pair(pair, engine=engine, horizon=style, n_trials_hint=n_trials_hint)
    result["pair"] = pair.get("display")
    result["symbol"] = pair.get("symbol")
    result["_pair_meta"] = {
        "display": pair.get("display"),
        "symbol": pair.get("symbol"),
        "type": pair.get("type"),
    }
    return result


def _leaderboard_row(result: dict) -> dict:
    metrics = result.get("metricsV3") or {}
    validation = result.get("validation") or {}
    return {
        "pair": result.get("pair"),
        "symbol": result.get("symbol"),
        "error": result.get("error"),
        "trades": result.get("totalTrades"),
        "winRate": result.get("winRate"),
        "sqn": result.get("sqn"),
        "deflatedSqn": metrics.get("deflatedSqn"),
        "profitFactor": result.get("profitFactor"),
        "totalR": result.get("totalR"),
        "maxDrawdownR": result.get("maxDrawdownR"),
        "verdict": validation.get("verdict"),
        "wallTimeSec": result.get("wallTimeSec"),
    }


class BacktestJobManager:
    """In-process registry of async universe jobs (one Flask process)."""

    def __init__(self, *, max_workers: int = 3):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._max_workers = max(1, int(max_workers))

    def start(self, *, symbols: list[str], engine: str, style: str, persist: bool = True) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "jobId": job_id,
            "state": "running",
            "engine": str(engine).upper(),
            "style": str(style).lower(),
            "total": len(symbols),
            "done": 0,
            "rows": [],
            "error": None,
            "startedAt": time.time(),
            "finishedAt": None,
            # Every symbol in the request counts as one trial for the
            # multiple-testing disclosure baked into each result.
            "trialCount": max(1, len(symbols)),
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, list(symbols), str(engine).upper(), str(style).lower(), persist),
            name=f"bt-v3-job-{job_id}",
            daemon=True,
        )
        thread.start()
        return job_id

    def status(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _run_job(self, job_id: str, symbols: list[str], engine: str, style: str, persist: bool) -> None:
        n_trials = max(1, len(symbols))
        try:
            with ProcessPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {
                    pool.submit(run_symbol_worker, symbol, engine, style, n_trials): symbol
                    for symbol in symbols
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # worker crash must not kill the job
                        result = {"symbol": symbol, "error": type(exc).__name__}
                    if persist and not result.get("error"):
                        try:
                            from athena_backtest.results import save_run

                            save_run(
                                result,
                                pair=result.get("_pair_meta") or {"symbol": symbol},
                                engine=engine,
                                style=style,
                            )
                        except Exception:
                            result["persistError"] = True
                    row = _leaderboard_row(result)
                    with self._lock:
                        job = self._jobs[job_id]
                        job["rows"].append(row)
                        job["done"] += 1
            with self._lock:
                job = self._jobs[job_id]
                job["rows"].sort(key=lambda r: (r.get("sqn") is None, -(r.get("sqn") or 0.0)))
                job["state"] = "done"
                job["finishedAt"] = time.time()
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job["state"] = "error"
                job["error"] = str(exc)
                job["finishedAt"] = time.time()
