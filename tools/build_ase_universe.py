#!/usr/bin/env python3
"""Generate athena_ase/universe.py from ALL_PAIRS in athena.py (single source derivation)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ATHENA = REPO / "athena.py"
OUT = REPO / "athena_ase" / "universe.py"

FOREX_MAJORS = frozenset(
    {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"}
)
CRYPTO_MAJORS = frozenset({"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"})
BENCHMARK_BY_FAMILY = {
    "forex": "USDX",
    "crypto": "BTCUSDT",
    "equity": "SPY",
    "index_etf": "SPY",
    "commodity": "XAUUSD",
}


def _load_athena_pairs() -> tuple[list[dict], dict]:
    src = ATHENA.read_text(encoding="utf-8")
    ns: dict = {}
    for name in (
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
        "_VENDOR_SYMBOL_OVERRIDES",
    ):
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
    all_pairs = (
        ns["FOREX_PAIRS"]
        + ns["COMMODITY_PAIRS"]
        + ns["INDEX_PAIRS"]
        + ns["US_STOCK_PAIRS"]
        + ns["ETF_PAIRS"]
        + ns["JSE_PAIRS"]
        + ns["CRYPTO_PAIRS"]
    )
    return all_pairs, ns["_VENDOR_SYMBOL_OVERRIDES"]


def _compact_symbol(raw: str) -> str:
    return (
        str(raw or "")
        .replace("/", "")
        .replace("=X", "")
        .replace(".US", "")
        .replace(" ", "")
        .upper()
    )


def _ase_family(pair_type: str) -> str | None:
    mapping = {
        "forex": "forex",
        "crypto": "crypto",
        "commodity": "commodity",
        "stock": "equity",
        "etf": "index_etf",
        "etf_bond": "index_etf",
        "index": "index_etf",
    }
    return mapping.get(pair_type)


def _subclass(pair: dict, family: str) -> str:
    if family == "forex":
        sym = _compact_symbol(pair.get("symbol", ""))
        return "major" if sym in FOREX_MAJORS else "cross_em"
    if family == "crypto":
        sym = _compact_symbol(pair.get("symbol", ""))
        return "major" if sym in CRYPTO_MAJORS else "alt"
    if family == "equity":
        if pair.get("source") == "jse" or ".J" in str(pair.get("symbol", "")):
            return "jse"
        return "us"
    if family == "commodity":
        return "cfd"
    return "default"


def _eodhd_symbol(pair: dict, overrides: dict) -> str:
    disp = pair.get("display", "")
    ov = overrides.get(disp, {})
    explicit = ov.get("eodhd") or ov.get("eodhd_intraday")
    if explicit == "":
        explicit = None
    if explicit:
        return str(explicit)
    ptype = pair.get("type", "")
    sym = pair.get("symbol", "")
    if ptype == "forex":
        return f"{_compact_symbol(sym)}.FOREX"
    if ptype == "crypto":
        return _compact_symbol(sym)
    if ptype in {"stock", "etf", "etf_bond"}:
        base = _compact_symbol(sym)
        return f"{base}.US" if not base.endswith(".US") else base
    if ptype == "index":
        return str(ov.get("eodhd") or _compact_symbol(sym))
    return _compact_symbol(sym)


def _mt5_live(pair: dict) -> bool:
    return bool(pair.get("enabled", True)) and str(pair.get("source")) == "mt5"


def build_rows() -> list[tuple[str, str, str, str, str, str, bool, bool]]:
    pairs, overrides = _load_athena_pairs()
    rows: list[tuple[str, str, str, str, str, str, bool, bool]] = []
    for pair in pairs:
        family = _ase_family(str(pair.get("type", "")))
        if not family:
            continue
        symbol = _compact_symbol(pair.get("symbol", ""))
        if str(pair.get("type")) == "forex" and str(pair.get("symbol", "")).endswith("=X"):
            symbol = _compact_symbol(str(pair["symbol"]).replace("=X", ""))
        subclass = _subclass(pair, family)
        eodhd = _eodhd_symbol(pair, overrides)
        benchmark = BENCHMARK_BY_FAMILY[family]
        swing_only = subclass == "jse"
        mt5_live = _mt5_live(pair)
        rows.append(
            (
                symbol,
                str(pair.get("display", symbol)),
                family,
                subclass,
                eodhd,
                benchmark,
                swing_only,
                mt5_live,
            )
        )
    return rows


def render(rows: list[tuple[str, str, str, str, str, str, bool, bool]]) -> str:
    lines = [
        '"""ASE instrument universe — derived from ALL_PAIRS (134 instruments)."""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from typing import Literal",
        "",
        "from athena_ase.data.costs import ModelFamily",
        "",
        "Horizon = Literal['intraday', 'swing']",
        "",
        "",
        "@dataclass(frozen=True)",
        "class Instrument:",
        "    symbol: str",
        "    display: str",
        "    family: ModelFamily",
        "    subclass: str",
        "    eodhd_symbol: str",
        "    benchmark: str",
        "    swing_only: bool = False",
        "    mt5_live: bool = True",
        "",
        "",
        "def compact_symbol(symbol: str) -> str:",
        '    return str(symbol or "").replace("/", "").replace(" ", "").upper()',
        "",
        "",
        "UNIVERSE: tuple[Instrument, ...] = (",
    ]
    for sym, disp, fam, sub, eod, bench, swing, mt5_live in rows:
        if swing or not mt5_live:
            lines.append(
                f'    Instrument("{sym}", "{disp}", "{fam}", "{sub}", "{eod}", "{bench}", {swing}, {mt5_live}),'
            )
        else:
            lines.append(
                f'    Instrument("{sym}", "{disp}", "{fam}", "{sub}", "{eod}", "{bench}"),'
            )
    lines.extend(
        [
            ")",
            "",
            "DEFAULT_INSTRUMENTS = UNIVERSE",
            "",
            "",
            "def instruments_for_family(family: ModelFamily) -> list[Instrument]:",
            "    return [i for i in UNIVERSE if i.family == family]",
            "",
            "",
            "def instrument_by_symbol(symbol: str) -> Instrument | None:",
            "    key = compact_symbol(symbol)",
            "    for inst in UNIVERSE:",
            "        if compact_symbol(inst.symbol) == key:",
            "            return inst",
            "    return None",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    rows = build_rows()
    OUT.write_text(render(rows), encoding="utf-8")
    print(f"wrote {OUT} with {len(rows)} instruments")


if __name__ == "__main__":
    main()
