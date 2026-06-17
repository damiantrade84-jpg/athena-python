from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd


def resolve_fixing_utc(
    date: pd.Timestamp,
    *,
    timezone_name: str,
    local_time: str,
) -> pd.Timestamp:
    day = pd.Timestamp(date).date()
    hour, minute = (int(part) for part in local_time.split(":"))
    local = pd.Timestamp(
        datetime.combine(day, time(hour, minute)),
        tz=ZoneInfo(timezone_name),
    )
    return local.tz_convert("UTC")
