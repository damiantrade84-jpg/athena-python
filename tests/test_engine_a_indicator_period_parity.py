"""Engine A indicator parity / differentiation test.

Proves the v12 design contract for Engine A indicators:

  (1) RAW periods are UNIVERSAL — ema21/ema50/ema200/rsi/macd are identical
      across every ENGINE_A_KNOWN_SCORE_GROUP for the same candle series.
      This confirms no silent per-group period override has crept in and that
      the universal-period design (research-supported) is intact.

  (2) The DIFFERENTIATION layer fires per group where it is supposed to:
        (2a) RSI overbought/oversold *bounds* resolve differently by group
             (e.g. crypto_doge 82/18 vs forex_majors 70/30) and therefore the
             overbought/oversold *flag* on a fixed RSI reading differs.
        (2b) NORMALIZATION_LOOKBACK differs by asset_type, so z-scored factor
             values differ across asset classes on the same series.

If (1) fails -> a per-group period override leaked in (regression).
If (2a)/(2b) fail -> the differentiation layer is NOT firing (the real bug the
operator was worried about), even though raw periods look fine.

Run:  py -m pytest tests/test_engine_a_indicator_period_parity.py -v
"""

from __future__ import annotations

import inspect
import math

import pytest

from config import CONFIG
from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from indicators import _calc_indicator_bundle, calc_indicators_with_normalized
from scoring import _resolve_class_keyed

# Map each score group to the asset_type the normalizer keys off.
_GROUP_TO_ASSET = {
    "forex_majors": "forex",
    "forex_crosses": "forex",
    "forex_exotics": "forex",
    "forex_other": "forex",
    "crypto_btc": "crypto",
    "crypto_eth": "crypto",
    "crypto_doge": "crypto",
    "crypto_alt_majors": "crypto",
    "crypto_other": "crypto",
    "precious_trackers": "commodity",
    "energy_oil": "commodity",
    "nat_gas": "commodity",
    "copper": "commodity",
    "pgm_metals": "commodity",
    "base_metals": "commodity",
    "softs": "commodity",
    "commodity_other": "commodity",
    "us_indices_trackers": "index",
    "eu_indices": "index",
    "asian_indices": "index",
    "index_other": "index",
    "us_stock_single": "stock",
    "bond_tlt": "stock",
    "smallcap_em_etf": "stock",
    "stock_other": "stock",
    "unknown": "stock",
}

_RAW_SNAP_KEYS = ("ema21", "ema50", "ema200", "rsi", "macdLine", "macdHist", "adx")

# Groups with intentional per-class RSI period overrides (see ENGINE_A_RSI_PERIOD_BY_CLASS).
_PERIOD_OVERRIDE_GROUPS = frozenset({"bond_tlt"})


def _make_candles(n: int = 520) -> list:
    """Deterministic synthetic series long enough for ema200 + normalization."""
    candles = []
    price = 100.0
    for i in range(n):
        drift = 0.05
        osc = math.sin(i / 9.0) * 0.8
        close = price + drift * i + osc
        high = close + 0.6
        low = close - 0.6
        open_ = close - osc * 0.3
        candles.append(
            {
                "open": round(open_, 5),
                "high": round(high, 5),
                "low": round(low, 5),
                "close": round(close, 5),
                "vol": 1000 + (i % 7) * 50,
                "time": i,
            }
        )
    return candles


CANDLES = _make_candles()


def _raw_snap(snap: dict) -> dict:
    return {k: snap.get(k) for k in _RAW_SNAP_KEYS}


def _raw_snaps_equal(ref_val, val) -> bool:
    if ref_val is None and val is None:
        return True
    if ref_val is None or val is None:
        return False
    return abs(float(val) - float(ref_val)) <= 1e-9


# ════════════════════════════════════════════════════════════════════════════
# (1) RAW PERIOD PARITY — raw indicator series must be identical everywhere
# ════════════════════════════════════════════════════════════════════════════


def test_raw_periods_are_universal_via_bundle():
    """_calc_indicator_bundle takes NO score_group — calling it repeatedly must
    yield identical raw series. This is the structural proof that raw periods
    cannot vary by group in v12 (the function has no group parameter)."""
    sig = inspect.signature(_calc_indicator_bundle)
    assert "score_group" not in sig.parameters, (
        "_calc_indicator_bundle gained a score_group parameter — raw periods "
        "are no longer guaranteed universal. Re-audit the period design."
    )
    ref = _calc_indicator_bundle(CANDLES)
    again = _calc_indicator_bundle(CANDLES)
    for key in ("ema21", "ema50", "ema200", "rsi"):
        assert ref[key] == again[key], f"raw {key} not reproducible"
    assert ref["macd"]["macd"] == again["macd"]["macd"], "raw macd line not reproducible"


def test_raw_snap_identical_across_asset_types():
    """Same candles, different asset_type only: RAW snap must match.

    Raw series does not depend on asset_type; this dedupes by asset_type to
    exercise each distinct class without redundant calls."""
    ref = _raw_snap(
        calc_indicators_with_normalized(CANDLES, asset_type="forex")["snap"]
    )

    mismatches = []
    seen_assets = set()
    for group in sorted(ENGINE_A_KNOWN_SCORE_GROUPS):
        asset = _GROUP_TO_ASSET[group]
        if asset in seen_assets:
            continue
        seen_assets.add(asset)
        snap = _raw_snap(
            calc_indicators_with_normalized(CANDLES, asset_type=asset)["snap"]
        )
        for key, ref_val in ref.items():
            val = snap.get(key)
            if not _raw_snaps_equal(ref_val, val):
                mismatches.append(f"{group}/{asset}: raw {key} {val} != ref {ref_val}")
    assert not mismatches, "Raw periods differ across asset types (regression!):\n" + "\n".join(
        mismatches
    )


def test_raw_snap_identical_across_all_score_groups_production_path():
    """Production call shape: asset_type + score_group for every known group.

    Matches athena.py analyze path when ENGINE_A_SCORE_GROUP_ADJUSTMENTS_ENABLED
    is on. Fails if per-group period maps diverge without an intentional change.
    """
    ref_group = "forex_majors"
    ref_asset = _GROUP_TO_ASSET[ref_group]
    ref = _raw_snap(
        calc_indicators_with_normalized(
            CANDLES, asset_type=ref_asset, score_group=ref_group
        )["snap"]
    )

    mismatches = []
    for group in sorted(ENGINE_A_KNOWN_SCORE_GROUPS):
        if group in _PERIOD_OVERRIDE_GROUPS:
            continue
        asset = _GROUP_TO_ASSET[group]
        snap = _raw_snap(
            calc_indicators_with_normalized(
                CANDLES, asset_type=asset, score_group=group
            )["snap"]
        )
        for key, ref_val in ref.items():
            val = snap.get(key)
            if not _raw_snaps_equal(ref_val, val):
                mismatches.append(f"{group}/{asset}: raw {key} {val} != ref {ref_val}")
    assert not mismatches, (
        "Raw periods differ across score groups on production path (regression!):\n"
        + "\n".join(mismatches)
    )


def test_bond_tlt_rsi_period_override():
    """bond_tlt uses a slower RSI (21) per ENGINE_A_RSI_PERIOD_BY_CLASS."""
    from factor_scoring import _resolve_rsi_period

    assert _resolve_rsi_period("bond_tlt", "stock") == 21
    assert _resolve_rsi_period("forex_majors", "forex") == 14


# ════════════════════════════════════════════════════════════════════════════
# (2a) RSI BOUNDS DIFFERENTIATION — must resolve per group
# ════════════════════════════════════════════════════════════════════════════


def _resolve_rsi_bounds(group: str):
    asset = _GROUP_TO_ASSET[group]
    return _resolve_class_keyed(
        CONFIG.get("RSI_BOUNDS", {}), group, asset, {"ob": 70, "os": 30}
    )


def test_rsi_bounds_resolve_and_vary_by_group():
    """RSI_BOUNDS must NOT collapse to one universal value."""
    bounds = {g: _resolve_rsi_bounds(g) for g in ENGINE_A_KNOWN_SCORE_GROUPS}
    distinct = {(b["ob"], b["os"]) for b in bounds.values()}
    assert len(distinct) > 1, (
        "RSI_BOUNDS collapsed to a single value across all groups — "
        "differentiation layer is NOT firing. Bounds seen: " + str(distinct)
    )
    assert _resolve_rsi_bounds("crypto_doge") == {"ob": 82, "os": 18}
    assert _resolve_rsi_bounds("forex_majors") == {"ob": 70, "os": 30}
    assert _resolve_rsi_bounds("crypto_btc")["ob"] == 80


def test_overbought_flag_differs_on_fixed_rsi_reading():
    """A fixed RSI reading must flag overbought differently across groups."""
    rsi_reading = 78.0
    forex_ob = float(_resolve_rsi_bounds("forex_majors")["ob"])
    crypto_ob = float(_resolve_rsi_bounds("crypto_doge")["ob"])
    forex_flag = rsi_reading >= forex_ob
    crypto_flag = rsi_reading >= crypto_ob
    assert forex_flag is True, "RSI 78 should be overbought for forex_majors (ob=70)"
    assert crypto_flag is False, "RSI 78 should NOT be overbought for crypto_doge (ob=82)"
    assert forex_flag != crypto_flag, "overbought flag failed to differentiate by group"


# ════════════════════════════════════════════════════════════════════════════
# (2b) NORMALIZATION DIFFERENTIATION — z-scores differ by asset class
# ════════════════════════════════════════════════════════════════════════════


def test_normalization_lookback_differs_by_asset():
    """NORMALIZATION_LOOKBACK must be differentiated across asset classes."""
    nl = CONFIG.get("NORMALIZATION_LOOKBACK", {})
    assert nl, "NORMALIZATION_LOOKBACK missing from config"
    lookbacks = {nl.get("forex"), nl.get("crypto"), nl.get("stock"), nl.get("index")}
    lookbacks.discard(None)
    assert len(lookbacks) > 1, (
        "NORMALIZATION_LOOKBACK collapsed to one value across asset classes — "
        "normalization differentiation is NOT firing."
    )


def test_normalized_values_differ_across_asset_classes():
    """Same candles, different asset_type -> different z-scored factor values."""
    forex = calc_indicators_with_normalized(CANDLES, asset_type="forex")["snap"]
    crypto = calc_indicators_with_normalized(CANDLES, asset_type="crypto")["snap"]

    candidate_fields = [
        k
        for k in forex.keys()
        if k.endswith("_z") or k.endswith("_pct") or k.startswith("realized_vol")
    ]
    assert candidate_fields, "no normalized fields found in snap — normalizer output missing"

    differed = []
    for f in candidate_fields:
        fv, cv = forex.get(f), crypto.get(f)
        if fv is None or cv is None:
            continue
        try:
            if abs(float(fv) - float(cv)) > 1e-9:
                differed.append(f"{f}: forex={fv} crypto={cv}")
        except (TypeError, ValueError):
            continue
    assert differed, (
        "No normalized factor differed between forex(400) and crypto(300) lookbacks "
        "on identical candles — normalization differentiation is NOT firing. "
        "Checked fields: " + ", ".join(candidate_fields)
    )
