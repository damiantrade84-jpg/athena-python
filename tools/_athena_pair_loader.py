"""Load canonical pair dicts from athena.py without importing athena.py."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ATHENA = REPO / "athena.py"

_PAIR_LIST_NAMES = (
    "FOREX_PAIRS",
    "COMMODITY_PAIRS",
    "INDEX_PAIRS",
    "US_STOCK_PAIRS",
    "ETF_PAIRS",
    "JSE_PAIRS",
    "CRYPTO_PAIRS",
)


def load_athena_pair_lists() -> dict[str, list]:
    src = ATHENA.read_text(encoding="utf-8")
    ns: dict = {}
    for name in _PAIR_LIST_NAMES:
        m = re.search(rf"^{name}\s*=\s*(\(|\[|\{{)", src, re.M)
        if not m:
            continue
        rest = src[m.start() :]
        depth = 0
        started = False
        for i, ch in enumerate(rest):
            if ch in "([{":
                depth += 1
                started = True
            elif ch in ")]}":
                depth -= 1
            if started and depth == 0:
                exec(rest[: i + 1], ns)  # noqa: S102
                break
    return ns


def find_pair_by_display(display: str) -> dict | None:
    ns = load_athena_pair_lists()
    for name in _PAIR_LIST_NAMES:
        for pair in ns.get(name, []):
            if pair.get("display") == display:
                return dict(pair)
    return None


def representative_audit_pairs() -> list[dict]:
    """One pair per score group for the SL-exit / H4 tuning audit."""
    targets = [
        "EUR/USD",
        "EUR/GBP",
        "USD/ZAR",
        "BTC/USDT",
        "SOL/USDT",
        "XAU/USD",
        "WTI Oil",
        "AAPL",
        "SPY",
        "TLT",
    ]
    pairs: list[dict] = []
    missing: list[str] = []
    for display in targets:
        pair = find_pair_by_display(display)
        if pair is None:
            missing.append(display)
        else:
            pairs.append(pair)
    if missing:
        raise ValueError(f"Missing canonical pair dicts for: {', '.join(missing)}")
    return pairs
