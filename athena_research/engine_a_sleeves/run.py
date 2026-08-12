"""Run one Engine A research sleeve against the parquet backtest store.

Usage (from repo root):
  python -m athena_research.engine_a_sleeves.run --sleeve F1
  python -m athena_research.engine_a_sleeves.run --sleeve F1 --overlay-variant timing_gates

Does not write config.yaml. Does not promote artifacts. Does not execute.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
# Import guard only — this process never places orders.
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "sleeves.yaml"
OUTPUT_ROOT = Path(__file__).resolve().parent / "output"

VARIANT_OVERLAYS = {
    "timing_gates": {"ENGINE_A_ENTRY_TIMING_GATES_ENABLED": True},
    "session_gates": {"ENGINE_A_SESSION_GATES_ENABLED": True},
    "mr_floor_025": {
        "ENGINE_A_V3_SETUP_UPGRADE": {"MR_MIN_CONFLUENCE_FRAC": 0.25},
    },
    # Tuning Lab XAU push 2026-08-08 + Backtest V3 run 238 (SQN 3.51 / holdout 2.13).
    # Forces the liquid_metals intradaily ladder regardless of config.local.yaml.
    "tuning_lab_tf": {
        "ENGINE_TF_ROLE_OVERRIDES": {
            "ENABLED": True,
            "BY_GROUP": {
                "liquid_metals": {
                    "intraday": {
                        "regime": "H4",
                        "bias": "H4",
                        "structure": "H4",
                        "setup": "H4",
                        "trigger": "M30",
                    }
                }
            },
        }
    },
    # config.yaml universal A/B ladder — contrast only.
    "policy_default_tf": {
        "ENGINE_TF_ROLE_OVERRIDES": {
            "ENABLED": True,
            "BY_GROUP": {
                "liquid_metals": {
                    "intraday": {
                        "regime": "D1",
                        "bias": "H4",
                        "structure": "H4",
                        "setup": "H1",
                        "trigger": "M15",
                    }
                }
            },
        }
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sleeves"), dict):
        raise ValueError("sleeve_manifest_invalid")
    return raw


def apply_config_overlay(overlay: dict) -> dict:
    from config import CONFIG

    previous = {key: copy.deepcopy(CONFIG.get(key)) for key in overlay}
    for key, value in overlay.items():
        current = CONFIG.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            CONFIG[key] = _deep_merge(current, value)
        else:
            CONFIG[key] = copy.deepcopy(value)
    return previous


def restore_config(previous: dict) -> None:
    from config import CONFIG

    for key, value in previous.items():
        if value is None and key in CONFIG:
            CONFIG.pop(key, None)
        else:
            CONFIG[key] = value


def _pair_metrics(result: dict) -> dict:
    trades = result.get("trades") if isinstance(result.get("trades"), list) else []
    validation = result.get("validation") or {}
    holdout = (validation.get("holdout") or {}).get("metrics") or {}
    headline = result.get("metricsV3") or {}
    first_entry = trades[0].get("entryTime") or trades[0].get("date") if trades else None
    last_entry = trades[-1].get("entryTime") or trades[-1].get("date") if trades else None
    return {
        "display": result.get("pair") or result.get("display"),
        "error": result.get("error"),
        "n": len(trades),
        "totalR": headline.get("totalR", result.get("totalR")),
        "winRate": headline.get("winRate", result.get("winRate")),
        "sqn": headline.get("sqn", result.get("sqn")),
        "profitFactor": headline.get("profitFactor", result.get("profitFactor")),
        "maxDrawdownR": headline.get("maxDrawdownR", result.get("maxDrawdownR")),
        "deflatedSqn": headline.get("deflatedSqn"),
        "holdoutN": holdout.get("totalTrades") or 0,
        "holdoutSqn": holdout.get("sqn"),
        "holdoutTotalR": holdout.get("totalR"),
        "validationVerdict": validation.get("verdict"),
        "entryTimeframe": result.get("entryTimeframe"),
        "regimeTf": result.get("regimeTf"),
        "biasTf": result.get("biasTf"),
        "structureTf": result.get("structureTf"),
        "setupTf": result.get("setupTf"),
        "triggerTf": result.get("triggerTf"),
        "firstEntry": first_entry,
        "lastEntry": last_entry,
        "venue": result.get("source") or result.get("provider"),
    }


def _sleeve_verdict(rows: list[dict], *, min_n: int, min_sqn: float) -> str:
    holdout_n = sum(int(row.get("holdoutN") or 0) for row in rows)
    sqns = [float(row["holdoutSqn"]) for row in rows if row.get("holdoutSqn") is not None]
    if holdout_n < min_n:
        return "INSUFFICIENT_SAMPLE"
    if not sqns:
        return "INSUFFICIENT_SAMPLE"
    # Pooled-n gate; SQN is the worst holdout print so one pair cannot hide
    # a sleeve that only works on a single name.
    worst = min(sqns)
    if worst > min_sqn:
        return "PASS"
    if worst > 0:
        return "RESEARCH_ONLY"
    return "FAIL"


def run_sleeve(name: str, *, variant: str | None = None, manifest_path: Path = DEFAULT_MANIFEST) -> dict:
    manifest = load_manifest(manifest_path)
    spec = (manifest.get("sleeves") or {}).get(name)
    if not isinstance(spec, dict):
        raise SystemExit(f"unknown sleeve {name!r}; known={sorted(manifest.get('sleeves') or {})}")

    overlay = dict(spec.get("overlay") or {})
    if variant:
        extra = VARIANT_OVERLAYS.get(variant)
        if extra is None:
            raise SystemExit(f"unknown overlay variant {variant!r}; known={sorted(VARIANT_OVERLAYS)}")
        overlay = _deep_merge(overlay, extra)

    from athena_backtest.runner import run_pair

    previous = apply_config_overlay(overlay)
    rows: list[dict] = []
    try:
        for pair in spec.get("pairs") or []:
            result = run_pair(dict(pair), engine="A", horizon=str(spec.get("style") or "intraday"))
            row = _pair_metrics(result)
            row["display"] = pair.get("display")
            row["source"] = pair.get("source")
            row["error"] = result.get("error") or row.get("error")
            if "error" not in result and not result.get("trades") and result.get("totalTrades") == 0:
                row["n"] = 0
            rows.append(row)
    finally:
        restore_config(previous)

    min_n = int(manifest.get("passHoldoutN") or 30)
    min_sqn = float(manifest.get("passHoldoutSqn") or 2.0)
    report = {
        "sleeve": name,
        "variant": variant or "baseline",
        "style": spec.get("style"),
        "description": spec.get("description"),
        "venueNote": spec.get("venueNote"),
        "overlay": overlay,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "pairs": rows,
        "holdoutN": sum(int(row.get("holdoutN") or 0) for row in rows),
        "verdict": _sleeve_verdict(rows, min_n=min_n, min_sqn=min_sqn),
        "passRule": {"holdoutN": min_n, "holdoutSqn": min_sqn},
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Engine A research sleeve runner")
    parser.add_argument("--sleeve", required=True, help="F1, F1b, F2, F3, or F4")
    parser.add_argument(
        "--overlay-variant",
        default="",
        help="optional timing_gates | session_gates | mr_floor_025 | tuning_lab_tf | policy_default_tf",
    )
    parser.add_argument("--out", default="", help="optional output json path")
    args = parser.parse_args()

    variant = args.overlay_variant.strip() or None
    report = run_sleeve(args.sleeve, variant=variant)
    out = Path(args.out) if args.out else (
        OUTPUT_ROOT / args.sleeve / f"{report['variant']}_{report['generatedAt'].replace(':', '')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(
        {
            "sleeve": report["sleeve"],
            "variant": report["variant"],
            "verdict": report["verdict"],
            "holdoutN": report["holdoutN"],
            "out": str(out),
            "pairs": [
                {
                    "display": row.get("display"),
                    "n": row.get("n"),
                    "holdoutN": row.get("holdoutN"),
                    "holdoutSqn": row.get("holdoutSqn"),
                    "error": row.get("error"),
                }
                for row in report["pairs"]
            ],
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
