from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path


PAIRS = ["BTC/USDT", "SOL/USDT"]
DEBUG_PATH = Path("tmp/phase18_ortho_votes.jsonl")


def _tail_for_tf(tf: str, candles: list[dict]) -> list[dict]:
    tf_u = str(tf or "").upper()
    if tf_u == "D1":
        return candles[-260:]
    if tf_u == "H4":
        return candles[-360:]
    if tf_u == "H1":
        return candles[-960:]
    return candles


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
    DEBUG_PATH.parent.mkdir(exist_ok=True)
    if DEBUG_PATH.exists():
        DEBUG_PATH.unlink()

    athena_path = Path("athena.py").resolve()
    spec = importlib.util.spec_from_file_location("athena_monolith_for_phase12_debug", athena_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {athena_path}")
    athena = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = athena
    spec.loader.exec_module(athena)

    import backtest_runner
    from config import CONFIG

    CONFIG["ENGINE_A_ORTHO_VOTE_ENABLED"] = True
    CONFIG["ENGINE_A_ORTHO_VOTE_DEBUG"] = True
    CONFIG["ENGINE_A_ORTHO_VOTE_DEBUG_PATH"] = str(DEBUG_PATH)

    original_crypto_fetch = backtest_runner._crypto_bt_signal_candles

    def _short_crypto_fetch(pair: dict, *, engine: str, tf: str, limit: int, min_bars: int | None):
        candles = original_crypto_fetch(
            pair,
            engine=engine,
            tf=tf,
            limit=limit,
            min_bars=min_bars,
        )
        return _tail_for_tf(tf, candles or [])

    backtest_runner._crypto_bt_signal_candles = _short_crypto_fetch
    pair_map = {p.get("display"): p for p in athena.ALL_PAIRS}
    results = {}
    for display in PAIRS:
        pair = pair_map[display]
        result = athena.backtest_pair(copy.deepcopy(pair), style="auto")
        results[display] = {
            "totalTrades": result.get("totalTrades"),
            "winRate": result.get("winRate"),
            "profitFactor": result.get("profitFactor"),
            "sqn": result.get("sqn"),
            "error": result.get("error"),
        }

    print("RESULTS", json.dumps(results, sort_keys=True))
    rows = [json.loads(line) for line in DEBUG_PATH.read_text(encoding="utf-8").splitlines()]
    for display in PAIRS:
        sub = [r for r in rows if r["display"] == display]
        print(display, "bars", len(sub))
        for factor in ["funding", "oi_divergence", "cot"]:
            vals = [r["votes"][factor]["vote"] for r in sub if factor in r["votes"]]
            sts = Counter(r["votes"][factor]["status"] for r in sub if factor in r["votes"])
            if vals:
                print(
                    "  ",
                    factor,
                    "mean",
                    round(sum(vals) / len(vals), 3),
                    "min",
                    min(vals),
                    "max",
                    max(vals),
                    "status",
                    dict(sts),
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
