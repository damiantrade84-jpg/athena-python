"""Targeted tests for the diagnostic-only lower-TF shadow (Engine B + ASE).

Proves the four acceptance points:
  1. Disabled by default → the gate the scanner/ASE wiring checks is False, so
     no diagnostic key is attached (output structure preserved).
  2. Enabled → ``compute_lower_tf_shadow`` returns the diagnostic fields.
  3. A missing M15/M5 is reported as missing and NEVER triggers an H1 fetch
     (no silent fallback).
  4. The diagnostic payload carries no scoring/gating/eligibility keys — it
     cannot influence a trade decision.

Runs standalone (``python tests/test_lower_tf_shadow.py``) or under pytest.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import lower_tf_shadow as lts


def _make_candles(tf: str, n: int = 60, *, forming: bool | None = None, trend: str = "up"):
    """Synthetic fresh candle list (most recent last). Confirmed unless forming."""
    tf_sec = lts._TF_SECONDS[tf]
    now = int(time.time())
    candles = []
    for i in range(n):
        # Oldest first; last bar opens one tf_sec before "now".
        open_t = now - (n - i) * tf_sec
        base = 100.0 + (i * 0.5 if trend == "up" else -i * 0.5)
        candles.append(
            {
                "time": open_t,
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + (0.3 if trend == "up" else -0.3),
                "vol": 1000.0,
            }
        )
    if forming is not None:
        candles[-1]["confirmed"] = not forming
    return candles


class _RecordingFetcher:
    """Records every (tf) requested; returns configured candles or None."""

    def __init__(self, available: dict[str, list]):
        self.available = available
        self.requested_tfs: list[str] = []

    def __call__(self, pair, tf, limit):
        self.requested_tfs.append(tf)
        return self.available.get(tf)


def _enable(monkeypatch, apply_to=("engine_b", "ase")):
    monkeypatch.setitem(
        config.CONFIG,
        "LOWER_TF_SHADOW",
        {
            "enabled": True,
            "tfs": ["M30", "M15", "M5"],
            "apply_to": list(apply_to),
            "diagnostic_only": True,
        },
    )


def _disable(monkeypatch):
    monkeypatch.setitem(
        config.CONFIG,
        "LOWER_TF_SHADOW",
        {"enabled": False, "tfs": ["M30", "M15", "M5"], "apply_to": ["engine_b", "ase"]},
    )


# --------------------------------------------------------------------------- #
# 1. Disabled → gate is False (wiring attaches nothing → output identical).
# --------------------------------------------------------------------------- #
def test_disabled_gate_is_false(monkeypatch):
    _disable(monkeypatch)
    assert lts.lower_tf_shadow_enabled("engine_b") is False
    assert lts.lower_tf_shadow_enabled("ase") is False


def test_enabled_but_component_not_opted_in_is_false(monkeypatch):
    _enable(monkeypatch, apply_to=("engine_b",))
    assert lts.lower_tf_shadow_enabled("engine_b") is True
    assert lts.lower_tf_shadow_enabled("ase") is False


# --------------------------------------------------------------------------- #
# 2. Enabled → diagnostic fields present.
# --------------------------------------------------------------------------- #
def test_enabled_adds_diagnostic_fields(monkeypatch):
    _enable(monkeypatch)
    fetcher = _RecordingFetcher(
        {
            "M30": _make_candles("M30", trend="up"),
            "M15": _make_candles("M15", trend="up"),
            "M5": _make_candles("M5", trend="up"),
        }
    )
    out = lts.compute_lower_tf_shadow(
        pair={"symbol": "EURUSD", "display": "EUR/USD", "source": "mt5"},
        source="mt5",
        fetch_candles=fetcher,
        direction="LONG",
        component="engine_b",
    )
    # Required diagnostic surface.
    for key in (
        "lower_tf_shadow_enabled",
        "lower_tf_shadow_source",
        "lower_tf_available_tfs",
        "lower_tf_missing_tfs",
        "m30_trend_state",
        "m15_trigger_state",
        "m5_micro_state",
        "m30_setup_alignment",
        "m15_trigger_alignment",
        "m5_precision_alignment",
        "lower_tf_alignment_score_diagnostic_only",
        "lower_tf_conflict_reason",
        "lower_tf_freshness_status",
        "lower_tf_partial_candle_status",
        "lower_tf_spread_atr_status",
        "lower_tf_status",
        "lower_tf_notes",
    ):
        assert key in out, f"missing diagnostic field: {key}"
    assert out["diagnostic_only"] is True
    assert out["lower_tf_shadow_source"] == "MT5"
    assert set(out["lower_tf_available_tfs"]) == {"M30", "M15", "M5"}
    assert out["lower_tf_missing_tfs"] == []
    assert out["lower_tf_status"] in {"PASS", "WARN", "FAIL", "BLOCKED", "NOT_USED"}
    # Up trend + LONG → aligned.
    assert out["m15_trigger_alignment"] == "aligned"


# --------------------------------------------------------------------------- #
# 3. Missing M15/M5 → reported missing, NEVER an H1 fetch.
# --------------------------------------------------------------------------- #
def test_missing_lower_tf_does_not_fall_back_to_h1(monkeypatch):
    _enable(monkeypatch)
    fetcher = _RecordingFetcher({"M30": _make_candles("M30")})  # M15/M5 absent
    out = lts.compute_lower_tf_shadow(
        pair={"symbol": "EURUSD", "display": "EUR/USD", "source": "mt5"},
        source="mt5",
        fetch_candles=fetcher,
        direction="LONG",
        component="engine_b",
    )
    # No silent fallback: H1 (and any non-configured TF) must never be fetched.
    assert "H1" not in fetcher.requested_tfs
    assert set(fetcher.requested_tfs) == {"M30", "M15", "M5"}
    assert set(out["lower_tf_missing_tfs"]) == {"M15", "M5"}
    assert out["per_tf"]["M15"]["available"] is False
    assert out["per_tf"]["M15"]["trend_state"] == "missing"
    assert out["lower_tf_status"] != "PASS"  # incomplete → not a clean PASS


def test_no_fetcher_reports_blocked(monkeypatch):
    _enable(monkeypatch)
    out = lts.compute_lower_tf_shadow(
        pair={"symbol": "EURUSD"},
        source="mt5",
        fetch_candles=None,
        direction="LONG",
        component="engine_b",
    )
    assert out["lower_tf_status"] == "BLOCKED"
    assert set(out["lower_tf_missing_tfs"]) == {"M30", "M15", "M5"}
    assert out["lower_tf_partial_candle_status"] == "unknown_no_fetcher"


# --------------------------------------------------------------------------- #
# Forming candle is reported, never consumed as a closed bar.
# --------------------------------------------------------------------------- #
def test_forming_bar_excluded_not_treated_as_closed(monkeypatch):
    _enable(monkeypatch)
    candles = _make_candles("M30", n=40, forming=True)
    fetcher = _RecordingFetcher({"M30": candles, "M15": _make_candles("M15"), "M5": _make_candles("M5")})
    out = lts.compute_lower_tf_shadow(
        pair={"symbol": "EURUSD", "source": "mt5"},
        source="mt5",
        fetch_candles=fetcher,
        direction="LONG",
        component="engine_b",
    )
    m30 = out["per_tf"]["M30"]
    assert m30["had_forming_bar"] is True
    # Confirmed count excludes the forming bar.
    assert m30["confirmed_bar_count"] == len(candles) - 1
    assert out["lower_tf_partial_candle_status"] == "forming_excluded"


# --------------------------------------------------------------------------- #
# 4. Payload carries NO scoring/gating/eligibility keys.
# --------------------------------------------------------------------------- #
def test_payload_has_no_decision_affecting_keys(monkeypatch):
    _enable(monkeypatch)
    fetcher = _RecordingFetcher(
        {"M30": _make_candles("M30"), "M15": _make_candles("M15"), "M5": _make_candles("M5")}
    )
    out = lts.compute_lower_tf_shadow(
        pair={"symbol": "EURUSD", "source": "mt5"},
        source="mt5",
        fetch_candles=fetcher,
        direction="LONG",
        component="engine_b",
    )
    forbidden = {
        "score",
        "passed",
        "eligible",
        "eligibility",
        "direction",  # only direction_ref (read-only echo) is allowed
        "confidence",
        "recommended_stop_loss",
        "recommended_take_profit",
        "sl",
        "tp",
        "risk",
        "structural_verdict",
    }
    assert forbidden.isdisjoint(out.keys())
    assert out["direction_ref"] == "LONG"
    assert out["diagnostic_only"] is True


if __name__ == "__main__":
    class _MP:
        def setitem(self, d, k, v):
            d[k] = v

    mp = _MP()
    tests = [
        test_disabled_gate_is_false,
        test_enabled_but_component_not_opted_in_is_false,
        test_enabled_adds_diagnostic_fields,
        test_missing_lower_tf_does_not_fall_back_to_h1,
        test_no_fetcher_reports_blocked,
        test_forming_bar_excluded_not_treated_as_closed,
        test_payload_has_no_decision_affecting_keys,
    ]
    failures = 0
    for t in tests:
        try:
            t(mp)
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print("ALL_OK" if failures == 0 else f"{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
