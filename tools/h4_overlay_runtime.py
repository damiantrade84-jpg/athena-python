#!/usr/bin/env python3
"""Research-only H4 indicator overlay helpers (not imported by config.py)."""

from __future__ import annotations

import copy
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OVERLAY_PATH = ROOT / "configs" / "proposed_h4_indicator_overlay.yaml"
OVERLAY_ENV = "ATHENA_BT_H4_OVERLAY"
_RUNTIME_BOOTSTRAPPED = False


def bootstrap_backtest_runtime() -> None:
    """Load athena.py monolith once so backtest_runner._rt() is available."""
    global _RUNTIME_BOOTSTRAPPED
    if _RUNTIME_BOOTSTRAPPED:
        return
    import importlib.util

    import athena_runtime

    try:
        athena_runtime.rt()
        _RUNTIME_BOOTSTRAPPED = True
        return
    except RuntimeError:
        pass

    athena_path = ROOT / "athena.py"
    spec = importlib.util.spec_from_file_location("athena_monolith", athena_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load athena.py from {athena_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # noqa: S102
    _RUNTIME_BOOTSTRAPPED = True


def overlay_path_from_env() -> Path | None:
    name = os.environ.get(OVERLAY_ENV, "").strip()
    if not name:
        return None
    path = ROOT / "configs" / f"{name}.yaml"
    return path if path.exists() else None


def load_h4_overlay(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_OVERLAY_PATH
    doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{target}: root must be a mapping")
    return doc


def _scale_int(value: Any, factor: float, minimum: int = 2) -> int:
    try:
        base = int(value)
    except (TypeError, ValueError):
        base = 14
    return max(minimum, int(round(base * factor)))


def build_h4_tuned_periods_from_live_config(live_cfg: dict[str, Any]) -> dict[str, Any]:
    """Derive H4-tuned period tables from live config using plan scaling rules."""
    ema_by_class = copy.deepcopy(live_cfg.get("ENGINE_A_EMA_PERIODS_BY_CLASS") or {})
    rsi_by_class = copy.deepcopy(live_cfg.get("ENGINE_A_RSI_PERIOD_BY_CLASS") or {})
    atr_adx_by_class = copy.deepcopy(live_cfg.get("ENGINE_A_ATR_ADX_PERIODS_BY_CLASS") or {})
    macd_by_class = copy.deepcopy(live_cfg.get("ENGINE_A_MACD_PARAMS_BY_CLASS") or {})

    for _group, row in ema_by_class.items():
        if not isinstance(row, dict):
            continue
        if "trend" in row:
            row["trend"] = _scale_int(row["trend"], 0.5)
        if "momentum" in row:
            row["momentum"] = _scale_int(row["momentum"], 0.25)
        # long anchor stays 200 per config.yaml:787

    for group, period in list(rsi_by_class.items()):
        rsi_by_class[group] = _scale_int(period, 0.5)

    sweep = int(live_cfg.get("ENGINE_B_SWEEP_LOOKBACK_BARS", 5) or 5)
    sweep_h4 = max(1, int(round(sweep * 0.4)))

    return {
        "ENGINE_A_EMA_PERIODS_BY_CLASS": ema_by_class,
        "ENGINE_A_RSI_PERIOD_BY_CLASS": rsi_by_class,
        "ENGINE_A_ATR_ADX_PERIODS_BY_CLASS": atr_adx_by_class,
        "ENGINE_A_MACD_PARAMS_BY_CLASS": macd_by_class,
        "ENGINE_B_SWEEP_LOOKBACK_BARS": sweep_h4,
    }


def _resolve_class_keyed(keyed: dict, score_group: str | None, asset_type: str, default: Any) -> Any:
    from factor_scoring import _resolve_class_keyed as _fs_resolve

    return _fs_resolve(keyed, score_group, asset_type, default)


def _make_resolver(
    table_key: str,
    overlay_tables: dict[str, Any],
    baseline_fn: Callable[..., Any],
    default: Any,
) -> Callable[..., Any]:
    keyed = overlay_tables.get(table_key) or {}

    def _resolver(score_group: str | None, asset_type: str) -> Any:
        if table_key.endswith("_BY_CLASS") and isinstance(default, dict):
            out = dict(default)
            overrides = _resolve_class_keyed(keyed, score_group, asset_type, {})
            if isinstance(overrides, dict):
                for k, v in overrides.items():
                    try:
                        out[k] = int(v) if k != "signal" else int(v)
                    except (TypeError, ValueError):
                        pass
            return out
        override = _resolve_class_keyed(keyed, score_group, asset_type, default)
        try:
            return int(override)
        except (TypeError, ValueError):
            return default

    return _resolver


@contextmanager
def apply_h4_indicator_overlay(overlay_doc: dict[str, Any] | None = None):
    """Monkey-patch factor_scoring resolvers + sweep lookback for research runs only."""
    import config
    import factor_scoring

    doc = overlay_doc or load_h4_overlay()
    tables = {
        "ENGINE_A_EMA_PERIODS_BY_CLASS": doc.get("ENGINE_A_EMA_PERIODS_BY_CLASS") or {},
        "ENGINE_A_RSI_PERIOD_BY_CLASS": doc.get("ENGINE_A_RSI_PERIOD_BY_CLASS") or {},
        "ENGINE_A_ATR_ADX_PERIODS_BY_CLASS": doc.get("ENGINE_A_ATR_ADX_PERIODS_BY_CLASS") or {},
        "ENGINE_A_MACD_PARAMS_BY_CLASS": doc.get("ENGINE_A_MACD_PARAMS_BY_CLASS") or {},
    }
    sweep = doc.get("ENGINE_B_SWEEP_LOOKBACK_BARS")

    patches = [
        patch.object(
            factor_scoring,
            "_resolve_ema_periods",
            _make_resolver(
                "ENGINE_A_EMA_PERIODS_BY_CLASS",
                tables,
                factor_scoring._resolve_ema_periods,
                {"trend": 21, "long": 200, "momentum": 50},
            ),
        ),
        patch.object(
            factor_scoring,
            "_resolve_rsi_period",
            _make_resolver(
                "ENGINE_A_RSI_PERIOD_BY_CLASS",
                tables,
                factor_scoring._resolve_rsi_period,
                14,
            ),
        ),
        patch.object(
            factor_scoring,
            "_resolve_atr_adx_periods",
            _make_resolver(
                "ENGINE_A_ATR_ADX_PERIODS_BY_CLASS",
                tables,
                factor_scoring._resolve_atr_adx_periods,
                {"atr": 14, "adx": 14},
            ),
        ),
        patch.object(
            factor_scoring,
            "_resolve_macd_params",
            _make_resolver(
                "ENGINE_A_MACD_PARAMS_BY_CLASS",
                tables,
                factor_scoring._resolve_macd_params,
                {"fast": 12, "slow": 26, "signal": 9},
            ),
        ),
    ]
    cfg_patch = None
    if sweep is not None:
        cfg_patch = patch.dict(config.CONFIG, {"ENGINE_B_SWEEP_LOOKBACK_BARS": int(sweep)})

    try:
        for p in patches:
            p.start()
        if cfg_patch is not None:
            cfg_patch.start()
        yield doc
    finally:
        if cfg_patch is not None:
            cfg_patch.stop()
        for p in reversed(patches):
            p.stop()


@contextmanager
def entry_tf_override(entry_tf: str):
    """Override style_profile entry_tf on the initialized backtest runtime."""
    import backtest_runner

    bootstrap_backtest_runtime()
    rt = backtest_runner._rt()
    original_style = rt.naked_scan_style_profile

    def _style(style, score_group=None, asset_type=None):
        resolved, profile = original_style(style, score_group=score_group, asset_type=asset_type)
        profile = dict(profile)
        profile["entry_tf"] = entry_tf
        return resolved, profile

    rt.naked_scan_style_profile = _style
    try:
        yield
    finally:
        rt.naked_scan_style_profile = original_style


@contextmanager
def research_backtest_patches(*, entry_tf: str = "H4", score_group_aware: bool = True):
    """Research-only BT patches: entry_tf override + score_group on indicators."""
    import backtest_runner

    bootstrap_backtest_runtime()
    original_calc = backtest_runner.calc_indicators_with_normalized

    def _calc(candles, asset_type, score_group=None):
        sg = score_group
        if sg is None and score_group_aware:
            sg = getattr(_calc, "_active_score_group", None)
        return original_calc(candles, asset_type, score_group=sg)

    with entry_tf_override(entry_tf), patch.object(
        backtest_runner, "calc_indicators_with_normalized", _calc
    ):
        yield _calc


def summarize_backtest(result: dict, trades: list) -> dict[str, Any]:
    total = int(result.get("totalTrades") or 0)
    sl = sum(1 for t in trades if t.get("outcome") == "SL")
    tp = sum(1 for t in trades if t.get("outcome") in ("TP1", "TP2"))
    timeout = sum(1 for t in trades if t.get("outcome") == "TIMEOUT")
    be = sum(1 for t in trades if t.get("outcome") == "BE")
    wr = float(result.get("winRate") or 0.0) / 100.0 if total else 0.0
    sl_pct = (100.0 * sl / total) if total else 0.0
    return {
        "trades": total,
        "win_rate": round(wr, 4),
        "avg_r": float(result.get("expectancy") or 0.0),
        "sqn": float(result.get("sqn") or 0.0),
        "sl_count": sl,
        "sl_pct": round(sl_pct, 2),
        "tp_count": tp,
        "timeout_count": timeout,
        "be_count": be,
        "error": result.get("error"),
    }
