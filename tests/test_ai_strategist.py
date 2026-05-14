from __future__ import annotations

from ai_strategist import strategist_morning_brief, strategist_pre_trade_check, weekly_strategy_retrospective


def test_strategist_brief_shape():
    out = strategist_morning_brief("all")
    assert out["schema_version"] == "strategist_brief.v1"
    assert isinstance(out["data_warnings"], list)


def test_pre_trade_check_is_advisory_and_cannot_execute():
    out = strategist_pre_trade_check({"direction": "LONG", "risk": {"rr": None}})
    assert out["schema_version"] == "strategist_pre_trade.v1"
    assert out["advisory_only"] is True
    assert out["execution_allowed"] is False


def test_pre_trade_check_requires_refresh_for_stale_market_intelligence():
    out = strategist_pre_trade_check(
        {
            "direction": "LONG",
            "risk": {"rr": 2.0, "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
            "data_quality": {"freshness_status": "fresh"},
            "market_intelligence": {
                "freshness_status": "stale",
                "warnings": ["macro stale"],
                "macro_regime": {"calendar_within_72h": []},
            }
        }
    )

    assert out["verdict"] == "DATA_INSUFFICIENT"
    assert out["recommended_action"] == "data_refresh_required"
    assert out["macro_conflict"] is True
    assert out["execution_allowed"] is False


def test_pre_trade_check_surfaces_calendar_event_risk():
    out = strategist_pre_trade_check(
        {
            "direction": "LONG",
            "risk": {"rr": 2.0, "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
            "data_quality": {"freshness_status": "fresh"},
            "market_intelligence": {
                "freshness_status": "partial",
                "warnings": [],
                "macro_regime": {"calendar_within_72h": [{"name": "NFP"}]},
            },
        }
    )

    assert out["event_risk_conflict"] is True
    assert out["advisory_only"] is True
    assert out["execution_allowed"] is False


def test_weekly_retrospective_never_auto_applies():
    out = weekly_strategy_retrospective()
    assert out["schema_version"] == "strategist_weekly.v1"
    assert out["do_not_auto_apply"] is True
