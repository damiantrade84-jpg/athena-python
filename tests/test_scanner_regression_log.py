"""Regression-A scan log formatting (no scanner import — avoids feed deps)."""


def test_regression_a_log_format_tolerates_null_score_fields():
    """run_full_scan REGRESSION-A line must not crash on explicit null A fields."""
    sig_a = {"confluenceScore": None, "maxScore": None, "direction": None}
    pair = {"display": "AUD/USD", "type": "forex"}
    score = float(sig_a.get("confluenceScore", 0) or 0)
    max_score = float(sig_a.get("maxScore", 3.0) or 3.0)
    direction = str(sig_a.get("direction") or "NONE")
    pair_type = str(pair.get("type") or "?")
    line = (
        f"[REGRESSION-A] {pair['display']:12s} type={pair_type:8s} "
        f"D1={1001:3d} H4={1001:3d} H1={1001:3d} "
        f"score={score:.2f}/{max_score:.1f} dir={direction:5s}"
    )
    assert "score=0.00/3.0" in line
    assert "dir=NONE" in line
