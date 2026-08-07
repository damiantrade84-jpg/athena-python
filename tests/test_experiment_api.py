"""Focused API contracts for Tuning Lab score-group isolation."""

from __future__ import annotations

from flask import Flask

import athena_experiment.api as experiment_api
from athena_experiment.worker import _timeframe_result_summary


_EURUSD = {"symbol": "EURUSD", "display": "EUR/USD", "type": "forex"}


def test_worker_timeframe_summary_tracks_full_route_and_dynamic_engine_a_trend():
    collapsed = _timeframe_result_summary(
        {
            "regimeTf": "D1",
            "biasTf": "H4",
            "structureTf": "H4",
            "setupTf": "H1",
            "triggerTf": "M15",
            "executionTf": "M5",
        },
        "A",
    )
    assert collapsed["roles"] == {
        "regime": "D1",
        "bias": "H4",
        "structure": "H4",
        "setup": "H1",
        "trigger": "M15",
        "execution": "M5",
    }
    assert collapsed["trendTimeframes"] == ["D1", "H4"]

    distinct = _timeframe_result_summary(
        {
            "regimeTf": "D1",
            "biasTf": "H4",
            "structureTf": "H1",
            "setupTf": "M30",
            "triggerTf": "M15",
            "executionTf": "M15",
        },
        "A",
    )
    assert distinct["trendTimeframes"] == ["D1", "H4", "H1"]


def test_tuning_lab_structure_knob_targets_authoritative_policy_group():
    from athena_experiment.overlay import build_overlay

    # Without a pair, score-group alias maps forex_majors → forex_majors_standard.
    overlay = build_overlay(
        "A",
        "forex_majors",
        "intraday",
        {"tf_role.structure": "H1"},
    )

    assert overlay == {
        "ENGINE_TF_ROLE_OVERRIDES": {
            "ENABLED": True,
            "BY_GROUP": {
                "forex_majors_standard": {
                    "intraday": {"structure": "H1"}
                }
            }
        }
    }


def test_tuning_lab_tf_roles_use_symbol_aware_policy_group():
    """GBPUSD must write forex_majors_fast, not the score-group alias standard."""
    from athena_experiment.overlay import build_overlay

    overlay = build_overlay(
        "A",
        "forex_majors",
        "intraday",
        {"tf_role.setup": "H1", "tf_role.trigger": "M15"},
        pair={"display": "GBP/USD", "type": "forex", "symbol": "GBPUSD"},
    )
    by_group = overlay["ENGINE_TF_ROLE_OVERRIDES"]["BY_GROUP"]
    assert "forex_majors_fast" in by_group
    assert "forex_majors_standard" not in by_group
    assert by_group["forex_majors_fast"]["intraday"] == {
        "setup": "H1",
        "trigger": "M15",
    }
    assert overlay["ENGINE_TF_ROLE_OVERRIDES"]["ENABLED"] is True

    nas = build_overlay(
        "B",
        "us_indices_trackers",
        "intraday",
        {"tf_role.structure": "H1"},
        pair={"display": "NASDAQ-100", "type": "index", "symbol": "NAS100"},
    )
    assert "equity_index_fast" in nas["ENGINE_TF_ROLE_OVERRIDES"]["BY_GROUP"]
    assert "equity_index_standard" not in nas["ENGINE_TF_ROLE_OVERRIDES"]["BY_GROUP"]


def _client():
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(
        experiment_api.create_experiment_blueprint(
            all_pairs_provider=lambda: [dict(_EURUSD)],
            json_safe=lambda value: value,
        )
    )
    return app.test_client()


def test_defaults_ignores_asset_filter_group_and_resolves_pair_score_group(monkeypatch):
    import athena_experiment.defaults as experiment_defaults

    captured: dict[str, object] = {}

    def fake_resolve(engine, pair, group, style):
        captured.update(engine=engine, pair=pair, group=group, style=style)
        return {"indicator.momentum.roc_weight": 0.0}

    monkeypatch.setattr(experiment_defaults, "resolve_current_values", fake_resolve)

    response = _client().get(
        "/api/experiment/defaults",
        query_string={
            "engine": "A",
            "symbol": "EUR/USD",
            "style": "intraday",
            # Legacy UI value: an asset filter, never a scoring key.
            "group": "Forex",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["group"] == "forex_majors"
    assert captured["group"] == "forex_majors"


def test_run_builds_overlay_for_canonical_pair_group_and_echoes_tested_knobs(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(symbol, engine, style, overlay, timeout_sec, replay_days=None):
        captured.update(
            symbol=symbol,
            engine=engine,
            style=style,
            overlay=overlay,
            timeout_sec=timeout_sec,
        )
        return {
            "symbol": "EURUSD",
            "pair": "EUR/USD",
            "baseline": {"totalTrades": 10, "sqn": 0.1},
            "variant": {"totalTrades": 11, "sqn": 0.2},
            "overlayApplied": overlay,
        }

    monkeypatch.setattr(experiment_api, "run_worker_subprocess", fake_run)
    knobs = {"indicator.momentum.roc_weight": 1.0}

    response = _client().post(
        "/api/experiment/run",
        json={
            "engine": "A",
            "symbol": "EUR/USD",
            "style": "intraday",
            # This old broad label previously produced inert BY_GROUP.forex.
            "group": "Forex",
            "overlay": knobs,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["group"] == "forex_majors"
    assert payload["knobs"] == knobs
    assert captured["overlay"] == {
        "ENGINE_A_V3_MOMENTUM_BLEND": {
            "BY_GROUP": {"forex_majors": {"ROC_WEIGHT": 1.0}}
        }
    }


def test_engine_b_run_uses_the_same_canonical_pair_group(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(symbol, engine, style, overlay, timeout_sec, replay_days=None):
        captured.update(engine=engine, overlay=overlay)
        return {
            "symbol": "EURUSD",
            "pair": "EUR/USD",
            "baseline": {"totalTrades": 4},
            "variant": {"totalTrades": 5},
        }

    monkeypatch.setattr(experiment_api, "run_worker_subprocess", fake_run)

    response = _client().post(
        "/api/experiment/run",
        json={
            "engine": "B",
            "symbol": "EURUSD",
            "group": "Forex",
            "knobs": {"indicator.engine_b.momentum_oscillator_weight": 0.4},
        },
    )

    assert response.status_code == 200
    assert response.get_json()["group"] == "forex_majors"
    assert captured == {
        "engine": "B",
        "overlay": {
            "ENGINE_B_WEIGHTED_SCORING": {
                "COMPONENT_WEIGHTS_BY_GROUP": {
                    "forex_majors": {"momentum_oscillator_confluence": 0.4}
                }
            }
        },
    }


def test_push_rejects_mismatched_group_and_writes_canonical_tested_group(monkeypatch):
    import athena_experiment.push as experiment_push

    writes: list[dict] = []

    def fake_push(overlay):
        writes.append(overlay)
        return {"written": True}

    monkeypatch.setattr(experiment_push, "push_overlay_to_local_config", fake_push)
    base_payload = {
        "engine": "A",
        "symbol": "EURUSD",
        "style": "intraday",
        "confirm": True,
        "knobs": {"indicator.momentum.roc_weight": 0.5},
    }

    mismatch = _client().post(
        "/api/experiment/push",
        json={**base_payload, "group": "Forex"},
    )
    assert mismatch.status_code == 422
    assert "does not match" in mismatch.get_json()["error"]
    assert writes == []

    response = _client().post(
        "/api/experiment/push",
        json={**base_payload, "group": "forex_majors"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["group"] == "forex_majors"
    assert writes == [
        {
            "ENGINE_A_V3_MOMENTUM_BLEND": {
                "BY_GROUP": {"forex_majors": {"ROC_WEIGHT": 0.5}}
            }
        }
    ]


def test_run_rejects_non_object_knob_payload_before_starting_worker(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(experiment_api, "run_worker_subprocess", fake_run)

    response = _client().post(
        "/api/experiment/run",
        json={"engine": "A", "symbol": "EURUSD", "overlay": ["not", "an", "object"]},
    )

    assert response.status_code == 422
    assert response.get_json()["error"] == "overlay/knobs must be an object"
    assert called is False


def test_engine_b_sl_clamp_knobs_target_the_consumed_clamp_config_surface():
    """Regression: engine_b.gate.min_sl_atr / max_sl_atr previously wrote to
    NAKED_ENGINE.score_group_overrides — a surface no consumer reads (the live
    clamp paths read ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES, see
    market_structure.py clamp/tighten/TP1-retry sites). The knobs were inert."""
    from athena_experiment.overlay import build_overlay

    overlay = build_overlay(
        "B",
        "forex_majors",
        "intraday",
        {"engine_b.gate.min_sl_atr": 0.9, "engine_b.gate.max_sl_atr": 2.5},
    )

    assert overlay == {
        "ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES": {
            "forex_majors": {"min_sl_atr": 0.9, "max_sl_atr": 2.5}
        }
    }


def test_engine_b_gate_defaults_resolve_clamps_from_consumed_surface(monkeypatch):
    """The defaults endpoint must show the genuinely live clamp values:
    group override -> ENGINE_B_MIN/MAX_SL_ATR_DEFAULT -> code fallback."""
    from config import CONFIG
    import athena_experiment.defaults as experiment_defaults

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_B_ATR_SL_CLAMP_SCORE_GROUP_OVERRIDES",
        {"forex_majors": {"min_sl_atr": 0.9}},
    )
    monkeypatch.setitem(CONFIG, "ENGINE_B_MIN_SL_ATR_DEFAULT", 0.75)
    monkeypatch.setitem(CONFIG, "ENGINE_B_MAX_SL_ATR_DEFAULT", 3.0)
    monkeypatch.setitem(CONFIG, "NAKED_ENGINE", {})

    values = experiment_defaults._engine_b_gate_values(_EURUSD, "forex_majors", "intraday")

    assert values["engine_b.gate.min_sl_atr"] == 0.9
    assert values["engine_b.gate.max_sl_atr"] == 3.0
    # Numeric gates resolve through the live style profile (shipped intraday
    # defaults from engine_b_snapshot) — real numbers, not "no override".
    assert values["engine_b.gate.min_rr"] == 1.2
    assert values["engine_b.gate.min_score"] == 4.0
    assert values["engine_b.gate.min_room_atr"] == 0.7
    assert values["engine_b.gate.macro_required"] is False


def test_engine_b_gate_defaults_layer_group_style_override_over_profile(monkeypatch):
    """An explicit score_group_overrides row must win over the style-profile
    default — that merged value is what the UI shows as currently live."""
    from config import CONFIG
    import athena_experiment.defaults as experiment_defaults

    monkeypatch.setitem(
        CONFIG,
        "NAKED_ENGINE",
        {"score_group_overrides": {"forex_majors": {"intraday": {"min_rr": 1.5, "macro_required": True}}}},
    )

    values = experiment_defaults._engine_b_gate_values(_EURUSD, "forex_majors", "intraday")

    assert values["engine_b.gate.min_rr"] == 1.5
    assert values["engine_b.gate.macro_required"] is True
    # Untouched fields still show the style-profile default.
    assert values["engine_b.gate.min_score"] == 4.0


def test_engine_b_run_passes_replay_days_to_worker_and_validates_range(monkeypatch):
    """The UI lookback select caps the Engine B replay window; both runs share
    it, so the A/B stays fair. Out-of-range values are rejected before the
    worker starts."""
    captured: dict[str, object] = {}

    def fake_run(symbol, engine, style, overlay, timeout_sec, replay_days=None):
        captured["replay_days"] = replay_days
        return {"symbol": "EURUSD", "pair": "EUR/USD", "baseline": {}, "variant": {}}

    monkeypatch.setattr(experiment_api, "run_worker_subprocess", fake_run)

    ok = _client().post(
        "/api/experiment/run",
        json={"engine": "B", "symbol": "EURUSD", "replayDays": 60, "overlay": {}},
    )
    assert ok.status_code == 200
    assert captured["replay_days"] == 60

    bad = _client().post(
        "/api/experiment/run",
        json={"engine": "B", "symbol": "EURUSD", "replayDays": 10, "overlay": {}},
    )
    assert bad.status_code == 422


def test_engine_b_min_score_rejects_implausible_values():
    """Engine B min_score lives on a ~0-8 gate-points scale (shipped profiles
    use 3.0-4.0); the knob must reject percentage-scale input like 50."""
    import pytest

    from athena_experiment.overlay import OverlayValidationError, build_overlay

    with pytest.raises(OverlayValidationError):
        build_overlay("B", "forex_majors", "intraday", {"engine_b.gate.min_score": 50.0})
    build_overlay("B", "forex_majors", "intraday", {"engine_b.gate.min_score": 4.5})


def test_engine_b_run_uses_the_extended_timeout(monkeypatch):
    """Engine B per-bar NakedEngine replay is far slower than Engine A; its
    runs timed out at the Engine A budget. The API must pass the larger
    per-engine timeout to the worker subprocess."""
    captured: dict[str, object] = {}

    def fake_run(symbol, engine, style, overlay, timeout_sec, replay_days=None):
        captured["timeout_sec"] = timeout_sec
        return {
            "symbol": "EURUSD",
            "pair": "EUR/USD",
            "baseline": {"totalTrades": 1},
            "variant": {"totalTrades": 1},
        }

    monkeypatch.setattr(experiment_api, "run_worker_subprocess", fake_run)

    response = _client().post(
        "/api/experiment/run",
        json={"engine": "B", "symbol": "EURUSD", "overlay": {}},
    )

    assert response.status_code == 200
    assert captured["timeout_sec"] == experiment_api._RUN_TIMEOUT_SEC_ENGINE_B
    assert experiment_api._RUN_TIMEOUT_SEC_ENGINE_B > experiment_api._RUN_TIMEOUT_SEC


def test_backtest_summary_exposes_the_ui_metric_contract():
    """The Tuning Lab tiles read totalTrades/winRate/profitFactor/expectancy/
    sqn/totalR/maxDrawdownR straight off the worker result, with winRate on a
    0-100 scale. A rename (e.g. expectancyR) or a 0-1 ratio silently breaks
    the panel."""
    import pytest

    from engine_a_v3.backtest import _summarize

    trades = [
        {"resultR": 1.0, "direction": "LONG"},
        {"resultR": -0.5, "direction": "SHORT"},
        {"resultR": 2.0, "direction": "LONG"},
    ]
    summary = _summarize(
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        "intraday",
        trades,
        0,
    )

    for key in (
        "totalTrades",
        "winRate",
        "profitFactor",
        "expectancy",
        "sqn",
        "totalR",
        "maxDrawdownR",
    ):
        assert key in summary, f"missing UI metric key: {key}"
    assert 0.0 <= summary["winRate"] <= 100.0
    assert summary["winRate"] == pytest.approx(2 / 3 * 100, rel=1e-2)
    assert summary["expectancy"] == pytest.approx((1.0 - 0.5 + 2.0) / 3, abs=1e-4)
