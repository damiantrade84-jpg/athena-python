"""guardian_routes.py — Flask API endpoints for Ultra Instinct modules.

Register with: app.register_blueprint(guardian_bp)
"""

import logging
import os

from flask import Blueprint, jsonify, request

from config import _json_safe

log = logging.getLogger("sentinel")

guardian_bp = Blueprint("guardian", __name__)


def _leaderboard_from_report(report: dict) -> list:
    """Build leaderboard rows from a factor_report() dict (same shape as factor_tracker)."""
    if not report.get("available"):
        return []
    factors = report.get("factors", {})
    ranked = []
    for name, data in factors.items():
        if data.get("recommendation") == "insufficient_data":
            continue
        ranked.append(
            {
                "factor": name,
                "ic": data.get("information_coefficient"),
                "spread": data.get("contribution_spread"),
                "win_rate": data.get("win_rate_when_active"),
                "samples": data.get("sample_count"),
                "recommendation": data.get("recommendation"),
            }
        )
    ranked.sort(key=lambda x: x.get("ic") or -999, reverse=True)
    return ranked


@guardian_bp.route("/api/guardian/status")
def api_guardian_status():
    """Boot check results + shield status + divergence summary."""
    result = {
        "guardian": {},
        "shield": {},
        "divergence": {},
        "forensics": {},
        "overall": "unknown",
    }

    try:
        from guardian import boot_check

        result["guardian"] = boot_check()
    except ImportError:
        result["guardian"] = {
            "passed": None,
            "failures": ["guardian.py not installed"],
            "total_checks": 0,
        }
    except Exception as e:
        result["guardian"] = {"passed": False, "failures": [str(e)], "total_checks": 0}

    try:
        from risk_shield import shield_status

        result["shield"] = shield_status()
    except ImportError:
        result["shield"] = {"circuit_breaker_open": None, "failure_count": 0}
    except Exception as e:
        result["shield"] = {"circuit_breaker_open": None, "error": str(e)}

    try:
        from divergence_monitor import divergence_report

        result["divergence"] = divergence_report(lookback_hours=24)
    except ImportError:
        result["divergence"] = {"total_checks": 0, "divergence_count": 0}
    except Exception as e:
        result["divergence"] = {"total_checks": 0, "error": str(e)}

    try:
        from forensic_observability import build_forensic_summary

        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
        result["forensics"] = build_forensic_summary(
            guardian=result["guardian"],
            shield=result["shield"],
            divergence=result["divergence"],
            db_path=db_path,
            lookback_hours=24,
        )
        result["overall"] = result["forensics"].get("overall", "unknown")
    except Exception as e:
        result["forensics"] = {"error": str(e)}
        g_ok = result["guardian"].get("passed") is True
        s_ok = result["shield"].get("circuit_breaker_open") is not True
        d_ok = result["divergence"].get("critical_count", 0) == 0
        if g_ok and s_ok and d_ok:
            result["overall"] = "healthy"
        elif not g_ok or not s_ok:
            result["overall"] = "critical"
        else:
            result["overall"] = "warning"

    return jsonify(_json_safe(result))


@guardian_bp.route("/api/guardian/boot-check")
def api_guardian_boot():
    """Run boot checks on demand."""
    try:
        from guardian import boot_check

        return jsonify(_json_safe(boot_check()))
    except ImportError:
        return jsonify({"error": "guardian.py not installed"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@guardian_bp.route("/api/factor-attribution")
def api_factor_attribution():
    """Factor predictive power report."""
    try:
        from factor_tracker import factor_leaderboard, factor_report

        asset_type = request.args.get("asset_type") or None
        lookback = int(request.args.get("lookback_days", 90))
        report = factor_report(asset_type=asset_type, lookback_days=lookback)
        if asset_type:
            leaderboard = _leaderboard_from_report(report)
        else:
            leaderboard = factor_leaderboard(lookback_days=lookback)
        return jsonify(
            _json_safe({"report": report, "leaderboard": leaderboard})
        )
    except ImportError:
        return jsonify({"error": "factor_tracker.py not installed"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@guardian_bp.route("/api/divergence")
def api_divergence():
    """Live vs backtest divergence report."""
    try:
        from divergence_monitor import divergence_report

        hours = int(request.args.get("hours", 24))
        return jsonify(_json_safe(divergence_report(lookback_hours=hours)))
    except ImportError:
        return jsonify({"error": "divergence_monitor.py not installed"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@guardian_bp.route("/api/forensics/summary")
def api_forensics_summary():
    """Unified forensic observability summary for UI and Telegram."""
    try:
        from divergence_monitor import divergence_report
        from forensic_observability import build_forensic_summary
        from guardian import boot_check
        from risk_shield import shield_status

        hours = int(request.args.get("hours", 24))
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")
        payload = build_forensic_summary(
            guardian=boot_check(),
            shield=shield_status(),
            divergence=divergence_report(lookback_hours=hours),
            db_path=db_path,
            lookback_hours=hours,
        )
        return jsonify(_json_safe(payload))
    except ImportError as e:
        return jsonify({"error": f"forensics dependency missing: {e}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@guardian_bp.route("/api/shield/status")
def api_shield_status():
    """Risk shield + circuit breaker status."""
    try:
        from risk_shield import shield_status

        return jsonify(_json_safe(shield_status()))
    except ImportError:
        return jsonify({"error": "risk_shield.py not installed"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@guardian_bp.route("/api/shield/reset", methods=["POST"])
def api_shield_reset():
    """Manual circuit breaker reset."""
    try:
        from risk_shield import reset_breaker

        reset_breaker()
        return jsonify({"success": True, "message": "Circuit breaker reset"})
    except ImportError:
        return jsonify({"error": "risk_shield.py not installed"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
