"""Kimi Code ↔ Athena HTTP Bridge.

Exposes Athena internals as REST endpoints that Kimi Code (or any agent)
can call via requests/httpx.

Usage in athena.py:
    from tools.kimi_bridge import register_kimi_routes
    register_kimi_routes(app)

Endpoints:
    GET  /api/kimi/health              → Server status
    POST /api/kimi/audit/query         → Read-only SQL on audit.db
    GET  /api/kimi/config/read         → Current config.yaml as JSON
    POST /api/kimi/config/update       → Safe config update (auto-backup)
    POST /api/kimi/backtest/run        → Trigger backtest
    POST /api/kimi/tests/run           → Run pytest suite
    GET  /api/kimi/signals/latest      → Latest signals
    GET  /api/kimi/trades/performance  → Aggregate trade stats
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

PROJECT_ROOT = Path(__file__).parent.parent
DB_AUDIT = PROJECT_ROOT / "audit.db"
DB_MICRO = PROJECT_ROOT / "microstructure.db"
DB_CANDLE = PROJECT_ROOT / "candle_cache.db"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

kimi_bp = Blueprint("kimi", __name__, url_prefix="/api/kimi")


def _jsonify_row(rows: list[sqlite3.Row]) -> dict:
    if not rows:
        return {"columns": [], "rows": [], "count": 0}
    return {
        "columns": list(rows[0].keys()),
        "rows": [dict(r) for r in rows],
        "count": len(rows),
    }


@kimi_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": "athena-python",
        "version": "4.0",
    })


@kimi_bp.route("/audit/query", methods=["POST"])
def audit_query():
    """Execute read-only SQL on audit.db."""
    data = request.get_json() or {}
    sql = data.get("sql", "").strip()
    limit = min(data.get("limit", 100), 5000)

    if not sql:
        return jsonify({"error": "No SQL provided"}), 400

    # Safety: only SELECT allowed
    first_word = sql.split()[0].lower()
    if first_word not in ("select", "with", "pragma"):
        return jsonify({"error": f"Only SELECT/PRAGMA queries allowed (got: {first_word})"}), 403

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"{sql} LIMIT ?", (limit,)).fetchall()
            return jsonify(_jsonify_row(rows))
    except Exception as e:
        return jsonify({"error": str(e), "sql": sql}), 500


@kimi_bp.route("/micro/query", methods=["POST"])
def micro_query():
    """Execute read-only SQL on microstructure.db."""
    data = request.get_json() or {}
    sql = data.get("sql", "").strip()
    limit = min(data.get("limit", 100), 5000)

    if not sql:
        return jsonify({"error": "No SQL provided"}), 400

    first_word = sql.split()[0].lower()
    if first_word not in ("select", "with", "pragma"):
        return jsonify({"error": f"Only SELECT/PRAGMA queries allowed"}), 403

    try:
        with sqlite3.connect(DB_MICRO, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"{sql} LIMIT ?", (limit,)).fetchall()
            return jsonify(_jsonify_row(rows))
    except Exception as e:
        return jsonify({"error": str(e), "sql": sql}), 500


@kimi_bp.route("/config/read", methods=["GET"])
def config_read():
    """Return current config.yaml as structured data."""
    try:
        import yaml
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return jsonify(yaml.safe_load(f))
    except ImportError:
        return jsonify({"error": "PyYAML not installed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/config/update", methods=["POST"])
def config_update():
    """Update a single config key (safe: backups old config)."""
    try:
        import yaml
    except ImportError:
        return jsonify({"error": "PyYAML not installed"}), 500

    data = request.get_json() or {}
    key_path = data.get("key", "").strip()
    value = data.get("value")

    if not key_path or value is None:
        return jsonify({"error": "Need 'key' and 'value'"}), 400

    if not CONFIG_PATH.exists():
        return jsonify({"error": f"config.yaml not found at {CONFIG_PATH}"}), 404

    # Backup
    backup_name = f"config.yaml.bak.{datetime.now():%Y%m%d_%H%M%S}"
    backup_path = CONFIG_PATH.with_name(backup_name)

    try:
        import shutil
        shutil.copy2(CONFIG_PATH, backup_path)

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Navigate key path (e.g., "RISK_PCT" or "SCALP_ENGINE.MIN_RR")
        keys = key_path.split(".")
        target = config
        for k in keys[:-1]:
            if k not in target or not isinstance(target[k], dict):
                target[k] = {}
            target = target[k]

        # Preserve type where possible
        old_value = target.get(keys[-1])
        if old_value is not None:
            try:
                if isinstance(old_value, bool):
                    value = bool(value)
                elif isinstance(old_value, int):
                    value = int(value)
                elif isinstance(old_value, float):
                    value = float(value)
            except (ValueError, TypeError):
                pass

        target[keys[-1]] = value

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, sort_keys=False, default_flow_style=False, allow_unicode=True)

        return jsonify({
            "status": "updated",
            "key": key_path,
            "old_value": old_value,
            "new_value": value,
            "backup": str(backup_path),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/tests/run", methods=["POST"])
def tests_run():
    """Run pytest via the Kimi test runner and return structured results."""
    data = request.get_json() or {}
    engine = data.get("engine", "")
    pattern = data.get("pattern", "")
    coverage = data.get("coverage", False)
    parallel = data.get("parallel", False)

    cmd = [sys.executable, "tools/run_kimi_tests.py"]
    if engine:
        cmd += ["--engine", engine]
    elif pattern:
        cmd += ["--pattern", pattern]
    else:
        cmd += ["--all"]
    if coverage:
        cmd.append("--coverage")
    if parallel:
        cmd.append("--parallel")
    cmd += ["--json", "--timeout", str(data.get("timeout", 600))]

    try:
        result = subprocess.run(
            cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=data.get("timeout", 600) + 30
        )
        # Parse JSON output
        try:
            test_result = json.loads(result.stdout.split("\n")[-1]) if result.stdout else {}
        except json.JSONDecodeError:
            test_result = {
                "raw_stdout": result.stdout[-4000:],
                "raw_stderr": result.stderr[-2000:],
                "parse_error": True,
            }

        return jsonify({
            "passed": result.returncode == 0,
            "returncode": result.returncode,
            "result": test_result,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Tests timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/signals/latest", methods=["GET"])
def signals_latest():
    """Return latest signals from audit.db."""
    limit = min(request.args.get("limit", 20, type=int), 500)
    engine = request.args.get("engine", "").upper()

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            con.row_factory = sqlite3.Row
            sql = "SELECT * FROM signals WHERE 1=1"
            params = []
            if engine:
                sql += " AND engine = ?"
                params.append(engine)
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            rows = con.execute(sql, params).fetchall()
            return jsonify({"signals": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/trades/performance", methods=["GET"])
def trades_performance():
    """Return aggregate trade performance."""
    days = min(request.args.get("days", 30, type=int), 365)
    pair = request.args.get("pair", "")

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            sql = """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END) as breakeven,
                    SUM(pnl) as net_pnl,
                    AVG(pnl) as avg_pnl,
                    AVG(r_multiple) as avg_r,
                    MAX(pnl) as best_trade,
                    MIN(pnl) as worst_trade
                FROM trades
                WHERE ts > datetime('now', '-' || ? || ' days')
            """
            params = [days]
            if pair:
                sql += " AND pair = ?"
                params.append(pair)
            row = con.execute(sql, params).fetchone()
            total = row[0] or 0
            return jsonify({
                "period_days": days,
                "pair_filter": pair or "all",
                "total_trades": total,
                "wins": row[1] or 0,
                "losses": row[2] or 0,
                "breakeven": row[3] or 0,
                "win_rate": round(row[1] / total * 100, 2) if total else 0,
                "net_pnl": row[4],
                "avg_pnl": row[5],
                "avg_r": row[6],
                "best_trade": row[7],
                "worst_trade": row[8],
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@kimi_bp.route("/trades/drawdown", methods=["GET"])
def trades_drawdown():
    """Return running drawdown analysis."""
    days = min(request.args.get("days", 90, type=int), 365)

    try:
        with sqlite3.connect(DB_AUDIT, timeout=10.0) as con:
            rows = con.execute(
                "SELECT ts, pnl, r_multiple FROM trades WHERE ts > datetime('now', '-' || ? || ' days') ORDER BY ts",
                (days,),
            ).fetchall()

        if not rows:
            return jsonify({"max_drawdown_pct": 0, "current_drawdown_pct": 0, "peak_equity": 0})

        equity = 0
        peak = 0
        max_dd = 0
        for _, pnl, _ in rows:
            equity += pnl or 0
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak else 0
            if dd > max_dd:
                max_dd = dd

        current_dd = (peak - equity) / peak * 100 if peak else 0
        return jsonify({
            "period_days": days,
            "trade_count": len(rows),
            "peak_equity": round(peak, 2),
            "current_equity": round(equity, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "current_drawdown_pct": round(current_dd, 2),
            "drawdown_status": "NORMAL" if current_dd < 10 else "REDUCED" if current_dd < 15 else "STOPPED",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def register_kimi_routes(app):
    app.register_blueprint(kimi_bp)
    print("[KIMI] Bridge routes registered at /api/kimi/*")
