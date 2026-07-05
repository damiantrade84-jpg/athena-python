"""Configuration loading for AutoResearch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "baseline_command": (
        "python research/autoresearch/stub_evaluator.py --scenario baseline"
    ),
    "evaluation_command": (
        "python research/autoresearch/stub_evaluator.py --scenario evaluate"
    ),
    "test_command": "pytest tests/research/autoresearch -q",
    "min_score_delta": 0.05,
    "min_trade_count": 30,
    "max_drawdown_R": 3.0,
    "max_symbol_concentration": 0.60,
    "require_oos_not_worse": True,
    "dry_run_default": False,
    "manual_poll_seconds": 5,
    "manual_timeout_seconds": 3600,
    "candidate_command": "",
    "metrics_output_path": "",
    "allowed_paths": [
        "research/",
        "config/research/",
        "configs/research/",
        "tests/research/",
        "athena_research/",
        "engine_a/research/",
        "engine_b/research/",
    ],
    "denied_keywords": [
        "live",
        "broker",
        "order",
        "execution",
        "execute",
        "autotrade",
        "scheduler",
        "credentials",
        "secret",
        "key",
        "mt5_login",
        "account",
        "trade_router",
        "position_manager",
        "risk_live",
        "ui",
        "frontend",
        "dashboard",
        "button",
        "send_order",
        "place_order",
        "close_order",
    ],
    "detected_alternatives": {
        "vectorbt_lab": "python tools/vectorbt_research_lab.py --mode tiny",
        "engine_a_ablation": (
            "python -m athena_research.engine_a_ablation.run "
            "--max-symbols 4 --step 4 "
            "--out research/autoresearch/logs/baseline_eval.json"
        ),
    },
}


def _merge_defaults(data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)

    suffix = config_path.suffix.lower()
    text = config_path.read_text(encoding="utf-8")

    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "PyYAML is required for YAML config. Install pyyaml or use JSON config."
            ) from exc
        data = yaml.safe_load(text) or {}
    elif suffix == ".json":
        data = json.loads(text)
    else:
        try:
            import yaml

            data = yaml.safe_load(text) or {}
        except Exception:
            data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {config_path}")
    return _merge_defaults(data)
