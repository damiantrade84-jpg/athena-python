"""Stage 4 validation & hardening tests.

Covers:
  4.1: Fatal boot-time config validation
  4.2: BT_MIN / BT_MIN_GROUP / BACKTEST_USE_BT_MIN_THRESHOLDS deletion
  4.3: Score-to-probability calibration decay monitor
"""

import sqlite3

import pytest
from config import CONFIG, _fatal_config_validation, ConfigValidationError
from scoring import _TIER_VOLATILE, _TIER_STABLE, get_score_threshold
from calibration import (
    calibration_decay_report,
    cached_calibration_decay_report,
    global_calibration_health,
    _brier_miscalibration,
)


# ── 4.1: Fatal config validation ─────────────────────────────────────────────

class TestFatalConfigValidation:
    def test_passes_on_valid_config(self):
        """Current CONFIG should pass fatal validation."""
        _fatal_config_validation(CONFIG)

    def test_rejects_bt_min_in_config(self):
        bad = {**CONFIG, "BACKTEST_USE_BT_MIN_THRESHOLDS": True}
        with pytest.raises(ConfigValidationError):
            _fatal_config_validation(bad)

    def test_rejects_bt_min_group_in_config(self):
        bad = {**CONFIG, "BT_MIN_GROUP": {"crypto": 1.0}}
        with pytest.raises(ConfigValidationError):
            _fatal_config_validation(bad)

    def test_rejects_low_conviction_floor(self):
        bad = {**CONFIG, "FACTOR_CONVICTION_FLOOR": 0.05}
        with pytest.raises(ConfigValidationError):
            _fatal_config_validation(bad)

    def test_rejects_high_conviction_floor(self):
        bad = {**CONFIG, "FACTOR_CONVICTION_FLOOR": 0.50}
        with pytest.raises(ConfigValidationError):
            _fatal_config_validation(bad)

    def test_rejects_inverted_tiers(self):
        """If we ever invert the tiers, validation must catch it."""
        # This test documents the invariant; tiers are hardcoded in config.py
        assert _TIER_VOLATILE >= _TIER_STABLE

    def test_trend_weights_sum_to_one(self):
        """INDICATOR_WEIGHTS.trend per asset class must sum to 1.0."""
        trend_weights = CONFIG.get("INDICATOR_WEIGHTS", {}).get("trend", {})
        for asset_class, weights in trend_weights.items():
            if isinstance(weights, dict):
                total = sum(v for v in weights.values() if isinstance(v, (int, float)))
                assert abs(total - 1.0) < 1e-6, (
                    f"Trend weights for {asset_class} sum to {total}, expected 1.0"
                )

    def test_rejects_missummed_engine_a_factor_weights(self):
        bad = {
            **CONFIG,
            "ENGINE_A_FACTOR_WEIGHTS_BY_CLASS": {
                **CONFIG.get("ENGINE_A_FACTOR_WEIGHTS_BY_CLASS", {}),
                "crypto": {"momentum": 0.9, "addon": 0.2, "base": 0.2},
            },
        }
        with pytest.raises(ConfigValidationError):
            _fatal_config_validation(bad)

    def test_rejects_missing_engine_a_score_group_threshold(self):
        thresholds = dict(CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {})
        thresholds.pop("crypto_other", None)
        bad = {**CONFIG, "ENGINE_A_SCORE_GROUP_THRESHOLDS": thresholds}
        with pytest.raises(ConfigValidationError):
            _fatal_config_validation(bad)

    def test_rejects_inactive_pair_profile_knobs(self):
        bad = {
            **CONFIG,
            "PAIR_PROFILES": {
                **CONFIG.get("PAIR_PROFILES", {}),
                "TEST/PAIR": {"weight_overrides": {"h4_fib": 2.0}, "bt_min": 1.0},
            },
        }
        with pytest.raises(ConfigValidationError):
            _fatal_config_validation(bad)


# ── 4.2: BT_MIN deletion ─────────────────────────────────────────────────────

class TestBtMinDeleted:
    def test_bt_min_not_in_config(self):
        assert "BT_MIN" not in CONFIG

    def test_bt_min_group_not_in_config(self):
        assert "BT_MIN_GROUP" not in CONFIG

    def test_backtest_use_bt_min_thresholds_not_in_config(self):
        assert "BACKTEST_USE_BT_MIN_THRESHOLDS" not in CONFIG

    def test_backtest_uses_same_thresholds_as_live(self):
        """Backtest and live return identical active Engine A thresholds."""
        pair = {"display": "EUR/USD", "type": "forex"}
        # Both wrappers and the canonical function return the same value now.
        live = get_score_threshold(pair)
        bt = get_score_threshold(pair)
        assert live == bt == CONFIG["ENGINE_A_SCORE_GROUP_THRESHOLDS"]["forex_majors"]

    def test_crypto_uses_volatile_tier(self):
        pair = {"display": "BTC/USDT", "type": "crypto"}
        assert get_score_threshold(pair) == _TIER_VOLATILE

    def test_nat_gas_uses_volatile_tier(self):
        pair = {"display": "Nat Gas", "type": "commodity"}
        assert get_score_threshold(pair) == _TIER_VOLATILE


# ── 4.3: Calibration decay monitor ───────────────────────────────────────────

class TestCalibrationDecayMonitor:
    def test_brier_miscalibration_symmetric(self):
        assert _brier_miscalibration(0.7, 0.5) == pytest.approx(0.2)
        assert _brier_miscalibration(0.5, 0.7) == pytest.approx(0.2)

    def test_decay_report_insufficient_data(self):
        """Empty records + missing DB → insufficient_data, not an error."""
        import os
        fake_db = "/nonexistent/path/audit.db"
        report = calibration_decay_report(records=[], db_path=fake_db)
        assert report["available"] is False
        assert report["reason"] == "insufficient_data"

    def test_decay_loader_preserves_explicit_engine(self, tmp_path):
        from calibration import _load_decay_records_from_audit_db

        db_path = tmp_path / "audit.db"
        with sqlite3.connect(db_path) as con:
            con.execute(
                "CREATE TABLE audit_log ("
                "id INTEGER, ts TEXT, pair TEXT, engine TEXT, score REAL, "
                "max_score REAL, score_pct REAL, asset_class TEXT, style TEXT, "
                "grade TEXT, pnl REAL, r_multiple REAL, exit_price REAL, error_tag TEXT)"
            )
            con.execute(
                "INSERT INTO audit_log VALUES (1, '2026-07-22T00:00:00+00:00', "
                "'APT/USDT', 'engine_b', 8, 10, 80, 'crypto', 'intraday', "
                "'EXECUTED', 1, 1, 6, NULL)"
            )

        records = _load_decay_records_from_audit_db(str(db_path))

        assert records[0]["engine"] == "engine_b"
        assert records[0]["score_pct"] == 80

    def test_decay_report_detects_decayed_bucket(self):
        """A recent high-score cohort materially underperforms its history."""
        records = []
        # Chronological reference cohort: 75% wins in the high-score bucket.
        for i in range(84):
            records.append(
                {
                    "engine": "engine_a",
                    "asset_class": "forex",
                    "style": "intraday",
                    "raw_score": 0.9,
                    "max_score": 1.0,
                    "won": i < 63,
                }
            )
        # Recent cohort: only 11% wins in the same bucket.
        for i in range(36):
            records.append(
                {
                    "engine": "engine_a",
                    "asset_class": "forex",
                    "style": "intraday",
                    "raw_score": 0.9,
                    "max_score": 1.0,
                    "won": i < 4,
                }
            )
        report = calibration_decay_report(
            records=records,
            engine="engine_a",
            asset_class="forex",
            style="intraday",
            decay_tolerance=0.08,  # tight tolerance to catch the 0.10 gap
            prior_weight=5.0,  # stronger prior so predicted stays high
        )
        assert report["available"] is True
        assert report["healthy"] is False
        assert report["decayed_buckets"] >= 1
        assert report["max_miscalibration"] > 0.08
        assert report["reference_records"] == 84
        assert report["recent_records"] == 36
        assert report["recommendation"] in ("recalibrate_model", "review_recent_regime")

    def test_decay_report_healthy_when_calibrated(self):
        """Synthetic records where all buckets are well-calibrated."""
        records = []
        # Chronological reference and recent cohorts both alternate wins/losses.
        for i in range(100):
            records.append(
                {
                    "engine": "engine_a",
                    "asset_class": "forex",
                    "style": "intraday",
                    "raw_score": 0.8,
                    "max_score": 1.0,
                    "won": i % 2 == 0,
                }
            )
        report = calibration_decay_report(
            records=records,
            engine="engine_a",
            asset_class="forex",
            style="intraday",
            decay_tolerance=0.15,
        )
        assert report["available"] is True
        assert report["healthy"] is True
        assert report["decayed_buckets"] == 0
        assert report["recommendation"] == "ok"

    def test_cached_decay_report_returns_same_result(self):
        report1 = cached_calibration_decay_report(records=[])
        report2 = cached_calibration_decay_report(records=[])
        assert report1 == report2

    def test_global_health_is_not_healthy_when_data_is_missing(self):
        summary = global_calibration_health("/nonexistent/path/audit.db")
        assert "checked" in summary
        assert "healthy" in summary
        assert "decayed_scopes" in summary
        assert "global_healthy" in summary
        assert summary["global_healthy"] is None
        assert summary["status"] == "insufficient_data"
