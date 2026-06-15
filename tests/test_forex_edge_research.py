from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_forex_edge_package_has_no_forbidden_imports() -> None:
    package = ROOT / "athena_research" / "forex_edge"
    forbidden = {
        "athena",
        "athena_ase",
        "scoring",
        "factor_scoring",
        "forex_scoring",
        "execution",
        "auto_trader",
        "risk_engine",
        "guardian",
        "mt5_executor",
        "bybit_executor",
    }
    imported: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)


def test_frozen_universe_matches_current_ase_contract() -> None:
    from athena_ase.universe import instruments_for_family
    from athena_research.forex_edge.universe import FOREX_PAIRS

    current = tuple(item.symbol for item in instruments_for_family("forex"))
    assert FOREX_PAIRS == current
    assert len(FOREX_PAIRS) == 21


def test_quote_orientation_normalizes_currency_appreciation() -> None:
    from athena_research.forex_edge.universe import currency_usd_price

    assert currency_usd_price("EUR", 1.10) == pytest.approx(1.10)
    assert currency_usd_price("JPY", 150.0) == pytest.approx(1 / 150.0)
    assert currency_usd_price("CAD", 1.35) == pytest.approx(1 / 1.35)


def test_dedicated_config_is_frozen_and_research_only() -> None:
    from athena_research.forex_edge.config import load_config

    cfg = load_config(ROOT / "configs" / "forex_edge_research.yaml")
    assert cfg["portfolio"]["development_end"] == "2018-12-31"
    assert cfg["fixing"]["development_end"] == "2020-12-31"
    assert cfg["portfolio"]["top_n"] == 4
    assert cfg["portfolio"]["min_currencies"] == 12
    assert cfg["production_eligible"] is False


def test_config_redaction_removes_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_research.forex_edge.config import redact_secrets

    monkeypatch.setenv("FRED_API_KEY", "fred-secret-value")
    payload = {
        "url": "https://api.test/data?api_key=fred-secret-value",
        "api_key": "fred-secret-value",
        "nested": {"Authorization": "Bearer fred-secret-value"},
    }
    redacted = redact_secrets(payload)
    assert "fred-secret-value" not in repr(redacted)
    assert redacted["api_key"] == "[REDACTED]"


def test_models_serialize_to_json_safe_mappings() -> None:
    from athena_research.forex_edge.models import (
        EligibilityResult,
        EligibilityStatus,
        EvidenceFlag,
        ReasonCode,
        StudyResult,
        StudyStatus,
        TrialRecord,
    )

    result = StudyResult(
        study_status=StudyStatus.BLOCKED_DATA,
        production_eligible=False,
        evidence_flags=(EvidenceFlag.PBO_UNAVAILABLE,),
        metrics={"reasons": (ReasonCode.PBO_UNAVAILABLE,)},
        eligibility=EligibilityResult(
            eligible=False,
            status=EligibilityStatus.BLOCKED_DATA,
            reason_codes=(ReasonCode.PBO_UNAVAILABLE,),
            details={"flags": (EvidenceFlag.PBO_UNAVAILABLE,)},
        ),
        trials=(
            TrialRecord(
                trial_id="trial-1",
                study="portfolio",
                configuration="momentum",
                cost_multiplier=1.0,
                returns_hash="abc123",
                n_observations=12,
            ),
        ),
    )

    assert json.loads(json.dumps(result.to_dict()))["production_eligible"] is False


def test_universe_helpers_fail_closed_and_preserve_quote_orientation() -> None:
    from athena_research.forex_edge.universe import (
        currency_usd_price,
        pair_weight_for_currency,
    )

    assert pair_weight_for_currency("EUR", 0.25) == pytest.approx(0.25)
    assert pair_weight_for_currency("JPY", 0.25) == pytest.approx(-0.25)
    with pytest.raises(ValueError, match="positive"):
        currency_usd_price("EUR", 0)


def test_store_root_override_and_empirical_quality_limits_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_research.forex_edge.config import (
        default_store_root,
        load_config,
        validate_empirical_config,
    )
    from athena_research.forex_edge.models import BlockedDataError

    monkeypatch.setenv("ATHENA_FOREX_EDGE_ROOT", "C:/research/forex-edge")
    assert default_store_root() == Path("C:/research/forex-edge")

    cfg = load_config(ROOT / "configs" / "forex_edge_research.yaml")
    with pytest.raises(BlockedDataError, match="UNREGISTERED_QUALITY_LIMIT"):
        validate_empirical_config(cfg, "portfolio")

    cfg["quality"].update(
        spot_staleness_days=3,
        rate_staleness_days=7,
        reer_staleness_days=45,
        m5_max_spread_bps=10,
    )
    validate_empirical_config(cfg, "portfolio")
    validate_empirical_config(cfg, "fixing")
    validate_empirical_config(cfg, "quality-report")
    validate_empirical_config(cfg, "both")
