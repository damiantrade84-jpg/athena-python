"""Flask routes for the athena_fx factor lane (research + config-gated demo execution)."""
from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any, Optional

from flask import jsonify, request

from config import CONFIG
from athena_fx import store as fx_store
from athena_fx.models import (
    ACTION_BUY,
    ACTION_FLATTEN,
    ACTION_SELL,
    ACTION_REDUCE,
    DIAG_MISSING_CARRY,
    G8_CURRENCIES,
    G8_PAIRS,
    GATE_FAIL,
    PRIMARY_FAMILIES,
)

log = logging.getLogger("athena.api.forex_factor")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FX_SYMBOL = re.compile(r"^[A-Z]{6}$")


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _envelope(
    data: Any = None,
    status: str = "ok",
    diagnostics: list[Any] | None = None,
    warnings: list[Any] | None = None,
    success: bool = True,
    http: int = 200,
):
    return jsonify(
        {
            "success": success,
            "data": data,
            "status": status,
            "diagnostics": list(diagnostics or []),
            "warnings": list(warnings or []),
            "generated_at": _utc_now_iso(),
        }
    ), http


def _summarize_diagnostics_for_response(
    diagnostics: list[Any],
    *,
    max_rows: int = 50,
    max_samples: int = 5,
) -> list[Any]:
    """Compact repeated diagnostics for UI envelopes without mutating reports."""
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    passthrough: list[Any] = []
    for diag in diagnostics:
        if not isinstance(diag, dict):
            passthrough.append(diag)
            continue
        code = str(diag.get("code") or diag.get("field") or "diagnostic")
        message = str(diag.get("message") or "")
        severity = str(diag.get("severity") or "")
        key = (code, message, severity)
        row = grouped.get(key)
        if row is None:
            row = {
                "code": code,
                "message": message,
                "severity": severity,
                "count": 0,
                "sample_symbols": [],
                "sample_as_of_dates": [],
            }
            grouped[key] = row
        row["count"] += 1

        context = diag.get("context") if isinstance(diag.get("context"), dict) else {}
        symbol = diag.get("symbol") or context.get("symbol")
        as_of_date = diag.get("as_of_date") or context.get("as_of_date")
        if symbol and symbol not in row["sample_symbols"] and len(row["sample_symbols"]) < max_samples:
            row["sample_symbols"].append(str(symbol))
        if (
            as_of_date
            and as_of_date not in row["sample_as_of_dates"]
            and len(row["sample_as_of_dates"]) < max_samples
        ):
            row["sample_as_of_dates"].append(str(as_of_date))

    compact = passthrough + list(grouped.values())
    if len(compact) <= max_rows:
        return compact
    hidden = len(compact) - max_rows
    return compact[:max_rows] + [
        {
            "code": "diagnostics_truncated",
            "message": f"{hidden} diagnostic groups omitted from response; inspect report diagnostics for full detail",
            "severity": "warning",
            "count": hidden,
        }
    ]


def _latest_decision_date(decisions: list[dict[str, Any]]) -> Optional[str]:
    dates = [str(d.get("as_of_date") or "") for d in decisions if d.get("as_of_date")]
    return max(dates) if dates else None


def _query_scalar(sql: str, args: tuple = ()) -> Optional[str]:
    with fx_store.connect() as con:
        row = con.execute(sql, args).fetchone()
    if row is None:
        return None
    val = row[0]
    return str(val) if val is not None else None


def _gate_summary(decision: dict[str, Any]) -> dict[str, Any]:
    gates = decision.get("gate_results") or []
    by_name: dict[str, Any] = {}
    for gate in gates:
        if isinstance(gate, dict) and gate.get("gate_name"):
            by_name[str(gate["gate_name"])] = {
                "result": gate.get("result"),
                "reason_code": gate.get("reason_code"),
                "recommended_action": gate.get("recommended_action"),
                "size_multiplier": gate.get("size_multiplier"),
            }
    return {
        "gates": by_name,
        "volatility_action": decision.get("volatility_action"),
        "size_multiplier": decision.get("size_multiplier"),
    }


def _signal_row(decision: dict[str, Any]) -> dict[str, Any]:
    factor_scores = decision.get("factor_scores") or {}
    family_scores = {
        fam: factor_scores.get(fam)
        for fam in PRIMARY_FAMILIES
        if fam in factor_scores
    }
    return {
        "symbol": decision.get("symbol"),
        "direction": decision.get("direction"),
        "action": decision.get("action"),
        "as_of_date": decision.get("as_of_date"),
        "family_scores": family_scores,
        "aligned_families": list(decision.get("aligned_families") or []),
        "blocked_families": list(decision.get("blocked_families") or []),
        "cot_overlay": decision.get("cot_overlay"),
        "gate_summary": _gate_summary(decision),
        "total_score": decision.get("total_score"),
        "reason": decision.get("reason"),
        "diagnostics": list(decision.get("diagnostics") or []),
    }


def _parse_bool_arg(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_eligible(decision: dict[str, Any]) -> bool:
    action = str(decision.get("action") or "")
    return action in {ACTION_BUY, ACTION_SELL}


def _is_blocked(decision: dict[str, Any]) -> bool:
    if decision.get("blocked_families"):
        return True
    action = str(decision.get("action") or "")
    return action not in {ACTION_BUY, ACTION_SELL}


def api_forex_factor_status():
    diagnostics: list[Any] = []
    warnings: list[str] = []
    try:
        decisions = fx_store.load_decisions()
        swap_rows = fx_store.load_latest_swap_audit()
        swap_ok_count = sum(1 for r in swap_rows if str(r.get("status") or "") == "ok")

        action_counts: dict[str, int] = {}
        for d in decisions:
            action = str(d.get("action") or "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1

        latest_as_of = _latest_decision_date(decisions)
        swap_latest = _query_scalar("SELECT MAX(generated_at) FROM swap_audit")
        total_return_latest = _query_scalar("SELECT MAX(date) FROM total_return")
        reer_latest = _query_scalar("SELECT MAX(available_date) FROM reer_observations")
        decisions_latest = _query_scalar("SELECT MAX(as_of_date) FROM decisions")

        if not swap_rows:
            warnings.append("missing:swap_audit")
        if total_return_latest is None:
            warnings.append("missing:total_return")
        if reer_latest is None:
            warnings.append("missing:reer")
        if not decisions:
            warnings.append("missing:decisions")

        data = {
            "latest_as_of_date": latest_as_of,
            "decision_count": len(decisions),
            "action_counts": action_counts,
            "swap_audit_valid_symbol_count": swap_ok_count,
            "data_freshness": {
                "swap_audit_latest_generated_at": swap_latest,
                "total_return_latest_date": total_return_latest,
                "reer_latest_available_date": reer_latest,
                "decisions_latest_date": decisions_latest,
            },
        }
        return _envelope(data=data, warnings=warnings, diagnostics=diagnostics)
    except Exception as exc:
        log.exception("forex factor-status failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_factor_signals():
    diagnostics: list[Any] = []
    warnings: list[str] = []
    try:
        symbol = (request.args.get("symbol") or "").strip().upper() or None
        family = (request.args.get("family") or "").strip().lower() or None
        direction = (request.args.get("direction") or "").strip().upper() or None
        eligible_only = _parse_bool_arg(request.args.get("eligible_only"))
        blocked_only = _parse_bool_arg(request.args.get("blocked_only"))
        as_of_arg = (request.args.get("as_of") or "").strip() or None

        all_decisions = fx_store.load_decisions(symbol=symbol)
        if not all_decisions:
            warnings.append("missing:decisions")
            return _envelope(data={"signals": [], "as_of_date": None}, warnings=warnings)

        as_of_date = as_of_arg or _latest_decision_date(all_decisions)
        decisions = [d for d in all_decisions if d.get("as_of_date") == as_of_date]

        if family:
            decisions = [
                d
                for d in decisions
                if family in (d.get("aligned_families") or [])
                or family in (d.get("factor_scores") or {})
                or family in (d.get("blocked_families") or [])
            ]
        if direction:
            decisions = [
                d for d in decisions if str(d.get("direction") or "").upper() == direction
            ]
        if eligible_only:
            decisions = [d for d in decisions if _is_eligible(d)]
        if blocked_only:
            decisions = [d for d in decisions if _is_blocked(d)]

        signals = [_signal_row(d) for d in decisions]
        return _envelope(
            data={"as_of_date": as_of_date, "signals": signals},
            warnings=warnings,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        log.exception("forex factor-signals failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_swap_audit():
    diagnostics: list[Any] = []
    warnings: list[str] = []
    try:
        symbol = (request.args.get("symbol") or "").strip().upper() or None
        rows = fx_store.load_latest_swap_audit(symbol=symbol)
        if not rows:
            warnings.append("missing:swap_audit")
        for row in rows:
            if str(row.get("status") or "") != "ok":
                warnings.append(
                    f"swap_audit_status:{row.get('symbol')}:{row.get('status')}"
                )
        return _envelope(data={"rows": rows}, warnings=warnings, diagnostics=diagnostics)
    except Exception as exc:
        log.exception("forex swap-audit failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_total_return():
    diagnostics: list[Any] = []
    warnings: list[str] = []
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return _envelope(
            data=None,
            status="invalid_request",
            diagnostics=[{"field": "symbol", "message": "required"}],
            success=False,
            http=400,
        )
    start = (request.args.get("start") or "").strip() or None
    end = (request.args.get("end") or "").strip() or None
    try:
        rows = fx_store.load_total_return(symbol, start=start, end=end)
        for row in rows:
            if str(row.get("data_quality_status") or "") != "ok":
                diagnostics.append(
                    {
                        "date": row.get("date"),
                        "symbol": row.get("symbol"),
                        "data_quality_status": row.get("data_quality_status"),
                    }
                )
        if not rows:
            warnings.append("missing:total_return")
        return _envelope(
            data={
                "symbol": symbol,
                "rows": rows,
                "production_momentum_uses_total_return": True,
            },
            warnings=warnings,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        log.exception("forex total-return failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_cot_positioning():
    diagnostics: list[Any] = []
    warnings: list[str] = []
    status = "ok"
    try:
        try:
            from athena_fx.cot_overlay import cot_overlay, currency_cot_percentile
        except ImportError:
            return _envelope(
                data=None,
                status="blocked",
                diagnostics=[{"code": "fx_factor_module_unavailable", "module": "cot_overlay"}],
                success=False,
                http=503,
            )

        symbol = (request.args.get("symbol") or "").strip().upper() or None
        currency = (request.args.get("currency") or "").strip().upper() or None
        as_of = (request.args.get("as_of") or "").strip() or _dt.date.today().isoformat()

        if currency:
            info = currency_cot_percentile(currency, as_of)
            if info is None:
                status = "degraded"
                diagnostics.append(
                    {"currency": currency, "error": "cot_percentile_unavailable"}
                )
                data = {"view": "currency", "as_of_date": as_of, "currencies": []}
            else:
                data = {"view": "currency", "as_of_date": as_of, "currencies": [info]}
            return _envelope(data=data, status=status, diagnostics=diagnostics, warnings=warnings)

        if symbol:
            pair_views: list[dict[str, Any]] = []
            for direction in ("LONG", "SHORT"):
                try:
                    result = cot_overlay(symbol, direction, as_of)
                    pair_views.append(
                        {
                            "direction": direction,
                            "overlay": result.to_dict() if hasattr(result, "to_dict") else result,
                        }
                    )
                except Exception as exc:
                    status = "degraded"
                    diagnostics.append(
                        {"symbol": symbol, "direction": direction, "error": str(exc)}
                    )
            return _envelope(
                data={"view": "pair", "symbol": symbol, "as_of_date": as_of, "overlays": pair_views},
                status=status,
                diagnostics=diagnostics,
                warnings=warnings,
            )

        currencies: list[dict[str, Any]] = []
        for ccy in G8_CURRENCIES:
            try:
                info = currency_cot_percentile(ccy, as_of)
                if info is None:
                    status = "degraded"
                    diagnostics.append({"currency": ccy, "error": "cot_percentile_unavailable"})
                else:
                    currencies.append(info)
            except Exception as exc:
                status = "degraded"
                diagnostics.append({"currency": ccy, "error": str(exc)})
        return _envelope(
            data={"view": "currency", "as_of_date": as_of, "currencies": currencies},
            status=status,
            diagnostics=diagnostics,
            warnings=warnings,
        )
    except Exception as exc:
        log.exception("forex cot-positioning failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_reer_value():
    diagnostics: list[Any] = []
    warnings: list[str] = []
    try:
        as_of = (request.args.get("as_of") or "").strip() or _dt.date.today().isoformat()
        rows = fx_store.load_reer_observations(available_on_or_before=as_of)
        if not rows:
            warnings.append("missing:reer")

        by_currency: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            ccy = str(row.get("currency") or "").upper()
            by_currency.setdefault(ccy, []).append(dict(row))

        reer_zscore_fn = None
        try:
            from athena_fx.value import reer_zscore as reer_zscore_fn  # noqa: F401
        except ImportError:
            warnings.append("reer_zscore_module_unavailable")

        currencies_out: list[dict[str, Any]] = []
        for ccy in sorted(by_currency):
            history = by_currency[ccy]
            latest_row = sorted(history, key=lambda r: str(r.get("observation_month") or ""))[-1]
            entry: dict[str, Any] = {
                "currency": ccy,
                "observation_month": latest_row.get("observation_month"),
                "available_date": latest_row.get("available_date"),
                "lag_method": latest_row.get("lag_method"),
                "reer_value": latest_row.get("reer_value"),
                "history_count": len(history),
            }
            if reer_zscore_fn is not None:
                try:
                    zinfo = reer_zscore_fn(ccy, as_of)
                    if zinfo:
                        entry["z_score"] = zinfo.get("z")
                        entry["status_label"] = zinfo.get("status_label")
                except Exception as exc:
                    diagnostics.append({"currency": ccy, "error": str(exc)})
            else:
                entry["raw_rows"] = history[-3:]
            currencies_out.append(entry)

        return _envelope(
            data={"as_of_date": as_of, "currencies": currencies_out},
            warnings=warnings,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        log.exception("forex reer-value failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_vol_cost_gates():
    diagnostics: list[Any] = []
    warnings: list[str] = []
    try:
        decisions = fx_store.load_decisions()
        if not decisions:
            warnings.append("missing:decisions")
            return _envelope(
                data={"gate_only": True, "as_of_date": None, "symbols": []},
                warnings=warnings,
            )
        as_of_date = _latest_decision_date(decisions)
        latest = [d for d in decisions if d.get("as_of_date") == as_of_date]
        symbols = [
            {
                "symbol": d.get("symbol"),
                "gate_results": d.get("gate_results") or [],
                "volatility_action": d.get("volatility_action"),
                "cost_diagnostics": d.get("cost_diagnostics") or {},
            }
            for d in latest
        ]
        return _envelope(
            data={"gate_only": True, "as_of_date": as_of_date, "symbols": symbols},
            warnings=warnings,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        log.exception("forex vol-cost-gates failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def _validate_backtest_body(body: dict[str, Any]) -> tuple[Optional[dict[str, Any]], list[Any], int]:
    field_errors: list[Any] = []
    start = str(body.get("start") or "").strip()
    end = str(body.get("end") or "").strip()
    if not _ISO_DATE.match(start):
        field_errors.append({"field": "start", "message": "required ISO date YYYY-MM-DD"})
    if not _ISO_DATE.match(end):
        field_errors.append({"field": "end", "message": "required ISO date YYYY-MM-DD"})

    symbols_raw = body.get("symbols")
    if not isinstance(symbols_raw, list) or not symbols_raw:
        field_errors.append({"field": "symbols", "message": "non-empty list required"})
    else:
        for idx, sym in enumerate(symbols_raw):
            normalized = str(sym).replace("/", "").upper()
            if not _FX_SYMBOL.match(normalized):
                field_errors.append(
                    {"field": f"symbols[{idx}]", "message": "must be 6-char FX symbol"}
                )

    if field_errors:
        return None, field_errors, 400
    normalized_symbols = [str(s).replace("/", "").upper() for s in symbols_raw]
    return {"start": start, "end": end, "symbols": normalized_symbols}, [], 200


def _build_fixture_decide_fn(fixture: dict[str, Any]):
    def decide_fn(
        symbol: str,
        as_of_date: str,
        _has_pos: bool = False,
        **_kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        key = f"{symbol}|{as_of_date}"
        return fixture.get(key)

    return decide_fn


def api_forex_backtest_run():
    body = request.get_json(force=True, silent=True) or {}
    if "strict_factor_data" not in body:
        body["strict_factor_data"] = True

    validated, field_errors, code = _validate_backtest_body(body)
    if validated is None:
        return _envelope(
            data=None,
            status="invalid_request",
            diagnostics=field_errors,
            success=False,
            http=code,
        )

    try:
        from athena_fx.backtest import FxBacktestRequest, run_fx_factor_backtest
    except ImportError:
        return _envelope(
            data=None,
            status="blocked",
            diagnostics=[{"code": "fx_factor_module_unavailable", "module": "backtest"}],
            success=False,
            http=503,
        )

    bars_by_symbol = body.get("bars_by_symbol")
    if not isinstance(bars_by_symbol, dict):
        bars_by_symbol = None
        try:
            from athena_fx.backtest_data import load_daily_bars
        except ImportError:
            return _envelope(
                data=None,
                status="blocked_data",
                diagnostics=[{"code": "missing_ohlc", "message": "bars unavailable"}],
                success=False,
                http=503,
            )
        bar_diagnostics: list = []
        try:
            bars_by_symbol, bar_diags = load_daily_bars(
                validated["symbols"],
                validated["start"],
                validated["end"],
            )
            bar_diagnostics = [
                d.to_dict() if hasattr(d, "to_dict") else d for d in bar_diags
            ]
        except Exception as exc:
            log.exception("load_daily_bars failed")
            return _envelope(
                data=None,
                status="blocked_data",
                diagnostics=[{"code": "missing_ohlc", "message": str(exc)}],
                success=False,
                http=503,
            )
        if not bars_by_symbol:
            return _envelope(
                data=None,
                status="blocked_data",
                diagnostics=(
                    bar_diagnostics
                    or [{"code": "missing_ohlc", "message": "empty bars"}]
                ),
                success=False,
                http=503,
            )

    decide_fn = None
    if "fixture_decisions" in body and isinstance(body["fixture_decisions"], dict):
        decide_fn = _build_fixture_decide_fn(body["fixture_decisions"])
    else:
        try:
            from athena_fx import pipeline as fx_pipeline

            decide_fn = getattr(fx_pipeline, "decide_for_backtest", None)
        except ImportError:
            decide_fn = None

    request_payload: dict[str, Any] = {
        "start": validated["start"],
        "end": validated["end"],
        "symbols": validated["symbols"],
        "strict_factor_data": bool(body.get("strict_factor_data", True)),
        "cost_model_enabled": bool(body.get("cost_model_enabled", True)),
    }
    if body.get("slippage_pips") is not None:
        request_payload["slippage_pips"] = float(body["slippage_pips"])
    if body.get("spread_pips_by_symbol"):
        request_payload["spread_pips_by_symbol"] = body["spread_pips_by_symbol"]
    elif not request_payload.get("spread_pips_by_symbol"):
        from config import CONFIG

        raw_spreads = CONFIG.get("FX_FACTOR_SPREAD_PIPS_BY_SYMBOL") or {}
        if isinstance(raw_spreads, dict):
            spread_map: dict[str, float] = {}
            for sym in validated["symbols"]:
                val = raw_spreads.get(sym) or raw_spreads.get(
                    f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 else sym
                )
                if val is not None:
                    try:
                        spread_map[sym] = float(val)
                    except (TypeError, ValueError):
                        pass
            request_payload["spread_pips_by_symbol"] = spread_map

    swap_returns: dict[str, dict[str, float | int]] = {}
    for sym in validated["symbols"]:
        audits = fx_store.load_latest_swap_audit(symbol=sym)
        if not audits or audits[0].get("status") != "ok":
            continue
        row = audits[0]
        swap_returns[sym] = {
            "long_daily": float(row.get("daily_swap_return_long") or 0.0),
            "short_daily": float(row.get("daily_swap_return_short") or 0.0),
            "rollover3days": int(row.get("swap_rollover3days") or 3),
        }

    try:
        bt_request = FxBacktestRequest(**request_payload)
        result = run_fx_factor_backtest(
            bt_request,
            bars_by_symbol=bars_by_symbol,
            decide_fn=decide_fn,
            swap_returns_by_symbol=swap_returns or None,
        )
    except Exception as exc:
        log.exception("forex backtest run failed")
        status = "error"
        diagnostics = [{"code": "backtest_failed", "message": str(exc)}]
        if hasattr(exc, "diagnostics"):
            diagnostics.extend(getattr(exc, "diagnostics", []))
        return _envelope(
            data={"report": getattr(exc, "report", None)},
            status=status,
            diagnostics=diagnostics,
            success=False,
            http=500,
        )

    if hasattr(result, "to_dict"):
        result = result.to_dict()
    elif not isinstance(result, dict):
        result = {"run_id": str(result)}

    return _envelope(
        data={"run_id": result.get("run_id"), "report": result.get("report", result)},
        diagnostics=_summarize_diagnostics_for_response(list(result.get("diagnostics") or [])),
    )


def api_forex_backtest_get(run_id: str):
    try:
        run = fx_store.load_backtest_run(run_id)
        if run is None:
            return _envelope(
                data=None,
                status="not_found",
                diagnostics=[{"code": "unknown_run_id", "run_id": run_id}],
                success=False,
                http=404,
            )
        return _envelope(data=run)
    except Exception as exc:
        log.exception("forex backtest get failed for %s", run_id)
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def _latest_completed_backtest() -> Optional[dict[str, Any]]:
    with fx_store.connect() as con:
        row = con.execute(
            "SELECT run_id FROM backtest_runs WHERE status = ? ORDER BY created_at DESC LIMIT 1",
            ("completed",),
        ).fetchone()
    if row is None:
        return None
    return fx_store.load_backtest_run(str(row["run_id"]))


_FX_VALIDATION_REQUIRED_MESSAGE = (
    "Run an FX Factor backtest that passes validation "
    "(completed run with at least 30 trades per carry, momentum, and value family) "
    "before demo execution."
)
_FX_VALIDATION_MIN_FAMILY_TRADES = 30


def _family_trade_counts_from_validation(
    validation: dict[str, Any] | None,
) -> dict[str, int]:
    if not isinstance(validation, dict):
        return {}
    raw = validation.get("family_trade_counts") or validation.get("by_family") or {}
    if not isinstance(raw, dict):
        return {}
    counts: dict[str, int] = {}
    for key, value in raw.items():
        try:
            counts[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return counts


def _validation_pass_from_snapshot(
    validation: dict[str, Any] | None,
    *,
    run_status: str | None = None,
) -> bool:
    """Match backtest report semantics; recompute pass for legacy reports missing the flag."""
    if not isinstance(validation, dict):
        return False
    if validation.get("pass") is True:
        return True
    if run_status and str(run_status).lower() != "completed":
        return False
    counts = _family_trade_counts_from_validation(validation)
    if not counts:
        return False
    return all(count >= _FX_VALIDATION_MIN_FAMILY_TRADES for count in counts.values())


def _enrich_validation_snapshot(
    validation: dict[str, Any] | None,
    *,
    run_status: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(validation, dict):
        return None
    enriched = dict(validation)
    counts = _family_trade_counts_from_validation(enriched)
    if counts:
        enriched["family_trade_counts"] = counts
    enriched["pass"] = _validation_pass_from_snapshot(enriched, run_status=run_status)
    enriched["n_warning"] = any(c < _FX_VALIDATION_MIN_FAMILY_TRADES for c in counts.values())
    return enriched


def _validation_block_detail(
    validation: dict[str, Any] | None,
    run_id: str | None,
) -> dict[str, Any]:
    counts = _family_trade_counts_from_validation(validation)
    failing = {
        family: count
        for family, count in counts.items()
        if count < _FX_VALIDATION_MIN_FAMILY_TRADES
    }
    detail: dict[str, Any] = {
        "code": "FX_FACTOR_VALIDATION_REQUIRED",
        "message": _FX_VALIDATION_REQUIRED_MESSAGE,
    }
    if run_id:
        detail["run_id"] = run_id
    if counts:
        detail["family_trade_counts"] = counts
    if failing:
        detail["failing_families"] = failing
        detail["message"] = (
            "Demo execution requires at least "
            f"{_FX_VALIDATION_MIN_FAMILY_TRADES} aligned trades per factor family. "
            f"Below threshold: {', '.join(f'{k}={v}' for k, v in failing.items())}."
        )
    elif not counts:
        detail["message"] = (
            "No completed FX Factor backtest validation found. "
            "Run a backtest in the Backtesting tab first."
        )
    return detail


def _fx_validation_required_for_execution() -> bool:
    return bool(CONFIG.get("FX_FACTOR_REQUIRE_VALIDATION_FOR_EXECUTION", True))


def _fx_backtest_validation_state() -> tuple[bool, dict[str, Any] | None, str | None]:
    """Return (passed, validation_snapshot, completed_run_id)."""
    if not _fx_validation_required_for_execution():
        return True, None, None
    run = _latest_completed_backtest()
    run_id = str(run.get("run_id") or "") if isinstance(run, dict) else None
    run_status = str(run.get("status") or "") if isinstance(run, dict) else None
    raw_validation = (run or {}).get("report", {}).get("validation") if run else None
    validation = _enrich_validation_snapshot(
        raw_validation if isinstance(raw_validation, dict) else None,
        run_status=run_status,
    )
    if validation and validation.get("pass") is True:
        return True, validation, run_id
    return False, validation, run_id


def api_forex_validation_latest():
    warnings: list[str] = []
    diagnostics: list[Any] = []
    try:
        run = _latest_completed_backtest()
        if run is None:
            warnings.append("missing:completed_backtest")
            return _envelope(
                data={"validation": None, "summary": None, "pair_sanity_flags": None},
                warnings=warnings,
            )
        report = run.get("report") or {}
        validation = _enrich_validation_snapshot(
            report.get("validation") if isinstance(report.get("validation"), dict) else None,
            run_status=str(run.get("status") or ""),
        )
        summary = report.get("summary")
        pair_sanity = report.get("pair_sanity_flags")
        n = None
        if isinstance(validation, dict):
            n = validation.get("n") or validation.get("sample_size")
        if isinstance(summary, dict) and n is None:
            n = summary.get("n") or summary.get("trade_count")
        if n is not None:
            try:
                if int(n) < 30:
                    warnings.append("validation_sample_size_below_30")
            except (TypeError, ValueError):
                pass
        return _envelope(
            data={
                "run_id": run.get("run_id"),
                "validation": validation,
                "summary": summary,
                "pair_sanity_flags": pair_sanity,
            },
            warnings=warnings,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        log.exception("forex validation/latest failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def _module_import_check(module_name: str) -> Optional[str]:
    try:
        __import__(module_name)
        return None
    except ImportError:
        return f"module_unavailable:{module_name}"


def api_forex_diagnostics():
    warnings: list[str] = []
    diagnostics: list[Any] = []
    try:
        decisions = fx_store.load_decisions()
        as_of_date = _latest_decision_date(decisions)
        latest = [d for d in decisions if d.get("as_of_date") == as_of_date] if as_of_date else []

        for d in latest:
            for diag in d.get("diagnostics") or []:
                diagnostics.append({"source": "decision", "symbol": d.get("symbol"), **diag})

        swap_rows = fx_store.load_latest_swap_audit()
        for row in swap_rows:
            if str(row.get("status") or "") != "ok":
                diagnostics.append(
                    {
                        "source": "swap_audit",
                        "symbol": row.get("symbol"),
                        "status": row.get("status"),
                    }
                )
            for diag in row.get("diagnostics") or []:
                diagnostics.append({"source": "swap_audit", "symbol": row.get("symbol"), **diag})

        reer_latest = _query_scalar("SELECT MAX(available_date) FROM reer_observations")
        if reer_latest:
            try:
                latest_dt = _dt.date.fromisoformat(reer_latest[:10])
                age_days = (_dt.date.today() - latest_dt).days
                if age_days > 90:
                    warnings.append("stale_reer")
                    diagnostics.append(
                        {
                            "code": "stale_reer",
                            "available_date": reer_latest,
                            "age_days": age_days,
                        }
                    )
            except ValueError:
                pass
        else:
            warnings.append("missing:reer")

        for mod in (
            "athena_fx.momentum",
            "athena_fx.carry",
            "athena_fx.value",
            "athena_fx.cot_overlay",
            "athena_fx.backtest",
            "athena_fx.execution",
        ):
            unavailable = _module_import_check(mod)
            if unavailable:
                warnings.append(unavailable)

        parity = {
            "parity": "shared_adapter",
            "adapter": "athena_fx.derivatives_adapter.resolve_factor_derivatives",
        }
        return _envelope(
            data={
                "as_of_date": as_of_date,
                "diagnostics": diagnostics,
                "parity": parity,
            },
            warnings=warnings,
        )
    except Exception as exc:
        log.exception("forex diagnostics failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_refresh_decisions():
    body = request.get_json(force=True, silent=True) or {}
    symbols_raw = body.get("symbols")
    symbols = None
    if isinstance(symbols_raw, list) and symbols_raw:
        symbols = [str(s).replace("/", "").upper() for s in symbols_raw]
    as_of = str(body.get("as_of") or "").strip() or None
    try:
        from athena_fx.pipeline import refresh_decisions

        summary = refresh_decisions(symbols=symbols, as_of_date=as_of)
        return _envelope(data=summary)
    except Exception as exc:
        log.exception("forex refresh-decisions failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "refresh_failed", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_rebalance():
    body = request.get_json(force=True, silent=True) or {}
    execute_trades = bool(body.get("executeTrades", False))
    dry_run = bool(body.get("dryRun", False))
    as_of = str(body.get("as_of") or "").strip() or None
    symbols_raw = body.get("symbols")
    symbols = None
    if isinstance(symbols_raw, list) and symbols_raw:
        symbols = [str(s).replace("/", "").upper() for s in symbols_raw]

    from config import CONFIG

    if execute_trades and not dry_run:
        if not CONFIG.get("FX_FACTOR_EXECUTION_ENABLED", False):
            return _envelope(
                data=None,
                status="blocked",
                diagnostics=[{"code": "FX_FACTOR_EXECUTION_DISABLED"}],
                success=False,
                http=403,
            )
        if not CONFIG.get("EXECUTION_ENABLED", False):
            return _envelope(
                data=None,
                status="blocked",
                diagnostics=[{"code": "EXECUTION_DISABLED"}],
                success=False,
                http=403,
            )

    try:
        from athena_fx.rebalance import run_fx_factor_rebalance

        result = run_fx_factor_rebalance(
            symbols=symbols,
            as_of_date=as_of,
            execute_trades=execute_trades,
            dry_run=dry_run,
        )
        return _envelope(data=result)
    except Exception as exc:
        log.exception("forex rebalance failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "rebalance_failed", "message": str(exc)}],
            success=False,
            http=500,
        )


def _trading_status_payload() -> dict[str, Any]:
    block_reasons: list[str] = []
    execution_enabled = bool(CONFIG.get("EXECUTION_ENABLED", False))
    fx_execution_enabled = bool(CONFIG.get("FX_FACTOR_EXECUTION_ENABLED", False))
    mt5_execution_enabled = bool(CONFIG.get("MT5_EXECUTION_ENABLED", True))
    kill_switch = bool(CONFIG.get("KILL_SWITCH", False))
    if not execution_enabled:
        block_reasons.append("EXECUTION_DISABLED")
    if not fx_execution_enabled:
        block_reasons.append("FX_FACTOR_EXECUTION_DISABLED")
    if not mt5_execution_enabled:
        block_reasons.append("MT5_EXECUTION_DISABLED")
    if kill_switch:
        block_reasons.append("KILL_SWITCH")
    if str(CONFIG.get("EXECUTOR_MODE", "paper")).lower() == "live" and not bool(
        CONFIG.get("FX_FACTOR_ALLOW_LIVE_EXECUTION", False)
    ):
        block_reasons.append("FX_FACTOR_LIVE_EXECUTION_DISABLED")

    latest_scan = fx_store.load_latest_scan()
    latest_scan_at = latest_scan.get("generated_at") if latest_scan else None
    decisions = fx_store.load_decisions()
    latest_decision_date = _latest_decision_date(decisions)

    open_positions: list[Any] = []
    account_mode = "unknown"
    account: dict[str, Any] | None = None
    try:
        from athena_fx.execution import default_fx_execution_deps

        deps = default_fx_execution_deps()
        positions, _raw = deps.get_positions()
        open_positions = [
            p for p in positions if str(p.get("symbol") or p.get("pair") or "").replace("/", "").upper() in G8_PAIRS
        ]
        account = deps.get_account()
        mode = deps.get_mt5_trade_mode()
        account_mode = "demo" if mode == 0 else "live" if mode == 2 else "unknown"
    except Exception as exc:
        block_reasons.append(f"MT5_STATUS_UNAVAILABLE:{exc}")

    validation_required = _fx_validation_required_for_execution()
    validation_pass, validation_snapshot, validation_run_id = _fx_backtest_validation_state()
    if validation_required and not validation_pass:
        block_reasons.append("FX_FACTOR_VALIDATION_REQUIRED")

    can_demo_execute = not block_reasons
    return {
        "market_open": True,
        "execution_enabled": execution_enabled,
        "fx_factor_execution_enabled": fx_execution_enabled,
        "mt5_execution_enabled": mt5_execution_enabled,
        "kill_switch": kill_switch,
        "executor_mode": str(CONFIG.get("EXECUTOR_MODE", "paper")),
        "account_mode": account_mode,
        "account": account,
        "auto_trade_enabled": bool(CONFIG.get("AUTO_TRADE_ENABLED", False)),
        "open_positions": open_positions,
        "latest_scan_at": latest_scan_at,
        "latest_decision_date": latest_decision_date,
        "validation_required": validation_required,
        "validation_pass": validation_pass,
        "validation_run_id": validation_run_id,
        "validation": validation_snapshot,
        "can_dry_run": True,
        "can_demo_execute": can_demo_execute,
        "block_reasons": block_reasons,
    }


def api_forex_trading_status():
    try:
        return _envelope(data=_trading_status_payload())
    except Exception as exc:
        log.exception("forex trading-status failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "internal_error", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_scan():
    body = request.get_json(force=True, silent=True) or {}
    symbols_raw = body.get("symbols")
    symbols = None
    if isinstance(symbols_raw, list) and symbols_raw:
        symbols = [str(s).replace("/", "").upper() for s in symbols_raw]
    refresh_requested = bool(
        body.get(
            "refreshDecisions",
            CONFIG.get("FX_FACTOR_SCAN_REFRESH_DECISIONS_DEFAULT", True),
        )
    )
    include_blocked = bool(
        body.get(
            "includeBlocked",
            CONFIG.get("FX_FACTOR_SCAN_INCLUDE_BLOCKED_DEFAULT", True),
        )
    )
    strict_factor_data = bool(
        body.get(
            "strictFactorData",
            CONFIG.get("FX_FACTOR_STRICT_DATA_DEFAULT", True),
        )
    )
    as_of = str(body.get("as_of") or "").strip() or None
    try:
        if not bool(CONFIG.get("FX_FACTOR_SCANNER_ENABLED", True)):
            return _envelope(
                data=None,
                status="blocked",
                diagnostics=[{"code": "FX_FACTOR_SCANNER_DISABLED"}],
                success=False,
                http=403,
            )
        status = _trading_status_payload()
        from athena_fx.scanner import scan_forex

        scan = scan_forex(
            symbols=symbols,
            refresh=refresh_requested,
            include_blocked=include_blocked,
            strict_factor_data=strict_factor_data,
            as_of_date=as_of,
            execution_allowed=bool(status.get("can_demo_execute")),
            execution_block_reason=";".join(status.get("block_reasons") or []) or None,
        )
        fx_store.save_scan(scan)
        return _envelope(
            data=scan,
            diagnostics=list(scan.get("diagnostics") or []),
            warnings=list(scan.get("warnings") or []),
        )
    except Exception as exc:
        log.exception("forex scan failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "scan_failed", "message": str(exc)}],
            success=False,
            http=500,
        )


def api_forex_scan_latest():
    scan = fx_store.load_latest_scan()
    if scan is None:
        return _envelope(data={"scan": None}, warnings=["missing:scan"])
    return _envelope(data=scan)


def _candidate_failed_gates(candidate: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    gates = candidate.get("gate_results") or {}
    if isinstance(gates, list):
        iterable = ((g.get("gate_name"), g) for g in gates if isinstance(g, dict))
    else:
        iterable = gates.items() if isinstance(gates, dict) else []
    for name, gate in iterable:
        if isinstance(gate, dict) and gate.get("result") == GATE_FAIL:
            failed.append(f"{name}:{gate.get('reason_code')}")
    return failed


def _candidate_to_decision(candidate: dict[str, Any]) -> dict[str, Any]:
    gates = candidate.get("gate_results") or {}
    if isinstance(gates, dict):
        gate_results = [
            {"gate_name": name, **gate} for name, gate in gates.items() if isinstance(gate, dict)
        ]
    else:
        gate_results = list(gates or [])
    return {
        "symbol": candidate.get("symbol"),
        "as_of_date": candidate.get("last_factor_decision_date"),
        "action": candidate.get("action"),
        "direction": candidate.get("direction"),
        "total_score": sum(
            abs(float(v.get("points") or 0.0))
            for v in (candidate.get("family_scores") or {}).values()
            if isinstance(v, dict)
        ),
        "size_multiplier": candidate.get("size_multiplier"),
        "aligned_families": candidate.get("aligned_families") or [],
        "blocked_families": candidate.get("blocked_families") or [],
        "gate_results": gate_results,
        "reason": candidate.get("reason"),
    }


def api_forex_execute_candidate():
    body = request.get_json(force=True, silent=True) or {}
    candidate = body.get("candidate")
    dry_run = bool(body.get("dryRun", True))
    if not isinstance(candidate, dict):
        return _envelope(
            data=None,
            status="invalid_request",
            diagnostics=[{"code": "MISSING_CANDIDATE"}],
            success=False,
            http=400,
        )
    action = str(candidate.get("action") or "")
    if action not in {ACTION_BUY, ACTION_SELL, ACTION_FLATTEN, ACTION_REDUCE}:
        return _envelope(
            data=None,
            status="invalid_request",
            diagnostics=[{"code": "ACTION_NOT_EXECUTABLE", "action": action}],
            success=False,
            http=400,
        )
    failed = _candidate_failed_gates(candidate)
    if failed:
        return _envelope(
            data=None,
            status="blocked",
            diagnostics=[{"code": "HARD_GATE_FAILED", "gates": failed}],
            success=False,
            http=403,
        )
    if not dry_run and not bool(candidate.get("tradable_now")) and action not in {ACTION_FLATTEN, ACTION_REDUCE}:
        return _envelope(
            data=None,
            status="blocked",
            diagnostics=[{"code": "CANDIDATE_NOT_TRADABLE"}],
            success=False,
            http=403,
        )

    if not dry_run:
        from athena_fx.execution import default_fx_execution_deps, assert_fx_execution_allowed

        allowed, reason = assert_fx_execution_allowed(default_fx_execution_deps())
        if not allowed:
            return _envelope(
                data=None,
                status="blocked",
                diagnostics=[{"code": reason}],
                success=False,
                http=403,
            )
        validation_pass, validation_snapshot, validation_run_id = _fx_backtest_validation_state()
        if not validation_pass:
            return _envelope(
                data=None,
                status="blocked",
                diagnostics=[
                    _validation_block_detail(validation_snapshot, validation_run_id),
                ],
                success=False,
                http=403,
            )

    try:
        from athena_fx.execution import (
            close_fx_symbol_position,
            execute_fx_decision,
            fx_decision_to_execution_dict,
        )

        decision = _candidate_to_decision(candidate)
        if action == ACTION_FLATTEN:
            result = close_fx_symbol_position(
                str(candidate.get("symbol") or ""),
                dry_run=dry_run,
            )
            return _envelope(data=result)
        if action == ACTION_REDUCE:
            return _envelope(
                data={
                    "executed": False,
                    "dry_run": dry_run,
                    "reason": "REDUCE_NOT_SUPPORTED_BY_EXECUTOR",
                    "symbol": candidate.get("symbol"),
                },
                status="blocked" if not dry_run else "ok",
                http=501 if not dry_run else 200,
                success=dry_run,
            )
        if dry_run:
            signal = fx_decision_to_execution_dict(
                decision,
                entry_price=float(candidate.get("entry") or candidate.get("current_price") or 0),
                sl=float(candidate.get("sl") or 0),
                tp1=float(candidate.get("tp1") or 0),
                tp2=float(candidate.get("tp2") or candidate.get("tp1") or 0),
            )
            return _envelope(
                data={
                    "executed": False,
                    "dry_run": True,
                    "reason": "dry_run",
                    "symbol": candidate.get("symbol"),
                    "signal": signal,
                    "risk_result": {},
                    "guardian_result": {},
                }
            )
        result = execute_fx_decision(decision, dry_run=False)
        return _envelope(data=result, success=bool(result.get("executed")), status="ok" if result.get("executed") else "blocked")
    except Exception as exc:
        log.exception("forex execute-candidate failed")
        return _envelope(
            data=None,
            status="error",
            diagnostics=[{"code": "execute_candidate_failed", "message": str(exc)}],
            success=False,
            http=500,
        )


def register_forex_factor_routes(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    endpoints = [
        ("/api/forex/factor-status", "api_forex_factor_status", api_forex_factor_status, ["GET"]),
        ("/api/forex/factor-signals", "api_forex_factor_signals", api_forex_factor_signals, ["GET"]),
        ("/api/forex/swap-audit", "api_forex_swap_audit", api_forex_swap_audit, ["GET"]),
        ("/api/forex/total-return", "api_forex_total_return", api_forex_total_return, ["GET"]),
        (
            "/api/forex/cot-positioning",
            "api_forex_cot_positioning",
            api_forex_cot_positioning,
            ["GET"],
        ),
        ("/api/forex/reer-value", "api_forex_reer_value", api_forex_reer_value, ["GET"]),
        (
            "/api/forex/vol-cost-gates",
            "api_forex_vol_cost_gates",
            api_forex_vol_cost_gates,
            ["GET"],
        ),
        (
            "/api/forex/backtest/run",
            "api_forex_backtest_run",
            api_forex_backtest_run,
            ["POST"],
        ),
        (
            "/api/forex/backtest/<run_id>",
            "api_forex_backtest_get",
            api_forex_backtest_get,
            ["GET"],
        ),
        (
            "/api/forex/validation/latest",
            "api_forex_validation_latest",
            api_forex_validation_latest,
            ["GET"],
        ),
        ("/api/forex/diagnostics", "api_forex_diagnostics", api_forex_diagnostics, ["GET"]),
        (
            "/api/forex/refresh-decisions",
            "api_forex_refresh_decisions",
            api_forex_refresh_decisions,
            ["POST"],
        ),
        (
            "/api/forex/rebalance",
            "api_forex_rebalance",
            api_forex_rebalance,
            ["POST"],
        ),
        ("/api/forex/trading-status", "api_forex_trading_status", api_forex_trading_status, ["GET"]),
        ("/api/forex/execution-status", "api_forex_execution_status", api_forex_trading_status, ["GET"]),
        ("/api/forex/scan", "api_forex_scan", api_forex_scan, ["POST"]),
        ("/api/forex/scan/latest", "api_forex_scan_latest", api_forex_scan_latest, ["GET"]),
        ("/api/forex/execute-candidate", "api_forex_execute_candidate", api_forex_execute_candidate, ["POST"]),
    ]
    for rule, name, view, methods in endpoints:
        if rule not in rules:
            app.add_url_rule(rule, name, view, methods=methods)
