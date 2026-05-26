"""Engine A analyze_pair abort diagnostics."""

from engine_a_analyze_abort import (
    build_abort_stub_fields,
    pop_analyze_pair_abort,
    record_analyze_pair_abort,
)


def test_abort_record_pop_and_stub_fields():
    pair = {"display": "AAPL", "symbol": "AAPL.US", "type": "stock"}
    record_analyze_pair_abort(
        pair,
        "pre_scoring_freshness",
        detail="H4:stale_multi_bucket",
        score_group="us_stock_single",
    )
    abort = pop_analyze_pair_abort(pair)
    assert abort is not None
    assert abort["reason"] == "pre_scoring_freshness"
    stub_fields = build_abort_stub_fields(abort)
    assert stub_fields["engineAAbortReason"] == "pre_scoring_freshness"
    assert stub_fields["scoreGroup"] == "us_stock_single"
    assert pop_analyze_pair_abort(pair) is None
