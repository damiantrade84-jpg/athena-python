from __future__ import annotations

import ast
from copy import deepcopy
import json
import math
from pathlib import Path

import pandas as pd
import pytest
import yaml


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


def test_config_redaction_normalizes_aliases_and_url_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from athena_research.forex_edge.config import redact_secrets

    monkeypatch.setenv("FRED_API_KEY", "fred-secret-value")
    payload = {
        "Api-Key": "fred-secret-value",
        "CLIENT SECRET": "client-value",
        "header": "Bearer bearer-value",
        "url": (
            "https://user:password@example.test/data"
            "?page=2&API-KEY=fred-secret-value&format=json"
        ),
    }

    redacted = redact_secrets(payload)

    assert "fred-secret-value" not in repr(redacted)
    assert "client-value" not in repr(redacted)
    assert "bearer-value" not in repr(redacted)
    assert "password" not in redacted["url"]
    assert redacted["Api-Key"] == "[REDACTED]"
    assert redacted["CLIENT SECRET"] == "[REDACTED]"
    assert redacted["header"] == "Bearer [REDACTED]"
    assert "page=2" in redacted["url"]
    assert "format=json" in redacted["url"]


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


def _study_result(*, production_eligible: bool, metrics: object) -> object:
    from athena_research.forex_edge.models import (
        EligibilityResult,
        EligibilityStatus,
        StudyResult,
        StudyStatus,
    )

    return StudyResult(
        study_status=StudyStatus.COMPLETED_NO_EDGE,
        production_eligible=production_eligible,
        evidence_flags=(),
        metrics=metrics,
        eligibility=EligibilityResult(
            eligible=False,
            status=EligibilityStatus.INELIGIBLE,
        ),
        trials=(),
    )


def test_study_result_rejects_production_eligibility() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    with pytest.raises(
        InvalidResearchInputError,
        match="production_eligible",
    ):
        _study_result(production_eligible=True, metrics={})

    result = _study_result(production_eligible=False, metrics={})
    assert result.to_dict()["production_eligible"] is False


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_json_safe_serialization_rejects_nonfinite_floats(value: float) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    result = _study_result(production_eligible=False, metrics={"value": value})
    with pytest.raises(InvalidResearchInputError, match="finite"):
        result.to_dict()


def test_json_safe_serialization_rejects_unsupported_types() -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    result = _study_result(
        production_eligible=False,
        metrics={"value": object()},
    )
    with pytest.raises(InvalidResearchInputError, match="unsupported"):
        result.to_dict()


@pytest.mark.parametrize(
    "mapping",
    [
        {1: "integer", "1": "string"},
        {True: "boolean", "True": "string"},
        {False: "boolean", "False": "string"},
    ],
)
def test_json_safe_serialization_rejects_nonstring_mapping_keys(
    mapping: dict[object, str],
) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError

    result = _study_result(
        production_eligible=False,
        metrics={"mapping": mapping},
    )

    with pytest.raises(InvalidResearchInputError, match="mapping keys"):
        result.to_dict()


def test_json_safe_serialization_sorts_sets_deterministically() -> None:
    first = _study_result(
        production_eligible=False,
        metrics={"values": {"JPY", "EUR", "USD"}},
    )
    second = _study_result(
        production_eligible=False,
        metrics={"values": frozenset(("USD", "JPY", "EUR"))},
    )

    assert first.to_dict() == second.to_dict()
    assert first.to_dict()["metrics"]["values"] == ["EUR", "JPY", "USD"]


def test_universe_helpers_fail_closed_and_preserve_quote_orientation() -> None:
    from athena_research.forex_edge.universe import (
        currency_usd_price,
        pair_weight_for_currency,
    )

    assert pair_weight_for_currency("EUR", 0.25) == pytest.approx(0.25)
    assert pair_weight_for_currency("JPY", 0.25) == pytest.approx(-0.25)
    with pytest.raises(ValueError, match="positive"):
        currency_usd_price("EUR", 0)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_universe_helpers_reject_nonfinite_prices_and_weights(
    value: float,
) -> None:
    from athena_research.forex_edge.universe import (
        currency_usd_price,
        pair_weight_for_currency,
    )

    for currency in ("EUR", "JPY"):
        with pytest.raises(ValueError, match="finite"):
            currency_usd_price(currency, value)
        with pytest.raises(ValueError, match="finite"):
            pair_weight_for_currency(currency, value)


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


@pytest.mark.parametrize(
    ("section", "value"),
    [
        ("universe", []),
        ("universe", None),
        ("portfolio", []),
        ("portfolio", None),
    ],
)
def test_load_config_rejects_nonmapping_nested_sections(
    tmp_path: Path,
    section: str,
    value: object,
) -> None:
    from athena_research.forex_edge.config import load_config
    from athena_research.forex_edge.models import InvalidResearchInputError

    raw = yaml.safe_load(
        (ROOT / "configs" / "forex_edge_research.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw[section] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(InvalidResearchInputError):
        load_config(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_currencies", "12"),
        ("min_currencies", 12.5),
        ("min_currencies", True),
        ("top_n", "4"),
        ("top_n", 4.0),
        ("top_n", False),
    ],
)
def test_load_config_requires_exact_integer_portfolio_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    from athena_research.forex_edge.config import load_config
    from athena_research.forex_edge.models import InvalidResearchInputError

    raw = yaml.safe_load(
        (ROOT / "configs" / "forex_edge_research.yaml").read_text(
            encoding="utf-8"
        )
    )
    malformed = deepcopy(raw)
    malformed["portfolio"][field] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(malformed), encoding="utf-8")

    with pytest.raises(InvalidResearchInputError):
        load_config(path)


def test_store_versions_data_without_overwrite(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    raw = store.write_raw("FRED", "spot_EUR", b'{"value":1.1}')
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2020-01-02", "2020-01-03"],
                utc=True,
            ),
            "value": [1.1, 1.2],
        }
    )
    first = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )
    same = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )

    assert first == same
    pd.testing.assert_frame_equal(
        store.load_normalized("spot", "EUR", first.version),
        frame,
    )

    changed = frame.copy()
    changed.loc[1, "value"] = 1.3
    second = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=changed,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(raw.sha256,),
        metadata={"unit": "USD_PER_CURRENCY", "config_hash": "cfg"},
    )

    assert second.version != first.version
    pd.testing.assert_frame_equal(
        store.load_normalized("spot", "EUR", second.version),
        changed,
    )


def test_store_rejects_conflicting_existing_partition(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    path = (
        store.root
        / "normalized"
        / "spot"
        / "EUR"
        / "bad"
        / "2020"
        / "data.parquet"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")

    with pytest.raises(RuntimeError, match="immutable partition conflict"):
        store._write_partition(path, pd.DataFrame({"x": [1]}))


def test_store_raw_writes_are_idempotent_and_immutable(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    first = store.write_raw("FRED", "spot_EUR", b"payload")
    same = store.write_raw("FRED", "spot_EUR", b"payload")
    assert first == same
    assert first.path.read_bytes() == b"payload"

    first.path.write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="immutable raw conflict"):
        store.write_raw("FRED", "spot_EUR", b"payload")


def test_store_rejects_missing_timestamp_and_nonfinite_hashing(
    tmp_path: Path,
) -> None:
    from athena_research.forex_edge.models import InvalidResearchInputError
    from athena_research.forex_edge.store import (
        ResearchStore,
        canonical_frame_hash,
    )

    store = ResearchStore(tmp_path)
    with pytest.raises(ValueError, match="timestamp"):
        store.write_normalized(
            dataset="spot",
            key="EUR",
            frame=pd.DataFrame({"value": [1.1]}),
            source="FRED",
            source_url="https://example.test/fred",
            raw_hashes=("raw",),
            metadata={"config_hash": "cfg"},
        )
    with pytest.raises(InvalidResearchInputError, match="finite"):
        canonical_frame_hash(pd.DataFrame({"value": [math.nan]}))


def test_store_rejects_conflicting_existing_manifest(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2020-01-02"], utc=True),
            "value": [1.1],
        }
    )
    manifest = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=("raw",),
        metadata={"config_hash": "cfg"},
    )
    path = (
        store.root
        / "manifests"
        / "spot"
        / "EUR"
        / f"{manifest.version}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_url"] = "https://conflict.test"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="immutable manifest conflict"):
        store.write_normalized(
            dataset="spot",
            key="EUR",
            frame=frame,
            source="FRED",
            source_url="https://example.test/fred",
            raw_hashes=("raw",),
            metadata={"config_hash": "cfg"},
        )


def test_store_empty_frame_and_run_directory(tmp_path: Path) -> None:
    from athena_research.forex_edge.store import ResearchStore

    store = ResearchStore(tmp_path)
    frame = pd.DataFrame(columns=["timestamp", "value"])
    manifest = store.write_normalized(
        dataset="spot",
        key="EUR",
        frame=frame,
        source="FRED",
        source_url="https://example.test/fred",
        raw_hashes=(),
        metadata={"config_hash": "cfg"},
    )

    loaded = store.load_normalized("spot", "EUR", manifest.version)
    assert loaded.empty
    assert list(loaded.columns) == ["timestamp", "value"]
    assert store.run_dir("run-1") == tmp_path / "runs" / "run-1"
    assert store.run_dir("run-1").is_dir()
