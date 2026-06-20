from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from athena_research.commodity_trend_v1.preflight import (
    ELIGIBLE_SYMBOLS,
    PreflightError,
    build_preflight_report,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture_repo(tmp_path: Path, *, omit_d1: set[str] | None = None) -> Path:
    omit_d1 = omit_d1 or set()
    clusters = []
    for symbol in ELIGIBLE_SYMBOLS:
        slug = symbol.replace("/", "_").replace(" ", "_")
        review = Path("reviews") / slug / "H4.review.json"
        _write_json(tmp_path / review, {"gate": "CLEAR_ON_FREEZE", "events": []})
        clusters.append(
            {
                "canonical_symbol": symbol,
                "artifact": review.as_posix(),
                "gate": "CLEAR_ON_FREEZE",
                "qualifying": True,
                "raw_content_hash": "a" * 64,
                "raw_content_unchanged_vs_artifact": True,
            }
        )
        for timeframe in ("H4", "D1"):
            if timeframe == "D1" and symbol in omit_d1:
                continue
            base = tmp_path / "logs/commodity_data_audit/normalized/commodity_ohlcv_v1" / slug
            _write_json(base / f"{timeframe}.json", [{"timestamp": "2026-01-01T00:00:00+00:00"}])
            _write_json(
                base / f"{timeframe}.manifest.json",
                {"provisional_row_count_at_capture": 0, "confirmed_row_count": 1},
            )
    clusters.append(
        {
            "canonical_symbol": "XPD/USD",
            "artifact": "reviews/XPD_USD/H4.review.json",
            "gate": "BLOCKED_PROVIDER_DEGRADATION",
            "qualifying": False,
            "raw_content_hash": "b" * 64,
            "raw_content_unchanged_vs_artifact": True,
        }
    )
    _write_json(
        tmp_path / "gate.json",
        {
            "schema": "commodity_h4_authoritative_gate_table_v1",
            "qualifying_count": 8,
            "success_condition_eight_qualifying": True,
            "all_raw_unchanged": True,
            "qualifying_clusters": list(ELIGIBLE_SYMBOLS),
            "clusters": clusters,
            "run_hash": "c" * 64,
        },
    )
    return tmp_path / "gate.json"


def test_complete_preflight_is_ready_and_excludes_xpd(tmp_path: Path) -> None:
    gate_path = _fixture_repo(tmp_path)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["qualifying_clusters"] = list(reversed(gate["qualifying_clusters"]))
    _write_json(gate_path, gate)

    report = build_preflight_report(tmp_path, gate_path)

    assert report["verdict"] == "READY_FOR_STRATEGY_RESEARCH"
    assert report["eligible_symbols"] == list(ELIGIBLE_SYMBOLS)
    assert "XPD/USD" not in report["eligible_symbols"]
    assert report["missing_required_artifacts"] == []


def test_missing_d1_fails_closed_and_report_hash_is_deterministic(tmp_path: Path) -> None:
    gate = _fixture_repo(tmp_path, omit_d1={"XAU/USD", "Nat Gas"})

    first = build_preflight_report(tmp_path, gate)
    second = build_preflight_report(tmp_path, gate)

    assert first["verdict"] == "BLOCKED_DATA"
    assert first["run_hash"] == second["run_hash"]
    assert {item["symbol"] for item in first["missing_required_artifacts"]} == {
        "XAU/USD",
        "Nat Gas",
    }
    assert all(item["timeframe"] == "D1" for item in first["missing_required_artifacts"])


def test_changed_raw_hash_status_is_rejected(tmp_path: Path) -> None:
    gate_path = _fixture_repo(tmp_path)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["all_raw_unchanged"] = False
    _write_json(gate_path, gate)

    with pytest.raises(PreflightError, match="raw hashes are not unchanged"):
        build_preflight_report(tmp_path, gate_path)


def test_research_preflight_has_no_production_or_execution_imports() -> None:
    package = Path(__file__).parents[1] / "athena_research/commodity_trend_v1"
    forbidden = {"athena", "execution", "auto_trader", "risk_engine", "engine_b"}
    imported: set[str] = set()
    for source_path in package.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)
