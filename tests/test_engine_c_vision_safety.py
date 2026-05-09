"""Tests for Engine C Chart Vision fail-closed behavior.

Confirmed bugs covered:
- Vision parser defaults missing confirms_direction to True (should be False).
- Vision parser defaults missing rating to MODERATE (should be AVOID/block).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from engine_c import apply_vision


class TestVisionFailClosed:
    """Malformed or empty Vision payloads must block, not confirm."""

    @pytest.fixture(autouse=True)
    def _patch_arbitrate_ai(self, monkeypatch):
        """Make arbitrate_ai return a clean pass-through so parser logic is exercised."""
        monkeypatch.setattr(
            "ai_reconciliation.arbitrate_ai",
            lambda *_a, **_kw: {
                "source_states": {
                    "vision": {"category": "ok", "raw": "TEST"}
                }
            },
        )

    def _base_consensus(self, **overrides):
        base = {
            "trade": True,
            "direction": "LONG",
            "conviction": 0.65,
            "tier": "MEDIUM",
            "sizing_override": 0.65,
            "sl": 1.0900,
            "tp": 1.1300,
            "entry": 1.1000,
            "rr": 2.0,
            "verdict": "ALIGNED",
            "decision_state": "execute",
        }
        base.update(overrides)
        return base

    def test_vision_empty_structured_blocks_trade(self):
        """Empty structured dict must not be treated as confirmation."""
        consensus = self._base_consensus()
        vision = {"structured": {}}
        result = apply_vision(consensus, vision)
        # Missing confirms_direction should default to False -> direction contradiction
        # which forces rating to CONTRADICTS -> trade=False
        assert result["trade"] is False
        assert result["vision_action"] in ("override", "contradict")

    def test_vision_missing_confirms_direction_defaults_false(self):
        """Structured dict without confirms_direction key must not confirm."""
        consensus = self._base_consensus()
        vision = {"structured": {"rating": "MODERATE"}}
        result = apply_vision(consensus, vision)
        # Since confirms_direction is missing, it should default False,
        # which converts rating to CONTRADICTS
        assert result["trade"] is False
        assert result["vision_rating"] == "CONTRADICTS"

    def test_vision_missing_rating_defaults_avoid(self):
        """Vision text with no recognizable rating tokens must block."""
        consensus = self._base_consensus()
        vision = {"structured": {}, "analysis": "Some generic text without rating keywords"}
        result = apply_vision(consensus, vision)
        # No rating found -> should default to AVOID (fail-closed)
        assert result["trade"] is False
        assert result["vision_action"] in ("override", "contradict")

    def test_vision_valid_confirm_preserves_trade(self):
        """Explicit confirm with confirms_direction=True should preserve trade."""
        consensus = self._base_consensus()
        vision = {
            "structured": {
                "rating": "STRONG",
                "confirms_direction": True,
            }
        }
        result = apply_vision(consensus, vision)
        assert result["trade"] is True
        assert result["vision_rating"] == "STRONG"
        assert result["vision_action"] == "confirm"

    def test_vision_explicit_avoid_blocks_trade(self):
        """Explicit AVOID must block regardless of consensus."""
        consensus = self._base_consensus()
        vision = {
            "structured": {
                "rating": "AVOID",
                "confirms_direction": True,
            }
        }
        result = apply_vision(consensus, vision)
        assert result["trade"] is False
        assert result["vision_rating"] == "AVOID"
        assert result["vision_action"] == "override"
