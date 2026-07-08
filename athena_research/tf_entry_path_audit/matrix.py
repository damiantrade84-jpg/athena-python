"""Timeframe matrix definitions for Engine A and Engine B diagnostics (Part 1)."""

from __future__ import annotations

from dataclasses import dataclass

from athena_research.tf_entry_path_audit.parameter_intent import IndicatorProfile


@dataclass(frozen=True)
class TfMatrixCell:
    engine: str
    signal_tf: str
    entry_tf: str
    exit_tf: str
    execution_tf: str
    label: str
    style: str = "intraday"
    horizon: str | None = None

    @property
    def key(self) -> str:
        return (
            f"{self.engine}|sig={self.signal_tf}|ent={self.entry_tf}|"
            f"exit={self.exit_tf}|exec={self.execution_tf}|{self.label}"
        )


ENGINE_B_MATRIX: list[TfMatrixCell] = [
    TfMatrixCell("B", "H1", "H1", "H1", "H1", "H1_sig_H1_entry", style="intraday"),
    TfMatrixCell("B", "H4", "H4", "H4", "H4", "H4_sig_H4_entry", style="intraday"),
    TfMatrixCell("B", "H4", "H1", "H1", "H1", "H4_sig_H1_entry", style="intraday"),
    TfMatrixCell("B", "H4", "M15", "M15", "M15", "H4_sig_M15_entry", style="intraday"),
    TfMatrixCell("B", "D1", "H4", "H4", "H4", "D1_sig_H4_entry", style="swing"),
]

ENGINE_A_MATRIX: list[TfMatrixCell] = [
    TfMatrixCell("A", "H1", "H1", "H1", "H1", "H1_score_H1_entry", style="intraday", horizon="intraday"),
    TfMatrixCell("A", "H4", "H4", "H4", "H4", "H4_score_H4_entry", style="swing", horizon="swing"),
    TfMatrixCell("A", "H4", "H1", "H1", "H1", "H4_score_H1_entry", style="intraday", horizon="swing"),
    TfMatrixCell("A", "H4", "M15", "M15", "M15", "H4_score_M15_entry", style="intraday", horizon="swing"),
    TfMatrixCell("A", "D1", "H4", "H4", "H4", "D1_score_H4_entry", style="swing", horizon="swing"),
    TfMatrixCell("A", "D1", "H1", "H1", "H1", "D1_score_H1_entry", style="intraday", horizon="swing"),
]

INDICATOR_PROFILES: list[IndicatorProfile] = [
    IndicatorProfile.BAR_NATIVE,
    IndicatorProfile.CALENDAR_EQUIVALENT,
    IndicatorProfile.VOLATILITY_NATIVE,
]

_SUPPORTED_EXECUTION_TFS = frozenset({"M15", "H1", "H4", "D1"})


def cell_requires_m15(cell: TfMatrixCell) -> bool:
    return cell.entry_tf == "M15" or cell.execution_tf == "M15" or cell.exit_tf == "M15"


def resolve_style_for_cell(cell: TfMatrixCell) -> str:
    if cell.engine == "B":
        return "naked" if cell.style == "intraday" else "swing"
    return cell.horizon or cell.style
