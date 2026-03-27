"""Focused tests for current signal debate payload semantics."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from signal_debate import _signal_max_score


def test_signal_max_score_prefers_explicit_value():
    assert _signal_max_score({"maxScore": 1.0, "confluenceScore": 2.5}) == 1.0


def test_signal_max_score_uses_current_one_point_scale_when_score_is_unit_interval():
    assert _signal_max_score({"confluenceScore": 0.8}) == 1.0


def test_signal_max_score_falls_back_to_current_three_point_engine_a_scale():
    assert _signal_max_score({"confluenceScore": 1.8}) == 3.0
