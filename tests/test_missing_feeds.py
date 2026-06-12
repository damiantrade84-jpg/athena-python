"""Missing feed routing tests."""

from __future__ import annotations

from athena_ase.inference.routing import route_for_feeds


def test_enriched_missing_routes_core():
    dq = route_for_feeds(core_missing=[], enriched_missing=["cot_pct"])
    assert dq["route"] == "core"
    assert dq["coreOk"] is True


def test_core_missing_routes_flat():
    dq = route_for_feeds(core_missing=["volu_z"], enriched_missing=[])
    assert dq["coreOk"] is False
    assert dq["decisionStatus"] == "FLAT"


def test_unverified_lag_excluded_from_enriched():
    dq = route_for_feeds(core_missing=[], enriched_missing=["cot_pct"], unverified_lag=["cot_pct"])
    assert dq["route"] in {"core", "enriched"}
