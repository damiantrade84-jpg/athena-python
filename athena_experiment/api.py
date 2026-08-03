"""Flask blueprint for the Tuning Lab: /api/experiment/*.

- GET  /api/experiment/knobs  -> knob catalog for the UI (athena_experiment.knobs)
- POST /api/experiment/run    -> one pair, baseline vs. overlay comparison,
                                  frozen candles, synchronous (single pair,
                                  same order of latency as /api/v3/backtest)
- POST /api/experiment/push   -> deep-merge an accepted overlay into
                                  config.local.yaml (requires {"confirm": true})

Every run executes in its own short-lived subprocess
(``python -m athena_experiment.worker_cli``, see subprocess_runner.py) — the
live-serving process's CONFIG is never touched, so this cannot add latency or
risk to live scanning regardless of what an experiment does. This is a real
``subprocess.run`` of a plain module, not ``ProcessPoolExecutor``: athena.py
has no ``if __name__ == "__main__":`` guard, and multiprocessing's Windows
spawn start method refuses to start a child process from inside an unguarded
``__main__`` module — see worker_cli.py's docstring for the exact failure.
"""

from __future__ import annotations

from collections.abc import Mapping

from flask import Blueprint, jsonify, request

from athena_experiment.knobs import catalog
from athena_experiment.overlay import OverlayValidationError, build_overlay
from athena_experiment.subprocess_runner import WorkerTimeoutError, run_worker_subprocess

# One pair, two backtests (baseline + variant) against already-downloaded
# frozen candles. Index/equity pairs route through opening-range-gap setups
# and run noticeably slower than forex/crypto even after the setups.py O(n^2)
# fix (~60s observed for NAS100 vs ~15-20s for EUR/USD) — 180s leaves real
# headroom above that instead of being a near-miss.
_RUN_TIMEOUT_SEC = 180
# Engine B replays every trigger-TF bar through the full NakedEngine
# evaluation (~29ms/bar measured for BTCUSDT M15) and, even with the bounded
# replay window (ENGINE_B_BT_MAX_REPLAY_DAYS), one pair's baseline+variant
# pair of runs routinely exceeds the Engine A budget; Engine B runs were
# timing out at 180s before producing any result.
_RUN_TIMEOUT_SEC_ENGINE_B = 600


def create_experiment_blueprint(*, all_pairs_provider, json_safe=None) -> Blueprint:
    bp = Blueprint("experiment", __name__)
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

    def _resolve_group(pair: dict) -> str | None:
        try:
            from engine_a_groups import resolve_score_group_by_type

            return resolve_score_group_by_type(dict(pair))
        except Exception:
            return None

    def _group_matches(requested: object, canonical: str) -> bool:
        """Return whether a client-supplied score group matches the pair.

        The main ``/api/pairs`` endpoint groups rows by broad UI labels such as
        ``Forex`` and ``Crypto``. Those labels are filters, not scoring keys;
        scoring consumes canonical groups such as ``forex_majors`` and
        ``crypto_btc``. Defaults/runs therefore ignore the optional legacy
        client group and derive the canonical value from the selected pair.
        Push is stricter and rejects a mismatch before writing local config.
        """
        return str(requested or "").strip().lower() == str(canonical).strip().lower()

    def _normalized_style(payload: dict) -> str:
        style = str(payload.get("style") or "intraday").strip().lower()
        return "intraday" if style in ("", "auto") else style

    @bp.route("/api/experiment/knobs", methods=["GET"])
    def api_experiment_knobs():
        engine = str(request.args.get("engine") or "A").strip().upper()
        try:
            return jsonify({"success": True, **catalog(engine)})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 422

    @bp.route("/api/experiment/defaults", methods=["GET"])
    def api_experiment_defaults():
        """Currently active value for every knob, resolved for one real pair —
        what the UI must show as "current", not a guessed static default."""
        engine = str(request.args.get("engine") or "A").strip().upper()
        token = request.args.get("symbol") or request.args.get("pair")
        if not token:
            return jsonify({"success": False, "error": "symbol is required"}), 400
        pair = _find_pair(token)
        if pair is None:
            return jsonify({"success": False, "error": f"Pair {token!r} not found."}), 404
        style = _normalized_style({"style": request.args.get("style")})
        # The selected pair is authoritative. ``group`` from older clients is
        # an asset-class display label (for example "Forex"), not a score-group
        # key, and must never steer config resolution.
        group = _resolve_group(pair)
        if not group:
            return jsonify(
                {"success": False, "error": "Could not resolve a score group for this pair."}
            ), 422

        from athena_experiment.defaults import resolve_current_values

        try:
            values = resolve_current_values(engine, pair, group, style)
        except Exception as exc:
            return jsonify({"success": False, "error": f"{type(exc).__name__}: {exc}"}), 500

        return jsonify({"success": True, "engine": engine, "group": group, "style": style, "values": _safe(values)})

    @bp.route("/api/experiment/run", methods=["POST"])
    def api_experiment_run():
        payload = request.get_json(force=True, silent=True) or {}
        token = payload.get("symbol") or payload.get("pair")
        if not token:
            return jsonify(
                {"success": False, "error": "No pair selected. The Tuning Lab tests one pair at a time."}
            ), 400
        pair = _find_pair(token)
        if pair is None:
            return jsonify({"success": False, "error": f"Pair {token!r} not found."}), 404

        engine = str(payload.get("engine") or "A").strip().upper()
        style = _normalized_style(payload)
        # Always derive the score group from the pair. Trusting the broad group
        # label emitted by /api/pairs creates inert BY_GROUP.forex-style keys
        # while the evaluator looks up BY_GROUP.forex_majors (and equivalents).
        group = _resolve_group(pair)
        if not group:
            return jsonify(
                {"success": False, "error": "Could not resolve a score group for this pair."}
            ), 422
        knob_values = payload.get("overlay") or payload.get("knobs") or {}
        if not isinstance(knob_values, Mapping):
            return jsonify({"success": False, "error": "overlay/knobs must be an object"}), 422

        try:
            overlay = build_overlay(engine, group, style, knob_values) if knob_values else {}
        except OverlayValidationError as exc:
            return jsonify({"success": False, "error": str(exc)}), 422

        try:
            result = run_worker_subprocess(
                pair.get("symbol") or pair.get("display"),
                engine,
                style,
                overlay,
                _RUN_TIMEOUT_SEC_ENGINE_B if engine == "B" else _RUN_TIMEOUT_SEC,
            )
        except WorkerTimeoutError as exc:
            return jsonify({"success": False, "error": str(exc)}), 504
        except Exception as exc:
            return jsonify({"success": False, "error": f"{type(exc).__name__}: {exc}"}), 500

        if not isinstance(result, dict) or result.get("error"):
            return jsonify({"success": False, **_safe(result or {})}), 422

        result["group"] = group
        result["style"] = style
        result["engine"] = engine
        result["overlay"] = overlay
        # Preserve the validated flat knob request so the UI can push exactly
        # the variant it tested rather than rebuilding a possibly changed form.
        result["knobs"] = dict(knob_values)
        return jsonify({"success": True, **_safe(result)})

    @bp.route("/api/experiment/push", methods=["POST"])
    def api_experiment_push():
        payload = request.get_json(force=True, silent=True) or {}
        if not bool(payload.get("confirm")):
            return jsonify(
                {"success": False, "error": "confirm must be true to push an override."}
            ), 400

        engine = str(payload.get("engine") or "A").strip().upper()
        style = _normalized_style(payload)
        token = payload.get("symbol") or payload.get("pair")
        if not token:
            return jsonify(
                {"success": False, "error": "symbol is required to verify the score group"}
            ), 400
        pair = _find_pair(token)
        if pair is None:
            return jsonify({"success": False, "error": f"Pair {token!r} not found."}), 404

        group = _resolve_group(pair)
        knob_values = payload.get("overlay") or payload.get("knobs") or {}
        if not group:
            return jsonify(
                {"success": False, "error": "Could not resolve a score group for this pair."}
            ), 422
        requested_group = payload.get("group")
        if requested_group and not _group_matches(requested_group, group):
            return jsonify(
                {
                    "success": False,
                    "error": (
                        f"group {requested_group!r} does not match {token!r}'s "
                        f"score group {group!r}; refresh the Tuning Lab before pushing"
                    ),
                }
            ), 422
        if not knob_values:
            return jsonify({"success": False, "error": "overlay is empty; nothing to push"}), 400
        if not isinstance(knob_values, Mapping):
            return jsonify({"success": False, "error": "overlay/knobs must be an object"}), 422

        try:
            overlay = build_overlay(engine, group, style, knob_values)
        except OverlayValidationError as exc:
            return jsonify({"success": False, "error": str(exc)}), 422

        from athena_experiment.push import push_overlay_to_local_config

        try:
            written = push_overlay_to_local_config(overlay)
        except Exception as exc:
            return jsonify({"success": False, "error": f"{type(exc).__name__}: {exc}"}), 500

        return jsonify(
            {
                "success": True,
                "group": group,
                "overlayWritten": overlay,
                "localConfigDocument": _safe(written),
                "restartRequired": True,
                "note": (
                    "Written to config.local.yaml. This does not take effect on the "
                    "currently running Athena process — restart to apply it to live "
                    "scanning. Any knob with scope \"global\" affects every pair that "
                    "shares that config, not only the one just tested."
                ),
            }
        )

    return bp
