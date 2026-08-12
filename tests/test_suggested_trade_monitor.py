"""Tests for alert-only suggested trade monitor."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_review.suggested_trade_plan import plan_playbook_warnings, sanitize_suggested_trade_plan
from suggested_trade_monitor import (
    DEFAULT_ACTIVE_PATH,
    DEFAULT_EVENTS_PATH,
    add_watch,
    build_watch_from_flag,
    cancel_watch,
    clear_terminal_watches,
    evaluate_all_watches_once,
    evaluate_trigger,
    evaluate_watches,
    get_runner_status,
    monitor_config,
    save_active_watches,
    start_suggested_trade_runner_once,
    validate_flag_payload,
)


def _valid_plan(**overrides):
    plan = {
        "schemaVersion": "suggested_trade_plan.v1",
        "armable": True,
        "source": "ai_chart_review",
        "symbol": "EURUSD",
        "direction": "SHORT",
        "action": "WAIT_FOR_LEVEL",
        "triggerType": "ACCEPTANCE_BELOW",
        "level": 1.085,
        "entryTf": "M15",
        "expiresInSeconds": 600,
    }
    plan.update(overrides)
    return plan


def test_sanitize_accepts_valid_level_plan():
    raw = {"suggestedTradePlan": _valid_plan()}
    out = sanitize_suggested_trade_plan(raw, source="ai_chart_review", symbol="EURUSD")
    assert out is not None
    assert out["armable"] is True
    assert out["level"] == 1.085


def test_sanitize_rejects_malformed_plan():
    raw = {"suggestedTradePlan": {"direction": "SIDEWAYS", "action": "WAIT_FOR_LEVEL", "triggerType": "ACCEPTANCE_ABOVE"}}
    out = sanitize_suggested_trade_plan(raw, source="ai_chart_review", symbol="EURUSD")
    assert out is not None
    assert out["armable"] is False


def test_sanitize_accepts_engine_b_hotbench_zone_plan():
    raw = {
        "suggestedTradePlan": _valid_plan(
            source="engine_b_hotbench",
            action="WAIT_FOR_ZONE",
            triggerType="PULLBACK_TO_ZONE",
            zoneLow=1.082,
            zoneHigh=1.084,
            invalidateAbove=1.086,
            level=None,
        )
    }
    out = sanitize_suggested_trade_plan(raw, source="engine_b_hotbench", symbol="EURUSD")
    assert out is not None
    assert out["armable"] is True
    assert out["source"] == "engine_b_hotbench"
    assert out["zoneLow"] == pytest.approx(1.082)
    assert out["zoneHigh"] == pytest.approx(1.084)


def test_sanitize_engine_b_hotbench_zone_plan_requires_real_zone():
    raw = {
        "suggestedTradePlan": _valid_plan(
            source="engine_b_hotbench",
            action="WAIT_FOR_ZONE",
            triggerType="PULLBACK_TO_ZONE",
            zoneLow=None,
            zoneHigh=None,
            level=None,
        )
    }
    out = sanitize_suggested_trade_plan(raw, source="engine_b_hotbench", symbol="EURUSD")
    assert out is not None
    assert out["armable"] is False
    assert "zone required" in str(out.get("reason") or "").lower()


def test_validate_flag_rejects_entry_now():
    payload = {
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(action="ENTRY_NOW"),
    }
    _, err = validate_flag_payload(payload)
    assert err is not None
    assert "ENTRY_NOW" in err or "cannot be watched" in err or "not watchable" in err or "invalid" in err.lower()


def test_validate_flag_rejects_engine_b_hotbench_entry_now():
    payload = {
        "symbol": "EURUSD",
        "source": "engine_b_hotbench",
        "suggestedTradePlan": _valid_plan(
            source="engine_b_hotbench",
            action="ENTRY_NOW",
            triggerType="ACCEPTANCE_ABOVE",
        ),
    }
    _, err = validate_flag_payload(payload)
    assert err is not None
    assert "ENTRY_NOW" in err or "cannot be watched" in err


def test_validate_flag_rejects_no_trade():
    payload = {
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(action="NO_TRADE"),
    }
    _, err = validate_flag_payload(payload)
    assert err is not None


def test_evaluate_trigger_acceptance_above():
    watch = {"trigger_type": "ACCEPTANCE_ABOVE", "level": 100.0}
    reached, _ = evaluate_trigger(watch, latest_close=101.0)
    assert reached is True


def test_evaluate_trigger_acceptance_below():
    watch = {"trigger_type": "ACCEPTANCE_BELOW", "level": 100.0}
    reached, _ = evaluate_trigger(watch, latest_close=99.0)
    assert reached is True


def test_evaluate_trigger_zone_touch():
    watch = {"trigger_type": "PULLBACK_TO_ZONE", "zone_low": 84.33, "zone_high": 84.84}
    reached, _ = evaluate_trigger(
        watch,
        latest_candle={"h": 84.5, "l": 84.4},
    )
    assert reached is True


def test_expiry_marks_watch_expired(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_expiry_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"
    monkeypatch.setattr("suggested_trade_monitor.DEFAULT_ACTIVE_PATH", active)
    monkeypatch.setattr("suggested_trade_monitor.DEFAULT_EVENTS_PATH", events)

    validated, err = validate_flag_payload({
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(expiresInSeconds=1),
        "createdAt": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
    })
    assert err is None
    watch, add_err = add_watch(validated, active_path=active, events_path=events)
    assert add_err is None
    assert watch is not None

    result = evaluate_watches(active_path=active, events_path=events)
    assert result["updated"] >= 1
    watches = json.loads(active.read_text(encoding="utf-8"))["watches"]
    assert watches[0]["status"] == "EXPIRED"


def test_monitor_module_has_no_execution_imports():
    source = Path(__file__).resolve().parents[1] / "suggested_trade_monitor.py"
    text = source.read_text(encoding="utf-8")
    forbidden = (
        "from execution",
        "import execution",
        "risk_engine",
        "guardian",
        "mt5_executor",
        "bybit_executor",
        "quick_execute",
        "scalp_execute",
    )
    for token in forbidden:
        assert token not in text


def test_alert_only_cannot_be_disabled_by_config():
    cfg = {"SUGGESTED_TRADE_MONITOR": {"ALERT_ONLY": False}}
    mcfg = monitor_config(cfg)
    assert mcfg["ALERT_ONLY"] is True


def test_runner_status_defaults_to_frontend_poll_when_not_started():
    status = get_runner_status({})
    assert status["alertOnly"] is True
    assert status["mode"] in ("frontend_poll", "manual_only", "background_thread")
    assert status["active"] in (True, False)


def test_evaluate_all_watches_once_marks_acceptance_above(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_above_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"

    validated, err = validate_flag_payload({
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(triggerType="ACCEPTANCE_ABOVE", level=1.08),
    })
    assert err is None
    watch, add_err = add_watch(validated, active_path=active, events_path=events)
    assert add_err is None

    def _fetch(_symbol, _tf, limit=5):
        return {"candles": [{"c": 1.081}]}

    result = evaluate_all_watches_once(
        active_path=active,
        events_path=events,
        fetch_candles_fn=_fetch,
    )
    assert result["updated"] >= 1
    watches = json.loads(active.read_text(encoding="utf-8"))["watches"]
    assert watches[0]["status"] == "READY_FOR_REVIEW"


def test_start_runner_once_is_idempotent():
    started = start_suggested_trade_runner_once(cfg={"SUGGESTED_TRADE_MONITOR": {"ENABLED": False}})
    assert started is False


def test_evaluate_invalidation_marks_watch_invalidated():
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_inv_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"

    validated, err = validate_flag_payload({
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(invalidateAbove=1.09),
    })
    assert err is None
    watch, add_err = add_watch(validated, active_path=active, events_path=events)
    assert add_err is None
    assert watch is not None

    def _fetch(_symbol, _tf, limit=5):
        return {"candles": [{"c": 1.091}]}

    result = evaluate_all_watches_once(
        active_path=active,
        events_path=events,
        fetch_candles_fn=_fetch,
    )
    assert result["updated"] >= 1
    watches = json.loads(active.read_text(encoding="utf-8"))["watches"]
    assert watches[0]["status"] == "INVALIDATED"
    event_rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e.get("type") == "watch_invalidated" for e in event_rows)


def test_evaluate_invalidation_leaves_watch_watching_inside_bounds():
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_inv_ok_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"

    validated, err = validate_flag_payload({
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(invalidateAbove=1.09),
    })
    assert err is None
    watch, add_err = add_watch(validated, active_path=active, events_path=events)
    assert add_err is None

    def _fetch(_symbol, _tf, limit=5):
        return {"candles": [{"c": 1.086}]}

    result = evaluate_all_watches_once(
        active_path=active,
        events_path=events,
        fetch_candles_fn=_fetch,
    )
    watches = json.loads(active.read_text(encoding="utf-8"))["watches"]
    assert watches[0]["status"] == "WATCHING"
    assert result["evaluated"] == 1


def test_clear_terminal_watches_removes_only_terminal():
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_clear_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"
    save_active_watches([
        {"watch_id": "a", "status": "WATCHING"},
        {"watch_id": "b", "status": "EXPIRED"},
        {"watch_id": "c", "status": "CANCELLED"},
        {"watch_id": "d", "status": "INVALIDATED"},
        {"watch_id": "e", "status": "READY_FOR_REVIEW"},
    ], active)

    result = clear_terminal_watches(active_path=active, events_path=events)
    assert result["removed"] == 3
    assert sorted(result["removed_ids"]) == ["b", "c", "d"]
    remaining = json.loads(active.read_text(encoding="utf-8"))["watches"]
    assert sorted(w["watch_id"] for w in remaining) == ["a", "e"]
    event_rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e.get("type") == "watches_cleared" for e in event_rows)


def test_plan_playbook_warnings_flags_m1_for_non_scalp():
    plan = sanitize_suggested_trade_plan(
        {"suggestedTradePlan": _valid_plan(entryTf="M1")},
        source="ai_chart_review",
        symbol="EURUSD",
    )
    assert plan is not None
    warnings = plan_playbook_warnings(plan)
    assert any("M1" in w for w in warnings)


def test_plan_playbook_warnings_flags_direction_trigger_mismatch():
    plan = sanitize_suggested_trade_plan(
        {"suggestedTradePlan": _valid_plan(direction="LONG", triggerType="ACCEPTANCE_BELOW")},
        source="ai_chart_review",
        symbol="EURUSD",
    )
    assert plan is not None
    warnings = plan_playbook_warnings(plan)
    assert any("mismatch" in w for w in warnings)


def test_plan_playbook_warnings_clean_for_coherent_plan():
    plan = sanitize_suggested_trade_plan(
        {"suggestedTradePlan": _valid_plan(invalidateBelow=1.08)},
        source="ai_chart_review",
        symbol="EURUSD",
    )
    assert plan is not None
    assert plan_playbook_warnings(plan) == []


def test_add_watch_stores_playbook_warnings():
    tmp_dir = Path(tempfile.mkdtemp(prefix="athena_suggested_warn_"))
    active = tmp_dir / "active.json"
    events = tmp_dir / "events.jsonl"

    validated, err = validate_flag_payload({
        "symbol": "EURUSD",
        "suggestedTradePlan": _valid_plan(entryTf="M1"),
    })
    assert err is None
    watch, add_err = add_watch(validated, active_path=active, events_path=events)
    assert add_err is None
    assert watch is not None
    stored = watch.to_dict()
    assert any("M1" in w for w in stored["playbook_warnings"])
