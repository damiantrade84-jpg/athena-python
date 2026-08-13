"""Tests for athena_ai.ai_review_payload_builder.

The builder produces an ADDITIVE strategy_layer block. It must:
  * carry rejected_models with reasons (FIX 1)
  * attach a BOUNDED raw OHLCV window plus a Vision-routing TODO marker (FIX 2)
  * embed self-describing facts (FIX 3)
  * surface the schema metadata so AI knows the verdict shape (FIX 4/5)
  * never imply or contain execution authority
"""

from __future__ import annotations

from typing import Any

from athena_ai.ai_review_payload_builder import (
    build_strategy_layer,
    render_strategy_block_for_prompt,
)
from athena_ai.strategy_classifier import classify_strategy


def _bar(o: float, h: float, l: float, c: float, v: float = 100.0, t: int = 0) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "time": t}


def _engine_a_ctx(**overrides) -> dict[str, Any]:
    base = {
        "asset_group": "crypto",
        "direction": "LONG",
        "regime": "balance",
        "confluence_score": 6,
        "max_score_override": 10,
        "atr": {"atr_chart_tf": 1.5, "atr_value": 1.5},
        "structure_context": {"profile": {"poc": 100.0, "vah": 101.0, "val": 99.0}},
        "ema_levels": {"ema50": 99.5},
    }
    base.update(overrides)
    return base


# ----- basic shape ------------------------------------------------------------


def test_layer_includes_all_required_top_level_keys() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    for key in (
        "schema_version",
        "schema_metadata",
        "playbook_universe",
        "loaded_playbook_ids",
        "price_action_facts",
        "candle_understanding",
        "candidate_models",
        "rejected_models",
        "classification_warnings",
        "engines_considered",
        "evaluation_style",
        "evaluation_timeframe",
        "raw_ohlcv_window",
        "vision_routing_todo",
        "advisory_only_notice",
    ):
        assert key in layer, f"missing top-level key: {key}"


def test_candle_understanding_in_strategy_layer() -> None:
    bars = [_bar(100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1) for i in range(10)]
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx(), ohlcv_window=bars)
    cu = layer.get("candle_understanding") or {}
    assert cu.get("enabled") is True
    assert cu.get("facts", {}).get("candle_anatomy") is not None


def test_prompt_includes_candle_read_order() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    text = render_strategy_block_for_prompt(layer)
    assert "CANDLE UNDERSTANDING read order" in text
    assert "Regime gate" in text


def test_strategy_layer_preserves_selected_style_independent_of_chart_tf() -> None:
    explicit = build_strategy_layer(
        engine_a_ctx=_engine_a_ctx(analyze_style="intraday"),
        timeframe="D1",
    )
    auto = build_strategy_layer(
        engine_a_ctx=_engine_a_ctx(),
        timeframe="D1",
    )

    assert explicit["evaluation_style"] == "intraday"
    assert auto["evaluation_style"] == "intraday"
    assert explicit["evaluation_timeframe"] == "D1"
    assert "style=intraday timeframe=D1" in render_strategy_block_for_prompt(explicit)


def test_advisory_notice_states_read_only() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    notice = layer["advisory_only_notice"].lower()
    assert "read-only" in notice or "advisory" in notice
    assert "not" in notice and "execute" in notice


# ----- FIX 1 ------------------------------------------------------------------


def test_rejected_models_carry_reasons_when_engines_missing_in_full() -> None:
    """Construct a scenario where a playbook has zero declared engines available
    and verify the rejection carries a reason string."""
    layer = build_strategy_layer(
        engine_a_ctx=_engine_a_ctx(),
        engine_b_summary={"available": False},
        engine_d_summary={"available": False},
    )
    for r in layer["rejected_models"]:
        assert isinstance(r.get("rejection_reason"), str)
        assert r["rejection_reason"].strip()


def test_classifier_does_not_list_rejected_models_as_candidates_without_hints() -> None:
    result = classify_strategy(
        price_action_facts={
            "setup_candidates": {"hints": []},
            "wick_event": {"type": "none"},
            "breakout_state": {"state": "unknown"},
            "profile_location": {"location": "unknown", "poc": None},
            "profile_vp_context": {"enabled": True},
            "volume_behavior": {"state": "normal"},
        },
        engine_a_summary={"regime": "balance", "confluence_score": 1, "max_score": 10},
        engine_b_summary={"available": False},
        engine_d_summary={"available": False},
        asset_group="crypto",
        direction="LONG",
    )

    candidate_ids = {c["id"] for c in result["candidate_models"]}
    rejected_ids = {r["id"] for r in result["rejected_models"]}
    assert not (candidate_ids & rejected_ids)


# ----- FIX 2 ------------------------------------------------------------------


def test_ohlcv_window_is_bounded_to_limit() -> None:
    bars = [_bar(100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1, t=i) for i in range(200)]
    layer = build_strategy_layer(
        engine_a_ctx=_engine_a_ctx(),
        ohlcv_window=bars,
        ohlcv_limit=50,
    )
    window = layer["raw_ohlcv_window"]
    assert window["limit"] == 50
    assert window["bar_count"] == 50
    assert len(window["bars"]) == 50
    # most-recent bars retained
    assert window["bars"][-1]["time"] == 199


def test_vision_routing_todo_marker_is_present() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    assert "vision_primary" in layer["vision_routing_todo"]


def test_ohlcv_window_empty_when_no_bars_supplied() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx(), ohlcv_window=None)
    window = layer["raw_ohlcv_window"]
    assert window["bars"] == []
    assert window["bar_count"] == 0


# ----- FIX 3 ------------------------------------------------------------------


def test_facts_in_layer_are_self_describing() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    facts = layer["price_action_facts"]
    assert isinstance(facts, dict)
    for key, value in facts.items():
        if key.startswith("_") or key in ("profile_vp_context", "candle_understanding"):
            continue  # diagnostic metadata / nested block, not a flat fact
        assert isinstance(value, dict), f"fact {key} is not a dict"
        assert "confidence" in value, f"fact {key} missing confidence (FIX 3)"


# ----- FIX 4 / FIX 5 ----------------------------------------------------------


def test_schema_metadata_exposes_loaded_ids_and_classifier_agreement_enum() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    meta = layer["schema_metadata"]
    assert "matched_strategy_model_choices" in meta
    assert set(layer["loaded_playbook_ids"]).issubset(
        set(meta["matched_strategy_model_choices"])
    )
    enums = meta["enums"]
    assert "classifier_agreement" in enums
    assert "AGREE" in enums["classifier_agreement"]
    assert "OVERRIDE_ADDED" in enums["classifier_agreement"]
    assert "OVERRIDE_REJECTED" in enums["classifier_agreement"]


def test_playbook_universe_carries_ai_review_rules() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    universe = layer["playbook_universe"]
    assert universe, "playbook universe must be non-empty"
    for entry in universe:
        assert entry["id"]
        assert entry["ai_review_rule"], (
            f"playbook {entry['id']} must surface its ai_review_rule to the AI"
        )


# ----- prompt rendering -------------------------------------------------------


def test_prompt_rendering_lists_rejected_with_reasons() -> None:
    layer = build_strategy_layer(
        engine_a_ctx=_engine_a_ctx(),
        engine_b_summary={"available": False},
        engine_d_summary={"available": False},
    )
    text = render_strategy_block_for_prompt(layer)
    assert "STRATEGY PLAYBOOK LAYER" in text
    assert "Rejected playbooks" in text
    if layer["rejected_models"]:
        first = layer["rejected_models"][0]
        assert first["id"] in text
        # at least the first reason should appear in the rendered text
        assert first["rejection_reason"].split(";")[0][:30] in text


def test_prompt_rendering_includes_classifier_agreement_enum() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    text = render_strategy_block_for_prompt(layer)
    assert "classifier_agreement" in text
    assert "OVERRIDE_ADDED" in text
    assert "matched_strategy_model" in text


def test_prompt_rendering_marks_vision_routing_todo() -> None:
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    text = render_strategy_block_for_prompt(layer)
    assert "vision_primary" in text


# ----- isolation --------------------------------------------------------------


def test_builder_does_not_mutate_engine_a_ctx() -> None:
    ctx = _engine_a_ctx()
    snapshot = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ctx.items()}
    build_strategy_layer(engine_a_ctx=ctx)
    assert ctx == snapshot, "builder must not mutate the engine_a_ctx it receives"


def test_builder_does_not_imply_execution_capability() -> None:
    """No execution-related field should leak into the strategy layer."""
    layer = build_strategy_layer(engine_a_ctx=_engine_a_ctx())
    forbidden = (
        "execute",
        "order",
        "place",
        "submit",
        "broker",
        "sl_price",
        "tp_price",
    )
    for top_key in layer.keys():
        for token in forbidden:
            assert token not in top_key.lower(), (
                f"strategy_layer top-level key '{top_key}' looks execution-related"
            )
