"""Tests for ASE mt5_live PTIS ingest eligibility and symbol resolution."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import numpy as np
import pytest

from athena_ase.data.ingest import mt5_live
from athena_ase.data.ingest.common import price_series_id
from athena_ase.data.ptis import PTISStore
from athena_ase.instruments import instrument_by_symbol
from athena_ase.universe import Instrument


def _bar(time_s: int, close: float = 100.0) -> np.void:
    return np.array(
        [(time_s, close - 1, close + 1, close - 2, close, 1000)],
        dtype=[
            ("time", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("tick_volume", "i8"),
        ],
    )[0]


class _FakeMT5:
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408

    def __init__(
        self,
        *,
        selectable: set[str] | None = None,
        rates_by_symbol: dict[str, np.ndarray] | None = None,
    ):
        self.selectable = selectable or set()
        self.rates_by_symbol = rates_by_symbol or {}
        self.copy_calls: list[tuple[str, int, int, int]] = []

    def symbol_select(self, symbol: str, _enable: bool) -> bool:
        return symbol in self.selectable

    def symbol_info(self, symbol: str):
        if symbol not in self.selectable:
            return None

        class _Info:
            name = symbol

        return _Info()

    def symbol_info_tick(self, _symbol: str):
        class _Tick:
            time = int(time.time())

        return _Tick()

    def copy_rates_from_pos(self, symbol: str, tf: int, start: int, count: int):
        self.copy_calls.append((symbol, tf, start, count))
        rates = self.rates_by_symbol.get(symbol)
        if rates is None or len(rates) == 0:
            return None
        return rates

    def last_error(self):
        return (1, "stub error")


@pytest.fixture
def ptis(tmp_path):
    return PTISStore(tmp_path / "ptis")


def test_ingest_skips_jse_and_disabled_etfs(ptis: PTISStore, monkeypatch):
    fake = _FakeMT5(selectable={"EURUSD"}, rates_by_symbol={"EURUSD": np.array([_bar(int(time.time()) - 7200)])})
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)

    def _map(key: str) -> str | None:
        return {"EURUSD": "EURUSD", "TQQQ": "TQQQ.US", "NPN.JO": "NPN.JO"}.get(key)

    monkeypatch.setattr("mt5_executor.mt5_map_symbol", _map)

    npn = Instrument("NPN.JO", "Naspers", "equity", "jse", "NPN.JO.US", "SPY", True, False)
    tqqq = Instrument("TQQQ", "TQQQ", "index_etf", "default", "TQQQ.US", "SPY", False, False)
    eur = instrument_by_symbol("EURUSD")
    assert eur is not None
    assert not npn.mt5_live
    assert not tqqq.mt5_live

    mt5_live.ingest_all(ptis, timeframes=("H1",), instruments=(npn, tqqq, eur))

    copied_symbols = {call[0] for call in fake.copy_calls}
    assert "NPN.JO" not in copied_symbols
    assert "TQQQ.US" not in copied_symbols
    assert "EURUSD" in copied_symbols


def test_ingest_uses_symbol_before_display(ptis: PTISStore, monkeypatch):
    now_s = float(time.time())
    inst = Instrument(
        "FOO",
        "Wrong Name",
        "index_etf",
        "default",
        "FOO.US",
        "SPY",
        mt5_live=True,
    )
    fake = _FakeMT5(
        selectable={"FOO.US"},
        rates_by_symbol={"FOO.US": np.array([_bar(int(now_s) - 7200)])},
    )
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)

    def _map(key: str) -> str | None:
        if key == "FOO":
            return "FOO.US"
        if key == "Wrong Name":
            return "Wrong Name"
        return None

    monkeypatch.setattr("mt5_executor.mt5_map_symbol", _map)

    totals = mt5_live.ingest_all(
        ptis,
        timeframes=("H1",),
        instruments=(inst,),
    )
    assert fake.copy_calls
    assert fake.copy_calls[0][0] == "FOO.US"
    assert totals


def test_gld_falls_back_to_bare_symbol(ptis: PTISStore, monkeypatch):
    now_s = float(time.time())
    gld = instrument_by_symbol("GLD")
    assert gld is not None
    bar = np.array([_bar(int(now_s) - 7200, close=210.0)])
    fake = _FakeMT5(selectable={"GLD"}, rates_by_symbol={"GLD": bar})
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)
    monkeypatch.setattr(
        "mt5_executor.mt5_map_symbol",
        lambda key: "GLD.US" if key == "GLD" else None,
    )

    totals = mt5_live.ingest_all(ptis, timeframes=("H1",), instruments=(gld,))

    assert fake.copy_calls
    assert fake.copy_calls[0][0] == "GLD"
    sid = price_series_id("MT5", "GLD", "H1", "close")
    assert sid in totals
    assert totals[sid] > 0


def test_eligible_missing_symbol_logs_warning(ptis: PTISStore, monkeypatch, caplog):
    inst = Instrument(
        "MISS",
        "MISS",
        "index_etf",
        "default",
        "MISS.US",
        "SPY",
        mt5_live=True,
    )
    fake = _FakeMT5(selectable=set())
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)
    monkeypatch.setattr("mt5_executor.mt5_map_symbol", lambda key: f"{key}.US" if key == "MISS" else None)

    with caplog.at_level(logging.WARNING, logger="ase.ingest.mt5_live"):
        totals = mt5_live.ingest_all(ptis, timeframes=("H1",), instruments=(inst,))

    assert totals == {}
    assert any("no terminal data for MISS" in rec.message for rec in caplog.records)


def test_mt5_symbol_candidates_prefers_ticker_and_us_fallback():
    inst = Instrument("GLD", "GLD", "index_etf", "default", "GLD.US", "SPY")

    def _map(key: str) -> str | None:
        return "GLD.US" if key == "GLD" else None

    candidates = mt5_live._mt5_symbol_candidates(inst, _map)
    assert candidates == ["GLD.US", "GLD"]


def test_ingest_retracts_phantom_bars_after_last_confirmed(ptis: PTISStore, monkeypatch):
    now_s = datetime(2026, 8, 17, 6, 45, tzinfo=timezone.utc).timestamp()
    friday_open = int(datetime(2026, 8, 14, 19, 0, tzinfo=timezone.utc).timestamp())
    phantom = int(datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    inst = Instrument("FRA40", "France 40", "index_etf", "default", "FRA40", "SPY")
    sid = price_series_id("MT5", inst.symbol, "H1", "close")
    from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
    from athena_ase.data.ptis import build_row

    avail = compute_available_time_ms(
        AvailabilityRuleId.MT5_BAR, value_time_ms=phantom, bar_close_ms=phantom
    )
    ptis.append_rows(sid, [build_row(sid, phantom, avail, 8610.0)], source="MT5")

    fake = _FakeMT5(
        selectable={"FRA40.s"},
        rates_by_symbol={"FRA40.s": np.array([_bar(friday_open, close=8635.0)])},
    )
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)
    monkeypatch.setattr(mt5_live, "_broker_offset_hours", lambda: 0)
    monkeypatch.setattr("mt5_executor.mt5_map_symbol", lambda key: "FRA40.s")
    monkeypatch.setattr(mt5_live.time, "time", lambda: now_s)

    mt5_live.ingest_all(ptis, timeframes=("H1",), instruments=(inst,))

    kept = ptis.asof(sid, int(now_s * 1000), n=4)
    assert len(kept) == 1
    # shift 0: close = Friday 19:00 + 1h
    assert int(kept[0]["value_time"]) == (friday_open + 3600) * 1000
    assert float(kept[0]["value"]) == pytest.approx(8635.0)


def test_topup_fetches_full_history_when_series_missing(ptis: PTISStore, monkeypatch):
    now_s = datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc).timestamp()
    inst = instrument_by_symbol("EURUSD")
    assert inst is not None
    open_s = int(datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc).timestamp())
    fake = _FakeMT5(
        selectable={"EURUSD"},
        rates_by_symbol={"EURUSD": np.array([_bar(open_s, close=1.08)])},
    )
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)
    monkeypatch.setattr(mt5_live, "_broker_offset_hours", lambda: 0)
    monkeypatch.setattr("mt5_executor.mt5_map_symbol", lambda key: "EURUSD")
    monkeypatch.setattr(mt5_live.time, "time", lambda: now_s)
    monkeypatch.setattr(
        mt5_live, "infer_mt5_rates_time_shift_seconds", lambda *a, **k: (0, "test")
    )

    mt5_live.ingest_all(ptis, timeframes=("H1",), instruments=(inst,), topup=True)

    assert fake.copy_calls
    assert fake.copy_calls[0][3] == mt5_live._BAR_COUNTS["H1"]


def test_topup_skips_rewrite_when_last_confirmed_present(ptis: PTISStore, monkeypatch):
    now_s = datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc).timestamp()
    open_s = int(datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc).timestamp())
    close_ms = (open_s + 3600) * 1000
    inst = instrument_by_symbol("EURUSD")
    assert inst is not None
    sid = price_series_id("MT5", inst.symbol, "H1", "close")
    from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
    from athena_ase.data.ptis import build_row

    avail = compute_available_time_ms(
        AvailabilityRuleId.MT5_BAR, value_time_ms=close_ms, bar_close_ms=close_ms
    )
    ptis.append_rows(sid, [build_row(sid, close_ms, avail, 1.08)], source="MT5")

    fake = _FakeMT5(
        selectable={"EURUSD"},
        rates_by_symbol={"EURUSD": np.array([_bar(open_s, close=1.08)])},
    )
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)
    monkeypatch.setattr(mt5_live, "_broker_offset_hours", lambda: 0)
    monkeypatch.setattr("mt5_executor.mt5_map_symbol", lambda key: "EURUSD")
    monkeypatch.setattr(mt5_live.time, "time", lambda: now_s)
    monkeypatch.setattr(
        mt5_live, "infer_mt5_rates_time_shift_seconds", lambda *a, **k: (0, "test")
    )

    totals = mt5_live.ingest_all(
        ptis, timeframes=("H1",), instruments=(inst,), topup=True
    )

    assert fake.copy_calls[0][3] == mt5_live._TOPUP_BARS["H1"]
    assert totals.get(sid, 0) == 0
    kept = ptis.asof(sid, int(now_s * 1000), n=2)
    assert len(kept) == 1
    assert int(kept[0]["value_time"]) == close_ms


def test_topup_still_retracts_phantom_bars(ptis: PTISStore, monkeypatch):
    now_s = datetime(2026, 8, 17, 6, 45, tzinfo=timezone.utc).timestamp()
    friday_open = int(datetime(2026, 8, 14, 19, 0, tzinfo=timezone.utc).timestamp())
    phantom = int(datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    inst = Instrument("FRA40", "France 40", "index_etf", "default", "FRA40", "SPY")
    sid = price_series_id("MT5", inst.symbol, "H1", "close")
    from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
    from athena_ase.data.ptis import build_row

    avail = compute_available_time_ms(
        AvailabilityRuleId.MT5_BAR, value_time_ms=phantom, bar_close_ms=phantom
    )
    ptis.append_rows(sid, [build_row(sid, phantom, avail, 8610.0)], source="MT5")

    fake = _FakeMT5(
        selectable={"FRA40.s"},
        rates_by_symbol={"FRA40.s": np.array([_bar(friday_open, close=8635.0)])},
    )
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)
    monkeypatch.setattr(mt5_live, "_broker_offset_hours", lambda: 0)
    monkeypatch.setattr("mt5_executor.mt5_map_symbol", lambda key: "FRA40.s")
    monkeypatch.setattr(mt5_live.time, "time", lambda: now_s)
    monkeypatch.setattr(
        mt5_live, "infer_mt5_rates_time_shift_seconds", lambda *a, **k: (0, "test")
    )

    mt5_live.ingest_all(ptis, timeframes=("H1",), instruments=(inst,), topup=True)

    kept = ptis.asof(sid, int(now_s * 1000), n=4)
    assert len(kept) == 1
    assert int(kept[0]["value_time"]) == (friday_open + 3600) * 1000
    assert float(kept[0]["value"]) == pytest.approx(8635.0)


def test_ingest_all_stops_at_deadline(ptis: PTISStore, monkeypatch):
    inst = instrument_by_symbol("EURUSD")
    assert inst is not None
    fake = _FakeMT5(selectable={"EURUSD"}, rates_by_symbol={"EURUSD": np.array([_bar(1)])})
    monkeypatch.setattr(mt5_live, "_connect_mt5", lambda: fake)
    monkeypatch.setattr("mt5_executor.mt5_map_symbol", lambda key: "EURUSD")

    totals = mt5_live.ingest_all(
        ptis,
        timeframes=("H1",),
        instruments=(inst,),
        deadline_monotonic=time.monotonic() - 1.0,
    )

    assert totals == {}
    assert fake.copy_calls == []
