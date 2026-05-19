from pathlib import Path

import yaml

from tools.validate_research_references import (
    REQUIRED_ENTRY_FIELDS,
    _config_py_keys,
    _config_yaml_keys,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "research" / "indicator_calibration_references.yaml"


def test_indicator_calibration_reference_yaml_parses():
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))

    assert isinstance(data, dict)
    assert "sections" in data
    assert set(data["sections"]) >= {
        "ATR",
        "ADX_DI",
        "RSI",
        "trend_momentum",
        "volatility_targeting",
        "data_snooping_multiple_testing",
        "ATR_SL_TP_practitioner_calibration",
        "Engine_B_structure_RR_validation",
    }


def test_every_reference_entry_has_required_fields():
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))

    for section in data["sections"].values():
        for entry in section.get("references", []):
            assert REQUIRED_ENTRY_FIELDS <= set(entry)
            assert entry["citation"].strip()
            assert entry["used_for"].strip()


def test_directly_mapped_config_keys_exist():
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    known = _config_py_keys(ROOT / "config.py") | _config_yaml_keys(ROOT / "config.yaml")

    for section in data["sections"].values():
        for entry in section.get("references", []):
            for key in entry["directly_mapped_config_keys"]:
                assert key in known


def test_unverified_references_cannot_be_production_supporting(tmp_path):
    bad = tmp_path / "bad_registry.yaml"
    bad.write_text(
        """
schema_version: 1
sections:
  bad_section:
    references:
      - title: "Unverified"
        author_source: "Unknown"
        year: 2026
        verified: false
        production_supporting: true
        verified_url_or_identifier: "none"
        citation: "Unknown (2026)."
        used_for: "Nothing production."
        limitation: "Unverified."
        directly_mapped_config_keys: []
""",
        encoding="utf-8",
    )

    errors = validate_registry(bad)

    assert any("unverified references cannot be production-supporting" in err for err in errors)


def test_reference_registry_validator_passes_current_registry():
    assert validate_registry(REGISTRY) == []
