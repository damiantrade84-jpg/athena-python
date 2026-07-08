"""Build markdown summary from engine_b_signal_card_consistency.csv."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def _truthy(val: object) -> bool:
    return str(val or "").lower() in {"true", "1", "yes"}


def build_summary(csv_path: Path, out_path: Path) -> str:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8"))) if csv_path.exists() else []
    total = len(rows)
    inconsistent = [r for r in rows if not _truthy(r.get("ui_consistency_pass"))]
    location_ok_on_reject = [
        r
        for r in rows
        if not _truthy(r.get("canonical_trade_ok"))
        and str(r.get("displayed_location_badge") or "").upper() == "OK"
    ]
    room_rr_ok_on_reject = [
        r
        for r in rows
        if not _truthy(r.get("canonical_trade_ok"))
        and str(r.get("displayed_room_rr_badge") or "").upper() == "OK"
    ]
    plain_confidence_on_reject = [
        r
        for r in rows
        if not _truthy(r.get("canonical_trade_ok"))
        and _truthy(r.get("confidence_passed"))
        and "GATE FAILED" not in str(r.get("displayed_structure_badge") or "")
    ]
    executable_levels_on_reject = [
        r
        for r in rows
        if not _truthy(r.get("canonical_trade_ok"))
        and _truthy(r.get("suggested_levels_displayed"))
    ]

    reasons = Counter()
    for r in inconsistent:
        for part in str(r.get("inconsistency_reasons") or "").split(";"):
            part = part.strip()
            if part:
                reasons[part] += 1

    before_after = [
        (
            "LONG entry inside resistance",
            "Before: Location OK, CONFIDENCE PASSED, executable SL/TP. "
            "After: Location FAIL, SCORE PASSED / GATE FAILED, Entry/SL/TP = -, "
            "primary REJECT_ENTRY_LOCATION.",
        ),
        (
            "SHORT entry inside support",
            "Before: Location OK with raw location_ok=true. "
            "After: canonical_location_ok=false, Location badge FAIL.",
        ),
        (
            "room_ok=false with rr_ok=true",
            "Before: Room/RR OK when rr alone passed. "
            "After: Room/RR FAIL unless canonical_room_ok and canonical_rr_ok.",
        ),
    ]

    lines = [
        "# Engine B Signal Card Consistency Summary",
        "",
        f"- Total cards checked: **{total}**",
        f"- Cards with UI inconsistency: **{len(inconsistent)}**",
        f"- Rejected cards showing Location OK: **{len(location_ok_on_reject)}**",
        f"- Rejected cards showing Room/RR OK: **{len(room_rr_ok_on_reject)}**",
        f"- Rejected cards with confidence passed (no gate-failed label): **{len(plain_confidence_on_reject)}**",
        f"- Rejected cards showing suggested executable SL/TP: **{len(executable_levels_on_reject)}**",
        "",
        "## Top inconsistency reasons",
        "",
    ]
    if reasons:
        for reason, count in reasons.most_common(20):
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- (none recorded)")
    lines.extend(["", "## Before / after examples", ""])
    for title, text in before_after:
        lines.append(f"### {title}")
        lines.append("")
        lines.append(text)
        lines.append("")

    text = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--csv",
        default="reports/engine_b_signal_card_consistency.csv",
    )
    parser.add_argument(
        "--out",
        default="reports/engine_b_signal_card_consistency_summary.md",
    )
    args = parser.parse_args()
    build_summary(Path(args.csv), Path(args.out))


if __name__ == "__main__":
    main()
