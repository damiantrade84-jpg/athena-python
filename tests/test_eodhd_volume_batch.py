from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eodhd_volume_batch import (
    LiveV2VolumeBatcher,
    build_non_ws_stock_pairs,
    set_live_v2_batcher,
)


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"


class _DummyResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _DummyCandleBuilder:
    def __init__(self):
        self.calls: list[tuple[str, float, float, int]] = []

    def on_tick(self, display, price, volume, ts_ms):
        self.calls.append((display, price, volume, ts_ms))


def _load_athena_main():
    spec = importlib.util.spec_from_file_location("athena_main_for_test", ATHENA_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_build_non_ws_stock_pairs_matches_current_inventory():
    mod = _load_athena_main()

    selected = build_non_ws_stock_pairs(mod.ALL_PAIRS)

    assert [p["display"] for p in selected] == [
        "GLD",
        "XLE",
    ]


def test_live_v2_batcher_baselines_initial_delta_increment_and_rollover(monkeypatch):
    import eodhd_volume_batch as evb

    cb = _DummyCandleBuilder()
    batcher = LiveV2VolumeBatcher(api_key="test", poll_interval=60)
    batcher.configure([{"display": "AMZN", "symbol": "AMZN.US", "type": "stock", "enabled": True}])

    payloads = [
        {"data": {"AMZN.US": {"volume": 1000, "lastTradePrice": 200.0, "lastTradeTime": 1710000000}}},
        {"data": {"AMZN.US": {"volume": 1250, "lastTradePrice": 201.5, "lastTradeTime": 1710000060}}},
        {"data": {"AMZN.US": {"volume": 120, "lastTradePrice": 202.0, "lastTradeTime": 1710086400}}},
    ]

    def _fake_get(*_args, **_kwargs):
        return _DummyResponse(payloads.pop(0))

    monkeypatch.setattr("data_feeds.http_requests.get", _fake_get)
    monkeypatch.setattr("candle_feeds.get_candle_builder", lambda: cb)
    monkeypatch.setattr(evb.time, "time", lambda: 1710000065.0)

    batcher._poll_once()
    batcher._poll_once()
    batcher._poll_once()

    assert cb.calls == [
        ("AMZN", 201.5, 250.0, 1710000060000),
    ]
    assert batcher.last_cumvol["AMZN"] == 120.0


def test_live_v2_skips_injection_when_quote_lag_exceeds_config(monkeypatch):
    import eodhd_volume_batch as evb

    cb = _DummyCandleBuilder()
    batcher = LiveV2VolumeBatcher(api_key="test", poll_interval=60)
    batcher.configure([{"display": "AMZN", "symbol": "AMZN.US", "type": "stock", "enabled": True}])

    monkeypatch.setattr(
        evb,
        "CONFIG",
        {"SCALP_ENGINE": {"EODHD_LIVE_V2_MAX_QUOTE_LAG_SEC": 50}},
    )
    monkeypatch.setattr(evb.time, "time", lambda: 1710000100.0)

    payloads = [
        {"data": {"AMZN.US": {"volume": 1000, "lastTradePrice": 200.0, "lastTradeTime": 1710000000}}},
    ]

    def _fake_get(*_args, **_kwargs):
        return _DummyResponse(payloads.pop(0))

    monkeypatch.setattr("data_feeds.http_requests.get", _fake_get)
    monkeypatch.setattr("candle_feeds.get_candle_builder", lambda: cb)

    batcher._poll_once()

    assert cb.calls == []
    assert batcher.last_cumvol.get("AMZN") is None


def test_live_v2_batcher_skips_quotes_when_event_marker_does_not_advance(monkeypatch):
    import eodhd_volume_batch as evb

    cb = _DummyCandleBuilder()
    batcher = LiveV2VolumeBatcher(api_key="test", poll_interval=60)
    batcher.configure([{"display": "QQQ", "symbol": "QQQ.US", "type": "stock", "enabled": True}])

    payloads = [
        {"data": {"QQQ.US": {"volume": 500, "lastTradePrice": 400.0, "timestamp": 1710000000}}},
        {"data": {"QQQ.US": {"volume": 700, "lastTradePrice": 401.0, "timestamp": 1710000000}}},
    ]

    def _fake_get(*_args, **_kwargs):
        return _DummyResponse(payloads.pop(0))

    monkeypatch.setattr("data_feeds.http_requests.get", _fake_get)
    monkeypatch.setattr("candle_feeds.get_candle_builder", lambda: cb)
    monkeypatch.setattr(evb.time, "time", lambda: 1710000005.0)

    batcher._poll_once()
    batcher._poll_once()

    assert cb.calls == []
    assert batcher.last_cumvol["QQQ"] == 500.0


def test_athena_candle_startup_seeds_before_batcher_start():
    tree = ast.parse(ATHENA_PATH.read_text(encoding="utf-8"))

    ensure_runtime = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "ensure_runtime_services_started"
    )
    cb_startup = next(
        node for node in ast.walk(ensure_runtime) if isinstance(node, ast.FunctionDef) and node.name == "_cb_startup"
    )

    line_map: dict[str, int] = {}
    for node in ast.walk(cb_startup):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "cb":
            line_map[f"cb.{node.func.attr}"] = node.lineno
        elif isinstance(node.func, ast.Name) and node.func.id == "start_live_v2_batcher_for_non_ws_stocks":
            line_map["start_live_v2_batcher_for_non_ws_stocks"] = node.lineno

    assert line_map["cb.seed"] < line_map["cb.bulk_update_d1"]
    assert line_map["cb.bulk_update_d1"] < line_map["cb.start_refresh_loop"]
    assert line_map["cb.start_refresh_loop"] < line_map["start_live_v2_batcher_for_non_ws_stocks"]


def test_forex_live_overlay_still_uses_mt5_tick():
    mod = _load_athena_main()

    pair = next(p for p in mod.ALL_PAIRS if p.get("display") == "EUR/USD")
    result = mod._fetch_eodhd_volume_only(pair, "H1", 1001)

    assert result["detail"] == "no_real_volume"
    assert result["volume_source"] == "mt5_tick"


def teardown_module():
    set_live_v2_batcher(None)
