"""ETF pair universe must match scan/UI asset_class=etf filters."""

from __future__ import annotations

from tests.test_scalp_execution import _load_athena_module

_athena = _load_athena_module()
ALL_PAIRS = _athena.ALL_PAIRS
ETF_PAIRS = _athena.ETF_PAIRS


def _etf_scan_candidates():
    return [
        p
        for p in ALL_PAIRS
        if p.get("type") in ("etf", "etf_bond") and p.get("enabled", True)
    ]


def test_etf_pairs_use_etf_asset_types():
    assert ETF_PAIRS, "ETF_PAIRS must be defined"
    for pair in ETF_PAIRS:
        ptype = pair.get("type")
        assert ptype in ("etf", "etf_bond"), (
            f"{pair.get('display')} has type={ptype!r}; expected etf or etf_bond"
        )
    tlt = next(p for p in ETF_PAIRS if p.get("display") == "TLT")
    assert tlt.get("type") == "etf_bond"


def test_etf_asset_class_scan_includes_all_enabled_etfs():
    enabled_etf_displays = {
        p["display"] for p in ETF_PAIRS if p.get("enabled", True)
    }
    candidate_displays = {p["display"] for p in _etf_scan_candidates()}
    assert enabled_etf_displays <= candidate_displays
