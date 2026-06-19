"""Engine A factor-ablation harness (research-only, demo/no-execution).

Drives the SHADOW scorer (and the production scorer as the "current" baseline)
over frozen point-in-time data, using the SAME entry/exit/cost machinery as the
production V3 backtest so that every variant differs ONLY in the scorer:

  * entries/levels  -> engine_a_v3.evaluator._build_levels
  * fills/exits     -> engine_a_v3.backtest._simulate_exit
  * costs           -> engine_a_v3.backtest._cost_r

Per bar we call compute_components ONCE (the expensive indicator step) and then
aggregate every variant cheaply. Trades are recorded pre-cost (raw R) plus a
per-trade cost_r so any cost multiple (1.0/1.5/2.0x) can be applied downstream.

Nothing here writes to live state, Engine B, Engine D, or any executor.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from engine_a_v3.backtest import _cost_r, _simulate_exit
from engine_a_v3.evaluator import _build_levels, _parse_time
from engine_a_v3.profile import baseline_profile
from engine_a_v3.routing import route_specialist
from engine_a_v3.setups import _efficiency_ratio

from athena_research.engine_a_ablation.context_builder import build_pit_context
from athena_research.engine_a_ablation.shadow_scorer import (
    _RESEARCH_WEIGHTS,
    PRICE_FACTORS,
    aggregate,
    compute_components,
)

# Sourced subsystems this pass (leak-free PIT data + verified orientation).
SOURCED_SUBSYSTEMS = ("carry", "sentiment")

# Representative round-trip costs in bps per family (spread/commission/slippage)
# + per-day carry/swap. Deliberately conservative; documented assumptions.
_COSTS: dict[str, dict[str, float]] = {
    "forex": {"spread": 1.0, "commission": 0.5, "slippage": 0.5, "swap": 0.3},
    "crypto": {"spread": 5.0, "commission": 6.0, "slippage": 5.0, "swap": 30.0},
    "commodity": {"spread": 4.0, "commission": 2.0, "slippage": 3.0, "swap": 2.0},
    "index": {"spread": 2.0, "commission": 1.0, "slippage": 2.0, "swap": 1.0},
    "equity_etf": {"spread": 3.0, "commission": 2.0, "slippage": 3.0, "swap": 1.0},
    "unknown": {"spread": 3.0, "commission": 2.0, "slippage": 3.0, "swap": 1.0},
}


@dataclass
class HarnessConfig:
    horizon: str = "swing"          # swing -> H4 primary
    max_hold_bars: int = 30
    warmup: int = 250
    min_d1: int = 220
    step: int = 1                   # evaluate every Nth H4 bar (decision axis only)
    prefix_cap: int = 700           # trailing bars fed to indicators (EMA200-converged)
    score_buckets: tuple[float, ...] = (2.0, 2.2, 2.4, 2.6)  # confluence bucket edges
    cost_multiples: tuple[float, ...] = (1.0, 1.5, 2.0)


def build_variants(family: str) -> dict[str, dict]:
    """Return {variant_name: {"mode": "current"|"shadow", "weights": {...}|None}}.

    Variants (all hold the threshold constant; only the scorer weights differ):
      current            production score_pair (no context) — baseline
      shadow_full        price + sourced subsystems (hybrid denominator)
      shadow_price_only  price factors only (subsystems weight 0 -> na)
      only_<factor>      single-factor model
      loo_<factor>       full model minus one factor (incremental-lift basis)
    """
    base = dict(_RESEARCH_WEIGHTS.get(family, _RESEARCH_WEIGHTS["unknown"]))
    price_only = {k: (base.get(k, 0.0) if k in PRICE_FACTORS else 0.0) for k in base}
    variants: dict[str, dict] = {
        "current": {"mode": "current", "weights": None},
        "shadow_full": {"mode": "shadow", "weights": dict(base)},
        "shadow_price_only": {"mode": "shadow", "weights": price_only},
    }
    measurable = [f for f in PRICE_FACTORS if base.get(f, 0.0) > 0] + [
        f for f in SOURCED_SUBSYSTEMS if base.get(f, 0.0) > 0
    ]
    for f in measurable:
        variants[f"only_{f}"] = {
            "mode": "shadow",
            "weights": {k: (base[k] if k == f else 0.0) for k in base},
        }
        variants[f"loo_{f}"] = {
            "mode": "shadow",
            "weights": {k: (0.0 if k == f else base.get(k, 0.0)) for k in base},
        }
    return variants


def load_universe(manifest_path: str | Path, *, primary_tf: str = "H4") -> list[dict]:
    """Enumerate backtestable symbols from a frozen manifest. Returns pair dicts
    with the D1/H4/H1 candle file paths attached."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    series = manifest.get("series", {})
    by_display: dict[str, dict] = {}
    for _key, rec in series.items():
        if rec.get("kind") != "candles":
            continue
        tf = rec.get("timeframe")
        if tf not in ("D1", "H4", "H1"):
            continue
        display = rec.get("display")
        slot = by_display.setdefault(
            display,
            {"display": display, "symbol": rec.get("symbol") or display,
             "type": rec.get("asset_type") or "other", "paths": {}},
        )
        slot["paths"][tf] = rec.get("path")
    # Keep only symbols with all three timeframes.
    return [p for p in by_display.values()
            if {"D1", "H4", "H1"}.issubset(p["paths"]) and primary_tf in p["paths"]]


def _load_rows(path: str | Path, frozen_root: Path) -> list[dict]:
    full = frozen_root / str(path)
    data = json.loads(full.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else (data.get("candles") or data.get("rows") or [])


def _bucket_label(score: float, edges: tuple[float, ...]) -> str:
    for i, e in enumerate(edges):
        if score < e:
            return f"b{i}_<{e}"
    return f"b{len(edges)}_>={edges[-1]}"


def _regime(h4_prefix: list[dict]) -> str:
    if len(h4_prefix) < 20:
        return "unknown"
    eff, _ = _efficiency_ratio(h4_prefix, 20)
    if eff >= 0.3:
        return "trending"
    if eff <= 0.2:
        return "ranging"
    return "neutral"


def run_symbol(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    cfg: HarnessConfig,
    variants: dict[str, dict],
) -> list[dict]:
    """Walk confirmed H4 prefixes; produce trades for every variant with
    identical entry/exit/cost machinery. Returns a flat list of trade dicts."""
    from engine_a_v3.quant_scorer import score_pair as production_score_pair

    route = route_specialist(pair)
    family = route.family
    profile = baseline_profile(route.score_group, cfg.horizon)
    costs = _COSTS.get(family, _COSTS["unknown"])
    primary = list(candles.get("H4") or [])
    n = len(primary)
    if n < cfg.warmup + 5:
        return []

    # Pre-index timeframe rows + times for O(log n) prefix slicing via bisect.
    tf_rows = {tf: list(rows or []) for tf, rows in candles.items()}
    tf_times = {tf: [str(c.get("time") or c.get("datetime")) for c in rows]
                for tf, rows in tf_rows.items()}
    cap = cfg.prefix_cap
    trades: list[dict] = []
    cooldown = {name: cfg.warmup for name in variants}

    for index in range(cfg.warmup, n - 1):
        if cfg.step > 1 and (index - cfg.warmup) % cfg.step != 0:
            continue  # thin the DECISION axis only; full history feeds indicators/exits
        cutoff = str(primary[index].get("time") or primary[index].get("datetime"))
        as_of_date = cutoff[:10]
        # Available-history counts (point-in-time) before capping.
        d1_n = bisect_right(tf_times.get("D1", []), cutoff)
        h4_n = bisect_right(tf_times.get("H4", []), cutoff)
        if d1_n < cfg.min_d1 or h4_n < 60:
            continue
        # Trailing-window prefixes: EMA200 fully converges within ~600 bars, so
        # the LATEST snapshot equals the full-history value (parity-preserving)
        # while cutting per-bar indicator cost dramatically.
        prefix = {}
        for tf, rows in tf_rows.items():
            idx = bisect_right(tf_times[tf], cutoff)
            prefix[tf] = rows[max(0, idx - cap): idx]
        h4_prefix = prefix["H4"]

        # Point-in-time subsystem context (carry/COT at the decision date).
        context = build_pit_context(pair["display"], as_of_date)

        # Expensive indicator step: ONCE per bar.
        bar = compute_components(route, cfg.horizon, prefix, context=context, profile=profile)

        # Production baseline reuses the same prefix (no context, 4-factor).
        prod = production_score_pair(route, cfg.horizon, prefix, context=None, profile=profile)
        regime = _regime(h4_prefix)
        entry_bar = primary[index + 1]

        for name, spec in variants.items():
            if index < cooldown[name]:
                continue
            if spec["mode"] == "current":
                direction = prod.direction if prod.direction in ("LONG", "SHORT") else None
                decision = prod.decision
                level_style = prod.level_style
                conf = prod.confluence_score
            else:
                sc = aggregate(bar, weights=spec["weights"], threshold=profile.trade_threshold)
                direction = sc.direction if sc.direction in ("LONG", "SHORT") else None
                decision = sc.decision
                level_style = sc.level_style
                conf = sc.confluence_score
            if decision != "TRADE" or direction is None:
                continue
            levels = _build_levels(h4_prefix, direction=direction, level_style=level_style)
            if levels is None or levels.entry_zone is None:
                continue

            zlo, zhi = float(levels.entry_zone.low), float(levels.entry_zone.high)
            blo, bhi = float(entry_bar["low"]), float(entry_bar["high"])
            if bhi < zlo or blo > zhi:
                continue
            bopen = float(entry_bar["open"])
            entry = (min(max(bopen, zlo), zhi) if direction == "LONG"
                     else max(min(bopen, zhi), zlo))
            sl = float(levels.invalidation)
            tgts = levels.targets
            if not tgts:
                continue
            tp1 = float(tgts[0].price)
            tp2 = float(tgts[1].price) if len(tgts) > 1 else tp1
            if direction == "LONG" and not (sl < entry < tp2):
                continue
            if direction == "SHORT" and not (sl > entry > tp2):
                continue
            risk = abs(entry - sl)
            if risk <= 0:
                continue

            max_exit = min(n - 1, index + cfg.max_hold_bars)
            ex = _simulate_exit(primary[index + 1: max_exit + 1], direction=direction,
                                entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                                exit_policy=profile.exit_policy)
            exit_index = index + 1 + ex.exit_offset
            et = _parse_time(entry_bar.get("time") or entry_bar.get("datetime"))
            xt = _parse_time(primary[exit_index].get("time") or primary[exit_index].get("datetime"))
            if et is None or xt is None:
                continue
            cost_r = _cost_r(entry, sl, spread_bps=costs["spread"], commission_bps=costs["commission"],
                             slippage_bps=costs["slippage"], swap_bps_per_day=costs["swap"],
                             entry_time=et, exit_time=xt)
            trades.append({
                "variant": name,
                "symbol": pair["display"],
                "family": family,
                "score_group": route.score_group,
                "direction": direction,
                "year": as_of_date[:4],
                "regime": regime,
                "score_bucket": _bucket_label(conf, cfg.score_buckets),
                "confluence": round(conf, 4),
                "raw_result_r": round(ex.result_r, 6),
                "cost_r": round(cost_r, 6),
                "outcome": ex.outcome,
                "date": cutoff,
            })
            cooldown[name] = exit_index + 1

    return trades
