"""Flask routes for ASE operational scan + journal (demo/paper only)."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from flask import jsonify, request

from athena_ase.execution.journal import load_trade_journal, trade_journal_summary
from athena_ase.horizon import Horizon
from athena_ase.instruments import instrument_by_symbol
from athena_ase.registry.artifacts import (
    active_artifact_version,
    artifact_content_hash,
    default_artifacts_root,
    load_manifest,
)
from athena_ase.registry.promotion import get_deployment_binding
from athena_ase.runtime.health import ase_health
from athena_ase.validation.gates import check_provisional
from athena_ase.runtime.scan import run_ase_dual_horizon_scan, run_ase_scan
from athena_research.ase.training_report import write_training_report

log = logging.getLogger("sentinel.ase")

_MODEL_FAMILIES = ("forex", "crypto", "commodity", "equity", "index_etf")
_MODEL_HORIZONS = ("intraday", "swing")


def _json_safe(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _training_report_summary() -> dict[str, Any]:
    path = Path("reports") / "ase_training_report.md"
    if not path.exists():
        return {"available": False, "path": str(path)}
    text = path.read_text(encoding="utf-8")
    families: list[dict[str, str]] = []
    for line in text.splitlines():
        if line.startswith("|") and "|" in line[1:] and "family" not in line:
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 8 and parts[0] not in ("---", ""):
                families.append(
                    {
                        "family": parts[0],
                        "horizon": parts[1],
                        "evalTrades": parts[2],
                        "expectancy": parts[3],
                        "threshold": parts[6],
                    }
                )
    return {"available": True, "path": str(path), "families": families}


def _model_provenance() -> list[dict[str, Any]]:
    """Read latest frozen manifest metadata without loading model pickle files."""
    root = default_artifacts_root()
    active_version = active_artifact_version(root=root)
    rows: list[dict[str, Any]] = []
    for family in _MODEL_FAMILIES:
        for horizon in _MODEL_HORIZONS:
            base = root / family / horizon
            if not base.exists():
                continue
            binding = get_deployment_binding(family, horizon)
            selected_version = binding.version if binding is not None else active_version
            versions = (
                [base / selected_version]
                if selected_version
                else sorted(
                    (path for path in base.iterdir() if path.is_dir()),
                    key=lambda path: path.name,
                    reverse=True,
                )
            )
            versions = [path for path in versions if path.is_dir()]
            if not versions:
                if selected_version:
                    rows.append(
                        {
                            "family": family,
                            "horizon": horizon,
                            "version": selected_version,
                            "available": False,
                            "threshold": None,
                            "thresholdSource": "no trained artifact (FLAT-only)",
                            "thresholdFallback": False,
                            "datasetHash": "",
                            "trainedAt": None,
                        }
                    )
                continue
            manifest_path = versions[0] / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = load_manifest(manifest_path)
            except (OSError, TypeError, ValueError) as exc:
                log.warning("ASE manifest provenance read failed for %s: %s", manifest_path, exc)
                continue
            eval_summary = manifest.eval_summary or {}
            validation_metrics = (manifest.validation_report or {}).get("metrics") or {}
            provisional_ok, promotion_failures = check_provisional(
                validation_metrics.get("holdout_gate_metrics") or {}
            )
            digest = artifact_content_hash(manifest)
            binding_matches = bool(
                binding is not None
                and binding.version == manifest.version
                and binding.artifact_hash == digest
            )
            if not binding_matches:
                promotion_failures = [*promotion_failures, "not_exactly_promoted"]
            rows.append(
                {
                    "family": manifest.family,
                    "horizon": manifest.horizon,
                    "version": manifest.version,
                    "artifactHash": digest,
                    "available": True,
                    "runtimeEligible": provisional_ok and binding_matches,
                    "promotionFailures": promotion_failures,
                    "threshold": float(manifest.thr_family),
                    "thresholdSource": (
                        eval_summary.get("threshold_source")
                        or validation_metrics.get("threshold_source")
                    ),
                    "thresholdFallback": bool(manifest.threshold_fallback),
                    "datasetHash": manifest.dataset_hash,
                    "trainedAt": manifest.trained_at,
                }
            )
    return rows


def _extract_ingest_sources(d: dict[str, Any]) -> tuple[tuple[str, ...] | None, bool]:
    """Return (sources, explicitly_set) from request body.

    - If ``skipIngest`` is true -> empty sources (disable default ingest).
    - If ``ingestSources`` is provided -> use those sources.
    - Otherwise -> ``(None, False)`` so the scan function uses its own default.
    """
    if bool(d.get("skipIngest", False)):
        return (), True
    raw_sources = d.get("ingestSources")
    if isinstance(raw_sources, list):
        sources = tuple(str(s).strip().lower() for s in raw_sources if str(s).strip())
        return sources, True
    return None, False


def api_ase_scan():
    d = request.get_json(force=True, silent=True) or {}
    horizon_raw = str(d.get("horizon", "both")).lower()
    family = str(d.get("family") or d.get("assetClass") or "").strip().lower() or None
    symbols = d.get("symbols") or d.get("pairs")
    sym_list = [str(s) for s in symbols] if isinstance(symbols, list) else None
    write_journal = bool(d.get("writeJournal", True))
    # Scan is read-only: never execute from a scan request. Trades require the
    # explicit per-symbol /api/ase-execute endpoint (matches other engines).
    execute_trades = False
    ingest_sources, _ = _extract_ingest_sources(d)
    scan_kwargs = {
        "family": family,
        "symbols": sym_list,
        "write_journal": write_journal,
        "execute_trades": execute_trades,
        "ptis_root": str(d.get("ptisRoot") or "") or None,
    }
    if ingest_sources is not None:
        scan_kwargs["ingest_sources"] = ingest_sources
    try:
        if horizon_raw == "both":
            result = run_ase_dual_horizon_scan(**scan_kwargs)
        else:
            horizon: Horizon = "swing" if horizon_raw == "swing" else "intraday"
            result = run_ase_scan(horizon=horizon, **scan_kwargs)
        return jsonify(_json_safe(result))
    except Exception as exc:
        log.exception("ASE scan failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def api_ase_execute():
    d = request.get_json(force=True, silent=True) or {}
    symbol = str(d.get("instrument") or d.get("symbol") or "").strip()
    if not symbol:
        return jsonify({"success": False, "error": "instrument_required"}), 400

    instrument = instrument_by_symbol(symbol)
    if instrument is None:
        return jsonify({"success": False, "error": f"unknown_instrument:{symbol}"}), 400

    horizon_raw = str(d.get("horizon") or "").strip().lower()
    if horizon_raw not in ("intraday", "swing"):
        return jsonify({"success": False, "error": "horizon_must_be_intraday_or_swing"}), 400
    horizon: Horizon = "swing" if horizon_raw == "swing" else "intraday"

    ingest_sources, _ = _extract_ingest_sources(d)
    scan_kwargs: dict[str, Any] = {
        "symbols": [instrument.symbol],
        "horizon": horizon,
        "write_journal": True,
        "execute_trades": True,
    }
    if ingest_sources is not None:
        scan_kwargs["ingest_sources"] = ingest_sources

    try:
        result = run_ase_scan(**scan_kwargs)
        signals = result.get("signals") or []
        signal = signals[0] if signals else None
        if not signal:
            return jsonify(
                {
                    "success": False,
                    "executed": False,
                    "reason": "signal_unavailable",
                }
            )

        status = str(signal.get("decisionStatus") or "ERROR")
        if status != "TRADE":
            return jsonify(
                {
                    "success": False,
                    "executed": False,
                    "reason": f"signal_status:{status}",
                    "signal": _json_safe(signal),
                }
            )

        executions = result.get("executions") or []
        if not executions:
            return jsonify(
                {
                    "success": False,
                    "executed": False,
                    "reason": "execution_result_missing",
                    "signal": _json_safe(signal),
                }
            )

        execution = executions[0]
        executed = bool(execution.get("executed"))
        return jsonify(
            {
                "success": executed,
                "executed": executed,
                "reason": str(execution.get("reason") or ("ok" if executed else "execution_failed")),
                "execution": _json_safe(execution),
                "signal": _json_safe(signal),
            }
        )
    except Exception as exc:
        log.exception("ASE manual execution failed for %s", instrument.symbol)
        return jsonify({"success": False, "error": str(exc)}), 500


def api_ase_journal_summary():
    try:
        journal = trade_journal_summary()
        training = _training_report_summary()
        return jsonify(
            {
                "success": True,
                "deployment": "OPERATIONAL",
                "journal": journal,
                "trainingReport": training,
            }
        )
    except Exception as exc:
        log.exception("ASE journal summary failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def _cell(value: Any) -> Any:
    """Journal cell -> JSON-safe scalar (pandas NULL/NaN -> None)."""
    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _cell_float(value: Any) -> float | None:
    value = _cell(value)
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _execution_detail_dict(raw: Any) -> dict[str, Any]:
    """Journalled execution detail as a JSON-safe mapping."""
    raw = _cell(raw)
    if not raw:
        return {}
    try:
        detail = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return {}
    return detail if isinstance(detail, dict) else {}


def _execution_detail_extract(raw: Any) -> dict[str, Any]:
    """Pull fill price / volume / ticket out of the journalled broker result."""
    detail = _execution_detail_dict(raw)
    out: dict[str, Any] = {}
    for key in ("fillPrice", "fill_price", "price"):
        val = _cell_float(detail.get(key))
        if val is not None:
            out["fillPrice"] = val
            break
    for key in ("volume", "lots", "qty", "quantity"):
        val = _cell_float(detail.get(key))
        if val is not None:
            out["volume"] = val
            break
    for key in ("ticket", "order_ticket", "orderId", "order_id"):
        val = detail.get(key)
        if val not in (None, ""):
            out["ticket"] = str(val)
            break
    return out


def _execution_pipeline(raw: Any, status: Any) -> dict[str, Any]:
    """Normalize new and legacy journal details into display-only stage state."""
    detail = _execution_detail_dict(raw)
    pipeline = detail.get("pipeline")
    if isinstance(pipeline, dict):
        return {str(key): _json_safe(value) for key, value in pipeline.items()}

    normalized_status = str(_cell(status) or "").lower()
    if normalized_status == "filled":
        # A fill from the ASE bridge is reachable only after all three checks.
        return {
            "gate": "passed",
            "guardian": "passed",
            "risk": "passed",
            "fill": "filled",
        }

    stage = str(detail.get("stage") or "").lower()
    stages = {
        "gate": "pending",
        "guardian": "pending",
        "risk": "pending",
        "fill": normalized_status or "pending",
    }
    order = ("gate", "guardian", "risk", "fill")
    aliases = {"demo_gate": "gate", "freshness": "gate", "venue": "gate"}
    stage = aliases.get(stage, stage)
    if stage in order:
        for prior in order[: order.index(stage)]:
            stages[prior] = "passed"
        stages[stage] = (
            "blocked"
            if normalized_status == "rejected"
            else normalized_status or "failed"
        )
    return stages


def _executed_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    entry = _cell_float(row.get("entryReference"))
    sl = _cell_float(row.get("sl"))
    tp1 = _cell_float(row.get("tp1"))
    rr1 = None
    if entry is not None and sl is not None and tp1 is not None:
        sl_dist = abs(entry - sl)
        if sl_dist > 0:
            rr1 = abs(tp1 - entry) / sl_dist
    decision_ms = _cell(row.get("decisionTimeMs"))
    detail = _execution_detail_dict(row.get("executionDetail"))
    instrument = _cell(row.get("instrument"))
    display = _cell(row.get("display"))
    if instrument and not display:
        inst = instrument_by_symbol(str(instrument))
        display = inst.display if inst is not None else instrument
    return {
        "instrument": instrument,
        "display": display,
        "direction": _cell(row.get("direction")),
        "horizon": _cell(row.get("horizon")),
        "modelFamily": _cell(row.get("modelFamily")),
        "entryReference": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": _cell_float(row.get("tp2")),
        "expectedNetR": _cell_float(row.get("expectedNetR")),
        "signalStrength": _cell(row.get("signalStrength")),
        "probabilityPositive": _cell_float(row.get("probabilityPositive")),
        "decisionTimeMs": int(decision_ms) if decision_ms is not None else None,
        "recordedAt": _cell(row.get("recordedAt")),
        "orderId": _cell(row.get("orderId")),
        "executionStatus": _cell(row.get("executionStatus")),
        "executionReason": _cell(detail.get("reason")),
        "approvalVolume": _cell_float(detail.get("approval_volume")),
        "pipeline": _execution_pipeline(
            row.get("executionDetail"), row.get("executionStatus")
        ),
        "rr1": rr1,
        "brokerFill": _execution_detail_extract(row.get("executionDetail")),
    }


def api_ase_executed_trades():
    """Read-only view of journalled ASE executions for manual mirroring."""
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except (TypeError, ValueError):
        limit = 50
    status = str(request.args.get("status", "filled")).strip().lower() or "filled"
    since_ms_raw = request.args.get("sinceMs")
    try:
        since_ms = int(since_ms_raw) if since_ms_raw not in (None, "") else None
    except (TypeError, ValueError):
        since_ms = None

    try:
        df = load_trade_journal()
        if df.empty or "executionStatus" not in df.columns:
            return jsonify({"success": True, "trades": [], "totalExecuted": 0})
        executed: list[dict[str, Any]] = []
        for record in df.to_dict(orient="records"):
            row_status = _cell(record.get("executionStatus"))
            if row_status is None:
                continue
            if status != "all" and str(row_status).lower() != status:
                continue
            decision_ms = _cell(record.get("decisionTimeMs"))
            if since_ms is not None and (
                decision_ms is None or int(decision_ms) <= since_ms
            ):
                continue
            executed.append(record)
        executed.sort(
            key=lambda r: int(_cell(r.get("decisionTimeMs")) or 0), reverse=True
        )
        trades = [_executed_trade_row(row) for row in executed[:limit]]
        return jsonify(
            {"success": True, "trades": trades, "totalExecuted": len(executed)}
        )
    except Exception as exc:
        log.exception("ASE executed-trades query failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def api_ase_health():
    horizon: Horizon = (
        "swing" if str(request.args.get("horizon", "intraday")).lower() == "swing" else "intraday"
    )
    ptis_root = str(request.args.get("ptisRoot") or "").strip() or None
    try:
        health = ase_health(ptis_root=ptis_root, horizon=horizon)
        health["deployment"] = "OPERATIONAL"
        health["modelProvenance"] = _model_provenance()
        return jsonify(_json_safe(health))
    except Exception as exc:
        log.exception("ASE health check failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def register_ase_routes(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/ase-scan" not in rules:
        app.add_url_rule("/api/ase-scan", "api_ase_scan", api_ase_scan, methods=["POST"])
    if "/api/ase-execute" not in rules:
        app.add_url_rule(
            "/api/ase-execute",
            "api_ase_execute",
            api_ase_execute,
            methods=["POST"],
        )
    if "/api/ase-journal-summary" not in rules:
        app.add_url_rule(
            "/api/ase-journal-summary",
            "api_ase_journal_summary",
            api_ase_journal_summary,
            methods=["GET"],
        )
    if "/api/ase-shadow-summary" not in rules:
        app.add_url_rule(
            "/api/ase-shadow-summary",
            "api_ase_shadow_summary",
            api_ase_journal_summary,
            methods=["GET"],
        )
    if "/api/ase-executed-trades" not in rules:
        app.add_url_rule(
            "/api/ase-executed-trades",
            "api_ase_executed_trades",
            api_ase_executed_trades,
            methods=["GET"],
        )
    if "/api/ase-health" not in rules:
        app.add_url_rule("/api/ase-health", "api_ase_health", api_ase_health, methods=["GET"])
