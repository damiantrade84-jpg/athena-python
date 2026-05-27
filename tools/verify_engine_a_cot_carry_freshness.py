#!/usr/bin/env python3
"""Read-only probe: COT + Carry feed coverage + basic freshness for all Engine A score groups.

Safe to run in any environment. Exercises the exact same addon paths used by
factor_scoring._asset_addon during live scans.

Usage:
    python tools/verify_engine_a_cot_carry_freshness.py
    python tools/verify_engine_a_cot_carry_freshness.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure we can import from project root when run as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from tools.engine_a_group_contracts import validate_engine_a_group_coverage


def _representative_display(group: str) -> str:
    """Return one realistic display name that get_pair_score_group maps to this group."""
    mapping = {
        "forex_majors": "EUR/USD",
        "forex_crosses": "EUR/GBP",
        "forex_exotics": "USD/MXN",
        "forex_other": "USD/SGD",
        "crypto_btc": "BTC/USDT",
        "crypto_eth": "ETH/USDT",
        "crypto_doge": "DOGE/USDT",
        "crypto_alt_majors": "SOL/USDT",
        "crypto_other": "XRP/USDT",
        "precious_trackers": "XAU/USD",
        "energy_oil": "WTI Oil",
        "nat_gas": "Nat Gas",
        "copper": "Copper",
        "pgm_metals": "XPT/USD",
        "base_metals": "Aluminium",
        "softs": "Corn",
        "commodity_other": "Cocoa",
        "us_indices_trackers": "S&P 500",
        "eu_indices": "DAX 40",
        "asian_indices": "Nikkei 225",
        "index_other": "ASX 200",
        "us_stock_single": "AAPL",
        "bond_tlt": "TLT",
        "smallcap_em_etf": "IWM",
        "stock_other": "TSLA",
        "unknown": "RANDOM/PAIR",
    }
    return mapping.get(group, "EUR/USD")


def _check_cot(display: str) -> dict:
    try:
        from cot_feed import get_cot_z, _cot_formula_supported

        supported = _cot_formula_supported(display)
        if not supported:
            return {"status": "no_formula", "z": None, "supported": False}

        z = get_cot_z(display)
        return {
            "status": "ok" if z != 0.0 else "zero_or_stale",
            "z": z,
            "supported": True,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)[:120], "supported": None}


def _check_carry(display: str) -> dict:
    try:
        from carry_feed import get_carry_z, _PAIR_CARRY_FORMULA

        supported = display in _PAIR_CARRY_FORMULA and bool(_PAIR_CARRY_FORMULA[display])
        if not supported:
            return {"status": "no_formula", "z": None, "supported": False}

        z = get_carry_z(display)
        return {
            "status": "ok" if z != 0.0 else "zero_or_stale",
            "z": z,
            "supported": True,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)[:120], "supported": None}


def run_probe() -> dict:
    group_report = validate_engine_a_group_coverage()

    results = []
    for group in sorted(ENGINE_A_KNOWN_SCORE_GROUPS):
        disp = _representative_display(group)
        cot = _check_cot(disp)
        carry = _check_carry(disp)
        results.append({
            "group": group,
            "display": disp,
            "cot": cot,
            "carry": carry,
        })

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "groups_checked": len(results),
        "group_contract_ok": group_report["ok"],
        "cot_with_formula": sum(1 for r in results if r["cot"].get("supported")),
        "carry_with_formula": sum(1 for r in results if r["carry"].get("supported")),
        "results": results,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    summary = run_probe()

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print("Engine A COT / Carry Freshness + Coverage Probe")
    print(f"Time: {summary['timestamp']}")
    print(f"Groups: {summary['groups_checked']} | Contract OK: {summary['group_contract_ok']}")
    print(f"COT formulas present: {summary['cot_with_formula']}")
    print(f"Carry formulas present: {summary['carry_with_formula']}")
    print()

    print(f"{'Group':<22} {'Display':<14} {'COT':<18} {'Carry':<18}")
    print("-" * 75)
    for r in summary["results"]:
        cot_s = r["cot"]["status"]
        cot_z = r["cot"].get("z")
        cot_str = f"{cot_s}({cot_z})" if cot_z is not None else cot_s

        carry_s = r["carry"]["status"]
        carry_z = r["carry"].get("z")
        carry_str = f"{carry_s}({carry_z})" if carry_z is not None else carry_s

        print(f"{r['group']:<22} {r['display']:<14} {cot_str:<18} {carry_str:<18}")

    print()
    if not summary["group_contract_ok"]:
        print("WARNING: Group coverage contract has gaps (run with --json for details).")
    else:
        print("Group coverage contract satisfied.")


if __name__ == "__main__":
    main()
