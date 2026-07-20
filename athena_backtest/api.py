"""Flask blueprint for the rebuilt (v3) backtester.

Routes are served under /api/v3/* because `athena_backtesting_v2`'s
legacy-retirement guard 410s every path starting with /api/backtest
(that whole legacy surface is retired app-wide):

- POST /api/v3/backtest          -> Engine A, one pair, synchronous
- POST /api/v3/backtest-naked    -> Engine B, one pair, synchronous
- POST /api/v3/backtest-all      -> async universe run, returns {jobId}
- GET  /api/v3/backtest-jobs/<id> -> job status/leaderboard

The separately-built `athena_backtesting_v2` package keeps its own routes;
nothing here reads or writes its state.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from athena_backtest.jobs import BacktestJobManager

_RETIRED = ("backtest-scalp", "backtest-consensus", "backtest-ase", "backtest-ase-all", "backtest-naked-all")


def create_backtest_v3_blueprint(*, all_pairs_provider, json_safe=None, max_workers: int = 3) -> Blueprint:
    bp = Blueprint("bt_v3", __name__)
    jobs = BacktestJobManager(max_workers=max_workers)
    _safe = json_safe if callable(json_safe) else (lambda value: value)

    def _find_pair(token: str) -> dict | None:
        wanted = str(token or "").strip()
        if not wanted:
            return None
        return next(
            (
                p
                for p in all_pairs_provider()
                if p.get("display") == wanted or p.get("symbol") == wanted
            ),
            None,
        )

    def _run_single(engine: str):
        payload = request.get_json(force=True, silent=True) or {}
        token = payload.get("symbol") or payload.get("pair")
        if not token:
            return jsonify(
                {
                    "success": False,
                    "error": "No pair selected. Single-pair backtest requires a symbol; "
                    "use /api/backtest-all for a universe run.",
                }
            ), 400
        pair = _find_pair(token)
        if pair is None:
            return jsonify({"success": False, "error": f"Pair {token!r} not found."}), 404
        style = str(payload.get("style") or "intraday").strip().lower()
        if style in ("", "auto"):
            style = "intraday"
        if style not in ("intraday", "swing"):
            return jsonify({"success": False, "error": f"Unsupported style {style!r}."}), 422
        from athena_backtest.runner import run_pair

        result = run_pair(pair, engine=engine, horizon=style)
        if isinstance(result, dict) and result.get("error"):
            return jsonify({"success": False, **_safe(result)}), 422
        persist = bool(payload.get("persist", True))
        if persist:
            try:
                from athena_backtest.results import save_run

                result["runId"] = save_run(result, pair=pair, engine=engine, style=style)
            except Exception:
                result["persistError"] = True
        return jsonify(_safe(result))

    @bp.route("/api/v3/backtest", methods=["POST"])
    def api_backtest_v3():
        return _run_single("A")

    @bp.route("/api/v3/backtest-naked", methods=["POST"])
    def api_backtest_naked_v3():
        return _run_single("B")

    @bp.route("/api/v3/backtest-all", methods=["POST"])
    def api_backtest_all_v3():
        payload = request.get_json(force=True, silent=True) or {}
        engine = str(payload.get("engine") or "A").strip().upper()
        if engine not in ("A", "B"):
            return jsonify({"success": False, "error": f"Unsupported engine {engine!r}."}), 422
        style = str(payload.get("style") or "intraday").strip().lower()
        if style in ("", "auto"):
            style = "intraday"
        if style not in ("intraday", "swing"):
            return jsonify({"success": False, "error": f"Unsupported style {style!r}."}), 422
        tokens = payload.get("symbols") or payload.get("pairs") or []
        if tokens:
            pairs = [p for t in tokens if (p := _find_pair(t)) is not None]
        else:
            pairs = [p for p in all_pairs_provider() if p.get("enabled", True)]
        symbols = [str(p.get("symbol")) for p in pairs if p.get("symbol")]
        if not symbols:
            return jsonify({"success": False, "error": "No valid symbols resolved."}), 400
        job_id = jobs.start(symbols=symbols, engine=engine, style=style)
        return jsonify({"success": True, "jobId": job_id, "total": len(symbols)}), 202

    @bp.route("/api/v3/backtest-jobs/<job_id>", methods=["GET"])
    def api_backtest_job_v3(job_id: str):
        status = jobs.status(job_id)
        if status is None:
            return jsonify({"success": False, "error": "Unknown job id."}), 404
        return jsonify(_safe({"success": True, **status}))

    def _gone(name: str):
        def _handler():
            return jsonify(
                {
                    "success": False,
                    "error": f"/api/{name} is retired. The rebuilt backtester (v3) covers "
                    "Engine A (/api/v3/backtest) and Engine B (/api/v3/backtest-naked) only.",
                }
            ), 410

        _handler.__name__ = f"api_{name.replace('-', '_')}_gone"
        return _handler

    for name in _RETIRED:
        bp.add_url_rule(f"/api/{name}", f"{name}_gone", _gone(name), methods=["POST"])

    return bp
