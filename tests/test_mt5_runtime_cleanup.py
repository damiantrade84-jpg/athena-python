"""Regression checks for MT5-only non-crypto live runtime cleanup."""

import ast
import re
from pathlib import Path


def _parse_pair_lists():
    text = (Path(__file__).resolve().parents[1] / "athena.py").read_text(encoding="utf-8")
    names = [
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    ]
    parsed = {}
    for name in names:
        match = re.search(rf"{name}\s*=\s*\[", text)
        assert match, f"{name} not found in athena.py"
        start = match.end() - 1
        depth = 0
        end = None
        for idx, ch in enumerate(text[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        assert end is not None, f"{name} list did not terminate"
        parsed[name] = ast.literal_eval(text[start:end])
    return parsed


def test_all_enabled_tradable_non_crypto_pairs_use_mt5():
    parsed = _parse_pair_lists()
    live_pairs = []
    for name, pairs in parsed.items():
        if name == "CRYPTO_PAIRS":
            continue
        live_pairs.extend(p for p in pairs if p.get("enabled", True))

    assert live_pairs
    assert all(p.get("source") == "mt5" for p in live_pairs)


def test_jse_pairs_are_excluded_from_live_runtime():
    parsed = _parse_pair_lists()
    jse_pairs = parsed["JSE_PAIRS"]

    assert jse_pairs
    assert all(not p.get("enabled", True) for p in jse_pairs)


def test_no_active_live_eodhd_pairs_remain_in_pair_definitions():
    parsed = _parse_pair_lists()
    active_eodhd = []
    for name, pairs in parsed.items():
        if name == "CRYPTO_PAIRS":
            continue
        active_eodhd.extend(
            p for p in pairs if p.get("enabled", True) and p.get("source") == "eodhd"
        )

    assert active_eodhd == []


def test_candle_builder_seed_helper_targets_crypto_or_eodhd_only():
    text = (Path(__file__).resolve().parents[1] / "athena.py").read_text(encoding="utf-8")
    assert 'and (p.get("type") == "crypto" or p.get("source") == "eodhd")' in text


def test_scanner_no_longer_attaches_server_indicators():
    text = (Path(__file__).resolve().parents[1] / "scanner.py").read_text(encoding="utf-8")
    assert "serverIndicators" not in text


def test_backtest_pair_checks_mt5_source_before_generic_forex_branch():
    text = (Path(__file__).resolve().parents[1] / "backtest_runner.py").read_text(encoding="utf-8")
    assert text.index('elif pair["source"] == "mt5":') < text.index('elif _ptype == "forex":')
