"""ALL_PAIRS universe coverage — source, score_group, and config contract matrix."""

from __future__ import annotations

import ast
import json
import os
import re
from collections import Counter
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

import pytest

from config import CONFIG
from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from scoring import get_pair_score_group, get_score_threshold
from tools.engine_b_group_contracts import validate_engine_b_group_coverage

ROOT = Path(__file__).resolve().parents[1]
ATHENA_PY = ROOT / "athena.py"
TOGGLE_STATE = ROOT / "toggle_state.json"

_VALID_SOURCES = frozenset({"mt5", "eodhd", "binance", "polygon", "yfinance"})


def _load_all_pairs() -> tuple[list[dict], list[dict]]:
    src = ATHENA_PY.read_text(encoding="utf-8")
    lists: dict[str, list[dict]] = {}
    for name in (
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    ):
        m = re.search(rf"{name}\s*=\s*\[", src)
        if not m:
            continue
        start = m.end() - 1
        depth = 0
        for i, ch in enumerate(src[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    lists[name] = ast.literal_eval(src[start : i + 1])
                    break
    all_pairs: list[dict] = []
    for key in (
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    ):
        all_pairs.extend(lists.get(key, []))
    if TOGGLE_STATE.is_file():
        state = json.loads(TOGGLE_STATE.read_text(encoding="utf-8"))
        for p in all_pairs:
            disp = p.get("display")
            if disp in state:
                p["enabled"] = bool(state[disp])
    return all_pairs, lists.get("JSE_PAIRS", [])


def _scan_candidate_pairs(all_pairs: list[dict], jse_pairs: list[dict]) -> list[dict]:
    disabled_jse = {
        p.get("symbol")
        for p in jse_pairs
        if not p.get("enabled", True)
    }
    return [
        p
        for p in all_pairs
        if p.get("symbol") not in disabled_jse
    ]


def _active_pairs(candidates: list[dict]) -> list[dict]:
    return [p for p in candidates if p.get("enabled", True)]


@pytest.fixture(scope="module")
def pair_universe() -> tuple[list[dict], list[dict], list[dict]]:
    all_pairs, jse = _load_all_pairs()
    candidates = _scan_candidate_pairs(all_pairs, jse)
    active = _active_pairs(candidates)
    return all_pairs, candidates, active


def test_every_pair_has_required_metadata(pair_universe):
    all_pairs, _, _ = pair_universe
    for pair in all_pairs:
        display = pair.get("display")
        symbol = pair.get("symbol")
        ptype = pair.get("type")
        source = pair.get("source")
        assert display, f"pair missing display: {pair}"
        assert symbol, f"{display}: missing symbol"
        assert ptype, f"{display}: missing type"
        assert source in _VALID_SOURCES, f"{display}: invalid source {source!r}"


def test_active_non_jse_pairs_do_not_resolve_to_unknown(pair_universe):
    _, candidates, active = pair_universe
    unknown_active = [
        p.get("display")
        for p in active
        if get_pair_score_group(p) == "unknown"
    ]
    assert not unknown_active, f"active pairs mapped to unknown: {unknown_active}"


def test_every_active_score_group_has_engine_a_threshold(pair_universe):
    _, _, active = pair_universe
    thresholds = CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {}
    groups_seen = {get_pair_score_group(p) for p in active}
    missing = sorted(g for g in groups_seen if g not in thresholds)
    assert not missing, f"active groups missing ENGINE_A_SCORE_GROUP_THRESHOLDS: {missing}"


def test_every_active_score_group_has_engine_b_override(pair_universe):
    _, _, active = pair_universe
    report = validate_engine_b_group_coverage()
    assert report["ok"], report

    overrides = (CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {}
    groups_seen = {get_pair_score_group(p) for p in active} - {"unknown"}
    missing = sorted(g for g in groups_seen if g not in overrides)
    assert not missing, f"active groups missing NAKED_ENGINE.score_group_overrides: {missing}"


def test_active_pairs_cover_major_score_groups(pair_universe):
    """Each non-unknown canonical group should have at least one active pair."""
    _, _, active = pair_universe
    hist = Counter(get_pair_score_group(p) for p in active)
    empty_groups = sorted(
        g for g in ENGINE_A_KNOWN_SCORE_GROUPS
        if g != "unknown" and hist.get(g, 0) == 0
    )
    # Catch-all groups with no dedicated active pair today (safety-net routing only)
    allowed_empty = {"forex_other", "stock_other"}
    unexpected_empty = [g for g in empty_groups if g not in allowed_empty]
    assert not unexpected_empty, f"score groups with zero active pairs: {unexpected_empty}"


def test_scan_candidate_count_matches_universe_contract(pair_universe):
    _, candidates, active = pair_universe
    assert len(candidates) >= 115
    assert len(active) >= 115


def test_score_threshold_resolves_for_all_active_pairs(pair_universe):
    _, _, active = pair_universe
    for pair in active:
        thr = get_score_threshold(pair)
        assert isinstance(thr, float)
        assert thr > 0, f"{pair.get('display')}: threshold {thr}"
