"""Engine A ENTRY/DIRECTION edge forensic ledger (research-only, no execution).

Builds ONE immutable event ledger of price-core candidates and records the fields
needed to separate THREE questions the prior passes could not disentangle:

  * DIRECTION  — does the canonical price-core sign predict the *raw* forward
    return (entry-anchored, NO SL/TP, NO cost) at multiple horizons?
  * ENTRY/EXIT — given the direction, does the production entry+SL/TP machinery
    capture or destroy whatever the raw forward path offered (MFE/MAE vs net R)?
  * SELECTION  — (handled downstream) does any score threshold pick a better
    untouched-holdout subset?

The candidate is IDENTICAL to the paired ledger's canonical rule:
  direction = sign of PRICE-CORE-only weighted dir_sum (subsystems excluded);
  entry/exit/cost = production V3 (_build_levels / _simulate_exit / _cost_r);
  one cooldown for the whole ledger so within-symbol trades don't overlap.

We additionally record, per candidate and WITHOUT changing exit behaviour:
  * raw forward signed return at h in {1,2,3,6,12,maxhold} bars, entry-anchored,
    normalised by structural risk |entry-sl| (an R-comparable "direction-only"
    measure with no SL/TP/cost);
  * the long-equivalent (unsigned-by-direction) forward return = market "beta"
    proxy, so alpha can be separated from beta;
  * MFE / MAE / reached_1r over the max-hold window (path potential);
  * structural raw_result_r + cost_r (full production pipeline);
  * entry-timing variants holding DIRECTION fixed (current / next-open / 1-bar
    delay / 2-bar delay) measured to a fixed eval horizon;
  * individual indicator votes (per-TF trend, RSI, MACD, DI, ADX, location,
    volume) for the collinearity audit;
  * a deterministic setup id (trend_pullback / momentum_continuation /
    range_mean_reversion / breakout) for setup-specific baselines.

Nothing here writes to live state, Engine B, Engine D, or any executor.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from engine_a_v3.backtest import _cost_r, _simulate_exit
from engine_a_v3.diagnostics import trade_path_diagnostics
from engine_a_v3.evaluator import _build_levels, _parse_time
from engine_a_v3.profile import baseline_profile
from engine_a_v3.quant_scorer import (
    _FAMILY_ASSET,
    _clamp,
    _f,
    _snapshots,
    _tf_trend,
)
from engine_a_v3.routing import route_specialist

from athena_research.engine_a_ablation.context_builder import build_pit_context
from athena_research.engine_a_ablation.harness import HarnessConfig, _COSTS, _regime
from athena_research.engine_a_ablation.shadow_scorer import (
    PRICE_FACTORS,
    _RESEARCH_WEIGHTS,
    aggregate,
    compute_components,
)

# Forward horizons (H4 bars after entry) for the direction audit.
FWD_HORIZONS = (1, 2, 3, 6, 12)


def _price_only_weights(family: str) -> dict[str, float]:
    base = dict(_RESEARCH_WEIGHTS.get(family, _RESEARCH_WEIGHTS["unknown"]))
    return {k: (base.get(k, 0.0) if k in PRICE_FACTORS else 0.0) for k in base}


def _macd_sig(snap) -> float | None:
    hist = _f(snap.get("macdHist"))
    if hist is None:
        return None
    prev = _f(snap.get("macdHistPrev"))
    base = 1.0 if hist > 0 else -1.0 if hist < 0 else 0.0
    if prev is not None and base != 0.0:
        rising = hist > prev
        strengthening = (base > 0 and rising) or (base < 0 and not rising)
        base *= 1.0 if strengthening else 0.6
    elif base != 0.0:
        base *= 0.8
    return base


def _rsi_sig(snap) -> float | None:
    rsi = _f(snap.get("rsi"))
    return None if rsi is None else _clamp((rsi - 50.0) / 30.0, -1.0, 1.0)


def _di_sig(snap) -> float | None:
    dp, dm = _f(snap.get("plusDI")), _f(snap.get("minusDI"))
    if dp is None or dm is None or (dp + dm) <= 0:
        return None
    return _clamp((dp - dm) / (dp + dm), -1.0, 1.0)


def _setup_id(level_style: str, regime: str, location_signal: float, direction: str) -> str:
    """Deterministic, non-overlapping setup tag from the production routing
    inputs (no confluence blending). location_signal>0 means 'pullback into
    support for a long' per _location_component; <0 means stretched/extended."""
    dsign = 1.0 if direction == "LONG" else -1.0
    aligned_loc = location_signal * dsign  # >0 => location favours the trade dir
    if level_style == "mean_reversion":
        return "range_mean_reversion"
    if regime == "trending":
        return "trend_pullback" if aligned_loc >= 0 else "momentum_continuation"
    if regime == "ranging":
        return "range_fade" if aligned_loc < 0 else "range_breakout"
    return "trend_neutral"


@dataclass
class EventCandidate:
    symbol: str
    family: str
    score_group: str
    direction: str
    date: str
    year: str
    regime: str
    setup_id: str
    # structural production outcome
    raw_result_r: float
    cost_r: float
    outcome: str
    # path potential
    mfe_r: float
    mae_r: float
    reached_1r: bool
    # raw direction-only forward returns (entry-anchored, no SL/TP/cost), in R
    fwd_r: dict[str, float] = field(default_factory=dict)       # "1".."12","maxhold"
    fwd_beta: dict[str, float] = field(default_factory=dict)    # long-equivalent
    # entry-timing variants (direction fixed), forward return to a fixed horizon
    entry_variants: dict[str, float] = field(default_factory=dict)
    # individual indicator votes for collinearity
    votes: dict[str, float] = field(default_factory=dict)
    # scores (canonical price-core confluence) for selection/ranking reuse
    score_price_only: float = 0.0


def build_event_ledger_for_symbol(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    cfg: HarnessConfig,
    entry_eval_h: int = 6,
) -> list[EventCandidate]:
    route = route_specialist(pair)
    family = route.family
    profile = baseline_profile(route.score_group, cfg.horizon)
    costs = _COSTS.get(family, _COSTS["unknown"])
    price_only_w = _price_only_weights(family)
    asset_type = _FAMILY_ASSET.get(family, "other")

    primary = list(candles.get("H4") or [])
    n = len(primary)
    if n < cfg.warmup + 5:
        return []

    tf_rows = {tf: list(rows or []) for tf, rows in candles.items()}
    tf_times = {tf: [str(c.get("time") or c.get("datetime")) for c in rows]
                for tf, rows in tf_rows.items()}
    cap = cfg.prefix_cap
    out: list[EventCandidate] = []
    cooldown_until = cfg.warmup

    for index in range(cfg.warmup, n - 1):
        if index < cooldown_until:
            continue
        if cfg.step > 1 and (index - cfg.warmup) % cfg.step != 0:
            continue
        cutoff = str(primary[index].get("time") or primary[index].get("datetime"))
        as_of_date = cutoff[:10]
        d1_n = bisect_right(tf_times.get("D1", []), cutoff)
        h4_n = bisect_right(tf_times.get("H4", []), cutoff)
        if d1_n < cfg.min_d1 or h4_n < 60:
            continue
        prefix = {}
        for tf, rows in tf_rows.items():
            idx = bisect_right(tf_times[tf], cutoff)
            prefix[tf] = rows[max(0, idx - cap): idx]
        h4_prefix = prefix["H4"]

        context = build_pit_context(pair["display"], as_of_date)
        bar = compute_components(route, cfg.horizon, prefix, context=context, profile=profile)

        canon = aggregate(bar, include=set(PRICE_FACTORS), weights=price_only_w,
                          threshold=profile.trade_threshold)
        direction = canon.direction
        if direction not in ("LONG", "SHORT"):
            continue
        dsign = 1.0 if direction == "LONG" else -1.0

        levels = _build_levels(h4_prefix, direction=direction, level_style=canon.level_style)
        if levels is None or levels.entry_zone is None:
            continue
        entry_bar = primary[index + 1]
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

        # ── structural production exit (unchanged) ──────────────────────────
        max_exit = min(n - 1, index + cfg.max_hold_bars)
        future_window = primary[index + 1: max_exit + 1]
        ex = _simulate_exit(future_window, direction=direction, entry=entry,
                            sl=sl, tp1=tp1, tp2=tp2, exit_policy=profile.exit_policy)
        exit_index = index + 1 + ex.exit_offset
        et = _parse_time(entry_bar.get("time") or entry_bar.get("datetime"))
        xt = _parse_time(primary[exit_index].get("time") or primary[exit_index].get("datetime"))
        if et is None or xt is None:
            continue
        cost_r = _cost_r(entry, sl, spread_bps=costs["spread"], commission_bps=costs["commission"],
                         slippage_bps=costs["slippage"], swap_bps_per_day=costs["swap"],
                         entry_time=et, exit_time=xt)

        # ── path potential (no behaviour change) ────────────────────────────
        path = trade_path_diagnostics(future_window, direction=direction,
                                      entry=entry, sl=sl, tp1=tp1, tp2=tp2)

        # ── raw direction-only forward returns (entry-anchored, no SL/TP) ───
        fwd_r: dict[str, float] = {}
        fwd_beta: dict[str, float] = {}
        for h in FWD_HORIZONS:
            j = min(n - 1, index + 1 + h)
            close_h = float(primary[j].get("close"))
            beta = (close_h - entry) / risk
            fwd_beta[str(h)] = round(beta, 6)
            fwd_r[str(h)] = round(dsign * beta, 6)
        jm = min(n - 1, index + cfg.max_hold_bars)
        close_m = float(primary[jm].get("close"))
        fwd_beta["maxhold"] = round((close_m - entry) / risk, 6)
        fwd_r["maxhold"] = round(dsign * (close_m - entry) / risk, 6)

        # ── entry-timing variants (DIRECTION fixed) to a fixed eval horizon ──
        ev: dict[str, float] = {}
        # current: entry as built (zone fill on index+1 open)
        je = min(n - 1, index + 1 + entry_eval_h)
        ev["current"] = round(dsign * (float(primary[je].get("close")) - entry) / risk, 6)
        # next confirmed bar open
        a_open = float(entry_bar.get("open"))
        ev["next_open"] = round(dsign * (float(primary[je].get("close")) - a_open) / risk, 6)
        # 1-bar delay: anchor at close[index+1]
        a1 = float(primary[index + 1].get("close"))
        j1 = min(n - 1, index + 1 + entry_eval_h + 1)
        ev["delay1"] = round(dsign * (float(primary[j1].get("close")) - a1) / risk, 6)
        # 2-bar delay: anchor at close[index+2]
        if index + 2 <= n - 1:
            a2 = float(primary[index + 2].get("close"))
            j2 = min(n - 1, index + 2 + entry_eval_h + 1)
            ev["delay2"] = round(dsign * (float(primary[j2].get("close")) - a2) / risk, 6)

        # ── individual votes for collinearity (point-in-time snap) ──────────
        snaps = _snapshots(prefix, asset_type, dict(profile.indicator_periods))
        h4_snap = snaps.get("H4") or {}
        votes = {
            "trend": round(bar.comps["trend"].signal, 6),
            "location": round(bar.comps["location"].signal, 6),
            "volume": round(bar.comps["volume"].signal, 6),
            "rsi": _rsi_sig(h4_snap),
            "macd": _macd_sig(h4_snap),
            "di": _di_sig(h4_snap),
            "adx": _f(h4_snap.get("adx")),
            "trend_d1": _tf_trend(snaps.get("D1") or {})[0] if snaps.get("D1") else None,
            "trend_h4": _tf_trend(h4_snap)[0] if h4_snap else None,
            "trend_h1": _tf_trend(snaps.get("H1") or {})[0] if snaps.get("H1") else None,
        }

        regime = _regime(h4_prefix)
        setup_id = _setup_id(canon.level_style, regime, bar.comps["location"].signal, direction)

        out.append(EventCandidate(
            symbol=pair["display"], family=family, score_group=route.score_group,
            direction=direction, date=cutoff, year=as_of_date[:4], regime=regime,
            setup_id=setup_id,
            raw_result_r=round(ex.result_r, 6), cost_r=round(cost_r, 6), outcome=ex.outcome,
            mfe_r=float(path["max_favorable_excursion_r"]),
            mae_r=float(path["max_adverse_excursion_r"]),
            reached_1r=bool(path["reached_one_r"]),
            fwd_r=fwd_r, fwd_beta=fwd_beta, entry_variants=ev, votes=votes,
            score_price_only=round(canon.confluence_score, 4),
        ))
        cooldown_until = exit_index + 1

    return out
