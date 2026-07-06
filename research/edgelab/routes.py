"""
Athena EdgeLab — Flask API routes (research-only).

Register with: register_edgelab_routes(app)

Safety: read-only research data + sqlite status updates only.
No live execution, no order placement, no production config writes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import jsonify, request

from research.edgelab.config_loader import load_config
from research.edgelab.database import (
    connect,
    init_db,
    insert_promotion_review,
    list_suggestions,
    update_suggestion_status,
)
from research.edgelab.export_suggestions import export_suggestions
from research.edgelab.paths import DB_PATH, OUT_DIR

log = logging.getLogger(__name__)


def register_edgelab_routes(app) -> None:
    init_db(DB_PATH)

    @app.route("/api/edgelab/suggestions", methods=["GET"])
    def api_edgelab_suggestions():
        engine = request.args.get("engine")
        symbol = request.args.get("symbol")
        status = request.args.get("status")
        limit = min(int(request.args.get("limit", 100)), 500)
        with connect(DB_PATH) as conn:
            rows = list_suggestions(conn, limit=limit, engine=engine, symbol=symbol, status=status)
        return jsonify({"research_only": True, "suggestions": rows})

    @app.route("/api/edgelab/freshness", methods=["GET"])
    def api_edgelab_freshness():
        with connect(DB_PATH) as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM data_freshness ORDER BY timestamp DESC LIMIT 100"
                ).fetchall()
            ]
        return jsonify({"research_only": True, "freshness": rows})

    @app.route("/api/edgelab/export", methods=["GET"])
    def api_edgelab_export():
        path = export_suggestions(db_path=DB_PATH)
        return jsonify({"research_only": True, "path": str(path), "url": "/api/edgelab/suggestions"})

    @app.route("/api/edgelab/suggestions/<suggestion_id>/review", methods=["POST"])
    def api_edgelab_review(suggestion_id: str):
        payload = request.get_json(silent=True) or {}
        action = str(payload.get("action") or "").strip().lower()
        notes = str(payload.get("notes") or "")
        reviewer = str(payload.get("reviewer") or "ui")

        status_map = {
            "mark_reviewed": "reviewed",
            "reviewed": "reviewed",
            "reject": "rejected",
            "promote": "promoted_to_review",
        }
        new_status = status_map.get(action)
        if not new_status:
            return jsonify({"error": "invalid action; use mark_reviewed|reject|promote"}), 400

        with connect(DB_PATH) as conn:
            ok = update_suggestion_status(conn, suggestion_id, new_status)
            if not ok:
                return jsonify({"error": "suggestion not found"}), 404
            insert_promotion_review(
                conn,
                {
                    "suggestion_id": suggestion_id,
                    "action": action,
                    "reviewer": reviewer,
                    "notes": notes,
                },
            )
            conn.commit()
        export_suggestions(db_path=DB_PATH)
        return jsonify({"ok": True, "id": suggestion_id, "status": new_status, "research_only": True})

    @app.route("/api/edgelab/status", methods=["GET"])
    def api_edgelab_status():
        cfg = load_config()
        out_file = OUT_DIR / "suggestions.json"
        return jsonify(
            {
                "research_only": True,
                "enabled": cfg.get("enabled", True),
                "enabled_engines": cfg.get("enabled_engines"),
                "candidate_generation_mode": cfg.get("candidate_generation_mode"),
                "export_file": str(out_file),
                "disclaimer": "EdgeLab never modifies live trading or execution paths.",
            }
        )

    log.info("[edgelab] routes registered (research-only)")
