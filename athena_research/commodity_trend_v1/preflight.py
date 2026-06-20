from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ELIGIBLE_SYMBOLS = (
    "XAU/USD",
    "XAG/USD",
    "XPT/USD",
    "Copper",
    "WTI Oil",
    "Brent Oil",
    "Nat Gas",
    "Gasoline",
)
ACCEPTED_GATES = {"CLEAR_ON_FREEZE", "CLEAR_WITH_GAP_EMBARGO"}
GATE_SCHEMA = "commodity_h4_authoritative_gate_table_v1"
REPORT_SCHEMA = "commodity_h4_trend_continuation_v1_preflight_v1"


class PreflightError(ValueError):
    """The authoritative gate violates a mandatory integrity contract."""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(symbol: str) -> str:
    return symbol.replace("/", "_").replace(" ", "_")


def _validate_gate(gate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if gate.get("schema") != GATE_SCHEMA:
        raise PreflightError("authoritative gate schema is invalid")
    if gate.get("qualifying_count") != 8 or gate.get("success_condition_eight_qualifying") is not True:
        raise PreflightError("authoritative gate is not 8/8")
    if gate.get("all_raw_unchanged") is not True:
        raise PreflightError("authoritative raw hashes are not unchanged")
    qualifying = gate.get("qualifying_clusters", ())
    if len(qualifying) != len(ELIGIBLE_SYMBOLS) or set(qualifying) != set(ELIGIBLE_SYMBOLS):
        raise PreflightError("authoritative qualifying universe differs from frozen strategy universe")

    by_symbol = {item.get("canonical_symbol"): item for item in gate.get("clusters", [])}
    for symbol in ELIGIBLE_SYMBOLS:
        row = by_symbol.get(symbol)
        if not row or row.get("qualifying") is not True or row.get("gate") not in ACCEPTED_GATES:
            raise PreflightError(f"eligible symbol is not authoritatively clear: {symbol}")
        if row.get("raw_content_unchanged_vs_artifact") is not True:
            raise PreflightError(f"raw hash changed for {symbol}")
        raw_hash = row.get("raw_content_hash")
        if not isinstance(raw_hash, str) or len(raw_hash) != 64:
            raise PreflightError(f"raw hash is invalid for {symbol}")
    if by_symbol.get("XPD/USD", {}).get("qualifying") is not False:
        raise PreflightError("XPD/USD must remain excluded")
    return by_symbol


def build_preflight_report(repo_root: Path, gate_path: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    gate_path = gate_path.resolve()
    gate = _load_json(gate_path)
    by_symbol = _validate_gate(gate)
    missing: list[dict[str, str]] = []
    evidence: dict[str, Any] = {}

    for symbol in ELIGIBLE_SYMBOLS:
        row = by_symbol[symbol]
        review_path = repo_root / row["artifact"]
        symbol_evidence: dict[str, Any] = {
            "gate": row["gate"],
            "raw_content_hash": row["raw_content_hash"],
            "review": str(review_path.relative_to(repo_root)).replace("\\", "/"),
            "review_sha256": _file_hash(review_path) if review_path.is_file() else None,
            "timeframes": {},
        }
        if not review_path.is_file():
            missing.append({"symbol": symbol, "timeframe": "H4", "artifact": "authoritative_review"})

        base = repo_root / "logs/commodity_data_audit/normalized/commodity_ohlcv_v1" / _slug(symbol)
        for timeframe in ("H4", "D1"):
            data_path = base / f"{timeframe}.json"
            manifest_path = base / f"{timeframe}.manifest.json"
            absent = []
            if not data_path.is_file():
                absent.append("normalized_data")
            if not manifest_path.is_file():
                absent.append("normalized_manifest")
            for artifact in absent:
                missing.append({"symbol": symbol, "timeframe": timeframe, "artifact": artifact})
            symbol_evidence["timeframes"][timeframe] = {
                "data_sha256": _file_hash(data_path) if data_path.is_file() else None,
                "manifest_sha256": _file_hash(manifest_path) if manifest_path.is_file() else None,
            }
        evidence[symbol] = symbol_evidence

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "strategy_family": "COMMODITY_H4_TREND_CONTINUATION_V1",
        "authoritative_gate_run_hash": gate.get("run_hash"),
        "eligible_symbols": list(ELIGIBLE_SYMBOLS),
        "excluded_symbols": ["XPD/USD"],
        "verdict": "BLOCKED_DATA" if missing else "READY_FOR_STRATEGY_RESEARCH",
        "missing_required_artifacts": missing,
        "evidence": evidence,
        "performance_inspected": False,
        "raw_data_modified": False,
    }
    report["run_hash"] = _stable_hash(report)
    return report
