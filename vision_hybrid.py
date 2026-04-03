"""Hybrid chart-vision training and inference (advisory-only)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from vision_data import VisionSample, fetch_training_rows


_ASSET_TYPES = ["forex", "crypto", "stock", "commodity", "index", "unknown"]
_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "UNKNOWN"]
_DIRECTIONS = ["LONG", "SHORT", "UNKNOWN"]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _one_hot(value: str, choices: list[str]) -> np.ndarray:
    out = np.zeros((len(choices),), dtype=np.float32)
    norm = str(value or "").strip().upper()
    if not norm:
        norm = choices[-1]
    for i, c in enumerate(choices):
        if norm == c:
            out[i] = 1.0
            break
    return out


def _load_image_array(path: str) -> np.ndarray:
    if not path or not os.path.exists(path):
        return np.zeros((32, 32), dtype=np.float32)
    try:
        import matplotlib.image as mpimg

        arr = mpimg.imread(path)
    except Exception:
        return np.zeros((32, 32), dtype=np.float32)
    if arr is None:
        return np.zeros((32, 32), dtype=np.float32)
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        arr = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    elif arr.ndim == 3:
        arr = arr[:, :, 0]
    if arr.ndim != 2:
        return np.zeros((32, 32), dtype=np.float32)
    if arr.max(initial=0.0) > 1.0:
        arr = arr / 255.0
    # Downsample to fixed grid by linear indexing for deterministic features.
    h, w = arr.shape
    yi = np.linspace(0, max(h - 1, 0), 32).astype(int)
    xi = np.linspace(0, max(w - 1, 0), 32).astype(int)
    return arr[np.ix_(yi, xi)].astype(np.float32)


def _image_features(image_path: str) -> np.ndarray:
    arr = _load_image_array(image_path)
    hist, _ = np.histogram(arr, bins=16, range=(0.0, 1.0), density=False)
    hist = hist.astype(np.float32)
    total = float(np.sum(hist))
    if total > 0:
        hist = hist / total
    gx = np.diff(arr, axis=1, prepend=arr[:, :1])
    gy = np.diff(arr, axis=0, prepend=arr[:1, :])
    grad = np.sqrt(gx * gx + gy * gy)
    stats = np.array(
        [
            float(arr.mean()),
            float(arr.std()),
            float(np.percentile(arr, 10)),
            float(np.percentile(arr, 50)),
            float(np.percentile(arr, 90)),
            float(grad.mean()),
            float(grad.std()),
        ],
        dtype=np.float32,
    )
    return np.concatenate([hist, stats], dtype=np.float32)


def _structure_features(row: dict[str, Any]) -> np.ndarray:
    model_context = row.get("model_context") or {}
    engine_b = row.get("engine_b") or {}
    structured = row.get("structured") or {}
    asset = str(row.get("asset_type") or "unknown").strip().lower()
    timeframe = str(row.get("timeframe") or "UNKNOWN").strip().upper()
    tf_role = str(row.get("tf_role") or timeframe or "UNKNOWN").strip().upper()
    direction = str(model_context.get("direction") or "UNKNOWN").strip().upper()
    right_edge = str(structured.get("right_edge_status") or "REVIEW").strip().upper()
    tf_alignment = str(structured.get("tf_alignment") or "CONFLICTED").strip().upper()
    style = structured.get("style_ratings") or {}

    obs = engine_b.get("order_blocks") or []
    fvgs = [f for f in (engine_b.get("active_fvgs") or []) if not f.get("mitigated")]
    bos = engine_b.get("bos_data") or {}
    choch = engine_b.get("choch_data") or {}

    numeric = np.array(
        [
            1.0 if bool(engine_b.get("bos_mtf_confirmed")) else 0.0,
            1.0 if bool(choch.get("choch_bull")) else 0.0,
            1.0 if bool(choch.get("choch_bear")) else 0.0,
            1.0 if bool(bos.get("bos_bull")) else 0.0,
            1.0 if bool(bos.get("bos_bear")) else 0.0,
            float(len(obs)),
            float(len(fvgs)),
            _safe_float(model_context.get("entry_price"), 0.0),
            _safe_float(model_context.get("sl"), 0.0),
            _safe_float(model_context.get("tp1"), 0.0),
            _safe_float(model_context.get("rr1"), 0.0),
            1.0 if right_edge == "CONFIRMS" else 0.0,
            1.0 if right_edge == "POTENTIAL_REVERSAL" else 0.0,
            1.0 if tf_alignment == "ALIGNED" else 0.0,
            1.0 if str(style.get("scalp", "")).upper() == "STRONG" else 0.0,
            1.0 if str(style.get("intraday", "")).upper() == "STRONG" else 0.0,
            1.0 if str(style.get("swing", "")).upper() == "STRONG" else 0.0,
        ],
        dtype=np.float32,
    )
    return np.concatenate(
        [
            _one_hot(asset, [a.upper() for a in _ASSET_TYPES]),
            _one_hot(timeframe, _TIMEFRAMES),
            _one_hot(tf_role, _TIMEFRAMES),
            _one_hot(direction, _DIRECTIONS),
            numeric,
        ],
        dtype=np.float32,
    )


def _fusion_features(row: dict[str, Any]) -> tuple[np.ndarray, dict[str, int]]:
    img_feat = _image_features(str(row.get("image_path") or ""))
    struct_feat = _structure_features(row)
    fused = np.concatenate([img_feat, struct_feat], dtype=np.float32)
    return fused, {"image_dim": int(len(img_feat)), "struct_dim": int(len(struct_feat))}


def _task_labels(row: dict[str, Any]) -> dict[str, str]:
    levels = row.get("label_levels") or {}

    def _action(style_name: str) -> str:
        rec = levels.get(style_name) if isinstance(levels, dict) else None
        if isinstance(rec, dict):
            action = str(rec.get("action") or "").strip().upper()
            if action in ("KEEP", "ADJUST"):
                return action
            if bool(rec.get("changed")):
                return "ADJUST"
        return "KEEP"

    return {
        "right_edge_status": str(row.get("label_right_edge_status") or "REVIEW").upper(),
        "scalp_rating": str(row.get("label_scalp_rating") or "MODERATE").upper(),
        "intraday_rating": str(row.get("label_intraday_rating") or "MODERATE").upper(),
        "swing_rating": str(row.get("label_swing_rating") or "MODERATE").upper(),
        "level_action_scalp": _action("scalp"),
        "level_action_intraday": _action("intraday"),
        "level_action_swing": _action("swing"),
    }


def _temporal_split(n_rows: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    idx = np.arange(n_rows, dtype=int)
    if n_rows < 5:
        return idx, np.array([], dtype=int), np.array([], dtype=int)
    train_end = max(1, int(n_rows * 0.70))
    val_end = max(train_end + 1, int(n_rows * 0.85))
    val_end = min(val_end, n_rows)
    return idx[:train_end], idx[train_end:val_end], idx[val_end:]


def _group_weights(rows: list[dict[str, Any]]) -> np.ndarray:
    keys = [
        (
            str(r.get("asset_type") or "unknown"),
            str(r.get("tf_role") or r.get("timeframe") or "UNKNOWN"),
        )
        for r in rows
    ]
    freq: dict[tuple[str, str], int] = {}
    for key in keys:
        freq[key] = freq.get(key, 0) + 1
    out = np.ones((len(rows),), dtype=np.float32)
    for i, key in enumerate(keys):
        out[i] = 1.0 / max(1, freq[key])
    out = out / max(1e-9, float(np.mean(out)))
    return out.astype(np.float32)


def _train_weighted_centroids(
    x: np.ndarray, y: np.ndarray, sample_weight: np.ndarray
) -> dict[str, Any]:
    labels = sorted({str(v) for v in y.tolist() if str(v)})
    if not labels:
        return {"classes": [], "centroids": []}
    centroids: list[list[float]] = []
    for cls in labels:
        mask = y == cls
        if not np.any(mask):
            continue
        w = sample_weight[mask]
        x_cls = x[mask]
        cls_freq = float(np.sum(mask))
        class_weight = 1.0 / max(1.0, cls_freq)
        w_eff = w * class_weight
        w_sum = float(np.sum(w_eff))
        if w_sum <= 0:
            centroid = np.mean(x_cls, axis=0)
        else:
            centroid = np.sum(x_cls * w_eff[:, None], axis=0) / w_sum
        centroids.append(centroid.astype(np.float32).tolist())
    return {"classes": labels, "centroids": centroids}


def _softmax(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    x = values - np.max(values)
    ex = np.exp(x)
    den = np.sum(ex)
    if den <= 0:
        return np.full_like(ex, 1.0 / max(1, len(ex)))
    return ex / den


def _predict_task(task_model: dict[str, Any], x: np.ndarray) -> tuple[str, float]:
    classes = list(task_model.get("classes") or [])
    cents = np.asarray(task_model.get("centroids") or [], dtype=np.float32)
    if not classes or cents.size == 0:
        return "UNKNOWN", 0.0
    # negative euclidean distance => higher is better
    dists = np.linalg.norm(cents - x[None, :], axis=1)
    probs = _softmax(-dists)
    idx = int(np.argmax(probs))
    return str(classes[idx]), float(probs[idx])


def _accuracy(task_model: dict[str, Any], x: np.ndarray, y: np.ndarray) -> float:
    if x.size == 0 or y.size == 0:
        return float("nan")
    hit = 0
    for i in range(len(y)):
        pred, _ = _predict_task(task_model, x[i])
        if pred == str(y[i]):
            hit += 1
    return hit / max(1, len(y))


def _normalize_features(train_x: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = np.mean(train_x, axis=0)
    sigma = np.std(train_x, axis=0)
    sigma = np.where(sigma < 1e-8, 1.0, sigma)
    return (x - mu) / sigma, mu, sigma


@dataclass(slots=True)
class HybridTrainResult:
    model_path: str
    rows_total: int
    rows_train: int
    rows_val: int
    rows_test: int
    metrics: dict[str, Any]


def train_hybrid_model(
    db_path: str,
    model_dir: str,
    min_samples: int = 40,
) -> HybridTrainResult:
    rows = fetch_training_rows(db_path)
    if len(rows) < int(min_samples):
        raise ValueError(
            f"Not enough labeled samples for training: {len(rows)} < {int(min_samples)}"
        )
    feats: list[np.ndarray] = []
    dims: dict[str, int] | None = None
    labels: list[dict[str, str]] = []
    kept_rows: list[dict[str, Any]] = []
    for row in rows:
        vec, dims_row = _fusion_features(row)
        if vec.size == 0:
            continue
        feats.append(vec)
        labels.append(_task_labels(row))
        kept_rows.append(row)
        if dims is None:
            dims = dims_row
    if not feats or dims is None:
        raise ValueError("No valid feature vectors for training")

    x_all = np.vstack(feats).astype(np.float32)
    train_idx, val_idx, test_idx = _temporal_split(len(kept_rows))
    x_train_raw = x_all[train_idx]
    x_all_norm, mu, sigma = _normalize_features(x_train_raw, x_all)

    weights_all = _group_weights(kept_rows)
    weights_train = weights_all[train_idx]

    tasks = [
        "right_edge_status",
        "scalp_rating",
        "intraday_rating",
        "swing_rating",
        "level_action_scalp",
        "level_action_intraday",
        "level_action_swing",
    ]
    model_tasks: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    for task in tasks:
        y = np.array([lab[task] for lab in labels], dtype=object)
        y_train = y[train_idx]
        task_model = _train_weighted_centroids(
            x_all_norm[train_idx], y_train, weights_train
        )
        model_tasks[task] = task_model
        metrics[task] = {
            "train_acc": _accuracy(task_model, x_all_norm[train_idx], y[train_idx]),
            "val_acc": _accuracy(task_model, x_all_norm[val_idx], y[val_idx]),
            "test_acc": _accuracy(task_model, x_all_norm[test_idx], y[test_idx]),
        }

    model_obj = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "type": "hybrid_fusion_centroid_v1",
        "image_dim": int(dims["image_dim"]),
        "struct_dim": int(dims["struct_dim"]),
        "feature_dim": int(x_all.shape[1]),
        "normalization": {
            "mean": mu.astype(np.float32).tolist(),
            "std": sigma.astype(np.float32).tolist(),
        },
        "tasks": model_tasks,
        "metrics": metrics,
        "rows_total": int(len(kept_rows)),
        "rows_train": int(len(train_idx)),
        "rows_val": int(len(val_idx)),
        "rows_test": int(len(test_idx)),
    }

    out_dir = Path(model_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "chart_vision_v2.json"
    model_path.write_text(json.dumps(model_obj, indent=2), encoding="utf-8")
    return HybridTrainResult(
        model_path=str(model_path),
        rows_total=int(len(kept_rows)),
        rows_train=int(len(train_idx)),
        rows_val=int(len(val_idx)),
        rows_test=int(len(test_idx)),
        metrics=metrics,
    )


def _load_model(model_dir: str) -> dict[str, Any] | None:
    path = Path(model_dir) / "chart_vision_v2.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _propose_levels(
    sample: dict[str, Any], action_map: dict[str, str]
) -> dict[str, dict[str, Any]]:
    ctx = sample.get("model_context") or {}
    current = sample.get("current_level_sets") or {}
    direction = str(ctx.get("direction") or "UNKNOWN").upper()
    entry = _safe_float(ctx.get("entry_price"), 0.0)
    out: dict[str, dict[str, Any]] = {}
    for style in ("scalp", "intraday", "swing"):
        action = action_map.get(style, "KEEP")
        if action != "ADJUST":
            out[style] = {"sl": "KEEP", "tp": "KEEP", "action": "KEEP"}
            continue
        row = current.get(style) or {}
        sl_cur = _safe_float(row.get("sl"), 0.0)
        tp_cur = _safe_float(row.get("tp1") or row.get("tp"), 0.0)
        if entry <= 0:
            out[style] = {"sl": "KEEP", "tp": "KEEP", "action": "KEEP"}
            continue
        if direction == "LONG":
            sl_new = sl_cur if sl_cur > 0 else entry * 0.9975
            tp_new = tp_cur if tp_cur > 0 else entry * 1.0040
        elif direction == "SHORT":
            sl_new = sl_cur if sl_cur > 0 else entry * 1.0025
            tp_new = tp_cur if tp_cur > 0 else entry * 0.9960
        else:
            out[style] = {"sl": "KEEP", "tp": "KEEP", "action": "KEEP"}
            continue
        out[style] = {
            "sl": round(float(sl_new), 6),
            "tp": round(float(tp_new), 6),
            "action": "ADJUST",
        }
    return out


def infer_chart_vision_v2(
    sample: VisionSample | dict[str, Any],
    model_dir: str,
    current_level_sets: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    model = _load_model(model_dir)
    if not model:
        return None
    if isinstance(sample, VisionSample):
        row: dict[str, Any] = {
            "id": sample.id,
            "symbol": sample.symbol,
            "timeframe": sample.timeframe,
            "tf_role": sample.tf_role,
            "asset_type": sample.asset_type,
            "image_path": sample.image_path,
            "model_context": sample.model_context,
            "engine_b": sample.engine_b,
            "structured": sample.structured,
            "current_level_sets": current_level_sets or {},
        }
    else:
        row = dict(sample)
        row.setdefault("current_level_sets", current_level_sets or {})
    x, _ = _fusion_features(row)
    if x.size == 0:
        return None
    mu = np.asarray(model.get("normalization", {}).get("mean") or [], dtype=np.float32)
    sigma = np.asarray(model.get("normalization", {}).get("std") or [], dtype=np.float32)
    if mu.size and sigma.size and len(mu) == len(x):
        x = (x - mu) / np.where(sigma < 1e-8, 1.0, sigma)
    tasks = model.get("tasks") or {}
    pred_right, conf_right = _predict_task(tasks.get("right_edge_status", {}), x)
    pred_scalp, conf_scalp = _predict_task(tasks.get("scalp_rating", {}), x)
    pred_intra, conf_intra = _predict_task(tasks.get("intraday_rating", {}), x)
    pred_swing, conf_swing = _predict_task(tasks.get("swing_rating", {}), x)
    act_scalp, _ = _predict_task(tasks.get("level_action_scalp", {}), x)
    act_intra, _ = _predict_task(tasks.get("level_action_intraday", {}), x)
    act_swing, _ = _predict_task(tasks.get("level_action_swing", {}), x)
    tf_alignment = "ALIGNED" if pred_right == "CONFIRMS" else "CONFLICTED"
    level_actions = {
        "scalp": act_scalp if act_scalp in ("KEEP", "ADJUST") else "KEEP",
        "intraday": act_intra if act_intra in ("KEEP", "ADJUST") else "KEEP",
        "swing": act_swing if act_swing in ("KEEP", "ADJUST") else "KEEP",
    }
    level_suggestions = _propose_levels(row, level_actions)
    confidence = float(np.mean([conf_right, conf_scalp, conf_intra, conf_swing]))
    return {
        "model_version": "chart_vision_v2",
        "right_edge_status": pred_right,
        "tf_alignment": tf_alignment,
        "scalp_rating": pred_scalp,
        "intraday_rating": pred_intra,
        "swing_rating": pred_swing,
        "level_actions": level_actions,
        "level_suggestions": level_suggestions,
        "confidence": round(confidence, 4),
        "task_confidence": {
            "right_edge_status": round(conf_right, 4),
            "scalp_rating": round(conf_scalp, 4),
            "intraday_rating": round(conf_intra, 4),
            "swing_rating": round(conf_swing, 4),
        },
    }

