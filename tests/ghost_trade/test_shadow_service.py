from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ghost_trade.config import GhostConfig
from ghost_trade.market_data import CandleBundle, SharedCandleProvider
from ghost_trade.models import (
    AssetGroup,
    Candle,
    Direction,
    GhostInstrument,
    GhostMode,
    SignalStatus,
    Style,
    Venue,
    VolatilityRegime,
)
from ghost_trade.persistence import GhostRepository
from ghost_trade.service import GhostService, ScanAlreadyRunning
from ghost_trade.universe import UniverseResult


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def instrument(symbol, canonical, group=AssetGroup.FOREX, *, skipped=()):
    return GhostInstrument(
        venue=Venue.MT5 if group is not AssetGroup.CRYPTO else Venue.BYBIT,
        broker_symbol=symbol,
        canonical_symbol=canonical,
        asset_group=group,
        asset_subgroup="forex_major" if group is AssetGroup.FOREX else "crypto_major",
        base_asset=canonical.split("/")[0],
        quote_asset=canonical.split("/")[-1],
        skip_reasons=tuple(skipped),
    )


def bar(index, *, open_=100, high=101, low=99, close=100):
    opened = NOW - timedelta(hours=50 - index)
    return Candle(
        open_time=opened,
        close_time=opened + timedelta(hours=1),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


def bundle():
    bars = tuple(bar(i) for i in range(40))
    return CandleBundle(d1=bars, h4=bars, h1=bars, as_of=NOW, provider="fake")


def score(*, selected=True, direction=Direction.LONG, value=0.62):
    geometry = SimpleNamespace(
        geometry=SimpleNamespace(
            entry=100.0,
            stop=95.0,
            target=110.0,
            raw_rr=2.0,
            risk_distance=5.0,
            target_distance=10.0,
            unbuffered_stop=95.5,
        ),
        reasons=(),
    )
    return SimpleNamespace(
        direction=direction,
        direction_confidence=0.61 if direction is Direction.LONG else -0.61,
        confirmed_score=value,
        selected=selected,
        reasons=() if selected else ("entry_quality_below_threshold",),
        entry_quality=SimpleNamespace(score=0.73, rr_score=0.5, room_score=1.0, pullback_score=1.0, trigger_score=1.0),
        volatility=SimpleNamespace(regime=VolatilityRegime.NORMAL, atr_percentile=55, bb_width_percentile=50),
        structure=SimpleNamespace(direction=1.0, strength=0.72),
        momentum=SimpleNamespace(direction=0.48, normalized_roc=0.5, normalized_pressure=0.46, volume_quality=1.0, correlation=0.4),
        exhaustion=SimpleNamespace(penalty=0.0, adverse_wick=False, declining_participation=False),
        geometry=geometry,
    )


class UniverseProvider:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def discover(self):
        self.calls += 1
        return self.result


class CandleProvider:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    def load(self, item, style, as_of):
        self.calls.append((item.broker_symbol, style, as_of))
        if item.broker_symbol in self.failures:
            raise RuntimeError("history unavailable")
        return bundle()


def service(tmp_path, *, scorer=None, candle_provider=None):
    repository = GhostRepository(tmp_path / "ghost.db")
    repository.migrate()
    items = UniverseResult(
        (
            instrument("EURUSD.a", "EUR/USD"),
            instrument("BTCUSDT", "BTC/USDT", AssetGroup.CRYPTO),
            instrument("DISABLED", "DISABLED", skipped=("trade_disabled",)),
        )
    )
    return (
        GhostService(
            config=GhostConfig(mode=GhostMode.SHADOW),
            repository=repository,
            universe_providers=(UniverseProvider(items),),
            candle_provider=candle_provider or CandleProvider(),
            score_fn=scorer or (lambda *_args, **_kwargs: score()),
            clock=lambda: NOW,
        ),
        repository,
    )


def test_shadow_scan_persists_all_instruments_scores_eligible_rows_and_never_executes(tmp_path):
    calls = 0

    def scorer(_bundle, **_kwargs):
        nonlocal calls
        calls += 1
        return score(selected=calls == 1, value=0.70 if calls == 1 else 0.30)

    ghost, repository = service(tmp_path, scorer=scorer)

    result = ghost.scan(style=Style.INTRADAY)

    assert result.discovered_count == 3
    assert result.scored_count == 2
    assert result.skipped_count == 1
    assert result.error_count == 0
    assert len(repository.list_instruments()) == 3
    signals = repository.list_signals()
    assert len(signals) == 2
    assert all(signal.can_execute is False for signal in signals)
    assert {signal.status for signal in signals} == {SignalStatus.ELIGIBLE, SignalStatus.ANALYSED}
    positions = repository.list_positions(status="OPEN")
    assert len(positions) == 1
    assert positions[0].mode == "SHADOW"


def test_candle_failure_is_reported_and_does_not_abort_other_symbols(tmp_path):
    ghost, repository = service(
        tmp_path, candle_provider=CandleProvider(failures={"BTCUSDT"})
    )

    result = ghost.scan()

    assert result.discovered_count == 3
    assert result.scored_count == 1
    assert result.error_count == 1
    assert result.errors[0].startswith("candle_load_failed:BTCUSDT:")
    assert repository.latest_scan()["status"] == "COMPLETED_WITH_ERRORS"


def test_second_scan_fails_fast_while_scan_lease_is_held(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def blocking_score(_bundle, **_kwargs):
        entered.set()
        release.wait(timeout=5)
        return score()

    ghost, _ = service(tmp_path, scorer=blocking_score)
    thread = threading.Thread(target=ghost.scan)
    thread.start()
    assert entered.wait(timeout=2)

    with pytest.raises(ScanAlreadyRunning):
        ghost.scan()

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_shadow_reconciliation_is_stop_first_and_updates_ghost_only_performance(tmp_path):
    ghost, repository = service(tmp_path)
    ghost.scan()
    ambiguous = Candle(
        open_time=NOW,
        close_time=NOW + timedelta(hours=1),
        open=100,
        high=111,
        low=94,
        close=105,
        volume=100,
    )

    result = ghost.reconcile_shadow({"EUR/USD": (ambiguous,)})
    performance = ghost.performance()

    assert result == {"checked": 1, "closed": 1, "ambiguous": 1}
    remaining = repository.list_positions(status="OPEN")
    assert [position.canonical_symbol for position in remaining] == ["BTC/USDT"]
    assert performance["totalClosedTrades"] == 1
    assert performance["meanR"] == pytest.approx(-1.0)
    assert performance["profitFactor"] == 0.0


def test_shared_candle_provider_keeps_confirmed_and_forming_states_separate():
    calls = []

    def loader(_instrument, timeframe, _limit):
        calls.append(timeframe)
        duration = {"D1": 24, "H4": 4, "H1": 1, "M15": 0.25}[timeframe]
        confirmed = Candle(
            open_time=NOW - timedelta(hours=duration),
            close_time=NOW,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=100,
        )
        forming = Candle(
            open_time=NOW,
            close_time=NOW + timedelta(hours=duration),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=100,
        )
        return [confirmed, forming]

    provider = SharedCandleProvider(loader)
    result = provider.load(instrument("EURUSD", "EUR/USD"), Style.INTRADAY, NOW)

    assert calls == ["D1", "H4", "H1", "M15"]
    assert len(result.d1) == len(result.h4) == len(result.h1) == 1
    assert len(result.m15_confirmed) == 1
    assert result.m15_forming is not None
    assert result.m15_forming.open_time == NOW
