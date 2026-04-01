import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from forensic_observability import build_forensic_summary


def test_forensic_summary_has_ui_and_telegram_contract():
    db_path = os.path.join(os.path.dirname(__file__), "_forensics_test.db")
    try:
        payload = build_forensic_summary(
            guardian={"passed": True, "failures": [], "total_checks": 3},
            shield={"circuit_breaker_open": False, "failure_count": 0},
            divergence={
                "total_checks": 10,
                "critical_count": 0,
                "warning_count": 1,
                "divergence_count": 1,
                "pairs_affected": ["EUR/USD"],
            },
            db_path=db_path,
            lookback_hours=24,
        )
    finally:
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except Exception:
            pass

    assert payload["version"] == 1
    assert payload["overall"] in {"healthy", "watch", "warning", "critical"}
    assert isinstance(payload.get("generated_at"), str)

    views = payload.get("views", {})
    assert set(views.keys()) == {"signal_truth", "drift_degradation", "execution_hygiene"}
    for key in ("signal_truth", "drift_degradation", "execution_hygiene"):
        view = views[key]
        assert view["status"] in {"healthy", "watch", "warning", "critical"}
        assert isinstance(view["score"], (int, float))
        assert isinstance(view.get("issues", []), list)
        assert isinstance(view.get("metrics", {}), dict)

    brief = payload.get("telegram_brief", [])
    assert isinstance(brief, list)
    assert len(brief) == 3
    assert any("Signal Truth" in line for line in brief)
    assert any("Drift/Degradation" in line for line in brief)
    assert any("Execution Hygiene" in line for line in brief)
