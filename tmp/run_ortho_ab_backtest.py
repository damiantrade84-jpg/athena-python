from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


PAIRS = [
    "EUR/USD",
    "GBP/JPY",
    "BTC/USDT",
    "SOL/USDT",
    "XAU/USD",
    "NASDAQ-100",
]


def _metric_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "pair": result.get("pair"),
        "type": result.get("type"),
        "totalTrades": result.get("totalTrades"),
        "winRate": result.get("winRate"),
        "profitFactor": result.get("profitFactor"),
        "sqn": result.get("sqn"),
        "sharpe": result.get("sharpe"),
        "sortino": result.get("sortino"),
        "maxDrawdownPct": result.get("maxDrawdownPct"),
        "expectancy": result.get("expectancy"),
        "error": result.get("error"),
    }


ARMS = {
    "off": {"ortho": False},
    "on": {"ortho": True},
}


def _run_one(arm: str, *, athena_mod: Any, config: dict[str, Any], backtest_pair: Any) -> list[dict[str, Any]]:
    arm_cfg = ARMS[arm]
    results: list[dict[str, Any]] = []
    pair_map = {p.get("display"): p for p in athena_mod.ALL_PAIRS}

    print(f"=== Engine A orthogonal vote A/B: arm {arm} ===")
    print(f"ENGINE_A_ORTHO_VOTE_ENABLED before set: {config.get('ENGINE_A_ORTHO_VOTE_ENABLED')!r}")
    config["ENGINE_A_ORTHO_VOTE_ENABLED"] = bool(arm_cfg["ortho"])
    print(f"ENGINE_A_ORTHO_VOTE_ENABLED active: {config.get('ENGINE_A_ORTHO_VOTE_ENABLED')!r}")
    print(f"Pairs: {', '.join(PAIRS)}")
    print("")

    for display in PAIRS:
        pair = pair_map.get(display)
        if not pair:
            row = {"pair": display, "error": "pair_not_found"}
            results.append(row)
            print(f"[ERR] {display}: pair_not_found")
            continue

        print(f"--- {display} ({pair.get('type')}, source={pair.get('source')}) ---")
        try:
            result = backtest_pair(copy.deepcopy(pair), style="auto")
            row = _metric_row(result)
            results.append(row)
            if row.get("error"):
                print(f"[ERR] {display}: {row['error']}")
            else:
                print(
                    "RESULT "
                    f"trades={row.get('totalTrades')} "
                    f"winRate={row.get('winRate')} "
                    f"profitFactor={row.get('profitFactor')} "
                    f"sqn={row.get('sqn')} "
                    f"sharpe={row.get('sharpe')} "
                    f"sortino={row.get('sortino')} "
                    f"maxDrawdownPct={row.get('maxDrawdownPct')} "
                    f"expectancy={row.get('expectancy')}"
                )
        except Exception as exc:
            row = {"pair": display, "error": repr(exc)}
            results.append(row)
            print(f"[EXC] {display}: {exc!r}")
            traceback.print_exc()
        print("")

    print("=== JSON SUMMARY ===")
    print(json.dumps(results, indent=2, sort_keys=True))

    json_path = Path("tmp") / f"ab4_{arm}.json"
    json_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=sorted(ARMS), default=None)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    Path("tmp").mkdir(exist_ok=True)
    os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")

    athena_path = Path("athena.py").resolve()
    spec = importlib.util.spec_from_file_location("athena_monolith_for_ortho_ab", athena_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {athena_path}")
    athena = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = athena
    spec.loader.exec_module(athena)

    from athena_runtime import rt
    from config import CONFIG

    # Keep Phase 20 research artifacts out of the primary audit DB while preserving
    # the existing backtest code path and metrics.
    rt().AUDIT_DB = str(Path("tmp") / "ab4_audit.db")

    arms = [args.arm] if args.arm else list(ARMS)
    combined = {
        arm: _run_one(arm, athena_mod=athena, config=CONFIG, backtest_pair=athena.backtest_pair)
        for arm in arms
    }
    Path("tmp/ab4_combined.json").write_text(
        json.dumps(combined, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    for arm in arms:
        print(f"wrote tmp/ab4_{arm}.json")
    print("wrote tmp/ab4_combined.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
