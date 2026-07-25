"""Cost-aware Layer 1 event backtest (ASE v2.1 §B6)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from athena_ase.data.ptis import PTISStore
from athena_ase.horizon import Horizon, horizon_bar_ms
from athena_ase.instruments import DEFAULT_INSTRUMENTS, Instrument
from athena_ase.labels.triple_barrier import label_candidate, simulate_barriers
from athena_ase.signals.arbitrate import Candidate
from athena_ase.signals.common import BarSeries, load_bar_series
from athena_ase.signals.engine import iter_candidates

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
EVENTS_PATH = OUTPUT_DIR / "phase1_events.parquet"
EntryTiming = Literal["confirmed_close", "next_bar_open"]


@dataclass
class EventOutcome:
    instrument: str
    family: str
    horizon: str
    decision_time_ms: int
    entry_time_ms: int
    direction: int
    entry_log: float
    evidence_entry_log: float
    sigma_bar: float
    gross_R: float
    cost_R: float
    net_R: float
    mae_R: float
    mfe_R: float
    hold_bars: int
    exit_reason: str
    agreement_count: int
    conflict_flag: bool
    primary_signals: str
    swap_R: float = 0.0
    total_cost_R: float = 0.0


def simulate_candidate(
    candidate: Candidate,
    store: PTISStore,
    instrument: Instrument,
    *,
    series: BarSeries | None = None,
    entry_timing: EntryTiming = "confirmed_close",
) -> EventOutcome | None:
    """Label one candidate using an explicit, auditable historical entry rule.

    ``next_bar_open`` is the backtest analogue of a live executable quote:
    evidence is fixed at the confirmed decision candle, but entry begins at the
    first price that would have been actionable afterwards.  It fails closed
    when the open is unavailable.
    """
    entry_log = candidate.entry_log
    entry_time_ms = candidate.decision_time_ms
    if entry_timing == "next_bar_open":
        if series is None or series.open_log is None:
            return None
        entry_idx = candidate.bar_index + 1
        if entry_idx >= len(series.open_log) or entry_idx >= len(series.value_time):
            return None
        entry_log = float(series.open_log[entry_idx])
        if not pd.notna(entry_log):
            return None
        # A session gap means the prior bar close is not necessarily this
        # bar's open (e.g. Friday close to Monday open).  Derive the actual
        # start of the actionable bar from its own close and timeframe.
        entry_time_ms = int(series.value_time[entry_idx]) - horizon_bar_ms(candidate.horizon)
    elif entry_timing != "confirmed_close":
        raise ValueError(f"unsupported entry timing: {entry_timing}")
    if series is None:
        if entry_timing != "confirmed_close":
            return None
        labeled = label_candidate(candidate, store, instrument)
    else:
        labeled = simulate_barriers(
            close_log=series.close_log,
            high_log=series.high_log,
            low_log=series.low_log,
            value_time=series.value_time,
            idx=candidate.bar_index,
            direction=candidate.direction,
            entry_log=entry_log,
            sigma_bar=candidate.sigma_bar,
            horizon=candidate.horizon,
            instrument=instrument,
        )
    if labeled is None:
        return None
    return EventOutcome(
        instrument=labeled.instrument,
        family=labeled.family,
        horizon=labeled.horizon,
        decision_time_ms=labeled.decision_time_ms,
        entry_time_ms=entry_time_ms,
        direction=labeled.direction,
        entry_log=labeled.entry_log,
        evidence_entry_log=candidate.entry_log,
        sigma_bar=labeled.sigma_bar,
        gross_R=labeled.gross_R,
        cost_R=labeled.cost_R,
        net_R=labeled.net_R,
        swap_R=labeled.swap_R,
        total_cost_R=labeled.total_cost_R,
        mae_R=labeled.mae_R,
        mfe_R=labeled.mfe_R,
        hold_bars=labeled.hold_bars,
        exit_reason=labeled.exit_reason,
        agreement_count=candidate.agreement_count,
        conflict_flag=candidate.conflict_flag,
        primary_signals=json.dumps(candidate.signals),
    )


def run_event_backtest(
    store: PTISStore,
    *,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
    instruments: tuple[Instrument, ...] | None = None,
    entry_timing: EntryTiming = "confirmed_close",
) -> list[EventOutcome]:
    inst_map = {i.symbol: i for i in (instruments or DEFAULT_INSTRUMENTS)}
    series_by_instrument = {
        symbol: load_bar_series(
            store,
            symbol,
            horizon,
            start_ms,
            end_ms + 60 * 86_400_000,
        )
        for symbol in inst_map
    }
    outcomes: list[EventOutcome] = []
    for cand in iter_candidates(
        store, instruments, horizon=horizon, start_ms=start_ms, end_ms=end_ms
    ):
        inst = inst_map.get(cand.instrument)
        if inst is None:
            continue
        out = simulate_candidate(
            cand,
            store,
            inst,
            series=series_by_instrument.get(cand.instrument),
            entry_timing=entry_timing,
        )
        if out is not None:
            outcomes.append(out)
    return outcomes


def persist_events(outcomes: list[EventOutcome], path: Path | None = None) -> Path:
    out_path = path or EVENTS_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([asdict(o) for o in outcomes])
    df.to_parquet(out_path, index=False)
    return out_path


def run_and_persist(
    store: PTISStore,
    *,
    start_ms: int,
    end_ms: int,
    horizons: tuple[Horizon, ...] = ("intraday", "swing"),
    instruments: tuple[Instrument, ...] | None = None,
    entry_timing: EntryTiming = "confirmed_close",
    path: Path | None = None,
) -> Path:
    all_outcomes: list[EventOutcome] = []
    for hz in horizons:
        all_outcomes.extend(
            run_event_backtest(
                store,
                horizon=hz,
                start_ms=start_ms,
                end_ms=end_ms,
                instruments=instruments,
                entry_timing=entry_timing,
            )
        )
    return persist_events(all_outcomes, path)
