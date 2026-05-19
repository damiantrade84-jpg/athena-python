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
_autopilot_plan_lock = threading.Lock()
_AUTOPILOT_IDEMPOTENCY_TTL_SECONDS = 60 * 60
_running_autopilot_plans: dict[str, dict] = {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_dt(raw: object) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = str(raw)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _validation_run_count_for_run(run_dir: Path, sessions_dir: Path | None = None) -> int:
    """Count validation child runs linked to this run via session metadata."""
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        return 0
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    session_id = meta.get("session_id")
    if not session_id:
        return 0

    from athena_research.autopilot_session import _SESSIONS_DIR

    sd = sessions_dir or _SESSIONS_DIR
    sess_path = sd / str(session_id) / "session.json"
    if not sess_path.exists():
        return 0
    try:
        data = json.loads(sess_path.read_text(encoding="utf-8"))
        return len(data.get("validation_run_ids", []) or [])
    except Exception:
        return 0


def _run_dir_has_summary(run_dir: Path) -> bool:
    return (run_dir / "research_summary.csv").exists()


def _quick_summary_counts(run_dir: Path, meta_data: dict | None = None) -> dict:
    """Fast summary for UI while reports finish or for large CSVs."""
    meta_data = meta_data or {}
    status_path = run_dir / "status.json"
    if status_path.exists():
        try:
            sd = json.loads(status_path.read_text(encoding="utf-8"))
            rc = int(sd.get("results_count") or 0)
            if rc > 0:
                return {
                    "total": rc,
                    "strong": None,
                    "weak": None,
                    "reject": None,
                    "files_ok": sd.get("files_ok", True),
                    "report_errors": sd.get("errors", []),
                }
        except Exception:
            pass

    summary_path = run_dir / "research_summary.csv"
    if not summary_path.exists():
        return {
            "total": int(meta_data.get("results_count") or 0),
            "strong": 0,
            "weak": 0,
            "reject": 0,
            "files_ok": False,
            "report_errors": [],
        }

    try:
        import pandas as pd
        df = pd.read_csv(summary_path, usecols=lambda c: c in {"status"})
        total = len(df)
        if "status" in df.columns:
            return {
                "total": total,
                "strong": int((df["status"] == "STRONG_CANDIDATE").sum()),
                "weak": int((df["status"] == "WEAK_CANDIDATE").sum()),
                "reject": int((df["status"] == "REJECT").sum()),
                "files_ok": True,
                "report_errors": [],
            }
    except Exception:
        pass

    try:
        total = max(0, sum(1 for _ in open(summary_path, encoding="utf-8")) - 1)
    except Exception:
        total = int(meta_data.get("results_count") or 0)
    return {
        "total": total,
        "strong": None,
        "weak": None,
        "reject": None,
        "files_ok": True,
        "report_errors": [],
    }


def _merge_run_meta_tags(output_dir: Path, run_id: str, tags: dict) -> None:
    """Merge non-empty relationship tags into a run_meta.json file."""
    meta_path = output_dir / run_id / "run_meta.json"
    if not meta_path.exists():
        return
    try:
        mdata = json.loads(meta_path.read_text(encoding="utf-8"))
        for key, value in tags.items():
            if value is None or value == "":
                continue
            mdata[key] = value
        meta_path.write_text(json.dumps(mdata, indent=2), encoding="utf-8")
    except Exception:
        log.debug("[research_lab_routes] failed to merge run_meta tags for %s", run_id, exc_info=True)


def _cleanup_autopilot_plan_states(now: Optional[datetime] = None) -> None:
    """Drop completed/failed idempotency records after their replay window."""
    now = now or _now_utc()
    stale_keys: list[str] = []
    for key, state in list(_running_autopilot_plans.items()):
        if not isinstance(state, dict):
            continue
        if state.get("status") == "running":
            continue
        updated_at = _parse_iso_dt(state.get("updated_at") or state.get("completed_at") or state.get("failed_at"))
        if updated_at is None or (now - updated_at).total_seconds() > _AUTOPILOT_IDEMPOTENCY_TTL_SECONDS:
            stale_keys.append(key)
    for key in stale_keys:
        _running_autopilot_plans.pop(key, None)


def _mark_autopilot_plan_child_done(
    idempotency_key: str,
    child_run_id: str,
    *,
    success: bool,
    error: Optional[str] = None,
) -> None:
    now_iso = _now_utc().isoformat()
    with _autopilot_plan_lock:
        state = _running_autopilot_plans.get(idempotency_key)
        if not isinstance(state, dict):
            return
        done_key = "completed_child_run_ids" if success else "failed_child_run_ids"
        state.setdefault(done_key, [])
        if child_run_id not in state[done_key]:
            state[done_key].append(child_run_id)
        if error:
            state.setdefault("errors", []).append({"run_id": child_run_id, "error": error})
        state["updated_at"] = now_iso
        expected = len(state.get("child_run_ids", []))
        done_count = len(set(state.get("completed_child_run_ids", []) + state.get("failed_child_run_ids", [])))
        if expected and done_count >= expected:
            state["status"] = "failed" if state.get("failed_child_run_ids") else "completed"
            finish_key = "failed_at" if state["status"] == "failed" else "completed_at"
            state[finish_key] = now_iso



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

        timeframes = body.get("timeframes", None)
        strategies = body.get("strategies", None)
        params = body.get("params", None)
        directions = body.get("directions", None)
        max_combinations = body.get("max_combinations", None)
        run_meta_tags = {
            "parent_run_id": body.get("parent_run_id"),
            "session_id": body.get("session_id"),
            "role": body.get("role"),
            "market_group": body.get("market_group"),
            "trading_style": body.get("trading_style"),
            "research_depth": body.get("research_depth"),
        }

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
                    symbols=symbols,
                    timeframes=timeframes,
                    families=families,
                    strategies=strategies,
                    params=params,
                    directions=directions,
                    max_combinations=max_combinations,
                    market_group=body.get("market_group"),
                    trading_style=body.get("trading_style"),
                )
                _merge_run_meta_tags(_DEFAULT_OUTPUT, run_id, run_meta_tags)
                _active_runs[run_id]["status"] = "complete"

                _active_runs[run_id]["result"] = result
            except Exception as e:
                import traceback as _tb
                full_tb = _tb.format_exc()
                log.error("[research_lab_routes] Run %s failed: %s\n%s",
                          run_id, e, full_tb)
                _active_runs[run_id]["status"] = "failed"
                _active_runs[run_id]["error"] = str(e)
                _active_runs[run_id]["traceback"] = full_tb
                # Write traceback to disk so it survives server restarts
                try:
                    tb_path = _DEFAULT_OUTPUT / run_id / "error_traceback.txt"
                    tb_path.parent.mkdir(parents=True, exist_ok=True)
                    tb_path.write_text(full_tb, encoding="utf-8")
                except Exception:
                    pass

        _active_runs[run_id] = {"status": "queued", "run_id": run_id}
        t = threading.Thread(target=_worker, daemon=True, name=f"research-{run_id}")
        _active_runs[run_id]["thread"] = t
        t.start()

        return jsonify({"run_id": run_id, "status": "queued", "mode": mode}), 202

    # ── POST /api/research-lab/auto-plan ──────────────────────────────────────
    @app.route("/api/research-lab/auto-plan", methods=["POST"])
    def api_research_lab_auto_plan():
        """Create automated recommendation plan."""
        body = request.get_json(silent=True) or {}
        run_id = body.get("run_id")
        planner_mode = body.get("planner_mode", "balanced")
        max_tests = int(body.get("max_tests", 5))
        max_comb = int(body.get("max_combinations_per_test", 300))
        
        if not run_id:
            return jsonify({"error": "run_id is required"}), 400
            
        from athena_research.autopilot import generate_auto_plan
        try:
            plan = generate_auto_plan(run_id, _DEFAULT_OUTPUT, planner_mode, max_tests, max_comb)
            return jsonify(plan)
        except Exception as e:
            log.error("[research_lab_routes] Auto plan failed: %s", e, exc_info=True)
            return jsonify({"error": str(e)}), 500

    # ── POST /api/research-lab/retest-plan ───────────────────────────────────
    @app.route("/api/research-lab/retest-plan", methods=["POST"])
    @app.route("/api/research-lab/retest-add-plan", methods=["POST"])
    def api_research_lab_retest_add_plan():
        """Build focused validation tests from selected recommendation rows."""
        import pandas as pd
        import uuid

        body = request.get_json(silent=True) or {}
        run_id = body.get("run_id")
        max_tests = int(body.get("max_tests", 12))
        selected_candidates = body.get("candidates") or []
        raw_recs = body.get("recommendations") or body.get("recommendation") or ["ADD"]
        if isinstance(raw_recs, str):
            wanted_recs = {raw_recs.strip().upper()}
        else:
            wanted_recs = {str(r).strip().upper() for r in raw_recs if str(r).strip()}
        if not wanted_recs:
            wanted_recs = {"ADD"}
        if "SELECTED" in wanted_recs:
            wanted_recs.discard("SELECTED")
            if selected_candidates:
                wanted_recs.update({"ADD", "RETEST", "WATCHLIST_ONLY"})
            else:
                wanted_recs.add("ADD")
        rec_label = "+".join(sorted(wanted_recs))

        if not run_id:
            return jsonify({"error": "run_id is required"}), 400

        rec_path = _DEFAULT_OUTPUT / run_id / "add_remove_retest_recommendations.csv"
        if not rec_path.exists():
            return jsonify({"error": "No recommendations found for run", "run_id": run_id}), 404

        try:
            df = pd.read_csv(rec_path)
            rec_series = (
                df["recommendation"].astype(str).str.strip().str.upper()
                if "recommendation" in df.columns
                else pd.Series([""] * len(df), index=df.index, dtype=str)
            )
            plan_df = df[rec_series.isin(wanted_recs)].copy()
            if selected_candidates and not plan_df.empty:
                selected_mask = pd.Series(False, index=plan_df.index)
                for cand in selected_candidates:
                    if not isinstance(cand, dict):
                        continue
                    cand_mask = pd.Series(True, index=plan_df.index)
                    matched_any_key = False
                    for key in ("recommendation", "engine", "engine_component", "strategy_name", "symbol", "timeframe", "direction", "pair_group", "timeframe_zone", "structure_context"):
                        if key not in plan_df.columns or cand.get(key) in (None, ""):
                            continue
                        matched_any_key = True
                        left = plan_df[key].fillna("").astype(str).str.strip().str.upper()
                        right = str(cand.get(key)).strip().upper()
                        cand_mask &= left.eq(right)
                    if matched_any_key:
                        selected_mask |= cand_mask
                narrowed = plan_df[selected_mask].copy()
                if narrowed.empty:
                    log.warning(
                        "[research_lab_routes] Retest candidate narrowing matched 0 CSV rows; using recommendation filter only (run_id=%s)",
                        run_id,
                    )
                else:
                    plan_df = narrowed
            if plan_df.empty:
                return jsonify({
                    "plan_id": f"retest_{rec_label.lower().replace('+', '_')}_{uuid.uuid4().hex[:8]}",
                    "source_run_id": run_id,
                    "recommendations": sorted(wanted_recs),
                    "recommended": False,
                    "tests": [],
                    "message": f"No {rec_label} recommendations found",
                })

            tests = []
            group_cols = [c for c in ["recommendation", "engine", "market_group", "pair_group", "timeframe_zone", "strategy_name"] if c in plan_df.columns]
            for priority, (_, grp) in enumerate(plan_df.groupby(group_cols, dropna=False), start=1):
                if len(tests) >= max_tests:
                    break
                row = grp.sort_values(
                    [c for c in ["baseline_delta_oos", "robustness_score", "oos_return"] if c in grp.columns],
                    ascending=False,
                ).iloc[0]
                strategy = str(row.get("strategy_name", "")).strip()
                family = str(row.get("family", "")).strip()
                if not family:
                    try:
                        from athena_research.strategies import STRATEGY_REGISTRY
                        family = STRATEGY_REGISTRY.get(strategy, ("", None))[0] or ""
                    except Exception:
                        family = ""
                symbol = str(row.get("symbol", "")).strip()
                if not symbol and selected_candidates:
                    for cand in selected_candidates:
                        sym = str(cand.get("symbol") or "").strip()
                        if sym:
                            symbol = sym
                            break
                if not symbol:
                    mg = str(row.get("market_group", "") or "").strip().lower()
                    if mg:
                        try:
                            from athena_research.run_manager import load_config, get_group_symbols
                            syms = get_group_symbols(load_config(), mg)
                            if syms:
                                symbol = str(syms[0]).strip()
                        except Exception:
                            pass
                timeframe = str(row.get("timeframe", "")).strip()
                if not timeframe:
                    zone = str(row.get("timeframe_zone", "")).strip().lower()
                    timeframe = {"scalp": "M15", "intra": "H4", "swing": "D1"}.get(zone, "H4")
                if not (strategy and family and symbol and timeframe):
                    continue
                row_rec = str(row.get("recommendation", rec_label) or rec_label).strip().upper()
                row_rec_slug = row_rec.lower().replace("_", "-")
                tests.append({
                    "test_id": f"retest_{row_rec.lower()}_{strategy}_{priority}",
                    "selected_by_default": True,
                    "priority": priority,
                    "title": f"Retest {row_rec} {strategy} ({row.get('timeframe_zone', timeframe)})",
                    "purpose": f"Validate a {row_rec} recommendation against the same group and zone before promotion, removal, or live Engine A/B consideration.",
                    "test_type": f"{row_rec_slug}_candidate_validation",
                    "recommendation": row_rec,
                    "engine": row.get("engine"),
                    "engine_component": row.get("engine_component"),
                    "market_group": row.get("market_group"),
                    "pair_group": row.get("pair_group"),
                    "structure_context": row.get("structure_context"),
                    "families": [family],
                    "strategies": [strategy],
                    "symbols": [symbol],
                    "timeframes": [timeframe],
                    "directions": [str(row.get("direction", "both") or "both")],
                    "mode": "tiny",
                    "max_combinations": 80,
                    "reason": f"{row_rec} recommendation: {row.get('engine', '')} {row.get('engine_component', '')} / {row.get('pair_group', '')} / {row.get('timeframe_zone', '')}",
                    "acceptance_criteria": {
                        "min_trade_count": 20,
                        "min_profit_factor": 1.20,
                        "min_oos_return": 0.0,
                        "min_robustness": 0.50,
                    },
                })

            return jsonify({
                "plan_id": f"retest_{rec_label.lower().replace('+', '_')}_{uuid.uuid4().hex[:8]}",
                "source_run_id": run_id,
                "recommendations": sorted(wanted_recs),
                "recommended": bool(tests),
                "tests": tests,
            })
        except Exception as e:
            log.error("[research_lab_routes] Retest ADD plan failed: %s", e, exc_info=True)
            return jsonify({"error": str(e)}), 500

    # ── POST /api/research-lab/run-auto-plan ──────────────────────────────────
    @app.route("/api/research-lab/run-auto-plan", methods=["POST"])
    def api_research_lab_run_auto_plan():
        """Execute selected items from recommendations with idempotency protection."""
        import uuid
        body = request.get_json(silent=True) or {}
        plan_id = body.get("plan_id") or "default_plan"
        source_run_id = body.get("source_run_id")
        tests = body.get("tests", [])
        
        if not source_run_id:
            return jsonify({"error": "source_run_id is required"}), 400

        # Backend Idempotency
        idempotency_key = body.get("idempotency_key") or f"{source_run_id}_{plan_id}"
        from athena_research.run_manager import run_research, _DEFAULT_CONFIG

        with _autopilot_plan_lock:
            _cleanup_autopilot_plan_states()
            existing = _running_autopilot_plans.get(idempotency_key)
            if isinstance(existing, list):
                existing = {
                    "status": "running",
                    "plan_id": plan_id,
                    "parent_run_id": source_run_id,
                    "child_run_ids": list(existing),
                    "completed_child_run_ids": [],
                    "failed_child_run_ids": [],
                    "errors": [],
                    "started_at": _now_utc().isoformat(),
                    "updated_at": _now_utc().isoformat(),
                }
                _running_autopilot_plans[idempotency_key] = existing
            if isinstance(existing, dict) and existing.get("status") == "running":
                log.info("[research_lab_routes] Duplicate auto plan request intercepted for %s", idempotency_key)
                return jsonify({
                    "status": "started",
                    "idempotency_status": "running",
                    "plan_id": plan_id,
                    "parent_run_id": source_run_id,
                    "child_run_ids": existing.get("child_run_ids", []),
                    "message": "Autopilot validation already in progress"
                }), 200

            child_run_ids = []
            child_specs = []
            for t_spec in tests:
                from datetime import datetime, timezone
                child_run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
                child_run_ids.append(child_run_id)
                child_specs.append((child_run_id, t_spec))

            now_iso = _now_utc().isoformat()
            _running_autopilot_plans[idempotency_key] = {
                "status": "running" if child_run_ids else "completed",
                "plan_id": plan_id,
                "parent_run_id": source_run_id,
                "child_run_ids": child_run_ids,
                "completed_child_run_ids": [],
                "failed_child_run_ids": [],
                "errors": [],
                "started_at": now_iso,
                "updated_at": now_iso,
                "completed_at": now_iso if not child_run_ids else None,
            }

        for child_run_id, t_spec in child_specs:
            mode = t_spec.get("mode", "small")
            direction = t_spec.get("directions", ["both"])[0] if t_spec.get("directions") else "both"
            symbols = t_spec.get("symbols")
            timeframes = t_spec.get("timeframes")
            families = t_spec.get("families")
            strategies = t_spec.get("strategies")
            params = t_spec.get("params")
            max_combinations = t_spec.get("max_combinations")
            
            def _worker_child(cid, m, d, s, tf, f, strats, p, max_comb):
                _active_runs[cid] = {"status": "running", "run_id": cid}
                try:
                    import json
                    run_research(
                        mode=m,
                        config_path=_DEFAULT_CONFIG,
                        output_dir=_DEFAULT_OUTPUT,
                        run_id=cid,
                        direction=d,
                        run_ai_review=False,
                        symbols=s,
                        timeframes=tf,
                        families=f,
                        strategies=strats,
                        params=p,
                        max_combinations=max_comb,
                    )
                    
                    # Update metadata on disk to link parent safely
                    meta_path = _DEFAULT_OUTPUT / cid / "run_meta.json"
                    if meta_path.exists():
                        try:
                            mdata = json.loads(meta_path.read_text(encoding="utf-8"))
                            mdata["parent_run_id"] = source_run_id
                            mdata["is_autopilot"] = True
                            meta_path.write_text(json.dumps(mdata, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                            
                    _active_runs[cid]["status"] = "complete"
                    _mark_autopilot_plan_child_done(idempotency_key, cid, success=True)
                except Exception as ex:
                    _active_runs[cid]["status"] = "failed"
                    _active_runs[cid]["error"] = str(ex)
                    _mark_autopilot_plan_child_done(idempotency_key, cid, success=False, error=str(ex))
                    
            _active_runs[child_run_id] = {"status": "queued", "run_id": child_run_id}
            t = threading.Thread(
                target=_worker_child,
                args=(child_run_id, mode, direction, symbols, timeframes, families, strategies, params, max_combinations),
                daemon=True,
            )
            _active_runs[child_run_id]["thread"] = t
            t.start()
            
        return jsonify({
            "status": "started",
            "idempotency_status": "running" if child_run_ids else "completed",
            "plan_id": plan_id,
            "parent_run_id": source_run_id,
            "child_run_ids": child_run_ids,
            "message": "Autopilot validation started"
        }), 202
    # ── POST /api/research-lab/style-run ──────────────────────────────────────
    @app.route("/api/research-lab/style-run", methods=["POST"])
    def api_research_lab_style_run():
        """Launch discovery for a market group and trading style profile."""
        body = request.get_json(silent=True) or {}
        market_group = body.get("market_group", "crypto").lower()
        trading_style = body.get("trading_style", "intra").lower()
        research_depth = body.get("research_depth", "standard").lower()
        session_id = body.get("session_id")
        parent_run_id = body.get("parent_run_id")

        # Resolve mode
        if research_depth == "quick":
            mode = "tiny"
        elif research_depth == "deep":
            mode = "large"
        else:
            mode = "medium"

        # Resolve symbols from YAML config (single source of truth)
        from athena_research.run_manager import load_config, get_group_symbols
        _cfg = load_config()
        symbols = get_group_symbols(_cfg, market_group)
        if not symbols:
            symbols = ["BTC/USDT"]

        # Resolve profiles
        from athena_research.autopilot import RESEARCH_STYLE_PROFILES
        if trading_style not in RESEARCH_STYLE_PROFILES:
            return jsonify({"error": f"Invalid trading style: {trading_style}"}), 400

        profile = RESEARCH_STYLE_PROFILES[trading_style]
        timeframes = profile["timeframes"]
        families = profile["strategy_families"]

        from athena_research.run_manager import run_research, _DEFAULT_CONFIG
        
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        def _worker_style():
            _active_runs[run_id]["status"] = "running"
            try:
                result = run_research(
                    mode=mode,
                    config_path=_DEFAULT_CONFIG,
                    output_dir=_DEFAULT_OUTPUT,
                    run_id=run_id,
                    direction="both",
                    run_ai_review=False,
                    symbols=symbols,
                    timeframes=timeframes,
                    families=families
                )
                
                _merge_run_meta_tags(_DEFAULT_OUTPUT, run_id, {
                    "market_group": market_group,
                    "trading_style": trading_style,
                    "research_depth": research_depth,
                    "zone_set": profile.get("zone_set"),
                    "validation_focus": profile.get("validation_focus"),
                    "session_id": session_id,
                    "parent_run_id": parent_run_id,
                    "role": body.get("role") or "style_run",
                })

                _active_runs[run_id]["status"] = "complete"
                _active_runs[run_id]["result"] = result
            except Exception as e:
                import traceback as _tb
                full_tb = _tb.format_exc()
                log.error("[research_lab_routes] Style run %s failed: %s\n%s", run_id, e, full_tb)
                _active_runs[run_id]["status"] = "failed"
                _active_runs[run_id]["error"] = str(e)
                try:
                    tb_path = _DEFAULT_OUTPUT / run_id / "error_traceback.txt"
                    tb_path.parent.mkdir(parents=True, exist_ok=True)
                    tb_path.write_text(full_tb, encoding="utf-8")
                except Exception:
                    pass

        _active_runs[run_id] = {"status": "queued", "run_id": run_id}
        t = threading.Thread(target=_worker_style, daemon=True, name=f"style-research-{run_id}")
        _active_runs[run_id]["thread"] = t
        t.start()

        return jsonify({"run_id": run_id, "status": "queued", "mode": mode}), 202


    # ── GET /api/research-lab/run-status ──────────────────────────────────────
    @app.route("/api/research-lab/run-status", methods=["GET"])
    def api_research_lab_run_status_query():
        """Polling endpoint for single child run updates."""
        run_id = request.args.get("run_id")
        if not run_id:
            return jsonify({"error": "run_id is required"}), 400
            
        if run_id in _active_runs:
            return jsonify({"run_id": run_id, "status": _active_runs[run_id].get("status", "unknown")})
            
        run_dir = _DEFAULT_OUTPUT / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found", "run_id": run_id}), 404
            
        import json
        status_path = run_dir / "status.json"
        if status_path.exists():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                return jsonify({"run_id": run_id, "status": data.get("status", "complete")})
            except Exception:
                pass
                
        return jsonify({"run_id": run_id, "status": "complete"})


    # ── GET /api/research-lab/autopilot-result ────────────────────────────────
    @app.route("/api/research-lab/autopilot-result", methods=["GET"])
    def api_research_lab_autopilot_result():
        """Retrieve aggregated validation reports for child tests."""
        parent_id = request.args.get("parent_run_id")
        if not parent_id:
            return jsonify({"error": "parent_run_id is required"}), 400
            
        from athena_research.run_manager import list_runs
        import pandas as pd
        import json
        
        try:
            runs = list_runs(_DEFAULT_OUTPUT)
        except Exception as e:
            return jsonify({"error": f"Failed listing runs: {str(e)}"}), 500
            
        child_runs_data = []
        tests_completed = 0
        confirmed = 0
        weakened = 0
        rejected = 0
        needs_more_data = 0
        
        for r in runs:
            if r.get("parent_run_id") == parent_id:
                rid = r["run_id"]
                r_dir = _DEFAULT_OUTPUT / rid
                
                ranked_strategies = []
                summary = {}
                
                import math
                def _clean_nans(obj):
                    if isinstance(obj, dict):
                        return {k: _clean_nans(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [_clean_nans(v) for v in obj]
                    elif isinstance(obj, float):
                        if math.isnan(obj) or math.isinf(obj):
                            return None
                    return obj

                ranked_csv = r_dir / "ranked_strategies.csv"
                if ranked_csv.exists():
                    try:
                        df = pd.read_csv(ranked_csv)
                        ranked_strategies = _clean_nans(df.to_dict(orient="records"))
                    except Exception:
                        pass
                        
                summary_csv = r_dir / "research_summary.csv"
                if summary_csv.exists():
                    try:
                        df_sum = pd.read_csv(summary_csv)
                        sum_recs = _clean_nans(df_sum.to_dict(orient="records"))
                        if sum_recs:
                            summary = sum_recs[0]
                    except Exception:
                        pass
                        
                status_list = [str(st).upper() for st in [item.get("status") for item in ranked_strategies] if st]
                
                classification = "NEEDS_MORE_DATA"
                reason = "Insufficient tests or failed setup."
                
                if "STRONG_CANDIDATE" in status_list:
                    classification = "CONFIRMED"
                    confirmed += 1
                    reason = "Strong candidate identified during validation tests."
                elif "WEAK_CANDIDATE" in status_list:
                    classification = "WEAKENED"
                    weakened += 1
                    reason = "Metrics showed degradation, weak validation outcome."
                elif "REJECT" in status_list:
                    classification = "REJECTED"
                    rejected += 1
                    reason = "Strategies failed basic profitability or robustness thresholds."
                else:
                    needs_more_data += 1
                    
                tests_completed += 1
                
                child_runs_data.append({
                    "run_id": rid,
                    "status": "complete",
                    "report_path": f"/api/research-lab/download/{rid}/research_report.md",
                    "ranked_strategies": ranked_strategies,
                    "summary": summary,
                    "classification": classification,
                    "reason": reason
                })
                
        aggregate_classification = "NEEDS_MORE_DATA"
        if confirmed > 0:
            aggregate_classification = "CONFIRMED"
        elif weakened > 0:
            aggregate_classification = "WEAKENED"
        elif rejected > 0:
            aggregate_classification = "REJECTED"
            
        return jsonify({
            "parent_run_id": parent_id,
            "child_runs": child_runs_data,
            "aggregate_classification": aggregate_classification,
            "aggregate_summary": {
                "tests_completed": tests_completed,
                "confirmed": confirmed,
                "weakened": weakened,
                "rejected": rejected,
                "needs_more_data": needs_more_data
            }
        }), 200

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

        run_dir = _DEFAULT_OUTPUT / run_id

        # Check active runs first
        if run_id in _active_runs:
            info = _active_runs[run_id]
            status = info.get("status", "unknown")
            if status == "complete":
                result = info.get("result", {})
                safe = {k: v for k, v in result.items()
                        if k not in ("thread",) and not callable(v)}

                meta_path = run_dir / "run_meta.json"
                if meta_path.exists():
                    try:
                        safe.update(json.loads(meta_path.read_text(encoding="utf-8")))
                    except Exception:
                        pass

                return jsonify({"run_id": run_id, "status": status, **safe})

            # Evaluations done, reports still writing — surface results to UI.
            if status == "running" and _run_dir_has_summary(run_dir):
                meta_data = {}
                meta_path = run_dir / "run_meta.json"
                if meta_path.exists():
                    try:
                        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                return jsonify({
                    "run_id": run_id,
                    "status": "reporting",
                    "message": "Evaluations complete — building reports. Ranked data should load shortly.",
                    "summary": _quick_summary_counts(run_dir, meta_data),
                    "results_count": meta_data.get("results_count"),
                    **meta_data,
                })

            return jsonify({"run_id": run_id, "status": status,
                            "error": info.get("error", ""),
                            "traceback": info.get("traceback", "")})


        run_dir = _DEFAULT_OUTPUT / run_id
        if not run_dir.exists():
            return jsonify({"error": "Run not found", "run_id": run_id}), 404

        # Read run_meta.json if available

        meta_path = run_dir / "run_meta.json"
        meta_data = {}
        if meta_path.exists():
            try:
                meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        df = get_run_results(run_id, _DEFAULT_OUTPUT)
        if df is None:
            status_path = run_dir / "status.json"
            partial: dict = {}
            if status_path.exists():
                try:
                    partial = json.loads(status_path.read_text(encoding="utf-8"))
                except Exception:
                    partial = {}
            results_count = int(
                partial.get("results_count")
                or meta_data.get("results_count")
                or meta_data.get("combination_count_executed")
                or 0
            )
            tb_path = run_dir / "error_traceback.txt"
            tb_hint = ""
            if tb_path.exists():
                try:
                    tb_hint = tb_path.read_text(encoding="utf-8")[-2000:]
                except Exception:
                    pass
            if results_count > 0:
                return jsonify({
                    "run_id": run_id,
                    "status": "partial",
                    "error": (
                        f"Evaluations completed ({results_count} rows) but full reports failed. "
                        "Check logs for generate_all_reports / write_csvs errors. "
                        "Re-run with fewer symbols or use Fast depth."
                    ),
                    "results_count": results_count,
                    "report_errors": partial.get("errors", []),
                    "traceback_tail": tb_hint,
                    **meta_data,
                })
            return jsonify({
                "run_id": run_id,
                "status": "failed",
                "error": "Run directory exists but results CSV missing — run may have crashed",
                "traceback_tail": tb_hint,
                **meta_data,
            })

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

        if len(df) > 3000:
            summary = _quick_summary_counts(run_dir, meta_data)
            summary["files_ok"] = files_ok
            summary["report_errors"] = report_errors
        else:
            summary = {
                "total": int(len(df)),
                "strong": int((df["status"] == "STRONG_CANDIDATE").sum()) if "status" in df.columns else 0,
                "weak": int((df["status"] == "WEAK_CANDIDATE").sum()) if "status" in df.columns else 0,
                "reject": int((df["status"] == "REJECT").sum()) if "status" in df.columns else 0,
                "files_ok": files_ok,
                "report_errors": report_errors,
            }
        return jsonify({"run_id": run_id, "status": "complete", "summary": summary, **meta_data})


    # ── POST /api/research-lab/analyze/<run_id> ───────────────────────────────
    @app.route("/api/research-lab/analyze/<run_id>", methods=["POST"])
    def api_research_lab_analyze(run_id: str):
        """Run AI analyst on an existing research run."""
        from athena_research.ai_analyst import run_ai_analysis, load_ai_review

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
            
            # Map correctly for the UI
            data = load_ai_review(run_dir)
            action_plan = data.get("action_plan") or {}
            
            response_data = {
                "run_id": run_id,
                "ai_review": data.get("review_md"),
                "summary": action_plan.get("summary") or action_plan.get("overall_verdict") or "See AI review for details.",
                "recommendation": action_plan.get("recommendation") or "See AI review for details.",
            }
            response_data.update(result)
            return jsonify(response_data)
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
            
        action_plan = data.get("action_plan") or {}
        
        response_data = {
            "run_id": run_id,
            "ai_review": data.get("review_md"),
            "summary": action_plan.get("summary") or action_plan.get("overall_verdict") or "See AI review for details.",
            "recommendation": action_plan.get("recommendation") or "See AI review for details.",
        }
        response_data.update(data)
        return jsonify(response_data)

    # ── GET /api/research-lab/download/<run_id>/<filename> ───────────────────
    @app.route("/api/research-lab/download/<run_id>/<path:filename>", methods=["GET"])
    def api_research_lab_download(run_id: str, filename: str):
        """Download a specific output file from a run."""
        # Sanitise — only allow known file names, no directory traversal
        allowed = {
            "research_summary.csv", "ranked_strategies.csv", "by_asset_group.csv",
            "by_symbol.csv", "by_timeframe.csv", "by_session.csv", "by_session_bucket.csv", "by_direction.csv",
            "by_zone.csv", "per_pair_recommendation.csv",
            "indicator_attribution.csv", "rejected_or_failed_configs.csv",
            "engine_a_component_audit.csv", "engine_b_component_audit.csv",
            "candidate_indicator_attribution.csv", "group_breakdown.csv",
            "zone_breakdown_scalp_intra_swing.csv", "structure_context_breakdown.csv",
            "automated_next_tests.csv", "add_remove_retest_recommendations.csv",
            "implementation_ready_candidates.csv",
            "research_report.md", "ai_research_review.md", "ai_action_plan.json",
            "ai_engine_recommendations.json", "run_meta.json", "error_traceback.txt",
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
        from athena_research.reporting import (
            _enrich_with_trust,
            _recommendation_table,
            build_operator_decision_summary,
            sample_operator_source_df,
        )

        run_dir = _DEFAULT_OUTPUT / run_id
        ranked_path = run_dir / "ranked_strategies.csv"
        summary_path = run_dir / "research_summary.csv"

        # Run dir exists but file missing → run failed before writing; return empty
        if not ranked_path.exists() and not summary_path.exists():
            if not run_dir.exists():
                return jsonify({"error": "Run not found", "run_id": run_id}), 404
            return jsonify({"run_id": run_id, "ranked": [], "note": "no ranked results"})

        try:
            if ranked_path.exists():
                df = pd.read_csv(ranked_path).head(50)
            else:
                df = pd.read_csv(summary_path).head(50)
                df = df[df["status"].isin(["STRONG_CANDIDATE", "WEAK_CANDIDATE"])] if "status" in df.columns else df
            rec_path = run_dir / "add_remove_retest_recommendations.csv"
            next_path = run_dir / "automated_next_tests.csv"
            validation_run_count = _validation_run_count_for_run(run_dir)
            recommendations = []
            next_tests = []
            operator_source = sample_operator_source_df(
                summary_path,
                ranked_path=ranked_path if ranked_path.exists() else None,
                limit=2000,
            )
            ready_ranked = _enrich_with_trust(df, validation_run_count=validation_run_count)
            records = json.loads(ready_ranked.fillna("").to_json(orient="records"))
            if rec_path.exists():
                rec_df = _enrich_with_trust(
                    pd.read_csv(rec_path).head(50),
                    validation_run_count=validation_run_count,
                )
                recommendations = json.loads(rec_df.fillna("").to_json(orient="records"))
            elif summary_path.exists():
                rec_df = _recommendation_table(
                    _enrich_with_trust(operator_source, validation_run_count=validation_run_count)
                ).head(50)
                recommendations = json.loads(rec_df.fillna("").to_json(orient="records"))
            if next_path.exists():
                next_df = pd.read_csv(next_path).head(50)
                next_tests = json.loads(next_df.fillna("").to_json(orient="records"))
            operator_summary = build_operator_decision_summary(
                operator_source,
                validation_run_count=validation_run_count,
            )
            trust_context = operator_summary.get("trust_context", {})
            if operator_summary.get("use_now"):
                next_tests = []
            return jsonify({
                "run_id": run_id,
                "ranked": records,
                "recommendations": recommendations,
                "automated_next_tests": next_tests,
                "operator_summary": operator_summary,
                "trust_context": trust_context,
                "validation_run_count": validation_run_count,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Session Autopilot routes ──────────────────────────────────────────────
    _register_session_autopilot_routes(app)

    log.info("[research_lab_routes] Research Lab routes registered")


def _register_session_autopilot_routes(app) -> None:
    """Register /api/research-lab/session-autopilot/* routes."""
    from flask import jsonify, request

    from athena_research.autopilot_session import (
        _SESSIONS_DIR, _OUTPUT_DIR,
        start_session, get_session_status, get_session_result,
        stop_session, list_sessions,
    )

    # ── POST /api/research-lab/session-autopilot/start ────────────────────────
    @app.route("/api/research-lab/session-autopilot/start", methods=["POST"])
    def api_session_autopilot_start():
        """Create and start a new one-click autopilot session."""
        body = request.get_json(silent=True) or {}
        market_group = str(body.get("market_group", "crypto")).lower()
        trading_style = str(body.get("trading_style", "intra")).lower()
        research_depth = str(body.get("research_depth", "standard")).lower()

        # Optional overrides forwarded from "Run Next Action"
        symbols_override = body.get("symbols") or None
        timeframes_override = body.get("timeframes") or None
        families_override = body.get("families") or None

        from athena_research.autopilot import RESEARCH_STYLE_PROFILES
        from athena_research.autopilot_session import GROUP_SYMBOLS
        if trading_style not in RESEARCH_STYLE_PROFILES:
            return jsonify({"error": f"Invalid trading_style: {trading_style}"}), 400
        if market_group not in GROUP_SYMBOLS:
            return jsonify({"error": f"Invalid market_group: {market_group}"}), 400

        try:
            state = start_session(
                market_group=market_group,
                trading_style=trading_style,
                research_depth=research_depth,
                sessions_dir=_SESSIONS_DIR,
                output_dir=_OUTPUT_DIR,
                symbols_override=symbols_override,
                timeframes_override=timeframes_override,
                families_override=families_override,
            )
            return jsonify(state), 202
        except Exception as e:
            log.error("[session_autopilot] start failed: %s", e, exc_info=True)
            return jsonify({"error": str(e)}), 500

    # ── GET /api/research-lab/session-autopilot/status/<session_id> ───────────
    @app.route("/api/research-lab/session-autopilot/status/<session_id>", methods=["GET"])
    def api_session_autopilot_status(session_id: str):
        state = get_session_status(session_id, _SESSIONS_DIR)
        if state is None:
            return jsonify({"error": "Session not found", "session_id": session_id}), 404
        return jsonify(state)

    # ── GET /api/research-lab/session-autopilot/result/<session_id> ───────────
    @app.route("/api/research-lab/session-autopilot/result/<session_id>", methods=["GET"])
    def api_session_autopilot_result(session_id: str):
        result = get_session_result(session_id, _OUTPUT_DIR, _SESSIONS_DIR)
        if result is None:
            return jsonify({"error": "Session not found", "session_id": session_id}), 404
        return jsonify(result)

    # ── GET /api/research-lab/session-autopilot/sessions ─────────────────────
    @app.route("/api/research-lab/session-autopilot/sessions", methods=["GET"])
    def api_session_autopilot_list():
        sessions = list_sessions(_SESSIONS_DIR)
        return jsonify({"sessions": sessions})

    # ── POST /api/research-lab/session-autopilot/stop/<session_id> ───────────
    @app.route("/api/research-lab/session-autopilot/stop/<session_id>", methods=["POST"])
    def api_session_autopilot_stop(session_id: str):
        state = stop_session(session_id, _SESSIONS_DIR)
        if state is None:
            return jsonify({"error": "Session not found", "session_id": session_id}), 404
        return jsonify(state)

    log.info("[research_lab_routes] Session Autopilot routes registered")
