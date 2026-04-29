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
from athena_research.metrics import StrategyMetrics, evaluate_strategy
from athena_research.reporting import generate_all_reports
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
    return float(fees_cfg.get(ac, fees_cfg.get("default", 0.001)))


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

    log.info("[run_manager] Run %s | mode=%s | symbols=%d | TFs=%s | families=%s",
             run_id, mode, len(symbols), timeframes, families)

    # Build direction variants for testing
    direction_list = [direction]
    if test_directions and direction == "both":
        direction_list = ["both", "long", "short"]

    # Build strategy specs for all directions
    specs = []
    for d in direction_list:
        specs.extend(iter_strategy_specs(families, strategy_params, direction=d))
    
    # Apply strategy/param filters
    if strategies is not None:
        if isinstance(strategies, str):
            strategies = [s.strip() for s in strategies.split(",") if s.strip()]
        specs = [s for s in specs if s.name in strategies]

    if params is not None:
        # If params is passed, keep only specs that match the passed parameters
        # params can be a dict of key=values
        filtered_specs = []
        for s in specs:
            matches = True
            for pk, pv in params.items():
                if str(s.params.get(pk)) != str(pv):
                    matches = False
                    break
            if matches:
                filtered_specs.append(s)
        specs = filtered_specs

    if directions is not None:
        if isinstance(directions, str):
            directions = [d.upper().strip() for d in directions.split(",") if d.strip()]
        specs = [s for s in specs if s.direction.upper() in [d.upper() for d in directions]]

    log.info("[run_manager] %d strategy specs to test", len(specs))


    # Download data for all symbol × TF combinations
    # load_ohlcv_multi returns dict[str, tuple[Optional[DataFrame], DataProvenance]]
    all_data: dict[tuple[str, str], tuple] = {}
    data_cfg = cfg.get("data", {})
    cache_dir = Path(data_cfg.get("cache_dir", "logs/research_lab/data_cache"))
    allow_yfinance = bool(data_cfg.get("ALLOW_YFINANCE_FALLBACK", False))

    for tf in timeframes:
        try:
            pair_dict = load_ohlcv_multi(
                symbols, tf,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
                allow_yfinance=allow_yfinance,
            )
        except Exception as e:
            log.error("[run_manager] load_ohlcv_multi failed for TF=%s: %s", tf, e, exc_info=True)
            continue
        for sym, (df, prov) in pair_dict.items():
            all_data[(sym, tf)] = (df, prov)

    log.info("[run_manager] Loaded data for %d symbol/TF pairs", len(all_data))

    # Run strategies in parallel
    all_results: list[StrategyMetrics] = []
    tasks = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for (symbol, tf), (ohlcv, prov) in all_data.items():
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
                symbol, tf, ohlcv, specs,
                run_id, fees_cfg, slippage, is_pct, min_trades,
                prov.data_source,
            )
            tasks.append(future)

        for future in as_completed(tasks):
            try:
                all_results.extend(future.result())
            except Exception as e:
                log.error("[run_manager] Worker failed: %s", e)

    log.info("[run_manager] Completed %d strategy evaluations", len(all_results))

    # Apply symbol breadth component to robustness (0.20 weight)
    from collections import defaultdict
    strategy_symbol_returns: dict[str, list[float]] = defaultdict(list)
    for m in all_results:
        if math.isfinite(m.net_return):
            key = f"{m.family}::{m.strategy_name}::{m.params_str}"
            strategy_symbol_returns[key].append(m.net_return)
    for m in all_results:
        key = f"{m.family}::{m.strategy_name}::{m.params_str}"
        returns = strategy_symbol_returns.get(key, [])
        if returns:
            breadth = sum(1 for r in returns if r > 0) / len(returns)
            m.robustness_score = min(1.0, m.robustness_score + breadth * 0.20)

    # Tag results with zone info based on timeframe
    zone_cfg = cfg.get("zones", {})
    tf_to_zone: dict[str, str] = {}
    for zname, zdef in zone_cfg.items():
        for ztf in zdef.get("timeframes", []):
            tf_to_zone.setdefault(ztf, zname)
    for m in all_results:
        m.zone = tf_to_zone.get(m.timeframe, "")

    # Generate reports
    run_meta = {
        "mode": mode,
        "symbol_count": len(symbols),
        "timeframes": timeframes,
        "families": families,
        "specs_count": len(specs),
        "results_count": len(all_results),
        "direction": direction,
        "zones": active_zones or list(zone_cfg.keys()),
        "test_directions": test_directions,
    }
    try:
        run_dir = generate_all_reports(all_results, output_dir, run_id, run_meta)
    except Exception as e:
        log.error("[run_manager] generate_all_reports failed: %s", e, exc_info=True)
        run_dir = Path(output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "mode": mode,
        "results_count": len(all_results),
        "symbols": symbols,
        "timeframes": timeframes,
        "families": families,
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
    """Load research_summary.csv for a given run_id."""
    p = Path(output_dir) / run_id / "research_summary.csv"
    if not p.exists():
        return None
    try:
        return pd.read_csv(p)
    except Exception:
        return None
