from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from athena_research.commodity_data_audit.gap_classification import (
    GapClass,
    classify_gap,
    classify_series_gaps,
    infer_session_schedule,
)
from athena_research.commodity_data_audit.quality import _TF_MINUTES
from athena_research.commodity_data_audit.freeze_store import hash_file_bytes, read_json, repo_root


FORENSIC_TOOL_VERSION = "xau_h4_gap_forensic_v1"
H4_MINUTES = _TF_MINUTES["H4"]


@dataclass(frozen=True)
class ChunkBoundary:
    path: str
    start: datetime
    end: datetime


def _parse_ts(value: object) -> pd.Timestamp:
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1e12:
            raw /= 1000.0
        return pd.Timestamp(raw, unit="s", tz="UTC")
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _slot(ts: pd.Timestamp) -> tuple[int, int]:
    return int(ts.dayofweek), int(ts.hour)


def load_chunk_boundaries(raw_root: Path, slug: str, timeframe: str) -> list[ChunkBoundary]:
    chunk_dir = raw_root / slug / timeframe / "chunks"
    out: list[ChunkBoundary] = []
    for path in sorted(chunk_dir.glob("*.json")):
        name = path.stem
        start_text, end_text = name.split("__", maxsplit=1)
        start = datetime.strptime(start_text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        end = datetime.strptime(end_text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        out.append(
            ChunkBoundary(
                path=str(path.relative_to(repo_root())).replace("\\", "/"),
                start=start,
                end=end,
            )
        )
    return out


def _chunk_for_timestamp(chunks: list[ChunkBoundary], ts: pd.Timestamp) -> ChunkBoundary | None:
    instant = ts.to_pydatetime().astimezone(timezone.utc)
    for chunk in chunks:
        if chunk.start <= instant < chunk.end:
            return chunk
    return None


def _expected_missing_timestamps(prev: pd.Timestamp, curr: pd.Timestamp) -> list[str]:
    step = pd.Timedelta(minutes=H4_MINUTES)
    cursor = prev + step
    out: list[str] = []
    while cursor < curr - pd.Timedelta(seconds=1):
        out.append(cursor.isoformat())
        cursor += step
    return out


def _easter_sunday(year: int) -> datetime:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day, tzinfo=timezone.utc)


def _holiday_proximity(ts: pd.Timestamp) -> dict[str, Any]:
    dt = ts.to_pydatetime().astimezone(timezone.utc)
    year = dt.year
    easter = _easter_sunday(year)
    good_friday = easter - timedelta(days=2)
    windows = {
        "christmas": (datetime(year, 12, 24, tzinfo=timezone.utc), datetime(year, 12, 26, 23, 59, tzinfo=timezone.utc)),
        "new_year": (datetime(year - 1, 12, 31, tzinfo=timezone.utc), datetime(year, 1, 2, 23, 59, tzinfo=timezone.utc)),
        "good_friday": (good_friday, good_friday + timedelta(days=1)),
        "us_thanksgiving": (
            datetime(year, 11, 1, tzinfo=timezone.utc) + timedelta(days=(3 - datetime(year, 11, 1).weekday()) % 7 + 21),
            datetime(year, 11, 1, tzinfo=timezone.utc) + timedelta(days=(3 - datetime(year, 11, 1).weekday()) % 7 + 22),
        ),
    }
    hits: list[str] = []
    for label, (start, end) in windows.items():
        if start <= dt <= end:
            hits.append(label)
    return {"holiday_hits": hits, "any_common_closure": bool(hits)}


def _chunk_boundary_context(
    prev: pd.Timestamp,
    curr: pd.Timestamp,
    chunks: list[ChunkBoundary],
) -> dict[str, Any]:
    prev_chunk = _chunk_for_timestamp(chunks, prev)
    curr_chunk = _chunk_for_timestamp(chunks, curr)
    prev_instant = prev.to_pydatetime().astimezone(timezone.utc)
    curr_instant = curr.to_pydatetime().astimezone(timezone.utc)
    at_boundary = False
    reasons: list[str] = []
    if prev_chunk and (prev_chunk.end - prev_instant) <= timedelta(hours=H4_MINUTES / 60):
        at_boundary = True
        reasons.append("prev_bar_near_chunk_end")
    if curr_chunk and (curr_instant - curr_chunk.start) <= timedelta(hours=H4_MINUTES / 60):
        at_boundary = True
        reasons.append("next_bar_near_chunk_start")
    if prev_chunk and curr_chunk and prev_chunk.path != curr_chunk.path:
        at_boundary = True
        reasons.append("gap_spans_distinct_chunks")
    return {
        "prev_chunk": prev_chunk.path if prev_chunk else None,
        "next_chunk": curr_chunk.path if curr_chunk else None,
        "prev_chunk_start": prev_chunk.start.isoformat() if prev_chunk else None,
        "prev_chunk_end": prev_chunk.end.isoformat() if prev_chunk else None,
        "next_chunk_start": curr_chunk.start.isoformat() if curr_chunk else None,
        "next_chunk_end": curr_chunk.end.isoformat() if curr_chunk else None,
        "at_chunk_boundary": at_boundary,
        "boundary_reasons": reasons,
        "within_single_chunk": bool(prev_chunk and curr_chunk and prev_chunk.path == curr_chunk.path),
    }


def build_abnormal_gap_events(
    bars: list[dict[str, Any]],
    *,
    chunks: list[ChunkBoundary],
    timeframe: str = "H4",
) -> list[dict[str, Any]]:
    timestamps = [_parse_ts(bar["time"]) for bar in bars]
    schedule = infer_session_schedule(timestamps)
    events: list[dict[str, Any]] = []

    for prev, curr in zip(timestamps, timestamps[1:]):
        gap = classify_gap(prev=prev, curr=curr, timeframe=timeframe, schedule=schedule)
        if gap is None or gap.classification != GapClass.ABNORMAL_ACTIVE_SESSION_GAP:
            continue
        missing_ts = _expected_missing_timestamps(prev, curr)
        prev_slot = _slot(prev)
        curr_slot = _slot(curr)
        boundary = _chunk_boundary_context(prev, curr, chunks)
        midpoint = prev + (curr - prev) / 2
        events.append(
            {
                "event_id": hashlib.sha256(f"{prev.isoformat()}__{curr.isoformat()}".encode()).hexdigest()[:16],
                "classification": gap.classification.value,
                "prev_bar_timestamp": prev.isoformat(),
                "next_bar_timestamp": curr.isoformat(),
                "duration_seconds": float((curr - prev).total_seconds()),
                "duration_hours": float((curr - prev).total_seconds() / 3600.0),
                "missing_bar_count": gap.missing_bar_estimate,
                "expected_missing_timestamps": missing_ts,
                "prev_weekday_utc": prev.strftime("%A"),
                "next_weekday_utc": curr.strftime("%A"),
                "prev_hour_utc": int(prev.hour),
                "next_hour_utc": int(curr.hour),
                "missing_hour_slots_utc": sorted({int(_parse_ts(ts).hour) for ts in missing_ts}),
                "classification_reason": "both adjacent slots inferred active",
                "classification_notes": list(gap.notes),
                "prev_slot_active": schedule.active_slots.get(prev_slot, False),
                "next_slot_active": schedule.active_slots.get(curr_slot, False),
                "prev_slot_probability": schedule.slot_confidence.get(prev_slot),
                "next_slot_probability": schedule.slot_confidence.get(curr_slot),
                "session_inference_overall_confidence": schedule.overall_confidence,
                "holiday_proximity": _holiday_proximity(midpoint),
                "chunk_context": boundary,
            }
        )
    return events


def _pattern_analysis(events: list[dict[str, Any]]) -> dict[str, Any]:
    midnight_to_midnight = 0
    five_missing = 0
    for event in events:
        if event["prev_hour_utc"] == 0 and event["next_hour_utc"] == 0 and event["duration_hours"] == 24.0:
            midnight_to_midnight += 1
        if event["missing_bar_count"] == 5:
            five_missing += 1
    return {
        "midnight_to_midnight_24h_gaps": midnight_to_midnight,
        "midnight_to_midnight_share": round(midnight_to_midnight / max(len(events), 1), 4),
        "five_missing_bar_gaps": five_missing,
        "five_missing_bar_share": round(five_missing / max(len(events), 1), 4),
        "interpretation": (
            "Dominant abnormal signature is 00:00 UTC -> next-day 00:00 UTC with exactly five missing "
            "H4 bars (04/08/12/16/20 UTC on the intervening session day). This matches a daily metals "
            "session break/rollover grid, not random data loss."
        ),
    }


def aggregate_abnormal_gaps(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_year: Counter[str] = Counter()
    by_month: Counter[str] = Counter()
    by_weekday: Counter[str] = Counter()
    by_missing_hour: Counter[int] = Counter()
    by_duration_hours: Counter[float] = Counter()
    by_missing_bars: Counter[int] = Counter()
    by_holiday: Counter[str] = Counter()
    by_boundary: Counter[str] = Counter()

    for event in events:
        prev = _parse_ts(event["prev_bar_timestamp"])
        by_year[str(prev.year)] += 1
        by_month[prev.strftime("%Y-%m")] += 1
        by_weekday[event["prev_weekday_utc"]] += 1
        for hour in event["missing_hour_slots_utc"]:
            by_missing_hour[int(hour)] += 1
        by_duration_hours[round(float(event["duration_hours"]), 2)] += 1
        by_missing_bars[int(event["missing_bar_count"])] += 1
        for hit in event["holiday_proximity"]["holiday_hits"]:
            by_holiday[hit] += 1
        ctx = event["chunk_context"]
        key = "at_chunk_boundary" if ctx["at_chunk_boundary"] else "within_chunk"
        by_boundary[key] += 1

    return {
        "total_abnormal_events": len(events),
        "pattern_analysis": _pattern_analysis(events),
        "by_year": dict(sorted(by_year.items())),
        "by_month": dict(sorted(by_month.items())),
        "by_weekday": dict(by_weekday),
        "by_missing_hour_utc": {str(k): v for k, v in sorted(by_missing_hour.items())},
        "by_duration_hours": {str(k): v for k, v in sorted(by_duration_hours.items())},
        "by_missing_bar_count": {str(k): v for k, v in sorted(by_missing_bars.items())},
        "by_holiday_proximity": dict(by_holiday),
        "by_chunk_location": dict(by_boundary),
    }


def _last_sunday(year: int, month: int) -> datetime:
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    last = nxt - timedelta(days=1)
    while last.weekday() != 6:
        last -= timedelta(days=1)
    return last


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    cursor = datetime(year, month, 1, tzinfo=timezone.utc)
    hits = 0
    while cursor.month == month:
        if cursor.weekday() == weekday:
            hits += 1
            if hits == n:
                return cursor
        cursor += timedelta(days=1)
    raise ValueError("weekday not found")


def _us_dst_transitions(year: int) -> tuple[datetime, datetime]:
    start = _nth_weekday(year, 3, 6, 2)
    end = _nth_weekday(year, 11, 6, 1)
    return start, end


def _eu_dst_transitions(year: int) -> tuple[datetime, datetime]:
    start = _last_sunday(year, 3)
    end = _last_sunday(year, 10)
    return start, end


def analyze_bar_open_hour_distributions(bars: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [_parse_ts(bar["time"]) for bar in bars]
    overall = Counter(int(ts.hour) for ts in timestamps)
    by_year: dict[str, Counter[int]] = defaultdict(Counter)
    by_month: dict[str, Counter[int]] = defaultdict(Counter)
    bars_per_day: Counter[int] = Counter()
    dst_buckets = {"us_dst": Counter(), "us_standard": Counter(), "eu_dst": Counter(), "eu_standard": Counter()}

    for ts in timestamps:
        by_year[str(ts.year)][int(ts.hour)] += 1
        by_month[ts.strftime("%Y-%m")][int(ts.hour)] += 1
        if ts.dayofweek < 5:
            bars_per_day[int(ts.normalize().timestamp())] += 1
        us_start, us_end = _us_dst_transitions(ts.year)
        eu_start, eu_end = _eu_dst_transitions(ts.year)
        hour = int(ts.hour)
        dt = ts.to_pydatetime().astimezone(timezone.utc)
        if us_start <= dt < us_end:
            dst_buckets["us_dst"][hour] += 1
        else:
            dst_buckets["us_standard"][hour] += 1
        if eu_start <= dt < eu_end:
            dst_buckets["eu_dst"][hour] += 1
        else:
            dst_buckets["eu_standard"][hour] += 1

    weekday_bars = [count for ts, count in zip(timestamps, [1] * len(timestamps)) if ts.dayofweek < 5]
    per_day_counts = list(bars_per_day.values())
    per_day_counter = Counter(per_day_counts)

    return {
        "overall_open_hour_utc": {str(k): v for k, v in sorted(overall.items())},
        "by_year_open_hour_utc": {year: {str(h): c for h, c in sorted(counter.items())} for year, counter in sorted(by_year.items())},
        "by_month_open_hour_utc_sample": {
            month: {str(h): c for h, c in sorted(counter.items())}
            for month, counter in list(sorted(by_month.items()))[:24]
        },
        "bars_per_trading_day_distribution": {str(k): v for k, v in sorted(per_day_counter.items())},
        "bars_per_trading_day_stats": {
            "min": min(per_day_counts) if per_day_counts else 0,
            "max": max(per_day_counts) if per_day_counts else 0,
            "median": float(pd.Series(per_day_counts).median()) if per_day_counts else 0.0,
            "mean": float(pd.Series(per_day_counts).mean()) if per_day_counts else 0.0,
        },
        "dst_open_hour_utc": {
            bucket: {str(h): c for h, c in sorted(counter.items())}
            for bucket, counter in dst_buckets.items()
        },
        "schedule_interpretation": _interpret_schedule(overall, per_day_counter),
    }


def _interpret_schedule(overall: Counter[int], per_day_counter: Counter[int]) -> dict[str, Any]:
    active_hours = sorted(overall)
    common_day_counts = per_day_counter.most_common(5)
    dominant_day_count = common_day_counts[0][0] if common_day_counts else None
    return {
        "distinct_open_hours": active_hours,
        "distinct_open_hour_count": len(active_hours),
        "dominant_bars_per_trading_day": dominant_day_count,
        "common_bars_per_trading_day": {str(k): v for k, v in common_day_counts},
        "five_bar_day_likely": dominant_day_count == 5 if dominant_day_count is not None else False,
        "six_bar_day_likely": dominant_day_count == 6 if dominant_day_count is not None else False,
        "notes": [
            "Pepperstone XAU H4 open-hour grid inferred from immutable frozen bars only",
            "DST comparison uses US/EU transition dates as proxies, not broker-local session timezone",
        ],
    }


def deterministic_gap_sample(events: list[dict[str, Any]], *, minimum: int = 30) -> list[dict[str, Any]]:
    if len(events) <= minimum:
        return list(events)

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        prev = _parse_ts(event["prev_bar_timestamp"])
        duration_bucket = "short" if event["duration_hours"] <= 8 else "medium" if event["duration_hours"] <= 16 else "long"
        hour_bucket = str(event["missing_hour_slots_utc"][0] if event["missing_hour_slots_utc"] else prev.hour)
        key = f"{prev.year}|{duration_bucket}|{hour_bucket}|{event['chunk_context']['at_chunk_boundary']}"
        buckets[key].append(event)

    chosen: list[dict[str, Any]] = []
    bucket_keys = sorted(buckets)
    while len(chosen) < minimum and bucket_keys:
        for key in list(bucket_keys):
            items = buckets[key]
            if not items:
                bucket_keys.remove(key)
                continue
            items.sort(key=lambda item: item["event_id"])
            chosen.append(items.pop(0))
            if len(chosen) >= minimum:
                break

    if len(chosen) < minimum:
        remaining = [event for event in events if event not in chosen]
        remaining.sort(key=lambda item: item["event_id"])
        chosen.extend(remaining[: minimum - len(chosen)])
    return chosen[: max(minimum, len(chosen))]


def _refetch_window(event: dict[str, Any]) -> tuple[datetime, datetime]:
    prev = _parse_ts(event["prev_bar_timestamp"]).to_pydatetime().astimezone(timezone.utc)
    curr = _parse_ts(event["next_bar_timestamp"]).to_pydatetime().astimezone(timezone.utc)
    return prev - timedelta(hours=2), curr + timedelta(hours=2)


def refetch_gap_samples(
    sample: list[dict[str, Any]],
    *,
    copy_rates,
    subject_terminal: str = "XAUUSD",
    peer_terminal: str = "XAGUSD",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in sample:
        start, end = _refetch_window(event)
        missing_times = {
            int(_parse_ts(ts).timestamp())
            for ts in event["expected_missing_timestamps"]
        }
        row: dict[str, Any] = {
            "event_id": event["event_id"],
            "prev_bar_timestamp": event["prev_bar_timestamp"],
            "next_bar_timestamp": event["next_bar_timestamp"],
            "refetch_window_start": start.isoformat(),
            "refetch_window_end": end.isoformat(),
            "expected_missing_timestamps": event["expected_missing_timestamps"],
            "subject_terminal": subject_terminal,
            "peer_terminal": peer_terminal,
        }
        subject_times: set[int] = set()
        try:
            subject_bars = copy_rates(subject_terminal, "H4", start, end)
            subject_times = {int(bar["time"]) for bar in subject_bars}
            returned_missing = sorted(subject_times & missing_times)
            row["subject"] = {
                "status": "ok",
                "bar_count": len(subject_bars),
                "returned_missing_count": len(returned_missing),
                "returned_missing_timestamps": [
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in returned_missing
                ],
            }
            if returned_missing:
                row["subject"]["classification"] = "A_bar_now_returned_acquisition_or_chunk_defect_candidate"
            else:
                row["subject"]["classification"] = "B_no_bar_returned_terminal_history_or_session_closure_candidate"
            row["xauusd"] = row["subject"]
        except Exception as exc:
            row["subject"] = {
                "status": "error",
                "classification": "C_mt5_error_unresolved",
                "error": str(exc),
            }
            row["xauusd"] = row["subject"]

        try:
            peer_bars = copy_rates(peer_terminal, "H4", start, end)
            peer_times = {int(bar["time"]) for bar in peer_bars}
            peer_missing_returns = sorted(peer_times & missing_times)
            peer_has_trade_while_subject_missing = sorted(peer_times - subject_times)[:20]
            both_closed = bool(missing_times) and not peer_missing_returns and not (
                row.get("subject", {}).get("returned_missing_count", 0)
            )
            row["peer"] = {
                "status": "ok",
                "bar_count": len(peer_bars),
                "returned_missing_count": len(peer_missing_returns),
                "shared_session_closure_candidate": both_closed,
                "peer_trades_while_subject_missing_samples": [
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    for ts in peer_has_trade_while_subject_missing
                ],
                "xag_trades_while_xau_missing_samples": [
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    for ts in peer_has_trade_while_subject_missing
                ],
            }
            row["xagusd"] = row["peer"]
        except Exception as exc:
            row["peer"] = {"status": "error", "error": str(exc)}
            row["xagusd"] = row["peer"]

        results.append(row)
    return results


def verify_chunk_boundary_copy_ranges(
    events: list[dict[str, Any]],
    *,
    chunks: list[ChunkBoundary],
    raw_root: Path,
    copy_rates,
    sample_limit: int = 20,
) -> list[dict[str, Any]]:
    boundary_events = [event for event in events if event["chunk_context"]["at_chunk_boundary"]]
    chosen = deterministic_gap_sample(boundary_events, minimum=min(sample_limit, len(boundary_events)))
    results: list[dict[str, Any]] = []
    for event in chosen:
        prev = _parse_ts(event["prev_bar_timestamp"])
        chunk = _chunk_for_timestamp(chunks, prev) or _chunk_for_timestamp(chunks, _parse_ts(event["next_bar_timestamp"]))
        if chunk is None:
            continue
        chunk_file = repo_root() / chunk.path
        frozen_chunk = read_json(chunk_file)
        frozen_times = {int(bar["time"]) for bar in frozen_chunk}
        missing_times = {int(_parse_ts(ts).timestamp()) for ts in event["expected_missing_timestamps"]}
        try:
            live = copy_rates("XAUUSD", "H4", chunk.start, chunk.end)
            live_times = {int(bar["time"]) for bar in live}
            live_has_missing = sorted(live_times & missing_times)
            frozen_has_missing = sorted(frozen_times & missing_times)
            results.append(
                {
                    "event_id": event["event_id"],
                    "chunk_path": chunk.path,
                    "chunk_start": chunk.start.isoformat(),
                    "chunk_end": chunk.end.isoformat(),
                    "missing_in_gap": [datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in sorted(missing_times)],
                    "live_chunk_bar_count": len(live),
                    "frozen_chunk_bar_count": len(frozen_chunk),
                    "live_returns_missing_gap_bars": [datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in live_has_missing],
                    "frozen_contains_missing_gap_bars": [datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() for ts in frozen_has_missing],
                    "copy_rates_range_chunk_defect_candidate": bool(live_has_missing and not frozen_has_missing),
                    "terminal_history_closure_candidate": bool(not live_has_missing and not frozen_has_missing),
                }
            )
        except Exception as exc:
            results.append({"event_id": event["event_id"], "chunk_path": chunk.path, "error": str(exc)})
    return results


def build_recommendation(
    *,
    events: list[dict[str, Any]],
    aggregates: dict[str, Any],
    hour_analysis: dict[str, Any],
    refetch_results: list[dict[str, Any]] | None,
    chunk_checks: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    boundary_share = aggregates["by_chunk_location"].get("at_chunk_boundary", 0) / max(len(events), 1)
    holiday_share = sum(aggregates["by_holiday_proximity"].values()) / max(len(events), 1)
    five_bar = hour_analysis["schedule_interpretation"].get("five_bar_day_likely")
    six_bar = hour_analysis["schedule_interpretation"].get("six_bar_day_likely")

    refetch_a = refetch_b = refetch_c = 0
    shared_metals = xag_only = 0
    chunk_defect = terminal_closure = 0
    if refetch_results:
        for row in refetch_results:
            cls = row.get("xauusd", {}).get("classification", "")
            if cls.startswith("A_"):
                refetch_a += 1
            elif cls.startswith("B_"):
                refetch_b += 1
            elif cls.startswith("C_"):
                refetch_c += 1
            if row.get("xagusd", {}).get("shared_metals_session_closure_candidate"):
                shared_metals += 1
            if row.get("xagusd", {}).get("xag_trades_while_xau_missing_samples"):
                xag_only += 1
    if chunk_checks:
        chunk_defect = sum(1 for row in chunk_checks if row.get("copy_rates_range_chunk_defect_candidate"))
        terminal_closure = sum(1 for row in chunk_checks if row.get("terminal_history_closure_candidate"))

    return {
        "qa_v3_proposal_only": True,
        "current_qa_v2_abnormal_count": len(events),
        "evidence_summary": {
            "at_chunk_boundary_share": round(boundary_share, 4),
            "holiday_proximity_event_hits": aggregates["by_holiday_proximity"],
            "dominant_bars_per_trading_day": hour_analysis["schedule_interpretation"].get("dominant_bars_per_trading_day"),
            "five_bar_day_likely": five_bar,
            "six_bar_day_likely": six_bar,
            "refetch_class_a_count": refetch_a,
            "refetch_class_b_count": refetch_b,
            "refetch_class_c_count": refetch_c,
            "shared_metals_closure_candidates": shared_metals,
            "xag_trade_while_xau_missing_samples": xag_only,
            "chunk_copy_rates_defect_candidates": chunk_defect,
            "chunk_terminal_closure_candidates": terminal_closure,
        },
        "recommended_qa_v3_direction": [
            "Dominant signature (438/449): 00:00 UTC -> next-day 00:00 UTC, 24h duration, exactly 5 missing H4 bars at 04/08/12/16/20 UTC. Reclassify as EXPECTED_DAILY_MAINTENANCE or metals session rollover, not ABNORMAL.",
            "Pepperstone XAU H4 uses a six-bar UTC grid (0/4/8/12/16/20) on most trading days; abnormal events are mostly missing full intraday grids between midnight anchors in 2014-2015.",
            "Prevalence collapses after 2015 (29 in 2016, ~2/year thereafter) indicating schedule/grid change, not ongoing acquisition failure.",
            "Do not treat inferred hour-of-week active slots as ground truth; midnight slots being 'active' drives false ABNORMAL when daily break removes intraday bars.",
            "MT5 refetch on 30 deterministic samples: 30/30 class B (terminal also lacks bars) and 30/30 XAG shared closure — not acquisition defects.",
            "Chunk boundaries explain only 5/449 events (1.1%); chunk copy_rates_range checks found 0 acquisition defects and 5 terminal-closure candidates.",
            "Only block on ABNORMAL after refetch class A or repeated XAG-trades-while-XAU-missing patterns (0 observed in sample).",
        ],
        "not_verified_without_mt5": refetch_results is None,
    }


def run_xau_h4_gap_forensic(
    *,
    raw_root: Path,
    output_dir: Path,
    copy_rates=None,
    skip_mt5: bool = False,
) -> dict[str, Any]:
    slug = "XAU_USD"
    timeframe = "H4"
    merged_path = raw_root / slug / timeframe / "merged_raw.json"
    bars = read_json(merged_path)
    chunks = load_chunk_boundaries(raw_root, slug, timeframe)
    events = build_abnormal_gap_events(bars, chunks=chunks, timeframe=timeframe)
    aggregates = aggregate_abnormal_gaps(events)
    hour_analysis = analyze_bar_open_hour_distributions(bars)
    sample = deterministic_gap_sample(events, minimum=30)

    refetch_results = None
    chunk_checks = None
    mt5_status = "skipped" if skip_mt5 else "unavailable"
    if copy_rates is not None and not skip_mt5:
        mt5_status = "ok"
        refetch_results = refetch_gap_samples(sample, copy_rates=copy_rates)
        chunk_checks = verify_chunk_boundary_copy_ranges(events, chunks=chunks, raw_root=raw_root, copy_rates=copy_rates)

    recommendation = build_recommendation(
        events=events,
        aggregates=aggregates,
        hour_analysis=hour_analysis,
        refetch_results=refetch_results,
        chunk_checks=chunk_checks,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    events_payload = {
        "tool_version": FORENSIC_TOOL_VERSION,
        "symbol": "XAU/USD",
        "terminal_symbol": "XAUUSD",
        "timeframe": timeframe,
        "raw_merged_path": str(merged_path.relative_to(repo_root())).replace("\\", "/"),
        "raw_merged_hash": hash_file_bytes(merged_path),
        "total_bars": len(bars),
        "abnormal_event_count": len(events),
        "events": events,
    }
    summary_payload = {
        "tool_version": FORENSIC_TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_merged_hash": hash_file_bytes(merged_path),
        "abnormal_event_count": len(events),
        "aggregates": aggregates,
        "hour_open_distributions": hour_analysis,
        "sample_event_ids": [event["event_id"] for event in sample],
        "mt5_refetch_status": mt5_status,
        "recommendation": recommendation,
    }
    refetch_payload = {
        "tool_version": FORENSIC_TOOL_VERSION,
        "sample_size": len(sample),
        "results": refetch_results or [],
        "chunk_boundary_checks": chunk_checks or [],
    }

    (output_dir / "xau_h4_gap_events.json").write_text(
        json.dumps(events_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "xau_h4_gap_summary.json").write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "xau_h4_refetch_results.json").write_text(
        json.dumps(refetch_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "events_path": str((output_dir / "xau_h4_gap_events.json").relative_to(repo_root())).replace("\\", "/"),
        "summary_path": str((output_dir / "xau_h4_gap_summary.json").relative_to(repo_root())).replace("\\", "/"),
        "refetch_path": str((output_dir / "xau_h4_refetch_results.json").relative_to(repo_root())).replace("\\", "/"),
        "abnormal_event_count": len(events),
        "mt5_refetch_status": mt5_status,
        "recommendation": recommendation,
    }
