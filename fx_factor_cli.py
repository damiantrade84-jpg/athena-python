#!/usr/bin/env python3
"""CLI for athena_fx research backtests (paper/research only — no execution).

Usage:
  python fx_factor_cli.py status
  python fx_factor_cli.py prepare --symbols EURUSD,GBPUSD,AUDJPY
  python fx_factor_cli.py run --start 2020-01-01 --end 2024-12-31 --symbols EURUSD,GBPUSD
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from athena_fx.backtest import FxBacktestRequest, run_fx_factor_backtest
from athena_fx.backtest_data import (
    ensure_d1_bars_mt5,
    g8_pairs_with_cached_bars,
    load_daily_bars,
)
from athena_fx.models import G8_CURRENCIES, G8_PAIRS
from athena_fx import store as fx_store
from athena_fx.swap_audit import run_swap_audit
from athena_fx.total_return import build_total_return_series, persist_total_return
from athena_fx.value import ingest_reer_rows

log = logging.getLogger("fx_factor_cli")


def _parse_symbols(raw: str | None) -> list[str]:
    if not raw:
        return list(G8_PAIRS)
    return [s.replace("/", "").upper() for s in raw.split(",") if s.strip()]


def cmd_status(_args: argparse.Namespace) -> int:
    from runtime_paths import resolve_backtest_candle_cache_db_path
    import os
    import sqlite3

    cache = str(resolve_backtest_candle_cache_db_path())
    print(f"backtest_candle_cache: {cache} (exists={os.path.exists(cache)})")
    if os.path.exists(cache):
        con = sqlite3.connect(cache)
        rows = con.execute(
            "SELECT display, COUNT(*) FROM backtest_candles WHERE timeframe='D1' "
            "GROUP BY display ORDER BY COUNT(*) DESC LIMIT 12"
        ).fetchall()
        print(f"  top D1 pairs ({len(rows)} shown):")
        for disp, n in rows:
            print(f"    {disp}: {n} bars")
        con.close()

    db = fx_store.resolve_db_path()
    print(f"fx_factor.db: {db}")
    for table in ("swap_audit", "total_return", "reer_observations", "backtest_runs"):
        try:
            with fx_store.connect(db) as con:
                n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {n} rows")
        except Exception as exc:
            print(f"  {table}: {exc}")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols)
    db_path = args.db_path or None

    if not args.skip_bars:
        print(f"Ensuring D1 bars in backtest cache for {len(symbols)} symbols (MT5)...")
        bar_diags = ensure_d1_bars_mt5(symbols)
        ready = g8_pairs_with_cached_bars()
        in_scope = [s for s in symbols if s in ready]
        print(f"  D1 cache: {len(in_scope)}/{len(symbols)} symbols have bars")
        for d in bar_diags:
            if d.severity in ("warning", "error"):
                print(f"  bar {d.severity}: {d.code} — {d.message}")

    if not args.skip_swap:
        print(f"Fetching MT5 swap audit for {len(symbols)} symbols...")
        rows = run_swap_audit(symbols, db_path=db_path)
        ok = sum(1 for r in rows if r.status == "ok")
        print(f"  swap audit: {ok}/{len(rows)} ok")
        if ok == 0:
            print("  WARNING: no valid swap rows — backtest will fail closed unless --no-strict on run")

    if not args.skip_reer:
        print("Fetching BIS REER for G8 currencies (network)...")
        try:
            import yaml
            from athena_research.forex_edge.sources.bis import fetch_bis_reer, parse_bis_reer_csv

            cfg_path = Path("configs/forex_edge_research.yaml")
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            bis = cfg["sources"]["bis"]
            base_url = bis["base_url"]
            series_map = bis["reer_series"]
            reer_rows: list[dict] = []
            for ccy in G8_CURRENCIES:
                key = series_map.get(ccy)
                if not key:
                    continue
                try:
                    _url, content = fetch_bis_reer(base_url, key, start="2000-01")
                    frame = parse_bis_reer_csv(content, currency=ccy, series_key=key)
                    for _, row in frame.iterrows():
                        ts = row["timestamp"]
                        obs_month = ts.strftime("%Y-%m")
                        pub = row["available_time"].strftime("%Y-%m-%d")
                        reer_rows.append({
                            "currency": ccy,
                            "observation_month": obs_month,
                            "reer_value": float(row["value"]),
                            "publication_date": pub,
                            "source": "bis",
                        })
                    print(f"  REER {ccy}: {len(frame)} months")
                except Exception as exc:
                    print(f"  REER {ccy}: FAILED ({exc})")
            if reer_rows:
                n = ingest_reer_rows(reer_rows, db_path=db_path)
                print(f"  persisted {n} REER observations")
        except Exception as exc:
            print(f"  REER ingest skipped: {exc}")

    print("Building total-return series from candle cache + swap audit...")
    start = args.history_start or "2000-01-01"
    end = args.history_end or "2026-12-31"
    bars_map, bar_diags = load_daily_bars(symbols, start, end, db_path=None)
    if bar_diags:
        for d in bar_diags:
            print(f"  bar warning: {d.code} {d.message} {d.context}")

    built = 0
    for sym, bars in bars_map.items():
        audit_rows = fx_store.load_latest_swap_audit(symbol=sym, db_path=db_path)
        audit = audit_rows[0] if audit_rows else None
        bar_fmt = [{"time": b["date"], **b} for b in bars]
        try:
            tr_rows = build_total_return_series(
                bar_fmt, audit, symbol=sym, strict=False,
            )
            persist_total_return(tr_rows, db_path=db_path)
            built += len(tr_rows)
            print(f"  {sym}: {len(tr_rows)} total-return rows")
        except Exception as exc:
            print(f"  {sym}: total-return FAILED ({exc})")

    print(f"Done. Total-return rows written: {built}")
    return 0


def _spread_map_from_config(symbols: list[str]) -> dict[str, float]:
    from config import CONFIG

    raw = CONFIG.get("FX_FACTOR_SPREAD_PIPS_BY_SYMBOL") or {}
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for sym in symbols:
        key = sym.replace("/", "").upper()
        val = raw.get(key) or raw.get(f"{key[:3]}/{key[3:]}")
        if val is not None:
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                pass
    return out


def cmd_run(args: argparse.Namespace) -> int:
    from athena_fx.pipeline import decide_for_backtest

    symbols = _parse_symbols(args.symbols)
    db_path = args.db_path or None

    if not args.skip_bars:
        print(f"Ensuring D1 bars for {len(symbols)} G8 symbols...")
        bar_fetch_diags = ensure_d1_bars_mt5(symbols)
        for d in bar_fetch_diags:
            if d.severity in ("warning", "error"):
                print(f"  bar {d.severity}: {d.code} — {d.message}")

    bars_map, bar_diags = load_daily_bars(symbols, args.start, args.end)
    if not bars_map:
        print("ERROR: no D1 bars loaded. Run: python fx_factor_cli.py prepare")
        for d in bar_diags:
            print(f"  {d.code}: {d.message}")
        return 1

    missing = [s for s in symbols if s not in bars_map]
    if missing:
        print(f"WARNING: no bars for: {', '.join(missing)}")
    print(f"Loaded D1 bars for {len(bars_map)}/{len(symbols)} symbols")

    swap_returns: dict[str, dict] = {}
    for sym in symbols:
        audits = fx_store.load_latest_swap_audit(symbol=sym, db_path=db_path)
        if not audits or audits[0].get("status") != "ok":
            continue
        a = audits[0]
        swap_returns[sym] = {
            "long_daily": float(a.get("daily_swap_return_long") or 0.0),
            "short_daily": float(a.get("daily_swap_return_short") or 0.0),
            "rollover3days": int(a.get("swap_rollover3days") or 3),
        }

    spread_map = _spread_map_from_config([s for s in symbols if s in bars_map])
    if args.spread_pips:
        for part in args.spread_pips.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                spread_map[k.replace("/", "").upper()] = float(v)

    req = FxBacktestRequest(
        start=args.start,
        end=args.end,
        symbols=[s for s in symbols if s in bars_map],
        strict_factor_data=not args.no_strict,
        cost_model_enabled=not args.no_cost,
        spread_pips_by_symbol=spread_map,
    )

    print(f"Running backtest {req.start} .. {req.end} | {len(req.symbols)} symbols | strict={req.strict_factor_data}")
    report = run_fx_factor_backtest(
        req,
        bars_by_symbol=bars_map,
        decide_fn=decide_for_backtest,
        swap_returns_by_symbol=swap_returns or None,
    )

    summary = report.get("summary") or {}
    print("\n=== BACKTEST SUMMARY ===")
    print(f"run_id:    {report.get('run_id')}")
    print(f"status:    {report.get('status')}")
    print(f"trades:    {summary.get('trade_count')}")
    print(f"win_rate:  {summary.get('win_rate')}")
    print(f"expect_r:  {summary.get('expectancy_r')}")
    print(f"sharpe:    {summary.get('sharpe')}")
    print(f"sqn:       {summary.get('sqn')}")
    print(f"max_dd:    {summary.get('max_drawdown_pct')}%")
    print(f"turnover:  {summary.get('turnover')}")
    print(f"swap_pnl:  {summary.get('swap_contribution')}")
    print(f"cost_drag: {summary.get('cost_drag')}")

    diags = report.get("diagnostics") or []
    if diags:
        print(f"\nDiagnostics ({len(diags)}):")
        for d in diags[:15]:
            if isinstance(d, dict):
                print(f"  {d.get('code')}: {d.get('message', d)}")
            else:
                print(f"  {d}")
        if len(diags) > 15:
            print(f"  ... and {len(diags) - 15} more")

    flags = report.get("pair_sanity_flags") or []
    if flags:
        print("\nPair sanity flags:")
        for f in flags:
            print(f"  - {f}")

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull report written to {out}")

    return 0 if report.get("status") == "completed" else 2


def cmd_rebalance(args: argparse.Namespace) -> int:
    from athena_fx.rebalance import run_fx_factor_rebalance

    symbols = _parse_symbols(args.symbols) if args.symbols else None
    result = run_fx_factor_rebalance(
        symbols=symbols,
        execute_trades=bool(args.execute),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 2


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="athena_fx research backtest CLI")
    p.add_argument("--db-path", help="Override ATHENA_FX_DB_PATH")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show data readiness")

    prep = sub.add_parser("prepare", help="Swap audit + REER + total-return series")
    prep.add_argument("--symbols", help="Comma-separated 6-char symbols (default: all G8)")
    prep.add_argument("--skip-bars", action="store_true", help="Skip MT5 D1 bar cache fetch")
    prep.add_argument("--skip-swap", action="store_true", help="Skip MT5 swap audit")
    prep.add_argument("--skip-reer", action="store_true", help="Skip BIS REER fetch")
    prep.add_argument("--history-start", default="2000-01-01")
    prep.add_argument("--history-end", default="2026-12-31")

    run = sub.add_parser("run", help="Run weekly factor backtest")
    run.add_argument("--start", required=True, help="YYYY-MM-DD")
    run.add_argument("--end", required=True, help="YYYY-MM-DD")
    run.add_argument("--symbols", help="Comma-separated symbols (default: G8 28)")
    run.add_argument("--skip-bars", action="store_true", help="Skip MT5 D1 bar cache fetch")
    run.add_argument("--no-strict", action="store_true", help="Allow degraded factor data")
    run.add_argument("--no-cost", action="store_true", help="Disable cost model")
    run.add_argument("--spread-pips", help="Override spreads: EURUSD=1.0,GBPUSD=1.3")
    run.add_argument("--json", help="Write full report JSON to path")

    reb = sub.add_parser("rebalance", help="Refresh decisions and optionally execute demo MT5 rebalance")
    reb.add_argument("--execute", action="store_true", help="Send orders (requires FX_FACTOR_EXECUTION_ENABLED)")
    reb.add_argument("--dry-run", action="store_true", help="Plan + signal build without broker send")
    reb.add_argument("--symbols", help="Comma-separated symbols (default: G8)")

    args = p.parse_args()
    if args.db_path:
        import os
        os.environ["ATHENA_FX_DB_PATH"] = args.db_path

    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "prepare":
        return cmd_prepare(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "rebalance":
        return cmd_rebalance(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
