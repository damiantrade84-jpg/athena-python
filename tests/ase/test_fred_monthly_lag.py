"""T0.5 — OECD MEI monthly FRED series get a realistic publication lag."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import append_ptis_rows, fred_series_id, row_from_rule
from athena_ase.data.ingest.fred import _rule_for_fred_series
from athena_ase.data.ptis import PTISStore


def _ms(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def test_series_routing():
    assert _rule_for_fred_series("IRSTCI01GBM156N") == AvailabilityRuleId.FRED_MONTHLY_OECD
    assert _rule_for_fred_series("IRSTCI01JPM156N") == AvailabilityRuleId.FRED_MONTHLY_OECD
    assert _rule_for_fred_series("DFF") == AvailabilityRuleId.FRED_POLICY
    assert _rule_for_fred_series("FEDFUNDS") == AvailabilityRuleId.FRED_POLICY
    assert _rule_for_fred_series("ECBDFR") == AvailabilityRuleId.FRED_DAILY


def test_monthly_observation_unavailable_until_publication_lag():
    root = tempfile.mkdtemp(prefix="ase_ptis_")
    store = PTISStore(root)
    sid = fred_series_id("IRSTCI01GBM156N")
    obs_ms = _ms(2026, 1, 1)  # January observation
    row = row_from_rule(
        sid,
        AvailabilityRuleId.FRED_MONTHLY_OECD,
        value_time_ms=obs_ms,
        value=4.25,
    )
    assert append_ptis_rows(store, sid, "FRED_MONTHLY_OECD", [row]) == 1

    month_end = _ms(2026, 2, 1)
    # One day after month end: with the old daily rule this was visible; now it must not be.
    assert len(store.asof(sid, month_end + 86_400_000)) == 0
    # 45 days after month end (> 42-day conservative lag): visible.
    rows = store.asof(sid, month_end + 45 * 86_400_000)
    assert len(rows) == 1
    assert float(rows[0]["value"]) == 4.25
