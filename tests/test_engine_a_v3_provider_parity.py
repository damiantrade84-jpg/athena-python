from __future__ import annotations

import pandas as pd
import warnings

from athena_research import data_loader


def _frame() -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=20, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        index=index,
    )


def test_required_bybit_source_never_falls_through_to_binance(monkeypatch):
    monkeypatch.setattr(data_loader, "_fetch_bybit_rest", lambda *args: _frame())
    monkeypatch.setattr(
        data_loader,
        "_fetch_binance_rest",
        lambda *args: (_ for _ in ()).throw(AssertionError("binance fallback forbidden")),
    )
    frame, source, notes = data_loader._dispatch(
        "BTC/USDT", "H1", 20, False, required_source="bybit_rest"
    )
    assert len(frame) == 20
    assert source == "bybit_rest"
    assert notes == ""


def test_required_source_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(data_loader, "_fetch_bybit_rest", lambda *args: None)
    frame, source, notes = data_loader._dispatch(
        "BTC/USDT", "H1", 20, False, required_source="bybit_rest"
    )
    assert frame is None
    assert source == "DATA_UNAVAILABLE"
    assert "required_source_failed:bybit_rest" in notes


def test_bybit_timestamp_conversion_has_no_string_unit_future_warning(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            rows = [[str(1_700_000_000_000 + i * 3_600_000), "1", "2", ".5", "1.5", "10", "15"] for i in range(10)]
            return {"retCode": 0, "result": {"list": rows}}

    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        frame = data_loader._fetch_bybit_rest("BTC/USDT", "H1", 10)
    assert len(frame) == 10
    assert not [item for item in caught if issubclass(item.category, FutureWarning)]
