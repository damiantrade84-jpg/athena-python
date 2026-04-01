import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stability_monitor import (
    get_signal_stability_index,
    init_stability_store,
    join_stability_audit_queue,
    record_execution_event,
    record_outcome_event,
    record_signal_event,
)


_TEST_TMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(_TEST_TMP_ROOT, exist_ok=True)


def _test_db_path() -> str:
    return os.path.join(_TEST_TMP_ROOT, f"ssi_{uuid.uuid4().hex}.db")


def test_stability_index_degrades_gracefully_with_no_data():
    db_path = _test_db_path()
    init_stability_store(db_path)

    result = get_signal_stability_index(engine="engine_a", db_path=db_path)

    assert result["engine"] == "engine_a"
    # No stability_events yet: _engine_ssi has no component scores → ssi None, band NO_DATA
    assert result["ssi"] is None
    assert result["band"] == "NO_DATA"
    assert "components" in result
    assert "warnings" in result
    assert result["components"]["score_drift"]["available"] is False
    assert result["components"]["execution_drift"]["runtime_only"] is True


def test_stability_index_tracks_drift_and_runtime_components():
    db_path = _test_db_path()
    init_stability_store(db_path)

    # Baseline window: healthy samples.
    for _ in range(10):
        record_signal_event(
            engine="engine_a",
            score=0.85,
            max_score=1.0,
            passed=True,
            expected_prob=0.8,
            feature_map={"trend": 0.8, "momentum": 0.7},
            db_path=db_path,
        )
        record_execution_event(
            engine="engine_a",
            success=True,
            score=0.85,
            max_score=1.0,
            expected_prob=0.8,
            slippage_bps=1.0,
            db_path=db_path,
        )
        record_outcome_event(
            engine="engine_a",
            won=True,
            expected_prob=0.8,
            expectancy_r=0.9,
            score=0.85,
            max_score=1.0,
            db_path=db_path,
        )

    # Current window: degraded samples.
    for _ in range(50):
        record_signal_event(
            engine="engine_a",
            score=0.35,
            max_score=1.0,
            passed=False,
            expected_prob=0.7,
            feature_map={"trend": 0.2, "momentum": 0.1},
            db_path=db_path,
        )
        record_execution_event(
            engine="engine_a",
            success=False,
            score=0.35,
            max_score=1.0,
            expected_prob=0.7,
            slippage_bps=18.0,
            db_path=db_path,
        )
        record_outcome_event(
            engine="engine_a",
            won=False,
            expected_prob=0.7,
            expectancy_r=-0.6,
            score=0.35,
            max_score=1.0,
            db_path=db_path,
        )

    join_stability_audit_queue(timeout=30.0)

    result = get_signal_stability_index(engine="engine_a", db_path=db_path)

    assert result["components"]["score_drift"]["available"] is True
    assert result["components"]["pass_rate_drift"]["available"] is True
    assert result["components"]["calibration_drift"]["available"] is True
    assert result["components"]["feature_drift"]["available"] is True
    assert result["components"]["execution_drift"]["available"] is True
    assert result["components"]["expectancy_drift"]["available"] is True
    assert result["components"]["score_drift"]["score"] < 60
    assert result["components"]["execution_drift"]["score"] < 60
    assert result["band"] in ("ORANGE", "RED", "YELLOW")
