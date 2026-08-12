"""Timestamp mismatch evaluation for AI chart review."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def evaluate_timestamp_mismatch(
    engine_a_ctx: dict[str, Any],
    screenshot_meta: dict[str, Any],
    cfg: dict[str, Any],
) -> list[str]:
    """Soft chart/engine timestamp alignment warnings (advisory only).

    Primary comparison uses ``review_timestamp`` when present — that is when the
    server rebuilt engine context for this review. Comparing capture only to the
    origin ``scan_timestamp`` falsely flags candidate reviews that humans open a
    few minutes after the scan even though live context and the screenshot are
    both fresh.
    """
    warnings: list[str] = []
    review_dt = _parse_iso(engine_a_ctx.get("review_timestamp"))
    scan_dt = _parse_iso(engine_a_ctx.get("scan_timestamp"))
    captured_dt = _parse_iso(screenshot_meta.get("captured_at"))
    max_delta = float(cfg.get("MISMATCH_WARN_MAX_SECONDS") or 120)

    # Prefer live review build time over origin scan age for chart freshness.
    data_dt = review_dt or scan_dt
    data_label = "review_timestamp" if review_dt else "scan_timestamp"

    if data_dt and captured_dt:
        delta = abs((captured_dt - data_dt).total_seconds())
        if delta > max_delta:
            warnings.append(
                f"chart captured {int(delta)}s from {data_label} (max {int(max_delta)}s)"
            )
    elif not captured_dt:
        warnings.append("screenshot_meta.captured_at missing")

    # Separate advisory: origin candidate is old relative to this review build.
    if review_dt and scan_dt:
        candidate_age = abs((review_dt - scan_dt).total_seconds())
        if candidate_age > max_delta:
            warnings.append(
                f"candidate scan_timestamp is {int(candidate_age)}s old at review "
                f"(max {int(max_delta)}s)"
            )

    latest = _parse_iso(engine_a_ctx.get("latest_candle_ts"))
    if latest and captured_dt and captured_dt < latest:
        warnings.append("chart captured before latest_candle_ts")

    return warnings


def evaluate_review_freshness(
    engine_a_ctx: dict[str, Any],
    screenshot_meta: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Structured freshness decision for the pre-provider gate.

    ``evaluate_timestamp_mismatch`` returns advisory strings and prefers
    ``review_timestamp`` as the comparison basis. That basis is stamped
    ``datetime.now()`` at context assembly, so capture-vs-review is always
    small for a normal submit and the capture-skew filter alone can never
    catch a stale candidate.

    The candidate age (``review_timestamp - scan_timestamp``) is the signal
    that actually matters, because the review rebuilds *factors* live while
    entry/SL/TP levels are still copied from the origin candidate. Fresh
    factors joined to stale levels present as fresh and are exactly the
    trigger-zone drift this fix pack was written for, so the age is blocking
    rather than advisory.
    """
    warnings = evaluate_timestamp_mismatch(engine_a_ctx, screenshot_meta, cfg)
    max_delta = float(cfg.get("MISMATCH_WARN_MAX_SECONDS") or 120)

    review_dt = _parse_iso(engine_a_ctx.get("review_timestamp"))
    scan_dt = _parse_iso(engine_a_ctx.get("scan_timestamp"))
    captured_dt = _parse_iso(screenshot_meta.get("captured_at"))
    basis_dt = review_dt or scan_dt

    capture_skew = (
        abs((captured_dt - basis_dt).total_seconds())
        if captured_dt and basis_dt
        else None
    )
    candidate_age = (
        abs((review_dt - scan_dt).total_seconds()) if review_dt and scan_dt else None
    )

    blocking: list[dict[str, Any]] = []
    if capture_skew is not None and capture_skew > max_delta:
        blocking.append(
            {
                "reason": "capture_skew_exceeded",
                "detail": (
                    f"chart captured {int(capture_skew)}s from "
                    f"{'review_timestamp' if review_dt else 'scan_timestamp'} "
                    f"(max {int(max_delta)}s)"
                ),
                "seconds": capture_skew,
            }
        )
    if captured_dt is None:
        blocking.append(
            {
                "reason": "capture_timestamp_missing",
                "detail": "screenshot_meta.captured_at missing — capture age unprovable",
                "seconds": None,
            }
        )
    # Candidate age is advisory, not blocking. Only ``candidate_entry`` comes
    # from the origin candidate; ``stop_loss`` / ``take_profit`` / ``price`` are
    # taken from the live re-analysis (engine_a_context assembles them from
    # ``signal``, not ``origin``), so an old candidate does not by itself mean
    # stale levels. Blocking on age alone rejected the normal workflow — a
    # candidate opened from a scan card minutes later is routinely older than
    # the window while its levels are current.
    #
    # The real risk from an old candidate is entry/trigger-zone drift, and that
    # is measured directly rather than inferred from a clock:
    # ``price_displacement_from_candidate_entry``, ``zone_status``
    # (ABOVE_ZONE/BELOW_ZONE clears execution_permitted) and ``rr_live``
    # recomputed at live price. Those remain blocking.
    if (
        candidate_age is not None
        and candidate_age > max_delta
        and bool(cfg.get("ENFORCE_CANDIDATE_AGE_PRE_PROVIDER", False))
    ):
        blocking.append(
            {
                "reason": "stale_candidate_levels",
                "detail": (
                    f"candidate scan_timestamp is {int(candidate_age)}s old at review "
                    f"(max {int(max_delta)}s); candidate_entry is from that candidate "
                    f"while price/SL/TP were rebuilt live"
                ),
                "seconds": candidate_age,
            }
        )

    return {
        "warnings": warnings,
        "blocking": blocking,
        "ok": not blocking,
        "captureSkewSeconds": capture_skew,
        "candidateAgeSeconds": candidate_age,
        "maxSeconds": max_delta,
        "comparisonBasis": "review_timestamp" if review_dt else "scan_timestamp",
        # Provenance is the point: these two do not share a clock.
        "levelsProvenance": {
            "levelsFrom": "origin_candidate",
            "levelsAsOf": engine_a_ctx.get("scan_timestamp"),
            "factorsRebuiltAt": engine_a_ctx.get("review_timestamp"),
            "sharedClock": candidate_age is not None and candidate_age <= max_delta,
        },
    }
