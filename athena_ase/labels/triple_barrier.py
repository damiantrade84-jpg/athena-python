"""Triple-barrier labels (ASE v2.1 §4)."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from athena_ase.data.costs import cost_model_for_instrument
from athena_ase.horizon import HORIZONS, K_SL, K_TP, Horizon
from athena_ase.instruments import Instrument
from athena_ase.signals.arbitrate import Candidate
from athena_ase.signals.common import load_bar_series


@dataclass(frozen=True)
class LabelOutcome:
    instrument: str
    family: str
    horizon: str
    decision_time_ms: int
    direction: int
    entry_log: float
    sigma_bar: float
    gross_R: float
    cost_R: float
    net_R: float
    mae_R: float
    mfe_R: float
    hold_bars: int
    exit_reason: str
    y: int
    agreement_count: int
    conflict_flag: bool
    primary_signals: str
    label_start_ms: int
    label_end_ms: int


def _median_range_frac(
    series_high: np.ndarray,
    series_low: np.ndarray,
    idx: int,
    window: int = 64,
) -> float:
    start = max(0, idx - window + 1)
    ranges = series_high[start : idx + 1] - series_low[start : idx + 1]
    if len(ranges) == 0:
        return 0.0
    return float(np.median(np.abs(ranges)))


def _cost_r(
    instrument: Instrument,
    sigma_bar: float,
    max_hold: int,
    range_frac: float,
) -> float:
    cm = cost_model_for_instrument(instrument.family, subclass=instrument.subclass)
    r_unit = K_SL * sigma_bar * math.sqrt(max_hold)
    if r_unit <= 0:
        return 0.0
    cost_frac = cm.round_trip_cost_bps() / 1e4 + cm.slippage_frac_of_range * range_frac * 2.0
    return cost_frac / r_unit


def simulate_barriers(
    *,
    close_log: np.ndarray,
    high_log: np.ndarray,
    low_log: np.ndarray,
    value_time: np.ndarray,
    idx: int,
    direction: int,
    entry_log: float,
    sigma_bar: float,
    horizon: Horizon,
    instrument: Instrument,
) -> LabelOutcome | None:
    cfg = HORIZONS[horizon]
    d = direction
    p_t = entry_log
    sig = sigma_bar
    h = cfg.max_hold_bars
    r_unit = K_SL * sig * math.sqrt(h)
    if r_unit <= 0 or idx >= len(close_log) - 1:
        return None

    upper = p_t + d * K_TP * r_unit
    lower = p_t - d * K_SL * r_unit
    vertical_idx = min(idx + h, len(close_log) - 1)

    mae = 0.0
    mfe = 0.0
    exit_log = float(close_log[vertical_idx])
    exit_reason = "vertical"
    hold = vertical_idx - idx

    for j in range(idx + 1, vertical_idx + 1):
        hi = float(high_log[j])
        lo = float(low_log[j])
        adv = (p_t - lo) * d if d > 0 else (hi - p_t)
        fav = (hi - p_t) * d if d > 0 else (p_t - lo)
        mae = max(mae, max(adv, 0.0))
        mfe = max(mfe, max(fav, 0.0))

        hit_upper = hi >= upper if d > 0 else lo <= upper
        hit_lower = lo <= lower if d > 0 else hi >= lower
        if hit_upper and hit_lower:
            exit_log = lower
            exit_reason = "adverse_first"
            hold = j - idx
            break
        if hit_lower:
            exit_log = lower
            exit_reason = "stop"
            hold = j - idx
            break
        if hit_upper:
            exit_log = upper
            exit_reason = "target"
            hold = j - idx
            break

    gross_r = (exit_log - p_t) * d / r_unit
    range_frac = _median_range_frac(high_log, low_log, idx)
    cost_r = _cost_r(instrument, sig, h, range_frac)
    net_r = gross_r - cost_r
    label_end_ms = int(value_time[vertical_idx])
    label_start_ms = int(value_time[idx])

    return LabelOutcome(
        instrument=instrument.symbol,
        family=instrument.family,
        horizon=horizon,
        decision_time_ms=int(value_time[idx]),
        direction=d,
        entry_log=p_t,
        sigma_bar=sig,
        gross_R=gross_r,
        cost_R=cost_r,
        net_R=net_r,
        mae_R=mae / r_unit,
        mfe_R=mfe / r_unit,
        hold_bars=hold,
        exit_reason=exit_reason,
        y=1 if net_r > 0 else 0,
        agreement_count=0,
        conflict_flag=False,
        primary_signals="[]",
        label_start_ms=label_start_ms,
        label_end_ms=label_end_ms,
    )


def label_candidate(
    candidate: Candidate,
    store,
    instrument: Instrument,
) -> LabelOutcome | None:
    cfg = HORIZONS[candidate.horizon]
    series = load_bar_series(
        store,
        candidate.instrument,
        candidate.horizon,
        candidate.decision_time_ms - 86400000 * 400,
        candidate.decision_time_ms + 86400000 * 60,
    )
    if series is None:
        return None
    # candidate.bar_index is only valid within the window it was computed on;
    # this series uses a different window, so re-derive the index from the
    # decision time (available_time ordering) instead.
    from athena_ase.signals.common import bar_index_at_decision

    idx = bar_index_at_decision(series, candidate.decision_time_ms)
    if idx is None or idx >= len(series.close_log) - 1:
        return None

    base = simulate_barriers(
        close_log=series.close_log,
        high_log=series.high_log,
        low_log=series.low_log,
        value_time=series.value_time,
        idx=idx,
        direction=candidate.direction,
        entry_log=candidate.entry_log,
        sigma_bar=candidate.sigma_bar,
        horizon=candidate.horizon,
        instrument=instrument,
    )
    if base is None:
        return None
    return LabelOutcome(
        **{
            **asdict(base),
            "agreement_count": candidate.agreement_count,
            "conflict_flag": candidate.conflict_flag,
            "primary_signals": json.dumps(candidate.signals),
        }
    )


def uniqueness_weights(outcomes: list[LabelOutcome]) -> np.ndarray:
    """Average concurrent-label overlap weights (López de Prado)."""
    if not outcomes:
        return np.array([], dtype=float)
    intervals = [(o.label_start_ms, o.label_end_ms) for o in outcomes]
    conc = np.zeros(len(outcomes), dtype=float)
    for i, (a0, a1) in enumerate(intervals):
        overlap = 0
        for j, (b0, b1) in enumerate(intervals):
            if a0 <= b1 and b0 <= a1:
                overlap += 1
        conc[i] = max(overlap, 1)
    return 1.0 / conc


def relabel_dataframe(df: pd.DataFrame, store, inst_map: dict[str, Instrument]) -> pd.DataFrame:
    rows: list[dict] = []
    for rec in df.to_dict(orient="records"):
        inst = inst_map.get(str(rec["instrument"]))
        if inst is None:
            continue
        cand = Candidate(
            instrument=str(rec["instrument"]),
            family=str(rec["family"]),
            horizon=rec["horizon"],  # type: ignore[arg-type]
            decision_time_ms=int(rec["decision_time_ms"]),
            direction=int(rec["direction"]),
            entry_log=float(rec["entry_log"]),
            sigma_bar=float(rec["sigma_bar"]),
            bar_index=int(rec.get("bar_index", 0)),
            agreement_count=int(rec.get("agreement_count", 0)),
            conflict_flag=bool(rec.get("conflict_flag", False)),
            signals=json.loads(rec.get("primary_signals", "[]")),
        )
        out = label_candidate(cand, store, inst)
        if out is not None:
            rows.append(asdict(out))
    return pd.DataFrame(rows)
