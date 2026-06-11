"""Frozen availability-time rules for PTIS ingestion (ASE v2.1 §1.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc

_MS_PER_SECOND = 1000
_MS_PER_MINUTE = 60 * _MS_PER_SECOND


class AvailabilityRuleId(str, Enum):
    EODHD_BAR = "eodhd_bar"
    DUKASCOPY_VOLUME = "dukascopy_volume"
    BYBIT_EVENT = "bybit_event"
    CFTC_COT = "cftc_cot"
    FRED_DAILY = "fred_daily"
    FRED_POLICY = "fred_policy"


@dataclass(frozen=True)
class AvailabilityRule:
    rule_id: AvailabilityRuleId
    description: str
    verified: bool = True


AVAILABILITY_RULES: dict[AvailabilityRuleId, AvailabilityRule] = {
    AvailabilityRuleId.EODHD_BAR: AvailabilityRule(
        AvailabilityRuleId.EODHD_BAR,
        "EODHD H1/H4/D1 bars: bar close + 90 s ingestion buffer",
        verified=True,
    ),
    AvailabilityRuleId.DUKASCOPY_VOLUME: AvailabilityRule(
        AvailabilityRuleId.DUKASCOPY_VOLUME,
        "Dukascopy tick volume (H1 agg): bar close + 5 min",
        verified=True,
    ),
    AvailabilityRuleId.BYBIT_EVENT: AvailabilityRule(
        AvailabilityRuleId.BYBIT_EVENT,
        "Bybit funding/OI/volume: event time + 30 s",
        verified=True,
    ),
    AvailabilityRuleId.CFTC_COT: AvailabilityRule(
        AvailabilityRuleId.CFTC_COT,
        "CFTC COT: published Friday 15:30 ET → available following Monday 00:00 UTC",
        verified=True,
    ),
    AvailabilityRuleId.FRED_DAILY: AvailabilityRule(
        AvailabilityRuleId.FRED_DAILY,
        "FRED daily rate series: observation date + 1 BD, 12:00 ET (default when no vintage)",
        verified=False,
    ),
    AvailabilityRuleId.FRED_POLICY: AvailabilityRule(
        AvailabilityRuleId.FRED_POLICY,
        "FRED policy rates: release calendar where known, else obs + 1 BD",
        verified=False,
    ),
}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * _MS_PER_SECOND)


def _from_ms(ms: int) -> datetime:
    # Epoch arithmetic avoids Windows fromtimestamp() failures before 1970.
    return datetime(1970, 1, 1, tzinfo=_UTC) + timedelta(milliseconds=int(ms))


def _next_business_day(d: datetime) -> datetime:
    """Next calendar day, skipping Sat/Sun (US equity-calendar proxy)."""
    cur = d
    while True:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            return cur


def compute_available_time_ms(
    rule_id: AvailabilityRuleId,
    *,
    value_time_ms: int,
    bar_close_ms: int | None = None,
    realtime_start_ms: int | None = None,
) -> int:
    """Compute available_time (epoch ms) for a row being ingested."""
    if rule_id == AvailabilityRuleId.EODHD_BAR:
        close_ms = bar_close_ms if bar_close_ms is not None else value_time_ms
        return close_ms + 90 * _MS_PER_SECOND

    if rule_id == AvailabilityRuleId.DUKASCOPY_VOLUME:
        close_ms = bar_close_ms if bar_close_ms is not None else value_time_ms
        return close_ms + 5 * _MS_PER_MINUTE

    if rule_id == AvailabilityRuleId.BYBIT_EVENT:
        return value_time_ms + 30 * _MS_PER_SECOND

    if rule_id == AvailabilityRuleId.CFTC_COT:
        return cot_available_time_ms(value_time_ms)

    if rule_id in (AvailabilityRuleId.FRED_DAILY, AvailabilityRuleId.FRED_POLICY):
        if realtime_start_ms is not None:
            return realtime_start_ms
        return fred_default_available_time_ms(value_time_ms)

    raise ValueError(f"unknown availability rule: {rule_id}")


def cot_publication_friday_ms(report_week_tuesday_ms: int) -> int:
    """COT report week (Tuesday anchor) publishes that Friday 15:30 ET."""
    tuesday = _from_ms(report_week_tuesday_ms)
    days_until_friday = (4 - tuesday.weekday()) % 7
    if days_until_friday == 0 and tuesday.hour > 0:
        days_until_friday = 7
    friday = tuesday.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=days_until_friday
    )
    pub_et = friday.replace(tzinfo=_ET).replace(hour=15, minute=30, second=0, microsecond=0)
    return _ms(pub_et.astimezone(_UTC))


def cot_available_time_ms(report_week_tuesday_ms: int) -> int:
    """Conservative COT lag: Monday 00:00 UTC after publication Friday."""
    pub_ms = cot_publication_friday_ms(report_week_tuesday_ms)
    pub_utc = _from_ms(pub_ms)
    days_to_monday = (7 - pub_utc.weekday()) % 7
    if days_to_monday == 0:
        days_to_monday = 7
    monday = pub_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=days_to_monday
    )
    return _ms(monday)


def fred_default_available_time_ms(observation_date_ms: int) -> int:
    """Observation calendar date (UTC) + 1 business day at 12:00 ET."""
    obs = _from_ms(observation_date_ms)
    obs_day = datetime(obs.year, obs.month, obs.day, tzinfo=_UTC)
    avail_day = _next_business_day(obs_day)
    avail_et = datetime(
        avail_day.year,
        avail_day.month,
        avail_day.day,
        12,
        0,
        0,
        tzinfo=_ET,
    )
    return _ms(avail_et.astimezone(_UTC))


def rule_for_source(source: str) -> AvailabilityRuleId:
    """Map ingest source prefix to a frozen availability rule."""
    src = source.strip().upper()
    if src.startswith("EODHD"):
        return AvailabilityRuleId.EODHD_BAR
    if src.startswith("DUKASCOPY"):
        return AvailabilityRuleId.DUKASCOPY_VOLUME
    if src.startswith("BYBIT"):
        return AvailabilityRuleId.BYBIT_EVENT
    if src.startswith("CFTC"):
        return AvailabilityRuleId.CFTC_COT
    if src.startswith("FRED_POLICY"):
        return AvailabilityRuleId.FRED_POLICY
    if src.startswith("FRED"):
        return AvailabilityRuleId.FRED_DAILY
    raise ValueError(f"no availability rule for source: {source}")


def series_metadata_for_audit(series_id: str, source: str) -> dict[str, Any]:
    """Metadata row for availability_audit.md (Phase 0 exit artifact)."""
    rule_id = rule_for_source(source)
    rule = AVAILABILITY_RULES[rule_id]
    return {
        "series_id": series_id,
        "source": source,
        "rule_id": rule_id.value,
        "description": rule.description,
        "verified": rule.verified,
        "flag": "" if rule.verified else "UNVERIFIED_LAG",
    }
