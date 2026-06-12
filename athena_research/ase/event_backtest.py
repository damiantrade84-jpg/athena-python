"""Cost-aware Layer 1 event backtest (ASE v2.1 §B6)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

import pandas as pd

from athena_ase.data.ptis import PTISStore
from athena_ase.horizon import Horizon
from athena_ase.instruments import DEFAULT_INSTRUMENTS, Instrument
from athena_ase.labels.triple_barrier import label_candidate
from athena_ase.signals.arbitrate import Candidate
from athena_ase.signals.engine import iter_candidates

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
EVENTS_PATH = OUTPUT_DIR / "phase1_events.parquet"


@dataclass
class EventOutcome:
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
    agreement_count: int
    conflict_flag: bool
    primary_signals: str


def simulate_candidate(
    candidate: Candidate,
    store: PTISStore,
    instrument: Instrument,
) -> EventOutcome | None:
    labeled = label_candidate(candidate, store, instrument)
    if labeled is None:
        return None
    return EventOutcome(
        instrument=labeled.instrument,
        family=labeled.family,
        horizon=labeled.horizon,
        decision_time_ms=labeled.decision_time_ms,
        direction=labeled.direction,
        entry_log=labeled.entry_log,
        sigma_bar=labeled.sigma_bar,
        gross_R=labeled.gross_R,
        cost_R=labeled.cost_R,
        net_R=labeled.net_R,
        mae_R=labeled.mae_R,
        mfe_R=labeled.mfe_R,
        hold_bars=labeled.hold_bars,
        exit_reason=labeled.exit_reason,
        agreement_count=labeled.agreement_count,
        conflict_flag=labeled.conflict_flag,
        primary_signals=labeled.primary_signals,
    )


def run_event_backtest(
    store: PTISStore,
    *,
    horizon: Horizon,
    start_ms: int,
    end_ms: int,
    instruments: tuple[Instrument, ...] | None = None,
) -> list[EventOutcome]:
    inst_map = {i.symbol: i for i in (instruments or DEFAULT_INSTRUMENTS)}
    outcomes: list[EventOutcome] = []
    for cand in iter_candidates(
        store, instruments, horizon=horizon, start_ms=start_ms, end_ms=end_ms
    ):
        inst = inst_map.get(cand.instrument)
        if inst is None:
            continue
        out = simulate_candidate(cand, store, inst)
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
) -> Path:
    all_outcomes: list[EventOutcome] = []
    for hz in horizons:
        all_outcomes.extend(
            run_event_backtest(store, horizon=hz, start_ms=start_ms, end_ms=end_ms)
        )
    return persist_events(all_outcomes)
