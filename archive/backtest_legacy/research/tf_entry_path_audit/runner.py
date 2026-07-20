"""Main orchestrator for TF/entry path audit."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from athena_research.tf_entry_path_audit.anti_overfit import evaluate_cohort
from athena_research.tf_entry_path_audit.diagnostic_engine_a import run_engine_a_diagnostic
from athena_research.tf_entry_path_audit.diagnostic_engine_b import run_engine_b_diagnostic
from athena_research.tf_entry_path_audit.matrix import (
    ENGINE_A_MATRIX,
    ENGINE_B_MATRIX,
    INDICATOR_PROFILES,
    TfMatrixCell,
)
from athena_research.tf_entry_path_audit.parameter_intent import IndicatorProfile, intent_map_as_rows
from athena_research.tf_entry_path_audit.patches import ensure_diagnostic_mode, indicator_profile_patch
from athena_research.tf_entry_path_audit.reports import (
    aggregate_realism_metrics,
    build_summary_markdown,
    group_metrics,
    write_csv,
)
from athena_research.tf_entry_path_audit.trade_path import enrich_trade_record
from athena_research.tf_entry_path_audit.vectorbt_layer import run_provisional_sweep, vectorbt_available


REPORT_DIR = Path("reports")


def _load_pairs(symbols: list[str] | None = None) -> list[dict]:
    from tools._athena_pair_loader import find_pair_by_display, representative_audit_pairs

    if symbols:
        pairs = []
        for sym in symbols:
            p = find_pair_by_display(sym)
            if p:
                pairs.append(p)
        return pairs
    return representative_audit_pairs()


def _enrich_trades_from_result(
    result: dict,
    cell: TfMatrixCell,
    pair: dict,
    profile: IndicatorProfile,
) -> list[dict]:
    engine = f"ENGINE_{cell.engine}"
    symbol = pair.get("display") or pair.get("symbol", "")
    trades = list(result.get("trades") or [])
    enriched: list[dict] = []
    for trade in trades:
        enriched.append(
            enrich_trade_record(
                trade,
                engine=engine,
                symbol=symbol,
                signal_tf=cell.signal_tf,
                entry_tf=cell.entry_tf,
                exit_tf=cell.exit_tf,
                execution_tf=cell.execution_tf,
                indicator_profile=profile.value,
                future_window=None,
                same_bar_policy=result.get("sameBarPolicy", "ADVERSE_SL_FIRST"),
            )
        )
    return enriched


def _run_cell(
    cell: TfMatrixCell,
    pair: dict,
    profile: IndicatorProfile,
    *,
    engine_a_candles: dict | None = None,
) -> tuple[dict, list[dict]]:
    with indicator_profile_patch(profile):
        if cell.engine == "A":
            result = run_engine_a_diagnostic(cell, pair, candles=engine_a_candles)
        else:
            result = run_engine_b_diagnostic(cell, pair)

    if result.get("blocked") == "BLOCKED_DATA":
        return result, []

    trades = _enrich_trades_from_result(result, cell, pair, profile)
    return result, trades


def run_tf_entry_path_audit(
    *,
    pairs: list[dict] | None = None,
    engines: tuple[str, ...] = ("A", "B"),
    profiles: tuple[IndicatorProfile, ...] = (IndicatorProfile.BAR_NATIVE,),
    output_dir: Path | None = None,
    use_vectorbt: bool = False,
    quick: bool = False,
    baseline_only: bool = False,
) -> dict[str, Any]:
    """Run full TF/entry path audit and write report files."""
    out = output_dir or REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)

    with ensure_diagnostic_mode():
        pair_list = pairs or _load_pairs()
        if quick:
            pair_list = pair_list[:3]

        all_trades: list[dict] = []
        by_engine_rows: list[dict] = []
        by_symbol_rows: list[dict] = []
        by_tf_rows: list[dict] = []
        validation_rows: list[dict] = []
        vectorbt_rows: list[dict] = []
        blocked: list[str] = []

        matrices: list[TfMatrixCell] = []
        if "A" in engines:
            a_cells = ENGINE_A_MATRIX
            if baseline_only:
                a_cells = [c for c in a_cells if c.signal_tf == c.entry_tf]
            matrices.extend(a_cells)
        if "B" in engines:
            b_cells = ENGINE_B_MATRIX
            if baseline_only:
                b_cells = [c for c in b_cells if c.signal_tf == c.entry_tf]
            matrices.extend(b_cells)

        engine_a_candle_cache: dict[str, dict] = {}

        for profile in profiles:
            for cell in matrices:
                for pair in pair_list:
                    label = f"{cell.label}|{pair['display']}|{profile.value}"
                    ea_candles = None
                    if cell.engine == "A" and cell.signal_tf != cell.entry_tf:
                        key = pair["display"]
                        if key not in engine_a_candle_cache:
                            from athena_research.tf_entry_path_audit.diagnostic_v3 import _fetch_engine_a_candles

                            engine_a_candle_cache[key] = _fetch_engine_a_candles(pair) or {}
                        ea_candles = engine_a_candle_cache[key] or None
                    try:
                        result, trades = _run_cell(cell, pair, profile, engine_a_candles=ea_candles)
                    except Exception as exc:
                        blocked.append(f"{label}: {exc}")
                        continue

                    if result.get("blocked"):
                        blocked.append(f"{label}: {result.get('error', result.get('blocked'))}")
                        validation_rows.append(
                            evaluate_cohort(
                                [],
                                label=label,
                                engine=cell.engine,
                                symbol=pair["display"],
                                signal_tf=cell.signal_tf,
                                entry_tf=cell.entry_tf,
                                indicator_profile=profile.value,
                            )
                        )
                        continue

                    all_trades.extend(trades)
                    metrics = aggregate_realism_metrics(trades)
                    metrics.update({
                        "engine": f"ENGINE_{cell.engine}",
                        "symbol": pair["display"],
                        "signal_tf": cell.signal_tf,
                        "entry_tf": cell.entry_tf,
                        "exit_tf": cell.exit_tf,
                        "execution_tf": cell.execution_tf,
                        "indicator_profile": profile.value,
                        "label": cell.label,
                        "harness": result.get("_diagnostic_mode", "athena_harness"),
                    })
                    by_engine_rows.append({**metrics, "group_key": f"ENGINE_{cell.engine}|{profile.value}"})
                    by_symbol_rows.append({**metrics, "group_key": pair["display"]})
                    by_tf_rows.append({
                        **metrics,
                        "group_key": f"{cell.signal_tf}->{cell.entry_tf}|{profile.value}",
                    })
                    validation_rows.append(
                        evaluate_cohort(
                            trades,
                            label=label,
                            engine=cell.engine,
                            symbol=pair["display"],
                            signal_tf=cell.signal_tf,
                            entry_tf=cell.entry_tf,
                            indicator_profile=profile.value,
                            validated_by_athena=True,
                        )
                    )

                    if use_vectorbt and vectorbt_available() and cell.entry_tf in ("H1", "H4"):
                        try:
                            import pandas as pd

                            # Provisional only — uses result trades as proxy, not full OHLC replay
                            if trades:
                                vectorbt_rows.extend(
                                    run_provisional_sweep(
                                        pd.DataFrame({"close": [t.get("entry_price", 0) for t in trades],
                                                      "high": [t.get("entry_price", 0) for t in trades],
                                                      "low": [t.get("entry_price", 0) for t in trades]}),
                                        symbol=pair["display"],
                                        signal_tf=cell.signal_tf,
                                        entry_tf=cell.entry_tf,
                                    )
                                )
                        except Exception:
                            pass

        # Dedupe grouped metrics
        by_engine_agg = group_metrics(all_trades, lambda t: f"{t.get('engine')}|{t.get('indicator_profile')}")
        by_symbol_agg = group_metrics(all_trades, lambda t: t.get("symbol", ""))
        by_tf_agg = group_metrics(
            all_trades,
            lambda t: f"{t.get('signal_timeframe')}->{t.get('entry_timeframe')}|{t.get('indicator_profile')}",
        )

        write_csv(out / "tf_entry_path_audit_trades.csv", all_trades)
        write_csv(out / "tf_entry_path_audit_by_engine.csv", by_engine_agg or by_engine_rows)
        write_csv(out / "tf_entry_path_audit_by_symbol.csv", by_symbol_agg or by_symbol_rows)
        write_csv(out / "tf_entry_path_audit_by_timeframe.csv", by_tf_agg or by_tf_rows)
        write_csv(out / "tf_entry_path_audit_athena_validation.csv", validation_rows)
        if vectorbt_rows:
            write_csv(out / "tf_entry_path_audit_vectorbt_provisional.csv", vectorbt_rows)

        summary_md = build_summary_markdown(
            engine_a_results=[r for r in by_engine_rows if r.get("engine") == "ENGINE_A"],
            engine_b_results=[r for r in by_engine_rows if r.get("engine") == "ENGINE_B"],
            validation_rows=validation_rows,
            vectorbt_rows=vectorbt_rows,
            parameter_intent_rows=intent_map_as_rows(),
        )
        if blocked:
            summary_md += "\n## Blocked Cells\n\n" + "\n".join(f"- {b}" for b in blocked) + "\n"
        (out / "tf_entry_path_audit_summary.md").write_text(summary_md, encoding="utf-8")

        return {
            "output_dir": str(out),
            "trade_count": len(all_trades),
            "validation_rows": len(validation_rows),
            "blocked": blocked,
            "vectorbt_used": bool(vectorbt_rows),
        }
