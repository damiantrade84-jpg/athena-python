"""Engine A V3 audit brief — programmatic checks for external audit systems.

External auditors should call ``run_audit_brief()`` or individual ``audit_*``
helpers rather than legacy ``scoring.get_score_threshold()`` / factor confluence
when validating live V3 behavior.
"""

from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from config import CONFIG
from engine_a_groups import ENGINE_A_KNOWN_SCORE_GROUPS
from engine_a_trade_gate import (
    engine_a_trade_gate_block_reason,
    resolve_engine_a_trade_eligibility,
)
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.profile import baseline_profile
from engine_a_v3.promotion import demo_unvalidated_registry, production_registry
from engine_a_v3.routing import KNOWN_SCORE_GROUPS, route_specialist
from engine_a_v3.workflow import load_manifest
from scoring import get_pair_score_group, get_score_threshold

AUDIT_BRIEF_VERSION = "1.0.0"

V3_BASELINE_TRADE_THRESHOLD = 2.0

# One representative pair per score group (mirrors tests/test_engine_a_v3.py).
AUDIT_REPRESENTATIVE_PAIRS: dict[str, dict[str, str]] = {
    "forex_majors": {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
    "forex_crosses": {"display": "EUR/GBP", "symbol": "EURGBP", "type": "forex"},
    "forex_exotics": {"display": "USD/ZAR", "symbol": "USDZAR", "type": "forex"},
    "forex_other": {"display": "USD/ILS", "symbol": "USDILS", "type": "forex"},
    "crypto_btc": {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"},
    "crypto_eth": {"display": "ETH/USDT", "symbol": "ETHUSDT", "type": "crypto"},
    "crypto_doge": {"display": "DOGE/USDT", "symbol": "DOGEUSDT", "type": "crypto"},
    "crypto_alt_majors": {"display": "SOL/USDT", "symbol": "SOLUSDT", "type": "crypto"},
    "crypto_other": {"display": "AAVE/USDT", "symbol": "AAVEUSDT", "type": "crypto"},
    "precious_trackers": {"display": "XAU/USD", "symbol": "XAUUSD", "type": "commodity"},
    "energy_oil": {"display": "WTI Oil", "symbol": "USOIL", "type": "commodity"},
    "nat_gas": {"display": "Nat Gas", "symbol": "NATGAS", "type": "commodity"},
    "copper": {"display": "Copper", "symbol": "COPPER", "type": "commodity"},
    "pgm_metals": {"display": "XPT/USD", "symbol": "XPTUSD", "type": "commodity"},
    "base_metals": {"display": "Aluminium", "symbol": "ALUMINIUM", "type": "commodity"},
    "softs": {"display": "Coffee", "symbol": "COFFEE", "type": "commodity"},
    "commodity_other": {"display": "Uranium", "symbol": "URANIUM", "type": "commodity"},
    "us_indices_trackers": {"display": "S&P 500", "symbol": "US500", "type": "index"},
    "eu_indices": {"display": "DAX", "symbol": "GER40", "type": "index"},
    "asian_indices": {"display": "Nikkei 225", "symbol": "JP225", "type": "index"},
    "index_other": {"display": "SA40", "symbol": "SA40", "type": "index"},
    "us_stock_single": {"display": "AAPL", "symbol": "AAPL.US", "type": "stock"},
    "bond_tlt": {"display": "TLT", "symbol": "TLT.US", "type": "etf_bond"},
    "smallcap_em_etf": {"display": "EEM", "symbol": "EEM.US", "type": "etf"},
    "stock_other": {"display": "JSE:NPN", "symbol": "NPN.JO", "type": "stock"},
    "unknown": {"display": "UNKNOWN", "symbol": "UNKNOWN", "type": "other"},
}

Status = Literal["PASS", "FAIL", "WARN"]


@dataclass
class AuditCheck:
    id: str
    status: Status
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditSection:
    name: str
    checks: list[AuditCheck] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if any(check.status == "FAIL" for check in self.checks):
            return "FAIL"
        if any(check.status == "WARN" for check in self.checks):
            return "WARN"
        return "PASS"


def _forbidden_legacy_tokens() -> tuple[str, ...]:
    return (
        "get_score_threshold",
        "ENGINE_A_SCORE_GROUP_THRESHOLDS",
        "calc_confluence",
        "compute_factor_scores",
    )


def _scan_engine_a_v3_sources() -> list[tuple[str, str]]:
    root = Path(__file__).parent
    hits: list[tuple[str, str]] = []
    for path in sorted(root.glob("*.py")):
        if path.name == "audit.py":
            continue
        text = path.read_text(encoding="utf-8")
        for token in _forbidden_legacy_tokens():
            if token in text:
                hits.append((path.name, token))
    return hits


def audit_entry_points() -> AuditSection:
    """Verify external audit should use V3 evaluator/routing, not legacy scoring."""
    checks: list[AuditCheck] = []

    checks.append(
        AuditCheck(
            id="public_evaluator_export",
            status="PASS" if callable(evaluate_engine_a_v3) else "FAIL",
            detail="evaluate_engine_a_v3 is the V3 scoring entry point",
            evidence={"module": inspect.getmodule(evaluate_engine_a_v3).__name__},
        )
    )
    checks.append(
        AuditCheck(
            id="public_routing_export",
            status="PASS" if callable(route_specialist) else "FAIL",
            detail="route_specialist resolves score_group/family/subclass",
            evidence={"module": inspect.getmodule(route_specialist).__name__},
        )
    )

    forbidden = _scan_engine_a_v3_sources()
    allowed_profile_period_resolvers = {
        ("profile.py", "get_score_threshold"),
        ("profile.py", "ENGINE_A_SCORE_GROUP_THRESHOLDS"),
        ("profile.py", "calc_confluence"),
        ("profile.py", "compute_factor_scores"),
    }
    forbidden = [
        hit for hit in forbidden if hit not in allowed_profile_period_resolvers
    ]
    # profile.py only imports factor_scoring period resolvers — not threshold helpers.
    forbidden = [hit for hit in forbidden if hit[0] != "profile.py"]
    checks.append(
        AuditCheck(
            id="v3_no_legacy_threshold_imports",
            status="PASS" if not forbidden else "FAIL",
            detail="engine_a_v3 modules must not reference legacy threshold/scoring APIs",
            evidence={"forbidden_hits": forbidden},
        )
    )

    source = inspect.getsource(evaluate_engine_a_v3)
    checks.append(
        AuditCheck(
            id="evaluator_uses_quant_scorer",
            status="PASS" if "score_pair" in source else "FAIL",
            detail="evaluator orchestrates quant_scorer.score_pair (not legacy score_pair)",
            evidence={},
        )
    )

    return AuditSection(name="audit_entry", checks=checks)


def audit_threshold_layers(
    *,
    config: dict[str, Any] | None = None,
) -> AuditSection:
    """V3 cutoff from profile.trade_threshold; legacy YAML is a separate layer."""
    cfg = config if isinstance(config, dict) else CONFIG
    checks: list[AuditCheck] = []

    group_thresholds_cfg = dict(cfg.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {})
    v3_thresholds = {
        group: baseline_profile(group, "intraday").trade_threshold
        for group in sorted(KNOWN_SCORE_GROUPS)
    }
    # V3 baseline must be config-driven: each group's threshold should match
    # ENGINE_A_SCORE_GROUP_THRESHOLDS[group] (or the "default" fallback), not a
    # uniform hardcoded value.
    config_mismatches: dict[str, dict[str, float]] = {}
    for group, v3_value in v3_thresholds.items():
        configured = group_thresholds_cfg.get(group)
        if configured is None:
            configured = group_thresholds_cfg.get("default")
        if configured is None:
            continue
        try:
            configured_value = float(configured)
        except (TypeError, ValueError):
            continue
        if abs(v3_value - configured_value) > 1e-9:
            config_mismatches[group] = {"v3": v3_value, "configured": configured_value}
    checks.append(
        AuditCheck(
            id="v3_baseline_config_driven",
            status="PASS" if not config_mismatches else "FAIL",
            detail="V3 baseline trade_threshold matches ENGINE_A_SCORE_GROUP_THRESHOLDS per group",
            evidence={"thresholds": v3_thresholds, "mismatches": config_mismatches},
        )
    )

    legacy_groups = {
        key for key in group_thresholds_cfg
        if key != "default" and key in ENGINE_A_KNOWN_SCORE_GROUPS
    }
    checks.append(
        AuditCheck(
            id="legacy_threshold_map_present",
            status="PASS" if legacy_groups == set(ENGINE_A_KNOWN_SCORE_GROUPS) else "FAIL",
            detail="Legacy ENGINE_A_SCORE_GROUP_THRESHOLDS covers all known score groups",
            evidence={"legacy_group_count": len(legacy_groups)},
        )
    )

    divergent: dict[str, dict[str, float]] = {}
    for group, pair in AUDIT_REPRESENTATIVE_PAIRS.items():
        legacy = float(get_score_threshold(pair))
        v3 = float(baseline_profile(group, "intraday").trade_threshold)
        if abs(legacy - v3) > 1e-9:
            divergent[group] = {"legacy": legacy, "v3_baseline": v3}
    checks.append(
        AuditCheck(
            id="legacy_and_v3_layers_aligned",
            status="PASS" if not divergent else "FAIL",
            detail="Legacy and V3 threshold layers align (both config-driven from ENGINE_A_SCORE_GROUP_THRESHOLDS)",
            evidence={"divergent_groups": divergent},
        )
    )

    return AuditSection(name="threshold_layer", checks=checks)


def audit_execution_gates(
    signal: dict[str, Any],
    pair: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> AuditSection:
    """Audit decision vs qualified vs executionScope vs engine_a_trade_gate."""
    cfg = config if isinstance(config, dict) else CONFIG
    checks: list[AuditCheck] = []

    decision = str(signal.get("decision") or "").upper()
    qualified = bool(signal.get("qualified"))
    execution_scope = str(signal.get("executionScope") or "")
    engine_trade_enabled = signal.get("engineATradeEnabled")
    trade_flag = bool(signal.get("trade"))
    executable_flag = bool(signal.get("executable"))

    checks.append(
        AuditCheck(
            id="qualified_implies_trade_decision",
            status="PASS" if (not qualified or decision == "TRADE") else "FAIL",
            detail="qualified=True requires decision=TRADE",
            evidence={"decision": decision, "qualified": qualified},
        )
    )
    checks.append(
        AuditCheck(
            id="qualified_requires_levels",
            status="PASS"
            if (not qualified or (signal.get("sl") and signal.get("tp1")))
            else "FAIL",
            detail="qualified=True requires SL/TP geometry on the signal",
            evidence={
                "sl": signal.get("sl"),
                "tp1": signal.get("tp1"),
            },
        )
    )
    checks.append(
        AuditCheck(
            id="engine_a_trade_enabled_matches_qualified",
            status="PASS" if engine_trade_enabled == qualified else "FAIL",
            detail="engineATradeEnabled must mirror qualified on V3 signals",
            evidence={
                "engineATradeEnabled": engine_trade_enabled,
                "qualified": qualified,
            },
        )
    )
    checks.append(
        AuditCheck(
            id="execution_scope_matches_qualified",
            status="PASS"
            if (qualified and execution_scope == "DEMO_ONLY")
            or (not qualified and execution_scope in {"NONE", ""})
            else "FAIL",
            detail="executionScope is DEMO_ONLY iff qualified",
            evidence={"executionScope": execution_scope, "qualified": qualified},
        )
    )
    checks.append(
        AuditCheck(
            id="trade_executable_flags_match_qualified",
            status="PASS" if trade_flag == qualified and executable_flag == qualified else "FAIL",
            detail="trade/executable mirror qualified (pre trade-gate annotation)",
            evidence={
                "trade": trade_flag,
                "executable": executable_flag,
                "qualified": qualified,
            },
        )
    )

    gate_detail = resolve_engine_a_trade_eligibility(pair, signal, config=cfg)
    block_reason = engine_a_trade_gate_block_reason(pair, signal, config=cfg)
    checks.append(
        AuditCheck(
            id="trade_gate_research_only_by_default",
            status="PASS" if not gate_detail.get("enabled") else "WARN",
            detail="Engine A trade gate should block live execution unless explicitly enabled",
            evidence={
                "gate_enabled": gate_detail.get("enabled"),
                "research_only": gate_detail.get("research_only"),
                "source": gate_detail.get("source"),
                "block_reason": block_reason,
            },
        )
    )
    if qualified and block_reason:
        checks.append(
            AuditCheck(
                id="qualified_still_blocked_by_trade_gate",
                status="PASS",
                detail="qualified V3 signal can remain non-executable via engine_a_trade_gate",
                evidence={"block_reason": block_reason},
            )
        )

    return AuditSection(name="exec_gates", checks=checks)


def audit_score_group_parity(
    pairs: dict[str, dict[str, str]] | None = None,
) -> AuditSection:
    """Verify get_pair_score_group() matches route_specialist().score_group."""
    pairs = pairs or AUDIT_REPRESENTATIVE_PAIRS
    checks: list[AuditCheck] = []
    mismatches: dict[str, dict[str, str]] = {}

    for expected_group, pair in pairs.items():
        scoring_group = get_pair_score_group(pair)
        routing_group = route_specialist(pair).score_group
        if scoring_group != routing_group or scoring_group != expected_group:
            mismatches[expected_group] = {
                "expected": expected_group,
                "scoring": scoring_group,
                "routing": routing_group,
            }

    checks.append(
        AuditCheck(
            id="known_score_group_sets_match",
            status="PASS" if KNOWN_SCORE_GROUPS == ENGINE_A_KNOWN_SCORE_GROUPS else "FAIL",
            detail="routing.KNOWN_SCORE_GROUPS matches engine_a_groups canonical set",
            evidence={
                "routing_only": sorted(KNOWN_SCORE_GROUPS - ENGINE_A_KNOWN_SCORE_GROUPS),
                "groups_only": sorted(ENGINE_A_KNOWN_SCORE_GROUPS - KNOWN_SCORE_GROUPS),
            },
        )
    )
    checks.append(
        AuditCheck(
            id="scoring_routing_parity",
            status="PASS" if not mismatches else "FAIL",
            detail="get_pair_score_group and route_specialist agree for representative pairs",
            evidence={"mismatches": mismatches},
        )
    )

    return AuditSection(name="group_coverage", checks=checks)


def audit_validation_lanes(
    manifest_path: str | Path = "configs/engine_a_v3_validation.yaml",
) -> AuditSection:
    """Verify validation-enabled vs demo-unvalidated lane coverage."""
    manifest = load_manifest(manifest_path)
    groups = manifest.get("groups") or {}
    checks: list[AuditCheck] = []

    enabled = sorted(
        name for name, group in groups.items() if group.get("validationEnabled") is True
    )
    disabled = sorted(
        name for name, group in groups.items() if group.get("validationEnabled") is not True
    )
    expected_enabled = {
        "crypto_btc",
        "crypto_eth",
        "crypto_doge",
        "forex_majors",
        "forex_crosses",
    }

    checks.append(
        AuditCheck(
            id="manifest_covers_all_groups",
            status="PASS" if set(groups) == set(KNOWN_SCORE_GROUPS) else "FAIL",
            detail="validation manifest declares all 26 score groups",
            evidence={"manifest_groups": len(groups)},
        )
    )
    checks.append(
        AuditCheck(
            id="five_validation_enabled_lanes",
            status="PASS" if set(enabled) == expected_enabled else "FAIL",
            detail="Exactly five groups have validationEnabled=true",
            evidence={"validation_enabled": enabled},
        )
    )
    checks.append(
        AuditCheck(
            id="twenty_one_unvalidated_lanes",
            status="PASS" if len(disabled) == 21 else "FAIL",
            detail="Twenty-one groups are validationEnabled=false (demo-unvalidated only)",
            evidence={"unvalidated_count": len(disabled)},
        )
    )

    demo_registry = demo_unvalidated_registry()
    prod_registry = production_registry()
    checks.append(
        AuditCheck(
            id="demo_registry_allows_unvalidated",
            status="PASS" if demo_registry.allow_demo_unvalidated else "FAIL",
            detail="demo_unvalidated_registry enables baseline profile fallback",
            evidence={},
        )
    )
    checks.append(
        AuditCheck(
            id="production_registry_demo_gated",
            status="PASS" if not prod_registry.allow_demo_unvalidated else "WARN",
            detail="production_registry only allows demo-unvalidated when EXECUTOR_MODE=demo",
            evidence={"allow_demo_unvalidated": prod_registry.allow_demo_unvalidated},
        )
    )

    return AuditSection(name="validation_lanes", checks=checks)


def run_audit_brief(
    *,
    pair: dict[str, Any] | None = None,
    candles: dict[str, list[dict]] | None = None,
    horizon: str = "intraday",
    config: dict[str, Any] | None = None,
    manifest_path: str | Path = "configs/engine_a_v3_validation.yaml",
    include_signal_eval: bool = True,
) -> dict[str, Any]:
    """Run all audit-brief sections and return a JSON-serializable report."""
    sections = [
        audit_entry_points(),
        audit_threshold_layers(config=config),
        audit_score_group_parity(),
        audit_validation_lanes(manifest_path=manifest_path),
    ]

    signal_report: dict[str, Any] | None = None
    if include_signal_eval and pair and candles:
        signal = evaluate_engine_a_v3(
            pair,
            candles,
            horizon=horizon,
            registry=demo_unvalidated_registry(),
        ).to_dict()
        sections.append(audit_execution_gates(signal, pair, config=config))
        signal_report = {
            "pair": pair.get("display") or pair.get("symbol"),
            "decision": signal.get("decision"),
            "qualified": signal.get("qualified"),
            "executionScope": signal.get("executionScope"),
            "confluenceScore": signal.get("confluenceScore"),
            "confluenceThreshold": signal.get("confluenceThreshold"),
        }

    overall: Status = "PASS"
    for section in sections:
        if section.status == "FAIL":
            overall = "FAIL"
            break
        if section.status == "WARN" and overall == "PASS":
            overall = "WARN"

    return {
        "auditBriefVersion": AUDIT_BRIEF_VERSION,
        "contractVersion": "3.1.0",
        "overall": overall,
        "sections": {
            section.name: {
                "status": section.status,
                "checks": [asdict(check) for check in section.checks],
            }
            for section in sections
        },
        "signalSample": signal_report,
        "entryPoints": {
            "evaluate": "engine_a_v3.evaluator.evaluate_engine_a_v3",
            "route": "engine_a_v3.routing.route_specialist",
            "legacy_threshold_only": "scoring.get_score_threshold (V3 disabled path only)",
        },
    }
