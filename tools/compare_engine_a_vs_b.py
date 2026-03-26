from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from athena_legacy import load as load_legacy
from backtest import backtest_pair, backtest_pair_naked


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Engine A vs Engine B backtest results across many pairs."
    )
    parser.add_argument(
        "--asset-class",
        dest="asset_class",
        help="Filter by pair type (for example: crypto, forex, stock, commodity, index).",
    )
    parser.add_argument(
        "--enabled-only",
        action="store_true",
        help="Only include pairs currently enabled in Athena.",
    )
    parser.add_argument(
        "--pairs",
        help="Comma-separated list of pair symbols or display labels to compare.",
    )
    parser.add_argument(
        "--style",
        default="auto",
        choices=("auto", "swing", "intraday", "scalp"),
        help="Requested backtest style for both engines.",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=None,
        help="Optional maximum number of pairs to process after filtering.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print results only; do not save CSV/JSON outputs.",
    )
    parser.add_argument(
        "--output-prefix",
        help="Optional output file prefix. Defaults to reports/engine_a_vs_b_<timestamp>.",
    )
    parser.add_argument(
        "--sort-by",
        default="expectancy_gap",
        choices=("expectancy_gap", "profit_factor_gap", "pair", "engine_a_expectancy", "engine_b_expectancy"),
        help="Console leaderboard sort key.",
    )
    parser.add_argument(
        "--leaderboard-limit",
        type=int,
        default=15,
        help="Maximum rows to print in the console leaderboard.",
    )
    return parser.parse_args()


def _selected_pair_names(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _load_pairs() -> list[dict[str, Any]]:
    monolith = load_legacy()
    return list(getattr(monolith, "ALL_PAIRS", []))


def _filter_pairs(
    pairs: list[dict[str, Any]],
    *,
    asset_class: str | None,
    enabled_only: bool,
    requested_pairs: set[str],
    max_pairs: int | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    asset_class_norm = (asset_class or "").strip().lower()
    for pair in pairs:
        if asset_class_norm and str(pair.get("type", "")).strip().lower() != asset_class_norm:
            continue
        if enabled_only and not bool(pair.get("enabled", True)):
            continue
        if requested_pairs:
            symbol = str(pair.get("symbol", "")).strip().lower()
            display = str(pair.get("display", "")).strip().lower()
            if symbol not in requested_pairs and display not in requested_pairs:
                continue
        filtered.append(pair)
        if max_pairs is not None and len(filtered) >= max_pairs:
            break
    return filtered


def _is_insufficient_result(result: dict[str, Any]) -> bool:
    err = str(result.get("error", "") or "").lower()
    return "insufficient" in err and "data" in err


def _result_metric(result: dict[str, Any], key: str) -> Any:
    return result.get(key)


def _profit_factor_value(result: dict[str, Any]) -> float | None:
    total_trades = int(result.get("totalTrades", 0) or 0)
    pf = result.get("profitFactor")
    if total_trades <= 0:
        return None
    if pf is None:
        return float("inf")
    try:
        return float(pf)
    except (TypeError, ValueError):
        return None


def _winner(a_value: float | None, b_value: float | None) -> str:
    if a_value is None and b_value is None:
        return "N/A"
    if a_value is None:
        return "B"
    if b_value is None:
        return "A"
    if abs(a_value - b_value) < 1e-9:
        return "TIE"
    return "A" if a_value > b_value else "B"


def _compact_style_used(a_result: dict[str, Any], b_result: dict[str, Any], requested_style: str) -> str:
    a_style = str(a_result.get("btStyle") or requested_style)
    b_style = str(b_result.get("btStyle") or requested_style)
    return a_style if a_style == b_style else f"A:{a_style}|B:{b_style}"


def _build_row(
    pair: dict[str, Any],
    requested_style: str,
    a_result: dict[str, Any],
    b_result: dict[str, Any],
) -> dict[str, Any]:
    a_expectancy = _result_metric(a_result, "expectancy")
    b_expectancy = _result_metric(b_result, "expectancy")
    a_pf_compare = _profit_factor_value(a_result)
    b_pf_compare = _profit_factor_value(b_result)
    return {
        "pair": pair["display"],
        "symbol": pair["symbol"],
        "type": pair.get("type"),
        "styleRequested": requested_style,
        "engineAStyleUsed": a_result.get("btStyle", requested_style),
        "engineBStyleUsed": b_result.get("btStyle", requested_style),
        "styleUsed": _compact_style_used(a_result, b_result, requested_style),
        "engineATotalTrades": a_result.get("totalTrades"),
        "engineAWinRate": a_result.get("winRate"),
        "engineAProfitFactor": a_result.get("profitFactor"),
        "engineAExpectancy": a_expectancy,
        "engineASQN": a_result.get("sqn"),
        "engineASharpe": a_result.get("sharpe"),
        "engineASortino": a_result.get("sortino"),
        "engineBTotalTrades": b_result.get("totalTrades"),
        "engineBWinRate": b_result.get("winRate"),
        "engineBProfitFactor": b_result.get("profitFactor"),
        "engineBExpectancy": b_expectancy,
        "engineBSQN": b_result.get("sqn"),
        "engineBSharpe": b_result.get("sharpe"),
        "engineBSortino": b_result.get("sortino"),
        "winnerByExpectancy": _winner(
            float(a_expectancy) if a_expectancy is not None else None,
            float(b_expectancy) if b_expectancy is not None else None,
        ),
        "winnerByProfitFactor": _winner(a_pf_compare, b_pf_compare),
        "engineAError": a_result.get("error"),
        "engineBError": b_result.get("error"),
    }


def _sort_key(row: dict[str, Any], sort_by: str) -> Any:
    if sort_by == "pair":
        return row["pair"]
    if sort_by == "profit_factor_gap":
        a_pf = _profit_factor_value(
            {"totalTrades": row.get("engineATotalTrades"), "profitFactor": row.get("engineAProfitFactor")}
        )
        b_pf = _profit_factor_value(
            {"totalTrades": row.get("engineBTotalTrades"), "profitFactor": row.get("engineBProfitFactor")}
        )
        if a_pf == float("inf") or b_pf == float("inf"):
            return float("inf")
        if a_pf is None or b_pf is None:
            return -1.0
        return abs(a_pf - b_pf)
    if sort_by == "engine_a_expectancy":
        return row.get("engineAExpectancy") if row.get("engineAExpectancy") is not None else float("-inf")
    if sort_by == "engine_b_expectancy":
        return row.get("engineBExpectancy") if row.get("engineBExpectancy") is not None else float("-inf")
    a_exp = row.get("engineAExpectancy")
    b_exp = row.get("engineBExpectancy")
    if a_exp is None or b_exp is None:
        return float("-inf")
    return abs(float(a_exp) - float(b_exp))


def _print_leaderboard(rows: list[dict[str, Any]], *, sort_by: str, limit: int) -> None:
    if not rows:
        print("No comparison rows to display.")
        return

    reverse = sort_by != "pair"
    ordered = sorted(rows, key=lambda row: _sort_key(row, sort_by), reverse=reverse)[: max(1, limit)]

    print("")
    print(f"Leaderboard ({len(ordered)} rows, sorted by {sort_by})")
    print("-" * 108)
    print(
        f"{'Pair':20} {'Type':10} {'Style':14} {'A Exp':>8} {'B Exp':>8} "
        f"{'A PF':>8} {'B PF':>8} {'Win Exp':>8} {'Win PF':>8}"
    )
    print("-" * 108)
    for row in ordered:
        print(
            f"{row['pair'][:20]:20} "
            f"{str(row.get('type') or '')[:10]:10} "
            f"{str(row.get('styleUsed') or '')[:14]:14} "
            f"{_fmt_num(row.get('engineAExpectancy')):>8} "
            f"{_fmt_num(row.get('engineBExpectancy')):>8} "
            f"{_fmt_num(row.get('engineAProfitFactor')):>8} "
            f"{_fmt_num(row.get('engineBProfitFactor')):>8} "
            f"{str(row.get('winnerByExpectancy') or ''):>8} "
            f"{str(row.get('winnerByProfitFactor') or ''):>8}"
        )


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if value == float("inf"):
        return "INF"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _default_output_prefix() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return ROOT / "reports" / f"engine_a_vs_b_{stamp}"


def _save_outputs(prefix: Path, payload: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = prefix.with_suffix(".json")
    csv_path = prefix.with_suffix(".csv")

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    fieldnames = list(rows[0].keys()) if rows else [
        "pair",
        "symbol",
        "type",
        "styleRequested",
        "engineAStyleUsed",
        "engineBStyleUsed",
        "styleUsed",
        "engineATotalTrades",
        "engineAWinRate",
        "engineAProfitFactor",
        "engineAExpectancy",
        "engineASQN",
        "engineASharpe",
        "engineASortino",
        "engineBTotalTrades",
        "engineBWinRate",
        "engineBProfitFactor",
        "engineBExpectancy",
        "engineBSQN",
        "engineBSharpe",
        "engineBSortino",
        "winnerByExpectancy",
        "winnerByProfitFactor",
        "engineAError",
        "engineBError",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return csv_path, json_path


def main() -> int:
    args = _parse_args()
    requested_pairs = _selected_pair_names(args.pairs)
    pairs = _filter_pairs(
        _load_pairs(),
        asset_class=args.asset_class,
        enabled_only=args.enabled_only,
        requested_pairs=requested_pairs,
        max_pairs=args.max_pairs,
    )

    if not pairs:
        print("No pairs matched the requested filters.")
        return 0

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    print(f"Comparing {len(pairs)} pair(s) with style={args.style}...")

    for idx, pair in enumerate(pairs, start=1):
        label = pair.get("display", pair.get("symbol", f"pair-{idx}"))
        print(f"[{idx}/{len(pairs)}] {label}")
        try:
            a_result = backtest_pair(pair, style=args.style)
            b_result = backtest_pair_naked(pair, style=args.style)
        except Exception as exc:
            failures.append({"pair": label, "reason": str(exc)})
            print(f"  failed: {exc}")
            continue

        if _is_insufficient_result(a_result) or _is_insufficient_result(b_result):
            skipped.append(
                {
                    "pair": label,
                    "reason": a_result.get("error") or b_result.get("error") or "insufficient data",
                }
            )
            print("  skipped: insufficient data")
            continue

        if a_result.get("error") or b_result.get("error"):
            failures.append(
                {
                    "pair": label,
                    "reason": f"A={a_result.get('error') or 'ok'} | B={b_result.get('error') or 'ok'}",
                }
            )
            print(f"  failed: A={a_result.get('error') or 'ok'} | B={b_result.get('error') or 'ok'}")
            continue

        row = _build_row(pair, args.style, a_result, b_result)
        rows.append(row)
        print(
            "  done: "
            f"A exp={_fmt_num(row['engineAExpectancy'])}, "
            f"B exp={_fmt_num(row['engineBExpectancy'])}, "
            f"winner={row['winnerByExpectancy']}"
        )

    _print_leaderboard(rows, sort_by=args.sort_by, limit=args.leaderboard_limit)

    summary = {
        "engineAExpectancyWins": sum(1 for row in rows if row["winnerByExpectancy"] == "A"),
        "engineBExpectancyWins": sum(1 for row in rows if row["winnerByExpectancy"] == "B"),
        "expectancyTies": sum(1 for row in rows if row["winnerByExpectancy"] == "TIE"),
        "engineAProfitFactorWins": sum(1 for row in rows if row["winnerByProfitFactor"] == "A"),
        "engineBProfitFactorWins": sum(1 for row in rows if row["winnerByProfitFactor"] == "B"),
        "profitFactorTies": sum(1 for row in rows if row["winnerByProfitFactor"] == "TIE"),
    }

    print("")
    print(
        f"Completed: {len(rows)} comparisons, {len(skipped)} skipped, {len(failures)} failed."
    )
    print(
        "Winner counts: "
        f"expectancy A={summary['engineAExpectancyWins']} B={summary['engineBExpectancyWins']} TIE={summary['expectancyTies']} | "
        f"PF A={summary['engineAProfitFactorWins']} B={summary['engineBProfitFactorWins']} TIE={summary['profitFactorTies']}"
    )

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "assetClass": args.asset_class,
            "enabledOnly": args.enabled_only,
            "pairs": sorted(requested_pairs),
            "style": args.style,
            "maxPairs": args.max_pairs,
        },
        "counts": {
            "requested": len(pairs),
            "compared": len(rows),
            "skipped": len(skipped),
            "failed": len(failures),
        },
        "summary": summary,
        "skipped": skipped,
        "failed": failures,
        "rows": rows,
    }

    if args.no_save:
        print("No-save mode enabled; CSV/JSON were not written.")
        return 0

    prefix = Path(args.output_prefix) if args.output_prefix else _default_output_prefix()
    csv_path, json_path = _save_outputs(prefix, payload, rows)
    print(f"Saved CSV: {csv_path}")
    print(f"Saved JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
