"""Research-only Engine A/B calibration sweep harness.

WARNING: Research-only. Does not mutate production ``config.yaml`` defaults,
does not auto-tune live thresholds, and must not be used to pick live settings
from raw PnL alone. Rankings use robust, sample-size-aware scores.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_ROW_FIELDS = [
    "engine",
    "asset_class",
    "score_group",
    "symbol",
    "regime",
    "timeframe_style",
    "parameter_set",
    "trade_count",
    "pass_count",
    "fail_count",
    "win_rate",
    "expectancy",
    "max_drawdown",
    "sqn",
    "average_rr_achieved",
    "level_mode",
    "fallback_tp_applied",
    "sample_size_warning",
    "robust_rank_score",
    "sample_period",
    "split_label",
    "parameter_variants_tested",
]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    return value


def _loaded_config() -> dict[str, Any] | None:
    module = sys.modules.get("config")
    cfg = getattr(module, "CONFIG", None) if module is not None else None
    return cfg if isinstance(cfg, dict) else None


def _parse_csv_floats(raw: str | None) -> list[float] | None:
    if raw is None:
        return None
    values: list[float] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        values.append(float(part))
    return values


def _stable_parameter_id(engine: str, overrides: dict[str, Any]) -> str:
    raw = json.dumps(_json_safe(overrides), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{engine.lower()}_{digest}"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


@contextmanager
def temporary_config(overrides: dict[str, Any]):
    cfg = _loaded_config()
    if cfg is None:
        yield {}
        return
    before = copy.deepcopy(cfg)
    try:
        cfg.clear()
        cfg.update(_deep_merge(before, overrides))
        yield cfg
    finally:
        cfg.clear()
        cfg.update(before)


def _engine_a_variants(
    *,
    preset: str,
    score_thresholds: list[float] | None = None,
    directional_ramp_min: list[float] | None = None,
    directional_ramp_full: list[float] | None = None,
    adx_trend_min: list[float] | None = None,
    adx_hard_fail: list[float] | None = None,
    volatility_low: list[float] | None = None,
    volatility_high: list[float] | None = None,
) -> list[dict[str, Any]]:
    if preset == "tiny":
        score_thresholds = score_thresholds or [2.1]
        directional_ramp_min = directional_ramp_min or [0.25]
        directional_ramp_full = directional_ramp_full or [0.45]
        adx_trend_min = adx_trend_min or [25.0]
        adx_hard_fail = adx_hard_fail or [15.0]
        volatility_low = volatility_low or [0.005]
        volatility_high = volatility_high or [0.025]
    else:
        score_thresholds = score_thresholds or [2.0, 2.1, 2.2]
        directional_ramp_min = directional_ramp_min or [0.20, 0.25, 0.30]
        directional_ramp_full = directional_ramp_full or [0.40, 0.45, 0.50]
        adx_trend_min = adx_trend_min or [22.0, 25.0, 28.0]
        adx_hard_fail = adx_hard_fail or [12.0, 15.0, 18.0]
        volatility_low = volatility_low or [0.004, 0.005]
        volatility_high = volatility_high or [0.020, 0.025, 0.030]

    variants: list[dict[str, Any]] = []
    for threshold, ramp_min, ramp_full, trend_min, hard_fail, vol_low, vol_high in product(
        score_thresholds,
        directional_ramp_min,
        directional_ramp_full,
        adx_trend_min,
        adx_hard_fail,
        volatility_low,
        volatility_high,
    ):
        if ramp_full <= ramp_min:
            raise ValueError("ENGINE_A directional ramp full value must be greater than min value")
        if hard_fail >= trend_min:
            raise ValueError("ENGINE_A ADX hard fail must be lower than ADX trend min")
        if vol_high <= vol_low:
            raise ValueError("ENGINE_A volatility scaler high band must be greater than low band")
        overrides = {
            "ENGINE_A_SCORE_GROUP_THRESHOLDS": {"default": threshold},
            "FACTOR_MIN_DIRECTIONAL": ramp_min,
            "FACTOR_DIRECTIONAL_SOFT_SPAN": ramp_full - ramp_min,
            "ADX_TREND_MIN": trend_min,
            "ADX_TREND_MIN_CLASS": {"default": trend_min},
            "FACTOR_ADX_HARD_FAIL_CLASS": {"default": hard_fail},
            "VOLATILITY_SCALER_BANDS": {
                "default": {"low": vol_low, "high": vol_high}
            },
        }
        variants.append(
            {
                "engine": "ENGINE_A",
                "overrides": overrides,
                "parameters": {
                    "score_group_threshold_default": threshold,
                    "directional_ramp_min": ramp_min,
                    "directional_ramp_full": ramp_full,
                    "adx_trend_min": trend_min,
                    "adx_hard_fail": hard_fail,
                    "volatility_scaler_low": vol_low,
                    "volatility_scaler_high": vol_high,
                },
            }
        )
    return variants


def _engine_b_variants(
    *,
    preset: str,
    engine_b_min_score: list[float] | None = None,
    engine_b_min_rr: list[float] | None = None,
    min_room_atr: list[float] | None = None,
    regime_multipliers: list[float] | None = None,
    fallback_tp_enabled: list[bool] | None = None,
) -> list[dict[str, Any]]:
    if preset == "tiny":
        engine_b_min_score = engine_b_min_score or [4.0]
        engine_b_min_rr = engine_b_min_rr or [1.5]
        min_room_atr = min_room_atr or [0.25]
        regime_multipliers = regime_multipliers or [1.0]
        fallback_tp_enabled = fallback_tp_enabled or [True]
    else:
        engine_b_min_score = engine_b_min_score or [3.5, 4.0, 4.5]
        engine_b_min_rr = engine_b_min_rr or [1.2, 1.5, 2.0]
        min_room_atr = min_room_atr or [0.20, 0.25, 0.35]
        regime_multipliers = regime_multipliers or [0.90, 1.0, 1.10]
        fallback_tp_enabled = fallback_tp_enabled or [True, False]

    variants: list[dict[str, Any]] = []
    for min_score, min_rr, room, regime_mult, fallback_enabled in product(
        engine_b_min_score,
        engine_b_min_rr,
        min_room_atr,
        regime_multipliers,
        fallback_tp_enabled,
    ):
        if min_score <= 0:
            raise ValueError("Engine B min_score values must be positive")
        if min_rr <= 0:
            raise ValueError("Engine B min_rr values must be positive")
        if room < 0:
            raise ValueError("Engine B min_room_atr values must be non-negative")
        if regime_mult <= 0:
            raise ValueError("Engine B regime multiplier values must be positive")
        overrides = {
            "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": bool(fallback_enabled),
            "NAKED_ENGINE": {
                "style_profiles": {
                    "intraday": {
                        "min_score": min_score,
                        "min_rr": min_rr,
                        "min_room_atr": room,
                        "fallback_rr": 2.0 if fallback_enabled else None,
                    }
                }
            },
            "ENGINE_B_REGIME_MULTIPLIERS": {
                "TRENDING": regime_mult,
                "NORMAL": regime_mult,
                "RANGING": regime_mult,
                "HIGH_VOLATILITY": regime_mult,
                "LOW_VOLATILITY": regime_mult,
            },
        }
        variants.append(
            {
                "engine": "ENGINE_B",
                "overrides": overrides,
                "parameters": {
                    "style": "intraday",
                    "style_min_score": min_score,
                    "style_min_rr": min_rr,
                    "min_room_atr": room,
                    "regime_multiplier": regime_mult,
                    "fallback_tp_enabled": bool(fallback_enabled),
                },
            }
        )
    return variants


def build_sweep_space(
    engine: str,
    *,
    preset: str = "tiny",
    directional_ramp_min: list[float] | None = None,
    directional_ramp_full: list[float] | None = None,
    engine_b_min_rr: list[float] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    engine_norm = str(engine or "").lower()
    preset_norm = str(preset or "tiny").lower()
    if preset_norm not in {"tiny", "custom", "standard"}:
        raise ValueError("preset must be one of: tiny, custom, standard")
    if engine_norm == "engine_a":
        return _engine_a_variants(
            preset=preset_norm,
            directional_ramp_min=directional_ramp_min,
            directional_ramp_full=directional_ramp_full,
            **kwargs,
        )
    if engine_norm == "engine_b":
        return _engine_b_variants(
            preset=preset_norm,
            engine_b_min_rr=engine_b_min_rr,
            **kwargs,
        )
    if engine_norm == "both":
        engine_a_keys = {
            "score_thresholds",
            "adx_trend_min",
            "adx_hard_fail",
            "volatility_low",
            "volatility_high",
        }
        engine_b_keys = {
            "engine_b_min_score",
            "min_room_atr",
            "regime_multipliers",
            "fallback_tp_enabled",
        }
        engine_a_kwargs = {k: v for k, v in kwargs.items() if k in engine_a_keys}
        engine_b_kwargs = {k: v for k, v in kwargs.items() if k in engine_b_keys}
        return build_sweep_space(
            "engine_a",
            preset=preset_norm,
            directional_ramp_min=directional_ramp_min,
            directional_ramp_full=directional_ramp_full,
            **engine_a_kwargs,
        ) + build_sweep_space(
            "engine_b",
            preset=preset_norm,
            engine_b_min_rr=engine_b_min_rr,
            **engine_b_kwargs,
        )
    raise ValueError("engine must be one of: engine_a, engine_b, both")


def _load_records(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("trades") or []
    if not isinstance(data, list):
        raise ValueError("input JSON must contain a list of rows or a dict with rows/trades")
    return [row for row in data if isinstance(row, dict)]


def _mean(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _robust_rank(row: dict[str, Any]) -> float:
    trade_count = float(row.get("trade_count") or 0)
    expectancy = float(row.get("expectancy") or 0.0)
    sqn = float(row.get("sqn") or 0.0)
    drawdown = abs(float(row.get("max_drawdown") or 0.0))
    rr = float(row.get("average_rr_achieved") or 0.0)
    sample_penalty = 1.0 if trade_count >= 30 else trade_count / 30.0
    return round(((expectancy * 2.0) + sqn + (rr * 0.25) - (drawdown * 0.5)) * sample_penalty, 6)


def _records_for_variant(
    variant: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    dry_run: bool,
    min_sample_size: int,
    sample_period: str,
    split_label: str,
    parameter_variants_tested: int,
) -> list[dict[str, Any]]:
    engine = variant["engine"]
    parameter_set = _stable_parameter_id(engine, variant["overrides"])
    source_records = [
        row for row in records if str(row.get("engine", engine)).upper() == engine
    ]
    if dry_run and not source_records:
        source_records = [
            {
                "engine": engine,
                "asset_class": "dry_run",
                "score_group": "dry_run",
                "symbol": "DRYRUN",
                "regime": "UNKNOWN",
                "timeframe_style": "intraday",
                "passed": False,
                "rr_actual": None,
                "level_mode": "unknown_level_mode" if engine == "ENGINE_B" else "",
                "fallback_tp_applied": False if engine == "ENGINE_B" else "",
            }
        ]

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in source_records:
        key = (
            engine,
            row.get("asset_class") or row.get("asset_type") or "",
            row.get("score_group") or "",
            row.get("symbol") or row.get("pair") or "",
            row.get("regime") or "",
            row.get("timeframe_style") or row.get("style") or row.get("timeframe") or "",
            parameter_set,
            row.get("level_mode") if engine == "ENGINE_B" else "",
            row.get("fallback_tp_applied") if engine == "ENGINE_B" else "",
        )
        grouped.setdefault(key, []).append(row)

    rows: list[dict[str, Any]] = []
    for key, items in grouped.items():
        passed = sum(1 for row in items if bool(row.get("passed", row.get("win", False))))
        failed = len(items) - passed
        wins = [row for row in items if row.get("win") is not None]
        win_rate = None
        if wins:
            win_rate = sum(1 for row in wins if bool(row.get("win"))) / len(wins)
        expectancy = _mean(
            row.get("expectancy", row.get("expectancy_r", row.get("resultR", row.get("r_multiple"))))
            for row in items
        )
        max_drawdown = _mean(row.get("max_drawdown", row.get("max_dd_pct")) for row in items)
        sqn = _mean(row.get("sqn") for row in items)
        avg_rr = _mean(row.get("rr_actual", row.get("rr", row.get("execution_rr"))) for row in items)
        out = {
            "engine": key[0],
            "asset_class": key[1],
            "score_group": key[2],
            "symbol": key[3],
            "regime": key[4],
            "timeframe_style": key[5],
            "parameter_set": key[6],
            "trade_count": len(items),
            "pass_count": passed,
            "fail_count": failed,
            "win_rate": round(win_rate, 6) if win_rate is not None else None,
            "expectancy": round(expectancy, 6) if expectancy is not None else None,
            "max_drawdown": round(max_drawdown, 6) if max_drawdown is not None else None,
            "sqn": round(sqn, 6) if sqn is not None else None,
            "average_rr_achieved": round(avg_rr, 6) if avg_rr is not None else None,
            "level_mode": key[7],
            "fallback_tp_applied": key[8],
            "sample_size_warning": len(items) < min_sample_size,
            "sample_period": sample_period,
            "split_label": split_label,
            "parameter_variants_tested": parameter_variants_tested,
            "parameters": variant["parameters"],
        }
        out["robust_rank_score"] = _robust_rank(out)
        rows.append(out)
    return rows


def _write_reports(
    *,
    output_dir: Path,
    metadata: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "calibration_sweep.json"
    csv_path = output_dir / "calibration_sweep.csv"
    payload = {"metadata": _json_safe(metadata), "rows": _json_safe(rows)}
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = REQUIRED_ROW_FIELDS + ["parameters"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flat = {key: row.get(key) for key in REQUIRED_ROW_FIELDS}
            flat["parameters"] = json.dumps(_json_safe(row.get("parameters", {})), sort_keys=True)
            writer.writerow(flat)
    return {"json": str(json_path), "csv": str(csv_path)}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Research-only Engine A/B calibration sweep harness. Does not mutate live config."
    )
    parser.add_argument("--engine", choices=("engine_a", "engine_b", "both"), default="both")
    parser.add_argument("--preset", choices=("tiny", "standard", "custom"), default="tiny")
    parser.add_argument("--dry-run", action="store_true", help="Use synthetic rows when no input data is supplied.")
    parser.add_argument("--input-json", help="Optional JSON rows/trades to aggregate under each parameter variant.")
    parser.add_argument("--output-dir", default="reports/calibration_sweep")
    parser.add_argument("--sample-period", default="unspecified")
    parser.add_argument("--split-label", default="unspecified")
    parser.add_argument("--min-sample-size", type=int, default=30)
    parser.add_argument("--engine-a-thresholds")
    parser.add_argument("--engine-a-directional-min")
    parser.add_argument("--engine-a-directional-full")
    parser.add_argument("--engine-a-adx-trend-min")
    parser.add_argument("--engine-a-adx-hard-fail")
    parser.add_argument("--engine-a-volatility-low")
    parser.add_argument("--engine-a-volatility-high")
    parser.add_argument("--engine-b-min-score")
    parser.add_argument("--engine-b-min-rr")
    parser.add_argument("--engine-b-min-room-atr")
    parser.add_argument("--engine-b-regime-multiplier")
    parser.add_argument(
        "--engine-b-fallback-tp",
        choices=("enabled", "disabled", "both"),
        default=None,
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    fallback_values = None
    if args.engine_b_fallback_tp == "enabled":
        fallback_values = [True]
    elif args.engine_b_fallback_tp == "disabled":
        fallback_values = [False]
    elif args.engine_b_fallback_tp == "both":
        fallback_values = [True, False]

    variants: list[dict[str, Any]] = []
    if args.engine in {"engine_a", "both"}:
        variants.extend(
            build_sweep_space(
                "engine_a",
                preset=args.preset,
                score_thresholds=_parse_csv_floats(args.engine_a_thresholds),
                directional_ramp_min=_parse_csv_floats(args.engine_a_directional_min),
                directional_ramp_full=_parse_csv_floats(args.engine_a_directional_full),
                adx_trend_min=_parse_csv_floats(args.engine_a_adx_trend_min),
                adx_hard_fail=_parse_csv_floats(args.engine_a_adx_hard_fail),
                volatility_low=_parse_csv_floats(args.engine_a_volatility_low),
                volatility_high=_parse_csv_floats(args.engine_a_volatility_high),
            )
        )
    if args.engine in {"engine_b", "both"}:
        variants.extend(
            build_sweep_space(
                "engine_b",
                preset=args.preset,
                engine_b_min_score=_parse_csv_floats(args.engine_b_min_score),
                engine_b_min_rr=_parse_csv_floats(args.engine_b_min_rr),
                min_room_atr=_parse_csv_floats(args.engine_b_min_room_atr),
                regime_multipliers=_parse_csv_floats(args.engine_b_regime_multiplier),
                fallback_tp_enabled=fallback_values,
            )
        )

    records = _load_records(args.input_json)
    if not args.dry_run and not records:
        raise ValueError("--input-json is required unless --dry-run is set")

    rows: list[dict[str, Any]] = []
    variant_count = len(variants)
    sample_period = str(args.sample_period)
    split_label = str(args.split_label)
    for variant in variants:
        with temporary_config(variant["overrides"]):
            rows.extend(
                _records_for_variant(
                    variant,
                    records,
                    dry_run=args.dry_run,
                    min_sample_size=max(1, int(args.min_sample_size)),
                    sample_period=sample_period,
                    split_label=split_label,
                    parameter_variants_tested=variant_count,
                )
            )

    rows.sort(key=lambda row: row.get("robust_rank_score", 0.0), reverse=True)
    metadata = {
        "research_only": True,
        "live_config_mutation": False,
        "live_execution_behavior_changed": False,
        "threshold_auto_tuning": False,
        "engines_merged": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run" if args.dry_run else "input_json",
        "engine": args.engine,
        "sample_period": args.sample_period,
        "split_label": args.split_label,
        "parameter_variants_tested": len(variants),
        "ranking_policy": "robust_metrics_not_raw_pnl",
        "small_sample_min_trades": max(1, int(args.min_sample_size)),
        "small_sample_warning_count": sum(1 for row in rows if row.get("sample_size_warning")),
        "warning": (
            "RESEARCH ONLY — not live auto-tuning. Do not promote settings from "
            "raw PnL or tiny samples. Validate with walk-forward / out-of-sample."
        ),
    }
    files = _write_reports(output_dir=Path(args.output_dir), metadata=metadata, rows=rows)
    return {**metadata, "rows": rows, "files": files}


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
    except Exception as exc:
        print(f"calibration_sweep failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_json_safe({"files": result["files"], "metadata": {k: result[k] for k in result if k not in {"rows", "files"}}}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
