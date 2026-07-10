"""Audit FRED series availability lags in PTIS (WO T0.5).

Prints every FRED series in PTIS with its availability rule, implied lag, and
the 3 most recent observations (value_time vs available_time) so the lags can
be spot-checked against the actual FRED release calendar. Availability rules
stay verified=False until Damian confirms the spot-check.

Usage: py tools/audit_fred_lags.py [--out reports/fred_lag_audit.md]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))

from athena_ase.data.availability import AVAILABILITY_RULES, rule_for_source  # noqa: E402
from athena_ase.data.ptis import PTISStore, default_ptis_root  # noqa: E402


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/fred_lag_audit.md")
    args = ap.parse_args()

    store = PTISStore(default_ptis_root())
    lines = [
        "# FRED availability-lag audit",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Spot-check the implied lags against the actual FRED release dates: "
        "https://fred.stlouisfed.org/series/<SERIES_ID> (Release table). Rules stay "
        "`verified=False` until confirmed.",
        "",
        "| series | source | rule | verified | value_time | available_time | lag (days) |",
        "|---|---|---|---|---|---|---|",
    ]
    found = 0
    for series_id in sorted(store.list_series()):
        if not series_id.upper().startswith("FRED"):
            continue
        source = store._series_source(series_id)  # noqa: SLF001
        if not str(source).upper().startswith("FRED"):
            continue
        found += 1
        try:
            rule_id = rule_for_source(source)
            rule = AVAILABILITY_RULES[rule_id]
        except (ValueError, KeyError):
            lines.append(f"| {series_id} | {source} | UNKNOWN | - | - | - | - |")
            continue
        rows = store.asof(series_id, 2**62, n=3)
        if len(rows) == 0:
            lines.append(
                f"| {series_id} | {source} | {rule_id.value} | {rule.verified} | (empty) | - | - |"
            )
            continue
        for row in rows:
            lag_days = (int(row["available_time"]) - int(row["value_time"])) / 86_400_000
            lines.append(
                f"| {series_id} | {source} | {rule_id.value} | {rule.verified} "
                f"| {_iso(int(row['value_time']))} | {_iso(int(row['available_time']))} "
                f"| {lag_days:.1f} |"
            )
    if not found:
        lines.append("| (no FRED series found in PTIS) | | | | | | |")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({found} FRED series)")


if __name__ == "__main__":
    main()
