from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3
import sys
import tempfile
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
    monkeypatch.setitem(
        athena_module.CONFIG["AI_SCALP_CHART_REVIEW"],
        "EXECUTE_REQUIRES_AI_REVIEW",
        False,
    )

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
        captured.update(kwargs)
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
    assert captured["sizing_override"] == 1.0
    assert captured.get("volume_mode") in (None, "min_lot")


def test_scalp_execute_calculated_mode_uses_grade_multiplier(monkeypatch):
    athena_module = _load_athena_module()
    captured = {}
    monkeypatch.setitem(
        athena_module.CONFIG["AI_SCALP_CHART_REVIEW"],
        "EXECUTE_REQUIRES_AI_REVIEW",
        False,
    )

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
        captured.update(kwargs)
        return _Approval()

    monkeypatch.setattr(risk_engine, "risk_check", _fake_risk_check)
    monkeypatch.setattr(
        execution_lifecycle,
        "run_managed_execution",
        lambda venue, signal, approval: {"success": True, "ticket": "123", "volume": 0.01, "entry_price": 1.1},
    )

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={"symbol": "EUR/USD", "volume_mode": "calculated", "sizing_override": 1.0},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["success"] is True
    assert captured["sizing_override"] == 0.25
    assert captured["volume_mode"] == "calculated"


def test_scalp_execute_rejects_without_ai_review(monkeypatch):
    athena_module = _load_athena_module()
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
                    "ai_grade": "B",
                    "gate_result": "PASS",
                    "executable": True,
                }
            ]
        },
    )
    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={"symbol": "EUR/USD", "signal": {"symbol": "EUR/USD", "direction": "LONG"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is False
    assert data["error"] == "AI_REVIEW_REQUIRED"


def test_scalp_execute_accepts_watchlist_with_fresh_ai_review(monkeypatch):
    athena_module = _load_athena_module()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        audit_db = tmp.name
    monkeypatch.setattr(athena_module, "_AUDIT_DB", audit_db)

    from ai_review.persistence import ensure_schema, record_review
    from datetime import datetime, timezone

    ensure_schema(audit_db)
    with sqlite3.connect(audit_db) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                ts TEXT, pair TEXT, score REAL, max_score REAL, engine TEXT, direction TEXT,
                grade TEXT, risk TEXT, style TEXT, entry_price REAL, sl REAL, tp REAL,
                tp_partial REAL, tp2 REAL, volume REAL, ticket TEXT, risk_amount REAL,
                risk_pct REAL, asset_class TEXT, regime TEXT, factors_json TEXT
            )
            """
        )
        con.commit()
    engine_d_ctx = {
        "symbol": "EUR/USD",
        "direction": "LONG",
        "execution_tf": "M1",
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
        "latest_candle_ts": datetime.now(timezone.utc).isoformat(),
        "scalpSetup": {"entry": 1.1, "stopLoss": 1.095, "tp1": 1.11},
    }
    review_row = record_review(
        symbol="EUR/USD",
        timeframe="M5",
        asset_group=None,
        provider="anthropic",
        model="test",
        latency_ms=1,
        screenshot_hash="hash1",
        screenshot_bytes=10,
        screenshot_meta={"native_chart": True},
        engine_d_context=engine_d_ctx,
        ai_review={
            "decision": "ENTRY_NOW",
            "entryAllowedNow": True,
            "structured": {"decision": "ENTRY_NOW", "entryAllowedNow": True},
            "verdict": "VALID",
            "parse_success": True,
        },
        concordance={},
        mismatch_warnings=[],
        audit_db=audit_db,
        review_type="engine_d",
    )

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
                    "ai_grade": "B",
                    "gate_result": "WATCHLIST",
                    "executable": False,
                    "soft_warnings": ["rr_below_min"],
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

    monkeypatch.setattr(risk_engine, "risk_check", lambda **kwargs: _Approval())
    monkeypatch.setattr(
        execution_lifecycle,
        "run_managed_execution",
        lambda venue, signal, approval: {"success": True, "ticket": "999", "volume": 0.01, "entry_price": 1.1},
    )
    monkeypatch.setattr(athena_module, "_guardian_pre_trade", lambda *args, **kwargs: (True, None))

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={
            "symbol": "EUR/USD",
            "review_id": review_row["review_id"],
            "signal": {"symbol": "EUR/USD", "direction": "LONG"},
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is True


def test_scalp_execute_rejects_disabled_pair_before_scan(monkeypatch):
    athena_module = _load_athena_module()
    monkeypatch.setattr(athena_module, "_disabled_pairs", {"EUR/USD"})

    def _unexpected_scan(_pairs):
        raise AssertionError("disabled pair should be rejected before Engine D scan")

    monkeypatch.setattr(scalp_engine, "run_scalp_scan", _unexpected_scan)

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={"symbol": "EUR/USD", "signal": {"symbol": "EUR/USD", "direction": "LONG"}},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is False
    assert data["error"] == "PAIR_DISABLED"


def test_scalp_execute_rejects_disabled_pair_yahoo_symbol_before_scan(monkeypatch):
    athena_module = _load_athena_module()
    monkeypatch.setattr(athena_module, "_disabled_pairs", {"EUR/USD"})

    def _unexpected_scan(_pairs):
        raise AssertionError("disabled Yahoo-format pair should be rejected before Engine D scan")

    monkeypatch.setattr(scalp_engine, "run_scalp_scan", _unexpected_scan)

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={"symbol": "EURUSD=X", "signal": {"symbol": "EURUSD=X", "direction": "LONG"}},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is False
    assert data["error"] == "PAIR_DISABLED"


def test_scalp_execute_does_not_reject_different_disabled_crypto_settlement_symbol(monkeypatch):
    athena_module = _load_athena_module()
    monkeypatch.setattr(athena_module, "_disabled_pairs", {"BTC/USDT:USDT"})

    def _fake_scan(_pairs):
        return {"signals": [], "skipped": [{"pair": "ETH/USDT:USDT", "reason": "no_setup"}]}

    monkeypatch.setattr(scalp_engine, "run_scalp_scan", _fake_scan)

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/scalp-execute",
        json={"symbol": "ETH/USDT:USDT", "signal": {"symbol": "ETH/USDT:USDT", "direction": "LONG"}},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["success"] is False
    assert data["error"] == "no_setup"


def test_modular_scalp_execute_rejects_non_executable_signal():
    assert (
        execution._engine_d_execution_block_reason(
            {
                "pair": "EUR/USD",
                "direction": "LONG",
                "ai_grade": "D",
                "executable": False,
                "candidate_status": "grade_D_context_only",
            }
        )
        == "ENGINE_D_GRADE_D_NOT_EXECUTABLE"
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
        CONFIG = {"AI_SCALP_CHART_REVIEW": {"EXECUTE_REQUIRES_AI_REVIEW": False}}
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
    assert "sourceContract" in out
    assert "marketLocation" in out
    assert "aggressionContext" in out
    assert "scalpSetup" in out
    assert out["sourceContract"]["orderflowSourceIsReal"] is True
    assert out["sourceContract"]["cvdSourceIsReal"] is True
    assert out["sourceContract"]["vpSourceIsReal"] is True
    assert out["orderflow_source_is_real"] is True
    assert out["cvd_source_is_real"] is True
    assert out["vp_source_is_real"] is True
    assert isinstance(out["sourceContract"]["absorptionSourceIsReal"], bool)
    assert out["scalpSetup"]["skipped"] is False


def test_scalp_ui_signal_source_contract_does_not_fabricate_missing_lvn_levels():
    athena_module = _load_athena_module()

    out = athena_module._scalp_ui_signal(
        {
            "pair": "EUR/USD",
            "direction": "LONG",
            "price": 1.1,
            "sl": 1.09,
            "tp1": 1.12,
            "tp2": 1.13,
            "rr1": 2.0,
            "ai_grade": "B",
            "vp_poc": 1.101,
            "vp_vah": 1.105,
            "vp_val": 1.095,
            "vp_lvn_count": 3,
            "vp_volume_source": "mt5_tick",
            "vp_is_proxy": True,
            "cvd_source": "candles",
            "cvd_is_proxy": True,
            "absorption_source": "mt5_tick",
            "absorption_is_proxy": True,
            "aggression_source": "candles",
            "aggression_source_is_proxy": True,
            "aggression_uses_real_order_flow": False,
            "execution_tf": "M1",
            "candleFetchMeta": {
                "pairSource": "mt5",
                "M1": {"last_scoring_ts": 1_700_000_000},
            },
        }
    )

    assert out["marketLocation"]["lvnLevels"] == []
    assert "lvn_count_available_but_levels_missing" in out["sourceContract"]["unavailableReasons"]
    assert out["sourceContract"]["orderflowSourceIsReal"] is False
    assert out["sourceContract"]["strictOrderflowSourcePass"] is False
    assert out["sourceContract"]["strictVolumeSourcePass"] is False
    assert out["strict_orderflow_source_pass"] is False
    assert "source_fidelity_summary" in out
    assert "proxy_warning" in out
    assert out["scalpSetup"]["skipped"] is False
    assert out["sourceContract"]["unavailableReasons"]


def test_scalp_scan_route_adds_source_contracts_to_signals_and_skips(monkeypatch):
    athena_module = _load_athena_module()

    def _fake_scan(pairs):
        return {
            "signals": [
                {
                    "pair": "BTC/USDT",
                    "display": "BTC/USDT",
                    "symbol": "BTCUSDT",
                    "type": "crypto",
                    "direction": "LONG",
                    "price": 100.0,
                    "sl": 99.0,
                    "tp1": 102.0,
                    "tp2": 103.0,
                    "rr1": 2.0,
                    "ai_grade": "B",
                    "ai_score": 72,
                    "gate_result": "WATCHLIST",
                    "executable": False,
                    "fail_reasons": ["rr_below_min"],
                    "vp_poc": 100.5,
                    "vp_vah": 101.0,
                    "vp_val": 99.5,
                    "vp_lvn_count": 2,
                    "vp_volume_source": "binance_aggtrade",
                    "vp_is_proxy": False,
                    "vp_uses_real_trade_buckets": True,
                    "cvd_source": "binance_aggtrade",
                    "cvd_is_proxy": False,
                    "cvd_uses_real_trade_buckets": True,
                    "cvd_slope": 3.5,
                    "absorption_source": "binance_candle",
                    "absorption_is_proxy": True,
                    "absorption_count": 1,
                    "aggression_source": "binance_aggtrade",
                    "aggression_source_is_proxy": False,
                    "aggression_confirmed": True,
                    "aggression_uses_real_order_flow": True,
                    "strict_fabio_pass": False,
                    "strict_fabio_missing_pillars": ["location"],
                    "execution_tf": "M1",
                    "candleFetchMeta": {
                        "pairSource": "binance",
                        "M1": {"last_scoring_ts": 1_700_000_000},
                    },
                }
            ],
            "skipped": [{"pair": "ETH/USDT", "reason": "no_setup:location"}],
            "scanned": len(pairs),
            "session": "london",
            "sessions_active": ["london"],
        }

    monkeypatch.setattr(scalp_engine, "run_scalp_scan", _fake_scan)
    client = athena_module.app.test_client()
    resp = client.post("/api/scalp-scan", json={"pairs": ["BTC/USDT"], "diagnostic": True})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    signal = payload["signals"][0]
    skipped = payload["skipped"][0]

    for key in ("symbol", "entry", "sl", "tp1", "tp2", "rr", "gate_result", "executable"):
        assert key in signal
    for key in ("sourceContract", "marketLocation", "aggressionContext", "scalpSetup"):
        assert key in signal
    assert signal["marketLocation"]["lvnLevels"] == []
    assert "lvn_count_available_but_levels_missing" in signal["sourceContract"]["unavailableReasons"]
    assert isinstance(signal["sourceContract"]["venueMismatch"], bool)
    assert isinstance(signal["sourceContract"]["candleSourceIsReal"], bool)
    assert isinstance(signal["sourceContract"]["orderflowSourceIsReal"], bool)
    assert signal["sourceContract"]["orderflowSourceIsReal"] is True
    assert signal["orderflow_source_is_real"] is True
    assert signal["sourceContract"]["absorptionSourceIsReal"] is False
    assert signal["absorption_source_is_real"] is False
    assert "rr_below_min" in signal["scalpSetup"]["strictGateReasons"]
    assert signal["aggressionContext"]["absorptionDetected"] is True
    assert signal["aggressionContext"]["cvdSlope"] == 3.5
    assert skipped["sourceContract"]["strictOrderflowSourcePass"] is False
    assert skipped["scalpSetup"]["skipped"] is True
    assert skipped["scalpSetup"]["skippedReason"] == "no_setup:location"
    assert "sourceContract" in skipped and "scalpSetup" in skipped


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


def test_open_trades_timed_exposes_unresolved_audit_rows(monkeypatch, tmp_path):
    athena_module = _load_athena_module()
    db_path = tmp_path / "audit.db"
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                pair TEXT,
                direction TEXT,
                entry_price REAL,
                sl REAL,
                tp REAL,
                volume REAL,
                engine TEXT,
                style TEXT,
                ticket TEXT,
                asset_class TEXT,
                exit_price REAL
            )
            """
        )
        con.executemany(
            """
            INSERT INTO audit_log (
                id, ts, pair, direction, entry_price, sl, tp, volume, engine,
                style, ticket, asset_class, exit_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "2026-05-22T08:00:00+00:00", "EUR/CHF", "SHORT", 0.91624, 0.92118, 0.90816, 0.18, "engine_a", "swing", "309018151", "forex", None),
                (2, "2026-05-22T08:01:00+00:00", "EUR/USD", "LONG", 1.1, 1.09, 1.12, 0.01, "engine_a", "swing", "123", "forex", None),
                (3, "2026-05-22T08:02:00+00:00", "EUR/USD", "LONG", 1.1, 1.09, 1.12, 0.01, "engine_a", "swing", "123", "forex", None),
            ],
        )

    monkeypatch.setattr(athena_module, "_AUDIT_DB", str(db_path))
    monkeypatch.setattr(
        mt5_executor,
        "mt5_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": "309018151",
                    "pair": "EUR/CHF",
                    "direction": "SHORT",
                    "profit": 90.0,
                    "entry": 0.91624,
                    "sl": 0.92118,
                    "tp": 0.90816,
                    "volume": 0.18,
                    "open_time": 1710000000,
                }
            ],
        },
    )
    import bybit_executor
    monkeypatch.setattr(bybit_executor, "bybit_get_positions", lambda: {"error": False, "positions": []})
    import timed_exit_monitor
    monkeypatch.setattr(timed_exit_monitor, "_load_recent_audit_rows", lambda _db: [])
    monkeypatch.setattr(timed_exit_monitor, "_match_audit_row_for_position", lambda p, rows: {})

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["count"] == 1
    assert payload["audit_unresolved_count"] == 2
    rows = {row["ticket"]: row for row in payload["audit_unresolved"]}
    assert rows["309018151"]["broker_live"] is True
    assert rows["309018151"]["status"] == "live_broker_position"
    assert rows["123"]["broker_live"] is False
    assert rows["123"]["status"] == "audit_only_no_broker_match"
    assert rows["123"]["duplicate_count"] == 2
    assert rows["123"]["close_action_enabled"] is True


def _seed_audit_orphan_table(db_path, rows):
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                pair TEXT,
                direction TEXT,
                entry_price REAL,
                sl REAL,
                tp REAL,
                volume REAL,
                engine TEXT,
                style TEXT,
                ticket TEXT,
                asset_class TEXT,
                exit_price REAL,
                exit_time TEXT,
                pnl REAL,
                r_multiple REAL,
                exit_reason TEXT,
                holding_period_hours REAL
            )
            """
        )
        for row in rows:
            con.execute(
                """
                INSERT INTO audit_log (
                    id, ts, pair, direction, entry_price, sl, tp, volume, engine,
                    style, ticket, asset_class, exit_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )


def test_reconcile_orphan_scratch_closes_audit_only_row(monkeypatch, tmp_path):
    athena_module = _load_athena_module()
    orphan_ticket = str(uuid.uuid4())
    db_path = tmp_path / "audit.db"
    _seed_audit_orphan_table(
        db_path,
        [
            (
                1,
                "2026-05-22T08:00:00+00:00",
                "AAVE/USDT",
                "SHORT",
                185.5,
                190.0,
                175.0,
                1.0,
                "scalp",
                "scalp",
                orphan_ticket,
                "crypto",
                None,
            ),
        ],
    )
    monkeypatch.setattr(athena_module, "_AUDIT_DB", str(db_path))
    monkeypatch.setattr(
        athena_module,
        "_broker_open_context_for_audit",
        lambda _db: (set(), set(), set()),
    )

    client = athena_module.app.test_client()
    resp = client.post("/api/audit/reconcile-orphan", json={"ticket": orphan_ticket})

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["closed"] == 1
    assert payload["results"][0]["status"] == "closed"

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT exit_price, pnl, exit_reason FROM audit_log WHERE ticket=?",
            (orphan_ticket,),
        ).fetchone()
    assert row[0] == 185.5
    assert row[1] == 0.0
    assert row[2] == "AUDIT_ORPHAN_RECONCILE"


def test_reconcile_orphan_rejects_broker_live_ticket(monkeypatch, tmp_path):
    athena_module = _load_athena_module()
    db_path = tmp_path / "audit.db"
    _seed_audit_orphan_table(
        db_path,
        [
            (
                1,
                "2026-05-22T08:00:00+00:00",
                "EUR/CHF",
                "SHORT",
                0.91624,
                0.92118,
                0.90816,
                0.18,
                "engine_a",
                "swing",
                "309818151",
                "forex",
                None,
            ),
        ],
    )
    monkeypatch.setattr(athena_module, "_AUDIT_DB", str(db_path))
    monkeypatch.setattr(
        athena_module,
        "_broker_open_context_for_audit",
        lambda _db: ({"309818151"}, set(), set()),
    )

    client = athena_module.app.test_client()
    resp = client.post("/api/audit/reconcile-orphan", json={"ticket": "309818151"})

    assert resp.status_code == 409
    payload = resp.get_json()
    assert payload["success"] is False
    assert payload["error"] == "broker_position_still_open"

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT exit_price FROM audit_log WHERE ticket=?",
            ("309818151",),
        ).fetchone()
    assert row[0] is None


def test_reconcile_all_audit_only_bulk(monkeypatch, tmp_path):
    athena_module = _load_athena_module()
    orphan_a = str(uuid.uuid4())
    orphan_b = str(uuid.uuid4())
    db_path = tmp_path / "audit.db"
    _seed_audit_orphan_table(
        db_path,
        [
            (
                1,
                "2026-05-22T08:00:00+00:00",
                "AAVE/USDT",
                "SHORT",
                185.5,
                190.0,
                175.0,
                1.0,
                "scalp",
                "scalp",
                orphan_a,
                "crypto",
                None,
            ),
            (
                2,
                "2026-05-22T08:01:00+00:00",
                "APT/USDT",
                "LONG",
                8.5,
                8.3,
                9.0,
                10.0,
                "scalp",
                "scalp",
                orphan_b,
                "crypto",
                None,
            ),
            (
                3,
                "2026-05-22T08:02:00+00:00",
                "EUR/CHF",
                "SHORT",
                0.91624,
                0.92118,
                0.90816,
                0.18,
                "engine_a",
                "swing",
                "309818151",
                "forex",
                None,
            ),
        ],
    )
    monkeypatch.setattr(athena_module, "_AUDIT_DB", str(db_path))
    monkeypatch.setattr(
        athena_module,
        "_broker_open_context_for_audit",
        lambda _db: ({"309818151"}, set(), set()),
    )

    client = athena_module.app.test_client()
    resp = client.post(
        "/api/audit/reconcile-orphan",
        json={"reconcile_all_audit_only": True},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["closed"] == 2

    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT ticket, exit_price, exit_reason FROM audit_log ORDER BY id"
        ).fetchall()
    closed = {ticket: (exit_price, exit_reason) for ticket, exit_price, exit_reason in rows}
    assert closed[orphan_a][1] == "AUDIT_ORPHAN_RECONCILE"
    assert closed[orphan_b][1] == "AUDIT_ORPHAN_RECONCILE"
    assert closed["309818151"][0] is None


def test_open_trades_timed_marks_bybit_uuid_audit_row_live_by_position_match(monkeypatch, tmp_path):
    athena_module = _load_athena_module()
    db_path = tmp_path / "audit.db"
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                pair TEXT,
                direction TEXT,
                entry_price REAL,
                sl REAL,
                tp REAL,
                volume REAL,
                engine TEXT,
                style TEXT,
                ticket TEXT,
                asset_class TEXT,
                exit_price REAL
            )
            """
        )
        con.execute(
            """
            INSERT INTO audit_log (
                id, ts, pair, direction, entry_price, sl, tp, volume, engine,
                style, ticket, asset_class, exit_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "2026-05-22T12:38:30+00:00",
                "TRX/USDT",
                "LONG",
                0.36369,
                0.36079,
                0.37044,
                9157.0,
                "engine_a",
                "intraday",
                "f7d90954-34a1-47b3-8d3c-4b94021bc4e4",
                "crypto",
                None,
            ),
        )

    monkeypatch.setattr(athena_module, "_AUDIT_DB", str(db_path))
    monkeypatch.setattr(mt5_executor, "mt5_get_positions", lambda: {"error": False, "positions": []})
    import bybit_executor
    monkeypatch.setattr(
        bybit_executor,
        "bybit_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": "0",
                    "pair": "TRX/USDT",
                    "symbol": "TRX/USDT:USDT",
                    "direction": "LONG",
                    "side": "long",
                    "profit": 0.73,
                    "entry": 0.36369,
                    "entryPrice": 0.36369,
                    "sl": 0.36079,
                    "tp": 0.37044,
                    "volume": 9157.0,
                    "contracts": 9157.0,
                }
            ],
        },
    )
    import timed_exit_monitor
    audit_row = {
        "ticket": "f7d90954-34a1-47b3-8d3c-4b94021bc4e4",
        "pair": "TRX/USDT",
        "direction": "LONG",
        "entry_price": 0.36369,
        "volume": 9157.0,
        "style": "intraday",
        "engine": "engine_a",
        "ts": "2026-05-22T12:38:30+00:00",
    }
    monkeypatch.setattr(timed_exit_monitor, "_load_recent_audit_rows", lambda _db: [audit_row])
    monkeypatch.setattr(timed_exit_monitor, "_match_audit_row_for_position", lambda p, rows: rows[0])

    client = athena_module.app.test_client()
    resp = client.get("/api/open-trades-timed")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["count"] == 1
    assert payload["audit_unresolved_count"] == 1
    row = payload["audit_unresolved"][0]
    assert row["pair"] == "TRX/USDT"
    assert row["broker_live"] is True
    assert row["status"] == "live_broker_position"
    assert row["exchange_hint"] == "bybit/paper"


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


def test_live_dashboard_engine_d_skipped_no_setup_keeps_reason():
    row = routes_live_dashboard._ld_build_engine_d_row(
        {
            "_ts": __import__("time").time(),
            "_skipped": True,
            "gateResult": "NO_SETUP",
            "reason": "no_setup:no_aggression_at_va_extreme",
        },
        {"type": "crypto"},
    )

    assert row["gateResult"] == "NO_SETUP"
    assert row["failReasons"] == ["no_setup:no_aggression_at_va_extreme"]
    assert row["grade"] is None
