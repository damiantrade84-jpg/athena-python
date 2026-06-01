from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import socket
import sys
import traceback
from pathlib import Path
from typing import Any


PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "GBP/JPY",
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "XAU/USD",
    "XAG/USD",
    "WTI Oil",
    "NASDAQ-100",
    "S&P 500",
    "Dow Jones",
    "DAX 40",
    "AAPL",
    "TSLA",
    "NVDA",
    "MSFT",
]

ARMS = {
    "off": {"ortho": False},
    "on": {"ortho": True},
}


def _metric_row(result: dict[str, Any], pair: dict[str, Any]) -> dict[str, Any]:
    funnel = result.get("funnel") if isinstance(result.get("funnel"), dict) else {}
    return {
        "pair": result.get("pair") or pair.get("display"),
        "symbol": pair.get("symbol"),
        "type": result.get("type") or pair.get("type"),
        "source": pair.get("source"),
        "totalTrades": result.get("totalTrades"),
        "winRate": result.get("winRate"),
        "profitFactor": result.get("profitFactor"),
        "sqn": result.get("sqn"),
        "sharpe": result.get("sharpe"),
        "sortino": result.get("sortino"),
        "maxDrawdownPct": result.get("maxDrawdownPct"),
        "expectancy": result.get("expectancy"),
        "funnelTaken": funnel.get("taken"),
        "funnelSetups": funnel.get("total_setups"),
        "error": result.get("error"),
    }


def _block_network() -> None:
    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("NETWORK_BLOCKED_AB6")

    socket.create_connection = _blocked
    socket.socket = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("NETWORK_BLOCKED_AB6")
    )
    try:
        import requests

        requests.get = _blocked
        requests.post = _blocked
        requests.sessions.Session.request = _blocked
    except Exception:
        pass
    for name in ("data_feeds", "carry_feed", "cot_feed", "vol_skew_feed", "candle_feeds"):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        if hasattr(mod, "http_requests"):
            try:
                setattr(mod.http_requests, "get", _blocked)
                setattr(mod.http_requests, "post", _blocked)
            except Exception:
                pass
        if hasattr(mod, "requests"):
            try:
                setattr(mod.requests, "get", _blocked)
                setattr(mod.requests, "post", _blocked)
            except Exception:
                pass


def _load_athena(repo_root: Path):
    spec = importlib.util.spec_from_file_location("athena_monolith_ab6", repo_root / "athena.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {repo_root / 'athena.py'}")
    athena = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = athena
    spec.loader.exec_module(athena)
    return athena


def _run_one(arm: str, *, athena_mod: Any, config: dict[str, Any], backtest_pair: Any) -> list[dict[str, Any]]:
    arm_cfg = ARMS[arm]
    config["ENGINE_A_ORTHO_VOTE_ENABLED"] = bool(arm_cfg["ortho"])
    config["ENGINE_A_ORTHO_VOTE_DEBUG"] = False
    pair_map = {p.get("display"): p for p in athena_mod.ALL_PAIRS}
    rows: list[dict[str, Any]] = []
    print(f"=== AB6 arm {arm} ortho={config['ENGINE_A_ORTHO_VOTE_ENABLED']} ===", flush=True)
    print(f"BACKTEST_DATA_AS_OF={os.environ.get('BACKTEST_DATA_AS_OF')}", flush=True)
    print(f"pairs={', '.join(PAIRS)}", flush=True)

    for display in PAIRS:
        pair = pair_map.get(display)
        if not pair:
            row = {"pair": display, "error": "pair_not_found"}
            rows.append(row)
            print(f"[ERR] {display}: pair_not_found", flush=True)
            continue
        print(f"--- {display} ({pair.get('type')}, source={pair.get('source')}) ---", flush=True)
        try:
            result = backtest_pair(copy.deepcopy(pair), style="auto")
            row = _metric_row(result, pair)
        except Exception as exc:
            row = {
                "pair": display,
                "symbol": pair.get("symbol"),
                "type": pair.get("type"),
                "source": pair.get("source"),
                "error": repr(exc),
            }
            print(f"[EXC] {display}: {exc!r}", flush=True)
            traceback.print_exc()
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
    out = Path("tmp") / f"ab6_{arm}.json"
    out.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=sorted(ARMS), required=True)
    parser.add_argument("--as-of", default="2026-05-30")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    Path("tmp").mkdir(exist_ok=True)
    os.environ["BACKTEST_DATA_AS_OF"] = args.as_of
    os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")

    athena = _load_athena(repo_root)
    from athena_runtime import rt
    from config import CONFIG

    rt().AUDIT_DB = str(Path("tmp") / "ab6_audit.db")
    _block_network()
    _run_one(args.arm, athena_mod=athena, config=CONFIG, backtest_pair=athena.backtest_pair)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
