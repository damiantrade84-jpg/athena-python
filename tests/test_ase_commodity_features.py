"""Commodity ASE feed, benchmark, and enriched-feature contracts."""

from __future__ import annotations

import math

import numpy as np

from athena_ase.data.ptis import PTIS_VALUE_DTYPE
from athena_ase.features import build as feature_build
from athena_ase.features.build import FeatureBuildContext, cot_asset_for_symbol
from athena_ase.instruments import instrument_by_symbol
from athena_ase.inference.predict import _build_context
from athena_ase.signals.arbitrate import Candidate
from athena_research.ase.train import enriched_training_mask


class _CommodityStore:
    def __init__(self, *, stale_real_yield: bool = False):
        self.stale_real_yield = stale_real_yield

    def asof(self, series_id: str, decision_time_ms: int, n: int = 1):
        size = max(2, n)
        rows = np.empty(size, dtype=PTIS_VALUE_DTYPE)
        step = 86_400_000 if series_id.startswith(("FRED:", "CFTC:")) else 3_600_000
        end = decision_time_ms - step
        if self.stale_real_yield and series_id == "FRED:DFII10:rate":
            end = decision_time_ms - 20 * 86_400_000
        rows["value_time"] = np.arange(size, dtype=np.int64) * step + end - (size - 1) * step
        rows["available_time"] = rows["value_time"]
        rows["value"] = np.linspace(100.0, 101.0, size)
        rows["revision"] = 0
        rows["ingest_time"] = rows["available_time"]
        return rows


def _context() -> FeatureBuildContext:
    return FeatureBuildContext(
        symbol="GC=F",
        family="commodity",
        subclass="cfd",
        horizon="intraday",
        decision_time_ms=1_800_000_000_000,
        direction=1,
        agreement_count=1,
        conflict_flag=False,
        benchmark_symbol="USDX",
        cot_asset="XAU",
    )


def test_commodity_universe_uses_explicit_benchmarks_and_xauzar_cot():
    expected = {
        "GC=F": "USDX",
        "XAUZAR": "GC=F",
        "SI=F": "GC=F",
        "CL=F": "BZ=F",
        "BZ=F": "CL=F",
        "NATGAS": "CL=F",
    }
    assert {symbol: instrument_by_symbol(symbol).benchmark for symbol in expected} == expected
    assert cot_asset_for_symbol("XAUZAR") == "XAU"


def test_commodity_enriched_features_require_fresh_macro_context():
    context = _context()
    row = feature_build.build_features_for_candidate(
        context, enriched=True, store=_CommodityStore()
    )
    for name in (
        "usd_index_ret_21",
        "us_real_yield_level",
        "us_real_yield_delta_21",
        "us_nominal_yield_level",
        "us_nominal_yield_delta_21",
        "quote_rate_level",
    ):
        assert math.isfinite(float(row[name]))
    assert context.enriched_missing_feeds == []

    stale_context = _context()
    feature_build.build_features_for_candidate(
        stale_context, enriched=True, store=_CommodityStore(stale_real_yield=True)
    )
    assert "us_real_yield" in stale_context.enriched_missing_feeds


def test_commodity_enriched_training_mask_includes_macro_contract():
    import pandas as pd

    row = {
        "cot_pct": 0.5,
        "cot_delta_4w": 1.0,
        "usd_index_ret_21": 0.01,
        "us_real_yield_level": 1.5,
        "us_real_yield_delta_21": 0.1,
        "us_nominal_yield_level": 4.0,
        "us_nominal_yield_delta_21": 0.2,
        "quote_rate_level": 3.5,
    }
    frame = pd.DataFrame([row, {**row, "usd_index_ret_21": float("nan")}])
    assert enriched_training_mask(frame, "commodity").tolist() == [True, False]


def test_beta_alignment_rejects_nonmatching_timestamps():
    store = _CommodityStore()
    left = store.asof("MT5:GCF:H1:close", 1_800_000_000_000, 80)
    right = left.copy()
    right["value_time"] += 30 * 60 * 1000
    assert math.isnan(feature_build._rolling_beta_aligned(left, right))


def test_legacy_commodity_artifact_context_keeps_old_feature_contract():
    instrument = instrument_by_symbol("XAUZAR")
    assert instrument is not None
    candidate = Candidate(
        instrument="XAUZAR",
        family="commodity",
        horizon="swing",
        decision_time_ms=1_800_000_000_000,
        bar_index=100,
        direction=1,
    )
    context = _build_context(
        candidate, instrument, commodity_macro_enabled=False
    )
    assert context.benchmark_symbol == "XAUUSD"
    assert context.cot_asset == ""
    assert context.commodity_macro_enabled is False
