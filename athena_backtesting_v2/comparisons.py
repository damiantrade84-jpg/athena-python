from __future__ import annotations

from typing import Any

from .hashing import content_hash
from .statistics import block_bootstrap_ci, deflated_sharpe_ratio, summarize_r


def compatibility_contract(run: dict[str, Any]) -> dict[str, Any]:
    request = run.get("request") or {}
    result = run.get("result") or {}
    return {
        "datasetId": run.get("dataset_id") or result.get("datasetId"),
        "startDate": request.get("start_date"),
        "endDate": request.get("end_date"),
        "costModel": request.get("cost_model"),
        "certification": run.get("certification") or result.get("certification"),
    }


def incompatibility_reasons(runs: list[dict[str, Any]]) -> list[str]:
    if not runs:
        return ["no_runs"]
    baseline = compatibility_contract(runs[0])
    reasons: set[str] = set()
    for run in runs[1:]:
        current = compatibility_contract(run)
        for key, label in (
            ("datasetId", "dataset_mismatch"),
            ("startDate", "start_date_mismatch"),
            ("endDate", "end_date_mismatch"),
            ("costModel", "cost_model_mismatch"),
            ("certification", "certification_mismatch"),
        ):
            if current.get(key) != baseline.get(key):
                reasons.add(label)
    return sorted(reasons)


def probability_backtest_overfitting(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(runs) < 2:
        return None
    fold_rows: list[dict[str, float]] = []
    for fold in range(1, 6):
        values: dict[str, float] = {}
        for run in runs:
            task_metrics = []
            for task in (run.get("result") or {}).get("tasks", []):
                metric = (((task.get("validation") or {}).get("metrics") or {}).get(f"WF{fold}_OOS") or {}).get("meanR")
                if metric is not None:
                    task_metrics.append(float(metric))
            if task_metrics:
                values[str(run["run_id"])] = sum(task_metrics) / len(task_metrics)
        if len(values) >= 2:
            fold_rows.append(values)
    if not fold_rows:
        return None
    overall = {
        str(run["run_id"]): float((((run.get("result") or {}).get("engines") or {}).get(next(iter((run.get("result") or {}).get("engines") or {"": {}})), {}).get("metrics") or {}).get("meanR", 0.0))
        for run in runs
    }
    selected_id = max(overall, key=overall.get)
    below_median = 0
    for fold in fold_rows:
        ordered = sorted(fold.values())
        median = ordered[len(ordered) // 2]
        if fold.get(selected_id, float("-inf")) < median:
            below_median += 1
    return {
        "value": below_median / len(fold_rows),
        "folds": len(fold_rows),
        "selectedVariantRunId": selected_id,
        "method": "anchored_oos_rank_logit_proxy",
    }


def build_comparison(runs: list[dict[str, Any]], trades_by_run: dict[str, list[dict[str, Any]]], *, seed: int) -> dict[str, Any]:
    reasons = incompatibility_reasons(runs)
    if reasons:
        return {"compatible": False, "incompatibilityReasons": reasons, "runs": [run["run_id"] for run in runs]}
    trial_count = len(runs)
    rows = []
    baseline_metrics: dict[str, Any] | None = None
    for index, run in enumerate(runs):
        trades = trades_by_run.get(str(run["run_id"]), [])
        values = [float(trade.get("net_r") or 0.0) for trade in trades]
        costs = [float(trade.get("cost_r") or 0.0) for trade in trades]
        metrics = summarize_r(values, costs)
        if index == 0:
            baseline_metrics = metrics
        deltas = {
            key: float(metrics.get(key, 0.0)) - float((baseline_metrics or {}).get(key, 0.0))
            for key in ("meanR", "totalR", "winRate", "profitFactor", "maxDrawdownR")
            if metrics.get(key) not in (float("inf"), float("-inf"))
            and (baseline_metrics or {}).get(key) not in (float("inf"), float("-inf"))
        }
        rows.append(
            {
                "runId": run["run_id"],
                "variantId": (run.get("request") or {}).get("variant_id"),
                "metrics": metrics,
                "deltasFromBaseline": deltas,
                "confidenceIntervals": block_bootstrap_ci(values, samples=1_000, seed=seed + index),
                "dsr": deflated_sharpe_ratio(values, trial_count),
            }
        )
    contract = compatibility_contract(runs[0])
    return {
        "compatible": True,
        "incompatibilityReasons": [],
        "compatibilityContract": contract,
        "compatibilityHash": content_hash(contract),
        "trialCount": trial_count,
        "runs": rows,
        "pbo": probability_backtest_overfitting(runs),
    }

