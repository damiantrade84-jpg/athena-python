from __future__ import annotations

import ast
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_forex_edge_package_has_no_forbidden_imports() -> None:
    package = ROOT / "athena_research" / "forex_edge"
    forbidden = {
        "athena",
        "athena_ase",
        "scoring",
        "factor_scoring",
        "forex_scoring",
        "execution",
        "auto_trader",
        "risk_engine",
        "guardian",
        "mt5_executor",
        "bybit_executor",
    }
    imported: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)


def test_frozen_universe_matches_current_ase_contract() -> None:
    from athena_ase.universe import instruments_for_family
    from athena_research.forex_edge.universe import FOREX_PAIRS

    current = tuple(item.symbol for item in instruments_for_family("forex"))
    assert FOREX_PAIRS == current
    assert len(FOREX_PAIRS) == 21


def test_quote_orientation_normalizes_currency_appreciation() -> None:
    from athena_research.forex_edge.universe import currency_usd_price

    assert currency_usd_price("EUR", 1.10) == pytest.approx(1.10)
    assert currency_usd_price("JPY", 150.0) == pytest.approx(1 / 150.0)
    assert currency_usd_price("CAD", 1.35) == pytest.approx(1 / 1.35)


def test_dedicated_config_is_frozen_and_research_only() -> None:
    from athena_research.forex_edge.config import load_config

    cfg = load_config(ROOT / "configs" / "forex_edge_research.yaml")
    assert cfg["portfolio"]["development_end"] == "2018-12-31"
    assert cfg["fixing"]["development_end"] == "2020-12-31"
    assert cfg["portfolio"]["top_n"] == 4
    assert cfg["portfolio"]["min_currencies"] == 12
    assert cfg["production_eligible"] is False


def test_config_redaction_removes_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_research.forex_edge.config import redact_secrets

    monkeypatch.setenv("FRED_API_KEY", "fred-secret-value")
    payload = {
        "url": "https://api.test/data?api_key=fred-secret-value",
        "api_key": "fred-secret-value",
        "nested": {"Authorization": "Bearer fred-secret-value"},
    }
    redacted = redact_secrets(payload)
    assert "fred-secret-value" not in repr(redacted)
    assert redacted["api_key"] == "[REDACTED]"


def test_config_redaction_normalizes_aliases_and_url_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_research.forex_edge.config import redact_secrets

    monkeypatch.setenv("FRED_API_KEY", "fred-secret-value")
    payload = {
        "Api-Key": "fred-secret-value",
        "CLIENT SECRET": "client-value",
        "header": "Bearer bearer-value",
        "url": (
            "https://user:password@example.test/data"
            "?page=2&API-KEY=fred-secret-value&format=json"
        ),
    }

    redacted = redact_secrets(payload)

    assert "fred-secret-value" not in repr(redacted)
    assert "client-value" not in repr(redacted)
    assert "bearer-value" not in repr(redacted)
    assert "password" not in redacted["url"]
    assert redacted["Api-Key"] == "[REDACTED]"
    assert redacted["CLIENT SECRET"] == "[REDACTED]"
    assert redacted["header"] == "Bearer [REDACTED]"
    assert "page=2" in redacted["url"]
    assert "format=json" in redacted["url"]


def test_models_serialize_to_json_safe_mappings() -> None:
    from athena_research.forex_edge.models import (
        EligibilityResult,
        EligibilityStatus,
        EvidenceFlag,
        ReasonCode,
        StudyResult,
        StudyStatus,
        TrialRecord,
    )

    result = StudyResult(
        study_status=StudyStatus.BLOCKED_DATA,
        production_eligible=False,
        evidence_flags=(EvidenceFlag.PBO_UNAVAILABLE,),
        metrics={"reasons": (ReasonCode.PBO_UNAVAILABLE,)},
        eligibility=EligibilityResult(
            eligible=False,
            status=EligibilityStatus.BLOCKED_DATA,
            reason_codes=(ReasonCode.PBO_UNAVAILABLE,),
            details={"flags": (EvidenceFlag.PBO_UNAVAILABLE,)},
        ),
        trials=(
            TrialRecord(
                trial_id="trial-1",
                study="portfolio",
                configuration="momentum",
                cost_multiplier=1.0,
                returns_hash="abc123",
                n_observations=12,
            ),
        ),
    )

    assert json.loads(json.dumps(result.to_dict()))["production_eligible"] is False


def _study_result(*, production_eligible: bool, metrics: object) -> object:
    from athena_research.forex_edge.models import (
        EligibilityResult,
        EligibilityStatus,
        StudyResult,
        StudyStatus,
    )

    return StudyResult(
        study_status=StudyStatus.COMPLETED_NO_EDGE,
        production_eligible=production_eligible,
        evidence_flags=(),
        metrics=metrics,
        eligibility=EligibilityResult(
            eligible=False,
            status=EligibilityStatus.INELIGIBLE,
        ),
        trials=(),
    )


def test_macro_asof_selects_latest_verified_row_and_ignores_future_unverified_row() -> None:
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2020-01-01", "2020-02-01", "2020-03-01"],
                utc=True,
            ),
            "available_time": pd.to_datetime(
                ["2020-01-15", "2020-02-10", "2020-05-01"],
                utc=True,
            ),
            "value": [1.0, 2.0, 3.0],
            "availability_verified": [True, True, False],
        }
    )

    row = macro_asof(rows, pd.Timestamp("2020-02-15", tz="UTC"))

    assert row["timestamp"] == pd.Timestamp("2020-02-01", tz="UTC")
    assert row["available_time"] == pd.Timestamp("2020-02-10", tz="UTC")
    assert row["value"] == pytest.approx(2.0)


def test_macro_asof_rejects_unverified_row_when_it_is_eligible() -> None:
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2020-01-01", "2020-02-01"],
                utc=True,
            ),
            "available_time": pd.to_datetime(
                ["2020-01-15", "2020-02-10"],
                utc=True,
            ),
            "value": [1.0, 2.0],
            "availability_verified": [True, False],
        }
    )

    with pytest.raises(ValueError, match="UNVERIFIED_AVAILABILITY"):
        macro_asof(rows, pd.Timestamp("2020-02-15", tz="UTC"))


def test_macro_asof_rejects_missing_required_schema() -> None:
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2020-01-01"], utc=True),
            "value": [1.0],
        }
    )

    with pytest.raises(ValueError, match="MISSING_SERIES"):
        macro_asof(rows, pd.Timestamp("2020-01-02", tz="UTC"))


def test_macro_asof_rejects_malformed_available_time() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2020-01-01"], utc=True),
            "available_time": ["not-a-timestamp"],
            "value": [1.0],
            "availability_verified": [True],
        }
    )

    with pytest.raises(InvalidResearchInputError, match="available_time"):
        macro_asof(rows, pd.Timestamp("2020-01-02", tz="UTC"))


def test_macro_asof_rejects_invalid_availability_flags() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2020-01-01", "2020-01-02"],
                utc=True,
            ),
            "available_time": pd.to_datetime(
                ["2020-01-15", "2020-01-16"],
                utc=True,
            ),
            "value": [1.0, 2.0],
            "availability_verified": [True, None],
        }
    )

    with pytest.raises(InvalidResearchInputError, match="availability_verified"):
        macro_asof(rows, pd.Timestamp("2020-01-20", tz="UTC"))


@pytest.mark.parametrize("decision_time", [None, "not-a-timestamp"])
def test_macro_asof_rejects_invalid_decision_time(
    decision_time: object,
) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2020-01-01"], utc=True),
            "available_time": pd.to_datetime(["2020-01-15"], utc=True),
            "value": [1.0],
            "availability_verified": [True],
        }
    )

    with pytest.raises(InvalidResearchInputError, match="decision_time"):
        macro_asof(rows, decision_time)  # type: ignore[arg-type]


def test_macro_asof_rejects_null_eligible_value() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.quality import macro_asof

    rows = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2020-01-01"], utc=True),
            "available_time": pd.to_datetime(["2020-01-15"], utc=True),
            "value": [None],
            "availability_verified": [True],
        }
    )

    with pytest.raises(InvalidResearchInputError, match="value"):
        macro_asof(rows, pd.Timestamp("2020-01-20", tz="UTC"))


def test_http_response_status_errors_do_not_leak_url_or_content() -> None:
    from athena_research.forex_edge.sources.common import HttpResponse

    for status in (200, 299):
        HttpResponse(
            status,
            b"secret-content",
            {},
            "https://example.test/?api_key=secret",
        ).raise_for_status()

    for status in (300, 404, 500):
        response = HttpResponse(
            status,
            b"secret-content",
            {},
            "https://example.test/?api_key=secret",
        )
        with pytest.raises(RuntimeError) as exc:
            response.raise_for_status()
        assert str(exc.value) == f"provider HTTP {status}"
        assert "secret" not in str(exc.value)
        assert "example.test" not in str(exc.value)


def test_requests_get_wraps_read_only_get(monkeypatch: pytest.MonkeyPatch) -> None:
    import athena_research.forex_edge.sources.common as common

    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        content = b"payload"
        headers = {"Content-Type": "application/json"}
        url = "https://provider.test/final?x=1"

    def fake_get(
        url: str,
        *,
        params: object = None,
        headers: object = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr(common.requests, "get", fake_get)

    response = common.requests_get(
        "https://provider.test/base",
        params={"series_id": "DEXUSEU"},
        headers={"Accept": "application/json"},
        timeout=12.5,
    )

    assert calls == [
        {
            "url": "https://provider.test/base",
            "params": {"series_id": "DEXUSEU"},
            "headers": {"Accept": "application/json"},
            "timeout": 12.5,
        }
    ]
    assert response.status_code == 200
    assert response.content == b"payload"
    assert response.headers == {"Content-Type": "application/json"}
    assert response.url == "https://provider.test/final?x=1"


def test_fred_normalization_uses_vintage_and_explicit_units() -> None:
    from athena_research.forex_edge.sources.fred import (
        normalize_fred_observations,
        percent_to_decimal,
    )

    payload = {
        "observations": [
            {
                "realtime_start": "2020-01-06",
                "realtime_end": "2020-01-06",
                "date": "2020-01-03",
                "value": "150.0",
            }
        ]
    }
    frame = normalize_fred_observations(
        payload,
        series_id="DEXJPUS",
        currency="JPY",
        kind="spot",
        unit="Japanese Yen to U.S. Dollar",
        usd_per_currency=False,
    )

    assert frame.loc[0, "value"] == pytest.approx(1 / 150.0)
    assert frame.loc[0, "available_time"] == pd.Timestamp(
        "2020-01-06 16:15",
        tz="America/New_York",
    ).tz_convert("UTC")
    assert frame.loc[0, "unit"] == "USD_PER_CURRENCY"
    assert bool(frame.loc[0, "availability_verified"]) is True
    assert percent_to_decimal(5.25, "Percent") == pytest.approx(0.0525)
    with pytest.raises(ValueError, match="AMBIGUOUS_UNIT"):
        percent_to_decimal(5.25, "Index")

    rate_payload = {
        "observations": [
            {
                "realtime_start": "2020-01-06",
                "realtime_end": "2020-01-06",
                "date": "2020-01-03",
                "value": "5.25",
            }
        ]
    }
    rate = normalize_fred_observations(
        rate_payload,
        series_id="DFF",
        currency="USD",
        kind="rate",
        unit="Percent",
    )
    assert rate.loc[0, "value"] == pytest.approx(5.25)
    assert rate.loc[0, "unit"] == "Percent"
    assert bool(rate.loc[0, "availability_verified"]) is False
    assert rate.loc[0, "availability_reason"] == "UNVERIFIED_AVAILABILITY"


def test_fred_errors_redact_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from athena_research.forex_edge.sources.common import HttpResponse
    from athena_research.forex_edge.sources.fred import fetch_fred_series

    monkeypatch.setenv("FRED_API_KEY", "super-secret")

    def fake_get(
        url: str,
        *,
        params: object = None,
        headers: object = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        return HttpResponse(
            500,
            b"failure super-secret",
            {},
            url + "?api_key=super-secret",
        )

    with pytest.raises(RuntimeError) as exc:
        fetch_fred_series(
            "DEXUSEU",
            api_base="https://api.test/fred",
            api_key_env="FRED_API_KEY",
            http_get=fake_get,
        )

    assert "super-secret" not in str(exc.value)
    assert "api_key=super-secret" not in str(exc.value)


def test_fred_fetch_requires_api_key_without_leaking_env_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_research.forex_edge.sources.fred import fetch_fred_series

    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="FRED_API_KEY not configured"):
        fetch_fred_series(
            "DEXUSEU",
            api_base="https://api.test/fred",
            api_key_env="FRED_API_KEY",
        )


def test_bis_reer_applies_conservative_lag_and_revision_flag() -> None:
    from athena_research.forex_edge.sources.bis import parse_bis_reer_csv

    content = (
        "FREQ,TYPE,BASKET,REF_AREA,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE\n"
        "M,R,B,GB,2020-01,101.5,IX\n"
    ).encode()
    frame = parse_bis_reer_csv(
        content,
        currency="GBP",
        series_key="M.R.B.GB",
    )

    assert frame.loc[0, "timestamp"] == pd.Timestamp(
        "2020-01-31 23:59:59.999999999",
        tz="UTC",
    )
    assert frame.loc[0, "available_time"] == pd.Timestamp(
        "2020-02-29 23:59:59",
        tz="UTC",
    )
    assert bool(frame.loc[0, "revision_history_verified"]) is False


def test_bis_reer_rejects_ambiguous_units() -> None:
    from athena_research.forex_edge.sources.bis import parse_bis_reer_csv

    content = (
        "TIME_PERIOD,OBS_VALUE,UNIT_MEASURE\n"
        "2020-01,101.5,UNKNOWN\n"
    ).encode()

    with pytest.raises(ValueError, match="AMBIGUOUS_UNIT"):
        parse_bis_reer_csv(content, currency="GBP", series_key="M.R.B.GB")


def test_bis_reer_rejects_duplicate_observation_conflicts() -> None:
    from athena_research.forex_edge.sources.bis import parse_bis_reer_csv

    content = (
        "TIME_PERIOD,OBS_VALUE,UNIT_MEASURE\n"
        "2020-01,101.5,IX\n"
        "2020-01,102.5,IX\n"
    ).encode()

    with pytest.raises(ValueError, match="DUPLICATE_CONFLICT"):
        parse_bis_reer_csv(content, currency="GBP", series_key="M.R.B.GB")


def test_cftc_applies_following_monday_and_reports_missing_mapping() -> None:
    from athena_research.forex_edge.sources.cftc import (
        missing_cot_currencies,
        normalize_cftc_frame,
    )

    raw = pd.DataFrame(
        {
            "Market and Exchange Names": [
                "EURO FX - CHICAGO MERCANTILE EXCHANGE"
            ],
            "As of Date in Form YYYY-MM-DD": ["2020-01-07"],
            "Noncommercial Positions-Long (All)": [100],
            "Noncommercial Positions-Short (All)": [40],
        }
    )
    frame = normalize_cftc_frame(raw, {"EUR": "EURO FX"})

    assert frame.loc[0, "net_noncommercial"] == 60
    assert frame.loc[0, "available_time"] == pd.Timestamp(
        "2020-01-13",
        tz="UTC",
    )
    assert missing_cot_currencies(("EUR", "SGD"), {"EUR": "EURO FX"}) == (
        "SGD",
    )


def test_cftc_rejects_duplicate_conflicts_and_bad_archives() -> None:
    from io import BytesIO
    from zipfile import ZipFile

    from athena_research.forex_edge.sources.cftc import (
        normalize_cftc_frame,
        parse_cftc_zip,
    )

    raw = pd.DataFrame(
        {
            "Market and Exchange Names": ["EURO FX", "EURO FX"],
            "As of Date in Form YYYY-MM-DD": ["2020-01-07", "2020-01-07"],
            "Noncommercial Positions-Long (All)": [100, 101],
            "Noncommercial Positions-Short (All)": [40, 40],
        }
    )

    with pytest.raises(ValueError, match="DUPLICATE_CONFLICT"):
        normalize_cftc_frame(raw, {"EUR": "EURO FX"})

    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("one.csv", "a\n1\n")
        archive.writestr("two.csv", "a\n2\n")
    with pytest.raises(ValueError, match="exactly one"):
        parse_cftc_zip(payload.getvalue())


def test_dukascopy_ticks_resample_to_executable_m5_bars(tmp_path: Path) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    source = tmp_path / "EURUSD_ticks.csv"
    source.write_text(
        "time,bid,ask\n"
        "2021-01-04 15:30:01,1.2000,1.2002\n"
        "2021-01-04 15:34:59,1.2004,1.2006\n"
        "2021-01-04 15:35:01,1.2003,1.2005\n",
        encoding="utf-8",
    )

    bars = import_dukascopy(
        source,
        symbol="EURUSD",
        timezone_name="UTC",
        schema="tick_bid_ask",
    )
    first = bars.iloc[0]

    assert first["timestamp"] == pd.Timestamp("2021-01-04 15:35:00Z")
    assert first["bid_open"] == pytest.approx(1.2000)
    assert first["bid_close"] == pytest.approx(1.2004)
    assert first["ask_open"] == pytest.approx(1.2002)
    assert first["ask_close"] == pytest.approx(1.2006)
    assert (bars["ask_low"] >= bars["bid_low"]).all()


@pytest.mark.parametrize(
    ("schema", "timezone_name", "expected"),
    [
        ("midpoint_m5", "UTC", "MIDPOINT_ONLY"),
        ("tick_bid_ask", "", "AMBIGUOUS_TIMEZONE"),
    ],
)
def test_dukascopy_rejects_ineligible_schema_or_timezone(
    tmp_path: Path,
    schema: str,
    timezone_name: str,
    expected: str,
) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    source = tmp_path / "quotes.csv"
    source.write_text(
        "time,bid,ask\n2021-01-04 15:30:01,1.2,1.2002\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=expected):
        import_dukascopy(
            source,
            symbol="EURUSD",
            timezone_name=timezone_name,
            schema=schema,
        )


def test_dukascopy_rejects_crossed_and_conflicting_quotes(
    tmp_path: Path,
) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    crossed = tmp_path / "crossed.csv"
    crossed.write_text(
        "time,bid,ask\n2021-01-04 15:30:01,1.2003,1.2002\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="CROSSED_QUOTE"):
        import_dukascopy(
            crossed,
            symbol="EURUSD",
            timezone_name="UTC",
            schema="tick_bid_ask",
        )

    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text(
        "time,bid,ask\n"
        "2021-01-04 15:30:01,1.2000,1.2002\n"
        "2021-01-04 15:30:01,1.2001,1.2003\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DUPLICATE_CONFLICT"):
        import_dukascopy(
            duplicate,
            symbol="EURUSD",
            timezone_name="UTC",
            schema="tick_bid_ask",
        )


def test_dukascopy_m5_bars_shift_start_timestamps_to_end(
    tmp_path: Path,
) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    source = tmp_path / "EURUSD_m5.csv"
    source.write_text(
        "time,bid_open,bid_high,bid_low,bid_close,"
        "ask_open,ask_high,ask_low,ask_close\n"
        "2021-01-04 15:30:00,1.2,1.2003,1.1999,1.2001,"
        "1.2002,1.2005,1.2001,1.2003\n",
        encoding="utf-8",
    )

    bars = import_dukascopy(
        source,
        symbol="EURUSD",
        timezone_name="UTC",
        schema="m5_bid_ask",
    )

    assert bars.loc[0, "timestamp"] == pd.Timestamp("2021-01-04 15:35:00Z")


def test_dukascopy_allows_explicit_column_mapping(tmp_path: Path) -> None:
    from athena_research.forex_edge.sources.dukascopy import import_dukascopy

    source = tmp_path / "custom.csv"
    source.write_text(
        "datetime,bid_px,ask_px\n"
        "2021-01-04 15:30:01,1.2000,1.2002\n",
        encoding="utf-8",
    )

    bars = import_dukascopy(
        source,
        symbol="EURUSD",
        timezone_name="UTC",
        schema="custom_vendor_tick",
        column_mapping={"time": "datetime", "bid": "bid_px", "ask": "ask_px"},
    )

    assert len(bars) == 1
    assert bars.loc[0, "bid_open"] == pytest.approx(1.2)


def test_portfolio_signals_use_only_available_values_and_do_not_impute() -> None:
    from athena_research.forex_edge.portfolio.signals import (
        carry_proxy_scores,
        momentum_12_1_scores,
        reer_value_5y_scores,
    )

    decision = pd.Timestamp("2020-01-31 23:59:59Z")
    rates = pd.DataFrame(
        {
            "currency": ["EUR", "GBP", "JPY"],
            "timestamp": pd.to_datetime(
                ["2020-01-30", "2020-01-30", "2020-01-30"],
                utc=True,
            ),
            "available_time": pd.to_datetime(
                ["2020-01-31", "2020-01-31", "2020-02-03"],
                utc=True,
            ),
            "value": [1.0, 2.0, -0.1],
            "unit": ["Percent", "Percent", "Percent"],
            "availability_verified": [True, True, True],
        }
    )
    carry = carry_proxy_scores(rates, decision)
    assert carry.to_dict() == {"EUR": 0.01, "GBP": 0.02}
    assert "JPY" not in carry

    dates = pd.date_range("2018-01-31", periods=25, freq="ME", tz="UTC")
    returns = pd.DataFrame(
        {
            "timestamp": list(dates) * 2,
            "currency": ["EUR"] * len(dates) + ["GBP"] * len(dates),
            "return": [0.01] * len(dates)
            + [0.02] * (len(dates) - 2)
            + [float("nan")]
            + [0.02],
            "available_time": list(dates) * 2,
        }
    )
    momentum = momentum_12_1_scores(returns, dates[-1] + pd.Timedelta(days=1))
    assert momentum["EUR"] == pytest.approx((1.01**11) - 1)
    assert "GBP" not in momentum

    reer_dates = pd.date_range("2015-01-31", periods=61, freq="ME", tz="UTC")
    reer = pd.DataFrame(
        {
            "timestamp": list(reer_dates) * 2,
            "available_time": list(reer_dates) * 2,
            "currency": ["EUR"] * 61 + ["GBP"] * 61,
            "value": [100.0] * 60 + [90.0] + [100.0] * 60 + [float("nan")],
        }
    )
    value = reer_value_5y_scores(reer, reer_dates[-1])
    assert value["EUR"] > 0
    assert "GBP" not in value


def test_centered_ranks_and_blend_preserve_missingness() -> None:
    from athena_research.forex_edge.portfolio.signals import (
        blend_rank_scores,
        centered_rank_scores,
    )

    ranked = centered_rank_scores(
        pd.Series({"EUR": 3.0, "GBP": 2.0, "JPY": 1.0})
    )
    assert ranked["EUR"] == pytest.approx(1.0)
    assert ranked["JPY"] == pytest.approx(-1.0)

    blend = blend_rank_scores(
        {
            "carry": pd.Series({"EUR": 1.0, "GBP": 0.0}),
            "momentum": pd.Series({"EUR": 0.5, "GBP": -0.5}),
            "value": pd.Series({"EUR": 0.0}),
        }
    )
    assert blend.to_dict() == {"EUR": pytest.approx(0.5)}


def test_currency_weights_are_neutral_capped_and_map_to_canonical_pairs() -> None:
    from athena_research.forex_edge.portfolio.construction import (
        build_currency_weights,
        map_currency_weights_to_pairs,
    )

    scores = pd.Series(
        {
            "AUD": 6,
            "EUR": 5,
            "GBP": 4,
            "NZD": 3,
            "CAD": -3,
            "CHF": -4,
            "JPY": -5,
            "USD": -6,
            "BRL": 2,
            "INR": 1,
            "MXN": 0,
            "SGD": -1,
        },
        dtype=float,
    )
    weights = build_currency_weights(scores, top_n=4, min_currencies=12)

    assert weights.abs().sum() == pytest.approx(1.0)
    assert weights.sum() == pytest.approx(0.0)
    assert weights.abs().max() <= 0.25

    pair_weights = map_currency_weights_to_pairs(weights)
    assert set(pair_weights.index).issubset(
        {
            "EURUSD",
            "GBPUSD",
            "USDJPY",
            "AUDUSD",
            "NZDUSD",
            "USDCAD",
            "USDCHF",
            "USDZAR",
            "USDMXN",
            "USDSGD",
            "USDBRL",
            "USDINR",
        }
    )


def test_volatility_scaling_uses_prior_returns_only() -> None:
    from athena_research.forex_edge.portfolio.construction import (
        scale_weights_to_vol,
    )

    weights = pd.Series({"EUR": 0.5, "JPY": -0.5})
    prior = pd.Series([0.001] * 62 + [0.002])
    scaled = scale_weights_to_vol(
        weights,
        prior,
        target_vol=0.10,
        lookback=63,
        max_gross=2.0,
    )
    changed_future = pd.concat([prior, pd.Series([0.50])], ignore_index=True)

    assert scale_weights_to_vol(
        weights,
        changed_future.iloc[:-1],
        target_vol=0.10,
        lookback=63,
        max_gross=2.0,
    ).to_dict() == pytest.approx(scaled.to_dict())
    assert scaled.abs().sum() <= 2.0


def test_portfolio_positions_start_after_decision_and_charge_turnover() -> None:
    from athena_research.forex_edge.portfolio.backtest import (
        run_monthly_portfolio,
    )

    dates = pd.to_datetime(
        ["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"],
        utc=True,
    )
    pair_returns = pd.DataFrame(
        {
            "timestamp": list(dates) * 2,
            "symbol": ["EURUSD"] * 4 + ["USDJPY"] * 4,
            "return": [0.0, 0.50, 0.01, 0.01, 0.0, -0.50, 0.01, 0.01],
        }
    )
    decisions = {
        pd.Timestamp("2020-01-31T00:00:00Z"): pd.Series(
            {"EUR": 0.5, "JPY": -0.5}
        )
    }

    result = run_monthly_portfolio(
        pair_returns,
        decisions,
        roundtrip_cost_bps={"EURUSD": 1.8, "USDJPY": 1.8},
        cost_multiplier=1.0,
    )
    daily = result.daily_returns.set_index("timestamp")

    assert daily.loc[pd.Timestamp("2020-01-31T00:00:00Z"), "gross_return"] == 0
    assert daily.loc[pd.Timestamp("2020-02-03T00:00:00Z"), "gross_return"] > 0
    assert daily["cost"].sum() > 0


def test_validate_bid_ask_bars_requires_timestamp_and_executable_ohlc() -> None:
    from athena_research.forex_edge.models import ReasonCode
    from athena_research.forex_edge.quality import validate_bid_ask_bars

    frame = pd.DataFrame(
        {
            "bid_open": [1.0],
            "bid_high": [1.0],
            "bid_low": [1.0],
            "bid_close": [1.0],
            "ask_open": [1.1],
            "ask_high": [1.1],
            "ask_low": [1.1],
            "ask_close": [1.1],
        }
    )

    result = validate_bid_ask_bars(frame)

    assert result.eligible is False
    assert result.reason_codes == (ReasonCode.MIDPOINT_ONLY,)
    assert result.details["missing_columns"] == ["timestamp"]


def test_validate_bid_ask_bars_rejects_nonfinite_and_nonpositive_prices() -> None:
    from athena_research.forex_edge.models import ReasonCode
    from athena_research.forex_edge.quality import validate_bid_ask_bars

    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2021-01-04 16:00Z"], utc=True),
            "bid_open": [1.0],
            "bid_high": [math.inf],
            "bid_low": [1.0],
            "bid_close": [0.0],
            "ask_open": [1.1],
            "ask_high": [1.2],
            "ask_low": [1.1],
            "ask_close": [1.2],
        }
    )

    result = validate_bid_ask_bars(frame)

    assert result.eligible is False
    assert ReasonCode.NONPOSITIVE_PRICE in result.reason_codes


def test_validate_bid_ask_bars_rejects_malformed_timestamp() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.quality import validate_bid_ask_bars

    frame = pd.DataFrame(
        {
            "timestamp": ["not-a-timestamp"],
            "bid_open": [1.0],
            "bid_high": [1.0],
            "bid_low": [1.0],
            "bid_close": [1.0],
            "ask_open": [1.1],
            "ask_high": [1.2],
            "ask_low": [1.1],
            "ask_close": [1.2],
        }
    )

    with pytest.raises(InvalidResearchInputError, match="timestamp"):
        validate_bid_ask_bars(frame)


def test_validate_bid_ask_bars_rejects_internal_ohlc_inconsistency_and_crossed_quotes() -> None:
    from athena_research.forex_edge.models import ReasonCode
    from athena_research.forex_edge.quality import validate_bid_ask_bars

    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2021-01-04 16:00Z", "2021-01-04 16:05Z"],
                utc=True,
            ),
            "bid_open": [1.2, 1.3],
            "bid_high": [1.1, 1.3],
            "bid_low": [1.05, 1.29],
            "bid_close": [1.15, 1.31],
            "ask_open": [1.1, 1.4],
            "ask_high": [1.2, 1.39],
            "ask_low": [1.08, 1.28],
            "ask_close": [1.16, 1.35],
        }
    )

    result = validate_bid_ask_bars(frame)

    assert result.eligible is False
    assert ReasonCode.CROSSED_QUOTE in result.reason_codes


def test_validate_bid_ask_bars_treats_identical_duplicates_as_eligible_but_rejects_conflicts() -> None:
    from athena_research.forex_edge.models import ReasonCode
    from athena_research.forex_edge.quality import validate_bid_ask_bars

    identical = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2021-01-04 16:00Z", "2021-01-04 16:00Z"],
                utc=True,
            ),
            "bid_open": [1.2, 1.2],
            "bid_high": [1.2001, 1.2001],
            "bid_low": [1.1999, 1.1999],
            "bid_close": [1.2, 1.2],
            "ask_open": [1.2002, 1.2002],
            "ask_high": [1.2004, 1.2004],
            "ask_low": [1.2002, 1.2002],
            "ask_close": [1.2003, 1.2003],
        }
    )

    identical_result = validate_bid_ask_bars(identical)
    assert identical_result.eligible is True
    assert identical_result.reason_codes == ()

    conflicting = identical.copy()
    conflicting.loc[1, "ask_close"] = 1.2005
    conflicting_result = validate_bid_ask_bars(conflicting)

    assert conflicting_result.eligible is False
    assert ReasonCode.DUPLICATE_CONFLICT in conflicting_result.reason_codes
    json.dumps(conflicting_result.to_dict(), allow_nan=False)


def test_study_result_rejects_production_eligibility() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    with pytest.raises(
        InvalidResearchInputError,
        match="production_eligible",
    ):
        _study_result(production_eligible=True, metrics={})

    result = _study_result(production_eligible=False, metrics={})
    assert result.to_dict()["production_eligible"] is False


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_json_safe_serialization_rejects_nonfinite_floats(value: float) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    result = _study_result(production_eligible=False, metrics={"value": value})
    with pytest.raises(InvalidResearchInputError, match="finite"):
        result.to_dict()


def test_json_safe_serialization_rejects_unsupported_types() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    result = _study_result(
        production_eligible=False,
        metrics={"value": object()},
    )
    with pytest.raises(InvalidResearchInputError, match="unsupported"):
        result.to_dict()


@pytest.mark.parametrize(
    "mapping",
    [
        {1: "integer", "1": "string"},
        {True: "boolean", "True": "string"},
        {False: "boolean", "False": "string"},
    ],
)
def test_json_safe_serialization_rejects_nonstring_mapping_keys(
    mapping: dict[object, str],
) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    result = _study_result(
        production_eligible=False,
        metrics={"mapping": mapping},
    )

    with pytest.raises(InvalidResearchInputError, match="mapping keys"):
        result.to_dict()


def test_json_safe_serialization_sorts_sets_deterministically() -> None:
    first = _study_result(
        production_eligible=False,
        metrics={"values": {"JPY", "EUR", "USD"}},
    )
    second = _study_result(
        production_eligible=False,
        metrics={"values": frozenset(("USD", "JPY", "EUR"))},
    )

    assert first.to_dict() == second.to_dict()
    assert first.to_dict()["metrics"]["values"] == ["EUR", "JPY", "USD"]


def test_universe_helpers_fail_closed_and_preserve_quote_orientation() -> None:
    from athena_research.forex_edge.universe import (
        currency_usd_price,
        pair_weight_for_currency,
    )

    assert pair_weight_for_currency("EUR", 0.25) == pytest.approx(0.25)
    assert pair_weight_for_currency("JPY", 0.25) == pytest.approx(-0.25)
    with pytest.raises(ValueError, match="positive"):
        currency_usd_price("EUR", 0)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_universe_helpers_reject_nonfinite_prices_and_weights(
    value: float,
) -> None:
    from athena_research.forex_edge.universe import (
        currency_usd_price,
        pair_weight_for_currency,
    )

    for currency in ("EUR", "JPY"):
        with pytest.raises(ValueError, match="finite"):
            currency_usd_price(currency, value)
        with pytest.raises(ValueError, match="finite"):
            pair_weight_for_currency(currency, value)


def test_store_root_override_and_empirical_quality_limits_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_research.forex_edge.config import (
        default_store_root,
        load_config,
        validate_empirical_config,
    )
    from athena_research.forex_edge.models import BlockedDataError

    monkeypatch.setenv("ATHENA_FOREX_EDGE_ROOT", "C:/research/forex-edge")
    assert default_store_root() == Path("C:/research/forex-edge")

    cfg = load_config(ROOT / "configs" / "forex_edge_research.yaml")
    with pytest.raises(BlockedDataError, match="UNREGISTERED_QUALITY_LIMIT"):
        validate_empirical_config(cfg, "portfolio")

    cfg["quality"].update(
        spot_staleness_days=3,
        rate_staleness_days=7,
        reer_staleness_days=45,
        m5_max_spread_bps=10,
    )
    validate_empirical_config(cfg, "portfolio")
    validate_empirical_config(cfg, "fixing")
    validate_empirical_config(cfg, "quality-report")
    validate_empirical_config(cfg, "both")


@pytest.mark.parametrize(
    ("section", "value"),
    [
        ("universe", []),
        ("universe", None),
        ("portfolio", []),
        ("portfolio", None),
    ],
)
def test_load_config_rejects_nonmapping_nested_sections(
    tmp_path: Path,
    section: str,
    value: object,
) -> None:
    from athena_research.forex_edge.config import load_config
    from athena_research.forex_edge.models import InvalidResearchInputError

    raw = yaml.safe_load(
        (ROOT / "configs" / "forex_edge_research.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw[section] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(InvalidResearchInputError):
        load_config(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_currencies", "12"),
        ("min_currencies", 12.5),
        ("min_currencies", True),
        ("top_n", "4"),
        ("top_n", 4.0),
        ("top_n", False),
    ],
)
def test_load_config_requires_exact_integer_portfolio_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    from athena_research.forex_edge.config import load_config
    from athena_research.forex_edge.models import InvalidResearchInputError

    raw = yaml.safe_load(
        (ROOT / "configs" / "forex_edge_research.yaml").read_text(
            encoding="utf-8"
        )
    )
    malformed = deepcopy(raw)
    malformed["portfolio"][field] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(malformed), encoding="utf-8")

    with pytest.raises(InvalidResearchInputError):
        load_config(path)


def test_store_versions_data_without_overwrite(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    raw = store.write_raw("FRED", "spot_EUR", b'{"value":1.1}')
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2020-01-02", "2020-01-03"],
                utc=True,
            ),
            "value": [1.1, 1.2],
        }
    )
    first = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )
    same = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )

    assert first == same
    pd.testing.assert_frame_equal(
        store.load_normalized("spot", "EUR", first.version),
        frame,
    )

    changed = frame.copy()
    changed.loc[1, "value"] = 1.3
    second = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=changed,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )

    assert second.version != first.version
    pd.testing.assert_frame_equal(
        store.load_normalized("spot", "EUR", second.version),
        changed,
    )


def test_store_rejects_conflicting_existing_partition(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    path = (
        store.root
        / "normalized"
        / "spot"
        / "EUR"
        / "bad"
        / "2020"
        / "data.parquet"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")

    with pytest.raises(RuntimeError, match="immutable partition conflict"):
        store._write_partition(path, pd.DataFrame({"x": [1]}))


def test_store_raw_writes_are_idempotent_and_immutable(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    first = store.write_raw("FRED", "spot_EUR", b"payload")
    same = store.write_raw("FRED", "spot_EUR", b"payload")
    assert first == same
    assert first.path.read_bytes() == b"payload"

    first.path.write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="immutable raw conflict"):
        store.write_raw("FRED", "spot_EUR", b"payload")


def test_store_rejects_missing_timestamp_and_nonfinite_hashing(
    tmp_path: Path,
) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.store import (
        ResearchStore,
        canonical_frame_hash,
    )

    store = ResearchStore(tmp_path)
    with pytest.raises(ValueError, match="timestamp"):
        store.write_normalized(
            dataset="spot",
            key="EUR",
            frame=pd.DataFrame({"value": [1.1]}),
            source="FRED",
            source_url="https://example.test/fred",
            raw_hashes=("raw",),
            metadata={"config_hash": "cfg"},
        )
    with pytest.raises(InvalidResearchInputError, match="finite"):
        canonical_frame_hash(pd.DataFrame({"value": [math.nan]}))


def test_store_rejects_conflicting_existing_manifest(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2020-01-02"], utc=True),
            "value": [1.1],
        }
    )
    manifest = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=("raw",),
        metadata={"config_hash": "cfg"},
    )
    path = (
        store.root
        / "manifests"
        / "spot"
        / "EUR"
        / f"{manifest.version}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_url"] = "https://conflict.test"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="immutable manifest conflict"):
        store.write_normalized(
            dataset="spot",
            key="EUR",
            frame=frame,
            source="FRED",
            source_url="https://example.test/fred",
            raw_hashes=("raw",),
            metadata={"config_hash": "cfg"},
        )


def test_store_empty_frame_and_run_directory(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    frame = pd.DataFrame(columns=["timestamp", "value"])
    manifest = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(),
        metadata={"config_hash": "cfg"},
    )

    loaded = store.load_normalized("spot", "EUR", manifest.version)
    assert loaded.empty
    assert list(loaded.columns) == ["timestamp", "value"]
    assert store.run_dir("run-1") == tmp_path / "runs" / "run-1"
    assert store.run_dir("run-1").is_dir()


@pytest.mark.parametrize(
    "timestamps",
    [
        [pd.Timestamp("2020-01-02", tz="UTC"), pd.NaT],
        [pd.NaT, pd.NaT],
    ],
    ids=["mixed-null", "all-null"],
)
def test_store_rejects_null_timestamps_without_dropping_rows(
    tmp_path: Path,
    timestamps: list[pd.Timestamp],
) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    frame = pd.DataFrame({"timestamp": timestamps, "value": [1.1, 1.2]})

    with pytest.raises(InvalidResearchInputError, match="missing"):
        store.write_normalized(
            dataset="spot",
            key="EUR",
            frame=frame,
            source="FRED",
            source_url="https://example.test/fred",
            raw_hashes=("raw",),
            metadata={"config_hash": "cfg"},
        )

    assert not list((tmp_path / "normalized").rglob("data.parquet"))


def test_store_manifest_is_deterministic_across_fresh_roots(
    tmp_path: Path,
) -> None:
    from athena_research.forex_edge.store import ResearchStore

    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2020-01-02"], utc=True),
            "value": [1.1],
        }
    )
    manifests = []
    payloads = []
    for root in (tmp_path / "first", tmp_path / "second"):
        store = ResearchStore(root)
        manifest = store.write_normalized(
            dataset="spot",
            key="EUR",
            frame=frame,
            source="FRED",
            source_url="https://example.test/fred",
            raw_hashes=("raw",),
            metadata={
                "config_hash": "cfg",
                "retrieved_at": "2020-01-03T00:00:00+00:00",
            },
        )
        manifests.append(manifest)
        payloads.append(
            (
                root
                / "manifests"
                / "spot"
                / "EUR"
                / f"{manifest.version}.json"
            ).read_bytes()
        )

    assert manifests[0] == manifests[1]
    assert manifests[0].retrieved_at == "2020-01-03T00:00:00+00:00"
    assert payloads[0] == payloads[1]


def test_store_manifest_uses_deterministic_retrieval_sentinel(
    tmp_path: Path,
) -> None:
    from athena_research.forex_edge.store import ResearchStore

    manifest = ResearchStore(tmp_path).write_normalized(
        dataset="spot",
        key="EUR",
        frame=pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2020-01-02"], utc=True),
                "value": [1.1],
            }
        ),
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=("raw",),
        metadata={"config_hash": "cfg"},
    )

    assert manifest.retrieved_at == ""


def test_canonical_frame_hash_normalizes_numpy_datetime64_ns() -> None:
    from athena_research.forex_edge.store import canonical_frame_hash

    numpy_frame = pd.DataFrame(
        {
            "timestamp": pd.Series(
                [np.datetime64("2020-01-02T03:04:05.000000000", "ns")],
                dtype=object,
            )
        }
    )
    pandas_frame = pd.DataFrame(
        {
            "timestamp": pd.Series(
                [pd.Timestamp("2020-01-02T03:04:05Z")],
                dtype=object,
            )
        }
    )

    assert canonical_frame_hash(numpy_frame) == canonical_frame_hash(
        pandas_frame
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "../escape",
        r"..\escape",
        "nested/name",
        r"nested\name",
        "C:/absolute",
        "C:\\absolute",
        "/absolute",
        "bad\x00name",
        "bad\nname",
    ],
)
def test_store_rejects_unsafe_path_segments(
    tmp_path: Path,
    value: str,
) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path / "root")
    frame = pd.DataFrame(columns=["timestamp", "value"])
    operations = (
        lambda: store.write_raw(value, "spot", b"payload"),
        lambda: store.write_raw("FRED", value, b"payload"),
        lambda: store.write_normalized(
            dataset=value,
            key="EUR",
            frame=frame,
            source="FRED",
            source_url="https://example.test",
            raw_hashes=(),
            metadata={"config_hash": "cfg"},
        ),
        lambda: store.write_normalized(
            dataset="spot",
            key=value,
            frame=frame,
            source="FRED",
            source_url="https://example.test",
            raw_hashes=(),
            metadata={"config_hash": "cfg"},
        ),
        lambda: store.load_normalized(value, "EUR", "version"),
        lambda: store.load_normalized("spot", value, "version"),
        lambda: store.load_normalized("spot", "EUR", value),
        lambda: store.run_dir(value),
    )

    for operation in operations:
        with pytest.raises(InvalidResearchInputError, match="path segment"):
            operation()

    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "root" / "raw").exists()
    assert not (tmp_path / "root" / "normalized").exists()
    assert not (tmp_path / "root" / "runs").exists()


def test_store_raw_publication_failure_leaves_no_partial_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import athena_research.forex_edge.store as store_module
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)

    def fail_publication(source: object, target: object) -> None:
        raise OSError("publication failed")

    monkeypatch.setattr(store_module.os, "link", fail_publication)

    with pytest.raises(OSError, match="publication failed"):
        store.write_raw("FRED", "spot_EUR", b"payload")

    raw_root = tmp_path / "raw"
    assert not list(raw_root.rglob("response.bin"))
    assert not list(raw_root.rglob("*.tmp"))


def test_store_raw_concurrent_identical_publication_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import athena_research.forex_edge.store as store_module
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    real_link = store_module.os.link

    def publish_then_report_exists(source: object, target: object) -> None:
        real_link(source, target)
        raise FileExistsError

    monkeypatch.setattr(
        store_module.os,
        "link",
        publish_then_report_exists,
    )

    artifact = store.write_raw("FRED", "spot_EUR", b"payload")

    assert artifact.path.read_bytes() == b"payload"
    assert not list(artifact.path.parent.glob("*.tmp"))
