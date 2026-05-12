from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3
import sys
import uuid

from flask import Flask

import execution
import scalp_engine
import mt5_executor
import risk_engine
import execution_lifecycle
from athena_app.api import routes_live_dashboard


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

    assert resp.status_code == 200, resp.get_data(as_text=True)
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
        risk_amount = 10.0
        risk_pct = 0.001

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

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is True
    assert captured["sizing_override"] == 0.25


def test_modular_scalp_execute_rejects_non_executable_signal():
    assert (
        execution._engine_d_execution_block_reason(
            {
                "pair": "EUR/USD",
                "direction": "LONG",
                "executable": False,
                "candidate_status": "grade_D_context_only",
            }
        )
        == "grade_D_context_only"
    )


def test_modular_scalp_execute_rejects_grade_d_signal():
    assert (
        execution._engine_d_execution_block_reason(
            {"pair": "EUR/USD", "direction": "LONG", "ai_grade": "D"}
        )
        == "ENGINE_D_GRADE_D_NOT_EXECUTABLE"
    )


def test_modular_scalp_execute_rebase_uses_pair_score_group_min_rr(monkeypatch, tmp_path):
    captured = {}
    audit_db = tmp_path / "audit.db"
    with sqlite3.connect(audit_db) as con:
        con.execute(
            """
            CREATE TABLE audit_log (
                ts TEXT, pair TEXT, score REAL, engine TEXT, direction TEXT,
                grade TEXT, risk TEXT, style TEXT, entry_price REAL, sl REAL,
                tp REAL, volume REAL, ticket TEXT, risk_amount REAL, risk_pct REAL
            )
            """
        )

    class _Log:
        def info(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

    class _Runtime:
        log = _Log()
        ALL_PAIRS = [{"display": "USD/ZAR", "type": "forex"}]
        AUDIT_DB = str(audit_db)

        @staticmethod
        def kill_switch():
            return False

    class _Approval:
        approved = True
        reason = "OK"
        risk_amount = 10.0
        risk_pct = 0.001

        @staticmethod
        def to_dict():
            return {"approved": True, "reason": "OK"}

    monkeypatch.setattr(execution, "rt", lambda: _Runtime())
    monkeypatch.setattr(mt5_executor, "mt5_get_account", lambda: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(mt5_executor, "mt5_get_positions", lambda: {"positions": []})
    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_symbol_info",
        lambda symbol: {"digits": 5, "point": 0.00001, "bid": 1.101, "ask": 1.1012},
    )
    monkeypatch.setattr(execution, "get_pair_score_group", lambda pair: "forex_exotics")
    monkeypatch.setattr(scalp_engine, "_scalp_min_rr_for_group", lambda asset_type, score_group: 1.75)

    def _levels(direction, entry, vp, setup_type, symbol_info, asset_type, **kwargs):
        captured.update(kwargs)
        return {"entry": entry, "sl": 1.095, "tp1": 1.11, "tp2": 1.12, "rr_below_min": False}

    monkeypatch.setattr(scalp_engine, "calculate_scalp_levels", _levels)

    def _risk_check(**kwargs):
        captured["signal"] = kwargs["signal"]
        return risk_engine.RiskApproval(True, 0.01, 10.0, 0.001, 0.001, 0.0, "OK")

    monkeypatch.setattr(risk_engine, "risk_check", _risk_check)
    monkeypatch.setattr(
        execution,
        "run_managed_execution",
        lambda venue, signal, approval: {"success": True, "ticket": "123", "volume": 0.01, "entryPrice": signal["price"]},
    )

    app = Flask(__name__)
    execution.register_execution_routes(app)
    client = app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={
            "signal": {
                "pair": "USD/ZAR",
                "type": "forex",
                "direction": "LONG",
                "price": 1.1,
                "sl": 1.095,
                "tp1": 1.11,
                "vp_poc": 1.1,
                "vp_vah": 1.12,
                "vp_val": 1.09,
                "zone_type": "trend_continuation",
            }
        },
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert captured["score_group"] == "forex_exotics"
    assert captured["min_rr_override"] == 1.75
    assert captured["signal"]["price"] == 1.1011


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


def test_scalp_ui_signal_preserves_flow_fidelity_fields():
    athena_module = _load_athena_module()

    out = athena_module._scalp_ui_signal(
        {
            "pair": "BTC/USDT",
            "direction": "LONG",
            "price": 100.0,
            "rr1": 1.2,
            "ai_grade": "B",
            "vp_volume_source": "binance_aggtrade",
            "vp_bucket_count": 14,
            "vp_fidelity": "real_trade_bucket",
            "vp_is_proxy": False,
            "vp_uses_real_trade_buckets": True,
            "absorption_source": "binance_candle",
            "absorption_fidelity": "absorption_candle_volume_proxy",
            "absorption_is_proxy": True,
            "cvd_source": "binance_aggtrade",
            "cvd_bucket_count": 14,
            "cvd_fidelity": "real_trade_bucket",
            "cvd_is_proxy": False,
            "cvd_uses_real_trade_buckets": True,
            "aggression_source": "binance_aggtrade",
            "aggression_confirmed": True,
            "aggression_source_is_proxy": False,
            "aggression_uses_real_order_flow": True,
            "data_fidelity": {
                "report_only": True,
                "vp_source": "binance_aggtrade",
                "cvd_source": "binance_aggtrade",
                "absorption_source": "binance_candle",
                "aggression_uses_real_order_flow": True,
            },
            "strict_fabio_pass": True,
            "strict_fabio_reason": "strict_pass",
            "strict_fabio_missing_pillars": [],
            "current_vs_strict_status": "current_pass_strict_pass",
            "profile_anchor_mode": "trade_bucket_session",
            "profile_anchor_bars": 50,
            "profile_anchor_start": "2026-05-06T10:00:00+00:00",
            "profile_anchor_end": "2026-05-06T12:00:00+00:00",
            "profile_anchor_shadow": {
                "report_only": True,
                "active_anchor": {"mode": "trade_bucket_session", "bars": 50},
                "candidates": {"prior_session": {"valid": False, "reason": "not_enough_data"}},
            },
        }
    )

    assert out["vp_volume_source"] == "binance_aggtrade"
    assert out["vp_bucket_count"] == 14
    assert out["vp_fidelity"] == "real_trade_bucket"
    assert out["vp_is_proxy"] is False
    assert out["vp_uses_real_trade_buckets"] is True
    assert out["absorption_source"] == "binance_candle"
    assert out["absorption_is_proxy"] is True
    assert out["cvd_source"] == "binance_aggtrade"
    assert out["cvd_bucket_count"] == 14
    assert out["cvd_fidelity"] == "real_trade_bucket"
    assert out["cvd_is_proxy"] is False
    assert out["cvd_uses_real_trade_buckets"] is True
    assert out["aggression_source"] == "binance_aggtrade"
    assert out["aggression_confirmed"] is True
    assert out["aggression_source_is_proxy"] is False
    assert out["aggression_uses_real_order_flow"] is True
    assert out["data_fidelity"]["report_only"] is True
    assert out["strict_fabio_pass"] is True
    assert out["strict_fabio_reason"] == "strict_pass"
    assert out["strict_fabio_missing_pillars"] == []
    assert out["current_vs_strict_status"] == "current_pass_strict_pass"
    assert out["profile_anchor_mode"] == "trade_bucket_session"
    assert out["profile_anchor_bars"] == 50
    assert out["profile_anchor_shadow"]["report_only"] is True


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
    assert row["trail_activation_r"] == 0.7
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


def test_live_dashboard_engine_d_pass_can_be_paper_candidate():
    freshness = {"gateDecision": "ALLOW"}
    engine_c = {"decisionState": "NO_SETUP", "reason": "No A/B setup"}
    engine_d = {"gateResult": "PASS"}
    levels = {"entry": 100.0, "sl": 99.0, "tp": 102.0, "tp1": 102.0, "rr": 2.0}

    final_state, main_reason, block_reason = routes_live_dashboard._ld_final_state(
        engine_c, engine_d, freshness, levels
    )

    assert final_state == "PAPER CANDIDATE"
    assert main_reason == "Engine D paper candidate"
    assert block_reason is None


def test_live_dashboard_engine_d_row_exposes_levels():
    row = routes_live_dashboard._ld_build_engine_d_row(
        {
            "_ts": __import__("time").time(),
            "gate_result": "PASS",
            "ai_grade": "B",
            "ai_score": 74,
            "direction": "LONG",
            "price": 100.0,
            "sl": 99.0,
            "tp1": 102.0,
            "tp2": 103.0,
            "rr1": 2.0,
        },
        {"type": "crypto"},
    )

    assert row["entry"] == 100.0
    assert row["sl"] == 99.0
    assert row["tp"] == 102.0
    assert row["tp1"] == 102.0
    assert row["tp2"] == 103.0
