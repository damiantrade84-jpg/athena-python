from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3
import sys
import uuid

import execution
import scalp_engine
import mt5_executor
import risk_engine
import execution_lifecycle


def _load_athena_module():
    path = Path(__file__).resolve().parents[1] / "athena.py"
    spec = spec_from_file_location("athena_route_module_for_tests", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_scalp_execute_rejects_direction_flip(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        scalp_engine,
        "run_scalp_scan",
        lambda pairs: {
            "signals": [
                {
                    "pair": "EUR/USD",
                    "direction": "LONG",
                    "price": 1.1,
                    "sl": 1.095,
                    "tp1": 1.11,
                }
            ]
        },
    )

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={"symbol": "EUR/USD", "signal": {"symbol": "EUR/USD", "direction": "SHORT"}},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert "SIGNAL_FLIPPED" in data["error"]
    assert data["newDirection"] == "LONG"


def test_quick_execute_rejects_direction_flip(monkeypatch):
    athena_module = _load_athena_module()

    class _FakeLog:
        def warning(self, *args, **kwargs):
            return None

    class _FakeRt:
        CONFIG = {"SIGNAL_MAX_AGE_SEC": 300, "EXECUTION_ENABLED": True}
        ALL_PAIRS = [{"display": "EUR/USD"}]
        log = _FakeLog()

        @staticmethod
        def kill_switch():
            return False

        @staticmethod
        def analyze_pair(pair_obj, btc_bias, style="swing"):
            return {
                "pair": pair_obj["display"],
                "direction": "LONG",
                "price": 1.101,
                "timestamp": "2026-03-26T00:00:00+00:00",
            }

    monkeypatch.setattr(execution, "rt", lambda: _FakeRt())

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/quick-execute",
        json={
            "signal": {
                "pair": "EUR/USD",
                "direction": "SHORT",
                "timestamp": "2000-01-01T00:00:00+00:00",
                "type": "forex",
            },
            "pip_mode": "intraday",
        },
    )

    assert resp.status_code == 409
    data = resp.get_json()
    assert "SIGNAL_FLIPPED" in data["error"]
    assert data["newDirection"] == "LONG"


def test_scan_style_auto_resolution_contract():
    athena_module = _load_athena_module()

    assert athena_module._resolve_scan_style("auto", {"type": "forex"}) == "intraday"
    assert athena_module._resolve_scan_style("auto", {"type": "crypto"}) == "intraday"
    assert athena_module._resolve_scan_style("auto", {"type": "stock"}) == "swing"
    assert athena_module._resolve_scan_style("swing", {"type": "forex"}) == "swing"


def test_scalp_execute_passes_size_multiplier_to_risk_engine(monkeypatch):
    athena_module = _load_athena_module()
    captured = {}

    monkeypatch.setattr(
        scalp_engine,
        "run_scalp_scan",
        lambda pairs: {
            "signals": [
                {
                    "pair": "EUR/USD",
                    "direction": "LONG",
                    "type": "forex",
                    "price": 1.1,
                    "sl": 1.095,
                    "tp1": 1.11,
                    "size_multiplier": 0.25,
                }
            ]
        },
    )
    monkeypatch.setattr(mt5_executor, "mt5_get_account", lambda: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(mt5_executor, "mt5_get_positions", lambda: {"positions": []})
    monkeypatch.setattr(mt5_executor, "mt5_get_symbol_info", lambda symbol: {"digits": 5, "point": 0.00001})

    class _Approval:
        approved = True
        reason = "OK"

        @staticmethod
        def to_dict():
            return {"approved": True, "reason": "OK"}

    def _fake_risk_check(**kwargs):
        captured["sizing_override"] = kwargs["sizing_override"]
        return _Approval()

    monkeypatch.setattr(risk_engine, "risk_check", _fake_risk_check)
    monkeypatch.setattr(
        execution_lifecycle,
        "run_managed_execution",
        lambda venue, signal, approval: {"success": True, "ticket": "123", "volume": 0.01, "entry_price": 1.1},
    )

    client = athena_module.app.test_client()
    resp = client.post("/api/scalp-execute", json={"symbol": "EUR/USD"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert captured["sizing_override"] == 0.25


def _make_outcome_db(path, *, engine="scalp", style="scalp"):
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket TEXT,
                exit_reason TEXT,
                style TEXT,
                engine TEXT,
                score REAL,
                max_score REAL,
                exit_price REAL,
                exit_time TEXT,
                pnl REAL,
                r_multiple REAL,
                holding_period_hours REAL
            )
            """
        )
        con.execute(
            "INSERT INTO audit_log(ticket, style, engine, score, max_score) VALUES (?, ?, ?, ?, ?)",
            ("T1", style, engine, 75.0, 100.0),
        )
        con.commit()


def _local_test_db_path():
    root = Path(__file__).resolve().parents[1] / ".pytest_local_tmp"
    root.mkdir(exist_ok=True)
    return root / f"audit_{uuid.uuid4().hex}.db"


def test_update_trade_outcome_records_scalp_session_r(monkeypatch):
    athena_module = _load_athena_module()
    db_path = _local_test_db_path()
    _make_outcome_db(db_path, engine="scalp", style="scalp")
    calls = []

    monkeypatch.setattr(athena_module, "_AUDIT_DB", str(db_path))
    monkeypatch.setitem(athena_module.CONFIG, "LEARNING_ENABLED", False)
    monkeypatch.setattr(athena_module, "record_outcome_event", lambda **kwargs: None)
    monkeypatch.setattr(mt5_executor, "mt5_get_account", lambda: {"balance": 0.0})
    monkeypatch.setattr(scalp_engine, "record_scalp_trade_outcome", lambda r: calls.append(r))

    athena_module._update_trade_outcome(
        ticket="T1",
        exit_price=102.0,
        exit_time="2026-04-24T10:00:00+00:00",
        pnl=100.0,
        entry_price=100.0,
        sl=99.0,
        tp=102.0,
        volume=1.0,
        entry_ts="2026-04-24T09:00:00+00:00",
        risk_amount=50.0,
        asset_type="forex",
    )

    assert calls == [2.0]


def test_update_trade_outcome_skips_non_scalp_session_r(monkeypatch):
    athena_module = _load_athena_module()
    db_path = _local_test_db_path()
    _make_outcome_db(db_path, engine="engine_a", style="intraday")
    calls = []

    monkeypatch.setattr(athena_module, "_AUDIT_DB", str(db_path))
    monkeypatch.setitem(athena_module.CONFIG, "LEARNING_ENABLED", False)
    monkeypatch.setattr(athena_module, "record_outcome_event", lambda **kwargs: None)
    monkeypatch.setattr(mt5_executor, "mt5_get_account", lambda: {"balance": 0.0})
    monkeypatch.setattr(scalp_engine, "record_scalp_trade_outcome", lambda r: calls.append(r))

    athena_module._update_trade_outcome(
        ticket="T1",
        exit_price=102.0,
        exit_time="2026-04-24T10:00:00+00:00",
        pnl=100.0,
        entry_price=100.0,
        sl=99.0,
        tp=102.0,
        volume=1.0,
        entry_ts="2026-04-24T09:00:00+00:00",
        risk_amount=50.0,
        asset_type="forex",
    )

    assert calls == []


def test_scalp_execute_returns_fresh_skip_details(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        scalp_engine,
        "run_scalp_scan",
        lambda pairs: {
            "signals": [],
            "skipped": [
                {
                    "pair": "GBP/AUD",
                    "reason": "grade_C_below_min",
                    "ai_grade": "C",
                    "ai_score": 58,
                    "min_grade": "B",
                }
            ],
            "session": "london",
        },
    )

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={
            "symbol": "GBP/AUD",
            "signal": {"symbol": "GBP/AUD", "direction": "SHORT", "ai_grade": "B", "ai_score": 62},
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert data["error"] == "grade_C_below_min"
    assert data["skipped"][0]["ai_grade"] == "C"
    assert data["skipped"][0]["ai_score"] == 58


def test_open_trades_timed_hides_intraday_labels_for_scalp(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": "123",
                    "pair": "EUR/USD",
                    "direction": "LONG",
                    "profit": 5.0,
                    "entry": 1.1,
                    "sl": 1.09,
                    "tp": 1.11,
                    "volume": 0.01,
                    "open_time": 1710000000,
                }
            ],
        },
    )
    import bybit_executor
    monkeypatch.setattr(bybit_executor, "bybit_get_positions", lambda: {"error": False, "positions": []})
    import timed_exit_monitor
    monkeypatch.setattr(
        timed_exit_monitor,
        "_load_recent_audit_rows",
        lambda _db: [{"ticket": "123", "style": "scalp", "engine": "scalp", "ts": "2026-04-14T10:00:00+00:00"}],
    )
    monkeypatch.setattr(
        timed_exit_monitor,
        "_match_audit_row_for_position",
        lambda p, rows: rows[0],
    )

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["count"] == 1
    row = payload["positions"][0]
    assert row["style"] == "scalp"
    assert row["be_trigger_min"] is None
    assert row["close_trigger_min"] is None


def test_open_trades_timed_engine_scalp_overrides_stale_intraday_style(monkeypatch):
    athena_module = _load_athena_module()

    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": "777",
                    "pair": "BTC/USDT",
                    "direction": "LONG",
                    "profit": 12.0,
                    "entry": 100.0,
                    "sl": 98.0,
                    "tp": 101.0,
                    "volume": 0.01,
                    "open_time": 1710000000,
                    "engine": "scalp",
                }
            ],
        },
    )
    import bybit_executor
    monkeypatch.setattr(bybit_executor, "bybit_get_positions", lambda: {"error": False, "positions": []})
    import timed_exit_monitor
    monkeypatch.setattr(
        timed_exit_monitor,
        "_load_recent_audit_rows",
        lambda _db: [{"ticket": "777", "style": "intraday", "engine": "scalp", "ts": "2026-04-14T10:00:00+00:00"}],
    )
    monkeypatch.setattr(timed_exit_monitor, "_match_audit_row_for_position", lambda p, rows: rows[0])

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")
    assert resp.status_code == 200
    payload = resp.get_json()
    row = payload["positions"][0]
    assert row["style"] == "scalp"
    assert row["be_trigger_min"] is None
    assert row["close_trigger_min"] is None
