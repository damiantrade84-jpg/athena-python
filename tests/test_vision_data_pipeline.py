import uuid
from pathlib import Path

from vision_data import (
    create_vision_label,
    create_vision_prediction,
    create_vision_sample,
    fetch_training_rows,
    fetch_vision_metrics,
)


_PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+Xq0QAAAAASUVORK5CYII="


def _mk_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parent / "_artifacts"
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / f"vision_data_{uuid.uuid4().hex}.db"
    return root, db_path


def test_vision_sample_dedupe_and_metrics():
    artifacts, db_path = _mk_paths()

    sample_1 = create_vision_sample(
        str(db_path),
        {
            "symbol": "EUR/USD",
            "timeframe": "H4",
            "tf_role": "H4",
            "asset_type": "forex",
            "request_id": "req-1",
            "image": _PNG_1X1,
            "engine_b": {"bos_mtf_confirmed": True},
            "structured": {"right_edge_status": "CONFIRMS"},
        },
        str(artifacts),
    )
    sample_2 = create_vision_sample(
        str(db_path),
        {
            "symbol": "EUR/USD",
            "timeframe": "H4",
            "tf_role": "H4",
            "asset_type": "forex",
            "request_id": "req-1",
            "image": _PNG_1X1,
            "engine_b": {"bos_mtf_confirmed": True},
            "structured": {"right_edge_status": "CONFIRMS"},
        },
        str(artifacts),
    )
    assert sample_1.id == sample_2.id
    assert Path(sample_1.image_path).exists()

    label = create_vision_label(
        str(db_path),
        {
            "sample_id": sample_1.id,
            "label_source": "resolved",
            "review_status": "resolved",
            "right_edge_status": "CONFIRMS",
            "tf_alignment": "ALIGNED",
            "scalp_rating": "STRONG",
            "intraday_rating": "MODERATE",
            "swing_rating": "MODERATE",
            "confidence": 0.95,
        },
    )
    assert label.sample_id == sample_1.id

    pred_v1 = create_vision_prediction(
        str(db_path),
        {
            "sample_id": sample_1.id,
            "model_version": "chart_vision_v1",
            "right_edge_status": "CONFIRMS",
            "tf_alignment": "ALIGNED",
            "scalp_rating": "STRONG",
            "intraday_rating": "MODERATE",
            "swing_rating": "MODERATE",
            "confidence": 0.7,
        },
    )
    pred_v2 = create_vision_prediction(
        str(db_path),
        {
            "sample_id": sample_1.id,
            "model_version": "chart_vision_v2",
            "right_edge_status": "REVIEW",
            "tf_alignment": "CONFLICTED",
            "scalp_rating": "WEAK",
            "intraday_rating": "WEAK",
            "swing_rating": "MODERATE",
            "confidence": 0.6,
        },
    )
    assert pred_v1.sample_id == pred_v2.sample_id

    metrics = fetch_vision_metrics(str(db_path), lookback_days=30)
    assert metrics["counts"]["samples"] >= 1
    assert metrics["counts"]["labels"] >= 1
    assert metrics["counts"]["predictions"] >= 2
    assert "disagreement" in metrics


def test_fetch_training_rows_returns_latest_label():
    artifacts, db_path = _mk_paths()
    sample = create_vision_sample(
        str(db_path),
        {
            "symbol": "BTCUSDT",
            "timeframe": "H1",
            "tf_role": "H1",
            "asset_type": "crypto",
            "request_id": "req-2",
            "image": _PNG_1X1,
        },
        str(artifacts),
    )
    create_vision_label(
        str(db_path),
        {
            "sample_id": sample.id,
            "label_source": "auto",
            "review_status": "auto",
            "right_edge_status": "REVIEW",
        },
    )
    create_vision_label(
        str(db_path),
        {
            "sample_id": sample.id,
            "label_source": "resolved",
            "review_status": "resolved",
            "right_edge_status": "CONFIRMS",
        },
    )
    rows = fetch_training_rows(str(db_path))
    assert len(rows) == 1
    assert rows[0]["label_right_edge_status"] == "CONFIRMS"
