"""Build markdown summary from engine_b_structure_gate_audit.csv."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path


def build_summary(csv_path: Path, out_path: Path) -> str:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8"))) if csv_path.exists() else []
    total = len(rows)
    by_status = Counter(r.get("structural_status") or "UNKNOWN" for r in rows)
    would_surface = [
        r
        for r in rows
        if (r.get("structural_status") or "") == "STRUCTURE_REJECT"
        and (r.get("final_candidate_routing") or "") != "rejected_diagnostics"
    ]
    reached_ui = sum(1 for r in rows if str(r.get("reached_ui")).lower() in {"true", "1", "yes"})
    reached_ai = sum(1 for r in rows if str(r.get("reached_ai_review")).lower() in {"true", "1", "yes"})
    reasons = Counter()
    for r in rows:
        for part in str(r.get("rejection_reason") or "").split(";"):
            part = part.strip()
            if part:
                reasons[part] += 1
    by_pair = Counter(r.get("pair") or "?" for r in rows if (r.get("structural_status") or "") == "STRUCTURE_REJECT")
    by_dir = Counter(r.get("direction") or "?" for r in rows if (r.get("structural_status") or "") == "STRUCTURE_REJECT")

    lines = [
        "# Engine B Structure Gate Summary",
        "",
        f"- Total candidates evaluated: **{total}**",
        f"- STRUCTURE_PASS: **{by_status.get('STRUCTURE_PASS', 0)}**",
        f"- STRUCTURE_WARN: **{by_status.get('STRUCTURE_WARN', 0)}**",
        f"- STRUCTURE_REJECT: **{by_status.get('STRUCTURE_REJECT', 0)}**",
        f"- Rejected that would have surfaced (routing not enforced): **{len(would_surface)}**",
        f"- Rejected that reached UI: **{reached_ui}**",
        f"- Rejected that reached AI review: **{reached_ai}**",
        "",
        "## Top rejection reasons",
        "",
    ]
    for reason, count in reasons.most_common(15):
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Per-pair STRUCTURE_REJECT", ""])
    for pair, count in by_pair.most_common():
        lines.append(f"- {pair}: {count}")
    lines.extend(["", "## Long/short STRUCTURE_REJECT", ""])
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
        default="reports/engine_b_structure_gate_audit.csv",
    )
    parser.add_argument(
        "--out",
        default="reports/engine_b_structure_gate_summary.md",
    )
    args = parser.parse_args()
    build_summary(Path(args.csv), Path(args.out))


if __name__ == "__main__":
    main()
