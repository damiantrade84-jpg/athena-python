"""Phase 1 — currency-state shadow layer is diagnostic only."""

from __future__ import annotations

import tempfile

from athena_ase.context.currency_state import (
    CurrencyState,
    compute_currency_states,
    pair_context,
)
from athena_ase.contracts import flat_signal
from athena_ase.data.ingest.common import append_ptis_rows, fred_series_id
from athena_ase.data.ptis import PTISStore, build_row
from athena_ase.instruments import instrument_by_symbol
from athena_ase.runtime.scan import _attach_fx_context_shadow


def _store_with_rates() -> PTISStore:
    store = PTISStore(tempfile.mkdtemp(prefix="ase_ctx_"))
    for fred_id, rate in (("ECBDFR", 4.0), ("DFF", 2.0)):
        sid = fred_series_id(fred_id)
        append_ptis_rows(
            store, sid, "FRED", [build_row(sid, 1_000, 2_000, rate)]
        )
    return store


def test_eurusd_carry_diff_positive():
    store = _store_with_rates()
    states = compute_currency_states(store, 1_000_000, "swing")
    ctx = pair_context(states, "EUR", "USD")
    assert ctx["carry_diff"] is not None and ctx["carry_diff"] > 0
    assert -1.0 <= float(ctx["bias"]) <= 1.0


def test_missing_rates_fall_back_to_trend_only():
    empty = PTISStore(tempfile.mkdtemp(prefix="ase_ctx_empty_"))
    states = compute_currency_states(empty, 1_000_000, "swing")
    ctx = pair_context(states, "EUR", "USD")
    assert ctx["carry_diff"] is None
    assert ctx["freshness"] == "degraded"
    assert -1.0 <= float(ctx["bias"]) <= 1.0
    # Manual states: missing one leg's rate also degrades.
    manual = {
        "EUR": CurrencyState(carry_rate=None, trend=0.2, pairs_used=3, freshness_ms=0),
        "USD": CurrencyState(carry_rate=2.0, trend=-0.1, pairs_used=3, freshness_ms=0),
    }
    ctx2 = pair_context(manual, "EUR", "USD")
    assert ctx2["carry_diff"] is None and ctx2["freshness"] == "degraded"


def test_attach_is_shadow_only():
    store = _store_with_rates()
    fx = flat_signal(
        instrument="EURUSD", family="forex", horizon="swing",
        model_version="t", gate_result={"ok": True},
    )
    btc = flat_signal(
        instrument="BTCUSDT", family="crypto", horizon="swing",
        model_version="t", gate_result={"ok": True},
    )
    inst_map = {
        "EURUSD": instrument_by_symbol("EURUSD"),
        "BTCUSDT": instrument_by_symbol("BTCUSDT"),
    }
    before = [(s.decisionStatus, s.direction, s.expectedNetR) for s in (fx, btc)]
    out = _attach_fx_context_shadow([fx, btc], store, "swing", inst_map, 1_000_000)
    after = [(s.decisionStatus, s.direction, s.expectedNetR) for s in out]
    assert before == after  # shadow guarantee
    assert out[0].fxContext is not None
    assert out[1].fxContext is None

    # Feature flag off: signals pass through untouched (same objects).
    from config import CONFIG

    old = CONFIG.get("ASE_FX_CONTEXT_SHADOW")
    try:
        CONFIG["ASE_FX_CONTEXT_SHADOW"] = False
        out_off = _attach_fx_context_shadow([fx, btc], store, "swing", inst_map, 1_000_000)
        assert out_off[0] is fx and out_off[1] is btc
    finally:
        if old is None:
            CONFIG.pop("ASE_FX_CONTEXT_SHADOW", None)
        else:
            CONFIG["ASE_FX_CONTEXT_SHADOW"] = old
