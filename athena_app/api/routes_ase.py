"""Flask routes for ASE shadow scan (demo/paper only — no Engine C)."""

from __future__ import annotations

import logging
from typing import Any

from flask import jsonify, request

from athena_ase.horizon import Horizon
from athena_ase.registry.promotion import summary as promotion_summary
from athena_ase.runtime.scan import run_ase_scan
from athena_ase.shadow.journal import shadow_summary

log = logging.getLogger("sentinel.ase")


def _json_safe(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def api_ase_scan():
    d = request.get_json(force=True, silent=True) or {}
    horizon: Horizon = "swing" if str(d.get("horizon", "intraday")).lower() == "swing" else "intraday"
    family = str(d.get("family") or d.get("assetClass") or "").strip().lower() or None
    symbols = d.get("symbols") or d.get("pairs")
    sym_list = [str(s) for s in symbols] if isinstance(symbols, list) else None
    write_journal = bool(d.get("writeJournal", True))
    try:
        result = run_ase_scan(
            family=family,
            horizon=horizon,
            symbols=sym_list,
            write_journal=write_journal,
            ptis_root=str(d.get("ptisRoot") or "") or None,
        )
        return jsonify(_json_safe(result))
    except Exception as exc:
        log.exception("ASE scan failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def api_ase_shadow_summary():
    try:
        promo = promotion_summary()
        journal = shadow_summary()
        return jsonify(
            {
                "success": True,
                "deployment": promo,
                "journal": journal,
            }
        )
    except Exception as exc:
        log.exception("ASE shadow summary failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def register_ase_routes(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    if "/api/ase-scan" not in rules:
        app.add_url_rule("/api/ase-scan", "api_ase_scan", api_ase_scan, methods=["POST"])
    if "/api/ase-shadow-summary" not in rules:
        app.add_url_rule(
            "/api/ase-shadow-summary",
            "api_ase_shadow_summary",
            api_ase_shadow_summary,
            methods=["GET"],
        )
