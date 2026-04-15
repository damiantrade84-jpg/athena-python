"""Preprocess Binance data.vision aggTrade CSV files into Athena trade buckets.

Usage:
  python tools/preprocess_binance_aggtrades.py BTCUSDT path/to/BTCUSDT-aggTrades-2026-04-14.csv

The script accepts raw or zipped CSV files downloaded from
https://data.binance.vision. It stores only aggregated price-level buckets in
the same local bucket DB used by live scanning.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys
from urllib.request import urlretrieve
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from athena.microstructure.trade_bucket_store import store_trade  # noqa: E402


def _download_daily(symbol: str, date: str, out_dir: Path) -> Path:
    sym = symbol.upper()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{sym}-aggTrades-{date}.zip"
    target = out_dir / name
    if target.exists() and target.stat().st_size > 0:
        return target
    url = (
        "https://data.binance.vision/data/futures/um/daily/aggTrades/"
        f"{sym}/{name}"
    )
    urlretrieve(url, target)
    return target


def _iter_rows(path: Path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                return
            with zf.open(names[0], "r") as raw:
                text = (line.decode("utf-8").strip() for line in raw)
                yield from csv.reader(text)
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        yield from csv.reader(f)


def _is_header(row: list[str]) -> bool:
    return bool(row and not str(row[0]).strip().lstrip("-").isdigit())


def preprocess(symbol: str, files: list[Path]) -> int:
    count = 0
    for path in files:
        for row in _iter_rows(path):
            if not row or _is_header(row):
                continue
            # Binance aggTrades CSV:
            # agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker
            try:
                price = float(row[1])
                qty = float(row[2])
                ts = float(row[5]) / 1000.0
                is_buyer_maker = str(row[6]).strip().lower() == "true"
            except (IndexError, TypeError, ValueError):
                continue
            store_trade(
                exchange="binance",
                symbol=symbol,
                price=price,
                quantity=qty,
                is_buyer_maker=is_buyer_maker,
                ts=ts,
            )
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", help="Binance futures symbol, e.g. BTCUSDT")
    parser.add_argument("files", nargs="*", type=Path, help="aggTrade CSV or ZIP files")
    parser.add_argument("--date", action="append", default=[], help="Download daily futures aggTrades date YYYY-MM-DD")
    parser.add_argument("--download-dir", type=Path, default=ROOT / "data" / "binance_aggtrades")
    args = parser.parse_args()
    files = list(args.files)
    for date in args.date:
        files.append(_download_daily(args.symbol, date, args.download_dir))
    if not files:
        parser.error("provide at least one file or --date YYYY-MM-DD")
    processed = preprocess(args.symbol, files)
    print(f"processed_trades={processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
