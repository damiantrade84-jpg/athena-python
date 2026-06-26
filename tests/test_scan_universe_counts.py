"""Scan universe size contract — mirrors scanner.run_full_scan pair selection."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATHENA_PY = ROOT / "athena.py"
TOGGLE_STATE = ROOT / "toggle_state.json"


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


def _scan_counts(
    all_pairs: list[dict],
    jse_pairs: list[dict],
    *,
    asset_class: str | None,
) -> tuple[int, int]:
    disabled_jse = {
        p.get("symbol")
        for p in jse_pairs
        if not p.get("enabled", True)
    }
    candidates = [
        p
        for p in all_pairs
        if p.get("symbol") not in disabled_jse
    ]
    ac = asset_class.lower().strip() if asset_class else None
    if ac:
        if ac == "etf":
            candidates = [p for p in candidates if p.get("type") in ("etf", "etf_bond")]
        else:
            candidates = [p for p in candidates if p.get("type") == ac]
    active = [p for p in candidates if p.get("enabled", True)]
    return len(candidates), len(active)


def test_single_asset_class_rosters_are_21_pairs():
    """21/21 funnel output matches forex, commodity, or US stock filter."""
    all_pairs, jse = _load_all_pairs()
    for ac in ("forex", "commodity", "stock"):
        total, active = _scan_counts(all_pairs, jse, asset_class=ac)
        assert total == 21, ac
        assert active == 21, ac


def test_all_assets_scan_universe_is_larger_than_single_class():
    """All-assets scan (Signals panel default) covers full enabled roster."""
    all_pairs, jse = _load_all_pairs()
    total, active = _scan_counts(all_pairs, jse, asset_class=None)
    assert total >= 115
    assert active >= 115
    assert total > 21
    assert active > 21


def test_handle_scan_request_all_assets_uses_no_filter():
    from athena_app.services.scan_backtest_service import handle_scan_request

    captured: dict = {}

    def _fake_run(*, style: str = "auto", asset_class=None):
        captured["style"] = style
        captured["asset_class"] = asset_class
        return {"success": True, "totalPairs": 0, "activePairs": 0}

    handle_scan_request({"style": "auto"}, run_full_scan=_fake_run)
    assert captured["asset_class"] is None

    handle_scan_request({"style": "auto", "asset_class": ""}, run_full_scan=_fake_run)
    assert captured["asset_class"] == ""
