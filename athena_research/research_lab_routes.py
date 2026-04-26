"""
Athena Research Lab — Flask API Routes
Register with: register_research_lab_routes(app)

Safety: routes are read-only or trigger isolated research runs.
No live execution imports. No production config writes.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_OUTPUT = Path("logs/research_lab")
_active_runs: dict[str, dict] = {}  # run_id → {status, thread, result}


def register_research_lab_routes(app) -> None:
    """Register all /api/research-lab/* routes on the Flask app."""
    from flask import jsonify, request, send_file, abort

    # ── POST /api/research-lab/run ────────────────────────────────────────────
    @app.route("/api/research-lab/run", methods=["POST"])
    def api_research_lab_run():
        """Start a new research run (async, returns run_id immediately)."""
        body = request.get_json(silent=True) or {}
        mode = body.get("mode", "tiny")
        direction = body.get("direction", "both")
        families = body.get("families", None)
        symbols = body.get("symbols", None)
        run_ai = bool(body.get("run_ai_review", False))

        from athena_research.run_manager import run_research, _DEFAULT_CONFIG
        from datetime import datetime, timezone

        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        def _worker():
            _active_runs[run_id]["status"] = "running"
            try:
                result = run_research(
                    mode=mode,
                    config_path=_DEFAULT_CONFIG,
                    output_dir=_DEFAULT_OUTPUT,
                    run_id=run_id,
                    direction=direction,
                    run_ai_review=run_ai,
                )
                _active_runs[run_id]["status"] = "complete"
                _active_runs[run_id]["result"] = result
            except Exception as e:
                log.error("[research_lab_routes] Run %s failed: %s", run_id, e)
                _active_runs[run_id]["status"] = "failed"
                _active_runs[run_id]["error"] = str(e)

        _active_runs[run_id] = {"status": "queued", "run_id": run_id}
        t = threading.Thread(target=_worker, daemon=True, name=f"research-{run_id}")
        _active_runs[run_id]["thread"] = t
        t.start()

        return jsonify({"run_id": run_id, "status": "queued", "mode": mode}), 202

    # ── GET /api/research-lab/runs ────────────────────────────────────────────
    @app.route("/api/research-lab/runs", methods=["GET"])
    def api_research_lab_list():
        """List all completed and active research runs."""
        from athena_research.run_manager import list_runs
        try:
            runs = list_runs(_DEFAULT_OUTPUT)
        except Exception as e:
            runs = []
        # Merge with active in-memory runs
        active_ids = {r["run_id"] for r in runs}
        for rid, info in _active_runs.items():
            if rid not in active_ids:
                runs.insert(0, {
                    "run_id": rid,
                    "status": info.get("status", "unknown"),
                })
        return jsonify({"runs": runs})

    # ── GET /api/research-lab/run/<run_id> ────────────────────────────────────
    @app.route("/api/research-lab/run/<run_id>", methods=["GET"])
    def api_research_lab_status(run_id: str):
        """Get status and summary for a run."""
        from athena_research.run_manager import get_run_results

        # Check active runs first
        if run_id in _active_runs:
            info = _active_runs[run_id]
            status = info.get("status", "unknown")
            if status == "complete":
                result = info.get("result", {})
                # Strip non-serialisable keys (thread object lives in info, not result)
                safe = {k: v for k, v in result.items()
                        if k not in ("thread",) and not callable(v)}
                return jsonify({"run_id": run_id, "status": status, **safe})
            return jsonify({"run_id": run_id, "status": status,
                            "error": info.get("error", "")})

        # Check completed runs on disk
        run_dir = _DEFAULT_OUTPUT / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found", "run_id": run_id}), 404

        df = get_run_results(run_id, _DEFAULT_OUTPUT)
        if df is None:
            return jsonify({"run_id": run_id, "status": "failed",
                            "error": "Run directory exists but results CSV missing — run may have crashed"})

        # Read sentinel if available
        status_path = run_dir / "status.json"
        files_ok = True
        report_errors: list = []
        if status_path.exists():
            try:
                sd = json.loads(status_path.read_text(encoding="utf-8"))
                files_ok = sd.get("files_ok", True)
                report_errors = sd.get("errors", [])
            except Exception:
                pass

        summary = {
            "total": int(len(df)),
            "strong": int((df["status"] == "STRONG_CANDIDATE").sum()) if "status" in df.columns else 0,
            "weak": int((df["status"] == "WEAK_CANDIDATE").sum()) if "status" in df.columns else 0,
            "reject": int((df["status"] == "REJECT").sum()) if "status" in df.columns else 0,
            "files_ok": files_ok,
            "report_errors": report_errors,
        }
        return jsonify({"run_id": run_id, "status": "complete", "summary": summary})

    # ── POST /api/research-lab/analyze/<run_id> ───────────────────────────────
    @app.route("/api/research-lab/analyze/<run_id>", methods=["POST"])
    def api_research_lab_analyze(run_id: str):
        """Run AI analyst on an existing research run."""
        from athena_research.ai_analyst import run_ai_analysis

        run_dir = _DEFAULT_OUTPUT / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found", "run_id": run_id}), 404

        body = request.get_json(silent=True) or {}
        try:
            result = run_ai_analysis(
                run_dir=run_dir,
                provider=body.get("provider", "auto"),
                model=body.get("model"),
                max_tokens=int(body.get("max_tokens", 4000)),
                temperature=float(body.get("temperature", 0.2)),
            )
            return jsonify(result)
        except Exception as e:
            log.error("[research_lab_routes] AI analysis failed for %s: %s", run_id, e)
            return jsonify({"error": str(e)}), 500

    # ── GET /api/research-lab/ai-review/<run_id> ──────────────────────────────
    @app.route("/api/research-lab/ai-review/<run_id>", methods=["GET"])
    def api_research_lab_ai_review(run_id: str):
        """Return saved AI review for a run."""
        from athena_research.ai_analyst import load_ai_review

        run_dir = _DEFAULT_OUTPUT / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found", "run_id": run_id}), 404

        data = load_ai_review(run_dir)
        if not data:
            return jsonify({"error": "No AI review found. Run /analyze first."}), 404
        return jsonify({"run_id": run_id, **data})

    # ── GET /api/research-lab/download/<run_id>/<filename> ───────────────────
    @app.route("/api/research-lab/download/<run_id>/<path:filename>", methods=["GET"])
    def api_research_lab_download(run_id: str, filename: str):
        """Download a specific output file from a run."""
        # Sanitise — only allow known file names, no directory traversal
        allowed = {
            "research_summary.csv", "ranked_strategies.csv", "by_asset_group.csv",
            "by_symbol.csv", "by_timeframe.csv", "by_session.csv", "by_direction.csv",
            "indicator_attribution.csv", "rejected_or_failed_configs.csv",
            "research_report.md", "ai_research_review.md", "ai_action_plan.json",
            "ai_engine_recommendations.json", "run_meta.json",
        }
        if filename not in allowed:
            return jsonify({"error": "File not allowed", "filename": filename}), 403

        file_path = _DEFAULT_OUTPUT / run_id / filename
        if not file_path.exists():
            return jsonify({"error": "File not found", "filename": filename}), 404

        return send_file(str(file_path), as_attachment=True, download_name=filename)

    # ── GET /api/research-lab/ranked/<run_id> ────────────────────────────────
    @app.route("/api/research-lab/ranked/<run_id>", methods=["GET"])
    def api_research_lab_ranked(run_id: str):
        """Return ranked strategies JSON for dashboard display."""
        import pandas as pd

        run_dir = _DEFAULT_OUTPUT / run_id
        ranked_path = run_dir / "ranked_strategies.csv"

        # Run dir exists but file missing → run failed before writing; return empty
        if not ranked_path.exists():
            if not run_dir.exists():
                return jsonify({"error": "Run not found", "run_id": run_id}), 404
            return jsonify({"run_id": run_id, "ranked": [], "note": "no ranked results"})

        try:
            df = pd.read_csv(ranked_path).head(50)
            # Use pandas JSON serialiser which handles numpy types correctly
            records = json.loads(df.fillna("").to_json(orient="records"))
            return jsonify({"run_id": run_id, "ranked": records})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    log.info("[research_lab_routes] Research Lab routes registered")
