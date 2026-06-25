"""Offline tests for deterministic FOMC tone scoring (no network, fixtures only)."""

from __future__ import annotations

import os
import tempfile

from macro.fomc_tone import _target_range_change_bps, process_event, score_statement
from macro.store import MacroEventStore
from macro.tone_store import MacroToneStore

# Neutral baseline statement containing no scored phrases.
BASE = (
    "The Committee seeks to achieve maximum employment and inflation at the rate of two "
    "percent over the longer run. The Committee will continue to monitor incoming data."
)


def test_added_inflation_language_scores_hawkish():
    cur = BASE + " Inflation remains elevated."
    r = score_statement(cur, BASE)
    assert r["tone_score"] > 10
    assert r["tone_direction"] == "hawkish"
    assert r["tone_label"] in ("MILDLY_HAWKISH", "HAWKISH", "STRONGLY_HAWKISH")


def test_added_employment_risk_scores_dovish():
    cur = BASE + " Risks to employment have increased."
    r = score_statement(cur, BASE)
    assert r["tone_score"] < -10
    assert r["tone_direction"] == "dovish"
    assert r["tone_label"] in ("MILDLY_DOVISH", "DOVISH", "STRONGLY_DOVISH")


def test_repeated_unchanged_phrase_has_limited_contribution():
    prev = BASE + " Inflation remains elevated."
    cur = BASE + " Inflation remains elevated."
    r = score_statement(cur, prev)
    unchanged = [e for e in r["evidence"] if e["change"] == "unchanged"]
    assert unchanged, "expected an unchanged-phrase evidence entry"
    # Unchanged contributes 0.3x the 45-pt base = 13.5, far below the added 45.
    assert unchanged[0]["impact"] == 13.5
    assert r["inflation_language_score"] < 45


def test_added_phrase_outweighs_unchanged_phrase():
    prev = BASE + " Job gains remain strong."
    cur = BASE + " Inflation remains elevated. Job gains remain strong."
    r = score_statement(cur, prev)
    # inflation = added (full), labor = unchanged (0.3x)
    assert r["inflation_language_score"] > r["labor_market_score"]


def test_removed_hawkish_phrase_reduces_hawkish_score():
    prev = BASE + " Inflation remains elevated."
    cur = BASE
    r = score_statement(cur, prev)
    assert r["tone_score"] < 0  # removing hawkish language = dovish pressure
    removed = [e for e in r["evidence"] if e["change"] == "removed"]
    assert removed and removed[0]["direction"] == "dovish"


def test_removed_dovish_phrase_increases_hawkish_score():
    prev = BASE + " Inflation has eased."
    cur = BASE
    r = score_statement(cur, prev)
    assert r["tone_score"] > 0  # removing dovish language = hawkish pressure


def test_missing_previous_statement_lowers_confidence():
    cur = BASE + " Inflation remains elevated."
    with_prev = score_statement(cur, BASE)["confidence"]
    no_prev = score_statement(cur, None)["confidence"]
    assert no_prev < with_prev


def test_missing_statement_text_returns_unknown():
    assert score_statement("", BASE)["tone_label"] == "UNKNOWN"
    assert score_statement("", BASE)["confidence"] == 0.0
    assert score_statement("too short", BASE)["tone_label"] == "UNKNOWN"


def test_fred_target_range_change_read_or_unavailable():
    # Present (a 25bps hike): 5.00-5.25 -> 5.25-5.50 → +25.0 bps
    ev = {
        "previous_lower": 5.0, "previous_upper": 5.25,
        "actual_lower": 5.25, "actual_upper": 5.5,
    }
    assert _target_range_change_bps(ev) == 25.0
    # Gracefully unavailable when FRED values are missing.
    assert _target_range_change_bps({"previous_lower": 5.0}) is None


def test_fred_change_is_not_called_market_surprise():
    r = score_statement(BASE + " Inflation remains elevated.", BASE,
                        target_range_change_bps=25.0)
    assert r["target_range_change_bps"] == 25.0
    assert r["rate_decision_score"] == 60  # 25 * 2.4
    # The FRED-derived change is NEVER stored as a market surprise.
    assert r["market_expectation_surprise_bps"] is None


def test_market_expectation_surprise_remains_null():
    r = score_statement(BASE + " Inflation remains elevated.", BASE)
    assert r["market_expectation_surprise_bps"] is None
    assert any("market expectation" in w.lower() for w in r["warnings"])


def test_tone_score_within_bounds_and_component_clamped():
    cur = (
        BASE + " Inflation remains elevated. Inflation has risen. Risks to inflation remain."
    )
    r = score_statement(cur, BASE)
    assert -100 <= r["tone_score"] <= 100
    assert r["inflation_language_score"] == 100  # 45+35+25 clamped to 100


def test_confidence_within_bounds():
    for cur, prev in [
        (BASE + " Inflation remains elevated.", BASE),
        (BASE + " Inflation has eased.", None),
        ("", BASE),
    ]:
        c = score_statement(cur, prev)["confidence"]
        assert 0.0 <= c <= 1.0


def test_process_event_reuses_fred_fields_without_surprise():
    d = tempfile.mkdtemp(prefix="tone_proc_")
    store = MacroEventStore(os.path.join(d, "m.db"))
    tstore = MacroToneStore(os.path.join(d, "m.db"))
    store.upsert_event({
        "id": "FOMC_STATEMENT:2026-06-17",
        "event_type": "FOMC_STATEMENT",
        "currency": "USD",
        "title": "Federal Reserve issues FOMC statement",
        "scheduled_time_utc": "2026-06-17T18:00:00+00:00",
        "status": "RELEASED",
        "source": "fed_press_monetary_rss",
        "raw_text": BASE + " Inflation remains elevated.",
        "previous_lower": 5.0, "previous_upper": 5.25,
        "actual_lower": 5.25, "actual_upper": 5.5,
    })
    rec = process_event("FOMC_STATEMENT:2026-06-17", store=store, tone_store=tstore)
    assert rec is not None
    assert rec["target_range_change_bps"] == 25.0
    assert rec["rate_decision_score"] == 60
    assert rec["market_expectation_surprise_bps"] is None
    # Idempotent: re-processing updates in place, never duplicates.
    process_event("FOMC_STATEMENT:2026-06-17", store=store, tone_store=tstore)
    assert tstore.count() == 1


def test_scan_candidate_receives_tone_without_changing_confluence(monkeypatch):
    import macro.store as store_mod
    import macro.tone_store as tone_store_mod
    from macro import scan_integration

    d = tempfile.mkdtemp(prefix="tone_scan_")
    ev_store = MacroEventStore(os.path.join(d, "m.db"))
    t_store = MacroToneStore(os.path.join(d, "m.db"))
    monkeypatch.setattr(store_mod, "_default_store", ev_store, raising=False)
    monkeypatch.setattr(tone_store_mod, "_default_tone_store", t_store, raising=False)
    # Seed a stored tone via the real pipeline.
    ev_store.upsert_event({
        "id": "FOMC_STATEMENT:scan",
        "event_type": "FOMC_STATEMENT", "currency": "USD",
        "title": "FOMC statement", "scheduled_time_utc": "2026-06-17T18:00:00+00:00",
        "status": "RELEASED", "source": "fed_press_monetary_rss",
        "raw_text": BASE + " Inflation remains elevated.",
    })
    process_event("FOMC_STATEMENT:scan", store=ev_store, tone_store=t_store)

    sig = {"symbol": "EUR/USD", "confluenceScore": 77, "type": "forex"}
    scan_integration.annotate_signals([sig])
    assert sig["confluenceScore"] == 77  # untouched
    assert sig["fomcExecutionUse"] == "CONTEXT_ONLY"
    assert sig["fomcToneLabel"] is not None
    assert sig["fomcToneDirection"] in ("hawkish", "dovish", "neutral")
