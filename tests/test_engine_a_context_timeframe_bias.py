"""Regression: v3 ``per_tf`` trend-coherence entries resolve to direction labels.

The v3 quant scorer emits ``trendCoherence.per_tf`` entries keyed by uppercase
timeframe with a ``direction`` field (commit 9ba5971e). ``_timeframe_bias`` must
read that label rather than stringifying the whole entry dict into the AI
review prompt context.
"""

from __future__ import annotations

import ai_review.engine_a_context as eac


def test_timeframe_bias_reads_v3_per_tf_direction_labels():
    ctx = {
        "factor_diagnostics": {
            "trendCoherence": {
                "per_tf": {
                    "D1": {"direction": "UP", "signal": 0.5, "weight": 0.4},
                    "H4": {"direction": "DOWN", "signal": -0.3, "weight": 0.35},
                }
            }
        },
        "regime": "trending",
    }

    bias = eac._timeframe_bias(ctx)

    assert bias["D1"] == "UP"
    assert bias["H4"] == "DOWN"
    # H1 has no per_tf entry: falls back to the regime base, never a dict dump.
    assert bias["H1"] == "trending"


def test_timeframe_bias_still_prefers_legacy_label_keys():
    ctx = {
        "factor_diagnostics": {
            "trendCoherence": {
                "per_tf": {
                    "D1": {"label": "FLAT", "direction": "UP"},
                }
            }
        },
        "regime": "ranging",
    }

    assert eac._timeframe_bias(ctx)["D1"] == "FLAT"
