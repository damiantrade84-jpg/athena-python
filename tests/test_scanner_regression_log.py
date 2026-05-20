"""Regression-A scan log diagnostics."""

from scanner import _engine_a_regression_tags, _regression_candle_count


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
        f"D1={_regression_candle_count({'D1': None}, 'D1'):3d} "
        f"H4={_regression_candle_count({'H4': []}, 'H4'):3d} "
        f"H1={_regression_candle_count({'H1': [1]}, 'H1'):3d} "
        f"score={score:.2f}/{max_score:.1f} dir={direction:5s}"
        f"{_engine_a_regression_tags(sig_a)}"
    )

    assert "D1=  0 H4=  0 H1=  1" in line
    assert "score=0.00/3.0" in line
    assert "dir=NONE" in line
    assert "scorer=not_called" in line
    assert "fallback_used=false" in line


def test_regression_a_log_tags_v2_scorer_and_abort():
    sig_a = {
        "factorDiagnostics": {
            "engineVersion": "A_V2",
            "scorerSelected": "factor_scoring.compute_factor_scores",
            "abortReason": "indeterminate_trend",
        }
    }

    tags = _engine_a_regression_tags(sig_a)

    assert "scorer=A_V2" in tags
    assert "selected=factor_scoring.compute_factor_scores" in tags
    assert "abort=indeterminate_trend" in tags
    assert "fallback_used=false" in tags


def test_regression_a_log_tags_pre_scoring_freshness_failure():
    sig_a = {
        "dataFreshness": {
            "allowed": False,
            "reason": "STALE_DATA_PRE_SCORING:H1 stale",
        }
    }

    tags = _engine_a_regression_tags(sig_a)

    assert "scorer=not_called" in tags
    assert "selected=pre_scoring_freshness_gate" in tags
    assert "failure=STALE_DATA_PRE_SCORING:H1_stale" in tags
    assert "fallback_used=false" in tags
