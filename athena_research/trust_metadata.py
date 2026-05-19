"""
Research Lab trust / fidelity labels for operator UI.

Read-only helpers — no live engine imports, no config writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

_MAIN_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

# Strategies that map to live ENGINE_A_RESEARCH_LAB_FACTORS (factor_scoring.py)
_LIVE_ENGINE_A_FACTORS = frozenset({
    "obv_divergence",
    "bollinger_touch",
    "price_momentum",
    "stochastic_cross",
    "chandelier_trend",
    "aroon_trend",
})

# Strategies aligned with ENGINE_B_RESEARCH_LAB_FACTORS (market_structure.py)
_LIVE_ENGINE_B_FACTORS = frozenset({
    "micro_breakout",
    "vwap_reclaim",
    "cvd_momentum",
    "vwap_deviation",
})

_PROXY_FAMILIES = frozenset({"engine_b_proxy", "engine_d_proxy", "scalp_momentum_proxy"})

_TRUST_LEGEND = [
    {
        "tier": "USE_NOW",
        "label": "Ready for paper review",
        "meaning": (
            "STRONG discovery result, passes implementation readiness, and maps to a "
            "live-aligned indicator already wired in config (Engine A/B research factors). "
            "Still not live execution — validate in paper before changing gates."
        ),
    },
    {
        "tier": "SCREEN_ONLY",
        "label": "Discovery / proxy only",
        "meaning": (
            "May show edge in VectorBT discovery but uses simplified or proxy logic "
            "(e.g. engine_b_proxy, engine_d_proxy). Do not treat as proof for live Engine B/D tuning."
        ),
    },
    {
        "tier": "RETEST",
        "label": "Needs more data or validation",
        "meaning": "Weak sample, failed OOS, or insufficient trades. Run validation or broader symbols.",
    },
    {
        "tier": "REJECT",
        "label": "Do not use",
        "meaning": "Negative or failed discovery metrics. Keep current live setup; do not add.",
    },
]


def trust_legend() -> list[dict[str, str]]:
    return [dict(x) for x in _TRUST_LEGEND]


_LIVE_FACTOR_CACHE: tuple[frozenset[str], frozenset[str]] | None = None


def _load_live_factor_names() -> tuple[frozenset[str], frozenset[str]]:
    """Read live research factor allowlists from config.yaml (read-only, cached)."""
    global _LIVE_FACTOR_CACHE
    if _LIVE_FACTOR_CACHE is not None:
        return _LIVE_FACTOR_CACHE
    try:
        cfg = yaml.safe_load(_MAIN_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        _LIVE_FACTOR_CACHE = (_LIVE_ENGINE_A_FACTORS, _LIVE_ENGINE_B_FACTORS)
        return _LIVE_FACTOR_CACHE

    a_factors = cfg.get("ENGINE_A_RESEARCH_LAB_FACTORS", {}) or {}
    b_groups = (cfg.get("ENGINE_B_RESEARCH_LAB_FACTORS", {}) or {}).get("GROUPS", {}) or {}
    a_names = {str(x) for x in (a_factors.get("FACTORS") or [])}
    b_names: set[str] = set()
    for group_list in b_groups.values():
        if isinstance(group_list, list):
            b_names.update(str(x) for x in group_list)
    _LIVE_FACTOR_CACHE = (
        frozenset(a_names or _LIVE_ENGINE_A_FACTORS),
        frozenset(b_names or _LIVE_ENGINE_B_FACTORS),
    )
    return _LIVE_FACTOR_CACHE


def engine_fidelity_for_row(
    strategy_name: str,
    family: str,
    engine: str = "",
) -> tuple[str, str]:
    """
    Return (engine_fidelity, fidelity_note).

    engine_fidelity:
      LIVE_ALIGNED_A | LIVE_ALIGNED_B | PROXY_ENGINE_B | PROXY_ENGINE_D | DISCOVERY_ONLY
    """
    name = (strategy_name or "").strip().lower()
    fam = (family or "").strip().lower()
    eng = (engine or "").strip().upper()

    live_a, live_b = _load_live_factor_names()

    if name in live_a or name in _LIVE_ENGINE_A_FACTORS:
        return "LIVE_ALIGNED_A", "Matches live Engine A research factor list in config.yaml"
    if name in live_b or name in _LIVE_ENGINE_B_FACTORS:
        return "LIVE_ALIGNED_B", "Matches live Engine B research factor list in config.yaml"

    if fam == "engine_b_proxy" or (eng == "ENGINE_B" and fam in _PROXY_FAMILIES):
        return "PROXY_ENGINE_B", "Simplified BOS/structure proxy — not market_structure.py"
    if fam in {"engine_d_proxy", "scalp_momentum_proxy"} or eng == "ENGINE_D":
        return "PROXY_ENGINE_D", "Scalp proxy — not real Engine D (VP/orderflow)"

    if fam in _PROXY_FAMILIES:
        return "PROXY_ENGINE_D" if "d" in fam else "PROXY_ENGINE_B", "Proxy family — not live engine code"

    return "DISCOVERY_ONLY", "Generic discovery strategy — screen only; confirm via backtest matrix"


def trust_tier_for_row(row: dict[str, Any], *, validation_run_count: int = 0) -> tuple[str, str]:
    """
    Return (trust_tier, trust_summary) for operator UI.

    trust_tier: USE_NOW | SCREEN_ONLY | RETEST | REJECT
    """
    rec = str(row.get("recommendation", "")).upper()
    status = str(row.get("status", "")).upper()
    impl = str(row.get("implementation_verdict", "")).upper()
    fidelity, _ = engine_fidelity_for_row(
        str(row.get("strategy_name", "")),
        str(row.get("family", "")),
        str(row.get("engine", "")),
    )

    if rec in {"REJECT", "REMOVE_OR_DEMOTE"} or status == "REJECT":
        return "REJECT", "Failed discovery — do not implement"

    if status in {"", "NEEDS_MORE_DATA"} or rec in {"RETEST", "WATCHLIST_ONLY"}:
        return "RETEST", "Insufficient evidence — broaden run or run session validation"

    if impl != "IMPLEMENTATION_READY":
        blockers = str(row.get("implementation_blockers", "") or "readiness checks failed")
        return "RETEST", f"Blocked: {blockers[:120]}"

    if fidelity.startswith("PROXY_"):
        if validation_run_count > 0:
            return "SCREEN_ONLY", (
                "Proxy logic with child validation run — edge is indicative only; "
                "confirm with backtest matrix before live changes"
            )
        return "SCREEN_ONLY", "Proxy engine logic — not live-parity; use for screening only"

    if fidelity == "DISCOVERY_ONLY":
        return "SCREEN_ONLY", "Discovery-only family — not wired to live engines"

    if rec in {"ADD", "KEEP"} and fidelity.startswith("LIVE_ALIGNED"):
        if validation_run_count > 0:
            return "USE_NOW", "Live-aligned factor, passed readiness, validation child run completed"
        return "USE_NOW", "Live-aligned factor and passed readiness — paper review recommended"

    if status == "STRONG_CANDIDATE":
        return "SCREEN_ONLY", "Strong discovery but not live-aligned — screen only"

    return "RETEST", "Weak or inconclusive — retest before acting"


def apply_trust_metadata(df: pd.DataFrame, *, validation_run_count: int = 0) -> pd.DataFrame:
    """Add engine_fidelity, fidelity_note, trust_tier, trust_summary columns."""
    if df.empty:
        out = df.copy()
        for col in ("engine_fidelity", "fidelity_note", "trust_tier", "trust_summary"):
            if col not in out.columns:
                out[col] = ""
        return out

    out = df.copy()
    fidelities: list[str] = []
    fidelity_notes: list[str] = []
    tiers: list[str] = []
    summaries: list[str] = []

    for _, row in out.iterrows():
        row_dict = row.to_dict()
        fid, note = engine_fidelity_for_row(
            str(row_dict.get("strategy_name", "")),
            str(row_dict.get("family", "")),
            str(row_dict.get("engine", "")),
        )
        tier, summary = trust_tier_for_row(row_dict, validation_run_count=validation_run_count)
        fidelities.append(fid)
        fidelity_notes.append(note)
        tiers.append(tier)
        summaries.append(summary)

    out["engine_fidelity"] = fidelities
    out["fidelity_note"] = fidelity_notes
    out["trust_tier"] = tiers
    out["trust_summary"] = summaries
    return out


def build_trust_context(df: pd.DataFrame, *, validation_run_count: int = 0) -> dict[str, Any]:
    """Aggregate trust counts and legend for API responses."""
    work = apply_trust_metadata(df, validation_run_count=validation_run_count)
    tier_counts: dict[str, int] = {}
    fidelity_counts: dict[str, int] = {}
    if not work.empty and "trust_tier" in work.columns:
        tier_counts = {str(k): int(v) for k, v in work["trust_tier"].value_counts().items()}
    if not work.empty and "engine_fidelity" in work.columns:
        fidelity_counts = {str(k): int(v) for k, v in work["engine_fidelity"].value_counts().items()}

    return {
        "legend": trust_legend(),
        "tier_counts": tier_counts,
        "fidelity_counts": fidelity_counts,
        "validation_run_count": validation_run_count,
        "disclaimer": (
            "Discovery uses simplified VectorBT strategies in athena_research/strategies.py — "
            "not full Engine A/B/C/D scoring. USE_NOW means paper-review ready for live-aligned "
            "factors only. Proxy and Engine D families are never live-parity."
        ),
    }
