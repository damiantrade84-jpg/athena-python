from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from eodhd_ticker_resolver import eodhd_ticker_for_pair
from eodhd_volume_overlay import (
    eodhd_commodity_ticker_for_pair,
    is_eodhd_volume_whitelisted,
)
from mt5_executor import mt5_map_symbol

from athena_research.commodity_data_audit.costs import (
    _DOCUMENTED_SPREAD_OVERRIDES,
    modelled_costs_for_instrument,
)

from athena_research.commodity_data_audit.models import (
    AcquisitionSource,
    CostStatus,
    InstrumentKind,
    InstrumentSpec,
    PriceField,
    ProviderAliases,
    VolumeKind,
)

INDEPENDENT_CLUSTER_MIN = 8

_REPO_ROOT = Path(__file__).resolve().parents[2]

# One symbol per economically distinct cluster for unseen-symbol holdouts.
INDEPENDENT_CLUSTERS: dict[str, str] = {
    "precious_gold": "XAU/USD",
    "precious_silver": "XAG/USD",
    "precious_platinum": "XPT/USD",
    "precious_palladium": "XPD/USD",
    "energy_wti": "WTI Oil",
    "energy_brent": "Brent Oil",
    "energy_natgas": "Nat Gas",
    "energy_gasoline": "Gasoline",
    "industrial_copper": "Copper",
    "industrial_aluminium": "Aluminium",
    "industrial_lead": "Lead",
    "industrial_nickel": "Nickel",
    "industrial_zinc": "Zinc",
    "ag_cattle": "Cattle",
    "ag_cocoa": "Cocoa",
    "ag_coffee": "Coffee",
    "ag_corn": "Corn",
    "ag_cotton": "Cotton",
    "ag_soybeans": "Soybeans",
    "ag_sugar": "Sugar",
    "ag_wheat": "Wheat",
}

_CLUSTER_BY_DISPLAY = {display: cluster_id for cluster_id, display in INDEPENDENT_CLUSTERS.items()}

_VENDOR_OVERRIDES: dict[str, dict[str, str]] = {
    "XAU/USD": {"eodhd": "XAUUSD.FOREX", "polygon": "C:XAUUSD", "yfinance": "GC=F"},
    "XAG/USD": {"eodhd": "XAGUSD.FOREX", "polygon": "C:XAGUSD", "yfinance": "SI=F"},
    "XPT/USD": {"eodhd": "XPTUSD.FOREX", "polygon": "C:XPTUSD", "yfinance": "PL=F"},
    "XPD/USD": {"eodhd": "XPDUSD.FOREX", "polygon": "C:XPDUSD", "yfinance": "PA=F"},
    "WTI Oil": {"eodhd": "CL", "yfinance": "CL=F"},
    "Brent Oil": {"eodhd": "BZ", "yfinance": "BZ=F"},
    "Nat Gas": {"eodhd": "NG", "yfinance": "NG=F"},
    "Copper": {"eodhd": "", "yfinance": "HG=F"},
    "Aluminium": {"eodhd": "", "yfinance": "ALI=F"},
    "Coffee": {"eodhd": "", "yfinance": "KC=F"},
    "Corn": {"eodhd": "CORN", "yfinance": "ZC=F"},
    "Cotton": {"eodhd": "", "yfinance": "CT=F"},
    "Sugar": {"eodhd": "", "yfinance": "SB=F"},
    "Wheat": {"eodhd": "", "yfinance": "ZW=F"},
}

_COT_PROXY_BY_DISPLAY: dict[str, str] = {
    "XAU/USD": "XAU",
    "XAG/USD": "XAG",
    "XPT/USD": "PL",
    "WTI Oil": "OIL",
    "Brent Oil": "OIL",
    "Nat Gas": "NG",
    "Copper": "HG",
    "Cocoa": "COCOA",
    "Coffee": "COFFEE",
    "Corn": "CORN",
    "Cotton": "COTTON",
    "Soybeans": "SOYBEANS",
    "Sugar": "SUGAR",
    "Wheat": "WHEAT",
    "Cattle": "CATTLE",
}

_EODHD_INTRADAY_H1_DAYS = 365
_EODHD_D1_DAYS = 730


def load_commodity_pairs() -> list[dict[str, Any]]:
    """Load COMMODITY_PAIRS from athena.py without importing athena.py."""
    path = _REPO_ROOT / "athena.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "COMMODITY_PAIRS":
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, list):
                        raise RuntimeError("COMMODITY_PAIRS is not a list")
                    return value
    raise RuntimeError("COMMODITY_PAIRS assignment not found in athena.py")


def _instrument_kind(display: str) -> InstrumentKind:
    if display in {"XAU/USD", "XAG/USD", "XPT/USD", "XPD/USD"}:
        return InstrumentKind.SPOT_CFD
    if display in {"WTI Oil", "Brent Oil", "Nat Gas", "Gasoline"}:
        return InstrumentKind.ENERGY_CFD
    if display in {"Copper", "Aluminium", "Lead", "Nickel", "Zinc"}:
        return InstrumentKind.INDUSTRIAL_METAL_CFD
    if display in {
        "Cattle", "Cocoa", "Coffee", "Corn", "Cotton", "Soybeans", "Sugar", "Wheat"
    }:
        return InstrumentKind.AGRICULTURAL_CFD
    return InstrumentKind.SPOT_CFD


def resolve_provider_aliases(pair: dict[str, Any]) -> ProviderAliases:
    display = str(pair.get("display") or "").strip()
    overrides = _VENDOR_OVERRIDES.get(display, {})
    return ProviderAliases(
        canonical_display=display,
        mt5_symbol=mt5_map_symbol(display),
        eodhd_ticker=eodhd_ticker_for_pair(pair),
        eodhd_volume_ticker=eodhd_commodity_ticker_for_pair(pair),
        yfinance_symbol=overrides.get("yfinance"),
        polygon_symbol=overrides.get("polygon"),
        cot_futures_proxy=_COT_PROXY_BY_DISPLAY.get(display),
    )


def _eodhd_h4_supported(pair: dict[str, Any], aliases: ProviderAliases) -> bool:
    if not aliases.eodhd_ticker:
        return False
    if aliases.eodhd_ticker.endswith(".COMM"):
        return is_eodhd_volume_whitelisted(pair, "H1")
    return True


def _rejection_reason(display: str, aliases: ProviderAliases, pair: dict[str, Any]) -> str | None:
    if not aliases.mt5_symbol:
        return "missing_mt5_symbol_mapping"
    return None


def _eodhd_fallback_status(display: str, aliases: ProviderAliases, pair: dict[str, Any]) -> str:
    if not aliases.eodhd_ticker and not aliases.eodhd_volume_ticker:
        return "mt5_only_no_eodhd_ticker"
    if not _eodhd_h4_supported(pair, aliases):
        if is_eodhd_volume_whitelisted(pair, "D1"):
            return "eodhd_d1_only; h4_requires_mt5_or_h1_resample"
        return "eodhd_unverified"
    return "eodhd_h1_resample_h4_available"


def instrument_spec_for_pair(pair: dict[str, Any]) -> InstrumentSpec:
    display = str(pair.get("display") or "").strip()
    symbol = str(pair.get("symbol") or "").strip()
    aliases = resolve_provider_aliases(pair)
    cluster_id = _CLUSTER_BY_DISPLAY.get(display, "unclustered")
    eodhd_h4 = _eodhd_h4_supported(pair, aliases)
    h4_construction = "mt5_native_h4"
    if not aliases.mt5_symbol:
        h4_construction = "blocked_no_mt5"
    elif not eodhd_h4:
        h4_construction = "mt5_native_h4; eodhd_fallback_unverified_for_h4"

    return InstrumentSpec(
        display=display,
        symbol=symbol,
        cluster_id=cluster_id,
        cluster_label=cluster_id.replace("_", " "),
        instrument_kind=_instrument_kind(display),
        primary_source=AcquisitionSource.MT5,
        h4_construction=h4_construction,
        price_field=PriceField.UNKNOWN,
        volume_kind=VolumeKind.TICK_VOLUME,
        spread_history=CostStatus.MODELLED,
        financing_history=CostStatus.MODELLED,
        rollover_method="broker_cfd_continuous; no stored adjustment series",
        eodhd_h4_supported=eodhd_h4,
        eodhd_h1_depth_days=_EODHD_INTRADAY_H1_DAYS,
        eodhd_d1_depth_days=_EODHD_D1_DAYS,
        mt5_native_h4=True,
        known_discontinuities=(
            ("yfinance_fallback uses continuous futures semantics (GC=F/CL=F), not Pepperstone CFD",)
            if aliases.yfinance_symbol and aliases.yfinance_symbol.endswith("=F")
            else ()
        ),
        rejection_reason=_rejection_reason(display, aliases, pair),
    )


def _eodhd_notes(display: str, aliases: ProviderAliases, pair: dict[str, Any]) -> tuple[str, ...]:
    notes: list[str] = []
    status = _eodhd_fallback_status(display, aliases, pair)
    if status != "eodhd_h1_resample_h4_available":
        notes.append(status)
    if display == "Gasoline" and not aliases.eodhd_volume_ticker:
        notes.append("no EODHD_COMMODITY_TICKERS entry")
    return tuple(notes)


def build_coverage_matrix(pairs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pairs or load_commodity_pairs():
        if not pair.get("enabled", True):
            continue
        spec = instrument_spec_for_pair(pair)
        aliases = resolve_provider_aliases(pair)
        modelled = modelled_costs_for_instrument(spec.display)
        _ = _DOCUMENTED_SPREAD_OVERRIDES  # documented fallback source for audit notes
        rows.append(
            {
                "canonical_instrument": spec.display,
                "internal_symbol": spec.symbol,
                "cluster_id": spec.cluster_id,
                "instrument_kind": spec.instrument_kind.value,
                "provider_symbols": {
                    "mt5": aliases.mt5_symbol,
                    "eodhd_ohlc": aliases.eodhd_ticker,
                    "eodhd_volume": aliases.eodhd_volume_ticker,
                    "yfinance_fallback": aliases.yfinance_symbol,
                    "polygon_fallback": aliases.polygon_symbol,
                    "cot_futures_proxy": aliases.cot_futures_proxy,
                },
                "primary_source": spec.primary_source.value,
                "h4_construction": spec.h4_construction,
                "mt5_native_h4": spec.mt5_native_h4,
                "eodhd_h4_supported": spec.eodhd_h4_supported,
                "eodhd_h1_depth_days": spec.eodhd_h1_depth_days,
                "eodhd_d1_depth_days": spec.eodhd_d1_depth_days,
                "price_field": spec.price_field.value,
                "volume_kind": spec.volume_kind.value,
                "spread_history": spec.spread_history.value,
                "financing_history": spec.financing_history.value,
                "modelled_spread_points": modelled.spread_points,
                "rollover_method": spec.rollover_method,
                "known_discontinuities": list(spec.known_discontinuities),
                "eodhd_fallback_status": _eodhd_fallback_status(
                    spec.display, aliases, pair
                ),
                "eodhd_notes": list(_eodhd_notes(spec.display, aliases, pair)),
                "rejection_reason": spec.rejection_reason,
                "independent_cluster": spec.cluster_id in INDEPENDENT_CLUSTERS,
            }
        )
    return rows


def independent_cluster_count(pairs: list[dict[str, Any]] | None = None) -> int:
    displays = {
        str(pair.get("display") or "")
        for pair in (pairs or load_commodity_pairs())
        if pair.get("enabled", True)
    }
    return sum(1 for display in INDEPENDENT_CLUSTERS.values() if display in displays)


def recommended_acquisition_batch(
    *,
    min_clusters: int = INDEPENDENT_CLUSTER_MIN,
) -> dict[str, Any]:
    pairs = [p for p in load_commodity_pairs() if p.get("enabled", True)]
    cluster_rows = build_coverage_matrix(pairs)
    eligible = [row for row in cluster_rows if row["rejection_reason"] is None]
    cluster_first = []
    seen_clusters: set[str] = set()
    for cluster_id, display in INDEPENDENT_CLUSTERS.items():
        if display not in {p["display"] for p in pairs}:
            continue
        if cluster_id in seen_clusters:
            continue
        row = next((r for r in eligible if r["canonical_instrument"] == display), None)
        if row:
            cluster_first.append(display)
            seen_clusters.add(cluster_id)
        if len(cluster_first) >= min_clusters:
            break
    remainder = [
        row["canonical_instrument"]
        for row in eligible
        if row["canonical_instrument"] not in cluster_first
    ]
    symbols = cluster_first + remainder
    return {
        "source": AcquisitionSource.MT5.value,
        "symbols": symbols,
        "timeframes": ["H4", "H1", "D1"],
        "limits": {"H4": 4400, "H1": 17600, "D1": 750},
        "min_bars": {"H4": 500, "H1": 500, "D1": 230},
        "date_range_policy": "max_available_from_mt5_terminal",
        "raw_preservation": "immutable provider payloads under logs/commodity_data_audit/raw/v1/",
        "normalized_version": "commodity_ohlcv_v1",
        "incremental": "resume by manifest content hash + last fetched bar timestamp",
        "rate_limits": {
            "mt5": "terminal-local; no REST quota",
            "eodhd": "1000 req/min; intraday ~365d per call",
        },
        "expected_storage_mb_per_symbol_tf": {"H4": 0.4, "H1": 1.5, "D1": 0.05},
    }
