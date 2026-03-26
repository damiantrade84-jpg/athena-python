from engine_c import ENGINE_C_AB_WEIGHTS, ENGINE_C_META_BLEND, _blend_consensus_weights


def test_blended_ab_weights_fall_back_to_base_and_move_within_bound():
    base = ENGINE_C_AB_WEIGHTS["TRENDING"]

    no_data = _blend_consensus_weights(base, None)
    assert no_data == base

    strongly_favor_a = _blend_consensus_weights(
        base,
        {
            "engine_a": 1.0,
            "engine_b": 0.0,
            "engine_c": 0.0,
            "scalp": 0.0,
        },
    )

    expected_a = round((base["A"] * (1.0 - ENGINE_C_META_BLEND)) + ENGINE_C_META_BLEND, 4)
    expected_b = round(base["B"] * (1.0 - ENGINE_C_META_BLEND), 4)

    assert strongly_favor_a["A"] == expected_a
    assert strongly_favor_a["B"] == expected_b
    assert strongly_favor_a["A"] > base["A"]
    assert strongly_favor_a["B"] < base["B"]
    assert abs(strongly_favor_a["A"] - base["A"]) <= 0.08
