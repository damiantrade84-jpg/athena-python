"""Engine A V2 routing audit tests.

Verifies that all Engine A runtime paths use V2 (calc_confluence/compute_factor_scores),
that forex does NOT route to legacy forex_scoring, and that zero-score results carry
explicit diagnostics.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
import scoring
import factor_scoring


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_snap(**overrides):
    base = {
        "close": 1.10, "open": 1.09, "high": 1.11, "low": 1.08,
        "ema21": 1.095, "ema50": 1.090, "ema200": 1.080,
        "rsi": 55, "macdHist": 0.001, "macdHistPrev": 0.0005,
        "adx": 30, "plusDI": 25, "minusDI": 15,
        "atr": 0.005, "vol": 1000, "bbWidth_pct": 50,
    }
    base.update(overrides)
    return base


def _make_pair(display="GBP/USD", ptype="forex"):
    return {"display": display, "symbol": display, "type": ptype, "enabled": True}


def _make_indicator_dict(snap):
    return {"snap": snap}


def _make_candles(snap, n=100):
    candles = []
    for i in range(n):
        candles.append({
            "time": f"2026-01-{(i % 28) + 1:02d}T12:00:00Z",
            "open": snap["open"], "high": snap["high"],
            "low": snap["low"], "close": snap["close"],
            "vol": snap.get("vol", 1000),
        })
    return candles


# ── Test 1: All asset types route to calc_confluence / compute_factor_scores ──

class TestAllAssetV2Routing:
    """Representative symbols from each asset class must route through V2."""

    @pytest.fixture(autouse=True)
    def _block_legacy(self, monkeypatch):
        """Monkeypatch compute_forex_score to raise if called."""
        import forex_scoring
        def _explode(*a, **kw):
            raise AssertionError("Legacy forex_scoring.compute_forex_score was called!")
        monkeypatch.setattr(forex_scoring, "compute_forex_score", _explode)

    @pytest.mark.parametrize("display,ptype", [
        ("GBP/USD", "forex"),
        ("EUR/USD", "forex"),
        ("USD/JPY", "forex"),
        ("BTC/USDT", "crypto"),
        ("ETH/USDT", "crypto"),
        ("XAU/USD", "commodity"),
        ("AAPL", "stock"),
    ])
    def test_v2_route_does_not_call_legacy(self, display, ptype):
        pair = _make_pair(display, ptype)
        snap = _make_snap()
        d1 = _make_indicator_dict(snap)
        h4 = _make_indicator_dict(snap)
        h1 = _make_indicator_dict(snap)
        candles = _make_candles(snap)
        result = scoring.calc_confluence(
            d1, h4, h1, vr=1.0, stoch={}, pair=pair, btc_bias="neutral",
            d1_candles=candles, h4_candles=candles, h1_candles=candles,
        )
        assert "score" in result
        assert "direction" in result
        assert result.get("maxScoreOverride") == 3.0


# ── Test 2: Forex does NOT use legacy forex_scoring ──────────────────────────

class TestForexNotLegacy:
    def test_forex_calls_compute_factor_scores(self, monkeypatch):
        called = {"v2": False}
        original = factor_scoring.compute_factor_scores

        def spy(*args, **kwargs):
            called["v2"] = True
            return original(*args, **kwargs)

        monkeypatch.setattr(factor_scoring, "compute_factor_scores", spy)
        pair = _make_pair("GBP/USD", "forex")
        snap = _make_snap()
        d1 = _make_indicator_dict(snap)
        h4 = _make_indicator_dict(snap)
        h1 = _make_indicator_dict(snap)
        candles = _make_candles(snap)
        scoring.calc_confluence(
            d1, h4, h1, vr=1.0, stoch={}, pair=pair, btc_bias="neutral",
            d1_candles=candles, h4_candles=candles, h1_candles=candles,
        )
        assert called["v2"], "compute_factor_scores was not called for forex"


# ── Test 3: Backtest runner source code does not import compute_forex_score ──

class TestBacktestRunnerLabel:
    def test_no_compute_forex_score_import(self):
        bt_path = os.path.join(os.path.dirname(__file__), "..", "backtest_runner.py")
        src = open(bt_path).read()
        assert "compute_forex_score" not in src, (
            "backtest_runner.py still references compute_forex_score"
        )

    def test_db_label_is_factor_scoring(self):
        bt_path = os.path.join(os.path.dirname(__file__), "..", "backtest_runner.py")
        src = open(bt_path).read()
        assert '"factor_scoring"' in src, (
            "backtest_runner.py DB engine label should be factor_scoring"
        )


# ── Test 4: V2 payload preservation ─────────────────────────────────────────

class TestV2PayloadIdentity:
    def test_payload_has_v2_identity_fields(self):
        pair = _make_pair("EUR/USD", "forex")
        snap = _make_snap()
        d1 = _make_indicator_dict(snap)
        h4 = _make_indicator_dict(snap)
        h1 = _make_indicator_dict(snap)
        candles = _make_candles(snap)
        result = scoring.calc_confluence(
            d1, h4, h1, vr=1.0, stoch={}, pair=pair, btc_bias="neutral",
            d1_candles=candles, h4_candles=candles, h1_candles=candles,
        )
        assert result["maxScoreOverride"] == 3.0
        fd = result.get("factorDiagnostics", {})
        assert fd.get("engineVersion") == "A_V2"
        assert fd.get("scorerSelected") == "factor_scoring.compute_factor_scores"


# ── Test 5: Zero-score diagnostics ──────────────────────────────────────────

class TestZeroScoreDiagnostics:
    def test_zero_score_includes_abort_reason(self):
        pair = _make_pair("GBP/USD", "forex")
        snap_conflicting = _make_snap(
            ema21=1.10, ema50=1.12, ema200=1.11,
            adx=30, rsi=50, macdHist=0.0,
        )
        d1_snap = dict(snap_conflicting)
        d1_snap["ema21"] = 1.08
        d1_snap["ema200"] = 1.10
        h4_snap = dict(snap_conflicting)
        h4_snap["ema21"] = 1.12
        h4_snap["ema50"] = 1.09
        h1_snap = dict(snap_conflicting)
        h1_snap["ema21"] = 1.11
        h1_snap["ema50"] = 1.10

        d1 = _make_indicator_dict(d1_snap)
        h4 = _make_indicator_dict(h4_snap)
        h1 = _make_indicator_dict(h1_snap)
        candles = _make_candles(snap_conflicting)
        result = scoring.calc_confluence(
            d1, h4, h1, vr=1.0, stoch={}, pair=pair, btc_bias="neutral",
            d1_candles=candles, h4_candles=candles, h1_candles=candles,
        )
        if result["score"] == 0.0 and result["direction"] == "neutral":
            fd = result.get("factorDiagnostics", {})
            abort_reason = fd.get("abortReason")
            assert abort_reason is not None, (
                "Zero-score neutral result must include abortReason in factorDiagnostics"
            )
            assert abort_reason in (
                "indeterminate_trend", "min_directional_failed",
                "adx_hard_abort", "weighted_tf_tie", "atr_invalid_abort",
                "final_score_invalid",
            )


# ── Test 6: No silent fallback ──────────────────────────────────────────────

class TestNoSilentFallback:
    def test_v2_exception_does_not_call_legacy(self, monkeypatch):
        """If V2 raises, the route must NOT silently call forex_scoring."""
        import forex_scoring
        legacy_called = {"val": False}

        def _legacy_spy(*a, **kw):
            legacy_called["val"] = True
            return forex_scoring.ForexScoreResult()

        monkeypatch.setattr(forex_scoring, "compute_forex_score", _legacy_spy)

        def _v2_explode(*a, **kw):
            raise RuntimeError("Simulated V2 failure")

        monkeypatch.setattr(factor_scoring, "compute_factor_scores", _v2_explode)

        pair = _make_pair("EUR/USD", "forex")
        snap = _make_snap()
        d1 = _make_indicator_dict(snap)
        h4 = _make_indicator_dict(snap)
        h1 = _make_indicator_dict(snap)
        candles = _make_candles(snap)
        with pytest.raises(RuntimeError):
            scoring.calc_confluence(
                d1, h4, h1, vr=1.0, stoch={}, pair=pair, btc_bias="neutral",
                d1_candles=candles, h4_candles=candles, h1_candles=candles,
            )
        assert not legacy_called["val"], (
            "Legacy forex_scoring was called as silent fallback after V2 failure"
        )


# ── Test 7: Engine B does not influence Engine A V2 score ────────────────────

class TestEngineBSeparation:
    def test_engine_a_score_independent_of_engine_b(self, monkeypatch):
        pair = _make_pair("BTC/USDT", "crypto")
        snap = _make_snap(close=60000, open=59500, high=61000, low=59000,
                          ema21=59800, ema50=59500, ema200=58000, atr=500)
        d1 = _make_indicator_dict(snap)
        h4 = _make_indicator_dict(snap)
        h1 = _make_indicator_dict(snap)
        candles = _make_candles(snap)

        result1 = scoring.calc_confluence(
            d1, h4, h1, vr=1.0, stoch={}, pair=pair, btc_bias="neutral",
            d1_candles=candles, h4_candles=candles, h1_candles=candles,
        )

        result2 = scoring.calc_confluence(
            d1, h4, h1, vr=1.0, stoch={}, pair=pair, btc_bias="neutral",
            d1_candles=candles, h4_candles=candles, h1_candles=candles,
            structure_result={"direction": "SHORT", "bos_confirmed": True,
                              "structural_verdict": "CLEAR", "score": 5.0},
        )
        assert result1["score"] == result2["score"], (
            "Engine A score changed when structure_result (Engine B) was provided — "
            "Engine B should not influence Engine A V2 score"
        )
        assert result1["direction"] == result2["direction"]


# ── Test 8: athena.py and scanner.py do not import forex_scoring ─────────────

class TestLivePathNoLegacy:
    def test_athena_no_compute_forex_score(self):
        athena_path = os.path.join(os.path.dirname(__file__), "..", "athena.py")
        src = open(athena_path).read()
        assert "compute_forex_score" not in src

    def test_scanner_no_compute_forex_score(self):
        scanner_path = os.path.join(os.path.dirname(__file__), "..", "scanner.py")
        src = open(scanner_path).read()
        assert "compute_forex_score" not in src
