"""Tests for athena_ai.strategy_playbook_loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from athena_ai import strategy_playbook_loader as loader
from athena_ai.strategy_playbook_loader import (
    PlaybookValidationError,
    clear_cache,
    get_candidate_playbooks,
    get_enabled_playbooks,
    load_strategy_playbooks,
    loaded_playbook_ids,
)


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    clear_cache()
    yield
    clear_cache()


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "playbooks.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_loads_real_default_file_with_expected_ids() -> None:
    playbooks = load_strategy_playbooks()
    ids = {p["id"] for p in playbooks}
    expected = {
        "TREND_CONTINUATION_PULLBACK",
        "FAILED_BREAKOUT_REVERSAL",
        "LIQUIDITY_SWEEP_REVERSAL",
        "WICK_REJECTION_AT_KEY_LEVEL",
        "RETURN_TO_POC_MEAN_REVERSION",
        "BALANCE_CHOP_NO_TRADE",
    }
    assert expected.issubset(ids)


def test_loaded_playbook_ids_returns_tuple_of_strings() -> None:
    ids = loaded_playbook_ids()
    assert isinstance(ids, tuple)
    assert all(isinstance(p, str) and p for p in ids)
    assert "TREND_CONTINUATION_PULLBACK" in ids


def test_missing_required_field_raises_with_id(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        schema_version: 1
        playbooks:
          - id: BROKEN_MODEL
            enabled: true
            engines: [A]
            asset_groups: ["*"]
            description: missing required fields below
        """,
    )
    with pytest.raises(PlaybookValidationError) as exc_info:
        load_strategy_playbooks(path, use_cache=False)
    msg = str(exc_info.value)
    assert "BROKEN_MODEL" in msg
    # the first missing field encountered is named so the author can fix it
    assert "required_context" in msg or "confirmation" in msg


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        schema_version: 1
        playbooks:
          - id: SAME
            enabled: true
            engines: [A]
            asset_groups: ["*"]
            description: first
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
          - id: SAME
            enabled: true
            engines: [A]
            asset_groups: ["*"]
            description: dup
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
        """,
    )
    with pytest.raises(PlaybookValidationError, match="duplicate playbook id: SAME"):
        load_strategy_playbooks(path, use_cache=False)


def test_disabled_playbook_excluded_from_enabled(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        schema_version: 1
        playbooks:
          - id: ON_MODEL
            enabled: true
            engines: [A]
            asset_groups: ["*"]
            description: x
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
          - id: OFF_MODEL
            enabled: false
            engines: [A]
            asset_groups: ["*"]
            description: x
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
        """,
    )
    enabled_ids = {p["id"] for p in get_enabled_playbooks(path)}
    assert enabled_ids == {"ON_MODEL"}


def test_candidate_filter_respects_engines_available(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        schema_version: 1
        playbooks:
          - id: A_ONLY
            enabled: true
            engines: [A]
            asset_groups: ["*"]
            description: x
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
          - id: NEEDS_D
            enabled: true
            engines: [A, D]
            asset_groups: ["*"]
            description: x
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
        """,
    )
    cands = get_candidate_playbooks(
        asset_group="crypto",
        symbol="BTCUSDT",
        timeframe="H4",
        engines=["A"],
        path=path,
    )
    ids = {p["id"] for p in cands}
    assert ids == {"A_ONLY"}


def test_candidate_filter_asset_group_match(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        schema_version: 1
        playbooks:
          - id: CRYPTO_ONLY
            enabled: true
            engines: [A]
            asset_groups: ["crypto"]
            description: x
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
          - id: ANY
            enabled: true
            engines: [A]
            asset_groups: ["*"]
            description: x
            required_context: []
            confirmation: []
            invalid_if: []
            invalidation_logic: x
            target_logic: x
            ai_review_rule: x
        """,
    )
    fx_cands = {p["id"] for p in get_candidate_playbooks("forex", "EURUSD", "H4", ["A"], path=path)}
    assert fx_cands == {"ANY"}
    crypto_cands = {
        p["id"] for p in get_candidate_playbooks("crypto", "BTCUSDT", "H4", ["A"], path=path)
    }
    assert crypto_cands == {"CRYPTO_ONLY", "ANY"}


def test_cache_returns_independent_copies(tmp_path: Path) -> None:
    # mutating returned dict should never affect the next call's results
    path = loader._DEFAULT_PATH
    first = load_strategy_playbooks(path)
    first[0]["description"] = "mutated"
    second = load_strategy_playbooks(path)
    assert second[0]["description"] != "mutated"
