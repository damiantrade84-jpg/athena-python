"""Build markdown summary from engine_b_actionability_reconciliation.csv."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def build_summary(csv_path: Path, out_path: Path) -> str:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8"))) if csv_path.exists() else []
    total = len(rows)
    conflicts = [
        r
        for r in rows
        if str(r.get("engine_b_actionable_raw")).lower() in {"false", "0", ""}
        and str(r.get("ai_calibration_actionable_raw")).lower() in {"true", "1"}
    ]
    no_ai_true = [r for r in rows if "ACTIONABILITY_CONFLICT_ENGINE_B_NO_AI_TRUE" in str(r.get("rejection_reasons"))]
    trigger_fail = [r for r in rows if (r.get("engine_b_canonical_status") or "") == "REJECT_NO_TRIGGER"]
    room_fail = [r for r in rows if (r.get("engine_b_canonical_status") or "") == "REJECT_NO_ROOM"]
    tp_fail = [r for r in rows if (r.get("engine_b_canonical_status") or "") == "REJECT_STRUCTURAL_TP"]
    rr_fail = [r for r in rows if (r.get("engine_b_canonical_status") or "") == "REJECT_RR_QUALITY"]
    surfaced = lambda status: [
        r
        for r in rows
        if (r.get("engine_b_canonical_status") or "") == status
        and (r.get("final_routing") or "") != "rejected_diagnostics"
    ]
    reached_ui = sum(1 for r in rows if str(r.get("reached_ui")).lower() in {"true", "1", "yes"})
    reached_ai = sum(1 for r in rows if str(r.get("reached_ai_review")).lower() in {"true", "1", "yes"})
    reasons = Counter()
    for r in rows:
        for part in str(r.get("rejection_reasons") or "").split(";"):
            part = part.strip()
            if part:
                reasons[part] += 1
    by_pair = Counter(r.get("pair") or "?" for r in rows if str(r.get("engine_b_canonical_actionable")).lower() != "true")
    by_dir = Counter(r.get("direction") or "?" for r in rows if str(r.get("engine_b_canonical_actionable")).lower() != "true")

    lines = [
        "# Engine B Actionability Reconciliation Summary",
        "",
        f"- Total candidates scanned: **{total}**",
        f"- Actionability conflicts (playbook NO + legacy AI TRUE): **{len(conflicts)}**",
        f"- REJECT_CONFLICT (ACTIONABILITY_CONFLICT_ENGINE_B_NO_AI_TRUE): **{len(no_ai_true)}**",
        f"- Trigger failures that still surfaced: **{len(surfaced('REJECT_NO_TRIGGER'))}**",
        f"- Room failures that still surfaced: **{len(surfaced('REJECT_NO_ROOM'))}**",
        f"- Structural TP failures that still surfaced: **{len(surfaced('REJECT_STRUCTURAL_TP'))}**",
        f"- RR1 failures that still surfaced: **{len(surfaced('REJECT_RR_QUALITY'))}**",
        f"- Rejected candidates that reached UI: **{reached_ui}**",
        f"- Rejected candidates that reached AI review: **{reached_ai}**",
        "",
        "## Status counts",
        "",
        f"- REJECT_NO_TRIGGER rows: {len(trigger_fail)}",
        f"- REJECT_NO_ROOM rows: {len(room_fail)}",
        f"- REJECT_STRUCTURAL_TP rows: {len(tp_fail)}",
        f"- REJECT_RR_QUALITY rows: {len(rr_fail)}",
        "",
        "## Top rejection reasons",
        "",
    ]
    for reason, count in reasons.most_common(20):
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Per-pair non-actionable", ""])
    for pair, count in by_pair.most_common():
        lines.append(f"- {pair}: {count}")
    lines.extend(["", "## Long/short non-actionable", ""])
    for direction, count in by_dir.most_common():
        lines.append(f"- {direction}: {count}")
    text = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--csv",
        default="reports/engine_b_actionability_reconciliation.csv",
    )
    parser.add_argument(
        "--out",
        default="reports/engine_b_actionability_reconciliation_summary.md",
    )
    args = parser.parse_args()
    build_summary(Path(args.csv), Path(args.out))


if __name__ == "__main__":
    main()
