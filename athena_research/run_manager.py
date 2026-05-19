"""
Athena Research Lab — Run Manager
Orchestrates full research runs: data loading, strategy generation,
metric computation, reporting, and optional AI analysis.

Safety: no live imports, no production config writes, no execution calls.
"""

from __future__ import annotations

import logging
import math
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml

from athena_research.data_loader import (
    asset_class_for, load_ohlcv_multi, vbt_freq,
)
from athena_research.metrics import StrategyMetrics, compute_param_sensitivity, evaluate_strategy
from athena_research.reporting import generate_all_reports, make_run_dir
from athena_research.research_context import annotate_research_results
from athena_research.strategies import StrategySpec, iter_strategy_specs, run_strategy

log = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path("configs/vectorbt_research_lab.yaml")
_DEFAULT_OUTPUT = Path("logs/research_lab")


# ─── Config loader ────────────────────────────────────────────────────────────

def load_config(config_path: str | Path = _DEFAULT_CONFIG) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw.get("research_lab", raw)


# ─── Symbol → asset class helpers ────────────────────────────────────────────

def _fee_for(symbol: str, fees_cfg: dict) -> float:
    ac = asset_class_for(symbol)
    # Config values are documented as round-trip fractions. VectorBT and the
    # pandas fallback both charge this input per side, so pass half here.
    round_trip_fee = float(fees_cfg.get(ac, fees_cfg.get("default", 0.001)))
    return max(0.0, round_trip_fee / 2.0)


def get_group_symbols(cfg: dict, group: str) -> list[str]:
    """Return symbols for a market group from YAML config (single source of truth)."""
    symbols_cfg = cfg.get("symbols", {})
    # Handle alias names the UI uses
    _aliases = {"metals": "commodity", "indices": "index", "stocks": "stock"}
    group_key = _aliases.get(group, group)
    syms = symbols_cfg.get(group_key, [])
    return list(syms) if isinstance(syms, list) else []


def get_all_group_names(cfg: dict) -> list[str]:
    """Return all available market group names from YAML config."""
    return list(cfg.get("symbols", {}).keys())


def get_zone_timeframes(cfg: dict, zone: str) -> list[str]:
    """Return timeframes for a trading zone from YAML config."""
    zones = cfg.get("zones", {})
    return list(zones.get(zone, {}).get("timeframes", []))


def get_all_zones(cfg: dict) -> list[str]:
    """Return all zone names defined in YAML config."""
    return list(cfg.get("zones", {}).keys())


def _symbols_for_mode(cfg: dict, mode: str = "tiny") -> list[str]:
    if mode == "tiny":
        return cfg.get("tiny_run", {}).get("symbols", [])
    if mode in ("medium", "standard"):
        symbols_cfg = cfg.get("symbols", {})
        syms: list[str] = []
        for group_syms in symbols_cfg.values():
            if isinstance(group_syms, list):
                syms.extend(group_syms[:10])  # first 10 per group for medium
        return syms
    # full, large, or anything else
    symbols_cfg = cfg.get("symbols", {})
    all_syms: list[str] = []
    for group_syms in symbols_cfg.values():
        if isinstance(group_syms, list):
            all_syms.extend(group_syms)
    return all_syms


def _timeframes_for_mode(cfg: dict, mode: str = "tiny") -> list[str]:
    if mode == "tiny":
        return cfg.get("tiny_run", {}).get("timeframes", ["H1", "H4"])
    if mode in ("medium", "standard"):
        return ["H1", "H4"]
    return cfg.get("timeframes", ["H1", "H4", "D1"])


def _families_for_mode(cfg: dict, mode: str = "tiny") -> list[str]:
    if mode == "tiny":
        return cfg.get("tiny_run", {}).get("families", ["trend_momentum"])
    all_fams = list(cfg.get("strategies", {}).keys())
    return all_fams


def _zones_for_timeframes(cfg: dict, timeframes: list[str]) -> list[str]:
    """Infer run metadata zones from the actual timeframes used."""
    tf_set = {str(tf).upper() for tf in timeframes or []}
    zones = []
    for zname, zdef in (cfg.get("zones", {}) or {}).items():
        z_tfs = {str(tf).upper() for tf in zdef.get("timeframes", [])}
        if tf_set & z_tfs:
            zones.append(zname)
    return zones


# ─── Single-symbol single-TF worker ──────────────────────────────────────────

def _run_symbol_tf(
    symbol: str,
    timeframe: str,
    ohlcv: pd.DataFrame,
    specs: list[StrategySpec],
    run_id: str,
    fees_cfg: dict,
    slippage: float,
    is_pct: float,
    min_trades: int,
    data_source: str = "",
    backtest_exit_config: Optional[dict] = None,
) -> list[StrategyMetrics]:
    """Run all strategy specs on one symbol/TF combination."""
    results = []
    asset_class = asset_class_for(symbol)
    fees = _fee_for(symbol, fees_cfg)
    freq = vbt_freq(timeframe)

    for spec in specs:
        try:
            signals = run_strategy(ohlcv, spec)
            if not signals:
                continue
            metrics = evaluate_strategy(
                df=ohlcv,
                signals=signals,
                spec=spec,
                run_id=run_id,
                symbol=symbol,
                asset_class=asset_class,
                timeframe=timeframe,
                fees=fees,
                slippage=slippage,
                is_pct=is_pct,
                min_trades=min_trades,
                freq=freq,
                data_source=data_source,
                backtest_exit_config=backtest_exit_config,
            )
            results.append(metrics)
        except Exception as e:
            log.debug("[run_manager] %s %s %s(%s): %s", symbol, timeframe, spec.name, spec.params, e)

    return results


# ─── Main run function ────────────────────────────────────────────────────────

def run_research(
    mode: str = "tiny",
    config_path: str | Path = _DEFAULT_CONFIG,
    output_dir: str | Path = _DEFAULT_OUTPUT,
    run_id: Optional[str] = None,
    force_refresh: bool = False,
    max_workers: int = 4,
    direction: str = "both",
    run_ai_review: bool = False,
    symbols: Optional[list[str]] = None,
    timeframes: Optional[list[str]] = None,
    families: Optional[list[str]] = None,
    strategies: Optional[list[str]] = None,
    params: Optional[dict] = None,
    directions: Optional[list[str]] = None,
    zones: Optional[list[str]] = None,
    test_directions: bool = False,
    max_combinations: Optional[int] = None,
    trading_style: Optional[str] = None,
    market_group: Optional[str] = None,
) -> dict:
    """
    Execute a full or focused research lab run.
    """
    cfg = load_config(config_path)
    run_id = run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    output_dir = Path(output_dir)

    # Allow custom overrides
    if symbols is not None:
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    else:
        symbols = _symbols_for_mode(cfg, mode)

    # Zone-based timeframe resolution
    active_zones: list[str] = []
    if zones is not None:
        active_zones = zones if isinstance(zones, list) else [zones]
        zone_tfs = set()
        for z in active_zones:
            zone_tfs.update(get_zone_timeframes(cfg, z))
        if zone_tfs:
            timeframes = sorted(zone_tfs, key=lambda t: ["M5","M15","H1","H4","D1"].index(t) if t in ["M5","M15","H1","H4","D1"] else 99)
    elif timeframes is not None:
        if isinstance(timeframes, str):
            timeframes = [t.strip() for t in timeframes.split(",") if t.strip()]
    else:
        timeframes = _timeframes_for_mode(cfg, mode)

    if families is not None:
        if isinstance(families, str):
            families = [f.strip() for f in families.split(",") if f.strip()]
    else:
        families = _families_for_mode(cfg, mode)

    fees_cfg = cfg.get("fees", {})
    slippage = float(cfg.get("slippage", {}).get("default", 0.0002))
    is_pct = float(cfg.get("is_pct", 0.70))
    min_trades = int(cfg.get("min_trades", 20))
    strategy_params = cfg.get("strategies", {})
    backtest_exit_config = cfg.get("backtest_exit", {})
    if trading_style is None and active_zones:
        if len(set(active_zones)) == 1 and active_zones[0] in {"scalp", "intra", "swing"}:
            trading_style = active_zones[0]

    log.info("[run_manager] Run %s | mode=%s | symbols=%d | TFs=%s | families=%s",
             run_id, mode, len(symbols), timeframes, families)

    # Build direction variants for testing
    direction_list = [direction]
    if test_directions and direction == "both":
        direction_list = ["both", "long", "short"]

    selected_strategies = strategies
    if selected_strategies is not None and isinstance(selected_strategies, str):
        selected_strategies = [s.strip() for s in selected_strategies.split(",") if s.strip()]
    selected_directions = directions
    if selected_directions is not None and isinstance(selected_directions, str):
        selected_directions = [d.upper().strip() for d in selected_directions.split(",") if d.strip()]

    def _build_specs_for_context(tf: str | None = None, group: str | None = None) -> list[StrategySpec]:
        built: list[StrategySpec] = []
        for d in direction_list:
            try:
                generated = iter_strategy_specs(
                    families,
                    strategy_params,
                    direction=d,
                    trading_style=trading_style,
                    market_group=group or market_group,
                    timeframe=tf,
                )
            except TypeError:
                # Older tests and integrations monkeypatch the historical
                # three-argument contract. Keep that compatibility while the
                # real implementation supports contextual overlays.
                generated = iter_strategy_specs(families, strategy_params, direction=d)
            built.extend(generated)
        if selected_strategies is not None:
            built = [s for s in built if s.name in selected_strategies]
        if params is not None:
            filtered_specs = []
            for s in built:
                matches = True
                for pk, pv in params.items():
                    if str(s.params.get(pk)) != str(pv):
                        matches = False
                        break
                if matches:
                    filtered_specs.append(s)
            built = filtered_specs
        if selected_directions is not None:
            built = [s for s in built if s.direction.upper() in [d.upper() for d in selected_directions]]
        return built

    # Generic planned count; concrete pair specs are rebuilt after data load so
    # timeframe/market profile overlays can differ per symbol/timeframe.
    specs = _build_specs_for_context()



    requested_combination_count = len(symbols) * len(timeframes) * len(specs)
    combination_cap = None
    if max_combinations is not None:
        try:
            combination_cap = max(0, int(max_combinations))
        except (TypeError, ValueError):
            combination_cap = None
    planned_combination_count = (
        min(requested_combination_count, combination_cap)
        if combination_cap is not None
        else requested_combination_count
    )
    combination_truncated = (
        combination_cap is not None
        and requested_combination_count > combination_cap
    )


    # Download data for all symbol × TF combinations
    # load_ohlcv_multi returns dict[str, tuple[Optional[DataFrame], DataProvenance]]
    all_data: dict[tuple[str, str], tuple] = {}
    data_cfg = cfg.get("data", {})
    cache_dir = Path(data_cfg.get("cache_dir", "logs/research_lab/data_cache"))
    allow_yfinance = bool(data_cfg.get("ALLOW_YFINANCE_FALLBACK", False))
    max_bars_cfg = data_cfg.get("max_bars", {}) or {}
    default_max_bars = int(data_cfg.get("default_max_bars", 1000))
    max_bars_by_timeframe: dict[str, int] = {}

    for tf in timeframes:
        try:
            tf_limit = int(max_bars_cfg.get(tf, default_max_bars))
        except (TypeError, ValueError):
            tf_limit = default_max_bars
        tf_limit = max(1, tf_limit)
        max_bars_by_timeframe[tf] = tf_limit
        try:
            pair_dict = load_ohlcv_multi(
                symbols, tf,
                cache_dir=cache_dir,
                limit=tf_limit,
                force_refresh=force_refresh,
                allow_yfinance=allow_yfinance,
            )
        except Exception as e:
            log.error("[run_manager] load_ohlcv_multi failed for TF=%s: %s", tf, e, exc_info=True)
            continue
        for sym, (df, prov) in pair_dict.items():
            all_data[(sym, tf)] = (df, prov)

    log.info("[run_manager] Loaded data for %d symbol/TF pairs", len(all_data))

    specs_by_pair: dict[tuple[str, str], list[StrategySpec]] = {}
    remaining = combination_cap
    for key in all_data.keys():
        symbol, tf = key
        pair_market_group = market_group or asset_class_for(symbol)
        pair_specs_all = _build_specs_for_context(tf=tf, group=pair_market_group)
        if remaining is None:
            specs_by_pair[key] = pair_specs_all
            continue
        take = min(len(pair_specs_all), remaining)
        specs_by_pair[key] = pair_specs_all[:take]
        remaining -= take

    # Run strategies in parallel
    all_results: list[StrategyMetrics] = []
    tasks = []
    executed_combination_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for (symbol, tf), (ohlcv, prov) in all_data.items():
            pair_specs = specs_by_pair.get((symbol, tf), specs)
            if not pair_specs:
                continue
            if ohlcv is None or ohlcv.empty:
                log.warning("[run_manager] %s %s: DATA_UNAVAILABLE (%s) — skipping",
                            symbol, tf, prov.notes if prov else "no data")
                continue
            if prov.data_source == "DATA_UNAVAILABLE":
                log.warning("[run_manager] %s %s: DATA_UNAVAILABLE — skipping", symbol, tf)
                continue
            if len(ohlcv) < 50:
                log.warning("[run_manager] %s %s: only %d bars — skipping", symbol, tf, len(ohlcv))
                continue
            future = pool.submit(
                _run_symbol_tf,
                symbol, tf, ohlcv, pair_specs,
                run_id, fees_cfg, slippage, is_pct, min_trades,
                prov.data_source, backtest_exit_config,
            )
            tasks.append(future)
            executed_combination_count += len(pair_specs)

        for future in as_completed(tasks):
            try:
                all_results.extend(future.result())
            except Exception as e:
                log.error("[run_manager] Worker failed: %s", e)

    log.info("[run_manager] Completed %d strategy evaluations", len(all_results))

    # Apply symbol breadth component to robustness (0.20 weight) and parameter
    # sensitivity across neighbouring configs in the same symbol/TF/family bucket.
    from collections import defaultdict
    strategy_symbol_returns: dict[str, list[float]] = defaultdict(list)
    sensitivity_buckets: dict[str, list[float]] = defaultdict(list)
    for m in all_results:
        if math.isfinite(m.net_return):
            key = f"{m.family}::{m.strategy_name}::{m.params_str}"
            strategy_symbol_returns[key].append(m.net_return)
            sens_key = f"{m.symbol}::{m.timeframe}::{m.family}::{m.strategy_name}::{m.direction}"
            sensitivity_buckets[sens_key].append(m.net_return)
    for m in all_results:
        key = f"{m.family}::{m.strategy_name}::{m.params_str}"
        returns = strategy_symbol_returns.get(key, [])
        if returns:
            breadth = sum(1 for r in returns if r > 0) / len(returns)
            m.robustness_score = min(1.0, m.robustness_score + breadth * 0.20)
        sens_key = f"{m.symbol}::{m.timeframe}::{m.family}::{m.strategy_name}::{m.direction}"
        sensitivity = compute_param_sensitivity(sensitivity_buckets.get(sens_key, []))
        if math.isfinite(sensitivity):
            m.param_sensitivity = sensitivity
            # Fold stability into robustness with a small research-only weight.
            m.robustness_score = min(1.0, m.robustness_score + sensitivity * 0.10)

    # Tag results with zone info based on timeframe
    zone_cfg = cfg.get("zones", {})
    tf_to_zone: dict[str, str] = {}
    for zname, zdef in zone_cfg.items():
        for ztf in zdef.get("timeframes", []):
            tf_to_zone.setdefault(ztf, zname)
    for m in all_results:
        m.zone = tf_to_zone.get(m.timeframe, "")

    # Research-only Engine A/B attribution and group-aware recommendations.
    all_results = annotate_research_results(all_results, cfg)

    # Checkpoint raw results to disk before full report pipeline (large runs can OOM here).
    run_dir = make_run_dir(output_dir, run_id)
    try:
        from athena_research.reporting import metrics_to_df, write_research_summary_csv
        write_research_summary_csv(metrics_to_df(all_results), run_dir)
    except Exception as e:
        log.error("[run_manager] Early research_summary.csv write failed: %s", e, exc_info=True)

    # Generate reports
    run_meta = {
        "mode": mode,
        "symbol_count": len(symbols),
        "timeframes": timeframes,
        "families": families,
        "specs_count": len(specs),
        "results_count": len(all_results),
        "direction": direction,
        "zones": active_zones or _zones_for_timeframes(cfg, timeframes),
        "test_directions": test_directions,
        "fee_model": "config_round_trip_converted_to_per_side",
        "max_combinations": combination_cap,
        "combination_count_requested": requested_combination_count,
        "combination_count_planned": planned_combination_count,
        "combination_count_executed": executed_combination_count,
        "combination_truncated": combination_truncated,
        "max_bars_by_timeframe": max_bars_by_timeframe,
        "market_group": market_group,
        "trading_style": trading_style,
        "backtest_exit_mode": (backtest_exit_config or {}).get("BACKTEST_EXIT_MODE", "triple_barrier"),
        "backtest_exit_config": backtest_exit_config,
    }
    try:
        run_dir = generate_all_reports(all_results, output_dir, run_id, run_meta)
    except Exception as e:
        log.error("[run_manager] generate_all_reports failed: %s", e, exc_info=True)
        run_dir = Path(output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        summary_path = run_dir / "research_summary.csv"
        if not summary_path.exists() and all_results:
            try:
                from athena_research.reporting import metrics_to_df, write_research_summary_csv
                write_research_summary_csv(metrics_to_df(all_results), run_dir)
                log.info("[run_manager] Recovered research_summary.csv after report failure")
            except Exception as recover_exc:
                log.error("[run_manager] Could not recover summary CSV: %s", recover_exc)

    result = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "mode": mode,
        "results_count": len(all_results),
        "symbols": symbols,
        "timeframes": timeframes,
        "families": families,
        "max_combinations": combination_cap,
        "combination_count_requested": requested_combination_count,
        "combination_count_executed": executed_combination_count,
        "combination_truncated": combination_truncated,
        "ai_review": None,
    }

    # Optional AI review
    if run_ai_review:
        try:
            from athena_research.ai_analyst import run_ai_analysis
            ai_cfg = cfg.get("ai", {})
            ai_result = run_ai_analysis(
                run_dir=run_dir,
                provider=ai_cfg.get("provider", "auto"),
                model=ai_cfg.get("model"),
                max_tokens=int(ai_cfg.get("max_tokens", 4000)),
                temperature=float(ai_cfg.get("temperature", 0.2)),
            )
            result["ai_review"] = ai_result
        except Exception as e:
            log.error("[run_manager] AI review failed: %s", e)
            result["ai_review"] = {"error": str(e)}

    log.info("[run_manager] Run complete: %s", run_dir)
    return result


# ─── List runs ────────────────────────────────────────────────────────────────

def list_runs(output_dir: str | Path = _DEFAULT_OUTPUT) -> list[dict[str, Any]]:
    """Return metadata for all completed research runs."""
    output_dir = Path(output_dir)
    runs = []
    if not output_dir.exists():
        return runs

    for d in sorted(output_dir.iterdir(), reverse=True):
        if not d.is_dir() or d.name in ("data_cache", "latest"):
            continue
        meta_path = d / "run_meta.json"
        summary_path = d / "research_summary.csv"
        run_info: dict[str, Any] = {"run_id": d.name, "run_dir": str(d)}
        if meta_path.exists():
            import json
            try:
                run_info.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                pass
        if summary_path.exists():
            try:
                df = pd.read_csv(summary_path)
                run_info["total_configs"] = len(df)
                if "status" in df.columns:
                    run_info["strong"] = int((df["status"] == "STRONG_CANDIDATE").sum())
                    run_info["weak"] = int((df["status"] == "WEAK_CANDIDATE").sum())
            except Exception:
                pass
        runs.append(run_info)

    return runs


def get_run_results(run_id: str, output_dir: str | Path = _DEFAULT_OUTPUT) -> Optional[pd.DataFrame]:
    """Load research_summary.csv for a given run_id (fallback: ranked_strategies.csv)."""
    run_dir = Path(output_dir) / run_id
    for name in ("research_summary.csv", "ranked_strategies.csv"):
        p = run_dir / name
        if not p.exists():
            continue
        try:
            return pd.read_csv(p)
        except Exception:
            log.warning("[run_manager] Failed to read %s for %s", name, run_id, exc_info=True)
    return None
