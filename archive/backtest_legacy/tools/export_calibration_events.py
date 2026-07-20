"""Export scan-time calibration diagnostics JSONL to normalized research artifacts.

Reads ``calibration_events.jsonl`` (gate/pass telemetry at scan time). This is
**not** the closed-trade sweep pipeline (``audit.db`` / ``calibration_sweep.py``).

Usage (PowerShell)::

    .venv\\Scripts\\python.exe tools/export_calibration_events.py `
      --input logs\\calibration_diagnostics\\calibration_events.jsonl `
      --output-dir logs\\calibration\\diagnostics_normalized

Does not mutate config, scoring, thresholds, or execution.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_diagnostics import DEFAULT_DIAGNOSTICS_PATH  # noqa: E402

NORMALIZED_FIELDS = [
    "timestamp",
    "engine",
    "symbol",
    "pair",
    "asset_class",
    "score_group",
    "regime",
    "timeframe_style",
    "raw_score",
    "max_score",
    "score_pct",
    "final_score",
    "threshold",
    "distance_to_threshold",
    "passed",
    "failure_reason",
    "engine_a_trend_score",
    "engine_a_adx_mult",
    "engine_a_di_align_mult",
    "engine_a_dir_ramp_mult",
    "engine_a_volatility_scaler",
    "engine_b_structure_ok",
    "engine_b_location_ok",
    "engine_b_entry_ok",
    "engine_b_space_gate_ok",
    "engine_b_rr_ok",
    "engine_b_macro_ok",
    "engine_b_level_mode",
    "engine_b_level_cohort",
    "engine_b_fallback_tp_applied",
    "engine_b_rr_required",
    "engine_b_rr_actual",
    "engine_b_rr_passed",
    "engine_b_min_score",
    "engine_b_scaled_min_score",
    "engine_b_regime_multiplier",
    "source_line",
]

ENGINE_A_FACTOR_KEYS = (
    ("trend_score", "engine_a_trend_score"),
    ("adx_mult", "engine_a_adx_mult"),
    ("di_align_mult", "engine_a_di_align_mult"),
    ("dir_ramp_mult", "engine_a_dir_ramp_mult"),
    ("volatility_scaler", "engine_a_volatility_scaler"),
)

ENGINE_B_GATE_KEYS = (
    ("structure_ok", "engine_b_structure_ok"),
    ("location_ok", "engine_b_location_ok"),
    ("entry_ok", "engine_b_entry_ok"),
    ("space_gate_ok", "engine_b_space_gate_ok"),
    ("room_ok", "engine_b_space_gate_ok"),
    ("rr_ok", "engine_b_rr_ok"),
    ("macro_ok", "engine_b_macro_ok"),
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        if out != out:
            return None
        return out
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _normalize_engine(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"engine_a", "a"}:
        return "ENGINE_A"
    if text in {"engine_b", "b"}:
        return "ENGINE_B"
    return text.upper()


def _score_for_distance(row: dict[str, Any]) -> float | None:
    engine = row.get("engine")
    raw = _safe_float(row.get("raw_score"))
    if raw is not None:
        return raw
    if engine == "ENGINE_A":
        return _safe_float(row.get("final_score"))
    return None


def compute_distance_to_threshold(score: float | None, threshold: float | None) -> float | None:
    if score is None or threshold is None:
        return None
    return round(score - threshold, 6)


def parse_jsonl(
    path: Path,
    *,
    strict: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (parsed_objects, malformed_line_records)."""
    parsed: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    if not path.exists():
        return parsed, malformed
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                record = {"line": line_no, "error": str(exc), "raw": stripped[:500]}
                malformed.append(record)
                if strict:
                    raise ValueError(f"malformed JSONL at line {line_no}: {exc}") from exc
                continue
            if not isinstance(obj, dict):
                record = {"line": line_no, "error": "expected JSON object", "raw": stripped[:500]}
                malformed.append(record)
                if strict:
                    raise ValueError(f"expected object at line {line_no}")
                continue
            obj["_source_line"] = line_no
            parsed.append(obj)
    return parsed, malformed


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    engine = _normalize_engine(raw.get("engine"))
    symbol = raw.get("symbol") or raw.get("pair")
    pair = raw.get("pair") or raw.get("symbol")
    timeframe_style = raw.get("timeframe_style") or raw.get("timeframe") or raw.get("style")
    raw_score = _safe_float(raw.get("raw_score"))
    if raw_score is None:
        raw_score = _safe_float(raw.get("score"))
    final_score = _safe_float(raw.get("final_score"))
    max_score = _safe_float(raw.get("max_score"))
    score_pct = _safe_float(raw.get("score_pct"))
    if score_pct is not None and score_pct > 1.0:
        score_pct = round(score_pct / 100.0, 6)
    threshold = _safe_float(raw.get("threshold"))
    if threshold is None:
        threshold = _safe_float(raw.get("scaled_min_score"))

    row: dict[str, Any] = {key: None for key in NORMALIZED_FIELDS}
    row["timestamp"] = raw.get("timestamp")
    row["engine"] = engine
    row["symbol"] = symbol
    row["pair"] = pair
    row["asset_class"] = raw.get("asset_class") or raw.get("asset_type")
    row["score_group"] = raw.get("score_group")
    row["regime"] = raw.get("regime")
    row["timeframe_style"] = timeframe_style
    row["raw_score"] = raw_score
    row["max_score"] = max_score
    row["score_pct"] = score_pct
    row["final_score"] = final_score if engine == "ENGINE_A" else None
    row["threshold"] = threshold
    row["distance_to_threshold"] = compute_distance_to_threshold(
        _score_for_distance(
            {
                "engine": engine,
                "raw_score": raw_score,
                "final_score": final_score,
            }
        ),
        threshold,
    )
    row["passed"] = _safe_bool(raw.get("passed"))
    failure = raw.get("failure_reason")
    if failure is None and row["passed"] is False:
        failure = "failed_unknown"
    row["failure_reason"] = str(failure) if failure not in (None, "") else None
    row["source_line"] = raw.get("_source_line")

    if engine == "ENGINE_A":
        for src, dest in ENGINE_A_FACTOR_KEYS:
            row[dest] = _safe_float(raw.get(src))
    elif engine == "ENGINE_B":
        for src, dest in ENGINE_B_GATE_KEYS:
            if dest == "engine_b_space_gate_ok" and row.get(dest) is not None:
                continue
            val = _safe_bool(raw.get(src))
            if val is not None:
                row[dest] = val
        row["engine_b_level_mode"] = raw.get("level_mode")
        row["engine_b_level_cohort"] = raw.get("level_cohort")
        row["engine_b_fallback_tp_applied"] = _safe_bool(raw.get("fallback_tp_applied"))
        row["engine_b_rr_required"] = _safe_float(raw.get("rr_required"))
        if row["engine_b_rr_required"] is None:
            row["engine_b_rr_required"] = _safe_float(raw.get("min_rr"))
        row["engine_b_rr_actual"] = _safe_float(raw.get("rr_actual"))
        if row["engine_b_rr_actual"] is None:
            row["engine_b_rr_actual"] = _safe_float(raw.get("rr"))
        row["engine_b_rr_passed"] = _safe_bool(raw.get("rr_passed"))
        if row["engine_b_rr_passed"] is None:
            row["engine_b_rr_passed"] = _safe_bool(raw.get("rr_ok"))
        row["engine_b_min_score"] = _safe_float(raw.get("min_score"))
        row["engine_b_scaled_min_score"] = _safe_float(raw.get("scaled_min_score"))
        row["engine_b_regime_multiplier"] = _safe_float(raw.get("regime_multiplier"))

    return row


def normalize_events(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_event(row) for row in raw_rows]


def _failure_label(row: dict[str, Any]) -> str:
    if row.get("passed") is True:
        return "passed"
    reason = row.get("failure_reason")
    return str(reason) if reason else "failed_unknown"


def _aggregate_failure_reasons(
    rows: list[dict[str, Any]],
    group_keys: list[str],
    *,
    include_failure_reason: bool = False,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        keys = list(group_keys)
        if include_failure_reason:
            keys = keys + ["failure_reason"]
        key_parts = []
        for k in keys:
            if k == "failure_reason":
                key_parts.append(_failure_label(row))
            else:
                key_parts.append(row.get(k) or "(blank)")
        buckets[tuple(key_parts)].append(row)
    out: list[dict[str, Any]] = []
    for key, items in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0])):
        record: dict[str, Any] = {}
        idx = 0
        for k in group_keys:
            record[k] = key[idx]
            idx += 1
        if include_failure_reason:
            record["failure_reason"] = key[idx]
            idx += 1
        record["event_count"] = len(items)
        record["passed_count"] = sum(1 for x in items if x.get("passed") is True)
        record["failed_count"] = sum(1 for x in items if x.get("passed") is False)
        out.append(record)
    return out


def build_engine_a_distance_report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    engine_a = [r for r in rows if r.get("engine") == "ENGINE_A"]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in engine_a:
        dist = row.get("distance_to_threshold")
        if dist is None:
            buckets["missing_distance"].append(row)
            continue
        if dist < 0:
            buckets["below_threshold"].append(row)
        elif dist == 0:
            buckets["at_threshold"].append(row)
        else:
            buckets["above_threshold"].append(row)
    report: list[dict[str, Any]] = []
    for bucket, items in sorted(buckets.items()):
        distances = [float(x["distance_to_threshold"]) for x in items if x.get("distance_to_threshold") is not None]
        report.append(
            {
                "distance_bucket": bucket,
                "event_count": len(items),
                "passed_count": sum(1 for x in items if x.get("passed") is True),
                "failed_count": sum(1 for x in items if x.get("passed") is False),
                "avg_distance_to_threshold": round(sum(distances) / len(distances), 6) if distances else None,
                "min_distance_to_threshold": round(min(distances), 6) if distances else None,
                "max_distance_to_threshold": round(max(distances), 6) if distances else None,
            }
        )
    return report


def build_engine_b_gate_failures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    engine_b = [r for r in rows if r.get("engine") == "ENGINE_B"]
    gate_cols = [dest for _, dest in ENGINE_B_GATE_KEYS if dest != "engine_b_space_gate_ok"]
    gate_cols = list(dict.fromkeys(gate_cols + ["engine_b_rr_passed"]))
    report: list[dict[str, Any]] = []
    for gate in gate_cols:
        false_rows = [r for r in engine_b if r.get(gate) is False]
        if not false_rows:
            continue
        report.append(
            {
                "gate": gate,
                "fail_count": len(false_rows),
                "passed_gate_count": sum(1 for r in engine_b if r.get(gate) is True),
                "unknown_count": sum(1 for r in engine_b if r.get(gate) is None),
                "top_failure_reason": Counter(
                    _failure_label(r) for r in false_rows
                ).most_common(1)[0][0],
            }
        )
    return sorted(report, key=lambda r: -int(r["fail_count"]))


def build_engine_b_fallback_tp_diagnostics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    engine_b = [r for r in rows if r.get("engine") == "ENGINE_B"]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in engine_b:
        val = row.get("engine_b_fallback_tp_applied")
        key = "null" if val is None else str(bool(val)).lower()
        buckets[key].append(row)
    report: list[dict[str, Any]] = []
    for key, items in sorted(buckets.items()):
        rr_vals = [x["engine_b_rr_actual"] for x in items if x.get("engine_b_rr_actual") is not None]
        report.append(
            {
                "fallback_tp_applied": key,
                "event_count": len(items),
                "passed_count": sum(1 for x in items if x.get("passed") is True),
                "failed_count": sum(1 for x in items if x.get("passed") is False),
                "avg_rr_actual": round(sum(rr_vals) / len(rr_vals), 6) if rr_vals else None,
                "top_failure_reason": Counter(_failure_label(x) for x in items).most_common(1)[0][0],
            }
        )
    return report


def build_summary_rows(rows: list[dict[str, Any]], malformed_count: int) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for engine in ("ENGINE_A", "ENGINE_B"):
        subset = [r for r in rows if r.get("engine") == engine]
        if not subset:
            continue
        reasons = Counter(
            _failure_label(r) for r in subset if r.get("passed") is not True
        )
        summary.append(
            {
                "engine": engine,
                "event_count": len(subset),
                "passed_count": sum(1 for r in subset if r.get("passed") is True),
                "failed_count": sum(1 for r in subset if r.get("passed") is False),
                "top_failure_reason": reasons.most_common(1)[0][0] if reasons else "passed",
                "malformed_jsonl_lines": malformed_count,
            }
        )
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def export_calibration_events(
    *,
    input_path: Path,
    output_dir: Path,
    strict: bool = False,
    also_write_calibration_root: bool = True,
) -> dict[str, Any]:
    raw_rows, malformed = parse_jsonl(input_path, strict=strict)
    normalized = normalize_events(raw_rows)
    summary_rows = build_summary_rows(normalized, len(malformed))

    output_dir.mkdir(parents=True, exist_ok=True)
    calibration_root = ROOT / "logs" / "calibration"

    normalized_json = output_dir / "diagnostic_events_normalized.json"
    summary_csv = output_dir / "diagnostic_events_summary.csv"
    payload = {
        "metadata": {
            "research_only": True,
            "pipeline": "calibration_events_jsonl",
            "separate_from_closed_trades": True,
            "input": str(input_path),
            "normalized_row_count": len(normalized),
            "malformed_line_count": len(malformed),
        },
        "malformed_lines": malformed,
        "rows": normalized,
    }
    normalized_json.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True), encoding="utf-8")
    _write_csv(summary_csv, summary_rows, list(summary_rows[0].keys()) if summary_rows else ["engine", "event_count"])

    reports = {
        "by_engine_failure_reason": _aggregate_failure_reasons(
            normalized, ["engine"], include_failure_reason=True
        ),
        "by_engine_asset_class_failure_reason": _aggregate_failure_reasons(
            normalized, ["engine", "asset_class"], include_failure_reason=True
        ),
        "by_engine_score_group_failure_reason": _aggregate_failure_reasons(
            normalized, ["engine", "score_group"], include_failure_reason=True
        ),
        "by_engine_regime_failure_reason": _aggregate_failure_reasons(
            normalized, ["engine", "regime"], include_failure_reason=True
        ),
        "engine_a_distance_to_threshold": build_engine_a_distance_report(normalized),
        "engine_b_gate_failures": build_engine_b_gate_failures(normalized),
        "engine_b_fallback_tp_diagnostics": build_engine_b_fallback_tp_diagnostics(normalized),
    }
    files: dict[str, str] = {
        "normalized_json": str(normalized_json),
        "summary_csv": str(summary_csv),
    }
    for name, rows in reports.items():
        path = output_dir / f"{name}.csv"
        if not rows:
            _write_csv(path, [], ["note"])
            files[name] = str(path)
            continue
        fieldnames = list(rows[0].keys())
        _write_csv(path, rows, fieldnames)
        files[name] = str(path)

    if also_write_calibration_root and output_dir.resolve() != calibration_root.resolve():
        calibration_root.mkdir(parents=True, exist_ok=True)
        root_json = calibration_root / "diagnostic_events_normalized.json"
        root_csv = calibration_root / "diagnostic_events_summary.csv"
        root_json.write_text(normalized_json.read_text(encoding="utf-8"), encoding="utf-8")
        _write_csv(root_csv, summary_rows, list(summary_rows[0].keys()) if summary_rows else ["engine"])
        files["calibration_root_normalized_json"] = str(root_json)
        files["calibration_root_summary_csv"] = str(root_csv)

    top_failures: dict[str, list[tuple[str, int]]] = {}
    for engine in ("ENGINE_A", "ENGINE_B"):
        subset = [r for r in normalized if r.get("engine") == engine and r.get("passed") is not True]
        top_failures[engine] = Counter(
            _failure_label(r) for r in subset
        ).most_common(10)

    return {
        "normalized_row_count": len(normalized),
        "malformed_line_count": len(malformed),
        "files": files,
        "top_failure_reasons": top_failures,
        "summary_rows": summary_rows,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_DIAGNOSTICS_PATH))
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "logs" / "calibration" / "diagnostics_normalized"),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Abort on first malformed JSONL line (default: skip and record).",
    )
    parser.add_argument(
        "--no-calibration-root-copy",
        action="store_true",
        help="Do not mirror normalized JSON/CSV to logs/calibration/.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = export_calibration_events(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        strict=bool(args.strict),
        also_write_calibration_root=not args.no_calibration_root_copy,
    )
    print(json.dumps(_json_safe(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
