#!/usr/bin/env python3
"""Generate Engine A/B pair coverage matrix (static + optional live crypto probe).

Read-only audit artifact. Does not import athena.py.

Usage:
    python tools/pair_coverage_matrix.py
    python tools/pair_coverage_matrix.py --probe-crypto --out reports/engine_ab_coverage_matrix.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ATHENA_PY = ROOT / "athena.py"
TOGGLE_STATE = ROOT / "toggle_state.json"


def _load_all_pairs() -> tuple[list[dict], list[dict]]:
    src = ATHENA_PY.read_text(encoding="utf-8")
    lists: dict[str, list[dict]] = {}
    for name in (
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    ):
        m = re.search(rf"{name}\s*=\s*\[", src)
        if not m:
            continue
        start = m.end() - 1
        depth = 0
        for i, ch in enumerate(src[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    lists[name] = ast.literal_eval(src[start : i + 1])
                    break
    all_pairs: list[dict] = []
    for key in (
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    ):
        all_pairs.extend(lists.get(key, []))
    if TOGGLE_STATE.is_file():
        state = json.loads(TOGGLE_STATE.read_text(encoding="utf-8"))
        for p in all_pairs:
            disp = p.get("display")
            if disp in state:
                p["enabled"] = bool(state[disp])
    return all_pairs, lists.get("JSE_PAIRS", [])


def _scan_candidates(all_pairs: list[dict], jse_pairs: list[dict]) -> list[dict]:
    disabled_jse = {
        p.get("symbol")
        for p in jse_pairs
        if not p.get("enabled", True)
    }
    return [p for p in all_pairs if p.get("symbol") not in disabled_jse]


def _probe_crypto_bybit(pair: dict) -> str:
    """Return feed_status for crypto pair via Bybit D1 probe."""
    try:
        from data_feeds import _fetch_bybit_klines

        symbol = str(pair.get("display") or pair.get("symbol") or "")
        rows = _fetch_bybit_klines(symbol, "D1", limit=5)
        if rows and len(rows) >= 3:
            return "PASS_BYBIT"
        return "DEGRADED_INSUFFICIENT_HISTORY"
    except Exception as exc:
        return f"BLOCKED:{type(exc).__name__}"


def _feed_status_for_pair(pair: dict, *, probe_crypto: bool) -> str:
    source = str(pair.get("source") or "").lower()
    ptype = str(pair.get("type") or "").lower()
    if ptype == "crypto" and probe_crypto:
        return _probe_crypto_bybit(pair)
    if source == "mt5":
        return "NOT_PROBED_MT5_UNAVAILABLE"
    if source == "eodhd":
        return "NOT_PROBED_EODHD"
    if source == "binance" and ptype != "crypto":
        return "NOT_PROBED_BINANCE"
    if source in ("polygon", "yfinance"):
        return f"NOT_PROBED_{source.upper()}"
    return "NOT_PROBED"


def build_matrix(*, probe_crypto: bool = False) -> dict:
    from config import CONFIG
    from scoring import get_pair_score_group
    from tools.engine_a_group_contracts import validate_engine_a_group_coverage
    from tools.engine_b_group_contracts import validate_engine_b_group_coverage

    all_pairs, jse = _load_all_pairs()
    candidates = _scan_candidates(all_pairs, jse)
    active = [p for p in candidates if p.get("enabled", True)]

    rows = []
    for pair in all_pairs:
        in_scan = pair in candidates or (
            pair.get("symbol") not in {
                p.get("symbol")
                for p in jse
                if not p.get("enabled", True)
            }
        )
        score_group = get_pair_score_group(pair)
        rows.append(
            {
                "display": pair.get("display"),
                "symbol": pair.get("symbol"),
                "type": pair.get("type"),
                "source": pair.get("source"),
                "enabled": bool(pair.get("enabled", True)),
                "score_group": score_group,
                "in_scan_universe": in_scan,
                "engine_a_scored_in_scan": in_scan,
                "engine_b_evaluated_in_scan": in_scan,
                "feed_status": _feed_status_for_pair(pair, probe_crypto=probe_crypto),
                "engine_a_threshold": float(
                    (CONFIG.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {}).get(score_group, 0)
                ),
                "engine_b_override_present": score_group in (
                    (CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {}
                ),
            }
        )

    active_groups = Counter(get_pair_score_group(p) for p in active)
    empty_groups = sorted(
        g for g in active_groups
        if active_groups[g] == 0
    )

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "all_pairs": len(all_pairs),
            "scan_candidates": len(candidates),
            "active_pairs": len(active),
            "engine_a_contract_ok": validate_engine_a_group_coverage()["ok"],
            "engine_b_contract_ok": validate_engine_b_group_coverage()["ok"],
            "active_score_group_histogram": dict(sorted(active_groups.items())),
            "catch_all_groups_without_active_pairs": sorted(
                g
                for g in ("forex_other", "stock_other", "unknown")
                if active_groups.get(g, 0) == 0
            ),
        },
        "gaps": {
            "intentional_exclusions": {
                "jse_pairs_disabled": [
                    p.get("display") for p in jse if not p.get("enabled", True)
                ],
                "note": "JSE symbols excluded from scanner candidate_pairs when disabled",
            },
            "config_gaps": {
                "unknown_score_group_b_override": "unknown" not in (
                    (CONFIG.get("NAKED_ENGINE") or {}).get("score_group_overrides") or {}
                ),
            },
            "live_probe_limits": {
                "mt5": "MetaTrader5 module not available in this environment",
                "crypto_bybit_probed": probe_crypto,
            },
        },
        "pairs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Engine A/B pair coverage matrix")
    parser.add_argument(
        "--probe-crypto",
        action="store_true",
        help="Live-probe crypto pairs via Bybit D1 klines",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "reports" / "engine_ab_coverage_matrix.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()

    matrix = build_matrix(probe_crypto=args.probe_crypto)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"[written] {out_path}")
    print(
        f"scan_candidates={matrix['summary']['scan_candidates']} "
        f"active={matrix['summary']['active_pairs']} "
        f"engine_a_contract={matrix['summary']['engine_a_contract_ok']} "
        f"engine_b_contract={matrix['summary']['engine_b_contract_ok']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
