"""audit_btc_bias_multiplier.py — Verify BTC-bias multiplier asymmetry.

Implements §7.5 of plans/engine-d-scoring-ai-review-audit-8577d0.md.

Audit claim (§2.2):
  - scoring.py:484-494 applies a BTC bias multiplier to crypto-alt scores.
  - Defaults: penalty=0.85 (15% cut when BTC opposes), boost=1.10 (10% lift when aligned).
  - Audit argument: penalty should be larger than boost (failure mode is asymmetric).

This tool resolves the **effective** values from the live config (CONFIG.get
("BTC_BIAS_MULTIPLIERS")) — no hard-coding — and reports:
  - Effective penalty / boost / asymmetry delta (boost - (1 - penalty)).
  - The implied per-trade expected-value impact at common winrates.
  - Any rows in logs/threshold_audit/signal_funnel.jsonl carrying
    `btc_bias_applied` / `btc_bias_delta` fields (currently not propagated by
    threshold_audit.build_signal_funnel_row, but if they ever are, this tool
    will surface them).

Read-only.  Does NOT modify config or scoring code.

Usage:
  python tools/audit_btc_bias_multiplier.py
  python tools/audit_btc_bias_multiplier.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FUNNEL = ROOT / "logs" / "threshold_audit" / "signal_funnel.jsonl"

# Hard-coded fallback defaults from scoring.py:484-485 (must mirror code)
_FALLBACK_PENALTY = 0.85
_FALLBACK_BOOST = 1.10


def _load_config_multipliers() -> dict[str, Any]:
    """Resolve BTC_BIAS_MULTIPLIERS from live CONFIG.  Falls back to defaults
    if config import fails (e.g. running outside the project venv).
    """
    sys.path.insert(0, str(ROOT))
    try:
        from config import CONFIG  # type: ignore
    except Exception as exc:
        return {
            "source": "fallback (config import failed)",
            "import_error": str(exc),
            "penalty": _FALLBACK_PENALTY,
            "boost": _FALLBACK_BOOST,
        }
    cfg = CONFIG.get("BTC_BIAS_MULTIPLIERS") or {}
    return {
        "source": "config.yaml" if cfg else "scoring.py defaults",
        "penalty": float(cfg.get("penalty", _FALLBACK_PENALTY)),
        "boost":   float(cfg.get("boost",   _FALLBACK_BOOST)),
    }


def _scan_signal_funnel(path: Path) -> dict[str, Any]:
    """Look for any signal_funnel rows that carry the btc_bias fields.
    Currently those fields live on the in-memory signal dict (factor_result)
    but are NOT included in the build_signal_funnel_row schema, so the count
    is expected to be zero today.  Tool stays forward-compatible.
    """
    counts: Counter = Counter()
    deltas: list[float] = []
    aligned = 0
    opposed = 0
    if not path.exists():
        return {"total_rows": 0, "with_btc_bias_fields": 0,
                "applied_distribution": {}, "deltas": [], "note": "signal_funnel.jsonl not found"}
    total = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            total += 1
            applied = row.get("btc_bias_applied")
            delta = row.get("btc_bias_delta")
            if applied is None and delta is None:
                continue
            counts[str(applied)] += 1
            if isinstance(delta, (int, float)):
                deltas.append(float(delta))
                if delta > 0:
                    aligned += 1
                elif delta < 0:
                    opposed += 1
    return {
        "total_rows": total,
        "with_btc_bias_fields": sum(counts.values()),
        "applied_distribution": dict(counts.most_common()),
        "delta_count": len(deltas),
        "delta_mean": round(sum(deltas) / len(deltas), 5) if deltas else None,
        "delta_min": round(min(deltas), 5) if deltas else None,
        "delta_max": round(max(deltas), 5) if deltas else None,
        "aligned_application_count": aligned,
        "opposed_application_count": opposed,
    }


def _expected_value_impact(penalty: float, boost: float) -> dict[str, Any]:
    """Rough per-trade EV impact at sample winrates.

    Modelled simply: assume a 50/50 split between aligned and opposed
    crypto setups, and a base score of 1.0 normalised.  Expected score
    multiplier = 0.5 * boost + 0.5 * penalty.
    The audit's claim is that asymmetry should *reduce* expected score on
    average (boost smaller than penalty cut), penalising the alt class.
    """
    avg_mult = 0.5 * boost + 0.5 * penalty
    boost_pct = (boost - 1.0) * 100.0
    penalty_pct = (1.0 - penalty) * 100.0
    asymmetry_bp_cut = boost_pct - penalty_pct  # +ve = boost dominates (audit calls this wrong-direction)
    return {
        "avg_score_multiplier_50_50_split": round(avg_mult, 4),
        "boost_pct": round(boost_pct, 2),
        "penalty_pct": round(penalty_pct, 2),
        "asymmetry_bp_diff_pct_pts": round(asymmetry_bp_cut, 2),
        "audit_expectation": (
            "Audit §2.2 expects penalty% > boost% (negative asymmetry). "
            "Positive asymmetry value here means BTC alignment over-rewards alts."
        ),
    }


def _summary(cfg: dict[str, Any], log_scan: dict[str, Any]) -> dict[str, Any]:
    impact = _expected_value_impact(cfg["penalty"], cfg["boost"])
    return {
        "effective_config": cfg,
        "expected_value_impact": impact,
        "signal_funnel_scan": log_scan,
    }


def _print_human(s: dict[str, Any]) -> None:
    cfg = s["effective_config"]
    impact = s["expected_value_impact"]
    log = s["signal_funnel_scan"]

    print("\n=== BTC Bias Multiplier Audit (scoring.py:484-494) ===\n")
    print(f"  source           : {cfg.get('source')}")
    if cfg.get("import_error"):
        print(f"  import_error     : {cfg.get('import_error')}")
    print(f"  effective penalty: {cfg['penalty']}  (multiplier when BTC opposes alt direction)")
    print(f"  effective boost  : {cfg['boost']}  (multiplier when BTC aligns with alt direction)")

    print("\n--- Per-trade EV impact (50/50 aligned/opposed split, normalised score=1.0) ---")
    print(f"  avg score multiplier        : {impact['avg_score_multiplier_50_50_split']}")
    print(f"  boost vs unity              : +{impact['boost_pct']}%")
    print(f"  penalty vs unity            : -{impact['penalty_pct']}%")
    print(f"  asymmetry (boost - penalty) : {impact['asymmetry_bp_diff_pct_pts']:+.2f} pp")
    print(f"  expectation: {impact['audit_expectation']}")

    print("\n--- Signal funnel scan ---")
    print(f"  total signal_funnel rows         : {log.get('total_rows', 0)}")
    print(f"  rows with btc_bias_* fields      : {log.get('with_btc_bias_fields', 0)}")
    if log.get("with_btc_bias_fields", 0):
        print(f"  applied_distribution             : {log.get('applied_distribution')}")
        print(f"  delta count / mean / min / max   : "
              f"{log.get('delta_count')} / {log.get('delta_mean')} / "
              f"{log.get('delta_min')} / {log.get('delta_max')}")
        print(f"  aligned applications  : {log.get('aligned_application_count')}")
        print(f"  opposed applications  : {log.get('opposed_application_count')}")
    else:
        print("  (note: btc_bias_applied / btc_bias_delta are not currently propagated")
        print("   into signal_funnel.jsonl by threshold_audit.build_signal_funnel_row.")
        print("   They DO live on the in-memory factor_result dict — see scoring.py:497-505.")
        print("   Tool is forward-compatible if those fields are added to the funnel row.)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-funnel", type=Path, default=SIGNAL_FUNNEL)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cfg = _load_config_multipliers()
    log_scan = _scan_signal_funnel(args.signal_funnel)
    summary = _summary(cfg, log_scan)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_human(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
