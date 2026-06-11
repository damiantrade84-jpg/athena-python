"""Generate Phase 0 availability audit report."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from athena_ase.data.ptis import PTISStore


def write_availability_audit(
    store: PTISStore,
    path: Path | str,
    *,
    spot_checks: list[dict] | None = None,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = store.audit_rows()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# ASE Phase 0 — PTIS availability audit",
        "",
        f"Generated: {now}",
        "",
        "Each series uses a frozen `available_time` rule. Series with default (unverified)",
        "FRED lag rules are flagged `UNVERIFIED_LAG` and excluded from enriched models.",
        "",
        "| series_id | source | rule_id | verified | flag | row_count |",
        "|-----------|--------|---------|----------|------|-----------|",
    ]

    for row in rows:
        sid = row.get("series_id", "")
        source = row.get("source", "")
        rule_id = row.get("rule_id", "")
        verified = "yes" if row.get("verified") else "no"
        flag = row.get("flag") or ""
        try:
            n = store.series_row_count(sid)
        except KeyError:
            n = 0
        lines.append(
            f"| {sid} | {source} | {rule_id} | {verified} | {flag} | {n} |"
        )

    if spot_checks:
        lines.extend(["", "## Spot checks", ""])
        for check in spot_checks:
            lines.append(f"- {check.get('series_id')}: {check.get('note', check)}")

    unverified = [r for r in rows if r.get("flag") == "UNVERIFIED_LAG"]
    if unverified:
        lines.extend(
            [
                "",
                "## UNVERIFIED_LAG series",
                "",
                "These may not be used in enriched ASE models until vintage/lag is verified:",
                "",
            ]
        )
        for row in unverified:
            lines.append(f"- `{row.get('series_id')}` — {row.get('description', '')}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
