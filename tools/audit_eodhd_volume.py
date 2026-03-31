"""Audit EODHD volume coverage for tracked stocks, commodities, and indices."""

from __future__ import annotations

from collections import Counter
import importlib.util
from pathlib import Path
import sys

from dotenv import load_dotenv


def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    load_dotenv(root / ".env")


def _status_summary(resp: dict) -> tuple[str, str]:
    candles = resp.get("candles") if isinstance(resp, dict) else None
    if not candles:
        return "FAIL", str(resp.get("detail") or "no candles")
    nonzero = sum(1 for c in candles if float(c.get("vol", 0) or 0) > 0)
    if nonzero <= 0:
        return "FAIL", "all returned volume bars were zero"
    last = candles[-1]
    return "OK", f"{nonzero}/{len(candles)} non-zero | last_vol={float(last.get('vol', 0) or 0):.2f}"


def main() -> int:
    _load_env()
    root = Path(__file__).resolve().parents[1]
    from eodhd_volume_overlay import is_eodhd_volume_whitelisted

    spec = importlib.util.spec_from_file_location("athena_monolith", root / "athena.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load athena.py")
    athena = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena)

    pairs = [
        *[p for p in athena.COMMODITY_PAIRS if p.get("enabled", True)],
        *[p for p in athena.INDEX_PAIRS if p.get("enabled", True)],
        *[p for p in athena.US_STOCK_PAIRS if p.get("enabled", True)],
        *[p for p in athena.ETF_PAIRS if p.get("enabled", True)],
    ]

    print(f"Auditing EODHD volume coverage for {len(pairs)} enabled pairs\n")
    failures = []
    counts: Counter[tuple[str, str]] = Counter()
    skipped = []

    for pair in pairs:
        display = pair.get("display", pair.get("symbol", "?"))
        asset_type = pair.get("type", "?")
        print(f"{display} [{asset_type}]")
        for tf, limit in (("D1", 120), ("H1", 240), ("H4", 240)):
            if not is_eodhd_volume_whitelisted(pair, tf):
                counts[(tf, "SKIP")] += 1
                skipped.append(
                    {
                        "pair": display,
                        "type": asset_type,
                        "tf": tf,
                    }
                )
                print(f"  {tf}: SKIP whitelist")
                continue
            resp = athena._fetch_eodhd_volume_only(pair, tf, limit)
            status, detail = _status_summary(resp)
            counts[(tf, status)] += 1
            ticker = resp.get("ticker") or "-"
            print(f"  {tf}: {status:<4} ticker={ticker} :: {detail}")
            if status != "OK":
                failures.append(
                    {
                        "pair": display,
                        "type": asset_type,
                        "tf": tf,
                        "ticker": ticker,
                        "detail": detail,
                    }
                )
        print()

    print("Summary")
    for tf in ("D1", "H1", "H4"):
        ok = counts[(tf, "OK")]
        fail = counts[(tf, "FAIL")]
        skip = counts[(tf, "SKIP")]
        print(f"  {tf}: OK={ok} FAIL={fail} SKIP={skip}")

    if failures:
        print("\nFailures")
        for row in failures:
            print(
                f"  {row['pair']} [{row['type']}] {row['tf']} ticker={row['ticker']} :: {row['detail']}"
            )

    if skipped:
        print("\nSkipped")
        for row in skipped:
            print(f"  {row['pair']} [{row['type']}] {row['tf']} :: not whitelisted")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
