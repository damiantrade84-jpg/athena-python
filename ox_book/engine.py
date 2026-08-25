"""OX Book orchestrator - candles in, advisory book verdict out.

Paper/research only. The verdict is a display/research artifact; nothing here may
be consumed by execution, sizing, or risk code paths.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd

from ox_book import settings
from ox_book.contracts import BookVerdict, MarketEvaluation, OxParams
from ox_book.evidence import build_book, evaluate_market
from ox_book.significance import TrialRegistry


def canonical_params() -> OxParams:
    return OxParams(
        fast=settings.ema_fast(),
        slow=settings.ema_slow(),
        atr_n=settings.atr_n(),
        atr_mult=settings.atr_mult(),
        long_only=settings.long_only(),
        cost_per_side=settings.base_cost_per_side(),
    )


def run_ox_book(
    candles_by_symbol: dict[str, pd.DataFrame],
    trial_registry: TrialRegistry | None = None,
) -> BookVerdict:
    canonical = canonical_params()
    registry = trial_registry or TrialRegistry()
    evaluations = [
        evaluate_market(symbol, df, canonical, trial_registry=registry)
        for symbol, df in sorted(candles_by_symbol.items())
    ]
    verdict = build_book(evaluations, candles_by_symbol, canonical)
    registry.record(
        {
            "kind": "book_verdict",
            "params": canonical.key,
            "members": [ev.symbol for ev in verdict.members],
            "rejected_count": len(verdict.rejected),
        }
    )
    return verdict


def now_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


__all__ = [
    "BookVerdict",
    "MarketEvaluation",
    "canonical_params",
    "now_stamp",
    "run_ox_book",
]
