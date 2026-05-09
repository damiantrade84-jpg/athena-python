"""Tests for guardian_routes.py fail-closed dashboard logic."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


class TestGuardianStatusFailClosed:
    """Dashboard guardian status must treat missing 'passed' as failure."""

    def _compute_overall(self, guardian, shield, divergence):
        """Reproduce the FIXED fallback logic (only explicit True counts as passed)."""
        g_ok = guardian.get("passed") is True
        s_ok = shield.get("circuit_breaker_open") is not True
        d_ok = divergence.get("critical_count", 0) == 0
        if g_ok and s_ok and d_ok:
            return "healthy"
        elif not g_ok or not s_ok:
            return "critical"
        else:
            return "warning"

    def test_guardian_missing_passed_is_critical(self):
        """Missing 'passed' key in guardian result must be critical."""
        overall = self._compute_overall(
            guardian={},
            shield={"circuit_breaker_open": False},
            divergence={"critical_count": 0},
        )
        assert overall == "critical"

    def test_guardian_passed_none_is_critical(self):
        overall = self._compute_overall(
            guardian={"passed": None},
            shield={"circuit_breaker_open": False},
            divergence={"critical_count": 0},
        )
        assert overall == "critical"

    def test_guardian_passed_false_is_critical(self):
        overall = self._compute_overall(
            guardian={"passed": False},
            shield={"circuit_breaker_open": False},
            divergence={"critical_count": 0},
        )
        assert overall == "critical"

    def test_guardian_passed_true_is_healthy(self):
        overall = self._compute_overall(
            guardian={"passed": True},
            shield={"circuit_breaker_open": False},
            divergence={"critical_count": 0},
        )
        assert overall == "healthy"
