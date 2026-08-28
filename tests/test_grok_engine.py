from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from types import SimpleNamespace

from flask import Flask
import pytest

from grok_engine.api import register_grok_routes
from grok_engine.config import GrokConfigError, load_grok_config
from grok_engine.execution import GrokExecutionCoordinator, GrokExecutionError
from grok_engine.indicators import cisd_state, dealing_quality, dealing_range, impulse_vector, raid_signature, void_map, wilder_atr
from grok_engine.market_data import normalize_closed_candles
from grok_engine.models import Candle, MarketSnapshot, Quote, TIMEFRAME_SECONDS
from grok_engine.persistence import GrokRepository
from grok_engine.replay import _outcome
from grok_engine.scoring import _delivery_sequence, _execution_geometry, _m5_trigger_is_recent, evaluate_snapshot
from grok_engine.sessions import classify_session, market_is_open


NOW = datetime(2026, 3, 17, 14, 32, tzinfo=timezone.utc).timestamp()


def test_impulse_vector_requires_minimum_run_unless_single_bar_meets_stricter_threshold() -> None:
    candles = [
        _candle(float(index + 1), 10.0, 10.1, 9.9, 10.0)
        for index in range(5)
    ]
    candles[-1] = _candle(5.0, 10.0, 10.8, 10.0, 10.8)

    result = impulse_vector(
        candles,
        atr=1.0,
        min_run=2,
        body_fraction=0.55,
        range_atr=0.62,
        single_range_atr=1.28,
    )

    assert result["available"] is False
    assert result["reason"] == "NO_DISPLACEMENT"

    candles[-1] = _candle(5.0, 10.0, 11.3, 10.0, 11.3)
    strict_single = impulse_vector(
        candles,
        atr=1.0,
        min_run=2,
        body_fraction=0.55,
        range_atr=0.62,
        single_range_atr=1.28,
    )

    assert strict_single["available"] is True
    assert strict_single["bars"] == 1
    assert strict_single["startIndex"] == 4
    assert strict_single["endIndex"] == 4


def test_impulse_vector_uses_latest_valid_delivery_not_stronger_stale_impulse() -> None:
    candles = [_candle(float(index + 1), 10.0, 10.1, 9.9, 10.0) for index in range(16)]
    candles[8] = _candle(9.0, 10.0, 11.6, 9.9, 11.5)
    candles[9] = _candle(10.0, 11.5, 13.1, 11.4, 13.0)
    candles[14] = _candle(15.0, 10.0, 10.65, 9.95, 10.6)
    candles[15] = _candle(16.0, 10.6, 11.25, 10.55, 11.2)

    result = impulse_vector(
        candles,
        atr=1.0,
        min_run=2,
        body_fraction=0.52,
        range_atr=0.62,
        single_range_atr=1.28,
    )

    assert result["available"] is True
    assert result["startIndex"] == 14
    assert result["endIndex"] == 15
    assert result["ageBars"] == 0


def test_impulse_vector_can_select_recent_candidate_for_required_direction() -> None:
    candles = [_candle(float(index + 1), 10.0, 10.1, 9.9, 10.0) for index in range(16)]
    candles[12] = _candle(13.0, 10.0, 10.65, 9.95, 10.6)
    candles[13] = _candle(14.0, 10.6, 11.25, 10.55, 11.2)
    candles[14] = _candle(15.0, 11.2, 11.25, 10.55, 10.6)
    candles[15] = _candle(16.0, 10.6, 10.65, 9.95, 10.0)

    result = impulse_vector(
        candles,
        atr=1.0,
        min_run=2,
        body_fraction=0.52,
        range_atr=0.62,
        single_range_atr=1.28,
        required_direction=1,
    )

    assert result["available"] is True
    assert result["direction"] == 1
    assert result["startIndex"] == 12
    assert result["endIndex"] == 13
    assert result["ageBars"] == 2


@pytest.mark.parametrize(
    ("close", "long_expected", "short_expected"),
    [
        (12.5, 1.0, 0.25 / 0.72),
        (17.5, 0.0, 1.0),
        (10.0, 1.0, 0.0),
    ],
)
def test_dealing_range_ote_is_directional_and_preserves_zero_position(
    close: float,
    long_expected: float,
    short_expected: float,
) -> None:
    candles = [_candle(float(index + 1), 15.0, 20.0, 10.0, 15.0) for index in range(12)]
    candles[-1] = _candle(12.0, 15.0, 20.0, 10.0, close)

    dealing = dealing_range(candles, lookback=12, ote_inner=0.62, ote_outer=0.79)

    assert dealing["position"] == pytest.approx((close - 10.0) / 10.0)
    assert dealing_quality(dealing, 1) == pytest.approx(long_expected)
    assert dealing_quality(dealing, -1) == pytest.approx(short_expected)


def test_cisd_requires_a_post_impulse_close_through_the_last_opposing_open() -> None:
    candles = [_candle(float(index + 1), 10.0, 10.1, 9.4, 9.8) for index in range(8)]
    candles[5] = _candle(6.0, 10.0, 10.05, 9.4, 9.5)
    candles[6] = _candle(7.0, 9.5, 9.9, 9.45, 9.8)
    candles[7] = _candle(8.0, 9.8, 9.95, 9.7, 9.9)
    raid = {"available": True, "direction": 1, "eventIndex": 5}
    impulse = {"available": True, "direction": 1, "origin": 9.4, "startIndex": 6, "endIndex": 6}

    unconfirmed = cisd_state(candles, raid, impulse, lookback=8)

    assert unconfirmed["origin"] == pytest.approx(10.0)
    assert unconfirmed["confirmed"] is False

    candles[7] = _candle(8.0, 9.8, 10.15, 9.7, 10.1)
    confirmed = cisd_state(candles, raid, impulse, lookback=8)

    assert confirmed["confirmed"] is True
    assert confirmed["eventIndex"] == 7


@pytest.mark.parametrize(
    ("raid_index", "impulse_start", "impulse_end", "void_index", "cisd_index"),
    [
        (5, 4, 4, 6, 7),
        (5, 6, 6, 4, 7),
        (5, 6, 6, 7, 5),
        (5, None, None, 7, 8),
    ],
)
def test_delivery_sequence_rejects_out_of_order_or_missing_events(
    raid_index: int,
    impulse_start: int | None,
    impulse_end: int | None,
    void_index: int,
    cisd_index: int,
) -> None:
    result = _delivery_sequence(
        {"eventIndex": raid_index},
        {"startIndex": impulse_start, "endIndex": impulse_end},
        {"index": void_index},
        {"eventIndex": cisd_index},
    )

    assert result["passed"] is False
    assert result["reason"] in {"DELIVERY_SEQUENCE_INVALID", "DELIVERY_SEQUENCE_UNAVAILABLE"}


def test_delivery_sequence_accepts_raid_then_impulse_then_void_and_cisd() -> None:
    result = _delivery_sequence(
        {"eventIndex": 5},
        {"startIndex": 6, "endIndex": 6},
        {"index": 7},
        {"eventIndex": 6},
    )

    assert result == {"passed": True, "reason": None}


def _last_closed_open(timeframe: str, as_of: float = NOW) -> float:
    seconds = TIMEFRAME_SECONDS[timeframe]
    return as_of - (as_of % seconds) - seconds


def _candle(opened: float, open_: float, high: float, low: float, close: float, volume: float = 120.0) -> Candle:
    return Candle(opened, open_, high, low, close, volume, "synthetic")


def _series(timeframe: str, count: int, price_at, *, as_of: float = NOW, wick: float = 0.0004) -> list[Candle]:
    seconds = TIMEFRAME_SECONDS[timeframe]
    last_open = _last_closed_open(timeframe, as_of)
    rows: list[Candle] = []
    previous = float(price_at(last_open - (count - 1) * seconds))
    for index in range(count):
        opened = last_open - (count - 1 - index) * seconds
        close = float(price_at(opened))
        high = max(previous, close) + wick
        low = min(previous, close) - wick
        rows.append(_candle(opened, previous, high, low, close))
        previous = close
    return rows


def _ready_snapshot(
    *,
    mirrored: bool = False,
    display: str = "EURUSD",
    asset_type: str = "forex",
    as_of: float = NOW,
) -> MarketSnapshot:
    asia_low = 1.09840
    asia_high = 1.10220
    raid_low = 1.09790
    impulse_top = 1.10090
    entry = 1.09920

    def d1_price(opened: float) -> float:
        age_days = max(0, int((_last_closed_open("D1") - opened) / TIMEFRAME_SECONDS["D1"]))
        return 1.0900 + 0.00018 * (80 - age_days)

    def path(opened: float) -> float:
        local = datetime.fromtimestamp(opened, tz=timezone.utc)
        minutes = local.hour * 60 + local.minute
        if minutes < 360:
            wave = 0.00025 * math.sin(opened / 900.0)
            return 1.09980 + wave
        if minutes < 420:
            return 1.09890
        if minutes < 720:
            progress = (minutes - 420) / 300.0
            return 1.09890 + progress * (impulse_top - 1.09890)
        progress = min(1.0, (minutes - 720) / 180.0)
        return impulse_top - progress * (impulse_top - entry)

    d1 = _series("D1", 80, d1_price, as_of=as_of, wick=0.0040)
    last_d1 = d1[-1]
    d1[-1] = _candle(last_d1.time, last_d1.open, 1.10580, 1.09420, last_d1.close, 8000)
    h1 = _series("H1", 90, path, as_of=as_of, wick=0.0008)
    swing = h1[-36]
    h1[-36] = _candle(swing.time, 1.10200, 1.10620, 1.10140, 1.10540, 900)
    trough = h1[-24]
    h1[-24] = _candle(trough.time, 1.10080, 1.10110, 1.09640, 1.09710, 900)
    m15 = _series("M15", 160, path, as_of=as_of, wick=0.00018)
    m5 = _series("M5", 220, path, as_of=as_of, wick=0.00018)

    asia_indexes = [
        index
        for index, candle in enumerate(m15)
        if datetime.fromtimestamp(candle.time, tz=timezone.utc).strftime("%Y-%m-%d") == "2026-03-17"
        and datetime.fromtimestamp(candle.time, tz=timezone.utc).hour < 6
    ]
    for offset, index in enumerate(asia_indexes):
        candle = m15[index]
        close = 1.09990 + 0.00010 * math.sin(offset)
        high = asia_high if offset == 0 else close + 0.00016
        low = asia_low if offset == 1 else close - 0.00016
        m15[index] = _candle(candle.time, close, high, low, close + 0.00004)

    raid_index = len(m15) - 6
    raid = m15[raid_index]
    left = m15[raid_index + 1]
    mid = m15[raid_index + 2]
    right = m15[raid_index + 3]
    pull = m15[raid_index + 4]
    last = m15[raid_index + 5]
    m15[raid_index] = _candle(raid.time, 1.09880, 1.09910, raid_low, 1.09895, 420)
    m15[raid_index + 1] = _candle(left.time, 1.09895, 1.09905, 1.09820, 1.09870, 360)
    m15[raid_index + 2] = _candle(mid.time, 1.09870, 1.10040, 1.09860, 1.10025, 420)
    m15[raid_index + 3] = _candle(right.time, 1.10025, 1.10090, 1.09945, 1.10080, 440)
    m15[raid_index + 4] = _candle(pull.time, 1.10020, 1.10055, 1.09958, 1.10005, 230)
    m15[raid_index + 5] = _candle(last.time, 1.09940, 1.09970, 1.09912, entry, 240)

    # Trigger impulse on M5 into the void.
    m5_closes = [entry - 0.0018 + 0.00012 * step for step in range(8)]
    m5_closes[-1] = entry
    for offset, close in enumerate(m5_closes, start=len(m5) - 8):
        candle = m5[offset]
        open_ = close - 0.00040
        m5[offset] = _candle(candle.time, open_, close + 0.00004, open_ - 0.00002, close, 260)

    frames = {"D1": d1, "H1": h1, "M15": m15, "M5": m5}
    if mirrored:
        axis = 3.0

        def mirror(candle: Candle) -> Candle:
            return Candle(
                candle.time,
                axis - candle.open,
                axis - candle.low,
                axis - candle.high,
                axis - candle.close,
                candle.volume,
                candle.volume_source,
            )

        frames = {timeframe: [mirror(candle) for candle in rows] for timeframe, rows in frames.items()}

    return MarketSnapshot(
        pair={"display": display, "symbol": display, "type": asset_type},
        frames=frames,
        provenance={
            timeframe: {"provider": "synthetic", "bars": len(rows)}
            for timeframe, rows in frames.items()
        },
        as_of_epoch=as_of,
    )


def _ready_signal(*, mirrored: bool = False, display: str = "EURUSD", asset_type: str = "forex") -> dict:
    return evaluate_snapshot(
        _ready_snapshot(mirrored=mirrored, display=display, asset_type=asset_type),
        load_grok_config(),
        generated_at_epoch=NOW,
    )


def _component_max(signal: dict, name: str) -> float:
    return float(next(item["maxScore"] for item in signal["components"] if item["name"] == name))


def _store_signals(repository: GrokRepository, signals: list[dict]) -> None:
    repository.migrate()
    scan_id = repository.create_scan({"test": True})
    repository.save_signals(scan_id, signals)
    repository.complete_scan(scan_id, "COMPLETED", {"pairCount": len(signals)})


class _Gateway:
    def __init__(self, quote: Quote, *, account: dict | None = None) -> None:
        self.current_quote = quote
        self.current_account = account or {
            "error": False,
            "demo": True,
            "testnet": False,
            "balance": 10_000.0,
            "equity": 10_000.0,
            "risk_domain": "test",
        }
        self.quote_calls = 0
        self.account_calls = 0
        self.position_calls = 0
        self.execute_calls = 0
        self.last_payload: dict | None = None

    def quote(self, signal: dict) -> Quote:
        self.quote_calls += 1
        return self.current_quote

    def account(self, venue: str) -> dict:
        self.account_calls += 1
        return self.current_account

    def positions(self, venue: str) -> dict:
        self.position_calls += 1
        return {"error": False, "positions": []}

    def symbol_info(self, signal: dict) -> dict:
        return {"error": False, "volume_min": 0.01, "volume_step": 0.01}

    def execute(self, venue: str, payload: dict, approval) -> dict:
        self.execute_calls += 1
        self.last_payload = payload
        return {"success": True, "ticket": "TEST"}


def _coordinator(
    tmp_path,
    signals: list[dict],
    gateway: _Gateway,
    *,
    kill_switch: bool = False,
    database: str = "grok.db",
) -> GrokExecutionCoordinator:
    repository = GrokRepository(tmp_path / database)
    _store_signals(repository, signals)
    return GrokExecutionCoordinator(
        config=load_grok_config(),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: kill_switch,
        now_fn=lambda: NOW,
    )


def _passing_quote(signal: dict, *, timestamp: float = NOW) -> Quote:
    ask = float(signal["entry"]) + float(signal["atr"]) * 0.01
    return Quote("mt5", signal["symbol"], ask - 0.00020, ask, timestamp, "test_tick")


def test_default_contract_is_valid_and_live_requires_research_validation() -> None:
    config = load_grok_config()
    assert config.version == "grok.v2"
    assert sum(config.scoring["weights"].values()) == 100.0
    assert config.execution["default_mode"] == "paper"
    assert config.execution["live_enabled"] is False
    with pytest.raises(GrokConfigError, match="research_status=VALIDATED"):
        load_grok_config({"GROK_ENGINE": {"execution": {"live_enabled": True}}})


def test_checked_in_yaml_overlay_keeps_live_locked() -> None:
    from pathlib import Path
    import yaml

    overlay = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")).get("GROK_ENGINE")
    config = load_grok_config({"GROK_ENGINE": overlay})
    assert config.enabled is True
    assert config.execution["default_mode"] == "paper"
    assert config.execution["live_enabled"] is False
    assert str(config.execution["research_status"]).upper() == "UNVALIDATED"
    assert config.sessions["display_timezone"] == "Africa/Johannesburg"
    assert config.sessions["timezone"] == "America/New_York"


def test_ny_silver_bullet_clock_and_weekday_session_gate() -> None:
    config = load_grok_config()
    clock = classify_session(NOW, config)
    assert clock["primaryKind"] == "silver_bullet"
    assert clock["quality"] == 1.0
    assert clock["displayTimezone"] == "Africa/Johannesburg"
    assert clock["displayClock"] == "16:32"
    assert clock["localClock"] == "10:32"
    assert market_is_open(NOW, config, "forex") is True
    friday_close = datetime(2026, 3, 13, 21, 30, tzinfo=timezone.utc).timestamp()
    assert market_is_open(friday_close, config, "forex") is False
    assert market_is_open(friday_close, config, "crypto") is True


def test_sast_display_clock_does_not_move_ny_killzones() -> None:
    config = load_grok_config()
    # 11:40 SAST on 2026-08-19 == 09:40 UTC == 05:40 NY (EDT).
    epoch = datetime(2026, 8, 19, 9, 40, tzinfo=timezone.utc).timestamp()
    clock = classify_session(epoch, config)
    assert clock["displayClock"] == "11:40"
    assert clock["localClock"] == "05:40"
    assert clock["displayTimezone"] == "Africa/Johannesburg"
    assert clock["inWindow"] is False
    london = next(row for row in clock["windowSchedule"] if row["name"] == "london_killzone")
    ny_am = next(row for row in clock["windowSchedule"] if row["name"] == "ny_am_killzone")
    assert london["nyStart"] == "02:00" and london["nyEnd"] == "05:00"
    assert london["displayStart"] == "08:00" and london["displayEnd"] == "11:00"
    assert ny_am["nyStart"] == "07:00" and ny_am["nyEnd"] == "10:00"
    assert ny_am["displayStart"] == "13:00" and ny_am["displayEnd"] == "16:00"


def test_forex_crypto_and_stock_use_distinct_scoring_profiles() -> None:
    forex = _ready_signal(display="EUR/USD", asset_type="forex")
    crypto = evaluate_snapshot(
        _ready_snapshot(display="BTC/USDT", asset_type="crypto"),
        load_grok_config(),
        generated_at_epoch=NOW,
    )
    stock = evaluate_snapshot(
        _ready_snapshot(display="AAPL", asset_type="stock"),
        load_grok_config(),
        generated_at_epoch=NOW,
    )
    assert forex["scoreGroup"] == "forex_majors"
    assert crypto["scoreGroup"] == "crypto_btc"
    assert stock["scoreGroup"] == "us_stock_single"
    assert forex["grokProfile"]["sessionMode"] == "ict_killzone"
    assert crypto["grokProfile"]["sessionMode"] == "ict_killzone"
    assert stock["grokProfile"]["sessionMode"] == "cash_rth"
    assert forex["readyThreshold"] < crypto["readyThreshold"]
    assert forex["readyThreshold"] < stock["readyThreshold"]
    assert _component_max(forex, "killzone_clock") != _component_max(crypto, "killzone_clock")
    assert _component_max(forex, "geometry") != _component_max(stock, "geometry")
    assert forex["grokProfile"]["family"] != crypto["grokProfile"]["family"]
    assert crypto["grokProfile"]["family"] != stock["grokProfile"]["family"]
    assert sum(forex["grokProfile"]["weights"].values()) == pytest.approx(100.0)
    assert forex["grokProfile"]["weightScope"] == "base"
    assert crypto["grokProfile"]["weightScope"] == "family:crypto"
    assert stock["grokProfile"]["weightScope"] == "family:stock"
    assert forex["grokProfile"]["calibrationStatus"] == "UNVALIDATED"


def test_stock_is_closed_during_london_silver_bullet_while_forex_is_open() -> None:
    london_sb = datetime(2026, 3, 17, 7, 30, tzinfo=timezone.utc).timestamp()
    forex = evaluate_snapshot(
        _ready_snapshot(display="EUR/USD", asset_type="forex", as_of=london_sb),
        load_grok_config(),
        generated_at_epoch=london_sb,
    )
    stock = evaluate_snapshot(
        _ready_snapshot(display="AAPL", asset_type="stock", as_of=london_sb),
        load_grok_config(),
        generated_at_epoch=london_sb,
    )
    assert "SESSION_CLOSED" not in forex["blockingReasons"]
    assert stock["decision"] != "READY"
    assert "SESSION_CLOSED" in stock["blockingReasons"]
    assert stock["sessionClock"]["primaryWindow"] != forex["sessionClock"]["primaryWindow"]


def test_crypto_other_is_stricter_than_forex_majors() -> None:
    forex = _ready_signal(display="EUR/USD", asset_type="forex")
    thin = evaluate_snapshot(
        _ready_snapshot(display="ETC/USDT", asset_type="crypto"),
        load_grok_config(),
        generated_at_epoch=NOW,
    )
    assert thin["scoreGroup"] == "crypto_other"
    assert thin["readyThreshold"] > forex["readyThreshold"]
    assert thin["grokProfile"]["minimumStopAtr"] > forex["grokProfile"]["minimumStopAtr"]
    assert thin["grokProfile"]["raidMinExcursionAtr"] > forex["grokProfile"]["raidMinExcursionAtr"]


def test_pair_profile_override_applies_only_to_that_symbol() -> None:
    config = load_grok_config(
        {
            "GROK_ENGINE": {
                "profiles": {
                    "pairs": {
                        "EUR/USD": {"scoring": {"ready_threshold": 91.0, "watch_threshold": 70.0}},
                    }
                }
            }
        }
    )
    eurusd = evaluate_snapshot(
        _ready_snapshot(display="EUR/USD", asset_type="forex"),
        config,
        generated_at_epoch=NOW,
    )
    gbpusd = evaluate_snapshot(
        _ready_snapshot(display="GBP/USD", asset_type="forex"),
        config,
        generated_at_epoch=NOW,
    )
    assert eurusd["readyThreshold"] == 91.0
    assert gbpusd["readyThreshold"] == load_grok_config().scoring["ready_threshold"]


@pytest.mark.parametrize(
    "overlay",
    [
        {"scoring": {"ready_threshold": 101.0}},
        {"scoring": {"ready_threshold": 50.0, "watch_threshold": 60.0}},
        {"levels": {"minimum_stop_atr": 3.0, "maximum_stop_atr": 2.0}},
        {"indicators": {"ote_inner": 0.85, "ote_outer": 0.79}},
    ],
)
def test_invalid_resolved_profile_overlays_fail_closed(overlay: dict[str, object]) -> None:
    with pytest.raises(GrokConfigError):
        load_grok_config(
            {
                "GROK_ENGINE": {
                    "profiles": {
                        "groups": {"forex_majors": overlay},
                    }
                }
            }
        )


def test_score_group_field_on_pair_is_honoured() -> None:
    snapshot = _ready_snapshot(display="EUR/USD", asset_type="forex")
    snapshot.pair["score_group"] = "forex_exotics"
    signal = evaluate_snapshot(snapshot, load_grok_config(), generated_at_epoch=NOW)
    assert signal["scoreGroup"] == "forex_exotics"
    assert signal["readyThreshold"] > load_grok_config().scoring["ready_threshold"]


def test_post_as_of_bar_is_dropped_without_false_future_error() -> None:
    rows = [
        {"open_time": NOW - 600, "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11},
        {"open_time": NOW + 20, "open": 1.11, "high": 1.12, "low": 1.10, "close": 1.115},
    ]
    candles, provenance, errors = normalize_closed_candles(
        rows,
        "M5",
        now_epoch=NOW,
        observed_at_epoch=NOW + 120,
        provider="test",
    )
    assert len(candles) == 1
    assert provenance["postAsOfBarsDropped"] == 1
    assert not any(error.startswith("FUTURE_CANDLES") for error in errors)


def test_candle_normalization_drops_forming_future_and_malformed_rows() -> None:
    rows = [
        {"open_time": NOW - 600, "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11},
        {"open_time": NOW - 120, "open": 1.11, "high": 1.12, "low": 1.10, "close": 1.115},
        {"open_time": NOW + 20, "open": 1.11, "high": 1.12, "low": 1.10, "close": 1.115},
        {"open_time": NOW - 1200, "open": 1.10, "high": 1.105, "low": 1.09, "close": 1.11},
    ]
    candles, provenance, errors = normalize_closed_candles(rows, "M5", now_epoch=NOW, provider="test")
    assert len(candles) == 1
    assert provenance["formingBarsDropped"] == 1
    assert provenance["futureBarsDropped"] == 1
    assert provenance["malformedBarsDropped"] == 1
    assert any(error.startswith("FUTURE_CANDLES:M5:1") for error in errors)


def _snapshot_with_raid_moved(age_bars: int) -> MarketSnapshot:
    snapshot = _ready_snapshot()
    m15 = list(snapshot.frames["M15"])
    recent_index = len(m15) - 6
    stale_index = len(m15) - 1 - age_bars
    assert 0 <= stale_index < recent_index
    raid = m15[recent_index]
    stale = m15[stale_index]
    m15[stale_index] = _candle(stale.time, raid.open, raid.high, raid.low, raid.close, raid.volume or 420)
    for index in range(recent_index, len(m15)):
        candle = m15[index]
        m15[index] = _candle(candle.time, 1.09940, 1.09970, 1.09912, 1.09920, candle.volume or 240)
    frames = dict(snapshot.frames)
    frames["M15"] = m15
    return MarketSnapshot(snapshot.pair, frames, snapshot.provenance, snapshot.as_of_epoch)


def test_raid_and_void_indicators_are_directional() -> None:
    snapshot = _ready_snapshot()
    atr = wilder_atr(snapshot.frames["M15"], 14)
    raid = raid_signature(
        snapshot.frames["M15"],
        {"asiaLow": 1.09840, "asiaHigh": 1.10220},
        {"pdl": 1.09420, "pdh": 1.10580},
        atr=atr,
        lookback=16,
        recent_bars=6,
        min_excursion_atr=0.08,
    )
    void = void_map(snapshot.frames["M15"], lookback=36, atr=atr)
    assert raid["available"] is True
    assert raid["direction"] == 1
    assert void["available"] is True
    assert void["direction"] == 1
    assert void["open"] is True


def test_raid_outside_recent_window_is_unavailable() -> None:
    snapshot = _snapshot_with_raid_moved(age_bars=11)
    atr = wilder_atr(snapshot.frames["M15"], 14)
    raid = raid_signature(
        snapshot.frames["M15"],
        {"asiaLow": 1.09840, "asiaHigh": 1.10220},
        {"pdl": 1.09420, "pdh": 1.10580},
        atr=atr,
        lookback=16,
        recent_bars=6,
        min_excursion_atr=0.08,
    )
    assert raid["available"] is False
    assert raid["reason"] == "RAID_NOT_RECENT"
    assert raid["eventAgeBars"] == 11


def test_stale_raid_cannot_promote_ready() -> None:
    signal = evaluate_snapshot(
        _snapshot_with_raid_moved(age_bars=11),
        load_grok_config(),
        generated_at_epoch=NOW,
    )
    assert signal["decision"] != "READY"
    assert any("RAID_NOT_RECENT" in reason for reason in signal["blockingReasons"])


@pytest.mark.parametrize(("mirrored", "direction"), [(False, "LONG"), (True, "SHORT")])
def test_ready_signal_is_symmetric_auditable_and_fully_gated(mirrored: bool, direction: str) -> None:
    signal = _ready_signal(mirrored=mirrored)
    assert signal["decision"] == "READY", signal["blockingReasons"]
    assert signal["direction"] == direction
    assert signal["setup"] in {"SILVER_BULLET", "JUDAS_RECLAIM", "CISD_CONTINUATION", "OTE_DELIVERY"}
    assert signal["score"] >= signal["readyThreshold"]
    assert sum(component["maxScore"] for component in signal["components"]) == 100.0
    assert sum(component["score"] for component in signal["components"]) == pytest.approx(signal["score"])
    assert all(gate["passed"] for gate in signal["gates"])
    assert signal["blockingReasons"] == []
    if direction == "LONG":
        assert signal["stop"] < signal["entry"] < signal["target"]
    else:
        assert signal["target"] < signal["entry"] < signal["stop"]


def test_opposing_m5_trigger_cannot_be_overridden_by_m15_cisd() -> None:
    snapshot = _ready_snapshot()
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    entry = frames["M5"][-1].close
    closes = [entry + 0.0018 - 0.00024 * step for step in range(8)]
    for offset, close in enumerate(closes, start=len(frames["M5"]) - len(closes)):
        candle = frames["M5"][offset]
        open_ = close + 0.00018
        frames["M5"][offset] = _candle(candle.time, open_, open_ + 0.00004, close - 0.00006, close, 260)

    signal = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, snapshot.as_of_epoch),
        load_grok_config(),
        generated_at_epoch=NOW,
    )

    assert signal["indicatorState"]["cisd"]["confirmed"] is True
    assert signal["indicatorState"]["triggerImpulse"]["available"] is False
    assert signal["indicatorState"]["triggerImpulse"]["reason"] == "NO_ALIGNED_DISPLACEMENT"
    assert signal["decision"] != "READY"
    assert "TRIGGER_NOT_ALIGNED" in signal["blockingReasons"]


def test_m5_trigger_inside_current_m15_impulse_is_recent() -> None:
    m15_open = 1_000_000.0
    m5 = [_candle(m15_open + index * 300, 1.10, 1.11, 1.09, 1.10) for index in range(4)]
    m15 = [
        _candle(m15_open - 900, 1.10, 1.11, 1.09, 1.10),
        _candle(m15_open, 1.10, 1.12, 1.09, 1.11),
    ]
    trigger = {"available": True, "ageBars": 3, "startIndex": 0, "endIndex": 0}
    impulse = {"available": True, "ageBars": 0, "startIndex": 1, "endIndex": 1}

    assert _m5_trigger_is_recent(trigger, impulse, m5, m15, recent_bars=2) is True


def test_m5_trigger_inside_previous_m15_impulse_is_recent() -> None:
    m15_open = 1_000_000.0
    m5 = [_candle(m15_open + index * 300, 1.10, 1.11, 1.09, 1.10) for index in range(7)]
    m15 = [
        _candle(m15_open, 1.10, 1.12, 1.09, 1.11),
        _candle(m15_open + 900, 1.10, 1.11, 1.09, 1.10),
    ]
    trigger = {"available": True, "ageBars": 5, "startIndex": 0, "endIndex": 1}
    impulse = {"available": True, "ageBars": 1, "startIndex": 0, "endIndex": 0}

    assert _m5_trigger_is_recent(trigger, impulse, m5, m15, recent_bars=2) is True


def test_m5_trigger_outside_impulse_window_stays_stale() -> None:
    m15_open = 1_000_000.0
    m5 = [_candle(m15_open + index * 300, 1.10, 1.11, 1.09, 1.10) for index in range(8)]
    m15 = [
        _candle(m15_open, 1.10, 1.12, 1.09, 1.11),
        _candle(m15_open + 900, 1.10, 1.11, 1.09, 1.10),
    ]
    trigger = {"available": True, "ageBars": 3, "startIndex": 4, "endIndex": 4}
    impulse = {"available": True, "ageBars": 1, "startIndex": 0, "endIndex": 0}

    assert _m5_trigger_is_recent(trigger, impulse, m5, m15, recent_bars=2) is False


def test_m5_trigger_overlapping_live_m15_impulse_is_not_trigger_stale() -> None:
    snapshot = _ready_snapshot(as_of=NOW + 240)
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    m5 = frames["M5"]
    m15 = frames["M15"]
    entry = m5[-1].close
    for offset in range(len(m5) - 3, len(m5)):
        candle = m5[offset]
        m5[offset] = _candle(candle.time, entry, entry + 0.00002, entry - 0.00002, entry, 120)
    last = m15[-1]
    m15[-1] = _candle(last.time, 1.09870, 1.10090, 1.09860, 1.10080, 440)

    signal = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, snapshot.as_of_epoch),
        load_grok_config(),
        generated_at_epoch=NOW + 240,
    )
    trigger = signal["indicatorState"]["triggerImpulse"]
    impulse = signal["indicatorState"]["impulse"]

    assert trigger["ageBars"] == 3
    assert impulse["ageBars"] == 0
    assert "TRIGGER_STALE" not in signal["blockingReasons"]


def test_stale_m5_trigger_cannot_promote_ready() -> None:
    snapshot = _ready_snapshot()
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    entry = frames["M5"][-1].close
    for offset in range(len(frames["M5"]) - 3, len(frames["M5"])):
        candle = frames["M5"][offset]
        frames["M5"][offset] = _candle(candle.time, entry, entry + 0.00002, entry - 0.00002, entry, 120)

    signal = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, snapshot.as_of_epoch),
        load_grok_config(),
        generated_at_epoch=NOW,
    )

    assert signal["indicatorState"]["triggerImpulse"]["direction"] == 1
    assert signal["indicatorState"]["triggerImpulse"]["ageBars"] == 3
    assert signal["decision"] != "READY"
    assert "TRIGGER_STALE" in signal["blockingReasons"]


def test_stronger_stale_m5_impulse_does_not_hide_recent_aligned_trigger() -> None:
    snapshot = _ready_snapshot()
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    m5 = frames["M5"]
    recent = [m5[-2], m5[-1]]
    entry = m5[-1].close
    for offset in range(len(m5) - 3, len(m5)):
        candle = m5[offset]
        m5[offset] = _candle(candle.time, entry, entry + 0.00002, entry - 0.00002, entry, 120)
    m5[-2] = _candle(m5[-2].time, recent[0].open, recent[0].high, recent[0].low, recent[0].close, recent[0].volume or 260)
    m5[-1] = _candle(m5[-1].time, recent[1].open, recent[1].high, recent[1].low, recent[1].close, recent[1].volume or 260)

    signal = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, snapshot.as_of_epoch),
        load_grok_config(),
        generated_at_epoch=NOW,
    )
    trigger = signal["indicatorState"]["triggerImpulse"]

    assert trigger["available"] is True
    assert trigger["direction"] == 1
    assert trigger["ageBars"] == 0
    assert "TRIGGER_STALE" not in signal["blockingReasons"]
    assert signal["decision"] == "READY", signal["blockingReasons"]


def test_same_setup_keeps_one_identity_across_trigger_refreshes() -> None:
    config = load_grok_config()
    snapshot = _ready_snapshot()
    first = evaluate_snapshot(snapshot, config, generated_at_epoch=NOW)
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    prior = frames["M5"][-1]
    refreshed_close = prior.close + 0.00012
    frames["M5"].append(
        Candle(
            NOW,
            prior.close,
            refreshed_close + 0.00004,
            prior.close - 0.00002,
            refreshed_close,
            260,
            "synthetic",
        )
    )
    refreshed = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, NOW + 300),
        config,
        generated_at_epoch=NOW + 300,
    )
    assert first["decision"] == "READY", first["blockingReasons"]
    assert refreshed["decision"] == "READY", refreshed["blockingReasons"]
    assert refreshed["barClosedAt"] != first["barClosedAt"]
    assert refreshed["setupEventAt"] == first["setupEventAt"]
    assert refreshed["signalId"] == first["signalId"]


def test_structural_stop_is_capped_to_max_atr_when_raid_still_fits() -> None:
    config = load_grok_config({"GROK_ENGINE": {"levels": {"maximum_stop_atr": 1.50}}})
    signal = evaluate_snapshot(_ready_snapshot(), config, generated_at_epoch=NOW)
    assert signal["decision"] == "READY", signal["blockingReasons"]
    assert "STOP_TOO_WIDE" not in signal["blockingReasons"]
    stop_atr = abs(float(signal["entry"]) - float(signal["stop"])) / float(signal["atr"])
    assert stop_atr <= 1.50 + 1e-6


def test_raid_stop_does_not_expand_to_unrelated_recent_wick() -> None:
    """Gold-style M15 wicks below the raid must not become the stop.

    Live XAU/USD on 2026-08-20 14:11 passed every GROK setup gate, then failed
    OPPOSING_LIQUIDITY_LIMITS_RR because the stop used min(raid, last-6-bar low)
    and was then widened to maximum_stop_atr. The ICT invalidation is the raid
    extreme. An unrelated later wick is not a new stop.
    """
    snapshot = _ready_snapshot(display="XAU/USD", asset_type="commodity")
    m15 = list(snapshot.frames["M15"])
    m5 = list(snapshot.frames["M5"])
    raid_extreme = 1.09790
    extra_low = 1.05000
    victim = m15[-2]
    m15[-2] = _candle(victim.time, victim.open, victim.high, extra_low, victim.close, victim.volume or 230)
    config = load_grok_config()
    geometry = _execution_geometry(
        m15,
        m5,
        {"extreme": raid_extreme, "direction": 1},
        {"pdh": 1.10580, "pdl": 1.09420},
        1,
        config.levels,
        int(config.indicators["atr_period"]),
    )
    assert geometry["valid"] is True, geometry
    assert geometry["reason"] is None
    assert geometry["stop"] > extra_low + 0.02
    assert geometry["stop"] < raid_extreme
    assert float(geometry["rr"]) >= float(config.levels["minimum_rr"]) - 1e-9
    assert float(geometry["stopAtr"]) <= float(config.levels["maximum_stop_atr"]) + 1e-9


def test_failed_stop_width_still_stamps_entry_stop_and_target() -> None:
    config = load_grok_config(
        {"GROK_ENGINE": {"levels": {"minimum_stop_atr": 0.01, "maximum_stop_atr": 0.02}}}
    )
    signal = evaluate_snapshot(_ready_snapshot(), config, generated_at_epoch=NOW)
    assert any("STOP_TOO_WIDE" in reason for reason in signal["blockingReasons"])
    assert signal["decision"] != "READY"
    assert signal["entry"] is not None and signal["entry"] > 0
    assert signal["stop"] is not None and signal["stop"] > 0
    assert signal["target"] is not None and signal["target"] > 0
    assert signal["rr"] is not None and signal["rr"] > 0


def test_outside_killzone_cannot_be_ready() -> None:
    lunch = datetime(2026, 3, 17, 16, 20, tzinfo=timezone.utc).timestamp()
    snapshot = _ready_snapshot(as_of=lunch)
    signal = evaluate_snapshot(snapshot, load_grok_config(), generated_at_epoch=lunch)
    assert signal["decision"] != "READY"
    assert any("OUTSIDE_KILLZONE" in reason for reason in signal["blockingReasons"])


def test_future_closed_bar_fails_closed() -> None:
    snapshot = _ready_snapshot()
    frames = {timeframe: list(rows) for timeframe, rows in snapshot.frames.items()}
    prior = frames["H1"][-1]
    frames["H1"].append(Candle(NOW, prior.close, prior.close + 0.002, prior.close - 0.001, prior.close + 0.001, 1000, "synthetic"))
    signal = evaluate_snapshot(
        MarketSnapshot(snapshot.pair, frames, snapshot.provenance, NOW),
        load_grok_config(),
        generated_at_epoch=NOW,
    )
    assert signal["decision"] == "BLOCKED"
    assert any(reason.startswith("FUTURE_DATA:H1:") for reason in signal["blockingReasons"])


def test_same_bar_stop_and_target_is_scored_stop_first() -> None:
    signal = {"direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 102.0}
    future = [Candle(NOW, 100.0, 103.0, 98.0, 101.0, 1, "synthetic")]
    assert _outcome(signal, future) == {"outcome": "LOSS", "rMultiple": -1.0, "barsHeld": 1, "exit": 99.0}


def test_signal_upsert_preserves_execution_foreign_key(tmp_path) -> None:
    repository = GrokRepository(tmp_path / "ledger.db")
    repository.migrate()
    signal = _ready_signal()
    first_scan = repository.create_scan({"scan": 1})
    repository.save_signals(first_scan, [signal])
    reservation, created = repository.reserve_execution(
        signal_id=signal["signalId"],
        idempotency_key="first",
        mode="paper",
        venue="mt5",
        request_payload={"signal": signal},
    )
    assert created is True
    repository.complete_execution(reservation["execution_id"], "SUCCESS", {"success": True})

    second_scan = repository.create_scan({"scan": 2})
    updated = {**signal, "score": signal["score"] + 0.01}
    repository.save_signals(second_scan, [updated])

    assert repository.get_signal(signal["signalId"])["score"] == updated["score"]
    duplicate, duplicate_created = repository.reserve_execution(
        signal_id=signal["signalId"],
        idempotency_key="second",
        mode="paper",
        venue="mt5",
        request_payload={"signal": updated},
    )
    assert duplicate_created is False
    assert duplicate["status"] == "SUCCESS"


def test_paper_execution_attests_quote_and_never_calls_broker_order(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    first = coordinator.execute(signal, mode="paper", idempotency_key="paper-1")
    second = coordinator.execute(signal, mode="paper", idempotency_key="paper-1")
    assert first["status"] == "SUCCESS"
    assert first["result"]["mode"] == "paper"
    assert first["result"]["riskCash"] == 25.0
    assert second["idempotent"] is True
    assert gateway.quote_calls == 1
    assert gateway.execute_calls == 0


def test_fresh_stock_quote_does_not_treat_spread_or_with_trend_tick_as_drift(tmp_path) -> None:
    signal = _ready_signal()
    signal["assetType"] = "stock"
    signal["direction"] = "LONG"
    signal["entry"] = 108.77
    signal["atr"] = 0.299317
    signal["stop"] = 108.0
    signal["target"] = 111.0
    quote = Quote("mt5", signal["symbol"], 108.81, 108.90, NOW - 5.2, "mt5_tick")
    coordinator = _coordinator(tmp_path, [signal], _Gateway(quote))
    preview = coordinator.preview(signal)
    assert preview["error"] != "BROKER_QUOTE_STALE"
    assert preview["quote"]["ageSec"] == pytest.approx(5.2)
    assert preview["executable"] is True, preview
    drift = next(gate for gate in preview["gates"] if gate["name"] == "quote_drift")
    assert drift["passed"] is True


def test_fresh_crypto_quote_does_not_reject_a_few_adverse_ticks(tmp_path) -> None:
    signal = _ready_signal()
    signal["assetType"] = "crypto"
    signal["venue"] = "bybit"
    signal["direction"] = "SHORT"
    signal["entry"] = 0.5316
    signal["atr"] = 0.001451
    signal["stop"] = 0.534732
    signal["target"] = 0.525023
    quote = Quote("bybit", signal["symbol"], 0.532, 0.5321, NOW, "bybit_rest")
    coordinator = _coordinator(tmp_path, [signal], _Gateway(quote), database="apt-drift.db")
    preview = coordinator.preview(signal)
    assert preview["quote"]["ageSec"] == pytest.approx(0.0)
    assert preview["error"] != "BROKER_QUOTE_STALE"
    assert preview["executable"] is True, preview
    drift = next(gate for gate in preview["gates"] if gate["name"] == "quote_drift")
    assert drift["passed"] is True


def test_fresh_index_quote_rebases_target_when_with_trend_fill_compresses_rr(tmp_path) -> None:
    signal = _ready_signal()
    signal["assetType"] = "index"
    signal["direction"] = "LONG"
    signal["entry"] = 66156.7
    signal["atr"] = 190.64649
    signal["stop"] = 65961.196562
    signal["target"] = 66567.25722
    quote = Quote("mt5", signal["symbol"], 66266.6, 66286.6, NOW, "mt5_tick")
    coordinator = _coordinator(tmp_path, [signal], _Gateway(quote), database="nikkei-geometry.db")
    preview = coordinator.preview(signal)
    assert preview["error"] != "QUOTE_DRIFT_EXCEEDS_LIMIT"
    assert preview["executable"] is True, preview
    assert preview["liveRr"] >= 1.80
    assert preview["liveTarget"] > signal["target"]
    geom = next(gate for gate in preview["gates"] if gate["name"] == "live_geometry")
    assert geom["passed"] is True
    assert geom.get("rebasedTarget") is True


def test_live_fill_past_original_target_still_rejects_geometry(tmp_path) -> None:
    signal = _ready_signal()
    signal["assetType"] = "index"
    signal["direction"] = "LONG"
    signal["entry"] = 66156.7
    signal["atr"] = 190.64649
    signal["stop"] = 65961.196562
    signal["target"] = 66567.25722
    quote = Quote("mt5", signal["symbol"], 66580.0, 66600.0, NOW, "mt5_tick")
    coordinator = _coordinator(tmp_path, [signal], _Gateway(quote), database="nikkei-past-target.db")
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "LIVE_GEOMETRY_INVALID"


def test_adverse_mid_move_still_rejects_quote_drift(tmp_path) -> None:
    signal = _ready_signal()
    signal["assetType"] = "stock"
    signal["direction"] = "LONG"
    signal["entry"] = 108.77
    signal["atr"] = 0.299317
    signal["stop"] = 108.0
    signal["target"] = 111.0
    quote = Quote("mt5", signal["symbol"], 108.366, 108.456, NOW - 5.2, "mt5_tick")
    coordinator = _coordinator(tmp_path, [signal], _Gateway(quote), database="adverse-drift.db")
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "QUOTE_DRIFT_EXCEEDS_LIMIT"


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (NOW - 20, "BROKER_QUOTE_STALE"),
        (NOW + 5, "BROKER_QUOTE_TIMESTAMP_INVALID"),
    ],
)
def test_quote_time_anomalies_reject_before_execution(tmp_path, timestamp: float, expected: str) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal, timestamp=timestamp))
    coordinator = _coordinator(tmp_path, [signal], gateway, database=f"{int(timestamp)}.db")
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == expected
    assert gateway.execute_calls == 0


def test_stale_contract_and_kill_switch_reject_before_quote(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway, kill_switch=True)
    preview = coordinator.preview({**signal, "contractVersion": "grok.old"})
    assert preview["executable"] is False
    assert preview["error"] == "SIGNAL_CONTRACT_STALE"
    assert any(gate["reason"] == "KILL_SWITCH_ACTIVE" for gate in preview["gates"])
    assert gateway.quote_calls == 0


def test_tampered_gate_proof_rejects_before_quote(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    tampered = deepcopy(signal)
    tampered["gates"][0]["passed"] = False
    preview = coordinator.preview(tampered)
    assert preview["executable"] is False
    assert preview["error"] == "SIGNAL_GATE_PROOF_INVALID"
    assert gateway.quote_calls == 0


def test_legacy_gate_proof_without_new_causal_gates_rejects_before_quote(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    legacy = deepcopy(signal)
    legacy["gates"] = [gate for gate in legacy["gates"] if gate["name"] != "trigger_aligned_recent"]

    preview = coordinator.preview(legacy)

    assert preview["executable"] is False
    assert preview["error"] == "SIGNAL_GATE_PROOF_INCOMPLETE"
    assert gateway.quote_calls == 0


def test_demo_order_rejects_real_account_attestation(tmp_path) -> None:
    signal = _ready_signal()
    real_account = {
        "error": False,
        "demo": False,
        "testnet": False,
        "accountEnvironment": "real",
        "balance": 10_000.0,
        "equity": 10_000.0,
    }
    gateway = _Gateway(_passing_quote(signal), account=real_account)
    coordinator = _coordinator(tmp_path, [signal], gateway)
    result = coordinator.execute(signal, mode="demo", idempotency_key="demo-real-mismatch")
    assert result["status"] == "REJECTED"
    assert result["result"]["error"] == "DEMO_ACCOUNT_ATTESTATION_FAILED"
    assert gateway.execute_calls == 0


def test_demo_order_reaches_broker_with_freshness_and_quote_contract(tmp_path, monkeypatch) -> None:
    import guardian
    import risk_engine

    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    approval = risk_engine.RiskApproval(True, 0.1, 10.0, 0.001, 0.001, 0.0, "OK", "calculated", "calculated")
    monkeypatch.setattr(guardian, "pre_trade_check", lambda *args, **kwargs: (True, "OK"))
    monkeypatch.setattr(risk_engine, "risk_check", lambda *args, **kwargs: approval)
    result = coordinator.execute(signal, mode="demo", idempotency_key="demo-contract")
    assert result["status"] == "SUCCESS"
    assert gateway.execute_calls == 1
    assert gateway.last_payload["grokExecution"] is True
    assert gateway.last_payload["engine"] == "GROK"
    assert set(gateway.last_payload["candleFreshness"]) == {"D1", "H1", "M15", "M5"}
    assert gateway.last_payload["sl"] == signal["stop"]
    assert gateway.last_payload["tp1"] == signal["target"]


def test_risk_gateway_cannot_mutate_grok_levels(tmp_path, monkeypatch) -> None:
    import guardian
    import risk_engine

    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    approval = risk_engine.RiskApproval(True, 0.1, 10.0, 0.001, 0.001, 0.0, "OK")

    def mutating_guardian(payload, *args, **kwargs):
        payload["sl"] = float(payload["sl"]) - 0.01
        return True, "OK"

    monkeypatch.setattr(guardian, "pre_trade_check", mutating_guardian)
    monkeypatch.setattr(risk_engine, "risk_check", lambda *args, **kwargs: approval)
    result = coordinator.execute(signal, mode="demo", idempotency_key="mutating-risk")
    assert result["status"] == "REJECTED"
    assert result["result"]["error"] == "IMMUTABLE_LEVELS_MUTATED_BY_RISK_GATE"
    assert gateway.execute_calls == 0


def test_grok_execute_blocked_when_engine_disabled(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    repository = GrokRepository(tmp_path / "disabled.db")
    _store_signals(repository, [signal])
    coordinator = GrokExecutionCoordinator(
        config=load_grok_config({"GROK_ENGINE": {"enabled": False}}),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: False,
        now_fn=lambda: NOW,
    )
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "GROK_ENGINE_DISABLED"
    with pytest.raises(GrokExecutionError) as error:
        coordinator.execute(signal, mode="paper", idempotency_key="disabled")
    assert error.value.code == "GROK_ENGINE_DISABLED"
    assert gateway.quote_calls == 0
    assert gateway.execute_calls == 0


def test_grok_kill_switch_alone_rejects_execute(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway, kill_switch=True)
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "KILL_SWITCH_ACTIVE"
    assert gateway.quote_calls == 0


def test_grok_execute_rejects_stale_closed_bars_inside_signal_age(tmp_path) -> None:
    signal = _ready_signal()
    later = NOW + 9 * 60
    gateway = _Gateway(_passing_quote(signal, timestamp=later))
    repository = GrokRepository(tmp_path / "stale-bars.db")
    _store_signals(repository, [signal])
    coordinator = GrokExecutionCoordinator(
        config=load_grok_config(),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: False,
        now_fn=lambda: later,
    )
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert str(preview["error"] or "").startswith("STALE_DATA:M5:")
    assert gateway.execute_calls == 0


def test_grok_execute_rejects_after_session_close(tmp_path) -> None:
    signal = _ready_signal()
    friday = datetime(2026, 3, 20, 21, 20, tzinfo=timezone.utc).timestamp()
    stamp = "2026-03-20T21:10:00Z"
    signal["generatedAt"] = stamp
    for gate in signal.get("gates") or []:
        if isinstance(gate, dict) and str(gate.get("name") or "").endswith("_freshness"):
            gate["lastClosedAt"] = stamp
    for meta in (signal.get("dataProvenance") or {}).values():
        if isinstance(meta, dict):
            meta["lastClosedAt"] = stamp
    gateway = _Gateway(_passing_quote(signal, timestamp=friday))
    repository = GrokRepository(tmp_path / "session-close.db")
    _store_signals(repository, [signal])
    coordinator = GrokExecutionCoordinator(
        config=load_grok_config(),
        repository=repository,
        gateway=gateway,
        root_config={"EXECUTOR_MODE": "demo", "REAL_ORDERS_ALLOWED": False},
        kill_switch_fn=lambda: False,
        now_fn=lambda: friday,
    )
    preview = coordinator.preview(signal)
    assert preview["executable"] is False
    assert preview["error"] == "SESSION_CLOSED"
    assert gateway.quote_calls == 0


def test_grok_execute_rejects_pair_removed_from_active_book(tmp_path) -> None:
    from grok_engine.service import GrokService

    signal = _ready_signal()
    repository = GrokRepository(tmp_path / "pair-book.db")
    _store_signals(repository, [signal])
    service = GrokService(
        config=load_grok_config(),
        repository=repository,
        market_data=None,
        pair_provider=lambda: [],
        execution=None,
        log=SimpleNamespace(exception=lambda *args, **kwargs: None),
    )
    with pytest.raises(GrokExecutionError) as error:
        service._executable_signal(signal["signalId"])
    assert error.value.code == "PAIR_NOT_IN_ACTIVE_BOOK"


def test_grok_pair_filter_matches_xau_by_prefix() -> None:
    from grok_engine.service import _symbol_query_matches

    xau = {"display": "XAU/USD", "symbol": "GC=F", "type": "commodity"}
    zar = {"display": "XAU/ZAR", "symbol": "XAUZAR=X", "type": "commodity"}
    eurusd = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    assert _symbol_query_matches("XAU", xau) is True
    assert _symbol_query_matches("XAU", zar) is True
    assert _symbol_query_matches("XAU/USD", xau) is True
    assert _symbol_query_matches("XAU", eurusd) is False
    assert _symbol_query_matches("USD", xau) is False
    assert _symbol_query_matches("USD", eurusd) is False


def test_grok_risk_policy_stays_fail_closed_and_not_engine_a() -> None:
    from tp_sl_rr_gate_policy import engine_ab_profitability_gates_enforced, resolve_profitability_gate_engine

    signal = {"engine": "GROK", "grokExecution": True, "source": "grok_engine"}
    assert resolve_profitability_gate_engine(signal) is None
    assert (
        engine_ab_profitability_gates_enforced(
            {"ENGINE_AB_PROFITABILITY_GATES_ENFORCED": False},
            signal=signal,
            engine="grok",
        )
        is True
    )


def test_idempotency_key_cannot_be_reused_for_another_signal(tmp_path) -> None:
    first_signal = _ready_signal(display="GROK-ONE")
    second_signal = _ready_signal(display="GROK-TWO")
    gateway = _Gateway(_passing_quote(first_signal))
    coordinator = _coordinator(tmp_path, [first_signal, second_signal], gateway)
    coordinator.execute(first_signal, mode="paper", idempotency_key="shared-key")
    with pytest.raises(GrokExecutionError) as error:
        coordinator.execute(second_signal, mode="paper", idempotency_key="shared-key")
    assert error.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert gateway.execute_calls == 0


def test_live_mode_is_disabled_by_default(tmp_path) -> None:
    signal = _ready_signal()
    gateway = _Gateway(_passing_quote(signal))
    coordinator = _coordinator(tmp_path, [signal], gateway)
    with pytest.raises(GrokExecutionError) as error:
        coordinator.execute(signal, mode="live", idempotency_key="live", confirm_live=True)
    assert error.value.code == "LIVE_EXECUTION_DISABLED"
    assert gateway.quote_calls == 0


def test_api_routes_register_under_the_grok_namespace() -> None:
    config = load_grok_config()

    class Service:
        def health(self):
            return {"success": True, "engine": "GROK"}

        def config_dict(self):
            return config.public_dict()

        def accounts(self):
            return {"success": True, "venues": {}}

        def current_scan(self):
            return None

        def signals(self, **kwargs):
            return []

        def execution_history(self, **kwargs):
            return []

        def preview_execution(self, signal_id):
            return {
                "executable": False,
                "error": "SPREAD_TOO_WIDE",
                "gates": [{"name": "spread", "passed": False, "reason": "SPREAD_TOO_WIDE"}],
            }

    service = Service()
    service.config = config
    app = Flask(__name__)
    register_grok_routes(app, SimpleNamespace(service=service))
    response = app.test_client().get("/api/grok/health")
    preview = app.test_client().post("/api/grok/signals/test/preview", json={})
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert response.status_code == 200
    assert response.get_json()["engine"] == "GROK"
    assert preview.status_code == 200
    assert preview.get_json()["error"] == "SPREAD_TOO_WIDE"
    assert "/api/grok/signals/<signal_id>/execute" in rules
    assert "/api/grok/accounts" in rules
    assert "/api/grok/replay" in rules


def test_api_preview_maps_execution_errors_to_forbidden() -> None:
    config = load_grok_config()

    class Service:
        def preview_execution(self, signal_id):
            raise GrokExecutionError("PAIR_NOT_IN_ACTIVE_BOOK")

        def execute_signal(self, *args, **kwargs):
            raise GrokExecutionError("GROK_ENGINE_DISABLED")

    service = Service()
    service.config = config
    app = Flask(__name__)
    register_grok_routes(app, SimpleNamespace(service=service))
    client = app.test_client()

    preview = client.post("/api/grok/signals/test/preview", json={})
    execute = client.post(
        "/api/grok/signals/test/execute",
        json={"mode": "paper", "idempotencyKey": "preview-error"},
    )

    assert preview.status_code == 403
    assert preview.get_json()["error"] == "PAIR_NOT_IN_ACTIVE_BOOK"
    assert execute.status_code == 403
    assert execute.get_json()["error"] == "GROK_ENGINE_DISABLED"


def test_api_rejected_execute_surfaces_the_gate_code() -> None:
    config = load_grok_config()

    class Service:
        def execute_signal(self, signal_id, **kwargs):
            return {
                "idempotent": False,
                "execution_id": "grokexec_test",
                "signal_id": signal_id,
                "status": "REJECTED",
                "result": {"success": False, "error": "SPREAD_TOO_WIDE", "detail": None},
            }

    service = Service()
    service.config = config
    app = Flask(__name__)
    register_grok_routes(app, SimpleNamespace(service=service))
    response = app.test_client().post(
        "/api/grok/signals/test/execute",
        json={"mode": "paper", "idempotencyKey": "reject-code"},
    )
    payload = response.get_json()
    assert response.status_code == 403
    assert payload["success"] is False
    assert payload["error"] == "SPREAD_TOO_WIDE"
    assert payload["result"]["error"] == "SPREAD_TOO_WIDE"
