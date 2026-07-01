"""Focused tests for ai_vision_deprecation (Phase 0 vision unification)."""

from __future__ import annotations

import pytest

import ai_vision_deprecation as avd


def test_disabled_returns_payload_unchanged():
    avd.CONFIG["AI_VISION_LEGACY_ROUTE_DEPRECATED"] = False
    try:
        payload = {"analysis": "x", "structured": {}}
        out = avd.stamp_vision_legacy_deprecation(payload)
        assert out is payload
        assert "deprecated" not in out
        assert "useInstead" not in out
        assert "migrationNote" not in out
    finally:
        avd.CONFIG.pop("AI_VISION_LEGACY_ROUTE_DEPRECATED", None)


def test_enabled_adds_deprecation_fields():
    avd.CONFIG["AI_VISION_LEGACY_ROUTE_DEPRECATED"] = True
    try:
        payload = {"analysis": "x", "structured": {}}
        out = avd.stamp_vision_legacy_deprecation(payload)
        assert out is payload
        assert out["deprecated"] is True
        assert out["useInstead"] == "/api/ai/chart-review"
        assert "migrationNote" in out and "unified" in out["migrationNote"]
        # Original fields preserved
        assert out["analysis"] == "x"
    finally:
        avd.CONFIG.pop("AI_VISION_LEGACY_ROUTE_DEPRECATED", None)


def test_enabled_does_not_overwrite_existing_fields():
    avd.CONFIG["AI_VISION_LEGACY_ROUTE_DEPRECATED"] = True
    try:
        payload = {"deprecated": False, "useInstead": "/custom"}
        out = avd.stamp_vision_legacy_deprecation(payload)
        assert out["deprecated"] is False
        assert out["useInstead"] == "/custom"
    finally:
        avd.CONFIG.pop("AI_VISION_LEGACY_ROUTE_DEPRECATED", None)


def test_non_dict_payload_returned_unchanged():
    avd.CONFIG["AI_VISION_LEGACY_ROUTE_DEPRECATED"] = True
    try:
        assert avd.stamp_vision_legacy_deprecation(None) is None
        assert avd.stamp_vision_legacy_deprecation("string") == "string"
        assert avd.stamp_vision_legacy_deprecation([1, 2]) == [1, 2]
    finally:
        avd.CONFIG.pop("AI_VISION_LEGACY_ROUTE_DEPRECATED", None)


def test_never_raises_on_non_dict():
    """The helper must never raise even on weird input types."""
    avd.CONFIG["AI_VISION_LEGACY_ROUTE_DEPRECATED"] = True
    try:
        # Various weird inputs should not raise
        assert avd.stamp_vision_legacy_deprecation(None) is None
        assert avd.stamp_vision_legacy_deprecation(42) == 42
        assert avd.stamp_vision_legacy_deprecation(True) is True
    finally:
        avd.CONFIG.pop("AI_VISION_LEGACY_ROUTE_DEPRECATED", None)
