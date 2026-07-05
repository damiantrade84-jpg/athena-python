"""EdgeLab configuration loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "enabled_engines": ["engine_b", "cascade", "engine_a"],
    "optional_engines": ["scalp", "ase"],
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "timeframes": ["H4", "H1"],
    "freshness_max_age_hours": 48,
    "freshness_min_bars": 200,
    "loop_interval_seconds": 900,
    "max_candidates_per_cycle": 3,
    "candidate_generation_mode": "none",
    "candidate_command": "",
    "min_score_delta": 0.05,
    "min_trade_count": 30,
    "max_drawdown_R": 3.0,
    "max_symbol_concentration": 0.60,
    "require_oos_not_worse": True,
    "require_tests_pass": True,
    "dry_run_default": False,
    "test_command": "pytest tests/research/edgelab -q",
    "baseline_command": "python research/edgelab/stub_evaluator.py --scenario baseline",
    "evaluation_command": "python research/edgelab/stub_evaluator.py --scenario evaluate",
    "metrics_output_path": "",
    "allowed_paths": [
        "research/",
        "config/research/",
        "configs/research/",
        "tests/research/",
        "athena_research/",
        "engine_a/research/",
        "engine_b/research/",
        "research/edgelab/",
        "research/autoresearch/",
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
    "scoring_weights": {
        "expR": 40,
        "profit_factor_cap": 3.0,
        "profit_factor_mult": 15,
        "sqn_cap": 4.0,
        "sqn_mult": 10,
        "max_drawdown_mult": 8,
    },
    "detected_research_commands": {
        "engine_a_ablation": (
            "python -m athena_research.engine_a_ablation.run "
            "--max-symbols 4 --step 4 "
            "--out research/edgelab/logs/engine_a_eval.json"
        ),
        "engine_b_quality": "python -m athena_research.engine_b_quality_report",
        "vectorbt_lab": "python tools/vectorbt_research_lab.py --mode tiny",
        "ase_backtest": "pytest tests/test_ase_backtest.py -q",
    },
    "engine_commands": {
        "engine_b": "python research/edgelab/stub_evaluator.py --scenario engine_b_stub",
        "cascade": "python research/edgelab/stub_evaluator.py --scenario cascade_stub",
        "engine_a": "python research/edgelab/stub_evaluator.py --scenario engine_a_stub",
        "scalp": "",
        "ase": "",
    },
    "autostart_daemon": False,
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(__file__).resolve().parent / "edgelab_config.yaml"
    merged = dict(DEFAULT_CONFIG)
    if not config_path.exists():
        return merged

    text = config_path.read_text(encoding="utf-8")
    suffix = config_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML required for YAML config") from exc
        data = yaml.safe_load(text) or {}
    elif suffix == ".json":
        data = json.loads(text)
    else:
        import yaml

        data = yaml.safe_load(text) or {}

    if isinstance(data, dict):
        merged.update(data)
    return merged
