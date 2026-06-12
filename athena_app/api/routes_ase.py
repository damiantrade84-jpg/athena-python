"""Flask routes for ASE operational scan + journal (demo/paper only)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from flask import jsonify, request

from athena_ase.execution.journal import trade_journal_summary
from athena_ase.horizon import Horizon
from athena_ase.runtime.health import ase_health
from athena_ase.runtime.scan import run_ase_dual_horizon_scan, run_ase_scan
from athena_research.ase.training_report import write_training_report

log = logging.getLogger("sentinel.ase")


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


def api_ase_scan():
    d = request.get_json(force=True, silent=True) or {}
    horizon_raw = str(d.get("horizon", "both")).lower()
    family = str(d.get("family") or d.get("assetClass") or "").strip().lower() or None
    symbols = d.get("symbols") or d.get("pairs")
    sym_list = [str(s) for s in symbols] if isinstance(symbols, list) else None
    write_journal = bool(d.get("writeJournal", True))
    execute_trades = bool(d.get("executeTrades", False))
    try:
        if horizon_raw == "both":
            result = run_ase_dual_horizon_scan(
                family=family,
                symbols=sym_list,
                write_journal=write_journal,
                execute_trades=execute_trades,
                ptis_root=str(d.get("ptisRoot") or "") or None,
            )
        else:
            horizon: Horizon = "swing" if horizon_raw == "swing" else "intraday"
            result = run_ase_scan(
                family=family,
                horizon=horizon,
                symbols=sym_list,
                write_journal=write_journal,
                execute_trades=execute_trades,
                ptis_root=str(d.get("ptisRoot") or "") or None,
            )
        return jsonify(_json_safe(result))
    except Exception as exc:
        log.exception("ASE scan failed")
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


def api_ase_health():
    horizon: Horizon = (
        "swing" if str(request.args.get("horizon", "intraday")).lower() == "swing" else "intraday"
    )
    ptis_root = str(request.args.get("ptisRoot") or "").strip() or None
    try:
        health = ase_health(ptis_root=ptis_root, horizon=horizon)
        health["deployment"] = "OPERATIONAL"
        return jsonify(_json_safe(health))
    except Exception as exc:
        log.exception("ASE health check failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def register_ase_routes(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/ase-scan" not in rules:
        app.add_url_rule("/api/ase-scan", "api_ase_scan", api_ase_scan, methods=["POST"])
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
    if "/api/ase-health" not in rules:
        app.add_url_rule("/api/ase-health", "api_ase_health", api_ase_health, methods=["GET"])
