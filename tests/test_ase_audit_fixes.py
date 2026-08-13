"""Targeted tests for the 2026-07-04 independent ASE audit fixes."""

from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from athena_ase.contracts import ASESignal
from athena_ase.execution.bridge import (
    ASEExecutionDeps,
    _result_order_id,
    enforce_time_stops,
    execute_trade_signal,
)
from athena_ase.inference.decision import (
    MIN_TRADE_EXPECTED_R,
    apply_decision_rule,
    legacy_expected_r_strength,
    signal_strength,
)
from athena_ase.levels.brackets import compute_brackets
from athena_ase.registry.promotion import promote_family
from athena_ase.signals.common import (
    BarSeries,
    live_bars_stale,
    resolve_price_source,
)

_HOUR = 3_600_000
_DAY = 86_400_000


@pytest.fixture(autouse=True)
def _promoted_execution_bindings(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_ASE_STATE_ROOT", str(tmp_path / "state"))
    for family in ("forex", "crypto"):
        promote_family(
            family,
            horizon="intraday",
            version="v1",
            artifact_hash="test-artifact",
            operator="test",
        )


# ── decision floor ────────────────────────────────────────────────────────────


def test_negative_thr_family_cannot_reach_trade():
    # crypto/swing shipped with thr_family = -0.387; a barely-positive
    # expected R must not TRADE under a negative threshold.
    status = apply_decision_rule(
        p_cal=0.60, expected_net_r=0.05, thr_family=-0.387
    )
    assert status == "WATCH"


def test_trade_requires_floor_even_when_thr_below_it():
    assert (
        apply_decision_rule(p_cal=0.60, expected_net_r=MIN_TRADE_EXPECTED_R, thr_family=-1.0)
        == "TRADE"
    )
    assert (
        apply_decision_rule(p_cal=0.60, expected_net_r=0.09, thr_family=-1.0)
        == "WATCH"
    )


def test_higher_family_threshold_still_respected():
    assert apply_decision_rule(p_cal=0.70, expected_net_r=0.2, thr_family=0.3) == "WATCH"
    assert apply_decision_rule(p_cal=0.70, expected_net_r=0.35, thr_family=0.3) == "TRADE"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_ase_scores_fail_closed(bad):
    assert apply_decision_rule(p_cal=0.70, expected_net_r=bad, thr_family=0.1) == "FLAT"
    assert apply_decision_rule(p_cal=bad, expected_net_r=0.3, thr_family=0.1) == "FLAT"
    assert apply_decision_rule(p_cal=0.70, expected_net_r=0.3, thr_family=bad) == "FLAT"
    assert signal_strength(bad, 0.70) == 0
    assert signal_strength(0.3, bad) == 0
    assert legacy_expected_r_strength(bad) == 0


# ── stale-bar fail-close ──────────────────────────────────────────────────────


def _series(last_bar_ms: int, n: int = 10, tf_ms: int = _HOUR) -> BarSeries:
    times = np.array([last_bar_ms - (n - 1 - i) * tf_ms for i in range(n)], dtype="i8")
    close = np.log(np.full(n, 100.0))
    return BarSeries(
        value_time=times,
        available_time=times,
        close_log=close,
        high_log=close + 0.001,
        low_log=close - 0.001,
        sigma_bar=np.full(n, 0.01),
        sigma_long=np.full(n, 0.01),
    )


def test_live_bars_stale_bounds():
    now = 1_800_000_000_000
    assert not live_bars_stale(_series(now - 2 * _HOUR), "intraday", now)
    assert not live_bars_stale(_series(now - 90 * _HOUR), "intraday", now)  # weekend
    assert live_bars_stale(_series(now - 10 * _DAY), "intraday", now)
    assert not live_bars_stale(_series(now - 3 * _DAY, tf_ms=_DAY), "swing", now)
    assert live_bars_stale(_series(now - 45 * _DAY, tf_ms=_DAY), "swing", now)


def test_resolve_price_source_prefers_freshest():
    from athena_ase.data.ptis import PTISStore, build_row

    store = PTISStore(tempfile.mkdtemp())
    now = int(time.time() * 1000)
    stale_t = now - 60 * _DAY
    fresh_t = now - 1 * _HOUR
    store.append_rows(
        "BINANCE:BTCUSDT:H1:close",
        [build_row("BINANCE:BTCUSDT:H1:close", stale_t, stale_t, 50_000.0)],
        source="BINANCE",
    )
    store.append_rows(
        "BYBIT:BTCUSDT:H1:close",
        [build_row("BYBIT:BTCUSDT:H1:close", fresh_t, fresh_t, 62_000.0)],
        source="BYBIT",
    )
    assert resolve_price_source(store, "BTCUSDT", "H1", end_ms=now) == "BYBIT"
    # Historical resolution (before the fresh source existed) still finds BINANCE.
    assert resolve_price_source(store, "BTCUSDT", "H1", end_ms=stale_t + 1) == "BINANCE"


# ── brackets entry zone ───────────────────────────────────────────────────────


def test_entry_zone_half_width_uses_sqrt_h():
    entry = 100.0
    sigma = 0.01
    b = compute_brackets(
        direction=1,
        entry_reference=entry,
        sigma_bar=sigma,
        horizon="intraday",
        mae_q={"q50": 0.4, "q90": 0.8},
        mfe_q={"q50": 0.9, "q75": 1.2},
    )
    expected_half = 0.25 * sigma * math.sqrt(16) * entry
    assert abs((b.entry_zone[1] - b.entry_zone[0]) - expected_half) < 1e-9


# ── execution bridge hardening ────────────────────────────────────────────────


@dataclass
class _Approval:
    approved: bool
    volume: float = 0.1
    reason: str = "OK"


def _trade_signal(instrument: str = "EURUSD", family: str = "forex") -> ASESignal:
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily=family,
        modelVersion="v1",
        horizon="intraday",
        decisionStatus="TRADE",
        direction="LONG",
        expectedNetR=0.2,
        expectedNetBps=2000.0,
        probabilityPositive=0.62,
        decisionMargin=0.12,
        signalStrength=40,
        returnQ={},
        maeQ={},
        mfeQ={},
        holdQ={},
        entryReference=1.1,
        entryZone=(1.095, 1.1),
        sl=1.08,
        tp1=1.15,
        tp2=1.2,
        maxHoldBars=16,
        primarySignals=[],
        predictionDiagnostics={},
        dataQuality={"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth={"artifactHash": "test-artifact", "gateResult": {"ok": True}},
        instrument=instrument,
        decisionTimeMs=1_700_000_000_000,
    )


def test_bybit_execution_requires_testnet_url():
    deps = ASEExecutionDeps(
        get_bybit_base_url=lambda: "",  # unset URL passes the demo gate...
        route_executor=lambda _s: "bybit",
        risk_check=lambda **_k: _Approval(True),
    )
    result = execute_trade_signal(
        _trade_signal("BTCUSDT", "crypto"), deps=deps, write_journal=False
    )
    # ...but must be positively proven testnet before an order is sent.
    assert result["executed"] is False
    assert result["reason"] == "venue:bybit_url_not_testnet"


def test_result_order_id_reads_mt5_ticket_and_legs():
    assert _result_order_id({"success": True, "ticket": 12345}) == "12345"
    assert _result_order_id({"legs": [{"ticket": 777}]}) == "777"
    assert _result_order_id({"orderId": "abc"}) == "abc"
    assert _result_order_id({"success": True}) == ""


def test_time_stop_matches_untagged_broker_position_via_journal(monkeypatch):
    now_ms = 1_700_000_000_000
    decision_ms = now_ms - 12 * _DAY  # swing held 12d > 10-bar vertical
    journal = pd.DataFrame(
        [
            {
                "instrument": "COCOA",
                "direction": "LONG",
                "decisionTimeMs": decision_ms,
                "maxHoldBars": 10,
                "horizon": "swing",
                "executionStatus": "filled",
                "orderId": "",
            }
        ]
    )
    import athena_ase.execution.journal as journal_mod

    monkeypatch.setattr(journal_mod, "load_trade_journal", lambda *a, **k: journal)

    closed: list[dict] = []
    deps = ASEExecutionDeps(close_position=lambda pos: closed.append(pos) or {"success": True})
    positions = [
        {  # untagged broker position, as returned by mt5_get_positions
            "ticket": 555,
            "pair": "Cocoa",
            "direction": "LONG",
            "open_time": (decision_ms + 60_000) // 1000,
            "volume": 1.0,
        },
        {  # young position from another engine window — must be left alone
            "ticket": 556,
            "pair": "Cocoa",
            "direction": "LONG",
            "open_time": (now_ms - _DAY) // 1000,
            "volume": 1.0,
        },
    ]
    result = enforce_time_stops(positions, deps=deps, now_ms=now_ms)
    assert len(result) == 1
    assert closed[0]["ticket"] == 555


def test_time_stop_ignores_positions_with_no_journal_match(monkeypatch):
    import athena_ase.execution.journal as journal_mod

    monkeypatch.setattr(
        journal_mod, "load_trade_journal", lambda *a, **k: pd.DataFrame()
    )
    deps = ASEExecutionDeps(close_position=lambda pos: {"success": True})
    positions = [
        {"ticket": 1, "pair": "EUR/USD", "direction": "LONG", "open_time": 1, "volume": 0.1}
    ]
    assert enforce_time_stops(positions, deps=deps, now_ms=1_700_000_000_000) == []


# ── reconciliation ────────────────────────────────────────────────────────────


def _write_journal(path: Path, rows: list[dict]) -> None:
    base = {
        "executionStatus": "filled",
        "reconciledAt": None,
        "realizedNetR": None,
        "realizedWin": None,
    }
    pd.DataFrame([{**base, **r} for r in rows]).to_parquet(path, index=False)


def test_reconcile_filled_from_ptis_target_and_stop():
    from athena_ase.data.ptis import PTISStore, build_row
    from athena_ase.execution.reconcile import reconcile_filled_from_ptis

    tmp = Path(tempfile.mkdtemp())
    store = PTISStore(tmp / "ptis")
    decision = 1_700_000_000_000
    now = decision + 30 * _HOUR

    # EURUSD long: TP 1.15 touched on bar 3. GBPUSD long: SL 1.08 hit on bar 2.
    def bars(symbol: str, highs: list[float], lows: list[float]) -> None:
        for field, vals in (("close", [(h + l) / 2 for h, l in zip(highs, lows)]), ("high", highs), ("low", lows)):
            sid = f"MT5:{symbol}:H1:{field}"
            rows = [
                build_row(sid, decision + (i + 1) * _HOUR, decision + (i + 1) * _HOUR, v)
                for i, v in enumerate(vals)
            ]
            store.append_rows(sid, rows, source="MT5")

    bars("EURUSD", [1.11, 1.12, 1.16, 1.12], [1.09, 1.10, 1.11, 1.10])
    bars("GBPUSD", [1.11, 1.10, 1.11, 1.11], [1.09, 1.07, 1.09, 1.09])

    journal_path = tmp / "journal.parquet"
    common = {
        "horizon": "intraday",
        "decisionTimeMs": decision,
        "entryReference": 1.10,
        "sl": 1.08,
        "tp1": 1.15,
        "maxHoldBars": 16,
        "direction": "LONG",
    }
    _write_journal(
        journal_path,
        [
            {**common, "instrument": "EURUSD"},
            {**common, "instrument": "GBPUSD"},
        ],
    )

    updated = reconcile_filled_from_ptis(path=journal_path, store=store, now_ms=now)
    assert updated == 2
    df = pd.read_parquet(journal_path)
    eur = df[df.instrument == "EURUSD"].iloc[0]
    gbp = df[df.instrument == "GBPUSD"].iloc[0]
    assert eur["realizedWin"] and abs(eur["realizedNetR"] - 2.5) < 1e-9  # 0.05/0.02
    assert not gbp["realizedWin"] and abs(gbp["realizedNetR"] + 1.0) < 1e-9
    assert eur["reconciledAt"] is not None


def test_reconcile_skips_still_open_trades():
    from athena_ase.data.ptis import PTISStore, build_row
    from athena_ase.execution.reconcile import reconcile_filled_from_ptis

    tmp = Path(tempfile.mkdtemp())
    store = PTISStore(tmp / "ptis")
    decision = 1_700_000_000_000
    sid = "MT5:EURUSD:H1:close"
    for field in ("close", "high", "low"):
        s = f"MT5:EURUSD:H1:{field}"
        store.append_rows(
            s,
            [build_row(s, decision + _HOUR, decision + _HOUR, 1.10)],
            source="MT5",
        )
    journal_path = tmp / "journal.parquet"
    _write_journal(
        journal_path,
        [
            {
                "instrument": "EURUSD",
                "horizon": "intraday",
                "decisionTimeMs": decision,
                "entryReference": 1.10,
                "sl": 1.08,
                "tp1": 1.15,
                "maxHoldBars": 16,
                "direction": "LONG",
            }
        ],
    )
    updated = reconcile_filled_from_ptis(
        path=journal_path, store=store, now_ms=decision + 2 * _HOUR
    )
    assert updated == 0  # trade still live — nothing to reconcile yet


# ── drift monitor (F2) ────────────────────────────────────────────────────────


def test_evaluate_monitor_detects_shifted_distribution():
    import numpy as np

    from athena_ase.inference.monitor import evaluate_monitor

    ref = {"ret_z_1": np.linspace(-2, 2, 50)}
    live_rows = [{"ret_z_1": float(x)} for x in np.linspace(1.0, 3.0, 8)]
    result = evaluate_monitor(reference_features=ref, live_feature_rows=live_rows)
    assert result.drift_score > 0.0
    assert result.watch_max is True


def test_evaluate_monitor_one_scan_is_one_time_sample():
    """Five symbols from a single scan tick must not satisfy MIN_SAMPLES."""
    import numpy as np

    from athena_ase.inference.monitor import DECISION_TIME_KEY, evaluate_monitor

    ref = {"ret_z_1": np.linspace(-2, 2, 50)}
    same_tick = [
        {"ret_z_1": float(x), DECISION_TIME_KEY: 1_700_000_000_000.0}
        for x in np.linspace(1.0, 3.0, 5)
    ]
    result = evaluate_monitor(
        reference_features=ref, live_feature_rows=same_tick, min_samples=5
    )
    assert result.psi == {}
    assert result.watch_max is False
    assert "ret_z_1" in result.insufficient_samples

    distinct_ticks = [
        {"ret_z_1": float(x), DECISION_TIME_KEY: 1_700_000_000_000.0 + i * 60_000}
        for i, x in enumerate(np.linspace(1.0, 3.0, 5))
    ]
    result = evaluate_monitor(
        reference_features=ref, live_feature_rows=distinct_ticks, min_samples=5
    )
    assert result.psi["ret_z_1"] > 0.0
    assert result.watch_max is True


def _predict_one_with_probability(monkeypatch, probability: float):
    import numpy as np
    from unittest.mock import MagicMock

    from athena_ase.artifacts.loader import ArtifactBundle
    from athena_ase.gates.demo_only import GateResult
    from athena_ase.inference.predict import predict_one
    from athena_ase.registry.artifacts import ArtifactManifest
    from athena_ase.signals.arbitrate import Candidate
    import athena_ase.inference.predict as pred_mod

    manifest = ArtifactManifest(
        family="forex",
        horizon="intraday",
        version="v-test",
        feature_schema_hash="x",
        feature_schema=["ret_z_1"],
        thr_family=0.1,
        eval_summary={"expectancy": 0.2},
    )
    bundle = ArtifactBundle(
        family="forex",
        horizon="intraday",
        version="v-test",
        root=MagicMock(),
        manifest=manifest,
        model_core=MagicMock(),
        model_enriched=None,
        calibrator=None,
        calibrator_enriched=None,
        quantile_heads={"MAE_R": {}, "MFE_R": {}, "net_R": {}, "hold_bars": {}},
        quantile_heads_enriched=None,
        thr_family=0.1,
        feature_names=("ret_z_1",),
        monitor_reference={"ret_z_1": np.linspace(-1, 1, 20)},
    )
    bundle.model_core.predict_proba.return_value = np.array(
        [[1.0 - probability, probability]]
    )

    inst = MagicMock()
    inst.family = "forex"
    inst.symbol = "EURUSD"
    inst.subclass = "major"
    inst.benchmark = "USDX"

    cand = Candidate(
        instrument="EURUSD",
        family="forex",
        horizon="intraday",
        decision_time_ms=1_700_000_000_000,
        bar_index=10,
        direction=1,
        sigma_bar=0.01,
        entry_log=0.0,
    )

    brackets = MagicMock(
        rr_ok=True,
        entry_reference=1.0,
        entry_zone=(1.0, 1.0),
        sl=0.9,
        tp1=1.1,
        tp2=1.2,
        max_hold_bars=16,
    )

    monkeypatch.setattr(pred_mod, "verify_manifest_hashes", lambda *_a, **_k: [])
    monkeypatch.setattr(pred_mod, "build_features_for_candidate", lambda *_a, **_k: {"ret_z_1": 0.5})
    monkeypatch.setattr(pred_mod, "compute_brackets", lambda **_k: brackets)
    monkeypatch.setattr(pred_mod, "assert_demo", lambda **_k: GateResult(ok=True, reason="ok", checks={}))
    monkeypatch.setattr(pred_mod, "is_watch_max", lambda *_a, **_k: False)
    monkeypatch.setattr(pred_mod, "set_watch_max", lambda *_a, **_k: None)

    live_buf: list[dict[str, float]] = []
    result = predict_one(
        cand,
        MagicMock(),
        inst,
        bundle=bundle,
        live_feature_rows=live_buf,
    )
    return result, live_buf


def test_predict_one_appends_live_feature_buffer(monkeypatch):
    _result, live_buf = _predict_one_with_probability(monkeypatch, 0.6)
    assert len(live_buf) == 1
    assert "ret_z_1" in live_buf[0]


@pytest.mark.parametrize(
    "bad_probability",
    [float("nan"), float("inf"), float("-inf"), -0.1, 1.1],
)
def test_predict_one_rejects_invalid_model_probability(monkeypatch, bad_probability):
    result, _live_buf = _predict_one_with_probability(monkeypatch, bad_probability)

    assert result.decisionStatus == "ERROR"
    assert result.modelHealth["errorReason"] == "model_probability_invalid"


def test_predict_one_rejects_nonfinite_modeled_expectancy(monkeypatch):
    import athena_ase.inference.predict as pred_mod

    monkeypatch.setattr(pred_mod, "_expected_net_r", lambda *_a, **_k: float("nan"))
    result, _live_buf = _predict_one_with_probability(monkeypatch, 0.6)

    assert result.decisionStatus == "ERROR"
    assert result.modelHealth["errorReason"] == "model_expectancy_nonfinite"


# ── demo execution deps gate (F5) ─────────────────────────────────────────────


def test_default_execution_deps_rejects_live_mode(monkeypatch):
    from athena_ase.execution.bridge import default_execution_deps

    import config

    monkeypatch.setitem(config.CONFIG, "EXECUTOR_MODE", "live")
    with pytest.raises(RuntimeError, match="blocked"):
        default_execution_deps()
