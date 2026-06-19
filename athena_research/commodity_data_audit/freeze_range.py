from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class RequestedRangeBoundary:
    start_inclusive: datetime
    end_exclusive: datetime
    start_input: str
    end_input: str
    end_boundary_semantics: str
    end_boundary_inclusive: datetime | None
    as_of: datetime | None


def parse_utc_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty datetime")
    if len(text) == 10:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_requested_range(
    *,
    start_date: str,
    end_date: str = "",
    end_time: str = "",
    as_of: datetime | None = None,
) -> RequestedRangeBoundary:
    start_inclusive = parse_utc_datetime(start_date)
    acquisition_as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if end_time.strip():
        end_exclusive = parse_utc_datetime(end_time)
        return RequestedRangeBoundary(
            start_inclusive=start_inclusive,
            end_exclusive=end_exclusive,
            start_input=start_date,
            end_input=end_time.strip(),
            end_boundary_semantics="exclusive_utc_instant",
            end_boundary_inclusive=None,
            as_of=acquisition_as_of,
        )

    if end_date.strip():
        day = parse_utc_datetime(end_date)
        end_boundary_inclusive = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        end_exclusive = day + timedelta(days=1)
        return RequestedRangeBoundary(
            start_inclusive=start_inclusive,
            end_exclusive=end_exclusive,
            start_input=start_date,
            end_input=end_date.strip(),
            end_boundary_semantics="inclusive_end_of_utc_day",
            end_boundary_inclusive=end_boundary_inclusive,
            as_of=acquisition_as_of,
        )

    return RequestedRangeBoundary(
        start_inclusive=start_inclusive,
        end_exclusive=acquisition_as_of,
        start_input=start_date,
        end_input=acquisition_as_of.isoformat(),
        end_boundary_semantics="acquisition_as_of_exclusive",
        end_boundary_inclusive=None,
        as_of=acquisition_as_of,
    )


def requested_range_to_manifest_dict(boundary: RequestedRangeBoundary) -> dict[str, str | None]:
    payload: dict[str, str | None] = {
        "start": boundary.start_inclusive.isoformat(),
        "start_semantics": "inclusive_utc_instant",
        "end_input": boundary.end_input,
        "end_exclusive": boundary.end_exclusive.isoformat(),
        "end_semantics": boundary.end_boundary_semantics,
    }
    if boundary.end_boundary_inclusive is not None:
        payload["end_boundary_inclusive"] = boundary.end_boundary_inclusive.isoformat()
    if boundary.as_of is not None:
        payload["as_of"] = boundary.as_of.isoformat()
    return payload
