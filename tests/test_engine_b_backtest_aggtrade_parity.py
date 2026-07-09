from __future__ import annotations

import config
from market_structure import _engine_b_crypto_aggtrade_context


def _enable_aggtrade(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_AGGTRADE_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_CRYPTO_REQUIRE_AGGTRADE_FOR_PASS", True)


def test_historical_mode_disables_gate_without_touching_live_buckets(monkeypatch):
    _enable_aggtrade(monkeypatch)

    def _boom(*args, **kwargs):  # any live bucket read is a parity leak
        raise AssertionError("live trade-bucket store must not be read in historical mode")

    monkeypatch.setattr(
        "athena.microstructure.aggtrade_volume.check_trade_bucket_cvd", _boom
    )
    monkeypatch.setattr(
        "athena.microstructure.aggtrade_volume.build_trade_bucket_volume_profile",
        _boom,
        raising=False,
    )

    ctx = _engine_b_crypto_aggtrade_context(
        "crypto", {"display": "BTC/USDT"}, historical_mode=True
    )
    # Gate disabled (not failed): calculate_confidence reads aggtrade_required
    # from this context, so the hard requirement must be off for backtests.
    assert ctx["aggtrade_required"] is False
    assert ctx["aggtrade_available"] is False
    assert ctx["aggtrade_reason"] == "aggtrade_unavailable_historical"
    assert ctx["aggtrade_cvd_direction"] is None


def test_live_mode_still_reports_gate_required(monkeypatch):
    _enable_aggtrade(monkeypatch)
    ctx = _engine_b_crypto_aggtrade_context("crypto", {"display": "BTC/USDT"})
    assert ctx["aggtrade_required"] is True


def test_non_crypto_unaffected_by_historical_mode(monkeypatch):
    _enable_aggtrade(monkeypatch)
    ctx = _engine_b_crypto_aggtrade_context(
        "forex", {"display": "EUR/USD"}, historical_mode=True
    )
    assert ctx["aggtrade_enabled"] is False
    assert ctx["aggtrade_required"] is False
