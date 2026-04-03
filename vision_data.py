"""Dataset and metrics helpers for chart-vision training/inference."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_RIGHT_EDGE_VALUES = {"CONFIRMS", "REVIEW", "POTENTIAL_REVERSAL"}
_TF_ALIGNMENT_VALUES = {"ALIGNED", "CONFLICTED"}
_STYLE_VALUES = {"STRONG", "MODERATE", "WEAK", "AVOID", "CONTRADICTS"}
_LABEL_SOURCE_VALUES = {"auto", "human", "resolved"}
_REVIEW_STATUS_VALUES = {"auto", "human", "resolved"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, separators=(",", ":"))


def _json_load(raw: Any) -> Any:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return {}


def _normalize_choice(value: Any, allowed: set[str], fallback: str) -> str:
    norm = str(value or "").strip().upper().replace(" ", "_")
    if norm in allowed:
        return norm
    return fallback


def _normalize_style_rating(value: Any, fallback: str = "MODERATE") -> str:
    norm = str(value or "").strip().upper()
    if norm == "CONTRADICT":
        norm = "CONTRADICTS"
    if norm in _STYLE_VALUES:
        return norm
    return fallback


def _decode_data_url_or_b64(image_value: str) -> bytes:
    raw = str(image_value or "").strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[1] if "," in raw else ""
    if not raw:
        return b""
    try:
        return base64.b64decode(raw, validate=False)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 image payload") from exc


def _image_artifact_path(artifacts_root: str) -> Path:
    now = datetime.now(timezone.utc)
    root = Path(artifacts_root)
    folder = root / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Some operator environments block nested directory creation under synced paths.
        # Fall back to the provided root (or CWD) so artifact persistence remains available.
        try:
            root.mkdir(parents=True, exist_ok=True)
            folder = root
        except Exception:
            folder = Path.cwd()
    return folder / f"{uuid.uuid4().hex}.png"


@dataclass(slots=True)
class VisionSample:
    id: int
    created_at: str
    symbol: str
    timeframe: str
    tf_role: str
    asset_type: str
    source: str
    chart_source: str
    request_id: str
    image_path: str
    image_sha1: str
    image_bytes: int
    model_context: dict[str, Any]
    engine_b: dict[str, Any]
    structured: dict[str, Any]
    outcome: dict[str, Any]


@dataclass(slots=True)
class VisionLabel:
    id: int
    sample_id: int
    created_at: str
    label_source: str
    review_status: str
    right_edge_status: str
    tf_alignment: str
    scalp_rating: str
    intraday_rating: str
    swing_rating: str
    levels: dict[str, Any]
    outcome: dict[str, Any]
    confidence: float
    notes: str


@dataclass(slots=True)
class VisionPrediction:
    id: int
    sample_id: int
    created_at: str
    model_version: str
    right_edge_status: str
    tf_alignment: str
    scalp_rating: str
    intraday_rating: str
    swing_rating: str
    levels: dict[str, Any]
    confidence: float
    disagreement_score: float | None
    drift_score: float | None
    payload: dict[str, Any]


def ensure_vision_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS vision_samples (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT NOT NULL,
            symbol           TEXT NOT NULL DEFAULT '',
            timeframe        TEXT NOT NULL DEFAULT '',
            tf_role          TEXT NOT NULL DEFAULT '',
            asset_type       TEXT NOT NULL DEFAULT '',
            source           TEXT NOT NULL DEFAULT '',
            chart_source     TEXT NOT NULL DEFAULT '',
            request_id       TEXT NOT NULL DEFAULT '',
            image_path       TEXT NOT NULL DEFAULT '',
            image_sha1       TEXT NOT NULL DEFAULT '',
            image_bytes      INTEGER NOT NULL DEFAULT 0,
            model_context_json TEXT NOT NULL DEFAULT '{}',
            engine_b_json    TEXT NOT NULL DEFAULT '{}',
            structured_json  TEXT NOT NULL DEFAULT '{}',
            outcome_json     TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS vision_labels (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id        INTEGER NOT NULL,
            created_at       TEXT NOT NULL,
            label_source     TEXT NOT NULL DEFAULT 'auto',
            review_status    TEXT NOT NULL DEFAULT 'auto',
            right_edge_status TEXT NOT NULL DEFAULT 'REVIEW',
            tf_alignment     TEXT NOT NULL DEFAULT 'CONFLICTED',
            scalp_rating     TEXT NOT NULL DEFAULT 'MODERATE',
            intraday_rating  TEXT NOT NULL DEFAULT 'MODERATE',
            swing_rating     TEXT NOT NULL DEFAULT 'MODERATE',
            levels_json      TEXT NOT NULL DEFAULT '{}',
            outcome_json     TEXT NOT NULL DEFAULT '{}',
            confidence       REAL NOT NULL DEFAULT 0.50,
            notes            TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(sample_id) REFERENCES vision_samples(id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS vision_predictions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id        INTEGER NOT NULL,
            created_at       TEXT NOT NULL,
            model_version    TEXT NOT NULL DEFAULT '',
            right_edge_status TEXT NOT NULL DEFAULT 'REVIEW',
            tf_alignment     TEXT NOT NULL DEFAULT 'CONFLICTED',
            scalp_rating     TEXT NOT NULL DEFAULT 'MODERATE',
            intraday_rating  TEXT NOT NULL DEFAULT 'MODERATE',
            swing_rating     TEXT NOT NULL DEFAULT 'MODERATE',
            levels_json      TEXT NOT NULL DEFAULT '{}',
            confidence       REAL NOT NULL DEFAULT 0.50,
            disagreement_score REAL,
            drift_score      REAL,
            payload_json     TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(sample_id) REFERENCES vision_samples(id)
        )
        """
    )
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_samples_dedupe
        ON vision_samples(symbol, timeframe, tf_role, request_id, image_sha1)
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_vision_samples_created ON vision_samples(created_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_vision_samples_asset_tf ON vision_samples(asset_type, timeframe)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_vision_labels_sample ON vision_labels(sample_id, review_status, created_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_vision_predictions_sample ON vision_predictions(sample_id, model_version, created_at)"
    )


def _derive_auto_label(payload: dict[str, Any]) -> dict[str, Any]:
    structured = _json_load(payload.get("structured"))
    engine_b = _json_load(payload.get("engine_b"))

    right_edge = _normalize_choice(
        structured.get("right_edge_status"),
        _RIGHT_EDGE_VALUES,
        "REVIEW",
    )
    if right_edge == "REVIEW" and bool(structured.get("countertrend_volume_flag")):
        right_edge = "POTENTIAL_REVERSAL"
    if right_edge == "REVIEW" and bool(engine_b.get("countertrend_volume_flag")):
        right_edge = "POTENTIAL_REVERSAL"
    if (
        right_edge == "REVIEW"
        and bool(engine_b.get("bos_mtf_confirmed"))
        and not bool(engine_b.get("choch_data", {}).get("choch_bear"))
        and not bool(engine_b.get("choch_data", {}).get("choch_bull"))
    ):
        right_edge = "CONFIRMS"

    tf_alignment = _normalize_choice(
        structured.get("tf_alignment") or payload.get("tf_alignment"),
        _TF_ALIGNMENT_VALUES,
        "ALIGNED" if right_edge == "CONFIRMS" else "CONFLICTED",
    )
    style = structured.get("style_ratings") or {}
    return {
        "right_edge_status": right_edge,
        "tf_alignment": tf_alignment,
        "scalp_rating": _normalize_style_rating(style.get("scalp")),
        "intraday_rating": _normalize_style_rating(style.get("intraday")),
        "swing_rating": _normalize_style_rating(style.get("swing")),
        "levels": _json_load(structured.get("level_suggestions")),
    }


def create_vision_sample(
    db_path: str,
    payload: dict[str, Any],
    artifacts_root: str,
) -> VisionSample:
    image_bytes = b""
    image_path = str(payload.get("image_path") or "").strip()
    image_sha1 = str(payload.get("image_sha1") or "").strip()
    if payload.get("image"):
        image_bytes = _decode_data_url_or_b64(str(payload.get("image")))
        image_sha1 = hashlib.sha1(image_bytes).hexdigest()
        artifact = _image_artifact_path(artifacts_root)
        artifact.write_bytes(image_bytes)
        image_path = str(artifact)
    elif image_path and os.path.exists(image_path):
        image_bytes = Path(image_path).read_bytes()
        if not image_sha1:
            image_sha1 = hashlib.sha1(image_bytes).hexdigest()
    if not image_sha1:
        image_sha1 = hashlib.sha1(
            f"{payload.get('symbol','')}|{payload.get('timeframe','')}|{payload.get('tf_role','')}".encode(
                "utf-8"
            )
        ).hexdigest()

    created_at = _utc_now_iso()
    symbol = str(payload.get("symbol") or "").strip()
    timeframe = str(payload.get("timeframe") or "").strip().upper()
    tf_role = str(payload.get("tf_role") or timeframe or "").strip().upper()
    asset_type = str(payload.get("asset_type") or "").strip().lower()
    source = str(payload.get("source") or "chart-analysis").strip()
    chart_source = str(payload.get("chart_source") or "").strip()
    request_id = str(payload.get("request_id") or "").strip()
    model_context = _json_load(payload.get("model_context"))
    engine_b = _json_load(payload.get("engine_b"))
    structured = _json_load(payload.get("structured"))
    outcome = _json_load(payload.get("outcome"))
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_vision_schema(con)
        con.execute(
            """
            INSERT OR IGNORE INTO vision_samples (
                created_at,symbol,timeframe,tf_role,asset_type,source,chart_source,
                request_id,image_path,image_sha1,image_bytes,
                model_context_json,engine_b_json,structured_json,outcome_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                created_at,
                symbol,
                timeframe,
                tf_role,
                asset_type,
                source,
                chart_source,
                request_id,
                image_path,
                image_sha1,
                int(len(image_bytes)),
                _json_dump(model_context),
                _json_dump(engine_b),
                _json_dump(structured),
                _json_dump(outcome),
            ),
        )
        row = con.execute(
            """
            SELECT * FROM vision_samples
            WHERE symbol=? AND timeframe=? AND tf_role=? AND request_id=? AND image_sha1=?
            ORDER BY id DESC LIMIT 1
            """,
            (symbol, timeframe, tf_role, request_id, image_sha1),
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to insert vision sample")
        return VisionSample(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            symbol=str(row["symbol"]),
            timeframe=str(row["timeframe"]),
            tf_role=str(row["tf_role"]),
            asset_type=str(row["asset_type"]),
            source=str(row["source"]),
            chart_source=str(row["chart_source"]),
            request_id=str(row["request_id"]),
            image_path=str(row["image_path"]),
            image_sha1=str(row["image_sha1"]),
            image_bytes=int(row["image_bytes"] or 0),
            model_context=_json_load(row["model_context_json"]),
            engine_b=_json_load(row["engine_b_json"]),
            structured=_json_load(row["structured_json"]),
            outcome=_json_load(row["outcome_json"]),
        )


def create_vision_label(db_path: str, payload: dict[str, Any]) -> VisionLabel:
    sample_id = int(payload.get("sample_id") or 0)
    if sample_id <= 0:
        raise ValueError("sample_id is required")
    created_at = _utc_now_iso()
    label_source = str(payload.get("label_source") or "auto").strip().lower()
    if label_source not in _LABEL_SOURCE_VALUES:
        label_source = "auto"
    review_status = str(payload.get("review_status") or label_source).strip().lower()
    if review_status not in _REVIEW_STATUS_VALUES:
        review_status = label_source
    auto = _derive_auto_label(payload)
    right_edge_status = _normalize_choice(
        payload.get("right_edge_status") or auto["right_edge_status"],
        _RIGHT_EDGE_VALUES,
        auto["right_edge_status"],
    )
    tf_alignment = _normalize_choice(
        payload.get("tf_alignment") or auto["tf_alignment"],
        _TF_ALIGNMENT_VALUES,
        auto["tf_alignment"],
    )
    scalp_rating = _normalize_style_rating(
        payload.get("scalp_rating") or auto["scalp_rating"],
        auto["scalp_rating"],
    )
    intraday_rating = _normalize_style_rating(
        payload.get("intraday_rating") or auto["intraday_rating"],
        auto["intraday_rating"],
    )
    swing_rating = _normalize_style_rating(
        payload.get("swing_rating") or auto["swing_rating"],
        auto["swing_rating"],
    )
    levels = _json_load(payload.get("levels"))
    if not levels:
        levels = auto["levels"]
    outcome = _json_load(payload.get("outcome"))
    if not outcome:
        outcome = _json_load(payload.get("realized_outcome"))
    confidence = payload.get("confidence")
    try:
        confidence_val = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_val = None
    if confidence_val is None:
        confidence_val = 0.55 if label_source == "auto" else 0.85
        if label_source == "resolved":
            confidence_val = 0.95
    confidence_val = max(0.0, min(1.0, confidence_val))
    notes = str(payload.get("notes") or "").strip()

    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_vision_schema(con)
        con.execute(
            """
            INSERT INTO vision_labels (
                sample_id,created_at,label_source,review_status,right_edge_status,tf_alignment,
                scalp_rating,intraday_rating,swing_rating,levels_json,outcome_json,confidence,notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sample_id,
                created_at,
                label_source,
                review_status,
                right_edge_status,
                tf_alignment,
                scalp_rating,
                intraday_rating,
                swing_rating,
                _json_dump(levels),
                _json_dump(outcome),
                confidence_val,
                notes,
            ),
        )
        row = con.execute(
            "SELECT * FROM vision_labels WHERE id = last_insert_rowid()"
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to insert vision label")
        return VisionLabel(
            id=int(row["id"]),
            sample_id=int(row["sample_id"]),
            created_at=str(row["created_at"]),
            label_source=str(row["label_source"]),
            review_status=str(row["review_status"]),
            right_edge_status=str(row["right_edge_status"]),
            tf_alignment=str(row["tf_alignment"]),
            scalp_rating=str(row["scalp_rating"]),
            intraday_rating=str(row["intraday_rating"]),
            swing_rating=str(row["swing_rating"]),
            levels=_json_load(row["levels_json"]),
            outcome=_json_load(row["outcome_json"]),
            confidence=float(row["confidence"] or 0.0),
            notes=str(row["notes"] or ""),
        )


def create_vision_prediction(
    db_path: str, payload: dict[str, Any]
) -> VisionPrediction:
    sample_id = int(payload.get("sample_id") or 0)
    if sample_id <= 0:
        raise ValueError("sample_id is required")
    created_at = _utc_now_iso()
    model_version = str(payload.get("model_version") or "").strip()
    if not model_version:
        raise ValueError("model_version is required")
    right_edge_status = _normalize_choice(
        payload.get("right_edge_status"),
        _RIGHT_EDGE_VALUES,
        "REVIEW",
    )
    tf_alignment = _normalize_choice(
        payload.get("tf_alignment"),
        _TF_ALIGNMENT_VALUES,
        "CONFLICTED",
    )
    scalp_rating = _normalize_style_rating(payload.get("scalp_rating"))
    intraday_rating = _normalize_style_rating(payload.get("intraday_rating"))
    swing_rating = _normalize_style_rating(payload.get("swing_rating"))
    levels = _json_load(payload.get("levels"))
    confidence = payload.get("confidence")
    try:
        confidence_val = float(confidence) if confidence is not None else 0.5
    except (TypeError, ValueError):
        confidence_val = 0.5
    confidence_val = max(0.0, min(1.0, confidence_val))
    disagreement = payload.get("disagreement_score")
    drift = payload.get("drift_score")
    try:
        disagreement_val = float(disagreement) if disagreement is not None else None
    except (TypeError, ValueError):
        disagreement_val = None
    try:
        drift_val = float(drift) if drift is not None else None
    except (TypeError, ValueError):
        drift_val = None
    pred_payload = _json_load(payload.get("payload"))

    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_vision_schema(con)
        con.execute(
            """
            INSERT INTO vision_predictions (
                sample_id,created_at,model_version,right_edge_status,tf_alignment,
                scalp_rating,intraday_rating,swing_rating,levels_json,confidence,
                disagreement_score,drift_score,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sample_id,
                created_at,
                model_version,
                right_edge_status,
                tf_alignment,
                scalp_rating,
                intraday_rating,
                swing_rating,
                _json_dump(levels),
                confidence_val,
                disagreement_val,
                drift_val,
                _json_dump(pred_payload),
            ),
        )
        row = con.execute(
            "SELECT * FROM vision_predictions WHERE id = last_insert_rowid()"
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to insert vision prediction")
        return VisionPrediction(
            id=int(row["id"]),
            sample_id=int(row["sample_id"]),
            created_at=str(row["created_at"]),
            model_version=str(row["model_version"]),
            right_edge_status=str(row["right_edge_status"]),
            tf_alignment=str(row["tf_alignment"]),
            scalp_rating=str(row["scalp_rating"]),
            intraday_rating=str(row["intraday_rating"]),
            swing_rating=str(row["swing_rating"]),
            levels=_json_load(row["levels_json"]),
            confidence=float(row["confidence"] or 0.0),
            disagreement_score=(
                float(row["disagreement_score"])
                if row["disagreement_score"] is not None
                else None
            ),
            drift_score=(
                float(row["drift_score"]) if row["drift_score"] is not None else None
            ),
            payload=_json_load(row["payload_json"]),
        )


def fetch_vision_sample(db_path: str, sample_id: int) -> VisionSample | None:
    sid = int(sample_id)
    if sid <= 0:
        return None
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_vision_schema(con)
        row = con.execute("SELECT * FROM vision_samples WHERE id=?", (sid,)).fetchone()
        if row is None:
            return None
        return VisionSample(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            symbol=str(row["symbol"]),
            timeframe=str(row["timeframe"]),
            tf_role=str(row["tf_role"]),
            asset_type=str(row["asset_type"]),
            source=str(row["source"]),
            chart_source=str(row["chart_source"]),
            request_id=str(row["request_id"]),
            image_path=str(row["image_path"]),
            image_sha1=str(row["image_sha1"]),
            image_bytes=int(row["image_bytes"] or 0),
            model_context=_json_load(row["model_context_json"]),
            engine_b=_json_load(row["engine_b_json"]),
            structured=_json_load(row["structured_json"]),
            outcome=_json_load(row["outcome_json"]),
        )


def fetch_training_rows(
    db_path: str,
    review_statuses: tuple[str, ...] = ("resolved", "human", "auto"),
) -> list[dict[str, Any]]:
    statuses = tuple(s for s in review_statuses if s in _REVIEW_STATUS_VALUES)
    if not statuses:
        statuses = ("resolved", "human", "auto")
    placeholders = ",".join("?" for _ in statuses)
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_vision_schema(con)
        rows = con.execute(
            f"""
            WITH latest_label AS (
                SELECT sample_id, MAX(id) AS max_id
                FROM vision_labels
                WHERE review_status IN ({placeholders})
                GROUP BY sample_id
            )
            SELECT
                s.*,
                l.right_edge_status AS label_right_edge_status,
                l.tf_alignment AS label_tf_alignment,
                l.scalp_rating AS label_scalp_rating,
                l.intraday_rating AS label_intraday_rating,
                l.swing_rating AS label_swing_rating,
                l.levels_json AS label_levels_json,
                l.confidence AS label_confidence,
                l.review_status AS label_review_status,
                l.label_source AS label_source
            FROM vision_samples s
            JOIN latest_label ll ON ll.sample_id = s.id
            JOIN vision_labels l ON l.id = ll.max_id
            ORDER BY s.created_at ASC, s.id ASC
            """,
            statuses,
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        rec["model_context"] = _json_load(rec.pop("model_context_json", "{}"))
        rec["engine_b"] = _json_load(rec.pop("engine_b_json", "{}"))
        rec["structured"] = _json_load(rec.pop("structured_json", "{}"))
        rec["outcome"] = _json_load(rec.pop("outcome_json", "{}"))
        rec["label_levels"] = _json_load(rec.pop("label_levels_json", "{}"))
        out.append(rec)
    return out


def _distribution(rows: list[str]) -> dict[str, float]:
    if not rows:
        return {}
    total = float(len(rows))
    out: dict[str, float] = {}
    for r in rows:
        key = _normalize_choice(r, _RIGHT_EDGE_VALUES, "REVIEW")
        out[key] = out.get(key, 0.0) + 1.0
    for k in list(out.keys()):
        out[k] = out[k] / total
    return out


def _total_variation_distance(a: dict[str, float], b: dict[str, float]) -> float:
    keys = sorted(set(a.keys()) | set(b.keys()))
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def fetch_vision_metrics(db_path: str, lookback_days: int = 30) -> dict[str, Any]:
    lookback_days = max(1, int(lookback_days))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)).isoformat()
    short_cutoff = (now - timedelta(days=7)).isoformat()
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        ensure_vision_schema(con)
        counts = {
            "samples": int(
                con.execute("SELECT COUNT(*) FROM vision_samples").fetchone()[0] or 0
            ),
            "labels": int(
                con.execute("SELECT COUNT(*) FROM vision_labels").fetchone()[0] or 0
            ),
            "predictions": int(
                con.execute("SELECT COUNT(*) FROM vision_predictions").fetchone()[0] or 0
            ),
        }
        asset_rows = con.execute(
            "SELECT asset_type, COUNT(*) AS n FROM vision_samples GROUP BY asset_type ORDER BY n DESC"
        ).fetchall()
        tf_rows = con.execute(
            "SELECT timeframe, COUNT(*) AS n FROM vision_samples GROUP BY timeframe ORDER BY n DESC"
        ).fetchall()
        model_rows = con.execute(
            "SELECT model_version, COUNT(*) AS n FROM vision_predictions GROUP BY model_version ORDER BY n DESC"
        ).fetchall()
        recent_preds = con.execute(
            """
            SELECT sample_id, model_version, right_edge_status, created_at
            FROM vision_predictions
            WHERE created_at >= ?
            ORDER BY sample_id ASC, id DESC
            """,
            (cutoff,),
        ).fetchall()
        latest_by_sample: dict[int, dict[str, str]] = {}
        for row in recent_preds:
            sid = int(row["sample_id"])
            model = str(row["model_version"])
            latest_by_sample.setdefault(sid, {})
            if model not in latest_by_sample[sid]:
                latest_by_sample[sid][model] = str(row["right_edge_status"])
        both = 0
        disagree = 0
        for sample_models in latest_by_sample.values():
            v1 = sample_models.get("chart_vision_v1")
            v2 = sample_models.get("chart_vision_v2")
            if not v1 or not v2:
                continue
            both += 1
            if v1 != v2:
                disagree += 1
        v2_last_30 = [
            str(r["right_edge_status"])
            for r in con.execute(
                "SELECT right_edge_status FROM vision_predictions WHERE model_version='chart_vision_v2' AND created_at >= ?",
                (cutoff,),
            ).fetchall()
        ]
        v2_last_7 = [
            str(r["right_edge_status"])
            for r in con.execute(
                "SELECT right_edge_status FROM vision_predictions WHERE model_version='chart_vision_v2' AND created_at >= ?",
                (short_cutoff,),
            ).fetchall()
        ]
        drift_score = _total_variation_distance(
            _distribution(v2_last_30), _distribution(v2_last_7)
        )

        perf_rows = con.execute(
            """
            WITH latest_pred AS (
                SELECT p.sample_id, p.model_version, MAX(p.id) AS max_id
                FROM vision_predictions p
                GROUP BY p.sample_id, p.model_version
            ),
            latest_label AS (
                SELECT l.sample_id, MAX(l.id) AS max_id
                FROM vision_labels l
                WHERE l.review_status = 'resolved'
                GROUP BY l.sample_id
            )
            SELECT
                s.asset_type AS asset_type,
                p.model_version AS model_version,
                COUNT(*) AS n,
                SUM(CASE WHEN p.right_edge_status = l.right_edge_status THEN 1 ELSE 0 END) AS right_edge_hits
            FROM latest_pred lp
            JOIN vision_predictions p ON p.id = lp.max_id
            JOIN latest_label ll ON ll.sample_id = lp.sample_id
            JOIN vision_labels l ON l.id = ll.max_id
            JOIN vision_samples s ON s.id = p.sample_id
            GROUP BY s.asset_type, p.model_version
            ORDER BY s.asset_type ASC, p.model_version ASC
            """
        ).fetchall()

    performance = [
        {
            "asset_type": str(r["asset_type"]),
            "model_version": str(r["model_version"]),
            "samples": int(r["n"] or 0),
            "right_edge_accuracy": round(
                float(r["right_edge_hits"] or 0) / max(1, int(r["n"] or 0)), 4
            ),
        }
        for r in perf_rows
    ]
    return {
        "as_of": _utc_now_iso(),
        "lookback_days": lookback_days,
        "counts": counts,
        "samples_by_asset": [
            {"asset_type": str(r["asset_type"]), "count": int(r["n"] or 0)}
            for r in asset_rows
        ],
        "samples_by_timeframe": [
            {"timeframe": str(r["timeframe"]), "count": int(r["n"] or 0)}
            for r in tf_rows
        ],
        "predictions_by_model": [
            {"model_version": str(r["model_version"]), "count": int(r["n"] or 0)}
            for r in model_rows
        ],
        "disagreement": {
            "paired_samples": both,
            "disagree_count": disagree,
            "disagree_rate": round(disagree / max(1, both), 4),
        },
        "drift": {
            "right_edge_tvd_7d_vs_lookback": round(float(drift_score), 4),
            "status": "alert" if drift_score >= 0.25 else "ok",
        },
        "performance": performance,
    }


def asdict_safe(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    return {"value": obj}
