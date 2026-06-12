"""ASE Phase 2 training pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.features.build import (
    FEATURE_SCHEMA_CORE,
    FEATURE_SCHEMA_ENRICHED,
    FeatureBuildContext,
    build_features_for_candidate,
)
from athena_ase.instruments import instrument_by_symbol
from athena_ase.labels.triple_barrier import LabelOutcome, label_candidate, uniqueness_weights
from athena_ase.models.calibrate import (
    brier_score,
    brier_skill,
    chronological_calibration_split,
    fit_isotonic,
)
from athena_ase.models.meta import BASE_HGB_PARAMS, _prepare_matrix, train_meta_classifier
from athena_ase.models.quantile_heads import (
    QUANTILE_HGB_PARAMS,
    TARGETS,
    predict_quantile_heads,
    train_quantile_heads,
)
from athena_ase.registry.artifacts import freeze_artifact_bundle
from athena_ase.signals.arbitrate import Candidate
from athena_research.ase.bootstrap import block_bootstrap_expectancy
from athena_research.ase.dsr_pbo import deflated_sharpe_ratio
from athena_research.ase.event_backtest import EVENTS_PATH
from athena_research.ase.training_report import write_training_report

log = logging.getLogger("ase.train")

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DATASET_DIR = OUTPUT_DIR / "datasets"
TRAIN_STATE = OUTPUT_DIR / "train_state.json"
MODEL_FAMILIES: tuple[str, ...] = ("forex", "crypto", "commodity", "equity", "index_etf")
MODEL_HORIZONS: tuple[str, ...] = ("intraday", "swing")
MIN_TRAIN_CANDIDATES = 500
MIN_ENRICHED_CANDIDATES = 250


def _append_audit(event: str, payload: dict[str, Any]) -> None:
    from datetime import datetime, timezone

    path = Path("ase_audit.jsonl")
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **payload}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _feature_row(cand: Candidate, inst, store: PTISStore) -> dict[str, Any]:
    ctx = FeatureBuildContext(
        symbol=inst.symbol,
        family=inst.family,
        subclass=inst.subclass,
        horizon=cand.horizon,
        decision_time_ms=cand.decision_time_ms,
        direction=cand.direction,
        agreement_count=cand.agreement_count,
        conflict_flag=cand.conflict_flag,
        benchmark_symbol=inst.benchmark,
        sig_tsmom=next((s["rawStrength"] * s["direction"] for s in cand.signals if s.get("name") == "tsmom"), 0.0),
        sig_carry=next((s["rawStrength"] * s["direction"] for s in cand.signals if s.get("name") == "carry"), 0.0),
        sig_xsec=next((s["rawStrength"] * s["direction"] for s in cand.signals if s.get("name") == "xsec"), 0.0),
        sig_mr=next((s["rawStrength"] * s["direction"] for s in cand.signals if s.get("name") == "meanrev"), 0.0),
    )
    feats = build_features_for_candidate(ctx, enriched=True)
    feats["decision_time_ms"] = cand.decision_time_ms
    feats["horizon"] = cand.horizon
    return feats


def materialize_labeled_dataset(store: PTISStore, df: pd.DataFrame, *, family: str, horizon: str) -> pd.DataFrame:
    import json as _json

    rows: list[dict[str, Any]] = []
    labels: list[LabelOutcome] = []
    for rec in df.to_dict(orient="records"):
        inst = instrument_by_symbol(str(rec["instrument"]))
        if inst is None or inst.family != family:
            continue
        signals = _json.loads(rec.get("primary_signals") or "[]")
        cand = Candidate(
            instrument=str(rec["instrument"]),
            family=family,
            horizon=horizon,  # type: ignore[arg-type]
            decision_time_ms=int(rec["decision_time_ms"]),
            bar_index=int(rec.get("bar_index", 0)),
            direction=int(rec["direction"]),
            signals=signals,
            agreement_count=int(rec.get("agreement_count", 0)),
            conflict_flag=bool(rec.get("conflict_flag", False)),
            sigma_bar=float(rec["sigma_bar"]),
            entry_log=float(rec["entry_log"]),
        )
        has_phase1_label = all(
            key in rec and pd.notna(rec[key])
            for key in ("net_R", "mae_R", "mfe_R", "hold_bars")
        )
        if has_phase1_label:
            hold_bars = int(rec["hold_bars"])
            bar_ms = 3_600_000 if horizon == "intraday" else 86_400_000
            start_ms = int(rec["decision_time_ms"])
            label = LabelOutcome(
                instrument=inst.symbol,
                family=family,
                horizon=horizon,
                decision_time_ms=start_ms,
                direction=int(rec["direction"]),
                entry_log=float(rec["entry_log"]),
                sigma_bar=float(rec["sigma_bar"]),
                gross_R=float(rec.get("gross_R", rec["net_R"])),
                cost_R=float(rec.get("cost_R", 0.0)),
                net_R=float(rec["net_R"]),
                mae_R=float(rec["mae_R"]),
                mfe_R=float(rec["mfe_R"]),
                hold_bars=hold_bars,
                exit_reason=str(rec.get("exit_reason", "phase1")),
                y=1 if float(rec["net_R"]) > 0 else 0,
                agreement_count=int(rec.get("agreement_count", 0)),
                conflict_flag=bool(rec.get("conflict_flag", False)),
                primary_signals=str(rec.get("primary_signals") or "[]"),
                label_start_ms=start_ms,
                label_end_ms=start_ms + hold_bars * bar_ms,
            )
        else:
            label = label_candidate(cand, store, inst)
        if label is None:
            continue
        row = _feature_row(cand, inst, store)
        row["y"] = label.y
        row["net_R"] = label.net_R
        row["mae_R"] = label.mae_R
        row["mfe_R"] = label.mfe_R
        row["MAE_R"] = label.mae_R
        row["MFE_R"] = label.mfe_R
        row["hold_bars"] = label.hold_bars
        row["instrument"] = label.instrument
        row["label_start_ms"] = label.label_start_ms
        row["label_end_ms"] = label.label_end_ms
        rows.append(row)
        labels.append(label)
    out = pd.DataFrame(rows)
    if not out.empty:
        out["sample_weight"] = uniqueness_weights(labels)
    return out


def chronological_train_eval_split(
    frame: pd.DataFrame,
    *,
    train_fraction: float = 0.80,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        raise ValueError("cannot split an empty dataset")
    if len(frame) < 2:
        raise ValueError("chronological split requires at least two rows")
    if "decision_time_ms" not in frame.columns:
        raise ValueError("decision_time_ms is required for chronological split")
    ordered = frame.sort_values("decision_time_ms", kind="stable").reset_index(drop=True)
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction)))
    return ordered.iloc[:cut].copy(), ordered.iloc[cut:].copy()


def enriched_training_mask(frame: pd.DataFrame, family: str) -> pd.Series:
    required_by_family = {
        "forex": ("cot_pct", "cot_delta_4w"),
        "commodity": ("cot_pct", "cot_delta_4w"),
        "crypto": ("funding_z", "oi_delta_z"),
    }
    required = required_by_family.get(family)
    if required is None or any(column not in frame.columns for column in required):
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame.loc[:, list(required)].notna().all(axis=1)


@dataclass(frozen=True)
class ThresholdSelection:
    threshold: float
    fallback: bool
    retained_trades: int
    expectancy: float


def select_expected_net_r_threshold(
    expected_net_r: Sequence[float],
    realized_net_r: Sequence[float],
    *,
    min_trades: int = 40,
) -> ThresholdSelection:
    expected = np.asarray(expected_net_r, dtype=float)
    realized = np.asarray(realized_net_r, dtype=float)
    if len(expected) != len(realized):
        raise ValueError("threshold inputs must have equal length")
    finite = np.isfinite(expected) & np.isfinite(realized)
    expected = expected[finite]
    realized = realized[finite]
    if len(expected) < min_trades:
        mask = expected >= 0.10
        expectancy = float(realized[mask].mean()) if np.any(mask) else 0.0
        return ThresholdSelection(0.10, True, int(mask.sum()), expectancy)

    best: ThresholdSelection | None = None
    for threshold in np.unique(expected):
        mask = expected >= threshold
        retained = int(mask.sum())
        if retained < min_trades:
            continue
        expectancy = float(realized[mask].mean())
        candidate = ThresholdSelection(float(threshold), False, retained, expectancy)
        if best is None or (candidate.expectancy, candidate.threshold) > (
            best.expectancy,
            best.threshold,
        ):
            best = candidate
    if best is None:
        return ThresholdSelection(0.10, True, int((expected >= 0.10).sum()), 0.0)
    return best


def _positive_probability(model: Any, matrix: np.ndarray) -> np.ndarray:
    probabilities = np.asarray(model.predict_proba(matrix), dtype=float)
    if probabilities.shape[1] != 2:
        raise ValueError("classifier did not produce two-class probabilities")
    return probabilities[:, 1]


def _fit_calibrated_classifier(
    training: pd.DataFrame,
    *,
    feature_names: Sequence[str],
    family: str,
    horizon: str,
    log_trials: bool,
) -> tuple[Any, Any, dict[str, float]]:
    fit_idx, calibration_idx = chronological_calibration_split(len(training))
    fit_frame = training.iloc[fit_idx]
    calibration_frame = training.iloc[calibration_idx]
    result = train_meta_classifier(
        fit_frame.to_dict(orient="records"),
        fit_frame["y"].to_numpy(dtype=int),
        sample_weight=fit_frame["sample_weight"].to_numpy(dtype=float),
        feature_names=feature_names,
        family=family,
        horizon=horizon,
        log_trials=log_trials,
    )
    calibration_matrix = _prepare_matrix(
        calibration_frame.to_dict(orient="records"),
        feature_names,
    )
    raw_probability = _positive_probability(result.model, calibration_matrix)
    calibrator = fit_isotonic(
        raw_probability,
        calibration_frame["y"].to_numpy(dtype=int),
    )
    calibrated = calibrator.predict(raw_probability)
    y_calibration = calibration_frame["y"].to_numpy(dtype=int)
    return result, calibrator, {
        "rows": int(len(calibration_frame)),
        "brier": brier_score(calibrated, y_calibration),
        "brier_skill": brier_skill(calibrated, y_calibration),
    }


def _expected_net_r(
    probability: np.ndarray,
    quantiles: dict[str, list[dict[str, float]]],
) -> np.ndarray:
    mfe = np.asarray([row["q50"] for row in quantiles["MFE_R"]], dtype=float)
    mae = np.asarray([row["q50"] for row in quantiles["MAE_R"]], dtype=float)
    expected_win = np.minimum(mfe, 1.0)
    expected_loss = -np.maximum(mae, 0.25)
    return probability * expected_win + (1.0 - probability) * expected_loss


def train_family_horizon(
    family: str,
    horizon: str,
    *,
    store: PTISStore | None = None,
    events_path: Path | None = None,
    version: str = "v1",
) -> dict[str, Any]:
    store = store or PTISStore(default_ptis_root())
    src = events_path or EVENTS_PATH
    if not src.exists():
        raise FileNotFoundError(f"phase1 events missing: {src}")
    raw = pd.read_parquet(src)
    raw = raw[(raw["family"] == family) & (raw["horizon"] == horizon)].copy()
    if raw.empty:
        raise ValueError(f"no events for {family}/{horizon}")

    labeled = materialize_labeled_dataset(store, raw, family=family, horizon=horizon)
    if len(labeled) < MIN_TRAIN_CANDIDATES:
        raise ValueError(
            f"insufficient candidates for {family}/{horizon}: "
            f"{len(labeled)} < {MIN_TRAIN_CANDIDATES}"
        )
    train_frame, eval_frame = chronological_train_eval_split(labeled)

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    ds_path = DATASET_DIR / f"{family}_{horizon}_train.parquet"
    labeled.to_parquet(ds_path, index=False)
    ds_hash = hashlib.sha256(ds_path.read_bytes()).hexdigest()

    core_result, core_calibrator, calibration_summary = _fit_calibrated_classifier(
        train_frame,
        feature_names=FEATURE_SCHEMA_CORE,
        family=family,
        horizon=horizon,
        log_trials=True,
    )
    quantile_heads = train_quantile_heads(
        train_frame.to_dict(orient="records"),
        {target: train_frame[target].to_numpy(dtype=float) for target in TARGETS},
        sample_weight=train_frame["sample_weight"].to_numpy(dtype=float),
        feature_names=FEATURE_SCHEMA_CORE,
    )
    eval_matrix = _prepare_matrix(
        eval_frame.to_dict(orient="records"),
        FEATURE_SCHEMA_CORE,
    )
    eval_raw_probability = _positive_probability(core_result.model, eval_matrix)
    eval_probability = core_calibrator.predict(eval_raw_probability)
    eval_quantiles = predict_quantile_heads(quantile_heads, eval_matrix)
    eval_expected_net_r = _expected_net_r(eval_probability, eval_quantiles)
    threshold = select_expected_net_r_threshold(
        eval_expected_net_r,
        eval_frame["net_R"].to_numpy(dtype=float),
    )
    selected = eval_expected_net_r >= threshold.threshold
    selected_net_r = eval_frame["net_R"].to_numpy(dtype=float)[selected]
    eval_y = eval_frame["y"].to_numpy(dtype=int)

    boot_source = selected_net_r if len(selected_net_r) else eval_frame["net_R"].to_numpy(dtype=float)
    boot = block_bootstrap_expectancy(boot_source)
    dsr = deflated_sharpe_ratio(boot_source, n_trials=1)
    eval_summary = {
        "expectancy": float(selected_net_r.mean()) if len(selected_net_r) else 0.0,
        "win_rate": float((selected_net_r > 0).mean()) if len(selected_net_r) else 0.0,
        "brier": brier_score(eval_probability, eval_y),
        "brier_skill": brier_skill(eval_probability, eval_y),
        "trade_count": int(selected.sum()),
        "eval_rows": int(len(eval_frame)),
        "threshold": threshold.threshold,
        "threshold_fallback": threshold.fallback,
    }

    models: dict[str, Any] = {
        "model_core.pkl": core_result.model,
        "calibrator.pkl": core_calibrator,
        "quantile_heads.pkl": quantile_heads,
    }
    enriched_rows = train_frame.loc[enriched_training_mask(train_frame, family)].copy()
    enriched_summary: dict[str, Any] = {
        "usable": False,
        "rows": int(len(enriched_rows)),
        "reason": "family_has_no_verified_enriched_route",
    }
    if len(enriched_rows) >= MIN_ENRICHED_CANDIDATES:
        enriched_result, enriched_calibrator, enriched_calibration = _fit_calibrated_classifier(
            enriched_rows,
            feature_names=FEATURE_SCHEMA_ENRICHED,
            family=family,
            horizon=horizon,
            log_trials=False,
        )
        enriched_quantiles = train_quantile_heads(
            enriched_rows.to_dict(orient="records"),
            {target: enriched_rows[target].to_numpy(dtype=float) for target in TARGETS},
            sample_weight=enriched_rows["sample_weight"].to_numpy(dtype=float),
            feature_names=FEATURE_SCHEMA_ENRICHED,
        )
        models.update(
            {
                "model_enriched.pkl": enriched_result.model,
                "calibrator_enriched.pkl": enriched_calibrator,
                "quantile_heads_enriched.pkl": enriched_quantiles,
            }
        )
        enriched_summary = {
            "usable": True,
            "rows": int(len(enriched_rows)),
            "calibration": enriched_calibration,
        }
    elif family in {"forex", "commodity", "crypto"}:
        enriched_summary["reason"] = "insufficient_verified_enriched_rows"

    metrics = {
        "family": family,
        "horizon": horizon,
        "training_rows": int(len(train_frame)),
        "eval_rows": int(len(eval_frame)),
        "eval_trades": eval_summary["trade_count"],
        "eval_expectancy": eval_summary["expectancy"],
        "eval_win_rate": eval_summary["win_rate"],
        "eval_brier": eval_summary["brier"],
        "eval_brier_skill": eval_summary["brier_skill"],
        "instruments": int(labeled["instrument"].nunique()),
        "thr_family": threshold.threshold,
        "threshold_fallback": threshold.fallback,
        "calibration": calibration_summary,
        "enriched": enriched_summary,
        "bootstrap_lb": boot.lb_expectancy,
        "dsr": dsr.deflated_sharpe,
    }
    artifact_path = freeze_artifact_bundle(
        family=family,
        horizon=horizon,
        version=version,
        models=models,
        route="core",
        dataset_hash=ds_hash,
        thr_family=threshold.threshold,
        threshold_fallback=threshold.fallback,
        eval_summary=eval_summary,
        model_params={
            "classifier": BASE_HGB_PARAMS,
            "quantile_heads": QUANTILE_HGB_PARAMS,
        },
        validation_report={
            "metrics": metrics,
            "calibration": calibration_summary,
            "enriched": enriched_summary,
        },
    )
    TRAIN_STATE.write_text(
        json.dumps({"family": family, "horizon": horizon, "version": version, "artifact_path": str(artifact_path)}),
        encoding="utf-8",
    )
    _append_audit(
        "train",
        {
            "family": family,
            "horizon": horizon,
            "version": version,
            "dataset_hash": ds_hash,
            "eval_expectancy": eval_summary["expectancy"],
            "threshold_fallback": threshold.fallback,
        },
    )
    return {
        "family": family,
        "horizon": horizon,
        "metrics": metrics,
        "artifact_path": str(artifact_path),
        "trained": True,
    }


def train_all(
    *,
    version: str = "v1",
    events_path: Path | None = None,
    report_path: Path | None = None,
    trainer: Callable[..., dict[str, Any]] = train_family_horizon,
) -> dict[str, Any]:
    trained: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for family in MODEL_FAMILIES:
        for horizon in MODEL_HORIZONS:
            try:
                trained.append(
                    trainer(
                        family,
                        horizon,
                        events_path=events_path,
                        version=version,
                    )
                )
            except Exception as exc:
                log.exception("ASE training failed for %s/%s", family, horizon)
                failed.append(
                    {
                        "family": family,
                        "horizon": horizon,
                        "reason": str(exc),
                    }
                )
    output = {
        "version": version,
        "trained": trained,
        "failed": failed,
    }
    output["report_path"] = str(
        write_training_report(
            output,
            report_path or Path("reports") / "ase_training_report.md",
        )
    )
    return output


def freeze_family_horizon(family: str, horizon: str, *, version: str = "v1") -> Path:
    state_path = TRAIN_STATE
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("family") == family and state.get("horizon") == horizon:
            return Path(state["artifact_path"])
    raise FileNotFoundError(
        f"trained artifact state missing for {family}/{horizon}; run train first"
    )
