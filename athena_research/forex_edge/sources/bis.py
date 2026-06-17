from __future__ import annotations

from io import BytesIO

import pandas as pd

from athena_research.forex_edge.models import ReasonCode
from athena_research.forex_edge.sources.common import HttpGet, requests_get


def build_bis_url(
    base_url: str,
    series_key: str,
    start: str,
    end: str = "",
) -> str:
    query = f"?startPeriod={start}&detail=full&format=csvfile"
    if end:
        query += f"&endPeriod={end}"
    return f"{base_url.rstrip('/')}/{series_key}/all{query}"


def parse_bis_reer_csv(
    content: bytes,
    *,
    currency: str,
    series_key: str,
) -> pd.DataFrame:
    frame = pd.read_csv(BytesIO(content))
    required = {"TIME_PERIOD", "OBS_VALUE", "UNIT_MEASURE"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"MISSING_SERIES:{sorted(missing)}")
    values = pd.to_numeric(frame["OBS_VALUE"], errors="coerce")
    if values.isna().any():
        raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
    raw_units = tuple(sorted(frame["UNIT_MEASURE"].dropna().astype(str).unique()))
    if raw_units not in {("IX",), ("882",)}:
        raise ValueError(ReasonCode.AMBIGUOUS_UNIT.value)
    conflicts = frame[frame.duplicated("TIME_PERIOD", keep=False)]
    if not conflicts.empty and any(
        len(group.drop_duplicates()) > 1
        for _, group in conflicts.groupby("TIME_PERIOD")
    ):
        raise ValueError("DUPLICATE_CONFLICT")
    frame = frame.drop_duplicates("TIME_PERIOD").reset_index(drop=True)
    values = pd.to_numeric(frame["OBS_VALUE"], errors="coerce")
    periods = pd.PeriodIndex(frame["TIME_PERIOD"].astype(str), freq="M")
    timestamp = periods.to_timestamp(how="end").tz_localize("UTC")
    available = (
        (periods + 1).to_timestamp(how="end").tz_localize("UTC").normalize()
        + pd.Timedelta(hours=23, minutes=59, seconds=59)
    )
    return pd.DataFrame(
        {
            "timestamp": timestamp,
            "available_time": available,
            "value": values.astype(float),
            "currency": currency,
            "series_id": series_key,
            "unit": "INDEX",
            "raw_unit": raw_units[0],
            "availability_verified": True,
            "revision_history_verified": False,
        }
    )


def fetch_bis_reer(
    base_url: str,
    series_key: str,
    *,
    start: str,
    end: str = "",
    http_get: HttpGet = requests_get,
) -> tuple[str, bytes]:
    url = build_bis_url(base_url, series_key, start, end)
    response = http_get(url, headers={"Accept": "text/csv"}, timeout=30.0)
    response.raise_for_status()
    return url, response.content
