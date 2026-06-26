"""Report Engine A/B scan universe sizes (mirrors scanner.run_full_scan pair selection)."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATHENA_PY = ROOT / "athena.py"
TOGGLE_STATE = ROOT / "toggle_state.json"


def _load_pair_lists() -> dict[str, list[dict]]:
    src = ATHENA_PY.read_text(encoding="utf-8")
    out: dict[str, list[dict]] = {}
    for name in (
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    ):
        m = re.search(rf"{name}\s*=\s*\[", src)
        if not m:
            continue
        start = m.end() - 1
        depth = 0
        for i, ch in enumerate(src[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    out[name] = ast.literal_eval(src[start : i + 1])
                    break
    return out


def _apply_toggle_state(all_pairs: list[dict]) -> None:
    if not TOGGLE_STATE.is_file():
        return
    state = json.loads(TOGGLE_STATE.read_text(encoding="utf-8"))
    for p in all_pairs:
        disp = p.get("display")
        if disp in state:
            p["enabled"] = bool(state[disp])


def _scan_counts(
    all_pairs: list[dict],
    jse_pairs: list[dict],
    *,
    asset_class: str | None,
    disabled_displays: set[str] | None = None,
) -> tuple[int, int, list[str]]:
    disabled = disabled_displays or set()
    disabled_jse = {
        p.get("symbol")
        for p in jse_pairs
        if not p.get("enabled", True)
    }
    candidates = [
        p
        for p in all_pairs
        if p.get("display") not in disabled
        and p.get("symbol") not in disabled_jse
    ]
    ac = asset_class.lower().strip() if asset_class else None
    if ac:
        if ac == "etf":
            candidates = [p for p in candidates if p.get("type") in ("etf", "etf_bond")]
        else:
            candidates = [p for p in candidates if p.get("type") == ac]
    active = [p for p in candidates if p.get("enabled", True)]
    return len(candidates), len(active), [str(p.get("display")) for p in candidates]


def main() -> int:
    lists = _load_pair_lists()
    all_pairs: list[dict] = []
    for key in (
        "FOREX_PAIRS",
        "COMMODITY_PAIRS",
        "INDEX_PAIRS",
        "US_STOCK_PAIRS",
        "ETF_PAIRS",
        "JSE_PAIRS",
        "CRYPTO_PAIRS",
    ):
        all_pairs.extend(lists.get(key, []))
    _apply_toggle_state(all_pairs)
    jse_pairs = lists.get("JSE_PAIRS", [])

    classes = [None, "forex", "commodity", "stock", "crypto", "index", "etf"]
    print("Engine A scan universe (same logic as scanner.run_full_scan)\n")
    print(f"{'asset_class':<14} {'total':>6} {'active':>7}  note")
    print("-" * 60)
    matches_21: list[str] = []
    for ac in classes:
        total, active, _ = _scan_counts(all_pairs, jse_pairs, asset_class=ac)
        label = ac or "(all)"
        note = ""
        if total == 21 and active == 21:
            note = "<-- matches 21/21 scan"
            if ac:
                matches_21.append(ac)
        print(f"{label:<14} {total:>6} {active:>7}  {note}")

    print()
    if matches_21:
        print(
            "21/21 scan fingerprint: asset_class filter is one of "
            + ", ".join(matches_21)
        )
    else:
        print("No single asset_class produces exactly 21/21 with current rosters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
