from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from ghost_trade.execution.bybit_demo import BybitDemoAdapter
from ghost_trade.execution.mt5_demo import MT5DemoAdapter
from ghost_trade.models import (
    AssetGroup,
    Direction,
    GhostInstrument,
    GhostSignal,
    SignalStatus,
    Style,
    Venue,
    VolatilityRegime,
)


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def signal(venue=Venue.MT5):
    crypto = venue is Venue.BYBIT
    instrument = GhostInstrument(
        venue=venue,
        broker_symbol="BTCUSDT" if crypto else "EURUSD.a",
        canonical_symbol="BTC/USDT" if crypto else "EUR/USD",
        asset_group=AssetGroup.CRYPTO if crypto else AssetGroup.FOREX,
        asset_subgroup="crypto_major" if crypto else "forex_major",
        base_asset="BTC" if crypto else "EUR",
        quote_asset="USDT" if crypto else "USD",
    )
    return GhostSignal(
        signal_id="signal-1", signal_version="ghost-v1", scan_id="scan-1",
        instrument=instrument, style=Style.INTRADAY, direction=Direction.LONG,
        decision_time=NOW, confirmed_score=0.7, live_adjustment=0,
        display_score=0.7, direction_confidence=0.6, entry_quality=0.7,
        entry=100, stop=95, target=110, raw_rr=2,
        volatility_regime=VolatilityRegime.NORMAL, can_execute=True,
        status=SignalStatus.ELIGIBLE,
    )


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, mode):
        self.mode = mode

    def account_info(self):
        return SimpleNamespace(trade_mode=self.mode, server="Pepperstone-Demo", login=123456)


def approval(volume=0.5):
    return SimpleNamespace(
        approved=True, volume=volume, risk_amount=25,
        risk_pct=0.0025, portfolio_heat=0.01, drawdown_pct=0, reason="OK",
    )


def test_mt5_real_or_unknown_account_rejects_before_shared_executor():
    calls = []
    adapter = MT5DemoAdapter(
        FakeMT5(FakeMT5.ACCOUNT_TRADE_MODE_REAL),
        connect_fn=lambda: True,
        execute_fn=lambda *_args: calls.append("execute"),
    )

    result = adapter.submit(signal(), approval())

    assert result.success is False
    assert result.error_code == "mt5_demo_not_verified"
    assert calls == []


def test_mt5_demo_translation_preserves_exact_symbol_structural_levels_and_approval():
    calls = []

    def execute(payload, approved):
        calls.append((payload, approved))
        return {"success": True, "ticket": 42, "entryPrice": 100.1, "sl": 95, "tp": 110}

    adapter = MT5DemoAdapter(
        FakeMT5(FakeMT5.ACCOUNT_TRADE_MODE_DEMO),
        connect_fn=lambda: True,
        execute_fn=execute,
    )

    result = adapter.submit(signal(), approval(0.25))

    assert result.success is True
    assert result.broker_order_id == "42"
    payload, approved = calls[0]
    assert payload["pair"] == "EURUSD.a"
    assert payload["symbol"] == "EURUSD.a"
    assert payload["engine"] == "GHOST_TRADE"
    assert payload["direction"] == "LONG"
    assert payload["sl"] == 95
    assert payload["tp1"] == 110
    assert payload["exit_strategy"] == "STRUCTURAL"
    assert approved.volume == 0.25


class FakeBybit:
    def __init__(self, api_url, *, demo=False):
        self.hostname = "bybit.com"
        self.options = {"enableDemoTrading": demo}
        self.urls = {"api": {"public": api_url, "private": api_url}}


def test_bybit_production_endpoint_rejects_before_shared_executor():
    calls = []
    adapter = BybitDemoAdapter(
        FakeBybit("https://api.bybit.com"),
        execute_fn=lambda *_args: calls.append("execute"),
    )

    result = adapter.submit(signal(Venue.BYBIT), approval())

    assert result.success is False
    assert result.error_code == "bybit_demo_not_verified"
    assert calls == []


def test_bybit_demo_and_testnet_endpoints_are_verified_and_use_exact_symbol():
    for url, demo in (
        ("https://api-demo.bybit.com", True),
        ("https://api-testnet.bybit.com", False),
    ):
        calls = []
        adapter = BybitDemoAdapter(
            FakeBybit(url, demo=demo),
            execute_fn=lambda payload, approved: calls.append((payload, approved)) or {
                "success": True, "ticket": "order-1", "entryPrice": 100,
            },
        )

        attestation = adapter.attest()
        result = adapter.submit(signal(Venue.BYBIT), approval(2.5))

        assert attestation.verified is True
        assert result.success is True
        assert calls[0][0]["pair"] == "BTCUSDT"
        assert calls[0][0]["symbol"] == "BTCUSDT"
        assert calls[0][0]["ghostSignalId"] == "signal-1"
        assert calls[0][1].volume == 2.5


def test_close_delegates_only_after_fresh_demo_attestation():
    mt5_calls = []
    bybit_calls = []
    mt5 = MT5DemoAdapter(
        FakeMT5(FakeMT5.ACCOUNT_TRADE_MODE_CONTEST),
        connect_fn=lambda: True,
        execute_fn=lambda *_args: None,
        close_fn=lambda ticket, volume=None: mt5_calls.append((ticket, volume)) or {"success": True},
    )
    bybit = BybitDemoAdapter(
        FakeBybit("https://api-testnet.bybit.com"),
        execute_fn=lambda *_args: None,
        close_fn=lambda pair, direction, volume: bybit_calls.append((pair, direction, volume)) or {"success": True},
    )

    assert mt5.close("42", direction="LONG", quantity=0.1).success is True
    assert bybit.close("BTCUSDT", direction="LONG", quantity=2).success is True
    assert mt5_calls == [(42, 0.1)]
    assert bybit_calls == [("BTCUSDT", "LONG", 2)]
