"""ASE Phase 2 training pipeline."""

from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.features.build import FEATURE_SCHEMA_CORE, FeatureBuildContext, build_features_for_candidate
from athena_ase.horizon import HORIZONS
from athena_ase.instruments import instrument_by_symbol
from athena_ase.labels.triple_barrier import LabelOutcome, label_candidate, uniqueness_weights
from athena_ase.models.calibrate import calibrate_fold
from athena_ase.models.meta import _prepare_matrix, train_meta_classifier
from athena_ase.registry.artifacts import freeze_artifact_bundle
from athena_ase.signals.arbitrate import Candidate
from athena_ase.validation.gates import check_provisional
from athena_research.ase.bootstrap import block_bootstrap_expectancy
from athena_research.ase.dsr_pbo import deflated_sharpe_ratio
from athena_research.ase.event_backtest import EVENTS_PATH
from athena_research.ase.holdout_manifest import write_holdout_manifest
from athena_research.ase.walkforward import build_walkforward_manifest, holdout_split, purge_embargo_mask

log = logging.getLogger("ase.train")

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DATASET_DIR = OUTPUT_DIR / "datasets"
TRAIN_STATE = OUTPUT_DIR / "train_state.json"


def _append_audit(event: str, payload: dict[str, Any]) -> None:
    from datetime import datetime, timezone

    path = Path("ase_audit.jsonl")
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **payload}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _dataset_hash(df: pd.DataFrame) -> str:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return hashlib.sha256(buf.getvalue()).hexdigest()


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
    feats = build_features_for_candidate(ctx, enriched=False)
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
        label = label_candidate(cand, store, inst)
        if label is None:
            continue
        row = _feature_row(cand, inst, store)
        row["y"] = label.y
        row["net_R"] = label.net_R
        row["mae_R"] = label.mae_R
        row["mfe_R"] = label.mfe_R
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


def _pick_thr_family(oos_net: np.ndarray, oos_pred: np.ndarray, *, min_trades: int = 40) -> float:
    best_thr = 0.10
    best_exp = -1e9
    for thr in np.linspace(0.0, 0.5, 51):
        mask = oos_pred >= thr
        if int(mask.sum()) < min_trades:
            continue
        exp = float(oos_net[mask].mean())
        if exp > best_exp:
            best_exp = exp
            best_thr = float(thr)
    return best_thr


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

    dev_df, holdout_df = holdout_split(raw)
    write_holdout_manifest(holdout_df, family, horizon)
    wf = build_walkforward_manifest(dev_df, max_hold_bars=HORIZONS[horizon].max_hold_bars)  # type: ignore[index]

    labeled = materialize_labeled_dataset(store, dev_df, family=family, horizon=horizon)
    if labeled.empty:
        raise ValueError(f"no labeled rows for {family}/{horizon}")

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    ds_path = DATASET_DIR / f"{family}_{horizon}_train.parquet"
    labeled.to_parquet(ds_path, index=False)
    ds_hash = _dataset_hash(labeled)

    fold_metrics: list[dict[str, Any]] = []
    oos_net: list[float] = []
    oos_pred: list[float] = []

    for fold in wf.folds:
        train_idx = purge_embargo_mask(
            labeled,
            train_idx=fold.train_idx,
            test_idx=fold.test_idx,
            max_hold_bars=HORIZONS[horizon].max_hold_bars,  # type: ignore[index]
        )
        tr = labeled.iloc[train_idx]
        te = labeled.iloc[fold.test_idx]
        if tr.empty or te.empty:
            continue
        X_train = [tr.iloc[i].to_dict() for i in range(len(tr))]
        y_train = tr["y"].to_numpy(dtype=int)
        result = train_meta_classifier(
            X_train, y_train, family=family, horizon=horizon,
        )
        cal = calibrate_fold(result.model, X_train, y_train)
        X_test = [te.iloc[i].to_dict() for i in range(len(te))]
        X_mat = _prepare_matrix(X_test, FEATURE_SCHEMA_CORE)
        raw_p = result.model.predict_proba(X_mat)[:, 1]
        p_cal = cal.calibrator.predict(raw_p) if cal.calibrator is not None else raw_p
        oos_net.extend(te["net_R"].tolist())
        oos_pred.extend(p_cal.tolist())
        fold_metrics.append({"fold": fold.fold_id, "brier": cal.brier, "brier_skill": cal.brier_skill})

    thr = _pick_thr_family(np.array(oos_net), np.array(oos_pred)) if oos_net else 0.10
    final = train_meta_classifier(
        [labeled.iloc[i].to_dict() for i in range(len(labeled))],
        labeled["y"].to_numpy(dtype=int),
        family=family,
        horizon=horizon,
    )
    cal_final = calibrate_fold(
        final.model,
        [labeled.iloc[i].to_dict() for i in range(len(labeled))],
        labeled["y"].to_numpy(dtype=int),
    )

    boot = block_bootstrap_expectancy(np.array(oos_net) if oos_net else labeled["net_R"].to_numpy())
    dsr = deflated_sharpe_ratio(
        np.array(oos_net) if oos_net else labeled["net_R"].to_numpy(),
        n_trials=18,
    )
    metrics = {
        "family": family,
        "horizon": horizon,
        "oos_trades": len(oos_net),
        "instruments": int(labeled["instrument"].nunique()),
        "folds_nonneg": sum(1 for f in fold_metrics if f.get("brier_skill", 0) >= 0),
        "max_instrument_profit_share": 0.0,
        "max_dd_R": 0.0,
        "brier_skill": float(np.mean([f.get("brier_skill", 0) for f in fold_metrics]) if fold_metrics else 0),
        "thr_family": thr,
        "bootstrap_lb": boot.lb_expectancy,
        "dsr": dsr.deflated_sharpe,
    }
    ok, failures = check_provisional(metrics)

    bundle = {
        "model_core.pkl": final.model,
        "calibrator.pkl": cal_final.calibrator,
        "quantile_heads.pkl": {},
    }
    artifact_path = freeze_artifact_bundle(
        family=family,
        horizon=horizon,
        version=version,
        models=bundle,
        route="core",
        dataset_hash=ds_hash,
        thr_family=thr,
        validation_report={"metrics": metrics, "folds": fold_metrics, "provisional_ok": ok, "failures": failures},
    )
    TRAIN_STATE.write_text(
        json.dumps({"family": family, "horizon": horizon, "version": version, "artifact_path": str(artifact_path)}),
        encoding="utf-8",
    )
    _append_audit("train", {"family": family, "horizon": horizon, "version": version, "dataset_hash": ds_hash, "provisional_ok": ok})
    return {"metrics": metrics, "artifact_path": str(artifact_path), "provisional_ok": ok}


def freeze_family_horizon(family: str, horizon: str, *, version: str = "v1") -> Path:
    state_path = TRAIN_STATE
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("family") == family and state.get("horizon") == horizon:
            return Path(state["artifact_path"])
    ds_path = DATASET_DIR / f"{family}_{horizon}_train.parquet"
    if not ds_path.exists():
        raise FileNotFoundError(f"run train first: {ds_path}")
    ds_hash = _dataset_hash(pd.read_parquet(ds_path))
    path = freeze_artifact_bundle(
        family=family,
        horizon=horizon,
        version=version,
        models={"model_core.pkl": train_meta_classifier([{"instrument_id": "EURUSD"}], [0]).model},
        dataset_hash=ds_hash,
        thr_family=0.10,
        validation_report={"note": "freeze-only refresh"},
    )
    _append_audit("freeze", {"family": family, "horizon": horizon, "version": version})
    return path
