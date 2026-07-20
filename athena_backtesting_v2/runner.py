from __future__ import annotations

import time
import tracemalloc
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .config_snapshot import apply_worker_config_snapshot
from .constants import TIMEFRAME_SECONDS
from .datasets import load_dataset_series
from .models import CostModel, ValidationPlan
from .replay import replay_engine_a, replay_engine_b
from .simulator import EventDrivenSimulator
from .statistics import summarize_r
from .validation import anchored_boundaries, apply_splits, validate_trades


def run_symbol_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Spawn-safe pure worker entrypoint for one Engine/symbol task."""

    started = time.perf_counter()
    tracemalloc.start()
    try:
        apply_worker_config_snapshot(payload["config_snapshot"])
        task = payload["task"]
        request = payload["request"]
        manifest = payload["dataset"]
        engine = str(task["engine"])
        symbol = str(task["symbol"])
        style = str(task["style"])
        pair = dict((manifest.get("pairSnapshots") or {}).get(symbol) or {})
        if not pair:
            raise ValueError(f"dataset pair snapshot missing for {symbol}")
        requirement = (manifest.get("policies") or {}).get(f"{engine}:{symbol}")
        if not isinstance(requirement, dict):
            return {
                "success": False,
                "excluded": True,
                "code": "DATASET_TASK_EXCLUDED",
                "error": f"no viable certified dataset for {engine}:{symbol}",
                "task": task,
            }
        available = {
            str(row.get("timeframe") or "").upper()
            for row in manifest.get("series", [])
            if row.get("symbol") == symbol and engine in (row.get("engines") or [])
        }
        missing = sorted(set(requirement.get("timeframes") or []) - available)
        if missing:
            return {
                "success": False,
                "excluded": True,
                "code": "DATASET_TASK_EXCLUDED",
                "error": f"required dataset series excluded: {', '.join(missing)}",
                "missingTimeframes": missing,
                "task": task,
            }
        frames = {
            timeframe: load_dataset_series(manifest, symbol, timeframe, engine)
            for timeframe in requirement.get("timeframes", [])
        }
        policy = dict(requirement["policy"])
        if engine == "A":
            intents, funnel = replay_engine_a(pair, frames, style=style, policy=policy)
        elif engine == "B":
            intents, funnel = replay_engine_b(pair, frames, style=style, policy=policy)
        else:
            raise ValueError(f"unsupported engine {engine!r}")
        execution_tf = str(funnel["executionTimeframe"]).upper()
        execution_frame = frames.get(execution_tf)
        if execution_frame is None:
            raise ValueError(f"execution timeframe {execution_tf} absent from dataset")
        cost_model = CostModel(**dict(request["cost_model"]))
        validation_plan = ValidationPlan(**dict(request["validation"]))
        simulator = EventDrivenSimulator(cost_model, max_hold_bars=validation_plan.max_hold_bars)
        trades, simulation = simulator.simulate(payload["run_id"], intents, execution_frame)
        start = datetime.fromisoformat(str(request["start_date"])).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(str(request["end_date"])).replace(tzinfo=timezone.utc)
        if validation_plan.enabled:
            boundaries = anchored_boundaries(
                start,
                end,
                validation_plan,
                execution_seconds=TIMEFRAME_SECONDS[execution_tf],
            )
            apply_splits(trades, boundaries)
            validation = validate_trades(
                trades,
                boundaries,
                validation_plan,
                seed=int(request["random_seed"]),
                trial_count=int(payload.get("trial_count", 1)),
            )
        else:
            for trade in trades:
                trade.split = "EXPLORATORY_UNVALIDATED"
            validation = {
                "enabled": False,
                "boundaries": None,
                "metrics": {},
                "confidenceIntervals": None,
                "psr": None,
                "dsr": None,
                "trialCount": int(payload.get("trial_count", 1)),
                "pbo": None,
                "pboReason": "validation_disabled_for_exploratory_run",
            }
        trade_rows = [trade.to_dict() for trade in trades]
        metrics = summarize_r(
            [trade.net_r for trade in trades],
            [trade.cost_r for trade in trades],
        )
        current, peak = tracemalloc.get_traced_memory()
        return {
            "success": True,
            "task": task,
            "metrics": metrics,
            "validation": validation,
            "funnel": funnel,
            "simulation": simulation,
            "trades": trade_rows,
            "timing": {"computeSec": round(time.perf_counter() - started, 4), "dataAcquisitionSec": 0.0, "jitCompilationSec": 0.0},
            "peakMemoryBytes": int(peak),
            "configSnapshotId": payload["config_snapshot"].get("snapshot_id"),
        }
    except Exception as exc:
        _current, peak = tracemalloc.get_traced_memory()
        return {
            "success": False,
            "excluded": False,
            "code": "TASK_FAILED",
            "error": str(exc),
            "task": payload.get("task"),
            "durationSec": round(time.perf_counter() - started, 4),
            "peakMemoryBytes": int(peak),
        }
    finally:
        tracemalloc.stop()
