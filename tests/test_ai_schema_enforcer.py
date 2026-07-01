"""Focused tests for ai_schema_enforcer (Phase 0 schema hardening)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

import ai_schema_enforcer as ase


class _DemoSchema(BaseModel):
    decision: str = Field(description="VALID | INVALID | NO_TRADE")
    confidence: float = Field(ge=0, le=100)


@pytest.fixture
def schema():
    return _DemoSchema


def test_disabled_passes_raw_through_unchanged():
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = False
    try:
        raw = {"decision": "VALID", "confidence": 80, "extra": "field"}
        out, ok = ase.validate_or_safe_default(
            _DemoSchema,
            raw,
            surface="demo",
            safe_default={"decision": "NO_TRADE", "confidence": 0},
        )
        assert ok is True
        assert out is raw  # unchanged, same object
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)


def test_enabled_valid_dict_returns_parsed():
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = True
    try:
        raw = {"decision": "VALID", "confidence": 80}
        out, ok = ase.validate_or_safe_default(
            _DemoSchema,
            raw,
            surface="demo",
            safe_default={"decision": "NO_TRADE", "confidence": 0},
        )
        assert ok is True
        assert out["decision"] == "VALID"
        assert out["confidence"] == 80.0
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)


def test_enabled_invalid_dict_returns_safe_default():
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = True
    try:
        # confidence out of range
        raw = {"decision": "VALID", "confidence": 150}
        out, ok = ase.validate_or_safe_default(
            _DemoSchema,
            raw,
            surface="demo",
            safe_default={"decision": "NO_TRADE", "confidence": 0},
        )
        assert ok is False
        assert out["decision"] == "NO_TRADE"
        assert out["confidence"] == 0
        assert out.get("_schema_validation_failed") is True
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)


def test_enabled_missing_field_returns_safe_default():
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = True
    try:
        raw = {"decision": "VALID"}  # missing confidence
        out, ok = ase.validate_or_safe_default(
            _DemoSchema,
            raw,
            surface="demo",
            safe_default={"decision": "NO_TRADE", "confidence": 0},
        )
        assert ok is False
        assert out["decision"] == "NO_TRADE"
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)


def test_enabled_non_dict_returns_safe_default():
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = True
    try:
        out, ok = ase.validate_or_safe_default(
            _DemoSchema,
            None,
            surface="demo",
            safe_default={"decision": "NO_TRADE", "confidence": 0},
        )
        assert ok is False
        assert out["decision"] == "NO_TRADE"
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)


def test_strict_override_takes_precedence_over_config():
    """Caller can force strict=True even when config is off."""
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = False
    try:
        raw = {"decision": "VALID", "confidence": 150}  # invalid
        out, ok = ase.validate_or_safe_default(
            _DemoSchema,
            raw,
            surface="demo",
            safe_default={"decision": "NO_TRADE", "confidence": 0},
            strict=True,
        )
        assert ok is False
        assert out["decision"] == "NO_TRADE"
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)


def test_no_schema_passes_through():
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = True
    try:
        raw = {"decision": "VALID", "confidence": 80}
        out, ok = ase.validate_or_safe_default(
            None,
            raw,
            surface="demo",
            safe_default={"decision": "NO_TRADE", "confidence": 0},
        )
        assert ok is True
        assert out is raw
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)


def test_never_raises_on_bad_schema_model():
    ase.CONFIG["AI_STRICT_SCHEMA_ENFORCEMENT"] = True
    try:
        # Pass a non-schema object — must not raise; falls back to safe default
        out, ok = ase.validate_or_safe_default(
            "not_a_schema",  # type: ignore[arg-type]
            {"x": 1},
            surface="demo",
            safe_default={"fallback": True},
        )
        # Fail-closed: bad schema → safe default
        assert ok is False
        assert out.get("fallback") is True
    finally:
        ase.CONFIG.pop("AI_STRICT_SCHEMA_ENFORCEMENT", None)
