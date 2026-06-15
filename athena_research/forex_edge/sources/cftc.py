from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pandas as pd

from athena_research.forex_edge.sources.common import HttpGet, requests_get


MARKET = "Market and Exchange Names"
REPORT_DATE = "As of Date in Form YYYY-MM-DD"
LONG = "Noncommercial Positions-Long (All)"
SHORT = "Noncommercial Positions-Short (All)"


def following_monday(report_date: pd.Timestamp) -> pd.Timestamp:
    report = pd.Timestamp(report_date)
    report = (
        report.tz_localize("UTC")
        if report.tzinfo is None
        else report.tz_convert("UTC")
    )
    days = (7 - report.weekday()) % 7 or 7
    return report.normalize() + pd.Timedelta(days=days)


def normalize_cftc_frame(
    raw: pd.DataFrame,
    mappings: dict[str, str],
) -> pd.DataFrame:
    required = {MARKET, REPORT_DATE, LONG, SHORT}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"MISSING_SERIES:{sorted(missing)}")
    rows: list[dict[str, object]] = []
    market_text = raw[MARKET].astype(str).str.upper()
    for currency, prefix in mappings.items():
        matches = raw[market_text.str.startswith(prefix.upper())]
        for _, row in matches.iterrows():
            report = pd.Timestamp(row[REPORT_DATE], tz="UTC")
            long_value = float(row[LONG])
            short_value = float(row[SHORT])
            rows.append(
                {
                    "timestamp": report,
                    "available_time": following_monday(report),
                    "currency": currency,
                    "net_noncommercial": long_value - short_value,
                    "long_noncommercial": long_value,
                    "short_noncommercial": short_value,
                    "availability_verified": True,
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    conflicts = frame[frame.duplicated(["currency", "timestamp"], keep=False)]
    if not conflicts.empty and any(
        len(group.drop_duplicates()) > 1
        for _, group in conflicts.groupby(["currency", "timestamp"])
    ):
        raise ValueError("DUPLICATE_CONFLICT")
    return (
        frame.drop_duplicates(["currency", "timestamp"])
        .sort_values(["currency", "timestamp"])
        .reset_index(drop=True)
    )


def missing_cot_currencies(
    currencies: tuple[str, ...],
    mappings: dict[str, str],
) -> tuple[str, ...]:
    return tuple(currency for currency in currencies if currency not in mappings)


def parse_cftc_zip(content: bytes) -> pd.DataFrame:
    with ZipFile(BytesIO(content)) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if name.lower().endswith((".csv", ".txt"))
        )
        if len(names) != 1:
            raise ValueError("CFTC archive must contain exactly one data file")
        with archive.open(names[0]) as handle:
            return pd.read_csv(handle, low_memory=False)


def fetch_cftc_year(
    url_template: str,
    year: int,
    *,
    http_get: HttpGet = requests_get,
) -> tuple[str, bytes]:
    url = url_template.format(year=int(year))
    response = http_get(url, timeout=60.0)
    response.raise_for_status()
    return url, response.content
