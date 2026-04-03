import uuid
from pathlib import Path

from vision_data import create_vision_label, create_vision_sample, fetch_vision_sample
from vision_hybrid import infer_chart_vision_v2, train_hybrid_model


_PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+Xq0QAAAAASUVORK5CYII="


def _seed_dataset(db_path: str, artifacts_dir: str, n: int = 24) -> int:
    first_id = 0
    for i in range(n):
        direction = "LONG" if i % 2 == 0 else "SHORT"
        right_edge = "CONFIRMS" if i % 2 == 0 else "REVIEW"
        sample = create_vision_sample(
            db_path,
            {
                "symbol": "EUR/USD" if i % 2 == 0 else "BTCUSDT",
                "timeframe": "H4" if i % 3 else "H1",
                "tf_role": "H4" if i % 3 else "H1",
                "asset_type": "forex" if i % 2 == 0 else "crypto",
                "request_id": f"seed-{i}",
                "image": _PNG_1X1,
                "model_context": {"direction": direction, "entry_price": 100.0 + i},
                "engine_b": {"bos_mtf_confirmed": i % 2 == 0},
                "structured": {"right_edge_status": right_edge},
            },
            artifacts_dir,
        )
        if first_id == 0:
            first_id = sample.id
        create_vision_label(
            db_path,
            {
                "sample_id": sample.id,
                "label_source": "resolved",
                "review_status": "resolved",
                "right_edge_status": right_edge,
                "tf_alignment": "ALIGNED" if right_edge == "CONFIRMS" else "CONFLICTED",
                "scalp_rating": "STRONG" if i % 2 == 0 else "WEAK",
                "intraday_rating": "MODERATE",
                "swing_rating": "MODERATE",
                "levels": {"scalp": {"action": "KEEP"}},
            },
        )
    return first_id


def _mk_paths() -> tuple[Path, Path, Path]:
    root = Path(__file__).resolve().parent / "_artifacts"
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / f"vision_hybrid_{uuid.uuid4().hex}.db"
    model_dir = root / f"vision_model_{uuid.uuid4().hex}"
    return root, db_path, model_dir


def test_train_and_infer_hybrid_model():
    artifacts, db_path, model_dir = _mk_paths()
    first_id = _seed_dataset(str(db_path), str(artifacts), n=24)

    result = train_hybrid_model(
        db_path=str(db_path),
        model_dir=str(model_dir),
        min_samples=8,
    )
    assert result.rows_total >= 8
    assert (model_dir / "chart_vision_v2.json").exists()

    sample = fetch_vision_sample(str(db_path), first_id)
    assert sample is not None
    inference = infer_chart_vision_v2(sample, str(model_dir), current_level_sets={})
    assert inference is not None
    assert inference["model_version"] == "chart_vision_v2"
    assert inference["right_edge_status"] in {"CONFIRMS", "REVIEW", "POTENTIAL_REVERSAL"}
    assert 0.0 <= float(inference["confidence"]) <= 1.0
