"""ASE demo/paper end-to-end smoke check.

Hard rules enforced by this script:
- risk_check is never bypassed, including demo execution;
- executors receive sizing only from risk_engine approval.volume;
- fxContext and triangular are diagnostics and never change decisions;
- no order path is entered unless paper/demo, MT5 demo, and Bybit testnet are proven.

Run read-only first::

    py tools/ase_demo_smoke.py --dry

An actual demo/testnet order attempt requires the explicit ``--execute-demo`` flag.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from athena_ase.execution.bridge import default_execution_deps  # noqa: E402
from athena_ase.execution.journal import load_trade_journal, trade_journal_summary  # noqa: E402
from athena_ase.gates.demo_only import assert_demo  # noqa: E402
from athena_ase.instruments import instrument_by_symbol  # noqa: E402
from athena_ase.runtime.scan import run_ase_scan  # noqa: E402


DEFAULT_SYMBOLS = ("EURUSD", "GBPJPY", "BTCUSDT")


def _print(label: str, value: Any) -> None:
    print(f"{label}: {json.dumps(value, indent=2, default=str, sort_keys=True)}")


def _assert_ai_review_risk_context() -> dict[str, bool]:
    """Verify the AI review call still receives deterministic account-risk context."""
    source = (REPO_ROOT / "athena.py").read_text(encoding="utf-8")
    checks = {
        "portfolio_heat": "portfolio_heat=_p_heat" in source,
        "drawdown_pct": "drawdown_pct=_dd_pct" in source,
    }
    if not all(checks.values()):
        missing = [key for key, ok in checks.items() if not ok]
        raise RuntimeError(f"AI review risk context missing: {', '.join(missing)}")
    return checks


def _venue(symbol: str) -> str:
    instrument = instrument_by_symbol(symbol)
    return "bybit" if instrument and instrument.family == "crypto" else "mt5"


def _preflight() -> tuple[Any, dict[str, Any]]:
    deps = default_execution_deps()
    executor_mode = deps.get_executor_mode()
    mt5_trade_mode = deps.get_mt5_trade_mode()
    bybit_base_url = str(deps.get_bybit_base_url() or "")
    gate = assert_demo(
        executor_mode=executor_mode,
        mt5_trade_mode=mt5_trade_mode,
        bybit_base_url=bybit_base_url,
    )
    checks: dict[str, Any] = {
        **gate.checks,
        "executor_mode_value": executor_mode,
        "mt5_trade_mode_value": mt5_trade_mode,
        "bybit_base_url": bybit_base_url,
        "mt5_trade_mode_is_demo": mt5_trade_mode == 0,
        "bybit_url_is_testnet": bybit_base_url.rstrip("/").endswith("api-testnet.bybit.com"),
    }
    if not gate.ok or not checks["mt5_trade_mode_is_demo"] or not checks["bybit_url_is_testnet"]:
        raise RuntimeError(f"demo preflight failed: {checks}")
    return deps, checks


def _latest_fill_rows(limit: int = 5) -> list[dict[str, Any]]:
    df = load_trade_journal()
    if df.empty or "executionStatus" not in df.columns:
        return []
    filled = df[df["executionStatus"].astype(str).str.lower() == "filled"]
    if filled.empty:
        return []
    columns = [
        name
        for name in ("instrument", "decisionTimeMs", "executionStatus", "orderId", "executionDetail")
        if name in filled.columns
    ]
    return filled.sort_values("decisionTimeMs", ascending=False).head(limit)[columns].to_dict(orient="records")


def run(*, execute_demo: bool, symbols: tuple[str, ...]) -> int:
    print("ASE DEMO SMOKE — diagnostic context is shadow-only; risk/guardian gates remain mandatory.")
    try:
        deps, checks = _preflight()
    except (RuntimeError, SystemExit) as exc:
        print(f"PRECONDITION BLOCKED: {exc}", file=sys.stderr)
        return 2
    _print("preflight", checks)
    _print("ai_review_risk_context", _assert_ai_review_risk_context())

    before = trade_journal_summary()
    scan = run_ase_scan(
        symbols=list(symbols),
        horizon="intraday",
        write_journal=True,
        execute_trades=False,
        ingest_sources=(),
    )
    signal_rows = scan.get("signals") or []
    _print(
        "signals",
        [
            {
                "instrument": row.get("instrument"),
                "decisionStatus": row.get("decisionStatus"),
                "direction": row.get("direction"),
                "expectedNetR": row.get("expectedNetR"),
                "fxContext": row.get("fxContext"),
                "triangular": row.get("triangular"),
            }
            for row in signal_rows
        ],
    )

    executions: list[dict[str, Any]] = []
    if execute_demo:
        selected: dict[str, str] = {}
        for row in signal_rows:
            symbol = str(row.get("instrument") or "")
            if row.get("decisionStatus") != "TRADE" or not symbol:
                continue
            selected.setdefault(_venue(symbol), symbol)
        for venue, symbol in selected.items():
            # The scan calls the real ASE bridge. That bridge re-runs demo gate,
            # guardian, risk_check and then passes approval.volume to the executor.
            outcome = run_ase_scan(
                symbols=[symbol],
                horizon="intraday",
                write_journal=True,
                execute_trades=True,
                execution_deps=deps,
                ingest_sources=(),
            )
            executions.append(
                {
                    "venue": venue,
                    "instrument": symbol,
                    "executions": outcome.get("executions") or [],
                }
            )
    _print("executions", executions)

    after = trade_journal_summary()
    _print("journal", {"before": before, "after": after})
    _print("fill_watcher_source_rows", _latest_fill_rows())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ASE paper/demo readiness smoke")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry", action="store_true", help="scan and journal only; never attempt an order")
    mode.add_argument(
        "--execute-demo",
        action="store_true",
        help="attempt at most one TRADE per demo/testnet venue through the real bridge",
    )
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    return run(execute_demo=bool(args.execute_demo), symbols=tuple(args.symbols))


if __name__ == "__main__":
    raise SystemExit(main())
