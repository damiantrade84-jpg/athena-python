from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

REPO_ROOT = Path(__file__).resolve().parents[1]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from athena_runtime import set_runtime
from backtest_candle_cache import _read_rows
import backtest_runner
from config import CONFIG


PAIR = {"display": "XAU/USD", "symbol": "GC=F", "source": "mt5", "type": "commodity"}
DEBUG_PATH = Path("tmp/phase13_xau_votes.jsonl")


class RuntimeStub:
    AUDIT_DB = "tmp/phase13_xau_audit.db"

    def fetch_candles(self, pair_arg, tf, limit):
        return _read_rows(pair_arg, str(tf).upper(), int(limit)) or []

    def fetch_bt_yfinance(self, _symbol):
        return [], []

    def max_score_for_pair(self, _pair):
        return 3.0

    def atr_for_levels(self, d1i, h4i, h1i, pair=None, style=None):
        for snap in (
            (d1i or {}).get("snap", {}),
            (h4i or {}).get("snap", {}),
            (h1i or {}).get("snap", {}),
        ):
            if snap.get("atr"):
                return snap.get("atr")
        return None

    def __getattr__(self, _name):
        def _missing(*_args, **_kwargs):
            return None

        return _missing


def main() -> int:
    if DEBUG_PATH.exists():
        DEBUG_PATH.unlink()

    set_runtime(RuntimeStub())
    CONFIG["ENGINE_A_ORTHO_VOTE_ENABLED"] = True
    CONFIG["ENGINE_A_ORTHO_VOTE_DEBUG"] = True
    CONFIG["ENGINE_A_ORTHO_VOTE_DEBUG_PATH"] = str(DEBUG_PATH)

    result = backtest_runner.backtest_pair(copy.deepcopy(PAIR), style="auto")
    summary = {
        k: result.get(k)
        for k in [
            "pair",
            "totalTrades",
            "winRate",
            "profitFactor",
            "sqn",
            "sharpe",
            "sortino",
            "maxDrawdownPct",
            "expectancy",
            "error",
        ]
    }
    Path("tmp/phase13_xau_on.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("RESULT", json.dumps(summary, sort_keys=True))

    rows = [json.loads(line) for line in DEBUG_PATH.read_text(encoding="utf-8").splitlines()]
    sub = [r for r in rows if r.get("display") == "XAU/USD"]
    print("VOTE_ROWS", len(sub))
    vals = [r["votes"]["vol_skew"]["vote"] for r in sub if "vol_skew" in r.get("votes", {})]
    statuses: dict[str, int] = {}
    for row in sub:
        if "vol_skew" in row.get("votes", {}):
            status = row["votes"]["vol_skew"]["status"]
            statuses[status] = statuses.get(status, 0) + 1
    if vals:
        print(
            "VOL_SKEW",
            "mean",
            round(sum(vals) / len(vals), 4),
            "min",
            min(vals),
            "max",
            max(vals),
            "status",
            statuses,
        )
    else:
        print("VOL_SKEW", "no_values", "status", statuses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
