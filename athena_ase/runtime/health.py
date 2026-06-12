"""ASE runtime health / preflight diagnostics."""

from __future__ import annotations

from typing import Any

from athena_ase.artifacts.loader import load_artifact_bundle
from athena_ase.data.ptis import PTISStore, default_ptis_root
from athena_ase.horizon import Horizon
from athena_ase.registry.artifacts import default_artifacts_root

_MODEL_FAMILIES = ("forex", "crypto", "commodity", "equity", "index_etf")
_EURUSD_H1_CLOSE = (
    "EODHD:EURUSD:H1:close",
    "MT5:EURUSD:H1:close",
)


def scan_diagnostics(store: PTISStore, *, horizon: Horizon = "intraday") -> dict[str, Any]:
    """Top-level scan preflight block (PTIS depth + artifact presence)."""
    eurusd_rows = 0
    eurusd_source = ""
    for series_id in _EURUSD_H1_CLOSE:
        if store.series_exists(series_id):
            eurusd_rows = store.series_row_count(series_id)
            eurusd_source = series_id.split(":", 1)[0]
            break

    families_with_artifacts = [
        family
        for family in _MODEL_FAMILIES
        if load_artifact_bundle(family, horizon) is not None
    ]

    return {
        "ptisRoot": str(store.root),
        "ptisSeriesCount": store.catalog_series_count(),
        "eurusdH1CloseRows": eurusd_rows,
        "eurusdH1Source": eurusd_source,
        "artifactsRoot": str(default_artifacts_root()),
        "horizon": horizon,
        "familiesWithArtifacts": families_with_artifacts,
    }


def ase_health(*, ptis_root: str | None = None, horizon: Horizon = "intraday") -> dict[str, Any]:
    store = PTISStore(ptis_root or default_ptis_root())
    diag = scan_diagnostics(store, horizon=horizon)
    blockers: list[str] = []
    if diag["ptisSeriesCount"] == 0:
        blockers.append("ptis_empty")
    elif diag["eurusdH1CloseRows"] == 0 and horizon == "intraday":
        blockers.append("forex_h1_bars_missing")
    if not diag["familiesWithArtifacts"]:
        blockers.append("no_frozen_artifacts")
    return {
        "success": True,
        "ready": len(blockers) == 0,
        "blockers": blockers,
        **diag,
    }
