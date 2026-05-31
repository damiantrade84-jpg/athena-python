from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


LIMITS = {"D1": 750, "H4": 4400, "H1": 17600}
MIN_BARS = {"D1": 230, "H4": 500, "H1": 500}


def _load_athena(repo_root: Path):
    spec = importlib.util.spec_from_file_location("athena_freeze_dataset", repo_root / "athena.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {repo_root / 'athena.py'}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _selected_pairs(all_pairs: list[dict[str, Any]], spec: str) -> list[dict[str, Any]]:
    by_display = {str(p.get("display")): p for p in all_pairs}
    if not spec or spec.strip().lower() in {"all", "*"}:
        return list(all_pairs)
    wanted = [x.strip() for x in spec.split(",") if x.strip()]
    missing = [x for x in wanted if x not in by_display]
    if missing:
        raise SystemExit(f"unknown pair(s): {', '.join(missing)}")
    return [by_display[x] for x in wanted]


def _normalise_tfs(value: str) -> list[str]:
    tfs = {x.strip().upper() for x in str(value or "").split(",") if x.strip()}
    # Engine A backtests require D1 context even when callers request only H4/H1.
    tfs.add("D1")
    return [tf for tf in ("D1", "H4", "H1") if tf in tfs]


def _crypto_provider(engine: str, config: dict[str, Any]) -> str:
    from athena_app.services.crypto_signal_feed import resolve_crypto_signal_feed

    feed = resolve_crypto_signal_feed(engine, config)
    return "bybit_linear_kline" if feed == "bybit" else "binance_futures"


def _freeze_factor_values(data_as_of: str, pair: dict[str, Any], candles_by_tf: dict[str, list[dict]]):
    from frozen_data import write_frozen_factor_values
    from cot_feed import get_cot_z
    from carry_feed import get_carry_z
    from vol_skew_feed import get_vol_skew_z

    dates = sorted(
        {
            str(c.get("time") or c.get("datetime") or "")[:10]
            for rows in candles_by_tf.values()
            for c in (rows or [])
            if str(c.get("time") or c.get("datetime") or "")[:10]
        }
    )
    display = str(pair.get("display") or pair.get("symbol") or "")
    cot_values = {d: get_cot_z(display, as_of_date=d) for d in dates}
    carry_values = {d: get_carry_z(display, as_of_date=d) for d in dates}
    vol_values = {d: get_vol_skew_z(display, as_of_date=d) for d in dates}
    return [
        write_frozen_factor_values(data_as_of, display, "cot_z", cot_values),
        write_frozen_factor_values(data_as_of, display, "carry_z", carry_values),
        write_frozen_factor_values(data_as_of, display, "vol_skew_z", vol_values),
    ]


def _freeze_crypto_derivatives(data_as_of: str, pair: dict[str, Any], candles_by_tf: dict[str, list[dict]]):
    if pair.get("type") != "crypto":
        return []
    from data_feeds import prepare_crypto_backtest_derivative_series
    from frozen_data import write_frozen_rows

    funding_rows, oi_rows = prepare_crypto_backtest_derivative_series(
        pair,
        candles_by_tf.get("D1"),
        candles_by_tf.get("H4"),
        candles_by_tf.get("H1"),
    )
    provider = "crypto_derivatives"
    return [
        write_frozen_rows(data_as_of, pair, "funding", provider, funding_rows),
        write_frozen_rows(data_as_of, pair, "oi", provider, oi_rows),
    ]


def _freeze_pair(data_as_of: str, pair: dict[str, Any], tfs: list[str], config: dict[str, Any]):
    import backtest_runner as br
    from frozen_data import write_frozen_candles

    source = str(pair.get("source") or "")
    ptype = str(pair.get("type") or "")
    pair_copy = copy.deepcopy(pair)
    records = []
    candles_by_tf: dict[str, list[dict]] = {}

    if source == "binance":
        provider = _crypto_provider("A", config)
        for tf in tfs:
            candles = br._crypto_bt_signal_candles(
                pair_copy,
                engine="A",
                tf=tf,
                limit=LIMITS.get(tf, 4400),
                min_bars=MIN_BARS.get(tf, 500),
            )
            candles_by_tf[tf] = candles or []
            records.append(write_frozen_candles(data_as_of, pair_copy, tf, provider, candles))
    elif source == "mt5":
        for tf in tfs:
            candles = br._bt_cached_fetch(
                pair_copy,
                tf,
                LIMITS.get(tf, 4400),
                lambda lim, _tf=tf: br._rt().fetch_candles(pair_copy, _tf, lim),
                provider="mt5",
                min_bars=MIN_BARS.get(tf, 500),
            )
            candles_by_tf[tf] = candles or []
            records.append(write_frozen_candles(data_as_of, pair_copy, tf, "mt5", candles))
    elif ptype in ("stock", "commodity", "index"):
        if "D1" in tfs:
            d1 = br._bt_cached_fetch(
                pair_copy,
                "D1",
                LIMITS["D1"],
                lambda lim: br._rt().extract_candles(br._rt().fetch_eodhd(pair_copy, "D1", lim)),
                provider="eodhd",
                min_bars=MIN_BARS["D1"],
            )
            candles_by_tf["D1"] = d1 or []
            records.append(write_frozen_candles(data_as_of, pair_copy, "D1", "eodhd", d1))
        need_intraday = any(tf in tfs for tf in ("H4", "H1"))
        if need_intraday:
            h4, h1 = br._bt_cached_eodhd_intraday(pair_copy, days=730)
            if "H4" in tfs:
                candles_by_tf["H4"] = h4 or []
                records.append(write_frozen_candles(data_as_of, pair_copy, "H4", "eodhd_intraday", h4))
            if "H1" in tfs:
                candles_by_tf["H1"] = h1 or []
                records.append(write_frozen_candles(data_as_of, pair_copy, "H1", "eodhd_intraday", h1))
    else:
        provider = str(pair_copy.get("source") or "fallback")
        for tf in tfs:
            candles = br._bt_cached_fetch(
                pair_copy,
                tf,
                LIMITS.get(tf, 4400),
                lambda lim, _tf=tf: br._rt().fetch_candles(pair_copy, _tf, lim),
                provider=provider,
                min_bars=MIN_BARS.get(tf, 500),
            )
            candles_by_tf[tf] = candles or []
            records.append(write_frozen_candles(data_as_of, pair_copy, tf, provider, candles))

    records.extend(_freeze_crypto_derivatives(data_as_of, pair_copy, candles_by_tf))
    records.extend(_freeze_factor_values(data_as_of, pair_copy, candles_by_tf))
    if pair_copy.get("display") == "XAU/USD":
        dxy_h4, _ = br._rt().fetch_bt_yfinance("DX-Y.NYB")
        dxy_pair = {
            "display": "DX-Y.NYB",
            "symbol": "DX-Y.NYB",
            "source": "yfinance",
            "type": "macro",
        }
        records.append(write_frozen_candles(data_as_of, dxy_pair, "H4", "yfinance", dxy_h4))
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True, help="YYYY-MM-DD dataset cutoff")
    parser.add_argument("--pairs", required=True, help="Comma-separated display names or all")
    parser.add_argument("--tfs", default="D1,H4,H1", help="Comma-separated timeframes; D1 is added automatically")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    os.environ["ATHENA_FREEZE_BUILDING"] = "1"
    os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
    os.environ.pop("BACKTEST_DATA_AS_OF", None)

    athena = _load_athena(repo_root)
    from config import CONFIG
    from frozen_data import manifest_path

    tfs = _normalise_tfs(args.tfs)
    pairs = _selected_pairs(list(athena.ALL_PAIRS), args.pairs)
    all_records = []
    for pair in pairs:
        print(f"[FREEZE] {pair.get('display')} tfs={','.join(tfs)}")
        records = _freeze_pair(args.as_of, pair, tfs, CONFIG)
        all_records.extend(records)
        print(f"[FREEZE] {pair.get('display')} records={len(records)}")

    path = manifest_path(args.as_of)
    print(json.dumps({"manifest": str(path), "pairs": len(pairs), "records": len(all_records)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
