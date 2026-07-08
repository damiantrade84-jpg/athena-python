"""Realized exit-R payoff from ASE trade journal (reconciled backtest/live exits)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from athena_ase.horizon import Horizon
from athena_ase.settings import payoff_realized_min_trades


@dataclass(frozen=True)
class RealizedPayoff:
    e_win: float
    e_loss: float
    win_count: int
    loss_count: int
    expectancy: float
    source: str = "journal"


@dataclass(frozen=True)
class CalibrationSnapshot:
    realized_win_rate: float
    mean_p_cal: float
    sample_count: int


def _journal_frame() -> Any:
    from athena_ase.execution.journal import load_trade_journal

    return load_trade_journal()


def _reconciled_mask(df: Any, family: str, horizon: Horizon) -> Any:
    import pandas as pd

    if df.empty:
        return pd.Series(dtype=bool)
    mask = pd.Series(True, index=df.index)
    if "modelFamily" in df.columns:
        mask &= df["modelFamily"].astype(str) == str(family)
    if "horizon" in df.columns:
        mask &= df["horizon"].astype(str) == str(horizon)
    if "realizedNetR" in df.columns:
        mask &= pd.to_numeric(df["realizedNetR"], errors="coerce").notna()
    if "reconciledAt" in df.columns:
        mask &= df["reconciledAt"].notna()
    return mask


def realized_exit_payoff(family: str, horizon: Horizon) -> RealizedPayoff | None:
    """Mean win/loss R from reconciled journal exits for a family+horizon."""
    import pandas as pd

    df = _journal_frame()
    mask = _reconciled_mask(df, family, horizon)
    if not mask.any():
        return None
    vals = pd.to_numeric(df.loc[mask, "realizedNetR"], errors="coerce").dropna()
    if len(vals) < payoff_realized_min_trades():
        return None
    wins = vals[vals > 0]
    losses = vals[vals <= 0]
    e_win = float(wins.mean()) if len(wins) else 0.5
    e_loss = float(losses.mean()) if len(losses) else -0.25
    return RealizedPayoff(
        e_win=e_win,
        e_loss=e_loss,
        win_count=int(len(wins)),
        loss_count=int(len(losses)),
        expectancy=float(vals.mean()),
        source="journal",
    )


def journal_calibration_snapshot(family: str, horizon: Horizon) -> CalibrationSnapshot | None:
    """Realized win rate vs mean calibrated P for reconciled TRADE rows."""
    import pandas as pd

    df = _journal_frame()
    if df.empty:
        return None
    mask = _reconciled_mask(df, family, horizon)
    if "decisionStatus" in df.columns:
        mask &= df["decisionStatus"].astype(str).str.upper() == "TRADE"
    if not mask.any():
        return None
    sub = df.loc[mask]
    vals = pd.to_numeric(sub["realizedNetR"], errors="coerce").dropna()
    if len(vals) < payoff_realized_min_trades():
        return None
    p_col = "probabilityPositive" if "probabilityPositive" in sub.columns else None
    if p_col is None:
        return None
    p_vals = pd.to_numeric(sub.loc[vals.index, p_col], errors="coerce").dropna()
    if len(p_vals) < payoff_realized_min_trades():
        return None
    return CalibrationSnapshot(
        realized_win_rate=float((vals > 0).mean()),
        mean_p_cal=float(p_vals.mean()),
        sample_count=int(len(vals)),
    )
